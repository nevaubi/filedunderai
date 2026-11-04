// Technicals page. Registers window.FU_PAGES.technicals(arg); app.js route() uses it in place of the built-in page when set.
// #technicals opens the last symbol, #technicals-TICKER a given one. Indicator math lives in technicals-lib.js (window.TA),
// heavier per-symbol analysis runs in technicals-worker.js. Data: tech/bars/{SYM}.json (daily history since 2008),
// tech/events/{T}.json (EDGAR 8-K/10-K/10-Q and parsed Form 4 trades), tech/signals.json, tech/breadth.json, tech/states.json.
window.FU_PAGES = window.FU_PAGES || {};
(function () {
  "use strict";
  const TX_CSS = `
.tx-top{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.tx-sym{height:32px;width:150px;border:1px solid var(--line);border-radius:7px;padding:0 10px;background:var(--surface);text-transform:uppercase}
.tx-bar{display:flex;gap:8px;align-items:center;flex-wrap:wrap;padding:10px 16px;border-bottom:1px solid var(--line)}
.tx-bar .sp{flex:1}
.tx-bar .seg{max-width:100%;overflow-x:auto;scrollbar-width:none}
.tx-sc td.k{min-width:200px;max-width:240px}
.tx-sc th,.tx-sc td{padding-left:7px;padding-right:7px}
.tx-sc td.k .sub{white-space:normal}
.tx-read{font-family:var(--f-mono);font-size:11.5px;color:var(--muted);padding:6px 16px 0;min-height:22px;display:flex;gap:12px;flex-wrap:wrap}
.tx-read b{color:var(--ink);font-weight:500}
.tx-chart{position:relative;padding:4px 0 8px;user-select:none;-webkit-user-select:none}
.tx-chart svg{display:block;touch-action:pan-y}
.tx-chart svg text{fill:var(--muted);font-size:10.5px;font-family:var(--f-ui)}
.tx-chart .gl line{stroke:var(--line)}
.tx-chart .mk{cursor:pointer}
.tx-ind{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:4px 18px;padding:12px 16px;border-bottom:1px solid var(--line);font-size:12.5px}
.tx-ind label{display:flex;gap:6px;align-items:center;min-height:28px;color:var(--ink2)}
.tx-ind input[type=number]{width:52px;height:24px;border:1px solid var(--line);border-radius:5px;padding:0 4px;background:var(--surface);font-size:12px}
.tx-ind .g{grid-column:1/-1;margin-top:6px}
.tx-sw{display:inline-block;width:10px;height:3px;border-radius:2px;margin-right:2px}
.tx-n{cursor:pointer;text-decoration:underline dotted var(--faint);text-underline-offset:3px;font-variant-numeric:tabular-nums}
.tx-n:hover{color:var(--accent);text-decoration-color:var(--accent)}
.tx-sum p{margin:0 0 8px;font-size:13.5px;line-height:1.55}
.tx-list{max-height:420px;overflow:auto}
.tx-list table.t td{padding:6px 10px;font-size:12.5px}
.tx-tag{display:inline-flex;align-items:center;justify-content:center;min-width:18px;height:18px;padding:0 4px;border-radius:4px;font-size:10.5px;font-weight:700;font-family:var(--f-mono)}
.tx-tag.u{background:var(--up-soft);color:var(--up)} .tx-tag.d{background:var(--down-soft);color:var(--down)} .tx-tag.n{background:var(--sunk);color:var(--muted)}
.tx-sc .sub{font-size:11px;color:var(--faint)}
.tx-calc table.t td{font-size:12px;padding:4px 8px;font-family:var(--f-mono)}
.tx-calc tr.hit td,.tx-calc tr.hit:hover td{background:var(--mark);color:var(--mark-ink)}
.tx-calc tr.win td{background:var(--accent-soft)}
.tx-chips{display:flex;gap:6px;flex-wrap:wrap;align-items:center}
.tx-tbl th.s{cursor:pointer}
.tx-tbl th.s:hover{color:var(--ink)}
@media (max-width:860px){.tx-list td:last-child{display:none}.tx-list th:last-child{display:none}.tx-bar{padding:8px 12px}.tx-read{padding:6px 12px 0}.tx-ind{padding:10px 12px}}
`;
  if (!document.getElementById("tx-css")) document.head.append(el("style", { id: "tx-css" }, TX_CSS));

  /* ---------- state ---------- */
  const DEF = {
    sym: "NVDA", tab: "chart", range: "1Y", type: "candle", log: false, cmp: "", ma: [["sma", 20, false], ["sma", 50, true], ["sma", 200, true], ["ema", 21, false]],
    bb: [false, 20, 2], kc: [false, 20, 2, 10], ich: [false, 9, 26, 52], st: [false, 10, 3], vwap: [true],
    panes: { vol: [true], rsi: [true, 14], stoch: [false, 14, 3, 3], macd: [true, 12, 26, 9], adx: [false, 14], atr: [false, 14], obv: [false], mfi: [false, 14], cmf: [false, 20], rs: [false] },
    pat: { levels: true, trend: true, gaps: true, events: true, hilo: true }, mk: { earn: true, k8: true, ins: true, per: false }, anchors: {}, hz: 20, riskWin: 252, riskMet: "corr", mktSort: ["rs63", true], showInd: false,
  };
  const TX = Object.assign(JSON.parse(JSON.stringify(DEF)), store.get("tx", {}));
  for (const k of ["panes", "pat", "mk"]) TX[k] = Object.assign({}, DEF[k], TX[k]);
  const save = () => store.set("tx", TX);
  const MKT = ["SPY", "QQQ", "IWM", "DIA", "SMH", "^VIX", "^TNX", "^IRX", "^TYX", "UUP", "CL=F", "GC=F", "HG=F", "BTC-USD", "XLK", "XLF", "XLE", "XLV", "XLY", "XLP", "XLI", "XLU", "XLB", "XLRE", "XLC"];
  const SECTOR_ETF = { "Information Technology": "XLK", Financials: "XLF", Energy: "XLE", "Health Care": "XLV", "Consumer Discretionary": "XLY", "Consumer Staples": "XLP", Industrials: "XLI", Utilities: "XLU", Materials: "XLB", "Real Estate": "XLRE", "Communication Services": "XLC" };
  const key = s => s.replace("^", "IDX_").replace("=", "_");
  const nameOf = s => SYMNAME[s] || SHORT[s] || s;
  const C = n => css(n);
  const MONTHS3 = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const f2 = v => v == null || !isFinite(v) ? "–" : fnum(v, 2);
  const pp = (v, d = 1) => v == null || !isFinite(v) ? "–" : fsgn(v, d);

  /* ---------- data ---------- */
  const HIST = {}, EVENTS = {};
  let SIGNALS = null, BREADTH = null, STATES = null;
  function fromBars(sym, p, tech) {
    const n = p.d.length, d = tech ? p.d : p.d.map(b => new Date(b[0] * 1000).toISOString().slice(0, 10));
    const col = j => tech ? p["ohlcv"[j]] : p.d.map(b => b[j + 1]);
    const H = { sym, d, o: col(0), h: col(1), l: col(2), c: col(3), v: col(4), div: p.div || p.dv || [], meta: p.meta || {}, tech, n };
    const dv = new Map(); H.div.forEach(([dd, a]) => dv.set(dd, (dv.get(dd) || 0) + a));
    H.tr = new Array(n); let acc = 1;
    for (let i = 0; i < n; i++) { if (i) acc *= (H.c[i] + (dv.get(d[i]) || 0)) / H.c[i - 1]; H.tr[i] = acc; }
    H.idx = new Map(d.map((x, i) => [x, i]));
    return H;
  }
  async function loadHist(sym) {
    if (HIST[sym]) return HIST[sym];
    let H = null;
    try { const p = await getJSON(`tech/bars/${key(sym)}.json`); if (p?.d?.length) { H = fromBars(sym, p, true); H.file = `tech/bars/${key(sym)}.json`; } } catch (e) { }
    if (!H) {
      try { const p = await getJSON(`prices/${key(sym)}.json`); if (p?.d?.length) { H = fromBars(sym, p, false); H.file = `prices/${key(sym)}.json`; H.meta.source = "Yahoo Finance chart API, two years of daily bars (long history not yet loaded for this symbol)"; } } catch (e) { }
    }
    if (H) HIST[sym] = H;
    return H;
  }
  async function loadIntraday(sym) {
    try {
      const p = await getJSON(`prices/${key(sym)}.json`);
      if (!p?.i?.length) return null;
      const off = (p.meta?.gmtoffset ?? -18000) * 1000;
      const d = p.i.map(b => new Date(b[0] * 1000 + off).toISOString().slice(0, 16).replace("T", " "));
      return { sym, intraday: true, d, o: p.i.map(b => b[1]), h: p.i.map(b => b[2]), l: p.i.map(b => b[3]), c: p.i.map(b => b[4]), v: p.i.map(b => b[5]), session: d.map(x => x.slice(0, 10)), meta: p.meta || {}, file: `prices/${key(sym)}.json`, n: p.i.length };
    } catch { return null; }
  }
  async function loadEvents(tk) {
    if (EVENTS[tk] !== undefined) return EVENTS[tk];
    let ev = null;
    try { ev = await getJSON(`tech/events/${tk}.json`); } catch { }
    if (!ev && IDX[tk]) {
      try { const b = await getJSON(`companies/${tk}.json`); ev = { filings: (b.filings || []).filter(f => /^(8-K|10-K|10-Q)/.test(f.f)).map(f => [f.f, f.d, f.a, f.p, f.i || "", f.r || "", b.cik]).reverse(), insiders: [], source: "Company filings index (recent filings only)", partial: true }; } catch { }
    }
    if (ev && !ev.insiders?.length && IDX[tk]) {
      try { const ins = await getJSON(`insiders/${tk}.json`); ev.insiders = (ins.rows || []).filter(r => ["P", "S"].includes(r.code) && !r.derivative && r.tx_date).map(r => [r.tx_date, r.code, r.shares, r.price, r.owner, r.role, r.accession, r.plan_10b5, r.filed]).sort((a, b) => a[0] < b[0] ? -1 : 1); ev.insiderNote = "last 12 months"; } catch { }
    }
    EVENTS[tk] = ev;
    return ev;
  }
  const loadSignals = async () => SIGNALS || (SIGNALS = await getJSON("tech/signals.json").catch(() => null));

  /* ---------- worker ---------- */
  let WK = null, wkSeq = 0; const wkWait = {};
  function work(kind, data) {
    if (!WK && typeof Worker !== "undefined") {
      try { WK = new Worker("/pages/technicals-worker.js"); WK.onmessage = e => { const w = wkWait[e.data.id]; if (!w) return; delete wkWait[e.data.id]; e.data.ok ? w.res(e.data.result) : w.rej(new Error(e.data.error)); }; WK.onerror = () => { WK = false; }; } catch { WK = false; }
    }
    if (!WK) return Promise.resolve(kind === "analyze" ? TA.analyze(data) : TA.risk(data));
    return new Promise((res, rej) => { const id = ++wkSeq; wkWait[id] = { res, rej }; WK.postMessage({ id, kind, data }); setTimeout(() => { if (wkWait[id]) { delete wkWait[id]; res(kind === "analyze" ? TA.analyze(data) : TA.risk(data)); } }, 20000); });
  }
  const ANA = {};
  async function analysis(H) {
    if (ANA[H.sym]) return ANA[H.sym];
    const t0 = performance.now();
    const a = await work("analyze", { o: H.o, h: H.h, l: H.l, c: H.c, v: H.v, tr: H.tr, d: H.d });
    a.ms = Math.round(performance.now() - t0);
    return (ANA[H.sym] = a);
  }

  /* ---------- source drawer: calculations from bars ---------- */
  function sourceLine(H) {
    const m = H.meta || {};
    return el("div", { class: "faint", style: "font-size:11.5px;line-height:1.5" }, "Bars: ", m.source || "Yahoo Finance chart API", m.retrieved ? `, retrieved ${fdate(m.retrieved.slice(0, 10))}` : "", m.updated ? `, last appended ${fdate(m.updated.slice(0, 10))}` : "", ". ",
      el("a", { href: PUB + H.file, target: "_blank", rel: "noopener" }, "Stored bars ↗"), m.source_url ? [" · ", el("a", { href: m.source_url, target: "_blank", rel: "noopener" }, "Original API response ↗")] : null);
  }
  // cfg: {title, value, sub, formula, cols: [[label, i => text]], rows: [indices], hit: [indices], win: [indices], H, notes, link}
  function openCalc(cfg) {
    hideTip();
    const dh = $("#dh"), db = $("#db"); dh.innerHTML = ""; db.innerHTML = "";
    DR.cell = { txCalc: true };
    dh.append(el("div", { class: "row" }, el("span", { class: "eyebrow" }, cfg.eyebrow || "Calculated from daily bars"), el("div", { class: "x", style: "display:flex;gap:6px" },
      cfg.cite ? el("button", { class: "btn", onclick: () => copyText(cfg.cite, "Citation copied") }, svgUse("i-copy"), "Cite") : null,
      el("button", { class: "iconbtn", onclick: closeSource, "aria-label": "Close source (Esc)" }, svgUse("i-x")))),
      el("div", { class: "row" }, el("h2", {}, cfg.title)),
      el("div", { class: "row" }, cfg.value != null ? el("span", { class: "chip acc mono", style: "font-size:13.5px;height:26px" }, cfg.value) : null, cfg.sub ? el("span", { class: "muted", style: "font-size:12.5px" }, cfg.sub) : null));
    const body = el("div", { class: "tx-calc", style: "display:flex;flex-direction:column;gap:14px;padding-top:14px" });
    if (cfg.formula) body.append(el("div", { class: "calc-box" }, el("div", { class: "eyebrow" }, "Formula"), el("div", { class: "eq" }, cfg.formula)));
    (cfg.notes || []).forEach(n => body.append(el("div", { class: "note" }, n)));
    let hitRow = null;
    if (cfg.cols && cfg.rows?.length) {
      const hit = new Set(cfg.hit || []), win = new Set(cfg.win || []);
      const t = el("table", { class: "t" }, el("thead", {}, el("tr", {}, cfg.cols.map(([lab], k) => el("th", { class: k ? "r" : "" }, lab)))));
      const tb = el("tbody");
      cfg.rows.forEach(i => { const tr = el("tr", { class: hit.has(i) ? "hit" : win.has(i) ? "win" : "" }, cfg.cols.map(([, fn], k) => el("td", { class: k ? "r" : "" }, fn(i)))); if (hit.has(i) && !hitRow) hitRow = tr; tb.append(tr); });
      t.append(tb);
      body.append(el("div", { class: "scroll", style: "max-height:60vh;overflow:auto;border:1px solid var(--line);border-radius:8px" }, t));
      if (cfg.rowsNote) body.append(el("div", { class: "faint", style: "font-size:11.5px" }, cfg.rowsNote));
    }
    if (cfg.H) body.append(sourceLine(cfg.H));
    if (cfg.link) body.append(el("div", { class: "faint", style: "font-size:11.5px" }, cfg.link.text, " ", el("a", { href: cfg.link.url, target: "_blank", rel: "noopener" }, cfg.link.label + " ↗")));
    db.append(body);
    drawer.classList.add("open"); drawer.setAttribute("aria-hidden", "false");
    if (innerWidth < 860) scrim.hidden = false;
    if (hitRow) requestAnimationFrame(() => { const sc = hitRow.closest(".scroll"); if (sc) sc.scrollTop = hitRow.offsetTop - sc.clientHeight / 2; });
  }
  const dcol = H => ["Date", i => H.d[i]];
  const ccol = (H, lab = "Close") => [lab, i => f2(H.c[i])];
  const range = (a, b) => { const o = []; for (let i = Math.max(0, a); i <= b; i++) o.push(i); return o; };
  const fx4 = v => v == null || !isFinite(v) ? "–" : (+v).toFixed(4);

  // A clickable number. `open` builds the openCalc config lazily.
  const num = (text, open, cls = "") => el("span", { class: "tx-n " + cls, role: "button", tabindex: "0", title: "Show the calculation and the bars it uses", onclick: e => { e.stopPropagation(); openCalc(open()); }, onkeydown: e => { if (e.key === "Enter") openCalc(open()); } }, text);

  function calcFor(H, k, A) {
    const L = H.n, i = L - 1, c = H.c;
    const smaCalc = n => { const s = TA.sma(c, n); return { title: `${H.sym} · ${n}-day simple moving average`, value: f2(s[i]), sub: `as of ${fdate(H.d[i])}`, formula: `SMA(${n}) = sum of the last ${n} closes / ${n} = ${f2(s[i])}. Close ${f2(c[i])} / SMA − 1 = ${pp(c[i] / s[i] - 1, 2)}`, cols: [dcol(H), ccol(H), [`SMA ${n}`, j => f2(s[j])]], rows: range(i - n - 5, i).reverse(), win: range(i - n + 1, i), hit: [i], H, rowsNote: `Shaded rows are the ${n} closes in the average.` }; };
    if (k === "vs200" || k === "sma200") return smaCalc(200);
    if (k === "vs50" || k === "sma50") return smaCalc(50);
    if (k === "rsi") {
      const r = TA.rsi(c, 14), g = [null], lo = [null]; for (let j = 1; j < L; j++) { const d = c[j] - c[j - 1]; g.push(Math.max(d, 0)); lo.push(Math.max(-d, 0)); }
      const ag = TA.rma(g, 14), al = TA.rma(lo, 14);
      return { title: `${H.sym} · RSI(14)`, value: f2(r[i]), sub: `Wilder smoothing, as of ${fdate(H.d[i])}`, formula: `RS = average gain / average loss (Wilder: avg = (prev × 13 + today) / 14). RSI = 100 − 100 / (1 + RS) = 100 − 100 / (1 + ${fx4(ag[i])} / ${fx4(al[i])}) = ${f2(r[i])}`, cols: [dcol(H), ccol(H), ["Change", j => j ? f2(c[j] - c[j - 1]) : "–"], ["Avg gain", j => fx4(ag[j])], ["Avg loss", j => fx4(al[j])], ["RSI", j => f2(r[j])]], rows: range(i - 40, i).reverse(), hit: [i], H, rowsNote: "Averages are carried from the first 14 changes in the history, so every earlier bar contributes with decaying weight." };
    }
    if (k === "adx" || k === "pdi" || k === "mdi") {
      const a = TA.adx(H.h, H.l, c, 14), tr = TA.trueRange(H.h, H.l, c);
      return { title: `${H.sym} · ADX / DMI (14)`, value: `ADX ${f2(a.adx[i])} · +DI ${f2(a.pdi[i])} · −DI ${f2(a.mdi[i])}`, formula: "+DI = 100 × smoothed +DM / smoothed TR; −DI likewise; DX = 100 × |+DI − −DI| / (+DI + −DI); ADX = Wilder average of DX over 14 bars.", cols: [dcol(H), ["High", j => f2(H.h[j])], ["Low", j => f2(H.l[j])], ccol(H), ["TR", j => f2(tr[j])], ["+DI", j => f2(a.pdi[j])], ["−DI", j => f2(a.mdi[j])], ["DX", j => f2(a.dx[j])], ["ADX", j => f2(a.adx[j])]], rows: range(i - 40, i).reverse(), hit: [i], H };
    }
    if (k === "hi52" || k === "fromHi" || k === "lo52" || k === "fromLo") {
      const st = A.state, hiK = k === "hi52" || k === "fromHi", at = hiK ? st.hi52i : st.lo52i, lv = hiK ? st.hi52 : st.lo52;
      return { title: `${H.sym} · 52-week ${hiK ? "high" : "low"}`, value: f2(lv), sub: `${hiK ? "highest high" : "lowest low"} of the last ${Math.min(252, L)} bars, set ${fdate(H.d[at])}`, formula: `Close ${f2(c[i])} / ${f2(lv)} − 1 = ${pp(c[i] / lv - 1, 2)}`, cols: [dcol(H), ["High", j => f2(H.h[j])], ["Low", j => f2(H.l[j])], ccol(H)], rows: range(L - Math.min(252, L), i).reverse(), hit: [at, i], H, rowsNote: "All bars in the 52-week window; the extreme and the latest close are highlighted." };
    }
    if (k === "atr" || k === "atrPct") {
      const a = TA.atr(H.h, H.l, c, 14), tr = TA.trueRange(H.h, H.l, c);
      return { title: `${H.sym} · ATR(14)`, value: f2(a[i]), sub: `${(a[i] / c[i] * 100).toFixed(2)}% of the close`, formula: "True range = max(high − low, |high − prior close|, |low − prior close|). ATR = Wilder average of 14 true ranges.", cols: [dcol(H), ["High", j => f2(H.h[j])], ["Low", j => f2(H.l[j])], ccol(H), ["True range", j => f2(tr[j])], ["ATR", j => f2(a[j])]], rows: range(i - 30, i).reverse(), hit: [i], H };
    }
    if (k === "squeeze") {
      const bb = TA.bollinger(c, 20, 2), kc = TA.keltner(H.h, H.l, c, 20, 1.5, 20);
      return { title: `${H.sym} · Volatility squeeze`, value: `${A.state.squeeze} bar${A.state.squeeze === 1 ? "" : "s"}`, formula: "Squeeze: Bollinger(20, 2 SD) upper below and lower above Keltner(EMA 20 ± 1.5 × ATR 20).", cols: [dcol(H), ccol(H), ["BB upper", j => f2(bb.upper[j])], ["KC upper", j => f2(kc.upper[j])], ["BB lower", j => f2(bb.lower[j])], ["KC lower", j => f2(kc.lower[j])], ["Inside", j => bb.upper[j] < kc.upper[j] && bb.lower[j] > kc.lower[j] ? "yes" : "no"]], rows: range(i - 40, i).reverse(), hit: [i], win: range(i - A.state.squeeze + 1, i), H };
    }
    if (k === "macdHist") {
      const m = TA.macd(c), e12 = TA.ema(c, 12), e26 = TA.ema(c, 26);
      return { title: `${H.sym} · MACD(12, 26, 9)`, value: `MACD ${fx4(m.line[i])} · signal ${fx4(m.signal[i])}`, formula: "MACD = EMA(12) − EMA(26); signal = EMA(9) of MACD; histogram = MACD − signal.", cols: [dcol(H), ccol(H), ["EMA 12", j => f2(e12[j])], ["EMA 26", j => f2(e26[j])], ["MACD", j => fx4(m.line[j])], ["Signal", j => fx4(m.signal[j])], ["Hist", j => fx4(m.hist[j])]], rows: range(i - 40, i).reverse(), hit: [i], H };
    }
    if (k === "vol21") {
      const rv = TA.realizedVol(c, 21), lr = TA.logRets(c), cone = A.cone.find(x => x.n === 21);
      return { title: `${H.sym} · 21-day realized volatility`, value: fpct(rv[i]), sub: `${Math.round(cone.pct * 100)}th percentile of ${cone.count.toLocaleString()} rolling 21-day windows since ${fdate(H.d[0])}`, formula: "Sample standard deviation of the last 21 daily log returns × √252.", cols: [dcol(H), ccol(H), ["Log return", j => lr[j] == null ? "–" : (lr[j] * 100).toFixed(3) + "%"], ["21d vol", j => rv[j] == null ? "–" : fpct(rv[j])]], rows: range(i - 30, i).reverse(), win: range(i - 20, i), hit: [i], H };
    }
    return null;
  }
  function rsCalc(H, B, n = 63) {
    const L = H.n, i = L - 1, d0 = H.d[i - n], j0 = B.idx.get(d0), j1 = B.idx.get(H.d[i]);
    const ra = H.c[i] / H.c[i - n], rb = B.c[j1] / B.c[j0];
    return { title: `${H.sym} vs ${B.sym} · ${n}-day relative performance`, value: pp(ra / rb - 1), formula: `(${f2(H.c[i])} / ${f2(H.c[i - n])}) / (${f2(B.c[j1])} / ${f2(B.c[j0])}) − 1 = ${pp(ra / rb - 1, 2)} (price returns, dividends excluded)`, cols: [["Date", x => x.d], [H.sym, x => f2(x.a)], [B.sym, x => f2(x.b)]].map(([l, f]) => [l, k => f(k)]), rows: [{ d: d0, a: H.c[i - n], b: B.c[j0] }, { d: H.d[i], a: H.c[i], b: B.c[j1] }], hit: [], H };
  }

  /* ---------- filing / Form 4 viewer ---------- */
  const ITEM = { "1.01": "Material agreement", "1.02": "Agreement terminated", "2.01": "Acquisition or disposition", "2.02": "Results of operations", "2.03": "Debt obligation", "2.05": "Exit costs", "2.06": "Impairment", "3.01": "Listing notice", "3.02": "Unregistered equity sale", "3.03": "Rights modification", "4.01": "Auditor change", "4.02": "Non-reliance on financials", "5.01": "Change in control", "5.02": "Officer/director change", "5.03": "Bylaw amendment", "5.07": "Shareholder vote", "7.01": "Reg FD disclosure", "8.01": "Other events", "9.01": "Exhibits" };
  const itemsText = s => (s || "").split(",").filter(x => x && x !== "9.01").map(x => ITEM[x] || "Item " + x).join(" · ") || "Current report";
  const edgarUrl = (cik, accn, doc) => `https://www.sec.gov/Archives/edgar/data/${Number(cik)}/${accn.replace(/-/g, "")}/${doc || ""}`;
  function openFiling(tk, f, hl) {
    hideTip();
    const dh = $("#dh"), db = $("#db"); dh.innerHTML = ""; db.innerHTML = "";
    const me = { txFiling: f.accn }; DR.cell = me;
    const url = edgarUrl(f.cik, f.accn, f.doc);
    dh.append(el("div", { class: "row" }, el("span", { class: "eyebrow" }, `${f.form} · filed ${fdate(f.filed)}`), el("div", { class: "x", style: "display:flex;gap:6px" },
      el("a", { class: "btn", href: url, target: "_blank", rel: "noopener" }, "EDGAR", svgUse("i-ext")), el("button", { class: "iconbtn", onclick: closeSource, "aria-label": "Close source (Esc)" }, svgUse("i-x")))),
      el("div", { class: "row" }, el("h2", {}, `${SHORT[tk] || tk} · ${f.title}`)),
      el("div", { class: "row" }, el("span", { class: "muted mono", style: "font-size:12px" }, `accession ${f.accn}`), f.detail ? el("span", { class: "muted", style: "font-size:12.5px" }, f.detail) : null));
    const status = el("span", { class: "faint", style: "font-size:12px" }, "Loading the filing…");
    const wrap = el("div", { class: "viewer" }, el("div", { class: "viewer-load" }, el("div", { class: "spin" }), el("div", {}, "Opening the filing")));
    if (f.rows) db.append(f.rows);
    db.append(el("div", { class: "viewer-bar" }, el("span", { style: "flex:1" }), status), wrap);
    drawer.classList.add("open"); drawer.setAttribute("aria-hidden", "false");
    if (innerWidth < 860) scrim.hidden = false;
    loadFilingDoc(f.accn).then(html => {
      if (DR.cell !== me) return;
      const iframe = el("iframe", { title: `${f.form} filed ${fdate(f.filed)}`, sandbox: "allow-same-origin allow-popups allow-popups-to-escape-sandbox", referrerpolicy: "no-referrer" });
      iframe.srcdoc = html + VIEWER_CSS; wrap.append(iframe);
      iframe.addEventListener("load", () => {
        const doc = iframe.contentDocument; if (!doc) return;
        wrap.querySelector(".viewer-load")?.remove();
        const w = doc.documentElement.scrollWidth, avail = iframe.clientWidth; if (w > avail + 4) doc.documentElement.style.zoom = Math.max(0.5, Math.floor(avail / w * 100) / 100);
        let hits = [];
        if (hl?.quote) hits = textFindQuote(doc, hl.quote, hl.printed);
        if (!hits.length && hl?.value != null) hits = textFind(doc, hl.value);
        if (hits.length) { hits.forEach(x => x.classList.add("fu-alt")); hits[0].classList.remove("fu-alt"); hits[0].classList.add("fu-hit"); const keep = $("#db").scrollTop; hits[0].scrollIntoView({ block: "center" }); $("#db").scrollTop = keep; status.textContent = hl.label || "Highlighted"; }
        else status.textContent = hl ? "Figure not found in the document text" : "";
        doc.addEventListener("keydown", e => { if (e.key === "Escape") closeSource(); });
      }, { once: true });
    }).catch(err => {
      if (DR.cell !== me) return;
      wrap.innerHTML = ""; status.textContent = "";
      wrap.append(el("div", { class: "note", style: "margin:12px" }, `The filing could not be opened in the viewer (${err.message}). `, el("a", { href: url, target: "_blank", rel: "noopener" }, "Open it on EDGAR ↗")));
    });
  }
  const money = v => v == null ? "–" : Math.abs(v) >= 1e6 ? fmtMoney(v) : "$" + Math.round(v).toLocaleString("en-US");
  // Form 4 primary documents are XML, so the trades are shown as parsed from the XML, with the filing index on EDGAR.
  function openInsiderDay(tk, day, rows, cik) {
    hideTip();
    const dh = $("#dh"), db = $("#db"); dh.innerHTML = ""; db.innerHTML = "";
    DR.cell = { txIns: day };
    const show = sel => {
      const r = rows[sel], idxUrl = `https://www.sec.gov/Archives/edgar/data/${Number(cik)}/${r[6].replace(/-/g, "")}/${r[6]}-index.htm`;
      dh.innerHTML = "";
      dh.append(el("div", { class: "row" }, el("span", { class: "eyebrow" }, `Form 4 · filed ${fdate(r[8] || day)}`), el("div", { class: "x", style: "display:flex;gap:6px" },
        el("a", { class: "btn", href: idxUrl, target: "_blank", rel: "noopener" }, "EDGAR", svgUse("i-ext")), el("button", { class: "iconbtn", onclick: closeSource, "aria-label": "Close source (Esc)" }, svgUse("i-x")))),
        el("div", { class: "row" }, el("h2", {}, `${SHORT[tk] || tk} · ${r[4]}`)),
        el("div", { class: "row" }, el("span", { class: "chip acc mono", style: "font-size:13.5px;height:26px" }, `${r[1] === "P" ? "Bought" : "Sold"} ${r[2] != null ? Math.round(r[2]).toLocaleString("en-US") : "?"} @ $${f2(r[3])}`), el("span", { class: "muted", style: "font-size:12.5px" }, `${fdate(r[0])} · ${r[5] || ""} · accession ${r[6]}`)));
      const t = el("table", { class: "t" }, el("thead", {}, el("tr", {}, el("th", {}, "Insider"), el("th", {}, "Type"), el("th", { class: "r" }, "Shares"), el("th", { class: "r" }, "Price"), el("th", { class: "r" }, "Value"), el("th", {}, "Accession"))));
      const tb = el("tbody");
      rows.forEach((x, k) => tb.append(el("tr", { class: "click" + (k === sel ? " hit" : ""), onclick: () => show(k) }, el("td", {}, el("div", {}, x[4]), el("div", { class: "faint", style: "font-size:11px" }, x[5] || "")), el("td", {}, el("span", { class: "chip " + (x[1] === "P" ? "up" : "down") }, x[1] === "P" ? "Buy" : "Sale"), x[7] ? el("span", { class: "chip", style: "margin-left:4px", title: "Executed under a Rule 10b5-1 trading plan" }, "10b5-1") : null),
        el("td", { class: "r num" }, x[2] != null ? Math.round(x[2]).toLocaleString("en-US") : "–"), el("td", { class: "r num" }, x[3] ? "$" + f2(x[3]) : "–"), el("td", { class: "r num" }, x[2] && x[3] ? money(x[2] * x[3]) : "–"), el("td", { class: "mono faint", style: "font-size:11px" }, el("a", { href: `https://www.sec.gov/Archives/edgar/data/${Number(cik)}/${x[6].replace(/-/g, "")}/${x[6]}-index.htm`, target: "_blank", rel: "noopener", onclick: e => e.stopPropagation() }, x[6] + " ↗")))));
      t.append(tb);
      db.innerHTML = "";
      db.append(el("div", { class: "tx-calc", style: "display:flex;flex-direction:column;gap:12px;padding-top:14px" }, el("div", { class: "scroll", style: "border:1px solid var(--line);border-radius:8px" }, t),
        el("div", { class: "faint", style: "font-size:11.5px;line-height:1.5" }, `Open-market ${r[1] === "P" ? "purchases (code P)" : "sales (code S)"} of non-derivative shares reported on ${fdate(r[0])}, parsed from each Form 4's XML (table I: transaction date, code, shares, price). The highlighted row is the selected trade; the EDGAR link opens the filing index with the rendered form.`)));
      drawer.classList.add("open"); drawer.setAttribute("aria-hidden", "false");
      if (innerWidth < 860) scrim.hidden = false;
    };
    show(0);
  }

  /* ---------- page ---------- */
  window.FU_PAGES.technicals = async function (arg) {
    const all = [...MKT, ...TICKERS];
    if (arg) { const a = String(arg).toUpperCase(); const m = all.find(s => s.replace("^", "") === a.replace("^", "")); if (m) TX.sym = m; }
    if (!all.includes(TX.sym)) TX.sym = "NVDA";
    save();
    const sym = TX.sym;
    const [H] = await Promise.all([loadHist(sym), loadHist("SPY")]);
    if (!H) throw new Error(`No price history for ${sym}`);
    const sector = IDX[sym]?.sector, secEtf = SECTOR_ETF[sector];
    const dl = el("datalist", { id: "tx-syms" }, all.map(s => el("option", { value: s.replace("^", "") }, nameOf(s))));
    const inp = el("input", { class: "tx-sym mono", list: "tx-syms", value: sym.replace("^", ""), "aria-label": "Symbol", placeholder: "Symbol" });
    const go = () => { const v = inp.value.trim().toUpperCase(); const m = all.find(s => s.replace("^", "") === v.replace("^", "")); if (m) location.hash = "#technicals-" + m.replace("^", ""); else toast(`${v} is not covered`); };
    inp.addEventListener("change", go); inp.addEventListener("keydown", e => { if (e.key === "Enter") go(); });
    const last = H.n - 1, chg = H.c[last] / H.c[last - 1] - 1;
    page.append(el("div", { class: "ph" }, el("div", {}, el("div", { class: "eyebrow" }, "Technicals"),
      el("h1", {}, nameOf(sym), " ", el("span", { class: "muted mono", style: "font-size:18px" }, sym.replace("^", ""))),
      el("div", { class: "sub" }, el("span", { class: "num" }, f2(H.c[last])), " ", el("span", { class: "num " + cls(chg) }, pp(chg, 2)), ` · close ${fdate(H.d[last])} · ${H.n.toLocaleString()} daily bars since ${fdate(H.d[0])}`, IDX[sym] ? [" · ", el("a", { href: "#company-" + sym.toLowerCase() }, "Company page")] : null)),
      el("div", { class: "right tx-top" }, inp, dl)));
    const tabs = el("div", { class: "tabs", role: "tablist", style: "margin-bottom:16px" });
    const body = el("div", { style: "display:flex;flex-direction:column;gap:16px" });
    const T = [["chart", "Chart and patterns"], ["signals", "Signal scorecard"], ["risk", "Volatility and risk"], ["market", "Market breadth"]];
    const show = id => { TX.tab = id; save(); tabs.querySelectorAll("button").forEach(b => b.classList.toggle("on", b.dataset.v === id)); body.innerHTML = ""; ({ chart: chartTab, signals: signalsTab, risk: riskTab, market: marketTab })[id](body, H, { sector, secEtf }); };
    T.forEach(([id, n]) => tabs.append(el("button", { "data-v": id, role: "tab", onclick: () => show(id) }, n)));
    page.append(tabs, body);
    show(T.some(t => t[0] === TX.tab) ? TX.tab : "chart");
  };

  /* ---------- chart tab ---------- */
  async function chartTab(body, H, ctx) {
    const sym = H.sym;
    const A0 = analysis(H), E0 = IDX[sym] ? loadEvents(sym) : Promise.resolve(null);
    const intra = await loadIntraday(sym);
    const RANGES = [...(intra ? [["5D", "5D"]] : []), ["1M", "1M"], ["3M", "3M"], ["6M", "6M"], ["YTD", "YTD"], ["1Y", "1Y"], ["2Y", "2Y"], ["5Y", "5Y"], ["10Y", "10Y"], ["MAX", "Max"]];
    if (TX.range === "5D" && !intra) TX.range = "1Y";
    const panelEl = el("section", { class: "panel" });
    const readout = el("div", { class: "tx-read" });
    const host = el("div", { class: "tx-chart" });
    const indBox = el("div", { class: "tx-ind", hidden: !TX.showInd });
    const cmpIn = el("input", { class: "tx-sym mono", style: "width:110px", list: "tx-syms", value: TX.cmp.replace("^", ""), placeholder: "Compare", "aria-label": "Comparison symbol" });
    const anchorBtn = el("button", { class: "btn", title: "Click a bar on the chart to anchor a VWAP there" }, "Anchor VWAP");
    let anchorMode = false;
    anchorBtn.onclick = () => { anchorMode = !anchorMode; anchorBtn.classList.toggle("acc", anchorMode); anchorBtn.textContent = anchorMode ? "Click a bar…" : "Anchor VWAP"; };
    const logBtn = el("button", { class: "btn" + (TX.log ? " acc" : ""), onclick: () => { TX.log = !TX.log; logBtn.classList.toggle("acc", TX.log); save(); draw(); } }, "Log");
    const indBtn = el("button", { class: "btn" + (TX.showInd ? " acc" : ""), onclick: () => { TX.showInd = !TX.showInd; indBox.hidden = !TX.showInd; indBtn.classList.toggle("acc", TX.showInd); save(); } }, "Indicators");
    const zoom = f => { const [a, b] = V.view, span = b - a + 1, ns = Math.max(10, Math.min(S().n, Math.round(span * f))), mid = (a + b) / 2; setView(Math.round(mid - ns / 2), Math.round(mid - ns / 2) + ns - 1); };
    panelEl.append(el("div", { class: "tx-bar" },
      seg(RANGES, TX.range, v => { TX.range = v; save(); resetView(); draw(); }),
      seg([["candle", "Candles"], ["ohlc", "OHLC"], ["line", "Line"]], TX.type, v => { TX.type = v; save(); draw(); }),
      logBtn, indBtn, anchorBtn, cmpIn,
      el("span", { class: "sp" }),
      el("button", { class: "iconbtn", title: "Zoom out", "aria-label": "Zoom out", onclick: () => zoom(1.5) }, el("span", { class: "mono", style: "font-size:15px" }, "−")),
      el("button", { class: "iconbtn", title: "Zoom in", "aria-label": "Zoom in", onclick: () => zoom(1 / 1.5) }, el("span", { class: "mono", style: "font-size:15px" }, "+"))),
      indBox, readout, host,
      el("div", { class: "src" }, "Daily bars: Yahoo Finance chart API (split-adjusted), stored since 2008. 5D uses 5-minute bars. Drag to pan, scroll or pinch to zoom, double-click to reset. Markers: E earnings 8-K (Item 2.02), 8 other 8-K, B/S open-market insider buys and sales from Form 4; click one to open the filing."));
    cmpIn.addEventListener("change", async () => { const v = cmpIn.value.trim().toUpperCase(); const m = [...MKT, ...TICKERS].find(s => s.replace("^", "") === v.replace("^", "")); TX.cmp = m || ""; if (v && !m) toast(`${v} is not covered`); save(); if (TX.cmp) await loadHist(TX.cmp); draw(); });
    body.append(panelEl);
    const sumHost = el("div", { class: "bd tx-sum" }, el("div", { class: "muted" }, "Computing…"));
    const evHost = el("div");
    const grid = el("div", { class: "grid g64" }, el("section", { class: "panel" }, el("div", { class: "hd" }, el("h3", {}, "Detected patterns and events")), evHost), el("section", { class: "panel" }, el("div", { class: "hd" }, el("h3", {}, "Technical summary"), el("div", { class: "r faint", style: "font-size:11.5px" }, "rule-based, from computed values")), sumHost, el("div", { class: "src" }, "Each sentence is generated by fixed rules from the values on this page; every number is checked against the computed value before it is shown. Click a number for its calculation.")));
    body.append(grid);
    if (TX.cmp) await loadHist(TX.cmp);
    const benchSyms = ["SPY", ctx.secEtf].filter(Boolean);
    await Promise.all(benchSyms.map(loadHist));

    /* indicator controls */
    function buildInd() {
      indBox.innerHTML = "";
      const numIn = (arr, k, min = 1, max = 400, step = 1) => { const x = el("input", { type: "number", value: arr[k], min, max, step }); x.onchange = () => { const v = +x.value; if (isFinite(v) && v >= min && v <= max) { arr[k] = v; save(); draw(); } }; return x; };
      const cb = (arr, label, ...extra) => { const x = el("input", { type: "checkbox" }); x.checked = !!arr[0]; x.onchange = () => { arr[0] = x.checked; save(); draw(); }; return el("label", {}, x, label, ...extra); };
      indBox.append(el("div", { class: "eyebrow g" }, "Moving averages"));
      TX.ma.forEach((m, k) => { const x = el("input", { type: "checkbox" }); x.checked = m[2]; x.onchange = () => { m[2] = x.checked; save(); draw(); }; const t = el("select", { class: "btn", style: "height:24px;padding:0 4px;font-size:12px" }, ["sma", "ema"].map(v => { const o = el("option", { value: v }, v.toUpperCase()); if (v === m[0]) o.selected = true; return o; })); t.onchange = () => { m[0] = t.value; save(); draw(); }; indBox.append(el("label", {}, x, el("span", { class: "tx-sw", style: `background:${maColor(k)}` }), t, numIn(m, 1, 2, 400), el("button", { class: "iconbtn", style: "width:24px;height:24px", title: "Remove", onclick: () => { TX.ma.splice(k, 1); save(); buildInd(); draw(); } }, "×"))); });
      indBox.append(el("label", {}, el("button", { class: "btn", style: "height:26px", onclick: () => { TX.ma.push(["ema", 50, true]); save(); buildInd(); draw(); } }, "+ Add average")));
      indBox.append(el("div", { class: "eyebrow g" }, "Bands and overlays"));
      indBox.append(cb(TX.bb, "Bollinger", numIn(TX.bb, 1, 2, 200), numIn(TX.bb, 2, 0.5, 5, 0.1)), cb(TX.kc, "Keltner EMA", numIn(TX.kc, 1, 2, 200), "×ATR", numIn(TX.kc, 2, 0.5, 5, 0.1), numIn(TX.kc, 3, 2, 100)),
        cb(TX.ich, "Ichimoku", numIn(TX.ich, 1, 2, 60), numIn(TX.ich, 2, 2, 120), numIn(TX.ich, 3, 2, 240)), cb(TX.st, "Supertrend", numIn(TX.st, 1, 2, 100), numIn(TX.st, 2, 0.5, 10, 0.1)), cb(TX.vwap, "VWAP (5D, per session)"));
      indBox.append(el("div", { class: "eyebrow g" }, "Lower panes"));
      const P = TX.panes;
      indBox.append(cb(P.vol, "Volume"), cb(P.rsi, "RSI", numIn(P.rsi, 1, 2, 100)), cb(P.stoch, "Stochastic", numIn(P.stoch, 1, 2, 100), numIn(P.stoch, 2, 1, 20), numIn(P.stoch, 3, 1, 20)), cb(P.macd, "MACD", numIn(P.macd, 1, 2, 100), numIn(P.macd, 2, 3, 200), numIn(P.macd, 3, 2, 50)),
        cb(P.adx, "ADX / DMI", numIn(P.adx, 1, 2, 100)), cb(P.atr, "ATR", numIn(P.atr, 1, 2, 100)), cb(P.obv, "On-balance volume"), cb(P.mfi, "Money flow index", numIn(P.mfi, 1, 2, 100)), cb(P.cmf, "Chaikin money flow", numIn(P.cmf, 1, 2, 100)), cb(P.rs, `Relative strength vs SPY${ctx.secEtf ? " and " + ctx.secEtf : ""}`));
      indBox.append(el("div", { class: "eyebrow g" }, "Patterns and markers"));
      const tg = (o, k, label) => { const x = el("input", { type: "checkbox" }); x.checked = o[k]; x.onchange = () => { o[k] = x.checked; save(); draw(); }; return el("label", {}, x, label); };
      indBox.append(tg(TX.pat, "levels", "Support / resistance"), tg(TX.pat, "trend", "Trend lines"), tg(TX.pat, "gaps", "Open gaps"), tg(TX.pat, "events", "Signal markers"), tg(TX.pat, "hilo", "52-week high / low"),
        tg(TX.mk, "earn", "Earnings 8-K"), tg(TX.mk, "k8", "Other 8-K"), tg(TX.mk, "per", "10-K / 10-Q"), tg(TX.mk, "ins", "Insider trades"));
      const an = TX.anchors[sym] || [];
      if (an.length) indBox.append(el("div", { class: "g tx-chips" }, el("span", { class: "eyebrow" }, "Anchored VWAP"), an.map(d => el("button", { class: "chip link", onclick: () => { TX.anchors[sym] = an.filter(x => x !== d); save(); buildInd(); draw(); }, title: "Remove" }, fdate(d) + " ×"))));
    }
    const maColor = k => [C("--c3"), C("--c1"), C("--c5"), C("--c2"), C("--c4"), C("--c6")][k % 6];

    /* view */
    const V = { view: [0, 0], hl: null };
    const S = () => TX.range === "5D" && intra ? intra : H;
    function resetView() {
      const s = S(), n = s.n;
      if (s.intraday) { V.view = [0, n - 1]; return; }
      const end = s.d[n - 1], y = +end.slice(0, 4);
      const back = { "1M": [0, 1], "3M": [0, 3], "6M": [0, 6], "1Y": [1, 0], "2Y": [2, 0], "5Y": [5, 0], "10Y": [10, 0] }[TX.range];
      let from = "0000";
      if (TX.range === "YTD") from = `${y}-01-01`;
      else if (back) { const dt = new Date(end + "T00:00:00Z"); dt.setUTCFullYear(dt.getUTCFullYear() - back[0]); dt.setUTCMonth(dt.getUTCMonth() - back[1]); from = dt.toISOString().slice(0, 10); }
      let i0 = s.d.findIndex(x => x >= from); if (i0 < 0) i0 = 0;
      V.view = [i0, n - 1];
    }
    function setView(a, b) { const n = S().n; const span = b - a; if (a < 0) { a = 0; b = Math.min(n - 1, span); } if (b > n - 1) { b = n - 1; a = Math.max(0, b - span); } V.view = [a, b]; schedule(); }
    let raf = 0; const schedule = () => { if (!raf) raf = requestAnimationFrame(() => { raf = 0; draw(); }); };

    /* indicator cache */
    const cache = new Map();
    const memo = (k, f) => { const s = S(); const kk = (s.intraday ? "i:" : "d:") + k; if (!cache.has(kk)) cache.set(kk, f(s)); return cache.get(kk); };
    function aligned(B, s) { return s.d.map(d => { const j = B.idx.get(d); return j == null ? null : B.c[j]; }); }

    let A = null, EV = null;
    function eventMarkers(s) {
      const out = [];
      if (!EV || s.intraday) return out;
      const at = d => { let j = s.idx.get(d); if (j != null) return j; let lo = 0, hi = s.n - 1; if (d > s.d[hi]) return null; while (lo < hi) { const m = (lo + hi) >> 1; if (s.d[m] < d) lo = m + 1; else hi = m; } return lo; };
      for (const r of EV.filings || []) {
        const [form, filed, accn, doc, items, rep, cik] = r;
        const earn = /^8-K/.test(form) && (items || "").includes("2.02");
        const kind = earn ? "earn" : /^8-K/.test(form) ? "k8" : "per";
        if (!TX.mk[kind]) continue;
        const i = at(filed); if (i == null) continue;
        out.push({ i, kind, lab: earn ? "E" : kind === "k8" ? "8" : form.startsWith("10-K") ? "K" : "Q", tip: `${form} filed ${fdate(filed)}<br>${esc(/^8-K/.test(form) ? itemsText(items) : form.startsWith("10-K") ? "Annual report" : "Quarterly report")}`, open: () => openFiling(sym, { form, filed, accn, doc, cik, title: /^8-K/.test(form) ? itemsText(items) : form.startsWith("10-K") ? "Annual report" : "Quarterly report", detail: rep ? `period ${fdate(rep)}` : "" }, earn ? { quote: "Item 2.02 Results of Operations", printed: "2.02", label: "Item 2.02 highlighted" } : null) });
      }
      if (TX.mk.ins && EV.insiders?.length) {
        const byDay = new Map(); EV.insiders.forEach(r => { const k = r[0] + r[1]; if (!byDay.has(k)) byDay.set(k, []); byDay.get(k).push(r); });
        for (const rows of byDay.values()) {
          const i = at(rows[0][0]); if (i == null) continue;
          const val = rows.reduce((t, r) => t + (r[2] && r[3] ? r[2] * r[3] : 0), 0), buy = rows[0][1] === "P";
          out.push({ i, kind: buy ? "buy" : "sell", lab: buy ? "B" : "S", val, tip: `${rows.length} open-market ${buy ? "purchase" : "sale"}${rows.length > 1 ? "s" : ""} on ${fdate(rows[0][0])}<br>${fmtMoney(val)} · ${esc(rows.slice(0, 3).map(r => r[4]).join(", "))}${rows.length > 3 ? "…" : ""}`, open: () => openInsiderDay(sym, rows[0][0], rows, IDX[sym] ? (EV.filings?.[0]?.[6] || D.companies[sym]?.cik) : null) });
        }
      }
      return out;
    }

    function draw() {
      const s = S(), n = s.n; if (!n) return;
      let [i0, i1] = V.view; i0 = Math.max(0, Math.min(i0, n - 2)); i1 = Math.max(i0 + 1, Math.min(i1, n - 1)); V.view = [i0, i1];
      host.innerHTML = "";
      const W = host.clientWidth || 800, phone = W < 640;
      const m = { l: phone ? 4 : 8, r: phone ? 48 : 62 };
      const P = TX.panes, ichOn = TX.ich[0] && !s.intraday;
      const pad = ichOn && i1 === n - 1 ? TX.ich[2] : 0;
      const slots = i1 - i0 + 1 + pad, step = (W - m.l - m.r) / slots;
      const X = i => m.l + (i - i0 + 0.5) * step;
      const lower = ["rsi", "stoch", "macd", "adx", "atr", "obv", "mfi", "cmf", "rs"].filter(k => P[k]?.[0] && !(k === "rs" && s.intraday));
      const priceH = phone ? 250 : 360, stripH = (EV && !s.intraday) ? 18 : 0, volH = P.vol[0] ? (phone ? 44 : 60) : 0, paneH = phone ? 74 : 92, axisH = 20;
      const panes = []; let y = 6;
      panes.push({ id: "price", y, h: priceH }); y += priceH;
      if (stripH) { panes.push({ id: "strip", y, h: stripH }); y += stripH; }
      if (volH) { panes.push({ id: "vol", y: y + 4, h: volH }); y += volH + 4; }
      lower.forEach(id => { panes.push({ id, y: y + 8, h: paneH }); y += paneH + 8; });
      const Ht = y + axisH;
      const svg = d3.select(host).append("svg").attr("width", W).attr("height", Ht).attr("viewBox", `0 0 ${W} ${Ht}`).attr("role", "img").attr("aria-label", `${sym} price chart`);
      const defs = svg.append("defs");
      panes.forEach(p => defs.append("clipPath").attr("id", "txc-" + p.id).append("rect").attr("x", m.l).attr("y", p.y).attr("width", W - m.l - m.r).attr("height", p.h));
      const up = C("--up"), dn = C("--down"), ink = C("--ink"), faint = C("--faint"), line = C("--line");
      const vis = []; for (let i = i0; i <= i1; i++) vis.push(i);

      // overlays on price
      const c = s.c;
      const ov = [];
      if (!s.intraday) TX.ma.forEach((mm, k) => { if (mm[2]) ov.push({ arr: memo(`${mm[0]}${mm[1]}`, z => TA[mm[0]](z.c, mm[1])), col: maColor(k), lab: `${mm[0].toUpperCase()} ${mm[1]}` }); });
      else TX.ma.forEach((mm, k) => { if (mm[2] && mm[1] <= 60) ov.push({ arr: memo(`${mm[0]}${mm[1]}`, z => TA[mm[0]](z.c, mm[1])), col: maColor(k), lab: `${mm[0].toUpperCase()} ${mm[1]} (5m)` }); });
      const bands = [];
      if (TX.bb[0]) { const b = memo(`bb${TX.bb[1]},${TX.bb[2]}`, z => TA.bollinger(z.c, TX.bb[1], TX.bb[2])); bands.push({ up: b.upper, lo: b.lower, mid: b.mid, col: C("--c4"), lab: `BB ${TX.bb[1]},${TX.bb[2]}` }); }
      if (TX.kc[0]) { const k = memo(`kc${TX.kc.join()}`, z => TA.keltner(z.h, z.l, z.c, TX.kc[1], TX.kc[2], TX.kc[3])); bands.push({ up: k.upper, lo: k.lower, mid: k.mid, col: C("--c2"), lab: `KC ${TX.kc[1]},${TX.kc[2]}` }); }
      const st = TX.st[0] ? memo(`st${TX.st.join()}`, z => TA.supertrend(z.h, z.l, z.c, TX.st[1], TX.st[2])) : null;
      const ich = ichOn ? memo(`ich${TX.ich.join()}`, z => TA.ichimoku(z.h, z.l, z.c, TX.ich[1], TX.ich[2], TX.ich[3], TX.ich[2])) : null;
      const vwap = s.intraday && TX.vwap[0] ? memo("vwap", z => TA.vwapSession(z.h, z.l, z.c, z.v, z.session)) : null;
      const anchors = s.intraday ? [] : (TX.anchors[sym] || []).map(d => ({ d, i: s.idx.get(d) })).filter(a => a.i != null).map((a, k) => ({ ...a, arr: memo("av" + a.d, z => TA.anchoredVwap(z.h, z.l, z.c, z.v, a.i)), col: [C("--c5"), C("--c4"), C("--c2")][k % 3] }));
      let cmp = null;
      if (TX.cmp && HIST[TX.cmp] && TX.cmp !== sym && !s.intraday) { const al = memo("cmp" + TX.cmp, z => aligned(HIST[TX.cmp], z)); let b0 = null; for (let i = i0; i <= i1; i++) if (al[i] != null) { b0 = i; break; } if (b0 != null) cmp = { arr: al.map((v, i) => v == null ? null : c[b0] * v / al[b0]), raw: al, b0 }; }
      // y domain
      let lo = Infinity, hi = -Infinity;
      const inc = v => { if (v != null && isFinite(v)) { if (v < lo) lo = v; if (v > hi) hi = v; } };
      vis.forEach(i => { inc(s.l[i]); inc(s.h[i]); ov.forEach(o => inc(o.arr[i])); bands.forEach(b => { inc(b.up[i]); inc(b.lo[i]); }); if (st) inc(st.line[i]); if (cmp) inc(cmp.arr[i]); if (vwap) inc(vwap[i]); });
      if (ich) for (let i = i0 - TX.ich[2]; i <= i1; i++) if (i >= 0) { inc(ich.spanA[i]); inc(ich.spanB[i]); }
      if (lo === hi) { lo *= 0.99; hi *= 1.01; }
      const pr = panes[0];
      const yP = (TX.log && lo > 0 ? d3.scaleLog() : d3.scaleLinear()).domain([lo, hi]).range([pr.y + pr.h - 6, pr.y + 8]);
      if (!TX.log) yP.nice();
      // grid and axis
      const gA = svg.append("g").attr("class", "gl").attr("transform", `translate(${W - m.r},0)`).call(d3.axisRight(yP).ticks(phone ? 5 : 7, TX.log ? "~r" : undefined).tickSize(-(W - m.l - m.r)).tickFormat(v => v >= 1000 ? d3.format(",.0f")(v) : d3.format(",.2f")(v)));
      gA.select(".domain").remove(); gA.selectAll("text").attr("x", 6);
      // x ticks
      const ticks = []; const lab = s.intraday ? (i => s.d[i].slice(5, 10) !== s.d[i - 1]?.slice(5, 10)) : null;
      const spanDays = vis.length;
      const keyOf = i => s.intraday ? s.d[i].slice(0, 10) : spanDays > 1500 ? s.d[i].slice(0, 4) : spanDays > 300 ? s.d[i].slice(0, 7).replace(/-(0[2-3]|0[5-6]|0[8-9]|1[1-2])$/, "-x") : s.d[i].slice(0, 7);
      for (let i = i0 + 1; i <= i1; i++) if (keyOf(i) !== keyOf(i - 1) && !keyOf(i).endsWith("-x")) ticks.push(i);
      const maxT = Math.max(2, Math.floor((W - m.l - m.r) / (phone ? 56 : 80))), every = Math.ceil(ticks.length / maxT);
      const fmtT = i => s.intraday ? `${MONTHS3[+s.d[i].slice(5, 7) - 1]} ${+s.d[i].slice(8, 10)}` : spanDays > 1500 ? s.d[i].slice(0, 4) : (s.d[i].slice(5, 7) === "01" ? s.d[i].slice(0, 4) : `${MONTHS3[+s.d[i].slice(5, 7) - 1]}${spanDays > 300 ? "" : " " + s.d[i].slice(2, 4)}`);
      const gx = svg.append("g");
      ticks.filter((_, k) => k % every === 0).forEach(i => { gx.append("line").attr("x1", X(i)).attr("x2", X(i)).attr("y1", pr.y).attr("y2", Ht - axisH).attr("stroke", line).attr("stroke-dasharray", "2 3"); gx.append("text").attr("x", X(i)).attr("y", Ht - 6).attr("text-anchor", "middle").text(fmtT(i)); });
      const gp = svg.append("g").attr("clip-path", "url(#txc-price)");
      const path = (arr, col, w = 1.3, dash = null, op = 1, yS = yP) => { let d = "", pen = false; for (let i = i0; i <= i1; i++) { const v = arr[i]; if (v == null || !isFinite(v) || (TX.log && yS === yP && v <= 0)) { pen = false; continue; } d += (pen ? "L" : "M") + X(i).toFixed(1) + "," + yS(v).toFixed(1); pen = true; } if (d) gp.append("path").attr("d", d).attr("fill", "none").attr("stroke", col).attr("stroke-width", w).attr("stroke-dasharray", dash).attr("opacity", op); };
      // highlight bars (focused event)
      if (V.hl) V.hl.bars.forEach(i => { if (i >= i0 && i <= i1) gp.append("rect").attr("x", X(i) - Math.max(step, 4) / 2).attr("width", Math.max(step, 4)).attr("y", pr.y).attr("height", pr.h).attr("fill", C("--mark")).attr("opacity", .55); });
      // ichimoku cloud
      if (ich) {
        const sh = TX.ich[2], ptsA = [], ptsB = [];
        for (let i = Math.max(0, i0 - sh); i <= i1; i++) if (ich.spanA[i] != null && ich.spanB[i] != null && i + sh <= i1 + pad) { ptsA.push(i); }
        if (ptsA.length) {
          const xa = i => X(i + sh);
          let k = 0; while (k < ptsA.length) { const bull = ich.spanA[ptsA[k]] >= ich.spanB[ptsA[k]]; let e = k; while (e + 1 < ptsA.length && (ich.spanA[ptsA[e + 1]] >= ich.spanB[ptsA[e + 1]]) === bull) e++; const seg_ = ptsA.slice(k, Math.min(e + 2, ptsA.length)); gp.append("path").attr("d", d3.area().x(xa).y0(i => yP(ich.spanA[i])).y1(i => yP(ich.spanB[i]))(seg_)).attr("fill", bull ? up : dn).attr("opacity", .12); k = e + 1; }
          const shifted = arr => { const o = new Array(n + sh).fill(null); arr.forEach((v, i) => { o[i + sh] = v; }); return o; };
          const sA = shifted(ich.spanA), sB = shifted(ich.spanB);
          const pth = (arr, col) => { let d = "", pen = false; for (let i = i0; i <= i1 + pad; i++) { const v = arr[i]; if (v == null) { pen = false; continue; } d += (pen ? "L" : "M") + X(i).toFixed(1) + "," + yP(v).toFixed(1); pen = true; } gp.append("path").attr("d", d).attr("fill", "none").attr("stroke", col).attr("stroke-width", 1).attr("opacity", .7); };
          pth(sA, up); pth(sB, dn);
        }
        path(ich.tenkan, C("--c1"), 1.1); path(ich.kijun, C("--c5"), 1.1);
        const lag = c.map((_, i) => c[i + sh] ?? null); path(lag, C("--c6"), 1, "2 2");
      }
      bands.forEach(b => { const pts = vis.filter(i => b.up[i] != null); if (pts.length) gp.append("path").attr("d", d3.area().x(X).y0(i => yP(b.lo[i])).y1(i => yP(b.up[i]))(pts)).attr("fill", b.col).attr("opacity", .07); path(b.up, b.col, 1, "3 3"); path(b.lo, b.col, 1, "3 3"); path(b.mid, b.col, 1, null, .6); });
      // patterns
      const st8 = A?.state;
      if (A && !s.intraday) {
        if (TX.pat.hilo && st8) [[st8.hi52, up, "52w high"], [st8.lo52, dn, "52w low"]].forEach(([v, col, t]) => { if (v >= lo && v <= hi) { const x0 = X(Math.max(i0, n - 252)); gp.append("line").attr("x1", x0).attr("x2", W - m.r).attr("y1", yP(v)).attr("y2", yP(v)).attr("stroke", col).attr("stroke-dasharray", "1 3").attr("opacity", .8); gp.append("text").attr("x", x0 + 4).attr("y", yP(v) - 3).text(t).style("fill", col); } });
        if (TX.pat.levels) A.levels.forEach(L => { if (L.price < lo || L.price > hi) return; const x0 = X(Math.max(i0, L.first)); const col = L.kind === "resistance" ? dn : up; gp.append("line").attr("x1", x0).attr("x2", W - m.r).attr("y1", yP(L.price)).attr("y2", yP(L.price)).attr("stroke", col).attr("stroke-width", 1).attr("opacity", .55); gp.append("text").attr("x", W - m.r - 4).attr("y", yP(L.price) - 3).attr("text-anchor", "end").text(`${L.kind === "resistance" ? "R" : "S"} ${L.touches}×`).style("fill", col).style("font-size", "10px"); L.bars.forEach(i => { if (i >= i0 && i <= i1) gp.append("circle").attr("cx", X(i)).attr("cy", yP(L.price)).attr("r", 2.4).attr("fill", col).attr("opacity", .8); }); });
        if (TX.pat.trend) A.trend.forEach(tl => { const a = Math.max(i0, tl.i1), b = i1; if (b < a) return; const at = i => tl.p1 + tl.slope * (i - tl.i1); gp.append("line").attr("x1", X(a)).attr("x2", X(b)).attr("y1", yP(at(a))).attr("y2", yP(at(b))).attr("stroke", tl.side === "support" ? up : dn).attr("stroke-width", 1.4).attr("stroke-dasharray", "6 3"); tl.bars.forEach(i => { if (i >= i0 && i <= i1) gp.append("circle").attr("cx", X(i)).attr("cy", yP(at(i))).attr("r", 2.6).attr("fill", "none").attr("stroke", tl.side === "support" ? up : dn); }); });
        if (TX.pat.gaps) A.gaps.filter(g => g.filled == null && g.i <= i1).forEach(g => { const x0 = X(Math.max(i0, g.i)) - step / 2; gp.append("rect").attr("x", x0).attr("width", Math.max(0, W - m.r - x0)).attr("y", yP(Math.max(g.top, g.bottom))).attr("height", Math.max(1, Math.abs(yP(g.top) - yP(g.bottom)))).attr("fill", g.type === "gap_up" ? up : dn).attr("opacity", .1); });
      }
      // price series
      const mode = TX.type === "line" || step < 2.2 ? "line" : TX.type;
      if (mode === "line") path(c, ink, 1.4);
      else {
        let wU = "", wD = "", bU = "", bD = "";
        const bw = Math.max(1, step * 0.66);
        for (const i of vis) {
          const x = X(i), o = s.o[i], cc = c[i], u = cc >= o;
          const yh = yP(s.h[i]), yl = yP(s.l[i]);
          if (mode === "candle") { const w = `M${x.toFixed(1)},${yh.toFixed(1)}V${yl.toFixed(1)}`; const top = yP(Math.max(o, cc)), hgt = Math.max(1, Math.abs(yP(o) - yP(cc))); const b = `M${(x - bw / 2).toFixed(1)},${top.toFixed(1)}h${bw.toFixed(1)}v${hgt.toFixed(1)}h${(-bw).toFixed(1)}Z`; if (u) { wU += w; bU += b; } else { wD += w; bD += b; } }
          else { const w = `M${x.toFixed(1)},${yh.toFixed(1)}V${yl.toFixed(1)}M${(x - bw / 2).toFixed(1)},${yP(o).toFixed(1)}H${x.toFixed(1)}M${x.toFixed(1)},${yP(cc).toFixed(1)}H${(x + bw / 2).toFixed(1)}`; if (u) wU += w; else wD += w; }
        }
        gp.append("path").attr("d", wU).attr("stroke", up).attr("stroke-width", 1).attr("fill", "none");
        gp.append("path").attr("d", wD).attr("stroke", dn).attr("stroke-width", 1).attr("fill", "none");
        if (bU) gp.append("path").attr("d", bU).attr("fill", up); if (bD) gp.append("path").attr("d", bD).attr("fill", dn);
      }
      ov.forEach(o => path(o.arr, o.col, 1.3));
      if (st) { const sU = st.line.map((v, i) => st.dir[i] === 1 ? v : null), sD = st.line.map((v, i) => st.dir[i] === -1 ? v : null); path(sU, up, 1.6); path(sD, dn, 1.6); }
      if (vwap) path(vwap, C("--c2"), 1.4, "4 2");
      anchors.forEach(a => path(a.arr, a.col, 1.5, "5 2"));
      if (cmp) path(cmp.arr, C("--c4"), 1.4, null, .9);
      // signal markers
      if (A && TX.pat.events && !s.intraday) {
        A.events.forEach(e => { if (e.i < i0 || e.i > i1) return; const sg = TA.SIGNALS[e.type]; const bull = sg.dir > 0; const yy = bull ? yP(s.l[e.i]) + 9 : yP(s.h[e.i]) - 9; gp.append("path").attr("class", "mk").attr("d", d3.symbol(d3.symbolTriangle, 26)()).attr("transform", `translate(${X(e.i)},${yy}) rotate(${bull ? 0 : 180})`).attr("fill", bull ? up : dn).attr("opacity", .85).on("mousemove", ev => showTip(ev, `<b>${esc(sg.name)}</b><br>${fdate(s.d[e.i])}<br><span style="opacity:.75">${esc(sg.text)}</span>`)).on("mouseleave", hideTip).on("click", () => focusEvent(e)); });
      }
      // last price tag
      const lastI = Math.min(i1, n - 1), ly = yP(c[lastI]);
      svg.append("rect").attr("x", W - m.r + 2).attr("y", ly - 9).attr("width", m.r - 4).attr("height", 18).attr("rx", 3).attr("fill", ink);
      svg.append("text").attr("x", W - m.r + 6).attr("y", ly + 4).text(c[lastI] >= 1000 ? d3.format(",.0f")(c[lastI]) : fnum(c[lastI])).style("fill", C("--bg")).style("font-weight", 600);
      // markers strip
      const strip = panes.find(p => p.id === "strip");
      if (strip) {
        const mk = eventMarkers(s).filter(e => e.i >= i0 && e.i <= i1);
        const gm = svg.append("g");
        const colOf = k => k === "earn" ? C("--accent") : k === "buy" ? up : k === "sell" ? dn : faint;
        mk.forEach(e => { const g = gm.append("g").attr("class", "mk").attr("transform", `translate(${X(e.i)},${strip.y + 9})`).on("mousemove", ev => showTip(ev, e.tip + "<br><span style='opacity:.7'>Click to open</span>")).on("mouseleave", hideTip).on("click", ev => { ev.stopPropagation(); e.open(); }); g.append("circle").attr("r", 7).attr("fill", colOf(e.kind)).attr("opacity", e.kind === "k8" || e.kind === "per" ? .25 : .9); g.append("text").attr("text-anchor", "middle").attr("y", 3.5).text(e.lab).style("fill", e.kind === "k8" || e.kind === "per" ? C("--ink") : "#fff").style("font-size", "9px").style("font-weight", 700); });
      }
      // volume
      const pv = panes.find(p => p.id === "vol");
      if (pv) {
        const vmax = d3.max(vis, i => s.v[i]) || 1, yv = d3.scaleLinear().domain([0, vmax]).range([pv.y + pv.h, pv.y + 4]);
        let dU = "", dD = ""; const bw = Math.max(1, step * 0.7);
        vis.forEach(i => { const r = `M${(X(i) - bw / 2).toFixed(1)},${yv(s.v[i]).toFixed(1)}h${bw.toFixed(1)}V${(pv.y + pv.h).toFixed(1)}h${(-bw).toFixed(1)}Z`; if (c[i] >= s.o[i]) dU += r; else dD += r; });
        svg.append("path").attr("d", dU).attr("fill", up).attr("opacity", .3); svg.append("path").attr("d", dD).attr("fill", dn).attr("opacity", .3);
        svg.append("text").attr("x", m.l + 2).attr("y", pv.y + 10).text("Volume");
        svg.append("text").attr("x", W - m.r + 6).attr("y", pv.y + 10).text(d3.format(".2s")(vmax));
      }
      // lower panes
      const PANE = {};
      lower.forEach(id => {
        const p = panes.find(q => q.id === id), cfg = paneDef(id, s, ctx);
        PANE[id] = cfg;
        const vals = []; vis.forEach(i => cfg.lines.forEach(L => { const v = L.arr[i]; if (v != null && isFinite(v)) vals.push(v); }));
        if (cfg.hist) vis.forEach(i => { const v = cfg.hist[i]; if (v != null) vals.push(v); });
        let dom = cfg.domain || d3.extent(vals); if (!dom || dom[0] == null) dom = [0, 1]; if (dom[0] === dom[1]) dom = [dom[0] - 1, dom[1] + 1];
        const ys = d3.scaleLinear().domain(dom).range([p.y + p.h - 2, p.y + 4]); if (!cfg.domain) ys.nice(3);
        cfg.ys = ys; cfg.p = p;
        const ga = svg.append("g").attr("class", "gl").attr("transform", `translate(${W - m.r},0)`).call(d3.axisRight(ys).ticks(3).tickSize(-(W - m.l - m.r)).tickFormat(cfg.fmt || d3.format("~s")));
        ga.select(".domain").remove(); ga.selectAll("text").attr("x", 6);
        const g = svg.append("g").attr("clip-path", `url(#txc-${id})`);
        (cfg.lines0 || []).forEach(v => g.append("line").attr("x1", m.l).attr("x2", W - m.r).attr("y1", ys(v)).attr("y2", ys(v)).attr("stroke", faint).attr("stroke-dasharray", "3 3"));
        if (cfg.hist) { let dU = "", dD = ""; const bw = Math.max(1, step * 0.7), y0 = ys(0); vis.forEach(i => { const v = cfg.hist[i]; if (v == null) return; const r = `M${(X(i) - bw / 2).toFixed(1)},${y0.toFixed(1)}h${bw.toFixed(1)}V${ys(v).toFixed(1)}h${(-bw).toFixed(1)}Z`; if (v >= 0) dU += r; else dD += r; }); g.append("path").attr("d", dU).attr("fill", up).attr("opacity", .4); g.append("path").attr("d", dD).attr("fill", dn).attr("opacity", .4); }
        cfg.lines.forEach(L => { let d = "", pen = false; for (let i = i0; i <= i1; i++) { const v = L.arr[i]; if (v == null || !isFinite(v)) { pen = false; continue; } d += (pen ? "L" : "M") + X(i).toFixed(1) + "," + ys(v).toFixed(1); pen = true; } g.append("path").attr("d", d).attr("fill", "none").attr("stroke", L.col).attr("stroke-width", 1.3); });
        svg.append("text").attr("x", m.l + 2).attr("y", p.y + 11).text(cfg.title).style("font-weight", 600);
      });
      // legend in readout default
      const legendItems = [...ov.map(o => [o.col, o.lab]), ...bands.map(b => [b.col, b.lab]), ...(st ? [[up, `Supertrend ${TX.st[1]},${TX.st[2]}`]] : []), ...(ich ? [[C("--c1"), "Ichimoku"]] : []), ...(vwap ? [[C("--c2"), "VWAP"]] : []), ...anchors.map(a => [a.col, `AVWAP ${a.d}`]), ...(cmp ? [[C("--c4"), `${TX.cmp.replace("^", "")} rebased`]] : [])];
      const setReadout = i => {
        readout.innerHTML = "";
        if (i == null) { readout.append(...legendItems.map(([col, t]) => el("span", {}, el("span", { class: "tx-sw", style: `background:${col}` }), t))); return; }
        const ch = i ? c[i] / c[i - 1] - 1 : null;
        readout.append(el("span", {}, el("b", {}, s.intraday ? s.d[i] + " ET" : fdate(s.d[i]))), el("span", {}, "O ", el("b", {}, f2(s.o[i]))), el("span", {}, "H ", el("b", {}, f2(s.h[i]))), el("span", {}, "L ", el("b", {}, f2(s.l[i]))), el("span", {}, "C ", el("b", {}, f2(c[i]))), el("span", { class: cls(ch) }, pp(ch, 2)), el("span", {}, "V ", el("b", {}, d3.format(".3s")(s.v[i] || 0))));
        ov.forEach(o => o.arr[i] != null && readout.append(el("span", { style: `color:${o.col}` }, `${o.lab} ${f2(o.arr[i])}`)));
        bands.forEach(b => b.up[i] != null && readout.append(el("span", { style: `color:${b.col}` }, `${b.lab} ${f2(b.lo[i])}–${f2(b.up[i])}`)));
        if (st && st.line[i] != null) readout.append(el("span", { style: `color:${st.dir[i] === 1 ? up : dn}` }, `ST ${f2(st.line[i])}`));
        if (vwap) readout.append(el("span", { style: `color:${C("--c2")}` }, `VWAP ${f2(vwap[i])}`));
        anchors.forEach(a => a.arr[i] != null && readout.append(el("span", { style: `color:${a.col}` }, `AVWAP ${f2(a.arr[i])}`)));
        if (cmp && cmp.raw[i] != null) readout.append(el("span", { style: `color:${C("--c4")}` }, `${TX.cmp.replace("^", "")} ${f2(cmp.raw[i])} (${pp(cmp.raw[i] / cmp.raw[cmp.b0] - 1)} vs ${pp(c[i] / c[cmp.b0] - 1)})`));
        lower.forEach(id => { const L = PANE[id]; readout.append(el("span", {}, `${L.title} `, el("b", {}, L.lines.map(z => z.arr[i] == null ? "–" : (L.fmt || d3.format(".2f"))(z.arr[i])).join(" / ")))); });
      };
      setReadout(null);
      // crosshair and interaction
      const cross = svg.append("line").attr("y1", pr.y).attr("y2", Ht - axisH).attr("stroke", faint).attr("stroke-dasharray", "2 2").style("display", "none").style("pointer-events", "none");
      const hline = svg.append("line").attr("x1", m.l).attr("x2", W - m.r).attr("stroke", faint).attr("stroke-dasharray", "2 2").style("display", "none").style("pointer-events", "none");
      const hlab = svg.append("g").style("display", "none").style("pointer-events", "none");
      hlab.append("rect").attr("x", W - m.r + 2).attr("width", m.r - 4).attr("height", 16).attr("rx", 3).attr("fill", C("--accent"));
      const hlt = hlab.append("text").attr("x", W - m.r + 6).style("fill", "#fff");
      const ov2 = svg.append("rect").attr("x", m.l).attr("y", pr.y).attr("width", W - m.l - m.r).attr("height", Ht - axisH - pr.y).attr("fill", "transparent").style("cursor", anchorMode ? "crosshair" : "default");
      const topG = svg.append("g"); svg.selectAll(".mk").each(function () { topG.node().appendChild(this); });
      const idxAt = mx => Math.max(i0, Math.min(i1, i0 + Math.floor((mx - m.l) / step)));
      const hover = (mx, my) => {
        const i = idxAt(mx); cross.style("display", null).attr("x1", X(i)).attr("x2", X(i)); setReadout(i);
        const p = panes.find(q => my >= q.y && my <= q.y + q.h && q.id !== "strip");
        if (p) { const ys = p.id === "price" ? yP : PANE[p.id]?.ys; if (ys) { hline.style("display", null).attr("y1", my).attr("y2", my); hlab.style("display", null).attr("transform", `translate(0,${my - 8})`); const v = ys.invert(my); hlt.attr("y", 12).text(Math.abs(v) >= 1000 ? d3.format(",.0f")(v) : d3.format(",.2f")(v)); } } else { hline.style("display", "none"); hlab.style("display", "none"); }
      };
      const leave = () => { cross.style("display", "none"); hline.style("display", "none"); hlab.style("display", "none"); setReadout(null); };
      const node = ov2.node(), ptrs = new Map(); let drag = null, pinch = null;
      node.addEventListener("pointerdown", e => { ptrs.set(e.pointerId, e); if (ptrs.size === 2) { const [a, b] = [...ptrs.values()]; pinch = { d: Math.abs(a.clientX - b.clientX), view: [...V.view] }; drag = null; } else drag = { x: e.clientX, y: e.clientY, view: [...V.view], moved: false, type: e.pointerType }; });
      node.addEventListener("pointermove", e => {
        const [mx, my] = d3.pointer(e, svg.node());
        if (ptrs.has(e.pointerId)) ptrs.set(e.pointerId, e);
        if (pinch && ptrs.size === 2) { const [a, b] = [...ptrs.values()]; const dd = Math.abs(a.clientX - b.clientX) || 1; const span = pinch.view[1] - pinch.view[0]; const ns = Math.max(10, Math.min(S().n, Math.round(span * pinch.d / dd))); const mid = (pinch.view[0] + pinch.view[1]) / 2; setView(Math.round(mid - ns / 2), Math.round(mid - ns / 2) + ns); return; }
        if (drag) { const dx = e.clientX - drag.x, dy = e.clientY - drag.y; if (!drag.moved && Math.abs(dx) > 4 && Math.abs(dx) > Math.abs(dy)) { drag.moved = true; try { node.setPointerCapture(e.pointerId); } catch { } } if (drag.moved) { const sh = Math.round(-dx / step); setView(drag.view[0] + sh, drag.view[1] + sh); return; } }
        hover(mx, my);
      });
      const end = e => { ptrs.delete(e.pointerId); if (ptrs.size < 2) pinch = null; if (drag && !drag.moved && e.type === "pointerup") { const [mx] = d3.pointer(e, svg.node()); const i = idxAt(mx); if (anchorMode && !s.intraday) { const d = s.d[i]; TX.anchors[sym] = [...new Set([...(TX.anchors[sym] || []), d])].slice(-3); save(); anchorMode = false; anchorBtn.classList.remove("acc"); anchorBtn.textContent = "Anchor VWAP"; buildInd(); draw(); toast(`VWAP anchored at ${fdate(d)}`); } } drag = null; };
      node.addEventListener("pointerup", end); node.addEventListener("pointercancel", end); node.addEventListener("pointerleave", e => { if (!drag) leave(); });
      node.addEventListener("wheel", e => { if (Math.abs(e.deltaY) < Math.abs(e.deltaX)) { e.preventDefault(); const sh = Math.round(e.deltaX / step); setView(V.view[0] + sh, V.view[1] + sh); return; } e.preventDefault(); const [mx] = d3.pointer(e, svg.node()); const i = idxAt(mx), f = e.deltaY > 0 ? 1.15 : 1 / 1.15, [a, b] = V.view, ns = Math.max(10, Math.min(S().n, Math.round((b - a + 1) * f))), r = (i - a) / Math.max(1, b - a); const na = Math.round(i - r * (ns - 1)); setView(na, na + ns - 1); }, { passive: false });
      node.addEventListener("dblclick", () => { resetView(); V.hl = null; draw(); });
    }

    function paneDef(id, s, ctx) {
      const P = TX.panes, c1 = C("--c1"), c3 = C("--c3"), c5 = C("--c5"), c2 = C("--c2");
      if (id === "rsi") { const r = memo("rsi" + P.rsi[1], z => TA.rsi(z.c, P.rsi[1])); return { title: `RSI ${P.rsi[1]}`, lines: [{ arr: r, col: c1 }], domain: [0, 100], lines0: [30, 70], fmt: d3.format(".0f") }; }
      if (id === "stoch") { const st = memo("st" + P.stoch.join(), z => TA.stochastic(z.h, z.l, z.c, P.stoch[1], P.stoch[2], P.stoch[3])); return { title: `Stoch ${P.stoch.slice(1).join(",")}`, lines: [{ arr: st.k, col: c1 }, { arr: st.d, col: c3 }], domain: [0, 100], lines0: [20, 80], fmt: d3.format(".0f") }; }
      if (id === "macd") { const mm = memo("macd" + P.macd.join(), z => TA.macd(z.c, P.macd[1], P.macd[2], P.macd[3])); return { title: `MACD ${P.macd.slice(1).join(",")}`, lines: [{ arr: mm.line, col: c1 }, { arr: mm.signal, col: c3 }], hist: mm.hist, lines0: [0], fmt: d3.format(".2~f") }; }
      if (id === "adx") { const a = memo("adx" + P.adx[1], z => TA.adx(z.h, z.l, z.c, P.adx[1])); return { title: `ADX ${P.adx[1]} · +DI · −DI`, lines: [{ arr: a.adx, col: C("--ink") }, { arr: a.pdi, col: C("--up") }, { arr: a.mdi, col: C("--down") }], lines0: [20, 25], fmt: d3.format(".0f") }; }
      if (id === "atr") { const a = memo("atr" + P.atr[1], z => TA.atr(z.h, z.l, z.c, P.atr[1])); return { title: `ATR ${P.atr[1]}`, lines: [{ arr: a, col: c5 }], fmt: d3.format(".2~f") }; }
      if (id === "obv") { const o = memo("obv", z => TA.obv(z.c, z.v)); return { title: "OBV", lines: [{ arr: o, col: c2 }], fmt: d3.format(".2s") }; }
      if (id === "mfi") { const o = memo("mfi" + P.mfi[1], z => TA.mfi(z.h, z.l, z.c, z.v, P.mfi[1])); return { title: `MFI ${P.mfi[1]}`, lines: [{ arr: o, col: c2 }], domain: [0, 100], lines0: [20, 80], fmt: d3.format(".0f") }; }
      if (id === "cmf") { const o = memo("cmf" + P.cmf[1], z => TA.cmf(z.h, z.l, z.c, z.v, P.cmf[1])); return { title: `CMF ${P.cmf[1]}`, lines: [{ arr: o, col: c2 }], lines0: [0], fmt: d3.format(".2f") }; }
      if (id === "rs") {
        const L = [];
        for (const [b, col] of [["SPY", c1], [ctx.secEtf, c3]]) { if (!b || !HIST[b] || b === sym) continue; const r = memo("rs" + b, z => TA.relStrength(z.c, aligned(HIST[b], z), 250)); L.push({ arr: r.mansfield, col, b }); }
        return { title: `Mansfield RS vs ${L.map(x => x.b).join(", ")} (%)`, lines: L, lines0: [0], fmt: d3.format(".0f") };
      }
    }

    function focusEvent(e) {
      const s = S(); if (s.intraday) return;
      const pad = 60, a = Math.max(0, Math.min(...e.bars) - pad), b = Math.min(s.n - 1, Math.max(e.i, ...e.bars) + pad);
      V.hl = { bars: e.bars.length > 2 && e.type.startsWith("squeeze") ? range(e.bars[0], e.bars[2]) : e.bars };
      V.view = [a, b]; draw();
      panelEl.scrollIntoView({ behavior: "smooth", block: "start" });
    }

    buildInd(); resetView(); draw();
    let rw = host.clientWidth; const ro = new ResizeObserver(() => { if (Math.abs(host.clientWidth - rw) > 2) { rw = host.clientWidth; draw(); } }); ro.observe(host);
    [A, EV] = await Promise.all([A0, E0]);
    draw();
    renderEvents(evHost, H, A, focusEvent, EV);
    renderSummary(sumHost, H, A, ctx);
  }

  function renderEvents(host, H, A, focus, EV) {
    let tab = "events";
    const box = el("div");
    const draw = () => {
      box.innerHTML = "";
      const t = el("table", { class: "t" }); const tb = el("tbody");
      const rowsEv = A.events.slice().reverse();
      if (tab === "events") {
        t.append(el("thead", {}, el("tr", {}, el("th", {}, "Date"), el("th", {}, "Event"), el("th", {}, "Defined by"))));
        rowsEv.slice(0, 200).forEach(e => { const sg = TA.SIGNALS[e.type]; tb.append(el("tr", { class: "click", onclick: () => focus(e), title: sg.text }, el("td", { class: "num" }, H.d[e.i]), el("td", {}, el("span", { class: "tx-tag " + (sg.dir > 0 ? "u" : "d") }, sg.dir > 0 ? "▲" : "▼"), " ", sg.name), el("td", { class: "faint", style: "font-size:11.5px" }, e.bars.map(i => H.d[i]).filter((x, k, arr) => arr.indexOf(x) === k).join(", ")))); });
      } else if (tab === "levels") {
        t.append(el("thead", {}, el("tr", {}, el("th", {}, "Level"), el("th", { class: "r" }, "Price"), el("th", { class: "r" }, "Touches"), el("th", { class: "r" }, "Distance"), el("th", {}, "Swing points"))));
        A.levels.forEach(L => tb.append(el("tr", { class: "click", onclick: () => focus({ i: L.last, bars: L.bars, type: "level" }) }, el("td", {}, el("span", { class: "tx-tag " + (L.kind === "resistance" ? "d" : "u") }, L.kind === "resistance" ? "R" : "S"), " ", L.kind), el("td", { class: "r num" }, f2(L.price)), el("td", { class: "r num" }, L.touches), el("td", { class: "r num " + cls(L.distance) }, pp(L.distance)), el("td", { class: "faint", style: "font-size:11.5px" }, L.bars.map(i => H.d[i]).join(", ")))));
      } else if (tab === "trend") {
        t.append(el("thead", {}, el("tr", {}, el("th", {}, "Line"), el("th", {}, "Anchors"), el("th", { class: "r" }, "Touches"), el("th", { class: "r" }, "Now at"))));
        A.trend.forEach(tl => tb.append(el("tr", { class: "click", onclick: () => focus({ i: H.n - 1, bars: tl.bars, type: "trend" }) }, el("td", {}, tl.side === "support" ? "Rising support" : "Falling resistance"), el("td", { class: "num" }, `${H.d[tl.i1]} ${f2(tl.p1)} → ${H.d[tl.i2]} ${f2(tl.p2)}`), el("td", { class: "r num" }, tl.touches), el("td", { class: "r num" }, f2(tl.now)))));
      } else {
        t.append(el("thead", {}, el("tr", {}, el("th", {}, "Date"), el("th", {}, "Gap"), el("th", { class: "r" }, "Range"), el("th", {}, "Filled"))));
        A.gaps.slice().reverse().slice(0, 150).forEach(g => tb.append(el("tr", { class: "click", onclick: () => focus(g) }, el("td", { class: "num" }, H.d[g.i]), el("td", {}, g.type === "gap_up" ? "Up" : "Down"), el("td", { class: "r num" }, `${f2(Math.min(g.top, g.bottom))}–${f2(Math.max(g.top, g.bottom))}`), el("td", { class: g.filled == null ? "" : "faint" }, g.filled == null ? "open" : H.d[g.filled]))));
      }
      if (!tb.children.length) tb.append(el("tr", {}, el("td", { colspan: 5, class: "muted" }, "Nothing detected.")));
      t.append(tb); box.append(el("div", { class: "tx-list" }, t));
    };
    host.append(el("div", { class: "tx-bar" }, seg([["events", `Events (${A.events.length})`], ["levels", `Levels (${A.levels.length})`], ["trend", `Trend lines (${A.trend.length})`], ["gaps", `Gaps (${A.gaps.filter(g => g.filled == null).length} open)`]], tab, v => { tab = v; draw(); })), box,
      el("div", { class: "src" }, `Swing points: highest high / lowest low with 5 bars on each side. Levels cluster swing prices within max(1% of price, 0.75 ATR) over the last two years; the six nearest with two or more touches are kept. Trend lines join two swing points and are dropped once a close breaks them. Click a row to show its bars on the chart. Computed in ${A.ms} ms${EV?.filings ? ` · ${EV.filings.length} filings and ${EV.insiders?.length || 0} insider trades loaded${EV.insiderNote ? " (" + EV.insiderNote + ")" : ""}` : ""}.`));
    draw();
  }

  async function renderSummary(host, H, A, ctx) {
    const st = A.state, L = H.n - 1;
    const extra = { cone: A.cone, since: H.d[0].slice(0, 4) };
    const rsOf = B => { if (!B || B === H.sym || !HIST[B]) return null; const b = HIST[B], j1 = b.idx.get(H.d[L]), j0 = b.idx.get(H.d[L - 63]); return j1 != null && j0 != null ? (H.c[L] / H.c[L - 63]) / (b.c[j1] / b.c[j0]) - 1 : null; };
    extra.rsSpy = rsOf("SPY"); extra.rsSector = rsOf(ctx.secEtf);
    extra.recent = A.events.filter(e => e.i >= L - 15).slice(-3).map(e => ({ type: e.type, date: fdate(H.d[e.i]) }));
    const S = TA.summary(st, extra);
    const values = { ...st, vol21: A.cone.find(x => x.n === 21)?.current, vol21pct: A.cone.find(x => x.n === 21)?.pct, rsSpy: extra.rsSpy, rsSector: extra.rsSector, since: +extra.since };
    const bad = TA.validateSummary(S, values);
    host.innerHTML = "";
    if (bad.length) { host.append(el("div", { class: "note" }, "The summary was withheld because a number in it did not match the computed values.")); return; }
    // turn numbers into links to their calculations, matched by the order of refs in each sentence
    const linkable = { vs200: "vs200", vs50: "vs50", adx: "adx", pdi: "adx", mdi: "adx", rsi: "rsi", fromHi: "fromHi", hi52: "hi52", fromLo: "fromLo", lo52: "lo52", atr: "atr", atrPct: "atr", squeeze: "squeeze", vol21: "vol21", macdHist: "macdHist" };
    S.forEach(sn => {
      const p = el("p");
      const refs = sn.refs.filter(r => r !== "sma50" && r !== "sma200");
      const parts = sn.text.split(/([+−-]?\d[\d,]*(?:\.\d+)?%?)/);
      let k = 0;
      parts.forEach((part, j) => {
        if (j % 2 === 0) { p.append(part); return; }
        const r = refs[k];
        if (TA.fixedNumber(parts[j - 1], parts[j + 1], part) || !r) { p.append(part); return; }
        k++;
        if (r === "rsSpy" || r === "rsSector") { const B = r === "rsSpy" ? "SPY" : ctx.secEtf; p.append(num(part, () => rsCalc(H, HIST[B], 63))); return; }
        const kk = linkable[r]; if (!kk) { p.append(part); return; }
        p.append(num(part, () => calcFor(H, kk, A)));
      });
      if (sn.refs.includes("macdHist")) p.append(" ", num("(MACD values)", () => calcFor(H, "macdHist", A)));
      host.append(p);
    });
  }

  /* ---------- signal scorecard ---------- */
  async function signalsTab(body, H) {
    const host = el("div", { class: "muted", style: "padding:20px" }, "Running the backtest…");
    body.append(host);
    const [A, SG] = await Promise.all([analysis(H), loadSignals()]);
    host.remove();
    const hzs = [5, 20, 60];
    if (!hzs.includes(TX.hz)) TX.hz = 20;
    const out = el("div");
    const draw = () => {
      out.innerHTML = "";
      const hz = TX.hz, bt = A.backtest, base = bt.base[hz], sb = SG?.base?.[hz]?.all;
      const t = el("table", { class: "t tx-sc" });
      t.append(el("thead", {}, el("tr", {}, el("th", {}, "Signal"), el("th", { class: "r" }, "Events"), el("th", { class: "r" }, `Avg ${hz}d`), el("th", { class: "r" }, "Median"), el("th", { class: "r" }, "Up share"), el("th", { class: "r" }, "Vs all days, 95% CI"),
        el("th", { class: "r", style: "border-left:1px solid var(--line)" }, "S&P 500"), el("th", { class: "r" }, `Avg ${hz}d`), el("th", { class: "r" }, "Up share"), el("th", { class: "r" }, "Vs all days, 95% CI"))));
      const tb = el("tbody");
      const ciTxt = (ci, bm) => ci ? `${pp(ci[0] - bm)} to ${pp(ci[1] - bm)}` : "–";
      const verdict = (ci, bm) => !ci ? null : ci[0] - bm > 0 ? el("span", { class: "chip up", title: "The interval lies above the all-days average" }, "above") : ci[1] - bm < 0 ? el("span", { class: "chip down", title: "The interval lies below the all-days average" }, "below") : el("span", { class: "chip", title: "The interval includes the all-days average" }, "no clear difference");
      tb.append(el("tr", { style: "background:var(--sunk)" }, el("td", { class: "k" }, el("b", {}, "All days (base rate)"), el("div", { class: "sub" }, `${H.sym}, from bar 253`)), el("td", { class: "r num" }, num(base.n.toLocaleString(), () => baseCalc(H, hz, base))), el("td", { class: "r num" }, num(pp(base.mean, 2), () => baseCalc(H, hz, base))), el("td", { class: "r num" }, pp(base.median, 2)), el("td", { class: "r num" }, fpct(base.hit, 0)), el("td", {}),
        el("td", { class: "r num", style: "border-left:1px solid var(--line)" }, sb ? num(sb.n.toLocaleString(), () => pooledCalc(SG, null, hz)) : "–"), el("td", { class: "r num" }, sb ? num(pp(sb.mean, 2), () => pooledCalc(SG, null, hz)) : "–"), el("td", { class: "r num" }, sb ? fpct(sb.hit, 0) : "–"), el("td", {})));
      const types = Object.keys(TA.SIGNALS);
      types.forEach(tp => {
        const sg = TA.SIGNALS[tp], s = bt.signals[tp]?.[hz], P = SG?.signals?.[tp]?.[hz];
        const mine = s && s.n ? [
          el("td", { class: "r num" }, num(`${s.n}`, () => eventsCalc(H, A, tp, hz)), s.n_eff !== s.n ? el("div", { class: "sub", title: "Events at least one horizon apart, used for the interval" }, `${s.n_eff} indep.`) : null, s.label ? el("div", {}, el("span", { class: "chip warn" }, s.label)) : null),
          el("td", { class: "r num " + cls(s.mean) }, num(pp(s.mean, 2), () => eventsCalc(H, A, tp, hz))), el("td", { class: "r num" }, pp(s.median, 2)), el("td", { class: "r num" }, fpct(s.hit, 0)),
          el("td", { class: "r num" }, s.ci && s.n_eff >= 10 ? [num(ciTxt(s.ci, base.mean), () => eventsCalc(H, A, tp, hz)), el("div", {}, verdict(s.ci, base.mean))] : el("span", { class: "faint" }, s.n_eff < 10 ? "too few to estimate" : "–"))]
          : [el("td", { class: "r faint" }, bt.signals[tp]?.[hz]?.pending ? `${bt.signals[tp][hz].pending} pending` : "0"), el("td"), el("td"), el("td"), el("td")];
        const pooled = P && P.n ? [el("td", { class: "r num", style: "border-left:1px solid var(--line)" }, num(P.n.toLocaleString(), () => pooledCalc(SG, tp, hz)), el("div", { class: "sub" }, `${SG.signals[tp].symbols} names · ${P.months} mo.`)),
          el("td", { class: "r num " + cls(P.mean) }, num(pp(P.mean, 2), () => pooledCalc(SG, tp, hz))), el("td", { class: "r num" }, fpct(P.hit, 0)),
          el("td", { class: "r num" }, P.excess_ci ? [num(`${pp(P.excess_ci[0])} to ${pp(P.excess_ci[1])}`, () => pooledCalc(SG, tp, hz)), el("div", {}, P.excess_ci[0] > 0 ? el("span", { class: "chip up" }, "above") : P.excess_ci[1] < 0 ? el("span", { class: "chip down" }, "below") : el("span", { class: "chip" }, "no clear difference"))] : "–")]
          : [el("td", { class: "r faint", style: "border-left:1px solid var(--line)" }, SG ? "0" : "not published yet"), el("td"), el("td"), el("td")];
        tb.append(el("tr", {}, el("td", { class: "k" }, el("span", { class: "tx-tag " + (sg.dir > 0 ? "u" : "d") }, sg.dir > 0 ? "▲" : "▼"), " ", sg.name, el("div", { class: "sub" }, sg.text)), ...mine, ...pooled));
      });
      t.append(tb);
      out.append(el("div", { class: "scroll" }, t));
    };
    body.append(el("section", { class: "panel" }, el("div", { class: "hd" }, el("h3", {}, `How ${H.sym} moved after each signal`), el("div", { class: "r" }, seg(hzs.map(h => [String(h), `${h} days`]), String(TX.hz), v => { TX.hz = +v; save(); draw(); }))),
      el("div", { class: "bd", style: "padding-top:10px" }, el("div", { class: "note", style: "margin-bottom:12px" }, "These are historical frequencies, not forecasts. Each row compares forward total returns after a signal with the average over all days. A signal \"means something\" here only if its interval sits clearly above or below the all-days figure, and even then markets change."), out),
      el("div", { class: "src" }, `${H.sym}: ${H.n.toLocaleString()} bars since ${fdate(H.d[0])}; forward returns include dividends. Intervals use the standard error over non-overlapping events (events closer than the horizon share returns); fewer than 10 is labelled too few, fewer than 30 a small sample. S&P 500 columns pool every current constituent${SG ? `, as of ${fdate(SG.as_of)}, with intervals clustered by calendar month` : ""}. Click a number for the events behind it.`)));
    if (SG) body.append(el("section", { class: "panel" }, el("div", { class: "hd" }, el("h3", {}, "Caveats for the S&P 500 statistics")), el("div", { class: "bd" }, ...(SG.caveats || []).map(x => el("p", { style: "margin:0 0 6px;font-size:13px" }, x)), el("p", { class: "faint", style: "margin:0;font-size:12px" }, SG.method))));
    draw();
  }
  function baseCalc(H, hz, base) {
    const L = H.n, rows = []; for (let i = 252; i + hz < L; i += Math.max(1, Math.floor((L - 252) / 400))) rows.push(i);
    return { title: `${H.sym} · all days, ${hz}-day forward return`, value: `${pp(base.mean, 2)} average`, sub: `${base.n.toLocaleString()} days · median ${pp(base.median, 2)} · up ${fpct(base.hit, 0)}`, formula: `Total return index (dividends reinvested) at day t+${hz} / at day t − 1, for every day from bar 253 that has ${hz} days after it.`, cols: [dcol(H), ["TR index", i => H.tr[i].toFixed(4)], [`+${hz}d`, i => H.d[i + hz]], [`TR +${hz}d`, i => H.tr[i + hz].toFixed(4)], ["Return", i => pp(H.tr[i + hz] / H.tr[i] - 1, 2)]], rows: rows.reverse(), H, rowsNote: rows.length < base.n ? `A sample of ${rows.length} evenly spaced days is listed; the average uses all ${base.n.toLocaleString()}.` : "" };
  }
  function eventsCalc(H, A, tp, hz) {
    const sg = TA.SIGNALS[tp], s = A.backtest.signals[tp][hz], ev = A.events.filter(e => e.type === tp && e.i >= 252);
    const rows = ev.map(e => e.i).reverse(), L = H.n; let lastI = -Infinity; const keep = new Set(); ev.forEach(e => { if (e.i + hz < L && e.i - lastI >= hz) { keep.add(e.i); lastI = e.i; } });
    return { title: `${H.sym} · ${sg.name}, ${hz}-day forward return`, value: `${pp(s.mean, 2)} average`, sub: `${s.n} events, ${s.n_eff} non-overlapping · median ${pp(s.median, 2)} · up ${fpct(s.hit, 0)}`, formula: `${sg.text}. Return = total return index ${hz} trading days later / on the signal day − 1. 95% interval: mean ± 1.96 × SD / √${s.n_eff}.`, cols: [["Signal day", i => H.d[i]], ccol(H), [`+${hz}d`, i => i + hz < L ? H.d[i + hz] : "pending"], ["Return", i => i + hz < L ? pp(H.tr[i + hz] / H.tr[i] - 1, 2) : "–"], ["Counted as independent", i => keep.has(i) ? "yes" : i + hz < L ? "overlaps" : ""]], rows, win: [...keep], H, rowsNote: "Shaded rows are the non-overlapping events used for the interval." };
  }
  function pooledCalc(SG, tp, hz) {
    const P = tp ? SG.signals[tp][hz] : SG.base[hz].all, sg = tp ? TA.SIGNALS[tp] : null;
    const by = tp ? P.by_period || {} : Object.fromEntries(Object.entries(SG.base[hz]).filter(([k]) => k !== "all"));
    const rows = Object.keys(by);
    return { eyebrow: "Calculated across the S&P 500", title: tp ? `S&P 500 · ${sg.name}, ${hz}-day forward return` : `S&P 500 · all days, ${hz}-day forward return`, value: `${pp(P.mean, 2)} average`, sub: tp ? `${P.n.toLocaleString()} events in ${P.months} months · ${SG.signals[tp].symbols} names · ${SG.signals[tp].first} to ${SG.signals[tp].last}` : `${P.n.toLocaleString()} symbol-days`,
      formula: tp ? `Mean forward total return over all events; excess = mean − all-days mean (${pp(SG.base[hz].all.mean, 2)}); 95% interval ${P.excess_ci ? `${pp(P.excess_ci[0], 2)} to ${pp(P.excess_ci[1], 2)}` : "–"} from a standard error clustered by calendar month.` : "Mean forward total return over every symbol-day from each name's 253rd bar.",
      notes: (SG.caveats || []).slice(0, 2), cols: [["Period", k => k], ["Events / days", k => (by[k].n || 0).toLocaleString()], ["Average", k => pp(by[k].mean, 2)], ["Vs all days", k => by[k].excess == null ? "–" : pp(by[k].excess, 2)], ["Up share", k => by[k].hit == null ? "–" : fpct(by[k].hit, 0)]], rows, rowsNote: "Split by period to show whether the pattern held in both halves of the sample.", link: { text: `${SG.method} Generated ${fdate(SG.generated.slice(0, 10))}.`, url: PUB + "tech/signals.json", label: "Published statistics" } };
  }

  /* ---------- volatility and risk ---------- */
  async function riskTab(body, H, ctx) {
    const wait = el("div", { class: "muted", style: "padding:20px" }, "Computing…"); body.append(wait);
    const bsyms = [["SPY", "S&P 500 (SPY)", false], [ctx.secEtf, `Sector (${ctx.secEtf})`, false], ["^TNX", "10Y yield (daily change)", true], ["CL=F", "WTI crude", false], ["UUP", "US dollar (UUP)", false]].filter(b => b[0] && b[0] !== H.sym);
    await Promise.all(bsyms.map(b => loadHist(b[0])));
    const A = await analysis(H);
    const bench = {}; bsyms.forEach(([s, , rate]) => { const B = HIST[s]; if (B) bench[s] = { v: H.d.map(d => { const j = B.idx.get(d); return j == null ? null : B.c[j]; }), rate }; });
    const R = await work("risk", { c: H.tr, bench, windows: [63, 252] });
    wait.remove();
    // cone
    const coneHost = el("div", { class: "bd" }), ddHost = el("div", { class: "bd" }), histHost = el("div", { class: "bd" }), rollHost = el("div", { class: "bd" }), seasHost = el("div", { class: "bd" });
    const g1 = el("div", { class: "grid g2" });
    const ct = el("table", { class: "t" }, el("thead", {}, el("tr", {}, el("th", {}, "Window"), el("th", { class: "r" }, "Now"), el("th", { class: "r" }, "Pctile"), el("th", { class: "r" }, "10th"), el("th", { class: "r" }, "Median"), el("th", { class: "r" }, "90th"), el("th", { class: "r" }, "Max"))));
    const ctb = el("tbody"); A.cone.forEach(z => ctb.append(el("tr", {}, el("td", {}, `${z.n}d`), el("td", { class: "r num" }, num(fpct(z.current), () => coneCalc(H, z))), el("td", { class: "r num" }, z.pct == null ? "–" : Math.round(z.pct * 100) + "th"), el("td", { class: "r num" }, fpct(z.p10)), el("td", { class: "r num" }, fpct(z.p50)), el("td", { class: "r num" }, fpct(z.p90)), el("td", { class: "r num" }, fpct(z.max))))); ct.append(ctb);
    g1.append(el("section", { class: "panel" }, el("div", { class: "hd" }, el("h3", {}, "Realized volatility cone")), coneHost, el("div", { class: "scroll", style: "padding:0 6px" }, ct), el("div", { class: "src" }, `Annualized close-to-close volatility for each window, every rolling window since ${fdate(H.d[0])}. Bands: 10th–90th and 25th–75th percentiles; dot: today.`)));
    const md = A.mdd;
    g1.append(el("section", { class: "panel" }, el("div", { class: "hd" }, el("h3", {}, "Drawdown from prior peak"), el("div", { class: "r faint", style: "font-size:12px" }, "max ", num(fpct(md.dd), () => ddCalc(H, md)), ` · ${fdate(H.d[md.peak])} to ${fdate(H.d[md.trough])}`, md.recovered != null ? ` · recovered ${fdate(H.d[md.recovered])}` : " · not recovered")), ddHost, el("div", { class: "src" }, "Total return index (dividends reinvested) relative to its running maximum.")));
    body.append(g1);
    const mo = A.moments;
    const g2 = el("div", { class: "grid g2" });
    const kv = (l, v) => el("div", { style: "display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid var(--line);font-size:13px" }, el("span", { class: "muted" }, l), el("span", { class: "num" }, v));
    g2.append(el("section", { class: "panel" }, el("div", { class: "hd" }, el("h3", {}, "Daily return distribution")), histHost, el("div", { class: "bd", style: "padding-top:0" },
      kv("Days", mo.n.toLocaleString()), kv("Mean / SD (daily)", `${pp(mo.mean, 3)} / ${fpct(mo.sd, 2)}`), kv("Skewness", mo.skew.toFixed(2)), kv("Excess kurtosis", mo.kurt.toFixed(2)),
      kv("1st / 99th percentile", `${pp(mo.p1, 2)} / ${pp(mo.p99, 2)}`), kv("Worst / best day", `${pp(mo.min, 2)} / ${pp(mo.max, 2)}`),
      kv("Days beyond 3 SD", `${mo.beyond3} (a normal distribution would give ${mo.normal3.toFixed(1)})`), kv("Days beyond 2 SD", `${mo.beyond2} (normal ${mo.normal2.toFixed(0)})`)),
      el("div", { class: "src" }, "Total-return daily changes. The curve is a normal distribution with the same mean and SD; excess kurtosis above 0 and more days beyond 3 SD than the normal count mean fatter tails.")));
    const rwSeg = seg([["63", "63 days"], ["252", "252 days"]], String(TX.riskWin), v => { TX.riskWin = +v; save(); drawRoll(); });
    const rmSeg = seg([["corr", "Correlation"], ["beta", "Beta"]], TX.riskMet, v => { TX.riskMet = v; save(); drawRoll(); });
    const rollTbl = el("div", { class: "bd", style: "padding-top:0" });
    g2.append(el("section", { class: "panel" }, el("div", { class: "hd" }, el("h3", {}, "Rolling sensitivity"), el("div", { class: "r" }, rmSeg, rwSeg)), rollHost, rollTbl, el("div", { class: "src" }, "Daily total returns of this symbol against daily returns of each benchmark (the 10-year yield uses its daily change in percentage points, so its beta is return per 1 pp). Dates matched exactly; a window needs 80% of days present.")));
    body.append(g2);
    const seasT = el("div", { class: "bd", style: "padding-top:0" });
    body.append(el("section", { class: "panel" }, el("div", { class: "hd" }, el("h3", {}, "Seasonality")), seasHost, seasT, el("div", { class: "src" }, `Month-end to month-end total returns by calendar month, and close-to-close returns by weekday, since ${fdate(H.d[0])}. Counts are small: about one observation per year per month.`)));
    requestAnimationFrame(() => { drawCone(coneHost, A.cone); drawDD(ddHost, H, A.dd, md); drawHist(histHost, A.hist, mo); drawRoll(); drawSeas(seasHost, seasT, H, A.seas); });
    function drawRoll() {
      const w = TX.riskWin, met = TX.riskMet, series = bsyms.filter(b => R[b[0]]).map(([s, lab], k) => ({ s, lab, col: [C("--c1"), C("--c3"), C("--c5"), C("--c2"), C("--c4")][k], arr: R[s][w][met] }));
      rollHost.innerHTML = ""; rollTbl.innerHTML = "";
      const W = rollHost.clientWidth || 600, Hh = 200, m = { t: 8, r: 40, b: 20, l: 6 };
      const from = Math.max(w, 0), xs = d3.scaleUtc().domain([new Date(H.d[from]), new Date(H.d[H.n - 1])]).range([m.l, W - m.r]);
      const vals = series.flatMap(z => z.arr.filter(v => v != null));
      const ys = d3.scaleLinear().domain(met === "corr" ? [-1, 1] : d3.extent(vals).map((v, k) => k ? Math.max(v, 0.2) : Math.min(v, -0.2))).nice().range([Hh - m.b, m.t]);
      const svg = d3.select(rollHost).append("svg").attr("width", W).attr("height", Hh);
      svg.append("g").attr("class", "gl").attr("transform", `translate(${W - m.r},0)`).call(d3.axisRight(ys).ticks(5).tickSize(-(W - m.l - m.r))).call(g => { g.select(".domain").remove(); g.selectAll("line").attr("stroke", C("--line")); });
      svg.append("g").attr("transform", `translate(0,${Hh - m.b})`).call(d3.axisBottom(xs).ticks(W < 500 ? 4 : 6).tickSizeOuter(0)).call(g => g.select(".domain").attr("stroke", C("--line")));
      series.forEach(z => svg.append("path").attr("d", d3.line().defined(i => z.arr[i] != null).x(i => xs(new Date(H.d[i]))).y(i => ys(z.arr[i]))(d3.range(from, H.n))).attr("fill", "none").attr("stroke", z.col).attr("stroke-width", 1.3));
      rollHost.append(el("div", { class: "legend" }, series.map(z => el("span", {}, el("i", { style: `background:${z.col}` }), z.lab))));
      series.forEach(z => { const cur = z.arr[H.n - 1]; const ok = z.arr.filter(v => v != null).sort((a, b) => a - b); rollTbl.append(kv(`${z.lab}, ${w}d ${met === "corr" ? "correlation" : "beta"}`, cur == null ? "–" : `${cur.toFixed(2)} (range ${ok[0]?.toFixed(2)} to ${ok.at(-1)?.toFixed(2)})`)); });
    }
  }
  function coneCalc(H, z) {
    const rv = TA.realizedVol(H.c, z.n), i = H.n - 1, lr = TA.logRets(H.c);
    return { title: `${H.sym} · ${z.n}-day realized volatility`, value: fpct(z.current), sub: `${Math.round(z.pct * 100)}th percentile of ${z.count.toLocaleString()} windows · median ${fpct(z.p50)}`, formula: `SD of the last ${z.n} daily log returns × √252.`, cols: [dcol(H), ccol(H), ["Log return", j => lr[j] == null ? "–" : (lr[j] * 100).toFixed(3) + "%"], [`${z.n}d vol`, j => rv[j] == null ? "–" : fpct(rv[j])]], rows: range(i - z.n - 5, i).reverse(), win: range(i - z.n + 1, i), hit: [i], H };
  }
  function ddCalc(H, md) {
    const rows = [md.peak, md.trough, ...(md.recovered != null ? [md.recovered] : []), H.n - 1];
    return { title: `${H.sym} · maximum drawdown`, value: fpct(md.dd), formula: `Trough total return index / prior peak − 1 = ${H.tr[md.trough].toFixed(4)} / ${H.tr[md.peak].toFixed(4)} − 1`, cols: [["Point", i => i === md.peak ? "Peak" : i === md.trough ? "Trough" : i === md.recovered ? "Recovered" : "Latest"], ["Date", i => H.d[i]], ccol(H), ["TR index", i => H.tr[i].toFixed(4)]], rows: [...new Set(rows)], hit: [md.trough], H };
  }
  function drawCone(host, cone) {
    const W = host.clientWidth || 500, Hh = 200, m = { t: 8, r: 44, b: 22, l: 8 };
    const x = d3.scalePoint().domain(cone.map(z => z.n)).range([m.l + 20, W - m.r - 10]);
    const y = d3.scaleLinear().domain([0, d3.max(cone, z => Math.min(z.max, z.p90 * 1.6))]).nice().range([Hh - m.b, m.t]);
    const svg = d3.select(host).append("svg").attr("width", W).attr("height", Hh);
    svg.append("g").attr("class", "gl").attr("transform", `translate(${W - m.r},0)`).call(d3.axisRight(y).ticks(5).tickSize(-(W - m.l - m.r)).tickFormat(d3.format(".0%"))).call(g => { g.select(".domain").remove(); g.selectAll("line").attr("stroke", C("--line")); });
    svg.append("g").attr("transform", `translate(0,${Hh - m.b})`).call(d3.axisBottom(x).tickFormat(v => v + "d").tickSizeOuter(0)).call(g => g.select(".domain").attr("stroke", C("--line")));
    const area = (a, b, op) => svg.append("path").attr("d", d3.area().x(z => x(z.n)).y0(z => y(z[a])).y1(z => y(z[b]))(cone)).attr("fill", C("--c1")).attr("opacity", op);
    area("p10", "p90", .1); area("p25", "p75", .15);
    svg.append("path").attr("d", d3.line().x(z => x(z.n)).y(z => y(z.p50))(cone)).attr("fill", "none").attr("stroke", C("--c1")).attr("stroke-dasharray", "3 3");
    svg.append("path").attr("d", d3.line().defined(z => z.current != null).x(z => x(z.n)).y(z => y(z.current))(cone)).attr("fill", "none").attr("stroke", C("--c5")).attr("stroke-width", 1.6);
    svg.selectAll(null).data(cone.filter(z => z.current != null)).join("circle").attr("cx", z => x(z.n)).attr("cy", z => y(z.current)).attr("r", 4).attr("fill", C("--c5"));
  }
  function drawDD(host, H, dd, md) {
    const W = host.clientWidth || 500, Hh = 200, m = { t: 8, r: 44, b: 22, l: 8 };
    const x = d3.scaleUtc().domain([new Date(H.d[0]), new Date(H.d[H.n - 1])]).range([m.l, W - m.r]);
    const y = d3.scaleLinear().domain([Math.min(-0.05, d3.min(dd)), 0]).nice().range([Hh - m.b, m.t]);
    const svg = d3.select(host).append("svg").attr("width", W).attr("height", Hh);
    svg.append("g").attr("class", "gl").attr("transform", `translate(${W - m.r},0)`).call(d3.axisRight(y).ticks(5).tickSize(-(W - m.l - m.r)).tickFormat(d3.format(".0%"))).call(g => { g.select(".domain").remove(); g.selectAll("line").attr("stroke", C("--line")); });
    svg.append("g").attr("transform", `translate(0,${Hh - m.b})`).call(d3.axisBottom(x).ticks(W < 500 ? 4 : 6).tickSizeOuter(0)).call(g => g.select(".domain").attr("stroke", C("--line")));
    const step = Math.max(1, Math.floor(H.n / 1200)), idx = d3.range(0, H.n, step); if (idx.at(-1) !== H.n - 1) idx.push(H.n - 1);
    svg.append("path").attr("d", d3.area().x(i => x(new Date(H.d[i]))).y0(y(0)).y1(i => y(dd[i]))(idx)).attr("fill", C("--down")).attr("opacity", .25);
    svg.append("path").attr("d", d3.line().x(i => x(new Date(H.d[i]))).y(i => y(dd[i]))(idx)).attr("fill", "none").attr("stroke", C("--down")).attr("stroke-width", 1);
    svg.append("circle").attr("cx", x(new Date(H.d[md.trough]))).attr("cy", y(md.dd)).attr("r", 3.5).attr("fill", C("--down"));
  }
  function drawHist(host, hist, mo) {
    const W = host.clientWidth || 500, Hh = 180, m = { t: 8, r: 10, b: 22, l: 8 };
    const x = d3.scaleLinear().domain([hist[0].x0, hist.at(-1).x1]).range([m.l, W - m.r]);
    const tot = d3.sum(hist, b => b.n), bw = hist[0].x1 - hist[0].x0;
    const pdf = v => tot * bw * Math.exp(-0.5 * ((v - mo.mean) / mo.sd) ** 2) / (mo.sd * Math.sqrt(2 * Math.PI));
    const y = d3.scaleLinear().domain([0, Math.max(d3.max(hist, b => b.n), pdf(mo.mean))]).range([Hh - m.b, m.t]);
    const svg = d3.select(host).append("svg").attr("width", W).attr("height", Hh);
    svg.append("g").attr("transform", `translate(0,${Hh - m.b})`).call(d3.axisBottom(x).ticks(7).tickFormat(d3.format(".0%")).tickSizeOuter(0)).call(g => g.select(".domain").attr("stroke", C("--line")));
    svg.selectAll(null).data(hist).join("rect").attr("x", b => x(b.x0) + 0.5).attr("width", b => Math.max(0.5, x(b.x1) - x(b.x0) - 1)).attr("y", b => y(b.n)).attr("height", b => y(0) - y(b.n)).attr("fill", b => b.x0 >= 0 ? C("--up") : C("--down")).attr("opacity", .45)
      .on("mousemove", (ev, b) => showTip(ev, `${pp(b.x0, 2)} to ${pp(b.x1, 2)}<br>${b.n} days`)).on("mouseleave", hideTip);
    const xs = d3.range(hist[0].x0, hist.at(-1).x1, bw / 4);
    svg.append("path").attr("d", d3.line().x(v => x(v)).y(v => y(pdf(v)))(xs)).attr("fill", "none").attr("stroke", C("--ink")).attr("stroke-width", 1.2).attr("stroke-dasharray", "4 3");
  }
  function drawSeas(host, tblHost, H, se) {
    const W = host.clientWidth || 700, Hh = 170, m = { t: 14, r: 44, b: 22, l: 8 };
    const x = d3.scaleBand().domain(MONTHS3).range([m.l, W - m.r]).padding(.25);
    const vals = se.month.map(z => z.mean ?? 0), y = d3.scaleLinear().domain([Math.min(0, d3.min(vals)), Math.max(0, d3.max(vals))]).nice().range([Hh - m.b, m.t]);
    const svg = d3.select(host).append("svg").attr("width", W).attr("height", Hh);
    svg.append("g").attr("class", "gl").attr("transform", `translate(${W - m.r},0)`).call(d3.axisRight(y).ticks(4).tickSize(-(W - m.l - m.r)).tickFormat(d3.format(".1%"))).call(g => { g.select(".domain").remove(); g.selectAll("line").attr("stroke", C("--line")); });
    svg.append("g").attr("transform", `translate(0,${Hh - m.b})`).call(d3.axisBottom(x).tickSizeOuter(0)).call(g => g.select(".domain").attr("stroke", C("--line")));
    svg.selectAll(null).data(se.month.map((z, k) => ({ ...z, k }))).join("rect").attr("x", z => x(MONTHS3[z.k])).attr("width", x.bandwidth()).attr("y", z => y(Math.max(0, z.mean || 0))).attr("height", z => Math.abs(y(z.mean || 0) - y(0))).attr("fill", z => z.mean >= 0 ? C("--up") : C("--down")).attr("opacity", .6)
      .on("mousemove", (ev, z) => showTip(ev, `${MONTHS3[z.k]}: average ${pp(z.mean, 2)}, median ${pp(z.median, 2)}<br>up in ${z.hit != null ? Math.round(z.hit * z.n) : 0} of ${z.n} years`)).on("mouseleave", hideTip);
    svg.selectAll(null).data(se.month.map((z, k) => ({ ...z, k }))).join("text").attr("x", z => x(MONTHS3[z.k]) + x.bandwidth() / 2).attr("y", m.t - 3).attr("text-anchor", "middle").text(z => `n=${z.n}`).style("font-size", "9.5px");
    const t = el("table", { class: "t" }, el("thead", {}, el("tr", {}, el("th", {}), ...MONTHS3.map(mn => el("th", { class: "r" }, mn)))));
    const tb = el("tbody");
    const monthCalc = k => ({ title: `${H.sym} · ${MONTHS3[k]} returns by year`, value: pp(se.month[k].mean, 2) + " average", sub: `${se.month[k].n} years`, formula: "Total return index at the last trading day of the month / at the last trading day of the prior month − 1.", cols: [["Month", r => r[0]], ["Return", r => pp(r[1], 2)]].map(([l, f]) => [l, i => f(se.monthRows[i])]), rows: se.monthRows.map((r, i) => +r[0].slice(5, 7) - 1 === k ? i : -1).filter(i => i >= 0).reverse(), H });
    tb.append(el("tr", {}, el("td", { class: "muted" }, "Average"), ...se.month.map((z, k) => el("td", { class: "r num " + cls(z.mean) }, z.n ? num(pp(z.mean, 1), () => monthCalc(k)) : "–"))));
    tb.append(el("tr", {}, el("td", { class: "muted" }, "Up share"), ...se.month.map(z => el("td", { class: "r num" }, z.n ? fpct(z.hit, 0) : "–"))));
    t.append(tb);
    const dt = el("table", { class: "t", style: "margin-top:10px" }, el("thead", {}, el("tr", {}, el("th", {}, "Weekday"), ...["Mon", "Tue", "Wed", "Thu", "Fri"].map(w => el("th", { class: "r" }, w)))));
    const db = el("tbody");
    db.append(el("tr", {}, el("td", { class: "muted" }, "Average"), ...se.dow.map(z => el("td", { class: "r num " + cls(z.mean) }, pp(z.mean, 3)))));
    db.append(el("tr", {}, el("td", { class: "muted" }, "Up share · days"), ...se.dow.map(z => el("td", { class: "r num" }, `${fpct(z.hit, 0)} · ${z.n}`))));
    dt.append(db);
    tblHost.append(el("div", { class: "scroll" }, t), el("div", { class: "scroll" }, dt));
  }

  /* ---------- market breadth ---------- */
  async function marketTab(body) {
    const wait = el("div", { class: "muted", style: "padding:20px" }, "Loading market data…"); body.append(wait);
    [BREADTH, STATES] = await Promise.all([BREADTH || getJSON("tech/breadth.json").catch(() => null), STATES || getJSON("tech/states.json").catch(() => null)]);
    wait.remove();
    if (!BREADTH || !STATES) { body.append(el("div", { class: "note" }, "The market-wide files have not been published yet. They are rebuilt every weekday evening after the close.")); return; }
    const B = BREADTH, n = B.d.length, i = n - 1;
    const p50 = B.above50.map((v, k) => B.n50[k] ? v / B.n50[k] : null), p200 = B.above200.map((v, k) => B.n200[k] ? v / B.n200[k] : null), nhnl = B.new_high.map((v, k) => v - B.new_low[k]);
    let ad = 0; const adl = B.adv.map((v, k) => (ad += v - B.dec[k]));
    const bc = (lab, val, k) => ({ eyebrow: "Calculated across the S&P 500", title: `S&P 500 breadth · ${lab}`, value: val, sub: `${fdate(B.d[k])}`, formula: "Counts over current constituents with enough history on that date: above 50/200-day = close above its simple average; new high/low = high above / low below the prior 252-day range; advancing/declining = close vs prior close.", cols: [["Date", j => B.d[j]], ["Above 50d", j => `${B.above50[j]} / ${B.n50[j]}`], ["Above 200d", j => `${B.above200[j]} / ${B.n200[j]}`], ["New highs", j => B.new_high[j]], ["New lows", j => B.new_low[j]], ["Adv / dec", j => `${B.adv[j]} / ${B.dec[j]}`]], rows: range(n - 30, n - 1).reverse(), hit: [k], notes: [B.caveat], link: { text: B.source + ".", url: PUB + "tech/breadth.json", label: "Published counts" } });
    body.append(el("div", { class: "stats" }, ...[
      ["Above 50-day average", fpct(p50[i], 0), `${B.above50[i]} of ${B.n50[i]}`],
      ["Above 200-day average", fpct(p200[i], 0), `${B.above200[i]} of ${B.n200[i]}`],
      ["New 52-week highs / lows", `${B.new_high[i]} / ${B.new_low[i]}`, `net ${nhnl[i] >= 0 ? "+" : ""}${nhnl[i]}`],
      ["Advancing / declining", `${B.adv[i]} / ${B.dec[i]}`, fdate(B.d[i])],
    ].map(([l, v, d]) => el("div", {}, el("div", { class: "l" }, l), el("div", { class: "v num" }, num(v, () => bc(l, v, i))), el("div", { class: "faint", style: "font-size:11px" }, d)))));
    let rng = store.get("txBr", "2Y");
    const ch = el("div", { class: "bd" });
    const drawB = () => {
      ch.innerHTML = "";
      const back = { "1Y": 1, "2Y": 2, "5Y": 5, "10Y": 10, MAX: 99 }[rng], from = `${+B.d[i].slice(0, 4) - back}${B.d[i].slice(4)}`;
      const idx = d3.range(0, n).filter(k => B.d[k] >= from);
      const W = ch.clientWidth || 800, m = { l: 8, r: 46 }, h1 = 170, h2 = 90, h3 = 90, gap = 22, Ht = h1 + h2 + h3 + gap * 2 + 22;
      const x = d3.scaleUtc().domain([new Date(B.d[idx[0]]), new Date(B.d[i])]).range([m.l, W - m.r]);
      const svg = d3.select(ch).append("svg").attr("width", W).attr("height", Ht);
      const pane = (y0, h, dom, fmt, title) => { const ys = d3.scaleLinear().domain(dom).nice().range([y0 + h, y0 + 4]); svg.append("g").attr("class", "gl").attr("transform", `translate(${W - m.r},0)`).call(d3.axisRight(ys).ticks(4).tickSize(-(W - m.l - m.r)).tickFormat(fmt)).call(g => { g.select(".domain").remove(); g.selectAll("line").attr("stroke", C("--line")); }); svg.append("text").attr("x", m.l + 2).attr("y", y0 + 12).text(title).style("font-weight", 600); return ys; };
      const ln = (ys, arr, col) => svg.append("path").attr("d", d3.line().defined(k => arr[k] != null).x(k => x(new Date(B.d[k]))).y(k => ys(arr[k]))(idx)).attr("fill", "none").attr("stroke", col).attr("stroke-width", 1.3);
      const y1 = pane(0, h1, [0, 1], d3.format(".0%"), "Share above 50-day (blue) and 200-day (orange) average"); ln(y1, p50, C("--c1")); ln(y1, p200, C("--c3"));
      const y2 = pane(h1 + gap, h2, d3.extent(idx, k => nhnl[k]), d3.format("d"), "New 52-week highs minus lows");
      const bw = Math.max(1, (W - m.l - m.r) / idx.length);
      svg.append("g").selectAll("rect").data(idx).join("rect").attr("x", k => x(new Date(B.d[k])) - bw / 2).attr("width", bw).attr("y", k => y2(Math.max(0, nhnl[k]))).attr("height", k => Math.abs(y2(nhnl[k]) - y2(0))).attr("fill", k => nhnl[k] >= 0 ? C("--up") : C("--down")).attr("opacity", .6);
      const y3 = pane(h1 + h2 + gap * 2, h3, d3.extent(idx, k => adl[k]), d3.format("~s"), "Advance/decline line (cumulative)"); ln(y3, adl, C("--c2"));
      svg.append("g").attr("transform", `translate(0,${Ht - 20})`).call(d3.axisBottom(x).ticks(W < 500 ? 4 : 7).tickSizeOuter(0)).call(g => g.select(".domain").attr("stroke", C("--line")));
      const cross = svg.append("line").attr("y1", 0).attr("y2", Ht - 20).attr("stroke", C("--faint")).attr("stroke-dasharray", "2 2").style("display", "none");
      svg.append("rect").attr("x", m.l).attr("y", 0).attr("width", W - m.l - m.r).attr("height", Ht - 20).attr("fill", "transparent").on("mousemove", ev => { const [mx] = d3.pointer(ev); const t = x.invert(mx).toISOString().slice(0, 10); let k = d3.bisectLeft(B.d, t); k = Math.min(i, Math.max(idx[0], k)); cross.style("display", null).attr("x1", x(new Date(B.d[k]))).attr("x2", x(new Date(B.d[k]))); showTip(ev, `<b>${fdate(B.d[k])}</b><br>Above 50d ${fpct(p50[k], 0)} · 200d ${fpct(p200[k], 0)}<br>New highs ${B.new_high[k]} · lows ${B.new_low[k]}<br>Adv ${B.adv[k]} · dec ${B.dec[k]}<br><span style="opacity:.7">${B.n50[k]} names</span>`); }).on("mouseleave", () => { cross.style("display", "none"); hideTip(); }).on("click", ev => { const [mx] = d3.pointer(ev); const t = x.invert(mx).toISOString().slice(0, 10); const k = Math.min(i, d3.bisectLeft(B.d, t)); openCalc({ ...bc("daily counts", fdate(B.d[k]), k), rows: range(k - 15, Math.min(i, k + 15)).reverse() }); });
    };
    body.append(el("section", { class: "panel" }, el("div", { class: "hd" }, el("h3", {}, "S&P 500 breadth"), el("div", { class: "r" }, seg([["1Y", "1Y"], ["2Y", "2Y"], ["5Y", "5Y"], ["10Y", "10Y"], ["MAX", "Max"]], rng, v => { rng = v; store.set("txBr", v); drawB(); }))), ch, el("div", { class: "src" }, `${B.caveat} Click the chart for the daily counts. Updated ${fdate(B.generated.slice(0, 10))}.`)));
    requestAnimationFrame(drawB);
    // state table
    const rows = STATES.rows.map(r => ({ ...r, name: SHORT[r.t] || r.t, last: r.events?.at(-1) }));
    let q = "", sec = "", tr = "";
    const COLS = [["t", "Ticker", null], ["name", "Name", null], ["trend", "Trend", null], ["rsi", "RSI 14", v => v == null ? "–" : v.toFixed(0)], ["vs50", "vs 50d", pp], ["vs200", "vs 200d", pp], ["from_hi", "From 52w high", pp], ["squeeze", "Squeeze bars", v => v || ""], ["adx", "ADX", v => v == null ? "–" : v.toFixed(0)], ["r21", "1M", pp], ["r63", "3M", pp], ["rs63", "3M vs SPY", pp], ["vol21_pct", "Vol pctile", v => v == null ? "–" : Math.round(v * 100) + "th"], ["last", "Latest signal", v => v ? `${TA.SIGNALS[v[0]]?.name || v[0]} · ${v[1].slice(5)}` : ""]];
    const tblHost = el("div", { class: "scroll", style: "max-height:640px;overflow:auto" });
    const drawT = () => {
      const [sk, desc] = TX.mktSort;
      let rs = rows.filter(r => (!q || r.t.includes(q) || r.name.toUpperCase().includes(q)) && (!sec || r.sector === sec) && (!tr || r.trend === tr || (tr === "squeeze" && r.squeeze > 0)));
      const val = r => sk === "last" ? r.last?.[1] : r[sk];
      rs.sort((a, b) => { const x = val(a), y = val(b); if (x == null) return 1; if (y == null) return -1; return (x < y ? -1 : x > y ? 1 : 0) * (desc ? -1 : 1); });
      const t = el("table", { class: "t tx-tbl" }, el("thead", {}, el("tr", {}, COLS.map(([k, lab]) => el("th", { class: "s" + (k === "t" || k === "name" || k === "trend" || k === "last" ? "" : " r"), style: "position:sticky;top:0;background:var(--surface);z-index:1", onclick: () => { TX.mktSort = [k, TX.mktSort[0] === k ? !TX.mktSort[1] : true]; save(); drawT(); } }, lab, TX.mktSort[0] === k ? (TX.mktSort[1] ? " ↓" : " ↑") : "")))));
      const tb = el("tbody");
      rs.forEach(r => tb.append(el("tr", { class: "click", onclick: () => { location.hash = "#technicals-" + r.t; }, title: `Open the ${r.t} chart` }, COLS.map(([k, , f]) => {
        const v = k === "last" ? r.last : r[k];
        if (k === "t") return el("td", {}, el("b", { class: "tkc" }, IDX[r.t] ? miniLogo(r.t, 16) : null, r.t));
        if (k === "name") return el("td", { class: "muted", style: "max-width:160px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" }, r.name);
        if (k === "trend") return el("td", {}, v ? el("span", { class: "chip " + (v === "up" ? "up" : v === "down" ? "down" : "") }, v) : "–");
        return el("td", { class: (k === "last" ? "" : "r num ") + (["vs50", "vs200", "r21", "r63", "rs63", "from_hi"].includes(k) ? cls(v) : ""), style: k === "last" ? "font-size:12px;white-space:nowrap" : "" }, f(v));
      }))));
      t.append(tb); tblHost.innerHTML = ""; tblHost.append(t);
      cnt.textContent = `${rs.length} of ${rows.length}`;
    };
    const cnt = el("span", { class: "faint", style: "font-size:12px" });
    const qIn = el("input", { class: "tx-sym", placeholder: "Filter", style: "width:120px", oninput: e => { q = e.target.value.trim().toUpperCase(); drawT(); } });
    const secSel = el("select", { class: "btn", onchange: e => { sec = e.target.value; drawT(); } }, el("option", { value: "" }, "All sectors"), Object.keys(SECTOR_ETF).sort().map(s => el("option", { value: s }, s)));
    const trSel = el("select", { class: "btn", onchange: e => { tr = e.target.value; drawT(); } }, [["", "Any trend"], ["up", "Up trend"], ["down", "Down trend"], ["mixed", "Mixed"], ["squeeze", "In a squeeze"]].map(([v, l]) => el("option", { value: v }, l)));
    body.append(el("section", { class: "panel" }, el("div", { class: "hd" }, el("h3", {}, "Technical state of every S&P 500 name"), el("div", { class: "r" }, qIn, secSel, trSel, cnt)), tblHost,
      el("div", { class: "src" }, `As of ${fdate(STATES.as_of)}. Trend: up when close > 50-day > 200-day average, down when close < 50-day < 200-day. Returns include dividends. Click a row to open its chart, where each value links to its calculation.`)));
    drawT();
  }
})();
