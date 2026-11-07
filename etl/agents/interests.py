"""What the agent learns the user cares about, and the periodic "insights for you" run.

Signals (sent by the app only while the user's learning setting is on; the user can see, pin, mute and clear them):
    view      opened a company or page            weight 1
    section   used a section (insiders, filings...) weight 1.5
    question  asked the assistant about something  weight 3
    screen    ran or saved a screen                weight 2
    watch     added to the watchlist               weight 5
    task      created a task or monitor            weight 3
Weights decay with a 21-day half-life. Pins add a fixed boost; muted tickers and topics are excluded.

Insights: deterministic candidates from the user's top tickers and topics (insider trades, filing flags, contract
shifts, estimate moves, upcoming events, KPI swings, technical signals with base rates), then one model call picks
the 3-5 most relevant, non-obvious ones and writes them up. A write-up is kept only if it cites candidate ids, and
its evidence is taken from those candidates, never from the model.
"""
import datetime as dt
import math
import re

from .. import llm
from . import monitors as M

KIND_W = {"view": 1.0, "section": 1.5, "question": 3.0, "screen": 2.0, "watch": 5.0, "task": 3.0}
HALF_LIFE = 21.0
PIN_BOOST = 10.0
TOPIC_RX = [
    ("insiders", r"insider|form 4|buyback"), ("filings", r"filing|redline|10-k|10-q|8-k|risk factor"), ("earnings", r"earning|estimate|consensus|guidance|surprise"),
    ("technicals", r"technical|market|chart|rsi|momentum|signal|breadth"), ("supply_chain", r"supply|supplier|customer|tsmc|chokepoint"),
    ("government", r"gov|contract|lobby|congress|alt-data|alt data|usaspending|fda"), ("ir_kpis", r"\bir\b|kpi|segment|investor relations|deck"),
    ("macro", r"macro|fed|cpi|rates|yield|inflation|jobs"), ("valuation", r"valuation|dcf|multiple|p/e|cheap|expensive"),
    ("sectors", r"sector|industry|rotation"), ("screener", r"screen"), ("themes", r"theme|tariff|ai\b|glp-1|reshoring"),
]


def topics_of(text):
    s = (text or "").lower()
    return [name for name, rx in TOPIC_RX if re.search(rx, s)]


def add_events(state, events, now=None):
    if not state["settings"].get("learning", True):
        return 0
    now = now or dt.datetime.now(dt.timezone.utc)
    ev = state["interests"]["events"]
    n = 0
    for e in events[:200]:
        kind = e.get("kind")
        if kind not in KIND_W:
            continue
        t = str(e.get("t") or "").upper()[:10] or None
        rec = {"ts": e.get("ts") or now.strftime("%Y-%m-%dT%H:%M:%SZ"), "kind": kind}
        if t and re.match(r"^[A-Z][A-Z0-9.\-]{0,9}$", t):
            rec["t"] = t
        for k in ("path", "section", "q"):
            if e.get(k):
                rec[k] = str(e[k])[:160]
        ev.append(rec)
        n += 1
    return n


def weights(state, sector_of=lambda t: None, now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    it = state["interests"]
    muted_t = set(it["muted"].get("tickers") or [])
    muted_k = set(it["muted"].get("topics") or [])
    W = {"tickers": {}, "sectors": {}, "topics": {}}
    for e in it["events"]:
        try:
            age = (now - dt.datetime.fromisoformat(e["ts"].replace("Z", "+00:00"))).total_seconds() / 86400
        except Exception:
            continue
        w = KIND_W.get(e["kind"], 1.0) * math.pow(0.5, max(0.0, age) / HALF_LIFE)
        t = e.get("t")
        if t and t not in muted_t:
            W["tickers"][t] = W["tickers"].get(t, 0) + w
            s = sector_of(t)
            if s:
                W["sectors"][s] = W["sectors"].get(s, 0) + w * 0.5
        for k in topics_of(" ".join(str(e.get(x) or "") for x in ("path", "section", "q"))):
            if k not in muted_k:
                W["topics"][k] = W["topics"].get(k, 0) + w
    for t in state.get("watchlist") or []:
        if t not in muted_t:
            W["tickers"][t] = W["tickers"].get(t, 0) + 2.0
    for t in it["pins"].get("tickers") or []:
        W["tickers"][t] = W["tickers"].get(t, 0) + PIN_BOOST
    for k in it["pins"].get("topics") or []:
        W["topics"][k] = W["topics"].get(k, 0) + PIN_BOOST
    for k in W:
        W[k] = dict(sorted(((a, round(b, 3)) for a, b in W[k].items()), key=lambda kv: -kv[1])[:40])
    it["weights"] = W
    it["updated_at"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    return W


def _probe(fn, data, tickers, params, lookback):
    mon = {"params": dict(params, lookback_days=lookback), "created_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "name": "insights"}
    try:
        return fn(data, mon, tickers, False)
    except Exception as e:
        print("insight probe failed:", fn.__name__, str(e)[:120])
        return []


def candidates(data, state, W, max_tickers=8):
    tickers = [t for t in W["tickers"] if data.row(t)][:max_tickers]
    topics = list(W["topics"])[:5]
    cands = []
    add = lambda src, hs: cands.extend(dict(h, source=src) for h in hs)
    add("insider", _probe(M.m_insider, data, tickers, {"min_value": 100_000, "side": "both"}, 30))
    add("filing_change", _probe(M.m_filing_change, data, tickers,
                                {"tags": ["material weakness", "going concern", "covenant", "default", "restatement", "impairment", "customer concentration", "liquidity", "new risk factor", "bridge loan"]}, 21))
    add("contracts", _probe(M.m_contracts, data, tickers, {"min_change_pct": 0.2, "min_amount": 5e7}, 365))
    add("upcoming", _probe(M.m_upcoming, data, tickers, {"kinds": ["earnings", "ex_dividend"], "within_days": 14}, 0))
    add("tech_signal", _probe(M.m_tech_signal, data, tickers, {"signals": ["low_52w", "rsi_oversold", "gap_down", "gap_up", "high_52w", "rsi_overbought"]}, 10))
    for t in tickers[:6]:
        e = data.estimate_row(t) or {}
        if isinstance(e.get("target_upside_median"), (int, float)) and abs(e["target_upside_median"]) >= 0.2:
            cands.append({"key": f"tgt:{t}", "source": "estimates", "tickers": [t], "severity": "info",
                          "title": f"{t}: median price target implies {M.pct(e['target_upside_median'])}",
                          "summary": f"{e.get('target_n')} analysts; median target {e.get('target_median')}; ratings {e.get('ratings')}.",
                          "evidence": [{"label": "Consensus (Yahoo Finance)", "url": f"https://finance.yahoo.com/quote/{t}/analysis/"}], "facts": e})
        k = data.kpis(t, limit=60)
        movers = [x for x in k.get("kpis") or [] if isinstance(x.get("yoy"), (int, float)) and abs(x["yoy"]) >= 0.15 and x.get("latest")]
        movers.sort(key=lambda x: -abs(x["yoy"]))
        for x in movers[:2]:
            lt = x["latest"]
            cands.append({"key": f"kpi:{t}:{x['name']}:{lt.get('period_end')}", "source": "kpi", "tickers": [t], "severity": "notable",
                          "title": f"{t}: {x['name']} {M.pct(x['yoy'])} YoY ({lt.get('label') or lt.get('period_end')})",
                          "summary": f"{x['name']} was {lt.get('v'):,} in {lt.get('label') or lt.get('period_end')} ({M.pct(x['yoy'])} YoY), reported in {lt.get('doc')}.",
                          "evidence": [{"label": lt.get("doc") or "IR document", "url": lt.get("url"), "quote": lt.get("quote")}], "facts": {"yoy": x["yoy"], "value": lt.get("v")}})
    if "macro" in topics or "technicals" in topics:
        m = data.market()
        b = m.get("breadth") or {}
        if b:
            cands.append({"key": f"breadth:{b.get('date')}", "source": "market", "tickers": [], "severity": "info",
                          "title": f"Breadth: {b['pct_above_200d'] * 100:.0f}% of stocks above their 200-day average",
                          "summary": f"{b['pct_above_50d'] * 100:.0f}% above the 50-day; {b['new_highs']} new highs vs {b['new_lows']} new lows on {b['date']}.",
                          "evidence": [{"label": "Breadth (tech/breadth.json)"}], "facts": b})
    for i, c in enumerate(cands):
        c["cid"] = f"c{i + 1}"
    return cands, tickers, topics


SYSTEM = """You pick and write short research insights for one investor, based only on the candidate facts given.
Return JSON: {"insights": [{"title": "...", "body": "2-3 sentences: what happened, the numbers, why it matters given the user's interests", "why_you": "one short line linking to their interests", "cids": ["c1", ...]}],
 "monitors": [{"name": "...", "type": "insider|filing_change|contracts|estimates|price|tech_signal|upcoming|ir_document|screen", "params": {...}, "scope": {"kind": "tickers", "tickers": ["..."]}, "why": "..."}]}
Rules: 3-5 insights, ranked by relevance to the user's top tickers and topics; prefer non-obvious combinations (e.g. an insider buy shortly after a negative filing change, a KPI swing ahead of earnings). Every insight cites at least one candidate id in cids. Use only numbers present in the candidates. No advice, no hype. Suggest at most 3 monitors that would have caught these, using the given types."""


def insights(data, state, post=None, now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    W = weights(state, data.sector_of, now)
    cands, tickers, topics = candidates(data, state, W)
    if not cands:
        return None
    by = {c["cid"]: c for c in cands}
    slim = [{"cid": c["cid"], "source": c["source"], "tickers": c.get("tickers"), "title": c["title"], "summary": c["summary"][:400], "severity": c.get("severity")} for c in cands[:60]]
    user = {"top_tickers": tickers, "top_topics": topics, "watchlist": state.get("watchlist", [])[:20], "candidates": slim}
    picked, mons = [], []
    try:
        import json
        obj, _, _ = llm.chat_json(["kimi-k3p1-pro", "deepseek-v4p1-flash"], [{"role": "system", "content": SYSTEM}, {"role": "user", "content": json.dumps(user)}],
                                  max_tokens=5000, temperature=0.2, timeout=150, post=post)
        for ins in obj.get("insights") or []:
            cids = [c for c in ins.get("cids") or [] if c in by]
            if not cids or not ins.get("title"):
                continue
            picked.append({"title": str(ins["title"])[:140], "body": str(ins.get("body") or "")[:900], "why_you": str(ins.get("why_you") or "")[:200],
                           "evidence": [e for c in cids for e in by[c].get("evidence") or []][:6], "tickers": sorted({t for c in cids for t in by[c].get("tickers") or []}),
                           "cids": cids})
        for m in obj.get("monitors") or []:
            if m.get("type") in M.TYPES and isinstance(m.get("params"), dict):
                mons.append({"name": str(m.get("name") or m["type"])[:80], "type": m["type"], "params": m["params"],
                             "scope": m.get("scope") if isinstance(m.get("scope"), dict) else {"kind": "watchlist"}, "why": str(m.get("why") or "")[:200]})
    except Exception as e:
        print("insights model call failed:", str(e)[:200])
    if not picked:  # deterministic fallback: the most severe candidates as they are
        rank = {"high": 0, "notable": 1, "info": 2}
        for c in sorted(cands, key=lambda c: rank.get(c.get("severity"), 3))[:4]:
            picked.append({"title": c["title"], "body": c["summary"], "why_you": "", "evidence": c.get("evidence") or [], "tickers": c.get("tickers") or [], "cids": [c["cid"]]})
    return {"insights": picked[:5], "monitors": mons[:3], "weights": W, "candidates": len(cands)}
