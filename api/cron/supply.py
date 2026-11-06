import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import serve  # noqa: E402


def run(sb, query):
    # step=rotate (default): read companies never processed, then those with a new 10-K/10-Q; step=intel: regions,
    # relationship news, sympathy moves and AI insights for companies whose links changed. Both stay inside 680 s.
    from etl import supply
    return supply.run(sb, step=query.get("step", "rotate"))


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        serve(self, run)
