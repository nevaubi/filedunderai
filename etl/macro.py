"""Macro series from the issuing agencies: Treasury yield curve, BLS via FRED, Fed H.15."""
import csv
import datetime as dt
import io
import os

from .common import request, get_json

FRED_SERIES = ["CPIAUCSL", "CPILFESL", "UNRATE", "PAYEMS", "DFF", "T10Y2Y"]
CURVE_HDR = ["1 Mo", "2 Mo", "3 Mo", "6 Mo", "1 Yr", "2 Yr", "3 Yr", "5 Yr", "7 Yr", "10 Yr", "20 Yr", "30 Yr"]


def treasury_curve(years=None):
    years = years or [dt.date.today().year - 2, dt.date.today().year - 1, dt.date.today().year]
    rows = []
    for yr in years:
        url = f"https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/{yr}/all?type=daily_treasury_yield_curve&field_tdr_date_value={yr}&page&_format=csv"
        text = request(url, timeout=60).replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")
        for r in csv.DictReader(io.StringIO(text, newline="")):
            d = r.get("Date")
            if not d:
                continue
            m, dd, y = d.split("/")
            vals = []
            for h in CURVE_HDR:
                v = r.get(h)
                vals.append(float(v) if v not in (None, "", "N/A") else None)
            rows.append([f"{y}-{m}-{dd}"] + vals)
    return sorted(rows)


def _fred_csv(sid, start):
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}&cosd={start}"
    try:
        return request(url, timeout=40, retries=2)
    except Exception:
        key = os.environ.get("FIRECRAWL_API_KEY")
        if not key:
            raise
        import json
        d = request("https://api.firecrawl.dev/v2/scrape", method="POST", data={"url": url, "formats": ["rawHtml"], "timeout": 60000},
                    headers={"Authorization": f"Bearer {key}"}, timeout=120)
        return json.loads(d)["data"]["rawHtml"]


def fred(series_ids=FRED_SERIES, start="2015-01-01"):
    """FRED graph CSV, one series per request (no key). Falls back to Firecrawl when FRED is not reachable directly."""
    out = {}
    for sid in series_ids:
        pts = []
        try:
            text = _fred_csv(sid, start)
            for r in csv.DictReader(io.StringIO(text.replace("\r\n", "\n").strip(), newline="")):
                day = r.get("observation_date") or r.get("DATE")
                v = r.get(sid)
                if day and day >= start and v not in (None, "", "."):
                    pts.append([day, float(v)])
        except Exception as e:
            print("fred failed", sid, str(e)[:160])
        out[sid] = pts
    return out


def effr_monthly():
    j = get_json("https://api.db.nomics.world/v22/series/FED/H15/RIFSPFF_N.M?observations=1", timeout=40)
    s = j["series"]["docs"][0]
    return [[p, v] for p, v in zip(s["period"], s["value"]) if v not in (None, "NA") and p >= "2015"]
