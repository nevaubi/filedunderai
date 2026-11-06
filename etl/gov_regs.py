"""Regulation and legislation.

- Federal Register API (federalregister.gov/api/v1, no key): final rules (RULE) and proposed rules (PRORULE) whose
  full text names the company. The query is the quoted legal name (ambiguous names) or quoted display name; the
  API's own search excerpt (with its <span class="match"> marks) is kept verbatim as the cited text. Results whose
  title, abstract and excerpt do not contain one of the company's aliases are dropped.
- Public laws (GovInfo PLAW collection): every law of the 118th and 119th Congress with its title, approval date and
  primary bill from the MODS record. A law is linked to a company when the company's LDA filings named that bill in
  the same Congress ("lobbied, became law"), or when the law's title matches the company's industry topics.
"""
import html as htmllib
import re
import urllib.parse
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor

from .common import now_iso
from .gov_common import (AMBIGUOUS, fetch, find_mentions, get_raw_json, norm_text, put_public, put_raw_json, src)

FR_API = "https://www.federalregister.gov/api/v1/documents.json"
FR_FIELDS = ["document_number", "title", "type", "excerpts", "agencies", "publication_date", "html_url", "pdf_url",
             "significant", "abstract", "action", "effective_on", "comments_close_on", "docket_ids", "regulation_id_numbers", "citation"]
PLAW_SITEMAP = "https://www.govinfo.gov/sitemap/PLAW_{year}_sitemap.xml"
PLAW_MODS = "https://www.govinfo.gov/metadata/pkg/{pkg}/mods.xml"
PLAW_DETAILS = "https://www.govinfo.gov/app/details/{pkg}"
NS = {"m": "http://www.loc.gov/mods/v3"}
BILL_TYPES = {"HR": "H.R.", "S": "S.", "HJRES": "H.J.Res.", "SJRES": "S.J.Res.", "HRES": "H.Res.", "SRES": "S.Res.",
              "HCONRES": "H.Con.Res.", "SCONRES": "S.Con.Res."}


def fr_terms(tk, legal_names, display):
    """Quoted search phrases: the display name when distinctive, otherwise the legal form ("Apple Inc")."""
    from .altdata import core_name, norm_name
    core = core_name(display or "")
    if core and core not in AMBIGUOUS and len(core) > 3:
        return [display if display and not display.isupper() else core.title()]
    terms = []
    for n in legal_names[:2]:
        words = norm_name(n).split()
        c = core_name(n).split()
        if not c or len(words) <= len(c):
            continue
        suffix = {"CORP": "Corporation", "INC": "Inc", "CO": "Company", "LTD": "Ltd", "PLC": "plc", "HOLDINGS": "Holdings"}.get(words[len(c)], words[len(c)].title())
        terms.append(" ".join(w.title() for w in c) + " " + suffix)
    return list(dict.fromkeys(terms))


def _strip(h):
    return norm_text(htmllib.unescape(re.sub(r"<[^>]+>", "", h or "")))


def federal_register(tk, legal_names, display, aliases, per_type=20, log=print):
    retrieved = now_iso()
    out, seen = {"rules": [], "proposed": []}, set()
    terms = fr_terms(tk, legal_names, display)
    for term in terms:
        for typ, key in (("RULE", "rules"), ("PRORULE", "proposed")):
            q = [("conditions[term]", f'"{term}"'), ("conditions[type][]", typ), ("per_page", str(per_type)), ("order", "newest")]
            q += [("fields[]", f) for f in FR_FIELDS]
            url = FR_API + "?" + urllib.parse.urlencode(q)
            d = __import__("json").loads(fetch(url, host_gap=0.3, timeout=60))
            for r in d.get("results") or []:
                if r["document_number"] in seen:
                    continue
                excerpt_html = r.get("excerpts") or ""
                excerpt = _strip(excerpt_html)
                marks = [norm_text(htmllib.unescape(m)) for m in re.findall(r'<span class="match">(.*?)</span>', excerpt_html)]
                text = " ".join([r.get("title") or "", r.get("abstract") or "", excerpt])
                hits = find_mentions(text, aliases, limit=3)
                if not hits:
                    continue
                seen.add(r["document_number"])
                out[key].append({"id": f"fr:{r['document_number']}", "document_number": r["document_number"], "title": r.get("title"),
                                 "type": r.get("type"), "action": r.get("action"), "date": r.get("publication_date"),
                                 "effective_on": r.get("effective_on"), "comments_close_on": r.get("comments_close_on"),
                                 "significant": r.get("significant"), "citation": r.get("citation"),
                                 "agencies": [a.get("name") or a.get("raw_name") for a in r.get("agencies") or [] if a.get("name") or a.get("raw_name")],
                                 "abstract": (r.get("abstract") or "")[:1200], "excerpt": excerpt[:1500], "marks": marks[:6],
                                 "match": norm_text(text[hits[0][0]:hits[0][1]]), "term": term,
                                 "url": r.get("html_url"), "pdf_url": r.get("pdf_url"), "docket_ids": r.get("docket_ids") or [],
                                 "src": src(url.split("&fields")[0], "Federal Register API full-text search (quoted name); excerpt verbatim from API", retrieved)})
            out.setdefault("counts", {})[f"{key}:{term}"] = d.get("count")
    for k in ("rules", "proposed"):
        out[k].sort(key=lambda r: r.get("date") or "", reverse=True)
    out.update({"terms": terms, "retrieved_at": retrieved})
    return out


# ---------------------------------------------------------------- public laws

def parse_plaw_mods(xml_text):
    root = ET.fromstring(xml_text.encode() if isinstance(xml_text, str) else xml_text)
    title, number, congress, date, bills, short = None, None, None, None, [], []
    for ti in root.findall("m:titleInfo", NS):
        t = (ti.findtext("m:title", default="", namespaces=NS) or "").strip()
        if ti.get("type") is None and not title:
            title = t
        elif t and not t.startswith("Public Law"):
            short.append(t)
    date = root.findtext("m:originInfo/m:dateIssued", default=None, namespaces=NS)
    for e in root.findall("m:extension", NS):
        congress = congress or e.findtext("m:congress", default=None, namespaces=NS)
        number = number or e.findtext("m:number", default=None, namespaces=NS)
        for b in e.findall("m:bill", NS):
            if b.get("priority") == "primary":
                bills.append(f"{BILL_TYPES.get((b.get('type') or '').upper(), b.get('type'))} {b.get('number')}")
        for st in e.findall("m:shortTitle", NS):
            if st.text:
                short.append(st.text.strip())
    return {"title": title, "short_titles": list(dict.fromkeys(short))[:4], "date": date, "congress": int(congress) if congress else None,
            "number": number, "bills": list(dict.fromkeys(bills))}


def sync_laws(sb, years, log=print, workers=4):
    doc = get_raw_json(sb, "govraw/plaw/index.json", {"laws": {}}) or {"laws": {}}
    pkgs = []
    for y in years:
        try:
            x = fetch(PLAW_SITEMAP.format(year=y), host_gap=0.12, timeout=90)
            pkgs += re.findall(r"/app/details/(PLAW-\d+publ\d+)</loc>", x)
        except Exception as e:
            log(f"plaw sitemap {y}: {str(e)[:120]}")
    todo = [p for p in dict.fromkeys(pkgs) if p not in doc["laws"]]

    def one(p):
        try:
            m = parse_plaw_mods(fetch(PLAW_MODS.format(pkg=p), host_gap=0.12, timeout=60))
            m.update({"id": p, "url": PLAW_DETAILS.format(pkg=p), "retrieved_at": now_iso(),
                      "label": f"Public Law {m.get('congress')}-{m.get('number')}"})
            return p, m
        except Exception as e:
            return p, {"id": p, "error": str(e)[:160]}
    with ThreadPoolExecutor(workers) as ex:
        for p, m in ex.map(one, todo):
            doc["laws"][p] = m
    doc["updated_at"] = now_iso()
    put_raw_json(sb, "govraw/plaw/index.json", doc)
    laws = [v for v in doc["laws"].values() if not v.get("error")]
    laws.sort(key=lambda l: (l.get("date") or ""), reverse=True)
    put_public(sb, "gov/laws.json", {"generated": now_iso(), "count": len(laws), "laws": laws,
                                     "src": src("https://www.govinfo.gov/app/collection/plaw", "GovInfo PLAW sitemap + MODS records")}, cache="max-age=3600")
    return laws


def link_laws(laws, lobby, sector_rx, limit=25):
    """Laws tied to a company: bills it lobbied on that became law, then industry-topic laws by title."""
    by_bill = {}
    for l in laws:
        for b in l.get("bills") or []:
            by_bill[(b, l.get("congress"))] = l
    out, seen = [], set()
    for b in (lobby or {}).get("bills") or []:
        l = by_bill.get((b["bill"], b.get("congress")))
        if l and l["id"] not in seen:
            seen.add(l["id"])
            out.append({**{k: l.get(k) for k in ("id", "label", "title", "short_titles", "date", "congress", "bills", "url")},
                        "link": "lobbied", "bill": b["bill"], "lobby_filings": b["filings"], "lobby_cites": b.get("cites", [])[:2],
                        "src": src(l.get("url"), "GovInfo PLAW MODS (primary bill) matched to LDA activity text", l.get("retrieved_at"))})
    if sector_rx:
        rx = re.compile(sector_rx, re.I)
        for l in laws:
            if l["id"] in seen:
                continue
            t = " ".join([l.get("title") or ""] + (l.get("short_titles") or []))
            m = rx.search(t)
            if m and len([x for x in out if x["link"] == "industry"]) < 12:
                seen.add(l["id"])
                out.append({**{k: l.get(k) for k in ("id", "label", "title", "short_titles", "date", "congress", "bills", "url")},
                            "link": "industry", "match": m.group(0),
                            "src": src(l.get("url"), "GovInfo PLAW MODS; title matched industry topic", l.get("retrieved_at"))})
    out.sort(key=lambda r: (r["link"] != "lobbied", -(int((r.get("date") or "0000")[:4]))))
    return out[:limit]
