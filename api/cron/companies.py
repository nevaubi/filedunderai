import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import make_handler  # noqa: E402


def run(sb):
    from etl import jobs
    done, failed = jobs.companies_job(sb, only_changed=True, time_budget=240, max_companies=6, workers=3)
    return {"updated": done, "failed": failed}


handler = make_handler(run)
