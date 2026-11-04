// Statements tab on the company page: financial statements as filed (SEC Financial Statement Data Sets),
// standardized templates per industry, and the company's own non-GAAP reconciliations from its KPI file.
// Registers itself in window.FU_COTABS; app.js adds it to the company tabs.
(function () {
  "use strict";
  const ST = { view: store.get("stView", "reported"), filing: null, report: null, kind: "A", basis: "latest", n: 8 };
  const STC = {};
  document.head.append(el("style", {}, "table.fin td.c.rst::before{content:\"\";position:absolute;left:3px;top:5px;width:4px;height:4px;border-radius:50%;background:var(--c5)}"));
  const STMT_LABEL = { BS: "Balance sheet", IS: "Income statement", CI: "Comprehensive income", CF: "Cash flow", EQ: "Shareholders' equity", UN: "Other statement" };
  const STMT_ORDER = { IS: 1, CI: 2, BS: 3, CF: 4, EQ: 5, UN: 6 };
  const TEMPLATE_NAME = { industrial: "Industrial and services", bank: "Bank and broker-dealer", insurer: "Insurer", reit: "REIT", utility: "Utility" };
  const TOTAL_TAGS = new Set(["Assets", "AssetsCurrent", "Liabilities", "LiabilitiesCurrent", "LiabilitiesAndStockholdersEquity", "StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest", "GrossProfit", "OperatingIncomeLoss", "NetIncomeLoss", "ProfitLoss", "Revenues", "CostsAndExpenses", "OperatingExpenses", "NetCashProvidedByUsedInOperatingActivities", "NetCashProvidedByUsedInInvestingActivities", "NetCashProvidedByUsedInFinancingActivities", "ComprehensiveIncomeNetOfTax", "InterestIncomeExpenseNet", "NoninterestIncome", "NoninterestExpense", "BenefitsLossesAndExpenses"]);

  async function gz(path) {
    const r = await fetch(PUB + path);
    if (!r.ok) throw new Error(`${r.status} loading ${path}`);
    return JSON.parse(await inflate(r));
  }
  async function loadIndex(tk) {
    if (!STC[tk]) STC[tk] = { filings: {} };
    if (!STC[tk].index) STC[tk].index = getJSON(`statements/${tk}/index.json`);
    return STC[tk].index;
  }
  async function loadFiling(tk, a) {
    STC[tk] = STC[tk] || { filings: {} };
    if (!STC[tk].filings[a]) { STC[tk].filings[a] = gz(`statements/${tk}/${a}.json.gz`); STC[tk].filings[a].catch(() => delete STC[tk].filings[a]); }
    return STC[tk].filings[a];
  }
  async function loadStd(tk) {
    STC[tk] = STC[tk] || { filings: {} };
    if (!STC[tk].std) { STC[tk].std = Promise.all([gz(`statements/${tk}/std.json.gz`), getJSON("statements/std_map.json")]); STC[tk].std.catch(() => delete STC[tk].std); }
    return STC[tk].std;
  }

  /* ---------- periods and formatting ---------- */
  const monthEnd = (y, m) => new Date(Date.UTC(y, m, 0)).toISOString().slice(0, 10); // m is 1-based month
  function shiftMonths(d, k) { const [y, m] = d.split("-").map(Number); const t = (y * 12 + (m - 1)) + k; return monthEnd(Math.floor(t / 12), (t % 12) + 1); }
  const startOf = (ddate, qtrs) => qtrs ? shiftDays(shiftMonths(ddate, -3 * qtrs), 1) : null;
  const colHead = (ddate, qtrs) => qtrs ? [`${qtrs * 3} months ended`, fdate(ddate)] : ["As of", fdate(ddate)];
  const isPerShare = u => /\//.test(u || "");
  const isShares = u => /^shares$/i.test(u || "");
  function scaleFor(facts) {
    const money = facts.filter(f => f[3] === "USD" && f[6] != null).map(f => Math.abs(f[6]));
    const sh = facts.filter(f => isShares(f[3]) && f[6] != null).map(f => Math.abs(f[6]));
    // the filer's rounding: the largest scale that nearly every value is a whole multiple of (a few exact figures may sit on a rounded statement)
    const share = (arr, k) => arr.filter(v => v % k === 0).length / arr.length;
    const pick = arr => !arr.length ? 1 : share(arr, 1e6) >= 0.9 ? 1e6 : share(arr, 1e3) >= 0.9 ? 1e3 : 1;
    return { money: pick(money), shares: pick(sh) };
  }
  const SCALE_WORD = { 1e6: "millions", 1e3: "thousands", 1: "units" };
  function fmtFiled(v, uom, sc) {
    if (v == null) return "";
    let x = v, d = 0;
    if (isPerShare(uom)) d = 2;
    else if (isShares(uom)) x = v / sc.shares;
    else if (uom === "USD") x = v / sc.money;
    else d = Math.abs(v) < 100 && v % 1 ? 2 : 0;
    if (!isPerShare(uom) && x % 1) d = Math.min(3, String(+x.toFixed(3)).split(".")[1]?.length || 0);
    const s = Math.abs(x).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
    return x < 0 ? `(${s})` : s;
  }
  function memberText(seg, coreg) {
    const parts = (seg || "").split(";").filter(Boolean).map(p => { const [ax, mem] = p.split("="); return (mem || ax).replace(/Member$/, "").replace(/([a-z])([A-Z])/g, "$1 $2").replace(/([A-Z])([A-Z][a-z])/g, "$1 $2"); });
    if (coreg) parts.push(coreg.replace(/([a-z])([A-Z])/g, "$1 $2"));
    return parts.join(" · ");
  }
  const unitOf = uom => isPerShare(uom) ? "USD/shares" : isShares(uom) ? "shares" : "USD";

  /* ---------- source cells ---------- */
  function fsCell(tk, filing, label, tag, custom, fact, sibs) {
    const [_, ddate, qtrs, uom, seg, coreg, value] = fact;
    if (!D.filings[filing.accession]) D.filings[filing.accession] = { index: filing.url, form: filing.form, filed: filing.filed };
    const start = startOf(ddate, qtrs);
    const cell = { tk, stmt: "asreported", label, concept: tag, conceptFull: custom ? `${tag} (company extension)` : `us-gaap:${tag}`, unit: unitOf(uom), kind: qtrs === 4 || (qtrs === 0 && /10-K/.test(filing.form)) ? "A" : "Q", end: ddate, start, v: value,
      dimText: seg || coreg ? memberText(seg, coreg) : null, rec: { v: value, accn: filing.accession, form: filing.form, filed: filing.filed, start, end: ddate } };
    cell.sib = () => sibs || [];
    return cell;
  }
  function clickCell(td, cell) {
    td.classList.add("c"); td.tabIndex = 0;
    td.onclick = () => { document.querySelectorAll("td.c.sel").forEach(x => x.classList.remove("sel")); td.classList.add("sel"); openSource(cell); };
    td.onkeydown = e => { if (e.key === "Enter") td.click(); };
  }

  /* ---------- tab ---------- */
  async function render(body, tk) {
    const host = el("div", { style: "display:flex;flex-direction:column;gap:16px" });
    const top = el("div", { class: "toolbar", style: "display:flex;gap:10px;flex-wrap:wrap;align-items:center" },
      seg([["reported", "As reported"], ["standard", "Standardized"], ["adjusted", "Adjusted"]], ST.view, v => { ST.view = v; store.set("stView", v); draw(); }));
    body.append(top, host);
    let idx;
    try { idx = await loadIndex(tk); } catch { idx = null; }
    function draw() {
      host.innerHTML = "";
      if (ST.view !== "adjusted" && !idx?.filings?.length) { host.append(el("div", { class: "empty" }, el("p", { class: "muted" }, "As-filed statements for this company are not loaded yet."))); return; }
      if (ST.view === "reported") reported(host, tk, idx);
      else if (ST.view === "standard") standardized(host, tk, idx);
      else adjusted(host, tk, idx);
    }
    draw();
  }

  /* ---------- as reported ---------- */
  function filingName(f) { return `${f.form} · ${f.fp && f.fy ? (f.fp === "FY" ? "FY" : f.fp + " ") + f.fy : fdate(f.period)} · filed ${fdate(f.filed)}`; }
  async function reported(host, tk, idx) {
    const filings = idx.filings;
    const sel = el("select", { class: "btn", style: "max-width:100%", "aria-label": "Filing" },
      el("option", { value: "" }, `Latest: ${filingName(filings[0])}`), filings.map(f => el("option", { value: f.a }, filingName(f) + (f.prevrpt ? " (amended later)" : ""))));
    if (ST.filing && filings.find(f => f.a === ST.filing)) sel.value = ST.filing;
    const repHost = el("span");
    const wrap = el("div", { class: "fin-wrap" });
    const head = el("div", { class: "legend", style: "padding:8px 16px;border-bottom:1px solid var(--line)" });
    const foot = el("div", { class: "src", style: "padding-top:10px" });
    host.append(el("section", { class: "panel" }, el("div", { class: "hd" }, el("h3", {}, "As reported"), el("div", { class: "r" }, sel)), el("div", { style: "padding:10px 16px;border-bottom:1px solid var(--line)" }, repHost), head, wrap, foot));
    sel.onchange = () => { ST.filing = sel.value || null; ST.report = null; load(); };
    async function load() {
      const meta = filings.find(f => f.a === (ST.filing || filings[0].a));
      wrap.innerHTML = ""; wrap.append(el("div", { class: "skel", style: "height:240px;margin:16px" }));
      let doc;
      try { doc = await loadFiling(tk, meta.a); } catch (e) { wrap.innerHTML = ""; wrap.append(el("div", { class: "note", style: "margin:16px" }, `Could not load this filing (${e.message}).`)); return; }
      const reps = doc.reports.slice().sort((a, b) => (STMT_ORDER[a.stmt] - STMT_ORDER[b.stmt]) || (a.inpth - b.inpth) || (a.report - b.report));
      if (!reps.find(r => r.report === ST.report)) ST.report = (reps.find(r => r.stmt === "IS" && !r.inpth) || reps[0]).report;
      repHost.innerHTML = "";
      repHost.append(seg(reps.map(r => [String(r.report), STMT_LABEL[r.stmt] + (r.inpth ? " (parenthetical)" : "")]), String(ST.report), v => { ST.report = +v; table(); }));
      table();
      function table() {
        const r = doc.reports.find(x => x.report === ST.report);
        const sc = scaleFor(r.facts);
        head.innerHTML = "";
        head.append(el("span", {}, el("span", { class: "form " + formCls(doc.form) }, doc.form), " ", `${doc.registrant} · accession `, el("span", { class: "mono" }, doc.accession)),
          el("span", { class: "faint" }, `USD in ${SCALE_WORD[sc.money]}${sc.shares !== 1 ? `, shares in ${SCALE_WORD[sc.shares]}` : ""}, except per-share amounts · labels, order and signs as filed · click a value for the filing`),
          el("a", { href: doc.url, target: "_blank", rel: "noopener", style: "margin-left:auto" }, "Filing index on EDGAR ↗"));
        wrap.innerHTML = "";
        wrap.append(r.stmt === "EQ" ? equityTable(tk, doc, r, sc) : stmtTable(tk, doc, r, sc));
        foot.textContent = `Source: SEC Financial Statement Data Sets (${doc.source?.split("/").pop() || ""}), pre.txt for lines and labels, num.txt for values. Statement ${r.report} of the filing as rendered by EDGAR. Periods are SEC's rounding to month end. Blank rows mark headings in the filed statement.`;
        requestAnimationFrame(() => { wrap.scrollLeft = 0; });
      }
    }
    load();
  }
  function isTotal(ln) { return TOTAL_TAGS.has(ln[1]) || /^(total|net (cash|income|earnings|increase|decrease|change|loss|revenue)|gross (profit|margin)|operating income|income before|comprehensive income)/i.test(ln[3]); }
  function stmtTable(tk, doc, r, sc) {
    const plain = r.facts.filter(f => !f[4] && !f[5]);
    const colKeys = [...new Set((plain.length ? plain : r.facts).map(f => f[1] + "|" + f[2]))].map(k => k.split("|")).map(([d, q]) => [d, +q]);
    colKeys.sort((a, b) => b[0].localeCompare(a[0]) || a[1] - b[1]);
    const byTag = {};
    r.facts.forEach(f => { (byTag[f[0]] = byTag[f[0]] || []).push(f); });
    const t = el("table", { class: "fin" });
    t.append(el("thead", {}, el("tr", {}, el("th", { class: "lab" }, STMT_LABEL[r.stmt] + (r.inpth ? " (parenthetical)" : "")), colKeys.map(([d, q], i) => { const [a, b] = colHead(d, q); return el("th", { "data-i": i }, a, el("span", { class: "pd" }, b)); }))));
    const tb = el("tbody");
    let prev = null;
    r.lines.forEach(ln => {
      const [line, tag, custom, plabel, negating] = ln;
      if (prev != null && line > prev + 1) tb.append(el("tr", { class: "grp" }, el("td", { class: "lab", colspan: colKeys.length + 1, style: "height:6px;padding:0" })));
      prev = line;
      const facts = byTag[tag] || [];
      const groups = {};
      facts.forEach(f => { const k = (f[4] || "") + "|" + (f[5] || ""); (groups[k] = groups[k] || []).push(f); });
      const keys = Object.keys(groups).sort((a, b) => (a === "|" ? -1 : b === "|" ? 1 : a.localeCompare(b)));
      if (!keys.length) keys.push("|");
      keys.forEach(k => {
        const fs = groups[k] || [];
        const sub = k !== "|";
        const name = sub ? memberText(...k.split("|")) : plabel;
        const tr = el("tr", { class: (!sub && isTotal(ln) ? "tot" : "") + (sub ? " sub" : "") },
          el("td", { class: "lab", title: `${plabel}\n${custom ? "Company extension tag" : "us-gaap"}: ${tag}${negating ? "\nNegated label: the filing shows this value with its sign reversed" : ""}${sub ? "\n" + k.replace("|", " ") : ""}` }, el("div", { class: "row" }, el("span", { class: "nm" }, name))));
        const sibs = [];
        colKeys.forEach(([d, q], i) => {
          const f = fs.find(x => x[1] === d && x[2] === q);
          if (!f || f[6] == null) { tr.append(el("td", { "data-i": i }, "")); return; }
          const shown = negating ? -f[6] : f[6];
          const td = el("td", { "data-i": i, class: shown < 0 ? "neg" : "", title: `${f[6].toLocaleString("en-US")} ${f[3]} · ${tag}${f[7] ? "\nFootnote: " + f[7] : ""}` }, fmtFiled(shown, f[3], sc));
          const cell = fsCell(tk, doc, `${plabel}${sub ? " · " + name : ""}`, tag, custom, f, sibs);
          sibs.push(cell);
          clickCell(td, cell);
          tr.append(td);
        });
        sibs.sort((a, b) => a.end.localeCompare(b.end));
        tb.append(tr);
      });
    });
    t.append(tb);
    return t;
  }
  // Statement of equity: columns are the equity components (EquityComponentsAxis members), rows repeat per period.
  function equityTable(tk, doc, r, sc) {
    const comps = [...new Set(r.facts.map(f => f[4] || ""))].sort((a, b) => (a === "" ? 1 : b === "" ? -1 : 0));
    const durs = [...new Set(r.facts.filter(f => f[2] > 0).map(f => f[1] + "|" + f[2]))].map(k => k.split("|")).map(([d, q]) => [d, +q]).sort((a, b) => a[0].localeCompare(b[0]) || a[1] - b[1]);
    const byTag = {};
    r.facts.forEach(f => { (byTag[f[0]] = byTag[f[0]] || []).push(f); });
    const firstDur = r.lines.findIndex(ln => (byTag[ln[1]] || []).some(f => f[2] > 0));
    const t = el("table", { class: "fin" });
    t.append(el("thead", {}, el("tr", {}, el("th", { class: "lab" }, STMT_LABEL.EQ), comps.map((c, i) => el("th", { "data-i": i }, c ? memberText(c) : "Total")))));
    const tb = el("tbody");
    (durs.length ? durs : [[null, 0]]).slice().reverse().forEach(([d, q]) => {
      if (d) tb.append(el("tr", { class: "grp" }, el("td", { class: "lab" }, `${q * 3} months ended ${fdate(d)}`), comps.map(() => el("td", {}, ""))));
      const begin = d ? shiftMonths(d, -3 * q) : null;
      r.lines.forEach((ln, li) => {
        const [line, tag, custom, plabel, negating] = ln;
        const fs = byTag[tag] || [];
        const instant = fs.length && fs.every(f => f[2] === 0);
        const want = f => !d ? true : instant ? f[2] === 0 && f[1] === (li < firstDur ? begin : d) : f[1] === d && f[2] === q;
        const row = comps.map(c => fs.find(f => (f[4] || "") === c && !f[5] && want(f)));
        if (!row.some(Boolean)) return;
        const tr = el("tr", { class: instant || isTotal(ln) ? "tot" : "" }, el("td", { class: "lab", title: `${plabel}\n${custom ? "Company extension tag" : "us-gaap"}: ${tag}` }, el("div", { class: "row" }, el("span", { class: "nm" }, plabel))));
        row.forEach((f, i) => {
          if (!f || f[6] == null) { tr.append(el("td", { "data-i": i }, "")); return; }
          const shown = negating ? -f[6] : f[6];
          const td = el("td", { "data-i": i, class: shown < 0 ? "neg" : "", title: `${f[6].toLocaleString("en-US")} ${f[3]} · ${tag}${f[4] ? " · " + f[4] : ""}` }, fmtFiled(shown, f[3], sc));
          clickCell(td, fsCell(tk, doc, `${plabel}${f[4] ? " · " + memberText(f[4]) : ""}`, tag, custom, f, null));
          tr.append(td);
        });
        tb.append(tr);
      });
    });
    t.append(tb);
    return t;
  }

  /* ---------- standardized ---------- */
  // For each template line and period: every filing that presented it, newest filing last.
  function stdSeries(std) {
    const out = {};
    const fl = Object.entries(std.filings).sort((a, b) => a[1].filed.localeCompare(b[1].filed) || a[0].localeCompare(b[0]));
    fl.forEach(([a, f]) => Object.entries(f.lines).forEach(([lid, pts]) => pts.forEach(([d, q, v, tag, rep, line, plabel]) => {
      const k = d + "|" + q;
      ((out[lid] = out[lid] || {})[k] = out[lid][k] || []).push({ a, f, d, q, v, tag, rep, line, plabel });
    })));
    return out;
  }
  function stdCell(tk, lineLabel, p, kind) {
    const filing = { accession: p.a, form: p.f.form, filed: p.f.filed, url: `https://www.sec.gov/Archives/edgar/data/${STC[tk].cik || ""}/${p.a.replace(/-/g, "")}/${p.a}-index.htm` };
    if (!D.filings[p.a]) D.filings[p.a] = { index: filing.url, form: p.f.form, filed: p.f.filed };
    const start = startOf(p.d, p.q);
    return { tk, stmt: "standardized", label: lineLabel, concept: p.tag, conceptFull: `${p.tag} (filed as "${p.plabel}")`, unit: /eps|dps/.test(p.lid || "") ? "USD/shares" : /shares/.test(p.lid || "") ? "shares" : "USD", kind, end: p.d, start, v: p.v,
      rec: { v: p.sign === -1 ? -p.v : p.v, accn: p.a, form: p.f.form, filed: p.f.filed, start, end: p.d } };
  }
  async function standardized(host, tk, idx) {
    const box = el("section", { class: "panel" });
    host.append(box);
    box.append(el("div", { class: "skel", style: "height:240px;margin:16px" }));
    let std, map;
    try { [std, map] = await loadStd(tk); } catch (e) { box.innerHTML = ""; box.append(el("div", { class: "note", style: "margin:16px" }, `Standardized statements are not available (${e.message}).`)); return; }
    STC[tk].cik = idx.filings[0].cik;
    const latest = idx.filings[0];
    const tpl = latest.template;
    const lines = map.templates[tpl];
    const series = stdSeries(std);
    const wrap = el("div", { class: "fin-wrap" });
    const mapHost = el("div");
    box.innerHTML = "";
    box.append(el("div", { class: "hd" }, el("h3", {}, `Standardized · ${TEMPLATE_NAME[tpl]} template`),
      el("div", { class: "r" }, seg([["A", "Annual"], ["Q", "Quarterly"]], ST.kind, v => { ST.kind = v; draw(); }), seg([["latest", "Latest filed"], ["first", "As first reported"]], ST.basis, v => { ST.basis = v; draw(); }))),
      el("div", { class: "legend", style: "padding:8px 16px;border-bottom:1px solid var(--line)" }, el("span", {}, el("i", { style: "background:var(--c3)" }), "Derived quarter"), el("span", {}, el("i", { style: "background:var(--c4)" }), "Calculated"), el("span", {}, el("i", { style: "background:var(--c5)" }), "Restated in a later filing"), el("span", { class: "faint" }, `USD millions except per share · template from SIC ${latest.sic || "–"} · hover a value for the filed label, click for the filing`)),
      wrap, el("div", { class: "src", style: "padding-top:10px" }, "Each value comes from one filing in the SEC Financial Statement Data Sets. \"Latest filed\" shows the most recent filing that presented the period (restatements included); \"As first reported\" shows the original filing. Cash flow quarters not presented on their own are derived from year-to-date figures."));
    host.append(mapHost);
    function periodsFor(kind) {
      const s = new Set();
      const dur = kind === "A" ? 4 : 1;
      Object.entries(series).forEach(([lid, byK]) => Object.keys(byK).forEach(k => { const [d, q] = k.split("|"); if (+q === dur) s.add(d); }));
      if (kind === "Q") Object.values(series).forEach(byK => Object.keys(byK).forEach(k => { const [d, q] = k.split("|"); if (+q > 1) s.add(d); }));
      return [...s].sort();
    }
    function pick(list) { return ST.basis === "first" ? list[0] : list[list.length - 1]; }
    function value(line, d, kind) {
      const byK = series[line.id] || {};
      const q = line.stmt === "BS" ? 0 : kind === "A" ? 4 : 1;
      let list = byK[d + "|" + q];
      if (list?.length) { const p = pick(list); return { p: { ...p, lid: line.id, sign: line.sign }, restated: list.some(x => x.v !== list[0].v) }; }
      if (kind === "Q" && line.stmt !== "BS" && !line.calc) {
        for (let k = 2; k <= 4; k++) {
          const cur = byK[d + "|" + k], prior = byK[shiftMonths(d, -3) + "|" + (k - 1)];
          if (cur?.length && prior?.length) {
            const a = pick(cur), b = pick(prior);
            if (!/eps|dps|shares/.test(line.id)) return { p: { ...a, lid: line.id, sign: line.sign }, derived: { from: a, minus: b } };
          }
        }
      }
      return null;
    }
    function draw() {
      const kind = ST.kind;
      let ends = periodsFor(kind).slice(-(kind === "A" ? 10 : 12));
      const t = el("table", { class: "fin" });
      t.append(el("thead", {}, el("tr", {}, el("th", { class: "lab" }, "Template line"), ends.map((e, i) => el("th", { "data-i": i }, plabel(tk, e, kind), el("span", { class: "pd" }, e))))));
      const tb = el("tbody");
      let lastStmt = null;
      lines.forEach(line => {
        if (line.stmt !== lastStmt) { tb.append(el("tr", { class: "grp" }, el("td", { class: "lab" }, { IS: "Income statement", BS: "Balance sheet", CF: "Cash flow" }[line.stmt]), ends.map(() => el("td", {}, "")))); lastStmt = line.stmt; }
        const cells = ends.map(e => value(line, e, kind));
        if (!cells.some(Boolean)) return;
        const per = /eps|dps/.test(line.id), shares = /shares/.test(line.id);
        const filedLabels = [...new Set(cells.filter(Boolean).map(c => c.p.plabel))];
        const tr = el("tr", { class: /total|operating$|net_income$|liab_equity|equity_parent|gross_profit|operating_income|cf_(operating|investing|financing)$/.test(line.id) ? "tot" : "" },
          el("td", { class: "lab", title: `${line.label}\nTags, in priority order: ${line.tags.length ? line.tags.join(", ") : line.calc}\nFiled as: ${filedLabels.join(" | ")}` }, el("div", { class: "row" }, el("span", { class: "nm" }, line.label))));
        const sib = [];
        cells.forEach((c, i) => {
          if (!c) { tr.append(el("td", { class: "faint", "data-i": i }, "–")); return; }
          const { p } = c;
          const txt = per ? (p.v < 0 ? `(${Math.abs(p.v).toFixed(2)})` : p.v.toFixed(2)) : shares ? fmtM(p.v, "USD") : fmtM(p.v, "USD");
          const td = el("td", { "data-i": i, class: (p.v < 0 ? "neg " : "") + (c.derived ? "der" : p.tag === "calc" ? "calc" : "") + (c.restated ? " rst" : ""),
            title: p.tag === "calc" ? `Calculated: ${p.plabel}` : `Filed as "${p.plabel}" (${p.tag})\n${p.f.form} filed ${p.f.filed}${c.restated ? "\nA different value was reported for this period in another filing" : ""}${c.derived ? "\nDerived: year-to-date minus prior year-to-date" : ""}` }, txt);
          let cell;
          if (p.tag === "calc") {
            const parts = (map.templates[tpl].find(x => x.id === line.id)?.calc || "").split(/\s[+-]\s/);
            const inputs = lines.filter(l => parts.includes(l.label)).map(l => value(l, ends[i], kind)).filter(Boolean).map(x => stdCell(tk, x.p.lid ? lines.find(l => l.id === x.p.lid).label : "", { ...x.p }, kind));
            cell = { tk, stmt: "standardized", label: line.label, unit: "USD", kind, end: ends[i], v: p.v, calc: { formula: p.plabel, inputs } };
          } else {
            cell = stdCell(tk, line.label, p, kind);
            if (c.derived) {
              const f = c.derived.from, m = c.derived.minus;
              cell.derived = { from: { v: f.v * (line.sign || 1), start: startOf(f.d, f.q), end: f.d, accn: f.a }, minus: { v: m.v * (line.sign || 1), start: startOf(m.d, m.q), end: m.d, accn: m.a, form: m.f.form } };
              if (!D.filings[m.a]) D.filings[m.a] = { form: m.f.form, filed: m.f.filed };
              cell.v = (f.v - m.v); cell.start = startOf(ends[i], 1);
              cell.rec = { ...cell.rec, v: f.v * (line.sign || 1), start: startOf(f.d, f.q), end: f.d };
              td.textContent = per ? cell.v.toFixed(2) : fmtM(cell.v, "USD");
            }
          }
          sib.push(cell); cell.sib = () => sib;
          clickCell(td, cell);
          tr.append(td);
        });
        tb.append(tr);
      });
      t.append(tb);
      wrap.innerHTML = ""; wrap.append(t);
      requestAnimationFrame(() => { wrap.scrollLeft = wrap.scrollWidth; });
    }
    draw();
    mappingPanel(mapHost, tk, latest, lines);
  }
  async function mappingPanel(host, tk, latest, lines) {
    let doc;
    try { doc = await loadFiling(tk, latest.a); } catch { return; }
    const t = el("table", { class: "t" }, el("thead", {}, el("tr", {}, el("th", {}, "Filed line"), el("th", {}, "Tag"), el("th", {}, "Template line"))));
    const tb = el("tbody");
    const lab = Object.fromEntries(lines.map(l => [l.id, l.label]));
    const mapped = {};
    Object.entries(doc.std.lines).forEach(([lid, pts]) => { const p = pts[0]; if (p[4] != null) mapped[p[4] + ":" + p[5]] = lid; });
    doc.reports.filter(r => ["IS", "BS", "CF"].includes(r.stmt) && !r.inpth).sort((a, b) => STMT_ORDER[a.stmt] - STMT_ORDER[b.stmt]).forEach(r => {
      tb.append(el("tr", { class: "grp" }, el("td", { colspan: 3, class: "eyebrow", style: "background:var(--sunk)" }, STMT_LABEL[r.stmt])));
      r.lines.forEach(ln => {
        const lid = mapped[r.report + ":" + ln[0]];
        const shown = lid || doc.std.unmapped.some(u => u[0] === r.report && u[1] === ln[0]);
        if (!shown) return;
        tb.append(el("tr", {}, el("td", {}, ln[3]), el("td", { class: "mono faint", style: "font-size:11.5px" }, ln[1] + (ln[2] ? " (extension)" : "")), el("td", { class: lid ? "" : "faint" }, lid ? lab[lid] : "Not mapped")));
      });
    });
    t.append(tb);
    host.append(panel(`Where ${SHORT[tk] || tk}'s lines map · ${latest.form} filed ${fdate(latest.filed)}`, null, el("div", { class: "scroll", style: "max-height:420px;overflow:auto" }, t),
      el("div", { class: "src", style: "padding-top:10px" }, "Template lines take the first matching tag in priority order (hover a template line for the list). Filed lines marked not mapped are shown in the as-reported view and are not added into any template line.")));
  }

  /* ---------- adjusted (company non-GAAP reconciliations from the KPI file) ---------- */
  const FAMILIES = [
    { id: "op", name: "Adjusted operating income", rx: /(adjusted|non-gaap|core|underlying|base business)[^%]*(operating (income|profit|earnings)|income from operations|segment profit|\bebit\b)|(operating (income|profit)|income from operations)[^%]*(adjusted|non-gaap|excluding)/i, gaap: "is_operating_income" },
    { id: "eps", name: "Adjusted EPS", rx: /(adjusted|non-gaap|core|underlying|operating|base business)[^%]*(eps|earnings per (diluted )?share)|(eps|earnings per share)[^%]*(adjusted|non-gaap|excluding)/i, gaap: "is_eps_diluted" },
    { id: "ebitda", name: "Adjusted EBITDA", rx: /ebitda/i, gaap: "is_net_income" },
    { id: "fcf", name: "Free cash flow", rx: /free cash flow/i, gaap: "cf_operating" },
  ];
  function kpiItems(kpi) { const out = []; (kpi?.groups || []).forEach(g => g.items.forEach(it => out.push({ ...it, category: g.category, subcategory: it.subcategory || g.subcategory }))); return out; }
  function nvals(it, K) { return Object.keys(it.values?.[K] || {}).length; }
  function pickMeasures(items, fam, K) {
    const cand = items.filter(it => fam.rx.test(it.name) && !/%|pct|percent/i.test(it.unit || "") && !/margin|growth|ratio|per unit|guidance|outlook|segment/i.test(it.name) && !/segment/i.test(it.category || "") && nvals(it, K) > 0);
    const top = cand.filter(it => !it.parent || !cand.some(c => c.id === it.parent));
    top.sort((a, b) => (b.is_total - a.is_total) || (items.filter(x => x.parent === b.id).length - items.filter(x => x.parent === a.id).length) || (nvals(b, K) - nvals(a, K)));
    return top.slice(0, 2);
  }
  async function adjusted(host, tk, idx) {
    const box = el("section", { class: "panel" });
    host.append(box);
    box.append(el("div", { class: "skel", style: "height:200px;margin:16px" }));
    const [{ kpi, ir }, stdPair] = await Promise.all([ensureSegments(tk), idx?.filings?.length ? loadStd(tk).catch(() => null) : null]);
    if (idx?.filings?.length) STC[tk].cik = idx.filings[0].cik;
    const items = kpiItems(kpi);
    box.innerHTML = "";
    const K = ST.kind;
    box.append(el("div", { class: "hd" }, el("h3", {}, "Adjusted (company non-GAAP) next to GAAP"), el("div", { class: "r" }, seg([["Q", "Quarterly"], ["A", "Annual"]], K, v => { ST.kind = v; host.innerHTML = ""; adjusted(host, tk, idx); }))));
    if (!items.length) { box.append(el("div", { class: "note", style: "margin:16px" }, `${SHORT[tk] || tk} has no published KPI file yet, so there are no company reconciliations to show.`)); return; }
    const series = stdPair ? stdSeries(stdPair[0]) : {};
    const tplLines = stdPair ? stdPair[1].templates[idx.filings[0].template] : [];
    let any = false;
    FAMILIES.forEach(fam => {
      const ms = pickMeasures(items, fam, K);
      ms.forEach(m => {
        any = true;
        const kids = items.filter(x => x.parent === m.id && nvals(x, K) > 0);
        const rows = [...kids, m];
        const ends = [...new Set(rows.flatMap(it => Object.keys(it.values?.[K] || {})))].sort().slice(-(K === "A" ? 6 : 8));
        const t = el("table", { class: "fin" });
        t.append(el("thead", {}, el("tr", {}, el("th", { class: "lab" }, m.name), ends.map((e, i) => el("th", { "data-i": i }, kpiPeriodLabel(tk, e, K), el("span", { class: "pd" }, e))))));
        const tb = el("tbody");
        const gl = tplLines.find(l => l.id === fam.gaap);
        let gaapRow = null;
        const gaapCells = [];
        if (gl && !kids.some(k => /\bgaap\b/i.test(k.name) && !/non-gaap/i.test(k.name))) {
          const tr = el("tr", {}, el("td", { class: "lab", title: `GAAP ${gl.label} from the filed statements (standardized line ${gl.id})` }, el("div", { class: "row" }, el("span", { class: "nm" }, `GAAP: ${gl.label} (filed statements)`))));
          ends.forEach((e, i) => {
            const list = (series[fam.gaap] || {})[nearKey(series[fam.gaap] || {}, e, gl.stmt === "BS" ? 0 : K === "A" ? 4 : 1)];
            if (!list?.length) { tr.append(el("td", { class: "faint", "data-i": i }, "–")); return; }
            const p = list[list.length - 1];
            const td = el("td", { "data-i": i, class: p.v < 0 ? "neg" : "", title: `Filed as "${p.plabel}" (${p.tag}) · ${p.f.form} filed ${p.f.filed}` }, fam.id === "eps" ? p.v.toFixed(2) : fmtM(p.v, "USD"));
            const gc = stdCell(tk, gl.label, { ...p, lid: gl.id, sign: gl.sign }, K);
            gaapCells[i] = gc;
            clickCell(td, gc);
            tr.append(td);
          });
          tb.append(tr);
          gaapRow = { tr, ends };
        }
        rows.forEach(it => {
          const vals = it.values?.[K] || {};
          const isM = it === m;
          const tr = el("tr", { class: isM ? "tot" : "sub" }, el("td", { class: "lab", title: `${it.name}${it.description ? "\n" + it.description : ""}` }, el("div", { class: "row" }, el("span", { class: "nm" }, it.name))));
          ends.forEach((e, i) => {
            const v = vals[e];
            if (!v) { tr.append(el("td", { class: "faint", "data-i": i }, "–")); return; }
            const td = el("td", { "data-i": i, class: "c" + (v.v < 0 ? " neg" : ""), tabindex: "0", title: `${v.doc_title || "Source document"}${v.quote ? "\n" + v.quote : ""}` }, (QMARK[v.q] || "") + fmtKpi(v.v, it.unit));
            td.onclick = () => openKpiSource(tk, it, K, e, v);
            td.onkeydown = ev => { if (ev.key === "Enter") td.click(); };
            tr.append(td);
          });
          tb.append(tr);
        });
        if (gaapRow && fam.id !== "ebitda") {
          const tr = el("tr", { class: "sub" }, el("td", { class: "lab", title: "Adjusted value minus the GAAP value from the filed statements" }, el("div", { class: "row" }, el("span", { class: "nm" }, "Difference: adjusted minus GAAP"))));
          ends.forEach((e, i) => {
            const v = m.values?.[K]?.[e], g = gaapCells[i];
            if (!v || !g) { tr.append(el("td", { class: "faint", "data-i": i }, "–")); return; }
            const d = v.v - g.v;
            const td = el("td", { "data-i": i, class: d < 0 ? "neg" : "" }, fam.id === "eps" ? d.toFixed(2) : fmtM(d, "USD"));
            clickCell(td, { tk, stmt: "adjusted", label: `${m.name} minus GAAP`, unit: fam.id === "eps" ? "USD/shares" : "USD", kind: K, end: e, v: d, calc: { formula: `${m.name} (${fmtKpi(v.v, m.unit)}, company document) - GAAP ${gl.label}`, inputs: [g] } });
            tr.append(td);
          });
          tb.append(tr);
        }
        t.append(tb);
        box.append(el("div", { style: "padding:12px 16px 4px", class: "eyebrow" }, `${fam.name} · ${m.category || ""}`), el("div", { class: "fin-wrap", style: "max-height:none" }, t));
      });
    });
    if (!any) box.append(el("div", { class: "note", style: "margin:16px" }, `The KPI file for ${SHORT[tk] || tk} has no adjusted operating income, adjusted EPS, adjusted EBITDA or free cash flow series for ${K === "A" ? "annual" : "quarterly"} periods.`));
    box.append(el("div", { class: "src", style: "padding-top:10px" }, "Adjusted lines are the company's own reconciliation rows from its earnings releases and presentations (KPI file); click one to open the document at the printed figure. The GAAP line comes from the filed statements in the SEC data sets."));
  }
  function nearKey(byK, e, q) {
    if (byK[e + "|" + q]) return e + "|" + q;
    for (const k of Object.keys(byK)) { const [d, qq] = k.split("|"); if (+qq === q && Math.abs(dayDiff(d, e)) <= 12) return k; }
    return null;
  }

  window.FU_COTABS = window.FU_COTABS || [];
  window.FU_COTABS.push({ id: "statements", name: "Statements", after: "financials", render });
})();
