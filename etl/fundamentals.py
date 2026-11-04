"""Derived fundamentals for screening: TTM sums, latest-quarter margins and growth."""
import datetime as dt


def _d(s):
    return dt.date.fromisoformat(s)


def norm_fye(fye):
    """52/53-week years that end in the first days of January (SEC reports e.g. 0103) are named for the calendar
    year they mostly cover, so they are treated as December year ends."""
    fye = fye or "1231"
    if fye[:2] == "01" and int(fye[2:]) <= 10:
        return "1231"
    return fye


# fiscal year-end changes: (first period end on the year end, year end). Periods ending on or after a date use its year end.
FYE_HISTORY = {
    # FedEx moved from a May 31 to a December 31 year end effective June 1, 2026
    "FDX": [("0000-00-00", "0531"), ("2026-06-01", "1231")],
    # Morgan Stanley and Goldman Sachs moved from November to December year ends after fiscal 2008
    "MS": [("0000-00-00", "1130"), ("2008-12-01", "1231")],
    "GS": [("0000-00-00", "1130"), ("2008-12-27", "1231")],
    # Amcor moved from a June 30 to a December 31 year end, with a transition period from July 1 to December 31, 2026
    "AMCR": [("0000-00-00", "0630"), ("2026-07-01", "1231")],
    # Archer-Daniels-Midland moved from a June 30 to a December 31 year end, with a transition period July 1 to December 31, 2012
    "ADM": [("0000-00-00", "0630"), ("2012-07-01", "1231")],
}
# companies that name a fiscal year after the calendar year in which it mostly falls, although it ends in January or
# early February of the next year (Home Depot's "fiscal 2024" ended February 2, 2025). Their labels are one year lower.
FY_NAME_PRIOR = {"HD", "LOW", "TGT", "DG", "DLTR", "ROST", "KR", "ULTA", "LULU", "BURL"}
# transition periods between two fiscal years: (start, end, short name). Quarters inside read "Q1 TP26", the period "TP2026".
TRANSITIONS = {
    "FDX": [("2026-06-01", "2026-12-31")],
    "AMCR": [("2026-07-01", "2026-12-31")],
    "ADM": [("2012-07-01", "2012-12-31")],
}


def fye_at(ticker, fye, end):
    """Year end in effect for a period ending on `end` (ISO date), honoring FYE_HISTORY."""
    out = fye
    for since, new in FYE_HISTORY.get(ticker or "", []):
        if end and end >= since:
            out = new
    return out


def period_label(ticker, fye, end, kind):
    """fy_label with the company's year-end history and transition periods applied."""
    for start, stop in TRANSITIONS.get(ticker or "", []):
        if start <= end <= stop:
            s, e = _d(start), _d(end)
            months = (e.year - s.year) * 12 + e.month - s.month + 1
            yy = stop[:4]
            return f"TP{yy}" if kind == "A" else f"Q{max(1, round(months / 3))} TP{yy[2:]}"
    lab = fy_label(fye_at(ticker, fye, end), end, kind)
    if ticker in FY_NAME_PRIOR:
        lab = _shift_fy(lab, -1)
    return lab


def _shift_fy(label, n):
    """Moves the fiscal year in "FY2025" / "Q3 FY25" by n years."""
    import re
    m = re.match(r"^FY(\d{4})$", label)
    if m:
        return f"FY{int(m.group(1)) + n}"
    m = re.match(r"^(Q\d) FY(\d\d)$", label)
    if m:
        return f"{m.group(1)} FY{(int(m.group(2)) + n) % 100:02d}"
    return label


def fy_label(fye, end, kind):
    fye = norm_fye(fye)
    fm, fd = int(fye[:2]), int(fye[2:])
    e = _d(end)

    def fy_end_of(y):
        try:
            return dt.date(y, fm, fd)
        except ValueError:
            return dt.date(y, fm, 28)

    fy = e.year
    if e > fy_end_of(fy) + dt.timedelta(days=10):
        fy += 1
    elif e <= fy_end_of(fy - 1) + dt.timedelta(days=10):
        fy -= 1
    try:
        fy_end = dt.date(fy, fm, fd)
    except ValueError:
        fy_end = dt.date(fy, fm, 28)
    months_left = round((fy_end - e).days / 30.44)
    q = max(1, min(4, 4 - round(months_left / 3)))
    return f"FY{fy}" if kind == "A" else f"Q{q} FY{str(fy)[2:]}"


def series(statements, label, kind="quarterly", prefer=("income", "balance", "cashflow")):
    for stmt in prefer:
        for row in statements.get(stmt, []):
            if row["label"] == label:
                return row[kind]
    return []


def ttm(statements, label, stmt_order=("income", "cashflow")):
    q = series(statements, label, "quarterly", stmt_order)[-4:]
    if len(q) < 4 or (_d(q[-1]["end"]) - _d(q[0]["end"])).days > 300:
        return None
    return sum(x["v"] for x in q)


def latest(statements, label, kind="quarterly"):
    s = series(statements, label, kind)
    return s[-1] if s else None


def at(statements, label, end, kind="quarterly", tol=12):
    for x in series(statements, label, kind):
        if abs((_d(x["end"]) - _d(end)).days) <= tol:
            return x
    return None


def compute(statements, profile, px):
    fye = profile.get("fiscalYearEnd") or "1231"
    rev_q = series(statements, "Revenue", "quarterly")
    lq = rev_q[-1]["end"] if rev_q else None
    lq_label = fy_label(fye, lq, "Q") if lq else None
    if lq_label and profile.get("fyNameOffset"):
        lq_label = _shift_fy(lq_label, profile["fyNameOffset"])
    out = {"latest_quarter": lq_label, "latest_quarter_end": lq}
    rev, ni = ttm(statements, "Revenue"), ttm(statements, "Net income")
    eps = ttm(statements, "EPS diluted")
    ocf, capex = ttm(statements, "Operating cash flow", ("cashflow",)), ttm(statements, "Capex", ("cashflow",))
    div, bb = ttm(statements, "Dividends paid", ("cashflow",)), ttm(statements, "Buybacks", ("cashflow",))
    fcf = ocf - capex if ocf is not None and capex is not None else (ocf if ocf is not None and capex is None else None)
    out.update({"revenue_ttm": rev, "net_income_ttm": ni, "eps_ttm": eps, "fcf_ttm": fcf})
    out["net_margin_ttm"] = ni / rev if rev and ni is not None else None
    if lq:
        r = at(statements, "Revenue", lq)
        gp = at(statements, "Gross profit", lq)
        cogs = at(statements, "Cost of revenue", lq)
        op = at(statements, "Operating income", lq)
        if r and r["v"]:
            if gp:
                out["gross_margin_lq"] = gp["v"] / r["v"]
            elif cogs:
                out["gross_margin_lq"] = (r["v"] - cogs["v"]) / r["v"]
            if op:
                out["op_margin_lq"] = op["v"] / r["v"]
            prior = (_d(lq) - dt.timedelta(days=365)).isoformat()
            pr = at(statements, "Revenue", prior, tol=20)
            if pr and pr["v"]:
                out["rev_growth_lq"] = r["v"] / pr["v"] - 1
    shares = (profile.get("sharesOutstanding") or {}).get("val")
    price = px.get("price") if px else None
    mcap = price * shares if price and shares else None
    out["market_cap"] = mcap
    out["pe_ttm"] = price / eps if price and eps and eps > 0 else None
    out["ps_ttm"] = mcap / rev if mcap and rev else None
    out["fcf_yield"] = fcf / mcap if fcf is not None and mcap else None
    out["dividend_yield"] = div / mcap if div and mcap else None
    out["buyback_yield"] = bb / mcap if bb and mcap else None
    return out
