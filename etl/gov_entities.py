"""Company identity for entity matching: SEC registrant name, former names, SIC, and Exhibit 21 subsidiaries.

Stored once for the universe in the private raw bucket (govraw/entities.json) and refreshed by rotation. Subsidiary
names come from the Exhibit 21 attached to the latest 10-K; they are used only for exact normalized-name matching
(USAspending recipients, LDA clients), never for free-text search.
"""
import html as htmllib
import re
from concurrent.futures import ThreadPoolExecutor

from .common import now_iso, sec_get, sec_json
from .gov_common import get_raw_json, put_raw_json, tickers
from .universe import BY_TICKER

PATH = "govraw/entities.json"


def _ex21_names(doc_html):
    """Subsidiary names from an Exhibit 21 document: first cell of table rows, or list lines."""
    text = doc_html
    names = []
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", text, re.S | re.I)
    if rows:
        for r in rows:
            cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", r, re.S | re.I)
            if not cells:
                continue
            first = htmllib.unescape(re.sub(r"<[^>]+>", " ", cells[0]))
            first = re.sub(r"\s+", " ", first).strip(" . *")
            if first:
                names.append(first)
    else:
        plain = htmllib.unescape(re.sub(r"<[^>]+>", "\n", text))
        for line in plain.splitlines():
            line = re.sub(r"\s+", " ", line).strip(" . *")
            if line:
                names.append(re.split(r"\s{2,}|\t|\s\(", line)[0])
    out = []
    for n in names:
        n = re.split(r",\s+an?\s+(?=[A-Z][a-z]+(?:\s[A-Z][a-z]+)?\s+(?:corporation|limited|company|partnership|general|statutory|business|trust))", n)[0].strip(" ,")
        if re.search(r"\.(?:htm|html|txt)$", n, re.I):
            continue
        if not (3 <= len(n) <= 120):
            continue
        if re.search(r"(?i)^(name|subsidiar|jurisdiction|state or|country|organized|percent|exhibit|list of|\(\d+\)|incorporat)", n):
            continue
        if not re.search(r"[A-Za-z]{3}", n):
            continue
        out.append(n)
    return list(dict.fromkeys(out))[:400]


def fetch_entity(tk, with_ex21=True):
    info = BY_TICKER[tk]
    cik = int(info["cik"])
    sub = sec_json(f"https://data.sec.gov/submissions/CIK{cik:010d}.json")
    former = [f.get("name") for f in sub.get("formerNames") or [] if f.get("name") and (f.get("to") or "")[:4] >= "2015"]
    rec = {"ticker": tk, "cik": cik, "name": sub.get("name"), "former": former, "sic": sub.get("sic"),
           "sic_description": sub.get("sicDescription"), "retrieved_at": now_iso(),
           "src": f"https://data.sec.gov/submissions/CIK{cik:010d}.json", "subsidiaries": [], "ex21": None}
    if not with_ex21:
        return rec
    recent = sub.get("filings", {}).get("recent", {})
    acc = None
    for form, a in zip(recent.get("form", []), recent.get("accessionNumber", [])):
        if form in ("10-K", "10-K/A", "20-F", "40-F"):
            acc = a
            break
    if not acc:
        return rec
    base = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}/"
    try:
        idx = sec_get(base + f"{acc}-index.htm", timeout=60)
        m = None
        for row in re.findall(r"<tr[^>]*>(.*?)</tr>", idx, re.S | re.I):
            if re.search(r">\s*EX-21(?:\.\d+)?\s*<", row):
                m = re.search(r'href="([^"]+)"', row)
                if m:
                    break
        if m:
            href = m.group(1)
            url = "https://www.sec.gov" + href if href.startswith("/") else base + href
            url = url.replace("/ix?doc=", "")
            names = _ex21_names(sec_get(url, timeout=60))
            rec.update(subsidiaries=names, ex21={"url": url, "accession": acc, "count": len(names)})
    except Exception as e:
        rec["ex21_error"] = str(e)[:160]
    return rec


def load(sb):
    return (get_raw_json(sb, PATH, {"companies": {}}) or {"companies": {}})["companies"]


def refresh(sb, tks=None, workers=6, log=print, deadline=None):
    doc = get_raw_json(sb, PATH, {"companies": {}}) or {"companies": {}}
    tks = tks or tickers()
    done = 0

    def one(tk):
        try:
            return tk, fetch_entity(tk), None
        except Exception as e:
            return tk, None, str(e)[:160]
    with ThreadPoolExecutor(workers) as ex:
        for tk, rec, err in ex.map(one, tks):
            if rec:
                doc["companies"][tk] = rec
                done += 1
            else:
                log("entity", tk, err)
            if done and done % 200 == 0:
                put_raw_json(sb, PATH, doc)
                log(f"  entities {done}")
            if deadline and deadline.passed(30):
                break
    doc["updated_at"] = now_iso()
    put_raw_json(sb, PATH, doc)
    return doc["companies"]


def legal_names(ent, tk):
    e = ent.get(tk) or {}
    return [n for n in [e.get("name")] + list(e.get("former") or []) if n]
