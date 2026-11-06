// node --test: public/lib/quant-lib.js matches hand-computed values and the Python definitions.
const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs"), path = require("node:path");
// the browser file is a classic script; evaluate it with a CommonJS-style module object (the package is "type": "module")
const m = { exports: {} };
new Function("module", fs.readFileSync(path.join(__dirname, "../../public/lib/quant-lib.js"), "utf8"))(m);
const QL = m.exports;
const close = (a, b, e = 1e-9) => assert.ok(Math.abs(a - b) < e, `${a} != ${b}`);

test("total return index reinvests dividends and skips non-positive prints", () => {
  const tr = QL.trIndex([10, 10, 11], ["a", "b", "c"], [["b", 0.5]]);
  close(tr[1], 1.05); close(tr[2], 1.05 * 1.1);
  assert.deepStrictEqual(QL.trIndex([10, -1, 5], ["a", "b", "c"], []), [1, 1, 1]);
});

test("rolling correlation and beta equal the direct formulas", () => {
  const a = [], b = []; let s = 7;
  const rnd = () => ((s = (s * 16807) % 2147483647) / 2147483647) - 0.5;
  for (let i = 0; i < 100; i++) { const x = rnd(); a.push(x); b.push(0.6 * x + 0.3 * rnd()); }
  const r = QL.rollingCorrBeta(a, b, 30);
  close(r.corr[99], QL.pearson(a.slice(70), b.slice(70)));
  close(r.beta[99], QL.olsBeta(a.slice(70), b.slice(70)));
  assert.strictEqual(r.corr[20], null);
});

test("beta of a scaled series is the scale", () => { close(QL.olsBeta([1, 2, 3, 4], [2, 4, 6, 8]), 0.5); });

test("geometric capture", () => {
  const c = QL.capture([0.02, -0.01, 0.03, -0.02], [0.01, -0.02, 0.02, -0.01]);
  close(c.up, (Math.sqrt(1.02 * 1.03) - 1) / (Math.sqrt(1.01 * 1.02) - 1));
  assert.strictEqual(c.n_down, 2);
});

test("aligned benchmark returns use exact date matches", () => {
  const r = QL.alignedReturns(["a", "b", "c", "d"], ["a", "c", "d"], [1, 1.1, 1.21]);
  assert.deepStrictEqual(r.slice(0, 3), [null, null, null]);
  close(r[3], 0.1);
});

test("month-end returns drop the first and the current partial month", () => {
  const m = QL.monthEnd([1, 1.1, 1.2, 1.21, 1.3], ["2026-01-29", "2026-01-30", "2026-02-02", "2026-02-27", "2026-03-02"]);
  assert.strictEqual(m.length, 1); assert.strictEqual(m[0][0], "2026-02"); close(m[0][1], 1.21 / 1.1 - 1);
});
