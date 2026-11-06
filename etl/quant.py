"""Technicals & Quant lab: per-symbol quantitative analytics precomputed from our own stored daily bars.

Inputs (all already in storage unless noted):
  tech/bars/{KEY}.json         split-adjusted daily OHLCV since 2008 + cash dividends (technicals job); falls back to
                               prices/{KEY}.json (two years of daily bars) for symbols without a long history file
  macro/s/TSY_BILL_13WEEKS.json  13-week Treasury bill, coupon equivalent (risk-free rate)
  quant/filings/{T}.json       8-K/10-K/10-Q with EDGAR acceptance times (etl/quant_sources.py; falls back to tech/events/{T}.json)
  quant/factors/ff_daily.json  Fama-French 5 + momentum daily factors (etl/quant_sources.py)
  quant/calendar/macro.json    CPI, payrolls and FOMC statement dates (etl/quant_sources.py)
Outputs:
  quant/t/{KEY}.json           everything for one symbol (statistics, correlation & factors, events, seasonality, regimes, backtests)
  quant/parts/part{k}.json     cross-sectional inputs of one slice (metrics + the last 252 daily returns)
  quant/cross.json             cross-section of covered companies (relative strength, sector ranks, scatter inputs)
  quant/peers/{T}.json         most / least correlated covered names and a sector-peer correlation matrix
  quant/methods.json           formula, window and data source of every published number (shown in the page tooltips)
  quant/meta.json              run status and coverage counts
"""
import bisect
import datetime as dt
import json
import math
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor

from . import quant_calc as Q
from .common import now_iso
from .prices import path_key
from .universe import BY_TICKER, MARKET_SYMBOLS, PREDECESSORS, SECTOR_ETF, UNIVERSE

VERSION = 1
SKIP = {"^TNX", "^IRX", "^TYX"}       # yield levels, not prices: used as macro factors, not analysed as assets
MACRO = [("^TNX", "10Y Treasury yield", "diff"), ("UUP", "US dollar (UUP)", "ret"), ("CL=F", "WTI crude (front month)", "ret"),
         ("GC=F", "Gold (front month)", "ret"), ("^VIX", "VIX", "ret")]
FACTORS = ["mkt_rf", "smb", "hml", "rmw", "cma", "mom"]
FACTOR_NAMES = {"mkt_rf": "Market (Mkt-RF)", "smb": "Size (SMB)", "hml": "Value (HML)", "rmw": "Profitability (RMW)", "cma": "Investment (CMA)", "mom": "Momentum (Mom)"}
RF_PATH = "macro/s/TSY_BILL_13WEEKS.json"
RF_SOURCE = "U.S. Treasury daily bill rates, 13-week coupon equivalent (macro/s/TSY_BILL_13WEEKS.json); daily rf = yield / 100 / 252, last value carried forward"
NW_LAGS = 5
STEP = 5          # downsampling of published daily series (every 5th trading day, the last day always included)
ITEM_NAMES = {"1.01": "Material agreement", "1.02": "Agreement terminated", "1.03": "Bankruptcy", "1.05": "Cybersecurity incident", "2.01": "Acquisition or disposition",
              "2.03": "Debt obligation", "2.04": "Triggering event", "2.05": "Exit costs", "2.06": "Impairment", "3.01": "Listing notice", "3.02": "Unregistered equity sale",
              "3.03": "Rights modification", "4.01": "Auditor change", "4.02": "Non-reliance on financials", "5.01": "Change in control", "5.02": "Officer/director change",
              "5.03": "Bylaw amendment", "5.07": "Shareholder vote", "5.08": "Director nominations", "7.01": "Reg FD disclosure", "8.01": "Other events"}


def R(x, d=6):
    if x is None or (isinstance(x, float) and not math.isfinite(x)):
        return None
    return round(x, d)


def all_symbols():
    return [s for s in MARKET_SYMBOLS if s not in SKIP] + [t for t, *_ in UNIVERSE]


def qpath(sym):
    return f"quant/t/{path_key(sym)}.json"


# ---------------------------------------------------------------- loading
def load_bars(sb, sym):
    """{'d','o','h','l','c','v','div','file','meta','long'} from tech/bars or the two-year prices file."""
    try:
        h = sb.get_json(f"tech/bars/{path_key(sym)}.json")
    except Exception:
        h = None
    if h and h.get("d"):
        return {"d": h["d"], "o": h["o"], "h": h["h"], "l": h["l"], "c": h["c"], "v": h["v"], "div": h.get("div") or [], "file": f"tech/bars/{path_key(sym)}.json",
                "meta": h.get("meta") or {}, "long": True}
    try:
        p = sb.get_json(f"prices/{path_key(sym)}.json")
    except Exception:
        p = None
    if not p or not p.get("d"):
        return None
    days, seen = [], set()
    o, hi, lo, c, v = [], [], [], [], []
    for b in p["d"]:
        d = dt.datetime.fromtimestamp(b[0], dt.timezone.utc).date().isoformat()
        if d in seen or b[4] is None:
            continue
        seen.add(d)
        days.append(d); o.append(b[1]); hi.append(b[2]); lo.append(b[3]); c.append(b[4]); v.append(int(b[5] or 0))
    return {"d": days, "o": o, "h": hi, "l": lo, "c": c, "v": v, "div": p.get("dv") or [], "file": f"prices/{path_key(sym)}.json", "meta": p.get("meta") or {}, "long": False}


def prep(H):
    H["tr"] = Q.total_return_index(H["c"], H["d"], H["div"])
    H["r"] = Q.simple_returns(H["tr"])
    H["lr"] = Q.log_returns(H["tr"])
    c = H["c"]
    for i in range(1, len(c)):
        if not (c[i] > 0 and c[i - 1] > 0):
            H["r"][i] = H["lr"][i] = None
    H["idx"] = {d: i for i, d in enumerate(H["d"])}
    return H


def rf_series(sb):
    j = sb.get_json(RF_PATH) or {}
    return {d: v for d, v in j.get("obs", []) if v is not None}, j.get("retrieved")


def daily_rf(days, rfmap):
    """Daily risk-free return per trading day: last published 13-week bill yield on or before the day / 100 / 252."""
    keys = sorted(rfmap)
    out, last = [], None
    k = 0
    for d in days:
        while k < len(keys) and keys[k] <= d:
            last = rfmap[keys[k]]
            k += 1
        out.append(last / 100.0 / 252 if last is not None else None)
    return out


class Context:
    """Shared inputs for one run: benchmark and macro histories, risk-free rate, factors, macro calendar, market calendar."""

    def __init__(self, sb, log=print):
        self.sb = sb
        self.H = {}
        for s in ["SPY"] + sorted(set(SECTOR_ETF.values())) + [m[0] for m in MACRO]:
            h = load_bars(sb, s)
            if h:
                self.H[s] = prep(h)
        self.rf, self.rf_retrieved = rf_series(sb)
        try:
            self.factors = sb.get_json("quant/factors/ff_daily.json")
        except Exception:
            self.factors = None
        try:
            self.calendar = sb.get_json("quant/calendar/macro.json")
        except Exception:
            self.calendar = None
        spy = self.H["SPY"]
        self.mkt_days = spy["d"]
        self.as_of = spy["d"][-1]
        self.pre_hol, self.post_hol = Q.holidays_from_calendar(spy["d"])
        self.pre_hol_d = {spy["d"][i] for i in self.pre_hol}
        self.post_hol_d = {spy["d"][i] for i in self.post_hol}
        self.cross_days = spy["d"][-253:]
        self.fidx = {d: i for i, d in enumerate(self.factors["d"])} if self.factors else {}
        self.recent = None      # set by run_part: accessions recorded in the filings table, to top up stale caches
        self.fstats = {}


# ---------------------------------------------------------------- helpers
def bench_returns(H, B):
    """Benchmark simple TR returns on the asset's dates (None where either day is missing in the benchmark)."""
    out = [None] * len(H["d"])
    bi = B["idx"]
    for i in range(1, len(H["d"])):
        a, b = bi.get(H["d"][i]), bi.get(H["d"][i - 1])
        if a is not None and b is not None and B["tr"][b] > 0 and B["c"][a] > 0 and B["c"][b] > 0:
            out[i] = B["tr"][a] / B["tr"][b] - 1
    return out


def macro_changes(H, B, how):
    out = [None] * len(H["d"])
    bi = B["idx"]
    for i in range(1, len(H["d"])):
        a, b = bi.get(H["d"][i]), bi.get(H["d"][i - 1])
        if a is None or b is None:
            continue
        x, y = B["c"][a], B["c"][b]
        if how == "diff":
            out[i] = x - y
        elif x > 0 and y > 0:
            out[i] = x / y - 1
    return out


def pairs(a, b, lo=0, hi=None):
    hi = len(a) if hi is None else hi
    xs, ys = [], []
    for i in range(max(lo, 0), hi):
        if a[i] is not None and b[i] is not None:
            xs.append(a[i]); ys.append(b[i])
    return xs, ys


def sample_idx(L, start, step=STEP):
    idx = list(range(max(start, 0), L, step))
    if not idx or idx[-1] != L - 1:
        idx.append(L - 1)
    return idx


def first_on_or_after(days, d):
    i = bisect.bisect_left(days, d)
    return i if i < len(days) else None


def first_after(days, d):
    i = bisect.bisect_right(days, d)
    return i if i < len(days) else None


def dist_out(dist):
    if not dist:
        return None
    o = {k: R(dist[k]) for k in ("mean", "sd", "min", "max", "up")}
    o.update({"n": dist["n"], "skew": R(dist["skew"], 4), "kurt": R(dist["kurt"], 4), "pct": {k: R(v) for k, v in dist["pct"].items()}})
    if "beyond3" in dist:
        o["beyond3"], o["normal3"] = dist["beyond3"], R(dist["normal3"], 2)
    return o


# ---------------------------------------------------------------- statistics
def statistics(H, ctx, rf, benches):
    L = len(H["d"])
    r = [x for x in H["r"][1:] if x is not None]
    lr = [x for x in H["lr"][1:] if x is not None]
    days = H["d"]
    out = {"daily": dist_out(Q.distribution(r)), "daily_1y": dist_out(Q.distribution(r[-252:])), "weekly": dist_out(Q.distribution(Q.week_returns(H["tr"], days)))}
    m = out["daily"]
    if m and m["sd"]:
        span = max(0.05, 4 * m["sd"])
        hist = Q.histogram(r, -span, span, 60)
        out["hist"] = {"lo": R(hist["lo"]), "w": R(hist["w"], 8), "n": hist["n"], "under": hist["under"], "over": hist["over"]}
    # realized volatility
    vol = {}
    series = {}
    for n in (20, 60, 252):
        v = Q.rolling_sd(H["lr"], n, math.sqrt(252))
        series[n] = v
        vals = sorted(x for x in v if x is not None)
        if not vals or v[-1] is None:
            vol[str(n)] = None
            continue
        vol[str(n)] = {"now": R(v[-1]), "pct": R(Q.pct_rank(vals, v[-1]), 4), "p10": R(Q.percentile(vals, 10)), "p50": R(Q.percentile(vals, 50)),
                       "p90": R(Q.percentile(vals, 90)), "max": R(vals[-1]), "min": R(vals[0]), "count": len(vals), "since": days[next(i for i, x in enumerate(v) if x is not None)]}
    out["vol"] = vol
    idx = sample_idx(L, L - 2520)
    out["vol_series"] = {"d": [days[i] for i in idx], "v20": [R(series[20][i], 4) for i in idx], "v60": [R(series[60][i], 4) for i in idx], "v252": [R(series[252][i], 4) for i in idx]}
    v20 = [x for x in series[20][-252:] if x is not None]
    if len(v20) >= 60:
        mv, sv = Q.mean(v20), Q.sd(v20)
        chg = [math.log(series[20][i] / series[20][i - 1]) for i in range(max(1, L - 251), L) if series[20][i] and series[20][i - 1]]
        out["vov"] = {"sd": R(sv, 4), "mean": R(mv, 4), "rel": R(sv / mv, 4) if mv else None, "chg_sd_ann": R(Q.sd(chg) * math.sqrt(252), 4) if len(chg) > 20 else None, "n": len(v20)}
    # drawdowns
    dd, top, cur = Q.drawdowns(H["tr"], days)
    out["dd"] = {"top": [{k: (R(v, 6) if isinstance(v, float) else v) for k, v in e.items()} for e in top], "current": R(cur["current"]), "peak_date": cur["peak_date"],
                 "series": {"d": [days[i] for i in sample_idx(L, 0)], "v": [R(dd[i], 4) for i in sample_idx(L, 0)]}}
    # capture vs benchmarks (monthly)
    me = Q.month_end_returns(H["tr"], days)
    cap = {}
    for bs, B in benches.items():
        bm = dict(Q.month_end_returns(B["tr"], B["d"]))
        pr = [(a, bm[k]) for k, a in me if k in bm]
        if len(pr) < 12:
            continue
        cap[bs] = {}
        for name, k in (("3y", 36), ("5y", 60), ("full", len(pr))):
            seg = pr[-k:]
            if len(seg) < 12 or (name != "full" and len(pr) < k):
                continue
            c = Q.capture([a for a, _ in seg], [b for _, b in seg])
            cap[bs][name] = {"up": R(c["up"], 4), "down": R(c["down"], 4), "ratio": R(c.get("ratio"), 4), "n_up": c["n_up"], "n_down": c["n_down"], "from": me[-len(seg)][0], "to": me[-1][0]}
    out["capture"] = cap
    # Sharpe / Sortino
    perf = {}
    for name, k in (("1y", 252), ("3y", 756), ("5y", 1260), ("full", L - 1)):
        if L - 1 < k:
            continue
        rr, ff = [], []
        for i in range(L - k, L):
            if H["r"][i] is not None and rf[i] is not None:
                rr.append(H["r"][i]); ff.append(rf[i])
        s = Q.sharpe_sortino(rr, ff)
        if s:
            perf[name] = {k2: (R(v, 4) if isinstance(v, float) else v) for k2, v in s.items()}
            perf[name]["from"] = days[L - k]
    out["perf"] = perf
    # VaR / CVaR
    vv = {}
    for name, sample in (("1y", r[-252:]), ("full", r)):
        e = {"n": len(sample)}
        for lev in (0.95, 0.99):
            v, c, n = Q.var_cvar(sample, lev)
            e[str(int(lev * 100))] = [R(v), R(c)]
        vv[name] = e
    out["var"] = vv
    # dependence (last five years of daily returns, or all when shorter)
    r5 = r[-1260:]
    lr5 = lr[-1260:]
    a = Q.acf(r5, 10)
    if a:
        out["acf"] = {"n": a["n"], "rho": [R(x, 4) for x in a["rho"]], "band": R(a["band"], 4), "lb_q": R(a["lb_q"], 3), "lb_p": R(a["lb_p"], 4), "from": days[L - len(r5)]}
    out["vr"] = [dict((k, R(v, 4) if isinstance(v, float) else v) for k, v in x.items()) for x in (Q.variance_ratio(lr5, q) for q in (2, 5, 10, 20)) if x]
    hu = Q.hurst_rs(lr)
    if hu:
        out["hurst"] = {"h": R(hu["h"], 4), "r2": R(hu["r2"], 4), "n": hu["n"], "points": [[p[0], R(p[1], 4), p[2]] for p in hu["points"]]}
    return out


# ---------------------------------------------------------------- correlation & factors
def correlation(H, ctx, benches):
    L = len(H["d"])
    days = H["d"]
    out = {"bench": {}, "macro": {}, "factors": None}
    idx = sample_idx(L, L - 2520)
    for bs, B in benches.items():
        rb = bench_returns(H, B)
        e = {}
        roll = {"d": [days[i] for i in idx]}
        for n in (63, 252):
            c, b = Q.rolling_corr_beta(H["r"], rb, n)
            roll[f"c{n}"] = [R(c[i], 4) for i in idx]
            roll[f"b{n}"] = [R(b[i], 4) for i in idx]
            e[f"c{n}"], e[f"b{n}"] = R(c[-1], 4), R(b[-1], 4)
            vals = sorted(x for x in b if x is not None)
            e[f"b{n}_range"] = [R(vals[0], 4), R(vals[-1], 4)] if vals else None
        xs, ys = pairs(H["r"], rb)
        e["full"] = {"corr": R(Q.corr(xs, ys), 4), "beta": R(Q.beta(xs, ys), 4), "n": len(xs), "from": days[1]}
        xs, ys = pairs(H["r"], rb, L - 252)
        up = [(x, y) for x, y in zip(xs, ys) if y > 0]
        dn = [(x, y) for x, y in zip(xs, ys) if y < 0]
        e["updown"] = {"corr_up": R(Q.corr([x for x, _ in up], [y for _, y in up]), 4), "corr_down": R(Q.corr([x for x, _ in dn], [y for _, y in dn]), 4),
                       "beta_up": R(Q.beta([x for x, _ in up], [y for _, y in up]), 4), "beta_down": R(Q.beta([x for x, _ in dn], [y for _, y in dn]), 4), "n_up": len(up), "n_down": len(dn)}
        e["r2_252"] = R(e["c252"] ** 2, 4) if e["c252"] is not None else None
        e["roll"] = roll
        out["bench"][bs] = e
    # macro sensitivities
    midx = sample_idx(L, max(252, L - 2520), 21)
    roll = {"d": [days[i] for i in midx]}
    for sym, label, how in MACRO:
        B = ctx.H.get(sym)
        if not B or sym == H.get("sym"):
            continue
        x = macro_changes(H, B, how)
        c, b = Q.rolling_corr_beta(H["r"], x, 252)
        xs, ys = pairs(H["r"], x)
        out["macro"][sym] = {"label": label, "transform": "daily change in yield, percentage points" if how == "diff" else "daily percent change",
                             "c252": R(c[-1], 4), "b252": R(b[-1], 4), "c_full": R(Q.corr(xs, ys), 4), "b_full": R(Q.beta(xs, ys), 4), "n": len(xs), "from": days[1]}
        roll[sym] = [R(c[i], 4) for i in midx]
    out["macro_roll"] = roll
    out["factors"] = factor_model(H, ctx)
    return out


def factor_model(H, ctx):
    F = ctx.factors
    if not F:
        return None
    fi = ctx.fidx
    y, X, dts = [], [], []
    for i in range(1, len(H["d"])):
        j = fi.get(H["d"][i])
        if j is None or H["r"][i] is None:
            continue
        row = F["f"][j]
        y.append(H["r"][i] - row[6])
        X.append(row[:6])
        dts.append(H["d"][i])
    if len(y) < 150:
        return {"as_of": F.get("as_of"), "n": len(y), "note": "fewer than 150 overlapping days"}
    out = {"as_of": F.get("as_of"), "source": F.get("source"), "source_url": F.get("source_url"), "retrieved": F.get("retrieved"), "windows": {}}
    for name, k in (("1y", 252), ("3y", 756), ("full", len(y))):
        if len(y) < min(k, 150) or (name != "full" and len(y) < k * 0.9):
            continue
        res = Q.ols(y[-k:], X[-k:], NW_LAGS)
        if not res:
            continue
        out["windows"][name] = {"n": res["n"], "from": dts[-min(k, len(y))], "to": dts[-1], "r2": R(res["r2"], 4), "adj_r2": R(res["adj_r2"], 4), "resid_sd": R(res["resid_sd"], 6),
                                "alpha_ann": R(res["coef"][0] * 252, 4), "alpha_t": R(res["nw_t"][0], 3), "alpha_p": R(res["nw_p"][0], 4),
                                "coef": {f: R(res["coef"][a + 1], 4) for a, f in enumerate(FACTORS)}, "t": {f: R(res["nw_t"][a + 1], 3) for a, f in enumerate(FACTORS)},
                                "p": {f: R(res["nw_p"][a + 1], 4) for a, f in enumerate(FACTORS)}, "t_ols": {f: R(res["t"][a + 1], 3) for a, f in enumerate(FACTORS)}}
    if len(y) >= 300:
        rl = Q.rolling_ols(y, X, 252, 21)
        out["roll"] = {"d": [dts[i] for i, _, _ in rl], "r2": [R(r2, 4) for _, _, r2 in rl], "alpha_ann": [R(c[0] * 252, 4) for _, c, _ in rl]}
        for a, f in enumerate(FACTORS):
            out["roll"][f] = [R(c[a + 1], 4) for _, c, _ in rl]
    return out


# ---------------------------------------------------------------- events
def recent_filings(sb, days=12, log=print):
    """{ticker: {accession}} of 8-K/10-K/10-Q filings the companies job recorded in the last `days` days."""
    cut = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    out = {}
    try:
        for r in sb.select("filings", f"select=ticker,accession&filed=gte.{cut}&form=in.(\"8-K\",\"10-Q\",\"10-K\",\"8-K/A\",\"10-Q/A\",\"10-K/A\")"):
            out.setdefault(r["ticker"], set()).add(r["accession"])
    except Exception as e:
        log("filings table read failed: " + str(e)[:160])
    return out


def filings_rows(sb, tk, recent=None, stats=None):
    """Normalised filing dicts. Prefers quant/filings (with acceptance times), else tech/events. With `recent`
    (accessions the filings table knows for this ticker), a stale cache is topped up from the EDGAR submissions API
    ('recent' block only) and saved; a missing cache is fetched in full."""
    src = None
    try:
        j = sb.get_json(f"quant/filings/{tk}.json")
    except Exception:
        j = None
    if recent is not None and tk in BY_TICKER:
        have = {r[2] for r in (j or {}).get("rows", [])}
        if not j or (recent - have):
            try:
                from .quant_sources import filings_doc
                j = filings_doc(tk, BY_TICKER[tk]["cik"], PREDECESSORS.get(tk, []), full=not j, prior=j)
                sb.put_json(f"quant/filings/{tk}.json", j, cache="max-age=3600")
                if stats is not None:
                    stats["refreshed"] = stats.get("refreshed", 0) + 1
            except Exception as e:
                if stats is not None:
                    stats.setdefault("failed", {})[tk] = str(e)[:160]
    rows = []
    if j and j.get("rows"):
        src = "quant/filings/%s.json (SEC EDGAR submissions API, with acceptance times)" % tk
        for f, filed, accn, doc, items, rep, acc, cik in j["rows"]:
            rows.append({"form": f, "filed": filed, "accn": accn, "doc": doc, "items": items, "report": rep, "accepted": acc, "cik": cik})
    else:
        try:
            e = sb.get_json(f"tech/events/{tk}.json")
        except Exception:
            e = None
        if e and e.get("filings"):
            src = "tech/events/%s.json (SEC EDGAR submissions API; no acceptance times)" % tk
            for f, filed, accn, doc, items, rep, cik in e["filings"]:
                rows.append({"form": f, "filed": filed, "accn": accn, "doc": doc, "items": items, "report": rep, "accepted": "", "cik": cik})
    return rows, src


def reaction_day(days, f):
    """Index of the first trading day that could react to a filing and the window length in days.
    Accepted before 09:30 ET -> that day (pre-market); 09:30-16:00 -> that day (intraday); at or after 16:00 -> next
    trading day (after the close). Without an acceptance time: the filing date and the next day (2-day window)."""
    if f.get("accepted"):
        try:
            d, mins = Q.to_eastern(f["accepted"])
        except ValueError:
            d, mins = None, None
        if d:
            if mins >= 960:
                return first_after(days, d), 1, "after close"
            return first_on_or_after(days, d), 1, "pre-market" if mins < 570 else "intraday"
    return first_on_or_after(days, f["filed"]), 2, "timing unknown"


def est_beta(r, rb, i0, lo=260, hi=11, minn=120):
    xs, ys = pairs(r, rb, i0 - lo, i0 - hi + 1)
    if len(xs) < minn:
        return 1.0, None, False
    b = Q.beta(xs, ys)
    res = [x - b * y for x, y in zip(xs, ys)]
    return b, Q.sd(res), True


def reaction(H, rb, rsec, i0, W):
    """Close-to-close move over the window [i0, i0 + W - 1] from the close before, the open gap, beta-adjusted
    abnormal return vs SPY (beta from trading days -260..-11), market-adjusted return vs the sector ETF, sigma,
    5-day pre-run and +5/+20 day post-event drift (raw and abnormal, from the close ending the window)."""
    L = len(H["d"])
    end = i0 + W - 1
    if i0 is None or i0 < 1 or end >= L:
        return None
    tr, r = H["tr"], H["r"]
    b, rsd, ok = est_beta(r, rb, i0)
    ar = lambda t: (r[t] - b * rb[t]) if r[t] is not None and rb[t] is not None else None
    win = [ar(t) for t in range(i0, end + 1)]
    abn = sum(win) if all(x is not None for x in win) else None
    sec = None
    if rsec is not None:
        sw = [(r[t] - rsec[t]) if r[t] is not None and rsec[t] is not None else None for t in range(i0, end + 1)]
        sec = sum(sw) if all(x is not None for x in sw) else None
    o = {"i0": i0, "day0": H["d"][i0], "w": W, "cc": tr[end] / tr[i0 - 1] - 1, "gap": H["o"][i0] / H["c"][i0 - 1] - 1, "intraday": H["c"][i0] / H["o"][i0] - 1 if H["o"][i0] else None,
         "abn": abn, "abn_sector": sec, "beta": b, "beta_est": ok, "sigma": abs(abn) / (rsd * math.sqrt(W)) if abn is not None and rsd and rsd > 1e-9 else None}
    pre = [ar(t) for t in range(i0 - 5, i0)] if i0 >= 6 else []
    o["pre5"] = sum(pre) if pre and all(x is not None for x in pre) else None
    for k in (5, 20):
        if end + k < L:
            o[f"d{k}"] = tr[end + k] / tr[end] - 1
            seg = [ar(t) for t in range(end + 1, end + k + 1)]
            o[f"a{k}"] = sum(seg) if all(x is not None for x in seg) else None
        else:
            o[f"d{k}"] = o[f"a{k}"] = None
    vv = [x for x in H["v"][max(0, i0 - 20):i0] if x]
    o["vol_ratio"] = (max(H["v"][i0:end + 1]) / (sum(vv) / len(vv))) if vv and H["v"][i0] else None
    return o


def typical_abs_ar(H, rb):
    xs, ys = pairs(H["r"], rb, len(H["r"]) - 1260)
    b = Q.beta(xs, ys) if len(xs) > 60 else 1.0
    a = [abs(x - b * y) for x, y in zip(xs, ys)]
    return sum(a) / len(a) if a else None, len(a)


def summarize_reactions(rs, typical=None):
    rs = [x for x in rs if x]
    out = {"n": len(rs)}
    if not rs:
        return out
    ab = [x["abn"] for x in rs if x["abn"] is not None]
    cc = [x["cc"] for x in rs]
    out.update({"mean_abs_cc": R(sum(abs(x) for x in cc) / len(cc)), "median_abs_cc": R(Q.percentile(sorted(abs(x) for x in cc), 50)), "mean_cc": R(sum(cc) / len(cc)),
                "up_share": R(sum(1 for x in cc if x > 0) / len(cc), 4)})
    if ab:
        mt = Q.mean_test(ab)
        out.update({"mean_abn": R(mt["mean"]), "abn_t": R(mt["t"], 3), "abn_p": R(mt["p"], 4), "median_abn": R(Q.percentile(sorted(ab), 50)),
                    "mean_abs_abn": R(sum(abs(x) for x in ab) / len(ab)), "abn_up_share": R(sum(1 for x in ab if x > 0) / len(ab), 4)})
        if typical:
            out["abs_ratio"] = R(out["mean_abs_abn"] / typical, 3)
    gp = [abs(x["gap"]) for x in rs if x["gap"] is not None]
    if gp:
        out["mean_abs_gap"] = R(sum(gp) / len(gp))
    sg = [x["sigma"] for x in rs if x["sigma"] is not None]
    if sg:
        out["mean_sigma"] = R(sum(sg) / len(sg), 3)
    for k in (5, 20):
        dd = [x for x in rs if x.get(f"a{k}") is not None and x["abn"] is not None]
        if len(dd) >= 3:
            mt = Q.mean_test([x[f"a{k}"] for x in dd])
            cont = [1 if (x[f"a{k}"] > 0) == (x["abn"] > 0) else 0 for x in dd if x["abn"] != 0]
            signed = Q.mean_test([x[f"a{k}"] * (1 if x["abn"] > 0 else -1) for x in dd if x["abn"] != 0])
            out[f"drift{k}"] = {"n": len(dd), "mean": R(mt["mean"]), "t": R(mt["t"], 3), "p": R(mt["p"], 4), "hit": R(sum(1 for x in dd if x[f"a{k}"] > 0) / len(dd), 4),
                                "cont": R(sum(cont) / len(cont), 4) if cont else None, "signed_mean": R(signed["mean"]), "signed_t": R(signed["t"], 3), "signed_p": R(signed["p"], 4),
                                "raw_mean": R(sum(x[f"d{k}"] for x in dd) / len(dd))}
    return out


REACT_COLS = ["filed", "accession", "form", "doc", "cik", "accepted", "timing", "day0", "w", "cc", "gap", "intraday", "abn", "abn_sector", "sigma", "pre5", "d5", "a5", "d20", "a20", "vol_ratio", "beta", "items"]


def react_row(f, x):
    return [f["filed"], f["accn"], f["form"], f["doc"], f["cik"], f.get("accepted") or "", x["timing"], x["day0"], x["w"], R(x["cc"]), R(x["gap"]), R(x["intraday"]), R(x["abn"]), R(x["abn_sector"]),
            R(x["sigma"], 3), R(x["pre5"]), R(x["d5"]), R(x["a5"]), R(x["d20"]), R(x["a20"]), R(x["vol_ratio"], 3), R(x["beta"], 3), f.get("items") or ""]


def events(H, ctx, rows, src, benches):
    out = {"source": src, "cols": REACT_COLS}
    if not rows:
        out["note"] = "no filings on record"
        return out
    days = H["d"]
    spy = benches.get("SPY") or ctx.H["SPY"]
    rb = bench_returns(H, spy)
    sec_sym = next((s for s in benches if s != "SPY"), None)
    rsec = bench_returns(H, benches[sec_sym]) if sec_sym else None
    typ, typ_n = typical_abs_ar(H, rb)
    out["typical_abs_ar"] = R(typ)
    out["typical_n"] = typ_n
    out["sector"] = sec_sym
    # earnings announcements: 8-K Item 2.02, falling back to 10-Q / 10-K when a quarter has no 2.02 8-K
    k202 = [f for f in rows if f["form"] == "8-K" and "2.02" in (f["items"] or "").split(",")]
    periodic = [f for f in rows if f["form"] in ("10-Q", "10-K")]
    k202_days = sorted(f["filed"] for f in k202)
    fallback = []
    for f in periodic:
        lo = (dt.date.fromisoformat(f["filed"]) - dt.timedelta(days=50)).isoformat()
        j = bisect.bisect_left(k202_days, lo)
        if not (j < len(k202_days) and k202_days[j] <= f["filed"]):
            fallback.append(f)
    earn, seen = [], set()
    for f in sorted(k202 + fallback, key=lambda f: (f["filed"], f.get("accepted") or "")):
        i0, W, timing = reaction_day(days, f)
        if i0 is None or i0 in seen:
            continue
        x = reaction(H, rb, rsec, i0, W)
        if not x:
            continue
        seen.add(i0)
        x["timing"] = timing
        earn.append((f, x))
    out["earnings"] = {"basis": "8-K Item 2.02 (results of operations) filings; 10-Q/10-K filings where no Item 2.02 8-K was filed in the 50 days before them",
                       "n_8k": sum(1 for f, _ in earn if f["form"] == "8-K"), "n_periodic": sum(1 for f, _ in earn if f["form"] != "8-K"),
                       "stats": summarize_reactions([x for _, x in earn], typ), "rows": [react_row(f, x) for f, x in earn]}
    tm = {}
    for _, x in earn:
        tm[x["timing"]] = tm.get(x["timing"], 0) + 1
    out["earnings"]["timing"] = tm
    # other 8-K items
    by_item = {}
    mat = []
    for f in rows:
        if f["form"] != "8-K":
            continue
        items = [s for s in (f["items"] or "").split(",") if s and s != "9.01"]
        if not items or "2.02" in items:
            continue
        i0, W, timing = reaction_day(days, f)
        if i0 is None or i0 in seen:
            continue
        x = reaction(H, rb, rsec, i0, W)
        if not x:
            continue
        x["timing"] = timing
        mat.append((f, x))
        for it in items:
            by_item.setdefault(it, []).append(x)
    items_out = []
    for it, xs in sorted(by_item.items(), key=lambda kv: -len(kv[1])):
        s = summarize_reactions(xs, typ)
        s.update({"item": it, "name": ITEM_NAMES.get(it, "Item " + it)})
        for k in ("drift5", "drift20"):
            s.pop(k, None)
        items_out.append(s)
    out["material"] = {"basis": "8-K filings without Item 2.02, grouped by each item code they report (Item 9.01 exhibits ignored); a filing with two items counts in both",
                       "items": items_out, "rows": [react_row(f, x) for f, x in mat[-150:]], "total": len(mat)}
    out["macro"] = macro_events(H, ctx, rb)
    return out


def macro_events(H, ctx, rb):
    cal = (ctx.calendar or {}).get("events") or {}
    days = H["d"]
    L = len(days)
    cb, bb = Q.rolling_corr_beta(H["r"], rb, 252)
    out = {}
    all_event = set()
    per = {}
    for key, ev in cal.items():
        idx = []
        for d in ev.get("dates", []):
            i = first_on_or_after(days, d)
            if i is None or i < 1 or (days[i] > d and (dt.date.fromisoformat(days[i]) - dt.date.fromisoformat(d)).days > 4):
                continue
            idx.append((d, i))
        per[key] = idx
        all_event.update(i for _, i in idx)
    base_abs = [abs(H["r"][i]) for i in range(1, L) if H["r"][i] is not None and i not in all_event]
    for key, idx in per.items():
        ev = cal[key]
        rows, rr, ab, rs = [], [], [], []
        for d, i in idx:
            r = H["r"][i]
            if r is None:
                continue
            b = bb[i - 1] if bb[i - 1] is not None else 1.0
            a = r - b * rb[i] if rb[i] is not None else None
            rows.append([d, days[i], R(r), R(rb[i]), R(a), R(b, 3)])
            rr.append(r)
            rs.append(abs(r))
            if a is not None:
                ab.append(a)
        if not rr:
            continue
        mt = Q.mean_test(rr)
        wt = Q.welch(rs, base_abs)
        s = {"title": ev.get("title"), "time": ev.get("time"), "source_url": ev.get("source_url"), "n": len(rr), "mean": R(mt["mean"]), "sd": R(mt["sd"]), "t": R(mt["t"], 3), "p": R(mt["p"], 4),
             "mean_abs": R(sum(rs) / len(rs)), "base_abs": R(sum(base_abs) / len(base_abs)) if base_abs else None, "abs_diff_t": R(wt["t"], 3), "abs_diff_p": R(wt["p"], 4),
             "up_share": R(sum(1 for x in rr if x > 0) / len(rr), 4), "first": rows[0][0], "last": rows[-1][0]}
        if base_abs:
            s["abs_ratio"] = R(s["mean_abs"] / s["base_abs"], 3)
        if len(ab) >= 2:
            ma = Q.mean_test(ab)
            s.update({"mean_abn": R(ma["mean"]), "abn_t": R(ma["t"], 3), "abn_p": R(ma["p"], 4), "sd_abn": R(ma["sd"])})
        s["rows"] = rows
        out[key] = s
    out["cols"] = ["release", "day0", "ret", "spy", "abn", "beta"]
    return out


# ---------------------------------------------------------------- seasonality
def _sig(p, n, minn, tests=1):
    if p is None or n < minn:
        return "too few"
    if p < 0.05 / tests:
        return "significant"
    if p < 0.05:
        return "nominal"
    return "not significant"


def seasonality(H, ctx):
    days, r, tr = H["d"], H["r"], H["tr"]
    L = len(days)
    out = {}
    me = Q.month_end_returns(tr, days)
    months = []
    for m in range(1, 13):
        a = [x for k, x in me if int(k[5:7]) == m]
        rest = [x for k, x in me if int(k[5:7]) != m]
        mt = Q.mean_test(a)
        w = Q.welch(a, rest)
        months.append({"m": m, "n": len(a), "mean": R(mt["mean"]), "median": R(Q.percentile(sorted(a), 50)) if a else None, "hit": R(sum(1 for x in a if x > 0) / len(a), 4) if a else None,
                       "t": R(mt["t"], 3), "p": R(mt["p"], 4), "vs_rest": R(w["diff"]), "vs_rest_t": R(w["t"], 3), "vs_rest_p": R(w["p"], 4),
                       "sig": _sig(w["p"], len(a), 8, 12)})
    out["month"] = months
    out["month_rows"] = [[k, R(x)] for k, x in me]
    dows = []
    allr = [(i, r[i]) for i in range(1, L) if r[i] is not None]
    for wd in range(5):
        a = [x for i, x in allr if dt.date.fromisoformat(days[i]).weekday() == wd]
        rest = [x for i, x in allr if dt.date.fromisoformat(days[i]).weekday() != wd]
        mt = Q.mean_test(a)
        w = Q.welch(a, rest)
        dows.append({"wd": wd, "n": len(a), "mean": R(mt["mean"]), "hit": R(sum(1 for x in a if x > 0) / len(a), 4) if a else None, "t": R(mt["t"], 3), "p": R(mt["p"], 4),
                     "vs_rest": R(w["diff"]), "vs_rest_t": R(w["t"], 3), "vs_rest_p": R(w["p"], 4), "sig": _sig(w["p"], len(a), 30, 5)})
    out["dow"] = dows
    # turn of month: last trading day of a month and the first three of the next
    tom = set()
    for i in range(L - 1):
        if days[i + 1][:7] != days[i][:7]:
            tom.update(j for j in (i, i + 1, i + 2, i + 3) if j < L)
    a = [x for i, x in allr if i in tom]
    b = [x for i, x in allr if i not in tom]
    w = Q.welch(a, b)
    out["tom"] = {"n": len(a), "n_other": len(b), "mean": R(Q.mean(a)), "mean_other": R(Q.mean(b)), "diff": R(w["diff"]), "t": R(w["t"], 3), "p": R(w["p"], 4),
                  "hit": R(sum(1 for x in a if x > 0) / len(a), 4) if a else None, "hit_other": R(sum(1 for x in b if x > 0) / len(b), 4) if b else None, "sig": _sig(w["p"], len(a), 30)}
    for name, dset in (("pre_holiday", ctx.pre_hol_d), ("post_holiday", ctx.post_hol_d)):
        a = [x for i, x in allr if days[i] in dset]
        b = [x for i, x in allr if days[i] not in dset and days[i] not in ctx.pre_hol_d and days[i] not in ctx.post_hol_d]
        w = Q.welch(a, b)
        out[name] = {"n": len(a), "n_other": len(b), "mean": R(Q.mean(a)), "mean_other": R(Q.mean(b)), "diff": R(w["diff"]), "t": R(w["t"], 3), "p": R(w["p"], 4),
                     "hit": R(sum(1 for x in a if x > 0) / len(a), 4) if a else None, "sig": _sig(w["p"], len(a), 15), "dates": [days[i] for i, _ in allr if days[i] in dset][-12:]}
    yrs = {}
    for i in range(L):
        y = days[i][:4]
        yrs.setdefault(y, [i, i])[1] = i
    years = []
    prev = None
    for y in sorted(yrs):
        a, b = yrs[y]
        start = prev if prev is not None else a
        years.append([y, R(tr[b] / tr[start] - 1), prev is None, days[b]])
        prev = b
    out["years"] = years
    return out


# ---------------------------------------------------------------- regimes & backtests
def regimes(H):
    c, days = H["c"], H["d"]
    L = len(c)
    s50, s200 = Q.sma(c, 50), Q.sma(c, 200)
    v20 = Q.rolling_sd(H["lr"], 20, math.sqrt(252))
    trend = [None] * L
    volr = [None] * L
    vpct = [None] * L
    for i in range(L):
        if s200[i] is not None:
            trend[i] = "up" if c[i] > s200[i] and s50[i] > s200[i] else "down" if c[i] < s200[i] and s50[i] < s200[i] else "transition"
    win = []   # sorted values of v20 over the trailing 756 positions
    for i in range(L):
        if i >= 756 and v20[i - 756] is not None:
            del win[bisect.bisect_left(win, v20[i - 756])]
        if v20[i] is None:
            continue
        bisect.insort(win, v20[i])
        if len(win) < 252:
            continue
        p = bisect.bisect_right(win, v20[i]) / len(win)
        vpct[i] = p
        volr[i] = "low" if p < 1 / 3 else "high" if p > 2 / 3 else "normal"
    tr = H["tr"]
    table = {}
    for i in range(L - 20):
        if trend[i] is None or volr[i] is None:
            continue
        f = tr[i + 20] / tr[i] - 1
        for key in (f"{trend[i]}|{volr[i]}", f"{trend[i]}|*", f"*|{volr[i]}", "*|*"):
            t = table.setdefault(key, [0, 0.0, 0])
            t[0] += 1; t[1] += f; t[2] += f > 0
    tot = table.get("*|*", [0])[0]
    rows = [{"key": k, "days": v[0], "share": R(v[0] / tot, 4) if tot else None, "fwd20": R(v[1] / v[0]) if v[0] else None, "hit": R(v[2] / v[0], 4) if v[0] else None} for k, v in sorted(table.items())]

    def runs(lab):
        seg = []
        for i in range(L):
            if lab[i] is None:
                continue
            if seg and seg[-1][2] == lab[i] and seg[-1][3] == i - 1:
                seg[-1][1], seg[-1][3] = days[i], i
            else:
                seg.append([days[i], days[i], lab[i], i])
        return [s[:3] for s in seg]
    cur = {"trend": trend[-1], "vol": volr[-1], "vol_pct": R(vpct[-1], 4), "v20": R(v20[-1], 4), "sma50": R(s50[-1], 4), "sma200": R(s200[-1], 4), "close": c[-1]}
    ts, vs = runs(trend), runs(volr)
    cur["trend_since"] = ts[-1][0] if ts else None
    cur["vol_since"] = vs[-1][0] if vs else None
    cut = days[max(0, L - 2520)]
    return {"current": cur, "table": rows, "trend_runs": [s for s in ts if s[1] >= cut], "vol_runs": [s for s in vs if s[1] >= cut], "first": days[next((i for i in range(L) if trend[i]), L - 1)]}


BT_START = 200


def backtests(H, rf):
    c, hi, lo, days, r = H["c"], H["h"], H["l"], H["d"], H["r"]
    L = len(c)
    if L < BT_START + 60:
        return None
    s50, s200 = Q.sma(c, 50), Q.sma(c, 200)
    rsi = Q.rsi_wilder(c, 14)
    rules = {}
    pos = [0] * L
    for i in range(L):
        pos[i] = 1 if s50[i] is not None and s200[i] is not None and s50[i] > s200[i] else 0
    rules["sma_cross"] = pos
    pos, st = [0] * L, 0
    for i in range(L):
        if rsi[i] is not None:
            if st == 0 and rsi[i] < 30:
                st = 1
            elif st == 1 and rsi[i] > 50:
                st = 0
        pos[i] = st
    rules["rsi_mr"] = pos
    pos, st = [0] * L, 0
    for i in range(20, L):
        if st == 0 and c[i] > max(hi[i - 20:i]):
            st = 1
        elif st == 1 and c[i] < min(lo[i - 10:i]):
            st = 0
        pos[i] = st
    rules["breakout"] = pos
    rules["bh"] = [1] * L
    s0 = BT_START - 1
    out = {"start": days[s0], "end": days[-1], "days": L - 1 - s0, "rules": {}}
    idx = sample_idx(L, s0)
    out["d"] = [days[i] for i in idx]
    rfz = [x if x is not None else 0.0 for x in rf]
    bh_curve = None
    for name, p in rules.items():
        eq, e = [1.0] * L, 1.0
        rets = []
        for i in range(s0 + 1, L):
            x = (p[i - 1] * (r[i] or 0.0)) + (1 - p[i - 1]) * rfz[i]
            e *= 1 + x
            eq[i] = e
            rets.append(x)
        n = len(rets)
        cagr = e ** (252 / n) - 1 if n else None
        vol = Q.sd(rets) * math.sqrt(252) if n > 2 else None
        ex = [x - rfz[i] for x, i in zip(rets, range(s0 + 1, L))]
        sdx = Q.sd(ex)
        sharpe = Q.mean(ex) / sdx * math.sqrt(252) if sdx else None
        peak, mdd = 1.0, 0.0
        for i in range(s0, L):
            peak = max(peak, eq[i])
            mdd = min(mdd, eq[i] / peak - 1)
        trades, entry = [], (s0 if p[s0] == 1 else None)
        changes = 0
        for i in range(s0 + 1, L):
            prev = p[i - 1]
            if p[i] != prev:
                changes += 1
            if p[i] == 1 and prev == 0:
                entry = i
            elif p[i] == 0 and prev == 1 and entry is not None:
                trades.append((entry, i, eq[i] / eq[entry] - 1))
                entry = None
        open_trade = None
        if entry is not None:
            open_trade = {"entry": days[entry], "ret": R(eq[-1] / eq[entry] - 1), "days": L - 1 - entry}
        yrs = n / 252
        exposure = sum(p[s0:L - 1]) / (L - 1 - s0)
        hold = [b - a for a, b, _ in trades]
        out["rules"][name] = {"cagr": R(cagr, 4), "vol": R(vol, 4), "sharpe": R(sharpe, 3), "maxdd": R(mdd, 4), "total": R(e - 1, 4), "exposure": R(exposure, 4),
                              "trades": len(trades), "hit": R(sum(1 for t in trades if t[2] > 0) / len(trades), 4) if trades else None,
                              "avg_trade": R(sum(t[2] for t in trades) / len(trades), 4) if trades else None, "avg_hold": R(sum(hold) / len(hold), 1) if hold else None,
                              "trades_per_year": R(len(trades) / yrs, 2) if yrs else None, "turnover": R(changes / yrs, 2) if yrs else None, "in_market": p[-1] == 1,
                              "open": open_trade, "last_trades": [[days[a], days[b], R(x, 4)] for a, b, x in trades[-12:]], "curve": [R(eq[i], 4) for i in idx]}
        if name == "bh":
            bh_curve = out["rules"][name]
    for name, x in out["rules"].items():
        if name != "bh" and bh_curve:
            x["vs_bh_cagr"] = R(x["cagr"] - bh_curve["cagr"], 4) if x["cagr"] is not None and bh_curve["cagr"] is not None else None
    return out


# ---------------------------------------------------------------- per symbol
def cross_inputs(H, ctx, corr_out, stats):
    tr, days = H["tr"], H["d"]
    L = len(tr)

    def ret(n):
        return tr[-1] / tr[-1 - n] - 1 if L > n else None
    rv = Q.rolling_sd(H["lr"][-61:], 60, math.sqrt(252))[-1] if L > 61 else None
    spy = corr_out["bench"].get("SPY") or {}
    hi = max(H["h"][-252:]) if L >= 20 else None
    out = {"as_of": days[-1], "r21": R(ret(21)), "r63": R(ret(63)), "r126": R(ret(126)), "r252": R(ret(252)), "mom12_1": R(tr[-22] / tr[-253] - 1) if L > 253 else None,
           "vol60": R(rv, 4), "beta": spy.get("b252"), "corr": spy.get("c252"), "from_hi": R(H["c"][-1] / hi - 1) if hi else None, "sharpe1y": (stats.get("perf", {}).get("1y") or {}).get("sharpe"),
           "maxdd": stats.get("dd", {}).get("top", [{}])[0].get("depth") if stats.get("dd", {}).get("top") else None, "bars": L}
    rets = []
    cd = ctx.cross_days
    for k in range(1, len(cd)):
        i = H["idx"].get(cd[k])
        rets.append(R(H["r"][i], 6) if i is not None and i >= 1 and days[i - 1] == cd[k - 1] else None)
    return out, rets


def analyze(sb, sym, ctx, H=None):
    t0 = time.time()
    H = H or load_bars(sb, sym)
    if H and ctx.as_of and H["d"] and H["d"][-1] > ctx.as_of:
        # align every symbol to the market as-of date (SPY's latest stored bar) so benchmarks and the cross-section
        # compare the same days; a prices/ file can be a day ahead of the long-history files between jobs
        k = bisect.bisect_right(H["d"], ctx.as_of)
        for f in ("d", "o", "h", "l", "c", "v"):
            H[f] = H[f][:k]
        H["div"] = [x for x in H["div"] if x[0] <= ctx.as_of]
        H["aligned"] = True
    if not H or len(H["d"]) < 80:
        return None, None
    H["sym"] = sym
    prep(H)
    info = BY_TICKER.get(sym)
    sector = info["sector"] if info else None
    benches = {}
    if sym != "SPY":
        benches["SPY"] = ctx.H["SPY"]
    elif ctx.H.get("QQQ"):
        pass
    se = SECTOR_ETF.get(sector) if sector else None
    if se and se != sym and se in ctx.H:
        benches[se] = ctx.H[se]
    rf = daily_rf(H["d"], ctx.rf)
    stats = statistics(H, ctx, rf, benches)
    corr_out = correlation(H, ctx, benches)
    ev = None
    if info:
        rows, src = filings_rows(sb, sym, ctx.recent.get(sym, set()) if ctx.recent is not None else None, ctx.fstats)
        ev = events(H, ctx, rows, src, benches)
    else:
        rb = bench_returns(H, ctx.H["SPY"]) if sym != "SPY" else [None] + [x for x in H["r"][1:]]
        ev = {"macro": macro_events(H, ctx, rb), "note": "filing events apply to companies only"}
    doc = {"v": VERSION, "sym": sym, "name": info["name"] if info else sym, "kind": "stock" if info else "etf", "sector": sector, "sector_etf": se, "benchmarks": list(benches),
           "as_of": H["d"][-1], "first": H["d"][0], "bars": len(H["d"]), "history": "long" if H["long"] else f"from the prices job file ({len(H['d'])} bars since {H['d'][0]}); the 2008+ history file is not stored for this symbol yet",
           "generated": now_iso(), "bars_file": H["file"], "bars_source": (H["meta"] or {}).get("source") or "Yahoo Finance chart API, split-adjusted daily bars",
           "rf_source": RF_SOURCE, "stats": stats, "corr": corr_out, "events": ev, "season": seasonality(H, ctx), "regimes": regimes(H), "backtests": backtests(H, rf)}
    cx, rets = cross_inputs(H, ctx, corr_out, stats)
    cx.update({"sym": sym, "sector": sector, "kind": doc["kind"], "long": H["long"]})
    if info:
        e = (ev or {}).get("earnings", {}).get("stats", {})
        cx["earn_abs"] = e.get("mean_abs_abn")
        cx["earn_n"] = e.get("n")
    for bs, b in corr_out["bench"].items():
        if bs != "SPY":
            cx["beta_sector"] = b.get("b252")
            cx["corr_sector"] = b.get("c252")
            B = benches[bs]
            for n in (63, 252):
                j1, j0 = B["idx"].get(H["d"][-1]), B["idx"].get(H["d"][-1 - n]) if len(H["d"]) > n else None
                if j1 is not None and j0 is not None:
                    cx[f"rs_sector{n}"] = R((H["tr"][-1] / H["tr"][-1 - n]) / (B["tr"][j1] / B["tr"][j0]) - 1)
    S = ctx.H["SPY"]
    for n in (63, 252):
        if len(H["d"]) > n:
            j1, j0 = S["idx"].get(H["d"][-1]), S["idx"].get(H["d"][-1 - n])
            if j1 is not None and j0 is not None:
                cx[f"rs_spy{n}"] = R((H["tr"][-1] / H["tr"][-1 - n]) / (S["tr"][j1] / S["tr"][j0]) - 1)
    reg = doc["regimes"]["current"]
    cx["trend"], cx["volreg"] = reg.get("trend"), reg.get("vol")
    cx["hurst"] = (stats.get("hurst") or {}).get("h")
    f3 = ((corr_out.get("factors") or {}).get("windows") or {}).get("3y")
    if f3:
        cx["ff_r2"] = f3["r2"]
        cx["ff_mkt"] = f3["coef"]["mkt_rf"]
    doc["compute_ms"] = int((time.time() - t0) * 1000)
    return doc, (cx, rets)


# ---------------------------------------------------------------- jobs
def refresh_filings(sb, tickers, deadline=None, log=print, force=False):
    """Fetches EDGAR filings (with acceptance times) for tickers that have none cached, plus tickers whose recent filings
    in the filings table are missing from the cache (a cheap 'recent' request only)."""
    from .quant_sources import filings_doc
    cut = (dt.date.today() - dt.timedelta(days=12)).isoformat()
    recent = {}
    try:
        tks = ",".join(f'"{t}"' for t in tickers)
        for r in sb.select("filings", f"select=ticker,accession&filed=gte.{cut}&form=in.(\"8-K\",\"10-Q\",\"10-K\",\"8-K/A\",\"10-Q/A\",\"10-K/A\")&ticker=in.({tks})"):
            recent.setdefault(r["ticker"], set()).add(r["accession"])
    except Exception as e:
        log("filings table read failed: " + str(e)[:160])
    cached = list_names(sb, "quant/filings/")
    if cached is not None and not force:
        tickers = [t for t in tickers if f"{t}.json" not in cached or t in recent]
    done, failed, pending = 0, {}, 0
    for tk in tickers:
        if deadline and time.time() > deadline:
            pending += 1
            continue
        try:
            cur = sb.get_json(f"quant/filings/{tk}.json")
        except Exception:
            cur = None
        have = {r[2] for r in (cur or {}).get("rows", [])}
        if cur and not force and not (recent.get(tk, set()) - have):
            continue
        try:
            info = BY_TICKER[tk]
            doc = filings_doc(tk, info["cik"], PREDECESSORS.get(tk, []), full=not cur or force, prior=cur)
            sb.put_json(f"quant/filings/{tk}.json", doc, cache="max-age=3600")
            done += 1
        except Exception as e:
            failed[tk] = str(e)[:160]
            log(f"filings {tk}: {failed[tk]}")
    if pending:
        log(f"filings: {pending} left for the next run (time budget)")
    return done, failed


def list_names(sb, prefix):
    """Object names directly under a storage prefix of the public bucket (None when listing fails)."""
    from .common import request
    names, off = set(), 0
    try:
        while True:
            page = json.loads(request(f"{sb.url}/storage/v1/object/list/public-data", method="POST", data={"prefix": prefix, "limit": 1000, "offset": off},
                                      headers=sb._h({"Content-Type": "application/json"}), timeout=60, retries=3))
            names.update(o["name"] for o in page)
            if len(page) < 1000:
                return names
            off += 1000
    except Exception:
        return None


def run_part(sb, part=0, parts=2, deadline=None, log=print, symbols=None):
    syms = symbols or all_symbols()[part::parts]
    ctx = Context(sb, log)
    ctx.recent = recent_filings(sb, log=log)
    out = {"part": part, "parts": parts, "generated": now_iso(), "cross_days": ctx.cross_days, "rows": {}, "rets": {}, "nodata": []}
    errors = {}

    def one(s):
        if deadline and time.time() > deadline:
            return s, None, "skipped: deadline"
        try:
            doc, cx = analyze(sb, s, ctx)
            if not doc:
                return s, None, "no bars"
            sb.put_json(qpath(s), doc, cache="max-age=600")
            return s, cx, None
        except Exception as e:
            import traceback
            traceback.print_exc()
            return s, None, f"{type(e).__name__}: {str(e)[:160]}"

    with ThreadPoolExecutor(int(os.environ.get("QUANT_WORKERS", "4"))) as ex:
        for k, (s, cx, err) in enumerate(ex.map(one, syms)):
            if err == "no bars":
                out["nodata"].append(s)
            elif err:
                errors[s] = err
            if cx:
                out["rows"][s], out["rets"][s] = cx
            if (k + 1) % 50 == 0:
                log(f"part {part}: {k + 1}/{len(syms)}")
    # keep yesterday's cross inputs for symbols skipped by the deadline
    try:
        prev = sb.get_json(f"quant/parts/part{part}.json") or {}
        for s in syms:
            if s not in out["rows"] and s in (prev.get("rows") or {}) and prev.get("cross_days") == ctx.cross_days:
                out["rows"][s], out["rets"][s] = prev["rows"][s], prev["rets"][s]
    except Exception:
        pass
    out["errors"] = errors
    out["filings"] = ctx.fstats
    sb.put_json(f"quant/parts/part{part}.json", out, cache="no-store")
    log(f"part {part}/{parts}: {len(out['rows'])} symbols, {len(errors)} errors, no bars {out['nodata']}, filings {ctx.fstats}")
    return out


def catch_up(sb, parts=2, deadline=None, log=print):
    """Recomputes symbols that the part runs skipped (deadline, transient errors) and patches them into the part
    files, so the cross-section stays complete. Symbols without stored bars are left alone."""
    ctx = None
    fixed = 0
    for k in range(parts):
        try:
            p = sb.get_json(f"quant/parts/part{k}.json")
        except Exception:
            p = None
        if not p:
            continue
        syms = all_symbols()[k::parts]
        todo = [s for s in syms if s not in p["rows"] and s not in (p.get("nodata") or [])]
        if not todo:
            continue
        ctx = ctx or Context(sb, log)
        changed = False
        for s in todo:
            if deadline and time.time() > deadline:
                break
            try:
                doc, cx = analyze(sb, s, ctx)
            except Exception as e:
                log(f"catch-up {s}: {str(e)[:160]}")
                continue
            if not doc:
                continue
            sb.put_json(qpath(s), doc, cache="max-age=600")
            p["rows"][s], p["rets"][s] = cx
            p["errors"].pop(s, None)
            fixed += 1
            changed = True
        if changed:
            p["cross_days"] = ctx.cross_days
            sb.put_json(f"quant/parts/part{k}.json", p, cache="no-store")
    return fixed


def _zs(v):
    """z-scored returns (missing values become 0 after centring, which drops them from the dot product)."""
    xs = [x for x in v if x is not None]
    if len(xs) < 200:
        return None
    m = sum(xs) / len(xs)
    s = math.sqrt(sum((x - m) ** 2 for x in xs) / len(xs))
    if s <= 0:
        return None
    return [((x - m) / s) if x is not None else 0.0 for x in v], [1.0 if x is not None else 0.0 for x in v]


def cross(sb, parts=2, log=print, deadline=None):
    import operator
    P = []
    for k in range(parts):
        try:
            p = sb.get_json(f"quant/parts/part{k}.json")
        except Exception:
            p = None
        if p:
            P.append(p)
    if not P:
        raise RuntimeError("no quant part files")
    rows, rets = {}, {}
    for p in P:
        rows.update(p["rows"])
        rets.update(p["rets"])
    try:
        ix = sb.get_json("index.json") or {}
        mcap = {c["ticker"]: c.get("market_cap") for c in ix.get("companies", [])}
    except Exception:
        mcap = {}
    stocks = sorted(s for s, r in rows.items() if r.get("kind") == "stock")
    Z = {}
    for s in stocks:
        z = _zs(rets.get(s) or [])
        if z:
            Z[s] = z
    names = sorted(Z)
    n = len(names)
    T = len(next(iter(Z.values()))[0]) if Z else 0
    C = {}
    t0 = time.time()
    for a in range(n):
        za, ma = Z[names[a]]
        full_a = sum(ma) == T
        for b in range(a + 1, n):
            zb, mb = Z[names[b]]
            if full_a and sum(mb) == T:
                c = sum(map(operator.mul, za, zb)) / T
            else:
                both = [x * y for x, y in zip(ma, mb)]
                k = sum(both)
                if k < 200:
                    continue
                c = Q.corr([za[i] for i in range(T) if both[i]], [zb[i] for i in range(T) if both[i]])
            if c is not None:
                C[(a, b)] = c
    log(f"cross: {n} names, {len(C)} pairs in {time.time() - t0:.1f}s")
    pos = {s: i for i, s in enumerate(names)}

    def cc(x, y):
        if x == y:
            return 1.0
        i, j = pos[x], pos[y]
        return C.get((i, j) if i < j else (j, i))
    # sector ranks
    by_sec = {}
    for s in stocks:
        by_sec.setdefault(rows[s].get("sector"), []).append(s)
    rank_keys = [("mom12_1", True), ("r63", True), ("vol60", False), ("beta", False), ("rs_sector252", True), ("sharpe1y", True)]
    for sec, ss in by_sec.items():
        for k, desc in rank_keys:
            vals = sorted((rows[s][k], s) for s in ss if rows[s].get(k) is not None)
            m = len(vals)
            if desc:
                vals = vals[::-1]
            for i, (_, s) in enumerate(vals):
                rows[s].setdefault("rank", {})[k] = [i + 1, m]
    written, failed = 0, {}
    gen = now_iso()
    as_of = max((r.get("as_of") or "") for r in rows.values())
    peers_docs = {}
    for s in names:
        lst = sorted(((cc(s, o), o) for o in names if o != s and cc(s, o) is not None), reverse=True)
        sec = rows[s].get("sector")
        same = [o for c, o in lst if rows[o].get("sector") == sec][:8]
        mat_names = [s] + same
        mat = [[R(cc(x, y), 3) for y in mat_names] for x in mat_names]
        sec_c = [c for c, o in lst if rows[o].get("sector") == sec]
        peers_docs[s] = {"sym": s, "as_of": as_of, "generated": gen, "window": f"{T} daily total returns ending {as_of}", "n_names": n,
                         "top": [[o, R(c, 4), rows[o].get("sector")] for c, o in lst[:12]], "bottom": [[o, R(c, 4), rows[o].get("sector")] for c, o in lst[-12:][::-1]],
                         "matrix": {"names": mat_names, "c": mat, "basis": "self plus the 8 most correlated names in the same sector"},
                         "sector_median": R(Q.percentile(sorted(sec_c), 50), 4) if sec_c else None, "all_median": R(Q.percentile(sorted(c for c, _ in lst), 50), 4) if lst else None}
        rows[s]["avg_corr"] = R(sum(c for c, _ in lst) / len(lst), 4) if lst else None

    def put(item):
        s, d = item
        try:
            sb.put_json(f"quant/peers/{s}.json", d, cache="max-age=600")
            return s, None
        except Exception as e:
            return s, str(e)[:160]
    with ThreadPoolExecutor(8) as ex:
        for s, err in ex.map(put, peers_docs.items()):
            if err:
                failed[s] = err
            else:
                written += 1
    cols = ["sym", "sector", "kind", "mcap", "r21", "r63", "r126", "r252", "mom12_1", "vol60", "beta", "corr", "beta_sector", "rs_spy63", "rs_spy252", "rs_sector63", "rs_sector252",
            "from_hi", "sharpe1y", "maxdd", "trend", "volreg", "hurst", "ff_r2", "earn_abs", "avg_corr", "long"]
    table = []
    for s, r in sorted(rows.items()):
        r["mcap"] = mcap.get(s)
        table.append([r.get(c) for c in cols])
    ranks = {s: r.get("rank") for s, r in rows.items() if r.get("rank")}
    sb.put_json("quant/cross.json", {"generated": gen, "as_of": as_of, "cols": cols, "rows": table, "ranks": ranks, "rank_keys": [k for k, _ in rank_keys],
                                     "source": "quant/parts (computed from tech/bars daily history; prices/ two-year bars where long history is not stored); market caps from index.json"}, cache="max-age=600")
    return {"names": n, "pairs": len(C), "peers_written": written, "failed": failed, "rows": len(table)}


def refresh_inputs(sb, log=print, max_age_days=7, force=False):
    from . import quant_sources as S
    out = {}
    for path, fn in ((S.FACTORS_PATH, lambda: S.fetch_french()), (S.CAL_PATH, lambda: S.fetch_macro_calendar(sb, log=log))):
        try:
            cur = sb.get_json(path)
        except Exception:
            cur = None
        stamp = (cur or {}).get("retrieved") or (cur or {}).get("generated")
        age = (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))).days if stamp else 999
        if cur and age < max_age_days and not force:
            out[path] = f"fresh ({age}d)"
            continue
        try:
            doc = fn()
            sb.put_json(path, doc, cache="max-age=3600")
            out[path] = "updated"
        except Exception as e:
            out[path] = "failed: " + str(e)[:160]
            log(f"{path}: {out[path]}")
    return out


def write_meta(sb, stats):
    sb.put_json("quant/methods.json", METHODS, cache="max-age=3600")
    sb.put_json("quant/meta.json", {"generated": now_iso(), "version": VERSION, "stats": stats}, cache="max-age=300")


# ---------------------------------------------------------------- AI "Quant read" (optional)
READ_MODELS = ["accounts/fireworks/models/kimi-k3", "accounts/fireworks/models/deepseek-v4p1-flash"]


def read_facts(doc):
    """The numbers the AI read may cite, as display strings keyed by an id the sentence must reference."""
    f = {}
    s, c = doc["stats"], doc["corr"]

    def pct(x, d=1):
        return None if x is None else f"{x * 100:.{d}f}%"
    v = (s.get("vol") or {}).get("60") or {}
    if v:
        f["vol60"] = (pct(v["now"]), "60-day annualized realized volatility")
        f["vol60_pct"] = (f"{round(v['pct'] * 100)}th", "percentile of the 60-day volatility versus its own history")
    b = (c.get("bench") or {}).get("SPY") or {}
    if b.get("b252") is not None:
        f["beta252"] = (f"{b['b252']:.2f}", "252-day beta vs SPY")
        f["corr252"] = (f"{b['c252']:.2f}", "252-day correlation with SPY")
    dd = (s.get("dd") or {}).get("top") or []
    if dd:
        f["maxdd"] = (pct(dd[0]["depth"]), f"maximum drawdown ({dd[0]['peak']} to {dd[0]['trough']})")
    p = (s.get("perf") or {}).get("3y") or (s.get("perf") or {}).get("1y")
    if p and p.get("sharpe") is not None:
        f["sharpe"] = (f"{p['sharpe']:.2f}", "Sharpe ratio (3 years, or 1 year when shorter)")
    e = ((doc.get("events") or {}).get("earnings") or {}).get("stats") or {}
    if e.get("mean_abs_abn") is not None:
        f["earn_abs"] = (pct(e["mean_abs_abn"]), f"average absolute abnormal earnings reaction over {e['n']} events")
    fw = (((c.get("factors") or {}).get("windows") or {}).get("3y")) or {}
    if fw:
        f["ff_r2"] = (f"{fw['r2']:.2f}", "R-squared of the 3-year six-factor regression")
    reg = (doc.get("regimes") or {}).get("current") or {}
    if reg.get("trend"):
        f["trend"] = (reg["trend"], "current trend regime")
        f["volreg"] = (reg.get("vol") or "", "current volatility regime")
    return {k: v for k, v in f.items() if v[0]}


def validate_read(sentences, facts):
    """Every sentence must cite at least one fact id and every number in it must equal a cited fact's display string."""
    ok = []
    for s in sentences:
        refs = [r for r in s.get("refs", []) if r in facts]
        if not refs or not isinstance(s.get("text"), str):
            continue
        allowed = {facts[r][0] for r in refs}
        words = set()
        for r in refs:   # window sizes named in the fact's meaning ("60-day", "3 years") may be repeated
            words.update(re.findall(r"\d+", facts[r][1]))
        nums = re.findall(r"-?\d+(?:\.\d+)?%?(?:th)?", s["text"])
        if all(n in allowed or n.lstrip("-") in allowed or n in words for n in nums):
            ok.append({"text": s["text"].strip(), "refs": refs})
    return ok


def quant_read(doc, api_key=None, request_fn=None):
    """Optional grounded summary through Fireworks (OpenAI-compatible chat completions). Returns None without a key."""
    key = api_key or os.environ.get("FIREWORKS_API_KEY")
    if not key:
        return None
    facts = read_facts(doc)
    if len(facts) < 3:
        return None
    prompt = ("Write 3 or 4 short neutral sentences describing this stock's statistical profile. Use ONLY these facts, quote each number exactly as given, "
              "no advice, no forecasts. Return JSON: {\"sentences\":[{\"text\":...,\"refs\":[fact ids]}]}.\nFacts: " +
              json.dumps({k: {"value": v, "meaning": m} for k, (v, m) in facts.items()}))
    from .common import request
    send = request_fn or (lambda url, body, hdr: request(url, method="POST", data=body, headers=hdr, timeout=90, retries=2))
    for model in READ_MODELS:
        try:
            raw = send("https://api.fireworks.ai/inference/v1/chat/completions",
                       {"model": model, "messages": [{"role": "user", "content": prompt}], "temperature": 0.2, "max_tokens": 600, "response_format": {"type": "json_object"}},
                       {"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
            j = json.loads(raw) if isinstance(raw, str) else raw
            content = j["choices"][0]["message"]["content"]
            m = re.search(r"\{.*\}", content, re.S)
            sents = validate_read(json.loads(m.group(0)).get("sentences", []), facts) if m else []
            if sents:
                return {"model": model, "generated": now_iso(), "as_of": doc["as_of"], "sentences": sents, "facts": {k: list(v) for k, v in facts.items()},
                        "label": "AI-generated from the computed statistics on this page; every number is checked against them"}
        except Exception:
            continue
    return None


def run_reads(sb, symbols, deadline=None, limit=150, log=print):
    if not os.environ.get("FIREWORKS_API_KEY"):
        return {"note": "no FIREWORKS_API_KEY; AI reads not generated"}
    try:
        cur = sb.get_json("quant/read_cursor.json") or {}
    except Exception:
        cur = {}
    k0 = int(cur.get("next", 0)) % max(1, len(symbols))
    order = symbols[k0:] + symbols[:k0]
    done = 0
    for k, s in enumerate(order[:limit]):
        if deadline and time.time() > deadline:
            break
        try:
            doc = sb.get_json(qpath(s))
            rd = quant_read(doc) if doc else None
            if rd:
                sb.put_json(f"quant/read/{s}.json", rd, cache="max-age=3600")
                done += 1
        except Exception as e:
            log(f"read {s}: {str(e)[:120]}")
        cur["next"] = (k0 + k + 1) % len(symbols)
    sb.put_json("quant/read_cursor.json", cur, cache="no-store")
    return {"written": done}


# ---------------------------------------------------------------- methods (tooltips)
METHODS = {
    "returns": "Daily simple returns of the total-return index TR_t = TR_{t-1} x (Close_t + Dividend_t) / Close_{t-1} (cash dividends reinvested at the ex-date close). Bars are split-adjusted daily OHLCV from the Yahoo Finance chart API stored in tech/bars (since 2008) or prices/ (two years) for symbols without a long history file.",
    "daily": "Distribution of daily returns over the full stored history: mean, sample SD (n-1), skewness g1 = m3/m2^1.5 and excess kurtosis g2 = m4/m2^2 - 3 (population central moments), percentiles by linear interpolation (type 7).",
    "daily_1y": "Same as the daily distribution, last 252 daily returns.",
    "weekly": "Distribution of weekly returns: total-return index at the last trading day of each ISO week / previous week's - 1, full history.",
    "vol": "Annualized realized volatility = sample SD of the last N daily log returns of the total-return index x sqrt(252), for N = 20, 60 and 252. Percentile = share of all rolling N-day values in the stored history at or below today's.",
    "vov": "Volatility of volatility: SD of the daily 20-day realized volatility over the last 252 days (and relative to its mean); also the annualized SD of daily log changes of the 20-day volatility.",
    "dd": "Drawdown = TR_t / running maximum - 1. Episodes run from a peak to the first day the index regains it; the five deepest are listed with trading days to trough and to recovery.",
    "capture": "Up/down capture vs the benchmark from month-end total returns: geometric mean of the asset's returns in months the benchmark rose (fell) / the benchmark's geometric mean in those months. Windows: last 36 months, 60 months, full history.",
    "perf": "Sharpe = mean(r - rf) / SD(r - rf) x sqrt(252); Sortino = mean(r - rf) x 252 / (sqrt(mean(min(r - rf, 0)^2)) x sqrt(252)). r = daily total return, rf = daily 13-week Treasury bill return (yield / 100 / 252, U.S. Treasury daily bill rates, coupon equivalent). Annual return is geometric.",
    "var": "Historical VaR at 95% / 99% = minus the 5th / 1st percentile of daily returns (type 7). CVaR (expected shortfall) = minus the mean of the returns at or below that percentile. Windows: last 252 days and full history. One-day horizon, no scaling.",
    "acf": "Autocorrelation of daily returns at lags 1-10 over the last five years (1,260 returns, or all when shorter): rho_k = sum (r_t - m)(r_{t-k} - m) / sum (r_t - m)^2. Band = +/- 1.96 / sqrt(n). Ljung-Box Q(10) = n(n+2) sum rho_k^2 / (n-k), p-value from chi-square with 10 degrees of freedom.",
    "vr": "Lo-MacKinlay variance ratio of daily log returns (last five years) for q = 2, 5, 10, 20 days with overlapping q-day sums: VR(q) = Var(q-day) / (q x Var(1-day)). z* is the heteroskedasticity-robust statistic; VR > 1 suggests trending (positive autocorrelation), VR < 1 mean reversion; |z*| < 1.96 is consistent with a random walk.",
    "hurst": "Hurst exponent by classical rescaled range (R/S) on daily log returns, full history: for 12 log-spaced window sizes from 10 days to a quarter of the sample, average R/S over non-overlapping blocks; H = slope of log(R/S) on log(window). About 0.5 = no long memory; R/S is biased upward in short samples (no Anis-Lloyd correction applied).",
    "bench": "Rolling Pearson correlation and OLS beta (cov / var of benchmark) of daily total returns over 63 and 252 trading days, dates matched exactly; a window needs 80% of its days present. Benchmarks: SPY and the company's sector SPDR ETF. Up/down: correlation and beta on the days of the last 252 when the benchmark rose / fell.",
    "macro": "Correlation and beta of daily total returns with daily changes of macro series we store in tech/bars: 10-year Treasury yield (^TNX, change in percentage points, so beta is return per 1 pp), US dollar (UUP), WTI crude (CL=F) and gold (GC=F) front-month futures and the VIX (daily percent changes). 252-day window and full history; rolling 252-day correlation sampled every 21 days.",
    "factors": "OLS of daily excess return (r - RF) on the Fama-French five factors (Mkt-RF, SMB, HML, RMW, CMA) and momentum (Mom), Kenneth R. French Data Library daily files, cached in quant/factors/ff_daily.json. t-statistics use Newey-West standard errors with 5 lags (Bartlett weights); classical OLS t also stored. Alpha annualized x 252. Windows: last 252 and 756 overlapping days and full history; rolling 252-day betas every 21 days. Factor data lag our prices by up to about two months.",
    "earnings": "Earnings reactions. Events: 8-K filings reporting Item 2.02 (results of operations); 10-Q/10-K filings where no Item 2.02 8-K was filed in the prior 50 days. Reaction day from the EDGAR acceptance time converted to US Eastern: before 09:30 = that day, 09:30-16:00 = that day (intraday release), 16:00 or later = next trading day; without an acceptance time the filing day and the next day (2-day window). Move = close before the window to close at its end (total return); gap = open of the reaction day / prior close - 1; abnormal = sum over the window of (r - beta x r_SPY), beta from trading days -260 to -11 (min 120 days, else 1); sector = r - r_sectorETF; sigma = |abnormal| / (SD of estimation residuals x sqrt(window)). Drift = cumulative abnormal return over the 5 / 20 trading days after the window; hit = share of positive drifts; continuation = share whose drift has the same sign as the reaction; signed drift = drift x sign(reaction).",
    "material": "Reactions to 8-K filings without Item 2.02, grouped by item code, measured like earnings reactions (same reaction-day rule and abnormal return vs SPY). Ratio = mean absolute abnormal reaction / mean absolute abnormal daily return over the last five years.",
    "macro_events": "Same-day total return on the first trading day on or after each release: CPI and Employment Situation dates from the BLS news release archive (8:30 a.m. ET, so the day's open-to-close and close-to-close both include the news), FOMC statement dates from the Federal Reserve FOMC calendars (2:00 p.m. ET). Abnormal = r - beta x r_SPY with the 252-day beta as of the prior day. Absolute-move ratio and its Welch t test compare event days with all other days (days of any listed release excluded).",
    "season": "Month of year: month-end to month-end total returns grouped by calendar month; t = mean / (SD / sqrt(n)) against zero, and a Welch test against all other months. Day of week: daily returns by weekday, Welch test vs other weekdays. Turn of month: last trading day of a month and the first three of the next vs all other days. Pre/post holiday: trading days immediately before / after a weekday on which the market was closed (from SPY's trading calendar) vs ordinary days. 'Significant' requires p < 0.05 after a Bonferroni adjustment for the number of comparisons (12 months, 5 weekdays); 'nominal' = p < 0.05 unadjusted only; small samples are labelled too few.",
    "regimes": "Trend regime (price closes): up = close > 200-day SMA and 50-day SMA > 200-day SMA; down = close < 200-day SMA and 50-day < 200-day; otherwise transition. Volatility regime: today's 20-day realized volatility ranked against the trailing 756 days (at least 252): low = bottom third, high = top third, else normal. Only past data enter each day's label. The table gives the share of days and the average forward 20-day total return in each regime (overlapping windows, in-sample, descriptive).",
    "backtests": "Historical simulation before costs, taxes and slippage; not advice. Signals use the close of day t and the position applies to day t+1's total return; cash earns the 13-week T-bill. Rules: 50/200 cross = long while the 50-day SMA is above the 200-day SMA; RSI(14) mean reversion = buy when Wilder RSI(14) closes below 30, sell when it closes above 50; 20-day breakout = buy when the close exceeds the highest high of the prior 20 days, sell when it falls below the lowest low of the prior 10 days; buy and hold. All start on the 200th bar. CAGR geometric; Sharpe on excess over the T-bill; hit rate = share of closed trades with a positive return; exposure = share of days invested; turnover = position changes per year.",
    "cross": "Cross-section of covered companies on the same as-of date: total returns over 21/63/126/252 days, 12-1 momentum = return from day -252 to day -21, 60-day realized volatility, 252-day beta and correlation vs SPY, relative strength = (1 + own return) / (1 + benchmark return) - 1. Sector ranks: 1 = highest momentum / return / Sharpe, lowest volatility / beta.",
    "peers": "Pearson correlation of the last 252 daily total returns between every pair of covered companies (pairs need 200 common days). Matrix: the symbol plus its eight most correlated names in the same sector.",
    "rf": RF_SOURCE,
}


def quant_job(sb, part=None, parts=2, step=None, time_budget=None, log=print):
    from .common import AggregateLease, LeaseBusy, Run
    deadline = time.time() + time_budget if time_budget else None
    with Run(sb, "quant") as run:
        if step == "cross":
            with AggregateLease(sb, name="quant-cross", seconds=600):
                run.stats["inputs"] = refresh_inputs(sb, log=log)
                run.stats["catch_up"] = catch_up(sb, parts, deadline=(time.time() + (time_budget or 600) * 0.5), log=log)
                run.stats["cross"] = cross(sb, parts, log=log)
                run.stats["reads"] = run_reads(sb, [t for t, *_ in UNIVERSE], deadline=deadline, log=log)
                write_meta(sb, run.stats)
            return run.stats
        if part is not None:
            try:
                with AggregateLease(sb, name=f"quant-part{part}", seconds=600):
                    try:
                        if not sb.get_json("quant/factors/ff_daily.json") or not sb.get_json("quant/calendar/macro.json"):
                            run.stats["inputs"] = refresh_inputs(sb, log=log)
                    except Exception as e:
                        run.stats["inputs"] = str(e)[:160]
                    out = run_part(sb, part, parts, deadline=deadline, log=log)
            except LeaseBusy:
                run.stats["skipped"] = 1
                return run.stats
            run.stats.update({"part": part, "symbols": len(out["rows"]), "errors": out["errors"], "no_bars": out["nodata"], "filings": out["filings"]})
            return run.stats
        run.stats["inputs"] = refresh_inputs(sb, log=log)
        for k in range(parts):
            out = run_part(sb, k, parts, log=log)
            run.stats[f"p{k}"] = {"symbols": len(out["rows"]), "errors": len(out["errors"])}
        run.stats["cross"] = cross(sb, parts, log=log)
        write_meta(sb, run.stats)
        return run.stats
