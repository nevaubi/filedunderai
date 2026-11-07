// POST /api/app-screen {prompt, history?, current?}
// The screen agent (api/screen.js) for the partner app on Lovable: same plain-English → screen spec
// translation, authenticated like /api/app-data (the partner app's Supabase session token, or the
// partner server key with no Origin). Tickers from the partner universe (app-datasets/universe.json)
// are accepted alongside the screener's own rows.
import { bearer, corsHeaders, originAllowed, serviceKeyOk, verifyUser } from "./_lib/appdata.js";
import { readBody } from "./_lib/guard.js";
import { buildSpec } from "./screen.js";

const PER_USER_DAILY = 300;
let UNI = null, UNI_AT = 0;

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

async function partnerTickers() {
  if (UNI && Date.now() - UNI_AT < 10 * 60e3) return UNI;
  const url = process.env.SUPABASE_URL, key = process.env.SUPABASE_SERVICE_ROLE_KEY;
  try {
    const r = await fetch(`${url.replace(/\/$/, "")}/storage/v1/object/app-datasets/universe.json`, { headers: { apikey: key, Authorization: `Bearer ${key}` }, signal: AbortSignal.timeout(12000) });
    if (!r.ok) return UNI || [];
    const d = await r.json();
    UNI = (d.companies || []).map((c) => String(c.ticker || "").toUpperCase()).filter(Boolean);
    UNI_AT = Date.now();
  } catch {
    return UNI || [];
  }
  return UNI;
}

export default async function handler(req, res) {
  const origin = req.headers.origin || "";
  const allowed = origin && originAllowed(origin, process.env.APP_DATA_ORIGINS);
  if (allowed) {
    for (const [k, v] of Object.entries(corsHeaders(origin))) res.setHeader(k, v);
    res.setHeader("Access-Control-Allow-Methods", "POST, OPTIONS");
    res.setHeader("Access-Control-Allow-Headers", "Authorization, Content-Type");
  }
  if (req.method === "OPTIONS") return res.status(allowed ? 204 : 403).end();
  if (req.method !== "POST") return res.status(405).json({ error: "Use POST" });
  if (origin && !allowed) return res.status(403).json({ error: "Origin not allowed" });

  const authUrl = process.env.APP_AUTH_URL, authKey = process.env.APP_AUTH_KEY;
  if (!authUrl || !authKey || !process.env.FIREWORKS_API_KEY) return res.status(503).json({ error: "The screen agent is not configured" });

  let user = null;
  if (req.headers["x-partner-key"] && !origin) {
    if (!serviceKeyOk(req.headers["x-partner-key"], process.env.APP_DATA_SERVICE_KEY)) return res.status(401).json({ error: "Invalid partner key" });
    user = "partner-server";
  } else {
    const token = bearer(req.headers.authorization);
    if (!token) return res.status(401).json({ error: "Sign in required" });
    try {
      user = await verifyUser(token, { authUrl, authKey });
    } catch {
      return res.status(502).json({ error: "Could not confirm the session" });
    }
    if (!user) return res.status(401).json({ error: "Session expired or invalid" });
  }

  try {
    const day = new Date().toISOString().slice(0, 10);
    const [mine, all] = await Promise.all([bump(`appscreen:${day}:${user}`), bump(`appscreen:${day}:all`)]);
    if (mine > (user === "partner-server" ? 2000 : PER_USER_DAILY) || all > 5000) return res.status(429).json({ error: "Daily screen limit reached. Try again tomorrow." });
  } catch {
    // Counter unavailable: fail open.
  }

  const body = readBody(req);
  if (JSON.stringify(body).length > 60_000) return res.status(413).json({ error: "Request too large" });
  const prompt = String(body.prompt || "").trim();
  if (!prompt) return res.status(400).json({ error: "prompt is required" });
  const ctl = new AbortController();
  const cancel = () => ctl.abort();
  res.on?.("close", () => { if (!res.writableEnded) cancel(); });
  try {
    const out = await buildSpec({
      prompt,
      history: Array.isArray(body.history) ? body.history : [],
      current: body.current || null,
      signal: ctl.signal,
      extraTickers: await partnerTickers(),
    });
    console.log(JSON.stringify({ appscreen: prompt.slice(0, 80), user: user === "partner-server" ? "server" : String(user).slice(0, 8), model: out.model, errors: out.errors.length }));
    res.setHeader("Cache-Control", "no-store");
    return res.status(200).json(out);
  } catch (e) {
    if (ctl.signal.aborted) return;
    return res.status(502).json({ error: "The screen agent did not return a usable screen. Try rephrasing.", detail: String(e.message || e).slice(0, 200) });
  }
}
