// Technicals & Quant lab tabs. Registered as window.FU_QUANT and rendered by technicals.js, which owns the page header,
// the shared symbol and benchmark selectors and the hash (#technicals-SYM/tab[/BENCH]).
// Data: quant/t/{KEY}.json (per symbol, etl/quant.py), quant/peers/{T}.json, quant/cross.json, quant/methods.json,
// quant/read/{T}.json (optional AI read). Every number opens the shared drawer with its formula, window, sample and as-of date.
window.FU_QUANT = (function () {
  "use strict";
  const QCSS = `
.ql-tiles{display:grid;grid-template-columns:repeat(auto-fill,minmax(132px,1fr));border:1px solid var(--line);border-radius:10px;background:var(--surface);overflow:hidden}
.ql-tiles>div{padding:9px 12px;border-right:1px solid var(--line);border-bottom:1px solid var(--line);margin:0 -1px -1px 0;min-width:0}
.ql-tiles .l{font-size:11px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ql-tiles .v{font-size:15.5px;font-weight:600;margin-top:2px;font-variant-numeric:tabular-nums}
.ql-tiles .d{font-size:10.5px;color:var(--faint);margin-top:1px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ql table.t td,.ql table.t th{padding:5px 8px;font-size:12px}
.ql table.t td.num,.ql table.t td.r{font-variant-numeric:tabular-nums}
.ql .scroll{overflow:auto}
.ql .cap{font-size:11px;color:var(--faint);padding:6px 16px 0}
.ql-chart{position:relative}
.ql-chart svg{display:block}
.ql-chart svg text{fill:var(--muted);font-size:10.5px;font-family:var(--f-ui)}
.ql-sig{display:inline-block;font-size:10px;font-weight:700;padding:1px 5px;border-radius:4px;background:var(--sunk);color:var(--muted);white-space:nowrap}
.ql-sig.s{background:var(--accent-soft);color:var(--accent)} .ql-sig.n{background:var(--warn-soft);color:var(--warn)}
.ql-ai{border:1px solid var(--line);border-left:3px solid var(--accent);border-radius:10px;padding:10px 14px;background:var(--surface);font-size:13px;line-height:1.55}
.ql-ai .meta{font-size:11px;color:var(--faint);margin-top:4px}
.ql-heat td{text-align:center;font-size:11px;padding:4px 3px;min-width:44px;font-variant-numeric:tabular-nums}
.ql-heat th{font-size:11px;padding:4px 3px}
.ql-bar{height:6px;border-radius:3px;background:var(--sunk);position:relative;min-width:60px}
.ql-bar i{position:absolute;left:0;top:0;bottom:0;border-radius:3px;background:var(--c1)}
.ql tr.me td{background:var(--accent-soft)}
.ql-ctl{display:flex;gap:6px;align-items:center;flex-wrap:wrap}
.ql-ctl select{height:28px;font-size:12px}
.ql-warn{font-size:12px;color:var(--warn);background:var(--warn-soft);border-radius:8px;padding:8px 12px}
@media (max-width:860px){.ql-tiles{grid-template-columns:repeat(2,minmax(0,1fr))}.ql table.t td,.ql table.t th{padding:4px 6px;font-size:11.5px}}
`;
  if (!document.getElementById("ql-css")) document.head.append(el("style", { id: "ql-css" }, QCSS));

  const TABS = [["stats", "Statistics"], ["corr", "Correlation & factors"], ["events", "Events"], ["season", "Seasonality"], ["regimes", "Regimes & backtests"], ["cross", "Cross-section"]];
  const BENCH_TABS = new Set(["stats", "corr"]);
  const key = s => s.replace("^", "IDX_").replace("=", "_");
  const QC = {}, PEERS = {}, READS = {};
  let CROSS = null, METH = null;
  const loadQ = async s => QC[s] !== undefined ? QC[s] : (QC[s] = await getJSON(`quant/t/${key(s)}.json`).catch(() => null));
  const loadMeth = async () => METH || (METH = await getJSON("quant/methods.json").catch(() => ({})));
  const loadPeers = async s => PEERS[s] !== undefined ? PEERS[s] : (PEERS[s] = await getJSON(`quant/peers/${s}.json`).catch(() => null));
  const loadCross = async () => CROSS || (CROSS = await getJSON("quant/cross.json").catch(() => null));
  const loadRead = async s => READS[s] !== undefined ? READS[s] : (READS[s] = await getJSON(`quant/read/${s}.json`).catch(() => null));
  const C = n => css(n);
  const P = (v, d = 1) => fpct(v, d), S = (v, d = 1) => fsgn(v, d);
  const F = (v, d = 2) => v == null || !isFinite(v) ? "–" : fnum(v, d);
  const T = v => v == null || !isFinite(v) ? "–" : (v >= 0 ? "" : "−") + Math.abs(v).toFixed(2);
  const PV = v => v == null ? "–" : v < 0.001 ? "<0.001" : v.toFixed(3);
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const WD = ["Mon", "Tue", "Wed", "Thu", "Fri"];
  const ORD = v => { const n = Math.round(v * 100), t = n % 100; return n + (t >= 11 && t <= 13 ? "th" : ["th", "st", "nd", "rd"][n % 10] || "th"); };

  /* ---------- explanation drawer ---------- */
  // cfg: {k: methods key, title, value, sub, window, n, asof, notes, cols, rows, hit, file}
  function explain(env, cfg) {
    const M = METH || {}, Q = env.Q;
    const notes = [];
    if (cfg.window) notes.push(`Window: ${cfg.window}.`);
    if (cfg.n != null) notes.push(`Sample: ${typeof cfg.n === "number" ? cfg.n.toLocaleString() : cfg.n}.`);
    notes.push(`Data as of ${fdate(cfg.asof || Q?.as_of)}${Q?.generated ? `; computed ${fdate(Q.generated.slice(0, 10))}` : ""}.`);
    (cfg.notes || []).forEach(n => notes.push(n));
    FU_TX.openCalc({ eyebrow: "Quant lab · precomputed from stored daily bars", title: `${env.sym.replace("^", "")} · ${cfg.title}`, value: cfg.value, sub: cfg.sub, formula: cfg.formula || M[cfg.k] || "",
      notes, cols: cfg.cols, rows: cfg.rows, hit: cfg.hit, rowsNote: cfg.rowsNote,
      link: { text: `${Q?.bars_source || "Daily bars"}; risk-free: ${M.rf || "13-week T-bill"}.`, url: PUB + (cfg.file || `quant/t/${key(env.sym)}.json`), label: "Published numbers" } });
  }
  function N(env, text, cfg, klass = "") {
    const tip = [cfg.window, cfg.n != null ? `n = ${typeof cfg.n === "number" ? cfg.n.toLocaleString() : cfg.n}` : null, `as of ${cfg.asof || env.Q?.as_of || ""}`].filter(Boolean).join(" · ") + ". Click for the formula.";
    return el("span", { class: "tx-n " + klass, role: "button", tabindex: "0", title: tip, onclick: e => { e.stopPropagation(); explain(env, cfg); }, onkeydown: e => { if (e.key === "Enter") explain(env, cfg); } }, text);
  }
  const tile = (l, v, d) => el("div", {}, el("div", { class: "l", title: l }, l), el("div", { class: "v" }, v), el("div", { class: "d" }, d || " "));
  const panel2 = (title, right, ...kids) => el("section", { class: "panel ql" }, el("div", { class: "hd" }, el("h3", {}, title), right ? el("div", { class: "r" }, right) : null), ...kids);
  const src = t => el("div", { class: "src" }, t);
  const sigChip = s => s ? el("span", { class: "ql-sig " + (s === "significant" ? "s" : s === "nominal" ? "n" : ""), title: s === "significant" ? "p < 0.05 after adjusting for the number of comparisons" : s === "nominal" ? "p < 0.05 before adjustment only: treat as noise unless it has a reason" : s === "too few" ? "too few observations to test" : "not statistically meaningful at 5%" }, { significant: "sig.", nominal: "nominal", "not significant": "n.s.", "too few": "too few" }[s] || s) : null;
  const pSig = p => p == null ? null : p < 0.01 ? "significant" : p < 0.05 ? "nominal" : "not significant";
  const table = (head, rows, opts = {}) => {
    const t = el("table", { class: "t" + (opts.cls ? " " + opts.cls : "") }, el("thead", {}, el("tr", {}, head.map((h, k) => el("th", { class: k && !opts.left?.includes(k) ? "r" : "" }, h)))));
    const tb = el("tbody"); rows.forEach(r => tb.append(r)); t.append(tb);
    return el("div", { class: "scroll", style: opts.max ? `max-height:${opts.max}px` : "" }, t);
  };
  const tr = (cells, attrs = {}) => el("tr", attrs, cells.map((c, k) => c instanceof Node && c.tagName === "TD" ? c : el("td", { class: k ? "r num" : "" }, c)));
  const later = fn => requestAnimationFrame(() => requestAnimationFrame(fn));

  /* ---------- charts ---------- */
  function lineChart(host, o) {
    host.innerHTML = "";
    const W = host.clientWidth || 600, H = o.h || 200, m = { t: 10, r: 46, b: 22, l: 8 };
    const xs = o.d.map(s => new Date(s));
    const x = d3.scaleUtc().domain(d3.extent(xs)).range([m.l, W - m.r]);
    const vals = o.series.flatMap(s => s.v.filter(v => v != null && isFinite(v)));
    let dom = o.domain || d3.extent(vals);
    if (o.zero) dom = [Math.min(0, dom[0]), Math.max(0, dom[1])];
    const y = (o.log ? d3.scaleLog() : d3.scaleLinear()).domain(dom).range([H - m.b, m.t]);
    if (!o.log) y.nice();
    const svg = d3.select(host).append("svg").attr("width", W).attr("height", H);
    const ax = d3.axisRight(y);
    if (o.log) { const [a, b] = y.domain(), tv = []; for (let e = Math.floor(Math.log10(a)); e <= Math.ceil(Math.log10(b)); e++) [1, 2, 5].forEach(k => { const v = k * 10 ** e; if (v >= a && v <= b) tv.push(v); }); ax.tickValues(tv); } else ax.ticks(5);
    svg.append("g").attr("transform", `translate(${W - m.r},0)`).call(ax.tickSize(-(W - m.l - m.r)).tickFormat(o.fmt || null)).call(g => { g.select(".domain").remove(); g.selectAll("line").attr("stroke", C("--line")); });
    svg.append("g").attr("transform", `translate(0,${H - m.b})`).call(d3.axisBottom(x).ticks(W < 500 ? 4 : 7).tickSizeOuter(0)).call(g => g.select(".domain").attr("stroke", C("--line")));
    if (o.ref != null) svg.append("line").attr("x1", m.l).attr("x2", W - m.r).attr("y1", y(o.ref)).attr("y2", y(o.ref)).attr("stroke", C("--faint")).attr("stroke-dasharray", "3 3");
    (o.bands || []).forEach(b => svg.append("rect").attr("x", x(new Date(b[0]))).attr("width", Math.max(1, x(new Date(b[1])) - x(new Date(b[0])))).attr("y", m.t).attr("height", H - m.b - m.t).attr("fill", b[2]).attr("opacity", b[3] ?? .12));
    o.series.forEach(s => svg.append("path").attr("d", d3.line().defined((v, i) => v != null && isFinite(v) && (!o.log || v > 0)).x((v, i) => x(xs[i])).y(v => y(v))(s.v)).attr("fill", "none").attr("stroke", s.color).attr("stroke-width", s.w || 1.4).attr("stroke-dasharray", s.dash || null).attr("opacity", s.op ?? 1));
    const cross = svg.append("line").attr("y1", m.t).attr("y2", H - m.b).attr("stroke", C("--faint")).attr("stroke-dasharray", "2 2").style("display", "none");
    svg.append("rect").attr("x", m.l).attr("y", 0).attr("width", W - m.l - m.r).attr("height", H - m.b).attr("fill", "transparent")
      .on("mousemove", ev => { const [mx] = d3.pointer(ev); const t = x.invert(mx); let i = d3.bisectLeft(xs, t); i = Math.max(0, Math.min(xs.length - 1, i)); cross.style("display", null).attr("x1", x(xs[i])).attr("x2", x(xs[i])); showTip(ev, `<b>${fdate(o.d[i])}</b><br>` + o.series.map(s => `<span style="color:${s.color}">■</span> ${s.name}: ${s.v[i] == null ? "–" : (o.tipFmt || o.fmt || (v => v.toFixed(2)))(s.v[i])}`).join("<br>")); })
      .on("mouseleave", () => { cross.style("display", "none"); hideTip(); })
      .on("click", ev => { if (!o.onClick) return; const [mx] = d3.pointer(ev); let i = d3.bisectLeft(xs, x.invert(mx)); o.onClick(Math.max(0, Math.min(xs.length - 1, i))); });
    if (o.legend !== false && o.series.length > 1) host.append(el("div", { class: "legend", style: "padding:0 4px" }, o.series.map(s => el("span", {}, el("i", { style: `background:${s.color}` }), s.name))));
  }
  function barChart(host, o) {
    host.innerHTML = "";
    const W = host.clientWidth || 600, H = o.h || 180, m = { t: 16, r: 46, b: 22, l: 8 };
    const x = d3.scaleBand().domain(o.labels.map((_, i) => i)).range([m.l, W - m.r]).padding(o.pad ?? .25);
    const vals = o.values.filter(v => v != null);
    const y = d3.scaleLinear().domain([Math.min(0, d3.min(vals) ?? 0), Math.max(0, d3.max(vals) ?? 0)]).nice().range([H - m.b, m.t]);
    const svg = d3.select(host).append("svg").attr("width", W).attr("height", H);
    svg.append("g").attr("transform", `translate(${W - m.r},0)`).call(d3.axisRight(y).ticks(4).tickSize(-(W - m.l - m.r)).tickFormat(o.fmt || d3.format(".1%"))).call(g => { g.select(".domain").remove(); g.selectAll("line").attr("stroke", C("--line")); });
    const every = Math.max(1, Math.ceil(o.labels.length / Math.max(4, Math.floor(W / 46))));
    svg.append("g").attr("transform", `translate(0,${H - m.b})`).call(d3.axisBottom(x).tickFormat(i => i % every ? "" : o.labels[i]).tickSizeOuter(0)).call(g => g.select(".domain").attr("stroke", C("--line")));
    svg.selectAll(null).data(o.values.map((v, i) => ({ v, i }))).join("rect").attr("x", z => x(z.i)).attr("width", x.bandwidth()).attr("y", z => y(Math.max(0, z.v || 0))).attr("height", z => Math.abs(y(z.v || 0) - y(0)))
      .attr("fill", z => o.color ? o.color(z.v, z.i) : z.v >= 0 ? C("--up") : C("--down")).attr("opacity", z => o.opacity ? o.opacity(z.i) : .7).style("cursor", o.onClick ? "pointer" : null)
      .on("mousemove", (ev, z) => o.tip && showTip(ev, o.tip(z.i))).on("mouseleave", hideTip).on("click", (_, z) => o.onClick && o.onClick(z.i));
    if (o.top) svg.selectAll(null).data(o.values.map((v, i) => ({ v, i }))).join("text").attr("x", z => x(z.i) + x.bandwidth() / 2).attr("y", m.t - 4).attr("text-anchor", "middle").text(z => o.top(z.i)).style("font-size", "9.5px");
  }

  /* ---------- shared bits ---------- */
  function benchOf(env) {
    const Q = env.Q, b = env.bench;
    if (b && Q.corr.bench[b]) return { sym: b, data: Q.corr.bench[b], cap: Q.stats.capture[b] };
    if (b && env.custom) return { sym: b, data: env.custom, cap: env.custom.capture, custom: true };
    const first = Object.keys(Q.corr.bench)[0];
    return first ? { sym: first, data: Q.corr.bench[first], cap: Q.stats.capture[first] } : null;
  }
  async function customBench(env) {
    const b = env.bench;
    if (!b || env.Q.corr.bench[b] || b === env.sym) return null;
    const B = await FU_TX.loadHist(b);
    if (!B) return null;
    const H = env.H;
    return QL.benchStats({ d: H.d, tr: H.tr }, { d: B.d, tr: B.tr });
  }
  function aiRead(env, R) {
    if (!R || !R.sentences?.length) return null;
    return el("div", { class: "ql-ai" }, el("div", { class: "eyebrow", style: "margin-bottom:4px" }, "Quant read · AI-generated"), ...R.sentences.map(s => el("p", { style: "margin:0 0 4px" }, s.text, " ", ...s.refs.map(r => el("button", { class: "chip link acc", style: "height:18px;font-size:10.5px", title: R.facts[r]?.[1], onclick: () => FU_TX.openCalc({ eyebrow: "AI read · evidence", title: R.facts[r]?.[1] || r, value: R.facts[r]?.[0], notes: [`Fact id ${r}, taken from the computed statistics for ${env.sym} as of ${fdate(R.as_of)}.`] }) }, r)))),
      el("div", { class: "meta" }, `${R.label}. Model ${R.model.split("/").pop()}, generated ${fdate(R.generated.slice(0, 10))}. Not advice.`));
  }

  /* ================= Statistics ================= */
  async function statsTab(body, env) {
    const Q = env.Q, s = Q.stats, asof = Q.as_of;
    const R = await loadRead(env.sym);
    if (R) body.append(aiRead(env, R));
    const v = s.vol, p1 = s.perf["1y"] || {}, p3 = s.perf["3y"], dd0 = s.dd.top[0], var1 = s.var["1y"];
    const vt = n => v[n] ? N(env, P(v[n].now), { k: "vol", title: `${n}-day realized volatility`, value: P(v[n].now), sub: `${ORD(v[n].pct)} percentile of ${v[n].count.toLocaleString()} rolling windows since ${fdate(v[n].since)}`, window: `${n} trading days`, n: v[n].count,
      cols: [["Statistic", r => r[0]], ["Value", r => r[1]]].map(([l, f]) => [l, i => f(i)]), rows: [["Now", P(v[n].now)], ["Percentile vs own history", P(v[n].pct, 0)], ["10th pct", P(v[n].p10)], ["Median", P(v[n].p50)], ["90th pct", P(v[n].p90)], ["Min", P(v[n].min)], ["Max", P(v[n].max)]] }) : "–";
    body.append(el("div", { class: "ql-tiles" },
      tile("Realized vol 20d", vt("20"), v["20"] ? `${ORD(v["20"].pct)} pctile` : ""), tile("Realized vol 60d", vt("60"), v["60"] ? `${ORD(v["60"].pct)} pctile` : ""), tile("Realized vol 252d", vt("252"), v["252"] ? `${ORD(v["252"].pct)} pctile` : ""),
      tile("Sharpe 1y", p1.sharpe != null ? N(env, F(p1.sharpe), perfCfg(env, "1y")) : "–", p3 ? `3y ${F(p3.sharpe)}` : ""), tile("Sortino 1y", p1.sortino != null ? N(env, F(p1.sortino), perfCfg(env, "1y")) : "–", p3 ? `3y ${F(p3.sortino)}` : ""),
      tile("Max drawdown", dd0 ? N(env, P(dd0.depth), ddCfg(env)) : "–", dd0 ? `${dd0.peak.slice(0, 7)} → ${dd0.trough.slice(0, 7)}` : ""),
      tile("1-day VaR 95%, 1y", var1["95"][0] != null ? N(env, P(var1["95"][0], 2), varCfg(env)) : "–", `CVaR ${P(var1["95"][1], 2)}`),
      tile("Hurst (R/S)", s.hurst ? N(env, F(s.hurst.h), hurstCfg(env)) : "–", s.hurst ? `R² ${F(s.hurst.r2)}` : "")));
    if (Q.history && !/^long/.test(Q.history)) body.append(el("div", { class: "ql-warn" }, `${Q.bars.toLocaleString()} daily bars since ${fdate(Q.first)} for ${env.sym}: ${Q.history}. Statistics use the stored sample; windows longer than it are omitted.`));
    // distribution + vol
    const g1 = el("div", { class: "grid g2" });
    const hist = el("div", { class: "ql-chart bd" });
    const dRows = [["Days / weeks", d => d.n.toLocaleString()], ["Mean", d => S(d.mean, 3)], ["SD", d => P(d.sd, 2)], ["Skewness", d => F(d.skew)], ["Excess kurtosis", d => F(d.kurt)], ["1st pct", d => S(d.pct["1"], 2)], ["5th pct", d => S(d.pct["5"], 2)], ["25th pct", d => S(d.pct["25"], 2)], ["Median", d => S(d.pct["50"], 2)], ["75th pct", d => S(d.pct["75"], 2)], ["95th pct", d => S(d.pct["95"], 2)], ["99th pct", d => S(d.pct["99"], 2)], ["Worst", d => S(d.min, 2)], ["Best", d => S(d.max, 2)], ["Up share", d => P(d.up, 0)]];
    const dsets = [["daily", "Daily, all", "full history"], ["daily_1y", "Daily, 1y", "last 252 days"], ["weekly", "Weekly, all", "full history, Friday to Friday"]];
    const dt = table(["", ...dsets.map(x => x[1])], dRows.map(([l, f]) => tr([el("td", { class: "muted" }, l), ...dsets.map(([k, , w]) => el("td", { class: "r num" }, s[k] ? N(env, f(s[k]), { k: k, title: `${l} of ${k === "weekly" ? "weekly" : "daily"} returns`, value: f(s[k]), window: w, n: s[k].n }) : "–"))])));
    g1.append(panel2("Return distribution", null, hist, el("div", { class: "bd", style: "padding-top:0" }, dt),
      src(`Total-return daily changes; bars are daily return frequencies, the dashed curve a normal distribution with the same mean and SD. ${s.daily?.beyond3 != null ? `${s.daily.beyond3} days beyond 3 SD vs ${F(s.daily.normal3, 1)} expected under normality.` : ""}`)));
    const vch = el("div", { class: "ql-chart bd" });
    const vtab = table(["Window", "Now", "Pctile", "10th", "Median", "90th", "Max"], ["20", "60", "252"].filter(n => v[n]).map(n => tr([`${n}d`, vt(n), P(v[n].pct, 0), P(v[n].p10), P(v[n].p50), P(v[n].p90), P(v[n].max)])));
    const vov = s.vov ? el("div", { class: "faint", style: "font-size:12px;padding:8px 0 0" }, "Vol of vol (20d vol over the last 252 days): SD ", N(env, P(s.vov.sd), { k: "vov", title: "volatility of volatility", value: P(s.vov.sd), sub: `mean 20d vol ${P(s.vov.mean)}; relative ${F(s.vov.rel)}; SD of daily log changes, annualized ${F(s.vov.chg_sd_ann)}`, window: "last 252 days of the 20-day realized volatility", n: s.vov.n }), ` · relative to its mean ${F(s.vov.rel)} · daily log-change SD (annualized) ${F(s.vov.chg_sd_ann)}`) : null;
    g1.append(panel2("Realized volatility", null, vch, el("div", { class: "bd", style: "padding-top:0" }, vtab, vov), src("Annualized SD of daily log total returns; percentiles against every rolling window in the stored history. Chart: last ten years, every fifth day.")));
    body.append(g1);
    // drawdowns + performance
    const g2 = el("div", { class: "grid g2" });
    const ddc = el("div", { class: "ql-chart bd" });
    const ddt = table(["Peak", "Trough", "Recovered", "Depth", "To trough", "To recover"], s.dd.top.map((e, k) => tr([e.peak, e.trough, e.recovered || el("span", { class: "faint" }, "not yet"), N(env, P(e.depth), ddCfg(env, k), "down"), `${e.to_trough}d`, e.to_recover != null ? `${e.to_recover}d` : "–"])), { left: [1, 2] });
    g2.append(panel2("Drawdowns", el("span", { class: "faint", style: "font-size:12px" }, `current ${P(s.dd.current)}${s.dd.peak_date ? ` from the ${fdate(s.dd.peak_date)} peak` : ""}`), ddc, el("div", { class: "bd", style: "padding-top:0" }, ddt), src("Total return index vs its running peak. Durations in trading days.")));
    const perfT = table(["Window", "Ann. return", "Ann. vol", "Sharpe", "Sortino", "Avg rf"], ["1y", "3y", "5y", "full"].filter(k => s.perf[k]).map(k => { const x = s.perf[k]; return tr([k === "full" ? `since ${x.from.slice(0, 4)}` : k, N(env, S(x.ann_return), perfCfg(env, k)), P(x.ann_vol), N(env, F(x.sharpe), perfCfg(env, k)), N(env, F(x.sortino), perfCfg(env, k)), P(x.rf_ann, 2)]); }));
    const varT = table(["Window", "VaR 95%", "CVaR 95%", "VaR 99%", "CVaR 99%", "Days"], ["1y", "full"].map(k => { const x = s.var[k]; return tr([k === "full" ? "all" : "1y", N(env, P(x["95"][0], 2), varCfg(env, k)), P(x["95"][1], 2), N(env, P(x["99"][0], 2), varCfg(env, k)), P(x["99"][1], 2), x.n.toLocaleString()]); }));
    g2.append(panel2("Risk-adjusted return and tail risk", null, el("div", { class: "bd" }, perfT, el("div", { style: "height:10px" }), varT), src("Historical one-day VaR and expected shortfall (CVaR) as losses; no scaling to longer horizons. Excess returns over the 13-week Treasury bill.")));
    body.append(g2);
    // capture + dependence
    const g3 = el("div", { class: "grid g2" });
    const bo = benchOf(env);
    const capHost = el("div", { class: "bd" });
    const capFor = (bs, cap, custom) => table(["Window", "Up capture", "Down capture", "Ratio", "Up / down months"], ["3y", "5y", "full"].filter(k => cap?.[k]).map(k => { const c = cap[k]; return tr([k === "full" ? "all" : k, N(env, F(c.up), capCfg(env, bs, c, k, custom)), N(env, F(c.down), capCfg(env, bs, c, k, custom)), F(c.ratio), `${c.n_up} / ${c.n_down}`]); }));
    const benches = Object.keys(s.capture);
    capHost.append(...benches.map(b => [el("div", { class: "eyebrow", style: "margin:4px 0" }, `vs ${b}`), capFor(b, s.capture[b])]).flat());
    if (bo?.custom && bo.cap) capHost.append(el("div", { class: "eyebrow", style: "margin:10px 0 4px" }, `vs ${bo.sym} (custom, computed in your browser)`), capFor(bo.sym, bo.cap, true));
    g3.append(panel2("Up / down capture", null, capHost, src("Month-end total returns. Above 1 = the stock gained (lost) more than the benchmark in its up (down) months. A negative down capture means it rose on average in the benchmark's down months.")));
    const acfHost = el("div", { class: "ql-chart bd" });
    const dep = el("div", { class: "bd", style: "padding-top:0" });
    if (s.acf) dep.append(el("div", { style: "font-size:12.5px;margin-bottom:6px" }, "Ljung-Box Q(10) ", N(env, F(s.acf.lb_q), { k: "acf", title: "Ljung-Box Q(10)", value: F(s.acf.lb_q), sub: `p = ${PV(s.acf.lb_p)}`, window: `daily returns since ${fdate(s.acf.from)}`, n: s.acf.n, cols: [["Lag", i => i + 1], ["Autocorrelation", i => s.acf.rho[i].toFixed(4)], ["Outside ±band", i => Math.abs(s.acf.rho[i]) > s.acf.band ? "yes" : "no"]], rows: s.acf.rho.map((_, i) => i) }), `, p = ${PV(s.acf.lb_p)} `, sigChip(pSig(s.acf.lb_p))));
    if (s.vr?.length) dep.append(table(["Horizon q", "Variance ratio", "z* (robust)", "p", ""], s.vr.map(x => tr([`${x.q} days`, N(env, F(x.vr, 3), { k: "vr", title: `variance ratio VR(${x.q})`, value: F(x.vr, 3), sub: `z* = ${T(x.z)}, p = ${PV(x.p)}`, window: `daily log returns, last ${x.n} days`, n: x.n }), T(x.z), PV(x.p), el("td", {}, sigChip(pSig(x.p)))]))));
    if (s.hurst) dep.append(el("div", { style: "font-size:12.5px;margin-top:8px" }, "Hurst exponent (R/S) ", N(env, F(s.hurst.h, 3), hurstCfg(env)), ` · log-log fit R² ${F(s.hurst.r2, 3)} over ${s.hurst.points.length} window sizes, ${s.hurst.n.toLocaleString()} returns`));
    g3.append(panel2("Serial dependence", null, acfHost, dep, src("Autocorrelation of daily returns (bars) with the ±1.96/√n band; variance ratios above 1 mean trending, below 1 mean reverting. Labels: significant p < 0.01, nominal p < 0.05.")));
    body.append(g3);
    later(() => {
      if (s.hist) { const nb = s.hist.n.length, lab = d3.range(nb).map(i => s.hist.lo + i * s.hist.w), tot = d3.sum(s.hist.n), m = s.daily.mean, sd = s.daily.sd;
        barChart(hist, { labels: lab.map(x => (x * 100).toFixed(1) + "%"), values: s.hist.n, fmt: d3.format("~s"), pad: .08, h: 170, color: (v, i) => lab[i] >= 0 ? C("--up") : C("--down"), tip: i => `${S(lab[i], 1)} to ${S(lab[i] + s.hist.w, 1)}<br>${s.hist.n[i]} days`, opacity: () => .5 });
        const svg = d3.select(hist).select("svg"), W = hist.clientWidth, x0 = 8, x1 = W - 46, y0 = 170 - 22, ymax = d3.max(s.hist.n), sc = (170 - 22 - 16) / ymax;
        const pdf = xv => tot * s.hist.w * Math.exp(-0.5 * ((xv - m) / sd) ** 2) / (sd * Math.sqrt(2 * Math.PI));
        const xs = d3.range(0, nb + 1, .25).map(i => s.hist.lo + i * s.hist.w);
        svg.append("path").attr("d", d3.line().x(xv => x0 + (xv - s.hist.lo) / (s.hist.w * nb) * (x1 - x0)).y(xv => y0 - Math.min(ymax, pdf(xv)) * sc)(xs)).attr("fill", "none").attr("stroke", C("--ink")).attr("stroke-dasharray", "4 3").attr("stroke-width", 1.1); }
      lineChart(vch, { d: s.vol_series.d, series: [{ name: "20d", v: s.vol_series.v20, color: C("--c6"), w: 1 }, { name: "60d", v: s.vol_series.v60, color: C("--c1") }, { name: "252d", v: s.vol_series.v252, color: C("--c3") }], fmt: d3.format(".0%"), h: 190, zero: true });
      lineChart(ddc, { d: s.dd.series.d, series: [{ name: "Drawdown", v: s.dd.series.v, color: C("--down") }], fmt: d3.format(".0%"), h: 170, legend: false });
      if (s.acf) barChart(acfHost, { labels: s.acf.rho.map((_, i) => String(i + 1)), values: s.acf.rho, fmt: d3.format(".2f"), h: 140, color: v => Math.abs(v) > s.acf.band ? C("--c5") : C("--c6"), tip: i => `lag ${i + 1}: ${s.acf.rho[i].toFixed(4)}` });
      if (s.acf) { const svg = d3.select(acfHost).select("svg"); const y = d3.scaleLinear().domain([Math.min(0, d3.min(s.acf.rho)), Math.max(0, d3.max(s.acf.rho))]).nice().range([140 - 22, 16]); [s.acf.band, -s.acf.band].forEach(b => { if (b >= y.domain()[0] && b <= y.domain()[1]) svg.append("line").attr("x1", 8).attr("x2", acfHost.clientWidth - 46).attr("y1", y(b)).attr("y2", y(b)).attr("stroke", C("--faint")).attr("stroke-dasharray", "3 3"); }); }
    });
  }
  const perfCfg = (env, k) => { const x = env.Q.stats.perf[k]; return { k: "perf", title: `risk-adjusted return, ${k}`, value: `Sharpe ${F(x.sharpe)} · Sortino ${F(x.sortino)}`, window: k === "full" ? `since ${fdate(x.from)}` : `${k} from ${fdate(x.from)}`, n: x.n, cols: [["Statistic", r => r[0]], ["Value", r => r[1]]], rows: [["Annualized return (geometric)", S(x.ann_return)], ["Annualized volatility", P(x.ann_vol)], ["Annualized mean excess return", S(x.ann_excess)], ["Average risk-free rate", P(x.rf_ann, 2)], ["Sharpe", F(x.sharpe)], ["Sortino", F(x.sortino)]] }; };
  const ddCfg = (env, k = 0) => { const e = env.Q.stats.dd.top[k]; return { k: "dd", title: `drawdown #${k + 1}`, value: P(e.depth), sub: `${fdate(e.peak)} peak → ${fdate(e.trough)} trough${e.recovered ? ` → recovered ${fdate(e.recovered)}` : ", not recovered"}`, window: "full stored history", n: env.Q.bars, cols: [["Episode", e => `#${e.k + 1}`], ["Peak", e => e.peak], ["Trough", e => e.trough], ["Recovered", e => e.recovered || "–"], ["Depth", e => P(e.depth)], ["Length", e => `${e.length}d`]], rows: env.Q.stats.dd.top.map((x, i) => ({ ...x, k: i })), hit: [] }; };
  const varCfg = (env, k = "1y") => { const x = env.Q.stats.var[k]; return { k: "var", title: `historical VaR and CVaR, ${k === "full" ? "all days" : "last 252 days"}`, value: `VaR95 ${P(x["95"][0], 2)} · CVaR95 ${P(x["95"][1], 2)}`, sub: `VaR99 ${P(x["99"][0], 2)} · CVaR99 ${P(x["99"][1], 2)}`, window: k === "full" ? "all daily returns" : "last 252 daily returns", n: x.n }; };
  const hurstCfg = env => { const h = env.Q.stats.hurst; return { k: "hurst", title: "Hurst exponent (rescaled range)", value: F(h.h, 3), sub: `R² of the log-log fit ${F(h.r2, 3)}`, window: "all daily log returns", n: h.n, cols: [["Window (days)", p => p[0]], ["Average R/S", p => p[1].toFixed(3)], ["Blocks", p => p[2]], ["log window", p => Math.log(p[0]).toFixed(3)], ["log R/S", p => Math.log(p[1]).toFixed(3)]], rows: h.points }; };
  const capCfg = (env, bs, c, k, custom) => ({ k: "capture", title: `up/down capture vs ${bs}, ${k}`, value: `up ${F(c.up)} · down ${F(c.down)}`, sub: `${c.n_up} up and ${c.n_down} down benchmark months, ${c.from} to ${c.to}`, window: `month-end returns ${c.from} to ${c.to}`, n: c.n_up + c.n_down, notes: custom ? ["Custom benchmark: computed in your browser from the stored daily bars with the same formula."] : [] });

  /* ================= Correlation & factors ================= */
  async function corrTab(body, env) {
    const Q = env.Q, c = Q.corr, bo = benchOf(env);
    if (!bo) { body.append(el("div", { class: "note" }, "No benchmark applies to this symbol.")); }
    if (bo) {
      const b = bo.data;
      const bc = (lab, v, n, k) => N(env, F(v), { k: "bench", title: `${lab} vs ${bo.sym}`, value: F(v), window: n === "full" ? `all matched days since ${fdate(b.full.from)}` : `${n} trading days`, n: n === "full" ? b.full.n : n, notes: bo.custom ? ["Custom benchmark: computed in your browser from the stored daily bars."] : [] });
      body.append(el("div", { class: "ql-tiles" }, tile(`Correlation 63d`, bc("63-day correlation", b.c63, 63), bo.sym), tile("Beta 63d", bc("63-day beta", b.b63, 63), b.b63_range ? `range ${F(b.b63_range[0])}–${F(b.b63_range[1])}` : ""), tile("Correlation 252d", bc("252-day correlation", b.c252, 252), `R² ${F(b.r2_252)}`), tile("Beta 252d", bc("252-day beta", b.b252, 252), b.b252_range ? `range ${F(b.b252_range[0])}–${F(b.b252_range[1])}` : ""),
        tile("Beta, all history", bc("full-sample beta", b.full.beta, "full"), `corr ${F(b.full.corr)}`), tile("Up-day beta 1y", N(env, F(b.updown.beta_up), { k: "bench", title: `beta on ${bo.sym} up days`, value: F(b.updown.beta_up), window: "last 252 days, benchmark up days", n: b.updown.n_up }), `corr ${F(b.updown.corr_up)}`), tile("Down-day beta 1y", N(env, F(b.updown.beta_down), { k: "bench", title: `beta on ${bo.sym} down days`, value: F(b.updown.beta_down), window: "last 252 days, benchmark down days", n: b.updown.n_down }), `corr ${F(b.updown.corr_down)}`)));
    }
    let met = store.get("qlMet", "corr"), win = store.get("qlWin", "252");
    const rch = el("div", { class: "ql-chart bd" });
    const drawRoll = () => {
      const pal = [C("--c1"), C("--c3"), C("--c5")];
      const list = Object.entries(c.bench).map(([s, d]) => ({ s, d })); if (bo?.custom) list.push({ s: bo.sym + " (custom)", d: bo.data });
      if (!list.length) return;
      const base = list[0].d.roll.d;
      const series = list.map((z, k) => { const m = new Map(z.d.roll.d.map((d, i) => [d, z.d.roll[(met === "corr" ? "c" : "b") + win][i]])); return { name: z.s, v: base.map(d => m.get(d) ?? null), color: pal[k % 3], w: z.s === bo?.sym ? 1.8 : 1.2 }; });
      lineChart(rch, { d: base, series, fmt: d3.format(".2f"), h: 210, domain: met === "corr" ? [-1, 1] : null, ref: met === "corr" ? 0 : 1 });
    };
    body.append(panel2("Rolling correlation and beta", el("div", { class: "ql-ctl" }, seg([["corr", "Correlation"], ["beta", "Beta"]], met, v => { met = v; store.set("qlMet", v); drawRoll(); }), seg([["63", "63d"], ["252", "252d"]], win, v => { win = v; store.set("qlWin", v); drawRoll(); })), rch,
      src(`${(METH || {}).bench || ""} Last ten years, every fifth day.`)));
    // macro + factors
    const g = el("div", { class: "grid g2" });
    const mch = el("div", { class: "ql-chart bd" });
    const mac = Object.entries(c.macro || {});
    const mt = table(["Series", "Corr 252d", "Beta 252d", "Corr all", "Beta all"], mac.map(([s, m]) => tr([el("td", {}, el("div", {}, m.label), el("div", { class: "faint", style: "font-size:10.5px" }, m.transform)), N(env, F(m.c252), { k: "macro", title: `correlation with ${m.label}`, value: F(m.c252), sub: `beta ${F(m.b252, 3)} (${m.transform})`, window: "252 trading days", n: Math.min(252, m.n) }), F(m.b252, 3), N(env, F(m.c_full), { k: "macro", title: `full-sample correlation with ${m.label}`, value: F(m.c_full), sub: `beta ${F(m.b_full, 3)}`, window: `matched days since ${fdate(m.from)}`, n: m.n }), F(m.b_full, 3)])));
    g.append(panel2("Macro sensitivities", null, mch, el("div", { class: "bd", style: "padding-top:0" }, mt), src((METH || {}).macro || "")));
    const fm = c.factors;
    const fHost = el("div", { class: "bd" }), fch = el("div", { class: "ql-chart bd" });
    let fw = store.get("qlFw", "3y");
    const drawF = () => {
      fHost.innerHTML = "";
      if (!fm || !fm.windows || !Object.keys(fm.windows).length) { fHost.append(el("div", { class: "muted" }, fm?.note || "Factor data not available.")); return; }
      if (!fm.windows[fw]) fw = Object.keys(fm.windows).includes("3y") ? "3y" : Object.keys(fm.windows)[0];
      const w = fm.windows[fw];
      const names = { mkt_rf: "Market (Mkt−RF)", smb: "Size (SMB)", hml: "Value (HML)", rmw: "Profitability (RMW)", cma: "Investment (CMA)", mom: "Momentum (Mom)" };
      const fc = (f, lab) => ({ k: "factors", title: `${lab} loading, ${fw}`, value: F(w.coef[f], 3), sub: `Newey-West t ${T(w.t[f])} (OLS t ${T(w.t_ols[f])}), p ${PV(w.p[f])}`, window: `${fdate(w.from)} to ${fdate(w.to)}`, n: w.n, asof: fm.as_of, notes: [`Factor file as of ${fdate(fm.as_of)}, retrieved ${fdate((fm.retrieved || "").slice(0, 10))}. ${fm.source || ""}`] });
      fHost.append(el("div", { style: "font-size:12.5px;margin-bottom:8px;display:flex;gap:14px;flex-wrap:wrap" }, el("span", {}, "R² ", N(env, F(w.r2), { k: "factors", title: `six-factor R², ${fw}`, value: F(w.r2), sub: `adjusted ${F(w.adj_r2)}; residual daily SD ${P(w.resid_sd, 2)}`, window: `${fdate(w.from)} to ${fdate(w.to)}`, n: w.n, asof: fm.as_of })),
        el("span", {}, "Alpha (ann.) ", N(env, S(w.alpha_ann), { k: "factors", title: `annualized alpha, ${fw}`, value: S(w.alpha_ann), sub: `t ${T(w.alpha_t)}, p ${PV(w.alpha_p)}`, window: `${fdate(w.from)} to ${fdate(w.to)}`, n: w.n, asof: fm.as_of }), " ", sigChip(pSig(w.alpha_p))), el("span", { class: "faint" }, `${w.n.toLocaleString()} days to ${fdate(w.to)}`)));
      fHost.append(table(["Factor", "Loading", "t (NW)", "p", ""], Object.keys(names).map(f => tr([names[f], N(env, F(w.coef[f], 3), fc(f, names[f])), T(w.t[f]), PV(w.p[f]), el("td", {}, sigChip(pSig(w.p[f])))]))));
      if (fm.roll) lineChart(fch, { d: fm.roll.d, series: [["mkt_rf", "--c1"], ["smb", "--c2"], ["hml", "--c3"], ["rmw", "--c4"], ["cma", "--c6"], ["mom", "--c5"]].map(([f, col]) => ({ name: names[f], v: fm.roll[f], color: C(col), w: f === "mkt_rf" ? 1.8 : 1.1 })), fmt: d3.format(".1f"), h: 200, ref: 0 });
    };
    g.append(panel2("Fama-French 5 + momentum", fm?.windows ? seg(Object.keys(fm.windows).map(k => [k, k === "full" ? "All" : k]), fw, v => { fw = v; store.set("qlFw", v); drawF(); }) : null, fHost, fch,
      src(`Daily excess returns on the six factors; t-statistics with Newey-West (5 lags) standard errors. Chart: rolling 252-day loadings every 21 days. ${fm?.as_of ? `Factors through ${fdate(fm.as_of)} (the library updates monthly). ` : ""}Fama/French factors courtesy of Kenneth R. French's data library.`)));
    body.append(g);
    // peers
    const PE = Q.kind === "stock" ? await loadPeers(env.sym) : null;
    if (PE) {
      const g2 = el("div", { class: "grid g64" });
      const heat = el("div", { class: "bd" });
      const m = PE.matrix, col = v => v == null ? "transparent" : `color-mix(in srgb, ${C("--c1")} ${Math.round(Math.max(0, v) * 70)}%, transparent)`;
      const ht = el("table", { class: "ql-heat" }, el("thead", {}, el("tr", {}, el("th"), m.names.map(n => el("th", {}, el("a", { href: `#technicals-${n}/corr` }, n))))));
      const hb = el("tbody");
      m.names.forEach((a, i) => hb.append(el("tr", {}, el("th", { style: "text-align:left" }, a), m.c[i].map((v, j) => el("td", { style: `background:${col(v)}`, title: `${a} vs ${m.names[j]}: ${v == null ? "–" : v.toFixed(3)}` }, i === j ? "" : v == null ? "–" : v.toFixed(2))))));
      ht.append(hb);
      heat.append(el("div", { class: "scroll" }, ht));
      g2.append(panel2("Sector peer correlation matrix", null, heat, src(`${m.basis}; ${PE.window}. Sector median correlation ${F(PE.sector_median)}, all covered names ${F(PE.all_median)}.`)));
      const lst = (rows) => table(["Name", "Corr", "Sector"], rows.map(([s, v, sec]) => tr([el("td", {}, el("a", { href: `#technicals-${s}/corr` }, s), " ", el("span", { class: "faint", style: "font-size:11px" }, SHORT[s] || "")), N(env, F(v), { k: "peers", title: `correlation with ${s}`, value: F(v), window: PE.window, n: 252, asof: PE.as_of, file: `quant/peers/${env.sym}.json` }), el("td", { class: "faint", style: "font-size:11px;white-space:nowrap;max-width:120px;overflow:hidden;text-overflow:ellipsis" }, sec || "")]), { left: [2] }), { max: 300 });
      g2.append(panel2("Most and least correlated covered names", null, el("div", { class: "bd" }, el("div", { class: "eyebrow", style: "margin-bottom:4px" }, "Most correlated"), lst(PE.top.slice(0, 10)), el("div", { class: "eyebrow", style: "margin:10px 0 4px" }, "Least correlated"), lst(PE.bottom.slice(0, 10))), src(`${PE.n_names} covered companies, ${PE.window}.`)));
      body.append(g2);
    }
    later(() => {
      drawRoll(); drawF();
      const mr = c.macro_roll; if (mr?.d?.length) lineChart(mch, { d: mr.d, series: mac.map(([s, m], k) => ({ name: m.label, v: mr[s] || [], color: C(["--c1", "--c2", "--c3", "--c4", "--c5"][k % 5]) })), fmt: d3.format(".1f"), h: 190, domain: [-1, 1], ref: 0 });
    });
  }

  /* ================= Events ================= */
  const colIdx = cols => Object.fromEntries(cols.map((c, i) => [c, i]));
  function openEventFiling(env, r, I, extra) {
    const title = r[I.form] === "8-K" ? FU_TX.itemsText(r[I.items]) : `${r[I.form]} filing`;
    const rows = el("div", { class: "tx-calc", style: "padding-top:12px" }, el("div", { class: "calc-box" }, el("div", { class: "eyebrow" }, "Price reaction (precomputed)"),
      el("div", { class: "eq" }, `Accepted ${r[I.accepted] ? r[I.accepted].replace("T", " ").slice(0, 16) + " UTC" : "time not recorded"} · ${r[I.timing]} · reaction day ${r[I.day0]} (${r[I.w]}-day window)`),
      el("div", { class: "eq" }, `Move ${S(r[I.cc], 2)} · gap ${S(r[I.gap], 2)} · abnormal vs SPY ${S(r[I.abn], 2)} (β ${F(r[I.beta])}) · vs sector ${S(r[I.abn_sector], 2)} · ${F(r[I.sigma], 1)}σ`),
      el("div", { class: "eq" }, `Pre-run 5d ${S(r[I.pre5], 2)} · drift +5d ${S(r[I.a5], 2)} · +20d ${S(r[I.a20], 2)} (abnormal) · volume ${F(r[I.vol_ratio], 1)}× 20-day average`)));
    FU_TX.openFiling(env.sym, { form: r[I.form], filed: r[I.filed], accn: r[I.accession], cik: r[I.cik], doc: r[I.doc], title, detail: extra || `reaction ${S(r[I.abn], 2)} abnormal`, rows });
  }
  async function eventsTab(body, env) {
    const Q = env.Q, E = Q.events || {};
    if (Q.kind !== "stock") { body.append(el("div", { class: "note" }, "Filing events apply to companies. Macro release reactions for this symbol are below.")); }
    const ER = E.earnings;
    if (ER) {
      const I = colIdx(E.cols), st = ER.stats, rows = ER.rows;
      const d5 = st.drift5 || {}, d20 = st.drift20 || {};
      const ec = (title, val, extra = {}) => ({ k: "earnings", title, value: val, window: `${rows.length ? rows[0][I.day0] : ""} to ${rows.length ? rows.at(-1)[I.day0] : ""}`, n: st.n, notes: [`Basis: ${ER.basis}.`, `Reaction timing: ${Object.entries(ER.timing || {}).map(([k, v]) => `${v} ${k}`).join(", ")}.`, `Typical day: mean |abnormal| ${P(E.typical_abs_ar, 2)} over ${E.typical_n} days.`], cols: E.cols && [["Reaction day", r => r[I.day0]], ["Form", r => r[I.form]], ["Timing", r => r[I.timing]], ["Abnormal", r => S(r[I.abn], 2)], ["+20d drift", r => S(r[I.a20], 2)]], rows: rows.slice().reverse(), ...extra });
      body.append(el("div", { class: "ql-tiles" }, tile("Earnings events", N(env, String(st.n), ec("earnings events", String(st.n))), `${ER.n_8k} 8-K · ${ER.n_periodic} 10-Q/K`), tile("Avg |move|", N(env, P(st.mean_abs_cc, 1), ec("average absolute reaction", P(st.mean_abs_cc, 2))), `median ${P(st.median_abs_cc, 1)}`),
        tile("Avg |abnormal|", N(env, P(st.mean_abs_abn, 1), ec("average absolute abnormal reaction", P(st.mean_abs_abn, 2))), st.abs_ratio ? `${F(st.abs_ratio, 1)}× a typical day` : ""), tile("Abnormal up share", N(env, P(st.abn_up_share, 0), ec("share of positive abnormal reactions", P(st.abn_up_share, 0))), `mean ${S(st.mean_abn, 2)}`),
        tile("Avg σ move", N(env, F(st.mean_sigma, 1), ec("average reaction in standard deviations", F(st.mean_sigma, 2))), "vs pre-event residual SD"),
        tile("Drift +5d", d5.n ? N(env, S(d5.mean, 2), ec("+5 day abnormal drift", S(d5.mean, 2), { sub: `t ${T(d5.t)}, p ${PV(d5.p)}; positive ${P(d5.hit, 0)}; continuation ${P(d5.cont, 0)}` })) : "–", d5.n ? `hit ${P(d5.hit, 0)} · cont. ${P(d5.cont, 0)}` : ""),
        tile("Drift +20d", d20.n ? N(env, S(d20.mean, 2), ec("+20 day abnormal drift", S(d20.mean, 2), { sub: `t ${T(d20.t)}, p ${PV(d20.p)}; positive ${P(d20.hit, 0)}; continuation ${P(d20.cont, 0)}; signed ${S(d20.signed_mean, 2)} (t ${T(d20.signed_t)})` })) : "–", d20.n ? `hit ${P(d20.hit, 0)} · cont. ${P(d20.cont, 0)}` : ""),
        tile("Signed drift +20d", d20.n ? N(env, S(d20.signed_mean, 2), ec("signed +20 day drift (in the direction of the reaction)", S(d20.signed_mean, 2), { sub: `t ${T(d20.signed_t)}, p ${PV(d20.signed_p)}` })) : "–", d20.n ? el("span", {}, `t ${T(d20.signed_t)} `, sigChip(pSig(d20.signed_p))) : "")));
      const ch = el("div", { class: "ql-chart bd" });
      const evT = table(["Reaction day", "Filing", "Timing", "Move", "Gap", "Abnormal", "vs sector", "σ", "Pre 5d", "+5d", "+20d", "Volume"], rows.slice().reverse().map(r => el("tr", { class: "click", title: "Open the filing", onclick: () => openEventFiling(env, r, I) },
        el("td", { class: "num" }, r[I.day0]), el("td", {}, `${r[I.form]} ${r[I.filed].slice(5)}`), el("td", { class: "faint", style: "font-size:11px;white-space:nowrap" }, r[I.timing]), el("td", { class: "r num " + cls(r[I.cc]) }, S(r[I.cc], 1)), el("td", { class: "r num" }, S(r[I.gap], 1)),
        el("td", { class: "r num " + cls(r[I.abn]) }, S(r[I.abn], 1)), el("td", { class: "r num" }, S(r[I.abn_sector], 1)), el("td", { class: "r num" }, F(r[I.sigma], 1)), el("td", { class: "r num" }, S(r[I.pre5], 1)), el("td", { class: "r num " + cls(r[I.a5]) }, S(r[I.a5], 1)), el("td", { class: "r num " + cls(r[I.a20]) }, S(r[I.a20], 1)), el("td", { class: "r num" }, r[I.vol_ratio] ? F(r[I.vol_ratio], 1) + "×" : "–"))), { max: 420, left: [1, 2] });
      body.append(panel2("Earnings reactions", el("span", { class: "faint", style: "font-size:11.5px" }, "click a row to open the filing"), ch, el("div", { class: "bd", style: "padding-top:0" }, evT),
        src(`${ER.basis}. Reaction day from the EDGAR acceptance time (Eastern): before 09:30 or during the session = that day, 16:00 or later = next trading day; unknown time = 2-day window. Abnormal = return − β×SPY (β from days −260 to −11); drift = abnormal return after the window. ${E.source || ""}.`)));
      later(() => barChart(ch, { labels: rows.map(r => r[I.day0].slice(0, 7)), values: rows.map(r => r[I.abn]), h: 170, pad: .15, tip: i => `<b>${rows[i][I.day0]}</b> ${rows[i][I.form]}<br>abnormal ${S(rows[i][I.abn], 2)} · move ${S(rows[i][I.cc], 2)}<br>+20d drift ${S(rows[i][I.a20], 2)}`, onClick: i => openEventFiling(env, rows[i], I) }));
    }
    if (E.material) {
      const I = colIdx(E.cols), M = E.material;
      const g = el("div", { class: "grid g2" });
      const it = table(["Item", "Events", "Avg abnormal", "Avg |abnormal|", "× typical", "t", ""], M.items.map(x => el("tr", { class: "click", onclick: () => explain(env, { k: "material", title: `8-K Item ${x.item} (${x.name}) reactions`, value: `${x.n} events · mean |abnormal| ${P(x.mean_abs_abn, 2)}`, sub: `mean ${S(x.mean_abn, 2)}, t ${T(x.abn_t)}, p ${PV(x.abn_p)}`, n: x.n, window: "all 8-K filings with this item since 2008 (or the stored history)", cols: [["Reaction day", r => r[I.day0]], ["Items", r => r[I.items]], ["Abnormal", r => S(r[I.abn], 2)], ["Move", r => S(r[I.cc], 2)]], rows: M.rows.filter(r => r[I.items].split(",").includes(x.item)).reverse(), rowsNote: "Most recent 150 non-earnings 8-Ks are listed; open them from the list beside this panel." }) },
        el("td", {}, el("b", { class: "mono" }, x.item), " ", x.name), el("td", { class: "r num" }, x.n), el("td", { class: "r num " + cls(x.mean_abn) }, S(x.mean_abn, 2)), el("td", { class: "r num" }, P(x.mean_abs_abn, 2)), el("td", { class: "r num" }, F(x.abs_ratio, 1)), el("td", { class: "r num" }, T(x.abn_t)), el("td", {}, sigChip(x.n < 8 ? "too few" : pSig(x.abn_p))))), { max: 360 });
      g.append(panel2("8-K material events by item", null, el("div", { class: "bd" }, it), src(`${M.basis}. ${M.total} filings. Significance of the mean abnormal reaction (t test vs zero); ×typical compares absolute reactions with an ordinary day.`)));
      const rl = table(["Reaction day", "Items", "Timing", "Abnormal", "+5d"], M.rows.slice().reverse().slice(0, 80).map(r => el("tr", { class: "click", title: "Open the filing", onclick: () => openEventFiling(env, r, I, FU_TX.itemsText(r[I.items])) }, el("td", { class: "num" }, r[I.day0]), el("td", { style: "font-size:11.5px;max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap", title: FU_TX.itemsText(r[I.items]) }, FU_TX.itemsText(r[I.items])), el("td", { class: "faint", style: "font-size:11px;white-space:nowrap" }, r[I.timing]), el("td", { class: "r num " + cls(r[I.abn]) }, S(r[I.abn], 1)), el("td", { class: "r num" }, S(r[I.a5], 1)))), { max: 360, left: [1, 2] });
      g.append(panel2("Recent 8-K filings (non-earnings)", null, el("div", { class: "bd" }, rl), src("Click a row to open the filing in the viewer.")));
      body.append(g);
    }
    const MX = E.macro;
    if (MX) {
      const keys = ["CPI", "EMPSIT", "FOMC"].filter(k => MX[k]);
      const names = { CPI: "CPI release", EMPSIT: "Payrolls (Employment Situation)", FOMC: "FOMC statement" };
      const mi = colIdx(MX.cols);
      const mcfg = (k, title, val, extra = {}) => { const x = MX[k]; return { k: "macro_events", title: `${names[k]}: ${title}`, value: val, window: `${fdate(x.first)} to ${fdate(x.last)}, ${x.time}`, n: x.n, notes: [`Release dates: ${x.source_url}`], cols: [["Release", r => r[mi.release]], ["Reaction day", r => r[mi.day0]], [env.sym.replace("^", ""), r => S(r[mi.ret], 2)], ["SPY", r => S(r[mi.spy], 2)], ["Abnormal", r => S(r[mi.abn], 2)]], rows: x.rows.slice().reverse(), ...extra }; };
      const t = table(["Release", "Events", "Avg move", "SD", "Avg |move|", "vs other days", "p (|move|)", "Avg abnormal", "t", "Up share"], keys.map(k => { const x = MX[k]; return tr([el("td", {}, el("div", {}, names[k]), el("div", { class: "faint", style: "font-size:10.5px" }, x.time)), N(env, String(x.n), mcfg(k, "events", String(x.n))), N(env, S(x.mean, 2), mcfg(k, "average same-day move", S(x.mean, 2), { sub: `t ${T(x.t)}, p ${PV(x.p)}` })), P(x.sd, 2), N(env, P(x.mean_abs, 2), mcfg(k, "average absolute move", P(x.mean_abs, 2), { sub: `other days ${P(x.base_abs, 2)}; ratio ${F(x.abs_ratio)}; Welch t ${T(x.abs_diff_t)}, p ${PV(x.abs_diff_p)}` })), `${F(x.abs_ratio)}×`, el("td", { class: "r num" }, PV(x.abs_diff_p), " ", sigChip(pSig(x.abs_diff_p))), N(env, S(x.mean_abn, 2), mcfg(k, "average abnormal move", S(x.mean_abn, 2), { sub: `t ${T(x.abn_t)}, p ${PV(x.abn_p)}` })), T(x.abn_t), P(x.up_share, 0)]); }));
      body.append(panel2("Macro release reactions", null, el("div", { class: "bd" }, t), src(`${(METH || {}).macro_events || ""} Release dates: BLS news release archive (CPI, Employment Situation) and Federal Reserve FOMC calendars, cached in quant/calendar/macro.json.`)));
    }
  }

  /* ================= Seasonality ================= */
  async function seasonTab(body, env) {
    const s = env.Q.season;
    const mch = el("div", { class: "ql-chart bd" });
    const mcfg = (m, x) => ({ k: "season", title: `${MONTHS[m - 1]} returns`, value: `${S(x.mean, 2)} average`, sub: `median ${S(x.median, 2)}, up in ${Math.round(x.hit * x.n)} of ${x.n}; t ${T(x.t)} (p ${PV(x.p)}); vs other months ${S(x.vs_rest, 2)}, t ${T(x.vs_rest_t)}, p ${PV(x.vs_rest_p)}`, window: "month-end to month-end total returns", n: x.n, cols: [["Month", r => r[0]], ["Return", r => S(r[1], 2)]], rows: s.month_rows.filter(r => +r[0].slice(5, 7) === m).reverse() });
    const mt = table(["Month", "Years", "Mean", "Median", "Up share", "t vs 0", "vs other months", "p", ""], s.month.map(x => tr([MONTHS[x.m - 1], x.n, N(env, S(x.mean, 1), mcfg(x.m, x), cls(x.mean)), S(x.median, 1), P(x.hit, 0), T(x.t), S(x.vs_rest, 1), PV(x.vs_rest_p), el("td", {}, sigChip(x.sig))])), {});
    body.append(panel2("Month of year", null, mch, el("div", { class: "bd", style: "padding-top:0" }, mt), src("Bars: mean monthly total return; n above each bar. 'significant' needs p < 0.05/12 against the other months (Bonferroni); 'nominal' passes only unadjusted. With about one observation per year per month these are descriptive.")));
    const g = el("div", { class: "grid g2" });
    const dt = table(["Weekday", "Days", "Mean", "Up share", "vs other days", "t", "p", ""], s.dow.map(x => tr([WD[x.wd], x.n.toLocaleString(), N(env, S(x.mean, 3), { k: "season", title: `${WD[x.wd]} returns`, value: S(x.mean, 3), sub: `vs other weekdays ${S(x.vs_rest, 3)}, Welch t ${T(x.vs_rest_t)}, p ${PV(x.vs_rest_p)}`, window: "daily total returns, full history", n: x.n }, cls(x.mean)), P(x.hit, 0), S(x.vs_rest, 3), T(x.vs_rest_t), PV(x.vs_rest_p), el("td", {}, sigChip(x.sig))])));
    g.append(panel2("Day of week", null, el("div", { class: "bd" }, dt), src("Daily total returns by weekday against all other weekdays (Welch t test, Bonferroni for 5 comparisons).")));
    const eff = [["Turn of month", s.tom, "last trading day of a month and the first three of the next"], ["Pre-holiday", s.pre_holiday, "trading day before a weekday market closure"], ["Post-holiday", s.post_holiday, "trading day after a weekday market closure"]];
    const et = table(["Effect", "Days", "Mean", "Other days", "Diff", "t", "p", ""], eff.map(([l, x, def]) => tr([el("td", { title: def, style: "white-space:nowrap" }, l), x.n.toLocaleString(), N(env, S(x.mean, 3), { k: "season", title: l, value: S(x.mean, 3), sub: `other days ${S(x.mean_other, 3)}; difference ${S(x.diff, 3)}, Welch t ${T(x.t)}, p ${PV(x.p)}`, window: def, n: x.n, notes: x.dates ? [`Most recent: ${x.dates.join(", ")}.`] : [] }, cls(x.mean)), S(x.mean_other, 3), S(x.diff, 3), T(x.t), PV(x.p), el("td", {}, sigChip(x.sig))])));
    g.append(panel2("Calendar effects", null, el("div", { class: "bd" }, et), src("Turn of month = last trading day of a month and the first three of the next; pre/post-holiday = trading day before/after a weekday market closure (from SPY's trading calendar). Average daily total returns; Welch t test against ordinary days. Labels: sig. = significant after adjustment, n.s. = not significant.")));
    body.append(g);
    const ych = el("div", { class: "ql-chart bd" });
    body.append(panel2("Calendar-year total returns", null, ych, src("Year-end to year-end total return (first year from the first stored bar; the current year to date).")));
    later(() => {
      barChart(mch, { labels: MONTHS, values: s.month.map(x => x.mean), top: i => `n=${s.month[i].n}`, tip: i => `${MONTHS[i]}: mean ${S(s.month[i].mean, 2)}, median ${S(s.month[i].median, 2)}<br>t ${T(s.month[i].t)} · vs others p ${PV(s.month[i].vs_rest_p)} · ${s.month[i].sig}`, opacity: i => s.month[i].sig === "significant" ? .9 : s.month[i].sig === "nominal" ? .7 : .4, onClick: i => explain(env, mcfg(i + 1, s.month[i])) });
      barChart(ych, { labels: s.years.map(y => y[0] + (y[2] ? "*" : "")), values: s.years.map(y => y[1]), h: 160, tip: i => `${s.years[i][0]}: ${S(s.years[i][1], 1)}${s.years[i][2] ? " (partial, from first bar)" : ""} to ${s.years[i][3]}` });
    });
  }

  /* ================= Regimes & backtests ================= */
  async function regimesTab(body, env) {
    const Q = env.Q, rg = Q.regimes, bt = Q.backtests, cur = rg.current;
    const rc = (title, val, extra = {}) => ({ k: "regimes", title, value: val, window: "daily closes", n: Q.bars, ...extra });
    body.append(el("div", { class: "ql-tiles" }, tile("Trend regime", N(env, cur.trend || "–", rc("trend regime", cur.trend, { sub: `close ${F(cur.close)} · 50d ${F(cur.sma50)} · 200d ${F(cur.sma200)}` })), cur.trend_since ? `since ${fdate(cur.trend_since)}` : ""),
      tile("Volatility regime", N(env, cur.vol || "–", rc("volatility regime", cur.vol, { sub: `20d vol ${P(cur.v20)} at the ${ORD(cur.vol_pct || 0)} percentile of the trailing 756 days` })), cur.vol_since ? `since ${fdate(cur.vol_since)}` : ""), tile("20d realized vol", P(cur.v20), cur.vol_pct != null ? `${ORD(cur.vol_pct)} pctile, 3y` : ""), tile("vs 200-day", S(cur.close / cur.sma200 - 1), `50d vs 200d ${S(cur.sma50 / cur.sma200 - 1)}`)));
    const band = el("div", { class: "ql-chart bd" });
    const cell = (t, v) => { const x = rg.table.find(r => r.key === `${t}|${v}`); return x ? N(env, `${P(x.share, 0)} · ${S(x.fwd20, 1)}`, rc(`${t === "*" ? "all trends" : t + " trend"}, ${v === "*" ? "all volatility" : v + " volatility"}`, `${x.days.toLocaleString()} days`, { sub: `share ${P(x.share, 1)}; average forward 20-day total return ${S(x.fwd20, 2)}; positive ${P(x.hit, 0)}`, n: x.days })) : "–"; };
    const rt = table(["Trend \\ volatility", "Low", "Normal", "High", "All"], ["up", "transition", "down", "*"].map(t => tr([t === "*" ? "All" : t, ...["low", "normal", "high", "*"].map(v => cell(t, v))])));
    body.append(panel2("Regimes", el("div", { class: "legend" }, el("span", {}, el("i", { style: `background:${C("--up")}` }), "up"), el("span", {}, el("i", { style: `background:${C("--c6")}` }), "transition"), el("span", {}, el("i", { style: `background:${C("--down")}` }), "down"), el("span", {}, el("i", { style: `background:${C("--c1")}` }), "low vol"), el("span", {}, el("i", { style: `background:${C("--c3")}` }), "high vol")), band,
      el("div", { class: "bd", style: "padding-top:0" }, el("div", { class: "faint", style: "font-size:11.5px;margin-bottom:4px" }, "Each cell: share of days · average forward 20-day total return"), rt), src((METH || {}).regimes || "")));
    if (bt) {
      const names = { sma_cross: "50/200 SMA cross", rsi_mr: "RSI(14) mean reversion", breakout: "20-day breakout", bh: "Buy and hold" };
      const cols = { sma_cross: "--c1", rsi_mr: "--c4", breakout: "--c2", bh: "--c6" };
      const eq = el("div", { class: "ql-chart bd" });
      const bcfg = (k, title, val) => { const x = bt.rules[k]; return { k: "backtests", title: `${names[k]}: ${title}`, value: val, window: `${fdate(bt.start)} to ${fdate(bt.end)}`, n: `${bt.days.toLocaleString()} trading days, ${x.trades} closed trades`, notes: ["Historical simulation before costs, taxes and slippage. Not advice."], cols: x.last_trades.length ? [["Entry", t => t[0]], ["Exit", t => t[1]], ["Return", t => S(t[2], 2)]] : null, rows: x.last_trades.slice().reverse(), rowsNote: x.last_trades.length ? `Last ${x.last_trades.length} closed trades.${x.open ? ` Open since ${x.open.entry}: ${S(x.open.ret, 2)}.` : ""}` : "" }; };
      const t = table(["Rule", "CAGR", "vs B&H", "Vol", "Sharpe", "Max DD", "Hit rate", "Trades", "Exposure", "Turnover/yr", "Now"], Object.keys(names).filter(k => bt.rules[k]).map(k => { const x = bt.rules[k]; return tr([el("td", {}, el("span", { class: "tx-sw", style: `background:${C(cols[k])};width:12px` }), " ", names[k]), N(env, S(x.cagr, 1), bcfg(k, "CAGR", S(x.cagr, 2))), x.vs_bh_cagr == null ? "–" : S(x.vs_bh_cagr, 1), P(x.vol, 1), N(env, F(x.sharpe), bcfg(k, "Sharpe", F(x.sharpe))), N(env, P(x.maxdd, 1), bcfg(k, "maximum drawdown", P(x.maxdd, 2))), x.hit == null ? "–" : N(env, P(x.hit, 0), bcfg(k, "hit rate", P(x.hit, 0))), x.trades, P(x.exposure, 0), F(x.turnover, 1), el("td", {}, x.in_market ? el("span", { class: "chip up" }, "long") : el("span", { class: "chip" }, "cash"))]); }));
      body.append(panel2("Rule backtests", el("span", { class: "chip warn" }, "historical simulation · before costs · not advice"), eq, el("div", { class: "bd", style: "padding-top:0" }, t), src((METH || {}).backtests || "")));
      later(() => lineChart(eq, { d: bt.d, series: Object.keys(names).filter(k => bt.rules[k]).map(k => ({ name: names[k], v: bt.rules[k].curve, color: C(cols[k]), w: k === "bh" ? 1.2 : 1.5, dash: k === "bh" ? "4 3" : null })), log: true, fmt: v => v >= 10 ? d3.format(".0f")(v) + "×" : d3.format(".1f")(v) + "×", h: 230 }));
    }
    later(() => {
      const W = band.clientWidth || 600, H = 64, m = { l: 8, r: 46 };
      const all = [...rg.trend_runs, ...rg.vol_runs]; if (!all.length) return;
      const x = d3.scaleUtc().domain([new Date(d3.min(all, r => r[0])), new Date(d3.max(all, r => r[1]))]).range([m.l, W - m.r]);
      const svg = d3.select(band).append("svg").attr("width", W).attr("height", H);
      const tc = { up: C("--up"), down: C("--down"), transition: C("--c6") }, vc = { low: C("--c1"), normal: C("--line2"), high: C("--c3") };
      svg.append("text").attr("x", W - m.r + 4).attr("y", 15).text("trend");
      svg.append("text").attr("x", W - m.r + 4).attr("y", 39).text("vol");
      svg.selectAll(null).data(rg.trend_runs).join("rect").attr("x", r => x(new Date(r[0]))).attr("width", r => Math.max(1, x(new Date(r[1])) - x(new Date(r[0])) + 1)).attr("y", 4).attr("height", 18).attr("fill", r => tc[r[2]]).attr("opacity", .65).on("mousemove", (ev, r) => showTip(ev, `${r[2]} trend<br>${fdate(r[0])} to ${fdate(r[1])}`)).on("mouseleave", hideTip);
      svg.selectAll(null).data(rg.vol_runs).join("rect").attr("x", r => x(new Date(r[0]))).attr("width", r => Math.max(1, x(new Date(r[1])) - x(new Date(r[0])) + 1)).attr("y", 28).attr("height", 18).attr("fill", r => vc[r[2]]).attr("opacity", .75).on("mousemove", (ev, r) => showTip(ev, `${r[2]} volatility<br>${fdate(r[0])} to ${fdate(r[1])}`)).on("mouseleave", hideTip);
      svg.append("g").attr("transform", `translate(0,${H - 14})`).call(d3.axisBottom(x).ticks(W < 500 ? 4 : 8).tickSize(3).tickSizeOuter(0)).call(g => g.select(".domain").remove());
    });
  }

  /* ================= Cross-section ================= */
  const XM = { r21: ["1M return", S], r63: ["3M return", S], r126: ["6M return", S], r252: ["1Y return", S], mom12_1: ["12-1 momentum", S], vol60: ["60d volatility", P], beta: ["Beta vs SPY (252d)", v => F(v)], corr: ["Corr vs SPY (252d)", v => F(v)], beta_sector: ["Beta vs sector ETF", v => F(v)], rs_spy63: ["3M RS vs SPY", S], rs_spy252: ["1Y RS vs SPY", S], rs_sector63: ["3M RS vs sector", S], rs_sector252: ["1Y RS vs sector", S], from_hi: ["From 52w high", S], sharpe1y: ["Sharpe 1y", v => F(v)], maxdd: ["Max drawdown", P], hurst: ["Hurst", v => F(v)], ff_r2: ["6-factor R² (3y)", v => F(v)], earn_abs: ["Avg |earnings reaction|", P], avg_corr: ["Avg corr to covered names", v => F(v)], mcap: ["Market cap", v => v == null ? "–" : fmtMoney(v)] };
  async function crossTab(body, env) {
    const X = await loadCross();
    if (!X) { body.append(el("div", { class: "note" }, "The cross-section has not been published yet. It is rebuilt every weekday evening after the per-symbol runs.")); return; }
    const I = colIdx(X.cols), rows = X.rows.map(r => Object.fromEntries(X.cols.map((c, k) => [c, r[k]])));
    const me = rows.find(r => r.sym === env.sym);
    const sec = me?.sector || env.ctx.sector;
    const xcfg = (k, r) => ({ k: "cross", title: XM[k]?.[0] || k, value: XM[k] ? XM[k][1](r[k]) : String(r[k]), window: `as of ${X.as_of}`, n: `${rows.length} covered symbols`, asof: X.as_of, file: "quant/cross.json" });
    if (me) {
      const rk = X.ranks[env.sym] || {};
      const rkTxt = k => rk[k] ? `${rk[k][0]} of ${rk[k][1]} in sector` : "";
      body.append(el("div", { class: "ql-tiles" }, ...["rs_spy63", "rs_spy252", "rs_sector63", "rs_sector252", "mom12_1", "vol60", "beta", "avg_corr"].map(k => tile(XM[k][0], me[k] == null ? "–" : N(env, XM[k][1](me[k]), xcfg(k, me), cls(["vol60", "beta", "avg_corr"].includes(k) ? 0 : me[k])), rkTxt(k) || (k.startsWith("rs_sector") && !me.beta_sector ? "no sector ETF" : "")))));
      const keys = X.rank_keys.filter(k => rk[k]);
      if (keys.length) body.append(panel2(`Rank within ${sec}`, null, el("div", { class: "bd" }, table(["Measure", "Value", "Rank", "Percentile", ""], keys.map(k => { const [i, m] = rk[k], pct = m > 1 ? 1 - (i - 1) / (m - 1) : 1; return tr([XM[k][0], N(env, XM[k][1](me[k]), xcfg(k, me)), `${i} / ${m}`, P(pct, 0), el("td", { style: "width:30%" }, el("div", { class: "ql-bar" }, el("i", { style: `width:${Math.round(pct * 100)}%` })))]); }))),
        src("Rank 1 = highest momentum, 3M return, 1Y relative strength vs sector and Sharpe; lowest volatility and beta. Percentile: share of sector names ranked below.")));
    } else body.append(el("div", { class: "note" }, `${env.sym} is not in the cross-section (companies only, with enough history).`));
    // scatter
    let xk = store.get("qlX", "beta"), yk = store.get("qlY", "r252"), only = store.get("qlOnly", "all");
    const sel = (v, on) => { const s = el("select", { class: "btn", onchange: e => on(e.target.value) }, Object.keys(XM).filter(k => k !== "mcap").map(k => { const o = el("option", { value: k }, XM[k][0]); if (k === v) o.selected = true; return o; })); return s; };
    const sc = el("div", { class: "ql-chart bd" });
    const draw = () => {
      sc.innerHTML = "";
      const pts = rows.filter(r => r.kind === "stock" && r[xk] != null && r[yk] != null && (only === "all" || r.sector === sec));
      if (!pts.length) return;
      const W = sc.clientWidth || 700, H = Math.min(440, Math.max(300, W * .55)), m = { t: 10, r: 14, b: 34, l: 52 };
      const q = (a, p) => d3.quantile(a.slice().sort((x, y) => x - y), p);
      const xv = pts.map(p => p[xk]), yv = pts.map(p => p[yk]);
      const xd = [q(xv, .005), q(xv, .995)], yd = [q(yv, .005), q(yv, .995)];
      if (me && me[xk] != null) { xd[0] = Math.min(xd[0], me[xk]); xd[1] = Math.max(xd[1], me[xk]); }
      if (me && me[yk] != null) { yd[0] = Math.min(yd[0], me[yk]); yd[1] = Math.max(yd[1], me[yk]); }
      const x = d3.scaleLinear().domain(xd).nice().range([m.l, W - m.r]), y = d3.scaleLinear().domain(yd).nice().range([H - m.b, m.t]);
      const fx = v => XM[xk][1](v), fy = v => XM[yk][1](v);
      const svg = d3.select(sc).append("svg").attr("width", W).attr("height", H);
      svg.append("g").attr("transform", `translate(0,${H - m.b})`).call(d3.axisBottom(x).ticks(6).tickFormat(v => fx(v).replace(/\.0+%/, "%"))).call(g => g.select(".domain").attr("stroke", C("--line")));
      svg.append("g").attr("transform", `translate(${m.l},0)`).call(d3.axisLeft(y).ticks(6).tickSize(-(W - m.l - m.r)).tickFormat(v => fy(v).replace(/\.0+%/, "%"))).call(g => { g.select(".domain").remove(); g.selectAll("line").attr("stroke", C("--line")); });
      svg.append("text").attr("x", W - m.r).attr("y", H - 4).attr("text-anchor", "end").text(XM[xk][0]);
      svg.append("text").attr("x", m.l + 4).attr("y", m.t + 10).text(XM[yk][0]);
      const clip = (v, d) => Math.max(d[0], Math.min(d[1], v));
      const g = svg.append("g");
      const ordered = pts.slice().sort((a, b) => (a.sym === env.sym) - (b.sym === env.sym) || (a.sector === sec) - (b.sector === sec));
      g.selectAll(null).data(ordered).join("circle").attr("cx", p => x(clip(p[xk], x.domain()))).attr("cy", p => y(clip(p[yk], y.domain()))).attr("r", p => p.sym === env.sym ? 6 : 3)
        .attr("fill", p => p.sym === env.sym ? C("--c5") : p.sector === sec ? C("--c1") : C("--c6")).attr("opacity", p => p.sym === env.sym ? 1 : p.sector === sec ? .7 : .28).attr("stroke", p => p.sym === env.sym ? C("--ink") : null).style("cursor", "pointer")
        .on("mousemove", (ev, p) => showTip(ev, `<b>${p.sym}</b> ${SHORT[p.sym] || ""}<br>${p.sector || ""}<br>${XM[xk][0]}: ${fx(p[xk])}<br>${XM[yk][0]}: ${fy(p[yk])}`)).on("mouseleave", hideTip)
        .on("click", (_, p) => { location.hash = `#technicals-${p.sym}/cross`; });
      if (me && me[xk] != null && me[yk] != null) svg.append("text").attr("x", x(clip(me[xk], x.domain())) + 9).attr("y", y(clip(me[yk], y.domain())) + 4).text(env.sym).style("font-weight", 700).style("fill", C("--ink"));
      const r = d3.range(pts.length);
      const mx = d3.mean(xv), my = d3.mean(yv), sxy = d3.sum(r, i => (xv[i] - mx) * (yv[i] - my)), sxx = d3.sum(r, i => (xv[i] - mx) ** 2);
      const b = sxx ? sxy / sxx : 0, cc = sxy / Math.sqrt(sxx * d3.sum(r, i => (yv[i] - my) ** 2));
      const xs = x.domain();
      svg.append("line").attr("x1", x(xs[0])).attr("x2", x(xs[1])).attr("y1", y(clip(my + b * (xs[0] - mx), y.domain()))).attr("y2", y(clip(my + b * (xs[1] - mx), y.domain()))).attr("stroke", C("--faint")).attr("stroke-dasharray", "4 3");
      sc.append(el("div", { class: "faint", style: "font-size:11.5px;padding:2px 4px" }, `${pts.length} companies · cross-sectional correlation ${isFinite(cc) ? cc.toFixed(2) : "–"} · dashed line = OLS fit · axes clipped at the 0.5th / 99.5th percentiles · click a point to open it`));
    };
    body.append(panel2("Cross-sectional scatter", el("div", { class: "ql-ctl" }, sel(xk, v => { xk = v; store.set("qlX", v); draw(); }), el("span", { class: "faint" }, "vs"), sel(yk, v => { yk = v; store.set("qlY", v); draw(); }), seg([["all", "All"], ["sector", "Sector"]], only, v => { only = v; store.set("qlOnly", v); draw(); })), sc,
      el("div", { class: "legend", style: "padding:0 16px 8px" }, el("span", {}, el("i", { style: `background:${C("--c5")}` }), env.sym), el("span", {}, el("i", { style: `background:${C("--c1")}` }), sec || "sector"), el("span", {}, el("i", { style: `background:${C("--c6")}` }), "other covered companies")),
      src(`${(METH || {}).cross || ""} As of ${fdate(X.as_of)}.`)));
    // sector table
    let sk = store.get("qlSort", "mom12_1");
    const st = el("div", { class: "bd" });
    const drawT = () => {
      const list = rows.filter(r => r.kind === "stock" && r.sector === sec).sort((a, b) => (b[sk] ?? -Infinity) - (a[sk] ?? -Infinity));
      const ks = ["r63", "r252", "mom12_1", "rs_sector252", "vol60", "beta", "sharpe1y", "maxdd", "trend"];
      st.innerHTML = "";
      st.append(table(["Company", ...ks.map(k => k === "trend" ? "Trend" : XM[k][0])], list.map(r => el("tr", { class: "click" + (r.sym === env.sym ? " me" : ""), onclick: () => { location.hash = `#technicals-${r.sym}/cross`; } }, el("td", {}, el("b", { class: "mono" }, r.sym), " ", el("span", { class: "faint", style: "font-size:11px" }, SHORT[r.sym] || "")), ...ks.map(k => k === "trend" ? el("td", {}, r.trend ? el("span", { class: "chip " + (r.trend === "up" ? "up" : r.trend === "down" ? "down" : "") }, r.trend) : "–") : el("td", { class: "r num " + (["r63", "r252", "mom12_1", "rs_sector252"].includes(k) ? cls(r[k]) : "") }, XM[k][1](r[k]))))), { max: 480 }));
    };
    const sortSel = el("select", { class: "btn", onchange: e => { sk = e.target.value; store.set("qlSort", sk); drawT(); } }, ["mom12_1", "r63", "r252", "rs_sector252", "vol60", "beta", "sharpe1y", "maxdd"].map(k => { const o = el("option", { value: k }, "Sort: " + XM[k][0]); if (k === sk) o.selected = true; return o; }));
    if (sec) body.append(panel2(`${sec}: covered companies`, sortSel, st, src("Click a row to open that company in this tab.")));
    later(() => { draw(); if (sec) drawT(); });
  }

  /* ---------- entry ---------- */
  async function render(id, body, env) {
    const wait = el("div", { class: "muted", style: "padding:20px" }, "Loading precomputed statistics…");
    body.append(wait);
    const [Q] = await Promise.all([loadQ(env.sym), loadMeth()]);
    env.Q = Q;
    wait.remove();
    if (!Q && id !== "cross") { body.append(el("div", { class: "note" }, `Quant statistics for ${env.sym} have not been published yet. They are computed every weekday evening after the price update.`)); return; }
    if (Q && BENCH_TABS.has(id)) env.custom = await customBench(env).catch(() => null);
    const fn = { stats: statsTab, corr: corrTab, events: eventsTab, season: seasonTab, regimes: regimesTab, cross: crossTab }[id];
    if (!env.Q) env.Q = { as_of: null };
    await fn(body, env);
    if (Q) body.append(el("div", { class: "faint", style: "font-size:11px" }, `Precomputed ${fdate(Q.generated.slice(0, 10))} from ${Q.bars.toLocaleString()} daily bars (${fdate(Q.first)} to ${fdate(Q.as_of)}) in ${Q.bars_file}. `, el("a", { href: PUB + `quant/t/${key(env.sym)}.json`, target: "_blank", rel: "noopener" }, "Published JSON ↗")));
  }
  return { TABS, BENCH_TABS, render };
})();
