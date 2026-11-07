"""Hourly: company briefs (etl/company_brief.py), companies whose cross-source facts changed since their last brief,
oldest brief first (drafts, verifies, checks and writes brief/{T}.json); then, in the time left, quant reads
(etl/quant.run_reads) for symbols whose statistics changed since their last read."""
import os
import sys
import time
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import serve  # noqa: E402


def run(sb, query):
    from etl import company_brief, quant
    from etl.universe import UNIVERSE
    t0 = time.time()
    out = {"brief": company_brief.run(sb, time_budget=560, per_run=int(query.get("n", "25")), workers=4)}
    left = 740 - (time.time() - t0)
    if left > 90:
        out["reads"] = quant.run_reads(sb, [t for t, *_ in UNIVERSE], deadline=time.time() + left - 30, limit=int(query.get("reads", "60")),
                                       workers=4, only_changed=True)
    return out


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        serve(self, run)
