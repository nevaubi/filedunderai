"""POST /api/export: Excel workbook for the KPI matrix page.

Body: {"tickers": [...], "kpis": [...], "basis": "Q"|"A", "label": "...", "hide_stale": bool}.
Same-site only (like api/_lib/guard.js), with per-IP and global daily caps from the bump_usage() Postgres function.
Data is read from the public-data bucket and cached for a few minutes per warm instance.
"""
import concurrent.futures as cf
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from etl import matrix_export as mx  # noqa: E402

ALLOWED_HOSTS = [re.compile(p) for p in (r"^filedunder\.ai$", r"^www\.filedunder\.ai$", r"^filedunderai(-[a-z0-9-]+)?\.vercel\.app$",
                                         r"^localhost(:\d+)?$", r"^127\.0\.0\.1(:\d+)?$")]
PER_IP, GLOBAL = 60, 3000
MAX_BODY = 64 * 1024
TTL = 600
_CACHE = {}


def _base():
    url = (os.environ.get("SUPABASE_URL") or "").rstrip("/")
    return f"{url}/storage/v1/object/public/public-data/"


def _fetch(path):
    hit = _CACHE.get(path)
    if hit and time.time() - hit[0] < TTL:
        return hit[1]
    req = urllib.request.Request(_base() + path, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=40) as r:
        data = json.loads(r.read())
    _CACHE[path] = (time.time(), data)
    return data


def _host(u):
    try:
        return urllib.parse.urlparse(u).netloc
    except ValueError:
        return ""


def _bump(bucket):
    url, key = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        return 0
    req = urllib.request.Request(f"{url.rstrip('/')}/rest/v1/rpc/bump_usage", method="POST",
                                 data=json.dumps({"p_bucket": bucket, "p_limit": 0}).encode(),
                                 headers={"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        try:
            return int(json.loads(r.read()) or 0)
        except (TypeError, ValueError):
            return 0


class handler(BaseHTTPRequestHandler):
    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._json(405, {"error": "Use POST."})

    def do_POST(self):
        origin = self.headers.get("Origin") or self.headers.get("Referer") or ""
        if not any(rx.match(_host(origin)) for rx in ALLOWED_HOSTS):
            return self._json(403, {"error": "This endpoint only serves filedunder.ai"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = 0
        if n <= 0 or n > MAX_BODY:
            return self._json(400, {"error": "Request body missing or too large."})
        try:
            body = json.loads(self.rfile.read(n))
        except (ValueError, UnicodeDecodeError):
            return self._json(400, {"error": "Request body is not valid JSON."})
        ip = (self.headers.get("X-Forwarded-For") or "").split(",")[0].strip() or "unknown"
        day = dt.date.today().isoformat()
        try:
            if _bump(f"export:{day}:{ip}") > PER_IP or _bump(f"export:{day}:all") > GLOBAL:
                return self._json(429, {"error": "Daily export limit reached. Try again tomorrow."})
        except Exception:
            pass  # counter unavailable: fail open rather than block research
        try:
            taxonomy = _fetch("kpistd/taxonomy.json")
            req = mx.clean_request(body, {k["id"] for k in taxonomy.get("kpis") or []})
        except ValueError as e:
            return self._json(400, {"error": str(e)})
        except Exception:
            return self._json(502, {"error": "KPI definitions are unavailable right now."})
        try:
            paths = ["index.json", "kpistd/latest.json"] + [f"kpistd/series/{k}.json" for k in req["kpis"]]
            with cf.ThreadPoolExecutor(max_workers=8) as ex:
                got = dict(zip(paths, ex.map(lambda p: _safe(p), paths)))
            if not got.get("index.json"):
                return self._json(502, {"error": "Company data is unavailable right now."})
            series = {k: got.get(f"kpistd/series/{k}.json") or {} for k in req["kpis"]}
            xlsx, name = mx.build(req, got["index.json"], taxonomy, series, mx.industries_from_latest(got.get("kpistd/latest.json") or {}))
        except Exception as e:
            print("export failed:", repr(e)[:300])
            return self._json(500, {"error": "The workbook could not be built."})
        self.send_response(200)
        self.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        self.send_header("Content-Disposition", f'attachment; filename="{name}"')
        self.send_header("Content-Length", str(len(xlsx)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(xlsx)


def _safe(path):
    try:
        return _fetch(path)
    except Exception:
        return None
