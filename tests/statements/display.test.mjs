import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { JSDOM } from "jsdom";

const fact = (tag, value, unit = "USD", qtrs = 1) =>
  [tag, "2026-06-30", qtrs, unit, null, null, value, null];
const line = (tag, label = tag, custom = 0) =>
  [1, tag, custom, label, 0, "D", "C"];

async function renderReport(lines, facts) {
  const dom = new JSDOM('<main id="page"></main>', {
    url: "https://research.example/#company-amt",
    runScripts: "outside-only",
  });
  const w = dom.window, opened = [];
  const accession = "0001053507-26-000133";
  const doc = {
    accession, ticker: "AMT", cik: 1053507, registrant: "American Tower Corporation",
    form: "10-Q", period: "2026-06-30", fy: 2026, fp: "Q2", filed: "2026-07-28",
    url: `https://www.sec.gov/Archives/edgar/data/1053507/${accession.replaceAll("-", "")}/${accession}-index.htm`,
    source: "https://www.sec.gov/files/dera/data/financial-statement-data-sets/2026q3.zip",
    reports: [{ report: 1, stmt: "IS", inpth: false, lines, facts }],
  };
  w.el = (tag, attrs = {}, ...kids) => {
    const e = w.document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (v == null || v === false) continue;
      if (k === "class") e.className = v;
      else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
      else e.setAttribute(k, v === true ? "" : v);
    }
    for (const child of kids.flat())
      if (child != null && child !== false) e.append(child.nodeType ? child : String(child));
    return e;
  };
  w.seg = (options, value, change) => w.el("div", {}, options.map(([v, label]) =>
    w.el("button", { onclick: () => change(v), "aria-selected": String(v === value) }, label)));
  w.store = { get: (_, fallback) => fallback, set() {} };
  w.D = { filings: {} };
  w.PUB = "https://fixtures.example/";
  w.fdate = date => date || "";
  w.formCls = () => "q";
  w.shiftDays = (date, days) => {
    const d = new Date(date + "T00:00:00Z");
    d.setUTCDate(d.getUTCDate() + days);
    return d.toISOString().slice(0, 10);
  };
  w.requestAnimationFrame = cb => cb();
  w.openSource = cell => opened.push(cell);
  w.getJSON = async path => {
    assert.equal(path, "statements/AMT/index.json");
    return { ticker: "AMT", filings: [{ ...doc, a: accession }] };
  };
  w.fetch = async url => {
    assert.equal(url, `https://fixtures.example/statements/AMT/${accession}.json.gz`);
    return { ok: true, text: async () => JSON.stringify(doc) };
  };
  w.inflate = response => response.text();
  w.eval(fs.readFileSync("public/lib/evidence.js", "utf8"));
  w.eval(fs.readFileSync("public/pages/statements.js", "utf8"));
  await w.FU_COTABS.find(tab => tab.id === "statements").render(w.document.getElementById("page"), "AMT");
  await new Promise(resolve => setImmediate(resolve));
  const row = label => [...w.document.querySelectorAll("table.fin tbody tr")]
    .find(r => r.querySelector(".nm")?.textContent === label);
  return { w, doc, opened, row, close: () => dom.window.close() };
}

test("as-reported EPS retains dollars per share under a thousands heading and in source cells", async () => {
  // Enough monetary facts to reproduce the existing thousands scale, as in the filing.
  const money = Array.from({ length: 10 }, (_, i) => "MonetaryLine" + i);
  const moneyFacts = money.flatMap((tag, i) => [
    fact(tag, 2685400000 + i * 1000), fact(tag, 5370800000 + i * 1000, "USD", 2),
    [tag, "2025-06-30", 1, "USD", null, null, 2500100000 + i * 1000, null],
    [tag, "2025-06-30", 2, "USD", null, null, 5000200000 + i * 1000, null],
  ]);
  const lines = [
    ...money.map(tag => line(tag)), line("EarningsPerShareBasic", "Basic EPS"),
    line("EarningsPerShareDiluted", "Diluted EPS"), line("WeightedAverageNumberOfSharesOutstandingBasic", "Weighted shares"),
  ];
  const facts = [...moneyFacts, fact("EarningsPerShareBasic", 1.86), fact("EarningsPerShareBasic", 3.71, "USD", 2),
    fact("EarningsPerShareDiluted", 1.85), fact("EarningsPerShareDiluted", 3.70, "USD", 2),
    fact("WeightedAverageNumberOfSharesOutstandingBasic", 468100000, "shares")];
  const { w, row, opened, doc, close } = await renderReport(lines, facts);
  try {
    const basic = [...row("Basic EPS").querySelectorAll("td[data-i]")];
    assert.deepEqual(basic.slice(0, 2).map(td => td.textContent), ["1.86", "3.71"]);
    assert.deepEqual([...row("Diluted EPS").querySelectorAll("td[data-i]")].slice(0, 2).map(td => td.textContent), ["1.85", "3.70"]);
    assert.equal(row("MonetaryLine0").querySelector('td[data-i="0"]').textContent, "2,685,400");
    assert.equal(row("Weighted shares").querySelector('td[data-i="0"]').textContent, "468,100");
    assert.match(w.document.querySelector(".legend").textContent, /USD in thousands, shares in thousands, except per-share amounts/);
    basic[0].click();
    assert.equal(opened[0].v, 1.86);
    assert.equal(opened[0].unit, "USD/shares");
    assert.equal(opened[0].reportedUnit, "USD");
    assert.equal(opened[0].rec.unit, "USD");
    assert.equal(opened[0].rec.accn, doc.accession);
    const evidence = w.FU_EVIDENCE.fromCell(opened[0], { sourceUrl: doc.url });
    assert.equal(evidence.value, 1.86);
    assert.equal(evidence.unit, "USD/shares");
    assert.equal(evidence.quality.reportedUnit, "USD");
    assert.match(evidence.quality.unitSelectionReason, /USD\/shares/);
    assert.match(evidence.quality.unitSelectionReason, /US GAAP.*EarningsPerShareBasic/);
    assert.ok(Object.isFrozen(evidence.quality));
    const serialized = JSON.parse(JSON.stringify(evidence));
    assert.equal(serialized.quality.reportedUnit, "USD");
    assert.equal(serialized.quality.unitSelectionReason, evidence.quality.unitSelectionReason);
    assert.match(basic[0].title, /1\.86 USD\/shares/);
    assert.match(basic[0].title, /data set unit: USD/);
    assert.match(w.document.querySelector('[aria-label="Filing"] option').textContent, /Q2 2026/);
    assert.match(w.document.querySelector(".src").textContent, /2026q3\.zip/);
  } finally { close(); }
});

test("per-share facts do not change the inferred monetary scale", async () => {
  const { w, row, close } = await renderReport(
    [line("Revenues"), line("NetIncomeLoss"), line("EarningsPerShareBasicAndDiluted")],
    [fact("Revenues", 2000000000), fact("NetIncomeLoss", 1000000000), fact("EarningsPerShareBasicAndDiluted", -1.86)],
  );
  try {
    assert.match(w.document.querySelector(".legend").textContent, /USD in millions/);
    assert.equal(row("Revenues").querySelector("td[data-i]").textContent, "2,000");
    assert.equal(row("EarningsPerShareBasicAndDiluted").querySelector("td[data-i]").textContent, "(1.86)");
  } finally { close(); }
});

test("source cells preserve foreign currency, explicit per-share, arbitrary units, and custom concept units", async () => {
  const cases = [
    ["EarningsPerShareBasic", "Foreign EPS", "EUR", 1.86, "EUR/shares", 0],
    ["EarningsPerShareDiluted", "Explicit EPS", "USD/shares", 1.85, "USD/shares", 0],
    ["CommonStockDividendsPerShareDeclared", "Dividend per share", "USD", 1.75, "USD/shares", 0],
    ["Margin", "Ratio", "pure", 0.123, "pure", 1],
    ["LandRate", "Rate per acre", "USD/acre", 12.34, "USD/acre", 1],
    ["ForeignRevenue", "Foreign revenue", "EUR", 1000, "EUR", 1],
    ["IncomeLossFromContinuingOperationsPerBasicShare", "Custom concept", "USD", 1000, "USD", 1],
  ];
  const { row, opened, close } = await renderReport(
    cases.map(([tag, label, , , , custom]) => line(tag, label, custom)),
    cases.map(([tag, , unit, value]) => fact(tag, value, unit)),
  );
  try {
    for (const [, label, rawUnit, value, unit] of cases) {
      row(label).querySelector("td[data-i]").click();
      const cell = opened.at(-1);
      assert.equal(cell.unit, unit, label);
      assert.equal(cell.reportedUnit, rawUnit, label);
      assert.equal(cell.v, value, label);
    }
    assert.equal(row("Foreign EPS").querySelector("td[data-i]").textContent, "1.86");
  } finally { close(); }
});
