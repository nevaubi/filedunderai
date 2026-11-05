"""Command line entry: python -m etl.run <job> [options]."""
import argparse
import json

from . import jobs
from .common import Supabase


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("job", choices=["companies", "prices", "macro", "index", "feed", "news", "insiders", "logos", "viewer", "segments", "ir", "compare", "sectors", "screener", "technicals", "mdata", "kpistd", "statements", "altdata", "all"])
    ap.add_argument("--tickers", nargs="*")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--news", action="store_true", help="refresh news for each company")
    ap.add_argument("--summary", action="store_true", help="regenerate summaries")
    ap.add_argument("--no-docs", action="store_true")
    ap.add_argument("--facts-only", action="store_true", help="companies: validate archived SEC values, disable news, summaries and unarchived gap fill")
    ap.add_argument("--full-history", action="store_true")
    ap.add_argument("--part", type=int)
    ap.add_argument("--parts", type=int, default=1)
    ap.add_argument("--changed", action="store_true", help="only companies with new 10-K/10-Q/8-K filings")
    ap.add_argument("--sec-only", action="store_true", help="IR job: EDGAR exhibits only, skip the IR website")
    ap.add_argument("--budget", type=float, help="time budget in seconds (companies, IR, compare, sectors)")
    ap.add_argument("--repair-existing", action="store_true", help="IR: repair extraction/dates using stored originals only")
    ap.add_argument("--publish-only", action="store_true", help="IR: reconcile manifests from existing rows without providers")
    ap.add_argument("--max-docs", type=int, default=90, help="IR: per-company new/repair document work cap")
    ap.add_argument("--max-pages", type=int, default=40, help="IR: per-source per-company discovery work cap")
    ap.add_argument("--refresh-site", action="store_true", help="IR: refresh discovery and reset blocked retries")
    ap.add_argument("--sec-years", type=float, default=3, help="IR job: years of EDGAR 8-K earnings exhibits to collect")
    ap.add_argument("--step", default="publish", choices=["prices", "publish", "notes"], help="sectors job step")
    ap.add_argument("--backfill", action="store_true", help="technicals job: fetch full daily history for symbols without a history file")
    ap.add_argument("--force", action="store_true", help="technicals backfill: refetch existing history; mdata: reprocess Fed files even if unchanged; statements: reload loaded quarters")
    ap.add_argument("--events", action="store_true", help="technicals job: EDGAR 8-K/10-K/10-Q history since 2008 per company")
    ap.add_argument("--merge", action="store_true", help="technicals job: only merge the part results")
    ap.add_argument("--pace", type=float, default=2.0, help="technicals backfill: seconds between Yahoo requests")
    ap.add_argument("--source", help="mdata job: frb_daily, frb_weekly, frb_monthly, nyfed, treasury, bls, oecd, cftc, fdic, calendar, legacy, catalog")
    ap.add_argument("--no-llm", action="store_true", help="kpistd job: rules only, no model proposals")
    ap.add_argument("--quarters", nargs="*", help="statements job: quarters to load, e.g. 2024q1 (default: every quarter not yet loaded)")
    ap.add_argument("--no-db", action="store_true", help="statements job: publish bundles only, skip the fs_* tables")
    ap.add_argument("--workdir", help="statements job: where quarterly ZIPs are kept while they load")
    ap.add_argument("--defer", action="store_true", help="statements job: publish ticker index files once at the end (backfills)")
    a = ap.parse_args()
    sb = Supabase()
    if a.job in ("prices", "all"):
        print("prices", jobs.prices_job(sb, full_history=a.full_history, part=a.part, parts=a.parts))
    if a.job in ("macro", "all"):
        jobs.macro_job(sb)
        print("macro ok")
    if a.job in ("companies", "all"):
        print(json.dumps(jobs.companies_job(sb, a.tickers, workers=a.workers, refresh_news=a.news, refresh_summary=a.summary, fetch_docs=not a.no_docs, only_changed=a.changed, time_budget=a.budget, facts_only=True if a.facts_only else None), default=str)[:2000])
    if a.job == "news":
        print(jobs.news_rotation_job(sb))
    if a.job in ("insiders", "all"):
        print("insiders", jobs.insiders_job(sb, a.tickers, workers=a.workers))
    if a.job == "ir":
        done, failed = jobs.ir_job(sb, a.tickers, workers=a.workers, web=not a.sec_only, sec_years=a.sec_years, time_budget=a.budget,
                                   max_docs=a.max_docs, max_pages=a.max_pages, repair_existing=a.repair_existing, publish_only=a.publish_only,
                                   refresh_site=a.refresh_site, backfill=a.full_history)
        print("ir", len(done), "failed", failed)
    if a.job == "altdata":
        from . import altdata
        done, failed = altdata.altdata_job(sb, a.tickers, workers=a.workers, time_budget=a.budget)
        print("altdata", len(done), "failed", json.dumps(failed)[:2000])
    if a.job == "segments":
        done, failed = jobs.segments_job(sb, a.tickers, workers=a.workers)
        print("segments", len(done), "failed", failed)
    if a.job == "sectors":
        print("sectors", json.dumps(jobs.sectors_job(sb, a.step, time_budget=a.budget), default=str)[:3000])
    if a.job == "statements":
        from .statements import statements_job
        print("statements", json.dumps(statements_job(sb, quarters=a.quarters, force=a.force, db=not a.no_db, workdir=a.workdir, workers=a.workers, defer=a.defer), default=str)[:2000])
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
    if a.job == "mdata":
        from .macrojobs import mdata_job
        print("mdata", json.dumps(mdata_job(sb, a.source, full=a.full_history, force=a.force), default=str)[:2000])
    if a.job == "kpistd":
        print("kpistd", json.dumps(jobs.kpistd_job(sb, a.tickers, full=a.full_history, use_llm=not a.no_llm, time_budget=a.budget), default=str))
    if a.job == "index":
        print("index", jobs.build_index(sb))
    if a.job == "feed":
        print("feed", jobs.build_feed(sb))


if __name__ == "__main__":
    main()
