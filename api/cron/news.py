import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import serve  # noqa: E402


def run(sb):
    from etl import jobs
    done, failed = jobs.news_rotation_job(sb, per_run=10, workers=4, time_budget=230)
    return {"updated": done, "failed": failed}


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        serve(self, run)
