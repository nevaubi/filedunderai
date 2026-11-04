import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import serve  # noqa: E402


def run(sb, query):
    """New transcripts and prepared remarks from the IR collector: parse, analyse (recent quarters first, then the
    backfill of older calls), publish calls/*.json and copy rows into the calls tables. ?step=publish rebuilds only
    the cross-company files."""
    from etl.calls import calls_job
    if query.get("step") == "publish":
        return calls_job(sb, step="publish")
    return calls_job(sb, workers=4, time_budget=170)


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        serve(self, run)
