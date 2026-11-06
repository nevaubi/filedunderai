"""Pre-generated research documents (etl/reports.py): earnings previews before scheduled reports, recaps after each
earnings 8-K, then stale company primers, capped per run (REPORTS_MAX_PER_RUN)."""
import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import serve  # noqa: E402


def run(sb, query):
    from etl import reports
    return reports.run(sb, time_budget=640)


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        serve(self, run)
