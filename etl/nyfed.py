"""Federal Reserve Bank of New York Markets Data API (https://markets.newyorkfed.org/static/docs/markets-api.html).

Reference rates (EFFR, OBFR, SOFR, TGCR, BGCR, SOFR averages), repo and reverse repo operation results, SOMA holdings
summary and primary dealer statistics. When the API host is not reachable from the machine running the job, the same
JSON URLs are fetched through Firecrawl and parsed identically.
"""
import datetime as dt
import json
import os
import re

from .common import request

API = "https://markets.newyorkfed.org"
RATES_PAGE = "https://www.newyorkfed.org/markets/reference-rates/"
PROVIDER = "Federal Reserve Bank of New York"


def get(path):
    url = API + path
    try:
        return json.loads(request(url, timeout=90, retries=3))
    except Exception as e:
        key = os.environ.get("FIRECRAWL_API_KEY")
        if not key:
            raise
        print("nyfed direct fetch failed, using Firecrawl:", str(e)[:120])
        d = json.loads(request("https://api.firecrawl.dev/v2/scrape", method="POST", data={"url": url, "formats": ["rawHtml"], "timeout": 90000},
                               headers={"Authorization": f"Bearer {key}"}, timeout=150))
        return parse_firecrawl(d["data"].get("rawHtml") or "")


def parse_firecrawl(raw):
    """Firecrawl returns the JSON body as text, sometimes wrapped in the browser's <pre> viewer."""
    import html as _html
    raw = raw.strip()
    if raw.startswith("<"):
        m = re.search(r"<pre[^>]*>(.*?)</pre>", raw, re.S)
        raw = _html.unescape(m.group(1) if m else re.sub(r"<[^>]+>", "", raw)).strip()
    return json.loads(raw)


def _windows(start, end, months):
    a = dt.date.fromisoformat(start)
    e = dt.date.fromisoformat(end)
    while a <= e:
        y, m = a.year + (a.month - 1 + months) // 12, (a.month - 1 + months) % 12 + 1
        b = min(dt.date(y, m, 1) - dt.timedelta(days=1), e)
        yield a.isoformat(), b.isoformat()
        a = b + dt.timedelta(days=1)


RATE_COLS = ["effectiveDate", "type", "percentRate", "percentPercentile1", "percentPercentile25", "percentPercentile75",
             "percentPercentile99", "volumeInBillions", "average30day", "average90day", "index", "revisionIndicator"]
RATES = {
    # id suffix: (type, field, title, page, extra)
    "EFFR": ("EFFR", "percentRate", "Effective federal funds rate (EFFR)", "effr", {"group": "overnight", "short": "EFFR"}),
    "OBFR": ("OBFR", "percentRate", "Overnight bank funding rate (OBFR)", "obfr", {"group": "overnight", "short": "OBFR"}),
    "SOFR": ("SOFR", "percentRate", "Secured overnight financing rate (SOFR)", "sofr", {"group": "overnight", "short": "SOFR"}),
    "TGCR": ("TGCR", "percentRate", "Tri-party general collateral rate (TGCR)", "tgcr", {"group": "overnight2", "short": "TGCR"}),
    "BGCR": ("BGCR", "percentRate", "Broad general collateral rate (BGCR)", "bgcr", {"group": "overnight2", "short": "BGCR"}),
    "SOFR_P99": ("SOFR", "percentPercentile99", "SOFR, 99th percentile of transactions", "sofr", {"group": "overnight2", "short": "SOFR 99th pct"}),
    "SOFR_VOL": ("SOFR", "volumeInBillions", "SOFR underlying volume", "sofr", {"group": "vol", "units": "Billions of dollars", "short_units": "bn", "dp": 0, "short": "SOFR volume"}),
    "EFFR_VOL": ("EFFR", "volumeInBillions", "EFFR underlying volume", "effr", {"group": "vol", "units": "Billions of dollars", "short_units": "bn", "dp": 0, "short": "EFFR volume"}),
    "SOFR30": ("SOFRAI", "average30day", "SOFR 30-day average", "sofr-averages-and-index", {"group": "overnight2", "short": "SOFR 30-day avg"}),
}


def rates(start, end):
    recs = []
    for a, b in _windows(start, end, 12):
        recs += get(f"/api/rates/all/search.json?startDate={a}&endDate={b}").get("refRates", [])
    out = []
    for sid, (typ, field, title, page, extra) in RATES.items():
        rows = sorted((r for r in recs if r.get("type") == typ), key=lambda r: r["effectiveDate"])
        obs = [(r["effectiveDate"], r[field]) for r in rows if r.get(field) not in (None, "")]
        if not obs:
            continue
        meta = {"id": f"NYFED:{sid}", "source": "Reference rates", "provider": PROVIDER, "title": title,
                "units": extra.get("units", "Percent"), "frequency": "D", "seasonal_adj": None, "geography": "United States",
                "source_url": RATES_PAGE + page, "data_url": f"{API}/api/rates/all/search.json?startDate={start}&endDate={end}",
                "source_key": f"{typ}.{field}", "section": "rates", "release": "NYFED:RATES",
                "notes": "Published each business day for the previous business day. revisionIndicator marks revised values."}
        meta.update({k: v for k, v in extra.items() if k != "units"})
        snap = [[r.get(c, "") if r.get(c) is not None else "" for c in RATE_COLS] for r in rows]
        out.append({"meta": meta, "obs": obs, "snap": {"url": meta["data_url"], "series": f"type={typ}, field={field}", "cols": RATE_COLS,
                                                       "rows": snap, "keys": [r["effectiveDate"] for r in rows], "hl": RATE_COLS.index(field)}})
    return out


OP_COLS = ["operationDate", "operationId", "operationType", "operationMethod", "term", "termCalenderDays", "totalAmtSubmitted", "totalAmtAccepted"]


def operations(start, end):
    ops = []
    for a, b in _windows(start, end, 6):
        ops += (get(f"/api/rp/results/search.json?startDate={a}&endDate={b}").get("repo") or {}).get("operations", [])
    out = []
    for typ, sid, title, short, page in [("Reverse Repo", "RRP", "Reverse repo operations accepted (ON RRP and term)", "Fed reverse repo take-up", "reverse-repo"),
                                         ("Repo", "REPO", "Repo operations accepted (standing repo facility and term)", "Fed repo operations", "repo")]:
        rows = sorted((o for o in ops if o.get("operationType") == typ and o.get("auctionStatus") == "Results"), key=lambda o: (o["operationDate"], o["operationId"]))
        daily = {}
        for o in rows:
            daily[o["operationDate"]] = daily.get(o["operationDate"], 0) + float(o.get("totalAmtAccepted") or 0)
        if not daily:
            continue
        meta = {"id": f"NYFED:{sid}", "source": "Repo and reverse repo operations", "provider": PROVIDER, "title": title,
                "units": "Dollars", "frequency": "D", "seasonal_adj": None, "geography": "United States",
                "source_url": f"https://www.newyorkfed.org/markets/desk-operations/{page}",
                "data_url": f"{API}/api/rp/results/search.json?startDate={start}&endDate={end}", "source_key": f"operationType={typ}",
                "section": "rates", "release": "NYFED:RP", "group": "ops", "short": short, "scale": 1e-9, "dunits": "Billions of dollars",
                "short_units": "bn", "dp": 1, "derived": "Sum of totalAmtAccepted across the day's operations of this type.",
                "notes": "Daily total of accepted amounts across all operations of this type on the operation date."}
        snap = [[str(o.get(c, "")) for c in OP_COLS] for o in rows]
        out.append({"meta": meta, "obs": [(d, repr(v)) for d, v in sorted(daily.items())],
                    "snap": {"url": meta["data_url"], "series": f"operationType={typ}", "cols": OP_COLS, "rows": snap,
                             "keys": [o["operationDate"] for o in rows], "hl": OP_COLS.index("totalAmtAccepted")}})
    return out


SOMA_COLS = ["asOfDate", "bills", "notesbonds", "frn", "tips", "tipsInflationCompensation", "agencies", "mbs", "cmbs", "total"]
SOMA = {"total": "SOMA holdings, total", "bills": "SOMA holdings, Treasury bills", "notesbonds": "SOMA holdings, Treasury notes and bonds", "mbs": "SOMA holdings, agency MBS"}


def soma():
    rows = sorted(get("/api/soma/summary.json")["soma"]["summary"], key=lambda r: r["asOfDate"])
    out = []
    for f, title in SOMA.items():
        meta = {"id": f"NYFED:SOMA_{f.upper()}", "source": "System Open Market Account holdings", "provider": PROVIDER, "title": title,
                "units": "Dollars", "frequency": "W", "seasonal_adj": None, "geography": "United States",
                "source_url": "https://www.newyorkfed.org/markets/soma-holdings", "data_url": f"{API}/api/soma/summary.json",
                "source_key": f"summary.{f}", "section": "rates", "release": "NYFED:SOMA", "group": "soma", "scale": 1e-9,
                "dunits": "Billions of dollars", "short_units": "bn", "dp": 0, "short": title.replace("SOMA holdings, ", "SOMA ")}
        out.append({"meta": meta, "obs": [(r["asOfDate"], r[f]) for r in rows if r.get(f) not in (None, "")],
                    "snap": {"url": meta["data_url"], "series": f, "cols": SOMA_COLS, "rows": [[r.get(c, "") for c in SOMA_COLS] for r in rows],
                             "keys": [r["asOfDate"] for r in rows], "hl": SOMA_COLS.index(f)}})
    return out


PD = {
    "PDPOSGST-TOT": ("Primary dealer net positions, Treasury securities excluding TIPS", "Dealer net Treasury positions"),
    "PDPOSCS-TOT": ("Primary dealer net positions, corporate securities", "Dealer net corporate positions"),
    "PDPOSMBS-TOT": ("Primary dealer net positions, agency and GSE MBS", "Dealer net MBS positions"),
    "PDSIRRA-UTSETTOT": ("Primary dealer securities in, reverse repo, Treasury excluding TIPS", "Dealer reverse repo, Treasuries"),
    "PDSORA-UTSETTOT": ("Primary dealer securities out, repo, Treasury excluding TIPS", "Dealer repo, Treasuries"),
}


def dealers():
    out = []
    for key, (title, short) in PD.items():
        rows = sorted(get(f"/api/pd/get/{key}.json")["pd"]["timeseries"], key=lambda r: r["asofdate"])
        meta = {"id": f"NYFED:PD:{key}", "source": "Primary dealer statistics", "provider": PROVIDER, "title": title,
                "units": "Millions of dollars", "frequency": "W", "seasonal_adj": None, "geography": "United States",
                "source_url": "https://www.newyorkfed.org/markets/counterparties/primary-dealers-statistics",
                "data_url": f"{API}/api/pd/get/{key}.json", "source_key": key, "section": "positioning", "release": "NYFED:PD",
                "group": "pdpos" if "POS" in key else "pdfin", "scale": 1e-3, "dunits": "Billions of dollars", "short_units": "bn", "dp": 1, "short": short,
                "notes": "Weekly FR 2004 reports, values as of Wednesday. Series spanning reporting-form changes follow the API's series break."}
        out.append({"meta": meta, "obs": [(r["asofdate"], r["value"]) for r in rows if r.get("value") not in (None, "", "*")],
                    "snap": {"url": meta["data_url"], "series": key, "cols": ["asofdate", "keyid", "value"],
                             "rows": [[r["asofdate"], r["keyid"], r["value"]] for r in rows], "keys": [r["asofdate"] for r in rows], "hl": 2}})
    return out
