"""Source documents for the supply-chain map: which filings to read per company, fetching them from SEC EDGAR
(SEC_USER_AGENT, shared 10 req/s throttle), plain-text conversion, and a raw-bucket copy under supplyraw/."""
import gzip
import json
import os
import re

from .common import HTTPError, request, sec_get, sec_json
from .supply_core import html_to_text

ARCHIVE = "https://www.sec.gov/Archives/edgar/data"
CACHE_DIR = os.environ.get("SUPPLY_CACHE")  # optional local cache for one-off backfills


def doc_url(cik, accn, doc):
    return f"{ARCHIVE}/{int(cik)}/{accn.replace('-', '')}/{doc}"


def filing_rows(sub):
    """Submissions feed (recent + older pages) as dicts with form, filed, accn, doc, items, report."""
    def rows(block):
        n = len(block.get("accessionNumber", []))
        for i in range(n):
            yield {"form": block["form"][i], "filed": block["filingDate"][i], "accn": block["accessionNumber"][i],
                   "doc": block["primaryDocument"][i], "items": block.get("items", [""] * n)[i] or "",
                   "report": block.get("reportDate", [""] * n)[i] or ""}
    out = list(rows(sub.get("filings", {}).get("recent", {})))
    return out


def older_rows(sub):
    out = []
    for f in sub.get("filings", {}).get("files", [])[:2]:
        try:
            block = sec_json("https://data.sec.gov/submissions/" + f["name"])
        except Exception:
            continue
        n = len(block.get("accessionNumber", []))
        for i in range(n):
            out.append({"form": block["form"][i], "filed": block["filingDate"][i], "accn": block["accessionNumber"][i],
                        "doc": block["primaryDocument"][i], "items": block.get("items", [""] * n)[i] or "",
                        "report": block.get("reportDate", [""] * n)[i] or ""})
    return out


def pick_filings(rows, prior_years=2):
    """Current set = latest 10-K + any newer 10-Q; history = the prior 10-Ks (for what changed)."""
    ks = sorted([r for r in rows if r["form"] in ("10-K", "10-K405") and r["doc"]], key=lambda r: r["filed"], reverse=True)
    if not ks:
        # new registrants (e.g. a holding-company reorganisation) may only have 10-Qs under this CIK so far
        qs = sorted([r for r in rows if r["form"] == "10-Q" and r["doc"]], key=lambda r: r["filed"], reverse=True)
        return [dict(qs[0], role="current")] if qs else []
    qs = sorted([r for r in rows if r["form"] == "10-Q" and r["filed"] > ks[0]["filed"] and r["doc"]], key=lambda r: r["filed"], reverse=True)
    out = [dict(ks[0], role="current")]
    if qs:
        out.append(dict(qs[0], role="current"))
    out += [dict(k, role="history") for k in ks[1:1 + prior_years]]
    return out


def agreement_filings(rows, since, limit=6):
    """8-K filings with Item 1.01 (entry into a material definitive agreement) since `since`, newest first."""
    return sorted([r for r in rows if r["form"] in ("8-K", "8-K/A") and "1.01" in (r["items"] or "") and r["filed"] >= since],
                  key=lambda r: r["filed"], reverse=True)[:limit]


def _cache_path(cik, accn, doc):
    if not CACHE_DIR:
        return None
    os.makedirs(CACHE_DIR, exist_ok=True)
    return os.path.join(CACHE_DIR, f"{int(cik)}_{accn}_{re.sub(r'[^A-Za-z0-9._-]', '_', doc)}.txt.gz")


def fetch_text(cik, accn, doc, sb=None):
    """Plain text of one filed document, with a local cache (backfills) and a raw-bucket archive copy (supplyraw/)."""
    cp = _cache_path(cik, accn, doc)
    if cp and os.path.exists(cp):
        return gzip.decompress(open(cp, "rb").read()).decode()
    html = sec_get(doc_url(cik, accn, doc), timeout=120)
    text = html_to_text(html)
    if cp:
        open(cp, "wb").write(gzip.compress(text.encode()))
    if sb is not None:
        try:
            sb.put_object("raw", f"supplyraw/docs/{accn}/{doc}.txt.gz", gzip.compress(text.encode()), "application/gzip", cache="no-store")
        except Exception as e:  # archive copy is best effort; the public text copy is what the page reads
            print("supplyraw archive failed", accn, doc, str(e)[:120])
    return text


def filing_index(cik, accn):
    try:
        j = sec_json(f"{ARCHIVE}/{int(cik)}/{accn.replace('-', '')}/index.json")
    except HTTPError:
        return []
    return [i["name"] for i in j.get("directory", {}).get("item", [])]


BORING = re.compile(r"credit agreement|indenture|employment|compensation|severance|stock (option|unit|award)|incentive|retention|offer letter|"
                    r"indemnif|note purchase|underwriting|separation|consulting agreement|deferred|bonus|change in control|aircraft time sharing|"
                    r"restricted|term loan|revolving|underwriters|purchase agreement relating to (the )?notes|guarantee|supplemental indenture", re.I)
GOOD = re.compile(r"supply|purchase|manufactur|foundry|wafer|license|collaborat|master services|services agreement|distribution|development agreement|"
                  r"capacity|offtake|take-or-pay|joint venture|alliance|merger|acquisition|strategic", re.I)


def exhibit_docs(cik, accn, names):
    """EX-10 material-contract exhibits in a filing folder (names from filing_index)."""
    return [n for n in names if re.search(r"ex-?10", n, re.I) and re.search(r"\.(htm|html|txt)$", n, re.I)]


_SEC_TITLES = None


def sec_titles():
    """{ticker: registrant title} from SEC's company tickers file, used only to confirm a proposed counterparty ticker."""
    global _SEC_TITLES
    if _SEC_TITLES is None:
        try:
            j = sec_json("https://www.sec.gov/files/company_tickers.json")
            _SEC_TITLES = {v["ticker"].upper().replace(".", "-"): v["title"] for v in j.values()}
        except Exception as e:
            print("company_tickers unavailable", str(e)[:120])
            _SEC_TITLES = {}
    return _SEC_TITLES


def public_text_copy(sb, accn, doc, meta, text, quotes, context=3):
    """Public copy of the paragraphs that carry cited quotes (plus `context` paragraphs either side) for the source panel.
    Main filings also open in the full EDGAR viewer through /api/filing; exhibits and 8-K items rely on this copy."""
    from .supply_core import norm
    paras = text.split("\n")
    nq = [norm(q) for q in quotes if q]
    keep = set()
    for i, p in enumerate(paras):
        np_ = norm(p)
        if any(q and q in np_ for q in nq):
            keep.update(range(max(0, i - context), min(len(paras), i + context + 1)))
    if not keep and nq:
        # quote spans a paragraph break: fall back to whole-text search windows
        joined = norm(text)
        for q in nq:
            if q in joined:
                keep.update(range(len(paras)))
                break
    blocks, last = [], -2
    for i in sorted(keep):
        if i != last + 1 and blocks:
            blocks.append(None)  # gap marker
        blocks.append(paras[i])
        last = i
    body = {"accn": accn, "doc": doc, **meta, "partial": len(keep) < len(paras), "paras": blocks}
    path = f"supply/docs/{accn}/{doc}.json.gz"
    sb.put_object("public-data", path, gzip.compress(json.dumps(body, separators=(",", ":")).encode()), "application/gzip", cache="max-age=86400")
    return path
