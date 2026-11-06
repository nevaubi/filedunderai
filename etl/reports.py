"""Pre-generated research documents per company: earnings preview, earnings recap and company primer.

Each document is written by the model from an evidence pack assembled deterministically from our own stored data
(SEC statements and filings, the latest earnings release, consensus and analyst records, company guidance, segment
tables, disclosure redlines, quant statistics, supply-chain links, ownership and news). Every evidence record has an
id, a source and a link. The model must cite ids for every paragraph; paragraphs citing unknown ids are dropped and
paragraphs whose numbers cannot be found in their cited records are kept but marked unverified. Key-metric tables
are computed here, not by the model.

Outputs (public-data): reports/{T}/{kind}.json, reports/{T}/{kind}.docx, reports/{T}/{kind}.md and reports/index.json.
Generated in the background by the reports cron (never on page load); users open or download the latest version.
"""
import datetime as dt
import io
import json
import os
import re
import time

from .common import AggregateLease, LeaseBusy, Run, Supabase, now_iso
from .common import request
from .estimates_ai import URL, number_tokens, supported, values_in

# Reports need far more output than the estimates summaries: a full document with cited sections. The shared
# estimates completer caps output at 1,800 tokens, which truncated every report (empty or unterminated JSON).
REPORT_MODELS = [m for m in (os.environ.get("FU_REPORTS_MODEL", "kimi-k3"), os.environ.get("FU_REPORTS_FALLBACK", "deepseek-v4p1-flash")) if m]
REPORT_MAX_TOKENS = int(os.environ.get("FU_REPORTS_MAX_TOKENS", "12000"))


def parse_model_json(txt):
    """The JSON object in a model reply: drops reasoning blocks and code fences, then takes the outermost braces."""
    txt = re.sub(r"(?s)<think>.*?</think>", "", txt or "").strip()
    txt = re.sub(r"^```(?:json)?\s*|\s*```$", "", txt).strip()
    a, b = txt.find("{"), txt.rfind("}")
    if a < 0 or b <= a:
        raise ValueError(f"no JSON object in model reply ({len(txt)} chars): {txt[:80]!r}")
    return json.loads(txt[a:b + 1])


def complete(prompt, key, models=None):
    last = None
    for model in models or REPORT_MODELS:
        try:
            d = json.loads(request(URL, method="POST", data={"model": f"accounts/fireworks/models/{model}", "messages": [{"role": "user", "content": prompt}],
                                                         "max_tokens": REPORT_MAX_TOKENS, "temperature": 0.2, "response_format": {"type": "json_object"}},
                                   headers={"Authorization": f"Bearer {key}"}, timeout=240, retries=1))
            choice = d["choices"][0]
            if choice.get("finish_reason") == "length":
                raise ValueError(f"output truncated at {REPORT_MAX_TOKENS} tokens")
            return parse_model_json(choice["message"].get("content")), model
        except Exception as e:
            last = ValueError(f"{model}: {str(e)[:160]}")
    raise last
from .universe import BY_TICKER, UNIVERSE

KINDS = {"preview": "Earnings preview", "recap": "Earnings recap", "primer": "Company primer"}
SEC = "https://www.sec.gov/Archives/edgar/data"
MAX_PER_RUN = int(os.environ.get("REPORTS_MAX_PER_RUN", "10"))
PRIMER_DAYS = int(os.environ.get("REPORTS_PRIMER_DAYS", "30"))
PREVIEW_WINDOW = int(os.environ.get("REPORTS_PREVIEW_DAYS", "10"))
RECAP_WINDOW = int(os.environ.get("REPORTS_RECAP_DAYS", "7"))


def _get(sb, path):
    try:
        return sb.get_json(path)
    except Exception:
        return None


def _filing_url(cik, accn):
    return f"{SEC}/{int(cik)}/{accn.replace('-', '')}/" if accn else None


def _today():
    return dt.date.today()


def _pd(s):
    try:
        return dt.date.fromisoformat(str(s)[:10])
    except Exception:
        return None


# ------------------------------------------------------------------ evidence pack

FIN_LABELS = [("income", "Revenue"), ("income", "Gross profit"), ("income", "Operating income"), ("income", "Net income"),
              ("income", "EPS diluted"), ("cashflow", "Operating cash flow"), ("cashflow", "Capex"),
              ("balance", "Cash & equivalents"), ("balance", "Long-term debt")]


def _row(bundle, stmt, label):
    return next((r for r in (bundle.get("statements") or {}).get(stmt) or [] if r.get("label") == label), None)


def _pts(row, kind, n):
    return [p for p in (row or {}).get(kind) or [] if p.get("v") is not None][-n:]


def financial_evidence(bundle, cik):
    ev = {}
    q_rev = {p["end"]: p["v"] for p in _pts(_row(bundle, "income", "Revenue"), "quarterly", 12)}
    a_rev = {p["end"]: p["v"] for p in _pts(_row(bundle, "income", "Revenue"), "annual", 6)}
    for stmt, label in FIN_LABELS:
        row = _row(bundle, stmt, label)
        if not row:
            continue
        q, a = _pts(row, "quarterly", 9), _pts(row, "annual", 4)
        if not q and not a:
            continue
        rec = {"metric": label, "statement": stmt, "unit": "USD per share" if "EPS" in label else "USD",
               "quarterly": [[p["end"], p["v"]] for p in q[-6:]], "annual": [[p["end"], p["v"]] for p in a[-4:]]}
        derived = {}
        if len(q) >= 5 and q[-5]["v"] and stmt != "balance":
            prev = next((x for x in q if 350 <= (_pd(q[-1]["end"]) - _pd(x["end"])).days <= 380), None)
            if prev and prev["v"]:
                derived["latest_quarter_yoy_change"] = round(q[-1]["v"] / prev["v"] - 1, 4)
        if len(a) >= 2 and a[-2]["v"] and stmt != "balance":
            derived["latest_year_yoy_change"] = round(a[-1]["v"] / a[-2]["v"] - 1, 4)
        if label not in ("Revenue", "EPS diluted") and stmt == "income":
            if q and q_rev.get(q[-1]["end"]):
                derived["latest_quarter_margin"] = round(q[-1]["v"] / q_rev[q[-1]["end"]], 4)
            if a and a_rev.get(a[-1]["end"]):
                derived["latest_year_margin"] = round(a[-1]["v"] / a_rev[a[-1]["end"]], 4)
        if stmt != "balance" and "EPS" not in label and len(q) >= 4:
            ends = [_pd(p["end"]) for p in q[-4:]]
            if all(80 <= (ends[i + 1] - ends[i]).days <= 100 for i in range(3)):
                derived["ttm"] = sum(p["v"] for p in q[-4:])
        if derived:
            rec["derived"] = derived
            rec["derived_note"] = "Computed by Filed Under from the reported values above (changes and margins as fractions)."
        last = (q or a)[-1]
        ev[f"FIN:{label}"] = {"kind": "SEC financial statements", "title": f"{label} ({stmt})", "data": rec,
                              "source": {"name": f"{last.get('form') or 'SEC filing'} filed {last.get('filed')}", "url": _filing_url(cik, last.get("accn"))},
                              "as_of": last.get("filed")}
    return ev


def release_evidence(bundle):
    r = bundle.get("release") or {}
    md = (r.get("markdown") or "").strip()
    if not md:
        return {}
    return {"REL": {"kind": "Earnings release", "title": f"Earnings release filed {r.get('filed')}",
                    "data": {"filed": r.get("filed"), "text": re.sub(r"\n{3,}", "\n\n", md)[:7000]},
                    "source": {"name": "8-K Exhibit 99.1", "url": r.get("url")}, "as_of": r.get("filed")}}


def estimates_evidence(est):
    ev = {}
    if not est:
        return ev
    c = est.get("consensus") or {}
    ne = c.get("next_earnings") or {}
    if c.get("periods"):
        ev["CONS"] = {"kind": "Consensus estimates (Yahoo Finance)", "title": "Consensus EPS and revenue by period",
                      "data": {"next_earnings": {"date": ne.get("date"), "is_estimate": ne.get("is_estimate")},
                               "periods": [{"period": p.get("label"), "fiscal_period_end": p.get("end"), "eps": p.get("eps"), "revenue": p.get("revenue")}
                                           for p in c["periods"]]},
                      "source": {"name": "Yahoo Finance via Firecrawl", "url": c.get("url")}, "as_of": c.get("as_of")}
    rev = est.get("revisions") or {}
    if rev:
        ev["REVS"] = {"kind": "Estimate revisions", "title": "Consensus revisions by window (days)", "data": rev,
                      "source": {"name": "Filed Under daily consensus snapshots", "url": None}, "as_of": c.get("as_of")}
    s = (est.get("surprises") or [])[-4:]
    if s:
        ev["SURP"] = {"kind": "Earnings surprises", "title": "Last four EPS surprises (provider basis)",
                      "data": [{k: x.get(k) for k in ("quarter_end", "eps_actual", "eps_estimate", "surprise_pct")} | {"next_session_move": (x.get("reaction") or {}).get("r1")} for x in s],
                      "source": {"name": "Yahoo Finance; reactions from Filed Under prices", "url": c.get("url")}, "as_of": c.get("as_of")}
    t = (est.get("targets") or {}).get("stats") or {}
    d = est.get("ratings") or {}
    acts = (est.get("actions") or [])[:6]
    if t or d.get("n") or acts:
        ev["STREET"] = {"kind": "Analyst ratings and targets (Benzinga)", "title": "Sell-side positioning, last 12 months",
                        "data": {"price": est.get("price"), "price_targets": t, "ratings": {"counts": d.get("counts"), "firms": d.get("n")},
                                 "recent_actions": [{k: a.get(k) for k in ("date", "firm", "action", "rating_prior", "rating_current", "pt_prior", "pt_current")} for a in acts]},
                        "source": {"name": "Benzinga via Firecrawl (Powered by Benzinga Data)", "url": None}, "as_of": (est.get("sources") or {}).get("benzinga", {}).get("retrieved_at")}
    g = est.get("guidance") or {}
    items = (g.get("items") or [])[:8]
    if items or g.get("vs_consensus"):
        ev["GUID"] = {"kind": "Company guidance", "title": "Latest guidance from company releases",
                      "data": {"items": [{k: i.get(k) for k in ("label", "period", "low", "high", "mid", "unit", "issued", "quote")} for i in items],
                               "vs_consensus": [{k: v.get(k) for k in ("label", "period", "guide_low", "guide_high", "consensus", "position", "gap_pct")} for v in (g.get("vs_consensus") or [])[:6]],
                               "credibility": g.get("credibility")},
                      "source": {"name": "Company press releases (8-K)", "url": None}, "as_of": g.get("generated")}
    return ev


def segment_evidence(seg, cik):
    if not seg:
        return {}
    for t in seg.get("tables") or []:
        if t.get("family") != "revenue" or t.get("category") not in ("segment", "product"):
            continue
        rows = []
        for r in (t.get("rows") or [])[:12]:
            a = r.get("A") or {}
            ends = sorted(a)[-2:]
            if not ends:
                continue
            cur = a[ends[-1]]
            prev = a[ends[0]] if len(ends) == 2 else None
            item = {"segment": r.get("label"), "fiscal_year_end": ends[-1], "value": cur.get("v")}
            if prev and prev.get("v"):
                item["prior_value"] = prev.get("v")
                item["yoy_change"] = round(cur["v"] / prev["v"] - 1, 4) if cur.get("v") is not None else None
            rows.append(item)
        if rows:
            return {"SEG": {"kind": "Segment revenue (XBRL)", "title": t.get("category_label") or "Segments", "data": rows,
                            "source": {"name": "10-K segment disclosures", "url": None}, "as_of": seg.get("generated")}}
    return {}


def redline_evidence(red):
    if not red:
        return {}
    ev = {}
    for c in red.get("comparisons") or []:
        hl = []
        for s in c.get("sections") or []:
            for ch in s.get("changes") or []:
                if ch.get("kind") == "numbers":
                    continue
                text = " ".join(t for op, t in ch.get("ops") or [] if op == "+") or ch.get("new") or ""
                if ch.get("kind") == "removed":
                    text = " ".join(t for op, t in ch.get("ops") or [] if op == "-") or ch.get("old") or ""
                hl.append((ch.get("score", 0), {"section": s.get("title"), "change": ch.get("kind"), "flags": ch.get("signals"), "text": text[:600]}))
        hl = [h for _, h in sorted(hl, key=lambda x: -x[0])[:6]]
        if hl:
            ev[f"RED:{c['kind']}"] = {"kind": "Disclosure changes", "title": f"{c['form']} filed {c['new']['filed']} vs {c['old']['form']} filed {c['old']['filed']}",
                                      "data": {"totals": c.get("totals"), "top_changes": hl},
                                      "source": {"name": f"{c['form']} {c['new']['accn']}", "url": c["new"].get("url")}, "as_of": c["new"].get("filed")}
    return ev


def quant_evidence(q):
    if not q:
        return {}
    st = q.get("stats") or {}
    data = {"as_of": q.get("as_of"), "history_from": q.get("first")}
    if st.get("perf"):
        data["performance"] = {k: {kk: v.get(kk) for kk in ("ann_return", "ann_vol", "sharpe", "from")} for k, v in st["perf"].items() if k in ("1y", "3y", "5y")}
    if st.get("vol"):
        data["realized_vol"] = {f"{k}d": {kk: v.get(kk) for kk in ("now", "pct")} for k, v in st["vol"].items()}
    dd = (st.get("dd") or {}).get("top") or []
    if dd:
        data["max_drawdown"] = {k: dd[0].get(k) for k in ("peak", "trough", "depth")}
    bench = (q.get("corr") or {}).get("bench") or {}
    if bench:
        data["beta_correlation"] = {b: {k: v.get(k) for k in ("c252", "b252", "c63", "b63")} for b, v in bench.items()}
    e = (q.get("events") or {}).get("earnings") or {}
    if e.get("stats"):
        es = e["stats"]
        data["earnings_reactions"] = {k: es.get(k) for k in ("n", "mean_abs_cc", "median_abs_cc", "up_share", "mean_abn")}
        for w in ("drift5", "drift20"):
            if isinstance(es.get(w), dict):
                data["earnings_reactions"][w] = {k: es[w].get(k) for k in ("mean", "hit", "p")}
    return {"QNT": {"kind": "Quant statistics", "title": "Returns, volatility, beta and earnings reactions", "data": data,
                    "source": {"name": "Filed Under quant lab (stored daily prices)", "url": None}, "as_of": q.get("as_of")}}


def supply_evidence(sup):
    links = [l for l in (sup or {}).get("links") or [] if l.get("q")][:10]
    if not links:
        return {}
    return {"SUP": {"kind": "Supply chain (filing-stated)", "title": "Named counterparties in the latest filings",
                    "data": [{"relation": l.get("rel"), "counterparty": l.get("cp"), "ticker": l.get("cpt"), "share": l.get("sh"), "single_source": l.get("ss"),
                              "quote": l.get("q"), "filing": f"{l.get('form')} {l.get('filed')}"} for l in links],
                    "source": {"name": "Company 10-K/10-Q (verbatim quotes)", "url": None}, "as_of": sup.get("generated")}}


def ownership_evidence(ins, gov, flows):
    data = {}
    if ins:
        data["form4_last_365d"] = ins.get("w365")
    if gov:
        trades = gov.get("congress_trades") or []
        cutoff = (_today() - dt.timedelta(days=365)).isoformat()
        recent = [t for t in trades if str(t.get("tx_date") or "") >= cutoff]
        if recent:
            data["congress_trades_last_365d"] = {"count": len(recent), "purchases": sum(1 for t in recent if "Purchase" in str(t.get("type"))),
                                                 "sales": sum(1 for t in recent if "Sale" in str(t.get("type")))}
        lob = gov.get("lobbying") or {}
        if lob.get("total") or lob.get("by_year"):
            data["lobbying"] = {k: lob.get(k) for k in ("total", "by_year") if lob.get(k) is not None}
    if flows:
        f13 = flows.get("inst13f") or {}
        if f13.get("top"):
            data["top_13f_holders"] = [{k: h.get(k) for k in ("name", "shares", "value", "change_shares")} for h in (f13.get("top") or [])[:6]]
    if not data:
        return {}
    return {"OWN": {"kind": "Ownership and influence", "title": "Insiders, institutions and government", "data": data,
                    "source": {"name": "SEC Forms 4 and 13F, House disclosures, Senate LDA", "url": None}, "as_of": (gov or flows or ins or {}).get("generated")}}


def news_evidence(bundle):
    items = ((bundle.get("news") or {}).get("items") or [])[:8]
    if not items:
        return {}
    return {"NEWS": {"kind": "News", "title": "Recent coverage", "data": [{"date": i.get("date"), "title": i.get("title"), "source": i.get("source")} for i in items],
                     "source": {"name": "Web news search", "url": None}, "as_of": (bundle.get("news") or {}).get("fetched_at")}}


def evidence(sb, tk):
    u = BY_TICKER[tk]
    bundle = _get(sb, f"companies/{tk}.json") or {}
    est = _get(sb, f"estimates/{tk}.json")
    ev = {"CO": {"kind": "Company", "title": "Company profile", "data": {"name": u["name"], "ticker": tk, "sector": u["sector"],
                 "profile": {k: v for k, v in (bundle.get("profile") or {}).items() if isinstance(v, (str, int, float))}},
                 "source": {"name": "SEC submissions and company profile", "url": None}, "as_of": bundle.get("generated")}}
    for part in (financial_evidence(bundle, u["cik"]), release_evidence(bundle), estimates_evidence(est),
                 segment_evidence(_get(sb, f"segments/{tk}.json"), u["cik"]), redline_evidence(_get(sb, f"redlines/{tk}.json")),
                 quant_evidence(_get(sb, f"quant/t/{tk}.json")), supply_evidence(_get(sb, f"supply/co/{tk}.json")),
                 ownership_evidence(_get(sb, f"insiders/{tk}.json"), _get(sb, f"gov/{tk}.json"), _get(sb, f"flows/{tk}.json")),
                 news_evidence(bundle)):
        ev.update(part)
    return ev, bundle, est


# ------------------------------------------------------------------ prompts

OUTLINES = {
    "preview": [("setup", "What the Street expects", "Consensus EPS and revenue for the quarter being reported, the range, number of estimates and recent revisions."),
                ("last_quarter", "Where the last quarter left off", "Latest reported results, growth and margins, and any guidance the company gave for this quarter or year."),
                ("watch", "What to watch", "Specific items: segment trends, margin drivers, guidance versus consensus, disclosure changes and supply-chain items in the evidence."),
                ("positioning", "Positioning and history", "Ratings and price-target distribution, recent analyst actions, the surprise record and typical post-earnings moves."),
                ("risks", "Risks to the print", "Risks evidenced in the records (risk-factor changes, concentration, regulation, litigation).")],
    "recap": [("results", "Results versus expectations", "Reported revenue and EPS against consensus and the surprise; growth and margins."),
              ("guidance", "Guidance", "What the company guided, how it compares with consensus and with prior guidance."),
              ("drivers", "Drivers and segments", "Segment and product trends and what the release says about them, with short quotes."),
              ("reaction", "Market and Street reaction", "Next-session price move and analyst actions after the release, as recorded."),
              ("changes", "Disclosure changes", "Material wording changes in the latest periodic filing."),
              ("watch_next", "Into next quarter", "Open questions the evidence leaves for the next report.")],
    "primer": [("business", "Business overview", "What the company does, segments and their size, using only the records."),
               ("financials", "Financial profile", "Multi-year revenue, profitability, cash generation and balance sheet."),
               ("segments", "Segments and key metrics", "Segment growth and mix."),
               ("ecosystem", "Customers, suppliers and competition", "Named counterparties and concentration from filings."),
               ("street", "Street view", "Consensus, ratings, targets and the surprise record."),
               ("risks", "Risks and disclosure changes", "Notable risk-factor and note changes, legal and regulatory items."),
               ("ownership", "Ownership and influence", "Insider activity, institutional holders, congressional trades and lobbying when present."),
               ("trading", "Trading profile", "Volatility, beta and earnings-reaction history from the quant statistics.")]}

PROMPT = """You are a senior equity research analyst writing a {kind_label} for {name} ({tk}) dated {today}.
Write for professional investors: precise, neutral, no hype, no investment recommendation, no price predictions.
Use ONLY the evidence records below; each has an id. Do not use outside knowledge. If the evidence does not support a
section, write one short paragraph saying what is not available rather than guessing.

Every paragraph and bullet must cite one or more evidence ids in "cites". Quote numbers exactly as they appear in the
cited records; you may round, convert fractions to percentages (0.1234 -> 12.3%) and express USD in millions or billions.
Do not compute new figures beyond simple differences between two cited numbers.

Number style: dollar amounts as $9.35B or $580M (never long digit strings), per-share figures to two decimals ($3.88),
percentages to one decimal (39.0%), multiples to one decimal (21.5x). Write dates as Nov 9, 2026.
Never write a sentence whose only content is that some data is missing, null or unavailable; leave that topic out.
Lead each paragraph with its conclusion, then the supporting figures. Explain why a figure matters in plain words.

Sections (keep this order and these ids):
{outline}

Return JSON:
{{"title": "...", "subtitle": "...", "summary": [{{"text": "...", "cites": ["FIN:Revenue"]}}],
  "sections": [{{"id": "...", "heading": "...", "paragraphs": [{{"text": "...", "cites": ["..."]}}], "bullets": [{{"text": "...", "cites": ["..."]}}]}}]}}
"summary" is 3 to 5 key points. Each section has 1 to 3 paragraphs and optional bullets.

Evidence:
{evidence}
"""


def _evidence_for_prompt(ev, limit=60000):
    slim = {k: {"kind": v["kind"], "title": v["title"], "as_of": v.get("as_of"), "data": v["data"]} for k, v in ev.items()}
    txt = json.dumps(slim, default=str)
    if len(txt) <= limit:
        return txt
    order = ["CO", "CONS", "FIN:Revenue", "FIN:Net income", "FIN:EPS diluted", "FIN:Operating income", "SURP", "GUID", "STREET", "REL", "SEG",
             "RED:annual", "RED:quarterly", "SUP", "QNT", "OWN", "NEWS"]
    out = {}
    for k in order + [k for k in slim if k not in order]:
        if k in slim:
            trial = dict(out, **{k: slim[k]})
            if len(json.dumps(trial, default=str)) > limit:
                continue
            out = trial
    return json.dumps(out, default=str)


_MISSING = re.compile(r"\b(no [\w\s-]{0,40}(data|record|records|information) (is|are|was|were) (available|provided)|(is|are) (null|not available|unavailable))\b", re.I)


def _money(m):
    v = float(m.group(1).replace(",", ""))
    if v >= 1e12:
        return f"${v / 1e12:.2f}T"
    if v >= 1e9:
        return f"${v / 1e9:.2f}B" if v < 1e10 else f"${v / 1e9:.1f}B"
    if v >= 1e6:
        return f"${v / 1e6:,.0f}M"
    return m.group(0)


def tidy_numbers(text):
    """Display-only rounding after verification: long dollar integers to $M/$B/$T, long decimals to 2 places,
    percentages to 1 place. The verified value is unchanged; only its printed precision is."""
    text = re.sub(r"\$(\d{1,3}(?:,\d{3}){2,}|\d{7,})(?![\d.])", _money, text)
    rnd = lambda n: (lambda m: f"{float(m.group(1)):.{n}f}")
    text = re.sub(r"(?<=\$)(\d+\.\d{3,})", rnd(2), text)
    text = re.sub(r"(?<![\d.])(\d+\.\d{2,})(?=\s?%)", rnd(1), text)
    text = re.sub(r"(?<![\d.$])(\d+\.\d{4,})(?![\d])", rnd(2), text)
    return text


def validate_items(items, ev):
    out = []
    for p in items or []:
        text = str((p or {}).get("text") or "").strip()
        cites = [c for c in (p or {}).get("cites") or [] if c in ev]
        if not text or not cites:
            continue
        vals = values_in([ev[c]["data"] for c in cites])
        bad = [t for t in number_tokens(text) if not supported(t, vals)]
        if _MISSING.search(text) and len(number_tokens(text)) <= 2:
            continue
        out.append({"text": tidy_numbers(text), "cites": cites, "verified": not bad, "unsupported_numbers": bad or None})
    return out


def metrics_table(ev):
    """Deterministic key-metrics table (latest quarter and year) from the financial evidence."""
    rows = []
    for _, label in FIN_LABELS:
        e = ev.get(f"FIN:{label}")
        if not e:
            continue
        d = e["data"]
        q = d["quarterly"][-1] if d["quarterly"] else None
        a = d["annual"][-1] if d["annual"] else None
        der = d.get("derived") or {}
        rows.append({"metric": label, "unit": d["unit"], "quarter_end": q and q[0], "quarter": q and q[1], "quarter_yoy": der.get("latest_quarter_yoy_change"),
                     "year_end": a and a[0], "year": a and a[1], "year_yoy": der.get("latest_year_yoy_change"), "ttm": der.get("ttm"),
                     "margin_q": der.get("latest_quarter_margin"), "cite": f"FIN:{label}"})
    return rows


def write(kind, tk, ev, completer=complete, key=None):
    u = BY_TICKER[tk]
    outline = "\n".join(f'- id "{i}": {h}. {d}' for i, h, d in OUTLINES[kind])
    prompt = PROMPT.format(kind_label=KINDS[kind].lower(), name=u["name"], tk=tk, today=_today().isoformat(), outline=outline,
                           evidence=_evidence_for_prompt(ev))
    out, model = completer(prompt, key or os.environ.get("FIREWORKS_API_KEY"))
    sections = []
    for i, heading, _ in OUTLINES[kind]:
        s = next((x for x in out.get("sections") or [] if x.get("id") == i), None) or {}
        paras, bullets = validate_items(s.get("paragraphs"), ev), validate_items(s.get("bullets"), ev)
        if paras or bullets:
            sections.append({"id": i, "heading": str(s.get("heading") or heading)[:120], "paragraphs": paras, "bullets": bullets})
    summary = validate_items(out.get("summary"), ev)
    if not sections:
        raise ValueError("model output had no cited sections")
    used = sorted({c for s in sections for p in s["paragraphs"] + s["bullets"] for c in p["cites"]} | {c for p in summary for c in p["cites"]})
    total = sum(len(s["paragraphs"]) + len(s["bullets"]) for s in sections) + len(summary)
    unverified = sum(1 for s in sections for p in s["paragraphs"] + s["bullets"] if not p["verified"]) + sum(1 for p in summary if not p["verified"])
    return {"schema_version": 1, "kind": kind, "kind_label": KINDS[kind], "ticker": tk, "name": u["name"], "sector": u["sector"],
            "title": str(out.get("title") or f"{u['name']}: {KINDS[kind]}")[:160], "subtitle": str(out.get("subtitle") or "")[:240],
            "label": "AI-generated from cited records", "model": model, "generated_at": now_iso(),
            "summary": summary, "sections": sections, "metrics": metrics_table(ev),
            "evidence": {k: ev[k] for k in used} | {k: ev[k] for k in ev if k.startswith("FIN:")},
            "quality": {"items": total, "unverified": unverified, "evidence_records": len(ev), "cited_records": len(used)}}


# ------------------------------------------------------------------ renderers

def fmt_usd(v, unit="USD"):
    if v is None:
        return "–"
    if "share" in unit:
        return f"${v:,.2f}"
    a = abs(v)
    s = f"{a / 1e9:,.2f}B" if a >= 1e9 else f"{a / 1e6:,.1f}M" if a >= 1e6 else f"{a:,.0f}"
    return ("−$" if v < 0 else "$") + s


def fmt_pct(v):
    return "–" if v is None else f"{v * 100:+.1f}%"


def to_markdown(doc):
    order = {k: i + 1 for i, k in enumerate(doc["evidence"])}
    cite = lambda cs: "".join(f"[{order[c]}]" for c in cs if c in order)
    lines = [f"# {doc['title']}", "", f"_{doc['subtitle']}_" if doc.get("subtitle") else "", "",
             f"{doc['kind_label']} · {doc['name']} ({doc['ticker']}) · generated {doc['generated_at'][:10]} · {doc['label']} ({doc['model']})", ""]
    if doc["summary"]:
        lines += ["## Key points", ""] + [f"- {p['text']} {cite(p['cites'])}{'' if p['verified'] else ' (unverified figure)'}" for p in doc["summary"]] + [""]
    if doc["metrics"]:
        lines += ["## Key metrics", "", "| Metric | Latest quarter | YoY | Latest year | YoY | TTM |", "|---|---|---|---|---|---|"]
        for r in doc["metrics"]:
            lines.append(f"| {r['metric']} | {fmt_usd(r['quarter'], r['unit'])} ({r['quarter_end'] or '–'}) | {fmt_pct(r['quarter_yoy'])} | "
                         f"{fmt_usd(r['year'], r['unit'])} ({r['year_end'] or '–'}) | {fmt_pct(r['year_yoy'])} | {fmt_usd(r['ttm'], r['unit'])} |")
        lines.append("")
    for s in doc["sections"]:
        lines += [f"## {s['heading']}", ""]
        for p in s["paragraphs"]:
            lines += [f"{p['text']} {cite(p['cites'])}{'' if p['verified'] else ' (unverified figure)'}", ""]
        for b in s["bullets"]:
            lines.append(f"- {b['text']} {cite(b['cites'])}")
        if s["bullets"]:
            lines.append("")
    lines += ["## Sources", ""]
    for k, n in order.items():
        e = doc["evidence"][k]
        src = e.get("source") or {}
        lines.append(f"{n}. {e['title']} ({e['kind']}; {src.get('name') or ''}{', as of ' + str(e['as_of'])[:10] if e.get('as_of') else ''})"
                     + (f" {src['url']}" if src.get("url") else ""))
    lines += ["", "Generated by Filed Under from stored SEC filings and cited third-party data. AI-written text is checked against the cited "
              "records; items marked unverified contain a figure not found in its sources. Not investment advice."]
    return "\n".join(l for l in lines if l is not None)


def to_docx(doc, brand=None):
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Pt, RGBColor
    brand = brand or {"firm": "Filed Under", "accent": (0x2B, 0x3A, 0x55)}
    accent = RGBColor(*brand["accent"])
    order = {k: i + 1 for i, k in enumerate(doc["evidence"])}
    d = Document()
    st = d.styles["Normal"]
    st.font.name, st.font.size = "Calibri", Pt(10.5)
    sec = d.sections[0]
    sec.header.paragraphs[0].text = f"{brand['firm']} · {doc['kind_label']} · {doc['ticker']}"
    sec.footer.paragraphs[0].text = f"Generated {doc['generated_at'][:10]}. {doc['label']}. Not investment advice."
    for p in (sec.header.paragraphs[0], sec.footer.paragraphs[0]):
        for r in p.runs:
            r.font.size, r.font.color.rgb = Pt(8), RGBColor(0x66, 0x66, 0x66)
    h = d.add_heading(doc["title"], level=0)
    for r in h.runs:
        r.font.color.rgb = accent
    if doc.get("subtitle"):
        p = d.add_paragraph(doc["subtitle"])
        p.runs[0].italic = True
    meta = d.add_paragraph(f"{doc['name']} ({doc['ticker']}) · {doc['sector']} · {doc['kind_label']} · {doc['generated_at'][:10]}")
    meta.runs[0].font.size, meta.runs[0].font.color.rgb = Pt(9), RGBColor(0x55, 0x55, 0x55)

    def cited(par, item):
        par.add_run(item["text"])
        for c in item["cites"]:
            if c in order:
                r = par.add_run(f"[{order[c]}]")
                r.font.superscript, r.font.size, r.font.color.rgb = True, Pt(8), accent
        if not item["verified"]:
            r = par.add_run(" (unverified figure)")
            r.italic, r.font.size, r.font.color.rgb = True, Pt(8), RGBColor(0xA0, 0x40, 0x20)

    if doc["summary"]:
        d.add_heading("Key points", level=1)
        for p in doc["summary"]:
            cited(d.add_paragraph(style="List Bullet"), p)
    if doc["metrics"]:
        d.add_heading("Key metrics", level=1)
        t = d.add_table(rows=1, cols=6)
        t.style = "Light Grid Accent 1"
        for i, hd in enumerate(["Metric", "Latest quarter", "YoY", "Latest year", "YoY", "TTM"]):
            t.rows[0].cells[i].text = hd
        for r in doc["metrics"]:
            cells = t.add_row().cells
            vals = [r["metric"], f"{fmt_usd(r['quarter'], r['unit'])} ({r['quarter_end'] or '–'})", fmt_pct(r["quarter_yoy"]),
                    f"{fmt_usd(r['year'], r['unit'])} ({r['year_end'] or '–'})", fmt_pct(r["year_yoy"]), fmt_usd(r["ttm"], r["unit"])]
            for i, v in enumerate(vals):
                cells[i].text = v
                if i:
                    cells[i].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.RIGHT
        note = d.add_paragraph("Computed from SEC filings by Filed Under; YoY compares the same fiscal period a year earlier.")
        note.runs[0].font.size = Pt(8)
    for s in doc["sections"]:
        hh = d.add_heading(s["heading"], level=1)
        for r in hh.runs:
            r.font.color.rgb = accent
        for p in s["paragraphs"]:
            cited(d.add_paragraph(), p)
        for b in s["bullets"]:
            cited(d.add_paragraph(style="List Bullet"), b)
    d.add_heading("Sources", level=1)
    for k, n in order.items():
        e = doc["evidence"][k]
        src = e.get("source") or {}
        p = d.add_paragraph(f"[{n}] {e['title']} — {e['kind']}; {src.get('name') or ''}" + (f"; as of {str(e['as_of'])[:10]}" if e.get("as_of") else ""))
        if src.get("url"):
            p.add_run(f" {src['url']}").font.color.rgb = accent
        for r in p.runs:
            r.font.size = Pt(8.5)
    disc = d.add_paragraph("Generated by Filed Under from stored SEC filings and cited third-party data. AI-written text is checked "
                           "against the cited records; items marked unverified contain a figure not found in its sources. Not investment advice.")
    disc.runs[0].font.size, disc.runs[0].italic = Pt(8), True
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


# ------------------------------------------------------------------ scheduling

def due(sb, index, today=None, max_n=MAX_PER_RUN):
    """[(kind, ticker, signature)] to generate now: previews before earnings, recaps after, then stale primers."""
    today = today or _today()
    est_idx = {r["ticker"]: r for r in ((_get(sb, "estimates/index.json") or {}).get("companies") or [])}
    caps_by = {r["ticker"]: float(r.get("market_cap") or 0) for r in ((_get(sb, "index.json") or {}).get("companies") or [])}
    universe = [u[0] for u in UNIVERSE]
    todo = []
    for tk in universe:
        r = est_idx.get(tk) or {}
        ne = _pd(r.get("next_earnings"))
        if ne and 1 <= (ne - today).days <= PREVIEW_WINDOW:
            sig = f"preview:{ne}"
            if (index.get(tk) or {}).get("preview", {}).get("sig") != sig:
                mcap = caps_by.get(tk, 0)
                todo.append((0, (ne - today).days * 1e15 - mcap, "preview", tk, sig))
    try:
        since = (today - dt.timedelta(days=RECAP_WINDOW)).isoformat()
        rows = sb.select("filings", f"select=ticker,accession,filed,items&form=eq.8-K&filed=gte.{since}&items=like.*2.02*&order=filed.desc")
    except Exception:
        rows = []
    live = set(universe)
    for r in rows:
        tk = r.get("ticker")
        if tk not in live:
            continue
        sig = f"recap:{r['accession']}"
        if (index.get(tk) or {}).get("recap", {}).get("sig") != sig and not any(t[3] == tk and t[2] == "recap" for t in todo):
            todo.append((1, -int(r["filed"].replace("-", "")), "recap", tk, sig))
    caps = sorted(universe, key=lambda t: -caps_by.get(t, 0))
    for rank, tk in enumerate(caps):
        p = (index.get(tk) or {}).get("primer") or {}
        g = _pd(p.get("generated_at"))
        if not g or (today - g).days >= PRIMER_DAYS:
            todo.append((2, rank, "primer", tk, f"primer:{today.isoformat()}"))
    todo.sort()
    return [(k, tk, sig) for _, _, k, tk, sig in todo[:max_n]]


def publish(sb, kind, tk, doc, sig):
    base = f"reports/{tk}/{kind}"
    sb.put_json(base + ".json", doc, cache="max-age=300")
    md = to_markdown(doc).encode()
    sb.put_object("public-data", base + ".md", md, content_type="text/markdown; charset=utf-8", cache="max-age=300")
    sb.put_object("public-data", base + ".docx", to_docx(doc), content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document", cache="max-age=300")
    return {"sig": sig, "generated_at": doc["generated_at"], "title": doc["title"], "quality": doc["quality"], "model": doc["model"]}


def run_job(sb, time_budget=640, max_n=MAX_PER_RUN, only=None, log=print, completer=complete):
    key = os.environ.get("FIREWORKS_API_KEY")
    if not key and completer is complete:
        return {"skipped": "FIREWORKS_API_KEY not set"}
    t0 = time.time()
    index = (_get(sb, "reports/index.json") or {}).get("companies") or {}
    if only:
        todo = [(k, tk, f"{k}:manual:{now_iso()}") for k, tk in only]
    else:
        todo = due(sb, index, max_n=max_n)
    stats = {"planned": [f"{k}:{t}" for k, t, _ in todo], "done": [], "failed": {}}
    updates = {}
    for kind, tk, sig in todo:
        if time.time() - t0 > time_budget - 90:
            break
        try:
            ev, _, _ = evidence(sb, tk)
            if len([k for k in ev if k.startswith("FIN:")]) < 2:
                raise ValueError("not enough financial evidence")
            doc = write(kind, tk, ev, completer=completer, key=key)
            updates.setdefault(tk, {})[kind] = publish(sb, kind, tk, doc, sig)
            stats["done"].append(f"{kind}:{tk}")
        except Exception as e:
            stats["failed"][f"{kind}:{tk}"] = str(e)[:200]
    if updates:
        for attempt in range(6):
            try:
                with AggregateLease(sb, name="reports-index", seconds=120):
                    idx = _get(sb, "reports/index.json") or {"schema_version": 1, "companies": {}}
                    for tk, kinds in updates.items():
                        idx["companies"].setdefault(tk, {}).update(kinds)
                    idx["generated"] = now_iso()
                    sb.put_json("reports/index.json", idx, cache="max-age=120")
                break
            except LeaseBusy:
                time.sleep(5)
    stats["seconds"] = round(time.time() - t0, 1)
    return stats


def run(sb, time_budget=640):
    with Run(sb, "reports") as r:
        r.stats = run_job(sb, time_budget=time_budget)
        return r.stats
