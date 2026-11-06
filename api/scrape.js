// Page reader with bounded, content-identified continuation for research, plus bounded site map,
// small crawl and prompt-driven extraction for the assistant's web tools. All modes share one guard.
import { createHash } from "node:crypto";
import { guard, readBody } from "./_lib/guard.js";

const MAX_CHARS = 12000, MAX_SOURCE_CHARS = 240000, CRAWL_PAGE_CHARS = 6000, CRAWL_MAX = 15;
const BLOCKED = /^(localhost|127\.|10\.|192\.168\.|169\.254\.|172\.(1[6-9]|2\d|3[01])\.|0\.0\.0\.0|\[|metadata\.|.*\.internal$|.*\.local$)/i;
const MODES = new Set(["read", "map", "crawl", "extract"]);
const FC = "https://api.firecrawl.dev/v2";

export function safeTarget(raw) {
  let url;
  try { url = new URL(String(raw)); } catch { return null; }
  if (!/^https?:$/.test(url.protocol) || BLOCKED.test(url.hostname) || url.username || url.password) return null;
  return url;
}

const fc = (path, body, signal, method = "POST") => fetch(FC + path, {
  method, signal,
  headers: { Authorization: `Bearer ${process.env.FIRECRAWL_API_KEY}`, "Content-Type": "application/json" },
  body: method === "POST" ? JSON.stringify(body) : undefined,
});

async function readPage(body, url, signal) {
  const offset = body.offset ?? 0;
  if (!Number.isInteger(offset) || offset < 0 || offset >= MAX_SOURCE_CHARS || (offset > 0 && !/^[a-f0-9]{64}$/.test(body.contentId || ""))) return [400, { error: "Use a valid offset and the contentId from the first page" }];
  const r = await fc("/scrape", { url: url.toString(), formats: ["markdown"], onlyMainContent: true, timeout: 45000 }, AbortSignal.any([signal, AbortSignal.timeout(55000)]));
  if (!r.ok) return [502, { error: `Couldn't read that page (${r.status})` }];
  const d = await r.json();
  const md = typeof d?.data?.markdown === "string" ? d.data.markdown : "";
  const contentId = createHash("sha256").update(md).digest("hex");
  if (body.contentId && body.contentId !== contentId) return [409, { error: "Source content changed; restart from offset zero", contentChanged: true }];
  const available = Math.min(md.length, MAX_SOURCE_CHARS), end = Math.min(offset + MAX_CHARS, available);
  return [200, { url: url.toString(), title: d?.data?.metadata?.title || "", markdown: md.slice(offset, end), offset, nextOffset: end < available ? end : null, totalCharacters: md.length, contentId, truncated: end < md.length, sourceLimitReached: md.length > MAX_SOURCE_CHARS, scope: "Extracted page text; not a guarantee of full source coverage" }];
}

async function mapSite(body, url, signal) {
  const search = typeof body.search === "string" ? body.search.slice(0, 120) : undefined;
  const limit = Math.max(1, Math.min(200, Number.isInteger(body.limit) ? body.limit : 120));
  const r = await fc("/map", { url: url.toString(), search: search || undefined, limit }, AbortSignal.any([signal, AbortSignal.timeout(45000)]));
  if (!r.ok) return [502, { error: `Couldn't map that site (${r.status})` }];
  const d = await r.json();
  const links = (Array.isArray(d?.links) ? d.links : []).map(l => typeof l === "string" ? { url: l } : { url: l?.url, title: l?.title || null, description: l?.description ? String(l.description).slice(0, 200) : null }).filter(l => safeTarget(l.url)).slice(0, limit);
  return [200, { url: url.toString(), search: search || null, links, scope: "Discovered URLs; not a complete site inventory" }];
}

async function crawlSite(body, url, signal) {
  const limit = Math.max(1, Math.min(CRAWL_MAX, Number.isInteger(body.limit) ? body.limit : 8));
  const includePaths = Array.isArray(body.includePaths) ? body.includePaths.filter(p => typeof p === "string" && p.length <= 200).slice(0, 10) : undefined;
  const start = await fc("/crawl", { url: url.toString(), limit, includePaths: includePaths?.length ? includePaths : undefined, scrapeOptions: { formats: ["markdown"], onlyMainContent: true } }, AbortSignal.any([signal, AbortSignal.timeout(20000)]));
  if (!start.ok) return [502, { error: `Couldn't start the crawl (${start.status})` }];
  const { id } = await start.json();
  if (typeof id !== "string" || !/^[A-Za-z0-9-]{8,80}$/.test(id)) return [502, { error: "Crawl did not start" }];
  const deadline = Date.now() + 70000;
  let d = null;
  while (Date.now() < deadline) {
    await new Promise(r => setTimeout(r, 3000));
    if (signal.aborted) return [499, { error: "Cancelled" }];
    const r = await fc(`/crawl/${id}`, null, AbortSignal.any([signal, AbortSignal.timeout(15000)]), "GET");
    if (!r.ok) continue;
    d = await r.json();
    if (d?.status === "completed" || d?.status === "failed") break;
  }
  const pages = (Array.isArray(d?.data) ? d.data : []).slice(0, limit).map(p => ({ url: p?.metadata?.sourceURL || p?.metadata?.url || null, title: p?.metadata?.title || "", markdown: String(p?.markdown || "").slice(0, CRAWL_PAGE_CHARS) }));
  return [200, { url: url.toString(), status: d?.status || "timeout", pages, scope: `At most ${limit} pages, ${CRAWL_PAGE_CHARS} characters each; partial when status is not completed` }];
}

async function extract(body, url, signal) {
  const prompt = String(body.prompt || "").trim().slice(0, 2000);
  if (!prompt) return [400, { error: "prompt is required" }];
  const r = await fc("/scrape", { url: url.toString(), formats: [{ type: "json", prompt }], onlyMainContent: true, timeout: 45000 }, AbortSignal.any([signal, AbortSignal.timeout(60000)]));
  if (!r.ok) return [502, { error: `Couldn't extract from that page (${r.status})` }];
  const d = await r.json();
  const json = d?.data?.json ?? null;
  const text = json == null ? "" : JSON.stringify(json);
  return [200, { url: url.toString(), title: d?.data?.metadata?.title || "", json: text.length > 40000 ? null : json, truncated: text.length > 40000, scope: "Model-extracted values from page text; check them against the page" }];
}

export default async function handler(req, res) {
  if (req.method !== "POST") return res.status(405).json({ error: "Use POST" });
  if (!(await guard(req, res, { name: "scrape", perIp: 120, global: 2000 }))) return;
  if (!process.env.FIRECRAWL_API_KEY) return res.status(500).json({ error: "Page reader is not configured" });
  const body = readBody(req);
  const mode = body.mode ?? "read";
  if (!MODES.has(mode)) return res.status(400).json({ error: "Unknown mode" });
  const url = safeTarget(body.url);
  if (!url) return res.status(400).json({ error: body.url ? "That URL can't be fetched" : "A valid http(s) url is required" });
  const ctl = new AbortController(), cancel = () => ctl.abort();
  req.on?.("aborted", cancel);
  const close = () => { if (!res.writableEnded) cancel(); }; res.on?.("close", close);
  try {
    const [status, data] = await (mode === "map" ? mapSite : mode === "crawl" ? crawlSite : mode === "extract" ? extract : readPage)(body, url, ctl.signal);
    if (status === 499) return;
    res.setHeader("Cache-Control", "no-store");
    return res.status(status).json(data);
  } catch (e) { if (ctl.signal.aborted) return; return res.status(502).json({ error: mode === "read" ? "Page read failed or timed out" : `The ${mode} request failed or timed out` }); }
  finally { req.off?.("aborted", cancel); res.off?.("close", close); }
}
