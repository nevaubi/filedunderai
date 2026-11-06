// KPI matrix page registers in FU_PAGES and is listed in the Screen & compare hub.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

test("matrix page registers", () => {
  const src = readFileSync(new URL("../../public/pages/matrix.js", import.meta.url), "utf8");
  const ctx = { window: {}, store: { get: (k, d) => d, set() {} }, el: () => ({}), document: { getElementById: () => true, head: { append() {} } } };
  vm.createContext(ctx);
  vm.runInContext(src, ctx);
  assert.equal(typeof ctx.window.FU_PAGES.matrix, "function");
});

test("matrix is in the screen hub and loaded after kpistd", () => {
  const app = readFileSync(new URL("../../public/app.js", import.meta.url), "utf8");
  assert.match(app, /\{ id: "matrix", name: "KPI matrix", hub: "screen"/);
  const html = readFileSync(new URL("../../public/index.html", import.meta.url), "utf8");
  assert.ok(html.indexOf("/pages/kpistd.js") < html.indexOf("/pages/matrix.js"));
});
