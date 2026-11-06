// KPI matrix page (#matrix). Companies down the side, valuation and financial columns from index.json and standardized
// operating KPIs from kpistd/latest.json across. Every KPI cell opens the document, page and quote it was read from
// (window.FU_KPISTD.openAt). Exports: Excel workbook from api/export.py (live formulas, sources, definitions) and CSV.
window.FU_PAGES = window.FU_PAGES || {};
(() => {
  "use strict";
  const FIN = [
    { id: "mcap", name: "Market cap", f: "market_cap", fmt: v => fmtMoney(v), hib: null },
    { id: "price", name: "Price", f: "price", fmt: v => "$" + fnum(v, 2), hib: null },
    { id: "rev", name: "Revenue TTM", f: "revenue_ttm", fmt: v => fmtMoney(v), hib: null },
    { id: "revg", name: "Rev growth LQ", f: "rev_growth_lq", fmt: v => fsgn(v, 1), hib: true },
    { id: "gm", name: "Gross margin", f: "gross_margin_lq", fmt: v => fpct(v), hib: true },
    { id: "om", name: "Op margin", f: "op_margin_lq", fmt: v => fpct(v), hib: true },
    { id: "nm", name: "Net margin TTM", f: "net_margin_ttm", fmt: v => fpct(v), hib: true },
    { id: "fcfy", name: "FCF yield", f: "fcf_yield", fmt: v => fpct(v), hib: true },
    { id: "ps", name: "P/S", f: "ps_ttm", fmt: v => fnum(v, 1) + "×", hib: false },
    { id: "pe", name: "P/E", f: "pe_ttm", fmt: v => v > 0 ? fnum(v, 1) + "×" : "NM", hib: false },
    { id: "dy", name: "Div yield", f: "dividend_yield", fmt: v => fpct(v), hib: null },
    { id: "r1y", name: "1Y return", f: "ret_1y", fmt: v => fsgn(v, 1), hib: true },
    { id: "beta", name: "Beta 1Y", f: "beta_1y", fmt: v => fnum(v, 2), hib: null },
  ];
  const FIN_DEFAULT = ["mcap", "rev", "revg", "gm", "om", "fcfy", "ps", "pe"];
  const STALE_DAYS = 548, AUTO_KPIS = 6, MAX_KPIS = 12, PAGE_ROWS = 150, EXPORT_MAX = 400;
  const ST = Object.assign({ uni: "sp100", custom: "", K: "Q", fin: FIN_DEFAULT, kpis: null, heat: true, hideStale: true, sort: { c: "f:mcap", d: -1 } }, store.get("matrix", {}));
  const save = () => store.set("matrix", { uni: ST.uni, custom: ST.custom, K: ST.K, fin: ST.fin, kpis: ST.kpis, heat: ST.heat, hideStale: ST.hideStale, sort: ST.sort });

  if (!document.getElementById("mx-css")) document.head.append(el("style", { id: "mx-css" }, `
.mx-ctl{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin-bottom:12px}
.mx-ctl label{font-size:12px;color:var(--muted);display:inline-flex;gap:6px;align-items:center}
.mx-ctl select,.mx-ctl input[type=text]{font:inherit;font-size:12.5px;padding:5px 8px;border:1px solid var(--line);border-radius:7px;background:var(--surface);color:var(--ink)}
.mx-ctl input[type=text]{width:min(280px,100%)}
.mx-ctl .sp{flex:1}
.mx-pick{position:relative}
.mx-pick>summary{list-style:none;cursor:pointer;font-size:12.5px;padding:5px 10px;border:1px solid var(--line);border-radius:7px;background:var(--surface)}
.mx-pick>summary::-webkit-details-marker{display:none}
.mx-pick[open]>summary{border-color:var(--ink)}
.mx-menu{position:absolute;z-index:30;top:calc(100% + 4px);left:0;width:min(420px,88vw);max-height:420px;overflow:auto;background:var(--surface);border:1px solid var(--line);border-radius:10px;box-shadow:0 10px 30px rgba(0,0,0,.12);padding:8px}
.mx-menu .fam{font-size:10.5px;text-transform:uppercase;letter-spacing:.05em;color:var(--faint);margin:8px 6px 4px}
.mx-menu label{display:flex;justify-content:space-between;gap:10px;padding:4px 6px;border-radius:6px;font-size:12.5px;color:var(--ink);cursor:pointer}
.mx-menu label:hover{background:var(--surface-hover,var(--accent-soft))}
.mx-menu label.off{color:var(--faint)}
.mx-menu .n{color:var(--muted);font-variant-numeric:tabular-nums;font-size:11.5px}
.mx-menu .tools{display:flex;gap:6px;padding:2px 4px 6px;border-bottom:1px solid var(--line);margin-bottom:4px}
.mx-wrap{overflow:auto;max-height:calc(100vh - 230px);border:1px solid var(--line);border-radius:10px;background:var(--surface)}
.mx-tbl{border-collapse:separate;border-spacing:0;font-size:12.5px;min-width:100%}
.mx-tbl th{position:sticky;top:0;z-index:2;background:var(--surface);font-weight:600;font-size:10.5px;text-transform:uppercase;letter-spacing:.04em;color:var(--faint);padding:8px 9px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap;cursor:pointer;user-select:none;vertical-align:bottom}
.mx-tbl th .u{display:block;text-transform:none;letter-spacing:0;font-weight:400;color:var(--faint);font-size:10.5px}
.mx-tbl th.grp{cursor:default;text-align:center;font-size:10px;color:var(--muted);padding:5px 9px;top:0;border-bottom:1px solid var(--line)}
.mx-tbl thead tr:nth-child(2) th{top:27px}
.mx-tbl th.on{color:var(--ink)}
.mx-tbl th.kp{max-width:120px;white-space:normal;line-height:1.25}
.mx-tbl td{padding:6px 9px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}
.mx-tbl td.co,.mx-tbl th.co{position:sticky;left:0;z-index:1;background:var(--surface);text-align:left}
.mx-tbl th.co{z-index:3}
.mx-tbl td.co a{color:var(--ink);text-decoration:none;display:flex;gap:8px;align-items:center}
.mx-tbl td.co .t{font-weight:600;min-width:44px}
.mx-tbl td.co .nm{color:var(--muted);max-width:170px;overflow:hidden;text-overflow:ellipsis}
.mx-tbl td.sep,.mx-tbl th.sep{border-left:1px solid var(--line)}
.mx-tbl td .sub{display:block;font-size:10.5px;color:var(--faint)}
.mx-tbl td.stale{color:var(--faint);font-style:italic}
.mx-tbl tfoot td{position:sticky;bottom:0;background:var(--sunk,#f6f6f6);font-weight:600;border-top:1px solid var(--line)}
.mx-tbl tfoot td.co{z-index:2;background:var(--sunk,#f6f6f6)}
.mx-n{border:0;background:none;padding:0;font:inherit;color:inherit;cursor:pointer;text-decoration:underline dotted var(--faint);text-underline-offset:3px}
.mx-n:hover{color:var(--accent);text-decoration-color:var(--accent)}
.mx-foot{display:flex;flex-wrap:wrap;gap:10px;align-items:center;justify-content:space-between;margin-top:10px;font-size:12px;color:var(--muted)}
.mx-legend{display:inline-flex;align-items:center;gap:6px}
.mx-legend i{display:inline-block;width:64px;height:8px;border-radius:4px;background:linear-gradient(90deg,color-mix(in oklch,var(--down) 18%,transparent),transparent,color-mix(in oklch,var(--up) 18%,transparent))}
.mx-act{display:inline-flex;gap:8px;margin-left:auto}
@media (max-width:860px){.mx-wrap{max-height:none}.mx-tbl td.co .nm{display:none}}
`));

  const dOf = s => new Date(String(s).slice(0, 10) + "T00:00:00Z");
  const staleCut = () => Date.now() - STALE_DAYS * 864e5;
  const isPct = u => u === "%" || u === "pp";
  const UNIT = { USD: "$", USD_per_share: "$ per share", USD_per_unit: "$ per unit", count: "count", x: "×", cents: "¢" };

  function universe(lat) {
    const all = Object.keys(IDX);
    const ind = {};
    Object.values(lat.kpis || {}).forEach(rows => Object.entries(rows).forEach(([t, r]) => { if (r.ind && !ind[t]) ind[t] = r.ind; }));
    const u = ST.uni;
    let tks, label;
    if (u === "sp100") { tks = all.filter(t => IDX[t].sp100); label = "S&P 100"; }
    else if (u === "sp500") { tks = all.filter(t => IDX[t].sp500); label = "S&P 500"; }
    else if (u === "all") { tks = all; label = "All covered companies"; }
    else if (u === "watch") { tks = store.get("watch", []).filter(t => IDX[t]); label = "Watchlist"; }
    else if (u.startsWith("sec:")) { const s = u.slice(4); tks = all.filter(t => IDX[t].sector === s); label = s; }
    else if (u.startsWith("ind:")) { const s = u.slice(4); tks = Object.keys(ind).filter(t => ind[t] === s && IDX[t]); label = s; }
    else { tks = [...new Set(ST.custom.toUpperCase().split(/[\s,;]+/).filter(Boolean))].filter(t => IDX[t]); label = "Custom list"; }
    return { tks, label, ind };
  }

  function kpiCell(lat, kid, tk) {
    const r = lat.kpis?.[kid]?.[tk];
    if (!r) return null;
    const p = ST.K === "A" ? r.a : r.q;
    if (!p || p[1] == null) return null;
    return { end: p[0], v: p[1], label: p[2], yoy: p[3], stale: dOf(p[0]).getTime() < staleCut() };
  }

  function coverage(lat, kid, tks) {
    const rows = lat.kpis?.[kid] || {};
    let n = 0;
    for (const t of tks) { const c = rows[t] && kpiCell(lat, kid, t); if (c && !(ST.hideStale && c.stale)) n++; }
    return n;
  }

  async function render() {
    const K = window.FU_KPISTD;
    const [tax, lat] = await Promise.all([K.loadTax(), K.loadLatest()]);
    const meta = Object.fromEntries((tax.kpis || []).map(k => [k.id, k]));
    const kpiList = (tax.kpis || []).filter(k => k.family !== "Segments" && lat.kpis?.[k.id]);
    page.append(el("div", { class: "ph" }, el("div", {}, el("div", { class: "eyebrow" }, "Screen & compare"), el("h1", {}, "KPI matrix"),
      el("div", { class: "sub" }, "Valuation, financials and standardized operating KPIs side by side for any set of companies. Click a KPI value to open the filing or release it was read from. The Excel export keeps live formulas, a sourced KPI history and definitions."))));
    const ctl = el("div", { class: "mx-ctl" });
    const host = el("div");
    const foot = el("div", { class: "mx-foot" });
    page.append(ctl, host, foot);

    const sectors = [...new Set(Object.values(IDX).map(c => c.sector).filter(Boolean))].sort();
    const inds = (() => { const c = {}; Object.values(lat.kpis || {}).forEach(rows => Object.entries(rows).forEach(([t, r]) => { if (r.ind && IDX[t]) (c[r.ind] = c[r.ind] || new Set()).add(t); })); return Object.entries(c).filter(([, s]) => s.size >= 3).map(([k, s]) => [k, s.size]).sort((a, b) => a[0].localeCompare(b[0])); })();
    const uniSel = el("select", { "aria-label": "Companies" },
      el("optgroup", { label: "Lists" }, [["sp100", "S&P 100"], ["sp500", "S&P 500"], ["watch", "Watchlist"], ["all", "All covered companies"], ["custom", "Custom tickers"]].map(([v, t]) => el("option", { value: v }, t))),
      el("optgroup", { label: "Sector" }, sectors.map(s => el("option", { value: "sec:" + s }, s))),
      el("optgroup", { label: "Industry (KPI coverage)" }, inds.map(([s, n]) => el("option", { value: "ind:" + s }, `${s} (${n})`))));
    uniSel.value = ST.uni;
    if (uniSel.value !== ST.uni) { ST.uni = "sp100"; uniSel.value = "sp100"; }
    const custom = el("input", { type: "text", placeholder: "Tickers, e.g. JPM BAC WFC C", value: ST.custom, "aria-label": "Custom tickers" });
    const showCustom = () => { custom.style.display = ST.uni === "custom" ? "" : "none"; };
    showCustom();
    uniSel.onchange = () => { ST.uni = uniSel.value; ST.kpis = null; save(); showCustom(); draw(); };
    let ct;
    custom.oninput = () => { clearTimeout(ct); ct = setTimeout(() => { ST.custom = custom.value; ST.kpis = null; save(); draw(); }, 350); };
    const basis = el("select", { "aria-label": "KPI period" }, el("option", { value: "Q" }, "Latest quarter"), el("option", { value: "A" }, "Latest fiscal year"));
    basis.value = ST.K;
    basis.onchange = () => { ST.K = basis.value; save(); draw(); };
    const finPick = el("details", { class: "mx-pick" }, el("summary", {}, "Financial columns"));
    const kpiPick = el("details", { class: "mx-pick" }, el("summary", {}, "KPIs"));
    const chk = (label, key) => el("label", {}, el("input", { type: "checkbox", checked: ST[key] ? true : null, onchange: e => { ST[key] = e.target.checked; save(); draw(); } }), label);
    const xl = el("button", { class: "btn acc", title: "Download an Excel workbook with live formulas, KPI history and sources" }, svgUse("i-dl"), "Excel");
    const csv = el("button", { class: "btn", title: "Download this view as CSV" }, svgUse("i-dl"), "CSV");
    ctl.append(uniSel, custom, basis, finPick, kpiPick, chk("Shade by rank", "heat"), chk("Hide values older than 18 months", "hideStale"), el("span", { class: "mx-act" }, csv, xl));
    document.addEventListener("click", e => { [finPick, kpiPick].forEach(d => { if (d.open && !d.contains(e.target)) d.open = false; }); });

    let cur = null, showAll = false;

    function chosenKpis(tks) {
      if (ST.kpis) return ST.kpis.filter(k => meta[k]);
      return kpiList.filter(k => k.id !== "total_revenue").map(k => [k.id, coverage(lat, k.id, tks)])
        .filter(([, n]) => n >= Math.min(3, Math.max(1, Math.ceil(tks.length * 0.1)))).sort((a, b) => b[1] - a[1]).slice(0, AUTO_KPIS).map(x => x[0]);
    }

    function paintPickers(tks, kids) {
      const fm = el("div", { class: "mx-menu" }, FIN.map(f => el("label", {}, el("span", {}, el("input", { type: "checkbox", checked: ST.fin.includes(f.id) ? true : null,
        onchange: e => { ST.fin = e.target.checked ? FIN.filter(x => x.id === f.id || ST.fin.includes(x.id)).map(x => x.id) : ST.fin.filter(x => x !== f.id); save(); draw(true); } }), " ", f.name))));
      finPick.querySelector(".mx-menu")?.remove(); finPick.append(fm);
      finPick.querySelector("summary").textContent = `Financial columns (${ST.fin.length})`;
      const km = el("div", { class: "mx-menu" });
      km.append(el("div", { class: "tools" },
        el("button", { class: "btn", onclick: () => { ST.kpis = null; save(); draw(true); } }, "Auto-pick by coverage"),
        el("button", { class: "btn", onclick: () => { ST.kpis = []; save(); draw(true); } }, "Clear")));
      const cov = Object.fromEntries(kpiList.map(k => [k.id, coverage(lat, k.id, tks)]));
      const fams = [...new Set(kpiList.map(k => k.family))];
      fams.forEach(f => {
        const items = kpiList.filter(k => k.family === f).sort((a, b) => cov[b.id] - cov[a.id] || a.name.localeCompare(b.name));
        km.append(el("div", { class: "fam" }, f));
        items.forEach(k => km.append(el("label", { class: cov[k.id] ? "" : "off", title: k.definition },
          el("span", {}, el("input", { type: "checkbox", checked: kids.includes(k.id) ? true : null, onchange: e => {
            const now = kids.slice();
            ST.kpis = e.target.checked ? [...now, k.id].slice(0, MAX_KPIS) : now.filter(x => x !== k.id);
            if (e.target.checked && now.length >= MAX_KPIS) toast(`Up to ${MAX_KPIS} KPIs at a time.`);
            save(); draw(true);
          } }), " ", k.name), el("span", { class: "n" }, `${cov[k.id]} of ${tks.length}`))));
      });
      kpiPick.querySelector(".mx-menu")?.remove(); kpiPick.append(km);
      kpiPick.querySelector("summary").textContent = `KPIs (${kids.length}${ST.kpis ? "" : ", auto"})`;
    }

    function draw(keepMenus) {
      const { tks, label } = universe(lat);
      const kids = chosenKpis(tks);
      const fins = FIN.filter(f => ST.fin.includes(f.id));
      const openF = finPick.open, openK = kpiPick.open;
      paintPickers(tks, kids);
      if (keepMenus) { finPick.open = openF; kpiPick.open = openK; }
      const cols = [
        ...fins.map(f => ({ key: "f:" + f.id, name: f.name, unit: "", hib: f.hib, val: t => { const v = IDX[t]?.[f.f]; return v == null || !isFinite(v) ? null : v; }, text: v => f.fmt(v), group: "Financials" })),
        ...kids.map(k => { const m = meta[k]; return { key: "k:" + k, kid: k, name: m.name, unit: m.unit, hib: m.higher_is_better ?? null, def: m.definition, group: "Operating KPIs",
          cell: t => { const c = kpiCell(lat, k, t); return c && !(ST.hideStale && c.stale) ? c : null; },
          val(t) { const c = this.cell(t); return c ? c.v : null; }, text: v => K.fmtV(v, m.unit) }; }),
      ];
      const rows = tks.map(t => ({ t, c: IDX[t], v: Object.fromEntries(cols.map(c => [c.key, c.val(t)])) }));
      const sc = ST.sort.c === "t" ? null : cols.find(c => c.key === ST.sort.c) || cols[0];
      if (sc) rows.sort((a, b) => { const x = a.v[sc.key], y = b.v[sc.key]; if (x == null && y == null) return a.t.localeCompare(b.t); if (x == null) return 1; if (y == null) return -1; return (x - y) * ST.sort.d; });
      else rows.sort((a, b) => a.t.localeCompare(b.t));
      // percentile rank per column for shading
      const rank = {};
      cols.forEach(c => {
        const vs = rows.map(r => r.v[c.key]).filter(v => v != null).sort((a, b) => a - b);
        rank[c.key] = v => { if (v == null || vs.length < 3) return null; let lo = 0, hi = vs.length; while (lo < hi) { const m = (lo + hi) >> 1; vs[m] < v ? lo = m + 1 : hi = m; } return lo / (vs.length - 1); };
        c.median = vs.length ? (vs.length % 2 ? vs[(vs.length - 1) / 2] : (vs[vs.length / 2 - 1] + vs[vs.length / 2]) / 2) : null;
        c.n = vs.length;
      });
      cur = { tks, label, kids, cols, rows };
      host.innerHTML = "";
      if (!tks.length) {
        host.append(el("section", { class: "panel" }, el("div", { class: "empty" }, el("h2", {}, ST.uni === "watch" ? "Your watchlist is empty" : "No companies match"),
          el("p", { class: "muted" }, ST.uni === "custom" ? "Type tickers separated by spaces or commas." : "Pick another list, sector or industry."))));
        foot.innerHTML = "";
        return;
      }
      const thead = el("thead");
      const g = el("tr", {}, el("th", { class: "co grp" }, ""));
      if (fins.length) g.append(el("th", { class: "grp sep", colspan: fins.length }, "Valuation & financials"));
      if (kids.length) g.append(el("th", { class: "grp sep", colspan: kids.length }, `Operating KPIs · ${ST.K === "A" ? "latest fiscal year" : "latest quarter"}`));
      const h = el("tr", {}, el("th", { class: "co" + (ST.sort.c === "t" ? " on" : ""), onclick: () => { ST.sort = { c: "t", d: 1 }; save(); draw(); } }, `${rows.length} companies`));
      cols.forEach((c, i) => {
        const on = ST.sort.c === c.key;
        const sep = i === 0 || (i === fins.length);
        h.append(el("th", { class: [c.kid ? "kp" : "", on ? "on" : "", sep ? "sep" : ""].filter(Boolean).join(" "), title: c.def || `Sort by ${c.name}`,
          onclick: () => { ST.sort = { c: c.key, d: on ? -ST.sort.d : -1 }; save(); draw(); } },
          c.name, on ? (ST.sort.d < 0 ? " ↓" : " ↑") : "", c.unit ? el("span", { class: "u" }, UNIT[c.unit] || c.unit.replace(/_/g, " ")) : null));
      });
      thead.append(g, h);
      const tb = el("tbody");
      const shown = showAll ? rows : rows.slice(0, PAGE_ROWS);
      shown.forEach(r => {
        const tr = el("tr", {}, el("td", { class: "co" }, el("a", { href: "#company-" + r.t.toLowerCase(), title: r.c?.name || r.t }, el("span", { class: "t" }, r.t), el("span", { class: "nm" }, r.c?.name || ""))));
        cols.forEach((c, i) => {
          const v = r.v[c.key];
          const sep = i === 0 || i === fins.length ? "sep" : "";
          const td = el("td", { class: sep });
          if (v == null) { td.append("–"); tr.append(td); return; }
          if (ST.heat && c.hib != null) {
            const p = rank[c.key](v);
            if (p != null) { const q = c.hib ? p : 1 - p, d = Math.abs(q - 0.5) * 2; if (d > 0.1) td.style.background = `color-mix(in oklch, var(${q > 0.5 ? "--up" : "--down"}) ${Math.round(3 + 15 * d)}%, transparent)`; }
          }
          if (c.kid) {
            const cell = c.cell(r.t);
            if (cell.stale) td.classList.add("stale");
            td.title = `${cell.label || ""} · period ended ${fdate(cell.end)}${cell.stale ? " · older than 18 months" : ""}`;
            td.append(el("button", { class: "mx-n", onclick: () => K.openAt(r.t, c.kid, ST.K, cell.end) }, c.text(v)),
              el("span", { class: "sub" }, [cell.label, cell.yoy == null ? null : isPct(c.unit) ? `${cell.yoy >= 0 ? "+" : "−"}${fnum(Math.abs(cell.yoy), 2)} pp` : fsgn(cell.yoy, 1)].filter(Boolean).join(" · ")));
          } else td.append(c.text(v));
          tr.append(td);
        });
        tb.append(tr);
      });
      const tf = el("tfoot", {}, el("tr", {}, el("td", { class: "co" }, "Median"), ...cols.map((c, i) => el("td", { class: i === 0 || i === fins.length ? "sep" : "", title: `${c.n} of ${rows.length} companies` }, c.median == null ? "–" : c.text(c.median)))));
      host.append(el("div", { class: "mx-wrap" }, el("table", { class: "mx-tbl" }, thead, tb, tf)));
      foot.innerHTML = "";
      foot.append(el("span", {}, `${label} · ${rows.length} companies · financials from SEC filings, prices as of ${fdate(Object.values(IDX)[0]?.as_of || "")}. KPI values are as reported; italic values are more than 18 months old.`),
        el("span", { class: "mx-legend" }, ST.heat ? [el("span", {}, "Below peer median"), el("i"), el("span", {}, "Above")] : null),
        rows.length > PAGE_ROWS ? el("button", { class: "btn", onclick: () => { showAll = !showAll; draw(); } }, showAll ? `Show first ${PAGE_ROWS}` : `Show all ${rows.length}`) : null);
    }

    csv.onclick = () => {
      if (!cur) return;
      const q = s => /[",\n]/.test(String(s)) ? `"${String(s).replace(/"/g, '""')}"` : String(s ?? "");
      const head = ["Ticker", "Company", ...cur.cols.flatMap(c => c.kid ? [`${c.name} (${c.unit})`, `${c.name} period`, `${c.name} YoY`] : [c.name])];
      const lines = [head.map(q).join(",")];
      cur.rows.forEach(r => lines.push([r.t, r.c?.name || "", ...cur.cols.flatMap(c => {
        if (!c.kid) return [r.v[c.key] ?? ""];
        const cell = c.cell(r.t);
        return cell ? [cell.v, cell.label || cell.end, cell.yoy ?? ""] : ["", "", ""];
      })].map(q).join(",")));
      lines.push("", q(`Filed Under KPI matrix, ${cur.label}, ${new Date().toISOString()}. Ratios and margins are fractions (0.25 = 25%). KPI values in reported units; percentage KPI YoY is in points.`));
      downloadText(`filedunder_matrix_${cur.label.toLowerCase().replace(/[^a-z0-9]+/g, "-")}.csv`, lines.join("\n"), "text/csv");
    };

    xl.onclick = async () => {
      if (!cur) return;
      if (cur.tks.length > EXPORT_MAX) { toast(`Excel export is limited to ${EXPORT_MAX} companies. Narrow the list first.`); return; }
      xl.disabled = true;
      const was = xl.lastChild.textContent;
      xl.lastChild.textContent = "Building…";
      try {
        const r = await fetch("/api/export", { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ tickers: cur.rows.map(r => r.t), kpis: cur.kids, basis: ST.K, label: cur.label, hide_stale: ST.hideStale }) });
        if (!r.ok) { const e = await r.json().catch(() => ({})); throw new Error(e.error || `Export failed (${r.status})`); }
        const blob = await r.blob();
        const name = (r.headers.get("Content-Disposition") || "").match(/filename="([^"]+)"/)?.[1] || "filedunder-comps.xlsx";
        const a = el("a", { href: URL.createObjectURL(blob), download: name });
        document.body.append(a); a.click(); a.remove();
        setTimeout(() => URL.revokeObjectURL(a.href), 4000);
        toast(`Downloaded ${name}`);
      } catch (e) {
        toast(e.message || "Export failed");
      } finally {
        xl.disabled = false;
        xl.lastChild.textContent = was;
      }
    };

    draw();
  }

  window.FU_PAGES.matrix = render;
})();
