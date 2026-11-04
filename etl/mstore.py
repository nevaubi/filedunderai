"""Generic macro series store: series metadata, point-in-time observations, per-series bundles and the Macro page catalog.

Every source module hands `save()` a list of series dicts:
  {"meta": {...}, "obs": [(YYYY-MM-DD, "raw value string"), ...], "published": YYYY-MM-DD or None,
   "snap": {"url", "cols", "rows", "keys"}}
`obs` are the values exactly as the publisher printed them. `macro_ingest` (db/schema.sql) stores a new vintage only when a
value is new or differs from the latest stored vintage, so history is never rewritten. The vintage date is the publisher's
release date when the source states one, otherwise the retrieval date.
"""
import datetime as dt
import json
import math
import re

from .common import HTTPError, now_iso

SECTIONS = [
    ("rates", "Rates and liquidity"),
    ("inflation", "Inflation"),
    ("labor", "Labor"),
    ("growth", "Growth and activity"),
    ("credit", "Credit and banking"),
    ("global", "Global and OECD"),
    ("positioning", "Positioning"),
]
FREQ_NAME = {"D": "Daily", "W": "Weekly", "M": "Monthly", "Q": "Quarterly", "A": "Annual"}
# Stored history starts here (sources go back further; the floor keeps the shared database small).
START = {"D": "2000-01-01", "W": "1995-01-01", "M": "1980-01-01", "Q": "1970-01-01", "A": "1970-01-01"}
META_KEYS = ["id", "source", "provider", "title", "units", "frequency", "seasonal_adj", "geography", "source_url", "notes",
             "data_url", "source_key", "section", "release"]


def fid(sid):
    """File-safe id used for storage paths."""
    return re.sub(r"[^A-Za-z0-9_.-]", "_", sid)


def today():
    return dt.datetime.now(dt.timezone.utc).date().isoformat()


def _num(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


# ---------------------------------------------------------------- display transforms and statistics
def display_series(meta, pts):
    """Turns stored values into what the page shows: level x scale, year-over-year % or period change."""
    kind, scale = meta.get("display", "level"), meta.get("scale", 1)
    if kind == "yoy":
        freq = meta.get("frequency")
        if freq not in ("M", "Q", "A"):
            return []
        key = (lambda d: d[:4]) if freq == "A" else (lambda d: d[:7])
        by = {key(d): v for d, v in pts}
        out = []
        for d, v in pts:
            p = by.get(str(int(d[:4]) - 1) + key(d)[4:])
            if p:
                out.append((d, (v / p - 1) * 100))
        return out
    if kind == "mom":
        return [(pts[i][0], (pts[i][1] / pts[i - 1][1] - 1) * 100) for i in range(1, len(pts)) if pts[i - 1][1]]
    if kind == "chg":
        return [(pts[i][0], (pts[i][1] - pts[i - 1][1]) * scale) for i in range(1, len(pts))]
    return [(d, v * scale) for d, v in pts]


def _sd(xs):
    if len(xs) < 8:
        return None, None
    m = sum(xs) / len(xs)
    var = sum((x - m) ** 2 for x in xs) / (len(xs) - 1)
    return m, math.sqrt(var) if var > 0 else None


def compute_stats(meta, pts):
    """Latest value, change, weekly change, z-score of the latest move against ten years of moves of the same length."""
    disp = display_series(meta, pts)
    if not disp:
        return None
    last_d, last_v = disp[-1]
    prev = disp[-2] if len(disp) > 1 else None
    freq = meta.get("frequency")
    h = 5 if freq == "D" else 1
    cutoff = (dt.date.fromisoformat(last_d) - dt.timedelta(days=3653)).isoformat()
    window = [x for x in disp if x[0] >= cutoff]
    moves = [window[i][1] - window[i - h][1] for i in range(h, len(window))]
    mean, sd = _sd(moves[:-1]) if moves else (None, None)
    move = disp[-1][1] - disp[-1 - h][1] if len(disp) > h else None
    z = (move - mean) / sd if (move is not None and sd) else None
    lv = sorted(v for _, v in window)
    pct = sum(1 for v in lv if v <= last_v) / len(lv) if lv else None
    wk = None
    if freq in ("D", "W"):
        t0 = (dt.date.fromisoformat(last_d) - dt.timedelta(days=7)).isoformat()
        base = [x for x in disp if x[0] <= t0]
        if base:
            wk = {"from": base[-1][0], "v0": round(base[-1][1], 6), "chg": round(last_v - base[-1][1], 6)}
    y0 = (dt.date.fromisoformat(last_d) - dt.timedelta(days=365)).isoformat()
    yb = [x for x in disp if x[0] <= y0]
    return {
        "last": [last_d, round(last_v, 6)], "prev": [prev[0], round(prev[1], 6)] if prev else None,
        "chg": round(last_v - prev[1], 6) if prev else None, "h": h, "move": round(move, 6) if move is not None else None,
        "z": round(z, 2) if z is not None else None, "pctile": round(pct, 3) if pct is not None else None,
        "wk": wk, "y1": [yb[-1][0], round(yb[-1][1], 6)] if yb else None,
        "lo": [min(window, key=lambda x: x[1])[0], round(min(lv), 6)] if lv else None,
        "hi": [max(window, key=lambda x: x[1])[0], round(max(lv), 6)] if lv else None,
    }


# ---------------------------------------------------------------- save
_DB = {"ok": None}


def db_ready(sb):
    """True once db/schema.sql (macro section) has been applied; until then the store runs on storage bundles only."""
    if _DB["ok"] is None:
        try:
            sb.select("macro_series", "select=id&limit=1")
            _DB["ok"] = True
        except HTTPError as e:
            if e.code in (404, 400):
                print("macro_series table not found: storage-only mode until the schema is applied")
                _DB["ok"] = False
            else:
                raise
    return _DB["ok"]


def load_registry(sb):
    """Series metadata and statistics as last published (macro/registry.json)."""
    try:
        return sb.get_json("macro/registry.json") or {}
    except Exception:
        return {}


def _prev_bundle(sb, sid):
    try:
        return sb.get_json(f"macro/s/{fid(sid)}.json") or {}
    except Exception:
        return {}


def _ingest_rest(sb, sid, rows, vt, published):
    """Same rule as macro_ingest() done through the REST API: insert only new or changed values as a new vintage."""
    from .common import now_iso as _now
    import urllib.parse
    cur = {r["d"]: float(r["value"]) for r in sb.select("macro_series_latest", f"select=d,value&series_id=eq.{urllib.parse.quote(sid)}")}
    new = [{"series_id": sid, "d": d, "vintage": vt, "value": v, "published": published, "retrieved_at": _now()}
           for d, v in rows if d not in cur or abs(cur[d] - float(v)) > 1e-12]
    if new:
        sb.upsert("macro_series_obs", new, "series_id,d,vintage")
    return [r["d"] for r in new]


def _ingest(sb, sid, rows, vt, published):
    if _DB.get("rest"):
        return _ingest_rest(sb, sid, rows, vt, published)
    changed = []
    for i in range(0, len(rows), 500):
        chunk = [{"s": sid, "d": d, "v": v, "vt": vt, "p": published or ""} for d, v in rows[i:i + 500]]
        try:
            res = sb.rpc("macro_ingest", {"p_rows": chunk}) or []
        except HTTPError as e:
            # an older deployed copy of macro_ingest (ambiguous column error) or a missing function: use the REST path
            if e.code in (400, 404) and ("42702" in e.body or "PGRST202" in e.body):
                print("macro_ingest unavailable, inserting through the REST API:", e.body[:120])
                _DB["rest"] = True
                return changed + _ingest_rest(sb, sid, rows[i:], vt, published)
            raise
        changed += [r["d"] for r in res]
    return changed


def save(sb, series, reg, partial=False, log=print, prev_reg=None):
    """Upserts metadata, ingests changed observations as new vintages, writes per-series bundles and snapshots.

    partial=True means `obs` only covers a recent window: the bundle merges it into the previously published history.
    `reg` is the registry dict from load_registry(); it is updated in place and written by publish_catalog().
    Returns {series_id: number of new or revised observations}.
    """
    out = {}
    stamp = today()
    use_db = db_ready(sb)
    known = {}
    if use_db:
        ids = ",".join('"' + s["meta"]["id"].replace('"', "") + '"' for s in series)
        for r in sb.select("macro_series", f"select=id,obs_count&id=in.({ids})"):
            known[r["id"]] = r.get("obs_count")
    for s in series:
        meta = dict(s["meta"])
        sid = meta["id"]
        floor = meta.get("start") or START.get(meta.get("frequency"), "1970-01-01")
        raw = sorted((d, str(v).strip()) for d, v in s["obs"] if d and d >= floor and _num(v) is not None)
        published = s.get("published")
        vt = published or stamp
        prev = _prev_bundle(sb, sid)
        pmap = {d: v for d, v in (prev.get("obs") or [])}
        cur = dict(pmap) if partial else {}
        for d, v in raw:
            cur[d] = _num(v)
        pts = sorted((d, v) for d, v in cur.items() if v is not None)
        if use_db:
            row = {k: meta.get(k) for k in META_KEYS}
            row["meta"] = {k: v for k, v in meta.items() if k not in META_KEYS}
            row["updated_at"] = now_iso()
            sb.upsert("macro_series", [row], "id")
            # First ingest of a series fed by a recent window: load the history already published in the bundle as well.
            rows = raw if (not partial or known.get(sid)) else sorted(set(raw) | {(d, repr(v)) for d, v in pmap.items() if d >= floor and v is not None})
            changed = _ingest(sb, sid, rows, vt, published)
        else:
            changed = [d for d, v in raw if pmap.get(d) is None or abs(pmap[d] - _num(v)) > 1e-12]
        rev = dict(prev.get("rev") or {})
        vin = dict(prev.get("vin") or {})
        for d in changed:
            if pmap.get(d) is not None and cur.get(d) is not None and abs(pmap[d] - cur[d]) > 1e-12:
                rev.setdefault(d, []).append([pmap[d], vin.get(d) or (prev.get("retrieved") or "")[:10]])
            vin[d] = vt
        keep = {d for d, _ in pts[-120:]}
        vin = {d: v for d, v in vin.items() if d in keep}
        stats = compute_stats(meta, pts)
        prev_stats = ((prev_reg if prev_reg is not None else reg).get(sid) or {}).get("stats")
        if stats is not None:
            first_load = not pmap
            new_last = bool(pts) and pts[-1][0] in set(changed) and not first_load
            prev_new = (prev_stats or {}).get("new_last_at") if not new_last else None
            stats.update(vintage=vin.get(pts[-1][0]), published=published or prev.get("published"), retrieved=now_iso(),
                         new_last_at=stamp if new_last else prev_new)
        bundle = {"id": sid, "retrieved": now_iso(), "published": published, "obs": [[d, v] for d, v in pts], "vin": vin, "rev": rev}
        sb.put_json(f"macro/s/{fid(sid)}.json", bundle, cache="max-age=600")
        if s.get("snap"):
            snap = dict(s["snap"])
            keys = snap.get("keys") or []
            if keys:
                lo = pts[0][0] if pts else floor
                idx = [i for i, k in enumerate(keys) if k and k >= lo]
                snap["rows"] = [snap["rows"][i] for i in idx]
                snap["keys"] = [keys[i] for i in idx]
            snap.update({"id": sid, "retrieved": now_iso(), "published": published})
            sb.put_json(f"macro/snap/{fid(sid)}.json", snap, cache="max-age=600")
        summary = {"stats": stats, "first_date": pts[0][0] if pts else None, "last_date": pts[-1][0] if pts else None,
                   "last_value": pts[-1][1] if pts else None, "obs_count": len(pts)}
        reg[sid] = dict(meta, **summary)
        if use_db:
            sb.update("macro_series", {"id": sid}, dict(summary, updated_at=now_iso()))
        out[sid] = len(changed)
    if out:
        log(f"saved {len(out)} series, {sum(out.values())} new or revised observations{'' if use_db else ' (storage only)'}")
    return out


def save_releases(sb, rows, log=print):
    """Release calendar rows: {release, release_date, release_time, provider, title, source_url}. Keeps other publishers' rows."""
    cur = []
    try:
        cur = sb.get_json("macro/releases.json") or []
    except Exception:
        pass
    mine = {r["release"] for r in rows}
    keep = [r for r in cur if r["release"] not in mine]
    stamp = now_iso()
    rows = [dict(r, retrieved_at=stamp) for r in rows]
    sb.put_json("macro/releases.json", sorted(keep + rows, key=lambda r: (r["release_date"], r["release"])), cache="max-age=300")
    if db_ready(sb) and rows:
        sb.upsert("macro_releases", rows, "release,release_date")
    log(f"calendar: {len(rows)} dates for {len(mine)} releases")
    return len(rows)


# ---------------------------------------------------------------- catalog for the page
def _fmt(meta, v):
    if v is None:
        return "n/a"
    u = meta.get("dunits") or meta.get("units") or ""
    dp = meta.get("dp", 2)
    s = f"{v:,.{dp}f}"
    if u.startswith("Percent") or meta.get("display") in ("yoy", "mom"):
        return s + "%"
    return s + (" " + meta.get("short_units") if meta.get("short_units") else "")


def _chg_text(meta, chg):
    if chg is None:
        return ""
    pctlike = (meta.get("dunits") or meta.get("units") or "").startswith("Percent") or meta.get("display") in ("yoy", "mom")
    if pctlike:
        bp = chg * 100
        return f"{'+' if bp >= 0 else '-'}{abs(bp):.0f} bp" if abs(bp) >= 1 or bp == 0 else f"{chg:+.2f} pt"
    dp = meta.get("dp", 2)
    return f"{'+' if chg >= 0 else '-'}{abs(chg):,.{dp}f}" + (" " + meta.get("short_units") if meta.get("short_units") else "")


def what_changed(series, days=7):
    """Largest moves among series with an observation or a new release in the last `days` days, ranked by |z|."""
    since = (dt.date.fromisoformat(today()) - dt.timedelta(days=days)).isoformat()
    cands = []
    for s in series:
        st, m = s.get("stats") or {}, s
        if not st or st.get("z") is None or m.get("no_changes"):
            continue
        # fresh: a daily or weekly observation dated this week, or a new period first seen this week (not the initial load)
        fresh = (st["last"][0] >= since and m.get("frequency") in ("D", "W")) or (st.get("new_last_at") or "") >= since
        if not fresh:
            continue
        cands.append((abs(st["z"]), s, st, m))
    cands.sort(key=lambda x: -x[0])
    out, per_group = [], {}
    for _, s, st, m in cands:
        g = m.get("group") or s["id"]
        if per_group.get(g, 0) >= 2:
            continue
        per_group[g] = per_group.get(g, 0) + 1
        horizon = {"D": "this week", "W": "on the week"}.get(m.get("frequency"), "versus the prior " + {"M": "month", "Q": "quarter", "A": "year"}.get(m.get("frequency"), "period"))
        move = st.get("wk", {}).get("chg") if m.get("frequency") in ("D", "W") and st.get("wk") else st.get("chg")
        text = (f"{m.get('short') or s['title']} at {_fmt(m, st['last'][1])}, {_chg_text(m, move)} {horizon}. "
                f"The latest {'five-day ' if st.get('h') == 5 else ''}move is {abs(st['z']):.1f} standard deviations "
                f"{'above' if st['z'] > 0 else 'below'} the average move over the past ten years.")
        out.append({"id": s["id"], "z": st["z"], "d": st["last"][0], "v": st["last"][1], "move": move, "text": text})
        if len(out) >= 12:
            break
    return out


def publish_catalog(sb, reg, log=print):
    """Writes macro/registry.json and macro/catalog.json: series with metadata and statistics, upcoming releases, this week's moves."""
    sb.put_json("macro/registry.json", reg, cache="no-store")
    t0 = today()
    horizon = (dt.date.fromisoformat(t0) + dt.timedelta(days=45)).isoformat()
    try:
        rel = sb.get_json("macro/releases.json") or []
    except Exception:
        rel = []
    cal = [r for r in rel if r["release_date"] >= t0]
    by_release = {}
    for sid, m in reg.items():
        if m.get("release"):
            by_release.setdefault(m["release"], []).append(sid)
    nxt = {}
    for c in cal:
        nxt.setdefault(c["release"], c)
    series = {}
    for sid, m in reg.items():
        c = nxt.get(m.get("release"))
        series[sid] = dict(m, next_release=[c["release_date"], c.get("release_time")] if c else None)
    calendar = [dict(c, series=by_release.get(c["release"], [])[:60]) for c in cal if c["release_date"] <= horizon and by_release.get(c["release"])]
    changes = what_changed([dict(m, id=sid) for sid, m in reg.items()])
    sb.put_json("macro/catalog.json", {"generated": now_iso(), "sections": SECTIONS, "series": series, "calendar": calendar, "changes": changes}, cache="max-age=300")
    log(f"catalog: {len(series)} series, {len(calendar)} upcoming release dates, {len(changes)} weekly moves")
    return len(series)


def snapshot_rows(cols, rows, keys, url, note=None):
    return {"url": url, "cols": cols, "rows": rows, "keys": keys, "note": note}


def dumps(o):
    return json.dumps(o, separators=(",", ":"), default=str)
