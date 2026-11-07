import os
import sys
import time
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import serve  # noqa: E402

LEASE_WAIT = 240  # seconds to wait for another aggregate publisher (IR, companies) to release the lease


def run(sb):
    from etl import jobs
    from etl.common import LeaseBusy
    t0 = time.time()
    while True:
        try:
            # The job's own budget shrinks by however long it waited for the lease.
            return jobs.screener_job(sb, time_budget=max(420, 720 - (time.time() - t0)), workers=8)
        except LeaseBusy:
            if time.time() - t0 > LEASE_WAIT:
                raise
            time.sleep(15)


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        serve(self, run)
