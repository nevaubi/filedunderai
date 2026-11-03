import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import serve  # noqa: E402


def run(sb):
    from etl import jobs
    done, failed = jobs.companies_job(sb, only_changed=True, time_budget=240, max_companies=6, workers=3)
    return {"updated": done, "failed": failed}


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        serve(self, run)
