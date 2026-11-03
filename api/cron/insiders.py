import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import serve  # noqa: E402


def run(sb):
    from etl import jobs
    done, failed = jobs.insiders_job(sb, workers=4, time_budget=250)
    return {"parsed": done, "failed": failed}


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        serve(self, run)
