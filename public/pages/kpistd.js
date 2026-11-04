// KPI peers page. Registers window.FU_PAGES.kpis(arg) (#kpis, #kpis-<canonical id>, #kpis-<canonical id>/<ticker>) and
// window.FU_KPISTD.companyPanel(host, ticker) for the company Segments & KPIs tab. Data from etl/kpistd.py:
// kpistd/taxonomy.json, kpistd/latest.json, kpistd/series/{id}.json and kpistd/t/{T}.json. Each company series is the
// values of one company KPI id; ids from other reporting bases are separate rows, never joined to it.
window.FU_PAGES = window.FU_PAGES || {};
(() => {
  "use strict";
  const C = { tax: null, latest: null, ser: {}, t: {} };
  const once = (key, path) => C[key] ? Promise.resolve(C[key]) : getJSON(path).then(j => (C[key] = j));
  const loadTax = () => once("tax", "kpistd/taxonomy.json");
  const loadLatest = () => C.latest ? Promise.resolve(C.latest) : getJSON("kpistd/latest.json").then(j => (C.latest = j)).catch(() => (C.latest = { kpis: {} }));
  const loadSer = id => C.ser[id] ? Promise.resolve(C.ser[id]) : getJSON(`kpistd/series/${id}.json`).then(j => (C.ser[id] = j));
  const loadT = tk => tk in C.t ? Promise.resolve(C.t[tk]) : getJSON(`kpistd/t/${tk}.json`).then(j => (C.t[tk] = j)).catch(() => (C.t[tk] = null));
  const ST = Object.assign({ K: "Q", align: "calendar", view: "value", group: "industry", focus: null, custom: [], alts: false, n: 10 }, store.get("kpistd", {}));
  const save = () => store.set("kpistd", { K: ST.K, align: ST.align, view: ST.view, group: ST.group, custom: ST.custom, alts: ST.alts, n: ST.n });

  if (!document.getElementById("ks-css")) document.head.append(el("style", { id: "ks-css" }, `
.ks-n{border:0;background:none;padding:0;font:inherit;color:inherit;cursor:pointer;font-variant-numeric:tabular-nums;text-decoration:underline dotted var(--faint);text-underline-offset:3px}
.ks-n:hover{color:var(--accent);text-decoration-color:var(--accent)}
.ks-ctl{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.ks-ctl label{font-size:12px;color:var(--muted);display:flex;gap:6px;align-items:center}
.ks-ctl select,.ks-ctl input{font:inherit;font-size:12.5px;padding:4px 8px;border:1px solid var(--line);border-radius:7px;background:var(--surface);color:var(--ink)}
.ks-ctl input{width:110px}
.ks-ctl input[type=checkbox]{width:auto;padding:0;margin:0}
.ks-tbl{width:100%;border-collapse:collapse;font-size:12.5px}
.ks-tbl th{font-weight:600;font-size:10.5px;text-transform:uppercase;letter-spacing:.04em;color:var(--faint);padding:7px 8px;border-bottom:1px solid var(--line);white-space:nowrap;text-align:right}
.ks-tbl th.l,.ks-tbl td.l{text-align:left}
.ks-tbl td{padding:6px 7px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}
.ks-tbl tr.focus td{background:var(--accent-soft)}
.ks-tbl tr.alt td{color:var(--muted);font-style:italic}
.ks-tbl td.co{min-width:150px}
.ks-tbl td.co a{color:inherit;text-decoration:none;display:inline-flex;gap:6px;align-items:center}
.ks-tbl td.co a:hover{color:var(--accent)}
.ks-tbl .sub{display:block;font-size:10.5px;color:var(--faint);font-style:normal;max-width:260px;overflow:hidden;text-overflow:ellipsis}
.ks-tbl td.na{color:var(--faint)}
.ks-pc{display:inline-block;min-width:34px;padding:1px 6px;border-radius:999px;font-size:11px;font-weight:600;background:var(--sunk);color:var(--muted)}
.ks-pc.hi{background:var(--up-soft);color:var(--up)}.ks-pc.lo{background:var(--down-soft);color:var(--down)}
.ks-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:10px}
.ks-card{border:1px solid var(--line);border-radius:10px;padding:10px 12px;background:var(--surface);display:flex;flex-direction:column;gap:3px;text-decoration:none;color:inherit;min-width:0}
.ks-card:hover{border-color:var(--accent)}
.ks-card .nm{font-weight:600;font-size:13px}
.ks-card .s{font-size:11.5px;color:var(--muted)}
.ks-card.off{opacity:.55}
.ks-fam{margin:14px 0 6px}
.ks-chart{width:100%}
.ks-chart svg{display:block;width:100%}
.ks-chart text{fill:var(--muted);font-size:10.5px}
.ks-chart .grid line{stroke:var(--line);stroke-dasharray:2 3}
.ks-chart .grid .domain{display:none}
.ks-chart .dot{cursor:pointer}
.ks-legend{display:flex;gap:10px;flex-wrap:wrap;font-size:11.5px;color:var(--muted);margin-top:6px}
.ks-legend i{display:inline-block;width:10px;height:3px;border-radius:2px;margin-right:4px;vertical-align:middle}
.ks-note{font-size:12px;color:var(--muted);max-width:90ch;line-height:1.5}
.ks-map td{font-size:11.5px;white-space:normal;text-align:left;vertical-align:top}
.ks-map td.rs{max-width:420px;color:var(--muted)}
details.ks-det>summary{cursor:pointer;padding:12px 16px;font-weight:600;font-size:13px}
@media (max-width:860px){.ks-hide-sm{display:none}.ks-tbl td.co{min-width:110px}.ks-tbl .sub{max-width:150px}.ks-ctl input{width:90px}}
`));

  /* ---------- formatting ---------- */
  function fmtV(v, unit) {
    if (v == null || !isFinite(v)) return "–";
    switch (unit) {
      case "%": return fnum(v, Math.abs(v) < 10 ? 2 : 1) + "%";
      case "pp": return fnum(v, 2) + " pp";
      case "USD": return fmtMoney(v);
      case "USD_per_share": case "USD_per_unit": return (v < 0 ? "−$" : "$") + fnum(Math.abs(v), 2);
      case "cents": return fnum(v, 2) + "¢";
      case "x": return fnum(v, 2) + "×";
      case "days": return fnum(v, 0) + " days";
      case "count": case "ASM": case "RPM": return fmtKpi(v, "count");
      default: return fmtKpi(v, "count") + " " + unit;
    }
  }
  const isPct = u => u === "%" || u === "pp";
  const fmtG = (g, unit) => g == null || !isFinite(g) ? "–" : isPct(unit) ? (g >= 0 ? "+" : "−") + fnum(Math.abs(g), 2) + " pp" : fsgn(g);
  const ord = n => { const s = ["th", "st", "nd", "rd"], v = n % 100; return n + (s[(v - 20) % 10] || s[v] || s[0]); };

  /* ---------- periods ---------- */
  const dOf = s => new Date(s + "T00:00:00Z");
  function fiscalKey(p, K) {
    const lab = String(p[2] || "");
    let m = lab.match(/^Q([1-4]) (FY|CY)(\d{2,4})$/);
    if (m && K === "Q") { const y = m[3].length === 2 ? 2000 + +m[3] : +m[3]; return { k: `Q${m[1]} ${m[2]}${String(y).slice(2)}`, o: y * 10 + +m[1] }; }
    m = lab.match(/^(FY|CY) ?(\d{2,4})$/);
    if (m && K === "A") { const y = m[2].length === 2 ? 2000 + +m[2] : +m[2]; return { k: `${m[1]}${y}`, o: y * 10 }; }
    return calKey(p, K);
  }
  function calKey(p, K) {
    // the calendar quarter or year that holds most of the period
    const d = dOf(p[0]); d.setUTCDate(d.getUTCDate() - (K === "Q" ? 45 : 182));
    const y = d.getUTCFullYear(), q = Math.floor(d.getUTCMonth() / 3) + 1;
    return K === "Q" ? { k: `Q${q} ${y}`, o: y * 10 + q } : { k: `${y}`, o: y * 10 };
  }
  const keyOf = (p, K) => ST.align === "calendar" ? calKey(p, K) : fiscalKey(p, K);
  function yoyOf(pts, i, unit) {
    const p = pts[i]; const e = dOf(p[0]);
    for (let j = i - 1; j >= 0; j--) {
      const dd = (e - dOf(pts[j][0])) / 864e5;
      if (Math.abs(dd - 365) <= 25) { const b = pts[j][1]; if (b == null || p[1] == null) return null; return isPct(unit) ? p[1] - b : b > 0 ? p[1] / b - 1 : null; }
      if (dd > 400) break;
    }
    return null;
  }

  /* ---------- sources ---------- */
  // the figure as printed in the quote (so the viewer highlights that exact token, not another number with the same digits)
  function printedIn(quote, v) {
    if (v == null || !quote) return null;
    const a = Math.abs(v);
    for (const t of String(quote).match(/\(?[-−]?\$?\d[\d,]*(\.\d+)?\)?%?/g) || []) {
      const n = parseFloat(t.replace(/[^\d.]/g, ""));
      if (!isFinite(n)) continue;
      if ([1, 1e3, 1e6, 1e9, 0.01].some(f => Math.abs(n * f - a) <= Math.max(0.005, a * 0.0015))) return t.replace(/[()%−-]/g, "").trim();
    }
    return null;
  }
  function openPoint(S, tk, s, p, K) {
    const d = (S.docs || {})[p[3]] || [];
    openKpiSource(tk, { name: s.name, unit: s.unit }, K, p[0], { v: p[9], printed: printedIn(p[5], p[9]), doc: p[3], page: p[4], quote: p[5], sheet: p[6], cell: p[7], derived: p[8] ? { formula: p[8] } : null,
      doc_title: d[0], path: d[1], ext: d[2], url: d[3], label: p[2] });
  }
  const numBtn = (text, onclick, title) => el("button", { class: "ks-n", title: title || "Open the source document at this value", onclick: e => { e.stopPropagation(); onclick(); } }, text);

  /* ---------- chart: one line per company series, every dot opens its source ---------- */
  function chart(host, cols, rows, unit, onDot) {
    host.innerHTML = "";
    const W = Math.max(320, host.clientWidth || 640), H = innerWidth < 860 ? 220 : 280, m = { t: 12, r: 64, b: 26, l: 8 };
    const vals = rows.flatMap(r => cols.map(c => r.cells[c.k]?.y).filter(v => v != null && isFinite(v)));
    if (!vals.length) { host.append(el("div", { class: "muted", style: "padding:20px" }, "No values in these periods.")); return; }
    const x = d3.scalePoint().domain(cols.map(c => c.k)).range([m.l + 10, W - m.r - 10]);
    let [y0, y1] = d3.extent(vals); const pad = (y1 - y0) * .08 || Math.abs(y1) * .1 || 1;
    const y = d3.scaleLinear().domain([y0 - pad, y1 + pad]).nice().range([H - m.b, m.t]);
    const svg = d3.select(host).append("svg").attr("viewBox", `0 0 ${W} ${H}`).attr("height", H);
    const yf = ST.view === "yoy" ? (isPct(unit) ? v => fnum(v, 1) + " pp" : v => (v * 100).toFixed(0) + "%") : v => fmtV(v, unit).replace(/ [a-z/]+$/i, "");
    svg.append("g").attr("class", "grid").attr("transform", `translate(${W - m.r},0)`).call(d3.axisRight(y).ticks(5).tickSize(-(W - m.r - m.l)).tickFormat(yf)).call(g => g.selectAll("text").attr("x", 6));
    const every = Math.max(1, Math.ceil(cols.length / Math.max(2, Math.floor(W / 70))));
    svg.append("g").attr("transform", `translate(0,${H - m.b})`).call(d3.axisBottom(x).tickValues(cols.map(c => c.k).filter((_, i) => (cols.length - 1 - i) % every === 0)).tickSizeOuter(0)).call(g => g.select(".domain").attr("stroke", css("--line2")));
    if (y.domain()[0] < 0 && y.domain()[1] > 0) svg.append("line").attr("x1", m.l).attr("x2", W - m.r).attr("y1", y(0)).attr("y2", y(0)).attr("stroke", css("--line2"));
    rows.forEach(r => {
      const pts = cols.map(c => ({ c, cell: r.cells[c.k] })).filter(z => z.cell && z.cell.y != null && isFinite(z.cell.y));
      const line = d3.line().x(z => x(z.c.k)).y(z => y(z.cell.y)).defined((z, i) => i === 0 || cols.indexOf(z.c) - cols.indexOf(pts[i - 1].c) <= 2);
      svg.append("path").attr("d", line(pts)).attr("fill", "none").attr("stroke", r.color).attr("stroke-width", r.focus ? 2.4 : 1.4).attr("opacity", r.focus || !rows.some(z => z.focus) ? 1 : .7).attr("stroke-dasharray", r.alt ? "4 3" : null);
      svg.selectAll(null).data(pts).join("circle").attr("class", "dot").attr("cx", z => x(z.c.k)).attr("cy", z => y(z.cell.y)).attr("r", r.focus ? 3.6 : 2.8).attr("fill", r.color)
        .on("mousemove", (ev, z) => showTip(ev, `<b>${esc(r.label)}</b> · ${esc(z.c.k)}<br>${esc(z.cell.text)}${z.cell.sub ? `<br><span style="opacity:.7">${esc(z.cell.sub)}</span>` : ""}<br><span style="opacity:.7">Click to open the source</span>`))
        .on("mouseleave", hideTip).on("click", (ev, z) => { hideTip(); onDot(r, z.cell); });
    });
    host.append(el("div", { class: "ks-legend" }, rows.map(r => el("span", {}, el("i", { style: `background:${r.color}` }), r.label))));
  }

  /* ---------- the peer comparison for one canonical KPI ---------- */
  const PAL = ["--c1", "--c2", "--c3", "--c4", "--c5", "--c6"];
  const EXTRA = ["#0F6CBD", "#B45309", "#4E8A00", "#BE185D", "#334155", "#7C3AED"];
  const colorAt = i => i < PAL.length ? css(PAL[i]) : EXTRA[(i - PAL.length) % EXTRA.length];

  async function pageKpi(id, focusArg) {
    const [tax, S] = await Promise.all([loadTax(), loadSer(id)]);
    const meta = (tax.kpis || []).find(k => k.id === id) || { name: S.name, definition: S.definition };
    const tks = Object.keys(S.companies || {}).sort();
    if (focusArg && S.companies[focusArg]) ST.focus = focusArg;
    if (ST.focus && !S.companies[ST.focus]) ST.focus = null;
    page.append(el("div", { class: "ph" }, el("div", {},
      el("div", { class: "sx-crumb", style: "font-size:12px;color:var(--muted);display:flex;gap:6px" }, el("a", { href: "#kpis" }, "Standardized KPIs"), "›", meta.family || ""),
      el("h1", {}, meta.name), el("div", { class: "sub" }, meta.definition, " ", el("span", { class: "faint" }, `${tks.length} compan${tks.length === 1 ? "y reports" : "ies report"} it in investor materials.`)))));
    const host = el("div", { style: "display:flex;flex-direction:column;gap:14px" });
    page.append(host);
    const draw = () => { host.innerHTML = ""; drawKpi(host, id, S, meta, tks); };
    draw();
  }

  function groupMembers(S, tks) {
    const f = ST.focus && S.companies[ST.focus];
    if (ST.group === "custom") { const set = new Set(ST.custom.filter(t => S.companies[t])); if (ST.focus) set.add(ST.focus); return [...set]; }
    if (!f || ST.group === "all") return tks;
    if (ST.group === "sector") return tks.filter(t => S.companies[t].sector === f.sector);
    const ind = tks.filter(t => S.companies[t].industry === f.industry);
    return ind.length >= 2 ? ind : tks.filter(t => S.companies[t].sector === f.sector);
  }

  function drawKpi(host, id, S, meta, tks) {
    const K = ST.K;
    const unit = S.unit;
    // controls
    const focusSel = el("select", { onchange: e => { ST.focus = e.target.value || null; history.replaceState(null, "", `#kpis-${id}${ST.focus ? "/" + ST.focus.toLowerCase() : ""}`); save(); redraw(); } },
      el("option", { value: "" }, "No focus company"), tks.map(t => el("option", { value: t, selected: t === ST.focus ? "" : null }, `${t} · ${S.companies[t].company || ""}`)));
    const grpSel = el("select", { onchange: e => { ST.group = e.target.value; save(); redraw(); } },
      [["industry", "Same industry"], ["sector", "Same sector"], ["all", "All companies"], ["custom", "Picked companies"]].map(([v, n]) => el("option", { value: v, selected: v === ST.group ? "" : null }, n)));
    const dl = el("datalist", { id: "ks-dl" }, tks.map(t => el("option", { value: t }, S.companies[t].company || t)));
    const addIn = el("input", { list: "ks-dl", placeholder: "Add ticker", onkeydown: e => { if (e.key !== "Enter") return; const t = e.target.value.trim().toUpperCase(); if (!S.companies[t]) { toast(`${t || "That ticker"} does not report ${meta.name}`); return; } if (!ST.custom.includes(t)) ST.custom.push(t); ST.group = "custom"; save(); redraw(); } });
    const redraw = () => { host.innerHTML = ""; drawKpi(host, id, S, meta, tks); };
    const ctl = el("div", { class: "ks-ctl" },
      el("label", {}, "Focus", focusSel), el("label", {}, "Peers", grpSel), addIn, dl,
      seg([["Q", "Quarterly"], ["A", "Annual"]], K, v => { ST.K = v; save(); redraw(); }),
      seg([["fiscal", "Fiscal periods"], ["calendar", "Calendar periods"]], ST.align, v => { ST.align = v; save(); redraw(); }),
      seg([["value", "Value"], ["yoy", isPct(unit) ? "YoY change" : "YoY growth"]], ST.view, v => { ST.view = v; save(); redraw(); }),
      el("label", { title: "Series a company reported on an earlier basis or under another definition. They are separate rows, never joined to the current series." },
        el("input", { type: "checkbox", checked: ST.alts ? "" : null, onchange: e => { ST.alts = e.target.checked; save(); redraw(); } }), "Other bases"));
    host.append(ctl);

    const members = groupMembers(S, tks);
    if (ST.group === "custom" && members.length) {
      host.append(el("div", { class: "sx-chips", style: "display:flex;gap:6px;flex-wrap:wrap" }, members.map(t => el("button", { class: "chip link", title: "Remove", onclick: () => { ST.custom = ST.custom.filter(x => x !== t); if (ST.focus === t) ST.focus = null; save(); redraw(); } }, t, " ×"))));
    }
    // rows: one per company series (and per older-basis series when shown)
    const series = [];
    members.forEach(t => {
      const c = S.companies[t];
      series.push({ tk: t, s: c, label: t, alt: false });
      if (ST.alts) (c.alts || []).forEach(a => series.push({ tk: t, s: { ...a, factor: c.factor }, label: `${t} · ${a.basis}`, alt: true }));
    });
    // cells keyed by period
    const colMap = new Map();
    series.forEach(r => {
      const pts = r.s[K] || [];
      r.cells = {};
      pts.forEach((p, i) => {
        const kk = keyOf(p, K);
        const g = yoyOf(pts, i, unit);
        const yv = ST.view === "yoy" ? g : p[1];
        r.cells[kk.k] = { p, y: yv, g, text: ST.view === "yoy" ? fmtG(g, unit) : fmtV(p[1], unit), sub: `${p[2] || ""} · ended ${fdate(p[0])}` };
        if (!colMap.has(kk.k)) colMap.set(kk.k, kk.o);
      });
    });
    const allCols = [...colMap.entries()].sort((a, b) => a[1] - b[1]).map(([k, o]) => ({ k, o }));
    const used = allCols.filter(c => series.some(r => r.cells[c.k] && r.cells[c.k].y != null));
    const n = K === "Q" ? ST.n : Math.min(ST.n, 10);
    const cols = used.slice(-n);
    // percentile inside the group of each company's latest value in the shown periods (current-basis rows only; fiscal
    // calendars differ, so the latest values are compared rather than one column)
    const pctl = {};
    const latestOf = r => { for (let i = cols.length - 1; i >= 0; i--) { const c = r.cells[cols[i].k]; if (c && c.y != null) return { v: c.y, k: cols[i].k }; } return null; };
    const lv = series.filter(r => !r.alt).map(r => ({ tk: r.tk, l: latestOf(r) })).filter(x => x.l);
    lv.forEach(x => { const below = lv.filter(z => z.l.v < x.l.v).length, eq = lv.filter(z => z.l.v === x.l.v).length - 1; pctl[x.tk] = lv.length > 1 ? { p: Math.round((below + eq / 2) / (lv.length - 1) * 100), k: x.l.k } : null; });
    // colors: focus first
    const order = [...new Set(series.map(r => r.tk))].sort((a, b) => (b === ST.focus) - (a === ST.focus) || a.localeCompare(b));
    series.forEach(r => { r.color = colorAt(order.indexOf(r.tk)); r.focus = r.tk === ST.focus; });

    const chartHost = el("div", { class: "ks-chart" });
    const shown = series.filter(r => !r.alt).slice(0, 14).concat(series.filter(r => r.alt).slice(0, 6));
    const sortLast = r => { for (let i = cols.length - 1; i >= 0; i--) { const c = r.cells[cols[i].k]; if (c && c.y != null) return c.y; } return -Infinity; };
    const tableRows = [...series].sort((a, b) => (b.focus - a.focus) || (a.alt - b.alt) || (sortLast(b) - sortLast(a)));
    const right = el("span", { class: "chip acc" }, "From investor materials");
    const titleTxt = `${meta.name}${ST.view === "yoy" ? (isPct(unit) ? ", change vs a year earlier" : ", growth vs a year earlier") : ""} · ${members.length} compan${members.length === 1 ? "y" : "ies"}`;
    host.append(panel(titleTxt, right, el("div", { class: "bd" }, chartHost),
      el("div", { class: "src" }, `Each line is one company's own KPI series, converted to ${unit} where the company prints another scale. ${ST.align === "calendar" ? "Periods are placed in the calendar quarter or year that holds most of the period." : "Periods follow each company's fiscal calendar, so the same label can cover different months for different companies."} Click any dot for the source page.`)));
    requestAnimationFrame(() => chart(chartHost, cols, shown, unit, (r, cell) => openPoint(S, r.tk, r.s, cell.p, K)));

    // table
    const t = el("table", { class: "ks-tbl" });
    const lastK = cols.at(-1)?.k;
    t.append(el("thead", {}, el("tr", {}, el("th", { class: "l" }, "Company"), cols.map((c, i) => el("th", { class: i < cols.length - 3 ? "ks-hide-sm" : "" }, c.k)),
      el("th", { title: isPct(unit) ? "Latest value minus the value a year earlier" : "Latest value over the value a year earlier, minus one" }, "YoY"),
      el("th", { title: `Percentile of each company's latest ${ST.view === "yoy" ? "change" : "value"} among the group (100 is the highest)` }, "Pctl"))));
    const tb = el("tbody");
    tableRows.forEach(r => {
      const c = r.s;
      const tr = el("tr", { class: (r.focus ? "focus " : "") + (r.alt ? "alt" : "") },
        el("td", { class: "l co" }, el("a", { href: `#company-${r.tk.toLowerCase()}`, title: "Open the company" }, r.alt ? null : miniLogo(r.tk, 16), r.label),
          el("span", { class: "sub", title: `${r.alt ? "" : (c.company || "") + ": "}${c.name}` }, r.alt ? c.name : `${c.company ? c.company + " · " : ""}${c.name}${c.factor && c.factor !== 1 ? ` (${c.unit}, converted)` : ""}`)));
      cols.forEach((col, i) => {
        const cell = r.cells[col.k];
        const cls = i < cols.length - 3 ? "ks-hide-sm" : "";
        if (!cell || cell.y == null) { tr.append(el("td", { class: "na " + cls }, "–")); return; }
        tr.append(el("td", { class: cls, title: cell.sub }, numBtn(cell.text, () => openPoint(S, r.tk, r.s, cell.p, K))));
      });
      // latest point of this series and its year-over-year change
      let li = -1; const pts = c[K] || [];
      for (let i = pts.length - 1; i >= 0; i--) if (pts[i][1] != null) { li = i; break; }
      const g = li >= 0 ? yoyOf(pts, li, unit) : null;
      tr.append(el("td", { title: li >= 0 ? `${pts[li][2] || ""} vs a year earlier` : "" }, fmtG(g, unit)));
      const pq = !r.alt ? pctl[r.tk] : null, pc = pq ? pq.p : null;
      tr.append(el("td", { title: pq ? `Latest value, ${pq.k}` : "" }, pc == null ? "–" : el("span", { class: "ks-pc " + (pc >= 67 ? "hi" : pc <= 33 ? "lo" : "") }, ord(pc))));
      tb.append(tr);
    });
    t.append(tb);
    const nSeg = seg([[6, "6"], [10, "10"], [20, "20"]], ST.n, v => { ST.n = +v; save(); redraw(); });
    host.append(panel("Peer table", nSeg, el("div", { class: "scroll" }, t),
      el("div", { class: "src" }, `Pctl is the percentile of each company's latest value within the group${meta.higher_is_better === false ? "; for this KPI a lower value is usually better" : ""}. Company definitions can differ; the row note gives each company's own name for the measure. Rows in italics are series on an older basis or another definition, kept separate.`)));

    // how each company's id was mapped
    if (ST.focus) mappingPanel(host, ST.focus, id);
  }

  async function mappingPanel(host, tk, onlyId) {
    const tf = await loadT(tk);
    if (!tf || !host.isConnected) return;
    const rows = (tf.map || []).filter(r => !onlyId || r.canonical === onlyId);
    const t = el("table", { class: "ks-tbl ks-map" }, el("thead", {}, el("tr", {}, ["Company KPI", "Canonical", "Scope", "Role", "Confidence", "Method", "Why"].map(h => el("th", { class: "l" }, h)))));
    const tb = el("tbody");
    rows.slice(0, 200).forEach(r => tb.append(el("tr", {}, el("td", {}, r.name, el("span", { class: "sub" }, [r.category, r.subcategory].filter(Boolean).join(" › "))),
      el("td", {}, el("a", { href: `#kpis-${r.canonical}/${tk.toLowerCase()}` }, r.canonical)), el("td", {}, r.scope + (r.member ? ` (${r.member})` : "")),
      el("td", {}, r.role === "primary" ? "Compared" : r.role === "alt" ? `Separate series: ${r.basis || r.era || ""}` : r.role === "rejected" ? "Rejected" : "–"),
      el("td", {}, (r.confidence * 100).toFixed(0) + "%"), el("td", {}, r.method === "rules" ? "Rules" : "Model, reviewed by rules"), el("td", { class: "rs" }, r.reason))));
    t.append(tb);
    host.append(el("details", { class: "panel ks-det" }, el("summary", {}, `How ${tk}'s KPIs map${onlyId ? " to this measure" : ""} (${rows.length})`), el("div", { class: "scroll", style: "padding:0 8px 8px" }, t)));
  }

  /* ---------- landing: the taxonomy with coverage ---------- */
  async function pageIndex() {
    const [tax, lat] = await Promise.all([loadTax(), loadLatest()]);
    const kp = (tax.kpis || []).filter(k => k.family !== "Segments");
    const covered = kp.filter(k => (k.coverage?.companies || 0) > 0);
    const nCos = new Set(Object.values(lat.kpis || {}).flatMap(o => Object.keys(o))).size;
    page.append(el("div", { class: "ph" }, el("div", {}, el("div", { class: "eyebrow" }, "Standardized KPIs"), el("h1", {}, "KPI peers"),
      el("div", { class: "sub" }, `Company operating KPIs from investor materials, mapped to ${kp.length} standard measures so they can be compared across companies. ${covered.length} measures have at least one company; ${nCos} companies have at least one standardized series. `,
        el("span", { class: "faint" }, tax.method)))));
    const q = el("input", { placeholder: "Filter measures or tickers", style: "font:inherit;font-size:13px;padding:6px 10px;border:1px solid var(--line);border-radius:8px;background:var(--surface);color:var(--ink);width:min(320px,100%)" });
    const body = el("div");
    let showAll = false;
    const allBox = el("label", {}, el("input", { type: "checkbox", onchange: e => { showAll = e.target.checked; draw(); } }), `Show the ${kp.length - covered.length} measures no company maps to yet`);
    page.append(el("div", { class: "ks-ctl" }, q, allBox), body);
    const draw = () => {
      body.innerHTML = "";
      const v = q.value.trim().toLowerCase();
      const fams = [...new Set(kp.map(k => k.family))];
      fams.forEach(f => {
        const items = kp.filter(k => k.family === f && (showAll || (k.coverage?.companies || 0) > 0) && (!v || k.name.toLowerCase().includes(v) || k.id.includes(v) || Object.keys(lat.kpis?.[k.id] || {}).some(t => t.toLowerCase() === v)));
        if (!items.length) return;
        body.append(el("div", { class: "eyebrow ks-fam" }, f));
        body.append(el("div", { class: "ks-grid" }, items.map(k => {
          const n = k.coverage?.companies || 0;
          const inds = Object.entries(k.coverage?.industries || {}).sort((a, b) => b[1] - a[1]).slice(0, 2).map(([i, c]) => `${i} ${c}`).join(", ");
          return el(n ? "a" : "div", { class: "ks-card" + (n ? "" : " off"), href: n ? `#kpis-${k.id}` : null, title: k.definition },
            el("span", { class: "nm" }, k.name), el("span", { class: "s" }, n ? `${n} compan${n === 1 ? "y" : "ies"}${inds ? " · " + inds : ""}` : "No company mapped yet"),
            el("span", { class: "s faint" }, k.unit));
        })));
      });
    };
    q.oninput = draw;
    draw();
  }

  async function render(arg) {
    if (!arg) return pageIndex();
    const [id, tk] = String(arg).split("/");
    return pageKpi(id, tk ? tk.toUpperCase() : null);
  }
  window.FU_PAGES.kpis = render;

  /* ---------- company tab panel ---------- */
  async function companyPanel(host, tk) {
    const slot = el("div");
    host.append(slot);
    const [tf, lat, tax] = await Promise.all([loadT(tk), loadLatest(), loadTax().catch(() => ({ kpis: [] }))]);
    if (!tf || !Object.values(tf.series || {}).some(s => s.current !== false) || !slot.isConnected) return;
    const meta = Object.fromEntries((tax.kpis || []).map(k => [k.id, k]));
    const old = Object.values(tf.series).filter(s => s.current === false).length;
    const rows = Object.entries(tf.series).filter(([, s]) => s.current !== false).map(([cid, s]) => {
      const L = (lat.kpis || {})[cid] || {};
      const me = L[tk];
      const peersInd = Object.entries(L).filter(([t, x]) => x.ind === tf.industry);
      const peers = peersInd.length >= 3 ? peersInd : Object.entries(L).filter(([t, x]) => x.sec === tf.sector);
      const scope = peersInd.length >= 3 ? "industry" : "sector";
      const vs = peers.map(([t, x]) => x.q?.[1] ?? x.a?.[1]).filter(v => v != null);
      const v0 = me?.q?.[1] ?? me?.a?.[1];
      const pc = vs.length > 1 && v0 != null ? Math.round((vs.filter(v => v < v0).length + (vs.filter(v => v === v0).length - 1) / 2) / (vs.length - 1) * 100) : null;
      return { cid, s, m: meta[cid] || { name: cid, unit: s.unit }, me, pc, n: vs.length, scope };
    }).sort((a, b) => (a.m.family || "").localeCompare(b.m.family || "") || a.m.name.localeCompare(b.m.name));
    const t = el("table", { class: "ks-tbl" }, el("thead", {}, el("tr", {}, el("th", { class: "l" }, "Measure"), el("th", { class: "l ks-hide-sm" }, "Company's name for it"), el("th", {}, "Latest"), el("th", {}, "YoY"), el("th", { title: "Percentile among companies in the same industry (or sector when fewer than three) that report it" }, "Peer pctl"), el("th", {}, ""))));
    const tb = el("tbody");
    const S0 = { docs: tf.docs };
    rows.forEach(r => {
      const K = r.s.Q.length ? "Q" : "A";
      const pts = r.s[K];
      const p = pts.at(-1);
      const unit = r.m.unit;
      const g = p ? yoyOf(pts, pts.length - 1, unit) : null;
      tb.append(el("tr", {},
        el("td", { class: "l" }, el("a", { href: `#kpis-${r.cid}/${tk.toLowerCase()}`, title: "Compare with peers" }, r.m.name)),
        el("td", { class: "l ks-hide-sm", style: "white-space:normal;color:var(--muted)" }, r.s.name, r.s.alts?.length ? el("span", { class: "sub" }, `${r.s.alts.length} older or alternative series kept separate`) : null),
        el("td", { title: p ? `${p[2] || ""} · ended ${fdate(p[0])}` : "" }, p ? numBtn(fmtV(p[1], unit), () => openPoint(S0, tk, r.s, p, K)) : "–", p ? el("span", { class: "sub" }, p[2] || "") : null),
        el("td", {}, fmtG(g, unit)),
        el("td", { title: r.pc != null ? `${r.n} companies in the ${r.scope}` : "" }, r.pc == null ? "–" : el("span", { class: "ks-pc " + (r.pc >= 67 ? "hi" : r.pc <= 33 ? "lo" : "") }, ord(r.pc))),
        el("td", {}, el("a", { href: `#kpis-${r.cid}/${tk.toLowerCase()}`, class: "faint", style: "font-size:11.5px" }, "Peers →"))));
    });
    t.append(tb);
    slot.append(panel("Standardized KPIs", el("a", { class: "chip acc", href: "#kpis" }, "All measures"), el("div", { class: "scroll" }, t),
      el("div", { class: "src" }, `${rows.length} of this company's current KPIs map to a standard measure${old ? ` (${old} more the company no longer reports are on the peer pages)` : ""}. Each one is a single series as the company reports it; click a value for the source page, a measure for the peer comparison.`)));
  }
  window.FU_KPISTD = { companyPanel };
})();
