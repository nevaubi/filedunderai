// Compare page. Registers window.FU_PAGES.compare(arg); app.js route() uses it in place of the built-in page when set.
// Side-by-side analysis for 2 to 8 companies: overview matrix ranked within the set, statements, KPIs and segments,
// charts, relative valuation and an AI summary whose numbers are checked against the matrix. Every number opens its source.
window.FU_PAGES = window.FU_PAGES || {};
(() => {
  "use strict";
  const MAXN = 8;
  const iso = t => t.toISOString().slice(0, 10);
  const DAY = 864e5;
  const CS = STATE.cmpx = STATE.cmpx || {
    tab: store.get("cmpTab", "overview"), stmt: "income", kind: "A", view: "values", align: "fiscal", n: "5",
    metric: "Revenue", mkind: "Q", mrange: "20", idx: false, log: false, trRange: "1Y", corrWin: 252, pairs: null,
    sx: "pe_ttm", sy: "rev_growth_lq", vmult: "pe", segK: "Q", kpiK: "Q",
  };
  STATE.cmp = (store.get("cmp", null) || STATE.cmp || []).slice(0, MAXN);

  /* ---------- small utilities ---------- */
  const median = a => { const v = a.filter(x => x != null && isFinite(x)).sort((x, y) => x - y); if (!v.length) return null; const h = v.length >> 1; return v.length % 2 ? v[h] : (v[h - 1] + v[h]) / 2; };
  const quant = (a, q) => { const v = a.filter(x => x != null && isFinite(x)).sort((x, y) => x - y); if (!v.length) return null; const p = (v.length - 1) * q, i = Math.floor(p); return v[i] + (v[Math.min(v.length - 1, i + 1)] - v[i]) * (p - i); };
  const fmtV = (v, unit) => {
    if (v == null || !isFinite(v)) return "–";
    if (unit === "pct") return fpct(v);
    if (unit === "x") return v.toFixed(1) + "×";
    if (unit === "num") return v.toFixed(2);
    if (unit === "USD/shares") return fmtMoney(v, "USD/shares");
    if (unit === "shares") return fmtMoney(v, "shares");
    return fmtMoney(v);
  };
  const ordinal = n => n + (n % 100 >= 11 && n % 100 <= 13 ? "th" : ["th", "st", "nd", "rd"][n % 10] || "th");
  function colors() {
    const base = [1, 2, 3, 4, 5, 6].map(i => css(`--c${i}`));
    return base.concat([d3.interpolateRgb(base[0], base[1])(0.5), d3.interpolateRgb(base[2], base[4])(0.5)]);
  }
  const colorOf = t => colors()[Math.max(0, STATE.cmp.indexOf(t)) % MAXN];
  const short = t => SHORT[t] || t;
  const plain = c => !!c && !c.px && !c.hist && !c.div && !c.uni && !c.custom && (c.calc ? c.calc.inputs.every(plain) : !!c.rec);

  /* ---------- styles (theme variables only, so light and dark both follow app.css) ---------- */
  function injectCss() {
    if (document.getElementById("cmp-css")) return;
    document.head.append(el("style", { id: "cmp-css" }, `
.cmp-pick{display:flex;flex-direction:column;gap:10px;padding:12px 16px}
.cmp-row{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.cmp-chip{display:inline-flex;align-items:center;gap:7px;height:30px;padding:0 6px 0 9px;border:1px solid var(--line2);border-radius:999px;background:var(--surface);font-size:12.5px;font-weight:600}
.cmp-chip i{width:9px;height:9px;border-radius:50%;flex:none}
.cmp-chip a{color:var(--ink)}
.cmp-chip button{border:0;background:none;cursor:pointer;color:var(--faint);font-size:15px;line-height:1;padding:0 3px}
.cmp-chip button:hover{color:var(--down)}
.cmp-search{position:relative;min-width:220px;flex:1;max-width:340px}
.cmp-search input{width:100%;height:32px;border:1px solid var(--line);border-radius:7px;padding:0 10px;background:var(--surface);outline:none}
.cmp-search input:focus{border-color:var(--accent)}
.cmp-menu{position:absolute;left:0;top:36px;z-index:30;min-width:100%;width:max-content;max-width:min(440px,calc(100vw - 32px));background:var(--surface);border:1px solid var(--line);border-radius:10px;box-shadow:var(--shadow);padding:6px;max-height:340px;overflow:auto}
.cmp-menu button{display:flex;width:100%;gap:8px;align-items:center;text-align:left;border:0;background:none;padding:7px 9px;border-radius:6px;cursor:pointer;font-size:12.5px}
.cmp-menu button.sel,.cmp-menu button:hover{background:var(--sunk)}
.cmp-menu .k{margin-left:auto;color:var(--faint);font-size:11.5px;white-space:nowrap;padding-left:10px}
.cmp-menu .hd{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--faint);font-weight:600;padding:6px 9px 4px}
.cmp-menu .x{margin-left:6px;color:var(--faint);border:0;background:none;cursor:pointer;width:auto;padding:0 4px}
.cmp-dd{position:relative}
.cmp-wrap{overflow:auto;max-height:78vh;-webkit-overflow-scrolling:touch}
table.cmpx{border-collapse:separate;border-spacing:0;font-size:12.5px;min-width:100%}
table.cmpx th,table.cmpx td{padding:6px 12px;white-space:nowrap;border-bottom:1px solid var(--line);text-align:right;font-variant-numeric:tabular-nums}
table.cmpx thead th{position:sticky;top:0;background:var(--surface);z-index:2;font-weight:600;font-size:11.5px;color:var(--muted);vertical-align:bottom}
table.cmpx thead th .tk{display:flex;align-items:center;justify-content:flex-end;gap:6px;color:var(--ink);font-size:12.5px}
table.cmpx thead th .nm{font-weight:400;font-size:10.5px;color:var(--faint);max-width:120px;overflow:hidden;text-overflow:ellipsis;margin-left:auto}
table.cmpx thead th .cb{display:block;height:3px;border-radius:2px;margin-top:5px}
table.cmpx .lab{position:sticky;left:0;background:var(--surface);z-index:1;text-align:left;min-width:220px;max-width:260px;border-right:1px solid var(--line);white-space:normal}
table.cmpx thead .lab{z-index:3}
table.cmpx tr.grp td{background:var(--sunk);font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--faint);font-weight:600;text-align:left}
table.cmpx tr.grp td.lab{background:var(--sunk)}
table.cmpx td.c{cursor:pointer;position:relative}
table.cmpx td.c:hover{box-shadow:inset 0 0 0 1.5px var(--accent)}
table.cmpx td.c.sel,table.cmpx td.c.flash{background:var(--mark)!important;color:var(--mark-ink)}
table.cmpx td.ctx{color:var(--muted);background:var(--rail)}
table.cmpx td.ctx.c:hover{color:var(--accent)}
table.cmpx th.ctx{background:var(--rail)}
table.cmpx td.na{color:var(--faint)}
table.cmpx .sub2{display:block;font-size:10px;color:var(--faint);font-family:var(--f-mono)}
table.cmpx tr.co td.lab{padding-left:22px;font-weight:500}
table.cmpx tr.li td{font-weight:600;background:var(--rail)}
table.cmpx tr.li td.lab{background:var(--rail);cursor:pointer}
table.cmpx tr.li td.lab:hover{color:var(--accent)}
table.cmpx .lab .lw{display:flex;align-items:center;gap:7px}
table.cmpx .lab i.dot{width:8px;height:8px;border-radius:50%;flex:none}
table.cmpx td.c.der::after{content:"";position:absolute;right:3px;top:5px;width:4px;height:4px;border-radius:50%;background:var(--c3)}
.cmp-note{font-size:12px;color:var(--muted);padding:8px 16px;border-bottom:1px solid var(--line);display:flex;gap:6px 14px;flex-wrap:wrap;align-items:center}
.cmp-ai{font-size:13.5px;line-height:1.6;max-width:86ch}
.cmp-ai p{margin:0 0 10px}
.cmp-ai .num-link{font:inherit;font-variant-numeric:tabular-nums;font-weight:600;color:var(--ink);background:color-mix(in srgb,var(--mark) 45%,transparent);border:0;border-radius:3px;padding:0 3px;cursor:pointer}
.cmp-ai .num-link:hover{outline:1px solid var(--accent)}
.cmp-ai .dropped{font-size:12px;color:var(--faint);border-top:1px dashed var(--line2);padding-top:8px;margin-top:4px}
.cmp-charts .bd>svg{display:block;width:100%;overflow:visible}
.cmp-legend{display:flex;gap:6px 14px;flex-wrap:wrap;font-size:11.5px;color:var(--muted);margin-top:6px}
.cmp-legend i{display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:5px;vertical-align:-1px}
.cmp-legend label{display:inline-flex;gap:5px;align-items:center;cursor:pointer}
.cmp-tools{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.cmp-tools select{height:30px}
.cmp-tools label.ck{display:inline-flex;gap:5px;align-items:center;font-size:12.5px;color:var(--muted)}
.cmp-mix{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:14px}
.cmp-mix .mx{border:1px solid var(--line);border-radius:10px;padding:10px 12px;min-width:0}
.cmp-mix .mx h4{margin:0 0 4px;font-size:13px;display:flex;gap:6px;align-items:center}
.cmp-band{font-size:12px}
.cmp-band .bl{cursor:pointer}
.cmp-k{display:inline-flex;align-items:center;gap:4px;font-size:10.5px;font-weight:600;border-radius:4px;padding:0 5px;height:17px;margin-left:6px;vertical-align:1px;white-space:nowrap}
.cmp-k.std{background:var(--up-soft);color:var(--up)}
.cmp-k.ns{background:var(--warn-soft);color:var(--warn)}
.cmp-src-t{width:100%;border-collapse:collapse;font-size:12.5px;font-variant-numeric:tabular-nums}
.cmp-src-t th,.cmp-src-t td{padding:5px 8px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}
.cmp-src-t th:first-child,.cmp-src-t td:first-child{text-align:left}
.cmp-src-t tr.hl td{background:color-mix(in srgb,var(--mark) 25%,transparent)}
.cmp-src-t td.hit{background:var(--mark);color:var(--mark-ink);font-weight:700;outline:2px solid var(--mark)}
.cmp-back{align-self:flex-start}
@media (max-width:860px){
  table.cmpx .lab{min-width:132px;max-width:150px}
  table.cmpx th,table.cmpx td{padding:6px 8px}
  table.cmpx thead th .nm{display:none}
  .cmp-search{max-width:none;min-width:0;flex-basis:100%}
  .cmp-pick{padding:12px}
}`));
  }

  /* ---------- extra data: universe fields, five-year history, peers ---------- */
  let UNI = null, PEERS = null;
  const HIST = {};
  async function ensureUniverse() {
    if (UNI) return UNI;
    let rows = INDEX.companies, src = "index.json", gen = INDEX.generated;
    try {
      const s = await getJSON("screener.json");
      let arr = Array.isArray(s) ? s : (s.companies || s.rows || s.data || []);
      const cols = s.columns || s.fields;
      if (Array.isArray(arr) && arr.length) {
        if (Array.isArray(arr[0]) && Array.isArray(cols)) arr = arr.map(r => Object.fromEntries(cols.map((c, i) => [typeof c === "string" ? c : c.key || c.k || c.id, r[i]])));
        const by = Object.fromEntries(arr.filter(r => r && r.ticker).map(r => [r.ticker, r]));
        rows = INDEX.companies.map(c => ({ ...c, ...(by[c.ticker] || {}) }));
        src = "screener.json and index.json"; gen = s.generated || gen;
      }
    } catch { /* screener.json not published yet: index.json fields only */ }
    UNI = { rows, src, gen, by: Object.fromEntries(rows.map(r => [r.ticker, r])) };
    return UNI;
  }
  async function ensureHistory(sym) {
    if (sym in HIST) return HIST[sym];
    try { HIST[sym] = await getJSON(`history/${sym.replace("^", "IDX_").replace("=", "_")}.json`); } catch { HIST[sym] = null; }
    return HIST[sym];
  }
  async function ensurePeers() {
    if (PEERS) return PEERS;
    try { PEERS = await getJSON("meta/peers.json"); } catch { PEERS = { peers: {} }; }
    return PEERS;
  }

  /* ---------- cells: filing values, calculations and market data ---------- */
  function calcCell(tk, label, unit, v, formula, inputs, extra = {}) {
    if (v == null || !isFinite(v)) return null;
    return { tk, label, unit, v, kind: extra.kind || "TTM", end: extra.end, calc: { formula, inputs: inputs.filter(Boolean) }, ...extra };
  }
  function pxCell(sym, i, field = "c", label) {
    const b = PX[sym]?.bars?.[i]; if (!b) return null;
    return { tk: sym, label: label || ({ c: "Close", h: "High", l: "Low", o: "Open" }[field]), unit: "USD/shares", v: b[field], px: { sym, i, field }, end: iso(b.t), kind: "PX" };
  }
  function histCell(sym, i, field = "c") {
    const h = HIST[sym], w = h?.w?.[i]; if (!w) return null;
    return { tk: sym, label: field === "a" ? "Weekly close, adjusted for dividends" : "Weekly close", unit: "USD/shares", v: field === "a" ? w[2] : w[1], hist: { sym, i, field }, end: iso(new Date(w[0] * 1000)), kind: "PX" };
  }
  function divCell(sym, d) { return { tk: sym, label: "Dividend per share", unit: "USD/shares", v: d[1], div: { sym, t: d[0] }, end: iso(new Date(d[0] * 1000)), kind: "PX" }; }
  function coverShares(tk) {
    const c = D.companies[tk], so = c?.sharesOutstanding; if (!so?.val) return null;
    const per = (c.filings || []).filter(f => /^10-[KQ]/.test(f.f));
    const f = per.filter(f => f.d >= so.end).sort((a, b) => a.d.localeCompare(b.d))[0] || per[0];
    const cell = { tk, stmt: "cover", label: "Shares outstanding (cover page)", concept: "EntityCommonStockSharesOutstanding", conceptFull: "dei:EntityCommonStockSharesOutstanding", unit: "shares", kind: "Q", end: so.end, v: so.val, sib: () => [] };
    if (f) cell.rec = { v: so.val, accn: f.a, form: f.f, filed: f.d, end: so.end };
    else cell.custom = "Reported on the cover page of the latest periodic report.";
    return cell;
  }
  // shares for per-share math: a reported quarter, else the fiscal-year figure at the same end (derived quarters are not share counts)
  function sharesAt(m, end, kind = "Q") {
    const q = m.get("Diluted shares", kind, end);
    if (q && !q.derived && q.v > 0) return q;
    const a = m.get("Diluted shares", "A", end);
    return a && a.v > 0 ? a : null;
  }
  function ttmAt(m, tk, label, end, filedBy) {
    let s = m.series(label, "Q").filter(x => x.end <= end || Math.abs(dayDiff(x.end, end)) <= 12);
    if (filedBy) s = s.filter(x => (x.cell.rec?.filed || "") <= filedBy);
    const q = s.slice(-4);
    if (q.length < 4 || Math.abs(dayDiff(q[3].end, end)) > 12 || dayDiff(q[0].end, q[3].end) > 300) return null;
    const unit = q[0].cell.unit; if (unit === "pct" || unit === "x") return null;
    return { v: q.reduce((a, x) => a + x.cell.v, 0), tk, label, unit, kind: "TTM", end: q[3].end, calc: { formula: `Sum of last four quarters of ${label}`, inputs: q.map(x => x.cell) } };
  }
  function bal(m, label, end) { return m.get(label, "Q", end) || m.get(label, "A", end); }
  function latestEnd(m, label) { const r = m.byLabel[label]; if (!r) return null; let e = null; for (const k of [...r.Q.keys(), ...r.A.keys()]) if (!e || k > e) e = k; return e; }

  /* ---------- price statistics with their inputs ---------- */
  function idxOnOrAfter(sym, t0) { const b = PX[sym]?.bars || []; const i = b.findIndex(x => x.t >= t0); return i; }
  function priceCells(tk) {
    const b = PX[tk]?.bars; if (!b || b.length < 30) return {};
    const n = b.length - 1, end = b[n].t, out = {};
    const ret = (days, label, key) => { const i = idxOnOrAfter(tk, new Date(end - days * DAY)); if (i < 0 || i >= n) return; out[key] = calcCell(tk, label, "pct", b[n].c / b[i].c - 1, `Close ${iso(b[n].t)} / close ${iso(b[i].t)} − 1`, [pxCell(tk, n), pxCell(tk, i)], { kind: "PX", end: iso(b[n].t), custom: 1 }); };
    ret(30, "1M price return", "r1m");
    ret(365, "1Y price return", "r1y");
    const y = end.getUTCFullYear(); const j = b.map(x => x.t.getUTCFullYear() < y).lastIndexOf(true);
    if (j >= 0) out.rytd = calcCell(tk, "YTD price return", "pct", b[n].c / b[j].c - 1, `Close ${iso(b[n].t)} / last close of ${y - 1} (${iso(b[j].t)}) − 1`, [pxCell(tk, n), pxCell(tk, j)], { kind: "PX", end: iso(b[n].t), custom: 1 });
    const r = rets(b.map(x => x.c));
    if (r.length >= 60) out.vol60 = calcCell(tk, "Volatility, 60 sessions", "pct", sd(r.slice(-60)) * Math.sqrt(252), `Standard deviation of the last 60 daily returns (${iso(b[n - 60].t)} to ${iso(b[n].t)}) × √252`, [pxCell(tk, n - 60), pxCell(tk, n)], { kind: "PX", end: iso(b[n].t), custom: 1 });
    if (tk !== "SPY" && PX.SPY) {
      const [ra, rb] = alignedRets(tk, "SPY", 252);
      if (ra.length > 60) { const s0 = idxOnOrAfter("SPY", b[Math.max(0, n - 252)].t); out.beta = calcCell(tk, "Beta vs S&P 500, 1Y", "num", beta(ra, rb), `Covariance of daily returns with SPY / variance of SPY returns, last ${ra.length} sessions`, [pxCell(tk, Math.max(0, n - 252)), pxCell(tk, n), pxCell("SPY", s0), pxCell("SPY", PX.SPY.bars.length - 1)], { kind: "PX", end: iso(b[n].t), custom: 1 }); }
    }
    const i1 = Math.max(0, idxOnOrAfter(tk, new Date(end - 365 * DAY)));
    let pk = i1, best = 0, bp = i1, bt = i1;
    for (let i = i1; i <= n; i++) { if (b[i].c > b[pk].c) pk = i; const dd = b[i].c / b[pk].c - 1; if (dd < best) { best = dd; bp = pk; bt = i; } }
    out.mdd = best < 0 ? calcCell(tk, "Max drawdown, 1Y", "pct", best, `Lowest close / highest prior close − 1 within the last year (peak ${iso(b[bp].t)}, trough ${iso(b[bt].t)})`, [pxCell(tk, bp), pxCell(tk, bt)], { kind: "PX", end: iso(b[n].t), custom: 1 })
      : calcCell(tk, "Max drawdown, 1Y", "pct", 0, "No close below a prior high within the last year", [pxCell(tk, n)], { kind: "PX", end: iso(b[n].t), custom: 1 });
    let hi = i1; for (let i = i1; i <= n; i++) if (b[i].h > b[hi].h) hi = i;
    out.fromhi = calcCell(tk, "From 52-week high", "pct", b[n].c / b[hi].h - 1, `Close ${iso(b[n].t)} / highest intraday high in the last year (${iso(b[hi].t)}) − 1`, [pxCell(tk, n), pxCell(tk, hi, "h")], { kind: "PX", end: iso(b[n].t), custom: 1 });
    const tr = totalReturn(tk, i1, n);
    if (tr) out.tr1y = tr.cell;
    return out;
  }
  // total return over daily bars, dividends reinvested at the ex-date close
  function divByBar(sym, i0, i1) {
    const h = HIST[sym], b = PX[sym]?.bars; const out = new Map(); if (!h || !b) return out;
    for (const d of h.div || []) {
      const t = new Date(d[0] * 1000); if (t < new Date(b[i0].t - 3 * DAY) || t > b[i1].t) continue;
      const day = iso(t); let i = b.findIndex(x => iso(x.t) >= day); if (i <= i0 || i > i1) continue;
      out.set(i, (out.get(i) || []).concat([d]));
    }
    return out;
  }
  function totalReturn(sym, i0, i1) {
    const b = PX[sym]?.bars; if (!b || !HIST[sym]) return null;
    const divs = divByBar(sym, i0, i1); let v = 1; const path = [[b[i0].t, 1]];
    for (let i = i0 + 1; i <= i1; i++) { const dv = (divs.get(i) || []).reduce((a, d) => a + d[1], 0); v *= (b[i].c + dv) / b[i - 1].c; path.push([b[i].t, v]); }
    const inputs = [pxCell(sym, i1), pxCell(sym, i0), ...[...divs.values()].flat().map(d => divCell(sym, d))];
    return { path, cell: calcCell(sym, "1Y total return", "pct", v - 1, `Daily close-to-close returns ${iso(b[i0].t)} to ${iso(b[i1].t)}, each dividend added back on its ex-date (${divs.size} payment${divs.size === 1 ? "" : "s"})`, inputs, { kind: "PX", end: iso(b[i1].t), custom: 1 }) };
  }

  /* ---------- the metric matrix ---------- */
  const CX = {};
  function ctx(tk) {
    const key = tk + "|" + (D.companies[tk]?.generated || "") + "|" + (last(tk)?.t?.getTime() || "") + "|" + (HIST[tk] ? 1 : 0);
    if (CX[tk]?.key === key) return CX[tk];
    const m = model(tk), lq = m.latestQ(), la = m.latestA();
    const n = (PX[tk]?.bars?.length || 0) - 1;
    const p = n >= 0 ? pxCell(tk, n) : null, cover = coverShares(tk), sh = cover || (lq ? sharesAt(m, lq) : null);
    const x = { key, tk, m, lq, la, p, sh, cells: {} };
    const T = l => lq ? ttmAt(m, tk, l, lq) : null;
    const rev = T("Revenue"), ni = T("Net income"), eps = T("EPS diluted"), oi = T("Operating income"), ocf = T("Operating cash flow"), capex = T("Capex");
    const rd = T("Research & development"), sbc = T("Stock-based comp"), dv = T("Dividends paid"), bb = T("Buybacks"), tax = T("Income tax"), pre = T("Pretax income");
    const end = lq, pxEnd = p?.end;
    const C = (k, c) => { if (c) x.cells[k] = c; return c; };
    const mcap = p && sh ? calcCell(tk, "Market cap", "USD", p.v * sh.v, cover ? "Close × shares outstanding (cover page)" : "Close × diluted weighted shares, latest quarter (no single cover-page share count is tagged)", [p, sh], { kind: "PX", end: pxEnd, custom: 1 }) : null;
    C("mcap", mcap);
    const fcf = ocf && capex ? calcCell(tk, "Free cash flow", "USD", ocf.v - capex.v, "TTM operating cash flow − TTM capex", [ocf, capex], { end }) : null;
    const cash = lq ? m.ratio("Cash & securities", "Q", lq) : null;
    const ltd = lq ? bal(m, "Long-term debt", lq) : null, eq = lq ? bal(m, "Shareholders' equity", lq) : null, ta = lq ? bal(m, "Total assets", lq) : null;
    if (p && eps && eps.v > 0) C("pe", calcCell(tk, "P/E TTM", "x", p.v / eps.v, "Close / TTM diluted EPS", [p, eps], { kind: "PX", end: pxEnd, custom: 1 }));
    if (mcap && rev && rev.v > 0) C("ps", calcCell(tk, "P/S TTM", "x", mcap.v / rev.v, "Market cap / TTM revenue", [mcap, rev], { kind: "PX", end: pxEnd, custom: 1 }));
    if (mcap && oi && oi.v > 0 && cash) { const ev = mcap.v + (ltd?.v || 0) - cash.v; C("evebit", calcCell(tk, "EV / EBIT TTM", "x", ev / oi.v, "(Market cap + long-term debt − cash & current marketable securities) / TTM operating income", [mcap, ltd, cash, oi], { kind: "PX", end: pxEnd, custom: 1 })); }
    if (mcap && eq && eq.v > 0) C("pb", calcCell(tk, "Price / book", "x", mcap.v / eq.v, "Market cap / shareholders' equity (latest quarter)", [mcap, eq], { kind: "PX", end: pxEnd, custom: 1 }));
    if (mcap && fcf) C("fcfy", calcCell(tk, "FCF yield", "pct", fcf.v / mcap.v, "TTM free cash flow / market cap", [fcf, mcap], { kind: "PX", end: pxEnd, custom: 1 }));
    // growth
    if (lq) C("revg", m.ratio("Revenue growth YoY", "Q", lq));
    const pe1 = lq && m.priorEnd("Q", lq);
    const revP = pe1 && ttmAt(m, tk, "Revenue", pe1);
    if (rev && revP && revP.v > 0) C("revgt", calcCell(tk, "Revenue growth, TTM YoY", "pct", rev.v / revP.v - 1, "TTM revenue / TTM revenue a year earlier − 1", [rev, revP], { end }));
    if (la) { const a = m.get("Revenue", "A", la); const e3 = m.periods("A").find(e => Math.abs(dayDiff(e, la) - 3 * 365.25) < 25); const b3 = e3 && m.get("Revenue", "A", e3); if (a && b3 && a.v > 0 && b3.v > 0) C("cagr3", calcCell(tk, "Revenue CAGR, 3 years", "pct", Math.pow(a.v / b3.v, 1 / 3) - 1, `(Revenue ${plabel(tk, la, "A")} / Revenue ${plabel(tk, e3, "A")})^(1/3) − 1`, [a, b3], { kind: "A", end: la })); }
    if (lq) C("epsg", m.ratio("EPS growth YoY", "Q", lq));
    const oiP = pe1 && ttmAt(m, tk, "Operating income", pe1);
    if (oi && oiP && oiP.v > 0) C("oig", calcCell(tk, "Operating income growth, TTM YoY", "pct", oi.v / oiP.v - 1, "TTM operating income / TTM operating income a year earlier − 1", [oi, oiP], { end }));
    // profitability
    if (lq) { C("gm", m.ratio("Gross margin", "Q", lq)); C("om", m.ratio("Operating margin", "Q", lq)); }
    if (ni && rev && rev.v > 0) C("nm", calcCell(tk, "Net margin TTM", "pct", ni.v / rev.v, "TTM net income / TTM revenue", [ni, rev], { end }));
    if (fcf && rev && rev.v > 0) C("fcfm", calcCell(tk, "FCF margin TTM", "pct", fcf.v / rev.v, "TTM free cash flow / TTM revenue", [fcf, rev], { end }));
    if (rd && rev && rev.v > 0) C("rd", calcCell(tk, "R&D % of revenue TTM", "pct", rd.v / rev.v, "TTM R&D / TTM revenue", [rd, rev], { end }));
    // balance sheet
    C("cash", cash); C("ltd", ltd);
    if (cash) C("netcash", calcCell(tk, "Net cash", "USD", cash.v - (ltd?.v || 0), "Cash & current marketable securities − long-term debt", [cash, ltd], { kind: "Q", end: lq }));
    if (lq) { C("cur", m.ratio("Current ratio", "Q", lq)); C("de", m.ratio("Debt to equity", "Q", lq)); }
    const tl = lq && bal(m, "Total liabilities", lq);
    if (tl && ta && ta.v > 0) C("lta", calcCell(tk, "Liabilities / assets", "pct", tl.v / ta.v, "Total liabilities / total assets", [tl, ta], { kind: "Q", end: lq }));
    // returns on capital
    const eqP = pe1 && bal(m, "Shareholders' equity", pe1), taP = pe1 && bal(m, "Total assets", pe1);
    if (ni && eq && eq.v > 0) { const avg = eqP && eqP.v > 0 ? (eq.v + eqP.v) / 2 : eq.v; C("roe", calcCell(tk, "Return on equity TTM", "pct", ni.v / avg, eqP && eqP.v > 0 ? "TTM net income / average of shareholders' equity now and a year earlier" : "TTM net income / shareholders' equity (latest quarter)", [ni, eq, eqP && eqP.v > 0 ? eqP : null], { end })); }
    if (ni && ta && ta.v > 0) { const avg = taP ? (ta.v + taP.v) / 2 : ta.v; C("roa", calcCell(tk, "Return on assets TTM", "pct", ni.v / avg, taP ? "TTM net income / average of total assets now and a year earlier" : "TTM net income / total assets (latest quarter)", [ni, ta, taP], { end })); }
    if (oi && eq && cash) {
      const ic = eq.v + (ltd?.v || 0) - cash.v;
      const tr = tax && pre && pre.v > 0 ? Math.min(0.5, Math.max(0, tax.v / pre.v)) : 0.21;
      if (ic > 0) C("roic", calcCell(tk, "ROIC TTM (approx.)", "pct", oi.v * (1 - tr) / ic, `TTM operating income × (1 − ${tax && pre && pre.v > 0 ? "TTM tax / TTM pretax income, capped 0 to 50%" : "21% statutory rate"}) / (equity + long-term debt − cash & current securities)`, [oi, tax && pre && pre.v > 0 ? tax : null, tax && pre && pre.v > 0 ? pre : null, eq, ltd, cash], { end }));
    }
    // shareholder returns
    if (mcap && dv) C("divy", calcCell(tk, "Dividend yield (cash paid TTM)", "pct", dv.v / mcap.v, "TTM dividends paid / market cap", [dv, mcap], { kind: "PX", end: pxEnd, custom: 1 }));
    if (mcap && bb) C("bby", calcCell(tk, "Buyback yield TTM", "pct", bb.v / mcap.v, "TTM buybacks / market cap", [bb, mcap], { kind: "PX", end: pxEnd, custom: 1 }));
    if (mcap && (dv || bb)) C("shy", calcCell(tk, "Shareholder yield TTM", "pct", ((dv?.v || 0) + (bb?.v || 0)) / mcap.v, "(TTM dividends paid + TTM buybacks) / market cap", [dv, bb, mcap], { kind: "PX", end: pxEnd, custom: 1 }));
    if (fcf && fcf.v > 0 && (dv || bb)) C("payout", calcCell(tk, "Cash returned / FCF TTM", "pct", ((dv?.v || 0) + (bb?.v || 0)) / fcf.v, "(TTM dividends paid + TTM buybacks) / TTM free cash flow", [dv, bb, fcf], { end }));
    if (lq && pe1) { const s1 = sharesAt(m, lq), s0 = sharesAt(m, pe1); if (s1 && s0) C("shchg", calcCell(tk, "Diluted shares, YoY change", "pct", s1.v / s0.v - 1, `Diluted shares ${plabel(tk, s1.end, s1.kind)} / diluted shares ${plabel(tk, s0.end, s0.kind)} − 1`, [s1, s0], { kind: "Q", end: lq })); }
    if (sbc && rev && rev.v > 0) C("sbc", calcCell(tk, "Stock comp % of revenue TTM", "pct", sbc.v / rev.v, "TTM stock-based compensation / TTM revenue", [sbc, rev], { end }));
    Object.entries(priceCells(tk)).forEach(([k, c]) => C(k, c));
    x.ttm = { rev, ni, eps, oi, ocf, capex, fcf };
    CX[tk] = x;
    return x;
  }
  // better: 1 higher is better, -1 lower is better, 0 no ranking. spx: universe fields with the same definition.
  const ROWS = [
    ["Valuation", [
      ["mcap", "Market cap", "USD", 0, ["market_cap"]],
      ["pe", "P/E TTM", "x", -1, ["pe_ttm"]],
      ["ps", "P/S TTM", "x", -1, ["ps_ttm"]],
      ["evebit", "EV / EBIT TTM", "x", -1, ["ev_ebit_ttm", "ev_ebit"]],
      ["pb", "Price / book", "x", -1, ["pb", "price_to_book", "pb_lq"]],
      ["fcfy", "FCF yield TTM", "pct", 1, ["fcf_yield"]]]],
    ["Growth", [
      ["revg", "Revenue growth YoY, latest quarter", "pct", 1, ["rev_growth_lq"]],
      ["revgt", "Revenue growth, TTM YoY", "pct", 1, ["rev_growth_ttm"]],
      ["cagr3", "Revenue CAGR, 3 fiscal years", "pct", 1, ["rev_cagr_3y", "revenue_cagr_3y"]],
      ["epsg", "EPS growth YoY, latest quarter", "pct", 1, ["eps_growth_lq"]],
      ["oig", "Operating income growth, TTM YoY", "pct", 1, ["op_income_growth_ttm"]]]],
    ["Profitability", [
      ["gm", "Gross margin, latest quarter", "pct", 1, ["gross_margin_lq"]],
      ["om", "Operating margin, latest quarter", "pct", 1, ["op_margin_lq"]],
      ["nm", "Net margin TTM", "pct", 1, ["net_margin_ttm"]],
      ["fcfm", "FCF margin TTM", "pct", 1, ["fcf_margin_ttm"]],
      ["rd", "R&D % of revenue TTM", "pct", 0, ["rd_pct_ttm"]]]],
    ["Balance sheet", [
      ["cash", "Cash & current securities", "USD", 0, []],
      ["ltd", "Long-term debt", "USD", 0, []],
      ["netcash", "Net cash", "USD", 1, []],
      ["cur", "Current ratio", "x", 1, ["current_ratio"]],
      ["de", "Long-term debt / equity", "x", -1, ["debt_to_equity"]],
      ["lta", "Liabilities / assets", "pct", -1, ["liabilities_to_assets"]]]],
    ["Returns on capital", [
      ["roe", "Return on equity TTM", "pct", 1, ["roe_ttm", "roe"]],
      ["roa", "Return on assets TTM", "pct", 1, ["roa_ttm", "roa"]],
      ["roic", "ROIC TTM (approx.)", "pct", 1, ["roic_ttm", "roic"]]]],
    ["Shareholder returns", [
      ["divy", "Dividend yield (cash paid TTM)", "pct", 1, ["dividend_yield"]],
      ["bby", "Buyback yield TTM", "pct", 1, ["buyback_yield"]],
      ["shy", "Shareholder yield TTM", "pct", 1, []],
      ["payout", "Cash returned / FCF TTM", "pct", 0, []],
      ["shchg", "Diluted shares, YoY change", "pct", -1, []],
      ["sbc", "Stock comp % of revenue TTM", "pct", -1, ["sbc_pct_ttm"]]]],
    ["Price and risk", [
      ["r1m", "1M price return", "pct", 1, ["ret_1m"]],
      ["rytd", "YTD price return", "pct", 1, ["ret_ytd"]],
      ["r1y", "1Y price return", "pct", 1, ["ret_1y"]],
      ["tr1y", "1Y total return", "pct", 1, ["tr_1y"]],
      ["vol60", "Volatility, 60 sessions", "pct", -1, ["vol_60d"]],
      ["beta", "Beta vs S&P 500, 1Y", "num", 0, ["beta_1y"]],
      ["mdd", "Max drawdown, 1Y", "pct", 1, ["max_dd_1y"]],
      ["fromhi", "From 52-week high", "pct", 1, []]]],
  ].map(([g, rows]) => [g, rows.map(([key, label, unit, better, spx]) => ({ key, label, unit, better, spx }))]);
  const ROW = Object.fromEntries(ROWS.flatMap(([, r]) => r.map(x => [x.key, x])));

  function spxStat(row) {
    if (!UNI) return null;
    const f = row.spx.find(k => UNI.rows.some(r => r[k] != null && isFinite(r[k])));
    if (!f) return null;
    const pairs = UNI.rows.filter(r => r[f] != null && isFinite(r[f])).map(r => [r.ticker, +r[f]]);
    if (pairs.length < 50) return null;
    return { field: f, pairs, v: median(pairs.map(p => p[1])) };
  }
  function matrixData() {
    const tks = STATE.cmp;
    const cx = Object.fromEntries(tks.map(t => [t, ctx(t)]));
    return ROWS.map(([g, rows]) => [g, rows.map(row => {
      const cells = Object.fromEntries(tks.map(t => [t, cx[t].cells[row.key] || null]));
      const vals = tks.map(t => cells[t]?.v).filter(v => v != null && isFinite(v));
      const set = vals.length >= 2 ? calcCell("SET", `${row.label}, set median`, row.unit, median(vals), `Median of ${vals.length} companies in this comparison`, tks.map(t => cells[t]), { kind: "PX", end: null, custom: 1, set: true }) : null;
      return { row, cells, set, spx: spxStat(row) };
    })]);
  }
  function shade(row, v, vals) {
    if (!row.better || v == null || vals.length < 2) return "";
    const sorted = [...vals].sort((a, b) => a - b);
    const lo = sorted.indexOf(v), hi = sorted.lastIndexOf(v);
    let t = ((lo + hi) / 2) / (sorted.length - 1);
    if (row.better < 0) t = 1 - t;
    const d = t - 0.5, a = Math.round(Math.abs(d) * 2 * 30);
    if (!a) return "";
    return `background:color-mix(in srgb, var(${d > 0 ? "--up" : "--down"}) ${a}%, transparent)`;
  }

  /* ---------- source viewers for market data and calculations ---------- */
  const BACK = [];
  let CUR = null;
  function openAny(cell, fromStack) {
    if (!cell) return;
    if (!fromStack && CUR && drawer.classList.contains("open")) BACK.push(CUR); else if (!fromStack) BACK.length = 0;
    CUR = cell;
    if (cell.kpiOpen) { cell.kpiOpen(); return; }
    if (plain(cell)) { BACK.length = 0; CUR = null; openSource(cell); return; }
    if (cell.px || cell.hist || cell.div) return openMarket(cell);
    if (cell.uni) return openUniverse(cell);
    return openDerivation(cell);
  }
  function drawerShell(eyebrow, title, value, sub, citeText, extraBtns = []) {
    hideTip();
    const dh = $("#dh"), db = $("#db"); dh.innerHTML = ""; db.innerHTML = "";
    const back = BACK.length ? el("button", { class: "btn cmp-back", onclick: () => { const c = BACK.pop(); openAny(c, true); } }, svgUse("i-left"), "Back") : null;
    dh.append(
      el("div", { class: "row" }, el("span", { class: "eyebrow" }, eyebrow),
        el("div", { class: "x", style: "display:flex;gap:6px" },
          el("button", { class: "btn", title: "Copy citation (c)", onclick: () => copyText(citeText, "Citation copied") }, svgUse("i-copy"), "Cite"), ...extraBtns,
          el("button", { class: "iconbtn", onclick: closeSource, "aria-label": "Close source (Esc)" }, svgUse("i-x")))),
      el("div", { class: "row" }, el("h2", {}, title)),
      el("div", { class: "row" }, value != null ? el("span", { class: "chip acc mono", style: "font-size:13.5px;height:26px" }, value) : null, sub ? el("span", { class: "muted", style: "font-size:12.5px" }, sub) : null));
    const body = el("div", { style: "display:flex;flex-direction:column;gap:16px" }, back);
    db.append(body);
    DR.cell = { cmp: true, cite: citeText, sib: () => [] };
    drawer.classList.add("open"); drawer.setAttribute("aria-hidden", "false");
    if (innerWidth < 860) scrim.hidden = false;
    return body;
  }
  // "c" copies the citation for these panels too; app.js handles the other keys
  document.addEventListener("keydown", e => {
    if (e.key !== "c" || !DR.cell?.cmp || !drawer.classList.contains("open")) return;
    if (/INPUT|TEXTAREA|SELECT/.test(document.activeElement?.tagName) || e.metaKey || e.ctrlKey || e.altKey) return;
    e.stopImmediatePropagation(); copyText(DR.cell.cite, "Citation copied");
  }, true);
  const nameOf = tk => tk === "SET" ? "This comparison" : tk === "SPX" ? "S&P 500" : `${short(tk)} (${tk})`;
  function inputLine(inp) {
    const per = inp.px || inp.hist || inp.div ? fdate(inp.end) : inp.kind === "PX" ? (inp.end ? "as of " + fdate(inp.end) : "") : inp.set ? "" : inp.kind === "TTM" ? "TTM to " + fdate(inp.end) : inp.end ? (() => { try { return plabel(inp.tk, inp.end, inp.kind); } catch { return fdate(inp.end); } })() : "";
    const src = inp.px ? "Daily bar" : inp.hist ? "Weekly bar" : inp.div ? "Dividend event" : inp.calc ? "Calculated" : inp.rec ? `${inp.rec.form}${inp.derived ? " · derived" : ""}` : inp.kpi ? "Investor materials" : "";
    return el("div", { class: "in", onclick: () => openAny(inp), title: "Open the source of this input" },
      el("span", {}, inp.tk && inp.tk !== CUR?.tk && !inp.set ? el("b", { class: "mono", style: "margin-right:6px" }, inp.tk) : null, el("b", {}, inp.label), " ", el("span", { class: "muted" }, per), " ", el("span", { class: "faint", style: "font-size:11px" }, src)),
      el("span", { class: "mono" }, fmtV(inp.v, inp.unit)));
  }
  function openDerivation(cell) {
    const per = cell.set ? "" : cell.kind === "TTM" ? "TTM to " + fdate(cell.end) : cell.kind === "PX" ? (cell.end ? "as of " + fdate(cell.end) : "") : cell.end ? plabel(cell.tk, cell.end, cell.kind) : "";
    const cite = `${nameOf(cell.tk)} ${cell.label}${per ? ", " + per : ""}: ${fmtV(cell.v, cell.unit)}. Calculated as ${cell.calc.formula} from ` + cell.calc.inputs.map(i => `${i.tk && !i.set ? i.tk + " " : ""}${i.label} ${fmtV(i.v, i.unit)}${i.rec ? ` (${i.rec.form} ${i.rec.accn})` : i.px ? ` (close ${i.end}, Yahoo Finance)` : ""}`).join("; ") + " (Filed Under).";
    const body = drawerShell("Calculated value", `${nameOf(cell.tk)} · ${cell.label}`, fmtV(cell.v, cell.unit), per, cite);
    const box = el("div", { class: "calc-box" }, el("div", { class: "eyebrow" }, "Formula"), el("div", { class: "eq" }, cell.calc.formula));
    cell.calc.inputs.forEach(inp => box.append(inputLine(inp)));
    box.append(el("div", { class: "in", style: "cursor:default;background:none;border-top:1px solid var(--line);border-radius:0" }, el("b", {}, "Result"), el("span", { class: "mono" }, fmtV(cell.v, cell.unit))));
    body.append(box, el("div", { class: "note" }, cell.set ? "Each company's value opens its own calculation and the filings behind it." : "Each input opens its source: filed values open the filing at the reported figure, market data opens the bars it came from, and calculated inputs open their own formula."));
    if (cell.custom && typeof cell.custom === "string") body.append(el("div", { class: "note" }, cell.custom));
  }
  function openMarket(cell) {
    const sym = cell.tk, weekly = !!cell.hist;
    const h = HIST[sym];
    const yahoo = `https://finance.yahoo.com/quote/${encodeURIComponent(sym.replace(".", "-"))}/history/`;
    const srcUrl = weekly || cell.div ? h?.source_url : `https://query2.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(sym.replace(".", "-"))}?range=2y&interval=1d`;
    const what = cell.div ? "dividend" : weekly ? "weekly bar" : "daily bar";
    const cite = `${sym} ${cell.label} ${fdate(cell.end)}: ${fmtV(cell.v, cell.unit)}. Source: Yahoo Finance chart API (${what}), ${weekly || cell.div ? `history/${sym}.json retrieved ${h?.retrieved_at || ""}` : `prices/${sym}.json`} (Filed Under).`;
    const body = drawerShell("Market data", `${nameOf(sym)} · ${cell.label}`, fmtV(cell.v, cell.unit), fdate(cell.end), cite, [el("a", { class: "btn", href: yahoo, target: "_blank", rel: "noopener" }, "Yahoo", svgUse("i-ext"))]);
    body.append(el("div", { style: "display:flex;gap:8px;align-items:center;flex-wrap:wrap" }, el("span", { class: "badge-src" }, "Source data"), el("span", { class: "eyebrow" }, weekly || cell.div ? "Five-year weekly history with dividends and splits" : "Daily bars, two years"), srcUrl ? el("a", { href: srcUrl, target: "_blank", rel: "noopener", style: "margin-left:auto;font-size:12px" }, "Source API ↗") : null));
    const t = el("table", { class: "cmp-src-t" });
    let hit = null;
    if (cell.px) {
      const b = PX[sym].bars, i = cell.px.i, f = cell.px.field;
      t.append(el("thead", {}, el("tr", {}, el("th", {}, "Date"), el("th", {}, "Open"), el("th", {}, "High"), el("th", {}, "Low"), el("th", {}, "Close"), el("th", {}, "Volume"))));
      const tb = el("tbody");
      for (let j = Math.max(0, i - 12); j <= Math.min(b.length - 1, i + 12); j++) {
        const x = b[j], tr = el("tr", { class: j === i ? "hl" : "" }, el("td", { class: "mono" }, iso(x.t)));
        ["o", "h", "l", "c"].forEach(k => { const td = el("td", {}, fnum(x[k])); if (j === i && k === f) { td.className = "hit"; hit = td; } tr.append(td); });
        tr.append(el("td", { class: "faint" }, x.v ? d3.format(".3s")(x.v) : ""));
        tb.append(tr);
      }
      t.append(tb);
    } else if (cell.hist) {
      const w = h.w, i = cell.hist.i;
      t.append(el("thead", {}, el("tr", {}, el("th", {}, "Week of"), el("th", {}, "Close"), el("th", {}, "Adjusted close"))));
      const tb = el("tbody");
      for (let j = Math.max(0, i - 10); j <= Math.min(w.length - 1, i + 10); j++) {
        const tr = el("tr", { class: j === i ? "hl" : "" }, el("td", { class: "mono" }, iso(new Date(w[j][0] * 1000))));
        [1, 2].forEach(k => { const td = el("td", {}, fnum(w[j][k])); if (j === i && (k === 1) === (cell.hist.field !== "a")) { td.className = "hit"; hit = td; } tr.append(td); });
        tb.append(tr);
      }
      t.append(tb);
    } else if (cell.div) {
      t.append(el("thead", {}, el("tr", {}, el("th", {}, "Ex-date"), el("th", {}, "Dividend per share"))));
      const tb = el("tbody");
      (h?.div || []).slice().reverse().forEach(d => { const on = d[0] === cell.div.t; const td = el("td", { class: on ? "hit" : "" }, "$" + d[1].toFixed(4)); if (on) hit = td; tb.append(el("tr", { class: on ? "hl" : "" }, el("td", { class: "mono" }, iso(new Date(d[0] * 1000))), td)); });
      t.append(tb);
    }
    body.append(el("div", { class: "scroll", style: "max-height:56vh;overflow:auto;border:1px solid var(--line);border-radius:8px" }, t));
    const splits = (h?.split || []).map(s => `${iso(new Date(s[0] * 1000))} ${s[1]}-for-${s[2]}`);
    body.append(el("div", { class: "note" }, weekly || cell.div
      ? `Weekly closes and dividend events from the Yahoo Finance chart API, retrieved ${h?.retrieved_at ? new Date(h.retrieved_at).toLocaleString("en-US") : "–"} and stored as history/${sym}.json. Closes are split-adjusted; dividends are per current share.${splits.length ? " Splits: " + splits.join(", ") + "." : ""}`
      : `Daily bars from the Yahoo Finance chart API, stored as prices/${sym}.json and refreshed after each close. Prices are split-adjusted.`));
    if (hit) requestAnimationFrame(() => hit.scrollIntoView({ block: "center", inline: "nearest" }));
  }
  function openUniverse(cell) {
    const { row, stat } = cell.uni;
    const vals = stat.pairs.map(p => p[1]);
    const cite = `S&P 500 median ${row.label}: ${fmtV(stat.v, row.unit)} across ${vals.length} companies. Source: Filed Under ${UNI.src} field ${stat.field}, generated ${UNI.gen}.`;
    const body = drawerShell("S&P 500 context", `S&P 500 · ${row.label}`, fmtV(stat.v, row.unit), `median of ${vals.length} companies`, cite);
    const q = [["Minimum", 0], ["10th percentile", .1], ["25th percentile", .25], ["Median", .5], ["75th percentile", .75], ["90th percentile", .9], ["Maximum", 1]];
    const t = el("table", { class: "cmp-src-t" }, el("thead", {}, el("tr", {}, el("th", {}, "Statistic"), el("th", {}, "Value"))));
    const tb = el("tbody"); q.forEach(([n, p]) => { const td = el("td", { class: p === .5 ? "hit" : "" }, fmtV(quant(vals, p), row.unit)); tb.append(el("tr", { class: p === .5 ? "hl" : "" }, el("td", {}, n), td)); }); t.append(tb);
    body.append(t);
    const sorted = [...stat.pairs].sort((a, b) => a[1] - b[1]);
    const pct = v => { const i = sorted.findIndex(p => p[1] >= v); return i < 0 ? 100 : Math.round(i / (sorted.length - 1) * 100); };
    const mine = STATE.cmp.map(tk => [tk, UNI.by[tk]?.[stat.field]]).filter(p => p[1] != null);
    if (mine.length) body.append(el("div", { class: "calc-box" }, el("div", { class: "eyebrow" }, "This comparison in the S&P 500 distribution"), ...mine.map(([tk, v]) => el("div", { class: "in", style: "cursor:default" }, el("span", {}, el("b", { class: "mono" }, tk), " ", el("span", { class: "muted" }, `${ordinal(pct(v))} percentile`)), el("span", { class: "mono" }, fmtV(v, row.unit))))));
    body.append(el("div", { class: "note" }, `Computed in the page from ${UNI.src} (field ${stat.field}, generated ${UNI.gen ? fdate(String(UNI.gen).slice(0, 10)) : "–"}), the same file the screener uses. Each company's figure there is built from its own filings and prices with the same definition as this row; open any company's cell in the matrix for its inputs.`));
  }

  /* ---------- shell ---------- */
  function setHash() {
    const h = "#compare-" + STATE.cmp.join(",");
    if (location.hash !== h) history.replaceState(null, "", h);
    store.set("cmp", STATE.cmp);
    document.querySelectorAll("[data-p]").forEach(a => a.classList.toggle("on", a.dataset.p === "compare"));
    document.title = `Compare ${STATE.cmp.join(", ")} · Filed Under`;
  }
  async function setTickers(list) {
    list = [...new Set(list.map(t => String(t).toUpperCase()).filter(t => IDX[t]))].slice(0, MAXN);
    if (!list.length) return;
    STATE.cmp = list;
    await render();
  }
  const addTicker = t => { if (STATE.cmp.includes(t)) return; if (STATE.cmp.length >= MAXN) { toast(`Up to ${MAXN} companies`); return; } setTickers([...STATE.cmp, t]); };
  const removeTicker = t => { if (STATE.cmp.length <= 1) { toast("Keep at least one company"); return; } setTickers(STATE.cmp.filter(x => x !== t)); };

  function menuFor(anchor, build) {
    document.querySelectorAll(".cmp-menu").forEach(m => m.remove());
    const menu = el("div", { class: "cmp-menu", role: "menu" });
    build(menu);
    anchor.append(menu);
    const off = e => { if (!anchor.contains(e.target)) { menu.remove(); document.removeEventListener("mousedown", off); } };
    setTimeout(() => document.addEventListener("mousedown", off), 0);
    return menu;
  }
  function searchBox() {
    const wrap = el("div", { class: "cmp-search" });
    const inp = el("input", { type: "search", placeholder: STATE.cmp.length >= MAXN ? `Up to ${MAXN} companies` : "Add a company: ticker or name", "aria-label": "Add a company", autocomplete: "off" });
    let items = [], sel = 0, menu = null;
    const draw = () => {
      const v = inp.value.trim().toLowerCase();
      menu?.remove(); menu = null;
      if (!v) return;
      items = TICKERS.filter(t => !STATE.cmp.includes(t)).map(t => { const c = IDX[t]; const s = t.toLowerCase() === v ? 0 : t.toLowerCase().startsWith(v) ? 1 : (c.name || "").toLowerCase().startsWith(v) ? 2 : ((c.name || "").toLowerCase().includes(v) || (c.legal_name || "").toLowerCase().includes(v)) ? 3 : 9; return [t, s]; }).filter(x => x[1] < 9).sort((a, b) => a[1] - b[1] || (IDX[b[0]].market_cap || 0) - (IDX[a[0]].market_cap || 0)).slice(0, 8).map(x => x[0]);
      sel = Math.min(sel, Math.max(0, items.length - 1));
      menu = el("div", { class: "cmp-menu" });
      items.forEach((t, i) => menu.append(el("button", { class: i === sel ? "sel" : "", onmousedown: e => { e.preventDefault(); pick(t); } }, miniLogo(t, 18), el("b", { class: "mono" }, t), el("span", {}, IDX[t].name), el("span", { class: "k" }, fmtMoney(IDX[t].market_cap)))));
      if (!items.length) menu.append(el("div", { class: "muted", style: "padding:8px 10px;font-size:12.5px" }, "No covered company matches."));
      wrap.append(menu);
    };
    const pick = t => { inp.value = ""; menu?.remove(); addTicker(t); };
    inp.addEventListener("input", () => { sel = 0; draw(); });
    inp.addEventListener("keydown", e => {
      if (e.key === "ArrowDown") { sel = Math.min(items.length - 1, sel + 1); draw(); e.preventDefault(); }
      else if (e.key === "ArrowUp") { sel = Math.max(0, sel - 1); draw(); e.preventDefault(); }
      else if (e.key === "Enter") { const t = items[sel] || (IDX[inp.value.trim().toUpperCase()] ? inp.value.trim().toUpperCase() : null); if (t) pick(t); e.preventDefault(); }
      else if (e.key === "Escape") { inp.value = ""; menu?.remove(); }
    });
    inp.addEventListener("blur", () => setTimeout(() => menu?.remove(), 120));
    if (STATE.cmp.length >= MAXN) inp.disabled = true;
    wrap.append(inp);
    return wrap;
  }
  async function peerCandidates(kind) {
    const a = STATE.cmp[0], A = IDX[a]; if (!A) return [];
    const pool = INDEX.companies.filter(c => !STATE.cmp.includes(c.ticker));
    if (kind === "sic") {
      let list = pool.filter(c => c.sic && c.sic === A.sic).sort((x, y) => (y.market_cap || 0) - (x.market_cap || 0)).map(c => [c.ticker, `SIC: ${c.sic}`]);
      if (list.length < 5) list = list.concat(pool.filter(c => c.sector === A.sector && c.sic !== A.sic).sort((x, y) => Math.abs(Math.log((x.market_cap || 1) / (A.market_cap || 1))) - Math.abs(Math.log((y.market_cap || 1) / (A.market_cap || 1)))).slice(0, 8 - list.length).map(c => [c.ticker, `same sector: ${c.sector}`]));
      return list.slice(0, 8);
    }
    if (kind === "cap") return pool.filter(c => c.sector === A.sector && c.market_cap).sort((x, y) => Math.abs(Math.log(x.market_cap / A.market_cap)) - Math.abs(Math.log(y.market_cap / A.market_cap))).slice(0, 8).map(c => [c.ticker, fmtMoney(c.market_cap)]);
    const P = await ensurePeers();
    return (P.peers?.[a] || []).filter(([t]) => !STATE.cmp.includes(t) && IDX[t]).slice(0, 8).map(([t, r]) => [t, `ρ ${r.toFixed(2)}`]);
  }
  function peersMenu() {
    const box = el("div", { class: "cmp-dd" });
    const btn = el("button", { class: "btn", "aria-haspopup": "menu" }, "Add peers ▾");
    btn.onclick = () => menuFor(box, async menu => {
      const kinds = [["sic", `Same industry as ${STATE.cmp[0]} (SIC)`], ["cap", `Closest market cap to ${STATE.cmp[0]}, same sector`], ["corr", `Most correlated with ${STATE.cmp[0]}, daily returns 1Y`]];
      for (const [k, title] of kinds) {
        menu.append(el("div", { class: "hd" }, title));
        const list = await peerCandidates(k);
        if (!list.length) { menu.append(el("div", { class: "faint", style: "padding:4px 9px 8px;font-size:12px" }, k === "corr" ? "Correlation peers are not published yet." : "No candidates.")); continue; }
        const room = MAXN - STATE.cmp.length;
        if (room > 1) menu.append(el("button", { onclick: () => { menu.remove(); setTickers([...STATE.cmp, ...list.slice(0, Math.min(3, room)).map(x => x[0])]); } }, el("b", {}, `Add top ${Math.min(3, room)}`), el("span", { class: "k" }, list.slice(0, Math.min(3, room)).map(x => x[0]).join(", "))));
        list.slice(0, 5).forEach(([t, why]) => menu.append(el("button", { disabled: room <= 0, onclick: () => { menu.remove(); addTicker(t); } }, miniLogo(t, 16), el("b", { class: "mono" }, t), el("span", { class: "muted" }, short(t)), el("span", { class: "k" }, why))));
      }
      if (STATE.cmp.length >= MAXN) menu.prepend(el("div", { class: "faint", style: "padding:6px 9px;font-size:12px" }, `The set is full (${MAXN}). Remove a company to add peers.`));
    });
    box.append(btn);
    return box;
  }
  function setsMenu() {
    const box = el("div", { class: "cmp-dd" });
    const sets = () => store.get("cmpSets", []);
    const btn = el("button", { class: "btn", "aria-haspopup": "menu" }, `Saved sets (${sets().length}) ▾`);
    btn.onclick = () => menuFor(box, menu => {
      const name = el("input", { type: "text", placeholder: "Name this set", style: "height:30px;border:1px solid var(--line);border-radius:6px;padding:0 8px;flex:1;min-width:0;background:var(--surface)", value: STATE.cmp.slice(0, 4).join(" vs ") });
      const save = el("button", { class: "btn", style: "width:auto", onclick: () => { const n = name.value.trim() || STATE.cmp.join(", "); const all = sets().filter(s => s.name !== n); all.unshift({ name: n, tickers: STATE.cmp.slice(), saved: new Date().toISOString() }); store.set("cmpSets", all.slice(0, 30)); toast(`Saved "${n}"`); menu.remove(); btn.firstChild.textContent = `Saved sets (${all.length}) ▾`; } }, "Save current");
      menu.append(el("div", { style: "display:flex;gap:6px;padding:6px" }, name, save));
      const all = sets();
      if (all.length) menu.append(el("div", { class: "hd" }, "Open a saved set"));
      all.forEach(s => menu.append(el("button", { onclick: () => { menu.remove(); setTickers(s.tickers); } }, el("b", {}, s.name), el("span", { class: "k" }, s.tickers.join(", ")),
        el("span", { class: "x", role: "button", title: "Delete this set", onclick: e => { e.stopPropagation(); store.set("cmpSets", sets().filter(x => x.name !== s.name)); menu.remove(); btn.firstChild.textContent = `Saved sets (${sets().length}) ▾`; toast(`Deleted "${s.name}"`); } }, "×"))));
      if (!all.length) menu.append(el("div", { class: "faint", style: "padding:4px 9px 8px;font-size:12px" }, "Saved sets stay in this browser."));
    });
    box.append(btn);
    return box;
  }

  const TABS = [["overview", "Overview"], ["financials", "Financial statements"], ["kpis", "KPIs & segments"], ["charts", "Charts"], ["valuation", "Relative valuation"]];
  async function render(arg) {
    injectCss();
    if (arg) { const list = String(arg).split(/[,\s+]+/).map(t => t.trim().toUpperCase()).filter(t => IDX[t]); if (list.length) STATE.cmp = [...new Set(list)].slice(0, MAXN); }
    STATE.cmp = STATE.cmp.filter(t => IDX[t]);
    if (!STATE.cmp.length) STATE.cmp = ["AAPL", "MSFT", "NVDA", "GOOGL"];
    const keep = window.scrollY;
    await Promise.all([...STATE.cmp.map(t => ensureCompany(t).catch(() => null)), ensurePrices("SPY"), ensureUniverse(), ...STATE.cmp.map(ensureHistory), ensureHistory("SPY")]);
    STATE.cmp = STATE.cmp.filter(loaded);
    setHash();
    page.innerHTML = "";
    page.append(el("div", { class: "ph" }, el("div", {}, el("div", { class: "eyebrow" }, "Compare"), el("h1", {}, STATE.cmp.length > 1 ? STATE.cmp.map(short).join(" · ") : "Side by side"),
      el("div", { class: "sub" }, `${STATE.cmp.length} of up to ${MAXN} companies. Cells are shaded by rank within this set. Every number opens its filing, the bars it came from, or its calculation.`)),
      el("div", { class: "right" }, el("button", { class: "btn", title: "Copy a link to this comparison", onclick: () => copyText(location.origin + location.pathname + "#compare-" + STATE.cmp.join(","), "Link copied") }, svgUse("i-copy"), "Copy link"))));
    const chips = el("div", { class: "cmp-row" }, STATE.cmp.map(t => el("span", { class: "cmp-chip" }, el("i", { style: `background:${colorOf(t)}` }), miniLogo(t, 16), el("a", { href: "#company-" + t.toLowerCase(), title: IDX[t].name }, t), el("button", { title: `Remove ${t}`, "aria-label": `Remove ${t}`, onclick: () => removeTicker(t) }, "×"))));
    page.append(el("section", { class: "panel" }, el("div", { class: "cmp-pick" }, chips, el("div", { class: "cmp-row" }, searchBox(), peersMenu(), setsMenu(), STATE.cmp.length > 1 ? el("button", { class: "btn", title: "Clear all but the first company", onclick: () => setTickers(STATE.cmp.slice(0, 1)) }, "Clear") : null))));
    const tabs = el("div", { class: "tabs", role: "tablist" });
    const body = el("div", { style: "display:flex;flex-direction:column;gap:16px" });
    const show = id => {
      if (!TABS.find(t => t[0] === id)) id = "overview";
      CS.tab = id; store.set("cmpTab", id);
      tabs.querySelectorAll("button").forEach(b => { b.classList.toggle("on", b.dataset.id === id); b.setAttribute("aria-selected", b.dataset.id === id); });
      body.innerHTML = "";
      ({ overview: tabOverview, financials: tabFinancials, kpis: tabKpis, charts: tabCharts, valuation: tabValuation })[id](body);
    };
    TABS.forEach(([id, n]) => tabs.append(el("button", { "data-id": id, role: "tab", onclick: () => show(id) }, n)));
    page.append(tabs, body);
    show(CS.tab);
    if (keep) window.scrollTo(0, keep);
  }

  /* ---------- overview: the matrix ---------- */
  let MATRIX = null;
  function tabOverview(body) {
    const data = MATRIX = matrixData();
    const tks = STATE.cmp;
    const csv = el("button", { class: "btn", title: "Download the matrix as CSV" }, svgUse("i-dl"), "CSV");
    const t = el("table", { class: "cmpx" });
    t.append(el("thead", {}, el("tr", {}, el("th", { class: "lab" }, "Metric"),
      tks.map(tk => el("th", {}, el("div", { class: "tk" }, miniLogo(tk, 16), el("a", { href: "#company-" + tk.toLowerCase(), style: "color:inherit" }, tk)), el("div", { class: "nm" }, short(tk)), el("span", { class: "cb", style: `background:${colorOf(tk)}` }))),
      el("th", { class: "ctx", title: "Median of the companies in this comparison" }, "Set median"),
      el("th", { class: "ctx", title: `Median across the S&P 500 from ${UNI?.src || "index.json"}, where the field has the same definition` }, "S&P 500 median"))));
    const tb = el("tbody");
    data.forEach(([g, rows]) => {
      tb.append(el("tr", { class: "grp" }, el("td", { class: "lab" }, g), el("td", { colspan: tks.length + 2 })));
      rows.forEach(({ row, cells, set, spx }) => {
        const vals = tks.map(tk => cells[tk]?.v).filter(v => v != null && isFinite(v));
        const tr = el("tr", {}, el("td", { class: "lab", title: (Object.values(cells).find(Boolean)?.calc?.formula) || row.label }, row.label));
        tks.forEach(tk => {
          const c = cells[tk];
          if (!c) { tr.append(el("td", { class: "na", title: "Not reported or not meaningful for this company" }, "–")); return; }
          const td = el("td", { class: "c", tabindex: 0, "data-k": `${row.key}:${tk}`, style: shade(row, c.v, vals), title: `${tk} ${row.label}: ${fmtV(c.v, row.unit)}\n${c.calc ? c.calc.formula : c.rec ? `${c.rec.form} filed ${c.rec.filed}` : ""}` }, fmtV(c.v, row.unit));
          td.onclick = () => { document.querySelectorAll("table.cmpx td.c.sel").forEach(x => x.classList.remove("sel")); td.classList.add("sel"); openAny(c); };
          td.onkeydown = e => { if (e.key === "Enter") td.click(); };
          tr.append(td);
        });
        const sm = el("td", { class: "ctx" + (set ? " c" : " na"), "data-k": `${row.key}:set` }, set ? fmtV(set.v, row.unit) : "–");
        if (set) sm.onclick = () => openAny(set);
        const sp = el("td", { class: "ctx" + (spx ? " c" : " na"), "data-k": `${row.key}:spx`, title: spx ? `Median of ${spx.pairs.length} companies, field ${spx.field}` : "No S&P 500 field with the same definition yet" }, spx ? fmtV(spx.v, row.unit) : "–");
        if (spx) sp.onclick = () => openAny({ uni: { row, stat: spx } });
        tr.append(sm, sp);
        tb.append(tr);
      });
    });
    t.append(tb);
    const lq = tks.map(tk => `${tk} ${ctx(tk).lq ? plabel(tk, ctx(tk).lq, "Q") : "–"}`).join(" · ");
    const note = el("div", { class: "cmp-note" },
      el("span", {}, el("b", {}, "Latest quarter: "), lq),
      el("span", {}, el("i", { style: `display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:5px;background:color-mix(in srgb, var(--up) 30%, transparent)` }), "best in set"),
      el("span", {}, el("i", { style: `display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:5px;background:color-mix(in srgb, var(--down) 30%, transparent)` }), "worst in set"),
      el("span", { class: "faint" }, `Prices as of ${fdate(ctx(tks[0]).p?.end)}. S&P 500 medians from ${UNI?.src || "index.json"}.`));
    const wrap = el("div", { class: "cmp-wrap" }, t);
    body.append(el("section", { class: "panel" }, el("div", { class: "hd" }, el("h3", {}, "Overview"), el("div", { class: "r" }, csv)), note, wrap,
      el("div", { class: "src", style: "padding-top:10px" }, "Filed values come from each company's 10-K and 10-Q (first filing to report the period); TTM is the sum of the last four quarters. Market values use the latest close and the share count on the latest cover page. Unshaded rows are not ranked (size, or no better direction).")));
    csv.onclick = () => {
      const q = s => `"${String(s).replace(/"/g, '""')}"`;
      const lines = [["Group", "Metric", "Unit", ...tks, "Set median", "S&P 500 median", "Definition"].map(q).join(",")];
      data.forEach(([g, rows]) => rows.forEach(({ row, cells, set, spx }) => lines.push([q(g), q(row.label), q(row.unit), ...tks.map(tk => cells[tk]?.v ?? ""), set?.v ?? "", spx?.v ?? "", q(Object.values(cells).find(Boolean)?.calc?.formula || "Reported value")].join(","))));
      lines.push("", q(`Filed Under compare export, ${new Date().toISOString()}. Percent values are fractions (0.25 = 25%). Prices as of ${ctx(tks[0]).p?.end || ""}.`));
      downloadText(`filedunder_compare_${tks.join("_")}.csv`, lines.join("\n"), "text/csv");
    };
    body.append(aiPanel());
  }
  function flashCell(key) {
    const td = document.querySelector(`table.cmpx td[data-k="${CSS.escape(key)}"]`);
    if (!td) return;
    td.scrollIntoView({ block: "center", inline: "center", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
    td.classList.add("flash"); setTimeout(() => td.classList.remove("flash"), 1600);
  }
  function cellByKey(key) {
    const [rk, who] = key.split(":");
    for (const [, rows] of MATRIX || []) for (const r of rows) if (r.row.key === rk) return who === "set" ? r.set : who === "spx" ? (r.spx ? { uni: { row: r.row, stat: r.spx } } : null) : r.cells[who];
    return null;
  }

  /* ---------- AI comparison: numbers only from the matrix, checked before display ---------- */
  const normNum = s => String(s).replace(/\s+/g, "").replace(/[−–]/g, "-").replace(/×/g, "x").replace(/^\+/, "").replace(/,/g, "").toLowerCase();
  const NUM_RX = /[−–+-]?\$?\d[\d,]*(?:\.\d+)?(?:\s?(?:%|×|x\b|bp\b|[TBMK]\b))?/g;
  function aiPayload() {
    const tks = STATE.cmp, rows = [], allowed = new Map();
    const add = (id, disp) => { if (!allowed.has(normNum(disp))) allowed.set(normNum(disp), []); allowed.get(normNum(disp)).push(id); };
    (MATRIX || matrixData()).forEach(([g, list]) => list.forEach(({ row, cells, set, spx }) => {
      const vals = {};
      tks.forEach(tk => { if (cells[tk]) { const d = fmtV(cells[tk].v, row.unit); vals[tk] = `${d} {${row.key}:${tk}}`; add(`${row.key}:${tk}`, d); } });
      if (set) { const d = fmtV(set.v, row.unit); vals.set_median = `${d} {${row.key}:set}`; add(`${row.key}:set`, d); }
      if (spx) { const d = fmtV(spx.v, row.unit); vals.sp500_median = `${d} {${row.key}:spx}`; add(`${row.key}:spx`, d); }
      if (Object.keys(vals).length) rows.push({ group: g, metric: row.label, higher_is: row.better > 0 ? "better" : row.better < 0 ? "worse" : "neutral", values: vals });
    }));
    const periods = Object.fromEntries(tks.map(tk => [tk, { name: short(tk), latest_quarter: ctx(tk).lq ? plabel(tk, ctx(tk).lq, "Q") : null, sector: IDX[tk]?.sector }]));
    return { rows, periods, allowed };
  }
  function validateAi(text, allowed, extraOk) {
    const ids = new Set([...allowed.values()].flat());
    const paras = String(text).replace(/<think>[\s\S]*?<\/think>/g, "").replace(/\*\*/g, "").split(/\n{2,}|\n(?=[-*•] )/).map(p => p.trim()).filter(Boolean);
    const kept = [], dropped = [];
    for (const p of paras) {
      const sentences = p.replace(/^[-*•]\s+/, "").split(/(?<=[.!?])\s+(?=[A-Z(“"])/);
      const ok = [];
      for (let s of sentences) {
        s = s.trim(); if (!s) continue;
        const cites = [...s.matchAll(/\{([a-z0-9_]+:[A-Za-z0-9.\-]+)\}/g)].map(m => m[1]);
        const bare = s.replace(/\{[^}]*\}/g, "");
        const nums = (bare.match(NUM_RX) || []).map(x => x.trim()).filter(x => /\d/.test(x));
        const bad = nums.filter(n => !allowed.has(normNum(n)) && !extraOk(n));
        if (bad.length || cites.some(c => !ids.has(c))) { dropped.push({ s, bad: bad.concat(cites.filter(c => !ids.has(c))) }); continue; }
        ok.push(s);
      }
      if (ok.length) kept.push(ok.join(" "));
    }
    return { kept, dropped };
  }
  function renderAi(host, kept, dropped, allowed) {
    host.innerHTML = "";
    const box = el("div", { class: "cmp-ai" });
    kept.forEach(p => {
      const para = el("p");
      // number followed by {id}: link to that cell; a bare number that matches exactly one cell links to it too
      const rx = /([−–+-]?\$?\d[\d,]*(?:\.\d+)?(?:\s?(?:%|×|x\b|bp\b|[TBMK]\b))?)(\s*\{([a-z0-9_]+:[A-Za-z0-9.\-]+)\})?/g;
      let i = 0, m;
      while ((m = rx.exec(p))) {
        para.append(p.slice(i, m.index).replace(/\{[^}]*\}/g, ""));
        const id = m[3] || ((allowed.get(normNum(m[1])) || []).length === 1 ? allowed.get(normNum(m[1]))[0] : null);
        if (id && cellByKey(id)) para.append(el("button", { class: "num-link", title: `Open ${id.replace(":", " · ")}`, onclick: () => { flashCell(id); openAny(cellByKey(id)); } }, m[1].trim()));
        else para.append(m[1]);
        i = m.index + m[0].length;
      }
      para.append(p.slice(i).replace(/\{[^}]*\}/g, ""));
      box.append(para);
    });
    if (!kept.length) box.append(el("div", { class: "note" }, "No sentence passed the number check. Try again."));
    if (dropped.length) box.append(el("div", { class: "dropped" }, `${dropped.length} sentence${dropped.length > 1 ? "s" : ""} removed because ${dropped.length > 1 ? "they" : "it"} contained a number not in the matrix: `, dropped.map(d => d.bad.join(", ")).join("; "), "."));
    host.append(box);
  }
  function aiPanel() {
    const out = el("div", { class: "bd" }, el("div", { class: "muted", style: "font-size:13px" }, "A short plain-English comparison written from the matrix above. The model only sees these numbers; any sentence with a number that is not in the matrix is removed, and every number links to its cell."));
    const btn = el("button", { class: "btn acc" }, svgUse("i-spark"), "Write comparison");
    btn.onclick = async () => {
      if (STATE.cmp.length < 2) { toast("Add a second company first"); return; }
      btn.disabled = true; out.innerHTML = ""; out.append(el("span", { class: "thinking" }, el("i"), "Writing"));
      const { rows, periods, allowed } = aiPayload();
      const words = new Set(["1", "2", "3", "4", "5", "52", "60", "500", "1y", "1m", "ytd", "s&p", "10", "8"]);
      const labelNums = new Set(rows.flatMap(r => (r.metric.match(/\d+/g) || [])).concat(Object.values(periods).flatMap(p => (p.latest_quarter || "").match(/\d+/g) || [])).concat(STATE.cmp.flatMap(t => t.match(/\d+/g) || [])));
      const extraOk = n => { const k = normNum(n).replace(/[$%x]/g, ""); return words.has(k) || labelNums.has(k) || /^(19|20)\d\d$/.test(k) && Object.values(periods).some(p => (p.latest_quarter || "").includes(k.slice(2))); };
      const system = "You write short, plain comparisons of public companies for an equity research site. Use only the numbers in the user's table. Copy each number exactly as it appears (same rounding and symbols) and put its cell id in braces right after it, for example: Apple's P/E is 37.0× {pe:AAPL}. Do not compute new numbers, do not round, do not convert units, and do not mention numbers that are not in the table. No investment advice, no hype, no em dashes.";
      const user = `Companies: ${JSON.stringify(periods)}\n\nMatrix (each value is followed by its cell id):\n${JSON.stringify(rows)}\n\nWrite 4 short paragraphs, 2 or 3 sentences each, with no more than 8 numbers per paragraph; pick the differences that matter: valuation, growth and profitability, balance sheet and capital returns, price behavior. Name the companies. Compare them with each other and, where useful, with the set median and the S&P 500 median.`;
      try {
        const r = await agentLoop({ system, turns: [{ role: "user", content: user }], tools: [] });
        const { kept, dropped } = validateAi(r.text, allowed, extraOk);
        renderAi(out, kept, dropped, allowed);
        out.append(el("div", { class: "resp-meta" }, `${r.meta} · ${kept.length} paragraph${kept.length === 1 ? "" : "s"} kept`));
      } catch (e) { out.innerHTML = ""; out.append(el("div", { class: "note" }, AI_ERR[e.code] || AI_ERR.upstream_error)); }
      finally { btn.disabled = false; }
    };
    return panel("AI comparison", btn, out);
  }

  /* ---------- financial statements side by side ---------- */
  const STMT_LABEL = { income: "Income statement", balance: "Balance sheet", cashflow: "Cash flow" };
  const fyeText = tk => { const f = D.companies[tk].fiscalYearEnd || "1231"; return `${MONTHS[+f.slice(0, 2) - 1]} ${+f.slice(2)}`; };
  function periodKey(tk, end, kind, align) {
    if (align === "calendar") {
      const d = new Date(pd(end).getTime() - (kind === "A" ? 180 : 45) * DAY);
      return kind === "A" ? `CY${d.getUTCFullYear()}` : `${kind === "TTM" ? "TTM " : ""}CQ${Math.floor(d.getUTCMonth() / 3) + 1} ${d.getUTCFullYear()}`;
    }
    return kind === "TTM" ? "TTM " + plabel(tk, end, "Q") : plabel(tk, end, kind);
  }
  function stmtCell(tk, label, kind, end, view, stmt) {
    const m = model(tk);
    const base = label === "Diluted shares" ? sharesAt(m, end, kind === "TTM" ? "Q" : kind)
      : kind === "TTM" ? (stmt === "balance" ? m.get(label, "Q", end) : ttmAt(m, tk, label, end)) : m.get(label, kind, end);
    if (!base || base.v == null) return null;
    const money = base.unit === "USD";
    const per = kind === "TTM" ? "TTM to " + fdate(end) : plabel(tk, end, kind);
    if (view === "values") return base;
    if (view === "pershare") {
      if (!money) return base.unit === "USD/shares" ? base : null;
      const sh = sharesAt(m, end, kind === "TTM" ? "Q" : kind); if (!sh) return null;
      return calcCell(tk, `${label} per share`, "USD/shares", base.v / sh.v, `${label} / diluted shares (${sh.derived ? "" : plabel(tk, sh.end, sh.kind)})`, [base, sh], { kind: kind === "TTM" ? "TTM" : kind, end });
    }
    if (view === "common") {
      if (!money) return null;
      const dl = stmt === "balance" ? "Total assets" : "Revenue";
      const d = stmt === "balance" ? (m.get(dl, kind === "TTM" ? "Q" : kind, end)) : kind === "TTM" ? ttmAt(m, tk, dl, end) : m.get(dl, kind, end);
      if (!d || !d.v) return null;
      return calcCell(tk, `${label}, % of ${dl.toLowerCase()}`, "pct", base.v / d.v, `${label} / ${dl} (${per})`, [base, d], { kind: kind === "TTM" ? "TTM" : kind, end });
    }
    if (view === "growth") {
      const pe = m.priorEnd(kind === "A" ? "A" : "Q", end); if (!pe) return null;
      const p = stmtCell(tk, label, kind, pe, "values", stmt);
      if (!p || !p.v) return null;
      return calcCell(tk, `${label}, YoY change`, "pct", base.v / Math.abs(p.v) - Math.sign(p.v), `${label} ${per} / ${label} a year earlier − 1`, [base, p], { kind: kind === "TTM" ? "TTM" : kind, end });
    }
    return base;
  }
  function tabFinancials(body) {
    const tks = STATE.cmp;
    const nOpts = () => CS.kind === "A" ? [["5", "5Y"], ["10", "10Y"], ["all", "All"]] : [["8", "8Q"], ["16", "16Q"], ["all", "All"]];
    if (!nOpts().some(o => o[0] === CS.n)) CS.n = nOpts()[0][0];
    const nHost = el("span");
    const csv = el("button", { class: "btn", title: "Download this view as CSV" }, svgUse("i-dl"), "CSV");
    const tools = el("div", { class: "toolbar" },
      seg([["income", "Income"], ["balance", "Balance sheet"], ["cashflow", "Cash flow"]], CS.stmt, v => { CS.stmt = v; draw(); }),
      seg([["A", "Annual"], ["Q", "Quarterly"], ["TTM", "TTM"]], CS.kind, v => { CS.kind = v; CS.n = nOpts()[0][0]; drawN(); draw(); }),
      nHost,
      seg([["values", "Values"], ["pershare", "Per share"], ["common", "Common size"], ["growth", "Growth YoY"]], CS.view, v => { CS.view = v; draw(); }),
      seg([["fiscal", "Fiscal periods"], ["calendar", "Calendar periods"]], CS.align, v => { CS.align = v; draw(); }),
      el("span", { class: "sp" }), csv);
    const drawN = () => { nHost.innerHTML = ""; nHost.append(seg(nOpts(), CS.n, v => { CS.n = v; draw(); })); };
    drawN();
    const note = el("div", { class: "cmp-note" });
    const wrap = el("div", { class: "cmp-wrap" });
    body.append(el("section", { class: "panel" }, tools, note, wrap, el("div", { class: "src", style: "padding-top:10px" }, "Values from each company's 10-K and 10-Q XBRL facts (first filing to report the period). TTM sums the last four quarters; balance sheet TTM columns show the quarter-end balance. Per share uses diluted weighted shares for the same period. Click any value for its filing or calculation; click a line name to chart it.")));
    let current = null;
    csv.onclick = () => {
      if (!current) return;
      const q = s => `"${String(s).replace(/"/g, '""')}"`;
      const lines = [["Line item", "Company", ...current.cols].map(q).join(",")];
      current.rows.forEach(r => tks.forEach(tk => lines.push([q(r.label), tk, ...current.cols.map(k => r.byTk[tk]?.[k]?.v ?? "")].join(","))));
      downloadText(`filedunder_compare_${CS.stmt}_${CS.kind}_${CS.view}.csv`, lines.join("\n"), "text/csv");
    };
    function draw() {
      const { stmt, kind, view, align } = CS;
      const fyes = [...new Set(tks.map(fyeText))];
      note.innerHTML = "";
      note.append(el("span", {}, el("b", {}, "Fiscal years end: "), tks.map(tk => `${tk} ${fyeText(tk)}`).join(" · ")));
      if (fyes.length > 1) note.append(el("span", { class: "chip warn" }, align === "fiscal" ? "Fiscal years end in different months, so the same fiscal label covers different months. Switch to calendar periods to line them up by the calendar period they mostly cover." : "Calendar periods: each fiscal period is placed in the calendar year or quarter that holds most of its months."));
      const cols = new Map();
      const byTk = {};
      const labels = new Set();
      tks.forEach(tk => {
        const m = model(tk);
        (m.rows[stmt] || []).forEach(r => labels.add(r.label));
        const k = kind === "TTM" ? "Q" : kind;
        let ends = stmt === "balance" ? (() => { const s = new Set(); m.rows.balance.forEach(r => r[k].forEach((_, e) => s.add(e))); return [...s].sort(); })() : m.periods(k);
        byTk[tk] = {};
        ends.forEach(e => { const key = periodKey(tk, e, kind, align); byTk[tk][key] = e; if (!cols.has(key)) cols.set(key, []); cols.get(key).push(pd(e).getTime()); });
      });
      let colKeys = [...cols.keys()].sort((a, b) => mean(cols.get(a)) - mean(cols.get(b)));
      // keep columns where at least half of the companies have a period
      colKeys = colKeys.filter(k => cols.get(k).length >= Math.max(1, Math.ceil(tks.length / 2)) || tks.length <= 2);
      if (CS.n !== "all") colKeys = colKeys.slice(-Number(CS.n));
      const order = l => { const i = METRIC_LABELS.indexOf(l); return i < 0 ? 999 : i; };
      const lab = [...labels].sort((a, b) => order(a) - order(b));
      const t = el("table", { class: "cmpx" });
      t.append(el("thead", {}, el("tr", {}, el("th", { class: "lab" }, STMT_LABEL[stmt], el("span", { class: "sub2", style: "font-family:var(--f-ui)" }, { values: "values", pershare: "per diluted share", common: stmt === "balance" ? "% of total assets" : "% of revenue", growth: "change vs a year earlier" }[view])), colKeys.map(k => el("th", {}, k)))));
      const tb = el("tbody");
      const rowsOut = [];
      lab.forEach(label => {
        const rowCells = {}; let any = false;
        tks.forEach(tk => { rowCells[tk] = {}; colKeys.forEach(k => { const e = byTk[tk][k]; const c = e ? stmtCell(tk, label, kind, e, view, stmt) : null; if (c) { rowCells[tk][k] = c; any = true; } }); });
        if (!any) return;
        rowsOut.push({ label, byTk: rowCells });
        const li = el("tr", { class: "li" }, el("td", { class: "lab", title: "Chart this line for all companies" }, label), el("td", { colspan: colKeys.length }));
        li.firstChild.onclick = () => { CS.metric = label; CS.mkind = kind === "A" ? "A" : kind === "TTM" ? "TTM" : "Q"; document.querySelector('.tabs button[data-id="charts"]')?.click(); };
        tb.append(li);
        tks.forEach(tk => {
          const tr = el("tr", { class: "co" }, el("td", { class: "lab" }, el("span", { class: "lw" }, el("i", { class: "dot", style: `background:${colorOf(tk)}` }), el("span", { class: "mono" }, tk))));
          colKeys.forEach(k => {
            const c = rowCells[tk][k];
            if (!c) { tr.append(el("td", { class: "na" }, "–")); return; }
            const e = byTk[tk][k];
            const txt = view === "growth" ? fsgn(c.v) : fmtV(c.v, c.unit);
            const td = el("td", { class: "c" + (c.derived ? " der" : ""), tabindex: 0, title: `${tk} ${plabel(tk, e, kind === "A" ? "A" : "Q")}${kind === "TTM" ? " TTM" : ""} · period ends ${fdate(e)}\n${c.calc ? c.calc.formula : `${c.rec.form} filed ${c.rec.filed}${c.derived ? " · derived quarter" : ""}`}` }, txt);
            if (c.v < 0 && view !== "growth") td.style.color = "var(--down)";
            if (view === "growth") td.classList.add(cls(c.v));
            td.onclick = () => { document.querySelectorAll("table.cmpx td.c.sel").forEach(x => x.classList.remove("sel")); td.classList.add("sel"); openAny(c); };
            td.onkeydown = ev => { if (ev.key === "Enter") td.click(); };
            tr.append(td);
          });
          tb.append(tr);
        });
      });
      t.append(tb);
      current = { cols: colKeys, rows: rowsOut };
      wrap.innerHTML = ""; wrap.append(t);
      requestAnimationFrame(() => { wrap.scrollLeft = wrap.scrollWidth; });
    }
    draw();
  }

  /* ---------- KPIs and segment mix ---------- */
  const kpiNorm = s => String(s || "").toLowerCase().replace(/\([^)]*\)/g, " ").replace(/&/g, " and ").replace(/[^a-z0-9%]+/g, " ").replace(/\b(total|the|reported)\b/g, " ").replace(/\s+/g, " ").trim();
  const unitClass = u => { u = String(u || "").toLowerCase(); return /usd|\$/.test(u) ? (/per|unit/.test(u) ? "usd per unit" : "usd") : /%|pct|percent/.test(u) ? "percent" : "count"; };
  const canonOf = (kpi, it) => it.canonical || it.canonical_id || it.std_id || it.kpi_std || kpi?.canonical?.[it.id] || null;
  function kpiLatest(it, K, aEnds) {
    const vals = kpiVals(it, K, aEnds); const ends = Object.keys(vals).sort(); const e = ends.at(-1); if (!e) return null;
    const pe = ends.find(x => Math.abs(dayDiff(x, e) + 365) <= 20);
    return { e, v: vals[e], p: pe ? vals[pe] : null, pe, vals };
  }
  function kpiCell(tk, it, K, e, v) {
    return { tk, label: it.name, unit: "kpi", v: v.v, kpi: true, end: e, kpiOpen: () => openKpiSource(tk, it, K, e, v) };
  }
  async function tabKpis(body) {
    const tks = STATE.cmp;
    const status = el("div", { class: "muted", style: "padding:12px 4px" }, `Loading investor-materials KPIs and XBRL breakdowns for ${tks.length} companies…`);
    body.append(status);
    const data = Object.fromEntries(await Promise.all(tks.map(async tk => [tk, await ensureSegments(tk).catch(() => ({}))])));
    if (!body.isConnected) return;
    status.remove();
    kpiPanel(body, tks, data);
    mixPanel(body, tks, data);
  }
  function kpiPanel(body, tks, data) {
    const groups = new Map();
    const aEndsOf = {};
    const counts = {};
    tks.forEach(tk => {
      const kpi = data[tk]?.kpi; const aEnds = aEndsOf[tk] = new Set();
      (kpi?.groups || []).forEach(g => g.items.forEach(it => Object.keys(it.values?.A || {}).forEach(e => aEnds.add(e))));
      const items = (kpi?.groups || []).flatMap(g => g.items.map(it => ({ it, g })));
      counts[tk] = items.length;
      const byId = Object.fromEntries(items.map(x => [x.it.id, x.it]));
      // only series the company still reports: a value within fifteen months of its latest KPI period
      const latestAll = items.reduce((mm, x) => { const l = kpiLastEnd(x.it); return l > mm ? l : mm; }, "");
      const cut = shiftDays(latestAll, -455);
      items.forEach(({ it, g }) => {
        const canon = canonOf(kpi, it);
        const parentName = it.parent && byId[it.parent] ? byId[it.parent].name : null;
        const key = canon ? "std:" + canon : "lbl:" + kpiNorm(parentName ? parentName + " " + it.name : it.name) + "|" + unitClass(it.unit);
        const lat = kpiLatest(it, CS.kpiK, aEnds); if (!lat || lat.e < cut) return;
        if (!groups.has(key)) groups.set(key, { key, std: !!canon, name: canon ? (it.canonical_label || it.name) : (parentName ? `${parentName} › ${it.name}` : it.name), unit: it.unit, by: {} });
        const gr = groups.get(key);
        const cur = gr.by[tk];
        // several items with the same label in one company: keep the top-level, most recent one
        if (!cur || (it.depth || 0) < (cur.it.depth || 0) || ((it.depth || 0) === (cur.it.depth || 0) && lat.e > cur.lat.e)) gr.by[tk] = { it, g, lat };
      });
    });
    const rows = [...groups.values()].filter(g => Object.keys(g.by).length >= 2).sort((a, b) => Object.keys(b.by).length - Object.keys(a.by).length || (b.std - a.std) || a.name.localeCompare(b.name));
    const kSeg = seg([["Q", "Quarterly"], ["A", "Annual"]], CS.kpiK, v => { CS.kpiK = v; const host = el("div"); p.replaceWith(host); kpiPanel(host, tks, data); host.replaceWith(...host.childNodes); });
    const t = el("table", { class: "cmpx" });
    t.append(el("thead", {}, el("tr", {}, el("th", { class: "lab" }, "KPI"), tks.map(tk => el("th", {}, el("div", { class: "tk" }, miniLogo(tk, 16), tk), el("div", { class: "nm" }, `${counts[tk] || 0} KPIs`), el("span", { class: "cb", style: `background:${colorOf(tk)}` }))))));
    const tb = el("tbody");
    rows.slice(0, 80).forEach(gr => {
      const tr = el("tr", {}, el("td", { class: "lab", style: "cursor:pointer", title: "Show the history for every company" }, gr.name, gr.std ? el("span", { class: "cmp-k std", title: "Same canonical KPI id across companies" }, "Standardized") : el("span", { class: "cmp-k ns", title: "Matched by label and unit only; definitions can differ between companies" }, "Not standardized")));
      tks.forEach(tk => {
        const x = gr.by[tk];
        if (!x) { tr.append(el("td", { class: "na" }, "–")); return; }
        const { it, lat } = x;
        const per = lat.v.label || kpiPeriodLabel(tk, lat.e, CS.kpiK);
        const chg = lat.p && lat.p.v ? (unitClass(it.unit) === "percent" ? null : lat.v.v / Math.abs(lat.p.v) - Math.sign(lat.p.v)) : null;
        const td = el("td", { class: "c", tabindex: 0, title: `${it.name} · ${per}\n${lat.v.doc_title || ""}${lat.v.page ? " · page " + lat.v.page : ""}` }, (QMARK[lat.v.q] || "") + fmtKpi(lat.v.v, it.unit), el("span", { class: "sub2" }, per + (chg != null ? ` · ${fsgn(chg)} YoY` : "")));
        td.onclick = () => { document.querySelectorAll("table.cmpx td.c.sel").forEach(z => z.classList.remove("sel")); td.classList.add("sel"); openKpiSource(tk, it, CS.kpiK, lat.e, lat.v); };
        tr.append(td);
      });
      tr.firstChild.onclick = () => {
        const nx = tr.nextElementSibling; if (nx?.classList.contains("exp")) { nx.remove(); return; }
        const host = el("div", { class: "cmp-charts" });
        const ex = el("tr", { class: "exp" }, el("td", { colspan: tks.length + 1, style: "text-align:left;white-space:normal" }, host));
        tr.after(ex);
        const series = tks.filter(tk => gr.by[tk]).map(tk => { const { it, lat } = gr.by[tk]; return { name: tk, color: colorOf(tk), pts: Object.entries(lat.vals).sort((a, b) => a[0].localeCompare(b[0])).slice(-24).map(([e, v]) => ({ x: pd(e), y: v.v, cell: kpiCell(tk, it, CS.kpiK, e, v) })) }; });
        requestAnimationFrame(() => xyChart(host, series, { height: 220, dots: true, yfmt: v => fmtKpi(v, gr.unit), onPoint: c => c.kpiOpen() }));
      };
      tb.append(tr);
    });
    if (!rows.length) tb.append(el("tr", {}, el("td", { colspan: tks.length + 1, class: "muted", style: "text-align:left;padding:18px;white-space:normal" }, tks.some(tk => counts[tk]) ? "No KPI is reported by two or more of these companies under the same label. Company-specific KPIs are on each company's Segments & KPIs tab." : "None of these companies has investor-materials KPIs collected yet.")));
    t.append(tb);
    const p = panel("Comparable KPIs from investor materials", kSeg, el("div", { class: "cmp-note" }, el("span", {}, `${rows.length} KPI${rows.length === 1 ? "" : "s"} reported by at least two of these companies. `), el("span", { class: "cmp-k ns" }, "Not standardized"), el("span", { class: "faint" }, "means the match is by label and unit; check each company's definition in the source before comparing. Click a KPI name for its history, a value for the document page it came from.")), el("div", { class: "cmp-wrap" }, t));
    body.append(p);
  }
  function revTable(seg, cat) {
    const ts = (seg?.tables || []).filter(t => t.family === "revenue" && t.category === cat && t.rows.length >= 2);
    const lastOf = t => { let m = ""; t.rows.concat([t.total]).forEach(r => ["Q", "A"].forEach(K => Object.keys(r[K] || {}).forEach(e => { if (e > m) m = e; }))); return m; };
    return ts.sort((a, b) => (a.view === "summary") - (b.view === "summary") || lastOf(b).localeCompare(lastOf(a)))[0] || null;
  }
  function mixPanel(body, tks, data) {
    const cat = CS.mixCat || "segment";
    const K = CS.segK;
    const grid = el("div", { class: "cmp-mix" });
    const tools = el("div", { class: "cmp-tools" }, seg([["segment", "Business segments"], ["product", "Products and services"], ["geo", "Geography"]], cat, v => { CS.mixCat = v; redraw(); }), seg([["Q", "Quarterly"], ["A", "Annual"]], K, v => { CS.segK = v; redraw(); }));
    const p = panel("Revenue mix over time", tools, el("div", { class: "bd" }, grid), el("div", { class: "src" }, "Share of each line in the consolidated total, from the inline XBRL breakdowns in each 10-K and 10-Q. Periods where the reported lines do not add up to the total within 2% are left out. Click a bar section for the filing."));
    body.append(p);
    function redraw() { const host = el("div"); p.replaceWith(host); mixPanel(host, tks, data); host.replaceWith(...host.childNodes); }
    tks.forEach(tk => {
      const t = revTable(data[tk]?.seg, cat);
      const box = el("div", { class: "mx" }, el("h4", {}, el("i", { style: `width:9px;height:9px;border-radius:50%;background:${colorOf(tk)}` }), tk, el("span", { class: "muted", style: "font-weight:400" }, short(tk))));
      grid.append(box);
      if (!t) { box.append(el("div", { class: "faint", style: "font-size:12.5px;padding:8px 0" }, "No tagged revenue breakdown of this kind.")); return; }
      const host = el("div");
      box.append(host);
      requestAnimationFrame(() => mixChart(host, tk, t, K));
    });
  }
  function mixChart(host, tk, t, K) {
    const tops = t.rows.filter(r => !r.parent && !r.recon && !r.memo);
    const ends = [...new Set(tops.flatMap(r => Object.keys(r[K] || {})))].sort();
    const periods = ends.filter(e => {
      const tot = t.total[K]?.[e]?.v; if (!tot) return false;
      const sum = tops.reduce((a, r) => a + (r[K]?.[e]?.v || 0), 0);
      return Math.abs(sum / tot - 1) <= 0.02 && tops.every(r => (r[K]?.[e]?.v || 0) >= 0);
    }).slice(K === "A" ? -6 : -8);
    if (!periods.length) { host.append(el("div", { class: "faint", style: "font-size:12.5px;padding:8px 0" }, "No period where the lines tie to the total.")); return; }
    const used = tops.filter(r => periods.some(e => r[K]?.[e]));
    const col = (i) => SEG_COLORS[i % SEG_COLORS.length];
    const W = host.clientWidth || 300, H = 190, m = { t: 6, r: 6, b: 22, l: 34 };
    const x = d3.scaleBand().domain(periods).range([m.l, W - m.r]).padding(0.22);
    const y = d3.scaleLinear().domain([0, 1]).range([H - m.b, m.t]);
    const svg = d3.select(host).append("svg").attr("viewBox", `0 0 ${W} ${H}`).attr("width", "100%").attr("height", H);
    svg.append("g").attr("transform", `translate(${m.l},0)`).call(d3.axisLeft(y).ticks(4).tickFormat(v => fpct(v, 0)).tickSize(-(W - m.l - m.r))).call(g => { g.select(".domain").remove(); g.selectAll("line").attr("stroke", css("--line")); });
    svg.append("g").attr("transform", `translate(0,${H - m.b})`).call(d3.axisBottom(x).tickSize(0).tickFormat(e => plabel(tk, e, K === "A" ? "A" : "Q").replace(" FY", "·"))).call(g => g.select(".domain").remove());
    periods.forEach(e => {
      const tot = t.total[K][e].v; let acc = 0;
      used.forEach((r, i) => {
        const c = r[K]?.[e]; if (!c || !c.v) return;
        const y0 = acc; acc += c.v / tot;
        svg.append("rect").attr("x", x(e)).attr("width", x.bandwidth()).attr("y", y(acc)).attr("height", Math.max(0, y(y0) - y(acc))).attr("fill", col(i)).style("cursor", "pointer")
          .on("mousemove", ev => showTip(ev, `<b>${esc(r.label)}</b><br>${esc(plabel(tk, e, K === "A" ? "A" : "Q"))} · ${fmtMoney(c.v)} (${fpct(c.v / tot)})<div style="opacity:.7;margin-top:2px">Click for the filing</div>`))
          .on("mouseleave", hideTip).on("click", () => openSource(segCell(tk, t, r, K, c, r[K]), "filing"));
      });
    });
    host.append(el("div", { class: "cmp-legend" }, used.map((r, i) => el("span", {}, el("i", { style: `background:${col(i)}` }), r.label))));
  }

  /* ---------- charts ---------- */
  function xyChart(host, series, o = {}) {
    host.innerHTML = "";
    const W = host.clientWidth || 600, H = o.height || 280, m = { t: 12, r: o.right ?? 58, b: 26, l: 8 };
    const ok = p => p.y != null && isFinite(p.y) && (!o.log || p.y > 0);
    const ser = series.map(s => ({ ...s, pts: s.pts.filter(ok) })).filter(s => s.pts.length);
    const all = ser.flatMap(s => s.pts);
    if (!all.length) { host.append(el("div", { class: "empty" }, o.empty || "No data for this selection")); return; }
    const x = d3.scaleUtc().domain(d3.extent(all, p => p.x)).range([m.l, W - m.r]);
    let [y0, y1] = d3.extent(all, p => p.y);
    if (o.base != null && !o.log) { y0 = Math.min(y0, o.base); y1 = Math.max(y1, o.base); }
    if (o.zero && !o.log) { y0 = Math.min(0, y0); y1 = Math.max(0, y1); }
    const y = o.log ? d3.scaleLog().domain([y0 / 1.08, y1 * 1.08]).range([H - m.b, m.t]) : d3.scaleLinear().domain([y0 - (y1 - y0) * .06 || y0 - 1, y1 + (y1 - y0) * .06 || y1 + 1]).nice().range([H - m.b, m.t]);
    const svg = d3.select(host).append("svg").attr("viewBox", `0 0 ${W} ${H}`).attr("height", H);
    const yt = o.log ? y.ticks(5).filter((v, i, a) => a.length <= 6 || [1, 2, 5].includes(+String(v.toExponential()).charAt(0))) : y.ticks(5);
    svg.append("g").attr("class", "grid").attr("transform", `translate(${W - m.r},0)`).call(d3.axisRight(y).tickValues(yt).tickSize(-(W - m.r - m.l)).tickFormat(o.yfmt || d3.format("~s"))).call(g => g.select(".domain").remove()).call(g => g.selectAll("text").attr("x", 6));
    svg.append("g").attr("transform", `translate(0,${H - m.b})`).call(d3.axisBottom(x).ticks(Math.max(2, Math.floor(W / 100))).tickSizeOuter(0));
    const refY = o.base != null ? o.base : (!o.log && y.domain()[0] < 0 && y.domain()[1] > 0 ? 0 : null);
    if (refY != null) svg.append("line").attr("x1", m.l).attr("x2", W - m.r).attr("y1", y(refY)).attr("y2", y(refY)).attr("stroke", css("--line2"));
    ser.forEach(s => {
      const line = d3.line().x(p => x(p.x)).y(p => y(p.y)).curve(o.step ? d3.curveStepAfter : d3.curveMonotoneX);
      if (o.area) svg.append("path").attr("d", d3.area().x(p => x(p.x)).y0(y(refY ?? y.domain()[0])).y1(p => y(p.y)).curve(d3.curveMonotoneX)(s.pts)).attr("fill", s.color).attr("opacity", .06);
      svg.append("path").attr("d", line(s.pts)).attr("fill", "none").attr("stroke", s.color).attr("stroke-width", s.width || 1.7).attr("stroke-dasharray", s.dash || null).attr("opacity", s.opacity || 1);
      if (o.dots) svg.selectAll(null).data(s.pts).join("circle").attr("cx", p => x(p.x)).attr("cy", p => y(p.y)).attr("r", 2.6).attr("fill", s.color);
    });
    const fmt = o.tfmt || o.yfmt || d3.format(",.2f");
    const focus = svg.append("line").attr("y1", m.t).attr("y2", H - m.b).attr("stroke", css("--faint")).attr("stroke-dasharray", "2 2").style("display", "none");
    const nearest = (mx, my) => { let best = null, bd = Infinity; ser.forEach(s => s.pts.forEach(p => { const d = Math.hypot(x(p.x) - mx, my == null ? 0 : (y(p.y) - my) * .35); if (d < bd) { bd = d; best = { p, s }; } })); return best; };
    svg.append("rect").attr("x", m.l).attr("y", m.t).attr("width", W - m.l - m.r).attr("height", H - m.t - m.b).attr("fill", "transparent").style("cursor", o.onPoint ? "pointer" : null)
      .on("mousemove", ev => {
        const [mx] = d3.pointer(ev); const t = x.invert(mx);
        const rowsHtml = ser.map(s => { let b = null, bd = Infinity; for (const p of s.pts) { const d = Math.abs(p.x - t); if (d < bd) { bd = d; b = p; } } return b && bd < (o.tol || 120 * DAY) ? `<div><span style="color:${s.color}">●</span> ${esc(s.name)} <b>${fmt(b.y)}</b>${o.sub ? ` <span style="opacity:.7">${esc(o.sub(b))}</span>` : ""}</div>` : ""; }).join("");
        focus.style("display", null).attr("x1", mx).attr("x2", mx);
        showTip(ev, `<div style="opacity:.7">${d3.utcFormat("%b %d, %Y")(t)}</div>${rowsHtml}${o.onPoint ? '<div style="opacity:.7;margin-top:2px">Click for the source of the nearest point</div>' : ""}`);
      }).on("mouseleave", () => { focus.style("display", "none"); hideTip(); })
      .on("click", ev => { if (!o.onPoint) return; const [mx, my] = d3.pointer(ev); const n = nearest(mx, my); if (n?.p.cell) o.onPoint(n.p.cell, n.s, n.p); else if (n) o.onPoint(null, n.s, n.p); });
    if (o.legend !== false && ser.length > 1) host.append(el("div", { class: "cmp-legend" }, ser.map(s => el("span", {}, el("i", { style: `background:${s.color}` }), s.name))));
  }
  const TTM_RATIO = {
    "Gross margin": [["Gross profit"], ["Revenue"], (a, b) => a / b, "TTM gross profit / TTM revenue"],
    "Operating margin": [["Operating income"], ["Revenue"], (a, b) => a / b, "TTM operating income / TTM revenue"],
    "Net margin": [["Net income"], ["Revenue"], (a, b) => a / b, "TTM net income / TTM revenue"],
    "R&D % of revenue": [["Research & development"], ["Revenue"], (a, b) => a / b, "TTM R&D / TTM revenue"],
    "Effective tax rate": [["Income tax"], ["Pretax income"], (a, b) => a / b, "TTM income tax / TTM pretax income"],
  };
  function metricCell(tk, label, kind, end) {
    const m = model(tk);
    if (kind !== "TTM") return label === "Diluted shares" ? sharesAt(m, end, kind) : m.metric(label, kind, end);
    if (m.byLabel[label]) {
      const st = m.byLabel[label].stmt;
      if (st === "balance") return m.get(label, "Q", end);
      if (label === "Diluted shares") return sharesAt(m, end);
      return ttmAt(m, tk, label, end);
    }
    if (TTM_RATIO[label]) { const [[a], [b], f, formula] = TTM_RATIO[label]; let A = ttmAt(m, tk, a, end); const B = ttmAt(m, tk, b, end); if (!A && a === "Gross profit") { const cg = ttmAt(m, tk, "Cost of revenue", end); if (cg && B) A = calcCell(tk, "Gross profit", "USD", B.v - cg.v, "TTM revenue − TTM cost of revenue", [B, cg], { end }); } return A && B && B.v ? calcCell(tk, label + " TTM", "pct", f(A.v, B.v), formula, [A, B], { end }) : null; }
    if (label === "Free cash flow" || label === "FCF margin" || label === "Capital returned") {
      const o = ttmAt(m, tk, "Operating cash flow", end), c = ttmAt(m, tk, "Capex", end), r = ttmAt(m, tk, "Revenue", end);
      if (label === "Free cash flow") return o && c ? calcCell(tk, "Free cash flow TTM", "USD", o.v - c.v, "TTM operating cash flow − TTM capex", [o, c], { end }) : null;
      if (label === "FCF margin") return o && c && r?.v ? calcCell(tk, "FCF margin TTM", "pct", (o.v - c.v) / r.v, "(TTM operating cash flow − TTM capex) / TTM revenue", [o, c, r], { end }) : null;
      const b = ttmAt(m, tk, "Buybacks", end), d = ttmAt(m, tk, "Dividends paid", end);
      return b || d ? calcCell(tk, "Capital returned TTM", "USD", (b?.v || 0) + (d?.v || 0), "TTM buybacks + TTM dividends paid", [b, d], { end }) : null;
    }
    if (label === "Revenue growth YoY" || label === "EPS growth YoY") {
      const l = label.startsWith("EPS") ? "EPS diluted" : "Revenue"; const pe = m.priorEnd("Q", end); const a = ttmAt(m, tk, l, end), b = pe && ttmAt(m, tk, l, pe);
      return a && b && b.v ? calcCell(tk, `${l} growth, TTM YoY`, "pct", a.v / Math.abs(b.v) - Math.sign(b.v), `TTM ${l} / TTM ${l} a year earlier − 1`, [a, b], { end }) : null;
    }
    if (label === "Return on equity") { const n = ttmAt(m, tk, "Net income", end), e = m.get("Shareholders' equity", "Q", end); return n && e?.v ? calcCell(tk, "Return on equity TTM", "pct", n.v / e.v, "TTM net income / shareholders' equity (period end)", [n, e], { end }) : null; }
    return m.metric(label, "Q", end);
  }
  function tabCharts(body) {
    body.classList.add("cmp-charts");
    const tks = STATE.cmp;
    // 1. any metric over time
    const msel = el("select", { class: "btn", "aria-label": "Metric" }, METRIC_LABELS.map(l => { const o = el("option", { value: l }, l); if (l === CS.metric) o.selected = true; return o; }));
    const idxCb = el("input", { type: "checkbox" }); idxCb.checked = CS.idx;
    const logCb = el("input", { type: "checkbox" }); logCb.checked = CS.log;
    const rngSeg = () => seg(CS.mkind === "A" ? [["5", "5Y"], ["10", "10Y"], ["all", "All"]] : [["12", "3Y"], ["20", "5Y"], ["all", "All"]], CS.mrange || "all", v => { CS.mrange = v; drawM(); });
    const rngHost = el("span", {}, rngSeg());
    const mHost = el("div", { class: "bd" });
    body.append(panel("Metric over time", el("div", { class: "cmp-tools" }, msel, seg([["Q", "Quarterly"], ["A", "Annual"], ["TTM", "TTM"]], CS.mkind, v => { CS.mkind = v; CS.mrange = v === "A" ? "10" : "20"; rngHost.innerHTML = ""; rngHost.append(rngSeg()); drawM(); }), rngHost, el("label", { class: "ck" }, idxCb, "Index to 100"), el("label", { class: "ck" }, logCb, "Log scale")), mHost,
      el("div", { class: "src" }, "Points sit on each company's period end date, so different fiscal calendars line up in time. TTM ratios use TTM numerators and denominators. Click a point for its filing or calculation.")));
    msel.onchange = () => { CS.metric = msel.value; drawM(); };
    idxCb.onchange = () => { CS.idx = idxCb.checked; drawM(); };
    logCb.onchange = () => { CS.log = logCb.checked; drawM(); };
    function drawM() {
      const lab = CS.metric, kind = CS.mkind;
      const series = tks.map(tk => {
        const m = model(tk); let ends = m.periods(kind === "A" ? "A" : "Q");
        if (m.byLabel[lab]?.stmt === "balance") { const r = m.byLabel[lab]; ends = [...r[kind === "A" ? "A" : "Q"].keys()].sort(); }
        if (CS.mrange && CS.mrange !== "all") ends = ends.slice(-Number(CS.mrange));
        let pts = ends.map(e => { const c = metricCell(tk, lab, kind, e); return c && c.v != null ? { x: pd(e), y: c.v, cell: c, e } : null; }).filter(Boolean);
        if (CS.idx) { const b = pts.find(p => p.y > 0); pts = b ? pts.filter(p => p.x >= b.x).map(p => ({ ...p, raw: p.y, y: p.y / b.y * 100 })) : []; }
        return { name: tk, color: colorOf(tk), pts };
      });
      const unit = series.flatMap(s => s.pts)[0]?.cell?.unit;
      const yfmt = CS.idx ? v => d3.format(",.0f")(v) : unit === "pct" ? v => fpct(v, 0) : unit === "x" ? v => v.toFixed(1) + "×" : unit === "USD/shares" ? v => "$" + v.toFixed(2) : unit === "shares" ? v => fmtMoney(v, "shares") : v => fmtMoney(v);
      xyChart(mHost, series, { height: 300, dots: true, log: CS.log, base: CS.idx ? 100 : null, yfmt, tfmt: CS.idx ? v => v.toFixed(1) : unit === "pct" ? v => fpct(v) : yfmt, sub: p => `${plabel(p.cell.tk, p.e, kind === "A" ? "A" : "Q")}${kind === "TTM" ? " TTM" : ""}${CS.idx ? " · " + fmtV(p.raw, unit) : ""}`, onPoint: c => openAny(c), empty: CS.log ? "No positive values to draw on a log scale" : "No data for this metric" });
    }
    // 2. total return and drawdown
    const trHost = el("div", { class: "bd" }), ddHost = el("div", { class: "bd", style: "padding-top:0" });
    body.append(panel("Total return, rebased to 100", seg([["1Y", "1Y"], ["2Y", "2Y"], ["5Y", "5Y weekly"]], CS.trRange, v => { CS.trRange = v; drawTR(); }), trHost, el("div", { class: "eyebrow", style: "padding:0 16px" }, "Drawdown from running high"), ddHost,
      el("div", { class: "src" }, "1Y and 2Y: daily closes with each dividend reinvested on its ex-date. 5Y: weekly closes adjusted for splits and dividends. Yahoo Finance chart API. SPY dashed. Click a line for the calculation at that date.")));
    function trSeries(sym) {
      if (CS.trRange === "5Y") {
        const h = HIST[sym]; if (!h?.w?.length) return null;
        const w = h.w, a0 = w[0][2];
        const path = w.map((x, i) => ({ x: new Date(x[0] * 1000), y: x[2] / a0 * 100, i }));
        const cellAt = i => calcCell(sym, `Total return since ${iso(new Date(w[0][0] * 1000))}`, "pct", w[i][2] / a0 - 1, `Adjusted weekly close ${iso(new Date(w[i][0] * 1000))} / adjusted weekly close ${iso(new Date(w[0][0] * 1000))} − 1 (adjusted for splits and dividends)`, [histCell(sym, i, "a"), histCell(sym, 0, "a")], { kind: "PX", end: iso(new Date(w[i][0] * 1000)), custom: 1 });
        return { path, cellAt };
      }
      const b = PX[sym]?.bars; if (!b) return null;
      const i0 = Math.max(0, idxOnOrAfter(sym, new Date(b.at(-1).t - (CS.trRange === "1Y" ? 365 : 730) * DAY)));
      const tr = HIST[sym] ? totalReturn(sym, i0, b.length - 1) : null;
      const path = tr ? tr.path.map(([t, v], k) => ({ x: t, y: v * 100, i: i0 + k })) : b.slice(i0).map((x, k) => ({ x: x.t, y: x.c / b[i0].c * 100, i: i0 + k }));
      const cellAt = i => { const r = HIST[sym] ? totalReturn(sym, i0, i) : null; if (r) { r.cell.label = `Total return since ${iso(b[i0].t)}`; return r.cell; } return calcCell(sym, `Price return since ${iso(b[i0].t)}`, "pct", b[i].c / b[i0].c - 1, `Close ${iso(b[i].t)} / close ${iso(b[i0].t)} − 1 (dividend history not loaded)`, [pxCell(sym, i), pxCell(sym, i0)], { kind: "PX", end: iso(b[i].t), custom: 1 }); };
      return { path, cellAt };
    }
    function drawTR() {
      const syms = [...tks, "SPY"];
      const S = syms.map(s => [s, trSeries(s)]).filter(x => x[1]);
      const ser = S.map(([s, r]) => ({ name: s === "SPY" ? "SPY (S&P 500)" : s, sym: s, color: s === "SPY" ? css("--ink") : colorOf(s), dash: s === "SPY" ? "3 3" : null, width: s === "SPY" ? 1.2 : 1.7, pts: r.path.map(p => ({ ...p, cell: null })), cellAt: r.cellAt }));
      const click = (c, s, p) => openAny(s.cellAt(p.i));
      xyChart(trHost, ser, { height: 280, base: 100, yfmt: v => v.toFixed(0), tfmt: v => v.toFixed(1), onPoint: click, tol: 10 * DAY });
      const dd = ser.map(s => { let pk = -Infinity; return { ...s, pts: s.pts.map(p => { pk = Math.max(pk, p.y); return { ...p, y: p.y / pk - 1 }; }) }; });
      xyChart(ddHost, dd, { height: 150, area: true, yfmt: v => fpct(v, 0), tfmt: v => fpct(v), legend: false, onPoint: (c, s, p) => { const d = ser.find(z => z.sym === s.sym); const k = d.pts.findIndex(z => z.i === p.i); let pi = 0; for (let j = 0; j <= k; j++) if (d.pts[j].y >= d.pts[pi].y) pi = j; const a = d.cellAt(d.pts[pi].i), b2 = d.cellAt(p.i); openAny(calcCell(s.sym, "Drawdown from running high", "pct", p.y, `Total-return index at ${iso(p.x)} / its running high at ${iso(d.pts[pi].x)} − 1`, [b2, a], { kind: "PX", end: iso(p.x), custom: 1 })); }, tol: 10 * DAY });
    }
    // 3. rolling correlation
    const pairsAll = []; tks.forEach((a, i) => tks.slice(i + 1).forEach(b => pairsAll.push([a, b])));
    if (!CS.pairs || CS.pairs.some(([a, b]) => !tks.includes(a) || !tks.includes(b))) CS.pairs = (tks.length <= 4 ? pairsAll : pairsAll.filter(p => p[0] === tks[0])).slice(0, 6);
    const pairBox = el("div", { class: "cmp-legend", style: "margin:0 16px 4px" });
    const cHost = el("div", { class: "bd" });
    body.append(panel("Rolling correlation of daily returns", seg([["63", "3 months"], ["126", "6 months"], ["252", "1 year"]], String(CS.corrWin), v => { CS.corrWin = +v; drawC(); }), pairBox, cHost, el("div", { class: "src" }, "Pearson correlation of daily close-to-close returns over a trailing window, on dates both stocks traded. Pick pairs above; click a line for the window behind the value.")));
    const palette = d3.schemeTableau10;
    pairsAll.forEach(([a, b], k) => { const on = CS.pairs.some(p => p[0] === a && p[1] === b); const cb = el("input", { type: "checkbox" }); cb.checked = on; cb.onchange = () => { CS.pairs = cb.checked ? [...CS.pairs, [a, b]] : CS.pairs.filter(p => !(p[0] === a && p[1] === b)); drawC(); }; pairBox.append(el("label", {}, cb, el("i", { style: `background:${palette[k % 10]}` }), `${a} · ${b}`)); });
    if (!pairsAll.length) pairBox.append(el("span", { class: "faint" }, "Add a second company to see correlations."));
    function drawC() {
      const w = CS.corrWin;
      const ser = CS.pairs.map(([a, b]) => {
        const k = pairsAll.findIndex(p => p[0] === a && p[1] === b);
        const A = PX[a]?.bars, B = PX[b]?.bars; if (!A || !B) return null;
        const mb = new Map(B.map((x, j) => [iso(x.t), j]));
        const al = A.map((x, i) => [i, mb.get(iso(x.t))]).filter(p => p[1] != null);
        const ra = [], rb = [], idx = [];
        for (let j = 1; j < al.length; j++) { ra.push(A[al[j][0]].c / A[al[j - 1][0]].c - 1); rb.push(B[al[j][1]].c / B[al[j - 1][1]].c - 1); idx.push(al[j]); }
        const pts = [];
        for (let j = w - 1; j < ra.length; j++) {
          const rho = corr(ra.slice(j - w + 1, j + 1), rb.slice(j - w + 1, j + 1));
          const [ia, ib] = idx[j], [sa, sb] = idx[j - w + 1];
          pts.push({ x: A[ia].t, y: rho, mk: () => calcCell(`${a}`, `Correlation with ${b}, ${w} sessions`, "num", rho, `Pearson correlation of ${w} daily returns of ${a} and ${b}, ${iso(A[sa].t)} to ${iso(A[ia].t)}`, [pxCell(a, sa), pxCell(a, ia), pxCell(b, sb), pxCell(b, ib)], { kind: "PX", end: iso(A[ia].t), custom: 1 }) });
        }
        return { name: `${a} · ${b}`, color: palette[k % 10], pts };
      }).filter(Boolean);
      xyChart(cHost, ser, { height: 240, yfmt: v => v.toFixed(1), tfmt: v => v.toFixed(2), onPoint: (c, s, p) => openAny(p.mk()), tol: 10 * DAY, empty: "Pick at least one pair" });
    }
    // 4. scatter of two metrics against the S&P 500
    scatterPanel(body);
    requestAnimationFrame(() => { drawM(); drawTR(); drawC(); });
  }
  const SC_FIELDS = [
    ["market_cap", "Market cap", "USD", "mcap"], ["pe_ttm", "P/E TTM", "x", "pe"], ["ps_ttm", "P/S TTM", "x", "ps"], ["fcf_yield", "FCF yield TTM", "pct", "fcfy"],
    ["revenue_ttm", "Revenue TTM", "USD", null], ["net_income_ttm", "Net income TTM", "USD", null], ["rev_growth_lq", "Revenue growth YoY, latest quarter", "pct", "revg"],
    ["gross_margin_lq", "Gross margin, latest quarter", "pct", "gm"], ["op_margin_lq", "Operating margin, latest quarter", "pct", "om"], ["net_margin_ttm", "Net margin TTM", "pct", "nm"],
    ["dividend_yield", "Dividend yield", "pct", "divy"], ["buyback_yield", "Buyback yield", "pct", "bby"], ["beta_1y", "Beta 1Y", "num", "beta"], ["vol_60d", "Volatility 60D", "pct", "vol60"],
    ["ret_1m", "1M return", "pct", "r1m"], ["ret_ytd", "YTD return", "pct", "rytd"], ["ret_1y", "1Y return", "pct", "r1y"],
  ];
  function scFields() {
    const known = new Set(SC_FIELDS.map(f => f[0]));
    const extra = [];
    if (UNI && UNI.src.startsWith("screener")) {
      const sample = UNI.rows.slice(0, 50);
      Object.keys(UNI.rows[0] || {}).forEach(k => { if (known.has(k) || ["spark", "price", "chg_1d", "high_52w", "low_52w", "insider_net_90d", "eps_ttm"].includes(k)) return; if (sample.filter(r => typeof r[k] === "number").length >= 25) { const row = Object.values(ROW).find(r => r.spx.includes(k)); extra.push([k, row ? row.label : k.replace(/_/g, " "), row ? row.unit : /margin|yield|growth|ret|vol|pct|ro[aei]/.test(k) ? "pct" : "num", row ? row.key : null]); } });
    }
    return SC_FIELDS.concat(extra).filter(f => UNI?.rows.some(r => r[f[0]] != null));
  }
  function scatterPanel(body) {
    const fields = scFields();
    if (!fields.find(f => f[0] === CS.sx)) CS.sx = "pe_ttm";
    if (!fields.find(f => f[0] === CS.sy)) CS.sy = "rev_growth_lq";
    const mk = (cur, on) => el("select", { class: "btn", onchange: e => on(e.target.value) }, fields.map(f => { const o = el("option", { value: f[0] }, f[1]); if (f[0] === cur) o.selected = true; return o; }));
    const host = el("div", { class: "bd" });
    body.append(panel("Scatter against the S&P 500", el("div", { class: "cmp-tools" }, el("span", { class: "muted", style: "font-size:12px" }, "x"), mk(CS.sx, v => { CS.sx = v; draw(); }), el("span", { class: "muted", style: "font-size:12px" }, "y"), mk(CS.sy, v => { CS.sy = v; draw(); })), host,
      el("div", { class: "src" }, `Faint points: every S&P 500 company from ${UNI?.src || "index.json"}. Highlighted: this comparison, using the matrix value where the field has a matrix row (click for its source). Axes trim the outer 2% of values; trimmed points sit on the edge. Click a faint point to add that company.`)));
    function draw() {
      host.innerHTML = "";
      const fx_ = fields.find(f => f[0] === CS.sx), fy_ = fields.find(f => f[0] === CS.sy);
      const rows = UNI.rows.filter(r => r[fx_[0]] != null && r[fy_[0]] != null && isFinite(r[fx_[0]]) && isFinite(r[fy_[0]]));
      const W = host.clientWidth || 600, H = 390, m = { t: 24, r: 16, b: 34, l: 56 };
      const logX = fx_[2] === "USD", logY = fy_[2] === "USD";
      const dom = (vals, lg) => { const v = vals.filter(x => !lg || x > 0); return [quant(v, .02), quant(v, .98)]; };
      const xv = rows.map(r => +r[fx_[0]]), yv = rows.map(r => +r[fy_[0]]);
      const sel = STATE.cmp.map(tk => { const cx = ctx(tk); const cX = fx_[3] && cx.cells[fx_[3]], cY = fy_[3] && cx.cells[fy_[3]]; const u = UNI.by[tk] || {}; return { tk, x: cX ? cX.v : u[fx_[0]], y: cY ? cY.v : u[fy_[0]], cX, cY }; }).filter(p => p.x != null && p.y != null);
      let [x0, x1] = dom(xv.concat(sel.map(p => p.x)), logX), [y0, y1] = dom(yv.concat(sel.map(p => p.y)), logY);
      sel.forEach(p => { x0 = Math.min(x0, p.x); x1 = Math.max(x1, p.x); y0 = Math.min(y0, p.y); y1 = Math.max(y1, p.y); });
      const x = (logX ? d3.scaleLog().domain([Math.max(x0, 1), x1]) : d3.scaleLinear().domain([x0, x1]).nice()).range([m.l, W - m.r]).clamp(true);
      const y = (logY ? d3.scaleLog().domain([Math.max(y0, 1), y1]) : d3.scaleLinear().domain([y0, y1]).nice()).range([H - m.b, m.t]).clamp(true);
      const f = u => u === "pct" ? v => fpct(v, 0) : u === "x" ? v => v.toFixed(0) + "×" : u === "USD" ? v => fmtMoney(v) : v => v.toFixed(1);
      const svg = d3.select(host).append("svg").attr("viewBox", `0 0 ${W} ${H}`).attr("height", H);
      svg.append("g").attr("class", "grid").attr("transform", `translate(${m.l},0)`).call(d3.axisLeft(y).ticks(6, logY ? "~s" : undefined).tickFormat(f(fy_[2])).tickSize(-(W - m.l - m.r))).call(g => g.select(".domain").remove());
      svg.append("g").attr("transform", `translate(0,${H - m.b})`).call(d3.axisBottom(x).ticks(Math.max(3, Math.floor(W / 90))).tickFormat(f(fx_[2])));
      svg.append("text").attr("x", W - m.r).attr("y", H - 2).attr("text-anchor", "end").text(fx_[1]);
      svg.append("text").attr("x", 4).attr("y", 11).text("↑ " + fy_[1]);
      const mx = median(xv), my = median(yv);
      if (mx != null) svg.append("line").attr("x1", x(mx)).attr("x2", x(mx)).attr("y1", m.t).attr("y2", H - m.b).attr("stroke", css("--faint")).attr("stroke-dasharray", "3 3");
      if (my != null) svg.append("line").attr("x1", m.l).attr("x2", W - m.r).attr("y1", y(my)).attr("y2", y(my)).attr("stroke", css("--faint")).attr("stroke-dasharray", "3 3");
      svg.selectAll(null).data(rows.filter(r => !STATE.cmp.includes(r.ticker))).join("circle").attr("cx", r => x(+r[fx_[0]])).attr("cy", r => y(+r[fy_[0]])).attr("r", 2.6).attr("fill", css("--faint")).attr("opacity", .35).style("cursor", "pointer")
        .on("mousemove", (ev, r) => showTip(ev, `<b>${esc(r.ticker)}</b> ${esc(r.name || "")}<br>${esc(fx_[1])} ${fmtV(+r[fx_[0]], fx_[2])}<br>${esc(fy_[1])} ${fmtV(+r[fy_[0]], fy_[2])}<div style="opacity:.7;margin-top:2px">Click to add to the comparison</div>`))
        .on("mouseleave", hideTip).on("click", (ev, r) => { hideTip(); addTicker(r.ticker); });
      const g = svg.selectAll(null).data(sel).join("g").attr("transform", p => `translate(${x(p.x)},${y(p.y)})`).style("cursor", "pointer")
        .on("mousemove", (ev, p) => showTip(ev, `<b>${p.tk}</b> ${esc(short(p.tk))}<br>${esc(fx_[1])} ${fmtV(p.x, fx_[2])}<br>${esc(fy_[1])} ${fmtV(p.y, fy_[2])}<div style="opacity:.7;margin-top:2px">Click for the source of ${p.cY ? "the y value" : p.cX ? "the x value" : "these values"}</div>`))
        .on("mouseleave", hideTip).on("click", (ev, p) => { hideTip(); const row = ROW[fy_[3]] || ROW[fx_[3]]; if (p.cY || p.cX) openAny(p.cY || p.cX); else { const st = { field: fy_[0], pairs: rows.map(r => [r.ticker, +r[fy_[0]]]), v: my }; openAny({ uni: { row: row || { label: fy_[1], unit: fy_[2] }, stat: st } }); } });
      g.append("circle").attr("r", 6).attr("fill", p => colorOf(p.tk)).attr("stroke", css("--surface")).attr("stroke-width", 1.5);
      g.append("text").attr("x", 9).attr("y", 4).text(p => p.tk).style("fill", css("--ink")).style("font-weight", 600).style("font-size", "11px");
      host.append(el("div", { class: "cmp-legend" }, el("span", {}, `Dashed lines: S&P 500 medians (${fmtV(mx, fx_[2])}, ${fmtV(my, fy_[2])}) of ${rows.length} companies`)));
    }
    requestAnimationFrame(draw);
  }

  /* ---------- relative valuation: each multiple against its own five years and the set ---------- */
  const MULTS = [["pe", "P/E TTM"], ["ps", "P/S TTM"], ["evebit", "EV / EBIT TTM"], ["pfcf", "P / FCF TTM"], ["pb", "Price / book"]];
  const VH = {};
  function valHistory(tk) {
    const h = HIST[tk], m = model(tk);
    const key = tk + "|" + (h?.retrieved_at || "") + "|" + (D.companies[tk]?.generated || "");
    if (VH[tk]?.key === key) return VH[tk];
    const out = { key, pe: [], ps: [], evebit: [], pfcf: [], pb: [] };
    VH[tk] = out;
    if (!h?.w?.length) return out;
    const filed = c => c?.rec?.filed || c?.calc?.inputs?.reduce((a, i) => (filed(i) > a ? filed(i) : a), "") || "";
    const qs = l => m.series(l, "Q").map(x => ({ end: x.end, cell: x.cell, filed: filed(x.cell) }));
    const S = Object.fromEntries(["Revenue", "Net income", "Operating income", "Operating cash flow", "Capex"].map(l => [l, qs(l)]));
    const shares = m.series("Diluted shares", "Q").filter(x => !x.cell.derived && x.cell.v > 0).concat(m.series("Diluted shares", "A").filter(x => x.cell.v > 0)).map(x => ({ end: x.end, cell: x.cell, filed: filed(x.cell) })).sort((a, b) => a.end.localeCompare(b.end));
    const balL = l => { const r = m.byLabel[l]; if (!r) return []; return [...r.Q.values(), ...r.A.values()].map(c => ({ end: c.end, cell: c, filed: filed(c) })).sort((a, b) => a.end.localeCompare(b.end)); };
    const B = { "Long-term debt": balL("Long-term debt"), "Shareholders' equity": balL("Shareholders' equity"), "Cash & equivalents": balL("Cash & equivalents"), "Marketable securities, current": balL("Marketable securities, current") };
    const asOf = (arr, d) => { let r = null; for (const x of arr) if (x.filed && x.filed <= d && (!r || x.end >= r.end)) r = x; return r && dayDiff(r.end, d) < 200 ? r : null; };
    const ttm = (l, d) => {
      const q = S[l].filter(x => x.filed && x.filed <= d).slice(-4);
      if (q.length < 4 || dayDiff(q[0].end, q[3].end) > 300 || dayDiff(q[3].end, d) > 200) return null;
      return { v: q.reduce((a, x) => a + x.cell.v, 0), tk, label: l, unit: "USD", kind: "TTM", end: q[3].end, calc: { formula: `Sum of the last four quarters of ${l} filed by ${d}`, inputs: q.map(x => x.cell) } };
    };
    const splits = h.split || [];
    h.w.forEach((w, i) => {
      const d = iso(new Date((w[0] + 4 * 86400) * 1000));
      const sh = asOf(shares, d); if (!sh) return;
      // closes are on today's share basis; restate the filed share count to it with the splits made after it was filed
      const f = splits.filter(s => iso(new Date(s[0] * 1000)) > sh.filed).reduce((a, s) => a * s[1] / s[2], 1);
      const mcap = w[1] * sh.cell.v * f;
      const hc = histCell(tk, i);
      const base = `Weekly close (split-adjusted) × diluted shares${f !== 1 ? ` × ${+f.toFixed(4)} (splits after those shares were filed)` : ""}`;
      const mk = (k, label, v, formula, inputs) => { if (v != null && isFinite(v) && v > 0) out[k].push({ t: new Date(w[0] * 1000), v, i, cell: () => calcCell(tk, `${label}, week of ${iso(new Date(w[0] * 1000))}`, "x", v, formula, [hc, sh.cell, ...inputs], { kind: "PX", end: d, custom: 1 }) }); };
      const ni = ttm("Net income", d), rv = ttm("Revenue", d), oi = ttm("Operating income", d), o = ttm("Operating cash flow", d), cx = ttm("Capex", d);
      if (ni && ni.v > 0) mk("pe", "P/E TTM", mcap / ni.v, `${base} / TTM net income filed by ${d}`, [ni]);
      if (rv && rv.v > 0) mk("ps", "P/S TTM", mcap / rv.v, `${base} / TTM revenue filed by ${d}`, [rv]);
      if (o && cx && o.v - cx.v > 0) mk("pfcf", "P / FCF TTM", mcap / (o.v - cx.v), `${base} / (TTM operating cash flow − TTM capex) filed by ${d}`, [o, cx]);
      const eq = asOf(B["Shareholders' equity"], d);
      if (eq && eq.cell.v > 0) mk("pb", "Price / book", mcap / eq.cell.v, `${base} / shareholders' equity filed by ${d}`, [eq.cell]);
      const ce = asOf(B["Cash & equivalents"], d);
      if (oi && oi.v > 0 && ce) {
        const ld = asOf(B["Long-term debt"], d), ms = asOf(B["Marketable securities, current"], d);
        const msOk = ms && ms.end === ce.end ? ms : null, ldOk = ld && ld.end === ce.end ? ld : null;
        mk("evebit", "EV / EBIT TTM", (mcap + (ldOk?.cell.v || 0) - ce.cell.v - (msOk?.cell.v || 0)) / oi.v, `(${base} + long-term debt − cash & current marketable securities) / TTM operating income, filed by ${d}`, [ldOk?.cell, ce.cell, msOk?.cell, oi]);
      }
    });
    return out;
  }
  function currentMult(tk, k) {
    const cx = ctx(tk);
    if (k === "pfcf") { const mc = cx.cells.mcap, f = cx.ttm.fcf; return mc && f && f.v > 0 ? calcCell(tk, "P / FCF TTM", "x", mc.v / f.v, "Market cap / TTM free cash flow", [mc, f], { kind: "PX", end: mc.end, custom: 1 }) : null; }
    return cx.cells[k] || null;
  }
  function tabValuation(body) {
    const tks = STATE.cmp;
    const stats = {};
    tks.forEach(tk => { const vh = valHistory(tk); stats[tk] = {}; MULTS.forEach(([k]) => { const arr = vh[k]; const vals = arr.map(p => p.v); const cur = currentMult(tk, k); stats[tk][k] = { arr, cur, n: vals.length, min: arr.length ? arr.reduce((a, p) => p.v < a.v ? p : a) : null, max: arr.length ? arr.reduce((a, p) => p.v > a.v ? p : a) : null, p25: quant(vals, .25), med: quant(vals, .5), p75: quant(vals, .75), medP: arr.length ? arr.reduce((a, p) => Math.abs(p.v - quant(vals, .5)) < Math.abs(a.v - quant(vals, .5)) ? p : a) : null, pct: cur && vals.length >= 26 ? vals.filter(v => v < cur.v).length / vals.length : null }; }); });
    const setMed = Object.fromEntries(MULTS.map(([k]) => [k, median(tks.map(tk => stats[tk][k].cur?.v))]));
    // summary table
    const t = el("table", { class: "cmpx" });
    t.append(el("thead", {}, el("tr", {}, el("th", { class: "lab" }, "Company"), MULTS.map(([k, n]) => el("th", { title: "Current value · where it sits in its own five-year weekly range · difference from the set median" }, n)))));
    const tb = el("tbody");
    tks.forEach(tk => {
      const tr = el("tr", {}, el("td", { class: "lab" }, el("span", { class: "lw" }, el("i", { class: "dot", style: `background:${colorOf(tk)}` }), el("b", { class: "mono" }, tk), el("span", { class: "muted" }, short(tk)))));
      MULTS.forEach(([k, n]) => {
        const s = stats[tk][k];
        if (!s.cur) { tr.append(el("td", { class: "na", title: "Not meaningful: negative or missing earnings, cash flow or equity" }, "n/m")); return; }
        const pctCell = s.pct != null ? calcCell(tk, `${n}, percentile in own 5-year range`, "pct", s.pct, `Share of ${s.n} weekly values from ${iso(s.arr[0].t)} to ${iso(s.arr.at(-1).t)} below the current ${fmtV(s.cur.v, "x")}`, [s.cur, s.min?.cell(), s.medP?.cell(), s.max?.cell()], { kind: "PX", end: s.cur.end, custom: 1 }) : null;
        const rel = setMed[k] && tks.length > 1 ? calcCell(tk, `${n} vs set median`, "pct", s.cur.v / setMed[k] - 1, `${n} / median ${n} of this comparison − 1`, [s.cur, calcCell("SET", `${n}, set median`, "x", setMed[k], `Median of ${tks.filter(x => stats[x][k].cur).length} companies`, tks.map(x => stats[x][k].cur), { kind: "PX", custom: 1, set: true })], { kind: "PX", end: s.cur.end, custom: 1 }) : null;
        const td = el("td", {});
        const b = (c, txt, cl) => el("button", { class: "chip link " + (cl || ""), style: "margin-left:4px", onclick: () => openAny(c), title: "Open the calculation" }, txt);
        td.append(b(s.cur, fmtV(s.cur.v, "x"), "acc mono"));
        if (pctCell) td.append(el("span", { class: "sub2" }, b(pctCell, `${ordinal(Math.round(s.pct * 100))} pct of 5y`), rel ? b(rel, `${fsgn(rel.v, 0)} vs set`, cls(-rel.v)) : null));
        else if (rel) td.append(el("span", { class: "sub2" }, b(rel, `${fsgn(rel.v, 0)} vs set`, cls(-rel.v))));
        tr.append(td);
      });
      tb.append(tr);
    });
    t.append(tb);
    body.append(panel("Multiples now, against five years and the set", null, el("div", { class: "cmp-wrap" }, t), el("div", { class: "src", style: "padding-top:10px" }, "Now: the matrix values (latest close). Five-year history: each week's split-adjusted close times the latest diluted share count filed by that week (restated for any later split), over TTM figures from the filings available that week (point in time, no later restatements). n/m: negative or missing denominator.")));
    // band chart
    const host = el("div", { class: "bd cmp-band" });
    const pick = seg(MULTS, CS.vmult, v => { CS.vmult = v; drawB(); });
    body.append(panel("Five-year range by company", pick, host, el("div", { class: "src" }, "Line: five-year low to high. Box: 25th to 75th percentile. Tick: median. Dot: now. Dashed: median of this comparison now. Log scale. Click the dot, the low, the median or the high for its calculation.")));
    function drawB() {
      host.innerHTML = "";
      const k = CS.vmult, rows = tks.map(tk => ({ tk, s: stats[tk][k] })).filter(r => r.s.n || r.s.cur);
      if (!rows.length) { host.append(el("div", { class: "empty" }, "No company in this set has a meaningful value for this multiple.")); return; }
      const vals = rows.flatMap(r => [r.s.min?.v, r.s.max?.v, r.s.cur?.v]).filter(v => v > 0);
      let lo = Math.min(...vals), hi = Math.max(...vals);
      hi = Math.min(hi, lo * 400);
      const W = host.clientWidth || 640, rh = 46, m = { t: 22, r: 24, b: 26, l: innerWidth < 860 ? 56 : 120 }, H = m.t + m.b + rows.length * rh;
      const x = d3.scaleLog().domain([lo / 1.15, hi * 1.15]).range([m.l, W - m.r]).clamp(true);
      const svg = d3.select(host).append("svg").attr("viewBox", `0 0 ${W} ${H}`).attr("height", H);
      svg.append("g").attr("class", "grid").attr("transform", `translate(0,${H - m.b})`).call(d3.axisBottom(x).ticks(6, "~g").tickSize(-(H - m.t - m.b)).tickFormat(v => (+v.toPrecision(2)) + "×")).call(g => g.select(".domain").remove());
      if (setMed[k]) { svg.append("line").attr("x1", x(setMed[k])).attr("x2", x(setMed[k])).attr("y1", m.t - 4).attr("y2", H - m.b).attr("stroke", css("--ink")).attr("stroke-dasharray", "4 3").attr("opacity", .55); svg.append("text").attr("x", x(setMed[k]) + 4).attr("y", 10).text(`set median ${fmtV(setMed[k], "x")}`); }
      rows.forEach(({ tk, s }, j) => {
        const cy = m.t + j * rh + rh / 2, col = colorOf(tk);
        const g = svg.append("g");
        g.append("text").attr("x", 4).attr("y", cy + 4).text(innerWidth < 860 ? tk : `${tk} · ${short(tk)}`.slice(0, 20)).style("fill", css("--ink")).style("font-weight", 600);
        const hit = (node, c, tipTxt) => node.style("cursor", "pointer").on("mousemove", ev => showTip(ev, tipTxt + '<div style="opacity:.7;margin-top:2px">Click for the calculation</div>')).on("mouseleave", hideTip).on("click", () => { hideTip(); openAny(typeof c === "function" ? c() : c); });
        if (s.n) {
          g.append("line").attr("x1", x(s.min.v)).attr("x2", x(s.max.v)).attr("y1", cy).attr("y2", cy).attr("stroke", col).attr("stroke-width", 1.4).attr("opacity", .7);
          g.append("rect").attr("x", x(s.p25)).attr("width", Math.max(1, x(s.p75) - x(s.p25))).attr("y", cy - 8).attr("height", 16).attr("rx", 2).attr("fill", col).attr("opacity", .18).attr("stroke", col);
          hit(g.append("line").attr("x1", x(s.medP.v)).attr("x2", x(s.medP.v)).attr("y1", cy - 9).attr("y2", cy + 9).attr("stroke", col).attr("stroke-width", 3), s.medP.cell, `${tk} five-year median ${fmtV(s.med, "x")}<br>closest week ${iso(s.medP.t)}: ${fmtV(s.medP.v, "x")}`);
          hit(g.append("circle").attr("cx", x(s.min.v)).attr("cy", cy).attr("r", 4).attr("fill", css("--surface")).attr("stroke", col).attr("stroke-width", 1.5), s.min.cell, `${tk} five-year low ${fmtV(s.min.v, "x")}<br>week of ${iso(s.min.t)}`);
          hit(g.append("circle").attr("cx", x(s.max.v)).attr("cy", cy).attr("r", 4).attr("fill", css("--surface")).attr("stroke", col).attr("stroke-width", 1.5), s.max.cell, `${tk} five-year high ${fmtV(s.max.v, "x")}<br>week of ${iso(s.max.t)}`);
          g.append("text").attr("x", x(s.min.v)).attr("y", cy + 20).attr("text-anchor", "middle").text(fmtV(s.min.v, "x")).style("font-size", "9.5px");
          g.append("text").attr("x", x(s.max.v)).attr("y", cy + 20).attr("text-anchor", "middle").text(fmtV(s.max.v, "x")).style("font-size", "9.5px");
        } else g.append("text").attr("x", m.l).attr("y", cy + 4).text("no five-year history").style("font-size", "10.5px");
        if (s.cur) { hit(g.append("circle").attr("cx", x(s.cur.v)).attr("cy", cy).attr("r", 6.5).attr("fill", col).attr("stroke", css("--surface")).attr("stroke-width", 2), s.cur, `${tk} now ${fmtV(s.cur.v, "x")}${s.pct != null ? `<br>${ordinal(Math.round(s.pct * 100))} percentile of its five years` : ""}`); g.append("text").attr("x", x(s.cur.v)).attr("y", cy - 11).attr("text-anchor", "middle").text(fmtV(s.cur.v, "x")).style("fill", css("--ink")).style("font-weight", 600).style("font-size", "10.5px"); }
      });
      // weekly history lines for the selected multiple
      const lh = el("div", { style: "margin-top:12px" });
      host.append(el("div", { class: "eyebrow", style: "margin-top:10px" }, "Weekly history"), lh);
      xyChart(lh, rows.filter(r => r.s.n).map(({ tk, s }) => ({ name: tk, color: colorOf(tk), pts: s.arr.map(p => ({ x: p.t, y: p.v, mk: p.cell })) })), { height: 220, log: true, yfmt: v => (+v.toPrecision(2)) + "×", tfmt: v => fmtV(v, "x"), onPoint: (c, s, p) => openAny(p.mk()), tol: 10 * DAY });
    }
    requestAnimationFrame(drawB);
  }

  /* ---------- registration ---------- */
  const P = window.FU_PAGES;
  P.compare = arg => render(arg);
  // #compare-AAPL,MSFT reaches the router as page id "compare-AAPL,MSFT"; resolve any such id to this page with its tickers
  if (!P.__cmpProxy) {
    const target = P;
    window.FU_PAGES = new Proxy(target, { get(t, k) { if (k === "__cmpProxy") return true; if (typeof k === "string" && k.startsWith("compare-") && !(k in t)) return () => t.compare(decodeURIComponent(k.slice(8))); return t[k]; } });
  }
})();
