// Viewer copies of SEC filings for the source panel. Given an accession number that appears in the
// site's data, fetches the primary document from EDGAR once, removes scripts, adds a base URL so images
// and links resolve to EDGAR, and stores it gzipped in the public bucket. Later requests go straight to storage.
import { gzipSync } from "node:zlib";
import { guard } from "./_lib/guard.js";

const ACCN = /^\d{10}-\d{2}-\d{6}$/;
const DOC = /^[A-Za-z0-9._-]+\.html?$/;

function secAgent() {
  const ua = process.env.SEC_USER_AGENT || "FiledUnder research contact@filedunder.ai";
  return ua.includes("@") ? ua : `${ua} contact@filedunder.ai`;
}

function sbHeaders(extra = {}) {
  const key = process.env.SUPABASE_SERVICE_ROLE_KEY;
  return { apikey: key, Authorization: `Bearer ${key}`, ...extra };
}

async function rest(path) {
  const r = await fetch(`${process.env.SUPABASE_URL}/rest/v1/${path}`, { headers: sbHeaders() });
  if (!r.ok) return [];
  return r.json();
}

async function exists(path) {
  const r = await fetch(`${process.env.SUPABASE_URL}/storage/v1/object/public/public-data/${path}`, { method: "HEAD" });
  return r.ok;
}

async function sec(url) {
  const r = await fetch(url, { headers: { "User-Agent": secAgent(), Accept: "text/html,application/json,*/*" } });
  if (!r.ok) throw new Error(`EDGAR returned ${r.status}`);
  return r;
}

// Resolves an accession to { cik, url } using only filings the site already knows about.
async function resolve(accn) {
  const f = await rest(`filings?accession=eq.${accn}&select=ticker,primary_doc,url`);
  let ticker = f[0]?.ticker;
  if (f[0]?.url && f[0]?.primary_doc && DOC.test(f[0].primary_doc.split("/").pop())) {
    const co = await rest(`companies?ticker=eq.${encodeURIComponent(ticker)}&select=cik`);
    return { cik: co[0]?.cik, url: f[0].url };
  }
  if (!ticker) {
    const fact = await rest(`facts?accession=eq.${accn}&select=ticker&limit=1`);
    ticker = fact[0]?.ticker;
  }
  if (!ticker) return null;
  const co = await rest(`companies?ticker=eq.${encodeURIComponent(ticker)}&select=cik`);
  const cik = co[0]?.cik;
  if (!cik) return null;
  const folder = `https://www.sec.gov/Archives/edgar/data/${Number(cik)}/${accn.replace(/-/g, "")}/`;
  const idx = await (await sec(folder + "index.json")).json();
  const items = (idx?.directory?.item || []).filter(i => DOC.test(i.name) && !/^R\d+\.htm$/i.test(i.name) && !/(^|[-_])ex[-_]?\d/i.test(i.name) && !/index/i.test(i.name));
  items.sort((a, b) => Number(b.size || 0) - Number(a.size || 0));
  if (!items.length) return null;
  return { cik, url: folder + items[0].name };
}

function viewerHtml(html, cik, accn) {
  const base = `<base href="https://www.sec.gov/Archives/edgar/data/${Number(cik)}/${accn.replace(/-/g, "")}/" target="_blank">`;
  html = html.replace(/<script\b[\s\S]*?<\/script>/gi, "");
  const m = html.match(/<head[^>]*>/i);
  return m ? html.slice(0, m.index + m[0].length) + base + html.slice(m.index + m[0].length) : base + html;
}

export default async function handler(req, res) {
  if (req.method !== "GET") return res.status(405).json({ error: "Use GET" });
  const accn = String(req.query.accn || "");
  if (!ACCN.test(accn)) return res.status(400).json({ error: "accn must look like 0000000000-00-000000" });
  const path = `filings/${accn}.html.gz`;
  try {
    if (await exists(path)) return res.status(200).json({ path, cached: true });
    if (!(await guard(req, res, { name: "filing", perIp: 400, global: 8000 }))) return;
    const src = await resolve(accn);
    if (!src?.url) return res.status(404).json({ error: "This filing is not in Filed Under's data" });
    const html = await (await sec(src.url)).text();
    const body = gzipSync(Buffer.from(viewerHtml(html, src.cik, accn), "utf8"), { level: 6 });
    const up = await fetch(`${process.env.SUPABASE_URL}/storage/v1/object/public-data/${path}`, {
      method: "POST",
      headers: sbHeaders({ "Content-Type": "application/gzip", "x-upsert": "true", "cache-control": "public, max-age=31536000, immutable" }),
      body,
    });
    if (!up.ok) return res.status(502).json({ error: `Storage upload failed (${up.status})` });
    res.setHeader("Cache-Control", "no-store");
    return res.status(200).json({ path, cached: false, source: src.url });
  } catch (e) {
    return res.status(502).json({ error: String(e.message || e).slice(0, 300) });
  }
}
