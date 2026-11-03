"""ETL jobs. Each job is resumable and writes both Postgres rows and the JSON bundles the site reads."""
import datetime as dt
import json
import time
from concurrent.futures import ThreadPoolExecutor

from . import docs, fundamentals, macro, news, prices, sec, summaries
from .common import Run, Supabase, now_iso
from .universe import BY_TICKER, MARKET_SYMBOLS, SECTOR_ETF, UNIVERSE

IR_URLS = {
    "AAPL": "https://investor.apple.com/", "MSFT": "https://www.microsoft.com/en-us/investor", "NVDA": "https://investor.nvidia.com/",
    "AMZN": "https://ir.aboutamazon.com/", "GOOGL": "https://abc.xyz/investor/", "META": "https://investor.atmeta.com/", "TSLA": "https://ir.tesla.com/",
    "COST": "https://investor.costco.com/", "JPM": "https://www.jpmorganchase.com/ir", "AVGO": "https://investors.broadcom.com/",
}
ITEMS = {"1.01": "Material agreement", "2.02": "Results of operations", "2.03": "Debt obligation", "5.02": "Officer/director change", "5.03": "Bylaw amendment",
         "5.07": "Shareholder vote", "7.01": "Reg FD disclosure", "8.01": "Other events", "9.01": "Exhibits"}


def describe(f):
    if f["f"] == "10-K":
        return "Annual report"
    if f["f"] == "10-Q":
        return "Quarterly report"
    if f["f"].startswith("8-K"):
        return " · ".join(ITEMS.get(x, "Item " + x) for x in (f["i"] or "").split(",") if x and x != "9.01") or "Current report"
    return {"4": "Insider transaction", "144": "Proposed insider sale", "DEF 14A": "Proxy statement", "S-8": "Employee plan registration",
            "SC 13G": "Beneficial owner >5%", "SC 13G/A": "Beneficial owner >5%", "3": "Initial insider holdings"}.get(f["f"], f["x"] or f["f"])


def _cache(sb, path, default):
    try:
        return sb.get_json(path) or default
    except Exception:
        return default


# ---------------------------------------------------------------- companies
def process_company(sb, tk, refresh_news=False, refresh_summary=False, fetch_docs=True, store_raw=True, log=print):
    u = BY_TICKER[tk]
    cik = u["cik"]
    prev = _cache(sb, f"companies/{tk}.json", {})
    sub = sec.submissions(cik)
    cf = sec.companyfacts(cik)
    facts = cf["facts"]
    if store_raw:
        sb.put_raw(f"sec/companyfacts/CIK{cik:010d}.json", json.dumps(cf), ticker=tk, kind="companyfacts", source_url=f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json")
    filings = sec.filing_list(sub)
    statements = {k: sec.build_series(facts, k) for k in sec.CONCEPTS}
    prof = sec.profile(sub, facts)
    ftables = sec.filing_tables(facts, statements, filings, cik)
    earn = [{"filed": f["d"], "accn": f["a"], "url": sec.filing_url(cik, f["a"], f["p"])} for f in filings if f["f"] == "8-K" and "2.02" in (f["i"] or "")][:12]

    # original statement tables for the latest 10-K and 10-Q
    ix = dict(prev.get("ix") or {})
    targets = [f for f in filings if f["f"] == "10-K"][:1] + [f for f in filings if f["f"] == "10-Q"][:1]
    keep = {f["a"] for f in targets}
    ix = {a: v for a, v in ix.items() if a in keep}
    if fetch_docs:
        for f in targets:
            if f["a"] in ix:
                continue
            url = sec.filing_url(cik, f["a"], f["p"])
            try:
                html = docs.firecrawl(url, "rawHtml").get("rawHtml", "")
                if store_raw and html:
                    sb.put_raw(f"sec/filings/{f['a']}/{f['p']}", html, ticker=tk, accession=f["a"], kind="filing_html", source_url=url)
                tabs = docs.ix_tables(html)
                if tabs:
                    ix[f["a"]] = {"form": f["f"], "url": url, "tables": tabs}
            except Exception as e:
                log(f"  {tk} ix {f['a']} failed: {str(e)[:160]}")

    # latest earnings release text
    release = prev.get("release")
    if fetch_docs and earn and (not release or release.get("accn") != earn[0]["accn"]):
        e0 = earn[0]
        f0 = next(f for f in filings if f["a"] == e0["accn"])
        try:
            url, md = docs.release_markdown(sec.filing_url(cik, e0["accn"]), f0["p"])
            if md:
                release = {"filed": e0["filed"], "accn": e0["accn"], "url": url, "markdown": md}
                if store_raw:
                    sb.put_raw(f"sec/releases/{e0['accn']}.md", md, ticker=tk, accession=e0["accn"], kind="earnings_release", source_url=url)
        except Exception as e:
            log(f"  {tk} release failed: {str(e)[:160]}")

    coverage = prev.get("news") or {}
    if refresh_news or not coverage.get("items"):
        try:
            coverage = {"items": news.company_news(u["name"], tk), "fetched_at": now_iso()}
        except Exception as e:
            log(f"  {tk} news failed: {str(e)[:160]}")

    summary = prev.get("summary")
    release_changed = (release or {}).get("accn") != ((prev.get("release") or {}).get("accn"))
    if refresh_summary or not summary or release_changed or refresh_news:
        try:
            summary = summaries.company(u["name"], tk, statements, prof["fiscalYearEnd"], (release or {}).get("markdown"), coverage.get("items", []))
        except Exception as e:
            log(f"  {tk} summary failed: {str(e)[:160]}")

    bundle = {
        "ticker": tk, "cik": cik, "displayName": u["name"], "sector": u["sector"], "irUrl": IR_URLS.get(tk), "generated": now_iso(),
        "profile": prof, "statements": statements, "filings": [f for f in filings][:250], "filingTables": ftables,
        "earnings8k": earn, "ix": ix, "release": release, "news": coverage, "summary": summary,
    }
    sb.put_json(f"companies/{tk}.json", bundle)

    # relational copies
    sb.upsert("companies", [{
        "ticker": tk, "cik": cik, "name": prof["name"], "display_name": u["name"], "sector": u["sector"], "sic": prof["sic"], "sic_description": prof["sicDescription"],
        "exchange": (prof["exchanges"] or [None])[0], "fiscal_year_end": prof["fiscalYearEnd"], "state_of_inc": prof["stateOfIncorporation"], "address": prof["address"],
        "phone": prof["phone"], "website": prof["website"], "ir_url": IR_URLS.get(tk), "shares_outstanding": (prof["sharesOutstanding"] or {}).get("val"),
        "shares_as_of": (prof["sharesOutstanding"] or {}).get("end"), "latest_accession": filings[0]["a"] if filings else None, "updated_at": now_iso()}], "ticker")
    sb.upsert("filings", [{
        "accession": f["a"], "ticker": tk, "form": f["f"], "filed": f["d"], "report_date": f["r"] or None, "primary_doc": f["p"], "items": f["i"],
        "description": describe(f), "url": sec.filing_url(cik, f["a"], f["p"]), "has_source_tables": f["a"] in ix, "updated_at": now_iso()} for f in filings[:250]], "accession")
    rows = {}
    for stmt, lst in statements.items():
        for r in lst:
            for kind, key in (("A", "annual"), ("Q", "quarterly")):
                for p in r[key]:
                    rows[(stmt, r["label"], kind, p["end"])] = {
                        "ticker": tk, "statement": stmt, "label": r["label"], "kind": kind, "period_end": p["end"], "period_start": p.get("start"),
                        "concept": p.get("c") or r["concept"], "unit": r["unit"], "value": p["v"], "accession": p["accn"], "form": p["form"], "filed": p["filed"], "derived": bool(p.get("derived"))}
    sb.upsert("facts", rows.values(), "ticker,statement,label,kind,period_end", batch=1000)
    if coverage.get("items"):
        lines = (summary or {}).get("news_lines") or {}
        sb.upsert("news_items", [{"url": x["url"], "ticker": tk, "title": x["title"][:500], "source": x["source"], "published_at": _ts(x.get("date")),
                                  "snippet": x["snippet"], "summary_line": lines.get(str(i + 1)), "fetched_at": coverage.get("fetched_at") or now_iso()} for i, x in enumerate(coverage["items"])], "url")
    if summary:
        sb.upsert("summaries", [{"id": f"company:{tk}", "kind": "company", "ticker": tk, "content": summary, "generated_at": summary.get("generated_at") or now_iso()}], "id")

    fund = fundamentals.compute(statements, prof, None)
    return {"ticker": tk, "name": u["name"], "legal_name": prof["name"], "sector": u["sector"], "sic": prof["sicDescription"], "exchange": (prof["exchanges"] or [None])[0],
            "fye": prof["fiscalYearEnd"], "shares": (prof["sharesOutstanding"] or {}).get("val"), "latest_filing": filings[0]["d"] if filings else None,
            "latest_accession": filings[0]["a"] if filings else None, "fund": fund, "has_source_tables": bool(ix), "has_release": bool(release),
            "div_ttm": fundamentals.ttm(statements, "Dividends paid", ("cashflow",)), "bb_ttm": fundamentals.ttm(statements, "Buybacks", ("cashflow",))}


def _ts(s):
    if not s:
        return None
    try:
        from email.utils import parsedate_to_datetime
        return parsedate_to_datetime(s).astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        return s if "T" in s else None


def companies_job(sb, tickers=None, workers=4, refresh_news=False, refresh_summary=False, fetch_docs=True, time_budget=None, only_changed=False, max_companies=None):
    with Run(sb, "companies") as run:
        t0 = time.time()
        fund = _cache(sb, "meta/fundamentals.json", {})
        tickers = tickers or [t for t, *_ in UNIVERSE]
        if only_changed:
            changed = []
            for tk in tickers:
                try:
                    sub = sec.submissions(BY_TICKER[tk]["cik"])
                    latest = sub["filings"]["recent"]["accessionNumber"][0]
                    if (fund.get(tk) or {}).get("latest_accession") != latest:
                        changed.append(tk)
                except Exception as e:
                    print("submissions check failed", tk, e)
                if time_budget and time.time() - t0 > time_budget * 0.25:
                    break
            tickers = changed
        if max_companies:
            tickers = tickers[:max_companies]
        done, failed = [], {}

        def one(tk):
            if time_budget and time.time() - t0 > time_budget:
                return tk, None, "skipped: time budget"
            try:
                return tk, process_company(sb, tk, refresh_news=refresh_news, refresh_summary=refresh_summary, fetch_docs=fetch_docs), None
            except Exception as e:
                return tk, None, str(e)[:300]

        with ThreadPoolExecutor(workers) as ex:
            for tk, res, err in ex.map(one, tickers):
                if res:
                    fund[tk] = res
                    done.append(tk)
                    print("ok", tk, flush=True)
                else:
                    failed[tk] = err
                    print("fail", tk, err, flush=True)
        sb.put_json("meta/fundamentals.json", fund, cache="no-store")
        run.stats.update({"done": len(done), "failed": failed, "tickers": done})
        if done:
            build_index(sb)
            build_feed(sb)
        return done, failed


# ---------------------------------------------------------------- prices
def prices_job(sb, intraday=True, full_history=False):
    with Run(sb, "prices") as run:
        comp = [t for t, *_ in UNIVERSE]
        syms = MARKET_SYMBOLS + comp
        daily, err1 = prices.fetch_many(syms, "2y", "1d")
        intra, err2 = prices.fetch_many(["SPY", "QQQ"] + comp, "5d", "5m") if intraday else ({}, {})
        spy = daily.get("SPY", {}).get("bars")
        pstats = {}
        for s, d in daily.items():
            sb.put_json(f"prices/{prices.path_key(s)}.json", {"meta": d["meta"], "d": d["bars"], "i": (intra.get(s) or {}).get("bars", [])}, cache="max-age=300")
            pstats[s] = prices.stats(d["bars"], spy if s != "SPY" else None)
            pstats[s]["spark"] = [round(b[4], 2) for b in d["bars"][-63:]]
            rows = d["bars"] if full_history else d["bars"][-7:]
            sb.upsert("prices_daily", [{"symbol": s, "d": dt.datetime.fromtimestamp(b[0], dt.timezone.utc).date().isoformat(), "o": b[1], "h": b[2], "l": b[3], "c": b[4], "v": b[5]} for b in rows], "symbol,d", batch=1000)
        market = {"generated": now_iso(), "symbols": {}}
        for s in MARKET_SYMBOLS:
            if s in daily:
                b = daily[s]["bars"]
                market["symbols"][s] = {"bars": [[x[0], x[4]] for x in b[-270:]], "o": b[-1][1], "h": b[-1][2], "l": b[-1][3]}
        sb.put_json("market.json", market, cache="max-age=300")
        sb.put_json("meta/pricestats.json", pstats, cache="no-store")
        run.stats.update({"symbols": len(daily), "errors": {**err1, **{k + "@5m": v for k, v in err2.items()}}})
        build_index(sb, pstats=pstats)
        return len(daily), {**err1, **err2}


# ---------------------------------------------------------------- index + feed
def build_index(sb, pstats=None):
    fund = _cache(sb, "meta/fundamentals.json", {})
    pstats = pstats or _cache(sb, "meta/pricestats.json", {})
    rows, metrics = [], []
    for tk, *_ in UNIVERSE:
        f = fund.get(tk)
        if not f:
            continue
        ps = pstats.get(tk) or {}
        fd = dict(f.get("fund") or {})
        price, shares = ps.get("price"), f.get("shares")
        mcap = price * shares if price and shares else None
        rev, eps, fcf = fd.get("revenue_ttm"), fd.get("eps_ttm"), fd.get("fcf_ttm")
        m = {
            "ticker": tk, "as_of": ps.get("as_of"), "price": price, "chg_1d": ps.get("chg_1d"), "ret_1m": ps.get("ret_1m"), "ret_ytd": ps.get("ret_ytd"), "ret_1y": ps.get("ret_1y"),
            "market_cap": mcap, "pe_ttm": price / eps if price and eps and eps > 0 else None, "ps_ttm": mcap / rev if mcap and rev else None, "eps_ttm": eps,
            "revenue_ttm": rev, "net_income_ttm": fd.get("net_income_ttm"), "fcf_ttm": fcf, "fcf_yield": fcf / mcap if fcf is not None and mcap else None,
            "gross_margin_lq": fd.get("gross_margin_lq"), "op_margin_lq": fd.get("op_margin_lq"), "net_margin_ttm": fd.get("net_margin_ttm"), "rev_growth_lq": fd.get("rev_growth_lq"),
            "dividend_yield": None, "buyback_yield": None, "beta_1y": ps.get("beta_1y"), "vol_60d": ps.get("vol_60d"), "high_52w": ps.get("high_52w"), "low_52w": ps.get("low_52w"),
            "latest_quarter": fd.get("latest_quarter"), "latest_quarter_end": fd.get("latest_quarter_end"), "updated_at": now_iso(),
        }
        if f.get("div_ttm") is not None and mcap:
            m["dividend_yield"] = f["div_ttm"] / mcap
        if f.get("bb_ttm") is not None and mcap:
            m["buyback_yield"] = f["bb_ttm"] / mcap
        metrics.append({k: (None if isinstance(v, float) and v != v else v) for k, v in m.items()})
        rows.append({**{k: v for k, v in m.items() if k not in ("updated_at",)}, "name": f["name"], "legal_name": f.get("legal_name"), "sector": f["sector"], "sic": f.get("sic"),
                     "exchange": f.get("exchange"), "fye": f.get("fye"), "latest_filing": f.get("latest_filing"), "has_source_tables": f.get("has_source_tables"), "spark": ps.get("spark", [])})
    if metrics:
        sb.upsert("company_metrics", metrics, "ticker")
    sb.put_json("index.json", {"generated": now_iso(), "sectors": SECTOR_ETF, "companies": rows}, cache="max-age=120")
    return len(rows)


def build_feed(sb, limit=400):
    rows = sb.select("filings", f"select=accession,ticker,form,filed,report_date,primary_doc,items,description,url&order=filed.desc,accession.desc&limit={limit}")
    sb.put_json("feed.json", {"generated": now_iso(), "filings": rows[:limit]}, cache="max-age=300")
    return len(rows)


# ---------------------------------------------------------------- macro
def macro_job(sb, refresh_news=True, brief=True):
    with Run(sb, "macro") as run:
        prev = _cache(sb, "macro.json", {})
        curve = macro.treasury_curve()
        fred = macro.fred()
        try:
            effr = macro.effr_monthly()
        except Exception as e:
            print("effr failed", e)
            effr = prev.get("effr", [])
        items = prev.get("news", [])
        if refresh_news:
            try:
                items = news.macro_news()
            except Exception as e:
                print("macro news failed", e)
        b = prev.get("brief")
        if brief and items:
            try:
                b = summaries.macro_brief(curve[-1], macro.CURVE_HDR, effr[-6:], fred.get("CPIAUCSL", [])[-6:], items)
            except Exception as e:
                print("macro brief failed", e)
        out = {"generated": now_iso(), "curve_hdr": macro.CURVE_HDR, "curve": curve, "fred": fred, "effr": effr, "news": items, "brief": b}
        sb.put_json("macro.json", out, cache="max-age=600")
        obs = []
        for sid, pts in fred.items():
            obs += [{"series": "FRED:" + sid, "d": d, "value": v} for d, v in pts]
        obs += [{"series": "FED:EFFR", "d": d, "value": v} for d, v in effr]
        i10, i2 = macro.CURVE_HDR.index("10 Yr") + 1, macro.CURVE_HDR.index("2 Yr") + 1
        obs += [{"series": "TSY:10Y", "d": r[0], "value": r[i10]} for r in curve if r[i10] is not None]
        obs += [{"series": "TSY:2Y", "d": r[0], "value": r[i2]} for r in curve if r[i2] is not None]
        sb.upsert("macro_observations", obs, "series,d", batch=1000)
        if b:
            sb.upsert("summaries", [{"id": "macro:brief", "kind": "macro", "content": b, "generated_at": b.get("generated_at") or now_iso()}], "id")
        if items:
            sb.upsert("news_items", [{"url": x["url"], "ticker": "MACRO", "title": x["title"][:500], "source": x["source"], "published_at": _ts(x.get("date")), "snippet": x["snippet"], "fetched_at": now_iso()} for x in items], "url")
        run.stats.update({"curve_rows": len(curve), "fred": {k: len(v) for k, v in fred.items()}, "news": len(items)})
        return out


# ---------------------------------------------------------------- rotation for news + summaries
def news_rotation_job(sb, per_run=12, workers=4, time_budget=240):
    state = _cache(sb, "meta/rotation.json", {"cursor": 0})
    tickers = [t for t, *_ in UNIVERSE]
    start = state.get("cursor", 0) % len(tickers)
    batch = (tickers[start:] + tickers[:start])[:per_run]
    done, failed = companies_job(sb, batch, workers=workers, refresh_news=True, fetch_docs=False, time_budget=time_budget)
    state["cursor"] = (start + len(done) + len(failed)) % len(tickers)
    state["updated_at"] = now_iso()
    sb.put_json("meta/rotation.json", state, cache="no-store")
    return done, failed
