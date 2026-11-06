// Exhibit library for Filed Under report PDFs. Each kind draws into a sized <div class="chart" data-kind data-w data-h>.
// Colors are fixed print tokens; every mark is placed on a d3 scale; labels avoid overlap with simple nudging.
(() => {
  const D = window.__D;
  const C = { navy: "#0F1830", acc: "#2B4ACB", accL: "#9DB0F2", accXL: "#DCE3FB", teal: "#0E9384", tealL: "#A6DDD6", amber: "#D97706", amberL: "#F6D3A3",
    up: "#0A8754", dn: "#D12F35", ink: "#0B0C0F", ink2: "#2A2E36", muted: "#5F6673", faint: "#959BA6", line: "#E3E5EA", soft: "#F4F5F8" };
  const mon = s => { const d = new Date(s + "T00:00:00Z"); return d.toLocaleString("en-US", { month: "short", timeZone: "UTC" }) + " '" + String(d.getUTCFullYear()).slice(2); };
  const pct = (v, n = 0) => (v >= 0 ? "+" : "−") + Math.abs(v * 100).toFixed(n) + "%";
  const bn = v => "$" + (v / 1e9).toFixed(v >= 1e11 ? 0 : 1) + "B";
  const K = {};
  function frame(el, W, H, m) {
    const svg = d3.select(el).append("svg").attr("width", W).attr("height", H).attr("viewBox", `0 0 ${W} ${H}`);
    return { svg, g: svg.append("g").attr("transform", `translate(${m.l},${m.t})`), iw: W - m.l - m.r, ih: H - m.t - m.b, m };
  }
  function yGrid(f, y, n, fmt, right) {
    const ax = right ? d3.axisRight(y) : d3.axisLeft(y);
    const g = f.g.append("g").attr("class", "grid").attr("transform", right ? `translate(${f.iw},0)` : null).call(ax.ticks(n).tickSize(right ? 0 : -f.iw).tickFormat(fmt));
    g.selectAll("text").attr("x", right ? 6 : -5);
    return g;
  }
  function xAxis(f, x, fmt, every = 1) {
    const g = f.g.append("g").attr("transform", `translate(0,${f.ih})`).call(d3.axisBottom(x).tickSize(0).tickFormat((d, i) => i % every ? "" : fmt(d, i)));
    g.select(".domain").attr("stroke", C.ink2).attr("stroke-width", .6); g.selectAll("text").attr("dy", 10);
    return g;
  }
  function legend(f, items, x = 0, y = -10) {
    let ox = x;
    items.forEach(([label, color, kind]) => {
      const g = f.g.append("g").attr("transform", `translate(${ox},${y})`);
      if (kind === "line") g.append("line").attr("x1", 0).attr("x2", 12).attr("y1", -2.5).attr("y2", -2.5).attr("stroke", color).attr("stroke-width", 2);
      else if (kind === "dot") g.append("circle").attr("cx", 4).attr("cy", -2.5).attr("r", 3.5).attr("fill", color);
      else g.append("rect").attr("y", -6.5).attr("width", 9).attr("height", 8).attr("fill", color);
      const t = g.append("text").attr("x", kind === "line" ? 16 : 13).attr("y", 0).text(label);
      ox += (kind === "line" ? 16 : 13) + t.node().getComputedTextLength() + 14;
    });
  }
  const nudge = (arr, gap) => { arr.sort((a, b) => a.y - b.y); for (let i = 1; i < arr.length; i++) if (arr[i].y - arr[i - 1].y < gap) arr[i].y = arr[i - 1].y + gap; return arr; };

  /* Revenue bars + YoY line + guided bar */
  K.revcombo = (el, W, H) => {
    const r = D.rev.slice(-12), g = D.guide[D.guide.length - 1];
    const rows = [...r.map(d => ({ k: d.end, v: d.rev, yoy: d.yoy })), { k: g.end, v: g.mid, lo: g.lo, hi: g.hi, guide: true }];
    const f = frame(el, W, H, { t: 22, r: 44, b: 22, l: 38 });
    const x = d3.scaleBand().domain(rows.map(d => d.k)).range([0, f.iw]).padding(.28);
    const y = d3.scaleLinear().domain([0, d3.max(rows, d => d.hi || d.v) * 1.1]).range([f.ih, 0]).nice();
    const yy = d3.scaleLinear().domain([0, d3.max(r, d => d.yoy) * 1.12]).range([f.ih, 0]).nice();
    yGrid(f, y, 5, v => "$" + v / 1e9);
    const ra = yGrid(f, yy, 5, v => v * 100 + "%", true); ra.selectAll("text").style("fill", C.amber);
    xAxis(f, x, (d, i) => rows[i].guide ? "Q3 FY27E" : mon(d), 2);
    f.g.selectAll("rect.b").data(rows).join("rect").attr("x", d => x(d.k)).attr("y", d => y(d.v)).attr("width", x.bandwidth()).attr("height", d => f.ih - y(d.v))
      .attr("fill", (d, i) => d.guide ? "#fff" : i === r.length - 1 ? C.acc : C.accL).attr("stroke", d => d.guide ? C.acc : "none").attr("stroke-dasharray", d => d.guide ? "3 2" : null);
    const gx = x(g.end) + x.bandwidth() / 2;
    f.g.append("line").attr("x1", gx).attr("x2", gx).attr("y1", y(g.hi)).attr("y2", y(g.lo)).attr("stroke", C.ink2);
    [g.lo, g.hi].forEach(v => f.g.append("line").attr("x1", gx - 5).attr("x2", gx + 5).attr("y1", y(v)).attr("y2", y(v)).attr("stroke", C.ink2));
    f.g.append("text").attr("class", "vl").attr("x", gx).attr("y", y(g.hi) - 5).attr("text-anchor", "middle").text("Guide $" + (g.mid / 1e9).toFixed(0) + "B ±2%");
    const lr = r[r.length - 1];
    f.g.append("text").attr("class", "vl").attr("x", x(lr.end) + x.bandwidth() / 2).attr("y", y(lr.rev) - 5).attr("text-anchor", "middle").text(bn(lr.rev));
    const line = d3.line().x(d => x(d.end) + x.bandwidth() / 2).y(d => yy(d.yoy)).curve(d3.curveMonotoneX);
    f.g.append("path").datum(r).attr("fill", "none").attr("stroke", "#fff").attr("stroke-width", 4).attr("d", line);
    f.g.append("path").datum(r).attr("fill", "none").attr("stroke", C.amber).attr("stroke-width", 1.8).attr("d", line);
    f.g.selectAll("circle").data(r).join("circle").attr("cx", d => x(d.end) + x.bandwidth() / 2).attr("cy", d => yy(d.yoy)).attr("r", 2.2).attr("fill", C.amber).attr("stroke", "#fff");
    legend(f, [["Revenue ($B)", C.accL], ["YoY growth (right)", C.amber, "line"], ["Guided quarter", "#fff"]], 0, -10);
  };

  /* Waterfall: YoY revenue bridge by segment */
  K.bridge = (el, W, H) => {
    const b = D.bridge, start = d3.sum(b.parts, p => p.a), end = d3.sum(b.parts, p => p.b);
    const steps = [{ k: b.from_label, v0: 0, v1: start, kind: "total" }];
    let acc = start;
    const SHORT = { "Hyperscale": "Hyperscale", "AI Clouds, Industrial, & Enterprise": "AI clouds & enterprise", "Edge Computing": "Edge computing" };
    b.parts.forEach(p => { steps.push({ k: SHORT[p.k] || p.k, v0: acc, v1: acc + (p.b - p.a), kind: p.b >= p.a ? "up" : "down", d: p.b - p.a, g: p.b / p.a - 1 }); acc += p.b - p.a; });
    steps.push({ k: b.to_label, v0: 0, v1: end, kind: "total" });
    const f = frame(el, W, H, { t: 22, r: 8, b: 32, l: 38 });
    const x = d3.scaleBand().domain(steps.map(s => s.k)).range([0, f.iw]).padding(.3);
    const y = d3.scaleLinear().domain([0, end * 1.12]).range([f.ih, 0]).nice();
    yGrid(f, y, 5, v => "$" + v / 1e9);
    const xa = f.g.append("g").attr("transform", `translate(0,${f.ih})`).call(d3.axisBottom(x).tickSize(0));
    xa.select(".domain").attr("stroke", C.ink2).attr("stroke-width", .6);
    xa.selectAll("text").attr("dy", 10).style("font-size", "6.4pt").each(function (d) { const t = d3.select(this), w = d.split(" "); if (w.length > 1 && d.length * 4.6 > x.bandwidth() * 1.5) { t.text(""); const half = Math.ceil(w.length / 2); [w.slice(0, half).join(" "), w.slice(half).join(" ")].forEach((s, i) => t.append("tspan").attr("x", 0).attr("dy", i ? 9 : 10).text(s)); } });
    steps.forEach((s, i) => {
      if (i === 0 || i === steps.length - 1) s.short = s.k;
      const lo = Math.min(s.v0, s.v1), hi = Math.max(s.v0, s.v1);
      f.g.append("rect").attr("x", x(s.k)).attr("width", x.bandwidth()).attr("y", y(hi)).attr("height", Math.max(1, y(lo) - y(hi)))
        .attr("fill", s.kind === "total" ? C.navy : s.kind === "up" ? C.teal : C.dn);
      if (i < steps.length - 1) f.g.append("line").attr("x1", x(s.k) + x.bandwidth()).attr("x2", x(steps[i + 1].k)).attr("y1", y(s.v1)).attr("y2", y(s.v1)).attr("stroke", C.faint).attr("stroke-dasharray", "2 2");
      f.g.append("text").attr("class", "vl").attr("x", x(s.k) + x.bandwidth() / 2).attr("y", y(hi) - (s.g != null ? 14 : 5)).attr("text-anchor", "middle").text(s.kind === "total" ? bn(s.v1) : "+" + bn(s.d).slice(1));
      if (s.g != null) f.g.append("text").attr("x", x(s.k) + x.bandwidth() / 2).attr("y", y(hi) - 4).attr("text-anchor", "middle").style("fill", C.teal).style("font-size", "6.4pt").text(pct(s.g) + " YoY");
    });
  };

  /* Stacked bars: Data Center vs everything else, with DC share line labels */
  K.mix = (el, W, H) => {
    const r = D.dc.filter(d => d.dc);
    const f = frame(el, W, H, { t: 24, r: 8, b: 22, l: 38 });
    const x = d3.scaleBand().domain(r.map(d => d.end)).range([0, f.iw]).padding(.25);
    const y = d3.scaleLinear().domain([0, d3.max(r, d => d.total) * 1.08]).range([f.ih, 0]).nice();
    yGrid(f, y, 5, v => "$" + v / 1e9);
    xAxis(f, x, d => mon(d), 2);
    r.forEach(d => {
      f.g.append("rect").attr("x", x(d.end)).attr("width", x.bandwidth()).attr("y", y(d.dc)).attr("height", f.ih - y(d.dc)).attr("fill", C.navy);
      f.g.append("rect").attr("x", x(d.end)).attr("width", x.bandwidth()).attr("y", y(d.total)).attr("height", y(d.dc) - y(d.total)).attr("fill", C.tealL);
      f.g.append("text").attr("x", x(d.end) + x.bandwidth() / 2).attr("y", y(d.total) - 4).attr("text-anchor", "middle").style("font-size", "6.4pt").text(Math.round(d.dc / d.total * 100) + "%");
    });
    legend(f, [["Data Center", C.navy], ["All other", C.tealL], ["label = DC share", C.faint, "dot"]], 0, -12);
  };

  /* Price line with area, 52-week band, earnings markers */
  K.price = (el, W, H) => {
    const px = D.px.map(([d, c]) => ({ d: new Date(d + "T00:00:00Z"), c }));
    const ev = D.react.filter(r => new Date(r.d + "T00:00:00Z") >= px[0].d);
    const f = frame(el, W, H, { t: 16, r: 46, b: 22, l: 8 });
    const x = d3.scaleUtc().domain(d3.extent(px, d => d.d)).range([0, f.iw]);
    const y = d3.scaleLinear().domain([d3.min(px, d => d.c) * .9, d3.max(px, d => d.c) * 1.08]).range([f.ih, 0]).nice();
    yGrid(f, y, 5, v => "$" + v, true);
    f.g.selectAll(".grid line").remove();
    f.g.append("g").attr("class", "grid").call(d3.axisLeft(y).ticks(5).tickSize(-f.iw).tickFormat("")).attr("transform", `translate(0,0)`).selectAll("line");
    const xa = f.g.append("g").attr("transform", `translate(0,${f.ih})`).call(d3.axisBottom(x).ticks(Math.max(3, Math.floor(f.iw / 85))).tickSize(0).tickFormat(d3.utcFormat("%b '%y")));
    xa.select(".domain").attr("stroke", C.ink2).attr("stroke-width", .6); xa.selectAll("text").attr("dy", 10);
    const last = px[px.length - 1], yr = px.filter(p => p.d >= new Date(last.d - 365 * 864e5));
    const hi = d3.max(yr, p => p.c), lo = d3.min(yr, p => p.c);
    f.g.append("rect").attr("x", x(yr[0].d)).attr("width", f.iw - x(yr[0].d)).attr("y", y(hi)).attr("height", y(lo) - y(hi)).attr("fill", C.accXL).attr("opacity", .55);
    if (f.iw > 450) f.g.append("text").attr("x", x(yr[0].d) + 4).attr("y", y(lo) + 10).text(`52-week range $${lo.toFixed(0)}–$${hi.toFixed(0)}`);
    const area = d3.area().x(d => x(d.d)).y0(f.ih).y1(d => y(d.c)), line = d3.line().x(d => x(d.d)).y(d => y(d.c));
    const grad = f.svg.append("defs").append("linearGradient").attr("id", "pg").attr("x1", 0).attr("x2", 0).attr("y1", 0).attr("y2", 1);
    grad.append("stop").attr("offset", 0).attr("stop-color", C.acc).attr("stop-opacity", .18); grad.append("stop").attr("offset", 1).attr("stop-color", C.acc).attr("stop-opacity", 0);
    f.g.append("path").datum(px).attr("fill", "url(#pg)").attr("d", area);
    f.g.append("path").datum(px).attr("fill", "none").attr("stroke", C.acc).attr("stroke-width", 1.5).attr("d", line);
    const close = d => { const t = +d; let best = px[0]; for (const p of px) if (Math.abs(p.d - t) < Math.abs(best.d - t)) best = p; return best; };
    ev.forEach(e => {
      const p = close(new Date(e.d + "T00:00:00Z")), cy = y(p.c), up = e.cc >= 0;
      f.g.append("line").attr("x1", x(p.d)).attr("x2", x(p.d)).attr("y1", cy).attr("y2", up ? cy - 16 : cy + 16).attr("stroke", up ? C.up : C.dn).attr("stroke-width", .8);
      f.g.append("circle").attr("cx", x(p.d)).attr("cy", cy).attr("r", 3).attr("fill", "#fff").attr("stroke", up ? C.up : C.dn).attr("stroke-width", 1.4);
      f.g.append("text").attr("class", "vl").attr("x", x(p.d)).attr("y", up ? cy - 19 : cy + 25).attr("text-anchor", "middle").style("fill", up ? C.up : C.dn).text(pct(e.cc));
    });
    f.g.append("circle").attr("cx", x(last.d)).attr("cy", y(last.c)).attr("r", 3).attr("fill", C.acc);
    f.g.append("text").attr("class", "vl").attr("x", x(last.d) - 4).attr("y", y(last.c) + 13).attr("text-anchor", "end").text("$" + last.c.toFixed(2));
    legend(f, [["Close", C.acc, "line"], ["Earnings: next-day move", C.ink2, "dot"]], 0, -6);
  };

  /* Monthly return heatmap */
  K.heat = (el, W, H) => {
    const r = D.mret, years = [...new Set(r.map(d => d.m.slice(0, 4)))], months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    const f = frame(el, W, H, { t: 16, r: 46, b: 6, l: 30 });
    const x = d3.scaleBand().domain(months).range([0, f.iw]).padding(.06), y = d3.scaleBand().domain(years).range([0, f.ih]).padding(.08);
    const ext = d3.max(r, d => Math.abs(d.r));
    const col = v => v >= 0 ? d3.interpolateRgb("#F4F5F8", C.up)(Math.min(1, v / ext)) : d3.interpolateRgb("#F4F5F8", C.dn)(Math.min(1, -v / ext));
    months.forEach(m => f.g.append("text").attr("x", x(m) + x.bandwidth() / 2).attr("y", -5).attr("text-anchor", "middle").text(m));
    years.forEach(yv => {
      f.g.append("text").attr("x", -6).attr("y", y(yv) + y.bandwidth() / 2 + 3).attr("text-anchor", "end").text(yv);
      const yrs = r.filter(d => d.m.startsWith(yv)), tot = yrs.reduce((a, d) => a * (1 + d.r), 1) - 1;
      f.g.append("text").attr("class", "vl").attr("x", f.iw + 6).attr("y", y(yv) + y.bandwidth() / 2 + 3).style("fill", tot >= 0 ? C.up : C.dn).text(pct(tot));
    });
    f.g.append("text").attr("x", f.iw + 6).attr("y", -5).text("Period");
    r.forEach(d => {
      const m = months[+d.m.slice(5) - 1], yv = d.m.slice(0, 4);
      f.g.append("rect").attr("x", x(m)).attr("y", y(yv)).attr("width", x.bandwidth()).attr("height", y.bandwidth()).attr("rx", 1.5).attr("fill", col(d.r));
      f.g.append("text").attr("x", x(m) + x.bandwidth() / 2).attr("y", y(yv) + y.bandwidth() / 2 + 3).attr("text-anchor", "middle").style("font-size", "6.3pt")
        .style("fill", Math.abs(d.r) > ext * .55 ? "#fff" : C.ink2).text((d.r * 100).toFixed(0));
    });
  };

  /* Bullet: consensus range vs guidance vs implied */
  K.range = (el, W, H) => {
    const cp = D.cons_periods.filter(p => p.code === "0q" || p.code === "+1q"), g = D.guide[D.guide.length - 1];
    const hist = D.guide.filter(x => x.act), beat = d3.mean(hist, x => x.act / x.mid - 1);
    const rows = cp.map(p => ({ lab: p.code === "0q" ? "Q3 FY27 (Oct)" : "Q4 FY27 (Jan)", lo: p.revenue.low, hi: p.revenue.high, avg: p.revenue.avg, n: p.revenue.n, ya: p.revenue.year_ago,
      glo: p.code === "0q" ? g.lo : null, ghi: p.code === "0q" ? g.hi : null, imp: p.code === "0q" ? g.mid * (1 + beat) : null }));
    const f = frame(el, W, H, { t: 26, r: 14, b: 22, l: 84 });
    const x = d3.scaleLinear().domain([d3.min(rows, r => Math.min(r.lo, r.glo || r.lo)) * .96, d3.max(rows, r => r.hi) * 1.03]).range([0, f.iw]).nice();
    const y = d3.scaleBand().domain(rows.map(r => r.lab)).range([0, f.ih]).padding(.45);
    const xa = f.g.append("g").attr("class", "grid").attr("transform", `translate(0,${f.ih})`).call(d3.axisBottom(x).ticks(6).tickSize(-f.ih).tickFormat(v => "$" + v / 1e9 + "B"));
    xa.selectAll("text").attr("dy", 9);
    rows.forEach(r => {
      const cy = y(r.lab), h = y.bandwidth();
      f.g.append("text").attr("class", "vl").attr("x", -8).attr("y", cy + h / 2 - 1).attr("text-anchor", "end").style("font-family", "Figtree").style("font-size", "7.6pt").text(r.lab);
      f.g.append("text").attr("x", -8).attr("y", cy + h / 2 + 9).attr("text-anchor", "end").text(`${r.n} estimates`);
      f.g.append("rect").attr("x", x(r.lo)).attr("width", x(r.hi) - x(r.lo)).attr("y", cy + h * .2).attr("height", h * .6).attr("fill", C.accXL).attr("stroke", C.accL);
      if (r.glo) {
        f.g.append("rect").attr("x", x(r.glo)).attr("width", x(r.ghi) - x(r.glo)).attr("y", cy - 3).attr("height", h + 6).attr("fill", "none").attr("stroke", C.amber).attr("stroke-width", 1.4).attr("stroke-dasharray", "3 2");
        f.g.append("text").attr("x", x(r.ghi) + 4).attr("y", cy + h + 2).style("fill", C.amber).text("Guided range ±2%");
        f.g.append("path").attr("d", d3.symbol(d3.symbolDiamond, 46)()).attr("transform", `translate(${x(r.imp)},${cy + h / 2})`).attr("fill", C.teal);
        f.g.append("text").attr("class", "vl").attr("x", x(r.imp)).attr("y", cy - 6).attr("text-anchor", "start").attr("dx", 6).style("fill", C.teal).text("implied " + bn(r.imp));
      }
      f.g.append("line").attr("x1", x(r.avg)).attr("x2", x(r.avg)).attr("y1", cy).attr("y2", cy + h).attr("stroke", C.navy).attr("stroke-width", 2.4);
      f.g.append("text").attr("class", "vl").attr("x", x(r.avg)).attr("y", cy - 6).attr("text-anchor", "end").attr("dx", -3).text("mean " + bn(r.avg));
    });
    legend(f, W < 420 ? [["Cons. range", C.accXL], ["Mean", C.navy, "line"], ["Implied by avg beat", C.teal, "dot"]] : [["Consensus low–high", C.accXL], ["Consensus mean", C.navy, "line"], ["Implied by 8-qtr avg beat", C.teal, "dot"]], -f.m.l + 2, -14);
  };

  /* Dumbbell: guided midpoint vs reported */
  K.dumbbell = (el, W, H) => {
    const r = D.guide.filter(d => d.act);
    const f = frame(el, W, H, { t: 18, r: 54, b: 18, l: 52 });
    const x = d3.scaleLinear().domain([0, d3.max(r, d => d.act) * 1.05]).range([0, f.iw]).nice();
    const y = d3.scaleBand().domain(r.map(d => d.p)).range([0, f.ih]).padding(.5);
    const xa = f.g.append("g").attr("class", "grid").attr("transform", `translate(0,${f.ih})`).call(d3.axisBottom(x).ticks(5).tickSize(-f.ih).tickFormat(v => "$" + v / 1e9 + "B"));
    xa.selectAll("text").attr("dy", 8);
    r.forEach(d => {
      const cy = y(d.p) + y.bandwidth() / 2;
      f.g.append("text").attr("class", "vl").attr("x", -8).attr("y", cy + 3).attr("text-anchor", "end").text(d.p);
      f.g.append("line").attr("x1", x(d.mid)).attr("x2", x(d.act)).attr("y1", cy).attr("y2", cy).attr("stroke", C.tealL).attr("stroke-width", 3);
      f.g.append("circle").attr("cx", x(d.mid)).attr("cy", cy).attr("r", 3.4).attr("fill", "#fff").attr("stroke", C.amber).attr("stroke-width", 1.5);
      f.g.append("circle").attr("cx", x(d.act)).attr("cy", cy).attr("r", 3.6).attr("fill", C.navy);
      f.g.append("text").attr("class", "vl").attr("x", x(d.act) + 7).attr("y", cy + 3).style("fill", C.up).text(pct(d.act / d.mid - 1, 1));
    });
    legend(f, [["Guided midpoint", C.amber, "dot"], ["Reported", C.navy, "dot"]], -f.m.l + 2, -6);
  };

  /* Rating mix over time */
  K.ratings = (el, W, H) => {
    const r = D.rating_hist;
    const f = frame(el, W, H, { t: 18, r: 6, b: 22, l: 24 });
    const x = d3.scaleBand().domain(r.map(d => d.month)).range([0, f.iw]).padding(.18);
    const y = d3.scaleLinear().domain([0, d3.max(r, d => d.n) * 1.12]).range([f.ih, 0]).nice();
    yGrid(f, y, 4, d3.format("d"));
    xAxis(f, x, d => mon(d + "-01").split(" ")[0], 2);
    r.forEach(d => {
      let acc = 0;
      [["buy", C.teal], ["hold", C.amberL], ["sell", C.dn]].forEach(([k, c]) => { if (!d[k]) return; f.g.append("rect").attr("x", x(d.month)).attr("width", x.bandwidth()).attr("y", y(acc + d[k])).attr("height", y(acc) - y(acc + d[k])).attr("fill", c); acc += d[k]; });
    });
    const L = r[r.length - 1];
    f.g.append("text").attr("class", "vl").attr("x", x(L.month) + x.bandwidth() / 2).attr("y", y(L.n) - 4).attr("text-anchor", "middle").text(`${L.buy}/${L.hold}/${L.sell}`);
    legend(f, [["Buy", C.teal], ["Hold", C.amberL], ["Sell", C.dn]], 0, -6);
  };

  /* Price target dot strip */
  K.targets = (el, W, H) => {
    const t = D.targets, price = D.price.close, med = d3.median(t, d => d.pt);
    const f = frame(el, W, H, { t: 30, r: 14, b: 22, l: 10 });
    const x = d3.scaleLinear().domain([Math.min(price, d3.min(t, d => d.pt)) * .9, d3.max(t, d => d.pt) * 1.05]).range([0, f.iw]).nice();
    const xa = f.g.append("g").attr("class", "grid").attr("transform", `translate(0,${f.ih})`).call(d3.axisBottom(x).ticks(5).tickSize(-f.ih).tickFormat(v => "$" + v));
    xa.selectAll("text").attr("dy", 9);
    const bins = d3.bin().domain(x.domain()).thresholds(20).value(d => d.pt)(t), rr = Math.min(3.6, f.ih / 2 / (d3.max(bins, b => b.length) + 1));
    bins.forEach(b => b.forEach((d, i) => f.g.append("circle").attr("cx", x((b.x0 + b.x1) / 2)).attr("cy", f.ih - rr - 1 - i * (rr * 2 + 1)).attr("r", rr).attr("fill", d.b === "buy" ? C.acc : C.amber)));
    [[price, "Last close $" + price.toFixed(0), C.dn, -18], [med, "Median target $" + med.toFixed(0), C.navy, -7]].forEach(([v, l, c, dy]) => {
      f.g.append("line").attr("x1", x(v)).attr("x2", x(v)).attr("y1", dy + 3).attr("y2", f.ih).attr("stroke", c).attr("stroke-dasharray", "3 2");
      f.g.append("text").attr("class", "vl").attr("x", x(v) + 3).attr("y", dy).style("fill", c).text(l);
    });
  };

  /* Next-day reaction bars */
  K.react = (el, W, H) => {
    const r = D.react, m = d3.mean(r, d => Math.abs(d.cc));
    const f = frame(el, W, H, { t: 12, r: 8, b: 22, l: 34 });
    const x = d3.scaleBand().domain(r.map(d => d.d)).range([0, f.iw]).padding(.22);
    const e = d3.max(r, d => Math.abs(d.cc)) * 1.1, y = d3.scaleLinear().domain([-e, e]).range([f.ih, 0]).nice();
    yGrid(f, y, 5, v => Math.round(v * 100) + "%");
    f.g.append("rect").attr("x", 0).attr("width", f.iw).attr("y", y(m)).attr("height", y(-m) - y(m)).attr("fill", C.soft);
    const xa = f.g.append("g").attr("transform", `translate(0,${f.ih})`).call(d3.axisBottom(x).tickSize(0).tickFormat((d, i) => (r.length - 1 - i) % (f.iw < 400 ? 8 : 4) ? "" : mon(d)));
    xa.select(".domain").remove(); xa.selectAll("text").attr("dy", 10);
    f.g.append("line").attr("x1", 0).attr("x2", f.iw).attr("y1", y(0)).attr("y2", y(0)).attr("stroke", C.ink2).attr("stroke-width", .6);
    f.g.selectAll("rect.b").data(r).join("rect").attr("x", d => x(d.d)).attr("width", x.bandwidth()).attr("y", d => y(Math.max(0, d.cc))).attr("height", d => Math.abs(y(d.cc) - y(0))).attr("fill", d => d.cc >= 0 ? C.up : C.dn);
    f.g.append("text").attr("x", f.iw).attr("y", -2).attr("text-anchor", "end").text(`grey band = average move ±${(m * 100).toFixed(1)}%`);
  };

  /* Margins */
  K.margins = (el, W, H) => {
    const r = D.rev;
    const f = frame(el, W, H, { t: 12, r: 74, b: 22, l: 30 });
    const x = d3.scalePoint().domain(r.map(d => d.end)).range([0, f.iw]);
    const y = d3.scaleLinear().domain([0, .85]).range([f.ih, 0]).nice();
    yGrid(f, y, 4, v => v * 100 + "%");
    xAxis(f, x, d => mon(d), 5);
    f.g.append("path").datum(r).attr("fill", C.accXL).attr("opacity", .6).attr("d", d3.area().x(d => x(d.end)).y0(d => y(d.om)).y1(d => y(d.gm)));
    const labs = [];
    [["gm", C.acc, "Gross"], ["om", C.teal, "Operating"]].forEach(([k, c, l]) => {
      f.g.append("path").datum(r).attr("fill", "none").attr("stroke", c).attr("stroke-width", 1.8).attr("d", d3.line().x(d => x(d.end)).y(d => y(d[k])));
      const L = r[r.length - 1]; labs.push({ y: y(L[k]), t: (L[k] * 100).toFixed(1) + "% " + (l === "Gross" ? "gross" : "op."), c });
    });
    nudge(labs, 10).forEach(l => f.g.append("text").attr("class", "vl").attr("x", f.iw + 5).attr("y", l.y + 3).style("fill", l.c).text(l.t));
  };

  /* Peer scatter with quadrant labels */
  K.peers = (el, W, H) => {
    const r = D.scatter;
    const f = frame(el, W, H, { t: 10, r: 10, b: 26, l: 32 });
    const x = d3.scaleLinear().domain(d3.extent(r, d => d.g)).range([0, f.iw]).nice();
    const y = d3.scaleLinear().domain(d3.extent(r, d => d.om)).range([f.ih, 0]).nice();
    const s = d3.scaleSqrt().domain([0, d3.max(r, d => d.mc)]).range([1.5, 13]);
    yGrid(f, y, 4, v => Math.round(v * 100) + "%");
    const xa = f.g.append("g").attr("class", "grid").attr("transform", `translate(0,${f.ih})`).call(d3.axisBottom(x).ticks(5).tickSize(-f.ih).tickFormat(v => Math.round(v * 100) + "%"));
    xa.selectAll("text").attr("dy", 9);
    const mx = d3.median(r, d => d.g), my = d3.median(r, d => d.om);
    f.g.append("rect").attr("x", x(mx)).attr("y", 0).attr("width", f.iw - x(mx)).attr("height", y(my)).attr("fill", C.accXL).attr("opacity", .35);
    f.g.append("text").attr("x", f.iw - 3).attr("y", y(my) - 4).attr("text-anchor", "end").text("above median on both");
    f.g.append("text").attr("x", f.iw).attr("y", f.ih + 22).attr("text-anchor", "end").text("revenue growth YoY →");
    const hl = new Set(["NVDA", "AVGO", "MSFT", "AAPL", "AMD"]);
    f.g.selectAll("circle").data([...r].sort((a, b) => b.mc - a.mc)).join("circle").attr("cx", d => x(d.g)).attr("cy", d => y(d.om)).attr("r", d => s(d.mc))
      .attr("fill", d => d.t === "NVDA" ? C.amber : hl.has(d.t) ? C.acc : C.accL).attr("fill-opacity", d => hl.has(d.t) ? .9 : .55).attr("stroke", "#fff").attr("stroke-width", .7);
    f.g.selectAll("text.t").data(r.filter(d => hl.has(d.t))).join("text").attr("class", "vl").attr("x", d => x(d.g) + s(d.mc) + 2).attr("y", d => y(d.om) + 3).text(d => d.t);
  };

  /* Geography bars with prior-year ghost */
  K.geo = (el, W, H) => {
    const r = [...D.geo].sort((a, b) => b.v - a.v);
    const f = frame(el, W, H, { t: 14, r: 70, b: 8, l: 110 });
    const x = d3.scaleLinear().domain([0, d3.max(r, d => Math.max(d.v, d.p))]).range([0, f.iw]);
    const y = d3.scaleBand().domain(r.map(d => d.k)).range([0, f.ih]).padding(.32);
    r.forEach(d => {
      const cy = y(d.k), h = y.bandwidth();
      f.g.append("text").attr("class", "vl").attr("x", -8).attr("y", cy + h / 2 + 3).attr("text-anchor", "end").style("font-family", "Figtree").text(d.k.replace(" (including Hong Kong)", " incl. HK"));
      f.g.append("rect").attr("x", 0).attr("y", cy - 2).attr("width", x(d.p)).attr("height", h + 4).attr("fill", "none").attr("stroke", C.faint).attr("stroke-dasharray", "2 2");
      f.g.append("rect").attr("x", 0).attr("y", cy).attr("width", x(d.v)).attr("height", h).attr("fill", C.navy);
      f.g.append("text").attr("class", "vl").attr("x", Math.max(x(d.v), x(d.p)) + 5).attr("y", cy + h / 2 + 3).text(bn(d.v));
      f.g.append("text").attr("x", Math.max(x(d.v), x(d.p)) + 40).attr("y", cy + h / 2 + 3).style("fill", d.v >= d.p ? C.up : C.dn).text(pct(d.v / d.p - 1));
    });
    legend(f, [["FY26", C.navy], ["FY25 (outline)", "#fff"]], -f.m.l + 2, -4);
  };

  /* Supply chain network: suppliers left, company center, customers/partners right, competitors below */
  K.network = (el, W, H) => {
    const L = D.links, sup = L.filter(l => l.rel === "supplier"), comp = L.filter(l => l.rel === "competitor");
    const cusMap = {};
    L.filter(l => l.rel === "customer" || l.rel === "partner").forEach(l => { (cusMap[l.cp] = cusMap[l.cp] || new Set()).add(l.rel); });
    const cus = Object.entries(cusMap).map(([cp, rels]) => ({ cp, sub: [...rels].sort().join(" and ") }));
    cus.push({ cp: "Direct customer A", sub: `${D.conc.pct}% of FY26 revenue (unnamed)` }, { cp: "Direct customer B", sub: "14% of FY26 revenue (unnamed)" });
    const f = frame(el, W, H, { t: 14, r: 6, b: 6, l: 6 });
    const top = f.ih * .74, cx = f.iw / 2, cy = top * .5, colL = 66, colR = f.iw - 76;
    const node = (x0, y0, label, sub, fill, txt, w = 124) => {
      f.g.append("rect").attr("x", x0 - w / 2).attr("y", y0 - 12).attr("width", w).attr("height", 24).attr("rx", 3).attr("fill", fill).attr("stroke", fill === "#fff" ? C.line : "none");
      f.g.append("text").attr("x", x0).attr("y", y0 - 1).attr("text-anchor", "middle").style("font-family", "Figtree").style("font-size", "7.4pt").style("font-weight", 600).style("fill", txt).text(label);
      if (sub) f.g.append("text").attr("x", x0).attr("y", y0 + 8).attr("text-anchor", "middle").style("font-size", "6pt").style("fill", txt === "#fff" ? "#C4CCE4" : C.muted).text(sub);
    };
    const edge = (x1, y1, x2, y2, c) => { const mx = (x1 + x2) / 2; f.g.append("path").attr("d", `M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`).attr("fill", "none").attr("stroke", c).attr("stroke-width", 1.2).attr("opacity", .7); };
    const ys = (n, a, b) => d3.range(n).map(i => n === 1 ? (a + b) / 2 : a + (b - a) * i / (n - 1));
    const sy = ys(sup.length, 14, top - 26), ry = ys(cus.length, cy - 52, cy + 52);
    sup.forEach((s, i) => edge(colL + 62, sy[i], cx - 78, cy, C.teal));
    cus.forEach((s, i) => edge(cx + 78, cy, colR - 72, ry[i], C.acc));
    sup.forEach((s, i) => node(colL, sy[i], s.cp + (s.t ? ` (${s.t})` : ""), s.det, "#fff", C.ink2));
    cus.forEach((s, i) => node(colR, ry[i], s.cp, s.sub, "#fff", C.ink2, 144));
    node(cx, cy, "NVIDIA (NVDA)", "fabless chip designer", C.navy, "#fff", 156);
    f.g.append("text").attr("x", colL).attr("y", 0).attr("text-anchor", "middle").style("fill", C.teal).style("font-weight", 600).text("SUPPLIERS");
    f.g.append("text").attr("x", colR).attr("y", cy - 70).attr("text-anchor", "middle").style("fill", C.acc).style("font-weight", 600).text("CUSTOMERS & PARTNERS");
    f.g.append("line").attr("x1", 0).attr("x2", f.iw).attr("y1", top).attr("y2", top).attr("stroke", C.line);
    f.g.append("text").attr("x", 0).attr("y", top + 13).style("font-weight", 600).text(`COMPETITORS NAMED IN FILINGS (${comp.length})`);
    let ox = 0, oy = top + 20;
    comp.forEach(c => {
      const label = c.t || c.cp, w = label.length * 5.4 + 14;
      if (ox + w > f.iw) { ox = 0; oy += 17; }
      f.g.append("rect").attr("x", ox).attr("y", oy).attr("width", w).attr("height", 13).attr("rx", 6.5).attr("fill", C.soft).attr("stroke", C.line);
      f.g.append("text").attr("x", ox + w / 2).attr("y", oy + 9).attr("text-anchor", "middle").style("fill", C.ink2).text(label);
      ox += w + 5;
    });
  };

  /* Sparkline inside tables and tiles */
  K.spark = (el, W, H) => {
    const v = JSON.parse(el.dataset.series), f = frame(el, W, H, { t: 2, r: 3, b: 2, l: 1 });
    const pts = v.map((y, i) => [i, y]).filter(p => p[1] != null);
    const x = d3.scaleLinear().domain([0, v.length - 1]).range([0, f.iw]), y = d3.scaleLinear().domain(d3.extent(pts, p => p[1])).range([f.ih, 0]);
    if (el.dataset.style === "bar") {
      const bw = f.iw / v.length * .7;
      pts.forEach(p => f.g.append("rect").attr("x", x(p[0]) - bw / 2).attr("width", bw).attr("y", y(p[1])).attr("height", f.ih - y(p[1]) + .5).attr("fill", p[0] === v.length - 1 ? C.acc : C.accL));
      return;
    }
    f.g.append("path").datum(pts).attr("fill", "none").attr("stroke", C.acc).attr("stroke-width", 1.1).attr("d", d3.line().x(p => x(p[0])).y(p => y(p[1])));
    const L = pts[pts.length - 1]; f.g.append("circle").attr("cx", x(L[0])).attr("cy", y(L[1])).attr("r", 1.8).attr("fill", C.acc);
  };

  /* Cover hero */
  K.cover = (el, W, H) => {
    const r = D.rev.slice(-16), g = D.guide[D.guide.length - 1];
    const rows = [...r.map(d => ({ k: d.end, v: d.rev })), { k: g.end, v: g.mid, guide: true }];
    const f = frame(el, W, H, { t: 18, r: 0, b: 16, l: 0 });
    const x = d3.scaleBand().domain(rows.map(d => d.k)).range([0, f.iw]).padding(.22);
    const y = d3.scaleLinear().domain([0, d3.max(rows, d => d.v) * 1.05]).range([f.ih, 0]);
    f.g.selectAll("rect").data(rows).join("rect").attr("x", d => x(d.k)).attr("y", d => y(d.v)).attr("width", x.bandwidth()).attr("height", d => f.ih - y(d.v))
      .attr("fill", (d, i) => d.guide ? "none" : i === rows.length - 2 ? "#8FA4FF" : "#2E3F78").attr("stroke", d => d.guide ? "#8FA4FF" : "none").attr("stroke-dasharray", "3 2");
    f.g.append("line").attr("x1", 0).attr("x2", f.iw).attr("y1", f.ih).attr("y2", f.ih).attr("stroke", "rgba(255,255,255,.25)");
    f.g.append("text").attr("x", 0).attr("y", f.ih + 12).text(mon(rows[0].k));
    f.g.append("text").attr("x", f.iw).attr("y", f.ih + 12).attr("text-anchor", "end").text("Q3 FY27 guide $" + (g.mid / 1e9).toFixed(0) + "B");
    f.g.append("text").attr("x", 0).attr("y", -6).text("Quarterly revenue, $B");
  };


  /* Candlesticks with 20/50-day averages, volume pane and earnings markers */
  K.candles = (el, W, H) => {
    const r = D.ohlc.map(([d, o, h, l, c, v]) => ({ d, o, h, l, c, v }));
    const f = frame(el, W, H, { t: 14, r: 46, b: 22, l: 6 });
    const vh = f.ih * .2, ph = f.ih - vh - 8;
    const x = d3.scaleBand().domain(r.map(d => d.d)).range([0, f.iw]).padding(.25);
    const y = d3.scaleLinear().domain([d3.min(r, d => d.l) * .96, d3.max(r, d => d.h) * 1.03]).range([ph, 0]).nice();
    const yv = d3.scaleLinear().domain([0, d3.max(r, d => d.v)]).range([f.ih, f.ih - vh]);
    const g = f.g.append("g").attr("class", "grid").attr("transform", `translate(${f.iw},0)`).call(d3.axisRight(y).ticks(6).tickSize(-f.iw).tickFormat(v => "$" + v));
    g.selectAll("text").attr("x", 6);
    const months = r.filter((d, i) => i && d.d.slice(5, 7) !== r[i - 1].d.slice(5, 7));
    months.forEach(m => f.g.append("text").attr("x", x(m.d)).attr("y", f.ih + 12).text(mon(m.d).split(" ")[0]));
    f.g.append("line").attr("x1", 0).attr("x2", f.iw).attr("y1", f.ih).attr("y2", f.ih).attr("stroke", C.ink2).attr("stroke-width", .6);
    r.forEach(d => {
      const cx = x(d.d) + x.bandwidth() / 2, up = d.c >= d.o, col = up ? C.up : C.dn;
      f.g.append("line").attr("x1", cx).attr("x2", cx).attr("y1", y(d.h)).attr("y2", y(d.l)).attr("stroke", col).attr("stroke-width", .6);
      f.g.append("rect").attr("x", x(d.d)).attr("width", Math.max(.8, x.bandwidth())).attr("y", y(Math.max(d.o, d.c))).attr("height", Math.max(.6, Math.abs(y(d.o) - y(d.c)))).attr("fill", up ? "#fff" : col).attr("stroke", col).attr("stroke-width", .5);
      f.g.append("rect").attr("x", x(d.d)).attr("width", Math.max(.8, x.bandwidth())).attr("y", yv(d.v)).attr("height", f.ih - yv(d.v)).attr("fill", up ? C.tealL : "#F3C3C5");
    });
    const ma = n => r.map((d, i) => i < n - 1 ? null : { d: d.d, v: d3.mean(r.slice(i - n + 1, i + 1), z => z.c) }).filter(Boolean);
    [[20, C.acc, null], [50, C.amber, "4 2"]].forEach(([n, c, dash]) => f.g.append("path").datum(ma(n)).attr("fill", "none").attr("stroke", c).attr("stroke-width", 1.2).attr("stroke-dasharray", dash)
      .attr("d", d3.line().x(d => x(d.d) + x.bandwidth() / 2).y(d => y(d.v))));
    const ev = D.react.filter(e => e.d >= r[0].d);
    ev.forEach(e => {
      const k = r.find(d => d.d >= e.d); if (!k) return;
      const cx = x(k.d) + x.bandwidth() / 2;
      f.g.append("line").attr("x1", cx).attr("x2", cx).attr("y1", 0).attr("y2", ph).attr("stroke", C.faint).attr("stroke-dasharray", "2 2");
      f.g.append("text").attr("class", "vl").attr("x", cx + 3).attr("y", 8).style("fill", e.cc >= 0 ? C.up : C.dn).text("Results " + pct(e.cc));
    });
    const L = r[r.length - 1];
    f.g.append("text").attr("class", "vl").attr("x", f.iw + 6).attr("y", y(L.c) + 3).style("fill", C.navy).text("$" + L.c.toFixed(2));
    legend(f, [["Up day", C.up], ["Down day", C.dn], ["20-day avg", C.acc, "line"], ["50-day avg", C.amber, "line"]], 0, -4);
  };

  /* Trailing P/E history with median and band */
  K.pehist = (el, W, H) => {
    const r = D.pe_hist, vs = r.map(d => d.pe).sort(d3.ascending), med = d3.median(vs), q1 = d3.quantile(vs, .25), q3 = d3.quantile(vs, .75);
    const f = frame(el, W, H, { t: 12, r: 52, b: 22, l: 28 });
    const x = d3.scalePoint().domain(r.map(d => d.m)).range([0, f.iw]);
    const y = d3.scaleLinear().domain([0, d3.max(vs) * 1.1]).range([f.ih, 0]).nice();
    yGrid(f, y, 4, v => v + "×");
    xAxis(f, x, d => mon(d + "-01"), 6);
    f.g.append("rect").attr("x", 0).attr("width", f.iw).attr("y", y(q3)).attr("height", y(q1) - y(q3)).attr("fill", C.soft);
    f.g.append("line").attr("x1", 0).attr("x2", f.iw).attr("y1", y(med)).attr("y2", y(med)).attr("stroke", C.muted).attr("stroke-dasharray", "3 2");
    f.g.append("text").attr("x", f.iw + 4).attr("y", y(med) + 3).text("median " + med.toFixed(0) + "×");
    f.g.append("path").datum(r).attr("fill", "none").attr("stroke", C.navy).attr("stroke-width", 1.6).attr("d", d3.line().x(d => x(d.m)).y(d => y(d.pe)));
    const L = r[r.length - 1];
    f.g.append("circle").attr("cx", x(L.m)).attr("cy", y(L.pe)).attr("r", 3).attr("fill", C.amber);
    f.g.append("text").attr("class", "vl").attr("x", f.iw + 4).attr("y", y(L.pe) + 12).style("fill", C.amber).text("now " + L.pe.toFixed(1) + "×");
  };

  document.querySelectorAll(".chart[data-kind]").forEach(el => {
    const fn = K[el.dataset.kind];
    try { if (fn) fn(el, +el.dataset.w, +el.dataset.h || 180); } catch (e) { console.error(el.dataset.kind, e.message); }
  });
})();
