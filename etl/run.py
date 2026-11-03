"""Command line entry: python -m etl.run <job> [options]."""
import argparse
import json

from . import jobs
from .common import Supabase


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("job", choices=["companies", "prices", "macro", "index", "feed", "news", "insiders", "all"])
    ap.add_argument("--tickers", nargs="*")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--news", action="store_true", help="refresh news for each company")
    ap.add_argument("--summary", action="store_true", help="regenerate summaries")
    ap.add_argument("--no-docs", action="store_true")
    ap.add_argument("--full-history", action="store_true")
    a = ap.parse_args()
    sb = Supabase()
    if a.job in ("prices", "all"):
        print("prices", jobs.prices_job(sb, full_history=a.full_history))
    if a.job in ("macro", "all"):
        jobs.macro_job(sb)
        print("macro ok")
    if a.job in ("companies", "all"):
        print(json.dumps(jobs.companies_job(sb, a.tickers, workers=a.workers, refresh_news=a.news, refresh_summary=a.summary, fetch_docs=not a.no_docs), default=str)[:2000])
    if a.job == "news":
        print(jobs.news_rotation_job(sb))
    if a.job in ("insiders", "all"):
        print("insiders", jobs.insiders_job(sb, a.tickers, workers=a.workers))
    if a.job == "index":
        print("index", jobs.build_index(sb))
    if a.job == "feed":
        print("feed", jobs.build_feed(sb))


if __name__ == "__main__":
    main()
