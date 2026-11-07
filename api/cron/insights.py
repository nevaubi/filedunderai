"""Filing insights (etl/filing_insights.py): companies whose disclosure redline is newer than their last insight run,
most recent filings first; drafts, verifies, checks and ranks, then writes insights/{T}.json."""
import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import serve  # noqa: E402


def run(sb, query):
    from etl import filing_insights
    return filing_insights.run(sb, time_budget=700, max_companies=int(query.get("n", "10")))


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        serve(self, run)
