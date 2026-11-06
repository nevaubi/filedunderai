// GET /api/app-data?path=companies/NVDA.json
// Serves the partner app's company/price datasets from the private `app-datasets` bucket to
// users signed in to that app. Auth: `Authorization: Bearer <partner Supabase access token>`,
// checked against APP_AUTH_URL with APP_AUTH_KEY (the partner project's publishable key).
import { BUCKET, bearer, cleanPath, corsHeaders, originAllowed, verifyUser } from "./_lib/appdata.js";

const PER_USER_DAILY = 5000;

async function bump(bucket) {
  const url = process.env.SUPABASE_URL, key = process.env.SUPABASE_SERVICE_ROLE_KEY;
  if (!url || !key) return 0;
  const r = await fetch(`${url}/rest/v1/rpc/bump_usage`, {
    method: "POST",
    headers: { apikey: key, Authorization: `Bearer ${key}`, "Content-Type": "application/json" },
    body: JSON.stringify({ p_bucket: bucket, p_limit: 0 }),
  });
  if (!r.ok) return 0;
  return Number(await r.json()) || 0;
}

export default async function handler(req, res) {
  const origin = req.headers.origin || "";
  const allowed = origin && originAllowed(origin, process.env.APP_DATA_ORIGINS);
  if (allowed) for (const [k, v] of Object.entries(corsHeaders(origin))) res.setHeader(k, v);
  if (req.method === "OPTIONS") return res.status(allowed ? 204 : 403).end();
  if (req.method !== "GET") return res.status(405).json({ error: "Use GET" });
  if (origin && !allowed) return res.status(403).json({ error: "Origin not allowed" });

  const path = cleanPath(req.query?.path);
  if (!path) return res.status(400).json({ error: "Unknown dataset path" });

  const authUrl = process.env.APP_AUTH_URL, authKey = process.env.APP_AUTH_KEY;
  const url = process.env.SUPABASE_URL, key = process.env.SUPABASE_SERVICE_ROLE_KEY;
  if (!authUrl || !authKey || !url || !key) return res.status(503).json({ error: "Dataset access is not configured" });

  const token = bearer(req.headers.authorization);
  if (!token) return res.status(401).json({ error: "Sign in required" });
  let user = null;
  try {
    user = await verifyUser(token, { authUrl, authKey });
  } catch {
    return res.status(502).json({ error: "Could not confirm the session" });
  }
  if (!user) return res.status(401).json({ error: "Session expired or invalid" });

  try {
    const day = new Date().toISOString().slice(0, 10);
    if ((await bump(`appdata:${day}:${user}`)) > PER_USER_DAILY) return res.status(429).json({ error: "Daily data limit reached" });
  } catch {
    // Counter unavailable: fail open.
  }

  const headers = { apikey: key, Authorization: `Bearer ${key}` };
  if (req.headers["if-none-match"]) headers["If-None-Match"] = req.headers["if-none-match"];
  let r;
  try {
    r = await fetch(`${url.replace(/\/$/, "")}/storage/v1/object/${BUCKET}/${path}`, { headers, signal: AbortSignal.timeout(20_000) });
  } catch {
    return res.status(502).json({ error: "Storage unavailable" });
  }
  res.setHeader("Cache-Control", "private, max-age=300");
  if (r.status === 304) return res.status(304).end();
  if (r.status === 400 || r.status === 404) return res.status(404).json({ error: "Not built yet" });
  if (!r.ok) return res.status(502).json({ error: "Storage error" });
  const etag = r.headers.get("etag");
  const lm = r.headers.get("last-modified");
  if (etag) res.setHeader("ETag", etag);
  if (lm) res.setHeader("Last-Modified", lm);
  res.setHeader("Content-Type", "application/json");
  const buf = Buffer.from(await r.arrayBuffer());
  return res.status(200).send(buf);
}
