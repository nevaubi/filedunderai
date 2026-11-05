"""Explicit authenticated profile refresh; not scheduled by default."""
import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0,os.path.dirname(os.path.abspath(__file__)))
from _common import serve


def run(sb):
    from etl.company_profiles import refresh_profiles
    # URL query parameters cannot widen the server's explicit spend allowlist.
    tickers=os.environ.get('PDL_COMPANY_TICKERS','').split(',')
    return refresh_profiles(sb,tickers)


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        serve(self,run)
