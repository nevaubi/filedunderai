import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import serve  # noqa: E402


def run(sb, query):
    from etl import jobs
    step = query.get("step", "publish")
    if step not in ("prices", "publish", "notes"):
        step = "publish"
    return jobs.sectors_job(sb, step, time_budget=240 if step != "notes" else None)


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        serve(self, run)
