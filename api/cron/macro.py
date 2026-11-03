import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import make_handler  # noqa: E402


def run(sb):
    from etl import jobs
    out = jobs.macro_job(sb)
    return {"curve_rows": len(out["curve"]), "news": len(out["news"]), "brief": bool(out.get("brief"))}


handler = make_handler(run)
