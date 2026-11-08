// GET /api/app-docsearch?q=...&tickers=AAPL,MSFT&types=release,supplement&since=2024-01-01&limit=30
// Full-text search over stored IR documents (public.search_doc_chunks) for users signed in to the partner app.
// Same auth as /api/app-data: the user's partner Supabase access token, or x-partner-key for its server code.
import { bearer, corsHeaders, originAllowed, serviceKeyOk, verifyUser } from "./_lib/appdata.js";
import { searchParams } from "./_lib/docsearch.js";

export default async function handler(req, res) {
  const origin = req.headers.origin || "";
  const allowed = origin && originAllowed(origin, process.env.APP_DATA_ORIGINS);
  if (allowed) for (const [k, v] of Object.entries(corsHeaders(origin))) res.setHeader(k, v);
  if (req.method === "OPTIONS") return res.status(allowed ? 204 : 403).end();
  if (req.method !== "GET") return res.status(405).json({ error: "Use GET" });
  if (origin && !allowed) return res.status(403).json({ error: "Origin not allowed" });

  const p = searchParams(req.query || {});
  if (p.error) return res.status(400).json({ error: p.error });

  const authUrl = process.env.APP_AUTH_URL, authKey = process.env.APP_AUTH_KEY;
  const url = process.env.SUPABASE_URL, key = process.env.SUPABASE_SERVICE_ROLE_KEY;
  if (!authUrl || !authKey || !url || !key) return res.status(503).json({ error: "Search is not configured" });

  if (req.headers["x-partner-key"] && !origin) {
    if (!serviceKeyOk(req.headers["x-partner-key"], process.env.APP_DATA_SERVICE_KEY)) return res.status(401).json({ error: "Invalid partner key" });
  } else {
    const token = bearer(req.headers.authorization);
    if (!token) return res.status(401).json({ error: "Sign in required" });
    let user = null;
    try {
      user = await verifyUser(token, { authUrl, authKey });
    } catch {
      return res.status(502).json({ error: "Could not confirm the session" });
    }
    if (!user) return res.status(401).json({ error: "Session expired or invalid" });
  }

  let r;
  try {
    r = await fetch(`${url.replace(/\/$/, "")}/rest/v1/rpc/search_doc_chunks`, {
      method: "POST",
      headers: { apikey: key, Authorization: `Bearer ${key}`, "Content-Type": "application/json" },
      body: JSON.stringify(p.args),
      signal: AbortSignal.timeout(20_000),
    });
  } catch {
    return res.status(502).json({ error: "Search unavailable" });
  }
  if (r.status === 404) return res.status(503).json({ error: "Document search is not set up yet" });
  if (!r.ok) return res.status(502).json({ error: "Search failed" });
  const rows = await r.json();
  res.setHeader("Cache-Control", "private, max-age=120");
  return res.status(200).json({ q: p.args.q, n: rows.length, results: rows });
}
