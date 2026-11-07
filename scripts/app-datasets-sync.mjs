// Uploads datasets built by the partner app's ingest (company-showcase-launchpad scripts/ingest.ts)
// into our private `app-datasets` bucket.
//   node scripts/app-datasets-sync.mjs <dir> [--tickers=A,B]
//   node scripts/app-datasets-sync.mjs --merge-only     (rebuild universe.json from entries/)
// <dir> holds companies/*.json, prices/*.json and universe.json (the ingest output folder).
// Each company's universe entry is stored as entries/<T>.json, so parallel runs can't lose each
// other's entries; --merge-only rebuilds universe.json from them after the runs finish.
//
// Two ways to authenticate:
//   - SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY (local runs): direct storage writes.
//   - Inside GitHub Actions with `id-token: write` and no service key: the workflow's OIDC token
//     goes to APP_DATASETS_API (default https://filedunderai.vercel.app/api/app-datasets-upload), which
//     returns signed upload URLs and runs the merge. No stored secret is needed.
import { readFile, readdir } from "node:fs/promises";
import { existsSync } from "node:fs";
import { join } from "node:path";

const BUCKET = "app-datasets";
const AUDIENCE = "filedunder-app-datasets";
const URL_ = (process.env.SUPABASE_URL || "").replace(/\/$/, "");
const KEY = process.env.SUPABASE_SERVICE_ROLE_KEY || "";
const API = process.env.APP_DATASETS_API || "https://filedunderai.vercel.app/api/app-datasets-upload";
const auth = { apikey: KEY, Authorization: `Bearer ${KEY}` };
const sleep = (ms) => new Promise((s) => setTimeout(s, ms));

export function mergeUniverse(prev, next, order = null) {
  const by = new Map((prev?.companies || []).map((c) => [c.ticker, c]));
  for (const c of next?.companies || []) if (c?.ticker) by.set(c.ticker, c);
  const companies = [...by.values()];
  if (order) {
    const pos = new Map(order.map((t, i) => [t, i]));
    companies.sort((a, b) => (pos.get(a.ticker) ?? 1e9) - (pos.get(b.ticker) ?? 1e9) || a.ticker.localeCompare(b.ticker));
  } else companies.sort((a, b) => a.ticker.localeCompare(b.ticker));
  return { generatedAt: new Date().toISOString(), companies };
}

export const symbolFile = (s) => s.replace(/\^/g, "IDX_").replace(/[^A-Za-z0-9_.-]/g, "_");

/** Which files in <dir> belong to this run. */
export async function collect(dir, only) {
  const staged = existsSync(join(dir, "universe.json")) ? JSON.parse(await readFile(join(dir, "universe.json"), "utf8")) : { companies: [] };
  const want = new Set(only || staged.companies.map((c) => c.ticker));
  const wantPrices = new Set([...want].map(symbolFile));
  const files = [];
  for (const kind of ["companies", "prices"]) {
    const d = join(dir, kind);
    if (!existsSync(d)) continue;
    for (const f of (await readdir(d)).filter((x) => x.endsWith(".json"))) {
      const t = f.slice(0, -5);
      if (kind === "companies" && !want.has(t)) continue;
      if (kind === "prices" && only && !wantPrices.has(t)) continue;
      files.push({ path: `${kind}/${f}`, file: join(d, f), ticker: kind === "companies" ? t : null });
    }
  }
  return { staged, files };
}

async function pool(items, n, fn) {
  const out = new Array(items.length);
  let next = 0;
  await Promise.all(Array.from({ length: Math.min(n, items.length) }, async () => {
    while (next < items.length) {
      const i = next++;
      out[i] = await fn(items[i]);
    }
  }));
  return out;
}

// ---------- direct mode (service key) ----------
const direct = {
  async prepare() {
    const r = await fetch(`${URL_}/storage/v1/bucket/${BUCKET}`, { headers: auth });
    if (r.ok) {
      if ((await r.json()).public) throw new Error(`${BUCKET} is public; it must stay private`);
      return;
    }
    const c = await fetch(`${URL_}/storage/v1/bucket`, {
      method: "POST",
      headers: { ...auth, "Content-Type": "application/json" },
      body: JSON.stringify({ id: BUCKET, name: BUCKET, public: false, file_size_limit: 20 * 1024 * 1024, allowed_mime_types: ["application/json"] }),
    });
    if (!c.ok) throw new Error(`create bucket: ${c.status} ${await c.text()}`);
  },
  async upload(items) {
    return pool(items, 6, async ({ path, body }) => {
      for (let i = 0; i < 4; i++) {
        const r = await fetch(`${URL_}/storage/v1/object/${BUCKET}/${path}`, {
          method: "POST",
          headers: { ...auth, "Content-Type": "application/json", "x-upsert": "true", "Cache-Control": "max-age=300" },
          body,
        });
        if (r.ok) return true;
        console.warn("upload retry", path, r.status, (await r.text()).slice(0, 200));
        await sleep(2000 * (i + 1));
      }
      return false;
    });
  },
  async merge() {
    const get = async (p) => {
      const r = await fetch(`${URL_}/storage/v1/object/${BUCKET}/${p}`, { headers: auth });
      return r.ok ? r.json() : null;
    };
    const names = [];
    for (let offset = 0; ; offset += 1000) {
      const r = await fetch(`${URL_}/storage/v1/object/list/${BUCKET}`, {
        method: "POST",
        headers: { ...auth, "Content-Type": "application/json" },
        body: JSON.stringify({ prefix: "entries/", limit: 1000, offset, sortBy: { column: "name", order: "asc" } }),
      });
      if (!r.ok) throw new Error(`list entries: ${r.status}`);
      const page = await r.json();
      names.push(...page.map((o) => o.name).filter((n) => n.endsWith(".json")));
      if (page.length < 1000) break;
    }
    const entries = (await pool(names, 12, (n) => get(`entries/${n}`))).filter((e) => e?.ticker);
    const prev = await get("universe.json");
    const merged = mergeUniverse(prev, { companies: entries }, prev?.companies?.map((c) => c.ticker));
    const [ok] = await direct.upload([{ path: "universe.json", body: JSON.stringify(merged) }]);
    if (!ok) throw new Error("universe upload failed");
    return { entries: entries.length, companies: merged.companies.length };
  },
};

// ---------- GitHub OIDC mode (no stored secret) ----------
async function oidcToken() {
  const u = process.env.ACTIONS_ID_TOKEN_REQUEST_URL, t = process.env.ACTIONS_ID_TOKEN_REQUEST_TOKEN;
  if (!u || !t) throw new Error("no SUPABASE_SERVICE_ROLE_KEY and no GitHub OIDC (add `permissions: id-token: write`)");
  const r = await fetch(`${u}&audience=${encodeURIComponent(AUDIENCE)}`, { headers: { Authorization: `Bearer ${t}` } });
  if (!r.ok) throw new Error(`OIDC token request failed: ${r.status}`);
  return (await r.json()).value;
}

async function api(body) {
  for (let i = 0; i < 4; i++) {
    const token = await oidcToken();
    const r = await fetch(API, { method: "POST", headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" }, body: JSON.stringify(body) });
    if (r.ok) return r.json();
    const text = (await r.text()).slice(0, 300);
    if (r.status < 500 && r.status !== 429) throw new Error(`${API} ${r.status}: ${text}`);
    console.warn("api retry", r.status, text);
    await sleep(3000 * (i + 1));
  }
  throw new Error(`${API} kept failing`);
}

const viaApi = {
  async prepare() {},
  async upload(items) {
    const ok = new Array(items.length).fill(false);
    for (let s = 0; s < items.length; s += 100) {
      const chunk = items.slice(s, s + 100);
      const { urls } = await api({ action: "sign", paths: chunk.map((x) => x.path) });
      const done = await pool(chunk, 6, async ({ path, body }) => {
        const url = urls[path];
        if (!url) return false;
        for (let i = 0; i < 3; i++) {
          const r = await fetch(url, { method: "PUT", headers: { "Content-Type": "application/json" }, body });
          if (r.ok) return true;
          console.warn("upload retry", path, r.status, (await r.text()).slice(0, 200));
          await sleep(2000 * (i + 1));
        }
        return false;
      });
      done.forEach((v, i) => (ok[s + i] = v));
    }
    return ok;
  },
  merge: () => api({ action: "merge" }),
};

async function main() {
  const [dir, ...rest] = process.argv.slice(2);
  if (!dir) throw new Error("usage: node scripts/app-datasets-sync.mjs <dir> [--tickers=A,B] | --merge-only");
  const mode = URL_ && KEY ? direct : viaApi;
  if (dir === "--merge-only") {
    const r = await mode.merge();
    console.log(`app-datasets: universe rebuilt from ${r.entries} entries (${r.companies} companies)`);
    return;
  }
  const only = rest.find((a) => a.startsWith("--tickers="))?.slice(10).split(",").filter(Boolean);
  await mode.prepare();
  const { staged, files } = await collect(dir, only);
  const items = await Promise.all(files.map(async (f) => ({ ...f, body: await readFile(f.file) })));
  const ok = await mode.upload(items);
  const okTickers = new Set(items.filter((x, i) => ok[i] && x.ticker).map((x) => x.ticker));
  const entries = staged.companies.filter((c) => okTickers.has(c.ticker));
  const entryOk = await mode.upload(entries.map((c) => ({ path: `entries/${c.ticker}.json`, body: JSON.stringify(c) })));
  const failed = ok.filter((v) => !v).length + entryOk.filter((v) => !v).length;
  console.log(`app-datasets: ${ok.filter(Boolean).length} files, ${okTickers.size} companies, ${failed} failed`);
  if (failed) process.exitCode = 1;
}

if (import.meta.url === `file://${process.argv[1]}`) main().catch((e) => {
  console.error(e.message);
  process.exit(1);
});
