"""U.S. Treasury daily interest rate data (home.treasury.gov): par yield curve, bill rates and real yield curve, plus three
spreads computed from them (10Y minus 2Y, 10Y minus 3M, 10Y nominal minus 10Y real)."""
import csv
import datetime as dt
import io

from .common import request

CSV = "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/{y}/all?type={t}&field_tdr_date_value={y}&page&_format=csv"
PAGE = "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/TextView?type={t}&field_tdr_date_value={y}"
PROVIDER = "U.S. Department of the Treasury"

TABLES = {
    "daily_treasury_yield_curve": ("PAR", "Daily Treasury par yield curve rates", {
        "1 Mo": ("1-month", "billsc"), "3 Mo": ("3-month", "short"), "6 Mo": ("6-month", "short"), "1 Yr": ("1-year", "short"),
        "2 Yr": ("2-year", "curve"), "5 Yr": ("5-year", "curve"), "10 Yr": ("10-year", "curve"), "30 Yr": ("30-year", "curve")}),
    "daily_treasury_bill_rates": ("BILL", "Daily Treasury bill rates", {
        "4 WEEKS COUPON EQUIVALENT": ("4-week bill, coupon equivalent", "bills"), "13 WEEKS COUPON EQUIVALENT": ("13-week bill, coupon equivalent", "bills"),
        "26 WEEKS COUPON EQUIVALENT": ("26-week bill, coupon equivalent", "bills"), "52 WEEKS COUPON EQUIVALENT": ("52-week bill, coupon equivalent", "bills")}),
    "daily_treasury_real_yield_curve": ("REAL", "Daily Treasury par real yield curve rates", {
        "5 YR": ("5-year real", "real"), "10 YR": ("10-year real", "real"), "30 YR": ("30-year real", "real")}),
}


def _iso(d):
    m, dd, y = d.split("/")
    return f"{y}-{m}-{dd}"


def table(t, years):
    rows = []
    for y in years:
        text = request(CSV.format(y=y, t=t), timeout=90).replace("\r\n", "\n").replace("\r", "\n").lstrip("﻿")
        rows += list(csv.DictReader(io.StringIO(text, newline="")))
    return sorted((r for r in rows if r.get("Date")), key=lambda r: _iso(r["Date"]))


def _sid(prefix, col):
    return "TSY:" + prefix + ":" + col.replace(" COUPON EQUIVALENT", "").replace(" ", "")


def collect(years, log=print):
    out = []
    tabs = {}
    last_year = max(years)
    for t, (prefix, name, cols) in TABLES.items():
        rows = table(t, years)
        tabs[t] = rows
        log(f"treasury {t}: {len(rows)} rows")
        for col, (label, group) in cols.items():
            snap = [[r["Date"], r.get(col, "")] for r in rows]
            obs = [(_iso(r["Date"]), r[col]) for r in rows if r.get(col) not in (None, "", "N/A")]
            if not obs:
                continue
            meta = {"id": _sid(prefix, col), "source": name, "provider": PROVIDER, "title": f"Treasury {label} yield" if prefix != "BILL" else f"Treasury {label} rate",
                    "units": "Percent", "frequency": "D", "seasonal_adj": None, "geography": "United States",
                    "source_url": PAGE.format(t=t, y=last_year), "data_url": CSV.format(y=last_year, t=t), "source_key": f"{t} column \"{col}\"",
                    "section": "rates", "release": "TSY:RATES", "group": "tsy_" + group, "short": f"{label.split(',')[0]} Treasury" if prefix != "REAL" else f"{label} yield",
                    "notes": "Published each business day around 6 p.m. Eastern. The CSV for each calendar year is a separate file; the link opens the latest year."}
            out.append({"meta": meta, "obs": obs, "snap": {"url": meta["data_url"], "series": col, "cols": ["Date", col], "rows": snap,
                                                           "keys": [_iso(r["Date"]) for r in rows], "hl": 1}})
    par = {_iso(r["Date"]): r for r in tabs["daily_treasury_yield_curve"]}
    real = {_iso(r["Date"]): r for r in tabs["daily_treasury_real_yield_curve"]}

    def num(r, c):
        try:
            return float(r.get(c))
        except (TypeError, ValueError):
            return None

    for sid, title, a_tab, a, b_tab, b, short in [
        ("TSY:SPREAD:2S10S", "Treasury 10-year minus 2-year par yield", par, "10 Yr", par, "2 Yr", "10Y minus 2Y"),
        ("TSY:SPREAD:3M10Y", "Treasury 10-year minus 3-month par yield", par, "10 Yr", par, "3 Mo", "10Y minus 3M"),
        ("TSY:SPREAD:BE10", "10-year breakeven inflation (nominal minus real par yield)", par, "10 Yr", real, "10 YR", "10Y breakeven"),
    ]:
        obs, snap, keys = [], [], []
        for d in sorted(set(a_tab) & set(b_tab)):
            x, y = num(a_tab[d], a), num(b_tab[d], b)
            snap.append([a_tab[d]["Date"], a_tab[d].get(a, ""), b_tab[d].get(b, "")])
            keys.append(d)
            if x is not None and y is not None:
                obs.append((d, f"{x - y:.2f}"))
        meta = {"id": sid, "source": "Daily Treasury par yield curve rates" + (" and par real yield curve rates" if b_tab is real else ""), "provider": PROVIDER,
                "title": title, "units": "Percentage points", "frequency": "D", "seasonal_adj": None, "geography": "United States",
                "source_url": PAGE.format(t="daily_treasury_yield_curve", y=last_year), "data_url": CSV.format(y=last_year, t="daily_treasury_yield_curve"),
                "source_key": f"\"{a}\" minus \"{b}\"", "section": "rates", "release": "TSY:RATES", "group": "tsy_spread", "short": short,
                "derived": f"{a} minus {b}", "inputs": [_sid("PAR", a), _sid("PAR" if b_tab is par else "REAL", b)], "dp": 2}
        out.append({"meta": meta, "obs": obs, "snap": {"url": meta["data_url"], "series": f"{a} - {b}", "cols": ["Date", a, b], "rows": snap, "keys": keys, "hl": [1, 2]}})
    return out


def years_since(y0):
    return list(range(y0, dt.date.today().year + 1))
