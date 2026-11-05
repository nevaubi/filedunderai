// Alt data tab on the company page: off-exchange short-sale volume (FINRA Reg SHO), fails-to-deliver (SEC),
// federal lobbying (LDA reports) and federal award obligations (USAspending). Data: altdata/{T}.json (etl/altdata.py).
// Registers itself in window.FU_COTABS; app.js adds it after the Insiders tab.
(function () {
  "use strict";
  const AD = {};
  const load = tk => AD[tk] || (AD[tk] = getJSON(`altdata/${tk}.json`).catch(e => { delete AD[tk]; throw e; }));
  const money = v => {
    if (v == null || !isFinite(v)) return "–";
    const a = Math.abs(v), s = v < 0 ? "−" : "";
    if (a >= 1e9) return `${s}$${(a / 1e9).toFixed(a >= 1e11 ? 0 : a >= 1e10 ? 1 : 2)}B`;
    if (a >= 1e6) return `${s}$${(a / 1e6).toFixed(a >= 1e8 ? 0 : 1)}M`;
    if (a >= 1e3) return `${s}$${(a / 1e3).toFixed(a >= 1e5 ? 0 : 1)}K`;
    return `${s}$${Math.round(a)}`;
  };
  const qty = v => {
    if (v == null || !isFinite(v)) return "–";
    const a = Math.abs(v);
    return a >= 1e6 ? (v / 1e6).toFixed(a >= 1e8 ? 0 : 1) + "M" : a >= 1e4 ? (v / 1e3).toFixed(0) + "K" : Math.round(v).toLocaleString("en-US");
  };
  const pct = (v, d = 1) => v == null || !isFinite(v) ? "–" : (v * 100).toFixed(d) + "%";
  const pp = v => v == null || !isFinite(v) ? "–" : (v >= 0 ? "+" : "−") + Math.abs(v * 100).toFixed(1) + " pts";
  const mean = a => a.length ? a.reduce((s, x) => s + x, 0) / a.length : null;
  const link = (href, text) => el("a", { href, target: "_blank", rel: "noopener" }, text);
  const stat = (l, v, d, cls = "") => el("div", {}, el("div", { class: "l" }, l), el("div", { class: "v num " + cls }, v), el("div", { class: "d" }, d || " "));
  const note = (...parts) => el("div", { class: "src" }, ...parts);

  function axes(svg, x, y, W, H, m, yfmt, xticks = 6) {
    svg.append("g").attr("transform", `translate(0,${H - m.b})`).call(d3.axisBottom(x).ticks(xticks).tickSizeOuter(0))
      .call(g => { g.selectAll("text").attr("fill", css("--muted")); g.select(".domain").attr("stroke", css("--line")); g.selectAll("line").attr("stroke", css("--line")); });
    svg.append("g").attr("transform", `translate(${m.l},0)`).call(d3.axisLeft(y).ticks(5).tickFormat(yfmt).tickSize(-(W - m.l - m.r)))
      .call(g => { g.select(".domain").remove(); g.selectAll("line").attr("stroke", css("--line")).attr("stroke-opacity", .6); g.selectAll("text").attr("fill", css("--muted")); });
  }
  const svgFor = (host, H, label) => d3.select(host).append("svg").attr("width", host.clientWidth || 760).attr("height", H).attr("role", "img").attr("aria-label", label);

  /* ---------- short-sale volume ---------- */
  function shortSeries(d) {
    const rows = (d.short_volume?.rows || []).filter(r => r[3] > 0).map(r => ({ t: pd(r[0]), date: r[0], short: r[1], exempt: r[2], total: r[3], ratio: r[1] / r[3] }));
    rows.forEach((r, i) => { const w = rows.slice(Math.max(0, i - 19), i + 1); r.ma = w.length >= 10 ? w.reduce((s, x) => s + x.short, 0) / w.reduce((s, x) => s + x.total, 0) : null; });
    return rows;
  }
  function shortChart(host, rows, days) {
    host.innerHTML = "";
    const cut = new Date(Date.now() - days * 864e5), data = rows.filter(r => r.t >= cut);
    if (data.length < 5) { host.append(el("div", { class: "muted" }, "Not enough daily files yet.")); return; }
    const W = host.clientWidth || 760, H = 230, m = { t: 12, r: 16, b: 24, l: 44 };
    const svg = svgFor(host, H, "Off-exchange short-sale volume ratio");
    const x = d3.scaleUtc().domain(d3.extent(data, r => r.t)).range([m.l, W - m.r]);
    const y = d3.scaleLinear().domain([Math.max(0, d3.min(data, r => r.ratio) - .05), Math.min(1, d3.max(data, r => r.ratio) + .05)]).nice().range([H - m.b, m.t]);
    axes(svg, x, y, W, H, m, d => (d * 100).toFixed(0) + "%");
    svg.append("path").datum(data).attr("fill", "none").attr("stroke", css("--c6")).attr("stroke-opacity", .55).attr("stroke-width", 1).attr("d", d3.line().x(r => x(r.t)).y(r => y(r.ratio)));
    svg.append("path").datum(data.filter(r => r.ma != null)).attr("fill", "none").attr("stroke", css("--c1")).attr("stroke-width", 1.8).attr("d", d3.line().x(r => x(r.t)).y(r => y(r.ma)));
    const hover = svg.append("circle").attr("r", 3.5).attr("fill", css("--c1")).attr("opacity", 0);
    svg.append("rect").attr("x", m.l).attr("y", m.t).attr("width", W - m.l - m.r).attr("height", H - m.t - m.b).attr("fill", "transparent")
      .on("mousemove", ev => {
        const t = x.invert(d3.pointer(ev)[0]), i = Math.min(data.length - 1, d3.bisector(r => r.t).left(data, t)), r = data[i];
        hover.attr("cx", x(r.t)).attr("cy", y(r.ratio)).attr("opacity", 1);
        showTip(ev, `<b>${fdate(r.date)}</b><br>Short ${pct(r.ratio)} of off-exchange volume<br>${qty(r.short)} of ${qty(r.total)} shares${r.ma != null ? `<br><span class="faint">20-day: ${pct(r.ma)}</span>` : ""}`);
      })
      .on("mouseleave", () => { tip.hidden = true; hover.attr("opacity", 0); });
  }

  /* ---------- fails to deliver ---------- */
  function ftdChart(host, rows) {
    const data = rows.map(r => ({ t: pd(r[0]), date: r[0], q: r[1], price: r[2], v: r[2] ? r[1] * r[2] : null }));
    if (!data.length) { host.append(el("div", { class: "muted" }, "No fails-to-deliver reported for this symbol in the files covered.")); return; }
    const W = host.clientWidth || 760, H = 200, m = { t: 12, r: 16, b: 24, l: 52 };
    const svg = svgFor(host, H, "Fails to deliver by settlement date");
    const x = d3.scaleUtc().domain([d3.min(data, r => r.t), d3.timeDay.offset(d3.max(data, r => r.t), 1)]).range([m.l, W - m.r]);
    const y = d3.scaleLinear().domain([0, d3.max(data, r => r.q)]).nice().range([H - m.b, m.t]);
    axes(svg, x, y, W, H, m, d => qty(d));
    const bw = Math.max(1.5, Math.min(8, (W - m.l - m.r) / Math.max(30, (x.domain()[1] - x.domain()[0]) / 864e5) * .7));
    svg.append("g").selectAll("rect").data(data).join("rect")
      .attr("x", r => x(r.t) - bw / 2).attr("width", bw).attr("y", r => y(r.q)).attr("height", r => y(0) - y(r.q)).attr("fill", css("--c3")).attr("rx", 1)
      .on("mousemove", (ev, r) => showTip(ev, `<b>${fdate(r.date)}</b><br>${Math.round(r.q).toLocaleString("en-US")} shares failed${r.price ? `<br>at $${fnum(r.price)} · ${money(r.v)}` : ""}`))
      .on("mouseleave", () => { tip.hidden = true; });
  }

  /* ---------- lobbying ---------- */
  function lobbyChart(host, rows) {
    const data = rows.map(r => ({ ...r, key: `${r.year} Q${r.quarter}` }));
    const W = host.clientWidth || 520, H = 200, m = { t: 12, r: 12, b: 24, l: 48 };
    const svg = svgFor(host, H, "Federal lobbying spend by quarter");
    const x = d3.scaleBand().domain(data.map(r => r.key)).range([m.l, W - m.r]).padding(.25);
    const y = d3.scaleLinear().domain([0, d3.max(data, r => r.amount) || 1]).nice().range([H - m.b, m.t]);
    svg.append("g").attr("transform", `translate(${m.l},0)`).call(d3.axisLeft(y).ticks(4).tickFormat(money).tickSize(-(W - m.l - m.r)))
      .call(g => { g.select(".domain").remove(); g.selectAll("line").attr("stroke", css("--line")).attr("stroke-opacity", .6); g.selectAll("text").attr("fill", css("--muted")); });
    const years = [...new Set(data.map(r => r.year))];
    svg.append("g").attr("transform", `translate(0,${H - m.b})`).call(d3.axisBottom(x).tickValues(data.filter(r => r.quarter === 1 || r === data[0]).map(r => r.key)).tickFormat(k => k.slice(0, 4)).tickSizeOuter(0))
      .call(g => { g.selectAll("text").attr("fill", css("--muted")); g.select(".domain").attr("stroke", css("--line")); });
    svg.append("g").selectAll("rect").data(data).join("rect")
      .attr("x", r => x(r.key)).attr("width", x.bandwidth()).attr("y", r => y(r.amount)).attr("height", r => y(0) - y(r.amount))
      .attr("fill", r => r.basis === "in-house report" ? css("--c4") : css("--c2")).attr("rx", 1)
      .on("mousemove", (ev, r) => showTip(ev, `<b>${r.year} Q${r.quarter}</b><br>${money(r.amount)} · ${esc(r.basis)}${r.firms?.length ? `<br><span class="faint">${r.firms.length} outside firm${r.firms.length === 1 ? "" : "s"}</span>` : ""}<br><span class="faint">${r.reports.length} report${r.reports.length === 1 ? "" : "s"}</span>`))
      .on("mouseleave", () => { tip.hidden = true; });
    if (years.length) host.append(el("div", { class: "ad-legend" }, el("span", {}, el("i", { style: `background:${css("--c4")}` }), "Company's own LD-2 report"), el("span", {}, el("i", { style: `background:${css("--c2")}` }), "Outside firms only")));
  }

  /* ---------- contracts ---------- */
  function contractChart(host, rows) {
    const data = rows.filter(r => r.fy);
    const W = host.clientWidth || 520, H = 200, m = { t: 12, r: 12, b: 24, l: 52 };
    const svg = svgFor(host, H, "Federal award obligations by fiscal year");
    const x = d3.scaleBand().domain(data.map(r => String(r.fy))).range([m.l, W - m.r]).padding(.3);
    const y = d3.scaleLinear().domain([0, d3.max(data, r => r.amount) || 1]).nice().range([H - m.b, m.t]);
    svg.append("g").attr("transform", `translate(${m.l},0)`).call(d3.axisLeft(y).ticks(4).tickFormat(money).tickSize(-(W - m.l - m.r)))
      .call(g => { g.select(".domain").remove(); g.selectAll("line").attr("stroke", css("--line")).attr("stroke-opacity", .6); g.selectAll("text").attr("fill", css("--muted")); });
    svg.append("g").attr("transform", `translate(0,${H - m.b})`).call(d3.axisBottom(x).tickFormat(f => "FY" + f.slice(2)).tickSizeOuter(0))
      .call(g => { g.selectAll("text").attr("fill", css("--muted")); g.select(".domain").attr("stroke", css("--line")); });
    svg.append("g").selectAll("rect").data(data).join("rect")
      .attr("x", r => x(String(r.fy))).attr("width", x.bandwidth()).attr("y", r => y(Math.max(0, r.amount))).attr("height", r => Math.max(0, y(0) - y(Math.max(0, r.amount))))
      .attr("fill", css("--c1")).attr("fill-opacity", r => r.partial ? .45 : 1).attr("rx", 1)
      .on("mousemove", (ev, r) => showTip(ev, `<b>FY${r.fy}</b>${r.partial ? " (in progress)" : ""}<br>${money(r.amount)} obligated<br><span class="faint">Oct 1, ${r.fy - 1} – Sep 30, ${r.fy}</span>`))
      .on("mouseleave", () => { tip.hidden = true; });
  }

  /* ---------- tab ---------- */
  function render(body, tk) {
    const host = el("div", { class: "ad-tab" }, el("div", { class: "muted", style: "padding:16px 4px" }, "Loading alternative data…"));
    body.append(host);
    load(tk).then(d => { host.innerHTML = ""; draw(host, tk, d); })
      .catch(() => { host.innerHTML = ""; host.append(panel("Alternative data", null, el("div", { class: "bd muted" }, "Alternative data for this company is still being collected. Check back after the next daily update."))); });
  }

  function draw(host, tk, d) {
    const sv = shortSeries(d), last = sv[sv.length - 1];
    const ma20 = last?.ma, y1 = sv.length ? sv.reduce((s, r) => s + r.short, 0) / sv.reduce((s, r) => s + r.total, 0) : null;
    const ftd = d.ftd?.rows || [], lastF = ftd[ftd.length - 1];
    const ftdCut = ftd.length ? pd(ftd[ftd.length - 1][0]) - 90 * 864e5 : 0;
    const ftd90 = ftd.filter(r => pd(r[0]) >= ftdCut);
    const lob = d.lobbying?.rows || [], last4 = lob.slice(-4), lob4 = last4.reduce((s, r) => s + r.amount, 0);
    const prev4 = lob.slice(-8, -4).reduce((s, r) => s + r.amount, 0);
    const con = (d.contracts?.rows || []).filter(r => !r.partial), lastFY = con[con.length - 1], prevFY = con[con.length - 2];
    host.append(el("div", { class: "stats co-stats ad-stats", role: "group", "aria-label": "Alternative data summary" },
      stat("Short volume, latest day", last ? pct(last.ratio) : "–", last ? `${fdate(last.date)} · ${qty(last.total)} sh` : "no FINRA data"),
      stat("Short volume, 20 days", pct(ma20), ma20 != null && y1 != null ? `${pp(ma20 - y1)} vs 1-year` : ""),
      stat("Fails to deliver, latest", lastF ? qty(lastF[1]) : "–", lastF ? `settled ${fdate(lastF[0])}` : "none reported"),
      stat("Fails, peak in 90 days", ftd90.length ? qty(Math.max(...ftd90.map(r => r[1]))) : "–", ftd90.length ? `${ftd90.length} days with fails` : ""),
      stat("Lobbying, last 4 quarters", last4.length ? money(lob4) : "–", last4.length ? prev4 ? `prior 4 quarters ${money(prev4)}` : `${last4[0].year} Q${last4[0].quarter} – ${last4[last4.length - 1].year} Q${last4[last4.length - 1].quarter}` : "no matching reports"),
      stat(lastFY ? `Federal awards, FY${lastFY.fy}` : "Federal awards", lastFY ? money(lastFY.amount) : "–", lastFY && prevFY && prevFY.amount > 0 ? `${lastFY.amount >= prevFY.amount ? "+" : "−"}${Math.abs(100 * (lastFY.amount / prevFY.amount - 1)).toFixed(0)}% vs FY${prevFY.fy}` : lastFY ? "obligations, all award types" : "no matching recipients")));

    // short volume
    let span = String(store.get("adSpan", "365"));
    if (!["90", "182", "365"].includes(span)) span = "365";
    const sHost = el("div", { class: "bd" });
    host.append(panel("Off-exchange short-sale volume", seg([["90", "3M"], ["182", "6M"], ["365", "1Y"]], span, v => { span = v; store.set("adSpan", span); shortChart(sHost, sv, +span); }),
      el("div", { class: "ad-key" }, el("span", {}, el("i", { style: `background:${css("--c1")}` }), "20-day ratio"), el("span", {}, el("i", { style: `background:${css("--c6")}` }), "Daily ratio")),
      sHost,
      note("Short-sale volume divided by total volume for trades reported to FINRA's off-exchange facilities (ATSs and internalizers). It counts market makers' hedging sales and is not short interest. Source: ",
        link(d.short_volume?.url || "https://www.finra.org/finra-data/browse-catalog/short-sale-volume-data/daily-short-sale-volume-files", "FINRA Reg SHO daily files"), `, ${sv.length} trading days.`)));
    requestAnimationFrame(() => shortChart(sHost, sv, +span));

    // fails to deliver
    const fHost = el("div", { class: "bd" });
    const big = [...ftd].sort((a, b) => b[1] - a[1]).slice(0, 5);
    const ft = el("table", { class: "t ad-table" }, el("thead", {}, el("tr", {}, el("th", {}, "Settlement date"), el("th", { class: "r" }, "Shares failed"), el("th", { class: "r" }, "Price"), el("th", { class: "r" }, "Value"))),
      el("tbody", {}, ...(big.length ? big.map(r => el("tr", {}, el("td", { class: "num" }, fdate(r[0])), el("td", { class: "r num" }, Math.round(r[1]).toLocaleString("en-US")), el("td", { class: "r num" }, r[2] ? "$" + fnum(r[2]) : "–"), el("td", { class: "r num" }, r[2] ? money(r[1] * r[2]) : "–")))
        : [el("tr", {}, el("td", { colspan: 4, class: "muted" }, "No fails reported."))])));
    const fg = el("div", { class: "grid g64" },
      panel("Fails to deliver", el("span", { class: "faint ad-hd-note" }, d.ftd?.through ? `SEC files through ${d.ftd.through.slice(0, 4)}-${d.ftd.through.slice(4, 6)} (${d.ftd.through.endsWith("a") ? "first" : "second"} half)` : ""), fHost,
        note("Aggregate net balance of shares that failed to settle on each date, across all clearing members. A fail can come from either side of a trade and is not by itself evidence of naked shorting. The SEC publishes these files twice a month with a lag. Source: ", link(d.ftd?.url || "https://www.sec.gov/data-research/sec-markets-data/fails-deliver-data", "SEC fails-to-deliver data"), ".")),
      panel("Largest fails", null, el("div", { class: "scroll" }, ft)));
    host.append(fg);
    requestAnimationFrame(() => ftdChart(fHost, ftd));

    // lobbying and contracts
    const g = el("div", { class: "grid g2" });
    const lHost = el("div", { class: "bd" });
    const L = d.lobbying;
    const lobBody = [];
    if (lob.length) {
      lobBody.push(lHost);
      const byYear = {};
      lob.forEach(r => { const y = byYear[r.year] || (byYear[r.year] = { amt: 0, q: 0, firms: new Set(), reports: [] }); y.amt += r.amount; y.q++; (r.firms || []).forEach(f => y.firms.add(f)); y.reports.push(...r.reports); });
      const yt = el("table", { class: "t ad-table" }, el("thead", {}, el("tr", {}, el("th", {}, "Year"), el("th", { class: "r" }, "Spend"), el("th", { class: "r" }, "Outside firms"), el("th", {}, "Reports"))),
        el("tbody", {}, ...Object.keys(byYear).sort().reverse().slice(0, 6).map(yk => {
          const y = byYear[yk], first = y.reports.find(r => r.url);
          return el("tr", {}, el("td", { class: "num" }, yk + (y.q < 4 ? ` (${y.q}Q)` : "")), el("td", { class: "r num" }, money(y.amt)), el("td", { class: "r num" }, String(y.firms.size)),
            el("td", {}, first ? link(first.url, `${y.reports.length} on lda.gov ↗`) : String(y.reports.length)));
        })));
      lobBody.push(el("div", { class: "scroll" }, yt));
      requestAnimationFrame(() => lobbyChart(lHost, lob));
    } else lobBody.push(el("div", { class: "bd muted" }, L ? "No Lobbying Disclosure Act reports name this company as a client." : "Lobbying reports are still being collected."));
    lobBody.push(note("Quarterly spend reported under the Lobbying Disclosure Act. Uses the company's own LD-2 expense report when it files one (which includes fees paid to outside firms); otherwise the sum of outside firms' reported income. Amendments replace the report they amend. ",
      L?.client_names?.length ? `Client names matched: ${L.client_names.slice(0, 6).join("; ")}${L.client_names.length > 6 ? "…" : ""}. ` : "",
      "Source: ", link(d.lobbying_source?.url || "https://lda.gov/filings/public/filing/search/", "lda.gov"), "."));
    g.append(panel("Federal lobbying", null, ...lobBody));

    const C = d.contracts;
    const cHost = el("div", { class: "bd" });
    const conBody = [];
    const anyAward = (C?.rows || []).some(r => Math.abs(r.amount) >= 1);
    if (anyAward) {
      conBody.push(cHost);
      requestAnimationFrame(() => contractChart(cHost, C.rows));
      if (C.awards?.length) {
        const at = el("table", { class: "t ad-table ad-awards" }, el("thead", {}, el("tr", {}, el("th", {}, "Award"), el("th", {}, "Agency"), el("th", { class: "r" }, "Award amount"))),
          el("tbody", {}, ...C.awards.slice(0, 6).map(a => el("tr", {},
            el("td", { title: a.description || "" }, el("div", { class: "ad-clip" }, a.url ? link(a.url, a.id + " ↗") : a.id), el("div", { class: "ad-clip faint" }, a.description || a.recipient || "")),
            el("td", { title: `${a.sub_agency || a.agency || ""} · ${a.recipient || ""}` }, el("div", { class: "ad-clip" }, a.sub_agency || a.agency || "–"), el("div", { class: "ad-clip faint" }, a.recipient || "")),
            el("td", { class: "r num" }, money(a.amount))))));
        conBody.push(el("div", { class: "ad-sub" }, "Largest contract awards active in the last 12 months"), el("div", { class: "scroll" }, at));
      }
    } else conBody.push(el("div", { class: "bd muted" }, C ? "No federal prime awards to entities registered under this company's legal name." : "Federal award data is still being collected."));
    conBody.push(note("Obligations by federal fiscal year (October–September) to the company's registered parent entity and the subsidiaries linked to it in SAM.gov, all prime award types. The current fiscal year is shaded while in progress. ",
      C?.entities?.length ? `Entities: ${C.entities.slice(0, 5).join("; ")}${C.entities.length > 5 ? ` and ${C.entities.length - 5} more` : ""}. ` : "",
      "Source: ", link(d.contracts_source?.url || "https://www.usaspending.gov/search", "USAspending.gov"), "."));
    g.append(panel("Federal contracts and awards", null, ...conBody));
    host.append(g);
    host.append(el("div", { class: "ad-foot" }, `Updated ${relTime(d.updated_at)}. Market files refresh each trading day; lobbying and federal awards refresh weekly.`));
  }

  window.FU_COTABS = window.FU_COTABS || [];
  window.FU_COTABS.push({ id: "altdata", name: "Alt data", after: "insiders", render });
})();
