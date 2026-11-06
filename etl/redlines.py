"""Disclosure redlines pipeline: per company, the latest 10-K against the prior 10-K and the latest 10-Q against the
same fiscal quarter a year earlier, section by section (redlines_core). Writes public-data/redlines/{T}.json,
an index of the most significant language changes across coverage (redlines/index.json) and a raw-bucket state.

Rotation (cron): companies never compared first, then a rotating slice re-checked for a newer 10-K/10-Q.
"""
import concurrent.futures as cf
import datetime as dt
import json
import time

from . import redlines_core as R
from . import supply_sources as SS
from .common import AggregateLease, LeaseBusy, Run, Supabase, now_iso, sec_json
from .universe import BY_TICKER, UNIVERSE

STATE = "redlines/state.json"
MAX_TEXT = 6000


def _day(s):
    return dt.date.fromisoformat(s[:10])


def pick_pairs(rows):
    """[(kind, new_row, old_row)] for the annual and quarterly comparisons available in a submissions feed."""
    ks = sorted([r for r in rows if r["form"] == "10-K" and r["doc"]], key=lambda r: r["filed"], reverse=True)
    qs = sorted([r for r in rows if r["form"] == "10-Q" and r["doc"]], key=lambda r: r["filed"], reverse=True)
    out = []
    if len(ks) >= 2:
        out.append(("annual", ks[0], ks[1]))
    if qs and qs[0].get("report"):
        target = _day(qs[0]["report"])
        prior = [q for q in qs[1:] if q.get("report") and 330 <= (target - _day(q["report"])).days <= 400]
        if prior:
            out.append(("quarterly", qs[0], prior[0]))
    return out


def _meta(cik, r):
    return {"accn": r["accn"], "form": r["form"], "filed": r["filed"], "report": r.get("report"), "doc": r["doc"],
            "url": SS.doc_url(cik, r["accn"], r["doc"])}


def _trim(change):
    out = dict(change)
    for k in ("old", "new"):
        if out.get(k) and len(out[k]) > MAX_TEXT:
            out[k] = out[k][:MAX_TEXT] + " …"
            out["clipped"] = True
    if out.get("ops") and sum(len(t) for _, t in out["ops"]) > MAX_TEXT * 2:
        out.pop("ops")
        out["clipped"] = True
    if out.get("ops"):
        # old and new are rebuilt from the word ops on the page ("=" and "-" runs / "=" and "+" runs)
        out.pop("old", None)
        out.pop("new", None)
    return out


def company(tk, rows=None, fetch=None):
    """Redline document for one company (no writes). fetch(cik, accn, doc) -> text; default SEC download."""
    u = BY_TICKER[tk]
    cik = u["cik"]
    if rows is None:
        sub = sec_json(f"https://data.sec.gov/submissions/CIK{cik:010d}.json")
        rows = SS.filing_rows(sub)
        if len([r for r in rows if r["form"] == "10-K"]) < 2:
            rows += SS.older_rows(sub)
    fetch = fetch or SS.fetch_text
    comps = []
    for kind, new, old in pick_pairs(rows):
        res = R.compare(fetch(cik, old["accn"], old["doc"]), fetch(cik, new["accn"], new["doc"]), new["form"])
        for s in res["sections"]:
            s["changes"] = [_trim(c) for c in s["changes"]]
        comps.append({"kind": kind, "form": new["form"], "new": _meta(cik, new), "old": _meta(cik, old),
                      "totals": res["totals"], "parsed": res["parsed"], "sections": res["sections"]})
    return {"schema_version": 1, "engine": R.VERSION, "ticker": tk, "cik": cik, "name": u["name"], "generated": now_iso(),
            "source": "SEC EDGAR primary documents; deterministic paragraph and word alignment (no AI)",
            "comparisons": comps}


def highlights(doc, n=5):
    """Top language changes (not figure-only) across a company's comparisons, for the coverage index."""
    out = []
    for c in doc["comparisons"]:
        for s in c["sections"]:
            for ch in s["changes"]:
                if ch["kind"] == "numbers":
                    continue
                text = " ".join(t for op, t in ch.get("ops") or [] if op == "+") or ch.get("new") or ch.get("old") or \
                    " ".join(t for op, t in ch.get("ops") or [] if op == "-")
                out.append({"kind": c["kind"], "form": c["form"], "filed": c["new"]["filed"], "section": s["title"], "section_id": s["id"],
                            "change": ch["kind"], "score": ch["score"], "signals": ch["signals"], "excerpt": text[:280]})
    return sorted(out, key=lambda x: -x["score"])[:n]


def summary_row(doc):
    row = {"ticker": doc["ticker"], "name": doc["name"], "generated": doc["generated"], "comparisons": []}
    for c in doc["comparisons"]:
        lang = sum(c["totals"][k] for k in ("added", "removed", "modified"))
        sig = sorted({x for s in c["sections"] for x in s.get("signals", [])})
        row["comparisons"].append({"kind": c["kind"], "form": c["form"], "filed": c["new"]["filed"], "old_filed": c["old"]["filed"],
                                   "language_changes": lang, "numbers": c["totals"]["numbers"], "signals": sig,
                                   "top_sections": [s["title"] for s in sorted(c["sections"], key=lambda s: -s.get("score", 0))[:3]]})
    row["highlights"] = highlights(doc)
    return row


def _accns(doc):
    return {c["kind"]: [c["new"]["accn"], c["old"]["accn"]] for c in doc["comparisons"]}


def run_batch(sb, tickers, state, deadline=None, workers=6, log=print):
    """Compare and publish each ticker; returns {ticker: summary_row} for those written."""
    done, failed = {}, {}

    def one(tk):
        if deadline and time.time() > deadline:
            return tk, None, "deadline"
        try:
            doc = company(tk)
            sb.put_json(f"redlines/{tk}.json", doc, cache="max-age=600")
            return tk, doc, None
        except Exception as e:
            return tk, None, str(e)[:200]

    with cf.ThreadPoolExecutor(workers) as ex:
        for tk, doc, err in ex.map(one, tickers):
            if doc:
                done[tk] = summary_row(doc)
                state["tickers"][tk] = {"at": doc["generated"], "accns": _accns(doc), "comparisons": len(doc["comparisons"])}
            elif err != "deadline":
                failed[tk] = err
                state["tickers"].setdefault(tk, {})["error"] = err
    return done, failed


def merge_index(sb, rows):
    with AggregateLease(sb, name="redlines-index", seconds=180):
        idx = sb.get_json("redlines/index.json") or {"schema_version": 1, "companies": {}}
        idx["companies"].update(rows)
        feed = [dict(h, ticker=t, name=r["name"]) for t, r in idx["companies"].items() for h in r.get("highlights", [])[:2]]
        idx["recent"] = sorted(feed, key=lambda h: (h["filed"], h["score"]), reverse=True)[:300]
        idx["generated"] = now_iso()
        idx["count"] = len(idx["companies"])
        sb.put_json("redlines/index.json", idx, cache="max-age=300")
        return idx["count"]


def _latest_accns(tk):
    cik = BY_TICKER[tk]["cik"]
    rows = SS.filing_rows(sec_json(f"https://data.sec.gov/submissions/CIK{cik:010d}.json"))
    return {kind: [n["accn"], o["accn"]] for kind, n, o in pick_pairs(rows)}


def rotate_job(sb, time_budget=680, workers=6, check=120, per_run=80, tickers=None, log=print):
    t0 = time.time()
    deadline = t0 + time_budget - 90
    state = _get_state(sb)
    order = tickers or [u[0] for u in UNIVERSE]
    todo = [t for t in order if t not in state["tickers"] or state["tickers"][t].get("error")][:per_run]
    stats = {"new": 0, "refreshed": 0, "checked": 0, "failed": {}}
    if len(todo) < per_run and not tickers:
        cur = state.get("cursor", 0) % max(1, len(order))
        sl = (order[cur:] + order[:cur])[:check]
        state["cursor"] = (cur + check) % max(1, len(order))
        for t in sl:
            if time.time() > deadline - 200 or len(todo) >= per_run:
                break
            try:
                stats["checked"] += 1
                if _latest_accns(t) != (state["tickers"].get(t) or {}).get("accns"):
                    todo.append(t)
            except Exception as e:
                stats["failed"][t] = "check: " + str(e)[:120]
    before = set(state["tickers"])
    done, failed = run_batch(sb, todo, state, deadline=deadline, workers=workers, log=log)
    stats["failed"].update(failed)
    stats["new"] = len([t for t in done if t not in before])
    stats["refreshed"] = len(done) - stats["new"]
    if done:
        for attempt in range(6):
            try:
                stats["index_companies"] = merge_index(sb, done)
                break
            except LeaseBusy:
                time.sleep(5)
    state["generated"] = now_iso()
    _put_state(sb, state)
    stats["remaining"] = len([t for t in (tickers or [u[0] for u in UNIVERSE]) if t not in state["tickers"]])
    stats["seconds"] = round(time.time() - t0, 1)
    return stats


def _get_state(sb):
    try:
        raw = sb.get_object("raw", STATE)
        if raw:
            return json.loads(raw)
    except Exception:
        pass
    return {"tickers": {}, "cursor": 0}


def _put_state(sb, state):
    sb.put_object("raw", STATE, json.dumps(state, separators=(",", ":")), cache="no-store")


def run(sb, time_budget=680):
    with Run(sb, "redlines") as r:
        r.stats = rotate_job(sb, time_budget=time_budget)
        return r.stats


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", nargs="*")
    ap.add_argument("--budget", type=float, default=3000)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--per-run", type=int, default=400)
    a = ap.parse_args()
    print(json.dumps(rotate_job(Supabase(), time_budget=a.budget, workers=a.workers, per_run=a.per_run, tickers=a.tickers), default=str)[:3000])
