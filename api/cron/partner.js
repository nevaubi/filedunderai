// GET /api/cron/partner?task=reports|supply — drives the partner app's job route
// (company-showcase-launchpad /api/public/partner/*) so standard reports and supply-chain links get
// written for every covered company without anyone opening them first.
// Daily caps (bump_usage buckets) bound the model spend on the partner's side:
//   PARTNER_REPORTS_DAILY (default 250 reports), PARTNER_SUPPLY_DAILY (default 120 companies).
import { timingSafeEqual, createHash } from "node:crypto";
import { drain, planReports, planSupply } from "../_lib/partnerjobs.js";

const APP = (process.env.PARTNER_APP_URL || "https://company-showcase-launchpad.lovable.app").replace(/\/$/, "");
const RUN_MS = 690_000;

function secretMatches(given, expected) {
  const d = (v) => createHash("sha256").update(String(v || "")).digest();
  return !!given && timingSafeEqual(d(given), d(expected));
}

function sb() {
  const url = (process.env.SUPABASE_URL || "").replace(/\/$/, ""), key = process.env.SUPABASE_SERVICE_ROLE_KEY || "";
  return { url, auth: { apikey: key, Authorization: `Bearer ${key}` } };
}

async function bump(bucket) {
  const s = sb();
  const r = await fetch(`${s.url}/rest/v1/rpc/bump_usage`, {
    method: "POST",
    headers: { ...s.auth, "Content-Type": "application/json" },
    body: JSON.stringify({ p_bucket: bucket, p_limit: 0 }),
  });
  if (!r.ok) throw new Error(`usage counter ${r.status}`);
  return Number(await r.json()) || 0;
}

async function peek(bucket) {
  const s = sb();
  const r = await fetch(`${s.url}/rest/v1/api_usage?bucket=eq.${encodeURIComponent(bucket)}&select=count`, { headers: s.auth });
  if (!r.ok) return 0;
  const rows = await r.json();
  return Number(rows[0]?.count) || 0;
}

async function coveredTickers() {
  const s = sb();
  const r = await fetch(`${s.url}/storage/v1/object/app-datasets/universe.json`, { headers: s.auth });
  if (!r.ok) throw new Error(`universe ${r.status}`);
  return ((await r.json()).companies || []).map((c) => c.ticker);
}

async function call(task, body, timeoutMs = 290_000) {
  try {
    const r = await fetch(`${APP}/api/public/partner/${task}`, {
      method: "POST",
      headers: { "x-partner-key": process.env.APP_DATA_SERVICE_KEY, "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
      signal: AbortSignal.timeout(timeoutMs),
    });
    return { http: r.status, body: await r.json().catch(() => ({})) };
  } catch (e) {
    return { http: 0, body: { error: String(e.message || e) } };
  }
}

export default async function handler(req, res) {
  res.setHeader("Cache-Control", "no-store");
  if (!process.env.CRON_SECRET || !secretMatches(req.headers.authorization, `Bearer ${process.env.CRON_SECRET}`)) return res.status(401).json({ error: "Unauthorized" });
  if (!process.env.APP_DATA_SERVICE_KEY) return res.status(503).json({ error: "APP_DATA_SERVICE_KEY not set" });
  const task = req.query?.task === "supply" ? "supply" : "reports";
  const deadline = Date.now() + RUN_MS;
  const day = new Date().toISOString().slice(0, 10);
  try {
    const [tickers, st] = await Promise.all([coveredTickers(), call("status", {}, 60_000)]);
    if (st.http !== 200) return res.status(502).json({ error: `partner status ${st.http}`, detail: st.body.error });

    if (task === "reports") {
      const cap = Number(process.env.PARTNER_REPORTS_DAILY || 250);
      let used = await peek(`partner-reports:${day}`);
      const todo = planReports(tickers, st.body.reports || []);
      let stop = null;
      const results = await drain(todo, 3, deadline, async (item) => {
        if (used >= cap) return (stop = "daily cap"), null;
        used = await bump(`partner-reports:${day}`);
        const r = await call("report", item);
        if (r.http === 429 || r.body.limit) stop = "partner writing limit";
        if (r.http === 401) stop = "partner key rejected";
        return { ...item, http: r.http, state: r.body.status ?? null, title: r.body.title ?? null, error: r.body.error ? String(r.body.error).slice(0, 160) : null, ms: r.body.ms ?? null };
      }, () => !!stop);
      const done = results.filter(Boolean);
      return res.status(200).json({ task, queued: todo.length, attempted: done.length, ok: done.filter((x) => x.http === 200 && x.state === "ready" && !x.error).length, stop, usedToday: used, cap, sample: done.slice(0, 20) });
    }

    const cap = Number(process.env.PARTNER_SUPPLY_DAILY || 120);
    let used = await peek(`partner-supply:${day}`);
    const todo = planSupply(tickers, st.body.supply || {});
    let stop = null;
    const results = await drain(todo, 2, deadline, async (ticker) => {
      if (used >= cap) return (stop = "daily cap"), null;
      used = await bump(`partner-supply:${day}`);
      const r = await call("supply", { ticker });
      if (r.http === 401) stop = "partner key rejected";
      return { ticker, http: r.http, state: r.body.status ?? null, links: r.body.links ?? 0, error: r.body.error ? String(r.body.error).slice(0, 160) : null };
    }, () => !!stop);
    const done = results.filter(Boolean);
    return res.status(200).json({ task, queued: todo.length, attempted: done.length, stored: done.filter((x) => x.state === "stored").length, stop, usedToday: used, cap, sample: done.slice(0, 20) });
  } catch (e) {
    return res.status(502).json({ error: String(e.message || e).slice(0, 300) });
  }
}
