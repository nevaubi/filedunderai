// Client-side quant helpers for the Technicals & Quant lab (custom benchmarks and small recomputations).
// The same formulas as etl/quant_calc.py: simple daily total returns, rolling Pearson correlation and OLS beta with an
// 80% coverage rule, geometric up/down capture on month-end returns. window.QL in the browser, module.exports in node.
(function (root) {
  "use strict";
  function trIndex(c, d, div) {
    const dv = new Map(); (div || []).forEach(([x, a]) => dv.set(x, (dv.get(x) || 0) + a));
    const out = new Array(c.length); let acc = 1;
    for (let i = 0; i < c.length; i++) { if (i && c[i - 1] > 0 && c[i] > 0) acc *= (c[i] + (dv.get(d[i]) || 0)) / c[i - 1]; out[i] = acc; }
    return out;
  }
  // benchmark simple returns on the asset's dates; null where either day is missing in the benchmark
  function alignedReturns(days, bDays, bTr) {
    const idx = new Map(bDays.map((x, i) => [x, i]));
    const out = new Array(days.length).fill(null);
    for (let i = 1; i < days.length; i++) { const a = idx.get(days[i]), b = idx.get(days[i - 1]); if (a != null && b != null && bTr[b] > 0) out[i] = bTr[a] / bTr[b] - 1; }
    return out;
  }
  function returns(tr) { const o = new Array(tr.length).fill(null); for (let i = 1; i < tr.length; i++) if (tr[i - 1] > 0) o[i] = tr[i] / tr[i - 1] - 1; return o; }
  function rollingCorrBeta(a, b, n, minFrac = 0.8) {
    const L = a.length, rc = new Array(L).fill(null), rb = new Array(L).fill(null), need = Math.ceil(n * minFrac);
    let sx = 0, sy = 0, sxx = 0, syy = 0, sxy = 0, k = 0;
    for (let i = 0; i < L; i++) {
      const x = a[i], y = b[i];
      if (x != null && y != null) { sx += x; sy += y; sxx += x * x; syy += y * y; sxy += x * y; k++; }
      const j = i - n;
      if (j >= 0) { const x0 = a[j], y0 = b[j]; if (x0 != null && y0 != null) { sx -= x0; sy -= y0; sxx -= x0 * x0; syy -= y0 * y0; sxy -= x0 * y0; k--; } }
      if (k >= need && k >= 3) {
        const cxy = sxy - sx * sy / k, cxx = sxx - sx * sx / k, cyy = syy - sy * sy / k;
        if (cyy > 1e-18) { rb[i] = cxy / cyy; if (cxx > 1e-18) rc[i] = Math.max(-1, Math.min(1, cxy / Math.sqrt(cxx * cyy))); }
      }
    }
    return { corr: rc, beta: rb };
  }
  function pearson(a, b) {
    const n = a.length; if (n < 3) return null;
    let ma = 0, mb = 0; for (let i = 0; i < n; i++) { ma += a[i]; mb += b[i]; } ma /= n; mb /= n;
    let c = 0, va = 0, vb = 0; for (let i = 0; i < n; i++) { const x = a[i] - ma, y = b[i] - mb; c += x * y; va += x * x; vb += y * y; }
    return va > 0 && vb > 0 ? c / Math.sqrt(va * vb) : null;
  }
  function olsBeta(a, b) {
    const n = a.length; if (n < 3) return null;
    let ma = 0, mb = 0; for (let i = 0; i < n; i++) { ma += a[i]; mb += b[i]; } ma /= n; mb /= n;
    let c = 0, vb = 0; for (let i = 0; i < n; i++) { c += (a[i] - ma) * (b[i] - mb); vb += (b[i] - mb) ** 2; }
    return vb > 0 ? c / vb : null;
  }
  function pairs(a, b, lo = 0) { const x = [], y = []; for (let i = Math.max(0, lo); i < a.length; i++) if (a[i] != null && b[i] != null) { x.push(a[i]); y.push(b[i]); } return [x, y]; }
  function monthEnd(tr, d) {
    const out = [];
    let prev = null;
    for (let i = 0; i < d.length - 1; i++) if (d[i + 1].slice(0, 7) !== d[i].slice(0, 7)) { if (prev != null) out.push([d[i].slice(0, 7), tr[i] / tr[prev] - 1]); prev = i; }
    return out;
  }
  function capture(rs, rb) {
    const geo = x => { let p = 1; x.forEach(v => { p *= 1 + v; }); return Math.pow(p, 1 / x.length) - 1; };
    const up = [], dn = []; rs.forEach((a, i) => { if (rb[i] > 0) up.push([a, rb[i]]); else if (rb[i] < 0) dn.push([a, rb[i]]); });
    const o = { n_up: up.length, n_down: dn.length, up: null, down: null };
    if (up.length) { const g = geo(up.map(p => p[1])); o.up = g ? geo(up.map(p => p[0])) / g : null; }
    if (dn.length) { const g = geo(dn.map(p => p[1])); o.down = g ? geo(dn.map(p => p[0])) / g : null; }
    o.ratio = o.up != null && o.down ? o.up / o.down : null;
    return o;
  }
  // everything the correlation tab shows for a benchmark, in the same shape as the precomputed quant/t/{SYM}.json corr.bench entry
  function benchStats(H, B, step = 5, years = 10) {
    const rA = returns(H.tr), rB = alignedReturns(H.d, B.d, B.tr), L = H.d.length;
    const idx = []; for (let i = Math.max(0, L - 252 * years); i < L; i += step) idx.push(i); if (idx.at(-1) !== L - 1) idx.push(L - 1);
    const o = { roll: { d: idx.map(i => H.d[i]) } };
    [63, 252].forEach(n => {
      const r = rollingCorrBeta(rA, rB, n);
      o.roll["c" + n] = idx.map(i => r.corr[i]); o.roll["b" + n] = idx.map(i => r.beta[i]);
      o["c" + n] = r.corr[L - 1]; o["b" + n] = r.beta[L - 1];
      const v = r.beta.filter(x => x != null).sort((x, y) => x - y); o[`b${n}_range`] = v.length ? [v[0], v.at(-1)] : null;
    });
    const [x, y] = pairs(rA, rB); o.full = { corr: pearson(x, y), beta: olsBeta(x, y), n: x.length, from: H.d[1] };
    const [x2, y2] = pairs(rA, rB, L - 252);
    const up = x2.map((v, i) => [v, y2[i]]).filter(p => p[1] > 0), dn = x2.map((v, i) => [v, y2[i]]).filter(p => p[1] < 0);
    o.updown = { corr_up: pearson(up.map(p => p[0]), up.map(p => p[1])), corr_down: pearson(dn.map(p => p[0]), dn.map(p => p[1])), beta_up: olsBeta(up.map(p => p[0]), up.map(p => p[1])), beta_down: olsBeta(dn.map(p => p[0]), dn.map(p => p[1])), n_up: up.length, n_down: dn.length };
    o.r2_252 = o.c252 != null ? o.c252 * o.c252 : null;
    const ma = monthEnd(H.tr, H.d), mb = new Map(monthEnd(B.tr, B.d)); const pr = ma.filter(([k]) => mb.has(k)).map(([k, v]) => [v, mb.get(k), k]);
    o.capture = {};
    [["3y", 36], ["5y", 60], ["full", pr.length]].forEach(([nm, k]) => { if (pr.length >= k && pr.length >= 12) { const s = pr.slice(-k); o.capture[nm] = { ...capture(s.map(p => p[0]), s.map(p => p[1])), from: s[0][2], to: s.at(-1)[2] }; } });
    o.custom = true;
    return o;
  }
  const QL = { trIndex, returns, alignedReturns, rollingCorrBeta, pearson, olsBeta, pairs, monthEnd, capture, benchStats };
  if (typeof module !== "undefined" && module.exports) module.exports = QL; else root.QL = QL;
})(typeof window !== "undefined" ? window : globalThis);
