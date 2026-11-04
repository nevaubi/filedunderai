"""OECD Data Explorer SDMX REST API (https://sdmx.oecd.org/public/rest/).

Dataflow ids were taken from the API's own dataflow list (/public/rest/dataflow/all), and the dimension filters below from
the dimension values the flows return. Each indicator is one request covering all areas; rows are matched on exact
dimension values so a flow change shows up as missing series rather than wrong ones.
"""
import csv
import io
import time

from .common import request

API = "https://sdmx.oecd.org/public/rest/data/"
EXPLORER = "https://data-explorer.oecd.org/vis?df[ds]=dsDisseminateFinalDMZ&df[id]={id}&df[ag]={ag}"
AREAS = {
    "USA": "United States", "CAN": "Canada", "MEX": "Mexico", "GBR": "United Kingdom", "DEU": "Germany", "FRA": "France",
    "ITA": "Italy", "JPN": "Japan", "KOR": "Korea", "AUS": "Australia", "CHN": "China", "IND": "India", "IDN": "Indonesia",
    "BRA": "Brazil", "ARG": "Argentina", "ZAF": "South Africa", "SAU": "Saudi Arabia", "TUR": "Türkiye",
    "EA20": "Euro area (20)", "EA": "Euro area", "EU27_2020": "European Union (27)", "EU": "European Union", "G20": "G20", "G7": "G7", "OECD": "OECD total",
}

STES = ["REF_AREA", "FREQ", "MEASURE", "UNIT_MEASURE", "ACTIVITY", "ADJUSTMENT", "TRANSFORMATION", "TIME_HORIZ", "METHODOLOGY"]
G20P = ["REF_AREA", "FREQ", "METHODOLOGY", "MEASURE", "UNIT_MEASURE", "EXPENDITURE", "ADJUSTMENT", "TRANSFORMATION"]
NAMAIN = ["FREQ", "ADJUSTMENT", "REF_AREA", "SECTOR", "COUNTERPART_SECTOR", "TRANSACTION", "INSTR_ASSET", "ACTIVITY", "EXPENDITURE",
          "UNIT_MEASURE", "PRICE_BASE", "TRANSFORMATION", "TABLE_IDENTIFIER"]
LFS = ["REF_AREA", "MEASURE", "UNIT_MEASURE", "TRANSFORMATION", "ADJUSTMENT", "SEX", "AGE", "ACTIVITY", "FREQ"]

# indicator: (agency, flow, version, dimension order (from the DSD), fixed key values, title, units, extra)
INDICATORS = {
    "CLI": ("OECD.SDD.STES", "DSD_STES@DF_CLI", "4.1", STES, {"FREQ": "M", "MEASURE": "LI", "ADJUSTMENT": "AA", "METHODOLOGY": "H"},
            "Composite leading indicator, amplitude adjusted", "Index, long-term average = 100", {"dp": 2}),
    "BCI": ("OECD.SDD.STES", "DSD_STES@DF_CLI", "4.1", STES, {"FREQ": "M", "MEASURE": "BCICP", "ADJUSTMENT": "AA"},
            "Business confidence, composite indicator", "Index, long-term average = 100", {"dp": 2}),
    "CCI": ("OECD.SDD.STES", "DSD_STES@DF_CLI", "4.1", STES, {"FREQ": "M", "MEASURE": "CCICP", "ADJUSTMENT": "AA"},
            "Consumer confidence, composite indicator", "Index, long-term average = 100", {"dp": 2}),
    "CPI": ("OECD.SDD.TPS", "DSD_G20_PRICES@DF_G20_PRICES", "1.0", G20P, {"FREQ": "M", "MEASURE": "CPI", "EXPENDITURE": "_T", "TRANSFORMATION": "GY"},
            "Consumer prices, all items, year over year", "Percent, year over year", {"dp": 1}),
    "GDP": ("OECD.SDD.NAD", "DSD_NAMAIN1@DF_QNA_EXPENDITURE_GROWTH_G20", "1.1", NAMAIN, {"FREQ": "Q", "SECTOR": "S1", "TRANSACTION": "B1GQ", "TRANSFORMATION": "G1"},
            "Real GDP growth, quarter over quarter", "Percent, quarter over quarter", {"dp": 1}),
    "UNR": ("OECD.SDD.TPS", "DSD_LFS@DF_IALFS_UNE_M", "1.0", LFS, {"MEASURE": "UNE_LF_M", "ADJUSTMENT": "Y", "SEX": "_T", "AGE": "Y_GE15", "FREQ": "M"},
            "Unemployment rate", "Percent of labour force", {"dp": 1}),
    "IR3": ("OECD.SDD.STES", "DSD_STES@DF_FINMARK", "4.0", STES, {"FREQ": "M", "MEASURE": "IR3TIB"},
            "Short-term interest rate (3-month)", "Percent per annum", {"dp": 2}),
    "IRLT": ("OECD.SDD.STES", "DSD_STES@DF_FINMARK", "4.0", STES, {"FREQ": "M", "MEASURE": "IRLT"},
            "Long-term interest rate (10-year government bond)", "Percent per annum", {"dp": 2}),
}


def _key(dims, fix, areas):
    return ".".join("+".join(areas) if d == "REF_AREA" else fix.get(d, "") for d in dims)


def fetch_indicator(code, start="1990-01"):
    ag, flow, ver, dims, filt, title, units, extra = INDICATORS[code]
    key = _key(dims, filt, AREAS)
    url = f"{API}{ag},{flow},{ver}/{key}?startPeriod={start}&format=csvfilewithlabels"
    for attempt in range(4):
        try:
            text = request(url, timeout=180, retries=2, headers={"Accept": "text/csv"})
            break
        except Exception as e:
            if attempt == 3:
                raise
            print("oecd retry", code, str(e)[:120])
            time.sleep(20 * (attempt + 1))
    rows = list(csv.DictReader(io.StringIO(text.lstrip("﻿"))))
    return url, rows


def _period_end(p):
    if "-Q" in p:
        y, q = p.split("-Q")
        return f"{y}-" + {"1": "03-31", "2": "06-30", "3": "09-30", "4": "12-31"}[q]
    if len(p) == 7 and p[4] == "-":
        y, m = int(p[:4]), int(p[5:])
        import calendar
        return f"{p}-{calendar.monthrange(y, m)[1]:02d}"
    if "-Q" in p:
        y, q = p.split("-Q")
        return f"{y}-" + {"1": "03-31", "2": "06-30", "3": "09-30", "4": "12-31"}[q]
    return p


def indicator_series(code, start="1990-01"):
    ag, flow, ver, kdims, filt, title, units, extra = INDICATORS[code]
    url, rows = fetch_indicator(code, start)
    out = []
    by_area = {}
    for r in rows:
        if any(r.get(k) != v for k, v in filt.items()):
            continue
        by_area.setdefault(r["REF_AREA"], []).append(r)
    dims = [c for c in (rows[0].keys() if rows else []) if c.isupper() and c not in ("STRUCTURE", "STRUCTURE_ID", "STRUCTURE_NAME", "ACTION")]
    for area, rs in by_area.items():
        if area not in AREAS:
            continue
        rs.sort(key=lambda r: r["TIME_PERIOD"])
        # one series per area: if several keys survive the filters, keep the one with the most observations
        keys = {}
        for r in rs:
            k = tuple(r.get(c) for c in dims if c not in ("TIME_PERIOD", "OBS_VALUE", "OBS_STATUS", "UNIT_MULT", "DECIMALS", "BASE_PER", "CONF_STATUS"))
            keys.setdefault(k, []).append(r)
        rs = max(keys.values(), key=len)
        freq = rs[0].get("FREQ") or ("Q" if code == "GDP" else "M")
        sdmx_key = ".".join(rs[0].get(c, "") for c in dims if c not in ("TIME_PERIOD", "OBS_VALUE", "OBS_STATUS", "UNIT_MULT", "DECIMALS", "BASE_PER", "CONF_STATUS", "REF_YEAR_PRICE", "CURRENCY", "DURABILITY"))
        meta = {"id": f"OECD:{code}:{area}", "source": rs[0].get("STRUCTURE_NAME") or flow, "provider": "OECD", "title": f"{title}, {AREAS[area]}",
                "units": units, "frequency": freq, "seasonal_adj": {"Y": "SA", "N": "NSA", "AA": "SA"}.get(rs[0].get("ADJUSTMENT")),
                "geography": AREAS[area], "source_url": EXPLORER.format(id=flow, ag=ag), "data_url": f"{API}{ag},{flow},{ver}/{_key(kdims, filt, [area])}?startPeriod={start}",
                "source_key": f"{flow} {sdmx_key}", "section": "global", "release": f"OECD:{code}", "group": f"oecd_{code.lower()}",
                "short": f"{AREAS[area]} {title.split(',')[0].lower()}", "indicator": code, "area": area, "no_changes": area not in ("USA", "G20", "OECD", "EA20", "CHN", "JPN", "DEU", "GBR")}
        meta.update(extra)
        cols = ["TIME_PERIOD", "OBS_VALUE", "OBS_STATUS", "REF_AREA", "MEASURE" if "MEASURE" in rs[0] else "TRANSACTION", "UNIT_MEASURE", "ADJUSTMENT", "TRANSFORMATION"]
        out.append({"meta": meta, "obs": [(_period_end(r["TIME_PERIOD"]), r["OBS_VALUE"]) for r in rs if r.get("OBS_VALUE") not in (None, "")],
                    "snap": {"url": meta["data_url"] + "&format=csvfilewithlabels", "series": sdmx_key, "cols": cols, "rows": [[r.get(c, "") for c in cols] for r in rs],
                             "keys": [_period_end(r["TIME_PERIOD"]) for r in rs], "hl": 1, "note": f"OECD SDMX dataflow {ag},{flow},{ver}."}})
    return out
