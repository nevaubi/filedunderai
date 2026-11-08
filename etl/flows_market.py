"""Off-exchange and short activity from FINRA's public Query API (api.finra.org/data/group/otcMarket, no key).

- weeklySummary, summaryTypeCode ATS_W_SMBL / OTC_W_SMBL: weekly shares and trades executed on alternative trading
  systems ("dark pools") and other off-exchange (non-ATS OTC) venues, per symbol. Tier 1 NMS stocks are published
  two weeks after the week, Tier 2 four weeks after.
- weeklySummary, ATS_W_SMBL_FIRM: the same per ATS, used for each company's largest venues in the latest weeks.
- consolidatedShortInterest: short positions per settlement date (twice a month), days to cover, change.

The off-exchange share of volume divides FINRA's weekly ATS + non-ATS shares by the consolidated weekly volume summed
from our daily bars (prices/{T}.json, Yahoo-sourced consolidated volume). Weeks with fewer than four daily bars or a
FINRA total above the consolidated volume are left without a share. The FINRA Reg SHO daily short-sale volume series
is already published by the Alt data pipeline (altdata/{T}.json) and is reused, not refetched.
"""
import datetime as dt
import json
import time

from .common import now_iso, request
from .gov_common import UA_GOV, polite, src, symbol_map, tickers

API = "https://api.finra.org/data/group/otcMarket/name/"
WEEKLY_PAGE = "https://otctransparency.finra.org/otctransparency/AtsIssueData"
SI_PAGE = "https://www.finra.org/finra-data/browse-catalog/equity-short-interest/data"


def _q(dataset, body, page_limit=5000, max_pages=8):
    out, offset = [], 0
    for _ in range(max_pages):
        polite("api.finra.org", 0.3)
        b = dict(body, limit=page_limit, offset=offset)
        rows = json.loads(request(API + dataset, method="POST", data=b, headers={**UA_GOV, "Accept": "application/json",
                                                                                 "Content-Type": "application/json"}, timeout=120, retries=4) or "[]")
        out += rows
        if len(rows) < page_limit:
            break
        offset += page_limit
    return out


def finra_symbols():
    """FINRA symbols use '.' for share classes (BRK.B)."""
    m = {}
    for tk in tickers():
        m[tk.replace("-", ".")] = tk
    return m


def chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def weekly(symbols, start, end, kind="ATS_W_SMBL", fields=None):
    rows = []
    for ch in chunks(symbols, 250):
        rows += _q("weeklySummary", {"compareFilters": [{"compareType": "EQUAL", "fieldName": "summaryTypeCode", "fieldValue": kind}],
                                     "dateRangeFilters": [{"fieldName": "weekStartDate", "startDate": start, "endDate": end}],
                                     "domainFilters": [{"fieldName": "issueSymbolIdentifier", "values": ch}],
                                     **({"fields": fields} if fields else {})})
    return rows


def short_interest(symbols, start, end):
    rows = []
    for ch in chunks(symbols, 250):
        rows += _q("consolidatedShortInterest", {"dateRangeFilters": [{"fieldName": "settlementDate", "startDate": start, "endDate": end}],
                                                 "domainFilters": [{"fieldName": "symbolCode", "values": ch}],
                                                 "fields": ["symbolCode", "settlementDate", "currentShortPositionQuantity", "previousShortPositionQuantity",
                                                            "averageDailyVolumeQuantity", "daysToCoverQuantity", "changePercent", "issueName",
                                                            "revisionFlag", "stockSplitFlag"]})
    return rows


def collect(today=None, weeks=26, si_months=12, log=print):
    """Pulls the FINRA datasets for the whole universe. Returns {tk: {...}} plus fetch metadata."""
    today = today or dt.date.today()
    fsyms = finra_symbols()
    syms = sorted(fsyms)
    start = (today - dt.timedelta(weeks=weeks)).isoformat()
    retrieved = now_iso()
    per = {}
    t0 = time.time()
    for kind, key in (("ATS_W_SMBL", "ats"), ("OTC_W_SMBL", "otc")):
        for r in weekly(syms, start, today.isoformat(), kind):
            tk = fsyms.get(r.get("issueSymbolIdentifier"))
            if not tk:
                continue
            w = per.setdefault(tk, {}).setdefault("weeks", {}).setdefault(r["weekStartDate"], {"week": r["weekStartDate"], "tier": r.get("tierDescription")})
            w[key + "_shares"] = r.get("totalWeeklyShareQuantity")
            w[key + "_trades"] = r.get("totalWeeklyTradeCount")
            w["published"] = r.get("initialPublishedDate")
    log(f"finra weekly done in {time.time() - t0:.0f}s, {len(per)} symbols")
    latest_weeks = sorted({w for v in per.values() for w in v.get("weeks", {})})[-2:]
    if latest_weeks:
        for r in weekly(syms, latest_weeks[0], latest_weeks[-1], "ATS_W_SMBL_FIRM",
                        fields=["issueSymbolIdentifier", "weekStartDate", "MPID", "marketParticipantName", "totalWeeklyShareQuantity", "totalWeeklyTradeCount"]):
            tk = fsyms.get(r.get("issueSymbolIdentifier"))
            if tk:
                per.setdefault(tk, {}).setdefault("venues", []).append({"week": r["weekStartDate"], "mpid": r.get("MPID"),
                                                                       "name": r.get("marketParticipantName"), "shares": r.get("totalWeeklyShareQuantity"),
                                                                       "trades": r.get("totalWeeklyTradeCount")})
    si_start = (today - dt.timedelta(days=int(si_months * 30.5))).isoformat()
    for r in short_interest(syms, si_start, today.isoformat()):
        tk = fsyms.get(r.get("symbolCode"))
        if tk:
            per.setdefault(tk, {}).setdefault("si", []).append({
                "date": r["settlementDate"], "short": r.get("currentShortPositionQuantity"), "prev": r.get("previousShortPositionQuantity"),
                "adv": r.get("averageDailyVolumeQuantity"), "dtc": r.get("daysToCoverQuantity"), "chg_pct": r.get("changePercent"),
                "revised": r.get("revisionFlag"), "split": r.get("stockSplitFlag")})
    log(f"finra all done in {time.time() - t0:.0f}s")
    return per, {"retrieved_at": retrieved, "weeks_from": start, "latest_weeks": latest_weeks}


def si_summary(si):
    """Latest settlement and the trend over the last six, from a company's sorted short_interest rows."""
    si = [r for r in si if r.get("short") is not None]
    if not si:
        return None
    last = si[-1]
    hist = si[-6:]
    prev = last.get("prev")
    base = hist[0].get("short") if len(hist) > 1 else None
    return {"date": last["date"], "short": last["short"], "prev": prev, "dtc": last.get("dtc"), "adv": last.get("adv"),
            "chg": (last["short"] / prev - 1) if prev else None,
            "chg_3m": (last["short"] / base - 1) if base else None,
            "trend": [[r["date"], r["short"]] for r in hist]}


def weekly_volume(bars):
    """{monday iso: [volume, n_days]} from daily bars [[t, o, h, l, c, v], ...] (t = epoch seconds)."""
    out = {}
    for b in bars or []:
        try:
            d = dt.datetime.fromtimestamp(int(b[0]), dt.timezone.utc).date()
            v = float(b[5] or 0)
        except (TypeError, ValueError, IndexError):
            continue
        mon = (d - dt.timedelta(days=d.weekday())).isoformat()
        c = out.setdefault(mon, [0.0, 0])
        c[0] += v
        c[1] += 1
    return out


def company_flows(tk, raw, meta, bars):
    retrieved = meta["retrieved_at"]
    vols = weekly_volume(bars)
    weeks = []
    for w in sorted((raw.get("weeks") or {}).values(), key=lambda x: x["week"]):
        ats, otc = w.get("ats_shares"), w.get("otc_shares")
        off = (ats or 0) + (otc or 0) if (ats is not None or otc is not None) else None
        vol, n = vols.get(w["week"], [None, 0])
        share = None
        note = None
        if off is not None and vol and n >= 4:
            if off <= vol:
                share = off / vol
            else:
                note = "FINRA off-exchange volume exceeds the consolidated volume in our bars; share not computed"
        elif off is not None:
            note = "incomplete week in daily bars" if vol else "no daily bars for this week"
        weeks.append({**w, "offex_shares": off, "consolidated_volume": vol, "bar_days": n, "offex_share": share,
                      "ats_share": (ats / vol) if (ats is not None and vol and n >= 4 and off is not None and off <= vol) else None, "note": note})
    venues = sorted(raw.get("venues") or [], key=lambda v: (v["week"], v.get("shares") or 0), reverse=True)
    latest = venues[0]["week"] if venues else None
    top = [v for v in venues if v["week"] == latest][:12]
    tot = sum(v.get("shares") or 0 for v in top) or None
    for v in top:
        v["pct_of_ats"] = (v["shares"] / tot) if tot and v.get("shares") is not None else None
    si = sorted(raw.get("si") or [], key=lambda x: x["date"])
    return {
        "ats_weekly": weeks, "ats_venues": {"week": latest, "rows": top},
        "short_interest": si,
        "src_weekly": src(API + "weeklySummary", "FINRA Query API weeklySummary (ATS_W_SMBL, OTC_W_SMBL)", retrieved, page=WEEKLY_PAGE),
        "src_venues": src(API + "weeklySummary", "FINRA Query API weeklySummary (ATS_W_SMBL_FIRM)", retrieved, page=WEEKLY_PAGE),
        "src_short_interest": src(API + "consolidatedShortInterest", "FINRA Query API consolidatedShortInterest", retrieved, page=SI_PAGE),
        "src_volume": src(f"prices/{tk}.json", "Consolidated daily volume from Filed Under daily bars (prices/ bundle)", None),
    }
