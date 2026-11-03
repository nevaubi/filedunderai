// Web and news search for the assistant's web_search tool.
import { guard, readBody } from "./_lib/guard.js";

export default async function handler(req, res) {
  if (req.method !== "POST") return res.status(405).json({ error: "Use POST" });
  if (!(await guard(req, res, { name: "search", perIp: 200, global: 3000 }))) return;
  if (!process.env.TAVILY_API_KEY) return res.status(500).json({ error: "Search is not configured" });
  const body = readBody(req);
  const query = String(body.query || "").slice(0, 400);
  if (!query) return res.status(400).json({ error: "query is required" });
  const r = await fetch("https://api.tavily.com/search", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ api_key: process.env.TAVILY_API_KEY, query, topic: body.news ? "news" : "general", days: body.news ? 21 : undefined, max_results: 8 }),
  });
  if (!r.ok) return res.status(502).json({ error: `Search failed (${r.status})` });
  const d = await r.json();
  res.setHeader("Cache-Control", "no-store");
  return res.status(200).json({ query, results: (d.results || []).map(x => ({ title: x.title, url: x.url, date: x.published_date || null, snippet: (x.content || "").slice(0, 500) })) });
}
