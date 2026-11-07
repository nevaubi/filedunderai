"""Per-user agent state for the partner app's agents (scheduled tasks, monitors, inbox, interests).

Storage: the private `agents` bucket.
    u/{uid}/state.json            tasks, monitors, inbox index, interests, settings (one document per user)
    u/{uid}/inbox/{id}.json       full inbox item: report markdown, canvas views, evidence, file list
    u/{uid}/files/{id}/{name}     files produced by agent runs

Every read-modify-write of a user's state happens under the `agents:u:{uid}` lease (the etl_leases RPC the
pipeline already uses), shared with the app-facing API (api/app-agents.js), so an edit in the app and a scheduled
tick never overwrite each other. Long work (an agent run) never holds the lease: it claims the task, releases,
runs, and takes the lease again to write the result.
"""
import contextlib
import json
import re
import time
import urllib.parse
import uuid

from ..common import HTTPError, now_iso, request

BUCKET = "agents"
UID_RX = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
STATE_VERSION = 1
INBOX_KEEP = 300
RUNS_KEEP = 20
EVENTS_KEEP = 1500


class Busy(RuntimeError):
    pass


def new_id():
    return uuid.uuid4().hex[:16]


def blank_state(uid):
    return {
        "version": STATE_VERSION, "uid": uid, "email": None, "tz": "America/Chicago", "created_at": now_iso(), "updated_at": now_iso(),
        "settings": {"email": False, "learning": True, "insights": "weekly", "insights_next_at": None},
        "watchlist": [], "portfolio": [],
        "tasks": [], "monitors": [], "inbox": [], "suggestions": [],
        "interests": {"events": [], "weights": {"tickers": {}, "sectors": {}, "topics": {}}, "pins": {"tickers": [], "topics": []},
                      "muted": {"tickers": [], "topics": []}, "updated_at": None},
    }


class Store:
    """Thin storage client over a pipeline `Supabase` instance (service role)."""

    def __init__(self, sb):
        self.sb = sb
        self._bucket_ok = False

    # ---- bucket and objects
    def ensure_bucket(self):
        if self._bucket_ok:
            return
        try:
            request(f"{self.sb.url}/storage/v1/bucket", method="POST", data={"id": BUCKET, "name": BUCKET, "public": False},
                    headers=self.sb._h({"Content-Type": "application/json"}), retries=1)
        except HTTPError as e:
            if e.code not in (400, 409):
                raise
        self._bucket_ok = True

    def put(self, path, obj, content_type="application/json"):
        self.ensure_bucket()
        body = obj if isinstance(obj, (bytes, bytearray)) else json.dumps(obj, separators=(",", ":"), default=str).encode()
        request(f"{self.sb.url}/storage/v1/object/{BUCKET}/{urllib.parse.quote(path)}", method="POST", data=body,
                headers=self.sb._h({"Content-Type": content_type, "x-upsert": "true", "cache-control": "no-store"}), timeout=120, retries=3)
        return len(body)

    def get_bytes(self, path):
        return self.sb.get_object(BUCKET, path)

    def get(self, path):
        b = self.get_bytes(path)
        return json.loads(b) if b else None

    def delete(self, paths):
        if not paths:
            return
        request(f"{self.sb.url}/storage/v1/object/{BUCKET}", method="DELETE", data={"prefixes": list(paths)},
                headers=self.sb._h({"Content-Type": "application/json"}), retries=2)

    def list(self, prefix, limit=1000):
        self.ensure_bucket()
        out, offset = [], 0
        while True:
            page = json.loads(request(f"{self.sb.url}/storage/v1/object/list/{BUCKET}", method="POST",
                                      data={"prefix": prefix, "limit": limit, "offset": offset, "sortBy": {"column": "name", "order": "asc"}},
                                      headers=self.sb._h({"Content-Type": "application/json"}), retries=2) or "[]")
            out += page
            if len(page) < limit:
                return out
            offset += limit

    def signed_url(self, path, seconds=3600):
        r = json.loads(request(f"{self.sb.url}/storage/v1/object/sign/{BUCKET}/{urllib.parse.quote(path)}", method="POST",
                               data={"expiresIn": int(seconds)}, headers=self.sb._h({"Content-Type": "application/json"}), retries=2))
        u = r.get("signedURL") or r.get("signedUrl")
        return f"{self.sb.url}/storage/v1{u}" if u and u.startswith("/") else u

    # ---- users
    def users(self):
        return sorted(e["name"] for e in self.list("u/") if UID_RX.match(e.get("name") or ""))

    def state_path(self, uid):
        return f"u/{uid}/state.json"

    def load(self, uid):
        if not UID_RX.match(uid or ""):
            raise ValueError("bad user id")
        s = self.get(self.state_path(uid)) or blank_state(uid)
        return upgrade(s, uid)

    def save(self, uid, state):
        state["updated_at"] = now_iso()
        state["inbox"] = state.get("inbox", [])[:INBOX_KEEP]
        ev = state.get("interests", {}).get("events")
        if ev and len(ev) > EVENTS_KEEP:
            state["interests"]["events"] = ev[-EVENTS_KEEP:]
        for t in state.get("tasks", []):
            t["runs"] = (t.get("runs") or [])[:RUNS_KEEP]
        self.put(self.state_path(uid), state)

    # ---- lease
    @contextlib.contextmanager
    def locked(self, uid, wait=20, seconds=90):
        owner = uuid.uuid4().hex
        name = f"agents:u:{uid}"
        end = time.time() + wait
        while True:
            ok = self.sb.rpc("acquire_etl_lease", {"lock_name": name, "owner": owner, "lease_seconds": seconds}) is True
            if ok:
                break
            if time.time() > end:
                raise Busy(f"agent state for {uid[:8]} is busy")
            time.sleep(1.5)
        try:
            yield
        finally:
            try:
                self.sb.rpc("release_etl_lease", {"lock_name": name, "owner": owner})
            except Exception as e:  # expires on its own
                print("agents lease release failed:", str(e)[:160])

    @contextlib.contextmanager
    def edit(self, uid, wait=20):
        """Read-modify-write of one user's state under the lease."""
        with self.locked(uid, wait=wait):
            s = self.load(uid)
            yield s
            self.save(uid, s)

    # ---- inbox
    def add_inbox(self, state, item):
        """Stores the full item and puts its card at the top of the user's inbox index (caller saves state)."""
        item = dict(item)
        item.setdefault("id", new_id())
        item.setdefault("created_at", now_iso())
        self.put(f"u/{state['uid']}/inbox/{item['id']}.json", item)
        card = {k: item.get(k) for k in ("id", "kind", "title", "summary", "created_at", "source", "tickers", "severity")}
        card.update({"read_at": None, "pinned": False, "n_views": len(item.get("views") or []), "n_files": len(item.get("files") or []),
                     "n_evidence": len(item.get("evidence") or [])})
        state["inbox"] = [card] + [c for c in state.get("inbox", []) if c.get("id") != item["id"]]
        return card

    def put_file(self, uid, item_id, name, data, content_type):
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", name)[:80] or "file"
        path = f"u/{uid}/files/{item_id}/{safe}"
        n = self.put(path, data, content_type)
        return {"name": safe, "path": path, "bytes": n, "content_type": content_type}


def upgrade(s, uid):
    """Fills fields added after a state was first written."""
    base = blank_state(uid)
    for k, v in base.items():
        if k not in s:
            s[k] = v
    for k, v in base["settings"].items():
        s["settings"].setdefault(k, v)
    for k, v in base["interests"].items():
        s["interests"].setdefault(k, v)
    s["uid"] = uid
    return s
