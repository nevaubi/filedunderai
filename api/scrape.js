// Page reader with bounded, content-identified continuation for research.
import { createHash } from "node:crypto";
import { guard, readBody } from "./_lib/guard.js";

const MAX_CHARS = 12000, MAX_SOURCE_CHARS = 240000;
const BLOCKED = /^(localhost|127\.|10\.|192\.168\.|169\.254\.|0\.0\.0\.0|\[)/;

export default async function handler(req, res) {
  if (req.method !== "POST") return res.status(405).json({ error: "Use POST" });
  if (!(await guard(req, res, { name: "scrape", perIp: 120, global: 2000 }))) return;
  if (!process.env.FIRECRAWL_API_KEY) return res.status(500).json({ error: "Page reader is not configured" });
  const body = readBody(req);
  const offset = body.offset ?? 0;
  if (!Number.isInteger(offset) || offset < 0 || offset >= MAX_SOURCE_CHARS || (offset > 0 && !/^[a-f0-9]{64}$/.test(body.contentId || ""))) return res.status(400).json({ error: "Use a valid offset and the contentId from the first page" });
  let url;
  try { url = new URL(String(body.url)); } catch { return res.status(400).json({ error: "A valid http(s) url is required" }); }
  if (!/^https?:$/.test(url.protocol) || BLOCKED.test(url.hostname)) return res.status(400).json({ error: "That URL can't be fetched" });
  const ctl = new AbortController(), cancel = () => ctl.abort();
  req.on?.("aborted", cancel);
  const close = () => { if (!res.writableEnded) cancel(); }; res.on?.("close", close);
  try {
    const r = await fetch("https://api.firecrawl.dev/v2/scrape", {
      method: "POST", signal: AbortSignal.any([ctl.signal, AbortSignal.timeout(55000)]),
      headers: { Authorization: `Bearer ${process.env.FIRECRAWL_API_KEY}`, "Content-Type": "application/json" },
      body: JSON.stringify({ url: url.toString(), formats: ["markdown"], onlyMainContent: true, timeout: 45000 }),
    });
    if (!r.ok) return res.status(502).json({ error: `Couldn't read that page (${r.status})` });
    const d = await r.json();
    const md = typeof d?.data?.markdown === "string" ? d.data.markdown : "";
    const contentId = createHash("sha256").update(md).digest("hex");
    if (body.contentId && body.contentId !== contentId) return res.status(409).json({ error: "Source content changed; restart from offset zero", contentChanged: true });
    const available = Math.min(md.length, MAX_SOURCE_CHARS), end = Math.min(offset + MAX_CHARS, available);
    res.setHeader("Cache-Control", "no-store");
    return res.status(200).json({ url: url.toString(), title: d?.data?.metadata?.title || "", markdown: md.slice(offset, end), offset, nextOffset: end < available ? end : null, totalCharacters: md.length, contentId, truncated: end < md.length, sourceLimitReached: md.length > MAX_SOURCE_CHARS, scope: "Extracted page text; not a guarantee of full source coverage" });
  } catch (e) { if (ctl.signal.aborted) return; return res.status(502).json({ error: "Page read failed or timed out" }); }
  finally { req.off?.("aborted", cancel); res.off?.("close", close); }
}
