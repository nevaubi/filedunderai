"""Always-on monitors: cheap rule checks over published data, no model calls.

Each monitor type takes the data cache, the monitor and its resolved ticker scope, and returns hits:
    {"key", "title", "summary", "tickers", "severity", "evidence": [{"label", "url"|"cite"}], "facts": {...}}
`key` is stable for the underlying event (a Form 4 accession, a filing date, a doc id), so `evaluate` drops
events already reported and applies the monitor's cooldown per key. A new monitor reports events from its look-back
window (default 3 days) and then only new ones.
"""
import datetime as dt
import re

from .data import T, _r

SEVERITY = ("info", "notable", "high")
MAX_SCOPE = 400          # per-ticker file reads per evaluation
SEEN_KEEP = 2000


def _date(s):
    return (s or "")[:10]


def _since(mon, lookback_days=3):
    created = _date(mon.get("created_at")) or dt.date.today().isoformat()
    base = (dt.date.fromisoformat(created) - dt.timedelta(days=int((mon.get("params") or {}).get("lookback_days", lookback_days)))).isoformat()
    return base


def money(v):
    v = float(v or 0)
    a = abs(v)
    return f"${v / 1e9:.2f}B" if a >= 1e9 else f"${v / 1e6:.1f}M" if a >= 1e6 else f"${v / 1e3:.0f}K"


def pct(v):
    return f"{v * 100:+.1f}%" if isinstance(v, (int, float)) else "n/a"


def resolve_scope(data, mon, state):
    sc = mon.get("scope") or {"kind": "watchlist"}
    kind = sc.get("kind", "watchlist")
    if kind == "tickers":
        out = [T(x) for x in sc.get("tickers") or [] if x]
    elif kind == "watchlist":
        out = [T(x) for x in state.get("watchlist") or []]
    elif kind == "portfolio":
        out = [T(x.get("ticker") if isinstance(x, dict) else x) for x in state.get("portfolio") or []]
    elif kind == "sector":
        sec = (sc.get("sector") or "").lower()
        out = [t for t in data.tickers() if (data.sector_of(t) or "").lower() == sec]
    else:
        out = data.tickers()
    return list(dict.fromkeys(out)), kind == "universe"


# ---------------------------------------------------------------- monitor types
OFFICER_RX = re.compile(r"chief|ceo|cfo|coo|president|officer|vp|vice president|treasurer|controller|counsel|secretary|chair|director", re.I)
# Funds and holding vehicles that hold a board seat file as "Director"; their buying is fund flow, not insider conviction.
ENTITY_RX = re.compile(r"\b(LP|L\.P\.|LLC|L\.L\.C\.|INC|CORP|ADVISORS?|CAPITAL|FUND|PARTNERS|MANAGEMENT|HOLDINGS|TRUST|GROUP|INVESTMENTS?|VENTURES)\b", re.I)


def m_insider(data, mon, tickers, universe):
    p = mon.get("params") or {}
    side = p.get("side", "buy")
    codes = {"buy": {"P"}, "sell": {"S"}, "both": {"P", "S"}}.get(side, {"P"})
    min_value = float(p.get("min_value", 250_000))
    od_only = p.get("officers_directors_only", True)
    since = max(_since(mon, 7), (dt.date.today() - dt.timedelta(days=int(p.get("window_days", 30)))).isoformat())
    if universe or len(tickers) > MAX_SCOPE:
        idx = (data.get("meta/insiders.json") or {}).get("companies") or {}
        key = "buys" if side == "buy" else "sells"
        vkey = "buy_value" if side == "buy" else "sell_value"
        cand = [t for t, v in idx.items() if ((v.get("w90") or {}).get(key) or 0) > 0 and ((v.get("w90") or {}).get(vkey) or 0) >= min_value
                and (not tickers or t in set(tickers))]
        cand.sort(key=lambda t: -((idx[t].get("w90") or {}).get(vkey) or 0))
        tickers = cand[:150]
    data.prefetch([f"insiders/{t}.json" for t in tickers[:MAX_SCOPE]])
    hits = []
    for t in tickers[:MAX_SCOPE]:
        d = data.get(f"insiders/{t}.json") or {}
        rows = [r for r in d.get("rows") or [] if r.get("code") in codes and not r.get("derivative") and _date(r.get("tx_date")) >= since
                and not (r.get("code") == "S" and r.get("plan_10b5"))]
        if od_only:
            rows = [r for r in rows if OFFICER_RX.search(r.get("role") or "") and not re.fullmatch(r"\s*10% owner\s*", r.get("role") or "", re.I)
                    and not ENTITY_RX.search(r.get("owner") or "")]
        by_acc = {}
        for r in rows:
            by_acc.setdefault(r.get("accession"), []).append(r)
        for acc, rs in by_acc.items():
            val = sum((r.get("shares") or 0) * (r.get("price") or 0) for r in rs)
            if val < min_value:
                continue
            r0 = rs[0]
            verb = "bought" if r0.get("code") == "P" else "sold (discretionary)"
            avgp = val / max(1, sum(r.get("shares") or 0 for r in rs))
            hits.append({
                "key": f"ins:{t}:{acc}", "tickers": [t], "severity": "high" if val >= 5e6 else "notable",
                "title": f"{t}: {r0.get('owner')} {verb} {money(val)}",
                "summary": f"{r0.get('owner')} ({r0.get('role')}) {verb} {sum(r.get('shares') or 0 for r in rs):,.0f} shares at about ${avgp:,.2f} on {r0.get('tx_date')}"
                           + (f"; also reported by {', '.join(r0['joint'])}" if r0.get("joint") else "") + ".",
                "evidence": [{"label": f"Form 4 filed {r0.get('filed')}", "cite": f"filing:{t}:{acc}"}],
                "facts": {"value": round(val), "date": r0.get("tx_date"), "owner": r0.get("owner"), "role": r0.get("role"), "code": r0.get("code")},
            })
    return hits


TAG_ALIASES = {"bridge loan": r"bridge (loan|facility)", "auditor change": r"(dismiss\w*|engag\w*|appoint\w*).{0,80}independent registered public accounting firm",
               "new risk factor": None}


def m_filing_change(data, mon, tickers, universe):
    p = mon.get("params") or {}
    tags = [x.lower() for x in p.get("tags") or ["material weakness", "going concern", "covenant", "default", "restatement"]]
    phrases = [re.compile(x, re.I) for x in p.get("phrases") or []] + [re.compile(TAG_ALIASES[x], re.I) for x in tags if TAG_ALIASES.get(x)]
    want_new_rf = "new risk factor" in tags
    since = _since(mon, 3)
    idx = (data.get("redlines/index.json") or {}).get("companies") or {}
    scope = set(tickers) if not universe else None
    hits = []
    cand = []
    for t, v in idx.items():
        if scope is not None and t not in scope:
            continue
        for c in v.get("comparisons") or []:
            if _date(c.get("filed")) < since:
                continue
            sig = {s.lower() for s in c.get("signals") or []}
            if sig & set(tags) or phrases or want_new_rf:
                cand.append((t, c))
    cand.sort(key=lambda x: x[1].get("filed") or "", reverse=True)
    data.prefetch([f"redlines/{t}.json" for t, _ in cand[:120]])
    for t, c in cand[:120]:
        det = data.get(f"redlines/{t}.json") or {}
        comp = next((x for x in det.get("comparisons") or [] if (x.get("new") or {}).get("filed") == c.get("filed") and x.get("form") == c.get("form")), None)
        if not comp:
            continue
        matched = []
        for s in comp.get("sections") or []:
            for ch in s.get("changes") or []:
                text = ch.get("new") or ch.get("old") or ""
                sig = {x.lower() for x in ch.get("signals") or []}
                why = sorted(sig & set(tags))
                if want_new_rf and ch.get("kind") == "added" and "risk factor" in (s.get("title") or "").lower() and (ch.get("score") or 0) >= 40:
                    why.append("new risk factor")
                if any(rx.search(text) for rx in phrases):
                    why.append("phrase")
                if why and ch.get("kind") in ("added", "modified"):
                    matched.append({"section": s.get("title"), "kind": ch.get("kind"), "why": why, "text": text[:300], "score": ch.get("score") or 0})
        if not matched:
            continue
        matched.sort(key=lambda m: -m["score"])
        new = comp.get("new") or {}
        allwhy = sorted({w for m in matched for w in m["why"]})
        sev = "high" if {"going concern", "material weakness", "restatement", "default"} & set(allwhy) else "notable"
        hits.append({
            "key": f"rl:{t}:{comp.get('form')}:{new.get('filed')}", "tickers": [t], "severity": sev,
            "title": f"{t} {comp.get('form')} ({new.get('filed')}): {', '.join(allwhy)[:80]}",
            "summary": f"{len(matched)} changed passages flagged in the {comp.get('form')} filed {new.get('filed')} vs the prior comparable filing. Top: "
                       f"{matched[0]['section']} ({matched[0]['kind']}): \"{matched[0]['text'][:200]}\"",
            "evidence": [{"label": f"{comp.get('form')} filed {new.get('filed')}", "cite": f"filing:{t}:{new.get('accn')}", "url": new.get("url")}]
                        + [{"label": f"{m['section']} ({m['kind']})", "quote": m["text"]} for m in matched[1:4]],
            "facts": {"tags": allwhy, "passages": len(matched)},
        })
    return hits


def m_contracts(data, mon, tickers, universe):
    p = mon.get("params") or {}
    thr = float(p.get("min_change_pct", 0.25))
    min_amt = float(p.get("min_amount", 1e8))
    data.prefetch([f"altdata/{t}.json" for t in tickers[:MAX_SCOPE]])
    hits = []
    for t in tickers[:MAX_SCOPE]:
        a = data.alt_data(t)
        fy = a.get("federal_obligations_by_fy") or {}
        ch = a.get("latest_full_fy_change")
        y1, y0 = a.get("latest_full_fy"), a.get("prior_full_fy")
        if ch is None or y1 not in fy or y0 not in fy:
            continue
        ys = [y0, y1]
        cur, prev = fy[y1], fy[y0]
        if max(cur, prev) < min_amt or abs(ch) < thr:
            continue
        hits.append({"key": f"con:{t}:{ys[-1]}", "tickers": [t], "severity": "notable",
                     "title": f"{t}: federal obligations {pct(ch)} in FY{ys[-1]}",
                     "summary": f"Prime contract obligations FY{ys[-2]} {money(prev)} → FY{ys[-1]} {money(cur)} ({pct(ch)}). Obligations are lumpy; multi-year awards can land in one year.",
                     "evidence": [{"label": "USAspending.gov prime awards", "url": ((a.get("contracts_source") or {}).get("url")) or "https://www.usaspending.gov/search"}],
                     "facts": {"fy": ys[-1], "amount": cur, "prior": prev, "change": ch}})
    return hits


def m_estimates(data, mon, tickers, universe):
    p = mon.get("params") or {}
    field = p.get("field", "eps_next_q")
    thr = float(p.get("min_pct", 0.05))
    base = mon.setdefault("baseline", {})
    hits = []
    for t in tickers[:MAX_SCOPE]:
        e = data.estimate_row(t) or {}
        v = e.get(field)
        if not isinstance(v, (int, float)):
            continue
        b = base.get(t)
        base[t] = v
        if not isinstance(b, (int, float)) or b == 0:
            continue
        ch = v / b - 1
        if abs(ch) >= thr:
            lab = "next-quarter EPS consensus" if field == "eps_next_q" else "median price target"
            hits.append({"key": f"est:{t}:{field}:{round(v, 4)}", "tickers": [t], "severity": "notable",
                         "title": f"{t}: {lab} {pct(ch)}", "summary": f"{lab.capitalize()} moved from {b:,.2f} to {v:,.2f} ({pct(ch)}) since the last check.",
                         "evidence": [{"label": "Consensus (Yahoo Finance)", "url": f"https://finance.yahoo.com/quote/{t}/analysis/"}],
                         "facts": {"from": b, "to": v, "change": ch, "field": field}})
    return hits


def m_price(data, mon, tickers, universe):
    p = mon.get("params") or {}
    cond = p.get("condition", "above")
    hits = []
    for t in tickers[:MAX_SCOPE]:
        st = data.tech_state(t)
        if not st:
            continue
        close, d = st.get("close"), st.get("d")
        fired = None
        if cond == "above" and close is not None and close >= float(p["level"]):
            fired = f"closed at ${close:,.2f}, at or above ${float(p['level']):,.2f}"
        elif cond == "below" and close is not None and close <= float(p["level"]):
            fired = f"closed at ${close:,.2f}, at or below ${float(p['level']):,.2f}"
        elif cond == "drawdown" and (st.get("from_hi") or 0) <= -abs(float(p.get("pct", 0.2))):
            fired = f"is {pct(st.get('from_hi'))} from its 52-week high"
        elif cond == "rsi_below" and (st.get("rsi") or 100) <= float(p.get("level", 30)):
            fired = f"has RSI {st.get('rsi'):.0f}"
        elif cond == "rsi_above" and (st.get("rsi") or 0) >= float(p.get("level", 70)):
            fired = f"has RSI {st.get('rsi'):.0f}"
        if fired:
            hits.append({"key": f"px:{t}:{cond}:{d}", "tickers": [t], "severity": "info", "title": f"{t} {fired}",
                         "summary": f"{t} {fired} as of {d}.", "evidence": [{"label": "Daily close (tech/states.json)"}],
                         "facts": {"close": close, "date": d}})
    return hits


def m_tech_signal(data, mon, tickers, universe):
    p = mon.get("params") or {}
    want = set(p.get("signals") or ["low_52w", "rsi_oversold", "gap_down", "gap_up"])
    since = _since(mon, 2)
    rows = (data.get("tech/states.json") or {}).get("rows") or []
    scope = set(tickers) if not universe else None
    hits = []
    for r in rows:
        if scope is not None and r.get("t") not in scope:
            continue
        for sig, d in r.get("events") or []:
            if sig in want and d >= since:
                br = data.base_rate(sig) or {}
                note = (f" Since 2008 this signal was followed by a 60-day excess return of {pct(br.get('excess'))} "
                        f"(95% range {pct((br.get('excess_ci') or [None, None])[0])} to {pct((br.get('excess_ci') or [None, None])[1])}, n={br.get('events'):,})."
                        if br.get("events") else "")
                hits.append({"key": f"sig:{r['t']}:{sig}:{d}", "tickers": [r["t"]], "severity": "info",
                             "title": f"{r['t']}: {sig.replace('_', ' ')} on {d}", "summary": f"{r['t']} triggered {sig.replace('_', ' ')} on {d}.{note}",
                             "evidence": [{"label": "Signal base rates (tech/signals.json)"}], "facts": {"signal": sig, "date": d, "base_rate": br}})
    return hits[:200]


def m_upcoming(data, mon, tickers, universe):
    p = mon.get("params") or {}
    cal = data.calendar(days=int(p.get("within_days", 7)), kinds=p.get("kinds") or ["earnings", "ex_dividend"],
                        tickers=None if universe else tickers)
    hits = []
    for e in cal["events"]:
        who = e.get("ticker") or e.get("symbol") or e.get("release") or e.get("name")
        what = {"earnings": "reports earnings", "ex_dividend": f"goes ex-dividend (${e.get('amount')})", "macro": f"{e.get('title')}",
                "ipo": f"IPO {e.get('status')}",
                "trial_readout": f"{e.get('phase')} trial {e.get('title')} reaches primary completion",
                "proposed_sale": f"{e.get('person')} may sell ${(e.get('value') or 0) / 1e6:,.1f}M (Form 144{', 10b5-1 plan' if e.get('plan') else ''})"}[e["kind"]]
        hits.append({"key": f"cal:{e['kind']}:{who}:{e['date']}", "tickers": [e["ticker"]] if e.get("ticker") else [], "severity": "info",
                     "title": f"{who} {what} on {e['date']}" if e["kind"] != "macro" else f"{e['date']}: {what}",
                     "summary": f"{who} {what} on {e['date']}" + (f" ({e.get('time')})" if e.get("time") else "") +
                                (f"; consensus EPS {e['eps_consensus']}" if e.get("eps_consensus") is not None else "") + ".",
                     "evidence": [{"label": e["kind"].replace("_", " "), "url": e.get("url")}] if e.get("url") else [], "facts": e})
    return hits


def m_ir_document(data, mon, tickers, universe):
    p = mon.get("params") or {}
    types = [x.lower() for x in p.get("doc_types") or []]
    since = _since(mon, 3)
    data.prefetch([f"ir/{t}.json" for t in tickers[:150]])
    hits = []
    for t in tickers[:150]:
        d = data.get(f"ir/{t}.json") or {}
        for x in d.get("documents") or []:
            # News means a recently published document; backfilled history (fetched now, published years ago, or
            # undated) is not reported.
            when = _date(x.get("published")) or _date(x.get("period_end"))
            if not when or when < since or (types and not any(k in (x.get("doc_type") or "").lower() for k in types)):
                continue
            hits.append({"key": f"ir:{t}:{x.get('id')}", "tickers": [t], "severity": "info",
                         "title": f"{t}: new IR document: {(x.get('title') or x.get('doc_type') or '')[:80]}",
                         "summary": f"{x.get('doc_type') or 'Document'} {x.get('fiscal_label') or ''} published {x.get('published') or 'n/a'}.",
                         "evidence": [{"label": x.get("title") or "IR document", "url": x.get("url")}], "facts": {"doc_id": x.get("id"), "doc_type": x.get("doc_type")}})
    return hits


def m_screen(data, mon, tickers, universe):
    p = mon.get("params") or {}
    res = data.screen(filters=p.get("filters") or [], sort=p.get("sort"), limit=500, fields=[])
    members = {r[0] for r in res["rows"]}
    if not universe:
        members &= set(tickers)
    prev = set(mon.get("baseline", {}).get("members") or [])
    first = "members" not in mon.get("baseline", {})
    mon.setdefault("baseline", {})["members"] = sorted(members)
    if first:
        return []
    hits = []
    for t in sorted(members - prev):
        hits.append({"key": f"scr:{t}:{dt.date.today().isoformat()}", "tickers": [t], "severity": "notable", "title": f"{t} entered your screen \"{mon.get('name')}\"",
                     "summary": f"{t} now meets: " + "; ".join(f"{f.get('field')} {f.get('op')} {f.get('value')}" for f in p.get("filters") or []) + ".",
                     "evidence": [{"label": res["source"]}], "facts": {"entered": True}})
    if p.get("report_exits"):
        for t in sorted(prev - members):
            hits.append({"key": f"scrx:{t}:{dt.date.today().isoformat()}", "tickers": [t], "severity": "info", "title": f"{t} left your screen \"{mon.get('name')}\"",
                         "summary": f"{t} no longer meets the screen.", "evidence": [], "facts": {"entered": False}})
    return hits


def m_feed(data, mon, tickers, universe):
    """Items from another job's feed (e.g. changes/feed.json, situations/index.json) for the scoped tickers."""
    p = mon.get("params") or {}
    path = p.get("path") or "changes/feed.json"
    d = data.get(path)
    if not d:
        return []
    items = d.get("items") if isinstance(d, dict) else d
    scope = set(tickers) if not universe else None
    minm = p.get("min_materiality")
    hits = []
    for it in items or []:
        t = it.get("ticker")
        if scope is not None and t not in scope:
            continue
        if minm is not None and isinstance(it.get("materiality"), (int, float)) and it["materiality"] < float(minm):
            continue
        key = it.get("id") or f"{t}:{it.get('kind')}:{it.get('date')}"
        hits.append({"key": f"feed:{path}:{key}", "tickers": [t] if t else [], "severity": it.get("severity") or "info",
                     "title": (it.get("title") or f"{t} {it.get('kind')}")[:140], "summary": (it.get("summary") or "")[:600],
                     "evidence": it.get("evidence") or ([{"label": it.get("source") or path, "url": it.get("url")}] if it.get("url") else []), "facts": it})
    return hits[:200]


TYPES = {
    "insider": (m_insider, "Insider buying or discretionary selling above a dollar threshold"),
    "filing_change": (m_filing_change, "Filing language flags: material weakness, going concern, covenants, bridge loans, auditor changes, new risk factors"),
    "contracts": (m_contracts, "Federal contract obligation shifts year over year"),
    "estimates": (m_estimates, "Consensus EPS or price-target revisions beyond a threshold"),
    "price": (m_price, "Price level, drawdown or RSI conditions"),
    "tech_signal": (m_tech_signal, "Technical signals, each with its historical base rate"),
    "upcoming": (m_upcoming, "Upcoming earnings, ex-dividend dates, macro releases, IPOs, trial readouts and proposed insider sales"),
    "ir_document": (m_ir_document, "New investor-relations documents (releases, decks, transcripts)"),
    "screen": (m_screen, "Companies entering (or leaving) a saved screen"),
    "feed": (m_feed, "Items from the change feed or situation screens"),
}

DEFAULT_EVERY = {"upcoming": 720, "contracts": 1440, "screen": 720, "estimates": 360, "price": 120, "tech_signal": 240}


def evaluate(data, mon, state, now=None):
    """Runs one monitor. Returns new hits (after dedup and cooldown) and updates the monitor's bookkeeping."""
    now = now or dt.datetime.now(dt.timezone.utc)
    fn = TYPES.get(mon.get("type"), (None,))[0]
    if not fn:
        raise ValueError(f"unknown monitor type {mon.get('type')!r}")
    tickers, universe = resolve_scope(data, mon, state)
    raw = fn(data, mon, tickers, universe)
    seen = {s["key"]: s["at"] for s in mon.get("seen") or []}
    cool = float(mon.get("cooldown_hours", 0) or 0)
    fresh = []
    for h in raw:
        last = seen.get(h["key"])
        if last is None or (cool and (now - dt.datetime.fromisoformat(last.replace("Z", "+00:00"))).total_seconds() > cool * 3600 and h["key"].startswith(("px:",))):
            fresh.append(h)
            seen[h["key"]] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    mon["seen"] = [{"key": k, "at": v} for k, v in sorted(seen.items(), key=lambda kv: kv[1])][-SEEN_KEEP:]
    mon["last_checked"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    mon["last_hits"] = len(fresh)
    return fresh
