"""Orchestration for the Insiders & influence tab: government, politics and market-flow data per company.

Outputs (public-data):
  gov/{T}.json    congress_trades, lobbying, pac, hearings, sector_hearings ref, regulation, legislation, spending, ai_read
  flows/{T}.json  holders13dg, inst13f, ats_weekly, ats_venues, short_interest
  gov/members.json, gov/laws.json, gov/congress/index.json, gov/congress/unmatched.json, gov/hearings/index.json,
  gov/hearings/sector-{slug}.json, gov/coverage.json
Private raw bucket: govraw/ (PTR PDFs, hearing transcripts, parse caches, entities, state).

Steps (python -m etl.gov_pipeline --step NAME [--tickers ...]):
  entities members congress hearings laws fec company holders f13 market insights coverage
The cron (api/cron/govflows.py) groups them into four schedules with a shared time budget and a rotation cursor.
"""
import argparse
import datetime as dt
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor

from .common import now_iso, request
from .gov_common import (BY_TICKER, Deadline, company_aliases, get_public, get_raw_json, overseeing_committees, put_public,
                         put_raw_json, status, tickers, today, update_company)
from . import gov_congress, gov_entities, gov_fec, gov_hearings, gov_insights, gov_lobby, gov_regs, gov_spending
from . import flows_holders, flows_market

STATE = "govraw/state.json"
PUB = "https://apxfeteebnspwyajlxfh.supabase.co/storage/v1/object/public/public-data/"


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")


def _state(sb):
    return get_raw_json(sb, STATE, {}) or {}


def _save_state(sb, st):
    st["updated_at"] = now_iso()
    put_raw_json(sb, STATE, st)


def _parallel(fn, items, workers=8):
    out = {}
    with ThreadPoolExecutor(workers) as ex:
        for k, v in zip(items, ex.map(fn, items)):
            out[k] = v
    return out


def _ents(sb):
    return gov_entities.load(sb)


def _names(ent, tk):
    return gov_entities.legal_names(ent, tk) or [BY_TICKER[tk]["name"]]


# ---------------------------------------------------------------- shared datasets

def step_entities(sb, tks=None, deadline=None, log=print):
    c = gov_entities.refresh(sb, tks, workers=4, log=log, deadline=deadline)
    return {"entities": len(c), "with_ex21": sum(1 for v in c.values() if v.get("subsidiaries"))}


def step_members(sb, log=print):
    md = gov_congress.load_members(log=log)
    put_raw_json(sb, "govraw/members.json", md)
    gov_congress.publish_members(sb, md)
    return {"members": len(md["members"])}


def _members(sb):
    md = get_raw_json(sb, "govraw/members.json")
    if not md:
        md = gov_congress.load_members()
        put_raw_json(sb, "govraw/members.json", md)
    return md


def step_congress(sb, deadline=None, log=print, years=None, tks=None):
    y = today().year
    years = years or [y - 1, y]
    house = gov_congress.collect_house(sb, years, deadline=deadline, log=log)
    senate = gov_congress.collect_senate(sb, f"{y - 1}-01-01", deadline=deadline, log=log)
    md = _members(sb)
    ent = _ents(sb)
    legal = {tk: gov_entities.legal_names(ent, tk) for tk in tickers()}
    per, unmatched, counts = gov_congress.build_trades(house, senate, md, legal=legal, log=log)
    senate_state = "ok" if senate.get("status") == "ok" else "not_collected"
    senate_note = None if senate_state == "ok" else (
        "Not collected. efdsearch.senate.gov requires the requester to affirm the use restrictions of 5 U.S.C. app. 105(c) "
        "before any report can be read; the pipeline does not affirm this on the owner's behalf. Set FU_EFD_ACKNOWLEDGED=1 "
        "(and FIRECRAWL_API_KEY) to enable it.")
    span = f"House PTRs filed {min(years) if years else ''}-{y}" if years else None
    all_t = tks or tickers()

    def write(tk):
        rows = per.get(tk, [])
        flagged = sum(1 for r in rows if r.get("committee_flag"))
        update_company(sb, "gov", tk, {
            "congress_trades": rows[:200],
            "congress_meta": {"committees_overseeing": [md["committee_names"].get(c, c) for c in overseeing_committees(tk)],
                              "committee_ids": overseeing_committees(tk), "legal_note": gov_congress.LEGAL_NOTE,
                              "flag_basis": "A trade is flagged when the member currently sits on a full committee whose jurisdiction covers the company's sector (mapping in etl/gov_common.py)."},
            "_sources": {"congress_house": status("ok" if rows else "empty", note=span, count=len(rows), url=gov_congress.HOUSE_SEARCH,
                                                  method="Clerk FD index ZIP + PTR PDF text"),
                         "congress_senate": status(senate_state, note=senate_note, url="https://efdsearch.senate.gov/search/"),
                         "committees": status("ok", count=flagged, note="trades by members of overseeing committees",
                                              url="https://github.com/unitedstates/congress-legislators")}})
        return len(rows)
    n = _parallel(write, all_t)
    recent = sorted([r for rows in per.values() for r in rows], key=lambda r: r.get("filed") or "", reverse=True)
    put_public(sb, "gov/congress/index.json", {"generated": now_iso(), "counts": counts, "companies": {tk: len(v) for tk, v in per.items()},
                                               "recent": [{k: r.get(k) for k in ("id", "member", "party", "chamber", "type", "tx_date", "filed", "amount", "pdf_url", "committee_flag")}
                                                          | {"ticker": tk} for tk, rows in per.items() for r in rows[:3]][:400],
                                               "legal_note": gov_congress.LEGAL_NOTE}, cache="max-age=3600")
    put_public(sb, "gov/congress/unmatched.json", {"generated": now_iso(), "count": len(unmatched), "rows": unmatched[:3000]}, cache="max-age=3600")
    return {**counts, "companies_with_trades": sum(1 for v in n.values() if v), "unmatched": len(unmatched), "senate": senate_state}


def step_hearings(sb, deadline=None, log=print, years=None, tks=None, batch=250, write=True, sync=True):
    y = today().year
    years = years or [y - 1, y]
    idx = gov_hearings.sync_corpus(sb, years, deadline=deadline, log=log) if sync else get_raw_json(sb, gov_hearings.INDEX)
    pk = idx["packages"]
    scan_state = get_raw_json(sb, "govraw/chrg/scan.json", {"scanned": [], "companies": {}}) or {"scanned": [], "companies": {}}
    scanned = set(scan_state["scanned"])
    todo = sorted([p for p, m in pk.items() if m.get("status") == "ok" and p not in scanned], key=lambda p: pk[p].get("date") or "", reverse=True)
    ent = _ents(sb)
    all_t = tks or tickers()
    aliases = {tk: company_aliases(tk, _names(ent, tk)) for tk in all_t}
    comp = scan_state["companies"]
    log(f"hearings: {len(todo)} transcripts to scan")
    for i in range(0, len(todo), batch):
        if deadline and deadline.passed(120):
            break
        part = todo[i:i + batch]
        texts = gov_hearings.load_texts(sb, part)
        found = gov_hearings.scan(texts, pk, aliases, log=log)
        for tk, recs in found.items():
            cur = {r["id"]: r for r in comp.get(tk, [])}
            for r in recs:
                cur[r["id"]] = r
            comp[tk] = sorted(cur.values(), key=lambda r: r.get("date") or "", reverse=True)[:40]
        scanned.update(part)
        scan_state["scanned"] = sorted(scanned)
        put_raw_json(sb, "govraw/chrg/scan.json", scan_state)
        log(f"  scanned {min(i + batch, len(todo))}/{len(todo)}; companies with mentions {len(comp)}")
    if not write:
        return {"scanned": len(scanned), "companies": len(comp)}
    sectors = {}
    for tk in all_t:
        info = BY_TICKER[tk]
        defense = "HSAS" in overseeing_committees(tk)
        key = slug(info["sector"]) + ("-defense" if defense else "")
        sectors.setdefault(key, (info["sector"], defense))
    for key, (sector, defense) in sectors.items():
        put_public(sb, f"gov/hearings/sector-{key}.json", {"generated": now_iso(), "sector": sector, "defense": defense,
                                                            "hearings": gov_hearings.sector_hearings(pk, sector, defense, limit=15),
                                                            "method": "GovInfo CHRG MODS titles matched to industry topic terms"}, cache="max-age=3600")
    span = f"GovInfo CHRG transcripts published {min(years)}-{y}; {sum(1 for m in pk.values() if m.get('status') == 'ok')} archived"

    def write(tk):
        recs = comp.get(tk, [])
        slim = []
        for r in recs[:30]:
            slim.append({**r, "quotes": [{**q, "context": q["context"][:1600]} for q in r["quotes"][:3]]})
        defense = "HSAS" in overseeing_committees(tk)
        update_company(sb, "gov", tk, {"hearings": slim, "sector_hearings_path": f"gov/hearings/sector-{slug(BY_TICKER[tk]['sector']) + ('-defense' if defense else '')}.json",
                                       "_sources": {"hearings": status("ok" if recs else "empty", note=span, count=len(recs),
                                                                       url="https://www.govinfo.gov/app/collection/chrg", method="GovInfo sitemap + MODS + HTML transcript; verbatim quote check")}})
        return len(recs)
    n = _parallel(write, all_t)
    put_public(sb, "gov/hearings/index.json", {"generated": now_iso(), "transcripts": sum(1 for m in pk.values() if m.get("status") == "ok"),
                                               "scanned": len(scanned), "companies": {tk: v for tk, v in n.items() if v},
                                               "years": years}, cache="max-age=3600")
    return {"transcripts": len(pk), "scanned": len(scanned), "companies_with_quotes": sum(1 for v in n.values() if v)}


def step_laws(sb, log=print):
    y = today().year
    laws = gov_regs.sync_laws(sb, list(range(2023, y + 1)), log=log)
    return {"laws": len(laws)}


def step_fec(sb, deadline=None, log=print, tks=None):
    y = today().year
    cur = y + (y % 2)
    cycles = [cur, cur - 2]
    data = [gov_fec.load_cycle(c, log=log) for c in cycles]
    md = _members(sb)
    fm = gov_fec.fec_member_map(md["members"])
    ent = _ents(sb)
    all_t = tks or tickers()

    def write(tk):
        e = ent.get(tk) or {}
        p = gov_fec.company_pacs(data, _names(ent, tk), e.get("subsidiaries"), fm, md["members"])
        over = set(overseeing_committees(tk))
        for c in p["cycles"]:
            for r in c["recipients"]:
                r["oversight"] = bool(over & set(r.get("committee_ids") or []))
        update_company(sb, "gov", tk, {"pac": p, "_sources": {
            "pac": status("ok" if p["committees"] else "empty", count=len(p["committees"]), url="https://www.fec.gov/data/browse-data/?tab=bulk-data",
                          note=f"FEC bulk files, cycles {cycles[1]}-{cycles[0]}", method="connected-organization exact match"),
            "employee_contrib": status("not_collected", note="Employee contribution totals need the multi-gigabyte FEC individual contributions file or keyed API access; not collected.")}})
        return len(p["committees"])
    n = _parallel(write, all_t)
    return {"companies_with_pac": sum(1 for v in n.values() if v), "cycles": cycles}


# ---------------------------------------------------------------- per-company rotation (lobbying, regulation, spending)

def company_one(sb, tk, ent, laws, log=print):
    e = ent.get(tk) or {}
    names = _names(ent, tk)
    subs = e.get("subsidiaries") or []
    aliases = company_aliases(tk, names)
    y = today().year
    sections, srcs = {}, {}
    try:
        lob = gov_lobby.company(names, [y - 1, y], log=log)
        sections["lobbying"] = lob
        srcs["lobbying"] = status("ok" if lob["filing_count"] else "empty", count=lob["filing_count"], url="https://lda.gov/filings/public/filing/search/",
                                  note=f"LDA filings {y - 1}-{y}, client = SEC legal name (exact, normalized)")
    except Exception as ex:
        srcs["lobbying"] = status("error", note=str(ex)[:200])
        lob = None
    try:
        reg = gov_regs.federal_register(tk, names, BY_TICKER[tk]["name"], aliases, per_type=20, log=log)
        sections["regulation"] = reg
        srcs["regulation"] = status("ok" if reg["rules"] or reg["proposed"] else "empty", count=len(reg["rules"]) + len(reg["proposed"]),
                                    url="https://www.federalregister.gov/developers/documentation/api/v1", note="Terms: " + ", ".join(reg["terms"]))
    except Exception as ex:
        srcs["regulation"] = status("error", note=str(ex)[:200])
    try:
        from .gov_hearings import SECTOR_TOPICS
        leg = gov_regs.link_laws(laws, lob, SECTOR_TOPICS.get(BY_TICKER[tk]["sector"]))
        sections["legislation"] = leg
        srcs["legislation"] = status("ok" if leg else "empty", count=len(leg), url="https://www.govinfo.gov/app/collection/plaw",
                                     note=f"{sum(1 for l in leg if l['link'] == 'lobbied')} lobbied-on bills became law")
    except Exception as ex:
        srcs["legislation"] = status("error", note=str(ex)[:200])
    try:
        sp = gov_spending.company(names, subs, log=log)
        sections["spending"] = sp
        srcs["spending"] = status("ok" if sp["status"] == "ok" else "empty", count=sp.get("entity_count") or len(sp.get("entities") or []),
                                  url="https://www.usaspending.gov/search",
                                  note={"no_match": "no USAspending recipient matched the legal name or Exhibit 21 subsidiaries",
                                        "no_awards": "matched recipients have no obligations in the period"}.get(sp["status"]))
    except Exception as ex:
        srcs["spending"] = status("error", note=str(ex)[:200])
    sections["aliases"] = {"names": names, "match_terms": [a[0] for a in aliases], "subsidiaries": len(subs),
                           "ex21": e.get("ex21"), "sic": e.get("sic"), "sic_description": e.get("sic_description")}
    sections["_sources"] = srcs
    update_company(sb, "gov", tk, sections)
    return {k: v["status"] for k, v in srcs.items()}


def step_company(sb, tks=None, per_run=40, deadline=None, workers=4, log=print):
    st = _state(sb)
    all_t = tickers()
    explicit = bool(tks)
    if not tks:
        start = st.get("company_cursor", 0) % len(all_t)
        tks = (all_t[start:] + all_t[:start])[:per_run]
    ent = _ents(sb)
    laws = (get_public(sb, "gov/laws.json", {}) or {}).get("laws") or []
    done, failed = {}, {}

    def one(tk):
        if deadline and deadline.passed(60):
            return tk, None, "skipped: time budget"
        try:
            return tk, company_one(sb, tk, ent, laws, log=log), None
        except Exception as ex:
            return tk, None, str(ex)[:200]
    with ThreadPoolExecutor(workers) as ex:
        for i, (tk, res, err) in enumerate(ex.map(one, tks)):
            if res is not None:
                done[tk] = res
            else:
                failed[tk] = err
            if (i + 1) % 50 == 0:
                log(f"  company {i + 1}/{len(tks)}")
    if not explicit:
        st["company_cursor"] = (st.get("company_cursor", 0) + len(done)) % len(all_t)
    _save_state(sb, st)
    return {"done": len(done), "failed": failed, "cursor": st.get("company_cursor")}


# ---------------------------------------------------------------- flows

def step_holders(sb, deadline=None, log=print, tks=None):
    cache = flows_holders.collect_13dg(sb, deadline=deadline, log=log)
    all_t = tks or tickers()

    def write(tk):
        h = flows_holders.holders_for(cache, tk)
        update_company(sb, "flows", tk, {"holders13dg": h, "_sources": {"holders13dg": status(
            "ok" if h["current"] else "empty", count=len(h["current"]), url="https://www.sec.gov/edgar/search/",
            note="Schedule 13D/13G filed about the company since 2025-01-01 (structured XML)")}})
        return len(h["current"])
    n = _parallel(write, all_t)
    return {"filings": len(cache["filings"]), "companies_with_holders": sum(1 for v in n.values() if v)}


def _periods_for(today_d=None):
    """Latest two quarter-ends whose 13F deadline (45 days) has passed and whose data set window has closed."""
    d = today_d or today()
    q_ends = []
    y = d.year
    for yy in (y, y - 1, y - 2):
        for m, day in ((12, 31), (9, 30), (6, 30), (3, 31)):
            q_ends.append(dt.date(yy, m, day))
    q_ends = sorted([q for q in q_ends if q + dt.timedelta(days=100) <= d], reverse=True)
    return [q_ends[1].isoformat(), q_ends[0].isoformat()]


def step_f13(sb, zips=None, log=print, tks=None, force=False):
    """13F holders. zips: local data-set paths (backfill) or None to download the two latest sets."""
    st = _state(sb)
    sets = flows_holders.list_13f_sets()[:2]
    if not force and st.get("f13_sets") == sets and not zips:
        return {"skipped": "no new 13F data set"}
    periods = _periods_for()
    cache = get_raw_json(sb, "govraw/flows/13dg.json", {"filings": {}}) or {"filings": {}}
    c13 = flows_holders.cusips_from_13dg(cache)
    cftd = get_raw_json(sb, "govraw/flows/cusips_ftd.json")
    if not cftd:
        today_d = today()
        per = []
        yy, mm = today_d.year, today_d.month
        for _ in range(24):
            per += [f"{yy}{mm:02d}b", f"{yy}{mm:02d}a"]
            mm -= 1
            if mm == 0:
                yy, mm = yy - 1, 12
        cmap, cnames = flows_holders.cusips_from_ftd(per, log=log)
        cftd = {"cusips": cmap, "names": cnames, "periods": per, "retrieved_at": now_iso()}
        put_raw_json(sb, "govraw/flows/cusips_ftd.json", cftd)
    cusips = {}
    for tk in tickers():
        cs = list(dict.fromkeys((c13.get(tk) or []) + (cftd["cusips"].get(tk) or [])))
        if cs:
            cusips[tk] = cs
    if zips is None:
        from .common import sec_get_raw
        zips = [sec_get_raw(u, timeout=600) for u in sets]
    out, rejected = flows_holders.build_13f(zips, cusips, periods, log=log)
    all_t = tks or tickers()
    retrieved = now_iso()

    def write(tk):
        r = out.get(tk)
        note = None
        if not r:
            note = "no CUSIP found for this ticker" if tk not in cusips else ("issuer name check failed for the CUSIP; not matched" if tk in rejected else "no 13F holders matched")
        sec = {"inst13f": ({**r, "src": {"url": sets[0] if sets else flows_holders.F13_PAGE, "page": flows_holders.F13_PAGE, "retrieved_at": retrieved,
                                          "method": "SEC Form 13F data sets (INFOTABLE by CUSIP; latest original/restated report per manager)"},
                            "cusip_source": "13D/13G cover page" if tk in c13 else "SEC fails-to-deliver file"} if r else None),
               "_sources": {"inst13f": status("ok" if r else "unavailable", count=(r or {}).get("totals", {}).get("managers"), note=note or f"{periods[1]} vs {periods[0]}",
                                              url=flows_holders.F13_PAGE)}}
        update_company(sb, "flows", tk, sec)
        return bool(r)
    n = _parallel(write, all_t)
    st["f13_sets"] = sets
    st["f13_periods"] = periods
    _save_state(sb, st)
    return {"companies_with_13f": sum(1 for v in n.values() if v), "cusips": len(cusips), "rejected": rejected[:40], "periods": periods}


def _bars(tk):
    try:
        p = json.loads(request(PUB + f"prices/{tk}.json", timeout=60, retries=2))
        return p.get("d") or []
    except Exception:
        return []


def step_market(sb, log=print, tks=None):
    per, meta = flows_market.collect(log=log)
    all_t = tks or tickers()

    def write(tk):
        raw = per.get(tk) or {}
        f = flows_market.company_flows(tk, raw, meta, _bars(tk))
        ok_w = sum(1 for w in f["ats_weekly"] if w.get("offex_share") is not None)
        f["_sources"] = {
            "finra_weekly": status("ok" if f["ats_weekly"] else "unavailable", count=len(f["ats_weekly"]), url=flows_market.WEEKLY_PAGE,
                                   note=f"{ok_w} weeks with an off-exchange share"),
            "finra_short_interest": status("ok" if f["short_interest"] else "unavailable", count=len(f["short_interest"]), url=flows_market.SI_PAGE),
            "regsho": status("ok", note="FINRA Reg SHO daily short-sale volume from the Alt data pipeline (altdata/{T}.json)", url=f"altdata/{tk}.json")}
        update_company(sb, "flows", tk, f)
        return len(f["ats_weekly"])
    n = _parallel(write, all_t)
    return {"companies_with_weekly": sum(1 for v in n.values() if v), "latest_weeks": meta["latest_weeks"]}


def step_insights(sb, tks=None, per_run=30, deadline=None, log=print):
    if not os.environ.get("FIREWORKS_API_KEY"):
        return {"skipped": "FIREWORKS_API_KEY not set"}
    st = _state(sb)
    all_t = tickers()
    if not tks:
        start = st.get("insight_cursor", 0) % len(all_t)
        tks = (all_t[start:] + all_t[:start])[:per_run]
    done = 0
    for tk in tks:
        if deadline and deadline.passed(60):
            break
        g = get_public(sb, f"gov/{tk}.json", {}) or {}
        f = get_public(sb, f"flows/{tk}.json", {}) or {}
        try:
            ins = json.loads(request(PUB + f"insiders/{tk}.json", timeout=30, retries=1))
        except Exception:
            ins = None
        try:
            r = gov_insights.read(tk, BY_TICKER[tk]["name"], g, f, ins)
        except Exception as ex:
            r = {"status": "error", "error": str(ex)[:200], "generated_at": now_iso()}
        update_company(sb, "gov", tk, {"ai_read": r, "_sources": {"ai_read": status(r.get("status") if r else "not_collected")}})
        done += 1
    st["insight_cursor"] = (st.get("insight_cursor", 0) + done) % len(all_t)
    _save_state(sb, st)
    return {"done": done}


def step_coverage(sb, log=print):
    keys = ["congress_house", "congress_senate", "committees", "lobbying", "pac", "employee_contrib", "hearings", "regulation", "legislation",
            "spending", "holders13dg", "inst13f", "finra_weekly", "finra_short_interest", "ai_read"]
    cov = {k: {} for k in keys}
    totals = {}

    def read(tk):
        g = get_public(sb, f"gov/{tk}.json", {}) or {}
        f = get_public(sb, f"flows/{tk}.json", {}) or {}
        return {**(g.get("sources") or {}), **(f.get("sources") or {})}
    res = _parallel(read, tickers(), workers=12)
    for tk, srcs in res.items():
        for k in keys:
            s = (srcs.get(k) or {}).get("status", "missing")
            cov[k][s] = cov[k].get(s, 0) + 1
            if s == "ok":
                totals[k] = totals.get(k, 0) + ((srcs.get(k) or {}).get("count") or 0)
    doc = {"generated": now_iso(), "companies": len(res), "by_source": cov, "record_totals": totals}
    put_public(sb, "gov/coverage.json", doc, cache="max-age=600")
    return doc


STEPS = {"entities": step_entities, "members": step_members, "congress": step_congress, "hearings": step_hearings, "laws": step_laws,
         "fec": step_fec, "company": step_company, "holders": step_holders, "f13": step_f13, "market": step_market,
         "insights": step_insights, "coverage": step_coverage}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--step", required=True, choices=sorted(STEPS))
    ap.add_argument("--tickers", nargs="*")
    ap.add_argument("--per-run", type=int, default=None)
    ap.add_argument("--zips", nargs="*")
    ap.add_argument("--years", nargs="*", type=int)
    ap.add_argument("--budget", type=float, default=None)
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()
    from .common import Supabase
    sb = Supabase()
    dl = Deadline(a.budget) if a.budget else None
    t0 = time.time()
    fn = STEPS[a.step]
    kw = {}
    if a.step in ("congress", "hearings", "fec", "holders", "f13", "market"):
        kw["tks"] = a.tickers
    if a.step in ("company", "insights", "entities"):
        kw["tks"] = a.tickers
    if a.step == "company":
        kw["per_run"] = a.per_run or 40
        kw["workers"] = a.workers
    if a.step in ("congress", "hearings") and a.years:
        kw["years"] = a.years
    if a.step == "f13" and a.zips:
        kw["zips"] = a.zips
        kw["force"] = True
    if a.step in ("congress", "hearings", "fec", "holders", "company", "insights", "entities") and dl:
        kw["deadline"] = dl
    print(json.dumps(fn(sb, **kw), default=str)[:3000])
    print(f"{a.step} finished in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()


# ---------------------------------------------------------------- scheduled entry point

def _due(st, key, hours):
    last = st.get("last", {}).get(key)
    if not last:
        return True
    try:
        t = dt.datetime.fromisoformat(last.replace("Z", "+00:00"))
    except ValueError:
        return True
    return (dt.datetime.now(dt.timezone.utc) - t).total_seconds() >= hours * 3600


def _mark(sb, key):
    st = _state(sb)
    st.setdefault("last", {})[key] = now_iso()
    _save_state(sb, st)


def scheduled(sb, step, time_budget=680, log=print):
    """Cron entry. Four schedules share one lease so gov/ and flows/ bundles never have two writers.

    daily:    members (weekly), laws (daily), congress PTRs (daily), FEC bulk (weekly), FINRA flows (when due, ~weekly)
    hearings: GovInfo CHRG sitemap sync, transcript archive, quote scan, per-company hearings
    company:  rotation of the next companies for LDA lobbying, Federal Register, USAspending, legislation links
    holders:  13D/13G incremental, 13F when a new data set is posted, AI reads rotation, coverage
    """
    from .common import AggregateLease, LeaseBusy, Run
    dl = Deadline(time_budget)
    out = {"step": step}
    try:
        lease = AggregateLease(sb, name="govflows", seconds=600)
        lease.__enter__()
    except LeaseBusy:
        return {"skipped": "another govflows run holds the lease"}
    try:
        with Run(sb, f"govflows:{step}") as run:
            st = _state(sb)
            if step == "daily":
                if _due(st, "members", 24 * 6):
                    out["members"] = step_members(sb, log=log); _mark(sb, "members")
                if _due(st, "laws", 20):
                    out["laws"] = step_laws(sb, log=log); _mark(sb, "laws")
                out["congress"] = step_congress(sb, deadline=dl, log=log); _mark(sb, "congress")
                if _due(st, "fec", 24 * 6) and dl.left() > 300:
                    out["fec"] = step_fec(sb, deadline=dl, log=log); _mark(sb, "fec")
                if _due(st, "market", 24 * 3) and dl.left() > 300:
                    out["market"] = step_market(sb, log=log); _mark(sb, "market")
            elif step == "hearings":
                out["hearings"] = step_hearings(sb, deadline=dl, log=log); _mark(sb, "hearings")
            elif step == "company":
                ent = _ents(sb)
                nxt = st.get("company_cursor", 0)
                all_t = tickers()
                pick = (all_t[nxt:] + all_t[:nxt])[:20]
                stale = [t for t in pick if t not in ent]
                if stale:
                    gov_entities.refresh(sb, stale, workers=3, log=log)
                out["company"] = step_company(sb, per_run=20, deadline=dl, workers=4, log=log)
            elif step == "holders":
                out["holders"] = step_holders(sb, deadline=dl, log=log); _mark(sb, "holders")
                if dl.left() > 400:
                    out["f13"] = step_f13(sb, log=log)
                if dl.left() > 120:
                    out["insights"] = step_insights(sb, per_run=25, deadline=dl, log=log)
                if dl.left() > 60:
                    cov = step_coverage(sb, log=log)
                    out["coverage"] = {"companies": cov["companies"]}
            else:
                raise ValueError("step must be daily, hearings, company or holders")
            run.stats.update({k: v for k, v in out.items() if k != "step"})
    finally:
        lease.__exit__(None, None, None)
    return out
