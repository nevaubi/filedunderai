import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import serve  # noqa: E402


def run(sb):
    # Loads any SEC Financial Statement Data Sets quarter not yet loaded (or republished by SEC); resumes a partial one.
    from etl.statements import statements_job
    return statements_job(sb, time_budget=720, workdir="/tmp", workers=8)


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        serve(self, run)
