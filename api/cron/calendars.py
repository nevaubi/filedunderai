import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import serve  # noqa: E402


def run(sb, query):
    from etl import calendars
    step = query.get("step", "dividends")
    if step == "dividends":
        return calendars.dividends_job(sb, time_budget=700)
    if step == "ipos":
        return calendars.ipos_job(sb, time_budget=700)
    raise ValueError("step must be dividends or ipos")


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        serve(self, run)
