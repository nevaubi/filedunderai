"""Financial statements as filed, from the SEC Financial Statement Data Sets (DERA).

Each quarterly ZIP holds sub.txt (one row per XBRL submission), pre.txt (every line of every rendered statement:
report, line order, statement code, filed label, tag), num.txt (every value presented on those statements, with
period end, quarters covered, unit, axis/member segments and co-registrant) and tag.txt (tag definitions).

For covered companies (etl/universe.py, including predecessor registrants up to their cut-off date) this module
keeps every 10-K and 10-Q (and amendments and transition reports) filed since 2009 exactly as presented:

- Postgres: fs_filings (one row per submission, with its standardized template values), fs_reports (one row per
  rendered statement, lines and values as compact JSON arrays), fs_tags (standard tag labels), fs_std_map.
- Storage (public-data): statements/{T}/index.json (filings and their statements), statements/{T}/{accession}.json.gz
  (all statements of one filing), statements/{T}/std.json.gz (standardized values per filing), statements/std_map.json.

Point in time: every filing keeps its own values; a figure restated in a later filing is a separate row of that
later filing. ddate is the period end rounded to a month end and qtrs the number of quarters, as SEC publishes them.
"""
import datetime as dt
import gzip
import io
import json
import os
import re
import tempfile
import time
import urllib.parse
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor

from .common import SEC_UA, HTTPError, Run, now_iso, sec_get, sec_throttle
from .stdmap import map_rows, published_map, standardize, template_for
from .universe import PREDECESSORS, UNIVERSE

PAGE = "https://www.sec.gov/data-research/sec-markets-data/financial-statement-data-sets"
STATE = "meta/statements.json"
FORMS = {"10-K", "10-K/A", "10-Q", "10-Q/A", "10-KT", "10-KT/A", "10-QT", "10-QT/A"}
STMTS = {"BS", "IS", "CF", "EQ", "CI", "UN"}
TICKER_BY_CIK = {c: t for t, c, _, _ in UNIVERSE}
PRED = {}  # predecessor cik -> [(ticker, cutoff)]
for _t, _lst in PREDECESSORS.items():
    for _c, _cut in _lst:
        PRED.setdefault(_c, []).append((_t, _cut))


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def ticker_for(cik, filed):
    """Ticker for a filing: the universe registrant, or a predecessor registrant filing before its cut-off date."""
    for t, cut in PRED.get(cik, []):
        if filed < cut:
            return t
    if cik in TICKER_BY_CIK:
        return TICKER_BY_CIK[cik]
    return None


def dataset_links():
    """[(name, url)] for every quarterly ZIP listed on SEC's Financial Statement Data Sets page, oldest first."""
    html = sec_get(PAGE)
    out = {}
    for href in re.findall(r'href="([^"]*/financial-statement-data-sets/(\d{4}q[1-4])\.zip)"', html):
        out[href[1]] = "https://www.sec.gov" + href[0] if href[0].startswith("/") else href[0]
    return sorted(out.items())


def head(url):
    for attempt in range(5):
        try:
            sec_throttle()
            req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": SEC_UA})
            with urllib.request.urlopen(req, timeout=60) as r:
                return {"size": int(r.headers.get("Content-Length") or 0), "last_modified": r.headers.get("Last-Modified")}
        except Exception as e:
            if attempt == 4:
                raise
            log("head retry", url, str(e)[:120])
            time.sleep(5 * (attempt + 1))


def download(url, path):
    """Streams a ZIP to disk (with retries); returns its size."""
    for attempt in range(5):
        try:
            sec_throttle()
            req = urllib.request.Request(url, headers={"User-Agent": SEC_UA})
            with urllib.request.urlopen(req, timeout=300) as r, open(path, "wb") as fh:
                while True:
                    b = r.read(1 << 20)
                    if not b:
                        break
                    fh.write(b)
            with zipfile.ZipFile(path) as z:
                z.testzip()
            return os.path.getsize(path)
        except Exception as e:
            log("download retry", url, str(e)[:120])
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"could not download {url}")


def _rows(z, name):
    with z.open(name) as f:
        hdr = f.readline().decode("utf-8", "replace").rstrip("\n").rstrip("\r").split("\t")
        for raw in f:
            yield hdr, raw


def _date(s):
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}" if s and len(s) == 8 else None


def _num(s):
    if s in (None, ""):
        return None
    v = float(s)
    return int(v) if v.is_integer() and abs(v) < 1e15 else v


def extract(zip_path):
    """Reads one quarterly ZIP and returns ({accession: filing}, {tag: tag row}) for covered companies."""
    subs = {}
    with zipfile.ZipFile(zip_path) as z:
        for hdr, raw in _rows(z, "sub.txt"):
            r = dict(zip(hdr, raw.decode("utf-8", "replace").rstrip("\n").split("\t")))
            if r.get("form") not in FORMS:
                continue
            cik = int(r["cik"])
            filed = _date(r["filed"])
            tk = ticker_for(cik, filed)
            if not tk:
                continue
            subs[r["adsh"]] = {
                "accession": r["adsh"], "ticker": tk, "cik": cik, "registrant": r["name"], "form": r["form"],
                "period": _date(r["period"]), "fy": int(r["fy"]) if r.get("fy", "").isdigit() else None, "fp": r.get("fp") or None,
                "fye": r.get("fye") or None, "filed": filed, "accepted": (r.get("accepted") or "").replace(".0", "") or None,
                "prevrpt": r.get("prevrpt") == "1", "sic": r.get("sic") or None, "reports": {}, "_num": [],
            }
        keys = {a.encode() for a in subs}
        for hdr, raw in _rows(z, "pre.txt"):
            if raw[:20] not in keys:
                continue
            p = raw.decode("utf-8", "replace").rstrip("\n").split("\t")
            r = dict(zip(hdr, p))
            if r["stmt"] not in STMTS:
                continue
            f = subs[r["adsh"]]
            rep = f["reports"].setdefault(int(r["report"]), {"report": int(r["report"]), "stmt": r["stmt"], "inpth": r["inpth"] == "1", "rfile": r.get("rfile") or None, "lines": []})
            custom = 1 if r["version"] == r["adsh"] else 0
            rep["lines"].append([int(r["line"]), r["tag"], custom, r["plabel"], 1 if r.get("negating") == "1" else 0, r["version"]])
        for hdr, raw in _rows(z, "num.txt"):
            if raw[:20] not in keys:
                continue
            p = raw.decode("utf-8", "replace").rstrip("\n").split("\t")
            r = dict(zip(hdr, p))
            subs[r["adsh"]]["_num"].append([r["tag"], r["version"], _date(r["ddate"]), int(r["qtrs"]), r["uom"], r.get("segments") or None, r.get("coreg") or None, _num(r.get("value")), r.get("footnote") or None])
        wanted = {(ln[1], ln[5]) for f in subs.values() for rep in f["reports"].values() for ln in rep["lines"]}
        tags = {}
        for hdr, raw in _rows(z, "tag.txt"):
            p = raw.decode("utf-8", "replace").rstrip("\n").split("\t")
            r = dict(zip(hdr, p))
            if (r["tag"], r["version"]) in wanted:
                tags[(r["tag"], r["version"])] = r
    filings = {}
    for a, f in subs.items():
        if not f["reports"]:
            continue
        reports = []
        for rid in sorted(f["reports"]):
            rep = f["reports"][rid]
            rep["lines"].sort(key=lambda x: x[0])
            tagset = {(ln[1], ln[5]) for ln in rep["lines"]}
            lines = []
            for ln in rep["lines"]:
                t = tags.get((ln[1], ln[5]), {})
                lines.append([ln[0], ln[1], ln[2], ln[3], ln[4], t.get("iord") or None, t.get("crdr") or None])
            facts = [[n[0], n[2], n[3], n[4], n[5], n[6], n[7], n[8]] for n in f["_num"] if (n[0], n[1]) in tagset]
            facts.sort(key=lambda x: (x[0], x[4] or "", x[5] or "", x[1] or "", x[2]))
            reports.append({"report": rid, "stmt": rep["stmt"], "inpth": rep["inpth"], "rfile": rep["rfile"], "lines": lines, "facts": facts})
        f["reports"] = reports
        f["n_values"] = len(f.pop("_num"))
        f["n_lines"] = sum(len(r["lines"]) for r in reports)
        f["template"] = template_for(f["sic"])
        f["std"] = standardize(f["template"], reports)
        filings[a] = f
    std_tags = {}
    for (tag, ver), r in tags.items():
        if r.get("custom") == "1":
            continue
        if tag not in std_tags or ver > std_tags[tag]["version"]:
            std_tags[tag] = {"tag": tag, "version": ver, "tlabel": r.get("tlabel") or None, "datatype": r.get("datatype") or None, "iord": r.get("iord") or None, "crdr": r.get("crdr") or None}
    return filings, std_tags


def filing_index_url(cik, accession):
    return f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession.replace('-', '')}/{accession}-index.htm"


def _gz_put(sb, path, obj):
    body = gzip.compress(json.dumps(obj, separators=(",", ":"), default=str).encode(), 6)
    sb.put_object("public-data", path, body, "application/gzip", cache="max-age=3600")
    return len(body)


def _index_entry(f):
    return {"a": f["accession"], "form": f["form"], "fy": f["fy"], "fp": f["fp"], "period": f["period"], "filed": f["filed"],
            "accepted": f["accepted"], "cik": f["cik"], "registrant": f["registrant"], "template": f["template"], "sic": f["sic"],
            "prevrpt": f["prevrpt"], "reports": [[r["report"], r["stmt"], 1 if r["inpth"] else 0, len(r["lines"])] for r in f["reports"]],
            "url": filing_index_url(f["cik"], f["accession"])}


def publish_filing(sb, f, source):
    """Writes statements/{T}/{accession}.json.gz: the whole filing as presented plus its standardized mapping."""
    doc = {k: f[k] for k in ("accession", "ticker", "cik", "registrant", "form", "period", "fy", "fp", "fye", "filed", "accepted", "prevrpt", "sic", "template", "reports", "std")}
    doc["url"] = filing_index_url(f["cik"], f["accession"])
    doc["source"] = source
    return _gz_put(sb, f"statements/{f['ticker']}/{f['accession']}.json.gz", doc)


def publish_ticker(sb, tk, new_filings, source):
    """Merges new filings into statements/{T}/index.json and statements/{T}/std.json.gz."""
    idx = sb.get_json(f"statements/{tk}/index.json") or {"ticker": tk, "filings": []}
    keep = {x["a"]: x for x in idx.get("filings", [])}
    for f in new_filings:
        keep[f["accession"]] = _index_entry(f)
    idx["filings"] = sorted(keep.values(), key=lambda x: (x["filed"], x["a"]), reverse=True)
    idx["generated"] = now_iso()
    idx["source"] = {"name": "SEC Financial Statement Data Sets", "page": PAGE}
    raw = sb.get_object("public-data", f"statements/{tk}/std.json.gz")
    std = json.loads(gzip.decompress(raw)) if raw else {"ticker": tk, "filings": {}}
    for f in new_filings:
        std["filings"][f["accession"]] = {"form": f["form"], "filed": f["filed"], "fy": f["fy"], "fp": f["fp"], "period": f["period"], "template": f["template"], "lines": f["std"]["lines"]}
    std["generated"] = now_iso()
    _gz_put(sb, f"statements/{tk}/std.json.gz", std)
    sb.put_json(f"statements/{tk}/index.json", idx, cache="max-age=600")


def publish_deferred(sb, defer_dir, workers=6):
    """Publishes the ticker index and standardized files once per ticker after a multi-quarter backfill."""
    if not os.path.isdir(defer_dir):
        return 0
    def one(fn):
        tk = fn[:-6]
        path = os.path.join(defer_dir, fn)
        with open(path) as fh:
            fl = [json.loads(line) for line in fh if line.strip()]
        publish_ticker(sb, tk, fl, PAGE)
        os.remove(path)
        return tk
    with ThreadPoolExecutor(max_workers=workers) as ex:
        done = list(ex.map(one, sorted(f for f in os.listdir(defer_dir) if f.endswith(".jsonl"))))
    return len(done)


def write_db(sb, filings, tags, source):
    rows, reps = [], []
    for f in filings:
        rows.append({k: f[k] for k in ("accession", "ticker", "cik", "registrant", "form", "period", "fy", "fp", "fye", "filed", "accepted", "prevrpt", "sic", "template", "n_lines", "n_values")}
                    | {"std": f["std"], "source_zip": source, "retrieved_at": now_iso()})
        for r in f["reports"]:
            reps.append({"accession": f["accession"], "report": r["report"], "stmt": r["stmt"], "inpth": r["inpth"], "rfile": r["rfile"], "lines": r["lines"], "facts": r["facts"]})
    sb.upsert("fs_filings", rows, "accession", batch=100)
    sb.upsert("fs_reports", reps, "accession,report", batch=60)
    if tags:
        sb.upsert("fs_tags", list(tags.values()), "tag", batch=500)
    return len(rows), len(reps)


def _tables_ready(sb):
    try:
        sb.select("fs_filings", "select=accession&limit=1")
        return True
    except HTTPError as e:
        if e.code in (404, 400) or "PGRST" in str(e):
            return False
        raise


def process_quarter(sb, name, url, state, workdir, deadline=None, db=True, workers=6, defer_dir=None):
    """Loads one quarter. Resumable: the accessions already published are kept in the state file."""
    q = state["quarters"].setdefault(name, {"url": url})
    zip_path = os.path.join(workdir, f"{name}.zip")
    if not os.path.exists(zip_path):
        q["bytes"] = download(url, zip_path)
    log(name, "extract", os.path.getsize(zip_path))
    filings, tags = extract(zip_path)
    q.update({"filings": len(filings), "values": sum(f["n_values"] for f in filings.values()), "lines": sum(f["n_lines"] for f in filings.values()), "retrieved_at": now_iso()})
    done = set(q.get("published") or [])
    todo = [f for a, f in sorted(filings.items()) if a not in done]
    log(name, f"{len(filings)} filings, {len(todo)} to publish, {q['values']} values, {q['lines']} lines")
    db_ok = db and _tables_ready(sb)
    if db and not db_ok:
        log(name, "fs_* tables are not in the database yet; publishing bundles only")
    by_tk = {}
    tickers_ok = True
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for i in range(0, len(todo), 60):
            if deadline and time.time() > deadline:
                break
            chunk = todo[i:i + 60]
            list(ex.map(lambda f: publish_filing(sb, f, url), chunk))
            for f in chunk:
                by_tk.setdefault(f["ticker"], []).append(f)
            if db_ok:
                write_db(sb, chunk, tags if i == 0 else None, url)
            done.update(f["accession"] for f in chunk)
            q["published"] = sorted(done)
            sb.put_json(STATE, state, cache="no-store")
        # filings of a quarter that were published by an earlier, interrupted run are merged again from this extract
        fresh = {f["accession"] for fl in by_tk.values() for f in fl}
        for a in sorted(done - fresh):
            f = filings.get(a)
            if f:
                by_tk.setdefault(f["ticker"], []).append(f)
        if defer_dir:
            os.makedirs(defer_dir, exist_ok=True)
            for tk, fl in by_tk.items():
                with open(os.path.join(defer_dir, f"{tk}.jsonl"), "a") as fh:
                    for f in fl:
                        fh.write(json.dumps({k: f[k] for k in ("accession", "ticker", "cik", "registrant", "form", "period", "fy", "fp", "filed", "accepted", "prevrpt", "sic", "template", "std")}
                                            | {"reports": [{"report": r["report"], "stmt": r["stmt"], "inpth": r["inpth"], "lines": [0] * len(r["lines"])} for r in f["reports"]]}, default=str) + "\n")
        else:
            tdone = set(q.get("tickers_done") or [])
            pending = [(tk, fl) for tk, fl in sorted(by_tk.items()) if tk not in tdone]
            step = workers * 4
            for i in range(0, len(pending), step):
                if deadline and time.time() > deadline:
                    break
                part = pending[i:i + step]
                list(ex.map(lambda kv: publish_ticker(sb, kv[0], kv[1], url), part))
                tdone.update(tk for tk, _ in part)
                q["tickers_done"] = sorted(tdone)
                sb.put_json(STATE, state, cache="no-store")
            tickers_ok = tdone >= set(by_tk)
    complete = len(done) >= len(filings) and tickers_ok
    q["status"] = "done" if complete else "partial"
    q["db"] = bool(db_ok)
    if complete:
        q.pop("published", None)
        q.pop("tickers_done", None)
        try:
            os.remove(zip_path)
        except OSError:
            pass
    sb.put_json(STATE, state, cache="no-store")
    return complete, len(by_tk)


def statements_job(sb, quarters=None, since="2009q2", force=False, time_budget=None, db=True, workdir=None, workers=6, defer=False):
    """Loads every quarter not yet loaded (or the named ones). Returns per-quarter stats."""
    t0 = time.time()
    deadline = t0 + time_budget if time_budget else None
    workdir = workdir or tempfile.gettempdir()
    state = sb.get_json(STATE) or {"quarters": {}}
    state.setdefault("quarters", {})
    out = {}
    with Run(sb, "statements") as run:
        links = dataset_links()
        sb.put_json("statements/std_map.json", published_map(), cache="max-age=3600")
        if db and _tables_ready(sb):
            rows = map_rows()
            sb.upsert("fs_std_map", rows, "template,line_id,tag")
            keep = {(r["template"], r["line_id"], r["tag"]) for r in rows}
            for r in sb.select("fs_std_map", "select=template,line_id,tag"):
                if (r["template"], r["line_id"], r["tag"]) not in keep:
                    sb.delete("fs_std_map", "&".join(f"{k}=eq.{urllib.parse.quote(r[k])}" for k in ("template", "line_id", "tag")))
        for name, url in links:
            if name < since or (quarters and name not in quarters):
                continue
            q = state["quarters"].get(name, {})
            if not force and q.get("status") == "done" and (q.get("db") or not db or not _tables_ready(sb)):
                meta = head(url)
                if q.get("size") == meta["size"] and q.get("last_modified") == meta["last_modified"]:
                    continue
                log(name, "changed on SEC, reloading")
            if force or q.get("status") == "done":
                state["quarters"][name] = q = {"url": url}
            if deadline and time.time() > deadline - 60:
                break
            try:
                meta = head(url)
                complete, tickers = process_quarter(sb, name, url, state, workdir, deadline=deadline, db=db, workers=workers, defer_dir=os.path.join(workdir, "deferred") if defer else None)
            except Exception as e:  # one failed quarter (network) is retried by the next run; keep going
                log(name, "failed:", str(e)[:300])
                out[name] = {"error": str(e)[:300]}
                continue
            state["quarters"][name].update(meta)
            sb.put_json(STATE, state, cache="no-store")
            out[name] = {"complete": complete, "tickers": tickers, "filings": state["quarters"][name].get("filings")}
            log(name, out[name])
        if defer:
            out["tickers_published"] = publish_deferred(sb, os.path.join(workdir, "deferred"), workers)
        run.stats = {"quarters": out, "loaded": sum(1 for v in state["quarters"].values() if v.get("status") == "done")}
    return out
