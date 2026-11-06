import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import serve  # noqa: E402


def run(sb, query=None):
    from etl import jobs
    # run=fresh: first document pass for newly added issuers only (catch-up after a cohort is activated).
    fresh = (query or {}).get("run") == "fresh"
    done, failed = jobs.ir_rotation_job(sb, per_run=16 if fresh else 20, time_budget=640, fresh_only=fresh)
    return {"updated": list(done), "failed": failed, "coverage": {tk: result.get("coverage") for tk, result in done.items()},
            "deferred": sum(result.get("deferred", 0) for result in done.values())}


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        serve(self, run)
