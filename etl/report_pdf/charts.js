(() => {
  const D = window.__D;
  const C = { acc: "#2B4ACB", acc2: "#0E9384", acc3: "#D97706", up: "#0A8754", dn: "#D12F35", ink2: "#2A2E36", muted: "#5F6673", line: "#E3E5EA", soft: "#C9D2F4" };
  const mon = s => { const d = new Date(s + "T00:00:00Z"); return d.toLocaleString("en-US", { month: "short", timeZone: "UTC" }) + " '" + String(d.getUTCFullYear()).slice(2); };
  const pct = (v, n = 0) => (v >= 0 ? "+" : "−") + Math.abs(v * 100).toFixed(n) + "%";
  function frame(el, W, H, m) {
    const svg = d3.select(el).append("svg").attr("width", W).attr("height", H).attr("viewBox", `0 0 ${W} ${H}`);
    return { svg, g: svg.append("g").attr("transform", `translate(${m.l},${m.t})`), iw: W - m.l - m.r, ih: H - m.t - m.b };
  }
  function yGrid(f, y, n, fmt) {
    const g = f.g.append("g").attr("class", "grid").call(d3.axisLeft(y).ticks(n).tickSize(-f.iw).tickFormat(fmt));
    g.selectAll("text").attr("x", -5);
  }
  const K = {};

    K.rev = (el, W, H) => {
    const r = D.rev.slice(-12), g = D.guide[D.guide.length - 1];
    const rows = [...r.map(d => ({ k: d.end, v: d.rev, yoy: d.yoy })), { k: g.end, v: g.mid, lo: g.lo, hi: g.hi, guide: true }];
    const f = frame(el, W, H || 215, { t: 14, r: 44, b: 22, l: 36 });
    const x = d3.scaleBand().domain(rows.map(d => d.k)).range([0, f.iw]).padding(.3);
    const y = d3.scaleLinear().domain([0, d3.max(rows, d => d.hi || d.v) * 1.08]).range([f.ih, 0]).nice();
    const yy = d3.scaleLinear().domain([0, d3.max(r, d => d.yoy) * 1.1]).range([f.ih, 0]).nice();
    yGrid(f, y, 5, v => "$" + v / 1e9);
    const ra = f.g.append("g").attr("transform", `translate(${f.iw},0)`).call(d3.axisRight(yy).ticks(5).tickSize(0).tickFormat(v => v * 100 + "%"));
    ra.select(".domain").remove(); ra.selectAll("text").attr("x", 6);
    const xa = f.g.append("g").attr("transform", `translate(0,${f.ih})`).call(d3.axisBottom(x).tickSize(0).tickFormat((d, i) => i % 2 ? "" : (rows[i].guide ? "Q3 FY27E" : mon(d))));
    xa.select(".domain").attr("stroke", C.line); xa.selectAll("text").attr("dy", 10);
    f.g.selectAll("rect.b").data(rows).join("rect").attr("x", d => x(d.k)).attr("y", d => y(d.v)).attr("width", x.bandwidth()).attr("height", d => f.ih - y(d.v))
      .attr("fill", d => d.guide ? "white" : C.acc).attr("fill-opacity", (d, i) => d.guide ? 1 : i === r.length - 1 ? 1 : .5)
      .attr("stroke", d => d.guide ? C.acc : "none").attr("stroke-dasharray", d => d.guide ? "3 2" : null);
    const gx = x(g.end) + x.bandwidth() / 2;
    f.g.append("line").attr("x1", gx).attr("x2", gx).attr("y1", y(g.hi)).attr("y2", y(g.lo)).attr("stroke", C.ink2).attr("stroke-width", 1.2);
    [g.lo, g.hi].forEach(v => f.g.append("line").attr("x1", gx - 5).attr("x2", gx + 5).attr("y1", y(v)).attr("y2", y(v)).attr("stroke", C.ink2));
    f.g.append("text").attr("class", "vl").attr("x", gx).attr("y", y(g.hi) - 5).attr("text-anchor", "middle").text("$" + (g.mid / 1e9).toFixed(0) + "B guide");
    const lr = r[r.length - 1];
    f.g.append("text").attr("class", "vl").attr("x", x(lr.end) + x.bandwidth() / 2).attr("y", y(lr.rev) - 5).attr("text-anchor", "middle").text("$" + (lr.rev / 1e9).toFixed(1) + "B");
    const line = d3.line().x(d => x(d.end) + x.bandwidth() / 2).y(d => yy(d.yoy)).curve(d3.curveMonotoneX);
    f.g.append("path").datum(r).attr("fill", "none").attr("stroke", C.acc3).attr("stroke-width", 1.8).attr("d", line);
    f.g.selectAll("circle").data(r).join("circle").attr("cx", d => x(d.end) + x.bandwidth() / 2).attr("cy", d => yy(d.yoy)).attr("r", 2).attr("fill", C.acc3);
    f.g.append("text").attr("class", "vl").attr("x", x(lr.end) - x.bandwidth() * 0.7).attr("y", yy(lr.yoy) - 7).attr("text-anchor", "end").style("fill", C.acc3).text(pct(lr.yoy) + " YoY");
  };

  K.guide = (el, W, H) => {
    const r = D.guide;
    const f = frame(el, W, H || 200, { t: 16, r: 8, b: 22, l: 36 });
    const x = d3.scaleBand().domain(r.map(d => d.p)).range([0, f.iw]).padding(.35);
    const y = d3.scaleLinear().domain([0, d3.max(r, d => Math.max(d.hi, d.act || 0)) * 1.1]).range([f.ih, 0]).nice();
    yGrid(f, y, 5, v => "$" + v / 1e9);
    const xa = f.g.append("g").attr("transform", `translate(0,${f.ih})`).call(d3.axisBottom(x).tickSize(0));
    xa.select(".domain").attr("stroke", C.line); xa.selectAll("text").attr("dy", 10);
    r.forEach(d => {
      const cx = x(d.p), w = x.bandwidth();
      if (d.act) f.g.append("rect").attr("x", cx).attr("y", y(d.act)).attr("width", w).attr("height", f.ih - y(d.act)).attr("fill", C.acc).attr("fill-opacity", .85);
      f.g.append("rect").attr("x", cx - 4).attr("y", y(d.hi)).attr("width", w + 8).attr("height", Math.max(2, y(d.lo) - y(d.hi))).attr("fill", C.acc3).attr("fill-opacity", .35).attr("stroke", C.acc3);
      f.g.append("line").attr("x1", cx - 4).attr("x2", cx + w + 4).attr("y1", y(d.mid)).attr("y2", y(d.mid)).attr("stroke", C.acc3).attr("stroke-width", 1.4);
      f.g.append("text").attr("class", "vl").attr("x", cx + w / 2).attr("y", y(Math.max(d.hi, d.act || 0)) - 5).attr("text-anchor", "middle")
        .style("fill", d.act ? C.up : C.muted).text(d.act ? pct(d.act / d.mid - 1, 1) : "pending");
    });
    const lg = f.g.append("g").attr("transform", "translate(4,-8)");
    lg.append("rect").attr("width", 9).attr("height", 7).attr("fill", C.acc); lg.append("text").attr("x", 13).attr("y", 7).text("Reported");
    lg.append("rect").attr("x", 70).attr("width", 9).attr("height", 7).attr("fill", C.acc3).attr("fill-opacity", .35).attr("stroke", C.acc3); lg.append("text").attr("x", 83).attr("y", 7).text("Guided range (line = midpoint)");
  };

  K.targets = (el, W, H) => {
    const t = D.targets, price = D.price.close;
    const f = frame(el, W, H || 160, { t: 32, r: 14, b: 24, l: 10 });
    const x = d3.scaleLinear().domain([Math.min(price, d3.min(t, d => d.pt)) * .9, d3.max(t, d => d.pt) * 1.05]).range([0, f.iw]).nice();
    const xa = f.g.append("g").attr("class", "grid").attr("transform", `translate(0,${f.ih})`).call(d3.axisBottom(x).ticks(5).tickSize(-f.ih).tickFormat(v => "$" + v));
    xa.selectAll("text").attr("dy", 9);
    const bins = d3.bin().domain(x.domain()).thresholds(18).value(d => d.pt)(t);
    bins.forEach(b => b.forEach((d, i) => f.g.append("circle").attr("cx", x((b.x0 + b.x1) / 2)).attr("cy", f.ih - 6 - i * 9).attr("r", 3.6).attr("fill", d.b === "buy" ? C.acc : C.muted).attr("fill-opacity", .8)));
    const med = d3.median(t, d => d.pt);
    [[price, "Close $" + price.toFixed(0), C.dn], [med, "Median $" + med.toFixed(0), C.ink2]].forEach(([v, l, c], i) => {
      f.g.append("line").attr("x1", x(v)).attr("x2", x(v)).attr("y1", -4).attr("y2", f.ih).attr("stroke", c).attr("stroke-dasharray", "3 2");
      f.g.append("text").attr("class", "vl").attr("x", x(v)).attr("y", -9 - i * 0).attr("text-anchor", "start").attr("dx", 3).attr("dy", i ? 0 : -10).style("fill", c).text(l);
    });
  };

  K.react = (el, W, H) => {
    const r = D.react, m = d3.mean(r, d => Math.abs(d.cc));
    const f = frame(el, W, H || 165, { t: 10, r: 8, b: 22, l: 36 });
    const x = d3.scaleBand().domain(r.map(d => d.d)).range([0, f.iw]).padding(.22);
    const e = d3.max(r, d => Math.abs(d.cc)) * 1.1, y = d3.scaleLinear().domain([-e, e]).range([f.ih, 0]).nice();
    yGrid(f, y, 6, v => Math.round(v * 100) + "%");
    [m, -m].forEach(v => f.g.append("line").attr("x1", 0).attr("x2", f.iw).attr("y1", y(v)).attr("y2", y(v)).attr("stroke", C.muted).attr("stroke-dasharray", "3 3"));
    f.g.append("text").attr("x", 3).attr("y", y(-m) + 10).text(`average ±${(m * 100).toFixed(1)}%`);
    const xa = f.g.append("g").attr("transform", `translate(0,${f.ih})`).call(d3.axisBottom(x).tickSize(0).tickFormat((d, i) => (r.length - 1 - i) % 4 ? "" : mon(d)));
    xa.select(".domain").remove(); xa.selectAll("text").attr("dy", 10);
    f.g.append("line").attr("x1", 0).attr("x2", f.iw).attr("y1", y(0)).attr("y2", y(0)).attr("stroke", C.ink2).attr("stroke-width", .6);
    f.g.selectAll("rect").data(r).join("rect").attr("x", d => x(d.d)).attr("width", x.bandwidth()).attr("y", d => y(Math.max(0, d.cc))).attr("height", d => Math.abs(y(d.cc) - y(0))).attr("fill", d => d.cc >= 0 ? C.up : C.dn);
  };

  K.margins = (el, W, H) => {
    const r = D.rev;
    const f = frame(el, W, H || 170, { t: 10, r: 58, b: 22, l: 30 });
    const x = d3.scalePoint().domain(r.map(d => d.end)).range([0, f.iw]);
    const y = d3.scaleLinear().domain([Math.min(0, d3.min(r, d => d.om)), 0.85]).range([f.ih, 0]).nice();
    yGrid(f, y, 4, v => v * 100 + "%");
    const xa = f.g.append("g").attr("transform", `translate(0,${f.ih})`).call(d3.axisBottom(x).tickSize(0).tickFormat((d, i) => (r.length - 1 - i) % 5 ? "" : mon(d)));
    xa.select(".domain").remove(); xa.selectAll("text").attr("dy", 10);
    [["gm", C.acc, "Gross"], ["om", C.acc2, "Operating"]].forEach(([k, c, l]) => {
      f.g.append("path").datum(r).attr("fill", "none").attr("stroke", c).attr("stroke-width", 1.8).attr("d", d3.line().x(d => x(d.end)).y(d => y(d[k])));
      const L = r[r.length - 1];
      f.g.append("text").attr("class", "vl").attr("x", f.iw + 4).attr("y", y(L[k]) + 3).style("fill", c).text((L[k] * 100).toFixed(0) + "% " + l);
    });
  };

  K.peers = (el, W, H) => {
    const r = D.scatter;
    const f = frame(el, W, H || 170, { t: 10, r: 10, b: 24, l: 30 });
    const x = d3.scaleLinear().domain(d3.extent(r, d => d.g)).range([0, f.iw]).nice();
    const y = d3.scaleLinear().domain(d3.extent(r, d => d.om)).range([f.ih, 0]).nice();
    const s = d3.scaleSqrt().domain([0, d3.max(r, d => d.mc)]).range([1.5, 14]);
    yGrid(f, y, 4, v => Math.round(v * 100) + "%");
    const xa = f.g.append("g").attr("class", "grid").attr("transform", `translate(0,${f.ih})`).call(d3.axisBottom(x).ticks(5).tickSize(-f.ih).tickFormat(v => Math.round(v * 100) + "%"));
    xa.selectAll("text").attr("dy", 9);
    const mx = d3.median(r, d => d.g), my = d3.median(r, d => d.om);
    f.g.append("line").attr("x1", x(mx)).attr("x2", x(mx)).attr("y1", 0).attr("y2", f.ih).attr("stroke", C.muted).attr("stroke-dasharray", "3 3");
    f.g.append("line").attr("x1", 0).attr("x2", f.iw).attr("y1", y(my)).attr("y2", y(my)).attr("stroke", C.muted).attr("stroke-dasharray", "3 3");
    const hl = new Set(["NVDA", "AVGO", "MSFT", "AAPL", "AMD"]);
    f.g.selectAll("circle").data([...r].sort((a, b) => b.mc - a.mc)).join("circle").attr("cx", d => x(d.g)).attr("cy", d => y(d.om)).attr("r", d => s(d.mc))
      .attr("fill", d => d.t === "NVDA" ? C.acc3 : hl.has(d.t) ? C.acc : C.soft).attr("fill-opacity", d => hl.has(d.t) ? .85 : .9).attr("stroke", "white").attr("stroke-width", .7);
    f.g.selectAll("text.t").data(r.filter(d => hl.has(d.t))).join("text").attr("class", "vl").attr("x", d => x(d.g) + s(d.mc) + 2).attr("y", d => y(d.om) + 3).text(d => d.t);
    };

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
  document.querySelectorAll(".chart[data-kind]").forEach(el => {
    const fn = K[el.dataset.kind];
    if (fn) fn(el, +el.dataset.w, +el.dataset.h || null);
  });
})();
