import os
import sys
import time
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import serve  # noqa: E402


LEASE_WAIT = 150  # seconds to wait for another aggregate publisher (IR coverage, KPI extraction) to release the lease


def run(sb, query):
    from etl import jobs
    from etl.common import LeaseBusy
    parts = int(query.get("parts", 8))
    part = int(query.get("part", 0))
    t0 = time.time()
    while True:
        try:
            # Parts dropped on LeaseBusy left the price files two days behind (Nov 5-6); wait instead.
            n, errors = jobs.prices_job(sb, intraday=True, part=part, parts=parts, time_budget=max(100, 250 - (time.time() - t0)))
            return {"part": part, "symbols": n, "errors": len(errors), "waited": round(time.time() - t0)}
        except LeaseBusy:
            if time.time() - t0 > LEASE_WAIT:
                raise
            time.sleep(10)


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        serve(self, run)
