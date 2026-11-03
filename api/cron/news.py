import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import make_handler  # noqa: E402


def run(sb):
    from etl import jobs
    done, failed = jobs.news_rotation_job(sb, per_run=10, workers=4, time_budget=230)
    return {"updated": done, "failed": failed}


handler = make_handler(run)
