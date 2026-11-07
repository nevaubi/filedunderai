"""Automated KPI extraction from stored IR documents.

The model proposes operating KPIs, segment and geographic breakdowns and company-defined non-GAAP figures from a
company's earnings releases and supplements, in the kpitools extraction schema (every value with the page, an exact
quote, the printed figure and its scale). kpitools.validate then checks every value against the source page; values
that do not check out are dropped, breakdowns that do not sum to their total lose their "sums to parent" claim, and
only what survives is published. Nothing the model says is published without that check.

Two publication modes, so hand-built KPI files are never overwritten:
- companies with no KPIs yet: the cumulative automated extraction (kept privately in raw/kpi_extract/{T}.json) is
  merged with the new values and published in full (kpitools.publish);
- companies whose KPIs were built by hand: only new points for KPI ids that already exist, plus new KPI ids, are
  added (kpitools.publish additive), so existing values and sources stay byte-for-byte unchanged.

    python -m etl.kpi_extract --tickers REAL,HLMN          one-off for some companies
    python -m etl.kpi_extract --budget 600 --workers 3     cron batch: companies whose earnings documents are new

State (private): raw/kpi_extract/state.json, per company: the earnings document ids already read, the outcome and
counts. A company is due when one of its earnings documents has not been read yet.
"""
import argparse
import concurrent.futures as cf
import copy
import datetime as dt
import json
import os
import re
import time

from . import kpitools
from .common import Supabase, now_iso
from .llm import LLMError, chat_json

MODELS = [m for m in (os.environ.get("FU_KPI_MODEL") or "kimi-k3p1-pro", os.environ.get("FU_KPI_FALLBACK") or "deepseek-v4p1-flash") if m]
PROMPT_VERSION = "kpi-extract-v1"
STATE_PATH = "kpi_extract/state.json"
DOC_CHARS = 60000           # text per document sent to the model
PERIODS_NEW = 6             # fiscal periods read for a company with no KPIs (each release also prints the prior year)
PERIODS_UPDATE = 2          # fiscal periods read for a company that already has KPIs
EARNINGS_TYPES = ("release", "supplement", "presentation")

SYSTEM = """You extract company-specific KPIs from one investor-relations document. Output JSON only.

Extract figures that are printed in the document text for the periods it reports:
- operating metrics (units, orders, deliveries, customers, subscribers, members, users, ARR, RPO, backlog, bookings,
  book-to-bill, same-store or comparable sales, traffic, average ticket, ARPU, retention, churn, occupancy, RevPAR,
  load factor, production volumes, realized prices, AUM, loans, deposits, net interest margin, efficiency ratio,
  CET1, charge-off rate, combined ratio, medical care ratio, FFO per share, store counts, headcount);
- revenue, operating income or margin by segment, product line or geography;
- company-defined non-GAAP measures (adjusted EBITDA, adjusted EPS, free cash flow, organic growth, constant-currency
  growth) and their margins.
Skip consolidated GAAP statement totals (total revenue, net income, GAAP EPS, total assets, cash flow totals): they
come from XBRL. Skip guidance (forward ranges) and anything not printed as a number.

For every value:
- "kind": "Q" for a fiscal quarter, "A" for a fiscal year, "Y" for year-to-date through a quarter end;
- "period_end": the exact period end date, chosen from the list of the company's period ends you are given;
- "page": the page number shown in the "=== page N ===" header the figure is on;
- "quote": a short span (under 200 characters) copied EXACTLY, character for character, from that page and
  containing the printed figure; do not paraphrase, reorder, add or remove words or spaces between words;
- "printed": the figure exactly as printed in the quote, e.g. "1,234.5", "(42.1)", "12.5%", "$3.10";
- "scale": what the printed figure is in: 1, 1000 (thousands), 1000000 (millions) or 1000000000 (billions), from
  the table heading or the words next to the figure; percentages and per-share figures use 1;
- "value": printed x scale in base units (dollars, units, members); percentages as printed points (12.5 for 12.5%);
  negative when the printed figure is in parentheses or the text says it is a decline or loss.
A table row usually prints the current period and the prior-year period: give both, each with its own period_end.

KPI fields: "id" (snake_case, stable; reuse an id from the existing list when it is the same measure), "name",
"category" (e.g. "Operating metrics", "Segments", "Geography", "Non-GAAP"), "unit" ("USD", "count", "%",
"USD_per_share", "USD_per_unit", "x", "days" or a short unit word), "parent" (id of the total a breakdown belongs to,
or null), "sums_to_parent" (true only when the children listed form a complete breakdown that adds up to the
parent), "description" (one sentence: what it measures, as the company defines it).

Return {"kpis": [{"id", "name", "category", "unit", "parent", "sums_to_parent", "description",
"values": [{"kind", "period_end", "page", "quote", "printed", "scale", "value"}]}]}.
Return {"kpis": []} when the document has no such figures. Prefer fewer, exact values over many uncertain ones."""


# ---------------------------------------------------------------- inputs
def period_ends(sb, ticker):
    """Company period ends with fiscal labels, from the published statements bundle."""
    b = sb.get_json(f"companies/{ticker}.json") or {}
    fye = (b.get("profile") or {}).get("fiscalYearEnd") or "1231"
    ends = set()
    for rows in (b.get("statements") or {}).values():
        for r in rows if isinstance(rows, list) else []:
            for x in r.get("quarterly", []) or []:
                if x.get("end"):
                    ends.add(("Q", x["end"]))
            for x in r.get("annual", []) or []:
                if x.get("end"):
                    ends.add(("A", x["end"]))
    from .fundamentals import fy_label
    out = []
    for kind, end in sorted(ends, key=lambda t: (t[1], t[0])):
        try:
            out.append({"kind": kind, "period_end": end, "label": fy_label(fye, end, kind)})
        except Exception:
            continue
    return fye, out[-40:]


def earnings_docs(docs, periods, since=None):
    """Earnings releases and supplements with text, newest fiscal periods first: per period the SEC exhibit release
    (8-K Item 2.02) first, then a supplement, at most two documents per period."""
    by_period = {}
    for d in docs:
        if not d.get("text_path") or (d.get("doc_type") or "") not in EARNINGS_TYPES or not d.get("period_end"):
            continue
        if (d.get("ext") or "").lower() in (".xls", ".xlsx", ".xlsm", ".csv"):
            continue  # workbook cells need sheet/cell references; the page-quote path covers the release text
        if since and d["period_end"] <= since:
            continue
        by_period.setdefault(d["period_end"], []).append(d)

    def rank(d):
        title = d.get("title") or ""
        sec_results = title.startswith("8-K") and "2.02" in title
        return (0 if d["doc_type"] == "release" and sec_results else 1 if d["doc_type"] == "release" else
                2 if d["doc_type"] == "supplement" else 3, -(d.get("pages") or 0))
    out = []
    for pe in sorted(by_period, reverse=True)[:periods]:
        picks = sorted(by_period[pe], key=rank)
        chosen = [picks[0]]
        supp = next((d for d in picks[1:] if d["doc_type"] == "supplement"), None)
        if supp:
            chosen.append(supp)
        out += chosen
    return out


def doc_text(sb, d, limit=DOC_CHARS):
    t = kpitools.text_of(sb, d)
    if not t or t.get("kind") != "pages":
        return None, 0
    parts, used = [], 0
    for i, page in enumerate(t["pages"], 1):
        page = re.sub(r"[ \t]+", " ", page or "").strip()
        if not page:
            continue
        chunk = f"=== page {i} ===\n{page}\n"
        if used + len(chunk) > limit:
            chunk = chunk[:max(0, limit - used)]
        parts.append(chunk)
        used += len(chunk)
        if used >= limit:
            break
    return "".join(parts), len(t["pages"])


def prompt_for(ticker, name, d, text, periods, existing_ids):
    lines = [f"Company: {name} ({ticker})",
             f"Document: {(d.get('title') or '').split('||')[0][:160]} ({d.get('doc_type')}, fiscal period {d.get('fiscal_label')}, period end {d.get('period_end')})",
             "Company period ends (kind, period_end, fiscal label): " + "; ".join(f"{p['kind']} {p['period_end']} {p['label']}" for p in periods),
             "Existing KPI ids (reuse when the same measure): " + (", ".join(existing_ids[:300]) if existing_ids else "none"),
             "", "Document text:", text]
    return "\n".join(lines)


# ---------------------------------------------------------------- merge and verify
def _clean_kpi(k):
    kid = re.sub(r"[^a-z0-9_]+", "_", str(k.get("id") or "").lower()).strip("_")[:80]
    if not kid or not isinstance(k.get("values"), list):
        return None
    unit = str(k.get("unit") or "").strip()[:24] or "count"
    return {"id": kid, "name": str(k.get("name") or kid)[:120], "category": str(k.get("category") or "Operating metrics")[:60],
            "subcategory": None, "unit": unit, "parent": (re.sub(r"[^a-z0-9_]+", "_", str(k["parent"]).lower()).strip("_") or None) if k.get("parent") else None,
            "sums_to_parent": bool(k.get("sums_to_parent")), "is_total": False, "description": str(k.get("description") or "")[:400], "values": []}


def _clean_value(v, doc_id, valid_ends):
    try:
        kind = v.get("kind")
        pe = str(v.get("period_end") or "")
        # year-to-date figures end on a quarter end (or the fiscal year end)
        allowed = valid_ends.get("A", set()) if kind == "A" else valid_ends.get("Q", set()) | valid_ends.get("A", set())
        if kind not in ("Q", "A", "Y") or pe not in allowed:
            return None
        page = int(v.get("page") or 1)
        value = float(v["value"])
        scale = float(v.get("scale") or 1)
        if not (value == value) or abs(value) == float("inf") or scale <= 0:
            return None
        quote = str(v.get("quote") or "")[:400]
        printed = str(v.get("printed") or "").strip()[:40] or None
        return {"kind": kind, "period_end": pe, "value": value, "doc": doc_id, "page": page, "sheet": None, "cell": None,
                "quote": quote, "printed": printed, "scale": int(scale) if scale.is_integer() else scale}
    except (TypeError, ValueError, KeyError):
        return None


def merge(base, proposals):
    """Adds proposed KPIs and values to an extraction (dict with "kpis"); first definition of an id wins, one value
    per (kind, period_end), the earlier one kept."""
    out = copy.deepcopy(base)
    by_id = {k["id"]: k for k in out["kpis"]}
    for k in proposals:
        cur = by_id.get(k["id"])
        if cur is None:
            cur = {**k, "values": []}
            out["kpis"].append(cur)
            by_id[k["id"]] = cur
        have = {(v["kind"], v["period_end"]) for v in cur["values"]}
        for v in k["values"]:
            if (v["kind"], v["period_end"]) not in have:
                cur["values"].append(v)
                have.add((v["kind"], v["period_end"]))
    return out


def verify(sb, data):
    """Keeps only values that check out against their page, drops KPIs left without values, and removes the
    sums-to-parent claim from any breakdown that does not tie. Returns (clean data, report)."""
    data = copy.deepcopy(data)
    report, data = kpitools.validate(sb, data, fix=True)
    data["kpis"] = [k for k in data["kpis"] if k.get("values")]
    ids = {k["id"] for k in data["kpis"]}
    for k in data["kpis"]:
        if k.get("parent") and k["parent"] not in ids:
            k["parent"], k["sums_to_parent"] = None, False
    broken = {(t["parent"], t.get("group")) for t in report.get("tie_failures", [])}
    if broken:
        for k in data["kpis"]:
            if k.get("sums_to_parent") and (k.get("parent"), k.get("group")) in broken:
                k["sums_to_parent"] = False
        report2, data = kpitools.validate(sb, data, fix=True)
        report2["bad_values"] = report["bad_values"] + report2["bad_values"]
        report = report2
    data["derived"] = []
    return data, report


# ---------------------------------------------------------------- per company
def _publish(sb, data, attempts=6, **kw):
    """kpitools.publish takes the aggregate lease; other publishers hold it briefly, so wait rather than lose the
    model work."""
    from .common import LeaseBusy
    for k in range(attempts):
        try:
            return kpitools.publish(sb, data, **kw)
        except LeaseBusy:
            if k == attempts - 1:
                raise
            time.sleep(15 + 10 * k)


def _existing(sb, ticker):
    defs = sb.select("kpi_definitions", f"select=kpi_id,name,unit,parent_id&ticker=eq.{ticker}&source_kind=eq.ir")
    pts = sb.select("kpi_values", f"select=kpi_id,period_kind,period_end&ticker=eq.{ticker}&source_kind=eq.ir")
    return defs, {(p["kpi_id"], p["period_kind"], p["period_end"]) for p in pts}


def _load_auto(sb, ticker):
    raw = sb.get_object("raw", f"kpi_extract/{ticker}.json")
    return json.loads(raw) if raw else None


def company(sb, ticker, name, state, complete=None, deadline=None, log=print):
    """Reads the company's unread earnings documents, verifies and publishes. Returns the new state entry."""
    complete = complete or (lambda messages: chat_json(MODELS, messages, max_tokens=12000, deadline=deadline, timeout=300))
    entry = dict(state or {})
    defs, points = _existing(sb, ticker)
    auto = _load_auto(sb, ticker)
    manual = bool(defs) and not auto  # KPIs built by hand: additive only
    fye, periods = period_ends(sb, ticker)
    if not periods:
        return {**entry, "status": "skipped", "reason": "no statement periods", "at": now_iso()}
    valid_ends = {"Q": {p["period_end"] for p in periods if p["kind"] == "Q"}, "A": {p["period_end"] for p in periods if p["kind"] == "A"}}
    docs = kpitools.docs_for(sb, ticker)
    read = set(entry.get("read") or [])
    latest_point = max((pe for (_k, _kind, pe) in points), default=None)
    picks = [d for d in earnings_docs(docs, PERIODS_UPDATE if defs else PERIODS_NEW, since=latest_point if manual else None) if d["id"] not in read]
    if not picks:
        return {**entry, "status": entry.get("status") or "current", "checked_at": now_iso()}
    base = auto or {"ticker": ticker, "kpis": [], "derived": [], "notes": ""}
    existing_ids = sorted({d["kpi_id"] for d in defs} | {k["id"] for k in base["kpis"]})
    proposals, read_now, errors, model_used = [], [], [], None
    for d in picks:
        if deadline and time.time() > deadline - 60:
            break
        text, _pages = doc_text(sb, d)
        if not text:
            read_now.append(d["id"])
            continue
        messages = [{"role": "system", "content": SYSTEM},
                    {"role": "user", "content": prompt_for(ticker, name, d, text, periods, existing_ids + [k["id"] for k in proposals])}]
        try:
            obj, model_used, _meta = complete(messages)
        except LLMError as e:
            errors.append(f"{d['id']}: {str(e)[:120]}")
            continue
        for raw in (obj or {}).get("kpis") or []:
            k = _clean_kpi(raw) if isinstance(raw, dict) else None
            if not k:
                continue
            k["values"] = [x for x in (_clean_value(v, d["id"], valid_ends) for v in raw.get("values") or [] if isinstance(v, dict)) if x]
            if k["values"]:
                proposals.append(k)
        read_now.append(d["id"])
    if not read_now:
        return {**entry, "status": "error", "errors": errors[:5], "at": now_iso()}
    merged = merge(base, proposals)
    merged["ticker"] = ticker
    clean, report = verify(sb, merged)
    kept = sum(len(k["values"]) for k in clean["kpis"])
    proposed = sum(len(k["values"]) for k in proposals)
    clean["notes"] = (f"Extracted automatically ({model_used or MODELS[0]}, {PROMPT_VERSION}); every value was checked against "
                      "its source page (quote, printed figure and scale) before publication.")
    published = 0
    if manual:
        # only new points; existing values and their sources stay as they are
        known = {d["kpi_id"] for d in defs}
        add = {"ticker": ticker, "kpis": [], "derived": [], "notes": clean["notes"]}
        for k in clean["kpis"]:
            vals = [v for v in k["values"] if (k["id"], v["kind"], v["period_end"]) not in points]
            if not vals:
                continue
            kk = {**k, "values": vals}
            if k["id"] in known:
                d0 = next(x for x in defs if x["kpi_id"] == k["id"])
                kk.update(unit=d0.get("unit") or k["unit"], parent=d0.get("parent_id"), sums_to_parent=False)
            add["kpis"].append(kk)
        if add["kpis"]:
            from .common import LeaseBusy
            try:
                _publish(sb, add, fye=fye, additive=True)
                published = sum(len(k["values"]) for k in add["kpis"])
            except LeaseBusy as e:
                # nothing written: leave the documents unread so the next run tries again
                return {**entry, "status": "error", "errors": [f"additive: {str(e)[:160]}"], "at": now_iso()}
            except ValueError as e:
                errors.append(f"additive: {str(e)[:200]}")
    elif clean["kpis"]:
        _publish(sb, clean, fye=fye)
        sb.put_object("raw", f"kpi_extract/{ticker}.json", json.dumps(clean, separators=(",", ":"), default=str), "application/json", "no-store")
        published = kept
    status = "ok" if published or not proposals else "nothing_verified"
    if errors and not published:
        status = "error" if not proposals else status
    return {"status": status, "mode": "additive" if manual else "auto", "read": sorted(read | set(read_now)),
            "proposed": proposed, "verified": kept, "published": published, "rejected": len(report.get("bad_values", [])),
            "kpis": len(clean["kpis"]), "errors": errors[:5], "model": model_used, "at": now_iso()}


# ---------------------------------------------------------------- batch
def load_state(sb):
    raw = sb.get_object("raw", STATE_PATH)
    return json.loads(raw) if raw else {"version": 1, "companies": {}}


def save_state(sb, updates, index_pending=None):
    """Read-modify-write: several cron runs can overlap, so each run writes back only the companies it handled."""
    state = load_state(sb)
    state.setdefault("companies", {}).update(updates)
    if index_pending is not None:
        state["index_pending"] = sorted(set(index_pending))
    state["generated"] = now_iso()
    sb.put_object("raw", STATE_PATH, json.dumps(state, separators=(",", ":"), default=str), "application/json", "no-store")
    return state


def claimed(sb, ticker, fn):
    """Runs fn() holding a per-company lease, so overlapping cron runs never extract the same company twice."""
    from .common import AggregateLease, LeaseBusy
    try:
        with AggregateLease(sb, name=f"kpi-extract:{ticker}", seconds=600):
            return fn()
    except LeaseBusy:
        return None


def due(sb, state, tickers=None, limit=None):
    """Companies with earnings documents not read yet: no KPIs at all first, then the rest by oldest check."""
    from .universe import UNIVERSE
    names = {t: n for t, _c, n, *_ in UNIVERSE}
    ix = (sb.get_json("meta/coverage_ir.json") or {}).get("companies") or {}
    cands = [t for t in (tickers or names) if t in names and (ix.get(t) or {}).get("docs")]
    seen = state.get("companies") or {}

    def key(t):
        e = seen.get(t) or {}
        failing = e.get("status") == "error" and e.get("fails", 0) >= 3
        return (failing, 1 if (ix.get(t) or {}).get("kpis") else 0, e.get("checked_at") or e.get("at") or "")
    out = sorted(cands, key=key)
    return out[:limit] if limit else out, names


def run(sb, tickers=None, budget=600, workers=3, complete=None, log=print):
    t0 = time.time()
    deadline = t0 + budget
    state = load_state(sb)
    todo, names = due(sb, state, tickers)
    done, failed, published, skipped = [], {}, 0, 0
    with cf.ThreadPoolExecutor(workers) as ex:
        futs = {}
        it = iter(todo)

        def submit():
            for tk in it:
                if time.time() > deadline - 240:
                    return False
                prev = (state.get("companies") or {}).get(tk)
                futs[ex.submit(claimed, sb, tk, lambda tk=tk, prev=prev: company(sb, tk, names[tk], prev, complete, deadline, log))] = tk
                return True
            return False
        for _ in range(workers):
            submit()
        while futs:
            fin, _ = cf.wait(list(futs), return_when=cf.FIRST_COMPLETED)
            for f in fin:
                tk = futs.pop(f)
                prev = (state.get("companies") or {}).get(tk) or {}
                try:
                    e = f.result()
                    if e is None:  # another run holds this company
                        skipped += 1
                        submit()
                        continue
                    e["fails"] = prev.get("fails", 0) + 1 if e.get("status") == "error" else 0
                    published += e.get("published") or 0
                    if e.get("status") == "error":
                        failed[tk] = "; ".join(e.get("errors") or [])[:200]
                    elif e.get("published"):
                        done.append(tk)
                except Exception as err:
                    e = {**prev, "status": "error", "errors": [str(err)[:200]], "fails": prev.get("fails", 0) + 1, "at": now_iso()}
                    failed[tk] = str(err)[:200]
                state = save_state(sb, {tk: e})
                submit()
    pending = sorted(set(state.get("index_pending") or []) | set(done))
    if pending:
        try:
            from .kpitools import build_index
            build_index(sb, coverage_updates={}, kpi_updates=pending)
            pending = []
        except Exception as e:
            log(f"coverage index KPI counts not refreshed (kept for the next run): {str(e)[:200]}")
        save_state(sb, {}, index_pending=pending)
    return {"done": len(done), "failed": failed, "published_values": published, "busy": skipped, "seconds": round(time.time() - t0, 1)}


def kpi_extract_job(sb, tickers=None, budget=600, workers=3):
    from .common import Run
    with Run(sb, "kpi_extract") as r:
        out = run(sb, tickers=tickers, budget=budget, workers=workers)
        r.stats.update(out)
        return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", default="")
    ap.add_argument("--budget", type=float, default=600)
    ap.add_argument("--workers", type=int, default=3)
    a = ap.parse_args()
    sb = Supabase()
    tickers = [t.strip().upper() for t in a.tickers.split(",") if t.strip()] or None
    print(json.dumps(kpi_extract_job(sb, tickers=tickers, budget=a.budget, workers=a.workers), default=str))


if __name__ == "__main__":
    main()
