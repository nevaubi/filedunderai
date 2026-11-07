"""AI-generated "Influence & flows read" per company, grounded only in the stored records.

Runs in the scheduled job (never on page load) when FIREWORKS_API_KEY is set. The model sees a numbered list of facts,
each derived from one stored record and carrying that record's id; it must return sentences that each cite one or more
fact ids. Every sentence is then checked:
- every cited id must exist in the fact list;
- every number in the sentence must appear in the cited facts' text (same digits after removing separators);
- any text in double quotes must appear verbatim in a cited fact (hearing quotes are verified against the transcript
  when the record is built).
Sentences that fail are dropped and counted; nothing unsourced is published.
"""
import json
import os
import re
import time

from .common import now_iso, request

PRIMARY = os.environ.get("FU_GOVREAD_MODEL", "kimi-k3p1-pro")
FALLBACK = os.environ.get("FU_GOVREAD_FALLBACK", "deepseek-v4p1-flash")
MAX_FACTS = 60


def _m(v):
    if v is None:
        return "n/a"
    a = abs(v)
    if a >= 1e9:
        return f"${v / 1e9:.2f}B"
    if a >= 1e6:
        return f"${v / 1e6:.1f}M"
    if a >= 1e3:
        return f"${v / 1e3:.0f}K"
    return f"${v:,.0f}"


def facts_for(gov, flows, ins=None):
    """[(id, text)] compact factual statements from the bundles."""
    out = []
    for t in (gov.get("congress_trades") or [])[:12]:
        flag = f"; sits on {', '.join(t['flag_committees'])}" if t.get("committee_flag") else ""
        out.append((t["id"], f"{t['member']} ({t.get('party') or 'party n/a'}, {t['chamber']}) reported a {t['type'].lower()} of {t['asset']} "
                             f"on {t.get('tx_date')} for {t['amount']}, filed {t.get('filed')} ({t.get('lag_days')} days later){flag}."))
    lob = gov.get("lobbying") or {}
    for q in (lob.get("quarters") or [])[-4:]:
        out.append((f"lda:q:{q['year']}Q{q['quarter']}", f"Federal lobbying spend {q['year']} Q{q['quarter']}: {_m(q['amount'])} ({q['basis']})."))
    for i in (lob.get("issues") or [])[:5]:
        out.append((f"lda:issue:{i['code']}", f"Lobbying issue area {i['name']} appears in {i['filings']} filings."))
    for b in (lob.get("bills") or [])[:6]:
        out.append((f"lda:bill:{b['bill']}:{b.get('congress')}", f"{b['bill']} ({b.get('congress')}th Congress) is named in {b['filings']} lobbying filings ({b.get('first')} to {b.get('last')})."))
    for l in (gov.get("legislation") or [])[:6]:
        if l.get("link") == "lobbied":
            out.append((l["id"], f"{l.get('label')} (from {l.get('bill')}), approved {l.get('date')}, was a bill the company lobbied on."))
    pac = gov.get("pac") or {}
    for c in (pac.get("cycles") or [])[:2]:
        out.append((f"fec:{c['cycle']}", f"Company PAC {c['cycle']} cycle: receipts {_m(c['receipts'])}, contributions to candidates {_m(c['to_candidates'])} "
                                         f"(Democrats {_m(c['dem'])}, Republicans {_m(c['rep'])})."))
    for h in (gov.get("hearings") or [])[:5]:
        q = (h.get("quotes") or [{}])[0]
        if q.get("text"):
            out.append((h["id"], f"Hearing \"{h.get('title')}\" ({h.get('date')}, {', '.join(h.get('committees') or [])}): {q.get('speaker') or 'unidentified speaker'} said \"{q['text'][:300]}\""))
    reg = gov.get("regulation") or {}
    for r in (reg.get("rules") or [])[:3] + (reg.get("proposed") or [])[:3]:
        out.append((r["id"], f"Federal Register {r.get('type')} \"{r.get('title')}\" published {r.get('date')} by {', '.join(r.get('agencies') or [])} names the company."))
    sp = gov.get("spending") or {}
    for f in (sp.get("fy") or [])[-3:]:
        out.append((f"usa:fy:{f['fy']}", f"Federal obligations FY{f['fy']}{' (partial year)' if f.get('partial') else ''}: total {_m(f['total'])}, contracts {_m(f['contracts'])}, grants {_m(f['grants'])}."))
    hold = flows.get("holders13dg") or {}
    for h in (hold.get("current") or [])[:5]:
        if h.get("percent") is not None:
            out.append((h["id"], f"{h.get('filer')} reported {h['percent']}% ({h['form']}, filed {h['filed']})."))
    f13 = flows.get("inst13f") or {}
    tot = f13.get("totals") or {}
    if tot:
        out.append(("13f:totals", f"13F filers holding the stock for {f13.get('period')}: {tot.get('managers')} managers, {tot.get('new_positions')} new positions, {tot.get('exits')} exits versus {f13.get('prior_period')}."))
    for b in (f13.get("buyers") or [])[:3]:
        out.append((f"13f:{b['cik']}", f"{b.get('manager')} changed its position by {b['change']:,.0f} shares to {b['shares']:,.0f} in {f13.get('period')}."))
    for s in (f13.get("sellers") or [])[:3]:
        out.append((f"13f:{s['cik']}", f"{s.get('manager')} changed its position by {s['change']:,.0f} shares to {s['shares']:,.0f} in {f13.get('period')}."))
    wk = [w for w in (flows.get("ats_weekly") or []) if w.get("offex_share") is not None]
    if wk:
        w = wk[-1]
        out.append((f"finra:week:{w['week']}", f"Week of {w['week']}: off-exchange share of volume {w['offex_share'] * 100:.1f}% (ATS {w.get('ats_shares') or 0:,} shares, non-ATS {w.get('otc_shares') or 0:,} shares)."))
    si = flows.get("short_interest") or []
    if si:
        s = si[-1]
        out.append((f"finra:si:{s['date']}", f"Short interest {s['date']}: {s.get('short') or 0:,} shares, {s.get('dtc')} days to cover, change {s.get('chg_pct')}%."))
    if ins:
        w = ins.get("w365") or {}
        out.append(("form4:w365", f"Form 4 open-market activity last 12 months: {w.get('buys', 0)} buys ({_m(w.get('buy_value'))}), {w.get('sells', 0)} sales ({_m(w.get('sell_value'))}), {w.get('plan_sells', 0)} sales under 10b5-1 plans."))
    return out[:MAX_FACTS]


PROMPT = """You write a short factual read for a professional equity research terminal about {name} ({tk}): its political
and government exposure and who is trading or holding the stock. Use ONLY the facts below. Every sentence must cite the
fact ids it uses. Do not add any number, name, date or claim that is not in the cited facts. No investment advice, no
speculation about motives, no adjectives like "significant" unless a fact says so. 3 to 5 sentences, each under 35 words;
name at most three records in one sentence.

Facts:
{facts}

Return JSON: {{"sentences": [{{"text": "...", "refs": ["fact id", ...]}}]}}"""


def _nums(s):
    return {re.sub(r"[,$%]", "", n).rstrip(".") for n in re.findall(r"\$?\d[\d,]*(?:\.\d+)?%?", s)}


def verify(sentences, facts):
    by = dict(facts)
    ok, dropped = [], []
    for s in sentences or []:
        text = (s.get("text") or "").strip()
        refs = [r for r in s.get("refs") or [] if r in by]
        if not text or not refs or len(refs) != len(s.get("refs") or []):
            dropped.append({"text": text, "reason": "missing or unknown reference"})
            continue
        src_text = " ".join(by[r] for r in refs)
        src_nums = _nums(src_text)
        bad = [n for n in _nums(text) if n not in src_nums and not any(x.startswith(n) or n.startswith(x) for x in src_nums if len(n) >= 3)]
        if bad:
            dropped.append({"text": text, "reason": f"numbers not in cited facts: {', '.join(sorted(bad)[:4])}"})
            continue
        quotes = re.findall(r"\"([^\"]{8,})\"", text)
        from .gov_common import norm_text
        if any(norm_text(q) not in norm_text(src_text) for q in quotes):
            dropped.append({"text": text, "reason": "quoted text not verbatim in cited facts"})
            continue
        ok.append({"text": text, "refs": refs})
    return ok, dropped


def complete(prompt, max_tokens=6000):
    from .llm import chat_json
    obj, model, _ = chat_json([PRIMARY, FALLBACK], [{"role": "user", "content": prompt}], max_tokens=max_tokens, temperature=0.2, timeout=180)
    return obj, model


CHECK_PROMPT = """You audit sentences written about a company's government exposure and stock ownership. For each sentence you get
the facts it cites. Try to refute it. A sentence is supported only if the cited facts state everything it says: names, numbers,
dates, direction of change and any comparison. Wording that implies a motive, a cause or an importance the facts do not state
is not supported.

Return JSON: {{"verdicts": [{{"n": <sentence number>, "supported": true or false, "reason": "at most 20 words"}}]}}

{items}"""


def check(ok, facts, draft_model, post=None):
    """Second pass with the other model and different instructions; returns (kept, dropped, check_model)."""
    by = dict(facts)
    items = "\n\n".join(f"SENTENCE {n}: {s['text']}\nFACTS: " + " | ".join(f"[{r}] {by[r]}" for r in s["refs"]) for n, s in enumerate(ok, 1))
    model = FALLBACK if draft_model == PRIMARY else PRIMARY
    j = (post or _call)(model, CHECK_PROMPT.format(items=items))
    verdicts = {v.get("n"): v for v in j.get("verdicts") or [] if isinstance(v, dict)}
    kept, dropped = [], []
    for n, s in enumerate(ok, 1):
        v = verdicts.get(n)
        if v and v.get("supported") is True:
            kept.append(s)
        else:
            dropped.append({"text": s["text"], "reason": "check: " + ((v or {}).get("reason") or "no verdict")[:160]})
    return kept, dropped, model


def _call(model, prompt, max_tokens=8000):
    from .llm import chat_json
    obj, _, _ = chat_json([model], [{"role": "user", "content": prompt}], max_tokens=max_tokens, timeout=180)
    return obj


def read(tk, name, gov, flows, ins=None, complete_fn=None, post=None):
    """Draft, verify in code, then a second model must support each sentence. Only sentences that pass are published;
    if the check cannot run, the result carries status "unchecked" and no sentences."""
    if not os.environ.get("FIREWORKS_API_KEY") and complete_fn is None:
        return None
    facts = facts_for(gov, flows, ins)
    if len(facts) < 3:
        return {"status": "insufficient", "generated_at": now_iso(), "facts": len(facts)}
    t0 = time.time()
    j, model = (complete_fn or complete)(PROMPT.format(name=name, tk=tk, facts="\n".join(f"[{i}] {t}" for i, t in facts)))
    ok, dropped = verify(j.get("sentences"), facts)
    check_model = None
    if ok:
        try:
            ok, more, check_model = check(ok, facts, model, post=post)
            dropped += more
        except Exception as e:
            return {"status": "unchecked", "generated_at": now_iso(), "model": model, "error": str(e)[:200], "sentences": [], "dropped": dropped}
    return {"status": "ok" if ok else "all_dropped", "generated_at": now_iso(), "model": model, "check_model": check_model,
            "seconds": round(time.time() - t0, 1), "sentences": ok, "dropped": dropped,
            "facts": [{"id": i, "text": t} for i, t in facts if any(i in s["refs"] for s in ok)],
            "label": "AI-generated from the records on this tab, checked by a second model; each sentence links to its records."}
