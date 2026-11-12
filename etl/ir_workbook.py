"""Trend workbooks: a company's quarterly supplement history laid out as one spreadsheet, turned into KPI values that
each point at the page of the supplement that prints them.

A trend workbook has one sheet per statement or business. Row 4 names the periods ("1Q04" ... "3Q26", then
"FY2004" ...), row 5 names the supplement each column was read from, and every row below is a line item with a unit.
An Index sheet lists the address of each supplement. Blue cells are figures read from a supplement; black cells are
formulas that rebuild a printed subtotal from the lines above it.

Nothing is published on the workbook's word. For every figure the importer finds the stored supplement that prints
it and the line on the page: the line item's own label with the same number on it, or, for a line the company later
renamed, a line that carries the same numbers in the neighbouring periods. The figure is written with that document,
page, line and printed number, and etl/kpitools.py checks each one again before anything is published. Figures that
cannot be found on a page stay out and are counted in the report.

    python -m etl.ir_workbook report TICKER      read the workbook, anchor every figure, print what was found
    python -m etl.ir_workbook build TICKER FILE  write the extraction file (kpitools schema) to FILE
    python -m etl.ir_workbook publish TICKER     build, validate and publish (replaces the ticker's IR KPI set;
                                                 the previous set is saved under raw/kpi_backups/ first)

Workbooks live in etl/ir_sources/workbooks/{TICKER}.xlsx with {TICKER}.json beside them: page titles per sheet
(which page of a supplement a sheet was read from) and corrections to the Unit column.
"""
import collections
import datetime as dt
import json
import os
import re
import sys

from . import ir_official

HERE = os.path.dirname(os.path.abspath(__file__))
BOOKS = os.path.join(HERE, "ir_sources", "workbooks")
STOP = ("checks", "notes")
MARKERS = (("(in trillions", 1e12), ("(in billions", 1e9), ("(in millions", 1e6), ("(in thousands", 1e3))
_COUNT = re.compile(r"\b(headcount|employees|branches|atms|advisors?|bankers|representatives|customers|households|participants|policies|"
                    r"accounts opened|open accounts|accounts with sales activity|accounts \(in|checking accounts|cards issued|check volume|"
                    r"clearing volume|funds transfer volume|number of)\b|(?<!principal )\btransactions\b(?! volume)", re.I)
_MONEY = re.compile(r"receivable|payable|liabilit|at cost|revenue|income|expense|loans|deposits|assets", re.I)
# A printed figure. Older PDFs put a space inside some figures ("1 ,662", "( 16)"); the page check in kpitools reads those too.
_NUM = re.compile(r"\(?\s?(?:(?<![\d.,])[-–−])?\$?\s?(?:\d{1,3}(?:\s?,\s?\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?|(?<![\d.])\.\d+)\s?\)?")
_FOOT = re.compile(r"\s*\(([a-z]|[a-z]\)\([a-z])\)")


# ---------------------------------------------------------------- reading the workbook
def period_of(label, fye="1231"):
    """("Q" | "A", period end) for a column heading such as "3Q26" or "FY2025"."""
    m = re.fullmatch(r"([1-4])Q(\d\d)", str(label or "").strip())
    if m:
        y = int(m.group(2))
        return "Q", ir_official.quarter_end(fye, y + (2000 if y < 80 else 1900), int(m.group(1)))
    m = re.fullmatch(r"FY((?:19|20)\d\d)", str(label or "").strip())
    if m:
        return "A", ir_official.quarter_end(fye, int(m.group(1)), 4)
    return None


def _terms(formula):
    """[(sign, row)] of a subtotal formula such as "=C7+C8-C10"; None when the formula is anything else."""
    body = formula[1:].replace(" ", "")
    if not re.fullmatch(r"[+-]?[A-Z]{1,3}\d+(?:[+-][A-Z]{1,3}\d+)*", body):
        return None
    return [(-1 if s == "-" else 1, int(r)) for s, r in re.findall(r"([+-]?)[A-Z]{1,3}(\d+)", body)]


def _evaluate(ws, terms, col, depth=0):
    """Value of a subtotal formula from the cells it names (which may be subtotals themselves)."""
    total = 0.0
    for sign, r in terms:
        v = ws.cell(r, col).value
        if isinstance(v, str) and v.startswith("=") and depth < 8:
            inner = _terms(v)
            v = _evaluate(ws, inner, col, depth + 1) if inner else None
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return None
        total += sign * v
    return total


def read(path, fye="1231"):
    """Sheets of a trend workbook: periods with their default source, and rows with label, unit, depth, the header
    rows above them, figures per period and, for subtotals, the lines they are built from."""
    import openpyxl
    formulas = openpyxl.load_workbook(path, data_only=False)
    cached = openpyxl.load_workbook(path, data_only=True)
    sources = {}
    index = cached.worksheets[0]
    for row in index.iter_rows(values_only=True):
        if isinstance(row[0], str) and isinstance(row[1], str) and row[1].startswith("http") and period_of(row[0], fye):
            sources[row[0].strip()] = row[1].strip()
    sheets = []
    for ws in formulas.worksheets[1:]:
        vs = cached[ws.title]
        head = next((r for r in range(1, 15) if str(ws.cell(r, 1).value or "").strip().lower() == "line item"), None)
        if head is None:
            continue
        periods = []
        for c in range(3, ws.max_column + 1):
            p = period_of(ws.cell(head, c).value, fye)
            if p:
                periods.append({"col": c, "label": str(ws.cell(head, c).value).strip(), "kind": p[0], "end": p[1],
                                "source": str(ws.cell(head + 1, c).value or "").strip() or None})
        rows, heads = [], []
        for r in range(head + 2, ws.max_row + 1):
            cell = ws.cell(r, 1)
            label = str(cell.value or "").strip()
            if label.lower().startswith(STOP):
                break
            if not label:
                continue
            depth = int(cell.alignment.indent or 0)
            figures, terms = {}, None
            for p in periods:
                raw, val = ws.cell(r, p["col"]).value, vs.cell(r, p["col"]).value
                formula = _terms(raw) if isinstance(raw, str) and raw.startswith("=") else None
                if formula and not isinstance(val, (int, float)):
                    val = _evaluate(ws, formula, p["col"])  # saved without cached results: rebuild the subtotal
                if isinstance(val, bool) or not isinstance(val, (int, float)):
                    continue
                figures[p["label"]] = val
                terms = formula or terms  # the newest period's formula wins
            if not figures:
                heads = [h for h in heads if h[0] < depth] + [(depth, label)]
                continue
            rows.append({"row": r, "label": label, "unit": (str(ws.cell(r, 2).value).strip() if ws.cell(r, 2).value else None), "depth": depth,
                         "bold": bool(cell.font and cell.font.b), "heads": [h[1] for h in heads if h[0] <= depth], "figures": figures, "terms": terms})
        sheets.append({"name": ws.title, "periods": periods, "rows": rows})
    return {"sources": sources, "sheets": sheets}


# ---------------------------------------------------------------- units
def unit_of(row, sheet, fixes=None):
    """(unit, scale, figure multiplier, note) for a row. The workbook's Unit column is the start; a "(in billions)"
    style marker on the line or a header above it sets the scale; labels decide money against counts; a fix from the
    workbook's own correction file overrides everything. note says why the Unit column was not taken as written."""
    for fix in fixes or []:
        if fix["sheet"] == sheet and fix["label"] == row["label"] and fix.get("row", row["row"]) == row["row"]:
            return fix["unit"], float(fix["scale"]), 1.0, fix.get("why") or "corrected"
    stated = (row["unit"] or "").lower()
    label = row["label"]
    context = " ".join([label] + row["heads"][::-1]).lower()
    marker = next((scale for text, scale in MARKERS if text in label.lower()), None) or next(
        (scale for head in row["heads"][::-1] for text, scale in MARKERS if text in head.lower()), None)
    if stated == "%":
        return "%", 1.0, 100.0, None
    per_share = "per share" in context and not re.search(r"\b(net income|income from|applicable to)\b[^:]*$", label, re.I)
    if stated == "$ per share":
        if re.search(r"per share\s+net income", label, re.I):
            return "USD", 1e6, 1.0, "a net income line under a per-share heading"
        return "USD_per_share", 1.0, 1.0, None
    if re.search(r"\bshares\b", label, re.I) and not re.search(r"at cost|per share", label, re.I):
        return "shares", marker or 1e6, 1.0, None if stated == "mm shares" else "share count"
    count = bool(_COUNT.search(label)) and not _MONEY.search(label)
    if stated in ("count", "thousands", "millions") or count:
        if _MONEY.search(label) and not _COUNT.search(label):
            return "USD", marker or 1e6, 1.0, f'Unit column says "{row["unit"]}" for a money line'
        base = {"thousands": 1e3, "millions": 1e6}.get(stated, 1.0)
        return "count", marker or base, 1.0, None if stated in ("count", "thousands", "millions") else f'Unit column says "{row["unit"]}" for a count'
    if stated == "mm shares":
        return "USD", 1e6, 1.0, 'Unit column says "mm shares" for a money line'
    values = list(row["figures"].values())
    decimals = any(abs(v - round(v)) > 1e-9 for v in values)
    if stated in ("usd mm", "") and decimals:
        # earnings per share and share prices are printed with cents and never reach four digits
        if (per_share or re.search(r"\b(basic|diluted) earnings\b|\beps\b|per common share", context)) and all(abs(v) < 500 for v in values):
            return "USD_per_share", 1.0, 1.0, "per-share figures under a per-share heading"
        if re.search(r"share price|average (purchase )?price", context) and all(abs(v) < 5000 for v in values):
            return "USD_per_share", 1.0, 1.0, "share prices"
    if stated == "usd bn":
        return "USD", marker or 1e9, 1.0, None
    return "USD", marker or 1e6, 1.0, (None if stated == "usd mm" else "no unit stated: taken as millions of dollars") if not marker or marker == 1e6 else "scale stated on the page"


def unclear(row, unit, scale):
    """Why a row's unit cannot be trusted enough to publish, or None."""
    values = list(row["figures"].values())
    decimals = any(abs(v - round(v, 6)) > 1e-9 or abs(v - round(v)) > 1e-9 for v in values)
    if unit == "USD" and scale == 1e6 and decimals:
        return "fractional figures in a line taken as millions of dollars"
    if unit == "count" and scale == 1 and decimals:
        return "fractional figures in a count"
    if unit in ("USD", "count", "shares"):
        # a line whose size changes a few hundred times over from one period to the next was printed in other units
        seq = [v for v in values if v]
        if any(a * b > 0 and not 1 / 300 < abs(a / b) < 300 and max(abs(a), abs(b)) >= 1000 for a, b in zip(seq, seq[1:])):
            return "figures change scale between periods"
    return None


# ---------------------------------------------------------------- hierarchy
def slug(text, n=60):
    return re.sub(r"[^a-z0-9]+", "_", _FOOT.sub("", text).lower()).strip("_")[:n].strip("_")


def clean(label):
    return re.sub(r"\s+", " ", _FOOT.sub("", label)).strip()


def instant(row, sheet, periods, instants=()):
    """True for a balance at a date rather than a flow over a period: a sheet of period-end balances, a label that
    says so, or full-year columns that repeat the fourth quarter."""
    if sheet in instants or re.search(r"period[- ]end|at period end", " ".join([row["label"]] + row["heads"]), re.I):
        return True
    years = [p for p in periods if p["kind"] == "A" and p["label"] in row["figures"]]
    same = 0
    for p in years:
        q4 = f"4Q{p['label'][-2:]}"
        if q4 not in row["figures"]:
            continue
        if abs(row["figures"][q4] - row["figures"][p["label"]]) > 1e-9:
            return False
        same += 1
    return same >= 2


def outline(book, config=None):
    """KPI definitions for every row, without values: id, name, category, unit, scale, kind and parent."""
    config = config or {}
    prefixes = config.get("prefixes") or {}
    out, used = [], set()
    for sheet in book["sheets"]:
        code = prefixes.get(sheet["name"]) or slug(sheet["name"], 12)
        by_row, block = {}, []
        for row in sheet["rows"]:
            unit, scale, mult, note = unit_of(row, sheet["name"], config.get("unit_fixes"))
            base = f"{code}_{slug(' '.join(row['heads'][-1:] + [row['label']]), 66)}"[:76].strip("_")
            kid, n = base, 1
            while kid in used:
                n += 1
                kid = f"{base}_{n}"
            used.add(kid)
            k = {"id": kid, "name": clean(row["label"])[:120], "category": sheet["name"], "subcategory": clean(row["heads"][0])[:60] if row["heads"] else None,
                 "unit": unit, "scale": scale, "mult": mult, "unit_note": note, "hold": unclear(row, unit, scale), "parent": None, "sums_to_parent": False,
                 "subtracts": False, "is_total": bool(row["terms"]) or row["bold"], "description": " > ".join(clean(h) for h in row["heads"])[:400] or None,
                 "instant": instant(row, sheet["name"], sheet["periods"], config.get("instant_sheets") or ()), "sheet": sheet["name"], "row": row}
            by_row[row["row"]] = k
            block.append(k)
        # a subtotal's formula names the lines that add up to it
        for k in block:
            for sign, r in k["row"]["terms"] or []:
                child = by_row.get(r)
                if child is not None and child is not k and child["parent"] is None:
                    child.update(parent=k["id"], sums_to_parent=True, subtracts=sign < 0)
        # other lines hang under the nearest line above that is indented less, inside the same headed block
        stack = []
        for k in block:
            depth, heads = k["row"]["depth"], tuple(k["row"]["heads"])
            stack = [s for s in stack if s[0] < depth and s[1] == heads[:len(s[1])]]
            if k["parent"] is None and stack:
                k["parent"] = stack[-1][2]
            stack.append((depth, heads, k["id"]))
        _break_cycles(block)
        out += block
    return out


def _break_cycles(block):
    """A line can end up under its own descendant: a subtotal indented under a line that an older formula counts as
    one of its parts. The hierarchy must be a tree, so the loop is cut at a link that makes no adding-up claim (or,
    failing that, at the lowest line of the loop)."""
    by_id = {k["id"]: k for k in block}
    for start in block:
        while True:
            seen, node = [], start
            while node is not None and node["id"] not in {k["id"] for k in seen}:
                seen.append(node)
                node = by_id.get(node["parent"])
            if node is None:
                break
            loop = seen[[k["id"] for k in seen].index(node["id"]):]
            cut = next((k for k in loop if not k["sums_to_parent"]), None) or max(loop, key=lambda k: k["row"]["row"])
            cut.update(parent=None, sums_to_parent=False, subtracts=False)


# ---------------------------------------------------------------- finding a figure on a page
def norm(text):
    return re.sub(r"[^a-z0-9]+", " ", _FOOT.sub("", str(text or "")).lower()).strip()


def tokens(line):
    """[(number, text as printed)] for the figures on a line; parentheses and minus signs read as negatives. A figure
    whose first digits were split off by the PDF ("5 31,379") is offered both ways, as 31,379 and as 531,379."""
    out, spans = [], []
    for m in _NUM.finditer(line):
        s = m.group(0).strip()
        if s.startswith("(") != s.endswith(")"):
            s = s.strip("() ")
        digits = re.sub(r"[^\d.]", "", s)
        if not digits or digits == ".":
            continue
        neg = s.startswith("(") or any(c in s for c in "-–−")
        try:
            number = -float(digits) if neg else float(digits)
        except ValueError:
            continue
        text = s.replace("$", "").strip()
        out.append((number, text))
        spans.append((m.start() + m.group(0).index(s[0]), m.end(), s))
    joined = []
    for (a0, a1, a), (b0, b1, b) in zip(spans, spans[1:]):
        lead = re.match(r"\d{1,3}", b)
        if re.fullmatch(r"\d{1,2}", a) and "," in b and lead and not b.startswith("(") and len(a) + len(lead.group(0)) <= 3 and line[a1 - 1:b0].strip() == "" and b0 - a0 <= len(a) + 2:
            try:
                joined.append((float(a + re.sub(r"[^\d.]", "", b)), line[a0:b0] + b))
            except ValueError:
                continue
    return out + joined


def key(value):
    return round(float(value), 4)


class Supplement:
    """Page lines of one stored supplement, looked up by the figures printed on them."""

    def __init__(self, doc_id, pages):
        self.doc_id = doc_id
        self.lines = []          # (page, text, label, [(number, printed)], page title)
        self.by_value = collections.defaultdict(list)
        for pno, page in enumerate(pages, 1):
            text_lines = [ln.strip() for ln in str(page or "").replace("\x00", " ").split("\n") if ln.strip()]
            title = norm(" ".join(text_lines[:3]))
            for ln in text_lines:
                toks = tokens(ln)
                if not toks:
                    continue
                first = _NUM.search(ln)
                label = norm(ln[:first.start()].rstrip("$ "))
                i = len(self.lines)
                self.lines.append((pno, ln, label, toks, title))
                for number, _printed in toks:
                    self.by_value[key(number)].append(i)

    def find(self, label, figure, title_rx=None, neighbours=()):
        """Best line printing `figure` for a row called `label`: (score, page, line, printed) or None. A line counts
        when it carries the row's label, or when it also prints the row's figures for two neighbouring periods."""
        want = norm(label)
        best = None
        for i in self.by_value.get(key(figure), ()):
            pno, ln, lab, toks, title = self.lines[i]
            if lab and lab == want:
                score = 6
            elif lab and len(lab) >= 3 and (want.endswith(" " + lab) or want.startswith(lab + " ")):
                score = 4
            else:
                values = {key(n) for n, _ in toks}
                score = 2 if sum(1 for n in neighbours if key(n) in values) >= 2 else 0
            if not score:
                continue
            if title_rx and title_rx.search(title):
                score += 1
            if best is None or score > best[0]:
                printed = next(p for n, p in toks if key(n) == key(figure))
                best = (score, pno, ln, printed)
        return best


def candidates(period, sources):
    """Supplements that print a period, newest first: a quarter's own and the four after it; for a full year, the
    fourth-quarter supplements of that year and the next."""
    kind, end = period["kind"], period["end"]
    y, m = int(end[:4]), int(end[5:7])
    out = []
    if kind == "A":
        out = [f"4Q{str(y + 1)[2:]}", f"4Q{str(y)[2:]}"]
    else:
        q = (m - 1) // 3 + 1
        for step in range(4, -1, -1):
            qq, yy = q + step, y
            while qq > 4:
                qq, yy = qq - 4, yy + 1
            out.append(f"{qq}Q{str(yy)[2:]}")
    first = [period["source"]] if period.get("source") else []
    return [s for s in dict.fromkeys(first + out) if s in sources]


def anchor(book, texts, config=None, log=None):
    """Extraction data (kpitools schema) for a workbook, with every figure tied to a page line of a stored supplement.
    texts maps a supplement label ("3Q26") to (document id, [page text]). Returns (data, report)."""
    config = config or {}
    supplements = {label: Supplement(doc_id, pages) for label, (doc_id, pages) in texts.items()}
    titles = {name: re.compile(rx, re.I) for name, rx in (config.get("page_titles") or {}).items()}
    kpis = outline(book, config)
    periods = {s["name"]: {p["label"]: p for p in s["periods"]} for s in book["sheets"]}
    order = {s["name"]: [p["label"] for p in s["periods"] if p["kind"] == "Q"] for s in book["sheets"]}
    report = {"rows": len(kpis), "figures": 0, "anchored": 0, "by_label": 0, "by_neighbours": 0, "not_found": 0, "no_supplement": 0,
              "zero_skipped": 0, "not_found_examples": [], "held_rows": [], "unit_notes": [], "missing_supplements": sorted(set(book["sources"]) - set(texts)),
              "not_found_by_sheet": collections.Counter(), "figures_by_sheet": collections.Counter(), "anchored_by_sheet": collections.Counter()}
    out = []
    for k in kpis:
        row, sheet = k["row"], k["sheet"]
        if k["unit_note"]:
            report["unit_notes"].append({"sheet": sheet, "row": row["row"], "label": row["label"], "stated": row["unit"], "unit": k["unit"], "scale": k["scale"], "why": k["unit_note"]})
        if k["hold"]:
            report["held_rows"].append({"sheet": sheet, "row": row["row"], "label": row["label"], "stated": row["unit"], "why": k["hold"], "figures": len(row["figures"])})
        values = []
        quarters = order[sheet]
        for label, figure in row["figures"].items():
            p = periods[sheet][label]
            if p["kind"] == "A" and k["instant"]:
                continue  # a year-end balance is the fourth-quarter balance
            report["figures"] += 1
            report["figures_by_sheet"][sheet] += 1
            printed_value = figure * k["mult"]
            if printed_value == 0:
                report["zero_skipped"] += 1  # printed as a dash; dashes are not recorded
                continue
            if k["hold"]:
                continue
            near = []
            if p["kind"] == "Q" and label in quarters:
                i = quarters.index(label)
                near = [row["figures"][q] * k["mult"] for q in quarters[max(0, i - 4):i + 5] if q != label and q in row["figures"]]
            hit, where = None, None
            cands = candidates(p, texts)
            for s in cands:
                found = supplements[s].find(row["label"], printed_value, titles.get(sheet), near)
                if found and (hit is None or found[0] > hit[0]):
                    hit, where = found, s
                if hit and hit[0] >= 6:
                    break
            if not cands:
                report["no_supplement"] += 1
                continue
            if not hit:
                report["not_found"] += 1
                report["not_found_by_sheet"][sheet] += 1
                if len(report["not_found_examples"]) < 400:
                    report["not_found_examples"].append({"sheet": sheet, "row": row["row"], "label": row["label"], "period": label, "figure": printed_value, "looked_in": cands})
                continue
            score, page, line, printed = hit
            report["anchored"] += 1
            report["anchored_by_sheet"][sheet] += 1
            report["by_label" if score >= 4 else "by_neighbours"] += 1
            number = next(n for n, t in tokens(line) if t == printed)
            values.append({"kind": "I" if k["instant"] else p["kind"], "period_end": p["end"], "value": number * k["scale"], "doc": supplements[where].doc_id,
                           "page": page, "sheet": None, "cell": None, "quote": line[:400], "printed": printed, "scale": int(k["scale"]) if float(k["scale"]).is_integer() else k["scale"]})
        if values:
            out.append({key_: k[key_] for key_ in ("id", "name", "category", "subcategory", "unit", "parent", "sums_to_parent", "subtracts", "is_total", "description")} | {"values": values})
    kept = {k["id"] for k in out}
    for k in out:
        if k["parent"] not in kept:
            k["parent"], k["sums_to_parent"], k["subtracts"] = None, False, False
    for name in ("not_found_by_sheet", "figures_by_sheet", "anchored_by_sheet"):
        report[name] = dict(report[name])
    notes = config.get("notes") or ("Figures are from the company's earnings release financial supplements, as collected in the trend workbook. "
                                    "Each period is taken from the newest supplement that prints it, so restated figures replace the originals. "
                                    "Every figure was found on a page of the stored supplement before publication.")
    return {"ticker": config.get("ticker"), "kpis": out, "derived": [], "notes": notes}, report


# ---------------------------------------------------------------- stored supplements, publishing
def load_config(ticker):
    path = os.path.join(BOOKS, f"{ticker}.json")
    config = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            config = json.load(f)
    return {**config, "ticker": ticker}


def stored_texts(sb, ticker, sources, log=print):
    """Page text of each supplement the workbook cites, from the document store. A supplement that is not stored
    under its listed address is reported and its figures stay unpublished."""
    from . import kpitools
    docs = {ir_official.url_key(d["url"]): d for d in kpitools.docs_for(sb, ticker)}
    out = {}
    for label, url in sources.items():
        d = docs.get(ir_official.url_key(url))
        t = kpitools.text_of(sb, d) if d else None
        if not t or t.get("kind") != "pages":
            log(f"  {ticker} supplement {label} is not stored with page text: {url}")
            continue
        out[label] = (d["id"], t["pages"])
    return out


def backup(sb, ticker):
    """Saves the ticker's published KPI bundle and rows before they are replaced. Returns the object path."""
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    body = {"ticker": ticker, "saved": stamp, "bundle": sb.get_json(f"kpis/{ticker}.json"),
            "definitions": sb.select("kpi_definitions", f"select=*&ticker=eq.{ticker}&source_kind=eq.ir"),
            "values": sb.select("kpi_values", f"select=*&ticker=eq.{ticker}&source_kind=eq.ir")}
    path = f"kpi_backups/{ticker}-{stamp}.json"
    sb.put_object("raw", path, json.dumps(body, separators=(",", ":"), default=str), "application/json", "no-store")
    return path


def build(sb, ticker, log=print):
    from .fundamentals import norm_fye
    config = load_config(ticker)
    lst = ir_official.load(ticker) or {}
    book = read(os.path.join(BOOKS, f"{ticker}.xlsx"), norm_fye(lst.get("fye")))
    return anchor(book, stored_texts(sb, ticker, book["sources"], log), config)


def publish(sb, ticker, minimum=0.9, log=print):
    """Builds, validates and publishes. Refuses when fewer than `minimum` of the workbook's non-zero figures were
    found on a stored page: a low share means supplements are missing from the store, not that the figures are wrong."""
    from . import kpitools
    data, report = build(sb, ticker, log)
    wanted = report["figures"] - report["zero_skipped"]
    share = report["anchored"] / wanted if wanted else 0
    log(f"{ticker}: {report['anchored']} of {wanted} figures found on a stored page ({share:.1%}); {len(report['missing_supplements'])} supplements not stored")
    if share < minimum:
        raise SystemExit(f"{ticker}: only {share:.1%} of the figures were found; store the missing supplements first (python -m etl.ir_official report {ticker})")
    # A subtotal's parts are taken from its newest formula; where an older period was built from other lines the
    # claim that the parts add up is withdrawn for that subtotal, as for model extractions.
    from .kpi_extract import verify
    data, checked = verify(sb, data)
    log(f"{ticker}: {checked['values']} values checked against their pages, {len(checked['bad_values'])} rejected, {len(checked['ties'])} subtotals tie")
    saved = backup(sb, ticker)
    log(f"{ticker}: previous KPI set saved to raw/{saved}")
    from .kpi_extract import _publish
    result = _publish(sb, data)  # waits for the aggregate lease rather than failing when another publisher holds it
    try:
        kpitools.build_index(sb, coverage_updates={}, kpi_updates=[ticker])
    except Exception as e:  # noqa: BLE001 - the counts are refreshed by the next KPI run
        log(f"{ticker}: coverage index KPI counts not refreshed: {str(e)[:200]}")
    log(f"{ticker}: published {result['values'] - len(result['bad_values'])} values, {len(result['bad_values'])} rejected by the page check, "
        f"{len(result['ties'])} subtotals tie, {len(result['tie_failures'])} do not")
    return result, report


def summary(report):
    lines = [f"rows {report['rows']}, figures {report['figures']} ({report['zero_skipped']} zeros not recorded)",
             f"found on a page: {report['anchored']} ({report['by_label']} by the line's label, {report['by_neighbours']} by neighbouring periods)",
             f"not found: {report['not_found']}; supplement not stored: {report['no_supplement']}; rows held for an unclear unit: {len(report['held_rows'])}",
             f"unit column corrected or completed on {len(report['unit_notes'])} rows"]
    for sheet, n in sorted(report["figures_by_sheet"].items(), key=lambda kv: -kv[1]):
        lines.append(f"  {sheet}: {report['anchored_by_sheet'].get(sheet, 0)} of {n}")
    return "\n".join(lines)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) < 2 or argv[0] not in ("report", "build", "publish"):
        raise SystemExit(__doc__)
    from .common import Supabase
    sb, ticker = Supabase(), argv[1].upper()
    if argv[0] == "publish":
        _result, report = publish(sb, ticker)
    else:
        data, report = build(sb, ticker)
        if argv[0] == "build":
            with open(argv[2], "w", encoding="utf-8") as f:
                json.dump(data, f, separators=(",", ":"))
    print(summary(report))


if __name__ == "__main__":
    main()
