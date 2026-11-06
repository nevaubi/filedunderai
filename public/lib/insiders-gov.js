// Company tab "Insiders & influence": Form 4 insiders, large holders (13D/13G, 13F), congressional trades, lobbying and
// PACs, hearings and verbatim quotes, regulation and legislation, government contracts, off-exchange and short activity.
// Data: insiders/{T}.json (etl/insiders.py), gov/{T}.json and flows/{T}.json (etl/gov_pipeline.py), altdata/{T}.json for
// FINRA Reg SHO daily short volume. Every value opens its record in the right-hand drawer with the cited text highlighted.
// Registers as the "insiders" company tab (replaces the built-in Form 4 view once app.js supports overrides).
(function () {
  "use strict";
  const CACHE = {};
  const load = (path) => CACHE[path] || (CACHE[path] = getJSON(path).catch(e => { delete CACHE[path]; throw e; }));
  const opt = path => load(path).catch(() => null);

  /* ---------- formatting ---------- */
  const money = v => {
    if (v == null || !isFinite(v)) return "–";
    const a = Math.abs(v), s = v < 0 ? "−" : "";
    if (a >= 1e12) return `${s}$${(a / 1e12).toFixed(2)}T`;
    if (a >= 1e9) return `${s}$${(a / 1e9).toFixed(a >= 1e11 ? 0 : a >= 1e10 ? 1 : 2)}B`;
    if (a >= 1e6) return `${s}$${(a / 1e6).toFixed(a >= 1e8 ? 0 : 1)}M`;
    if (a >= 1e3) return `${s}$${(a / 1e3).toFixed(a >= 1e5 ? 0 : 1)}K`;
    return `${s}$${Math.round(a)}`;
  };
  const qty = v => {
    if (v == null || !isFinite(v)) return "–";
    const a = Math.abs(v), s = v < 0 ? "−" : "";
    return s + (a >= 1e9 ? (a / 1e9).toFixed(2) + "B" : a >= 1e6 ? (a / 1e6).toFixed(a >= 1e8 ? 0 : 1) + "M" : a >= 1e4 ? (a / 1e3).toFixed(0) + "K" : Math.round(a).toLocaleString("en-US"));
  };
  const pct = (v, d = 1) => v == null || !isFinite(v) ? "–" : (v * 100).toFixed(d) + "%";
  const sgnq = v => v == null || !isFinite(v) ? "–" : (v > 0 ? "+" : v < 0 ? "−" : "") + qty(Math.abs(v));
  const dt = s => s ? fdate(String(s).slice(0, 10)) : "–";
  const short = (s, n = 90) => !s ? "" : s.length > n ? s.slice(0, n - 1) + "…" : s;
  const nameCase = s => !s ? "" : /[a-z]/.test(s) ? s : s.toLowerCase().replace(/(^|[\s'\-.(&])([a-z])/g, (m, p, c) => p + c.toUpperCase()).replace(/\b(Llc|Lp|Ltd|Inc|Ii|Iii|Iv|Usa|Us|Na|Plc|Etf|Ag|Sa|Nv)\b/g, w => w.toUpperCase());
  const party = p => !p ? "" : /^D|Democrat/.test(p) ? "D" : /^R|Republican/.test(p) ? "R" : p[0];
  const ext = (href, text, cls = "") => href ? el("a", { href, target: "_blank", rel: "noopener", class: cls, onclick: e => e.stopPropagation() }, text) : null;
  const stat = (l, v, d, cls = "", onclick) => el("div", {}, el("div", { class: "l" }, l),
    el(onclick ? "button" : "div", { class: "v num " + cls + (onclick ? " src" : ""), onclick, title: onclick ? "Open source" : null }, v), el("div", { class: "d" }, d || " "));
  const srcNote = (...parts) => el("div", { class: "src" }, ...parts);
  const edgar = (cik, acc) => `https://www.sec.gov/Archives/edgar/data/${+cik}/${String(acc || "").replace(/-/g, "")}/`;

  /* ---------- drawer: one generic record view rendered into the shared drawer ---------- */
  function markText(text, marks) {
    let html = esc(text || "");
    const ms = [...new Set((marks || []).filter(Boolean).map(String))].sort((a, b) => b.length - a.length);
    if (!ms.length) return html;
    const rx = new RegExp(ms.map(m => esc(m).replace(/[.*+?^${}()|[\]\\]/g, "\\$&").replace(/\s+/g, "\\s+")).join("|"), "g");
    return html.replace(rx, m => `<mark class="fu-hit">${m}</mark>`);
  }
  function record(r) {
    hideTip();
    DR.cell = { gov: r.id || r.title };
    const wasOpen = drawer.classList.contains("open");
    if (!wasOpen) { const a = document.activeElement; DR.trigger = a && a !== document.body && !drawer.contains(a) ? a : null; DR.trigger?.classList.add("src-active"); }
    const dh = $("#dh"), db = $("#db");
    dh.innerHTML = ""; db.innerHTML = "";
    const links = (r.links || []).filter(l => l && l[1]);
    dh.append(
      el("div", { class: "row" }, el("span", { class: "eyebrow" }, r.eyebrow || "Source record"),
        el("div", { class: "x", style: "display:flex;gap:6px" },
          r.cite ? el("button", { class: "btn", title: "Copy citation", onclick: () => copyText(r.cite, "Citation copied") }, svgUse("i-copy"), "Cite") : null,
          ...links.slice(0, 2).map(([label, href]) => el("a", { class: "btn", href, target: "_blank", rel: "noopener" }, label, svgUse("i-ext"))),
          el("button", { class: "iconbtn", onclick: closeSource, "aria-label": "Close source (Esc)" }, svgUse("i-x")))),
      el("div", { class: "row" }, el("h2", {}, r.title || "Record")),
      el("div", { class: "row" }, r.value != null ? el("span", { class: "chip acc mono", style: "font-size:13.5px;height:26px" }, r.value) : null,
        r.sub ? el("span", { class: "muted", style: "font-size:12.5px" }, r.sub) : null));
    if (r.ai) db.append(el("div", { class: "note ig-ainote" }, el("span", { class: "chip chip-ai" }, svgUse("i-spark"), "AI-generated"), " ", r.ai));
    if (r.warn) db.append(el("div", { class: "note" }, r.warn));
    const body = el("div", { class: "ig-dbody" });
    db.append(body);
    if (r.cited) {
      const box = el("div", { class: "ig-cited" }, el("div", { class: "ig-cited-hd" }, el("span", { class: "badge-src" }, r.citedLabel || "Cited text"), r.citedSrc ? el("span", { class: "faint" }, r.citedSrc) : null));
      const pre = el("div", { class: "ig-cited-tx", html: markText(r.cited, r.marks) });
      box.append(pre);
      body.append(box);
    }
    if (r.fields?.length) {
      const dl = el("dl", { class: "kv" });
      r.fields.filter(f => f && f[1] != null && f[1] !== "").forEach(([k, v]) => dl.append(el("dt", {}, k), el("dd", {}, v)));
      body.append(dl);
    }
    if (r.table) body.append(r.table);
    if (r.extra) body.append(r.extra);
    const p = r.src || {};
    body.append(el("div", { class: "ig-prov" }, el("div", { class: "eyebrow" }, "Provenance"),
      el("dl", { class: "kv" },
        el("dt", {}, "Source"), el("dd", {}, p.url ? ext(p.url, short(p.url, 70) + " ↗") : (r.srcName || "–")),
        p.page ? el("dt", {}, "Dataset page") : null, p.page ? el("dd", {}, ext(p.page, short(p.page, 70) + " ↗")) : null,
        el("dt", {}, "Method"), el("dd", {}, p.method || r.method || "–"),
        el("dt", {}, "Retrieved"), el("dd", { class: "mono" }, p.retrieved_at ? p.retrieved_at.replace("T", " ").replace("Z", " UTC") : "–"),
        ...links.slice(2).flatMap(([label, href]) => [el("dt", {}, "Link"), el("dd", {}, ext(href, label + " ↗"))]))));
    if (r.note) body.append(el("div", { class: "note" }, r.note));
    drawer.classList.add("open"); drawer.setAttribute("aria-hidden", "false");
    if (innerWidth < 860) scrim.hidden = false;
    db.scrollTop = 0;
    const hit = db.querySelector("mark.fu-hit");
    if (hit) requestAnimationFrame(() => { const box = hit.closest(".ig-cited-tx"); if (box) box.scrollTop = Math.max(0, hit.offsetTop - box.clientHeight / 3); reveal(hit, "center"); });
    if (!wasOpen) { const h = dh.querySelector("h2"); h?.setAttribute("tabindex", "-1"); h?.focus({ preventScroll: true }); }
  }

  /* ---------- source status strip ---------- */
  const STATUS_TXT = { ok: "available", empty: "no records", unavailable: "unavailable", not_collected: "not collected", error: "error", missing: "not yet collected", insufficient: "not enough records", all_dropped: "no verified sentences" };
  function sourceStrip(srcs, keys) {
    const wrap = el("div", { class: "ig-srcs" });
    keys.forEach(([k, label]) => {
      const s = srcs?.[k] || { status: "missing" };
      const cls = s.status === "ok" ? "ok" : ["empty", "insufficient"].includes(s.status) ? "empty" : "bad";
      wrap.append(el("button", { class: "ig-src " + cls, title: [label, STATUS_TXT[s.status] || s.status, s.note].filter(Boolean).join(" · "),
        onclick: () => record({ eyebrow: "Source status", title: label, value: STATUS_TXT[s.status] || s.status, sub: s.checked_at ? "checked " + dt(s.checked_at) : null,
          fields: [["Status", STATUS_TXT[s.status] || s.status], ["Records", s.count != null ? String(s.count) : null], ["Note", s.note], ["Method", s.method]],
          src: { url: s.url, method: s.method, retrieved_at: s.checked_at } }) },
        el("i"), label, s.status !== "ok" ? el("span", { class: "ig-src-st" }, STATUS_TXT[s.status] || s.status) : null));
    });
    return wrap;
  }
  const emptyRow = (cols, text) => el("tr", {}, el("td", { colspan: cols, class: "muted ig-empty" }, text));
  const missing = (text) => el("div", { class: "ig-missing" }, text);
  function table(heads, rows, cls = "") {
    const t = el("table", { class: "t ig-t " + cls }, el("thead", {}, el("tr", {}, ...heads.map(h => typeof h === "string" ? el("th", {}, h) : el("th", { class: h[1] || "" }, h[0])))));
    const tb = el("tbody");
    rows.forEach(r => tb.append(r));
    t.append(tb);
    return el("div", { class: "ig-scroll" }, t);
  }
  const tr = (onclick, ...tds) => el("tr", { class: onclick ? "click" : "", onclick, tabindex: onclick ? "0" : null, onkeydown: onclick ? e => { if (e.key === "Enter") onclick(); } : null }, ...tds);
  const td = (v, cls = "") => el("td", { class: cls }, v ?? "–");

  /* ---------- charts ---------- */
  function axes(svg, x, y, W, H, m, yfmt, xticks = 6) {
    svg.append("g").attr("transform", `translate(0,${H - m.b})`).call(d3.axisBottom(x).ticks(xticks).tickSizeOuter(0))
      .call(g => { g.selectAll("text").attr("fill", css("--muted")); g.select(".domain").attr("stroke", css("--line")); g.selectAll("line").attr("stroke", css("--line")); });
    svg.append("g").attr("transform", `translate(${m.l},0)`).call(d3.axisLeft(y).ticks(5).tickFormat(yfmt).tickSize(-(W - m.l - m.r)))
      .call(g => { g.select(".domain").remove(); g.selectAll("line").attr("stroke", css("--line")).attr("stroke-opacity", .6); g.selectAll("text").attr("fill", css("--muted")); });
  }
  const svgFor = (host, H, label) => d3.select(host).append("svg").attr("width", host.clientWidth || 760).attr("height", H).attr("role", "img").attr("aria-label", label);
  function barsChart(host, data, { keys, colors, yfmt = money, H = 200, label = "chart", onBar, tipf }) {
    host.innerHTML = "";
    if (!data.length || typeof d3 === "undefined") { host.append(el("div", { class: "muted" }, "No data to chart.")); return; }
    const W = host.clientWidth || 760, m = { t: 10, r: 10, b: 24, l: 52 };
    const svg = svgFor(host, H, label);
    const x = d3.scaleBand().domain(data.map(d => d.k)).range([m.l, W - m.r]).padding(.25);
    const max = d3.max(data, d => keys.reduce((s, k) => s + Math.max(0, d[k] || 0), 0)) || 1;
    const y = d3.scaleLinear().domain([0, max]).nice().range([H - m.b, m.t]);
    axes(svg, x, y, W, H, m, yfmt);
    const every = Math.ceil(data.length / Math.max(1, Math.floor((W - m.l) / 46)));
    svg.selectAll(".tick text").filter((d, i) => typeof d === "string" && i % every !== 0).attr("opacity", 0);
    data.forEach(d => {
      let acc = 0;
      keys.forEach((k, i) => {
        const v = Math.max(0, d[k] || 0);
        if (!v) return;
        svg.append("rect").attr("x", x(d.k)).attr("width", x.bandwidth()).attr("y", y(acc + v)).attr("height", y(acc) - y(acc + v)).attr("fill", colors[i]).attr("rx", 1)
          .style("cursor", onBar ? "pointer" : null)
          .on("mousemove", ev => showTip(ev, tipf ? tipf(d, k) : `<b>${esc(d.k)}</b><br>${esc(k)}: ${yfmt(v)}`)).on("mouseleave", () => { tip.hidden = true; })
          .on("click", () => onBar && onBar(d, k));
        acc += v;
      });
    });
  }
  function lineChart(host, series, { yfmt = v => v, H = 220, label = "chart", onPoint, tipf, y0 }) {
    host.innerHTML = "";
    const all = series.flatMap(s => s.pts);
    if (all.length < 2 || typeof d3 === "undefined") { host.append(el("div", { class: "muted" }, "Not enough data to chart.")); return; }
    const W = host.clientWidth || 760, m = { t: 12, r: 14, b: 24, l: 52 };
    const svg = svgFor(host, H, label);
    const x = d3.scaleUtc().domain(d3.extent(all, p => p.t)).range([m.l, W - m.r]);
    const lo = y0 != null ? y0 : d3.min(all, p => p.v), hi = d3.max(all, p => p.v);
    const y = d3.scaleLinear().domain([lo, hi === lo ? lo + 1 : hi]).nice().range([H - m.b, m.t]);
    axes(svg, x, y, W, H, m, yfmt);
    series.forEach(s => {
      svg.append("path").datum(s.pts).attr("fill", "none").attr("stroke", s.color).attr("stroke-width", s.width || 1.6).attr("stroke-dasharray", s.dash || null)
        .attr("d", d3.line().defined(p => p.v != null).x(p => x(p.t)).y(p => y(p.v)));
      svg.append("g").selectAll("circle").data(s.pts.filter(p => p.v != null)).join("circle").attr("cx", p => x(p.t)).attr("cy", p => y(p.v)).attr("r", s.dots ? 3 : 2.2)
        .attr("fill", s.color).attr("opacity", s.dots ? .9 : .55).style("cursor", onPoint ? "pointer" : null)
        .on("mousemove", (ev, p) => showTip(ev, tipf ? tipf(p, s) : `<b>${fdate(p.d)}</b><br>${esc(s.name)}: ${yfmt(p.v)}`)).on("mouseleave", () => { tip.hidden = true; })
        .on("click", (ev, p) => onPoint && onPoint(p, s));
    });
  }
  function priceDots(host, tk, items, { color, label }) {
    host.innerHTML = "";
    const bars = (PX[tk]?.bars || []).filter(b => b.t >= new Date(Date.now() - 740 * 864e5));
    if (bars.length < 5 || typeof d3 === "undefined") { host.append(el("div", { class: "muted" }, "Price history is not loaded.")); return; }
    const W = host.clientWidth || 760, H = 220, m = { t: 12, r: 14, b: 24, l: 48 };
    const svg = svgFor(host, H, label);
    const x = d3.scaleUtc().domain(d3.extent(bars, b => b.t)).range([m.l, W - m.r]);
    const y = d3.scaleLinear().domain(d3.extent(bars, b => b.c)).nice().range([H - m.b, m.t]);
    axes(svg, x, y, W, H, m, v => "$" + fnum(v, 0));
    svg.append("path").datum(bars).attr("fill", "none").attr("stroke", css("--ink") || "#222").attr("stroke-width", 1.3).attr("d", d3.line().x(b => x(b.t)).y(b => y(b.c)));
    const near = t => { const i = d3.bisector(b => b.t).left(bars, t); return bars[Math.min(i, bars.length - 1)]; };
    const pts = items.filter(i => i.t >= bars[0].t);
    svg.append("g").selectAll("circle").data(pts).join("circle").attr("cx", d => x(d.t)).attr("cy", d => y(near(d.t).c)).attr("r", d => Math.max(2.5, (d.r || 4) * Math.min(1, W / 900)))
      .attr("fill", d => color(d)).attr("fill-opacity", .35).attr("stroke", d => color(d)).attr("stroke-width", 1).style("cursor", "pointer")
      .on("mousemove", (ev, d) => showTip(ev, d.tip)).on("mouseleave", () => { tip.hidden = true; }).on("click", (ev, d) => d.open && d.open());
  }

  /* =====================================================================
     1. Insiders (Form 4): transactions, clusters, 10b5-1
     ===================================================================== */
  function form4Record(tk, r, codes) {
    const val = r.shares && r.price ? r.shares * r.price : null;
    const cik = D.companies[tk]?.cik;
    const line = `${r.owner}${r.role ? " (" + r.role + ")" : ""}: ${codes[r.code] || r.code} (code ${r.code}) of ${r.shares != null ? Math.round(r.shares).toLocaleString("en-US") : "–"} shares of ${r.security || "–"} at ${r.price ? "$" + fnum(r.price) : "no price"} on ${r.tx_date}; ${r.owned_after != null ? Math.round(r.owned_after).toLocaleString("en-US") : "–"} owned after (${r.ownership === "I" ? "indirect" : "direct"})${r.plan_10b5 ? "; filing checks the Rule 10b5-1 box" : ""}.`;
    record({ eyebrow: "Form 4 transaction", title: `${insName(r.owner)} · ${codes[r.code] || r.code}`, value: val ? money(val) : (r.shares != null ? qty(r.shares) + " sh" : null),
      sub: `${dt(r.tx_date)} · filed ${dt(r.filed)}`, citedLabel: "Parsed from the Form 4 XML", cited: line, marks: [r.shares != null ? Math.round(r.shares).toLocaleString("en-US") : null, r.price ? "$" + fnum(r.price) : null],
      fields: [["Insider", insName(r.owner)], ["Role", r.role], ["Transaction code", `${r.code} · ${codes[r.code] || ""}`], ["Security", r.security], ["Derivative", r.derivative ? "yes" : "no"],
        ["Acquired/disposed", r.acq_disp === "A" ? "Acquired" : r.acq_disp === "D" ? "Disposed" : r.acq_disp], ["Shares", r.shares != null ? Math.round(r.shares).toLocaleString("en-US") : null],
        ["Price", r.price ? "$" + fnum(r.price) : null], ["Value", val ? money(val) : null], ["Owned after", r.owned_after != null ? Math.round(r.owned_after).toLocaleString("en-US") : null],
        ["Rule 10b5-1 plan", r.plan_10b5 ? "Checked on the filing" : "Not checked"], ["Accession", r.accession]],
      links: [["EDGAR filing", cik ? edgar(cik, r.accession) : null]],
      src: { url: cik ? edgar(cik, r.accession) : null, method: "Form 4 XML parsed by etl/insiders.py (insider_transactions)", retrieved_at: null },
      cite: `${tk} Form 4, ${r.owner}, ${codes[r.code] || r.code} ${r.tx_date}, accession ${r.accession}. SEC EDGAR.` });
  }
  function clusters(rows, code, days = 14, min = 3) {
    const xs = rows.filter(r => r.code === code && !r.derivative && r.tx_date).sort((a, b) => a.tx_date.localeCompare(b.tx_date));
    const out = [];
    let i = 0;
    while (i < xs.length) {
      const start = pd(xs[i].tx_date);
      const win = xs.filter(r => { const t = pd(r.tx_date); return t >= start && (t - start) / 864e5 <= days; });
      const people = new Set(win.map(r => r.owner));
      if (people.size >= min) {
        out.push({ code, from: win[0].tx_date, to: win.at(-1).tx_date, people: [...people], rows: win, value: win.reduce((s, r) => s + (r.shares || 0) * (r.price || 0), 0) });
        i += win.length;
      } else i++;
    }
    return out;
  }
  function renderInsiders(host, tk, ins) {
    if (!ins) { host.append(missing("Form 4 transactions are not available for this company yet.")); return; }
    const codes = ins.codes || {}, rows = ins.rows || [];
    const w = ins.w90 || {}, y = ins.w365 || {};
    const net90 = (w.buy_value || 0) - (w.sell_value || 0), net12 = (y.buy_value || 0) - (y.sell_value || 0);
    const cl = [...clusters(rows, "P"), ...clusters(rows, "S")].sort((a, b) => b.from.localeCompare(a.from));
    const planSales = rows.filter(r => r.code === "S" && !r.derivative);
    host.append(el("div", { class: "stats co-stats ig-stats" },
      stat("Open-market buys, 90d", String(w.buys || 0), w.buys ? `${money(w.buy_value)} · ${w.buyers} insider${w.buyers === 1 ? "" : "s"}` : "none", w.buys ? "up" : ""),
      stat("Open-market sales, 90d", String(w.sells || 0), w.sells ? `${money(w.sell_value)} · ${w.sellers} insider${w.sellers === 1 ? "" : "s"}` : "none", w.sells ? "down" : ""),
      stat("Net open-market, 90d", net90 ? money(net90) : "–", "buys minus sales", net90 > 0 ? "up" : net90 < 0 ? "down" : ""),
      stat("Net open-market, 12m", net12 ? money(net12) : "–", `${y.buys || 0} buys · ${y.sells || 0} sales`, net12 > 0 ? "up" : net12 < 0 ? "down" : ""),
      stat("Sales under 10b5-1, 12m", planSales.length ? pct(planSales.filter(r => r.plan_10b5).length / planSales.length, 0) : "–", `${planSales.filter(r => r.plan_10b5).length} of ${planSales.length} sales`),
      stat("Cluster events, 12m", String(cl.length), "3+ insiders within 14 days")));
    const grid = el("div", { class: "grid g64 ig-grid" });
    const ch = el("div", { class: "bd" });
    grid.append(panel("Price with open-market insider trades", el("span", { class: "faint ig-hint" }, "dot size = value · click a dot for the filing"), ch));
    const clBody = el("div", { class: "ig-list" });
    if (!cl.length) clBody.append(el("div", { class: "muted ig-pad" }, "No cluster of three or more insiders buying or selling within 14 days in the last 12 months."));
    cl.slice(0, 8).forEach(c => clBody.append(el("button", { class: "ig-cl " + (c.code === "P" ? "up" : "down"), onclick: () => record({
      eyebrow: "Insider cluster", title: `${c.people.length} insiders ${c.code === "P" ? "bought" : "sold"} within ${Math.max(1, dayDiff(c.from, c.to))} days`, value: money(c.value), sub: `${dt(c.from)} – ${dt(c.to)}`,
      cited: c.rows.map(r => `${r.tx_date}  ${r.owner}  ${codes[r.code] || r.code}  ${Math.round(r.shares || 0).toLocaleString("en-US")} @ $${fnum(r.price || 0)}${r.plan_10b5 ? "  [10b5-1]" : ""}  ${r.accession}`).join("\n"),
      citedLabel: "Form 4 transactions in the window", marks: c.people, method: "Three or more distinct insiders with open-market trades of the same direction within 14 days (computed from insider_transactions)",
      fields: [["Rule", "≥3 distinct insiders, same direction (code P or S, non-derivative), within 14 calendar days"], ["Transactions", String(c.rows.length)], ["Under 10b5-1", String(c.rows.filter(r => r.plan_10b5).length)]],
      src: { method: "Computed from Form 4 rows on this page" } }) },
      el("span", { class: "ig-cl-d" }, `${fmon(c.from)}`), el("span", {}, `${c.people.length} insiders ${c.code === "P" ? "bought" : "sold"}`), el("span", { class: "num" }, money(c.value)))));
    grid.append(panel("Cluster buys and sells", null, clBody, srcNote("Computed from the Form 4 rows below. Sales flagged 10b5-1 were pre-scheduled under a trading plan.")));
    host.append(grid);
    requestAnimationFrame(() => priceDots(ch, tk, rows.filter(r => ["P", "S"].includes(r.code) && !r.derivative && r.tx_date && r.shares && r.price).map(r => ({
      t: pd(r.tx_date), r: Math.max(3, Math.min(10, Math.sqrt(r.shares * r.price / 5e4))), code: r.code,
      tip: `<b>${esc(insName(r.owner))}</b><br>${r.code === "P" ? "Bought" : "Sold"} ${Math.round(r.shares).toLocaleString("en-US")} @ $${fnum(r.price)}<br>${money(r.shares * r.price)} · ${fdate(r.tx_date)}${r.plan_10b5 ? " · 10b5-1" : ""}`,
      open: () => form4Record(tk, r, codes) })), { color: d => d.code === "P" ? css("--up") : css("--down"), label: `${tk} price with insider trades` }));
    let mode = "market", planOnly = false, who = "";
    const hostT = el("div");
    const count = el("span", { class: "ig-count" });
    const people = [...new Set(rows.map(r => r.owner))].sort();
    const sel = el("select", { class: "btn ig-sel", onchange: e => { who = e.target.value; draw(); }, "aria-label": "Insider" }, el("option", { value: "" }, "All insiders"), ...people.map(p => el("option", { value: p }, insName(p))));
    const planBtn = el("button", { class: "btn ig-toggle", "aria-pressed": "false", onclick: () => { planOnly = !planOnly; planBtn.classList.toggle("on", planOnly); planBtn.setAttribute("aria-pressed", String(planOnly)); draw(); } }, "10b5-1 only");
    const draw = () => {
      const list = rows.filter(r => (mode === "all" ? true : mode === "market" ? ["P", "S"].includes(r.code) && !r.derivative : ["A", "M", "F", "X", "C", "G"].includes(r.code)) && (!planOnly || r.plan_10b5) && (!who || r.owner === who));
      count.textContent = `${list.length} transaction${list.length === 1 ? "" : "s"}`;
      hostT.innerHTML = "";
      hostT.append(table(["Date", "Insider", "Type", ["Shares", "r"], ["Price", "r"], ["Value", "r"], ["Owned after", "r"], ["Filing", "r"]],
        list.slice(0, 250).map(r => {
          const val = r.shares && r.price ? r.shares * r.price : null, dir = r.code === "P" ? "up" : r.code === "S" ? "down" : "";
          return tr(() => form4Record(tk, r, codes), td(dt(r.tx_date), "mono"), el("td", {}, el("div", { class: "ig-who" }, el("b", {}, insName(r.owner)), el("span", {}, r.role || ""))),
            el("td", {}, el("span", { class: "chip " + dir }, codes[r.code] || r.code), r.plan_10b5 && r.code === "S" ? el("span", { class: "chip", title: "Rule 10b5-1 trading plan" }, "10b5-1") : null, r.derivative ? el("span", { class: "chip" }, "Deriv.") : null),
            td(r.shares != null ? Math.round(r.shares).toLocaleString("en-US") : "–", "r num"), td(r.price ? "$" + fnum(r.price) : "–", "r num"), td(val ? money(val) : "–", "r num " + dir),
            td(r.owned_after != null ? Math.round(r.owned_after).toLocaleString("en-US") : "–", "r num muted"),
            el("td", { class: "r" }, ext(edgar(D.companies[tk]?.cik || 0, r.accession), "Form 4 ↗", "ig-link")));
        }).concat(list.length ? [] : [emptyRow(8, "No transactions match these filters in the last 12 months.")]), "ig-wide"));
    };
    draw();
    host.append(panel("Form 4 transactions", el("div", { class: "ig-hd" }, count, sel, planBtn, seg([["market", "Buys & sales"], ["comp", "Grants & exercises"], ["all", "All"]], mode, v => { mode = v; draw(); })),
      hostT, srcNote(`Parsed from ${new Set(rows.map(r => r.accession)).size} Form 4 filings over the last 12 months (SEC EDGAR). Updated ${relTime(ins.generated)}.`)));
  }

  /* =====================================================================
     2. Large holders: 13D/13G and 13F
     ===================================================================== */
  function holderRecord(tk, h) {
    const persons = (h.persons || []).map(p => `${p.name || "–"}: ${p.shares != null ? Math.round(p.shares).toLocaleString("en-US") : "–"} shares, ${p.percent != null ? p.percent + "%" : "–"} of class (${p.type || "–"})`).join("\n");
    record({ eyebrow: h.activist ? "Schedule 13D (activist / control intent)" : "Schedule 13G (passive holder)", title: nameCase(h.filer) || "Reporting person", value: h.percent != null ? h.percent + "%" : null,
      sub: `${h.form} · filed ${dt(h.filed)}${h.event_date ? " · event " + h.event_date : ""}`, citedLabel: h.purpose ? "Item 4, purpose of transaction" : "Cover page, reporting persons",
      cited: (h.purpose ? h.purpose + "\n\n" : "") + persons, marks: [h.percent != null ? String(h.percent) : null, ...(h.persons || []).map(p => p.name)],
      fields: [["Form", h.form], ["Filer type", h.filer_type], ["Shares", h.shares != null ? Math.round(h.shares).toLocaleString("en-US") : null], ["Percent of class", h.percent != null ? h.percent + "%" : null],
        ["Event date", h.event_date], ["Accession", h.accession], ["Status", h.status === "legacy" ? h.note : null]],
      links: [["Filing", h.url], ["Filing index", h.index_url]], src: h.src, cite: `${tk}: ${h.filer} ${h.form} filed ${h.filed}, ${h.percent ?? "–"}% of class, accession ${h.accession}. SEC EDGAR.` });
  }
  function f13Record(tk, f, x) {
    const acc = (x.accessions || [])[0], pacc = (x.prev_accessions || [])[0];
    record({ eyebrow: "Form 13F holding", title: nameCase(x.manager) || "Manager CIK " + x.cik, value: qty(x.shares) + " sh", sub: `${f.period} vs ${f.prior_period}`,
      citedLabel: "13F information table rows (summed per manager)", cited: `${x.manager} (CIK ${x.cik})\n${f.period}: ${Math.round(x.shares).toLocaleString("en-US")} shares, value $${Math.round(x.value).toLocaleString("en-US")}\n${f.prior_period}: ${Math.round(x.prev_shares).toLocaleString("en-US")} shares\nChange: ${x.change >= 0 ? "+" : ""}${Math.round(x.change).toLocaleString("en-US")} shares${x.new ? " (new position)" : ""}${x.exited ? " (exited)" : ""}`,
      marks: [Math.round(x.shares).toLocaleString("en-US")],
      fields: [["CUSIP", (f.cusips || []).join(", ")], ["CUSIP source", f.cusip_source], ["Issuer name in 13F", (f.issuer_names || []).join("; ")], ["Report", acc], ["Prior report", pacc]],
      links: [["13F filing", acc ? edgar(x.cik, acc) : null], ["Prior 13F", pacc ? edgar(x.cik, pacc) : null]], src: f.src,
      note: "Value is as filed by the manager (US dollars). Options and principal-amount rows are excluded; a manager's latest original or restated report for the quarter is used." });
  }
  function renderHolders(host, tk, gov, flows) {
    const h = flows?.holders13dg || {}, f = flows?.inst13f;
    host.append(sourceStrip(flows?.sources, [["holders13dg", "13D/13G"], ["inst13f", "13F holdings"]]));
    const cur = h.current || [];
    const top = cur[0];
    const t = f?.totals || {};
    host.append(el("div", { class: "stats co-stats ig-stats" },
      stat("5%+ holders on file", String(cur.filter(x => (x.percent || 0) >= 5).length), "latest 13D/13G per filer"),
      stat("Largest reported stake", top?.percent != null ? top.percent + "%" : "–", top ? short(nameCase(top.filer), 26) : "none", "", top ? () => holderRecord(tk, top) : null),
      stat("Activist (13D) filers", String(cur.filter(x => x.activist).length), "Schedule 13D since 2025"),
      stat("13F managers", t.managers != null ? String(t.managers) : "–", f ? `${f.period}` : "13F unavailable"),
      stat("13F net share change", f ? sgnq((t.shares || 0) - (t.prior_shares || 0)) : "–", f ? `vs ${f.prior_period}` : " ", (t.shares || 0) >= (t.prior_shares || 0) ? "up" : "down"),
      stat("New / exited positions", f ? `${t.new_positions} / ${t.exits}` : "–", "13F managers")));
    host.append(panel("Schedule 13D and 13G filers", null, cur.length ? table(["Filer", "Type", ["% class", "r"], ["Shares", "r"], "Form", "Filed", ""], cur.map(x => tr(() => holderRecord(tk, x),
      el("td", {}, el("b", {}, short(nameCase(x.filer), 48))), td(x.filer_type), td(x.percent != null ? x.percent + "%" : "–", "r num"), td(qty(x.shares), "r num"),
      el("td", {}, el("span", { class: "chip " + (x.activist ? "warn" : "") }, x.form)), td(dt(x.filed), "mono"), el("td", { class: "r" }, ext(x.url, "Filing ↗", "ig-link"))))) :
      missing("No Schedule 13D or 13G about this company since January 2025 in our filings index."),
      srcNote("Beneficial owners above 5% must file Schedule 13D (with intent to influence) or 13G (passive). Latest report per reporting person; structured XML cover pages since December 2024.")));
    if (!f) { host.append(panel("Institutional holders (Form 13F)", null, missing(flows?.sources?.inst13f?.note ? "13F holdings unavailable: " + flows.sources.inst13f.note : "13F holdings are not available for this company."))); return; }
    const g = el("div", { class: "grid g2 ig-grid2" });
    const mk = (title, list, neg) => panel(title, null, table(["Manager", ["Change", "r"], ["Now", "r"]], list.slice(0, 10).map(x => tr(() => f13Record(tk, f, x), td(short(nameCase(x.manager), 40)),
      td(sgnq(x.change) + (x.new ? " new" : x.exited ? " exit" : ""), "r num " + (neg ? "down" : "up")), td(qty(x.shares), "r num")))));
    g.append(mk("Largest 13F buyers", f.buyers || [], false), mk("Largest 13F sellers", f.sellers || [], true));
    host.append(panel("Top institutional holders (Form 13F)", el("span", { class: "faint ig-hint" }, `${f.period} vs ${f.prior_period}`),
      table(["Manager", ["Shares", "r"], ["Value", "r"], ["Change", "r"], ["% chg", "r"]], (f.holders || []).slice(0, 25).map(x => tr(() => f13Record(tk, f, x), el("td", {}, el("b", {}, short(nameCase(x.manager), 46))),
        td(qty(x.shares), "r num"), td(money(x.value), "r num"), td(sgnq(x.change), "r num " + (x.change > 0 ? "up" : x.change < 0 ? "down" : "")), td(x.new ? "new" : pct(x.pct_change), "r num")))),
      srcNote(`SEC Form 13F data sets. CUSIP ${(f.cusips || []).join(", ")} (${f.cusip_source}). 13F covers managers with over $100M in US equities; it reports long positions only and is filed up to 45 days after quarter end.`)), g);
  }

  /* =====================================================================
     3. Congress trades
     ===================================================================== */
  function tradeRecord(tk, r) {
    record({ eyebrow: `${r.chamber} periodic transaction report`, title: `${r.member} · ${r.type}`, value: r.amount, sub: `${party(r.party) ? party(r.party) + "-" : ""}${r.state_dst || ""} · traded ${dt(r.tx_date)} · filed ${dt(r.filed)}`,
      citedLabel: `PTR text, page ${r.page || "–"}`, citedSrc: r.doc_id ? `Filing ID ${r.doc_id}` : null, cited: r.cited, marks: [r.ticker_reported ? `(${r.ticker_reported})` : null, r.amount, r.amount?.split(" - ")[0], r.amount?.split(" - ")[1]],
      warn: r.committee_flag ? `Committee flag: ${r.member} sits on ${r.flag_committees.join(", ")}, which oversee this company's sector.` : null,
      fields: [["Member", r.member], ["Party", r.party], ["Chamber", r.chamber], ["State / district", r.state_dst], ["Owner", r.owner], ["Asset as reported", r.asset], ["Asset type", r.asset_type],
        ["Transaction", r.type], ["Transaction date", dt(r.tx_date)], ["Notification date", r.notified ? dt(r.notified) : null], ["Filed", dt(r.filed)],
        ["Disclosure lag", r.lag_days != null ? `${r.lag_days} days` + (r.lag_days > 45 ? " (over the 45-day deadline)" : "") : null], ["Amount range", r.amount],
        ["Committees", (r.committees || []).join("; ") || (r.member_matched ? "none listed" : "member not matched to current roster")]],
      links: [["PTR PDF", r.pdf_url]], src: r.src, note: "Members report trades in value ranges, not exact amounts. Use of financial disclosure reports is restricted by 5 U.S.C. app. 105(c).",
      cite: `${r.member}, ${r.chamber} PTR ${r.doc_id}: ${r.type} of ${r.asset} on ${r.tx_date}, ${r.amount}, filed ${r.filed}. ${r.pdf_url}` });
  }
  function renderCongress(host, tk, gov) {
    const rows = gov?.congress_trades || [];
    const meta = gov?.congress_meta || {};
    host.append(sourceStrip(gov?.sources, [["congress_house", "House PTRs"], ["congress_senate", "Senate eFD"], ["committees", "Committee assignments"]]));
    const yr = new Date(Date.now() - 365 * 864e5).toISOString().slice(0, 10);
    const r12 = rows.filter(r => (r.tx_date || "") >= yr);
    const buys = r12.filter(r => r.type_code === "P" || r.type === "Purchase"), sells = r12.filter(r => /Sale/.test(r.type || ""));
    const lag = rows.filter(r => r.lag_days != null).map(r => r.lag_days).sort((a, b) => a - b);
    host.append(el("div", { class: "stats co-stats ig-stats" },
      stat("Member trades, 12m", String(r12.length), `${new Set(r12.map(r => r.member)).size} members`),
      stat("Purchases / sales, 12m", `${buys.length} / ${sells.length}`, "transactions"),
      stat("By overseeing committees", String(r12.filter(r => r.committee_flag).length), "flagged trades, 12m", r12.some(r => r.committee_flag) ? "warn" : ""),
      stat("Purchase value range, 12m", buys.length ? `${money(buys.reduce((s, r) => s + (r.amin || 0), 0))}–${money(buys.reduce((s, r) => s + (r.amax || r.amin || 0), 0))}` : "–", "sum of reported ranges"),
      stat("Median disclosure lag", lag.length ? `${lag[Math.floor(lag.length / 2)]} days` : "–", "trade to filing"),
      stat("Latest trade", rows[0] ? dt(rows[0].tx_date) : "–", rows[0] ? short(rows[0].member, 24) : "none", "", rows[0] ? () => tradeRecord(tk, rows[0]) : null)));
    const ch = el("div", { class: "bd" });
    host.append(panel("Member trades on the price chart", el("span", { class: "faint ig-hint" }, "green = purchase · red = sale · amber = overseeing committee"), ch));
    requestAnimationFrame(() => priceDots(ch, tk, rows.filter(r => r.tx_date).map(r => ({ t: pd(r.tx_date), r: Math.max(3.5, Math.min(12, Math.log10((r.amax || r.amin || 1000)) * 2)), r0: r,
      tip: `<b>${esc(r.member)}</b> (${esc(party(r.party))})<br>${esc(r.type)} · ${esc(r.amount)}<br>${fdate(r.tx_date)}${r.committee_flag ? "<br>Overseeing committee" : ""}`, open: () => tradeRecord(tk, r) })),
      { color: d => d.r0.committee_flag ? css("--warn") : /Sale/.test(d.r0.type) ? css("--down") : css("--up"), label: "Congressional trades" }));
    let ch2 = "all", ty = "all", flagged = false;
    const hostT = el("div"), count = el("span", { class: "ig-count" });
    const fbtn = el("button", { class: "btn ig-toggle", onclick: () => { flagged = !flagged; fbtn.classList.toggle("on", flagged); draw(); } }, "Committee flag only");
    const draw = () => {
      const list = rows.filter(r => (ch2 === "all" || r.chamber === ch2) && (ty === "all" || (ty === "buy" ? (r.type_code === "P" || r.type === "Purchase") : /Sale/.test(r.type || ""))) && (!flagged || r.committee_flag));
      count.textContent = `${list.length} trade${list.length === 1 ? "" : "s"}`;
      hostT.innerHTML = "";
      hostT.append(table(["Traded", "Member", "Party", "Committee", "Type", ["Amount", "r"], "Filed", ["Lag", "r"], ["PDF", "r"]], list.slice(0, 300).map(r => tr(() => tradeRecord(tk, r),
        td(dt(r.tx_date), "mono"), el("td", {}, el("div", { class: "ig-who" }, el("b", {}, r.member), el("span", {}, `${r.chamber} · ${r.state_dst || ""}${r.owner && r.owner !== "Self" ? " · " + r.owner : ""}`))),
        el("td", {}, el("span", { class: "ig-party " + party(r.party) }, party(r.party) || "–")),
        el("td", {}, r.committee_flag ? el("span", { class: "chip warn", title: r.flag_committees.join(", ") }, "Oversight") : el("span", { class: "faint" }, r.member_matched ? "–" : "unmatched")),
        el("td", {}, el("span", { class: "chip " + (/Sale/.test(r.type) ? "down" : r.type === "Purchase" ? "up" : "") }, r.type)), td(r.amount, "r num"), td(dt(r.filed), "mono"),
        td(r.lag_days != null ? r.lag_days + "d" : "–", "r num " + (r.lag_days > 45 ? "down" : "")), el("td", { class: "r" }, ext(r.pdf_url, "PDF ↗", "ig-link"))))
        .concat(list.length ? [] : [emptyRow(9, rows.length ? "No trades match these filters." : "No House periodic transaction report since 2024 names this company's ticker.")]), "ig-wide"));
    };
    draw();
    host.append(panel("Congressional stock trades", el("div", { class: "ig-hd" }, count, fbtn, seg([["all", "All"], ["buy", "Purchases"], ["sell", "Sales"]], ty, v => { ty = v; draw(); }), seg([["all", "Both"], ["House", "House"], ["Senate", "Senate"]], ch2, v => { ch2 = v; draw(); })),
      hostT, srcNote(`House: Clerk periodic transaction reports (PDF text). Senate: ${gov?.sources?.congress_senate?.status === "ok" ? "eFD" : "not collected (see the source status above)"}. Overseeing committees for this company: ${(meta.committees_overseeing || []).join(", ") || "–"}. ${meta.flag_basis || ""} ${meta.legal_note || ""}`)));
  }

  /* =====================================================================
     4. Lobbying & PACs
     ===================================================================== */
  function lobbyRecord(tk, f, hl) {
    const text = (f.activities || []).map(a => `${a.issue}: ${a.description}${a.entities?.length ? "\n  Lobbied: " + a.entities.join(", ") : ""}`).join("\n\n");
    record({ eyebrow: "Lobbying Disclosure Act report", title: `${nameCase(f.registrant)} for ${nameCase(f.client)}`, value: f.amount != null ? money(f.amount) : null, sub: `${f.year} Q${f.quarter} · ${f.type || ""} · posted ${dt(f.posted)}`,
      citedLabel: "Lobbying activities as filed", cited: text, marks: hl ? [hl] : [],
      fields: [["Registrant", nameCase(f.registrant)], ["Client", f.client], ["Period", `${f.year} Q${f.quarter}`], ["Amount", f.amount != null ? money(f.amount) + ` (${f.basis})` : "not reported"], ["Filing UUID", f.uuid]],
      links: [["LDA filing", f.url]], src: f.src, cite: `LDA ${f.type} by ${f.registrant} for ${f.client}, ${f.year} Q${f.quarter}, ${f.url}` });
  }
  function renderLobby(host, tk, gov, alt) {
    const l = gov?.lobbying, pac = gov?.pac;
    host.append(sourceStrip(gov?.sources, [["lobbying", "LDA lobbying"], ["pac", "FEC PAC"], ["employee_contrib", "Employee contributions"]]));
    const q = l?.quarters || [];
    const last4 = q.slice(-4).reduce((s, x) => s + (x.amount || 0), 0);
    const c0 = pac?.cycles?.[0];
    host.append(el("div", { class: "stats co-stats ig-stats" },
      stat("Lobbying, last 4 quarters", q.length ? money(last4) : "–", q.length ? `${q.at(-4)?.year || q[0].year} Q${q.at(-4)?.quarter || q[0].quarter} – ${q.at(-1).year} Q${q.at(-1).quarter}` : "no LDA reports matched"),
      stat("LDA reports", String(l?.filing_count ?? 0), `${(l?.clients || []).length} client name${(l?.clients || []).length === 1 ? "" : "s"}`),
      stat("Bills named", String((l?.bills || []).length), "in activity descriptions"),
      stat("Agencies lobbied", String((l?.agencies || []).filter(a => !/HOUSE OF REP|SENATE/.test(a.name)).length), "besides House and Senate"),
      stat("PAC to candidates", c0 ? money(c0.to_candidates) : "–", c0 ? `${c0.cycle} cycle · ${c0.n_recipients} recipients` : "no corporate PAC matched", "", c0 ? () => pacRecord(tk, pac, c0) : null),
      stat("PAC split D / R", c0 && c0.to_candidates ? `${pct(c0.dem / c0.to_candidates, 0)} / ${pct(c0.rep / c0.to_candidates, 0)}` : "–", c0 ? "direct contributions" : " ")));
    const g = el("div", { class: "grid g64 ig-grid" });
    const ch = el("div", { class: "bd" });
    g.append(panel("Federal lobbying spend by quarter", el("span", { class: "faint ig-hint" }, "in-house expenses, or outside firms' income"), ch));
    const iss = l?.issues || [];
    g.append(panel("Issue areas", null, iss.length ? table(["Issue", ["Reports", "r"]], iss.slice(0, 12).map(i => tr(() => { const f = (l.filings || []).find(x => x.uuid === i.latest_filing); if (f) lobbyRecord(tk, f, i.name); }, td(i.name), td(String(i.filings), "r num")))) : missing("No lobbying reports matched.")));
    host.append(g);
    requestAnimationFrame(() => barsChart(ch, q.map(x => ({ k: `${String(x.year).slice(2)}Q${x.quarter}`, v: x.amount, x })), { keys: ["v"], colors: [css("--c1")], label: "Lobbying spend",
      tipf: d => `<b>${d.x.year} Q${d.x.quarter}</b><br>${money(d.v)} · ${d.x.basis}<br>${d.x.n || d.x.filings?.length || 0} reports`,
      onBar: d => { const f = (l.filings || []).find(x => x.year === d.x.year && x.quarter === d.x.quarter && /in-house/.test(x.basis)) || (l.filings || []).find(x => x.year === d.x.year && x.quarter === d.x.quarter); if (f) lobbyRecord(tk, f); } }));
    const g2 = el("div", { class: "grid g2 ig-grid2" });
    const bills = l?.bills || [];
    g2.append(panel("Bills named in lobbying reports", null, bills.length ? table(["Bill", "Congress", ["Reports", "r"], "Period"], bills.slice(0, 20).map(b => tr(() => {
      const c = b.cites?.[0]; const f = (l.filings || []).find(x => x.uuid === c?.filing);
      if (f) lobbyRecord(tk, f, c.match); else record({ eyebrow: "Bill in lobbying report", title: b.bill, cited: c?.text, marks: [c?.match], src: l.src });
    }, el("td", {}, el("b", {}, b.bill)), td(`${b.congress}th`), td(String(b.filings), "r num"), td(`${b.first} – ${b.last}`, "mono")))) : missing("No bill numbers appear in the matched reports.")));
    const ag = (l?.agencies || []).slice(0, 14);
    g2.append(panel("Agencies and chambers lobbied", null, ag.length ? table(["Entity", ["Reports", "r"]], ag.map(a => tr(() => { const f = (l.filings || []).find(x => x.uuid === a.latest_filing); if (f) lobbyRecord(tk, f, a.name); }, td(a.name), td(String(a.filings), "r num")))) : missing("No agencies listed.")));
    host.append(g2);
    const fl = l?.filings || [];
    host.append(panel("Lobbying reports", el("span", { class: "faint ig-hint" }, `${fl.length} shown`), fl.length ? table(["Period", "Registrant", "Issues", ["Amount", "r"], ["Report", "r"]], fl.slice(0, 40).map(f => tr(() => lobbyRecord(tk, f),
      td(`${f.year} Q${f.quarter}`, "mono"), td(short(nameCase(f.registrant), 40)), td(short([...new Set((f.activities || []).map(a => a.issue))].join(", "), 60)), td(f.amount != null ? money(f.amount) : "–", "r num"), el("td", { class: "r" }, ext(f.url, "LDA ↗", "ig-link"))))) :
      missing("No Lobbying Disclosure Act reports name this company as the client (exact legal-name match)."), srcNote(`lda.gov API. Client names matched: ${(l?.clients || []).join("; ") || "none"}. Search terms: ${(l?.search || []).join(", ")}.`)));
    renderPac(host, tk, pac, gov);
  }
  function pacRecord(tk, pac, c) {
    record({ eyebrow: "Corporate PAC, FEC", title: (pac.committees || []).map(x => nameCase(x.name)).join("; "), value: money(c.to_candidates), sub: `${c.cycle} cycle`,
      cited: (c.recipients || []).slice(0, 25).map(r => `${r.name} (${r.party || "–"}, ${r.office}-${r.state}${r.district && r.district !== "00" ? r.district : ""}): $${Math.round(r.amount).toLocaleString("en-US")}${r.oversight ? "  [overseeing committee]" : ""}`).join("\n"),
      citedLabel: "Largest direct contributions (FEC pas2, type 24K)", marks: ["[overseeing committee]"],
      fields: [["Receipts", money(c.receipts)], ["Disbursements", money(c.disbursements)], ["To candidates", money(c.to_candidates)], ["Democrats", money(c.dem)], ["Republicans", money(c.rep)], ["Other", money(c.other)], ["Recipients", String(c.n_recipients)]],
      links: (pac.committees || []).map(x => ["FEC committee", x.url]), src: c.src });
  }
  function renderPac(host, tk, pac, gov) {
    if (!gov?.sources?.pac) { host.append(panel("Political action committee", null, missing("FEC PAC data has not been collected for this company yet."))); return; }
    if (!pac?.committees?.length) { host.append(panel("Political action committee", null, missing("No FEC committee lists this company (legal name or Exhibit 21 subsidiary) as its connected organization."), srcNote(gov?.sources?.employee_contrib?.note || ""))); return; }
    const cyc = pac.cycles || [];
    const c = cyc[0];
    const body = el("div");
    body.append(table(["Cycle", ["Receipts", "r"], ["Disbursed", "r"], ["To candidates", "r"], ["Dem", "r"], ["Rep", "r"]], cyc.map(x => tr(() => pacRecord(tk, pac, x),
      td(String(x.cycle), "mono"), td(money(x.receipts), "r num"), td(money(x.disbursements), "r num"), td(money(x.to_candidates), "r num"), td(money(x.dem), "r num"), td(money(x.rep), "r num")))));
    if (c?.recipients?.length) body.append(el("div", { class: "eyebrow ig-sub" }, `Top recipients, ${c.cycle} cycle`), table(["Candidate", "Party", "Office", ["Amount", "r"], ""], c.recipients.slice(0, 12).map(r => tr(() => pacRecord(tk, pac, c),
      td(nameCase(r.member || r.name)), el("td", {}, el("span", { class: "ig-party " + party(r.party) }, party(r.party) || "–")), td(`${r.office}-${r.state}${r.district && r.district !== "00" ? r.district : ""}`, "mono"),
      td(money(r.amount), "r num"), el("td", {}, r.oversight ? el("span", { class: "chip warn" }, "Oversight") : null)))));
    host.append(panel("Political action committee (FEC)", el("span", { class: "faint ig-hint" }, (pac.committees || []).map(x => x.id).join(", ")), body,
      srcNote("FEC bulk data: committee master (connected organization), PAC summary, contributions to candidates. " + (gov?.sources?.employee_contrib?.note || ""))));
  }

  /* =====================================================================
     5. Hearings & quotes
     ===================================================================== */
  function quoteRecord(tk, h, q) {
    record({ eyebrow: `Congressional hearing${h.chamber ? ", " + h.chamber : ""}`, title: h.title, value: q.speaker || "Speaker not identified", sub: `${dt(h.date)} · ${(h.committees || []).join("; ")}`,
      citedLabel: "Transcript passage (GovInfo)", citedSrc: h.id, cited: q.context, marks: [q.text, q.match],
      fields: [["Quote", q.text], ["Speaker", q.speaker || "not identified"], ["Speaker party", q.speaker_party], ["Matched name", `${q.match} (${q.alias_kind})`], ["Committee", (h.committees || []).join("; ")],
        ["Subcommittee", (h.subcommittees || []).join("; ")], ["Mentions in transcript", String(h.mentions)], ["Package", h.id]],
      links: [["GovInfo", h.url], ["Transcript", h.text_url]], src: h.src,
      note: "The quote was cut from the printed transcript and re-checked verbatim against it (after whitespace and quote normalisation). Speaker attribution follows the transcript's turn markers.",
      cite: `"${q.text}" ${q.speaker || ""}, ${h.title}, ${(h.committees || []).join("; ")}, ${h.date}. ${h.url}` });
  }
  function renderHearings(host, tk, gov, sector) {
    const hs = gov?.hearings || [];
    host.append(sourceStrip(gov?.sources, [["hearings", "GovInfo hearings"]]));
    const qn = hs.reduce((s, h) => s + (h.quotes || []).length, 0);
    host.append(el("div", { class: "stats co-stats ig-stats" },
      stat("Hearings naming the company", String(hs.length), gov?.sources?.hearings?.note ? short(gov.sources.hearings.note, 40) : " "),
      stat("Verified quotes", String(qn), "verbatim from transcripts"),
      stat("Committees", String(new Set(hs.flatMap(h => h.committees || [])).size), "distinct"),
      stat("Latest", hs[0] ? dt(hs[0].date) : "–", hs[0] ? short(hs[0].committees?.[0], 30) : "none", "", hs[0]?.quotes?.[0] ? () => quoteRecord(tk, hs[0], hs[0].quotes[0]) : null)));
    const list = el("div", { class: "ig-quotes" });
    hs.forEach(h => {
      const item = el("div", { class: "ig-hearing" }, el("div", { class: "ig-h-hd" }, el("span", { class: "mono ig-h-date" }, dt(h.date)), el("b", {}, short(nameCase(h.title), 120)), ext(h.url, "GovInfo ↗", "ig-link")),
        el("div", { class: "ig-h-sub" }, [(h.committees || []).join("; "), h.chamber, h.mentions ? `${h.mentions} mention${h.mentions === 1 ? "" : "s"}` : null].filter(Boolean).join(" · ")));
      (h.quotes || []).forEach(q => item.append(el("button", { class: "ig-q", onclick: () => quoteRecord(tk, h, q) },
        el("span", { class: "ig-q-who" }, q.speaker || "Speaker not identified", q.speaker_party ? ` (${q.speaker_party})` : ""), el("span", { class: "ig-q-tx", html: "“" + markText(q.text, [q.match]) + "”" }))));
      list.append(item);
    });
    host.append(panel("Hearings that name the company", null, hs.length ? list : missing("No hearing transcript published on GovInfo since 2024 names this company (legal name, distinctive name or curated brand)."),
      srcNote("GovInfo CHRG collection (printed hearing transcripts, published weeks to months after the hearing). Ambiguous names match only in legal form.")));
    const sh = sector?.hearings || [];
    host.append(panel(`Recent ${sector?.sector || "industry"} hearings`, el("span", { class: "faint ig-hint" }, "title matches industry topics"), sh.length ? table(["Date", "Hearing", "Committee", "Match"], sh.map(h => tr(() => record({
      eyebrow: "Industry hearing", title: nameCase(h.title), sub: `${dt(h.date)} · ${(h.committees || []).join("; ")}`, cited: h.title + (h.witnesses?.length ? "\n\nWitnesses:\n" + h.witnesses.join("\n") : ""), marks: [h.match],
      fields: [["Chamber", h.chamber], ["Matched topic term", h.match]], links: [["GovInfo", h.url], ["Transcript", h.text_url]], src: { url: h.url, method: sector.method } }),
      td(dt(h.date), "mono"), td(short(nameCase(h.title), 90)), td(short((h.committees || [])[0], 40)), td(h.match)))) : missing("No industry hearing list available.")));
  }

  /* =====================================================================
     6. Regulation & legislation
     ===================================================================== */
  function frRecord(tk, r) {
    record({ eyebrow: `Federal Register · ${r.type}`, title: r.title, value: r.citation || r.document_number, sub: `${dt(r.date)} · ${(r.agencies || []).join("; ")}`,
      citedLabel: "Search excerpt (Federal Register API)", cited: [r.excerpt, r.abstract ? "\nAbstract: " + r.abstract : ""].join(""), marks: [...(r.marks || []), r.match],
      fields: [["Action", r.action], ["Document number", r.document_number], ["Effective", r.effective_on ? dt(r.effective_on) : null], ["Comments close", r.comments_close_on ? dt(r.comments_close_on) : null],
        ["Significant", r.significant ? "yes (EO 12866)" : null], ["Docket", (r.docket_ids || []).join(", ")], ["Search term", r.term]],
      links: [["Federal Register", r.url], ["PDF", r.pdf_url]], src: r.src, cite: `${r.title}, ${r.citation || r.document_number}, ${r.date}. ${r.url}` });
  }
  function lawRecord(tk, l) {
    const c = l.lobby_cites?.[0];
    record({ eyebrow: l.link === "lobbied" ? "Public law the company lobbied on" : "Industry public law", title: l.label, value: l.bill || (l.bills || [])[0], sub: `approved ${dt(l.date)} · ${l.congress}th Congress`,
      citedLabel: c ? "Lobbying report text naming the bill" : "Law title", cited: c ? c.text : [l.title, ...(l.short_titles || [])].join("\n"), marks: c ? [c.match] : [l.match],
      fields: [["Title", l.title], ["Short titles", (l.short_titles || []).join("; ")], ["Bills", (l.bills || []).join(", ")], ["Link", l.link === "lobbied" ? `${l.bill} named in ${l.lobby_filings} lobbying reports in the same Congress` : `title matches "${l.match}"`]],
      links: [["GovInfo", l.url]], src: l.src });
  }
  function renderRegs(host, tk, gov) {
    const rg = gov?.regulation || {}, lg = gov?.legislation || [];
    host.append(sourceStrip(gov?.sources, [["regulation", "Federal Register"], ["legislation", "Public laws"]]));
    host.append(el("div", { class: "stats co-stats ig-stats" },
      stat("Final rules naming the company", String((rg.rules || []).length), (rg.counts && Object.entries(rg.counts).filter(([k]) => k.startsWith("rules")).map(([, v]) => v).reduce((a, b) => a + b, 0)) ? "latest 20 shown" : " "),
      stat("Proposed rules", String((rg.proposed || []).length), " "),
      stat("Lobbied bills that became law", String(lg.filter(l => l.link === "lobbied").length), "118th–119th Congress"),
      stat("Industry laws", String(lg.filter(l => l.link === "industry").length), "title topic match")));
    const frT = list => list.length ? table(["Published", "Title", "Agency", ""], list.map(r => tr(() => frRecord(tk, r), td(dt(r.date), "mono"), el("td", {}, el("b", {}, short(r.title, 110)), r.significant ? el("span", { class: "chip warn" }, "Significant") : null),
      td(short((r.agencies || []).join("; "), 50)), el("td", { class: "r" }, ext(r.url, "FR ↗", "ig-link"))))) : missing("None found.");
    const g = el("div", { class: "grid g2 ig-grid2" });
    g.append(panel("Final rules naming the company", null, frT(rg.rules || [])), panel("Proposed rules naming the company", null, frT(rg.proposed || [])));
    host.append(g);
    host.append(panel("Legislation", el("span", { class: "faint ig-hint" }, "public laws, 118th and 119th Congress"), lg.length ? table(["Approved", "Law", "Title", "Link"], lg.map(l => tr(() => lawRecord(tk, l),
      td(dt(l.date), "mono"), el("td", {}, el("b", {}, l.label)), td(short((l.short_titles || [])[0] || l.title, 110)),
      el("td", {}, el("span", { class: "chip " + (l.link === "lobbied" ? "acc" : "") }, l.link === "lobbied" ? `Lobbied (${l.bill})` : "Industry"))))) : missing("No public laws linked."),
      srcNote(`Federal Register API full-text search, terms: ${(rg.terms || []).join(", ") || "–"}. Laws: GovInfo PLAW records; "Lobbied" means the law's primary bill appears in this company's LDA reports in the same Congress.`)));
  }

  /* =====================================================================
     7. Government contracts & funding
     ===================================================================== */
  function awardRecord(tk, a, sp) {
    record({ eyebrow: `Federal award · ${a.group}`, title: nameCase(a.recipient), value: money(a.amount), sub: `${a.agency || ""}${a.sub_agency ? " · " + a.sub_agency : ""}`,
      citedLabel: "Award description (USAspending)", cited: a.description || "(no description)", marks: (a.programs || []).length ? ["CHIPS", "Inflation Reduction Act", "INFLATION REDUCTION ACT"] : [],
      fields: [["Award ID", a.award_id], ["Type", a.group], ["Amount", money(a.amount)], ["Awarding agency", a.agency], ["Sub-agency", a.sub_agency], ["Start", a.start], ["End", a.end], ["Programs", (a.programs || []).join(", ")]],
      links: [["USAspending award", a.url]], src: a.src });
  }
  function renderSpending(host, tk, gov, alt) {
    const sp = gov?.spending;
    host.append(sourceStrip(gov?.sources, [["spending", "USAspending"]]));
    if (!sp || sp.status !== "ok") {
      host.append(panel("Federal contracts and funding", null, missing(sp?.status === "no_match" || gov?.sources?.spending?.note ? `No federal awards found: ${gov?.sources?.spending?.note || "no recipient matched"}.` : "Federal award data has not been collected for this company yet."),
        srcNote(`USAspending recipient search terms: ${(sp?.search || []).join(", ") || "–"}. Matching is exact on the SEC legal name or an Exhibit 21 subsidiary.`)));
      return;
    }
    const fy = sp.fy || [], full = fy.filter(f => !f.partial), lastF = full.at(-1), prevF = full.at(-2);
    const ag = sp.agencies || {}, agKey = Object.keys(ag).sort().at(-2) || Object.keys(ag)[0];
    host.append(el("div", { class: "stats co-stats ig-stats" },
      stat(`Obligations FY${lastF?.fy || ""}`, money(lastF?.total), lastF && prevF && prevF.total ? `${lastF.total >= prevF.total ? "+" : ""}${pct(lastF.total / prevF.total - 1)} vs FY${prevF.fy}` : " ", "", lastF ? () => record({ eyebrow: "USAspending obligations", title: `FY${lastF.fy}`, value: money(lastF.total), fields: [["Contracts (incl. IDVs)", money(lastF.contracts)], ["Grants", money(lastF.grants)], ["Loans", money(lastF.loans)], ["Other assistance", money(lastF.other)], ["Entities queried", (sp.ueis || []).join(", ")]], src: sp.src }) : null),
      stat("Contracts share", lastF?.total ? pct(lastF.contracts / lastF.total, 0) : "–", "of obligations"),
      stat("Grants + loans", money((lastF?.grants || 0) + (lastF?.loans || 0)), `FY${lastF?.fy || ""}`),
      stat("Top agency", ag[agKey]?.[0] ? short(ag[agKey][0].code || ag[agKey][0].name, 14) : "–", ag[agKey]?.[0] ? `${money(ag[agKey][0].amount)} · FY${agKey}` : " "),
      stat("Matched entities", String(sp.entity_count || (sp.entities || []).length), `${(sp.ueis || []).length} UEIs queried`),
      stat("Program flags", (sp.programs || []).length ? sp.programs.join(", ") : "none", "CHIPS / IRA / IIJA in award text")));
    const g = el("div", { class: "grid g64 ig-grid" });
    const ch = el("div", { class: "bd" });
    g.append(panel("Federal obligations by fiscal year", el("span", { class: "ig-legend" }, ...[["Contracts", "--c1"], ["Grants", "--c2"], ["Loans", "--c3"], ["Other", "--c4"]].map(([n, c]) => el("span", {}, el("i", { style: `background:var(${c})` }), n))), ch));
    g.append(panel(`Awarding agencies, FY${agKey || ""}`, null, (ag[agKey] || []).length ? table(["Agency", ["Obligated", "r"]], ag[agKey].map(a => tr(() => record({ eyebrow: "Awarding agency", title: a.name, value: money(a.amount), sub: `FY${agKey}`, src: sp.src, fields: [["Code", a.code]] }), td(a.name), td(money(a.amount), "r num")))) : missing("No agency breakdown.")));
    host.append(g);
    requestAnimationFrame(() => barsChart(ch, fy.map(f => ({ k: `FY${String(f.fy).slice(2)}${f.partial ? "*" : ""}`, f, contracts: f.contracts, grants: f.grants, loans: f.loans, other: f.other })),
      { keys: ["contracts", "grants", "loans", "other"], colors: [css("--c1"), css("--c2"), css("--c3"), css("--c4")], label: "Federal obligations",
        tipf: d => `<b>FY${d.f.fy}${d.f.partial ? " (partial)" : ""}</b><br>Contracts ${money(d.contracts)}<br>Grants ${money(d.grants)}<br>Loans ${money(d.loans)}<br>Other ${money(d.other)}`,
        onBar: d => record({ eyebrow: "USAspending obligations", title: `FY${d.f.fy}${d.f.partial ? " (year to date)" : ""}`, value: money(d.f.total), fields: [["Contracts (incl. IDVs)", money(d.f.contracts)], ["Grants", money(d.f.grants)], ["Loans", money(d.f.loans)], ["Other assistance", money(d.f.other)]], src: sp.src }) }));
    const aw = sp.awards || [];
    host.append(panel("Largest awards with activity in the last three fiscal years (total award value)", null, aw.length ? table(["Recipient", "Type", "Agency", ["Amount", "r"], "Description", ""], aw.map(a => tr(() => awardRecord(tk, a, sp),
      td(short(nameCase(a.recipient), 34)), td(a.group), td(short(a.sub_agency || a.agency, 34)), td(money(a.amount), "r num"), el("td", {}, short(a.description, 70), ...(a.programs || []).map(p => el("span", { class: "chip acc" }, p))),
      el("td", { class: "r" }, ext(a.url, "Award ↗", "ig-link"))))) : missing("No awards in the period."),
      srcNote(`USAspending.gov. Entities: ${(sp.entities || []).slice(0, 6).map(e => nameCase(e.name)).join("; ")}${(sp.entity_count || 0) > 6 ? "…" : ""}. Contract values are obligations including modifications; IDV totals are included with contracts.`)));
  }

  /* =====================================================================
     8. Off-exchange & short activity
     ===================================================================== */
  function weekRecord(tk, w, f) {
    record({ eyebrow: "FINRA OTC transparency, weekly", title: `Week of ${dt(w.week)}`, value: w.offex_share != null ? pct(w.offex_share) + " off-exchange" : "share not computed", sub: w.tier || "",
      citedLabel: "Calculation", cited: `Off-exchange share = (ATS shares + non-ATS OTC shares) / consolidated weekly volume\n= (${(w.ats_shares || 0).toLocaleString("en-US")} + ${(w.otc_shares || 0).toLocaleString("en-US")}) / ${w.consolidated_volume ? Math.round(w.consolidated_volume).toLocaleString("en-US") : "n/a"}${w.offex_share != null ? "\n= " + pct(w.offex_share) : ""}`,
      marks: [pct(w.offex_share)], warn: w.note,
      fields: [["ATS (dark pool) shares", (w.ats_shares ?? "–").toLocaleString("en-US")], ["ATS trades", (w.ats_trades ?? "–").toLocaleString("en-US")], ["Non-ATS OTC shares", (w.otc_shares ?? "–").toLocaleString("en-US")],
        ["Non-ATS OTC trades", (w.otc_trades ?? "–").toLocaleString("en-US")], ["Consolidated volume (daily bars)", w.consolidated_volume ? Math.round(w.consolidated_volume).toLocaleString("en-US") + ` (${w.bar_days} days)` : "–"], ["FINRA published", w.published]],
      links: [["FINRA OTC transparency", f.src_weekly?.page]], src: f.src_weekly, note: "FINRA publishes Tier 1 NMS stocks two weeks after the week and Tier 2 four weeks after. Consolidated volume comes from Filed Under's daily bars." });
  }
  function siRecord(tk, s, f) {
    record({ eyebrow: "FINRA consolidated short interest", title: `Settlement ${dt(s.date)}`, value: qty(s.short) + " shares", sub: `${s.dtc ?? "–"} days to cover`,
      fields: [["Short position", (s.short ?? 0).toLocaleString("en-US")], ["Previous", (s.prev ?? 0).toLocaleString("en-US")], ["Change", s.chg_pct != null ? s.chg_pct + "%" : null], ["Average daily volume", (s.adv ?? 0).toLocaleString("en-US")],
        ["Days to cover", s.dtc], ["Revision", s.revised], ["Split", s.split]], links: [["FINRA short interest", f.src_short_interest?.page]], src: f.src_short_interest });
  }
  const venueName = v => String(v.name || "").replace(new RegExp("^" + (v.mpid || "####") + "\\s+"), "") || v.mpid;
  function renderFlows(host, tk, flows, alt) {
    const f = flows || {};
    host.append(sourceStrip(f.sources, [["finra_weekly", "FINRA ATS/OTC weekly"], ["finra_short_interest", "FINRA short interest"], ["regsho", "Reg SHO daily (Alt data)"]]));
    const wk = f.ats_weekly || [], ok = wk.filter(w => w.offex_share != null), lw = ok.at(-1);
    const avg = a => a.length ? a.reduce((s, x) => s + x, 0) / a.length : null;
    const a4 = avg(ok.slice(-4).map(w => w.offex_share)), a26 = avg(ok.map(w => w.offex_share));
    const si = f.short_interest || [], ls = si.at(-1);
    const sv = (alt?.short_volume?.rows || []).filter(r => r[3] > 0), sv20 = sv.slice(-20);
    const svr = sv20.length ? sv20.reduce((s, r) => s + r[1], 0) / sv20.reduce((s, r) => s + r[3], 0) : null;
    host.append(el("div", { class: "stats co-stats ig-stats" },
      stat("Off-exchange share, latest week", lw ? pct(lw.offex_share) : "–", lw ? `week of ${dt(lw.week)}` : "unavailable", "", lw ? () => weekRecord(tk, lw, f) : null),
      stat("Off-exchange, 4w vs 26w", a4 != null ? `${pct(a4)} / ${pct(a26)}` : "–", "average share"),
      stat("ATS (dark pool) share", lw?.ats_share != null ? pct(lw.ats_share) : "–", "of consolidated volume"),
      stat("Short interest", ls ? qty(ls.short) : "–", ls ? `${dt(ls.date)} · ${ls.chg_pct != null ? (ls.chg_pct >= 0 ? "+" : "") + ls.chg_pct + "%" : ""}` : "unavailable", "", ls ? () => siRecord(tk, ls, f) : null),
      stat("Days to cover", ls?.dtc != null ? String(ls.dtc) : "–", "short / avg daily volume"),
      stat("Short volume ratio, 20d", svr != null ? pct(svr) : "–", "FINRA Reg SHO, off-exchange")));
    const g = el("div", { class: "grid g2 ig-grid2" });
    const c1 = el("div", { class: "bd" }), c2 = el("div", { class: "bd" });
    g.append(panel("Off-exchange share of volume, weekly", el("span", { class: "ig-legend" }, el("span", {}, el("i", { style: "background:var(--c1)" }), "ATS + OTC"), el("span", {}, el("i", { style: "background:var(--c3)" }), "ATS only")), c1),
      panel("Short interest", el("span", { class: "ig-legend" }, el("span", {}, el("i", { style: "background:var(--c5)" }), "shares short")), c2));
    host.append(g);
    requestAnimationFrame(() => {
      lineChart(c1, [{ name: "Off-exchange", color: css("--c1"), pts: wk.map(w => ({ t: pd(w.week), d: w.week, v: w.offex_share, w })) },
        { name: "ATS", color: css("--c3"), pts: wk.map(w => ({ t: pd(w.week), d: w.week, v: w.ats_share, w })) }],
        { yfmt: v => pct(v, 0), label: "Off-exchange share", onPoint: p => weekRecord(tk, p.w, f), tipf: (p, s) => `<b>Week of ${fdate(p.d)}</b><br>${esc(s.name)}: ${pct(p.v)}` });
      lineChart(c2, [{ name: "Short interest", color: css("--c5"), dots: true, pts: si.map(s => ({ t: pd(s.date), d: s.date, v: s.short, s })) }],
        { yfmt: v => qty(v), label: "Short interest", onPoint: p => siRecord(tk, p.s, f), tipf: p => `<b>${fdate(p.d)}</b><br>${qty(p.v)} short · ${p.s.dtc} days to cover` });
    });
    const ven = f.ats_venues?.rows || [];
    host.append(panel("Largest ATS venues", el("span", { class: "faint ig-hint" }, f.ats_venues?.week ? `week of ${dt(f.ats_venues.week)}` : ""), ven.length ? table(["ATS", "MPID", ["Shares", "r"], ["Trades", "r"], ["Share of ATS", "r"]], ven.map(v => tr(() => record({
      eyebrow: "FINRA ATS weekly, by venue", title: venueName(v), value: qty(v.shares) + " shares", sub: `week of ${dt(v.week)} · MPID ${v.mpid}`, fields: [["Shares", (v.shares || 0).toLocaleString("en-US")], ["Trades", (v.trades || 0).toLocaleString("en-US")], ["Share of ATS volume", pct(v.pct_of_ats)]],
      links: [["FINRA OTC transparency", f.src_venues?.page]], src: f.src_venues }), td(venueName(v)), td(v.mpid, "mono"), td(qty(v.shares), "r num"), td((v.trades || 0).toLocaleString("en-US"), "r num"), td(pct(v.pct_of_ats), "r num")))) :
      missing("No ATS venue data for the latest weeks."),
      srcNote("FINRA OTC (ATS and non-ATS) transparency data via the FINRA Query API. Consolidated volume from Filed Under daily bars. FINRA weekly data excludes trades on exchanges; short interest is reported twice a month.")));
  }

  /* =====================================================================
     AI read
     ===================================================================== */
  function findRef(gov, flows, ins, id) {
    const all = [...(gov?.congress_trades || []), ...(gov?.lobbying?.filings || []), ...(gov?.hearings || []), ...(gov?.regulation?.rules || []), ...(gov?.regulation?.proposed || []), ...(gov?.legislation || []),
      ...(flows?.holders13dg?.current || []), ...(gov?.spending?.awards || [])];
    return all.find(r => r.id === id);
  }
  function aiRead(host, tk, gov, flows, ins, go) {
    const a = gov?.ai_read;
    if (!a || a.status !== "ok" || !(a.sentences || []).length) return;
    const facts = Object.fromEntries((a.facts || []).map(f => [f.id, f.text]));
    const box = el("div", { class: "ig-ai" }, el("div", { class: "ig-ai-hd" }, el("span", { class: "chip chip-ai" }, svgUse("i-spark"), "AI-generated"), el("b", {}, "Influence & flows read"),
      el("span", { class: "faint" }, `${a.model || ""} · ${relTime(a.generated_at)}${a.dropped?.length ? ` · ${a.dropped.length} unverified sentence${a.dropped.length === 1 ? "" : "s"} removed` : ""}`)));
    const p = el("div", { class: "ig-ai-tx" });
    a.sentences.forEach(s => {
      p.append(el("span", {}, s.text + " "), ...s.refs.map((r, i) => el("button", { class: "chip link ig-ref", title: facts[r] || r, onclick: () => {
        const rec = findRef(gov, flows, ins, r);
        if (rec && rec.chamber && rec.asset) return tradeRecord(tk, rec);
        if (rec && rec.quotes) return quoteRecord(tk, rec, rec.quotes[0]);
        if (rec && rec.document_number) return frRecord(tk, rec);
        if (rec && rec.uuid) return lobbyRecord(tk, rec);
        if (rec && rec.label && rec.link) return lawRecord(tk, rec);
        if (rec && rec.form && rec.accession) return holderRecord(tk, rec);
        if (rec && rec.award_id !== undefined) return awardRecord(tk, rec);
        record({ eyebrow: "Record behind the AI sentence", title: r, cited: facts[r], ai: "Sentence generated from this stored record.", src: { method: "Fact assembled from the stored records on this tab" } });
      } }, String(i + 1))), " ");
    });
    box.append(p, el("div", { class: "ig-ai-ft" }, a.label || "AI-generated from the records on this tab. Each sentence links to its records."));
    host.append(box);
  }

  /* =====================================================================
     tab
     ===================================================================== */
  const SUBS = [["insiders", "Insiders"], ["holders", "Large holders"], ["congress", "Congress trades"], ["lobby", "Lobbying & PACs"], ["hearings", "Hearings & quotes"], ["regs", "Regulation & legislation"], ["spending", "Contracts & funding"], ["flows", "Off-exchange & short"]];
  async function render(body, tk) {
    const host = el("div", { class: "ig" }, el("div", { class: "muted ig-pad" }, "Loading insider, government and flow data…"));
    body.append(host);
    const [ins, gov, flows, alt] = await Promise.all([opt(`insiders/${tk}.json`), opt(`gov/${tk}.json`), opt(`flows/${tk}.json`), opt(`altdata/${tk}.json`)]);
    if (!host.isConnected) return;
    host.innerHTML = "";
    let sub = store.get("igSub", "insiders");
    if (!SUBS.find(s => s[0] === sub)) sub = "insiders";
    const nav = el("div", { class: "ig-nav", role: "tablist", "aria-label": "Insiders and influence sections" });
    const pane = el("div", { class: "ig-pane" });
    const counts = {
      insiders: (ins?.rows || []).length, holders: (flows?.holders13dg?.current || []).length + (flows?.inst13f?.holders || []).length, congress: (gov?.congress_trades || []).length,
      lobby: gov?.lobbying?.filing_count || 0, hearings: (gov?.hearings || []).length, regs: (gov?.regulation?.rules || []).length + (gov?.regulation?.proposed || []).length + (gov?.legislation || []).length,
      spending: (gov?.spending?.fy || []).length, flows: (flows?.ats_weekly || []).length,
    };
    const show = id => {
      sub = id; store.set("igSub", id);
      nav.querySelectorAll("button").forEach(b => { const on = b.dataset.id === id; b.classList.toggle("on", on); b.setAttribute("aria-selected", String(on)); });
      pane.innerHTML = "";
      const go = { insiders: () => renderInsiders(pane, tk, ins), holders: () => renderHolders(pane, tk, gov, flows), congress: () => renderCongress(pane, tk, gov),
        lobby: () => renderLobby(pane, tk, gov, alt), hearings: async () => { const s = gov?.sector_hearings_path ? await opt(gov.sector_hearings_path) : null; renderHearings(pane, tk, gov, s); },
        regs: () => renderRegs(pane, tk, gov), spending: () => renderSpending(pane, tk, gov, alt), flows: () => renderFlows(pane, tk, flows, alt) }[id];
      try { go(); } catch (e) { console.error(e); pane.append(missing("This section could not be displayed: " + e.message)); }
    };
    SUBS.forEach(([id, n]) => nav.append(el("button", { "data-id": id, role: "tab", onclick: () => show(id) }, n, counts[id] ? el("span", { class: "ig-n" }, String(counts[id] > 999 ? "999+" : counts[id])) : null)));
    host.append(el("div", { class: "ig-head" }, el("div", {}, el("div", { class: "eyebrow" }, "Insiders & influence"),
      el("div", { class: "muted ig-lede" }, "Insider trades, large holders, members of Congress who trade the stock, lobbying and PAC money, hearings that name the company, rules and laws, federal awards, and off-exchange and short activity. Every value opens its source.")),
      el("div", { class: "faint ig-asof" }, gov?.generated ? `Government data updated ${relTime(gov.generated)}` : "Government data not yet collected", flows?.generated ? ` · flows ${relTime(flows.generated)}` : "")));
    aiRead(host, tk, gov, flows, ins);
    host.append(nav, pane);
    show(sub);
  }

  window.FU_INSIDERS_GOV = { render, clusters, markText };
  window.FU_COTABS = window.FU_COTABS || [];
  window.FU_COTABS.push({ id: "insiders", name: "Insiders & influence", after: "filings", render });
})();
