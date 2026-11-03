import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import serve  # noqa: E402


def run(sb):
    from etl import jobs
    n, errors = jobs.prices_job(sb, intraday=True)
    return {"symbols": n, "errors": errors}


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        serve(self, run)
