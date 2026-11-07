"""Bulk IR document backfill for long-running machines (GitHub Actions), outside the Vercel cron limits.

Same code path as the cron (jobs.ir_job -> ir.process_company: Tavily site discovery, Firecrawl map/scrape, SEC
exhibits, text extraction into the `ir` bucket), run in chunks with more workers and no 800 s ceiling. After each
chunk the coverage index is updated for that chunk under the aggregate lease (kpitools.build_index), so the app and
the cron rotation see the new documents as they land.

Lanes. SEC allows 10 requests per second in total, however many machines are used, while Firecrawl and Tavily scale
with the number of runners. So the work is split:
  --lane web   many shards: company IR sites only (no SEC requests), only companies without IR documents
  --lane sec   one runner at SEC's full rate: SEC enumeration and exhibits, first for indexed companies whose SEC
               enumeration is not complete, then for each company the web lane finishes (manifest newer than the
               run start); it keeps polling for those until its time is up
  --lane all   both in one process (the original mode; SEC_GAP must be split across shards by the caller)

No company is worked on by two lanes at once: the web lane takes only companies without documents, the sec lane
takes those only after the web lane's manifest for them is written.

Order for the web and all lanes: companies with no IR documents first, starting from the end of the universe (the
cron's first-pass catch-up starts from the front, so the two rarely meet), then (all lane) partial companies, oldest
first.

    python -m etl.ir_backfill_runner --lane web --shard 3 --shards 19 --workers 8
    python -m etl.ir_backfill_runner --lane sec --workers 8 --started 2026-11-07T17:30:00Z
    python -m etl.ir_backfill_runner --tickers NVDA,AAPL
"""
import argparse
import datetime as dt
import json
import time

from . import jobs
from .common import LeaseBusy, Supabase


def unindexed(sb, index):
    """Companies processed since the coverage index was last published (a run stopped before its chunk published).
    Their per-company manifest ir/{T}.json holds the coverage the index is missing. Returns {ticker: coverage}."""
    since = index.get("generated") or "1970-01-01T00:00:00Z"
    rows = sb.select("ir_documents", f"select=ticker&fetched_at=gte.{since}")
    out = {}
    for t in sorted({r["ticker"] for r in rows if r.get("ticker")}):
        try:
            m = sb.get_json(f"ir/{t}.json") or {}
        except Exception:
            continue
        if (m.get("generated") or "") >= since and isinstance(m.get("coverage"), dict):
            out[t] = m["coverage"]
    return out


def shard_of(ticker, shards):
    """Stable shard for a ticker. Shards start a few seconds apart and may see different skip sets or a newer
    index, so position in the planned list cannot decide ownership: two shards would share a company (one then fails
    on its per-company lease) and another company would land in none."""
    import zlib
    return zlib.crc32(ticker.encode()) % max(1, shards)


def _cov(rows, t):
    return (rows.get(t) or {}).get("coverage") or {}


def plan(sb, tickers=None, include_partial=True, index=None, skip=()):
    from .universe import UNIVERSE
    order = [t for t, *_ in UNIVERSE]
    if tickers:
        return [t for t in tickers if t in order]
    index = index if index is not None else (sb.get_json("meta/coverage_ir.json") or {})
    rows = index.get("companies") or {}
    skip = set(skip)
    empty = [t for t in reversed(order) if not (rows.get(t) or {}).get("docs") and t not in skip]
    partial = []
    if include_partial:
        partial = [t for t in order if t not in skip and (rows.get(t) or {}).get("docs") and _cov(rows, t).get("status") == "partial"]
        partial.sort(key=lambda t: _cov(rows, t).get("updated_at") or "")
    return empty + partial


def sec_due(index, recovered=None):
    """Companies with documents (in the index, or recovered from manifests) whose SEC enumeration is not complete,
    oldest coverage first."""
    rows = dict(index.get("companies") or {})
    for t, cov in (recovered or {}).items():
        rows[t] = {**(rows.get(t) or {}), "docs": (rows.get(t) or {}).get("docs") or 1, "coverage": cov}
    due = [t for t, v in rows.items() if v.get("docs") and (_cov(rows, t).get("enumeration") or {}).get("sec") != "complete"]
    return sorted(due, key=lambda t: _cov(rows, t).get("updated_at") or "")


def web_finished_since(sb, started, candidates, seen, workers=16):
    """Web-lane companies whose manifest was written after `started` (the web lane is done with them) and whose SEC
    part is open. Checks manifests directly, so companies where no IR site was found are included too."""
    from concurrent.futures import ThreadPoolExecutor

    def one(t):
        try:
            m = sb.get_json(f"ir/{t}.json") or {}
        except Exception:
            return None
        cov = m.get("coverage") or {}
        if (m.get("generated") or "") >= started and (cov.get("enumeration") or {}).get("sec") != "complete":
            return t
        return None
    todo = [t for t in candidates if t not in seen]
    with ThreadPoolExecutor(workers) as ex:
        return [t for t in ex.map(one, todo) if t]


def publish_coverage(sb, updates, attempts=4, log=print):
    """Coverage index update; waits a little for the aggregate lease. On failure the caller keeps the updates and
    sends them again with the next chunk, so nothing is dropped while the cron holds the lease."""
    from .kpitools import build_index
    for k in range(attempts):
        try:
            build_index(sb, coverage_updates=updates)
            return True
        except LeaseBusy:
            log(f"coverage index: lease busy, retry {k + 1}/{attempts}")
            time.sleep(20 + 10 * min(k, 4))
    log(f"coverage index: lease still busy; {len(updates)} company updates carried to the next chunk")
    return False


class Runner:
    def __init__(self, sb, a, end, t0):
        self.sb, self.a, self.end, self.t0 = sb, a, end, t0
        self.totals = {"done": 0, "failed": 0, "docs": 0}
        self.unpublished = {}
        self.chunks = 0

    def left(self):
        return self.end - time.time()

    def run_chunk(self, chunk, web, sec):
        a = self.a
        done, failed = jobs.ir_job(self.sb, chunk, workers=a.workers, time_budget=min(self.left() - 300, 3 * 3600),
                                   max_docs=a.max_docs, web=web, sec=sec)
        self.unpublished.update({tk: r["coverage"] for tk, r in done.items() if isinstance(r.get("coverage"), dict)})
        if self.unpublished and publish_coverage(self.sb, dict(self.unpublished)):
            self.unpublished.clear()
        self.chunks += 1
        self.totals["done"] += len(done)
        self.totals["failed"] += sum(1 for v in failed.values() if not str(v).startswith("partial"))
        self.totals["docs"] += sum(int(r.get("sec") or 0) + int(r.get("web") or 0) for r in done.values())
        print(json.dumps({"chunk": self.chunks, "done": len(done), "failed": {k: str(v)[:120] for k, v in failed.items()},
                          "elapsed_min": round((time.time() - self.t0) / 60, 1)}), flush=True)

    def run_list(self, todo, web, sec):
        for i in range(0, len(todo), self.a.chunk):
            if self.left() < 600:
                print("stopping: wall-clock budget", flush=True)
                return False
            self.run_chunk(todo[i:i + self.a.chunk], web, sec)
        return True

    def finish(self):
        if self.unpublished and not publish_coverage(self.sb, dict(self.unpublished), attempts=30):
            print(f"coverage index not updated for {len(self.unpublished)} companies: {sorted(self.unpublished)}", flush=True)
        print("IR backfill totals", json.dumps(self.totals), flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lane", choices=("all", "web", "sec"), default="all")
    ap.add_argument("--hours", type=float, default=5.5, help="wall-clock budget")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--chunk", type=int, default=12)
    ap.add_argument("--max-docs", type=int, default=90)
    ap.add_argument("--tickers", default="")
    ap.add_argument("--no-partial", action="store_true")
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--shards", type=int, default=1)
    ap.add_argument("--started", default="", help="run start (UTC ISO); the sec lane follows web-lane manifests newer than this")
    ap.add_argument("--poll", type=int, default=120, help="sec lane: seconds between checks for companies the web lane finished")
    a = ap.parse_args()
    sb = Supabase()
    t0 = time.time()
    end = t0 + a.hours * 3600
    started = a.started or dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    explicit = [t.strip().upper() for t in a.tickers.split(",") if t.strip()] or None
    index = sb.get_json("meta/coverage_ir.json") or {}
    # Every shard computes the same skip set, so the split stays consistent; one runner alone publishes it.
    recovered = {} if explicit else unindexed(sb, index)
    if recovered:
        print(f"{len(recovered)} companies processed since the last index; skipping them", flush=True)
    r = Runner(sb, a, end, t0)
    if (a.lane == "sec" or (a.lane == "all" and a.shard == 0)) and recovered:
        r.unpublished.update(recovered)

    if a.lane == "sec":
        if explicit:
            r.run_list(explicit, web=False, sec=True)
            r.finish()
            return
        todo = sec_due(index, recovered)
        print(f"IR backfill sec lane: {len(todo)} companies with documents and SEC enumeration open, {a.workers} workers", flush=True)
        seen = set(todo)
        web_plan = plan(sb, include_partial=False, index=index, skip=recovered)
        if r.run_list(todo, web=False, sec=True):
            # Then follow the web lane: each company it finishes gets its SEC part here.
            while r.left() > 600:
                fresh = web_finished_since(sb, started, web_plan, seen)
                if not fresh:
                    time.sleep(a.poll)
                    continue
                seen.update(fresh)
                if not r.run_list(fresh, web=False, sec=True):
                    break
        r.finish()
        return

    if a.lane == "web":
        todo = plan(sb, explicit, include_partial=False, index=index, skip=recovered)
    else:
        todo = plan(sb, explicit, include_partial=not a.no_partial, index=index, skip=recovered)
    todo = [t for t in todo if shard_of(t, a.shards) == a.shard]
    print(f"IR backfill {a.lane} lane: {len(todo)} companies, {a.workers} workers, {a.hours} h", flush=True)
    r.run_list(todo, web=True, sec=a.lane == "all")
    r.finish()


if __name__ == "__main__":
    main()
