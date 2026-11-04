"""Bureau of Labor Statistics Public Data API v2 (https://www.bls.gov/developers/api_signature_v2.htm).

CPI components, payrolls by sector, household survey rates, JOLTS and the Employment Cost Index. Uses BLS_API_KEY when set
(50 series and 20 years per request); without it the API allows 25 series and 10 years per request and a small daily
quota per IP. When the quota is used up the same API request is fetched through Firecrawl as single-series GET calls.
"""
import calendar
import datetime as dt
import json
import os
import time

from .common import request

API = "https://api.bls.gov/publicAPI/v2/timeseries/data/"
VIEW = "https://data.bls.gov/timeseries/"
YOY = {"display": "yoy", "dunits": "Percent, year over year", "dp": 1}
MOM = {"display": "mom", "dunits": "Percent, month over month", "dp": 2}
CHG = {"display": "chg", "dunits": "Thousands of jobs, monthly change", "short_units": "k", "dp": 0}

# id: (title, section, group, release, units, seasonal, extra)
SERIES = {
    "CUUR0000SA0": ("CPI-U, all items, year over year", "inflation", "cpi", "BLS:CPI", "Index 1982-84=100", "NSA", dict(YOY, short="CPI, y/y")),
    "CUUR0000SA0L1E": ("CPI-U, all items less food and energy, year over year", "inflation", "cpi", "BLS:CPI", "Index 1982-84=100", "NSA", dict(YOY, short="Core CPI, y/y")),
    "CUSR0000SA0": ("CPI-U, all items, month over month", "inflation", "cpim", "BLS:CPI", "Index 1982-84=100", "SA", dict(MOM, short="CPI, m/m")),
    "CUSR0000SA0L1E": ("CPI-U, all items less food and energy, month over month", "inflation", "cpim", "BLS:CPI", "Index 1982-84=100", "SA", dict(MOM, short="Core CPI, m/m")),
    "CUUR0000SAF1": ("CPI-U, food, year over year", "inflation", "cpic", "BLS:CPI", "Index 1982-84=100", "NSA", dict(YOY, short="Food")),
    "CUUR0000SA0E": ("CPI-U, energy, year over year", "inflation", "cpic", "BLS:CPI", "Index 1982-84=100", "NSA", dict(YOY, short="Energy")),
    "CUUR0000SAH1": ("CPI-U, shelter, year over year", "inflation", "cpic", "BLS:CPI", "Index 1982-84=100", "NSA", dict(YOY, short="Shelter")),
    "CUUR0000SACL1E": ("CPI-U, core goods (commodities less food and energy), year over year", "inflation", "cpic", "BLS:CPI", "Index 1982-84=100", "NSA", dict(YOY, short="Core goods")),
    "CUUR0000SASLE": ("CPI-U, services less energy services, year over year", "inflation", "cpic", "BLS:CPI", "Index 1982-84=100", "NSA", dict(YOY, short="Core services")),
    "CUUR0000SAM": ("CPI-U, medical care, year over year", "inflation", "cpic2", "BLS:CPI", "Index 1982-84=100", "NSA", dict(YOY, short="Medical care")),
    "CUUR0000SETA02": ("CPI-U, used cars and trucks, year over year", "inflation", "cpic2", "BLS:CPI", "Index 1982-84=100", "NSA", dict(YOY, short="Used cars and trucks")),
    "CUUR0000SAS4": ("CPI-U, transportation services, year over year", "inflation", "cpic2", "BLS:CPI", "Index 1982-84=100", "NSA", dict(YOY, short="Transportation services")),
    "CES0000000001": ("Nonfarm payrolls, monthly change", "labor", "nfp", "BLS:EMPSIT", "Thousands of employees", "SA", dict(CHG, short="Nonfarm payrolls")),
    "CES0500000001": ("Private payrolls, monthly change", "labor", "nfp", "BLS:EMPSIT", "Thousands of employees", "SA", dict(CHG, short="Private payrolls")),
    "CES0600000001": ("Goods-producing payrolls, monthly change", "labor", "sector", "BLS:EMPSIT", "Thousands of employees", "SA", dict(CHG, short="Goods-producing")),
    "CES2000000001": ("Construction payrolls, monthly change", "labor", "sector", "BLS:EMPSIT", "Thousands of employees", "SA", dict(CHG, short="Construction")),
    "CES3000000001": ("Manufacturing payrolls, monthly change", "labor", "sector", "BLS:EMPSIT", "Thousands of employees", "SA", dict(CHG, short="Manufacturing")),
    "CES4000000001": ("Trade, transportation and utilities payrolls, monthly change", "labor", "sector", "BLS:EMPSIT", "Thousands of employees", "SA", dict(CHG, short="Trade, transportation, utilities")),
    "CES5000000001": ("Information payrolls, monthly change", "labor", "sector", "BLS:EMPSIT", "Thousands of employees", "SA", dict(CHG, short="Information")),
    "CES5500000001": ("Financial activities payrolls, monthly change", "labor", "sector", "BLS:EMPSIT", "Thousands of employees", "SA", dict(CHG, short="Financial activities")),
    "CES6000000001": ("Professional and business services payrolls, monthly change", "labor", "sector", "BLS:EMPSIT", "Thousands of employees", "SA", dict(CHG, short="Professional and business services")),
    "CES6500000001": ("Private education and health services payrolls, monthly change", "labor", "sector", "BLS:EMPSIT", "Thousands of employees", "SA", dict(CHG, short="Education and health")),
    "CES7000000001": ("Leisure and hospitality payrolls, monthly change", "labor", "sector", "BLS:EMPSIT", "Thousands of employees", "SA", dict(CHG, short="Leisure and hospitality")),
    "CES8000000001": ("Other services payrolls, monthly change", "labor", "sector", "BLS:EMPSIT", "Thousands of employees", "SA", dict(CHG, short="Other services")),
    "CES9000000001": ("Government payrolls, monthly change", "labor", "sector", "BLS:EMPSIT", "Thousands of employees", "SA", dict(CHG, short="Government")),
    "CES0500000003": ("Average hourly earnings, total private, year over year", "labor", "wages", "BLS:EMPSIT", "Dollars per hour", "SA", dict(YOY, short="Average hourly earnings, y/y")),
    "LNS14000000": ("Unemployment rate", "labor", "unrate", "BLS:EMPSIT", "Percent", "SA", {"dp": 1}),
    "LNS13327709": ("U-6 unemployment rate", "labor", "unrate", "BLS:EMPSIT", "Percent", "SA", {"dp": 1, "short": "U-6 rate"}),
    "LNS11300000": ("Labor force participation rate", "labor", "part", "BLS:EMPSIT", "Percent", "SA", {"dp": 1, "short": "Participation rate"}),
    "LNS12300060": ("Employment-population ratio, ages 25-54", "labor", "part", "BLS:EMPSIT", "Percent", "SA", {"dp": 1, "short": "Prime-age employment ratio"}),
    "JTS000000000000000JOL": ("Job openings, total nonfarm", "labor", "jolts_l", "BLS:JOLTS", "Thousands", "SA", {"dp": 0, "short_units": "k", "short": "Job openings"}),
    "JTS000000000000000JOR": ("Job openings rate, total nonfarm", "labor", "jolts", "BLS:JOLTS", "Percent", "SA", {"dp": 1, "short": "Openings rate"}),
    "JTS000000000000000HIR": ("Hires rate, total nonfarm", "labor", "jolts", "BLS:JOLTS", "Percent", "SA", {"dp": 1, "short": "Hires rate"}),
    "JTS000000000000000QUR": ("Quits rate, total nonfarm", "labor", "jolts", "BLS:JOLTS", "Percent", "SA", {"dp": 1, "short": "Quits rate"}),
    "JTS000000000000000LDR": ("Layoffs and discharges rate, total nonfarm", "labor", "jolts", "BLS:JOLTS", "Percent", "SA", {"dp": 1, "short": "Layoffs rate"}),
    "CIU1010000000000A": ("Employment Cost Index, total compensation, civilian workers, 12-month change", "labor", "eci", "BLS:ECI", "Percent, 12-month change", "NSA", {"dp": 1, "short": "ECI, 12-month"}),
    "CIS1010000000000Q": ("Employment Cost Index, total compensation, civilian workers, 3-month change", "labor", "eci", "BLS:ECI", "Percent, 3-month change", "SA", {"dp": 1, "short": "ECI, 3-month"}),
    "CIU2020000000000A": ("Employment Cost Index, wages and salaries, private industry, 12-month change", "labor", "eci", "BLS:ECI", "Percent, 12-month change", "NSA", {"dp": 1, "short": "ECI private wages, 12-month"}),
}
RELEASE_NAMES = {"BLS:CPI": "Consumer Price Index", "BLS:EMPSIT": "Employment Situation", "BLS:JOLTS": "Job Openings and Labor Turnover Survey", "BLS:ECI": "Employment Cost Index"}
RELEASE_PAGES = {"BLS:CPI": "https://www.bls.gov/cpi/", "BLS:EMPSIT": "https://www.bls.gov/ces/", "BLS:JOLTS": "https://www.bls.gov/jlt/", "BLS:ECI": "https://www.bls.gov/eci/"}


class QuotaError(RuntimeError):
    pass


def _post(ids, y0, y1, key):
    body = {"seriesid": ids, "startyear": str(y0), "endyear": str(y1)}
    if key:
        body["registrationkey"] = key
    j = json.loads(request(API, method="POST", data=body, timeout=120, retries=3))
    if j.get("status") != "REQUEST_SUCCEEDED":
        msg = " ".join(j.get("message") or [])
        if "threshold" in msg.lower() or "daily" in msg.lower():
            raise QuotaError(msg)
        raise RuntimeError(f"BLS API: {j.get('status')} {msg[:300]}")
    return {s["seriesID"]: s.get("data", []) for s in j["Results"]["series"]}, j.get("message") or []


def _firecrawl_get(sid, y0, y1):
    """The API's GET form for one series, fetched through Firecrawl (another IP, so another daily quota)."""
    key = os.environ.get("FIRECRAWL_API_KEY")
    if not key:
        raise QuotaError("no Firecrawl key for the BLS fallback")
    url = f"{API}{sid}?startyear={y0}&endyear={y1}"
    d = json.loads(request("https://api.firecrawl.dev/v2/scrape", method="POST", data={"url": url, "formats": ["rawHtml"], "timeout": 90000},
                           headers={"Authorization": f"Bearer {key}"}, timeout=150))
    from .nyfed import parse_firecrawl
    j = parse_firecrawl(d["data"].get("rawHtml") or "")
    if j.get("status") != "REQUEST_SUCCEEDED":
        raise QuotaError(" ".join(j.get("message") or [])[:200])
    return j["Results"]["series"][0].get("data", [])


def fetch(ids, start_year, end_year=None, log=print):
    key = os.environ.get("BLS_API_KEY")
    end_year = end_year or dt.date.today().year
    per, span = (50, 20) if key else (25, 10)
    data = {i: [] for i in ids}
    windows = []
    y = start_year
    while y <= end_year:
        windows.append((y, min(end_year, y + span - 1)))
        y += span
    fallback = False
    for k in range(0, len(ids), per):
        batch = ids[k:k + per]
        for y0, y1 in windows:
            if not fallback:
                try:
                    got, msgs = _post(batch, y0, y1, key)
                    for sid, rows in got.items():
                        data[sid] += rows
                    for m in msgs:
                        if "does not exist" in m or "invalid" in m.lower():
                            log("bls:", m)
                    continue
                except QuotaError as e:
                    log("bls quota reached, switching to Firecrawl GET:", str(e)[:120])
                    fallback = True
            for sid in batch:
                data[sid] += _firecrawl_get(sid, y0, y1)
                time.sleep(0.5)
    return data


def _date(year, period):
    if period.startswith("M") and period != "M13":
        m = int(period[1:])
        return f"{year}-{m:02d}-{calendar.monthrange(int(year), m)[1]:02d}"
    if period.startswith("Q") and period != "Q05":
        return f"{year}-" + {"01": "03-31", "02": "06-30", "03": "09-30", "04": "12-31"}[period[1:]]
    return None


COLS = ["year", "period", "periodName", "value", "latest", "footnotes"]


def collect(start_year=2007, end_year=None, ids=None, log=print):
    ids = ids or list(SERIES)
    data = fetch(ids, start_year, end_year, log=log)
    out = []
    for sid in ids:
        rows = sorted(data.get(sid) or [], key=lambda r: (r["year"], r["period"]))
        seen, uniq = set(), []
        for r in rows:
            k = (r["year"], r["period"])
            if k not in seen:
                seen.add(k)
                uniq.append(r)
        if not uniq:
            log("bls: no data for", sid)
            continue
        title, section, group, rel, units, sa, extra = SERIES[sid]
        freq = "Q" if uniq[-1]["period"].startswith("Q") else "M"
        meta = {"id": f"BLS:{sid}", "source": RELEASE_NAMES[rel], "provider": "Bureau of Labor Statistics", "title": title, "units": units,
                "frequency": freq, "seasonal_adj": sa, "geography": "United States", "source_url": VIEW + sid, "data_url": API + sid,
                "source_key": sid, "section": section, "release": rel, "group": group, "release_page": RELEASE_PAGES[rel]}
        meta.update(extra)
        snap = [[r["year"], r["period"], r.get("periodName", ""), r["value"], r.get("latest", ""), "; ".join(f.get("text", "") for f in r.get("footnotes") or [] if f)] for r in uniq]
        obs = [(_date(r["year"], r["period"]), r["value"]) for r in uniq if _date(r["year"], r["period"]) and r["value"] not in ("-", "")]
        out.append({"meta": meta, "obs": obs, "snap": {"url": API + sid, "series": sid, "cols": COLS, "rows": snap,
                                                       "keys": [_date(r["year"], r["period"]) for r in uniq], "hl": 3}})
    return out
