"""KPI extraction tooling: read stored IR documents, validate an extraction file, and publish it.

    python -m etl.kpitools docs TICKER                    list stored documents (id, type, period, pages)
    python -m etl.kpitools text DOCID [PAGE]              print a document page (1-based) or every page
    python -m etl.kpitools sheet DOCID [SHEET]            print a workbook sheet with A1 cell references
    python -m etl.kpitools grep TICKER PATTERN            search every document of a company (regex, case-insensitive)
    python -m etl.kpitools periods TICKER                 fiscal labels and exact period end dates to use
    python -m etl.kpitools xbrl TICKER                    summary of the XBRL segment tables already on the site
    python -m etl.kpitools validate FILE                  check an extraction file: quotes, values, sums, Q4
    python -m etl.kpitools publish FILE                   validate, then write kpis/{T}.json and kpi rows

Extraction file schema (JSON):
{
  "ticker": "NFLX",
  "kpis": [
    {"id": "paid_memberships_total", "name": "Paid memberships", "category": "Members", "subcategory": null,
     "unit": "count" | "USD" | "%" | "USD_per_unit" | "x" | "days" | "units" | text,
     "parent": null | "<kpi id>", "sums_to_parent": false, "subtracts": false, "group": null | "by_region", "is_total": false, "description": "...",
     "values": [
        {"kind": "Q" | "A" | "I" | "Y" | "T", "period_end": "2026-09-30", "fiscal_label": "Q3 FY26", "value": 301630000,
         "doc": "<document id>", "page": 7, "sheet": null, "cell": null,
         "quote": "exact text copied from that page that contains the printed number",
         "printed": "301.63", "scale": 1000000}
     ]}
  ],
  "derived": [   // values computed rather than printed (for example Q4 = FY - Q1 - Q2 - Q3)
     {"kpi": "<kpi id>", "kind": "Q", "period_end": "...", "fiscal_label": "Q4 FY25", "value": 123,
      "formula": "FY2025 - Q1 - Q2 - Q3", "inputs": [{"kind": "A", "period_end": "..."}, {"kind": "Q", "period_end": "..."}]}
  ],
  "notes": "anything the reviewer should know"
}
Values are in base units: dollars (not millions), members (not millions), percentages as printed (12.5 means 12.5%).
"""
import collections
import gzip
import json
import os
import re
import sys
import urllib.parse
import urllib.request

from .common import Supabase, load_env, now_iso

IR_PUBLIC = None
_TEXT_CACHE = {}


def _ir_public(sb):
    return f"{sb.url}/storage/v1/object/public/ir/"


def docs_for(sb, ticker):
    return sb.select("ir_documents", f"select=id,url,title,doc_type,fiscal_label,period_end,ext,bytes,pages,text_path,storage_path,fetched_at&ticker=eq.{ticker}")


def text_of(sb, doc):
    if doc["id"] in _TEXT_CACHE:
        return _TEXT_CACHE[doc["id"]]
    if not doc.get("text_path"):
        return None
    with urllib.request.urlopen(_ir_public(sb) + doc["text_path"], timeout=60) as r:
        raw = r.read()
    try:
        raw = gzip.decompress(raw)
    except OSError:
        pass
    t = json.loads(raw)
    _TEXT_CACHE[doc["id"]] = t
    return t


def col_name(i):
    s, i = "", i + 1
    while i:
        m = (i - 1) % 26
        s = chr(65 + m) + s
        i = (i - 1) // 26
    return s


def parse_ref(ref):
    m = re.match(r"^([A-Z]+)(\d+)$", (ref or "").upper())
    if not m:
        return None
    c = 0
    for ch in m.group(1):
        c = c * 26 + ord(ch) - 64
    return int(m.group(2)) - 1, c - 1


def norm(s):
    # PDF ligatures can come through as NUL characters; Postgres rejects them, so they are read as spaces
    return re.sub(r"\s+", " ", str(s or "").replace("\x00", " ")).strip().lower()


def numbers_in(text):
    """Numbers printed in a string, as floats, with parentheses read as negatives."""
    out = []
    for m in re.finditer(r"\(?-?\$?(?:\d[\d,]*(?:\.\d+)?|(?<![\d.])\.\d+)\)?", text or ""):
        s = m.group(0)
        neg = s.startswith("(") and s.endswith(")") or "-" in s
        try:
            v = float(re.sub(r"[^\d.]", "", s))
        except ValueError:
            continue
        out.append(-v if neg else v)
    return out


def check_value(sb, docs_by_id, v):
    """Returns a list of problems for one extracted value (empty when it checks out)."""
    errs = []
    d = docs_by_id.get(v.get("doc"))
    if not d:
        return [f"unknown document {v.get('doc')}"]
    t = text_of(sb, d)
    if not t:
        return [f"document {d['id']} has no text"]
    value = v.get("value")
    if value is None:
        return ["missing value"]
    if t["kind"] == "sheets":
        sheet = v.get("sheet")
        rows = t["sheets"].get(sheet) if sheet else None
        if rows is None:
            return [f"sheet {sheet!r} not in {list(t['sheets'])[:8]}"]
        rc = parse_ref(v.get("cell"))
        if not rc:
            return [f"bad cell reference {v.get('cell')!r}"]
        r, c = rc
        cellv = rows[r][c] if r < len(rows) and c < len(rows[r]) else None
        if not isinstance(cellv, (int, float)):
            return [f"{sheet}!{v.get('cell')} holds {cellv!r}, not a number"]
        scale = v.get("scale") or 1
        if not _close(cellv * scale, value) and not _close(cellv * scale * 100, value) and not _close(cellv, value):
            errs.append(f"{sheet}!{v.get('cell')} = {cellv} (x{scale}) does not match value {value}")
        return errs
    pages = t["pages"]
    page = v.get("page") or 1
    if page < 1 or page > len(pages):
        return [f"page {page} out of range (1-{len(pages)})"]
    ptxt = norm(pages[page - 1])
    q = norm(v.get("quote"))
    if not q:
        errs.append("missing quote")
    elif q not in ptxt:
        # tolerate quotes that span a line break or repeated spaces differently
        q2 = re.sub(r"[^a-z0-9.,%$()-]", "", q)
        if q2 not in re.sub(r"[^a-z0-9.,%$()-]", "", ptxt):
            errs.append(f"quote not found on page {page}: {v.get('quote')[:80]!r}")
    printed = v.get("printed")
    scale = v.get("scale") or 1
    if printed is not None and str(printed).strip() in ("—", "–", "-", "— ", "nm", "NM") and value == 0:
        return errs
    if printed is not None:
        ps = str(printed)
        # PDF text sometimes splits one figure with spaces ("3 ,110", "1 05,262"); `printed` is a single figure,
        # so spaces inside a run of digits, commas and points are joined before it is read
        if re.fullmatch(r"[\s$(–−-]*\d[\d,. ]*\d[\s)%]*", ps) and re.search(r"[\d.,]\s+[\d,.]", ps):
            ps = re.sub(r"(?<=[\d,.])\s+(?=[\d,.])", "", ps)
        nums = numbers_in(ps)
        if not nums:
            errs.append(f"printed {printed!r} has no number")
        else:
            pv = nums[0] * scale
            if not (_close(pv, value) or _close(-pv, value)):
                errs.append(f"printed {printed} x {scale} = {pv} does not match value {value}")
            elif pv and _close(-pv, value) and not _close(pv, value) and not _negative_ok(str(printed), v.get("quote") or ""):
                errs.append(f"value {value} is negative but nothing in the printed figure or quote says so")
            if q and str(printed).replace("$", "").strip("() ") not in v.get("quote", ""):
                errs.append(f"printed {printed!r} not inside quote")
            elif q and str(printed).strip().startswith("(") and not re.search(r"\(\s*\$?\s*" + re.escape(str(printed).replace("$", "").strip("() ")), v.get("quote", "")):
                errs.append(f"printed {printed!r} has parentheses the quote does not show")
    else:
        if not any(_close(n * s, value) or _close(-n * s, value) for n in numbers_in(v.get("quote")) for s in (1, 1e3, 1e6, 1e9, 0.01)):
            errs.append(f"no number in the quote matches value {value}")
    return errs


_NEG_BEFORE = re.compile(r"\b(down|declin\w*|decreas\w*|lower|fell|fall\w*|drop\w*|negative|loss\w*|reduc\w*|contract\w*|headwind\w*|unfavorabl\w*|outflow\w*|minus|deficit|shrank|below|less|offset by|eliminat\w*|intersegment\w*|intercompany|used in|used by|used for)\b(?:(?!\.\s+[A-Z])[^;]){0,110}$", re.I)
_NEG_AFTER = re.compile(r"^[\s%$a-z]{0,12}\b(lower|less|decline|decrease|drop|down|loss|losses|headwind|unfavorable|negative|reduction|contraction|below)\b", re.I)


def _negative_ok(printed, quote):
    """A negative value needs a sign in the printed figure ((1.2), -1.2, −1.2) or a word around it in the quote
    ("down 4 percent", "3% lower", "losses of $7.0 billion", "(0.09)")."""
    p = printed.strip()
    if p.startswith(("(", "-", "−", "–", "$(", "$-", "$–")) or p.endswith(")"):
        return True
    core = re.escape(p.replace("$", "").strip("() "))
    for m in re.finditer(r"(?<![\d.])" + core + r"(?![\d])", quote):
        i, j = m.start(), m.end()
        pre = quote[max(0, i - 3):i]
        if "(" in pre or pre.rstrip().endswith(("-", "−", "–")):
            return True
        if _NEG_BEFORE.search(quote[:i]) or _NEG_AFTER.search(quote[j:]):
            return True
    return False


def _close(a, b):
    """Equal up to rounding: tight for small numbers (rates, per-share), half a unit for large ones."""
    try:
        a, b = float(a), float(b)
    except (TypeError, ValueError):
        return False
    floor = 0.005 if abs(b) < 1000 else 0.5
    return abs(a - b) <= max(floor, abs(b) * 0.0015)


TIE_REL = float(os.environ.get("KPI_TIE_REL", "0.001"))


def validate(sb, data, fix=False):
    """Checks every value against its source page or cell, recomputes derived values, and checks that children sum to
    parents marked sums_to_parent. Returns (report, cleaned data)."""
    tk = data["ticker"]
    docs = {d["id"]: d for d in docs_for(sb, tk)}
    report = {"ticker": tk, "values": 0, "bad_values": [], "ties": [], "tie_failures": [], "derived_bad": []}
    kpis = {k["id"]: k for k in data.get("kpis", [])}
    for k in data.get("kpis", []):
        good = []
        seen = {}
        for v in k.get("values", []):
            # one value per (kind, period end): an exact repeat is dropped, a conflicting repeat is rejected
            key = (v.get("kind"), v.get("period_end"))
            if key in seen:
                if _close(seen[key], v.get("value")):
                    continue
                report["values"] += 1
                report["bad_values"].append({"kpi": k["id"], "period_end": key[1], "kind": key[0],
                                             "errors": [f"duplicate period with a different value ({seen[key]} vs {v.get('value')})"]})
                continue
            seen[key] = v.get("value")
            if isinstance(v.get("quote"), str) and "\x00" in v["quote"]:
                v["quote"] = re.sub(r" +", " ", v["quote"].replace("\x00", " "))
            report["values"] += 1
            errs = check_value(sb, docs, v)
            if errs:
                report["bad_values"].append({"kpi": k["id"], "period_end": v.get("period_end"), "kind": v.get("kind"), "errors": errs})
            else:
                good.append(v)
        if fix:
            k["values"] = good
    # derived values: recompute from inputs
    idx = {(k["id"], v["kind"], v["period_end"]): v["value"] for k in data.get("kpis", []) for v in k.get("values", [])}

    def _unit(v):
        """Rounding unit of a printed figure: scale x 10^-decimals (e.g. "1,234.5" in millions -> 100,000)."""
        pr = re.sub(r"[^0-9.]", "", str(v.get("printed") or ""))
        dec = len(pr.split(".")[1]) if "." in pr else 0
        return float(v.get("scale") or 1) * 10 ** -dec

    unit = {(k["id"], v["kind"], v["period_end"]): _unit(v) for k in data.get("kpis", []) for v in k.get("values", [])}
    kunit = {k["id"]: (k.get("unit") or "").strip().lower() for k in data.get("kpis", [])}
    for dv in data.get("derived", []):
        # ratios, percentages and per-unit figures are not additive: a quarter cannot be derived by subtraction
        if kunit.get(dv["kpi"]) in ("%", "pct", "percent", "ratio", "x", "bp", "usd_per_unit", "$/unit", "per_share", "usd_per_share"):
            report["derived_bad"].append({**dv, "error": f"unit {kunit.get(dv['kpi'])} is not additive"})
            continue
        ins = []
        for inp in dv.get("inputs", []):
            ins.append(idx.get((dv["kpi"], inp["kind"], inp["period_end"])))
        if None in ins or not ins:
            report["derived_bad"].append({**dv, "error": "missing input"})
            continue
        if dv.get("formula", "").startswith("FY") or "-" in dv.get("formula", ""):
            calc = ins[0] - sum(ins[1:])
        else:
            calc = sum(ins)
        if not _close(calc, dv["value"]):
            report["derived_bad"].append({**dv, "error": f"recomputed {calc}"})
        else:
            idx[(dv["kpi"], dv["kind"], dv["period_end"])] = dv["value"]
            # a derived figure carries the rounding of every printed input it was computed from
            unit[(dv["kpi"], dv["kind"], dv["period_end"])] = sum(unit.get((dv["kpi"], inp["kind"], inp["period_end"]), 0) for inp in dv.get("inputs", []))
    # sums: children flagged sums_to_parent must add up to the parent
    children = {}
    # a child marked "subtracts": true enters its parent's sum with a minus sign
    # (e.g. capital expenditures stored as a positive outflow under free cash flow)
    minus = {k["id"] for k in data.get("kpis", []) if k.get("subtracts")}
    for k in data.get("kpis", []):
        if k.get("parent") and k.get("sums_to_parent"):
            # several complete breakdowns of one parent are allowed: give each its own "group"
            children.setdefault((k["parent"], k.get("group")), []).append(k["id"])
    report.setdefault("tie_incomplete", [])
    for (parent, grp), kids in children.items():
        periods = {(kind, pe) for (kid, kind, pe) in idx if kid == parent}
        for kind, pe in sorted(periods):
            parts = [idx.get((c, kind, pe)) for c in kids]
            if any(p is None for p in parts):
                if any(p is not None for p in parts):
                    report["tie_incomplete"].append({"parent": parent, "group": grp, "kind": kind, "period_end": pe,
                                                     "missing": [c for c, p in zip(kids, parts) if p is None]})
                continue
            s, tot = sum(-p if c in minus else p for c, p in zip(kids, parts)), idx[(parent, kind, pe)]
            # each printed figure carries up to half a rounding unit of error, so the sum may drift by that much per line
            slack = 0.5 * sum(unit.get((c, kind, pe), 0) for c in kids) + 0.5 * unit.get((parent, kind, pe), 0)
            ok = _close(s, tot) or abs(s - tot) <= max(1.0, abs(tot) * TIE_REL, slack * 1.0001)
            (report["ties"] if ok else report["tie_failures"]).append({"parent": parent, "group": grp, "kind": kind, "period_end": pe, "sum": s, "total": tot})
    return report, data


_QUAL = [(re.compile(r"(more than|over|at least|in excess of|exceed(?:ed|ing)?|above|upwards of|north of)\s*$", re.I), ">"),
         (re.compile(r"(nearly|almost|just under|close to|under|below|less than)\s*$", re.I), "<"),
         (re.compile(r"(approximately|approx\.?|about|around|roughly|some|~|≈|an estimated|estimated)\s*$", re.I), "~")]


def qualifier(quote, printed):
    """">" (lower bound), "<" (upper bound) or "~" (approximate) when the words right before the printed figure in the
    quote say so ("over $4.4 billion", "nearly 19 billion", "approximately $2 billion"); None for an exact figure."""
    if not quote or not printed:
        return None
    if str(printed).strip() in ("-", "—", "–") and re.search(r"\b(flat|stable|unchanged|even|in line)\b", quote, re.I):
        return "~"
    num = re.escape(str(printed).strip()).replace("\\,", ",?")
    m = re.search(r"(?:\$\s*)?" + num, quote)
    if not m:
        return None
    before = re.sub(r"[$\s]+$", "", quote[:m.start()])[-40:] + " "
    before = before.rstrip()
    for rx, q in _QUAL:
        if rx.search(before):
            return q
    return None


def _kpi_label(tk, fye, kpi_fye, end, kind):
    """Label for a KPI value. A KPI with its own calendar-year basis ("fye": "1231" on a non-December filer) reads
    "Q1 CY24" / "CY2024" so it is not mistaken for the company's fiscal periods."""
    if not kpi_fye:
        return _label(fye, end, kind, tk)
    lab = _label(kpi_fye, end, kind)
    if kpi_fye == "1231" and (fye or "1231") != "1231":
        lab = lab.replace(" FY", " CY").replace("FY", "CY", 1) if lab.startswith("FY") else lab.replace(" FY", " CY")
    return lab


def _label(fye, end, kind, ticker=None):
    """Fiscal label; "Y" (year to date through a quarter end) reads e.g. "9M FY25". With a ticker, the company's
    year-end history and transition periods apply (a KPI's own "fye" is passed as fye with ticker None)."""
    from .fundamentals import fy_label, period_label
    lab = (lambda k: period_label(ticker, fye, end, k)) if ticker else (lambda k: fy_label(fye, end, k))
    if kind == "A":
        return lab("A")
    q = lab("Q")
    if kind == "Y":
        n, fy = q.split(" ", 1)
        return f"{int(n[1:]) * 3}M {fy}"
    if kind == "T":
        return f"TTM {q}"
    return q


def publish(sb, data, fye="1231"):
    """Writes kpis/{T}.json (grouped for display) and kpi_definitions / kpi_values rows. Only validated values."""
    report, data = validate(sb, data, fix=True)
    tk = data["ticker"]
    # fiscal labels are always recomputed from the company's fiscal year end, never taken from the input file
    from .fundamentals import fy_label
    co = sb.select("companies", f"select=fiscal_year_end&ticker=eq.{tk}")
    fye = (co[0].get("fiscal_year_end") if co else None) or fye
    kfye = {k["id"]: k.get("fye") for k in data.get("kpis", []) if k.get("fye")}
    for k in data.get("kpis", []):
        for v in k.get("values", []):
            v["fiscal_label"] = _kpi_label(tk, fye, kfye.get(k["id"]), v["period_end"], v["kind"])
    for d in data.get("derived", []):
        d["fiscal_label"] = _kpi_label(tk, fye, kfye.get(d.get("kpi")), d["period_end"], d["kind"])
    docs = {d["id"]: d for d in docs_for(sb, tk)}
    tie = {(t["parent"], t["kind"], t["period_end"]): True for t in report["ties"]}
    tie.update({(t["parent"], t["kind"], t["period_end"]): False for t in report["tie_failures"]})
    derived = {(d["kpi"], d["kind"], d["period_end"]): d for d in data.get("derived", []) if not any(x for x in report["derived_bad"] if x["kpi"] == d["kpi"] and x["period_end"] == d["period_end"])}
    kpis = data.get("kpis", [])
    order = {k["id"]: i for i, k in enumerate(kpis)}
    depth = {}

    def dep(k):
        if k["id"] in depth:
            return depth[k["id"]]
        p = next((x for x in kpis if x["id"] == k.get("parent")), None)
        depth[k["id"]] = 0 if not p else min(3, dep(p) + 1)
        return depth[k["id"]]

    groups = {}
    defs, rows = [], []
    # tree order inside each category: each root followed by its descendants
    ordered, seen = [], set()

    def walk(k):
        if k["id"] in seen:
            return
        seen.add(k["id"])
        ordered.append(k)
        for c in kpis:
            if c.get("parent") == k["id"]:
                walk(c)
    for k in kpis:
        if not k.get("parent") or not any(x["id"] == k.get("parent") for x in kpis):
            walk(k)
    for k in kpis:
        walk(k)
    kpis = ordered
    for k in kpis:
        g = groups.setdefault((k.get("category") or "Operating metrics", None), [])
        vals = {"Q": {}, "A": {}, "I": {}, "Y": {}, "T": {}}
        for v in k.get("values", []):
            d = docs.get(v["doc"], {})
            vals[v["kind"]][v["period_end"]] = {"v": v["value"], "doc": v["doc"], "page": v.get("page"), "sheet": v.get("sheet"), "cell": v.get("cell"),
                                                "quote": v.get("quote"), "printed": v.get("printed"), "q": qualifier(v.get("quote"), v.get("printed")), "doc_title": (d.get("title") or "").split("||")[0][:120], "path": d.get("storage_path"),
                                                "ext": d.get("ext"), "url": d.get("url"), "label": v.get("fiscal_label"),
                                                "tie": tie.get((k["id"], v["kind"], v["period_end"]))}
            rows.append({"ticker": tk, "kpi_id": k["id"], "period_kind": v["kind"], "period_end": v["period_end"], "fiscal_label": v.get("fiscal_label"),
                         "value": v["value"], "unit": k.get("unit"), "source_kind": "ir", "source_id": v["doc"],
                         "anchor": {"page": v.get("page"), "sheet": v.get("sheet"), "cell": v.get("cell"), "quote": (v.get("quote") or "")[:400]},
                         "derived": None, "reconciled": tie.get((k["id"], v["kind"], v["period_end"])), "updated_at": now_iso()})
        for (kid, kind, pe), dv in derived.items():
            if kid != k["id"]:
                continue
            src = next((vals[i["kind"]].get(i["period_end"]) for i in dv["inputs"] if vals[i["kind"]].get(i["period_end"])), None) or {}
            vals[kind][pe] = {**src, "v": dv["value"], "label": dv.get("fiscal_label"), "derived": {"formula": dv["formula"], "inputs": dv["inputs"]},
                              "tie": tie.get((k["id"], kind, pe))}
            rows.append({"ticker": tk, "kpi_id": k["id"], "period_kind": kind, "period_end": pe, "fiscal_label": dv.get("fiscal_label"), "value": dv["value"],
                         "unit": k.get("unit"), "source_kind": "ir", "source_id": "derived", "anchor": None, "derived": {"formula": dv["formula"], "inputs": dv["inputs"]},
                         "reconciled": tie.get((k["id"], kind, pe)), "updated_at": now_iso()})
        g.append({"id": k["id"], "name": k["name"], "unit": k.get("unit"), "parent": k.get("parent"), "depth": dep(k), "subcategory": k.get("subcategory"), "is_total": bool(k.get("is_total")),
                  "sums_to_parent": bool(k.get("sums_to_parent")), "description": k.get("description"), "values": vals})
        defs.append({"ticker": tk, "kpi_id": k["id"], "name": k["name"], "category": k.get("category"), "subcategory": k.get("subcategory"), "unit": k.get("unit"),
                     "parent_id": k.get("parent"), "sums_to_parent": bool(k.get("sums_to_parent")), "source_kind": "ir", "description": k.get("description"),
                     "sort": order[k["id"]], "updated_at": now_iso()})
    bundle = {"ticker": tk, "generated": now_iso(), "notes": data.get("notes"),
              "groups": [{"category": c, "subcategory": s, "items": items} for (c, s), items in groups.items() if any(any(it["values"][x]) for it in items for x in "QAIT")],
              "check": {"values": report["values"], "rejected": len(report["bad_values"]), "ties": len(report["ties"]), "tie_failures": len(report["tie_failures"])}}
    sb.put_json(f"kpis/{tk}.json", bundle, cache="max-age=600")
    if defs:
        sb.upsert("kpi_definitions", defs, "ticker,kpi_id")
    if rows:
        uniq = {(r["kpi_id"], r["period_kind"], r["period_end"]): r for r in rows}
        sb.upsert("kpi_values", list(uniq.values()), "ticker,kpi_id,period_kind,period_end,source_kind", batch=1000)
        # drop rows from earlier publishes that are no longer in the file (renamed ids, changed kinds, removed periods)
        existing = sb.select("kpi_values", f"select=kpi_id,period_kind,period_end&ticker=eq.{tk}&source_kind=eq.ir")
        stale = collections.defaultdict(list)
        for r in existing:
            if (r["kpi_id"], r["period_kind"], r["period_end"]) not in uniq:
                stale[(r["kpi_id"], r["period_kind"])].append(r["period_end"])
        live_ids = {r["kpi_id"] for r in uniq.values()}
        gone = sorted({kid for (kid, _k) in stale if kid not in live_ids})
        # ids no longer in the file go in one request per batch; the rest per id and kind with an in() list
        for i in range(0, len(gone), 50):
            ids = ",".join('"' + g.replace('"', '') + '"' for g in gone[i:i + 50])
            sb.delete("kpi_values", f"ticker=eq.{tk}&source_kind=eq.ir&kpi_id=in.({urllib.parse.quote(ids)})")
        for (kid, kind), ends in stale.items():
            if kid in gone:
                continue
            for i in range(0, len(ends), 100):
                sb.delete("kpi_values", f"ticker=eq.{tk}&source_kind=eq.ir&kpi_id=eq.{urllib.parse.quote(kid)}"
                                        f"&period_kind=eq.{kind}&period_end=in.({','.join(ends[i:i + 100])})")
        live = {k["id"] for k in kpis}
        dead = [d["kpi_id"] for d in sb.select("kpi_definitions", f"select=kpi_id&ticker=eq.{tk}&source_kind=eq.ir") if d["kpi_id"] not in live]
        for i in range(0, len(dead), 50):
            ids = ",".join('"' + g.replace('"', '') + '"' for g in dead[i:i + 50])
            sb.delete("kpi_definitions", f"ticker=eq.{tk}&source_kind=eq.ir&kpi_id=in.({urllib.parse.quote(ids)})")
    return report


def main():
    load_env()
    sb = Supabase()
    cmd, args = sys.argv[1], sys.argv[2:]
    if cmd == "docs":
        for d in sorted(docs_for(sb, args[0]), key=lambda d: (d.get("fiscal_label") or "", d["doc_type"] or ""), reverse=True):
            title = (d.get("title") or "").split("||")[0][:90]
            print(f"{d['id']}  {d['doc_type'] or '-':12} {d.get('fiscal_label') or '-':9} {d['ext']:6} pages={d.get('pages')}  {title}")
    elif cmd == "text":
        d = _doc(sb, args[0])
        t = text_of(sb, d)
        if t["kind"] == "sheets":
            print("This is a workbook; use: sheet", args[0], "SHEET. Sheets:", list(t["sheets"]))
            return
        pages = t["pages"]
        sel = [int(args[1])] if len(args) > 1 else range(1, len(pages) + 1)
        for p in sel:
            print(f"===== page {p} / {len(pages)} =====")
            print(pages[p - 1])
    elif cmd == "sheet":
        d = _doc(sb, args[0])
        t = text_of(sb, d)
        if t["kind"] != "sheets":
            print("not a workbook")
            return
        names = list(t["sheets"])
        if len(args) < 2:
            print("sheets:", names)
            return
        rows = t["sheets"][args[1]]
        for r_i, row in enumerate(rows):
            cells = [f"{col_name(c)}{r_i + 1}={v}" for c, v in enumerate(row) if v not in ("", None)]
            if cells:
                print(" | ".join(cells))
    elif cmd == "grep":
        rx = re.compile(args[1], re.I)
        for d in docs_for(sb, args[0]):
            t = text_of(sb, d)
            if not t:
                continue
            if t["kind"] == "pages":
                for i, p in enumerate(t["pages"]):
                    for line in p.splitlines():
                        if rx.search(line):
                            print(f"{d['id']} p{i + 1} [{d.get('fiscal_label')} {d['doc_type']}]: {line.strip()[:200]}")
            else:
                for sh, rows in t["sheets"].items():
                    for r_i, row in enumerate(rows):
                        line = " | ".join(str(v) for v in row if v not in ("", None))
                        if rx.search(line):
                            print(f"{d['id']} {sh}!row{r_i + 1} [{d.get('fiscal_label')}]: {line[:200]}")
    elif cmd == "index":
        print(build_index(sb))
    elif cmd == "periods":
        b = sb.get_json(f"companies/{args[0]}.json") or {}
        from .fundamentals import fy_label
        fye = (b.get("profile") or {}).get("fiscalYearEnd") or "1231"
        ends = set()
        for rows in (b.get("statements") or {}).values():
            for r in rows:
                for x in r.get("quarterly", []):
                    ends.add(("Q", x["end"], x.get("start")))
                for x in r.get("annual", []):
                    ends.add(("A", x["end"], x.get("start")))
        print(f"fiscal year end (MMDD): {fye}")
        for k, e, st in sorted(ends, key=lambda t: (t[1], t[0]))[-40:]:
            print(f"{k} {fy_label(fye, e, k):9} start={st} end={e}")
    elif cmd == "xbrl":
        b = sb.get_json(f"segments/{args[0]}.json") or {}
        for t in b.get("tables", []):
            print(f"{t['metric']} by {t['category_label']}{' (summary)' if t.get('view') else ''}: " + ", ".join(r["label"] for r in t["rows"]))
    elif cmd in ("validate", "publish"):
        data = json.load(open(args[0]))
        if cmd == "validate":
            report, _ = validate(sb, data)
        else:
            report = publish(sb, data)
        print(json.dumps({k: (v if not isinstance(v, list) else v[:40]) for k, v in report.items()}, indent=1, default=str))
        print(f"\nvalues={report['values']} rejected={len(report['bad_values'])} ties={len(report['ties'])} tie_failures={len(report['tie_failures'])} "
              f"tie_incomplete={len(report.get('tie_incomplete', []))} derived_bad={len(report['derived_bad'])}")
        if cmd == "publish":
            print(f"PUBLISHED kpis/{data['ticker']}.json and kpi rows")


def build_index(sb):
    """meta/coverage_ir.json: IR documents, KPI and segment coverage per company, for the Data status page."""
    import collections
    docs = sb.select("ir_documents", "select=ticker,doc_type,bytes")
    defs = sb.select("kpi_definitions", "select=ticker,kpi_id")
    vals = sb.select("kpi_values", "select=ticker&source_kind=eq.ir")
    seg = (sb.get_json("meta/segments.json") or {}).get("companies", {})
    by = collections.defaultdict(lambda: {"docs": 0, "bytes": 0, "kpis": 0, "kpi_values": 0, "types": collections.Counter()})
    for d in docs:
        b = by[d["ticker"]]
        b["docs"] += 1
        b["bytes"] += d.get("bytes") or 0
        b["types"][d.get("doc_type") or "other"] += 1
    for d in defs:
        by[d["ticker"]]["kpis"] += 1
    for v in vals:
        by[v["ticker"]]["kpi_values"] += 1
    out = {}
    for tk, b in by.items():
        out[tk] = {**b, "types": dict(b["types"])}
    for tk, sg in seg.items():
        out.setdefault(tk, {"docs": 0, "bytes": 0, "kpis": 0, "kpi_values": 0, "types": {}})
        out[tk]["segment_tables"] = sg.get("tables")
        out[tk]["segment_ties"] = sg.get("recon_ok")
    summary = {"companies_with_docs": sum(1 for v in out.values() if v["docs"]), "documents": len(docs), "bytes": sum(v["bytes"] for v in out.values()),
               "companies_with_kpis": sum(1 for v in out.values() if v["kpis"]), "kpis": len(defs), "kpi_values": len(vals),
               "companies_with_segments": sum(1 for v in out.values() if v.get("segment_tables")), "segment_tables": sum(v.get("segment_tables") or 0 for v in out.values())}
    sb.put_json("meta/coverage_ir.json", {"generated": now_iso(), "summary": summary, "companies": out}, cache="max-age=300")
    return summary


def _doc(sb, did):
    r = sb.select("ir_documents", f"select=*&id=eq.{did}")
    if not r:
        raise SystemExit(f"no document {did}")
    return r[0]


if __name__ == "__main__":
    main()
