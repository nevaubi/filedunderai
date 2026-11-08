"""Shared plumbing: environment, HTTP with retries, Supabase REST and Storage."""
import datetime as dt
import gzip
import functools
import uuid
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

# Model spend switch. While True, scheduled jobs run without the Fireworks key: every model step skips the way it does
# when the key is unset (rule-only outputs, previous AI text kept). Flip to False to resume.
LLM_PAUSED = True
if LLM_PAUSED and os.environ.pop("FIREWORKS_API_KEY", None) is not None:
    os.environ["FIREWORKS_PAUSED"] = "1"

SEC_UA = os.environ.get("SEC_USER_AGENT") or "FiledUnder research contact@filedunder.ai"
if "@" not in SEC_UA:
    # sec.gov Archives rejects agents without a contact address.
    SEC_UA = SEC_UA + " contact@filedunder.ai"
WEB_UA = "FiledUnder/1.0 (+https://filedunder.ai)"


def now_iso():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class HTTPError(RuntimeError):
    def __init__(self, code, body):
        super().__init__(f"HTTP {code}: {body[:300]}")
        self.code = code
        self.body = body


def request(url, method="GET", data=None, headers=None, timeout=60, retries=6, raw=False):
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
            # connection resets come in bursts when many jobs share the proxy, so back off longer before giving up
            if attempt < retries - 1:
                time.sleep(min(30, 3 * (attempt + 1) ** 1.5))
                continue
            raise
    raise last


def get_json(url, headers=None, timeout=60, retries=6):
    return json.loads(request(url, headers=headers, timeout=timeout, retries=retries))


_sec_lock = threading.Lock()
_sec_last = [0.0]


def _shared_throttle(path, min_gap):
    """Rate limit shared by every process on the machine (SEC_SHARED=path): the last request time lives in a locked file."""
    import fcntl
    with open(path, "a+") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        fh.seek(0)
        try:
            last = float(fh.read().strip() or 0)
        except ValueError:
            last = 0.0
        wait = last + min_gap - time.time()
        if wait > 0:
            time.sleep(wait)
        fh.seek(0)
        fh.truncate()
        fh.write(repr(time.time()))
        fh.flush()
        fcntl.flock(fh, fcntl.LOCK_UN)


def sec_throttle(min_gap=None):
    """Keeps the process under SEC's 10 requests per second across threads (and across processes with SEC_SHARED)."""
    min_gap = min_gap if min_gap is not None else float(os.environ.get("SEC_GAP", "0.13"))
    shared = os.environ.get("SEC_SHARED")
    if shared:
        with _sec_lock:
            _shared_throttle(shared, min_gap)
        return
    with _sec_lock:
        wait = _sec_last[0] + min_gap - time.time()
        if wait > 0:
            time.sleep(wait)
        _sec_last[0] = time.time()


def sec_json(url):
    sec_throttle()
    return get_json(url, headers={"User-Agent": SEC_UA}, timeout=60, retries=4)


def sec_get_raw(url, timeout=90):
    """Raw bytes from sec.gov (binary exhibits)."""
    sec_throttle()
    return request(url, headers={"User-Agent": SEC_UA}, timeout=timeout, retries=3, raw=True)


def sec_get(url, timeout=60):
    """Fetches a document from sec.gov with a declared user agent."""
    sec_throttle()
    return request(url, headers={"User-Agent": SEC_UA}, timeout=timeout, retries=3)


def dedupe_conflict_rows(rows, on_conflict):
    """Collapse rows that share the on_conflict key, keeping the last occurrence of each key.

    PostgREST runs one INSERT ... ON CONFLICT DO UPDATE per request, and Postgres rejects a
    statement that would update the same row twice (error 21000). Later rows win, matching the
    result of applying the batch in order. Rows missing any key column are passed through
    unchanged because their effective key depends on database defaults.
    """
    rows = list(rows)
    keys = [k.strip() for k in str(on_conflict or "").split(",") if k.strip()]
    if not keys:
        return rows
    positions, out = {}, []
    for row in rows:
        if not isinstance(row, dict) or any(k not in row for k in keys):
            out.append(row)
            continue
        try:
            ident = tuple(json.dumps(row[k], sort_keys=True, default=str) for k in keys)
        except (TypeError, ValueError):
            out.append(row)
            continue
        if ident in positions:
            out[positions[ident]] = None
        positions[ident] = len(out)
        out.append(row)
    return [row for row in out if row is not None]


class Supabase:
    def __init__(self):
        self.url = os.environ["SUPABASE_URL"].rstrip("/")
        self.key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]

    def _check_lease(self):
        lease = getattr(self, "_aggregate_lease", None)
        if lease:
            lease.check()

    def _h(self, extra=None):
        h = {"apikey": self.key, "Authorization": f"Bearer {self.key}"}
        h.update(extra or {})
        return h

    def upsert(self, table, rows, on_conflict, batch=500):
        self._check_lease()
        rows = dedupe_conflict_rows(rows, on_conflict)
        for i in range(0, len(rows), batch):
            self._check_lease()
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

    def delete(self, table, query):
        """Deletes rows matching a PostgREST filter query (e.g. "ticker=eq.MDLZ&source_kind=eq.ir")."""
        if not query:
            raise ValueError("delete needs a filter")
        request(f"{self.url}/rest/v1/{table}?{query}", method="DELETE", headers=self._h({"Prefer": "return=minimal"}))

    def select(self, table, query="select=*"):
        out, offset = [], 0
        while True:
            page = json.loads(request(f"{self.url}/rest/v1/{table}?{query}", headers=self._h({"Range-Unit": "items", "Range": f"{offset}-{offset + 999}"})))
            out += page
            if len(page) < 1000:
                return out
            offset += 1000

    def put_object(self, bucket, path, content, content_type="application/json", cache="max-age=300"):
        self._check_lease()
        if isinstance(content, str):
            content = content.encode()
        request(f"{self.url}/storage/v1/object/{bucket}/{urllib.parse.quote(path)}", method="POST", data=content,
                headers=self._h({"Content-Type": content_type, "x-upsert": "true", "cache-control": cache}), timeout=180)
        return len(content)

    def _storage_read_request(self, bucket, path, public=False):
        """Share freshness options without changing each caller's error or timeout rules."""
        base = f"{self.url}/storage/v1/object/{'public' if public else 'authenticated'}/{bucket}/{urllib.parse.quote(path)}"
        headers = {}
        if not public:
            # Authenticated downloads can also be CDN-cached. Request freshness with
            # Supabase's documented cacheNonce and cache directives; exact readback
            # comparisons remain authoritative if an intermediary still serves stale data.
            base += f"?cacheNonce={uuid.uuid4().hex}"
            headers = {"Cache-Control": "no-cache, no-store", "Pragma": "no-cache"}
        return base, self._h(headers)

    def get_object(self, bucket, path, public=False):
        base, headers = self._storage_read_request(bucket, path, public)
        try:
            return request(base, headers=headers, raw=True, retries=2)
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


class LeaseBusy(RuntimeError):
    pass


class AggregateLease:
    """Serialize aggregate publishers across machines using a renewable DB lease.

    Requires the additive etl_leases RPC migration. Never fall back to unsafe writes.
    A lost heartbeat stops subsequent guarded writes; publication is not transactional.
    """
    def __init__(self, sb, name="financial-aggregates", seconds=300):
        self.sb, self.name = sb, name
        self.seconds = max(30, min(seconds, 600))
        self.owner = str(uuid.uuid4())
        self.stop = threading.Event()
        self.lost = None

    def acquire(self):
        return self.sb.rpc("acquire_etl_lease", {"lock_name":self.name, "owner":self.owner, "lease_seconds":self.seconds}) is True

    def check(self):
        if getattr(self, "valid_until", float("inf")) <= time.monotonic():
            self.lost = "lease expired before publication"
        if self.lost:
            raise RuntimeError("Aggregate lease lost; refusing publication: " + self.lost)

    def __enter__(self):
        try:
            acquired = self.acquire()
        except Exception as e:
            raise RuntimeError("ETL lease unavailable; apply the etl_leases migration before aggregate publishing") from e
        if not acquired:
            raise LeaseBusy("Aggregate publisher already running; retry this job later")
        self.valid_until = time.monotonic() + self.seconds
        def renew():
            while not self.stop.wait(self.seconds / 3):
                try:
                    self.check()
                    if not self.acquire():
                        self.lost = "lease owned by another run"
                        return
                    self.check()
                    self.valid_until = time.monotonic() + self.seconds
                except Exception as e:
                    self.lost = str(e)
                    return
        self.thread = threading.Thread(target=renew, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, et, ev, tb):
        self.stop.set()
        self.thread.join(timeout=1)
        try:
            self.sb.rpc("release_etl_lease", {"lock_name":self.name, "owner":self.owner})
        except Exception as e:
            print("ETL lease release failed (will expire):", str(e)[:200])
        if not ev:
            self.check()
        return False


_aggregate_local = threading.local()


def serialized_aggregates(fn):
    @functools.wraps(fn)
    def wrapped(sb, *args, **kwargs):
        # Nested build_index shares its caller's lease on this thread only.
        held = getattr(_aggregate_local, "held", None)
        if held and held.sb is sb:
            held.check()
            return fn(sb, *args, **kwargs)
        with AggregateLease(sb) as lease:
            _aggregate_local.held = lease
            # Guard writes made by worker threads sharing this client as well.
            previous = getattr(sb, "_aggregate_lease", None)
            sb._aggregate_lease = lease
            try:
                return fn(sb, *args, **kwargs)
            finally:
                sb._aggregate_lease = previous
                _aggregate_local.held = held
    return wrapped


def run_outcome(stats, error=None):
    """A completed function can still represent a partial or skipped job."""
    if error:
        return "error"
    failures, skipped = 0, 0
    def count(value):
        nonlocal failures, skipped
        if isinstance(value, dict):
            for v in value.values():
                count(v)
        elif isinstance(value, (list, tuple)):
            for v in value:
                count(v)
        elif isinstance(value, (int, float)):
            failures += int(value)
        elif value:
            if "skipped" in str(value).lower() or str(value).lower() in ("deadline", "time budget"):
                skipped += 1
            else:
                failures += 1
    def inspect(obj):
        nonlocal skipped
        for key, value in obj.items():
            if key in ("failed", "errors"):
                count(value)
            elif key == "skipped":
                skipped += len(value) if isinstance(value, (dict,list)) else int(value or 0)
            elif isinstance(value, dict):
                inspect(value)
    inspect(stats)
    completed = next((stats[k] for k in ("done", "symbols", "rows", "stocks") if isinstance(stats.get(k), int)), None)
    if failures:
        return "error" if completed == 0 else "partial"
    if skipped:
        return "skipped" if completed == 0 else "partial"
    return "ok"


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
                self.sb.update("etl_runs", {"id": self.id}, {"finished_at": now_iso(), "status": run_outcome(self.stats, ev), "stats": self.stats, "error": str(ev)[:2000] if ev else None})
            except Exception as e:
                print("etl_runs update failed:", e)
        publish_status(self.sb)
        return False


def publish_status(sb):
    """Writes meta/status.json: the latest runs per job, for the Data status page."""
    try:
        rows = sb.select("etl_runs", "select=job,started_at,finished_at,status,stats,error&order=started_at.desc&limit=200")
        latest, recent = {}, []
        now = dt.datetime.now(dt.timezone.utc)
        for r in rows:
            if r.get("status") == "running" and r.get("started_at"):
                try:
                    started = dt.datetime.fromisoformat(r["started_at"].replace("Z", "+00:00"))
                    age = (now-started).total_seconds()
                    r["stale"] = age > float(os.environ.get("ETL_STALE_SECONDS", "7200"))
                    if r["stale"]:
                        r["status"] = "stale"
                        r["running_age_seconds"] = round(age)
                except (ValueError, TypeError):
                    r["stale"] = None
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
