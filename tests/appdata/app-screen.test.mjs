// Partner screen agent endpoint: CORS preflight, method and auth checks (no model calls).
import test from "node:test";
import assert from "node:assert/strict";

function mockRes() {
  const r = { code: 0, headers: {}, body: null, ended: false, writableEnded: false };
  r.setHeader = (k, v) => { r.headers[k] = v; };
  r.status = (c) => { r.code = c; return r; };
  r.json = (b) => { r.body = b; r.ended = r.writableEnded = true; return r; };
  r.end = () => { r.ended = r.writableEnded = true; return r; };
  r.on = () => {};
  return r;
}

process.env.APP_AUTH_URL = "https://auth.example.supabase.co";
process.env.APP_AUTH_KEY = "pk";
process.env.FIREWORKS_API_KEY = "fw";
const { default: handler } = await import("../../api/app-screen.js");

test("preflight from the partner origin allows POST with JSON", async () => {
  const res = mockRes();
  await handler({ method: "OPTIONS", headers: { origin: "https://company-showcase-launchpad.lovable.app" } }, res);
  assert.equal(res.code, 204);
  assert.match(res.headers["Access-Control-Allow-Methods"], /POST/);
  assert.match(res.headers["Access-Control-Allow-Headers"], /Content-Type/);
});

test("other origins and methods are refused", async () => {
  let res = mockRes();
  await handler({ method: "POST", headers: { origin: "https://evil.example.com" } }, res);
  assert.equal(res.code, 403);
  res = mockRes();
  await handler({ method: "GET", headers: {} }, res);
  assert.equal(res.code, 405);
});

test("a session token or partner key is required", async () => {
  let res = mockRes();
  await handler({ method: "POST", headers: { origin: "https://company-showcase-launchpad.lovable.app" }, body: { prompt: "x" } }, res);
  assert.equal(res.code, 401);
  process.env.APP_DATA_SERVICE_KEY = "k".repeat(40);
  res = mockRes();
  await handler({ method: "POST", headers: { "x-partner-key": "wrong" }, body: { prompt: "x" } }, res);
  assert.equal(res.code, 401);
});
