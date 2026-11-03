// Research assistant endpoint. The page sends the conversation and tool schemas; this route calls the
// configured model and returns an OpenAI-compatible completion. Tools run in the browser.
import { guard, readBody } from "./_lib/guard.js";

const PRIMARY = process.env.FU_ASSISTANT_MODEL || "kimi-k3";
const FALLBACK = process.env.FU_ASSISTANT_FALLBACK || "deepseek-v4p1-flash";
const MAX_BODY = 700_000;

async function complete(model, body) {
  return fetch("https://api.fireworks.ai/inference/v1/chat/completions", {
    method: "POST",
    headers: { Authorization: `Bearer ${process.env.FIREWORKS_API_KEY}`, "Content-Type": "application/json" },
    body: JSON.stringify({
      model: `accounts/fireworks/models/${model}`,
      messages: body.messages,
      tools: Array.isArray(body.tools) && body.tools.length ? body.tools : undefined,
      max_tokens: 9000,
      temperature: 0.3,
    }),
  });
}

export default async function handler(req, res) {
  if (req.method !== "POST") return res.status(405).json({ error: "Use POST" });
  if (!(await guard(req, res, { name: "ai", perIp: 300, global: 6000 }))) return;
  if (!process.env.FIREWORKS_API_KEY) return res.status(500).json({ error: "Assistant is not configured" });
  const body = readBody(req);
  if (JSON.stringify(body).length > MAX_BODY) return res.status(413).json({ error: "Request too large" });
  if (!Array.isArray(body.messages) || !body.messages.length) return res.status(400).json({ error: "messages is required" });
  let r = await complete(PRIMARY, body);
  if (!r.ok && r.status >= 500) r = await complete(FALLBACK, body);
  const text = await r.text();
  if (!r.ok) return res.status(r.status === 429 ? 429 : 502).json({ error: "The assistant service returned an error", status: r.status });
  try {
    const d = JSON.parse(text);
    delete d.model;
    res.setHeader("Cache-Control", "no-store");
    return res.status(200).json(d);
  } catch {
    return res.status(502).json({ error: "Malformed response from the assistant service" });
  }
}
