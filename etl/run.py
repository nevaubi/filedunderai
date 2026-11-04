"""Command line entry: python -m etl.run <job> [options]."""
import argparse
import json

from . import jobs
from .common import Supabase


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("job", choices=["companies", "prices", "macro", "index", "feed", "news", "insiders", "logos", "viewer", "segments", "ir", "compare", "sectors", "screener", "calls", "all"])
    ap.add_argument("--tickers", nargs="*")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--news", action="store_true", help="refresh news for each company")
    ap.add_argument("--summary", action="store_true", help="regenerate summaries")
    ap.add_argument("--no-docs", action="store_true")
    ap.add_argument("--full-history", action="store_true")
    ap.add_argument("--part", type=int)
    ap.add_argument("--parts", type=int, default=1)
    ap.add_argument("--changed", action="store_true", help="only companies with new 10-K/10-Q/8-K filings")
    ap.add_argument("--sec-only", action="store_true", help="IR job: EDGAR exhibits only, skip the IR website")
    ap.add_argument("--budget", type=float, help="compare job: time budget in seconds")
    ap.add_argument("--sec-years", type=float, default=3, help="IR job: years of EDGAR 8-K earnings exhibits to collect")
    ap.add_argument("--step", default="publish", choices=["prices", "publish", "notes"], help="sectors job step")
    ap.add_argument("--mode", default="run", choices=["run", "publish", "db"], help="calls job: run (scan + analyse + publish), publish (cross-company files) or db (copy bundles into the calls tables)")
    ap.add_argument("--recent", action="store_true", help="calls job: analyse only calls from the last 8 quarters")
    ap.add_argument("--limit", type=int, help="calls job: most new analyses per company in this run")
    a = ap.parse_args()
    sb = Supabase()
    if a.job in ("prices", "all"):
        print("prices", jobs.prices_job(sb, full_history=a.full_history, part=a.part, parts=a.parts))
    if a.job in ("macro", "all"):
        jobs.macro_job(sb)
        print("macro ok")
    if a.job in ("companies", "all"):
        print(json.dumps(jobs.companies_job(sb, a.tickers, workers=a.workers, refresh_news=a.news, refresh_summary=a.summary, fetch_docs=not a.no_docs, only_changed=a.changed), default=str)[:2000])
    if a.job == "news":
        print(jobs.news_rotation_job(sb))
    if a.job in ("insiders", "all"):
        print("insiders", jobs.insiders_job(sb, a.tickers, workers=a.workers))
    if a.job == "ir":
        done, failed = jobs.ir_job(sb, a.tickers, workers=a.workers, web=not a.sec_only, sec_years=a.sec_years)
        print("ir", len(done), "failed", failed)
    if a.job == "segments":
        done, failed = jobs.segments_job(sb, a.tickers, workers=a.workers)
        print("segments", len(done), "failed", failed)
    if a.job == "sectors":
        print("sectors", json.dumps(jobs.sectors_job(sb, a.step, time_budget=a.budget), default=str)[:3000])
    if a.job == "viewer":
        print("viewer copies", jobs.publish_filings_job(sb, a.tickers, workers=a.workers))
    if a.job == "logos":
        from .logos import logos_job
        print("logos", logos_job(sb, a.tickers))
    if a.job == "compare":
        from .compare import compare_job
        done, errors = compare_job(sb, a.tickers, time_budget=a.budget, full=a.full_history, workers=min(a.workers, 4))
        print("compare history", len(done), "errors", errors)
    if a.job == "screener":
        print("screener", jobs.screener_job(sb, full=a.full_history, workers=a.workers))
    if a.job == "calls":
        from .calls import calls_job
        print("calls", json.dumps(calls_job(sb, a.tickers, step=a.mode, workers=a.workers, time_budget=a.budget, analyze_limit=a.limit, recent_only=a.recent), default=str)[:3000])
    if a.job == "index":
        print("index", jobs.build_index(sb))
    if a.job == "feed":
        print("feed", jobs.build_feed(sb))


if __name__ == "__main__":
    main()
