"""Release calendar from the publishers' own schedules.

Federal Reserve Board: the monthly statistical release calendar pages (/newsevents/YYYY-month.htm), the Z.1 release
dates page and the FOMC meeting calendar. BLS: the official iCalendar feed of news releases. CFTC: the Commitments of
Traders release schedule page. BEA: the news release schedule. Census: the economic indicator calendar.
bls.gov and cftc.gov block this machine's network, so those two are fetched through Firecrawl.
"""
import datetime as dt
import html as _html
import json
import os
import re

from .common import request

FRB = "https://www.federalreserve.gov"
MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"]
FRB_NAMES = [("H.15", "FRB:H.15"), ("H.4.1", "FRB:H.4.1"), ("H.8 ", "FRB:H.8"), ("H.6 ", "FRB:H.6"), ("G.17", "FRB:G.17"), ("G.19", "FRB:G.19"),
             ("Senior Loan Officer", "FRB:SLOOS"), ("Senior Credit Officer", "FRB:SCOOS"), ("Z.1", "FRB:Z.1"), ("Household Debt Service", "FRB:DSR")]
BLS_NAMES = {"Consumer Price Index": "BLS:CPI", "Employment Situation": "BLS:EMPSIT", "Job Openings and Labor Turnover Survey": "BLS:JOLTS", "Employment Cost Index": "BLS:ECI",
             "Producer Price Index": "BLS:PPI", "Import and Export Price Indexes": "BLS:MXP", "Productivity and Costs": "BLS:PROD", "Real Earnings": "BLS:REAL"}
# BEA titles carry the period ("GDP (Second Estimate) ... 3rd Quarter 2026"), so these match on the leading words.
BEA_NAMES = [("GDP (", "BEA:GDP"), ("Gross Domestic Product", "BEA:GDP"), ("Personal Income and Outlays", "BEA:PIO"),
             ("U.S. International Trade in Goods and Services", "BEA:TRADE"), ("U.S. International Transactions", "BEA:BOP")]
CENSUS_NAMES = [("Advance Monthly Sales for Retail", "CEN:RETAIL"), ("Advance Report on Durable Goods", "CEN:DURABLES"),
                ("New Residential Construction", "CEN:STARTS"), ("New Residential Sales", "CEN:NEWHOME"),
                ("Construction Spending", "CEN:CONSTR"), ("Manufacturers' Shipments, Inventories", "CEN:M3"),
                ("Advance Economic Indicators", "CEN:AEI")]


def _text(html):
    t = re.sub(r"<script.*?</script>|<style.*?</style>", "", html, flags=re.S)
    t = _html.unescape(re.sub(r"<[^>]+>", " | ", t))
    return [x.strip() for x in re.split(r"\s*\|\s*", re.sub(r"\s+", " ", t)) if x.strip()]


def _frb_key(name):
    for p, k in FRB_NAMES:
        p = p.strip()
        if name.startswith(p + " ") or (len(p) > 6 and p in name):
            return k
    return None


def frb_month(year, month):
    url = f"{FRB}/newsevents/{year}-{MONTHS[month - 1]}.htm"
    try:
        toks = _text(request(url, timeout=60, retries=2))
    except Exception as e:
        print("fed calendar", url, str(e)[:100])
        return []
    try:
        i = toks.index("Statistical Releases")
    except ValueError:
        return []
    out, tm, name = [], None, None
    for t in toks[i + 1:]:
        if t in ("Other", "Previous", "Next") or t.startswith("Last Update"):
            break
        if t in ("Time:", "Release Date(s):"):
            continue
        if re.fullmatch(r"\d{1,2}:\d{2} [ap]\.m\.", t):
            tm = t
            continue
        if re.fullmatch(r"\d{1,2}(, \d{1,2})*( and \d{1,2})?", t):
            key = _frb_key(name) if name else None
            if name and key:
                for day in re.findall(r"\d{1,2}", t):
                    out.append({"release": key, "release_date": f"{year}-{month:02d}-{int(day):02d}", "release_time": tm and tm + " ET",
                                "provider": "Federal Reserve Board", "title": name, "source_url": url})
            name, tm = None, None
            continue
        if "approximate" in t.lower():
            continue
        name = t
    return out


def z1_dates():
    url = FRB + "/releases/z1/release-dates.htm"
    out = []
    try:
        toks = _text(request(url, timeout=60, retries=2))
    except Exception as e:
        print("z1 dates", str(e)[:100])
        return out
    for t in toks:
        for m in re.finditer(r"(January|February|March|April|May|June|July|August|September|October|November|December) (\d{1,2}), (\d{4})", t):
            d = dt.date(int(m.group(3)), MONTHS.index(m.group(1).lower()) + 1, int(m.group(2))).isoformat()
            out.append({"release": "FRB:Z.1", "release_date": d, "release_time": "12:00 p.m. ET", "provider": "Federal Reserve Board",
                        "title": "Z.1 Financial Accounts of the United States", "source_url": url})
    return out


def frb(months_ahead=3):
    t = dt.date.today()
    out = []
    for k in range(months_ahead):
        y, m = t.year + (t.month - 1 + k) // 12, (t.month - 1 + k) % 12 + 1
        out += frb_month(y, m)
    out += z1_dates()
    seen, uniq = set(), []
    for r in out:
        if (r["release"], r["release_date"]) not in seen:
            seen.add((r["release"], r["release_date"]))
            uniq.append(r)
    return uniq


def fomc():
    """FOMC meetings: the decision is announced at 2:00 p.m. ET on the meeting's last day; minutes three weeks later."""
    url = FRB + "/monetarypolicy/fomccalendars.htm"
    try:
        h = request(url, timeout=60, retries=2)
    except Exception as e:
        print("fomc calendar", str(e)[:100])
        return []
    out = []
    for blk in re.split(r'<div class="panel-heading">', h)[1:]:
        y = re.search(r"(\d{4}) FOMC Meetings", blk)
        if not y:
            continue
        year = int(y.group(1))
        for mon, day in re.findall(r'fomc-meeting__month[^>]*><strong>([^<]+)</strong>.*?fomc-meeting__date[^>]*>([^<]+)<', blk, re.S):
            mon, day = mon.strip(), _html.unescape(day).strip()
            if "notation" in day.lower() or "unscheduled" in day.lower():
                continue
            last_mon = mon.split("/")[-1].lower()[:3]
            m = next((i + 1 for i, x in enumerate(MONTHS) if x[:3] == last_mon), None)
            d = re.findall(r"\d{1,2}", day)
            if not m or not d:
                continue
            sep = day.endswith("*")
            date = dt.date(year, m, int(d[-1]))
            out.append({"release": "FOMC:DECISION", "release_date": date.isoformat(), "release_time": "2:00 p.m. ET",
                        "provider": "Federal Reserve Board",
                        "title": "FOMC statement and press conference" + (" with Summary of Economic Projections" if sep else ""),
                        "source_url": url})
            mins = re.search(r"fomcminutes%d%02d%02d\.htm.*?\(Released ([A-Za-z]+) (\d{1,2}), (\d{4})\)" % (year, m, date.day), blk, re.S)
            mdate = (dt.date(int(mins.group(3)), MONTHS.index(mins.group(1).lower()) + 1, int(mins.group(2))) if mins
                     else date + dt.timedelta(days=21))
            out.append({"release": "FOMC:MINUTES", "release_date": mdate.isoformat(), "release_time": "2:00 p.m. ET",
                        "provider": "Federal Reserve Board", "title": f"Minutes of the FOMC meeting of {date.strftime('%B')} {date.day}",
                        "source_url": url})
    return out


def _name_key(names, title):
    return next((k for p, k in names if title.startswith(p)), None)


def bea():
    url = "https://www.bea.gov/news/schedule"
    try:
        h = request(url, timeout=60, retries=2)
    except Exception as e:
        print("bea schedule", str(e)[:100])
        return []
    y = re.search(r"Year (\d{4})", h)
    year = int(y.group(1)) if y else dt.date.today().year
    out, prev = [], 0
    for mon, day, tm, title in re.findall(r'release-date">([A-Za-z]+) (\d{1,2})</div>\s*<small[^>]*>([^<]*)</small>.*?release-title[^>]*>([^<]+)<', h, re.S):
        m = MONTHS.index(mon.lower()) + 1
        if m < prev:  # the schedule runs into the next year
            year += 1
        prev = m
        title = _html.unescape(title).strip()
        key = _name_key(BEA_NAMES, title)
        if not key:
            continue
        out.append({"release": key, "release_date": f"{year}-{m:02d}-{int(day):02d}", "release_time": tm.strip().replace("AM", "a.m.").replace("PM", "p.m.") + " ET",
                    "provider": "Bureau of Economic Analysis", "title": title, "source_url": url})
    return out


def census():
    """The list view holds the coming calendar year; the prior year's page covers the rest of this one."""
    out = []
    pages = ["https://www.census.gov/economic-indicators/calendar-listview.html",
             f"https://www.census.gov/economic-indicators/calendar-listview-{dt.date.today().year}.html"]
    merged = {}
    for url in dict.fromkeys(pages):
        for r in _census_page(url):
            k = (r["release"], r["release_date"])
            if k not in merged:
                merged[k] = r
            elif r["title"] not in merged[k]["title"]:
                # Catch-up releases publish two reference periods on one date.
                merged[k] = dict(merged[k], title=merged[k]["title"] + " and " + r["title"].rsplit(", ", 1)[-1])
    return list(merged.values())


def _census_page(url):
    try:
        h = request(url, timeout=60, retries=2)
    except Exception as e:
        print("census calendar", url, str(e)[:100])
        return []
    out = []
    for title, key_dt, tm, period in re.findall(r'<td height="20"><a[^>]*>([^<]+)</a></td>\s*<td sorttable_customkey="(\d{12})">[^<]*</td>\s*<td>([^<]*)</td>\s*<td>([^<]*)</td>', h):
        title = re.sub(r"\s+", " ", _html.unescape(title)).strip()
        key = _name_key(CENSUS_NAMES, title)
        if not key:
            continue
        out.append({"release": key, "release_date": f"{key_dt[:4]}-{key_dt[4:6]}-{key_dt[6:8]}", "release_time": tm.strip().replace("AM", "a.m.").replace("PM", "p.m.") + " ET",
                    "provider": "U.S. Census Bureau", "title": f"{title}, {period.strip()}", "source_url": url})
    return out


def _firecrawl(url, fmt="rawHtml"):
    key = os.environ.get("FIRECRAWL_API_KEY")
    if not key:
        raise RuntimeError("FIRECRAWL_API_KEY not set")
    d = json.loads(request("https://api.firecrawl.dev/v2/scrape", method="POST", data={"url": url, "formats": [fmt], "timeout": 90000},
                           headers={"Authorization": f"Bearer {key}"}, timeout=150))
    return d["data"].get(fmt) or ""


def bls():
    url = "https://www.bls.gov/schedule/news_release/bls.ics"
    try:
        text = request(url, timeout=60, retries=1)
    except Exception:
        text = _firecrawl(url)
    out = []
    for ev in re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", text, re.S):
        s = re.search(r"SUMMARY:(.*)", ev)
        d = re.search(r"DTSTART[^:]*:(\d{8})T(\d{4})", ev)
        if not s or not d:
            continue
        name = s.group(1).strip()
        key = BLS_NAMES.get(name)
        if not key:
            continue
        hh, mm = int(d.group(2)[:2]), d.group(2)[2:]
        tm = f"{(hh - 1) % 12 + 1}:{mm} {'a.m.' if hh < 12 else 'p.m.'} ET"
        out.append({"release": key, "release_date": f"{d.group(1)[:4]}-{d.group(1)[4:6]}-{d.group(1)[6:]}", "release_time": tm,
                    "provider": "Bureau of Labor Statistics", "title": name, "source_url": url})
    return out


def cftc():
    url = "https://www.cftc.gov/MarketReports/CommitmentsofTraders/ReleaseSchedule/index.htm"
    try:
        md = request(url, timeout=60, retries=1)
        md = "\n".join(_text(md))
        if "Release Schedule" not in md:
            raise RuntimeError("schedule not in response")
    except Exception:
        md = _firecrawl(url, "markdown")
    out = []
    year = month = None
    for tok in re.split(r"[|\n]", md):
        t = tok.replace("\\", "").replace("*", "").replace("#", "").strip()
        y = re.search(r"(\d{4}) Release Schedule", t)
        if y:
            year, month = int(y.group(1)), None
        elif year and t.lower() in MONTHS:
            month = MONTHS.index(t.lower()) + 1
        elif year and month and re.fullmatch(r"\d{1,2}", t):
            out.append({"release": "CFTC:COT", "release_date": f"{year}-{month:02d}-{int(t):02d}", "release_time": "3:30 p.m. ET",
                        "provider": "Commodity Futures Trading Commission", "title": "Commitments of Traders", "source_url": url})
        elif t and year and month and not re.fullmatch(r"-+|Month|Dates", t):
            month = None if len(t) > 12 else month
    return out


def collect(log=print):
    rows = []
    for name, fn in [("frb", frb), ("fomc", fomc), ("bls", bls), ("cftc", cftc), ("bea", bea), ("census", census)]:
        try:
            got = fn()
            log(f"calendar {name}: {len(got)} dates")
            rows += got
        except Exception as e:
            log(f"calendar {name} failed: {str(e)[:200]}")
    return rows
