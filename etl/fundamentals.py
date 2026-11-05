"""Derived fundamentals for screening: TTM sums, latest-quarter margins and growth."""
import copy
import datetime as dt
import math


# Aggregate caches must identify the normalizer that excluded synthetic EPS and shares.
NORMALIZATION_VERSION = 2

# A provider's price-adjustment event is not proof of a financial share basis.
# These sourced holds require review of consistent recast quarters before release.
VALUATION_BASIS_HOLDS = {
    "HON": {
        "status": "held", "code": "corporate_action_recast_required",
        "affected_fields": ["eps_ttm", "pe_ttm", "earnings_yield"],
        "effective_date": "2026-06-29",
        "reason": "EPS spans Honeywell's spin-off and one-for-two reverse stock split. A consistent recast four-quarter series is required.",
        "sources": [
            {"title": "Honeywell completion of spin-off and one-for-two reverse stock split",
             "url": "https://www.sec.gov/Archives/edgar/data/773840/000077384026000084/exhibit991-pressrelease629.htm"},
            {"title": "Honeywell second-quarter 2026 filing and retrospective share adjustment",
             "url": "https://www.sec.gov/Archives/edgar/data/773840/000077384026000124/hon-20260630.htm"},
        ],
    },
}


def normalized(entry):
    version = (entry or {}).get("pipeline_version")
    return type(version) is int and version >= NORMALIZATION_VERSION


def valuation_hold(ticker=None, declared=None, as_of=None):
    """Explicit, sourced current-valuation hold; historical observations stay intact."""
    basis = declared if isinstance(declared, dict) and declared.get("status") == "held" else VALUATION_BASIS_HOLDS.get(ticker)
    if not basis:
        return None
    if basis.get("effective_date"):
        try:
            if _d(as_of or dt.datetime.now(dt.timezone.utc).date().isoformat()) < _d(basis["effective_date"]):
                return None
        except (TypeError, ValueError):
            pass  # Unknown date cannot clear an otherwise explicit hold.
    return copy.deepcopy(basis)


def basis_blocks(basis, field):
    return bool(basis and basis.get("status") == "held" and
                field in (basis.get("affected_fields") or ("eps_ttm", "pe_ttm", "earnings_yield")))


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
    # Best Buy's year ended on the Saturday nearest the end of February through fiscal 2012 (March 3, 2012), then on the
    # Saturday nearest January 31 from fiscal 2013 (a 48-week year with a 14-week first quarter)
    "BBY": [("0000-00-00", "0228"), ("2012-03-04", "0130")],
    # EDGAR lists Brown & Brown's year end as 0119, but its fiscal year is the calendar year
    "BRO": [("0000-00-00", "1231")],
    # Deckers moved from a December 31 to a March 31 year end, with a transition period January 1 to March 31, 2014
    "DECK": [("0000-00-00", "1231"), ("2014-01-01", "0331")],
}
# companies that name a fiscal year after the calendar year in which it mostly falls, although it ends in January or
# early February of the next year (Home Depot's "fiscal 2024" ended February 2, 2025). Their labels are one year lower.
FY_NAME_PRIOR = {"HD", "LOW", "TGT", "DG", "DLTR", "ROST", "KR", "ULTA", "LULU", "BURL"}
# transition periods between two fiscal years: (start, end, short name). Quarters inside read "Q1 TP26", the period "TP2026".
TRANSITIONS = {
    "FDX": [("2026-06-01", "2026-12-31")],
    "AMCR": [("2026-07-01", "2026-12-31")],
    "ADM": [("2012-07-01", "2012-12-31")],
    "DECK": [("2014-01-01", "2014-03-31")],
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


NON_SUMMABLE = {"Diluted shares", "Basic shares", "Shares outstanding", "Total assets", "Total liabilities", "Shareholders' equity"}


def consecutive_quarters(q):
    if len(q) != 4 or any(not r.get("start") for r in q):
        return False
    q = sorted(q, key=lambda r:r["end"])
    if any(not 80 <= (_d(r["end"])-_d(r["start"])).days <= 120 for r in q):
        return False
    return all((_d(b["start"])-_d(a["end"])).days == 1 for a,b in zip(q,q[1:]))


def current_period_end(statements):
    """Latest reported income quarter is the common current calculation window."""
    income = [p["end"] for label in ("Revenue", "Net income")
              for p in series(statements, label, "quarterly", ("income",))]
    if income:
        return max(income)
    flows = [p["end"] for stmt in ("income", "cashflow") for row in statements.get(stmt, [])
             if row["label"] not in NON_SUMMABLE for p in row.get("quarterly", [])]
    return max(flows) if flows else None


def ttm_records(statements, label, stmt_order=("income", "cashflow"), end=None):
    if label in NON_SUMMABLE:
        return []
    # A balance cannot become a flow by passing a different statement order.
    for row in statements.get("balance", []):
        if row["label"] == label:
            return []
    q = sorted((r for r in series(statements, label, "quarterly", stmt_order)
                if end is None or r["end"] <= end), key=lambda r:r["end"])[-4:]
    if end is not None and (not q or q[-1]["end"] != end):
        return []
    if label in ("EPS diluted", "EPS basic") and any(r.get("derived") for r in q):
        return []
    return q if consecutive_quarters(q) else []


def ttm(statements, label, stmt_order=("income", "cashflow"), end=None):
    q = ttm_records(statements, label, stmt_order, end=end)
    return sum(x["v"] for x in q) if q else None


def current_ttm_value(fund, field, basis=None):
    """A cached current scalar must carry the contiguous reporting periods it covers."""
    value = fund.get(field)
    if type(value) not in (int, float) or not math.isfinite(value) or basis_blocks(basis or fund.get("valuation_basis"), field):
        return None
    try:
        records = [{"start": start, "end": end} for start, end in (fund.get("ttm_periods") or {}).get(field, [])]
        return value if consecutive_quarters(records) and max(r["end"] for r in records) == fund.get("latest_quarter_end") else None
    except (TypeError, ValueError, KeyError):
        return None


def market_cap(price, shares, price_date=None):
    """Reported shares available on the close date, at most 400 days old.

    Match the browser's valuation guard. Missing, future, invalid or synthetic
    counts remain unknown; invalid source metadata must not abort publication.
    """
    if not isinstance(shares, dict) or shares.get("basis") != "reported_outstanding" or shares.get("derived"):
        return None
    count = shares.get("val")
    if any(type(value) not in (int, float) or not math.isfinite(value) or value <= 0 for value in (price, count)):
        return None

    def iso_date(value):
        if not isinstance(value, str) or len(value) != 10:
            raise ValueError("Expected YYYY-MM-DD")
        parsed = _d(value)
        if parsed.isoformat() != value:
            raise ValueError("Expected YYYY-MM-DD")
        return parsed

    try:
        as_of = iso_date(price_date or dt.datetime.now(dt.timezone.utc).date().isoformat())
        end = iso_date(shares.get("end"))
        if not 0 <= (as_of - end).days <= 400:
            return None
        for field in ("filed", "available_at"):
            if shares.get(field) and not end <= iso_date(shares[field]) <= as_of:
                return None
    except (TypeError, ValueError):
        return None
    value = price * count
    if not math.isfinite(value):
        return None
    return value


def latest(statements, label, kind="quarterly"):
    s = series(statements, label, kind)
    return s[-1] if s else None


def at(statements, label, end, kind="quarterly", tol=12):
    for x in series(statements, label, kind):
        if abs((_d(x["end"]) - _d(end)).days) <= tol:
            return x
    return None


def compute(statements, profile, px, ticker=None):
    fye = profile.get("fiscalYearEnd") or "1231"
    lq = current_period_end(statements)
    lq_label = fy_label(fye, lq, "Q") if lq else None
    if lq_label and profile.get("fyNameOffset"):
        lq_label = _shift_fy(lq_label, profile["fyNameOffset"])
    out = {"latest_quarter": lq_label, "latest_quarter_end": lq}
    rev, ni = ttm(statements, "Revenue", end=lq), ttm(statements, "Net income", end=lq)
    eps = ttm(statements, "EPS diluted", end=lq)
    basis = valuation_hold(ticker, profile.get("valuation_basis"), (px or {}).get("as_of"))
    if basis_blocks(basis, "eps_ttm"):
        eps = None
    out["valuation_basis"] = basis
    ocf, capex = ttm(statements, "Operating cash flow", ("cashflow",), end=lq), ttm(statements, "Capex", ("cashflow",), end=lq)
    div, bb = ttm(statements, "Dividends paid", ("cashflow",), end=lq), ttm(statements, "Buybacks", ("cashflow",), end=lq)
    ocf_q, capex_q = ttm_records(statements, "Operating cash flow", ("cashflow",), end=lq), ttm_records(statements, "Capex", ("cashflow",), end=lq)
    aligned = [(r["start"], r["end"]) for r in ocf_q] == [(r["start"], r["end"]) for r in capex_q]
    fcf = ocf - sum(abs(r["v"]) for r in capex_q) if ocf is not None and capex is not None and aligned else None
    out.update({"revenue_ttm": rev, "net_income_ttm": ni, "eps_ttm": eps, "fcf_ttm": fcf})
    periods = lambda records: [[r["start"], r["end"]] for r in records]
    rev_periods = periods(ttm_records(statements, "Revenue", end=lq))
    ni_periods = periods(ttm_records(statements, "Net income", end=lq))
    out["ttm_periods"] = {"revenue_ttm": rev_periods, "net_income_ttm": ni_periods,
                          "eps_ttm": periods(ttm_records(statements, "EPS diluted", end=lq)),
                          "fcf_ttm": periods(ocf_q) if fcf is not None else []}
    out["net_margin_ttm"] = ni / rev if rev and ni is not None and rev_periods == ni_periods else None
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
    shares = profile.get("sharesOutstanding")
    price = px.get("price") if px else None
    mcap = market_cap(price, shares, (px or {}).get("as_of"))
    out["market_cap"] = mcap
    out["pe_ttm"] = price / eps if price and eps and eps > 0 else None
    out["ps_ttm"] = mcap / rev if mcap and rev else None
    out["fcf_yield"] = fcf / mcap if fcf is not None and mcap else None
    out["dividend_yield"] = div / mcap if div and mcap else None
    out["buyback_yield"] = bb / mcap if bb and mcap else None
    return out
