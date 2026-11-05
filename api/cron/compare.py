import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import serve  # noqa: E402


def run(sb):
    from etl.compare import compare_job
    done, errors = compare_job(sb, time_budget=700)
    return {"history": len(done), "errors": len(errors)}


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        serve(self, run)
