"""Firecrawl Alexandria client and the scheduled refresh steps for estimates/.

REST shape (docs.firecrawl.dev/features/alexandria): POST https://api.firecrawl.dev/v2/scrape with
    {"alexandria": {"provider": ..., "capability": ..., "options": {...}}}
or a list of 1-10 such calls, Authorization: Bearer $FIRECRAWL_API_KEY, optional x-request-id (idempotency).
Results arrive in data.alexandria[i].data (or .error per call); data.creditsCost is the request's cost.

UNTESTED LIVE: FIRECRAWL_API_KEY is not available where this was written; the same calls were executed through the
Firecrawl MCP tools for the initial backfill and the parsing is unit-tested against those payloads.

Steps (api/cron/estimates.py?step=...):
  consensus  Yahoo equities/earnings rotation: companies reporting within REPORT_WINDOW_DAYS get a daily refresh,
             the rest rotate every ROTATION_DAYS. One snapshot per company per day accumulates revision history.
  ratings    Benzinga calendar/ratings delta via parameters[updated] since the last sync (50 tickers per call,
             paged), calendar/removed for withdrawn records, and the analyst/firm rosters weekly.
  build      Street view AI summaries (Fireworks) for changed bundles and firm logos for firms without one.
All steps honour dry_run=1 (no provider calls, no writes; prints the plan) and a 680 s time budget.
"""
import datetime as dt
import gzip
import json
import os
import time
import uuid

from . import estimates as E
from .common import HTTPError, Run, now_iso, request
from .universe import UNIVERSE, BY_TICKER

API = "https://api.firecrawl.dev/v2/scrape"
STATE_PATH = "estraw/state.json"          # private raw bucket
ROTATION_DAYS = int(os.environ.get("ESTIMATES_ROTATION_DAYS", "7"))
REPORT_WINDOW_DAYS = int(os.environ.get("ESTIMATES_REPORT_WINDOW_DAYS", "3"))
MAX_YAHOO_PER_RUN = int(os.environ.get("ESTIMATES_MAX_YAHOO_PER_RUN", "400"))
ROSTER_EVERY_DAYS = 7
TIME_BUDGET = 680


class Firecrawl:
    """Minimal Alexandria executor with retries. Each call list is at most 10 calls (one request)."""

    def __init__(self, key=None, dry_run=False, sleep=time.sleep):
        self.key = key if key is not None else os.environ.get("FIRECRAWL_API_KEY")
        self.dry_run = dry_run
        self.sleep = sleep
        self.credits = 0
        self.requests = 0
        if not self.key and not dry_run:
            raise RuntimeError("FIRECRAWL_API_KEY is not set")

    def run(self, calls, retries=3):
        """Returns a list aligned with `calls`: each item is the provider data or {"_error": {...}}."""
        if not calls:
            return [], {}
        if len(calls) > 10:
            raise ValueError("at most 10 Alexandria calls per request")
        if self.dry_run:
            return [{"_error": {"code": "dry_run"}} for _ in calls], {}
        body = {"alexandria": calls if len(calls) > 1 else calls[0]}
        rid = str(uuid.uuid4())   # reused across retries of the identical payload (idempotent on the provider side)
        last = None
        for attempt in range(retries):
            try:
                raw = request(API, method="POST", data=body, timeout=170, retries=1,
                              headers={"Authorization": f"Bearer {self.key}", "Content-Type": "application/json", "x-request-id": rid})
                d = json.loads(raw)
                self.requests += 1
                data = d.get("data") or {}
                self.credits += data.get("creditsCost") or d.get("creditsCost") or 0
                res = data.get("alexandria") or []
                if isinstance(res, dict):
                    res = [res]
                out = []
                for i in range(len(calls)):
                    r = res[i] if i < len(res) else None
                    out.append(r.get("data") if r and r.get("data") is not None and not r.get("error") else {"_error": (r or {}).get("error") or {"code": "missing"}})
                return out, d
            except HTTPError as e:
                last = e
                if e.code in (400, 401, 402, 403):
                    raise
            except Exception as e:  # network, JSON
                last = e
            self.sleep(4 * (attempt + 1) ** 2)
        raise RuntimeError(f"Firecrawl request failed after {retries} attempts: {str(last)[:200]}")


    def run_url(self, url, formats):
        """URL-mode scrape (used for the branding format on research-firm homepages)."""
        if self.dry_run:
            return {}, {}
        raw = request(API, method="POST", data={"url": url, "formats": formats}, timeout=120, retries=2,
                      headers={"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"})
        d = json.loads(raw)
        self.requests += 1
        self.credits += (d.get("data") or {}).get("metadata", {}).get("creditsUsed") or 1
        return d.get("data") or {}, d


def yahoo_call(sym):
    return {"provider": "finance-yahoo-com", "capability": "equities/earnings", "options": {"symbol": sym}}


def ratings_call(tickers, updated=None, date_from=None, page=0):
    o = {"parameters[tickers]": ",".join(t.replace("-", ".") for t in tickers), "pagesize": 1000}
    if updated:
        o["parameters[updated]"] = int(updated)
    if date_from:
        o["parameters[date_from]"] = date_from
    if page:
        o["page"] = page
    return {"provider": "benzinga", "capability": "calendar/ratings", "options": o}


# ---------- state ----------

def load_state(sb):
    b = sb.get_object("raw", STATE_PATH)
    return json.loads(b) if b else {"yahoo": {}, "ratings_updated": None, "roster_at": None, "summaries": {}}


def save_state(sb, st, dry):
    if not dry:
        sb.put_object("raw", STATE_PATH, json.dumps(st, separators=(",", ":")), "application/json", cache="no-store")


def store_raw(sb, kind, payload, dry):
    path = f"estraw/{kind}/{dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%d/%H%M%S')}-{uuid.uuid4().hex[:6]}.json.gz"
    if not dry:
        sb.put_object("raw", path, gzip.compress(json.dumps(payload, separators=(",", ":")).encode()), "application/gzip", cache="no-store")
    return path


# ---------- consensus rotation ----------

def consensus_due(state, bundles_next, today, max_n=MAX_YAHOO_PER_RUN):
    """Tickers to refresh today: reporting within the window first (daily), then the stalest beyond ROTATION_DAYS."""
    t = E.pdate(today)
    near, stale = [], []
    for tk in [u[0] for u in UNIVERSE]:
        last = state["yahoo"].get(tk)
        nxt = bundles_next.get(tk)
        if last == today:
            continue
        if nxt and abs((E.pdate(nxt) - t).days) <= REPORT_WINDOW_DAYS:
            near.append(tk)
        elif not last or (t - E.pdate(last)).days >= ROTATION_DAYS:
            stale.append((last or "0000", tk))
    stale.sort()
    return (near + [tk for _, tk in stale])[:max_n]


def refresh_consensus(sb, fc, state, *, dry=False, deadline=None, today=None):
    from .estimates_backfill import fetch_inputs
    today = today or dt.datetime.now(dt.timezone.utc).date().isoformat()
    idx = sb.get_json("estimates/index.json") or {}
    due = consensus_due(state, {r["ticker"]: r.get("next_earnings") for r in idx.get("companies") or []}, today)
    stats = {"due": len(due), "done": 0, "failed": {}, "credits": 0}
    if dry:
        stats["plan"] = due[:40]
        return stats
    spy = sb.get_json("prices/SPY.json")
    for i in range(0, len(due), 10):
        if deadline and time.time() > deadline:
            stats["skipped"] = len(due) - i
            break
        chunk = due[i:i + 10]
        try:
            res, envelope = fc.run([yahoo_call(t) for t in chunk])
        except Exception as e:
            for t in chunk:
                stats["failed"][t] = str(e)[:120]
            continue
        rawp = store_raw(sb, "yahoo", envelope, dry)
        for tk, data in zip(chunk, res):
            if "_error" in (data or {}):
                stats["failed"][tk] = str(data["_error"])[:120]
                continue
            y = E.parse_yahoo_earnings(data)
            if not y:
                stats["failed"][tk] = "empty payload"
                continue
            inp = fetch_inputs(sb, tk)
            src = {"yahoo": {"provider": E.YAHOO_LABEL, "via": "Firecrawl Alexandria finance-yahoo-com/equities/earnings", "method": "cron consensus",
                             "url": y.get("url"), "retrieved_at": now_iso(), "raw": rawp}}
            b = E.build_bundle(tk, name=BY_TICKER.get(tk, {}).get("name"), yahoo=y, prev=inp.get("prev"), company=inp.get("company"),
                               prices=inp.get("prices"), spy=spy, guidance=inp.get("guidance"), sources=src, today=today)
            sb.put_json(f"estimates/{tk}.json", b, cache="max-age=600")
            state["yahoo"][tk] = today
            stats["done"] += 1
    stats["credits"] = fc.credits
    return stats


# ---------- ratings delta ----------

def refresh_ratings(sb, fc, state, *, dry=False, deadline=None, today=None):
    from .estimates_backfill import fetch_inputs
    today = today or dt.datetime.now(dt.timezone.utc).date().isoformat()
    since = state.get("ratings_updated") or int(time.time()) - 3 * 86400
    started = int(time.time())
    tickers = [u[0] for u in UNIVERSE]
    groups = [tickers[i:i + 50] for i in range(0, len(tickers), 50)]
    stats = {"groups": len(groups), "since": since, "records": 0, "tickers": 0, "failed": {}, "credits": 0}
    if dry:
        stats["plan"] = [len(g) for g in groups]
        return stats
    new = {}
    pending = [(g, 0) for g in range(len(groups))]
    while pending:
        if deadline and time.time() > deadline:
            stats["skipped"] = len(pending)
            break
        batch, pending = pending[:10], pending[10:]
        try:
            res, envelope = fc.run([ratings_call(groups[g], updated=since, page=p) for g, p in batch])
        except Exception as e:
            for g, p in batch:
                stats["failed"][f"{g}:{p}"] = str(e)[:120]
            continue
        rawp = store_raw(sb, "benzinga", envelope, dry)
        for (g, p), data in zip(batch, res):
            if "_error" in (data or {}):
                stats["failed"][f"{g}:{p}"] = str(data["_error"])[:120]
                continue
            rows = data.get("ratings") if isinstance(data, dict) else []
            for r in rows or []:
                a = E.parse_rating(r)
                new.setdefault(a["ticker"], []).append(a)
            stats["records"] += len(rows or [])
            if len(rows or []) >= 900:
                pending.append((g, p + 1))
        stats["raw"] = rawp
    removed = set()
    try:
        res, envelope = fc.run([{"provider": "benzinga", "capability": "calendar/removed", "options": {"type": "ratings", "pageSize": 1000, "updated": int(since)}}])
        store_raw(sb, "benzinga-removed", envelope, dry)
        removed = {r["id"] for r in (res[0] or {}).get("removed") or [] if r.get("id")}
    except Exception as e:
        stats["failed"]["removed"] = str(e)[:120]
    spy = sb.get_json("prices/SPY.json")
    touched = set(new)
    if removed:  # a withdrawn record may sit in any bundle; only bundles that hold one are rewritten
        idx = sb.get_json("estimates/index.json") or {}
        touched |= {r["ticker"] for r in idx.get("companies") or [] if set(r.get("action_ids_recent") or []) & removed}
    for tk in sorted(touched):
        inp = fetch_inputs(sb, tk)
        src = {"benzinga": {"provider": "Benzinga", "attribution": E.BENZINGA_ATTRIBUTION, "via": "Firecrawl Alexandria benzinga/calendar/ratings",
                            "method": "cron ratings delta", "retrieved_at": now_iso(), "raw": stats.get("raw")}}
        b = E.build_bundle(tk, name=BY_TICKER.get(tk, {}).get("name"), prev=inp.get("prev"), actions=new.get(tk, []), company=inp.get("company"),
                           prices=inp.get("prices"), spy=spy, guidance=inp.get("guidance"), sources=src, today=today, removed_ids=removed)
        sb.put_json(f"estimates/{tk}.json", b, cache="max-age=600")
        stats["tickers"] += 1
    if not stats["failed"]:
        state["ratings_updated"] = started - 600   # small overlap; merge is idempotent by id
    stats["credits"] = fc.credits
    return stats


def refresh_rosters(sb, fc, state, *, dry=False, today=None):
    today = today or dt.datetime.now(dt.timezone.utc).date().isoformat()
    if state.get("roster_at") and (E.pdate(today) - E.pdate(state["roster_at"])).days < ROSTER_EVERY_DAYS:
        return {"skipped": "roster fresh"}
    if dry:
        return {"plan": "analysts pages 0-6, firms page 0"}
    analysts, firms = [], []
    for p in range(0, 10):
        res, envelope = fc.run([{"provider": "benzinga", "capability": "calendar/ratings-analysts", "options": {"pageSize": 1000, **({"page": p} if p else {})}}])
        store_raw(sb, "benzinga-roster", envelope, dry)
        rows = (res[0] or {}).get("analyst_ratings_analyst") or []
        analysts += rows
        if len(rows) < 900:
            break
    res, envelope = fc.run([{"provider": "benzinga", "capability": "calendar/ratings-firms", "options": {"pageSize": 1000}}])
    store_raw(sb, "benzinga-roster", envelope, dry)
    firms = (res[0] or {}).get("analyst_ratings_firm") or []
    sb.put_object("raw", "estraw/rosters/latest.json.gz", E.gz({"analysts": analysts, "firms": firms, "at": now_iso()}), "application/gzip", cache="no-store")
    state["roster_at"] = today
    return {"analysts": len(analysts), "firms": len(firms)}


def publish_index(sb, tickers=None):
    """estimates/index.json: per-company summary used for rotation (next earnings) and cross-company views."""
    from concurrent.futures import ThreadPoolExecutor
    tickers = tickers or [u[0] for u in UNIVERSE]

    def one(tk):
        try:
            return tk, sb.get_json(f"estimates/{tk}.json")
        except Exception:
            return tk, None
    rows, acts = [], {}
    with ThreadPoolExecutor(12) as ex:
        for tk, b in ex.map(one, tickers):
            if not b:
                continue
            acts[tk] = b.get("actions") or []
            c = b.get("consensus") or {}
            q0 = next((p for p in c.get("periods") or [] if p["code"] == "0q"), {})
            rows.append({"ticker": tk, "next_earnings": (c.get("next_earnings") or {}).get("date"), "consensus_as_of": c.get("as_of"),
                         "eps_0q": (q0.get("eps") or {}).get("avg"), "n_actions": len(acts[tk]), "last_action": (acts[tk][0]["date"] if acts[tk] else None),
                         "action_ids_recent": [a["id"] for a in acts[tk][:60]], "targets": (b.get("targets") or {}).get("stats"),
                         "ratings": (b.get("ratings") or {}).get("counts"), "street_view": bool(b.get("street_view"))})
    sb.put_json("estimates/index.json", {"generated": now_iso(), "companies": rows}, cache="max-age=600")
    return rows, acts


def rebuild_rosters(sb, acts):
    b = sb.get_object("raw", "estraw/rosters/latest.json.gz")
    roster = json.loads(gzip.decompress(b)) if b else {"analysts": [], "firms": []}
    reg = sb.get_json("estimates/firm-logos/registry.json") or {}
    logos = {k: v.get("path") for k, v in (reg.get("firms") or {}).items() if v.get("path")}
    analysts, firms = E.build_rosters(acts, roster["analysts"], roster["firms"], logos)
    sb.put_json("estimates/analysts.json", analysts, cache="max-age=600")
    sb.put_json("estimates/firms.json", firms, cache="max-age=600")
    return {"analysts": len(analysts["analysts"]), "firms": len(firms["firms"])}


def job(sb, step="consensus", dry_run=False, limit=None):
    deadline = time.time() + TIME_BUDGET
    fc = Firecrawl(dry_run=dry_run)
    state = load_state(sb)
    with Run(sb, f"estimates_{step}") if not dry_run else _NoRun() as run:
        if step == "consensus":
            run.stats.update(refresh_consensus(sb, fc, state, dry=dry_run, deadline=deadline - 60))
        elif step == "ratings":
            run.stats.update(refresh_ratings(sb, fc, state, dry=dry_run, deadline=deadline - 120))
            run.stats["rosters"] = refresh_rosters(sb, fc, state, dry=dry_run)
        elif step == "build":
            from . import estimates_ai, estimates_logos
            run.stats["summaries"] = estimates_ai.run(sb, state, dry=dry_run, deadline=deadline - 200, limit=limit)
            run.stats["logos"] = estimates_logos.run(sb, fc, dry=dry_run, deadline=deadline - 120)
        else:
            raise ValueError("step must be consensus, ratings or build")
        if not dry_run:
            rows, acts = publish_index(sb)
            run.stats["index"] = len(rows)
            if step in ("ratings", "build"):
                run.stats["rosters_published"] = rebuild_rosters(sb, acts)
        save_state(sb, state, dry_run)
        run.stats["credits"] = fc.credits
        return run.stats


class _NoRun:
    def __enter__(self):
        self.stats = {"dry_run": True}
        return self

    def __exit__(self, *a):
        return False
