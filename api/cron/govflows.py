import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import serve  # noqa: E402


def run(sb, query):
    """Insiders & influence pipeline (gov/ and flows/ bundles). step = daily | hearings | company | holders."""
    from etl import gov_pipeline
    step = query.get("step", "daily")
    if step not in ("daily", "hearings", "company", "holders"):
        raise ValueError("step must be daily, hearings, company or holders")
    return gov_pipeline.scheduled(sb, step, time_budget=680)


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        serve(self, run)
