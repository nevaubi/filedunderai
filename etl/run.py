"""Command line entry: python -m etl.run <job> [options]."""
import argparse
import json

from . import jobs
from .common import Supabase


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("job", choices=["companies", "prices", "macro", "index", "feed", "news", "insiders", "logos", "viewer", "segments", "ir", "compare", "technicals", "all"])
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
    ap.add_argument("--backfill", action="store_true", help="technicals job: fetch full daily history for symbols without a history file")
    ap.add_argument("--force", action="store_true", help="technicals backfill: refetch even when a history file exists")
    ap.add_argument("--events", action="store_true", help="technicals job: EDGAR 8-K/10-K/10-Q history since 2008 per company")
    ap.add_argument("--merge", action="store_true", help="technicals job: only merge the part results")
    ap.add_argument("--pace", type=float, default=2.0, help="technicals backfill: seconds between Yahoo requests")
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
    if a.job == "viewer":
        print("viewer copies", jobs.publish_filings_job(sb, a.tickers, workers=a.workers))
    if a.job == "logos":
        from .logos import logos_job
        print("logos", logos_job(sb, a.tickers))
    if a.job == "compare":
        from .compare import compare_job
        done, errors = compare_job(sb, a.tickers, time_budget=a.budget, full=a.full_history, workers=min(a.workers, 4))
        print("compare history", len(done), "errors", errors)
    if a.job == "technicals":
        if a.events:
            from . import technicals
            done, failed = technicals.events_backfill(sb, a.tickers, force=a.force)
            print("technicals events", done, "failed", json.dumps(failed)[:2000])
        elif a.merge:
            print("technicals merge", json.dumps(jobs.technicals_job(sb, merge=True), default=str)[:2000])
        elif a.backfill:
            from . import technicals
            done, failed = technicals.backfill(sb, a.tickers, force=a.force, pace=a.pace)
            print("technicals backfill", len(done), "failed", json.dumps(failed)[:2000])
        else:
            print("technicals", json.dumps(jobs.technicals_job(sb, part=a.part, parts=a.parts if a.parts > 1 else 4), default=str)[:3000])
    if a.job == "index":
        print("index", jobs.build_index(sb))
    if a.job == "feed":
        print("feed", jobs.build_feed(sb))


if __name__ == "__main__":
    main()
