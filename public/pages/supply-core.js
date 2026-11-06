// Pure supply-chain logic shared by the page, the company tab and tests (node --test). Ported from the reference
// platform's src/lib/supply/core.ts, analytics.ts, region.ts and counterparty.ts with the same semantics, on our
// compact link records: { id, t (filer), cp, cpt, rel, det, sh (share %), ge10, amt, ss, rg [], hq, risk, prod, lt, q, row, accn, doc, form, filed }.
(function (root) {
  "use strict";
  const RELATIONS = ["customer", "supplier", "partner", "competitor"];
  const HOTSPOTS = ["china", "taiwan", "hong kong", "russia", "ukraine", "israel", "korea", "middle east"];

  // ---------------------------------------------------------------- text
  const QMAP = { "‘": "'", "’": "'", "‛": "'", "′": "'", "“": '"', "”": '"', "‟": '"', "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "―": "-", "−": "-", " ": " ", " ": " ", " ": " " };
  const normText = s => String(s ?? "").replace(/[‘’‛′“”‟‐-―−   ]/g, c => QMAP[c]).replace(/\s+/g, " ").trim().toLowerCase();
  function quoteInText(quote, text) {
    const q = normText(quote);
    const n = q ? q.split(" ").length : 0;
    if (n < 8 || n > 40) return false;
    return normText(text).includes(q);
  }
  /** Character range [start, end) of `quote` inside `text` after whitespace/quote normalisation, or null. */
  function findQuote(text, quote) {
    const src = String(text ?? ""), q = normText(quote);
    if (!q) return null;
    let out = "", map = [], prevSpace = true;
    for (let i = 0; i < src.length; i++) {
      let c = QMAP[src[i]] ?? src[i];
      if (/\s/.test(c)) { if (prevSpace) continue; c = " "; prevSpace = true; } else prevSpace = false;
      out += c.toLowerCase(); map.push(i);
    }
    const k = out.indexOf(q);
    if (k < 0) return null;
    return [map[k], map[k + q.length - 1] + 1];
  }

  // ---------------------------------------------------------------- identities
  const ALIASES = [[/taiwan semiconductor|\btsmc\b/, "TSM"], [/\bsamsung/, "samsung"], [/sk hynix|\bhynix\b/, "sk hynix"], [/foxconn|hon hai/, "hon hai"], [/amazon web services|\baws\b/, "AMZN"], [/google cloud/, "GOOGL"], [/microsoft azure|\bazure\b/, "MSFT"], [/^(the )?u\.?s\.? (federal )?government|^united states (federal )?government|department of defense/, "us government"]];
  function entityKey(name, ticker) {
    if (ticker) return String(ticker).toUpperCase();
    const n = normText(name);
    for (const [re, k] of ALIASES) if (re.test(n)) return k;
    return n.replace(/[.,'()]/g, " ").replace(/\b(incorporated|inc|corp|corporation|co|company|ltd|limited|plc|llc|lp|ag|sa|nv|se|holdings?|group|the)\b\.?/g, " ").replace(/\s+/g, " ").trim();
  }
  const inverse = r => r === "customer" ? "supplier" : r === "supplier" ? "customer" : r;
  const maxShare = n => Math.max(0, ...n.links.map(l => l.sh ?? 0));

  /** Links that mention `focus` (either side), one node per counterparty as seen from `focus`. */
  function focusGraph(focus, all, names = {}) {
    const f = String(focus).toUpperCase(), by = new Map();
    const add = (key, name, ticker, relation, link, hq) => {
      const k = `${relation}:${key}`;
      const n = by.get(k) || { key: k, name, ticker, relation, links: [], hq: hq || null };
      n.links.push(link); if (!n.hq && hq) n.hq = hq; by.set(k, n);
    };
    for (const l of all) {
      if (l.t === f) { if (l.cpt === f) continue; add(entityKey(l.cp, l.cpt), l.cpt ? (names[l.cpt] || l.cp) : l.cp, l.cpt || null, l.rel, l, l.hq); }
      else if (l.cpt === f) add(l.t, names[l.t] || l.t, l.t, inverse(l.rel), l, null);
    }
    return [...by.values()].sort((a, b) => maxShare(b) - maxShare(a) || b.links.length - a.links.length || a.name.localeCompare(b.name));
  }

  /** Suppliers flow in from the left, customers out to the right, partners above and competitors below; wrapped columns never overlap. */
  function sidePositions(count, side) {
    const ROW = 50, COL = 200, PER_COL = 10, PER_ROW = 6;
    return Array.from({ length: count }, (_, i) => {
      if (side === "supplier" || side === "customer") {
        const col = Math.floor(i / PER_COL), row = i % PER_COL, inCol = Math.min(PER_COL, count - col * PER_COL), dir = side === "supplier" ? -1 : 1;
        return { x: dir * (330 + col * COL), y: (row - (inCol - 1) / 2) * ROW };
      }
      const r = Math.floor(i / PER_ROW), c = i % PER_ROW, inRow = Math.min(PER_ROW, count - r * PER_ROW), dir = side === "partner" ? -1 : 1;
      return { x: (c - (inRow - 1) / 2) * COL, y: dir * (250 + r * ROW) };
    });
  }

  // ---------------------------------------------------------------- risk
  /** Dollar value of a link: the stated amount, else share x the filer's revenue (customer shares only, which are % of revenue). */
  function linkDollars(l, revenue = {}) {
    if (l.amt != null && l.amt > 0) return l.amt;
    const rev = revenue[l.t];
    if (l.sh != null && l.rel === "customer" && rev) return (l.sh / 100) * rev;
    return null;
  }
  const isHot = r => HOTSPOTS.some(h => String(r).toLowerCase().includes(h));
  function nodeRisk(links, revenue = {}) {
    const regions = [...new Set(links.flatMap(l => l.rg || []))];
    const dollars = links.map(l => linkDollars(l, revenue)).filter(d => d != null);
    return { singleSource: links.some(l => !!l.ss), concentrated: links.some(l => (l.sh ?? 0) >= 10 || l.ge10), hotspots: regions.filter(isHot), regions, dollars: dollars.length ? Math.max(...dollars) : null };
  }

  /** Suppliers of the focus's suppliers and customers of its customers, up to perParent per first-order company. */
  function secondOrder(focus, first, all, names = {}, perParent = 4) {
    const f = String(focus).toUpperCase(), seen = new Set(first.map(n => n.ticker || n.name.toLowerCase())), out = [];
    seen.add(f);
    for (const p of first) {
      if (!p.ticker || (p.relation !== "supplier" && p.relation !== "customer")) continue;
      const kids = focusGraph(p.ticker, all, names).filter(k => k.relation === p.relation && !seen.has(k.ticker || k.name.toLowerCase()));
      for (const k of kids.slice(0, perParent)) { seen.add(k.ticker || k.name.toLowerCase()); out.push({ ...k, key: `h2:${p.key}:${k.key}`, hop: 2, parent: p.key }); }
    }
    return out;
  }

  /** Propagate a demand/output shock at `source` (0.2 = 20% drop): suppliers lose sales sized by the share they disclose
   *  (recursing upstream); customers face supply disruption (high when single-sourced), one more hop downstream. */
  function simulateShock(source, shock, all, names = {}, revenue = {}, maxHop = 2) {
    const out = new Map(), nm = t => names[t] || t, src = String(source).toUpperCase();
    const score = x => (x.revenuePct ?? 0) * 10 + (x.severity === "high" ? 3 : x.severity === "medium" ? 2 : 1) - x.hop;
    const keep = imp => { const prev = out.get(imp.key); if (!prev || score(imp) > score(prev)) out.set(imp.key, imp); };
    const walk = (s, size, hop, path, dir) => {
      if (hop > maxHop || size < 0.002) return;
      for (const l of all) {
        const up = l.rel === "customer" && l.cpt === s ? { t: l.t, name: nm(l.t), share: l.sh } : l.rel === "supplier" && l.t === s ? { t: l.cpt || null, name: l.cpt ? nm(l.cpt) : l.cp, share: null } : null;
        if (up && dir !== "down" && up.t !== src) {
          const pct = up.share != null ? (up.share / 100) * size : null, key = up.t || normText(up.name), rev = up.t ? revenue[up.t] : undefined;
          keep({ key, name: up.name, ticker: up.t, hop, channel: "lost sales", revenuePct: pct, dollars: pct != null && rev ? pct * rev : null, severity: pct != null ? (pct >= 0.05 ? "high" : pct >= 0.01 ? "medium" : "low") : "low", path: [...path, up.name], link: l });
          if (up.t && pct != null) walk(up.t, pct, hop + 1, [...path, up.name], "up");
        }
        const down = l.rel === "supplier" && l.cpt === s ? { t: l.t, name: nm(l.t) } : l.rel === "customer" && l.t === s ? { t: l.cpt || null, name: l.cpt ? nm(l.cpt) : l.cp } : null;
        if (down && dir !== "up" && down.t !== src) {
          const key = down.t || normText(down.name);
          keep({ key, name: down.name, ticker: down.t, hop, channel: "supply disruption", revenuePct: null, dollars: null, severity: l.ss ? "high" : hop === 1 ? "medium" : "low", path: [...path, down.name], link: l });
          if (down.t && hop < maxHop) walk(down.t, size / 2, hop + 1, [...path, down.name], "down");
        }
      }
    };
    walk(src, shock, 1, [nm(src)], "both");
    const rank = { high: 0, medium: 1, low: 2 };
    return [...out.values()].sort((a, b) => (b.revenuePct ?? -1) - (a.revenuePct ?? -1) || rank[a.severity] - rank[b.severity] || a.hop - b.hop);
  }

  // ---------------------------------------------------------------- change over time
  const linkKey = l => `${l.rel}:${entityKey(l.cp, l.cpt)}`;
  function annualFilings(ticker, all) {
    const m = new Map();
    for (const l of all) if (l.t === ticker && l.form === "10-K") m.set(l.accn, l.filed);
    return [...m].map(([accn, filed]) => ({ accn, filed })).sort((a, b) => b.filed.localeCompare(a.filed));
  }
  /** Counterparties named in `newer` but not `older` (added), the reverse (dropped), and stated-share moves of 1pt or more. */
  function linkChanges(ticker, all, newerAccn, olderAccn) {
    const pick = accn => { const m = new Map(); for (const l of all) if (l.t === ticker && l.accn === accn && !m.has(linkKey(l))) m.set(linkKey(l), l); return m; };
    const a = pick(newerAccn), b = pick(olderAccn), out = [];
    const mk = (l, kind, from, to, prior) => ({ key: linkKey(l), name: l.cp, ticker: l.cpt || null, relation: l.rel, kind, from, to, link: l, prior: prior || null });
    for (const [k, l] of a) { const o = b.get(k); if (!o) out.push(mk(l, "added", null, l.sh ?? null)); else if (l.sh != null && o.sh != null && Math.abs(l.sh - o.sh) >= 1) out.push(mk(l, "share", o.sh, l.sh, o)); }
    for (const [k, l] of b) if (!a.has(k)) out.push(mk(l, "dropped", l.sh ?? null, null));
    const order = { share: 0, added: 1, dropped: 2 };
    return out.sort((x, y) => order[x.kind] - order[y.kind] || x.name.localeCompare(y.name));
  }

  // ---------------------------------------------------------------- earnings reactions
  function eventReturn(c, date) {
    const i = c.findIndex(([d]) => d >= date);
    if (i < 1 || i + 1 >= c.length) return null;
    return c[i + 1][1] / c[i - 1][1] - 1;
  }
  function spillover(focus, other, dates) {
    const rows = dates.map(date => ({ date, focus: eventReturn(focus, date), other: eventReturn(other, date) })).filter(r => r.focus != null && r.other != null);
    if (rows.length < 3) return null;
    const n = rows.length, mf = rows.reduce((s, r) => s + r.focus, 0) / n, mo = rows.reduce((s, r) => s + r.other, 0) / n;
    const cov = rows.reduce((s, r) => s + (r.focus - mf) * (r.other - mo), 0), vf = rows.reduce((s, r) => s + (r.focus - mf) ** 2, 0);
    return { events: n, meanMove: mo, meanAbs: rows.reduce((s, r) => s + Math.abs(r.other), 0) / n, sameDir: rows.filter(r => Math.sign(r.focus) === Math.sign(r.other)).length / n, beta: vf > 0 ? cov / vf : null, rows };
  }

  // ---------------------------------------------------------------- analyst layer
  /** corroborated = the counterparty's own filing names the filer with the mirrored relation; stated = share, amount or dependency; mentioned = named only. */
  function confidence(l, all) {
    if (l.cpt && all.some(m => m.t === l.cpt && m.cpt === l.t && m.rel === inverse(l.rel))) return "corroborated";
    return l.sh != null || l.ge10 || (l.amt ?? 0) > 0 || l.ss ? "stated" : "mentioned";
  }
  const mirrors = (l, all) => l.cpt ? all.filter(m => m.t === l.cpt && m.cpt === l.t && m.rel === inverse(l.rel)) : [];

  /** Fragility of one company from its own current filing: customer concentration, single-source inputs, hotspot ties. */
  function dependencyScore(ticker, all) {
    const own = all.filter(l => l.t === ticker), shares = new Map();
    for (const l of own) if (l.rel === "customer" && l.sh != null) { const k = entityKey(l.cp, l.cpt); shares.set(k, Math.max(shares.get(k) ?? 0, l.sh)); }
    const s = [...shares.values()], hhi = s.reduce((a, x) => a + x * x, 0), top = Math.max(0, ...s), stated = Math.min(100, s.reduce((a, x) => a + x, 0));
    const single = new Set(own.filter(l => l.ss && l.rel !== "customer").map(l => entityKey(l.cp, l.cpt))).size;
    const hotLinks = own.filter(l => (l.rg || []).some(isHot));
    const hotspots = [...new Set(hotLinks.flatMap(l => (l.rg || []).filter(isHot)))];
    const cPts = Math.round(Math.min(40, top * 0.8 + Math.min(10, hhi / 250))), sPts = Math.min(30, single * 10), gPts = Math.min(30, hotLinks.length * 6);
    return {
      score: cPts + sPts + gPts, customerHhi: Math.round(hhi), topCustomerPct: top, statedCustomerPct: stated, singleSource: single, hotspotLinks: hotLinks.length, hotspots,
      parts: [
        { label: "Customer concentration", points: cPts, max: 40, why: top ? `largest named customer ${top}% of revenue; ${s.length} customers with stated shares` : "no customer shares stated" },
        { label: "Single-source inputs", points: sPts, max: 30, why: `${single} supplier/partner dependencies stated as sole, single or limited source` },
        { label: "Geopolitical exposure", points: gPts, max: 30, why: hotspots.length ? `${hotLinks.length} links tied to ${hotspots.join(", ")}` : "no hotspot regions tied to links" },
      ],
    };
  }
  function regionExposure(nodes) {
    const m = new Map();
    for (const n of nodes) {
      const regions = new Set(n.links.flatMap(l => (l.rg || []).map(r => r.trim())).filter(Boolean));
      for (const r of regions) {
        const key = r.toLowerCase(), e = m.get(key) || { region: r, hotspot: isHot(key), suppliers: 0, customers: 0, other: 0 };
        if (n.relation === "supplier") e.suppliers++; else if (n.relation === "customer") e.customers++; else e.other++;
        m.set(key, e);
      }
    }
    return [...m.values()].sort((a, b) => b.suppliers + b.customers + b.other - (a.suppliers + a.customers + a.other));
  }
  function concentrationBasis(quote) {
    const q = String(quote).toLowerCase();
    if (/receivable/.test(q)) return /vendor/.test(q) ? "vendor" : "receivables";
    if (/\b(vendor|supplier|purchases)\b/.test(q)) return "vendor";
    if (/\b(channel|distribution)\b/.test(q)) return "channel";
    if (/(revenue|net sales|total sales)/.test(q) && /(customer|client|distributor|partner)/.test(q)) return "revenue";
    return "other";
  }

  // ---------------------------------------------------------------- geography
  const CALIAS = { "u.s.": "United States", us: "United States", usa: "United States", "united states of america": "United States", "u.s.a.": "United States", america: "United States", "greater china": "China", prc: "China", "mainland china": "China", "people's republic of china": "China", uk: "United Kingdom", "u.k.": "United Kingdom", "great britain": "United Kingdom", britain: "United Kingdom", england: "United Kingdom", "republic of korea": "South Korea", korea: "South Korea", "s. korea": "South Korea", "the netherlands": "Netherlands", holland: "Netherlands", "czech republic": "Czechia", "russian federation": "Russia", "viet nam": "Vietnam", "republic of china": "Taiwan", "hong kong sar": "Hong Kong", uae: "United Arab Emirates", "united states": "United States" };
  function canonCountry(raw) {
    if (!raw) return null;
    const t = String(raw).trim().replace(/\s+/g, " ");
    if (!t) return null;
    return CALIAS[t.toLowerCase()] || (t === t.toLowerCase() ? t.replace(/\b\w/g, c => c.toUpperCase()) : t);
  }
  const HQ = [[/taiwan semiconductor|\btsmc\b|hon hai|foxconn|wistron|quanta|pegatron|compal|inventec|mediatek|united microelectronics|\bumc\b|advanced semiconductor engineering|\base technology/, "Taiwan"], [/samsung|sk hynix|hynix|\blg\b|lg (energy|display|electronics|chem)|hyundai|\bkia\b|\bsk on\b|posco/, "South Korea"], [/\bsony\b|toyota|panasonic|murata|kioxia|tokyo electron|mitsubishi|hitachi|toshiba|nidec|kyocera|renesas|shin-etsu|sumco|\btdk\b|fujitsu|nintendo|honda|nissan|softbank|takeda|astellas|daiichi|olympus|keyence|denso/, "Japan"], [/huawei|\bbyd\b|catl|contemporary amperex|alibaba|tencent|baidu|xiaomi|lenovo|\bzte\b|smic|luxshare|goertek|boe technology|haier|midea|cosco|sinopec|petrochina|cnooc/, "China"], [/\basml\b|nxp|philips|stellantis|heineken|prosus/, "Netherlands"], [/infineon|siemens|\bbasf\b|\bsap\b|bosch|zeiss|volkswagen|bmw|mercedes|bayer|merck kgaa|deutsche telekom|allianz|biontech|boehringer/, "Germany"], [/novo nordisk|maersk|vestas|genmab/, "Denmark"], [/nestl|novartis|roche|stmicro|\babb\b|logitech|glencore|coca-cola hbc/, "Switzerland"], [/teva|check point|mobileye|tower semiconductor|elbit/, "Israel"], [/\btata\b|infosys|wipro|reliance|hcl tech|dr\. reddy|sun pharma/, "India"], [/nokia/, "Finland"], [/ericsson|volvo|spotify/, "Sweden"], [/astrazeneca|glaxo|\bgsk\b|unilever|arm holdings|\barm\b|rolls-royce|bae systems|vodafone|hsbc|barclays|diageo|rio tinto|coca-cola europacific/, "United Kingdom"], [/airbus|sanofi|lvmh|totalenergies|schneider|safran|thales|capgemini|danone|kering|dassault/, "France"], [/bhp|\bcsl\b/, "Australia"], [/shopify|nutrien|enbridge|celestica|magna\b|teck/, "Canada"], [/vale\b|petrobras|embraer/, "Brazil"], [/aramco/, "Saudi Arabia"], [/femsa|cemex|grupo bimbo/, "Mexico"], [/tengizchevroil/, "Kazakhstan"]];
  function counterpartyCountry(name) { const n = String(name).toLowerCase(); for (const [re, c] of HQ) if (re.test(n)) return c; return null; }
  const hqOf = l => (l.hq && l.hq !== "United States" ? l.hq : null) || counterpartyCountry(l.cp);

  /** Every stated tie to each country across links (regions), counterparty headquarters and filing policy statements. */
  function countryExposure(links, policy = [], revenue = {}) {
    const m = new Map();
    const row = c => { let r = m.get(c); if (!r) { r = { country: c, hotspot: isHot(c), links: 0, companies: new Set(), singleSource: 0, dollars: 0, policy: 0, hq: 0, ids: [] }; m.set(c, r); } return r; };
    for (const l of links) {
      for (const raw of new Set((l.rg || []).map(canonCountry).filter(Boolean))) { if (raw === "Europe" || raw === "Asia") continue; const r = row(raw); r.links++; r.companies.add(l.t); if (l.ss) r.singleSource++; r.dollars += linkDollars(l, revenue) ?? 0; r.ids.push(l.id); }
      const hq = l.rel !== "competitor" ? hqOf(l) : null;
      if (hq && !(l.rg || []).map(canonCountry).includes(hq)) { const r = row(hq); r.hq++; r.companies.add(l.t); if (l.ss) r.singleSource++; r.ids.push(l.id); }
    }
    for (const p of policy) for (const c of p.countries || []) { const cc = canonCountry(c); if (!cc || cc === "Europe" || cc === "Asia") continue; const r = row(cc); r.policy++; r.companies.add(p.t); r.ids.push(p.id); }
    return [...m.values()].map(r => ({ ...r, companies: [...r.companies].sort() })).sort((a, b) => b.links + b.hq + b.policy - (a.links + a.hq + a.policy));
  }
  /** Who is hurt if `country` is disrupted: single-source ties x25 + stated $ vs revenue (x200, max 40) + HQ'd counterparties x6 + other links x4 + policy x3, max 100. */
  function regionShock(country, links, policy = [], revenue = {}) {
    const c = canonCountry(country), by = new Map();
    const hit = t => { let h = by.get(t); if (!h) { h = { ticker: t, links: 0, singleSource: 0, policy: 0, hq: 0, dollars: 0, score: 0, severity: "low", examples: [], ids: [] }; by.set(t, h); } return h; };
    for (const l of links) {
      const stated = (l.rg || []).some(r => canonCountry(r) === c);
      if (!stated) {
        if (l.rel === "competitor" || hqOf(l) !== c) continue;
        const h = hit(l.t); h.hq++; if (l.ss) h.singleSource++; h.dollars += linkDollars(l, revenue) ?? 0; if (h.examples.length < 3) h.examples.push(`${l.rel}: ${l.cp}`); h.ids.push(l.id); continue;
      }
      const h = hit(l.t); h.links++; if (l.ss) h.singleSource++; h.dollars += linkDollars(l, revenue) ?? 0; if (h.examples.length < 3) h.examples.push(`${l.rel}: ${l.cp}`); h.ids.push(l.id);
    }
    for (const p of policy) if ((p.countries || []).some(x => canonCountry(x) === c)) { const h = hit(p.t); h.policy++; h.ids.push(p.id); }
    for (const h of by.values()) {
      const rev = revenue[h.ticker], dollarPts = rev && h.dollars ? Math.min(40, (h.dollars / rev) * 200) : 0, other = Math.max(0, h.links - h.singleSource);
      h.score = Math.round(Math.min(100, h.singleSource * 25 + dollarPts + other * 4 + h.hq * 6 + h.policy * 3));
      h.severity = h.score >= 50 ? "high" : h.score >= 20 ? "medium" : "low";
    }
    return [...by.values()].sort((a, b) => b.score - a.score || a.ticker.localeCompare(b.ticker));
  }
  /** Stated customer revenue shares per annual filing (10-K only), oldest first, one series per named customer. */
  function concentrationHistory(ticker, history, top = 6) {
    const own = history.filter(l => l.t === ticker && l.form === "10-K" && l.rel === "customer" && l.sh != null);
    const names = new Map(), peak = new Map(), byFiled = new Map();
    for (const l of own) {
      const k = entityKey(l.cp, l.cpt);
      if (!names.has(k)) names.set(k, l.cp);
      peak.set(k, Math.max(peak.get(k) ?? 0, l.sh));
      const f = byFiled.get(l.filed) || new Map(); f.set(k, Math.max(f.get(k) ?? 0, l.sh)); byFiled.set(l.filed, f);
    }
    const keys = [...peak].sort((a, b) => b[1] - a[1]).slice(0, top).map(([k]) => k), series = keys.map(k => names.get(k));
    const points = [...byFiled].sort((a, b) => a[0].localeCompare(b[0])).map(([filed, f]) => { const p = { filed }; keys.forEach((k, i) => { if (f.has(k)) p[series[i]] = f.get(k); }); return p; });
    return { points, series };
  }
  function freshness(filed, today) {
    const days = Math.max(0, Math.round((Date.parse(today) - Date.parse(filed)) / 864e5));
    return { days, status: days <= 200 ? "fresh" : days <= 400 ? "aging" : "stale" };
  }
  function pearson(a, b) {
    const n = Math.min(a.length, b.length); if (n < 10) return null;
    let sa = 0, sb = 0; for (let i = 0; i < n; i++) { sa += a[i]; sb += b[i]; }
    const ma = sa / n, mb = sb / n; let c = 0, va = 0, vb = 0;
    for (let i = 0; i < n; i++) { c += (a[i] - ma) * (b[i] - mb); va += (a[i] - ma) ** 2; vb += (b[i] - mb) ** 2; }
    return va && vb ? c / Math.sqrt(va * vb) : null;
  }
  /** Equal-weight daily-rebalanced basket of `members` over the last `days` sessions vs focus and SPY (index starts at 100). */
  function ecosystemBasket(focus, members, dates, returns, days) {
    const f = returns[focus], mem = members.filter(t => t !== focus && returns[t]);
    if (!f || !mem.length) return null;
    const start = Math.max(1, dates.length - days), spy = returns.SPY;
    let b = 100, fo = 100, sp = 100;
    const index = [{ date: dates[start - 1], basket: 100, focus: 100, spy: spy ? 100 : null }];
    for (let i = start; i < dates.length; i++) {
      const rs = mem.map(t => returns[t][i]).filter(x => x != null);
      b *= 1 + (rs.length ? rs.reduce((a, x) => a + x, 0) / rs.length : 0); fo *= 1 + (f[i] ?? 0); if (spy) sp *= 1 + (spy[i] ?? 0);
      index.push({ date: dates[i], basket: b, focus: fo, spy: spy ? sp : null });
    }
    const fr = f.slice(start).map(x => x ?? 0);
    const rows = mem.map(t => {
      const r = returns[t].slice(start).map(x => x ?? 0), ret = r.reduce((a, x) => a * (1 + x), 1) - 1, mean = r.reduce((a, x) => a + x, 0) / r.length;
      const vol = Math.sqrt(r.reduce((a, x) => a + (x - mean) ** 2, 0) / Math.max(1, r.length - 1)) * Math.sqrt(252);
      return { ticker: t, ret, vol, corr: pearson(fr, r) };
    }).sort((a, b) => b.ret - a.ret);
    const mr = rows.map(m => m.ret), mm = mr.reduce((a, x) => a + x, 0) / mr.length;
    return { index, members: rows, basketRet: b / 100 - 1, focusRet: fo / 100 - 1, spyRet: spy ? sp / 100 - 1 : null, dispersion: Math.sqrt(mr.reduce((a, x) => a + (x - mm) ** 2, 0) / mr.length) };
  }
  const closesToReturns = c => c.map((x, i) => i && x != null && c[i - 1] != null ? x / c[i - 1] - 1 : null);

  // ---------------------------------------------------------------- agreement terms (reference region.ts)
  const MONTH = "(?:January|February|March|April|May|June|July|August|September|October|November|December|Jan\\.?|Feb\\.?|Mar\\.?|Apr\\.?|Jun\\.?|Jul\\.?|Aug\\.?|Sept?\\.?|Oct\\.?|Nov\\.?|Dec\\.?)";
  const DATE_RE = new RegExp(`\\b${MONTH}\\s+\\d{1,2},\\s*(?:19|20)\\d{2}\\b|\\b${MONTH}\\s+(?:19|20)\\d{2}\\b|\\b(?:fiscal(?: year)?|FY)\\s*(?:19|20)\\d{2}\\b|\\b(?:Q[1-4]|(?:first|second|third|fourth) quarter(?: of)?)\\s+(?:fiscal\\s+)?(?:19|20)\\d{2}\\b|\\b(?:through|until|by|in|since|beginning|ending)\\s+(?:19|20)\\d{2}\\b`, "gi");
  const NUMWORD = "(?:\\d+(?:\\.\\d+)?|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen|twenty)";
  const DURATION_RE = new RegExp(`\\b${NUMWORD}[- ](?:year|month|quarter)s?\\b(?:[- ](?:term|agreement|contract|period|commitment|supply agreement))?`, "gi");
  const AMOUNT_RE = /(?:US)?\$\s?\d[\d,]*(?:\.\d+)?\s?(?:trillion|billion|million|thousand|bn|mm|[BMK]\b)?/gi;
  const PCT_RE = /\b\d{1,3}(?:\.\d+)?\s?(?:%|percent\b)/gi;
  const CLAUSES = [[/take[- ]or[- ]pay/i, "Take-or-pay"], [/prepay|prepaid|advance payment|deposit/i, "Prepayment / deposit"], [/non[- ]?cancel+able|cannot be cancel+ed/i, "Non-cancellable"], [/purchase (?:obligation|commitment)s?|minimum purchase|commit(?:ted|ment)s? to (?:buy|purchase)/i, "Purchase commitment"], [/capacity (?:reservation|agreement|commitment)|reserve(?:d)? capacity/i, "Capacity reservation"], [/exclusiv/i, "Exclusivity"], [/sole[- ]source|single[- ]source|only (?:supplier|source)|limited number of suppliers/i, "Sole / limited source"], [/long[- ]term (?:supply|agreement|contract)/i, "Long-term agreement"], [/terminat/i, "Termination right"], [/renew|extend(?:ed)? (?:the|its|our) (?:agreement|term)|extension/i, "Renewal / extension"], [/licens/i, "Licence"], [/warrant(?:s)? to (?:purchase|acquire)|equity stake|invest(?:ed|ment) in/i, "Equity / warrant"], [/most[- ]favou?red/i, "Most-favoured terms"], [/volume (?:discount|rebate|commitment)|rebate/i, "Volume rebate"], [/penalt|liquidated damages/i, "Penalties"], [/tariff|export control|license requirement|sanction/i, "Trade-control exposure"]];
  const uniq = xs => [...new Map(xs.map(x => [x.toLowerCase().replace(/\s+/g, " ").trim(), x.replace(/\s+/g, " ").trim()])).values()];
  function agreementTerms(text) {
    const t = text || "";
    return { dates: uniq(t.match(DATE_RE) || []), durations: uniq(t.match(DURATION_RE) || []), amounts: uniq((t.match(AMOUNT_RE) || []).filter(a => /\d/.test(a))), percents: uniq(t.match(PCT_RE) || []), clauses: CLAUSES.filter(([re]) => re.test(t)).map(([, l]) => l) };
  }
  function markTerms(text, a) {
    const needles = [...a.dates, ...a.durations, ...a.amounts, ...a.percents].filter(Boolean).sort((x, y) => y.length - x.length);
    if (!needles.length) return [{ s: text, hit: false }];
    const esc = needles.map(n => n.replace(/[.*+?^${}()|[\]\\]/g, "\\$&").replace(/\s+/g, "\\s+"));
    const re = new RegExp(`(${esc.join("|")})`, "gi"), test = new RegExp(`^(?:${esc.join("|")})$`, "i");
    return text.split(re).filter(s => s !== "").map(s => ({ s, hit: test.test(s) }));
  }
  function riskTier(s) {
    const v = [s.stability, s.ruleOfLaw, s.regulatory].filter(x => typeof x === "number");
    if (!v.length) return "unrated";
    const min = Math.min(...v);
    return min < -1 ? "high" : min < -0.25 ? "elevated" : min < 0.75 ? "moderate" : "low";
  }

  // ---------------------------------------------------------------- cross-source insights (computed, not AI)
  /** Facts that need two or more sources to see: a supplier shared across covered companies, links both sides confirm,
   *  stated dependencies in a country that also carries a U.S. trade action, shares that moved between annual reports. */
  function crossInsights(focus, all, history = [], regions = {}, names = {}) {
    const out = [], nm = t => names[t] || t, f = String(focus).toUpperCase();
    const own = all.filter(l => l.t === f);
    // shared upstream dependencies
    for (const l of own.filter(x => x.rel === "supplier")) {
      const k = entityKey(l.cp, l.cpt);
      const peers = all.filter(m => m.t !== f && m.rel === "supplier" && entityKey(m.cp, m.cpt) === k);
      const peerT = [...new Set(peers.map(p => p.t))];
      if (peerT.length >= 2) out.push({ kind: "shared supplier", text: `${l.cp} is also named as a supplier by ${peerT.length} other covered companies (${peerT.slice(0, 6).map(nm).join(", ")}${peerT.length > 6 ? ", …" : ""}). A disruption there reaches ${nm(f)} and these peers at the same time.`, refs: [l.id, ...peers.slice(0, 6).map(p => p.id)], weight: peerT.length + (l.ss ? 3 : 0) });
    }
    // corroborated both ways
    for (const l of own) {
      const ms = mirrors(l, all);
      if (ms.length) out.push({ kind: "corroborated", text: `${nm(f)} names ${l.cp} as a ${l.rel}, and ${l.cp}'s own ${ms[0].form} names ${nm(f)} as a ${ms[0].rel}. Both filings state the relationship.`, refs: [l.id, ms[0].id], weight: 2 + (l.sh ? 2 : 0) });
    }
    // others name the focus but the focus does not name them
    const ownKeys = new Set(own.map(l => `${l.rel}:${l.cpt || ""}`));
    const inbound = all.filter(l => l.cpt === f && !ownKeys.has(`${inverse(l.rel)}:${l.t}`) && l.rel !== "competitor");
    if (inbound.length) out.push({ kind: "one-sided", text: `${inbound.length} covered compan${inbound.length > 1 ? "ies name" : "y names"} ${nm(f)} as a ${[...new Set(inbound.map(l => l.rel))].join(" or ")} (${[...new Set(inbound.map(l => nm(l.t)))].slice(0, 5).join(", ")}) without ${nm(f)}'s filing naming them back.`, refs: inbound.slice(0, 6).map(l => l.id), weight: inbound.length });
    // policy coincidence: own links tied to a country with Federal Register trade actions
    const tied = new Map();
    for (const l of own) for (const c of [...(l.rg || []).map(canonCountry), hqOf(l)].filter(Boolean)) { if (!tied.has(c)) tied.set(c, []); tied.get(c).push(l); }
    for (const [c, ls] of tied) {
      const fr = (regions[c]?.policy || []).filter(p => p.q);
      if (fr.length && ls.some(l => l.rel !== "competitor")) {
        const key = ls.filter(l => l.rel !== "competitor").slice(0, 3);
        out.push({ kind: "policy", text: `${key.map(l => l.cp).join(", ")} tie${key.length === 1 ? "s" : ""} ${nm(f)} to ${c}, where ${regions[c].policyTotal || fr.length} U.S. trade, export-control or sanctions notices were published in the last 12 months (latest: ${fr[0].agency}, ${fr[0].date}).`, refs: [...key.map(l => l.id), "F" + fr[0].id], weight: 2 + Math.min(3, fr.length / 3) + key.filter(l => l.ss).length * 2 });
      }
    }
    // share moves between annual reports
    const filings = annualFilings(f, [...own, ...history]);
    if (filings.length >= 2) for (const ch of linkChanges(f, [...own, ...history], filings[0].accn, filings[1].accn).filter(c => c.kind === "share")) out.push({ kind: "share move", text: `${ch.name}'s stated share moved from ${ch.from}% to ${ch.to}% between the 10-Ks filed ${filings[1].filed.slice(0, 4)} and ${filings[0].filed.slice(0, 4)}.`, refs: [ch.link.id, ch.prior.id], weight: 3 + Math.abs(ch.to - ch.from) / 5 });
    return out.sort((a, b) => b.weight - a.weight);
  }

  const API = { RELATIONS, HOTSPOTS, normText, quoteInText, findQuote, entityKey, inverse, maxShare, focusGraph, sidePositions, linkDollars, isHot, nodeRisk, secondOrder, simulateShock, linkKey, annualFilings, linkChanges, eventReturn, spillover, confidence, mirrors, dependencyScore, regionExposure, concentrationBasis, canonCountry, counterpartyCountry, hqOf, countryExposure, regionShock, concentrationHistory, freshness, pearson, ecosystemBasket, closesToReturns, agreementTerms, markTerms, riskTier, crossInsights };
  if (typeof module !== "undefined" && module.exports) module.exports = API;
  else root.FU_SUPPLY_CORE = API;
})(typeof window !== "undefined" ? window : globalThis);
