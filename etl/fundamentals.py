"""Derived fundamentals for screening: TTM sums, latest-quarter margins and growth."""
import datetime as dt


def _d(s):
    return dt.date.fromisoformat(s)


def fy_label(fye, end, kind):
    fm, fd = int(fye[:2]), int(fye[2:])
    e = _d(end)
    fy = e.year
    try:
        fy_end_this = dt.date(fy, fm, fd)
    except ValueError:
        fy_end_this = dt.date(fy, fm, 28)
    if e > fy_end_this + dt.timedelta(days=10):
        fy += 1
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
    out = {"latest_quarter": fy_label(fye, lq, "Q") if lq else None, "latest_quarter_end": lq}
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
