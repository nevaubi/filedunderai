"""Earnings-call intelligence from stored transcripts and prepared remarks (ir_documents).

    python -m etl.run calls                       incremental: new or changed documents, recent calls first
    python -m etl.run calls --tickers MSFT AAPL   a subset
    python -m etl.run calls --step publish        rebuild the cross-company files (themes, people, coverage)
    python -m etl.run calls --step db             copy the published call bundles into the calls tables

Pipeline per document: text (kpitools.text_of) -> deterministic parse (callparse) -> classify; when the speaker labels
are ambiguous the model returns the label roster (cached) and the parse runs again with it. Documents for the same
event (a transcript and the prepared remarks, or a Q&A-only file) are grouped and the most complete one becomes the
call. Each call then gets one model analysis with strict JSON: themes on a fixed taxonomy with sentiment and
management confidence, guidance statements, analyst concerns, answers that were not direct, and changes versus the
previous call. Every item must carry a quote; quotes are matched back to the source text and stored as character
offsets into the document text (pages joined with "\\n"), so the site opens the document at the exact passage. Items
whose quote cannot be found verbatim are dropped. Model results are cached in the raw bucket by a hash of the prompt
version and the inputs, so re-runs cost nothing unless the document or the previous call changes.

Storage (public-data): calls/{T}.json (calls with analysis), calls/{T}/{call_id}.json (turn text), calls/themes.json,
calls/themes/{theme}.json, calls/people.json, calls/coverage.json. Private state: raw/calls/state.json.gz.
"""
import collections
import concurrent.futures as cf
import datetime as dt
import difflib
import gzip
import hashlib
import json
import os
import re
import time

from . import callparse as cp
from .common import request, now_iso, HTTPError

PRIMARY = os.environ.get("FU_CALLS_MODEL") or os.environ.get("FU_SUMMARY_MODEL", "deepseek-v4p1-flash")
FALLBACK = os.environ.get("FU_CALLS_FALLBACK") or os.environ.get("FU_SUMMARY_FALLBACK", "kimi-k3p1-pro")
PROMPT_VERSION = "calls-v1"
ROSTER_VERSION = "roster-v1"
PARSE_VERSION = 5  # bump when callparse changes so stored documents are parsed again
STATE_PATH = "calls/state.json"
RECENT_QUARTERS = 8

THEMES = [
    ("pricing", "Pricing", "Price increases, discounts, price/mix, rate actions"),
    ("demand", "Demand", "Volumes, orders, bookings, traffic, backlog, end-market demand"),
    ("margins", "Margins", "Gross, operating or segment margin direction and drivers"),
    ("costs", "Costs", "Cost inflation, cost savings, restructuring, efficiency programs"),
    ("ai", "AI", "Artificial intelligence products, demand, investment, productivity"),
    ("tariffs", "Tariffs and trade", "Tariffs, trade policy, export controls"),
    ("capex", "Capex", "Capital spending, capacity additions, data centers, plants"),
    ("guidance", "Guidance and outlook", "Forward targets, raised/lowered/maintained outlook"),
    ("labor", "Labor", "Hiring, headcount, wages, layoffs, labor availability"),
    ("supply_chain", "Supply chain", "Supply constraints, logistics, inventories at suppliers, lead times"),
    ("regulation", "Regulation and policy", "Regulators, government policy, approvals, legislation"),
    ("m_and_a", "M&A", "Acquisitions, divestitures, spin-offs, integration"),
    ("buybacks", "Buybacks", "Share repurchases"),
    ("dividends", "Dividends", "Dividend levels and policy"),
    ("balance_sheet", "Balance sheet and leverage", "Debt, liquidity, refinancing, ratings, cash"),
    ("competition", "Competition", "Market share, competitors, competitive intensity"),
    ("macro", "Macro and consumer", "Economy, consumer health, recession risk, spending patterns"),
    ("rates", "Interest rates", "Rate levels and their effect on the business, net interest income"),
    ("fx", "Currency", "Foreign exchange effects"),
    ("credit", "Credit quality", "Loan losses, delinquencies, reserves, counterparty risk"),
    ("china", "China and international", "China, emerging markets, geopolitics, regions outside the US"),
    ("inventory", "Inventory", "Company or channel inventory levels and destocking"),
    ("products", "Products and pipeline", "Launches, pipeline, R&D programs, clinical trials"),
    ("legal", "Litigation", "Lawsuits, settlements, investigations"),
    ("energy", "Energy and commodities", "Oil, gas, power, metals and other input or output prices"),
    ("capital_allocation", "Capital allocation", "Priorities among investment, returns and debt reduction"),
]
THEME_IDS = [t[0] for t in THEMES]

HEDGES = re.compile(r"\b(may|might|could|possibly|perhaps|approximately|roughly|around|somewhat|uncertain(ty)?|unclear|hopefully|"
                    r"potentially|likely|unlikely|believe|we think|i think|probably|appears?|seems?|suggests?|anticipate|"
                    r"tentative(ly)?|depend(s|ing)? on|subject to|assum(e|ing|ption)|estimate[sd]?|expect(s|ed)?|too early|"
                    r"hard to (say|predict|tell)|difficult to (say|predict)|we'?ll see|time will tell|cautious(ly)?|"
                    r"kind of|sort of|a bit|a little)\b", re.I)
POSITIVE = re.compile(r"\b(strong(er|est|ly)?|record|grow(th|ing|s)?|improv(e|ed|es|ing|ement)|exceed(ed|s)?|outperform(ed|ing)?|"
                      r"momentum|robust|solid|accelerat(e|ed|ing|ion)|gain(s|ed)?|expand(ed|ing)?|expansion|benefit(s|ed)?|"
                      r"pleased|proud|excited|confident|confidence|success(ful|fully)?|achiev(e|ed|ing)|opportunit(y|ies)|"
                      r"healthy|resilient|resilience|beat|upside|favorable|tailwinds?|outstanding|excellent|great|better|best|"
                      r"progress|advance(d|s)?|win(s|ning)?|won|leadership|efficien(t|cy)|profitab(le|ility)|rais(e|ed|ing))\b", re.I)
NEGATIVE = re.compile(r"\b(declin(e|ed|es|ing)|decrease[sd]?|weak(er|ness|ened)?|soft(er|ness)|loss(es)?|lower|down|pressure[sd]?|"
                      r"headwinds?|challeng(e|es|ed|ing)|difficult(y|ies)?|slow(er|ed|ing|down)?|risk(s|y)?|uncertain(ty|ties)?|"
                      r"disappoint(ed|ing)?|miss(ed)?|shortfall|delay(s|ed)?|disrupt(ed|ion|ions)?|volatil(e|ity)|concern(s|ed)?|"
                      r"unfavorable|impair(ed|ment)|deteriorat(e|ed|ing|ion)|cut(s|ting)?|reduc(e|ed|ing|tion)|layoffs?|"
                      r"downturn|recession|inflation(ary)?|tariffs?|constrain(ed|ts?)|shortages?|worse|negative(ly)?|drag)\b", re.I)


# ---------------------------------------------------------------- model calls

LLM_DOWN = []  # set when the provider refuses the account (billing, auth); later calls in the run fail fast


def _model(prompt, max_tokens, temperature=0.1, effort="low"):
    """JSON completion through etl.llm.chat_json (cut-off replies retried with double the budget, then the fallback
    model). A provider refusal of the account (billing, auth) makes later calls in the run fail fast."""
    from .llm import LLMError, chat_json
    if LLM_DOWN:
        raise RuntimeError("model provider refused the account earlier in this run: " + LLM_DOWN[0])
    try:
        j, model, meta = chat_json([PRIMARY, FALLBACK], [{"role": "user", "content": prompt}], max_tokens=max_tokens,
                                   temperature=temperature, timeout=240)
    except LLMError as e:
        if any(str(code) in str(a) for a in getattr(e, "attempts", []) for code in ("HTTP 401", "HTTP 402", "HTTP 403", "HTTP 412")):
            LLM_DOWN.append(str(e)[:200])
        raise
    meta = meta or {}
    return j, {"model": model, "effort": meta.get("effort"), "seconds": meta.get("seconds"), "in": meta.get("in") or meta.get("prompt_tokens"),
               "out": meta.get("out") or meta.get("completion_tokens")}


def _cache_get(sb, key):
    b = sb.get_object("raw", f"calls/llm/{key}.json.gz")
    if not b:
        return None
    try:
        return json.loads(gzip.decompress(b))
    except Exception:
        return None


def _cache_put(sb, key, obj):
    sb.put_object("raw", f"calls/llm/{key}.json.gz", gzip.compress(json.dumps(obj, separators=(",", ":")).encode()), "application/gzip", cache="no-store")


def cached_model(sb, kind, payload, prompt, max_tokens, effort="low"):
    key = kind + "-" + hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:40]
    hit = _cache_get(sb, key)
    if hit is not None:
        return hit["result"], hit["meta"], True
    if LLM_DOWN:
        raise RuntimeError("model provider unavailable: " + LLM_DOWN[0])
    res, meta = _model(prompt, max_tokens, effort=effort)
    meta["at"] = now_iso()
    _cache_put(sb, key, {"result": res, "meta": meta, "payload": payload})
    return res, meta, False


ROSTER_PROMPT = """You are reading the start of a document a company published to investors. Decide what it is and, if it is a call transcript or a script of prepared remarks, list how speakers are labelled in the text.

Company: {company} ({ticker})
Document title: {title}

Text (first part; "|" marks line breaks are not used, lines are shown as they appear):
<<<
{head}
>>>

Lines that might be speaker labels, with how often they occur:
{cands}

Return one JSON object:
{{"doc_kind": "transcript" (a call or meeting with several speakers and audience questions) | "remarks" (prepared remarks, a call script or a CFO commentary, without audience questions) | "letter" (a shareholder letter) | "release" (a press release or results announcement) | "slides" | "other",
 "event_type": "earnings" | "conference" | "investor_day" | "annual_meeting" | "other",
 "event_date": "YYYY-MM-DD" or null (the date the call or event took place, as stated in the text),
 "author": null or {{"name": ..., "role": ..., "firm": ...}} (for remarks with no speaker labels: the person delivering them, only if the text names them),
 "speakers": [{{"label": the exact text that starts each of this person's passages, copied from the text (for example "AMY HOOD:", "Lisa Gill:", "Albert White - The Cooper Companies, Inc. - President", "Q - Laurent Favre", "Operator"), "name": person's full name, "role": title or "Analyst", "firm": company or bank, "kind": "management" | "analyst" | "operator" | "other"}}]}}
Only include speakers whose label appears in the text above. Copy labels exactly, without the speech that follows them."""


def roster_from_llm(sb, ticker, company, title, pages, text_sha):
    doc = cp.Doc(pages)
    head = "\n".join(doc.text(i) for i in range(len(doc.lines)) if not doc.boiler[i])[:9000]
    cnt = collections.Counter()
    for i in range(len(doc.lines)):
        t = cp.strip_md(doc.text(i)).strip()
        if not t or doc.boiler[i]:
            continue
        m = re.match(r"^(.{3,70}?)(\^|:|\s[-–—]{1,2}\s|$)", t)
        if m and re.match(r"^[A-Z]", t) and len(m.group(1).split()) <= 8:
            lab = m.group(1) + (m.group(2) if m.group(2) in (":", "^") else "")
            if not re.search(r"[.?!,;]$", m.group(1)):
                cnt[lab.strip()] += 1
    cands = "\n".join(f"{n}x  {lab}" for lab, n in cnt.most_common(60) if n >= 2) or "(none recurring)"
    prompt = ROSTER_PROMPT.format(company=company, ticker=ticker, title=(title or "")[:200], head=head, cands=cands)
    res, meta, hit = cached_model(sb, "roster", {"v": ROSTER_VERSION, "sha": text_sha, "model": PRIMARY}, prompt, 4000, effort="none")
    return res, meta


# ---------------------------------------------------------------- quotes

_NORM_CH = {"’": "'", "‘": "'", "“": '"', "”": '"', "–": "-", "—": "-", "−": "-", " ": " ", " ": " ", "…": "..."}


def _norm_map(text):
    """Lowercased text with whitespace runs collapsed and typographic characters folded, plus an index map."""
    out, idx, prev_space = [], [], False
    for i, ch in enumerate(text):
        ch = _NORM_CH.get(ch, ch)
        if ch.isspace():
            if prev_space or not out:
                continue
            out.append(" ")
            idx.append(i)
            prev_space = True
            continue
        prev_space = False
        for c in ch.lower():
            out.append(c)
            idx.append(i)
    return "".join(out), idx


def _norm(s):
    return _norm_map(s)[0].strip(" \"'")


def locate(quote, turn):
    """(start, end) of a quote in turn['text'] (clean text), exact after normalisation, else the best matching run of
    at least 6 words covering 70% of the quote; None if the quote is not in the turn."""
    if not quote or not turn.get("text"):
        return None
    q = _norm(quote)
    parts = [p.strip(" .,") for p in re.split(r"\s*(?:\.\.\.|\[\.\.\.\]|…)\s*", q) if p.strip(" .,")]
    nt, idx = turn.get("_norm") or _norm_map(turn["text"])
    turn["_norm"] = (nt, idx)
    if len(parts) > 1:
        pos, spans = 0, []
        for p in parts:
            i = nt.find(p, pos)
            if i < 0:
                spans = None
                break
            spans.append((i, i + len(p)))
            pos = i + len(p)
        if spans:
            return idx[spans[0][0]], idx[spans[-1][1] - 1] + 1
    i = nt.find(q)
    if i >= 0 and q:
        return idx[i], idx[i + len(q) - 1] + 1
    qw, tw = q.split(), nt.split(" ")
    if len(qw) < 6:
        return None
    sm = difflib.SequenceMatcher(None, tw, qw, autojunk=False)
    m = sm.find_longest_match(0, len(tw), 0, len(qw))
    if m.size >= 6 and m.size >= 0.7 * len(qw):
        # character span of the matched words in the normalised turn text
        starts, p = [], 0
        for w in tw:
            starts.append(p)
            p += len(w) + 1
        a = starts[m.a]
        b = starts[m.a + m.size - 1] + len(tw[m.a + m.size - 1])
        return idx[a], idx[b - 1] + 1
    return None


def quote_ref(parsed, turn_no, quote, prefer_section=None):
    """Quote object {t, s, e, p, q} with source offsets, searching the given turn first, then every turn."""
    turns = parsed["turns"]
    order = []
    if isinstance(turn_no, int) and 0 <= turn_no < len(turns):
        order.append(turn_no)
    order += [i for i in range(len(turns)) if i not in order and (prefer_section is None or turns[i]["section"] == prefer_section)]
    order += [i for i in range(len(turns)) if i not in order]
    for i in order:
        t = turns[i]
        sp = locate(quote, t)
        if sp:
            a, b = sp
            s, e = t["cmap"][a], t["cmap"][b - 1] + 1
            return {"t": i, "s": s, "e": e, "p": parsed["doc"].page_of(s), "q": t["text"][a:b]}
    return None


# ---------------------------------------------------------------- per-document processing

def doc_dates(row, pages):
    head = "\n".join(pages)[:4000]
    filed = None
    m = re.match(r"^8-K (\d{4}-\d\d-\d\d)", row.get("title") or "")
    if m:
        filed = dt.date.fromisoformat(m.group(1))
    ref = filed or (dt.date.fromisoformat(row["published"]) if row.get("published") else None)
    fetched = dt.date.fromisoformat((row.get("fetched_at") or now_iso())[:10])
    best = None
    for _pos, d in cp.find_dates(head):
        if d > fetched + dt.timedelta(days=1) or d.year < 2000:
            continue
        if ref and not (-200 <= (d - ref).days <= 30):
            continue
        best = d
        break
    if best is None:
        best = ref
    if best is None and row.get("period_end"):
        best = dt.date.fromisoformat(row["period_end"]) + dt.timedelta(days=30)
    return best


def process_doc(sb, row, company, use_llm=True, log=print):
    """Parses one document. Returns (record, parsed) where record is the state entry; parsed is None when the
    document is not a call."""
    from .kpitools import text_of
    tx = text_of(sb, row)
    if not tx or tx.get("kind") != "pages":
        return {"verdict": None, "reason": "no page text"}, None
    pages = tx.get("pages") or []
    full = "\n".join(pages)
    sha = hashlib.sha256(full.encode()).hexdigest()
    name, disp = company.get("name"), company.get("display_name")
    parsed = cp.parse(pages, name, disp)
    kind, why = cp.classify(row, pages, parsed)
    method = "deterministic"
    q = parsed["quality"]
    callish = re.search(r"transcript|remarks|script|commentary|earnings call|conference call|webcast", f"{row.get('title')} {row.get('url')} {full[:3000]}", re.I) \
        and row.get("doc_type") in ("transcript", "remarks") or re.search(r"transcript|prepared remarks|earnings call|conference call", f"{row.get('title')} {row.get('url')}", re.I)
    weak = (kind is None and callish and q["words"] >= 1200) or (kind == "remarks" and (q["turns"] < 2 or why.startswith("written"))) \
        or (kind == "transcript" and (q["coverage"] < 0.75 or any(s["kind"] == "other" for s in parsed["speakers"])))
    roster = None
    if weak and use_llm:
        try:
            roster, _meta = roster_from_llm(sb, row["ticker"], disp or name, row.get("title"), pages, sha)
        except Exception as e:
            log("roster failed", row["id"], str(e)[:200])
            if kind is None:
                return {"verdict": None, "reason": "roster unavailable", "sha": sha, "retry": True}, None
        if roster:
            dk = roster.get("doc_kind")
            hints = [h for h in (roster.get("speakers") or []) if isinstance(h, dict) and h.get("label") and h.get("name")]
            if dk not in ("transcript", "remarks"):
                return {"verdict": None, "reason": f"model: {dk}", "sha": sha}, None
            if hints:
                p2 = cp.parse(pages, name, disp, roster_hint=hints)
                k2, w2 = cp.classify(row, pages, p2)
                if p2["quality"]["coverage"] >= q["coverage"] - 0.02 and len(p2["turns"]) >= len(parsed["turns"]) * 0.8 or kind is None:
                    parsed, kind, why, method = p2, k2 or dk, w2 if k2 else "model roster", "model_roster"
            if kind is None:
                kind, why = dk, "model: " + dk
    if kind is None:
        return {"verdict": None, "reason": why, "sha": sha}, None
    if roster and parsed["speakers"]:
        # roles and firms the model read from the introductions fill the gaps of label-only formats
        by = {cp.name_key(h.get("name") or ""): h for h in roster.get("speakers") or [] if isinstance(h, dict) and h.get("name")}
        for s in parsed["speakers"]:
            h = by.get(cp.name_key(s["name"]))
            if not h:
                continue
            s["role"] = s.get("role") or (str(h.get("role"))[:120] if h.get("role") else None)
            s["firm"] = s.get("firm") or (str(h.get("firm"))[:120] if h.get("firm") else None)
            if s["kind"] == "other" and h.get("kind") in ("management", "analyst"):
                s["kind"] = h["kind"]
        parsed["exchanges"] = cp.build_exchanges(parsed["turns"], parsed["speakers"])
    if not parsed["turns"]:
        # written remarks without labels: one passage per paragraph block, attributed to the author when named
        author = (roster or {}).get("author") or {}
        parsed = single_speaker(pages, author, disp or name)
        method = "single_speaker"
    head = "\n".join(pages)[:5000]
    etype = (roster or {}).get("event_type") if roster and roster.get("event_type") in ("earnings", "conference", "investor_day", "annual_meeting", "other") else cp.event_type(head, row.get("title"))
    d = None
    if roster and roster.get("event_date"):
        try:
            d = dt.date.fromisoformat(roster["event_date"][:10])
        except ValueError:
            d = None
    d = d or doc_dates(row, pages)
    rec = {"verdict": kind, "reason": why, "sha": sha, "method": method, "event_type": etype, "date": d.isoformat() if d else None,
           "has_qa": bool(parsed["exchanges"]), "turns": len(parsed["turns"]), "words": parsed["quality"]["words"], "coverage": parsed["quality"]["coverage"], "needs_roster": bool(weak and roster is None)}
    return rec, parsed


def single_speaker(pages, author, company):
    doc = cp.Doc(pages)
    n = len(doc.lines)
    text, cmap = doc.clean(0, n)
    speakers = [{"idx": 0, "name": (author or {}).get("name") or f"{company} (written remarks)", "role": (author or {}).get("role"),
                 "firm": (author or {}).get("firm") or company, "kind": "management"}]
    turns = []
    # split into blocks of roughly 250 words at paragraph boundaries so quotes and the browser stay readable
    pos = 0
    while pos < len(text):
        end = pos
        words = 0
        while end < len(text) and words < 250:
            nl = text.find("\n", end + 1)
            nl = len(text) if nl < 0 else nl
            words += len(text[end:nl].split())
            end = nl
        seg = text[pos:end]
        lead = len(seg) - len(seg.lstrip())
        a, b = pos + lead, pos + len(seg.rstrip())
        if b > a:
            turns.append({"idx": len(turns), "speaker": 0, "section": "prepared", "s": cmap[a], "e": cmap[b - 1] + 1, "page": doc.page_of(cmap[a]),
                          "words": len(text[a:b].split()), "text": text[a:b], "cmap": cmap[a:b]})
        pos = end + 1
    return {"speakers": speakers, "turns": turns, "exchanges": [], "doc": doc, "quality": {"turns": len(turns), "speakers": 1, "coverage": 1.0, "words": len(text.split()), "qa": False}}


# ---------------------------------------------------------------- analysis

ANALYSIS_PROMPT = """You analyse an investor call for an equity research terminal. Use only the transcript below. Every item you return must include a quote copied exactly from the numbered passage you cite: 8 to 40 consecutive words, character for character, no ellipses, no paraphrase, no words joined from different sentences.

Company: {company} ({ticker}), sector {sector}
Event: {etype} on {date}{label}
Document: {kind}{qa_note}

Theme taxonomy (use these ids only):
{taxonomy}

{previous}
Speakers:
{speakers}

Passages ([n] number, speaker, section):
{turns}

Q&A exchanges (exchange id: question passages -> answer passages):
{exchanges}

Return one JSON object:
{{"summary": 3 to 5 plain sentences on what management said and what analysts pushed on, no adjectives like "strong" unless quoted,
 "themes": up to 10 objects, most discussed first: {{"theme": taxonomy id, "label": 2 to 6 word specific description, "sentiment": number from -1 (clearly negative for the business) to 1 (clearly positive), "confidence": number from 0 (management sounded unsure, hedged) to 1 (management sounded certain), "summary": one sentence, "quotes": 1 to 3 objects {{"n": passage number, "quote": exact text}}}},
 "guidance": every forward-looking quantitative target management stated (revenue, EPS, margin, capex, growth rates, unit targets...): {{"metric": short name, "period": e.g. "Q4 FY26", "FY2026", "2027", "long term", "low": number or null, "high": number or null, "unit": "USD" | "USD_per_share" | "%" | "count" | "x" | other short unit, "basis": "new" | "raised" | "lowered" | "maintained" | "reiterated" | "unclear", "n": passage number, "quote": exact text containing the figure}} (values in base units: 4.2 billion dollars is 4200000000; percentages as printed, 12.5% is 12.5),
 "exchanges": one object per exchange id listed above: {{"id": exchange id, "topics": 1 to 3 taxonomy ids, "concern": one sentence naming what the analyst is worried about or probing, "q_n": passage number, "q_quote": exact text from the question, "answer": "direct" | "partial" | "deflected" (deflected: declined, changed the subject or answered a different question; partial: answered only part of it), "reason": null or one sentence on what was not answered, "a_n": passage number or null, "a_quote": exact text from the answer or null}},
 "changes": up to 6 notable changes versus the previous call listed above (only if a previous call is given; otherwise an empty list): {{"theme": taxonomy id, "change": one sentence comparing this call with the previous one, "n": passage number, "quote": exact text from this call}}}}
Return only JSON."""


def _turn_lines(parsed, budget=150000):
    lines, used = [], 0
    sp = parsed["speakers"]
    for t in parsed["turns"]:
        s = sp[t["speaker"]]
        txt = re.sub(r"\s+", " ", t["text"]).strip()
        if s["kind"] == "operator" and len(txt) > 600:
            txt = txt[:600] + " (operator instructions shortened)"
        ln = f"[{t['idx']}] {s['name']} ({s['kind']}) [{t['section']}]: {txt}"
        lines.append(ln)
        used += len(ln)
    if used > budget:
        # keep every Q&A passage and as much of the prepared remarks as fits
        scale = max(0.2, (budget - sum(len(x) for x, t in zip(lines, parsed["turns"]) if t["section"] == "qa")) / max(1, sum(len(x) for x, t in zip(lines, parsed["turns"]) if t["section"] != "qa")))
        out = []
        for x, t in zip(lines, parsed["turns"]):
            if t["section"] != "qa" and len(x) > 400:
                x = x[:max(400, int(len(x) * scale))] + " (passage shortened)"
            out.append(x)
        lines = out
    return "\n".join(lines)


def previous_context(prev):
    if not prev:
        return "Previous call: none available.\n"
    th = "; ".join(f"{t['theme']} ({t.get('label')}): sentiment {t.get('sentiment')}, {t.get('summary')}" for t in (prev.get("themes") or [])[:10])
    gd = "; ".join(f"{g['metric']} {g.get('period')}: {g.get('low')}-{g.get('high')} {g.get('unit')}" for g in (prev.get("guidance") or [])[:15])
    return f"Previous call ({prev.get('date')}{', ' + prev['label'] if prev.get('label') else ''}):\nSummary: {prev.get('summary')}\nThemes: {th or 'n/a'}\nGuidance: {gd or 'none stated'}\n"


def _num(v):
    try:
        if v is None or v == "":
            return None
        f = float(str(v).replace(",", ""))
        return f if f == f and abs(f) < 1e16 else None
    except (TypeError, ValueError):
        return None


def analyze(sb, call, parsed, company, prev):
    sp = parsed["speakers"]
    spl = "\n".join(f"{s['idx']}: {s['name']} - {s['kind']}{', ' + s['role'] if s.get('role') else ''}{', ' + s['firm'] if s.get('firm') else ''}" for s in sp)
    exl = "\n".join(f"E{e['idx']}: {e['q_turns']} -> {e['a_turns']}" for e in parsed["exchanges"]) or "(no Q&A section)"
    tax = "\n".join(f"{i}: {n} ({d})" for i, n, d in THEMES)
    prompt = ANALYSIS_PROMPT.format(company=company.get("display_name") or company.get("name"), ticker=call["ticker"], sector=company.get("sector") or "n/a",
                                    etype=call["event_type"].replace("_", " "), date=call.get("date") or "unknown date",
                                    label=f" ({call['label']})" if call.get("label") else "", kind=call["kind"],
                                    qa_note="" if parsed["exchanges"] else " (no Q&A in this document)", taxonomy=tax,
                                    previous=previous_context(prev), speakers=spl, turns=_turn_lines(parsed), exchanges=exl)
    payload = {"v": PROMPT_VERSION, "sha": call["sha"], "model": PRIMARY, "prev": hashlib.sha256(previous_context(prev).encode()).hexdigest()[:16],
               "spk": hashlib.sha256(spl.encode()).hexdigest()[:16], "ex": hashlib.sha256(exl.encode()).hexdigest()[:16]}
    res, meta, hit = cached_model(sb, "analysis", payload, prompt, 16000, effort="none")
    return validate(res, parsed, prev), meta, hit


def validate(res, parsed, prev):
    """Keeps only items whose quotes are found in the source; converts quotes to offsets."""
    dropped = 0
    out = {"summary": str(res.get("summary") or "").strip()[:1500], "themes": [], "guidance": [], "exchanges": [], "changes": []}
    for th in (res.get("themes") or [])[:12]:
        if not isinstance(th, dict) or th.get("theme") not in THEME_IDS:
            dropped += 1
            continue
        qs = []
        for q in (th.get("quotes") or [])[:3]:
            if isinstance(q, dict):
                r = quote_ref(parsed, q.get("n"), q.get("quote"))
                if r and not any(abs(r["s"] - x["s"]) < 5 for x in qs):
                    qs.append(r)
                elif not r:
                    dropped += 1
        if not qs:
            dropped += 1
            continue
        out["themes"].append({"theme": th["theme"], "label": str(th.get("label") or "")[:80], "sentiment": max(-1, min(1, _num(th.get("sentiment")) or 0)),
                              "confidence": max(0, min(1, _num(th.get("confidence")) if _num(th.get("confidence")) is not None else 0.5)),
                              "summary": str(th.get("summary") or "")[:400], "quotes": qs})
    for g in (res.get("guidance") or [])[:40]:
        if not isinstance(g, dict) or not g.get("metric"):
            continue
        r = quote_ref(parsed, g.get("n"), g.get("quote"))
        if not r:
            dropped += 1
            continue
        out["guidance"].append({"metric": str(g["metric"])[:80], "period": str(g.get("period") or "")[:40] or None, "low": _num(g.get("low")), "high": _num(g.get("high")),
                                "unit": str(g.get("unit") or "")[:20] or None, "basis": g.get("basis") if g.get("basis") in ("new", "raised", "lowered", "maintained", "reiterated", "unclear") else "unclear", "quote": r})
    exs = {e["idx"]: e for e in parsed["exchanges"]}
    for x in res.get("exchanges") or []:
        if not isinstance(x, dict):
            continue
        xid = x.get("id")
        if isinstance(xid, str):
            m = re.match(r"^E?(\d+)$", xid.strip())
            xid = int(m.group(1)) if m else None
        if xid not in exs:
            continue
        e = exs[xid]
        qq = quote_ref(parsed, x.get("q_n") if x.get("q_n") in e["q_turns"] else e["q_turns"][0], x.get("q_quote"))
        if qq and qq["t"] not in e["q_turns"]:
            qq = None
        aq = None
        if x.get("a_quote") and e["a_turns"]:
            aq = quote_ref(parsed, x.get("a_n") if x.get("a_n") in e["a_turns"] else e["a_turns"][0], x.get("a_quote"))
            if aq and aq["t"] not in e["a_turns"]:
                aq = None
        ans = x.get("answer") if x.get("answer") in ("direct", "partial", "deflected") else None
        if ans in ("partial", "deflected") and not aq:
            # a non-direct verdict needs the answer text it is based on
            dropped += 1
            ans = None
        if not qq:
            dropped += 1
        e["topics"] = [t for t in (x.get("topics") or []) if t in THEME_IDS][:3]
        e["concern"] = str(x.get("concern") or "")[:300] if qq else None
        e["concern_quote"] = qq
        e["answer"] = ans
        e["reason"] = str(x.get("reason") or "")[:300] if ans in ("partial", "deflected") else None
        e["answer_quote"] = aq
    if prev:
        for c in (res.get("changes") or [])[:8]:
            if not isinstance(c, dict) or c.get("theme") not in THEME_IDS or not c.get("change"):
                continue
            r = quote_ref(parsed, c.get("n"), c.get("quote"))
            if not r:
                dropped += 1
                continue
            out["changes"].append({"theme": c["theme"], "text": str(c["change"])[:400], "quote": r})
    out["dropped"] = dropped
    return out


# ---------------------------------------------------------------- speaker statistics

def speaker_stats(parsed):
    tot = sum(t["words"] for t in parsed["turns"] if parsed["speakers"][t["speaker"]]["kind"] != "operator") or 1
    acc = collections.defaultdict(lambda: {"turns": 0, "words": 0, "hedge": 0, "pos": 0, "neg": 0})
    for t in parsed["turns"]:
        a = acc[t["speaker"]]
        a["turns"] += 1
        a["words"] += t["words"]
        a["hedge"] += len(HEDGES.findall(t["text"]))
        a["pos"] += len(POSITIVE.findall(t["text"]))
        a["neg"] += len(NEGATIVE.findall(t["text"]))
    out = []
    for s in parsed["speakers"]:
        a = acc[s["idx"]]
        w = a["words"] or 1
        out.append({**{k: s.get(k) for k in ("idx", "name", "role", "firm", "kind")}, "turns": a["turns"], "words": a["words"],
                    "share": round(a["words"] / tot, 4) if s["kind"] != "operator" else None,
                    "hedge": round(a["hedge"] / w * 1000, 1) if a["words"] >= 50 else None,
                    "tone": round((a["pos"] - a["neg"]) / w * 1000, 1) if a["words"] >= 50 else None})
    return out


# ---------------------------------------------------------------- state, grouping, publishing

def load_state(sb):
    b = sb.get_object("raw", STATE_PATH + ".gz")
    if not b:
        return {"docs": {}, "calls": {}}
    return json.loads(gzip.decompress(b))


def save_state(sb, st):
    st["saved"] = now_iso()
    sb.put_object("raw", STATE_PATH + ".gz", gzip.compress(json.dumps(st, separators=(",", ":")).encode()), "application/gzip", cache="no-store")


DOC_COLS = "select=id,ticker,doc_type,title,period_end,fiscal_label,published,pages,bytes,text_path,url,ext,fetched_at,storage_path,sha256"


def candidate_docs(sb, tickers=None):
    tf = f"&ticker=in.({','.join(tickers)})" if tickers else ""
    a = sb.select("ir_documents", DOC_COLS + "&doc_type=in.(transcript,remarks)" + tf)
    b = sb.select("ir_documents", DOC_COLS + "&doc_type=not.in.(transcript,remarks)&or=(title.ilike.*transcript*,url.ilike.*transcript*,title.ilike.*earnings%20call*,"
                  "title.ilike.*conference%20call*,title.ilike.*prepared*,url.ilike.*prepared*,title.ilike.*remarks*,url.ilike.*remarks*,title.ilike.*script*,"
                  "url.ilike.*script*,title.ilike.*commentary*,url.ilike.*commentary*)" + tf)
    out = {}
    for d in a + b:
        if d.get("text_path"):
            out[d["id"]] = d
    return out


def call_id_of(ticker, date, doc_id):
    return f"{ticker}-{date or 'undated'}-{hashlib.sha1(doc_id.encode()).hexdigest()[:6]}"


def group_events(ticker, recs):
    """recs: [(doc_row, rec)] for documents that are calls. Returns events [{primary, alts}] with the most complete
    document first (Q&A, then transcript, then length)."""
    items = sorted([x for x in recs if x[1].get("date")], key=lambda x: x[1]["date"])
    groups = []
    for row, rec in items:
        d = dt.date.fromisoformat(rec["date"])
        g = next((g for g in groups if abs((d - g["d"]).days) <= 4 and (g["etype"] == rec["event_type"] or "earnings" in (g["etype"], rec["event_type"]))), None)
        if g is None:
            groups.append({"d": d, "etype": rec["event_type"], "members": [(row, rec)]})
        else:
            g["members"].append((row, rec))
            if rec["event_type"] == "earnings":
                g["etype"] = "earnings"
    out = []
    for g in groups:
        ms = sorted(g["members"], key=lambda x: (x[1].get("has_qa"), x[1]["verdict"] == "transcript", x[1].get("coverage", 0) >= 0.7, x[1].get("words", 0)), reverse=True)
        out.append({"primary": ms[0], "alts": [m[0]["id"] for m in ms[1:]], "event_type": g["etype"], "date": ms[0][1]["date"]})
    return out


def fiscal_label(company, etype, date):
    if etype != "earnings" or not date:
        return None, None
    from .ir import quarter_for_date
    try:
        label, e = quarter_for_date(company.get("fiscal_year_end") or "1231", dt.date.fromisoformat(date) - dt.timedelta(days=2))
        return label, e.isoformat() if e else None
    except Exception:
        return None, None


def doc_meta(row):
    return {k: row.get(k) for k in ("id", "title", "url", "ext", "text_path", "storage_path", "doc_type", "fiscal_label", "published")}


def turn_file(call_id, parsed):
    return {"call_id": call_id, "turns": [{"i": t["idx"], "sp": t["speaker"], "sec": t["section"], "s": t["s"], "e": t["e"], "p": t["page"], "w": t["words"], "x": t["text"]} for t in parsed["turns"]]}


def recent_cutoff(today=None):
    today = today or dt.date.today()
    return (today - dt.timedelta(days=int(RECENT_QUARTERS * 91.3) + 45)).isoformat()


def _companies(sb):
    return {r["ticker"]: r for r in sb.select("companies", "select=ticker,name,display_name,sector,fiscal_year_end")}


def scan(sb, docs, companies, state, workers=4, use_llm=True, log=print, time_budget=None):
    """Parses documents that are new or changed since the last scan and records the verdict in state. Returns the ids
    of documents that are calls."""
    t0 = time.time()
    todo = [d for d in docs.values() if state["docs"].get(d["id"], {}).get("fetched_at") != d.get("fetched_at") or state["docs"].get(d["id"], {}).get("pv") != PARSE_VERSION]
    # recent documents first (8-K exhibits carry their filing date in the title)
    def when(d):
        m = re.match(r"^8-K (\d{4}-\d\d-\d\d)", d.get("title") or "")
        return (m.group(1) if m else None) or d.get("published") or d.get("period_end") or (d.get("fetched_at") or "")[:10]
    todo.sort(key=when, reverse=True)
    parsed = set()

    def one(row):
        try:
            rec, p = process_doc(sb, row, companies.get(row["ticker"], {}), use_llm=use_llm, log=log)
        except Exception as e:
            log("parse failed", row["id"], str(e)[:200])
            return row, {"verdict": None, "reason": "error: " + str(e)[:200], "retry": True}, False
        return row, rec, p is not None
    n = 0
    with cf.ThreadPoolExecutor(workers) as ex:
        futs = []
        for row in todo:
            futs.append(ex.submit(one, row))
        for f in cf.as_completed(futs):
            row, rec, p = f.result()
            if not rec.get("retry"):
                rec["fetched_at"] = row.get("fetched_at")
                # documents parsed without the model roster they needed are parsed again on a later run
                rec["pv"] = PARSE_VERSION if not rec.get("needs_roster") else None
            rec["ticker"] = row["ticker"]
            state["docs"][row["id"]] = rec
            if p:
                parsed.add(row["id"])  # ids only: parsed documents are large, build_calls parses again when it analyses
            n += 1
            if n % 100 == 0:
                log(f"scanned {n}/{len(todo)}")
                save_state(sb, state)
            if time_budget and time.time() - t0 > time_budget:
                for x in futs:
                    x.cancel()
                break
    return parsed


def build_calls(sb, ticker, docs, companies, state, analyze_limit=None, deadline=None, log=print, workers=3, recent_only=False):
    """Groups a ticker's call documents into events, analyses calls whose inputs changed (oldest first so each call
    can be compared with the one before it) and writes calls/{T}.json plus one turn file per call."""
    from .kpitools import text_of
    company = companies.get(ticker, {})
    recs = [(docs[i], r) for i, r in state["docs"].items() if r.get("ticker") == ticker and r.get("verdict") and i in docs]
    events = group_events(ticker, recs)
    old = sb.get_json(f"calls/{ticker}.json") or {"calls": []}
    old_by = {c["call_id"]: c for c in old.get("calls", [])}
    cut = recent_cutoff()
    calls, prev_earn, analysed = [], None, 0
    for ev in events:
        row, rec = ev["primary"]
        cid = call_id_of(ticker, ev["date"], row["id"])
        label, pend = fiscal_label(company, ev["event_type"], ev["date"])
        base = {"call_id": cid, "ticker": ticker, "doc": row["id"], "alt_docs": ev["alts"], "kind": rec["verdict"], "event_type": ev["event_type"],
                "title": re.sub(r"\s+", " ", (row.get("title") or "").split("||")[0]).strip()[:200], "date": ev["date"], "label": label, "period_end": pend,
                "has_qa": rec.get("has_qa"), "words": rec.get("words"), "n_turns": rec.get("turns"), "parse_method": rec.get("method"), "sha": rec.get("sha"), "pv": PARSE_VERSION}
        prev_ctx = prev_earn if ev["event_type"] == "earnings" else None
        prev_key = (prev_ctx or {}).get("call_id")
        o = old_by.get(cid)
        fresh = o and o.get("sha") == rec.get("sha") and o.get("prev_call") == prev_key and o.get("analysis_v") == PROMPT_VERSION and o.get("pv") == PARSE_VERSION and o.get("themes") is not None
        want = (not recent_only or ev["date"] >= cut) and (analyze_limit is None or analysed < analyze_limit) and (deadline is None or time.time() < deadline)
        if fresh:
            c = {**o, **base}
        else:
            has_struct = o and o.get("pv") == PARSE_VERSION and o.get("sha") == rec.get("sha") and o.get("speakers") is not None
            p = None
            if want and not LLM_DOWN or not has_struct:
                _r, p = process_doc(sb, row, company, use_llm=not LLM_DOWN, log=log)
                if p is None:
                    continue
            if has_struct:
                c = {**o, **base}
            else:
                # parsed structure without the model's reading: speakers, statistics, exchanges and passage text
                c = {**base, "speakers": speaker_stats(p),
                     "exchanges": [{k: e.get(k) for k in ("idx", "analyst", "asked_by", "q_turns", "a_turns")} for e in p["exchanges"]]}
                sb.put_json(f"calls/{ticker}/{cid}.json", turn_file(cid, p), cache="max-age=3600")
            res = None
            if want and not LLM_DOWN:
                try:
                    res, meta, hit = analyze(sb, {**base, "label": label}, p, company, prev_ctx)
                except Exception as e:
                    log("analysis failed", cid, str(e)[:200])
            if res is not None:
                analysed += 0 if hit else 1
                c = {**base, "summary": res["summary"], "themes": res["themes"], "guidance": res["guidance"], "changes": res["changes"],
                     "exchanges": [{k: e.get(k) for k in ("idx", "analyst", "asked_by", "q_turns", "a_turns", "topics", "concern", "concern_quote", "answer", "reason", "answer_quote")} for e in p["exchanges"]],
                     "speakers": speaker_stats(p), "prev_call": prev_key, "analysis_v": PROMPT_VERSION, "model": meta.get("model"), "analyzed_at": meta.get("at"),
                     "dropped": res["dropped"]}
                sb.put_json(f"calls/{ticker}/{cid}.json", turn_file(cid, p), cache="max-age=3600")
            elif o and o.get("themes") is not None and o.get("pv") == PARSE_VERSION and o.get("sha") == rec.get("sha"):
                # keep the earlier analysis (only the previous-call comparison is out of date)
                c = {**o, **base, "pending": True}
            else:
                for k in ("summary", "themes", "guidance", "changes", "prev_call", "analysis_v", "model", "analyzed_at", "dropped"):
                    c.pop(k, None)
                c["pending"] = True
        calls.append(c)
        if ev["event_type"] == "earnings" and c.get("themes") is not None:
            prev_earn = {"call_id": cid, "date": ev["date"], "label": label, "summary": c.get("summary"), "themes": c.get("themes"), "guidance": c.get("guidance")}
    docs_used = {}
    for c in calls:
        for did in [c["doc"]] + (c.get("alt_docs") or []):
            if did in docs:
                docs_used[did] = doc_meta(docs[did])
    calls.sort(key=lambda c: c["date"] or "", reverse=True)
    bundle = {"ticker": ticker, "generated": now_iso(), "taxonomy": [{"id": i, "name": n, "desc": d} for i, n, d in THEMES], "docs": docs_used, "calls": calls}
    sb.put_json(f"calls/{ticker}.json", bundle, cache="max-age=600")
    return bundle, analysed


def _firm_key(f):
    f = re.sub(r",?\s*(research division|equity research|research|securities|llc|l\.l\.c\.|inc\.?|incorporated|ltd\.?|limited|plc|ag|s\.a\.|& co\.?|and company|co\.|corp\.?|group|capital markets|, the)\b", " ", (f or "").lower())
    f = re.sub(r"[^a-z0-9 ]", " ", f)
    f = re.sub(r"\s+", " ", f).strip()
    alias = {"jp morgan": "jpmorgan", "j p morgan": "jpmorgan", "jpmorgan chase": "jpmorgan", "bofa": "bank of america", "bofa global": "bank of america",
             "merrill lynch": "bank of america", "bank of america merrill lynch": "bank of america", "citigroup global markets": "citi", "citigroup": "citi",
             "morgan stanley &": "morgan stanley", "goldman sachs &": "goldman sachs", "keefe bruyette woods": "kbw", "keefe bruyette & woods": "kbw",
             "td cowen": "td cowen", "cowen and": "td cowen", "cowen": "td cowen", "wells fargo": "wells fargo", "ubs investment bank": "ubs", "barclays bank": "barclays"}
    return alias.get(f, f)


def publish_aggregates(sb, tickers, companies, log=print):
    """calls/themes.json, calls/themes/{theme}.json, calls/people.json and calls/coverage.json from every ticker bundle."""
    bundles = {}

    def get(t):
        return t, sb.get_json(f"calls/{t}.json")
    with cf.ThreadPoolExecutor(12) as ex:
        for t, b in ex.map(get, tickers):
            if b and b.get("calls"):
                bundles[t] = b
    sectors = sorted({(companies.get(t) or {}).get("sector") or "Other" for t in bundles})
    cells = collections.defaultdict(lambda: collections.defaultdict(lambda: collections.defaultdict(lambda: [0, 0.0])))
    mentions = collections.defaultdict(list)
    quarters = set()
    docs = {}
    execs, analysts, firms = {}, {}, {}
    coverage = []
    for t, b in bundles.items():
        sec = (companies.get(t) or {}).get("sector") or "Other"
        cs = b["calls"]
        coverage.append({"t": t, "sector": sec, "calls": len(cs), "earnings": sum(1 for c in cs if c["event_type"] == "earnings"),
                         "analysed": sum(1 for c in cs if c.get("themes") is not None), "qa": sum(1 for c in cs if c.get("has_qa")),
                         "first": min((c["date"] for c in cs if c.get("date")), default=None), "last": max((c["date"] for c in cs if c.get("date")), default=None),
                         "recent": sum(1 for c in cs if (c.get("date") or "") >= recent_cutoff())})
        for c in cs:
            if c.get("themes") is None or not c.get("date"):
                continue
            d = dt.date.fromisoformat(c["date"])
            qk = f"{d.year}Q{(d.month - 1) // 3 + 1}"
            quarters.add(qk)
            for i, th in enumerate(c["themes"]):
                cell = cells[th["theme"]][sec][qk]
                cell[0] += 1
                cell[1] += th["sentiment"]
                q0 = th["quotes"][0]
                mentions[th["theme"]].append({"t": t, "c": c["call_id"], "d": c["date"], "l": c.get("label"), "et": c["event_type"], "sec": sec, "q": qk,
                                              "lab": th["label"], "sen": th["sentiment"], "conf": th["confidence"], "sum": th["summary"],
                                              "quotes": th["quotes"], "doc": c["doc"]})
                docs[c["doc"]] = b["docs"].get(c["doc"])
            sp = c.get("speakers") or []
            for s in sp:
                if s["kind"] == "management" and s.get("words", 0) >= 150:
                    k = f"{t}|{cp.name_key(s['name'])}"
                    e = execs.setdefault(k, {"name": s["name"], "t": t, "role": s.get("role"), "calls": []})
                    if s.get("role"):
                        e["role"] = s["role"]
                    e["calls"].append({"c": c["call_id"], "i": s["idx"], "d": c["date"], "l": c.get("label"), "et": c["event_type"], "w": s["words"], "sh": s.get("share"), "h": s.get("hedge"), "tn": s.get("tone")})
            for x in c.get("exchanges") or []:
                if x.get("analyst") is None or x["analyst"] >= len(sp):
                    continue
                s = sp[x["analyst"]]
                if s["kind"] not in ("analyst", "other") or not s.get("name"):
                    continue
                k = cp.name_key(s["name"])
                a = analysts.setdefault(k, {"name": s["name"], "firm": s.get("firm"), "firms": collections.Counter(), "covers": collections.Counter(), "topics": collections.Counter(), "q": []})
                if s.get("firm"):
                    a["firms"][s["firm"]] += 1
                a["covers"][t] += 1
                for tp in x.get("topics") or []:
                    a["topics"][tp] += 1
                a["q"].append({"t": t, "c": c["call_id"], "d": c["date"], "x": x["idx"], "tp": x.get("topics") or [], "cn": x.get("concern"), "ans": x.get("answer")})
    for th in mentions:
        mentions[th].sort(key=lambda m: m["d"], reverse=True)
    qs = sorted(quarters)
    out_cells = {th: {sec: {q: [v[0], round(v[1] / v[0], 3)] for q, v in byq.items()} for sec, byq in bys.items()} for th, bys in cells.items()}
    calls_per = collections.defaultdict(lambda: collections.defaultdict(int))
    for t, b in bundles.items():
        sec = (companies.get(t) or {}).get("sector") or "Other"
        for c in b["calls"]:
            if c.get("themes") is not None and c.get("date"):
                d = dt.date.fromisoformat(c["date"])
                calls_per[sec][f"{d.year}Q{(d.month - 1) // 3 + 1}"] += 1
    sb.put_json("calls/themes.json", {"generated": now_iso(), "taxonomy": [{"id": i, "name": n, "desc": d} for i, n, d in THEMES], "quarters": qs, "sectors": sectors,
                                      "cells": out_cells, "calls": {s: dict(v) for s, v in calls_per.items()},
                                      "totals": {th: len(m) for th, m in mentions.items()}}, cache="max-age=600")
    for th in THEME_IDS:
        ms = mentions.get(th, [])
        sb.put_json(f"calls/themes/{th}.json", {"generated": now_iso(), "theme": th, "mentions": ms, "docs": {m["doc"]: docs.get(m["doc"]) for m in ms}}, cache="max-age=600")
    fl = {}
    for k, a in analysts.items():
        if a["firms"]:
            a["firm"] = a["firms"].most_common(1)[0][0]
        fk = _firm_key(a["firm"])
        if fk:
            f = fl.setdefault(fk, {"firm": a["firm"], "analysts": [], "covers": collections.Counter(), "topics": collections.Counter(), "n": 0})
            f["analysts"].append(a["name"])
            f["covers"].update(a["covers"])
            f["topics"].update(a["topics"])
            f["n"] += len(a["q"])
    people = {"generated": now_iso(),
              "executives": sorted([{**e, "calls": sorted(e["calls"], key=lambda x: x["d"])} for e in execs.values()], key=lambda e: (e["t"], -len(e["calls"]))),
              "analysts": sorted([{"name": a["name"], "firm": a["firm"], "covers": dict(a["covers"].most_common()), "topics": dict(a["topics"].most_common()), "n": len(a["q"]),
                                   "q": sorted(a["q"], key=lambda x: x["d"], reverse=True)[:60]} for a in analysts.values()], key=lambda a: -a["n"]),
              "firms": sorted([{"firm": f["firm"], "analysts": sorted(set(f["analysts"])), "covers": dict(f["covers"].most_common()), "topics": dict(f["topics"].most_common()), "n": f["n"]} for f in fl.values()], key=lambda f: -f["n"])}
    sb.put_json("calls/people.json", people, cache="max-age=600")
    sb.put_json("calls/coverage.json", {"generated": now_iso(), "recent_from": recent_cutoff(), "companies": sorted(coverage, key=lambda c: c["t"])}, cache="max-age=600")
    return {"tickers": len(bundles), "theme_mentions": sum(len(m) for m in mentions.values()), "executives": len(execs), "analysts": len(analysts), "firms": len(fl)}


# ---------------------------------------------------------------- database rows

def _tables_ready(sb):
    try:
        sb.select("calls", "select=call_id&limit=1")
        return True
    except HTTPError as e:
        if e.code in (404, 400) or "PGRST205" in str(e) or "does not exist" in str(e):
            return False
        raise


def db_sync(sb, tickers, log=print):
    """Copies published bundles into calls, call_speakers, call_turns, call_exchanges and call_items. Skips (and says
    so) when the tables have not been created yet; the schema lives in db/schema.sql and is applied on deploy."""
    if not _tables_ready(sb):
        log("calls tables not found; run scripts/migrate.mjs (db/schema.sql) first")
        return {"skipped": "tables missing"}
    n = collections.Counter()
    for t in tickers:
        b = sb.get_json(f"calls/{t}.json")
        if not b:
            continue
        rows, sps, trs, exs, its = [], [], [], [], []
        for c in b["calls"]:
            if c.get("themes") is None:
                continue
            cid = c["call_id"]
            d = b["docs"].get(c["doc"]) or {}
            sp = c.get("speakers") or []
            rows.append({"call_id": cid, "ticker": t, "doc_id": c["doc"], "alt_doc_ids": c.get("alt_docs") or [], "kind": c["kind"], "event_type": c["event_type"],
                         "title": c.get("title"), "event_date": c.get("date"), "fiscal_label": c.get("label"), "period_end": c.get("period_end"), "has_qa": bool(c.get("has_qa")),
                         "words": c.get("words"), "n_turns": c.get("n_turns"), "n_exchanges": len(c.get("exchanges") or []), "parse_method": c.get("parse_method"),
                         "text_sha256": c.get("sha"), "summary": c.get("summary"), "overall_tone": None, "analysis_model": c.get("model"), "analyzed_at": c.get("analyzed_at"),
                         "source_url": d.get("url"), "updated_at": now_iso()})
            for s in sp:
                sps.append({"call_id": cid, "idx": s["idx"], "name": s["name"], "role": s.get("role"), "firm": s.get("firm"), "kind": s["kind"], "turns": s.get("turns"),
                            "words": s.get("words"), "talk_share": s.get("share"), "hedge_per_1k": s.get("hedge"), "tone": s.get("tone")})
            tf = sb.get_json(f"calls/{t}/{cid}.json") or {"turns": []}
            for x in tf["turns"]:
                trs.append({"call_id": cid, "idx": x["i"], "speaker_idx": x["sp"], "section": x["sec"], "s": x["s"], "e": x["e"], "page": x["p"], "words": x["w"], "text": x["x"].replace("\x00", " ")})
            for x in c.get("exchanges") or []:
                exs.append({"call_id": cid, "idx": x["idx"], "analyst_idx": x.get("analyst"), "q_turns": x.get("q_turns"), "a_turns": x.get("a_turns"), "topics": x.get("topics") or [],
                            "concern": x.get("concern"), "concern_quote": x.get("concern_quote"), "deflected": x.get("answer") in ("partial", "deflected") if x.get("answer") else None,
                            "deflection_reason": x.get("reason"), "deflection_quote": x.get("answer_quote") if x.get("answer") in ("partial", "deflected") else None})
            for i, th in enumerate(c.get("themes") or []):
                its.append({"call_id": cid, "kind": "theme", "idx": i, "theme": th["theme"], "sentiment": th["sentiment"], "confidence": th["confidence"], "metric": None, "period": None,
                            "value_low": None, "value_high": None, "unit": None, "text": f"{th['label']}: {th['summary']}", "quote": th["quotes"][0]})
            for i, g in enumerate(c.get("guidance") or []):
                its.append({"call_id": cid, "kind": "guidance", "idx": i, "theme": "guidance", "sentiment": None, "confidence": None, "metric": g["metric"], "period": g.get("period"),
                            "value_low": g.get("low"), "value_high": g.get("high"), "unit": g.get("unit"), "text": g.get("basis"), "quote": g["quote"]})
            for i, ch in enumerate(c.get("changes") or []):
                its.append({"call_id": cid, "kind": "change", "idx": i, "theme": ch["theme"], "sentiment": None, "confidence": None, "metric": None, "period": None,
                            "value_low": None, "value_high": None, "unit": None, "text": ch["text"], "quote": ch["quote"]})
        if not rows:
            continue
        sb.upsert("calls", rows, "call_id")
        for tbl, rs, key in (("call_speakers", sps, "call_id,idx"), ("call_turns", trs, "call_id,idx"), ("call_exchanges", exs, "call_id,idx"), ("call_items", its, "call_id,kind,idx")):
            if rs:
                sb.upsert(tbl, rs, key, batch=500 if tbl != "call_turns" else 200)
            n[tbl] += len(rs)
        n["calls"] += len(rows)
    return dict(n)


# ---------------------------------------------------------------- job

def calls_job(sb, tickers=None, step="run", workers=4, time_budget=None, analyze_limit=None, recent_only=False, log=print):
    """Incremental run: scan new/changed documents, analyse calls (recent quarters first when recent_only), publish
    the ticker bundles that changed, then the cross-company files, then copy rows into the database when it has the
    tables. Resumable: state is saved after the scan and after every ticker."""
    t0 = time.time()
    deadline = t0 + time_budget if time_budget else None
    companies = _companies(sb)
    state = load_state(sb)
    if step == "publish":
        return publish_aggregates(sb, sorted({r.get("ticker") for r in state["docs"].values() if r.get("verdict")}), companies, log=log)
    if step == "db":
        return db_sync(sb, tickers or sorted({r.get("ticker") for r in state["docs"].values() if r.get("verdict")}), log=log)
    docs = candidate_docs(sb, tickers)
    parsed = scan(sb, docs, companies, state, workers=workers, log=log, time_budget=(time_budget * 0.4 if time_budget else None))
    save_state(sb, state)
    stats = {"docs": len(docs), "scanned": len(parsed), "calls_docs": sum(1 for r in state["docs"].values() if r.get("verdict"))}
    tks = sorted({r["ticker"] for i, r in state["docs"].items() if r.get("verdict") and i in docs and (not tickers or r["ticker"] in tickers)})
    changed = {docs[i]["ticker"] for i in parsed}
    tstate = state.setdefault("tickers", {})
    # skip companies with nothing new and nothing pending; companies with new documents go first
    work = [t for t in tks if t in changed or tstate.get(t, {}).get("pending", 1) or tstate.get(t, {}).get("pv") != PARSE_VERSION or tickers]
    work.sort(key=lambda t: (t not in changed, t))
    analysed, done = 0, set()

    def one(t, recent):
        b, n = build_calls(sb, t, docs, companies, state, analyze_limit=analyze_limit, deadline=deadline, log=log, recent_only=recent)
        return t, b, n
    # recent quarters for every company first, then older calls (unless recent_only)
    for recent in ((True,) if recent_only else (True, False)):
        if deadline and time.time() > deadline:
            break
        with cf.ThreadPoolExecutor(max(1, min(workers, 6))) as ex:
            for t, b, n in ex.map(lambda t: one(t, recent), work):
                analysed += n
                done.add(t)
                pend = sum(1 for c in b["calls"] if c.get("pending"))
                tstate[t] = {"pending": pend, "calls": len(b["calls"]), "pv": PARSE_VERSION, "at": now_iso()}
                log(f"{t}: {len(b['calls'])} calls, {n} analysed now, {pend} pending")
        save_state(sb, state)
        work = [t for t in work if tstate.get(t, {}).get("pending")]
    stats.update({"tickers": len(done), "analysed": analysed, "pending": sum(v.get("pending", 0) for v in tstate.values())})
    if done or not (deadline and time.time() > deadline):
        stats["aggregates"] = publish_aggregates(sb, sorted({r.get("ticker") for r in state["docs"].values() if r.get("verdict")}), companies, log=log)
    if done and (not deadline or time.time() < deadline):
        stats["db"] = db_sync(sb, sorted(done), log=log)
    stats["seconds"] = round(time.time() - t0, 1)
    return stats
