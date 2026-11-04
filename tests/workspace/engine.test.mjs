import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
const context = { console, structuredClone, Date };
context.globalThis = context;
const path = "public/lib/workspace.js";
if (fs.existsSync(path))
  vm.runInNewContext(fs.readFileSync(path, "utf8"), context);
const W = context.FU_WORKSPACE_ENGINE;
const book = () => W.createWorkbook("Research");
test("formula engine recalculates sheets, ranges, named inputs and errors without coercing missing financial data", () => {
  assert.ok(W, "workspace engine must exist");
  const b = book(),
    s = b.sheets[0];
  s.cells = {
    A1: { value: 10 },
    A2: { value: 20 },
    A3: { value: "=SUM(A1:A2)" },
    A4: { value: "=A3*Growth" },
    A5: { value: '=FU("T","Revenue","LFY")' },
    A6: { value: "=A5+1" },
    A7: { value: "=BOGUS(1)" },
    A8: { value: "=A9" },
    A9: { value: "=A8" },
    A10: { value: "=1/0" },
    A11: { value: "=SUM(A5:A6)" },
  };
  b.names.Growth = { sheet: s.id, address: "B1" };
  s.cells.B1 = { value: 0.1 };
  const e = W.evaluate(b, () => null);
  assert.equal(e.value(s.id, "A3"), 30);
  assert.equal(e.value(s.id, "A4"), 3);
  for (const a of ["A5", "A6", "A11"]) assert.equal(e.value(s.id, a), "#N/A");
  assert.equal(e.value(s.id, "A7"), "#NAME?");
  assert.equal(e.value(s.id, "A8"), "#CYCLE!");
  assert.equal(e.value(s.id, "A10"), "#DIV/0!");
  b.sheets.push(W.createSheet("Other"));
  b.sheets[1].cells.A1 = { value: "=Sheet1!A3+1" };
  assert.equal(W.evaluate(b).value(b.sheets[1].id, "A1"), 31);
});
test("range paste is atomic, expands grid, preserves undo and translates fill references", () => {
  const b = book(),
    h = new W.History(b);
  W.paste(b, b.activeSheet, "B2", "1\t2\n3\t=A2+$B$2");
  h.commit(b, "Paste");
  assert.equal(b.sheets[0].cells.C3.value, "=A2+$B$2");
  const before = W.clone(b);
  W.fill(b, b.activeSheet, "C3:C5");
  h.commit(b, "Fill");
  assert.equal(b.sheets[0].cells.C5.value, "=A4+$B$2");
  assert.deepEqual(h.undo(), before);
  assert.equal(h.redo().sheets[0].cells.C5.value, "=A4+$B$2");
  assert.throws(
    () => W.paste(b, b.activeSheet, "ZZ99999", "bad"),
    /range|limit/i,
  );
});
test("staged patches validate every cell, preview old values, apply once and reject conflicts", () => {
  const b = book();
  b.sheets[0].cells.A1 = { value: 5, source: { id: "old" } };
  const p = W.stage(b, {
    summary: "Update assumption",
    cells: [{ address: "A1", value: 10 }],
  });
  assert.equal(b.sheets[0].cells.A1.value, 5);
  assert.equal(p.cells[0].before.value, 5);
  W.applyProposal(b, p);
  assert.equal(b.sheets[0].cells.A1.value, 10);
  assert.equal(p.status, "applied");
  assert.throws(() => W.applyProposal(b, p), /pending/);
  const conflict = W.stage(b, { cells: [{ address: "A1", value: 20 }] });
  b.sheets[0].cells.A1.value = 11;
  assert.throws(() => W.applyProposal(b, conflict), /changed/);
  assert.throws(
    () => W.stage(b, { cells: [{ address: "A0", value: "oops" }] }),
    /address/i,
  );
});
test("legacy migration backs up originals once without deleting original sheet or memo", () => {
  const m = new Map([
    ["fu:sheet", JSON.stringify({ A1: 42, B1: "=A1*2" })],
    ["fu:memo", JSON.stringify("<p>Original</p>")],
  ]);
  const storage = {
    getItem: (k) => m.get(k) ?? null,
    setItem: (k, v) => m.set(k, v),
  };
  const b = W.load(storage);
  assert.equal(b.sheets[0].cells.A1.value, 42);
  assert.equal(b.document.html, "<p>Original</p>");
  assert.equal(m.get("fu:sheet"), JSON.stringify({ A1: 42, B1: "=A1*2" }));
  const backup = m.get("fu:workspace:legacy-backup");
  W.save(storage, b);
  W.load(storage);
  assert.equal(m.get("fu:workspace:legacy-backup"), backup);
});
test("model builder creates linked history, editable forecast drivers and source sheet with deterministic arithmetic", () => {
  const b = W.buildModel({
    ticker: "TEST",
    periods: ["FY2024", "FY2025"],
    resolve: (t, m, p) => ({
      value:
        {
          Revenue: p === "FY2024" ? 100e6 : 120e6,
          "Operating income": 24e6,
          "Operating cash flow": 30e6,
          Capex: 6e6,
          "Net income": 20e6,
        }[m] ?? null,
      source: { id: `${m}-${p}`, sourceUrl: "https://www.sec.gov/test" },
    }),
  });
  const s = b.sheets[0],
    e = W.evaluate(b, (t, m, p) => ({
      value:
        {
          Revenue: p === "FY2024" ? 100e6 : 120e6,
          "Operating income": 24e6,
          "Operating cash flow": 30e6,
          Capex: 6e6,
          "Net income": 20e6,
        }[m] ?? null,
    }));
  assert.equal(e.value(s.id, "C5"), 120);
  assert.equal(e.value(s.id, "C12"), 24);
  assert.equal(e.value(s.id, "D5"), 120);
  assert.equal(e.value(s.id, "D12"), 24);
  assert.equal(b.sheets[1].name, "Assumptions");
  assert.equal(b.sheets[2].name, "Sources");
  assert.match(b.sheets[0].cells.D3.value, /Forecast/);
  const noData = W.buildModel({
    ticker: "EMPTY",
    periods: [],
    resolve: () => null,
  });
  assert.equal(
    W.evaluate(noData, () => null).value(noData.sheets[0].id, "C5"),
    "#N/A",
  );
});
test("invalid valuation assumptions and unsupported input never produce plausible terminal value", () => {
  const b = W.buildModel({
    ticker: "TEST",
    periods: ["FY2025"],
    resolve: () => ({ value: 100e6 }),
  });
  const inputs = b.sheets[1];
  inputs.cells.B9.value = 0.01;
  inputs.cells.B10.value = 0.02;
  inputs.cells.B13.value = 0.25;
  inputs.cells.B14.value = 0.05;
  inputs.cells.B15.value = 0.02;
  assert.equal(
    W.evaluate(b, () => ({ value: 100e6 })).value(b.sheets[0].id, "E22"),
    "#NUM!",
  );
});
test("model snapshot keeps filed evidence, rejects malformed workbook and caps oversized selections", () => {
  const b = W.buildModel({
    ticker: "T",
    periods: ["FY2025"],
    resolve: () => ({
      value: 100e6,
      source: { id: "f1", sourceUrl: "https://www.sec.gov/f1" },
    }),
  });
  assert.equal(b.sheets[0].cells.B5.source.id, "f1");
  const invalid = book();
  delete invalid.sheets[0].cells;
  assert.deepEqual(Object.keys(W.validateBook(invalid).sheets[0].cells), []);
  assert.throws(() => W.refs("A1:CV2000"), /limit/i);
});
test("overriding a filed formula clears its source while history retains the earlier provenance", () => {
  const b = book(),
    s = b.sheets[0];
  s.cells.A1 = {
    value: '=FU("T","Revenue","LFY")',
    source: { id: "immutable-filed-record" },
  };
  const history = new W.History(b);
  W.put(b, s.id, "A1", 50);
  history.commit(b, "User assumption");
  assert.equal(b.sheets[0].cells.A1.source, undefined);
  assert.equal(
    history.undo().sheets[0].cells.A1.source.id,
    "immutable-filed-record",
  );
});
test("enterprise valuation discounts explicit unlevered cash flow assumptions instead of operating cash flow proxy", () => {
  const b = W.buildModel({
    ticker: "T",
    periods: ["FY2025"],
    resolve: (t, m) => ({
      value: {
        Revenue: 120e6,
        "Operating income": 24e6,
        "Operating cash flow": 30e6,
        Capex: 6e6,
        "Net income": 20e6,
      }[m],
    }),
  });
  const value = () =>
    W.evaluate(b, (t, m) => ({
      value: {
        Revenue: 120e6,
        "Operating income": 24e6,
        "Operating cash flow": 30e6,
        Capex: 6e6,
        "Net income": 20e6,
      }[m],
    }));
  assert.equal(value().value(b.sheets[0].id, "C18"), "#N/A");
  const a = b.sheets[1];
  a.cells.B13.value = 0.25;
  a.cells.B14.value = 0.05;
  a.cells.B15.value = 0.02;
  a.cells.B11.value = 10;
  a.cells.B12.value = 20;
  const e = value();
  assert.equal(e.value(b.sheets[0].id, "C18"), 15.6);
  assert.ok(e.value(b.sheets[0].id, "E28") > 0);
});
test("approved null clear normalizes to an empty cell and survives saved-workbook reload without losing other work", () => {
  const b = book(),
    s = b.sheets[0];
  s.cells.A1 = { value: 42, source: { id: "old-source" } };
  s.cells.B1 = { value: "Preserve this", format: { bold: true } };
  b.document.html = "<p>Preserve this note</p>";
  const history = new W.History(b);
  const p = W.stage(b, {
    cells: [
      { address: "A1", value: null },
      { address: "C1", value: null, format: { number: "percent" } },
    ],
  });
  assert.equal(p.cells[0].value, "");
  assert.equal(b.sheets[0].cells.A1.value, 42);
  W.applyProposal(b, p);
  history.commit(b, "Approved clear");
  const memory = new Map(),
    storage = {
      getItem: (k) => memory.get(k) ?? null,
      setItem: (k, v) => memory.set(k, v),
    };
  W.save(storage, b);
  const reloaded = W.load(storage);
  assert.equal(reloaded.sheets[0].cells.A1, undefined);
  assert.equal(reloaded.sheets[0].cells.C1.value, "");
  assert.equal(reloaded.sheets[0].cells.C1.format.number, "percent");
  assert.equal(
    JSON.stringify(reloaded.sheets[0].cells.B1),
    JSON.stringify(b.sheets[0].cells.B1),
  );
  assert.equal(reloaded.document.html, "<p>Preserve this note</p>");
  assert.equal(history.undo().sheets[0].cells.A1.source.id, "old-source");
});
test("previously saved null-valued cells recover as blank while retaining the rest of the workbook", () => {
  const b = book(),
    s = b.sheets[0];
  s.cells.A1 = {
    value: null,
    format: { number: "currency" },
    source: { id: "obsolete" },
  };
  s.cells.B1 = { value: "Existing work" };
  const saved = JSON.stringify(b),
    storage = {
      getItem: (k) => (k === "fu:workspace:v1" ? saved : null),
      setItem: () => {},
    };
  const loaded = W.load(storage);
  assert.equal(loaded.sheets[0].cells.A1.value, "");
  assert.equal(loaded.sheets[0].cells.A1.format.number, "currency");
  assert.equal(loaded.sheets[0].cells.A1.source, undefined);
  assert.equal(loaded.sheets[0].cells.B1.value, "Existing work");
  assert.equal(b.sheets[0].cells.A1.value, null);
});
