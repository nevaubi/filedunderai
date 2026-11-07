// Gated partner dataset endpoint: path allowlist, origins, token verification and the handler.
import test from "node:test";
import assert from "node:assert/strict";
import { cleanPath, originAllowed, bearer, tokenExp, verifyUser, _resetCache } from "../../api/_lib/appdata.js";

const jwt = (payload) => `h.${Buffer.from(JSON.stringify(payload)).toString("base64url")}.sig-xxxxxxxxxxxx`;

test("cleanPath allows dataset files only", () => {
  assert.equal(cleanPath("companies/NVDA.json"), "companies/NVDA.json");
  assert.equal(cleanPath("prices/IDX_GSPC.json"), "prices/IDX_GSPC.json");
  assert.equal(cleanPath("universe.json"), "universe.json");
  assert.equal(cleanPath("companies/BRK.B.json"), "companies/BRK.B.json");
  for (const bad of ["../raw/x.json", "/companies/NVDA.json", "companies/../universe.json", "raw/NVDA.json", "companies/NVDA.txt", "companies/a/b.json", "", null]) {
    assert.equal(cleanPath(bad), null, String(bad));
  }
});

test("originAllowed covers partner hosts and extras", () => {
  assert.ok(originAllowed("https://company-showcase-launchpad.lovable.app"));
  assert.ok(originAllowed("https://id-preview--abc123.lovable.app"));
  assert.ok(originAllowed("https://www.filedunder.ai"));
  assert.ok(originAllowed("http://localhost:8080"));
  assert.ok(!originAllowed("https://evil.example.com"));
  assert.ok(!originAllowed("https://lovable.app.evil.com"));
  assert.ok(originAllowed("https://app.example.com", "app.example.com, other.com"));
  assert.ok(!originAllowed("not a url"));
});

test("bearer and tokenExp", () => {
  assert.equal(bearer("Bearer abcdefghijklmnopqrstuvwxyz"), "abcdefghijklmnopqrstuvwxyz");
  assert.equal(bearer("Basic abc"), null);
  assert.equal(bearer(""), null);
  assert.equal(tokenExp(jwt({ exp: 123 })), 123);
  assert.equal(tokenExp("garbage"), 0);
});

test("verifyUser confirms with the auth server and caches", async () => {
  _resetCache();
  let calls = 0;
  const fetchImpl = async (url, init) => {
    calls++;
    assert.equal(url, "https://p.supabase.co/auth/v1/user");
    assert.equal(init.headers.apikey, "pub");
    return { ok: init.headers.Authorization.endsWith("good"), json: async () => ({ id: "user-1" }) };
  };
  const now = 1_000_000_000_000;
  const good = jwt({ exp: now / 1000 + 3600 }) + "good";
  const opts = { authUrl: "https://p.supabase.co/", authKey: "pub", fetchImpl, now };
  assert.equal(await verifyUser(good, opts), "user-1");
  assert.equal(await verifyUser(good, opts), "user-1");
  assert.equal(calls, 1);
  assert.equal(await verifyUser(jwt({ exp: now / 1000 + 60 }) + "bad", opts), null);
  assert.equal(await verifyUser(jwt({ exp: now / 1000 - 1 }) + "good", opts), null, "expired tokens never reach the server");
  assert.equal(calls, 2);
  assert.equal(await verifyUser(good, { ...opts, authKey: "" }), null);
});

function mockRes() {
  const res = { code: 0, headers: {}, body: undefined };
  res.setHeader = (k, v) => (res.headers[k] = v);
  res.status = (c) => ((res.code = c), res);
  res.json = (o) => ((res.body = o), res);
  res.send = (b) => ((res.body = b), res);
  res.end = () => res;
  return res;
}

test("handler: auth, storage passthrough and errors", async () => {
  _resetCache();
  Object.assign(process.env, { APP_AUTH_URL: "https://p.supabase.co", APP_AUTH_KEY: "pub", SUPABASE_URL: "https://ours.supabase.co", SUPABASE_SERVICE_ROLE_KEY: "svc" });
  const { default: handler } = await import("../../api/app-data.js");
  const seen = [];
  const realFetch = globalThis.fetch;
  globalThis.fetch = async (url, init = {}) => {
    seen.push(url);
    if (url.endsWith("/auth/v1/user")) return { ok: init.headers.Authorization.endsWith("good"), json: async () => ({ id: "u1" }) };
    if (url.includes("bump_usage")) return { ok: true, json: async () => 1 };
    if (url.includes("/storage/v1/object/app-datasets/companies/NVDA.json")) {
      assert.equal(init.headers.Authorization, "Bearer svc");
      return { ok: true, status: 200, headers: new Map([["etag", '"e1"']]), arrayBuffer: async () => new TextEncoder().encode('{"profile":{}}').buffer };
    }
    return { ok: false, status: 404, headers: new Map(), arrayBuffer: async () => new ArrayBuffer(0) };
  };
  try {
    const good = jwt({ exp: Date.now() / 1000 + 3600 }) + "good";
    const base = { method: "GET", headers: { origin: "https://company-showcase-launchpad.lovable.app", authorization: `Bearer ${good}` }, query: { path: "companies/NVDA.json" } };

    let res = mockRes();
    await handler(base, res);
    assert.equal(res.code, 200);
    assert.equal(String(res.body), '{"profile":{}}');
    assert.equal(res.headers["Access-Control-Allow-Origin"], "https://company-showcase-launchpad.lovable.app");
    assert.equal(res.headers.ETag, '"e1"');

    res = mockRes();
    await handler({ ...base, headers: { origin: base.headers.origin } }, res);
    assert.equal(res.code, 401);

    res = mockRes();
    await handler({ ...base, headers: { ...base.headers, authorization: `Bearer ${jwt({ exp: Date.now() / 1000 + 60 })}bad` } }, res);
    assert.equal(res.code, 401);

    res = mockRes();
    await handler({ ...base, query: { path: "../raw/x.json" } }, res);
    assert.equal(res.code, 400);

    res = mockRes();
    await handler({ ...base, query: { path: "companies/ZZZZ.json" } }, res);
    assert.equal(res.code, 404);

    res = mockRes();
    await handler({ ...base, headers: { ...base.headers, origin: "https://evil.example.com" } }, res);
    assert.equal(res.code, 403);

    res = mockRes();
    await handler({ ...base, method: "OPTIONS" }, res);
    assert.equal(res.code, 204);
    assert.ok(!seen.some((u) => u.includes("evil")));
  } finally {
    globalThis.fetch = realFetch;
  }
});

test("mergeUniverse keeps prior entries and replaces updated ones", async () => {
  const { mergeUniverse } = await import("../../scripts/app-datasets-sync.mjs");
  const prev = { companies: [{ ticker: "B", v: 1 }, { ticker: "A", v: 1 }] };
  const out = mergeUniverse(prev, { companies: [{ ticker: "A", v: 2 }, { ticker: "C", v: 1 }] }, ["B", "A"]);
  assert.deepEqual(out.companies.map((c) => [c.ticker, c.v]), [["B", 1], ["A", 2], ["C", 1]]);
  assert.deepEqual(mergeUniverse(null, { companies: [{ ticker: "Z" }, { ticker: "Y" }] }).companies.map((c) => c.ticker), ["Y", "Z"]);
});

test("partner server key path", async () => {
  const { serviceKeyOk } = await import("../../api/_lib/appdata.js");
  const k = "k".repeat(40);
  assert.ok(serviceKeyOk(k, k));
  assert.ok(!serviceKeyOk("x", k));
  assert.ok(!serviceKeyOk(k, "short"), "short configured keys are refused");
  assert.ok(!serviceKeyOk("", k));
  _resetCache();
  Object.assign(process.env, { APP_AUTH_URL: "https://p.supabase.co", APP_AUTH_KEY: "pub", SUPABASE_URL: "https://ours.supabase.co", SUPABASE_SERVICE_ROLE_KEY: "svc", APP_DATA_SERVICE_KEY: k });
  const { default: handler } = await import("../../api/app-data.js");
  const realFetch = globalThis.fetch;
  globalThis.fetch = async (url) => {
    if (url.includes("bump_usage")) return { ok: true, json: async () => 1 };
    if (url.includes("/storage/v1/object/app-datasets/companies/NVDA.json")) return { ok: true, status: 200, headers: new Map(), arrayBuffer: async () => new TextEncoder().encode("{}").buffer };
    throw new Error("unexpected " + url);
  };
  try {
    let res = mockRes();
    await handler({ method: "GET", headers: { "x-partner-key": k }, query: { path: "companies/NVDA.json" } }, res);
    assert.equal(res.code, 200);
    res = mockRes();
    await handler({ method: "GET", headers: { "x-partner-key": "wrong" }, query: { path: "companies/NVDA.json" } }, res);
    assert.equal(res.code, 401);
    res = mockRes();
    await handler({ method: "GET", headers: { "x-partner-key": k, origin: "https://company-showcase-launchpad.lovable.app" }, query: { path: "companies/NVDA.json" } }, res);
    assert.equal(res.code, 401, "browser requests can't use the server key");
  } finally {
    globalThis.fetch = realFetch;
  }
});

test("statement and KPI passthrough paths map to public-data", async () => {
  const { cleanPath, bucketFor } = await import("../../api/_lib/appdata.js");
  for (const p of ["statements/AAPL/index.json", "statements/AAPL/std.json.gz", "statements/AAPL/0000320193-26-000020.json.gz", "statements/std_map.json", "kpis/AAPL.json", "estimates/AAPL.json", "guidance/AAPL.json", "estimates/firms.json", "screener.json", "kpistd/latest.json", "kpistd/taxonomy.json", "kpistd/t/AAPL.json", "kpistd/series/same_store_sales_growth.json", "sectors.json", "sectors/notes.json", "sectors/information-technology.json", "sectors/software.comp.json", "tech/signals.json", "tech/bars/NVDA.json", "tech/bars/CL=F.json", "tech/events/NVDA.json", "quant/t/NVDA.json", "quant/methods.json", "quant/read/NVDA.json", "flows/NVDA.json", "gov/NVDA.json", "altdata/NVDA.json", "insiders/NVDA.json", "estimates/index.json", "macro/releases.json", "calendars/ipos.json", "calendars/dividends.json", "insights/NVDA.json", "brief/NVDA.json", "redlines/index.json", "redlines/NVDA.json", "insights/index.json", "movers.json", "situations/index.json", "situations/NVDA.json", "changes/feed.json", "changes/NVDA.json"]) {
    assert.equal(cleanPath(p), p, p);
    assert.equal(bucketFor(p), "public-data", p);
  }
  assert.equal(bucketFor("companies/AAPL.json"), "app-datasets");
  for (const bad of ["statements/AAPL/../x.json", "statements/AAPL/raw.json.gz", "kpis/AAPL.json.gz", "statements/AAPL/index.json.gz", "estimates/AAPL/x.json", "estraw/backfill/x.json", "kpistd/other.json", "kpistd/t/../x.json", "sectors/Software.json", "sectors/a/b.json", "tech/bars/../x.json", "quant/other.json", "macro/registry.json", "calendars/div_docs.json", "calendars/ipo_state.json.gz", "redlines/state.json.gz", "redlines/a/b.json", "changes/state.json.gz", "changes/a/b.json"]) assert.equal(cleanPath(bad), null, bad);
});
