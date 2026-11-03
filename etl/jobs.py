"""ETL jobs. Each job is resumable and writes both Postgres rows and the JSON bundles the site reads."""
import datetime as dt
import json
import time
from concurrent.futures import ThreadPoolExecutor

from . import docs, fundamentals, insiders, macro, news, prices, sec, summaries
from .common import Run, Supabase, now_iso
from .universe import BY_TICKER, MARKET_SYMBOLS, SECTOR_ETF, SP100, UNIVERSE

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
    try:
        sec.fill_gaps(cik, facts, sub, log=log)
    except Exception as e:
        log(f"  {tk} gap filler failed: {str(e)[:200]}")
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
                html = docs.filing_html(url)
                if store_raw and html:
                    sb.put_raw(f"sec/filings/{f['a']}/{f['p']}", html, ticker=tk, accession=f["a"], kind="filing_html", source_url=url)
                if html:
                    try:
                        docs.publish_filing(sb, html, cik, f["a"])
                    except Exception as e:
                        log(f"  {tk} publish {f['a']} failed: {str(e)[:160]}")
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
            "latest_accession": filings[0]["a"] if filings else None, "material_accession": material_accession(filings), "fund": fund, "has_source_tables": bool(ix), "has_release": bool(release),
            "div_ttm": fundamentals.ttm(statements, "Dividends paid", ("cashflow",)), "bb_ttm": fundamentals.ttm(statements, "Buybacks", ("cashflow",))}


def _ts(s):
    if not s:
        return None
    try:
        from email.utils import parsedate_to_datetime
        return parsedate_to_datetime(s).astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        return s if "T" in s else None


MATERIAL = ("10-K", "10-Q", "10-K/A", "10-Q/A", "8-K", "20-F", "40-F", "6-K")


def material_accession(filings):
    return next((f["a"] for f in filings if f["f"] in MATERIAL), None)


def detect_changes(sb, tickers, fund, deadline):
    """Checks SEC submissions for each ticker. New material filings (10-K, 10-Q, 8-K) mark the company for
    reprocessing; every other new filing (Form 4, 144, proxies) is written straight to the filings table."""
    state = _cache(sb, "meta/check_cursor.json", {"cursor": 0})
    start = state.get("cursor", 0) % max(len(tickers), 1)
    order = tickers[start:] + tickers[:start]
    changed, checked, light = [], 0, []

    def check(tk):
        if time.time() > deadline:
            return tk, None, None
        try:
            sub = sec.submissions(BY_TICKER[tk]["cik"])
            return tk, sec.filing_list(sub), None
        except Exception as e:
            return tk, None, str(e)[:160]

    with ThreadPoolExecutor(4) as ex:
        for tk, fl, err in ex.map(check, order):
            if fl is None:
                if err:
                    print("submissions check failed", tk, err)
                continue
            checked += 1
            prev = fund.get(tk) or {}
            mat = material_accession(fl)
            known = prev.get("material_accession") or prev.get("latest_accession")
            if not prev or (mat and mat != known):
                changed.append(tk)
            elif fl and fl[0]["a"] != prev.get("latest_accession"):
                cik = BY_TICKER[tk]["cik"]
                for f in fl[:40]:
                    if f["a"] == prev.get("latest_accession"):
                        break
                    light.append({"accession": f["a"], "ticker": tk, "form": f["f"], "filed": f["d"], "report_date": f["r"] or None, "primary_doc": f["p"],
                                  "items": f["i"], "description": describe(f), "url": sec.filing_url(cik, f["a"], f["p"]), "updated_at": now_iso()})
                prev["latest_accession"] = fl[0]["a"]
                prev["latest_filing"] = fl[0]["d"]
    if light:
        sb.upsert("filings", light, "accession")
    state["cursor"] = (start + checked) % max(len(tickers), 1)
    state["updated_at"] = now_iso()
    sb.put_json("meta/check_cursor.json", state, cache="no-store")
    print(f"checked {checked}/{len(tickers)}, {len(changed)} with new reports, {len(light)} other filings synced")
    return changed


def companies_job(sb, tickers=None, workers=4, refresh_news=False, refresh_summary=False, fetch_docs=True, time_budget=None, only_changed=False, max_companies=None):
    with Run(sb, "companies") as run:
        t0 = time.time()
        fund = _cache(sb, "meta/fundamentals.json", {})
        tickers = tickers or [t for t, *_ in UNIVERSE]
        if only_changed:
            tickers = detect_changes(sb, tickers, fund, deadline=t0 + (time_budget or 600) * 0.4)
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
                    if len(done) % 20 == 0:
                        sb.put_json("meta/fundamentals.json", fund, cache="no-store")
                else:
                    failed[tk] = err
                    print("fail", tk, err, flush=True)
        sb.put_json("meta/fundamentals.json", fund, cache="no-store")
        run.stats.update({"done": len(done), "failed": failed, "tickers": done})
        build_index(sb)
        build_feed(sb)
        return done, failed


# ---------------------------------------------------------------- prices
def prices_job(sb, intraday=True, full_history=False, part=None, parts=1, time_budget=None):
    """Daily and 5-minute bars. With parts > 1 the stock list is split so each run fits a function time limit;
    part 0 also carries the market symbols. Each part writes its own stats file, merged by build_index."""
    with Run(sb, "prices") as run:
        comp = [t for t, *_ in UNIVERSE]
        if part is not None and parts > 1:
            comp = comp[part::parts]
        with_market = part in (None, 0)
        syms = (MARKET_SYMBOLS if with_market else []) + comp
        t0 = time.time()
        daily, err1 = prices.fetch_many(syms, "2y", "1d", deadline=t0 + time_budget * 0.6 if time_budget else None)
        intra, err2 = prices.fetch_many((["SPY", "QQQ"] if with_market else []) + comp, "5d", "5m", deadline=t0 + time_budget * 0.8 if time_budget else None) if intraday else ({}, {})
        spy = daily.get("SPY", {}).get("bars")
        if not spy:
            try:
                spy = prices.chart("SPY", "2y", "1d")["bars"]
            except Exception:
                spy = None
        pstats, db_rows = {}, []
        for s_, d in daily.items():
            pstats[s_] = prices.stats(d["bars"], spy if s_ != "SPY" else None)
            pstats[s_]["spark"] = [round(b[4], 2) for b in d["bars"][-63:]]
            rows = d["bars"] if full_history else d["bars"][-7:]
            db_rows += [{"symbol": s_, "d": dt.datetime.fromtimestamp(b[0], dt.timezone.utc).date().isoformat(), "o": b[1], "h": b[2], "l": b[3], "c": b[4], "v": b[5]} for b in rows]

        def put(item):
            s_, d = item
            i_bars = (intra.get(s_) or {}).get("bars")
            if i_bars is None and intraday:
                prev = _cache(sb, f"prices/{prices.path_key(s_)}.json", {}) or {}
                i_bars = prev.get("i", [])
            sb.put_json(f"prices/{prices.path_key(s_)}.json", {"meta": d["meta"], "d": d["bars"], "i": i_bars or []}, cache="max-age=300")

        with ThreadPoolExecutor(8) as ex:
            list(ex.map(put, daily.items()))
        if db_rows:
            sb.upsert("prices_daily", db_rows, "symbol,d", batch=2000)
        if with_market:
            market = {"generated": now_iso(), "symbols": {}}
            for s_ in MARKET_SYMBOLS:
                if s_ in daily:
                    b = daily[s_]["bars"]
                    market["symbols"][s_] = {"bars": [[x[0], x[4]] for x in b[-270:]], "o": b[-1][1], "h": b[-1][2], "l": b[-1][3]}
            sb.put_json("market.json", market, cache="max-age=300")
        key = "all" if part is None or parts <= 1 else f"p{part}"
        sb.put_json(f"meta/pricestats/{key}.json", {"generated": now_iso(), "parts": parts, "stats": pstats}, cache="no-store")
        run.stats.update({"part": key, "symbols": len(daily), "errors": {**err1, **{k + "@5m": v for k, v in err2.items()}}})
        build_index(sb)
        return len(daily), {**err1, **err2}


def load_pricestats(sb):
    """Merges per-part stats files, keeping the newest bar date per symbol."""
    merged = {}
    sources = [_cache(sb, "meta/pricestats.json", {})]
    for key in ["all"] + [f"p{i}" for i in range(8)]:
        doc = _cache(sb, f"meta/pricestats/{key}.json", None)
        if doc:
            sources.append(doc.get("stats") or {})
    for src in sources:
        for sym, st in src.items():
            if not isinstance(st, dict):
                continue
            cur = merged.get(sym)
            if not cur or (st.get("as_of") or "") >= (cur.get("as_of") or ""):
                merged[sym] = st
    return merged


# ---------------------------------------------------------------- index + feed
def _ins_net(entry):
    w = (entry or {}).get("w90") or {}
    if not w:
        return None
    return (w.get("buy_value") or 0) - (w.get("sell_value") or 0)


def build_index(sb, pstats=None):
    fund = _cache(sb, "meta/fundamentals.json", {})
    pstats = pstats or load_pricestats(sb)
    ins = (_cache(sb, "meta/insiders.json", {}) or {}).get("companies", {})
    logos = (_cache(sb, "meta/logos.json", {}) or {}).get("tickers", {})
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
                     "exchange": f.get("exchange"), "fye": f.get("fye"), "latest_filing": f.get("latest_filing"), "has_source_tables": f.get("has_source_tables"), "sp100": tk in SP100, "logo": tk in logos, "insider_net_90d": _ins_net(ins.get(tk)), "spark": ps.get("spark", [])})
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


# ---------------------------------------------------------------- insiders
def insiders_job(sb, tickers=None, days=365, per_company=120, workers=4, time_budget=None):
    """Parses new Form 4 filings into insider_transactions and rewrites insiders/{T}.json for touched companies."""
    t0 = time.time()
    with Run(sb, "insiders") as run:
        cut = (dt.date.today() - dt.timedelta(days=days)).isoformat()
        q = f"select=ticker,accession,primary_doc,filed&form=eq.4&filed=gte.{cut}&order=filed.desc,accession.desc"
        if tickers:
            q += "&ticker=in.(" + ",".join(tickers) + ")"
        filings = sb.select("filings", q)
        known = {r["accession"] for r in sb.select("insider_transactions", f"select=accession&filed=gte.{cut}")}
        per, todo = {}, []
        for f in filings:
            if f["accession"] in known or not f.get("primary_doc"):
                continue
            known.add(f["accession"])
            if per.get(f["ticker"], 0) >= per_company:
                continue
            per[f["ticker"]] = per.get(f["ticker"], 0) + 1
            todo.append(f)
        run.stats["pending"] = len(todo)
        done, failed, buf, touched = 0, 0, [], set(tickers or [])

        def one(f):
            return insiders.fetch(f["ticker"], BY_TICKER[f["ticker"]]["cik"], f)

        with ThreadPoolExecutor(workers) as ex:
            futs = []
            for f in todo:
                if f["ticker"] not in BY_TICKER:
                    continue
                futs.append((f, ex.submit(one, f)))
            for f, fu in futs:
                if time_budget and time.time() - t0 > time_budget:
                    fu.cancel()
                    continue
                try:
                    buf += fu.result()
                    done += 1
                    touched.add(f["ticker"])
                except Exception as e:
                    failed += 1
                    print("form4", f["ticker"], f["accession"], str(e)[:160])
                if len(buf) >= 400:
                    sb.upsert("insider_transactions", list({(r["accession"], r["seq"]): r for r in buf}.values()), "accession,seq")
                    buf = []
        if buf:
            sb.upsert("insider_transactions", list({(r["accession"], r["seq"]): r for r in buf}.values()), "accession,seq")
        index = _cache(sb, "meta/insiders.json", {"companies": {}})
        for tk in sorted(touched):
            rows = sb.select("insider_transactions", f"select=*&ticker=eq.{tk}&filed=gte.{cut}&order=tx_date.desc")
            bundle = insiders.summarize(rows)
            bundle.update({"ticker": tk, "generated": now_iso(), "codes": insiders.CODES})
            sb.put_json(f"insiders/{tk}.json", bundle, cache="max-age=600")
            index["companies"][tk] = {"w90": bundle["w90"], "w365": bundle["w365"],
                                      "last": bundle["rows"][0]["tx_date"] if bundle["rows"] else None}
        index["generated"] = now_iso()
        sb.put_json("meta/insiders.json", index, cache="max-age=600")
        run.stats.update({"parsed": done, "failed": failed, "companies": len(touched)})
        return done, failed


# ---------------------------------------------------------------- filing viewer copies
def publish_filings_job(sb, tickers=None, workers=6):
    """Publishes viewer copies of each company's latest 10-K and 10-Q from the archived originals."""
    import gzip as _gz
    tickers = tickers or [t for t, *_ in UNIVERSE]

    def one(tk):
        b = _cache(sb, f"companies/{tk}.json", {})
        cik = BY_TICKER[tk]["cik"]
        n = 0
        for accn, meta in (b.get("ix") or {}).items():
            f = next((x for x in b.get("filings", []) if x["a"] == accn), None)
            if not f:
                continue
            raw = sb.get_object("raw", f"sec/filings/{accn}/{f['p']}.gz")
            if raw:
                try:
                    html = _gz.decompress(raw).decode("utf-8", "replace")
                except OSError:
                    html = raw.decode("utf-8", "replace")
            else:
                html = docs.filing_html(sec.filing_url(cik, accn, f["p"]))
            if html:
                docs.publish_filing(sb, html, cik, accn)
                n += 1
        return tk, n

    total = 0
    with ThreadPoolExecutor(workers) as ex:
        for tk, n in ex.map(one, tickers):
            total += n
    return total


# ---------------------------------------------------------------- segments (XBRL dimensions)
def _viewer_or_fetch(sb, cik, f):
    """Filing HTML for parsing: the stored viewer copy when present, otherwise EDGAR (and store the viewer copy)."""
    import gzip as _gz
    try:
        raw = sb.get_object("public-data", f"filings/{f['a']}.html.gz", public=True)
    except Exception:
        raw = None
    if raw:
        try:
            return _gz.decompress(raw).decode("utf-8", "replace")
        except OSError:
            return raw.decode("utf-8", "replace")
    html = docs.filing_html(sec.filing_url(cik, f["a"], f["p"]))
    if html:
        try:
            docs.publish_filing(sb, html, cik, f["a"])
        except Exception as e:
            print("publish failed", f["a"], str(e)[:120])
    return html


def periodic_filings(cik, cut, forms=("10-K", "10-Q")):
    """10-K and 10-Q filings since cut, newest first, following the paged submissions files for heavy filers."""
    sub = sec.submissions(cik)
    out = [f for f in sec.filing_list(sub) if f["f"] in forms and f["d"] >= cut and f.get("p")]
    recent = sub["filings"]["recent"]["filingDate"]
    if recent and min(recent) > cut:
        for extra in sub["filings"].get("files", []):
            if extra.get("filingTo", "9999") < cut:
                continue
            page = sec.sec_json(f"https://data.sec.gov/submissions/{extra['name']}")
            out += [f for f in sec.filing_list({"filings": {"recent": page}}) if f["f"] in forms and f["d"] >= cut and f.get("p")]
    seen, res = set(), []
    for f in sorted(out, key=lambda f: f["d"], reverse=True):
        if f["a"] not in seen:
            seen.add(f["a"])
            res.append(f)
    return res


def segments_for(sb, tk, years=None, log=print):
    from . import segments as S
    years = years or S.TABLE_YEARS
    u = BY_TICKER[tk]
    cik = u["cik"]
    b = _cache(sb, f"companies/{tk}.json", {})
    fye = (b.get("profile") or {}).get("fiscalYearEnd") or "1231"
    cut = (dt.date.today() - dt.timedelta(days=int(365 * years))).isoformat()
    fl = periodic_filings(cik, cut)
    parsed = []
    keep_names = {n for _f, _l, _u, names in S.FAMILIES for n in names}

    def slim(ctx, facts):
        """Only what segment tables use: dimensional facts and the family totals."""
        fs = [x for x in facts if ctx.get(x["ctx"], {}).get("dims") or x["name"].split(":")[-1] in keep_names or S.EXT_METRIC.search(x["name"])]
        used = {x["ctx"] for x in fs}
        return {k: v for k, v in ctx.items() if k in used}, fs

    import gzip as _gz
    for f in fl:
        cache_path = f"segcache/{f['a']}.json.gz"
        try:
            raw = sb.get_object("raw", cache_path)
            if raw:
                c = json.loads(_gz.decompress(raw))
                parsed.append((f, c["ctx"], c["facts"]))
                continue
        except Exception:
            pass
        try:
            facts = None
            # inline XBRL became mandatory for large filers in mid-2019; older reports carry a separate instance file
            if f["d"] >= "2019-06-01":
                html = _viewer_or_fetch(sb, cik, f)
                if html:
                    ctx, facts = S.parse_ixbrl(html)
            if not facts:
                url = S.instance_url(cik, f["a"])
                if url:
                    ctx, facts = S.parse_instance(S.sec_get(url, timeout=120))
            if facts:
                ctx, facts = slim(ctx, facts)
                parsed.append((f, ctx, facts))
                try:
                    sb.put_object("raw", cache_path, _gz.compress(json.dumps({"ctx": ctx, "facts": facts}, separators=(",", ":")).encode()),
                                  "application/gzip", cache="max-age=31536000")
                except Exception as e:
                    log(f"  {tk} cache write {f['a']} failed: {str(e)[:100]}")
        except Exception as e:
            log(f"  {tk} {f['a']} parse failed: {str(e)[:140]}")
    labels = {}
    tenks = [x for x in fl if x["f"] == "10-K"]
    # member labels from older 10-Ks first (members dropped later), then the latest filings override
    older = [next((x for x in tenks if x["d"] < cutoff), None) for cutoff in ("2013-06-01", "2016-06-01", "2019-06-01")]
    for f in [x for x in older if x] + tenks[:1] + [x for x in fl if x["f"] == "10-Q"][:1]:
        try:
            labels.update(S.label_linkbase(cik, f["a"], f["p"]))
        except Exception as e:
            log(f"  {tk} labels {f['a']} failed: {str(e)[:120]}")
    obs, totals = S.collect(parsed)
    tables = S.pick_tables(S.add_residuals(S.build_tables(obs, totals, fye, labels, fundamentals.fy_label)))
    bundle = {"ticker": tk, "generated": now_iso(), "fye": fye, "filings": len(parsed), "tables": tables}
    sb.put_json(f"segments/{tk}.json", bundle, cache="max-age=600")
    rows = []
    for t in tables:
        for r in t["rows"]:
            kid = f"xbrl:{t['id']}:{r['key']}"
            for kind in ("A", "Q", "I"):
                for end, c in r[kind].items():
                    rows.append({"ticker": tk, "kpi_id": kid[:300], "period_kind": kind, "period_end": end, "fiscal_label": c.get("p"), "value": c["v"],
                                 "unit": "USD", "source_kind": "xbrl", "source_id": c.get("accn") or "", "anchor": {"id": c.get("id"), "name": c.get("name"), "ctx": c.get("ctx")},
                                 "derived": c.get("derived"), "reconciled": (t["recon"].get(f"{kind}:{end}") or {}).get("ok"), "updated_at": now_iso()})
    if rows:
        try:
            sb.upsert("kpi_values", rows, "ticker,kpi_id,period_kind,period_end,source_kind", batch=1000)
        except Exception as e:
            log(f"  {tk} kpi_values upsert failed: {str(e)[:160]}")
    ok = sum(1 for t in tables for rc in t["recon"].values() if rc["ok"])
    bad = sum(1 for t in tables for rc in t["recon"].values() if not rc["ok"])
    return {"tables": len(tables), "filings": len(parsed), "recon_ok": ok, "recon_residual": bad}


def segments_job(sb, tickers=None, workers=5, time_budget=None):
    t0 = time.time()
    with Run(sb, "segments") as run:
        tickers = tickers or [t for t, *_ in UNIVERSE]
        done, failed = {}, {}

        def one(tk):
            if time_budget and time.time() - t0 > time_budget:
                return tk, None, "skipped: time budget"
            try:
                return tk, segments_for(sb, tk), None
            except Exception as e:
                return tk, None, str(e)[:300]

        with ThreadPoolExecutor(workers) as ex:
            for tk, res, err in ex.map(one, tickers):
                if res:
                    done[tk] = res
                    print("ok", tk, res, flush=True)
                else:
                    failed[tk] = err
                    print("fail", tk, err, flush=True)
        idx = _cache(sb, "meta/segments.json", {"companies": {}})
        idx["companies"].update(done)
        idx["generated"] = now_iso()
        sb.put_json("meta/segments.json", idx, cache="max-age=600")
        run.stats.update({"done": len(done), "failed": failed})
        return done, failed


# ---------------------------------------------------------------- investor relations documents
def ir_job(sb, tickers=None, workers=4, time_budget=None, refresh_site=False, web=True, sec_years=3):
    from . import ir
    t0 = time.time()
    with Run(sb, "ir") as run:
        tickers = tickers or [t for t, *_ in UNIVERSE]
        done, failed = {}, {}

        def one(tk):
            if time_budget and time.time() - t0 > time_budget:
                return tk, None, "skipped: time budget"
            try:
                b = _cache(sb, f"companies/{tk}.json", {}) or {}
                fye = (b.get("profile") or {}).get("fiscalYearEnd") or "1231"
                ir.PERIOD_ENDS[tk] = sorted({dt.date.fromisoformat(x["end"]) for rows in (b.get("statements") or {}).values() for r in rows
                                             for x in r.get("quarterly", []) + r.get("annual", [])})
                return tk, ir.process_company(sb, tk, BY_TICKER[tk]["name"], BY_TICKER[tk]["cik"], refresh_site=refresh_site, fye=fye, web=web, sec_years=sec_years), None
            except Exception as e:
                return tk, None, str(e)[:300]

        with ThreadPoolExecutor(workers) as ex:
            for tk, res, err in ex.map(one, tickers):
                if res:
                    done[tk] = res
                    print("ok", tk, res, flush=True)
                else:
                    failed[tk] = err
                    print("fail", tk, err, flush=True)
        run.stats.update({"done": len(done), "failed": failed})
        return done, failed


def segments_changed_job(sb, time_budget=240, workers=4):
    """Recomputes segment tables for companies whose latest 10-K/10-Q differs from the one last processed."""
    fund = _cache(sb, "meta/fundamentals.json", {})
    idx = _cache(sb, "meta/segments.json", {"companies": {}})
    todo = [tk for tk, *_ in UNIVERSE if (fund.get(tk) or {}).get("material_accession") and
            (idx["companies"].get(tk) or {}).get("material_accession") != fund[tk]["material_accession"]]
    done, failed = segments_job(sb, todo[:12], workers=workers, time_budget=time_budget)
    idx = _cache(sb, "meta/segments.json", {"companies": {}})
    for tk in done:
        idx["companies"].setdefault(tk, {})["material_accession"] = (fund.get(tk) or {}).get("material_accession")
    sb.put_json("meta/segments.json", idx, cache="max-age=600")
    return done, failed


def ir_rotation_job(sb, per_run=8, time_budget=240):
    """Collects IR documents: companies that reported earnings in the last four days first, then a rolling cursor."""
    recent = sb.select("filings", "select=ticker,items,filed&form=eq.8-K&filed=gte." + (dt.date.today() - dt.timedelta(days=4)).isoformat())
    hot = [r["ticker"] for r in recent if "2.02" in (r.get("items") or "")]
    state = _cache(sb, "meta/ir_rotation.json", {"cursor": 0, "done_hot": {}})
    tickers = [t for t, *_ in UNIVERSE]
    hot = [t for t in dict.fromkeys(hot) if state["done_hot"].get(t) != dt.date.today().isoformat()][:per_run]
    start = state.get("cursor", 0) % len(tickers)
    rest = [t for t in (tickers[start:] + tickers[:start]) if t not in hot][:max(0, per_run - len(hot))]
    done, failed = ir_job(sb, hot + rest, workers=4, time_budget=time_budget)
    for t in hot:
        if t in done:
            state["done_hot"][t] = dt.date.today().isoformat()
    state["cursor"] = (start + len(rest)) % len(tickers)
    state["updated_at"] = now_iso()
    sb.put_json("meta/ir_rotation.json", state, cache="no-store")
    try:
        from .kpitools import build_index
        build_index(sb)
    except Exception as e:
        print("coverage index failed", e)
    return done, failed
