// Page reader for the assistant's read_url tool. Returns markdown trimmed to 12,000 characters.
import { guard, readBody } from "./_lib/guard.js";

const MAX_CHARS = 12000;
const BLOCKED = /^(localhost|127\.|10\.|192\.168\.|169\.254\.|0\.0\.0\.0|\[)/;

export default async function handler(req, res) {
  if (req.method !== "POST") return res.status(405).json({ error: "Use POST" });
  if (!(await guard(req, res, { name: "scrape", perIp: 120, global: 2000 }))) return;
  if (!process.env.FIRECRAWL_API_KEY) return res.status(500).json({ error: "Page reader is not configured" });
  const body = readBody(req);
  let url;
  try { url = new URL(String(body.url)); } catch { return res.status(400).json({ error: "A valid http(s) url is required" }); }
  if (!/^https?:$/.test(url.protocol) || BLOCKED.test(url.hostname)) return res.status(400).json({ error: "That URL can't be fetched" });
  const r = await fetch("https://api.firecrawl.dev/v2/scrape", {
    method: "POST",
    headers: { Authorization: `Bearer ${process.env.FIRECRAWL_API_KEY}`, "Content-Type": "application/json" },
    body: JSON.stringify({ url: url.toString(), formats: ["markdown"], onlyMainContent: true, timeout: 45000 }),
  });
  if (!r.ok) return res.status(502).json({ error: `Couldn't read that page (${r.status})` });
  const d = await r.json();
  const md = d?.data?.markdown || "";
  res.setHeader("Cache-Control", "no-store");
  return res.status(200).json({ url: url.toString(), title: d?.data?.metadata?.title || "", markdown: md.slice(0, MAX_CHARS), truncated: md.length > MAX_CHARS });
}
