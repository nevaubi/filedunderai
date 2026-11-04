// Runs tests/screener_prompts.json against the screen agent and reports how many specs contain the expected filters.
//   node scripts/test_screen_prompts.mjs                 calls the /api/screen handler in-process (env from FU_ENV_FILE)
//   node scripts/test_screen_prompts.mjs --url https://filedunder.ai/api/screen
//   options: --only 3,7 (case numbers, 1-based)  --concurrency 3  --json out.json
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const args = process.argv.slice(2);
const opt = (k, d) => { const i = args.indexOf(k); return i >= 0 ? args[i + 1] : d; };
const envFile = process.env.FU_ENV_FILE;
if (envFile && fs.existsSync(envFile)) {
  for (const line of fs.readFileSync(envFile, "utf8").split("\n")) {
    const m = line.match(/^\s*([A-Z0-9_]+)\s*=\s*(.*)\s*$/);
    if (m && !process.env[m[1]]) process.env[m[1]] = m[2].replace(/^["']|["']$/g, "");
  }
}
const suite = JSON.parse(fs.readFileSync(path.join(ROOT, "tests/screener_prompts.json"), "utf8"));
const only = opt("--only") ? new Set(opt("--only").split(",").map(Number)) : null;
const url = opt("--url");
const conc = Number(opt("--concurrency", 3));

async function callHandler(body) {
  if (url) {
    const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json", Origin: new URL(url).origin }, body: JSON.stringify(body) });
    return { status: r.status, json: await r.json().catch(() => ({})) };
  }
  const { default: handler } = await import(path.join(ROOT, "api/screen.js"));
  return new Promise((resolve) => {
    const res = { code: 200, headers: {}, status(c) { this.code = c; return this; }, setHeader(k, v) { this.headers[k] = v; }, json(o) { resolve({ status: this.code, json: o }); return this; } };
    handler({ method: "POST", headers: { origin: "http://localhost:8080", "x-forwarded-for": "127.0.0.1" }, body }, res);
  });
}

const DIR = { ">": "gt", ">=": "gt", top: "gt", "<": "lt", "<=": "lt", bottom: "lt", between: "between", "=": "eq", "!=": "ne", true: "true", false: "false", exists: "exists" };
function check(c, spec) {
  const problems = [];
  const used = new Set();
  for (const e of c.filters) {
    const fields = Array.isArray(e.field) ? e.field : [e.field];
    const hit = spec.filters.findIndex((f, i) => !used.has(i) && fields.includes(f.field) && (e.dir === "any" || DIR[f.op] === e.dir || (f.op === "between" && ["gt", "lt"].includes(e.dir) && f.value?.[e.dir === "gt" ? 0 : 1] != null)) && (!e.scope || f.scope === e.scope));
    if (hit < 0) { problems.push(`missing ${fields.join("|")} ${e.dir}${e.scope ? " (" + e.scope + ")" : ""}`); continue; }
    used.add(hit);
    const f = spec.filters[hit];
    if (e.value_min != null || e.value_max != null) {
      const v = f.op === "between" ? f.value[e.dir === "lt" ? 1 : 0] : f.value;
      if (v == null || (e.value_min != null && v < e.value_min - 1e-9) || (e.value_max != null && v > e.value_max + 1e-9)) problems.push(`${f.field} value ${JSON.stringify(f.value)} outside [${e.value_min}, ${e.value_max}]`);
    }
  }
  const extra = spec.filters.length - used.size;
  if (extra > (c.max_extra ?? 1)) problems.push(`${extra} extra filters: ${spec.filters.filter((_, i) => !used.has(i)).map(f => f.field).join(", ")}`);
  for (const s of c.sectors || []) if (!spec.sectors.includes(s)) problems.push(`sector ${s} not kept`);
  if (c.industries_match) {
    const rx = new RegExp(c.industries_match, "i");
    if (!spec.industries.length || !spec.industries.every(x => rx.test(x))) problems.push(`industries ${JSON.stringify(spec.industries)} do not match /${c.industries_match}/`);
  }
  if (c.exclude_match) {
    const rx = new RegExp(c.exclude_match, "i");
    if (![...spec.exclude_industries, ...spec.exclude_sectors].some(x => rx.test(x))) problems.push(`nothing excluded matching /${c.exclude_match}/`);
  }
  if (c.index && spec.index !== c.index) problems.push(`index ${spec.index}, expected ${c.index}`);
  if (c.sort && spec.sort?.field !== c.sort) problems.push(`sort ${spec.sort?.field}, expected ${c.sort}`);
  return problems;
}

const cases = suite.cases.map((c, i) => ({ ...c, n: i + 1 })).filter(c => !only || only.has(c.n));
const results = [];
let next = 0;
async function worker() {
  while (next < cases.length) {
    const c = cases[next++];
    const t0 = Date.now();
    let out;
    try { out = await callHandler({ prompt: c.prompt, current: c.current || null }); } catch (e) { out = { status: 0, json: { error: String(e) } }; }
    const spec = out.json?.spec;
    const problems = out.status !== 200 || !spec ? [`HTTP ${out.status}: ${out.json?.error || ""} ${out.json?.detail || ""}`] : check(c, spec);
    results.push({ n: c.n, prompt: c.prompt, ok: !problems.length, problems, seconds: (Date.now() - t0) / 1000, model: out.json?.model, retried: out.json?.retried, spec });
    console.log(`${problems.length ? "FAIL" : "ok  "} ${String(c.n).padStart(2)} ${((Date.now() - t0) / 1000).toFixed(1)}s ${c.prompt}${problems.length ? "\n       " + problems.join("\n       ") + "\n       filters: " + JSON.stringify(spec?.filters || []) : ""}`);
  }
}
await Promise.all(Array.from({ length: conc }, worker));
results.sort((a, b) => a.n - b.n);
const pass = results.filter(r => r.ok).length;
console.log(`\n${pass} of ${results.length} match the expected filters (${Math.round(pass / results.length * 100)}%).`);
if (opt("--json")) fs.writeFileSync(opt("--json"), JSON.stringify({ at: new Date().toISOString(), pass, total: results.length, results }, null, 1));
process.exit(0);
