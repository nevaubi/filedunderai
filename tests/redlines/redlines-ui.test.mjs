// Redlines tab: registers after Filings & IR and rebuilds old/new text from word ops.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

test("redlines tab registers and renders inline ops", () => {
  const src = readFileSync(new URL("../../public/lib/redlines.js", import.meta.url), "utf8");
  const made = [];
  const node = (tag) => ({ tag, kids: [], attrs: {}, append(...k) { this.kids.push(...k); }, set textContent(v) { this.kids = [v]; } });
  const ctx = { window: {}, localStorage: { getItem: () => null, setItem() {} }, getJSON: async () => ({}),
    el: (tag, attrs, ...kids) => { const n = node(tag); n.attrs = attrs || {}; n.kids = kids.flat().filter(k => k != null); made.push(n); return n; },
    document: { createTextNode: t => t, querySelector: () => null, head: { append() {} } } };
  vm.createContext(ctx);
  vm.runInContext(src, ctx);
  const tab = ctx.window.FU_COTABS.find(t => t.id === "redlines");
  assert.equal(tab.after, "filings");
  const p = ctx.window.FU_REDLINES.opsView({ kind: "modified", ops: [["=", "relies on"], ["-", "a"], ["+", "single or"], ["=", "limited sources"]] });
  const flat = JSON.stringify(p);
  assert.match(flat, /"tag":"del".*"a"/);
  assert.match(flat, /"tag":"ins".*"single or"/);
});
