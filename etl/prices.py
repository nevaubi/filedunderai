"""Daily and intraday bars from the Yahoo Finance chart API, plus price statistics."""
import datetime as dt
import math
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

from .common import get_json, HTTPError


def path_key(sym):
    """Storage-safe file name for a symbol (^VIX -> IDX_VIX, CL=F -> CL_F)."""
    return sym.replace("^", "IDX_").replace("=", "_")


def yahoo_symbol(t):
    return t.replace(".", "-")


def chart(sym, rng="2y", interval="1d"):
    url = f"https://query2.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(yahoo_symbol(sym))}?range={rng}&interval={interval}&includePrePost=false"
    for attempt in range(4):
        try:
            j = get_json(url, timeout=30, retries=1)
            break
        except HTTPError as e:
            if e.code == 429 and attempt < 3:
                time.sleep(4 * (attempt + 1))
                continue
            raise
    r = j["chart"]["result"][0]
    q = r["indicators"]["quote"][0]
    bars = []
    for i, t in enumerate(r.get("timestamp") or []):
        c = q["close"][i]
        if c is None:
            continue
        o, h, l = q["open"][i] or c, q["high"][i] or c, q["low"][i] or c
        bars.append([t, round(o, 4), round(h, 4), round(l, 4), round(c, 4), q["volume"][i] or 0])
    m = r["meta"]
    meta = {k: m.get(k) for k in ("symbol", "longName", "shortName", "currency", "exchangeName", "regularMarketPrice", "fiftyTwoWeekHigh", "fiftyTwoWeekLow", "gmtoffset")}
    return {"meta": meta, "bars": bars}


def fetch_many(symbols, rng="2y", interval="1d", workers=3, pause=0.35, deadline=None):
    """Fetches bars for many symbols. With a deadline (epoch seconds) remaining symbols are skipped once it passes,
    so a scheduled run finishes inside its time limit; skipped symbols keep yesterday's files."""
    out, errors = {}, {}

    def one(s):
        if deadline and time.time() > deadline:
            return s, None, "skipped: deadline"
        time.sleep(pause)
        try:
            return s, chart(s, rng, interval), None
        except Exception as e:
            return s, None, str(e)[:200]

    with ThreadPoolExecutor(workers) as ex:
        for s, d, err in ex.map(one, symbols):
            if d and d["bars"]:
                out[s] = d
            else:
                errors[s] = err or "no bars"
    # rate-limited symbols get a slower second pass
    retry = [s for s, e in errors.items() if "429" in e]
    if retry and not (deadline and time.time() + 25 > deadline):
        time.sleep(20)
        for s in retry:
            if deadline and time.time() > deadline:
                break
            time.sleep(2.5)
            s_, d, err = one(s)
            if d and d["bars"]:
                out[s] = d
                errors.pop(s, None)
            else:
                errors[s] = err or "no bars"
    return out, errors


def _rets(c):
    return [c[i] / c[i - 1] - 1 for i in range(1, len(c))]


def _sd(a):
    if len(a) < 2:
        return None
    m = sum(a) / len(a)
    return math.sqrt(sum((x - m) ** 2 for x in a) / (len(a) - 1))


def stats(bars, spy_bars=None):
    if not bars:
        return {}
    c = [b[4] for b in bars]
    last_t = bars[-1][0]

    def ret_days(n):
        t0 = last_t - n * 86400
        for b in bars:
            if b[0] >= t0:
                return c[-1] / b[4] - 1
        return None

    y = dt.datetime.fromtimestamp(last_t, dt.timezone.utc).year
    prev_year = [b for b in bars if dt.datetime.fromtimestamp(b[0], dt.timezone.utc).year < y]
    y1 = [b for b in bars if b[0] >= last_t - 365 * 86400]
    r = _rets(c)
    beta = None
    if spy_bars:
        sm = {dt.datetime.fromtimestamp(b[0], dt.timezone.utc).date(): b[4] for b in spy_bars}
        pts = [(b[4], sm.get(dt.datetime.fromtimestamp(b[0], dt.timezone.utc).date())) for b in bars]
        pts = [p for p in pts if p[1]][-253:]
        ra = [pts[i][0] / pts[i - 1][0] - 1 for i in range(1, len(pts))]
        rb = [pts[i][1] / pts[i - 1][1] - 1 for i in range(1, len(pts))]
        if len(ra) > 30:
            ma, mb = sum(ra) / len(ra), sum(rb) / len(rb)
            cov = sum((ra[i] - ma) * (rb[i] - mb) for i in range(len(ra)))
            var = sum((x - mb) ** 2 for x in rb)
            beta = cov / var if var else None
    v60 = _sd(r[-60:])
    return {
        "price": c[-1], "as_of": dt.datetime.fromtimestamp(last_t, dt.timezone.utc).date().isoformat(),
        "chg_1d": c[-1] / c[-2] - 1 if len(c) > 1 else None, "ret_1m": ret_days(30), "ret_1y": ret_days(365),
        "ret_ytd": c[-1] / prev_year[-1][4] - 1 if prev_year else None,
        "high_52w": max(b[2] for b in y1), "low_52w": min(b[3] for b in y1),
        "vol_60d": v60 * math.sqrt(252) if v60 else None, "beta_1y": beta,
    }
