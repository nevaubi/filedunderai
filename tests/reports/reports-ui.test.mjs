// Reports tab registers after Overview and exposes its document view.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

test("reports tab registers after overview", () => {
  const src = readFileSync(new URL("../../public/lib/reports.js", import.meta.url), "utf8");
  const ctx = { window: {}, localStorage: { getItem: () => null, setItem() {} }, getJSON: async () => ({}), el: () => ({}), document: { querySelector: () => null, head: { append() {} } } };
  vm.createContext(ctx);
  vm.runInContext(src, ctx);
  const tab = ctx.window.FU_COTABS.find(t => t.id === "reports");
  assert.equal(tab.after, "overview");
  assert.equal(typeof ctx.window.FU_REPORTS.docView, "function");
});
