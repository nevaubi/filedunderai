"""Excel workbook for the KPI matrix page (api/export.py).

The workbook is built from the same published files the page reads: index.json (prices, market cap, TTM financials
computed from SEC filings), kpistd/taxonomy.json (KPI definitions) and kpistd/series/{kpi}.json (every company's
reported KPI series with the source document, page and quote for each value).

Conventions: blue font = value taken from source data, black font = formula. Dollar amounts in millions unless a
column says otherwise. Negative numbers in parentheses. Every KPI value links to its source document.
"""
import datetime as dt
import io
import re

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

TICKER_RX = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,9}$")
KPI_RX = re.compile(r"^[a-z0-9_]{1,60}$")
MAX_TICKERS = 400
MAX_KPIS = 12
STALE_DAYS = 548  # a latest value more than ~18 months old is flagged as stale
HISTORY_Q = 12
HISTORY_A = 5

FONT = "Arial"
NAVY = "1F3864"
F_TITLE = Font(name=FONT, size=14, bold=True, color=NAVY)
F_SUB = Font(name=FONT, size=9, italic=True, color="595959")
F_HEAD = Font(name=FONT, size=9, bold=True, color="FFFFFF")
F_GROUP = Font(name=FONT, size=9, bold=True, color=NAVY)
F_INPUT = Font(name=FONT, size=9, color="0000FF")
F_INPUT_STALE = Font(name=FONT, size=9, color="7F7F9F", italic=True)
F_CALC = Font(name=FONT, size=9, color="000000")
F_TEXT = Font(name=FONT, size=9, color="000000")
F_MUTED = Font(name=FONT, size=9, color="7F7F7F", italic=True)
F_STAT = Font(name=FONT, size=9, bold=True, color="000000")
F_LINK = Font(name=FONT, size=9, color="0563C1", underline="single")
FILL_HEAD = PatternFill("solid", fgColor=NAVY)
FILL_GROUP = PatternFill("solid", fgColor="D9E1F2")
FILL_STAT = PatternFill("solid", fgColor="F2F2F2")
THIN = Side(style="thin", color="BFBFBF")
B_BOTTOM = Border(bottom=THIN)
B_TOP = Border(top=Side(style="thin", color="000000"))
CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)
RIGHT = Alignment(horizontal="right")
WRAP = Alignment(wrap_text=True, vertical="top")

FMT_M = '#,##0;(#,##0);"–"'
FMT_M1 = '#,##0.0;(#,##0.0);"–"'
FMT_PCT = '0.0%;(0.0%);"–"'
FMT_PCT2 = '0.00%;(0.00%);"–"'
FMT_CHG = '+0.0%;(0.0%);0.0%'
FMT_PTS = '+0.00%;(0.00%);0.00%'
FMT_X = '0.0"x";(0.0"x");"–"'
FMT_X2 = '0.00"x";(0.00"x");"–"'
FMT_PX = '$#,##0.00;($#,##0.00)'
FMT_INT = '#,##0;(#,##0)'
FMT_CENTS = '0.00"¢";(0.00"¢")'
FMT_DATE = "yyyy-mm-dd"

# Comps sheet columns: (header, kind, index.json field or formula, number format, width)
COMPS = [
    ("Ticker", "text", "ticker", None, 9),
    ("Company", "text", "name", None, 28),
    ("Sector", "text", "sector", None, 20),
    ("Industry", "text", "industry", None, 24),
    ("Price ($)", "input", "price", FMT_PX, 10),
    ("Market cap ($M)", "input_m", "market_cap", FMT_M, 12),
    ("Revenue TTM ($M)", "input_m", "revenue_ttm", FMT_M, 12),
    ("Net income TTM ($M)", "input_m", "net_income_ttm", FMT_M, 12),
    ("FCF TTM ($M)", "input_m", "fcf_ttm", FMT_M, 12),
    ("Revenue growth LQ (YoY)", "input", "rev_growth_lq", FMT_PCT, 11),
    ("Gross margin LQ", "input", "gross_margin_lq", FMT_PCT, 10),
    ("Operating margin LQ", "input", "op_margin_lq", FMT_PCT, 10),
    ("Net margin TTM", "formula", "=IF(AND(ISNUMBER({NI}),ISNUMBER({REV})),IFERROR({NI}/{REV},\"\"),\"\")", FMT_PCT, 10),
    ("P/S TTM", "formula", "=IF(AND(ISNUMBER({MC}),ISNUMBER({REV})),IF({REV}>0,{MC}/{REV},\"NM\"),\"\")", FMT_X, 9),
    ("P/E TTM", "formula", "=IF(AND(ISNUMBER({MC}),ISNUMBER({NI})),IF({NI}>0,{MC}/{NI},\"NM\"),\"\")", FMT_X, 9),
    ("FCF yield", "formula", "=IF(AND(ISNUMBER({FCF}),ISNUMBER({MC})),IFERROR({FCF}/{MC},\"\"),\"\")", FMT_PCT, 9),
    ("Dividend yield", "input", "dividend_yield", FMT_PCT, 9),
    ("Buyback yield", "input", "buyback_yield", FMT_PCT, 9),
    ("1Y return", "input", "ret_1y", FMT_PCT, 9),
    ("Beta (1Y)", "input", "beta_1y", '0.00', 8),
    ("Latest quarter", "text", "latest_quarter", None, 10),
    ("Quarter end", "date", "latest_quarter_end", FMT_DATE, 11),
]
REFS = {"MC": "Market cap ($M)", "REV": "Revenue TTM ($M)", "NI": "Net income TTM ($M)", "FCF": "FCF TTM ($M)"}

PCT_UNITS = {"%", "pp", "pct", "percent"}


def unit_style(unit):
    """(scale applied to the stored value, number format, header suffix) for a KPI unit."""
    u = unit or ""
    if u in PCT_UNITS:
        return 0.01, FMT_PCT2, "%"
    if u == "USD":
        return 1e-6, FMT_M1, "$M"
    if u in ("USD_per_share", "USD_per_unit"):
        return 1, FMT_PX, "$"
    if u == "cents":
        return 1, FMT_CENTS, "¢"
    if u == "x":
        return 1, FMT_X2, "x"
    return 1, FMT_INT, u.replace("_", " ") if u not in ("count",) else "#"


def _date(s):
    try:
        return dt.date.fromisoformat(str(s)[:10])
    except (TypeError, ValueError):
        return None


def year_ago(points, i):
    """The point about one year before points[i] (365 +/- 25 days), or None."""
    e = _date(points[i][0])
    if not e:
        return None
    for j in range(i - 1, -1, -1):
        d = _date(points[j][0])
        if not d:
            continue
        gap = (e - d).days
        if abs(gap - 365) <= 25:
            return points[j]
        if gap > 400:
            break
    return None


def clean_request(body, taxonomy_ids):
    """Validates the POST body. Raises ValueError with a user-facing message."""
    if not isinstance(body, dict):
        raise ValueError("Request body must be a JSON object.")
    tickers = body.get("tickers")
    if not isinstance(tickers, list) or not tickers:
        raise ValueError("Select at least one company.")
    out_t = []
    for t in tickers:
        t = str(t or "").strip().upper()
        if TICKER_RX.match(t) and t not in out_t:
            out_t.append(t)
    if not out_t:
        raise ValueError("No valid tickers in the request.")
    if len(out_t) > MAX_TICKERS:
        raise ValueError(f"Export is limited to {MAX_TICKERS} companies. Narrow the universe first.")
    kpis = body.get("kpis") or []
    if not isinstance(kpis, list):
        raise ValueError("kpis must be a list.")
    out_k = []
    for k in kpis:
        k = str(k or "")
        if KPI_RX.match(k) and k in taxonomy_ids and k not in out_k:
            out_k.append(k)
    out_k = out_k[:MAX_KPIS]
    basis = "A" if body.get("basis") == "A" else "Q"
    label = re.sub(r"[^\w &/,.()+\-]", "", str(body.get("label") or "Custom universe"))[:80] or "Custom universe"
    return {"tickers": out_t, "kpis": out_k, "basis": basis, "label": label, "hide_stale": bool(body.get("hide_stale"))}


def _header(ws, row, col, text, width=None):
    c = ws.cell(row=row, column=col, value=text)
    c.font, c.fill, c.alignment = F_HEAD, FILL_HEAD, CENTER
    if width:
        ws.column_dimensions[get_column_letter(col)].width = width
    return c


def _title(ws, title, subtitle):
    ws["A1"] = title
    ws["A1"].font = F_TITLE
    ws["A2"] = subtitle
    ws["A2"].font = F_SUB
    ws.sheet_view.showGridLines = False


def _stats(ws, first, last, row, cols, fmts):
    """Median / Mean / High / Low rows below a block. cols: column numbers; fmts: {col: number format}."""
    labels = [("Median", "MEDIAN"), ("Mean", "AVERAGE"), ("High", "MAX"), ("Low", "MIN"), ("Count", "COUNT")]
    for i, (lab, fn) in enumerate(labels):
        r = row + i
        c = ws.cell(row=r, column=1, value=lab)
        c.font, c.fill = F_STAT, FILL_STAT
        if i == 0:
            c.border = B_TOP
        for col in range(2, max(cols) + 1 if cols else 2):
            cc = ws.cell(row=r, column=col)
            cc.fill = FILL_STAT
            if i == 0:
                cc.border = B_TOP
        for col in cols:
            L = get_column_letter(col)
            cc = ws.cell(row=r, column=col, value=f"=IFERROR({fn}({L}{first}:{L}{last}),\"\")")
            cc.font = F_STAT
            cc.number_format = "0" if fn == "COUNT" else fmts.get(col, "General")
    return row + len(labels)


def comps_sheet(wb, rows, label, as_of):
    ws = wb.active
    ws.title = "Comps"
    _title(ws, f"Comparable companies: {label}",
           f"As of {as_of}. $ in millions except price. Blue = source data (SEC filings, market prices); black = formula. "
           "LQ = latest reported quarter, TTM = trailing twelve months.")
    hr = 4
    col_of = {}
    for j, (h, kind, src, fmt, w) in enumerate(COMPS, start=1):
        _header(ws, hr, j, h, w)
        col_of[h] = j
    ws.row_dimensions[hr].height = 30
    first = hr + 1
    for i, r in enumerate(rows):
        rr = first + i
        refs = {k: f"{get_column_letter(col_of[v])}{rr}" for k, v in REFS.items()}
        for j, (h, kind, src, fmt, w) in enumerate(COMPS, start=1):
            c = ws.cell(row=rr, column=j)
            if kind == "text":
                c.value, c.font = r.get(src), F_TEXT
                if src == "ticker":
                    c.font = Font(name=FONT, size=9, bold=True)
            elif kind == "date":
                d = _date(r.get(src))
                c.value, c.font, c.number_format = d, F_TEXT, fmt
            elif kind in ("input", "input_m"):
                v = r.get(src)
                if isinstance(v, (int, float)):
                    c.value = v / 1e6 if kind == "input_m" else v
                c.font, c.number_format = F_INPUT, fmt
            else:
                c.value, c.font, c.number_format = src.format(**refs), F_CALC, fmt
            c.border = B_BOTTOM
    last = first + max(len(rows), 1) - 1
    num_cols = [j for j, (h, kind, src, fmt, w) in enumerate(COMPS, start=1) if kind in ("input", "input_m", "formula")]
    _stats(ws, first, last, last + 2, num_cols, {j: COMPS[j - 1][3] for j in num_cols})
    ws.freeze_panes = ws.cell(row=first, column=3)
    ws.auto_filter.ref = f"A{hr}:{get_column_letter(len(COMPS))}{last}"
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.print_title_rows = f"{hr}:{hr}"
    return ws


def latest_point(series, basis):
    pts = (series or {}).get(basis) or []
    return (len(pts) - 1, pts) if pts else (None, pts)


def kpi_sheet(wb, rows, kpis, tax, series_by_kpi, basis, label, as_of, today, hide_stale):
    ws = wb.create_sheet("KPI matrix")
    per = "quarter" if basis == "Q" else "fiscal year"
    _title(ws, f"Operating KPIs: {label}",
           f"Latest reported {per} per company, from company filings and releases. Blue = reported value (click to open the "
           f"source document); black = formula. Grey italic = latest value older than 18 months or a discontinued series. As of {as_of}.")
    gr, hr = 4, 5
    for j, (h, w) in enumerate([("Ticker", 9), ("Company", 26), ("Industry", 22)], start=1):
        _header(ws, gr, j, None)
        _header(ws, hr, j, h, w)
    col = 4
    blocks = []
    for k in kpis:
        meta = tax.get(k, {})
        scale, fmt, suffix = unit_style(meta.get("unit"))
        is_pct = (meta.get("unit") or "") in PCT_UNITS
        ws.merge_cells(start_row=gr, start_column=col, end_row=gr, end_column=col + 3)
        g = ws.cell(row=gr, column=col, value=f"{meta.get('name', k)} ({suffix})")
        g.font, g.fill, g.alignment = F_GROUP, FILL_GROUP, CENTER
        for jj in range(col, col + 4):
            ws.cell(row=gr, column=jj).fill = FILL_GROUP
        for jj, (h, w) in enumerate([("Latest", 11), ("Period", 10), ("Year-ago", 11), ("YoY chg (pts)" if is_pct else "YoY", 9)]):
            _header(ws, hr, col + jj, h, w)
        blocks.append((k, col, scale, fmt, is_pct))
        col += 4
    ws.row_dimensions[gr].height = 30
    ws.row_dimensions[hr].height = 22
    first = hr + 1
    stale_cut = today - dt.timedelta(days=STALE_DAYS)
    for i, r in enumerate(rows):
        rr = first + i
        tk = r["ticker"]
        for j, v in enumerate([tk, r.get("name"), r.get("industry")], start=1):
            c = ws.cell(row=rr, column=j, value=v)
            c.font = Font(name=FONT, size=9, bold=True) if j == 1 else F_TEXT
            c.border = B_BOTTOM
        for k, c0, scale, fmt, is_pct in blocks:
            S = series_by_kpi.get(k) or {}
            s = (S.get("companies") or {}).get(tk)
            docs = S.get("docs") or {}
            idx, pts = latest_point(s, basis)
            cells = [ws.cell(row=rr, column=c0 + d) for d in range(4)]
            for c in cells:
                c.border = B_BOTTOM
            if idx is None:
                continue
            p = pts[idx]
            end = _date(p[0])
            stale = (s.get("current") is False) or (end is not None and end < stale_cut)
            if stale and hide_stale:
                continue
            val = p[9] if len(p) > 9 and p[9] is not None else p[1]
            if not isinstance(val, (int, float)):
                continue
            cur, per_c, prior, chg = cells
            cur.value, cur.number_format = val * scale, fmt
            cur.font = F_INPUT_STALE if stale else F_INPUT
            d = docs.get(p[3]) if len(p) > 3 else None
            if d and len(d) > 3 and d[3]:
                cur.hyperlink = d[3]
            per_c.value = (p[2] or str(p[0])) + (" (stale)" if stale else "")
            per_c.font = F_MUTED if stale else F_TEXT
            per_c.alignment = RIGHT
            ya = year_ago(pts, idx)
            if ya is not None:
                yv = ya[9] if len(ya) > 9 and ya[9] is not None else ya[1]
                if isinstance(yv, (int, float)):
                    prior.value, prior.number_format, prior.font = yv * scale, fmt, F_INPUT_STALE if stale else F_INPUT
                    yd = docs.get(ya[3]) if len(ya) > 3 else None
                    if yd and len(yd) > 3 and yd[3]:
                        prior.hyperlink = yd[3]
            a, b = f"{get_column_letter(c0)}{rr}", f"{get_column_letter(c0 + 2)}{rr}"
            if is_pct:
                chg.value = f"=IF(AND(ISNUMBER({a}),ISNUMBER({b})),{a}-{b},\"\")"
                chg.number_format = FMT_PTS
            else:
                chg.value = f"=IF(AND(ISNUMBER({a}),ISNUMBER({b})),IF({b}>0,{a}/{b}-1,\"NM\"),\"\")"
                chg.number_format = FMT_CHG
            chg.font = F_CALC
    last = first + max(len(rows), 1) - 1
    stat_cols, fmts = [], {}
    for k, c0, scale, fmt, is_pct in blocks:
        stat_cols += [c0, c0 + 3]
        fmts[c0] = fmt
        fmts[c0 + 3] = FMT_PTS if is_pct else FMT_CHG
    if stat_cols:
        _stats(ws, first, last, last + 2, stat_cols, fmts)
    ws.freeze_panes = ws.cell(row=first, column=4)
    ws.auto_filter.ref = f"A{hr}:{get_column_letter(max(col - 1, 3))}{last}"
    ws.page_setup.orientation = "landscape"
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.print_title_rows = f"{gr}:{hr}"
    return ws


def history_sheet(wb, rows, kpis, tax, series_by_kpi):
    ws = wb.create_sheet("KPI history")
    _title(ws, "KPI history with sources",
           f"Last {HISTORY_Q} quarters and {HISTORY_A} fiscal years of each company's KPI series, as reported. "
           "Value is in the KPI's reporting unit. Each row cites the document, page and quoted text it was read from.")
    heads = [("Ticker", 9), ("Company", 24), ("KPI", 26), ("Company series", 30), ("Basis", 11), ("Period", 10),
             ("Period end", 11), ("Value", 14), ("Unit", 10), ("Derivation", 14), ("Source document", 40), ("Page", 6),
             ("Quoted text", 60), ("Source URL", 40)]
    hr = 4
    for j, (h, w) in enumerate(heads, start=1):
        _header(ws, hr, j, h, w)
    rr = hr + 1
    names = {r["ticker"]: r.get("name") for r in rows}
    for r in rows:
        tk = r["ticker"]
        for k in kpis:
            S = series_by_kpi.get(k) or {}
            s = (S.get("companies") or {}).get(tk)
            if not s:
                continue
            docs = S.get("docs") or {}
            unit = s.get("unit") or tax.get(k, {}).get("unit")
            for basis, n, lab in (("Q", HISTORY_Q, "Quarter"), ("A", HISTORY_A, "Fiscal year")):
                pts = (s.get(basis) or [])[-n:]
                for p in reversed(pts):
                    val = p[9] if len(p) > 9 and p[9] is not None else p[1]
                    d = docs.get(p[3]) if len(p) > 3 else None
                    vals = [tk, names.get(tk), tax.get(k, {}).get("name", k), s.get("name"), lab, p[2], _date(p[0]), val, unit,
                            p[8] if len(p) > 8 else None, d[0] if d else None, p[4] if len(p) > 4 else None,
                            (p[5] or "")[:500] if len(p) > 5 else None, d[3] if d and len(d) > 3 else None]
                    for j, v in enumerate(vals, start=1):
                        c = ws.cell(row=rr, column=j, value=v)
                        c.font = F_INPUT if j == 8 else F_TEXT
                        if j == 7:
                            c.number_format = FMT_DATE
                        if j == 8:
                            c.number_format = "#,##0.####"
                        if j == 14 and v:
                            c.hyperlink, c.font = v, F_LINK
                    rr += 1
    if rr == hr + 1:
        ws.cell(row=rr, column=1, value="No KPI history for the selected companies and KPIs.").font = F_MUTED
    ws.freeze_panes = ws.cell(row=hr + 1, column=3)
    ws.auto_filter.ref = f"A{hr}:{get_column_letter(len(heads))}{max(rr - 1, hr + 1)}"
    return ws


def definitions_sheet(wb, kpis, tax, rows, series_by_kpi):
    ws = wb.create_sheet("Definitions")
    _title(ws, "KPI definitions", "How each standardized KPI is defined and how many of the exported companies report it.")
    heads = [("KPI", 28), ("Id", 24), ("Family", 16), ("Unit", 10), ("Definition", 80), ("Peer aggregate", 12),
             ("Higher is better", 10), ("Companies in export", 12)]
    hr = 4
    for j, (h, w) in enumerate(heads, start=1):
        _header(ws, hr, j, h, w)
    tks = {r["ticker"] for r in rows}
    for i, k in enumerate(kpis):
        m = tax.get(k, {})
        n = len(tks & set(((series_by_kpi.get(k) or {}).get("companies") or {}).keys()))
        hib = m.get("higher_is_better")
        vals = [m.get("name", k), k, m.get("family"), m.get("unit"), m.get("definition"), m.get("how"),
                "Yes" if hib is True else "No" if hib is False else "", n]
        for j, v in enumerate(vals, start=1):
            c = ws.cell(row=hr + 1 + i, column=j, value=v)
            c.font, c.alignment, c.border = F_TEXT, WRAP, B_BOTTOM
    ws.freeze_panes = "A5"
    return ws


def notes_sheet(wb, label, as_of, generated, basis, hide_stale, n):
    ws = wb.create_sheet("Notes")
    _title(ws, "Sources and notes", f"Generated {generated} by Filed Under (filedunder.ai).")
    lines = [
        ("Universe", f"{label} ({n} companies)"),
        ("Market data as of", as_of),
        ("KPI basis", "Latest reported quarter" if basis == "Q" else "Latest reported fiscal year"),
        ("Stale values", "Excluded" if hide_stale else "Included, shown in grey italic and marked (stale) in the Period column"),
        ("Financials", "Revenue, net income, free cash flow and margins are computed from XBRL facts in each company's SEC "
                       "10-K and 10-Q filings. TTM sums the latest four reported quarters. Market cap uses the latest close "
                       "times shares outstanding from the latest filing cover page."),
        ("Formulas", "Net margin, P/S, P/E and FCF yield are live formulas on the Comps sheet, so editing an input recalculates "
                     "them. P/E and P/S show NM when the denominator is zero or negative. YoY on the KPI sheet is a formula "
                     "of Latest and Year-ago; for percentage KPIs it is the change in percentage points."),
        ("KPIs", "Operating KPIs are read from earnings releases, supplements, presentations and filings. Each company series "
                 "keeps one reporting basis; series from different bases are never spliced. The KPI history sheet lists "
                 "the source document, page and quoted text for every value."),
        ("Year-ago", "The value for the period ending 340 to 390 days before the latest period in the same series."),
        ("Colour code", "Blue font: value from source data. Black font: formula. Grey italic: stale or discontinued series."),
        ("Disclaimer", "For research only. Not investment advice. Verify figures against the cited filings before relying on them."),
    ]
    for i, (k, v) in enumerate(lines):
        a = ws.cell(row=4 + i, column=1, value=k)
        b = ws.cell(row=4 + i, column=2, value=v)
        a.font, b.font, b.alignment = Font(name=FONT, size=9, bold=True), F_TEXT, WRAP
    ws.column_dimensions["A"].width = 20
    ws.column_dimensions["B"].width = 110
    return ws


def build(req, index, taxonomy, series_by_kpi, industries=None, now=None):
    """Returns (xlsx bytes, filename). index: index.json; taxonomy: kpistd/taxonomy.json;
    series_by_kpi: {kpi_id: kpistd/series/{kpi_id}.json}; industries: {ticker: industry}."""
    now = now or dt.datetime.now(dt.timezone.utc)
    today = now.date()
    by = {c["ticker"]: c for c in index.get("companies") or []}
    tax = {k["id"]: k for k in taxonomy.get("kpis") or []}
    industries = industries or {}
    rows = []
    for t in req["tickers"]:
        c = dict(by.get(t) or {"ticker": t})
        c["industry"] = industries.get(t) or c.get("sic")
        c.setdefault("name", t)
        rows.append(c)
    as_of = max((c.get("as_of") or "" for c in rows), default="") or today.isoformat()
    wb = Workbook()
    comps_sheet(wb, rows, req["label"], as_of)
    if req["kpis"]:
        kpi_sheet(wb, rows, req["kpis"], tax, series_by_kpi, req["basis"], req["label"], as_of, today, req["hide_stale"])
        history_sheet(wb, rows, req["kpis"], tax, series_by_kpi)
        definitions_sheet(wb, req["kpis"], tax, rows, series_by_kpi)
    notes_sheet(wb, req["label"], as_of, now.strftime("%Y-%m-%d %H:%M UTC"), req["basis"], req["hide_stale"], len(rows))
    wb.properties.creator = "Filed Under"
    wb.properties.title = f"Comps: {req['label']}"
    buf = io.BytesIO()
    wb.save(buf)
    slug = re.sub(r"[^a-z0-9]+", "-", req["label"].lower()).strip("-")[:40] or "universe"
    return buf.getvalue(), f"filedunder-comps-{slug}-{today.isoformat()}.xlsx"


def industries_from_latest(latest):
    out = {}
    for rows in (latest.get("kpis") or {}).values():
        for t, r in rows.items():
            if r.get("ind") and t not in out:
                out[t] = r["ind"]
    return out
