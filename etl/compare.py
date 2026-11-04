"""Inputs for the Compare page that the daily price job does not carry.

- Five years of weekly closes, dividend-adjusted closes, dividends and splits per symbol (Yahoo Finance chart API).
  The page uses them for total-return charts beyond two years and for each company's five-year valuation range.
- Return-correlation peers: for every company, the ten companies whose daily returns over the last year
  correlate most with its own (computed from the published daily bars).
"""
import datetime as dt
import math
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

from .common import HTTPError, get_json, now_iso
from .prices import path_key, yahoo_symbol

HIST_RANGE = "5y"
HIST_INTERVAL = "1wk"
CORR_WINDOW = 252
CORR_TOP = 10


def history_url(sym):
    q = urllib.parse.quote(yahoo_symbol(sym))
    return f"https://query2.finance.yahoo.com/v8/finance/chart/{q}?range={HIST_RANGE}&interval={HIST_INTERVAL}&events=div%2Csplit&includePrePost=false"


def _day(ts):
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).date().isoformat()


def history(sym):
    """Weekly bars with dividend and split events. Returns the bundle written to history/{SYM}.json."""
    url = history_url(sym)
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
    adj = (r["indicators"].get("adjclose") or [{}])[0].get("adjclose") or [None] * len(r.get("timestamp") or [])
    w = []
    for i, t in enumerate(r.get("timestamp") or []):
        c = q["close"][i]
        if c is None:
            continue
        a = adj[i] if i < len(adj) and adj[i] is not None else c
        w.append([t, round(c, 4), round(a, 4)])
    ev = r.get("events") or {}
    div = sorted([[int(d["date"]), round(float(d["amount"]), 6)] for d in (ev.get("dividends") or {}).values() if d.get("amount") is not None])
    split = sorted([[int(s["date"]), float(s["numerator"]), float(s["denominator"])] for s in (ev.get("splits") or {}).values() if s.get("numerator") and s.get("denominator")])
    m = r.get("meta") or {}
    return {
        "symbol": sym, "currency": m.get("currency"), "interval": HIST_INTERVAL, "range": HIST_RANGE,
        "source": "Yahoo Finance chart API", "source_url": url, "retrieved_at": now_iso(),
        "note": "w: [week start (epoch s), close (split-adjusted), close adjusted for splits and dividends]. The last week can be partial. "
                "div: [ex-date, amount per share, split-adjusted]. split: [date, numerator, denominator].",
        "w": w, "div": div, "split": split,
    }


MISSING_TABLES = set()


def db_upsert(sb, table, rows, key, log=print):
    """Upserts rows; when the table is not there yet (schema not migrated) the JSON bundles still publish
    and the missing table is reported in the run stats instead of failing the job."""
    if not rows or table in MISSING_TABLES:
        return 0
    try:
        return sb.upsert(table, rows, key, batch=500)
    except HTTPError as e:
        body = str(getattr(e, "body", "") or e)
        if e.code == 404 or "PGRST205" in body or "does not exist" in body:
            MISSING_TABLES.add(table)
            log(f"  {table} is missing; run scripts/migrate.mjs. Rows not written.")
            return 0
        raise


def db_rows(h, last_n=None):
    weeks = h["w"][-last_n:] if last_n else h["w"]
    pw = [{"symbol": h["symbol"], "d": _day(t), "c": c, "adj": a, "source_url": h["source_url"], "retrieved_at": h["retrieved_at"]} for t, c, a in weeks]
    acts = [{"symbol": h["symbol"], "d": _day(t), "kind": "dividend", "value": v, "numerator": None, "denominator": None,
             "source_url": h["source_url"], "retrieved_at": h["retrieved_at"]} for t, v in h["div"]]
    acts += [{"symbol": h["symbol"], "d": _day(t), "kind": "split", "value": n / d, "numerator": n, "denominator": d,
              "source_url": h["source_url"], "retrieved_at": h["retrieved_at"]} for t, n, d in h["split"]]
    # one row per (symbol, day, kind): Yahoo occasionally repeats an event
    seen, out = set(), []
    for a in acts:
        k = (a["symbol"], a["d"], a["kind"])
        if k not in seen:
            seen.add(k)
            out.append(a)
    return pw, out


def history_job(sb, symbols, manifest, time_budget=None, full=False, workers=3, pause=0.35, log=print):
    """Fetches history for symbols, oldest retrieval first, until the time budget runs out.
    manifest maps symbol -> retrieved_at and is updated in place. Returns (done, errors)."""
    t0 = time.time()
    order = sorted(symbols, key=lambda s: manifest.get(s) or "")
    done, errors = [], {}

    def one(s):
        if time_budget and time.time() - t0 > time_budget:
            return s, None, "skipped: time budget"
        time.sleep(pause)
        try:
            return s, history(s), None
        except Exception as e:
            return s, None, str(e)[:200]

    weekly, actions = [], []
    with ThreadPoolExecutor(workers) as ex:
        for s, h, err in ex.map(one, order):
            if err:
                if not err.startswith("skipped"):
                    errors[s] = err
                continue
            if not h["w"]:
                errors[s] = "no bars"
                continue
            sb.put_json(f"history/{path_key(s)}.json", h, cache="max-age=3600")
            pw, acts = db_rows(h, None if full else 3)
            weekly += pw
            actions += acts
            manifest[s] = h["retrieved_at"]
            done.append(s)
            if len(weekly) >= 5000:
                db_upsert(sb, "prices_weekly", weekly, "symbol,d", log)
                weekly = []
            if len(done) % 50 == 0:
                log(f"  history {len(done)} of {len(order)}")
    db_upsert(sb, "prices_weekly", weekly, "symbol,d", log)
    db_upsert(sb, "corporate_actions", actions, "symbol,d,kind", log)
    return done, errors


# ---------------------------------------------------------------- return-correlation peers
def _aligned_returns(bars_by_sym, window):
    """Daily returns on the dates every symbol shares inside the last window+1 sessions of SPY's calendar."""
    out = {}
    for s, bars in bars_by_sym.items():
        out[s] = {_day(b[0]): b[4] for b in bars}
    cal = sorted(out.get("SPY", {}).keys())[-(window + 1):]
    rets = {}
    for s, m in out.items():
        if s == "SPY":
            continue
        pts = [m.get(d) for d in cal]
        r = []
        for i in range(1, len(pts)):
            a, b = pts[i - 1], pts[i]
            r.append(b / a - 1 if a and b else None)
        if sum(x is not None for x in r) >= window * 0.8:
            rets[s] = r
    return cal, rets


def correlation_peers(bars_by_sym, window=CORR_WINDOW, top=CORR_TOP):
    """Pearson correlation of daily returns, pairwise over days where both have a return. Standardised vectors
    with missing days set to zero make each pair a dot product; pairs with fewer than 80% shared days are skipped."""
    cal, rets = _aligned_returns(bars_by_sym, window)
    syms = sorted(rets)
    z, mask = {}, {}
    for s in syms:
        r = rets[s]
        v = [x for x in r if x is not None]
        m = sum(v) / len(v)
        sdv = math.sqrt(sum((x - m) ** 2 for x in v) / (len(v) - 1)) or 1e-12
        z[s] = [((x - m) / sdv) if x is not None else 0.0 for x in r]
        mask[s] = [x is not None for x in r]
    n = len(cal) - 1
    full = {s: all(mask[s]) for s in syms}
    out = {s: [] for s in syms}
    for i, a in enumerate(syms):
        za, ma = z[a], mask[a]
        for b in syms[i + 1:]:
            zb, mb = z[b], mask[b]
            k = n if full[a] and full[b] else sum(1 for j in range(n) if ma[j] and mb[j])
            if k < n * 0.8:
                continue
            rho = sum(za[j] * zb[j] for j in range(n)) / (k - 1)
            rho = max(-1.0, min(1.0, rho))
            out[a].append((b, rho))
            out[b].append((a, rho))
    res = {s: [[p, round(r, 4)] for p, r in sorted(v, key=lambda x: -x[1])[:top]] for s, v in out.items()}
    return {"from": cal[0] if cal else None, "to": cal[-1] if cal else None, "sessions": n, "peers": res}


# ---------------------------------------------------------------- job
def compare_job(sb, tickers=None, time_budget=None, full=False, peers=True, workers=3, log=print):
    """Refreshes history/{SYM}.json (oldest first, inside the time budget) and meta/peers.json.
    full=True writes every weekly row to prices_weekly (backfill); otherwise only the last three weeks."""
    from .common import Run
    from .universe import UNIVERSE
    with Run(sb, "compare") as run:
        t0 = time.time()
        syms = tickers or (["SPY"] + [t for t, *_ in UNIVERSE])
        manifest = {}
        try:
            manifest = (sb.get_json("meta/history.json") or {}).get("symbols") or {}
        except Exception:
            manifest = {}
        hist_budget = time_budget * 0.6 if time_budget else None
        done, errors = history_job(sb, syms, manifest, time_budget=hist_budget, full=full, workers=workers, log=log)
        sb.put_json("meta/history.json", {"generated": now_iso(), "source": "Yahoo Finance chart API", "range": HIST_RANGE, "interval": HIST_INTERVAL, "symbols": manifest}, cache="no-store")
        run.stats.update({"history": len(done), "history_errors": errors})
        if peers and not tickers:
            peer_doc = peers_job(sb, deadline=t0 + time_budget * 0.95 if time_budget else None, log=log)
            run.stats.update({"peers": len(peer_doc["peers"]) if peer_doc else 0})
        if MISSING_TABLES:
            run.stats["missing_tables"] = sorted(MISSING_TABLES)
        return done, errors


def peers_job(sb, deadline=None, log=print):
    from .universe import UNIVERSE
    syms = ["SPY"] + [t for t, *_ in UNIVERSE]

    def load(s):
        try:
            d = sb.get_json(f"prices/{path_key(s)}.json") or {}
            return s, d.get("d") or []
        except Exception:
            return s, []

    with ThreadPoolExecutor(8) as ex:
        bars = {s: b for s, b in ex.map(load, syms) if b}
    if deadline and time.time() > deadline:
        log("  peers skipped: time budget")
        return None
    res = correlation_peers(bars)
    doc = {"generated": now_iso(), "method": f"Pearson correlation of daily close-to-close returns over the last {res['sessions']} sessions ({res['from']} to {res['to']}), top {CORR_TOP} per company",
           "source": "Daily closes in prices/{SYMBOL}.json (Yahoo Finance chart API)", **res}
    sb.put_json("meta/peers.json", doc, cache="max-age=3600")
    rows = [{"ticker": t, "rank": i + 1, "peer": p, "rho": r, "window_from": res["from"], "window_to": res["to"], "computed_at": doc["generated"]}
            for t, lst in res["peers"].items() for i, (p, r) in enumerate(lst)]
    db_upsert(sb, "return_peers", rows, "ticker,rank", log)
    return doc
