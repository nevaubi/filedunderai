"""Model calls for the supply-chain cron (Fireworks, OpenAI-compatible chat completions with JSON output).
Nothing here is trusted: links are kept only when their quote is verbatim in the filing, tickers are re-resolved,
and insight claims are kept only when every cited id exists and every number in the claim appears in a cited record."""
import json
import os
import re
import time

from .common import request
from .supply_core import LINK_TYPES, RELATIONS, norm, quote_ok

URL = "https://api.fireworks.ai/inference/v1/chat/completions"
MODELS = [os.environ.get("FU_SUPPLY_MODEL", "kimi-k3"), os.environ.get("FU_SUPPLY_FALLBACK", "deepseek-v4p1-flash")]

LINK_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["links"],
    "properties": {"links": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["counterparty", "counterparty_ticker", "relation", "detail", "share_pct", "amount_usd", "single_source", "regions", "risk", "product", "link_type", "quote"],
        "properties": {
            "counterparty": {"type": "string"}, "counterparty_ticker": {"type": "string"},
            "relation": {"type": "string", "enum": list(RELATIONS)}, "detail": {"type": "string"},
            "share_pct": {"type": ["number", "null"]}, "amount_usd": {"type": ["number", "null"]}, "single_source": {"type": "boolean"},
            "regions": {"type": "array", "items": {"type": "string"}}, "risk": {"type": "string"}, "product": {"type": "string"},
            "link_type": {"type": "string", "enum": list(LINK_TYPES)}, "quote": {"type": "string"}}}}},
}

INSTRUCTIONS = """You read excerpts of a company's 10-K or 10-Q and list the specific, NAMED organisations it does business with.
relation = what the counterparty is to the filer: customer (buys from the filer), supplier (sells to or manufactures for the filer), partner (alliance, joint venture, licensing, co-development, distribution), competitor (named as a competitor).
Rules:
- Only organisations named in the text. Never infer from general knowledge. Skip "a customer", "Customer A", "certain suppliers".
- quote: 8 to 40 consecutive words copied character-for-character from the text; it must name the counterparty or its role. No ellipses, no edits.
- share_pct only when the text states a percentage for that counterparty; amount_usd only when a dollar figure is stated for it (convert "$2.1 billion" to 2100000000).
- single_source only when the text says sole, single or limited source, or a stated dependency on that counterparty.
- regions: countries or regions the text ties to this link. risk: the stated dependency or concentration risk in at most 15 words, empty if none.
- counterparty_ticker: the US listing ticker only if you are certain from the list of known tickers; otherwise empty.
- detail: at most 20 words on what flows between them. product: at most 6 words, empty if not stated. link_type: the most specific kind the text supports.
- Skip executive biographies, litigation, peer-group lists for stock performance, and deals between other companies. Up to 60 links, one per counterparty and relation.
Return only JSON matching this schema: """ + json.dumps(LINK_SCHEMA)


def _post(messages, max_tokens, deadline=None, schema_name=None):
    last = None
    for model in MODELS:
        remaining = None if deadline is None else deadline - time.time()
        if remaining is not None and remaining <= 5:
            raise TimeoutError("supply model budget exhausted") from last
        try:
            d = json.loads(request(URL, method="POST", data={
                "model": f"accounts/fireworks/models/{model}", "messages": messages, "max_tokens": max_tokens, "temperature": 0,
                "response_format": {"type": "json_object"}},
                headers={"Authorization": f"Bearer {os.environ['FIREWORKS_API_KEY']}"},
                timeout=min(240, remaining) if remaining else 240, retries=1))
            txt = re.sub(r"(?s)<think>.*?</think>", "", d["choices"][0]["message"]["content"] or "").strip()
            m = re.search(r"(?s)\{.*\}", txt)
            return json.loads(m.group(0) if m else txt), model
        except Exception as e:
            last = e
            print("supply model failed", model, str(e)[:160])
    raise last


def extract_links(filer, name, form, excerpt, known, deadline=None, post=_post):
    """Model proposals for one filing. `known` is "TICKER=Name, ..." for the covered universe."""
    msgs = [{"role": "system", "content": INSTRUCTIONS},
            {"role": "user", "content": f"Filer: {name} ({filer}). Known tickers: {known}\n\n{form} excerpts:\n{excerpt}"}]
    data, model = post(msgs, 12000, deadline)
    return data.get("links") or [], model


def verify_links(proposals, text_norm):
    """Keep only proposals whose quote is verbatim in the filing and whose fields have the right types."""
    kept, dropped = [], 0
    for p in proposals:
        q = (p.get("quote") or "").strip()
        if p.get("relation") not in RELATIONS or not p.get("counterparty") or not quote_ok(q, text_norm):
            dropped += 1
            continue
        kept.append(p)
    return kept, dropped


INSIGHT_SCHEMA = {"type": "object", "required": ["headline", "insights", "watch"], "properties": {
    "headline": {"type": "string"},
    "insights": {"type": "array", "items": {"type": "object", "required": ["kind", "text", "refs"], "properties": {
        "kind": {"type": "string", "enum": ["dependency", "concentration", "geography", "change", "policy", "corroboration", "second_order", "opportunity"]},
        "text": {"type": "string"}, "refs": {"type": "array", "items": {"type": "string"}}}}},
    "watch": {"type": "array", "items": {"type": "string"}}}}


def insights(ticker, name, records, deadline=None, post=_post):
    """Cross-source analyst insights. `records` is [{id, kind, text}] built from links (both directions), prior-year links,
    agreements, filing policy statements, Federal Register actions and news sentences."""
    lines = "\n".join(f"{r['id']} | {r['kind']} | {r['text']}" for r in records)[:60000]
    msgs = [{"role": "system", "content": f"""You are a supply-chain analyst. Using ONLY the numbered records (id | kind | text), write cross-source insights on {name} ({ticker}).
Prefer insights that connect two or more records: a dependency stated in one filing and confirmed by the counterparty's filing, a single supplier shared by several companies, a stated share that moved between annual reports, a country tie that coincides with a U.S. export-control, tariff or sanctions action, or contract terms behind a relationship.
headline: one sentence. insights: 4 to 8 items of at most 45 words; refs = the record ids each rests on (at least one, two or more when it is a cross-source claim). Never write ids in the text. Use only numbers present in the cited records. watch: 2 to 4 short items to monitor.
Return only JSON matching: {json.dumps(INSIGHT_SCHEMA)}"""}, {"role": "user", "content": lines}]
    data, model = post(msgs, 6000, deadline)
    return data, model


def _numbers(s):
    return {n.replace(",", "") for n in re.findall(r"\d[\d,]*(?:\.\d+)?", s or "")}


def validate_insights(data, records):
    """Drop claims without valid refs or with numbers that no cited record contains; mark the rest grounded."""
    by = {r["id"]: r for r in records}
    out = []
    for it in data.get("insights") or []:
        refs = [r for r in it.get("refs") or [] if r in by]
        text = re.sub(r"\s*\[?\(?\b(?:L|P|A|N|F|C)[0-9a-f]{3,12}\b\)?\]?", "", it.get("text") or "").strip()
        if not refs or not text:
            continue
        cited = set().union(*(_numbers(by[r]["text"]) for r in refs))
        missing = [n for n in _numbers(text) if n not in cited and not re.fullmatch(r"(19|20)\d{2}", n)]
        out.append({"kind": it.get("kind"), "text": text, "refs": refs, "status": "grounded" if not missing else "unverified", "unverified_numbers": missing})
    return {"headline": (data.get("headline") or "").strip(), "insights": out, "watch": [w for w in data.get("watch") or [] if isinstance(w, str)][:4]}
