"""Federal Reserve Board statistical releases.

The Board is retiring the Data Download Program (the "Build your package" CSV option goes away the week of November 9, 2026)
and points users to the complete release files on each release page. This module reads those files:
the SDMX XML zips (H.15, H.4.1, H.8, H.6, G.17, G.19, SLOOS, SCOOS), the DDP package for household debt service ratios
(no release-page file exists yet) and the Z.1 CSV tables. Values are kept exactly as printed in the file; the file's
Last-Modified date (Eastern time) is the publication date of the vintage.
"""
import csv
import datetime as dt
import email.utils
import io
import xml.etree.ElementTree as ET
import zipfile
from zoneinfo import ZoneInfo

from .common import request

BASE = "https://www.federalreserve.gov"
ET_TZ = ZoneInfo("America/New_York")
FREQ = {"9": "D", "8": "D", "19": "W", "17": "W", "18": "W", "129": "M", "162": "Q", "203": "A"}

# release key: file, release page, title, calendar key
RELEASES = {
    "H15": (BASE + "/releases/h15/data/FRB_h15_xml.zip", BASE + "/releases/h15/", "H.15 Selected Interest Rates", "FRB:H.15"),
    "H41": (BASE + "/releases/h41/data/FRB_h41_xml.zip", BASE + "/releases/h41/", "H.4.1 Factors Affecting Reserve Balances", "FRB:H.4.1"),
    "H8": (BASE + "/releases/h8/data/FRB_h8_xml.zip", BASE + "/releases/h8/current/", "H.8 Assets and Liabilities of Commercial Banks", "FRB:H.8"),
    "H6": (BASE + "/releases/h6/data/FRB_h6_xml.zip", BASE + "/releases/h6/current/", "H.6 Money Stock Measures", "FRB:H.6"),
    "G17": (BASE + "/releases/g17/data/FRB_g17_xml.zip", BASE + "/releases/g17/current/", "G.17 Industrial Production and Capacity Utilization", "FRB:G.17"),
    "G19": (BASE + "/releases/g19/data/FRB_g19_xml.zip", BASE + "/releases/g19/current/", "G.19 Consumer Credit", "FRB:G.19"),
    "SLOOS": (BASE + "/releases/sloos/data/FRB_SLOOS_xml.zip", BASE + "/data/sloos.htm", "Senior Loan Officer Opinion Survey on Bank Lending Practices", "FRB:SLOOS"),
    "SCOOS": (BASE + "/releases/scoos/data/FRB_SCOOS_xml.zip", BASE + "/data/scoos.htm", "Senior Credit Officer Opinion Survey on Dealer Financing Terms", "FRB:SCOOS"),
    "DSR": (BASE + "/datadownload/Output.aspx?rel=DSR&filetype=zip", BASE + "/releases/housedebt/", "Household Debt Service and Financial Obligations Ratios", "FRB:DSR"),
    "Z1": (BASE + "/releases/z1/current/z1_csv_files.zip", BASE + "/releases/z1/", "Z.1 Financial Accounts of the United States", "FRB:Z.1"),
}
GROUPS = {"daily": ["H15"], "weekly": ["H41", "H8", "H6"], "monthly": ["G17", "G19", "SLOOS", "SCOOS", "DSR", "Z1"]}

B = dict(scale=1e-3, dunits="Billions of dollars", short_units="bn", dp=0)   # values published in millions
T = dict(scale=1e-6, dunits="Trillions of dollars", short_units="tn", dp=2)
NP = dict(dunits="Net percentage", dp=1, short_units="pts")
YOY = dict(display="yoy", dunits="Percent, year over year", dp=1)

# code: (title, section, group, extra meta)
SERIES = {
    "H15": {
        "RIFSPFF_N.B": ("Federal funds effective rate", "rates", "policy", {"short": "Fed funds effective"}),
        "RIFSRP_F02_N.B": ("Discount rate, primary credit", "rates", "policy", {}),
        "RIFSPBLP_N.B": ("Bank prime loan rate", "rates", "policy", {}),
        "RIFSPPNAAD90_N.B": ("3-month AA nonfinancial commercial paper rate", "rates", "cp", {"short": "3M nonfinancial CP"}),
        "RIFSPPFAAD90_N.B": ("3-month AA financial commercial paper rate", "rates", "cp", {"short": "3M financial CP"}),
        "RIFLGFCY05_XII_N.B": ("5-year TIPS yield, constant maturity", "rates", "real", {}),
    },
    "H41": {
        "RESPPA_N.WW": ("Federal Reserve total assets", "rates", "fedbs", dict(B, short="Fed total assets")),
        "RESH4R_N.WW": ("Reserve balances with Federal Reserve Banks", "rates", "liq", dict(B, short="Reserve balances")),
        "RESPPLLRD_N.WW": ("Reverse repurchase agreements, others (ON RRP)", "rates", "liq", dict(B, short="Reverse repo, others")),
        "RESPPLLDT_N.WW": ("U.S. Treasury General Account", "rates", "liq", dict(B, short="Treasury General Account")),
        "RESPPALGUO_N.WW": ("Fed holdings of U.S. Treasury securities", "rates", "fedbs", dict(B, short="Fed Treasury holdings")),
        "RESPPALGASMO_N.WW": ("Fed holdings of mortgage-backed securities", "rates", "fedbs", dict(B, short="Fed MBS holdings")),
        "RESTBC_N.WW": ("Currency in circulation", "rates", "liq2", B),
        "RESPPALDP_N.WW": ("Discount window primary credit outstanding", "rates", "liq2", dict(B, dp=1)),
    },
    "H8": {
        "B1001NCBA": ("Bank credit, all commercial banks", "credit", "h8", B),
        "B1020NCBA": ("Loans and leases, all commercial banks", "credit", "h8", B),
        "B1023NCBA": ("Commercial and industrial loans, all commercial banks", "credit", "h8loans", dict(B, short="C&I loans")),
        "B1026NCBA": ("Real estate loans, all commercial banks", "credit", "h8loans", dict(B, short="Real estate loans")),
        "B3219NCBA": ("Commercial real estate loans, all commercial banks", "credit", "h8loans", dict(B, short="CRE loans")),
        "B1029NCBA": ("Consumer loans, all commercial banks", "credit", "h8loans", dict(B, short="Consumer loans")),
        "B1002NCBA": ("Securities in bank credit, all commercial banks", "credit", "h8", B),
        "B1058NCBA": ("Deposits, all commercial banks", "credit", "dep", dict(B, short="Bank deposits")),
        "B1058NSMA": ("Deposits, small domestically chartered commercial banks", "credit", "dep", dict(B, short="Small bank deposits")),
        "B3094NCBA": ("Borrowings, all commercial banks", "credit", "dep", B),
    },
    "H6": {
        "M2.M": ("M2 money stock, year over year", "rates", "money", dict(YOY, short="M2, y/y")),
        "M1.M": ("M1 money stock, year over year", "rates", "money", dict(YOY, short="M1, y/y")),
    },
    "G17": {
        "IP.B50001.S": ("Industrial production, total, year over year", "growth", "ip", dict(YOY, short="Industrial production, y/y")),
        "IP.GMF.S": ("Industrial production, manufacturing (NAICS), year over year", "growth", "ip", dict(YOY, short="Manufacturing output, y/y")),
        "CAPUTL.B50001.S": ("Capacity utilization, total industry", "growth", "cu", {"dp": 1}),
        "CAPUTL.GMF.S": ("Capacity utilization, manufacturing (NAICS)", "growth", "cu", {"dp": 1}),
    },
    "G19": {
        "DTCTL.M": ("Consumer credit outstanding, total", "credit", "cc", B),
        "DTCTLR.M": ("Consumer credit outstanding, revolving", "credit", "cc", B),
        "DTCTLN.M": ("Consumer credit outstanding, nonrevolving", "credit", "cc", B),
        "RIFSPBCICCI_N.M": ("Credit card interest rate, accounts assessed interest", "credit", "ccrate", {"dp": 2}),
    },
    "SLOOS": {
        "SUBLPDCILS_N.Q": ("SLOOS: tightening standards, C&I loans to large and middle-market firms", "credit", "sloos", dict(NP, short="Tightening, C&I large firms")),
        "SUBLPDCISS_N.Q": ("SLOOS: tightening standards, C&I loans to small firms", "credit", "sloos", dict(NP, short="Tightening, C&I small firms")),
        "SUBLPDRCSN_N.Q": ("SLOOS: tightening standards, CRE loans secured by nonfarm nonresidential properties", "credit", "sloos", dict(NP, short="Tightening, CRE nonresidential")),
        "SUBLPDCLCS_N.Q": ("SLOOS: tightening standards, credit card loans", "credit", "sloos2", dict(NP, short="Tightening, credit cards")),
        "SUBLPDCLAS_N.Q": ("SLOOS: tightening standards, auto loans", "credit", "sloos2", dict(NP, short="Tightening, auto loans")),
        "SUBLPDCILD_N.Q": ("SLOOS: stronger demand, C&I loans from large and middle-market firms", "credit", "sloosd", dict(NP, short="Demand, C&I large firms")),
    },
    "SCOOS": {
        "EXH_E1_C3.Q4.NP": ("SCOOS: price terms offered to hedge funds", "credit", "scoos", dict(NP, short="Hedge fund price terms", q="4")),
        "EXH_E1_C5.Q5.NP": ("SCOOS: nonprice terms with hedge funds", "credit", "scoos", dict(NP, short="Hedge fund nonprice terms", q="5")),
        "EXH_E2_C1.Q8.NP": ("SCOOS: use of financial leverage by hedge funds", "credit", "scoos", dict(NP, short="Hedge fund leverage", q="8")),
        "EXH_E3_C1.Q53.NP": ("SCOOS: demand for funding of high-grade corporate bonds", "credit", "scoos2", dict(NP, short="Funding demand, high-grade bonds", q="53")),
        "EXH_E3_C1.Q57.NP": ("SCOOS: demand for funding of high-yield corporate bonds", "credit", "scoos2", dict(NP, short="Funding demand, high-yield bonds", q="57")),
        "EXH_E3_C2.Q61.NP": ("SCOOS: demand for funding of equities", "credit", "scoos2", dict(NP, short="Funding demand, equities", q="61")),
    },
    "DSR": {
        "DSR_RATIO": ("Household debt service ratio", "credit", "dsr", {"dp": 2}),
        "MORTGAGE_DSR_RATIO": ("Mortgage debt service ratio", "credit", "dsr", {"dp": 2}),
        "CONSUMER_DSR_RATIO": ("Consumer debt service ratio", "credit", "dsr", {"dp": 2}),
    },
}
# Z.1 CSV tables: code -> (csv table, title, group, meta)
Z1_SERIES = {
    "LA384104005.Q": ("D3_s", "Debt of domestic nonfinancial sectors", "z1debt", dict(T, short="Nonfinancial debt")),
    "LA154104005.Q": ("D3_s", "Household and nonprofit debt", "z1debt", dict(T, short="Household debt")),
    "LA104104005.Q": ("D3_s", "Nonfinancial corporate business debt", "z1debt", dict(T, short="Nonfinancial corporate debt")),
    "LA314104005.Q": ("D3_s", "Federal government debt", "z1debt", dict(T, short="Federal debt")),
    "FL152090005.Q": ("S1M_b", "Household and nonprofit net worth", "z1nw", dict(T, short="Household net worth")),
}


def fetch(url):
    """Returns (bytes, publication date in Eastern time from Last-Modified)."""
    import urllib.request
    from .common import WEB_UA
    req = urllib.request.Request(url, headers={"User-Agent": WEB_UA})
    last = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                body = r.read()
                lm = r.headers.get("Last-Modified")
                pub = email.utils.parsedate_to_datetime(lm).astimezone(ET_TZ).date().isoformat() if lm else None
                return body, pub, lm
        except Exception as e:
            last = e
            import time
            time.sleep(5 * (attempt + 1))
    raise last


def last_modified(url):
    try:
        import urllib.request
        from .common import WEB_UA
        with urllib.request.urlopen(urllib.request.Request(url, method="HEAD", headers={"User-Agent": WEB_UA}), timeout=60) as r:
            return r.headers.get("Last-Modified")
    except Exception:
        return None


def _units(unit, mult):
    try:
        m = float(mult or 1)
    except ValueError:
        m = 1
    u = (unit or "").replace("_", " ")
    if u.startswith("Currency"):
        return {1e3: "Thousands of dollars", 1e6: "Millions of dollars", 1e9: "Billions of dollars"}.get(m, f"Dollars x {m:g}")
    if u.startswith("Percent"):
        return "Percent"
    if u.startswith("Index"):
        return u.replace("Index: ", "Index, ").replace("Index:", "Index, ").replace(" 100", "=100")
    return u or None


def parse_sdmx(blob, wanted):
    """Streams the release XML and returns {code: {"attrs", "desc", "obs": [(TIME_PERIOD, OBS_VALUE, OBS_STATUS)]}} for wanted codes."""
    z = zipfile.ZipFile(io.BytesIO(blob))
    name = [n for n in z.namelist() if n.endswith("_data.xml")][0]
    out, cur, desc, obs = {}, None, {}, None
    for ev, e in ET.iterparse(z.open(name), events=("start", "end")):
        tag = e.tag.rsplit("}", 1)[-1]
        if ev == "start":
            if tag == "Series":
                code = e.attrib.get("SERIES_NAME")
                cur = code if code in wanted and code not in out else None
                desc, obs = {}, []
            continue
        if tag == "Obs":
            if cur:
                a = e.attrib
                obs.append((a.get("TIME_PERIOD"), a.get("OBS_VALUE"), a.get("OBS_STATUS", "")))
            e.clear()
        elif tag == "Annotation" and cur:
            k = {c.tag.rsplit("}", 1)[-1]: (c.text or "") for c in e}
            desc[k.get("AnnotationType")] = k.get("AnnotationText")
        elif tag == "Series":
            if cur:
                out[cur] = {"attrs": dict(e.attrib), "desc": desc, "obs": obs}
            cur = None
            e.clear()
    return out


def _numeric(v):
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False


def release_series(key, blob, pub):
    url, page, rname, cal = RELEASES[key]
    spec = SERIES[key]
    got = parse_sdmx(blob, set(spec))
    out = []
    for code, (title, section, group, extra) in spec.items():
        g = got.get(code)
        if not g:
            print("frb: series not in file", key, code)
            continue
        a = g["attrs"]
        freq = FREQ.get(a.get("FREQ"), "M")
        units = _units(a.get("UNIT"), a.get("UNIT_MULT"))
        long = g["desc"].get("Long Description") or g["desc"].get("Short Description") or ""
        low = long.lower()
        if "not seasonally adjusted" in low or code.endswith("_N.M"):
            sa = "NSA"
        elif "seasonally adjusted" in low or code.endswith(".S") or key == "H8":
            sa = "SA"
        else:
            sa = None
        meta = {
            "id": f"FRB:{key}:{code}", "source": rname, "provider": "Federal Reserve Board", "title": title, "units": units,
            "frequency": freq, "seasonal_adj": sa, "geography": "United States", "source_url": page, "data_url": url,
            "source_key": code, "section": section, "release": cal, "group": group,
            "notes": (long[:400] + (f" Question {extra['q']} of the SCOOS questionnaire; values are the net percentages charted in the release exhibits." if extra.get("q") else "")).strip(),
        }
        meta.update({k: v for k, v in extra.items() if k != "q"})
        # -9999 with OBS_STATUS ND/NA marks a missing observation in Board files.
        obs = [(d, v) for d, v, st in g["obs"] if _numeric(v) and st not in ("ND", "NA", "NC") and v.strip() != "-9999"]
        rows = [[d, v, s] for d, v, s in g["obs"]]
        out.append({"meta": meta, "obs": obs, "published": pub,
                    "snap": {"url": url, "file": url.rsplit("/", 1)[-1] + " / " + key + "_data.xml", "series": code,
                             "cols": ["TIME_PERIOD", "OBS_VALUE", "OBS_STATUS"], "rows": rows, "keys": [r[0] for r in rows],
                             "note": f"SDMX series {code} in the {rname} release file."}})
    return out


def z1_series(blob, pub):
    url, page, rname, cal = RELEASES["Z1"]
    z = zipfile.ZipFile(io.BytesIO(blob))
    dicts = {}
    out = []
    for code, (table, title, group, extra) in Z1_SERIES.items():
        if table not in dicts:
            dd = {}
            for line in z.read(f"data_dictionary/{table}.txt").decode("utf-8", "replace").splitlines():
                parts = line.split("\t")
                if len(parts) >= 5:
                    dd[parts[0]] = parts
            text = z.read(f"csv/{table}.csv").decode("utf-8", "replace")
            dicts[table] = (dd, list(csv.reader(io.StringIO(text))))
        dd, rows = dicts[table]
        hdr = rows[0]
        if code not in hdr:
            print("frb: Z.1 series not in table", table, code)
            continue
        j = hdr.index(code)
        snap_rows, obs = [], []
        for r in rows[1:]:
            if len(r) <= j:
                continue
            y, q = r[0].split(":Q")
            end = {"1": "03-31", "2": "06-30", "3": "09-30", "4": "12-31"}[q]
            d = f"{y}-{end}"
            snap_rows.append([r[0], r[j]])
            if _numeric(r[j]):
                obs.append((d, r[j]))
        info = dd.get(code, [code, "", "", table, ""])
        units = info[4].split(";")[0].strip() if len(info) > 4 else "Millions of dollars"
        meta = {"id": f"FRB:Z1:{code}", "source": rname, "provider": "Federal Reserve Board", "title": title, "units": units,
                "frequency": "Q", "seasonal_adj": "SA" if "seasonally adjusted" in info[4] and "not seasonally" not in info[4] else "NSA",
                "geography": "United States", "source_url": page, "data_url": url, "source_key": code, "section": "credit",
                "release": cal, "group": group, "notes": f"{info[1]}. {info[2]} of table {info[3]}. {info[4]}."}
        meta.update(extra)
        out.append({"meta": meta, "obs": obs, "published": pub,
                    "snap": {"url": url, "file": f"z1_csv_files.zip / csv/{table}.csv", "series": code, "cols": ["date", code],
                             "rows": snap_rows, "keys": [_qend(r[0]) for r in snap_rows], "note": f"Column {code} of csv/{table}.csv in the Z.1 CSV package."}})
    return out


def _qend(s):
    try:
        y, q = s.split(":Q")
        return f"{y}-" + {"1": "03-31", "2": "06-30", "3": "09-30", "4": "12-31"}[q]
    except Exception:
        return None


def collect(keys, state, force=False, log=print):
    """Downloads each release whose file changed since the last run (state: {url: Last-Modified}) and returns series dicts."""
    out = []
    for key in keys:
        url = RELEASES[key][0]
        lm = last_modified(url)
        if not force and lm and state.get(url) == lm:
            log(f"frb {key}: unchanged since {lm}")
            continue
        blob, pub, lm2 = fetch(url)
        if "datadownload/Output.aspx" in url:
            pub = None  # DDP output is generated on request, so its Last-Modified is not a publication date
        series = z1_series(blob, pub) if key == "Z1" else release_series(key, blob, pub)
        log(f"frb {key}: {len(series)} series, file of {pub}")
        out.append((key, url, lm2 or lm, series))
    return out
