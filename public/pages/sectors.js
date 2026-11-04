// Sectors page. Registers window.FU_PAGES.sectors(arg); app.js route() uses it in place of the built-in page when set.
// #sectors is the overview, #sectors-<slug> one sector or industry. Data: sectors.json, sectors/{slug}.json,
// sectors/{slug}.comp.json (per-company inputs behind every aggregate) and sectors/notes.json, built by etl/sectors.py.
window.FU_PAGES = window.FU_PAGES || {};
(() => {
  "use strict";
  const S = { ov: null, g: {}, comp: {}, notes: null };
  const ld = (key, path) => S[key] ? Promise.resolve(S[key]) : getJSON(path).then(j => (S[key] = j));
  const loadOv = () => ld("ov", "sectors.json");
  const loadNotes = () => S.notes ? Promise.resolve(S.notes) : getJSON("sectors/notes.json").then(j => (S.notes = j)).catch(() => (S.notes = { notes: [] }));
  const loadGroup = slug => S.g[slug] ? Promise.resolve(S.g[slug]) : getJSON(`sectors/${slug}.json`).then(j => (S.g[slug] = j));
  const loadComp = slug => S.comp[slug] ? Promise.resolve(S.comp[slug]) : getJSON(`sectors/${slug}.comp.json`).then(j => (S.comp[slug] = j));

  /* ---------- styles (app.css variables only, so any theme applies) ---------- */
  if (!document.getElementById("sx-css")) document.head.append(el("style", { id: "sx-css" }, `
.sx-n{border:0;background:none;padding:0;font:inherit;color:inherit;cursor:pointer;font-variant-numeric:tabular-nums;text-decoration:underline dotted var(--faint);text-underline-offset:3px}
.sx-n:hover{color:var(--accent);text-decoration-color:var(--accent)}
.sx-n.up{color:var(--up)}.sx-n.down{color:var(--down)}
.sx-tbl td.nm{min-width:180px}
.sx-tbl tr.ind td:first-child{padding-left:26px}
.sx-tbl tr.ind td{font-size:12.5px}
.sx-tbl tr.sec td{font-weight:500}
.sx-tog{border:0;background:none;cursor:pointer;color:var(--faint);width:18px;padding:0;font-size:11px}
.sx-range{position:relative;height:16px;min-width:90px}
.sx-range .bar{position:absolute;top:7px;left:0;right:0;height:2px;background:var(--line2);border-radius:2px}
.sx-range .med{position:absolute;top:3px;width:1px;height:10px;background:var(--faint)}
.sx-range .dot{position:absolute;top:3px;width:10px;height:10px;margin-left:-5px;border-radius:50%;background:var(--accent);border:2px solid var(--surface)}
.sx-tiles{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px}
.sx-tile{border:1px solid var(--line);border-radius:10px;padding:10px 12px;background:var(--surface);display:flex;flex-direction:column;gap:2px;min-width:0}
.sx-tile .l{font-size:11.5px;color:var(--muted)}
.sx-tile .v{font-size:18px;font-weight:600}
.sx-tile .s{font-size:11px;color:var(--faint)}
.sx-note{border:1px solid var(--line);border-radius:10px;padding:12px 14px;display:flex;flex-direction:column;gap:6px;background:var(--surface)}
.sx-note p{margin:0;line-height:1.55}
.sx-note .fig{border:0;background:var(--accent-soft);color:var(--accent);border-radius:4px;padding:0 4px;font:inherit;font-variant-numeric:tabular-nums;cursor:pointer}
.sx-heat{display:grid;gap:2px;font-size:11px;min-width:640px}
.sx-heat div{display:grid;place-items:center;height:30px;border-radius:3px;font-variant-numeric:tabular-nums}
.sx-heat .h{color:var(--faint);font-size:10.5px;text-align:center;line-height:1.15;height:38px}
.sx-heat .rl{justify-content:start;padding-left:4px;color:var(--ink);cursor:pointer}
.sx-heat .c{cursor:pointer}
.sx-heat .c:hover{outline:1px solid var(--accent)}
.sx-crumb{font-size:12px;color:var(--muted);display:flex;gap:6px;flex-wrap:wrap;align-items:center}
.sx-chips{display:flex;gap:6px;flex-wrap:wrap}
.sx-sort{cursor:pointer;user-select:none}
.sx-sort.on{color:var(--ink)}
.sx-dtbl td,.sx-dtbl th{padding:6px 8px;font-size:12px}
.sx-dtbl td.l{cursor:pointer}
.sx-dtbl td.l:hover{color:var(--accent)}
.sx-sum{display:flex;gap:16px;flex-wrap:wrap;font-size:12.5px}
.sx-sum b{font-variant-numeric:tabular-nums}
.sx-map td{font-size:12px}
details.sx-det>summary{cursor:pointer;padding:12px 16px;font-weight:600;font-size:13px}
@media (max-width:860px){.sx-hide-sm{display:none}.sx-tbl td.nm{min-width:140px}}
`));

  /* ---------- formatting ---------- */
  const fmtU = (v, u, d) => v == null || !isFinite(v) ? "–" : u === "pct" ? fpct(v, d ?? 1) : u === "x" ? fx(v) : u === "USD" ? fmtMoney(v) : u === "bp" ? fnum(v, 1) + " bp" : fnum(v, d ?? 2);
  const qlab = k => k ? `Q${k[5]} ${k.slice(0, 4)}` : "";
  const qdate = k => new Date(Date.UTC(+k.slice(0, 4), +k[5] * 3, 0));
  const ddate = s => new Date(s + "T00:00:00Z");
  const nearestQ = (qs, d) => { let best = qs[0], bd = Infinity; for (const k of qs) { const x = Math.abs(qdate(k) - d); if (x < bd) { bd = x; best = k; } } return best; };
  const isFin = sec => sec === "Financials" || sec === "Real Estate";
  const N = (text, onclick, extra = "") => el("button", { class: "sx-n " + extra, onclick: e => { e.stopPropagation(); onclick(); }, title: "Open derivation" }, text);
  const liIdx = ns => { const ix = []; ns.forEach((n, i) => { if (n) ix.push(i); }); if (!ix.length) return null; const i = ix.at(-1); return ix.length > 1 && ns[i] < .5 * ns[ix.at(-2)] ? ix.at(-2) : i; };
  // one reference quarter per group (the latest with most of the group reported), so every tile reads the same period
  const refIdx = g => liIdx(g.agg.rev_growth.n.map((n, i) => n || g.agg.ni_growth.n[i]));
  const lastOf = (g, k) => { const a = g.agg[k]; let i = refIdx(g); if (i == null) return null; if (a.agg[i] == null && a.med[i] == null) { const j = liIdx(a.n); if (j == null) return null; i = j; } return { i, q: g.quarters[i], v: a.agg[i], med: a.med[i], cw: a.cw[i], n: a.n[i] }; };
  const fmtBeta = (b, unit) => b == null ? "–" : unit === "bp" ? (b * 100).toFixed(3) + "% per bp" : unit === "%" ? fnum(b, 2) + "× per 1%" : unit === "k" ? (b * 1e4).toFixed(2) + "% per 100k" : fnum(b, 4);
  const tkName = t => SHORT[t] || t;
  const sortRows = (rows, key, desc) => rows.sort((a, b) => { const x = a[key], y = b[key]; if (x == null && y == null) return 0; if (x == null) return 1; if (y == null) return -1; return typeof x === "string" ? (desc ? y.localeCompare(x) : x.localeCompare(y)) : desc ? y - x : x - y; });

  /* ---------- drawer ---------- */
  function openPanel({ eyebrow, title, value, sub, links = [] }) {
    hideTip();
    DR.cell = { sector: true };
    const dh = $("#dh"), db = $("#db");
    dh.innerHTML = ""; db.innerHTML = "";
    dh.append(
      el("div", { class: "row" }, el("span", { class: "eyebrow" }, eyebrow || "Derivation"),
        el("div", { class: "x", style: "display:flex;gap:6px" }, ...links.map(([href, label]) => el("a", { class: "btn", href, target: "_blank", rel: "noopener" }, label, svgUse("i-ext"))),
          el("button", { class: "iconbtn", onclick: closeSource, "aria-label": "Close (Esc)" }, svgUse("i-x")))),
      el("div", { class: "row" }, el("h2", {}, title)),
      el("div", { class: "row" }, value != null ? el("span", { class: "chip acc mono", style: "font-size:13.5px;height:26px" }, value) : null, sub ? el("span", { class: "muted", style: "font-size:12.5px" }, sub) : null));
    drawer.classList.add("open"); drawer.setAttribute("aria-hidden", "false");
    if (innerWidth < 860) scrim.hidden = false;
    return db;
  }
  const loadingIn = host => host.append(el("div", { class: "faint", style: "font-size:12px" }, "Loading…"));

  /* ---------- opening company facts in the filing viewer ---------- */
  const FACT = { rev_q: "Revenue", rev_py: "Revenue", ni_q: "Net income", ni_py: "Net income", rev_ttm: "Revenue", gp_ttm: "Gross profit", oi_ttm: "Operating income", ni_ttm: "Net income", ocf_ttm: "Operating cash flow", capex_ttm: "Capex", da_ttm: "D&A", tax_ttm: "Income tax", pretax_ttm: "Pretax income", ltd: "Long-term debt", cash: "Cash & equivalents", eq: "Shareholders' equity", inv: "Inventory", cor_ttm: "Cost of revenue", sh: "Diluted shares" };
  const FIELD_NAME = { rev_q: "Revenue (quarter)", rev_py: "Revenue (year-ago quarter)", ni_q: "Net income (quarter)", ni_py: "Net income (year-ago quarter)", rev_ttm: "Revenue TTM", gp_ttm: "Gross profit TTM", oi_ttm: "Operating income TTM", ni_ttm: "Net income TTM", ocf_ttm: "Operating cash flow TTM", capex_ttm: "Capex TTM", da_ttm: "D&A TTM", tax_ttm: "Income tax TTM", pretax_ttm: "Pretax income TTM", ltd: "Long-term debt", cash: "Cash & equivalents", eq: "Shareholders' equity", inv: "Inventory", cor_ttm: "Cost of revenue TTM", sh: "Diluted shares", px: "Month-end close (unadjusted)", mcap: "Market cap" };
  async function openFact(slug, tk, field, q) {
    const comp = await loadComp(slug);
    const row = compRow(comp, tk, q);
    if (field === "mcap" || field === "px") return openMcap(slug, tk, q);
    const label = FACT[field];
    let end = field === "rev_py" || field === "ni_py" ? row?.end_py : row?.end;
    const qq = field.endsWith("_py") ? qshift(q, -4) : q;
    try { await ensureCompany(tk); } catch (e) { toast(`${tk}: ${e.message}`); return; }
    const m = model(tk);
    if (field.endsWith("_ttm")) {
      const ser = m.series(label, "Q").filter(x => x.end <= end).slice(-4);
      const tot = ser.reduce((a, x) => a + x.cell.v, 0);
      if (ser.length === 4 && dayDiff(ser[0].end, ser[3].end) < 300) return openSource({ v: tot, tk, label, unit: ser[0].cell.unit, kind: "TTM", end: ser[3].end, calc: { formula: `Sum of four quarters of ${label}`, inputs: ser.map(x => x.cell) }, sib: () => [] });
      const lbl = label === "Gross profit" ? "Revenue" : label;
      const c = m.metric(lbl, "Q", end); if (c) return openSource(c);
    } else {
      const c = m.metric(label, "Q", end);
      if (c) return openSource(c);
    }
    toast(`${tk} ${label} for ${qlab(qq)} is not in the company's statements`);
  }
  function qshift(k, n) { const i = +k.slice(0, 4) * 4 + (+k[5] - 1) + n; return `${Math.floor(i / 4)}Q${i % 4 + 1}`; }
  function compRow(comp, tk, q) {
    const i = comp.quarters.indexOf(q); const a = comp.data[tk]?.[i]; if (!a) return null;
    const o = {}; comp.fields.forEach((f, j) => { o[f] = a[j]; }); return o;
  }
  async function openMcap(slug, tk, q) {
    const comp = await loadComp(slug); const r = compRow(comp, tk, q); const px = comp.px[tk] || {};
    const db = openPanel({ eyebrow: "Calculated value", title: `${tkName(tk)} · market cap, end of ${qlab(q)}`, value: fmtMoney(r?.mcap), sub: "Today's market cap scaled by unadjusted price and diluted shares", links: px.url ? [[px.url.replace("query2.finance.yahoo.com/v8/finance/chart/", "finance.yahoo.com/quote/").split("?")[0] + "/history", "Price history"]] : [] });
    const box = el("div", { class: "calc-box" }, el("div", { class: "eyebrow" }, "Formula"),
      el("div", { class: "eq" }, "Market cap today × (close at quarter end × diluted shares in the quarter) / (latest close × latest diluted shares)"),
      el("div", { class: "in", onclick: () => location.hash = "company-" + tk.toLowerCase() }, el("span", {}, el("b", {}, "Market cap today"), " ", el("span", { class: "muted" }, "price × shares outstanding, as on the company page")), el("span", { class: "mono" }, fmtMoney(comp.mcap_now[tk]))),
      el("div", { class: "in", onclick: () => openFact(slug, tk, "sh", q) }, el("span", {}, el("b", {}, "Diluted shares"), " ", el("span", { class: "muted" }, "weighted average for the quarter, from the filing")), el("span", { class: "mono" }, r?.sh ? (r.sh / 1e6).toLocaleString("en-US", { maximumFractionDigits: 0 }) + "M" : "–")),
      el("div", { class: "in", style: "cursor:default" }, el("span", {}, el("b", {}, "Month-end close"), " ", el("span", { class: "muted" }, `${qdate(q).toISOString().slice(0, 7)}, as printed then (split-adjusted closes multiplied back by later splits)`)), el("span", { class: "mono" }, r?.px ? "$" + fnum(r.px, 2) : "–")));
    db.append(box);
    if ((px.splits || []).length) db.append(el("div", { class: "note" }, "Splits used to undo the adjustment: " + px.splits.map(([d, x]) => `${fdate(d)} ${x >= 1 ? x + "-for-1" : "1-for-" + Math.round(1 / x)}`).join(", ") + "."));
    db.append(el("div", { class: "note" }, `Monthly closes from the Yahoo Finance chart API, retrieved ${px.at ? fdate(px.at.slice(0, 10)) : "–"}. Weighted diluted shares stand in for shares outstanding at quarter end; scaling from today's market cap keeps share classes consistent.`));
  }

  /* ---------- aggregate metric derivations ---------- */
  const fcf = r => r.ocf_ttm != null && r.capex_ttm != null ? r.ocf_ttm - r.capex_ttm : null;
  const nopatIc = r => { if (r.oi_ttm == null || r.eq == null) return [null, null]; let t = r.tax_ttm != null && r.pretax_ttm > 0 ? r.tax_ttm / r.pretax_ttm : null; if (t == null || t < 0 || t > .5) t = .21; return [r.oi_ttm * (1 - t), r.eq + (r.ltd || 0) - (r.cash || 0), t]; };
  const posRev = r => r.rev_ttm > 0 ? r.rev_ttm : null;
  const DEF = {
    rev_growth: { f: "Σ revenue in the quarter / Σ revenue in the same quarter a year earlier − 1", num: r => r.rev_q, den: r => r.rev_py, nf: ["rev_q"], df: ["rev_py"], growth: true, pos: true },
    ni_growth: { f: "Σ net income in the quarter / Σ net income a year earlier − 1", num: r => r.ni_q, den: r => r.ni_py, nf: ["ni_q"], df: ["ni_py"], growth: true, pos: true, cpos: true },
    gm: { f: "Σ gross profit TTM / Σ revenue TTM (gross profit = revenue − cost of revenue where not reported)", num: r => r.gp_ttm, den: posRev, nf: ["gp_ttm"], df: ["rev_ttm"] },
    om: { f: "Σ operating income TTM / Σ revenue TTM", num: r => r.oi_ttm, den: posRev, nf: ["oi_ttm"], df: ["rev_ttm"] },
    nm: { f: "Σ net income TTM / Σ revenue TTM", num: r => r.ni_ttm, den: posRev, nf: ["ni_ttm"], df: ["rev_ttm"] },
    fcfm: { f: "Σ (operating cash flow − capex) TTM / Σ revenue TTM", num: fcf, den: posRev, nf: ["ocf_ttm", "capex_ttm"], df: ["rev_ttm"] },
    roic: { f: "Σ NOPAT / Σ invested capital. NOPAT = operating income TTM × (1 − tax rate); tax rate = income tax / pretax income TTM (21% when outside 0 to 50%); invested capital = equity + long-term debt − cash, positive only", num: r => nopatIc(r)[0], den: r => { const ic = nopatIc(r)[1]; return ic > 0 ? ic : null; }, nf: ["oi_ttm", "tax_ttm", "pretax_ttm"], df: ["eq", "ltd", "cash"] },
    roe: { f: "Σ net income TTM / Σ shareholders' equity (positive equity only)", num: r => r.ni_ttm, den: r => r.eq > 0 ? r.eq : null, nf: ["ni_ttm"], df: ["eq"] },
    nd_ebitda: { f: "Σ (long-term debt − cash) / Σ (operating income + D&A) TTM, positive EBITDA only", num: r => r.ltd != null ? r.ltd - (r.cash || 0) : null, den: r => { const e = r.oi_ttm != null && r.da_ttm != null ? r.oi_ttm + r.da_ttm : null; return e > 0 ? e : null; }, nf: ["ltd", "cash"], df: ["oi_ttm", "da_ttm"] },
    de: { f: "Σ long-term debt / Σ shareholders' equity (positive equity only)", num: r => r.ltd, den: r => r.eq > 0 ? r.eq : null, nf: ["ltd"], df: ["eq"] },
    pe: { f: "Σ market cap / Σ net income TTM (all companies with both; the median uses positive earnings only)", num: r => r.mcap, den: r => r.ni_ttm, nf: ["mcap"], df: ["ni_ttm"], pos: true, mpos: true },
    ps: { f: "Σ market cap / Σ revenue TTM", num: r => r.mcap, den: posRev, nf: ["mcap"], df: ["rev_ttm"], mpos: true },
    pfcf: { f: "Σ market cap / Σ free cash flow TTM (all with both; the median uses positive FCF only)", num: r => r.mcap, den: fcf, nf: ["mcap"], df: ["ocf_ttm", "capex_ttm"], pos: true, mpos: true },
    pct_rev_up: { f: "Share of companies whose quarterly revenue rose from the same quarter a year earlier", count: r => r.rev_py > 0 && r.rev_q != null ? r.rev_q / r.rev_py - 1 : null, nf: ["rev_q", "rev_py"] },
    pct_ni_up: { f: "Share of companies whose quarterly net income rose from a positive year-earlier figure", count: r => r.ni_py > 0 && r.ni_q != null ? r.ni_q / r.ni_py - 1 : null, nf: ["ni_q", "ni_py"] },
    rev_ttm: { f: "Σ revenue TTM", sum: "rev_ttm" }, ni_ttm: { f: "Σ net income TTM", sum: "ni_ttm" }, mcap: { f: "Σ market cap at the quarter end", sum: "mcap" },
  };
  async function openAgg(slug, metric, q, mode = "agg") {
    const g = await loadGroup(slug); const M = g.metrics[metric]; const i = g.quarters.indexOf(q); const A = g.agg[metric];
    const shown = A[mode]?.[i] ?? A.agg[i];
    const db = openPanel({ eyebrow: mode === "med" ? "Median across companies" : mode === "cw" ? "Market-cap-weighted average" : "Aggregate", title: `${g.name} · ${M.name}`, value: fmtU(shown, M.unit), sub: `${qlab(q)} · ${A.n[i]} companies` });
    loadingIn(db);
    const comp = await loadComp(slug).catch(() => null);
    db.innerHTML = "";
    const d = DEF[metric];
    db.append(el("div", { class: "calc-box" }, el("div", { class: "eyebrow" }, "Formula"), el("div", { class: "eq" }, d.f),
      el("div", { class: "sx-sum" }, el("span", {}, "Aggregate ", el("b", {}, fmtU(A.agg[i], M.unit))), A.cw[i] != null ? el("span", {}, "Cap-weighted ", el("b", {}, fmtU(A.cw[i], M.unit))) : null, A.med[i] != null ? el("span", {}, "Median ", el("b", {}, fmtU(A.med[i], M.unit))) : null, el("span", {}, "Companies ", el("b", {}, A.n[i])))));
    if (!comp || comp.quarters.indexOf(q) < 0) { db.append(el("div", { class: "note" }, "Per-company inputs are published for the last eleven years. This quarter is older; the aggregate was computed from the same facts store.")); historyChart(db, g, metric); return; }
    const rows = [];
    for (const tk of g.members) {
      const r = compRow(comp, tk, q); if (!r) continue;
      if (d.sum) { if (r[d.sum] != null) rows.push({ tk, val: r[d.sum], w: r.mcap, r }); continue; }
      if (d.count) { const v = d.count(r); if (v != null) rows.push({ tk, val: v, w: r.mcap, r }); continue; }
      const a = d.num(r), b = d.den(r);
      if (a == null || b == null) continue;
      rows.push({ tk, a, b, val: d.growth ? (b > 0 ? a / b - 1 : null) : (d.mpos && b <= 0 ? null : b ? a / b : null), w: r.mcap, r });
    }
    let summary;
    if (d.sum) { const s = rows.reduce((x, r) => x + r.val, 0); rows.forEach(r => { r.share = r.val / s; }); summary = `Σ = ${fmtU(s, "USD")}`; }
    else if (d.count) { const up = rows.filter(r => r.val > 0).length; summary = `${up} of ${rows.length} growing = ${fpct(rows.length ? up / rows.length : null)}`; }
    else {
      const sa = rows.reduce((x, r) => x + r.a, 0), sb = rows.reduce((x, r) => x + r.b, 0);
      rows.forEach(r => { r.share = d.growth ? (r.a - r.b) / sb : sb ? r.b / sb : null; });
      summary = `Σ numerator ${fmtMoney(sa)} / Σ denominator ${fmtMoney(sb)}${d.growth ? " − 1" : ""} = ${fmtU(d.growth ? sa / sb - 1 : sa / sb, M.unit)}`;
    }
    db.append(el("div", { class: "note" }, summary + ". Click a figure to open the filing it came from."));
    const tb = el("tbody");
    const cols = d.sum || d.count ? [] : [["Numerator", "a", d.nf], ["Denominator", "b", d.df]];
    sortRows(rows, d.growth ? "share" : "w", true);
    if (d.growth) rows.sort((x, y) => Math.abs(y.share || 0) - Math.abs(x.share || 0));
    rows.forEach(r => {
      const tr = el("tr", {},
        el("td", { class: "l", onclick: () => location.hash = "company-" + r.tk.toLowerCase() }, el("b", { class: "mono" }, r.tk), " ", el("span", { class: "muted sx-hide-sm" }, tkName(r.tk))),
        ...cols.map(([, k, fs]) => el("td", { class: "r num" }, fs.length === 1 || (k === "a" && d === DEF.pe) ? N(fmtMoney(r[k]), () => openFact(slug, r.tk, fs[0], q)) : el("span", {}, fmtMoney(r[k]), " ", el("span", { class: "sx-chips", style: "display:inline-flex" }, ...fs.map(f => N(f.replace(/_ttm|_q$/, "").replace("_", " "), () => openFact(slug, r.tk, f, q))))))),
        d.sum ? el("td", { class: "r num" }, N(fmtU(r.val, "USD"), () => d.sum === "mcap" ? openMcap(slug, r.tk, q) : openFact(slug, r.tk, d.sum, q))) : el("td", { class: "r num" }, r.val == null ? "–" : d.count ? N(fsgn(r.val), () => openFact(slug, r.tk, d.nf[0], q)) : fmtU(r.val, M.unit)),
        el("td", { class: "r num sx-hide-sm" }, r.w ? N(fmtMoney(r.w), () => openMcap(slug, r.tk, q)) : "–"),
        d.count ? null : el("td", { class: "r num" }, r.share == null ? "–" : d.growth ? fsgn(r.share, 2) : fpct(r.share)));
      tb.append(tr);
    });
    db.append(el("div", { class: "scroll" }, el("table", { class: "t sx-dtbl" }, el("thead", {}, el("tr", {}, el("th", {}, "Company"), ...cols.map(([n]) => el("th", { class: "r" }, n)), el("th", { class: "r" }, d.sum ? "Value" : "Company value"), el("th", { class: "r sx-hide-sm" }, "Mkt cap"), d.count ? null : el("th", { class: "r" }, d.growth ? "Contribution" : "Share of Σ"))), tb)));
    const excl = g.members.filter(t => !rows.find(r => r.tk === t));
    if (excl.length) db.append(el("div", { class: "note" }, `Not included (missing an input for ${qlab(q)}): ${excl.join(", ")}.`));
    historyChart(db, g, metric);
  }
  function historyChart(db, g, metric) {
    const host = el("div", { style: "min-height:200px" });
    db.append(el("div", { class: "eyebrow" }, "History"), host);
    const M = g.metrics[metric], A = g.agg[metric];
    requestAnimationFrame(() => lineChart(host, [["agg", "Aggregate"], ["med", "Median"]].filter(([k]) => A[k].some(v => v != null)).map(([k, n], j) => ({ name: n, pts: g.quarters.map((q, i) => [qdate(q), A[k][i]]), dash: j ? "4 3" : null })), { height: 200, yfmt: M.unit === "pct" ? v => fpct(v, 0) : M.unit === "x" ? v => v.toFixed(0) + "×" : v => fmtMoney(v), tfmt: v => fmtU(v, M.unit), onClick: d => openAgg(g.slug, metric, nearestQ(g.quarters, d)) }));
  }
  async function openValNow(slug, key) {
    const g = await loadGroup(slug); const v = g.val_now, M = g.metrics[key], rng = g.val_range?.[key];
    const db = openPanel({ eyebrow: "Aggregate today", title: `${g.name} · ${M.name.replace(" (TTM)", "")} today`, value: fmtU(v[key], "x"), sub: `${v.n} companies · market caps from today's prices` });
    const den = { pe: [3, "Net income TTM", "ni_ttm"], ps: [4, "Revenue TTM", "rev_ttm"], pfcf: [5, "FCF TTM", null] }[key];
    db.append(el("div", { class: "calc-box" }, el("div", { class: "eyebrow" }, "Formula"), el("div", { class: "eq" }, `Σ market cap today / Σ ${den[1]} (latest four reported quarters, reported within the last two quarters)`),
      el("div", { class: "sx-sum" }, el("span", {}, "Aggregate ", el("b", {}, fx(v[key]))), el("span", {}, "Median ", el("b", {}, fx(v[key + "_med"]))))));
    if (rng) db.append(el("div", { class: "note" }, `10-year range of the quarterly aggregate (${qlab(rng.from)} to ${qlab(rng.to)}, ${rng.n} quarters): low ${fx(rng.min)} (${qlab(rng.min_q)}), median ${fx(rng.med)}, high ${fx(rng.max)} (${qlab(rng.max_q)}). Today sits at the ${Math.round((rng.pct || 0) * 100)}th percentile. Quarterly values use quarter-end prices.`));
    const tb = el("tbody");
    const rows = v.rows.map(r => ({ tk: r[0], q: r[1], mcap: r[2], d: r[den[0]] })).filter(r => r.d != null).sort((a, b) => b.mcap - a.mcap);
    const sm = rows.reduce((a, r) => a + r.mcap, 0);
    rows.forEach(r => tb.append(el("tr", {}, el("td", { class: "l", onclick: () => location.hash = "company-" + r.tk.toLowerCase() }, el("b", { class: "mono" }, r.tk), " ", el("span", { class: "muted sx-hide-sm" }, tkName(r.tk))),
      el("td", { class: "r num" }, fmtMoney(r.mcap)), el("td", { class: "r num" }, den[2] ? N(fmtMoney(r.d), () => openFact(slug, r.tk, den[2], r.q)) : el("span", {}, fmtMoney(r.d), " ", N("ocf", () => openFact(slug, r.tk, "ocf_ttm", r.q)), " ", N("capex", () => openFact(slug, r.tk, "capex_ttm", r.q)))),
      el("td", { class: "r num" }, r.d > 0 ? fx(r.mcap / r.d) : "n/m"), el("td", { class: "r num sx-hide-sm" }, fpct(r.mcap / sm)), el("td", { class: "faint sx-hide-sm" }, qlab(r.q)))));
    db.append(el("div", { class: "scroll" }, el("table", { class: "t sx-dtbl" }, el("thead", {}, el("tr", {}, el("th", {}, "Company"), el("th", { class: "r" }, "Mkt cap"), el("th", { class: "r" }, den[1]), el("th", { class: "r" }, "Multiple"), el("th", { class: "r sx-hide-sm" }, "Weight"), el("th", { class: "sx-hide-sm" }, "TTM to"))), tb)));
    historyChart(db, g, key);
  }

  /* ---------- market-data derivations ---------- */
  async function openReturn(slug, series, w) {
    const g = await loadGroup(slug); const v = g.returns[series]?.[w];
    const name = { cw: "cap-weighted index", ew: "equal-weight index", spy: "SPY", etf: g.etf }[series];
    const db = openPanel({ eyebrow: "Price return", title: `${g.name} · ${name} · ${w}`, value: fsgn(v), sub: `to ${fdate(g.perf.dates.at(-1))}` });
    db.append(el("div", { class: "calc-box" }, el("div", { class: "eyebrow" }, "Method"), el("div", {}, series === "cw" ? "Index of the members' daily closes weighted by shares outstanding (today's market cap / price), chain-linked so companies without a price on a day drop out of that day's return." : series === "ew" ? "Average of the members' daily price returns, compounded." : `Daily closes of ${series === "spy" ? "SPY" : g.etf}.`), el("div", { class: "faint" }, "Prices: daily closes from the Yahoo Finance chart API as stored in the site's price files (split-adjusted, dividends not included).")));
    if (series === "cw" || series === "ew") {
      const key = { "1M": "ret_1m", YTD: "ret_ytd", "1Y": "ret_1y" }[w];
      if (key) {
        const rows = g.constituents.filter(c => c[key] != null).map(c => ({ ...c, contrib: (series === "cw" ? c.w : 1 / g.constituents.length) * c[key] })).sort((a, b) => Math.abs(b.contrib) - Math.abs(a.contrib));
        db.append(el("div", { class: "eyebrow" }, "Largest contributors (today's weights × return)"));
        const tb = el("tbody"); rows.slice(0, 25).forEach(c => tb.append(el("tr", { class: "click", onclick: () => location.hash = "company-" + c.tk.toLowerCase() }, el("td", {}, el("b", { class: "mono" }, c.tk), " ", el("span", { class: "muted sx-hide-sm" }, c.name)), el("td", { class: "r num" }, fpct(series === "cw" ? c.w : 1 / g.constituents.length)), el("td", { class: "r num " + cls(c[key]) }, fsgn(c[key])), el("td", { class: "r num " + cls(c.contrib) }, fsgn(c.contrib, 2)))));
        db.append(el("div", { class: "scroll" }, el("table", { class: "t sx-dtbl" }, el("thead", {}, el("tr", {}, el("th", {}, "Company"), el("th", { class: "r" }, "Weight"), el("th", { class: "r" }, w), el("th", { class: "r" }, "Contribution"))), tb)));
      }
    }
    const host = el("div", { style: "min-height:220px" }); db.append(host);
    requestAnimationFrame(() => lineChart(host, [{ name: "Cap-weighted", pts: g.perf.dates.map((d, i) => [ddate(d), g.perf.cw[i] / g.perf.cw[0] - 1]) }, { name: "Equal-weight", pts: g.perf.dates.map((d, i) => [ddate(d), g.perf.ew[i] / g.perf.ew[0] - 1]) }, { name: "SPY", pts: g.perf.dates.map((d, i) => [ddate(d), g.perf.spy[i] / g.perf.spy[0] - 1]), color: css("--ink"), dash: "3 3" }], { height: 220, yfmt: v => fpct(v, 0), tfmt: v => fsgn(v) }));
  }
  async function openBreadth(slug, key) {
    const g = await loadGroup(slug); const p = g.perf;
    const lab = { b50: "Members above their 50-day average", b200: "Members above their 200-day average", top5: "Weight of the five largest members", disp: "Dispersion of 21-day returns" }[key];
    const db = openPanel({ eyebrow: "Breadth and concentration", title: `${g.name} · ${lab}`, value: key === "disp" ? fpct(p[key].at(-1)) : fpct(p[key].at(-1), 0), sub: `as of ${fdate(p.dates.at(-1))}` });
    db.append(el("div", { class: "note" }, { b50: "Share of members whose latest close is above the average of their last 50 closes.", b200: "Share of members whose latest close is above the average of their last 200 closes (members with 200 days of history).", top5: "Market cap of the five largest members over the group total, using shares outstanding at today's market cap.", disp: "Cross-sectional standard deviation of the members' 21-trading-day price returns. Higher means stock picking mattered more." }[key]));
    if (key === "b50" || key === "b200") {
      const idx = key === "b50" ? 0 : 1;
      const above = g.constituents.filter(c => (idx ? c.above200 : c.above50) === true).map(c => c.tk), below = g.constituents.filter(c => (idx ? c.above200 : c.above50) === false).map(c => c.tk);
      db.append(el("div", {}, el("div", { class: "eyebrow" }, `Above (${above.length})`), el("div", { class: "sx-chips", style: "margin-top:6px" }, ...above.map(t => el("a", { class: "chip", href: "#company-" + t.toLowerCase() }, t)))),
        el("div", {}, el("div", { class: "eyebrow" }, `Below (${below.length})`), el("div", { class: "sx-chips", style: "margin-top:6px" }, ...below.map(t => el("a", { class: "chip", href: "#company-" + t.toLowerCase() }, t)))));
    } else if (key === "top5") {
      const top = g.constituents.slice(0, 5);
      db.append(el("div", { class: "sx-chips" }, ...top.map(c => el("a", { class: "chip", href: "#company-" + c.tk.toLowerCase() }, `${c.tk} ${fpct(c.w)}`))));
    }
    const host = el("div", { style: "min-height:200px" }); db.append(host);
    requestAnimationFrame(() => lineChart(host, [{ name: lab, pts: p.dates.map((d, i) => [ddate(d), p[key][i]]) }], { height: 200, yfmt: v => fpct(v, 0), tfmt: v => fpct(v, 1) }));
  }
  async function openMacro(slug, fid, w) {
    const g = await loadGroup(slug); const s = g.macro.find(x => x.id === fid); const f = g.factors.find(x => x.id === fid) || {};
    if (!s) return;
    const x = s[w] || {};
    const db = openPanel({ eyebrow: w === "d" ? "Daily, last year" : "Monthly, ten years", title: `${g.name} · ${s.name}`, value: "corr " + fnum(x.corr, 2), sub: `beta ${fmtBeta(x.beta, s.unit)} · ${x.n} ${w === "d" ? "days" : "months"} · ${x.from || ""} to ${x.to || ""}`, links: f.url ? [[f.url, "Series"]] : [] });
    db.append(el("div", { class: "calc-box" }, el("div", { class: "eyebrow" }, "Method"),
      el("div", {}, w === "d" ? "Pearson correlation and OLS beta of the group's daily cap-weighted price return against the same day's change in the factor." : "Correlation and beta of the group's monthly cap-weighted price return (weights: start-of-month market caps) against the month's change in the factor (month-end values)."),
      el("div", { class: "faint" }, `Factor change: ${s.unit === "bp" ? "difference in basis points" : s.unit === "%" ? "percent change" : "difference"}. Source: ${f.source || "–"}${f.retrieved ? `, retrieved ${fdate(f.retrieved.slice(0, 10))}` : ""}. Observations ${f.first || "–"} to ${f.last || "–"}${f.latest ? `, latest ${f.latest[1]} on ${f.latest[0]}` : ""}.`)));
    if (s.roll?.length) { const host = el("div", { style: "min-height:200px" }); db.append(el("div", { class: "eyebrow" }, "Rolling 63-day correlation (daily)"), host); requestAnimationFrame(() => lineChart(host, [{ name: "Correlation", pts: s.roll.map(([d, v]) => [ddate(d), v]) }], { height: 200, zero: true, yfmt: v => v.toFixed(1), tfmt: v => v.toFixed(2) })); }
    if (s.top?.length) {
      const tb = el("tbody"); s.top.forEach(t => tb.append(el("tr", { class: "click", onclick: () => location.hash = "company-" + t.tk.toLowerCase() }, el("td", {}, el("b", { class: "mono" }, t.tk), " ", el("span", { class: "muted sx-hide-sm" }, tkName(t.tk))), el("td", { class: "r num" }, fnum(t.corr, 2)), el("td", { class: "r num" }, fmtBeta(t.beta, s.unit)), el("td", { class: "r num" }, t.n))));
      db.append(el("div", { class: "eyebrow" }, "Most sensitive members (monthly, ten years)"), el("table", { class: "t sx-dtbl" }, el("thead", {}, el("tr", {}, el("th", {}, "Company"), el("th", { class: "r" }, "Corr"), el("th", { class: "r" }, "Beta"), el("th", { class: "r" }, "Months"))), tb));
    }
  }
  async function openKpi(slug, kid, q, ind) {
    const g = await loadGroup(slug); const k = g.kpis.find(x => x.id === kid && (x.industry || null) === (ind || null)); if (!k) return;
    const i = k.quarters.indexOf(q);
    const db = openPanel({ eyebrow: k.how === "sum" ? "Sum across companies" : "Median across companies", title: `${k.industry || g.name} · ${k.name}`, value: kfmt(k.agg[i], k.unit), sub: `${qlab(q)} · ${k.n[i]} of ${k.members_total} companies` });
    db.append(el("div", { class: "note" }, k.definition));
    const tb = el("tbody");
    Object.entries(k.comps).map(([tk, ser]) => [tk, ser[q]]).filter(([, v]) => v).sort((a, b) => b[1][0] - a[1][0]).forEach(([tk, v]) => {
      const def = k.defs[tk] || {};
      tb.append(el("tr", {}, el("td", { class: "l", onclick: () => location.hash = "company-" + tk.toLowerCase() }, el("b", { class: "mono" }, tk), " ", el("span", { class: "muted sx-hide-sm" }, tkName(tk))),
        el("td", { class: "r num" }, N(kfmt(v[0], k.unit), () => openKpiValue(slug, k, tk, q))), el("td", { class: "faint", style: "font-size:11.5px" }, def.name || def.fact || def.derived || ""), el("td", { class: "faint sx-hide-sm" }, v[2] || fdate(v[1]))));
    });
    db.append(el("div", { class: "scroll" }, el("table", { class: "t sx-dtbl" }, el("thead", {}, el("tr", {}, el("th", {}, "Company"), el("th", { class: "r" }, "Value"), el("th", {}, "As reported"), el("th", { class: "sx-hide-sm" }, "Period"))), tb)));
    const host = el("div", { style: "min-height:180px" }); db.append(host);
    requestAnimationFrame(() => lineChart(host, [{ name: k.name, pts: k.quarters.map((q, j) => [qdate(q), k.agg[j]]) }], { height: 180, yfmt: v => kfmt(v, k.unit), tfmt: v => kfmt(v, k.unit), onClick: d => openKpi(slug, kid, nearestQ(k.quarters, d), ind) }));
  }
  const kfmt = (v, u) => v == null ? "–" : u === "%" ? fnum(v, 2) + "%" : u === "USD" ? fmtMoney(v) : u === "x" ? fnum(v, 2) + "×" : u === "days" ? fnum(v, 0) + " days" : fmtKpi(v, u);
  async function openKpiValue(slug, k, tk, q) {
    const v = k.comps[tk][q]; const def = k.defs[tk] || {};
    if (def.fact) { const comp = await loadComp(slug); try { await ensureCompany(tk); } catch (e) { toast(e.message); return; } const c = model(tk).metric(def.fact, "Q", v[1]); if (c) return openSource(c); toast("Fact not found in the company's statements"); return; }
    if (def.derived) { try { await ensureCompany(tk); } catch (e) { toast(e.message); return; } const m = model(tk); const inv = m.metric("Inventory", "Q", v[1]); const ser = m.series("Cost of revenue", "Q").filter(x => x.end <= v[1]).slice(-4); if (inv && ser.length === 4) return openSource({ v: v[0], tk, label: "Inventory days", unit: "x", kind: "Q", end: v[1], calc: { formula: "Inventory / (sum of four quarters of cost of revenue) × 365", inputs: [inv, ...ser.map(x => x.cell)] }, sib: () => [] }); toast("Inputs not found"); return; }
    await ensureSegments(tk).catch(() => null);
    openKpiSource(tk, { name: def.name || k.name, unit: k.unit }, "Q", v[1], { doc: v[3], page: v[4], quote: v[5], sheet: v[6], cell: v[7], v: v[0], derived: v[8] ? { formula: "derived from reported figures" } : null });
  }
  async function openSegment(s) {
    try { await ensureCompany(s.tk); } catch (e) { toast(e.message); return; }
    const sg = await ensureSegments(s.tk);
    const tid = s.kpi_id.slice(5, s.kpi_id.length - s.member.length - 1);
    const t = (sg?.seg?.tables || []).find(x => x.id === tid); const r = t?.rows.find(x => x.key === s.member);
    if (t && r && r.A[s.end]) return openSource(segCell(s.tk, t, r, "A", r.A[s.end], r.A));
    const f = D.companies[s.tk]?.filings?.find(x => x.a === s.accn);
    window.open(f ? filingUrl(s.tk, f) : `https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=${D.companies[s.tk]?.cik || ""}&type=10-K`, "_blank", "noopener");
  }
  async function openNoteFact(slug, f) {
    const r = f.ref || {};
    if (r.type === "agg") return openAgg(slug, r.metric, r.q);
    if (r.type === "val_now" || r.type === "val_range") return openValNow(slug, r.metric);
    if (r.type === "ret") return openReturn(slug, r.series, r.w);
    if (r.type === "breadth") return openBreadth(slug, r.series);
    if (r.type === "macro") return openMacro(slug, r.id, r.w);
    if (r.type === "quarter") return openAgg(slug, "rev_growth", r.q);
  }

  /* ---------- charts ---------- */
  function rrgChart(host, sets, o = {}) {
    host.innerHTML = "";
    const W = host.clientWidth || 520, H = o.height || 340, m = { t: 16, r: 16, b: 30, l: 40 };
    const all = sets.flatMap(s => s.pts); if (!all.length) { host.append(el("div", { class: "empty" }, "Not enough history")); return; }
    const ex = Math.max(1.5, d3.max(all, p => Math.abs(p[1] - 100)) * 1.15), ey = Math.max(1.5, d3.max(all, p => Math.abs(p[2] - 100)) * 1.15);
    const x = d3.scaleLinear().domain([100 - ex, 100 + ex]).range([m.l, W - m.r]), y = d3.scaleLinear().domain([100 - ey, 100 + ey]).range([H - m.b, m.t]);
    const svg = d3.select(host).append("svg").attr("viewBox", `0 0 ${W} ${H}`).attr("height", H);
    [["Leading", 1, 1], ["Weakening", 1, -1], ["Lagging", -1, -1], ["Improving", -1, 1]].forEach(([n, sx, sy]) => { svg.append("rect").attr("x", sx > 0 ? x(100) : m.l).attr("y", sy > 0 ? m.t : y(100)).attr("width", (W - m.r - m.l) / 2).attr("height", (H - m.b - m.t) / 2).attr("fill", sx > 0 && sy > 0 ? css("--up") : sx < 0 && sy < 0 ? css("--down") : css("--c3")).attr("opacity", .05); svg.append("text").attr("x", sx > 0 ? W - m.r - 6 : m.l + 6).attr("y", sy > 0 ? m.t + 12 : H - m.b - 6).attr("text-anchor", sx > 0 ? "end" : "start").text(n).style("font-weight", 600); });
    svg.append("g").attr("transform", `translate(0,${y(100)})`).call(d3.axisBottom(x).ticks(5).tickFormat(v => v.toFixed(0)));
    svg.append("g").attr("transform", `translate(${x(100)},0)`).call(d3.axisLeft(y).ticks(5).tickFormat(v => v.toFixed(0)));
    svg.append("text").attr("x", W - m.r).attr("y", y(100) - 6).attr("text-anchor", "end").text("RS-Ratio →");
    svg.append("text").attr("x", x(100) + 6).attr("y", m.t + 24).text("↑ RS-Momentum");
    sets.forEach((s, i) => {
      const col = s.color || css(`--c${(i % 6) + 1}`);
      svg.append("path").attr("d", d3.line().x(p => x(p[1])).y(p => y(p[2])).curve(d3.curveCatmullRom)(s.pts)).attr("fill", "none").attr("stroke", col).attr("stroke-width", 1.3).attr("opacity", .55);
      svg.selectAll(null).data(s.pts.slice(0, -1)).join("circle").attr("cx", p => x(p[1])).attr("cy", p => y(p[2])).attr("r", 1.8).attr("fill", col).attr("opacity", .55);
      const lp = s.pts.at(-1);
      const gg = svg.append("g").attr("transform", `translate(${x(lp[1])},${y(lp[2])})`).style("cursor", "pointer").on("click", () => s.onClick && s.onClick())
        .on("mousemove", ev => showTip(ev, `<b>${esc(s.label)}</b> ${esc(s.name || "")}<br>RS-Ratio ${lp[1].toFixed(1)} · RS-Momentum ${lp[2].toFixed(1)}<br><span style="opacity:.7">week of ${esc(lp[0])}; tail = ${s.pts.length} weeks</span>`)).on("mouseleave", hideTip);
      gg.append("circle").attr("r", 5).attr("fill", col);
      gg.append("text").attr("x", 7).attr("y", 4).text(s.label).style("fill", css("--ink")).style("font-weight", 600);
    });
  }
  const rangeBar = (r, now, onclick) => {
    if (!r || now == null) return el("span", { class: "faint" }, "–");
    const lo = Math.min(r.min, now), hi = Math.max(r.max, now), pos = v => ((v - lo) / (hi - lo || 1) * 100).toFixed(1) + "%";
    return el("div", { class: "sx-range", title: `10-year range ${fx(r.min)} to ${fx(r.max)}, median ${fx(r.med)}; today ${fx(now)}`, onclick, style: "cursor:pointer" }, el("div", { class: "bar" }), el("div", { class: "med", style: `left:${pos(r.med)}` }), el("div", { class: "dot", style: `left:${pos(now)}` }));
  };

  /* ---------- notes ---------- */
  function noteCard(n, slug, full = true) {
    const p = el("p", {});
    const parts = n.text.split(/(\{\{\s*[a-z0-9_]+\s*\}\})/);
    parts.forEach(t => { const m = /^\{\{\s*([a-z0-9_]+)\s*\}\}$/.exec(t); if (m && n.facts[m[1]]) { const f = n.facts[m[1]]; p.append(el("button", { class: "fig", title: f.label, onclick: () => openNoteFact(slug, f) }, noteVal(f))); } else p.append(t); });
    return el("div", { class: "sx-note" }, el("div", { style: "display:flex;gap:8px;align-items:baseline;flex-wrap:wrap" }, full ? null : el("a", { href: "#sectors-" + slug }, el("b", {}, n.sector)), el("b", {}, n.title), el("span", { class: "faint", style: "font-size:11.5px;margin-left:auto" }, `Week of ${fdate(n.week)}`)), p,
      el("div", { class: "faint", style: "font-size:11px" }, "Written by a language model from the computed figures only; every figure is inserted from the data and opens its derivation."));
  }
  const noteVal = f => f.text ? f.text : f.unit === "pct" ? fpct(f.value) : f.unit === "x" ? fx(f.value) : fnum(f.value, 2);

  /* ================= overview ================= */
  async function overview() {
    const [ov, notes] = await Promise.all([loadOv(), loadNotes()]);
    page.append(el("div", { class: "ph" }, el("div", {}, el("div", { class: "eyebrow" }, "Sectors"), el("h1", {}, "Sectors and industries"),
      el("div", { class: "sub" }, `The S&P 500 grouped into eleven sectors and ${ov.groups.filter(g => g.kind === "industry").length} industries, aggregated from each company's own filings and prices. Click any figure for the companies and filings behind it.`)),
      el("div", { class: "right" }, el("span", { class: "faint", style: "font-size:12px" }, `Built ${fdate(ov.generated.slice(0, 10))} · prices to ${fdate(ov.as_of)}`))));
    const wk = (notes.notes || []).filter(n => n.week === notes.notes[0]?.week);
    if (wk.length) page.append(panel("This week's sector notes", el("span", { class: "faint", style: "font-size:12px" }, `${wk.length} sectors`), el("div", { class: "bd grid g2" }, ...wk.map(n => noteCard(n, n.slug, false)))));
    page.append(overviewTable(ov));
    page.append(rotationSection(ov));
    page.append(macroHeat(ov));
    page.append(breadthTable(ov));
    page.append(mappingPanel(ov));
  }
  function overviewTable(ov) {
    const view = { mode: store.get("sxMode", "agg") };
    const host = el("div", { class: "scroll" });
    const cols = [["rev_growth", "Rev growth"], ["ni_growth", "EPS growth"], ["om", "Op margin"], ["fcfm", "FCF margin"], ["roic", "ROIC / ROE"]];
    const draw = () => {
      host.innerHTML = "";
      const open = new Set(store.get("sxOpen", []));
      const t = el("table", { class: "t sx-tbl" }, el("thead", {}, el("tr", {}, el("th", {}, "Group"), el("th", { class: "r sx-hide-sm" }, "Cos"), el("th", { class: "r" }, "Mkt cap"), ...cols.map(([, n]) => el("th", { class: "r" }, n)), el("th", { class: "r" }, "P/E"), el("th", { class: "sx-hide-sm" }, "P/E vs 10Y"), el("th", { class: "r" }, "1M"), el("th", { class: "r" }, "YTD"))));
      const tb = el("tbody");
      const secs = ov.groups.filter(g => g.kind === "sector").sort((a, b) => b.mcap - a.mcap);
      const row = g => {
        const L = k => g.latest[k] || [];
        const isF = isFin(g.sector);
        const cell = (k) => { const kk = k === "roic" && isF ? "roe" : k; const [q, v, n] = L(kk); if (v == null) return el("td", { class: "r faint" }, "–"); const mode = view.mode; return el("td", { class: "r num" }, N(fmtU(v, ov.metrics[kk].unit), () => openAgg(g.slug, kk, q, mode), /growth/.test(kk) ? cls(v) : ""), kk === "roe" ? el("span", { class: "faint", style: "font-size:10px" }, " ROE") : null); };
        return el("tr", { class: g.kind === "sector" ? "sec" : "ind" },
          el("td", { class: "nm" }, g.kind === "sector" ? el("button", { class: "sx-tog", "aria-label": open.has(g.slug) ? "Hide industries" : "Show industries", onclick: () => { open.has(g.slug) ? open.delete(g.slug) : open.add(g.slug); store.set("sxOpen", [...open]); draw(); } }, open.has(g.slug) ? "▾" : "▸") : null, el("a", { href: "#sectors-" + g.slug }, g.name), el("span", { class: "faint", style: "font-size:11px;margin-left:6px;white-space:nowrap" }, qlab(g.q))),
          el("td", { class: "r num faint sx-hide-sm" }, g.n), el("td", { class: "r num" }, N(fmtMoney(g.mcap), () => openValNow(g.slug, "pe"))),
          ...cols.map(([k]) => cell(k)),
          el("td", { class: "r num" }, g.val_now.pe ? N(fx(g.val_now.pe), () => openValNow(g.slug, "pe")) : "–"),
          el("td", { class: "sx-hide-sm" }, rangeBar(g.pe_range, g.val_now.pe, () => openValNow(g.slug, "pe"))),
          ...["1M", "YTD"].map(w => el("td", { class: "r num" }, g.returns.cw[w] == null ? "–" : N(fsgn(g.returns.cw[w]), () => openReturn(g.slug, "cw", w), cls(g.returns.cw[w])))));
      };
      secs.forEach(s => { tb.append(row(s)); if (open.has(s.slug)) ov.groups.filter(g => g.kind === "industry" && g.sector === s.sector).sort((a, b) => b.mcap - a.mcap).forEach(g => tb.append(row(g))); });
      t.append(tb); host.append(t);
    };
    draw();
    const ctl = seg([["agg", "Aggregate"], ["med", "Median"]], view.mode, v => { view.mode = v; store.set("sxMode", v); draw(); });
    return panel("Fundamentals by sector and industry", el("div", { style: "display:flex;gap:8px;align-items:center" }, el("span", { class: "faint", style: "font-size:11.5px" }, "Derivation opens as"), ctl), host,
      el("div", { class: "src" }, "Latest calendar quarter with most of the group reported. Growth: same quarter a year earlier. Margins, ROIC and ROE: trailing four quarters. P/E: today's market caps over the latest TTM earnings; the bar shows today against the 10-year range of quarterly values (tick = median). Returns: cap-weighted index of the members. Financials and Real Estate show ROE in place of ROIC."));
  }
  function rotationSection(ov) {
    const wrap = el("div", { style: "display:flex;flex-direction:column;gap:16px" });
    const per = [["1W", 7], ["1M", 30], ["3M", 91], ["6M", 182], ["YTD", 0], ["1Y", 365]];
    const R = s => per.map(([k, d]) => k === "YTD" ? ytd(s) : retOver(s, d));
    const t = el("table", { class: "t" }, el("thead", {}, el("tr", {}, el("th", {}, "Sector ETF"), per.map(([k]) => el("th", { class: "r" }, k)), el("th", { class: "r sx-hide-sm" }, "Vol 60D"), el("th", { class: "r sx-hide-sm" }, "Beta"), el("th", { class: "sx-hide-sm" }, "1Y"))));
    const tb = el("tbody");
    const all = R("SPY");
    const heat = (v, bench) => { if (v == null || bench == null) return ""; const d = v - bench; const a = Math.min(1, Math.abs(d) / .08); return `background:color-mix(in srgb, ${d >= 0 ? css("--up") : css("--down")} ${Math.round(a * 28)}%, transparent)`; };
    const slugOf = Object.fromEntries(ov.groups.filter(g => g.kind === "sector").map(g => [g.etf, g.slug]));
    [...SECTORS, "SPY"].filter(s => PX[s]).forEach(s => { const r = R(s), st = priceStats(s); tb.append(el("tr", { class: "click", onclick: () => { if (slugOf[s]) location.hash = "sectors-" + slugOf[s]; else { STATE.taSym = s; location.hash = "technicals"; } } }, el("td", {}, el("b", { class: "mono" }, s), " ", el("span", { class: "muted" }, SYMNAME[s])), r.map((v, i) => el("td", { class: "r num", style: s === "SPY" ? "" : heat(v, all[i]) }, el("button", { class: "sx-n", title: "Open in Technicals", onclick: e => { e.stopPropagation(); STATE.taSym = s; location.hash = "technicals"; } }, fsgn(v)))), el("td", { class: "r num sx-hide-sm" }, fpct(st.vol60)), el("td", { class: "r num sx-hide-sm" }, st.beta1y == null ? "1.00" : st.beta1y.toFixed(2)), el("td", { style: "width:90px", class: "sx-hide-sm" }, sparkSVG(closes(s).slice(-252), css("--c1"), 90, 22)))); });
    t.append(tb);
    wrap.append(panel("Sector ETFs: returns vs SPY", el("span", { class: "muted", style: "font-size:12px" }, "Shading = difference from SPY over the same window"), el("div", { class: "scroll" }, t), el("div", { class: "src" }, "Select Sector SPDR ETFs, daily closes. Each return opens the ETF in Technicals; the row opens the sector built from its member companies.")));
    const g = el("div", { class: "grid g2" });
    const rr = el("div", { class: "bd" }), perf = el("div", { class: "bd" });
    let mode = store.get("sxRrg", "etf");
    const drawR = () => {
      if (mode === "etf") rrgChart(rr, SECTORS.filter(s => ov.etf_rrg[s]).map(s => ({ label: s, name: SYMNAME[s], pts: ov.etf_rrg[s].map(p => p), onClick: () => slugOf[s] && (location.hash = "sectors-" + slugOf[s]) })));
      else rrgChart(rr, ov.groups.filter(x => x.kind === "sector" && x.rrg).map(x => ({ label: x.etf || x.name.slice(0, 4), name: x.name + " (members)", pts: x.rrg, onClick: () => location.hash = "sectors-" + x.slug })));
    };
    g.append(panel("Rotation map", seg([["etf", "ETFs"], ["members", "Member indexes"]], mode, v => { mode = v; store.set("sxRrg", v); drawR(); }), rr, el("div", { class: "src" }, "Weekly. RS-Ratio = 100 × (price / SPY) over its 13-week average; RS-Momentum = 100 × RS-Ratio over its value four weeks earlier. Tails show the last ten weeks; the labelled dot is the latest week.")));
    let sel = ["XLK", "XLF", "XLE", "XLV", "XLU"];
    g.append(panel("Normalized performance, 1 year", null, perf));
    wrap.append(g);
    requestAnimationFrame(() => {
      drawR();
      if (PX.SPY) { const t0 = new Date(last("SPY").t - 365 * 864e5); lineChart(perf, ["SPY", ...sel].filter(s => PX[s]).map((s, i) => { const b = PX[s].bars.filter(x => x.t >= t0); return { name: s, pts: b.map(x => [x.t, x.c / b[0].c - 1]), color: s === "SPY" ? css("--ink") : css(`--c${i}`), dash: s === "SPY" ? "3 3" : null }; }), { height: 300, yfmt: v => fpct(v, 0), tfmt: v => fsgn(v) }); }
    });
    return wrap;
  }
  function macroHeat(ov) {
    let w = store.get("sxMacroW", "m");
    const host = el("div", { class: "bd scroll" });
    const secs = ov.groups.filter(g => g.kind === "sector").sort((a, b) => b.mcap - a.mcap);
    const fids = ov.factors.filter(f => secs.some(s => s.macro.some(m => m.id === f.id && m[w] != null)));
    const draw = () => {
      host.innerHTML = "";
      const fs = ov.factors.filter(f => secs.some(s => s.macro.some(m => m.id === f.id && m[w] != null)));
      const g = el("div", { class: "sx-heat", style: `grid-template-columns:150px repeat(${fs.length},minmax(58px,1fr))` });
      g.append(el("div"));
      fs.forEach(f => g.append(el("div", { class: "h", title: f.source }, f.name)));
      const col = d3.scaleDiverging(t => d3.interpolateRgb(css("--down"), css("--up"))(t)).domain([-1, 0, 1]);
      secs.forEach(s => {
        g.append(el("div", { class: "rl", onclick: () => location.hash = "sectors-" + s.slug }, s.name));
        fs.forEach(f => { const m = s.macro.find(x => x.id === f.id); const v = m?.[w]; g.append(v == null ? el("div", { class: "faint" }, "–") : el("div", { class: "c", style: `background:color-mix(in srgb, ${col(v)} ${Math.round(Math.min(1, Math.abs(v) / .6) * 70)}%, var(--surface));color:${Math.abs(v) > .45 ? "#fff" : "var(--ink)"}`, title: `${s.name} vs ${f.name}: ${v.toFixed(2)}`, onclick: () => openMacro(s.slug, f.id, w) }, v.toFixed(2))); });
      });
      host.append(g);
    };
    draw();
    return panel("Macro sensitivity", seg([["m", "Monthly, 10 years"], ["d", "Daily, 1 year"]], w, v => { w = v; store.set("sxMacroW", v); draw(); }), host,
      el("div", { class: "src" }, "Correlation of each sector's cap-weighted price return with changes in the factor. Yields and spreads in basis points, dollar and oil in percent. Click a cell for the beta, a rolling correlation and the most sensitive companies."));
  }
  function breadthTable(ov) {
    const secs = ov.groups.filter(g => g.kind === "sector").sort((a, b) => b.mcap - a.mcap);
    const tb = el("tbody");
    secs.forEach(s => tb.append(el("tr", {}, el("td", {}, el("a", { href: "#sectors-" + s.slug }, s.name)),
      el("td", { class: "r num" }, N(fpct(s.breadth.b50, 0), () => openBreadth(s.slug, "b50"))), el("td", { class: "r num" }, N(fpct(s.breadth.b200, 0), () => openBreadth(s.slug, "b200"))),
      el("td", { class: "r num" }, N(fpct(s.top5, 0), () => openBreadth(s.slug, "top5"))), el("td", { class: "r num" }, N(fpct(s.disp, 1), () => openBreadth(s.slug, "disp"))),
      ...["1M", "YTD"].map(w => { const d = s.returns.cw[w] != null && s.returns.ew[w] != null ? s.returns.ew[w] - s.returns.cw[w] : null; return el("td", { class: "r num sx-hide-sm" }, d == null ? "–" : N(fsgn(d), () => openReturn(s.slug, "ew", w), cls(d))); }))));
    return panel("Breadth and concentration", null, el("div", { class: "scroll" }, el("table", { class: "t" }, el("thead", {}, el("tr", {}, el("th", {}, "Sector"), el("th", { class: "r" }, "> 50D avg"), el("th", { class: "r" }, "> 200D avg"), el("th", { class: "r" }, "Top-5 weight"), el("th", { class: "r" }, "Dispersion"), el("th", { class: "r sx-hide-sm" }, "EW − CW 1M"), el("th", { class: "r sx-hide-sm" }, "EW − CW YTD"))), tb)),
      el("div", { class: "src" }, "Share of members above their 50- and 200-day averages; weight of the five largest members; dispersion is the cross-sectional standard deviation of 21-day returns. EW − CW: equal-weight minus cap-weight return; positive means the average member beat the largest."));
  }
  function mappingPanel(ov) {
    const det = el("details", { class: "panel sx-det" }, el("summary", {}, "How companies are classified"));
    const tb = el("tbody");
    ov.mapping.forEach(m => tb.append(el("tr", {}, el("td", {}, m.sector), el("td", {}, el("a", { href: "#sectors-" + m.slug }, m.industry)), el("td", { class: "mono" }, m.catch_all ? "all other SIC codes in the sector" : m.sic.join(", ")), el("td", { class: "r num" }, ov.companies.filter(c => c.industry === m.industry).length))));
    const ob = el("tbody"); ov.overrides.forEach(o => ob.append(el("tr", {}, el("td", { class: "mono" }, o.tk), el("td", {}, o.industry), el("td", { class: "muted" }, o.why))));
    const q = el("input", { class: "btn", placeholder: "Find a company", style: "width:200px", oninput: () => drawCos() });
    const cb = el("tbody");
    const drawCos = () => { cb.innerHTML = ""; const v = q.value.trim().toUpperCase(); if (!v) return; ov.companies.filter(c => c.tk.startsWith(v) || c.name.toUpperCase().includes(v)).slice(0, 12).forEach(c => cb.append(el("tr", {}, el("td", { class: "mono" }, c.tk), el("td", {}, c.name), el("td", {}, c.sector), el("td", {}, c.industry), el("td", { class: "muted" }, `SIC ${c.sic || "–"} ${c.sic_desc || ""}; ${c.rule}`)))); };
    const kb = el("tbody"); ov.kpi_specs.forEach(k => kb.append(el("tr", {}, el("td", {}, k.name), el("td", {}, k.industries.join(", ")), el("td", { class: "muted" }, k.definition), el("td", { class: "mono", style: "font-size:11px" }, k.match || k.fact || k.derived))));
    const sb = el("tbody"); ov.segment_rules.forEach(r => sb.append(el("tr", {}, el("td", { class: "mono", style: "font-size:11px" }, r.pattern + (r.exclude ? `  (not ${r.exclude})` : "")), el("td", {}, r.industry))));
    det.append(el("div", { class: "bd", style: "display:flex;flex-direction:column;gap:16px" },
      el("div", { class: "note" }, "Sectors follow the GICS-style assignment in the coverage list. Industries come from each company's SEC SIC code: the first rule in its sector whose code list contains the SIC code, otherwise the sector's catch-all. A few overrides fix SIC codes that describe a company's history rather than today's business."),
      el("div", { class: "scroll" }, el("table", { class: "t sx-map" }, el("thead", {}, el("tr", {}, el("th", {}, "Sector"), el("th", {}, "Industry"), el("th", {}, "SIC codes"), el("th", { class: "r" }, "Cos"))), tb)),
      el("div", { class: "eyebrow" }, "Overrides"), el("div", { class: "scroll" }, el("table", { class: "t sx-map" }, ob)),
      el("div", { style: "display:flex;gap:8px;align-items:center" }, el("span", { class: "eyebrow" }, "Look up a company"), q), el("div", { class: "scroll" }, el("table", { class: "t sx-map" }, cb)),
      el("div", { class: "eyebrow" }, "Industry KPIs: which reported metrics are combined"), el("div", { class: "scroll" }, el("table", { class: "t sx-map" }, el("thead", {}, el("tr", {}, el("th", {}, "KPI"), el("th", {}, "Industries"), el("th", {}, "Definition"), el("th", {}, "Name match or source"))), kb)),
      el("div", { class: "eyebrow" }, "Segment rules: revenue counted toward another industry"), el("div", { class: "scroll" }, el("table", { class: "t sx-map" }, el("thead", {}, el("tr", {}, el("th", {}, "Segment name pattern"), el("th", {}, "Industry"))), sb))));
    return det;
  }

  /* ================= one sector or industry ================= */
  async function detail(slug) {
    const [ov, g, notes] = await Promise.all([loadOv(), loadGroup(slug), loadNotes()]);
    document.title = `${g.name} · Sectors · Filed Under`;
    const ovg = ov.groups.find(x => x.slug === slug) || {};
    page.append(el("div", { class: "ph" }, el("div", {},
      el("div", { class: "sx-crumb" }, el("a", { href: "#sectors" }, "Sectors"), g.kind === "industry" ? ["›", el("a", { href: "#sectors-" + g.sector_slug }, g.sector)] : null, "›", el("span", {}, g.kind === "sector" ? "Sector" : "Industry")),
      el("h1", {}, g.name),
      el("div", { class: "sub" }, `${g.members.length} S&P 500 companies · ${fmtMoney(g.val_now.mcap)} market cap${g.etf ? ` · tracked by ${g.etf}` : ""}. Built ${fdate(g.generated.slice(0, 10))}.`),
      g.industries.length && g.kind === "sector" ? el("div", { class: "sx-chips", style: "margin-top:8px" }, ...g.industries.map(x => el("a", { class: "chip", href: "#sectors-" + x.slug }, `${x.name} · ${x.n}`))) : null,
      g.kind === "industry" ? el("div", { class: "sx-chips", style: "margin-top:8px" }, ...g.industries.filter(x => x.slug !== slug).map(x => el("a", { class: "chip", href: "#sectors-" + x.slug }, x.name))) : null)));
    const myNotes = (notes.notes || []).filter(n => n.slug === slug);
    if (myNotes.length) page.append(panel("Sector note", myNotes.length > 1 ? el("span", { class: "faint", style: "font-size:12px" }, `${myNotes.length} weeks on file`) : null, el("div", { class: "bd", style: "display:flex;flex-direction:column;gap:10px" }, noteCard(myNotes[0], slug), myNotes.length > 1 ? el("details", {}, el("summary", { class: "muted", style: "cursor:pointer;font-size:12.5px" }, "Earlier notes"), el("div", { style: "display:flex;flex-direction:column;gap:10px;margin-top:10px" }, ...myNotes.slice(1, 12).map(n => noteCard(n, slug)))) : null)));
    page.append(tiles(g));
    const g1 = el("div", { class: "grid g2" }); g1.append(marginsPanel(g), valuationPanel(g)); page.append(g1);
    page.append(earningsPanel(g));
    const g2 = el("div", { class: "grid g2" }); g2.append(returnsCapitalPanel(g), perfPanel(g)); page.append(g2);
    const g3 = el("div", { class: "grid g2" }); g3.append(breadthPanel(g), rotationPanel(g, ov)); page.append(g3);
    page.append(macroPanel(g));
    if (g.kpis.length) page.append(kpiPanel(g));
    page.append(constituentsPanel(g));
    page.append(segmentsPanel(g));
    const g4 = el("div", { class: "grid g2" }); g4.append(insiderPanel(g), filingsPanel(g)); page.append(g4);
  }
  function tiles(g) {
    const fin = isFin(g.sector);
    const keys = ["rev_growth", "ni_growth", "pct_rev_up", fin ? "nm" : "om", fin ? null : "fcfm", fin ? "roe" : "roic", fin ? null : "nd_ebitda"].filter(Boolean);
    const box = el("div", { class: "sx-tiles" });
    keys.forEach(k => {
      const L = lastOf(g, k), M = g.metrics[k];
      if (!L) return;
      const prevI = L.i - 4, pv = prevI >= 0 ? g.agg[k].agg[prevI] : null;
      box.append(el("div", { class: "sx-tile" }, el("span", { class: "l" }, M.name), el("span", { class: "v" }, L.v == null ? "–" : N(fmtU(L.v, M.unit), () => openAgg(g.slug, k, L.q), /growth/.test(k) ? cls(L.v) : "")),
        el("span", { class: "s" }, `${qlab(L.q)} · n ${L.n}${L.med != null ? " · median " + fmtU(L.med, M.unit) : ""}${pv != null && M.unit === "pct" && !/growth|pct/.test(k) ? ` · ${((L.v - pv) * 1e4 >= 0 ? "+" : "−") + Math.abs((L.v - pv) * 1e4).toFixed(0)} bp YoY` : ""}`)));
    });
    const v = g.val_now, r = g.val_range?.pe;
    box.append(el("div", { class: "sx-tile" }, el("span", { class: "l" }, "P/E today"), el("span", { class: "v" }, v.pe ? N(fx(v.pe), () => openValNow(g.slug, "pe")) : "–"), el("span", { class: "s" }, r ? `10Y ${fx(r.min)} to ${fx(r.max)} · ${Math.round((r.pct || 0) * 100)}th pct` : `median ${fx(v.pe_med)}`)));
    box.append(el("div", { class: "sx-tile" }, el("span", { class: "l" }, "P/S · P/FCF today"), el("span", { class: "v" }, v.ps ? N(fx(v.ps), () => openValNow(g.slug, "ps")) : "–", el("span", { class: "faint" }, " · "), v.pfcf ? N(fx(v.pfcf), () => openValNow(g.slug, "pfcf")) : "–"), el("span", { class: "s" }, `medians ${fx(v.ps_med)} · ${fx(v.pfcf_med)}`)));
    return panel("Latest", el("span", { class: "faint", style: "font-size:12px" }, "Aggregates over the members that reported; click for the companies and filings"), el("div", { class: "bd" }, box));
  }
  function seriesChart(host, g, keys, mode, o = {}) {
    const from = o.from || "2014Q1";
    const qs = g.quarters.map((q, i) => [q, i]).filter(([q]) => q >= from);
    lineChart(host, keys.map(([k, n], j) => ({ name: n, pts: qs.map(([q, i]) => [qdate(q), g.agg[k][mode][i]]), color: o.colors?.[j] })), { height: o.height || 260, zero: o.zero, yfmt: o.yfmt || (v => fpct(v, 0)), tfmt: o.tfmt || (v => fpct(v, 1)), onClick: d => openAgg(g.slug, keys[0][0], nearestQ(g.quarters, d), mode) });
  }
  function marginsPanel(g) {
    const host = el("div", { class: "bd" }); let mode = "agg";
    const fin = isFin(g.sector);
    const keys = fin ? [["nm", "Net margin"], ["roe", "ROE"]] : [["gm", "Gross"], ["om", "Operating"], ["nm", "Net"], ["fcfm", "FCF"]];
    const sel = el("div", { class: "sx-chips" });
    const draw = () => { requestAnimationFrame(() => seriesChart(host, g, keys.filter(([k]) => g.agg[k][mode].some(v => v != null)), mode)); };
    keys.forEach(([k, n]) => { const L = lastOf(g, k); if (L && L.v != null) sel.append(el("span", { class: "chip" }, n + " ", N(fmtU(mode === "agg" ? L.v : L.med, "pct"), () => openAgg(g.slug, k, L.q, mode)))); });
    draw();
    return panel("Margin cycle (TTM)", seg([["agg", "Aggregate"], ["med", "Median"], ["cw", "Cap-weighted"]], mode, v => { mode = v; draw(); }), el("div", { class: "bd", style: "padding-bottom:0" }, sel), host, el("div", { class: "src" }, "Trailing four quarters. Aggregate = Σ profit / Σ revenue. Click the chart for the quarter's companies."));
  }
  function valuationPanel(g) {
    const host = el("div", { class: "bd" }); let key = "pe";
    const draw = () => requestAnimationFrame(() => {
      const r = g.val_range?.[key];
      const qs = g.quarters.map((q, i) => [q, i]).filter(([q]) => q >= "2010Q1");
      const series = [{ name: "Aggregate", pts: qs.map(([q, i]) => [qdate(q), g.agg[key].agg[i] > 0 ? g.agg[key].agg[i] : null]) }, { name: "Median", pts: qs.map(([q, i]) => [qdate(q), g.agg[key].med[i]]), dash: "4 3" }];
      if (r) { const span = [qdate(r.from), new Date()]; series.push({ name: "10Y median", pts: span.map(d => [d, r.med]), color: css("--faint"), dash: "2 3", width: 1 }); }
      const now = g.val_now[key]; if (now) series.push({ name: "Today", pts: [[new Date(), now]], color: css("--c3") });
      lineChart(host, series, { height: 260, yfmt: v => v.toFixed(0) + "×", tfmt: v => fx(v), onClick: d => d > qdate(g.quarters.at(-1)) ? openValNow(g.slug, key) : openAgg(g.slug, key, nearestQ(g.quarters, d)) });
    });
    draw();
    const r = g.val_range?.pe;
    return panel("Valuation history", seg([["pe", "P/E"], ["ps", "P/S"], ["pfcf", "P/FCF"]], key, v => { key = v; draw(); }), host,
      el("div", { class: "src" }, `Σ market cap at quarter end / Σ TTM earnings, sales or FCF; median of company multiples with positive denominators. ${r ? `Aggregate P/E over the last 10 years: ${fx(r.min)} to ${fx(r.max)}, median ${fx(r.med)}.` : ""} Quarterly points before ${g.quarters[g.quarters.length - 44]} have fewer members with price history.`));
  }
  function earningsPanel(g) {
    const host = el("div", { class: "bd" }), pct = el("div", { class: "bd", style: "padding-top:0" }), contrib = el("div", { class: "bd" });
    const n = 20, qs = g.quarters.slice(-n), idx = qs.map(q => g.quarters.indexOf(q));
    let selQ = lastOf(g, "rev_growth")?.q || qs.at(-1);
    requestAnimationFrame(() => {
      barChart(host, qs.map((q, j) => ({ k: `Q${q[5]} ${q.slice(2, 4)}`, vals: [{ v: g.agg.rev_growth.agg[idx[j]], name: "Revenue growth", cell: { q, m: "rev_growth" } }, { v: g.agg.ni_growth.agg[idx[j]], name: "Earnings growth", cell: { q, m: "ni_growth" } }] })), { height: 220, yfmt: v => fpct(v, 0), tfmt: v => fsgn(v), legend: ["Revenue growth YoY", "Earnings growth YoY"], onBar: c => { selQ = c.q; drawC(); openAgg(g.slug, c.m, c.q); } });
      lineChart(pct, [{ name: "Growing revenue", pts: qs.map((q, j) => [qdate(q), g.agg.pct_rev_up.agg[idx[j]]]) }, { name: "Growing earnings", pts: qs.map((q, j) => [qdate(q), g.agg.pct_ni_up.agg[idx[j]]]) }], { height: 150, yfmt: v => fpct(v, 0), tfmt: v => fpct(v, 0), onClick: d => openAgg(g.slug, "pct_rev_up", nearestQ(g.quarters, d)) });
    });
    const qsel = el("select", { class: "btn", onchange: e => { selQ = e.target.value; drawC(); } }, ...qs.slice().reverse().map(q => el("option", { value: q, selected: q === selQ }, qlab(q))));
    const drawC = async () => {
      contrib.innerHTML = ""; qsel.value = selQ;
      const comp = await loadComp(g.slug).catch(() => null); if (!comp) { contrib.append(el("div", { class: "muted" }, "Company inputs unavailable.")); return; }
      const cols = [["Revenue", "rev_q", "rev_py"], ["Net income", "ni_q", "ni_py"]];
      const box = el("div", { class: "grid g2" });
      cols.forEach(([lab, a, b]) => {
        const rows = g.members.map(tk => ({ tk, r: compRow(comp, tk, selQ) })).filter(x => x.r && x.r[a] != null && x.r[b] != null);
        const den = rows.reduce((s, x) => s + x.r[b], 0);
        rows.forEach(x => { x.c = den ? (x.r[a] - x.r[b]) / den : null; x.g = x.r[b] > 0 ? x.r[a] / x.r[b] - 1 : null; });
        rows.sort((x, y) => Math.abs(y.c) - Math.abs(x.c));
        const tot = rows.reduce((s, x) => s + x.c, 0);
        const tb = el("tbody");
        rows.slice(0, 10).forEach(x => tb.append(el("tr", {}, el("td", { class: "l", onclick: () => location.hash = "company-" + x.tk.toLowerCase() }, el("b", { class: "mono" }, x.tk), " ", el("span", { class: "muted sx-hide-sm" }, tkName(x.tk))),
          el("td", { class: "r num" }, N(fmtMoney(x.r[a]), () => openFact(g.slug, x.tk, a, selQ))), el("td", { class: "r num " + cls(x.g) }, x.g == null ? "n/m" : fsgn(x.g)), el("td", { class: "r num " + cls(x.c) }, fsgn(x.c, 2)))));
        box.append(el("div", {}, el("div", { class: "eyebrow", style: "margin-bottom:6px" }, `${lab}: who drove the ${fsgn(tot)} change`), el("div", { class: "scroll" }, el("table", { class: "t sx-dtbl" }, el("thead", {}, el("tr", {}, el("th", {}, "Company"), el("th", { class: "r" }, qlab(selQ)), el("th", { class: "r" }, "YoY"), el("th", { class: "r" }, "Contribution"))), tb)),
          el("div", { class: "faint", style: "font-size:11px;margin-top:4px" }, `Contribution = (company change) / Σ year-ago ${lab.toLowerCase()}. ${rows.length} companies reported both quarters; top 10 by size of contribution.`)));
      });
      contrib.append(box);
    };
    drawC();
    return panel("Sector earnings", el("div", { style: "display:flex;gap:8px;align-items:center" }, el("span", { class: "faint", style: "font-size:11.5px" }, "Contributions for"), qsel), host, el("div", { class: "eyebrow", style: "padding:0 16px" }, "Share of companies growing"), pct, contrib,
      el("div", { class: "src" }, "Calendar quarters; each company quarter is placed in the calendar quarter nearest its period end. Growth compares the same companies a year apart. Beat/miss data is not shown because no consensus source is used."));
  }
  function returnsCapitalPanel(g) {
    const host = el("div", { class: "bd" }), lev = el("div", { class: "bd", style: "padding-top:0" });
    const fin = isFin(g.sector);
    requestAnimationFrame(() => {
      seriesChart(host, g, (fin ? [["roe", "ROE"]] : [["roic", "ROIC"], ["roe", "ROE"]]).filter(([k]) => g.agg[k].agg.some(v => v != null)), "agg", { height: 200 });
      const lk = (fin ? [["de", "Debt / equity"]] : [["nd_ebitda", "Net debt / EBITDA"], ["de", "Debt / equity"]]).filter(([k]) => g.agg[k].agg.some(v => v != null));
      if (lk.length) seriesChart(lev, g, lk, "agg", { height: 170, yfmt: v => v.toFixed(1) + "×", tfmt: v => fx(v), zero: true });
    });
    return panel("Returns on capital and leverage", null, host, lev, el("div", { class: "src" }, "ROIC: NOPAT over equity plus long-term debt minus cash. Leverage: long-term debt only (current portion and leases excluded), as tagged in each 10-K and 10-Q."));
  }
  function perfPanel(g) {
    const host = el("div", { class: "bd" });
    const wins = ["1W", "1M", "3M", "6M", "YTD", "1Y"];
    const ser = [["cw", "Cap-weighted"], ["ew", "Equal-weight"], g.returns.etf ? ["etf", g.etf] : null, ["spy", "SPY"]].filter(Boolean);
    const t = el("table", { class: "t sx-dtbl" }, el("thead", {}, el("tr", {}, el("th", {}, ""), ...wins.map(w => el("th", { class: "r" }, w)))), el("tbody", {}, ...ser.map(([k, n]) => el("tr", {}, el("td", {}, n), ...wins.map(w => { const v = g.returns[k]?.[w]; return el("td", { class: "r num" }, v == null ? "–" : N(fsgn(v), () => openReturn(g.slug, k, w), cls(v))); })))));
    requestAnimationFrame(() => { const p = g.perf; lineChart(host, [{ name: "Cap-weighted", pts: p.dates.map((d, i) => [ddate(d), p.cw[i] / p.cw[0] - 1]) }, { name: "Equal-weight", pts: p.dates.map((d, i) => [ddate(d), p.ew[i] / p.ew[0] - 1]) }, { name: "SPY", pts: p.dates.map((d, i) => [ddate(d), p.spy[i] / p.spy[0] - 1]), color: css("--ink"), dash: "3 3" }], { height: 230, yfmt: v => fpct(v, 0), tfmt: v => fsgn(v), onClick: () => openReturn(g.slug, "cw", "1Y") }); });
    return panel("Relative performance", null, el("div", { class: "bd scroll", style: "padding-bottom:0" }, t), host, el("div", { class: "src" }, "Indexes built from the members' daily closes over two years (price only)."));
  }
  function breadthPanel(g) {
    const host = el("div", { class: "bd" }), h2 = el("div", { class: "bd", style: "padding-top:0" });
    const p = g.perf;
    const chips = el("div", { class: "sx-chips" }, ...[["b50", "> 50D"], ["b200", "> 200D"], ["top5", "Top-5 weight"], ["disp", "Dispersion"]].map(([k, n]) => el("span", { class: "chip" }, n + " ", N(fpct(p[k].at(-1), k === "disp" ? 1 : 0), () => openBreadth(g.slug, k)))));
    requestAnimationFrame(() => {
      lineChart(host, [{ name: "Above 50-day", pts: p.dates.map((d, i) => [ddate(d), p.b50[i]]) }, { name: "Above 200-day", pts: p.dates.map((d, i) => [ddate(d), p.b200[i]]) }], { height: 180, yfmt: v => fpct(v, 0), tfmt: v => fpct(v, 0), onClick: () => openBreadth(g.slug, "b50") });
      lineChart(h2, [{ name: "Top-5 weight", pts: p.dates.map((d, i) => [ddate(d), p.top5[i]]) }, { name: "Dispersion (21D)", pts: p.dates.map((d, i) => [ddate(d), p.disp[i]]) }], { height: 160, yfmt: v => fpct(v, 0), tfmt: v => fpct(v, 1), onClick: () => openBreadth(g.slug, "top5") });
    });
    return panel("Breadth, concentration, dispersion", null, el("div", { class: "bd", style: "padding-bottom:0" }, chips), host, h2);
  }
  function rotationPanel(g, ov) {
    const host = el("div", { class: "bd" });
    const peers = ov.groups.filter(x => x.kind === "industry" && x.sector === g.sector && x.rrg);
    const sets = peers.map((x, i) => ({ label: x.name.split(/[ ,&]/)[0], name: x.name, pts: x.rrg, color: x.slug === g.slug ? css("--accent") : css(`--c${(i % 6) + 1}`), onClick: () => location.hash = "sectors-" + x.slug }));
    const me = ov.groups.find(x => x.slug === g.slug);
    if (g.kind === "sector" && me?.rrg) sets.push({ label: "Sector", name: g.name, pts: me.rrg, color: css("--ink") });
    requestAnimationFrame(() => rrgChart(host, sets, { height: 320 }));
    return panel(g.kind === "sector" ? "Industry rotation within the sector" : "Rotation against sibling industries", null, host, el("div", { class: "src" }, "Cap-weighted member indexes against SPY, weekly; tails show ten weeks. Click a dot to open that industry."));
  }
  function macroPanel(g) {
    const tb = el("tbody");
    g.macro.forEach(s => {
      const b = v => fmtBeta(v, s.unit);
      tb.append(el("tr", {}, el("td", {}, s.name),
        el("td", { class: "r num" }, s.d ? N(fnum(s.d.corr, 2), () => openMacro(g.slug, s.id, "d")) : "–"), el("td", { class: "r num sx-hide-sm" }, s.d ? b(s.d.beta) : "–"),
        el("td", { class: "r num" }, s.m ? N(fnum(s.m.corr, 2), () => openMacro(g.slug, s.id, "m")) : "–"), el("td", { class: "r num sx-hide-sm" }, s.m ? b(s.m.beta) : "–"),
        el("td", {}, el("div", { class: "sx-chips" }, ...(s.top || []).slice(0, 4).map(t => el("a", { class: "chip", href: "#company-" + t.tk.toLowerCase(), title: `corr ${t.corr}, ${t.n} months` }, `${t.tk} ${fnum(t.corr, 2)}`))))));
    });
    return panel("Macro sensitivity", null, el("div", { class: "scroll" }, el("table", { class: "t" }, el("thead", {}, el("tr", {}, el("th", {}, "Factor"), el("th", { class: "r" }, "Corr 1Y daily"), el("th", { class: "r sx-hide-sm" }, "Beta"), el("th", { class: "r" }, "Corr 10Y monthly"), el("th", { class: "r sx-hide-sm" }, "Beta"), el("th", {}, "Most sensitive members (monthly corr)"))), tb)),
      el("div", { class: "src" }, "Beta: group price return per factor move (yields, spreads and inflation per basis point, dollar and oil per 1%, payrolls per 100,000 jobs). Factor series: " + g.factors.map(f => `${f.name} (${f.source})`).join("; ") + "."));
  }
  function kpiPanel(g) {
    const box = el("div", { class: "bd grid g2" });
    g.kpis.forEach(k => {
      const i = liIdx(k.n); if (i == null) return;
      const q = k.quarters[i];
      const host = el("div", { style: "min-height:140px" });
      box.append(el("div", { class: "sx-note" }, el("div", { style: "display:flex;gap:8px;align-items:baseline;flex-wrap:wrap" }, el("b", {}, k.name), k.industry && g.kind === "sector" ? el("a", { class: "faint", style: "font-size:11.5px", href: "#sectors-" + slugify(k.industry) }, k.industry) : null, el("span", { style: "margin-left:auto", class: "num" }, N(kfmt(k.agg[i], k.unit), () => openKpi(g.slug, k.id, q, k.industry)))),
        el("div", { class: "faint", style: "font-size:11.5px" }, `${k.how === "sum" ? "Sum" : "Median"} · ${qlab(q)} · ${k.n[i]} of ${k.members_total} companies report it`), host));
      requestAnimationFrame(() => lineChart(host, [{ name: k.name, pts: k.quarters.map((qq, j) => [qdate(qq), k.agg[j]]) }], { height: 140, yfmt: v => kfmt(v, k.unit), tfmt: v => kfmt(v, k.unit), onClick: d => openKpi(g.slug, k.id, nearestQ(k.quarters, d), k.industry) }));
    });
    return panel("Industry KPIs", null, box, el("div", { class: "src" }, "Operating metrics combined only where companies report the same thing (see the definitions under How companies are classified). Values come from company investor materials (opened at the page and quote) or from XBRL facts."));
  }
  const slugify = s => s.toLowerCase().replace(/&/g, "and").replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
  function constituentsPanel(g) {
    const st = store.get("sxCons", { k: "mcap", d: true });
    const host = el("div", { class: "scroll" });
    const fin = isFin(g.sector);
    const cols = [["tk", "Company", ""], ["w", "Weight", "pct"], ["mcap", "Mkt cap", "USD"], ["ret_1m", "1M", "sgn"], ["ret_ytd", "YTD", "sgn"], ["ret_1y", "1Y", "sgn"], ["pe", "P/E", "x"], ["rev_growth", "Rev growth", "sgn"], [fin ? "roic" : "om", fin ? "ROIC" : "Op margin", "pct"], ["above200", "> 200D", "b"], ["spark", "3M", "spark"]];
    const draw = () => {
      host.innerHTML = "";
      const rows = sortRows(g.constituents.slice(), st.k, st.d);
      const t = el("table", { class: "t" }, el("thead", {}, el("tr", {}, ...cols.map(([k, n]) => el("th", { class: (k === "tk" ? "" : "r ") + "sx-sort" + (st.k === k ? " on" : "") + (["ret_1y", "pe", "above200", "w"].includes(k) ? " sx-hide-sm" : ""), onclick: () => { if (k === "spark") return; st.d = st.k === k ? !st.d : k !== "tk"; st.k = k; store.set("sxCons", st); draw(); } }, n + (st.k === k ? (st.d ? " ↓" : " ↑") : ""))))));
      const tb = el("tbody");
      rows.forEach(c => tb.append(el("tr", { class: "click", onclick: () => location.hash = "company-" + c.tk.toLowerCase() }, ...cols.map(([k, , u]) => {
        const hide = ["ret_1y", "pe", "above200", "w"].includes(k) ? " sx-hide-sm" : "";
        if (k === "tk") return el("td", {}, el("span", { class: "tkc", style: "display:inline-flex;gap:6px;align-items:center" }, miniLogo(c.tk, 18), el("b", { class: "mono" }, c.tk)), " ", el("span", { class: "muted sx-hide-sm" }, c.name), g.kind === "sector" ? el("div", { class: "faint sx-hide-sm", style: "font-size:11px" }, c.industry) : null);
        if (k === "spark") return el("td", { style: "width:90px" }, sparkSVG(c.spark || [], css((c.spark || []).at(-1) >= (c.spark || [])[0] ? "--up" : "--down"), 90, 22));
        const v = c[k];
        if (u === "b") return el("td", { class: "r" + hide }, v == null ? "–" : v ? el("span", { class: "chip up" }, "Yes") : el("span", { class: "chip down" }, "No"));
        if (v == null) return el("td", { class: "r faint" + hide }, "–");
        const txt = u === "pct" ? fpct(v) : u === "USD" ? fmtMoney(v) : u === "sgn" ? fsgn(v) : fx(v);
        const click = ["rev_growth", "om", "roic"].includes(k) && c.q ? () => openFact(g.slug, c.tk, k === "rev_growth" ? "rev_q" : k === "om" ? "oi_ttm" : "oi_ttm", c.q) : k === "w" || k === "mcap" ? () => openValNow(g.slug, "pe") : null;
        return el("td", { class: "r num " + (u === "sgn" ? cls(v) : "") + hide }, click ? N(txt, click) : txt);
      }))));
      t.append(tb); host.append(t);
    };
    draw();
    return panel("Constituents", el("span", { class: "faint", style: "font-size:12px" }, `${g.constituents.length} companies`), host, el("div", { class: "src" }, "Prices and returns from the site's daily price data; growth and margins from each company's latest quarter in the filings. Click a column to sort, a row to open the company."));
  }
  function segmentsPanel(g) {
    const ins = g.segments.inside.slice().sort((a, b) => b.v - a.v), outs = g.segments.outside.slice().sort((a, b) => b.v - a.v);
    const own = g.constituents.length;
    const row = s => el("tr", {}, el("td", {}, el("a", { href: "#company-" + s.tk.toLowerCase() }, el("b", { class: "mono" }, s.tk)), " ", el("span", { class: "muted sx-hide-sm" }, tkName(s.tk))), el("td", {}, s.label), el("td", { class: "sx-hide-sm" }, s.industry), el("td", { class: "r num" }, N(fmtMoney(s.v), () => openSegment(s))), el("td", { class: "faint" }, s.fy || fdate(s.end)));
    const tbl = (rows, empty) => rows.length ? el("div", { class: "scroll" }, el("table", { class: "t sx-dtbl" }, el("thead", {}, el("tr", {}, el("th", {}, "Company"), el("th", {}, "Segment"), el("th", { class: "sx-hide-sm" }, "Industry by segment"), el("th", { class: "r" }, "Revenue"), el("th", {}, "Year"))), el("tbody", {}, ...rows.slice(0, 25).map(row)))) : el("div", { class: "muted", style: "font-size:12.5px" }, empty);
    const sum = ins.reduce((a, s) => a + s.v, 0);
    return panel("Segment exposure", null, el("div", { class: "bd", style: "display:flex;flex-direction:column;gap:12px" },
      el("div", { class: "eyebrow" }, `Revenue from companies outside the ${g.kind}: ${fmtMoney(sum)} across ${new Set(ins.map(s => s.tk)).size} companies`), tbl(ins, `No business segment of a company outside this ${g.kind} matches it by name.`),
      el("div", { class: "eyebrow" }, `Members' segments that belong elsewhere`), tbl(outs, `None of the ${own} members reports a segment that matches another industry by name.`)),
      el("div", { class: "src" }, "Latest fiscal year of XBRL business-segment revenue (10-K). A segment counts toward an industry when its name matches the segment rules listed under How companies are classified; the rest stay with the parent."));
  }
  function insiderPanel(g) {
    const I = g.insiders;
    const host = el("div", { class: "bd", style: "padding-top:0" });
    requestAnimationFrame(() => { if (I.months.length) barChart(host, I.months.map(([m, b, s]) => ({ k: fmon(m + "-01"), vals: [{ v: b, name: "Bought", color: css("--up") }, { v: -s, name: "Sold", color: css("--down") }] })), { height: 160, legend: null }); });
    const tb = el("tbody");
    I.top.slice(0, 12).forEach(t => tb.append(el("tr", {}, el("td", {}, el("a", { href: "#company-" + t.tk.toLowerCase() }, el("b", { class: "mono" }, t.tk))), el("td", { class: "sx-hide-sm", style: "max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" }, t.owner, el("div", { class: "faint", style: "font-size:11px" }, t.role || "")), el("td", {}, el("span", { class: "chip " + (t.code === "P" ? "up" : "down") }, t.code === "P" ? "Buy" : "Sell"), t.plan ? el("span", { class: "faint", style: "font-size:10.5px" }, " 10b5-1") : null), el("td", { class: "r num" }, el("a", { href: t.url, target: "_blank", rel: "noopener", title: "Form 4 on EDGAR" }, fmtMoney(t.value))), el("td", { class: "faint" }, fdate(t.date)))));
    return panel("Insider activity", el("span", { class: "faint", style: "font-size:12px" }, "Open-market trades, 180 days"), el("div", { class: "bd" }, el("div", { class: "sx-sum" }, el("span", {}, "Bought ", el("b", { class: "up" }, fmtMoney(I.buy)), ` (${I.n_buy})`), el("span", {}, "Sold ", el("b", { class: "down" }, fmtMoney(I.sell)), ` (${I.n_sell})`), el("span", {}, "Companies ", el("b", {}, I.companies.length)))), host,
      el("div", { class: "scroll" }, el("table", { class: "t sx-dtbl" }, el("thead", {}, el("tr", {}, el("th", {}, "Co"), el("th", { class: "sx-hide-sm" }, "Insider"), el("th", {}, "Type"), el("th", { class: "r" }, "Value"), el("th", {}, "Date"))), tb)), el("div", { class: "src" }, "Form 4 transactions with codes P and S, non-derivative. Each value links to the filing on EDGAR."));
  }
  function filingsPanel(g) {
    const box = el("div", { class: "bd", style: "max-height:560px;overflow:auto" });
    if (!g.filings.length) box.append(el("div", { class: "muted" }, "No filings in the last 30 days."));
    g.filings.slice(0, 60).forEach(f => box.append(filingRow({ f: f.form, d: f.filed, r: f.report_date, a: f.accession, p: f.primary_doc, i: f.items || "", x: f.description || "", t: f.ticker, url: f.url || `https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=${encodeURIComponent(f.ticker)}&type=${encodeURIComponent(f.form)}` })));
    return panel("Recent filings", el("span", { class: "faint", style: "font-size:12px" }, "30 days, Form 4 excluded"), box);
  }

  const render = async arg => {
    const slug = arg ? String(arg).toLowerCase() : (location.hash.startsWith("#sectors-") ? location.hash.slice(9) : null);
    document.querySelectorAll("[data-p]").forEach(a => a.classList.toggle("on", a.dataset.p === "sectors"));
    if (slug) return detail(slug);
    return overview();
  };
  window.FU_PAGES.sectors = render;
  // #sectors-<slug>: route() looks pages up by the whole hash, so keys starting with "sectors-" resolve to the detail view
  if (!window.FU_PAGES.__sectorsProxy) {
    window.FU_PAGES = new Proxy(window.FU_PAGES, { get: (t, k) => k === "__sectorsProxy" ? true : typeof k === "string" && k.startsWith("sectors-") && !(k in t) ? () => render(k.slice(8)) : t[k] });
  }
})();
