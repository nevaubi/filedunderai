"""Form 144 notices of proposed insider sales.

A Form 144 is filed before an affiliate (officer, director, 10% holder) sells restricted or control stock. It names
the seller, the shares and approximate market value to be sold, the expected sale date, the broker, how the shares
were acquired, sales in the past three months and, since 2023, the dates any Rule 10b5-1 plan behind the sale was
adopted. The Form 4 that reports the executed sale comes later, so open notices are a forward view of selling.

Inputs: the filings table (form 144 rows, filled by the filings jobs) and insiders/{T}.json (Form 4 rows).
Outputs (public-data):
  form144/{T}.json     notices for one company (last 365 days), each matched to the Form 4 sale that executed it
  form144/recent.json  notices across covered companies in the last 30 days, largest first
Raw cache: govraw/form144/parsed.json (parsed notices by accession, so a document is fetched once)."""
import concurrent.futures as cf
import datetime as dt
import re
import time
import xml.etree.ElementTree as ET

from .common import now_iso, sec_get
from .gov_common import get_raw_json, put_raw_json

CACHE_PATH = "govraw/form144/parsed.json"
KEEP_DAYS = 400
MATCH_DAYS = 14   # a Form 4 sale within this many days after the notice (or 3 before) executes it


def _strip_ns(root):
    for el in root.iter():
        if isinstance(el.tag, str) and "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
    return root


def _txt(node, path):
    if node is None:
        return None
    el = node.find(path)
    if el is None or el.text is None:
        return None
    return el.text.strip() or None


def _num(s):
    try:
        return float(str(s).replace(",", "")) if s not in (None, "") else None
    except ValueError:
        return None


def _date(s):
    """MM/DD/YYYY (as filed) -> YYYY-MM-DD."""
    if not s:
        return None
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", s.strip())
    if m:
        return f"{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s.strip())
    return m.group(0) if m else None


def parse(xml_text):
    """One notice from a Form 144 XML document (several securities lines are summed)."""
    root = _strip_ns(ET.fromstring(xml_text.encode() if isinstance(xml_text, str) else xml_text))
    fd = root.find("formData")
    if fd is None:
        raise ValueError("not a Form 144 document")
    iss = fd.find("issuerInfo")
    rels = [r.text.strip() for r in fd.findall("issuerInfo/relationshipsToIssuer/relationshipToIssuer") if r.text and r.text.strip()]
    lines = fd.findall("securitiesInformation")
    shares = sum(_num(_txt(l, "noOfUnitsSold")) or 0 for l in lines)
    value = sum(_num(_txt(l, "aggregateMarketValue")) or 0 for l in lines)
    outstanding = max([_num(_txt(l, "noOfUnitsOutstanding")) or 0 for l in lines] or [0]) or None
    sale_dates = sorted(d for d in (_date(_txt(l, "approxSaleDate")) for l in lines) if d)
    brokers = sorted({b for b in (_txt(l, "brokerOrMarketmakerDetails/name") for l in lines) if b})
    exchanges = sorted({e for e in (_txt(l, "securitiesExchangeName") for l in lines) if e})
    acquired = []
    for a in fd.findall("securitiesToBeSold"):
        acquired.append({"date": _date(_txt(a, "acquiredDate")), "how": _txt(a, "natureOfAcquisitionTransaction"),
                         "shares": _num(_txt(a, "amountOfSecuritiesAcquired")), "gift": _txt(a, "isGiftTransaction") == "Y"})
    past = fd.findall("securitiesSoldInPast3Months")
    plan_dates = sorted({d for d in (_date(p.text) for p in fd.findall("noticeSignature/planAdoptionDates/planAdoptionDate")) if d})
    return {
        "issuer_cik": (_txt(iss, "issuerCik") or "").lstrip("0") or None,
        "issuer": _txt(iss, "issuerName"),
        "person": _txt(iss, "nameOfPersonForWhoseAccountTheSecuritiesAreToBeSold"),
        "relationship": ", ".join(rels) or None,
        "shares": shares or None,
        "value": round(value, 2) if value else None,
        "pct_outstanding": round(shares / outstanding * 100, 4) if shares and outstanding else None,
        "sale_date": sale_dates[0] if sale_dates else None,
        "brokers": brokers,
        "exchanges": exchanges,
        "acquired": acquired[:6],
        "how": "; ".join(sorted({a["how"] for a in acquired if a.get("how")}))[:160] or None,
        "past3m_shares": sum(_num(_txt(p, "amountOfSecuritiesSold")) or 0 for p in past) or None,
        "past3m_proceeds": round(sum(_num(_txt(p, "grossProceeds")) or 0 for p in past), 2) or None,
        "notice_date": _date(_txt(fd, "noticeSignature/noticeDate")),
        "plan_adopted": plan_dates,
        "plan": bool(plan_dates),
    }


def doc_url(row):
    """Raw XML URL (the filings table stores the XSL-rendered path)."""
    return re.sub(r"/xsl144X\d+/", "/", row.get("url") or "")


# ---------------------------------------------------------------- matching to Form 4

def _tokens(name):
    return {w for w in re.sub(r"[^a-z ]", " ", (name or "").lower()).split() if len(w) > 1 and w not in {"jr", "sr", "ii", "iii", "iv", "mr", "ms", "dr"}}


def same_person(a, b):
    """Form 144 writes 'First Last'; Form 4 writes 'Last First M'. Same person when the shorter token set is contained."""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return False
    short, long_ = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    return len(short) >= 2 and short <= long_ or (len(short) == 1 and len(long_) == 1 and short == long_)


def match_form4(notice, rows):
    """Form 4 open-market sales (code S) by the same person between 3 days before and MATCH_DAYS after the notice."""
    if not notice.get("filed"):
        return None
    d0 = dt.date.fromisoformat(notice["filed"])
    lo, hi = (d0 - dt.timedelta(days=3)).isoformat(), (d0 + dt.timedelta(days=MATCH_DAYS)).isoformat()
    sells = [r for r in rows if r.get("code") == "S" and not r.get("derivative") and lo <= (r.get("tx_date") or "") <= hi
             and same_person(notice.get("person"), r.get("owner"))]
    if not sells:
        return None
    shares = sum(r.get("shares") or 0 for r in sells)
    value = sum((r.get("shares") or 0) * (r.get("price") or 0) for r in sells)
    return {"shares": shares, "value": round(value, 2), "first": min(r["tx_date"] for r in sells), "last": max(r["tx_date"] for r in sells),
            "accessions": sorted({r["accession"] for r in sells})[:6], "plan_10b5": any(r.get("plan_10b5") for r in sells),
            "filled_pct": round(shares / notice["shares"] * 100, 1) if notice.get("shares") else None}


def summarize(notices, today):
    def window(days):
        since = (today - dt.timedelta(days=days)).isoformat()
        w = [n for n in notices if (n.get("filed") or "") >= since]
        return {"n": len(w), "value": round(sum(n.get("value") or 0 for n in w)), "people": len({n.get("person") for n in w}),
                "plan_share": round(sum(1 for n in w if n.get("plan")) / len(w), 2) if w else None}
    open_ = [n for n in notices if n.get("status") == "open"]
    return {"d30": window(30), "d90": window(90), "d365": window(365),
            "open": {"n": len(open_), "value": round(sum(n.get("value") or 0 for n in open_)),
                     "people": sorted({n.get("person") for n in open_ if n.get("person")})[:12]}}


def status_of(notice, executed, today):
    if executed:
        return "executed"
    if notice.get("filed") and (today - dt.date.fromisoformat(notice["filed"])).days <= MATCH_DAYS + 2:
        return "open"
    return "not_reported"   # no matching Form 4 sale yet: sold later, sold under another name, or not sold


# ---------------------------------------------------------------- job

def form144_job(sb, days=365, workers=6, time_budget=600, tickers=None, log=print):
    """Parses new Form 144 filings, rebuilds per-company files for tickers with notices, publishes recent.json."""
    t0 = time.time()
    today = dt.date.today()
    since = (today - dt.timedelta(days=days)).isoformat()
    q = f"select=accession,ticker,filed,url,primary_doc&form=eq.144&filed=gte.{since}&order=filed.desc"
    rows = sb.select("filings", q)
    if tickers:
        want = set(tickers)
        rows = [r for r in rows if r.get("ticker") in want]
    cache = get_raw_json(sb, CACHE_PATH, {}) or {}
    todo = [r for r in rows if r["accession"] not in cache]
    log(f"form144: {len(rows)} notices since {since}, {len(todo)} to parse")

    def one(r):
        if time.time() - t0 > time_budget:
            return r, None, "budget"
        try:
            return r, parse(sec_get(doc_url(r))), None
        except Exception as e:
            return r, None, str(e)[:160]
    parsed = errors = 0
    with cf.ThreadPoolExecutor(workers) as ex:
        for r, rec, err in ex.map(one, todo):
            if rec:
                rec.update(ticker=r["ticker"], filed=r["filed"], accession=r["accession"], url=r.get("url"))
                cache[r["accession"]] = rec
                parsed += 1
                if parsed % 300 == 0:
                    put_raw_json(sb, CACHE_PATH, cache)
            elif err and err != "budget":
                cache[r["accession"]] = {"ticker": r["ticker"], "filed": r["filed"], "accession": r["accession"], "error": err}
                errors += 1
    keep = (today - dt.timedelta(days=KEEP_DAYS)).isoformat()
    cache = {k: v for k, v in cache.items() if (v.get("filed") or "") >= keep}
    put_raw_json(sb, CACHE_PATH, cache)

    by_t = {}
    for v in cache.values():
        if v.get("error") or not v.get("ticker"):
            continue
        by_t.setdefault(v["ticker"], []).append(v)
    recent_since = (today - dt.timedelta(days=30)).isoformat()

    def build(item):
        t, notices = item
        try:
            f4 = (sb.get_json(f"insiders/{t}.json") or {}).get("rows") or []
        except Exception:
            f4 = []
        out, rec = [], []
        for n in sorted(notices, key=lambda x: (x.get("filed") or "", x.get("accession") or ""), reverse=True):
            if (n.get("filed") or "") < since:
                continue
            ex_ = match_form4(n, f4)
            n2 = {k: n.get(k) for k in ("accession", "filed", "url", "person", "relationship", "shares", "value", "pct_outstanding",
                                         "sale_date", "brokers", "how", "past3m_shares", "past3m_proceeds", "plan", "plan_adopted", "acquired")}
            n2["executed"] = ex_
            n2["status"] = status_of(n, ex_, today)
            out.append(n2)
            if n2["filed"] >= recent_since:
                rec.append({"ticker": t, **{k: n2[k] for k in ("accession", "filed", "url", "person", "relationship", "shares", "value",
                                                                  "pct_outstanding", "sale_date", "plan", "plan_adopted", "status")}})
        if out:
            sb.put_json(f"form144/{t}.json", {"ticker": t, "generated": now_iso(), "summary": summarize(out, today), "notices": out[:200]},
                        cache="max-age=900")
        return bool(out), rec
    recent, written = [], 0
    with cf.ThreadPoolExecutor(12) as ex:
        for ok, rec in ex.map(build, sorted(by_t.items())):
            written += ok
            recent += rec
    recent.sort(key=lambda r: -(r.get("value") or 0))
    sb.put_json("form144/recent.json", {"generated": now_iso(), "since": recent_since, "n": len(recent),
                                         "value": round(sum(r.get("value") or 0 for r in recent)), "rows": recent[:600]},
                cache="max-age=900")
    return {"notices": len(rows), "parsed": parsed, "errors": errors, "companies": written, "recent": len(recent),
            "seconds": round(time.time() - t0)}
