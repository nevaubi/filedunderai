// Web/news search (Tavily) and SEC EDGAR full-text search for the assistant's research tools.
import { guard, readBody } from "./_lib/guard.js";

const TOPICS = new Set(["general", "news", "finance"]);
const FORM = /^[A-Z0-9][A-Z0-9 ./-]{0,15}$/i;

function secAgent() {
  const ua = process.env.SEC_USER_AGENT || "FiledUnder research contact@filedunder.ai";
  return ua.includes("@") ? ua : `${ua} contact@filedunder.ai`;
}

// Arguments are validated here as well as in the browser: only bounded, typed values reach the provider.
export function edgarQuery(body) {
  const q = String(body.query || "").trim().slice(0, 300);
  if (!q) throw Object.assign(new Error("query is required"), { status: 400 });
  const params = new URLSearchParams({ q });
  const ciks = (Array.isArray(body.ciks) ? body.ciks : []).map(c => String(c).replace(/\D/g, "")).filter(c => c && c.length <= 10).slice(0, 5).map(c => c.padStart(10, "0"));
  if (ciks.length) params.set("ciks", ciks.join(","));
  const forms = (Array.isArray(body.forms) ? body.forms : []).map(f => String(f).trim().toUpperCase()).filter(f => FORM.test(f)).slice(0, 8);
  if (forms.length) params.set("forms", forms.join(","));
  const day = /^\d{4}-\d{2}-\d{2}$/;
  if (day.test(body.startdt || "") || day.test(body.enddt || "")) {
    params.set("dateRange", "custom");
    params.set("startdt", day.test(body.startdt || "") ? body.startdt : "2001-01-01");
    params.set("enddt", day.test(body.enddt || "") ? body.enddt : new Date().toISOString().slice(0, 10));
  }
  return `https://efts.sec.gov/LATEST/search-index?${params}`;
}

export function edgarHits(d) {
  const hits = Array.isArray(d?.hits?.hits) ? d.hits.hits : [];
  return {
    total: Number(d?.hits?.total?.value) || hits.length,
    hits: hits.slice(0, 20).map(h => {
      const s = h?._source || {}, [adsh, file] = String(h?._id || "").split(":");
      const accession = s.adsh || adsh || "", cik = String(s.ciks?.[0] || "").replace(/^0+/, "");
      const url = cik && accession && file ? `https://www.sec.gov/Archives/edgar/data/${cik}/${accession.replace(/-/g, "")}/${file}` : null;
      return { company: String(s.display_names?.[0] || "").replace(/\s+/g, " ").trim(), cik: cik || null, form: s.form || s.file_type || null, filed: s.file_date || null, period: s.period_ending || null, accession, document: file || null, description: s.file_description || null, url };
    }),
  };
}

export default async function handler(req, res) {
  if (req.method !== "POST") return res.status(405).json({ error: "Use POST" });
  if (!(await guard(req, res, { name: "search", perIp: 200, global: 3000 }))) return;
  const body = readBody(req);
  if (body.source === "edgar") {
    let url;
    try { url = edgarQuery(body); } catch (e) { return res.status(e.status || 400).json({ error: e.message }); }
    try {
      const r = await fetch(url, { headers: { "User-Agent": secAgent(), Accept: "application/json" }, signal: AbortSignal.timeout(25000) });
      if (!r.ok) return res.status(502).json({ error: `EDGAR full-text search failed (${r.status})` });
      res.setHeader("Cache-Control", "no-store");
      return res.status(200).json({ query: String(body.query).slice(0, 300), source: "SEC EDGAR full-text search", searchUrl: `https://www.sec.gov/edgar/search/#/q=${encodeURIComponent(String(body.query).slice(0, 300))}`, ...edgarHits(await r.json()) });
    } catch { return res.status(502).json({ error: "EDGAR full-text search timed out" }); }
  }
  if (!process.env.TAVILY_API_KEY) return res.status(500).json({ error: "Search is not configured" });
  const query = String(body.query || "").slice(0, 400);
  if (!query) return res.status(400).json({ error: "query is required" });
  const topic = TOPICS.has(body.topic) ? body.topic : body.news ? "news" : "general";
  const days = Number.isInteger(body.days) && body.days > 0 ? Math.min(365, body.days) : topic === "news" ? 21 : undefined;
  const r = await fetch("https://api.tavily.com/search", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ api_key: process.env.TAVILY_API_KEY, query, topic, days: topic === "news" ? days : undefined, max_results: 8 }),
  });
  if (!r.ok) return res.status(502).json({ error: `Search failed (${r.status})` });
  const d = await r.json();
  res.setHeader("Cache-Control", "no-store");
  return res.status(200).json({ query, topic, results: (d.results || []).map(x => ({ title: x.title, url: x.url, date: x.published_date || null, snippet: (x.content || "").slice(0, 500) })) });
}
