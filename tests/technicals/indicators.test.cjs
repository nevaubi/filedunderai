// Indicator tests: TA-Lib reference outputs (reference.json, see make_reference.py), published worked examples,
// and brute-force re-implementations for indicators TA-Lib does not cover. Run: node --test tests/technicals/
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const fs = require("node:fs");
// the module is a plain browser script (the repo's package.json is type=module), so load it with a CommonJS shim
const TA = (() => { const m = { exports: {} }; new Function("module", fs.readFileSync(path.join(__dirname, "../../public/pages/technicals-lib.js"), "utf8"))(m); return m.exports; })();
const R = require("./reference.json");
const { o, h, l, c, v } = R;

// Compares two aligned series. `from` skips the warm-up where seeding conventions legitimately differ.
function close(name, got, want, { from = 0, tol = 1e-6 } = {}) {
  assert.equal(got.length, want.length, name + " length");
  let checked = 0, worst = 0;
  for (let i = from; i < want.length; i++) {
    if (want[i] == null) { continue; }
    assert.ok(got[i] != null, `${name}[${i}] is null, expected ${want[i]}`);
    const d = Math.abs(got[i] - want[i]) / Math.max(1, Math.abs(want[i]));
    worst = Math.max(worst, d);
    assert.ok(d <= tol, `${name}[${i}] = ${got[i]}, TA-Lib ${want[i]}`);
    checked++;
  }
  assert.ok(checked > 100, name + " compared too few values");
  return { checked, worst };
}
const RESULTS = [];
const rec = (name, r) => RESULTS.push(`${name}: ${r.checked} values, max rel. diff ${r.worst.toExponential(1)}`);

test("SMA, EMA, WMA match TA-Lib exactly", () => {
  rec("SMA(20)", close("sma", TA.sma(c, 20), R.sma20));
  rec("EMA(20)", close("ema", TA.ema(c, 20), R.ema20));
  rec("WMA(10)", close("wma", TA.wma(c, 10), R.wma10));
  // warm-up alignment: first value exactly where TA-Lib starts
  assert.equal(TA.sma(c, 20).findIndex(x => x != null), R.sma20.findIndex(x => x != null));
  assert.equal(TA.ema(c, 20).findIndex(x => x != null), R.ema20.findIndex(x => x != null));
});
test("Bollinger(20,2) matches TA-Lib BBANDS", () => {
  const b = TA.bollinger(c, 20, 2);
  rec("Bollinger upper", close("bb_upper", b.upper, R.bb_upper));
  close("bb_mid", b.mid, R.bb_mid); close("bb_lower", b.lower, R.bb_lower);
});
test("ATR(14) and RSI(14) (Wilder) match TA-Lib from the first value", () => {
  rec("ATR(14)", close("atr", TA.atr(h, l, c, 14), R.atr14));
  rec("RSI(14)", close("rsi", TA.rsi(c, 14), R.rsi14));
  assert.equal(TA.rsi(c, 14).findIndex(x => x != null), R.rsi14.findIndex(x => x != null));
});
test("Stochastic(14,3,3) matches TA-Lib STOCH", () => {
  const s = TA.stochastic(h, l, c, 14, 3, 3);
  rec("Stochastic %K", close("stoch_k", s.k, R.stoch_k));
  rec("Stochastic %D", close("stoch_d", s.d, R.stoch_d));
});
test("MACD(12,26,9) matches TA-Lib after the EMA seeds converge", () => {
  // TA-Lib seeds the fast EMA at the slow EMA's start; the difference decays geometrically.
  const m = TA.macd(c, 12, 26, 9);
  rec("MACD line", close("macd", m.line, R.macd, { from: 150 }));
  rec("MACD signal", close("macd_signal", m.signal, R.macd_signal, { from: 150 }));
  close("macd_hist", m.hist, R.macd_hist, { from: 150 });
});
test("ADX/DMI(14) matches TA-Lib after the Wilder seeds converge", () => {
  // TA-Lib seeds the smoothed DM and TR sums with n-1 bars; Wilder's book seeds with n. Converges within ~250 bars.
  const d = TA.adx(h, l, c, 14);
  rec("+DI(14)", close("pdi", d.pdi, R.pdi14, { from: 300 }));
  rec("-DI(14)", close("mdi", d.mdi, R.mdi14, { from: 300 }));
  rec("ADX(14)", close("adx", d.adx, R.adx14, { from: 300 }));
});
test("OBV and MFI(14) match TA-Lib", () => {
  // TA-Lib starts OBV at the first volume; this module starts at 0. Differences are a constant offset.
  const ob = TA.obv(c, v), off = R.obv[0] - ob[0];
  rec("OBV", close("obv", ob.map(x => x + off), R.obv));
  rec("MFI(14)", close("mfi", TA.mfi(h, l, c, v, 14), R.mfi14));
});

test("RSI worked example (Wilder method, StockCharts ChartSchool table)", () => {
  const cl = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08, 45.89, 46.03, 45.61, 46.28, 46.28, 46.00, 46.03, 46.41, 46.22, 45.64];
  const r = TA.rsi(cl, 14);
  const want = [70.53, 66.32, 66.55, 69.41, 66.36, 57.97];
  // The published table rounds its average gain/loss columns to two decimals, which moves RSI by up to ~0.08;
  // unrounded values (identical to TA-Lib) are checked exactly.
  want.forEach((x, k) => assert.ok(Math.abs(r[14 + k] - x) < 0.1, `RSI[${14 + k}] = ${r[14 + k]}, expected ${x}`));
  const exact = [70.46413502109705, 66.24961855355505, 66.48094183471265, 69.34685316290866, 66.29471265892624, 57.91502067008556];
  exact.forEach((x, k) => assert.ok(Math.abs(r[14 + k] - x) < 1e-9));
  RESULTS.push("RSI worked example: 6 values within 0.1 of the published (rounded) table, exact against unrounded TA-Lib");
});

// Brute-force definitions written independently of the module for indicators TA-Lib lacks.
const bruteATR = (n) => { const tr = c.map((x, i) => i ? Math.max(h[i] - l[i], Math.abs(h[i] - c[i - 1]), Math.abs(l[i] - c[i - 1])) : null); const o2 = Array(c.length).fill(null); let a = 0; for (let i = 1; i <= n; i++) a += tr[i]; a /= n; o2[n] = a; for (let i = n + 1; i < c.length; i++) { a = (a * (n - 1) + tr[i]) / n; o2[i] = a; } return o2; };
test("Keltner(20,2,10) = EMA(20) +- 2 x ATR(10)", () => {
  const k = TA.keltner(h, l, c, 20, 2, 10), e = R.ema20, a = bruteATR(10);
  rec("Keltner upper", close("kc_upper", k.upper, e.map((m, i) => m == null || a[i] == null ? null : m + 2 * a[i])));
  close("kc_lower", k.lower, e.map((m, i) => m == null || a[i] == null ? null : m - 2 * a[i]));
});
test("Ichimoku(9,26,52) lines are midpoints of rolling highs and lows", () => {
  const ic = TA.ichimoku(h, l, c);
  const mid = (i, n) => i < n - 1 ? null : (Math.max(...h.slice(i - n + 1, i + 1)) + Math.min(...l.slice(i - n + 1, i + 1))) / 2;
  const ten = c.map((_, i) => mid(i, 9)), kij = c.map((_, i) => mid(i, 26)), sb = c.map((_, i) => mid(i, 52));
  rec("Ichimoku tenkan", close("tenkan", ic.tenkan, ten));
  close("kijun", ic.kijun, kij); close("spanB", ic.spanB, sb);
  close("spanA", ic.spanA, ten.map((t, i) => t == null || kij[i] == null ? null : (t + kij[i]) / 2));
  assert.equal(ic.shift, 26);
});
test("Supertrend(10,3) matches a direct transcription of the TradingView script", () => {
  const n = 10, m = 3, a = bruteATR(n);
  const line = Array(c.length).fill(null), dir = Array(c.length).fill(null);
  let up = null, dn = null, trend = 1;
  for (let i = 0; i < c.length; i++) {
    if (a[i] == null) continue;
    const hl2 = (h[i] + l[i]) / 2; let u = hl2 - m * a[i], d = hl2 + m * a[i];
    if (up == null) { trend = c[i] >= hl2 ? 1 : -1; }
    else { u = c[i - 1] > up ? Math.max(u, up) : u; d = c[i - 1] < dn ? Math.min(d, dn) : d; trend = trend === -1 && c[i] > dn ? 1 : trend === 1 && c[i] < up ? -1 : trend; }
    up = u; dn = d; dir[i] = trend; line[i] = trend === 1 ? u : d;
  }
  const st = TA.supertrend(h, l, c, n, m);
  rec("Supertrend line", close("supertrend", st.line, line));
  assert.deepEqual(st.dir, dir);
  assert.ok(new Set(dir.filter(x => x != null)).size === 2, "series should contain both trend states");
});
test("VWAP: session reset and anchored cumulation", () => {
  const sess = c.map((_, i) => Math.floor(i / 78));
  const vw = TA.vwapSession(h, l, c, v, sess);
  for (const i of [0, 77, 78, 300, 599]) {
    const s0 = Math.floor(i / 78) * 78; let pv = 0, vv = 0;
    for (let j = s0; j <= i; j++) { pv += (h[j] + l[j] + c[j]) / 3 * v[j]; vv += v[j]; }
    assert.ok(Math.abs(vw[i] - pv / vv) < 1e-9, "vwap " + i);
  }
  const av = TA.anchoredVwap(h, l, c, v, 250);
  assert.equal(av[249], null);
  let pv = 0, vv = 0; for (let j = 250; j < c.length; j++) { pv += (h[j] + l[j] + c[j]) / 3 * v[j]; vv += v[j]; }
  assert.ok(Math.abs(av.at(-1) - pv / vv) < 1e-9);
  // worked example: two bars, typical prices 10 and 13, volumes 100 and 300 -> (1000 + 3900) / 400 = 12.25
  assert.equal(TA.anchoredVwap([11, 14], [9, 12], [10, 13], [100, 300], 0)[1], 12.25);
  RESULTS.push("VWAP: session and anchored values exact to 1e-9");
});
test("Chaikin money flow and relative strength", () => {
  // CMF worked example: close at the high gives multiplier +1, at the low -1
  assert.equal(TA.cmf([10, 10], [8, 8], [10, 8], [100, 300], 2)[1], (100 - 300) / 400);
  const rs = TA.relStrength([10, 11, 12], [20, 20, 30], 2);
  [100, 110, 80].forEach((x, k) => assert.ok(Math.abs(rs.ratio[k] - x) < 1e-9));
  assert.ok(Math.abs(rs.mansfield[2] - 100 * (80 / 95 - 1)) < 1e-12);
});

test("Event detection on constructed series", () => {
  // golden cross: long decline then a strong rally
  const s = [...Array(260)].map((_, i) => 100 - i * 0.1).concat([...Array(120)].map((_, i) => 74 + i * 0.6));
  const ev = TA.detect(s, s.map(x => x * 1.005), s.map(x => x * 0.995), s).events;
  const gc = ev.filter(e => e.type === "golden_cross");
  assert.equal(gc.length, 1);
  const s50 = TA.sma(s, 50), s200 = TA.sma(s, 200), i = gc[0].i;
  assert.ok(s50[i - 1] <= s200[i - 1] && s50[i] > s200[i]);
  assert.ok(ev.some(e => e.type === "high_52w"));
  // gap up: open above the prior high and 2% above the prior close
  const cc = [...Array(30)].map(() => 50), oo = cc.slice(), hh = cc.map(x => x + 0.5), ll = cc.map(x => x - 0.5);
  oo[20] = 52; hh[20] = 53; ll[20] = 51.5; cc[20] = 52.5;
  for (let k = 21; k < 30; k++) { oo[k] = cc[k] = 52.5; hh[k] = 53; ll[k] = 52; }
  const g = TA.detect(oo, hh, ll, cc).events.filter(e => e.type === "gap_up");
  assert.equal(g.length, 1); assert.equal(g[0].i, 20);
  const gf = TA.gapFills(g, hh, ll); assert.equal(gf[0].filled, null); assert.equal(gf[0].edge, 50.5);
});
test("Statistics: n_eff, Wilson interval, backtest base rate", () => {
  assert.equal(TA.nEff([0, 3, 10, 21, 22, 45], 20), 3);
  const [a, b] = TA.wilson(8, 10); assert.ok(Math.abs(a - 0.4902) < 1e-3 && Math.abs(b - 0.9433) < 1e-3);
  const tr = [...Array(400)].map((_, i) => 1.001 ** i);
  const bt = TA.backtest([{ type: "x", i: 260 }, { type: "x", i: 300 }], tr, [5, 20]);
  assert.ok(Math.abs(bt.base[5].mean - (1.001 ** 5 - 1)) < 1e-12);
  assert.equal(bt.signals.x[20].n, 2); assert.equal(bt.signals.x[20].n_eff, 2);
  assert.ok(Math.abs(bt.signals.x[20].excess) < 1e-12);
  assert.equal(bt.signals.x[20].label, "too few");
});
test("Risk measures", () => {
  const cl = [100, 110, 99, 120, 60, 90, 130];
  const md = TA.maxDrawdown(cl); assert.equal(md.dd, -0.5); assert.equal(md.peak, 3); assert.equal(md.trough, 4); assert.equal(md.recovered, 6);
  const rv = TA.realizedVol(c, 21), lr = TA.logRets(c).slice(-21);
  const m = lr.reduce((s, x) => s + x, 0) / 21, sd = Math.sqrt(lr.reduce((s, x) => s + (x - m) ** 2, 0) / 20);
  assert.ok(Math.abs(rv.at(-1) - sd * Math.sqrt(252)) < 1e-12);
  const mo = TA.moments([1, 2, 3, 4, 10]); // adjusted sample skewness and excess kurtosis, as pandas Series.skew() / .kurt() report: 1.697056, 3.152
  assert.ok(Math.abs(mo.skew - 1.697056) < 1e-6, "skew " + mo.skew); assert.ok(Math.abs(mo.kurt - 3.152) < 1e-9, "kurt " + mo.kurt);
  const se = TA.seasonality(["2024-01-30", "2024-01-31", "2024-02-28", "2024-02-29", "2024-03-28"], [1, 1.1, 1.2, 1.21, 1.331]);
  assert.equal(se.month[1].n, 1); assert.ok(Math.abs(se.month[1].mean - 0.1) < 1e-12);
});
test("Summary sentences only contain computed numbers", () => {
  const st = TA.state(o, h, l, c, v);
  const S = TA.summary(st);
  assert.ok(S.length >= 4);
  const values = { ...st, vs200: st.vs200, vs50: st.vs50 };
  assert.deepEqual(TA.validateSummary(S, values), []);
  assert.ok(TA.validateSummary([{ text: "RSI is 99.9", refs: ["rsi"] }], values).length === 1);
});

test.after(() => { console.log("\nIndicator checks\n  " + RESULTS.join("\n  ")); });
