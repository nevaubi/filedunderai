import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import { JSDOM } from "jsdom";

const code = fs.readFileSync("public/pages/compare.js", "utf8");
const specCode = fs.readFileSync("public/pages/screenspec.js", "utf8");
const bundle = () => ({
  universe: { id: "tracked", membership_version: 1 }, generated: "2026-11-04T00:00:00Z",
  companies: [
    ...Array.from({ length: 50 }, (_, i) => ({ ticker: "S" + String(i).padStart(2, "0"), name: "Member " + i, sector: "Technology", sp500: true, sp100: i < 10, market_cap: (i + 1) * 1e9, pe_ttm: i + 1 })),
    { ticker: "NEW", name: "Additional issuer", sector: "Technology", sp500: false, sp100: false, market_cap: 1.1e9, pe_ttm: 1000 },
    { ticker: "ALT", name: "Another issuer", sector: "Technology", sp500: false, sp100: false, market_cap: 1.2e9, pe_ttm: 2000 },
  ],
});
const screener = index => ({
  universe: index.universe, generated: index.generated,
  cols: ["ticker", "name", "sp500", "sp100", "cik", "pe_ttm", "quality", "operating_cash_flow", "ev_ebitda"],
  fields: [{ id: "pe_ttm", u: "x" }, { id: "quality", u: "num" }, { id: "operating_cash_flow", n: "Operating cash flow", u: "usd" }, { id: "ev_ebitda", n: "EV / EBITDA", u: "x" }],
  rows: index.companies.map((c, i) => [c.ticker, c.name, Number(c.sp500), Number(c.sp100), 10000 + i, c.pe_ttm * 10, 100 + i, (i + 1) * 1e6, 10 + i / 10]),
});
function setup(index = bundle(), data = screener(index)) {
  const c = vm.createContext({ console });
  c.window = c;
  c.INDEX = index;
  c.IDX = Object.fromEntries(index.companies.map(row => [row.ticker, row]));
  c.STATE = { cmp: ["S00"] };
  c.ROW = {};
  c.fmtMoney = value => String(value);
  c.getJSON = async path => {
    assert.equal(path, "screener.json");
    if (data === null) throw new Error("Not published");
    return data;
  };
  vm.runInContext(specCode, c);
  vm.runInContext(code.slice(code.indexOf("const median ="), code.indexOf("const quant =")), c);
  vm.runInContext(code.slice(code.indexOf("let UNI = null"), code.indexOf("async function ensureHistory(")), c);
  vm.runInContext(code.slice(code.indexOf("function spxStat("), code.indexOf("function matrixData(")), c);
  vm.runInContext(code.slice(code.indexOf("const SC_FIELDS ="), code.indexOf("function scatterPanel(")), c);
  vm.runInContext(code.slice(code.indexOf("async function peerCandidates("), code.indexOf("function peersMenu(")), c);
  return c;
}

test("comparison SP500 medians exclude additional issuers while retaining their comparison data and peer eligibility", async () => {
  const c = setup();
  const universe = await c.ensureUniverse();
  assert.equal(universe.rows.length, 50);
  const stat = c.spxStat({ spx: ["pe_ttm"] });
  assert.equal(stat.pairs.length, 50);
  assert.equal(stat.v, 255, "median of the 50 SP500 values 10 through 500");
  assert.equal(universe.by.NEW.pe_ttm, 10000, "an added issuer retains its loaded values for comparison highlights");
  assert.equal(Object.keys(universe.by).length, 52);
  assert.deepEqual(Array.from((await c.peerCandidates("cap")).slice(0, 2), row => row[0]), ["NEW", "ALT"]);
});

test("comparison reads screener cols as metadata and never offers membership flags or CIK as numeric metrics", async () => {
  const c = setup();
  const universe = await c.ensureUniverse();
  assert.equal(universe.by.NEW.quality, 150);
  const fields = c.scFields().map(row => row[0]);
  assert.ok(fields.includes("quality"));
  for (const identity of ["sp500", "sp100", "cik"]) assert.ok(!fields.includes(identity), identity);
});

test("comparison retains published field names and units when loading additional numeric screener metrics", async () => {
  const c = setup();
  await c.ensureUniverse();
  const fields = Object.fromEntries(c.scFields().map(row => [row[0], row]));
  assert.equal(fields.operating_cash_flow[2], "USD");
  assert.equal(fields.ev_ebitda[2], "x");
  assert.equal(fields.operating_cash_flow[1], "Operating cash flow");
});

test("comparison falls back to explicit company-index membership when screener data is unavailable", async () => {
  const c = setup(bundle(), null);
  const universe = await c.ensureUniverse();
  assert.equal(universe.rows.length, 50);
  assert.equal(c.spxStat({ spx: ["pe_ttm"] }).v, 25.5);
  assert.equal(universe.by.NEW.pe_ttm, 1000);
});

test("comparison legacy fallback requires a declared SP500 bundle", async () => {
  for (const declared of [false, true]) {
    const data = bundle();
    data.companies = data.companies.slice(0, 50).map(({ sp500, ...row }) => row);
    data.universe = declared ? { id: "sp500" } : undefined;
    const c = setup(data, null);
    const universe = await c.ensureUniverse();
    assert.equal(universe.rows.length, declared ? 50 : 0);
    assert.equal(c.spxStat({ spx: ["pe_ttm"] })?.v ?? null, declared ? 25.5 : null);
  }
});

test("comparison without verified benchmark members shows an unavailable scatter instead of failing", async () => {
  const data = bundle();
  data.companies = data.companies.map(({ sp500, ...row }) => row);
  data.universe = undefined;
  const c = setup(data, null);
  await c.ensureUniverse();
  const dom = new JSDOM("<main></main>");
  const d = dom.window.document;
  try {
    c.CS = { sx: "pe_ttm", sy: "rev_growth_lq" };
    c.el = (tag, attrs = {}, ...children) => {
      const node = d.createElement(tag);
      for (const [key, value] of Object.entries(attrs)) {
        if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
        else if (value != null) node.setAttribute(key, String(value));
      }
      for (const child of children.flat()) if (child != null) node.append(child.nodeType ? child : String(child));
      return node;
    };
    c.panel = (title, tools, ...body) => c.el("section", {}, c.el("h3", {}, title), tools, ...body);
    c.requestAnimationFrame = fn => fn();
    vm.runInContext(code.slice(code.indexOf("function scatterPanel("), code.indexOf("/* ---------- relative valuation:")), c);
    assert.doesNotThrow(() => c.scatterPanel(d.querySelector("main")));
    assert.match(d.querySelector("main").textContent, /not available|unavailable/i);
  } finally { dom.window.close(); }
});
