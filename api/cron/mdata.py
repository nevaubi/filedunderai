import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import serve  # noqa: E402


def run(sb, q):
    from etl.macrojobs import mdata_job
    return mdata_job(sb, q.get("source", "catalog"))


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        serve(self, run)
