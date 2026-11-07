"""Company brief: what changed for a company in the last 30 days, across sources, in a few checked sentences.

Background loop (draft, verify, check, rank and cut, publish), run on a schedule and never on page load:
1. Gather. Facts from what the pipeline already published, each with an id and the app tab that shows its record:
   price move against the S&P 500 (prices/), analyst actions and consensus changes (estimates/), the latest guidance
   items (estimates/ guidance), the next and last earnings (estimates/), the top verified filing insights
   (insights/), Form 4 activity (insiders/), short interest (flows/) and congressional trades (gov/).
2. Draft. One model writes 3 to 5 sentences on what changed and why it may matter, each citing fact ids.
3. Verify in code. Every id must exist; every number in a sentence must appear in a cited fact; quoted text must be
   verbatim in a cited fact (etl/gov_insights.verify).
4. Check. The other model tries to refute each sentence against its facts; unsupported sentences are dropped. If
   the check cannot run, nothing new is published.
5. Rank and cut. A sentence whose facts were all used in a brief published in the last 30 days is dropped as not new;
   at most five sentences are kept.
6. Publish brief/{T}.json with the facts behind every kept sentence, both models and a run id.
"""
import datetime as dt
import hashlib
import json
import re
import time
import uuid

from . import gov_insights as G
from .common import now_iso

STATE = "meta/company_brief_state.json"
WINDOW = 30
MAX_SENTENCES = 5

PROMPT = """You write the brief at the top of an equity research page for {name} ({tk}): what changed in the last 30 days
and why it may matter to an investor. Use ONLY the facts below. 3 to 5 sentences, most important first. Every sentence must
cite the fact ids it uses. Prefer sentences that connect facts from different sources (for example a guidance change and the
analyst actions that followed, or a filing change and the price move). Do not add any number, name, date or claim that is not
in the cited facts. No investment advice, no predictions, no adjectives like "significant" or "strong" unless a fact says so.

Facts:
{facts}

Return JSON: {{"sentences": [{{"text": "...", "refs": ["fact id", ...]}}]}}"""


def _pct(v, d=1):
    return f"{v * 100:+.{d}f}%"


def _ret(bars, days):
    if not bars:
        return None, None
    last_t, last_c = bars[-1][0], bars[-1][4]
    cut = last_t - days * 86400
    base = next((b for b in bars if b[0] >= cut), None)
    if not base or not base[4]:
        return None, None
    return last_c / base[4] - 1, dt.datetime.fromtimestamp(base[0], dt.timezone.utc).date().isoformat()


def gather(sb, tk, today=None):
    """[(id, text, link)] facts for one company; link = {"tab": ..., "section"?: ...} for the page that shows the record."""
    today = today or dt.date.today()
    since = (today - dt.timedelta(days=WINDOW)).isoformat()
    out = []
    px, spy = sb.get_json(f"prices/{tk.replace('.', '-')}.json") or {}, sb.get_json("prices/SPY.json") or {}
    r, base = _ret(px.get("d") or [], 30)
    if r is not None:
        rs, _ = _ret(spy.get("d") or [], 30)
        last = dt.datetime.fromtimestamp(px["d"][-1][0], dt.timezone.utc).date().isoformat()
        out.append(("px:30d", f"Share price change from {base} to {last}: {_pct(r)}" + (f" (S&P 500 ETF {_pct(rs)})." if rs is not None else "."),
                    {"tab": "technicals"}))
    est = sb.get_json(f"estimates/{tk}.json") or {}
    acts = [a for a in est.get("actions") or [] if (a.get("date") or "") >= since]
    for a in acts[:8]:
        pt = ""
        if a.get("pt_current") is not None:
            pt = f", price target {a['pt_prior']:g} to {a['pt_current']:g}" if a.get("pt_prior") is not None else f", price target {a['pt_current']:g}"
        rating = f"{a.get('rating_prior')} to {a.get('rating_current')}" if a.get("rating_prior") and a.get("rating_prior") != a.get("rating_current") else (a.get("rating_current") or "")
        out.append((f"act:{a['id']}", f"{a.get('date')}: {a.get('firm')} {(a.get('action') or '').lower()} {rating}{pt}.", {"tab": "estimates"}))
    snaps = est.get("snapshots") or []
    if snaps:
        now = snaps[-1]
        old = next((s for s in snaps if s.get("as_of", "") <= (today - dt.timedelta(days=25)).isoformat() and s["as_of"] >= (today - dt.timedelta(days=45)).isoformat()), None)
        for code, label in (("0q", "current quarter"), ("0y", "current fiscal year")):
            a, b = (old or {}).get("p", {}).get(code, {}), now.get("p", {}).get(code, {})
            if a.get("eps") and b.get("eps") and a.get("end") == b.get("end"):
                out.append((f"rev:{code}", f"Consensus EPS for the {label} (ending {b['end']}) moved from {a['eps']:.2f} on {old['as_of']} to {b['eps']:.2f} on {now['as_of']}.",
                             {"tab": "estimates"}))
    cons = est.get("consensus") or {}
    ne = cons.get("next_earnings") or {}
    if ne.get("date") and ne["date"] >= today.isoformat():
        eps = (ne.get("eps") or {}).get("avg")
        out.append(("earn:next", f"Next earnings {ne['date']}{' (date estimated)' if ne.get('is_estimate') else ''}" + (f"; consensus EPS {eps:.2f}." if eps is not None else "."),
                    {"tab": "estimates"}))
    last = next((s for s in reversed(est.get("surprises") or []) if s.get("eps_actual") is not None), None)
    if last and last.get("surprise_pct") is not None:
        out.append(("earn:last", f"Quarter ended {last['quarter_end']}: EPS {last['eps_actual']:.2f} against a consensus of {last['eps_estimate']:.2f} ({_pct(last['surprise_pct'])})." if last.get("eps_estimate") is not None else
                    f"Quarter ended {last['quarter_end']}: EPS {last['eps_actual']:.2f}.", {"tab": "estimates"}))
    gi = sorted([i for i in (est.get("guidance") or {}).get("items") or [] if (i.get("issued") or "") >= (today - dt.timedelta(days=60)).isoformat()],
                key=lambda i: i.get("issued") or "", reverse=True)
    for i in gi[:4]:
        q = re.sub(r"\s+", " ", i.get("quote") or "").strip()[:260]
        out.append((f"guid:{i['id']}", f"Guidance issued {i['issued']} for {i.get('label')} ({i.get('period')}){' raised' if i.get('action') == 'raise' else ' lowered' if i.get('action') == 'lower' else ''}: \"{q}\"",
                    {"tab": "estimates"}))
    ins = sb.get_json(f"insights/{tk}.json") or {}
    if (ins.get("filing") or {}).get("filed", "") >= (today - dt.timedelta(days=120)).isoformat():
        for i in (ins.get("insights") or [])[:3]:
            out.append((f"fi:{i['hash']}", f"{ins['filing']['form']} filed {ins['filing']['filed']} versus the prior year: {i['title']}. {i['text']}", {"tab": "overview"}))
    form4 = sb.get_json(f"insiders/{tk}.json") or {}
    w = form4.get("w90") or {}
    if w.get("buys") or w.get("sells"):
        out.append(("form4:w90", f"Form 4 open-market activity in the last 90 days: {w.get('buys', 0)} buys ({G._m(w.get('buy_value'))}), {w.get('sells', 0)} sales "
                                 f"({G._m(w.get('sell_value'))}), {w.get('plan_sells', 0)} of the sales under 10b5-1 plans.", {"tab": "insiders", "section": "form4"}))
    flows = sb.get_json(f"flows/{tk}.json") or {}
    si = flows.get("short_interest") or []
    if len(si) >= 2 and si[-1].get("short") and si[-2].get("short"):
        a, b = si[-2], si[-1]
        out.append(("si:last", f"Short interest {b['date']}: {b['short']:,} shares versus {a['short']:,} on {a['date']}, {b.get('dtc')} days to cover.",
                    {"tab": "insiders", "section": "trading"}))
    gov = sb.get_json(f"gov/{tk}.json") or {}
    ct = [t for t in gov.get("congress_trades") or [] if (t.get("tx_date") or "") >= (today - dt.timedelta(days=90)).isoformat()]
    if ct:
        buys = sum(1 for t in ct if t.get("type_code") == "P")
        out.append(("gov:ct90", f"Members of Congress reported {len(ct)} trades in the last 90 days ({buys} purchases, {len(ct) - buys} sales or exchanges).",
                    {"tab": "insiders", "section": "congress"}))
    return out


def facts_key(facts):
    return hashlib.sha1(json.dumps(sorted(i for i, _, _ in facts)).encode()).hexdigest()[:16]


def brief(sb, tk, name, state, today=None, complete_fn=None, post=None):
    today = today or dt.date.today()
    facts = gather(sb, tk, today)
    if len(facts) < 3:
        return None, "too few facts"
    pairs = [(i, t) for i, t, _ in facts]
    t0 = time.time()
    j, model = (complete_fn or G.complete)(PROMPT.format(name=name, tk=tk, facts="\n".join(f"[{i}] {t}" for i, t in pairs)))
    ok, dropped = G.verify(j.get("sentences"), pairs)
    check_model = None
    if ok:
        ok, more, check_model = G.check(ok, pairs, model, post=post)
        dropped += more
    # facts already used by a brief in the window (a re-run on the same fact set does not count against itself)
    recent = [set(h["refs"]) for h in state.get("history") or []
              if h.get("date", "") >= (today - dt.timedelta(days=WINDOW)).isoformat() and h.get("key") != facts_key(facts)]
    used = set().union(*recent) if recent else set()
    kept = []
    for s in ok:
        if used and set(s["refs"]) <= used:
            dropped.append({"text": s["text"], "reason": "not new: every cited fact was in a brief in the last 30 days"})
            continue
        kept.append(s)
    kept = kept[:MAX_SENTENCES]
    links = {i: l for i, _, l in facts}
    texts = dict(pairs)
    res = {"ticker": tk, "name": name, "generated": now_iso(), "run_id": uuid.uuid4().hex[:12], "window_days": WINDOW,
           "status": "ok" if kept else "all_dropped", "sentences": kept, "dropped": dropped,
           "facts": [{"id": i, "text": texts[i], "link": links[i]} for i in dict.fromkeys(r for s in kept for r in s["refs"])],
           "facts_considered": len(facts), "models": {"draft": model, "check": check_model}, "seconds": round(time.time() - t0, 1),
           "label": "AI-generated from the records on this site, checked by a second model; each sentence links to its records."}
    state.setdefault("history", []).append({"date": today.isoformat(), "refs": sorted({r for s in kept for r in s["refs"]}), "key": facts_key(facts)})
    state["history"] = state["history"][-12:]
    state.update({"key": facts_key(facts), "at": res["generated"]})
    return res, None


def run(sb, time_budget=640, per_run=15, log=print):
    """Rotation: companies whose fact set changed since their last brief, oldest brief first."""
    from .universe import UNIVERSE
    t0 = time.time()
    deadline = t0 + time_budget * 0.85
    st = sb.get_json(STATE) or {"tickers": {}, "cursor": 0}
    names = {t: n for t, c, n, *_ in UNIVERSE}
    order = sorted(names, key=lambda t: (st["tickers"].get(t) or {}).get("at", ""))
    done, failed, skipped = [], {}, 0
    for tk in order:
        if len(done) >= per_run or time.time() > deadline - 60:
            break
        s = st["tickers"].setdefault(tk, {})
        if s.get("at", "") >= (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=20)).strftime("%Y-%m-%dT%H:%M:%SZ"):
            continue
        try:
            facts = gather(sb, tk)
            if len(facts) >= 3 and facts_key(facts) == s.get("key"):
                s["at"] = now_iso()
                skipped += 1
                continue
            res, why = brief(sb, tk, names[tk], s)
        except Exception as e:
            failed[tk] = str(e)[:200]
            s["at"] = now_iso()  # move on; the next pass retries
            continue
        if res is None:
            s.update({"at": now_iso(), "skipped": why})
            continue
        sb.put_json(f"brief/{tk}.json", res, cache="max-age=1800")
        done.append({"ticker": tk, "sentences": len(res["sentences"]), "dropped": len(res["dropped"])})
    st["generated"] = now_iso()
    sb.put_json(STATE, st, cache="no-store")
    log(f"company brief: {len(done)} written, {skipped} unchanged, {len(failed)} failed, {round(time.time() - t0)}s")
    return {"done": done, "failed": failed, "unchanged": skipped}
