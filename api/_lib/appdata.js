// Gated dataset access for the partner app (company-showcase-launchpad on Lovable).
// The app sends its signed-in user's Supabase access token; we confirm it with that project's
// auth server, then stream the file from our private `app-datasets` bucket. Pure helpers here,
// the HTTP handler is api/app-data.js.
import { createHash, timingSafeEqual } from "node:crypto";

export const BUCKET = "app-datasets";

const PATH_RX = [
  /^(companies|prices)\/[A-Za-z0-9_.-]{1,32}\.json$/,
  /^(universe|manifest)\.json$/,
];

// Files our own site already serves from the public-data bucket, passed through for the partner
// app so it reads them behind sign-in like everything else: as-filed statements (index, one
// gzipped file per filing, standardized values), the template map, company KPI files, and the
// estimates (consensus, analyst actions, surprises, guidance) and guidance history files.
const PUBLIC_RX = [
  /^statements\/[A-Za-z0-9_.-]{1,32}\/index\.json$/,
  /^statements\/[A-Za-z0-9_.-]{1,32}\/(std|\d{10}-\d{2}-\d{6})\.json\.gz$/,
  /^statements\/std_map\.json$/,
  /^kpis\/[A-Za-z0-9_.-]{1,32}\.json$/,
  /^(estimates|guidance)\/[A-Za-z0-9_.-]{1,32}\.json$/,
  /^screener\.json$/,
  /^kpistd\/(latest|taxonomy|map)\.json$/,
  /^kpistd\/(t|series)\/[A-Za-z0-9_.-]{1,64}\.json$/,
  /^sectors\.json$/,
  /^movers\.json$/,
  /^sectors\/(notes|[a-z0-9-]{1,80}(\.comp)?)\.json$/,
  /^tech\/(signals|breadth|states)\.json$/,
  /^tech\/(bars|events)\/[A-Za-z0-9_.^=-]{1,32}\.json$/,
  /^quant\/(methods|cross)\.json$/,
  /^quant\/(t|read|peers)\/[A-Za-z0-9_.^=-]{1,64}\.json$/,
  /^(flows|gov|altdata|insiders|insights|brief|redlines)\/[A-Za-z0-9_.-]{1,32}\.json$/,
  /^kpi_insights\/[A-Za-z0-9_.-]{1,32}\.json$/,
  /^macro\/releases\.json$/,
  /^calendars\/(ipos|dividends)\.json$/,
  /^changes\/(feed|[A-Za-z0-9_.-]{1,32})\.json$/,
];

/** Which bucket a cleaned path lives in. */
export function bucketFor(path) {
  return PUBLIC_RX.some((rx) => rx.test(path)) ? "public-data" : BUCKET;
}

const ORIGIN_RX = [
  /^filedunder\.ai$/,
  /^www\.filedunder\.ai$/,
  /^filedunderai(-[a-z0-9-]+)?\.vercel\.app$/,
  /^[a-z0-9-]+\.lovable\.app$/,
  /^[a-z0-9-]+\.lovableproject\.com$/,
  /^localhost(:\d+)?$/,
  /^127\.0\.0\.1(:\d+)?$/,
];

/** Only dataset files, no traversal, no other buckets. */
export function cleanPath(p) {
  const s = String(p || "").trim();
  if (!s || s.includes("..") || s.startsWith("/")) return null;
  return PATH_RX.some((rx) => rx.test(s)) || PUBLIC_RX.some((rx) => rx.test(s)) ? s : null;
}

export function originAllowed(origin, extra = "") {
  let host = "";
  try {
    host = new URL(origin).host;
  } catch {
    return false;
  }
  const more = String(extra || "")
    .split(",")
    .map((h) => h.trim().toLowerCase())
    .filter(Boolean);
  return ORIGIN_RX.some((rx) => rx.test(host)) || more.includes(host.toLowerCase());
}

export function bearer(header) {
  const m = /^Bearer\s+([A-Za-z0-9._~+/=-]{20,4096})$/.exec(String(header || "").trim());
  return m ? m[1] : null;
}

/** JWT `exp` (seconds) without verifying; used only to cap how long a verified token stays cached. */
export function tokenExp(token) {
  const part = token.split(".")[1];
  if (!part) return 0;
  try {
    const json = JSON.parse(Buffer.from(part.replace(/-/g, "+").replace(/_/g, "/"), "base64").toString("utf8"));
    return Number(json.exp) || 0;
  } catch {
    return 0;
  }
}

const VERIFIED = new Map();
const CACHE_MS = 5 * 60_000;
const CACHE_MAX = 2000;

export function _resetCache() {
  VERIFIED.clear();
}

/**
 * Confirms the token with the partner project's auth server (GET /auth/v1/user).
 * Returns the user id, or null when the token is not a live session there.
 */
export async function verifyUser(token, { authUrl, authKey, fetchImpl = fetch, now = Date.now() } = {}) {
  if (!token || !authUrl || !authKey) return null;
  const key = createHash("sha256").update(token).digest("hex");
  const hit = VERIFIED.get(key);
  if (hit && hit.until > now) return hit.user;
  const exp = tokenExp(token) * 1000;
  if (exp && exp <= now) return null;
  const r = await fetchImpl(`${authUrl.replace(/\/$/, "")}/auth/v1/user`, {
    headers: { apikey: authKey, Authorization: `Bearer ${token}` },
    signal: AbortSignal.timeout(8000),
  });
  if (!r.ok) return null;
  const u = await r.json().catch(() => null);
  const user = u && typeof u.id === "string" ? u.id : null;
  if (!user) return null;
  if (VERIFIED.size >= CACHE_MAX) VERIFIED.delete(VERIFIED.keys().next().value);
  VERIFIED.set(key, { user, until: Math.min(now + CACHE_MS, exp || now + CACHE_MS) });
  return user;
}

export function corsHeaders(origin) {
  return {
    "Access-Control-Allow-Origin": origin,
    "Access-Control-Allow-Methods": "GET, OPTIONS",
    "Access-Control-Allow-Headers": "Authorization, If-None-Match",
    "Access-Control-Expose-Headers": "ETag, Last-Modified",
    "Access-Control-Max-Age": "600",
    Vary: "Origin",
  };
}

/** Server-to-server access for the partner app's own server code (APP_DATA_SERVICE_KEY). */
export function serviceKeyOk(given, expected) {
  if (!given || !expected || expected.length < 32) return false;
  const a = createHash("sha256").update(String(given)).digest();
  const b = createHash("sha256").update(String(expected)).digest();
  return timingSafeEqual(a, b);
}
