"""Research-firm logos for analyst actions: estimates/firm-logos/{firm_id}.png plus registry.json.

Each firm's official domain is listed below (FIRM_DOMAINS, keyed by Benzinga's firm name). Candidates come from
Firecrawl's branding format on the firm's homepage (logo, favicon, og image) and from the homepage's own
<link rel="icon"/"apple-touch-icon"> tags. A candidate is kept only if it decodes as an image, is at least
32 px on both sides, is not near-uniform, and is roughly square (wide wordmarks are rejected because they are
unreadable at row size). It is stored as a 64x64 PNG. Firms without a validated logo get a monogram in the UI.
Logos are firm marks only; no photos of people are collected.
"""
import io
import json
import re
import time
import urllib.parse

from .common import now_iso, request

REGISTRY = "estimates/firm-logos/registry.json"
UA = {"User-Agent": "Mozilla/5.0 (compatible; FiledUnder/1.0; +https://filedunder.ai)"}

FIRM_DOMAINS = {
    "Barclays": "barclays.com", "Wells Fargo": "wellsfargo.com", "UBS": "ubs.com", "JP Morgan": "jpmorgan.com", "Citigroup": "citigroup.com",
    "Morgan Stanley": "morganstanley.com", "RBC Capital": "rbccm.com", "Truist Securities": "truist.com", "Evercore ISI Group": "evercore.com",
    "TD Cowen": "tdsecurities.com", "Piper Sandler": "pipersandler.com", "Mizuho": "mizuhogroup.com", "B of A Securities": "bankofamerica.com",
    "Stifel": "stifel.com", "Goldman Sachs": "goldmansachs.com", "Jefferies": "jefferies.com", "BMO Capital": "bmo.com", "Needham": "needhamco.com",
    "Baird": "rwbaird.com", "Keybanc": "key.com", "DA Davidson": "dadavidson.com", "Cantor Fitzgerald": "cantor.com", "BTIG": "btig.com",
    "Keefe, Bruyette & Woods": "kbw.com", "Oppenheimer": "oppenheimer.com", "Susquehanna": "sig.com", "Scotiabank": "scotiabank.com",
    "Guggenheim": "guggenheimpartners.com", "Raymond James": "raymondjames.com", "Bernstein": "bernstein.com", "Rosenblatt": "rblt.com",
    "Stephens & Co.": "stephens.com", "Telsey Advisory Group": "telseygroup.com", "Wedbush": "wedbush.com", "Canaccord Genuity": "canaccordgenuity.com",
    "Deutsche Bank": "db.com", "Argus Research": "argusresearch.com", "Benchmark": "benchmarkcompany.com", "Freedom Broker": "freedombroker.com",
    "Wolfe Research": "wolferesearch.com", "Citizens": "citizensbank.com", "B. Riley Securities": "brileysecurities.com", "HSBC": "hsbc.com",
    "Roth Capital": "roth.com", "HC Wainwright & Co.": "hcwco.com", "BNP Paribas": "bnpparibas.com", "Macquarie": "macquarie.com",
    "Seaport Global": "seaportglobal.com", "Barrington Research": "barringtonresearch.com", "Loop Capital": "loopcapital.com",
    "JMP Securities": "jmpsecurities.com", "Leerink Partners": "leerink.com", "William Blair": "williamblair.com", "Rothschild & Co": "rothschildandco.com",
    "Tigress Financial": "tigressfp.com", "StoneX": "stonex.com", "GLJ Research": "glj-research.com", "Melius Research": "meliusresearch.com",
    "CIBC": "cibc.com", "Ladenburg Thalmann": "ladenburg.com", "Freedom Capital Markets": "freedomcapitalmarkets.com",
    "Northland Capital Markets": "northlandcapitalmarkets.com", "Compass Point": "compasspointllc.com", "Zelman & Assoc": "zelmanassociates.com",
    "Jones Trading": "jonestrading.com", "CICC": "cicc.com", "KGI Securities": "kgi.com", "Daiwa Capital": "daiwa.co.jp", "Craig-Hallum": "craig-hallum.com",
    "Hovde Group": "hovdegroup.com", "BWG Global": "bwgstrategy.com", "Chardan Capital": "chardan.com", "CLSA": "clsa.com", "Gordon Haskett": "gordonhaskett.com",
    "Maxim Group": "maximgrp.com", "BWS Financial": "bwsfinancial.com", "China Renaissance": "chinarenaissance.com", "Pivotal Research": "pvtl.com",
    "WestPark Capital": "wpcapital.com", "Berenberg": "berenberg.de", "Johnson Rice": "johnsonrice.com", "Redburn Atlantic": "redburnatlantic.com",
    "Clear Street": "clearstreet.io", "Lake Street": "lakestreetcm.com", "CFRA": "cfraresearch.com", "Fermium Research": "fermiumresearch.com",
    "MoffettNathanson": "moffettnathanson.com", "Erste Group": "erstegroup.com", "Longbow Research": "longbowresearch.com", "Alembic Global": "alembicglobal.com",
    "Janney Montgomery Scott": "janney.com", "CJS Securities": "cjssecurities.com", "BNP Paribas Exane": "exane.com", "Arete Research": "arete.net",
    "Texas Capital Securities": "texascapitalbank.com", "Sidoti & Co.": "sidoti.com", "New Street Research": "newstreetresearch.com", "CBRE": "cbre.com",
    "TD Securities": "tdsecurities.com", "Northcoast Research": "northcoastresearch.com", "Phillip Securities": "phillip.com.sg",
    "Vertical Research": "verticalresearchpartners.com", "Itau BBA": "itau.com.br", "Roth MKM": "roth.com", "Exane BNP Paribas": "exane.com",
    "LUCID CAPITAL MARKETS": "lucidcm.com", "LifeSci Capital": "lifescicapital.com", "DZ Bank": "dzbank.com", "Citizens Capital Markets": "citizensbank.com",
    "Monness, Crespi, Hardt": "monness.com", "Lynx Global": "lynxglobal.com", "Fox Advisors": "foxadvisorsllc.com", "Aletheia Capital": "aletheia-capital.com",
    "Colliers Securities": "colliers.com", "Amerx": None,
}


def candidates_from_branding(branding, base):
    """Image URLs from a Firecrawl branding payload, square icons first."""
    imgs = (branding or {}).get("images") or {}
    out = []
    for k in ("favicon", "logo", "ogImage"):
        u = imgs.get(k) or (branding or {}).get(k)
        if u and isinstance(u, str) and not u.startswith("data:"):
            out.append((k, urllib.parse.urljoin(base, u)))
    return out


ICON_RE = re.compile(r"<link\b[^>]*>", re.I)


def candidates_from_html(html, base):
    out = []
    for tag in ICON_RE.findall(html or ""):
        rel = re.search(r"rel=[\"']([^\"']+)", tag, re.I)
        href = re.search(r"href=[\"']([^\"']+)", tag, re.I)
        if not rel or not href or "icon" not in rel.group(1).lower():
            continue
        sizes = re.search(r"sizes=[\"'](\d+)", tag, re.I)
        out.append((rel.group(1).lower(), urllib.parse.urljoin(base, href.group(1)), int(sizes.group(1)) if sizes else 0))
    out.sort(key=lambda x: (-("apple-touch" in x[0]), -x[2]))
    return [(r, u) for r, u, _ in out] + [("favicon.ico", urllib.parse.urljoin(base, "/favicon.ico"))]


def validate_png(data, rasterize_svg=None):
    """Decode, check and normalize an image to a 64x64 PNG. Returns (png_bytes, info) or (None, reason)."""
    try:
        from PIL import Image, ImageStat
    except ImportError:
        return None, "Pillow unavailable"
    if not data or len(data) < 100:
        return None, "empty"
    head = data[:200].lstrip().lower()
    if head.startswith(b"<svg") or head.startswith(b"<?xml"):
        if not rasterize_svg:
            return None, "svg without rasterizer"
        data = rasterize_svg(data)
        if not data:
            return None, "svg rasterize failed"
    if head.startswith(b"<!doctype") or head.startswith(b"<html"):
        return None, "html, not an image"
    try:
        im = Image.open(io.BytesIO(data))
        if getattr(im, "n_frames", 1) > 1 and im.format == "ICO":
            sizes = sorted(im.info.get("sizes") or [], key=lambda s: -s[0])
            if sizes:
                im.size = sizes[0]
        im.load()
        im = im.convert("RGBA")
    except Exception as e:
        return None, f"decode failed: {str(e)[:60]}"
    w, h = im.size
    if w < 32 or h < 32:
        return None, f"too small {w}x{h}"
    if max(w, h) / min(w, h) > 2.5:
        return None, f"not square enough {w}x{h}"
    bbox = im.getchannel("A").getbbox()
    if not bbox:
        return None, "fully transparent"
    flat = Image.new("RGBA", im.size, (255, 255, 255, 255))
    flat.alpha_composite(im)
    st = ImageStat.Stat(flat.convert("L"))
    if st.stddev[0] < 6:
        return None, "near-uniform image"
    im.thumbnail((64, 64), Image.LANCZOS)
    canvas = Image.new("RGBA", (64, 64), (255, 255, 255, 0))
    canvas.alpha_composite(im, ((64 - im.size[0]) // 2, (64 - im.size[1]) // 2))
    buf = io.BytesIO()
    canvas.save(buf, "PNG", optimize=True)
    return buf.getvalue(), {"width": w, "height": h, "format": "png"}


def fetch(url, timeout=25):
    try:
        return request(url, headers=UA, timeout=timeout, retries=1, raw=True)
    except Exception:
        return None


def resolve(name, domain, fc=None, branding=None, rasterize_svg=None):
    """Find a validated logo for one firm. Returns a registry entry (path filled by the caller)."""
    if not domain:
        return {"name": name, "domain": None, "status": "no official domain on file"}
    base = f"https://www.{domain}/" if domain.count(".") == 1 else f"https://{domain}/"
    tried = []
    cands = []
    if branding is None and fc is not None:
        try:
            res, _ = fc.run_url(base, ["branding"]) if hasattr(fc, "run_url") else (None, None)
            branding = (res or {}).get("branding")
        except Exception as e:
            tried.append(f"branding failed: {str(e)[:60]}")
    if branding:
        cands += [("branding:" + k, u) for k, u in candidates_from_branding(branding, base)]
    html = fetch(base)
    if html:
        cands += [("html:" + k, u) for k, u in candidates_from_html(html.decode("utf-8", "replace"), base)]
    seen = set()
    # square icons first: apple-touch-icon / sized icons / favicon before wide logos
    cands.sort(key=lambda c: (0 if "apple-touch" in c[0] else 1 if "favicon" in c[0] or "icon" in c[0] else 2))
    for how, u in cands:
        if u in seen:
            continue
        seen.add(u)
        png, info = validate_png(fetch(u), rasterize_svg)
        if png:
            return {"name": name, "domain": domain, "status": "ok", "source_url": u, "method": how, "png": png, **info, "validated_at": now_iso()}
        tried.append(f"{how}: {info}")
    return {"name": name, "domain": domain, "status": "no valid logo", "tried": tried[:8]}


def run(sb, fc, dry=False, deadline=None, branding_cache=None, rasterize_svg=None, only=None):
    """Fill logos for firms in estimates/firms.json that have none yet."""
    reg = sb.get_json(REGISTRY) or {"firms": {}}
    firms = (sb.get_json("estimates/firms.json") or {}).get("firms") or []
    todo = [f for f in firms if f.get("id") and f["id"] not in reg["firms"] and (not only or f["name"] in only)]
    if dry:
        return {"todo": len(todo), "plan": [f["name"] for f in todo[:20]]}
    done = 0
    for f in todo:
        if deadline and time.time() > deadline:
            break
        e = resolve(f["name"], FIRM_DOMAINS.get(f["name"]), fc, (branding_cache or {}).get(f["name"]), rasterize_svg)
        png = e.pop("png", None)
        if png:
            path = f"estimates/firm-logos/{f['id']}.png"
            sb.put_object("public-data", path, png, "image/png", cache="max-age=604800")
            e["path"] = path
            done += 1
        reg["firms"][f["id"]] = e
    reg["generated"] = now_iso()
    sb.put_json(REGISTRY, reg, cache="max-age=600")
    return {"todo": len(todo), "stored": done}
