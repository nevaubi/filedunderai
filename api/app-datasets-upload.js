// POST /api/app-datasets-upload — lets the app-datasets GitHub workflow write to the private
// `app-datasets` bucket without any stored secret. The workflow sends its GitHub OIDC token
// (audience "filedunder-app-datasets"); only nevaubi/filedunderai runs on main are accepted.
//   {"action":"sign","paths":["companies/NVDA.json", ...]} → {"urls":{path: signedUploadUrl}}
//   {"action":"merge"} → rebuilds universe.json from entries/*.json
import { verifyGithubOidc } from "./_lib/ghoidc.js";
import { BUCKET } from "./_lib/appdata.js";

export const AUDIENCE = "filedunder-app-datasets";
const EXPECT = { repository: "nevaubi/filedunderai", ref: "refs/heads/main" };
const UPLOAD_RX = /^(companies|prices|entries)\/[A-Za-z0-9_.-]{1,32}\.json$/;
const MAX_PATHS = 200;

export function uploadPathOk(p) {
  const s = String(p || "");
  return !s.includes("..") && UPLOAD_RX.test(s);
}

export function mergeUniverse(prev, entries) {
  const order = (prev?.companies || []).map((c) => c.ticker);
  const by = new Map((prev?.companies || []).map((c) => [c.ticker, c]));
  for (const c of entries) if (c?.ticker) by.set(c.ticker, c);
  const pos = new Map(order.map((t, i) => [t, i]));
  const companies = [...by.values()].sort((a, b) => (pos.get(a.ticker) ?? 1e9) - (pos.get(b.ticker) ?? 1e9) || a.ticker.localeCompare(b.ticker));
  return { generatedAt: new Date().toISOString(), companies };
}

function storage() {
  const url = (process.env.SUPABASE_URL || "").replace(/\/$/, "");
  const key = process.env.SUPABASE_SERVICE_ROLE_KEY || "";
  return { url, auth: { apikey: key, Authorization: `Bearer ${key}` }, ok: !!(url && key) };
}

async function sign(st, path) {
  const r = await fetch(`${st.url}/storage/v1/object/upload/sign/${BUCKET}/${path}`, { method: "POST", headers: { ...st.auth, "x-upsert": "true" } });
  if (!r.ok) return null;
  const j = await r.json();
  return j.url ? `${st.url}/storage/v1${j.url}` : null;
}

async function pool(items, n, fn) {
  const out = new Array(items.length);
  let next = 0;
  await Promise.all(Array.from({ length: Math.min(n, items.length) }, async () => {
    while (next < items.length) {
      const i = next++;
      out[i] = await fn(items[i]);
    }
  }));
  return out;
}

async function merge(st) {
  const names = [];
  for (let offset = 0; ; offset += 1000) {
    const r = await fetch(`${st.url}/storage/v1/object/list/${BUCKET}`, {
      method: "POST",
      headers: { ...st.auth, "Content-Type": "application/json" },
      body: JSON.stringify({ prefix: "entries/", limit: 1000, offset, sortBy: { column: "name", order: "asc" } }),
    });
    if (!r.ok) throw new Error(`list ${r.status}`);
    const page = await r.json();
    names.push(...page.map((o) => o.name).filter((n) => n.endsWith(".json")));
    if (page.length < 1000) break;
  }
  const get = async (p) => {
    const r = await fetch(`${st.url}/storage/v1/object/${BUCKET}/${p}`, { headers: st.auth });
    return r.ok ? r.json().catch(() => null) : null;
  };
  const entries = (await pool(names, 16, (n) => get(`entries/${n}`))).filter((e) => e?.ticker);
  const merged = mergeUniverse(await get("universe.json"), entries);
  const w = await fetch(`${st.url}/storage/v1/object/${BUCKET}/universe.json`, {
    method: "POST",
    headers: { ...st.auth, "Content-Type": "application/json", "x-upsert": "true" },
    body: JSON.stringify(merged),
  });
  if (!w.ok) throw new Error(`write ${w.status}`);
  return { entries: entries.length, companies: merged.companies.length };
}

export default async function handler(req, res) {
  res.setHeader("Cache-Control", "no-store");
  if (req.method !== "POST") return res.status(405).json({ error: "Use POST" });
  const st = storage();
  if (!st.ok) return res.status(503).json({ error: "Storage not configured" });
  const token = String(req.headers.authorization || "").replace(/^Bearer\s+/i, "");
  let claims = null;
  try {
    claims = await verifyGithubOidc(token, { audience: AUDIENCE, expect: EXPECT });
  } catch {
    return res.status(502).json({ error: "Could not load GitHub keys" });
  }
  if (!claims) return res.status(401).json({ error: "GitHub OIDC token required" });
  const body = typeof req.body === "string" ? JSON.parse(req.body || "{}") : req.body || {};
  try {
    if (body.action === "sign") {
      const paths = Array.isArray(body.paths) ? body.paths : [];
      if (!paths.length || paths.length > MAX_PATHS) return res.status(400).json({ error: `1-${MAX_PATHS} paths` });
      const bad = paths.filter((p) => !uploadPathOk(p));
      if (bad.length) return res.status(400).json({ error: "Path not allowed", bad: bad.slice(0, 5) });
      const signed = await pool(paths, 12, (p) => sign(st, p));
      const urls = Object.fromEntries(paths.map((p, i) => [p, signed[i]]).filter(([, u]) => u));
      return res.status(200).json({ urls, run: claims.run_id });
    }
    if (body.action === "merge") return res.status(200).json(await merge(st));
    return res.status(400).json({ error: "Unknown action" });
  } catch (e) {
    console.error("app-datasets-upload", String(e).slice(0, 300));
    return res.status(500).json({ error: "Upload service failed" });
  }
}
