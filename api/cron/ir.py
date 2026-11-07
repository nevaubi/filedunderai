import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import serve  # noqa: E402


def run(sb, query=None):
    from etl import jobs
    # run=fresh: first document pass for newly added issuers only (catch-up after a cohort is activated).
    fresh = (query or {}).get("run") == "fresh"
    # Sized for the function's memory and 800 s limit: since Nov 6 runs with 16-20 companies and 4-6 workers were
    # killed for memory (32 times) or at the time limit (25 times), so the rotation checkpoint never advanced.
    done, failed = jobs.ir_rotation_job(sb, per_run=6 if fresh else 10, time_budget=560, fresh_only=fresh)
    return {"updated": list(done), "failed": failed, "coverage": {tk: result.get("coverage") for tk, result in done.items()},
            "deferred": sum(result.get("deferred", 0) for result in done.values())}


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        serve(self, run)
