"""Estimates refresh (etl/estimates_sources.py). step=consensus | ratings | build; dry_run=1 plans without calls or writes."""
import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import serve  # noqa: E402


def run(sb, query):
    from etl import estimates_sources
    step = query.get("step", "consensus")
    if step not in ("consensus", "ratings", "build"):
        raise ValueError("step must be consensus, ratings or build")
    return estimates_sources.job(sb, step=step, dry_run=query.get("dry_run") in ("1", "true"),
                                 limit=int(query["limit"]) if query.get("limit", "").isdigit() else None)


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        serve(self, run)
