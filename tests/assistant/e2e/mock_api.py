"""Local stand-in for the Vercel API routes used by the assistant panel, for browser end-to-end tests only.

Serves the built site from dist/ and answers:
  POST /api/ai      scripted Fireworks-style chat completions with tool calls (no model, no key)
  POST /api/search  fixed Tavily-shaped results and EDGAR full-text hits (clearly marked test fixtures)
  POST /api/scrape  a fixed page for read/map/crawl/extract
  GET  /api/filing  404 (filings come from the public bucket cache)

The /api/ai script reads the conversation it receives, so citations in the final answer use the real evidence ids
and filing passages returned by the browser-side tools. Scenario is chosen by a keyword in the user's question.
Usage: python3 tests/assistant/e2e/mock_api.py [port]
"""
import json
import re
import sys
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DIST = ROOT / "dist"
LOG = []


def call(name, args, i):
    return {"id": f"call_{name}_{i}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def reply(content="", tool_calls=None, reasoning=None):
    msg = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    if reasoning:
        msg["reasoning_content"] = reasoning
    return {"choices": [{"finish_reason": "tool_calls" if tool_calls else "stop", "message": msg}], "usage": {"completion_tokens": 400}, "research": {"providerCalls": 1, "chargedTokens": 400}}


def current_turn(messages):
    start = max(i for i, m in enumerate(messages) if m.get("role") == "user" and str(m.get("content", "")).startswith("[Context"))
    question = messages[start]["content"].split("\n", 1)[-1]
    after = messages[start + 1:]
    return question, after


def tool_outputs(after):
    return [m.get("content", "") for m in after if m.get("role") == "tool"]


def first_evidence(outputs):
    """Latest revenue value and evidence id from the get_financials output (the last point of the Revenue row)."""
    for out in outputs:
        m = re.search(r"Revenue \(USD\):\n((?:  .*\n?)+)", out)
        if not m:
            continue
        hits = re.findall(r"\) = (-?\d+(?:\.\d+)?) USD[^\[\n]*\[(ev_[a-f0-9]{16})\]", m.group(1))
        if hits:
            return float(hits[-1][0]), hits[-1][1]
    return None, None


def quote_from(outputs):
    for out in outputs:
        if "--- passage 1 ---" not in out:
            continue
        body = out.split("--- passage 1 ---", 1)[1]
        for line in body.split("\n"):
            words = line.split()
            if len(line) > 80 and "|" not in line and len(words) > 12 and not re.search(r"[\d$%()’“”\"';:]", " ".join(words[2:10])):
                return " ".join(words[2:10])
    return None


def accession(outputs, form="10-Q"):
    for out in outputs:
        m = re.search(r"(\d{10}-\d{2}-\d{6}) = " + re.escape(form), out)
        if m:
            return m.group(1)
    return "0001045810-26-000075"


def fmt_b(v):
    return f"${v / 1e9:.2f}B"


def script(body):
    messages = body.get("messages") or []
    question, after = current_turn(messages)
    stage = sum(1 for m in after if m.get("role") == "assistant")
    outs = tool_outputs(after)
    reviewed = any(m.get("role") == "user" and str(m.get("content", "")).startswith("[Self-review") for m in after)
    q = question.lower()
    if "scenario slow" in q:
        time.sleep(25)
        return reply("Too late.")
    if "scenario deep" in q:
        if not reviewed and stage == 0:
            return reply("", [
                call("update_plan", {"goal": "Assess NVDA momentum versus semiconductors", "steps": [{"title": "Pull NVDA technicals", "status": "doing"}, {"title": "Compare with SMH", "status": "todo"}, {"title": "Rank the screen", "status": "todo"}], "notes": ""}, 0),
                call("get_technicals", {"symbol": "NVDA"}, 1),
                call("get_quotes", {"symbols": ["NVDA", "SMH"]}, 2),
                call("screen_companies", {"sort_by": "ret_1y", "descending": True, "limit": 5, "sector": "Information Technology"}, 3),
            ], reasoning="**Scoping** Momentum needs price statistics, a peer benchmark and a ranked screen.")
        if not reviewed:
            return reply("Draft answer: NVDA momentum is strong relative to peers.")
        if not any("get_price_history" in json.dumps(m.get("tool_calls", [])) for m in after):
            return reply("", [
                call("update_plan", {"goal": "Assess NVDA momentum versus semiconductors", "steps": [{"title": "Pull NVDA technicals", "status": "done"}, {"title": "Compare with SMH", "status": "doing"}, {"title": "Rank the screen", "status": "done"}], "notes": "Draft lacked the SMH one-year path; adding it."}, 4),
                call("get_price_history", {"symbol": "SMH", "range": "1y"}, 5),
            ], reasoning="**Review** The draft compared levels without the benchmark's one-year path.")
        return reply("## Revised answer\nNVDA momentum, checked against the SMH one-year path and the sector screen.\n\n- Technicals and quotes come from the stored daily closes.\n- The benchmark path is now included.\n\nWhat to watch: the next earnings release.")
    # default scenario: plan -> parallel tools -> ask_user -> filing read -> cited answer
    if stage == 0:
        return reply("", [
            call("update_plan", {"goal": "Explain NVDA's revenue trend, insider activity and data center commentary", "steps": [{"title": "Pull quarterly revenue and margins", "status": "doing"}, {"title": "Check insider trades", "status": "doing"}, {"title": "Scan recent coverage", "status": "doing"}, {"title": "Read the latest 10-Q", "status": "todo"}], "notes": ""}, 0),
            call("get_financials", {"ticker": "NVDA", "frequency": "quarterly", "periods": 6, "metrics": ["Revenue", "Gross margin"]}, 1),
            call("get_insider_trades", {"ticker": "NVDA", "open_market_only": True, "limit": 8}, 2),
            call("web_research", {"query": "NVIDIA data center revenue outlook", "topic": "news", "days": 30}, 3),
        ], reasoning="**Planning the work** Revenue trend first, then insiders and coverage in parallel.")
    if stage == 1:
        return reply("", [call("ask_user", {"question": "Which horizon should I emphasize?", "options": ["Last 4 quarters", "Last 8 quarters"]}, 4)])
    if stage == 2:
        return reply("", [
            call("update_plan", {"goal": "Explain NVDA's revenue trend, insider activity and data center commentary", "steps": [{"title": "Pull quarterly revenue and margins", "status": "done"}, {"title": "Check insider trades", "status": "done"}, {"title": "Scan recent coverage", "status": "done"}, {"title": "Read the latest 10-Q", "status": "doing"}], "notes": "Insiders were net sellers; horizon chosen by the user."}, 5),
            call("read_filing", {"ticker": "NVDA", "accession": accession(outs), "query": "Data Center"}, 6),
        ])
    value, ev = first_evidence(outs)
    accn = accession(outs)
    quote = quote_from(outs)
    answer = [f"NVDA's latest reported quarterly revenue was {fmt_b(value)} [[E:{ev}]]." if ev else "Revenue evidence was not returned.",
              "",
              "| Item | Value | Source |",
              "|---|---:|---|",
              f"| Revenue | {fmt_b(value)} | [[E:{ev}]] |" if ev else "| Revenue | n/a | n/a |",
              "| Insider activity | Net selling | [Form 4 filings](https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0001045810&type=4) |",
              "",
              f"The 10-Q discusses the segment directly: [10-Q](filing:NVDA:{accn} \"{quote}\")." if quote else f"See the [10-Q](filing:NVDA:{accn}).",
              "",
              "```chart",
              json.dumps({"type": "bar", "title": "Revenue (test chart)", "unit": "$", "x": "period", "series": ["NVDA"], "data": [{"period": "Q-1", "NVDA": (value or 1e9) * 0.9}, {"period": "Q0", "NVDA": value or 1e9}]}),
              "```",
              "",
              "Coverage: [test fixture article](https://example.com/nvda-fixture).",
              "",
              "What to watch: next quarter's guidance."]
    return reply("\n".join(answer))


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=str(DIST), **k)

    def log_message(self, *a):
        pass

    def _json(self, code, data):
        raw = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        try:
            self.wfile.write(raw)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        if self.path.startswith("/api/filing"):
            return self._json(404, {"error": "not in fixture"})
        if self.path.startswith("/api/"):
            return self._json(404, {"error": "unknown"})
        return super().do_GET()

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        LOG.append({"path": self.path, "body": body})
        if self.path == "/api/ai":
            if not body.get("profile", "").startswith("agent_"):
                return self._json(400, {"error": "expected an agent profile"})
            try:
                return self._json(200, script(body))
            except Exception as e:  # surface script bugs to the test
                return self._json(500, {"error": f"mock failure: {e}"})
        if self.path == "/api/search":
            if body.get("source") == "edgar":
                return self._json(200, {"query": body.get("query"), "total": 1, "searchUrl": "https://www.sec.gov/edgar/search/", "hits": [{"company": "NVIDIA CORP (NVDA)", "form": "10-Q", "filed": "2026-08-26", "accession": "0001045810-26-000075", "document": "fixture.htm", "url": "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000075/"}]})
            return self._json(200, {"query": body.get("query"), "results": [
                {"title": "Test fixture: data center demand article", "url": "https://example.com/nvda-fixture", "date": "2026-11-01", "snippet": "Fixture text for browser tests."},
                {"title": "Test fixture: SEC filing index", "url": "https://www.sec.gov/edgar/search/", "date": "2026-10-30", "snippet": "Fixture text."}]})
        if self.path == "/api/scrape":
            return self._json(200, {"url": body.get("url"), "title": "Fixture page", "markdown": "# Fixture\nTest page text.", "offset": 0, "nextOffset": None, "contentId": "0" * 64, "links": [], "pages": [], "json": {}})
        return self._json(404, {"error": "unknown"})


def serve(port):
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8106
    serve(port)
    print(f"mock API + dist on http://127.0.0.1:{port}")
    while True:
        time.sleep(3600)
