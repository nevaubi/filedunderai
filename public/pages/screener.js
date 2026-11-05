// Screener page. Registers window.FU_PAGES.screener(arg); app.js route() uses it in place of the built-in page when set.
// Data: screener.json (field catalogue + one row per company), screener/src/{T}.json (provenance per field, loaded on
// click), screener/corr.json (pairwise return correlations, loaded for correlation clusters). The agent chat sends the
// request to /api/screen, which returns a screen spec; the spec is validated here and applied to the visible filters.
window.FU_PAGES = window.FU_PAGES || {};
(function () {
  "use strict";
  const SPECLIB = window.FU_SCREENSPEC;
  const SCR = { data: null, rows: [], byTk: {}, fields: {}, order: [], groups: [], corr: null, src: {} };
  const KEY = "scr2";
  const SHARE_PREFIX = "screener=";

  /* ---------- shared screen links: #screener=<spec> ---------- */
  const b64e = s => btoa(unescape(encodeURIComponent(s))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  const b64d = s => decodeURIComponent(escape(atob(s.replace(/-/g, "+").replace(/_/g, "/"))));
  let PENDING = null;
  function takeSharedHash() {
    const h = location.hash.slice(1);
    if (!h.startsWith(SHARE_PREFIX)) return;
    try { PENDING = JSON.parse(b64d(h.slice(SHARE_PREFIX.length))); } catch { PENDING = null; }
    history.replaceState(null, "", location.pathname + location.search + "#screener");
  }
  takeSharedHash();
  // runs before app.js's own hashchange handler (capture listeners on the target fire first)
  addEventListener("hashchange", takeSharedHash, true);

  /* ---------- formatting ---------- */
  const SIGNED = /(_g_|_cagr|_accel|_chg3|^ret_|^rs_|^dist_|^ins_net|^shr_chg|^kpi_.*_yoy$|^std_.*_yoy$|^macd_hist$)/;
  const fieldOf = id => SCR.fields[id];
  function fmtV(id, v) {
    const f = fieldOf(id);
    if (v == null || (typeof v === "number" && !isFinite(v))) return "–";
    if (!f) return String(v);
    switch (f.u) {
      case "usd": return id === "price" ? "$" + fnum(v, 2) : fmtMoney(v);
      case "pct": return SIGNED.test(id) ? fsgn(v) : fpct(v, Math.abs(v) < 0.1 ? 2 : 1);
      case "x": return v.toFixed(Math.abs(v) >= 100 ? 0 : 1) + "×";
      case "pctl": return Math.round(v * 100) + "th";
      case "corr": return (v >= 0 ? "" : "−") + Math.abs(v).toFixed(2);
      case "days": return Math.round(v) + "d";
      case "bool": return v ? "Yes" : "No";
      case "score": return Number.isInteger(v) ? String(v) : v.toFixed(2);
      default: return Math.abs(v) >= 1e6 ? fmtMoney(v).replace("$", "") : Number.isInteger(v) ? v.toLocaleString("en-US") : v.toLocaleString("en-US", { maximumFractionDigits: 2 });
    }
  }
  const colorCls = (id, v) => (SIGNED.test(id) && typeof v === "number") ? cls(v) : "";
  // values typed into filter boxes: pct fields in percent, usd in billions
  const inScale = f => f.u === "pct" ? 100 : f.u === "usd" ? 1e-9 : 1;
  const inUnit = f => f.u === "pct" ? "%" : f.u === "usd" ? "$B" : f.u === "x" ? "×" : f.u === "days" ? "days" : "";
  const toInput = (f, v) => v == null ? "" : +(v * inScale(f)).toPrecision(4);
  const fromInput = (f, s) => s === "" || s == null || !isFinite(+s) ? null : +s / inScale(f);

  /* ---------- data ---------- */
  async function load() {
    if (SCR.data) return;
    const d = await getJSON("screener.json");
    SCR.data = d;
    d.fields.forEach(f => { SCR.fields[f.id] = f; });
    SCR.groups = d.groups;
    SCR.rows = d.rows.map(r => {
      const o = {};
      d.cols.forEach((c, i) => { o[c] = r[i]; });
      d.fields.forEach(f => { if (f.u === "bool" && o[f.id] != null) o[f.id] = !!o[f.id]; });
      o.sp500 = SPECLIB.inIndex(o, "sp500", d.universe);
      o.sp100 = SPECLIB.inIndex(o, "sp100", d.universe);
      return o;
    });
    SCR.rows.forEach(r => { SCR.byTk[r.ticker] = r; });
    SCR.catalogue = { fields: d.fields, sectors: [...new Set(SCR.rows.map(r => r.sector))].sort(), industries: [...new Set(SCR.rows.map(r => r.industry).filter(Boolean))].sort(), tickers: SCR.rows.map(r => r.ticker) };
    SCR.median = {};
    d.fields.forEach(f => { if (f.u !== "bool") SCR.median[f.id] = median(SCR.rows.map(r => r[f.id])); });
  }
  function median(arr) { const a = arr.filter(v => typeof v === "number" && isFinite(v)).sort((x, y) => x - y); if (!a.length) return null; const m = a.length >> 1; return a.length % 2 ? a[m] : (a[m - 1] + a[m]) / 2; }
  const mcapBucket = v => v == null ? "Unknown" : v >= 300e9 ? "Mega (> $300B)" : v >= 60e9 ? "Large ($60B to $300B)" : v >= 20e9 ? "Mid ($20B to $60B)" : "Small (< $20B)";
  const BUCKET_ORDER = ["Mega (> $300B)", "Large ($60B to $300B)", "Mid ($20B to $60B)", "Small (< $20B)", "Unknown"];

  /* ---------- presets ---------- */
  const PRESETS = [
    { id: "quality", n: "Quality compounders", why: "High returns on capital that have held up: ROIC above 15%, gross margin no lower than three years ago, five-year revenue CAGR above 7% and modest leverage. Businesses that reinvest at high rates tend to compound book value.",
      spec: { filters: [{ field: "roic", op: ">", value: 0.15 }, { field: "gm_chg3", op: ">=", value: 0 }, { field: "rev_cagr5", op: ">", value: 0.07 }, { field: "nd_ebitda", op: "<", value: 2 }], sort: { field: "roic", dir: "desc" }, columns: ["roic", "gm", "gm_chg3", "rev_cagr5", "nd_ebitda", "pe", "pe_p5"] } },
    { id: "deepvalue", n: "Deep value, improving margins", why: "Cheapest 25% of each sector on P/E with operating margin up over three years. Low multiples alone often flag decline; rising margins suggest the business is not shrinking into the multiple.",
      spec: { filters: [{ field: "pe", op: "bottom", pct: 25, scope: "sector" }, { field: "om_chg3", op: ">", value: 0 }, { field: "om", op: ">", value: 0 }], sort: { field: "pe_p5", dir: "asc" }, columns: ["pe", "pe_p5", "om", "om_chg3", "rev_g_ttm", "fcf_yield"] } },
    { id: "fcf", n: "High FCF yield, low debt", why: "Free cash flow yield above 6% with net debt under 1.5× EBITDA. Cash the company can return or reinvest, without a balance sheet that absorbs it.",
      spec: { filters: [{ field: "fcf_yield", op: ">", value: 0.06 }, { field: "nd_ebitda", op: "<", value: 1.5 }], sort: { field: "fcf_yield", dir: "desc" }, columns: ["fcf_yield", "nd_ebitda", "fcfm", "fcf_g_ttm", "ev_fcf", "sh_yield"] } },
    { id: "momentum", n: "Momentum leaders above the 200-day", why: "Top fifth of tracked companies on three-month return, trading above the 200-day average and within 10% of the 52-week high. Trend persistence is one of the better documented return patterns.",
      spec: { filters: [{ field: "mom_rank_3m", op: ">", value: 0.8 }, { field: "above_sma200", op: "true" }, { field: "dist_hi52", op: ">", value: -0.1 }], sort: { field: "ret_6m", dir: "desc" }, columns: ["ret_3m", "ret_6m", "mom_rank_3m", "dist_hi52", "dist_sma200", "rs_sec_6m", "rsi14"] } },
    { id: "insiders", n: "Insider cluster buying", why: "Two or more insiders bought shares on the open market in the last 90 days, and purchases exceed sales. Open-market buying by several insiders at once is rarer and more informative than routine selling.",
      spec: { filters: [{ field: "ins_buyers_90d", op: ">=", value: 2 }, { field: "ins_net_90d", op: ">", value: 0 }], sort: { field: "ins_net_90d", dir: "desc" }, columns: ["ins_buyers_90d", "ins_net_90d", "ins_net_1y", "ret_3m", "pe"] } },
    { id: "rateup", n: "Rising-rate beneficiaries", why: "Top 10% by correlation of daily returns with changes in the 10-year yield: names that have traded up when yields rise (often banks and insurers).",
      spec: { filters: [{ field: "c_tnx", op: "top", pct: 10, scope: "all" }], sort: { field: "c_tnx", dir: "desc" }, columns: ["c_tnx", "cm_10y", "c_spy", "beta", "pb", "ret_3m"] } },
    { id: "ratedown", n: "Rate-sensitive (bond proxies)", why: "Bottom 10% by correlation with 10-year yield changes: names that have traded down when yields rise, typically utilities, REITs and long-duration growth.",
      spec: { filters: [{ field: "c_tnx", op: "bottom", pct: 10, scope: "all" }], sort: { field: "c_tnx", dir: "asc" }, columns: ["c_tnx", "cm_10y", "div_yield", "nd_ebitda", "beta", "ret_3m"] } },
    { id: "dollar", n: "Dollar-hurt multinationals", why: "Large companies in the fifth of tracked names whose returns have moved most against the dollar index. A stronger dollar shrinks foreign sales when translated, so these tend to lag when the dollar rallies.",
      spec: { filters: [{ field: "c_dxy", op: "bottom", pct: 20, scope: "all" }, { field: "mcap", op: ">", value: 50e9 }], exclude_sectors: ["Utilities", "Real Estate"], sort: { field: "c_dxy", dir: "asc" }, columns: ["c_dxy", "mcap", "c_spy", "rev_g_ttm", "om"] } },
    { id: "divgrow", n: "Dividend growers", why: "Pays a dividend above 1.5% with five-year EPS growth above 5% and positive free cash flow yield above the dividend's typical cover. Dividends are paid from earnings growth, not leverage.",
      spec: { filters: [{ field: "div_yield", op: ">", value: 0.015 }, { field: "eps_cagr5", op: ">", value: 0.05 }, { field: "fcf_yield", op: ">", value: 0.03 }, { field: "nd_ebitda", op: "<", value: 3 }], sort: { field: "div_yield", dir: "desc" }, columns: ["div_yield", "eps_cagr5", "fcf_yield", "sh_yield", "nd_ebitda", "pio_f"] } },
    { id: "turnaround", n: "Turnarounds", why: "Shares down over a year and operating margin below three years ago, but revenue growth accelerating and operating income up on the year. Early evidence that a decline is reversing.",
      spec: { filters: [{ field: "ret_1y", op: "<", value: 0 }, { field: "om_chg3", op: "<", value: 0 }, { field: "rev_accel", op: ">", value: 0 }, { field: "oi_g_q", op: ">", value: 0 }], sort: { field: "rev_accel", dir: "desc" }, columns: ["ret_1y", "rev_accel", "rev_g_q", "oi_g_q", "om_chg3", "pe_p5"] } },
    { id: "accel", n: "Accelerating growth", why: "Revenue up more than 10% on the year in the latest quarter and the growth rate rising by at least 2 points. Acceleration tends to drive estimate revisions.",
      spec: { filters: [{ field: "rev_g_q", op: ">", value: 0.1 }, { field: "rev_accel", op: ">", value: 0.02 }], sort: { field: "rev_accel", dir: "desc" }, columns: ["rev_g_q", "rev_accel", "rev_streak", "eps_g_q", "om", "ps"] } },
    { id: "netcash", n: "Net cash, high margin", why: "More cash and short-term securities than debt, and operating margin above 25%. Pricing power without balance sheet risk.",
      spec: { filters: [{ field: "net_debt", op: "<", value: 0 }, { field: "om", op: ">", value: 0.25 }], sort: { field: "om", dir: "desc" }, columns: ["net_debt", "om", "gm", "roic", "ev_ebit", "fcf_yield"] } },
    { id: "shyield", n: "Shareholder yield", why: "Dividends plus buybacks above 6% of market cap and the diluted share count down on the year, so the buybacks are not just offsetting stock awards.",
      spec: { filters: [{ field: "sh_yield", op: ">", value: 0.06 }, { field: "shr_chg1", op: "<", value: 0 }], sort: { field: "sh_yield", dir: "desc" }, columns: ["sh_yield", "div_yield", "bb_yield", "shr_chg1", "shr_chg3", "fcf_yield"] } },
    { id: "oversold", n: "Oversold quality", why: "RSI below 35 on companies with ROIC above 12% and a Piotroski score of 6 or more: short-term weakness in businesses whose fundamentals pass.",
      spec: { filters: [{ field: "rsi14", op: "<", value: 35 }, { field: "roic", op: ">", value: 0.12 }, { field: "pio_f", op: ">=", value: 6 }], sort: { field: "rsi14", dir: "asc" }, columns: ["rsi14", "ret_1m", "dist_sma50", "roic", "pio_f", "pe_p5"] } },
    { id: "lowvol", n: "Low-volatility defensives", why: "Lowest fifth of tracked companies on one-year volatility, beta under 0.8 and a dividend. These have historically fallen less in drawdowns.",
      spec: { filters: [{ field: "vol_1y", op: "bottom", pct: 20, scope: "all" }, { field: "beta", op: "<", value: 0.8 }, { field: "div_yield", op: ">", value: 0 }], sort: { field: "vol_1y", dir: "asc" }, columns: ["vol_1y", "beta", "mdd_1y", "div_yield", "c_spy"] } },
    { id: "oil", n: "Oil-linked", why: "Top 10% of tracked companies by correlation of daily returns with WTI crude over the last year: the names an oil move will carry.",
      spec: { filters: [{ field: "c_oil", op: "top", pct: 10, scope: "all" }], sort: { field: "c_oil", dir: "desc" }, columns: ["c_oil", "c_spy", "c_dxy", "fcf_yield", "ret_3m"] } },
    { id: "cheaphist", n: "Cheap vs own history", why: "P/E and EV/Sales both in the bottom fifth of their own five-year monthly range: multiples compressed relative to what the market has paid for this company before.",
      spec: { filters: [{ field: "pe_p5", op: "<", value: 0.2 }, { field: "ev_sales_p5", op: "<", value: 0.2 }], sort: { field: "pe_p5", dir: "asc" }, columns: ["pe", "pe_p5", "ev_sales", "ev_sales_p5", "rev_g_ttm", "om_chg3"] } },
    { id: "strong", n: "Financially strong", why: "Piotroski F-score 8 or 9 and Altman Z above 3: improving profitability, liquidity and efficiency with a low distress score.",
      spec: { filters: [{ field: "pio_f", op: ">=", value: 8 }, { field: "altman_z", op: ">", value: 3 }], sort: { field: "altman_z", dir: "desc" }, columns: ["pio_f", "altman_z", "roa", "current_ratio", "nd_ebitda"] } },
    { id: "earnings", n: "Reporting in the next two weeks", why: "Estimated from each company's recent results 8-K cadence. Dates are estimates; check the company's announcement.",
      spec: { filters: [{ field: "next_earn_days", op: "between", value: [0, 14] }], sort: { field: "next_earn_days", dir: "asc" }, columns: ["next_earn_days", "days_10q", "rev_g_q", "eps_g_q", "ret_1m"] } },
  ];
  const COLSETS = {
    Overview: ["price", "ret_1y", "mcap", "pe", "rev_g_ttm", "om", "fcf_yield", "div_yield"],
    Valuation: ["mcap", "pe", "pe_p5", "ev_ebit", "ev_sales", "ev_fcf", "pb", "fcf_yield", "earn_yield"],
    Growth: ["rev_g_q", "rev_g_ttm", "rev_cagr3", "rev_cagr5", "rev_accel", "eps_g_ttm", "eps_cagr3", "fcf_g_ttm", "oi_g_ttm"],
    Quality: ["gm", "om", "nm", "fcfm", "om_chg3", "roe", "roic", "accruals", "cash_conv", "sbc_pct"],
    Momentum: ["ret_1m", "ret_3m", "ret_6m", "ret_1y", "mom_rank_3m", "dist_hi52", "dist_sma200", "rsi14", "rs_sec_6m"],
    Risk: ["beta", "vol_1y", "mdd_1y", "nd_ebitda", "de", "int_cov", "altman_z", "pio_f", "shr_chg1"],
    Income: ["div_yield", "bb_yield", "sh_yield", "fcf_yield", "shr_chg1", "eps_cagr5", "nd_ebitda"],
    Correlation: ["c_spy", "c_sector", "c_tnx", "c_oil", "c_gold", "c_dxy", "c_btc", "cm_cpi"],
  };
  const SIM_FIELDS = ["pe", "ev_sales", "fcf_yield", "rev_g_ttm", "rev_cagr3", "eps_g_ttm", "gm", "om", "roic", "nd_ebitda", "beta", "vol_1y", "ret_1y", "ret_3m", "c_spy", "c_tnx", "c_oil", "c_dxy", "div_yield", "sbc_pct", "rnd_int"];

  /* ---------- state ---------- */
  const blank = () => ({ ...SPECLIB.empty() });
  // Older compact saved/shared screens omitted their default SP500 selection.
  // New saved screens always include an explicit index, including All tracked.
  const storedSpec = spec => ({ ...spec, index: spec?.index ?? "sp500" });
  function loadState() {
    const s = store.get(KEY, null) || {};
    return { spec: s.spec ? { ...blank(), ...storedSpec(s.spec) } : blank(), cols: s.cols || COLSETS.Overview.slice(), colset: s.colset || "Overview", q: s.q || "", preset: s.preset || null, openGroups: s.openGroups || ["val"], filtersOpen: s.filtersOpen ?? false, chatOpen: s.chatOpen ?? false, shown: 150 };
  }
  let ST = null;
  const save = () => store.set(KEY, { spec: ST.spec, cols: ST.cols, colset: ST.colset, q: ST.q, preset: ST.preset, openGroups: ST.openGroups, filtersOpen: ST.filtersOpen, chatOpen: ST.chatOpen });
  const validate = spec => SPECLIB.validate(spec, SCR.catalogue);

  /* ---------- engine ---------- */
  function universe(spec) {
    return SCR.rows.filter(r => SPECLIB.inIndex(r, spec.index, SCR.data.universe)
      && (!spec.sectors.length || spec.sectors.includes(r.sector)) && !spec.exclude_sectors.includes(r.sector)
      && (!spec.industries.length || spec.industries.includes(r.industry)) && !spec.exclude_industries.includes(r.industry)
      && (!spec.tickers.length || spec.tickers.includes(r.ticker)) && !spec.exclude_tickers.includes(r.ticker));
  }
  function pctPass(rows, f) {
    // top/bottom N%: rank within the universe (scope all) or within each sector / industry
    const key = f.scope === "sector" ? "sector" : f.scope === "industry" ? "industry" : null;
    const groups = new Map();
    rows.forEach(r => { const v = r[f.field]; if (typeof v !== "number") return; const g = key ? r[key] : "_"; if (!groups.has(g)) groups.set(g, []); groups.get(g).push(r); });
    const pass = new Set();
    groups.forEach(list => {
      list.sort((a, b) => a[f.field] - b[f.field]);
      const n = Math.max(1, Math.round(list.length * f.pct / 100));
      (f.op === "top" ? list.slice(-n) : list.slice(0, n)).forEach(r => pass.add(r.ticker));
    });
    return pass;
  }
  function testFilter(f, v) {
    switch (f.op) {
      case "true": return v === true;
      case "false": return v === false;
      case "exists": return v != null;
    }
    if (typeof v !== "number") return false;
    switch (f.op) {
      case ">": return v > f.value; case ">=": return v >= f.value; case "<": return v < f.value; case "<=": return v <= f.value;
      case "=": return Math.abs(v - f.value) < 1e-9; case "!=": return Math.abs(v - f.value) >= 1e-9;
      case "between": return (f.value[0] == null || v >= f.value[0]) && (f.value[1] == null || v <= f.value[1]);
    }
    return true;
  }
  function zscorer(fields, rows) {
    const st = {};
    fields.forEach(id => {
      const f = fieldOf(id); if (!f) return;
      const tr = v => id === "mcap" ? Math.log(v) : v;
      const lo = f.q ? tr(f.q[0]) : null, hi = f.q ? tr(f.q[2]) : null;
      const clip = v => v == null || typeof v !== "number" || (id === "mcap" && v <= 0) ? null : Math.min(hi ?? Infinity, Math.max(lo ?? -Infinity, tr(v)));
      const vals = rows.map(r => clip(r[id])).filter(v => v != null);
      if (vals.length < 20) return;
      const m = vals.reduce((a, b) => a + b, 0) / vals.length;
      const sdv = Math.sqrt(vals.reduce((a, b) => a + (b - m) ** 2, 0) / vals.length) || 1;
      st[id] = v => { const c = clip(v); return c == null ? null : (c - m) / sdv; };
    });
    return st;
  }
  function similarity(target, rows, fields) {
    const z = zscorer(fields, SCR.rows);
    const ids = Object.keys(z);
    const t = SCR.byTk[target];
    const tv = ids.map(id => z[id](t[id]));
    const out = new Map();
    rows.forEach(r => {
      let s = 0, n = 0;
      ids.forEach((id, k) => { const a = tv[k], b = z[id](r[id]); if (a != null && b != null) { s += (a - b) ** 2; n++; } });
      if (n >= Math.min(8, ids.length)) out.set(r.ticker, Math.sqrt(s / n));
    });
    return out;
  }
  function run(spec, q) {
    const uni = universe(spec);
    let rows = uni;
    const pass = new Map();
    spec.filters.forEach((f, i) => { if (f.op === "top" || f.op === "bottom") pass.set(i, pctPass(uni, f)); });
    rows = rows.filter(r => spec.filters.every((f, i) => pass.has(i) ? pass.get(i).has(r.ticker) : testFilter(f, r[f.field])));
    const qq = (q || "").trim().toLowerCase();
    if (qq) rows = rows.filter(r => r.ticker.toLowerCase().includes(qq) || r.name.toLowerCase().includes(qq) || (r.industry || "").toLowerCase().includes(qq));
    let sim = null;
    if (spec.similar_to && SCR.byTk[spec.similar_to.ticker]) {
      sim = similarity(spec.similar_to.ticker, rows, spec.similar_to.fields || SIM_FIELDS);
      rows = rows.filter(r => sim.has(r.ticker) && r.ticker !== spec.similar_to.ticker).sort((a, b) => sim.get(a.ticker) - sim.get(b.ticker)).slice(0, spec.similar_to.n);
    }
    const s = spec.sort;
    if (s && s.field === "similarity" && sim) rows.sort((a, b) => sim.get(a.ticker) - sim.get(b.ticker));
    else if (s && s.field) {
      const dir = s.dir === "asc" ? 1 : -1;
      rows = rows.slice().sort((a, b) => { const x = a[s.field], y = b[s.field]; if (x == null && y == null) return 0; if (x == null) return 1; if (y == null) return -1; return typeof x === "string" ? dir * x.localeCompare(y) : dir * (x - y); });
    }
    if (spec.limit) rows = rows.slice(0, spec.limit);
    return { rows, uni, sim };
  }

  /* ---------- describing filters ---------- */
  const OPTXT = { ">": ">", ">=": "≥", "<": "<", "<=": "≤", "=": "=", "!=": "≠" };
  function filterText(f) {
    const fd = fieldOf(f.field); const n = fd ? fd.n : f.field;
    if (f.op === "true") return fd?.u === "bool" ? n : `${n}: yes`;
    if (f.op === "false") return `Not: ${n.toLowerCase()}`;
    if (f.op === "exists") return `${n} reported`;
    if (f.op === "top" || f.op === "bottom") return `${n}: ${f.op} ${f.pct}%${f.scope === "sector" ? " of sector" : f.scope === "industry" ? " of industry" : ""}`;
    if (f.op === "between") return `${n} ${f.value[0] != null ? fmtV(f.field, f.value[0]) : "…"} to ${f.value[1] != null ? fmtV(f.field, f.value[1]) : "…"}`;
    return `${n} ${OPTXT[f.op] || f.op} ${fmtV(f.field, f.value)}`;
  }
  function specDiff(a, b) {
    const k = f => JSON.stringify([f.field, f.op, f.value, f.pct, f.scope]);
    const A = new Set(a.filters.map(k)), B = new Set(b.filters.map(k));
    const out = [];
    b.filters.forEach(f => { if (!A.has(k(f))) out.push(["+", filterText(f)]); });
    a.filters.forEach(f => { if (!B.has(k(f))) out.push(["−", filterText(f)]); });
    for (const [key, lab] of [["sectors", "Sector"], ["exclude_sectors", "Excluded sector"], ["industries", "Industry"], ["exclude_industries", "Excluded industry"], ["tickers", "Ticker"], ["exclude_tickers", "Excluded ticker"]]) {
      b[key].filter(x => !a[key].includes(x)).forEach(x => out.push(["+", `${lab}: ${x}`]));
      a[key].filter(x => !b[key].includes(x)).forEach(x => out.push(["−", `${lab}: ${x}`]));
    }
    if (a.index !== b.index) out.push(["~", `Universe: ${SPECLIB.INDEX_LABELS[b.index]}`]);
    if (JSON.stringify(a.similar_to) !== JSON.stringify(b.similar_to)) out.push([b.similar_to ? "+" : "−", b.similar_to ? `Similar to ${b.similar_to.ticker} (${b.similar_to.n} nearest)` : `Similar to ${a.similar_to.ticker}`]);
    if (JSON.stringify(a.sort) !== JSON.stringify(b.sort) && b.sort) out.push(["~", `Sort: ${b.sort.field === "similarity" ? "similarity" : fieldOf(b.sort.field)?.n || b.sort.field} ${b.sort.dir === "asc" ? "ascending" : "descending"}`]);
    if (a.group_by !== b.group_by) out.push(["~", `Group: ${b.group_by ? groupLabel(b.group_by) : "none"}`]);
    if (a.limit !== b.limit && b.limit) out.push(["~", `Limit: ${b.limit}`]);
    return out;
  }
  const groupLabel = g => g === "sector" ? "sector" : g === "industry" ? "industry" : g === "mcap_bucket" ? "market-cap bucket" : g === "corr_cluster" ? "correlation cluster" : g?.startsWith("quintile:") ? `${fieldOf(g.slice(9))?.n || g.slice(9)} quintile` : "none";

  /* ---------- page ---------- */
  async function page_(arg) {
    activePopover?.close({ restoreFocus: false });
    await load();
    ST = loadState();
    if (PENDING) {
      const v = validate(storedSpec(PENDING)); PENDING = null;
      ST.spec = v.spec; ST.preset = null; if (v.spec.columns.length) { ST.cols = v.spec.columns.slice(); ST.colset = "Custom"; }
      if (v.errors.length) toast("Some parts of the shared screen were not recognized");
      save();
    }
    injectStyle();
    const d = SCR.data;
    page.append(el("div", { class: "ph" }, el("div", {}, el("div", { class: "eyebrow" }, "Screener"), el("h1", {}, "Tracked companies"),
      el("div", { class: "sub" }, `${SCR.rows.length} companies · ${d.fields.length} fields · Prices as of ${fdate(d.asof.prices)}`))));
    const grid = el("div", { class: "sx-grid" + (ST.chatOpen ? "" : " nochat") });
    const main = el("div", { class: "sx-main" });
    const aside = el("aside", { class: "sx-chat panel" });
    grid.append(main, aside);
    page.append(grid);
    SCR.ui = { grid, main, aside };
    buildMain(main);
    buildChat(aside);
    render();
  }

  function setSpec(spec, opts = {}) {
    const v = validate(spec);
    ST.spec = v.spec;
    if (opts.columns !== false && v.spec.columns.length) { ST.cols = v.spec.columns.slice(); ST.colset = "Custom"; }
    if (!opts.keepPreset) ST.preset = null;
    ST.shown = 150;
    save();
    render();
    return v;
  }

  /* ---------- main column ---------- */
  function buildMain(main) {
    const U = SCR.ui;
    U.presets = el("select", { class: "btn sx-presets", "aria-label": "Preset screen", onchange: () => {
      const p = PRESETS.find(p => p.id === U.presets.value);
      if (p) { setSpec({ ...blank(), ...p.spec }); ST.preset = p.id; }
      else ST.preset = null;
      save(); renderPresets();
    } }, el("option", { value: "" }, "Custom screen"), PRESETS.map(p => el("option", { value: p.id }, p.n)));
    U.presetWhy = el("div", { class: "sx-why", hidden: true });
    // toolbar
    U.q = el("input", { type: "search", placeholder: "Filter by ticker, name or industry", value: ST.q, "aria-label": "Filter by ticker, name or industry" });
    U.q.oninput = () => { ST.q = U.q.value; ST.shown = 150; save(); renderResults(); };
    U.filtersBtn = el("button", { class: "btn", "aria-controls": "sx-filters", onclick: () => { ST.filtersOpen = !ST.filtersOpen; save(); renderFilters(); } }, "Filters (0)");
    U.groupSel = el("select", { class: "btn", "aria-label": "Group results", onchange: () => { ST.spec.group_by = U.groupSel.value || null; if (ST.spec.group_by === "quintile") ST.spec.group_by = "quintile:" + (ST.spec.sort?.field && ST.spec.sort.field !== "similarity" ? ST.spec.sort.field : "mcap"); save(); render(); } });
    U.actionsBtn = el("button", { class: "btn", "aria-haspopup": "dialog", "aria-expanded": "false", onclick: () => {
      if (activePopover?.anchor === U.actionsBtn) activePopover.close(); else openActions();
    } }, "Actions");
    const tools = el("div", { class: "scr-tools" }, U.q, U.presets, U.filtersBtn, U.actionsBtn);
    U.chips = el("div", { class: "sx-chips" });
    U.filters = el("div", { class: "sx-filters", id: "sx-filters" });
    U.summary = el("div", { class: "sx-summary" });
    U.table = el("div", { class: "sx-table" });
    U.explain = el("div", { class: "sx-explain", hidden: true });
    main.append(el("section", { class: "panel" }, tools, U.presetWhy, U.chips, U.explain, U.filters, U.summary, U.table,
        el("div", { class: "src", style: "padding-top:10px" }, "Fundamentals are the trailing four quarters as filed; prices are daily closes. Click a number to see the filing, dates and inputs behind it. Click a row to open the company.")));
  }

  function compact(spec, withCols) {
    const s = {};
    for (const [k, v] of Object.entries(spec)) {
      if (v == null || v === "" || (Array.isArray(v) && !v.length) || k === "explanation" || k === "assumptions") continue;
      if (k === "columns" && !withCols) continue;
      s[k] = v;
    }
    if (withCols) s.columns = ST.cols.slice();
    return s;
  }

  function render() {
    renderPresets(); renderChips(); renderFilters(); renderGroupSel(); renderResults();
  }
  function renderGroupSel() {
    const U = SCR.ui, g = ST.spec.group_by || "";
    U.groupSel.innerHTML = "";
    [["", "No grouping"], ["sector", "Group: sector"], ["industry", "Group: industry"], ["mcap_bucket", "Group: market cap"], ["quintile", "Group: quintile of sort"], ["corr_cluster", "Group: correlation cluster"]]
      .forEach(([v, n]) => { const o = el("option", { value: v }, n); if (v === g || (v === "quintile" && g.startsWith("quintile:"))) o.selected = true; U.groupSel.append(o); });
  }
  function renderPresets() {
    const U = SCR.ui;
    U.presets.value = ST.preset || "";
    const p = PRESETS.find(x => x.id === ST.preset);
    U.presetWhy.hidden = !p;
    U.presetWhy.innerHTML = "";
    if (p) U.presetWhy.append(el("b", {}, p.n + ". "), p.why);
  }

  /* ---------- chips ---------- */
  function renderChips() {
    const U = SCR.ui, s = ST.spec;
    U.chips.innerHTML = "";
    const chip = (text, onEdit, onRemove, title) => el("span", { class: "sx-chip", title: title || "" },
      el("button", { class: "t", onclick: onEdit || null, disabled: !onEdit }, text),
      el("button", { class: "x", "aria-label": "Remove " + text, onclick: () => { onRemove(); if (U.chips.hidden) U.filtersBtn.focus(); } }, svgUse("i-x")));
    s.filters.forEach((f, i) => U.chips.append(chip(filterText(f), e => editFilter(i, e.currentTarget), () => { s.filters.splice(i, 1); setSpec(s, { columns: false, keepPreset: true }); }, fieldOf(f.field)?.d)));
    const lists = [["sectors", "Sector"], ["exclude_sectors", "Not sector"], ["industries", "Industry"], ["exclude_industries", "Not industry"], ["tickers", "Only"], ["exclude_tickers", "Not"]];
    lists.forEach(([k, lab]) => s[k].forEach(x => U.chips.append(chip(`${lab}: ${x}`, null, () => { s[k] = s[k].filter(y => y !== x); setSpec(s, { columns: false, keepPreset: true }); }))));
    if (s.index !== "all") U.chips.append(chip(`${SPECLIB.INDEX_LABELS[s.index]} only`, null, () => { s.index = "all"; setSpec(s, { columns: false, keepPreset: true }); }));
    if (s.similar_to) U.chips.append(chip(`Similar to ${s.similar_to.ticker} · ${s.similar_to.n} nearest`, null, () => { s.similar_to = null; if (s.sort?.field === "similarity") s.sort = null; setSpec(s, { columns: false, keepPreset: true }); }, "Nearest neighbours on standardized valuation, growth, quality, risk and correlation fields"));
    if (s.limit) U.chips.append(chip(`First ${s.limit}`, null, () => { s.limit = null; setSpec(s, { columns: false, keepPreset: true }); }));
    U.chips.hidden = !U.chips.childElementCount;
    if (U.chips.hidden) return;
    const add = el("button", { class: "btn sx-add", onclick: e => addFilterMenu(e.currentTarget) }, "+ Add filter");
    U.chips.append(add);
  }
  let activePopover = null;
  function popover(anchor, content, onClose, name = "Edit screen filter") {
    activePopover?.close({ restoreFocus: false });
    const pop = el("div", { class: "sx-pop", role: "dialog", "aria-label": name, tabindex: "-1" }, content);
    document.body.append(pop);
    const r = anchor.getBoundingClientRect();
    const w = Math.min(360, innerWidth - 16);
    pop.style.width = w + "px";
    pop.style.left = Math.max(8, Math.min(innerWidth - w - 8, r.left)) + "px";
    pop.style.top = (r.bottom + 6 + scrollY) + "px";
    let closed = false;
    const off = e => { if (!pop.contains(e.target) && !anchor.contains(e.target)) close(); };
    const esc = e => { if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(); } };
    const route = () => close({ restoreFocus: false });
    function close({ restoreFocus = true } = {}) {
      if (closed) return;
      closed = true;
      pop.remove();
      document.removeEventListener("mousedown", off, true);
      document.removeEventListener("keydown", esc, true);
      removeEventListener("hashchange", route);
      anchor.setAttribute("aria-expanded", "false");
      if (activePopover?.pop === pop) activePopover = null;
      onClose?.();
      if (restoreFocus && anchor.isConnected) anchor.focus();
    }
    activePopover = { close, pop, anchor };
    anchor.setAttribute("aria-expanded", "true");
    document.addEventListener("mousedown", off, true);
    document.addEventListener("keydown", esc, true);
    addEventListener("hashchange", route);
    (pop.querySelector("input,select,button") || pop).focus();
    return activePopover;
  }
  function openActions() {
    const U = SCR.ui;
    const action = (label, fn) => el("button", { class: "btn", onclick: () => { activePopover?.close(); fn(); } }, label);
    const sort = el("select", { class: "btn", "aria-label": "Sort results", onchange: () => { ST.spec.sort = { field: sort.value, dir: dir.value }; save(); renderResults(); } },
      ["ticker", "name", ...SCR.data.fields.filter(f => f.g !== "id").map(f => f.id)].map(id => el("option", { value: id }, fieldOf(id)?.n || (id === "ticker" ? "Ticker" : "Company"))));
    if (ST.spec.sort?.field === "similarity") sort.append(el("option", { value: "similarity" }, "Similarity"));
    sort.value = ST.spec.sort?.field || "ticker";
    const dir = el("select", { class: "btn", "aria-label": "Sort direction", onchange: () => { ST.spec.sort = { field: sort.value, dir: dir.value }; save(); renderResults(); } }, el("option", { value: "asc" }, "Ascending"), el("option", { value: "desc" }, "Descending"));
    dir.value = ST.spec.sort?.dir || "asc";
    popover(U.actionsBtn, el("div", { class: "sx-actions" },
      el("label", {}, "Sort", sort), dir, el("label", {}, "Group", U.groupSel),
      action("Columns", () => openColumns({ currentTarget: U.actionsBtn })),
      action("Saved screens", () => openSaved({ currentTarget: U.actionsBtn })),
      action("Share screen", () => copyText(location.origin + location.pathname + "#" + SHARE_PREFIX + b64e(JSON.stringify(compact(ST.spec, true))), "Screen link copied")),
      action("Export CSV", csv),
      el("button", { class: "btn", "aria-pressed": String(ST.chatOpen), onclick: () => { activePopover?.close(); ST.chatOpen = !ST.chatOpen; save(); U.grid.classList.toggle("nochat", !ST.chatOpen); if (ST.chatOpen) U.ta?.focus(); } }, "Assistant"),
      action("Reset screen", () => setSpec(blank(), { columns: false }))), null, "Screen actions");
  }
  function filterEditor(f, onApply) {
    const fd = fieldOf(f.field);
    const box = el("div", { class: "sx-ed" });
    box.append(el("div", { class: "eyebrow" }, (SCR.groups.find(g => g.id === fd.g) || {}).n || ""), el("b", {}, fd.n), el("div", { class: "faint", style: "font-size:12px" }, fd.d));
    if (fd.q) box.append(el("div", { class: "faint mono", style: "font-size:11.5px" }, `All tracked: 5th pct ${fmtV(fd.id, fd.q[0])} · median ${fmtV(fd.id, fd.q[1])} · 95th pct ${fmtV(fd.id, fd.q[2])} · ${fd.cov} reporting`));
    if (fd.u === "bool") {
      const sel = el("select", { class: "btn" }, el("option", { value: "true" }, "Yes"), el("option", { value: "false" }, "No"));
      sel.value = f.op === "false" ? "false" : "true";
      box.append(sel, el("button", { class: "btn acc", onclick: () => onApply({ field: f.field, op: sel.value }) }, "Apply"));
      return box;
    }
    const mode = el("select", { class: "btn", "aria-label": "Condition" }, ...[["between", "Range"], [">", "Above"], ["<", "Below"], ["top", "Top N%"], ["bottom", "Bottom N%"], ["exists", "Reported"]].map(([v, n]) => el("option", { value: v }, n)));
    const op0 = [">=", ">"].includes(f.op) ? ">" : ["<=", "<"].includes(f.op) ? "<" : f.op;
    mode.value = ["between", ">", "<", "top", "bottom", "exists"].includes(op0) ? op0 : "between";
    const lo = el("input", { type: "number", step: "any", placeholder: "min", "aria-label": "Minimum" }), hi = el("input", { type: "number", step: "any", placeholder: "max", "aria-label": "Maximum" });
    const pct = el("input", { type: "number", min: 1, max: 100, step: 1, value: f.pct || 20, "aria-label": "Percent" });
    const scope = el("select", { class: "btn", "aria-label": "Within" }, el("option", { value: "all" }, "of the index"), el("option", { value: "sector" }, "of each sector"), el("option", { value: "industry" }, "of each industry"));
    scope.value = f.scope || "all";
    if (f.op === "between") { lo.value = toInput(fd, f.value?.[0]); hi.value = toInput(fd, f.value?.[1]); }
    else if (op0 === ">") lo.value = toInput(fd, f.value);
    else if (op0 === "<") hi.value = toInput(fd, f.value);
    const unit = inUnit(fd);
    const slider = rangeSlider(fd, lo, hi);
    const rangeRow = el("div", { class: "sx-range" }, lo, el("span", { class: "faint" }, "to"), hi, unit ? el("span", { class: "faint" }, unit) : null);
    const pctRow = el("div", { class: "sx-range" }, pct, el("span", { class: "faint" }, "%"), scope);
    const sync = () => { const m = mode.value; rangeRow.hidden = slider.hidden = !["between", ">", "<"].includes(m); pctRow.hidden = !["top", "bottom"].includes(m); lo.disabled = m === "<"; hi.disabled = m === ">"; };
    mode.onchange = sync; sync();
    const apply = () => {
      const m = mode.value;
      let nf;
      if (m === "top" || m === "bottom") nf = { field: f.field, op: m, pct: Math.max(1, Math.min(100, +pct.value || 20)), scope: scope.value };
      else if (m === "exists") nf = { field: f.field, op: "exists" };
      else if (m === ">") nf = { field: f.field, op: ">", value: fromInput(fd, lo.value) };
      else if (m === "<") nf = { field: f.field, op: "<", value: fromInput(fd, hi.value) };
      else nf = { field: f.field, op: "between", value: [fromInput(fd, lo.value), fromInput(fd, hi.value)] };
      if ((nf.op === ">" || nf.op === "<") && nf.value == null) return toast("Enter a value");
      if (nf.op === "between" && nf.value[0] == null && nf.value[1] == null) return toast("Enter a minimum or maximum");
      onApply(nf);
    };
    [lo, hi, pct].forEach(i => i.addEventListener("keydown", e => { if (e.key === "Enter") apply(); }));
    box.append(mode, rangeRow, slider, pctRow, el("button", { class: "btn acc", onclick: apply }, "Apply"));
    return box;
  }
  function rangeSlider(fd, lo, hi) {
    // two overlaid range inputs over the field's 5th to 95th percentile, synced with the number boxes
    const wrap = el("div", { class: "sx-slider" });
    if (!fd.q || fd.q[0] == null || fd.q[2] == null || fd.q[0] === fd.q[2]) { wrap.hidden = true; return wrap; }
    const a = toInput(fd, fd.q[0]), b = toInput(fd, fd.q[2]);
    const step = Math.abs(b - a) / 200;
    const r1 = el("input", { type: "range", min: a, max: b, step, "aria-label": "Minimum slider" }), r2 = el("input", { type: "range", min: a, max: b, step, "aria-label": "Maximum slider" });
    r1.value = lo.value !== "" ? lo.value : a; r2.value = hi.value !== "" ? hi.value : b;
    const nice = v => +(+v).toPrecision(3);
    r1.oninput = () => { if (+r1.value > +r2.value) r1.value = r2.value; lo.value = nice(r1.value); };
    r2.oninput = () => { if (+r2.value < +r1.value) r2.value = r1.value; hi.value = nice(r2.value); };
    lo.addEventListener("input", () => { if (lo.value !== "") r1.value = lo.value; });
    hi.addEventListener("input", () => { if (hi.value !== "") r2.value = hi.value; });
    wrap.append(r1, r2, el("div", { class: "sx-ticks faint mono" }, el("span", {}, fmtV(fd.id, fd.q[0])), el("span", {}, fmtV(fd.id, fd.q[1]) + " median"), el("span", {}, fmtV(fd.id, fd.q[2]))));
    return wrap;
  }
  function editFilter(i, anchor) {
    const f = ST.spec.filters[i];
    const p = popover(anchor, filterEditor(f, nf => { ST.spec.filters[i] = nf; p.close({ restoreFocus: false }); setSpec(ST.spec, { columns: false, keepPreset: true }); focusFilter(i); }), null, `Edit ${fieldOf(f.field)?.n || f.field} filter`);
  }
  function focusFilter(i) {
    (SCR.ui.chips.querySelectorAll(".sx-chip .t")[i] || SCR.ui.filtersBtn).focus();
  }
  function addFilterMenu(anchor) {
    const q = el("input", { type: "search", placeholder: "Search fields (e.g. margin, oil, insider)", "aria-label": "Search fields" });
    const list = el("div", { class: "sx-flist" });
    const box = el("div", {}, q, list);
    const p = popover(anchor, box, null, "Add filter");
    const draw = () => {
      list.innerHTML = "";
      const t = q.value.trim().toLowerCase();
      const hits = SCR.data.fields.filter(f => !t || f.n.toLowerCase().includes(t) || f.id.includes(t) || f.d.toLowerCase().includes(t)).slice(0, 60);
      hits.forEach(f => list.append(el("button", { onclick: () => { p.close({ restoreFocus: false }); const nf = f.u === "bool" ? { field: f.id, op: "true" } : { field: f.id, op: "between", value: [null, null] }; const pp = popover(anchor, filterEditor(nf, x => { const i = ST.spec.filters.length; ST.spec.filters.push(x); pp.close({ restoreFocus: false }); if (!ST.cols.includes(x.field)) { ST.cols.push(x.field); } setSpec(ST.spec, { columns: false, keepPreset: true }); focusFilter(i); })); } },
        el("span", {}, f.n), el("span", { class: "faint", style: "font-size:11px" }, (SCR.groups.find(g => g.id === f.g) || {}).n))));
      if (!hits.length) list.append(el("div", { class: "faint", style: "padding:8px" }, "No field matches."));
    };
    q.oninput = draw; draw();
  }

  /* ---------- filter panel ---------- */
  function renderFilters() {
    const U = SCR.ui;
    U.filters.innerHTML = "";
    U.filters.hidden = !ST.filtersOpen;
    U.filtersBtn.classList.toggle("on", ST.filtersOpen);
    U.filtersBtn.setAttribute("aria-expanded", String(ST.filtersOpen));
    U.filtersBtn.textContent = `Filters (${SCR.ui.chips.querySelectorAll(".sx-chip").length})`;
    if (!ST.filtersOpen) return;
    const s = ST.spec;
    // universe: index, sectors, industries, excluded tickers
    const idx = seg(SPECLIB.INDEXES.map(id => [id, SPECLIB.INDEX_LABELS[id]]), s.index, v => { s.index = v; setSpec(s, { columns: false, keepPreset: true }); });
    const sectors = el("div", { class: "picker sx-sectors" }, SCR.catalogue.sectors.map(sec => {
      const st = s.sectors.includes(sec) ? "on" : s.exclude_sectors.includes(sec) ? "off" : "";
      return el("button", { class: st, title: "Click to include, again to exclude, again to clear", onclick: () => {
        if (st === "") { s.sectors.push(sec); } else if (st === "on") { s.sectors = s.sectors.filter(x => x !== sec); s.exclude_sectors.push(sec); } else { s.exclude_sectors = s.exclude_sectors.filter(x => x !== sec); }
        setSpec(s, { columns: false, keepPreset: true });
      } }, st === "off" ? "Not " + sec : sec);
    }));
    const indIn = el("input", { type: "search", placeholder: "Add industry (SIC), e.g. banks, semiconductors", list: "sx-ind", "aria-label": "Industry" });
    const dl = el("datalist", { id: "sx-ind" }, SCR.catalogue.industries.map(x => el("option", { value: x })));
    const indMode = el("select", { class: "btn", "aria-label": "Include or exclude" }, el("option", { value: "in" }, "Only"), el("option", { value: "out" }, "Exclude"));
    const indAdd = () => { const v = SCR.catalogue.industries.find(x => x.toLowerCase() === indIn.value.trim().toLowerCase()) || SCR.catalogue.industries.find(x => x.toLowerCase().includes(indIn.value.trim().toLowerCase())); if (!v || !indIn.value.trim()) return toast("No industry matches"); (indMode.value === "in" ? s.industries : s.exclude_industries).push(v); setSpec(s, { columns: false, keepPreset: true }); };
    indIn.addEventListener("keydown", e => { if (e.key === "Enter") indAdd(); });
    const exIn = el("input", { type: "search", placeholder: "Exclude tickers, e.g. TSLA, NVDA", "aria-label": "Exclude tickers" });
    exIn.addEventListener("keydown", e => { if (e.key !== "Enter") return; const ts = exIn.value.toUpperCase().split(/[\s,]+/).filter(t => SCR.byTk[t]); if (!ts.length) return toast("No covered ticker"); s.exclude_tickers.push(...ts.filter(t => !s.exclude_tickers.includes(t))); setSpec(s, { columns: false, keepPreset: true }); });
    const fsearch = el("input", { type: "search", placeholder: `Search ${SCR.data.fields.length} fields`, "aria-label": "Search fields" });
    const groupsBox = el("div", { class: "sx-groups" });
    U.filters.append(el("div", { class: "sx-uni" },
      el("div", { class: "sx-row" }, el("span", { class: "eyebrow" }, "Universe"), idx),
      el("div", { class: "sx-row" }, el("span", { class: "eyebrow" }, "Sectors"), sectors),
      el("div", { class: "sx-row" }, el("span", { class: "eyebrow" }, "Industry"), el("div", { class: "sx-inline" }, indMode, indIn, dl, el("button", { class: "btn", onclick: indAdd }, "Add"))),
      el("div", { class: "sx-row" }, el("span", { class: "eyebrow" }, "Exclude"), exIn),
      el("div", { class: "sx-row" }, el("span", { class: "eyebrow" }, "Fields"), el("div", { class: "sx-inline" }, fsearch,
        el("button", { class: "btn sx-add", onclick: e => addFilterMenu(e.currentTarget) }, "+ Add filter")))), groupsBox);
    const drawGroups = () => {
      groupsBox.innerHTML = "";
      const t = fsearch.value.trim().toLowerCase();
      SCR.groups.filter(g => g.id !== "id").forEach(g => {
        const fs = SCR.data.fields.filter(f => f.g === g.id && (!t || f.n.toLowerCase().includes(t) || f.d.toLowerCase().includes(t) || f.id.includes(t)));
        if (!fs.length) return;
        const active = s.filters.filter(f => fieldOf(f.field)?.g === g.id).length;
        const open = !!t || ST.openGroups.includes(g.id);
        const det = el("details", { class: "sx-group" });
        if (open) det.open = true;
        det.append(el("summary", {}, el("b", {}, g.n), el("span", { class: "faint" }, ` ${fs.length} fields`), active ? el("span", { class: "chip acc", style: "margin-left:8px" }, `${active} active`) : null));
        const body = el("div", { class: "sx-fields" });
        det.append(body);
        const fill = () => { if (body.childElementCount) return; fs.forEach(f => body.append(fieldRow(f))); };
        if (open) fill();
        det.addEventListener("toggle", () => { if (det.open) { fill(); if (!t && !ST.openGroups.includes(g.id)) ST.openGroups.push(g.id); } else ST.openGroups = ST.openGroups.filter(x => x !== g.id); save(); });
        groupsBox.append(det);
      });
    };
    fsearch.oninput = drawGroups; drawGroups();
  }
  function fieldRow(f) {
    const s = ST.spec;
    const i = s.filters.findIndex(x => x.field === f.id);
    const cur = i >= 0 ? s.filters[i] : null;
    const name = el("div", { class: "nm" }, el("span", {}, f.n), el("span", { class: "faint", title: f.d, style: "cursor:help" }, " ⓘ"));
    const row = el("div", { class: "sx-frow" + (cur ? " on" : "") }, name);
    if (f.u === "bool") {
      const val = cur ? (cur.op === "false" ? "no" : "yes") : "any";
      row.append(seg([["any", "Any"], ["yes", "Yes"], ["no", "No"]], val, v => {
        const k = s.filters.findIndex(x => x.field === f.id);
        if (k >= 0) s.filters.splice(k, 1);
        if (v !== "any") s.filters.push({ field: f.id, op: v === "yes" ? "true" : "false" });
        setSpec(s, { columns: false, keepPreset: true });
      }), el("span", { class: "faint", style: "font-size:11px" }, `${f.true ?? 0} of ${f.cov}`));
      return row;
    }
    const lo = el("input", { type: "number", step: "any", placeholder: "min", "aria-label": f.n + " minimum" });
    const hi = el("input", { type: "number", step: "any", placeholder: "max", "aria-label": f.n + " maximum" });
    if (cur && (cur.op === "between")) { lo.value = toInput(f, cur.value[0]); hi.value = toInput(f, cur.value[1]); }
    if (cur && [">", ">="].includes(cur.op)) lo.value = toInput(f, cur.value);
    if (cur && ["<", "<="].includes(cur.op)) hi.value = toInput(f, cur.value);
    const commit = () => {
      const a = fromInput(f, lo.value), b = fromInput(f, hi.value);
      const k = s.filters.findIndex(x => x.field === f.id);
      const nf = a == null && b == null ? null : a != null && b != null ? { field: f.id, op: "between", value: [a, b] } : a != null ? { field: f.id, op: ">=", value: a } : { field: f.id, op: "<=", value: b };
      if (k >= 0) { if (nf) s.filters[k] = nf; else s.filters.splice(k, 1); } else if (nf) s.filters.push(nf);
      setSpec(s, { columns: false, keepPreset: true });
    };
    lo.addEventListener("change", commit); hi.addEventListener("change", commit);
    const pctBtn = el("button", { class: "btn sx-mini", title: "Top or bottom N% of the index or sector", onclick: e => { const nf = cur && ["top", "bottom"].includes(cur.op) ? cur : { field: f.id, op: "top", pct: 20, scope: "all" }; const p = popover(e.currentTarget, filterEditor(nf, x => { const k = s.filters.findIndex(y => y.field === f.id); if (k >= 0) s.filters[k] = x; else s.filters.push(x); p.close({ restoreFocus: false }); setSpec(s, { columns: false, keepPreset: true }); focusFilter(k >= 0 ? k : s.filters.length - 1); })); } }, cur && ["top", "bottom"].includes(cur.op) ? `${cur.op} ${cur.pct}%` : "N%");
    const slider = rangeSlider(f, lo, hi);
    slider.querySelectorAll("input").forEach(r => r.addEventListener("change", commit));
    row.append(el("div", { class: "sx-range" }, lo, hi, el("span", { class: "faint", style: "min-width:22px" }, inUnit(f)), pctBtn), slider);
    return row;
  }

  /* ---------- results ---------- */
  function cols() {
    const c = ST.cols.filter(id => fieldOf(id));
    if (fieldOf(ST.spec.sort?.field) && !c.includes(ST.spec.sort.field)) c.unshift(ST.spec.sort.field);
    return c;
  }
  function renderResults() {
    const U = SCR.ui;
    const res = run(ST.spec, ST.q);
    SCR.last = res;
    renderSummary(res);
    U.table.innerHTML = "";
    const C = cols();
    const t = el("table", { class: "t scr sx" });
    const sortBy = id => { const s = ST.spec.sort; if (s && s.field === id) s.dir = s.dir === "asc" ? "desc" : "asc"; else ST.spec.sort = { field: id, dir: id === "ticker" || id === "name" ? "asc" : "desc" }; save(); renderResults(); };
    const th = (id, label, r) => { const s = ST.spec.sort; const on = s && s.field === id; return el("th", { class: (r ? "r " : "") + (on ? "sorted" : ""), title: fieldOf(id)?.d || "", onclick: () => sortBy(id) }, label, on ? el("span", { class: "ar" }, s.dir === "asc" ? "▲" : "▼") : null); };
    t.append(el("thead", {}, el("tr", {}, el("th", {}, ""), th("ticker", "Ticker"), th("name", "Company"), res.sim ? el("th", { class: "r", title: "Distance on standardized fields; lower is more similar" }, "Distance") : null, C.map(id => th(id, fieldOf(id).n, true)))));
    const tb = el("tbody");
    const w = watchlist();
    const ncol = C.length + 3 + (res.sim ? 1 : 0);
    const row = r => {
      const star = el("td", { class: "star" + (w.includes(r.ticker) ? " on" : ""), title: "Watchlist", onclick: e => { e.stopPropagation(); const on = toggleWatch(r.ticker); star.classList.toggle("on", on); } }, "★");
      return el("tr", { onclick: () => { location.hash = "company-" + r.ticker.toLowerCase(); } }, star,
        el("td", {}, el("span", { class: "tkc" }, miniLogo(r.ticker, 20), el("b", { class: "mono" }, r.ticker))),
        el("td", { class: "sx-name" }, el("div", {}, r.name), el("div", { class: "faint", style: "font-size:11px" }, r.industry || r.sector)),
        res.sim ? el("td", { class: "r num faint" }, res.sim.get(r.ticker)?.toFixed(2)) : null,
        C.map(id => { const v = r[id]; return el("td", { class: "r num c " + colorCls(id, v), onclick: v == null ? null : e => { e.stopPropagation(); openField(r.ticker, id); }, title: v == null ? "Not available" : "Open source" }, fmtV(id, v)); }));
    };
    const groups = grouping(res.rows, C);
    let shown = 0;
    if (groups) {
      groups.forEach(g => {
        tb.append(el("tr", { class: "sx-grp" }, el("td", { colspan: 3 + (res.sim ? 1 : 0) }, el("b", {}, g.label), el("span", { class: "faint" }, ` · ${g.rows.length}`), g.note ? el("div", { class: "faint", style: "font-size:11px;font-weight:400" }, g.note) : null),
          C.map(id => el("td", { class: "r num faint", title: "Median of the group" }, g.rows.length > 1 ? fmtV(id, median(g.rows.map(r => r[id]))) : ""))));
        g.rows.forEach(r => { if (shown < ST.shown) tb.append(row(r)); shown++; });
      });
    } else res.rows.forEach(r => { if (shown < ST.shown) tb.append(row(r)); shown++; });
    if (!res.rows.length) tb.append(el("tr", {}, el("td", { colspan: ncol, class: "muted", style: "padding:24px;text-align:center" }, "No companies match. Remove a filter chip or loosen a threshold.")));
    t.append(tb);
    U.table.append(el("div", { class: "scroll" }, t));
    if (res.rows.length > ST.shown) U.table.append(el("div", { style: "padding:10px 16px" }, el("button", { class: "btn", onclick: () => { ST.shown = res.rows.length; renderResults(); } }, `Show all ${res.rows.length}`)));
  }
  function grouping(rows, C) {
    const g = ST.spec.group_by;
    if (!g || !rows.length) return null;
    const by = (keyf, order) => {
      const m = new Map();
      rows.forEach(r => { const k = keyf(r); if (!m.has(k)) m.set(k, []); m.get(k).push(r); });
      let arr = [...m.entries()].map(([label, rs]) => ({ label, rows: rs }));
      arr.sort(order || ((a, b) => b.rows.length - a.rows.length || String(a.label).localeCompare(String(b.label))));
      return arr;
    };
    if (g === "sector") return by(r => r.sector);
    if (g === "industry") return by(r => r.industry || "Unclassified");
    if (g === "mcap_bucket") return by(r => mcapBucket(r.mcap), (a, b) => BUCKET_ORDER.indexOf(a.label) - BUCKET_ORDER.indexOf(b.label));
    if (g.startsWith("quintile:")) {
      const id = g.slice(9), f = fieldOf(id);
      const vals = rows.map(r => r[id]).filter(v => typeof v === "number").sort((a, b) => a - b);
      if (!vals.length) return by(() => "No data");
      const cut = k => vals[Math.min(vals.length - 1, Math.floor(vals.length * k / 5))];
      const qOf = v => typeof v !== "number" ? 6 : v < cut(1) ? 1 : v < cut(2) ? 2 : v < cut(3) ? 3 : v < cut(4) ? 4 : 5;
      const lab = q => q === 6 ? `${f.n}: not reported` : `${f.n}, quintile ${q} (${q === 1 ? "lowest" : q === 5 ? "highest" : "middle"})`;
      return by(r => lab(qOf(r[id])), (a, b) => b.label.localeCompare(a.label));
    }
    if (g === "corr_cluster") {
      if (!SCR.corr) { loadCorr().then(() => renderResults()).catch(e => toast("Correlation data could not load: " + e.message)); return [{ label: "Loading correlations…", rows: [] }]; }
      return clusters(rows);
    }
    return null;
  }
  async function loadCorr() {
    if (SCR.corr) return SCR.corr;
    const d = await getJSON("screener/corr.json");
    const bin = atob(d.m), n = d.tickers.length;
    const m = new Int8Array(bin.length);
    for (let i = 0; i < bin.length; i++) m[i] = bin.charCodeAt(i) << 24 >> 24;
    const pos = Object.fromEntries(d.tickers.map((t, i) => [t, i]));
    const at = (i, j) => { if (i === j) return 1; if (i > j) [i, j] = [j, i]; return m[i * n - (i * (i + 1)) / 2 + (j - i - 1)] / 100; };
    SCR.corr = { pos, at, n: d.n, from: d.from, to: d.to };
    return SCR.corr;
  }
  function clusters(rows) {
    // average-linkage hierarchical clustering on 1 − correlation of daily returns
    const C = SCR.corr;
    const items = rows.filter(r => C.pos[r.ticker] != null);
    const rest = rows.filter(r => C.pos[r.ticker] == null);
    const n = items.length;
    if (n < 3) return [{ label: "Too few companies to cluster", rows }];
    const k = Math.max(2, Math.min(12, Math.round(n / 20) + 1));
    const dist = new Float32Array(n * n);
    for (let i = 0; i < n; i++) for (let j = i + 1; j < n; j++) { const d = 1 - C.at(C.pos[items[i].ticker], C.pos[items[j].ticker]); dist[i * n + j] = dist[j * n + i] = d; }
    const size = new Array(n).fill(1), alive = new Array(n).fill(true), members = items.map((_, i) => [i]);
    let count = n;
    while (count > k) {
      let best = Infinity, bi = -1, bj = -1;
      for (let i = 0; i < n; i++) { if (!alive[i]) continue; for (let j = i + 1; j < n; j++) { if (!alive[j]) continue; const d = dist[i * n + j]; if (d < best) { best = d; bi = i; bj = j; } } }
      for (let x = 0; x < n; x++) { if (!alive[x] || x === bi || x === bj) continue; const d = (dist[bi * n + x] * size[bi] + dist[bj * n + x] * size[bj]) / (size[bi] + size[bj]); dist[bi * n + x] = dist[x * n + bi] = d; }
      size[bi] += size[bj]; members[bi] = members[bi].concat(members[bj]); alive[bj] = false; count--;
    }
    const out = [];
    for (let i = 0; i < n; i++) if (alive[i]) {
      const rs = members[i].map(x => items[x]);
      let s = 0, c = 0;
      for (let a = 0; a < members[i].length; a++) for (let b = a + 1; b < members[i].length; b++) { s += C.at(C.pos[rs[a].ticker], C.pos[rs[b].ticker]); c++; }
      out.push({ rows: rs, label: clusterLabel(rs), note: c ? `Average pairwise correlation ${(s / c).toFixed(2)} (daily returns ${C.from} to ${C.to})` : "" });
    }
    out.sort((a, b) => b.rows.length - a.rows.length);
    if (rest.length) out.push({ label: "No correlation data", rows: rest });
    return out;
  }
  function clusterLabel(rs) {
    // traits the members share: dominant sector(s), and correlation or size traits that stand out from the index
    const cnt = new Map(); rs.forEach(r => cnt.set(r.sector, (cnt.get(r.sector) || 0) + 1));
    const top = [...cnt.entries()].sort((a, b) => b[1] - a[1]);
    let lab = top[0][1] / rs.length >= 0.6 ? top[0][0] : top.slice(0, 2).map(x => x[0]).join(" + ");
    const ind = new Map(); rs.forEach(r => r.industry && ind.set(r.industry, (ind.get(r.industry) || 0) + 1));
    const ti = [...ind.entries()].sort((a, b) => b[1] - a[1])[0];
    if (ti && rs.length >= 3 && ti[1] / rs.length >= 0.5) lab = ti[0];
    const traits = [];
    const m = id => median(rs.map(r => r[id]));
    const idx = id => SCR.median[id];
    if (m("c_oil") > 0.35) traits.push("oil-linked");
    if (m("c_tnx") > 0.2) traits.push("gains when yields rise");
    if (m("c_tnx") < -0.2) traits.push("rate-sensitive");
    if (m("c_gold") > 0.35) traits.push("gold-linked");
    if (m("c_btc") > 0.35) traits.push("crypto-linked");
    if (m("c_dxy") < -0.15) traits.push("dollar-hurt");
    if (m("beta") > 1.3) traits.push("high beta");
    if (m("beta") < 0.7) traits.push("low beta");
    if (m("rev_g_ttm") > 0.2) traits.push("fast growth");
    if (idx("div_yield") != null && m("div_yield") > 0.03) traits.push("high yield");
    return lab + (traits.length ? " · " + traits.slice(0, 2).join(", ") : "");
  }

  /* ---------- summary bar ---------- */
  const SUMMARY = ["pe", "rev_g_ttm", "om", "roic", "fcf_yield", "div_yield", "ret_1y", "beta"];
  const SEC_COLORS = ["--c1", "--c2", "--c3", "--c4", "--c5", "--c6"];
  function renderSummary(res) {
    const U = SCR.ui, rows = res.rows;
    const statisticsOpen = !!U.summary.querySelector("details")?.open;
    U.summary.innerHTML = "";
    const uniTxt = `${SPECLIB.INDEX_LABELS[ST.spec.index]}${res.uni.length === SCR.rows.length ? "" : ` · ${res.uni.length} in the selected universe`}`;
    const head = el("div", { class: "sx-count" }, el("b", { class: "num" }, rows.length), el("span", { class: "muted" }, ` of ${SCR.rows.length} companies · ${uniTxt}`));
    const stats = el("div", { class: "sx-stats" }, SUMMARY.map(id => {
      const a = median(rows.map(r => r[id])), b = SCR.median[id];
      return el("div", { class: "sx-stat", title: `Median of the results vs median of all tracked companies. ${fieldOf(id)?.d || ""}` }, el("span", { class: "faint" }, fieldOf(id)?.n || id),
        el("b", { class: "num" }, fmtV(id, a)), el("span", { class: "faint num", style: "font-size:11px" }, "all tracked " + fmtV(id, b)));
    }));
    const cnt = new Map(); rows.forEach(r => cnt.set(r.sector, (cnt.get(r.sector) || 0) + 1));
    const secs = [...cnt.entries()].sort((a, b) => b[1] - a[1]);
    const bar = el("div", { class: "sx-mix", role: "img", "aria-label": "Sector mix: " + secs.map(([s, n]) => `${s} ${n}`).join(", ") });
    secs.forEach(([s, n], i) => bar.append(el("span", { style: `width:${(n / Math.max(1, rows.length)) * 100}%;background:var(${SEC_COLORS[i % SEC_COLORS.length]});opacity:${1 - Math.floor(i / SEC_COLORS.length) * 0.45}`, title: `${s}: ${n}`, onmousemove: e => showTip(e, `<b>${esc(s)}</b> · ${n} (${Math.round(n / rows.length * 100)}%)`), onmouseleave: hideTip })));
    const legend = el("div", { class: "legend" }, secs.slice(0, 6).map(([s, n], i) => el("span", {}, el("i", { style: `background:var(${SEC_COLORS[i % SEC_COLORS.length]})` }), `${s} ${n}`)), secs.length > 6 ? el("span", { class: "faint" }, `+${secs.length - 6} more`) : null);
    const details = el("details", { class: "sx-statistics" }, el("summary", {}, "Result statistics"), stats, rows.length ? bar : null, rows.length ? legend : null);
    details.open = statisticsOpen;
    U.summary.append(head, details);
  }

  /* ---------- columns, saved screens, csv ---------- */
  function openColumns(e) {
    const box = el("div", { class: "sx-cols" });
    const sets = el("div", { class: "picker" }, Object.keys(COLSETS).concat(["Custom"]).map(n => el("button", { class: ST.colset === n ? "on" : "", onclick: () => { if (n !== "Custom") ST.cols = COLSETS[n].slice(); ST.colset = n; save(); renderResults(); p.close(); } }, n)));
    const q = el("input", { type: "search", placeholder: "Search fields", "aria-label": "Search fields" });
    const list = el("div", { class: "sx-flist" });
    const draw = () => {
      list.innerHTML = "";
      const t = q.value.trim().toLowerCase();
      SCR.groups.filter(g => g.id !== "id").forEach(g => {
        const fs = SCR.data.fields.filter(f => f.g === g.id && (!t || f.n.toLowerCase().includes(t) || f.id.includes(t)));
        if (!fs.length) return;
        list.append(el("div", { class: "eyebrow", style: "padding:8px 6px 2px" }, g.n));
        fs.forEach(f => { const cb = el("input", { type: "checkbox" }); cb.checked = ST.cols.includes(f.id); cb.onchange = () => { ST.cols = cb.checked ? ST.cols.concat(f.id) : ST.cols.filter(x => x !== f.id); ST.colset = "Custom"; save(); renderResults(); }; list.append(el("label", { class: "sx-cb" }, cb, el("span", {}, f.n))); });
      });
    };
    q.oninput = draw; draw();
    box.append(el("div", { class: "eyebrow" }, "Column sets"), sets, q, list);
    const p = popover(e.currentTarget, box, null, "Choose columns");
  }
  function openSaved(e) {
    const name = el("input", { type: "text", placeholder: "Name this screen", "aria-label": "Screen name" });
    const list = el("div", { class: "sx-flist" });
    const draw = () => {
      list.innerHTML = "";
      const cur = store.get(KEY + ":saved", []);
      if (!cur.length) list.append(el("div", { class: "faint", style: "padding:8px" }, "No saved screens yet."));
      cur.forEach((s, i) => list.append(el("div", { class: "sx-saved" }, el("button", { class: "t", onclick: () => { ST.preset = null; setSpec(storedSpec(s.spec)); if (s.cols) { ST.cols = s.cols; ST.colset = "Custom"; save(); renderResults(); } p.close(); toast(`Loaded "${s.name}"`); } }, el("b", {}, s.name), el("span", { class: "faint", style: "font-size:11px" }, ` ${(s.spec.filters || []).length} filters · ${new Date(s.at).toLocaleDateString("en-US")}`)),
        el("button", { class: "iconbtn", "aria-label": "Delete " + s.name, onclick: () => { const c = store.get(KEY + ":saved", []); c.splice(i, 1); store.set(KEY + ":saved", c); draw(); } }, svgUse("i-x")))));
    };
    const doSave = () => { const n = name.value.trim(); if (!n) return toast("Enter a name"); const c = store.get(KEY + ":saved", []).filter(x => x.name !== n); c.unshift({ name: n, spec: compact(ST.spec, false), cols: ST.cols, at: Date.now() }); store.set(KEY + ":saved", c.slice(0, 50)); name.value = ""; draw(); toast(`Saved "${n}"`); };
    name.addEventListener("keydown", ev => { if (ev.key === "Enter") doSave(); });
    draw();
    const p = popover(e.currentTarget, el("div", {}, el("div", { class: "eyebrow" }, "Saved screens (this browser)"), el("div", { class: "sx-inline" }, name, el("button", { class: "btn acc", onclick: doSave }, "Save")), list), null, "Saved screens");
  }
  function csv() {
    const C = cols();
    const head = ["Ticker", "Company", "Sector", "Industry"].concat(C.map(id => `${fieldOf(id).n} (${fieldOf(id).u})`));
    const q = v => typeof v === "string" ? `"${v.replace(/"/g, '""')}"` : v == null ? "" : typeof v === "boolean" ? (v ? 1 : 0) : v;
    const lines = [head.map(q).join(",")].concat(SCR.last.rows.map(r => [r.ticker, r.name, r.sector, r.industry].concat(C.map(id => r[id])).map(q).join(",")));
    lines.push("", `"Source: Filed Under screener, prices as of ${SCR.data.asof.prices}. Fractions are decimals (0.15 = 15%)."`);
    downloadText("filedunder_screen.csv", lines.join("\n"), "text/csv");
  }

  /* ---------- source panel ---------- */
  async function srcOf(tk) {
    if (!SCR.src[tk]) SCR.src[tk] = getJSON(`screener/src/${tk}.json`);
    return SCR.src[tk];
  }
  const UNIT_OF = { "EPS diluted": "USD/shares", "Diluted shares": "shares", "Shares outstanding (cover page)": "shares" };
  function factCell(tk, ref) {
    const [stmt, label, end, start, v, accn, form, filed, concept, derived] = ref;
    const unit = UNIT_OF[label] || "USD";
    try {
      const m = model(tk);
      if (m.byLabel[label]) { const c = m.get(label, "Q", end); if (c && c.rec.accn === accn && c.end === end) return c; }
    } catch { }
    return { tk, stmt: stmt === "cover" ? "balance" : stmt, label, concept, unit, rec: { v, accn, form, filed, start: start || null, end }, kind: "Q", end, start: start || null, derived: null, sib: () => [], _derivedFlag: derived };
  }
  async function openFactRef(tk, ref, back) {
    try { await ensureCompany(tk); } catch (e) { toast(e.message); return; }
    const cell = factCell(tk, ref);
    openSource(cell, "filing");
    if (back) $("#dh").prepend(el("div", { class: "row" }, el("button", { class: "btn", onclick: back }, svgUse("i-left"), "Back to the calculation")));
  }
  function xbrlCell(tk, x, label) {
    return { tk, stmt: "income", label: label || x.l || "Segment revenue", concept: x.c, unit: "USD", rec: { v: x.v, accn: x.a, form: x.f, filed: x.d, start: x.b || null, end: x.e }, kind: x.b ? "A" : "Q", end: x.e, start: x.b || null, ix: { id: x.id }, sib: () => [] };
  }
  async function openField(tk, id) {
    const f = fieldOf(id), r = SCR.byTk[tk], v = r[id];
    hideTip();
    const dh = $("#dh"), db = $("#db");
    DR.cell = { scr: tk + id };
    dh.innerHTML = ""; db.innerHTML = "";
    const g = (SCR.groups.find(x => x.id === f.g) || {}).n || "";
    dh.append(
      el("div", { class: "row" }, el("span", { class: "eyebrow" }, `Screener · ${g}`), el("div", { class: "x", style: "display:flex;gap:6px" },
        el("button", { class: "btn", title: "Copy citation", onclick: () => copyText(`${tk} ${f.n}: ${fmtV(id, v)} (${SCR.data.asof.prices}). ${f.d} Source: ${f.src}. Filed Under screener.`, "Citation copied") }, svgUse("i-copy"), "Cite"),
        el("button", { class: "iconbtn", onclick: closeSource, "aria-label": "Close source (Esc)" }, svgUse("i-x")))),
      el("div", { class: "row" }, el("h2", {}, `${SHORT[tk] || r.name} · ${f.n}`)),
      el("div", { class: "row" }, el("span", { class: "chip acc mono", style: "font-size:13.5px;height:26px" }, fmtV(id, v)), el("span", { class: "muted", style: "font-size:12.5px" }, f.d)));
    drawer.classList.add("open"); drawer.setAttribute("aria-hidden", "false");
    if (innerWidth < 860) scrim.hidden = false;
    const body = el("div", { style: "display:flex;flex-direction:column;gap:14px" }, el("div", { class: "faint" }, "Loading the inputs…"));
    db.append(body);
    let src;
    try { src = await srcOf(tk); } catch (e) { body.innerHTML = ""; body.append(el("div", { class: "note" }, `The provenance file could not be loaded (${e.message}).`)); return; }
    if (DR.cell?.scr !== tk + id) return;
    const p = src.f[id];
    body.innerHTML = "";
    if (!p) { body.append(el("div", { class: "note" }, `No derivation stored for this value. Source: ${f.src}.`)); return; }
    const back = () => openField(tk, id);
    const refs = (p.i || []).map(k => src.refs[k]);
    const box = el("div", { class: "calc-box" }, el("div", { class: "eyebrow" }, "How it is calculated"), el("div", { class: "eq" }, p.x));
    body.append(box);
    if (p.p && p.p.length) {
      const pb = el("div", { class: "calc-box" }, el("div", { class: "eyebrow" }, "Prices used"));
      p.p.filter(x => x && x[0]).forEach(([d, c]) => pb.append(el("div", { class: "in sx-static", title: "Daily close" }, el("span", {}, el("b", {}, fdate(d)), " ", el("span", { class: "muted" }, "close")), el("span", { class: "mono" }, c != null ? fnum(c) : "–"))));
      pb.append(el("div", { class: "faint", style: "font-size:11.5px" }, `${f.id === "price" ? "" : "Split-adjusted daily closes, "}Yahoo Finance chart API.`));
      body.append(pb);
    }
    if (p.r) body.append(el("dl", { class: "kv" }, el("dt", {}, "Window"), el("dd", {}, `${/^\d{4}-\d{2}$/.test(p.r[0]) ? p.r[0] : fdate(p.r[0])} to ${/^\d{4}-\d{2}$/.test(p.r[1]) ? p.r[1] : fdate(p.r[1])}`), p.n ? el("dt", {}, "Observations") : null, p.n ? el("dd", {}, String(p.n)) : null, p.series ? el("dt", {}, "Series") : null, p.series ? el("dd", { class: "mono" }, p.series) : null));
    if (refs.length) {
      const fb = el("div", { class: "calc-box" }, el("div", { class: "eyebrow" }, `Filed values (${refs.length}) · click one to open the filing at the figure`));
      refs.sort((a, b) => a[1].localeCompare(b[1]) || b[2].localeCompare(a[2])).forEach(ref => {
        const [stmt, label, end, start, val, accn, form, filed] = ref;
        const unit = UNIT_OF[label] || "USD";
        fb.append(el("div", { class: "in", onclick: () => openFactRef(tk, ref, back), title: `${form} ${accn}, filed ${filed}` },
          el("span", {}, el("b", {}, label), " ", el("span", { class: "muted" }, start ? `${fdate(start)} – ${fdate(end)}` : `as of ${fdate(end)}`), " ", el("span", { class: "form " + formCls(form), style: "margin-left:4px" }, form)),
          el("span", { class: "mono" }, unit === "USD" ? fmtM(val) + "M" : unit === "shares" ? fmtMoney(val, "shares") : fmtMoney(val, "USD/shares"))));
      });
      // totals per line for trailing-four-quarter inputs, so the arithmetic can be checked
      const by = new Map();
      refs.forEach(ref => { const k = ref[1]; if (!by.has(k)) by.set(k, []); by.get(k).push(ref); });
      const sums = [...by.entries()].filter(([lab, list]) => list.length === 4 && list.every(x => x[3]) && (UNIT_OF[lab] || "USD") !== "shares");
      if (sums.length) fb.append(el("div", { class: "faint", style: "font-size:12px" }, "Sum of four quarters: " + sums.map(([lab, list]) => { const t = list.reduce((a, x) => a + (["Capex", "Buybacks", "Dividends paid"].includes(lab) ? Math.abs(x[4]) : x[4]), 0); return `${lab} ${(UNIT_OF[lab] || "USD") === "USD" ? fmtM(t) + "M" : fnum(t)}`; }).join(" · ") + (sums.some(([lab]) => lab === "EPS diluted") ? " (EPS before split adjustment)" : "")));
      body.append(fb);
    }
    if (p.xbrl) {
      const xb = el("div", { class: "calc-box" }, el("div", { class: "eyebrow" }, "Segment facts (XBRL dimensions) · click to open the filing"));
      p.xbrl.forEach(x => xb.append(el("div", { class: "in", onclick: async () => { try { await ensureCompany(tk); } catch { } openSource(xbrlCell(tk, x), "filing"); $("#dh").prepend(el("div", { class: "row" }, el("button", { class: "btn", onclick: back }, svgUse("i-left"), "Back to the calculation"))); } },
        el("span", {}, el("b", {}, x.l || x.c), " ", el("span", { class: "muted" }, `${x.f || ""} ${x.e || ""}`)), el("span", { class: "mono" }, fmtMoney(x.v)))));
      body.append(xb);
    }
    if (p.docs) {
      const kb = el("div", { class: "calc-box" }, el("div", { class: "eyebrow" }, "Reported in investor materials · click to open the document at the figure"));
      p.docs.filter(Boolean).forEach(d => kb.append(el("div", { class: "in", onclick: () => { openDoc(tk, { id: d.doc, title: d.title, storage_path: d.path, ext: d.ext, url: d.url }, { page: d.page, quote: d.quote, printed: d.printed, value: d.v, sheet: d.sheet, cell: d.cell, label: `${f.n} · ${d.label || d.end || ""}`, display: fmtKpi(d.v, p.unit) }); $("#dh").prepend(el("div", { class: "row" }, el("button", { class: "btn", onclick: back }, svgUse("i-left"), "Back to the calculation"))); } },
        el("span", {}, el("b", {}, d.label || d.end || ""), " ", el("span", { class: "muted" }, (d.title || "").slice(0, 70))), el("span", { class: "mono" }, fmtKpi(d.v, p.unit)))));
      body.append(kb, el("div", { class: "faint", style: "font-size:11.5px" }, `KPI id ${p.kpi} · kpi_values document id ${p.docs[0]?.doc || ""}`));
    }
    if (p.a) {
      const ab = el("div", { class: "calc-box" }, el("div", { class: "eyebrow" }, "Filings"));
      const cik = String(r.cik || D.companies[tk]?.cik || "");
      p.a.forEach(([accn, form, d, pdoc]) => ab.append(el("div", { class: "in", onclick: () => window.open(`https://www.sec.gov/Archives/edgar/data/${cik || ""}/${accn.replace(/-/g, "")}/${pdoc || ""}`, "_blank", "noopener"), title: "Open on EDGAR" },
        el("span", {}, el("span", { class: "form " + formCls(form) }, form), " ", el("b", {}, fdate(d))), el("span", { class: "mono faint" }, accn))));
      body.append(ab);
      if (p.est) body.append(el("div", { class: "note" }, `Estimated next results date: ${fdate(p.est)}. This is an estimate from the filing cadence, not a date the company announced.`));
    }
    if (p.u) {
      const nb = el("div", { class: "calc-box" }, el("div", { class: "eyebrow" }, `News items (${p.u.length} shown)`));
      p.u.forEach(([u, t, d]) => nb.append(el("a", { class: "in", href: u, target: "_blank", rel: "noopener" }, el("span", {}, t), el("span", { class: "faint mono", style: "font-size:11px" }, d))));
      body.append(nb);
    }
    if (p.src === "insiders") {
      const ib = el("div", { class: "calc-box" }, el("div", { class: "eyebrow" }, `Form 4 open-market transactions, ${fdate(p.r[0])} to ${fdate(p.r[1])}`), el("div", { class: "faint" }, "Loading transactions…"));
      body.append(ib);
      getJSON(`insiders/${tk}.json`).then(ins => {
        ib.lastChild.remove();
        const rows = (ins.rows || []).filter(x => !x.derivative && ["P", "S"].includes(x.code) && x.tx_date >= p.r[0]).slice(0, 40);
        if (!rows.length) ib.append(el("div", { class: "faint" }, "No open-market purchases or sales in this window."));
        const cik = String(r.cik || D.companies[tk]?.cik || "");
        rows.forEach(x => ib.append(el("a", { class: "in", href: `https://www.sec.gov/Archives/edgar/data/${cik}/${x.accession.replace(/-/g, "")}/`, target: "_blank", rel: "noopener", title: `Form 4 ${x.accession}` },
          el("span", {}, el("b", {}, x.code === "P" ? "Buy" : "Sell"), " ", x.owner, el("span", { class: "muted" }, ` · ${fdate(x.tx_date)}${x.plan_10b5 ? " · 10b5-1 plan" : ""}`)), el("span", { class: "mono " + (x.code === "P" ? "up" : "down") }, fmtMoney((x.shares || 0) * (x.price || 0))))));
      }).catch(e => { ib.lastChild.textContent = "Transactions could not load: " + e.message; });
    }
    if (p.kpi && !p.docs) body.append(el("div", { class: "note" }, p.x));
    body.append(el("dl", { class: "kv" }, el("dt", {}, "Field"), el("dd", { class: "mono" }, id), el("dt", {}, "Source"), el("dd", {}, f.src), el("dt", {}, "Data as of"), el("dd", {}, `Prices ${fdate(src.asof)}; latest quarter ${r.lq || "–"}${r.lq_end ? " (" + fdate(r.lq_end) + ")" : ""}`)));
  }

  /* ---------- agent chat ---------- */
  const SUGGEST = ["Profitable small caps with accelerating revenue that correlate with oil", "Like NVDA in 2016 but cheaper", "Banks most hurt by falling rates", "Quality compounders trading below their 5-year average P/E", "Dividend payers with insider buying"];
  function buildChat(aside) {
    const U = SCR.ui;
    const log = el("div", { class: "log", "aria-live": "polite" });
    const ta = el("textarea", { rows: 3, placeholder: "Describe a screen in plain English. Follow up to change it (\"now only above the 200-day\", \"drop banks\").", "aria-label": "Describe a screen" });
    const send = el("button", { class: "btn acc" }, svgUse("i-spark"), "Screen");
    const undo = el("button", { class: "btn", title: "Restore the screen before the last agent change", disabled: true }, "Undo");
    const clear = el("button", { class: "btn", title: "Start a new conversation" }, "New");
    const close = el("button", { class: "iconbtn sx-chatclose", "aria-label": "Hide the agent", onclick: () => { ST.chatOpen = false; save(); U.grid.classList.add("nochat"); } }, svgUse("i-x"));
    const sugg = el("div", { class: "sugg" }, SUGGEST.map(s => el("button", { onclick: () => { ta.value = s; go(); } }, s)));
    aside.append(el("div", { class: "hd" }, el("h3", {}, "Screen agent"), el("div", { class: "r" }, close)), el("div", { class: "ai sx-ai" }, log, el("div", { class: "comp" }, sugg, ta, el("div", { class: "row" }, send, undo, clear, el("span", { class: "faint", style: "font-size:11.5px;flex:1;min-width:120px" }, "Builds filters you can see and edit. It does not supply numbers.")))));
    U.ta = ta;
    const hist = store.get(KEY + ":chat", []);
    const undoStack = [];
    const intro = () => log.append(el("div", { class: "msg a" }, "Describe the companies you want. I turn the request into filters on the fields of this screener, and every number in the table comes from the data, not from me."));
    const drawTurn = (h) => {
      log.append(el("div", { class: "msg u" }, h.prompt));
      const a = el("div", { class: "msg a sx-turn" });
      if (h.error) a.append(el("div", { class: "note" }, h.error));
      else {
        if (h.spec.explanation) a.append(el("p", {}, h.spec.explanation));
        if (h.diff?.length) a.append(el("ul", { class: "sx-diff" }, h.diff.map(([s, t]) => el("li", { class: s === "+" ? "add" : s === "−" ? "rem" : "chg" }, el("b", { class: "mono" }, s), " ", t))));
        if (h.spec.assumptions?.length) a.append(el("div", { class: "faint", style: "font-size:12px" }, el("b", {}, "Assumptions"), el("ul", {}, h.spec.assumptions.map(x => el("li", {}, x)))));
        if (h.warnings?.length) a.append(el("div", { class: "faint", style: "font-size:12px" }, h.warnings.join(". ")));
        if (h.count != null) a.append(el("div", { class: "faint", style: "font-size:12px" }, `${h.count} companies matched when applied.`));
      }
      log.append(a);
      log.scrollTop = log.scrollHeight;
    };
    intro();
    hist.forEach(drawTurn);
    sugg.hidden = hist.length > 0;
    clear.onclick = () => { hist.length = 0; store.set(KEY + ":chat", hist); log.innerHTML = ""; intro(); sugg.hidden = false; };
    undo.onclick = () => { const prev = undoStack.pop(); if (prev) { setSpec(prev.spec, { columns: false }); ST.cols = prev.cols; save(); renderResults(); toast("Restored the previous screen"); } undo.disabled = !undoStack.length; };
    ta.addEventListener("keydown", e => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); go(); } });
    send.onclick = () => go();
    async function go() {
      const q = ta.value.trim();
      if (!q || send.disabled) return;
      ta.value = "";
      const pending = el("div", { class: "msg a" }, el("span", { class: "thinking" }, el("i"), "Building the screen"));
      log.append(el("div", { class: "msg u" }, q), pending); log.scrollTop = log.scrollHeight;
      send.disabled = true;
      const before = JSON.parse(JSON.stringify(ST.spec));
      const turn = { prompt: q, at: Date.now() };
      try {
        const r = await fetch("/api/screen", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ prompt: q, current: compact(ST.spec, false), history: hist.filter(h => h.spec).slice(-4).map(h => ({ prompt: h.prompt, spec: compact(h.spec, true) })) }) });
        const j = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(j.error || (r.status === 429 ? AI_ERR.rate_limited : r.status === 403 ? AI_ERR.forbidden : AI_ERR.upstream_error));
        const v = validate(j.spec);                         // client-side check against the same catalogue
        if (v.errors.length) throw new Error("The agent's screen referenced fields this page does not have: " + v.errors.join("; "));
        undoStack.push({ spec: before, cols: ST.cols.slice() }); undo.disabled = false;
        setSpec(v.spec);
        turn.spec = v.spec; turn.diff = specDiff(before, ST.spec); turn.warnings = [...(j.warnings || []), ...v.warnings]; turn.count = SCR.last.rows.length;
        if (j.retried) turn.warnings.push("The first draft named an unknown field and was corrected");
      } catch (e) {
        turn.error = e.message || String(e);
      }
      pending.remove(); log.lastChild?.remove();
      drawTurn(turn);
      hist.push(turn); while (hist.length > 30) hist.shift();
      store.set(KEY + ":chat", hist);
      sugg.hidden = true;
      send.disabled = false;
      if (innerWidth < 860 && turn.spec) SCR.ui.summary.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }

  /* ---------- styles (app.css variables only, so any theme applies) ---------- */
  function injectStyle() {
    if (document.getElementById("sx-style")) return;
    const s = document.createElement("style");
    s.id = "sx-style";
    s.textContent = `
.sx-grid{display:grid;grid-template-columns:minmax(0,1fr) 360px;gap:16px;align-items:start}
.sx-grid.nochat{grid-template-columns:minmax(0,1fr)}
.sx-grid.nochat .sx-chat{display:none}
.sx-main{display:flex;flex-direction:column;gap:16px;min-width:0}
.sx-chat{position:sticky;top:76px;display:flex;flex-direction:column;max-height:calc(100vh - 96px);min-height:420px}
.sx-chat .sx-ai{flex:1;min-height:0;display:flex;flex-direction:column}
.sx-chat .log{min-height:120px}
.scr-tools{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.scr-tools input[type=search]{flex:1;min-width:180px}
.sx-presets{max-width:240px;min-width:0}
.sx-why{padding:0 16px 10px;font-size:12.5px;color:var(--muted);max-width:110ch}
.sx-actions{display:flex;flex-direction:column;gap:8px}
.sx-actions label{display:flex;flex-direction:column;gap:4px;font-size:12px}
.sx-actions .btn{text-align:left}
.sx-statistics>summary{cursor:pointer;font-size:12px;color:var(--muted)}
.sx-statistics[open]{display:flex;flex-direction:column;gap:10px}
.scr-tools .btn.on{border-color:var(--ink)}
.sx-chips{display:flex;flex-wrap:wrap;gap:6px;padding:10px 16px;border-bottom:1px solid var(--line);align-items:center}
.sx-chip{display:inline-flex;align-items:center;border:1px solid var(--line2);border-radius:999px;background:var(--accent-soft);color:var(--accent);font-size:12px;font-weight:600;overflow:hidden}
.sx-chip button{border:0;background:none;color:inherit;cursor:pointer;font:inherit;padding:3px 4px 3px 10px}
.sx-chip button.t[disabled]{cursor:default}
.sx-chip button.x{padding:3px 8px 3px 4px;display:inline-grid;place-items:center}
.sx-chip svg{width:11px;height:11px;stroke:currentColor;fill:none;stroke-width:2}
.sx-add{height:26px;font-size:12px;border-style:dashed}
.sx-explain{padding:10px 16px;border-bottom:1px solid var(--line);font-size:12.5px;color:var(--muted)}
.sx-filters{border-bottom:1px solid var(--line);padding:12px 16px;display:flex;flex-direction:column;gap:12px;background:var(--rail)}
.sx-uni{display:flex;flex-direction:column;gap:8px}
.sx-row{display:grid;grid-template-columns:90px minmax(0,1fr);gap:10px;align-items:center}
.sx-row input[type=search]{height:30px;border:1px solid var(--line);border-radius:7px;padding:0 10px;background:var(--surface);width:100%;max-width:420px}
.sx-sectors button{font-size:11.5px;padding:3px 9px}
.sx-sectors button.on{background:var(--accent-soft);color:var(--accent);border-color:var(--accent)}
.sx-sectors button.off{background:var(--down-soft);color:var(--down);border-color:var(--down);text-decoration:line-through}
.sx-inline{display:flex;gap:6px;align-items:center;flex-wrap:wrap}
.sx-inline input{height:30px;border:1px solid var(--line);border-radius:7px;padding:0 10px;background:var(--surface);min-width:0;flex:1}
.sx-groups{display:flex;flex-direction:column;gap:6px}
.sx-group{border:1px solid var(--line);border-radius:8px;background:var(--surface)}
.sx-group>summary{cursor:pointer;padding:8px 12px;font-size:13px;list-style-position:inside}
.sx-fields{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:2px 16px;padding:4px 12px 12px}
.sx-frow{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:4px 8px;align-items:center;padding:6px 6px;border-radius:6px}
.sx-frow.on{background:var(--accent-soft)}
.sx-frow .nm{font-size:12.5px;min-width:0}
.sx-frow .sx-slider{grid-column:1/-1}
.sx-range{display:flex;gap:4px;align-items:center}
.sx-range input[type=number]{width:72px;height:28px;border:1px solid var(--line);border-radius:6px;padding:0 6px;background:var(--surface);font-size:12px}
.sx-mini{height:28px;padding:0 8px;font-size:11.5px}
.sx-slider{position:relative;height:36px}
.sx-slider input[type=range]{position:absolute;left:0;right:0;top:2px;width:100%;margin:0;background:none;pointer-events:none;-webkit-appearance:none;appearance:none;height:16px}
.sx-slider input[type=range]::-webkit-slider-thumb{pointer-events:auto;-webkit-appearance:none;width:14px;height:14px;border-radius:50%;background:var(--accent);border:2px solid var(--surface);box-shadow:0 0 0 1px var(--accent);cursor:pointer}
.sx-slider input[type=range]::-moz-range-thumb{pointer-events:auto;width:12px;height:12px;border-radius:50%;background:var(--accent);border:2px solid var(--surface);cursor:pointer}
.sx-slider input[type=range]::-webkit-slider-runnable-track{height:4px;background:var(--line2);border-radius:2px}
.sx-slider input[type=range]::-moz-range-track{height:4px;background:var(--line2);border-radius:2px}
.sx-slider input[type=range]+input[type=range]::-webkit-slider-runnable-track{background:transparent}
.sx-ticks{position:absolute;left:0;right:0;top:21px;display:flex;justify-content:space-between;font-size:10px}
.sx-summary{padding:12px 16px;border-bottom:1px solid var(--line);display:flex;flex-direction:column;gap:10px}
.sx-count{font-size:13px}.sx-count b{font-size:18px}
.sx-stats{display:grid;grid-template-columns:repeat(8,minmax(0,1fr));gap:8px}
.sx-stat{display:flex;flex-direction:column;border:1px solid var(--line);border-radius:8px;padding:6px 10px;font-size:11.5px;background:var(--surface)}
.sx-stat b{font-size:15px;color:var(--ink)}
.sx-mix{display:flex;height:10px;border-radius:5px;overflow:hidden;background:var(--sunk)}
.sx-mix span{height:100%;min-width:2px}
table.sx td.c{cursor:pointer}
table.sx td.c:hover{background:var(--accent-soft);color:var(--accent)}
table.sx td.sx-name{max-width:220px}
table.sx td.sx-name div{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
table.sx th.r{white-space:normal;min-width:64px;max-width:118px;vertical-align:bottom;line-height:1.25}
table.sx td{padding-top:6px;padding-bottom:6px}
table.sx tr.sx-grp td{background:var(--sunk);font-size:12px;cursor:default;font-weight:600}
table.sx thead th{position:sticky;top:0;background:var(--surface);z-index:1}
.sx-table .scroll{max-height:none}
.sx-pop{position:absolute;z-index:60;background:var(--surface);border:1px solid var(--line2);border-radius:10px;box-shadow:var(--shadow);padding:12px;display:flex;flex-direction:column;gap:8px;max-height:70vh;overflow:auto}
.sx-pop input[type=search],.sx-pop input[type=text]{height:32px;border:1px solid var(--line);border-radius:7px;padding:0 10px;background:var(--surface);width:100%}
.sx-ed{display:flex;flex-direction:column;gap:8px}
.sx-ed .sx-range input{width:90px}
.sx-flist{display:flex;flex-direction:column;max-height:360px;overflow:auto;margin-top:6px}
.sx-flist>button{display:flex;justify-content:space-between;gap:8px;border:0;background:none;text-align:left;padding:7px 8px;border-radius:6px;cursor:pointer;font-size:13px}
.sx-flist>button:hover{background:var(--sunk)}
.sx-cb{display:flex;gap:8px;align-items:center;padding:4px 6px;font-size:13px;cursor:pointer}
.sx-saved{display:flex;gap:6px;align-items:center}
.sx-saved .t{flex:1;text-align:left;border:0;background:none;padding:7px 8px;border-radius:6px;cursor:pointer}
.sx-saved .t:hover{background:var(--sunk)}
.calc-box .in.sx-static{cursor:default}.calc-box .in.sx-static:hover{outline:0}
.sx-diff{list-style:none;padding:0;margin:6px 0;display:flex;flex-direction:column;gap:3px;font-size:12.5px}
.sx-diff li.add b{color:var(--up)} .sx-diff li.rem b{color:var(--down)} .sx-diff li.chg b{color:var(--accent)}
.sx-turn ul{margin:4px 0 0;padding-left:18px}
@media (max-width:1180px){.sx-grid{grid-template-columns:minmax(0,1fr) 320px}.sx-stats{grid-template-columns:repeat(4,minmax(0,1fr))}}
@media (max-width:980px){
  .sx-grid{grid-template-columns:minmax(0,1fr)}
  .sx-chat{position:static;max-height:none;order:-1;min-height:0}
  .sx-chat .log{max-height:300px}
}
@media (max-width:860px){
  .sx-row{grid-template-columns:minmax(0,1fr)}
  .sx-fields{grid-template-columns:minmax(0,1fr)}
  .scr-tools{gap:6px}
  .scr-tools input[type=search]{min-width:0;flex:1 1 100%}
  .sx-stats{grid-template-columns:repeat(4,minmax(0,1fr));gap:6px}
  .sx-stat{padding:5px 7px;font-size:10.5px}.sx-stat b{font-size:13px}
  table.sx td.sx-name{max-width:150px}
}`;
    document.head.append(s);
  }

  window.FU_PAGES.screener = page_;
  window.FU_SCREENER = { load, run: (spec, q) => run(SPECLIB.validate(spec, SCR.catalogue).spec, q), validate, fmtV, data: SCR };
})();
