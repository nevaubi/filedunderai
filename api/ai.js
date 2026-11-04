// Research tools run in the client. Only validated model messages leave this endpoint.
import { guard, readBody } from "./_lib/guard.js";
const PRIMARY = process.env.FU_ASSISTANT_MODEL || "kimi-k3";
const FALLBACK = process.env.FU_ASSISTANT_FALLBACK || "deepseek-v4p1-flash";
const MAX_BODY = 700_000;
export function validCompletion(d) {
  const m = d?.choices?.[0]?.message;
  if (!m || d.choices[0].finish_reason === "length") return false;
  if (m.tool_calls != null && !Array.isArray(m.tool_calls)) return false;
  if (m.tool_calls?.length) return m.tool_calls.length <= 12 && new Set(m.tool_calls.map(t => t.id)).size === m.tool_calls.length && m.tool_calls.every(t => {
    if (typeof t.id !== "string" || !t.id || t.type !== "function" || typeof t.function?.name !== "string" || typeof t.function.arguments !== "string") return false;
    try { const a = JSON.parse(t.function.arguments); return a && typeof a === "object" && !Array.isArray(a); } catch { return false; }
  });
  return typeof m.content === "string" && m.content.trim().length > 0;
}
export async function complete(model, body, signal) {
  const timeout = AbortSignal.timeout(25000);
  const r = await fetch("https://api.fireworks.ai/inference/v1/chat/completions", {
    method: "POST", signal: signal ? AbortSignal.any([signal, timeout]) : timeout,
    headers: { Authorization: `Bearer ${process.env.FIREWORKS_API_KEY}`, "Content-Type": "application/json" },
    body: JSON.stringify({ model: `accounts/fireworks/models/${model}`, messages: body.messages, tools: Array.isArray(body.tools) && body.tools.length ? body.tools : undefined, max_tokens: 9000, temperature: 0.3 }),
  });
  if (!r.ok) { const e = new Error("Assistant upstream error"); e.status = r.status; throw e; }
  const data = await r.json();
  if (!validCompletion(data)) throw new Error("Malformed response from the assistant service");
  delete data.model; return data;
}
export async function completeWithFallback(body, signal) {
  try { return await complete(PRIMARY, body, signal); }
  catch (e) { if (signal?.aborted || (e.status && e.status < 500 && ![404, 408, 429].includes(e.status))) throw e; return complete(FALLBACK, body, signal); }
}
export default async function handler(req, res) {
  if (req.method !== "POST") return res.status(405).json({ error: "Use POST" });
  if (!(await guard(req, res, { name: "ai", perIp: 300, global: 6000 }))) return;
  if (!process.env.FIREWORKS_API_KEY) return res.status(500).json({ error: "Assistant is not configured" });
  const body = readBody(req);
  if (JSON.stringify(body).length > MAX_BODY) return res.status(413).json({ error: "Request too large" });
  if (!Array.isArray(body.messages) || !body.messages.length || body.messages.some(m => !m || !["system", "user", "assistant", "tool"].includes(m.role))) return res.status(400).json({ error: "Valid messages are required" });
  const ctl = new AbortController(), cancel = () => ctl.abort();
  req.on?.("aborted", cancel);
  const close = () => { if (!res.writableEnded) cancel(); }; res.on?.("close", close);
  try { const data = await completeWithFallback(body, ctl.signal); res.setHeader("Cache-Control", "no-store"); return res.status(200).json(data); }
  catch (e) { if (ctl.signal.aborted) return; return res.status(e.status === 429 ? 429 : 502).json({ error: "The assistant did not return a usable response", status: e.status || 502 }); }
  finally { req.off?.("aborted", cancel); res.off?.("close", close); }
}
