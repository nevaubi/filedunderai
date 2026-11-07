"""Written summaries: company narratives, release highlights, news one-liners and the macro brief."""
import json
import os
import re
import time

from .common import now_iso
from .fundamentals import series, fy_label

PRIMARY = os.environ.get("FU_SUMMARY_MODEL", "deepseek-v4p1-flash")
FALLBACK = os.environ.get("FU_SUMMARY_FALLBACK", "kimi-k3p1-pro")


def complete_json(prompt, max_tokens=9000):
    from .llm import chat_json
    t0 = time.time()
    j, _, _ = chat_json([PRIMARY, FALLBACK], [{"role": "user", "content": prompt}], max_tokens=max_tokens, temperature=0.3)
    j["generated_at"] = now_iso()
    j["seconds"] = round(time.time() - t0, 1)
    return j


def digest(statements, fye):
    rev = series(statements, "Revenue")[-6:]
    gp = {x["end"]: x["v"] for x in series(statements, "Gross profit")}
    op = {x["end"]: x["v"] for x in series(statements, "Operating income")}
    ni = {x["end"]: x["v"] for x in series(statements, "Net income")}
    lines = []
    for x in rev:
        e, v = x["end"], x["v"]
        s = f"{fy_label(fye, e, 'Q')} (ended {e}): revenue ${v / 1e9:.2f}B"
        if gp.get(e) and v:
            s += f", gross margin {gp[e] / v * 100:.1f}%"
        if op.get(e) and v:
            s += f", operating margin {op[e] / v * 100:.1f}%"
        if ni.get(e) is not None:
            s += f", net income ${ni[e] / 1e9:.2f}B"
        lines.append(s)
    return "\n".join(lines) or "n/a"


PROMPT = """You are an equity research analyst writing for a professional research terminal. Use only the material below.
Company: {name} ({tk})
Reported quarterly figures (from SEC filings):
{digest}

Latest earnings release excerpt:
{release}

Recent news (numbered):
{news}

Return one JSON object with keys:
"narratives": array of 3 or 4 objects {{"title": <=8 words, "summary": 1-2 plain sentences (<=45 words) in your own words, "stance": "bull"|"bear"|"neutral", "news": [news numbers that support it], "metrics": [metric names from: Revenue, Gross margin, Operating margin, Net income, Free cash flow, Revenue growth YoY]}}
"release_highlights": array of up to 5 short bullet strings summarizing the earnings release in your own words with the key numbers (empty array if no release)
"news_lines": object mapping each news number (as string) to a one-line neutral summary (<=18 words, your own words)
No quotes from articles longer than 5 words. No investment advice. Write plainly."""


def company(name, tk, statements, fye, release_md, news):
    nl = "\n".join(f"[{i + 1}] {x['title']} ({(x.get('date') or '')[:16]}): {x['snippet'][:400]}" for i, x in enumerate(news[:10]))
    return complete_json(PROMPT.format(name=name, tk=tk, digest=digest(statements, fye), release=(release_md or "")[:5000] or "n/a", news=nl or "n/a"))


def macro_brief(curve_row, curve_hdr, effr_tail, cpi_tail, news):
    nums = f"Treasury curve {curve_row[0]}: " + ", ".join(f"{h} {v}%" for h, v in zip(curve_hdr, curve_row[1:]))
    nums += f"\nEffective fed funds, recent months: {effr_tail}\nCPI index, recent months: {cpi_tail}"
    nl = "\n".join(f"[{i + 1}] {x['title']} ({(x.get('date') or '')[:16]}): {x['snippet'][:350]}" for i, x in enumerate(news[:16]))
    prompt = f"""You write the macro brief for an equity research terminal. Use only this material.
{nums}

News:
{nl}

Return JSON: {{"headline": <=12 words, "bullets": 4 strings (<=35 words each, your own words, cite news as [n]), "watch": 3 short strings naming upcoming catalysts mentioned in the news}}. No investment advice."""
    return complete_json(prompt, max_tokens=6000)
