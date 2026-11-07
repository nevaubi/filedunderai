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
    if step == "changes":
        # The "what changed" feed reads the calendars plus filings, insiders, estimates, redlines and alt data.
        from etl import changes
        return changes.changes_job(sb, time_budget=700)
    if step == "situations":
        # Situation screens (etl/situations.py) share this function; daily, after the screener build.
        from etl import situations
        return situations.situations_job(sb, time_budget=700)
    raise ValueError("step must be dividends, ipos, changes or situations")


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        serve(self, run)
