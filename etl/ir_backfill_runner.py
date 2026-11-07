"""Bulk IR document backfill for a long-running machine (GitHub Actions), outside the Vercel cron limits.

Same code path as the cron (jobs.ir_job -> ir.process_company: Tavily site discovery, Firecrawl map/scrape, SEC
exhibits, text extraction into the `ir` bucket), run in chunks with more workers and no 800 s ceiling. After each
chunk the coverage index is updated for that chunk under the aggregate lease (kpitools.build_index), so the app and
the cron rotation see the new documents as they land.

Order: companies with no IR documents first, starting from the end of the universe (the cron's first-pass catch-up
starts from the front, so the two rarely meet), then partial companies, oldest coverage first.

    python -m etl.ir_backfill_runner --hours 5.5 --workers 6
    python -m etl.ir_backfill_runner --tickers NVDA,AAPL
"""
import argparse
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
        partial = [t for t in order if t not in skip and (rows.get(t) or {}).get("docs") and ((rows[t].get("coverage") or {}).get("status") == "partial")]
        partial.sort(key=lambda t: ((rows[t].get("coverage") or {}).get("updated_at") or ""))
    return empty + partial


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
            time.sleep(30)
    log(f"coverage index: lease still busy; {len(updates)} company updates carried to the next chunk")
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=5.5, help="wall-clock budget")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--chunk", type=int, default=12)
    ap.add_argument("--max-docs", type=int, default=90)
    ap.add_argument("--tickers", default="")
    ap.add_argument("--no-partial", action="store_true")
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--shards", type=int, default=1)
    a = ap.parse_args()
    sb = Supabase()
    t0 = time.time()
    end = t0 + a.hours * 3600
    explicit = [t.strip().upper() for t in a.tickers.split(",") if t.strip()] or None
    index = sb.get_json("meta/coverage_ir.json") or {}
    # Every shard computes the same skip set, so the split stays consistent; shard 0 alone publishes it.
    recovered = {} if explicit else unindexed(sb, index)
    if recovered:
        print(f"{len(recovered)} companies processed since the last index; skipping them", flush=True)
    todo = plan(sb, explicit, include_partial=not a.no_partial, index=index, skip=recovered)
    todo = todo[a.shard::a.shards]
    print(f"IR backfill: {len(todo)} companies, {a.workers} workers, {a.hours} h", flush=True)
    totals = {"done": 0, "failed": 0, "docs": 0}
    unpublished = dict(recovered) if a.shard == 0 else {}
    for i in range(0, len(todo), a.chunk):
        left = end - time.time()
        if left < 600:
            print("stopping: wall-clock budget", flush=True)
            break
        chunk = todo[i:i + a.chunk]
        done, failed = jobs.ir_job(sb, chunk, workers=a.workers, time_budget=min(left - 300, 3 * 3600), max_docs=a.max_docs)
        unpublished.update({tk: r["coverage"] for tk, r in done.items() if isinstance(r.get("coverage"), dict)})
        if unpublished and publish_coverage(sb, dict(unpublished)):
            unpublished.clear()
        totals["done"] += len(done)
        totals["failed"] += sum(1 for v in failed.values() if not str(v).startswith("partial"))
        totals["docs"] += sum(int(r.get("sec") or 0) + int(r.get("web") or 0) for r in done.values())
        print(json.dumps({"chunk": i // a.chunk + 1, "done": len(done), "failed": {k: str(v)[:120] for k, v in failed.items()},
                          "elapsed_min": round((time.time() - t0) / 60, 1)}), flush=True)
    if unpublished and not publish_coverage(sb, dict(unpublished), attempts=30):
        print(f"coverage index not updated for {len(unpublished)} companies: {sorted(unpublished)}", flush=True)
    print("IR backfill totals", json.dumps(totals), flush=True)


if __name__ == "__main__":
    main()
