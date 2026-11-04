import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import serve  # noqa: E402


def run(sb):
    from etl import jobs
    done, failed = jobs.ir_rotation_job(sb, per_run=8, time_budget=230)
    return {"updated": list(done), "failed": failed, "coverage": {tk: result.get("coverage") for tk, result in done.items()},
            "deferred": sum(result.get("deferred", 0) for result in done.values())}


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        serve(self, run)
