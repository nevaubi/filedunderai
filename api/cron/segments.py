import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import serve  # noqa: E402


def run(sb):
    from etl import jobs
    done, failed = jobs.segments_changed_job(sb, time_budget=240)
    return {"updated": list(done), "failed": failed}


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        serve(self, run)
