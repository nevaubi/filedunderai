"""Earnings workflow: preview packs before each report, a live scorecard minutes after the 8-K lands, a diff of the
call against the previous one, and a season view across all reporters.

Everything is assembled from records the pipeline already publishes, and every figure carries the record it came from:
- estimates/{T}.json      consensus (Yahoo Finance), revisions, analyst actions, price targets, surprises
- estimates/index.json    next report date, last report and its one-day move
- guidance/{T}.json       guided ranges by period, actuals and outcomes, management credibility summary
- quant/t/{T}.json        reaction to every Item 2.02 8-K (close-to-close, abnormal vs SPY, gap, 5/20-day drift)
- kpistd/t/{T}.json, kpis/{T}.json   industry KPIs and company KPIs from IR documents, each value with its quote
- redlines/{T}.json       what changed in the latest 10-Q/10-K
- insiders/{T}.json       Form 4 activity since the last report
- calls/{T}.json          analysed earnings calls (themes, guidance, Q&A with answer quality)

Outputs (public-data, read by the app through /api/app-data):
- earnings/preview/{T}.json  for companies reporting within the next three trading days
- earnings/score/{T}.json    the latest scorecard, written when an Item 2.02 8-K is detected on EDGAR's current feed
- earnings/calls/{T}.json    the latest call against the previous one
- earnings/season.json       upcoming reports, reporters of the last 30 days, sector beat rates, what moved shares

Model use (draft, verify in code, second-model check), only when FIREWORKS_API_KEY is set:
- preview "listen for" questions: drafted from numbered facts, every number and id verified in code (gov_insights.verify),
  then the other model must support each one; without a key or if the check fails, rule-written questions from the same
  facts are published and labelled as such.
- scorecard KPIs and guidance: the model returns each item with the printed number and the sentence it came from; code
  checks the sentence is verbatim in the release and that the printed number times its scale equals the value; then
  the other model must confirm each item. Headline revenue and EPS come from rules first.
"""
import datetime as dt
import html
import json
import math
import os
import re
import statistics
import time
import urllib.parse

from .common import SEC_UA, now_iso, request, sec_throttle

STATE = "meta/earnings_state.json"
PREVIEW_AHEAD = 3            # trading days before the report
PREVIEW_BEHIND = 0           # keep generating on the report day itself
SEASON_DAYS = 30
FEED = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=8-K&company=&dateb=&owner=include&start={start}&count=100&output=atom"
SCALE = {"thousand": 1e3, "thousands": 1e3, "million": 1e6, "millions": 1e6, "billion": 1e9, "billions": 1e9, "": 1.0, None: 1.0}


# ------------------------------------------------------------------ small helpers
def _get(sb, path):
    try:
        return sb.get_json(path)
    except Exception:
        return None


def _list(sb, prefix):
    """{name: updated_at} for the files directly under a public-data prefix (storage list API, paged)."""
    out, off = {}, 0
    while True:
        body = json.dumps({"prefix": prefix, "limit": 1000, "offset": off}).encode()
        r = json.loads(request(f"{sb.url}/storage/v1/object/list/public-data", method="POST", data=json.loads(body),
                               headers={"apikey": sb.key, "Authorization": f"Bearer {sb.key}"}, timeout=30, retries=2))
        for x in r:
            if x.get("metadata"):
                out[x["name"]] = x.get("updated_at") or ""
        off += 1000
        if len(r) < 1000:
            return out


def _date(s):
    try:
        return dt.date.fromisoformat(str(s)[:10])
    except Exception:
        return None


def trading_days_until(today, day):
    """Weekdays strictly after `today` up to and including `day` (negative if `day` is past). Exchange holidays are
    not removed, so a preview can be written one day early around a holiday."""
    if day is None:
        return None
    if day < today:
        return -sum(1 for k in range(1, (today - day).days + 1) if (today - dt.timedelta(days=k)).weekday() < 5)
    return sum(1 for k in range(1, (day - today).days + 1) if (today + dt.timedelta(days=k)).weekday() < 5)


def _money(v):
    if v is None:
        return "n/a"
    a = abs(v)
    sign = "-" if v < 0 else ""
    if a >= 1e9:
        return f"{sign}${a / 1e9:.2f}B"
    if a >= 1e6:
        return f"{sign}${a / 1e6:.1f}M"
    if a >= 1e3:
        return f"{sign}${a / 1e3:.1f}K"
    return f"{sign}${a:.2f}"


def _pct(v, d=1):
    return "n/a" if v is None else f"{v * 100:+.{d}f}%"


def _fmt(v, unit):
    if v is None:
        return "n/a"
    u = (unit or "").lower()
    if u in ("usd",):
        return _money(v)
    if u in ("usd/share", "usdpershare", "per_share", "eps"):
        return f"${v:.2f}"
    if u in ("pct", "%", "percent"):
        return f"{v:.1f}%" if abs(v) > 1.5 else f"{v * 100:.1f}%"
    if abs(v) >= 1e6:
        return f"{v / 1e6:.2f}M"
    if abs(v) >= 1e3:
        return f"{v:,.0f}"
    return f"{v:g}"


def _yoy(a, b):
    try:
        if a is None or b is None or b == 0:
            return None
        if b < 0 and a < 0:
            return None
        return a / b - 1
    except Exception:
        return None


def _norm(s):
    s = html.unescape(str(s or ""))
    s = s.replace(" ", " ").replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    s = s.replace("–", "-").replace("—", "-").replace("−", "-")
    return " ".join(s.split())


def _parse_num(printed):
    """'1,329' -> 1329.0, '(0.53)' -> -0.53, '$5.29' -> 5.29."""
    if printed is None:
        return None
    s = str(printed).strip()
    neg = s.startswith("(") and s.endswith(")") or s.startswith("-")
    s = re.sub(r"[^\d.]", "", s)
    if not s or s.count(".") > 1:
        return None
    v = float(s)
    return -v if neg else v


def _close(a, b, rel=1e-6):
    if a is None or b is None:
        return False
    return abs(a - b) <= max(abs(b), 1.0) * rel


def _median(xs):
    xs = [x for x in xs if x is not None and isinstance(x, (int, float)) and math.isfinite(x)]
    return statistics.median(xs) if xs else None


# ------------------------------------------------------------------ preview inputs
def consensus_block(est):
    """Current-quarter and current-year consensus with the revision history the estimates job keeps."""
    if not est or not est.get("consensus"):
        return None
    c = est["consensus"]
    per = {p.get("code"): p for p in c.get("periods") or []}
    q, y = per.get("0q"), per.get("0y")
    if not q:
        return None

    def side(p, key):
        x = (p or {}).get(key) or {}
        if x.get("avg") is None:
            return None
        return {"avg": x.get("avg"), "low": x.get("low"), "high": x.get("high"), "n": x.get("n"), "year_ago": x.get("year_ago"),
                "yoy": _yoy(x.get("avg"), x.get("year_ago"))}
    snaps = est.get("snapshots") or []
    hist = []
    for s in snaps:
        p = (s.get("p") or {}).get("0q") or {}
        if p.get("end") == q.get("end"):
            hist.append({"as_of": s.get("as_of"), "eps": p.get("eps"), "rev": p.get("rev"), "eps_n": p.get("eps_n")})
    rev = est.get("revisions") or {}
    return {"period": {"label": q.get("label"), "end": q.get("end"), "code": "0q"},
            "eps": side(q, "eps"), "revenue": side(q, "revenue"),
            "year": {"label": (y or {}).get("label"), "end": (y or {}).get("end"), "eps": side(y, "eps"), "revenue": side(y, "revenue")} if y else None,
            "revisions": rev.get("0q"), "history": hist[-12:],
            "source": {"provider": c.get("provider"), "url": c.get("url"), "as_of": c.get("as_of")}}


def street_block(est):
    if not est:
        return None
    t = est.get("targets") or {}
    rows = t.get("rows") or []
    pts = [r.get("pt") for r in rows if isinstance(r.get("pt"), (int, float))]
    s90 = (est.get("summary") or {}).get("d90") or {}
    acts = [a for a in est.get("actions") or [] if a.get("date")]
    acts.sort(key=lambda a: a["date"], reverse=True)
    return {"ratings": (est.get("ratings") or {}).get("counts"), "target_median": _median(pts), "targets_n": len(pts),
            "price": (est.get("price") or {}).get("close"), "price_date": (est.get("price") or {}).get("date"),
            "d90": s90, "recent_actions": [{k: a.get(k) for k in ("date", "firm", "action", "action_pt", "rating_prior", "rating_current", "pt_prior", "pt_current")}
                                           for a in acts[:6]]}


def _taxonomy_ids_for(industry, taxonomy):
    out = []
    for k in (taxonomy or {}).get("kpis") or []:
        if industry and industry in (k.get("industries") or []):
            out.append(k["id"])
    return out


def kpi_block(kstd, kpis, taxonomy, limit=8):
    """KPIs that matter: the standardized KPIs defined for the company's industry first, then the company's own
    operating KPIs from its IR documents. Each point keeps the document, quote and link it was read from."""
    out, seen = [], set()
    industry = (kstd or {}).get("industry")
    wanted = _taxonomy_ids_for(industry, taxonomy)
    canon_of = {m.get("kpi_id"): m.get("canonical") for m in (kstd or {}).get("map") or []}
    series = (kstd or {}).get("series") or {}
    docs = (kstd or {}).get("docs") or {}
    general = {"capex", "operating_cash_flow", "share_repurchases", "dividends_paid", "total_revenue", "fcf"}
    order = sorted(series.items(), key=lambda kv: (0 if canon_of.get(kv[0]) in wanted else 1 if canon_of.get(kv[0]) not in general else 2))
    fresh = dt.date.today() - dt.timedelta(days=220)
    for kid, s in order:
        q = s.get("Q") or []
        if len(q) < 3 or (_date(q[-1][0]) or dt.date.min) < fresh or not q[-1][1]:
            continue
        pts = []
        for row in q[-9:]:
            end, v, label, doc = row[0], row[1], row[2], row[3] if len(row) > 3 else None
            quote = row[5] if len(row) > 5 else None
            d = docs.get(doc) if isinstance(docs, dict) else None
            url = d[3] if isinstance(d, list) and len(d) > 3 else (d or {}).get("url") if isinstance(d, dict) else None
            title = d[0] if isinstance(d, list) and d else (d or {}).get("title") if isinstance(d, dict) else None
            pts.append({"end": end, "label": label, "v": v, "quote": quote, "url": url, "doc": doc, "doc_title": title})
        canon = canon_of.get(kid)
        if canon in seen:
            continue
        seen.add(canon or kid)
        out.append(_kpi_item(kid, s.get("name") or kid, s.get("unit"), pts, "kpistd", canon, canon in wanted))
        if len(out) >= limit:
            return out
    for g in (kpis or {}).get("groups") or []:
        cat = (g.get("category") or "").lower()
        if any(w in cat for w in ("reconciliation", "non-gaap", "statement", "balance sheet", "cash flow")):
            continue
        for it in g.get("items") or []:
            vals = ((it.get("values") or {}).get("Q") or {})
            if len(vals) < 3 or it.get("id") in seen or str(it.get("id") or "").startswith(("consolidated_", "condensed_")):
                continue
            pts = [{"end": end, "label": x.get("label"), "v": x.get("v"), "quote": x.get("quote"), "url": x.get("url"), "doc": x.get("doc")}
                   for end, x in sorted(vals.items())[-9:]]
            if not pts or (_date(pts[-1]["end"]) or dt.date.min) < fresh or not pts[-1].get("v"):
                continue
            seen.add(it.get("id"))
            out.append(_kpi_item(it.get("id"), it.get("name"), it.get("unit"), pts, "ir", None, False, g.get("category")))
            if len(out) >= limit:
                return out
    return out


def _kpi_item(kid, name, unit, pts, source, canonical, industry_kpi, category=None):
    latest = pts[-1] if pts else None
    year_ago = None
    if latest:
        le = _date(latest["end"])
        for p in pts[:-1]:
            pe = _date(p["end"])
            if le and pe and 330 <= (le - pe).days <= 400:
                year_ago = p
    yoy = None
    if latest and year_ago and (unit or "").lower() not in ("pct", "%", "percent"):
        yoy = _yoy(latest["v"], year_ago["v"])
    return {"id": kid, "name": name, "unit": unit, "source": source, "canonical": canonical, "industry_kpi": industry_kpi,
            "category": category, "points": pts[-8:], "latest": latest, "year_ago": year_ago, "yoy": yoy}


def guidance_block(g, today=None):
    """Guided ranges against what was reported, and what is still open, from guidance/{T}.json."""
    if not g or not g.get("series"):
        return None
    today = today or dt.date.today()
    ledger, open_items = [], []
    for s in g["series"]:
        for p in s.get("periods") or []:
            pts = p.get("points") or []
            if not pts:
                continue
            first, last = pts[0], pts[-1]
            row = {"metric": s.get("metric"), "label": s.get("label"), "unit": s.get("unit"), "basis": s.get("basis"), "segment": s.get("segment"),
                   "period": p.get("label"), "end": p.get("end"),
                   "initial": {"lo": first.get("lo"), "hi": first.get("hi"), "mid": first.get("mid"), "issued": first.get("d")},
                   "final": {"lo": last.get("lo"), "hi": last.get("hi"), "mid": last.get("mid"), "issued": last.get("d"), "quote": last.get("q")},
                   "actions": [x.get("a") for x in pts], "doc": last.get("doc")}
            if p.get("open"):
                open_items.append(row)
                continue
            out = p.get("outcome") or {}
            act = p.get("actual") or {}
            if act.get("v") is None:
                continue
            row.update({"actual": act.get("v"), "actual_quote": act.get("quote"), "result": out.get("result"),
                        "surprise_rel": out.get("surprise_rel"), "revisions": out.get("revisions")})
            ledger.append(row)
    ledger.sort(key=lambda r: r.get("end") or "")
    open_items.sort(key=lambda r: r.get("end") or "")
    docs = g.get("docs") or {}
    for r in ledger + open_items:
        d = docs.get(r.get("doc")) if isinstance(docs, dict) else None
        r["url"] = (d or {}).get("url")
    return {"credibility": g.get("credibility"), "open": open_items[-12:], "ledger": ledger[-24:], "withdrawn": g.get("withdrawn")}


def reaction_block(quant):
    ev = ((quant or {}).get("events") or {}).get("earnings") or {}
    cols = ((quant or {}).get("events") or {}).get("cols") or []
    rows = ev.get("rows") or []
    if not rows or not cols:
        return None
    ix = {c: i for i, c in enumerate(cols)}
    keep = ("filed", "accession", "timing", "day0", "cc", "abn", "gap", "d5", "a5", "d20", "a20", "sigma")
    last = [{k: r[ix[k]] for k in keep if k in ix} for r in rows[-8:]]
    st = ev.get("stats") or {}
    return {"rows": last, "n": st.get("n"), "median_abs_cc": st.get("median_abs_cc"), "mean_abs_abn": st.get("mean_abs_abn"),
            "up_share": st.get("up_share"), "basis": ev.get("basis"),
            "median_abs_last8": _median([abs(r["cc"]) for r in last if r.get("cc") is not None]),
            "source": "quant/t (SEC EDGAR acceptance times, daily closes)"}


def filing_change_block(red, idx_entry=None):
    comps = (red or {}).get("comparisons") or []
    if not comps:
        return None
    c = max(comps, key=lambda x: ((x.get("new") or {}).get("filed") or ""))
    secs = sorted(c.get("sections") or [], key=lambda s: -sum((s.get("counts") or {}).get(k, 0) for k in ("added", "removed", "modified")))
    excerpts = []
    for s in secs[:4]:
        for ch in s.get("changes") or []:
            kind = ch.get("kind")
            if kind not in ("added", "removed", "modified"):
                continue
            text = _norm(ch.get("new") if kind != "removed" else ch.get("old"))
            if len(text) < 60:
                continue
            excerpts.append({"section": s.get("title"), "kind": kind, "text": text[:320]})
            break
    sig = None
    for x in (idx_entry or {}).get("comparisons") or []:
        if x.get("filed") == (c.get("new") or {}).get("filed"):
            sig = x.get("signals")
    return {"form": c.get("form"), "filed": (c.get("new") or {}).get("filed"), "old_filed": (c.get("old") or {}).get("filed"),
            "url": (c.get("new") or {}).get("url"), "totals": c.get("totals"),
            "top_sections": [{"title": s.get("title"), "counts": s.get("counts")} for s in secs[:5]], "signals": sig, "excerpts": excerpts[:4]}


def insider_block(ins, since):
    rows = [r for r in (ins or {}).get("rows") or [] if r.get("tx_date") and (since is None or r["tx_date"] >= since)
            and r.get("code") in ("P", "S") and not r.get("derivative")]
    if not rows:
        return {"since": since, "buys": 0, "sells": 0, "buy_value": 0, "sell_value": 0, "plan_sells": 0, "rows": []}
    val = lambda r: (r.get("shares") or 0) * (r.get("price") or 0)
    buys = [r for r in rows if r["code"] == "P"]
    sells = [r for r in rows if r["code"] == "S"]
    top = sorted(rows, key=lambda r: -val(r))[:6]
    return {"since": since, "buys": len(buys), "sells": len(sells), "buy_value": round(sum(map(val, buys))), "sell_value": round(sum(map(val, sells))),
            "plan_sells": sum(1 for r in sells if r.get("plan_10b5")), "discretionary_sell_value": round(sum(val(r) for r in sells if not r.get("plan_10b5"))),
            "rows": [{k: r.get(k) for k in ("tx_date", "owner", "role", "code", "shares", "price", "plan_10b5", "accession")} | {"value": round(val(r))} for r in top]}


def last_call_block(calls):
    cs = [c for c in (calls or {}).get("calls") or [] if c.get("event_type") == "earnings"]
    if not cs:
        return None
    cs.sort(key=lambda c: c.get("date") or "", reverse=True)
    c = cs[0]
    weak = [e for e in c.get("exchanges") or [] if e.get("answer") and e.get("answer") != "direct"]
    return {"date": c.get("date"), "label": c.get("label"), "kind": c.get("kind"), "call_id": c.get("call_id"),
            "unanswered": [{"concern": e.get("concern"), "answer": e.get("answer"), "reason": e.get("reason"), "topics": e.get("topics")} for e in weak[:5]],
            "guidance": [{k: g.get(k) for k in ("metric", "period", "low", "high", "unit", "basis")} | {"quote": (g.get("quote") or {}).get("q")} for g in (c.get("guidance") or [])[:8]]}


# ------------------------------------------------------------------ facts and "listen for"
def preview_facts(p):
    """Numbered facts for the question writer; ids are stable so the app can link each question to its evidence."""
    f = []
    c = p.get("consensus") or {}
    per = f"the quarter ending {(c.get('period') or {}).get('end')}" if (c.get("period") or {}).get("end") else "the current quarter"
    if c.get("eps"):
        e = c["eps"]
        f.append(("cons:eps", f"Consensus EPS for {per} is ${e['avg']:.2f} from {int(e['n'] or 0)} estimates (range ${(e.get('low') or 0):.2f} to ${(e.get('high') or 0):.2f}); a year ago it was ${(e.get('year_ago') or 0):.2f}."))
    if c.get("revenue"):
        r = c["revenue"]
        f.append(("cons:rev", f"Consensus revenue for {per} is {_money(r['avg'])} from {int(r['n'] or 0)} estimates; a year ago revenue was {_money(r.get('year_ago'))} ({_pct(r.get('yoy'))} implied growth)."))
    hist = c.get("history") or []
    if len(hist) >= 2 and hist[0].get("eps") and hist[-1].get("eps") and abs(hist[-1]["eps"] - hist[0]["eps"]) >= 0.005:
        f.append(("cons:rev_hist", f"Consensus EPS for {per} moved from ${hist[0]['eps']:.2f} on {hist[0]['as_of']} to ${hist[-1]['eps']:.2f} on {hist[-1]['as_of']}."))
    g = p.get("guidance") or {}
    for i, o in enumerate((g.get("open") or [])[:4]):
        fin = o.get("final") or {}
        if fin.get("lo") is None and fin.get("mid") is None:
            continue
        rng = f"{_fmt(fin.get('lo'), o.get('unit'))} to {_fmt(fin.get('hi'), o.get('unit'))}" if fin.get("lo") is not None and fin.get("hi") is not None else _fmt(fin.get("mid"), o.get("unit"))
        f.append((f"guide:{i}", f"Management guided {o.get('label')} for {o.get('period')} at {rng} on {fin.get('issued')} (actions so far: {', '.join(a for a in o.get('actions') or [] if a)})."))
    cred = g.get("credibility") or {}
    if cred.get("n_scored"):
        f.append(("guide:cred", f"Across {cred['n_scored']} scored guided periods, results met or beat guidance {round(100 * (cred.get('hit_rate') or 0))}% of the time and came in above the range {round(100 * (cred.get('above_rate') or 0))}% of the time; guidance was raised in {round(100 * (cred.get('raise_rate') or 0))}% of periods."))
    for k in (p.get("kpis") or [])[:5]:
        lt = k.get("latest") or {}
        if lt.get("v") is None:
            continue
        extra = f", {_pct(k['yoy'])} year over year" if k.get("yoy") is not None else ""
        f.append((f"kpi:{k['id']}", f"{k['name']} was {_fmt(lt['v'], k.get('unit'))} in {lt.get('label')}{extra}."))
    r = p.get("reactions") or {}
    if r.get("rows"):
        last = r["rows"][-1]
        f.append(("react", f"After the last {len(r['rows'])} reports the median absolute one-day move was {_pct(r.get('median_abs_last8'))[1:]}; after the latest report ({last.get('filed')}) shares moved {_pct(last.get('cc'))} ({_pct(last.get('abn'))} versus the S&P 500)."))
    fc = p.get("filing_changes") or {}
    if fc.get("filed"):
        tops = ", ".join(s["title"] for s in fc.get("top_sections") or [] if s.get("title"))[:160]
        t = fc.get("totals") or {}
        f.append(("red", f"The {fc.get('form')} filed {fc['filed']} changed {t.get('added', 0)} passages added, {t.get('removed', 0)} removed and {t.get('modified', 0)} modified against the prior filing; most changes were in {tops}."))
    ins = p.get("insiders") or {}
    if ins.get("buys") or ins.get("sells"):
        parts = []
        if ins.get("buys"):
            parts.append(f"{ins['buys']} open-market purchases ({_money(ins.get('buy_value'))})")
        if ins.get("sells"):
            plan = f", {ins.get('plan_sells', 0)} of them under 10b5-1 plans" if ins.get("plan_sells") else ", none under 10b5-1 plans"
            parts.append(f"{ins['sells']} sales ({_money(ins.get('sell_value'))}{plan})")
        f.append(("ins", f"Since the last report ({ins.get('since')}), insiders made " + " and ".join(parts) + "."))
    lc = p.get("last_call") or {}
    for i, u in enumerate((lc.get("unanswered") or [])[:3]):
        if u.get("concern"):
            f.append((f"call:{i}", f"On the {lc.get('label')} call ({lc.get('date')}), an analyst question got a {u.get('answer')} answer: {u['concern']}"))
    st = p.get("street") or {}
    if st.get("d90"):
        d = st["d90"]
        f.append(("street", f"In the last 90 days analysts made {d.get('upgrades', 0)} upgrades, {d.get('downgrades', 0)} downgrades, {d.get('target_raises', 0)} target raises and {d.get('target_cuts', 0)} target cuts."))
    return f


LISTEN_PROMPT = """You prepare an analyst for {name}'s ({tk}) earnings report on {date}. Using ONLY the facts below, write the 5
questions most worth listening for: each should test something specific the report or call will answer (a guided range
against consensus, a KPI trend, a filing change, an unanswered question from the last call, insider activity). Each
question must cite the fact ids it relies on, use only numbers that appear in those facts, and be under 40 words. No
predictions, no advice, no adjectives the facts do not support.

Facts:
{facts}

Return JSON: {{"questions": [{{"text": "...", "refs": ["fact id", ...]}}]}}"""


def rule_questions(p, facts):
    """Deterministic questions from the same facts, used when no model is available or the check rejects the draft."""
    by = dict(facts)
    out = []
    c = p.get("consensus") or {}
    g = p.get("guidance") or {}
    per = (c.get("period") or {}).get("label")
    for i, o in enumerate(g.get("open") or []):
        if f"guide:{i}" not in by:
            continue
        refs = [f"guide:{i}"] + (["cons:rev"] if o.get("metric") == "revenue" and "cons:rev" in by else ["cons:eps"] if o.get("metric") in ("eps", "adjusted_eps") and "cons:eps" in by else [])
        out.append({"text": f"Does {o.get('label')} for {o.get('period')} land inside the range management guided, and how does it compare with consensus?", "refs": refs})
        if len(out) >= 2:
            break
    if not out and "cons:rev" in by:
        out.append({"text": "Does revenue meet the consensus estimate, and what explains the change from a year ago?", "refs": ["cons:rev"]})
    for k in (p.get("kpis") or [])[:2]:
        if f"kpi:{k['id']}" in by:
            out.append({"text": f"Does {k['name']} continue the trend of the last reported quarter?", "refs": [f"kpi:{k['id']}"]})
    if "red" in by:
        out.append({"text": "Do management's remarks explain the sections that changed most in the latest periodic filing?", "refs": ["red"]})
    for key in ("call:0", "call:1"):
        if key in by:
            out.append({"text": "Does management give a fuller answer this time to the question it did not answer directly on the last call?", "refs": [key]})
            break
    if "ins" in by and len(out) < 5:
        out.append({"text": "Does anything in the report bear on the insider buying or selling since the last report?", "refs": ["ins"]})
    return out[:5]


def listen_for(p, facts, complete_fn=None, post=None, allow_model=True):
    from . import gov_insights as G
    meta = {"method": "rules", "draft_model": None, "check_model": None, "dropped": []}
    if allow_model and (complete_fn or os.environ.get("FIREWORKS_API_KEY")) and len(facts) >= 4:
        try:
            j, model = (complete_fn or G.complete)(LISTEN_PROMPT.format(name=p.get("name") or p["ticker"], tk=p["ticker"], date=(p.get("report") or {}).get("date"),
                                                                       facts="\n".join(f"[{i}] {t}" for i, t in facts)))
            ok, dropped = G.verify(j.get("questions"), facts)
            meta["draft_model"] = model
            meta["dropped"] += dropped
            if ok:
                kept, more, check_model = G.check(ok, facts, model, post=post)
                meta["check_model"] = check_model
                meta["dropped"] += more
                if len(kept) >= 3:
                    meta["method"] = "model"
                    return kept[:5], meta
        except Exception as e:
            meta["error"] = str(e)[:200]
    return rule_questions(p, facts), meta


def build_preview(sb, tk, name, sector, idx_row, today=None, taxonomy=None, red_index=None, complete_fn=None, post=None, allow_model=True):
    today = today or dt.date.today()
    est = _get(sb, f"estimates/{tk}.json")
    e = (idx_row or {}).get("earnings") or {}
    last = e.get("last") or {}
    since = ((last.get("report") or {}).get("filed")) or None
    kstd = _get(sb, f"kpistd/t/{tk}.json")
    p = {"ticker": tk, "name": name, "sector": sector, "industry": (kstd or {}).get("industry"), "generated": now_iso(), "version": 1,
         "report": {"date": e.get("date") or (idx_row or {}).get("next_earnings"), "is_estimate": e.get("is_estimate"), "call_date": e.get("call_date"),
                    "days_until": trading_days_until(today, _date(e.get("date") or (idx_row or {}).get("next_earnings"))),
                    "source": {"provider": e.get("provider"), "url": e.get("url")}},
         "last_report": {"quarter_end": last.get("quarter_end"), "eps_actual": last.get("eps_actual"), "eps_estimate": last.get("eps_estimate"),
                         "surprise_pct": last.get("surprise_pct"), "filed": since, "url": (last.get("report") or {}).get("url"), "move": last.get("move")},
         "consensus": consensus_block(est), "street": street_block(est),
         "kpis": kpi_block(kstd, _get(sb, f"kpis/{tk}.json"), taxonomy),
         "guidance": guidance_block(_get(sb, f"guidance/{tk}.json"), today),
         "reactions": reaction_block(_get(sb, f"quant/t/{tk}.json")),
         "filing_changes": filing_change_block(_get(sb, f"redlines/{tk}.json"), (red_index or {}).get(tk)),
         "insiders": insider_block(_get(sb, f"insiders/{tk}.json"), since),
         "last_call": last_call_block(_get(sb, f"calls/{tk}.json"))}
    facts = preview_facts(p)
    qs, meta = listen_for(p, facts, complete_fn=complete_fn, post=post, allow_model=allow_model)
    p["facts"] = [{"id": i, "text": t} for i, t in facts]
    p["listen_for"] = qs
    p["listen_for_meta"] = meta
    return p


# ------------------------------------------------------------------ live: EDGAR current feed -> scorecards
def _sec(url, raw=False, timeout=30):
    sec_throttle()
    return request(url, headers={"User-Agent": SEC_UA, "Accept-Encoding": "gzip"}, timeout=timeout, retries=2, raw=raw)


def parse_feed(atom):
    """Entries of EDGAR's current 8-K feed: accession, CIK, items, filed date, acceptance time and index URL."""
    out = []
    for ent in re.findall(r"<entry>(.*?)</entry>", atom or "", re.S):
        acc = re.search(r"accession-number=([\d-]+)", ent)
        link = re.search(r'href="([^"]+)"', ent)
        cik = re.search(r"\((\d{10})\) \((?:Filer|Subject)", ent)
        upd = re.search(r"<updated>([^<]+)</updated>", ent)
        summ = html.unescape(re.search(r"<summary[^>]*>(.*?)</summary>", ent, re.S).group(1)) if re.search(r"<summary", ent) else ""
        items = re.findall(r"Item (\d+\.\d+)", summ)
        filed = re.search(r"Filed:</b>\s*(\d{4}-\d{2}-\d{2})", summ)
        form = re.search(r'term="([^"]+)"', ent)
        if not acc or not cik:
            continue
        out.append({"accession": acc.group(1), "cik": int(cik.group(1)), "items": sorted(set(items)), "filed": filed.group(1) if filed else None,
                    "accepted": upd.group(1) if upd else None, "index_url": link.group(1) if link else None, "form": form.group(1) if form else "8-K"})
    return out


def poll_feed(seen, max_pages=3, fetch=None):
    """New Item 2.02 8-Ks on the current feed since the accessions in `seen` (newest first)."""
    fetch = fetch or (lambda u: _sec(u))
    new = []
    for page in range(max_pages):
        ents = parse_feed(fetch(FEED.format(start=page * 100)))
        if not ents:
            break
        stop = False
        for e in ents:
            if e["accession"] in seen:
                stop = True
                continue
            new.append(e)
        if stop or len(ents) < 100:
            break
    # the same filing appears once per filer/subject; keep one
    uniq = {}
    for e in new:
        uniq.setdefault(e["accession"], e)
    return list(uniq.values())


REV_RX = re.compile(r"(?P<lead>(?:total |net |quarterly |consolidated |record |third[- ]quarter |fourth[- ]quarter |first[- ]quarter |second[- ]quarter )*"
                    r"(?:revenues?|net sales|sales|total net revenue))\s+"
                    r"(?:for the (?:third|fourth|first|second) quarter(?: of (?:fiscal )?\d{4})? )?"
                    r"(?:of|was|were|totaled|totaling|reached|grew to|increased to|rose to|came in at)?\s*(?:a record )?(?:approximately )?"
                    r"\$\s?(?P<num>[\d,]+(?:\.\d+)?)\s?(?P<scale>million|billion|thousand)\b", re.I)
EPS_RX = re.compile(r"(?P<pre>(?:adjusted |non-gaap |gaap |core |reported |consolidated )*)"
                    r"(?P<kind>diluted (?:earnings|net income|net loss|loss|net earnings|eps)(?: per (?:diluted |common )?share)?|"
                    r"(?:earnings|net income|net loss|loss|net earnings)(?: \(loss\))? per (?:common |basic and diluted |diluted )?share(?: \(diluted\))?|"
                    r"(?:earnings|net income|net loss|loss|net earnings) per diluted share|eps)"
                    r"(?:\s*\(\W*eps\W*\))?\s+"
                    r"(?:attributable to [\w ,.&]+? )?(?:from continuing operations )?(?:for the (?:third|fourth|first|second) quarter(?: of (?:fiscal )?\d{4})? )?"
                    r"(?:was|were|of|totaled|came in at|remained at)?\s*(?:a )?(?:\(?\$\s?)(?P<num>\(?-?\d+\.\d+\)?)(?!\s*(?:million|billion|thousand|%))", re.I)
YOY_RX = re.compile(r"(?:up|an increase of|increased|increase of|grew|rose|growth of|growing|down|a decrease of|decreased|declined|fell)\s+(?:by\s+)?"
                    r"(?P<p>\d+(?:\.\d+)?)\s?(?:percent|%)", re.I)
# Sentences about other periods, guidance or changes are not the quarter's reported figure.
OTHER_PERIOD = re.compile(r"\b(nine months|six months|twelve months|full[- ]year|fiscal year 20\d\d|year ended|year-to-date|ytd)\b", re.I)
FORWARD = re.compile(r"\b(expects?|expected|outlook|guidance|forecast|anticipat\w+|project(?:s|ed|ing)|target(?:s|ing)?|range of|to be between)\b", re.I)
BEFORE_CHANGE = re.compile(r"(increase|decrease|change|growth|decline|reduction|improvement)s? in\s*(?:\w+\s+){0,3}$", re.I)
BEFORE_PRIOR = re.compile(r"(compared (?:to|with)|versus|vs\.?|from|prior[- ]year|last year|same period|a year ago)\W*(?:\w+\W+){0,4}$", re.I)


def sentences(text):
    """Release text as sentences: the extractor keeps one line per paragraph or table cell; split lines on sentence ends
    and bullets so a figure is always quoted with the sentence it belongs to."""
    out, paras = [], []
    for line in str(text or "").split("\n"):
        line = _norm(line)
        if not line:
            continue
        if line in ("◦", "•", "▪", "●", "-", "~"):
            paras.append("")
            continue
        # HTML releases often split a label and its value across elements ("Net Revenues" / "of $944 million ...").
        if (paras and paras[-1] and not re.search(r"[.!?:;\"”)]$", paras[-1])
                and (re.match(r"[a-z$(\d%]|of\b|was\b|were\b|compared\b|vs\b", line) or (len(paras[-1]) < 40 and len(line) < 40))):
            paras[-1] = paras[-1] + " " + line
        else:
            paras.append(line)
    for para in paras:
        for part in re.split(r"(?<=[.;])\s+(?=[A-Z(\"'$•])|\s*[•▪●◦]\s*|\s+~\s+", para):
            part = part.strip(" •~◦")
            if part:
                out.append(part)
    return out


def rule_headlines(text, head_sentences=140):
    """Revenue and EPS from the narrative of an earnings release (first sentences only); every value keeps the
    sentence it came from. Sentences about other periods, guidance, changes or the prior year are skipped."""
    sents = sentences(text)[:head_sentences]
    out, rev_cands = {}, []
    for sent in sents:
        if OTHER_PERIOD.search(sent) or FORWARD.search(sent):
            continue
        if re.search(r"\bfiscal (?:year )?(?:20)?\d\d\b", sent, re.I) and not re.search(r"quarter|three months|Q[1-4]\b", sent, re.I):
            continue
        for m in REV_RX.finditer(sent):
            before = sent[:m.start()]
            if BEFORE_CHANGE.search(before) or BEFORE_PRIOR.search(before[-60:]):
                continue
            lead = m.group("lead").lower().strip()
            v = _parse_num(m.group("num")) * SCALE[m.group("scale").lower()]
            # A capitalised word right before the label names a segment ("Retail Net Revenues", "Services revenue").
            prev = (re.findall(r"([A-Za-z&-]+)\W*$", before) or [""])[0]
            qualified = bool(prev) and prev[:1].isupper() and len(before.strip()) > len(prev) + 1 and prev.lower() not in (
                "the", "total", "record", "quarterly", "quarter", "reported", "highlights", "results", "consolidated", "gaap", "net", "company",
                "first", "second", "third", "fourth", "q1", "q2", "q3", "q4", "fiscal", "group", "corporate")
            if prev[:1].isupper() and len(before.strip()) <= len(prev) + 1 and prev.lower() not in ("total", "net", "quarterly", "consolidated", "record", "gaap", "the"):
                qualified = True
            rec = {"value": v, "printed": f"${m.group('num')} {m.group('scale')}", "quote": sent[:400], "method": "rules",
                   "total": not qualified and (bool(re.search(r"total|consolidated|quarterly|net revenue", lead)) or lead in ("revenue", "revenues", "net sales", "sales"))}
            yoy = YOY_RX.search(sent[m.end():m.end() + 120])
            if yoy:
                sign = -1 if re.match(r"(?:down|a decrease|decreased|declined|fell)", yoy.group(0), re.I) else 1
                rec["yoy_printed"] = sign * float(yoy.group("p")) / 100
            prior = re.search(r"compared (?:to|with)(?: revenue of)? \$\s?([\d,]+(?:\.\d+)?)\s?(million|billion|thousand)", sent[m.end():], re.I)
            if prior:
                rec["prior_year"] = _parse_num(prior.group(1)) * SCALE[prior.group(2).lower()]
            rev_cands.append(rec)
        for m in EPS_RX.finditer(sent):
            before = sent[:m.start()]
            if BEFORE_PRIOR.search(before[-60:]):
                continue
            pre = (m.group("pre") or "").lower()
            ctx = before[-14:].lower()
            kind = m.group("kind").lower()
            adjusted = any(w in pre or w in ctx for w in ("adjusted", "non-gaap", "core"))
            key = "eps_adjusted" if adjusted else "eps_gaap"
            if key in out:
                continue
            num = m.group("num")
            v = _parse_num(num)
            if v is None:
                continue
            if "loss" in kind and v > 0:
                v = -v
            rec = {"value": v, "printed": f"${num.strip('()')}" if not num.startswith("(") else f"(${num.strip('()')})", "quote": sent[:400], "method": "rules"}
            tail = re.split(r"\b(?:while|whereas|and (?:adjusted|non-gaap|gaap))\b", sent[m.end():m.end() + 80], maxsplit=1, flags=re.I)[0]
            yoy = YOY_RX.search(tail)
            if yoy:
                sign = -1 if re.match(r"(?:down|a decrease|decreased|declined|fell)", yoy.group(0), re.I) else 1
                rec["yoy_printed"] = sign * float(yoy.group("p")) / 100
            out[key] = rec
    if rev_cands:
        # Releases lead with the quarter; later sentences repeat the figures for year-to-date periods under their own
        # headings, so the first total-company figure wins and segment figures are only a fallback.
        totals = [r for r in rev_cands if r["total"]]
        best = totals[0] if totals else max(rev_cands, key=lambda r: r["value"])
        out["revenue"] = {k: v for k, v in best.items() if k != "total"}
    # Tables, when the narrative has no per-share sentence: the first two figures after the row label are the current
    # quarter and the same quarter last year in nearly every release. These are marked unconfirmed until the model
    # check agrees, because some releases print the prior year or a year-to-date column first.
    t = _norm(text)
    num = r"\$?\s*(\(?-?\d{1,3}\.\d{2}\)?)"
    if "eps_adjusted" not in out:
        m = re.search(r"(?:Adjusted|Non-GAAP|Core)\s+(?:diluted\s+)?(?:earnings|net income|EPS|net earnings)(?:\s*\(loss\))?(?:\s+per\s+(?:diluted\s+)?share)?"
                      r"(?:\s*\(?(?:diluted)?\)?)?\*?(?:\s*[-:])?\s*" + num + r"\s*" + num, t, re.I)
        if m:
            out["eps_adjusted"] = {"value": _parse_num(m.group(1)), "printed": m.group(1), "prior_year": _parse_num(m.group(2)),
                                   "quote": m.group(0)[:300], "method": "rules-table", "unconfirmed": True}
    if "eps_gaap" not in out:
        m = re.search(r"(?:(?:earnings|income|loss|net income|net earnings)(?:\s*\(loss\))?\s+per\s+(?:common\s+)?share[^$]{0,80}?)?"
                      r"(?:Basic and diluted|Diluted)(?:\s+(?:earnings|net income|EPS|loss)[^$]{0,60}?)?\s*" + num + r"\s*" + num, t, re.I)
        if m and "per share" in t[max(0, m.start() - 160):m.end()].lower():
            out["eps_gaap"] = {"value": _parse_num(m.group(1)), "printed": m.group(1), "prior_year": _parse_num(m.group(2)),
                               "quote": m.group(0)[:300], "method": "rules-table", "unconfirmed": True}
    return out


def quarter_end_in(text, filed=None):
    """The latest 'quarter/three months ended <date>' in the release head that is not after the filing date."""
    best = None
    fd = _date(filed)
    for m in re.finditer(r"(?:quarter|three months|period|thirteen weeks|twelve weeks|sixteen weeks)\s+ended,?\s+([A-Z][a-z]+\.? \d{1,2},? \d{4})", _norm(text)[:12000]):
        raw = m.group(1).replace(",", "").replace(".", "")
        d = None
        for fmt in ("%B %d %Y", "%b %d %Y"):
            try:
                d = dt.datetime.strptime(raw, fmt).date()
                break
            except ValueError:
                d = None
        if d and (not fd or d <= fd) and (best is None or d > best):
            best = d
    return best.isoformat() if best else None


EXTRACT_PROMPT = """From this earnings press release, extract the reported figures for the quarter just ended. Return ONLY items
that are printed in the text. For each item give the number exactly as printed (with its sign or parentheses), the scale
word that applies (thousand, million, billion, or "" for per-share and percentages), and the exact sentence or table row
text it came from, copied verbatim.

Return JSON:
{{"quarter_end": "YYYY-MM-DD or null",
  "revenue": {{"printed": "...", "scale": "...", "quote": "...", "prior_year_printed": "... or null"}} or null,
  "eps_gaap": {{"printed": "...", "quote": "..."}} or null,
  "eps_adjusted": {{"printed": "...", "quote": "..."}} or null,
  "kpis": [{{"name": "...", "printed": "...", "scale": "...", "unit": "USD|%|count|ratio|other", "quote": "...", "prior_year_printed": "... or null"}}],
  "guidance": [{{"metric": "...", "period": "...", "low_printed": "... or null", "high_printed": "... or null", "point_printed": "... or null", "scale": "...", "unit": "USD|%|per_share|other", "quote": "..."}}]}}
At most 10 KPIs (operating metrics a company in this industry is judged on, not reconciliation lines) and every
guidance item management gives.

RELEASE:
{text}"""

CHECK_EXTRACT = """You audit figures extracted from an earnings press release. For each item, decide whether the quoted text
states that figure for the stated metric and period (for guidance: that range or point for that period). Units, scale and
sign must match. Return JSON: {{"verdicts": [{{"n": <item number>, "supported": true or false, "reason": "at most 15 words"}}]}}

{items}"""


def _verify_item(t_norm, printed, scale, quote):
    """Code check of one extracted figure: verbatim quote, the printed number inside the quote, and value = printed x scale."""
    if not printed or not quote:
        return None, "missing printed value or quote"
    q = _norm(quote)
    if len(q) < 8 or q not in t_norm:
        return None, "quote not verbatim in release"
    digits = re.sub(r"[^\d.]", "", str(printed))
    if not digits or digits not in re.sub(r"[^\d.]", "", q):
        return None, "printed number not in quote"
    base = _parse_num(printed)
    if base is None:
        return None, "unparseable number"
    mult = SCALE.get((scale or "").lower().strip(), None)
    if mult is None:
        return None, f"unknown scale {scale}"
    return base * mult, None


def model_extract(text, complete_fn=None, check_fn=None):
    """Model structure + code verification + second-model check. Returns (items, meta)."""
    from . import gov_insights as G
    from .llm import chat_json
    t = _norm(text)
    meta = {"draft_model": None, "check_model": None, "dropped": []}
    if complete_fn is None and not os.environ.get("FIREWORKS_API_KEY"):
        meta["skipped"] = "no model key"
        return {}, meta
    prompt = EXTRACT_PROMPT.format(text=t[:24000])
    if complete_fn:
        j, model = complete_fn(prompt)
    else:
        j, model, _ = chat_json([G.PRIMARY, G.FALLBACK], [{"role": "user", "content": prompt}], max_tokens=6000, temperature=0.1, timeout=180)
    meta["draft_model"] = model
    items = []
    out = {"quarter_end": j.get("quarter_end") if isinstance(j.get("quarter_end"), str) else None, "kpis": [], "guidance": []}
    for key in ("revenue", "eps_gaap", "eps_adjusted"):
        x = j.get(key)
        if not isinstance(x, dict):
            continue
        v, err = _verify_item(t, x.get("printed"), x.get("scale") if key == "revenue" else "", x.get("quote"))
        if err:
            meta["dropped"].append({"item": key, "reason": err})
            continue
        rec = {"value": v, "printed": x.get("printed"), "quote": _norm(x.get("quote")), "method": "model"}
        if key == "revenue" and x.get("prior_year_printed"):
            pv, perr = _verify_item(t, x["prior_year_printed"], x.get("scale"), x.get("quote"))
            if not perr:
                rec["prior_year"] = pv
        items.append((key, rec, f"{key}: {x.get('printed')} {x.get('scale') or ''}"))
    for k in (j.get("kpis") or [])[:10]:
        if not isinstance(k, dict):
            continue
        v, err = _verify_item(t, k.get("printed"), k.get("scale"), k.get("quote"))
        if err:
            meta["dropped"].append({"item": k.get("name"), "reason": err})
            continue
        rec = {"name": k.get("name"), "value": v, "printed": k.get("printed"), "unit": k.get("unit"), "quote": _norm(k.get("quote")), "method": "model"}
        if k.get("prior_year_printed"):
            pv, perr = _verify_item(t, k["prior_year_printed"], k.get("scale"), k.get("quote"))
            if not perr:
                rec["prior_year"] = pv
                rec["yoy"] = _yoy(v, pv) if (k.get("unit") or "") != "%" else None
        items.append(("kpi", rec, f"KPI {k.get('name')}: {k.get('printed')} {k.get('scale') or ''}"))
    for g in (j.get("guidance") or [])[:12]:
        if not isinstance(g, dict):
            continue
        vals, bad = {}, None
        for side in ("low", "high", "point"):
            pr = g.get(f"{side}_printed")
            if pr in (None, "", "null"):
                continue
            v, err = _verify_item(t, pr, g.get("scale"), g.get("quote"))
            if err:
                bad = err
                break
            vals[side] = v
        if bad or not vals:
            meta["dropped"].append({"item": g.get("metric"), "reason": bad or "no guided value"})
            continue
        rec = {"metric": g.get("metric"), "period": g.get("period"), "low": vals.get("low"), "high": vals.get("high"), "point": vals.get("point"),
               "unit": g.get("unit"), "quote": _norm(g.get("quote")), "method": "model"}
        items.append(("guidance", rec, f"Guidance {g.get('metric')} {g.get('period')}: low {g.get('low_printed')} high {g.get('high_printed')} point {g.get('point_printed')} {g.get('scale') or ''}"))
    if not items:
        return out, meta
    listing = "\n\n".join(f"ITEM {n}: {desc}\nQUOTE: {rec['quote']}" for n, (_, rec, desc) in enumerate(items, 1))
    check_model = G.FALLBACK if model == G.PRIMARY else G.PRIMARY
    try:
        if check_fn:
            v = check_fn(check_model, CHECK_EXTRACT.format(items=listing))
        else:
            v, _, _ = chat_json([check_model], [{"role": "user", "content": CHECK_EXTRACT.format(items=listing)}], max_tokens=4000, timeout=180)
    except Exception as e:
        meta["check_error"] = str(e)[:200]
        meta["dropped"].append({"item": "all model items", "reason": "check could not run"})
        return {"quarter_end": out["quarter_end"], "kpis": [], "guidance": []}, meta
    meta["check_model"] = check_model
    verdicts = {x.get("n"): x for x in (v or {}).get("verdicts") or [] if isinstance(x, dict)}
    for n, (key, rec, desc) in enumerate(items, 1):
        vd = verdicts.get(n)
        if not vd or vd.get("supported") is not True:
            meta["dropped"].append({"item": desc, "reason": "check: " + ((vd or {}).get("reason") or "no verdict")[:120]})
            continue
        if key == "kpi":
            out["kpis"].append(rec)
        elif key == "guidance":
            out["guidance"].append(rec)
        else:
            out[key] = rec
    return out, meta


def consensus_for(est, filed, quarter_end=None):
    """Consensus for the quarter a release reports: the latest snapshot taken before the release whose current-quarter
    end is the most recent quarter end before the filing; otherwise the provider's recorded estimate for that quarter."""
    fd = _date(filed)
    if not est or not fd:
        return None
    best = None
    for s in est.get("snapshots") or []:
        if not s.get("as_of") or s["as_of"] > filed:
            continue
        p = (s.get("p") or {}).get("0q") or {}
        end = _date(p.get("end"))
        if end and end < fd and (fd - end).days <= 80 and (best is None or s["as_of"] > best[0]):
            best = (s["as_of"], p)
    if best:
        p = best[1]
        return {"basis": "snapshot", "as_of": best[0], "end": p.get("end"), "eps": p.get("eps"), "eps_n": p.get("eps_n"), "revenue": p.get("rev"), "revenue_n": p.get("rev_n")}
    for p in (est.get("consensus") or {}).get("periods") or []:
        end = _date(p.get("end"))
        if p.get("code") == "0q" and end and end < fd and (fd - end).days <= 80:
            return {"basis": "consensus", "as_of": (est.get("consensus") or {}).get("as_of"), "end": p.get("end"),
                    "eps": (p.get("eps") or {}).get("avg"), "eps_n": (p.get("eps") or {}).get("n"),
                    "revenue": (p.get("revenue") or {}).get("avg"), "revenue_n": (p.get("revenue") or {}).get("n"),
                    "eps_year_ago": (p.get("eps") or {}).get("year_ago"), "revenue_year_ago": (p.get("revenue") or {}).get("year_ago")}
    for s in est.get("surprises") or []:
        end = _date(s.get("quarter_end"))
        if end and end < fd and (fd - end).days <= 80:
            return {"basis": "provider surprise record", "end": s.get("quarter_end"), "eps": s.get("eps_estimate")}
    return None


def guidance_for(g, quarter_end, filed):
    """What management last guided, before this release, for the quarter being reported."""
    out = []
    qe, fd = _date(quarter_end), _date(filed)
    if not g or not qe:
        return out
    for s in g.get("series") or []:
        for p in s.get("periods") or []:
            pe = _date(p.get("end"))
            if p.get("type") != "Q" or not pe or abs((pe - qe).days) > 10:
                continue
            pts = [x for x in p.get("points") or [] if x.get("d") and (not fd or _date(x["d"]) < fd)]
            if not pts:
                continue
            last = pts[-1]
            out.append({"metric": s.get("metric"), "label": s.get("label"), "basis": s.get("basis"), "measure": s.get("measure"), "unit": s.get("unit"), "segment": s.get("segment"),
                        "lo": last.get("lo"), "hi": last.get("hi"), "mid": last.get("mid"), "issued": last.get("d"), "quote": last.get("q")})
    return out


def _vs_range(v, lo, hi, mid):
    if v is None:
        return None
    if lo is not None and hi is not None:
        return "above" if v > hi else "below" if v < lo else "within"
    if mid is not None:
        return "above" if v > mid * 1.001 else "below" if v < mid * 0.999 else "at"
    return None


def build_score(sb, tk, name, entry, fetch=None, complete_fn=None, check_fn=None, release_text=None):
    """Scorecard for one Item 2.02 8-K: reported figures (rules first, model second, all verified against the release),
    against consensus, the guidance for that quarter and last year."""
    fetch = fetch or (lambda u, raw=False: _sec(u, raw=raw))
    t0 = time.time()
    acc = entry["accession"]
    folder = f"https://www.sec.gov/Archives/edgar/data/{int(entry['cik'])}/{acc.replace('-', '')}/"
    ex_url = None
    if release_text is None:
        from .ir import _exhibit_names, extract_text
        page = fetch(folder + f"{acc}-index.htm")
        names = _exhibit_names(page, {"i": "2.02", "p": None})
        if not names:
            raise ValueError("no EX-99 exhibit in the filing")
        pick = next((n for n in names if re.search(r"99[-_.]?0?1\b|ex991|ex-991|991", n.lower())), names[0])
        ex_url = urllib.parse.urljoin(folder, pick)
        body = fetch(ex_url, raw=True)
        ext = ".pdf" if body[:5] == b"%PDF-" else ".htm"
        res = extract_text(body, ext)
        release_text = "\n".join(res.get("pages") or [])
    text = release_text
    rules = rule_headlines(text)
    model, mmeta = model_extract(text, complete_fn=complete_fn, check_fn=check_fn)
    heads = {}
    for k in ("revenue", "eps_gaap", "eps_adjusted"):
        r, m = rules.get(k), model.get(k)
        if r and m:
            if _close(r["value"], m["value"], 1e-3):
                heads[k] = {**r, "unconfirmed": False, "confirmed_by": "model"}
            else:
                # The model item passed the verbatim-quote check and the second model; the rule read is kept as a note.
                heads[k] = {**m, "conflict": {"rules": r["value"], "rules_quote": r["quote"]}}
        else:
            heads[k] = r or m
    qe = model.get("quarter_end") or quarter_end_in(text, entry.get("filed"))
    est = _get(sb, f"estimates/{tk}.json")
    cons = consensus_for(est, entry.get("filed") or "", qe)
    if not qe and cons:
        qe = cons.get("end")
    guided = guidance_for(_get(sb, f"guidance/{tk}.json"), qe, entry.get("filed"))
    metrics = []
    for key, label, cons_key in (("revenue", "Revenue", "revenue"), ("eps_gaap", "EPS (GAAP, diluted)", "eps"), ("eps_adjusted", "EPS (adjusted)", "eps")):
        h = heads.get(key)
        if not h:
            continue
        row = {"id": key, "label": label, "value": h["value"], "printed": h.get("printed"), "quote": h.get("quote"), "method": h.get("method"),
               "conflict": h.get("conflict"), "yoy_printed": h.get("yoy_printed"), "unconfirmed": bool(h.get("unconfirmed"))}
        if h.get("prior_year") is not None:
            row["prior_year"] = h["prior_year"]
            row["yoy"] = _yoy(h["value"], h["prior_year"])
        # Consensus: providers report adjusted EPS for most companies; compare GAAP EPS only when no adjusted figure exists.
        if cons and cons.get(cons_key) is not None and (key != "eps_gaap" or not heads.get("eps_adjusted")):
            row["consensus"] = cons[cons_key]
            row["consensus_n"] = cons.get(f"{cons_key}_n")
            row["surprise"] = (h["value"] - cons[cons_key]) / abs(cons[cons_key]) if cons[cons_key] else None
        bases = {"revenue": ("gaap", "unspecified", None), "eps_gaap": ("gaap", "unspecified", None), "eps_adjusted": ("adjusted", "unspecified", None)}[key]
        metric = "revenue" if key == "revenue" else "eps"
        cands = [x for x in guided if x.get("metric") == metric and not x.get("segment") and (x.get("measure") in ("level", None)) and x.get("basis") in bases]
        # an exact basis beats "unspecified"
        cands.sort(key=lambda x: 0 if x.get("basis") == bases[0] else 1)
        gg = cands[0] if cands else None
        if gg:
            row["guided"] = {**gg, "result": _vs_range(h["value"], gg.get("lo"), gg.get("hi"), gg.get("mid"))}
        metrics.append(row)
    new_guidance = []
    for g in model.get("guidance") or []:
        prev = None
        for old in (_get(sb, f"guidance/{tk}.json") or {}).get("series") or []:
            if (old.get("label") or "").lower() == (g.get("metric") or "").lower():
                for p in old.get("periods") or []:
                    if (p.get("label") or "").replace(" ", "").lower() == (g.get("period") or "").replace(" ", "").lower() and p.get("points"):
                        prev = p["points"][-1]
        change = None
        if prev and (g.get("low") is not None or g.get("point") is not None):
            mid = g.get("point") if g.get("point") is not None else ((g["low"] + g["high"]) / 2 if g.get("high") is not None else g["low"])
            pm = prev.get("mid")
            if pm:
                change = "raised" if mid > pm * 1.001 else "lowered" if mid < pm * 0.999 else "maintained"
        new_guidance.append({**g, "prior": {"lo": (prev or {}).get("lo"), "hi": (prev or {}).get("hi"), "mid": (prev or {}).get("mid"), "issued": (prev or {}).get("d")} if prev else None,
                             "change": change})
    accepted = entry.get("accepted")
    latency = None
    try:
        latency = round(time.time() - dt.datetime.fromisoformat(accepted).timestamp()) if accepted else None
    except ValueError:
        latency = None
    return {"ticker": tk, "name": name, "version": 1, "generated": now_iso(), "filed": entry.get("filed"), "accepted": accepted, "accession": acc,
            "items": entry.get("items"), "filing_url": folder + f"{acc}-index.htm", "exhibit_url": ex_url, "quarter_end": qe,
            "metrics": metrics, "kpis": model.get("kpis") or [], "guidance": new_guidance, "guided_for_quarter": guided,
            "consensus": cons, "method": {"rules": sorted(rules), "model": mmeta, "build_seconds": round(time.time() - t0, 1)},
            "latency_seconds": latency}


# ------------------------------------------------------------------ calls diff
def build_call_diff(calls):
    cs = sorted([c for c in (calls or {}).get("calls") or [] if c.get("event_type") == "earnings" and c.get("kind") == "transcript"],
                key=lambda c: c.get("date") or "", reverse=True)
    if not cs:
        return None
    cur = cs[0]
    prev = next((c for c in cs[1:] if c.get("call_id") == cur.get("prev_call")), cs[1] if len(cs) > 1 else None)
    th = lambda c: {t.get("theme"): t for t in (c or {}).get("themes") or [] if t.get("theme")}
    a, b = th(cur), th(prev)
    started = [{"theme": k, "label": v.get("label"), "summary": v.get("summary"), "quote": ((v.get("quotes") or [{}])[0]).get("q")} for k, v in a.items() if k not in b]
    stopped = [{"theme": k, "label": v.get("label"), "summary": v.get("summary"), "last_said": prev.get("date")} for k, v in b.items() if k not in a]
    tone = []
    for k in set(a) & set(b):
        sa, sb_ = a[k].get("sentiment"), b[k].get("sentiment")
        if isinstance(sa, (int, float)) and isinstance(sb_, (int, float)) and abs(sa - sb_) >= 0.3:
            tone.append({"theme": k, "label": a[k].get("label"), "now": sa, "before": sb_})
    key = lambda g: ((g.get("metric") or "").lower().strip(), (g.get("period") or "").replace(" ", "").lower())
    old = {key(g): g for g in (prev or {}).get("guidance") or []}
    glog = []
    for g in cur.get("guidance") or []:
        o = old.get(key(g))
        mid = lambda x: x.get("high") if x.get("low") is None else x.get("low") if x.get("high") is None else (x["low"] + x["high"]) / 2
        status = "new"
        if o:
            m1, m0 = mid(g), mid(o)
            status = "maintained" if m1 is None or m0 is None or abs(m1 - m0) <= abs(m0) * 0.001 else "raised" if m1 > m0 else "lowered"
        glog.append({"metric": g.get("metric"), "period": g.get("period"), "low": g.get("low"), "high": g.get("high"), "unit": g.get("unit"),
                     "status": status, "prior": {"low": o.get("low"), "high": o.get("high")} if o else None, "quote": (g.get("quote") or {}).get("q")})
    weak = [{"concern": e.get("concern"), "answer": e.get("answer"), "reason": e.get("reason"), "topics": e.get("topics"),
             "question_quote": (e.get("concern_quote") or {}).get("q"), "answer_quote": (e.get("answer_quote") or {}).get("q")}
            for e in cur.get("exchanges") or [] if e.get("answer") and e.get("answer") != "direct"]
    return {"call": {k: cur.get(k) for k in ("call_id", "date", "label", "title", "words", "has_qa", "summary")},
            "previous": {k: (prev or {}).get(k) for k in ("call_id", "date", "label")} if prev else None,
            "started": started, "stopped": stopped, "tone_shifts": tone, "changes": [{"theme": c.get("theme"), "text": c.get("text"), "quote": (c.get("quote") or {}).get("q")} for c in cur.get("changes") or []],
            "not_answered": weak, "guidance_log": glog, "generated": now_iso(), "version": 1}


# ------------------------------------------------------------------ season
def build_season(idx, names, today=None, spy=None, scores=None):
    """Upcoming reports, reporters of the last SEASON_DAYS days, sector beat rates and what moved shares."""
    today = today or dt.date.today()
    up, rep = [], []
    spy = spy or {}
    for r in idx:
        tk = r.get("ticker")
        e = r.get("earnings") or {}
        nm = names.get(tk) or {}
        d = _date(e.get("date") or r.get("next_earnings"))
        if d and today <= d <= today + dt.timedelta(days=21):
            up.append({"ticker": tk, "name": nm.get("name"), "sector": nm.get("sector"), "date": d.isoformat(), "is_estimate": e.get("is_estimate"),
                       "eps": e.get("eps"), "revenue": e.get("revenue"), "median_abs_move": (e.get("reaction") or {}).get("median_abs_move")})
        last = e.get("last") or {}
        filed = (last.get("report") or {}).get("filed")
        fd = _date(filed)
        if fd and today - dt.timedelta(days=SEASON_DAYS) <= fd <= today:
            mv = last.get("move")
            sm = spy.get(last.get("move_date")) if last.get("move_date") else None
            rep.append({"ticker": tk, "name": nm.get("name"), "sector": nm.get("sector"), "filed": filed, "quarter_end": last.get("quarter_end"),
                        "eps_actual": last.get("eps_actual"), "eps_estimate": last.get("eps_estimate"), "surprise_pct": last.get("surprise_pct"),
                        "beat": last.get("beat"), "move": mv, "move_date": last.get("move_date"), "spy_move": sm,
                        "excess": (mv - sm) if mv is not None and sm is not None else None, "report_url": (last.get("report") or {}).get("url"),
                        "score": bool((scores or {}).get(tk))})
    up.sort(key=lambda x: (x["date"], x["ticker"]))
    rep.sort(key=lambda x: (x["filed"], x["ticker"]), reverse=True)
    # Sector beat rates. Surprises beyond +-300% are provider unit errors or near-zero bases and are left out of medians.
    sane = lambda s: s is not None and abs(s) <= 3
    sectors = {}
    for x in rep:
        s = sectors.setdefault(x["sector"] or "Other", {"sector": x["sector"] or "Other", "n": 0, "beats": 0, "misses": 0, "inline": 0, "surprises": [], "moves": []})
        if x["beat"] is None:
            continue
        s["n"] += 1
        s["beats" if x["beat"] > 0 else "misses" if x["beat"] < 0 else "inline"] += 1
        if sane(x["surprise_pct"]):
            s["surprises"].append(x["surprise_pct"])
        if x["move"] is not None:
            s["moves"].append(x["move"])
    sec_rows = []
    for s in sectors.values():
        if not s["n"]:
            continue
        sec_rows.append({"sector": s["sector"], "n": s["n"], "beats": s["beats"], "misses": s["misses"], "inline": s["inline"], "beat_rate": s["beats"] / s["n"],
                         "median_surprise": _median(s["surprises"]), "median_move": _median(s["moves"])})
    sec_rows.sort(key=lambda r: -r["n"])
    pairs = [(x["surprise_pct"], x["excess"] if x["excess"] is not None else x["move"]) for x in rep if sane(x["surprise_pct"]) and (x["excess"] is not None or x["move"] is not None)]
    corr = None
    if len(pairs) >= 20:
        xs, ys = zip(*pairs)
        try:
            corr = statistics.correlation(xs, ys)
        except statistics.StatisticsError:
            corr = None
    beat_moves = [x["excess"] if x["excess"] is not None else x["move"] for x in rep if x["beat"] == 1 and (x["move"] is not None)]
    miss_moves = [x["excess"] if x["excess"] is not None else x["move"] for x in rep if x["beat"] == -1 and (x["move"] is not None)]
    beat_down = sum(1 for m in beat_moves if m is not None and m < 0)
    drivers = {"n_pairs": len(pairs), "corr_surprise_move": corr,
               "beats": {"n": len(beat_moves), "median_move": _median(beat_moves), "share_down": beat_down / len(beat_moves) if beat_moves else None},
               "misses": {"n": len(miss_moves), "median_move": _median(miss_moves)},
               "basis": "EPS surprise from the consensus provider; move = one-day close-to-close after the report, minus the S&P 500 (SPY) move that day where available"}
    kpi_rows = []
    for tk, sc in (scores or {}).items():
        x = next((r for r in rep if r["ticker"] == tk), None)
        for k in (sc or {}).get("kpis") or []:
            if k.get("yoy") is not None:
                kpi_rows.append({"ticker": tk, "kpi": k.get("name"), "yoy": k["yoy"], "move": (x or {}).get("excess") if x else None})
    return {"generated": now_iso(), "as_of": today.isoformat(), "window_days": SEASON_DAYS, "upcoming": up[:600], "reported": rep,
            "sectors": sec_rows, "drivers": drivers, "kpi_moves": kpi_rows[:300], "version": 1}


def _spy_moves(sb):
    m = _get(sb, "market.json") or {}
    bars = ((m.get("symbols") or {}).get("SPY") or {}).get("bars") or []
    out = {}
    for i in range(1, len(bars)):
        d = dt.datetime.fromtimestamp(bars[i][0], dt.timezone.utc).date().isoformat()
        if bars[i - 1][1]:
            out[d] = bars[i][1] / bars[i - 1][1] - 1
    return out


# ------------------------------------------------------------------ job
def job(sb, step="all", time_budget=270, tickers=None, allow_model=True, log=print, today=None):
    """step: live (EDGAR feed -> scorecards), preview (due preview packs), calls (call diffs), season, all, or backfill
    (scorecards for the last week's Item 2.02 8-Ks from the filings table; run by hand)."""
    from .universe import UNIVERSE
    t0 = time.time()
    left = lambda: time_budget - (time.time() - t0)
    today = today or dt.date.today()
    names = {t: {"name": n, "sector": s, "cik": c} for t, c, n, s in UNIVERSE}
    by_cik = {c: t for t, c, *_ in UNIVERSE}
    state = _get(sb, STATE) or {}
    state.setdefault("seen", [])
    state.setdefault("previews", {})
    state.setdefault("scores", {})
    state.setdefault("calls", {})
    stats = {"scores": 0, "score_errors": {}, "previews": 0, "preview_errors": {}, "calls": 0, "season": False}
    idx = (_get(sb, "estimates/index.json") or {}).get("companies") or []
    idx_by = {r["ticker"]: r for r in idx if r.get("ticker")}

    if step in ("live", "all"):
        try:
            seen = set(state["seen"])
            ents = poll_feed(seen) if seen else parse_feed(_sec(FEED.format(start=0)))
            hits = [e for e in ents if "2.02" in e["items"] and e["cik"] in by_cik and (not tickers or by_cik[e["cik"]] in tickers)]
            if not seen:
                # First run: only the newest page, so a cold start does not score yesterday's backlog.
                hits = [e for e in hits if e.get("filed") == today.isoformat()]
            for e in hits:
                if left() < 60:
                    break
                tk = by_cik[e["cik"]]
                try:
                    sc = build_score(sb, tk, names[tk]["name"], e)
                    sb.put_json(f"earnings/score/{tk}.json", sc, cache="max-age=120")
                    state["scores"][tk] = {"accession": e["accession"], "filed": e.get("filed"), "accepted": e.get("accepted"), "generated": sc["generated"],
                                           "method": "model" if sc["method"]["model"].get("draft_model") else "rules"}
                    stats["scores"] += 1
                    log(f"scorecard {tk} {e['accession']} metrics={len(sc['metrics'])} kpis={len(sc['kpis'])} guidance={len(sc['guidance'])}")
                except Exception as ex:
                    stats["score_errors"][tk] = str(ex)[:160]
            state["seen"] = ([e["accession"] for e in ents] + state["seen"])[:600]
        except Exception as ex:
            stats["feed_error"] = str(ex)[:200]
        # Scorecards written without a model (key missing at the time, or a backfill) are redone a few per run.
        if allow_model and os.environ.get("FIREWORKS_API_KEY"):
            cut = (today - dt.timedelta(days=10)).isoformat()
            redo = [(tk, v) for tk, v in state["scores"].items() if v.get("method") == "rules" and (v.get("filed") or "") >= cut][:6]
            for tk, v in redo:
                if left() < 90:
                    break
                try:
                    sc = build_score(sb, tk, names[tk]["name"], {"accession": v["accession"], "cik": names[tk]["cik"], "filed": v.get("filed"),
                                                                 "items": ["2.02"], "accepted": v.get("accepted")})
                    sb.put_json(f"earnings/score/{tk}.json", sc, cache="max-age=120")
                    state["scores"][tk] = {**v, "generated": sc["generated"], "method": "model" if sc["method"]["model"].get("draft_model") else "rules"}
                    stats["rescored"] = stats.get("rescored", 0) + 1
                except Exception as ex:
                    stats["score_errors"][tk] = str(ex)[:160]

    if step == "backfill":
        # Scorecards for Item 2.02 8-Ks already in the filings table (filed since `since`), newest first.
        since = (today - dt.timedelta(days=7)).isoformat()
        rows = sb.select("filings", f"select=ticker,accession,filed,items&form=eq.8-K&items=like.*2.02*&filed=gte.{since}&order=filed.desc")
        for r in rows:
            tk = r.get("ticker")
            if left() < 30:
                break
            if tk not in names or (tickers and tk not in tickers) or (state["scores"].get(tk) or {}).get("accession") == r["accession"]:
                continue
            try:
                e = {"accession": r["accession"], "cik": names[tk]["cik"], "filed": r["filed"], "items": (r.get("items") or "").split(",")}
                sc = build_score(sb, tk, names[tk]["name"], e)
                sb.put_json(f"earnings/score/{tk}.json", sc, cache="max-age=600")
                state["scores"][tk] = {"accession": r["accession"], "filed": r["filed"], "generated": sc["generated"],
                                       "method": "model" if sc["method"]["model"].get("draft_model") else "rules"}
                stats["scores"] += 1
            except Exception as ex:
                stats["score_errors"][tk] = str(ex)[:160]

    if step in ("preview", "all") and left() > 60:
        taxonomy = _get(sb, "kpistd/taxonomy.json")
        red_index = ((_get(sb, "redlines/index.json") or {}).get("companies")) or {}
        due = []
        for tk, r in idx_by.items():
            if tk not in names or (tickers and tk not in tickers):
                continue
            e = r.get("earnings") or {}
            d = _date(e.get("date") or r.get("next_earnings"))
            n = trading_days_until(today, d)
            if n is None or not (-PREVIEW_BEHIND <= n <= PREVIEW_AHEAD):
                continue
            done = state["previews"].get(tk) or {}
            # Once a day per report date; a rule-written pack is redone when a model is available.
            if done.get("for") == d.isoformat() and done.get("on") == today.isoformat() and (
                    done.get("method") == "model" or not (allow_model and os.environ.get("FIREWORKS_API_KEY"))):
                continue
            due.append((d, tk))
        due.sort()
        from concurrent.futures import ThreadPoolExecutor

        def one(tk):
            r = idx_by[tk]
            p = build_preview(sb, tk, names[tk]["name"], names[tk]["sector"], r, today=today, taxonomy=taxonomy, red_index=red_index, allow_model=allow_model)
            sb.put_json(f"earnings/preview/{tk}.json", p, cache="max-age=600")
            return p
        with ThreadPoolExecutor(4) as ex:
            futs = []
            for d, tk in due:
                futs.append((d, tk, ex.submit(one, tk)))
            for d, tk, fu in futs:
                if left() < 30:
                    fu.cancel()
                    continue
                try:
                    p = fu.result(timeout=max(5, left() - 20))
                    state["previews"][tk] = {"for": d.isoformat(), "on": today.isoformat(), "generated": p["generated"], "method": p["listen_for_meta"]["method"]}
                    stats["previews"] += 1
                except Exception as ex2:
                    stats["preview_errors"][tk] = str(ex2)[:160]
        stats["previews_due"] = len(due)

    if step in ("calls", "all") and left() > 40:
        try:
            listing = _list(sb, "calls/")
        except Exception as ex:
            listing = {}
            stats["calls_error"] = str(ex)[:160]
        n = 0
        for fname, updated in sorted(listing.items(), key=lambda kv: kv[1], reverse=True):
            tk = fname[:-5] if fname.endswith(".json") else None
            if not tk or tk not in names or (tickers and tk not in tickers):
                continue
            if left() < 30 or n >= 60:
                break
            if (state["calls"].get(tk) or {}).get("src") == updated:
                continue
            calls = _get(sb, f"calls/{tk}.json")
            d = build_call_diff(calls)
            state["calls"][tk] = {"src": updated, "date": (d or {}).get("call", {}).get("date"), "generated": now_iso()}
            if d:
                sb.put_json(f"earnings/calls/{tk}.json", d, cache="max-age=3600")
                stats["calls"] += 1
            n += 1

    if step in ("season", "live", "preview", "all", "backfill") and left() > 15:
        recent = sorted(state["scores"].items(), key=lambda kv: kv[1].get("filed") or "", reverse=True)[:80]
        scores = ({tk: _get(sb, f"earnings/score/{tk}.json") for tk, _ in recent} if step in ("season", "all")
                  else {tk: {"kpis": []} for tk, _ in recent})
        season = build_season(idx, names, today=today, spy=_spy_moves(sb), scores=scores)
        sb.put_json("earnings/season.json", season, cache="max-age=300")
        stats["season"] = True
        stats["upcoming"] = len(season["upcoming"])
        stats["reported"] = len(season["reported"])
    state["updated_at"] = now_iso()
    sb.put_json(STATE, state, cache="no-store")
    stats["seconds"] = round(time.time() - t0, 1)
    return stats
