// Technical indicators, pattern detection, signal statistics and risk measures for the Technicals page.
// Pure functions over plain arrays (no DOM). Loaded by the page, by its Web Worker (importScripts) and by the
// Node tests (require). Output arrays are aligned to the input; values before an indicator's warm-up are null.
// etl/technicals_calc.py mirrors the signal definitions for the S&P 500 statistics; tests/technicals check both.
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.TA = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";
  const N = a => a == null || !isFinite(a);
  const nulls = n => new Array(n).fill(null);

  /* ---------- moving averages ---------- */
  function sma(a, n) {
    const o = nulls(a.length); let s = 0, k = 0;
    for (let i = 0; i < a.length; i++) {
      if (N(a[i])) { s = 0; k = 0; continue; }
      s += a[i]; k++;
      if (k > n) { s -= a[i - n]; k = n; }
      if (k === n) o[i] = s / n;
    }
    return o;
  }
  // Exponential moving average seeded with the simple average of the first n values (the TA-Lib / StockCharts convention).
  function ema(a, n) {
    const o = nulls(a.length), k = 2 / (n + 1);
    let start = 0; while (start < a.length && N(a[start])) start++;
    if (a.length - start < n) return o;
    let e = 0; for (let i = start; i < start + n; i++) e += a[i]; e /= n;
    o[start + n - 1] = e;
    for (let i = start + n; i < a.length; i++) { if (N(a[i])) { o[i] = e; continue; } e = a[i] * k + e * (1 - k); o[i] = e; }
    return o;
  }
  // Wilder's smoothing (RMA): seeded with the simple average of the first n values, then alpha = 1/n.
  function rma(a, n) {
    const o = nulls(a.length);
    let start = 0; while (start < a.length && N(a[start])) start++;
    if (a.length - start < n) return o;
    let e = 0; for (let i = start; i < start + n; i++) e += a[i]; e /= n;
    o[start + n - 1] = e;
    for (let i = start + n; i < a.length; i++) { e = (e * (n - 1) + (N(a[i]) ? e : a[i])) / n; o[i] = e; }
    return o;
  }
  function wma(a, n) {
    const o = nulls(a.length), den = n * (n + 1) / 2;
    for (let i = n - 1; i < a.length; i++) { let s = 0, ok = true; for (let j = 0; j < n; j++) { const v = a[i - n + 1 + j]; if (N(v)) { ok = false; break; } s += v * (j + 1); } if (ok) o[i] = s / den; }
    return o;
  }
  // Rolling population standard deviation (Bollinger's definition).
  function stdev(a, n) {
    const o = nulls(a.length);
    for (let i = n - 1; i < a.length; i++) {
      let s = 0, ok = true; for (let j = i - n + 1; j <= i; j++) { if (N(a[j])) { ok = false; break; } s += a[j]; }
      if (!ok) continue; const m = s / n; let v = 0; for (let j = i - n + 1; j <= i; j++) v += (a[j] - m) ** 2; o[i] = Math.sqrt(v / n);
    }
    return o;
  }
  const rollMax = (a, n) => a.map((_, i) => { if (i < n - 1) return null; let m = -Infinity; for (let j = i - n + 1; j <= i; j++) if (a[j] > m) m = a[j]; return m; });
  const rollMin = (a, n) => a.map((_, i) => { if (i < n - 1) return null; let m = Infinity; for (let j = i - n + 1; j <= i; j++) if (a[j] < m) m = a[j]; return m; });

  /* ---------- bands and channels ---------- */
  function bollinger(c, n = 20, k = 2) {
    const mid = sma(c, n), sd = stdev(c, n);
    const upper = mid.map((m, i) => m == null ? null : m + k * sd[i]), lower = mid.map((m, i) => m == null ? null : m - k * sd[i]);
    const width = mid.map((m, i) => m == null || !m ? null : (upper[i] - lower[i]) / m);
    const pctb = c.map((x, i) => upper[i] == null || upper[i] === lower[i] ? null : (x - lower[i]) / (upper[i] - lower[i]));
    return { mid, upper, lower, width, pctb };
  }
  function trueRange(h, l, c) { return h.map((x, i) => i === 0 ? null : Math.max(x - l[i], Math.abs(x - c[i - 1]), Math.abs(l[i] - c[i - 1]))); }
  // Average true range, Wilder smoothing; first value at index n (mean of the first n true ranges), as in TA-Lib.
  function atr(h, l, c, n = 14) { return rma(trueRange(h, l, c), n); }
  function keltner(h, l, c, n = 20, mult = 2, atrN = 10) {
    const mid = ema(c, n), a = atr(h, l, c, atrN);
    return { mid, upper: mid.map((m, i) => m == null || a[i] == null ? null : m + mult * a[i]), lower: mid.map((m, i) => m == null || a[i] == null ? null : m - mult * a[i]) };
  }
  function donchian(h, l, n = 20) { const up = rollMax(h, n), lo = rollMin(l, n); return { upper: up, lower: lo, mid: up.map((u, i) => u == null ? null : (u + lo[i]) / 2) }; }
  // Ichimoku: conversion and base lines; leading spans are computed at bar i and plotted `shift` bars ahead; the lagging span is the close plotted `shift` bars back.
  function ichimoku(h, l, c, conv = 9, base = 26, spanB = 52, shift = 26) {
    const mid = n => { const hi = rollMax(h, n), lo = rollMin(l, n); return hi.map((x, i) => x == null ? null : (x + lo[i]) / 2); };
    const tenkan = mid(conv), kijun = mid(base), sB = mid(spanB);
    return { tenkan, kijun, spanA: tenkan.map((t, i) => t == null || kijun[i] == null ? null : (t + kijun[i]) / 2), spanB: sB, lagging: c.slice(), shift };
  }
  // Supertrend on Wilder ATR with final-band carry-over (the common TradingView definition: the trend flips when the
  // close crosses the previous bar's final band); dir 1 = up trend (line below price), -1 = down trend.
  function supertrend(h, l, c, n = 10, mult = 3) {
    const a = atr(h, l, c, n), L = c.length, line = nulls(L), dir = nulls(L);
    let fu = null, fl = null, d = 1;
    for (let i = 0; i < L; i++) {
      if (a[i] == null) continue;
      const hl2 = (h[i] + l[i]) / 2, bu = hl2 + mult * a[i], bl = hl2 - mult * a[i];
      if (fu == null) { fu = bu; fl = bl; d = c[i] >= hl2 ? 1 : -1; }
      else {
        const pu = fu, pl = fl;
        fu = bu < pu || c[i - 1] > pu ? bu : pu;
        fl = bl > pl || c[i - 1] < pl ? bl : pl;
        if (d === 1 && c[i] < pl) d = -1; else if (d === -1 && c[i] > pu) d = 1;
      }
      dir[i] = d; line[i] = d === 1 ? fl : fu;
    }
    return { line, dir };
  }

  /* ---------- oscillators ---------- */
  // Wilder RSI; first value at index n.
  function rsi(c, n = 14) {
    const o = nulls(c.length); if (c.length <= n) return o;
    let g = 0, ls = 0;
    for (let i = 1; i <= n; i++) { const d = c[i] - c[i - 1]; if (d > 0) g += d; else ls -= d; }
    g /= n; ls /= n;
    o[n] = ls === 0 ? (g === 0 ? 50 : 100) : 100 - 100 / (1 + g / ls);
    for (let i = n + 1; i < c.length; i++) {
      const d = c[i] - c[i - 1];
      g = (g * (n - 1) + Math.max(d, 0)) / n; ls = (ls * (n - 1) + Math.max(-d, 0)) / n;
      o[i] = ls === 0 ? (g === 0 ? 50 : 100) : 100 - 100 / (1 + g / ls);
    }
    return o;
  }
  // Slow stochastic: %K raw over kN bars, smoothed by a kSmooth SMA; %D is a dN SMA of %K.
  function stochastic(h, l, c, kN = 14, kSmooth = 3, dN = 3) {
    const hi = rollMax(h, kN), lo = rollMin(l, kN);
    const raw = c.map((x, i) => hi[i] == null ? null : hi[i] === lo[i] ? 50 : 100 * (x - lo[i]) / (hi[i] - lo[i]));
    const k = sma(raw, kSmooth), d = sma(k, dN);
    return { raw, k, d };
  }
  function macd(c, fast = 12, slow = 26, sig = 9) {
    const ef = ema(c, fast), es = ema(c, slow);
    const line = c.map((_, i) => ef[i] == null || es[i] == null ? null : ef[i] - es[i]);
    const signal = ema(line, sig);
    return { line, signal, hist: line.map((x, i) => x == null || signal[i] == null ? null : x - signal[i]) };
  }
  // Wilder's directional movement: +DI, -DI and ADX. +DI/-DI from index n, ADX from index 2n - 1.
  function adx(h, l, c, n = 14) {
    const L = c.length, pdm = nulls(L), mdm = nulls(L);
    for (let i = 1; i < L; i++) { const up = h[i] - h[i - 1], dn = l[i - 1] - l[i]; pdm[i] = up > dn && up > 0 ? up : 0; mdm[i] = dn > up && dn > 0 ? dn : 0; }
    const tr = trueRange(h, l, c), sTR = rma(tr, n), sP = rma(pdm, n), sM = rma(mdm, n);
    const pdi = sTR.map((t, i) => t == null ? null : t === 0 ? 0 : 100 * sP[i] / t), mdi = sTR.map((t, i) => t == null ? null : t === 0 ? 0 : 100 * sM[i] / t);
    const dx = pdi.map((p, i) => p == null ? null : p + mdi[i] === 0 ? 0 : 100 * Math.abs(p - mdi[i]) / (p + mdi[i]));
    return { pdi, mdi, dx, adx: rma(dx, n) };
  }
  function obv(c, v) { const o = nulls(c.length); if (!c.length) return o; let s = 0; o[0] = 0; for (let i = 1; i < c.length; i++) { s += c[i] > c[i - 1] ? v[i] : c[i] < c[i - 1] ? -v[i] : 0; o[i] = s; } return o; }
  // Money Flow Index over n bars of typical price times volume; first value at index n.
  function mfi(h, l, c, v, n = 14) {
    const L = c.length, o = nulls(L), tp = c.map((x, i) => (h[i] + l[i] + x) / 3), pos = nulls(L), neg = nulls(L);
    for (let i = 1; i < L; i++) { const f = tp[i] * v[i]; pos[i] = tp[i] > tp[i - 1] ? f : 0; neg[i] = tp[i] < tp[i - 1] ? f : 0; }
    for (let i = n; i < L; i++) { let p = 0, q = 0; for (let j = i - n + 1; j <= i; j++) { p += pos[j]; q += neg[j]; } o[i] = p + q === 0 ? 50 : q === 0 ? 100 : 100 - 100 / (1 + p / q); }
    return o;
  }
  // Chaikin money flow over n bars.
  function cmf(h, l, c, v, n = 20) {
    const mfv = c.map((x, i) => h[i] === l[i] ? 0 : ((x - l[i]) - (h[i] - x)) / (h[i] - l[i]) * v[i]);
    return c.map((_, i) => { if (i < n - 1) return null; let a = 0, b = 0; for (let j = i - n + 1; j <= i; j++) { a += mfv[j]; b += v[j]; } return b ? a / b : 0; });
  }
  // Session VWAP for intraday bars: resets when the session key changes.
  function vwapSession(h, l, c, v, session) {
    let pv = 0, vv = 0, key = null;
    return c.map((x, i) => { if (session[i] !== key) { pv = 0; vv = 0; key = session[i]; } pv += (h[i] + l[i] + x) / 3 * v[i]; vv += v[i]; return vv ? pv / vv : (h[i] + l[i] + x) / 3; });
  }
  // VWAP anchored at bar `from` (typical price times volume, cumulated from the anchor).
  function anchoredVwap(h, l, c, v, from) {
    let pv = 0, vv = 0;
    return c.map((x, i) => { if (i < from) return null; pv += (h[i] + l[i] + x) / 3 * v[i]; vv += v[i]; return vv ? pv / vv : (h[i] + l[i] + x) / 3; });
  }
  // Price ratio against a benchmark aligned on the same dates, rebased to 100 at the first common bar,
  // plus Mansfield relative strength: ratio over its n-bar simple average, minus 1, in percent.
  function relStrength(c, b, n = 250) {
    let base = null;
    const ratio = c.map((x, i) => { if (N(x) || N(b[i]) || !b[i]) return null; const r = x / b[i]; if (base == null) base = r; return 100 * r / base; });
    const avg = sma(ratio, n);
    return { ratio, mansfield: ratio.map((r, i) => r == null || avg[i] == null ? null : 100 * (r / avg[i] - 1)) };
  }

  /* ---------- swings, levels, trend lines ---------- */
  // Pivot highs/lows: bar i is a swing high if its high is the strict maximum of the k bars on each side. Known only k bars later.
  function swings(h, l, k = 5) {
    const highs = [], lows = [];
    for (let i = k; i < h.length - k; i++) {
      let hi = true, lo = true;
      for (let j = i - k; j <= i + k; j++) { if (j === i) continue; if (h[j] >= h[i]) hi = false; if (l[j] <= l[i]) lo = false; if (!hi && !lo) break; }
      if (hi) highs.push(i); if (lo) lows.push(i);
    }
    return { highs, lows };
  }
  // Horizontal support/resistance: swing prices within `tol` (fraction of price, default from ATR) are clustered;
  // each level reports its price (mean of members), the swing bars that define it and the number of touches.
  function levels(h, l, c, opt = {}) {
    const k = opt.k || 5, look = opt.lookback || 504, minTouches = opt.minTouches || 2;
    const L = c.length, from = Math.max(0, L - look);
    const sw = swings(h.slice(from), l.slice(from), k);
    const a = atr(h, l, c, 14), lastAtr = a[L - 1] || (c[L - 1] * 0.02);
    const tol = opt.tol || Math.max(0.006 * c[L - 1], 0.5 * lastAtr);
    const pts = [...sw.highs.map(i => ({ i: i + from, p: h[i + from], kind: "high" })), ...sw.lows.map(i => ({ i: i + from, p: l[i + from], kind: "low" }))].sort((x, y) => x.p - y.p);
    const clusters = [];
    for (const p of pts) { const cl = clusters.at(-1); if (cl && p.p - cl.members.at(-1).p <= tol) cl.members.push(p); else clusters.push({ members: [p] }); }
    const last = c[L - 1];
    return clusters.filter(cl => cl.members.length >= minTouches).map(cl => {
      const price = cl.members.reduce((s, m) => s + m.p, 0) / cl.members.length;
      const idx = cl.members.map(m => m.i).sort((x, y) => x - y);
      return { price, touches: cl.members.length, bars: idx, first: idx[0], last: idx.at(-1), kind: price >= last ? "resistance" : "support", highs: cl.members.filter(m => m.kind === "high").length, lows: cl.members.filter(m => m.kind === "low").length, distance: price / last - 1 };
    }).sort((x, y) => Math.abs(x.distance) - Math.abs(y.distance)).slice(0, opt.max || 8);
  }
  // Trend lines through pairs of swing points (lows for rising support, highs for falling resistance) within the lookback.
  // A line is kept if no close between its anchors and the last bar crosses it by more than tol; touches count swing
  // points within tol of the line. Returns the best rising and falling line.
  function trendLines(h, l, c, opt = {}) {
    const k = opt.k || 5, look = opt.lookback || 252, L = c.length, from = Math.max(0, L - look);
    const sw = swings(h, l, k);
    const a = atr(h, l, c, 14), tol = opt.tol || 0.5 * (a[L - 1] || c[L - 1] * 0.02);
    const out = [];
    const fit = (pts, price, side) => {
      let best = null;
      const cand = pts.filter(i => i >= from);
      for (let x = 0; x < cand.length; x++) for (let y = x + 1; y < cand.length; y++) {
        const i1 = cand[x], i2 = cand[y]; if (i2 - i1 < 10) continue;
        const slope = (price[i2] - price[i1]) / (i2 - i1);
        if (side === "support" && slope <= 0) continue;
        if (side === "resistance" && slope >= 0) continue;
        const at = i => price[i1] + slope * (i - i1);
        let ok = true;
        for (let i = i1; i < L; i++) { const v = at(i); if (side === "support" ? c[i] < v - tol : c[i] > v + tol) { ok = false; break; } }
        if (!ok) continue;
        const touches = cand.filter(i => i >= i1 && Math.abs(price[i] - at(i)) <= tol);
        const score = touches.length * 1000 + (i2 - i1);
        if (!best || score > best.score) best = { side, i1, i2, p1: price[i1], p2: price[i2], slope, touches: touches.length, bars: touches, score, now: at(L - 1) };
      }
      return best;
    };
    const s = fit(sw.lows, l, "support"), r = fit(sw.highs, h, "resistance");
    if (s) out.push(s); if (r) out.push(r);
    return out;
  }

  /* ---------- events ---------- */
  // Signal types used for the chart and for the statistics. Every definition uses only data up to the event bar.
  const SIGNALS = {
    golden_cross: { name: "Golden cross", dir: 1, text: "50-day average crossed above the 200-day" },
    death_cross: { name: "Death cross", dir: -1, text: "50-day average crossed below the 200-day" },
    high_52w: { name: "52-week high break", dir: 1, text: "First close above the prior 52-week high (after 20 bars without one)" },
    low_52w: { name: "52-week low break", dir: -1, text: "First close below the prior 52-week low (after 20 bars without one)" },
    gap_up: { name: "Gap up", dir: 1, text: "Opened above the prior high and at least 2% above the prior close" },
    gap_down: { name: "Gap down", dir: -1, text: "Opened below the prior low and at least 2% below the prior close" },
    rsi_oversold: { name: "RSI below 30", dir: 1, text: "RSI(14) crossed below 30" },
    rsi_overbought: { name: "RSI above 70", dir: -1, text: "RSI(14) crossed above 70" },
    rsi_bull_div: { name: "Bullish RSI divergence", dir: 1, text: "Lower swing low in price with a higher RSI(14) low; confirmed 5 bars after the second low" },
    rsi_bear_div: { name: "Bearish RSI divergence", dir: -1, text: "Higher swing high in price with a lower RSI(14) high; confirmed 5 bars after the second high" },
    macd_bull: { name: "MACD bullish cross", dir: 1, text: "MACD(12,26) crossed above its 9-day signal line" },
    macd_bear: { name: "MACD bearish cross", dir: -1, text: "MACD(12,26) crossed below its 9-day signal line" },
    squeeze_bull: { name: "Squeeze release up", dir: 1, text: "Bollinger(20,2) left the Keltner(20,1.5) channel after 6+ bars inside, close above the 20-day average" },
    squeeze_bear: { name: "Squeeze release down", dir: -1, text: "Bollinger(20,2) left the Keltner(20,1.5) channel after 6+ bars inside, close below the 20-day average" },
    st_up: { name: "Supertrend turns up", dir: 1, text: "Supertrend(10,3) flipped to an up trend" },
    st_down: { name: "Supertrend turns down", dir: -1, text: "Supertrend(10,3) flipped to a down trend" },
  };
  const COOLDOWN = 10, BREAK_GAP = 20, DIV_K = 5, DIV_MIN = 10, DIV_MAX = 60;
  // Returns { events: [{type, i, bars}], squeeze: bool[] }. `bars` are the indices that define the event.
  function detect(o, h, l, c) {
    const L = c.length, ev = [];
    const s50 = sma(c, 50), s200 = sma(c, 200), r = rsi(c, 14), m = macd(c), st = supertrend(h, l, c, 10, 3);
    const bb = bollinger(c, 20, 2), kc = keltner(h, l, c, 20, 1.5, 20);
    const squeeze = c.map((_, i) => bb.upper[i] != null && kc.upper[i] != null && bb.upper[i] < kc.upper[i] && bb.lower[i] > kc.lower[i]);
    const lastAt = {};
    const push = (type, i, bars) => { if (lastAt[type] != null && i - lastAt[type] < COOLDOWN) return; lastAt[type] = i; ev.push({ type, i, bars }); };
    let lastHi = -1e9, lastLo = -1e9, sqRun = squeeze[0] ? 1 : 0;
    for (let i = 1; i < L; i++) {
      if (s50[i - 1] != null && s200[i - 1] != null) {
        if (s50[i - 1] <= s200[i - 1] && s50[i] > s200[i]) push("golden_cross", i, [i]);
        if (s50[i - 1] >= s200[i - 1] && s50[i] < s200[i]) push("death_cross", i, [i]);
      }
      if (i >= 252) {
        let ph = -Infinity, pl = Infinity, phI = -1, plI = -1;
        for (let j = i - 252; j < i; j++) { if (h[j] > ph) { ph = h[j]; phI = j; } if (l[j] < pl) { pl = l[j]; plI = j; } }
        if (c[i] > ph) { if (i - lastHi > BREAK_GAP) ev.push({ type: "high_52w", i, bars: [phI, i] }); lastHi = i; }
        if (c[i] < pl) { if (i - lastLo > BREAK_GAP) ev.push({ type: "low_52w", i, bars: [plI, i] }); lastLo = i; }
      }
      if (o[i] > h[i - 1] && o[i] >= c[i - 1] * 1.02) push("gap_up", i, [i - 1, i]);
      if (o[i] < l[i - 1] && o[i] <= c[i - 1] * 0.98) push("gap_down", i, [i - 1, i]);
      if (r[i - 1] != null) {
        if (r[i - 1] >= 30 && r[i] < 30) push("rsi_oversold", i, [i]);
        if (r[i - 1] <= 70 && r[i] > 70) push("rsi_overbought", i, [i]);
      }
      if (m.hist[i - 1] != null) {
        if (m.hist[i - 1] <= 0 && m.hist[i] > 0) push("macd_bull", i, [i]);
        if (m.hist[i - 1] >= 0 && m.hist[i] < 0) push("macd_bear", i, [i]);
      }
      if (st.dir[i - 1] != null && st.dir[i] !== st.dir[i - 1]) push(st.dir[i] === 1 ? "st_up" : "st_down", i, [i]);
      if (squeeze[i]) sqRun++;
      else { if (squeeze[i - 1] && sqRun >= 6 && bb.mid[i] != null) push(c[i] > bb.mid[i] ? "squeeze_bull" : "squeeze_bear", i, [i - sqRun, i - 1, i]); sqRun = 0; }
    }
    // divergences: compare each confirmed swing with the previous swing of the same kind
    const sw = swings(h, l, DIV_K);
    for (let x = 1; x < sw.lows.length; x++) {
      const a = sw.lows[x - 1], b = sw.lows[x], i = b + DIV_K;
      if (i >= L || b - a < DIV_MIN || b - a > DIV_MAX || r[a] == null) continue;
      if (l[b] < l[a] && r[b] > r[a] && r[a] < 40) push("rsi_bull_div", i, [a, b]);
    }
    for (let x = 1; x < sw.highs.length; x++) {
      const a = sw.highs[x - 1], b = sw.highs[x], i = b + DIV_K;
      if (i >= L || b - a < DIV_MIN || b - a > DIV_MAX || r[a] == null) continue;
      if (h[b] > h[a] && r[b] < r[a] && r[a] > 60) push("rsi_bear_div", i, [a, b]);
    }
    ev.sort((p, q) => p.i - q.i || (p.type < q.type ? -1 : 1));
    return { events: ev, squeeze };
  }
  // Gaps that remain open: for each gap event, the bar where price traded back through the prior bar's range, if any.
  function gapFills(ev, h, l) {
    return ev.filter(e => e.type === "gap_up" || e.type === "gap_down").map(e => {
      const p = e.i - 1, up = e.type === "gap_up", edge = up ? h[p] : l[p];
      let fill = null; for (let j = e.i; j < h.length; j++) if (up ? l[j] <= edge : h[j] >= edge) { fill = j; break; }
      return { ...e, edge, top: up ? l[e.i] : edge, bottom: up ? edge : h[e.i], filled: fill };
    });
  }

  /* ---------- statistics ---------- */
  const quantile = (sorted, q) => { if (!sorted.length) return null; const pos = (sorted.length - 1) * q, lo = Math.floor(pos), hi = Math.ceil(pos); return sorted[lo] + (sorted[hi] - sorted[lo]) * (pos - lo); };
  const median = a => quantile([...a].sort((x, y) => x - y), 0.5);
  const meanOf = a => a.length ? a.reduce((s, x) => s + x, 0) / a.length : null;
  function sdOf(a) { if (a.length < 2) return null; const m = meanOf(a); return Math.sqrt(a.reduce((s, x) => s + (x - m) ** 2, 0) / (a.length - 1)); }
  function wilson(k, n, z = 1.96) { if (!n) return [null, null]; const p = k / n, d = 1 + z * z / n, c = (p + z * z / (2 * n)) / d, w = z * Math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d; return [c - w, c + w]; }
  // Events closer than the horizon share part of their forward window. n_eff counts events kept greedily at least
  // `h` bars apart; standard errors and intervals use n_eff, so overlapping windows do not overstate precision.
  function nEff(idx, hz) { let n = 0, lastI = -Infinity; for (const i of idx) if (i - lastI >= hz) { n++; lastI = i; } return n; }
  function summarize(vals, idx, hz) {
    const n = vals.length;
    if (!n) return { n: 0, n_eff: 0 };
    const ne = nEff(idx, hz), m = meanOf(vals), s = sdOf(vals), wins = vals.filter(v => v > 0).length;
    const se = s != null && ne > 1 ? s / Math.sqrt(ne) : null;
    return { n, n_eff: ne, mean: m, median: median(vals), hit: wins / n, sd: s, ci: se == null ? null : [m - 1.96 * se, m + 1.96 * se], hit_ci: wilson(Math.round(wins / n * ne), ne) };
  }
  const SAMPLE = ne => ne < 10 ? "too few" : ne < 30 ? "small sample" : "";
  // Forward returns after each signal versus all days. tr is a total-return index aligned to the bars.
  // Only bars from `start` (default 252, after every indicator's warm-up) are used for both signals and the base rate.
  function backtest(events, tr, horizons = [5, 20, 60], start = 252) {
    const L = tr.length, out = { horizons, base: {}, signals: {} };
    for (const hz of horizons) {
      const v = []; for (let i = start; i + hz < L; i++) v.push(tr[i + hz] / tr[i] - 1);
      out.base[hz] = { n: v.length, mean: meanOf(v), median: median(v), hit: v.length ? v.filter(x => x > 0).length / v.length : null };
    }
    const types = [...new Set(events.map(e => e.type))];
    for (const t of types) {
      const evs = events.filter(e => e.type === t && e.i >= start);
      out.signals[t] = {};
      for (const hz of horizons) {
        const ok = evs.filter(e => e.i + hz < L);
        const st = summarize(ok.map(e => tr[e.i + hz] / tr[e.i] - 1), ok.map(e => e.i), hz);
        if (st.n) { st.excess = st.mean - out.base[hz].mean; st.label = SAMPLE(st.n_eff); }
        st.pending = evs.length - ok.length;
        out.signals[t][hz] = st;
      }
    }
    return out;
  }

  /* ---------- volatility and risk ---------- */
  function logRets(c) { const r = nulls(c.length); for (let i = 1; i < c.length; i++) if (c[i] > 0 && c[i - 1] > 0) r[i] = Math.log(c[i] / c[i - 1]); return r; }
  // Annualized close-to-close volatility over n bars (sample SD of log returns times sqrt 252).
  function realizedVol(c, n) {
    const r = logRets(c), o = nulls(c.length);
    for (let i = n; i < c.length; i++) { let s = 0, s2 = 0; for (let j = i - n + 1; j <= i; j++) { s += r[j]; s2 += r[j] * r[j]; } const m = s / n; o[i] = Math.sqrt(Math.max(0, (s2 - n * m * m) / (n - 1)) * 252); }
    return o;
  }
  // Volatility cone: distribution of n-bar realized vol across the history (sampled every bar) and its current value.
  function volCone(c, windows = [10, 21, 63, 126, 252]) {
    return windows.map(n => {
      const rv = realizedVol(c, n), v = rv.filter(x => x != null).sort((a, b) => a - b), cur = rv[rv.length - 1];
      return { n, count: v.length, min: v[0], p10: quantile(v, 0.1), p25: quantile(v, 0.25), p50: quantile(v, 0.5), p75: quantile(v, 0.75), p90: quantile(v, 0.9), max: v.at(-1), current: cur, pct: cur == null ? null : v.filter(x => x <= cur).length / v.length };
    });
  }
  function drawdown(c) { let pk = -Infinity, pkI = 0; return c.map((x, i) => { if (x > pk) { pk = x; pkI = i; } return { dd: x / pk - 1, peak: pkI }; }); }
  function maxDrawdown(c) {
    const d = drawdown(c); let worst = 0, at = 0; d.forEach((x, i) => { if (x.dd < worst) { worst = x.dd; at = i; } });
    let rec = null; for (let i = at; i < c.length; i++) if (c[i] >= c[d[at].peak]) { rec = i; break; }
    return { dd: worst, peak: d[at].peak, trough: at, recovered: rec };
  }
  function moments(r) {
    const v = r.filter(x => x != null && isFinite(x)), n = v.length; if (n < 4) return null;
    const m = meanOf(v), s = sdOf(v); let m3 = 0, m4 = 0; for (const x of v) { m3 += ((x - m) / s) ** 3; m4 += ((x - m) / s) ** 4; }
    const skew = n / ((n - 1) * (n - 2)) * m3;
    const kurt = n * (n + 1) / ((n - 1) * (n - 2) * (n - 3)) * m4 - 3 * (n - 1) ** 2 / ((n - 2) * (n - 3));
    const sorted = [...v].sort((a, b) => a - b);
    const beyond = k => v.filter(x => Math.abs(x - m) > k * s).length;
    return { n, mean: m, sd: s, skew, kurt, p1: quantile(sorted, 0.01), p5: quantile(sorted, 0.05), p95: quantile(sorted, 0.95), p99: quantile(sorted, 0.99), min: sorted[0], max: sorted.at(-1), beyond2: beyond(2), beyond3: beyond(3), normal2: 0.0455 * n, normal3: 0.0027 * n };
  }
  function histogram(v, bins = 60, clip = 0.005) {
    const s = v.filter(x => x != null && isFinite(x)).sort((a, b) => a - b); if (!s.length) return [];
    const lo = quantile(s, clip), hi = quantile(s, 1 - clip), w = (hi - lo) / bins || 1, out = Array.from({ length: bins }, (_, k) => ({ x0: lo + k * w, x1: lo + (k + 1) * w, n: 0 }));
    for (const x of s) { const k = Math.min(bins - 1, Math.max(0, Math.floor((x - lo) / w))); out[k].n++; }
    return out;
  }
  // Rolling beta and correlation of a's returns against b's (both arrays already aligned, nulls skipped).
  function rollingBetaCorr(a, b, n) {
    const L = a.length, beta = nulls(L), corr = nulls(L);
    for (let i = n; i < L; i++) {
      let k = 0, sa = 0, sb = 0, saa = 0, sbb = 0, sab = 0;
      for (let j = i - n + 1; j <= i; j++) { const x = a[j], y = b[j]; if (N(x) || N(y)) continue; k++; sa += x; sb += y; saa += x * x; sbb += y * y; sab += x * y; }
      if (k < n * 0.8) continue;
      const cov = sab - sa * sb / k, va = saa - sa * sa / k, vb = sbb - sb * sb / k;
      if (vb > 0) beta[i] = cov / vb; if (va > 0 && vb > 0) corr[i] = cov / Math.sqrt(va * vb);
    }
    return { beta, corr };
  }
  // Average return by calendar month (month-end to month-end) and by weekday (close to close), from a total-return index.
  function seasonality(days, tr) {
    const months = Array.from({ length: 12 }, () => []), dows = Array.from({ length: 5 }, () => []), monthRows = [];
    let prevEnd = null;
    for (let i = 0; i < days.length; i++) {
      const d = days[i], next = days[i + 1];
      if (!next || next.slice(0, 7) !== d.slice(0, 7)) { if (prevEnd != null) { const r = tr[i] / tr[prevEnd] - 1; months[+d.slice(5, 7) - 1].push(r); monthRows.push([d.slice(0, 7), r]); } prevEnd = i; }
      if (i) { const wd = new Date(d + "T00:00:00Z").getUTCDay(); if (wd >= 1 && wd <= 5) dows[wd - 1].push(tr[i] / tr[i - 1] - 1); }
    }
    const sum = a => ({ n: a.length, mean: meanOf(a), median: a.length ? median(a) : null, hit: a.length ? a.filter(x => x > 0).length / a.length : null, sd: sdOf(a) });
    return { month: months.map(sum), dow: dows.map(sum), monthRows };
  }

  /* ---------- state and summary ---------- */
  function state(o, h, l, c, v) {
    const L = c.length, i = L - 1;
    const s20 = sma(c, 20), s50 = sma(c, 50), s200 = sma(c, 200), r = rsi(c, 14), a = atr(h, l, c, 14), m = macd(c), d = adx(h, l, c, 14), st = supertrend(h, l, c, 10, 3);
    const bb = bollinger(c, 20, 2), kc = keltner(h, l, c, 20, 1.5, 20);
    const look = Math.min(252, L); let hi = -Infinity, lo = Infinity, hiI = i, loI = i;
    for (let j = L - look; j < L; j++) { if (h[j] > hi) { hi = h[j]; hiI = j; } if (l[j] < lo) { lo = l[j]; loI = j; } }
    let sq = 0; for (let j = i; j >= 0; j--) { if (bb.upper[j] != null && kc.upper[j] != null && bb.upper[j] < kc.upper[j] && bb.lower[j] > kc.lower[j]) sq++; else break; }
    const slope50 = s50[i] != null && s50[i - 20] != null ? s50[i] / s50[i - 20] - 1 : null;
    const bwHist = bb.width.slice(-252).filter(x => x != null).sort((x, y) => x - y);
    const trend = s50[i] == null || s200[i] == null ? null : c[i] > s50[i] && s50[i] > s200[i] ? "up" : c[i] < s50[i] && s50[i] < s200[i] ? "down" : "mixed";
    return {
      i, close: c[i], sma20: s20[i], sma50: s50[i], sma200: s200[i], vs50: s50[i] ? c[i] / s50[i] - 1 : null, vs200: s200[i] ? c[i] / s200[i] - 1 : null, slope50,
      rsi: r[i], atr: a[i], atrPct: a[i] != null ? a[i] / c[i] : null, macd: m.line[i], macdSig: m.signal[i], macdHist: m.hist[i], adx: d.adx[i], pdi: d.pdi[i], mdi: d.mdi[i],
      st: st.dir[i], stLine: st.line[i], hi52: hi, hi52i: hiI, lo52: lo, lo52i: loI, fromHi: c[i] / hi - 1, fromLo: c[i] / lo - 1,
      squeeze: sq, bbWidth: bb.width[i], bbWidthPct: bb.width[i] == null || !bwHist.length ? null : bwHist.filter(x => x <= bb.width[i]).length / bwHist.length, trend,
    };
  }
  const pct = (x, d = 1) => (x >= 0 ? "+" : "−") + Math.abs(x * 100).toFixed(d) + "%";
  // Plain-English summary built only from computed values. Each sentence lists the values it uses as `refs`.
  function summary(st, extra = {}) {
    const S = [];
    if (st.sma200 != null) S.push({ text: `Price is ${pct(st.vs200)} from its 200-day average and ${pct(st.vs50)} from its 50-day average; the 50-day is ${st.sma50 > st.sma200 ? "above" : "below"} the 200-day${st.trend === "up" ? ", a rising trend arrangement" : st.trend === "down" ? ", a falling trend arrangement" : ", a mixed arrangement"}.`, refs: ["vs200", "vs50", "sma50", "sma200"] });
    else if (st.sma50 != null) S.push({ text: `Price is ${pct(st.vs50)} from its 50-day average; there is not enough history for a 200-day average.`, refs: ["vs50"] });
    if (st.adx != null) S.push({ text: `ADX(14) is ${st.adx.toFixed(1)}, ${st.adx >= 25 ? "a trending reading" : st.adx < 20 ? "a weak-trend reading" : "between weak and trending"}; +DI ${st.pdi.toFixed(1)} vs −DI ${st.mdi.toFixed(1)}.`, refs: ["adx", "pdi", "mdi"] });
    if (st.rsi != null) S.push({ text: `RSI(14) is ${st.rsi.toFixed(1)}${st.rsi >= 70 ? ", above the conventional overbought line of 70" : st.rsi <= 30 ? ", below the conventional oversold line of 30" : ""}; MACD is ${st.macdHist >= 0 ? "above" : "below"} its signal line.`, refs: ["rsi", "macdHist"] });
    S.push({ text: `The close is ${pct(st.fromHi)} from the 52-week high of ${st.hi52.toFixed(2)} and ${pct(st.fromLo)} above the 52-week low of ${st.lo52.toFixed(2)}.`, refs: ["fromHi", "hi52", "fromLo", "lo52"] });
    if (st.atrPct != null) S.push({ text: `ATR(14) is ${st.atr.toFixed(2)}, ${(st.atrPct * 100).toFixed(1)}% of price.${st.squeeze >= 1 ? ` Bollinger bands have been inside the Keltner channel for ${st.squeeze} bar${st.squeeze === 1 ? "" : "s"} (a volatility squeeze).` : st.bbWidthPct != null && st.bbWidthPct <= 0.1 ? " Bollinger band width is in the lowest tenth of the past year." : ""}`, refs: ["atr", "atrPct", "squeeze"] });
    if (extra.cone) { const c21 = extra.cone.find(x => x.n === 21); if (c21 && c21.current != null) S.push({ text: `21-day realized volatility is ${(c21.current * 100).toFixed(1)}%, at the ${Math.round(c21.pct * 100)}th percentile of its history since ${extra.since}.`, refs: ["vol21"] }); }
    if (extra.rsSpy != null) S.push({ text: `Over 63 trading days it has ${extra.rsSpy >= 0 ? "outperformed" : "underperformed"} SPY by ${pct(Math.abs(extra.rsSpy)).slice(1)}${extra.rsSector != null ? ` and ${extra.rsSector >= 0 ? "outperformed" : "underperformed"} its sector ETF by ${pct(Math.abs(extra.rsSector)).slice(1)}` : ""}.`, refs: ["rsSpy", "rsSector"] });
    if (extra.recent?.length) S.push({ text: `Recent signals: ${extra.recent.map(e => `${SIGNALS[e.type].name.toLowerCase()} on ${e.date}`).join("; ")}. These describe what happened; the scorecard shows how such signals were followed historically, which is not a forecast.`, refs: [] });
    return S;
  }
  // Checks that every number printed in a summary sentence matches a computed value (guards against formatting slips).
  function validateSummary(sentences, values) {
    const nums = Object.values(values).filter(v => typeof v === "number" && isFinite(v));
    const bad = [];
    for (const s of sentences) {
      for (const m of s.text.matchAll(/[+−-]?\d+(?:\.\d+)?%?/g)) {
        const t = m[0]; const isPct = t.endsWith("%"); const x = parseFloat(t.replace("−", "-").replace("%", ""));
        if (!isFinite(x) || /^\d{4}$/.test(t) || [14, 20, 25, 30, 50, 52, 63, 70, 200].includes(Math.abs(x)) && !isPct) continue;
        const ok = nums.some(v => { const cands = isPct ? [v * 100, Math.abs(v * 100), v] : [v, Math.abs(v), v * 100]; return cands.some(cv => Math.abs(cv - Math.abs(x)) <= Math.max(0.051, Math.abs(cv) * 0.001) || Math.abs(cv - x) <= 0.051); });
        if (!ok) bad.push(t);
      }
    }
    return bad;
  }

  return { sma, ema, rma, wma, stdev, rollMax, rollMin, bollinger, trueRange, atr, keltner, donchian, ichimoku, supertrend, rsi, stochastic, macd, adx, obv, mfi, cmf, vwapSession, anchoredVwap, relStrength, swings, levels, trendLines, SIGNALS, detect, gapFills, quantile, median, meanOf, sdOf, wilson, nEff, summarize, backtest, logRets, realizedVol, volCone, drawdown, maxDrawdown, moments, histogram, rollingBetaCorr, seasonality, state, summary, validateSummary };
});
