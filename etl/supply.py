"""Supply-chain map jobs: per-company extraction from SEC filings, material agreements, stated policy factors,
coverage-wide graph publication, region evidence, sympathy moves and cross-source insights.

Storage (public-data): supply/graph.json.gz (all current links), supply/co/{T}.json (one company: links, prior-year
links, agreements, policy, concentration, intel, moves, insights), supply/docs/{accn}/{doc}.json.gz (text copies of
cited passages), supply/regions.json (WGI + Federal Register by country), supply/state.json (rotation state).
Raw bucket: supplyraw/docs/{accn}/{doc}.txt.gz (full plain text of every document read)."""
import concurrent.futures as cf
import datetime as dt
import gzip
import json
import os
import re
import time

from .common import AggregateLease, LeaseBusy, Run, now_iso, request, sec_json
from .supply_core import (LT_LABEL, agreement_terms, concentration_candidates, entity_key, entity_patterns, link_id, link_type_of, norm,
                          policy_candidates, quote_ok, resolve_ticker, rule_candidates, select_passages, sentences, window_quote)
from .supply_sources import (BORING, GOOD, agreement_filings, doc_url, exhibit_docs, fetch_text, filing_index, filing_rows, older_rows, pick_filings,
                             public_text_copy, sec_titles)
from .universe import BY_TICKER, UNIVERSE

PUBLIC = "https://{host}/storage/v1/object/public/public-data/"
EXTERNAL_TICKERS = {"TSM", "ASML", "SONY", "TM", "HMC", "ARM", "GFS", "UMC", "AMKR", "ASX", "BABA", "BIDU", "CRWV", "NOK", "ERIC", "ERJ", "NVO",
                    "NVS", "AZN", "SNY", "GSK", "BNTX", "GMAB", "MRNA", "KOF", "CCEP", "COKE", "MBLY"}


def covered_names():
    return {t: [v["name"]] for t, v in BY_TICKER.items()}


def known_list():
    return ", ".join(f"{t}={v['name']}" for t, v in BY_TICKER.items())


def public_get(sb, path, gz=False):
    raw = sb.get_object("public-data", path)
    if raw is None:
        return None
    if gz:
        raw = gzip.decompress(raw)
    return json.loads(raw)


def public_put(sb, path, obj, gz=False, cache="max-age=300"):
    body = json.dumps(obj, separators=(",", ":"), default=str).encode()
    if gz:
        return sb.put_object("public-data", path, gzip.compress(body), "application/gzip", cache)
    return sb.put_object("public-data", path, body, "application/json", cache)


# ---------------------------------------------------------------- links

def _ticker_for(c, sec):
    """Covered counterparties keep their universe ticker; curated non-covered names keep a US ticker only if SEC lists it."""
    tk = c.get("cpt")
    if tk and tk in BY_TICKER:
        return tk
    if tk and tk in EXTERNAL_TICKERS and (not sec or tk in sec):
        return tk
    return resolve_ticker(c["cp"], tk, covered_names(), sec) if tk else None


def make_link(t, f, c, method, sec):
    cpt = _ticker_for(c, sec)
    if cpt == t:
        return None
    lt = c.get("lt") or link_type_of(c.get("q", ""), c["rel"], c["cp"])
    risk = c.get("risk") or None
    return {"id": link_id(t, f["accn"], c["rel"], c["cp"], cpt), "t": t, "cp": c["cp"], "cpt": cpt, "rel": c["rel"],
            "det": (c.get("det") or LT_LABEL.get(lt, "Named counterparty")).strip()[:160], "sh": c.get("share"), "ge10": bool(c.get("ge10")),
            "amt": c.get("amt"), "ss": bool(c.get("ss")), "rg": c.get("rg") or [], "hq": c.get("hq"), "risk": risk, "prod": c.get("prod") or None,
            "lt": lt, "q": c["q"], "row": c.get("row"), "accn": f["accn"], "doc": f["doc"], "form": f["form"], "filed": f["filed"],
            "cur": f["role"] == "current", "m": method}


def apply_review(t, cands, form):
    from . import supply_review as R
    out = []
    for c in cands:
        key = f"{t}|{c['cp']}|{c['rel']}"
        if key in R.DROP or any(x in c["q"] for x in getattr(R, "QDROP", [])):
            continue
        if key in R.FORMS and form not in R.FORMS[key]:
            continue
        c = dict(c)
        if key in R.REL:
            c["rel"] = R.REL[key]
            if c["rel"] != "customer":
                c["share"], c["ge10"] = None, False
        if f"{t}|{c['cp']}" in R.CP:
            c["cp"], c["cpt"] = R.CP[f"{t}|{c['cp']}"]
        for k, v in R.SET.get(f"{t}|{c['cp']}|{c['rel']}", {}).items():
            c[k] = v
        out.append(c)
    # a relabel can create duplicates of the same counterparty and relation; keep the best-supported one
    best = {}
    for c in out:
        k = (c["rel"], entity_key(c["cp"], c.get("cpt")))
        score = (c.get("share") is not None) * 4 + bool(c.get("ge10")) * 3 + (c.get("amt") is not None) * 2 + bool(c.get("ss")) * 2
        if k not in best or score > best[k][0]:
            best[k] = (score, c)
    return [c for _, c in best.values()]


def llm_candidates(t, f, text, deadline):
    from .supply_llm import extract_links, verify_links
    excerpt = select_passages(text)
    if not excerpt:
        return [], {"proposed": 0, "dropped": 0}
    props, model = extract_links(t, BY_TICKER[t]["name"], f["form"], excerpt, known_list(), deadline=deadline)
    kept, dropped = verify_links(props, norm(text))
    sec = sec_titles()
    out = []
    for p in kept:
        cp = p["counterparty"].strip()
        cpt = resolve_ticker(cp, p.get("counterparty_ticker"), covered_names(), sec)
        out.append({"cp": cp, "cpt": cpt, "rel": p["relation"], "share": p.get("share_pct") if p["relation"] == "customer" else None,
                    "amt": p.get("amount_usd"), "ss": bool(p.get("single_source")), "rg": [r.strip() for r in p.get("regions") or [] if r.strip()][:6],
                    "risk": (p.get("risk") or "").strip() or None, "prod": (p.get("product") or "").strip() or None, "lt": p.get("link_type"),
                    "det": (p.get("detail") or "").strip(), "q": p["quote"].strip(), "ge10": False})
    return out, {"proposed": len(props), "dropped": dropped, "model": model}


# ---------------------------------------------------------------- agreements (8-K Item 1.01 and their EX-10 exhibits)

ITEM101 = re.compile(r"Item\s*1\.01\.?\s*(?:Entry into a Material Definitive Agreement)?", re.I)
NEXT_ITEM = re.compile(r"\n\s*Item\s*[1-9]\.\d\d", re.I)
PARTY = re.compile(r"\b(?:with|between [^,;]{2,80}? and)\s+((?:[A-Z][\w&'.-]*,?\s){0,5}[A-Z][\w&'.-]*(?:,)?\s(?:Inc\.|Incorporated|Corporation|Corp\.|LLC|L\.L\.C\.|Ltd\.|Limited|L\.P\.|plc|N\.V\.|S\.A\.|AG|Co\.|Company))")
CLAUSE_CUES = re.compile(r"exclusiv|terminat|initial term|term of|expire|renew|minimum purchase|take-or-pay|prepay|non-cancel|purchase commitment|capacity|"
                         r"royalt|milestone|upfront|warrant|equity|supply|license", re.I)


def item_101(text):
    m = ITEM101.search(text)
    if not m:
        return ""
    rest = text[m.end():]
    n = NEXT_ITEM.search(rest)
    return rest[:n.start()] if n else rest[:20000]


def clause_quotes(text, cap=6):
    out, kinds = [], set()
    for s in sentences(text):
        if len(s) > 1500:
            continue
        a = agreement_terms(s)
        new = [c for c in a["clauses"] if c not in kinds]
        if not new and not (a["amounts"] or a["durations"]):
            continue
        anchor = (CLAUSE_CUES.search(s) or re.search(r"\$", s))
        q = window_quote(s, anchor.group(0) if anchor else "")
        if not q or not quote_ok(q, norm(s)):
            continue
        kinds.update(a["clauses"])
        out.append({"q": q, "terms": agreement_terms(q)})
        if len(out) >= cap:
            break
    return out


def agreements_for(t, cik, filings, pats, sb=None, exhibits=True):
    out, texts = [], {}
    for f in filings:
        try:
            text = fetch_text(cik, f["accn"], f["doc"], sb)
        except Exception as e:
            print(t, "8-K fetch", f["accn"], str(e)[:120])
            continue
        sec = item_101(text) or text[:8000]
        if BORING.search(sec[:2500]) and not GOOD.search(sec[:2500]):
            continue
        cps = []
        for name, tk, hq, rx, needle in pats:
            if (not needle or needle in sec.lower()) and rx.search(sec) and name not in [c["cp"] for c in cps]:
                cps.append({"cp": name, "cpt": tk})
        for m in PARTY.finditer(sec):
            nm = m.group(1).strip().rstrip(",")
            if not any(entity_key(nm) == entity_key(c["cp"]) or norm(c["cp"]) in norm(nm) for c in cps) and not re.search(BY_TICKER[t]["name"].split()[0], nm):
                cps.append({"cp": nm, "cpt": None})
        quotes = clause_quotes(sec)
        if not quotes:
            continue
        title = next((window_quote(s, "") for s in sentences(sec) if len(s.split()) >= 8), None)
        rec = {"id": f"A{f['accn'].replace('-', '')[-10:]}", "t": t, "accn": f["accn"], "doc": f["doc"], "form": f["form"], "filed": f["filed"],
               "items": f.get("items"), "url": doc_url(cik, f["accn"], f["doc"]), "title": title if title and quote_ok(title, norm(sec)) else None,
               "parties": cps[:4], "quotes": quotes, "exhibits": []}
        texts[(f["accn"], f["doc"])] = (text, [q["q"] for q in quotes] + ([rec["title"]] if rec["title"] else []), {"form": f["form"], "filed": f["filed"], "t": t, "url": rec["url"]})
        if exhibits:
            for ex in exhibit_docs(cik, f["accn"], filing_index(cik, f["accn"]))[:2]:
                try:
                    et = fetch_text(cik, f["accn"], ex, sb)
                except Exception:
                    continue
                head = et[:4000]
                if BORING.search(head) and not GOOD.search(head):
                    continue
                eq = clause_quotes(et[:60000], cap=5)
                if eq:
                    rec["exhibits"].append({"doc": ex, "url": doc_url(cik, f["accn"], ex), "quotes": eq})
                    texts[(f["accn"], ex)] = (et, [q["q"] for q in eq], {"form": f"{f['form']} EX-10", "filed": f["filed"], "t": t, "url": doc_url(cik, f["accn"], ex)})
        out.append(rec)
    return out, texts


# ---------------------------------------------------------------- one company

def plan(t):
    cik = BY_TICKER[t]["cik"]
    sub = sec_json(f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json")
    rows = filing_rows(sub)
    if len([r for r in rows if r["form"] == "10-K"]) < 3:
        rows += older_rows(sub)
    since = (dt.date.today() - dt.timedelta(days=3 * 365)).isoformat()
    return {"ticker": t, "cik": cik, "name": BY_TICKER[t]["name"], "legal": sub.get("name"), "filings": pick_filings(rows),
            "agreements": agreement_filings(rows, since=since)}


def process_company(sb, t, mode="llm", deadline=None, p=None, review=False, write=True):
    """Reads one company's filings and writes supply/co/{T}.json plus text copies; returns (current links, all links, summary)."""
    p = p or plan(t)
    cik, sec = p["cik"], sec_titles()
    pats = entity_patterns({k: v for k, v in covered_names().items() if k != t})
    links, policy, conc, texts, stats = [], [], [], {}, {"filings": 0, "proposed": 0, "dropped": 0}
    method = "rules+review" if mode == "rules" and review else ("rules" if mode == "rules" else "llm")
    for f in p["filings"]:
        text = fetch_text(cik, f["accn"], f["doc"], sb)
        stats["filings"] += 1
        if mode == "llm":
            cands, st = llm_candidates(t, f, text, deadline)
            stats["proposed"] += st["proposed"]
            stats["dropped"] += st["dropped"]
            stats["model"] = st.get("model")
        else:
            cands = rule_candidates(text, t, covered_names(), [p["name"], p.get("legal")])
            if review:
                cands = apply_review(t, cands, f["form"])
        tn = norm(text)
        fl = []
        for c in cands:
            if not quote_ok(c["q"], tn) or (c.get("row") and norm(c["row"]) not in tn):
                stats["dropped"] += 1
                continue
            l = make_link(t, f, c, method, sec)
            if l:
                fl.append(l)
        links += fl
        quotes = [l["q"] for l in fl] + [l["row"] for l in fl if l.get("row")]
        if f["role"] == "current":
            for k, x in enumerate(policy_candidates(text)):
                policy.append({"id": f"P{f['accn'][-6:]}{k}", "t": t, "kind": x["kind"], "countries": x["countries"], "q": x["q"],
                               "accn": f["accn"], "doc": f["doc"], "form": f["form"], "filed": f["filed"]})
                quotes.append(x["q"])
        for k, x in enumerate(concentration_candidates(text)):
            conc.append({"id": f"C{f['accn'][-6:]}{k}", "t": t, "pct": x["pct"], "unnamed": x["unnamed"], "q": x["q"],
                         "accn": f["accn"], "doc": f["doc"], "form": f["form"], "filed": f["filed"], "cur": f["role"] == "current"})
            quotes.append(x["q"])
        texts[(f["accn"], f["doc"])] = (text, quotes, {"form": f["form"], "filed": f["filed"], "t": t, "url": doc_url(cik, f["accn"], f["doc"])})
    agreements, atexts = agreements_for(t, cik, p["agreements"], pats, sb)
    texts.update(atexts)
    cur = [l for l in links if l["cur"]]
    # one current link per counterparty and relation: the 10-K statement, unless only the newer 10-Q names it
    seen, current = set(), []
    for l in sorted(cur, key=lambda x: (x["form"] != "10-K", -(x["sh"] or 0))):
        k = (l["rel"], entity_key(l["cp"], l["cpt"]))
        if k not in seen:
            seen.add(k)
            current.append(l)
    doc_paths = {}
    if write:
        for (accn, doc), (text, quotes, meta) in texts.items():
            if quotes:
                doc_paths[f"{accn}/{doc}"] = public_text_copy(sb, accn, doc, meta, text, quotes)
    co = {"ticker": t, "name": p["name"], "legal": p.get("legal"), "cik": cik, "generated": now_iso(), "method": method,
          "filings": [{k: f.get(k) for k in ("accn", "form", "filed", "doc", "role", "report")} | {"url": doc_url(cik, f["accn"], f["doc"])} for f in p["filings"]],
          "links": current, "history": [l for l in links if not l["cur"]], "q10": [l for l in cur if l not in current],
          "policy": policy, "conc": conc, "agreements": agreements, "docs": doc_paths,
          "stats": stats, "source": "SEC EDGAR filings (www.sec.gov/Archives), read with SEC_USER_AGENT"}
    if write:
        old = public_get(sb, f"supply/co/{t}.json") or {}
        for k in ("intel", "moves", "insights", "px"):
            if k in old:
                co[k] = old[k]
        public_put(sb, f"supply/co/{t}.json", co)
    return current, co, {"links": len(current), "history": len(co["history"]), "agreements": len(agreements), "policy": len(policy), **stats}


# ---------------------------------------------------------------- coverage-wide graph

GRAPH_KEYS = ("id", "t", "cp", "cpt", "rel", "det", "sh", "ge10", "amt", "ss", "rg", "hq", "risk", "prod", "lt", "q", "row", "accn", "doc", "form", "filed", "m")


def merge_graph(sb, updates):
    """Replace the given companies' links in supply/graph.json.gz (serialised by a lease so two runs never interleave)."""
    with AggregateLease(sb, name="supply-graph", seconds=120):
        g = public_get(sb, "supply/graph.json.gz", gz=True) or {"links": [], "companies": {}}
        drop = set(updates)
        g["links"] = [l for l in g["links"] if l["t"] not in drop]
        for t, (links, co) in updates.items():
            g["links"] += [{k: l.get(k) for k in GRAPH_KEYS if l.get(k) not in (None, [], False)} for l in links]
            k10 = next((f for f in co["filings"] if f["form"] == "10-K" and f["role"] == "current"), None)
            g["companies"][t] = {"n": co["name"], "c": co.get("cik"), "k": k10["filed"] if k10 else None, "at": co["generated"], "m": co["method"], "links": len(links)}
        g["generated"] = now_iso()
        g["counts"] = {"companies": len(g["companies"]), "links": len(g["links"]),
                       "with_links": len({l["t"] for l in g["links"]}), "corroborated": corroborated_count(g["links"])}
        public_put(sb, "supply/graph.json.gz", g, gz=True, cache="max-age=300")
        return g["counts"]


def inverse(rel):
    return {"customer": "supplier", "supplier": "customer"}.get(rel, rel)


def corroborated_count(links):
    have = {(l["t"], l.get("cpt"), l["rel"]) for l in links if l.get("cpt")}
    return sum(1 for l in links if l.get("cpt") and (l["cpt"], l["t"], inverse(l["rel"])) in have)


# ---------------------------------------------------------------- rotation (cron)

def latest_periodic(t):
    """Accessions of the newest 10-K and 10-Q in the submissions feed (cheap change check)."""
    cik = BY_TICKER[t]["cik"]
    rows = filing_rows(sec_json(f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json"))
    k = next((r["accn"] for r in rows if r["form"] == "10-K"), None)
    q = next((r["accn"] for r in rows if r["form"] == "10-Q"), None)
    return k, q


def rotate_job(sb, time_budget=680, workers=8, check=150, shard=0, shards=1):
    """Processes companies never read, then companies with a new 10-K or 10-Q, within the time budget.

    shard/shards split the universe by position so parallel cron entries never take the same company;
    each run merges only its own ticker updates into supply/state.json under a short lease."""
    t0 = time.time()
    deadline = t0 + time_budget - 60
    if not os.environ.get("FIREWORKS_API_KEY") and not os.environ.get("SUPPLY_RULES_FALLBACK"):
        return {"skipped": "FIREWORKS_API_KEY is not set; extraction needs the model (set SUPPLY_RULES_FALLBACK=1 for the rule extractor)"}
    mode = "llm" if os.environ.get("FIREWORKS_API_KEY") else "rules"
    state = public_get(sb, "supply/state.json") or {"tickers": {}, "cursor": 0}
    full_order = [u[0] for u in UNIVERSE]
    order = [t for i, t in enumerate(full_order) if i % shards == shard]
    todo = [t for t in order if t not in state["tickers"]]
    cursor_key = "cursor" if shards == 1 else f"cursor_{shard}_{shards}"
    mine = {}
    stats = {"processed": [], "failed": {}, "checked": 0, "mode": mode}
    if len(todo) < workers * 3:
        cur = state.get(cursor_key, 0) % max(1, len(order))
        sl = (order[cur:] + order[:cur])[:check]
        state[cursor_key] = (cur + check) % max(1, len(order))
        for t in sl:
            if time.time() > deadline - 120:
                break
            try:
                k, q = latest_periodic(t)
                stats["checked"] += 1
                s = state["tickers"].get(t, {})
                if ((k and k != s.get("k")) or (q and q != s.get("q"))) and t not in todo:
                    todo.append(t)
            except Exception as e:
                stats["failed"][t] = "check: " + str(e)[:120]
    updates = {}

    def one(t):
        if time.time() > deadline - 150:
            return t, None, "deadline"
        try:
            p = plan(t)
            links, co, summ = process_company(sb, t, mode=mode, deadline=deadline, p=p)
            return t, (links, co, p), summ
        except Exception as e:
            return t, None, str(e)[:200]

    with cf.ThreadPoolExecutor(workers) as ex:
        futs = [ex.submit(one, t) for t in todo[:workers * 6]]
        for fu in cf.as_completed(futs):
            t, res, info = fu.result()
            if res is None:
                if info == "deadline":
                    stats.setdefault("skipped", 0)
                    stats["skipped"] += 1
                else:
                    stats["failed"][t] = info
                continue
            links, co, p = res
            updates[t] = (links, co)
            k = next((f["accn"] for f in p["filings"] if f["form"] == "10-K"), None)
            q = next((f["accn"] for f in p["filings"] if f["form"] == "10-Q"), None)
            state["tickers"][t] = mine[t] = {"k": k, "q": q, "at": now_iso(), "m": co["method"], "links": len(links), "intel": None}
            stats["processed"].append(t)
    if updates:
        try:
            stats["graph"] = merge_graph(sb, updates)
        except LeaseBusy:
            stats["failed"]["graph"] = "graph lease busy; links kept in supply/co and merged next run"
            for t in updates:
                state["tickers"][t]["pending_graph"] = True
                mine[t] = state["tickers"][t]
    pending = [t for t, s in state["tickers"].items() if s.get("pending_graph") and t not in updates]
    if pending and "graph" in stats:
        try:
            merge_graph(sb, {t: (co["links"], co) for t in pending for co in [public_get(sb, f"supply/co/{t}.json")] if co})
            for t in pending:
                state["tickers"][t].pop("pending_graph", None)
                mine[t] = state["tickers"][t]
        except Exception as e:
            stats["failed"]["pending"] = str(e)[:120]
    for attempt in range(6):
        try:
            with AggregateLease(sb, name="supply-state", seconds=120):
                latest = public_get(sb, "supply/state.json") or {"tickers": {}}
                latest.setdefault("tickers", {}).update(mine)
                latest[cursor_key] = state.get(cursor_key, 0)
                latest["generated"] = now_iso()
                latest["remaining"] = len([t for t in full_order if t not in latest["tickers"]])
                public_put(sb, "supply/state.json", latest, cache="max-age=60")
                state = latest
            break
        except LeaseBusy:
            time.sleep(5)
    else:
        stats["failed"]["state"] = "state lease busy; this run's companies are re-read next run"
    stats["done"] = len(stats["processed"])
    stats["remaining"] = state.get("remaining")
    stats["shard"] = f"{shard}/{shards}"
    return stats


# ---------------------------------------------------------------- intel step (cron, daily)

def bars(sym, cache):
    if sym not in cache:
        try:
            base = os.environ["SUPABASE_URL"].rstrip("/") + "/storage/v1/object/public/public-data/"
            cache[sym] = json.loads(request(base + f"tech/bars/{sym}.json", timeout=60, retries=2))
        except Exception:
            cache[sym] = None
    return cache[sym]


def events(t):
    try:
        base = os.environ["SUPABASE_URL"].rstrip("/") + "/storage/v1/object/public/public-data/"
        return json.loads(request(base + f"tech/events/{t}.json", timeout=60, retries=2))
    except Exception:
        return {"filings": [], "cols": []}


def neighbours(t, graph_links):
    out = {}
    for l in graph_links:
        if l["t"] == t and l.get("cpt") in BY_TICKER and l["cpt"] != t:
            out.setdefault(l["cpt"], l["rel"])
        elif l.get("cpt") == t and l["t"] != t:
            out.setdefault(l["t"], inverse(l["rel"]))
    return out


def moves_for(t, graph_links, cache):
    from .supply_intel import basket_series, sympathy
    nb = neighbours(t, graph_links)
    fb = bars(t, cache)
    if not fb:
        return None, None
    peers = [{"t": s, "rel": r, "bars": bars(s, cache)} for s, r in list(nb.items())[:20]]
    mv = sympathy(t, fb, events(t), peers)
    syms = [t, "SPY"] + [p["t"] for p in peers if p["bars"] and p["rel"] != "competitor"]
    bars("SPY", cache)
    px = basket_series(syms, {s: cache.get(s) for s in syms if cache.get(s)})
    return mv, px


def record_lines(t, co, graph_links, regions):
    """Numbered evidence records for the insight model: own links, links naming this company, prior-year links,
    agreements, filing policy statements, Federal Register actions for tied countries and news sentences."""
    recs = []
    for l in co.get("links", []):
        recs.append({"id": "L" + l["id"], "kind": f"{t} names {l['rel']}", "text": f"{l['cp']} ({l.get('cpt') or 'private'}) {l['det']}; share {l.get('sh')}; {l['form']} {l['filed']}: {l['q']}"})
    for l in graph_links:
        if l.get("cpt") == t and l["t"] != t:
            recs.append({"id": "L" + l["id"], "kind": f"{l['t']} names {t} as {l['rel']}", "text": f"{l['form']} {l['filed']}: {l['q']}"})
    for l in co.get("history", [])[:40]:
        recs.append({"id": "L" + l["id"], "kind": f"prior {l['form']} {l['filed']}", "text": f"{l['cp']} {l['rel']}; share {l.get('sh')}: {l['q']}"})
    for a in co.get("agreements", []):
        for i, q in enumerate(a["quotes"][:3]):
            recs.append({"id": f"{a['id']}q{i}", "kind": f"agreement {a['form']} {a['filed']}", "text": f"parties {', '.join(p['cp'] for p in a['parties'])}: {q['q']}"})
    for x in co.get("policy", [])[:15]:
        recs.append({"id": x["id"], "kind": f"filing policy factor {x['kind']}", "text": f"{', '.join(x['countries'])}: {x['q']}"})
    tied = {c for l in co.get("links", []) for c in (l.get("rg") or []) + ([l["hq"]] if l.get("hq") else [])}
    for c in sorted(tied):
        for d in (regions.get(c, {}).get("policy") or [])[:3]:
            recs.append({"id": "F" + d["id"], "kind": f"Federal Register {d['agency']} {d['date']}", "text": f"{c}: {d['title']}. {d.get('q') or ''}"})
    for n in co.get("intel", [])[:12]:
        recs.append({"id": "N" + str(abs(hash(n["url"] + n["q"])) % 10 ** 10), "kind": f"news {n['host']} {n.get('date') or ''}", "text": n["q"]})
    return recs


KEY_COUNTRIES = ["Taiwan", "China", "Hong Kong", "Japan", "South Korea", "India", "Vietnam", "Malaysia", "Thailand", "Singapore", "Philippines", "Indonesia",
                 "Mexico", "Canada", "Brazil", "Israel", "Russia", "Ukraine", "Germany", "Netherlands", "Ireland", "United Kingdom", "France", "Italy",
                 "Switzerland", "Denmark", "Kazakhstan", "Saudi Arabia", "United Arab Emirates"]


def intel_job(sb, time_budget=680, per_run=80, news_links=4, force_regions=False, workers=6):
    """Daily: region evidence (WGI, Federal Register), relationship news, sympathy moves and AI insights for companies
    whose links changed since their last intel pass."""
    t0 = time.time()
    deadline = t0 + time_budget - 60
    g = public_get(sb, "supply/graph.json.gz", gz=True) or {"links": []}
    state = public_get(sb, "supply/state.json") or {"tickers": {}}
    stats = {"done": 0, "failed": {}, "regions": 0}
    countries = sorted({c for l in g["links"] for c in (l.get("rg") or []) + ([l["hq"]] if l.get("hq") else []) if c not in ("Europe", "Asia", "Middle East")} | set(KEY_COUNTRIES))
    regions = public_get(sb, "supply/regions.json") or {}
    stale = force_regions or (regions.get("generated") or "") < (dt.datetime.utcnow() - dt.timedelta(hours=20)).strftime("%Y-%m-%dT%H:%M:%SZ")
    if stale:
        from .supply_intel import country_scores, federal_register
        scores = country_scores(countries)
        out = {"generated": now_iso(), "countries": {}}
        for c in countries:
            if time.time() > deadline - 300:
                break
            try:
                fr = federal_register(c)
            except Exception as e:
                fr = {"total": 0, "items": [], "error": str(e)[:120]}
            out["countries"][c] = {"score": scores.get(c), "policy": fr["items"], "policyTotal": fr["total"], "query": fr.get("query")}
        public_put(sb, "supply/regions.json", out, cache="max-age=3600")
        regions = out
        stats["regions"] = len(out["countries"])
    todo = [t for t, s in state["tickers"].items() if s.get("intel") != s.get("at")][:per_run]
    cache = {}

    def one(t):
        if time.time() > deadline - 90:
            return t, "deadline"
        co = public_get(sb, f"supply/co/{t}.json")
        if not co:
            return t, None
        co["moves"], co["px"] = moves_for(t, g["links"], cache)
        if os.environ.get("TAVILY_API_KEY"):
            from .supply_intel import corroborate
            ranked = sorted(co["links"], key=lambda l: -((l.get("sh") or 0) + 20 * bool(l.get("ss")) + 10 * bool(l.get("cpt"))))
            intel = []
            for l in [x for x in ranked if x["rel"] != "competitor"][:news_links]:
                intel += corroborate(l, co["name"], l["cp"])
            co["intel"] = intel
        if os.environ.get("FIREWORKS_API_KEY"):
            from .supply_llm import insights, validate_insights
            recs = record_lines(t, co, g["links"], regions.get("countries", {}))
            data, model = insights(t, co["name"], recs, deadline=deadline)
            v = validate_insights(data, recs)
            co["insights"] = {**v, "model": model, "generated": now_iso(), "label": "AI-generated",
                              "records": {r["id"]: r["text"][:400] for r in recs if any(r["id"] in i["refs"] for i in v["insights"])}}
        public_put(sb, f"supply/co/{t}.json", co)
        return t, None

    with cf.ThreadPoolExecutor(workers) as ex:
        for fu in cf.as_completed([ex.submit(one, t) for t in todo]):
            try:
                t, err = fu.result()
            except Exception as e:
                stats["failed"]["?"] = str(e)[:200]
                continue
            if err:
                continue
            state["tickers"][t]["intel"] = state["tickers"][t].get("at")
            stats["done"] += 1
    public_put(sb, "supply/state.json", state, cache="max-age=60")
    return stats


def run(sb, step="rotate", shard=0, shards=1):
    with Run(sb, f"supply:{step}") as r:
        r.stats = rotate_job(sb, shard=shard, shards=shards) if step == "rotate" else intel_job(sb)
        return r.stats
