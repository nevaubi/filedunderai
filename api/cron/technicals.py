import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import serve  # noqa: E402


def run(sb, query):
    from etl import jobs
    parts = int(query.get("parts", 4))
    if query.get("merge"):
        return jobs.technicals_job(sb, merge=True, parts=parts)
    return jobs.technicals_job(sb, part=int(query.get("part", 0)), parts=parts, time_budget=240)


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        serve(self, run)
