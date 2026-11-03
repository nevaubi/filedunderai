import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import serve  # noqa: E402


def run(sb):
    from etl import jobs
    out = jobs.macro_job(sb)
    return {"curve_rows": len(out["curve"]), "news": len(out["news"]), "brief": bool(out.get("brief"))}


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        serve(self, run)
