import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import serve  # noqa: E402


def run(sb):
    # incremental: only companies whose KPI definitions changed since the last run; the rest finishes on the next run
    from etl import jobs
    return jobs.kpistd_job(sb, time_budget=640)


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        serve(self, run)
