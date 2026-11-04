// Guidance. Registers window.FU_PAGES.guidance (the cross-company page, #guidance) and window.FU_COTABS.guidance (the
// company tab). Data: guidance/index.json and guidance/{T}.json, built by etl/guidance.py. Every guidance point opens the
// document it was read from at its page with the figure highlighted; every actual opens its XBRL fact, KPI or release.
window.FU_PAGES = window.FU_PAGES || {};
window.FU_COTABS = window.FU_COTABS || {};
(() => {
  "use strict";
  const G = { idx: null, co: {} };
  const loadIdx = () => G.idx ? Promise.resolve(G.idx) : getJSON("guidance/index.json").then(j => (G.idx = j));
  const loadCo = tk => G.co[tk] ? Promise.resolve(G.co[tk]) : getJSON(`guidance/${tk}.json`).then(j => (G.co[tk] = j));

  /* ---------- nav entry and icon ---------- */
  const sprite = document.querySelector("svg symbol#logo")?.parentNode;
  if (sprite && !document.getElementById("i-gd")) sprite.insertAdjacentHTML("beforeend", '<symbol id="i-gd" viewBox="0 0 24 24"><path d="M4 19h16"/><path d="M6 15l4-4 3 2 5-6"/><path d="M15 7h3v3"/><path d="M6 19v-2M10 19v-5M14 19v-3M18 19v-6" opacity=".45"/></symbol>');
  if (typeof PAGES !== "undefined" && !PAGES.find(p => p.id === "guidance")) {
    const at = PAGES.findIndex(p => p.id === "sectors");
    PAGES.splice(at >= 0 ? at + 1 : PAGES.length, 0, { id: "guidance", name: "Guidance", icon: "i-gd", key: "u" });
  }

  /* ---------- styles (app.css variables only) ---------- */
  if (!document.getElementById("gd-css")) document.head.append(el("style", { id: "gd-css" }, `
.gd-v{border:0;background:none;padding:0;font:inherit;color:inherit;cursor:pointer;font-variant-numeric:tabular-nums;text-decoration:underline dotted var(--faint);text-underline-offset:3px;text-align:inherit}
.gd-v:hover{color:var(--accent);text-decoration-color:var(--accent)}
.gd-v.up{color:var(--up)}.gd-v.down{color:var(--down)}
.gd-stats{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px}
.gd-stat{border:1px solid var(--line);border-radius:10px;padding:10px 12px;background:var(--surface);display:flex;flex-direction:column;gap:2px;min-width:0}
.gd-stat .l{font-size:11.5px;color:var(--muted)}
.gd-stat .v{font-size:18px;font-weight:600}
.gd-stat .s{font-size:11px;color:var(--faint)}
.gd-revs{display:flex;gap:4px;flex-wrap:wrap}
.gd-rev{display:inline-flex;align-items:center;gap:4px;height:22px;padding:0 7px;border-radius:999px;font-size:11.5px;font-weight:600;background:var(--sunk);color:var(--muted);border:0;cursor:pointer;white-space:nowrap;font-variant-numeric:tabular-nums}
.gd-rev:hover{outline:1px solid var(--accent)}
.gd-rev.up{background:var(--up-soft);color:var(--up)}.gd-rev.down{background:var(--down-soft);color:var(--down)}.gd-rev.acc{background:var(--accent-soft);color:var(--accent)}
.gd-tbl td,.gd-tbl th{white-space:nowrap}
.gd-tbl td.wrap{white-space:normal;min-width:180px}
.gd-chart svg{display:block;width:100%;overflow:visible}
.gd-chart text{fill:var(--muted);font-size:10.5px;font-family:var(--f-ui)}
.gd-chart .grid line{stroke:var(--line);stroke-dasharray:2 3}
.gd-chart .grid .domain,.gd-chart .ax .domain{stroke:var(--line2)}
.gd-chart .hit{cursor:pointer}
.gd-legend{display:flex;gap:10px;flex-wrap:wrap;font-size:11.5px;color:var(--muted);margin-top:6px}
.gd-legend i{display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:4px;vertical-align:-1px}
.gd-bar{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.gd-quote{font-size:12.5px;line-height:1.5;border-left:3px solid var(--mark);padding:4px 10px;background:var(--sunk);border-radius:0 6px 6px 0}
.gd-sec{display:grid;grid-template-columns:minmax(120px,190px) 1fr 52px;gap:8px;align-items:center;font-size:12.5px;padding:3px 0}
.gd-sec .b{height:8px;border-radius:4px;background:var(--line);position:relative;overflow:hidden}
.gd-sec .b i{position:absolute;left:0;top:0;bottom:0;background:var(--accent);border-radius:4px}
.gd-sec button{border:0;background:none;padding:0;text-align:left;cursor:pointer;color:var(--ink);font:inherit}
.gd-sec button:hover{color:var(--accent)}
.drawer .gd-list td{font-size:12px}
@media (max-width:860px){.gd-hide-sm{display:none}.gd-stat .v{font-size:16px}}
`));

  /* ---------- formatting ---------- */
  const BASIS = { gaap: "GAAP", adjusted: "Adjusted", organic: "Organic", constant_currency: "Constant currency", unspecified: "As stated" };
  const ACT = { initial: "Initial", raised: "Raised", lowered: "Lowered", reaffirmed: "Reaffirmed", narrowed: "Narrowed", widened: "Widened", reinstated: "Reinstated" };
  const RES = { above: "Above range", within: "In range", below: "Below range" };
  const sgnStr = (v, s) => (v > 0 ? "+" : v < 0 ? "−" : "") + s;
  function fv(v, unit, measure) {
    if (v == null || !isFinite(v)) return "–";
    const a = Math.abs(v), neg = v < 0 ? "−" : "";
    if (unit === "pct") return (measure === "growth" || measure === "change" ? sgnStr(v, "") : neg) + a.toFixed(a < 1 && a > 0 ? 2 : 1) + "%";
    if (unit === "USD_per_share") return neg + "$" + a.toFixed(2);
    if (unit === "USD") {
      const pre = measure === "change" ? sgnStr(v, "") : neg;
      if (a >= 1e12) return pre + "$" + (a / 1e12).toFixed(2) + "T";
      if (a >= 1e9) return pre + "$" + (a / 1e9).toFixed(a >= 1e11 ? 1 : 2) + "B";
      if (a >= 1e6) return pre + "$" + (a / 1e6).toFixed(a >= 1e8 ? 0 : 1) + "M";
      return pre + "$" + a.toLocaleString("en-US", { maximumFractionDigits: 2 });
    }
    if (unit === "count") return neg + (a >= 1e9 ? (a / 1e9).toFixed(2) + "B" : a >= 1e6 ? (a / 1e6).toFixed(1) + "M" : a.toLocaleString("en-US", { maximumFractionDigits: 1 }));
    return neg + a.toLocaleString("en-US", { maximumFractionDigits: 2 });
  }
  function frange(p, unit, measure) {
    const f = v => fv(v, unit, measure);
    if (p.b === "at_least") return "≥ " + f(p.lo);
    if (p.b === "at_most") return "≤ " + f(p.hi);
    if (p.lo == null && p.hi == null) return f(p.mid);
    if (p.lo === p.hi || p.hi == null || p.lo == null) return (p.b === "approximately" ? "~" : "") + f(p.mid);
    return `${f(p.lo)} to ${f(p.hi)}`;
  }
  function fsurp(o, unit, measure) {
    if (!o) return "–";
    if (o.surprise_rel != null) return sgnStr(o.surprise_rel, Math.abs(o.surprise_rel * 100).toFixed(1) + "%");
    if (unit === "pct" || measure !== "level") return sgnStr(o.surprise_abs, Math.abs(o.surprise_abs).toFixed(1) + " pts");
    return sgnStr(o.surprise_abs, fv(Math.abs(o.surprise_abs), unit, "level"));
  }
  const pctTxt = v => v == null ? "–" : Math.round(v * 100) + "%";
  const goodCls = (sign, dir) => !sign || !dir ? "" : sign * dir > 0 ? "up" : "down";
  const actCls = (a, dir) => a === "raised" ? goodCls(1, dir) || "acc" : a === "lowered" ? goodCls(-1, dir) || "acc" : a === "reaffirmed" || a === "narrowed" ? "acc" : "";
  const resCls = (r, dir) => r === "within" ? "acc" : r === "above" ? goodCls(1, dir) : r === "below" ? goodCls(-1, dir) : "";
  const seriesName = s => [s.segment && !s.name.toLowerCase().includes(String(s.segment).toLowerCase()) ? s.segment : null, s.name].filter(Boolean).join(" · ");
  const seriesSub = s => [BASIS[s.basis], s.measure === "growth" ? "growth" : s.measure === "change" ? "change" : null, s.label && s.label.toLowerCase() !== s.name.toLowerCase() ? `“${s.label}”` : null].filter(Boolean).join(" · ");
  const ptypeName = { Q: "Quarterly", FY: "Annual", H1: "Half-year", H2: "Half-year", M: "Long-term targets" };
  const kindOf = t => t === "H1" || t === "H2" ? "H" : t;
  const doc = (b, id) => (b.docs || {})[id] || { id, title: id };
  const printedOf = pr => pr ? (pr.low || pr.point || pr.high || pr.value || null) : null;

  /* ---------- source opening ---------- */
  function openPoint(tk, b, s, per, pt) {
    openDoc(tk, doc(b, pt.doc), { page: pt.pg, quote: pt.q, printed: printedOf(pt.pr), label: `${seriesName(s)} guidance · ${per} · issued ${fdate(pt.d)}`, display: frange(pt, s.unit, s.measure) });
  }
  async function openActual(tk, b, s, per, a) {
    if (!a) return;
    const display = fv(a.v, s.unit, s.measure);
    if (a.src === "xbrl") {
      try { await ensureCompany(tk); } catch { }
      const cell = findCell(tk, a.label, per);
      if (cell) return openSource(cell);
      return openList(`${a.label} · ${per}`, "Reported in SEC filings", [el("div", { class: "note" }, `${a.formula}. Filed values:`), factTable(a.inputs)]);
    }
    if (a.src === "release" || a.src === "kpi") {
      if (!a.doc || a.doc === "derived") return openList(`${s.name} · ${per}`, "Derived KPI value", [el("div", { class: "note" }, "This KPI value is derived from other reported values; open the company's KPI table for its inputs.")]);
      return openDoc(tk, doc(b, a.doc), { page: a.page, quote: a.quote, printed: a.printed, value: a.v, label: `${a.kpi_name || a.label || s.name} · ${per} · reported`, display });
    }
  }
  function factTable(inputs) {
    const t = el("table", { class: "t gd-list" }, el("thead", {}, el("tr", {}, el("th", {}, "Line"), el("th", {}, "Period end"), el("th", { class: "r" }, "Value"), el("th", {}, "Filing"))));
    const tb = el("tbody");
    (inputs || []).forEach(r => tb.append(el("tr", {}, el("td", {}, r.label), el("td", { class: "num" }, r.end), el("td", { class: "r num" }, Math.abs(r.value) > 1e5 ? fmtMoney(r.value) : fnum(r.value, 2)),
      el("td", {}, el("a", { href: `https://www.sec.gov/Archives/edgar/data/${(r.accession || "").slice(0, 10).replace(/^0+/, "")}/${(r.accession || "").replace(/-/g, "")}/`, target: "_blank", rel: "noopener" }, `${r.form || ""} ${r.filed || ""}`)))));
    t.append(tb);
    return el("div", { class: "scroll" }, t);
  }
  function openList(title, eyebrow, nodes) {
    hideTip();
    DR.cell = { guidance: title };
    const dh = $("#dh"), db = $("#db");
    dh.innerHTML = ""; db.innerHTML = "";
    dh.append(el("div", { class: "row" }, el("span", { class: "eyebrow" }, eyebrow), el("div", { class: "x" }, el("button", { class: "iconbtn", onclick: closeSource, "aria-label": "Close (Esc)" }, svgUse("i-x")))),
      el("div", { class: "row" }, el("h2", {}, title)));
    db.append(...nodes);
    drawer.classList.add("open"); drawer.setAttribute("aria-hidden", "false");
    if (innerWidth < 860) scrim.hidden = false;
  }
  const vbtn = (txt, onclick, c = "", title = "Open source") => el("button", { class: "gd-v " + c, title, onclick: e => { e.stopPropagation(); onclick(); } }, txt);

  /* ---------- credibility ---------- */
  function scored(b) {
    const out = [];
    b.series.forEach(s => s.periods.forEach(p => { if (p.outcome) out.push({ s, p }); }));
    return out.sort((x, y) => y.p.end.localeCompare(x.p.end));
  }
  function outcomeTable(tk, b, rows) {
    const t = el("table", { class: "t gd-tbl gd-list" }, el("thead", {}, el("tr", {}, el("th", {}, "Period"), el("th", {}, "Metric"), el("th", { class: "r" }, "Last guidance"), el("th", { class: "r" }, "Actual"), el("th", { class: "r" }, "vs midpoint"), el("th", {}, "Result"))));
    const tb = el("tbody");
    rows.forEach(({ s, p }) => {
      const o = p.outcome, fin = p.points.find(x => x.id === o.final_id) || p.points.at(-1);
      tb.append(el("tr", {}, el("td", { class: "mono" }, p.label), el("td", {}, seriesName(s), el("div", { class: "faint", style: "font-size:11px" }, BASIS[s.basis])),
        el("td", { class: "r" }, vbtn(frange(fin, s.unit, s.measure), () => openPoint(tk, b, s, p.label, fin))),
        el("td", { class: "r" }, vbtn(fv(p.actual.v, s.unit, s.measure), () => openActual(tk, b, s, p.label, p.actual))),
        el("td", { class: "r num " + goodCls(Math.sign(o.surprise_abs), s.direction) }, fsurp(o, s.unit, s.measure)),
        el("td", {}, el("span", { class: "chip " + resCls(o.result, s.direction) }, RES[o.result]))));
    });
    t.append(tb);
    return el("div", { class: "scroll" }, t);
  }
  function credPanel(host, tk, b) {
    const c = b.credibility || {};
    const all = scored(b);
    const list = (title, filter) => () => openList(title, `${tk} guidance record`, [el("div", { class: "note" }, "Each row is the last guidance issued before the actual was reported, against that actual. Click a figure to open its source."), outcomeTable(tk, b, all.filter(filter))]);
    const stat = (l, v, s, onclick) => el("div", { class: "gd-stat" }, el("div", { class: "l" }, l), el("div", { class: "v" }, onclick && v !== "–" ? vbtn(v, onclick, "", "Show the periods behind this figure") : v), s ? el("div", { class: "s" }, s) : null);
    const dirOK = x => x.s.direction !== 0;
    const tend = { conservative: "Conservative: actuals usually land above the range", balanced: "Balanced: actuals usually land inside the range", optimistic: "Optimistic: actuals often land below the range" }[c.tendency];
    const g = el("div", { class: "gd-stats" },
      stat("Scored periods", c.n_scored ? String(c.n_scored) : "–", c.first_issued ? `guidance since ${fmon(c.first_issued)}` : null, list("All scored periods", () => true)),
      stat("Met or beat", pctTxt(c.hit_rate), c.n_met != null ? `${c.n_met} of ${c.n_scored}` : null, list("Met or beat guidance", x => x.p.outcome.met)),
      stat("Inside the range", pctTxt(c.in_range_rate), null, list("Actual inside the guided range", x => x.p.outcome.result === "within")),
      stat("Above the range", pctTxt(c.above_rate), null, list("Actual above the guided range", x => x.p.outcome.result === "above")),
      stat("Below the range", pctTxt(c.below_rate), null, list("Actual below the guided range", x => x.p.outcome.result === "below")),
      stat("Average surprise", c.avg_surprise == null ? "–" : sgnStr(c.avg_surprise, Math.abs(c.avg_surprise * 100).toFixed(1) + "%"), "vs midpoint, amounts and EPS, signed so + is better", list("Surprise vs midpoint (amounts and EPS)", x => x.p.outcome.surprise_rel != null && dirOK(x))),
      stat("Annual guidance raised", pctTxt(c.raise_rate), c.n_fy ? `of ${c.n_fy} fiscal-year targets` : null, list("Fiscal-year guidance: first vs last midpoint", x => x.p.type === "FY" && x.s.direction > 0)),
      el("div", { class: "gd-stat" }, el("div", { class: "l" }, "Tendency"), el("div", { class: "v", style: "font-size:15px;text-transform:capitalize" }, c.tendency || "Too few periods"), el("div", { class: "s" }, tend || "Needs four scored periods of revenue, earnings or margin guidance")));
    const ch = (c.definition_changes || []);
    const chg = ch.length ? el("div", { style: "display:flex;flex-direction:column;gap:6px;margin-top:12px" }, el("div", { class: "eyebrow" }, "Definition changes"),
      ...ch.map(x => el("div", { style: "font-size:12.5px" }, el("b", {}, x.metric), ` FY${x.fy}: guided as ${x.to.join(", ")} instead of ${x.from.join(", ")} `,
        vbtn("first statement " + fdate(x.issued), () => { const s = b.series.find(ss => ss.periods.some(p => p.points.some(pt => pt.id === x.id))); const p = s && s.periods.find(pp => pp.points.some(pt => pt.id === x.id)); const pt = p && p.points.find(q => q.id === x.id); if (pt) openPoint(tk, b, s, p.label, pt); })))) : null;
    host.append(panel("Guidance record", el("span", { class: "faint", style: "font-size:12px" }, "Last guidance before each report vs the reported actual"), el("div", { class: "bd" }, g, chg),
      el("div", { class: "src" }, "Guidance read from earnings releases, remarks, presentations and transcripts (8-K exhibits and IR sites). Actuals: XBRL facts for GAAP measures, the company's own reported figures for adjusted measures, published KPI values for operating metrics. Met or beat: at or above the range for revenue, earnings and margins, at or below it for costs, inside it for neutral items such as tax rate or capex.")));
  }

  /* ---------- chart ---------- */
  function drawChart(host, tk, b, s, periods) {
    host.innerHTML = "";
    const W = Math.max(300, host.clientWidth || 640), H = innerWidth < 860 ? 220 : 270, m = { t: 14, r: 58, b: 26, l: 8 };
    const vals = [], dates = [];
    periods.forEach(p => { p.points.forEach(pt => { [pt.lo, pt.hi, pt.mid].forEach(v => v != null && vals.push(v)); dates.push(pd(pt.d)); }); if (p.actual) { vals.push(p.actual.v); dates.push(pd(p.actual.reported_on || p.end)); } });
    if (!vals.length) { host.append(el("div", { class: "empty" }, "No guidance in this view")); return; }
    let [x0, x1] = d3.extent(dates); const padx = Math.max(20 * 864e5, (x1 - x0) * 0.04); x0 = new Date(+x0 - padx); x1 = new Date(+x1 + padx);
    const x = d3.scaleUtc().domain([x0, x1]).range([m.l, W - m.r]);
    let [y0, y1] = d3.extent(vals); const pady = (y1 - y0) * 0.1 || Math.abs(y1) * 0.05 || 1;
    const y = d3.scaleLinear().domain([y0 - pady, y1 + pady]).nice().range([H - m.b, m.t]);
    const svg = d3.select(host).append("svg").attr("viewBox", `0 0 ${W} ${H}`).attr("height", H);
    const yf = v => fv(v, s.unit, s.measure);
    svg.append("g").attr("class", "grid").attr("transform", `translate(${W - m.r},0)`).call(d3.axisRight(y).ticks(5).tickSize(-(W - m.r - m.l)).tickFormat(yf)).call(g => g.select(".domain").remove()).call(g => g.selectAll("text").attr("x", 6));
    svg.append("g").attr("class", "ax").attr("transform", `translate(0,${H - m.b})`).call(d3.axisBottom(x).ticks(Math.max(2, Math.floor(W / 110))).tickSizeOuter(0));
    const leg = el("div", { class: "gd-legend" });
    periods.forEach((p, i) => {
      const col = css(`--c${(i % 6) + 1}`);
      const pts = p.points.map(pt => [pd(pt.d), pt.mid]);
      if (p.actual) pts.push([pd(p.actual.reported_on || p.end), null]);
      const line = p.points.map(pt => [x(pd(pt.d)), y(pt.mid)]);
      if (line.length > 1) svg.append("path").attr("d", d3.line().curve(d3.curveStepAfter)(line)).attr("fill", "none").attr("stroke", col).attr("stroke-width", 1.4).attr("opacity", .75);
      if (p.actual && p.points.length) {
        const lp = p.points.at(-1), ax = x(pd(p.actual.reported_on || p.end));
        svg.append("path").attr("d", d3.line()([[x(pd(lp.d)), y(lp.mid)], [ax, y(lp.mid)]])).attr("fill", "none").attr("stroke", col).attr("stroke-width", 1).attr("stroke-dasharray", "3 3").attr("opacity", .6);
      }
      p.points.forEach(pt => {
        const cx = x(pd(pt.d));
        const tipHtml = `<div style="opacity:.7">${esc(p.label)} · ${esc(ACT[pt.a] || pt.a)} · ${fdate(pt.d)}</div><b>${esc(frange(pt, s.unit, s.measure))}</b><div style="opacity:.7;max-width:320px;white-space:normal">${esc((pt.q || "").slice(0, 220))}</div>`;
        const g = svg.append("g").attr("class", "hit").on("mousemove", ev => showTip(ev, tipHtml)).on("mouseleave", hideTip).on("click", () => { hideTip(); openPoint(tk, b, s, p.label, pt); });
        if (pt.lo != null && pt.hi != null && pt.lo !== pt.hi) g.append("rect").attr("x", cx - 3).attr("width", 6).attr("y", y(pt.hi)).attr("height", Math.max(2, y(pt.lo) - y(pt.hi))).attr("rx", 2).attr("fill", col).attr("opacity", .35).attr("stroke", col);
        if (pt.b === "at_least" || pt.b === "at_most") g.append("path").attr("d", d3.symbol(d3.symbolTriangle, 46)()).attr("transform", `translate(${cx},${y(pt.mid)}) rotate(${pt.b === "at_least" ? 0 : 180})`).attr("fill", col);
        else g.append("circle").attr("cx", cx).attr("cy", y(pt.mid)).attr("r", 3.6).attr("fill", "var(--surface)").attr("stroke", col).attr("stroke-width", 2);
        g.append("rect").attr("x", cx - 8).attr("width", 16).attr("y", Math.min(y(pt.hi ?? pt.mid), y(pt.mid)) - 8).attr("height", Math.abs(y(pt.lo ?? pt.mid) - y(pt.hi ?? pt.mid)) + 16).attr("fill", "transparent");
      });
      if (p.actual) {
        const ax = x(pd(p.actual.reported_on || p.end)), ay = y(p.actual.v);
        const o = p.outcome;
        const tipHtml = `<div style="opacity:.7">${esc(p.label)} actual${p.actual.reported_on ? " · reported " + fdate(p.actual.reported_on) : ""}</div><b>${esc(fv(p.actual.v, s.unit, s.measure))}</b>${o ? `<div>${esc(RES[o.result])} · ${esc(fsurp(o, s.unit, s.measure))} vs midpoint</div>` : ""}<div style="opacity:.7">${esc(p.actual.src === "xbrl" ? "SEC filing (XBRL)" : p.actual.src === "kpi" ? "Published KPI" : "Company-reported figure")}</div>`;
        svg.append("path").attr("class", "hit").attr("d", d3.symbol(d3.symbolDiamond, 90)()).attr("transform", `translate(${ax},${ay})`).attr("fill", col).attr("stroke", "var(--surface)").attr("stroke-width", 1.5)
          .on("mousemove", ev => showTip(ev, tipHtml)).on("mouseleave", hideTip).on("click", () => { hideTip(); openActual(tk, b, s, p.label, p.actual); });
      }
      leg.append(el("span", {}, el("i", { style: `background:${col}` }), p.label));
    });
    host.append(leg, el("div", { class: "gd-legend" }, el("span", {}, "○ guidance midpoint, bar = range, ▲ “at least”"), el("span", {}, "◆ actual"), el("span", {}, "Click any mark for its source")));
  }

  /* ---------- per-period table ---------- */
  function periodTable(tk, b, s, periods) {
    const t = el("table", { class: "t gd-tbl" }, el("thead", {}, el("tr", {}, el("th", {}, "Period"), el("th", { class: "r" }, "First guidance"), el("th", {}, "Revisions"), el("th", { class: "r" }, "Latest"), el("th", { class: "r" }, "Actual"), el("th", { class: "r gd-hide-sm" }, "vs midpoint"), el("th", {}, "Result"))));
    const tb = el("tbody");
    [...periods].reverse().forEach(p => {
      const first = p.points[0], lastp = p.points.at(-1), o = p.outcome;
      const revs = el("div", { class: "gd-revs" }, ...p.points.slice(1).map(pt => el("button", { class: "gd-rev " + actCls(pt.a, s.direction), title: `${ACT[pt.a] || pt.a} ${fdate(pt.d)}: ${frange(pt, s.unit, s.measure)}. Open source`, onclick: () => openPoint(tk, b, s, p.label, pt) },
        (pt.a === "raised" ? "↑ " : pt.a === "lowered" ? "↓ " : "") + (ACT[pt.a] || pt.a) + " " + fmon(pt.d))));
      tb.append(el("tr", {},
        el("td", { class: "mono" }, p.label),
        el("td", { class: "r" }, vbtn(frange(first, s.unit, s.measure), () => openPoint(tk, b, s, p.label, first)), el("div", { class: "faint", style: "font-size:11px" }, fdate(first.d))),
        el("td", { class: "wrap" }, p.points.length > 1 ? revs : el("span", { class: "faint" }, "none")),
        el("td", { class: "r" }, p.points.length > 1 ? vbtn(frange(lastp, s.unit, s.measure), () => openPoint(tk, b, s, p.label, lastp)) : el("span", { class: "faint" }, "same")),
        el("td", { class: "r" }, p.actual ? vbtn(fv(p.actual.v, s.unit, s.measure), () => openActual(tk, b, s, p.label, p.actual)) : el("span", { class: "faint" }, p.open ? "pending" : "not matched")),
        el("td", { class: "r num gd-hide-sm " + (o ? goodCls(Math.sign(o.surprise_abs), s.direction) : "") }, o ? fsurp(o, s.unit, s.measure) : "–"),
        el("td", {}, o ? el("span", { class: "chip " + resCls(o.result, s.direction) }, RES[o.result]) : p.actual ? el("span", { class: "chip", title: "Guidance was issued after the actual was reported" }, "not scored") : null)));
    });
    t.append(tb);
    return el("div", { class: "scroll" }, t);
  }

  /* ---------- estimates slot ---------- */
  function estimatesPanel(host, b) {
    const rows = b.consensus || [];
    const body = el("div", { class: "bd" });
    if (!rows.length) body.append(el("div", { class: "note" }, "No consensus feed is connected. The free sources checked (Yahoo Finance, Nasdaq, Zacks, Alpha Vantage, Financial Modeling Prep, Finnhub) either prohibit automated collection or require a paid license to display estimates publicly. This table fills in when a licensed feed writes to consensus_estimates; guidance, actuals and the record above do not depend on it."));
    else {
      const t = el("table", { class: "t gd-tbl" }, el("thead", {}, el("tr", {}, el("th", {}, "Period"), el("th", {}, "Metric"), el("th", { class: "r" }, "Mean"), el("th", { class: "r" }, "Range"), el("th", { class: "r" }, "Estimates"), el("th", {}, "As of"), el("th", {}, "Provider"))));
      const tb = el("tbody");
      rows.forEach(r => tb.append(el("tr", {}, el("td", { class: "mono" }, r.fiscal_label), el("td", {}, `${r.metric} · ${BASIS[r.basis] || r.basis}`), el("td", { class: "r num" }, fv(+r.mean, r.unit, "level")),
        el("td", { class: "r num" }, r.low != null ? `${fv(+r.low, r.unit, "level")} to ${fv(+r.high, r.unit, "level")}` : "–"), el("td", { class: "r num" }, r.n_estimates ?? "–"), el("td", { class: "num" }, fdate(r.as_of)),
        el("td", {}, r.source_url ? el("a", { href: r.source_url, target: "_blank", rel: "noopener" }, r.provider) : r.provider))));
      t.append(tb); body.append(el("div", { class: "scroll" }, t));
    }
    host.append(panel("Consensus estimates", null, body));
  }

  /* ---------- company tab ---------- */
  async function coGuidance(body, tk) {
    const wrap = el("div", { style: "display:flex;flex-direction:column;gap:16px" }, el("div", { class: "skel", style: "height:120px" }));
    body.append(wrap);
    let b;
    try { b = await loadCo(tk); } catch { b = null; }
    wrap.innerHTML = "";
    if (!b || !b.series?.length) {
      wrap.append(panel("Guidance", null, el("div", { class: "bd" }, el("div", { class: "muted" }, b ? `No quantitative guidance found in the ${b.coverage?.docs_read || 0} earnings documents read for ${tk}. Some companies do not give numeric guidance.` : `Guidance for ${tk} has not been extracted yet. Coverage starts with the S&P 100 and grows to every company.`))));
      if (b) estimatesPanel(wrap, b);
      return;
    }
    credPanel(wrap, tk, b);
    const key = `gd:${tk}`;
    let cur = store.get(key, null);
    if (!b.series.find(s => s.key === cur)) cur = b.series[0].key;
    const sel = el("select", { class: "btn", style: "max-width:min(420px,100%)", onchange: e => { cur = e.target.value; store.set(key, cur); draw(); } },
      ...b.series.map(s => { const o = el("option", { value: s.key }, `${seriesName(s)} · ${BASIS[s.basis]}${s.measure === "growth" ? " growth" : ""} (${s.periods.length})`); if (s.key === cur) o.selected = true; return o; }));
    const kindHost = el("span");
    const chartHost = el("div", { class: "bd gd-chart" });
    const tableHost = el("div");
    const sub = el("div", { class: "faint", style: "font-size:12px;padding:0 16px" });
    wrap.append(panel("Guidance history", el("div", { class: "gd-bar" }, sel, kindHost), sub, chartHost, tableHost,
      el("div", { class: "src" }, "Every point is a statement in a stored document; same-day repeats (release, remarks, slides) count once. Actual: " + "the XBRL fact for GAAP or unstated measures, otherwise the company's own reported figure or published KPI.")));
    let kind = null;
    function draw() {
      const s = b.series.find(x => x.key === cur);
      const kinds = [...new Set(s.periods.map(p => kindOf(p.type)))].sort((a, c) => ["FY", "Q", "H", "M"].indexOf(a) - ["FY", "Q", "H", "M"].indexOf(c));
      if (!kinds.includes(kind)) kind = kinds[0];
      kindHost.innerHTML = "";
      if (kinds.length > 1) kindHost.append(seg(kinds.map(k => [k, ptypeName[k === "H" ? "H1" : k]]), kind, v => { kind = v; render(); }));
      render();
      function render() {
        const ps = s.periods.filter(p => kindOf(p.type) === kind);
        sub.textContent = `${seriesName(s)} · ${seriesSub(s)} · ${ps.length} ${ptypeName[kind === "H" ? "H1" : kind].toLowerCase()} period${ps.length === 1 ? "" : "s"}, ${ps.reduce((a, p) => a + p.points.length, 0)} statements`;
        const shown = ps.slice(-(innerWidth < 860 ? 6 : 12));
        drawChart(chartHost, tk, b, s, shown);
        tableHost.innerHTML = "";
        tableHost.append(periodTable(tk, b, s, ps));
      }
    }
    draw();
    if (b.withdrawn?.length) {
      const t = el("table", { class: "t gd-tbl" }, el("thead", {}, el("tr", {}, el("th", {}, "Date"), el("th", {}, "Period"), el("th", {}, "Statement"))));
      const tb = el("tbody");
      b.withdrawn.forEach(w => tb.append(el("tr", { class: "click", onclick: () => openDoc(tk, doc(b, w.doc), { page: w.pg, quote: w.q, label: `Guidance withdrawn · ${w.per} · ${fdate(w.d)}` }) },
        el("td", { class: "num" }, fdate(w.d)), el("td", { class: "mono" }, w.per), el("td", { class: "wrap", style: "white-space:normal" }, w.q.slice(0, 220)))));
      t.append(tb);
      wrap.append(panel("Withdrawn or suspended guidance", null, el("div", { class: "scroll" }, t)));
    }
    estimatesPanel(wrap, b);
    const cv = b.coverage || {};
    wrap.append(el("div", { class: "faint", style: "font-size:11.5px" }, `${cv.docs_read || 0} documents read (issued ${cv.from ? fdate(cv.from) : "–"} to ${cv.to ? fdate(cv.to) : "–"}). Extraction: fixed patterns first, a language model for the rest; every quote is checked against the document text. Updated ${relTime(b.generated)}.`));
  }
  window.FU_COTABS.guidance = { name: "Guidance", after: "earnings", render: coGuidance };

  /* ---------- cross-company page ---------- */
  async function pageGuidance(arg) {
    const ix = await loadIdx();
    page.innerHTML = "";
    page.append(el("div", { class: "ph" }, el("div", {}, el("div", { class: "eyebrow" }, "Guidance"), el("h1", {}, "What companies said they would do"),
      el("div", { class: "sub" }, "Numeric guidance read from earnings releases, remarks, slides and transcripts, every revision dated and quoted, and scored against what the company later reported."))));
    const st = ix.stats || {};
    const tile = (l, v, s) => el("div", { class: "gd-stat" }, el("div", { class: "l" }, l), el("div", { class: "v num" }, v), s ? el("div", { class: "s" }, s) : null);
    const evs = ix.events || [];
    const n = a => evs.filter(e => e.a === a).length;
    page.append(el("div", { class: "gd-stats" }, tile("Companies with guidance", String(st.companies || 0)), tile("Guidance statements", (st.points || 0).toLocaleString("en-US")), tile("Periods scored", (st.scored || 0).toLocaleString("en-US")),
      tile("Raised", String(n("raised")), `since ${fdate(ix.since)}`), tile("Lowered", String(n("lowered")), `since ${fdate(ix.since)}`), tile("Withdrawn", String((ix.withdrawn || []).length), `since ${fdate(ix.since)}`)));
    const b = { docs: ix.docs || {} };
    // recent changes
    let act = store.get("gd:act", "chg"), sector = store.get("gd:sector", ""), only100 = store.get("gd:sp100", false), lim = 60;
    const sectors = [...new Set((ix.reliability || []).map(r => r.sector).concat(evs.map(e => IDX[e.t]?.sector)).filter(Boolean))].sort();
    const host = el("div");
    const secSel = el("select", { class: "btn", onchange: e => { sector = e.target.value; store.set("gd:sector", sector); lim = 60; drawEv(); drawRel(); } }, el("option", { value: "" }, "All sectors"), ...sectors.map(s => { const o = el("option", { value: s }, s); if (s === sector) o.selected = true; return o; }));
    const cb = el("input", { type: "checkbox" }); cb.checked = only100; cb.onchange = () => { only100 = cb.checked; store.set("gd:sp100", only100); drawEv(); drawRel(); };
    const filt = el("div", { class: "gd-bar" }, seg([["chg", "Raised or lowered"], ["raised", "Raised"], ["lowered", "Lowered"], ["initial", "New"], ["reaffirmed", "Reaffirmed"], ["withdrawn", "Withdrawn"]], act, v => { act = v; store.set("gd:act", v); lim = 60; drawEv(); }),
      secSel, el("label", { style: "display:flex;gap:5px;align-items:center;font-size:12.5px;color:var(--muted)" }, cb, "S&P 100"));
    page.append(panel("Recent guidance changes", filt, host, el("div", { class: "src" }, "Changes are measured against the previous statement for the same metric, basis and period. Click a figure to open the sentence it came from.")));
    const okT = t => (!sector || IDX[t]?.sector === sector) && (!only100 || IDX[t]?.sp100 || (ix.reliability || []).find(r => r.t === t)?.sp100);
    function drawEv() {
      host.innerHTML = "";
      if (act === "withdrawn") {
        const ws = (ix.withdrawn || []).filter(w => okT(w.t));
        const t = el("table", { class: "t gd-tbl" }, el("thead", {}, el("tr", {}, el("th", {}, "Date"), el("th", {}, "Company"), el("th", {}, "Period"), el("th", {}, "Statement"))));
        const tb = el("tbody");
        ws.forEach(w => tb.append(el("tr", { class: "click", onclick: () => openDoc(w.t, doc(b, w.doc), { page: w.pg, quote: w.q, label: `${w.t} guidance withdrawn · ${w.per}` }) }, el("td", { class: "num" }, fdate(w.d)), el("td", {}, el("a", { href: "#company-" + w.t.toLowerCase(), onclick: e => { e.stopPropagation(); store.set("coTab", "guidance"); STATE.coTab = "guidance"; } }, w.t)), el("td", { class: "mono" }, w.per), el("td", { class: "wrap", style: "white-space:normal" }, w.q.slice(0, 200)))));
        t.append(tb);
        host.append(ws.length ? el("div", { class: "scroll" }, t) : el("div", { class: "empty" }, "No withdrawn guidance in this window."));
        return;
      }
      const rows = evs.filter(e => (act === "chg" ? e.a === "raised" || e.a === "lowered" : act === "initial" ? e.a === "initial" || e.a === "reinstated" : e.a === act) && okT(e.t));
      const t = el("table", { class: "t gd-tbl" }, el("thead", {}, el("tr", {}, el("th", {}, "Date"), el("th", {}, "Company"), el("th", {}, "Metric"), el("th", {}, "Period"), el("th", { class: "r" }, "Before"), el("th", { class: "r" }, "Now"), el("th", { class: "r gd-hide-sm" }, "Change"))));
      const tb = el("tbody");
      rows.slice(0, lim).forEach(e => {
        const s = { name: e.name, segment: e.seg, unit: e.unit, measure: e.measure, label: e.label, basis: e.basis };
        const ch = e.prev && e.prev.mid != null && e.mid != null ? (e.unit === "pct" || e.measure !== "level" ? sgnStr(e.mid - e.prev.mid, Math.abs(e.mid - e.prev.mid).toFixed(1) + " pts") : e.prev.mid ? sgnStr(e.mid - e.prev.mid, Math.abs((e.mid / e.prev.mid - 1) * 100).toFixed(1) + "%") : "–") : "–";
        tb.append(el("tr", {},
          el("td", { class: "num" }, fdate(e.d)),
          el("td", {}, el("a", { href: "#company-" + e.t.toLowerCase(), onclick: () => { store.set("coTab", "guidance"); STATE.coTab = "guidance"; store.set(`gd:${e.t}`, null); } }, miniLogo(e.t, 16), " ", e.t)),
          el("td", { class: "wrap" }, seriesName(s), el("div", { class: "faint", style: "font-size:11px" }, BASIS[e.basis] + (e.measure === "growth" ? " growth" : ""))),
          el("td", { class: "mono" }, e.per),
          el("td", { class: "r" }, e.prev ? vbtn(frange(e.prev, e.unit, e.measure), () => openDoc(e.t, doc(b, e.prev.doc), { page: e.prev.pg, quote: e.prev.q, printed: printedOf(e.prev.pr), label: `${e.t} ${seriesName(s)} · ${e.per} · issued ${fdate(e.prev.d)}`, display: frange(e.prev, e.unit, e.measure) })) : el("span", { class: "faint" }, "–")),
          el("td", { class: "r" }, vbtn(frange(e, e.unit, e.measure), () => openDoc(e.t, doc(b, e.doc), { page: e.pg, quote: e.q, printed: printedOf(e.pr), label: `${e.t} ${seriesName(s)} · ${e.per} · issued ${fdate(e.d)}`, display: frange(e, e.unit, e.measure) }), actCls(e.a, e.dir))),
          el("td", { class: "r gd-hide-sm" }, el("span", { class: "chip " + actCls(e.a, e.dir) }, (ACT[e.a] || e.a) + (e.prev ? " " + ch : "")))));
      });
      t.append(tb);
      host.append(rows.length ? el("div", { class: "scroll" }, t) : el("div", { class: "empty" }, "Nothing in this window for these filters."));
      if (rows.length > lim) host.append(el("div", { style: "padding:10px 16px" }, el("button", { class: "btn", onclick: () => { lim += 120; drawEv(); } }, `Show more (${rows.length - lim} left)`)));
    }
    drawEv();
    // reliability
    const relHost = el("div"), secHost = el("div", { class: "bd" });
    let minN = store.get("gd:minN", 8), sortK = "hit";
    const g = el("div", { class: "grid g64" });
    g.append(panel("Most reliable guiders", seg([["4", "4+ periods"], ["8", "8+ periods"], ["16", "16+ periods"]], String(minN), v => { minN = +v; store.set("gd:minN", minN); drawRel(); }), relHost,
      el("div", { class: "src" }, "Met or beat: the share of scored periods where the actual was at or above the last guidance range (at or below for costs, inside for neutral items). Click a figure for the periods behind it.")),
      panel("By sector", el("span", { class: "faint", style: "font-size:12px" }, "Median met-or-beat rate"), secHost));
    page.append(g);
    async function showRecord(t, filter, title) {
      const cb_ = await loadCo(t);
      const rows = scored(cb_).filter(filter);
      openList(`${t} · ${title}`, "Guidance record", [el("div", { class: "note" }, "Last guidance issued before each report against the reported actual. Click a figure to open its source."), outcomeTable(t, cb_, rows)]);
    }
    function drawRel() {
      relHost.innerHTML = "";
      const rows = (ix.reliability || []).filter(r => r.n >= minN && okT(r.t)).sort((a, c) => sortK === "surp" ? (c.surp ?? -9) - (a.surp ?? -9) : (c[sortK] ?? -1) - (a[sortK] ?? -1) || c.n - a.n);
      const th = (k, l, cl = "r") => el("th", { class: cl + " sx-sort" + (sortK === k ? " on" : ""), style: "cursor:pointer", onclick: () => { sortK = k; drawRel(); } }, l + (sortK === k ? " ↓" : ""));
      const t = el("table", { class: "t gd-tbl" }, el("thead", {}, el("tr", {}, el("th", {}, "Company"), el("th", { class: "gd-hide-sm" }, "Sector"), th("n", "Scored"), th("hit", "Met or beat"), th("in", "In range"), th("above", "Above", "r gd-hide-sm"), th("surp", "Avg surprise", "r gd-hide-sm"), el("th", {}, "Tendency"))));
      const tb = el("tbody");
      rows.slice(0, 60).forEach(r => tb.append(el("tr", {},
        el("td", {}, el("a", { href: "#company-" + r.t.toLowerCase(), onclick: () => { store.set("coTab", "guidance"); STATE.coTab = "guidance"; } }, miniLogo(r.t, 16), " ", r.t), el("span", { class: "faint gd-hide-sm", style: "font-size:11.5px;margin-left:6px" }, SHORT[r.t] || r.name || "")),
        el("td", { class: "muted gd-hide-sm" }, r.sector || ""),
        el("td", { class: "r" }, vbtn(String(r.n), () => showRecord(r.t, () => true, "all scored periods"))),
        el("td", { class: "r" }, vbtn(pctTxt(r.hit), () => showRecord(r.t, x => x.p.outcome.met, "met or beat"))),
        el("td", { class: "r" }, vbtn(pctTxt(r.in), () => showRecord(r.t, x => x.p.outcome.result === "within", "inside the range"))),
        el("td", { class: "r gd-hide-sm" }, vbtn(pctTxt(r.above), () => showRecord(r.t, x => x.p.outcome.result === "above", "above the range"))),
        el("td", { class: "r gd-hide-sm" }, r.surp == null ? "–" : vbtn(sgnStr(r.surp, Math.abs(r.surp * 100).toFixed(1) + "%"), () => showRecord(r.t, x => x.p.outcome.surprise_rel != null && x.s.direction !== 0, "surprise vs midpoint"), cls(r.surp))),
        el("td", {}, r.tend ? el("span", { class: "chip " + (r.tend === "conservative" ? "up" : r.tend === "optimistic" ? "down" : "acc") }, r.tend) : el("span", { class: "faint" }, "–")))));
      t.append(tb);
      relHost.append(rows.length ? el("div", { class: "scroll" }, t) : el("div", { class: "empty" }, "No company has enough scored periods for these filters yet."));
      // sector medians
      secHost.innerHTML = "";
      const bySec = {};
      (ix.reliability || []).filter(r => r.n >= minN && (!only100 || r.sp100)).forEach(r => (bySec[r.sector] = bySec[r.sector] || []).push(r));
      const med = a => { const s = a.map(r => r.hit).filter(v => v != null).sort((x, y) => x - y); return s.length ? s[Math.floor((s.length - 1) / 2)] : null; };
      Object.entries(bySec).map(([k, v]) => [k, med(v), v.length]).filter(x => x[1] != null).sort((a, c) => c[1] - a[1]).forEach(([k, m, nn]) => secHost.append(el("div", { class: "gd-sec" },
        el("button", { title: "Filter to this sector", onclick: () => { sector = sector === k ? "" : k; secSel.value = sector; store.set("gd:sector", sector); drawEv(); drawRel(); } }, k, el("span", { class: "faint" }, ` (${nn})`)),
        el("div", { class: "b" }, el("i", { style: `width:${Math.round(m * 100)}%` })), el("div", { class: "num", style: "text-align:right" }, pctTxt(m)))));
      if (!secHost.children.length) secHost.append(el("div", { class: "muted" }, "No sector has scored companies yet."));
    }
    drawRel();
    page.append(el("div", { class: "faint", style: "font-size:11.5px" }, `Updated ${relTime(ix.generated)}. Coverage starts with the S&P 100 over the last three years and extends to every company back to 2010.`));
  }
  window.FU_PAGES.guidance = pageGuidance;
})();
