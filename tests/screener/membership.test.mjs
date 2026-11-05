import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import { JSDOM } from "jsdom";
import { buildSpec } from "../../api/screen.js";

const app = fs.readFileSync("public/app.js", "utf8");
const specCode = fs.readFileSync("public/pages/screenspec.js", "utf8");
const companies = () => [
  { ticker: "AAA", name: "Alpha", sector: "Technology", sp500: true, sp100: true, market_cap: 1e11 },
  { ticker: "BBB", name: "Beta", sector: "Technology", sp500: true, sp100: false, market_cap: 2e11 },
  { ticker: "CCC", name: "Additional", sector: "Technology", sp500: false, sp100: false, market_cap: 3e11 },
];
const index = () => ({ universe: { id: "tracked", membership_version: 1 }, companies: companies() });

test("shared validation accepts all tracked while retaining explicit legacy index choices", () => {
  const spec = globalThis.FU_SCREENSPEC;
  const cat = { fields: [], sectors: [], industries: [], tickers: ["AAA", "BBB", "CCC"] };
  assert.equal(spec.validate({}, cat).spec.index, "all");
  for (const choice of ["all", "sp500", "sp100"]) {
    const result = spec.validate({ index: choice }, cat);
    assert.deepEqual(result.errors, []);
    assert.equal(result.spec.index, choice);
  }
  assert.equal(spec.validate({ index: "nasdaq" }, cat).errors.length, 1);
});

test("assistant company screens apply explicit membership before sorting and limiting", async () => {
  const c = vm.createContext({ console, JSON, performance, DOMException });
  c.window = c;
  c.INDEX = index();
  c.METRIC_LABELS = [];
  vm.runInContext(specCode, c);
  vm.runInContext(app.slice(app.indexOf("function compactSeries("), app.indexOf("const CITE_RULE")), c);
  const screen = c.baseTools(() => ({ done() {}, fail() {} })).find(t => t.name === "screen_companies");
  assert.deepEqual(Array.from(await screen.execute({ index: "sp500", sort: "market_cap", limit: 1 }), r => r.ticker), ["BBB"]);
  assert.deepEqual(Array.from(await screen.execute({ index: "sp100" }), r => r.ticker), ["AAA"]);
  assert.deepEqual(Array.from(await screen.execute({}), r => r.ticker), ["AAA", "BBB", "CCC"]);
  await assert.rejects(screen.execute({ index: "nasdaq" }), /index/i);
});

test("screen API exposes expanded choices and returns an all-tracked spec without a repair call", async () => {
  const original = globalThis.fetch;
  let captured, modelCalls = 0;
  try {
    globalThis.fetch = async (url, init) => {
      if (url.includes("storage/")) return { ok: true, json: async () => ({
        universe: { id: "tracked", membership_version: 1 },
        cols: ["ticker", "name", "sector", "industry", "sp500", "sp100"],
        rows: [["AAA", "Alpha", "Technology", "Software", 1, 1], ["BBB", "Beta", "Technology", "Software", 1, 0], ["CCC", "Additional", "Technology", "Software", 0, 0]],
        fields: [{ id: "mcap", n: "Market cap", u: "usd", g: "val", d: "Market cap", q: [1e11, 2e11, 3e11], cov: 3 }],
        groups: [{ id: "val", n: "Valuation" }], asof: { prices: "2026-11-03" },
      }) };
      modelCalls++;
      captured = JSON.parse(init.body);
      return { ok: true, json: async () => ({ choices: [{ message: { content: JSON.stringify({ filters: [], index: "all" }) } }] }) };
    };
    const result = await buildSpec({ prompt: "Show all tracked companies" });
    assert.deepEqual(result.errors, []);
    assert.equal(result.spec.index, "all");
    assert.equal(result.retried, false);
    assert.equal(modelCalls, 1);
    const instructions = captured.messages[0].content;
    assert.match(instructions, /"index": "all"\|"sp500"\|"sp100"/);
    assert.match(instructions, /all \(all tracked; 3 companies\)/);
    assert.match(instructions, /sp500 \(S&P 500 members; 2 companies\)/);
    assert.match(instructions, /sp100 \(S&P 100 members; 1 company\)/);
  } finally { globalThis.fetch = original; }
});

test("fallback screener includes additions only in All tracked and preserves legacy saved state", () => {
  for (const [saved, choice, expected] of [[null, "all", ["AAA", "BBB", "CCC"]], [{}, "sp500", ["AAA", "BBB"]], [{ uni: "" }, "sp500", ["AAA", "BBB"]], [{ uni: "sp500" }, "sp500", ["AAA", "BBB"]], [{ uni: "sp100" }, "sp100", ["AAA"]]]) {
  const dom = new JSDOM('<main id="page"></main>', { url: "https://research.example/#screener", runScripts: "outside-only" });
  const w = dom.window, d = w.document;
  try {
    w.eval(specCode);
    w.INDEX = index();
    if (saved) w.localStorage.setItem("fu:scr", JSON.stringify({ sort: "ticker", desc: false, ...saved }));
    w.page = d.querySelector("main");
    w.watchlist = () => [];
    w.miniLogo = () => d.createElement("span");
    w.svgUse = () => d.createElement("svg");
    w.eval(app.slice(app.indexOf("const $ ="), app.indexOf("/* ---------- prices ---------- */")) + "\n" + app.slice(app.indexOf("const STATE ="), app.indexOf("function renderRail(")) + "\n" + app.slice(app.indexOf("const SCR_COLS ="), app.indexOf("function downloadText(")));
    w.pScreener();
    const tickers = () => [...d.querySelectorAll("table.scr tbody tr td:nth-child(2)")].map(n => n.textContent).sort();
    assert.deepEqual(tickers(), expected, JSON.stringify(saved));
    const selector = d.querySelector("#scr-uni");
    assert.equal(selector.value, choice);
    selector.value = "all";
    selector.dispatchEvent(new w.Event("change"));
    assert.deepEqual(tickers(), ["AAA", "BBB", "CCC"]);
    selector.value = "sp100";
    selector.dispatchEvent(new w.Event("change"));
    assert.deepEqual(tickers(), ["AAA"]);
    assert.ok(!d.querySelector("h1").textContent.includes("S&P"));
  } finally { w.close(); }
  }
});
