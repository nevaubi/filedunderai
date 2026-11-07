// Agents for the partner app: per-user state in the private `agents` bucket (u/{uid}/state.json, inbox items,
// files), edited under the same `agents:u:{uid}` lease the scheduler tick uses (etl/agents/store.py), so app edits
// and scheduled runs never overwrite each other. Pure validation helpers are exported for tests.
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

export const BUCKET = "agents";
export const LIMITS = { tasks: 30, monitors: 60, events: 200 };
export const MONITOR_TYPES = ["insider", "filing_change", "contracts", "estimates", "price", "tech_signal", "upcoming", "ir_document", "screen", "feed"];
export const DELIVERABLES = ["report", "dashboard", "memo", "csv", "xlsx"];
export const SCOPES = ["watchlist", "portfolio", "tickers", "sector", "universe"];
const TICKER = /^[A-Z][A-Z0-9.\-]{0,9}$/;
const ID = /^[0-9a-f]{16}$/;

let TEMPLATES = null;
export function templates() {
  if (TEMPLATES) return TEMPLATES;
  const here = dirname(fileURLToPath(import.meta.url));
  for (const p of [join(here, "../../etl/agents/templates.json"), join(process.cwd(), "etl/agents/templates.json")]) {
    try {
      TEMPLATES = JSON.parse(readFileSync(p, "utf8"));
      return TEMPLATES;
    } catch {}
  }
  return { tasks: [], monitors: [] };
}

export function newId() {
  return createHash("sha256").update(String(Math.random()) + Date.now() + process.hrtime.bigint()).digest("hex").slice(0, 16);
}

// ---- cron (same grammar as etl/agents/schedule.py)
const FIELDS = [[0, 59], [0, 23], [1, 31], [1, 12], [0, 7]];
const PRESETS = { hourly: "0 * * * *", daily: "0 7 * * *", weekdays: "0 7 * * 1-5", weekly: "0 7 * * 1", monthly: "0 7 1 * *" };
export function parseCron(expr) {
  const e = PRESETS[String(expr || "").trim().toLowerCase()] || String(expr || "").trim();
  const parts = e.split(/\s+/);
  if (parts.length !== 5) throw new Error("Schedules need five fields: minute hour day-of-month month day-of-week");
  parts.forEach((p, i) => {
    const [lo, hi] = FIELDS[i];
    for (const piece of p.split(",")) {
      const [range, step] = piece.split("/");
      if (step !== undefined && !/^\d+$/.test(step)) throw new Error(`Bad step in "${p}"`);
      if (range === "*") continue;
      const m = /^(\d+)(?:-(\d+))?$/.exec(range);
      if (!m) throw new Error(`Bad value "${piece}"`);
      const a = Number(m[1]), b = m[2] === undefined ? a : Number(m[2]);
      if (a < lo || b > hi || a > b) throw new Error(`"${piece}" is outside ${lo}-${hi}`);
    }
  });
  return e;
}

// ---- validation
const str = (v, n) => (typeof v === "string" ? v.trim().slice(0, n) : "");
const bool = (v, d) => (typeof v === "boolean" ? v : d);
function tickers(v) {
  return [...new Set((Array.isArray(v) ? v : String(v || "").split(/[\s,]+/)).map((t) => String(t).trim().toUpperCase()).filter((t) => TICKER.test(t)))].slice(0, 200);
}
export function cleanScope(s) {
  const kind = SCOPES.includes(s?.kind) ? s.kind : "watchlist";
  const out = { kind };
  if (kind === "tickers") {
    out.tickers = tickers(s.tickers);
    if (!out.tickers.length) throw new Error("Add at least one ticker");
  }
  if (kind === "sector") out.sector = str(s.sector, 60) || "Information Technology";
  return out;
}
function cleanJson(v, max = 20000) {
  const s = JSON.stringify(v ?? {});
  if (s.length > max) throw new Error("Settings are too large");
  return JSON.parse(s);
}

export function cleanTask(t, existing) {
  const base = existing || { id: newId(), created_at: new Date().toISOString(), runs: [], running: null, next_at: null };
  const name = str(t.name, 80);
  const prompt = str(t.prompt, 4000);
  if (!name) throw new Error("Give the task a name");
  if (!prompt) throw new Error("Describe what the task should do");
  const kind = t.schedule?.kind === "once" ? "once" : "cron";
  const schedule = kind === "once" ? { kind } : { kind, cron: parseCron(t.schedule?.cron || "weekly"), tz: str(t.schedule?.tz, 60) || "America/Chicago" };
  const changedSchedule = !existing || JSON.stringify(existing.schedule) !== JSON.stringify(schedule);
  return {
    ...base,
    name, prompt, notes: str(t.notes, 1500), template: str(t.template, 40) || null,
    schedule, scope: cleanScope(t.scope || {}),
    deliverable: DELIVERABLES.includes(t.deliverable) ? t.deliverable : "report",
    deep: bool(t.deep, false), enabled: bool(t.enabled, true),
    budget_usd: Math.min(5, Math.max(0.05, Number(t.budget_usd) || (t.deep ? 1 : 0.4))),
    next_at: changedSchedule ? null : base.next_at,
    updated_at: new Date().toISOString(),
  };
}

export function cleanMonitor(m, existing) {
  const base = existing || { id: newId(), created_at: new Date().toISOString(), seen: [], baseline: {}, next_at: null };
  if (!MONITOR_TYPES.includes(m.type)) throw new Error("Unknown monitor type");
  const name = str(m.name, 80) || m.type;
  const params = cleanJson(m.params || {}, 8000);
  if (m.type === "price" && ["above", "below"].includes(params.condition) && !Number.isFinite(Number(params.level))) throw new Error("Price monitors need a level");
  if (m.type === "screen" && !(Array.isArray(params.filters) && params.filters.length)) throw new Error("Screen monitors need at least one filter");
  const typeChanged = existing && existing.type !== m.type;
  return {
    ...base,
    ...(typeChanged ? { seen: [], baseline: {} } : {}),
    name, type: m.type, params, scope: cleanScope(m.scope || {}),
    escalate: bool(m.escalate, false), enabled: bool(m.enabled, true),
    cooldown_hours: Math.min(720, Math.max(0, Number(m.cooldown_hours) || 0)),
    every_minutes: m.every_minutes ? Math.min(10080, Math.max(10, Number(m.every_minutes) || 60)) : null,
    updated_at: new Date().toISOString(),
  };
}

export function blankState(uid) {
  const now = new Date().toISOString();
  return {
    version: 1, uid, email: null, tz: "America/Chicago", created_at: now, updated_at: now,
    settings: { email: false, learning: true, insights: "weekly", insights_next_at: null },
    watchlist: [], portfolio: [], tasks: [], monitors: [], inbox: [], suggestions: [],
    interests: { events: [], weights: { tickers: {}, sectors: {}, topics: {} }, pins: { tickers: [], topics: [] }, muted: { tickers: [], topics: [] }, updated_at: null },
  };
}

export function upgrade(s, uid) {
  const b = blankState(uid);
  for (const k of Object.keys(b)) if (!(k in s)) s[k] = b[k];
  s.settings = { ...b.settings, ...s.settings };
  s.interests = { ...b.interests, ...s.interests };
  s.uid = uid;
  return s;
}

const EVENT_KINDS = new Set(["view", "section", "question", "screen", "watch", "task"]);
export function cleanEvents(events) {
  return (Array.isArray(events) ? events : []).slice(0, LIMITS.events).filter((e) => EVENT_KINDS.has(e?.kind)).map((e) => {
    const out = { kind: e.kind, ts: /^\d{4}-\d{2}-\d{2}T/.test(e.ts || "") ? String(e.ts).slice(0, 24) : new Date().toISOString() };
    const t = String(e.t || "").toUpperCase();
    if (TICKER.test(t)) out.t = t;
    for (const k of ["path", "section", "q"]) if (e[k]) out[k] = String(e[k]).slice(0, 160);
    return out;
  });
}

/** Applies one user action to the state. Returns the response payload. Throws Error with .status for bad input. */
export function applyAction(s, body, { now = new Date() } = {}) {
  const bad = (msg, status = 400) => Object.assign(new Error(msg), { status });
  const a = body.action;
  const find = (list, id) => {
    if (!ID.test(String(id || ""))) throw bad("Invalid id");
    const i = s[list].findIndex((x) => x.id === id);
    if (i < 0) throw bad("Not found", 404);
    return i;
  };
  switch (a) {
    case "sync": {
      if (Array.isArray(body.watchlist)) s.watchlist = tickers(body.watchlist);
      if (Array.isArray(body.portfolio)) s.portfolio = body.portfolio.slice(0, 300).map((p) => (typeof p === "string" ? { ticker: p.toUpperCase() } : { ticker: String(p.ticker || "").toUpperCase(), weight: Number(p.weight) || null, shares: Number(p.shares) || null })).filter((p) => TICKER.test(p.ticker));
      if (body.tz && /^[A-Za-z_]+\/[A-Za-z_\-/]+$/.test(body.tz)) s.tz = String(body.tz).slice(0, 60);
      return { ok: true };
    }
    case "task.save": {
      const t = body.task || {};
      if (t.id) {
        const i = find("tasks", t.id);
        s.tasks[i] = cleanTask(t, s.tasks[i]);
        return { task: s.tasks[i] };
      }
      if (s.tasks.length >= LIMITS.tasks) throw bad(`At most ${LIMITS.tasks} tasks`);
      const task = cleanTask(t);
      if (body.run_now) task.run_now = true;
      s.tasks.unshift(task);
      return { task };
    }
    case "task.delete": {
      s.tasks.splice(find("tasks", body.id), 1);
      return { ok: true };
    }
    case "task.run": {
      const i = find("tasks", body.id);
      if (s.tasks[i].running) return { task: s.tasks[i], already: true };
      s.tasks[i].run_now = true;
      return { task: s.tasks[i] };
    }
    case "monitor.save": {
      const m = body.monitor || {};
      if (m.id) {
        const i = find("monitors", m.id);
        s.monitors[i] = cleanMonitor(m, s.monitors[i]);
        return { monitor: s.monitors[i] };
      }
      if (s.monitors.length >= LIMITS.monitors) throw bad(`At most ${LIMITS.monitors} monitors`);
      const mon = cleanMonitor(m);
      mon.check_now = true;
      s.monitors.unshift(mon);
      return { monitor: mon };
    }
    case "monitor.delete": {
      s.monitors.splice(find("monitors", body.id), 1);
      return { ok: true };
    }
    case "monitor.check": {
      const i = find("monitors", body.id);
      s.monitors[i].check_now = true;
      return { monitor: s.monitors[i] };
    }
    case "inbox.update": {
      const c = s.inbox[find("inbox", body.id)];
      if (typeof body.read === "boolean") c.read_at = body.read ? now.toISOString() : null;
      if (typeof body.pinned === "boolean") c.pinned = body.pinned;
      return { card: c };
    }
    case "inbox.read_all": {
      for (const c of s.inbox) if (!c.read_at) c.read_at = now.toISOString();
      return { ok: true };
    }
    case "inbox.delete": {
      const i = find("inbox", body.id);
      const [c] = s.inbox.splice(i, 1);
      return { deleted: c.id };
    }
    case "events": {
      if (!s.settings.learning) return { recorded: 0 };
      const ev = cleanEvents(body.events);
      s.interests.events.push(...ev);
      if (s.interests.events.length > 1500) s.interests.events = s.interests.events.slice(-1500);
      return { recorded: ev.length };
    }
    case "interests.update": {
      const it = s.interests;
      if (body.clear) {
        it.events = [];
        it.weights = { tickers: {}, sectors: {}, topics: {} };
      }
      for (const k of ["pins", "muted"]) {
        if (body[k]) {
          if (Array.isArray(body[k].tickers)) it[k].tickers = tickers(body[k].tickers).slice(0, 50);
          if (Array.isArray(body[k].topics)) it[k].topics = body[k].topics.map((x) => str(x, 40)).filter(Boolean).slice(0, 30);
        }
      }
      if (typeof body.learning === "boolean") s.settings.learning = body.learning;
      return { interests: publicInterests(s) };
    }
    case "settings": {
      if (typeof body.email === "boolean") s.settings.email = body.email;
      if (["weekly", "daily", "off"].includes(body.insights)) s.settings.insights = body.insights;
      if (typeof body.learning === "boolean") s.settings.learning = body.learning;
      return { settings: s.settings };
    }
    case "insights.run": {
      s.settings.insights_next_at = now.toISOString();
      if (s.settings.insights === "off") s.settings.insights = "weekly";
      return { settings: s.settings };
    }
    case "suggestion.accept": {
      const sug = s.suggestions?.[Number(body.index)];
      if (!sug) throw bad("Suggestion not found", 404);
      if (s.monitors.length >= LIMITS.monitors) throw bad(`At most ${LIMITS.monitors} monitors`);
      const mon = cleanMonitor(sug);
      mon.check_now = true;
      s.monitors.unshift(mon);
      s.suggestions.splice(Number(body.index), 1);
      return { monitor: mon };
    }
    default:
      throw bad("Unknown action");
  }
}

export function publicInterests(s) {
  const it = s.interests;
  return { weights: it.weights, pins: it.pins, muted: it.muted, updated_at: it.updated_at, events: it.events.length, recent: it.events.slice(-25).reverse(), learning: s.settings.learning };
}

/** What the app receives: everything except internal bookkeeping (seen keys, baselines, raw events). */
export function publicState(s, extra = {}) {
  return {
    tz: s.tz, settings: s.settings, watchlist: s.watchlist, portfolio: s.portfolio,
    tasks: s.tasks.map(({ running, ...t }) => ({ ...t, running: !!running, running_since: running?.started_at || null, runs: (t.runs || []).slice(0, 10) })),
    monitors: s.monitors.map(({ seen, baseline, ...m }) => ({ ...m, seen_count: (seen || []).length })),
    inbox: s.inbox, unread: s.inbox.filter((c) => !c.read_at).length, suggestions: s.suggestions || [],
    interests: publicInterests(s), ...extra,
  };
}

// ---- storage + lease (service role)
export function storageClient({ url, key, fetchImpl = fetch }) {
  const base = url.replace(/\/$/, "");
  const H = { apikey: key, Authorization: `Bearer ${key}` };
  async function rpc(fn, args) {
    const r = await fetchImpl(`${base}/rest/v1/rpc/${fn}`, { method: "POST", headers: { ...H, "Content-Type": "application/json" }, body: JSON.stringify(args), signal: AbortSignal.timeout(10000) });
    if (!r.ok) throw new Error(`lease rpc ${r.status}`);
    return r.json();
  }
  async function get(path) {
    const r = await fetchImpl(`${base}/storage/v1/object/authenticated/${BUCKET}/${path}?cacheNonce=${newId()}`, { headers: { ...H, "Cache-Control": "no-cache" }, signal: AbortSignal.timeout(15000) });
    if (r.status === 400 || r.status === 404) return null;
    if (!r.ok) throw new Error(`storage ${r.status}`);
    return r.json();
  }
  async function put(path, obj) {
    const r = await fetchImpl(`${base}/storage/v1/object/${BUCKET}/${path}`, {
      method: "POST", headers: { ...H, "Content-Type": "application/json", "x-upsert": "true", "cache-control": "no-store" }, body: JSON.stringify(obj), signal: AbortSignal.timeout(20000),
    });
    if (r.status === 404 || r.status === 400) {
      // First use: create the private bucket, then retry once.
      await fetchImpl(`${base}/storage/v1/bucket`, { method: "POST", headers: { ...H, "Content-Type": "application/json" }, body: JSON.stringify({ id: BUCKET, name: BUCKET, public: false }) });
      const r2 = await fetchImpl(`${base}/storage/v1/object/${BUCKET}/${path}`, { method: "POST", headers: { ...H, "Content-Type": "application/json", "x-upsert": "true" }, body: JSON.stringify(obj) });
      if (!r2.ok) throw new Error(`storage write ${r2.status}`);
      return;
    }
    if (!r.ok) throw new Error(`storage write ${r.status}`);
  }
  async function sign(path, seconds = 3600) {
    const r = await fetchImpl(`${base}/storage/v1/object/sign/${BUCKET}/${path}`, { method: "POST", headers: { ...H, "Content-Type": "application/json" }, body: JSON.stringify({ expiresIn: seconds }) });
    if (!r.ok) return null;
    const j = await r.json();
    const u = j.signedURL || j.signedUrl;
    return u ? (u.startsWith("/") ? `${base}/storage/v1${u}` : u) : null;
  }
  async function remove(paths) {
    if (!paths.length) return;
    await fetchImpl(`${base}/storage/v1/object/${BUCKET}`, { method: "DELETE", headers: { ...H, "Content-Type": "application/json" }, body: JSON.stringify({ prefixes: paths }) });
  }
  async function withLease(uid, fn, { wait = 15000 } = {}) {
    const owner = newId();
    const name = `agents:u:${uid}`;
    const end = Date.now() + wait;
    for (;;) {
      if ((await rpc("acquire_etl_lease", { lock_name: name, owner, lease_seconds: 60 })) === true) break;
      if (Date.now() > end) throw Object.assign(new Error("Your agents are busy writing a result; try again in a moment"), { status: 409 });
      await new Promise((r) => setTimeout(r, 700));
    }
    try {
      return await fn();
    } finally {
      await rpc("release_etl_lease", { lock_name: name, owner }).catch(() => {});
    }
  }
  return { get, put, sign, remove, withLease, rpc };
}

// ---- auth (the partner app's Supabase session) with email
const USERS = new Map();
export async function appUser(token, { authUrl, authKey, fetchImpl = fetch, now = Date.now() }) {
  const k = createHash("sha256").update(token).digest("hex");
  const hit = USERS.get(k);
  if (hit && hit.until > now) return hit.user;
  const r = await fetchImpl(`${authUrl.replace(/\/$/, "")}/auth/v1/user`, { headers: { apikey: authKey, Authorization: `Bearer ${token}` }, signal: AbortSignal.timeout(8000) });
  if (!r.ok) return null;
  const u = await r.json().catch(() => null);
  if (!u || typeof u.id !== "string") return null;
  const user = { id: u.id, email: typeof u.email === "string" ? u.email : null };
  if (USERS.size > 2000) USERS.delete(USERS.keys().next().value);
  USERS.set(k, { user, until: now + 5 * 60_000 });
  return user;
}
