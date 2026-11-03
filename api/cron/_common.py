"""Shared plumbing for the scheduled ETL jobs (Vercel cron)."""
import json
import os
import sys
import inspect
import traceback
import urllib.parse

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def send(req, code, obj):
    body = json.dumps(obj, default=str).encode()
    req.send_response(code)
    req.send_header("Content-Type", "application/json")
    req.send_header("Cache-Control", "no-store")
    req.end_headers()
    req.wfile.write(body)


def serve(req, run):
    """Checks the cron secret, runs the job and writes a JSON response."""
    secret = os.environ.get("CRON_SECRET")
    if not secret or req.headers.get("Authorization") != f"Bearer {secret}":
        return send(req, 401, {"error": "unauthorized"})
    try:
        from etl.common import Supabase
        query = {k: v[-1] for k, v in urllib.parse.parse_qs(urllib.parse.urlparse(req.path).query).items()}
        result = run(Supabase(), query) if len(inspect.signature(run).parameters) > 1 else run(Supabase())
        return send(req, 200, {"ok": True, "result": result})
    except Exception as e:
        traceback.print_exc()
        return send(req, 500, {"ok": False, "error": str(e)[:500]})
