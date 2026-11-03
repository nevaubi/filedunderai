import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import serve  # noqa: E402


def run(sb, query):
    from etl import jobs
    parts = int(query.get("parts", 4))
    part = int(query.get("part", 0))
    n, errors = jobs.prices_job(sb, intraday=True, part=part, parts=parts)
    return {"part": part, "symbols": n, "errors": len(errors)}


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        serve(self, run)
