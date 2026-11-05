"""Long daily history for the Technicals page, plus market-wide technical state, breadth and signal statistics.

History files (public bucket, `tech/bars/{key}.json`) hold split-adjusted daily OHLCV from the Yahoo Finance chart API
back to 2008-01-01 (or the listing date) and the cash dividends Yahoo reports, so total-return series can be rebuilt
without relying on Yahoo's retroactively re-based adjusted close. The daily update appends the bars the prices job
already fetched (`prices/{key}.json`), so it makes no extra Yahoo requests; a split shows up as a jump between the stored
and the freshly split-adjusted bars on overlapping dates and rescales the older history.
"""
import datetime as dt
import math
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


# ---------------------------------------------------------------- filing and insider events per company
EVENT_FORMS = ("8-K", "8-K/A", "10-K", "10-K/A", "10-Q", "10-Q/A")


def _sub_rows(block):
    n = len(block.get("form", []))
    items = block.get("items") or [""] * n
    for i in range(n):
        yield block["form"][i], block["filingDate"][i], block["accessionNumber"][i], block["primaryDocument"][i], items[i] or "", block["reportDate"][i] or ""


def sec_filing_events(cik, since=START):
    """8-K, 10-K and 10-Q filings since `since` from the EDGAR submissions API, including its older pages."""
    from .common import sec_json
    sub = sec_json(f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json")
    rows = list(_sub_rows(sub["filings"]["recent"]))
    for f in sub["filings"].get("files", []):
        if (f.get("filingTo") or "9999") < since:
            continue
        rows += list(_sub_rows(sec_json(f"https://data.sec.gov/submissions/{f['name']}")))
    out = {}
    for form, filed, accn, doc, items, rep in rows:
        if form in EVENT_FORMS and filed >= since:
            out[accn] = [form, filed, accn, doc, items, rep, int(cik)]
    return out


def events_path(tk):
    return f"tech/events/{tk}.json"


def events_backfill(sb, tickers=None, force=False, log=print):
    """EDGAR filing history (8-K with items, 10-K, 10-Q) since 2008 for each company, predecessor registrants included."""
    from .universe import BY_TICKER, PREDECESSORS
    tickers = tickers or [t for t, *_ in UNIVERSE]
    done, failed = 0, {}
    for k, tk in enumerate(tickers):
        if not force:
            try:
                cur = sb.get_json(events_path(tk))
            except Exception:
                cur = None
            if cur and cur.get("since") == START:
                continue
        try:
            ev = sec_filing_events(BY_TICKER[tk]["cik"])
            for pcik, cut in PREDECESSORS.get(tk, []):
                ev.update({a: r for a, r in sec_filing_events(pcik).items() if r[1] <= cut})
            rows = sorted(ev.values(), key=lambda r: (r[1], r[2]))
            sb.put_json(events_path(tk), {"ticker": tk, "since": START, "generated": now_iso(), "source": "SEC EDGAR submissions API (data.sec.gov/submissions)",
                                          "cols": ["form", "filed", "accession", "primary_doc", "items", "report_date", "cik"], "filings": rows, "insiders": []}, cache="max-age=3600")
            done += 1
            log(f"[{k + 1}/{len(tickers)}] {tk} {len(rows)} filings")
        except Exception as e:
            failed[tk] = str(e)[:200]
            log(f"[{k + 1}/{len(tickers)}] {tk} failed {failed[tk]}")
    return done, failed


def refresh_events(sb, tickers, insiders):
    """Adds filings the companies job wrote to the `filings` table in the last 30 days, and replaces the insider rows."""
    from .universe import BY_TICKER
    cut = (dt.date.today() - dt.timedelta(days=30)).isoformat()
    forms = ",".join(f'"{f}"' for f in EVENT_FORMS)
    recent = sb.select("filings", f"select=ticker,form,filed,accession,primary_doc,items,report_date&filed=gte.{cut}&form=in.({forms})")
    by_tk = {}
    for r in recent:
        by_tk.setdefault(r["ticker"], []).append(r)
    n = 0
    for tk in tickers:
        try:
            cur = sb.get_json(events_path(tk))
        except Exception:
            cur = None
        if not cur:
            continue
        have = {r[2] for r in cur["filings"]}
        add = [[r["form"], r["filed"], r["accession"], r["primary_doc"] or "", r["items"] or "", r["report_date"] or "", BY_TICKER[tk]["cik"]] for r in by_tk.get(tk, []) if r["accession"] not in have]
        ins = insiders.get(tk, [])
        if not add and ins == cur.get("insiders"):
            continue
        cur["filings"] = sorted(cur["filings"] + add, key=lambda r: (r[1], r[2]))
        cur["insiders"] = ins
        cur["insider_cols"] = ["tx_date", "code", "shares", "price", "owner", "role", "accession", "plan_10b5", "filed"]
        cur["insider_source"] = "Form 4 filings parsed by Filed Under (insider_transactions), open-market purchases (P) and sales (S) of non-derivative securities"
        cur["generated"] = now_iso()
        sb.put_json(events_path(tk), cur, cache="max-age=3600")
        n += 1
    return n


def insider_trades(sb, tickers=None):
    flt = "&ticker=in.(" + ",".join(f'"{t}"' for t in tickers) + ")" if tickers else ""
    rows = sb.select("insider_transactions", "select=ticker,tx_date,code,shares,price,owner,role,accession,plan_10b5,filed&code=in.(P,S)&derivative=eq.false&order=tx_date.asc,accession.asc,seq.asc" + flt)
    out = {}
    for r in rows:
        if not r.get("tx_date"):
            continue
        out.setdefault(r["ticker"], []).append([r["tx_date"], r["code"], float(r["shares"]) if r["shares"] is not None else None, float(r["price"]) if r["price"] is not None else None,
                                                r["owner"], r["role"], r["accession"], bool(r["plan_10b5"]), r["filed"]])
    return out


# ---------------------------------------------------------------- market-wide computation
HORIZONS = (5, 20, 60)
STAT_START = 252          # first bar used for signals and base rates (after every indicator's warm-up)
BIN_W, BIN_LO, NBINS = 0.001, -0.8, 1600   # histogram for base-rate medians: 0.1 percentage-point bins
PERIODS = (("2008-2016", "2008-01-01", "2016-12-31"), ("2017-latest", "2017-01-01", "9999-12-31"))


def _period(day):
    for name, a, b in PERIODS:
        if a <= day <= b:
            return name
    return None


def analyze_symbol(h, spy_c=None):
    """Signals with forward total returns, base-rate accumulators, breadth contributions and the current state."""
    from . import technicals_calc as C
    o, hi, lo, c, days = h["o"], h["h"], h["l"], h["c"], h["d"]
    L = len(c)
    tr = total_return_index(h)
    ev, ind = C.detect(o, hi, lo, c)
    sig = {}
    for t, i, bars in ev:
        if i < STAT_START:
            continue
        fw = [round(tr[i + k] / tr[i] - 1, 6) if i + k < L else None for k in HORIZONS]
        sig.setdefault(t, []).append([days[i]] + fw)
    base = {}
    for k in HORIZONS:
        b = {p: {"n": 0, "sum": 0.0, "hit": 0} for p in ["all"] + [p[0] for p in PERIODS]}
        hist = [0] * (NBINS + 2)
        for i in range(STAT_START, L - k):
            r = tr[i + k] / tr[i] - 1
            pr = _period(days[i])
            for p in ("all", pr) if pr else ("all",):
                b[p]["n"] += 1
                b[p]["sum"] += r
                b[p]["hit"] += r > 0
            j = int((r - BIN_LO) / BIN_W)
            hist[0 if j < 0 else NBINS + 1 if j >= NBINS else j + 1] += 1
        base[k] = {"acc": b, "hist": hist}
    # breadth: per date flags
    s50, s200 = ind["sma50"], ind["sma200"]
    breadth = {}
    from collections import deque
    dqh, dql = deque(), deque()
    for i in range(1, L):
        j = i - 1
        while dqh and hi[dqh[-1]] < hi[j]:
            dqh.pop()
        dqh.append(j)
        while dql and lo[dql[-1]] > lo[j]:
            dql.pop()
        dql.append(j)
        while dqh[0] < i - 252:
            dqh.popleft()
        while dql[0] < i - 252:
            dql.popleft()
        f = [0] * 9
        if s50[i] is not None:
            f[0], f[1] = 1, int(c[i] > s50[i])
        if s200[i] is not None:
            f[2], f[3] = 1, int(c[i] > s200[i])
        if i >= 252:
            f[4], f[5], f[6] = 1, int(hi[i] > hi[dqh[0]]), int(lo[i] < lo[dql[0]])
        f[7], f[8] = int(c[i] > c[i - 1]), int(c[i] < c[i - 1])
        breadth[days[i]] = f
    # current state
    i = L - 1
    pdi, mdi, ad = C.adx(hi, lo, c, 14)
    v21 = C.realized_vol(c, 21)
    vv = sorted(x for x in v21 if x is not None)
    look = min(252, L)
    hh = max(hi[L - look:])
    ll = min(lo[L - look:])
    sq = 0
    for j in range(i, -1, -1):
        if ind["squeeze"][j]:
            sq += 1
        else:
            break
    def ret(n):
        return tr[i] / tr[i - n] - 1 if i - n >= 0 else None
    trend = None
    if s50[i] is not None and s200[i] is not None:
        trend = "up" if c[i] > s50[i] > s200[i] else "down" if c[i] < s50[i] < s200[i] else "mixed"
    rs63 = None
    if spy_c:
        a = spy_c.get(days[i]), spy_c.get(days[i - 63]) if i >= 63 else None
        if a[0] and a[1]:
            rs63 = (c[i] / c[i - 63]) / (a[0] / a[1]) - 1
    last_ev = [[t, days[k]] for t, k, _ in ev[-3:]]
    state = {"d": days[i], "close": c[i], "trend": trend, "rsi": ind["rsi"][i], "vs50": c[i] / s50[i] - 1 if s50[i] else None, "vs200": c[i] / s200[i] - 1 if s200[i] else None,
             "from_hi": c[i] / hh - 1, "from_lo": c[i] / ll - 1, "squeeze": sq, "adx": ad[i], "st": ind["st"][i], "macd_hist": ind["macd_hist"][i],
             "vol21": v21[i], "vol21_pct": (sum(1 for x in vv if x <= v21[i]) / len(vv)) if v21[i] is not None and vv else None,
             "r21": ret(21), "r63": ret(63), "r252": ret(252), "rs63": rs63, "events": last_ev, "first": days[0], "bars": L}
    return {"signals": sig, "base": base, "breadth": breadth, "state": state}


def _r(x, d=6):
    return None if x is None else round(x, d)


def run_part(sb, part=0, parts=4, update=True, deadline=None, log=print):
    """Updates the histories of a slice of the symbols from the prices job's files and writes the slice's partial results."""
    from concurrent.futures import ThreadPoolExecutor
    syms = all_symbols()[part::parts]
    stocks = {t for t, *_ in UNIVERSE}
    spy = load_hist(sb, "SPY")
    spy_c = dict(zip(spy["d"], spy["c"])) if spy else None
    notes = {}

    def one(s):
        if deadline and time.time() > deadline:
            return s, None, "skipped: deadline"
        h = load_hist(sb, s)
        if not h:
            return s, None, "no history file"
        if update:
            try:
                p = sb.get_json(f"prices/{path_key(s)}.json") or {}
                if p.get("d") and _day(p["d"][-1][0]) >= h["d"][-1]:
                    h, note = merge_recent(h, p["d"], p.get("dv"))
                    save_hist(sb, s, h)
                    if note:
                        notes[s] = note
            except Exception as e:
                notes[s] = "update failed: " + str(e)[:120]
        if s not in stocks or len(h["c"]) < 60:
            return s, None, None
        return s, analyze_symbol(h, spy_c), None

    out = {"part": part, "parts": parts, "generated": now_iso(), "symbols": {}, "base": {}, "breadth": {}, "signals": {}}
    errors = {}
    with ThreadPoolExecutor(6) as ex:
        for s, a, err in ex.map(one, syms):
            if err:
                errors[s] = err
            if not a:
                continue
            out["symbols"][s] = a["state"]
            for t, rows in a["signals"].items():
                out["signals"].setdefault(t, {})[s] = rows
            for k, b in a["base"].items():
                cur = out["base"].setdefault(str(k), {"acc": {}, "hist": [0] * (NBINS + 2)})
                for p, v in b["acc"].items():
                    ca = cur["acc"].setdefault(p, {"n": 0, "sum": 0.0, "hit": 0})
                    ca["n"] += v["n"]; ca["sum"] += v["sum"]; ca["hit"] += v["hit"]
                cur["hist"] = [x + y for x, y in zip(cur["hist"], b["hist"])]
            for d, f in a["breadth"].items():
                cur = out["breadth"].get(d)
                out["breadth"][d] = f if cur is None else [x + y for x, y in zip(cur, f)]
    tks = [s for s in syms if s in stocks]
    try:
        out["events_written"] = refresh_events(sb, tks, insider_trades(sb, tks))
    except Exception as e:
        errors["events"] = str(e)[:200]
    out["errors"], out["notes"] = errors, notes
    sb.put_json(f"meta/tech/part{part}.json", out, cache="no-store")
    log(f"part {part}/{parts}: {len(out['symbols'])} stocks, {len(errors)} errors, {len(notes)} notes")
    return out


def _cluster_ci(vals, clusters):
    """Mean with a 95% interval from a cluster-robust standard error (clusters = calendar months of the signal date),
    so signals that fire together in one market episode are not treated as independent observations."""
    n = len(vals)
    if n < 2:
        return None, None, 0
    m = sum(vals) / n
    g = {}
    for v, k in zip(vals, clusters):
        g[k] = g.get(k, 0.0) + (v - m)
    G = len(g)
    if G < 2:
        return m, None, G
    var = (G / (G - 1)) * sum(x * x for x in g.values()) / (n * n)
    se = math.sqrt(var)
    return m, [m - 1.96 * se, m + 1.96 * se], G


def _hist_median(hist, n):
    if not n:
        return None
    half, acc = n / 2, 0
    for j, x in enumerate(hist):
        acc += x
        if acc >= half:
            return BIN_LO + (j - 0.5) * BIN_W if 0 < j <= NBINS else None
    return None


def merge(sb, parts=4, log=print):
    """Combines the partial results into tech/signals.json, tech/breadth.json and tech/states.json."""
    from .universe import BY_TICKER
    P = [sb.get_json(f"meta/tech/part{k}.json") for k in range(parts)]
    P = [p for p in P if p]
    if not P:
        raise RuntimeError("no partial results")
    base = {}
    for k in HORIZONS:
        acc, hist = {}, [0] * (NBINS + 2)
        for p in P:
            b = p["base"].get(str(k))
            if not b:
                continue
            for name, v in b["acc"].items():
                a = acc.setdefault(name, {"n": 0, "sum": 0.0, "hit": 0})
                a["n"] += v["n"]; a["sum"] += v["sum"]; a["hit"] += v["hit"]
            hist = [x + y for x, y in zip(hist, b["hist"])]
        base[str(k)] = {name: {"n": a["n"], "mean": _r(a["sum"] / a["n"]) if a["n"] else None, "hit": _r(a["hit"] / a["n"], 4) if a["n"] else None} for name, a in acc.items()}
        base[str(k)]["all"]["median"] = _r(_hist_median(hist, acc.get("all", {}).get("n", 0)), 4)
    signals = {}
    types = sorted({t for p in P for t in p["signals"]})
    for t in types:
        rows = [(s, r) for p in P for s, rs in p["signals"].get(t, {}).items() for r in rs]
        out = {"symbols": len({s for s, _ in rows}), "events": len(rows), "first": min(r[1][0] for r in rows), "last": max(r[1][0] for r in rows)}
        for hi_, k in enumerate(HORIZONS):
            vals = [(r[1 + hi_], r[0][:7], s, r[0]) for s, r in rows if r[1 + hi_] is not None]
            if not vals:
                out[str(k)] = {"n": 0}
                continue
            v = [x[0] for x in vals]
            m, ci, G = _cluster_ci(v, [x[1] for x in vals])
            hits = [1.0 if x > 0 else 0.0 for x in v]
            hm, hci, _ = _cluster_ci(hits, [x[1] for x in vals])
            sv = sorted(v)
            med = sv[len(sv) // 2] if len(sv) % 2 else (sv[len(sv) // 2 - 1] + sv[len(sv) // 2]) / 2
            bm = base[str(k)]["all"]["mean"]
            st = {"n": len(v), "months": G, "mean": _r(m), "median": _r(med), "hit": _r(hm, 4), "ci": [_r(x) for x in ci] if ci else None, "hit_ci": [_r(x, 4) for x in hci] if hci else None,
                  "excess": _r(m - bm) if bm is not None else None, "excess_ci": [_r(x - bm) for x in ci] if ci and bm is not None else None}
            by = {}
            for name, a, b in PERIODS:
                pv = [x for x in vals if a <= x[3] <= b]
                if pv:
                    pm, pci, pg = _cluster_ci([x[0] for x in pv], [x[1] for x in pv])
                    bmp = (base[str(k)].get(name) or {}).get("mean")
                    by[name] = {"n": len(pv), "months": pg, "mean": _r(pm), "excess": _r(pm - bmp) if bmp is not None else None, "ci": [_r(x) for x in pci] if pci else None,
                                "hit": _r(sum(1 for x in pv if x[0] > 0) / len(pv), 4)}
            st["by_period"] = by
            out[str(k)] = st
        signals[t] = out
    states, errors, notes = {}, {}, {}
    for p in P:
        states.update(p["symbols"]); errors.update(p.get("errors", {})); notes.update(p.get("notes", {}))
    gen = now_iso()
    as_of = max((s["d"] for s in states.values()), default=None)
    sb.put_json("tech/signals.json", {
        "generated": gen, "as_of": as_of, "horizons": list(HORIZONS), "universe": "Current companies tracked by Filed Under",
        "method": ("Forward total returns (dividends reinvested) from the signal day's close. Base rate: every trading day of the same names from their 253rd bar. "
                   "Intervals are 95% with standard errors clustered by calendar month of the signal, because signals across names fire together. "
                   "Base-rate median is read from a 0.1 percentage-point histogram."),
        "caveats": ["Selection and survivorship bias: only currently tracked companies are included; previously delisted or untracked companies are absent.",
                    "Forward windows of signals less than one horizon apart overlap, which the month clustering only partly absorbs.",
                    "Past frequencies, not forecasts."],
        "source": SOURCE, "base": base, "signals": signals}, cache="max-age=900")
    # breadth
    days = sorted({d for p in P for d in p["breadth"]})
    agg = {d: [0] * 9 for d in days}
    for p in P:
        for d, f in p["breadth"].items():
            agg[d] = [x + y for x, y in zip(agg[d], f)]
    days = [d for d in days if agg[d][0] >= 50]
    col = lambda j: [agg[d][j] for d in days]  # noqa: E731
    sb.put_json("tech/breadth.json", {"generated": gen, "source": "Computed by Filed Under from the daily history files (tech/bars) of currently tracked companies",
                                      "cols": ["n50", "above50", "n200", "above200", "n52", "new_high", "new_low", "adv", "dec"], "d": days, **{k: col(j) for j, k in enumerate(["n50", "above50", "n200", "above200", "n52", "new_high", "new_low", "adv", "dec"])},
                                      "caveat": "Current tracked companies only: early years cover fewer names and omit companies outside current coverage."}, cache="max-age=900")
    rows = []
    for tk, s in states.items():
        u = BY_TICKER.get(tk) or {}
        rows.append({"t": tk, "sector": u.get("sector"), **{k: (round(v, 5) if isinstance(v, float) else v) for k, v in s.items()}})
    sb.put_json("tech/states.json", {"generated": gen, "as_of": as_of, "sector_etf": SECTOR_ETF, "rows": rows, "errors": errors, "notes": notes}, cache="max-age=900")
    log(f"merged {len(P)} parts: {len(states)} states, {len(signals)} signal types, {len(days)} breadth days")
    return {"parts": len(P), "states": len(states), "signals": len(signals), "breadth_days": len(days), "errors": len(errors)}
