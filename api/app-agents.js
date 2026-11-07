// /api/app-agents — scheduled agent tasks, monitors, inbox and interest learning for the partner app (Lovable).
// GET                      → the user's agents state (tasks, monitors, inbox index, interests, templates)
// POST {action: ...}       → see applyAction in ./_lib/agents.js; plus "inbox.item" {id} (full item with signed file URLs)
// Auth: the partner app's Supabase session token (checked with APP_AUTH_URL/APP_AUTH_KEY, like /api/app-data).
// State lives in the private `agents` bucket and every write takes the user's lease (shared with the scheduler).
// The work itself runs in the scheduler (api/cron/agents.py, every 10 minutes); "run now" and new monitors also
// poke it for this user so results arrive in a minute or two instead of at the next tick.
import { bearer, corsHeaders, originAllowed } from "./_lib/appdata.js";
import { readBody } from "./_lib/guard.js";
import { appUser, applyAction, blankState, publicState, storageClient, templates, upgrade } from "./_lib/agents.js";

function poke(uid) {
  const secret = process.env.CRON_SECRET;
  const host = process.env.VERCEL_PROJECT_PRODUCTION_URL || process.env.VERCEL_URL;
  if (!secret || !host) return Promise.resolve(false);
  // Fire and forget: the scheduler run continues on its own after this request ends.
  return fetch(`https://${host}/api/cron/agents?uid=${uid}&budget=600`, { headers: { Authorization: `Bearer ${secret}` }, signal: AbortSignal.timeout(1500) })
    .then(() => true)
    .catch(() => true);
}

export default async function handler(req, res) {
  res.setHeader("Cache-Control", "no-store");
  const origin = req.headers.origin || "";
  const allowed = origin && originAllowed(origin, process.env.APP_DATA_ORIGINS);
  if (allowed) {
    for (const [k, v] of Object.entries(corsHeaders(origin))) res.setHeader(k, v);
    res.setHeader("Access-Control-Allow-Methods", "GET, POST, OPTIONS");
    res.setHeader("Access-Control-Allow-Headers", "Authorization, Content-Type");
  }
  if (req.method === "OPTIONS") return res.status(allowed ? 204 : 403).end();
  if (!["GET", "POST"].includes(req.method)) return res.status(405).json({ error: "Use GET or POST" });
  if (origin && !allowed) return res.status(403).json({ error: "Origin not allowed" });

  const authUrl = process.env.APP_AUTH_URL, authKey = process.env.APP_AUTH_KEY;
  const url = process.env.SUPABASE_URL, key = process.env.SUPABASE_SERVICE_ROLE_KEY;
  if (!authUrl || !authKey || !url || !key) return res.status(503).json({ error: "Agents are not configured" });
  const token = bearer(req.headers.authorization);
  if (!token) return res.status(401).json({ error: "Sign in required" });
  let user;
  try {
    user = await appUser(token, { authUrl, authKey });
  } catch {
    return res.status(502).json({ error: "Could not confirm the session" });
  }
  if (!user) return res.status(401).json({ error: "Session expired or invalid" });

  const st = storageClient({ url, key });
  const path = `u/${user.id}/state.json`;
  const extra = () => ({ templates: templates(), email_configured: !!(process.env.RESEND_API_KEY && (process.env.AGENTS_FROM_EMAIL || process.env.MONITORING_FROM_EMAIL)), email: user.email });

  try {
    if (req.method === "GET") {
      const s = upgrade((await st.get(path)) || blankState(user.id), user.id);
      return res.status(200).json(publicState(s, extra()));
    }
    const body = readBody(req);
    if (JSON.stringify(body).length > 120_000) return res.status(413).json({ error: "Request too large" });

    if (body.action === "inbox.item") {
      if (!/^[0-9a-f]{16}$/.test(String(body.id || ""))) return res.status(400).json({ error: "Invalid id" });
      const item = await st.get(`u/${user.id}/inbox/${body.id}.json`);
      if (!item) return res.status(404).json({ error: "Not found" });
      item.files = await Promise.all((item.files || []).map(async (f) => ({ ...f, url: f.path?.startsWith(`u/${user.id}/`) ? await st.sign(f.path, 3600) : null, path: undefined })));
      return res.status(200).json({ item });
    }

    let out;
    let pokeIt = false;
    await st.withLease(user.id, async () => {
      const s = upgrade((await st.get(path)) || blankState(user.id), user.id);
      if (user.email && s.email !== user.email) s.email = user.email;
      out = applyAction(s, body);
      if (body.action === "inbox.delete" && out.deleted) await st.remove([`u/${user.id}/inbox/${out.deleted}.json`]);
      s.updated_at = new Date().toISOString();
      await st.put(path, s);
      pokeIt = ["task.run", "monitor.check", "insights.run", "suggestion.accept"].includes(body.action) || (body.action === "monitor.save" && !body.monitor?.id) || (body.action === "task.save" && body.run_now);
      out = { ...out, state: publicState(s, extra()) };
    });
    if (pokeIt) await poke(user.id);
    return res.status(200).json(out);
  } catch (e) {
    const status = e.status >= 400 && e.status < 600 ? e.status : 502;
    return res.status(status).json({ error: e.message || "Agents are unavailable" });
  }
}
