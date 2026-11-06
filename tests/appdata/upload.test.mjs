// GitHub OIDC-gated upload signer for the app-datasets workflow.
import test from "node:test";
import assert from "node:assert/strict";
import { generateKeyPairSync, createSign } from "node:crypto";
import { verifyGithubOidc, _setJwks, ISSUER } from "../../api/_lib/ghoidc.js";

const { privateKey, publicKey } = generateKeyPairSync("rsa", { modulusLength: 2048 });
const jwk = { ...publicKey.export({ format: "jwk" }), kid: "k1", alg: "RS256", use: "sig" };
const enc = (o) => Buffer.from(JSON.stringify(o)).toString("base64url");
function jwt(claims, kid = "k1", key = privateKey) {
  const head = enc({ alg: "RS256", kid, typ: "JWT" });
  const body = enc(claims);
  const sig = createSign("RSA-SHA256").update(`${head}.${body}`).sign(key).toString("base64url");
  return `${head}.${body}.${sig}`;
}
const now = Math.floor(Date.now() / 1000);
const good = { iss: ISSUER, aud: "filedunder-app-datasets", exp: now + 300, repository: "nevaubi/filedunderai", ref: "refs/heads/main", run_id: "9" };
const expect = { repository: "nevaubi/filedunderai", ref: "refs/heads/main" };
const noFetch = async () => ({ ok: true, json: async () => ({ keys: [jwk] }) });

test("verifyGithubOidc accepts only signed, scoped, unexpired tokens", async () => {
  _setJwks([jwk]);
  const o = { audience: "filedunder-app-datasets", expect, fetchImpl: noFetch };
  assert.equal((await verifyGithubOidc(jwt(good), o)).run_id, "9");
  assert.equal(await verifyGithubOidc(jwt({ ...good, repository: "someone/fork" }), o), null);
  assert.equal(await verifyGithubOidc(jwt({ ...good, ref: "refs/heads/dev" }), o), null);
  assert.equal(await verifyGithubOidc(jwt({ ...good, aud: "other" }), o), null);
  assert.equal(await verifyGithubOidc(jwt({ ...good, exp: now - 5 }), o), null);
  assert.equal(await verifyGithubOidc(jwt({ ...good, iss: "https://evil" }), o), null);
  const other = generateKeyPairSync("rsa", { modulusLength: 2048 }).privateKey;
  assert.equal(await verifyGithubOidc(jwt(good, "k1", other), o), null, "wrong signature");
  assert.equal(await verifyGithubOidc("a.b", o), null);
});

function mockRes() {
  const res = { code: 0, headers: {}, body: undefined };
  res.setHeader = (k, v) => (res.headers[k] = v);
  res.status = (c) => ((res.code = c), res);
  res.json = (o) => ((res.body = o), res);
  return res;
}

test("upload handler signs allowed paths and merges entries", async () => {
  _setJwks([jwk]);
  Object.assign(process.env, { SUPABASE_URL: "https://ours.supabase.co", SUPABASE_SERVICE_ROLE_KEY: "svc" });
  const { default: handler, uploadPathOk, mergeUniverse } = await import("../../api/app-datasets-upload.js");
  assert.ok(uploadPathOk("companies/NVDA.json") && uploadPathOk("entries/BRK.B.json"));
  assert.ok(!uploadPathOk("universe.json") && !uploadPathOk("../x.json") && !uploadPathOk("raw/x.json"));
  assert.deepEqual(mergeUniverse({ companies: [{ ticker: "B" }, { ticker: "A", v: 1 }] }, [{ ticker: "A", v: 2 }, { ticker: "C" }]).companies.map((c) => c.ticker + (c.v || "")), ["B", "A2", "C"]);

  const writes = [];
  const realFetch = globalThis.fetch;
  globalThis.fetch = async (url, init = {}) => {
    if (url.includes("/object/upload/sign/app-datasets/")) return { ok: true, json: async () => ({ url: url.split("/storage/v1")[1] + "?token=t" }) };
    if (url.endsWith("/object/list/app-datasets")) return { ok: true, json: async () => [{ name: "NVDA.json" }, { name: "AAPL.json" }] };
    if (url.endsWith("/entries/NVDA.json")) return { ok: true, json: async () => ({ ticker: "NVDA" }) };
    if (url.endsWith("/entries/AAPL.json")) return { ok: true, json: async () => ({ ticker: "AAPL" }) };
    if (url.endsWith("/app-datasets/universe.json") && init.method === "POST") {
      writes.push(JSON.parse(init.body));
      return { ok: true };
    }
    if (url.endsWith("/app-datasets/universe.json")) return { ok: true, json: async () => ({ companies: [{ ticker: "MSFT" }] }) };
    return { ok: false, status: 404, json: async () => ({}) };
  };
  try {
    const auth = { authorization: `Bearer ${jwt(good)}` };
    let res = mockRes();
    await handler({ method: "POST", headers: auth, body: { action: "sign", paths: ["companies/NVDA.json", "entries/NVDA.json"] } }, res);
    assert.equal(res.code, 200);
    assert.equal(res.body.urls["companies/NVDA.json"], "https://ours.supabase.co/storage/v1/object/upload/sign/app-datasets/companies/NVDA.json?token=t");

    res = mockRes();
    await handler({ method: "POST", headers: auth, body: { action: "sign", paths: ["universe.json"] } }, res);
    assert.equal(res.code, 400);

    res = mockRes();
    await handler({ method: "POST", headers: { authorization: `Bearer ${jwt({ ...good, repository: "x/y" })}` }, body: { action: "sign", paths: ["companies/NVDA.json"] } }, res);
    assert.equal(res.code, 401);

    res = mockRes();
    await handler({ method: "POST", headers: auth, body: { action: "merge" } }, res);
    assert.equal(res.code, 200);
    assert.deepEqual(res.body, { entries: 2, companies: 3 });
    assert.deepEqual(writes[0].companies.map((c) => c.ticker), ["MSFT", "AAPL", "NVDA"]);
  } finally {
    globalThis.fetch = realFetch;
  }
});
