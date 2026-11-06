// Estimates & guidance tab on the company page: consensus by period (Yahoo Finance), revision history from our own
// daily snapshots, earnings surprises (provider basis, with SEC GAAP EPS alongside), analyst actions, price targets and
// ratings (Benzinga, "Powered by Benzinga Data"), and company guidance with verbatim quotes (IR press releases).
// Data: estimates/{T}.json, estimates/firms.json (etl/estimates.py). Every value opens its source in the right drawer.
// Registers itself in window.FU_COTABS after the Financials tab.
(function () {
  "use strict";
  const CACHE = {};
  let FIRMS = null;
  const load = tk => CACHE[tk] || (CACHE[tk] = getJSON(`estimates/${tk}.json`).catch(e => { delete CACHE[tk]; throw e; }));
  const loadFirms = () => FIRMS || (FIRMS = getJSON("estimates/firms.json").then(f => new Map((f.firms || []).map(x => [x.id, x]))).catch(() => new Map()));
  const BZ = "Powered by Benzinga Data";
  const KIND_LABEL = { upgrade: "Upgrade", downgrade: "Downgrade", initiate: "Initiated", target: "Target change", reiterate: "Reiterated", drop: "Coverage dropped" };
  const INACTIVE = new Set(["Terminates Coverage On", "Suspends", "Removes", "Firm Dissolved"]);
  const PERIOD = { "0q": "Current qtr", "+1q": "Next qtr", "0y": "Current FY", "+1y": "Next FY" };

  /* ---------- formatting ---------- */
  const eps = v => v == null || !isFinite(v) ? "–" : (v < 0 ? "−$" : "$") + Math.abs(v).toFixed(2);
  const usd = v => v == null || !isFinite(v) ? "–" : fmtMoney(v);
  const px = v => v == null || !isFinite(v) ? "–" : "$" + fnum(v, v >= 1000 ? 0 : 2);
  const pctS = (v, d = 1) => v == null || !isFinite(v) ? "–" : fsgn(v, d);
  const cnt = v => v == null || !isFinite(v) ? "–" : String(Math.round(v));
  const ago = n => new Date(Date.now() - n * 864e5).toISOString().slice(0, 10);
  const link = (href, text) => href ? el("a", { href, target: "_blank", rel: "noopener" }, text) : text;
  const note = (...kids) => el("div", { class: "src" }, ...kids);
  const btn = (text, onclick, title, extra = "") => el("button", { class: "es-v " + extra, onclick: e => { e.stopPropagation(); onclick(); }, title: title || "Open source" }, text);

  function kind(a) {
    if (a.action === "Upgrades") return "upgrade";
    if (a.action === "Downgrades") return "downgrade";
    if (["Initiates Coverage On", "Reinstates", "Assumes"].includes(a.action)) return "initiate";
    if (INACTIVE.has(a.action)) return "drop";
    if (a.pt_current != null && a.pt_prior != null && a.pt_current !== a.pt_prior) return "target";
    return "reiterate";
  }
  function bucket(r) {
    const k = String(r || "").trim().toLowerCase();
    if (!k) return null;
    if (/strong buy|^buy|outperform|overweight|positive|accumulate|top pick|^add$|speculative buy|conviction/.test(k)) return "buy";
    if (/underperform|underweight|^sell|strong sell|reduce|negative/.test(k)) return "sell";
    if (/hold|neutral|equal|perform|in-line|inline|weight|mixed|fair value|average|cautious/.test(k)) return "hold";
    return "other";
  }

  /* ---------- firm logos (marks only; analyst portraits are never shown) ---------- */
  function firmLogo(a, size = 18) {
    const f = FIRMS_MAP?.get(a.firm_id);
    const initials = String(a.firm || "?").replace(/[^A-Za-z& ]/g, " ").split(/\s+/).filter(w => w && w !== "&").slice(0, 2).map(w => w[0]).join("").toUpperCase() || "?";
    const mono = () => el("span", { class: "es-logo mono", style: `width:${size}px;height:${size}px;font-size:${Math.round(size * .42)}px`, "aria-hidden": "true" }, initials);
    if (!f?.logo) return mono();
    const im = el("img", { class: "es-logo", src: PUB + f.logo, alt: "", width: size, height: size, loading: "lazy", decoding: "async" });
    im.onerror = () => im.replaceWith(mono());
    return im;
  }
  let FIRMS_MAP = null;

  /* ---------- source drawer for third-party records ---------- */
  // Renders into the shared right-hand drawer (#drawer/#dh/#db) used by openSource/openDoc.
  function openRecord(spec) {
    hideTip();
    const wasOpen = drawer.classList.contains("open");
    if (!wasOpen) { const active = document.activeElement; DR.trigger?.classList.remove("src-active"); DR.trigger = active && active !== document.body && !drawer.contains(active) ? active : null; DR.trigger?.classList.add("src-active"); }
    DR.cell = { record: spec.id || spec.title };
    const dh = $("#dh"), db = $("#db");
    dh.innerHTML = ""; db.innerHTML = "";
    dh.append(
      el("div", { class: "row" }, el("span", { class: "eyebrow" }, spec.eyebrow || "Third-party data"),
        el("div", { class: "x", style: "display:flex;gap:6px" },
          spec.url ? el("a", { class: "btn", href: spec.url, target: "_blank", rel: "noopener" }, spec.urlLabel || "Provider page", svgUse("i-ext")) : null,
          el("button", { class: "iconbtn", onclick: closeSource, "aria-label": "Close source (Esc)" }, svgUse("i-x")))),
      el("div", { class: "row" }, el("h2", {}, spec.title)),
      el("div", { class: "row" }, spec.value ? el("span", { class: "chip acc mono", style: "font-size:13.5px;height:26px" }, spec.value) : null,
        el("span", { class: "muted", style: "font-size:12.5px" }, spec.sub || "")));
    const prov = el("div", { class: "note es-prov" },
      el("b", {}, spec.provider || "Provider"), spec.attribution ? el("span", { class: "es-attr" }, " · " + spec.attribution) : null,
      el("div", {}, spec.retrieved ? `Retrieved ${spec.retrieved.replace("T", " ").replace("Z", " UTC")}` : "", spec.via ? ` via ${spec.via}` : ""),
      spec.caveat ? el("div", { class: "es-caveat" }, spec.caveat) : null);
    db.append(prov);
    const t = el("table", { class: "t es-rec" });
    const tb = el("tbody");
    let hit = null;
    (spec.rows || []).forEach(([k, v, hl]) => {
      const tr = el("tr", { class: hl ? "es-hit" : "" }, el("th", {}, k), el("td", { class: "mono" }, v == null || v === "" ? "–" : v));
      if (hl && !hit) hit = tr;
      tb.append(tr);
    });
    t.append(tb);
    db.append(el("div", { class: "es-rec-wrap" }, t));
    if (spec.extra) db.append(spec.extra);
    if (spec.raw) db.append(el("details", { class: "es-raw" }, el("summary", {}, "Stored record (JSON)"), el("pre", {}, JSON.stringify(spec.raw, null, 2))));
    drawer.classList.add("open"); drawer.setAttribute("aria-hidden", "false");
    if (innerWidth < 860) scrim.hidden = false;
    db.scrollTop = 0;
    if (hit) requestAnimationFrame(() => hit.scrollIntoView({ block: "center" }));
    if (!wasOpen) { const h = dh.querySelector("h2"); h?.setAttribute("tabindex", "-1"); h?.focus({ preventScroll: true }); }
  }

  function yahooSpec(d, title, value, sub, rows, raw) {
    const s = d.sources?.yahoo || {};
    return { eyebrow: "Consensus estimate · third-party", title, value, sub, rows, raw, provider: "Yahoo Finance", url: d.consensus?.url,
      urlLabel: "Yahoo Finance analysis", retrieved: d.consensus?.as_of || s.retrieved_at, via: "Firecrawl Alexandria (finance-yahoo-com/equities/earnings)",
      caveat: "Compiled by Yahoo Finance from sell-side analysts. Not from company filings. Basis (GAAP or adjusted) is the provider's; consensus EPS is generally on the adjusted basis analysts forecast." };
  }
  function actionSpec(d, a) {
    const k = kind(a);
    return { eyebrow: "Analyst action · " + BZ, id: a.id, title: `${a.firm}${a.analyst ? " · " + a.analyst : ""}`, value: a.rating_current || KIND_LABEL[k],
      sub: `${fdate(a.date)}${a.time ? " " + a.time + " ET" : ""} · ${a.action}${a.action_pt ? " · target " + a.action_pt.toLowerCase() : ""}`,
      provider: "Benzinga", attribution: BZ, url: a.url, urlLabel: "Benzinga ratings page", retrieved: d.sources?.benzinga?.retrieved_at, via: "Firecrawl Alexandria (benzinga/calendar/ratings)",
      rows: [["Date", a.date], ["Firm", a.firm], ["Analyst", a.analyst], ["Action", a.action, true], ["Price target action", a.action_pt],
        ["Rating, prior", a.rating_prior], ["Rating, current", a.rating_current, true], ["Price target, prior", a.pt_prior_printed], ["Price target, current", a.pt_current_printed, true],
        ["Split-adjusted target, prior", a.adj_pt_prior], ["Split-adjusted target, current", a.adj_pt_current], ["Currency", a.currency], ["Benzinga id", a.id]],
      extra: a.url_news ? el("div", { class: "src", style: "padding:10px 0" }, "Related coverage: ", link(a.url_news, "Benzinga analyst ratings news ↗")) : null,
      raw: a };
  }

  /* ---------- charts ---------- */
  const svgFor = (host, H, label) => d3.select(host).append("svg").attr("width", host.clientWidth || 640).attr("height", H).attr("role", "img").attr("aria-label", label);
  function axisStyle(g) { g.selectAll("text").attr("fill", css("--muted")).attr("font-size", 10.5); g.select(".domain").attr("stroke", css("--line")); g.selectAll("line").attr("stroke", css("--line")); }

  function revisionChart(host, d) {
    host.innerHTML = "";
    const snaps = d.snapshots || [];
    const codes = ["0q", "+1q", "0y", "+1y"].filter(c => snaps.some(s => s.p[c]?.eps != null));
    if (snaps.length < 2) {
      host.append(el("div", { class: "es-empty" }, el("b", {}, "Revision history is accumulating."), ` ${snaps.length} daily snapshot${snaps.length === 1 ? "" : "s"} stored since ${snaps[0] ? fdate(snaps[0].as_of) : "the first refresh"}. Yahoo's feed has no estimate trend, so changes are measured only against our own stored snapshots of the same fiscal period. The chart fills in as the refresh job runs.`));
      return;
    }
    const W = host.clientWidth || 640, H = 190, m = { t: 10, r: 12, b: 22, l: 46 };
    const svg = svgFor(host, H, "Consensus EPS over time");
    const series = codes.map((c, i) => ({ c, color: css(`--c${i + 1}`), pts: snaps.filter(s => s.p[c]?.eps != null).map(s => ({ t: pd(s.as_of), v: s.p[c].eps, end: s.p[c].end, s })) }));
    const all = series.flatMap(s => s.pts);
    const x = d3.scaleUtc().domain(d3.extent(all, p => p.t)).range([m.l, W - m.r]);
    const y = d3.scaleLinear().domain(d3.extent(all, p => p.v)).nice().range([H - m.b, m.t]);
    svg.append("g").attr("transform", `translate(0,${H - m.b})`).call(d3.axisBottom(x).ticks(5).tickSizeOuter(0)).call(axisStyle);
    svg.append("g").attr("transform", `translate(${m.l},0)`).call(d3.axisLeft(y).ticks(4).tickFormat(v => "$" + v.toFixed(2)).tickSize(-(W - m.l - m.r))).call(g => { g.select(".domain").remove(); g.selectAll("line").attr("stroke", css("--line")).attr("stroke-opacity", .6); g.selectAll("text").attr("fill", css("--muted")).attr("font-size", 10.5); });
    for (const s of series) {
      svg.append("path").datum(s.pts).attr("fill", "none").attr("stroke", s.color).attr("stroke-width", 1.6).attr("d", d3.line().x(p => x(p.t)).y(p => y(p.v)));
      svg.append("g").selectAll("circle").data(s.pts).join("circle").attr("cx", p => x(p.t)).attr("cy", p => y(p.v)).attr("r", 2.6).attr("fill", s.color).style("cursor", "pointer")
        .on("mousemove", (ev, p) => showTip(ev, `<b>${PERIOD[s.c]} · FY period ending ${fdate(p.end)}</b><br>${eps(p.v)} consensus EPS<br><span class="faint">snapshot ${fdate(p.s.as_of)} · ${cnt(p.s.p[s.c].eps_n)} analysts</span>`))
        .on("mouseleave", hideTip)
        .on("click", (ev, p) => openRecord(yahooSpec(d, `${PERIOD[s.c]} EPS consensus · snapshot`, eps(p.v), `Snapshot ${fdate(p.s.as_of)} · period ending ${fdate(p.end)}`,
          [["Snapshot date", p.s.as_of], ["Period", s.c], ["Fiscal period end", p.end], ["Consensus EPS", eps(p.v), true], ["Analysts", cnt(p.s.p[s.c].eps_n)], ["Low", eps(p.s.p[s.c].eps_lo)], ["High", eps(p.s.p[s.c].eps_hi)], ["Consensus revenue", usd(p.s.p[s.c].rev)]], p.s)));
    }
    host.append(el("div", { class: "es-key" }, ...series.map(s => el("span", {}, el("i", { style: `background:${s.color}` }), PERIOD[s.c]))));
  }

  function surpriseChart(host, d, rows) {
    host.innerHTML = "";
    const data = rows.filter(r => r.surprise_pct != null);
    if (!data.length) { host.append(el("div", { class: "es-empty" }, "No surprise history from the provider.")); return; }
    const W = host.clientWidth || 420, H = 150, m = { t: 10, r: 8, b: 22, l: 40 };
    const svg = svgFor(host, H, "EPS surprise by quarter");
    const x = d3.scaleBand().domain(data.map(r => r.quarter_end)).range([m.l, W - m.r]).padding(.35);
    const ext = d3.extent([0, ...data.map(r => r.surprise_pct)]);
    const y = d3.scaleLinear().domain(ext).nice().range([H - m.b, m.t]);
    svg.append("g").attr("transform", `translate(0,${H - m.b})`).call(d3.axisBottom(x).tickFormat(q => { const t = pd(q); return MONTHS[t.getUTCMonth()] + " '" + String(t.getUTCFullYear()).slice(2); }).tickSizeOuter(0)).call(axisStyle);
    svg.append("g").attr("transform", `translate(${m.l},0)`).call(d3.axisLeft(y).ticks(4).tickFormat(v => (v * 100).toFixed(0) + "%").tickSize(-(W - m.l - m.r))).call(g => { g.select(".domain").remove(); g.selectAll("line").attr("stroke", css("--line")).attr("stroke-opacity", .6); g.selectAll("text").attr("fill", css("--muted")).attr("font-size", 10.5); });
    svg.append("g").selectAll("rect").data(data).join("rect").attr("x", r => x(r.quarter_end)).attr("width", x.bandwidth())
      .attr("y", r => y(Math.max(0, r.surprise_pct))).attr("height", r => Math.abs(y(r.surprise_pct) - y(0))).attr("rx", 2)
      .attr("fill", r => r.surprise_pct >= 0 ? css("--up") : css("--down")).style("cursor", "pointer")
      .on("mousemove", (ev, r) => showTip(ev, `<b>Quarter ending ${fdate(r.quarter_end)}</b><br>Reported ${eps(r.eps_actual)} vs consensus ${eps(r.eps_estimate)}<br>Surprise ${pctS(r.surprise_pct)}${r.reaction ? `<br>Next session ${pctS(r.reaction.r1)}` : ""}<br><span class="faint">Yahoo Finance basis</span>`))
      .on("mouseleave", hideTip).on("click", (ev, r) => openSurprise(d, r, "surprise_pct"));
  }

  function targetChart(host, d) {
    host.innerHTML = "";
    const T = d.targets || {}, rows = T.rows || [], price = d.price?.close;
    if (!rows.length) { host.append(el("div", { class: "es-empty" }, "No firm-level price targets in the past year.")); return; }
    const W = host.clientWidth || 420, H = 104, m = { t: 26, r: 14, b: 22, l: 14 };
    const svg = svgFor(host, H, "Price targets by firm against the current price");
    const vals = rows.map(r => r.pt).concat(price ? [price] : []);
    const x = d3.scaleLinear().domain(d3.extent(vals)).nice().range([m.l, W - m.r]);
    svg.append("g").attr("transform", `translate(0,${H - m.b})`).call(d3.axisBottom(x).ticks(6).tickFormat(v => "$" + d3.format(",")(v)).tickSizeOuter(0)).call(axisStyle);
    const yy = H - m.b - 18;
    const s = T.stats;
    if (s) svg.append("rect").attr("x", x(s.low)).attr("width", Math.max(1, x(s.high) - x(s.low))).attr("y", yy - 5).attr("height", 10).attr("rx", 5).attr("fill", css("--accent")).attr("fill-opacity", .12);
    const jitter = new Map();
    svg.append("g").selectAll("circle").data(rows).join("circle").attr("cx", r => x(r.pt)).attr("cy", r => { const k = Math.round(x(r.pt) / 6); const n = jitter.get(k) || 0; jitter.set(k, n + 1); return yy + (n % 2 ? 1 : -1) * Math.min(12, Math.ceil(n / 2) * 4); })
      .attr("r", 4).attr("fill", r => r.bucket === "buy" ? css("--up") : r.bucket === "sell" ? css("--down") : css("--muted")).attr("fill-opacity", .8).attr("stroke", css("--surface")).style("cursor", "pointer")
      .on("mousemove", (ev, r) => showTip(ev, `<b>${esc(r.firm)}</b>${r.analyst ? " · " + esc(r.analyst) : ""}<br>Target ${px(r.pt)}${r.adjusted ? " (split-adjusted)" : ""} · ${esc(r.rating || "")}<br><span class="faint">${fdate(r.date)}</span>`))
      .on("mouseleave", hideTip).on("click", (ev, r) => { const a = d.actions.find(x => x.id === r.id); if (a) openRecord(actionSpec(d, a)); });
    const mark = (v, label, color, up) => { if (v == null) return; const xx = x(v), anchor = xx < W * .2 ? "start" : xx > W * .8 ? "end" : "middle"; svg.append("line").attr("x1", xx).attr("x2", xx).attr("y1", up ? m.t - 4 : m.t + 6).attr("y2", H - m.b).attr("stroke", color).attr("stroke-width", 1.5).attr("stroke-dasharray", up ? null : "3 2"); svg.append("text").attr("x", xx).attr("y", up ? m.t - 8 : m.t + 2).attr("text-anchor", anchor).attr("font-size", 10.5).attr("fill", color).text(label); };
    mark(price, `Price ${px(price)}`, css("--ink"), true);
    if (s) mark(s.mean, `Mean ${px(s.mean)}`, css("--accent"));
  }

  function ratingBar(d) {
    const R = d.ratings || {}, c = R.counts || {}, n = (c.buy || 0) + (c.hold || 0) + (c.sell || 0) + (c.other || 0);
    if (!n) return el("div", { class: "es-empty" }, "No current ratings in the past year.");
    const seg = (k, label, color) => c[k] ? el("button", { class: "es-rseg", style: `flex:${c[k]};background:${color}`, title: `${label}: ${c[k]} of ${n} firms`, onclick: () => openRatings(d, k) }, c[k] >= 2 || n < 12 ? String(c[k]) : "") : null;
    return el("div", {}, el("div", { class: "es-rbar" }, seg("buy", "Buy", css("--up")), seg("hold", "Hold", css("--muted")), seg("sell", "Sell", css("--down")), seg("other", "Other", css("--c5"))),
      el("div", { class: "es-key" }, ...[["buy", "Buy", "--up"], ["hold", "Hold", "--muted"], ["sell", "Sell", "--down"]].map(([k, l, v]) => el("span", {}, el("i", { style: `background:${css(v)}` }), `${l} ${c[k] || 0}`)), el("span", { class: "faint" }, `${n} firms, latest rating since ${fdate(R.since)}`)));
  }

  function ratingHistory(host, d) {
    host.innerHTML = "";
    const H0 = (d.rating_history || []).filter(r => r.n);
    if (H0.length < 2) return;
    const W = host.clientWidth || 420, H = 96, m = { t: 6, r: 6, b: 18, l: 24 };
    const svg = svgFor(host, H, "Rating distribution by month");
    const x = d3.scaleBand().domain(H0.map(r => r.month)).range([m.l, W - m.r]).padding(.2);
    const y = d3.scaleLinear().domain([0, d3.max(H0, r => r.n)]).nice().range([H - m.b, m.t]);
    svg.append("g").attr("transform", `translate(0,${H - m.b})`).call(d3.axisBottom(x).tickValues(H0.filter((r, i) => i % 3 === H0.length % 3 - 1 || i === H0.length - 1).map(r => r.month)).tickFormat(mo => MONTHS[+mo.slice(5) - 1] + " '" + mo.slice(2, 4)).tickSizeOuter(0)).call(axisStyle);
    svg.append("g").attr("transform", `translate(${m.l},0)`).call(d3.axisLeft(y).ticks(3).tickSize(0)).call(g => { g.select(".domain").remove(); g.selectAll("text").attr("fill", css("--muted")).attr("font-size", 10); });
    const keys = [["sell", "--down"], ["hold", "--muted"], ["buy", "--up"]];
    const stack = d3.stack().keys(keys.map(k => k[0]))(H0);
    stack.forEach((layer, i) => svg.append("g").attr("fill", css(keys[i][1])).attr("fill-opacity", .85).selectAll("rect").data(layer).join("rect")
      .attr("x", p => x(p.data.month)).attr("width", x.bandwidth()).attr("y", p => y(p[1])).attr("height", p => y(p[0]) - y(p[1]))
      .on("mousemove", (ev, p) => showTip(ev, `<b>${p.data.month}</b><br>Buy ${p.data.buy} · Hold ${p.data.hold} · Sell ${p.data.sell}<br><span class="faint">latest rating per firm, trailing 12 months</span>`)).on("mouseleave", hideTip));
  }

  /* ---------- drawers for derived views ---------- */
  function openRatings(d, k) {
    const R = d.ratings, rows = (R.firms || []).filter(f => !k || f.bucket === k);
    openRecord({ eyebrow: "Rating distribution · " + BZ, title: `${({ buy: "Buy", hold: "Hold", sell: "Sell", other: "Other" })[k] || "All"} ratings`, value: `${rows.length} of ${R.n} firms`,
      sub: `Latest rating per firm since ${fdate(R.since)}; firms that dropped coverage are excluded`, provider: "Benzinga", attribution: BZ, retrieved: d.sources?.benzinga?.retrieved_at,
      via: "Calculated by Filed Under from Benzinga calendar/ratings records", rows: rows.map(f => [f.firm, `${f.rating} · ${f.date}`]),
      caveat: "Rating labels are each firm's own scale, grouped into buy, hold and sell by Filed Under." });
  }
  function openTargets(d, field) {
    const T = d.targets, s = T.stats || {};
    openRecord({ eyebrow: "Price targets · " + BZ, title: "Price target distribution", value: px(s[field] ?? s.mean), sub: `${s.n} firms, latest target per firm since ${fdate(T.since)}`,
      provider: "Benzinga", attribution: BZ, retrieved: d.sources?.benzinga?.retrieved_at, via: "Calculated by Filed Under from Benzinga calendar/ratings records",
      rows: [["Mean", px(s.mean), field === "mean"], ["Median", px(s.median), field === "median"], ["High", px(s.high), field === "high"], ["Low", px(s.low), field === "low"],
        ["Current price", `${px(d.price?.close)} (close ${d.price?.date || "–"}, Filed Under prices)`], ["Upside to mean", pctS(s.upside_mean), field === "upside_mean"],
        ...T.rows.map(r => [r.firm + (r.analyst ? " · " + r.analyst : ""), `${px(r.pt)}${r.adjusted ? " adj." : ""} · ${r.date}`])],
      caveat: "Uses Benzinga's split-adjusted target where it differs from the printed target, so targets are comparable with today's price." });
  }
  function openSurprise(d, r, field) {
    const rows = [["Quarter end (provider)", r.quarter_end], ["Consensus EPS", eps(r.eps_estimate), field === "eps_estimate"], ["Reported EPS (provider)", eps(r.eps_actual), field === "eps_actual"],
      ["Difference", r.eps_difference], ["Surprise", pctS(r.surprise_pct), field === "surprise_pct"], ["Basis", "Provider's reported EPS vs provider's consensus (generally adjusted)"],
      ["SEC diluted EPS (GAAP)", r.gaap_eps ? `${eps(r.gaap_eps.v)} · ${r.gaap_eps.form} filed ${r.gaap_eps.filed}` : "Not reported as a quarter in filings"],
      ["Earnings 8-K", r.report ? `${r.report.filed} · ${r.report.accn}` : "–"],
      ["Next-session move", r.reaction ? `${pctS(r.reaction.r1)} on ${r.reaction.date} (SPY ${pctS(r.reaction.spy1)})` : "–", field === "reaction"]];
    openRecord({ ...yahooSpec(d, `EPS surprise · quarter ending ${fdate(r.quarter_end)}`, pctS(r.surprise_pct), "Yahoo Finance basis; GAAP figure from SEC shown for reference", rows, r),
      extra: el("div", { class: "src", style: "padding:10px 0;display:flex;gap:12px;flex-wrap:wrap" }, r.report?.url ? link(r.report.url, "Earnings 8-K on EDGAR ↗") : null, r.gaap_eps?.url ? link(r.gaap_eps.url, `${r.gaap_eps.form} filing ↗`) : null) });
  }
  function openSec(tk, label, end, fallback) {
    try {
      const cell = model(tk).get(label, "Q", end);
      if (cell) return openSource(cell);
    } catch { /* company model not loaded */ }
    if (fallback) window.open(fallback, "_blank", "noopener");
  }
  function openGuide(tk, d, g) {
    const doc = d.guidance?.docs?.[g.doc];
    if (!doc) return openRecord({ eyebrow: "Company guidance", title: g.label, value: rangeText(g.low ?? g.guide_low, g.high ?? g.guide_high, g.unit), rows: [["Quote", g.quote, true]], provider: "Filed Under guidance extraction" });
    openDoc(tk, doc, { quote: g.quote, printed: g.printed?.low || g.printed?.point || null, page: g.page, label: `${g.label} guidance · ${g.period}`, display: rangeText(g.low ?? g.guide_low, g.high ?? g.guide_high, g.unit) });
  }
  function rangeText(lo, hi, unit) {
    const f = v => unit === "pct" ? fnum(v, 1) + "%" : unit === "USD_per_share" ? eps(v) : usd(v);
    if (lo == null && hi == null) return "–";
    if (lo == null || hi == null || lo === hi) return f(lo ?? hi);
    return `${f(lo)} – ${f(hi)}`;
  }

  /* ---------- sections ---------- */
  function headline(tk, d) {
    const c = d.consensus || {}, q0 = (c.periods || []).find(p => p.code === "0q"), y0 = (c.periods || []).find(p => p.code === "0y");
    const T = d.targets?.stats, R = d.ratings?.counts || {}, s90 = d.summary?.d90 || {}, ne = c.next_earnings;
    const sur = (d.surprises || []).filter(r => r.beat != null), beats = sur.filter(r => r.beat > 0).length;
    const r30 = d.revisions?.["0y"]?.["30"];
    const stat = (l, v, sub, on, tone = "") => el("div", {}, el("div", { class: "l" }, l), on ? btn(v, on, null, "v num " + tone) : el("div", { class: "v num " + tone }, v), el("div", { class: "d" }, sub || " "));
    return el("div", { class: "stats co-stats es-stats", role: "group", "aria-label": "Street summary" },
      stat("Ratings (firms)", R.buy + R.hold + R.sell ? `${R.buy}·${R.hold}·${R.sell}` : "–", "buy · hold · sell", () => openRatings(d)),
      stat("Mean price target", px(T?.mean), T ? `${pctS(T.upside_mean)} vs ${px(d.price?.close)}` : "no targets", T ? () => openTargets(d, "mean") : null, cls(T?.upside_mean)),
      stat("Next report", ne ? fdate(ne.date) : "–", ne ? (ne.is_estimate ? "date estimated by provider" : "date confirmed by provider") : "", ne ? () => openRecord(yahooSpec(d, "Next earnings date", fdate(ne.date), ne.is_estimate ? "Estimated" : "Confirmed",
        [["Date", ne.date, true], ["Estimated", ne.is_estimate ? "yes" : "no"], ["Window end", ne.window_end], ["Call date (provider)", ne.call_date], ["Consensus EPS", eps(ne.eps?.avg)], ["Consensus revenue", usd(ne.revenue?.avg)]], ne)) : null),
      stat("Qtr EPS consensus", eps(q0?.eps.avg), q0 ? `${cnt(q0.eps.n)} analysts · ${MONTHS[pd(q0.end).getUTCMonth()]} ${pd(q0.end).getUTCFullYear()} qtr` : "", q0 ? () => openPeriod(d, q0, "eps", "avg") : null),
      stat("FY EPS, 30-day revision", r30 ? pctS(r30.eps_pct) : "–", r30 ? `from ${eps(r30.eps_from)} on ${fdate(r30.from_date)}` : `history from ${d.snapshots?.[0] ? fdate(d.snapshots[0].as_of) : "–"}`, r30 ? () => openRevision(d, "0y", "30") : null, cls(r30?.eps_pct)),
      stat("Last 90 days", `${s90.upgrades || 0}↑ ${s90.downgrades || 0}↓`, `${s90.target_raises || 0} target raises · ${s90.target_cuts || 0} cuts`, () => { const t = document.getElementById("es-actions"); t && reveal(t, "start"); }),
      stat("EPS beat rate", sur.length ? `${beats}/${sur.length}` : "–", sur.length ? "last quarters, provider basis" : "", sur.length ? () => openSurprise(d, sur.at(-1), "surprise_pct") : null));
  }

  function openPeriod(d, p, metric, field) {
    const r = p[metric], f = metric === "eps" ? eps : usd;
    openRecord(yahooSpec(d, `${p.label} ${metric === "eps" ? "EPS" : "revenue"} consensus`, f(r[field]), `Fiscal period ending ${fdate(p.end)}${p.end_fixed ? " (end date corrected, see note)" : ""}`,
      [["Period code", p.code], ["Fiscal period end", p.end], ["Provider end date", p.end_provider || p.end], ["Average", f(r.avg), field === "avg"], ["Low", f(r.low), field === "low"], ["High", f(r.high), field === "high"],
        ["Analysts", cnt(r.n), field === "n"], ["Year-ago", f(r.year_ago), field === "year_ago"], ["Growth (provider)", metric === "eps" && p.growth != null ? pctS(p.growth) : "–"]].concat(p.end_fixed ? [["End date note", p.end_fixed]] : []), p));
  }
  function openRevision(d, code, w) {
    const r = d.revisions?.[code]?.[w];
    if (!r) return;
    openRecord({ ...yahooSpec(d, `${PERIOD[code]} consensus revision, ${w} days`, pctS(r.eps_pct), `Our snapshot ${fdate(r.from_date)} vs latest snapshot`,
      [["Window", w + " days"], ["From snapshot", r.from_date], ["EPS then", eps(r.eps_from)], ["EPS now", eps(r.eps_to)], ["EPS change", pctS(r.eps_pct), true], ["Revenue then", usd(r.rev_from)], ["Revenue now", usd(r.rev_to)], ["Revenue change", pctS(r.rev_pct)]], r),
      eyebrow: "Revision · calculated from stored snapshots" });
  }

  function consensusTable(tk, d) {
    const c = d.consensus;
    if (!c?.periods?.length) return panel("Consensus estimates", null, el("div", { class: "bd muted" }, "No consensus estimates from the provider for this company."));
    const rb = (r, f, p, metric) => {
      const pos = r.avg != null && r.low != null && r.high != null && r.high > r.low ? Math.min(1, Math.max(0, (r.avg - r.low) / (r.high - r.low))) : null;
      return el("div", { class: "es-range" },
        el("div", { class: "es-range-top" }, btn(f(r.avg), () => openPeriod(d, p, metric, "avg"), null, "num es-strong"), btn(cnt(r.n) + " est.", () => openPeriod(d, p, metric, "n"), null, "faint es-n")),
        el("div", { class: "es-track" }, pos != null ? el("i", { style: `left:calc(${pos * 100}% - 4px)` }) : null),
        el("div", { class: "es-range-lh" }, btn(f(r.low), () => openPeriod(d, p, metric, "low"), null, "num"), btn(f(r.high), () => openPeriod(d, p, metric, "high"), null, "num")));
    };
    const growth = (now, ya) => now != null && ya ? (now - ya) / Math.abs(ya) : null;
    const t = el("table", { class: "t es-cons" }, el("thead", {}, el("tr", {}, el("th", {}, "Period"), el("th", {}, "EPS · low – high"), el("th", { class: "r" }, "Year ago"), el("th", { class: "r" }, "Growth"),
      el("th", {}, "Revenue · low – high"), el("th", { class: "r" }, "Year ago"), el("th", { class: "r" }, "Growth"))),
      el("tbody", {}, ...c.periods.map(p => {
        const ge = growth(p.eps.avg, p.eps.year_ago), gr = growth(p.revenue.avg, p.revenue.year_ago);
        return el("tr", {}, el("td", {}, el("div", { class: "es-strong" }, p.label), el("div", { class: "faint es-sm" }, `ends ${fdate(p.end)}`, p.end_fixed ? el("span", { title: p.end_fixed + `; provider listed ${p.end_provider}` }, " †") : null)),
          el("td", {}, rb(p.eps, eps, p, "eps")), el("td", { class: "r" }, btn(eps(p.eps.year_ago), () => openPeriod(d, p, "eps", "year_ago"), null, "num")), el("td", { class: "r num " + cls(ge) }, pctS(ge)),
          el("td", {}, rb(p.revenue, usd, p, "revenue")), el("td", { class: "r" }, btn(usd(p.revenue.year_ago), () => openPeriod(d, p, "revenue", "year_ago"), null, "num")), el("td", { class: "r num " + cls(gr) }, pctS(gr)));
      })));
    return panel("Consensus estimates", el("span", { class: "es-prov-chip" }, "Yahoo Finance · as of " + fdate(c.as_of.slice(0, 10))), el("div", { class: "scroll" }, t),
      note("Average, low and high of analyst forecasts compiled by Yahoo Finance; third-party, not from filings. Growth is against the provider's year-ago figure. ",
        c.periods.some(p => p.end_fixed) ? "† Period end corrected: the provider repeated an already reported or earlier period end. " : "", "Click any value for the stored record."));
  }

  function revisionsPanel(tk, d) {
    const host = el("div", { class: "bd" });
    const R = d.revisions || {};
    const t = el("table", { class: "t es-rev" }, el("thead", {}, el("tr", {}, el("th", {}, "Period"), el("th", { class: "r" }, "EPS now"), ...["7", "30", "60", "90"].map(w => el("th", { class: "r" }, w + "d")))),
      el("tbody", {}, ...(d.consensus?.periods || []).map(p => el("tr", {}, el("td", {}, PERIOD[p.code]), el("td", { class: "r" }, btn(eps(p.eps.avg), () => openPeriod(d, p, "eps", "avg"), null, "num")),
        ...["7", "30", "60", "90"].map(w => { const r = R[p.code]?.[w]; return el("td", { class: "r" }, r ? btn(pctS(r.eps_pct), () => openRevision(d, p.code, w), null, "num " + cls(r.eps_pct)) : el("span", { class: "faint" }, "–")); })))));
    requestAnimationFrame(() => revisionChart(host, d));
    return panel("Estimate revisions", el("span", { class: "es-prov-chip" }, `${(d.snapshots || []).length} snapshot${(d.snapshots || []).length === 1 ? "" : "s"}`), host, el("div", { class: "scroll" }, t),
      note("Revisions compare the latest consensus with our own stored snapshot of the same fiscal period 7, 30, 60 and 90 days earlier. A dash means no snapshot that old exists yet."));
  }

  function surprisesPanel(tk, d) {
    const rows = (d.surprises || []).slice().sort((a, b) => a.quarter_end.localeCompare(b.quarter_end));
    const host = el("div", { class: "bd", style: "padding-bottom:4px" });
    requestAnimationFrame(() => surpriseChart(host, d, rows));
    const t = el("table", { class: "t es-sur" }, el("thead", {}, el("tr", {}, el("th", {}, "Quarter"), el("th", { class: "r" }, "Consensus"), el("th", { class: "r" }, "Reported"), el("th", { class: "r" }, "Surprise"),
      el("th", { class: "r", title: "Diluted EPS from SEC filings (GAAP), for reference" }, "GAAP EPS"), el("th", { class: "r", title: "Next session after the earnings 8-K, stock vs SPY" }, "Reaction"))),
      el("tbody", {}, ...rows.slice().reverse().map(r => el("tr", {}, el("td", { class: "num" }, fdate(r.quarter_end)),
        el("td", { class: "r" }, btn(eps(r.eps_estimate), () => openSurprise(d, r, "eps_estimate"), null, "num")), el("td", { class: "r" }, btn(eps(r.eps_actual), () => openSurprise(d, r, "eps_actual"), null, "num")),
        el("td", { class: "r" }, btn(pctS(r.surprise_pct), () => openSurprise(d, r, "surprise_pct"), null, "num " + cls(r.surprise_pct))),
        el("td", { class: "r" }, r.gaap_eps ? btn(eps(r.gaap_eps.v), () => openSec(tk, "EPS diluted", r.gaap_eps.end, r.gaap_eps.url), "Open the SEC filing value", "num") : el("span", { class: "faint", title: "No quarterly diluted EPS in filings for this quarter (often the fiscal Q4, reported only for the year)" }, "–")),
        el("td", { class: "r" }, r.reaction ? btn(`${pctS(r.reaction.r1)}`, () => openSurprise(d, r, "reaction"), `SPY ${pctS(r.reaction.spy1)}`, "num " + cls(r.reaction.r1)) : el("span", { class: "faint" }, "–"))))));
    const rs = d.revenue_surprises || [];
    return panel("Earnings surprises", el("span", { class: "es-prov-chip" }, "Yahoo Finance basis"), host, el("div", { class: "scroll" }, t),
      rs.length ? el("div", { class: "scroll" }, el("table", { class: "t es-sur" }, el("thead", {}, el("tr", {}, el("th", {}, "Revenue, quarter"), el("th", { class: "r" }, "Consensus (snapshot)"), el("th", { class: "r" }, "SEC revenue"), el("th", { class: "r" }, "Surprise"))),
        el("tbody", {}, ...rs.map(r => el("tr", {}, el("td", {}, fdate(r.quarter_end)), el("td", { class: "r num" }, `${usd(r.consensus)} (${r.consensus_as_of})`), el("td", { class: "r" }, btn(usd(r.actual), () => openSec(tk, "Revenue", r.quarter_end, r.filing?.url), null, "num")), el("td", { class: "r num " + cls(r.surprise_pct) }, pctS(r.surprise_pct))))))) : null,
      note("Surprise is the provider's reported EPS against the provider's consensus, generally on the adjusted (non-GAAP) basis analysts forecast. GAAP diluted EPS from SEC filings is shown alongside and never mixed into the surprise. Reaction: next session after the earnings 8-K filing date, from Filed Under prices."));
  }

  function targetsPanel(tk, d) {
    const host = el("div", { class: "bd", style: "padding-bottom:0" }), hist = el("div", { class: "bd", style: "padding-top:4px" });
    const T = d.targets?.stats;
    requestAnimationFrame(() => { targetChart(host, d); ratingHistory(hist, d); });
    const kv = (l, v, f) => el("div", {}, el("span", { class: "faint" }, l), btn(v, () => openTargets(d, f), null, "num"));
    return panel("Price targets and ratings", el("span", { class: "es-prov-chip" }, BZ), host,
      T ? el("div", { class: "es-kv bd" }, kv("Low", px(T.low), "low"), kv("Median", px(T.median), "median"), kv("Mean", px(T.mean), "mean"), kv("High", px(T.high), "high"), kv("Firms", String(T.n), "n")) : null,
      el("div", { class: "bd", style: "padding-top:0" }, ratingBar(d)), hist,
      note("Each firm's latest price target and rating from the past 12 months; coverage drops are excluded. Dots: green buy, grey hold, red sell."));
  }

  function actionsPanel(tk, d) {
    const acts = d.actions || [];
    let k = store.get("esKind", "all"), win = store.get("esWin", "365"), limit = 30;
    const body = el("div", { class: "scroll es-act-wrap" });
    const draw = () => {
      const since = win === "all" ? "" : ago(+win);
      const rows = acts.filter(a => a.date >= since && (k === "all" || kind(a) === k));
      body.innerHTML = "";
      if (!rows.length) { body.append(el("div", { class: "bd muted" }, "No analyst actions in this window.")); return; }
      const t = el("table", { class: "t es-act" }, el("thead", {}, el("tr", {}, el("th", {}, "Date"), el("th", {}, "Firm · analyst"), el("th", {}, "Action"), el("th", {}, "Rating"), el("th", { class: "r" }, "Price target"), el("th", {}, ""))));
      const tb = el("tbody");
      rows.slice(0, limit).forEach(a => {
        const kk = kind(a), mv = a.pt_current != null && a.pt_prior ? a.pt_current / a.pt_prior - 1 : null;
        tb.append(el("tr", { class: "click", onclick: () => openRecord(actionSpec(d, a)), title: "Open the Benzinga record" },
          el("td", { class: "num nowrap" }, fdate(a.date)),
          el("td", {}, el("span", { class: "es-firm" }, firmLogo(a, 20), el("span", {}, el("span", { class: "es-fn" }, a.firm), el("span", { class: "es-an faint" }, a.analyst || "analyst not named")))),
          el("td", {}, el("span", { class: `es-kind k-${kk}` }, KIND_LABEL[kk])),
          el("td", { class: "es-rt" }, a.rating_prior && a.rating_prior !== a.rating_current ? el("span", { class: "faint" }, a.rating_prior + " → ") : null, a.rating_current || "–"),
          el("td", { class: "r num nowrap" }, a.pt_current == null ? el("span", { class: "faint" }, "–") : [a.pt_prior != null && a.pt_prior !== a.pt_current ? el("span", { class: "faint" }, px(a.pt_prior) + " → ") : null, px(a.pt_current),
            mv != null && Math.abs(mv) > .0005 ? el("span", { class: "es-mv " + cls(mv) }, " " + pctS(mv, 0)) : null]),
          el("td", { class: "r" }, a.url ? el("a", { href: a.url, target: "_blank", rel: "noopener", onclick: e => e.stopPropagation(), title: "Benzinga" }, "↗") : null)));
      });
      t.append(tb); body.append(t);
      if (rows.length > limit) body.append(el("div", { class: "bd" }, el("button", { class: "btn", onclick: () => { limit += 60; draw(); } }, `Show more (${rows.length - limit} more)`)));
    };
    const ctl = el("div", { class: "r" },
      seg([["all", "All"], ["upgrade", "Upgrades"], ["downgrade", "Downgrades"], ["initiate", "Initiations"], ["target", "Target changes"]], k, v => { k = v; store.set("esKind", v); limit = 30; draw(); }),
      seg([["90", "90D"], ["365", "1Y"], ["all", "All"]], win, v => { win = v; store.set("esWin", v); limit = 30; draw(); }));
    draw();
    const p = panel("Analyst actions", ctl, body, note(`${acts.length} actions from ${acts.length ? fdate(acts.at(-1).date) : "–"} to ${acts.length ? fdate(acts[0].date) : "–"} (Benzinga history on this feed starts July 2, 2025). ${BZ}. Click a row for the full record.`));
    p.id = "es-actions";
    return p;
  }

  function guidancePanels(tk, d) {
    const G = d.guidance || {};
    if (!G.available) return [panel("Company guidance", null, el("div", { class: "bd muted" }, G.reason || "No guidance extracted for this company."))];
    const out = [];
    const vs = G.vs_consensus || [];
    if (vs.length) {
      const t = el("table", { class: "t es-gvc" }, el("thead", {}, el("tr", {}, el("th", {}, "Metric · period"), el("th", { class: "r" }, "Guidance"), el("th", { class: "r" }, "Consensus"), el("th", { class: "r" }, "Gap to midpoint"), el("th", {}, "Consensus vs range"))),
        el("tbody", {}, ...vs.map(g => {
          const u = g.metric === "eps" ? "USD_per_share" : "USD";
          const ok = g.scale_check !== false;
          return el("tr", {}, el("td", {}, el("div", { class: "es-strong" }, `${g.label} · ${g.period}`), el("div", { class: "faint es-sm" }, `issued ${fdate(g.issued)} · ${PERIOD[g.code]}`)),
            el("td", { class: "r" }, btn(rangeText(g.guide_low, g.guide_high, u), () => openGuide(tk, d, { ...g, unit: u, low: g.guide_low, high: g.guide_high }), "Open the press release at the quoted guidance", "num")),
            el("td", { class: "r" }, btn((u === "USD" ? usd : eps)(g.consensus), () => { const p = d.consensus.periods.find(x => x.code === g.code); p && openPeriod(d, p, g.metric === "eps" ? "eps" : "revenue", "avg"); }, null, "num")),
            el("td", { class: "r num " + (ok ? cls(g.gap_pct) : "") }, ok ? pctS(g.gap_pct) : el("span", { class: "chip warn", title: "Guidance and consensus differ by more than 2x; likely a period or unit mismatch in the extraction" }, "not comparable")),
            el("td", {}, ok && g.position ? el("span", { class: "chip " + (g.position === "within" ? "" : g.position === "above" ? "up" : "down") }, g.position === "within" ? "within range" : g.position + " range") : "–"));
        })));
      out.push(panel("Guidance vs consensus", el("span", { class: "es-prov-chip" }, "company guidance · Yahoo consensus"), el("div", { class: "scroll" }, t),
        note("Latest company guidance for a fiscal period against the current consensus for the same period (period ends within 20 days, quarterly with quarterly, annual with annual). Guidance values open the press release with the quoted sentence highlighted.")));
    }
    const items = (G.items || []).filter(i => i.quote);
    const byPeriod = el("div", { class: "es-gtl" });
    let shown = 40;
    const drawItems = () => {
      byPeriod.innerHTML = "";
      items.slice(0, shown).forEach(i => {
        byPeriod.append(el("div", { class: "es-gi" + (i.withdrawn ? " wd" : "") },
          el("div", { class: "es-gi-h" }, el("span", { class: "num faint" }, fdate(i.issued)), el("b", {}, i.label), i.basis && i.basis !== "unspecified" ? el("span", { class: "chip" }, i.basis === "gaap" ? "GAAP" : i.basis === "non_gaap" || i.basis === "adjusted" ? "non-GAAP" : i.basis) : null, el("span", { class: "faint" }, i.period), i.action && i.action !== "initial" ? el("span", { class: "chip " + (i.action === "raised" ? "up" : i.action === "lowered" ? "down" : "") }, i.action) : null,
            i.withdrawn ? el("span", { class: "chip warn" }, "withdrawn") : null, el("span", { style: "margin-left:auto" }, btn(rangeText(i.low, i.high, i.unit), () => openGuide(tk, d, i), "Open the press release at this sentence", "num es-strong"))),
          el("button", { class: "es-quote", onclick: () => openGuide(tk, d, i), title: "Open the source document with this quote highlighted" }, "“" + String(i.quote).replace(/^[“"\s•]+|[”"\s]+$/g, "") + "”")));
      });
      if (items.length > shown) byPeriod.append(el("button", { class: "btn", onclick: () => { shown += 60; drawItems(); } }, `Show more (${items.length - shown})`));
    };
    drawItems();
    const C = G.credibility;
    const cred = C ? el("div", { class: "es-cred" },
      el("div", {}, el("div", { class: "l" }, "Guidance met"), el("div", { class: "v num" }, C.hit_rate != null ? fnum(C.hit_rate * 100, 0) + "%" : "–"), el("div", { class: "d" }, `${C.n_met ?? "–"} of ${C.n_scored ?? "–"} scored forecasts`)),
      el("div", {}, el("div", { class: "l" }, "Actual above range"), el("div", { class: "v num" }, C.above_rate != null ? fnum(C.above_rate * 100, 0) + "%" : "–"), el("div", { class: "d" }, `below ${C.below_rate != null ? fnum(C.below_rate * 100, 0) + "%" : "–"}`)),
      el("div", {}, el("div", { class: "l" }, "Raised during period"), el("div", { class: "v num" }, C.raise_rate != null ? fnum(C.raise_rate * 100, 0) + "%" : "–"), el("div", { class: "d" }, C.tendency ? "tendency: " + C.tendency : "")),
      el("div", {}, el("div", { class: "l" }, "Track record"), el("div", { class: "v num" }, `${C.n_series ?? "–"} series`), el("div", { class: "d" }, `${C.first_issued ? fdate(C.first_issued) : "–"} – ${C.last_issued ? fdate(C.last_issued) : "–"}`))) : null;
    const oc = (G.outcomes || []).slice(0, 12);
    const ot = oc.length ? el("table", { class: "t es-out" }, el("thead", {}, el("tr", {}, el("th", {}, "Period"), el("th", {}, "Metric"), el("th", { class: "r" }, "Final guidance"), el("th", { class: "r" }, "Actual"), el("th", {}, "Result"))),
      el("tbody", {}, ...oc.map(o => el("tr", {}, el("td", { class: "num" }, o.period), el("td", {}, o.label), el("td", { class: "r num" }, rangeText(o.final_low, o.final_high, o.unit)),
        el("td", { class: "r" }, o.actual_inputs?.[0]?.accession ? btn(rangeText(o.actual, o.actual, o.unit), () => window.open(`https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=${D.companies[tk]?.cik || ""}&type=${o.actual_inputs[0].form}`, "_blank", "noopener"), `${o.actual_inputs[0].form} filed ${o.actual_inputs[0].filed} · ${o.actual_inputs[0].accession}`, "num") : rangeText(o.actual, o.actual, o.unit)),
        el("td", {}, o.scale_check === false ? el("span", { class: "chip warn", title: "Guidance and actual differ by more than 2x: likely a quarterly figure compared with an annual one" }, "period mismatch") : el("span", { class: "chip " + (o.result === "above" ? "up" : o.result === "below" ? "down" : "") }, o.result || "–")))))) : null;
    out.push(el("div", { class: "grid g64" },
      panel("Guidance timeline", el("span", { class: "es-prov-chip" }, `${items.length} quoted forecasts`), el("div", { class: "bd" }, byPeriod),
        note("Verbatim forward-looking statements extracted from the company's earnings releases and investor materials. Each quote opens the source document with the sentence highlighted.")),
      panel("Guidance credibility", null, cred || el("div", { class: "bd muted" }, "Not enough scored guidance yet."), ot ? el("div", { class: "scroll" }, ot) : null,
        note("Track record compares the final guidance for each period with the reported figure from SEC filings. Rows flagged “period mismatch” compare different period lengths in the source extraction and should not be read as a beat or miss."))));
    return out;
  }

  function streetView(tk, d) {
    const S = d.street_view;
    const hd = el("span", { class: "aimeta" }, svgUse("i-spark"), S ? `AI-generated · ${fdate(S.generated_at.slice(0, 10))} · ${S.model}` : "AI-generated summary");
    if (!S) return panel("Street view", hd, el("div", { class: "bd muted" }, "The summary is written by the nightly estimates job from the records on this page. It has not run for this company yet."));
    const refChip = r => {
      const label = r.startsWith("A:") ? (d.actions.find(a => "A:" + a.id === r)?.firm || "action") : r.replace(/^C:/, "consensus ").replace(/^R:/, "revision ").replace(/^S:/, "surprise ").replace(/^G:.*/, "guidance").replace(/^T$/, "targets").replace(/^D$/, "ratings");
      return el("button", { class: "chip link", onclick: () => openRef(tk, d, r) }, label);
    };
    return panel("Street view", hd, el("div", { class: "bd es-sv" }, S.headline ? el("p", { class: "es-sv-h" }, S.headline, el("span", { class: "faint", title: "The headline is not checked against the records" }, " (unverified summary)")) : null,
      el("ul", {}, ...S.points.map(p => el("li", {}, p.text, " ", ...p.refs.map(refChip), p.verified ? null : el("span", { class: "chip warn", title: "Numbers not found in the cited records: " + (p.unsupported_numbers || []).join(", ") }, "unverified"))))),
      note("Generated from the stored records cited in each point; nothing else is given to the model. Not investment advice."));
  }
  function openRef(tk, d, r) {
    if (r.startsWith("A:")) { const a = d.actions.find(x => "A:" + x.id === r); return a && openRecord(actionSpec(d, a)); }
    if (r.startsWith("C:")) { const p = d.consensus.periods.find(x => x.code === r.slice(2)); return p && openPeriod(d, p, "eps", "avg"); }
    if (r.startsWith("R:")) { const [, code, w] = r.split(":"); return openRevision(d, code, w); }
    if (r.startsWith("S:")) { const s = d.surprises.find(x => x.quarter_end === r.slice(2)); return s && openSurprise(d, s, "surprise_pct"); }
    if (r.startsWith("G:")) { const g = d.guidance.vs_consensus.find(x => "G:" + x.guide_id === r); return g && openGuide(tk, d, { ...g, unit: g.metric === "eps" ? "USD_per_share" : "USD", low: g.guide_low, high: g.guide_high }); }
    if (r === "T") return openTargets(d, "mean");
    if (r === "D") return openRatings(d);
  }

  /* ---------- tab ---------- */
  function render(body, tk) {
    injectCSS();
    const host = el("div", { class: "es-tab" }, el("div", { class: "muted", style: "padding:16px 4px" }, "Loading estimates…"));
    body.append(host);
    Promise.all([load(tk), loadFirms()]).then(([d, firms]) => { FIRMS_MAP = firms; host.innerHTML = ""; draw(host, tk, d); })
      .catch(() => { host.innerHTML = ""; host.append(panel("Estimates & guidance", null, el("div", { class: "bd muted" }, "Estimates for this company are not available yet. They are refreshed daily."))); });
  }

  function draw(host, tk, d) {
    host.append(headline(tk, d));
    host.append(el("div", { class: "grid g64" }, consensusTable(tk, d), streetView(tk, d)));
    host.append(el("div", { class: "grid g2" }, revisionsPanel(tk, d), surprisesPanel(tk, d)));
    host.append(el("div", { class: "grid g64" }, actionsPanel(tk, d), targetsPanel(tk, d)));
    guidancePanels(tk, d).forEach(p => host.append(p));
    host.append(el("div", { class: "es-foot" }, el("b", {}, BZ), ` (analyst actions, ratings and price targets; retrieved ${relTime(d.sources?.benzinga?.retrieved_at)}). Consensus estimates and surprises: Yahoo Finance (retrieved ${relTime(d.consensus?.as_of)}), via Firecrawl. Actuals: SEC filings. Guidance: company press releases. Bundle built ${relTime(d.generated)}.`));
  }

  let cssDone = false;
  function injectCSS() {
    if (cssDone || document.querySelector('link[href*="estimates.css"]')) { cssDone = true; return; }
    cssDone = true;
    document.head.append(el("link", { rel: "stylesheet", href: "/estimates.css" }));
  }

  window.FU_ESTIMATES = { openRecord, kind, bucket, rangeText };
  window.FU_COTABS = window.FU_COTABS || [];
  window.FU_COTABS.push({ id: "estimates", name: "Estimates & guidance", after: "financials", render });
})();
