"""Headless research agent for scheduled tasks and monitor write-ups (no browser, no user on the other end).

A JSON tool loop over Fireworks models (etl.llm.chat_json): each step the model returns
    {"thought": "...", "calls": [{"tool": "company", "args": {"ticker": "NVDA"}}, ...]}
or  {"thought": "...", "final": {"title", "summary", "report_md", "tickers"}}
Tools read the published Filed Under data (etl.agents.data), the web (Tavily search, Firecrawl read) and collect
deliverables: canvas views (same spec format as the app's assistant canvas, validated again by the app) and files.

Rules the prompt enforces and the code backs up: every number comes from a tool output; SEC facts are cited as
filing:TICKER:ACCESSION links and web facts as URLs; the run stops at its step, time and cost budget and then must
write the best answer it can, saying what is missing.
"""
import csv
import io
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor

from ..common import request
from .. import llm
from .data import Data

MODELS = ["kimi-k3p1-pro", "deepseek-v4p1-flash"]
# USD per million tokens (input, output), for the run's cost estimate only.
PRICE = {"kimi-k3p1-pro": (0.6, 2.5), "deepseek-v4p1-flash": (0.27, 1.1)}
OUT_MAX = 6000
VIEW_TYPES = {"kpis", "chart", "hbar", "scatter", "histogram", "heatmap", "waterfall", "sparktable", "candles", "treemap", "donut",
              "multiples", "timeline", "cards", "mermaid", "graph", "table", "note", "dashboard"}

TOOLS = {
    "company": "company(ticker): one-screen snapshot — valuation and own-history percentiles, growth, margins, returns, technical state, estimates, insider 90d summary",
    "financials": "financials(ticker, metrics=[label words], periods=8, annual=false): SEC statement lines with values, period ends and accession numbers",
    "insiders": "insiders(ticker, days=90): Form 4 open-market buys/sells with owner, role, 10b5-1 flag, value, accession",
    "filing_changes": "filing_changes(ticker): latest 10-K/10-Q vs prior comparable filing — top changed passages with section, score and risk tags",
    "estimates": "estimates(ticker): consensus by period, revisions, last surprises, price targets, ratings",
    "kpis": "kpis(ticker, query=None): company-reported KPIs from IR releases/decks (segments, units, customers, backlog...) with history, YoY and source quotes",
    "ir_documents": "ir_documents(ticker, limit=15, doc_type=None): investor-relations documents (releases, decks, transcripts, supplements)",
    "ir_read": "ir_read(ticker, doc_id, query=None): text of one IR document (optionally only passages around a phrase)",
    "alt_data": "alt_data(ticker): federal contract obligations by fiscal year, short-volume ratio, lobbying, fails-to-deliver",
    "gov": "gov(ticker): congressional trades, hearings mentioning the company, federal rules, lobbying",
    "supply": "supply(ticker): disclosed customers, suppliers, partners, single-source dependencies, revenue shares, regions, with verbatim quotes",
    "technicals": "technicals(ticker): trend, momentum and volatility state plus recent signals, each with its historical 60-day base rate",
    "regulatory": "regulatory(ticker): Health Care only — ClinicalTrials.gov trials (active by phase, upcoming primary completions, terminations with reasons), FDA approvals, recalls, 510(k) clearances",
    "proposed_sales": "proposed_sales(ticker): Form 144 notices of proposed insider sales (seller, value, expected date, 10b5-1 plan), each marked executed/open by matching Form 4 sales",
    "sec_review": "sec_review(ticker): SEC staff comment letters and company responses by reviewed filing, with topics and the comments' opening sentences",
    "calendar": "calendar(days=14, kinds=['earnings','ex_dividend','macro','ipo','trial_readout','proposed_sale'], tickers=None): upcoming dated events",
    "market": "market(): index/commodity/rates moves, breadth (share above 50/200-day averages, new highs/lows), macro brief",
    "screen": "screen(filters=[{field, op, value}], sort={field, desc}, limit=25, fields=[...]): filter all ~1,844 covered companies. ops: > >= < <= = != in. Useful fields: mcap, pe, pe_p5 (0=cheapest vs own 5y), fcf_yield, fcf_yield_p5, rev_g_q, rev_accel, eps_g_q, gm, om, roic, nd_ebitda, altman_z, pio_f, ret_1m, ret_3m, ret_ytd, dist_hi52, dist_lo52, rsi14, beta, ins_net_90d, ins_buyers_90d, next_earn_days, sector",
    "feed": "feed(name, ticker=None): published feeds: 'changes' (what changed), 'situations', 'earnings_preview', 'earnings_score', 'earnings_season', 'themes', 'kpi_insights' (may not exist yet)",
    "web_search": "web_search(query): web search (Tavily); returns titles, URLs and snippets",
    "read_url": "read_url(url): read one web page or PDF as text (Firecrawl)",
    "render_view": "render_view(spec): add a dashboard view to the deliverable. spec types: kpis{items:[{label,value,unit,delta,note}]}, chart{chart:line|bar|area|combo|stacked, x, series:[...], data:[{x..., series...}], unit}, hbar{items:[{label,value}]}, table{columns:[...], rows:[[...]]}, timeline{events:[{date,label,detail,tone}]}, note{markdown}, dashboard{sections:[{heading, columns, items:[leaf specs]}]}. Always set title, subtitle and source.",
    "create_file": "create_file(format=csv|md|json|xlsx, name, columns=[...], rows=[[...]], content=None): attach a file to the deliverable",
}

FEEDS = {"changes": "changes/feed.json", "situations": "situations/index.json", "earnings_season": "earnings/season.json",
         "themes": "themes/index.json", "kpi_insights": "kpi_insights/index.json"}
FEEDS_T = {"changes": "changes/{t}.json", "situations": "situations/{t}.json", "earnings_preview": "earnings/preview/{t}.json",
           "earnings_score": "earnings/score/{t}.json", "kpi_insights": "kpi_insights/{t}.json"}


def system_prompt(today, deliverable, deep):
    tools = "\n".join(f"- {v}" for v in TOOLS.values())
    return f"""You are Filed Under's research agent, running a scheduled task for a user who is not watching. Today is {today}.
You produce one deliverable: a sourced research note{' with a dashboard of views' if deliverable in ('dashboard', 'report') else ''}{' plus a file' if deliverable in ('csv', 'xlsx') else ''}.

Tools (call several in one step when independent):
{tools}

Reply with ONE JSON object per step, nothing else:
  {{"thought": "one or two sentences: what you need next and why", "calls": [{{"tool": "name", "args": {{...}}}}]}}
or, when you have enough:
  {{"thought": "...", "final": {{"title": "short title", "summary": "2-3 sentence answer a busy reader can act on", "report_md": "the full note in Markdown", "tickers": ["..."]}}}}

Rules:
- Use only numbers that appear in tool outputs; give each its period or date. If data is missing, say so plainly.
- Cite SEC facts as Markdown links [10-Q Nov 2026](filing:TICKER:ACCESSION) using accession numbers from tool outputs; cite web and IR pages with their URLs.
- Lead with what changed or what matters, then evidence. Prefer specific, non-obvious findings over generic commentary. No hype, no investment advice.
- When a technical signal is mentioned, state its base rate from the tool output (and that it is a past frequency, not a forecast).
- {'Build a small dashboard with render_view (KPIs first, then a chart or table) before finishing; refer to views by title in the note.' if deliverable in ('dashboard', 'report') else 'Keep the note tight.'}
- {'Attach the main table with create_file.' if deliverable in ('csv', 'xlsx') else ''}
- Never put JSON, tool names or view specs in report_md.
- {'Work thoroughly: check the main claims against a second source.' if deep else 'Be efficient: a few targeted steps.'}"""


def _clip(obj):
    s = json.dumps(obj, default=str, separators=(",", ":"))
    return s if len(s) <= OUT_MAX else s[:OUT_MAX] + "…[truncated]"


class Run:
    def __init__(self, sb, data=None, post=None, web_fetch=None):
        self.sb = sb
        self.data = data or Data(sb)
        self.views, self.files, self.sources = [], [], []
        self.post = post            # tests: replaces the model call
        self.web_fetch = web_fetch  # tests: replaces Tavily/Firecrawl
        self.cost = 0.0
        self.tokens = 0
        self.steps = []

    # ---- tools
    def tool(self, name, args):
        d = self.data
        a = args or {}
        if name == "company":
            return d.company(a["ticker"])
        if name == "financials":
            return d.financials(a["ticker"], a.get("metrics"), a.get("periods", 8), a.get("annual", False))
        if name == "insiders":
            return d.insiders(a["ticker"], a.get("days", 90))
        if name == "filing_changes":
            return d.filing_changes(a["ticker"])
        if name == "estimates":
            return d.estimates(a["ticker"])
        if name == "kpis":
            return d.kpis(a["ticker"], a.get("query"))
        if name == "ir_documents":
            return d.ir_documents(a["ticker"], a.get("limit", 15), a.get("doc_type"))
        if name == "ir_read":
            return d.ir_read(a["ticker"], a["doc_id"], a.get("query"))
        if name == "alt_data":
            return d.alt_data(a["ticker"])
        if name == "gov":
            return d.gov(a["ticker"])
        if name == "supply":
            return d.supply(a["ticker"])
        if name == "technicals":
            return d.technicals(a["ticker"])
        if name == "regulatory":
            return d.regulatory(a["ticker"])
        if name == "proposed_sales":
            return d.proposed_sales(a["ticker"])
        if name == "sec_review":
            return d.sec_review(a["ticker"])
        if name == "calendar":
            return d.calendar(a.get("days", 14), a.get("kinds"), a.get("tickers"))
        if name == "market":
            return d.market()
        if name == "screen":
            return d.screen(a.get("filters"), a.get("sort"), a.get("limit", 25), a.get("fields"))
        if name == "feed":
            n = a.get("name")
            if a.get("ticker") and n in FEEDS_T:
                return d.feed_file(FEEDS_T[n].format(t=str(a["ticker"]).upper()))
            if n in FEEDS:
                return d.feed_file(FEEDS[n])
            return {"error": f"unknown feed {n!r}", "feeds": sorted(set(FEEDS) | set(FEEDS_T))}
        if name == "web_search":
            return self.web_search(a["query"])
        if name == "read_url":
            return self.read_url(a["url"])
        if name == "render_view":
            spec = a.get("spec") or a
            if not isinstance(spec, dict) or spec.get("type") not in VIEW_TYPES:
                return {"error": f"spec.type must be one of {sorted(VIEW_TYPES)}"}
            if len(self.views) >= 8:
                return {"error": "at most 8 views per deliverable"}
            self.views.append(spec)
            return {"ok": True, "view": len(self.views), "title": spec.get("title")}
        if name == "create_file":
            return self.create_file(a)
        return {"error": f"unknown tool {name!r}", "tools": sorted(TOOLS)}

    def web_search(self, query):
        if self.web_fetch:
            return self.web_fetch("search", query)
        key = os.environ.get("TAVILY_API_KEY")
        if not key:
            return {"error": "web search not configured"}
        r = json.loads(request("https://api.tavily.com/search", method="POST", data={"query": query[:400], "max_results": 6, "search_depth": "basic"},
                               headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"}, timeout=40, retries=1))
        res = [{"title": x.get("title"), "url": x.get("url"), "snippet": (x.get("content") or "")[:400], "published": x.get("published_date")} for x in r.get("results") or []]
        self.sources += [{"label": x["title"], "url": x["url"]} for x in res[:3]]
        return {"query": query, "results": res}

    def read_url(self, url):
        if self.web_fetch:
            return self.web_fetch("read", url)
        key = os.environ.get("FIRECRAWL_API_KEY")
        if not key or not re.match(r"^https?://", url or ""):
            return {"error": "page reading not configured or bad URL"}
        r = json.loads(request("https://api.firecrawl.dev/v2/scrape", method="POST", data={"url": url, "formats": ["markdown"], "onlyMainContent": True, "timeout": 45000},
                               headers={"Authorization": f"Bearer {key}"}, timeout=60, retries=1))
        md = ((r.get("data") or {}).get("markdown") or "")[:9000]
        self.sources.append({"label": ((r.get("data") or {}).get("metadata") or {}).get("title") or url, "url": url})
        return {"url": url, "markdown": md}

    def create_file(self, a):
        fmt = (a.get("format") or "csv").lower()
        name = re.sub(r"[^A-Za-z0-9_. -]+", "", a.get("name") or "data")[:60] or "data"
        cols, rows = a.get("columns") or [], a.get("rows") or []
        if fmt in ("csv", "xlsx") and not (cols and rows):
            return {"error": "csv/xlsx need columns and rows"}
        if len(self.files) >= 4:
            return {"error": "at most 4 files"}
        safe = lambda v: ("'" + v) if isinstance(v, str) and v[:1] in "=+-@" else v  # spreadsheet formula injection
        if fmt == "csv":
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(cols)
            for r in rows[:5000]:
                w.writerow([safe(v) for v in r])
            data, ct, ext = buf.getvalue().encode(), "text/csv", "csv"
        elif fmt == "xlsx":
            from openpyxl import Workbook
            wb = Workbook()
            ws = wb.active
            ws.append([str(c) for c in cols])
            for r in rows[:20000]:
                ws.append([safe(v) for v in r])
            b = io.BytesIO()
            wb.save(b)
            data, ct, ext = b.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx"
        elif fmt == "json":
            data, ct, ext = json.dumps({"columns": cols, "rows": rows} if cols else a.get("content"), default=str, indent=1).encode(), "application/json", "json"
        else:
            data, ct, ext = str(a.get("content") or "").encode(), "text/markdown", "md"
        self.files.append({"name": f"{name}.{ext}", "data": data, "content_type": ct})
        return {"ok": True, "file": f"{name}.{ext}", "bytes": len(data)}

    # ---- loop
    def _call(self, messages, deadline, max_tokens=6000):
        obj, model, meta = llm.chat_json(MODELS, messages, max_tokens=max_tokens, temperature=0.1, deadline=deadline, timeout=180, post=self.post)
        pin, pout = PRICE.get(model, (1.0, 3.0))
        tin = sum(len(m["content"]) for m in messages) / 4
        tout = meta.get("tokens") or 800
        self.tokens += int(tin + tout)
        self.cost += tin / 1e6 * pin + tout / 1e6 * pout
        return obj, model

    def run(self, task_prompt, today, scope_note="", deliverable="report", deep=False, budget_seconds=300, budget_usd=0.5, max_steps=None, log=print):
        t0 = time.time()
        deadline = t0 + budget_seconds
        max_steps = max_steps or (16 if deep else 9)
        messages = [{"role": "system", "content": system_prompt(today, deliverable, deep)},
                    {"role": "user", "content": f"Task: {task_prompt}\n{scope_note}".strip()}]
        final = None
        model = None
        for step in range(max_steps):
            last = step == max_steps - 1 or time.time() > deadline - 70 or self.cost > budget_usd * 0.85
            if last:
                messages.append({"role": "user", "content": "Budget reached. Reply now with the final JSON object, writing the best note you can from what you have and noting gaps."})
            try:
                obj, model = self._call(messages, deadline)
            except llm.LLMError as e:
                log("agent step failed:", str(e)[:200])
                break
            messages.append({"role": "assistant", "content": json.dumps(obj)[:12000]})
            thought = str(obj.get("thought") or "")[:300]
            if obj.get("final"):
                final = obj["final"]
                self.steps.append({"thought": thought, "final": True})
                break
            calls = [c for c in (obj.get("calls") or []) if isinstance(c, dict) and c.get("tool")][:6]
            if not calls or last:
                if last:
                    continue
                messages.append({"role": "user", "content": "No tool calls found. Either call tools or reply with the final object."})
                continue

            def go(c):
                ts = time.time()
                try:
                    out = self.tool(c["tool"], c.get("args") or {})
                    ok = not (isinstance(out, dict) and out.get("error"))
                except Exception as e:
                    out, ok = {"error": f"{type(e).__name__}: {str(e)[:200]}"}, False
                return c, out, ok, round(time.time() - ts, 2)

            with ThreadPoolExecutor(min(6, len(calls))) as ex:
                results = list(ex.map(go, calls))
            self.steps.append({"thought": thought, "calls": [{"tool": c["tool"], "args": c.get("args"), "ok": ok, "s": s} for c, _, ok, s in results]})
            for c, out, _, _ in results:
                for k in ("url",):
                    if isinstance(out, dict) and isinstance(out.get(k), str) and out[k].startswith("http"):
                        self.sources.append({"label": out.get("title") or out[k], "url": out[k]})
            messages.append({"role": "user", "content": "Tool results:\n" + "\n".join(f"[{c['tool']}] {_clip(out)}" for c, out, _, _ in results)})
            # Keep the context bounded: shrink the oldest tool results first.
            size = sum(len(m["content"]) for m in messages)
            i = 2
            while size > 160_000 and i < len(messages) - 4:
                if messages[i]["role"] == "user" and len(messages[i]["content"]) > 800:
                    size -= len(messages[i]["content"]) - 600
                    messages[i]["content"] = messages[i]["content"][:600] + "…[older results compacted]"
                i += 1
        if not final:
            final = {"title": "Run ended before a note was written", "summary": "The agent ran out of budget before finishing. Try a narrower task or deep mode.",
                     "report_md": "", "tickers": []}
        report = str(final.get("report_md") or "").strip()
        report = re.sub(r"```(?:json)?\s*\{.*?\}\s*```", "", report, flags=re.S)  # never show raw JSON blocks
        return {"title": str(final.get("title") or "Agent report")[:140], "summary": str(final.get("summary") or "")[:800], "report_md": report[:40000],
                "tickers": [str(x).upper() for x in final.get("tickers") or [] if isinstance(x, str)][:20], "views": self.views, "files": self.files,
                "sources": _dedupe(self.sources)[:20], "steps": self.steps, "model": model, "seconds": round(time.time() - t0, 1),
                "cost_usd_est": round(self.cost, 4), "tokens_est": self.tokens}


def _dedupe(xs):
    seen, out = set(), []
    for x in xs:
        u = x.get("url")
        if u and u not in seen:
            seen.add(u)
            out.append(x)
    return out


def docx_from_markdown(title, md):
    """Minimal memo rendering: headings, bullets, paragraphs and pipe tables."""
    from docx import Document
    doc = Document()
    doc.add_heading(title, level=1)
    lines = md.splitlines()
    i = 0
    while i < len(lines):
        ln = lines[i].rstrip()
        if ln.startswith("|") and i + 1 < len(lines) and re.match(r"^\|[\s:|-]+\|$", lines[i + 1].strip()):
            hdr = [c.strip() for c in ln.strip("|").split("|")]
            body = []
            i += 2
            while i < len(lines) and lines[i].startswith("|"):
                body.append([c.strip() for c in lines[i].strip("|").split("|")])
                i += 1
            t = doc.add_table(rows=1 + len(body), cols=len(hdr))
            t.style = "Light Grid Accent 1"
            for j, h in enumerate(hdr):
                t.rows[0].cells[j].text = h
            for r, row in enumerate(body, 1):
                for j, c in enumerate(row[:len(hdr)]):
                    t.rows[r].cells[j].text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", c)
            continue
        clean = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", ln)
        clean = clean.replace("**", "")
        m = re.match(r"^(#{1,4})\s+(.*)", clean)
        if m:
            doc.add_heading(m.group(2), level=min(4, len(m.group(1)) + 1))
        elif re.match(r"^\s*[-*]\s+", clean):
            doc.add_paragraph(re.sub(r"^\s*[-*]\s+", "", clean), style="List Bullet")
        elif clean.strip():
            doc.add_paragraph(clean)
        i += 1
    b = io.BytesIO()
    doc.save(b)
    return b.getvalue()
