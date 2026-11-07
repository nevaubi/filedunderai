"""Filing insights: what in a company's latest 10-K or 10-Q would change an analyst's view, ranked and verified.

Pipeline per company (draft, verify, check, rank, publish):
1. Diff first. Input is the redline of the latest filing against the same filing a year earlier
   (redlines/{T}.json, deterministic paragraph alignment), so only added, removed or reworded passages are read.
2. Draft. One model proposes insights from the numbered passages, each with a type, the stake when the text states
   one, a direction, and a quote copied from the passage.
3. Verify in code. The quote must be 8-40 words and appear verbatim in the cited passage (the new text, or the old
   text for a removed passage); every number in the claim and the stake must appear in a cited passage.
4. Check. A second model with different instructions tries to refute each surviving claim against its passages and
   rejects any the quote does not support. If the check cannot run, nothing new is published.
5. Rank. Materiality is scored in code: model impact, type, novelty, stake relative to TTM revenue, the redline's own
   change score, and the section. Claims whose quote was published for the company in the last 30 days are dropped.
6. Publish insights/{T}.json with the filing, both models, the run id and the rejected counts.
"""
import datetime as dt
import hashlib
import json
import math
import os
import re
import time
import uuid

from .common import now_iso, request
from .supply_core import norm, quote_ok

URL = "https://api.fireworks.ai/inference/v1/chat/completions"
DRAFT_MODEL = os.environ.get("FU_INSIGHT_MODEL", "kimi-k3p1-pro")
CHECK_MODEL = os.environ.get("FU_INSIGHT_CHECK_MODEL", "deepseek-v4p1-flash")
STATE = "meta/filing_insights_state.json"
TYPES = ["guidance", "risk", "customer_supplier", "accounting", "litigation", "capital_allocation", "kpi", "regulation", "operations", "other"]
TYPE_WEIGHT = {"guidance": 15, "litigation": 12, "customer_supplier": 12, "capital_allocation": 10, "accounting": 10,
               "regulation": 8, "risk": 8, "kpi": 8, "operations": 5, "other": 0}
NOVELTY_WEIGHT = {"new": 10, "removed": 8, "changed": 6}
KEY_SECTIONS = re.compile(r"risk factors|md&a|management.s discussion|legal proceedings|subsequent events|commitments and contingencies", re.I)
MAX_RECORDS = 30
MAX_CHARS = 1100
REPEAT_DAYS = 30


# ---------------------------------------------------------------- records from the redline
def _sides(ch):
    """Old and new text of one redline change; for word-level ops the unchanged runs appear on both sides."""
    if ch.get("ops"):
        old = " ".join(t for op, t in ch["ops"] if op in ("=", "-"))
        new = " ".join(t for op, t in ch["ops"] if op in ("=", "+"))
        changed = " ".join(f"[{'+' if op == '+' else '-'}{t}]" for op, t in ch["ops"] if op != "=")
        return old, new, changed
    return ch.get("old") or "", ch.get("new") or "", ""


def _clip(s, n=MAX_CHARS):
    s = re.sub(r"\s+", " ", s or "").strip()
    return s if len(s) <= n else s[:n].rsplit(" ", 1)[0] + " ..."


def latest_comparison(doc):
    comps = [c for c in doc.get("comparisons") or [] if c.get("sections")]
    return max(comps, key=lambda c: c["new"]["filed"]) if comps else None


def records(comp):
    """Numbered passages for the model: the highest-scoring changed passages of the latest comparison."""
    out = []
    for s in comp["sections"]:
        for ch in s.get("changes") or []:
            if ch.get("kind") not in ("added", "removed", "modified", "numbers") or (ch.get("score") or 0) < 4:
                continue
            old, new, changed = _sides(ch)
            if len((new or old).split()) < 12:
                continue
            out.append({"section": s["title"], "kind": ch["kind"], "old": old, "new": new, "changed": changed,
                        "score": ch.get("score") or 0, "signals": ch.get("signals") or []})
    out.sort(key=lambda r: -r["score"])
    out = out[:MAX_RECORDS]
    for i, r in enumerate(out):
        r["id"] = f"R{i + 1}"
    return out


def _record_text(r):
    if r["kind"] == "added":
        return f"{r['id']} | {r['section']} | ADDED | NEW: {_clip(r['new'])}"
    if r["kind"] == "removed":
        return f"{r['id']} | {r['section']} | REMOVED | OLD: {_clip(r['old'])}"
    return f"{r['id']} | {r['section']} | {'REWORDED' if r['kind'] == 'modified' else 'NUMBERS CHANGED'} | OLD: {_clip(r['old'], 900)} | NEW: {_clip(r['new'], 900)} | CHANGES: {_clip(r['changed'], 500)}"


# ---------------------------------------------------------------- model calls
def _post(model, messages, max_tokens, deadline=None):
    """The named model first, the other one as fallback; the reply carries "_model" (which model answered)."""
    from .llm import chat_json
    order = [model] + [m for m in (DRAFT_MODEL, CHECK_MODEL) if m != model]
    obj, used, _meta = chat_json(order, messages, max_tokens=max_tokens, deadline=deadline)
    obj["_model"] = used
    return obj


DRAFT_SCHEMA = {"type": "object", "required": ["insights"], "properties": {"insights": {"type": "array", "items": {
    "type": "object", "required": ["type", "title", "text", "quote", "refs", "direction", "novelty", "impact"],
    "properties": {"type": {"type": "string", "enum": TYPES}, "title": {"type": "string"}, "text": {"type": "string"},
                   "quote": {"type": "string"}, "refs": {"type": "array", "items": {"type": "string"}},
                   "stake": {"type": ["object", "null"], "properties": {"value": {"type": ["number", "null"]}, "unit": {"type": ["string", "null"], "enum": ["USD", "percent", None]}, "what": {"type": "string"}}},
                   "direction": {"type": "string", "enum": ["positive", "negative", "mixed", "neutral"]},
                   "novelty": {"type": "string", "enum": ["new", "changed", "removed"]},
                   "impact": {"type": "integer", "minimum": 1, "maximum": 3}}}}}}

DRAFT_INSTRUCTIONS = """You are an equity analyst reading what changed in {name} ({ticker})'s {form} filed {filed}, compared with the same filing a year earlier.
Each numbered passage is one change: ADDED (new text), REMOVED (text dropped), REWORDED or NUMBERS CHANGED (old and new versions, with the changed words).
List the changes that could change an analyst's view of the company: new or dropped risks, guidance or outlook, customers or suppliers, accounting policies or estimates, litigation and investigations, buybacks, dividends, debt and capital allocation, disclosed KPIs, regulation, operations.
Skip boilerplate, date roll-forwards, renumbering, and changes that only restate the period.
For each: type (one of {types}); title of at most 12 words; text of at most 45 words saying what changed and why it matters, using only facts in the passages; quote = 8 to 40 consecutive words copied character for character from the NEW text of the passage (from the OLD text for a REMOVED passage), no ellipses; refs = the passage ids it rests on; stake = the dollar amount (unit USD, in dollars, e.g. 2100000000 for $2.1 billion) or percent (unit percent) at stake when a passage states one, otherwise null; direction for shareholders; novelty new, changed or removed; impact 1 (minor) to 3 (would move estimates or the thesis).
At most 8 items, most important first. Keep your reasoning short. Never write passage ids inside text. Return only JSON matching this schema: {schema}"""

CHECK_INSTRUCTIONS = """You audit analyst claims about a company filing. For each claim you get the passages it cites (old and new text of a change between two filings).
Try to refute the claim. A claim is supported only if the cited passages state it: the change it describes really happened between the old and new text, its numbers match the passages, and its quote says what the claim relies on. Interpretation that goes beyond the passages ("this signals", "likely") is not supported unless the passage says it.
Return JSON: {"verdicts": [{"id": "<claim id>", "supported": true or false, "reason": "at most 20 words"}]} with one verdict per claim."""


def draft(ticker, name, comp, recs, deadline=None, post=_post):
    """Returns (proposals, model that answered)."""
    msgs = [{"role": "system", "content": DRAFT_INSTRUCTIONS.format(name=name, ticker=ticker, form=comp["form"], filed=comp["new"]["filed"],
                                                                    types=", ".join(TYPES), schema=json.dumps(DRAFT_SCHEMA))},
            {"role": "user", "content": "\n".join(_record_text(r) for r in recs)}]
    data = post(DRAFT_MODEL, msgs, 12000, deadline)
    return data.get("insights") or [], data.get("_model", DRAFT_MODEL)


def check(claims, recs, deadline=None, post=_post, draft_model=DRAFT_MODEL):
    """Returns ({claim id: verdict}, model that answered)."""
    by = {r["id"]: r for r in recs}
    blocks = []
    for c in claims:
        cited = " || ".join(_record_text(by[r]) for r in c["refs"])
        blocks.append(f"CLAIM {c['id']}: {c['text']} | QUOTE: \"{c['quote']}\" | STAKE: {json.dumps(c.get('stake'))}\nCITED: {cited}")
    msgs = [{"role": "system", "content": CHECK_INSTRUCTIONS}, {"role": "user", "content": "\n\n".join(blocks)}]
    # the check runs on the model that did not draft
    other = DRAFT_MODEL if draft_model == CHECK_MODEL else CHECK_MODEL
    data = post(other, msgs, 6000, deadline)
    return {v.get("id"): v for v in data.get("verdicts") or [] if isinstance(v, dict)}, data.get("_model", other)


# ---------------------------------------------------------------- verification in code
def _numbers(s):
    out = set()
    for n in re.findall(r"\d[\d,]*(?:\.\d+)?", s or ""):
        n = n.replace(",", "").rstrip(".")
        if n:
            out.add(n.lstrip("0") or "0")
    return out


def _stake_ok(stake, cited_text):
    """A stated stake must be written in a cited passage: as dollars, millions, billions or a percent."""
    if not stake or stake.get("value") is None:
        return True
    v = float(stake["value"])
    nums = {float(x) for x in _numbers(cited_text) if re.fullmatch(r"\d+(\.\d+)?", x)}
    forms = {v} if stake.get("unit") == "percent" else {v, v / 1e3, v / 1e6, v / 1e9}
    return any(any(abs(f - n) <= max(0.005, abs(n) * 0.0005) for n in nums) for f in forms)


def verify(proposals, recs):
    by = {r["id"]: r for r in recs}
    kept, rejected = [], {"refs": 0, "quote": 0, "numbers": 0, "type": 0}
    for i, p in enumerate(proposals):
        refs = [r for r in p.get("refs") or [] if r in by]
        if not refs:
            rejected["refs"] += 1
            continue
        if p.get("type") not in TYPES:
            rejected["type"] += 1
            continue
        q = (p.get("quote") or "").strip().strip('"')
        src = next((r for r in refs if quote_ok(q, norm(by[r]["old"] if by[r]["kind"] == "removed" else by[r]["new"]))), None)
        if not src:
            rejected["quote"] += 1
            continue
        cited = " ".join(by[r]["old"] + " " + by[r]["new"] for r in refs)
        text = re.sub(r"\s*\(?\bR\d{1,3}\b\)?", "", p.get("text") or "").strip()
        missing = [n for n in _numbers(text) | _numbers(p.get("title")) if n not in _numbers(cited) and not re.fullmatch(r"(19|20)\d{2}", n)]
        if missing or not _stake_ok(p.get("stake"), cited):
            rejected["numbers"] += 1
            continue
        kept.append({"id": f"c{i + 1}", "type": p["type"], "title": (p.get("title") or "").strip()[:120], "text": text[:400], "quote": q,
                     "refs": refs, "quote_ref": src, "stake": p.get("stake") if (p.get("stake") or {}).get("value") is not None else None,
                     "direction": p.get("direction") if p.get("direction") in ("positive", "negative", "mixed", "neutral") else "neutral",
                     "novelty": p.get("novelty") if p.get("novelty") in NOVELTY_WEIGHT else "changed",
                     "impact": max(1, min(3, int(p.get("impact") or 1)))})
    return kept, rejected


def materiality(c, recs, revenue):
    """Score in code; the parts are kept so the page can show why an item ranks where it does."""
    by = {r["id"]: r for r in recs}
    parts = {"impact": c["impact"] * 20, "type": TYPE_WEIGHT.get(c["type"], 0), "novelty": NOVELTY_WEIGHT.get(c["novelty"], 0)}
    st = c.get("stake") or {}
    if st.get("value") is not None and st.get("unit") == "USD" and revenue:
        parts["size"] = round(min(25.0, 250 * abs(float(st["value"])) / revenue), 1)
    elif st.get("value") is not None and st.get("unit") == "percent":
        parts["size"] = round(min(15.0, abs(float(st["value"])) / 2), 1)
    rs = max(by[r]["score"] for r in c["refs"])
    parts["redline"] = round(min(15.0, 2 * math.log2(rs + 1)), 1)
    if any(KEY_SECTIONS.search(by[r]["section"]) for r in c["refs"]):
        parts["section"] = 5
    return round(sum(parts.values()), 1), parts


def quote_hash(q):
    return hashlib.sha1(norm(q).encode()).hexdigest()[:16]


# ---------------------------------------------------------------- one company
def company(sb, ticker, name, revenue, state, deadline=None, post=_post, today=None, log=print):
    doc = sb.get_json(f"redlines/{ticker}.json")
    comp = latest_comparison(doc or {})
    if not comp:
        return None, "no redline"
    recs = records(comp)
    if len(recs) < 3:
        return None, "too few changed passages"
    today = today or dt.date.today()
    run_id = uuid.uuid4().hex[:12]
    proposals, draft_model = draft(ticker, name, comp, recs, deadline=deadline, post=post)
    kept, rejected = verify(proposals, recs)
    verdicts, check_model = check(kept, recs, deadline=deadline, post=post, draft_model=draft_model) if kept else ({}, None)
    passed = []
    rejected["check"] = 0
    for c in kept:
        v = verdicts.get(c["id"])
        if not v or v.get("supported") is not True:
            rejected["check"] += 1
            continue
        c["check"] = {"model": check_model, "reason": (v.get("reason") or "")[:200]}
        passed.append(c)
    hist = {h["h"]: h for h in (state.get("history") or []) if h.get("date", "") >= (today - dt.timedelta(days=REPEAT_DAYS)).isoformat()}
    prev_accn = state.get("accn")
    rejected["repeat"] = 0
    out = []
    for c in passed:
        h = quote_hash(c["quote"])
        # a quote shown for an earlier filing in the last 30 days is not new; a re-run of the same filing keeps it
        if h in hist and hist[h].get("accn") != comp["new"]["accn"]:
            rejected["repeat"] += 1
            continue
        score, parts = materiality(c, recs, revenue)
        by = {r["id"]: r for r in recs}
        src = by[c["quote_ref"]]
        out.append(dict(c, score=score, score_parts=parts, section=src["section"], change=src["kind"], hash=h,
                        passage={"old": _clip(src["old"], 900) if src["kind"] != "added" else None,
                                 "new": _clip(src["new"], 900) if src["kind"] != "removed" else None},
                        doc="old" if src["kind"] == "removed" else "new"))
    out.sort(key=lambda c: -c["score"])
    for i, c in enumerate(out):
        c["rank"] = i + 1
        c.pop("quote_ref", None)
    result = {"ticker": ticker, "name": name, "generated": now_iso(), "run_id": run_id, "label": "AI-generated, verified against the filing",
              "filing": {k: comp["new"][k] for k in ("accn", "form", "filed", "report", "doc", "url")},
              "compared_to": {k: comp["old"][k] for k in ("accn", "form", "filed", "report", "doc", "url")},
              "kind": comp["kind"], "insights": out, "proposed": len(proposals), "rejected": rejected,
              "models": {"draft": draft_model, "check": check_model},
              "method": "Drafted from the passages that changed against the prior-year filing; each quote is verbatim in the filing and every "
                        "number appears in a cited passage; a second model rejected claims the passages do not support; ranked by a "
                        "materiality score computed in code."}
    new_hist = list(hist.values()) + [{"h": c["hash"], "date": today.isoformat(), "accn": comp["new"]["accn"]} for c in out[:5]]
    state.update({"accn": comp["new"]["accn"], "at": result["generated"], "history": new_hist[-60:], "n": len(out)})
    if prev_accn and prev_accn != comp["new"]["accn"]:
        state["prev_accn"] = prev_accn
    return result, None


# ---------------------------------------------------------------- coverage index
INDEX = "insights/index.json"


def index_row(res):
    """The cross-company feed's row for one company: its filing and its top three insights."""
    f = res["filing"]
    return {"ticker": res["ticker"], "name": res.get("name"), "form": f["form"], "filed": f["filed"], "accn": f["accn"], "doc": f["doc"],
            "generated": res["generated"], "n": len(res["insights"]),
            "top": [{"title": i["title"], "type": i["type"], "direction": i["direction"], "score": i["score"],
                     "quote": i["quote"], "doc": i["doc"]} for i in res["insights"][:3]]}


def merge_index(sb, rows):
    idx = sb.get_json(INDEX) or {"companies": {}}
    idx.setdefault("companies", {}).update(rows)
    idx["generated"] = now_iso()
    idx["count"] = len(idx["companies"])
    sb.put_json(INDEX, idx, cache="max-age=600")
    return idx["count"]


# ---------------------------------------------------------------- rotation
def run(sb, time_budget=640, max_companies=10, log=print):
    t0 = time.time()
    deadline = t0 + time_budget * 0.85
    idx = sb.get_json("redlines/index.json") or {}
    st = sb.get_json(STATE) or {"tickers": {}}
    home = sb.get_json("index.json") or {}
    revenue = {c.get("ticker"): c.get("revenue_ttm") for c in home.get("companies") or []} if isinstance(home, dict) else {}
    todo = []
    for t, row in (idx.get("companies") or {}).items():
        comps = row.get("comparisons") or []
        if not comps:
            continue
        filed = max(c["filed"] for c in comps)
        s = st["tickers"].get(t) or {}
        # a company is due when its latest compared filing is not the one its insights were drawn from;
        # a re-run of the redline for the same filings does not trigger a new model run
        if s.get("filed") != filed:
            s.pop("errors", None)
        if s.get("filed") != filed and (s.get("errors") or 0) < 3:
            todo.append((filed, t, row.get("name") or t))
    todo.sort(reverse=True)
    done, failed, published = [], {}, {}

    def one(item):
        filed, t, name = item
        if time.time() > deadline - 150:
            return
        s = st["tickers"].setdefault(t, {})
        try:
            res, why = company(sb, t, name, revenue.get(t), s, deadline=deadline, log=log)
        except Exception as e:
            failed[t] = str(e)[:200]
            s["error"] = failed[t]
            s["errors"] = (s.get("errors") or 0) + 1  # three failures park the company until its redline changes
            return
        if res is None:
            s.update({"at": now_iso(), "skipped": why, "filed": filed})
            return
        sb.put_json(f"insights/{t}.json", res, cache="max-age=1800")
        s.pop("error", None)
        s.pop("errors", None)
        s["filed"] = filed
        done.append({"ticker": t, "insights": len(res["insights"]), "rejected": res["rejected"]})
        published[t] = index_row(res)

    # three companies at a time: each is two model calls of one to three minutes
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(3) as ex:
        list(ex.map(one, todo[:max_companies]))
    if published:
        merge_index(sb, published)
    st["generated"] = now_iso()
    sb.put_json(STATE, st, cache="no-store")
    log(f"filing insights: {len(done)} companies, {len(failed)} failed, {len(todo)} due, {round(time.time() - t0)}s")
    return {"done": done, "failed": failed, "due": len(todo)}
