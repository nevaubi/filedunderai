"""Long daily history for the Technicals page, plus market-wide technical state, breadth and signal statistics.

History files (public bucket, `tech/bars/{key}.json`) hold split-adjusted daily OHLCV from the Yahoo Finance chart API
back to 2008-01-01 (or the listing date) and the cash dividends Yahoo reports, so total-return series can be rebuilt
without relying on Yahoo's retroactively re-based adjusted close. The daily update appends the bars the prices job
already fetched (`prices/{key}.json`), so it makes no extra Yahoo requests; a split shows up as a jump between the stored
and the freshly split-adjusted bars on overlapping dates and rescales the older history.
"""
import datetime as dt
import json
import time
import urllib.parse

from .common import HTTPError, get_json, now_iso
from .prices import path_key, yahoo_symbol
from .universe import MARKET_SYMBOLS, SECTOR_ETF, UNIVERSE

START = "2008-01-01"
SOURCE = "Yahoo Finance chart API (query1.finance.yahoo.com/v8/finance/chart), split-adjusted OHLCV and cash dividends"


def all_symbols():
    return MARKET_SYMBOLS + [t for t, *_ in UNIVERSE]


def chart_url(sym, period1, period2=None):
    p2 = period2 or int(time.time())
    return (f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(yahoo_symbol(sym))}"
            f"?period1={period1}&period2={p2}&interval=1d&includePrePost=false&events=div,split")


def _epoch(day):
    return int(dt.datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp())


def _day(ts):
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).date().isoformat()


def fetch_history(sym, start=START, pace=None):
    """Daily bars and dividends since `start`. Retries 429s with a long backoff (Yahoo throttles shared addresses)."""
    url = chart_url(sym, _epoch(start))
    j = None
    for attempt in range(7):
        try:
            j = get_json(url, timeout=40, retries=2)
            break
        except HTTPError as e:
            if e.code == 429 and attempt < 6:
                time.sleep((pace or 6) * (attempt + 1))
                continue
            raise
    res = (j.get("chart") or {}).get("result") or []
    if not res:
        raise RuntimeError(f"no chart result for {sym}")
    r = res[0]
    q = r["indicators"]["quote"][0]
    out = {"d": [], "o": [], "h": [], "l": [], "c": [], "v": []}
    seen = set()
    for i, ts in enumerate(r.get("timestamp") or []):
        c = q["close"][i]
        if c is None:
            continue
        day = _day(ts)
        if day in seen:
            continue
        seen.add(day)
        o, h, l = q["open"][i] or c, q["high"][i] or c, q["low"][i] or c
        out["d"].append(day)
        out["o"].append(round(o, 4))
        out["h"].append(round(max(h, o, c), 4))
        out["l"].append(round(min(l, o, c), 4))
        out["c"].append(round(c, 4))
        out["v"].append(int(q["volume"][i] or 0))
    ev = r.get("events") or {}
    divs = sorted([_day(int(k)), round(float(v["amount"]), 6)] for k, v in (ev.get("dividends") or {}).items())
    splits = sorted([_day(int(k)), v.get("splitRatio") or f'{v.get("numerator")}:{v.get("denominator")}'] for k, v in (ev.get("splits") or {}).items())
    m = r.get("meta") or {}
    meta = {"symbol": sym, "currency": m.get("currency"), "exchange": m.get("exchangeName"), "first_trade": _day(m["firstTradeDate"]) if m.get("firstTradeDate") else None,
            "source": SOURCE, "source_url": url, "retrieved": now_iso(), "start": start}
    return {"meta": meta, **out, "div": divs, "splits": splits}


def hist_path(sym):
    return f"tech/bars/{path_key(sym)}.json"


def load_hist(sb, sym):
    try:
        return sb.get_json(hist_path(sym))
    except Exception:
        return None


def save_hist(sb, sym, h):
    sb.put_json(hist_path(sym), h, cache="max-age=600")


def backfill(sb, symbols=None, force=False, pace=2.0, log=print):
    """Full history for every symbol that does not have a history file yet. Resumable: finished symbols are skipped."""
    symbols = symbols or all_symbols()
    done, failed = [], {}
    for k, s in enumerate(symbols):
        if not force:
            cur = load_hist(sb, s)
            if cur and cur.get("d") and cur["meta"].get("start") == START:
                continue
        try:
            h = fetch_history(s, pace=pace * 3)
            if len(h["d"]) < 20:
                raise RuntimeError(f"only {len(h['d'])} bars")
            save_hist(sb, s, h)
            done.append(s)
            log(f"[{k + 1}/{len(symbols)}] {s} {len(h['d'])} bars {h['d'][0]} .. {h['d'][-1]}, {len(h['div'])} dividends")
        except Exception as e:
            failed[s] = str(e)[:200]
            log(f"[{k + 1}/{len(symbols)}] {s} failed: {failed[s]}")
        time.sleep(pace)
    return done, failed


def merge_recent(h, bars, divs=None):
    """Appends newer daily bars ([epoch, o, h, l, c, v] as in prices/{key}.json) to a history. If the stored closes on
    overlapping dates differ from the fresh split-adjusted closes by a stable factor, the older history is rescaled."""
    if not bars:
        return h, None
    fresh = {}
    for b in bars:
        fresh[_day(b[0])] = b
    idx = {d: i for i, d in enumerate(h["d"])}
    ratios = [h["c"][idx[d]] / fresh[d][4] for d in fresh if d in idx and fresh[d][4]]
    note = None
    if len(ratios) >= 5:
        ratios.sort()
        r = ratios[len(ratios) // 2]
        if abs(r - 1) > 0.03 and all(abs(x / r - 1) < 0.01 for x in ratios[len(ratios) // 4: 3 * len(ratios) // 4]):
            for key in ("o", "h", "l", "c"):
                h[key] = [round(v / r, 4) for v in h[key]]
            h["v"] = [int(v * r) for v in h["v"]]
            h["div"] = [[d, round(a / r, 6)] for d, a in h.get("div", [])]
            note = f"rescaled history by 1/{r:.4f} (split)"
    last = h["d"][-1] if h["d"] else ""
    for d in sorted(fresh):
        b = fresh[d]
        if d in idx:
            i = idx[d]
            if d >= (h["d"][-5] if len(h["d"]) >= 5 else ""):
                h["o"][i], h["h"][i], h["l"][i], h["c"][i], h["v"][i] = b[1], b[2], b[3], b[4], int(b[5] or 0)
            continue
        if d <= last:
            continue
        h["d"].append(d)
        h["o"].append(b[1]); h["h"].append(b[2]); h["l"].append(b[3]); h["c"].append(b[4]); h["v"].append(int(b[5] or 0))
    if divs:
        have = {d for d, _ in h.get("div", [])}
        h["div"] = sorted(h.get("div", []) + [[d, a] for d, a in divs if d not in have])
    h["meta"]["updated"] = now_iso()
    return h, note


def total_return_index(h):
    """Close-to-close total return index: dividends reinvested at the ex-date close. Starts at 1."""
    dv = {}
    for d, a in h.get("div", []):
        dv[d] = dv.get(d, 0) + a
    out, acc = [], 1.0
    c = h["c"]
    for i in range(len(c)):
        if i:
            acc *= (c[i] + dv.get(h["d"][i], 0)) / c[i - 1]
        out.append(acc)
    return out
