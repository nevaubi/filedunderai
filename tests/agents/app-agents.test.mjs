// Partner-app agents API: validation, actions, auth, lease-guarded writes (storage and auth mocked).
import test from "node:test";
import assert from "node:assert/strict";
import { applyAction, blankState, cleanMonitor, cleanTask, parseCron, publicState, storageClient, templates } from "../../api/_lib/agents.js";

const UID = "0b1e9b5c-1111-4222-8333-944455556666";

test("cron grammar matches the scheduler's", () => {
  assert.equal(parseCron("weekly"), "0 7 * * 1");
  assert.equal(parseCron("*/15 9-16 * * 1-5"), "*/15 9-16 * * 1-5");
  assert.throws(() => parseCron("61 * * * *"), /outside/);
  assert.throws(() => parseCron("0 7 * *"), /five fields/);
});

test("tasks are validated, keep their run history and reset next_at only when the schedule changes", () => {
  const t = cleanTask({ name: "Brief", prompt: "Do it for {scope}", schedule: { cron: "0 7 * * 1" }, scope: { kind: "tickers", tickers: "aapl, msft bad!" }, deliverable: "memo", deep: true });
  assert.equal(t.scope.tickers.join(","), "AAPL,MSFT");
  assert.equal(t.deliverable, "memo");
  assert.equal(t.budget_usd, 1);
  assert.throws(() => cleanTask({ name: "", prompt: "x" }), /name/);
  assert.throws(() => cleanTask({ name: "x", prompt: "y", scope: { kind: "tickers", tickers: [] } }), /ticker/);
  const kept = cleanTask({ ...t, name: "Brief 2" }, { ...t, next_at: "2026-11-09T13:00:00Z", runs: [{ id: "r" }] });
  assert.equal(kept.next_at, "2026-11-09T13:00:00Z");
  assert.equal(kept.runs.length, 1);
  const moved = cleanTask({ ...t, schedule: { cron: "0 8 * * 1" } }, { ...t, next_at: "2026-11-09T13:00:00Z" });
  assert.equal(moved.next_at, null);
});

test("monitors need valid types and parameters", () => {
  assert.throws(() => cleanMonitor({ type: "nope" }), /Unknown/);
  assert.throws(() => cleanMonitor({ type: "price", params: { condition: "above" } }), /level/);
  assert.throws(() => cleanMonitor({ type: "screen", params: {} }), /filter/);
  const m = cleanMonitor({ type: "insider", params: { min_value: 1e6 }, scope: { kind: "watchlist" }, escalate: true });
  assert.equal(m.escalate, true);
  assert.equal(m.scope.kind, "watchlist");
});

test("actions: create, run, inbox, interests, suggestions; public state hides bookkeeping", () => {
  const s = blankState(UID);
  const { task } = applyAction(s, { action: "task.save", task: { name: "A", prompt: "p", schedule: { cron: "daily" } } });
  applyAction(s, { action: "task.run", id: task.id });
  assert.equal(s.tasks[0].run_now, true);
  const { monitor } = applyAction(s, { action: "monitor.save", monitor: { type: "upcoming", params: { within_days: 7 } } });
  assert.equal(s.monitors[0].check_now, true);
  s.monitors[0].seen = [{ key: "x", at: "t" }];
  s.inbox = [{ id: "a".repeat(16), title: "x", read_at: null }, { id: "b".repeat(16), title: "y", read_at: null }];
  applyAction(s, { action: "inbox.update", id: "a".repeat(16), read: true, pinned: true });
  assert.ok(s.inbox[0].read_at && s.inbox[0].pinned);
  const pub = publicState(s);
  assert.equal(pub.unread, 1);
  assert.equal(pub.monitors[0].seen, undefined);
  assert.equal(pub.monitors[0].seen_count, 1);
  applyAction(s, { action: "events", events: [{ kind: "view", t: "nvda" }, { kind: "evil" }] });
  assert.deepEqual(s.interests.events.map((e) => e.t), ["NVDA"]);
  applyAction(s, { action: "settings", learning: false });
  assert.equal(applyAction(s, { action: "events", events: [{ kind: "view", t: "AAPL" }] }).recorded, 0);
  applyAction(s, { action: "interests.update", clear: true, pins: { tickers: ["msft"] } });
  assert.equal(s.interests.events.length, 0);
  assert.deepEqual(s.interests.pins.tickers, ["MSFT"]);
  s.suggestions = [{ name: "flags", type: "filing_change", params: { tags: ["going concern"] }, scope: { kind: "watchlist" } }];
  applyAction(s, { action: "suggestion.accept", index: 0 });
  assert.equal(s.monitors[0].type, "filing_change");
  assert.equal(s.suggestions.length, 0);
  assert.throws(() => applyAction(s, { action: "task.delete", id: "../x" }), /Invalid id/);
  assert.throws(() => applyAction(s, { action: "drop_tables" }), /Unknown action/);
  applyAction(s, { action: "monitor.delete", id: monitor.id });
});

test("templates load from etl/agents/templates.json", () => {
  const t = templates();
  assert.ok(t.tasks.length >= 8 && t.monitors.length >= 6);
  for (const x of t.tasks) parseCron(x.cron);
  for (const m of t.monitors) cleanMonitor(m);
});

test("lease is held during writes and released", async () => {
  const calls = [];
  let held = null;
  const fetchImpl = async (url, opts = {}) => {
    calls.push(url);
    if (url.includes("acquire_etl_lease")) {
      const b = JSON.parse(opts.body);
      if (held && held !== b.owner) return new Response("false", { status: 200 });
      held = b.owner;
      return new Response("true", { status: 200 });
    }
    if (url.includes("release_etl_lease")) {
      held = null;
      return new Response("null", { status: 200 });
    }
    return new Response("{}", { status: 200 });
  };
  const st = storageClient({ url: "https://x.supabase.co", key: "k", fetchImpl });
  let inside = null;
  await st.withLease(UID, async () => { inside = held; });
  assert.ok(inside);
  assert.equal(held, null);
  held = "someone-else";
  await assert.rejects(st.withLease(UID, async () => {}, { wait: 50 }), /busy/);
});

test("handler: CORS, auth and GET state", async () => {
  process.env.APP_AUTH_URL = "https://auth.example.supabase.co";
  process.env.APP_AUTH_KEY = "pk";
  process.env.SUPABASE_URL = "https://x.supabase.co";
  process.env.SUPABASE_SERVICE_ROLE_KEY = "svc";
  const realFetch = globalThis.fetch;
  globalThis.fetch = async (url) => {
    if (String(url).includes("/auth/v1/user")) return new Response(JSON.stringify({ id: UID, email: "a@b.co" }), { status: 200 });
    if (String(url).includes("/storage/v1/object/authenticated/")) return new Response("not found", { status: 404 });
    return new Response("{}", { status: 200 });
  };
  const { default: handler } = await import("../../api/app-agents.js");
  const mk = () => {
    const r = { code: 0, headers: {}, body: null };
    r.setHeader = (k, v) => { r.headers[k] = v; };
    r.status = (c) => { r.code = c; return r; };
    r.json = (b) => { r.body = b; return r; };
    r.end = () => r;
    return r;
  };
  try {
    let res = mk();
    await handler({ method: "OPTIONS", headers: { origin: "https://filedunder.ai" } }, res);
    assert.equal(res.code, 204);
    assert.match(res.headers["Access-Control-Allow-Methods"], /POST/);
    res = mk();
    await handler({ method: "GET", headers: { origin: "https://evil.example.com" } }, res);
    assert.equal(res.code, 403);
    res = mk();
    await handler({ method: "GET", headers: {} }, res);
    assert.equal(res.code, 401);
    res = mk();
    await handler({ method: "GET", headers: { authorization: "Bearer " + "t".repeat(40) } }, res);
    assert.equal(res.code, 200);
    assert.equal(res.body.email, "a@b.co");
    assert.ok(res.body.templates.tasks.length);
    assert.deepEqual(res.body.tasks, []);
  } finally {
    globalThis.fetch = realFetch;
  }
});
