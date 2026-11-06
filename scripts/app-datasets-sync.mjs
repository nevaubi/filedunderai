// Uploads datasets built by the partner app's ingest (company-showcase-launchpad scripts/ingest.ts)
// into our private `app-datasets` bucket, merging universe.json by ticker so chunked runs add up.
//   node scripts/app-datasets-sync.mjs <dir> [--tickers=A,B]
//   node scripts/app-datasets-sync.mjs --merge-only     (rebuild universe.json from entries/)
// Each company's universe entry is also stored as entries/<T>.json, so parallel runs can't lose
// each other's entries; --merge-only rebuilds universe.json from them after the runs finish.
// <dir> holds companies/*.json, prices/*.json and universe.json (the ingest output folder).
// Needs SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY.
import { readFile, readdir } from "node:fs/promises";
import { existsSync } from "node:fs";
import { join } from "node:path";

const BUCKET = "app-datasets";
const URL_ = (process.env.SUPABASE_URL || "").replace(/\/$/, "");
const KEY = process.env.SUPABASE_SERVICE_ROLE_KEY || "";
const auth = { apikey: KEY, Authorization: `Bearer ${KEY}` };

export function mergeUniverse(prev, next, order = null) {
  const by = new Map((prev?.companies || []).map((c) => [c.ticker, c]));
  for (const c of next?.companies || []) if (c?.ticker) by.set(c.ticker, c);
  let companies = [...by.values()];
  if (order) {
    const pos = new Map(order.map((t, i) => [t, i]));
    companies.sort((a, b) => (pos.get(a.ticker) ?? 1e9) - (pos.get(b.ticker) ?? 1e9) || a.ticker.localeCompare(b.ticker));
  } else companies.sort((a, b) => a.ticker.localeCompare(b.ticker));
  return { generatedAt: new Date().toISOString(), companies };
}

async function ensureBucket() {
  const r = await fetch(`${URL_}/storage/v1/bucket/${BUCKET}`, { headers: auth });
  if (r.ok) {
    const b = await r.json();
    if (b.public) throw new Error(`${BUCKET} is public; it must stay private`);
    return;
  }
  const c = await fetch(`${URL_}/storage/v1/bucket`, {
    method: "POST",
    headers: { ...auth, "Content-Type": "application/json" },
    body: JSON.stringify({ id: BUCKET, name: BUCKET, public: false, file_size_limit: 20 * 1024 * 1024, allowed_mime_types: ["application/json"] }),
  });
  if (!c.ok) throw new Error(`create bucket: ${c.status} ${await c.text()}`);
}

async function put(path, body) {
  for (let i = 0; i < 4; i++) {
    const r = await fetch(`${URL_}/storage/v1/object/${BUCKET}/${path}`, {
      method: "POST",
      headers: { ...auth, "Content-Type": "application/json", "x-upsert": "true", "Cache-Control": "max-age=300" },
      body,
    });
    if (r.ok) return true;
    console.warn("upload retry", path, r.status, (await r.text()).slice(0, 200));
    await new Promise((s) => setTimeout(s, 2000 * (i + 1)));
  }
  return false;
}

const symbolFile = (s) => s.replace(/\^/g, "IDX_").replace(/[^A-Za-z0-9_.-]/g, "_");

async function listEntries() {
  const out = [];
  for (let offset = 0; ; offset += 1000) {
    const r = await fetch(`${URL_}/storage/v1/object/list/${BUCKET}`, {
      method: "POST",
      headers: { ...auth, "Content-Type": "application/json" },
      body: JSON.stringify({ prefix: "entries/", limit: 1000, offset, sortBy: { column: "name", order: "asc" } }),
    });
    if (!r.ok) throw new Error(`list entries: ${r.status}`);
    const page = await r.json();
    out.push(...page.map((o) => o.name).filter((n) => n.endsWith(".json")));
    if (page.length < 1000) return out;
  }
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

async function mergeOnly() {
  const names = await listEntries();
  const entries = (await pool(names, 12, (n) => getJson(`entries/${n}`))).filter((e) => e?.ticker);
  const prev = await getJson("universe.json");
  const merged = mergeUniverse(prev, { companies: entries }, prev?.companies?.map((c) => c.ticker));
  if (!(await put("universe.json", JSON.stringify(merged)))) throw new Error("universe upload failed");
  console.log(`app-datasets: universe rebuilt from ${entries.length} entries (${merged.companies.length} companies)`);
}

async function getJson(path) {
  const r = await fetch(`${URL_}/storage/v1/object/${BUCKET}/${path}`, { headers: auth });
  return r.ok ? r.json() : null;
}

async function main() {
  const [dir, ...rest] = process.argv.slice(2);
  if (dir === "--merge-only" && URL_ && KEY) return mergeOnly();
  if (!dir || !URL_ || !KEY) throw new Error("usage: node scripts/app-datasets-sync.mjs <dir>  (SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)");
  const only = rest.find((a) => a.startsWith("--tickers="))?.slice(10).split(",").filter(Boolean);
  await ensureBucket();
  const staged = existsSync(join(dir, "universe.json")) ? JSON.parse(await readFile(join(dir, "universe.json"), "utf8")) : { companies: [] };
  const want = new Set(only || staged.companies.map((c) => c.ticker));
  const wantPrices = new Set([...want].map(symbolFile));
  const okTickers = new Set();
  let files = 0, failed = 0;
  for (const kind of ["companies", "prices"]) {
    const d = join(dir, kind);
    if (!existsSync(d)) continue;
    for (const f of (await readdir(d)).filter((x) => x.endsWith(".json"))) {
      const t = f.slice(0, -5);
      if (kind === "companies" && !want.has(t)) continue;
      if (kind === "prices" && only && !wantPrices.has(t)) continue;
      if (await put(`${kind}/${f}`, await readFile(join(d, f)))) {
        files++;
        if (kind === "companies") okTickers.add(t);
      } else failed++;
    }
  }
  const next = { companies: staged.companies.filter((c) => okTickers.has(c.ticker)) };
  for (const c of next.companies) if (!(await put(`entries/${c.ticker}.json`, JSON.stringify(c)))) failed++;
  const prev = await getJson("universe.json");
  const merged = mergeUniverse(prev, next, prev?.companies?.map((c) => c.ticker));
  if (!(await put("universe.json", JSON.stringify(merged)))) failed++;
  console.log(`app-datasets: ${files} files, ${okTickers.size} companies updated, universe ${merged.companies.length}, ${failed} failed`);
  if (failed) process.exitCode = 1;
}

if (import.meta.url === `file://${process.argv[1]}`) main().catch((e) => {
  console.error(e.message);
  process.exit(1);
});
