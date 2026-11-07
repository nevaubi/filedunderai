"""Filing language themes: how companies' 10-K and 10-Q language on a theme (tariffs, AI, GLP-1, ...) changes over time.

Pipeline (no AI; every number traces to sentences in a filing):

1. Index. For each company, the 10-K and 10-Q filings since SINCE are turned into section text (Business, Risk
   factors, Cybersecurity, MD&A, Market risk; Part I Items 2/3 and Part II Item 1A for 10-Qs) with redlines_core's
   Item splitter. Text comes from stored copies first (supply-chain archive, viewer copies, archived originals) and
   only then from EDGAR under the shared SEC throttle. Section text is kept in the raw bucket (themes/text/{accn}),
   so theme definitions can change without refetching.
2. Match. Each sentence is tested against every theme's include/exclude patterns (etl/themes_defs.json). Hits keep
   the sentence, its section, a digit-masked hash, a negation flag ("did not have a material impact") and a
   safe-harbor flag (forward-looking statement lists). Per company: raw themes/co/{T}.json.gz.
3. Aggregate. Sentences repeated word-for-word in many companies' filings are shared boilerplate and do not count.
   A filing is compared like for like (10-K with the prior 10-K, 10-Q with the same quarter a year earlier): new
   sentences are matched sentences with no near-identical sentence in the prior filing; a company "added" a theme
   when the prior filing had no counting sentence and the new one has, "dropped" the reverse (only when the new
   filing parsed the sections the old sentences came from). Quarterly shares, intensity (counting sentences per
   10,000 words), sector splits, added/dropped lists with the exact sentences and links, and baskets ranked by
   disclosed exposure (reported geographic revenue share, segment names, supply-graph counterparties).

Outputs (public-data): themes/index.json, themes/{id}.json, themes/co/{T}.json.
"""
import collections
import concurrent.futures as cf
import datetime as dt
import gzip
import hashlib
import json
import os
import re
import time
import urllib.parse

from . import redlines_core as R
from .common import HTTPError, now_iso, request, sec_get, sec_json
from .supply_core import html_to_text
from .universe import BY_TICKER, UNIVERSE

DEFS_PATH = os.path.join(os.path.dirname(__file__), "themes_defs.json")
SINCE = "2021-01-01"
ENGINE = 1
SECTIONS = {
    "10-K": {"1": "Business", "1A": "Risk factors", "1C": "Cybersecurity", "7": "MD&A", "7A": "Market risk"},
    "10-Q": {"I-2": "MD&A", "I-3": "Market risk", "II-1A": "Risk factors"},
}
TEXT_PATH = "themes/text/{accn}.json.gz"
CO_RAW = "themes/co/{tk}.json.gz"
MAX_HITS = 80          # sentences kept per theme per filing (all hashes are kept)
BOILER_MIN = 10        # a digit-masked sentence in this many companies' filings is shared boilerplate
SAME = 0.82            # token Jaccard at or above which two sentences count as the same sentence
ARCHIVE = "https://www.sec.gov/Archives/edgar/data"


# ---------------------------------------------------------------- definitions
def load_defs(path=DEFS_PATH):
    raw = json.load(open(path))
    themes = []
    for t in raw["themes"]:
        themes.append({**t, "_inc": [_rx(p) for p in t["include"]], "_exc": [_rx(p) for p in t.get("exclude") or []]})
    return {"version": raw["version"], "themes": themes, "notes": raw.get("notes"),
            "_neg": re.compile(raw["negation"], re.I), "_fls": re.compile(raw["safe_harbor"], re.I)}


def _rx(p):
    if p.startswith("(?-i)"):
        return re.compile(p[5:])
    return re.compile(p, re.I)


def defs_fingerprint(d):
    body = json.dumps([{k: v for k, v in t.items() if not k.startswith("_")} for t in d["themes"]], sort_keys=True)
    return f"v{d['version']}-{hashlib.sha1(body.encode()).hexdigest()[:8]}"


# ---------------------------------------------------------------- text
_SENT = re.compile(r"(?<=[.!?])[\"”’)]?\s+(?=[A-Z(\"“‘$0-9])")
_WS = re.compile(r"\s+")
_TOK = re.compile(r"[a-z]+")


def sentences(para):
    """Sentences of one paragraph; very short fragments and table-like lines are dropped."""
    out = []
    for s in _SENT.split(para):
        s = _WS.sub(" ", s).strip()
        if len(s) < 40 or len(s) > 1500:
            continue
        letters = sum(c.isalpha() for c in s)
        if letters < 0.55 * len(s):
            continue
        out.append(s)
    return out


def norm(s):
    return _WS.sub(" ", re.sub(r"\d[\d,.]*", "#", s.lower())).strip(" .;:")


def shash(s):
    return hashlib.sha1(norm(s).encode()).hexdigest()[:12]


def tokens(s):
    return frozenset(_TOK.findall(s.lower()))


def same_sentence(a, b):
    if not a or not b:
        return False
    return len(a & b) / max(1, len(a | b)) >= SAME


def extract_sections(text, form):
    """{code: [paragraph, ...]} for the indexed Items; a filing whose Items cannot be found falls back to its
    narrative paragraphs under "full" (tables and very short lines removed)."""
    base = "10-K" if form.startswith("10-K") else "10-Q"
    want = SECTIONS[base]
    try:
        items = R.split_items(text, base)
    except Exception:
        items = {}
    out = {code: v["paras"] for code, v in items.items() if code in want and v["paras"]}
    if not out:
        paras = [p for p in text.split("\n") if len(p) >= 80 and sum(c.isalpha() for c in p) >= 0.6 * len(p)]
        if paras:
            out = {"full": paras}
    return out


def section_title(form, code):
    base = "10-K" if form.startswith("10-K") else "10-Q"
    return SECTIONS[base].get(code, "Full text")


# ---------------------------------------------------------------- matching
def match_sentence(defs, s):
    """[(theme_id, negated, safe_harbor)] for one sentence."""
    hits = []
    for t in defs["themes"]:
        if any(rx.search(s) for rx in t["_inc"]) and not any(rx.search(s) for rx in t["_exc"]):
            hits.append(t["id"])
    if not hits:
        return []
    neg = bool(defs["_neg"].search(s))
    fls = bool(defs["_fls"].search(s))
    return [(h, neg, fls) for h in hits]


def index_sections(defs, sections, form):
    """Per filing: words per section and, per theme, the matching sentences."""
    words, hits = {}, collections.defaultdict(list)
    for code, paras in sections.items():
        words[code] = sum(len(p.split()) for p in paras)
        seen = set()
        for p in paras:
            for s in sentences(p):
                h = shash(s)
                if h in seen:
                    continue
                seen.add(h)
                for tid, neg, fls in match_sentence(defs, s):
                    row = {"s": code, "h": h}
                    if neg:
                        row["n"] = 1
                    if fls:
                        row["f"] = 1
                    if len(hits[tid]) < MAX_HITS:
                        row["t"] = s
                    hits[tid].append(row)
    return words, dict(hits)


# ---------------------------------------------------------------- storage helpers
def _list(sb, bucket, prefix, limit=1000):
    out, off = [], 0
    while True:
        body = json.loads(request(f"{sb.url}/storage/v1/object/list/{bucket}", method="POST",
                                  data={"prefix": prefix, "limit": limit, "offset": off,
                                        "sortBy": {"column": "updated_at", "order": "desc"}},
                                  headers=sb._h({"Content-Type": "application/json"}), timeout=60, retries=3))
        out += body
        off += limit
        if len(body) < limit:
            return out


def _gunzip(b):
    if b is None:
        return None
    try:
        return gzip.decompress(b)
    except OSError:
        return b


def get_raw_json(sb, path):
    try:
        b = sb.get_object("raw", path)
    except Exception:
        return None
    b = _gunzip(b)
    return json.loads(b) if b else None


def put_raw_json(sb, path, obj):
    sb.put_object("raw", path, gzip.compress(json.dumps(obj, separators=(",", ":")).encode()), "application/gzip", cache="no-store")


def submissions(sb, tk, allow_sec=True):
    """Latest archived SEC submissions feed for a company (written by the companies job); EDGAR as a fallback."""
    cik = BY_TICKER[tk]["cik"]
    try:
        rows = _list(sb, "raw", f"sec/submissions/archives/CIK{cik:010d}/", limit=100)
        files = [r for r in rows if r.get("metadata")]
        if files:
            files.sort(key=lambda r: r.get("updated_at") or "", reverse=True)
            b = _gunzip(sb.get_object("raw", f"sec/submissions/archives/CIK{cik:010d}/{files[0]['name']}"))
            if b:
                return json.loads(b), "archive"
    except Exception:
        pass
    if not allow_sec:
        return None, None
    return sec_json(f"https://data.sec.gov/submissions/CIK{cik:010d}.json"), "sec"


def periodic(sub, since=SINCE, allow_sec=True):
    """10-K and 10-Q filings (no amendments) filed on or after `since`, newest first."""
    def rows(block):
        n = len(block.get("accessionNumber", []))
        for i in range(n):
            yield {"form": block["form"][i], "filed": block["filingDate"][i], "accn": block["accessionNumber"][i],
                   "doc": block["primaryDocument"][i], "report": (block.get("reportDate") or [""] * n)[i] or ""}
    recent = list(rows(sub.get("filings", {}).get("recent", {})))
    out = [r for r in recent if r["form"] in ("10-K", "10-Q", "10-KT") and r["doc"] and r["filed"] >= since]
    oldest = min((r["filed"] for r in recent), default="")
    if allow_sec and oldest and oldest > since:
        for f in (sub.get("filings", {}).get("files") or [])[:2]:
            if (f.get("filingTo") or "9999") < since:
                continue
            try:
                block = sec_json("https://data.sec.gov/submissions/" + f["name"])
            except Exception:
                continue
            out += [r for r in rows(block) if r["form"] in ("10-K", "10-Q", "10-KT") and r["doc"] and r["filed"] >= since]
    seen, uniq = set(), []
    for r in sorted(out, key=lambda r: r["filed"], reverse=True):
        if r["accn"] not in seen:
            seen.add(r["accn"])
            uniq.append({**r, "form": "10-K" if r["form"] == "10-KT" else r["form"]})
    return uniq


def doc_url(cik, accn, doc):
    return f"{ARCHIVE}/{int(cik)}/{accn.replace('-', '')}/{doc}"


def stored_text(sb, accn, doc):
    """(plain text, source) from copies already in storage, or (None, None)."""
    try:
        b = sb.get_object("raw", f"supplyraw/docs/{accn}/{doc}.txt.gz")
        if b:
            return _gunzip(b).decode("utf-8", "replace"), "supply archive"
    except Exception:
        pass
    try:
        b = sb.get_object("public-data", f"filings/{accn}.html.gz", public=True)
        if b:
            return html_to_text(_gunzip(b).decode("utf-8", "replace")), "viewer copy"
    except Exception:
        pass
    try:
        b = sb.get_object("raw", f"sec/filings/{accn}/{doc}.gz")
        if b:
            return html_to_text(_gunzip(b).decode("utf-8", "replace")), "archived original"
    except Exception:
        pass
    return None, None


# ---------------------------------------------------------------- per company index
def index_company(sb, tk, defs, allow_sec=True, since=SINCE, floor=None, deadline=None, max_fetch=None, log=print):
    """Index a company's filings filed on or after `floor` (default `since`), newest first. Returns stats."""
    fp = defs_fingerprint(defs)
    path = CO_RAW.format(tk=tk)
    doc = get_raw_json(sb, path) or {}
    if doc.get("engine") != ENGINE:
        doc = {}
    filings = doc.get("filings") or {}
    sub, src = submissions(sb, tk, allow_sec=allow_sec)
    stats = {"indexed": 0, "rematched": 0, "fetched": 0, "missing": 0, "skipped": 0}
    if not sub:
        stats["error"] = "no submissions feed"
        return stats
    cik = BY_TICKER[tk]["cik"]
    todo = periodic(sub, since=since, allow_sec=allow_sec)
    lo = floor or since
    changed = False
    for f in todo:
        if f["filed"] < lo:
            continue
        if deadline and time.time() > deadline:
            stats["skipped"] += 1
            continue
        have = filings.get(f["accn"])
        if have and have.get("defs") == fp:
            continue
        sections = None
        if have:
            cached = get_raw_json(sb, TEXT_PATH.format(accn=f["accn"]))
            if cached:
                sections = cached.get("sections")
                stats["rematched"] += 1
        if sections is None:
            text, source = stored_text(sb, f["accn"], f["doc"])
            if text is None:
                if not allow_sec or (max_fetch is not None and stats["fetched"] >= max_fetch):
                    stats["skipped"] += 1
                    continue
                try:
                    text, source = html_to_text(sec_get(doc_url(cik, f["accn"], f["doc"]), timeout=120)), "EDGAR"
                    stats["fetched"] += 1
                except HTTPError as e:
                    log(f"themes {tk} {f['accn']}: HTTP {e.code}")
                    stats["missing"] += 1
                    continue
                except Exception as e:
                    log(f"themes {tk} {f['accn']}: {str(e)[:120]}")
                    stats["missing"] += 1
                    continue
            sections = extract_sections(text, f["form"])
            put_raw_json(sb, TEXT_PATH.format(accn=f["accn"]), {"accn": f["accn"], "form": f["form"], "source": source,
                                                               "sections": sections})
            stats["indexed"] += 1
        words, hits = index_sections(defs, sections, f["form"])
        filings[f["accn"]] = {"a": f["accn"], "f": f["form"], "d": f["filed"], "r": f["report"], "p": f["doc"],
                              "w": words, "hits": hits, "defs": fp}
        changed = True
    if changed or not doc:
        put_raw_json(sb, path, {"ticker": tk, "cik": cik, "engine": ENGINE, "generated": now_iso(), "filings": filings})
    stats["filings"] = len(filings)
    return stats


# ---------------------------------------------------------------- aggregation
def quarter(d):
    y, m = int(d[:4]), int(d[5:7])
    return f"{y}Q{(m - 1) // 3 + 1}"


def prior_like(filings, f):
    """Like-for-like prior filing: the previous 10-K for a 10-K; for a 10-Q the 10-Q whose period ended 330-400 days
    earlier (filing dates when report dates are missing)."""
    cands = [g for g in filings if g["f"] == f["f"] and g["d"] < f["d"]]
    if not cands:
        return None
    if f["f"] == "10-K":
        return max(cands, key=lambda g: g["d"])
    def day(x):
        return dt.date.fromisoformat(x[:10])
    ref = f.get("r") or f["d"]
    best = None
    for g in cands:
        gr = g.get("r") or g["d"]
        gap = (day(ref) - day(gr)).days
        if 330 <= gap <= 400 and (best is None or g["d"] > best["d"]):
            best = g
    return best


def counting(rows, boiler):
    return [r for r in rows if not r.get("f") and r["h"] not in boiler]


def boilerplate(companies):
    """Digit-masked sentence hashes that occur in at least BOILER_MIN companies' filings, per theme."""
    seen = collections.defaultdict(set)
    for tk, doc in companies.items():
        for f in (doc.get("filings") or {}).values():
            for tid, rows in (f.get("hits") or {}).items():
                for r in rows:
                    seen[(tid, r["h"])].add(tk)
    out = collections.defaultdict(set)
    for (tid, h), tks in seen.items():
        if len(tks) >= BOILER_MIN:
            out[tid].add(h)
    return out


def company_view(tk, doc, defs, boiler):
    """Per filing per theme: counting sentences, new sentences vs the like-for-like prior, added/dropped."""
    fl = sorted((doc.get("filings") or {}).values(), key=lambda f: f["d"])
    by_accn = {f["a"]: f for f in fl}
    out = []
    for f in fl:
        p = prior_like(fl, f)
        row = {"a": f["a"], "f": f["f"], "d": f["d"], "r": f.get("r"), "p": f.get("p"), "q": quarter(f["d"]),
               "w": sum((f.get("w") or {}).values()), "prior": p["a"] if p else None, "themes": {}}
        for t in defs["themes"]:
            tid = t["id"]
            cur = counting((f.get("hits") or {}).get(tid) or [], boiler[tid])
            entry = {"n": len(cur), "neg": sum(1 for r in cur if r.get("n"))}
            if p is not None:
                old = counting((p.get("hits") or {}).get(tid) or [], boiler[tid])
                old_h = {r["h"] for r in old}
                old_tok = [tokens(r["t"]) for r in old if r.get("t")]
                new = []
                for r in cur:
                    if r["h"] in old_h:
                        continue
                    tk_ = tokens(r.get("t") or "")
                    if r.get("t") and any(same_sentence(tk_, o) for o in old_tok):
                        continue
                    new.append(r)
                entry["new"] = len(new)
                entry["new_rows"] = new
                entry["old_n"] = len(old)
                # A drop counts only when the new filing parsed the sections the old sentences came from.
                old_secs = {r["s"] for r in old}
                parsed = set((f.get("w") or {}).keys())
                entry["comparable"] = not old_secs or bool(old_secs & parsed) or "full" in parsed
                if old and not cur and not entry["comparable"]:
                    entry["comparable"] = False
            entry["rows"] = cur
            row["themes"][tid] = entry
        out.append(row)
    return out, by_accn


def _excerpts(rows, k=2):
    rows = [r for r in rows if r.get("t")]
    rows.sort(key=lambda r: (bool(r.get("n")), -len(r["t"])))
    return [{"text": r["t"], "section": r["s"], "negated": bool(r.get("n"))} for r in rows[:k]]


def sec_link(cik, f):
    return doc_url(cik, f["a"], f["p"]) if f.get("p") else f"{ARCHIVE}/{int(cik)}/{f['a'].replace('-', '')}/"


# ---------------------------------------------------------------- baskets
_GEO_ROW = re.compile(r"\b(americas|america|united\s+states|u\.s\.|us\b|europe|emea|asia|apac|pacific|china|japan|"
                      r"international|international\s+operations|other\s+countries|rest\s+of|canada|mexico|latin|"
                      r"germany|united\s+kingdom|france|india|korea|taiwan|middle\s+east|africa|domestic|foreign|"
                      r"north\s+america|south\s+america|greater\s+china|hong\s+kong|singapore|australia|brazil)\b", re.I)


def geo_share(seg, rx):
    """(share, label, period) of revenue reported for geographies matching `rx` in the latest annual period of a
    geography (or geography-shaped segment) revenue table; None when nothing matches."""
    if not seg or not rx:
        return None
    best = None
    for t in seg.get("tables") or []:
        if t.get("family") != "revenue":
            continue
        rows = [r for r in t.get("rows") or [] if r.get("depth", 0) == 0 and not r.get("recon") and not r.get("memo")]
        georows = [r for r in rows if _GEO_ROW.search(r.get("label") or "")]
        if t.get("category") != "geo" and len(georows) < 2:
            continue
        use = rows if t.get("category") == "geo" else georows
        periods = collections.Counter(p for r in use for p in (r.get("A") or {}))
        if not periods:
            continue
        period = max(p for p, n in periods.items() if n >= 2) if any(n >= 2 for n in periods.values()) else None
        if not period:
            continue
        vals = [(r["label"], (r.get("A") or {}).get(period, {}).get("v")) for r in use]
        vals = [(l, v) for l, v in vals if isinstance(v, (int, float)) and v > 0]
        total = sum(v for _, v in vals)
        hit = [(l, v) for l, v in vals if rx.search(l)]
        if not total or not hit:
            continue
        share = sum(v for _, v in hit) / total
        if share >= 0.999:
            continue
        cand = (share, ", ".join(l for l, _ in hit), period, t.get("id"))
        if best is None or period > best[2] or (period == best[2] and share > best[0]):
            best = cand
    return best


def basket(theme, views, names, sectors, seg_get, graph, latest_rows):
    """Companies ranked by disclosed exposure: reported revenue share (geography, or customer share to a named
    counterparty), then the number of independent exposure evidence types, then counting sentences in the latest
    filing. Every entry carries its evidence."""
    cfg = theme.get("basket") or {}
    geo_rx = re.compile(cfg["geo_terms"], re.I) if cfg.get("geo_terms") else None
    seg_rx = re.compile(cfg["segment_terms"], re.I) if cfg.get("segment_terms") else None
    cps = [c.lower() for c in cfg.get("counterparties") or []]
    regions = {r.lower() for r in cfg.get("counterparty_regions") or []}
    links = collections.defaultdict(list)
    for l in (graph or {}).get("links") or []:
        cp = (l.get("cp") or "").lower()
        cpt = (l.get("cpt") or "").lower()
        rg = {x.lower() for x in (l.get("rg") or [])} | ({(l.get("hq") or "").lower()} if l.get("hq") else set())
        named = any(c == cpt or c in cp for c in cps) if cps else False
        regional = bool(regions & rg) and l.get("rel") in ("supplier", "customer")
        if named or regional:
            links[l["t"]].append(l)
    out = []
    for tk in set(latest_rows) | set(links):
        ev = []
        share = None
        seg = seg_get(tk) if (geo_rx or seg_rx) else None
        if geo_rx:
            g = geo_share(seg, geo_rx)
            if g:
                share = g[0]
                ev.append({"type": "revenue share", "text": f"{g[1]}: {g[0]:.1%} of revenue in reported geography table, period ended {g[2]}",
                           "value": round(g[0], 4)})
        if seg_rx and seg:
            labels = sorted({r["label"] for t in seg.get("tables") or [] if t.get("family") == "revenue" and t.get("category") in ("segment", "product")
                             for r in t.get("rows") or [] if seg_rx.search(r.get("label") or "")})
            if labels:
                ev.append({"type": "segment", "text": "Reported revenue lines: " + "; ".join(labels[:4])})
        ls = links.get(tk) or []
        if ls:
            sh = [l for l in ls if l.get("sh")]
            if sh:
                top = max(sh, key=lambda l: float(l["sh"]))
                v = float(top["sh"]) / 100 if float(top["sh"]) > 1 else float(top["sh"])
                share = max(share or 0, v)
                ev.append({"type": "customer share", "text": f"{top['cp']}: {v:.0%} of revenue ({top['form']} filed {top['filed']})",
                           "value": round(v, 4), "quote": top.get("q")})
            named = sorted({l["cp"] for l in ls})
            ev.append({"type": "counterparties", "text": "Named in filings: " + "; ".join(named[:5]) + (f" (+{len(named) - 5})" if len(named) > 5 else ""),
                       "quote": ls[0].get("q"), "filed": ls[0].get("filed")})
        lr = latest_rows.get(tk)
        if lr and lr["n"]:
            ev.append({"type": "language", "text": f"{lr['n']} counting sentence{'s' if lr['n'] != 1 else ''} in the {lr['form']} filed {lr['filed']}",
                       "excerpts": lr["excerpts"][:1]})
        kinds = {e["type"] for e in ev if e["type"] != "language"}
        if not kinds:
            continue
        out.append({"ticker": tk, "name": names.get(tk), "sector": sectors.get(tk), "disclosed_share": round(share, 4) if share is not None else None,
                    "evidence_types": len(kinds), "mentions_latest": (lr or {}).get("n", 0), "evidence": ev})
    out.sort(key=lambda r: (-(r["disclosed_share"] if r["disclosed_share"] is not None else -1), -r["evidence_types"], -r["mentions_latest"]))
    return out[:60]


# ---------------------------------------------------------------- aggregate job
def aggregate(companies, defs, seg_get=None, graph=None, quarters=24, lists_quarters=6, per_list=60):
    """companies: {ticker: raw company index}. Returns (index, {theme_id: theme doc}, {ticker: company theme doc})."""
    names = {t: n for t, _c, n, _s in UNIVERSE}
    sectors = {t: s for t, _c, _n, s in UNIVERSE}
    boiler = boilerplate(companies)
    views = {tk: company_view(tk, doc, defs, boiler) for tk, doc in companies.items()}
    all_q = sorted({r["q"] for v, _ in views.values() for r in v})
    all_q = all_q[-quarters:]
    themes_out, co_out = {}, {}
    index = {"generated": now_iso(), "engine": ENGINE, "defs": defs_fingerprint(defs), "quarters": all_q,
             "companies_indexed": len(companies),
             "filings_indexed": sum(len(v) for v, _ in views.values()),
             "method": __doc__.split("\n\n")[1].strip() if __doc__ else "",
             "notes": defs.get("notes"), "themes": []}
    for t in defs["themes"]:
        tid = t["id"]
        per_q = {q: {"filers": set(), "mention": set(), "comparable": set(), "new": set(), "added": set(), "dropped": set(),
                     "hits": 0, "neg": 0, "words": 0} for q in all_q}
        sec_q = collections.defaultdict(lambda: collections.defaultdict(lambda: {"filers": set(), "mention": set(), "new": set(), "comparable": set()}))
        adds, drops, news = collections.defaultdict(list), collections.defaultdict(list), collections.defaultdict(list)
        latest_rows = {}
        for tk, (rows, by_accn) in views.items():
            cik = BY_TICKER[tk]["cik"] if tk in BY_TICKER else 0
            sec = sectors.get(tk) or "Other"
            for r in rows:
                e = r["themes"][tid]
                if r["q"] in per_q:
                    b = per_q[r["q"]]
                    b["filers"].add(tk)
                    b["words"] += r["w"]
                    b["hits"] += e["n"]
                    b["neg"] += e["neg"]
                    s = sec_q[sec][r["q"]]
                    s["filers"].add(tk)
                    if e["n"]:
                        b["mention"].add(tk)
                        s["mention"].add(tk)
                    if "new" in e:
                        b["comparable"].add(tk)
                        s["comparable"].add(tk)
                        if e["new"]:
                            b["new"].add(tk)
                            s["new"].add(tk)
                        f = by_accn[r["a"]]
                        p = by_accn.get(r["prior"])
                        base = {"ticker": tk, "name": names.get(tk), "sector": sec, "form": r["f"], "filed": r["d"], "accession": r["a"],
                                "doc": f.get("p"), "url": sec_link(cik, f),
                                "prior": {"form": p["f"], "filed": p["d"], "accession": p["a"], "doc": p.get("p"), "url": sec_link(cik, p)} if p else None}
                        if e["n"] and not e.get("old_n"):
                            b["added"].add(tk)
                            adds[r["q"]].append({**base, "sentences": e["n"], "excerpts": _excerpts(e["rows"])})
                        elif e.get("old_n") and not e["n"] and e.get("comparable"):
                            b["dropped"].add(tk)
                            oldrows = counting((p.get("hits") or {}).get(tid) or [], boiler[tid]) if p else []
                            drops[r["q"]].append({**base, "sentences_before": e["old_n"], "excerpts": _excerpts(oldrows)})
                        elif e["new"]:
                            news[r["q"]].append({**base, "sentences": e["n"], "new_sentences": e["new"], "excerpts": _excerpts(e["new_rows"])})
            if rows:
                last = rows[-1]
                e = last["themes"][tid]
                latest_rows[tk] = {"n": e["n"], "form": last["f"], "filed": last["d"], "excerpts": _excerpts(e["rows"], 2),
                                   "accession": last["a"], "doc": by_accn[last["a"]].get("p"), "url": sec_link(cik, by_accn[last["a"]]), "new": e.get("new"),
                                   "added": bool(e["n"] and "new" in e and not e.get("old_n")),
                                   "dropped": bool(e.get("old_n") and not e["n"] and e.get("comparable"))}
        series = []
        for q in all_q:
            b = per_q[q]
            nf, nm, nc = len(b["filers"]), len(b["mention"]), len(b["comparable"])
            series.append({"q": q, "filers": nf, "mentioning": nm, "share": round(nm / nf, 4) if nf else None,
                           "comparable": nc, "new_language": len(b["new"]), "new_share": round(len(b["new"]) / nc, 4) if nc else None,
                           "added": len(b["added"]), "dropped": len(b["dropped"]), "sentences": b["hits"],
                           "negated_share": round(b["neg"] / b["hits"], 4) if b["hits"] else None,
                           "intensity": round(b["hits"] / b["words"] * 10000, 3) if b["words"] else None})
        by_sector = {}
        for sec, qs in sec_q.items():
            by_sector[sec] = [{"q": q, "filers": len(qs[q]["filers"]), "mentioning": len(qs[q]["mention"]),
                               "share": round(len(qs[q]["mention"]) / len(qs[q]["filers"]), 4) if qs[q]["filers"] else None,
                               "new_share": round(len(qs[q]["new"]) / len(qs[q]["comparable"]), 4) if qs[q]["comparable"] else None}
                              for q in all_q if q in qs]
        recent_q = all_q[-lists_quarters:]
        def top(lst, key):
            return sorted(lst, key=key)[:per_list]
        changes = {q: {"added": top(adds[q], lambda x: -x["sentences"]), "dropped": top(drops[q], lambda x: -x["sentences_before"]),
                       "new_language": top(news[q], lambda x: -x["new_sentences"])} for q in recent_q}
        bask = basket(t, views, names, sectors, seg_get or (lambda tk: None), graph, latest_rows)
        doc = {"id": tid, "name": t["name"], "description": t["description"], "generated": index["generated"],
               "defs": index["defs"], "include": t["include"], "exclude": t.get("exclude") or [],
               "boilerplate_sentences": len(boiler[tid]), "series": series, "by_sector": by_sector, "changes": changes,
               "basket": bask}
        themes_out[tid] = doc
        last = next((s for s in reversed(series) if s["filers"] >= 50), series[-1] if series else None)
        index["themes"].append({"id": tid, "name": t["name"], "description": t["description"],
                                "spark": [{"q": s["q"], "share": s["share"], "new_share": s["new_share"]} for s in series],
                                "latest": last, "basket_top": [b["ticker"] for b in bask[:8]]})
        for tk, lr in latest_rows.items():
            co_out.setdefault(tk, {"ticker": tk, "generated": index["generated"], "themes": {}})
            if lr["n"] or lr["dropped"]:
                co_out[tk]["themes"][tid] = {**lr, "name": t["name"]}
    return index, themes_out, co_out


def _seg_loader(sb):
    cache = {}
    def get(tk):
        if tk not in cache:
            try:
                b = sb.get_object("public-data", f"segments/{tk}.json", public=True)
                cache[tk] = json.loads(_gunzip(b)) if b else None
            except Exception:
                cache[tk] = None
        return cache[tk]
    return get


def load_graph(sb):
    try:
        b = sb.get_object("public-data", "supply/graph.json.gz", public=True)
        return json.loads(_gunzip(b)) if b else None
    except Exception:
        return None


def aggregate_job(sb, tickers=None, workers=16, log=print):
    """Reads every company index, writes themes/index.json, themes/{id}.json and themes/co/{T}.json."""
    defs = load_defs()
    tickers = tickers or [t for t, *_ in UNIVERSE]

    def one(tk):
        return tk, get_raw_json(sb, CO_RAW.format(tk=tk))
    companies = {}
    with cf.ThreadPoolExecutor(workers) as ex:
        for tk, doc in ex.map(one, tickers):
            if doc and doc.get("filings"):
                companies[tk] = doc
    log(f"themes: {len(companies)} companies with indexed filings")
    seg = _seg_loader(sb)
    # Pre-load segments in parallel for the basket pass.
    with cf.ThreadPoolExecutor(workers) as ex:
        list(ex.map(seg, list(companies)))
    index, themes_out, co_out = aggregate(companies, defs, seg_get=seg, graph=load_graph(sb))
    for tid, doc in themes_out.items():
        sb.put_json(f"themes/{tid}.json", doc, cache="max-age=900")

    def put(item):
        tk, doc = item
        sb.put_json(f"themes/co/{tk}.json", doc, cache="max-age=900")
    with cf.ThreadPoolExecutor(workers) as ex:
        list(ex.map(put, co_out.items()))
    sb.put_json("themes/index.json", index, cache="max-age=900")
    return {"companies": len(companies), "filings": index["filings_indexed"], "themes": len(themes_out), "company_files": len(co_out)}
