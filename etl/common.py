"""Shared plumbing: environment, HTTP with retries, Supabase REST and Storage."""
import datetime as dt
import gzip
import hashlib
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request


def load_env(path=None):
    path = path or os.environ.get("FU_ENV_FILE")
    if path and os.path.exists(path):
        for line in open(path):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k, v.strip().strip('"'))


load_env()

SEC_UA = os.environ.get("SEC_USER_AGENT") or "FiledUnder research contact@filedunder.ai"
WEB_UA = "FiledUnder/1.0 (+https://filedunder.ai)"


def now_iso():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class HTTPError(RuntimeError):
    def __init__(self, code, body):
        super().__init__(f"HTTP {code}: {body[:300]}")
        self.code = code
        self.body = body


def request(url, method="GET", data=None, headers=None, timeout=60, retries=3, raw=False):
    hdrs = {"User-Agent": WEB_UA, "Accept-Encoding": "gzip"}
    hdrs.update(headers or {})
    body = None
    if data is not None:
        if isinstance(data, (bytes, bytearray)):
            body = bytes(data)
        else:
            body = json.dumps(data).encode()
            hdrs.setdefault("Content-Type", "application/json")
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, data=body, method=method, headers=hdrs)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                content = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    content = gzip.decompress(content)
                return content if raw else content.decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            text = e.read()
            try:
                text = (gzip.decompress(text) if e.headers.get("Content-Encoding") == "gzip" else text).decode("utf-8", "replace")
            except Exception:
                text = str(text)[:300]
            last = HTTPError(e.code, text)
            if e.code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                time.sleep(2.5 * (attempt + 1) ** 2)
                continue
            raise last
        except Exception as e:  # network errors
            last = e
            if attempt < retries - 1:
                time.sleep(2 * (attempt + 1))
                continue
            raise
    raise last


def get_json(url, headers=None, timeout=60, retries=3):
    return json.loads(request(url, headers=headers, timeout=timeout, retries=retries))


_sec_lock = threading.Lock()
_sec_last = [0.0]


def sec_throttle(min_gap=0.13):
    """Keeps the process under SEC's 10 requests per second across threads."""
    with _sec_lock:
        wait = _sec_last[0] + min_gap - time.time()
        if wait > 0:
            time.sleep(wait)
        _sec_last[0] = time.time()


def sec_json(url):
    sec_throttle()
    return get_json(url, headers={"User-Agent": SEC_UA}, timeout=60, retries=4)


def sec_get(url, timeout=60):
    """Fetches a document from sec.gov with a declared user agent."""
    sec_throttle()
    return request(url, headers={"User-Agent": SEC_UA}, timeout=timeout, retries=3)


class Supabase:
    def __init__(self):
        self.url = os.environ["SUPABASE_URL"].rstrip("/")
        self.key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]

    def _h(self, extra=None):
        h = {"apikey": self.key, "Authorization": f"Bearer {self.key}"}
        h.update(extra or {})
        return h

    def upsert(self, table, rows, on_conflict, batch=500):
        rows = list(rows)
        for i in range(0, len(rows), batch):
            chunk = rows[i:i + batch]
            request(f"{self.url}/rest/v1/{table}?on_conflict={on_conflict}", method="POST", data=chunk,
                    headers=self._h({"Prefer": "resolution=merge-duplicates,return=minimal", "Content-Type": "application/json"}), timeout=120)
        return len(rows)

    def insert(self, table, row, returning=True):
        out = request(f"{self.url}/rest/v1/{table}", method="POST", data=row,
                      headers=self._h({"Prefer": "return=representation" if returning else "return=minimal"}))
        return json.loads(out) if returning and out else None

    def update(self, table, match, values):
        q = "&".join(f"{k}=eq.{urllib.parse.quote(str(v))}" for k, v in match.items())
        request(f"{self.url}/rest/v1/{table}?{q}", method="PATCH", data=values, headers=self._h({"Prefer": "return=minimal"}))

    def select(self, table, query="select=*"):
        out, offset = [], 0
        while True:
            page = json.loads(request(f"{self.url}/rest/v1/{table}?{query}", headers=self._h({"Range-Unit": "items", "Range": f"{offset}-{offset + 999}"})))
            out += page
            if len(page) < 1000:
                return out
            offset += 1000

    def put_object(self, bucket, path, content, content_type="application/json", cache="max-age=300"):
        if isinstance(content, str):
            content = content.encode()
        request(f"{self.url}/storage/v1/object/{bucket}/{urllib.parse.quote(path)}", method="POST", data=content,
                headers=self._h({"Content-Type": content_type, "x-upsert": "true", "cache-control": cache}), timeout=180)
        return len(content)

    def get_object(self, bucket, path, public=False):
        base = f"{self.url}/storage/v1/object/{'public/' if public else ''}{bucket}/{urllib.parse.quote(path)}"
        try:
            return request(base, headers=self._h(), raw=True, retries=2)
        except HTTPError as e:
            if e.code in (400, 404):
                return None
            raise

    def put_json(self, path, obj, cache="max-age=300"):
        body = json.dumps(obj, separators=(",", ":"), default=str)
        return self.put_object("public-data", path, body, "application/json", cache)

    def get_json(self, path):
        b = self.get_object("public-data", path)
        return json.loads(b) if b else None

    def put_raw(self, path, content, ticker=None, accession=None, kind="raw", source_url=None):
        data = content.encode() if isinstance(content, str) else content
        gz = gzip.compress(data)
        self.put_object("raw", path + ".gz", gz, "application/gzip", cache="no-store")
        self.upsert("documents", [{
            "id": path, "ticker": ticker, "accession": accession, "kind": kind, "source_url": source_url,
            "storage_path": "raw/" + path + ".gz", "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(), "fetched_at": now_iso(),
        }], "id")

    def rpc(self, fn, args):
        return json.loads(request(f"{self.url}/rest/v1/rpc/{fn}", method="POST", data=args, headers=self._h()) or "null")


class Run:
    """Records an etl_runs row for a job."""

    def __init__(self, sb, job):
        self.sb, self.job, self.stats, self.id = sb, job, {}, None

    def __enter__(self):
        try:
            r = self.sb.insert("etl_runs", {"job": self.job})
            self.id = r[0]["id"] if r else None
        except Exception as e:
            print("etl_runs insert failed:", e)
        self.t0 = time.time()
        return self

    def __exit__(self, et, ev, tb):
        self.stats["seconds"] = round(time.time() - self.t0, 1)
        if self.id:
            try:
                self.sb.update("etl_runs", {"id": self.id}, {"finished_at": now_iso(), "status": "error" if ev else "ok", "stats": self.stats, "error": str(ev)[:2000] if ev else None})
            except Exception as e:
                print("etl_runs update failed:", e)
        publish_status(self.sb)
        return False


def publish_status(sb):
    """Writes meta/status.json: the latest runs per job, for the Data status page."""
    try:
        rows = sb.select("etl_runs", "select=job,started_at,finished_at,status,stats,error&order=started_at.desc&limit=200")
        latest, recent = {}, []
        for r in rows:
            if r.get("error"):
                r["error"] = r["error"][:300]
            if r["job"] not in latest:
                latest[r["job"]] = r
            if len(recent) < 30:
                recent.append(r)
        last_ok = {}
        for r in rows:
            if r.get("status") == "ok" and r["job"] not in last_ok:
                last_ok[r["job"]] = r.get("finished_at")
        sb.put_json("meta/status.json", {"generated": now_iso(), "latest": latest, "last_ok": last_ok, "recent": recent}, cache="max-age=60")
    except Exception as e:
        print("status publish failed:", e)
