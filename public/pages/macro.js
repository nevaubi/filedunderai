// Macro page. Registers window.FU_PAGES.macro; app.js route() uses it in place of the built-in page.
// Data: macro/catalog.json (series metadata, statistics, release calendar, weekly moves), macro/s/<id>.json (history),
// macro/snap/<id>.json (the rows exactly as the publisher printed them, for the source panel).
window.FU_PAGES = window.FU_PAGES || {};
(() => {
  const MS = { cat: null, s: {}, snap: {}, range: store.get("mrange", "5Y") };
  const MDR = { id: null, d: null, tab: "source" };
  const fidOf = id => id.replace(/[^A-Za-z0-9_.-]/g, "_");
  const getCat = async fresh => { if (!MS.cat || fresh) MS.cat = await getJSON("macro/catalog.json", { fresh }); return MS.cat; };
  const getSer = id => (MS.s[id] = MS.s[id] || getJSON(`macro/s/${fidOf(id)}.json`).catch(() => ({ obs: [] })));
  const getSnap = id => (MS.snap[id] = MS.snap[id] || getJSON(`macro/snap/${fidOf(id)}.json`).catch(() => null));
  const META = id => MS.cat?.series?.[id];

  const style = document.createElement("style");
  style.textContent = `
.mx-sec{display:flex;flex-direction:column;gap:14px}
.mx-sec>h2{font-size:18px;margin:8px 0 0;display:flex;align-items:baseline;gap:10px}
.mx-sec>h2 small{font-size:12px;color:var(--faint);font-weight:500}
.mx-vals{display:flex;flex-wrap:wrap;gap:6px 10px;padding:10px 16px 0}
.mx-val{display:flex;flex-direction:column;gap:2px;min-width:0}
.mx-val .n{font-size:11px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:220px}
.mx-val .v{display:flex;gap:6px;align-items:center}
.mx-meta{font-size:11px;color:var(--faint);padding:0 16px 12px;display:flex;flex-wrap:wrap;gap:4px 12px}
.mx-meta a{color:var(--muted)}
.mx-chg{font-size:11.5px}
.mx-list{display:flex;flex-direction:column}
.mx-list .it{display:flex;gap:10px;padding:9px 16px;border-top:1px solid var(--line);cursor:pointer;align-items:flex-start}
.mx-list .it:first-child{border-top:0}
.mx-list .it:hover{background:var(--sunk)}
.mx-list .z{flex:none;width:46px;text-align:right;font-size:12px;font-weight:700}
.mx-cal{width:100%;border-collapse:collapse;font-size:12.5px}
.mx-cal td{padding:6px 16px;border-top:1px solid var(--line);vertical-align:top}
.mx-cal tr:first-child td{border-top:0}
.mx-cal .dt{white-space:nowrap;color:var(--muted);width:1%}
.mx-tbl{overflow-x:auto}
.mx-tbl table.t td.c{cursor:pointer;text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.mx-tbl table.t td.c:hover{background:var(--accent-soft);color:var(--accent)}
.mx-tbl table.t th.r{text-align:right}
.mx-snap{max-height:56vh;overflow:auto;border:1px solid var(--line);border-radius:8px}
.mx-snap table{width:100%;border-collapse:collapse;font-size:12px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
.mx-snap th{position:sticky;top:0;background:var(--sunk);text-align:left;padding:6px 8px;font-weight:600;border-bottom:1px solid var(--line);white-space:nowrap}
.mx-snap td{padding:4px 8px;border-bottom:1px solid var(--line);white-space:nowrap}
.mx-snap tr.hl td{background:color-mix(in srgb,var(--mark) 25%,transparent)}
.mx-snap td.hit{background:var(--mark);color:var(--mark-ink);font-weight:700;outline:2px solid var(--mark)}
.mx-snap tr.hl2 td{background:color-mix(in srgb,var(--mark) 12%,transparent)}
.mx-rng{margin-left:auto}
@media (max-width:860px){.mx-val .n{max-width:150px}.mx-cal td{padding:6px 12px}}
`;
  document.head.append(style);

  /* ---------- values ---------- */
  const isPct = m => m.display === "yoy" || m.display === "mom" || /^Percent(?!age)/.test(m.dunits || m.units || "") || /^Net percentage/.test(m.dunits || "");
  function disp(m, obs) {
    const k = m.display || "level", sc = m.scale || 1;
    if (k === "yoy") {
      const key = m.frequency === "A" ? d => d.slice(0, 4) : d => d.slice(0, 7);
      const by = new Map(obs.map(([d, v]) => [key(d), v]));
      return obs.map(([d, v]) => { const p = by.get(String(+d.slice(0, 4) - 1) + key(d).slice(4)); return p ? [d, (v / p - 1) * 100] : null; }).filter(Boolean);
    }
    if (k === "mom") return obs.slice(1).map(([d, v], i) => [d, (v / obs[i][1] - 1) * 100]);
    if (k === "chg") return obs.slice(1).map(([d, v], i) => [d, (v - obs[i][1]) * sc]);
    return obs.map(([d, v]) => [d, v * sc]);
  }
  function fv(m, v) {
    if (v == null || !isFinite(v)) return "–";
    const dp = m.dp ?? 2, s = (v < 0 ? "−" : "") + Math.abs(v).toLocaleString("en-US", { minimumFractionDigits: dp, maximumFractionDigits: dp });
    if (/^Percentage points/.test(m.units || "")) return s + " pp";
    if (/^Net percentage/.test(m.dunits || "")) return s;
    if (isPct(m)) return s + "%";
    return s + (m.short_units ? " " + m.short_units : "");
  }
  function fchg(m, c) {
    if (c == null || !isFinite(c)) return "";
    const sg = c > 0 ? "+" : c < 0 ? "−" : "±", a = Math.abs(c);
    if ((m.display || "level") === "level" && /^Percent(?!age)|^Percentage points/.test(m.units || "") && (m.dp ?? 2) >= 2) return sg + (a * 100).toFixed(0) + " bp";
    if (isPct(m) || /^Net percentage/.test(m.dunits || "")) return sg + a.toFixed(m.dp ?? 1) + (/^Net/.test(m.dunits || "") ? " pts" : " pp");
    const dp = m.dp ?? 2; return sg + a.toLocaleString("en-US", { minimumFractionDigits: dp, maximumFractionDigits: dp }) + (m.short_units ? " " + m.short_units : "");
  }
  function plab(m, d) {
    if (!d) return "";
    const f = m?.frequency;
    if (f === "M") return monthLabel(d);
    if (f === "Q") return `Q${Math.ceil(+d.slice(5, 7) / 3)} ${d.slice(0, 4)}`;
    if (f === "A") return d.slice(0, 4);
    return fdate(d);
  }
  const monthLabel = d => `${MONTHS[+d.slice(5, 7) - 1]} ${d.slice(0, 4)}`;
  const periodLabel = (m, d) => m?.frequency === "M" ? monthLabel(d) : plab(m, d);
  const rangeStart = () => { const t = new Date(); const r = MS.range; if (r === "Max") return "0000"; if (r === "2008") return "2008-01-01"; const y = r === "2Y" ? 2 : r === "10Y" ? 10 : 5; return `${t.getUTCFullYear() - y}${t.toISOString().slice(4, 10)}`; };

  /* ---------- panels ---------- */
  function valChip(id, label) {
    const m = META(id); if (!m?.stats) return null;
    const st = m.stats;
    return el("div", { class: "mx-val" }, el("span", { class: "n", title: m.title }, label || m.short || m.title),
      el("span", { class: "v" }, el("button", { class: "chip link acc mono", title: `${m.title}, ${periodLabel(m, st.last[0])}. Open the source`, onclick: e => { e.stopPropagation(); openMacro(id, st.last[0]); } }, fv(m, st.last[1])),
        el("span", { class: "mx-chg num " + cls(st.chg) }, fchg(m, st.chg)), el("span", { class: "faint", style: "font-size:11px" }, periodLabel(m, st.last[0]))));
  }
  function metaLine(ids) {
    const m = META(ids[0]); if (!m) return null;
    const provs = [...new Map(ids.map(META).filter(Boolean).map(x => [x.provider + x.source, x])).values()];
    const st = m.stats || {};
    const nx = m.next_release;
    return el("div", { class: "mx-meta" },
      st.published ? el("span", {}, `Released ${fdate(st.published)}`) : st.vintage ? el("span", {}, `First seen ${fdate(st.vintage)}`) : null,
      nx ? el("span", {}, `Next ${fdate(nx[0])}${nx[1] ? " " + nx[1] : ""}`) : el("span", {}, FREQ_LABEL[m.frequency] || ""),
      ...provs.slice(0, 3).map(x => el("a", { href: x.source_url, target: "_blank", rel: "noopener" }, `${x.provider}, ${x.source} ↗`)));
  }
  const FREQ_LABEL = { D: "Daily", W: "Weekly", M: "Monthly", Q: "Quarterly", A: "Annual" };
  function chartPanel(spec) {
    const ids = spec.ids.filter(id => META(id)?.stats && !META(id).hidden);
    if (!ids.length) return null;
    const host = el("div", { class: "bd", style: "padding-top:8px" });
    const p = panel(spec.title, spec.right || null, el("div", { class: "mx-vals" }, ids.map((id, i) => valChip(id, spec.labels?.[i]))), host, metaLine(ids));
    p.dataset.ids = ids.join(",");
    requestAnimationFrame(async () => {
      const all = await Promise.all(ids.map(getSer));
      const t0 = spec.start || rangeStart();
      const series = ids.map((id, i) => ({ id, name: spec.labels?.[i] || META(id).short || META(id).title, pts: disp(META(id), all[i].obs || []).filter(p => p[0] >= t0).map(([d, v]) => [pd(d), v]), color: spec.colors?.[i] ? css(spec.colors[i]) : null }));
      const m0 = META(ids[0]);
      const fmt = v => fv(m0, v);
      if (spec.kind === "bar") {
        const s0 = series[0];
        const pts = s0.pts.slice(-(spec.bars || 36));
        barChart(host, pts.map(([t, v]) => { const d = t.toISOString().slice(0, 10); return { k: periodLabel(m0, d), vals: [{ v, name: s0.name, color: v >= 0 ? css("--c1") : css("--down"), cell: { id: ids[0], d } }] }; }), { height: spec.height || 220, yfmt: v => fv(m0, v), tfmt: fmt, onBar: c => openMacro(c.id, c.d) });
      } else {
        lineChart(host, series, { height: spec.height || 220, yfmt: v => fv({ ...m0, dp: Math.min(m0.dp ?? 2, Math.abs(v) >= 100 ? 0 : 1) }, v), tfmt: fmt, step: spec.step, zero: spec.zero, area: series.length === 1,
          onClick: t => { const d = nearest(all[0].obs, t); if (d) openMacro(ids[0], d); } });
        host.append(el("div", { class: "faint", style: "font-size:11px;margin-top:4px" }, "Click the chart or a value to open the source row."));
      }
    });
    return p;
  }
  function nearest(obs, t) {
    if (!obs?.length) return null;
    const x = t instanceof Date ? t.getTime() : pd(t).getTime();
    let best = null, bd = Infinity;
    for (const [d] of obs) { const dd = Math.abs(pd(d).getTime() - x); if (dd < bd) { bd = dd; best = d; } }
    return best;
  }
  function gridOf(cls, panels) { const g = el("div", { class: "grid " + cls }); panels.filter(Boolean).forEach(p => g.append(p)); return g.children.length ? g : null; }

  /* ---------- tables ---------- */
  function latestTable(title, ids, cols, opts = {}) {
    ids = ids.filter(id => META(id)?.stats && !META(id).hidden);
    if (!ids.length) return null;
    const wrap = el("div", { class: "mx-tbl" });
    const p = panel(title, opts.right || null, wrap, metaLine(ids));
    (async () => {
      const all = await Promise.all(ids.map(getSer));
      const t = el("table", { class: "t" }, el("thead", {}, el("tr", {}, el("th", {}, opts.first || "Series"), cols.map(c => el("th", { class: "r" }, c.name)))));
      const tb = el("tbody");
      ids.forEach((id, i) => {
        const m = META(id), ds = disp(m, all[i].obs || []);
        tb.append(el("tr", {}, el("td", {}, opts.label ? opts.label(m) : m.short || m.title), cols.map(c => {
          const r = c.get(m, ds);
          if (!r || r.v == null) return el("td", { class: "r faint" }, "–");
          return el("td", { class: "c " + (c.color ? cls(r.v) : ""), title: `${m.title}, ${periodLabel(m, r.d)}. Open the source`, onclick: () => openMacro(id, r.d) }, c.fmt ? c.fmt(m, r.v) : fv(m, r.v));
        })));
      });
      t.append(tb); wrap.append(t);
    })();
    return p;
  }
  const COL = {
    last: { name: "Latest", get: (m, ds) => ds.length ? { d: ds.at(-1)[0], v: ds.at(-1)[1] } : null },
    prev: { name: "Prior", get: (m, ds) => ds.length > 1 ? { d: ds.at(-2)[0], v: ds.at(-2)[1] } : null },
    chg: { name: "Change", color: true, get: (m, ds) => ds.length > 1 ? { d: ds.at(-1)[0], v: ds.at(-1)[1] - ds.at(-2)[1] } : null, fmt: fchg },
    yago: { name: "Year ago", get: (m, ds) => { if (!ds.length) return null; const t = pd(ds.at(-1)[0]) - 365 * 864e5; const r = [...ds].reverse().find(x => pd(x[0]) <= t + 5 * 864e5); return r ? { d: r[0], v: r[1] } : null; } },
    avg3: { name: "3-month avg", get: (m, ds) => ds.length >= 3 ? { d: ds.at(-1)[0], v: ds.slice(-3).reduce((a, x) => a + x[1], 0) / 3 } : null },
  };

  function ipoTable(frequency) {
    const ids = Object.keys(MS.cat.series).filter(id => {
      const m = META(id);
      return id.startsWith("SEC_IPO:") && m.group === "sec_ipo" && m.frequency === frequency && !m.hidden;
    }).sort((a, b) => (META(a).source_column || a).localeCompare(META(b).source_column || b));
    if (!ids.length) return null;
    const anchor = META(`SEC_IPO:COUNT_TOTAL:${frequency}`)?.stats?.last;
    const d = Array.isArray(anchor) && anchor.length === 2 && Number.isFinite(anchor[1]) ? anchor[0] : null;
    const table = el("table", { class: "t" }, el("thead", {}, el("tr", {},
      el("th", {}, "Source measure"), el("th", {}, "Source period"), el("th", { class: "r" }, "Reported value"), el("th", {}, "Units"))));
    const body = el("tbody");
    for (const id of ids) {
      const m = META(id), last = m.stats?.last, cell = el("td", { class: "r num" });
      const show = value => cell.replaceChildren(Number.isFinite(value)
        ? el("button", { class: "chip link acc mono",
            title: `${m.title}, ${periodLabel(m, d)} (${d}), ${m.units}. Open the source row`, onclick: () => openMacro(id, d) }, fv(m, value))
        : "–");
      show(d && last?.[0] === d ? last[1] : null);
      // A different latest period cannot stand in for the count anchor.
      // Load history only when needed, and require an exact date match.
      if (d && last && last[0] !== d) getSer(id).then(s => show((s.obs || []).find(row => row[0] === d)?.[1]));
      body.append(el("tr", { "data-series-id": id, "data-period": d || "" },
        el("td", {}, m.source_label || m.title), el("td", { class: "mono" }, periodLabel(m, d) || "–"), cell,
        el("td", { class: "muted" }, m.units)));
    }
    table.append(body);
    const headings = [...new Set(ids.map(id => META(id).source_range_heading).filter(Boolean))];
    return panel(`IPO summary statistics — ${frequency === "A" ? "annual" : "quarterly"}`,
      el("a", { href: META(ids[0]).data_url, target: "_blank", rel: "noopener", class: "btn" }, "SEC workbook ↗"),
      el("div", { class: "mx-meta" }, headings.map(heading => el("span", {}, heading))),
      el("div", { class: "mx-tbl" }, table),
      el("div", { class: "mx-meta" }, "Annual and quarterly periods remain separate. Every measure uses the latest total IPO count period for its frequency; a dash means no value for that period. Average and median proceeds are not additive."),
      metaLine(ids));
  }

  /* ---------- page ---------- */
  const SECTIONS = [
    ["rates", "Rates and liquidity", () => [
      gridOf("g2", [curvePanel(),
        chartPanel({ title: "Policy and overnight rates", ids: ["NYFED:EFFR", "NYFED:SOFR", "NYFED:OBFR", "FRB:H15:RIFSRP_F02_N.B"], step: true })]),
      gridOf("g2", [chartPanel({ title: "Treasury yields", ids: ["TSY:PAR:2Yr", "TSY:PAR:10Yr", "TSY:PAR:30Yr", "TSY:BILL:13WEEKS"] }),
        chartPanel({ title: "Curve spreads", ids: ["TSY:SPREAD:2S10S", "TSY:SPREAD:3M10Y"], zero: true })]),
      gridOf("g2", [chartPanel({ title: "Real yields and breakeven inflation", ids: ["TSY:REAL:10YR", "TSY:REAL:5YR", "TSY:SPREAD:BE10"] }),
        chartPanel({ title: "Repo rates and SOFR distribution", ids: ["NYFED:TGCR", "NYFED:BGCR", "NYFED:SOFR_P99", "NYFED:SOFR30"] })]),
      gridOf("g2", [chartPanel({ title: "Federal Reserve balance sheet", ids: ["FRB:H41:RESPPA_N.WW", "FRB:H41:RESPPALGUO_N.WW", "FRB:H41:RESPPALGASMO_N.WW"] }),
        chartPanel({ title: "Reserves, Treasury cash and reverse repo", ids: ["FRB:H41:RESH4R_N.WW", "FRB:H41:RESPPLLDT_N.WW", "FRB:H41:RESPPLLRD_N.WW"] })]),
      gridOf("g2", [chartPanel({ title: "Fed repo and reverse repo operations", ids: ["NYFED:RRP", "NYFED:REPO"] }),
        chartPanel({ title: "SOMA holdings", ids: ["NYFED:SOMA_TOTAL", "NYFED:SOMA_NOTESBONDS", "NYFED:SOMA_MBS", "NYFED:SOMA_BILLS"] })]),
      gridOf("g3", [chartPanel({ title: "Commercial paper", ids: ["FRB:H15:RIFSPPNAAD90_N.B", "FRB:H15:RIFSPPFAAD90_N.B"] }),
        chartPanel({ title: "Money stock, year over year", ids: ["FRB:H6:M2.M", "FRB:H6:M1.M"], zero: true }),
        chartPanel({ title: "Funding volumes", ids: ["NYFED:SOFR_VOL", "NYFED:EFFR_VOL"] })]),
    ]],
    ["inflation", "Inflation", () => [
      gridOf("g2", [chartPanel({ title: "CPI, year over year", ids: ["BLS:CUUR0000SA0", "BLS:CUUR0000SA0L1E"], zero: true }),
        chartPanel({ title: "Core CPI, month over month", ids: ["BLS:CUSR0000SA0L1E"], kind: "bar", bars: 36 })]),
      gridOf("g2", [chartPanel({ title: "CPI components, year over year", ids: ["BLS:CUUR0000SAH1", "BLS:CUUR0000SASLE", "BLS:CUUR0000SACL1E", "BLS:CUUR0000SA0E"], zero: true }),
        latestTable("CPI components", ["BLS:CUUR0000SA0", "BLS:CUUR0000SA0L1E", "BLS:CUUR0000SAF1", "BLS:CUUR0000SA0E", "BLS:CUUR0000SAH1", "BLS:CUUR0000SASLE", "BLS:CUUR0000SACL1E", "BLS:CUUR0000SAM", "BLS:CUUR0000SETA02", "BLS:CUUR0000SAS4", "BLS:CUSR0000SA0", "BLS:CUSR0000SA0L1E"], [COL.last, COL.prev, COL.chg, COL.yago], { first: "Year over year unless noted" })]),
      gridOf("g2", [chartPanel({ title: "10-year breakeven inflation", ids: ["TSY:SPREAD:BE10"] }),
        chartPanel({ title: "Euro area, G20 and China consumer prices", ids: ["OECD:CPI:G20", "OECD:CPI:EA", "OECD:CPI:CHN", "OECD:CPI:USA"], zero: true })]),
    ]],
    ["labor", "Labor", () => [
      gridOf("g2", [chartPanel({ title: "Nonfarm payrolls, monthly change", ids: ["BLS:CES0000000001"], kind: "bar", bars: 36 }),
        chartPanel({ title: "Unemployment", ids: ["BLS:LNS14000000", "BLS:LNS13327709"] })]),
      gridOf("g2", [latestTable("Payrolls by sector, monthly change (thousands)", ["BLS:CES0000000001", "BLS:CES0500000001", "BLS:CES0600000001", "BLS:CES2000000001", "BLS:CES3000000001", "BLS:CES4000000001", "BLS:CES5000000001", "BLS:CES5500000001", "BLS:CES6000000001", "BLS:CES6500000001", "BLS:CES7000000001", "BLS:CES8000000001", "BLS:CES9000000001"], [COL.last, COL.prev, COL.avg3], { first: "Sector" }),
        chartPanel({ title: "Job openings and turnover rates", ids: ["BLS:JTS000000000000000JOR", "BLS:JTS000000000000000HIR", "BLS:JTS000000000000000QUR", "BLS:JTS000000000000000LDR"] })]),
      gridOf("g3", [chartPanel({ title: "Participation", ids: ["BLS:LNS11300000", "BLS:LNS12300060"] }),
        chartPanel({ title: "Wages and compensation", ids: ["BLS:CES0500000003", "BLS:CIU1010000000000A", "BLS:CIU2020000000000A"] }),
        chartPanel({ title: "Job openings", ids: ["BLS:JTS000000000000000JOL"] })]),
    ]],
    ["growth", "Growth and activity", () => [
      gridOf("g3", [chartPanel({ title: "Industrial production, year over year", ids: ["FRB:G17:IP.B50001.S", "FRB:G17:IP.GMF.S"], zero: true }),
        chartPanel({ title: "Capacity utilization", ids: ["FRB:G17:CAPUTL.B50001.S", "FRB:G17:CAPUTL.GMF.S"] }),
        chartPanel({ title: "Leading indicators, United States (OECD)", ids: ["OECD:CLI:USA", "OECD:BCI:USA", "OECD:CCI:USA"] })]),
      gridOf("g2", [chartPanel({ title: "Real GDP growth, quarter over quarter (OECD)", ids: ["OECD:GDP:USA", "OECD:GDP:OECD", "OECD:GDP:CHN", "OECD:GDP:EA"], kind: "line", zero: true }),
        chartPanel({ title: "Composite leading indicators (OECD)", ids: ["OECD:CLI:USA", "OECD:CLI:G7", "OECD:CLI:CHN", "OECD:CLI:DEU"] })]),
    ]],
    ["credit", "Credit and banking", () => [
      gridOf("g2", [chartPanel({ title: "Bank lending standards (SLOOS), net percentage tightening", ids: ["FRB:SLOOS:SUBLPDCILS_N.Q", "FRB:SLOOS:SUBLPDCISS_N.Q", "FRB:SLOOS:SUBLPDRCSN_N.Q", "FRB:SLOOS:SUBLPDCLCS_N.Q"], zero: true, start: "2005-01-01" }),
        chartPanel({ title: "Commercial bank loans (H.8)", ids: ["FRB:H8:B1023NCBA", "FRB:H8:B1026NCBA", "FRB:H8:B3219NCBA", "FRB:H8:B1029NCBA"] })]),
      gridOf("g2", [chartPanel({ title: "Bank deposits and borrowings (H.8)", ids: ["FRB:H8:B1058NCBA", "FRB:H8:B1058NSMA", "FRB:H8:B3094NCBA"] }),
        chartPanel({ title: "Consumer credit (G.19)", ids: ["FRB:G19:DTCTL.M", "FRB:G19:DTCTLN.M", "FRB:G19:DTCTLR.M"] })]),
      gridOf("g3", [chartPanel({ title: "Dealer financing terms (SCOOS)", ids: ["FRB:SCOOS:EXH_E1_C3.Q4.NP", "FRB:SCOOS:EXH_E1_C5.Q5.NP", "FRB:SCOOS:EXH_E2_C1.Q8.NP"], zero: true, start: "2010-01-01" }),
        chartPanel({ title: "Household debt service ratios", ids: ["FRB:DSR:DSR_RATIO", "FRB:DSR:MORTGAGE_DSR_RATIO", "FRB:DSR:CONSUMER_DSR_RATIO"], start: "2005-01-01" }),
        chartPanel({ title: "Credit card interest rate", ids: ["FRB:G19:RIFSPBCICCI_N.M"] })]),
      gridOf("g3", [chartPanel({ title: "Debt outstanding by sector (Z.1)", ids: ["FRB:Z1:LA154104005.Q", "FRB:Z1:LA104104005.Q", "FRB:Z1:LA314104005.Q"], start: "2000-01-01" }),
        chartPanel({ title: "Household net worth (Z.1)", ids: ["FRB:Z1:FL152090005.Q"], start: "2000-01-01" }),
        chartPanel({ title: "Bank failures per year (FDIC)", ids: ["FDIC:FAILURES"], kind: "bar", bars: 20 })]),
      gridOf("g2", [latestTable("FDIC-insured institutions, year end", ["FDIC:ASSET", "FDIC:DEP", "FDIC:NETINC", "FDIC:NTLNLS", "FDIC:BANKS", "FDIC:FAILED_ASSETS"], [COL.last, COL.prev, COL.chg], { first: "Measure" }),
        chartPanel({ title: "Funding demand reported by dealers (SCOOS)", ids: ["FRB:SCOOS:EXH_E3_C1.Q53.NP", "FRB:SCOOS:EXH_E3_C1.Q57.NP", "FRB:SCOOS:EXH_E3_C2.Q61.NP"], zero: true, start: "2010-01-01" })]),
      ipoTable("A"), ipoTable("Q"),
    ]],
    ["global", "Global and OECD", () => [oecdTable(),
      gridOf("g2", [chartPanel({ title: "Unemployment rates (OECD)", ids: ["OECD:UNR:USA", "OECD:UNR:EA", "OECD:UNR:JPN", "OECD:UNR:GBR"] }),
        chartPanel({ title: "Long-term interest rates (OECD)", ids: ["OECD:IRLT:USA", "OECD:IRLT:DEU", "OECD:IRLT:JPN", "OECD:IRLT:GBR"] })])]],
    ["positioning", "Positioning", () => [cotTable(),
      gridOf("g2", [chartPanel({ title: "Treasury futures, leveraged funds net (contracts)", ids: ["CFTC:042601:LEV", "CFTC:044601:LEV", "CFTC:043602:LEV"], zero: true }),
        chartPanel({ title: "E-mini S&P 500 net positions (contracts)", ids: ["CFTC:13874A:AM", "CFTC:13874A:LEV"], zero: true })]),
      gridOf("g2", [chartPanel({ title: "Commodities, managed money net (contracts)", ids: ["CFTC:067651:MM", "CFTC:088691:MM", "CFTC:085692:MM"], zero: true }),
        chartPanel({ title: "Primary dealer net positions", ids: ["NYFED:PD:PDPOSGST-TOT", "NYFED:PD:PDPOSCS-TOT", "NYFED:PD:PDPOSMBS-TOT"], zero: true })])]],
  ];

  function curvePanel() {
    const ten = ["1Mo", "3Mo", "6Mo", "1Yr", "2Yr", "5Yr", "10Yr", "30Yr"], ids = ten.map(t => "TSY:PAR:" + t);
    if (!ids.every(id => META(id)?.stats)) return null;
    const host = el("div", { class: "bd" });
    const p = panel("Treasury par yield curve", el("span", { class: "muted mono", style: "font-size:12px" }, fdate(META(ids[6]).stats.last[0])), el("div", { class: "mx-vals" }, ["TSY:PAR:2Yr", "TSY:PAR:10Yr", "TSY:PAR:30Yr"].map(id => valChip(id))), host, metaLine(ids));
    requestAnimationFrame(async () => {
      const all = await Promise.all(ids.map(getSer));
      const last = META(ids[6]).stats.last[0];
      const at = (s, t) => { const r = [...s.obs].reverse().find(o => o[0] <= t); return r || null; };
      const back = days => new Date(pd(last) - days * 864e5).toISOString().slice(0, 10);
      const pick = t => ten.map((k, i) => { const r = at(all[i], t); return [k.replace("Mo", "M").replace("Yr", "Y"), r ? r[1] : null, r && r[0]]; });
      const now = pick(last), m3 = pick(back(91)), y1 = pick(back(365));
      lineChart(host, [{ name: "Latest " + fdate(last), pts: now }, { name: "3 months earlier", pts: m3, color: css("--c3"), dash: "4 3" }, { name: "1 year earlier", pts: y1, color: css("--c6"), dash: "2 3" }],
        { band: now.map(x => x[0]), height: 240, yfmt: v => v.toFixed(2) + "%", dots: true, onClick: k => { const i = now.findIndex(x => x[0] === k); if (i >= 0 && now[i][2]) openMacro(ids[i], now[i][2]); } });
      host.append(el("div", { class: "faint", style: "font-size:11px;margin-top:4px" }, "Click a tenor to open the Treasury source row."));
    });
    return p;
  }

  function oecdTable() {
    const S = MS.cat.series;
    const IND = [["CLI", "Leading indicator"], ["CPI", "CPI y/y"], ["GDP", "GDP q/q"], ["UNR", "Unemployment"], ["IR3", "3M rate"], ["IRLT", "10Y rate"], ["BCI", "Business conf."], ["CCI", "Consumer conf."]];
    const areas = [...new Set(Object.values(S).filter(m => m.indicator && m.area).map(m => m.area))];
    const ORDER = ["USA", "CAN", "MEX", "GBR", "DEU", "FRA", "ITA", "EA20", "EA", "EU27_2020", "EU", "JPN", "KOR", "AUS", "CHN", "IND", "IDN", "BRA", "ARG", "ZAF", "SAU", "TUR", "G7", "G20", "OECD"];
    areas.sort((a, b) => (ORDER.indexOf(a) + 1 || 99) - (ORDER.indexOf(b) + 1 || 99));
    if (!areas.length) return null;
    const t = el("table", { class: "t" }, el("thead", {}, el("tr", {}, el("th", {}, "Area"), IND.map(([, n]) => el("th", { class: "r" }, n)))));
    const tb = el("tbody");
    areas.forEach(a => {
      const geo = Object.values(S).find(m => m.area === a)?.geography || a;
      tb.append(el("tr", {}, el("td", {}, geo), IND.map(([k]) => {
        const id = `OECD:${k}:${a}`, m = S[id];
        if (!m?.stats) return el("td", { class: "r faint" }, "–");
        const st = m.stats, stale = (new Date() - pd(st.last[0])) / 864e5 > (m.frequency === "Q" ? 270 : 150);
        return el("td", { class: "c" + (stale ? " faint" : ""), title: `${m.title}, ${periodLabel(m, st.last[0])}. Open the source`, onclick: () => openMacro(id, st.last[0]) }, fv(m, st.last[1]), el("div", { class: "faint", style: "font-size:10.5px" }, periodLabel(m, st.last[0])));
      })));
    });
    t.append(tb);
    const any = S["OECD:CLI:USA"] || Object.values(S).find(m => m.provider === "OECD");
    return panel("G20 and OECD economies, latest values", el("span", { class: "faint", style: "font-size:12px" }, "OECD Data Explorer, SDMX API"), el("div", { class: "mx-tbl" }, t),
      el("div", { class: "mx-meta" }, el("span", {}, "Amplitude-adjusted CLI and confidence indexes: 100 is the long-term average. Grey values are older than the usual publication lag."), any ? el("a", { href: "https://data-explorer.oecd.org/", target: "_blank", rel: "noopener" }, "OECD Data Explorer ↗") : null));
  }

  function cotTable() {
    const S = MS.cat.series;
    const ids = Object.keys(S).filter(id => id.startsWith("CFTC:") && S[id].stats).sort((a, b) => (S[a].group + S[a].market + S[a].measure).localeCompare(S[b].group + S[b].market + S[b].measure));
    if (!ids.length) return null;
    const NAME = { LEV: "Leveraged funds", AM: "Asset managers", MM: "Managed money" };
    const t = el("table", { class: "t" }, el("thead", {}, el("tr", {}, el("th", {}, "Market"), el("th", {}, "Trader group"), el("th", { class: "r" }, "Net (contracts)"), el("th", { class: "r" }, "Week change"), el("th", { class: "r" }, "z-score of change"), el("th", { class: "r" }, "10y percentile"), el("th", { class: "r" }, "As of"))));
    const tb = el("tbody");
    ids.forEach(id => {
      const m = S[id], st = m.stats;
      tb.append(el("tr", {}, el("td", {}, m.market), el("td", { class: "muted" }, NAME[m.measure] || m.measure),
        el("td", { class: "c", title: "Open the CFTC source row", onclick: () => openMacro(id, st.last[0]) }, fv(m, st.last[1])),
        el("td", { class: "c " + cls(st.chg), onclick: () => openMacro(id, st.last[0]) }, fchg(m, st.chg)),
        el("td", { class: "c " + (Math.abs(st.z || 0) >= 2 ? "down" : ""), title: "Computed from the published history. Open the source row", onclick: () => openMacro(id, st.last[0], "history") }, st.z == null ? "–" : st.z.toFixed(1)),
        el("td", { class: "c", title: "Share of the past ten years at or below the latest value. Open the history", onclick: () => openMacro(id, st.last[0], "history") }, st.pctile == null ? "–" : Math.round(st.pctile * 100) + "%"),
        el("td", { class: "r faint" }, fdate(st.last[0]))));
    });
    t.append(tb);
    const m0 = S[ids[0]];
    return panel("Commitments of Traders, latest week", el("span", { class: "faint", style: "font-size:12px" }, "Net = long minus short"), el("div", { class: "mx-tbl" }, t),
      el("div", { class: "mx-meta" }, el("span", {}, "Futures only. Traders in Financial Futures for financial contracts, Disaggregated report for commodities."), m0.next_release ? el("span", {}, `Next release ${fdate(m0.next_release[0])} 3:30 p.m. ET`) : null,
        el("a", { href: "https://www.cftc.gov/MarketReports/CommitmentsofTraders/index.htm", target: "_blank", rel: "noopener" }, "CFTC Commitments of Traders ↗")));
  }

  function changesPanel(c) {
    const box = el("div", { class: "mx-list" });
    (c.changes || []).forEach(x => { const m = META(x.id); if (!m) return; box.append(el("div", { class: "it", onclick: () => openMacro(x.id, x.d), title: "Open the source row" }, el("span", { class: "z num " + (x.z > 0 ? "up" : "down") }, (x.z > 0 ? "+" : "−") + Math.abs(x.z).toFixed(1) + "σ"), el("span", {}, x.text, el("span", { class: "faint", style: "font-size:11px;display:block" }, `${m.provider} · ${periodLabel(m, x.d)}`)))); });
    if (!box.children.length) box.append(el("div", { class: "empty" }, "No series published a new observation in the last seven days."));
    return panel("What changed this week", el("span", { class: "faint", style: "font-size:12px" }, "Largest moves against 10 years of history"), box,
      el("div", { class: "mx-meta" }, el("span", {}, "Ranked by the size of the latest move (five trading days for daily series, one period otherwise) in standard deviations of past moves.")));
  }
  function calendarPanel(c) {
    // one row per release: its next scheduled date (daily releases would otherwise fill the list)
    const seen = new Set(), rows = (c.calendar || []).filter(r => !seen.has(r.release) && seen.add(r.release));
    const t = el("table", { class: "mx-cal" });
    rows.forEach(r => t.append(el("tr", {}, el("td", { class: "dt" }, fdate(r.release_date), el("div", { class: "faint", style: "font-size:11px" }, r.release_time || "")),
      el("td", {}, el("a", { href: r.source_url, target: "_blank", rel: "noopener" }, r.title), el("div", { class: "faint", style: "font-size:11px" }, `${r.provider} · ${r.series.length} series on this page${(c.calendar || []).filter(x => x.release === r.release).length > 3 ? " · then " + (c.calendar || []).filter(x => x.release === r.release).slice(1, 3).map(x => fdate(x.release_date).replace(/, \d{4}$/, "")).join(", ") : ""}`)))));
    if (!rows.length) t.append(el("tr", {}, el("td", { class: "faint" }, "No scheduled releases in the next 45 days.")));
    return panel("Release calendar", el("span", { class: "faint", style: "font-size:12px" }, "From the publishers' schedules"), el("div", { style: "max-height:420px;overflow:auto" }, t));
  }

  async function pageMacro() {
    const [c] = await Promise.all([getCat(), MACRO ? null : getJSON("macro.json").then(applyMacro).catch(() => null)]);
    const n = Object.values(c.series).filter(m => !m.hidden).length;
    const providers = [...new Set(Object.values(c.series).filter(m => !m.hidden).map(m => m.provider))];
    const rng = el("div", { class: "seg mx-rng" }, ["2Y", "5Y", "10Y", "2008", "Max"].map(r => el("button", { class: MS.range === r ? "on" : "", onclick: () => { MS.range = r; store.set("mrange", r); route(); } }, r === "2008" ? "Since 2008" : r)));
    page.append(el("div", { class: "ph" }, el("div", {}, el("div", { class: "eyebrow" }, "Macro"), el("h1", {}, "Macro and markets data"),
      el("div", { class: "sub" }, `${n} series from ${providers.length} official publishers: ${providers.join(", ")}. Every value opens the publisher's own row.`)), el("div", { class: "right" }, rng)));
    const nav = el("div", { class: "seg", style: "flex-wrap:wrap" }, SECTIONS.map(([id, name]) => el("button", { onclick: () => document.getElementById("mx-" + id)?.scrollIntoView({ behavior: "smooth", block: "start" }) }, name)));
    page.append(nav);
    page.append(gridOf("g64", [changesPanel(c), calendarPanel(c)]));
    if (E?.macro_brief) { const mb = E.macro_brief; page.append(panel("Macro brief", aiCredit(mb.meta), el("div", { class: "bd brief" }, el("h2", {}, mb.headline), el("ul", {}, (mb.bullets || []).map(b => el("li", { html: esc(b).replace(/\[(\d+)\]/g, (_, k) => { const it = E.macro_news?.[+k - 1]; return it ? `<a href="${esc(it.url)}" target="_blank" rel="noopener" class="mono" style="font-size:11px">[${k}]</a>` : ""; }) })))))); }
    for (const [id, name, build] of SECTIONS) {
      const sec = el("section", { class: "mx-sec", id: "mx-" + id }, el("h2", {}, name, el("small", {}, sectionNote(c, id))));
      build().filter(Boolean).forEach(x => sec.append(x));
      if (sec.children.length > 1) page.append(sec);
    }
    page.append(el("div", { class: "faint", style: "font-size:11.5px" }, `Catalog built ${new Date(c.generated).toLocaleString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}. Observations keep every published vintage; revisions show in each value's history.`));
  }
  function sectionNote(c, sec) {
    const ms = Object.values(c.series).filter(m => m.section === sec && !m.hidden);
    return `${ms.length} series · ${[...new Set(ms.map(m => m.provider))].join(", ")}`;
  }

  /* ---------- source panel ---------- */
  function inputsFor(m, obs, d) {
    // the stored observations that produce the displayed value at date d
    const i = obs.findIndex(o => o[0] === d); if (i < 0) return [];
    const k = m.display || "level";
    if (k === "yoy") { const key = m.frequency === "A" ? x => x.slice(0, 4) : x => x.slice(0, 7); const want = String(+d.slice(0, 4) - 1) + key(d).slice(4); const p = obs.find(o => key(o[0]) === want); return p ? [obs[i], p] : [obs[i]]; }
    if (k === "mom" || k === "chg") return i > 0 ? [obs[i], obs[i - 1]] : [obs[i]];
    return [obs[i]];
  }
  function formula(m, ins) {
    const k = m.display || "level", raw = v => v.toLocaleString("en-US", { maximumFractionDigits: 6 });
    if (k === "yoy" && ins.length === 2) return `${raw(ins[0][1])} ÷ ${raw(ins[1][1])} − 1 = ${fv(m, (ins[0][1] / ins[1][1] - 1) * 100)}   (${periodLabel(m, ins[0][0])} over ${periodLabel(m, ins[1][0])})`;
    if (k === "mom" && ins.length === 2) return `${raw(ins[0][1])} ÷ ${raw(ins[1][1])} − 1 = ${fv(m, (ins[0][1] / ins[1][1] - 1) * 100)}`;
    if (k === "chg" && ins.length === 2) return `${raw(ins[0][1])} − ${raw(ins[1][1])} = ${fv(m, (ins[0][1] - ins[1][1]) * (m.scale || 1))}`;
    if ((m.scale || 1) !== 1 && ins.length) return `${raw(ins[0][1])} ${m.units ? "(" + m.units.toLowerCase() + ")" : ""} = ${fv(m, ins[0][1] * m.scale)}`;
    return null;
  }
  function citeText(m, d, v, s) {
    return `${m.title}, ${periodLabel(m, d)}: ${fv(m, v)}. Source: ${m.provider}, ${m.source}; series ${m.source_key}; ${m.data_url}${s?.retrieved ? " (retrieved " + s.retrieved.slice(0, 10) + ")" : ""}.`;
  }
  async function openMacro(id, d, tab) {
    hideTip();
    if (!MS.cat) await getCat();
    const m = META(id); if (!m) return;
    DR.cell = null;
    MDR.id = id; MDR.d = d; if (tab) MDR.tab = tab;
    const dh = $("#dh"), db = $("#db");
    dh.innerHTML = ""; db.innerHTML = "";
    drawer.classList.add("open"); drawer.setAttribute("aria-hidden", "false"); if (innerWidth < 860) scrim.hidden = false;
    const s = await getSer(id);
    if (MDR.id !== id || MDR.d !== d) return;
    const obs = s.obs || [], ds = disp(m, obs), dv = new Map(ds), v = dv.get(d);
    const i = obs.findIndex(o => o[0] === d);
    const go = j => { const o = ds[j]; if (o) openMacro(id, o[0]); };
    const j = ds.findIndex(o => o[0] === d);
    dh.append(
      el("div", { class: "row" }, el("span", { class: "eyebrow" }, m.derived || (m.display && m.display !== "level") ? "Calculated from published values" : "Published value"),
        el("div", { class: "x", style: "display:flex;gap:6px" },
          el("button", { class: "btn", title: "Copy citation (c)", onclick: () => copyText(citeText(m, d, v, s), "Citation copied") }, svgUse("i-copy"), "Cite"),
          el("a", { class: "btn", href: m.data_url, target: "_blank", rel: "noopener" }, "Data", svgUse("i-ext")),
          el("button", { class: "iconbtn", onclick: closeSource, "aria-label": "Close source (Esc)" }, svgUse("i-x")))),
      el("div", { class: "row" }, el("h2", {}, `${m.provider} · ${m.title}`)),
      el("div", { class: "row" }, el("span", { class: "chip acc mono", style: "font-size:13.5px;height:26px" }, fv(m, v)), el("span", { class: "muted", style: "font-size:12.5px" }, `${periodLabel(m, d)}${m.seasonal_adj ? " · " + m.seasonal_adj : ""}`),
        el("span", { style: "margin-left:auto" }, el("div", { class: "pnav" },
          el("button", { "aria-label": "Previous observation", title: "Previous (←)", disabled: j <= 0, onclick: () => go(j - 1) }, svgUse("i-left")),
          el("button", { "aria-label": "Next observation", title: "Next (→)", disabled: j < 0 || j >= ds.length - 1, onclick: () => go(j + 1) }, svgUse("i-right"))))));
    const tabs = el("div", { class: "dtabs", role: "tablist" });
    [["source", "Source"], ["details", "Details"], ["history", "History"]].forEach(([t, n]) => tabs.append(el("button", { class: MDR.tab === t ? "on" : "", role: "tab", onclick: () => openMacro(id, d, t) }, n)));
    db.append(tabs);
    const body = el("div", { style: "display:flex;flex-direction:column;gap:14px;padding:14px 18px 24px" });
    db.append(body);
    const ins = inputsFor(m, obs, d);
    if (MDR.tab === "details") return details(body, m, d, s, i);
    if (MDR.tab === "history") return history(body, m, id, ds, d, s);
    const f = formula(m, ins);
    if (f || m.derived) body.append(el("div", { class: "calc-box" }, el("div", { class: "eyebrow" }, "How this value is computed"), m.derived ? el("div", {}, m.derived + ".") : null, f ? el("div", { class: "eq mono" }, f) : null,
      (m.inputs || []).length ? el("div", { class: "muted", style: "font-size:12px" }, "Inputs: ", m.inputs.map((x, k) => [k ? ", " : "", el("a", { href: "#", onclick: e => { e.preventDefault(); openMacro(x, d); } }, META(x)?.title || x)])) : null));
    body.append(el("div", { class: "muted", style: "font-size:12px" }, `Rows as published in ${m.provider}'s file. Retrieved ${s.retrieved ? fdate(s.retrieved.slice(0, 10)) : "–"}.`));
    const host = el("div", {}, el("div", { class: "skel", style: "height:220px" }));
    body.append(host);
    const snap = await getSnap(id);
    if (MDR.id !== id || MDR.d !== d) return;
    host.innerHTML = "";
    if (!snap?.rows?.length) { host.append(el("div", { class: "note" }, "No stored snapshot for this series yet. Open the data link above for the publisher's file.")); return; }
    host.append(el("div", { class: "kv", style: "margin-bottom:10px" }, el("dt", {}, "File"), el("dd", {}, el("a", { href: snap.url, target: "_blank", rel: "noopener" }, snap.file || snap.url)), el("dt", {}, "Series"), el("dd", { class: "mono" }, snap.series || m.source_key), snap.note ? el("dt", {}, "Note") : null, snap.note ? el("dd", {}, snap.note) : null));
    const keys = snap.keys || [];
    const main = new Set([d]), alt = new Set(ins.slice(1).map(x => x[0]));
    let at = keys.findIndex(k => k === d); if (at < 0) at = keys.length - 1;
    const lo = Math.max(0, at - 30), hi = Math.min(keys.length, at + 31);
    const hl = Array.isArray(snap.hl) ? snap.hl : snap.hl != null ? [snap.hl] : [1];
    const t = el("table", {}, el("thead", {}, el("tr", {}, snap.cols.map(c => el("th", {}, c)))));
    const tb = el("tbody");
    let first = null;
    for (let r = hi - 1; r >= lo; r--) {
      const isMain = main.has(keys[r]), isAlt = alt.has(keys[r]);
      const tr = el("tr", { class: isMain ? "hl" : isAlt ? "hl2" : "" }, snap.rows[r].map((c, k) => el("td", { class: (isMain || isAlt) && hl.includes(k) ? "hit" : "" }, c)));
      if (isMain && !first) first = tr;
      tb.append(tr);
    }
    t.append(tb);
    const box = el("div", { class: "mx-snap" }, t);
    host.append(box, el("div", { class: "faint", style: "font-size:11px;margin-top:6px" }, `Rows ${lo + 1} to ${hi} of ${keys.length}, newest first. Highlighted: the row for ${periodLabel(m, d)}${alt.size ? " and the comparison row used in the calculation" : ""}.`));
    if (first) requestAnimationFrame(() => { box.scrollTop = Math.max(0, first.offsetTop - box.clientHeight / 2); first.querySelectorAll("td.hit")[0]?.animate?.([{ outlineColor: "transparent" }, { outlineColor: css("--mark") }], { duration: 600, iterations: 2 }); });
  }
  function details(body, m, d, s, i) {
    const vin = s.vin?.[d], rev = s.rev?.[d];
    body.append(el("dl", { class: "kv" },
      el("dt", {}, "Publisher"), el("dd", {}, m.provider),
      el("dt", {}, "Release"), el("dd", {}, m.source),
      el("dt", {}, "Series"), el("dd", { class: "mono" }, m.source_key),
      el("dt", {}, "Title"), el("dd", {}, m.title),
      el("dt", {}, "Units"), el("dd", {}, m.units + (m.dunits && m.dunits !== m.units ? ` (shown as ${m.dunits.toLowerCase()})` : "")),
      el("dt", {}, "Frequency"), el("dd", {}, FREQ_LABEL[m.frequency] || m.frequency),
      el("dt", {}, "Adjustment"), el("dd", {}, m.seasonal_adj === "SA" ? "Seasonally adjusted" : m.seasonal_adj === "NSA" ? "Not seasonally adjusted" : "Not applicable"),
      el("dt", {}, "Geography"), el("dd", {}, m.geography || "–"),
      el("dt", {}, "Stored value"), el("dd", { class: "mono" }, i >= 0 ? String(s.obs[i][1]) : "–"),
      el("dt", {}, "First seen"), el("dd", {}, vin ? fdate(vin) : "Before the latest 120 observations"),
      el("dt", {}, "File published"), el("dd", {}, s.published ? fdate(s.published) : "Not stated by the publisher; vintage is the retrieval date"),
      rev ? el("dt", {}, "Earlier values") : null, rev ? el("dd", {}, rev.map(([v, at]) => `${v} (seen ${at || "earlier"})`).join("; ")) : null,
      m.notes ? el("dt", {}, "Notes") : null, m.notes ? el("dd", {}, m.notes) : null,
      el("dt", {}, "Links"), el("dd", {}, el("a", { href: m.source_url, target: "_blank", rel: "noopener" }, "Release page ↗"), " · ", el("a", { href: m.data_url, target: "_blank", rel: "noopener" }, "Data file or API ↗"), m.release_page ? [" · ", el("a", { href: m.release_page, target: "_blank", rel: "noopener" }, "Program page ↗")] : null)));
  }
  function history(body, m, id, ds, d, s) {
    const host = el("div", { style: "min-height:200px" });
    body.append(host);
    const recent = ds.slice(-60);
    requestAnimationFrame(() => lineChart(host, [{ name: m.short || m.title, pts: recent.map(([x, v]) => [pd(x), v]) }], { height: 200, yfmt: v => fv(m, v), area: true, marks: [{ t: pd(d), label: periodLabel(m, d) }], onClick: t => { const x = nearest(recent, t); if (x) openMacro(id, x, "source"); } }));
    const t = el("table", { class: "t" }, el("thead", {}, el("tr", {}, el("th", {}, "Period"), el("th", { class: "r" }, "Value"), el("th", { class: "r" }, "Change"), el("th", {}, "First seen"))));
    const tb = el("tbody");
    recent.slice().reverse().forEach(([x, v], k, arr) => { const p = arr[k + 1]; tb.append(el("tr", { class: "click", onclick: () => openMacro(id, x, "source") }, el("td", { class: x === d ? "" : "muted" }, el("b", {}, periodLabel(m, x)), s.rev?.[x] ? el("span", { class: "chip warn", style: "margin-left:6px;height:18px" }, "revised") : null), el("td", { class: "r num" }, fv(m, v)), el("td", { class: "r num " + cls(p ? v - p[1] : 0) }, p ? fchg(m, v - p[1]) : ""), el("td", { class: "faint", style: "font-size:11.5px" }, s.vin?.[x] ? fdate(s.vin[x]) : ""))); });
    t.append(tb); body.append(t);
  }
  document.addEventListener("keydown", e => {
    if (!MDR.id || DR.cell || !drawer.classList.contains("open") || /INPUT|TEXTAREA|SELECT/.test(document.activeElement?.tagName)) return;
    if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
      const m = META(MDR.id); const s = MS.s[MDR.id];
      Promise.resolve(s).then(x => { const ds = disp(m, x.obs || []); const j = ds.findIndex(o => o[0] === MDR.d) + (e.key === "ArrowLeft" ? -1 : 1); if (ds[j]) openMacro(MDR.id, ds[j][0]); });
      e.preventDefault();
    }
    if (e.key === "c") { const m = META(MDR.id); Promise.resolve(MS.s[MDR.id]).then(s => { const v = new Map(disp(m, s.obs || [])).get(MDR.d); copyText(citeText(m, MDR.d, v, s), "Citation copied"); }); }
  });

  window.FU_PAGES.macro = pageMacro;
  window.openMacroSource = openMacro;
})();
