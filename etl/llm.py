"""One JSON-returning chat call for the background loops (Fireworks, OpenAI-compatible).

Reasoning models can spend the token budget thinking and return empty or cut-off content. This helper:
- strips <think> blocks and markdown fences and reads the first JSON object in the reply;
- treats a reply cut off by the token limit, an empty reply, or unparseable JSON as a failure and retries once
  with double the budget, then moves to the next model;
- returns the parsed object, the model that produced it, and call metadata (finish reason, tokens, seconds) so a
  failed run can say why.
"""
import json
import os
import re
import time

from .common import request

URL = "https://api.fireworks.ai/inference/v1/chat/completions"
# Reasoning budget per model, as the KPI job has used it in production: a low effort keeps the reasoning model
# from spending the whole token budget thinking and returning an empty reply.
EFFORT = {"kimi-k3p1-pro": "low", "deepseek-v4p1-flash": "none"}


class LLMError(RuntimeError):
    def __init__(self, msg, attempts):
        super().__init__(msg)
        self.attempts = attempts


def parse_json(txt):
    txt = re.sub(r"(?s)<think>.*?</think>", "", txt or "").strip()
    txt = re.sub(r"^```(?:json)?\s*|\s*```$", "", txt.strip())
    start = txt.find("{")
    if start < 0:
        raise ValueError("no JSON object in reply")
    depth, in_str, esc = 0, False, False
    for i, ch in enumerate(txt[start:], start):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                body = txt[start:i + 1]
                try:
                    return json.loads(body)
                except json.JSONDecodeError:
                    return json.loads(re.sub(r",\s*([}\]])", r"\1", body))
    raise ValueError("JSON object not closed (reply cut off)")


def chat_json(models, messages, max_tokens=8000, temperature=0, deadline=None, timeout=240, post=None, effort=None, api_key=None):
    """Returns (obj, model, meta). `post` replaces the HTTP call in tests: post(model, messages, max_tokens) -> raw API dict."""
    attempts = []
    for model in models:
        budget = max_tokens
        for _ in range(2):
            remaining = None if deadline is None else deadline - time.time()
            if remaining is not None and remaining <= 15:
                raise LLMError("model budget exhausted", attempts)
            t0 = time.time()
            try:
                if post:
                    d = post(model, messages, budget)
                else:
                    body = {"model": f"accounts/fireworks/models/{model}", "messages": messages, "max_tokens": budget,
                            "temperature": temperature, "response_format": {"type": "json_object"}}
                    eff = (effort or {}).get(model, EFFORT.get(model))
                    if eff:
                        body["reasoning_effort"] = eff
                    d = json.loads(request(URL, method="POST", data=body,
                        headers={"Authorization": f"Bearer {api_key or os.environ['FIREWORKS_API_KEY']}"},
                        timeout=min(timeout, remaining) if remaining else timeout, retries=1))
                ch = (d.get("choices") or [{}])[0]
                finish = ch.get("finish_reason")
                content = (ch.get("message") or {}).get("content") or ""
                meta = {"model": model, "finish": finish, "tokens": (d.get("usage") or {}).get("completion_tokens"), "seconds": round(time.time() - t0, 1)}
                if finish == "length":
                    attempts.append(dict(meta, error="cut off at the token limit"))
                    budget *= 2
                    continue
                return parse_json(content), model, meta
            except Exception as e:
                attempts.append({"model": model, "error": str(e)[:160], "seconds": round(time.time() - t0, 1)})
                if "timed out" in str(e):
                    break  # a slow model will be slow again; try the next one
                budget = int(budget * 1.5)
    raise LLMError("; ".join(f"{a['model']}: {a.get('error')}" for a in attempts)[:400], attempts)
