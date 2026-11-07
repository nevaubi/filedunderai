"""Estimates refresh (etl/estimates_sources.py). step=consensus | ratings | build; dry_run=1 plans without calls or writes.
step=earnings runs the earnings workflow (etl/earnings.py; part=all | live | preview | calls | season)."""
import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import serve  # noqa: E402


def run(sb, query):
    step = query.get("step", "consensus")
    if step == "earnings":
        # Earnings workflow (etl/earnings.py): EDGAR feed -> scorecards, preview packs, call diffs, season view.
        from etl import earnings
        from etl.common import Run
        with Run(sb, "earnings") as r:
            r.stats.update(earnings.job(sb, step=query.get("part", "all"), time_budget=int(query.get("budget", 270))))
            return r.stats
    from etl import estimates_sources
    if step not in ("consensus", "ratings", "build"):
        raise ValueError("step must be consensus, ratings, build or earnings")
    return estimates_sources.job(sb, step=step, dry_run=query.get("dry_run") in ("1", "true"),
                                 limit=int(query["limit"]) if query.get("limit", "").isdigit() else None)


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        serve(self, run)
