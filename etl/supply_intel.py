"""External evidence around supply links: U.S. trade and sanctions actions (Federal Register), governance indicators
(World Bank WGI), relationship news (Tavily search, Firecrawl page reads) and earnings sympathy moves from our stored
prices. Every quote kept here is verbatim from its source text and carries the source URL and retrieval time."""
import datetime as dt
import json
import os
import re
import urllib.parse

from .common import now_iso, request
from .supply_core import norm, quote_ok, sentences, spillover, words

FR_AGENCIES = ["industry-and-security-bureau", "trade-representative-office-of-united-states", "foreign-assets-control-office",
               "international-trade-administration", "international-trade-commission", "u-s-customs-and-border-protection"]
ISO3 = {"United States": "USA", "China": "CHN", "Taiwan": "TWN", "Hong Kong": "HKG", "Japan": "JPN", "South Korea": "KOR", "India": "IND",
        "Vietnam": "VNM", "Thailand": "THA", "Malaysia": "MYS", "Singapore": "SGP", "Indonesia": "IDN", "Philippines": "PHL", "Israel": "ISR",
        "Russia": "RUS", "Ukraine": "UKR", "Mexico": "MEX", "Canada": "CAN", "Brazil": "BRA", "Germany": "DEU", "France": "FRA",
        "United Kingdom": "GBR", "Ireland": "IRL", "Netherlands": "NLD", "Switzerland": "CHE", "Italy": "ITA", "Denmark": "DNK",
        "Finland": "FIN", "Sweden": "SWE", "Kazakhstan": "KAZ", "Saudi Arabia": "SAU", "United Arab Emirates": "ARE", "Australia": "AUS"}
WGI = [("GOV_WGI_PV.EST", "stability"), ("GOV_WGI_RL.EST", "ruleOfLaw"), ("GOV_WGI_RQ.EST", "regulatory")]


def risk_tier(s):
    """Governance tier from the weakest WGI estimate (World Bank publishes approx. -2.5 weak to +2.5 strong; no Taiwan series)."""
    v = [s.get(k) for _, k in WGI if isinstance(s.get(k), (int, float))]
    if not v:
        return "unrated"
    m = min(v)
    return "high" if m < -1 else "elevated" if m < -0.25 else "moderate" if m < 0.75 else "low"


def latest_by_country(body):
    rows = body[1] if isinstance(body, list) and len(body) > 1 and isinstance(body[1], list) else []
    out = {}
    for r in rows:
        if r.get("value") is None or not r.get("countryiso3code"):
            continue
        cur = out.get(r["countryiso3code"])
        if not cur or r["date"] > cur["date"]:
            out[r["countryiso3code"]] = {"value": r["value"], "date": r["date"]}
    return out


def country_scores(countries):
    isos = sorted({ISO3[c] for c in countries if c in ISO3})
    got = {}
    if isos:
        lst = ";".join(isos)
        for ind, key in WGI + [("NY.GDP.MKTP.KD.ZG", "gdpGrowth")]:
            src = "&source=3" if ind.startswith("GOV_") else ""
            url = f"https://api.worldbank.org/v2/country/{lst}/indicator/{ind}?format=json&mrv=3&per_page=1000{src}"
            try:
                got[key] = latest_by_country(json.loads(request(url, timeout=60, retries=2)))
            except Exception as e:
                print("worldbank", ind, str(e)[:120])
                got[key] = {}
    out = {}
    for c in countries:
        iso = ISO3.get(c)
        s = {k: (got.get(k, {}).get(iso, {}) or {}).get("value") if iso else None for _, k in WGI + [(None, "gdpGrowth")]}
        out[c] = {**s, "iso3": iso, "year": (got.get("stability", {}).get(iso) or {}).get("date") if iso else None, "tier": risk_tier(s),
                  "source": "World Bank Worldwide Governance Indicators and World Development Indicators (api.worldbank.org/v2)",
                  "url": f"https://data.worldbank.org/country/{iso.lower()}" if iso else None, "retrieved": now_iso()}
    return out


def fr_kind(title):
    t = title.lower()
    return ("export_controls" if re.search(r"export|entity list|commerce control", t) else "tariffs" if re.search(r"tariff|duty|duties|section 301|section 232|antidumping|countervailing", t)
            else "sanctions" if re.search(r"sanction|blocking|ofac|designation", t) else "trade")


def federal_register(country, since=None, per_page=20):
    """U.S. trade, export-control and sanctions documents naming `country` in the last 12 months, with a verbatim
    sentence from each abstract that names the country."""
    since = since or (dt.date.today() - dt.timedelta(days=365)).isoformat()
    p = [("per_page", str(per_page)), ("order", "newest"), ("conditions[term]", f'"{country}"'), ("conditions[publication_date][gte]", since)]
    p += [("conditions[agencies][]", a) for a in FR_AGENCIES]
    p += [("fields[]", f) for f in ("title", "abstract", "publication_date", "html_url", "agencies", "type", "document_number")]
    url = "https://www.federalregister.gov/api/v1/documents.json?" + urllib.parse.urlencode(p)
    d = json.loads(request(url, timeout=60, retries=2))
    out = []
    for r in d.get("results", []):
        abstract = r.get("abstract") or ""
        q = next((s for s in sentences(abstract) if country.lower() in s.lower() and 8 <= len(words(s)) <= 60), None)
        if q and len(words(q)) > 40:
            toks = q.split()
            i = next((k for k, w in enumerate(toks) if country.split()[0].lower() in w.lower()), 0)
            lo = max(0, min(i - 12, len(toks) - 40))
            q = " ".join(toks[lo:lo + 40])
        title = r.get("title") or ""
        if not q and country.lower() in title.lower() and len(words(title)) >= 5:
            q, qsrc = " ".join(title.split()[:40]), "title"
        else:
            qsrc = "abstract"
        out.append({"id": r.get("document_number"), "title": r.get("title"), "qsrc": qsrc, "date": r.get("publication_date"), "type": r.get("type"),
                    "agency": next((a.get("name") or a.get("raw_name") for a in reversed(r.get("agencies") or []) if a.get("name") or a.get("raw_name")), "Federal Register"),
                    "url": r.get("html_url"), "abstract": abstract, "q": q if q and (quote_ok(q, norm(abstract)) or (qsrc == "title" and norm(q) in norm(title))) else None, "kind": fr_kind(r.get("title") or ""),
                    "country": country, "source": "Federal Register API (federalregister.gov/api/v1)", "retrieved": now_iso()})
    return {"total": d.get("count", 0), "items": out, "query": url}


# ---------------------------------------------------------------- relationship news (cron: Tavily + Firecrawl)

def name_variants(name, ticker=None):
    base = re.sub(r"\b(inc|corp|corporation|co|ltd|limited|plc|llc|lp|ag|sa|nv|holdings?|group|company|the)\b", "", re.sub(r"[.,]", "", name.lower()))
    base = re.sub(r"\s+", " ", base).strip()
    ws = base.split()
    out = {base}
    if len(ws) > 2:
        out.add(" ".join(ws[:2]))
    if ws and len(ws[0]) >= 5:
        out.add(ws[0])
    if "taiwan semiconductor" in base or base == "tsmc":
        out |= {"tsmc", "taiwan semiconductor"}
    if ticker and len(ticker) >= 3:
        out.add(ticker.lower())
    return [v for v in out if len(v) >= 3]


def cooccurring(text, a, b, limit=4):
    """Verbatim sentences (20-400 chars) of `text` that name both companies."""
    def has(s, vs):
        return any(re.search(r"\b" + re.escape(v) + r"\b", s, re.I) for v in vs)
    out = []
    for s in sentences(re.sub(r"[ \t]+", " ", text or "").replace("\r", "")):
        t = re.sub(r"^[#>*\-\s]+", "", s.strip())
        if not (20 <= len(t) <= 400) or re.search(r"\]\(|https?:", t):
            continue
        if has(t, a) and has(t, b) and t not in out:
            out.append(t)
        if len(out) >= limit:
            break
    return out


def tavily_search(query, days=None, max_results=6, topic="general"):
    body = {"api_key": os.environ["TAVILY_API_KEY"], "query": query, "max_results": max_results, "search_depth": "basic", "topic": topic}
    if days:
        body["days"] = days
    return json.loads(request("https://api.tavily.com/search", method="POST", data=body, timeout=45, retries=1)).get("results", [])


def corroborate(link, filer_name, cp_name, search=tavily_search):
    """Recent web coverage naming both companies in one sentence; each kept sentence is verbatim in the page text."""
    a, b = name_variants(filer_name, link.get("t")), name_variants(cp_name, link.get("cpt"))
    rel = {"customer": "customer contract", "supplier": "supplier agreement"}.get(link["rel"], link["rel"])
    seen, out = set(), []
    for q, kw in ((f'"{filer_name}" "{cp_name}" {rel}', {}), (f"{filer_name} {cp_name} {link.get('prod') or ''}".strip(), {"topic": "news", "days": 365})):
        try:
            results = search(q, **kw)
        except Exception as e:
            print("tavily", q[:60], str(e)[:120])
            continue
        for r in results:
            url = r.get("url") or ""
            if url in seen or "sec.gov" in url:
                continue
            seen.add(url)
            text = r.get("content") or ""
            for s in cooccurring(text, a, b, 2):
                if len(words(s)) >= 8:
                    out.append({"link": link["id"], "url": url, "title": r.get("title"), "date": (r.get("published_date") or "")[:10] or None,
                                "host": urllib.parse.urlparse(url).netloc.replace("www.", ""), "q": " ".join(s.split()[:40]),
                                "text": text[:4000], "provider": "Tavily search", "retrieved": now_iso()})
    return out[:6]


# ---------------------------------------------------------------- earnings sympathy and basket inputs

def earnings_dates(events, since):
    """8-K filings with Item 2.02 (results of operations) on or after `since`, from tech/events/{T}.json."""
    cols = events.get("cols") or []
    ix = {c: i for i, c in enumerate(cols)}
    out = []
    for r in events.get("filings", []):
        if r[ix["form"]].startswith("8-K") and "2.02" in (r[ix["items"]] or "") and r[ix["filed"]] >= since:
            out.append({"date": r[ix["filed"]], "accn": r[ix["accession"]], "doc": r[ix["primary_doc"]], "cik": r[ix["cik"]]})
    return sorted({d["date"]: d for d in out}.values(), key=lambda d: d["date"])


def closes_of(bars):
    return list(zip(bars.get("d") or [], bars.get("c") or []))


def sympathy(focus, focus_bars, focus_events, peers, years=5):
    """Average move of each covered counterparty over the two sessions around the focus company's earnings releases."""
    since = (dt.date.today() - dt.timedelta(days=365 * years)).isoformat()
    dates = earnings_dates(focus_events, since)
    fc = closes_of(focus_bars)
    rows = []
    for p in peers:
        if not p.get("bars"):
            continue
        s = spillover(fc, closes_of(p["bars"]), [d["date"] for d in dates])
        if s:
            rows.append({"t": p["t"], "rel": p["rel"], **{k: (round(v, 6) if isinstance(v, float) else v) for k, v in s.items() if k != "rows"},
                         "rows": [[r["date"], round(r["focus"], 6), round(r["other"], 6)] for r in s["rows"]]})
    rows.sort(key=lambda r: -r["meanAbs"])
    return {"dates": dates, "rows": rows, "years": years, "method": "close before release date to close after it (eventReturn)",
            "source": "tech/bars (split-adjusted daily closes) and tech/events (8-K Item 2.02 filing dates)"}


def basket_series(symbols, bars_by_symbol, days=756):
    """Aligned daily closes over the last `days` sessions for the ecosystem basket (computed in the browser)."""
    ref = bars_by_symbol.get("SPY") or next(iter(bars_by_symbol.values()), None)
    if not ref:
        return None
    dates = (ref.get("d") or [])[-days:]
    out = {}
    for s in symbols:
        b = bars_by_symbol.get(s)
        if not b:
            continue
        m = dict(zip(b.get("d") or [], b.get("c") or []))
        out[s] = [round(m[d], 4) if d in m and m[d] else None for d in dates]
    return {"dates": dates, "c": out}
