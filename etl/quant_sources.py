"""External inputs of the quant lab that are not already in storage, cached under quant/ in the public bucket:

  quant/factors/ff_daily.json   Fama-French 5 factors (2x3) + momentum, daily, from Kenneth French's data library
  quant/calendar/macro.json     CPI and Employment Situation release dates (BLS news release archive) and FOMC
                                statement dates (Federal Reserve FOMC calendars), merged with macro/releases.json
  quant/filings/{T}.json        8-K / 10-K / 10-Q filings since 2008 with EDGAR acceptance timestamps (submissions API)

Each file records its source URLs, retrieval time and method.
"""
import datetime as dt
import io
import re
import zipfile

from .common import now_iso, request, sec_json

FF5_URL = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/F-F_Research_Data_5_Factors_2x3_daily_CSV.zip"
MOM_URL = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/F-F_Momentum_Factor_daily_CSV.zip"
FF_LIBRARY = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/data_library.html"
FACTORS_PATH = "quant/factors/ff_daily.json"
CAL_PATH = "quant/calendar/macro.json"
FACTOR_START = "20070101"
UA = {"User-Agent": "Mozilla/5.0 (compatible; FiledUnder research; contact@filedunder.ai)"}


def _csv_rows(zbytes):
    z = zipfile.ZipFile(io.BytesIO(zbytes))
    text = z.read(z.namelist()[0]).decode("latin-1")
    header, rows, note = None, [], []
    for line in text.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if header is None:
            if len(parts) > 1 and parts[0] == "" and parts[1]:
                header = parts[1:]
            elif line.strip():
                note.append(line.strip())
            continue
        if len(parts) >= len(header) + 1 and re.fullmatch(r"\d{8}", parts[0]):
            rows.append((parts[0], [float(x) for x in parts[1:len(header) + 1]]))
        elif rows:
            break   # the daily table ends at the first non-date line
    return header, rows, " ".join(note)


def parse_french(ff5_zip, mom_zip, start=FACTOR_START):
    """Daily factor table in decimals (the library publishes percent). Dates are the intersection of both files."""
    h5, r5, n5 = _csv_rows(ff5_zip)
    hm, rm, nm = _csv_rows(mom_zip)
    if h5[:6] != ["Mkt-RF", "SMB", "HML", "RMW", "CMA", "RF"]:
        raise ValueError(f"unexpected FF5 header {h5}")
    if not hm or hm[0].strip().lower() not in ("mom", "mom   "):
        raise ValueError(f"unexpected momentum header {hm}")
    mom = {d: v[0] for d, v in rm}
    d_out, f_out = [], []
    for d, v in r5:
        if d < start or d not in mom:
            continue
        d_out.append(f"{d[:4]}-{d[4:6]}-{d[6:]}")
        f_out.append([round(x / 100.0, 6) for x in v[:5]] + [round(mom[d] / 100.0, 6), round(v[5] / 100.0, 6)])
    return {"cols": ["mkt_rf", "smb", "hml", "rmw", "cma", "mom", "rf"], "d": d_out, "f": f_out, "note": n5, "mom_note": nm}


def fetch_french():
    a = request(FF5_URL, headers=UA, timeout=90, retries=4, raw=True)
    b = request(MOM_URL, headers=UA, timeout=90, retries=4, raw=True)
    t = parse_french(a, b)
    t.update({"source": "Kenneth R. French Data Library: Fama/French 5 Factors (2x3) [Daily] and Momentum Factor (Mom) [Daily]",
              "source_url": FF_LIBRARY, "files": [FF5_URL, MOM_URL], "retrieved": now_iso(), "as_of": t["d"][-1] if t["d"] else None,
              "units": "daily returns in decimals (library values / 100); rf is the daily one-month T-bill return used by the library",
              "attribution": "Fama/French factors courtesy of Kenneth R. French, Tuck School of Business, Dartmouth"})
    return t


# ---------------------------------------------------------------- macro calendar
BLS_PAGES = {"CPI": ("https://www.bls.gov/bls/news-release/cpi.htm", r"cpi_(\d{2})(\d{2})(\d{4})\.htm", "Consumer Price Index", "8:30 a.m. ET"),
             "EMPSIT": ("https://www.bls.gov/bls/news-release/empsit.htm", r"empsit_(\d{2})(\d{2})(\d{4})\.htm", "Employment Situation (payrolls)", "8:30 a.m. ET")}
FOMC_CUR = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
FOMC_HIST = "https://www.federalreserve.gov/monetarypolicy/fomchistorical{y}.htm"


def parse_bls_archive(html, pattern):
    """Release dates from the archived news-release file names (cpi_MMDDYYYY.htm is the release published that day)."""
    out = set()
    for mm, dd, yy in re.findall(pattern, html):
        try:
            out.add(dt.date(int(yy), int(mm), int(dd)).isoformat())
        except ValueError:
            pass
    return sorted(out)


_FOMC_LINK = re.compile(r'<a href="([^"]*(?:pressreleases/monetary|press/monetary/)(\d{8})[a-z]\.htm)">\s*(Statement|HTML)\s*</a>', re.I)


def parse_fomc(html):
    """FOMC statement dates: links labelled 'Statement', or 'HTML' links under a 'Statement:' label (current calendar)."""
    out = {}
    for m in _FOMC_LINK.finditer(html):
        href, ymd, label = m.group(1), m.group(2), m.group(3)
        if label.lower() == "html":
            k = html.rfind("<strong>", 0, m.start())
            lab = html[k:html.find("</strong>", k)] if k >= 0 else ""
            if k < 0 or "Statement" not in lab or m.start() - k > 400:
                continue
        d = f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:]}"
        out[d] = href if href.startswith("http") else "https://www.federalreserve.gov" + href
    return out


def fetch_macro_calendar(sb=None, since=2008, log=print):
    events = {}
    sources = {}
    for key, (url, pat, title, time_) in BLS_PAGES.items():
        html = request(url, headers=UA, timeout=60, retries=4)
        today = dt.date.today().isoformat()
        ds = [d for d in parse_bls_archive(html, pat) if f"{since}-01-01" <= d <= today]
        events[key] = {"title": title, "time": time_, "dates": ds, "source_url": url, "method": "release dates parsed from archived news release file names (e.g. cpi_MMDDYYYY.htm)"}
        sources[key] = url
    fomc = {}
    fomc.update(parse_fomc(request(FOMC_CUR, headers=UA, timeout=60, retries=4)))
    for y in range(since, dt.date.today().year - 5):   # the current calendar page covers the last five years
        try:
            fomc.update(parse_fomc(request(FOMC_HIST.format(y=y), headers=UA, timeout=60, retries=4)))
        except Exception as e:
            log(f"FOMC {y}: {str(e)[:120]}")
    events["FOMC"] = {"title": "FOMC statement", "time": "2:00 p.m. ET (scheduled meetings; unscheduled statements vary)", "dates": sorted(fomc),
                      "links": fomc, "source_url": FOMC_CUR, "method": "statement links on the Federal Reserve FOMC calendar and historical-year pages"}
    # scheduled future releases from our own release calendar
    if sb is not None:
        try:
            rel = sb.get_json("macro/releases.json") or []
            rows = rel if isinstance(rel, list) else rel.get("rows", [])
            up = {"CPI": [], "EMPSIT": []}
            for r in rows:
                k = {"BLS:CPI": "CPI", "BLS:EMPSIT": "EMPSIT"}.get(r.get("release"))
                if k:
                    up[k].append(r["release_date"])
            for k, ds in up.items():
                have = set(events[k]["dates"])
                events[k]["scheduled"] = sorted(d for d in ds if d not in have and d > dt.date.today().isoformat())
                events[k]["scheduled_source"] = "macro/releases.json (BLS release calendar)"
        except Exception as e:
            log("release calendar merge failed: " + str(e)[:120])
    return {"generated": now_iso(), "events": events, "sources": sources}


# ---------------------------------------------------------------- EDGAR filings with acceptance time
FORMS = ("8-K", "8-K/A", "10-K", "10-K/A", "10-Q", "10-Q/A")


def _rows(block):
    n = len(block.get("form", []))
    acc = block.get("acceptanceDateTime") or [""] * n
    items = block.get("items") or [""] * n
    for i in range(n):
        yield [block["form"][i], block["filingDate"][i], block["accessionNumber"][i], block["primaryDocument"][i], items[i] or "", block["reportDate"][i] or "", acc[i] or ""]


def sec_filings(cik, since="2008-01-01", full=True):
    sub = sec_json(f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json")
    rows = list(_rows(sub["filings"]["recent"]))
    if full:
        for f in sub["filings"].get("files", []):
            if (f.get("filingTo") or "9999") < since:
                continue
            rows += list(_rows(sec_json(f"https://data.sec.gov/submissions/{f['name']}")))
    return [r + [int(cik)] for r in rows if r[0] in FORMS and r[1] >= since]


def filings_doc(tk, cik, predecessors=(), full=True, prior=None):
    """quant/filings/{T}.json: one row per filing, cols form, filed, accession, primary_doc, items, report_date, accepted (UTC), cik."""
    by = {}
    if prior:
        for r in prior.get("rows", []):
            by[r[2]] = r
    for r in sec_filings(cik, full=full):
        by[r[2]] = r
    for pcik, cut in predecessors:
        if full or not prior:
            for r in sec_filings(pcik, full=full):
                if r[1] <= cut:
                    by[r[2]] = r
    rows = sorted(by.values(), key=lambda r: (r[1], r[2]))
    return {"ticker": tk, "since": "2008-01-01", "generated": now_iso(), "source": "SEC EDGAR submissions API (data.sec.gov/submissions)",
            "source_url": f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json",
            "cols": ["form", "filed", "accession", "primary_doc", "items", "report_date", "accepted", "cik"], "rows": rows}
