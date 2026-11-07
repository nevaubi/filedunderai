import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import serve  # noqa: E402


def run(sb, query=None):
    from etl import jobs
    if (query or {}).get("step") == "calls":
        # Earnings-call transcripts and prepared remarks among the stored IR documents: deterministic parse, one
        # quote-verified model analysis per call, calls/*.json bundles and cross-company theme/people files.
        from etl import calls
        calls.LLM_DOWN.clear()  # module state survives warm invocations
        return calls.calls_job(sb, workers=4, time_budget=540)
    if (query or {}).get("step") == "kpi":
        # KPI extraction from stored earnings releases and supplements (model proposes, kpitools verifies each value
        # against its page, only verified values are published). Light on memory; the model calls set the pace.
        from etl.kpi_extract import kpi_extract_job
        return kpi_extract_job(sb, budget=640, workers=4)
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
