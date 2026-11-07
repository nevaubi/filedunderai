"""Estimates, analysts, revisions and guidance per company, published to estimates/{T}.json.

Sources, each kept separate and labeled in the bundle:
- Consensus EPS/revenue by period (0q, +1q, 0y, +1y), analyst counts, low/high, next earnings date and the
  last four quarters of EPS actual vs estimate: Yahoo Finance via Firecrawl Alexandria
  (finance-yahoo-com/equities/earnings). Third-party data, not from filings.
- Analyst actions (rating changes, price target changes, initiations) with analyst and firm names, and the
  analyst roster with Benzinga's accuracy statistics: Benzinga via Firecrawl Alexandria (calendar/ratings,
  calendar/ratings-analysts, calendar/ratings-firms, calendar/removed). Attribution "Powered by Benzinga Data".
- Reported actuals: our own SEC bundle (companies/{T}.json): diluted EPS (GAAP) and revenue by quarter.
- Guidance: the existing guidance/{T}.json bundle (verbatim quotes from IR press releases with document links,
  guidance outcomes and the credibility track record). Read only; this module never rewrites it.

Revision history accumulates from our own snapshots: each consensus refresh appends one dated snapshot per
period, and 7/30/60/90-day revisions are computed only when a snapshot of the same fiscal period exists that far
back. The Alexandria Yahoo contract has no provider-side estimate-trend fields, so nothing is backfilled for them.

Raw provider responses are stored gzipped in the private raw bucket under estraw/ for audit.
"""
import datetime as dt
import gzip
import json
import math
import re
import statistics

from .common import now_iso

VERSION = 1
BENZINGA_ATTRIBUTION = "Powered by Benzinga Data"
YAHOO_LABEL = "Yahoo Finance"
PERIODS = ("0q", "+1q", "0y", "+1y")
PERIOD_LABEL = {"0q": "Current quarter", "+1q": "Next quarter", "0y": "Current fiscal year", "+1y": "Next fiscal year"}
SNAPSHOT_KEEP_DAYS = 800
REVISION_WINDOWS = (7, 30, 60, 90)
ACTION_WINDOW_DAYS = 365          # latest rating / target per firm counts toward distributions within this window
INACTIVE_ACTIONS = {"Terminates Coverage On", "Suspends", "Removes", "Firm Dissolved"}

BUY = {"buy", "strong buy", "outperform", "overweight", "positive", "accumulate", "sector outperform", "market outperform",
       "top pick", "conviction buy", "add", "speculative buy", "long-term buy", "outperformer", "moderate buy", "buy-rated",
       "trading buy", "above average", "focus list", "tactical buy"}
HOLD = {"hold", "neutral", "equal-weight", "equal weight", "market perform", "sector perform", "in-line", "inline", "peer perform",
        "sector weight", "mixed", "fair value", "perform", "market weight", "average", "sector neutral", "hold neutral", "cautious",
        "moderate hold", "not rated"}
SELL = {"sell", "strong sell", "underperform", "underweight", "reduce", "negative", "sector underperform", "market underperform",
        "underperformer", "moderate sell", "below average"}


# ---------- small helpers ----------

def num(v):
    """Float from a provider value ('380.0000', 2.9, '') or None. Never coerces missing to zero."""
    if v is None or v == "" or isinstance(v, bool):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def pdate(s):
    return dt.date.fromisoformat(str(s)[:10])


def add_months_end(iso, months):
    """Last day of the month `months` after iso's month."""
    d = pdate(iso)
    y, m = divmod(d.month - 1 + months, 12)
    y, m = d.year + y, m + 1
    nxt = dt.date(y + (m == 12), m % 12 + 1, 1)
    return (nxt - dt.timedelta(days=1)).isoformat()


def days_between(a, b):
    return (pdate(b) - pdate(a)).days


def norm_ticker(t):
    return str(t or "").upper().replace(".", "-").replace("/", "-")


def gz(obj):
    return gzip.compress(json.dumps(obj, separators=(",", ":"), default=str).encode())


def median(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def rating_bucket(r):
    k = re.sub(r"\s+", " ", str(r or "").strip().lower())
    if not k:
        return None
    if k in BUY:
        return "buy"
    if k in HOLD:
        return "hold"
    if k in SELL:
        return "sell"
    return "other"


def action_kind(a):
    ac = a.get("action") or ""
    if ac == "Upgrades":
        return "upgrade"
    if ac == "Downgrades":
        return "downgrade"
    if ac in ("Initiates Coverage On", "Reinstates", "Assumes"):
        return "initiate"
    if ac in INACTIVE_ACTIONS:
        return "drop"
    pc, pp = a.get("pt_current"), a.get("pt_prior")
    if pc is not None and pp is not None and pc != pp:
        return "target"
    return "reiterate"


# ---------- Yahoo consensus ----------

def _range(e):
    e = e or {}
    return {"avg": num(e.get("average")), "low": num(e.get("low")), "high": num(e.get("high")),
            "n": num(e.get("number_of_analysts")), "year_ago": num(e.get("year_ago"))}


def fix_period_ends(periods, last_reported):
    """Yahoo sometimes keeps a reported quarter's end on 0q and repeats the fiscal-year end on +1y.
    Re-anchor so 0q follows the latest reported quarter and each later period ends after the one before.
    Every corrected end is flagged (end_fixed) and keeps the provider's value (end_provider)."""
    by = {p["code"]: p for p in periods}

    def setend(p, value, why):
        p["end_provider"] = p.get("end")
        p["end"] = value
        p["end_fixed"] = why

    q0, q1, y0, y1 = by.get("0q"), by.get("+1q"), by.get("0y"), by.get("+1y")
    if q0 and q0.get("end") and last_reported and q0["end"][:7] <= last_reported[:7]:
        setend(q0, add_months_end(last_reported, 3), "provider end was an already reported quarter")
    if q0 and q1 and q0.get("end") and (not q1.get("end") or q1["end"] <= q0["end"]):
        setend(q1, add_months_end(q0["end"], 3), "provider end was not after the current quarter")
    if y0 and y1 and y0.get("end") and (not y1.get("end") or y1["end"] <= y0["end"]):
        setend(y1, add_months_end(y0["end"], 12), "provider end repeated the current fiscal year")
    return periods


MIN_SURPRISE_BASE = 0.05   # |consensus EPS| below this makes a percentage surprise meaningless
MAX_SURPRISE = 10.0        # beyond ±1,000% the percentage carries no information the EPS difference does not


def sane_surprise(actual, estimate, given=None):
    """Percentage EPS surprise, or None when it is not meaningful: a consensus near zero (LLYVA: -3.20 against
    0.01 reads as -32,100%), or a result beyond ±1,000%. The denominator is |estimate| so a miss on a negative
    estimate stays negative. Without both EPS values the provider's figure is kept within the same bounds."""
    if actual is not None and estimate is not None:
        if abs(estimate) < MIN_SURPRISE_BASE:
            return None
        s = (actual - estimate) / abs(estimate)
    else:
        s = given
    if s is None or not isinstance(s, (int, float)) or s != s or abs(s) > MAX_SURPRISE:
        return None
    return round(s, 6)


def parse_yahoo_earnings(data, retrieved_at=None):
    """Normalize one equities/earnings payload. Returns None when it carries no usable estimates."""
    if not isinstance(data, dict):
        return None
    hist = []
    for h in data.get("history") or []:
        q = (h.get("quarter") or {}).get("date")
        if not q:
            continue
        actual, estimate = num(h.get("eps_actual")), num(h.get("eps_estimate"))
        hist.append({"quarter_end": q, "eps_actual": actual, "eps_estimate": estimate,
                     "eps_difference": num(h.get("eps_difference")),
                     "surprise_pct": sane_surprise(actual, estimate, num(h.get("surprise_percent"))),
                     "currency": h.get("currency") or "USD"})
    # Yahoo repeats period labels (two "-4q" rows); quarters are identified by their date only.
    seen, history = set(), []
    for h in sorted(hist, key=lambda x: x["quarter_end"]):
        if h["quarter_end"] not in seen:
            seen.add(h["quarter_end"])
            history.append(h)
    periods = []
    for t in data.get("trend") or []:
        if t.get("period") not in PERIODS:
            continue
        periods.append({"code": t["period"], "label": PERIOD_LABEL[t["period"]], "end": t.get("end_date"),
                        "eps": _range(t.get("eps_estimate")), "revenue": _range(t.get("revenue_estimate")),
                        "growth": num(t.get("growth"))})
    periods.sort(key=lambda p: PERIODS.index(p["code"]))
    last = max((h["quarter_end"] for h in history if h["eps_actual"] is not None), default=None)
    fix_period_ends(periods, last)
    for p in periods:  # Yahoo reports 0 analysts as an all-zero range: that is missing, not a zero estimate
        for k in ("eps", "revenue"):
            r = p[k]
            if not r["n"]:
                p[k] = {**r, "avg": None, "low": None, "high": None, "n": r["n"] or None}
    ne = data.get("next_earnings") or {}
    nxt = None
    if ne.get("date"):
        nxt = {"date": ne["date"], "is_estimate": bool(ne.get("is_estimate")), "window_end": ne.get("window_end_date"),
               "call_date": (ne.get("earnings_call") or {}).get("date"),
               "eps": {k: num((ne.get("eps_estimate") or {}).get(k2)) for k, k2 in (("avg", "average"), ("low", "low"), ("high", "high"))},
               "revenue": {k: num((ne.get("revenue_estimate") or {}).get(k2)) for k, k2 in (("avg", "average"), ("low", "low"), ("high", "high"))}}
    obs = data.get("observed_at_ms")
    as_of = dt.datetime.fromtimestamp(obs / 1000, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if isinstance(obs, (int, float)) else (retrieved_at or now_iso())
    if not periods and not history:
        return None
    return {"provider": YAHOO_LABEL, "symbol": data.get("symbol"), "name": data.get("name"), "url": data.get("source_url"),
            "as_of": as_of, "periods": periods, "history": history, "next_earnings": nxt}


def snapshot_of(consensus):
    """Compact dated snapshot of the consensus by period, appended on each refresh."""
    return {"as_of": consensus["as_of"][:10], "at": consensus["as_of"],
            "p": {p["code"]: {"end": p["end"], "eps": p["eps"]["avg"], "eps_n": p["eps"]["n"], "eps_lo": p["eps"]["low"], "eps_hi": p["eps"]["high"],
                              "rev": p["revenue"]["avg"], "rev_n": p["revenue"]["n"]} for p in consensus["periods"]}}


def merge_snapshots(old, new, today=None):
    """One snapshot per day (the latest wins), oldest first, trimmed to SNAPSHOT_KEEP_DAYS."""
    by = {s["as_of"]: s for s in (old or [])}
    if new:
        by[new["as_of"]] = new
    out = sorted(by.values(), key=lambda s: s["as_of"])
    if out:
        cut = (pdate(today or out[-1]["as_of"]) - dt.timedelta(days=SNAPSHOT_KEEP_DAYS)).isoformat()
        out = [s for s in out if s["as_of"] >= cut]
    return out


def revisions(snapshots):
    """Change in consensus EPS and revenue for each period vs the snapshot N days earlier, same fiscal period only.
    Returns per period {window: {eps_from, eps_to, eps_pct, rev_from, rev_to, rev_pct, from_date}} or None per window."""
    if not snapshots:
        return {}
    cur = snapshots[-1]
    out = {}
    for code, now in cur["p"].items():
        row = {}
        for w in REVISION_WINDOWS:
            target = (pdate(cur["as_of"]) - dt.timedelta(days=w)).isoformat()
            # the latest snapshot at or before the target date, no more than w/2 (min 3) days older than it
            cands = [s for s in snapshots if s["as_of"] <= target and code in s["p"] and s["p"][code].get("end") == now.get("end")]
            base = cands[-1] if cands else None
            if not base or days_between(base["as_of"], target) > max(3, w // 2):
                row[str(w)] = None
                continue
            b = base["p"][code]
            row[str(w)] = {"from_date": base["as_of"], "eps_from": b.get("eps"), "eps_to": now.get("eps"), "eps_pct": pct_change(now.get("eps"), b.get("eps")),
                           "rev_from": b.get("rev"), "rev_to": now.get("rev"), "rev_pct": pct_change(now.get("rev"), b.get("rev"))}
        out[code] = row
    return out


def pct_change(now, then):
    """Sign-safe relative change (works for negative EPS); None when either side is missing or zero."""
    if now is None or then is None or then == 0:
        return None
    return (now - then) / abs(then)


# ---------- Benzinga actions ----------

def parse_rating(r):
    """One calendar/ratings record -> action dict. Price targets stay as printed (strings) plus parsed numbers."""
    return {"id": r.get("id"), "date": r.get("date"), "time": r.get("time"), "ticker": norm_ticker(r.get("ticker")),
            "firm": (r.get("analyst") or "").strip(), "firm_id": r.get("firm_id"), "analyst": (r.get("analyst_name") or "").strip() or None,
            "analyst_id": r.get("analyst_id") or None, "action": r.get("action_company") or "", "action_pt": r.get("action_pt") or "",
            "rating_prior": r.get("rating_prior") or None, "rating_current": r.get("rating_current") or None,
            "pt_prior": num(r.get("pt_prior")), "pt_current": num(r.get("pt_current")),
            "pt_prior_printed": r.get("pt_prior") or None, "pt_current_printed": r.get("pt_current") or None,
            "adj_pt_prior": num(r.get("adjusted_pt_prior")), "adj_pt_current": num(r.get("adjusted_pt_current")),
            "currency": r.get("currency") or "USD", "importance": r.get("importance"), "notes": (r.get("notes") or "").strip() or None,
            "url": r.get("url") or r.get("url_calendar"), "url_news": r.get("url_news"), "updated": r.get("updated")}


def merge_actions(old, new, removed_ids=()):
    """Merge by Benzinga id (newer `updated` wins), drop ids Benzinga has withdrawn, newest first."""
    by = {a["id"]: a for a in (old or []) if a.get("id")}
    for a in new or []:
        if not a.get("id"):
            continue
        prev = by.get(a["id"])
        if not prev or (a.get("updated") or 0) >= (prev.get("updated") or 0):
            by[a["id"]] = a
    rm = set(removed_ids or ())
    out = [a for a in by.values() if a["id"] not in rm]
    out.sort(key=lambda a: (a.get("date") or "", a.get("time") or "", a["id"]), reverse=True)
    return out


def latest_by_firm(actions, since):
    """Most recent action per firm since `since` (actions newest first). Coverage drops remove the firm."""
    seen, out = set(), []
    for a in actions:
        if (a.get("date") or "") < since or not a.get("firm"):
            continue
        key = a.get("firm_id") or a["firm"]
        if key in seen:
            continue
        seen.add(key)
        if a.get("action") in INACTIVE_ACTIONS:
            continue
        out.append(a)
    return out


def target_stats(actions, since, price=None):
    """Latest price target per firm within the window. Uses Benzinga's split-adjusted target when present
    (comparable with today's price) and keeps the printed target alongside."""
    rows = []
    dropped = set()
    seen = set()
    for a in actions:  # firms whose most recent action ended coverage do not contribute a target
        k = a.get("firm_id") or a.get("firm")
        if k in seen:
            continue
        seen.add(k)
        if a.get("action") in INACTIVE_ACTIONS:
            dropped.add(k)
    for a in latest_by_firm([a for a in actions if (a.get("adj_pt_current") or a.get("pt_current")) is not None and (a.get("firm_id") or a.get("firm")) not in dropped], since):
        pt = a.get("adj_pt_current") if a.get("adj_pt_current") is not None else a.get("pt_current")
        rows.append({"id": a["id"], "date": a["date"], "firm": a["firm"], "firm_id": a.get("firm_id"), "analyst": a.get("analyst"),
                     "analyst_id": a.get("analyst_id"), "pt": pt, "pt_printed": a.get("pt_current_printed"),
                     "adjusted": a.get("adj_pt_current") is not None and a.get("pt_current") is not None and abs(a["adj_pt_current"] - a["pt_current"]) > 1e-9,
                     "rating": a.get("rating_current"), "bucket": rating_bucket(a.get("rating_current")), "url": a.get("url")})
    rows.sort(key=lambda r: -r["pt"])
    pts = [r["pt"] for r in rows]
    stats = None
    if pts:
        mean = sum(pts) / len(pts)
        stats = {"n": len(pts), "mean": mean, "median": median(pts), "high": max(pts), "low": min(pts),
                 "upside_mean": (mean / price - 1) if price else None, "upside_median": (median(pts) / price - 1) if price else None}
    return {"since": since, "rows": rows, "stats": stats}


def rating_distribution(actions, since):
    rows = latest_by_firm([a for a in actions if a.get("rating_current")], since)
    counts = {"buy": 0, "hold": 0, "sell": 0, "other": 0}
    for a in rows:
        counts[rating_bucket(a["rating_current"]) or "other"] += 1
    return {"since": since, "counts": counts, "n": len(rows),
            "firms": [{"id": a["id"], "firm": a["firm"], "firm_id": a.get("firm_id"), "rating": a["rating_current"], "bucket": rating_bucket(a["rating_current"]), "date": a["date"]} for a in rows]}


def rating_history(actions, months=12, today=None):
    """Month-end distribution of the latest rating per firm (trailing 12-month window at each month end)."""
    today = pdate(today or dt.datetime.now(dt.timezone.utc).date().isoformat())
    out = []
    for i in range(months - 1, -1, -1):
        y, m = divmod(today.month - 1 - i, 12)
        y, m = today.year + y, m + 1
        end = (dt.date(y + (m == 12), m % 12 + 1, 1) - dt.timedelta(days=1)).isoformat()
        if i == 0:
            end = today.isoformat()
        start = (pdate(end) - dt.timedelta(days=ACTION_WINDOW_DAYS)).isoformat()
        d = rating_distribution([a for a in actions if (a.get("date") or "") <= end], start)
        out.append({"month": end[:7], "end": end, **d["counts"], "n": d["n"]})
    return out


def action_summary(actions, since):
    w = [a for a in actions if (a.get("date") or "") >= since]
    kinds = [action_kind(a) for a in w]
    moved = [a for a in w if a.get("pt_current") is not None and a.get("pt_prior") not in (None, 0) and a["pt_current"] != a["pt_prior"]]
    return {"since": since, "count": len(w), "upgrades": kinds.count("upgrade"), "downgrades": kinds.count("downgrade"),
            "initiations": kinds.count("initiate"), "target_raises": sum(1 for a in moved if a["pt_current"] > a["pt_prior"]),
            "target_cuts": sum(1 for a in moved if a["pt_current"] < a["pt_prior"]),
            "median_target_move": median([a["pt_current"] / a["pt_prior"] - 1 for a in moved])}


# ---------- SEC actuals and price reaction ----------

def sec_quarterly(bundle, label):
    for r in ((bundle or {}).get("statements") or {}).get("income") or []:
        if r.get("label") == label:
            return [q for q in r.get("quarterly") or [] if q.get("v") is not None and q.get("end")]
    return []


def match_quarter(rows, end, tol=20):
    best = None
    for r in rows:
        d = abs(days_between(r["end"], end))
        if d <= tol and (best is None or d < best[0]):
            best = (d, r)
    return best[1] if best else None


def filing_url(cik, accn):
    if not cik or not accn:
        return None
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accn.replace('-', '')}/"


def bars_from_prices(p):
    out = []
    for b in (p or {}).get("d") or []:
        try:
            out.append((dt.datetime.fromtimestamp(b[0], dt.timezone.utc).date().isoformat(), float(b[4])))
        except (TypeError, ValueError, IndexError):
            continue
    return out


def reaction(bars, filed, spy_bars=None):
    """Next-session move after the 8-K filing date (same convention as the Earnings tab), and SPY on that session."""
    if not bars or not filed:
        return None
    i = next((k for k, b in enumerate(bars) if b[0] > filed), None)
    if i is None or i < 1:
        return None
    r1 = bars[i][1] / bars[i - 1][1] - 1
    s1 = None
    if spy_bars:
        idx = {b[0]: k for k, b in enumerate(spy_bars)}
        j = idx.get(bars[i][0])
        if j:
            s1 = spy_bars[j][1] / spy_bars[j - 1][1] - 1
    return {"date": bars[i][0], "prev_date": bars[i - 1][0], "r1": r1, "spy1": s1, "close": bars[i][1], "prev_close": bars[i - 1][1]}


def surprises(yahoo, company, bars=None, spy_bars=None):
    """EPS surprise history. The surprise itself is the provider's: Yahoo's reported EPS vs Yahoo's consensus,
    which is generally on the adjusted (non-GAAP) basis analysts forecast. Our SEC diluted EPS (GAAP) for the
    same quarter is attached for reference and never substituted into the surprise."""
    if not yahoo:
        return []
    eps = sec_quarterly(company, "EPS diluted")
    rev = sec_quarterly(company, "Revenue")
    e8k = sorted((company or {}).get("earnings8k") or [], key=lambda x: x.get("filed") or "")
    cik = (company or {}).get("cik")
    out = []
    for h in yahoo.get("history") or []:
        q = h["quarter_end"]
        g = match_quarter(eps, q)
        rv = match_quarter(rev, q)
        rep = next((e for e in e8k if e.get("filed") and 0 < days_between(q, e["filed"]) <= 75), None)
        row = {**h, "basis": "provider", "provider": YAHOO_LABEL,
               "beat": None if h["eps_actual"] is None or h["eps_estimate"] is None else (h["eps_actual"] > h["eps_estimate"]) - (h["eps_actual"] < h["eps_estimate"]),
               "gaap_eps": {"v": g["v"], "end": g["end"], "form": g.get("form"), "filed": g.get("filed"), "accn": g.get("accn"), "url": filing_url(cik, g.get("accn")),
                            "derived": g.get("validation_status") == "derived"} if g else None,
               "revenue": {"v": rv["v"], "end": rv["end"], "form": rv.get("form"), "filed": rv.get("filed"), "accn": rv.get("accn"), "url": filing_url(cik, rv.get("accn")),
                           "derived": rv.get("validation_status") == "derived"} if rv else None,
               "report": {"filed": rep["filed"], "accn": rep.get("accn"), "url": rep.get("url")} if rep else None}
        row["reaction"] = reaction(bars, rep["filed"], spy_bars) if rep and bars else None
        out.append(row)
    return out


def revenue_surprises(snapshots, company):
    """Revenue vs consensus going forward: the last stored snapshot taken before the quarter's 8-K, against SEC
    revenue (GAAP) for the same quarter. Both sides are labeled; history accrues as snapshots accumulate."""
    rev = sec_quarterly(company, "Revenue")
    e8k = sorted((company or {}).get("earnings8k") or [], key=lambda x: x.get("filed") or "")
    cik = (company or {}).get("cik")
    out, seen = [], set()
    for s in snapshots or []:
        p = s["p"].get("0q")
        if not p or not p.get("end") or p.get("rev") is None or p["end"] in seen:
            continue
        rep = next((e for e in e8k if e.get("filed") and 0 < days_between(p["end"], e["filed"]) <= 75), None)
        if not rep:
            continue
        before = [x for x in snapshots if x["as_of"] < rep["filed"] and x["p"].get("0q", {}).get("end") == p["end"] and x["p"]["0q"].get("rev") is not None]
        actual = match_quarter(rev, p["end"])
        if not before or not actual:
            continue
        seen.add(p["end"])
        c = before[-1]["p"]["0q"]
        out.append({"quarter_end": p["end"], "consensus": c["rev"], "consensus_n": c.get("rev_n"), "consensus_as_of": before[-1]["as_of"],
                    "actual": actual["v"], "actual_basis": "GAAP revenue (SEC)", "surprise_pct": pct_change(actual["v"], c["rev"]),
                    "filing": {"form": actual.get("form"), "accn": actual.get("accn"), "url": filing_url(cik, actual.get("accn"))}, "report": rep})
    return out


# ---------- guidance (existing bundle, read only) ----------

GUIDE_METRIC = {"revenue": "revenue", "eps": "eps", "eps_diluted": "eps", "adjusted_eps": "eps", "net_sales": "revenue"}


def guidance_section(g, consensus):
    """Guidance timeline with verbatim quotes, guidance vs consensus where the period matches, and credibility."""
    if not g:
        return {"available": False, "reason": "No guidance bundle for this company."}
    items, docs_used = [], set()
    for s in g.get("series") or []:
        for p in s.get("periods") or []:
            for pt in p.get("points") or []:
                items.append({"id": pt.get("id"), "series": s.get("key"), "metric": s.get("metric"), "label": s.get("label") or s.get("name"),
                              "basis": s.get("basis"), "unit": s.get("unit"), "segment": s.get("segment"), "period": p.get("label"),
                              "period_type": p.get("type"), "period_end": p.get("end"), "issued": pt.get("d"), "low": pt.get("lo"), "high": pt.get("hi"),
                              "mid": pt.get("mid"), "bound": pt.get("b"), "action": pt.get("a"), "withdrawn": bool(pt.get("w")), "quote": pt.get("q"),
                              "printed": pt.get("pr"), "doc": pt.get("doc"), "page": pt.get("pg"), "method": pt.get("m")})
                if pt.get("doc"):
                    docs_used.add(pt["doc"])
    items.sort(key=lambda x: (x["issued"] or "", x["id"] or ""), reverse=True)
    docs = {k: v for k, v in (g.get("docs") or {}).items() if k in docs_used}
    outcomes = []
    for s in g.get("series") or []:
        for p in s.get("periods") or []:
            o, a = p.get("outcome"), p.get("actual")
            if o and a:
                outcomes.append({"series": s.get("key"), "label": s.get("label") or s.get("name"), "unit": s.get("unit"), "basis": s.get("basis"),
                                 "period": p.get("label"), "period_type": p.get("type"), "period_end": p.get("end"), "final_low": o.get("final_low"),
                                 "final_high": o.get("final_high"), "final_mid": o.get("final_mid"), "final_id": o.get("final_id"), "actual": a.get("v"),
                                 "actual_source": a.get("src"), "actual_inputs": (a.get("inputs") or [])[:2], "result": o.get("result"),
                                 "revisions": o.get("revisions"), "scale_check": scale_ok(o.get("final_mid"), a.get("v"))})
    outcomes.sort(key=lambda x: x["period_end"] or "", reverse=True)
    return {"available": True, "generated": g.get("generated"), "coverage": g.get("coverage"), "items": items[:400], "docs": docs,
            "vs_consensus": guidance_vs_consensus(items, consensus), "outcomes": outcomes[:60], "credibility": g.get("credibility"),
            "withdrawn": g.get("withdrawn") or []}


def scale_ok(a, b):
    """Guards against comparing a quarterly figure with an annual one (or mismatched units): within 0.5x-2x."""
    if a is None or b is None or a == 0 or b == 0 or (a > 0) != (b > 0):
        return None
    r = abs(a / b)
    return 0.5 <= r <= 2


def guidance_vs_consensus(items, consensus):
    """Latest open guidance per metric and period against the consensus for the same fiscal period
    (end dates within 20 days, quarterly vs quarterly, annual vs annual, same scale)."""
    if not consensus:
        return []
    latest = {}
    for it in items:
        m = GUIDE_METRIC.get(it.get("metric") or "")
        if not m or it.get("withdrawn") or not it.get("period_end") or it.get("segment"):
            continue
        k = (m, it["period_end"], it.get("basis"))
        if k not in latest or (it["issued"] or "") > (latest[k]["issued"] or ""):
            latest[k] = it
    out = []
    for (m, end, basis), it in latest.items():
        for p in consensus.get("periods") or []:
            if not p.get("end") or abs(days_between(p["end"], end)) > 20:
                continue
            quarterly = p["code"].endswith("q")
            if (it.get("period_type") == "FY") == quarterly:
                continue
            c = p["eps" if m == "eps" else "revenue"]
            if c.get("avg") is None:
                continue
            gm = it.get("mid") if it.get("mid") is not None else it.get("high") if it.get("low") is None else it.get("low")
            out.append({"metric": m, "label": it["label"], "basis": basis, "period": it["period"], "period_end": end, "code": p["code"],
                        "guide_low": it.get("low"), "guide_high": it.get("high"), "guide_mid": gm, "guide_id": it["id"], "issued": it["issued"],
                        "doc": it.get("doc"), "page": it.get("page"), "quote": it.get("quote"), "consensus": c["avg"], "consensus_low": c.get("low"),
                        "consensus_high": c.get("high"), "consensus_n": c.get("n"), "consensus_as_of": consensus["as_of"],
                        "gap_pct": pct_change(c["avg"], gm) if gm else None, "scale_check": scale_ok(gm, c["avg"]),
                        "position": None if it.get("low") is None or it.get("high") is None else ("above" if c["avg"] > it["high"] else "below" if c["avg"] < it["low"] else "within")})
    out.sort(key=lambda x: (x["period_end"], x["metric"]))
    return out


# ---------- bundle ----------

def build_bundle(ticker, *, name=None, yahoo=None, prev=None, actions=None, company=None, prices=None, spy=None, guidance=None,
                 sources=None, today=None, removed_ids=()):
    """Assemble estimates/{T}.json from normalized inputs. `prev` is the previously published bundle (snapshots and
    actions accumulate across refreshes; a refresh without new Yahoo data keeps the previous consensus)."""
    today = today or dt.datetime.now(dt.timezone.utc).date().isoformat()
    prev = prev or {}
    consensus = yahoo or prev.get("consensus")
    snaps = merge_snapshots(prev.get("snapshots"), snapshot_of(yahoo) if yahoo and yahoo.get("periods") else None, today)
    acts = merge_actions(prev.get("actions"), actions, removed_ids)
    bars = bars_from_prices(prices)
    spy_bars = bars_from_prices(spy)
    price = {"close": bars[-1][1], "date": bars[-1][0], "source": "prices/" + ticker + ".json"} if bars else None
    since = (pdate(today) - dt.timedelta(days=ACTION_WINDOW_DAYS)).isoformat()
    srcs = dict(prev.get("sources") or {})
    srcs.update(sources or {})
    b = {
        "ticker": ticker, "name": name, "version": VERSION, "generated": now_iso(), "today": today,
        "attribution": {"benzinga": BENZINGA_ATTRIBUTION, "yahoo": "Consensus estimates from Yahoo Finance (third-party, not from company filings)"},
        "sources": srcs, "price": price,
        "consensus": consensus, "snapshots": snaps, "revisions": revisions(snaps),
        "surprises": surprises(consensus, company, bars, spy_bars) if consensus else [],
        "revenue_surprises": revenue_surprises(snaps, company),
        "actions": acts,
        "targets": target_stats(acts, since, price["close"] if price else None),
        "ratings": rating_distribution(acts, since), "rating_history": rating_history(acts, 12, today),
        "summary": {"d90": action_summary(acts, (pdate(today) - dt.timedelta(days=90)).isoformat()),
                    "d365": action_summary(acts, since)},
        "guidance": guidance_section(guidance, consensus),
        "street_view": prev.get("street_view"),
    }
    b["coverage"] = {"consensus": bool(consensus and consensus.get("periods")), "periods": len((consensus or {}).get("periods") or []),
                     "snapshots": len(snaps), "surprises": len(b["surprises"]), "actions": len(acts),
                     "actions_from": acts[-1]["date"] if acts else None, "actions_to": acts[0]["date"] if acts else None,
                     "firms": len({a.get("firm_id") or a["firm"] for a in acts}), "analysts": len({a.get("analyst_id") for a in acts if a.get("analyst_id")}),
                     "guidance_items": len(b["guidance"].get("items") or []), "guidance_vs_consensus": len(b["guidance"].get("vs_consensus") or [])}
    return b


# ---------- rosters ----------

ACCURACY_KEYS = ("smart_score", "overall_success_rate", "overall_average_return", "overall_avg_return_percentile", "total_ratings",
                 "total_ratings_percentile", "1y_success_rate", "1y_average_return", "1y_smart_score", "3y_success_rate", "3y_average_return",
                 "overall_gain_count", "overall_loss_count")


def build_rosters(bundles_actions, analysts_raw, firms_raw, logos=None, today=None):
    """estimates/analysts.json and estimates/firms.json from every company's actions plus Benzinga's rosters.
    Analyst accuracy is Benzinga's own statistic across all of an analyst's ratings (not just our coverage)."""
    today = today or dt.datetime.now(dt.timezone.utc).date().isoformat()
    an, fm = {}, {}
    for tk, acts in bundles_actions.items():
        for a in acts:
            fid = a.get("firm_id") or a.get("firm")
            f = fm.setdefault(fid, {"id": a.get("firm_id"), "name": a.get("firm"), "tickers": set(), "analysts": set(), "n_actions": 0, "last": ""})
            f["tickers"].add(tk)
            f["n_actions"] += 1
            f["last"] = max(f["last"], a.get("date") or "")
            if a.get("analyst_id"):
                f["analysts"].add(a["analyst_id"])
                x = an.setdefault(a["analyst_id"], {"id": a["analyst_id"], "name": a.get("analyst"), "firm_id": a.get("firm_id"), "firm": a.get("firm"),
                                                    "tickers": {}, "n_actions": 0, "last": "", "first": "9999"})
                x["tickers"][tk] = x["tickers"].get(tk, 0) + 1
                x["n_actions"] += 1
                x["last"] = max(x["last"], a.get("date") or "")
                x["first"] = min(x["first"], a.get("date") or "9999")
    roster = {r.get("id"): r for r in analysts_raw or [] if r.get("id")}
    firms_by = {r.get("id"): r for r in firms_raw or [] if r.get("id")}
    analysts = []
    for x in an.values():
        r = roster.get(x["id"]) or {}
        acc = r.get("ratings_accuracy") or {}
        analysts.append({"id": x["id"], "name": x["name"] or r.get("name_full"), "firm_id": x["firm_id"], "firm": x["firm"] or r.get("firm_name"),
                         "coverage": sorted(x["tickers"], key=lambda t: -x["tickers"][t]), "n_actions": x["n_actions"], "first": x["first"], "last": x["last"],
                         "accuracy": {k: num(acc.get(k)) for k in ACCURACY_KEYS if acc.get(k) not in (None, "")} or None,
                         "accuracy_updated": dt.datetime.fromtimestamp(r["updated"], dt.timezone.utc).date().isoformat() if isinstance(r.get("updated"), (int, float)) else None})
    analysts.sort(key=lambda x: (-x["n_actions"], x["name"] or ""))
    firms = []
    for fid, f in fm.items():
        r = firms_by.get(fid) or {}
        firms.append({"id": f["id"], "name": f["name"] or r.get("name"), "n_actions": f["n_actions"], "n_tickers": len(f["tickers"]), "n_analysts": len(f["analysts"]),
                      "last": f["last"], "logo": (logos or {}).get(fid), "homepage": r.get("homepage")})
    firms.sort(key=lambda x: -x["n_actions"])
    meta = {"generated": now_iso(), "attribution": BENZINGA_ATTRIBUTION, "provider": "Benzinga via Firecrawl Alexandria",
            "note": "Accuracy statistics are Benzinga's, computed across each analyst's full rating history."}
    return {**meta, "analysts": analysts}, {**meta, "firms": firms}
