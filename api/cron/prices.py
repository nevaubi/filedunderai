import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import make_handler  # noqa: E402


def run(sb):
    from etl import jobs
    n, errors = jobs.prices_job(sb, intraday=True)
    return {"symbols": n, "errors": errors}


handler = make_handler(run)
