"""Disclosure redlines (etl/redlines.py): companies never compared first, then a rotating slice re-checked for a
newer 10-K or 10-Q; writes redlines/{T}.json and the coverage index."""
import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import serve  # noqa: E402


def run(sb, query):
    from etl import redlines
    return redlines.run(sb, time_budget=640)


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        serve(self, run)
