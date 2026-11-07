"""Bulk runner for the filing-language theme index (GitHub Actions or a long-running machine).

    python -m etl.themes_runner --shard 0 --shards 2 --floor 2024-07-01 --hours 5     # recent filings first
    python -m etl.themes_runner --no-sec --hours 1                                    # stored copies only
    python -m etl.themes_runner --aggregate                                           # publish themes/*.json

Stored copies are used first; EDGAR fetches go through the shared SEC throttle (set SEC_GAP to this lane's share
of SEC's 10 requests per second). Companies are owned by a stable crc32 shard so every runner sees the same split.
"""
import argparse
import concurrent.futures as cf
import json
import time
import zlib

from . import themes
from .common import Supabase
from .universe import UNIVERSE


def shard_of(ticker, shards):
    return zlib.crc32(ticker.encode()) % max(1, shards)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=5.0)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--shards", type=int, default=1)
    ap.add_argument("--floor", default="", help="only filings filed on or after this date in this pass (newest first)")
    ap.add_argument("--since", default=themes.SINCE)
    ap.add_argument("--tickers", default="")
    ap.add_argument("--no-sec", action="store_true", help="stored copies only")
    ap.add_argument("--max-fetch", type=int, default=None, help="EDGAR documents per company per pass")
    ap.add_argument("--aggregate", action="store_true", help="publish the theme outputs after indexing")
    ap.add_argument("--aggregate-only", action="store_true")
    a = ap.parse_args()
    sb = Supabase()
    t0 = time.time()
    end = t0 + a.hours * 3600
    if not a.aggregate_only:
        defs = themes.load_defs()
        tickers = [t.strip().upper() for t in a.tickers.split(",") if t.strip()] or [t for t, *_ in UNIVERSE]
        tickers = [t for t in tickers if shard_of(t, a.shards) == a.shard]
        print(f"themes index: {len(tickers)} companies, floor {a.floor or a.since}, sec={'off' if a.no_sec else 'on'}", flush=True)
        totals = {"indexed": 0, "rematched": 0, "fetched": 0, "missing": 0, "skipped": 0, "companies": 0, "errors": 0}

        def one(tk):
            if time.time() > end - 300:
                return tk, {"skipped": 1}
            try:
                return tk, themes.index_company(sb, tk, defs, allow_sec=not a.no_sec, since=a.since, floor=a.floor or None,
                                                deadline=end - 300, max_fetch=a.max_fetch)
            except Exception as e:
                return tk, {"error": str(e)[:200]}
        done = 0
        with cf.ThreadPoolExecutor(a.workers) as ex:
            for tk, st in ex.map(one, tickers):
                done += 1
                if st.get("error"):
                    totals["errors"] += 1
                    print(f"  {tk}: {st['error']}", flush=True)
                for k in ("indexed", "rematched", "fetched", "missing", "skipped"):
                    totals[k] += int(st.get(k) or 0)
                totals["companies"] += 1
                if done % 50 == 0:
                    print(json.dumps({**totals, "elapsed_min": round((time.time() - t0) / 60, 1)}), flush=True)
        print("themes index totals", json.dumps(totals), flush=True)
    if a.aggregate or a.aggregate_only:
        print("themes aggregate", json.dumps(themes.aggregate_job(sb)), flush=True)


if __name__ == "__main__":
    main()
