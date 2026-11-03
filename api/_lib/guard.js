// Request guard for the public API routes: same-site origin check plus per-IP and global daily caps
// backed by the bump_usage() function in Postgres.
const ALLOWED_HOSTS = [/^filedunder\.ai$/, /^www\.filedunder\.ai$/, /^filedunderai(-[a-z0-9-]+)?\.vercel\.app$/, /^localhost(:\d+)?$/, /^127\.0\.0\.1(:\d+)?$/];

function hostOf(u) {
  try { return new URL(u).host; } catch { return ""; }
}

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

export async function guard(req, res, { name, perIp, global }) {
  const origin = req.headers.origin || req.headers.referer || "";
  const host = hostOf(origin);
  if (!host || !ALLOWED_HOSTS.some(rx => rx.test(host))) {
    res.status(403).json({ error: "This endpoint only serves filedunder.ai" });
    return false;
  }
  const ip = String(req.headers["x-forwarded-for"] || "").split(",")[0].trim() || "unknown";
  const day = new Date().toISOString().slice(0, 10);
  try {
    const [nIp, nAll] = await Promise.all([bump(`${name}:${day}:${ip}`), bump(`${name}:${day}:all`)]);
    if (nIp > perIp || nAll > global) {
      res.status(429).json({ error: "Daily limit reached. Try again tomorrow." });
      return false;
    }
  } catch {
    // If the counter is unavailable, fail open rather than block research.
  }
  return true;
}

export function readBody(req) {
  if (req.body && typeof req.body === "object") return req.body;
  try { return JSON.parse(req.body || "{}"); } catch { return {}; }
}
