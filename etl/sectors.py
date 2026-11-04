"""Sector and industry analysis built from the companies' own filings, prices and the macro store.

Outputs (public-data bucket):
  sectors.json                   overview: every sector and industry with headline figures, the SIC mapping, ETF rotation
  sectors/{slug}.json            one group: quarterly aggregates, performance, macro sensitivity, KPIs, constituents,
                                 segment exposure, insider activity, filings
  sectors/{slug}.comp.json       per-company inputs behind every aggregate (loaded when a number is opened)
  sectors/notes.json             weekly written notes (step "notes")
Caches: meta/sectors/pxm.json (monthly closes and splits per stock), meta/sectors/factors.json (FRED series).

Aggregation rules
  quarters      each company quarter is placed in the calendar quarter whose end is nearest its period end
  TTM           sum of the four quarters that end in four consecutive calendar quarters
  growth        same calendar quarter a year earlier; aggregate = sum(current) / sum(prior) - 1 over companies with both
  margins       aggregate = sum(numerator TTM) / sum(revenue TTM) over companies with both
  multiples     aggregate = sum(market cap) / sum(earnings, sales or FCF TTM); medians use positive denominators only
  cap-weighted  mean of company values weighted by market cap at the quarter end
  market cap    today's market cap scaled back by unadjusted month-end close times diluted shares for the quarter
"""
import datetime as dt
import json
import math
import re
import statistics
import time
from concurrent.futures import ThreadPoolExecutor

from .common import HTTPError, Supabase, get_json, now_iso, request
from .sector_map import INDUSTRIES, KPI_SPECS, OVERRIDES, SECTOR_SLUG, SEGMENT_RULES, industry_of, mapping_table, segment_industry, slugify
from .universe import SECTOR_ETF

START_Q = "2008Q1"
COMP_QUARTERS = 44            # quarters of per-company detail published for derivations
FACT_LABELS = [
    ("income", "Revenue"), ("income", "Cost of revenue"), ("income", "Gross profit"), ("income", "Operating income"),
    ("income", "Net income"), ("income", "Pretax income"), ("income", "Income tax"), ("income", "Diluted shares"),
    ("cashflow", "Operating cash flow"), ("cashflow", "Capex"), ("cashflow", "D&A"),
    ("balance", "Long-term debt"), ("balance", "Cash & equivalents"), ("balance", "Shareholders' equity"),
    ("balance", "Inventory"), ("balance", "Deposits"),
]
FLOW = {"Revenue", "Cost of revenue", "Gross profit", "Operating income", "Net income", "Pretax income", "Income tax", "Operating cash flow", "Capex", "D&A"}
# per-company fields published for derivations: (key, fact label or None for computed, description)
COMP_FIELDS = ["end", "rev_q", "rev_py", "ni_q", "ni_py", "rev_ttm", "gp_ttm", "oi_ttm", "ni_ttm", "ocf_ttm", "capex_ttm", "da_ttm",
               "tax_ttm", "pretax_ttm", "ltd", "cash", "eq", "inv", "cor_ttm", "sh", "px", "mcap", "end_py"]
METRICS = {
    "rev_growth": {"name": "Revenue growth YoY", "unit": "pct"},
    "ni_growth": {"name": "Earnings growth YoY", "unit": "pct"},
    "pct_rev_up": {"name": "Companies growing revenue", "unit": "pct"},
    "pct_ni_up": {"name": "Companies growing earnings", "unit": "pct"},
    "gm": {"name": "Gross margin (TTM)", "unit": "pct"},
    "om": {"name": "Operating margin (TTM)", "unit": "pct"},
    "nm": {"name": "Net margin (TTM)", "unit": "pct"},
    "fcfm": {"name": "FCF margin (TTM)", "unit": "pct"},
    "roic": {"name": "ROIC (TTM)", "unit": "pct"},
    "roe": {"name": "Return on equity (TTM)", "unit": "pct"},
    "nd_ebitda": {"name": "Net debt / EBITDA", "unit": "x"},
    "de": {"name": "Debt / equity", "unit": "x"},
    "pe": {"name": "P/E (TTM)", "unit": "x"},
    "ps": {"name": "P/S (TTM)", "unit": "x"},
    "pfcf": {"name": "P/FCF (TTM)", "unit": "x"},
    "rev_ttm": {"name": "Revenue (TTM)", "unit": "USD"},
    "ni_ttm": {"name": "Net income (TTM)", "unit": "USD"},
    "mcap": {"name": "Market cap", "unit": "USD"},
}
FRED_FACTORS = {
    "BAA10Y": {"name": "Credit spread (Baa - 10Y)", "kind": "level", "unit": "pp", "source": "Moody's via FRED"},
    "DTWEXBGS": {"name": "US dollar (broad index)", "kind": "pct", "unit": "index", "source": "Federal Reserve H.10 via FRED"},
    "DCOILWTICO": {"name": "WTI crude oil", "kind": "pct", "unit": "$/bbl", "source": "EIA via FRED"},
    "DGS10": {"name": "10Y Treasury yield", "kind": "level", "unit": "%", "source": "Federal Reserve H.15 via FRED"},
}
MACRO_NAMES = {
    "TSY:10Y": ("10Y Treasury yield", "level", "U.S. Treasury daily par yield curve"),
    "TSY:2Y": ("2Y Treasury yield", "level", "U.S. Treasury daily par yield curve"),
    "FRED:T10Y2Y": ("2s10s curve slope", "level", "FRED T10Y2Y"),
    "FRED:DFF": ("Fed funds rate", "level", "FRED DFF"),
    "FRED:CPIAUCSL": ("CPI inflation (YoY)", "yoy", "BLS via FRED CPIAUCSL"),
    "FRED:CPILFESL": ("Core CPI inflation (YoY)", "yoy", "BLS via FRED CPILFESL"),
    "FRED:UNRATE": ("Unemployment rate", "level", "BLS via FRED UNRATE"),
    "FRED:PAYEMS": ("Payrolls", "diff", "BLS via FRED PAYEMS"),
    "FED:EFFR": ("Effective fed funds rate", "level", "Federal Reserve H.15"),
}


# ---------------------------------------------------------------- small helpers
def _r(v, n=6):
    if v is None or isinstance(v, bool):
        return v
    if isinstance(v, float):
        if v != v or v in (math.inf, -math.inf):
            return None
        if v == 0:
            return 0.0
        return float(f"{v:.{n}g}")
    return v


def qkey(end):
    """Calendar quarter whose end is nearest a period end date (ISO)."""
    d = dt.date.fromisoformat(end)
    best, bd = None, None
    for y in (d.year - 1, d.year, d.year + 1):
        for q, (m, dd) in enumerate([(3, 31), (6, 30), (9, 30), (12, 31)], 1):
            gap = abs((dt.date(y, m, dd) - d).days)
            if bd is None or gap < bd:
                best, bd = f"{y}Q{q}", gap
    return best


def qshift(k, n):
    y, q = int(k[:4]), int(k[5])
    i = y * 4 + q - 1 + n
    return f"{i // 4}Q{i % 4 + 1}"


def qrange(a, b):
    out, k = [], a
    while k <= b:
        out.append(k)
        k = qshift(k, 1)
    return out


def qend_month(k):
    return f"{k[:4]}-{int(k[5]) * 3:02d}"


def median(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def _mean(a):
    return sum(a) / len(a)


def corr_beta(y, x):
    n = len(x)
    if n < 20:
        return None, None
    mx, my = _mean(x), _mean(y)
    sxy = sum((x[i] - mx) * (y[i] - my) for i in range(n))
    sxx = sum((v - mx) ** 2 for v in x)
    syy = sum((v - my) ** 2 for v in y)
    if sxx <= 0 or syy <= 0:
        return None, None
    return sxy / math.sqrt(sxx * syy), sxy / sxx


def fetch_all(sb, table, query, workers=6):
    """All rows of a PostgREST query, pages fetched in parallel (needs a deterministic order in `query`)."""
    import urllib.request
    url = f"{sb.url}/rest/v1/{table}?{query}"
    req = urllib.request.Request(url, headers=sb._h({"Range": "0-0", "Prefer": "count=exact"}))
    total = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                cr = resp.headers.get("Content-Range") or "*/0"
                total = int(cr.split("/")[-1]) if cr.split("/")[-1] != "*" else 0
            break
        except Exception:
            if attempt == 3:
                raise
            time.sleep(3 * (attempt + 1))
    if not total:
        return []

    def page(off):
        return json.loads(request(url, headers=sb._h({"Range-Unit": "items", "Range": f"{off}-{off + 999}"}), timeout=90))

    out = []
    with ThreadPoolExecutor(workers) as ex:
        for rows in ex.map(page, range(0, total, 1000)):
            out += rows
    return out


def _put(sb, path, obj, cache="max-age=600"):
    return sb.put_json(path, obj, cache=cache)


def _get(sb, path, default=None):
    try:
        return sb.get_json(path) or default
    except Exception:
        return default


def _try_upsert(sb, table, rows, key, log=print):
    """Writes to a workstream table; the table only exists once db/schema.sql has been applied."""
    if not rows:
        return 0
    try:
        return sb.upsert(table, rows, key)
    except HTTPError as e:
        log(f"  {table} upsert skipped: {str(e)[:160]}")
        return 0


# ---------------------------------------------------------------- universe
def load_universe(sb):
    cos = sb.select("companies", "select=ticker,cik,name,display_name,sector,sic,sic_description&order=ticker")
    ix = _get(sb, "index.json", {}) or {}
    by_ix = {c["ticker"]: c for c in ix.get("companies", [])}
    out = {}
    for c in cos:
        tk = c["ticker"]
        if c.get("sector") not in INDUSTRIES:
            continue
        ind, rule = industry_of(tk, c["sector"], c.get("sic"))
        x = by_ix.get(tk, {})
        out[tk] = {"tk": tk, "cik": c["cik"], "name": c.get("display_name") or x.get("name") or c["name"], "legal": c["name"], "sector": c["sector"],
                   "industry": ind, "rule": rule, "sic": c.get("sic"), "sic_desc": c.get("sic_description"), "mcap": x.get("market_cap"), "price": x.get("price"),
                   "ix": x}
    return out, ix


def groups_of(uni):
    """[(slug, name, kind, sector, [tickers])] for all sectors and industries."""
    gs = []
    for sec, rules in INDUSTRIES.items():
        members = sorted(t for t, c in uni.items() if c["sector"] == sec)
        gs.append({"slug": SECTOR_SLUG[sec], "name": sec, "kind": "sector", "sector": sec, "members": members, "etf": SECTOR_ETF.get(sec)})
        for name, _ in rules:
            m = sorted(t for t, c in uni.items() if c["sector"] == sec and c["industry"] == name)
            if m:
                gs.append({"slug": slugify(name), "name": name, "kind": "industry", "sector": sec, "members": m, "etf": None})
    return gs


# ---------------------------------------------------------------- fundamentals from facts
def load_facts(sb, log=print):
    """{ticker: {label: {qkey: [end, value, accession, form, derived]}}} for the quarterly facts the aggregates use."""
    out = {}

    def one(sl):
        stmt, label = sl
        q = (f"select=ticker,period_end,value,accession,form,derived&statement=eq.{stmt}&label=eq.{request_quote(label)}&kind=eq.Q"
             f"&period_end=gte.2007-01-01&order=ticker,period_end")
        return label, fetch_all(sb, "facts", q, workers=4)

    with ThreadPoolExecutor(4) as ex:
        for label, rows in ex.map(one, FACT_LABELS):
            for r in rows:
                k = qkey(r["period_end"])
                d = out.setdefault(r["ticker"], {}).setdefault(label, {})
                prev = d.get(k)
                # two fiscal quarters that land in one calendar quarter (52/53-week drift): keep the later one
                if not prev or r["period_end"] > prev[0]:
                    d[k] = [r["period_end"], float(r["value"]), r["accession"], r.get("form"), bool(r.get("derived"))]
            log(f"  facts {label}: {len(rows)} rows")
    return out


def request_quote(s):
    import urllib.parse
    return urllib.parse.quote(s, safe="")


# ---------------------------------------------------------------- monthly prices (Yahoo) with splits, cached
def monthly_history(sym):
    from .prices import yahoo_symbol
    import urllib.parse
    url = f"https://query2.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(yahoo_symbol(sym))}?range=20y&interval=1mo&events=split&includePrePost=false"
    j = get_json(url, timeout=30, retries=3)
    r = j["chart"]["result"][0]
    q = r["indicators"]["quote"][0]
    off = (r.get("meta") or {}).get("gmtoffset") or 0
    closes = {}
    for i, t in enumerate(r.get("timestamp") or []):
        c = q["close"][i]
        if c is None:
            continue
        m = dt.datetime.fromtimestamp(t + off + 5 * 86400, dt.timezone.utc).strftime("%Y-%m")
        closes[m] = round(c, 4)
    splits = []
    for s in ((r.get("events") or {}).get("splits") or {}).values():
        if s.get("numerator") and s.get("denominator"):
            splits.append([dt.datetime.fromtimestamp(s["date"], dt.timezone.utc).date().isoformat(), s["numerator"] / s["denominator"]])
    return {"c": closes, "splits": sorted(splits), "url": url.replace("&events=split", "&events=split"), "at": now_iso()}


def refresh_pxm(sb, tickers, max_age_days=6, time_budget=240, log=print):
    """Monthly closes for every stock and ETF. Entries older than max_age_days are refetched, oldest first, within the budget."""
    t0 = time.time()
    cache = _get(sb, "meta/sectors/pxm.json", {}) or {}
    data = cache.get("data", {})
    cut = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=max_age_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    todo = sorted([t for t in tickers if (data.get(t) or {}).get("at", "") < cut], key=lambda t: (data.get(t) or {}).get("at", ""))
    errors = {}

    def one(t):
        if time.time() - t0 > time_budget:
            return t, None, "skipped: budget"
        time.sleep(0.3)
        try:
            return t, monthly_history(t), None
        except Exception as e:
            return t, None, str(e)[:120]

    done = 0
    with ThreadPoolExecutor(3) as ex:
        for t, d, err in ex.map(one, todo):
            if d and d["c"]:
                data[t] = d
                done += 1
            elif err and not err.startswith("skipped"):
                errors[t] = err
    _put(sb, "meta/sectors/pxm.json", {"generated": now_iso(), "data": data}, cache="no-store")
    log(f"  monthly prices: {done} refreshed, {len(todo) - done} pending, {len(errors)} errors")
    return data, {"refreshed": done, "pending": len(todo) - done - len(errors), "errors": errors}


def unadjusted(px):
    """Month -> close as printed at the time (Yahoo closes are split-adjusted; splits after a month are undone)."""
    out = {}
    splits = px.get("splits") or []
    for m, c in px.get("c", {}).items():
        f = 1.0
        nxt = (dt.date(int(m[:4]), int(m[5:7]), 1) + dt.timedelta(days=32)).replace(day=1).isoformat()
        for d, ratio in splits:
            if d >= nxt:
                f *= ratio
        out[m] = c * f
    return out


# ---------------------------------------------------------------- per-company quarterly inputs
def company_quarters(tk, F, px, mcap_now, quarters):
    """{qkey: dict of COMP_FIELDS} for one company; None fields where the facts do not allow a value."""
    if not F:
        return {}
    # weighted diluted shares are not additive: a Q4 derived as the annual figure less nine months is meaningless
    F = dict(F)
    F["Diluted shares"] = {k: v for k, v in (F.get("Diluted shares") or {}).items() if not v[4] and v[1] > 0}
    get = lambda lab, k: (F.get(lab) or {}).get(k)
    val = lambda lab, k: (get(lab, k) or [None, None])[1]

    def ttm(lab, k):
        vs = [val(lab, qshift(k, -i)) for i in range(4)]
        return sum(vs) if all(v is not None for v in vs) else None

    unadj = unadjusted(px) if px else {}
    adj = (px or {}).get("c", {})
    last_m = max(unadj) if unadj else None
    sh_keys = sorted((F.get("Diluted shares") or {}).keys())
    sh_last = val("Diluted shares", sh_keys[-1]) if sh_keys else None
    scale = None
    if mcap_now and last_m and sh_last:
        scale = mcap_now / (unadj[last_m] * sh_last)
    out = {}
    for k in quarters:
        rev = get("Revenue", k)
        ni = get("Net income", k)
        row = {f: None for f in COMP_FIELDS}
        row["end"] = (rev or ni or [None])[0]
        row["rev_q"], row["ni_q"] = val("Revenue", k), val("Net income", k)
        py = qshift(k, -4)
        row["rev_py"], row["ni_py"] = val("Revenue", py), val("Net income", py)
        row["end_py"] = (get("Revenue", py) or get("Net income", py) or [None])[0]
        row["rev_ttm"], row["oi_ttm"], row["ni_ttm"] = ttm("Revenue", k), ttm("Operating income", k), ttm("Net income", k)
        gp = ttm("Gross profit", k)
        cor = ttm("Cost of revenue", k)
        row["cor_ttm"] = cor
        row["gp_ttm"] = gp if gp is not None else (row["rev_ttm"] - cor if row["rev_ttm"] is not None and cor is not None else None)
        row["ocf_ttm"], row["capex_ttm"], row["da_ttm"] = ttm("Operating cash flow", k), ttm("Capex", k), ttm("D&A", k)
        row["tax_ttm"], row["pretax_ttm"] = ttm("Income tax", k), ttm("Pretax income", k)
        row["ltd"], row["cash"], row["eq"], row["inv"] = val("Long-term debt", k), val("Cash & equivalents", k), val("Shareholders' equity", k), val("Inventory", k)
        sh = val("Diluted shares", k)
        if sh is None:
            prior = [x for x in sh_keys if x <= k][-1:]
            sh = val("Diluted shares", prior[0]) if prior and qshift(prior[0], 2) >= k else None
        row["sh"] = sh
        m = qend_month(k)
        p = unadj.get(m)
        row["px"] = p
        if scale and p and sh and adj.get(m):
            row["mcap"] = scale * p * sh
        if any(row[f] is not None for f in ("rev_q", "ni_q", "rev_ttm", "eq", "mcap")):
            out[k] = row
    return out


def company_metrics(r):
    """Per-company ratios for one quarter row."""
    g = lambda a, b: (a / b - 1) if a is not None and b and b > 0 else None
    d = lambda a, b: (a / b) if a is not None and b else None
    m = {}
    m["rev_growth"] = g(r["rev_q"], r["rev_py"])
    m["ni_growth"] = g(r["ni_q"], r["ni_py"])
    m["gm"] = d(r["gp_ttm"], r["rev_ttm"]) if r["rev_ttm"] and r["rev_ttm"] > 0 else None
    m["om"] = d(r["oi_ttm"], r["rev_ttm"]) if r["rev_ttm"] and r["rev_ttm"] > 0 else None
    m["nm"] = d(r["ni_ttm"], r["rev_ttm"]) if r["rev_ttm"] and r["rev_ttm"] > 0 else None
    fcf = r["ocf_ttm"] - r["capex_ttm"] if r["ocf_ttm"] is not None and r["capex_ttm"] is not None else None
    m["fcf_ttm"] = fcf
    m["fcfm"] = d(fcf, r["rev_ttm"]) if fcf is not None and r["rev_ttm"] and r["rev_ttm"] > 0 else None
    nopat, ic = nopat_ic(r)
    m["nopat"], m["ic"] = nopat, ic
    m["roic"] = nopat / ic if nopat is not None and ic and ic > 0 else None
    m["roe"] = r["ni_ttm"] / r["eq"] if r["ni_ttm"] is not None and r["eq"] and r["eq"] > 0 else None
    ebitda = r["oi_ttm"] + r["da_ttm"] if r["oi_ttm"] is not None and r["da_ttm"] is not None else None
    m["ebitda"] = ebitda
    m["nd"] = (r["ltd"] or 0) - (r["cash"] or 0) if r["ltd"] is not None else None
    m["nd_ebitda"] = m["nd"] / ebitda if m["nd"] is not None and ebitda and ebitda > 0 else None
    m["de"] = r["ltd"] / r["eq"] if r["ltd"] is not None and r["eq"] and r["eq"] > 0 else None
    mc = r["mcap"]
    m["pe"] = mc / r["ni_ttm"] if mc and r["ni_ttm"] and r["ni_ttm"] > 0 else None
    m["ps"] = mc / r["rev_ttm"] if mc and r["rev_ttm"] and r["rev_ttm"] > 0 else None
    m["pfcf"] = mc / fcf if mc and fcf and fcf > 0 else None
    return m


def nopat_ic(r):
    if r["oi_ttm"] is None or r["eq"] is None:
        return None, None
    t = None
    if r["tax_ttm"] is not None and r["pretax_ttm"] and r["pretax_ttm"] > 0:
        t = r["tax_ttm"] / r["pretax_ttm"]
    if t is None or t < 0 or t > 0.5:
        t = 0.21
    ic = r["eq"] + (r["ltd"] or 0) - (r["cash"] or 0)
    return r["oi_ttm"] * (1 - t), ic


def aggregate(members, CQ, CM, quarters):
    """{metric: {agg, cw, med, n}} lists aligned to quarters for one group."""
    out = {k: {"agg": [], "cw": [], "med": [], "n": []} for k in METRICS}
    for k in quarters:
        rows = [(t, CQ[t][k], CM[t][k]) for t in members if k in CQ.get(t, {})]

        def ratio(num, den, cond=lambda r, m: True, positive=True):
            pairs = [(num(r, m), den(r, m)) for _t, r, m in rows if cond(r, m)]
            pairs = [(a, b) for a, b in pairs if a is not None and b is not None]
            sa, sb_ = sum(a for a, _ in pairs), sum(b for _, b in pairs)
            if not pairs or (positive and sb_ <= 0) or sb_ == 0:
                return None, len(pairs)
            return sa / sb_, len(pairs)

        def put(name, agg, n, key=None):
            key = key or name
            vals = [(m.get(key), r["mcap"]) for _t, r, m in rows if m.get(key) is not None]
            w = [(v, c) for v, c in vals if c]
            cw = sum(v * c for v, c in w) / sum(c for _, c in w) if w else None
            out[name]["agg"].append(_r(agg))
            out[name]["cw"].append(_r(cw))
            out[name]["med"].append(_r(median([v for v, _ in vals])))
            out[name]["n"].append(n)

        a, n = ratio(lambda r, m: r["rev_q"], lambda r, m: r["rev_py"])
        put("rev_growth", a - 1 if a is not None else None, n)
        a, n = ratio(lambda r, m: r["ni_q"], lambda r, m: r["ni_py"])
        put("ni_growth", a - 1 if a is not None else None, n)
        g = [m["rev_growth"] for _t, r, m in rows if m["rev_growth"] is not None]
        out["pct_rev_up"]["agg"].append(_r(sum(1 for x in g if x > 0) / len(g)) if g else None)
        out["pct_rev_up"]["cw"].append(None); out["pct_rev_up"]["med"].append(None); out["pct_rev_up"]["n"].append(len(g))
        g = [m["ni_growth"] for _t, r, m in rows if m["ni_growth"] is not None]
        out["pct_ni_up"]["agg"].append(_r(sum(1 for x in g if x > 0) / len(g)) if g else None)
        out["pct_ni_up"]["cw"].append(None); out["pct_ni_up"]["med"].append(None); out["pct_ni_up"]["n"].append(len(g))
        rev = lambda r, m: r["rev_ttm"] if r["rev_ttm"] and r["rev_ttm"] > 0 else None
        put("gm", *ratio(lambda r, m: r["gp_ttm"], rev))
        put("om", *ratio(lambda r, m: r["oi_ttm"], rev))
        put("nm", *ratio(lambda r, m: r["ni_ttm"], rev))
        put("fcfm", *ratio(lambda r, m: m["fcf_ttm"], rev))
        put("roic", *ratio(lambda r, m: m["nopat"], lambda r, m: m["ic"] if m["ic"] and m["ic"] > 0 else None))
        put("roe", *ratio(lambda r, m: r["ni_ttm"], lambda r, m: r["eq"] if r["eq"] and r["eq"] > 0 else None))
        put("nd_ebitda", *ratio(lambda r, m: m["nd"], lambda r, m: m["ebitda"] if m["ebitda"] and m["ebitda"] > 0 else None))
        put("de", *ratio(lambda r, m: r["ltd"], lambda r, m: r["eq"] if r["eq"] and r["eq"] > 0 else None))
        put("pe", *_nm(ratio(lambda r, m: r["mcap"], lambda r, m: r["ni_ttm"])))
        put("ps", *ratio(lambda r, m: r["mcap"], rev))
        put("pfcf", *_nm(ratio(lambda r, m: r["mcap"], lambda r, m: m["fcf_ttm"])))
        for name, f in (("rev_ttm", "rev_ttm"), ("ni_ttm", "ni_ttm"), ("mcap", "mcap")):
            vs = [r[f] for _t, r, m in rows if r[f] is not None]
            out[name]["agg"].append(_r(sum(vs)) if vs else None)
            out[name]["cw"].append(None); out[name]["med"].append(_r(median(vs))); out[name]["n"].append(len(vs))
    return out


def _nm(res, cap=150):
    """Aggregate multiples above `cap` mean the group's earnings were close to zero: not meaningful, left out."""
    v, n = res
    return (v if v is not None and v <= cap else None), n


def current_valuation(members, uni, CQ, CM, quarters):
    """Today's aggregate multiples: index market caps over each company's latest TTM (reported within the last two quarters)."""
    last = quarters[-1]
    rows = []
    for t in members:
        mc = uni[t].get("mcap")
        q = CQ.get(t, {})
        ks = [k for k in sorted(q) if k >= qshift(last, -2) and q[k]["ni_ttm"] is not None]
        if not mc or not ks:
            continue
        r, m = q[ks[-1]], CM[t][ks[-1]]
        rows.append({"tk": t, "q": ks[-1], "mcap": mc, "ni": r["ni_ttm"], "rev": r["rev_ttm"], "fcf": m["fcf_ttm"]})
    out = {"n": len(rows), "rows": [[x["tk"], x["q"], _r(x["mcap"]), _r(x["ni"]), _r(x["rev"]), _r(x["fcf"])] for x in rows]}
    smc = sum(x["mcap"] for x in rows)
    for key, den in (("pe", "ni"), ("ps", "rev"), ("pfcf", "fcf")):
        sel = [x for x in rows if x[den] is not None]
        s = sum(x[den] for x in sel)
        out[key] = _r(sum(x["mcap"] for x in sel) / s) if sel and s > 0 else None
        out[key + "_med"] = _r(median([x["mcap"] / x[den] for x in sel if x[den] > 0]))
    out["mcap"] = _r(smc)
    return out


def ten_year(series, quarters, now):
    """Range of the aggregate series over the last 40 quarters and where `now` sits in it."""
    vals = [(k, v) for k, v in zip(quarters, series) if v is not None and v > 0][-40:]
    if len(vals) < 8:
        return None
    vs = sorted(v for _, v in vals)
    out = {"min": _r(vs[0]), "max": _r(vs[-1]), "med": _r(statistics.median(vs)), "from": vals[0][0], "to": vals[-1][0], "n": len(vs),
           "min_q": min(vals, key=lambda x: x[1])[0], "max_q": max(vals, key=lambda x: x[1])[0]}
    if now is not None:
        out["pct"] = _r(sum(1 for v in vs if v <= now) / len(vs))
    return out


# ---------------------------------------------------------------- daily prices and performance
def load_daily(sb, syms, log=print):
    """{symbol: [(date, close)]} from the published 2-year price files (split-consistent, rewritten daily)."""
    from .prices import path_key

    def one(s):
        try:
            b = sb.get_object("public-data", f"prices/{path_key(s)}.json")
            if not b:
                return s, None
            j = json.loads(b)
            return s, [(dt.datetime.fromtimestamp(x[0], dt.timezone.utc).date().isoformat(), x[4]) for x in j.get("d", [])]
        except Exception as e:
            log(f"  prices {s}: {str(e)[:80]}")
            return s, None

    out = {}
    with ThreadPoolExecutor(8) as ex:
        for s, d in ex.map(one, syms):
            if d:
                out[s] = d
    return out


def ticker_signals(D, dates):
    """Per stock, aligned to dates: close, above-50-day flag, above-200-day flag (None until enough history) and the
    21-day return. Computed once and shared by every group."""
    out = {}
    for t, rows in D.items():
        m = dict(rows)
        c = [m.get(d) for d in dates]
        f50, f200, r21 = [None] * len(c), [None] * len(c), [None] * len(c)
        pc, ps = [0], [0.0]           # prefix counts and sums of the closes present
        for v in c:
            pc.append(pc[-1] + (v is not None))
            ps.append(ps[-1] + (v or 0.0))
        for i, v in enumerate(c):
            if v is None:
                continue
            # windows count trading days on the SPY calendar; most of them must have a close
            for w, f, need in ((50, f50, 45), (200, f200, 180)):
                if i >= w - 1:
                    n = pc[i + 1] - pc[i + 1 - w]
                    if n >= need:
                        f[i] = v > (ps[i + 1] - ps[i + 1 - w]) / n
            if i >= 21 and c[i - 21]:
                r21[i] = v / c[i - 21] - 1
        out[t] = (c, f50, f200, r21)
    return out


def performance(members, uni, SIG, dates):
    """Cap- and equal-weight index, breadth, dispersion and concentration for a group over the daily dates."""
    tks = [t for t in members if t in SIG]
    shares = {}
    for t in tks:
        mc, p = uni[t].get("mcap"), uni[t].get("price")
        if mc and p:
            shares[t] = mc / p
    cw, ew, b50, b200, disp, top5 = [100.0], [100.0], [], [], [], []
    for i in range(len(dates)):
        if i:
            num = den = 0.0
            rs = []
            for t in tks:
                a, b = SIG[t][0][i - 1], SIG[t][0][i]
                if a and b:
                    rs.append(b / a - 1)
                    if t in shares:
                        num += shares[t] * b
                        den += shares[t] * a
            cw.append(cw[-1] * (num / den if den else 1))
            ew.append(ew[-1] * (1 + (_mean(rs) if rs else 0)))
        f5 = [SIG[t][1][i] for t in tks if SIG[t][1][i] is not None]
        f2 = [SIG[t][2][i] for t in tks if SIG[t][2][i] is not None]
        r21 = [SIG[t][3][i] for t in tks if SIG[t][3][i] is not None]
        b50.append(_r(sum(f5) / len(f5), 4) if f5 else None)
        b200.append(_r(sum(f2) / len(f2), 4) if f2 else None)
        disp.append(_r(statistics.pstdev(r21), 4) if len(r21) >= 3 else None)
        caps = sorted((shares[t] * SIG[t][0][i] for t in shares if SIG[t][0][i]), reverse=True)
        top5.append(_r(sum(caps[:5]) / sum(caps), 4) if caps else None)
    above = {}
    for t in tks:
        f5 = next((x for x in reversed(SIG[t][1]) if x is not None), None)
        f2 = next((x for x in reversed(SIG[t][2]) if x is not None), None)
        if f5 is not None or f2 is not None:
            above[t] = [f5, f2]
    return {"cw": [_r(x, 7) for x in cw], "ew": [_r(x, 7) for x in ew], "b50": b50, "b200": b200, "disp": disp, "top5": top5}, above


def window_returns(series, dates):
    """1W, 1M, 3M, 6M, YTD, 1Y returns of a level series on dates."""
    out = {}
    if not series:
        return out
    end = dt.date.fromisoformat(dates[-1])

    def at_or_after(target):
        for i, d in enumerate(dates):
            if d >= target:
                return i
        return None

    for k, days in (("1W", 7), ("1M", 30), ("3M", 91), ("6M", 182), ("1Y", 365)):
        i = at_or_after((end - dt.timedelta(days=days)).isoformat())
        out[k] = _r(series[-1] / series[i] - 1) if i is not None and series[i] else None
    ys = [i for i, d in enumerate(dates) if d < f"{end.year}-01-01"]
    out["YTD"] = _r(series[-1] / series[ys[-1]] - 1) if ys else None
    return out


def weekly(dates, series):
    """Last value of each ISO week."""
    out = {}
    for d, v in zip(dates, series):
        if v is None:
            continue
        y, w, _ = dt.date.fromisoformat(d).isocalendar()
        out[(y, w)] = (d, v)
    return [out[k] for k in sorted(out)]


def rrg(dates, series, spy, tail=10):
    """Relative rotation: RS-Ratio = 100 x (price/SPY) / its 13-week mean; RS-Momentum = 100 x RS-Ratio / RS-Ratio four weeks earlier."""
    w = weekly(dates, [s / b if s and b else None for s, b in zip(series, spy)])
    if len(w) < 13 + 4 + tail:
        return None
    rs = [v for _, v in w]
    ratio = [None] * len(rs)
    for i in range(12, len(rs)):
        ratio[i] = 100 * rs[i] / _mean(rs[i - 12:i + 1])
    pts = []
    for i in range(len(rs) - tail, len(rs)):
        if ratio[i] is not None and ratio[i - 4]:
            pts.append([w[i][0], _r(ratio[i], 5), _r(100 * ratio[i] / ratio[i - 4], 5)])
    return pts


# ---------------------------------------------------------------- macro factors
def fred_series(sid, start):
    from .macro import _fred_csv
    import csv
    import io
    text = _fred_csv(sid, start)
    pts = []
    for r in csv.DictReader(io.StringIO(text.replace("\r\n", "\n").strip(), newline="")):
        d = r.get("observation_date") or r.get("DATE")
        v = r.get(sid)
        if d and v not in (None, "", "."):
            pts.append([d, float(v)])
    return pts


def load_factors(sb, log=print):
    """Daily and monthly factor series with provenance: macro_observations plus FRED series the store lacks."""
    cache = _get(sb, "meta/sectors/factors.json", {}) or {}
    fred = cache.get("fred", {})
    for sid in FRED_FACTORS:
        prev = fred.get(sid) or {}
        if prev.get("at", "") >= (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=20)).strftime("%Y-%m-%dT%H:%M:%SZ"):
            continue
        try:
            pts = fred_series(sid, "2014-01-01")
            if pts:
                fred[sid] = {"pts": pts, "at": now_iso(), "url": f"https://fred.stlouisfed.org/series/{sid}", "csv": f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}"}
        except Exception as e:
            log(f"  fred {sid} failed: {str(e)[:120]}")
    _put(sb, "meta/sectors/factors.json", {"generated": now_iso(), "fred": fred}, cache="no-store")
    macro = {}
    since = (dt.date.today() - dt.timedelta(days=365 * 11)).isoformat()
    for s in MACRO_NAMES:
        rows = fetch_all(sb, "macro_observations", f"select=d,value&series=eq.{request_quote(s)}&d=gte.{since}&order=d", workers=3)
        if rows:
            macro[s] = [[r["d"], float(r["value"])] for r in rows]
    return fred, macro


def build_factors(fred, macro, D):
    """Factor definitions: daily change series (2 years) and month-end change series (10 years)."""
    out = []

    def daily_changes(pts, kind):
        ch = {}
        for (d0, v0), (d1, v1) in zip(pts, pts[1:]):
            if kind == "pct":
                if v0 and v0 > 0 and v1 > 0:
                    ch[d1] = v1 / v0 - 1
            else:
                ch[d1] = (v1 - v0) * 100       # percentage points -> basis points
        return ch

    def month_end(pts):
        m = {}
        for d, v in pts:
            m[d[:7]] = v
        return m

    def monthly_changes(pts, kind):
        m = month_end(pts)
        ks = sorted(m)
        ch = {}
        for a, b in zip(ks, ks[1:]):
            if kind == "pct":
                if m[a] and m[a] > 0 and m[b] > 0:
                    ch[b] = m[b] / m[a] - 1
            elif kind == "yoy":
                pa = f"{int(a[:4]) - 1}{a[4:]}"
                pb = f"{int(b[:4]) - 1}{b[4:]}"
                if m.get(pa) and m.get(pb):
                    ch[b] = ((m[b] / m[pb] - 1) - (m[a] / m[pa] - 1)) * 1e4    # bp change in YoY rate
            elif kind == "diff":
                ch[b] = m[b] - m[a]
            else:
                ch[b] = (m[b] - m[a]) * 100
        return ch

    def add(fid, name, kind, unit_change, source, url, pts, daily=True, retrieved=None):
        d = daily_changes(pts, "pct" if kind == "pct" else "level") if daily else {}
        mkind = kind if kind in ("pct", "yoy", "diff") else "level"
        out.append({"id": fid, "name": name, "unit": unit_change, "source": source, "url": url, "retrieved": retrieved,
                    "first": pts[0][0] if pts else None, "last": pts[-1][0] if pts else None, "n_obs": len(pts),
                    "daily": d, "monthly": monthly_changes(pts, mkind), "latest": pts[-1] if pts else None})

    tsy = macro.get("TSY:10Y") or []
    dgs = (fred.get("DGS10") or {}).get("pts") or []
    # 10Y yield: Treasury's own series where it covers the window, FRED DGS10 (H.15) for the longer monthly history
    if dgs:
        long_pts = sorted({**{d: v for d, v in dgs}, **{d: v for d, v in tsy}}.items())
        add("y10", "10Y Treasury yield", "level", "bp", "U.S. Treasury par yield curve (2024 on) and Federal Reserve H.15 via FRED DGS10", "https://fred.stlouisfed.org/series/DGS10",
            [list(x) for x in long_pts], retrieved=(fred.get("DGS10") or {}).get("at"))
    for sid in ("BAA10Y", "DTWEXBGS", "DCOILWTICO"):
        f = fred.get(sid)
        if f and f.get("pts"):
            m = FRED_FACTORS[sid]
            add(sid.lower(), m["name"], "pct" if m["kind"] == "pct" else "level", "%" if m["kind"] == "pct" else "bp", m["source"], f["url"], f["pts"], retrieved=f.get("at"))
    for s, (name, kind, src) in MACRO_NAMES.items():
        if s == "TSY:10Y" or s not in macro:
            continue
        pts = macro[s]
        recent = [p for p in pts if p[0] >= (dt.date.today() - dt.timedelta(days=730)).isoformat()]
        is_daily = len(recent) >= 200
        if is_daily:
            ch = daily_changes(recent, "level")
            if sum(1 for v in ch.values() if v != 0) < len(ch) * 0.2:
                is_daily = False      # rarely moves day to day (policy rate): monthly only
        if s == "TSY:2Y":
            continue                   # the curve slope and the 10Y cover it
        add(s.split(":")[1].lower(), name, kind, "bp" if kind in ("level", "yoy") else "k", src + " (macro_observations " + s + ")",
            None, pts, daily=is_daily)
    return out


def macro_sensitivity(members, dates, perf, factors, monthly_idx, comp_monthly):
    """Correlation and beta of the group's returns to each factor: daily over the last year (with a rolling 3-month
    correlation) and monthly over ten years; the companies with the strongest monthly relationship."""
    cw = perf["cw"]
    r = {dates[i]: cw[i] / cw[i - 1] - 1 for i in range(1, len(dates)) if cw[i - 1]}
    out = []
    for f in factors:
        row = {"id": f["id"], "name": f["name"], "unit": f["unit"]}
        if f["daily"]:
            ds = [d for d in dates[-253:] if d in r and d in f["daily"]]
            y, x = [r[d] for d in ds], [f["daily"][d] for d in ds]
            c, b = corr_beta(y, x)
            row["d"] = {"corr": _r(c, 4), "beta": _r(b, 4), "n": len(ds), "from": ds[0] if ds else None, "to": ds[-1] if ds else None}
            all_d = [d for d in dates if d in r and d in f["daily"]]
            roll = []
            for i in range(63, len(all_d) + 1, 5):
                w = all_d[i - 63:i]
                cc, _ = corr_beta([r[d] for d in w], [f["daily"][d] for d in w])
                roll.append([w[-1], _r(cc, 3)])
            row["roll"] = roll
        ms = [m for m in sorted(monthly_idx) if m in f["monthly"]][-120:]
        if len(ms) >= 36:
            c, b = corr_beta([monthly_idx[m] for m in ms], [f["monthly"][m] for m in ms])
            row["m"] = {"corr": _r(c, 4), "beta": _r(b, 4), "n": len(ms), "from": ms[0], "to": ms[-1]}
            tops = []
            for t in members:
                cm = comp_monthly.get(t) or {}
                mm = [m for m in ms if m in cm]
                if len(mm) >= 36:
                    cc, bb = corr_beta([cm[m] for m in mm], [f["monthly"][m] for m in mm])
                    if cc is not None:
                        tops.append({"tk": t, "corr": _r(cc, 3), "beta": _r(bb, 4), "n": len(mm)})
            tops.sort(key=lambda z: -abs(z["corr"]))
            row["top"] = tops[:6]
        if "d" in row or "m" in row:
            out.append(row)
    return out


def monthly_returns(px):
    c = px.get("c", {}) if px else {}
    ks = sorted(c)
    return {b: c[b] / c[a] - 1 for a, b in zip(ks, ks[1:]) if c[a]}


def monthly_index(members, uni, pxm):
    """Cap-weighted monthly price return of the group: weights are each stock's market cap at the start of the month
    (today's market cap scaled by its adjusted price)."""
    rets, caps = {}, {}
    for t in members:
        px = pxm.get(t)
        mc = uni[t].get("mcap")
        if not px or not mc:
            continue
        c = px["c"]
        last = c[max(c)]
        rets[t] = monthly_returns(px)
        caps[t] = {m: mc * v / last for m, v in c.items()}
    out = {}
    months = sorted({m for v in rets.values() for m in v})
    this_month = dt.date.today().strftime("%Y-%m")
    for m in months:
        if m >= this_month:
            continue
        prev = (dt.date(int(m[:4]), int(m[5:]), 1) - dt.timedelta(days=1)).strftime("%Y-%m")
        num = den = 0.0
        for t in rets:
            if m in rets[t] and prev in caps[t]:
                num += caps[t][prev] * rets[t][m]
                den += caps[t][prev]
        if den:
            out[m] = num / den
    return out, {t: {m: v for m, v in rets[t].items() if m < this_month} for t in rets}


# ---------------------------------------------------------------- KPIs
def load_kpis(sb, uni, F, CQ, quarters, log=print):
    """{industry: [kpi block]} following KPI_SPECS."""
    defs = fetch_all(sb, "kpi_definitions", "select=ticker,kpi_id,name,unit&source_kind=eq.ir&order=ticker,kpi_id", workers=4)
    picks = {}   # spec id -> {ticker: [kpi defs]}
    from .kpistd import primary_map
    std, std_f = primary_map(sb, with_factors=True)   # standardized KPI ids: {ticker: {canonical: kpi_id}}
    dby = {(d["ticker"], d["kpi_id"]): d for d in defs} if std else {}
    for spec in KPI_SPECS:
        if spec.get("canonical") and std:
            # one total-company series per company from the standardized mapping (never several bases joined)
            for t, cm in std.items():
                c, kid = uni.get(t), cm.get(spec["canonical"])
                if c and kid and c["industry"] in spec["industries"] and (t, kid) in dby:
                    picks.setdefault(spec["id"], {})[t] = [dby[(t, kid)]]
            continue
        if not spec.get("match"):
            continue
        rx = re.compile(spec["match"], re.I)
        for d in defs:
            c = uni.get(d["ticker"])
            if not c or c["industry"] not in spec["industries"]:
                continue
            nm = re.sub(r"\s+", " ", (d["name"] or "").strip())
            if rx.search(nm) and _unit_ok(d.get("unit"), spec["unit"]):
                picks.setdefault(spec["id"], {}).setdefault(d["ticker"], []).append(d)
    values = {}
    pairs = [(t, d["kpi_id"]) for sid in picks for t, ds in picks[sid].items() for d in ds]
    by_t = {}
    for t, k in pairs:
        by_t.setdefault(t, set()).add(k)

    def one(t):
        ids = ",".join('"' + k.replace('"', '') + '"' for k in sorted(by_t[t]))
        rows = fetch_all(sb, "kpi_values", f"select=kpi_id,period_kind,period_end,fiscal_label,value,unit,source_id,anchor,derived&ticker=eq.{t}&period_kind=eq.Q&kpi_id=in.({request_quote(ids)})&order=kpi_id,period_end", workers=2)
        return t, rows

    with ThreadPoolExecutor(6) as ex:
        for t, rows in ex.map(one, list(by_t)):
            for r in rows:
                values.setdefault((t, r["kpi_id"]), []).append(r)
    recent = quarters[-24:]
    out = {}
    for spec in KPI_SPECS:
        comps = {}
        defs_used = {}
        if spec.get("match") or spec.get("canonical"):
            fac = {t: (std_f.get(t) or {}).get(spec["canonical"], 1) for t in (picks.get(spec["id"]) or {})} if spec.get("canonical") and std else {}
            for t, ds in (picks.get(spec["id"]) or {}).items():
                # one definition per company: the one with the most quarters, then the shortest id
                best = max(ds, key=lambda d: (len(values.get((t, d["kpi_id"]), [])), -len(d["kpi_id"])))
                rows = values.get((t, best["kpi_id"]), [])
                if not rows:
                    continue
                defs_used[t] = {"kpi_id": best["kpi_id"], "name": best["name"], "unit": best.get("unit")}
                if t in fac:
                    defs_used[t]["canonical"] = spec["canonical"]
                ser = {}
                for r in rows:
                    k = qkey(r["period_end"])
                    a = r.get("anchor") or {}
                    ser[k] = [_r(float(r["value"]) * fac.get(t, 1)), r["period_end"], r.get("fiscal_label"), r.get("source_id"), a.get("page"), (a.get("quote") or "")[:300], a.get("sheet"), a.get("cell"),
                              bool(r.get("derived"))]
                comps[t] = ser
        elif spec.get("fact"):
            for t, c in uni.items():
                if c["industry"] not in spec["industries"]:
                    continue
                ser = {}
                for k, rec in ((F.get(t) or {}).get(spec["fact"]) or {}).items():
                    ser[k] = [_r(rec[1]), rec[0], None, rec[2], None, None, None, None, rec[4], spec["fact"]]
                if ser:
                    comps[t] = ser
                    defs_used[t] = {"fact": spec["fact"]}
        elif spec.get("derived") == "inventory_days":
            for t, c in uni.items():
                if c["industry"] not in spec["industries"]:
                    continue
                ser = {}
                for k, row in (CQ.get(t) or {}).items():
                    if row["inv"] is not None and row["cor_ttm"] and row["cor_ttm"] > 0:
                        ser[k] = [_r(row["inv"] / row["cor_ttm"] * 365), row["end"], None, None, None, None, None, None, True, "Inventory / Cost of revenue TTM x 365"]
                if ser:
                    comps[t] = ser
                    defs_used[t] = {"derived": "Inventory / trailing four quarters of Cost of revenue x 365"}
        if not comps:
            continue
        for ind in spec["industries"]:
            members = [t for t in comps if uni[t]["industry"] == ind]
            if not members:
                continue
            agg, n = [], []
            for k in recent:
                vs = [comps[t][k][0] for t in members if k in comps[t] and comps[t][k][0] is not None]
                n.append(len(vs))
                if not vs:
                    agg.append(None)
                elif spec["how"] == "sum":
                    agg.append(_r(sum(vs)))
                else:
                    agg.append(_r(statistics.median(vs)))
            if not any(n):
                continue
            out.setdefault(ind, []).append({"id": spec["id"], "name": spec["name"], "unit": spec["unit"], "how": spec["how"], "definition": spec["definition"],
                                            "quarters": recent, "agg": agg, "n": n, "members_total": sum(1 for c in uni.values() if c["industry"] == ind),
                                            "defs": {t: defs_used[t] for t in members},
                                            "comps": {t: {k: v for k, v in comps[t].items() if k in recent} for t in members}})
    log(f"  kpis: {sum(len(v) for v in out.values())} industry blocks")
    return out


def _unit_ok(u, want):
    u = (u or "").strip().lower()
    if want == "%":
        return u in ("%", "pct", "percent")
    if want == "x":
        return u in ("x", "ratio", "")
    if want == "USD":
        return u in ("usd", "$")
    if want == "ASM":
        return u in ("asm", "asms", "miles", "")
    return True


# ---------------------------------------------------------------- segments, insiders, filings
def humanize_member(member):
    m = member.split(":")[-1]
    m = re.sub(r"(Segments?|Operating|Reportable)?Member$", "", m)
    m = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", m)
    return m.replace(" And ", " and ").strip() or member


def load_segments(sb, uni, log=print):
    """Latest annual business-segment revenue per company (XBRL), with the industry its name points to."""
    rows = fetch_all(sb, "kpi_values", "select=ticker,kpi_id,period_end,fiscal_label,value,source_id,anchor&period_kind=eq.A&source_kind=eq.xbrl"
                     "&kpi_id=like.xbrl:revenue:*StatementBusinessSegmentsAxis:*&period_end=gte." + (dt.date.today() - dt.timedelta(days=800)).isoformat()
                     + "&order=ticker,kpi_id,period_end", workers=4)
    latest = {}
    for r in rows:
        key = (r["ticker"], r["kpi_id"])
        if key not in latest or r["period_end"] > latest[key]["period_end"]:
            latest[key] = r
    by_t = {}
    for (t, kid), r in latest.items():
        by_t.setdefault(t, []).append(r)
    out, seen = [], set()
    for t, rs in by_t.items():
        if t not in uni:
            continue
        end = max(r["period_end"] for r in rs)
        for r in rs:
            if r["period_end"] != end:
                continue
            member = r["kpi_id"].split("StatementBusinessSegmentsAxis:", 1)[1]
            if "|" in member or member.count(":") > 1:
                continue
            label = humanize_member(member)
            if (t, label.lower()) in seen or (t, float(r["value"])) in seen:
                continue
            seen.update({(t, label.lower()), (t, float(r["value"]))})
            ind, pat = segment_industry(label)
            out.append({"tk": t, "member": member, "label": label, "industry": ind, "rule": pat, "v": _r(float(r["value"])), "end": r["period_end"],
                        "fy": r.get("fiscal_label"), "kpi_id": r["kpi_id"], "accn": r.get("source_id")})
    log(f"  segments: {len(out)} latest annual segment rows")
    return out


def load_insiders(sb, days=180):
    since = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    return fetch_all(sb, "insider_transactions", f"select=accession,seq,ticker,cik,filed,owner,role,tx_date,code,shares,price,plan_10b5&derivative=eq.false&code=in.(P,S)&tx_date=gte.{since}&order=accession,seq", workers=4)


def insider_block(members, uni, rows):
    ms = set(members)
    rs = [r for r in rows if r["ticker"] in ms and r.get("shares") and r.get("price")]
    by_co, by_month, trades = {}, {}, []
    for r in rs:
        v = float(r["shares"]) * float(r["price"])
        buy = r["code"] == "P"
        c = by_co.setdefault(r["ticker"], {"buy": 0.0, "sell": 0.0, "nb": 0, "ns": 0, "owners": set()})
        c["buy" if buy else "sell"] += v
        c["nb" if buy else "ns"] += 1
        c["owners"].add(r.get("owner"))
        m = (r.get("tx_date") or r["filed"])[:7]
        mm = by_month.setdefault(m, [0.0, 0.0])
        mm[0 if buy else 1] += v
        trades.append(r | {"value": v})
    trades.sort(key=lambda r: -r["value"])
    cik = lambda t: uni[t]["cik"]
    link = lambda r: f"https://www.sec.gov/Archives/edgar/data/{cik(r['ticker'])}/{r['accession'].replace('-', '')}/{r['accession']}-index.htm"
    return {
        "since": min((r.get("tx_date") or r["filed"] for r in rs), default=None),
        "buy": _r(sum(c["buy"] for c in by_co.values())), "sell": _r(sum(c["sell"] for c in by_co.values())),
        "n_buy": sum(c["nb"] for c in by_co.values()), "n_sell": sum(c["ns"] for c in by_co.values()),
        "companies": sorted([{"tk": t, "buy": _r(c["buy"]), "sell": _r(c["sell"]), "nb": c["nb"], "ns": c["ns"], "owners": len(c["owners"])} for t, c in by_co.items()],
                            key=lambda x: -(x["buy"] + x["sell"])),
        "months": [[m, _r(v[0]), _r(v[1])] for m, v in sorted(by_month.items())],
        "top": [{"tk": r["ticker"], "owner": r.get("owner"), "role": r.get("role"), "date": r.get("tx_date"), "code": r["code"], "shares": _r(float(r["shares"])),
                 "price": _r(float(r["price"])), "value": _r(r["value"]), "accn": r["accession"], "plan": r.get("plan_10b5"), "url": link(r)} for r in trades[:25]],
    }


def load_filings(sb, days=30):
    since = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    return fetch_all(sb, "filings", f"select=accession,ticker,form,filed,report_date,items,description,url,primary_doc&filed=gte.{since}&form=neq.4&order=filed.desc,accession.desc", workers=4)


# ---------------------------------------------------------------- the publish step
def publish(sb, log=print, time_budget=None):
    t0 = time.time()
    stats = {}
    uni, ix = load_universe(sb)
    groups = groups_of(uni)
    log(f"universe {len(uni)} companies, {len(groups)} groups")
    F = load_facts(sb, log=log)
    stats["facts_companies"] = len(F)
    pxm = (_get(sb, "meta/sectors/pxm.json", {}) or {}).get("data", {})
    stats["monthly_prices"] = len(pxm)
    all_q = sorted({k for t in F.values() for lab in t.values() for k in lab})
    last_q = qkey(dt.date.today().isoformat())
    last_q = max(k for k in all_q if k <= last_q)
    quarters = qrange(START_Q, last_q)
    CQ, CM = {}, {}
    for t, c in uni.items():
        CQ[t] = company_quarters(t, F.get(t), pxm.get(t), c.get("mcap"), quarters)
        CM[t] = {k: company_metrics(r) for k, r in CQ[t].items()}
    log(f"company quarters built ({time.time() - t0:.0f}s)")

    syms = sorted(set(uni) | {"SPY"} | set(SECTOR_ETF.values()))
    D = load_daily(sb, syms, log=log)
    dates = [d for d, _ in D.get("SPY", [])]
    spy_map = dict(D.get("SPY", []))
    spy = [spy_map.get(d) for d in dates]
    SIG = ticker_signals(D, dates)
    log(f"daily prices {len(D)} symbols, {len(dates)} days ({time.time() - t0:.0f}s)")
    fred, macro = load_factors(sb, log=log)
    factors = build_factors(fred, macro, D)
    kpis = load_kpis(sb, uni, F, CQ, quarters, log=log)
    segs = load_segments(sb, uni, log=log)
    ins = load_insiders(sb)
    fil = load_filings(sb)
    log(f"inputs loaded ({time.time() - t0:.0f}s)")

    overview = []
    etf_rrg = {}
    for sec, etf in SECTOR_ETF.items():
        if etf in D:
            em = dict(D[etf])
            etf_rrg[etf] = rrg(dates, [em.get(d) for d in dates], spy)
    spy_ret = window_returns(spy, dates)
    db_rows = []
    writer = ThreadPoolExecutor(6)
    pending = []
    done_slugs = set()
    group_perf = {}
    for g in groups:
        members = [t for t in g["members"] if t in uni]
        agg = aggregate(members, CQ, CM, quarters)
        val_now = current_valuation(members, uni, CQ, CM, quarters)
        val_range = {k: ten_year(agg[k]["agg"], quarters, val_now.get(k)) for k in ("pe", "ps", "pfcf")}
        perf, above = performance(members, uni, SIG, dates)
        group_perf[g["slug"]] = perf
        rets = {"cw": window_returns(perf["cw"], dates), "ew": window_returns(perf["ew"], dates), "spy": spy_ret}
        if g["etf"] and g["etf"] in D:
            em = dict(D[g["etf"]])
            rets["etf"] = window_returns([em.get(d) for d in dates], dates)
        rot = rrg(dates, perf["cw"], spy)
        midx, comp_m = monthly_index(members, uni, pxm)
        sens = macro_sensitivity(members, dates, perf, factors, midx, comp_m)
        mc_total = sum(uni[t]["mcap"] or 0 for t in members)
        cons = []
        for t in members:
            c = uni[t]
            x = c["ix"]
            q = CQ.get(t, {})
            lk = max(q) if q else None
            m = CM[t].get(lk, {}) if lk else {}
            cons.append({"tk": t, "name": c["name"], "industry": c["industry"], "sic": c["sic"], "rule": c["rule"], "mcap": _r(c["mcap"]),
                         "w": _r((c["mcap"] or 0) / mc_total, 4) if mc_total else None, "price": x.get("price"), "chg_1d": _r(x.get("chg_1d")),
                         "ret_1m": _r(x.get("ret_1m")), "ret_ytd": _r(x.get("ret_ytd")), "ret_1y": _r(x.get("ret_1y")), "pe": _r(x.get("pe_ttm")),
                         "ps": _r(x.get("ps_ttm")), "fcf_yield": _r(x.get("fcf_yield")), "beta": _r(x.get("beta_1y")), "spark": x.get("spark") or [],
                         "q": lk, "rev_growth": _r(m.get("rev_growth")), "gm": _r(m.get("gm")), "om": _r(m.get("om")), "roic": _r(m.get("roic")),
                         "above50": (above.get(t) or [None, None])[0], "above200": (above.get(t) or [None, None])[1]})
        cons.sort(key=lambda r: -(r["mcap"] or 0))
        members_set = set(members)
        if g["kind"] == "industry":
            seg_in = [s for s in segs if s["industry"] == g["name"] and uni[s["tk"]]["industry"] != g["name"]]
        else:
            seg_in = [s for s in segs if s["industry"] and _sector_of_industry(s["industry"]) == g["sector"] and uni[s["tk"]]["sector"] != g["sector"]]
        seg_out = [s for s in segs if s["tk"] in members_set and s["industry"] and (s["industry"] != uni[s["tk"]]["industry"] if g["kind"] == "industry" else _sector_of_industry(s["industry"]) != g["sector"])]
        kp = []
        if g["kind"] == "industry":
            kp = kpis.get(g["name"], [])
        else:
            for name, _ in INDUSTRIES[g["sector"]]:
                for b in kpis.get(name, []):
                    kp.append(dict(b, industry=name))
        fl = [f for f in fil if f["ticker"] in members_set][:80]
        sub = [x for x in groups if x["kind"] == "industry" and x["sector"] == g["sector"]] if g["kind"] == "sector" else [x for x in groups if x["kind"] == "industry" and x["sector"] == g["sector"]]
        doc = {
            "slug": g["slug"], "name": g["name"], "kind": g["kind"], "sector": g["sector"], "sector_slug": SECTOR_SLUG[g["sector"]], "etf": g["etf"],
            "generated": now_iso(), "members": members, "industries": [{"slug": x["slug"], "name": x["name"], "n": len(x["members"])} for x in sub],
            "quarters": quarters, "agg": agg, "metrics": METRICS, "val_now": val_now, "val_range": val_range,
            "perf": {"dates": dates, "spy": [_r(v, 7) for v in spy], **perf}, "returns": rets, "rrg": rot,
            "rrg_peers": None, "macro": sens, "factors": [{k: f[k] for k in ("id", "name", "unit", "source", "url", "retrieved", "first", "last", "n_obs", "latest")} for f in factors],
            "kpis": kp, "constituents": cons, "segments": {"inside": seg_in, "outside": seg_out},
            "insiders": insider_block(members, uni, ins), "filings": fl,
        }
        comp = {"fields": COMP_FIELDS, "quarters": quarters[-COMP_QUARTERS:],
                "data": {t: [[_r(CQ[t][k][f]) if f != "end" and f != "end_py" else CQ[t][k][f] for f in COMP_FIELDS] if k in CQ.get(t, {}) else None for k in quarters[-COMP_QUARTERS:]] for t in members},
                "px": {t: {"url": (pxm.get(t) or {}).get("url"), "at": (pxm.get(t) or {}).get("at"), "splits": (pxm.get(t) or {}).get("splits")} for t in members},
                "mcap_now": {t: _r(uni[t]["mcap"]) for t in members}}
        pending.append(writer.submit(_put, sb, f"sectors/{g['slug']}.json", doc))
        pending.append(writer.submit(_put, sb, f"sectors/{g['slug']}.comp.json", comp))
        done_slugs.add(g["slug"])
        li = _last_idx([a or b for a, b in zip(agg["rev_growth"]["n"], agg["ni_growth"]["n"])])
        head = {k: (agg[k]["agg"][li] if li is not None else None) for k in METRICS}
        lv = {}
        ref = _last_idx([a or b for a, b in zip(agg["rev_growth"]["n"], agg["ni_growth"]["n"])])
        for k in METRICS:
            i = ref
            if i is not None and agg[k]["agg"][i] is None:
                i = next((j for j in range(len(quarters) - 1, -1, -1) if agg[k]["agg"][j] is not None), None)
            lv[k] = (quarters[i], agg[k]["agg"][i]) if i is not None else (None, None)
        overview.append({"slug": g["slug"], "name": g["name"], "kind": g["kind"], "sector": g["sector"], "etf": g["etf"], "n": len(members),
                         "mcap": _r(mc_total), "q": quarters[li] if li is not None else None,
                         "latest": {k: [lv[k][0], lv[k][1], agg[k]["n"][quarters.index(lv[k][0])] if lv[k][0] else 0] for k in METRICS},
                         "val_now": {k: val_now.get(k) for k in ("pe", "ps", "pfcf", "pe_med", "n")}, "pe_range": val_range["pe"],
                         "returns": rets, "rrg": rot, "breadth": {"b50": perf["b50"][-1], "b200": perf["b200"][-1]}, "top5": perf["top5"][-1], "disp": perf["disp"][-1],
                         "macro": [{"id": s["id"], "d": (s.get("d") or {}).get("corr"), "m": (s.get("m") or {}).get("corr"), "mb": (s.get("m") or {}).get("beta"), "db": (s.get("d") or {}).get("beta")} for s in sens]})
        for k in METRICS:
            for i, q in enumerate(quarters):
                if agg[k]["agg"][i] is not None or agg[k]["med"][i] is not None:
                    db_rows.append({"group_id": g["slug"], "metric": k, "quarter": q, "agg": agg[k]["agg"][i], "cw": agg[k]["cw"][i], "med": agg[k]["med"][i],
                                    "n": agg[k]["n"][i], "updated_at": now_iso()})
        if time_budget and time.time() - t0 > time_budget:
            log("time budget reached; remaining groups keep their previous files")
            break
    for f in pending:
        f.result()
    writer.shutdown()
    if len(done_slugs) < len(groups):
        # over budget: groups not rebuilt keep their previous files and overview rows
        prev = {x["slug"]: x for x in ((_get(sb, "sectors.json", {}) or {}).get("groups") or [])}
        overview += [prev[g["slug"]] for g in groups if g["slug"] not in done_slugs and g["slug"] in prev]
        stats["skipped"] = [g["slug"] for g in groups if g["slug"] not in done_slugs]
    sp = {
        "generated": now_iso(), "as_of": dates[-1] if dates else None, "quarters": quarters[-1:], "groups": overview, "metrics": METRICS,
        "mapping": mapping_table(), "overrides": [{"tk": t, "industry": v[0], "why": v[1]} for t, v in OVERRIDES.items()],
        "companies": [{"tk": t, "name": c["name"], "sector": c["sector"], "industry": c["industry"], "sic": c["sic"], "sic_desc": c["sic_desc"], "rule": c["rule"]} for t, c in sorted(uni.items())],
        "segment_rules": [{"pattern": p, "industry": i, "exclude": x} for p, i, x in SEGMENT_RULES], "kpi_specs": [{k: v for k, v in s.items() if k != "match"} | {"match": s.get("match")} for s in KPI_SPECS],
        "etf_rrg": etf_rrg, "spy_returns": spy_ret,
        "factors": [{k: f[k] for k in ("id", "name", "unit", "source", "url", "retrieved", "first", "last", "n_obs", "latest")} for f in factors],
        "method": __doc__,
    }
    _put(sb, "sectors.json", sp, cache="max-age=300")
    stats["db_rows"] = _try_upsert(sb, "sector_metrics", db_rows, "group_id,metric,quarter", log=log)
    stats.update({"groups": len(overview), "quarters": f"{quarters[0]}..{quarters[-1]}", "seconds": round(time.time() - t0, 1)})
    return stats


def _last_idx(ns):
    """Latest quarter with at least half the reporting count of the quarter before it (so an early-season quarter
    with a handful of reports is not taken as the group's latest)."""
    idx = [i for i, n in enumerate(ns) if n]
    if not idx:
        return None
    i = idx[-1]
    if len(idx) > 1 and ns[i] < 0.5 * ns[idx[-2]]:
        return idx[-2]
    return i


def _sector_of_industry(ind):
    for s, rules in INDUSTRIES.items():
        if any(n == ind for n, _ in rules):
            return s
    return None


# ---------------------------------------------------------------- weekly notes
NOTE_PROMPT = """You write short weekly sector notes for a professional equity research site. Use only the facts below.
Sector: {name} ({n} S&P 500 companies). Week of {week}.

Facts (id: description = value):
{facts}

Write 3 to 5 plain sentences (under 120 words) on what the numbers show: growth, margins, valuation against its 10-year range,
relative performance, breadth and the strongest macro link. Rules:
- Every figure must be written as its id in double braces, e.g. {{{{rev_growth}}}}. Never write a digit yourself, not even years or quarter numbers;
  refer to periods with the ids provided (for example {{{{q}}}}).
- No hype, no advice, no predictions, no em dashes. Neutral, terse.
Return JSON: {{"title": "<6 to 10 words, no digits>", "text": "<the note>"}}"""


def note_facts(doc):
    """Facts with ids for a sector note; every value points back to a metric in the sector file."""
    q = doc["quarters"]
    facts = {}

    ref = _last_idx([a or b for a, b in zip(doc["agg"]["rev_growth"]["n"], doc["agg"]["ni_growth"]["n"])])

    def latest(k):
        """Value at the group's reference quarter (most of the group reported), else the latest available."""
        a = doc["agg"][k]["agg"]
        if ref is not None and a[ref] is not None:
            return ref, a[ref]
        for i in range(len(a) - 1, -1, -1):
            if a[i] is not None:
                return i, a[i]
        return None, None

    i, _ = latest("rev_growth")
    if i is None:
        return facts
    facts["q"] = {"label": "Latest calendar quarter", "text": f"Q{q[i][5]} {q[i][:4]}", "ref": {"type": "quarter", "q": q[i]}}
    for k in ("rev_growth", "ni_growth", "pct_rev_up", "om", "fcfm", "roic"):
        j, v = latest(k)
        if v is not None:
            facts[k] = {"label": doc["metrics"][k]["name"] + f" ({q[j]})", "value": v, "unit": doc["metrics"][k]["unit"], "ref": {"type": "agg", "metric": k, "q": q[j]}}
            pv = doc["agg"][k]["agg"][j - 4] if j >= 4 else None
            if pv is not None and k in ("om", "fcfm", "roic"):
                facts[k + "_py"] = {"label": doc["metrics"][k]["name"] + f" a year earlier ({q[j - 4]})", "value": pv, "unit": "pct", "ref": {"type": "agg", "metric": k, "q": q[j - 4]}}
    vn, vr = doc["val_now"], (doc["val_range"] or {}).get("pe")
    if vn.get("pe"):
        facts["pe_now"] = {"label": "Aggregate P/E today", "value": vn["pe"], "unit": "x", "ref": {"type": "val_now", "metric": "pe"}}
    if vr:
        facts["pe_10y_med"] = {"label": "10-year median aggregate P/E", "value": vr["med"], "unit": "x", "ref": {"type": "val_range", "metric": "pe"}}
        if vr.get("pct") is not None:
            facts["pe_pct"] = {"label": "Percentile of today's P/E within its 10-year range", "value": vr["pct"], "unit": "pctile", "ref": {"type": "val_range", "metric": "pe"}}
    r = doc["returns"]
    for w in ("1M", "YTD"):
        if r["cw"].get(w) is not None:
            facts["ret_" + w.lower()] = {"label": f"Cap-weighted return {w}", "value": r["cw"][w], "unit": "pct", "ref": {"type": "ret", "series": "cw", "w": w}}
        if r["spy"].get(w) is not None:
            facts["spy_" + w.lower()] = {"label": f"S&P 500 (SPY) return {w}", "value": r["spy"][w], "unit": "pct", "ref": {"type": "ret", "series": "spy", "w": w}}
    p = doc["perf"]
    if p["b200"] and p["b200"][-1] is not None:
        facts["b200"] = {"label": "Share of members above their 200-day average", "value": p["b200"][-1], "unit": "pct", "ref": {"type": "breadth", "series": "b200"}}
    best = max((s for s in doc["macro"] if (s.get("m") or {}).get("corr") is not None), key=lambda s: abs(s["m"]["corr"]), default=None)
    if best:
        facts["macro_corr"] = {"label": f"Monthly correlation with {best['name']} changes over {best['m']['n']} months", "value": best["m"]["corr"], "unit": "num",
                               "ref": {"type": "macro", "id": best["id"], "w": "m"}}
    return facts


def fmt_fact(f):
    v, u = f.get("value"), f.get("unit")
    if "text" in f:
        return f["text"]
    if u == "pct":
        return f"{v * 100:.1f}%"
    if u == "x":
        return f"{v:.1f}x"
    if u == "num":
        return f"{v:.2f}"
    if u == "pctile":
        n = round(v * 100)
        return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"
    return str(v)


def notes(sb, log=print):
    from .summaries import complete_json
    prev = _get(sb, "sectors/notes.json", {}) or {}
    items = prev.get("notes", [])
    week = (dt.date.today() - dt.timedelta(days=dt.date.today().weekday())).isoformat()
    made = 0
    rows = []
    for sec, slug in SECTOR_SLUG.items():
        if any(n["slug"] == slug and n["week"] == week for n in items):
            continue
        doc = _get(sb, f"sectors/{slug}.json")
        if not doc:
            continue
        facts = note_facts(doc)
        if len(facts) < 4:
            continue
        lines = "\n".join(f"{k}: {f['label']} = {fmt_fact(f)}" for k, f in facts.items())
        note = None
        for attempt in range(3):
            try:
                j = complete_json(NOTE_PROMPT.format(name=sec, n=len(doc["members"]), week=week, facts=lines), max_tokens=8000)
            except Exception as e:
                log(f"  note {slug} failed: {str(e)[:120]}")
                break
            text, title = (j.get("text") or "").strip(), (j.get("title") or "").strip()
            ok, why = validate_note(text, title, facts)
            if ok:
                note = {"slug": slug, "sector": sec, "week": week, "title": title, "text": text, "facts": facts, "generated_at": j.get("generated_at"), "model": j.get("model")}
                break
            log(f"  note {slug} rejected ({why}), retry {attempt + 1}")
        if note:
            items.append(note)
            rows.append({"group_id": slug, "week": week, "content": note, "generated_at": now_iso()})
            made += 1
    items = sorted(items, key=lambda n: (n["week"], n["slug"]), reverse=True)[:11 * 26]
    _put(sb, "sectors/notes.json", {"generated": now_iso(), "notes": items}, cache="max-age=600")
    _try_upsert(sb, "sector_notes", rows, "group_id,week", log=log)
    return {"notes": made, "week": week}


def validate_note(text, title, facts):
    """Numbers only through fact ids: no digits outside {{id}} tokens, every token a known fact, no em dashes."""
    if not text or len(text) > 1200:
        return False, "empty or long"
    toks = re.findall(r"\{\{\s*([a-z0-9_]+)\s*\}\}", text)
    if not toks:
        return False, "no figures"
    unknown = [t for t in toks if t not in facts]
    if unknown:
        return False, "unknown ids " + ",".join(unknown)
    bare = re.sub(r"\{\{\s*[a-z0-9_]+\s*\}\}", "", text)
    if re.search(r"\d", bare) or re.search(r"\d", title):
        return False, "bare digits"
    if "—" in text or "—" in title or "{" in bare or "}" in bare:
        return False, "em dash or stray braces"
    return True, None
