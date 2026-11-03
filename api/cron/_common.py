"""Shared handler plumbing for the scheduled ETL jobs (Vercel cron)."""
import json
import os
import sys
import traceback
from http.server import BaseHTTPRequestHandler

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def make_handler(run):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            secret = os.environ.get("CRON_SECRET")
            if not secret or self.headers.get("Authorization") != f"Bearer {secret}":
                return self._send(401, {"error": "unauthorized"})
            try:
                from etl.common import Supabase
                result = run(Supabase())
                return self._send(200, {"ok": True, "result": result})
            except Exception as e:
                traceback.print_exc()
                return self._send(500, {"ok": False, "error": str(e)[:500]})

        def _send(self, code, obj):
            body = json.dumps(obj, default=str).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

    return Handler
