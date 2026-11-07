"""Company briefs (etl/company_brief.py): companies whose cross-source facts changed since their last brief,
oldest brief first; drafts, verifies, checks and writes brief/{T}.json."""
import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import serve  # noqa: E402


def run(sb, query):
    from etl import company_brief
    return company_brief.run(sb, time_budget=700, per_run=int(query.get("n", "15")))


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        serve(self, run)
