// Supply-chain visuals (d3): network map (side or force layout, second-order ring, overlays, shock ripple), flow
// (Sankey-style bands sized by disclosed dollars or stated shares) and the world exposure map (d3-geo + world-atlas).
(function () {
  "use strict";
  const C = window.FU_SUPPLY_CORE;
  const REL_COLOR = { customer: "var(--c1)", supplier: "var(--c3)", partner: "var(--up)", competitor: "var(--down)", focus: "var(--ink)" };
  const REL_LABEL = { customer: "Customers", supplier: "Suppliers", partner: "Partners", competitor: "Competitors" };
  const NS = "http://www.w3.org/2000/svg";
  const trunc = (s, n) => (s = String(s || "")).length > n ? s.slice(0, n - 1) + "…" : s;
  const money = v => v == null ? "" : fmtMoney(v);
  const flagged = (o, r) => !r ? false : o === "single" ? r.singleSource : o === "concentration" ? r.concentrated : o === "geo" ? r.hotspots.length > 0 : o === "dollars" ? r.dollars != null : false;
  const impactKey = n => n.ticker || C.normText(n.name);
  const initials = n => (n.ticker || n.name).replace(/[^A-Za-z0-9]/g, "").slice(0, 3).toUpperCase();

  function nodeCard(g, n, o) {
    const w = o.focus ? 200 : o.hop2 ? 168 : 186, h = o.focus ? 48 : o.hop2 ? 34 : 40;
    g.attr("class", "sv-node" + (o.focus ? " focus" : "") + (o.hop2 ? " hop2" : "") + (o.sel ? " sel" : "") + (o.hit ? " hit" : "") + (o.dim ? " dim" : "") + (o.impact ? " imp-" + o.impact.severity : ""))
      .attr("tabindex", o.focus ? null : 0).attr("role", o.focus ? null : "button").attr("aria-label", `${n.name}, ${n.relation}`);
    g.append("rect").attr("x", -w / 2).attr("y", -h / 2).attr("width", w).attr("height", h).attr("rx", 6).attr("class", "bg");
    if (!o.focus) g.append("rect").attr("x", -w / 2).attr("y", -h / 2).attr("width", 3.5).attr("height", h).attr("rx", 1.5).attr("fill", REL_COLOR[n.relation]);
    const lx = -w / 2 + (o.focus ? 12 : 10), ls = o.focus ? 26 : o.hop2 ? 18 : 22;
    const url = n.ticker && typeof logoUrl === "function" ? logoUrl(n.ticker) : null;
    if (url) g.append("image").attr("href", url).attr("x", lx).attr("y", -ls / 2).attr("width", ls).attr("height", ls).attr("preserveAspectRatio", "xMidYMid meet");
    else {
      g.append("rect").attr("x", lx).attr("y", -ls / 2).attr("width", ls).attr("height", ls).attr("rx", 4).attr("class", "ini");
      g.append("text").attr("x", lx + ls / 2).attr("y", 3.5).attr("text-anchor", "middle").attr("class", "ini-t").text(initials(n));
    }
    const tx = lx + ls + 7;
    g.append("text").attr("x", tx).attr("y", o.focus ? -3 : o.hop2 ? -2 : -3).attr("class", "nm").text(trunc(n.name, o.focus ? 22 : o.hop2 ? 19 : 21));
    const share = C.maxShare(n), r = o.risk;
    const bits = [n.ticker || (n.hq ? n.hq : "private / other")];
    if (share > 0) bits.push(share + "%"); else if (n.links.some(l => l.ge10)) bits.push("≥10%");
    if (r?.dollars != null) bits.push(money(r.dollars));
    if (o.impact?.revenuePct != null) bits.push("−" + (o.impact.revenuePct * 100).toFixed(1) + "% rev");
    else if (o.impact) bits.push(o.impact.channel === "lost sales" ? "lost sales" : "disruption");
    const sub = g.append("text").attr("x", tx).attr("y", o.focus ? 13 : o.hop2 ? 10 : 11).attr("class", "sb mono").text(trunc(bits.join(" · "), 30));
    if (r?.singleSource) g.append("text").attr("x", w / 2 - 8).attr("y", -h / 2 + 11).attr("text-anchor", "end").attr("class", "flag").text("SOLE");
    if (r && r.hotspots.length) g.append("circle").attr("cx", w / 2 - 7).attr("cy", h / 2 - 8).attr("r", 3).attr("class", "hot").append("title").text(r.hotspots.join(", "));
    if (o.corro) g.append("text").attr("x", w / 2 - 8).attr("y", h / 2 - 5).attr("text-anchor", "end").attr("class", "corro").text("✓✓").append("title").text("Both companies' filings state this link");
    return { w, h, sub };
  }

  /** Network map: suppliers left, customers right, partners above, competitors below (side) or a relation-anchored force layout. */
  function network(host, o) {
    host.innerHTML = "";
    const W = host.clientWidth || 900, H = host.clientHeight || 600;
    const svg = d3.select(host).append("svg").attr("class", "sv-net").attr("width", W).attr("height", H).attr("viewBox", `0 0 ${W} ${H}`);
    const defs = svg.append("defs");
    defs.append("pattern").attr("id", "sv-grid").attr("width", 24).attr("height", 24).attr("patternUnits", "userSpaceOnUse").append("circle").attr("cx", 1).attr("cy", 1).attr("r", 1).attr("class", "gridpt");
    svg.append("rect").attr("width", W).attr("height", H).attr("fill", "url(#sv-grid)");
    const root = svg.append("g");
    const zoom = d3.zoom().scaleExtent([0.2, 2.5]).on("zoom", e => root.attr("transform", e.transform));
    svg.call(zoom).on("dblclick.zoom", null);
    const nodes = [{ ...o.focus, x: 0, y: 0, isFocus: true }], pos = new Map();
    const shown = o.graph.filter(n => o.visible.has(n.relation));
    for (const r of C.RELATIONS) {
      if (!o.visible.has(r)) continue;
      const group = shown.filter(n => n.relation === r).slice(0, 30), p = C.sidePositions(group.length, r);
      group.forEach((n, i) => { nodes.push({ ...n, x: p[i].x, y: p[i].y }); pos.set(n.key, p[i]); });
      if (r === "supplier" || r === "customer") {
        const kids = (o.hop2 || []).filter(h => h.relation === r && pos.has(h.parent)), cols = Math.ceil(Math.min(group.length, 30) / 10), dir = r === "supplier" ? -1 : 1, p2 = C.sidePositions(kids.length, r);
        kids.forEach((k, i) => nodes.push({ ...k, x: p2[i].x + dir * (cols * 214 + 30), y: p2[i].y * 0.8, isHop2: true }));
      }
    }
    if (o.layout === "force" && nodes.length > 2) {
      const anchor = { supplier: [-320, 0], customer: [320, 0], partner: [0, -260], competitor: [0, 260] };
      const sim = d3.forceSimulation(nodes).force("x", d3.forceX(d => d.isFocus ? 0 : (anchor[d.relation] || [0, 0])[0] * (d.isHop2 ? 1.7 : 1)).strength(d => d.isFocus ? 1 : 0.18))
        .force("y", d3.forceY(d => d.isFocus ? 0 : (anchor[d.relation] || [0, 0])[1]).strength(d => d.isFocus ? 1 : 0.12))
        .force("collide", d3.forceCollide(d => d.isFocus ? 110 : 98).strength(0.9)).force("charge", d3.forceManyBody().strength(-40)).stop();
      nodes[0].fx = 0; nodes[0].fy = 0;
      for (let i = 0; i < 260; i++) sim.tick();
    }
    const byKey = new Map(nodes.map(n => [n.isFocus ? "focus" : n.key, n]));
    const dimOf = n => o.impacts ? !o.impacts.has(impactKey(n)) : false;
    const edges = root.append("g").attr("class", "sv-edges");
    for (const n of nodes) {
      if (n.isFocus) continue;
      const parent = n.isHop2 ? byKey.get(n.parent) : nodes[0];
      if (!parent) continue;
      const a = n.relation === "supplier" ? n : parent, b = n.relation === "supplier" ? parent : n;
      const share = C.maxShare(n), hot = flagged(o.overlay, o.risks[n.key]);
      const mx = (a.x + b.x) / 2, path = n.relation === "partner" || n.relation === "competitor" ? `M${a.x},${a.y} C${a.x},${(a.y + b.y) / 2} ${b.x},${(a.y + b.y) / 2} ${b.x},${b.y}` : `M${a.x},${a.y} C${mx},${a.y} ${mx},${b.y} ${b.x},${b.y}`;
      edges.append("path").attr("d", path).attr("class", "sv-edge " + n.relation + (hot ? " hot" : "") + (n.isHop2 ? " h2" : "") + ((o.selected && o.selected !== n.key && o.selected !== n.parent) || dimOf(n) ? " dim" : ""))
        .attr("stroke-width", n.isHop2 ? 1 + Math.min(share, 40) / 8 : 1.2 + Math.min(share, 40) / 6);
    }
    const layer = root.append("g");
    for (const n of nodes) {
      const g = layer.append("g").attr("transform", `translate(${n.x},${n.y})`);
      const risk = o.risks[n.key];
      nodeCard(g, n, { focus: n.isFocus, hop2: n.isHop2, sel: o.selected === n.key, hit: !n.isFocus && flagged(o.overlay, risk), dim: !n.isFocus && dimOf(n), risk, impact: n.isFocus ? null : o.impacts?.get(impactKey(n)), corro: !n.isFocus && n.links.some(l => C.confidence(l, o.all) === "corroborated") });
      if (!n.isFocus) {
        g.on("click", e => { e.stopPropagation(); o.onSelect(n.key); }).on("keydown", e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); o.onSelect(n.key); } });
        g.on("mouseenter", e => showTip(e, `<b>${esc(n.name)}</b><br>${esc(n.relation)}${n.isHop2 ? " (2nd order)" : ""} · ${n.links.length} statement${n.links.length > 1 ? "s" : ""}<br><span class="faint">Click for evidence</span>`)).on("mouseleave", hideTip);
      }
    }
    // shock ripple from the shocked company
    if (o.impacts && o.shockKey) {
      const src = o.shockKey === "focus" ? nodes[0] : nodes.find(n => n.ticker === o.shockKey) || nodes[0];
      const rip = root.insert("g", ":first-child").attr("class", "sv-ripple").attr("transform", `translate(${src.x},${src.y})`);
      [0, 1, 2].forEach(i => rip.append("circle").attr("r", 40).attr("style", `animation-delay:${i * 0.8}s`));
    }
    // fit
    const xs = nodes.map(n => n.x), ys = nodes.map(n => n.y);
    const x0 = Math.min(...xs) - 120, x1 = Math.max(...xs) + 120, y0 = Math.min(...ys) - 50, y1 = Math.max(...ys) + 50;
    const k = Math.min(1, 0.95 * Math.min(W / (x1 - x0), H / (y1 - y0)));
    svg.call(zoom.transform, d3.zoomIdentity.translate(W / 2 - k * (x0 + x1) / 2, H / 2 - k * (y0 + y1) / 2).scale(k));
    const ctl = el("div", { class: "sv-zoom" },
      el("button", { class: "iconbtn", "aria-label": "Zoom in", onclick: () => svg.transition().duration(200).call(zoom.scaleBy, 1.3) }, "+"),
      el("button", { class: "iconbtn", "aria-label": "Zoom out", onclick: () => svg.transition().duration(200).call(zoom.scaleBy, 1 / 1.3) }, "−"),
      el("button", { class: "iconbtn", "aria-label": "Fit", onclick: () => svg.transition().duration(200).call(zoom.transform, d3.zoomIdentity.translate(W / 2 - k * (x0 + x1) / 2, H / 2 - k * (y0 + y1) / 2).scale(k)) }, "⤢"));
    host.append(ctl);
    ["supplier", "customer", "partner", "competitor"].forEach(() => {});
    return { count: nodes.length - 1 };
  }

  /** Suppliers -> company -> customers; band width = disclosed dollars, else stated share, else equal. */
  function flow(host, o) {
    host.innerHTML = "";
    const W = 1000, H = Math.max(420, host.clientHeight || 600), NODE_W = 10, GAP = 6;
    const side = rel => {
      const nodes = o.graph.filter(n => n.relation === rel).map(n => {
        const d = Math.max(0, ...n.links.map(l => C.linkDollars(l, o.revenue) ?? 0)), s = Math.max(0, ...n.links.map(l => l.sh ?? 0));
        return { n, d, s, product: n.links.find(l => l.prod)?.prod || n.links[0]?.det || "" };
      }).sort((a, b) => b.d - a.d || b.s - a.s).slice(0, 16);
      const anyD = nodes.some(x => x.d > 0), maxD = Math.max(1, ...nodes.map(y => y.d));
      const w = nodes.map(x => anyD ? (x.d > 0 ? Math.max(0.15, Math.sqrt(x.d / maxD)) : 0.12) : x.s > 0 ? 0.3 + x.s / 25 : 0.3);
      const total = w.reduce((a, b) => a + b, 0) || 1, avail = H - 40 - GAP * Math.max(0, nodes.length - 1);
      let y = 20;
      return nodes.map((x, i) => { const h = Math.max(8, (w[i] / total) * avail); const r = { ...x, y, h }; y += h + GAP; return r; });
    };
    const sup = side("supplier"), cus = side("customer"), fx = W / 2 - NODE_W / 2;
    const stack = arr => { const tot = arr.reduce((a, b) => a + b.h, 0); let y = H / 2 - tot / 2; return arr.map(a => { const r = y; y += a.h; return r; }); };
    const supIn = stack(sup), cusOut = stack(cus), tot = Math.max(sup.reduce((a, b) => a + b.h, 0), cus.reduce((a, b) => a + b.h, 0), 60);
    const band = (x0, y0, x1, y1, h) => { const m = (x0 + x1) / 2; return `M${x0},${y0} C${m},${y0} ${m},${y1} ${x1},${y1} L${x1},${y1 + h} C${m},${y1 + h} ${m},${y0 + h} ${x0},${y0 + h} Z`; };
    const label = x => `${trunc(x.n.name, 26)}${x.d ? " · " + money(x.d) : x.s ? " · " + x.s + "%" : ""}`;
    const svg = d3.select(host).append("svg").attr("class", "sv-flow").attr("viewBox", `0 0 ${W} ${H}`).attr("preserveAspectRatio", "xMidYMid meet");
    const draw = (arr, ins, rel) => arr.forEach((x, i) => {
      const left = rel === "supplier", bx = left ? 270 : W - 280;
      const g = svg.append("g").attr("class", "sv-band" + (o.selected && o.selected !== x.n.key ? " dim" : "")).attr("tabindex", 0).attr("role", "button");
      g.append("path").attr("d", left ? band(270 + NODE_W, x.y, fx, ins[i], x.h) : band(fx + NODE_W, ins[i], W - 280, x.y, x.h)).attr("fill", REL_COLOR[rel]).attr("opacity", 0.26);
      g.append("rect").attr("x", bx).attr("y", x.y).attr("width", NODE_W).attr("height", x.h).attr("fill", REL_COLOR[rel]).attr("rx", 2);
      g.append("text").attr("x", left ? 262 : W - 272).attr("y", x.y + x.h / 2).attr("text-anchor", left ? "end" : "start").attr("dominant-baseline", "middle").attr("class", "nm").text(label(x));
      g.append("text").attr("x", left ? 262 : W - 272).attr("y", x.y + x.h / 2 + 12).attr("text-anchor", left ? "end" : "start").attr("dominant-baseline", "middle").attr("class", "sb").text(trunc(x.product, 34));
      g.on("click", () => o.onSelect(x.n.key)).on("keydown", e => { if (e.key === "Enter") o.onSelect(x.n.key); });
    });
    draw(sup, supIn, "supplier"); draw(cus, cusOut, "customer");
    svg.append("rect").attr("x", fx).attr("y", H / 2 - tot / 2).attr("width", NODE_W).attr("height", tot).attr("fill", "var(--ink)").attr("rx", 2);
    svg.append("text").attr("x", W / 2).attr("y", H / 2 - tot / 2 - 8).attr("text-anchor", "middle").attr("class", "fc").text(o.focus.name);
    svg.append("text").attr("x", 270).attr("y", 12).attr("class", "hd mono").text("SUPPLIERS");
    svg.append("text").attr("x", W - 280).attr("y", 12).attr("class", "hd mono").text("CUSTOMERS");
    if (!sup.length && !cus.length) host.append(el("div", { class: "sv-empty" }, "No named suppliers or customers to draw as flows; partners and competitors are on the network view."));
  }

  // ---------------------------------------------------------------- world map
  let WORLD = null;
  async function world() {
    if (WORLD) return WORLD;
    if (!window.topojson) await loadScript("https://cdn.jsdelivr.net/npm/topojson-client@3.1.0/dist/topojson-client.min.js");
    const topo = await fetch("https://cdn.jsdelivr.net/npm/world-atlas@2.0.2/countries-110m.json").then(r => { if (!r.ok) throw new Error("World map unavailable"); return r.json(); });
    WORLD = topojson.feature(topo, topo.objects.countries).features;
    return WORLD;
  }
  const POINTS = { Singapore: [103.82, 1.35], "Hong Kong": [114.17, 22.32], Luxembourg: [6.13, 49.61], Malta: [14.4, 35.9], Bermuda: [-64.75, 32.3] };
  const ATLAS = { "United States of America": "United States", "S. Korea": "South Korea", "Korea": "South Korea", "Czech Rep.": "Czechia", "Dominican Rep.": "Dominican Republic", "Bosnia and Herz.": "Bosnia and Herzegovina", "Russian Federation": "Russia" };
  const atlasName = f => C.canonCountry(ATLAS[f.properties.name] || f.properties.name);

  async function worldMap(host, o) {
    host.innerHTML = "";
    const feats = await world();
    const W = 960, H = 470;
    const proj = d3.geoNaturalEarth1().fitExtent([[6, 6], [W - 6, H - 6]], { type: "Sphere" }), path = d3.geoPath(proj);
    const byName = new Map(o.rows.map(r => [r.country, r]));
    const weight = r => r ? r.links + r.hq + r.policy : 0, max = Math.max(1, ...o.rows.map(weight));
    const centroidOf = c => { if (POINTS[c]) return proj(POINTS[c]); const f = feats.find(g => atlasName(g) === c); return f ? proj(d3.geoCentroid(f)) : null; };
    const svg = d3.select(host).append("svg").attr("class", "sv-map").attr("viewBox", `0 0 ${W} ${H}`);
    svg.append("path").attr("d", path({ type: "Sphere" })).attr("class", "sphere");
    svg.append("g").selectAll("path").data(feats).join("path").attr("d", path)
      .attr("class", f => { const c = atlasName(f), r = byName.get(c); return "land" + (r ? (r.hotspot ? " hot" : " tie") : "") + (o.selected === c ? " sel" : ""); })
      .attr("fill-opacity", f => { const r = byName.get(atlasName(f)); return r ? 0.18 + 0.72 * Math.sqrt(weight(r) / max) : 1; })
      .on("click", (e, f) => { const c = atlasName(f); if (byName.has(c)) o.onSelect(o.selected === c ? null : c); })
      .on("mouseenter", (e, f) => { const r = byName.get(atlasName(f)); if (r) showTip(e, tipHTML(r)); }).on("mouseleave", hideTip);
    const HOME = [-98, 39];
    if (o.arcs) for (const r of o.rows.filter(r => r.country !== "United States").slice(0, 25)) {
      const end = POINTS[r.country] || (() => { const f = feats.find(g => atlasName(g) === r.country); return f ? d3.geoCentroid(f) : null; })();
      if (!end) continue;
      const ip = d3.geoInterpolate(HOME, end);
      svg.append("path").attr("d", path({ type: "LineString", coordinates: Array.from({ length: 24 }, (_, i) => ip(i / 23)) }))
        .attr("class", "arc" + (r.hotspot ? " hot" : "") + (r.singleSource ? " ss" : "") + (o.selected && o.selected !== r.country ? " dim" : "")).attr("stroke-width", 1.2 + 3 * Math.sqrt(weight(r) / max));
    }
    for (const r of o.rows) {
      const p = centroidOf(r.country); if (!p) continue;
      const rad = 2.5 + 9 * Math.sqrt(weight(r) / max);
      const g = svg.append("g").attr("class", "bub" + (r.hotspot ? " hot" : "")).attr("tabindex", 0).attr("role", "button").attr("aria-label", r.country)
        .on("click", () => o.onSelect(o.selected === r.country ? null : r.country)).on("mouseenter", e => showTip(e, tipHTML(r))).on("mouseleave", hideTip);
      g.append("circle").attr("cx", p[0]).attr("cy", p[1]).attr("r", rad);
      if (r.singleSource) g.append("circle").attr("cx", p[0]).attr("cy", p[1]).attr("r", rad + 3).attr("class", "ssr");
      if (weight(r) / max > 0.22 || o.selected === r.country) g.append("text").attr("x", p[0]).attr("y", p[1] - rad - 4).attr("text-anchor", "middle").text(r.country);
    }
    return { unmapped: o.rows.filter(r => !centroidOf(r.country)) };
  }
  const tipHTML = r => `<b>${esc(r.country)}</b>${r.hotspot ? " · hotspot" : ""}<br>${r.links} stated links · ${r.hq} HQ'd counterparties<br>${r.singleSource} single-source · ${r.policy} filing policy statements<br><span class="faint">${esc(r.companies.slice(0, 8).join(" · "))}</span>`;

  window.FU_SUPPLY_VIZ = { network, flow, worldMap, REL_COLOR, REL_LABEL };
})();
