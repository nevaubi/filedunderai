"use strict";
"use strict";
const CFG = window.FU_CONFIG || {};
const PUB = CFG.dataBase;
/* Global stores, filled lazily from the public data bucket. */
const D = { companies: {}, filings: {}, macro: {}, generated: "" };
const E = { ix: {}, releases: {}, news: {}, narratives: {}, macro_brief: null, macro_news: [] };
let TICKERS = [];
const IDX = {};
let INDEX = null, FEED = null, MACRO = null, MARKET = null;
const _inflight = {};
async function getJSON(path, { fresh = false } = {}) {
  const key = path;
  if (!fresh && _inflight[key]) return _inflight[key];
  const p = fetch(PUB + path, { cache: fresh ? "no-cache" : "default" }).then(r => { if (!r.ok) throw new Error(`${r.status} loading ${path}`); return r.json(); });
  _inflight[key] = p;
  p.catch(() => { delete _inflight[key]; });
  return p;
}
function applyIndex(ix) {
  INDEX = ix; D.generated = ix.generated;
  TICKERS = ix.companies.map(c => c.ticker);
  ix.companies.forEach(c => { IDX[c.ticker] = c; NAME[c.ticker] = c.legal_name || c.name; SHORT[c.ticker] = c.name; });
}
function applyMarket(m) {
  MARKET = m;
  for (const [s, v] of Object.entries(m.symbols || {})) {
    if (PX[s] && !PX[s].light) continue;
    const bars = v.bars.map(([t, c]) => ({ t: new Date(t * 1000), o: c, h: c, l: c, c, v: 0 }));
    if (bars.length) { const b = bars.at(-1); b.o = v.o ?? b.c; b.h = v.h ?? b.c; b.l = v.l ?? b.c; }
    PX[s] = { meta: {}, bars, light: true };
  }
}
function applyMacro(m) {
  MACRO = m;
  const ym = arr => (arr || []).map(([d, v]) => [d.slice(0, 7), v]);
  D.macro = { "TSY:curve_hdr": m.curve_hdr, "TSY:curve": m.curve, "FED:EFFR": m.effr || [], "FRED:CPIAUCSL": ym(m.fred?.CPIAUCSL), "FRED:CPILFESL": ym(m.fred?.CPILFESL), "FRED:UNRATE": ym(m.fred?.UNRATE), "FRED:PAYEMS": ym(m.fred?.PAYEMS) };
  E.macro_brief = m.brief ? { ...m.brief, meta: { at: m.brief.generated_at } } : null;
  E.macro_news = m.news || [];
}
async function ensurePrices(sym) {
  if (PX[sym] && !PX[sym].light) return PX[sym];
  try {
    const p = await getJSON(`prices/${sym.replace("^", "IDX_").replace("=", "_")}.json`);
    PX[sym] = { meta: p.meta || {}, bars: (p.d || []).map(b => ({ t: new Date(b[0] * 1000), o: b[1], h: b[2], l: b[3], c: b[4], v: b[5] })) };
    if (p.i?.length) PX[sym + "@5m"] = { meta: p.meta || {}, bars: p.i.map(b => ({ t: new Date(b[0] * 1000), o: b[1], h: b[2], l: b[3], c: b[4], v: b[5] })) };
  } catch (e) { console.warn("prices", sym, e.message); }
  return PX[sym];
}
async function ensureCompany(tk) {
  tk = String(tk || "").toUpperCase();
  if (!IDX[tk]) throw new Error(`${tk} is not in coverage`);
  if (D.companies[tk]?.statements) return D.companies[tk];
  const [b] = await Promise.all([getJSON(`companies/${tk}.json`), ensurePrices(tk), ensurePrices("SPY")]);
  const p = b.profile || {};
  D.companies[tk] = { ticker: tk, cik: b.cik, name: p.name, sic: p.sic, sicDescription: p.sicDescription, exchanges: p.exchanges, fiscalYearEnd: p.fiscalYearEnd || "1231", stateOfIncorporation: p.stateOfIncorporation, phone: p.phone, website: p.website, address: p.address, sharesOutstanding: p.sharesOutstanding, filings: (b.filings || []).map(f => ({ ...f })), statements: b.statements, earnings8k: b.earnings8k || [], sector: b.sector, generated: b.generated };
  Object.assign(D.filings, b.filingTables || {});
  Object.assign(E.ix, b.ix || {});
  if (b.release) E.releases[tk] = b.release;
  E.news[tk] = { news: (b.news?.items || []).map(x => ({ ...x })), fetched_at: b.news?.fetched_at };
  if (b.summary) E.narratives[tk] = { ...b.summary, meta: { at: b.summary.generated_at } };
  if (b.irUrl) IR[tk] = b.irUrl;
  if (p.website) WEB[tk] = p.website.replace(/^https?:\/\//, "").replace(/\/$/, "");
  delete CACHE[tk];
  return D.companies[tk];
}
const loaded = tk => !!D.companies[tk]?.statements;
const $ = (s, r = document) => r.querySelector(s);
const el = (tag, attrs = {}, ...kids) => {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") e.className = v;
    else if (k === "html") e.innerHTML = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (k === "style") e.setAttribute("style", v);
    else e.setAttribute(k, v === true ? "" : v);
  }
  for (const k of kids.flat()) if (k != null && k !== false) e.append(k.nodeType ? k : document.createTextNode(String(k)));
  return e;
};
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const store = {
  get(k, d) { try { const v = localStorage.getItem("fu:" + k); return v == null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem("fu:" + k, JSON.stringify(v)); } catch {} }
};

/* ---------- formatting ---------- */
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const pd = s => { const [y, m, d] = s.split("-").map(Number); return new Date(Date.UTC(y, m - 1, d || 1)); };
const fdate = s => { if (!s) return ""; const d = pd(s); return `${MONTHS[d.getUTCMonth()]} ${d.getUTCDate()}, ${d.getUTCFullYear()}`; };
const fmon = s => { const d = pd(s); return `${MONTHS[d.getUTCMonth()]} ${String(d.getUTCFullYear()).slice(2)}`; };
const dayDiff = (a, b) => Math.round((pd(b) - pd(a)) / 864e5);
function fmtMoney(v, unit) {
  if (v == null || !isFinite(v)) return "–";
  if (unit === "USD/shares") return (v < 0 ? "−$" : "$") + Math.abs(v).toFixed(2);
  if (unit === "shares") return (v / 1e6).toLocaleString("en-US", { maximumFractionDigits: 0 }) + "M";
  const a = Math.abs(v), s = v < 0 ? "−" : "";
  if (a >= 1e12) return s + "$" + (a / 1e12).toFixed(2) + "T";
  if (a >= 1e9) return s + "$" + (a / 1e9).toFixed(a >= 1e11 ? 1 : 2) + "B";
  if (a >= 1e6) return s + "$" + (a / 1e6).toFixed(0) + "M";
  return s + "$" + a.toLocaleString("en-US");
}
function fmtM(v, unit) { // statement view: millions
  if (v == null || !isFinite(v)) return "–";
  if (unit === "USD/shares") return v < 0 ? `(${Math.abs(v).toFixed(2)})` : v.toFixed(2);
  const m = v / 1e6;
  const s = Math.abs(m).toLocaleString("en-US", { maximumFractionDigits: 0 });
  return m < 0 ? `(${s})` : s;
}
const fpct = (v, d = 1) => v == null || !isFinite(v) ? "–" : (v * 100).toFixed(d) + "%";
const fsgn = (v, d = 1) => v == null || !isFinite(v) ? "–" : (v >= 0 ? "+" : "−") + Math.abs(v * 100).toFixed(d) + "%";
const fnum = (v, d = 2) => v == null || !isFinite(v) ? "–" : v.toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
const fx = v => v == null ? "–" : v.toFixed(1) + "×";
const cls = v => v > 0 ? "up" : v < 0 ? "down" : "";

/* ---------- prices ---------- */
const PX = {};
for (const [s, p] of Object.entries(D.prices)) PX[s] = { meta: p.meta, bars: p.bars.map(b => ({ t: new Date(b[0] * 1000), o: b[1], h: b[2], l: b[3], c: b[4], v: b[5] })) };
const SYMNAME = { SPY: "S&P 500", QQQ: "Nasdaq 100", IWM: "Russell 2000", DIA: "Dow 30", SMH: "Semiconductors", "^VIX": "VIX", "^TNX": "10Y yield", "^IRX": "13W T-bill", "^TYX": "30Y yield", "DX-Y.NYB": "US dollar", "CL=F": "WTI crude", "GC=F": "Gold", "HG=F": "Copper", "BTC-USD": "Bitcoin", XLK: "Technology", XLF: "Financials", XLE: "Energy", XLV: "Health care", XLY: "Cons. discretionary", XLP: "Cons. staples", XLI: "Industrials", XLU: "Utilities", XLB: "Materials", XLRE: "Real estate", XLC: "Communication" };
const SECTORS = ["XLK", "XLC", "XLY", "XLF", "XLI", "XLV", "XLP", "XLE", "XLU", "XLB", "XLRE"];
const last = s => PX[s]?.bars.at(-1);
const prev = s => PX[s]?.bars.at(-2);
const dayChg = s => { const a = last(s), b = prev(s); return a && b ? a.c / b.c - 1 : null; };
function retOver(s, days) {
  const b = PX[s]?.bars; if (!b || !b.length) return null;
  const end = b.at(-1), t0 = new Date(end.t - days * 864e5);
  let i = b.findIndex(x => x.t >= t0); if (i < 0) return null;
  return end.c / b[i].c - 1;
}
function ytd(s) {
  const b = PX[s]?.bars; if (!b) return null;
  const y = b.at(-1).t.getUTCFullYear();
  const lastPrev = [...b].reverse().find(x => x.t.getUTCFullYear() < y);
  return lastPrev ? b.at(-1).c / lastPrev.c - 1 : null;
}
const closes = s => PX[s]?.bars.map(b => b.c) || [];
function rets(arr) { const r = []; for (let i = 1; i < arr.length; i++) r.push(arr[i] / arr[i - 1] - 1); return r; }
function alignedRets(a, b, n) {
  const mb = new Map(PX[b].bars.map(x => [x.t.toISOString().slice(0, 10), x.c]));
  const pts = PX[a].bars.map(x => [x.c, mb.get(x.t.toISOString().slice(0, 10))]).filter(p => p[1] != null).slice(-(n + 1));
  const ra = [], rb = [];
  for (let i = 1; i < pts.length; i++) { ra.push(pts[i][0] / pts[i - 1][0] - 1); rb.push(pts[i][1] / pts[i - 1][1] - 1); }
  return [ra, rb];
}
const mean = a => a.reduce((s, x) => s + x, 0) / a.length;
const sd = a => { const m = mean(a); return Math.sqrt(a.reduce((s, x) => s + (x - m) ** 2, 0) / (a.length - 1)); };
function corr(a, b) { const ma = mean(a), mb = mean(b); let n = 0, da = 0, db = 0; for (let i = 0; i < a.length; i++) { n += (a[i] - ma) * (b[i] - mb); da += (a[i] - ma) ** 2; db += (b[i] - mb) ** 2; } return n / Math.sqrt(da * db); }
function beta(a, b) { const ma = mean(a), mb = mean(b); let c = 0, v = 0; for (let i = 0; i < a.length; i++) { c += (a[i] - ma) * (b[i] - mb); v += (b[i] - mb) ** 2; } return c / v; }
function sma(a, n) { return a.map((_, i) => i < n - 1 ? null : mean(a.slice(i - n + 1, i + 1))); }
function ema(a, n) { const k = 2 / (n + 1); const o = []; let e = null; a.forEach((x, i) => { e = e == null ? x : x * k + e * (1 - k); o.push(i < n - 1 ? null : e); }); return o; }
function rsi(a, n = 14) { const o = Array(a.length).fill(null); let g = 0, l = 0; for (let i = 1; i < a.length; i++) { const d = a[i] - a[i - 1]; if (i <= n) { g += Math.max(d, 0); l += Math.max(-d, 0); if (i === n) { g /= n; l /= n; o[i] = 100 - 100 / (1 + g / (l || 1e-9)); } } else { g = (g * (n - 1) + Math.max(d, 0)) / n; l = (l * (n - 1) + Math.max(-d, 0)) / n; o[i] = 100 - 100 / (1 + g / (l || 1e-9)); } } return o; }
function maxDD(a) { let pk = a[0], dd = 0; for (const x of a) { pk = Math.max(pk, x); dd = Math.min(dd, x / pk - 1); } return dd; }
function priceStats(s) {
  const b = PX[s]?.bars; if (!b) return null;
  const c = b.map(x => x.c), r = rets(c), y1 = b.filter(x => x.t > new Date(b.at(-1).t - 365 * 864e5));
  const hi = Math.max(...y1.map(x => x.h)), lo = Math.min(...y1.map(x => x.l));
  let bt = null, cr = null;
  if (s !== "SPY" && PX.SPY) { const [ra, rb] = alignedRets(s, "SPY", 252); bt = beta(ra, rb); cr = corr(ra, rb); }
  const tr = b.slice(-15).map((x, i, arr) => i ? Math.max(x.h - x.l, Math.abs(x.h - arr[i - 1].c), Math.abs(x.l - arr[i - 1].c)) : null).filter(x => x != null);
  return {
    symbol: s, last: c.at(-1), date: b.at(-1).t.toISOString().slice(0, 10), chg1d: dayChg(s), r1w: retOver(s, 7), r1m: retOver(s, 30), r3m: retOver(s, 91), ytd: ytd(s), r1y: retOver(s, 365),
    vol20: sd(r.slice(-20)) * Math.sqrt(252), vol60: sd(r.slice(-60)) * Math.sqrt(252), hi52: hi, lo52: lo, fromHi: c.at(-1) / hi - 1,
    beta1y: bt, corrSPY1y: cr, maxDD1y: maxDD(y1.map(x => x.c)), atr14: mean(tr), rsi14: rsi(c).at(-1),
    sma50: sma(c, 50).at(-1), sma200: sma(c, 200).at(-1), upDays1y: r.slice(-252).filter(x => x > 0).length / Math.min(252, r.length)
  };
}

/* ---------- fundamentals model ---------- */
const IR = {}, WEB = {}, NAME = {}, SHORT = {};
const LOGO_COLORS = ["#1D1D1F", "#2B4ACB", "#0E7C66", "#B4232A", "#7C3AED", "#B45309", "#0F6CBD", "#4E8A00", "#BE185D", "#334155"];
const logoColor = t => LOGO_COLORS[[...t].reduce((a, c) => a + c.charCodeAt(0), 0) % LOGO_COLORS.length];
const STMT_NAME = { income: "Income statement", balance: "Balance sheet", cashflow: "Cash flow statement", ratios: "Ratios & KPIs" };
const STMT_DOC = { income: "Consolidated Statements of Operations", balance: "Consolidated Balance Sheets", cashflow: "Consolidated Statements of Cash Flows" };
const TOTALS = new Set(["Gross profit", "Operating income", "Net income", "Total assets", "Total liabilities", "Liabilities & equity", "Operating cash flow", "Total current assets", "Total current liabilities", "Shareholders' equity"]);
const SUBS = new Set(["Research & development", "SG&A", "D&A", "Stock-based comp"]);
const CACHE = {};

function fyInfo(tk, end) {
  const fye = D.companies[tk].fiscalYearEnd || "1231";
  const fm = +fye.slice(0, 2), fd = +fye.slice(2);
  const e = pd(end);
  let fy = e.getUTCFullYear();
  const fyEndThis = Date.UTC(fy, fm - 1, fd);
  if (e.getTime() > fyEndThis + 10 * 864e5) fy += 1;
  const fyEnd = Date.UTC(fy, fm - 1, fd);
  const monthsLeft = Math.round((fyEnd - e.getTime()) / (30.44 * 864e5));
  const q = Math.max(1, Math.min(4, 4 - Math.round(monthsLeft / 3)));
  return { fy, q };
}
const plabel = (tk, end, kind) => { const { fy, q } = fyInfo(tk, end); return kind === "A" ? `FY${fy}` : `Q${q} FY${String(fy).slice(2)}`; };

function model(tk) {
  if (CACHE[tk]) return CACHE[tk];
  const c = D.companies[tk];
  const rows = {};
  const byLabel = {};
  for (const [stmt, list] of Object.entries(c.statements)) {
    rows[stmt] = list.map(r => {
      const mk = (arr, kind) => new Map(arr.map(rec => [rec.end, { v: rec.v, tk, stmt, label: r.label, concept: rec.c || r.concept, unit: r.unit, rec, kind, end: rec.end, start: rec.start, derived: rec.derived }]));
      const row = { stmt, label: r.label, concept: r.concept, unit: r.unit, A: mk(r.annual, "A"), Q: mk(r.quarterly, "Q") };
      if (!byLabel[r.label] || stmt !== "cashflow") byLabel[r.label] = row;
      return row;
    });
  }
  const periods = kind => {
    const s = new Set();
    for (const r of rows.income) for (const k of r[kind].keys()) s.add(k);
    if (!s.size) for (const r of rows.balance) for (const k of r[kind].keys()) s.add(k);
    return [...s].sort();
  };
  const near = (map, end, tol = 12) => { if (map.has(end)) return map.get(end); for (const [k, v] of map) if (Math.abs(dayDiff(k, end)) <= tol) return v; return null; };
  const get = (label, kind, end) => { const r = byLabel[label]; return r ? near(r[kind], end) : null; };
  const priorEnd = (kind, end) => { const ps = periods(kind); const tgt = pd(end).getTime() - 365 * 864e5; return ps.find(p => Math.abs(pd(p).getTime() - tgt) < 20 * 864e5) || null; };
  const m = { tk, c, rows, byLabel, periods, get, priorEnd, near };
  m.ratioDefs = [
    { label: "Revenue growth YoY", unit: "pct", f: (k, e) => { const pe = priorEnd(k, e); return ratio(get("Revenue", k, e), pe && get("Revenue", k, pe), "Revenue / Revenue (prior year) − 1", true); } },
    { label: "Gross margin", unit: "pct", f: (k, e) => { const gp = get("Gross profit", k, e); if (gp) return ratio(gp, get("Revenue", k, e), "Gross profit / Revenue"); const rv = get("Revenue", k, e), cg = get("Cost of revenue", k, e); return rv && cg ? { v: (rv.v - cg.v) / rv.v, calc: { formula: "(Revenue − Cost of revenue) / Revenue", inputs: [rv, cg] } } : null; } },
    { label: "Operating margin", unit: "pct", f: (k, e) => ratio(get("Operating income", k, e), get("Revenue", k, e), "Operating income / Revenue") },
    { label: "Net margin", unit: "pct", f: (k, e) => ratio(get("Net income", k, e), get("Revenue", k, e), "Net income / Revenue") },
    { label: "R&D % of revenue", unit: "pct", f: (k, e) => ratio(get("Research & development", k, e), get("Revenue", k, e), "R&D / Revenue") },
    { label: "Effective tax rate", unit: "pct", f: (k, e) => ratio(get("Income tax", k, e), get("Pretax income", k, e), "Income tax / Pretax income") },
    { label: "EPS growth YoY", unit: "pct", f: (k, e) => { const pe = priorEnd(k, e); return ratio(get("EPS diluted", k, e), pe && get("EPS diluted", k, pe), "EPS diluted / EPS diluted (prior year) − 1", true); } },
    { label: "Free cash flow", unit: "USD", f: (k, e) => { const o = get("Operating cash flow", k, e), x = get("Capex", k, e); return o && x ? { v: o.v - x.v, calc: { formula: "Operating cash flow − Capex", inputs: [o, x] } } : null; } },
    { label: "FCF margin", unit: "pct", f: (k, e) => { const o = get("Operating cash flow", k, e), x = get("Capex", k, e), r = get("Revenue", k, e); return o && x && r ? { v: (o.v - x.v) / r.v, calc: { formula: "(Operating cash flow − Capex) / Revenue", inputs: [o, x, r] } } : null; } },
    { label: "Capital returned", unit: "USD", f: (k, e) => { const b = get("Buybacks", k, e), d = get("Dividends paid", k, e); if (!b && !d) return null; return { v: (b?.v || 0) + (d?.v || 0), calc: { formula: "Buybacks + Dividends paid", inputs: [b, d].filter(Boolean) } }; } },
    { label: "Return on equity", unit: "pct", annualOnly: true, f: (k, e) => ratio(get("Net income", k, e), get("Shareholders' equity", k, e), "Net income / Shareholders' equity (period end)") },
    { label: "Current ratio", unit: "x", f: (k, e) => ratio(get("Total current assets", k, e), get("Total current liabilities", k, e), "Total current assets / Total current liabilities") },
    { label: "Debt to equity", unit: "x", f: (k, e) => ratio(get("Long-term debt", k, e), get("Shareholders' equity", k, e), "Long-term debt / Shareholders' equity") },
    { label: "Cash & securities", unit: "USD", f: (k, e) => { const a = get("Cash & equivalents", k, e), b = get("Marketable securities, current", k, e); if (!a) return null; return { v: a.v + (b?.v || 0), calc: { formula: "Cash & equivalents + Marketable securities, current", inputs: [a, b].filter(Boolean) } }; } },
  ];
  function ratio(a, b, formula, growth) { if (!a || !b || !b.v) return null; return { v: growth ? a.v / b.v - 1 : a.v / b.v, calc: { formula, inputs: [a, b] } }; }
  m.ratio = (label, kind, end) => { const d = m.ratioDefs.find(x => x.label === label); if (!d || (d.annualOnly && kind === "Q")) return null; const r = d.f(kind, end); if (!r) return null; return { ...r, tk, stmt: "ratios", label, unit: d.unit, kind, end }; };
  m.metric = (label, kind, end) => byLabel[label] ? get(label, kind, end) : m.ratio(label, kind, end);
  m.series = (label, kind) => m.periods(kind).map(e => ({ end: e, cell: m.metric(label, kind, e) })).filter(x => x.cell && x.cell.v != null);
  m.ttm = label => {
    const s = m.series(label, "Q").slice(-4);
    if (s.length < 4 || dayDiff(s[0].end, s[3].end) > 300) return null;
    const unit = s[0].cell.unit;
    if (unit === "pct" || unit === "x") return null;
    return { v: s.reduce((a, x) => a + x.cell.v, 0), tk, label, unit, kind: "TTM", end: s[3].end, calc: { formula: `Sum of last four quarters of ${label}`, inputs: s.map(x => x.cell) } };
  };
  m.latestQ = () => m.periods("Q").at(-1);
  m.latestA = () => m.periods("A").at(-1);
  CACHE[tk] = m;
  return m;
}
const METRIC_LABELS = ["Revenue", "Cost of revenue", "Gross profit", "Research & development", "SG&A", "Operating expenses", "Operating income", "Net interest income", "Other income (expense)", "Pretax income", "Income tax", "Net income", "EPS basic", "EPS diluted", "Diluted shares", "Cash & equivalents", "Marketable securities, current", "Accounts receivable", "Inventory", "Total current assets", "PP&E, net", "Goodwill", "Total assets", "Accounts payable", "Total current liabilities", "Long-term debt", "Deposits", "Total liabilities", "Shareholders' equity", "Liabilities & equity", "D&A", "Stock-based comp", "Operating cash flow", "Capex", "Investing cash flow", "Buybacks", "Dividends paid", "Financing cash flow", "Revenue growth YoY", "Gross margin", "Operating margin", "Net margin", "R&D % of revenue", "Effective tax rate", "EPS growth YoY", "Free cash flow", "FCF margin", "Capital returned", "Return on equity", "Current ratio", "Debt to equity", "Cash & securities"];
function allMetricLabels() { return METRIC_LABELS; }
function snapshot(tk) {
  const m = model(tk), c = D.companies[tk], px = last(tk)?.c;
  const sh = c.sharesOutstanding?.val;
  const mcap = px && sh ? px * sh : null;
  const eps = m.ttm("EPS diluted"), rev = m.ttm("Revenue"), ni = m.ttm("Net income"), ocf = m.ttm("Operating cash flow"), cap = m.ttm("Capex");
  const fcf = ocf && cap ? { v: ocf.v - cap.v, tk, label: "Free cash flow", unit: "USD", kind: "TTM", calc: { formula: "TTM operating cash flow − TTM capex", inputs: [ocf, cap] } } : null;
  const lq = m.latestQ();
  return { m, c, px, sh, mcap, eps, rev, ni, fcf, pe: eps && px ? px / eps.v : null, ps: mcap && rev ? mcap / rev.v : null, fcfy: fcf && mcap ? fcf.v / mcap : null,
    gm: lq && m.ratio("Gross margin", "Q", lq), growth: lq && m.ratio("Revenue growth YoY", "Q", lq), nm: ni && rev ? ni.v / rev.v : null };
}

/* ---------- tooltip ---------- */
const tip = $("#tip");
function showTip(ev, html) { tip.innerHTML = html; tip.hidden = false; const w = tip.offsetWidth, h = tip.offsetHeight; let x = ev.clientX + 14, y = ev.clientY + 14; if (x + w > innerWidth - 8) x = ev.clientX - w - 14; if (y + h > innerHeight - 8) y = ev.clientY - h - 14; tip.style.left = x + "px"; tip.style.top = y + "px"; }
const hideTip = () => { tip.hidden = true; };

/* ---------- charts ---------- */
function sparkSVG(vals, color, w = 100, h = 24) {
  const v = vals.filter(x => x != null && isFinite(x)); if (v.length < 2) return el("span");
  const mn = Math.min(...v), mx = Math.max(...v), sx = i => (i / (v.length - 1)) * (w - 2) + 1, sy = x => h - 2 - ((x - mn) / (mx - mn || 1)) * (h - 4);
  const d = v.map((x, i) => (i ? "L" : "M") + sx(i).toFixed(1) + " " + sy(x).toFixed(1)).join("");
  const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  s.setAttribute("viewBox", `0 0 ${w} ${h}`); s.setAttribute("preserveAspectRatio", "none");
  s.innerHTML = `<path d="${d} L${sx(v.length - 1)} ${h} L1 ${h}Z" fill="${color}" opacity=".10"/><path d="${d}" fill="none" stroke="${color}" stroke-width="1.4" vector-effect="non-scaling-stroke"/><circle cx="${sx(v.length - 1)}" cy="${sy(v.at(-1))}" r="1.8" fill="${color}"/>`;
  return s;
}
function lineChart(host, series, o = {}) {
  host.innerHTML = "";
  const W = host.clientWidth || 600, H = o.height || 240, m = { t: 12, r: o.right ?? 52, b: 26, l: o.left ?? 10 };
  const all = series.flatMap(s => s.pts.filter(p => p[1] != null && isFinite(p[1])));
  if (!all.length) { host.append(el("div", { class: "empty" }, "No data")); return; }
  const x = (o.band ? d3.scalePoint().domain(o.band).padding(.3) : d3.scaleUtc().domain(d3.extent(all, p => p[0]))).range([m.l, W - m.r]);
  let [y0, y1] = d3.extent(all, p => p[1]); if (o.zero) { y0 = Math.min(0, y0); y1 = Math.max(0, y1); }
  const pad = (y1 - y0) * .08 || 1; const y = d3.scaleLinear().domain([o.zero && y0 === 0 ? 0 : y0 - pad, y1 + pad]).nice().range([H - m.b, m.t]);
  const svg = d3.select(host).append("svg").attr("viewBox", `0 0 ${W} ${H}`).attr("height", H);
  svg.append("g").attr("class", "grid").attr("transform", `translate(${W - m.r},0)`).call(d3.axisRight(y).ticks(5).tickSize(-(W - m.r - m.l)).tickFormat(o.yfmt || d3.format("~s"))).call(g => g.select(".domain").remove()).call(g => g.selectAll("text").attr("x", 6));
  svg.append("g").attr("transform", `translate(0,${H - m.b})`).call(o.band ? d3.axisBottom(x) : d3.axisBottom(x).ticks(Math.max(2, Math.floor(W / 110))).tickSizeOuter(0));
  if (o.zero || (y.domain()[0] < 0 && y.domain()[1] > 0)) svg.append("line").attr("x1", m.l).attr("x2", W - m.r).attr("y1", y(0)).attr("y2", y(0)).attr("stroke", css("--line2"));
  (o.marks || []).forEach(mk => { const xx = x(mk.t); if (xx == null || xx < m.l || xx > W - m.r) return; svg.append("line").attr("x1", xx).attr("x2", xx).attr("y1", m.t).attr("y2", H - m.b).attr("stroke", css("--c3")).attr("stroke-dasharray", "2 3").attr("opacity", .8); svg.append("text").attr("x", xx + 3).attr("y", m.t + 9).text(mk.label).style("fill", css("--c3")).style("font-size", "9.5px"); });
  series.forEach((s, i) => {
    const col = s.color || css(`--c${(i % 6) + 1}`);
    const pts = s.pts.filter(p => p[1] != null && isFinite(p[1]));
    const line = d3.line().x(p => x(p[0])).y(p => y(p[1])).curve(o.step ? d3.curveStepAfter : d3.curveMonotoneX);
    if (o.area && i === 0 && series.length === 1) svg.append("path").attr("d", d3.area().x(p => x(p[0])).y0(y(Math.max(y.domain()[0], 0))).y1(p => y(p[1])).curve(d3.curveMonotoneX)(pts)).attr("fill", col).attr("opacity", .09);
    svg.append("path").attr("d", line(pts)).attr("fill", "none").attr("stroke", col).attr("stroke-width", s.width || 1.7).attr("stroke-dasharray", s.dash || null).attr("opacity", s.opacity || 1);
    if (o.dots) svg.selectAll(null).data(pts).join("circle").attr("cx", p => x(p[0])).attr("cy", p => y(p[1])).attr("r", 3).attr("fill", col);
    const lp = pts.at(-1); if (lp && !o.noEnd) { svg.append("circle").attr("cx", x(lp[0])).attr("cy", y(lp[1])).attr("r", 3).attr("fill", col); }
  });
  const fmt = o.tfmt || o.yfmt || d3.format(",.2f");
  const focus = svg.append("g").style("display", "none");
  focus.append("line").attr("y1", m.t).attr("y2", H - m.b).attr("stroke", css("--faint")).attr("stroke-dasharray", "2 2");
  svg.append("rect").attr("x", m.l).attr("y", m.t).attr("width", W - m.l - m.r).attr("height", H - m.t - m.b).attr("fill", "transparent")
    .on("mousemove", ev => {
      const [mx] = d3.pointer(ev);
      let xv;
      if (o.band) { const dom = o.band; const step = (W - m.r - m.l) / dom.length; xv = dom[Math.max(0, Math.min(dom.length - 1, Math.round((mx - x(dom[0])) / x.step())))]; }
      else xv = x.invert(mx);
      const rowsHtml = series.map((s, i) => {
        const pts = s.pts.filter(p => p[1] != null);
        let best = null, bd = Infinity; for (const p of pts) { const dd = o.band ? (p[0] === xv ? 0 : Infinity) : Math.abs(p[0] - xv); if (dd < bd) { bd = dd; best = p; } }
        return best ? `<div><span style="color:${s.color || css(`--c${(i % 6) + 1}`)}">●</span> ${esc(s.name || "")} <b>${fmt(best[1])}</b></div>` : "";
      }).join("");
      const lx = x(xv); focus.style("display", null).select("line").attr("x1", lx).attr("x2", lx);
      showTip(ev, `<div style="opacity:.7">${o.band ? esc(xv) : d3.utcFormat(o.intraday ? "%b %d %H:%M" : "%b %d, %Y")(xv)}</div>${rowsHtml}`);
    }).on("mouseleave", () => { focus.style("display", "none"); hideTip(); })
    .on("click", ev => { if (!o.onClick) return; const [mx] = d3.pointer(ev); o.onClick(o.band ? o.band[Math.max(0, Math.min(o.band.length - 1, Math.round((mx - x(o.band[0])) / x.step())))] : x.invert(mx)); });
  if (o.legend !== false && series.length > 1) {
    const lg = el("div", { class: "legend", style: "margin-top:6px" }, series.map((s, i) => el("span", {}, el("i", { style: `background:${s.color || css(`--c${(i % 6) + 1}`)}` }), s.name)));
    host.append(lg);
  }
}
function barChart(host, data, o = {}) {
  host.innerHTML = "";
  const W = host.clientWidth || 600, H = o.height || 200, m = { t: 14, r: 48, b: 26, l: 6 };
  const x = d3.scaleBand().domain(data.map(d => d.k)).range([m.l, W - m.r]).padding(.22);
  const vals = data.flatMap(d => d.vals.map(v => v.v)).filter(v => v != null);
  const y = d3.scaleLinear().domain([Math.min(0, d3.min(vals)), Math.max(0, d3.max(vals))]).nice().range([H - m.b, m.t]);
  const svg = d3.select(host).append("svg").attr("viewBox", `0 0 ${W} ${H}`).attr("height", H);
  svg.append("g").attr("class", "grid").attr("transform", `translate(${W - m.r},0)`).call(d3.axisRight(y).ticks(4).tickSize(-(W - m.r - m.l)).tickFormat(o.yfmt || (v => fmtMoney(v)))).call(g => g.select(".domain").remove()).call(g => g.selectAll("text").attr("x", 6));
  const every = Math.ceil(data.length / Math.max(2, Math.floor(W / 64)));
  svg.append("g").attr("transform", `translate(0,${H - m.b})`).call(d3.axisBottom(x).tickValues(data.map(d => d.k).filter((_, i) => (data.length - 1 - i) % every === 0)).tickSizeOuter(0));
  const nser = data[0]?.vals.length || 1; const sub = d3.scaleBand().domain(d3.range(nser)).range([0, x.bandwidth()]).padding(.08);
  data.forEach((d, i) => d.vals.forEach((v, j) => {
    if (v.v == null) return;
    const col = v.color || css(`--c${j + 1}`);
    svg.append("rect").attr("x", x(d.k) + sub(j)).attr("width", sub.bandwidth()).attr("y", y(Math.max(0, v.v))).attr("height", Math.abs(y(v.v) - y(0))).attr("rx", 1.5)
      .attr("fill", col).attr("opacity", i === data.length - 1 ? 1 : .78).style("cursor", v.cell ? "pointer" : null)
      .on("mousemove", ev => showTip(ev, `<div style="opacity:.7">${esc(d.k)}</div>${esc(v.name || "")} <b>${(o.tfmt || o.yfmt || fmtMoney)(v.v)}</b>${v.cell ? '<div style="opacity:.7;margin-top:2px">Click for source</div>' : ""}`))
      .on("mouseleave", hideTip).on("click", () => v.cell && (o.onBar ? o.onBar(v.cell) : openSource(v.cell)));
  }));
  if (o.legend) host.append(el("div", { class: "legend", style: "margin-top:6px" }, o.legend.map((n, j) => el("span", {}, el("i", { style: `background:${css(`--c${j + 1}`)}` }), n))));
}

/* ---------- toasts ---------- */
function toast(msg) { const t = el("div", { class: "toast" }, msg); $("#toasts").append(t); setTimeout(() => t.remove(), 2600); }
async function copyText(text, label) { try { await navigator.clipboard.writeText(text); toast(label || "Copied"); } catch { const ta = el("textarea", { style: "position:fixed;left:-9999px" }, text); document.body.append(ta); ta.select(); try { document.execCommand("copy"); toast(label || "Copied"); } catch { toast("Select and copy the text manually"); } ta.remove(); } }

/* ---------- source drawer ---------- */
const drawer = $("#drawer"), scrim = $("#scrim");
const DR = { cell: null, tab: "filing" };
function closeSource() { drawer.classList.remove("open"); drawer.setAttribute("aria-hidden", "true"); scrim.hidden = true; DR.cell = null; document.querySelectorAll("td.c.sel").forEach(x => x.classList.remove("sel")); }
scrim.addEventListener("click", closeSource);
const formCls = f => /^10-K/.test(f) ? "k" : /^10-Q/.test(f) ? "q" : /^8-K/.test(f) ? "e" : /^4$|^144|^3$|^5$/.test(f) ? "i" : "";
function colHeader(key) {
  if (key.includes("|")) { const [a, b] = key.split("|"); const mo = Math.round(dayDiff(a, b) / 30.44); return [`${mo} mo. ended`, fdate(b)]; }
  return ["As of", fdate(key)];
}
const fmtCell = c => c.unit === "pct" ? fpct(c.v) : c.unit === "x" ? fx(c.v) : fmtMoney(c.v, c.unit);
const periodText = c => c.kind === "TTM" ? "TTM to " + fdate(c.end) : plabel(c.tk, c.end, c.kind);
function siblings(cell) {
  if (!cell || cell.kind === "TTM" || !cell.end) return [];
  return model(cell.tk).series(cell.label, cell.kind).map(x => x.cell);
}
function citation(cell) {
  if (cell.calc) return `${cell.tk} ${cell.label}, ${periodText(cell)}: ${fmtCell(cell)}. Calculated as ${cell.calc.formula} from SEC filings (Filed Under).`;
  const f = D.filings[cell.rec.accn];
  return `${cell.tk} ${cell.label}, ${periodText(cell)}: ${fmtCell(cell)}${cell.derived ? " (derived from cumulative figures)" : ""}. Source: ${cell.rec.form} filed ${fdate(cell.rec.filed)}, accession ${cell.rec.accn}, us-gaap:${cell.concept}. ${f?.primary || f?.index || ""}`;
}
function openSource(cell, tab) {
  if (!cell) return;
  hideTip();
  DR.cell = cell; if (tab) DR.tab = tab;
  if (cell.calc && DR.tab === "filing") DR.tab = "filing";
  const dh = $("#dh"), db = $("#db");
  dh.innerHTML = ""; db.innerHTML = "";
  const sib = siblings(cell), idx = sib.findIndex(x => x.end === cell.end);
  const nav = el("div", { class: "pnav" },
    el("button", { "aria-label": "Previous period", title: "Previous period (←)", disabled: idx <= 0, onclick: () => openSource(sib[idx - 1]) }, svgUse("i-left")),
    el("button", { "aria-label": "Next period", title: "Next period (→)", disabled: idx < 0 || idx >= sib.length - 1, onclick: () => openSource(sib[idx + 1]) }, svgUse("i-right")));
  const f = cell.rec ? D.filings[cell.rec.accn] : null;
  dh.append(
    el("div", { class: "row" }, el("span", { class: "eyebrow" }, cell.calc ? "Calculated value" : cell.derived ? "Derived from filing" : "Filed value"),
      el("div", { class: "x", style: "display:flex;gap:6px" },
        el("button", { class: "btn", title: "Copy citation (c)", onclick: () => copyText(citation(cell), "Citation copied") }, svgUse("i-copy"), "Cite"),
        f ? el("a", { class: "btn", href: f.primary || f.index, target: "_blank", rel: "noopener" }, "EDGAR", svgUse("i-ext")) : null,
        el("button", { class: "iconbtn", onclick: closeSource, "aria-label": "Close source (Esc)" }, svgUse("i-x")))),
    el("div", { class: "row" }, el("h2", {}, `${SHORT[cell.tk] || cell.tk} · ${cell.label}`)),
    el("div", { class: "row" }, el("span", { class: "chip acc mono", style: "font-size:13.5px;height:26px" }, fmtCell(cell)), el("span", { class: "muted", style: "font-size:12.5px" }, periodText(cell) + (cell.start ? ` · ${fdate(cell.start)} – ${fdate(cell.end)}` : cell.end && cell.kind !== "TTM" ? ` · as of ${fdate(cell.end)}` : "")), el("span", { style: "margin-left:auto" }, sib.length ? nav : null)));
  const tabs = el("div", { class: "dtabs", role: "tablist" });
  const T = [["filing", cell.calc ? "Calculation" : "Filing"], ["details", "Details"], ["history", "History"]];
  T.forEach(([id, n]) => tabs.append(el("button", { class: DR.tab === id ? "on" : "", role: "tab", "aria-selected": DR.tab === id ? "true" : "false", onclick: () => openSource(cell, id) }, n)));
    db.append(tabs);
  const body = el("div", { style: "display:flex;flex-direction:column;gap:16px;padding-top:14px" });
  db.append(body);
  if (DR.tab === "history") renderHistory(body, cell, sib);
  else if (DR.tab === "details") renderDetails(body, cell);
  else if (cell.calc) renderCalc(body, cell);
  else renderFiling(body, cell, D.companies[cell.tk]);
  drawer.classList.add("open"); drawer.setAttribute("aria-hidden", "false");
  if (innerWidth < 860) scrim.hidden = false;
}
function svgUse(id) { const s = document.createElementNS("http://www.w3.org/2000/svg", "svg"); s.innerHTML = `<use href="#${id}"/>`; return s; }
function renderCalc(body, cell) {
  const box = el("div", { class: "calc-box" }, el("div", { class: "eyebrow" }, "Formula"), el("div", { class: "eq" }, cell.calc.formula));
  cell.calc.inputs.forEach(inp => box.append(el("div", { class: "in", onclick: () => openSource(inp, "filing"), title: "Open the source of this input" },
    el("span", {}, el("b", {}, inp.label), " ", el("span", { class: "muted" }, periodText(inp))), el("span", { class: "mono" }, fmtCell(inp)))));
  body.append(box, el("div", { class: "note" }, "Each input links to the filing it came from. Open one to see the reported line highlighted in the original statement."));
}
function renderDetails(body, cell) {
  const c = D.companies[cell.tk];
  if (cell.calc) { body.append(el("dl", { class: "kv" }, el("dt", {}, "Company"), el("dd", {}, c.name), el("dt", {}, "Metric"), el("dd", {}, cell.label), el("dt", {}, "Period"), el("dd", {}, periodText(cell)), el("dt", {}, "Formula"), el("dd", { class: "mono" }, cell.calc.formula), el("dt", {}, "Inputs"), el("dd", {}, cell.calc.inputs.length + " filed values"))); return; }
  const rec = cell.rec, f = D.filings[rec.accn];
  body.append(el("dl", { class: "kv" },
    el("dt", {}, "Company"), el("dd", {}, `${c.name} · CIK ${c.cik}`),
    el("dt", {}, "Form"), el("dd", {}, el("span", { class: "form " + formCls(rec.form) }, rec.form)),
    el("dt", {}, "Accession no."), el("dd", { class: "mono" }, rec.accn),
    el("dt", {}, "Filed"), el("dd", {}, fdate(rec.filed)),
    el("dt", {}, "Period"), el("dd", {}, rec.start ? `${fdate(rec.start)} – ${fdate(rec.end)} (${dayDiff(rec.start, rec.end)} days)` : `Instant, ${fdate(rec.end)}`),
    el("dt", {}, "XBRL element"), el("dd", { class: "mono" }, "us-gaap:" + cell.concept),
    el("dt", {}, "Reported value"), el("dd", { class: "mono" }, rec.v.toLocaleString("en-US") + " " + cell.unit),
    el("dt", {}, "Selection rule"), el("dd", {}, "First filing that reported this period. Later restatements are kept in the facts store."),
    el("dt", {}, "Links"), el("dd", {}, f?.primary ? el("a", { href: f.primary, target: "_blank", rel: "noopener" }, "Document ↗") : "–", " · ", el("a", { href: f?.index || "#", target: "_blank", rel: "noopener" }, "Filing index ↗"), " · ", el("a", { href: `https://data.sec.gov/api/xbrl/companyconcept/CIK${String(c.cik).padStart(10, "0")}/us-gaap/${cell.concept}.json`, target: "_blank", rel: "noopener" }, "Concept API ↗"))));
  if (cell.derived) {
    const dv = cell.derived;
    body.append(el("div", { class: "calc-box" },
      el("div", { class: "eyebrow" }, "Derived quarter"),
      el("div", {}, "Not reported on its own. Computed as the cumulative figure in this filing minus the cumulative figure for the prior period."),
      el("div", { class: "eq" }, `${fmtM(dv.from.v)} (${Math.round(dayDiff(dv.from.start, dv.from.end) / 30.44)} mo. to ${fdate(dv.from.end)}) − ${fmtM(dv.minus.v)} (${Math.round(dayDiff(dv.minus.start, dv.minus.end) / 30.44)} mo. to ${fdate(dv.minus.end)}) = ${fmtM(cell.v)}`),
      el("div", { class: "in", onclick: () => openSource({ ...cell, v: dv.minus.v, derived: null, rec: { v: dv.minus.v, accn: dv.minus.accn, form: dv.minus.form, filed: D.filings[dv.minus.accn]?.filed || "", start: dv.minus.start, end: dv.minus.end }, start: dv.minus.start, end: dv.minus.end, kind: "Q" }, "filing") },
        el("span", {}, "Subtracted figure from ", el("b", { class: "mono" }, dv.minus.form + " " + dv.minus.accn)), el("span", { class: "mono" }, fmtM(dv.minus.v)))));
  }
}
function renderHistory(body, cell, sib) {
  const host = el("div", { style: "min-height:220px" });
  const recent = sib.slice(-16);
  body.append(el("div", { class: "eyebrow" }, `${cell.label}, ${cell.kind === "A" ? "annual" : "quarterly"}`), host);
  requestAnimationFrame(() => barChart(host, recent.map(c => ({ k: plabel(c.tk, c.end, c.kind), vals: [{ v: c.v, name: cell.label, cell: c, color: c.end === cell.end ? css("--c3") : css("--c1") }] })), { height: 220, yfmt: cell.unit === "pct" ? v => fpct(v, 0) : cell.unit === "x" ? v => v.toFixed(1) : v => fmtMoney(v, cell.unit), tfmt: cell.unit === "pct" ? v => fpct(v) : v => fmtMoney(v, cell.unit), onBar: c => openSource(c, "filing") }));
  const t = el("table", { class: "t" }, el("thead", {}, el("tr", {}, el("th", {}, "Period"), el("th", { class: "r" }, "Value"), el("th", { class: "r" }, "Change"), el("th", {}, "Source"))));
  const tb = el("tbody");
  recent.slice().reverse().forEach((c, i, arr) => { const p = arr[i + 1]; const ch = p && p.v ? c.v / Math.abs(p.v) - Math.sign(p.v) : null; tb.append(el("tr", { class: "click", onclick: () => openSource(c, "filing") }, el("td", { class: c.end === cell.end ? "" : "muted" }, el("b", {}, plabel(c.tk, c.end, c.kind))), el("td", { class: "r num" }, fmtCell(c)), el("td", { class: "r num " + cls(ch) }, cell.unit === "pct" ? (p ? ((c.v - p.v) * 1e4 >= 0 ? "+" : "−") + Math.abs((c.v - p.v) * 1e4).toFixed(0) + " bp" : "") : fsgn(ch)), el("td", { class: "mono faint", style: "font-size:11.5px" }, c.calc ? "calc" : `${c.rec.form}${c.derived ? " · derived" : ""}`))); });
  t.append(tb); body.append(t);
}
function renderFiling(db, cell, c) {
  const rec = cell.rec, f = D.filings[rec.accn];
  const targetKey = cell.stmt === "balance" ? rec.end : `${rec.start}|${rec.end}`;
  if (cell.derived) db.append(el("div", { class: "note" }, "This quarter is derived: the filing reports a cumulative period. The highlighted figure below is the cumulative value it starts from. See Details for the subtraction."));
  const ixDoc = E?.ix?.[rec.accn]?.tables?.[cell.stmt];
  const lookKey = cell.derived ? `${cell.derived.from.start}|${cell.derived.from.end}` : targetKey;
  if (ixDoc) {
    const doc = el("div", { class: "doc ix" });
    doc.append(el("div", { class: "dt" }, el("div", { class: "co" }, NAME[cell.tk] || c.name), el("div", { class: "st" }, STMT_DOC[cell.stmt]), el("div", { class: "un" }, "(In millions, except per-share amounts)")), el("div", { class: "tw", html: ixDoc }));
    db.append(el("div", { style: "display:flex;gap:8px;align-items:center;flex-wrap:wrap" }, el("span", { class: "badge-src" }, "Original filing"), el("span", { class: "eyebrow" }, `${rec.form} · ${STMT_DOC[cell.stmt]}`), el("a", { href: E.ix[rec.accn].url, target: "_blank", rel: "noopener", style: "margin-left:auto;font-size:12px" }, "Full document ↗")), doc,
      el("div", { class: "note" }, "This table is cut from the filed HTML document. Every tagged number keeps its inline XBRL element and period, so the panel highlights the exact fact behind the value. Hover any number to see its tag."));
    let hit = doc.querySelector(`span[data-ix="us-gaap:${CSS.escape(cell.concept)}"][data-p="${CSS.escape(lookKey)}"]`);
    if (!hit) { const want = fmtM(cell.derived ? cell.derived.from.v : rec.v, cell.unit).replace(/[()]/g, ""); hit = [...doc.querySelectorAll("span[data-ix]")].find(s => s.dataset.p === lookKey && s.textContent.replace(/[()$,\s]/g, "") === want.replace(/[,\s]/g, "")); }
    doc.querySelectorAll("span[data-ix]").forEach(s => { s.title = `${s.dataset.ix} · ${s.dataset.p.replace("|", " to ")}`; });
    if (hit) { hit.classList.add("hit"); hit.closest("tr")?.classList.add("hl"); requestAnimationFrame(() => hit.scrollIntoView({ block: "center", inline: "center", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" })); }
    return;
  }
  const st = f?.statements?.[cell.stmt];
  if (!st) { db.append(el("div", { class: "note" }, "This filing's statement table is not in the prototype bundle. Use the EDGAR link above.")); return; }
  const doc = el("div", { class: "doc" });
  doc.append(el("div", { class: "dt" }, el("div", { class: "co" }, NAME[cell.tk] || c.name), el("div", { class: "st" }, STMT_DOC[cell.stmt]), el("div", { class: "un" }, "(In millions, except per-share amounts)")));
  const cols = st.cols.slice(0, 6);
  if (!cols.includes(targetKey) && st.cols.includes(targetKey)) cols[cols.length - 1] = targetKey;
  const tbl = el("table");
  tbl.append(el("thead", {}, el("tr", {}, el("th", {}, ""), cols.map(k => { const [a, b] = colHeader(k); return el("th", {}, a, el("br"), b); }))));
  const tb = el("tbody");
  let hit = null;
  st.rows.forEach(r => {
    const tr = el("tr", { class: r.concept === cell.concept ? "hl" : "" }, el("td", {}, r.label));
    cols.forEach(k => {
      const v = r.vals[k];
      const td = el("td", {}, v == null ? "" : r.unit === "USD/shares" ? (v < 0 ? `(${Math.abs(v).toFixed(2)})` : v.toFixed(2)) : r.unit === "shares" ? (v / 1e6).toLocaleString("en-US", { maximumFractionDigits: 0 }) : fmtM(v));
      if (r.concept === cell.concept && (k === targetKey || k === lookKey)) { td.className = "hit"; hit = td; }
      tr.append(td);
    });
    tb.append(tr);
  });
  tbl.append(tb);
  doc.append(el("div", { class: "tw" }, tbl));
  db.append(el("div", { style: "display:flex;gap:8px;align-items:center" }, el("span", { class: "badge-src alt" }, "Rebuilt from XBRL"), el("span", { class: "eyebrow" }, `${rec.form} · ${STMT_DOC[cell.stmt]}`)), doc,
    el("div", { class: "note" }, "Older filings are rebuilt from the XBRL facts in the filing. The latest 10-K and two 10-Qs per company show the original filed table."));
  requestAnimationFrame(() => hit?.scrollIntoView({ block: "center", inline: "center", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" }));
}

/* ---------- assistant ---------- */
const AI_ERR = { rate_limited: "You've hit the request limit for now. Try again in a few minutes.", too_large: "That request carried too much data. Ask about fewer companies at a time.", upstream_error: "The assistant didn't respond. Try again.", forbidden: "The assistant is only available on filedunder.ai." };
function findCell(tk, label, period) {
  tk = String(tk || "").toUpperCase(); if (!D.companies[tk]) return null;
  const m = model(tk); period = String(period || "").trim().toUpperCase();
  if (period === "TTM") return m.ttm(label);
  const kind = /^Q/.test(period) || period === "LQ" ? "Q" : "A";
  const ends = m.periods(kind);
  let end = null;
  if (period === "LQ") end = m.latestQ(); else if (period === "LA" || period === "LFY") end = m.latestA();
  else end = ends.find(e => plabel(tk, e, kind).toUpperCase() === period.replace(/\s+/g, " ")) || null;
  if (!end) return null;
  const lab = m.byLabel[label] ? label : (m.ratioDefs.find(d => d.label.toLowerCase() === String(label).toLowerCase())?.label || Object.keys(m.byLabel).find(l => l.toLowerCase() === String(label).toLowerCase()));
  return lab ? m.metric(lab, kind, end) : null;
}
async function findCellAsync(tk, label, period) { try { await ensureCompany(tk); } catch { return null; } return findCell(tk, label, period); }
function compactSeries(tk, label, kind, n) {
  const m = model(tk);
  return m.series(label, kind).slice(-n).map(x => ({ period: plabel(tk, x.end, kind), end: x.end, value: x.cell.unit === "pct" ? +(x.cell.v * 100).toFixed(2) : x.cell.v, unit: x.cell.unit === "pct" ? "%" : x.cell.unit, source: x.cell.calc ? "calculated" : `${x.cell.rec.form} ${x.cell.rec.accn}${x.cell.derived ? " (derived)" : ""}` }));
}
function runJS(code, timeout = 4000) {
  return new Promise(resolve => {
    const data = { companies: {}, prices: {}, universe: (INDEX?.companies || []).map(c => ({ ticker: c.ticker, name: c.name, sector: c.sector, price: c.price, market_cap: c.market_cap, pe_ttm: c.pe_ttm, ps_ttm: c.ps_ttm, revenue_ttm: c.revenue_ttm, net_income_ttm: c.net_income_ttm, fcf_ttm: c.fcf_ttm, fcf_yield: c.fcf_yield, gross_margin_lq: c.gross_margin_lq, op_margin_lq: c.op_margin_lq, rev_growth_lq: c.rev_growth_lq, dividend_yield: c.dividend_yield, ret_ytd: c.ret_ytd, ret_1y: c.ret_1y, beta_1y: c.beta_1y })) };
    for (const t of Object.keys(D.companies).filter(loaded)) { const m = model(t); data.companies[t] = { name: SHORT[t], annual: {}, quarterly: {} }; for (const l of allMetricLabels()) { if (!m.byLabel[l] && !m.ratioDefs.find(d => d.label === l)) continue; data.companies[t].annual[l] = m.series(l, "A").map(x => [plabel(t, x.end, "A"), x.end, x.cell.v]); data.companies[t].quarterly[l] = m.series(l, "Q").map(x => [plabel(t, x.end, "Q"), x.end, x.cell.v]); } }
    for (const s of Object.keys(PX)) if (!s.includes("@")) data.prices[s] = PX[s].bars.map(b => [b.t.toISOString().slice(0, 10), b.c]);
    const src = `const data=${JSON.stringify(data)};onmessage=async e=>{try{const fn=new Function("data",'"use strict";'+e.data);const r=await fn(data);postMessage({ok:true,result:JSON.stringify(r===undefined?null:r).slice(0,12000)})}catch(err){postMessage({ok:false,error:String(err&&err.message||err)})}}`;
    let w;
    try { w = new Worker(URL.createObjectURL(new Blob([src], { type: "text/javascript" }))); } catch (e) { resolve({ ok: false, error: "Workers unavailable: " + e.message }); return; }
    const t = setTimeout(() => { w.terminate(); resolve({ ok: false, error: "Timed out after " + timeout + " ms" }); }, timeout);
    w.onmessage = e => { clearTimeout(t); w.terminate(); resolve(e.data); };
    w.onerror = e => { clearTimeout(t); w.terminate(); resolve({ ok: false, error: e.message }); };
    w.postMessage(String(code));
  });
}
function baseTools(trace) {
  const wrap = (name, fn) => async (input, ctx) => { const st = trace(name, input); try { const r = await fn(input || {}, ctx); st.done(r); return r; } catch (e) { st.fail(e); throw e; } };
  const resolveLabel = (m, lab) => m.byLabel[lab] ? lab : (m.ratioDefs.find(d => d.label.toLowerCase() === String(lab).toLowerCase())?.label || Object.keys(m.byLabel).find(l => l.toLowerCase() === String(lab).toLowerCase()));
  return [
    { name: "screen_companies", description: "Screen the covered universe (S&P 100). Returns ticker, name, sector, price, market cap, P/E, P/S, TTM revenue/net income/FCF, FCF yield, latest-quarter gross and operating margin, revenue growth YoY, dividend yield, YTD and 1Y return, beta. Optional filters: sector (exact name), sort (field name), desc (bool), limit.", inputSchema: { type: "object", properties: { sector: { type: "string" }, sort: { type: "string" }, desc: { type: "boolean" }, limit: { type: "number" } } },
      execute: wrap("screen_companies", i => { let rows = (INDEX?.companies || []).filter(c => !i.sector || c.sector === i.sector); if (i.sort) rows = rows.filter(c => c[i.sort] != null).sort((a, b) => (i.desc === false ? 1 : -1) * (a[i.sort] - b[i.sort])); return rows.slice(0, Math.min(102, Number(i.limit) || 25)).map(c => ({ ticker: c.ticker, name: c.name, sector: c.sector, price: c.price, market_cap: c.market_cap, pe_ttm: c.pe_ttm && +c.pe_ttm.toFixed(1), ps_ttm: c.ps_ttm && +c.ps_ttm.toFixed(1), revenue_ttm: c.revenue_ttm, net_income_ttm: c.net_income_ttm, fcf_ttm: c.fcf_ttm, fcf_yield: c.fcf_yield && +(c.fcf_yield * 100).toFixed(2), gross_margin_lq: c.gross_margin_lq && +(c.gross_margin_lq * 100).toFixed(1), op_margin_lq: c.op_margin_lq && +(c.op_margin_lq * 100).toFixed(1), rev_growth_lq: c.rev_growth_lq && +(c.rev_growth_lq * 100).toFixed(1), dividend_yield: c.dividend_yield && +(c.dividend_yield * 100).toFixed(2), ret_ytd: c.ret_ytd && +(c.ret_ytd * 100).toFixed(1), ret_1y: c.ret_1y && +(c.ret_1y * 100).toFixed(1), beta_1y: c.beta_1y && +c.beta_1y.toFixed(2), latest_quarter: c.latest_quarter })); }) },
    { name: "get_financials", description: "Return a reported or calculated metric series for one company from its SEC filings. period is 'annual' or 'quarterly'. Each point has a period label (e.g. 'FY2025', 'Q3 FY26'), value, unit and the filing it came from. Percent metrics are in percent. Metric labels: " + METRIC_LABELS.join(", "), inputSchema: { type: "object", properties: { ticker: { type: "string" }, metric: { type: "string" }, period: { type: "string", enum: ["annual", "quarterly"] }, last: { type: "number", description: "How many most recent periods (default 8, max 40)" } }, required: ["ticker", "metric"] },
      execute: wrap("get_financials", async i => { const tk = String(i.ticker).toUpperCase(); await ensureCompany(tk); const m = model(tk); const lab = resolveLabel(m, i.metric); if (!lab) throw new Error("Unknown metric for " + tk + ". Valid: " + Object.keys(m.byLabel).concat(m.ratioDefs.map(d => d.label)).join(", ")); return { ticker: tk, metric: lab, points: compactSeries(tk, lab, i.period === "annual" ? "A" : "Q", Math.min(40, Number(i.last) || 8)), ttm: m.ttm(lab)?.v ?? null }; }) },
    { name: "get_price_stats", description: "Price statistics for a symbol from daily bars: last close, 1D/1W/1M/3M/YTD/1Y returns, 20/60-day annualized volatility, 52-week range, beta and correlation to SPY over 1 year, max drawdown, RSI14, 50/200-day moving averages. Symbols: any covered ticker plus SPY, QQQ, IWM, DIA, SMH, sector SPDRs, ^VIX, ^TNX, DX-Y.NYB, CL=F, GC=F, HG=F, BTC-USD.", inputSchema: { type: "object", properties: { symbol: { type: "string" } }, required: ["symbol"] },
      execute: wrap("get_price_stats", async i => { const s = String(i.symbol).toUpperCase(); await Promise.all([ensurePrices(s), ensurePrices("SPY")]); const st = priceStats(s); if (!st) throw new Error("No price data for " + i.symbol); return Object.fromEntries(Object.entries(st).map(([k, v]) => [k, typeof v === "number" ? +v.toFixed(4) : v])); }) },
    { name: "get_earnings_reactions", description: "For one company, list earnings-release dates (8-K Item 2.02) with the stock's next-session return, the same-day SPY return, and the 5-session drift.", inputSchema: { type: "object", properties: { ticker: { type: "string" } }, required: ["ticker"] }, execute: wrap("get_earnings_reactions", async i => { const tk = String(i.ticker).toUpperCase(); await ensureCompany(tk); return earningsReactions(tk).map(r => ({ filed: r.date, next1d: +(r.r1 * 100).toFixed(2), spy1d: r.s1 == null ? null : +(r.s1 * 100).toFixed(2), drift5d: r.r5 == null ? null : +(r.r5 * 100).toFixed(2) })); }) },
    { name: "get_macro", description: "Macro series. Names: CPI, CORE_CPI, UNRATE (%), PAYEMS (thousands), EFFR (%), CURVE (latest Treasury curve), SPREAD_2S10S (daily, %). Returns the last n observations (default 24).", inputSchema: { type: "object", properties: { name: { type: "string" }, n: { type: "number" } }, required: ["name"] }, execute: wrap("get_macro", i => macroSeries(String(i.name).toUpperCase(), Math.min(120, Number(i.n) || 24))) },
    { name: "list_filings", description: "Recent SEC filings for a company, newest first, optionally filtered by form type (10-K, 10-Q, 8-K, 4, DEF 14A).", inputSchema: { type: "object", properties: { ticker: { type: "string" }, form: { type: "string" }, limit: { type: "number" } }, required: ["ticker"] }, execute: wrap("list_filings", async i => { const tk = String(i.ticker).toUpperCase(); await ensureCompany(tk); const c = D.companies[tk]; return c.filings.filter(f => !i.form || f.f === i.form).slice(0, Math.min(30, Number(i.limit) || 10)).map(f => ({ form: f.f, filed: f.d, period: f.r, accession: f.a, items: f.i })); }) },
    { name: "run_js", description: "Run JavaScript in a sandboxed worker for calculations. The function body receives `data` = {universe:[{ticker, name, sector, price, market_cap, pe_ttm, ...}], companies:{TICKER:{name, annual:{metric:[[label,end,value]...]}, quarterly:{...}}} (only companies already fetched with get_financials), prices:{SYMBOL:[[date,close]...]}} and must `return` a JSON-serializable result (under 12 KB). No network. 4 s limit.", inputSchema: { type: "object", properties: { code: { type: "string" } }, required: ["code"] }, execute: wrap("run_js", async i => { const r = await runJS(i.code); if (!r.ok) throw new Error(r.error); return r.result; }) },
  ];
}
const CITE_RULE = "When you state a number that comes from get_financials, cite it right after the number as [[TICKER|Metric|Period]] using the exact metric label and period label from the tool output (period can also be TTM). Example: revenue was $46.7B [[NVDA|Revenue|Q2 FY26]]. Do not cite price or macro numbers this way.";
function md(text) {
  const lines = String(text).replace(/\r/g, "").split("\n");
  let html = "", list = null, tbl = null;
  const inline = s => esc(s).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\[\[([A-Z.-]+)\|([^|\]]+)\|([^\]]+)\]\]/g, (_, t, m, p) => `<button class="cite" data-t="${t}" data-m="${esc(m)}" data-p="${esc(p)}" title="Open source">${t} ${esc(m)} ${esc(p)}</button>`);
  const flush = () => { if (list) { html += `</${list}>`; list = null; } if (tbl) { html += "</table>"; tbl = null; } };
  for (const ln of lines) {
    if (/^\s*\|.*\|\s*$/.test(ln)) { if (/^\s*\|[\s:|-]+\|\s*$/.test(ln)) continue; const cells = ln.trim().slice(1, -1).split("|"); if (!tbl) { flush(); html += "<table>"; tbl = 1; html += "<tr>" + cells.map(c => `<th>${inline(c.trim())}</th>`).join("") + "</tr>"; } else html += "<tr>" + cells.map(c => `<td>${inline(c.trim())}</td>`).join("") + "</tr>"; continue; }
    let m;
    if ((m = ln.match(/^\s*[-*]\s+(.*)/))) { if (list !== "ul") { flush(); html += "<ul>"; list = "ul"; } html += `<li>${inline(m[1])}</li>`; continue; }
    if ((m = ln.match(/^\s*\d+[.)]\s+(.*)/))) { if (list !== "ol") { flush(); html += "<ol>"; list = "ol"; } html += `<li>${inline(m[1])}</li>`; continue; }
    flush();
    if ((m = ln.match(/^#{1,4}\s+(.*)/))) html += `<h4>${inline(m[1])}</h4>`;
    else if (ln.trim()) html += `<p>${inline(ln)}</p>`;
  }
  flush();
  return html;
}
document.addEventListener("click", async e => { const b = e.target.closest(".cite"); if (!b) return; const cell = await findCellAsync(b.dataset.t, b.dataset.m, b.dataset.p); if (cell) openSource(cell); else { b.title = "No matching filed value"; b.style.opacity = .5; } });

/* Assistant client. The server route picks the model and holds the keys. */
async function agentLoop({ system, turns, tools, signal, onText }) {
  const t0 = performance.now();
  const msgs = [{ role: "system", content: system }, ...turns];
  const spec = tools.map(t => ({ type: "function", function: { name: t.name, description: t.description, parameters: t.inputSchema || { type: "object", properties: {} } } }));
  let calls = 0;
  for (let round = 0; round < 10; round++) {
    const r = await fetch("/api/ai", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ messages: msgs, tools: spec }), signal });
    if (!r.ok) { const body = await r.json().catch(() => ({})); throw { code: r.status === 429 ? "rate_limited" : r.status === 413 ? "too_large" : r.status === 403 ? "forbidden" : "upstream_error", message: body.error || r.statusText }; }
    const d = await r.json();
    const m = d.choices?.[0]?.message || {};
    if (m.tool_calls?.length) {
      msgs.push({ role: "assistant", content: m.content || "", tool_calls: m.tool_calls });
      if (m.content) onText?.({ text: m.content });
      calls += m.tool_calls.length;
      await Promise.all(m.tool_calls.map(async tc => {
        const tool = tools.find(t => t.name === tc.function.name);
        let out;
        try { const args = JSON.parse(tc.function.arguments || "{}"); out = tool ? await tool.execute(args, { signal }) : { error: "Unknown tool" }; } catch (e) { out = { error: String(e.message || e) }; }
        msgs.push({ role: "tool", tool_call_id: tc.id, content: (typeof out === "string" ? out : JSON.stringify(out)).slice(0, 14000) });
      }));
      continue;
    }
    const text = (m.content || "").replace(/<think>[\s\S]*?<\/think>/g, "").trim();
    onText?.({ text });
    return { text, meta: `${((performance.now() - t0) / 1000).toFixed(1)}s · ${calls} data lookup${calls === 1 ? "" : "s"}` };
  }
  throw { code: "upstream_error", message: "Too many steps" };
}
function webTools(trace) {
  const wrap = (name, fn) => async (i, ctx) => { const st = trace(name, i); try { const r = await fn(i || {}, ctx); st.done(r); return r; } catch (e) { st.fail(e); throw e; } };
  const post = async (path, body) => { const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }); const d = await r.json(); if (!r.ok) throw new Error(d.error || r.statusText); return d; };
  return [
    { name: "web_search", description: "Search recent web and news. Returns titles, URLs, dates and short snippets. Use for events after the latest filings.", inputSchema: { type: "object", properties: { query: { type: "string" }, news: { type: "boolean" } }, required: ["query"] }, execute: wrap("web_search", i => post("/api/search", i)) },
    { name: "read_url", description: "Fetch a web page as markdown, trimmed to 12,000 characters. Use for IR pages, press releases and filings.", inputSchema: { type: "object", properties: { url: { type: "string" } }, required: ["url"] }, execute: wrap("read_url", i => post("/api/scrape", i)) },
  ];
}

/* chat widget used by Research and Workbench */
function chatPanel(host, cfg) {
  host.classList.add("ai");
  const log = el("div", { class: "log", "aria-live": "polite" });
  const ta = el("textarea", { id: cfg.id + "-input", placeholder: cfg.placeholder, rows: 3 });
  const send = el("button", { class: "btn acc" }, svgUse("i-spark"), "Ask");
  const stop = el("button", { class: "btn", hidden: true }, svgUse("i-stop"), "Stop");
  const status = el("span", { class: "muted", style: "font-size:12px;flex:1;min-width:160px" }, cfg.footnote || "");
  const clear = el("button", { class: "btn", title: "Start a new conversation" }, "New chat");
  const sugg = el("div", { class: "sugg" }, cfg.suggestions.map(s => el("button", { onclick: () => { ta.value = s; go(); } }, s)));
  host.append(log, el("div", { class: "comp" }, sugg, ta, el("div", { class: "row" }, send, stop, status, clear)));
  const intro = () => { if (cfg.intro) log.append(el("div", { class: "msg a", html: md(cfg.intro) })); };
  intro();
  const turns = [];
  let ctl = null;
  clear.onclick = () => { ctl?.abort(); turns.length = 0; log.innerHTML = ""; intro(); };
  ta.addEventListener("keydown", e => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) go(); });
  stop.onclick = () => ctl?.abort();
  send.onclick = () => go();
  async function go() {
    const q = ta.value.trim(); if (!q || send.disabled) return;
    ta.value = ""; log.append(el("div", { class: "msg u" }, q));
    const steps = el("div", { class: "steps" }); const out = el("div", { class: "msg a" }, el("span", { class: "thinking" }, el("i"), "Working"));
    const meta = el("div", { class: "resp-meta" });
    log.append(steps, out, meta); log.scrollTop = log.scrollHeight;
    const trace = (name, input) => {
      const det = el("details", { class: "step" }); const st = el("span", { class: "st chip" }, "running");
      det.append(el("summary", {}, el("span", { class: "nm" }, name.replace(/_/g, " ")), el("span", { class: "muted", style: "overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0" }, JSON.stringify(input || {}).slice(0, 90)), st));
      steps.append(det); log.scrollTop = log.scrollHeight;
      const t0 = performance.now();
      return { done: r => { st.className = "st chip up"; st.textContent = `${Math.round(performance.now() - t0)} ms`; det.append(el("pre", {}, (typeof r === "string" ? r : JSON.stringify(r, null, 1)).slice(0, 3000))); }, fail: e => { st.className = "st chip down"; st.textContent = "error"; det.append(el("pre", {}, String(e.message || e))); } };
    };
    turns.push({ role: "user", content: q });
    ctl = new AbortController(); send.disabled = true; stop.hidden = false;
    try {
      const tools = [...baseTools(trace), ...webTools(trace), ...(cfg.tools ? cfg.tools(trace) : [])];
      const ctx = cfg.context ? cfg.context() : "";
      const rules = cfg.rules + "\n\n" + CITE_RULE + (ctx ? "\n\nCurrent workspace:\n" + ctx : "");
      const r = await agentLoop({ system: rules, turns: turns.slice(-8), tools, signal: ctl.signal, onText: ({ text }) => { out.innerHTML = md(text); log.scrollTop = log.scrollHeight; } });
      out.innerHTML = md(r.text); meta.textContent = r.meta; turns.push({ role: "assistant", content: r.text });
    } catch (e) {
      out.innerHTML = "";
      if (e.name !== "AbortError" && e.code !== "cancelled") out.append(el("div", { class: "note" }, AI_ERR[e.code] || AI_ERR.upstream_error));
      turns.pop();
    } finally { send.disabled = false; stop.hidden = true; ctl = null; cfg.onDone && cfg.onDone(); }
  }
  return { ask: q => { ta.value = q; go(); } };
}

/* ---------- earnings reactions ---------- */
function earningsReactions(tk) {
  const c = D.companies[tk]; if (!c || !PX[tk]) return [];
  const b = PX[tk].bars, s = PX.SPY?.bars || [];
  const iso = d => d.toISOString().slice(0, 10);
  const sIdx = new Map(s.map((x, i) => [iso(x.t), i]));
  return (c.earnings8k || []).map(e => {
    const i = b.findIndex(x => iso(x.t) > e.filed); if (i < 1) return null;
    const r1 = b[i].c / b[i - 1].c - 1, r5 = b[i + 4] ? b[i + 4].c / b[i - 1].c - 1 : null;
    const si = sIdx.get(iso(b[i].t)); const s1 = si ? s[si].c / s[si - 1].c - 1 : null;
    return { date: e.filed, accn: e.accn, url: e.url, r1, r5, s1, t: b[i].t };
  }).filter(Boolean);
}

/* ---------- macro ---------- */
function yoy(arr) { const m = new Map(arr); return arr.map(([d, v]) => { const [y, mo] = d.split("-"); const p = m.get(`${+y - 1}-${mo}`); return [d, p ? v / p - 1 : null]; }).filter(x => x[1] != null); }
function macroSeries(name, n) {
  const M = D.macro;
  if (name === "CPI") return { name: "CPI-U, all items (index 1982-84=100) and YoY %", source: "BLS via FRED CPIAUCSL", points: yoy(M["FRED:CPIAUCSL"]).slice(-n).map(([d, v]) => ({ month: d, yoyPct: +(v * 100).toFixed(2) })) };
  if (name === "CORE_CPI") return { name: "Core CPI YoY %", source: "BLS via FRED CPILFESL", points: yoy(M["FRED:CPILFESL"]).slice(-n).map(([d, v]) => ({ month: d, yoyPct: +(v * 100).toFixed(2) })) };
  if (name === "UNRATE") return { name: "Unemployment rate %", source: "BLS via FRED UNRATE", points: M["FRED:UNRATE"].slice(-n) };
  if (name === "PAYEMS") return { name: "Nonfarm payrolls, monthly change (thousands)", source: "BLS via FRED PAYEMS", points: M["FRED:PAYEMS"].map((x, i, a) => i ? [x[0], x[1] - a[i - 1][1]] : null).filter(Boolean).slice(-n) };
  if (name === "EFFR") return { name: "Effective fed funds rate %, monthly", source: "Federal Reserve H.15 via DBnomics", points: M["FED:EFFR"].slice(-n) };
  if (name === "CURVE") { const c = M["TSY:curve"].at(-1); return { date: c[0], source: "U.S. Treasury par yield curve", tenors: Object.fromEntries(M["TSY:curve_hdr"].map((h, i) => [h, c[i + 1]])) }; }
  if (name === "SPREAD_2S10S") { const h = M["TSY:curve_hdr"], i2 = h.indexOf("2 Yr") + 1, i10 = h.indexOf("10 Yr") + 1; return { source: "U.S. Treasury", points: M["TSY:curve"].filter(r => r[i2] != null && r[i10] != null).slice(-n).map(r => [r[0], +(r[i10] - r[i2]).toFixed(2)]) }; }
  throw new Error("Unknown series. Use CPI, CORE_CPI, UNRATE, PAYEMS, EFFR, CURVE, SPREAD_2S10S.");
}

/* ---------- router & shell ---------- */
const PAGES = [
  { id: "markets", name: "Markets", icon: "i-home", key: "m" },
  { id: "screener", name: "Screener", icon: "i-co", key: "c" },
  { id: "compare", name: "Compare", icon: "i-cmp", key: "o" },
  { id: "feed", name: "Filings feed", icon: "i-feed", key: "f" },
  { id: "technicals", name: "Technicals", icon: "i-ta", key: "t" },
  { id: "sectors", name: "Sectors", icon: "i-sec", key: "s" },
  { id: "macro", name: "Macro", icon: "i-mac", key: "x" },
  { id: "theses", name: "Theses", icon: "i-th", key: "h" },
  { id: "workbench", name: "Workbench", icon: "i-wb", key: "w" },
  { id: "research", name: "Research assistant", icon: "i-ag", key: "r" },
  { id: "coverage", name: "Data status", icon: "i-src", key: "d" },
];
const page = $("#page");
const WATCH_DEFAULT = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "JPM", "LLY"];
const watchlist = () => store.get("watch", WATCH_DEFAULT).filter(t => IDX[t]);
function toggleWatch(t) { const w = store.get("watch", WATCH_DEFAULT); const i = w.indexOf(t); i >= 0 ? w.splice(i, 1) : w.push(t); store.set("watch", w); renderRail(); toast(i >= 0 ? `Removed ${t} from watchlist` : `Added ${t} to watchlist`); return i < 0; }
const STATE = { coTab: store.get("coTab", "overview"), finStmt: "income", finKind: "A", finView: "values", finRange: "all", finDense: store.get("dense", false), taSym: "NVDA", taRange: "1Y", taInd: { sma: true, bb: false, vol: true }, cmp: ["AAPL", "MSFT", "NVDA", "GOOGL"], cmpMetric: "Revenue", cmpKind: "Q", scr: store.get("scr", { sector: "", sort: "market_cap", desc: true, q: "" }) };
function renderRail() {
  const wl = $("#wl"); wl.innerHTML = "";
  watchlist().forEach(t => { const c = IDX[t]; wl.append(el("a", { href: "#company-" + t.toLowerCase(), "data-t": t, title: `${c.name} · ${fnum(c.price)}` }, el("span", { class: "t" }, t), sparkSVG((c.spark || []).slice(-30), c.chg_1d >= 0 ? css("--up") : css("--down"), 80, 20), el("span", { class: "p num " + cls(c.chg_1d) }, fsgn(c.chg_1d)))); });
  if (!watchlist().length) wl.append(el("div", { class: "faint", style: "padding:6px 10px;font-size:12px" }, "Star a company to add it."));
}
function buildShell() {
  $("#nav").append(...PAGES.map(p => el("a", { href: "#" + p.id, "data-p": p.id, title: `${p.name} (g then ${p.key})` }, svgUse(p.icon), p.name)));
  $("#mnav").append(...PAGES.map(p => el("a", { href: "#" + p.id, "data-p": p.id }, p.name)));
  renderRail();
  const lt = last("SPY")?.t;
  $("#asof").textContent = lt ? "Prices as of " + d3.utcFormat("%b %d, %Y")(lt) : "";
  $("#built").textContent = new Date(D.generated).toLocaleString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
  $("#kbhelp").onclick = openShortcuts;
  $("#kbclose").onclick = () => $("#kbsheet").hidden = true;
  $("#kbsheet").addEventListener("click", e => { if (e.target.id === "kbsheet") e.target.hidden = true; });
  $("#q").addEventListener("focus", e => { e.target.blur(); openPalette(); });
  $("#q").addEventListener("click", () => openPalette());
  setupPalette(); setupKeys();
}
function loadingView(msg = "Loading") { page.innerHTML = ""; page.append(el("div", { class: "ph" }, el("div", { style: "width:100%" }, el("div", { class: "skel", style: "height:12px;width:120px;margin-bottom:12px" }), el("div", { class: "skel", style: "height:28px;width:320px;margin-bottom:10px" }), el("div", { class: "skel", style: "height:14px;width:min(560px,90%)" }))), el("div", { class: "grid g2" }, el("div", { class: "skel", style: "height:280px" }), el("div", { class: "skel", style: "height:280px" })), el("div", { class: "faint", style: "font-size:12px" }, msg + "…")); }
let ROUTE_SEQ = 0;
async function route() {
  closeSource(); hideTip();
  const seq = ++ROUTE_SEQ;
  const h = (location.hash || "#markets").slice(1);
  let pid = h, arg = null;
  if (h.startsWith("company-")) { pid = "company"; arg = h.slice(8).toUpperCase(); }
  if (pid === "companies") pid = "screener";
  document.querySelectorAll("[data-p]").forEach(a => a.classList.toggle("on", a.dataset.p === pid || (pid === "company" && a.dataset.p === "screener")));
  document.querySelectorAll("#wl a").forEach(a => a.classList.toggle("on", a.dataset.t === arg));
  const fn = { markets: pMarkets, screener: pScreener, company: () => pCompany(arg), compare: pCompare, feed: pFeed, technicals: pTechnicals, sectors: pSectors, macro: pMacro, theses: pTheses, workbench: pWorkbench, research: pResearch, coverage: pCoverage }[pid] || pMarkets;
  document.title = (pid === "company" && arg ? `${arg} · ${SHORT[arg] || ""} · ` : (PAGES.find(p => p.id === pid)?.name ? PAGES.find(p => p.id === pid).name + " · " : "")) + "Filed Under";
  window.scrollTo(0, 0);
  loadingView(pid === "company" ? `Loading ${arg} filings` : "Loading");
  try {
    const frag = document.createDocumentFragment();
    const real = page;
    await fn.prep?.();
    if (seq !== ROUTE_SEQ) return;
    page.innerHTML = "";
    await fn();
  } catch (e) {
    if (seq !== ROUTE_SEQ) return;
    console.error(e);
    page.innerHTML = "";
    page.append(el("div", { class: "empty" }, el("h2", {}, "Couldn't load this page"), el("p", { class: "muted" }, String(e.message || e)), el("button", { class: "btn", onclick: () => route() }, "Retry")));
  }
}
addEventListener("hashchange", route);
let rW = innerWidth, rT; addEventListener("resize", () => { clearTimeout(rT); rT = setTimeout(() => { if (Math.abs(innerWidth - rW) > 40 && !/workbench|research/.test(location.hash)) { rW = innerWidth; route(); } }, 250); });

/* command palette */
const PAL = { items: [], sel: 0 };
function openPalette(prefill = "") { $("#pal").hidden = false; const q = $("#pal-q"); q.value = prefill; renderPalette(); setTimeout(() => q.focus(), 0); }
function closePalette() { $("#pal").hidden = true; }
function setupPalette() {
  const q = $("#pal-q");
  q.addEventListener("input", () => { PAL.sel = 0; renderPalette(); });
  q.addEventListener("keydown", e => {
    if (e.key === "ArrowDown") { PAL.sel = Math.min(PAL.items.length - 1, PAL.sel + 1); renderPalette(true); e.preventDefault(); }
    else if (e.key === "ArrowUp") { PAL.sel = Math.max(0, PAL.sel - 1); renderPalette(true); e.preventDefault(); }
    else if (e.key === "Enter") { const it = PAL.items[PAL.sel]; if (it) { closePalette(); it.go(); } e.preventDefault(); }
    else if (e.key === "Escape") { closePalette(); e.stopPropagation(); }
  });
  $("#pal").addEventListener("click", e => { if (e.target.id === "pal") closePalette(); });
}
function paletteItems(v) {
  v = v.trim().toLowerCase(); const out = [];
  const add = (g, label, sub, ic, go) => out.push({ g, label, sub, ic, go });
  const parts = v.split(/\s+/); const tk = TICKERS.find(t => parts[0] === t.toLowerCase() || parts[0] === (SHORT[t] || "").toLowerCase());
  if (tk && parts.length > 1) {
    const rest = parts.slice(1).join(" ");
    allMetricLabels().filter(m => m.toLowerCase().includes(rest)).slice(0, 6).forEach(mt => add("Metrics", `${tk} ${mt}`, "latest quarter, opens the source", tk[0], async () => { await ensureCompany(tk); const m = model(tk); const cell = m.metric(mt, "Q", m.latestQ()) || m.metric(mt, "A", m.latestA()); STATE.coTab = "financials"; if (location.hash !== "#company-" + tk.toLowerCase()) { location.hash = "company-" + tk.toLowerCase(); await new Promise(r => setTimeout(r, 400)); } if (cell) openSource(cell, "filing"); else toast(`${tk} doesn't report ${mt}`); }));
    [["financials", "Financials"], ["news", "News & narratives"], ["earnings", "Earnings & IR"], ["filings", "Filings"]].filter(([, n]) => n.toLowerCase().includes(rest)).forEach(([id, n]) => add("Companies", `${tk} · ${n}`, SHORT[tk], tk[0], () => { STATE.coTab = id; location.hash === "#company-" + tk.toLowerCase() ? route() : location.hash = "company-" + tk.toLowerCase(); }));
  }
  const matches = TICKERS.filter(t => { const c = IDX[t]; return !v || t.toLowerCase().startsWith(v) || (c.name || "").toLowerCase().includes(v) || (c.legal_name || "").toLowerCase().includes(v); });
  (v ? matches : watchlist()).slice(0, v ? 10 : 8).forEach(t => { const c = IDX[t]; add("Companies", `${t} · ${c.name}`, `${fnum(c.price)} · ${fsgn(c.chg_1d, 2)}`, t[0], () => location.hash = "company-" + t.toLowerCase()); });
  if (v) { const sec = [...new Set(Object.values(IDX).map(c => c.sector))].filter(s => s.toLowerCase().includes(v)); sec.forEach(s => add("Screener", `${s} sector`, "screener", "▤", () => { STATE.scr.sector = s; store.set("scr", STATE.scr); location.hash === "#screener" ? route() : location.hash = "screener"; })); }
  for (const p of PAGES) if (!v || p.name.toLowerCase().includes(v)) add("Pages", p.name, `g ${p.key}`, "→", () => location.hash = p.id);
  if (v) for (const s of Object.keys(SYMNAME)) if (s.toLowerCase().replace("^", "").startsWith(v) || SYMNAME[s].toLowerCase().includes(v)) add("Charts", `${s.replace("^", "")} · ${SYMNAME[s]}`, "technicals", "↗", () => { STATE.taSym = s; location.hash === "#technicals" ? route() : location.hash = "technicals"; });
  if (v.length > 3) add("Ask", `Ask the research assistant: “${v}”`, "assistant", "✦", () => { location.hash = "research"; setTimeout(() => RESEARCH?.ask(v), 300); });
  return out.slice(0, 26);
}
function renderPalette(keepScroll) {
  PAL.items = paletteItems($("#pal-q").value);
  const list = $("#pal-list"); list.innerHTML = "";
  let g = null;
  PAL.items.forEach((it, i) => {
    if (it.g !== g) { g = it.g; list.append(el("div", { class: "pal-g" }, g)); }
    const b = el("button", { class: "pal-i" + (i === PAL.sel ? " sel" : ""), onmousemove: () => { if (PAL.sel !== i) { PAL.sel = i; renderPalette(true); } }, onclick: () => { closePalette(); it.go(); } }, el("span", { class: "ic" }, it.ic), el("span", {}, it.label), el("span", { class: "k" }, it.sub || ""));
    list.append(b);
    if (i === PAL.sel && keepScroll) b.scrollIntoView({ block: "nearest" });
  });
  if (!PAL.items.length) list.append(el("div", { class: "empty" }, "No matches. Try a ticker like NVDA, or “AAPL gross margin”."));
}
const SHORTCUTS = [["⌘ K or /", "Command palette"], ["?", "This list"], ["g then m", "Markets"], ["g then c", "Companies"], ["g then o", "Compare"], ["g then t", "Technicals"], ["g then x", "Macro"], ["g then w", "Workbench"], ["g then r", "Research agent"], ["Esc", "Close panel or dialog"], ["← / →", "Previous or next period in the source panel"], ["c", "Copy citation in the source panel"], ["1 – 6", "Company tabs"], ["s", "Star or unstar the open company"], ["⌘ Enter", "Send to the assistant"]];
function openShortcuts() { const l = $("#sc-list"); l.innerHTML = ""; SHORTCUTS.forEach(([k, d]) => l.append(el("div", {}, el("span", {}, d), el("span", {}, ...k.split(" ").map(x => /^(then|or|–)$/.test(x) ? el("span", { class: "faint", style: "margin:0 4px" }, x) : el("span", { class: "kbd" }, x)))))); $("#kbsheet").hidden = false; }
function setupKeys() {
  let g = 0;
  document.addEventListener("keydown", e => {
    const typing = /INPUT|TEXTAREA|SELECT/.test(document.activeElement?.tagName) || document.activeElement?.isContentEditable;
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); openPalette(); return; }
    if (e.key === "Escape") { if (!$("#pal").hidden) closePalette(); else if (!$("#kbsheet").hidden) $("#kbsheet").hidden = true; else closeSource(); return; }
    if (typing || e.metaKey || e.ctrlKey || e.altKey) return;
    if (DR.cell && drawer.classList.contains("open")) {
      if (e.key === "ArrowLeft" || e.key === "ArrowRight") { const sib = siblings(DR.cell); const i = sib.findIndex(x => x.end === DR.cell.end); const n = sib[i + (e.key === "ArrowLeft" ? -1 : 1)]; if (n) { openSource(n); e.preventDefault(); } return; }
      if (e.key === "c") { copyText(citation(DR.cell), "Citation copied"); return; }
    }
    if (e.key === "/") { e.preventDefault(); openPalette(); return; }
    if (e.key === "?") { openShortcuts(); return; }
    if (e.key === "g") { g = Date.now(); return; }
    if (Date.now() - g < 900) { const p = PAGES.find(p => p.key === e.key); if (p) { location.hash = p.id; g = 0; } return; }
    if (/^[1-6]$/.test(e.key) && location.hash.startsWith("#company-")) { const b = document.querySelectorAll(".tabs button")[+e.key - 1]; b?.click(); }
    if (e.key === "s" && location.hash.startsWith("#company-")) { const t = location.hash.slice(9).toUpperCase(); if (IDX[t]) { const on = toggleWatch(t); document.querySelector("#star")?.classList.toggle("pri", on); } }
  });
}
function seg(options, value, onChange) {
  const s = el("div", { class: "seg", role: "tablist" });
  options.forEach(([v, l]) => s.append(el("button", { class: v === value ? "on" : "", role: "tab", "aria-selected": v === value ? "true" : "false", onclick: () => { s.querySelectorAll("button").forEach(b => b.classList.remove("on")); s.querySelector(`[data-v="${CSS.escape(v)}"]`).classList.add("on"); onChange(v); }, "data-v": v }, l)));
  return s;
}
const panel = (title, right, ...body) => el("section", { class: "panel" }, el("div", { class: "hd" }, el("h3", {}, title), right ? el("div", { class: "r" }, right) : null), ...body);
const cellChip = (cell, text) => cell ? el("button", { class: "chip link acc mono", onclick: e => { e.stopPropagation(); openSource(cell); }, title: "Open source" }, text) : null;

/* ================= PAGES ================= */
function aiCredit(meta) {
  if (!meta?.at) return null;
  return el("div", { class: "aimeta", title: "Generated from the filings, the latest release and recent coverage. Check figures against the linked sources." }, el("span", { class: "chip acc" }, svgUse("i-spark"), "Summary"), el("span", {}, `Updated ${relTime(meta.at)}`));
}
function relTime(s) { if (!s) return ""; const t = new Date(s); if (isNaN(t)) return ""; if (Date.now() - t < 6e4) return "just now"; const h = Math.max(0, (Date.now() - t) / 36e5); return h < 1 ? "just now" : h < 24 ? `${Math.round(h)}h ago` : `${Math.round(h / 24)}d ago`; }
const domainOf = u => { try { return new URL(u).hostname.replace(/^www\./, ""); } catch { return ""; } };
function newsList(items, lines, n = 10) {
  const box = el("div", { class: "news" });
  items.slice(0, n).forEach((x, i) => box.append(el("a", { class: "it", href: x.url, target: "_blank", rel: "noopener" },
    el("span", { class: "tt" }, el("span", { class: "n" }, i + 1), x.title), el("span", { class: "mt" }, `${domainOf(x.url)} · ${relTime(x.date)}`),
    lines?.[String(i + 1)] ? el("span", { class: "ln" }, lines[String(i + 1)]) : null)));
  if (!items.length) box.append(el("div", { class: "empty" }, "No recent coverage in the snapshot."));
  return box;
}
function pMarkets() {
  page.append(el("div", { class: "ph" }, el("div", {}, el("div", { class: "eyebrow" }, "Markets · " + d3.utcFormat("%A, %B %d")(last("SPY").t)), el("h1", {}, "Today across the tape"), el("div", { class: "sub" }, "Indexes, rates, commodities, the S&P 100 and your watchlist. Every tile opens its chart.")), el("div", { class: "right" }, el("button", { class: "btn", onclick: () => openPalette() }, svgUse("i-search"), "Search", el("span", { class: "kbd" }, "⌘K")))));
  const syms = ["SPY", "QQQ", "IWM", "DIA", "SMH", "^VIX", "^TNX", "DX-Y.NYB", "CL=F", "GC=F", "HG=F", "BTC-USD"];
  page.append(el("div", { class: "tiles" }, syms.filter(s => PX[s]).map(s => {
    const ch = s === "^TNX" ? (last(s).c - prev(s).c) / 100 : dayChg(s);
    return el("button", { class: "tile", onclick: () => { STATE.taSym = s; location.hash = "technicals"; }, title: `Open ${SYMNAME[s]} chart` },
      el("span", { class: "n" }, el("span", {}, SYMNAME[s] || s), el("span", { class: "mono faint" }, s.replace("^", ""))),
      el("span", { class: "v num" }, s === "^TNX" ? last(s).c.toFixed(2) + "%" : fnum(last(s).c, last(s).c > 1000 ? 0 : 2)),
      el("span", { class: "num " + cls(ch), style: "font-size:12px;font-weight:600" }, s === "^TNX" ? ((ch >= 0 ? "+" : "−") + Math.abs(ch * 1e4).toFixed(1) + " bp") : fsgn(ch, 2)),
      sparkSVG(closes(s).slice(-63), ch >= 0 ? css("--up") : css("--down"), 140, 30));
  })));

  // breadth across the covered universe
  const U = INDEX.companies.filter(c => c.chg_1d != null);
  const adv = U.filter(c => c.chg_1d > 0).length, dec = U.filter(c => c.chg_1d < 0).length;
  const capw = U.reduce((a, c) => a + (c.market_cap || 0), 0);
  const capChg = U.reduce((a, c) => a + (c.market_cap || 0) * c.chg_1d, 0) / (capw || 1);
  const above = U.filter(c => c.high_52w && c.price >= c.high_52w * 0.97).length;
  page.append(el("div", { class: "stats" }, ...[
    ["S&P 100 advancers", `${adv} / ${U.length}`, `${dec} decliners`],
    ["Cap-weighted move", fsgn(capChg, 2), "today"],
    ["Near 52-week high", String(above), "within 3%"],
    ["Median P/E (TTM)", (() => { const p = U.map(c => c.pe_ttm).filter(Boolean).sort((a, b) => a - b); return p.length ? p[Math.floor(p.length / 2)].toFixed(1) : "–"; })(), "covered companies"],
    ["Median FCF yield", (() => { const p = U.map(c => c.fcf_yield).filter(v => v != null).sort((a, b) => a - b); return p.length ? fpct(p[Math.floor(p.length / 2)]) : "–"; })(), "TTM"],
  ].map(([l, v, d]) => el("div", {}, el("div", { class: "l" }, l), el("div", { class: "v num" }, v), el("div", { class: "faint", style: "font-size:11px" }, d)))));

  const g0 = el("div", { class: "grid g64" });
  const mb = E?.macro_brief;
  const brief = el("div", { class: "bd brief" });
  if (mb) brief.append(el("h2", {}, mb.headline), el("ul", {}, (mb.bullets || []).map(b => el("li", { html: esc(b).replace(/\[(\d+)\]/g, (_, n) => { const it = E.macro_news?.[+n - 1]; return it ? `<a href="${esc(it.url)}" target="_blank" rel="noopener" class="mono" style="font-size:11px">[${n}]</a>` : ""; }) }))), el("div", { class: "watch" }, el("span", { class: "eyebrow", style: "margin-right:4px" }, "Watch"), (mb.watch || []).map(w => el("span", { class: "chip warn" }, w))));
  else brief.append(el("div", { class: "muted" }, "The market brief loads with the macro data."));
  g0.append(panel("Market brief", aiCredit(mb?.meta), brief));
  const mv = el("div", { class: "bd movers", style: "padding-top:4px" });
  const movers = [...U.map(c => ({ s: c.ticker, ch: c.chg_1d, px: c.price, nm: c.name, co: true })), ...SECTORS.filter(s => PX[s]).map(s => ({ s, ch: dayChg(s), px: last(s).c, nm: SYMNAME[s] }))].sort((a, b) => Math.abs(b.ch) - Math.abs(a.ch)).slice(0, 9);
  movers.forEach(x => mv.append(el("div", { class: "r", onclick: () => x.co ? location.hash = "company-" + x.s.toLowerCase() : (STATE.taSym = x.s, location.hash = "technicals") }, el("b", { class: "mono" }, x.s), el("span", { class: "muted", style: "overflow:hidden;text-overflow:ellipsis;white-space:nowrap" }, x.nm), el("span", { class: "num", style: "text-align:right" }, fnum(x.px)), el("span", { class: "chip " + cls(x.ch), style: "justify-self:end" }, fsgn(x.ch, 2)))));
  g0.append(panel("Biggest moves today", el("a", { href: "#screener" }, "Screener"), mv));
  page.append(g0);

  const g = el("div", { class: "grid g64" });
  const wl = el("table", { class: "t" }, el("thead", {}, el("tr", {}, el("th", {}, "Watchlist"), el("th", { class: "r" }, "Price"), el("th", { class: "r" }, "1D"), el("th", { class: "r" }, "YTD"), el("th", { class: "r" }, "Mkt cap"), el("th", { class: "r" }, "P/E"), el("th", { class: "r" }, "Rev growth"), el("th", {}, "3M"))));
  const tb = el("tbody");
  watchlist().forEach(t => { const c = IDX[t]; tb.append(el("tr", { class: "click", onclick: () => location.hash = "company-" + t.toLowerCase() },
    el("td", {}, el("b", {}, t), " ", el("span", { class: "muted" }, c.name)),
    el("td", { class: "r num" }, fnum(c.price)), el("td", { class: "r num " + cls(c.chg_1d) }, fsgn(c.chg_1d, 2)), el("td", { class: "r num " + cls(c.ret_ytd) }, fsgn(c.ret_ytd)),
    el("td", { class: "r num" }, fmtMoney(c.market_cap)), el("td", { class: "r num" }, c.pe_ttm ? c.pe_ttm.toFixed(1) : "–"), el("td", { class: "r num " + cls(c.rev_growth_lq) }, c.rev_growth_lq != null ? fsgn(c.rev_growth_lq) : "–"),
    el("td", { style: "width:90px" }, sparkSVG((c.spark || []).slice(-63), css("--c1"), 90, 22)))); });
  if (!watchlist().length) tb.append(el("tr", {}, el("td", { colspan: 8, class: "muted" }, "Your watchlist is empty. Star companies from the screener or a company page.")));
  wl.append(tb);
  g.append(panel("Your watchlist", el("a", { href: "#compare" }, "Compare"), el("div", { class: "scroll" }, wl), el("div", { class: "src" }, "Market cap = last close × shares outstanding from the latest filing cover page. P/E uses the last four reported diluted EPS figures. Star a company to add it.")));
  const feed = el("div", { class: "bd", style: "padding:4px 16px" });
  const fl = allFilings().filter(f => ["10-K", "10-Q", "8-K", "DEF 14A"].includes(f.f)).slice(0, 9);
  fl.forEach(f => feed.append(filingRow(f)));
  if (!fl.length) feed.append(el("div", { class: "muted", style: "padding:12px 0" }, "Loading filings…"));
  g.append(panel("Latest filings", el("a", { href: "#feed" }, "All filings"), feed));
  page.append(g);

  const g2 = el("div", { class: "grid g3" });
  const secHost = el("div", { class: "bd" });
  g2.append(panel("Sector SPDRs, 1-month return", el("a", { href: "#sectors" }, "Sectors"), secHost));
  const mac = el("div", { class: "bd" });
  g2.append(panel("Macro snapshot", el("a", { href: "#macro" }, "Macro"), mac));
  g2.append(panel("Macro headlines", null, el("div", { class: "bd", style: "padding-top:0" }, newsList(E?.macro_news || [], null, 6))));
  page.append(g2);
  requestAnimationFrame(() => barChart(secHost, SECTORS.filter(s => PX[s]).map(s => ({ k: s, vals: [{ v: retOver(s, 30), name: SYMNAME[s], color: retOver(s, 30) >= 0 ? css("--up") : css("--down") }] })).sort((a, b) => b.vals[0].v - a.vals[0].v), { yfmt: v => fpct(v, 0), tfmt: v => fsgn(v, 2), height: 220 }));
  if (D.macro["FRED:CPIAUCSL"]?.length) {
    const cpi = yoy(D.macro["FRED:CPIAUCSL"]).at(-1), core = yoy(D.macro["FRED:CPILFESL"]).at(-1), un = D.macro["FRED:UNRATE"].at(-1), ef = D.macro["FED:EFFR"].at(-1), cv = D.macro["TSY:curve"].at(-1), h = D.macro["TSY:curve_hdr"];
    const sp = cv[h.indexOf("10 Yr") + 1] - cv[h.indexOf("2 Yr") + 1];
    mac.append(el("div", { class: "stats", style: "border:0;box-shadow:none" },
      ...[["CPI YoY", fpct(cpi[1]), fmon(cpi[0])], ["Core CPI YoY", fpct(core[1]), fmon(core[0])], ["Unemployment", un[1].toFixed(1) + "%", fmon(un[0])], ["Fed funds (eff.)", ef ? ef[1].toFixed(2) + "%" : "–", ef ? fmon(ef[0]) : ""], ["10Y Treasury", cv[h.indexOf("10 Yr") + 1].toFixed(2) + "%", fdate(cv[0])], ["2s10s spread", (sp >= 0 ? "+" : "") + Math.round(sp * 100) + " bp", fdate(cv[0])]]
        .map(([l, v, d]) => el("div", {}, el("div", { class: "l" }, l), el("div", { class: "v num" }, v), el("div", { class: "faint", style: "font-size:11px" }, d)))));
  } else mac.append(el("div", { class: "muted" }, "Loading macro data…"));
}
function allFilings() { return (FEED?.filings || []).map(f => ({ f: f.form, d: f.filed, r: f.report_date, a: f.accession, p: f.primary_doc, i: f.items || "", x: f.description || "", t: f.ticker, url: f.url })); }
const ITEMS = { "1.01": "Material agreement", "2.02": "Results of operations", "2.03": "Debt obligation", "5.02": "Officer/director change", "5.03": "Bylaw amendment", "5.07": "Shareholder vote", "7.01": "Reg FD disclosure", "8.01": "Other events", "9.01": "Exhibits" };
function filingDesc(f) {
  if (f.f === "10-K") return "Annual report";
  if (f.f === "10-Q") return "Quarterly report";
  if (f.f === "8-K") return (f.i || "").split(",").filter(x => x !== "9.01").map(x => ITEMS[x] || "Item " + x).join(" · ") || "Current report";
  if (f.f === "4") return "Insider transaction";
  if (f.f === "144") return "Proposed insider sale";
  if (f.f === "DEF 14A") return "Proxy statement";
  if (f.f === "S-8") return "Employee plan registration";
  if (/SC 13G/.test(f.f)) return "Beneficial owner >5%";
  return f.x || f.f;
}
const filingUrl = (t, f) => f.url || `https://www.sec.gov/Archives/edgar/data/${D.companies[t]?.cik || ""}/${f.a.replace(/-/g, "")}/${f.p || ""}`;
function filingRow(f) {
  return el("div", { style: "display:grid;grid-template-columns:72px 44px minmax(0,1fr) auto;gap:10px;align-items:center;padding:8px 0;border-bottom:1px solid var(--line)" },
    el("span", { class: "form " + formCls(f.f) }, f.f), el("b", {}, f.t),
    el("span", { style: "min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" }, filingDesc(f)),
    el("a", { class: "mono faint", style: "font-size:11.5px", href: filingUrl(f.t, f), target: "_blank", rel: "noopener" }, fdate(f.d)));
}

const SCR_COLS = [
  { k: "ticker", n: "Ticker", f: (c, v) => el("b", { class: "mono" }, v) },
  { k: "name", n: "Company", f: (c, v) => el("span", {}, v, el("div", { class: "faint", style: "font-size:11px" }, c.sector)) },
  { k: "price", n: "Price", r: 1, f: v => fnum(v) },
  { k: "chg_1d", n: "1D", r: 1, f: v => fsgn(v, 2), c: 1 },
  { k: "ret_ytd", n: "YTD", r: 1, f: v => fsgn(v), c: 1 },
  { k: "ret_1y", n: "1Y", r: 1, f: v => fsgn(v), c: 1 },
  { k: "market_cap", n: "Mkt cap", r: 1, f: v => fmtMoney(v), bar: 1 },
  { k: "pe_ttm", n: "P/E", r: 1, f: v => v ? v.toFixed(1) : "–" },
  { k: "ps_ttm", n: "P/S", r: 1, f: v => v ? v.toFixed(1) : "–" },
  { k: "fcf_yield", n: "FCF yield", r: 1, f: v => fpct(v) },
  { k: "revenue_ttm", n: "Revenue TTM", r: 1, f: v => fmtMoney(v) },
  { k: "rev_growth_lq", n: "Rev growth", r: 1, f: v => fsgn(v), c: 1 },
  { k: "gross_margin_lq", n: "Gross margin", r: 1, f: v => fpct(v) },
  { k: "op_margin_lq", n: "Op margin", r: 1, f: v => fpct(v) },
  { k: "net_margin_ttm", n: "Net margin", r: 1, f: v => fpct(v) },
  { k: "dividend_yield", n: "Div yield", r: 1, f: v => fpct(v, 2) },
  { k: "beta_1y", n: "Beta", r: 1, f: v => v ? v.toFixed(2) : "–" },
  { k: "latest_quarter", n: "Last Q", r: 1, f: v => v || "–" },
];
const SCR_PRESETS = [
  ["All", {}],
  ["Cash machines", { min: { fcf_yield: 0.04 }, sort: "fcf_yield" }],
  ["Fast growers", { min: { rev_growth_lq: 0.15 }, sort: "rev_growth_lq" }],
  ["High margin", { min: { op_margin_lq: 0.3 }, sort: "op_margin_lq" }],
  ["Dividend payers", { min: { dividend_yield: 0.025 }, sort: "dividend_yield" }],
  ["Value", { max: { pe_ttm: 18 }, sort: "pe_ttm", desc: false }],
  ["Laggards YTD", { max: { ret_ytd: 0 }, sort: "ret_ytd", desc: false }],
];
function pScreener() {
  const st = STATE.scr; st.min = st.min || {}; st.max = st.max || {};
  page.append(el("div", { class: "ph" }, el("div", {}, el("div", { class: "eyebrow" }, "Screener"), el("h1", {}, "S&P 100"), el("div", { class: "sub" }, `${INDEX.companies.length} companies with fundamentals from their SEC filings and prices as of ${fdate(INDEX.companies.find(c => c.as_of)?.as_of)}. Sort any column, filter, star to your watchlist, or open a company.`))));
  const q = el("input", { type: "search", id: "scr-q", placeholder: "Filter by ticker or name", value: st.q || "" });
  const sectors = [...new Set(INDEX.companies.map(c => c.sector))].sort();
  const sec = el("select", { class: "btn", id: "scr-sector" }, el("option", { value: "" }, "All sectors"), ...sectors.map(s => { const o = el("option", { value: s }, s); if (s === st.sector) o.selected = true; return o; }));
  const presets = el("div", { class: "picker" }, SCR_PRESETS.map(([n, p]) => el("button", { class: (st.preset || "All") === n ? "on" : "", onclick: () => { st.preset = n; st.min = { ...(p.min || {}) }; st.max = { ...(p.max || {}) }; if (p.sort) { st.sort = p.sort; st.desc = p.desc !== false; } save(); route(); } }, n)));
  const count = el("span", { class: "muted", style: "font-size:12.5px;margin-left:auto" });
  const csvBtn = el("button", { class: "btn", title: "Download the filtered table as CSV" }, svgUse("i-dl"), "CSV");
  const wrap = el("div", { class: "scroll" });
  page.append(el("section", { class: "panel" }, el("div", { class: "scr-tools" }, q, sec, presets, count, csvBtn), wrap, el("div", { class: "src", style: "padding-top:10px" }, "Fundamentals are trailing twelve months or latest quarter as reported. Banks and insurers do not report gross margin. Click a column to sort.")));
  const save = () => store.set("scr", st);
  let rowsNow = [];
  q.oninput = () => { st.q = q.value; save(); draw(); };
  sec.onchange = () => { st.sector = sec.value; save(); draw(); };
  csvBtn.onclick = () => { const head = SCR_COLS.map(c => c.n); const lines = [head.join(",")].concat(rowsNow.map(r => SCR_COLS.map(c => { const v = r[c.k]; return typeof v === "string" ? `"${v.replace(/"/g, '""')}"` : v ?? ""; }).join(","))); downloadText("filedunder_screener.csv", lines.join("\n"), "text/csv"); };
  function draw() {
    const qq = (st.q || "").toLowerCase();
    let rows = INDEX.companies.filter(c => (!st.sector || c.sector === st.sector) && (!qq || c.ticker.toLowerCase().includes(qq) || c.name.toLowerCase().includes(qq)));
    for (const [k, v] of Object.entries(st.min)) rows = rows.filter(c => c[k] != null && c[k] >= v);
    for (const [k, v] of Object.entries(st.max)) rows = rows.filter(c => c[k] != null && c[k] <= v && (k !== "pe_ttm" || c[k] > 0));
    const dir = st.desc ? -1 : 1;
    rows.sort((a, b) => { const x = a[st.sort], y = b[st.sort]; if (x == null && y == null) return 0; if (x == null) return 1; if (y == null) return -1; return typeof x === "string" ? dir * x.localeCompare(y) : dir * (x - y); });
    rowsNow = rows;
    count.textContent = `${rows.length} of ${INDEX.companies.length}`;
    const maxCap = Math.max(...rows.map(c => c.market_cap || 0));
    const t = el("table", { class: "t scr" });
    t.append(el("thead", {}, el("tr", {}, el("th", {}, ""), SCR_COLS.map(c => el("th", { class: (c.r ? "r " : "") + (st.sort === c.k ? "sorted" : ""), onclick: () => { if (st.sort === c.k) st.desc = !st.desc; else { st.sort = c.k; st.desc = typeof INDEX.companies[0][c.k] !== "string"; } save(); draw(); } }, c.n, st.sort === c.k ? el("span", { class: "ar" }, st.desc ? "▼" : "▲") : null)))));
    const tb = el("tbody");
    const w = watchlist();
    rows.forEach(c => {
      const star = el("td", { class: "star" + (w.includes(c.ticker) ? " on" : ""), title: "Watchlist", onclick: e => { e.stopPropagation(); const on = toggleWatch(c.ticker); star.classList.toggle("on", on); } }, "★");
      tb.append(el("tr", { onclick: () => location.hash = "company-" + c.ticker.toLowerCase() }, star, SCR_COLS.map(col => {
        const v = c[col.k];
        const td = el("td", { class: (col.r ? "r num " : "") + (col.c ? cls(v) : "") }, col.f.length === 2 ? col.f(c, v) : col.f(v));
        if (col.bar && v) td.append(el("span", { class: "bar-mini", style: `width:${Math.max(2, Math.round((v / maxCap) * 40))}px` }));
        return td;
      })));
    });
    if (!rows.length) tb.append(el("tr", {}, el("td", { colspan: SCR_COLS.length + 1, class: "muted", style: "padding:24px" }, "No companies match. Clear a filter or pick another preset.")));
    t.append(tb); wrap.innerHTML = ""; wrap.append(t);
  }
  draw();
}
function downloadText(filename, text, mime) {
  try { const a = el("a", { href: URL.createObjectURL(new Blob([text], { type: mime })), download: filename }); document.body.append(a); a.click(); a.remove(); toast(`Downloaded ${filename}`); }
  catch { copyText(text, "Copied to clipboard"); }
}
function sectorConstituents() {
  const by = {};
  INDEX.companies.forEach(c => (by[c.sector] = by[c.sector] || []).push(c));
  const grid = el("div", { class: "bd sec-grid" });
  Object.entries(by).sort((a, b) => b[1].reduce((s, c) => s + (c.market_cap || 0), 0) - a[1].reduce((s, c) => s + (c.market_cap || 0), 0)).forEach(([s, list]) => {
    const etf = INDEX.sectors?.[s];
    const cap = list.reduce((a, c) => a + (c.market_cap || 0), 0);
    const chg = list.reduce((a, c) => a + (c.market_cap || 0) * (c.chg_1d || 0), 0) / (cap || 1);
    grid.append(el("div", { class: "sec-card" }, el("div", { style: "display:flex;justify-content:space-between;align-items:baseline;gap:8px" }, el("b", {}, s), el("span", { class: "num " + cls(chg), style: "font-size:12px" }, fsgn(chg, 2))),
      el("div", { class: "faint", style: "font-size:11.5px" }, `${list.length} companies · ${fmtMoney(cap)}${etf ? " · " + etf : ""}`),
      ...list.sort((a, b) => (b.market_cap || 0) - (a.market_cap || 0)).slice(0, 6).map(c => el("div", { class: "row", onclick: () => location.hash = "company-" + c.ticker.toLowerCase() }, el("span", {}, el("b", { class: "mono" }, c.ticker), " ", el("span", { class: "muted" }, c.name)), el("span", { class: "num " + cls(c.chg_1d) }, fsgn(c.chg_1d, 2))))));
  });
  return panel("S&P 100 by sector", el("a", { href: "#screener" }, "Screener"), grid, el("div", { class: "src" }, "Sector move is weighted by market cap. Largest six companies shown per sector."));
}
function logoTile(t, size = 56) { return el("div", { class: "logo", style: `background:${logoColor(t)};width:${size}px;height:${size}px;font-size:${size * (t.length > 3 ? .26 : .32)}px`, "aria-hidden": "true" }, t.replace("-", ".")); }

pCompany.prep = null;
async function pCompany(tk) {
  if (!IDX[tk]) { page.append(el("div", { class: "empty" }, el("h2", {}, `${tk} isn't covered yet`), el("p", { class: "muted" }, "Coverage is the S&P 100 today."), el("a", { class: "btn", href: "#screener" }, "Open the screener"))); return; }
  await ensureCompany(tk);
  page.innerHTML = "";
  const s = snapshot(tk), c = s.c, a = c.address || {};
  const st = priceStats(tk);
  const crumbs = el("div", { class: "crumbs" }, el("a", { href: "#companies" }, "Companies"), el("span", { class: "sep" }, "/"), el("span", {}, SHORT[tk]), el("span", { class: "sep" }, "/"), el("span", { id: "crumb-tab" }, ""));
  const head = el("div", { class: "co-head" },
    logoTile(tk),
    el("div", { style: "min-width:0" },
      el("div", { class: "eyebrow" }, `${tk} · ${(c.exchanges || []).join(", ")} · ${IDX[tk].sector} · CIK ${String(c.cik).padStart(10, "0")}`),
      el("h1", {}, c.name.replace(/\b([A-Z])([A-Z]+)\b/g, (w, a, b) => w.length <= 3 ? w : a + b.toLowerCase()).replace(/ \/[A-Z]{2}\/?$/, "")),
      el("div", { class: "co-meta" },
        el("span", {}, el("b", {}, c.sicDescription || "–"), ` (SIC ${c.sic || "–"})`),
        el("span", {}, [a.street1, a.city, a.stateOrCountry, a.zipCode].filter(Boolean).map(x => x.replace(/\b\w+/g, w => w.length > 2 && w === w.toUpperCase() && !/^\d/.test(w) ? w[0] + w.slice(1).toLowerCase() : w)).join(", ")),
        c.phone ? el("span", {}, c.phone) : null,
        el("span", {}, "FY ends ", el("b", {}, fdate("2000-" + c.fiscalYearEnd.slice(0, 2) + "-" + c.fiscalYearEnd.slice(2)).replace(", 2000", ""))),
        el("span", {}, "Inc. ", el("b", {}, c.stateOfIncorporation || "–"))),
      el("div", { class: "co-meta", style: "margin-top:8px" },
        WEB[tk] ? el("a", { href: "https://" + WEB[tk], target: "_blank", rel: "noopener" }, WEB[tk] + " ↗") : null,
        IR[tk] ? el("a", { href: IR[tk], target: "_blank", rel: "noopener" }, "Investor relations ↗") : null,
        el("a", { href: `https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=${c.cik}&type=&dateb=&owner=include&count=40`, target: "_blank", rel: "noopener" }, "EDGAR ↗"),
        (() => { const k = c.filings.find(f => f.f === "10-K"); return k ? el("a", { href: filingUrl(tk, k), target: "_blank", rel: "noopener" }, "Latest 10-K ↗") : null; })(),
        (() => { const k = c.filings.find(f => f.f === "10-Q"); return k ? el("a", { href: filingUrl(tk, k), target: "_blank", rel: "noopener" }, "Latest 10-Q ↗") : null; })(),
        el("button", { class: "chip link", onclick: () => { location.hash = "research"; setTimeout(() => RESEARCH?.ask(`Give me a one-page brief on ${tk}: latest quarter vs prior year, margin trend, cash returns vs free cash flow, recent news, and how the stock reacted to earnings. Cite every financial number.`), 300); } }, svgUse("i-spark"), "Write a brief"))),
    el("div", { class: "px" }, el("div", { style: "display:flex;justify-content:flex-end;margin-bottom:6px" }, el("button", { id: "star", class: "btn" + (watchlist().includes(tk) ? " pri" : ""), title: "Add to watchlist (s)", onclick: e => { const on = toggleWatch(tk); e.currentTarget.classList.toggle("pri", on); } }, "★ Watch")), el("div", { class: "big num" }, "$" + fnum(s.px)), el("div", { class: "num " + cls(st.chg1d), style: "font-weight:600" }, `${fsgn(st.chg1d, 2)} today`), el("div", { class: "faint", style: "font-size:11.5px" }, "Close " + fdate(st.date)), el("div", { style: "width:180px;margin-left:auto;margin-top:6px" }, sparkSVG((PX[tk + "@5m"]?.bars || []).map(b => b.c), st.chg1d >= 0 ? css("--up") : css("--down"), 180, 34)), el("div", { class: "faint", style: "font-size:11px" }, "Last 5 sessions, 5-minute bars")));
  page.append(crumbs, head);
  const rng = el("div", { class: "rng" }, el("i", { style: `left:calc(${((s.px - st.lo52) / (st.hi52 - st.lo52)) * 100}% - 1px)` }));
  const stat = (l, v, cell) => el("div", {}, el("div", { class: "l" }, l), el("div", { class: "v num" + (cell ? " src" : ""), onclick: cell ? () => openSource(cell) : null, title: cell ? "Open source" : null }, v));
  page.append(el("div", { class: "stats" },
    stat("Market cap", fmtMoney(s.mcap)), stat("Revenue TTM", fmtMoney(s.rev?.v), s.rev), stat("Net income TTM", fmtMoney(s.ni?.v), s.ni), stat("EPS TTM", s.eps ? "$" + s.eps.v.toFixed(2) : "–", s.eps),
    stat("P/E TTM", s.pe ? s.pe.toFixed(1) : "–"), stat("P/S TTM", s.ps ? s.ps.toFixed(1) : "–"), stat("FCF yield", fpct(s.fcfy), s.fcf), stat("Gross margin LQ", fpct(s.gm?.v), s.gm), stat("Rev growth LQ", fsgn(s.growth?.v), s.growth),
    el("div", {}, el("div", { class: "l" }, "52-week range"), el("div", { class: "num", style: "font-size:12.5px;display:flex;justify-content:space-between;margin-top:4px" }, el("span", {}, fnum(st.lo52)), el("span", {}, fnum(st.hi52))), rng)));
  const tabsDef = [["overview", "Overview"], ["financials", "Financials"], ["news", "News & narratives"], ["earnings", "Earnings & IR"], ["filings", "Filings"], ["insiders", "Insiders"]];
  const tabs = el("div", { class: "tabs", role: "tablist" });
  const body = el("div", { style: "display:flex;flex-direction:column;gap:16px" });
  const show = id => { if (!tabsDef.find(t => t[0] === id)) id = "overview"; STATE.coTab = id; store.set("coTab", id); $("#crumb-tab").textContent = tabsDef.find(t => t[0] === id)[1]; tabs.querySelectorAll("button").forEach(b => { b.classList.toggle("on", b.dataset.id === id); b.setAttribute("aria-selected", b.dataset.id === id); }); body.innerHTML = ""; ({ overview: coOverview, financials: coFinancials, news: coNews, filings: coFilings, earnings: coEarnings, insiders: coInsiders })[id](body, tk); };
  tabsDef.forEach(([id, n], i) => tabs.append(el("button", { "data-id": id, role: "tab", title: `${n} (${i + 1})`, onclick: () => show(id) }, n)));
  page.append(tabs, body);
  show(STATE.coTab);
}
function coNews(body, tk) {
  const N = E?.narratives?.[tk], news = E?.news?.[tk]?.news || [], tr = [];
  const m = model(tk), lq = m.latestQ();
  const g = el("div", { class: "grid g64" });
  const nb = el("div", { class: "bd" });
  if (N) {
    const grid = el("div", { class: "narr" });
    (N.narratives || []).forEach(n => {
      const tone = n.stance === "bull" ? "up" : n.stance === "bear" ? "down" : "";
      const metrics = (n.metrics || []).map(lab => { const cell = m.metric(lab, "Q", lq); return cell ? cellChip(cell, `${lab} ${fmtCell(cell)}`) : null; }).filter(Boolean);
      const refs = (n.news || []).map(i => news[i - 1] ? el("a", { class: "chip", href: news[i - 1].url, target: "_blank", rel: "noopener", title: news[i - 1].title }, `[${i}] ${domainOf(news[i - 1].url)}`) : null).filter(Boolean);
      grid.append(el("div", { class: "card" }, el("div", { style: "display:flex;gap:6px;align-items:center" }, el("span", { class: "chip " + tone }, { bull: "Bullish", bear: "Bearish", neutral: "Neutral" }[n.stance] || n.stance)), el("h3", {}, n.title), el("p", {}, n.summary), metrics.length ? el("div", { class: "refs", style: "display:flex;gap:6px;flex-wrap:wrap" }, metrics) : null, refs.length ? el("div", { style: "display:flex;gap:6px;flex-wrap:wrap" }, refs) : null));
    });
    nb.append(grid);
  } else nb.append(el("div", { class: "empty" }, "No narratives in this snapshot."));
  g.append(panel("Trending narratives", aiCredit(N?.meta), nb, el("div", { class: "src" }, "Built from the latest earnings release, reported figures and recent coverage. Metric chips open the filing; source chips open the article.")));
  const side = el("div", { style: "display:flex;flex-direction:column;gap:16px" });
  side.append(panel("Recent coverage", el("span", { class: "faint", style: "font-size:12px" }, `Updated ${relTime(E?.news?.[tk]?.fetched_at)}`), el("div", { class: "bd", style: "padding-top:0" }, newsList(news, N?.news_lines, 10))));
  if (tr.length) side.append(panel("Earnings call transcripts", null, el("div", { class: "bd", style: "padding-top:0" }, el("div", { class: "news" }, tr.map(x => el("a", { class: "it", href: x.url, target: "_blank", rel: "noopener" }, el("span", { class: "tt" }, x.title), el("span", { class: "mt" }, domainOf(x.url)))))), el("div", { class: "src" }, "Links to third-party transcripts. Licensed ingestion is on the roadmap.")));
  g.append(side);
  body.append(g);
}
function signals(tk) {
  const m = model(tk), out = [];
  const qs = m.periods("Q"), lq = qs.at(-1); if (!lq) return out;
  const L = e => plabel(tk, e, "Q");
  const g = m.ratio("Revenue growth YoY", "Q", lq), gp = m.ratio("Revenue growth YoY", "Q", qs.at(-2));
  if (g) out.push({ tone: g.v >= 0 ? "up" : "down", text: `Revenue ${g.v >= 0 ? "grew" : "fell"} ${fpct(Math.abs(g.v))} year over year in ${L(lq)}${gp ? `, ${g.v > gp.v ? "accelerating from" : "slowing from"} ${fsgn(gp.v)} the quarter before` : ""}.`, refs: [[m.get("Revenue", "Q", lq), "Revenue " + L(lq)], [g, "Growth calc"]] });
  const gms = qs.slice(-8).map(e => m.ratio("Gross margin", "Q", e)).filter(Boolean);
  if (gms.length >= 4) { const cur = gms.at(-1), hi = Math.max(...gms.map(x => x.v)), lo = Math.min(...gms.map(x => x.v)); const pos = cur.v === hi ? `the highest of the last ${gms.length} quarters` : cur.v === lo ? `the lowest of the last ${gms.length} quarters` : `inside its ${gms.length}-quarter range of ${fpct(lo)} to ${fpct(hi)}`; out.push({ tone: cur.v === lo ? "down" : cur.v === hi ? "up" : "neutral", text: `Gross margin was ${fpct(cur.v)} in ${L(lq)}, ${pos}.`, refs: [[cur, "Gross margin " + L(lq)]] }); }
  const opx = m.get("Operating expenses", "Q", lq), opxP = m.priorEnd("Q", lq) && m.get("Operating expenses", "Q", m.priorEnd("Q", lq));
  if (opx && opxP && g) { const og = opx.v / opxP.v - 1; out.push({ tone: og < g.v ? "up" : "warn", text: `Operating expenses ${og >= 0 ? "rose" : "fell"} ${fpct(Math.abs(og))} against revenue ${fsgn(g.v)}, ${og < g.v ? "so operating leverage improved" : "so costs outpaced sales"}.`, refs: [[opx, "Opex " + L(lq)], [opxP, "Opex prior year"]] }); }
  const cr = m.ttm("Buybacks"), dv = m.ttm("Dividends paid"), ocf = m.ttm("Operating cash flow"), cx = m.ttm("Capex");
  if (cr && ocf && cx) { const fcf = ocf.v - cx.v, ret = cr.v + (dv?.v || 0); out.push({ tone: ret > fcf ? "warn" : "neutral", text: `Over the last four quarters the company returned ${fmtMoney(ret)} through buybacks${dv ? " and dividends" : ""}, ${ret > fcf ? "more than" : "within"} its ${fmtMoney(fcf)} of free cash flow (${fpct(ret / fcf, 0)}).`, refs: [[cr, "Buybacks TTM"], [ocf, "OCF TTM"], [cx, "Capex TTM"]] }); }
  const er = earningsReactions(tk).at(0);
  if (er) { const since = last(tk).c / PX[tk].bars.find(b => b.t >= er.t).c - 1; out.push({ tone: since >= 0 ? "up" : "down", text: `Shares moved ${fsgn(er.r1)} the session after the ${fdate(er.date)} earnings release (SPY ${fsgn(er.s1)}) and are ${fsgn(since)} since.`, refs: [] }); }
  const cash = m.ratio("Cash & securities", "Q", lq), cashP = m.priorEnd("Q", lq) && m.ratio("Cash & securities", "Q", m.priorEnd("Q", lq));
  if (cash && cashP) out.push({ tone: "neutral", text: `Cash and current marketable securities stand at ${fmtMoney(cash.v)}, ${cash.v >= cashP.v ? "up" : "down"} from ${fmtMoney(cashP.v)} a year earlier.`, refs: [[cash, "Cash " + L(lq)], [cashP, "Cash prior year"]] });
  return out;
}
function coOverview(body, tk) {
  const m = model(tk);
  const g = el("div", { class: "grid g64" });
  const pxHost = el("div", { class: "bd" });
  let rng = "1Y";
  const draw = () => { const days = { "3M": 91, "6M": 182, "1Y": 365, "2Y": 730 }[rng]; const t0 = new Date(last(tk).t - days * 864e5); const b = PX[tk].bars.filter(x => x.t >= t0); const marks = earningsReactions(tk).filter(e => e.t >= t0).map(e => ({ t: e.t, label: "ER" })); const spy = PX.SPY.bars.filter(x => x.t >= t0); lineChart(pxHost, [{ name: tk, pts: b.map(x => [x.t, x.c / b[0].c - 1]) }, { name: "SPY", pts: spy.map(x => [x.t, x.c / spy[0].c - 1]), color: css("--c6"), width: 1.2, dash: "3 3" }], { height: 250, yfmt: v => fpct(v, 0), tfmt: v => fsgn(v), marks }); };
  g.append(panel("Price vs S&P 500", seg([["3M", "3M"], ["6M", "6M"], ["1Y", "1Y"], ["2Y", "2Y"]], rng, v => { rng = v; draw(); }), pxHost, el("div", { class: "src" }, "Daily closes, Yahoo Finance. ER marks the session after each 8-K Item 2.02 earnings release.")));
  const sg = el("div", { class: "bd sig" });
  const aiBtn = el("button", { class: "btn", onclick: () => { location.hash = "research"; setTimeout(() => RESEARCH?.ask(`Write a five-bullet brief on ${tk}: latest quarter results vs the prior year, margin trend, cash returns vs free cash flow, and how the stock has reacted to earnings. Cite every financial number.`), 80); } }, svgUse("i-spark"), "AI brief");
  signals(tk).forEach(s => sg.append(el("div", { class: "it" }, el("span", { class: "dot", style: `background:${s.tone === "up" ? css("--up") : s.tone === "down" ? css("--down") : s.tone === "warn" ? css("--warn") : css("--c6")}` }), el("div", {}, el("div", { class: "tx" }, s.text), s.refs.length ? el("div", { class: "refs" }, s.refs.filter(r => r[0]).map(r => cellChip(r[0], r[1]))) : null))));
  g.append(panel("Signals from the filings", aiBtn, sg, el("div", { class: "src" }, "Computed from reported figures. Highlighted chips open the filing behind each number.")));
  body.append(g);
  const g2 = el("div", { class: "grid g2" });
  const revHost = el("div", { class: "bd" }), mHost = el("div", { class: "bd" });
  g2.append(panel("Quarterly revenue and net income", null, revHost, el("div", { class: "src" }, "Click a bar to open its source filing.")));
  g2.append(panel("Margins by quarter", null, mHost));
  body.append(g2);
  requestAnimationFrame(() => {
    draw();
    const qs = m.periods("Q").slice(-12);
    barChart(revHost, qs.map(e => ({ k: plabel(tk, e, "Q"), vals: [{ v: m.get("Revenue", "Q", e)?.v, name: "Revenue", cell: m.get("Revenue", "Q", e) }, { v: m.get("Net income", "Q", e)?.v, name: "Net income", cell: m.get("Net income", "Q", e) }] })), { legend: ["Revenue", "Net income"], height: 230 });
    const ms = ["Gross margin", "Operating margin", "Net margin"].map(l => ({ name: l, pts: qs.map(e => [plabel(tk, e, "Q"), m.ratio(l, "Q", e)?.v ?? null]) }));
    lineChart(mHost, ms, { band: qs.map(e => plabel(tk, e, "Q")), height: 230, yfmt: v => fpct(v, 0), dots: true, onClick: lab => { const e = qs.find(q => plabel(tk, q, "Q") === lab); openSource(m.ratio("Gross margin", "Q", e)); } });
  });
}
async function saveFile(filename, data, mime) {
  try { const dl = window.claude ? await window.claude.use("downloads") : null; if (dl) { const r = await dl.save({ filename, data: new Blob([data], { type: mime }) }); if (r?.status) toast(`Saved ${filename}`); return; } } catch (e) { if (e?.code === "cancelled" || e?.code === "declined") return; }
  copyText(data, "Downloads aren't available here. CSV copied to clipboard");
}
function coFinancials(body, tk) {
  const m = model(tk);
  const rangeOpts = () => STATE.finKind === "A" ? [["5", "5Y"], ["10", "10Y"], ["all", "All"]] : [["8", "8Q"], ["16", "16Q"], ["all", "All"]];
  const rangeHost = el("span");
  const csv = el("button", { class: "btn", title: "Export visible table as CSV" }, svgUse("i-dl"), "CSV");
  const dense = el("button", { class: "btn" + (STATE.finDense ? " pri" : ""), title: "Compact rows" }, "Compact");
  const tb1 = el("div", { class: "toolbar" },
    seg([["income", "Income"], ["balance", "Balance sheet"], ["cashflow", "Cash flow"], ["ratios", "Ratios & KPIs"]], STATE.finStmt, v => { STATE.finStmt = v; draw(); }),
    seg([["A", "Annual"], ["Q", "Quarterly"]], STATE.finKind, v => { STATE.finKind = v; STATE.finRange = "all"; drawRange(); draw(); }),
    rangeHost,
    seg([["values", "Values"], ["common", "Common size"], ["yoy", "+ YoY"]], STATE.finView, v => { STATE.finView = v; draw(); }),
    el("span", { class: "sp" }), dense, csv);
  const legend = el("div", { class: "legend", style: "padding:8px 16px;border-bottom:1px solid var(--line)" }, el("span", {}, el("i", { style: "background:var(--c3)" }), "Derived quarter"), el("span", {}, el("i", { style: "background:var(--c4)" }), "Calculated"), el("span", { class: "faint" }, "USD millions except per share · click a value for its source · click a row name for its chart"));
  const wrap = el("div", { class: "fin-wrap" });
  body.append(el("section", { class: "panel" }, tb1, legend, wrap, el("div", { class: "src", style: "padding-top:10px" }, "XBRL facts from each 10-K and 10-Q, using the filing that first reported the period. Quarters not reported on their own are derived from cumulative figures and marked.")));
  function drawRange() { rangeHost.innerHTML = ""; rangeHost.append(seg(rangeOpts(), STATE.finRange, v => { STATE.finRange = v; draw(); })); }
  dense.onclick = () => { STATE.finDense = !STATE.finDense; store.set("dense", STATE.finDense); dense.classList.toggle("pri", STATE.finDense); draw(); };
  let current = null;
  csv.onclick = () => { if (!current) return; const { ends, rows, kind } = current; const lines = [["Metric", ...ends.map(e => plabel(tk, e, kind))].join(",")]; rows.forEach(r => lines.push([`"${r.label}"`, ...ends.map(e => { const c = r.get(e); return c?.v ?? ""; })].join(","))); saveFile(`${tk}_${STATE.finStmt}_${kind === "A" ? "annual" : "quarterly"}.csv`, lines.join("\n"), "text/csv"); };
  drawRange();
  function draw() {
    const kind = STATE.finKind, stmt = STATE.finStmt, view = stmt === "ratios" ? "values" : STATE.finView;
    let ends = stmt === "balance" ? (() => { const s = new Set(); m.rows.balance.forEach(r => r[kind].forEach((_, k) => s.add(k))); return [...s].sort(); })() : m.periods(kind);
    if (STATE.finRange !== "all") ends = ends.slice(-Number(STATE.finRange));
    const rows = stmt === "ratios" ? m.ratioDefs.filter(d => !(d.annualOnly && kind === "Q")).map(d => ({ label: d.label, unit: d.unit, get: e => m.ratio(d.label, kind, e) })) : m.rows[stmt].map(r => ({ label: r.label, unit: r.unit, get: e => m.near(r[kind], e, 0) }));
    current = { ends, rows, kind };
    const base = stmt === "balance" ? "Total assets" : "Revenue";
    const t = el("table", { class: "fin" + (STATE.finDense ? " compact" : "") });
    t.append(el("thead", {}, el("tr", {}, el("th", { class: "lab" }, STMT_NAME[stmt], view === "common" ? el("span", { class: "pd", style: "font-family:var(--f-ui)" }, `% of ${base.toLowerCase()}`) : null), ends.map((e, i) => el("th", { "data-i": i }, plabel(tk, e, kind), el("span", { class: "pd" }, e))))));
    const tb = el("tbody");
    rows.forEach(r => {
      const cells = ends.map(e => r.get(e));
      const vals = cells.map(c => c?.v ?? null);
      const isMoney = r.unit !== "pct" && r.unit !== "x" && r.unit !== "USD/shares" && r.unit !== "shares";
      const lab = el("td", { class: "lab", title: "Show chart" }, el("div", { class: "row" }, el("span", { class: "nm" }, r.label), sparkSVG(vals.slice(-12), css("--c1"), 56, 18)));
      const tr = el("tr", { class: (TOTALS.has(r.label) ? "tot" : "") + (SUBS.has(r.label) ? " sub" : "") }, lab);
      cells.forEach((c, i) => {
        if (!c || c.v == null) { tr.append(el("td", { class: "faint", "data-i": i }, "–")); return; }
        let txt = r.unit === "pct" ? fpct(c.v) : r.unit === "x" ? fx(c.v) : fmtM(c.v, r.unit);
        if (view === "common" && isMoney) { const b = m.metric(base, kind, ends[i]); txt = b?.v ? fpct(c.v / b.v) : "–"; }
        const td = el("td", { "data-i": i, class: "c" + (c.derived ? " der" : "") + (c.calc ? " calc" : "") + (c.v < 0 && r.unit !== "pct" ? " neg" : ""), tabindex: "0", title: `${c.v.toLocaleString("en-US")} ${r.unit === "pct" ? "" : r.unit}\n${c.derived ? "Derived from cumulative figures" : c.calc ? c.calc.formula : `${c.rec.form} filed ${c.rec.filed}`}` }, txt);
        td.onclick = () => { document.querySelectorAll("td.c.sel").forEach(x => x.classList.remove("sel")); td.classList.add("sel"); openSource(c); };
        td.onkeydown = e => { if (e.key === "Enter") td.click(); };
        tr.append(td);
      });
      tb.append(tr);
      if (view === "yoy" && r.unit !== "pct" && r.unit !== "x") {
        const yr = el("tr", {}, el("td", { class: "lab" }, el("div", { class: "row" }, el("span", { class: "nm faint", style: "font-weight:400;padding-left:14px" }, "YoY"))));
        ends.forEach((e, i) => { const pe = m.priorEnd(kind, e); const p = pe ? r.get(pe) : null; const c = cells[i]; const v = c && p && p.v ? c.v / Math.abs(p.v) - Math.sign(p.v) : null; yr.append(el("td", { class: "yoy " + cls(v), "data-i": i }, v == null ? "" : fsgn(v))); });
        tb.append(yr);
      }
      lab.onclick = () => {
        const nx = tr.nextElementSibling; if (nx?.classList.contains("exp")) { nx.remove(); return; }
        const host = el("div", { style: "max-width:900px" });
        const ex = el("tr", { class: "exp" }, el("td", { class: "lab" }, el("div", { class: "muted", style: "font-size:12px;white-space:normal" }, `${r.label}, ${kind === "A" ? "annual" : "quarterly"}. Click a bar for its source.`)), el("td", { colspan: ends.length, style: "text-align:left" }, host));
        tr.after(ex);
        requestAnimationFrame(() => barChart(host, ends.map((e, i) => ({ k: plabel(tk, e, kind), vals: [{ v: cells[i]?.v ?? null, name: r.label, cell: cells[i] }] })), { height: 170, yfmt: r.unit === "pct" ? v => fpct(v, 0) : r.unit === "x" ? v => v.toFixed(1) : v => fmtMoney(v, r.unit), tfmt: r.unit === "pct" ? v => fpct(v) : v => fmtMoney(v, r.unit) }));
      };
    });
    t.append(tb);
    t.addEventListener("mouseover", e => { const i = e.target.closest("td")?.dataset.i; t.querySelectorAll("th.hov").forEach(x => x.classList.remove("hov")); if (i != null) t.querySelector(`thead th[data-i="${i}"]`)?.classList.add("hov"); });
    t.addEventListener("mouseleave", () => t.querySelectorAll("th.hov").forEach(x => x.classList.remove("hov")));
    wrap.innerHTML = ""; wrap.append(t);
    requestAnimationFrame(() => { wrap.scrollLeft = wrap.scrollWidth; });
  }
  draw();
}
function coFilings(body, tk) {
  const c = D.companies[tk];
  let form = "all";
  const host = el("div", { class: "scroll" });
  const forms = ["all", ...new Set(c.filings.map(f => f.f))];
  body.append(panel(`${c.filings.length} most recent filings`, seg(forms.slice(0, 9).map(f => [f, f === "all" ? "All" : f]), form, v => { form = v; draw(); }), host));
  function draw() {
    const t = el("table", { class: "t" }, el("thead", {}, el("tr", {}, el("th", {}, "Form"), el("th", {}, "Filed"), el("th", {}, "Period"), el("th", {}, "Description"), el("th", {}, "Accession no."), el("th", {}, ""))));
    const tb = el("tbody");
    c.filings.filter(f => form === "all" || f.f === form).forEach(f => tb.append(el("tr", {}, el("td", {}, el("span", { class: "form " + formCls(f.f) }, f.f)), el("td", { class: "num" }, fdate(f.d)), el("td", { class: "num muted" }, f.r ? fdate(f.r) : "–"), el("td", {}, filingDesc({ ...f, t: tk })), el("td", { class: "mono faint", style: "font-size:12px" }, f.a), el("td", {}, el("a", { href: filingUrl(tk, f), target: "_blank", rel: "noopener" }, "Open ↗")))));
    t.append(tb); host.innerHTML = ""; host.append(t);
  }
  draw();
}
function coEarnings(body, tk) {
  const ers = earningsReactions(tk);
  const R = E?.releases?.[tk], N = E?.narratives?.[tk];
  if (R) {
    const g0 = el("div", { class: "grid g64" });
    const relLines = R.markdown.replace(/\\\$/g, "$").replace(/\*\*\*/g, "**").split("\n"); const startAt = relLines.findIndex(l => l.trim().split(/\s+/).length >= 4 && !/\.htm/.test(l));
    const doc = el("div", { class: "release", html: md(relLines.slice(Math.max(0, startAt)).join("\n")) });
    g0.append(panel(`Earnings release · filed ${fdate(R.filed)}`, el("a", { href: R.url, target: "_blank", rel: "noopener" }, "Exhibit 99.1 on EDGAR ↗"), doc, el("div", { class: "src", style: "padding-top:10px" }, `8-K accession ${R.accn}. Text taken from the filed exhibit; tables shown as filed.`)));
    const hl = el("div", { class: "bd brief" }, el("ul", {}, (N?.release_highlights || []).map(b => el("li", {}, b))));
    const side = el("div", { style: "display:flex;flex-direction:column;gap:16px" }, panel("Release highlights", aiCredit(N?.meta), hl));
    const ch = el("div", { class: "bd" });
    side.append(panel("Next-session move after each release", null, ch, el("div", { class: "src" }, "Stock vs SPY on the first session after the 8-K Item 2.02 filing date.")));
    g0.append(side);
    body.append(g0);
    requestAnimationFrame(() => barChart(ch, ers.slice().reverse().map(e => ({ k: fmon(e.date), vals: [{ v: e.r1, name: tk, color: e.r1 >= 0 ? css("--up") : css("--down") }, { v: e.s1, name: "SPY", color: css("--c6") }] })), { yfmt: v => fpct(v, 0), tfmt: v => fsgn(v, 2), height: 200, legend: [tk, "SPY"] }));
  }
  const t = el("table", { class: "t" }, el("thead", {}, el("tr", {}, el("th", {}, "Release (8-K)"), el("th", { class: "r" }, "Next 1D"), el("th", { class: "r" }, "SPY 1D"), el("th", { class: "r" }, "Excess"), el("th", { class: "r" }, "5D drift"), el("th", {}, ""))));
  const tb = el("tbody"); ers.forEach(e => tb.append(el("tr", {}, el("td", { class: "num" }, fdate(e.date)), el("td", { class: "r num " + cls(e.r1) }, fsgn(e.r1)), el("td", { class: "r num " + cls(e.s1) }, fsgn(e.s1)), el("td", { class: "r num " + cls(e.r1 - (e.s1 || 0)) }, fsgn(e.r1 - (e.s1 || 0))), el("td", { class: "r num " + cls(e.r5) }, fsgn(e.r5)), el("td", {}, el("a", { href: e.url, target: "_blank", rel: "noopener" }, "8-K ↗")))));
  t.append(tb);
  const g = el("div", { class: "grid g2" });
  g.append(panel("Release log", null, el("div", { class: "scroll" }, t)));
  const ir = el("div", { class: "bd cover" },
    srcCard("Earnings press releases", "8-K Exhibit 99.1", "live", "Latest release text pulled from EDGAR when the 8-K lands, with highlights."),
    srcCard("Investor relations site", (IR[tk] || "IR site").replace("https://", ""), "planned", "Decks, shareholder letters and event pages."),
    srcCard("Earnings call transcripts", "IR event pages", "planned", "Speaker-tagged transcripts, searchable and citable."),
    srcCard("News coverage", "News search", "live", "Recent articles, refreshed on a rolling schedule and summarized on the News tab."));
  g.append(panel("Sources for this company", IR[tk] ? el("a", { href: IR[tk], target: "_blank", rel: "noopener" }, "Open IR site ↗") : null, ir));
  body.append(g);
}
function srcCard(title, sub, status, note) {
  const map = { live: ["up", "Live"], partial: ["warn", "Partial"], planned: ["", "Planned"] };
  return el("div", { class: "c" }, el("div", { class: "s" }, el("b", {}, title), el("span", { class: "chip " + map[status][0], style: "margin-left:auto" }, map[status][1])), el("div", { class: "mono faint", style: "font-size:11.5px" }, sub), el("div", { class: "muted", style: "font-size:12.5px" }, note));
}
function coInsiders(body, tk) {
  const c = D.companies[tk];
  const f4 = c.filings.filter(f => ["4", "144", "3", "5"].includes(f.f));
  const ch = el("div", { class: "bd" });
  body.append(panel("Insider filing activity by month", null, ch, el("div", { class: "src" }, `${f4.length} Forms 3/4/5/144 in the latest ${c.filings.length} filings. Transaction-level parsing (shares, price, buy/sell code) is part of the production pipeline.`)));
  const months = {}; f4.forEach(f => { const k = f.d.slice(0, 7); months[k] = months[k] || { 4: 0, 144: 0 }; months[k][f.f === "144" ? 144 : 4]++; });
  const ks = Object.keys(months).sort();
  const t = el("table", { class: "t" }, el("thead", {}, el("tr", {}, el("th", {}, "Form"), el("th", {}, "Filed"), el("th", {}, "Transaction date"), el("th", {}, "Accession no."), el("th", {}, ""))));
  const tb = el("tbody"); f4.slice(0, 40).forEach(f => tb.append(el("tr", {}, el("td", {}, el("span", { class: "form i" }, f.f)), el("td", { class: "num" }, fdate(f.d)), el("td", { class: "num muted" }, f.r ? fdate(f.r) : "–"), el("td", { class: "mono faint", style: "font-size:12px" }, f.a), el("td", {}, el("a", { href: filingUrl(tk, f), target: "_blank", rel: "noopener" }, "Open ↗")))));
  t.append(tb);
  body.append(panel("Recent insider filings", null, el("div", { class: "scroll" }, t)));
  requestAnimationFrame(() => ks.length ? barChart(ch, ks.map(k => ({ k: fmon(k + "-01"), vals: [{ v: months[k][4], name: "Form 4" }, { v: months[k][144], name: "Form 144" }] })), { yfmt: d3.format("d"), height: 180, legend: ["Form 4", "Form 144"] }) : ch.append(el("div", { class: "empty" }, "No insider filings in the recent window.")));
}

async function pFeed() {
  FEED = FEED || await getJSON("feed.json");
  page.append(el("div", { class: "ph" }, el("div", {}, el("div", { class: "eyebrow" }, "Filings feed"), el("h1", {}, "Everything filed, in order"), el("div", { class: "sub" }, "SEC submissions across the watchlist. 8-K items are decoded so earnings, officer changes and votes stand out."))));
  let form = "core", tkf = "all";
  const host = el("div", { class: "bd", style: "padding:4px 16px 12px" });
  const right = el("div", { style: "display:flex;gap:8px;flex-wrap:wrap" }, seg([["core", "Reports & 8-Ks"], ["insider", "Insider"], ["all", "All"]], form, v => { form = v; draw(); }), (() => { const s = el("select", { class: "btn", id: "feed-tk", onchange: e => { tkf = e.target.value; draw(); } }, el("option", { value: "all" }, "All companies"), ...watchlist().map(t => el("option", { value: t }, `${t} (watchlist)`)), ...TICKERS.filter(t => !watchlist().includes(t)).map(t => el("option", { value: t }, t))); return s; })());
  page.append(panel("Filings", right, host));
  function draw() {
    host.innerHTML = "";
    const fs = allFilings().filter(f => (tkf === "all" || f.t === tkf) && (form === "all" || (form === "core" ? !["4", "144", "3", "5"].includes(f.f) : ["4", "144", "3", "5"].includes(f.f)))).slice(0, 120);
    let lastDay = "";
    fs.forEach(f => { if (f.d !== lastDay) { host.append(el("div", { class: "eyebrow", style: "padding:14px 0 2px" }, fdate(f.d))); lastDay = f.d; } host.append(filingRow(f)); });
    if (!fs.length) host.append(el("div", { class: "empty" }, "No filings match these filters."));
  }
  draw();
}

async function pTechnicals() {
  const syms = [...Object.keys(MARKET?.symbols || {}), ...TICKERS];
  if (!syms.includes(STATE.taSym)) STATE.taSym = "NVDA";
  const corrSyms = [...watchlist().slice(0, 6), "SPY", "QQQ", "SMH", "XLK", "XLP", "^TNX", "GC=F", "BTC-USD"];
  await Promise.all([ensurePrices(STATE.taSym), ensurePrices("SPY"), ...corrSyms.map(ensurePrices), IDX[STATE.taSym] ? ensureCompany(STATE.taSym).catch(() => null) : null]);
  const sel = el("select", { id: "ta-sym", class: "btn", style: "padding-right:24px", onchange: e => { STATE.taSym = e.target.value; route(); } }, syms.map(s => { const o = el("option", { value: s }, `${s.replace("^", "")} · ${SYMNAME[s] || SHORT[s] || s}`); if (s === STATE.taSym) o.selected = true; return o; }));
  page.append(el("div", { class: "ph" }, el("div", {}, el("div", { class: "eyebrow" }, "Technicals"), el("h1", {}, (SYMNAME[STATE.taSym] || SHORT[STATE.taSym] || STATE.taSym), " ", el("span", { class: "muted mono", style: "font-size:18px" }, STATE.taSym.replace("^", ""))), el("div", { class: "sub" }, "Daily and 5-minute bars with moving averages, Bollinger bands, RSI and MACD. Statistics are computed in the page from the bars.")), el("div", { class: "right" }, sel)));
  const s = STATE.taSym, hasIntra = !!PX[s + "@5m"];
  const ranges = [...(hasIntra ? [["5D", "5D · 5m"]] : []), ["1M", "1M"], ["3M", "3M"], ["6M", "6M"], ["1Y", "1Y"], ["2Y", "2Y"]];
  if (STATE.taRange === "5D" && !hasIntra) STATE.taRange = "1Y";
  const tog = (k, l) => { const cb = el("input", { type: "checkbox", id: "ta-" + k }); cb.checked = STATE.taInd[k]; cb.onchange = () => { STATE.taInd[k] = cb.checked; drawC(); }; return el("label", { style: "display:flex;gap:5px;align-items:center;font-size:12.5px;color:var(--muted)" }, cb, l); };
  const chartHost = el("div", { class: "bd" }), rsiHost = el("div", { class: "bd", style: "padding-top:0" }), macdHost = el("div", { class: "bd", style: "padding-top:0" });
  const g = el("div", { class: "grid g64" });
  g.append(el("section", { class: "panel" }, el("div", { class: "hd" }, seg(ranges, STATE.taRange, v => { STATE.taRange = v; drawC(); }), el("div", { class: "r" }, tog("sma", "SMA 20/50/200"), tog("bb", "Bollinger 20,2"), tog("vol", "Volume"))), chartHost, el("div", { class: "eyebrow", style: "padding:0 16px" }, "RSI 14"), rsiHost, el("div", { class: "eyebrow", style: "padding:0 16px" }, "MACD 12/26/9"), macdHost, el("div", { class: "src" }, "Yahoo Finance chart API. Indicators computed over the full 2-year history before trimming to the visible range.")));
  const st = priceStats(s);
  const kv = (l, v, c) => el("div", { style: "display:flex;justify-content:space-between;padding:7px 0;border-bottom:1px solid var(--line);font-size:13px" }, el("span", { class: "muted" }, l), el("span", { class: "num " + (c || "") }, v));
  const stats = el("div", { class: "bd" }, kv("Last close", fnum(st.last) + " · " + fdate(st.date)), kv("1D / 1W", `${fsgn(st.chg1d, 2)} / ${fsgn(st.r1w)}`), kv("1M / 3M", `${fsgn(st.r1m)} / ${fsgn(st.r3m)}`), kv("YTD / 1Y", `${fsgn(st.ytd)} / ${fsgn(st.r1y)}`), kv("Volatility 20D / 60D", `${fpct(st.vol20)} / ${fpct(st.vol60)}`), kv("Beta / corr. vs SPY (1Y)", st.beta1y == null ? "–" : `${st.beta1y.toFixed(2)} / ${st.corrSPY1y.toFixed(2)}`), kv("Max drawdown 1Y", fpct(st.maxDD1y), "down"), kv("From 52W high", fsgn(st.fromHi), cls(st.fromHi)), kv("RSI 14", st.rsi14.toFixed(1), st.rsi14 > 70 ? "down" : st.rsi14 < 30 ? "up" : ""), kv("vs SMA 50 / 200", `${fsgn(st.last / st.sma50 - 1)} / ${st.sma200 ? fsgn(st.last / st.sma200 - 1) : "–"}`), kv("ATR 14", fnum(st.atr14)), kv("Up days, 1Y", fpct(st.upDays1y, 0)));
  const side = el("div", { style: "display:flex;flex-direction:column;gap:16px" }, panel("Statistics", null, stats));
  if (D.companies[s]) {
    const ers = earningsReactions(s).slice(0, 6);
    const t = el("table", { class: "t" }, el("thead", {}, el("tr", {}, el("th", {}, "Earnings"), el("th", { class: "r" }, "1D"), el("th", { class: "r" }, "vs SPY"), el("th", { class: "r" }, "5D"))));
    const tb = el("tbody"); ers.forEach(e => tb.append(el("tr", {}, el("td", { class: "num" }, fdate(e.date)), el("td", { class: "r num " + cls(e.r1) }, fsgn(e.r1)), el("td", { class: "r num " + cls(e.r1 - (e.s1 || 0)) }, fsgn(e.r1 - (e.s1 || 0))), el("td", { class: "r num " + cls(e.r5) }, fsgn(e.r5))))); t.append(tb);
    side.append(panel("Earnings reactions", null, t));
  }
  const aiOut = el("div", { class: "bd", style: "font-size:13.5px" }, el("div", { class: "muted" }, "Get a plain-language read of these statistics: trend, momentum, volatility and behavior against SPY."));
  const aiBtn = el("button", { class: "btn" }, svgUse("i-spark"), "Explain");
  aiBtn.onclick = async () => {
    aiBtn.disabled = true; aiOut.innerHTML = ""; aiOut.append(el("span", { class: "thinking" }, el("i"), "Working"));
    try { const r = await agentLoop({ system: "You are a market technician writing for an equity research terminal. Be concrete and brief. No advice to buy or sell.", turns: [{ role: "user", content: `In 4 short bullets, interpret these computed statistics for ${s} (${SYMNAME[s] || SHORT[s] || ""}). Use the numbers; cover trend, momentum, volatility regime and relative behavior vs SPY.\n\n${JSON.stringify(st)}\n\nEarnings reactions: ${JSON.stringify(D.companies[s] ? earningsReactions(s).slice(0, 6).map(e => ({ date: e.date, next1d: +(e.r1 * 100).toFixed(2), spy: e.s1 && +(e.s1 * 100).toFixed(2) })) : [])}` }], tools: [], onText: ({ text }) => { aiOut.innerHTML = md(text); } }); aiOut.innerHTML = md(r.text); }
    catch (e) { aiOut.innerHTML = ""; aiOut.append(el("div", { class: "note" }, AI_ERR[e.code] || AI_ERR.upstream_error)); }
    finally { aiBtn.disabled = false; }
  };
  side.append(panel("Plain-language read", aiBtn, aiOut));
  g.append(side);
  page.append(g);
  const corrHost = el("div", { class: "bd" });
  page.append(panel("Correlation of daily returns, last 60 sessions", null, el("div", { class: "scroll" }, corrHost), el("div", { class: "src" }, "Pearson correlation on aligned daily closes.")));
  requestAnimationFrame(() => { drawC(); drawCorr(corrHost, corrSyms); });

  function drawC() {
    const intra = STATE.taRange === "5D";
    const src = intra ? PX[s + "@5m"].bars : PX[s].bars;
    const cl = src.map(b => b.c);
    const S20 = sma(cl, 20), S50 = sma(cl, 50), S200 = sma(cl, 200);
    const sdv = cl.map((_, i) => i < 19 ? null : sd(cl.slice(i - 19, i + 1)));
    const R = rsi(cl), E12 = ema(cl, 12), E26 = ema(cl, 26), MAC = cl.map((_, i) => E12[i] != null && E26[i] != null ? E12[i] - E26[i] : null);
    const sigRaw = ema(MAC.map(x => x ?? 0), 9), SIG = MAC.map((x, i) => x == null || i < 34 ? null : sigRaw[i]);
    let vwap = null; if (intra) { let pv = 0, vv = 0, day = ""; vwap = src.map(b => { const d = b.t.toISOString().slice(0, 10); if (d !== day) { pv = 0; vv = 0; day = d; } pv += (b.h + b.l + b.c) / 3 * b.v; vv += b.v; return vv ? pv / vv : null; }); }
    const days = { "1M": 31, "3M": 92, "6M": 183, "1Y": 366, "2Y": 9999 }[STATE.taRange];
    let i0 = intra ? 0 : src.findIndex(b => b.t >= new Date(src.at(-1).t - days * 864e5)); if (i0 < 0) i0 = 0;
    const idx = d3.range(i0, src.length);
    candle(chartHost, src, idx, { S20, S50, S200, sdv, vwap, intra });
    subLine(rsiHost, idx.map(i => [src[i].t, R[i]]), { lines: [30, 70], domain: [0, 100], intra, bars: null });
    subLine(macdHost, idx.map(i => [src[i].t, MAC[i]]), { sig: idx.map(i => [src[i].t, SIG[i]]), hist: idx.map(i => [src[i].t, MAC[i] != null && SIG[i] != null ? MAC[i] - SIG[i] : null]), intra });
  }
  function candle(host, src, idx, ind) {
    host.innerHTML = "";
    const W = host.clientWidth || 700, H = 330, m = { t: 10, r: 56, b: 24, l: 6 }, volH = STATE.taInd.vol ? 54 : 0;
    const x = d3.scaleBand().domain(idx).range([m.l, W - m.r]).padding(.25);
    const lo = d3.min(idx, i => Math.min(src[i].l, STATE.taInd.bb && ind.sdv[i] ? ind.S20[i] - 2 * ind.sdv[i] : Infinity));
    const hi = d3.max(idx, i => Math.max(src[i].h, STATE.taInd.bb && ind.sdv[i] ? ind.S20[i] + 2 * ind.sdv[i] : -Infinity));
    const y = d3.scaleLinear().domain([lo, hi]).nice().range([H - m.b - volH, m.t]);
    const svg = d3.select(host).append("svg").attr("viewBox", `0 0 ${W} ${H}`).attr("height", H);
    svg.append("g").attr("class", "grid").attr("transform", `translate(${W - m.r},0)`).call(d3.axisRight(y).ticks(6).tickSize(-(W - m.r - m.l)).tickFormat(d3.format(",.2f"))).call(g => g.select(".domain").remove()).call(g => g.selectAll("text").attr("x", 6));
    const tf = ind.intra ? d3.utcFormat("%a %H:%M") : d3.utcFormat("%b %d");
    const every = Math.ceil(idx.length / Math.max(3, Math.floor(W / 90)));
    svg.append("g").attr("transform", `translate(0,${H - m.b})`).call(d3.axisBottom(x).tickValues(idx.filter((_, j) => j % every === 0)).tickFormat(i => tf(new Date(src[i].t.getTime() + (ind.intra ? (PX[STATE.taSym + "@5m"]?.meta.gmtoffset || -18000) * 1000 : 0)))).tickSizeOuter(0));
    if (STATE.taInd.vol) { const vy = d3.scaleLinear().domain([0, d3.max(idx, i => src[i].v) || 1]).range([H - m.b, H - m.b - volH + 6]); svg.append("g").selectAll("rect").data(idx).join("rect").attr("x", i => x(i)).attr("width", x.bandwidth()).attr("y", i => vy(src[i].v)).attr("height", i => H - m.b - vy(src[i].v)).attr("fill", i => src[i].c >= src[i].o ? css("--up") : css("--down")).attr("opacity", .28); }
    if (STATE.taInd.bb) { const pts = idx.filter(i => ind.sdv[i] != null); svg.append("path").attr("d", d3.area().x(i => x(i) + x.bandwidth() / 2).y0(i => y(ind.S20[i] - 2 * ind.sdv[i])).y1(i => y(ind.S20[i] + 2 * ind.sdv[i]))(pts)).attr("fill", css("--c4")).attr("opacity", .08); }
    const wick = Math.max(1, x.bandwidth() < 3 ? 1 : 1.1);
    const g = svg.append("g");
    if (x.bandwidth() < 2.2) g.append("path").attr("d", d3.line().x(i => x(i) + x.bandwidth() / 2).y(i => y(src[i].c))(idx)).attr("fill", "none").attr("stroke", css("--ink")).attr("stroke-width", 1.3);
    else {
      g.selectAll("line").data(idx).join("line").attr("x1", i => x(i) + x.bandwidth() / 2).attr("x2", i => x(i) + x.bandwidth() / 2).attr("y1", i => y(src[i].h)).attr("y2", i => y(src[i].l)).attr("stroke", i => src[i].c >= src[i].o ? css("--up") : css("--down")).attr("stroke-width", wick);
      g.selectAll("rect").data(idx).join("rect").attr("x", i => x(i)).attr("width", x.bandwidth()).attr("y", i => y(Math.max(src[i].o, src[i].c))).attr("height", i => Math.max(1, Math.abs(y(src[i].o) - y(src[i].c)))).attr("fill", i => src[i].c >= src[i].o ? css("--up") : css("--down"));
    }
    const ln = (arr, col, dash, w = 1.3) => { const pts = idx.filter(i => arr[i] != null); if (pts.length) svg.append("path").attr("d", d3.line().x(i => x(i) + x.bandwidth() / 2).y(i => y(arr[i]))(pts)).attr("fill", "none").attr("stroke", col).attr("stroke-width", w).attr("stroke-dasharray", dash); };
    if (STATE.taInd.sma) { ln(ind.S20, css("--c3")); ln(ind.S50, css("--c1")); ln(ind.S200, css("--c5"), null, 1.6); }
    if (STATE.taInd.bb) { ln(ind.S20.map((v, i) => ind.sdv[i] != null ? v + 2 * ind.sdv[i] : null), css("--c4"), "3 3", 1); ln(ind.S20.map((v, i) => ind.sdv[i] != null ? v - 2 * ind.sdv[i] : null), css("--c4"), "3 3", 1); }
    if (ind.vwap) ln(ind.vwap, css("--c2"), "4 2", 1.4);
    const lastI = idx.at(-1); svg.append("rect").attr("x", W - m.r + 2).attr("y", y(src[lastI].c) - 9).attr("width", m.r - 4).attr("height", 18).attr("rx", 3).attr("fill", css("--ink")); svg.append("text").attr("x", W - m.r + 6).attr("y", y(src[lastI].c) + 4).text(fnum(src[lastI].c)).style("fill", css("--bg")).style("font-weight", 600);
    const cross = svg.append("line").attr("y1", m.t).attr("y2", H - m.b).attr("stroke", css("--faint")).attr("stroke-dasharray", "2 2").style("display", "none");
    svg.append("rect").attr("x", m.l).attr("y", m.t).attr("width", W - m.l - m.r).attr("height", H - m.t - m.b).attr("fill", "transparent").on("mousemove", ev => {
      const [mx] = d3.pointer(ev); const j = Math.max(0, Math.min(idx.length - 1, Math.floor((mx - m.l) / x.step()))); const i = idx[j], b = src[i];
      cross.style("display", null).attr("x1", x(i) + x.bandwidth() / 2).attr("x2", x(i) + x.bandwidth() / 2);
      showTip(ev, `<div style="opacity:.7">${ind.intra ? d3.utcFormat("%a %b %d %H:%M")(new Date(b.t.getTime() + (PX[STATE.taSym + "@5m"]?.meta.gmtoffset || -18000) * 1000)) + " ET" : d3.utcFormat("%a %b %d, %Y")(b.t)}</div>O ${fnum(b.o)} H ${fnum(b.h)}<br>L ${fnum(b.l)} C <b>${fnum(b.c)}</b><br>Vol ${d3.format(".3s")(b.v)}${STATE.taInd.sma && ind.S50[i] ? `<br><span style="color:${css("--c1")}">SMA50 ${fnum(ind.S50[i])}</span>` : ""}`);
    }).on("mouseleave", () => { cross.style("display", "none"); hideTip(); });
    const lg = el("div", { class: "legend", style: "margin-top:6px" });
    if (STATE.taInd.sma) lg.append(el("span", {}, el("i", { style: "background:var(--c3)" }), "SMA 20"), el("span", {}, el("i", { style: "background:var(--c1)" }), "SMA 50"), el("span", {}, el("i", { style: "background:var(--c5)" }), "SMA 200"));
    if (STATE.taInd.bb) lg.append(el("span", {}, el("i", { style: "background:var(--c4)" }), "Bollinger"));
    if (ind.vwap) lg.append(el("span", {}, el("i", { style: "background:var(--c2)" }), "VWAP (session)"));
    host.append(lg);
  }
  function subLine(host, pts, o) {
    host.innerHTML = "";
    const W = host.clientWidth || 700, H = 90, m = { t: 6, r: 56, b: 4, l: 6 };
    const x = d3.scaleUtc().domain(d3.extent(pts, p => p[0])).range([m.l, W - m.r]);
    const vals = [...pts, ...(o.sig || []), ...(o.hist || [])].map(p => p[1]).filter(v => v != null);
    const y = d3.scaleLinear().domain(o.domain || d3.extent(vals)).nice().range([H - m.b, m.t]);
    const svg = d3.select(host).append("svg").attr("viewBox", `0 0 ${W} ${H}`).attr("height", H);
    svg.append("g").attr("class", "grid").attr("transform", `translate(${W - m.r},0)`).call(d3.axisRight(y).ticks(3).tickSize(-(W - m.r - m.l)).tickFormat(d3.format(".2~f"))).call(g => g.select(".domain").remove()).call(g => g.selectAll("text").attr("x", 6));
    (o.lines || []).forEach(v => svg.append("line").attr("x1", m.l).attr("x2", W - m.r).attr("y1", y(v)).attr("y2", y(v)).attr("stroke", css("--c3")).attr("stroke-dasharray", "3 3"));
    if (o.hist) { const bw = Math.max(1, (W - m.l - m.r) / pts.length - 1); svg.selectAll(null).data(o.hist.filter(p => p[1] != null)).join("rect").attr("x", p => x(p[0]) - bw / 2).attr("width", bw).attr("y", p => y(Math.max(0, p[1]))).attr("height", p => Math.abs(y(p[1]) - y(0))).attr("fill", p => p[1] >= 0 ? css("--up") : css("--down")).attr("opacity", .45); }
    const ln = (arr, col) => svg.append("path").attr("d", d3.line().defined(p => p[1] != null).x(p => x(p[0])).y(p => y(p[1]))(arr)).attr("fill", "none").attr("stroke", col).attr("stroke-width", 1.4);
    ln(pts, css("--c1")); if (o.sig) ln(o.sig, css("--c3"));
  }
}
function drawCorr(host, list) {
  const syms = list.filter(s => PX[s] && PX[s].bars.length > 70);
  const n = syms.length;
  const M = syms.map(a => syms.map(b => { if (a === b) return 1; const [ra, rb] = alignedRets(a, b, 60); return corr(ra, rb); }));
  const g = el("div", { class: "matrix", style: `grid-template-columns:72px repeat(${n},minmax(46px,1fr));min-width:${72 + n * 48}px` });
  g.append(el("div"));
  syms.forEach(s => g.append(el("div", { class: "mono faint" }, s.replace("^", "").replace("-USD", "").replace("=F", ""))));
  const col = d3.scaleDiverging(t => d3.interpolateRgb(css("--down"), css("--up"))(t)).domain([-1, 0, 1]);
  syms.forEach((a, i) => { g.append(el("div", { class: "mono faint", style: "justify-content:start" }, a.replace("^", "").replace("-USD", "").replace("=F", ""))); syms.forEach((b, j) => { const v = M[i][j]; const c = col(v); g.append(el("div", { style: `background:color-mix(in srgb, ${c} ${Math.round(Math.abs(v) * 70)}%, var(--surface));color:${Math.abs(v) > .6 ? "#fff" : "var(--ink)"}`, title: `${a} vs ${b}: ${v.toFixed(2)}` }, i === j ? "" : v.toFixed(2))); }); });
  host.append(g);
}

function pSectors() {
  page.append(el("div", { class: "ph" }, el("div", {}, el("div", { class: "eyebrow" }, "Sectors"), el("h1", {}, "Sector rotation"), el("div", { class: "sub" }, "The eleven Select Sector SPDR ETFs measured against SPY. The rotation map plots 3-month relative strength against its 1-month change."))));
  const per = [["1W", 7], ["1M", 30], ["3M", 91], ["6M", 182], ["YTD", 0], ["1Y", 365]];
  const R = s => per.map(([k, d]) => k === "YTD" ? ytd(s) : retOver(s, d));
  const t = el("table", { class: "t" }, el("thead", {}, el("tr", {}, el("th", {}, "Sector"), per.map(([k]) => el("th", { class: "r" }, k)), el("th", { class: "r" }, "Vol 60D"), el("th", { class: "r" }, "Beta"), el("th", {}, "1Y"))));
  const tb = el("tbody");
  const all = R("SPY");
  const heat = (v, bench) => { const d = v - bench; const a = Math.min(1, Math.abs(d) / .08); return `background:color-mix(in srgb, ${d >= 0 ? css("--up") : css("--down")} ${Math.round(a * 28)}%, transparent)`; };
  [...SECTORS, "SPY"].forEach(s => { const r = R(s), st = priceStats(s); tb.append(el("tr", { class: "click", onclick: () => { STATE.taSym = s; location.hash = "technicals"; } }, el("td", {}, el("b", { class: "mono" }, s), " ", el("span", { class: "muted" }, SYMNAME[s])), r.map((v, i) => el("td", { class: "r num", style: s === "SPY" ? "" : heat(v, all[i]) }, fsgn(v))), el("td", { class: "r num" }, fpct(st.vol60)), el("td", { class: "r num" }, st.beta1y == null ? "1.00" : st.beta1y.toFixed(2)), el("td", { style: "width:90px" }, sparkSVG(closes(s).slice(-252), css("--c1"), 90, 22)))); });
  t.append(tb);
  page.append(panel("Returns vs SPY", el("span", { class: "muted", style: "font-size:12px" }, "Cell shading = difference from SPY over the same window"), el("div", { class: "scroll" }, t)));
  const g = el("div", { class: "grid g2" });
  const rrg = el("div", { class: "bd" }), perf = el("div", { class: "bd" });
  g.append(panel("Rotation map", null, rrg, el("div", { class: "src" }, "x: 3-month return minus SPY. y: change in that spread over the last month. Upper right is leading and improving.")));
  let sel = ["XLK", "XLF", "XLE", "XLV", "XLU"];
  g.append(panel("Normalized performance, 1 year", null, perf));
  page.append(g);
  page.append(sectorConstituents());
  requestAnimationFrame(() => {
    const W = rrg.clientWidth || 500, H = 320, m = { t: 16, r: 16, b: 30, l: 44 };
    const pts = SECTORS.map(s => { const rs3 = retOver(s, 91) - retOver("SPY", 91); const b = PX[s].bars, sb = PX.SPY.bars; const i = b.length - 22, j = sb.length - 22; const rsPrev = (b[i].c / b[Math.max(0, i - 63)].c) - (sb[j].c / sb[Math.max(0, j - 63)].c); return { s, x: rs3, y: rs3 - rsPrev }; });
    const ex = d3.max(pts, p => Math.abs(p.x)) * 1.2, ey = d3.max(pts, p => Math.abs(p.y)) * 1.2;
    const x = d3.scaleLinear().domain([-ex, ex]).range([m.l, W - m.r]), y = d3.scaleLinear().domain([-ey, ey]).range([H - m.b, m.t]);
    const svg = d3.select(rrg).append("svg").attr("viewBox", `0 0 ${W} ${H}`).attr("height", H);
    [["Leading", 1, 1], ["Weakening", 1, -1], ["Lagging", -1, -1], ["Improving", -1, 1]].forEach(([n, sx, sy]) => { svg.append("rect").attr("x", sx > 0 ? x(0) : m.l).attr("y", sy > 0 ? m.t : y(0)).attr("width", (W - m.r - m.l) / 2).attr("height", (H - m.b - m.t) / 2).attr("fill", sx > 0 && sy > 0 ? css("--up") : sx < 0 && sy < 0 ? css("--down") : css("--c3")).attr("opacity", .05); svg.append("text").attr("x", sx > 0 ? W - m.r - 6 : m.l + 6).attr("y", sy > 0 ? m.t + 12 : H - m.b - 6).attr("text-anchor", sx > 0 ? "end" : "start").text(n).style("font-weight", 600); });
    svg.append("g").attr("transform", `translate(0,${y(0)})`).call(d3.axisBottom(x).ticks(5).tickFormat(v => fpct(v, 0)));
    svg.append("g").attr("transform", `translate(${x(0)},0)`).call(d3.axisLeft(y).ticks(5).tickFormat(v => fpct(v, 0)));
    const g2 = svg.selectAll(null).data(pts).join("g").attr("transform", p => `translate(${x(p.x)},${y(p.y)})`).style("cursor", "pointer").on("click", (_, p) => { STATE.taSym = p.s; location.hash = "technicals"; }).on("mousemove", (ev, p) => showTip(ev, `<b>${p.s}</b> ${SYMNAME[p.s]}<br>RS 3M ${fsgn(p.x)}<br>1M change ${fsgn(p.y)}`)).on("mouseleave", hideTip);
    g2.append("circle").attr("r", 5).attr("fill", p => p.x >= 0 ? css("--up") : css("--down"));
    g2.append("text").attr("x", 8).attr("y", 4).text(p => p.s).style("fill", css("--ink")).style("font-weight", 600);
    const drawP = () => { const t0 = new Date(last("SPY").t - 365 * 864e5); lineChart(perf, ["SPY", ...sel].map((s, i) => { const b = PX[s].bars.filter(x => x.t >= t0); return { name: s, pts: b.map(x => [x.t, x.c / b[0].c - 1]), color: s === "SPY" ? css("--ink") : css(`--c${i}`), dash: s === "SPY" ? "3 3" : null }; }), { height: 300, yfmt: v => fpct(v, 0), tfmt: v => fsgn(v) }); };
    drawP();
  });
}

async function pMacro() {
  if (!MACRO) applyMacro(await getJSON("macro.json"));
  const M = D.macro, h = M["TSY:curve_hdr"], cv = M["TSY:curve"];
  page.append(el("div", { class: "ph" }, el("div", {}, el("div", { class: "eyebrow" }, "Macro"), el("h1", {}, "Rates, inflation and jobs"), el("div", { class: "sub" }, "Straight from the issuing agencies: Treasury's daily par yield curve, BLS series via FRED, and the Fed's H.15 effective funds rate."))));
  if (E?.macro_brief) { const mb = E.macro_brief; const g0 = el("div", { class: "grid g64" }); g0.append(panel("Macro brief", aiCredit(mb.meta), el("div", { class: "bd brief" }, el("h2", {}, mb.headline), el("ul", {}, (mb.bullets || []).map(b => el("li", { html: esc(b).replace(/\[(\d+)\]/g, (_, n) => { const it = E.macro_news?.[+n - 1]; return it ? `<a href="${esc(it.url)}" target="_blank" rel="noopener" class="mono" style="font-size:11px">[${n}]</a>` : ""; }) }))), el("div", { class: "watch" }, el("span", { class: "eyebrow", style: "margin-right:4px" }, "Watch"), (mb.watch || []).map(w => el("span", { class: "chip warn" }, w)))))); g0.append(panel("Headlines", el("span", { class: "faint", style: "font-size:12px" }, "Numbered as cited"), el("div", { class: "bd", style: "padding-top:0;max-height:360px;overflow:auto" }, newsList(E.macro_news || [], null, 16)))); page.append(g0); }
  const cur = cv.at(-1);
  const find = days => { const t = pd(cur[0]).getTime() - days * 864e5; return [...cv].reverse().find(r => pd(r[0]).getTime() <= t); };
  const c3 = find(91), c12 = find(365);
  const g = el("div", { class: "grid g2" });
  const ycH = el("div", { class: "bd" }), spH = el("div", { class: "bd" }), cpiH = el("div", { class: "bd" }), labH = el("div", { class: "bd" }), ffH = el("div", { class: "bd" }), pyH = el("div", { class: "bd" });
  g.append(panel("Treasury par yield curve", el("span", { class: "muted mono", style: "font-size:12px" }, fdate(cur[0])), ycH, el("div", { class: "src" }, el("a", { href: "https://home.treasury.gov/resource-center/data-chart-center/interest-rates", target: "_blank", rel: "noopener" }, "U.S. Department of the Treasury ↗"))));
  g.append(panel("Curve spreads", null, spH, el("div", { class: "src" }, "10Y minus 2Y and 10Y minus 3M, in percentage points.")));
  g.append(panel("CPI inflation, year over year", null, cpiH, el("div", { class: "src" }, el("a", { href: "https://fred.stlouisfed.org/series/CPIAUCSL", target: "_blank", rel: "noopener" }, "BLS CPI-U via FRED (CPIAUCSL, CPILFESL) ↗"))));
  g.append(panel("Unemployment rate", null, labH, el("div", { class: "src" }, el("a", { href: "https://fred.stlouisfed.org/series/UNRATE", target: "_blank", rel: "noopener" }, "BLS via FRED (UNRATE) ↗"))));
  g.append(panel("Effective federal funds rate", null, ffH, el("div", { class: "src" }, el("a", { href: "https://www.federalreserve.gov/releases/h15/", target: "_blank", rel: "noopener" }, "Federal Reserve H.15 ↗"), " · monthly average via DBnomics")));
  g.append(panel("Nonfarm payrolls, monthly change", null, pyH, el("div", { class: "src" }, el("a", { href: "https://fred.stlouisfed.org/series/PAYEMS", target: "_blank", rel: "noopener" }, "BLS via FRED (PAYEMS) ↗"))));
  page.append(g);
  const mk = ["DX-Y.NYB", "CL=F", "GC=F", "HG=F", "^VIX", "BTC-USD"];
  const mg = el("div", { class: "grid g3" });
  mk.forEach(s => { if (!PX[s]) return; const host = el("div", { class: "bd" }); mg.append(panel(SYMNAME[s], el("span", { class: "num " + cls(retOver(s, 30)) }, fnum(last(s).c) + " · 1M " + fsgn(retOver(s, 30))), host)); requestAnimationFrame(() => lineChart(host, [{ pts: PX[s].bars.slice(-252).map(b => [b.t, b.c]) }], { height: 130, area: true, yfmt: d3.format(",.0f") })); });
  page.append(el("div", { class: "eyebrow" }, "Market-implied macro, Yahoo Finance"), mg);
  requestAnimationFrame(() => {
    const tenor = h; const pick = r => r ? h.map((k, i) => [k, r[i + 1]]) : [];
    lineChart(ycH, [{ name: "Now " + fdate(cur[0]), pts: pick(cur) }, { name: "3 months ago", pts: pick(c3), color: css("--c3"), dash: "4 3" }, { name: "1 year ago", pts: pick(c12), color: css("--c6"), dash: "2 3" }], { band: tenor, height: 240, yfmt: v => v.toFixed(2) + "%", dots: true });
    const i2 = h.indexOf("2 Yr") + 1, i10 = h.indexOf("10 Yr") + 1, i3m = h.indexOf("3 Mo") + 1;
    lineChart(spH, [{ name: "10Y − 2Y", pts: cv.filter(r => r[i2] != null).map(r => [pd(r[0]), r[i10] - r[i2]]) }, { name: "10Y − 3M", pts: cv.filter(r => r[i3m] != null).map(r => [pd(r[0]), r[i10] - r[i3m]]), color: css("--c3") }], { height: 240, yfmt: v => v.toFixed(2), zero: true });
    lineChart(cpiH, [{ name: "CPI", pts: yoy(M["FRED:CPIAUCSL"]).map(([d, v]) => [pd(d + "-01"), v]) }, { name: "Core CPI", pts: yoy(M["FRED:CPILFESL"]).filter(x => x[0] >= "2019").map(([d, v]) => [pd(d + "-01"), v]), color: css("--c3") }], { height: 220, yfmt: v => fpct(v, 0), tfmt: v => fpct(v, 2) });
    lineChart(labH, [{ name: "Unemployment", pts: M["FRED:UNRATE"].filter(x => x[0] >= "2019").map(([d, v]) => [pd(d + "-01"), v]) }], { height: 220, area: true, yfmt: v => v.toFixed(1) + "%" });
    lineChart(ffH, [{ name: "EFFR", pts: M["FED:EFFR"].filter(x => x[0] >= "2019").map(([d, v]) => [pd(d), v]) }], { height: 220, step: true, area: true, yfmt: v => v.toFixed(2) + "%" });
    const pay = M["FRED:PAYEMS"].map((x, i, a) => i ? [x[0], x[1] - a[i - 1][1]] : null).filter(Boolean).filter(x => x[0] >= "2023");
    barChart(pyH, pay.map(([d, v]) => ({ k: fmon(d + "-01"), vals: [{ v, name: "Payrolls", color: v >= 0 ? css("--c2") : css("--down") }] })), { yfmt: v => d3.format("+,.0f")(v) + "k", height: 220 });
  });
}

/* ---------- compare ---------- */
async function pCompare() {
  await Promise.all(STATE.cmp.map(t => ensureCompany(t).catch(() => null)));
  STATE.cmp = STATE.cmp.filter(loaded);
  page.append(el("div", { class: "ph" }, el("div", {}, el("div", { class: "eyebrow" }, "Compare"), el("h1", {}, "Side by side"), el("div", { class: "sub" }, "Pick companies and a metric. Fiscal calendars differ, so periods are aligned on their end dates. Every point opens its filing."))));
  const labels = allMetricLabels();
  const pick = el("div", { class: "picker" });
  const msel = el("select", { class: "btn", id: "cmp-metric" }, labels.map(l => { const o = el("option", { value: l }, l); if (l === STATE.cmpMetric) o.selected = true; return o; }));
  const chartHost = el("div", { class: "bd" }), tableHost = el("div", { class: "scroll" }), pxHost = el("div", { class: "bd" });
  const colorOf = t => css(`--c${(STATE.cmp.indexOf(t) % 6) + 1}`);
  const renderPick = () => { pick.innerHTML = ""; STATE.cmp.forEach(t => pick.append(el("button", { class: "on", title: "Remove", onclick: () => { if (STATE.cmp.length > 1) { STATE.cmp = STATE.cmp.filter(x => x !== t); renderPick(); draw(); } } }, el("i", { style: `background:${colorOf(t)}` }), t, " ×"))); const add = el("select", { class: "btn", id: "cmp-add", onchange: async e => { const t = e.target.value; if (!t) return; e.target.disabled = true; try { await ensureCompany(t); if (!STATE.cmp.includes(t) && STATE.cmp.length < 6) STATE.cmp.push(t); } finally { renderPick(); draw(); } } }, el("option", { value: "" }, "+ Add company"), ...TICKERS.filter(t => !STATE.cmp.includes(t)).map(t => el("option", { value: t }, `${t} · ${SHORT[t]}`))); pick.append(add); };
  renderPick();
  msel.onchange = () => { STATE.cmpMetric = msel.value; draw(); };
  page.append(el("section", { class: "panel" }, el("div", { class: "toolbar" }, pick, msel, seg([["Q", "Quarterly"], ["A", "Annual"]], STATE.cmpKind, v => { STATE.cmpKind = v; draw(); }), el("span", { class: "sp" })), chartHost, el("div", { style: "border-top:1px solid var(--line)" }, tableHost)));
  page.append(panel("Share price, rebased to 100 one year ago", null, pxHost));
  function draw() {
    const kind = STATE.cmpKind, lab = STATE.cmpMetric;
    const series = STATE.cmp.map(t => ({ t, pts: model(t).series(lab, kind).slice(kind === "Q" ? -16 : -10) }));
    const unit = series.find(s => s.pts.length)?.pts[0].cell.unit;
    const yf = unit === "pct" ? v => fpct(v, 0) : unit === "x" ? v => v.toFixed(1) : v => fmtMoney(v, unit);
    lineChart(chartHost, series.map(s => ({ name: s.t, pts: s.pts.map(p => [pd(p.end), p.cell.v]), color: colorOf(s.t) })), { height: 280, yfmt: yf, tfmt: unit === "pct" ? v => fpct(v) : v => fmtMoney(v, unit), dots: true, onClick: d => { let best = null, bd = Infinity; series.forEach(s => s.pts.forEach(p => { const dd = Math.abs(pd(p.end) - d); if (dd < bd) { bd = dd; best = p.cell; } })); if (best) openSource(best); } });
    const t = el("table", { class: "t" }, el("thead", {}, el("tr", {}, el("th", {}, "Company"), el("th", {}, "Latest period"), el("th", { class: "r" }, lab), el("th", { class: "r" }, "Prior year"), el("th", { class: "r" }, "Change"), el("th", { class: "r" }, "TTM"), el("th", {}, "Trend"))));
    const tb = el("tbody");
    series.forEach(s => { const lastP = s.pts.at(-1); if (!lastP) return; const m = model(s.t); const pe = m.priorEnd(kind, lastP.end); const prior = pe && m.metric(lab, kind, pe); const ch = prior && prior.v ? (unit === "pct" ? lastP.cell.v - prior.v : lastP.cell.v / Math.abs(prior.v) - Math.sign(prior.v)) : null; const ttm = m.ttm(lab);
      tb.append(el("tr", {}, el("td", {}, el("b", {}, s.t), " ", el("span", { class: "muted" }, SHORT[s.t])), el("td", { class: "muted" }, plabel(s.t, lastP.end, kind)), el("td", { class: "r num" }, cellChip(lastP.cell, fmtCell(lastP.cell))), el("td", { class: "r num" }, prior ? cellChip(prior, fmtCell(prior)) : "–"), el("td", { class: "r num " + cls(ch) }, ch == null ? "–" : unit === "pct" ? (ch >= 0 ? "+" : "−") + Math.abs(ch * 1e4).toFixed(0) + " bp" : fsgn(ch)), el("td", { class: "r num" }, ttm ? cellChip(ttm, fmtCell(ttm)) : "–"), el("td", { style: "width:100px" }, sparkSVG(s.pts.map(p => p.cell.v), colorOf(s.t), 100, 22)))); });
    t.append(tb); tableHost.innerHTML = ""; tableHost.append(t);
    const t0 = new Date(last("SPY").t - 365 * 864e5);
    lineChart(pxHost, [...STATE.cmp.map(tk => { const b = PX[tk].bars.filter(x => x.t >= t0); return { name: tk, pts: b.map(x => [x.t, x.c / b[0].c * 100]), color: colorOf(tk) }; }), (() => { const b = PX.SPY.bars.filter(x => x.t >= t0); return { name: "SPY", pts: b.map(x => [x.t, x.c / b[0].c * 100]), color: css("--ink"), dash: "3 3", width: 1.2 }; })()], { height: 260, yfmt: v => v.toFixed(0) });
  }
  requestAnimationFrame(draw);
}

/* ---------- theses ---------- */
const DEFAULT_THESES = [
  { id: "nvda-dc", ticker: "NVDA", title: "Data-center demand holds NVIDIA's gross margin above 70%", stance: "Long", created: "2026-03-01", author: "Example",
    body: "Pricing power on accelerated computing should keep gross margin in the low-to-mid 70s while revenue keeps compounding. A break below 70% would mean competition or mix is biting.",
    checks: [{ metric: "Gross margin", period: "LQ", op: ">=", value: 0.70 }, { metric: "Revenue growth YoY", period: "LQ", op: ">=", value: 0.30 }, { metric: "Operating margin", period: "LQ", op: ">=", value: 0.55 }] },
  { id: "aapl-ret", ticker: "AAPL", title: "Apple funds buybacks from free cash flow, not the balance sheet", stance: "Watch", created: "2026-01-15", author: "Example",
    body: "Capital returns have run close to free cash flow for years. If buybacks plus dividends stay above FCF, net cash keeps shrinking and the return program has to slow.",
    checks: [{ metric: "Revenue growth YoY", period: "LQ", op: ">=", value: 0.03 }, { metric: "FCF margin", period: "LQ", op: ">=", value: 0.22 }, { metric: "Gross margin", period: "LQ", op: ">=", value: 0.45 }] },
  { id: "cost-steady", ticker: "COST", title: "Costco's membership model keeps margins thin and steady", stance: "Long", created: "2025-11-01", author: "Example",
    body: "Costco runs merchandise near break-even and earns on membership fees, so gross margin should sit in a tight band near 12-13% while sales grow high single digits.",
    checks: [{ metric: "Gross margin", period: "LQ", op: ">=", value: 0.12 }, { metric: "Revenue growth YoY", period: "LQ", op: ">=", value: 0.05 }, { metric: "Net margin", period: "LQ", op: ">=", value: 0.025 }] },
];
function evalCheck(tk, ch) {
  const m = model(tk); const kind = ch.period === "LA" ? "A" : "Q"; const end = kind === "Q" ? m.latestQ() : m.latestA();
  const cell = m.metric(ch.metric, kind, end);
  if (!cell) return { status: "na", cell: null };
  const v = cell.v, t = ch.value; const ok = ch.op === ">=" ? v >= t : v <= t;
  const margin = Math.abs(v - t) / (Math.abs(t) || 1);
  return { status: ok ? (margin < .05 ? "watch" : "pass") : "fail", cell, history: m.series(ch.metric, kind).slice(-8) };
}
async function pTheses() {
  let theses = store.get("theses", null) || DEFAULT_THESES;
  await Promise.all([...new Set(theses.map(t => t.ticker))].filter(t => IDX[t]).map(t => ensureCompany(t).catch(() => null)));
  theses = theses.filter(t => loaded(t.ticker));
  const follows = new Set(store.get("follows", ["nvda-dc"]));
  page.append(el("div", { class: "ph" }, el("div", {}, el("div", { class: "eyebrow" }, "Theses"), el("h1", {}, "Theses with live checks"), el("div", { class: "sub" }, "Write the claim, then tie it to numbers. Each check re-runs when a new 10-Q or 10-K lands, and every value opens its filing. The three below are examples.")), el("div", { class: "right" }, el("button", { class: "btn pri", onclick: () => form.hidden = !form.hidden }, "New thesis"))));
  const form = el("section", { class: "panel", hidden: true });
  const fTk = el("select", { id: "th-tk" }, TICKERS.map(t => el("option", { value: t }, t)));
  const fTitle = el("input", { id: "th-title", placeholder: "Claim in one line" });
  const fMetric = el("select", { id: "th-metric" }, allMetricLabels().map(l => el("option", { value: l }, l)));
  const fOp = el("select", { id: "th-op" }, el("option", { value: ">=" }, "at least"), el("option", { value: "<=" }, "at most"));
  const fVal = el("input", { id: "th-val", type: "number", step: "any", placeholder: "70 for 70%, or a dollar amount" });
  form.append(el("div", { class: "hd" }, el("h3", {}, "New thesis")), el("form", { class: "bd grid g4", onsubmit: async e => { e.preventDefault(); await ensureCompany(fTk.value); const lab = fMetric.value; const isPct = model(fTk.value).ratioDefs.find(d => d.label === lab)?.unit === "pct"; const v = Number(fVal.value); if (!fTitle.value.trim() || !isFinite(v)) return; theses = [{ id: "u" + Date.now(), ticker: fTk.value, title: fTitle.value.trim(), stance: "Watch", created: new Date().toISOString().slice(0, 10), author: "You", body: "", checks: [{ metric: lab, period: "LQ", op: fOp.value, value: isPct ? v / 100 : v }] }, ...theses]; store.set("theses", theses); route(); } },
    el("label", { class: "field" }, "Company", fTk), el("label", { class: "field", style: "grid-column:span 3" }, "Claim", fTitle), el("label", { class: "field" }, "Metric (latest quarter)", fMetric), el("label", { class: "field" }, "Condition", fOp), el("label", { class: "field" }, "Threshold", fVal), el("div", { style: "display:flex;align-items:end" }, el("button", { class: "btn acc", type: "submit" }, "Save thesis"))));
  page.append(form);
  const g = el("div", { class: "grid g2" });
  theses.forEach(th => {
    const res = th.checks.map(ch => ({ ch, ...evalCheck(th.ticker, ch) }));
    const worst = res.some(r => r.status === "fail") ? "fail" : res.some(r => r.status === "watch") ? "watch" : "pass";
    const chip = { pass: ["up", "On track"], watch: ["warn", "Near threshold"], fail: ["down", "Broken"] }[worst];
    const fb = el("button", { class: "btn" + (follows.has(th.id) ? " pri" : ""), onclick: () => { follows.has(th.id) ? follows.delete(th.id) : follows.add(th.id); store.set("follows", [...follows]); route(); } }, follows.has(th.id) ? "Following" : "Follow");
    const card = el("div", { class: "bd th-card" },
      el("div", { style: "display:flex;gap:8px;align-items:center;flex-wrap:wrap" }, el("a", { href: "#company-" + th.ticker.toLowerCase(), class: "chip acc mono" }, th.ticker), el("span", { class: "chip" }, th.stance), el("span", { class: "chip " + chip[0] }, chip[1]), el("span", { class: "faint", style: "font-size:12px;margin-left:auto" }, `${th.author} · since ${fdate(th.created)}`)),
      el("h2", {}, th.title), th.body ? el("p", { class: "muted", style: "margin:0" }, th.body) : null,
      el("div", {}, res.map(r => el("div", { class: "check" },
        el("span", { class: "chip " + ({ pass: "up", watch: "warn", fail: "down", na: "" }[r.status]) }, { pass: "Pass", watch: "Close", fail: "Fail", na: "n/a" }[r.status]),
        el("div", {}, el("div", {}, `${r.ch.metric} ${r.ch.op === ">=" ? "≥" : "≤"} ${model(th.ticker).ratioDefs.find(d => d.label === r.ch.metric)?.unit === "pct" ? fpct(r.ch.value, 0) : fmtMoney(r.ch.value)}`), r.history ? el("div", { style: "margin-top:4px;width:140px" }, sparkSVG(r.history.map(x => x.cell.v), css(r.status === "fail" ? "--down" : "--c1"), 140, 20)) : null),
        el("span", { class: "val", onclick: () => r.cell && openSource(r.cell), title: "Open source" }, r.cell ? `${r.cell.unit === "pct" ? fpct(r.cell.v) : fmtMoney(r.cell.v, r.cell.unit)} · ${plabel(th.ticker, r.cell.end, r.cell.kind)}` : "–")))),
      el("div", {}, el("div", { class: "eyebrow", style: "margin-bottom:6px" }, "Updates since created"), ...D.companies[th.ticker].filings.filter(f => f.d >= th.created && ["10-K", "10-Q", "8-K"].includes(f.f)).slice(0, 4).map(f => filingRow({ ...f, t: th.ticker }))),
      el("div", { style: "display:flex;gap:8px" }, fb, el("button", { class: "btn", onclick: () => { location.hash = "research"; setTimeout(() => RESEARCH?.ask(`Stress-test this thesis on ${th.ticker}: "${th.title}". ${th.body} Pull the relevant quarterly series, say what the latest filings show, and list what would break it. Cite every financial number.`), 80); } }, svgUse("i-spark"), "Stress-test with AI")));
    g.append(el("section", { class: "panel" }, card));
  });
  page.append(g);
}

/* ---------- workbench ---------- */
const COLS = "ABCDEFGHIJ".split("");
const NROWS = 24;
function defaultSheet() {
  const s = {};
  const put = (r, v) => Object.assign(s, r ? { [r]: v } : {});
  put("A1", "Comps · TTM from filings"); put("A3", "Ticker"); ["Revenue TTM", "Net income TTM", "Net margin", "Gross margin LQ", "Rev growth LQ", "Mkt cap", "P/E"].forEach((h, i) => put(COLS[i + 1] + "3", h));
  ["AAPL", "MSFT", "NVDA", "GOOGL"].forEach((t, i) => { const r = i + 4; put("A" + r, t); put("B" + r, `=FU("${t}","Revenue","TTM")`); put("C" + r, `=FU("${t}","Net income","TTM")`); put("D" + r, `=C${r}/B${r}`); put("E" + r, `=FU("${t}","Gross margin","LQ")`); put("F" + r, `=FU("${t}","Revenue growth YoY","LQ")`); put("G" + r, `=MCAP("${t}")`); put("H" + r, `=G${r}/C${r}`); });
  put("A9", "Total"); put("B9", "=SUM(B4:B7)"); put("C9", "=SUM(C4:C7)"); put("D9", "=C9/B9");
  put("A11", "NVDA quarterly revenue"); ["Q1", "Q2", "Q3", "Q4"].forEach((q, i) => put(COLS[i + 1] + "11", ""));
  const m = model("NVDA"); m.periods("Q").slice(-4).forEach((e, i) => { put(COLS[i + 1] + "12", plabel("NVDA", e, "Q")); put(COLS[i + 1] + "13", `=FU("NVDA","Revenue","${plabel("NVDA", e, "Q")}")`); });
  put("A13", "Revenue"); put("A14", "QoQ"); ["C", "D", "E"].forEach((c, i) => put(c + "14", `=${c}13/${COLS[i + 1]}13-1`));
  return s;
}
const FMT = { D: "pct", E: "pct", F: "pct", H: "x" };
function evalSheet(cells) {
  const vals = {}, meta = {}, visiting = new Set();
  const refVal = ref => { if (ref in vals) return vals[ref]; if (visiting.has(ref)) throw new Error("Circular reference"); visiting.add(ref); const v = evalCell(ref); visiting.delete(ref); vals[ref] = v; return v; };
  function evalCell(ref) {
    const raw = cells[ref]; if (raw == null || raw === "") return null;
    if (typeof raw === "string" && raw.startsWith("=")) { try { const r = parseExpr(raw.slice(1), ref); return r; } catch (e) { meta[ref] = { err: e.message }; return "#ERR"; } }
    const n = Number(raw); return raw !== "" && isFinite(n) ? n : raw;
  }
  function parseExpr(src, self) {
    let i = 0; const s = src;
    const ws = () => { while (s[i] === " ") i++; };
    const expr = () => { let v = term(); ws(); while (s[i] === "+" || s[i] === "-") { const op = s[i++]; const r = term(); v = op === "+" ? num(v) + num(r) : num(v) - num(r); ws(); } return v; };
    const term = () => { let v = pow(); ws(); while (s[i] === "*" || s[i] === "/") { const op = s[i++]; const r = pow(); v = op === "*" ? num(v) * num(r) : num(v) / num(r); ws(); } return v; };
    const pow = () => { let v = unary(); ws(); if (s[i] === "^") { i++; v = Math.pow(num(v), num(unary())); } return v; };
    const unary = () => { ws(); if (s[i] === "-") { i++; return -num(atom()); } return atom(); };
    const num = v => { if (Array.isArray(v)) throw new Error("Range used as a value"); if (v == null) return 0; if (typeof v !== "number") throw new Error("Not a number"); return v; };
    const atom = () => {
      ws();
      if (s[i] === "(") { i++; const v = expr(); ws(); if (s[i++] !== ")") throw new Error("Missing )"); return v; }
      if (s[i] === '"') { const j = s.indexOf('"', i + 1); if (j < 0) throw new Error("Unclosed string"); const v = s.slice(i + 1, j); i = j + 1; return v; }
      let m = s.slice(i).match(/^\d+(\.\d+)?%?/); if (m) { i += m[0].length; return m[0].endsWith("%") ? parseFloat(m[0]) / 100 : parseFloat(m[0]); }
      m = s.slice(i).match(/^([A-Z]+)\(/i);
      if (m) { i += m[0].length; const args = []; ws(); if (s[i] !== ")") { for (;;) { args.push(rangeOrExpr()); ws(); if (s[i] === ",") { i++; continue; } break; } } if (s[i++] !== ")") throw new Error("Missing )"); return call(m[1].toUpperCase(), args, self); }
      m = s.slice(i).match(/^([A-J])(\d+)/); if (m) { i += m[0].length; const v = refVal(m[0]); if (v === "#ERR") throw new Error("Bad input " + m[0]); return v; }
      throw new Error("Unexpected input at " + (i + 1));
    };
    const rangeOrExpr = () => { ws(); const m = s.slice(i).match(/^([A-J])(\d+):([A-J])(\d+)/); if (m) { i += m[0].length; const out = []; for (let c = COLS.indexOf(m[1]); c <= COLS.indexOf(m[3]); c++) for (let r = +m[2]; r <= +m[4]; r++) out.push(refVal(COLS[c] + r)); return out; } return expr(); };
    const v = expr(); ws(); if (i < s.length) throw new Error("Unexpected " + s[i]); return v;
  }
  function call(fn, args, self) {
    const flat = args.flat().filter(v => typeof v === "number");
    if (fn === "SUM") return flat.reduce((a, b) => a + b, 0);
    if (fn === "AVG" || fn === "AVERAGE") return flat.length ? flat.reduce((a, b) => a + b, 0) / flat.length : 0;
    if (fn === "MIN") return Math.min(...flat); if (fn === "MAX") return Math.max(...flat);
    if (fn === "FU") { if (!loaded(String(args[0]).toUpperCase())) throw new Error(IDX[String(args[0]).toUpperCase()] ? `Loading ${args[0]}…` : `${args[0]} isn't covered`); const cell = findCell(args[0], args[1], args[2]); if (!cell) throw new Error(`No value for ${args[0]} ${args[1]} ${args[2]}`); meta[self] = { ...(meta[self] || {}), cell }; return cell.v; }
    if (fn === "PRICE") { const c = IDX[String(args[0]).toUpperCase()]; if (!c?.price) throw new Error("No price"); return c.price; }
    if (fn === "MCAP") { const c = IDX[String(args[0]).toUpperCase()]; if (!c?.market_cap) throw new Error("No market cap"); return c.market_cap; }
    if (fn === "PE") { const c = IDX[String(args[0]).toUpperCase()]; if (!c?.pe_ttm) throw new Error("No P/E"); return c.pe_ttm; }
    throw new Error("Unknown function " + fn);
  }
  for (const c of COLS) for (let r = 1; r <= NROWS; r++) refVal(c + r);
  return { vals, meta };
}
function fmtSheet(v, ref, meta) {
  if (v == null) return ""; if (typeof v !== "number") return String(v);
  const unit = meta?.cell?.unit; const col = ref[0];
  if (unit === "pct" || (FMT[col] === "pct" && Math.abs(v) < 5 && !Number.isInteger(v))) return fpct(v);
  if (FMT[col] === "x") return v.toFixed(1) + "×";
  if (Math.abs(v) >= 1e6) return fmtMoney(v);
  if (!Number.isInteger(v) && Math.abs(v) < 1) return fpct(v);
  return fnum(v, Number.isInteger(v) ? 0 : 2);
}
const DEFAULT_MEMO = `<h2>NVIDIA vs. Apple: where the next dollar of cash goes</h2><p class="muted">Working note · example content</p><h3>Question</h3><p>Both companies generate large free cash flow. NVIDIA reinvests and accumulates cash while revenue compounds; Apple returns roughly all of it. Which model is more exposed if growth slows?</p><h3>What the filings show</h3><p>Use the comps sheet to the left for TTM revenue, net income, margins and valuation. Ask the assistant to pull series, update the sheet, or draft this section with citations.</p><h3>Open items</h3><ul><li>Gross margin trend over eight quarters for both</li><li>Buybacks plus dividends against free cash flow</li><li>Share-count change over three years</li></ul>`;
async function pWorkbench() {
  await Promise.all(["AAPL", "MSFT", "NVDA", "GOOGL"].map(t => ensureCompany(t)));
  let cells = store.get("sheet", null) || defaultSheet();
  const sheetTickers = c => [...new Set(Object.values(c).join(" ").match(/FU\(\s*"([A-Z.-]+)"/g)?.map(x => x.match(/"([A-Z.-]+)"/)[1]) || [])].filter(t => IDX[t]);
  await Promise.all(sheetTickers(cells).map(t => ensureCompany(t).catch(() => null)));
  let selRef = "B4";
  page.append(el("div", { class: "ph" }, el("div", {}, el("div", { class: "eyebrow" }, "Workbench"), el("h1", {}, "Sheet, memo and assistant"), el("div", { class: "sub" }, "Formulas pull filed values with =FU(ticker, metric, period). Periods take FY2025, Q2 FY26, TTM, LQ or LFY. Select a computed cell to open its source. The assistant can read and edit both the sheet and the memo.")), el("div", { class: "right" }, el("button", { class: "btn", onclick: () => { store.set("sheet", null); store.set("memo", null); route(); } }, "Reset example"))));
  const wb = el("div", { class: "wb" });
  const left = el("div", { style: "display:flex;flex-direction:column;gap:16px;min-width:0" });
  const refBox = el("span", { class: "ref" }, selRef);
  const fIn = el("input", { id: "wb-formula", "aria-label": "Formula" });
  const srcBtn = el("button", { class: "btn", disabled: true }, "Source");
  const sheetWrap = el("div", { class: "sheet-wrap" });
  left.append(el("section", { class: "panel" }, el("div", { class: "fbar" }, refBox, fIn, srcBtn), sheetWrap));
  const memo = el("div", { class: "memo", contenteditable: "true", id: "wb-memo", html: store.get("memo", null) || DEFAULT_MEMO });
  memo.addEventListener("input", () => store.set("memo", memo.innerHTML));
  left.append(el("section", { class: "panel" }, el("div", { class: "hd" }, el("h3", {}, "Memo"), el("div", { class: "r" }, el("span", { class: "faint", style: "font-size:12px" }, "Saved in this browser"))), memo));
  const ai = el("section", { class: "panel", style: "display:flex;flex-direction:column;max-height:calc(100vh - 140px);position:sticky;top:76px" });
  wb.append(left, ai); page.append(wb);
  let ev = evalSheet(cells);
  function renderSheet() {
    ev = evalSheet(cells);
    const t = el("table", { class: "sheet" });
    t.append(el("thead", {}, el("tr", {}, el("th", {}, ""), COLS.map(c => el("th", {}, c)))));
    const tb = el("tbody");
    for (let r = 1; r <= NROWS; r++) {
      const tr = el("tr", {}, el("td", { class: "rh" }, r));
      COLS.forEach(c => {
        const ref = c + r, raw = cells[ref], v = ev.vals[ref], meta = ev.meta[ref];
        const td = el("td", { class: [typeof v === "number" ? "n" : "", meta?.cell ? "f" : "", r === 1 || r === 3 || c === "A" ? "b" : "", meta?.err ? "err" : "", ref === selRef ? "sel" : ""].join(" "), title: meta?.err || (typeof raw === "string" && raw.startsWith("=") ? raw : "") }, meta?.err ? "#ERR" : fmtSheet(v, ref, meta));
        td.onclick = () => select(ref);
        td.ondblclick = () => edit(ref, td);
        tr.append(td);
      });
      tb.append(tr);
    }
    t.append(tb); sheetWrap.innerHTML = ""; sheetWrap.append(t);
  }
  function select(ref) { selRef = ref; refBox.textContent = ref; fIn.value = cells[ref] ?? ""; const m = ev.meta[ref]; srcBtn.disabled = !m?.cell; srcBtn.onclick = () => m?.cell && openSource(m.cell); sheetWrap.querySelectorAll("td.sel").forEach(x => x.classList.remove("sel")); const r = +ref.slice(1), c = COLS.indexOf(ref[0]); sheetWrap.querySelector(`tbody tr:nth-child(${r}) td:nth-child(${c + 2})`)?.classList.add("sel"); }
  async function commit(ref, val) { if (val === "" || val == null) delete cells[ref]; else cells[ref] = val; store.set("sheet", cells); await Promise.all(sheetTickers(cells).map(t => ensureCompany(t).catch(() => null))); renderSheet(); select(ref); }
  function edit(ref, td) { const inp = el("input", { value: cells[ref] ?? "" }); td.innerHTML = ""; td.append(inp); inp.focus(); inp.onkeydown = e => { if (e.key === "Enter") commit(ref, inp.value); if (e.key === "Escape") renderSheet(); }; inp.onblur = () => commit(ref, inp.value); }
  fIn.onkeydown = e => { if (e.key === "Enter") commit(selRef, fIn.value); };
  renderSheet(); select(selRef);
  const sheetText = () => { const out = []; for (let r = 1; r <= NROWS; r++) { const row = COLS.map(c => { const raw = cells[c + r]; if (raw == null) return null; const v = ev.vals[c + r]; return `${c}${r}: ${typeof raw === "string" && raw.startsWith("=") ? raw + " → " + (ev.meta[c + r]?.err ? "#ERR" : fmtSheet(v, c + r, ev.meta[c + r])) : raw}`; }).filter(Boolean); if (row.length) out.push(row.join(" | ")); } return out.join("\n"); };
  const memoText = () => memo.innerText.slice(0, 6000);
  chatPanel(ai, {
    id: "wb", placeholder: "Ask about the sheet, or tell it what to change. Ctrl/⌘+Enter to send.",
    intro: "I can read the comps sheet and the memo, pull any filed metric, run calculations, write formulas into cells and draft memo sections with citations.",
    suggestions: ["Add a FCF margin column to the comps", "Draft the 'What the filings show' section", "Which company has the steadier gross margin?"],
    rules: "Today is " + new Date().toISOString().slice(0, 10) + ". You are the Filed Under workbench assistant. The user is an equity analyst. Use the tools to read data and to edit the sheet or memo when asked. Sheet formulas: =FU(\"TICKER\",\"Metric label\",\"PERIOD\") where PERIOD is like FY2025, Q2 FY26, TTM, LQ (latest quarter) or LFY; also PRICE(\"T\"), MCAP(\"T\"), PE(\"T\"), SUM(A1:A4), AVG, MIN, MAX, + - * / ^ and cell refs A1..J24. Prefer formulas over typed numbers so values stay linked to filings. When you edit, say which cells or sections changed. Keep answers short.",
    context: () => `SHEET (ref: raw → value)\n${sheetText()}\n\nMEMO (plain text)\n${memoText()}`,
    tools: trace => {
      const wrap = (name, fn) => async (i, ctx) => { const st = trace(name, i); try { const r = await fn(i || {}, ctx); st.done(r); return r; } catch (e) { st.fail(e); throw e; } };
      return [
        { name: "set_cells", description: "Write values or formulas into sheet cells. Returns the evaluated results so you can check them.", inputSchema: { type: "object", properties: { cells: { type: "array", items: { type: "object", properties: { ref: { type: "string" }, value: { type: "string" } }, required: ["ref", "value"] } } }, required: ["cells"] },
          execute: wrap("set_cells", async i => { const out = []; await Promise.all(sheetTickers(Object.fromEntries((i.cells || []).map(c => [c.ref, String(c.value)]))).map(t => ensureCompany(t).catch(() => null))); for (const c of (i.cells || []).slice(0, 80)) { const ref = String(c.ref).toUpperCase(); if (!/^[A-J]([1-9]|1\d|2[0-4])$/.test(ref)) { out.push({ ref, error: "Out of range" }); continue; } if (c.value === "") delete cells[ref]; else cells[ref] = String(c.value); } store.set("sheet", cells); renderSheet(); for (const c of (i.cells || []).slice(0, 80)) { const ref = String(c.ref).toUpperCase(); out.push({ ref, value: ev.meta[ref]?.err ? "#ERR " + ev.meta[ref].err : fmtSheet(ev.vals[ref], ref, ev.meta[ref]) }); } return out; }) },
        { name: "write_memo_section", description: "Replace the memo section under a heading (h3 text) with new markdown, or append a new section if the heading does not exist. Keep [[TICKER|Metric|Period]] citations in the markdown.", inputSchema: { type: "object", properties: { heading: { type: "string" }, markdown: { type: "string" } }, required: ["heading", "markdown"] },
          execute: wrap("write_memo_section", i => { const h3 = [...memo.querySelectorAll("h3")].find(h => h.textContent.trim().toLowerCase() === String(i.heading).trim().toLowerCase()); const frag = el("div", { class: "fresh", html: md(i.markdown) }); if (h3) { let n = h3.nextSibling; while (n && n.nodeName !== "H3") { const nx = n.nextSibling; n.remove(); n = nx; } h3.after(frag); } else { memo.append(el("h3", {}, i.heading), frag); } store.set("memo", memo.innerHTML); setTimeout(() => frag.classList.remove("fresh"), 2500); return "Section written: " + i.heading; }) },
      ];
    },
  });
}

/* ---------- research ---------- */
let RESEARCH = null;
async function pResearch() {
  page.append(el("div", { class: "ph" }, el("div", {}, el("div", { class: "eyebrow" }, "Research"), el("h1", {}, "Research assistant"), el("div", { class: "sub" }, "Ask about any covered company, sector or macro series. The assistant looks up filed figures, prices and macro data, searches recent news when it needs to, runs calculations, and cites every financial number back to its filing. Each lookup is shown as it runs."))));
  const g = el("div", { class: "grid", style: "grid-template-columns:minmax(0,1fr) 300px" });
  const chat = el("section", { class: "panel", style: "display:flex;flex-direction:column;min-height:68vh" });
  const side = el("div", { style: "display:flex;flex-direction:column;gap:16px" },
    panel("What it can look up", null, el("div", { class: "bd", style: "display:flex;flex-direction:column;gap:8px;font-size:12.5px" }, ...[["Screen", "Rank the S&P 100 on valuation, margins, growth, returns"], ["Financials", "Filed and calculated series with sources"], ["Prices", "Returns, volatility, beta, RSI"], ["Earnings reactions", "Moves after each 8-K Item 2.02"], ["Macro", "CPI, jobs, rates, the curve"], ["Filings", "SEC filings by form"], ["Calculations", "Sandboxed code over the data"], ["Web", "Recent news and any public page"]].map(([n, d]) => el("div", {}, el("b", {}, n), el("div", { class: "muted" }, d))))),
    el("div", { class: "note" }, "Citations like ", el("span", { class: "cite" }, "NVDA Revenue Q2 FY27"), " open the filing behind the number. Answers can be wrong; check the sources."));
  g.append(chat, side); page.append(g);
  if (innerWidth < 860) g.style.gridTemplateColumns = "minmax(0,1fr)";
  RESEARCH = chatPanel(chat, {
    id: "rs", tier: "default", placeholder: "e.g. Compare free cash flow conversion across all four companies over the last eight quarters. Ctrl/⌘+Enter to send.",
    suggestions: ["Top 10 by FCF yield, and what explains the leaders?", "Which megacaps grew revenue fastest last quarter?", "Compare JPM, BAC and WFC net income over eight quarters", "How did NVDA react to its last four earnings?", "Is the yield curve still inverted, and since when?"],
    rules: "Today is " + new Date().toISOString().slice(0, 10) + ". You are Filed Under's research assistant for US equities (coverage: S&P 100). Work like a careful analyst: call tools to get facts (never guess financial figures), use screen_companies for cross-sectional questions, run_js for multi-series math, web_search only for events the data does not cover. Answer in concise markdown with a short table when comparing. Note when a quarter is derived from cumulative figures. Do not give investment advice.",
  });
}

/* ---------- coverage ---------- */
async function pCoverage() {
  const [meta, status] = await Promise.all([getJSON("meta/fundamentals.json").catch(() => ({})), getJSON("meta/status.json", { fresh: true }).catch(() => null)]);
  if (!MACRO) await getJSON("macro.json").then(applyMacro).catch(() => {});
  if (!FEED) FEED = await getJSON("feed.json").catch(() => null);
  page.innerHTML = "";
  page.append(el("div", { class: "ph" }, el("div", {}, el("div", { class: "eyebrow" }, "Data status"), el("h1", {}, "What's loaded and how fresh it is"), el("div", { class: "sub" }, "Every dataset on the site, where it comes from, and when it last refreshed. Jobs run on a schedule after the market close and through the day."))));
  const age = s => s ? relTime(s) : "–";
  const dot = s => { if (!s) return css("--faint"); const h = (Date.now() - new Date(s)) / 36e5; return h < 30 ? css("--up") : h < 80 ? css("--warn") : css("--down"); };
  const rows = [
    ["Company index and metrics", "index.json", INDEX?.generated, `${INDEX?.companies.length || 0} companies`],
    ["Prices", "market.json + prices/*", MARKET?.generated, `${Object.keys(MARKET?.symbols || {}).length} market symbols, ${INDEX?.companies.length || 0} stocks`],
    ["Macro: curve, CPI, jobs, fed funds", "macro.json", MACRO?.generated, `${MACRO?.curve?.length || 0} curve days`],
    ["Market brief", "macro.json", MACRO?.brief?.generated_at, MACRO?.brief ? "available" : "pending"],
    ["Filings feed", "feed.json", FEED?.generated, `${FEED?.filings?.length || 0} recent filings`],
  ];
  const t = el("table", { class: "t" }, el("thead", {}, el("tr", {}, el("th", {}, "Dataset"), el("th", {}, "Updated"), el("th", {}, "Detail"))));
  const tb = el("tbody");
  rows.forEach(([n, f, at, d]) => tb.append(el("tr", {}, el("td", {}, el("span", { class: "status-dot", style: `background:${dot(at)}` }), n, el("div", { class: "faint mono", style: "font-size:11px" }, f)), el("td", { class: "num" }, at ? `${age(at)} · ${new Date(at).toLocaleString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}` : "–"), el("td", { class: "muted" }, d))));
  t.append(tb);
  page.append(panel("Datasets", null, el("div", { class: "scroll" }, t)));
  page.append(panel("Sources", null, el("div", { class: "bd cover" },
    srcCard("SEC XBRL company facts", "data.sec.gov/api/xbrl/companyfacts", "live", "Every tagged value in every 10-K and 10-Q, with accession numbers. Feeds the financial tables and the source panel."),
    srcCard("SEC submissions", "data.sec.gov/submissions", "live", "Company profile, addresses, SIC, fiscal year end, and the filing index used by the feed."),
    srcCard("Filing documents", "sec.gov/Archives", "live", "Latest 10-K and 10-Q per company: original statement tables with inline XBRL anchors. Older filings rebuild from XBRL. Source files are archived."),
    srcCard("Earnings releases", "8-K Exhibit 99.1", "live", "Latest release per company, full text with tables."),
    srcCard("Prices", "Exchange data, daily and 5-minute", "live", "Two years of daily bars and five days of 5-minute bars, refreshed after the close."),
    srcCard("Treasury yield curve", "home.treasury.gov", "live", "Daily par yield curve, 1 month to 30 years."),
    srcCard("CPI, unemployment, payrolls, fed funds", "BLS and Federal Reserve via FRED", "live", "Monthly CPI-U, core CPI, unemployment, payrolls, daily effective fed funds."),
    srcCard("News and summaries", "News search", "live", "Recent coverage per company on a rolling schedule, with written summaries that link to sources."),
    srcCard("Insider transactions", "Form 4 XML", "partial", "Filings are listed. Transaction-level parsing (code, shares, price) is next."),
    srcCard("13F holdings", "SEC 13F-HR", "planned", "Institutional positions by quarter."),
    srcCard("Segments and KPIs", "XBRL dimensions", "planned", "Segment revenue and operating KPIs, each with a source anchor."),
    srcCard("Earnings call transcripts", "IR event pages", "planned", "Speaker-tagged, searchable, citable."))));
  const ct = el("table", { class: "t" }, el("thead", {}, el("tr", {}, el("th", {}, "Ticker"), el("th", {}, "Company"), el("th", {}, "Sector"), el("th", { class: "r" }, "Latest quarter"), el("th", { class: "r" }, "Latest filing"), el("th", {}, "Original tables"))));
  const cb = el("tbody");
  INDEX.companies.forEach(c => cb.append(el("tr", { class: "click", onclick: () => location.hash = "company-" + c.ticker.toLowerCase() }, el("td", { class: "mono" }, c.ticker), el("td", {}, c.name), el("td", { class: "muted" }, c.sector), el("td", { class: "r num" }, c.latest_quarter || "–"), el("td", { class: "r num" }, c.latest_filing ? fdate(c.latest_filing) : "–"), el("td", {}, c.has_source_tables ? el("span", { class: "chip up" }, "Yes") : el("span", { class: "chip" }, "Rebuilt")))));
  ct.append(cb);
  page.append(panel(`Coverage · ${INDEX.companies.length} companies`, null, el("div", { class: "scroll", style: "max-height:520px;overflow:auto" }, ct)));
}

async function boot() {
  try {
    const [ix, mk] = await Promise.all([getJSON("index.json"), getJSON("market.json")]);
    applyIndex(ix); applyMarket(mk);
    getJSON("macro.json").then(applyMacro).catch(() => {});
    getJSON("feed.json").then(f => { FEED = f; }).catch(() => {});
  } catch (e) {
    page.innerHTML = ""; page.append(el("div", { class: "empty" }, el("h2", {}, "Data is temporarily unavailable"), el("p", { class: "muted" }, String(e.message || e)), el("button", { class: "btn", onclick: () => location.reload() }, "Reload")));
    return;
  }
  buildShell();
  route();
}
boot();
