import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import serve  # noqa: E402


def run(sb):
    # documents the IR job stored in the last four days (it runs three times a day); extraction then the company and index files
    from etl.guidance import guidance_job
    return guidance_job(sb, recent_days=4, time_budget=250, workers=4, doc_workers=3)


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        serve(self, run)
