import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { JSDOM } from "jsdom";

const code = fs.readFileSync("public/pages/screener.js", "utf8");
const specCode = fs.readFileSync("public/pages/screenspec.js", "utf8");
const ids = "price ret_1y mcap pe rev_g_ttm om fcf_yield div_yield roic gm gm_chg3 rev_cagr5 nd_ebitda pe_p5 beta om_chg3".split(" ");
const fields = ids.map(id => ({ id, n: id, g: "val", u: id === "mcap" ? "usd" : "x", d: id, q: [0, 1, 100], cov: 2 }));
const cols = ["ticker", "name", "sector", "industry", "sp100", ...ids];
const row = (ticker, name, quality) => {
  const values = { ticker, name, sector: "Technology", industry: "Software", sp100: true, price: 10, ret_1y: 0.2, mcap: 1e11, pe: 20, rev_g_ttm: 0.15, om: 0.2, fcf_yield: 0.04, div_yield: 0, roic: quality ? 0.3 : 0.05, gm: 0.5, gm_chg3: 0.01, rev_cagr5: 0.1, nd_ebitda: 1, pe_p5: 0.4, beta: 1, om_chg3: 0.02 };
  return cols.map(id => values[id]);
};
async function setup(saved = null, data = null, hash = "#screener") {
  const dom = new JSDOM('<main id="page"></main>', { url: "https://research.example/" + hash, runScripts: "outside-only", pretendToBeVisual: true });
  const w = dom.window, d = w.document;
  const errors = [];
  w.addEventListener("error", event => { errors.push(event.error?.message || event.message); event.preventDefault(); });
  w.page = d.querySelector("main");
  const storage = w.localStorage;
  w.store = { get: (k, fallback) => JSON.parse(storage.getItem(k)) ?? fallback, set: (k, v) => storage.setItem(k, JSON.stringify(v)) };
  if (saved) w.store.set("scr2", saved);
  w.el = (tag, attrs = {}, ...children) => {
    const n = d.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (v == null || v === false) continue;
      if (k.startsWith("on")) n[k] = v;
      else if (k === "class") n.className = v;
      else n.setAttribute(k, v === true ? "" : String(v));
    }
    for (const child of children.flat()) if (child != null) n.append(child.nodeType ? child : String(child));
    return n;
  };
  w.getJSON = async () => data || ({ fields, cols, rows: [row("AAA", "Alpha", true), row("BBB", "Beta", false)], groups: [{ id: "val", n: "Valuation" }], asof: { prices: "2026-11-03" } });
  for (const name of ["fdate", "fnum", "fmtMoney", "fpct", "fsgn"]) w[name] = v => String(v);
  w.svgUse = () => w.el("svg");
  w.miniLogo = () => w.el("span");
  w.seg = (options, value, onChange) => w.el("div", { class: "seg" }, options.map(([id, label]) => w.el("button", { class: id === value ? "on" : "", onclick: () => onChange(id) }, label)));
  w.watchlist = () => [];
  w.cls = () => "";
  w.showTip = w.hideTip = w.toast = () => {};
  w.copyText = text => { w.copied = text; };
  w.downloadText = (name, text, type) => { w.downloaded = { name, text, type }; };
  w.eval(specCode);
  w.eval(code);
  await w.FU_PAGES.screener();
  const click = label => {
    const n = [...d.querySelectorAll("button")].find(n => n.textContent === label);
    assert.ok(n, "Missing button: " + label);
    n.click();
    return n;
  };
  const change = (selector, value) => {
    const n = d.querySelector(selector);
    assert.ok(n, "Missing control: " + selector);
    n.value = value;
    n.dispatchEvent(new w.Event("change", { bubbles: true }));
    return n;
  };
  return { w, d, click, change, close: () => { w.close(); assert.deepEqual(errors, [], "DOM events must not throw"); } };
}

const tracked = () => ({
  universe: { id: "tracked", membership_version: 1 }, fields,
  cols: [...cols, "sp500"],
  rows: [["AAA", "Alpha", true, true, 1e11], ["BBB", "Beta", false, true, 2e11], ["CCC", "Additional", false, false, 3e11]].map(([ticker, name, sp100, sp500, mcap]) => {
    const values = row(ticker, name, true);
    values[cols.indexOf("sp100")] = Number(sp100);
    values[cols.indexOf("mcap")] = mcap;
    return [...values, Number(sp500)];
  }),
  groups: [{ id: "val", n: "Valuation" }], asof: { prices: "2026-11-03" },
});
const visibleTickers = d => [...d.querySelectorAll("table.sx tbody tr td:nth-child(2)")].map(n => n.textContent).sort();
const fullSpec = (overrides = {}) => ({ filters: [], sectors: [], exclude_sectors: [], industries: [], exclude_industries: [], index: "sp500", tickers: [], exclude_tickers: [], similar_to: null, sort: null, columns: [], group_by: null, limit: null, explanation: "", assumptions: [], ...overrides });

test("saved SP500 screens exclude additional companies before percentile filters are applied", async () => {
  const { w, d, click, close } = await setup({ spec: fullSpec({ filters: [{ field: "mcap", op: "top", pct: 50, scope: "all" }] }) }, tracked());
  try {
    assert.deepEqual(visibleTickers(d), ["BBB"]);
    assert.equal(w.store.get("scr2").spec.index, "sp500");
    d.querySelector('.sx-chip .x').click();
    assert.deepEqual(visibleTickers(d), ["AAA", "BBB"]);
    click("Filters (1)");
    click("S&P 100");
    assert.deepEqual(visibleTickers(d), ["AAA"]);
    click("All tracked");
    assert.deepEqual(visibleTickers(d), ["AAA", "BBB", "CCC"]);
    assert.equal(w.store.get("scr2").spec.index, "all");
    assert.equal(d.querySelector(".sx-chips").hidden, true);
    assert.ok(!d.querySelector("h1").textContent.includes("S&P"));
    assert.match(d.querySelector(".sx-count").textContent, /All tracked/);
    assert.ok([...d.querySelectorAll(".sx-stat")].every(n => !n.title.includes("S&P 500")));
  } finally { close(); }
});

test("new screens include additions and persist explicit universe choices through sharing", async () => {
  const { w, d, click, close } = await setup(null, tracked());
  try {
    assert.deepEqual(visibleTickers(d), ["AAA", "BBB", "CCC"]);
    click("Filters (0)");
    assert.equal([...d.querySelectorAll(".sx-uni .seg button")].find(n => n.classList.contains("on")).textContent, "All tracked");
    for (const [label, index, expected] of [["S&P 500", "sp500", ["AAA", "BBB"]], ["All tracked", "all", ["AAA", "BBB", "CCC"]]]) {
      click(label);
      click("Actions"); click("Share screen");
      const shared = JSON.parse(w.atob(w.copied.split("screener=")[1].replace(/-/g, "+").replace(/_/g, "/")));
      assert.equal(shared.index, index);
      w.location.hash = w.copied.split("#")[1];
      w.dispatchEvent(new w.HashChangeEvent("hashchange"));
      w.page.replaceChildren();
      await w.FU_PAGES.screener();
      assert.deepEqual(visibleTickers(d), expected);
    }
  } finally { close(); }
});

test("legacy shared and named saved screens with an omitted index retain SP500 membership", async () => {
  const legacy = Buffer.from(JSON.stringify({ filters: [] })).toString("base64url");
  const { w, d, click, close } = await setup(null, tracked(), "#screener=" + legacy);
  try {
    assert.deepEqual(visibleTickers(d), ["AAA", "BBB"]);
    click("Actions"); click("Reset screen");
    assert.deepEqual(visibleTickers(d), ["AAA", "BBB", "CCC"]);
    w.store.set("scr2:saved", [{ name: "Legacy", at: 1, spec: { filters: [] }, cols: ["mcap"] }]);
    click("Actions"); click("Saved screens");
    d.querySelector(".sx-saved .t").click();
    assert.deepEqual(visibleTickers(d), ["AAA", "BBB"]);
    assert.equal(w.store.get("scr2").spec.index, "sp500");
  } finally { close(); }
});

test("legacy bundle fallback requires declared SP500 scope and never overrides explicit false membership", async () => {
  for (const [scope, explicit, expected] of [[undefined, false, []], ["tracked", false, []], ["sp500", false, ["AAA", "BBB"]], ["sp500", true, ["AAA", "BBB"]]]) {
    const data = tracked();
    data.universe = scope ? { id: scope } : undefined;
    if (!explicit) {
      data.cols.pop();
      data.rows = data.rows.slice(0, 2).map(r => r.slice(0, -1));
    }
    const { d, close } = await setup({ spec: fullSpec() }, data);
    try { assert.deepEqual(visibleTickers(d), expected, `${scope || "unstamped"}, explicit=${explicit}`); }
    finally { close(); }
  }
});

test("invalid membership values are not coerced to index membership", async () => {
  const data = tracked();
  data.rows[2][data.cols.indexOf("sp500")] = "false";
  data.rows[2][data.cols.indexOf("sp100")] = "false";
  const { d, click, close } = await setup({ spec: fullSpec() }, data);
  try {
    assert.deepEqual(visibleTickers(d), ["AAA", "BBB"]);
    click("Filters (1)"); click("S&P 100");
    assert.deepEqual(visibleTickers(d), ["AAA"]);
  } finally { close(); }
});

test("compact primary controls retain presets, counted editable filters and collapsed statistics", async () => {
  const { w, d, click, change, close } = await setup();
  try {
    assert.equal(d.querySelectorAll(".scr-tools > button").length, 2);
    assert.equal(d.querySelector(".sx-chips").hidden, true, "an unfiltered screen has no redundant chip row");
    assert.equal(d.querySelector(".sx-chips .sx-add"), null, "the collapsed row contains no hidden command");
    assert.equal(d.querySelector('select[aria-label="Preset screen"]').options.length, 20);
    assert.equal(d.querySelector(".sx-statistics").open, false);
    d.querySelector(".sx-statistics").open = true;
    assert.equal(d.querySelector(".sx-grid").classList.contains("nochat"), true);
    change('[aria-label="Preset screen"]', "quality");
    assert.equal(d.querySelector(".sx-chips").hidden, false);
    assert.equal(d.querySelector(".sx-statistics").open, true, "explicit statistics expansion survives result updates");
    assert.equal(w.store.get("scr2").spec.filters.length, 4);
    assert.equal(d.querySelector(".sx-count b").textContent, "1");
    d.querySelector(".sx-chip .t").click();
    const minimum = d.querySelector('[aria-label="Minimum"]');
    minimum.value = "0.01";
    const oldChip = d.querySelector(".sx-chip .t");
    click("Apply");
    assert.notEqual(d.activeElement, oldChip);
    assert.equal(d.activeElement, d.querySelector(".sx-chip .t"), "Apply focuses the replacement chip after rendering");
    assert.equal(w.store.get("scr2").spec.filters[0].value, 0.01);
    assert.equal(d.querySelector(".sx-count b").textContent, "2");
    click("Filters (4)");
    assert.equal(d.querySelector(".sx-filters").hidden, false);
    assert.equal(d.querySelector('[aria-controls="sx-filters"]').getAttribute("aria-expanded"), "true");
    d.querySelector('.sx-chip .x').click();
    assert.equal(d.querySelector('[aria-controls="sx-filters"]').textContent, "Filters (3)");
    const search = d.querySelector('.scr-tools input[type="search"]');
    search.value = "Beta";
    search.dispatchEvent(new w.Event("input", { bubbles: true }));
    assert.equal(d.querySelector(".sx-count b").textContent, "1", "remaining filters now permit the searched company");
    assert.equal(w.store.get("scr2").q, "Beta");
    click("Actions"); click("Reset screen");
    assert.equal(d.querySelector('[aria-label="Preset screen"]').value, "");
    assert.equal(w.store.get("scr2").preset, null);
    assert.equal(w.store.get("scr2").spec.filters.length, 0);
    assert.equal(d.querySelector(".sx-chips").hidden, true);
  } finally { close(); }
});

test("Filters creates the first filter and keeps new-chip focus when the empty row returns", async () => {
  const { w, d, click, close } = await setup();
  try {
    click("Filters (0)");
    assert.equal(d.querySelector(".sx-filters").hidden, false);
    const oldAdd = d.querySelector(".sx-filters .sx-add");
    assert.ok(oldAdd, "the open Filters panel offers the first-filter shortcut");
    oldAdd.click();
    click("peValuation");
    d.querySelector('[aria-label="Minimum"]').value = "10";
    click("Apply");
    assert.equal(oldAdd.isConnected, false);
    assert.equal(w.store.get("scr2").spec.filters[0].field, "pe");
    assert.equal(d.activeElement, d.querySelector(".sx-chip .t"));
    assert.equal(d.activeElement.isConnected, true);
    assert.equal(d.querySelector(".sx-chips").hidden, false);
    d.querySelector(".sx-chips .sx-add").click();
    click("omValuation");
    d.querySelector('[aria-label="Minimum"]').value = "0.1";
    click("Apply");
    assert.equal(w.store.get("scr2").spec.filters[1].field, "om");
    assert.equal(d.activeElement, d.querySelectorAll(".sx-chip .t")[1]);
    d.querySelector(".sx-chip .x").click();
    d.querySelector(".sx-chip .x").click();
    assert.equal(d.querySelector(".sx-chips").hidden, true);
    assert.equal(d.activeElement, d.querySelector('[aria-controls="sx-filters"]'), "removing the last chip returns focus to Filters");
  } finally { close(); }
});

test("identity sorting keeps the table and CSV intact without adding identity metric columns", async () => {
  const { w, d, click, change, close } = await setup();
  try {
    for (const field of ["ticker", "name"]) {
      click("Actions");
      change('[aria-label="Sort results"]', field);
      change('[aria-label="Sort direction"]', "desc");
      assert.equal(d.querySelector("table.sx tbody tr td:nth-child(2)").textContent, "BBB");
      assert.equal(d.querySelectorAll("table.sx thead th").length, 11);
      click("Export CSV");
      assert.equal(w.downloaded.text.split("\n")[0].split(",").length, 12);
      assert.ok(w.downloaded.text.split("\n")[1].startsWith('"BBB","Beta"'));
      assert.ok(!w.downloaded.text.includes("undefined"));
      w.page.replaceChildren();
      await w.FU_PAGES.screener();
      assert.equal(d.querySelector("table.sx tbody tr td:nth-child(2)").textContent, "BBB", "restoring the universe must preserve identity sorting");
    }
  } finally { close(); }
});

test("Actions preserves saved screens, sorting, grouping, selected columns, export and share state", async () => {
  const { w, d, click, change, close } = await setup({ chatOpen: true });
  try {
    assert.equal(d.querySelector(".sx-grid").classList.contains("nochat"), false, "explicit assistant preference survives");
    click("Actions");
    change('[aria-label="Sort results"]', "pe");
    change('[aria-label="Sort direction"]', "desc");
    change('[aria-label="Group results"]', "sector");
    click("Columns");
    const cb = [...d.querySelectorAll(".sx-cb")].find(n => n.textContent === "beta").querySelector("input");
    cb.checked = true;
    cb.dispatchEvent(new w.Event("change", { bubbles: true }));
    d.dispatchEvent(new w.KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    click("Actions");
    assert.equal(d.querySelector('[aria-label="Group results"]').value, "sector");
    assert.equal(d.querySelector('[aria-label="Sort results"]').value, "pe");
    assert.equal(d.querySelector('[aria-label="Sort direction"]').value, "desc");
    click("Saved screens");
    d.querySelector('[aria-label="Screen name"]').value = "My screen";
    click("Save");
    assert.equal(w.store.get("scr2:saved")[0].spec.group_by, "sector");
    d.dispatchEvent(new w.KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    click("Actions"); click("Reset screen");
    click("Actions"); click("Saved screens");
    d.querySelector(".sx-saved .t").click();
    assert.equal(w.store.get("scr2").spec.sort.field, "pe");
    assert.ok(w.store.get("scr2").cols.includes("beta"));
    click("Actions"); click("Export CSV");
    assert.equal(w.downloaded.type, "text/csv");
    assert.ok(w.downloaded.text.includes('"beta (x)"'));
    assert.ok(w.downloaded.text.includes('"AAA"'));
    click("Actions"); click("Share screen");
    const shared = JSON.parse(w.atob(w.copied.split("screener=")[1].replace(/-/g, "+").replace(/_/g, "/")));
    assert.equal(shared.group_by, "sector");
    assert.deepEqual(shared.sort, { field: "pe", dir: "desc" });
    assert.ok(shared.columns.includes("beta"));
    w.location.hash = w.copied.split("#")[1];
    w.dispatchEvent(new w.HashChangeEvent("hashchange"));
    w.page.replaceChildren();
    await w.FU_PAGES.screener();
    assert.equal(w.store.get("scr2").spec.group_by, "sector", "shared hash applies through the registered route handler");
    assert.ok(w.store.get("scr2").cols.includes("beta"));
    click("Actions"); click("Assistant");
    assert.equal(w.store.get("scr2").chatOpen, false);
  } finally { close(); }
});

test("repeated nested panels clean up listeners and restore focus to persistent Actions", async () => {
  const { w, d, click, close } = await setup();
  try {
    const tracked = new Map();
    for (const [target, types] of [[d, ["mousedown", "keydown"]], [w, ["hashchange"]]]) {
      const add = target.addEventListener.bind(target), remove = target.removeEventListener.bind(target);
      for (const type of types) tracked.set(type, new Set());
      target.addEventListener = (type, fn, ...args) => { tracked.get(type)?.add(fn); return add(type, fn, ...args); };
      target.removeEventListener = (type, fn, ...args) => { tracked.get(type)?.delete(fn); return remove(type, fn, ...args); };
    }
    const actions = [...d.querySelectorAll("button")].find(n => n.textContent === "Actions");
    for (let i = 0; i < 4; i++) {
      click("Actions"); click(i % 2 ? "Columns" : "Saved screens");
      assert.equal(d.querySelectorAll(".sx-pop").length, 1);
      assert.ok(["Choose columns", "Saved screens"].includes(d.querySelector(".sx-pop").getAttribute("aria-label")));
      for (const listeners of tracked.values()) assert.equal(listeners.size, 1);
      d.dispatchEvent(new w.KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true }));
      assert.equal(d.activeElement, actions);
      assert.equal(actions.getAttribute("aria-expanded"), "false");
      for (const listeners of tracked.values()) assert.equal(listeners.size, 0);
    }
    click("Actions");
    d.body.dispatchEvent(new w.MouseEvent("mousedown", { bubbles: true }));
    assert.equal(d.querySelector(".sx-pop"), null);
    click("Actions");
    w.dispatchEvent(new w.HashChangeEvent("hashchange"));
    assert.equal(d.querySelector(".sx-pop"), null);
    for (const listeners of tracked.values()) assert.equal(listeners.size, 0);
  } finally { close(); }
});
