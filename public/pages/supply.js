// Supply chain page (#supply, #supply-<TICKER>) and the company "Supply chain" tab. Registers window.FU_PAGES.supply.
// Data (built by etl/supply.py, refreshed by api/cron/supply.py): supply/graph.json.gz (every current link across
// coverage), supply/co/{T}.json (one company: links, prior 10-K links, agreements, policy statements, concentration,
// news corroboration, sympathy moves, basket prices, AI insights), supply/regions.json (WGI + Federal Register),
// supply/state.json (rotation progress), supply/docs/... (text copies of cited passages, opened in the drawer).
window.FU_PAGES = window.FU_PAGES || {};
window.FU_COTABS = window.FU_COTABS || [];
(() => {
  "use strict";
  const C = window.FU_SUPPLY_CORE, V = window.FU_SUPPLY_VIZ, DRW = window.FU_SUPPLY_DRAWER;
  const S = { graph: null, co: {}, regions: null, state: null, tk: null, sel: null, tab: store.get("supply:tab", "evidence"), view: store.get("supply:view", "network"), layout: store.get("supply:layout", "side"), depth: store.get("supply:depth", 1), overlay: store.get("supply:overlay", "none"), visible: new Set(C.RELATIONS), shockSrc: null, shock: 20, country: null, clause: null, basketWin: 252, basketSide: "all", intelKind: "all" };
  const TODAY = new Date().toISOString().slice(0, 10);
  const gz = path => fetch(PUB + path).then(r => { if (!r.ok) throw new Error(`${r.status} loading ${path}`); return inflate(r); }).then(JSON.parse);
  const loadGraph = () => S.graph ? Promise.resolve(S.graph) : gz("supply/graph.json.gz").then(g => (S.graph = g));
  const loadCo = t => S.co[t] !== undefined ? Promise.resolve(S.co[t]) : getJSON(`supply/co/${encodeURIComponent(t)}.json`).then(j => (S.co[t] = j)).catch(() => (S.co[t] = null));
  const loadRegions = () => S.regions ? Promise.resolve(S.regions) : getJSON("supply/regions.json").then(j => (S.regions = j)).catch(() => (S.regions = { countries: {} }));
  const loadState = () => S.state ? Promise.resolve(S.state) : getJSON("supply/state.json").then(j => (S.state = j)).catch(() => (S.state = { tickers: {} }));

  /* ---------- styles ---------- */
  if (!document.getElementById("sc-css")) document.head.append(el("style", { id: "sc-css" }, `
.sc-page{display:flex;flex-direction:column;gap:14px}
.sc-kpis{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:8px}
.sc-kpi{border:1px solid var(--line);border-radius:8px;background:var(--surface);padding:9px 11px;min-width:0;text-align:left;font:inherit;color:inherit;cursor:pointer;box-shadow:var(--shadow-sm)}
.sc-kpi:hover{border-color:var(--line2)}
.sc-kpi .l{font-size:11px;color:var(--muted);display:flex;gap:4px;align-items:center}
.sc-kpi .v{font-size:17px;font-weight:600;font-variant-numeric:tabular-nums;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-top:1px}
.sc-kpi .s{font-size:10.5px;color:var(--faint);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.sc-fresh{display:flex;height:6px;border-radius:3px;overflow:hidden;background:var(--sunk);margin-top:7px}
.sc-fresh i{display:block}
.sc-ctl{display:flex;gap:6px;flex-wrap:wrap;align-items:center}
.sc-rel{display:inline-flex;align-items:center;gap:6px;height:26px;padding:0 10px;border:1px solid var(--line);border-radius:999px;background:var(--surface);font-size:12px;font-weight:500;cursor:pointer}
.sc-rel.off{opacity:.42}
.sc-rel i{width:9px;height:9px;border-radius:50%;display:inline-block}
.sc-rel .n{color:var(--faint);font-variant-numeric:tabular-nums}
.sc-ctl select{height:28px;border:1px solid var(--line2);border-radius:6px;background:var(--surface);font:inherit;font-size:12px;padding:0 6px}
.sc-main{display:grid;grid-template-columns:minmax(0,1fr) 384px;gap:14px;align-items:start}
.sc-canvas{position:relative;height:620px;border:1px solid var(--line);border-radius:8px;background:var(--surface);overflow:hidden}
.sc-canvas .cap{position:absolute;left:12px;bottom:8px;font-size:10.5px;color:var(--faint);pointer-events:none}
.sc-canvas .lg{position:absolute;left:12px;top:10px;display:flex;gap:10px;font-size:10.5px;color:var(--muted);pointer-events:none;flex-wrap:wrap}
.sc-canvas .lg i{display:inline-block;width:14px;height:3px;border-radius:2px;margin-right:4px;vertical-align:middle}
.sv-net{display:block;cursor:grab}
.sv-net .gridpt{fill:var(--line)}
.sv-node .bg{fill:var(--surface);stroke:var(--line2);stroke-width:1}
.sv-node{cursor:pointer;outline:none}
.sv-node:hover .bg,.sv-node:focus .bg{stroke:var(--ink)}
.sv-node.focus{cursor:default}
.sv-node.focus .bg{stroke:var(--ink);stroke-width:1.6}
.sv-node.hop2 .bg{stroke-dasharray:3 3}
.sv-node.sel .bg{stroke:#e0b100;stroke-width:2.4}
.sv-node.hit .bg{stroke:var(--down);stroke-width:2}
.sv-node.dim{opacity:.28}
.sv-node.imp-high .bg{fill:var(--down-soft);stroke:var(--down)}
.sv-node.imp-medium .bg{fill:var(--warn-soft)}
.sv-node .nm{font-size:11.5px;font-weight:600;fill:var(--ink);font-family:var(--f-ui)}
.sv-node.focus .nm{font-size:13.5px}
.sv-node .sb{font-size:9.5px;fill:var(--muted)}
.sv-node .ini{fill:var(--sunk);stroke:var(--line2)}
.sv-node .ini-t{font-size:8.5px;font-weight:700;fill:var(--muted);font-family:var(--f-mono)}
.sv-node .flag{font-size:8px;font-weight:700;fill:var(--down);font-family:var(--f-mono)}
.sv-node .hot{fill:var(--down)}
.sv-node .corro{font-size:9px;fill:var(--up);font-weight:700}
.sv-edge{fill:none;opacity:.8}
.sv-edge.customer{stroke:var(--c1)}.sv-edge.supplier{stroke:var(--c3)}.sv-edge.partner{stroke:var(--up)}.sv-edge.competitor{stroke:var(--down);stroke-dasharray:4 4;opacity:.55}
.sv-edge.customer,.sv-edge.supplier{stroke-dasharray:6 5;animation:sv-flow 1.6s linear infinite}
.sv-edge.h2{stroke-dasharray:3 3;opacity:.45;animation:none}
.sv-edge.hot{stroke:var(--down) !important;opacity:.95}
.sv-edge.dim{opacity:.12}
@keyframes sv-flow{to{stroke-dashoffset:-22}}
.sv-ripple circle{fill:none;stroke:var(--down);stroke-width:2;opacity:0;transform-box:fill-box;transform-origin:center;animation:sv-rip 2.4s ease-out infinite}
@keyframes sv-rip{0%{opacity:.7;transform:scale(.6)}100%{opacity:0;transform:scale(9)}}
@media (prefers-reduced-motion:reduce){.sv-edge.customer,.sv-edge.supplier,.sv-ripple circle{animation:none}}
.sv-zoom{position:absolute;right:10px;top:10px;display:flex;flex-direction:column;gap:4px}
.sv-zoom .iconbtn{width:28px;height:28px;font-size:15px;background:var(--surface);border:1px solid var(--line)}
.sv-flow{width:100%;height:100%}
.sv-flow .nm{font-size:11px;fill:var(--ink)}.sv-flow .sb{font-size:9px;fill:var(--muted)}.sv-flow .fc{font-size:13px;font-weight:600;fill:var(--ink)}.sv-flow .hd{font-size:10px;fill:var(--faint)}
.sv-band{cursor:pointer}.sv-band.dim{opacity:.35}.sv-band:hover path{opacity:.45}
.sv-empty,.sc-empty{padding:18px;color:var(--muted);font-size:12.5px}
.sv-empty{position:absolute;inset:auto 0 40% 0;text-align:center}
.sv-map{display:block;width:100%;height:auto}
.sv-map .sphere{fill:var(--bg);stroke:var(--line)}
.sv-map .land{fill:var(--sunk);stroke:var(--surface);stroke-width:.5}
.sv-map .land.tie{fill:var(--c1);cursor:pointer}.sv-map .land.hot{fill:var(--down);cursor:pointer}
.sv-map .land.sel{stroke:var(--ink);stroke-width:1.6}
.sv-map .arc{fill:none;stroke:var(--ink);stroke-opacity:.55;stroke-dasharray:3 3;stroke-linecap:round}
.sv-map .arc.ss{stroke-dasharray:none}.sv-map .arc.hot{stroke:var(--down)}.sv-map .arc.dim{stroke-opacity:.12}
.sv-map .bub{cursor:pointer}.sv-map .bub circle{fill:var(--ink);fill-opacity:.85;stroke:var(--surface);stroke-width:1.2}
.sv-map .bub.hot circle{fill:var(--down)}.sv-map .bub .ssr{fill:none !important;stroke:var(--down) !important;stroke-dasharray:2 2}
.sv-map .bub text{font-size:11px;font-weight:600;fill:var(--ink);paint-order:stroke;stroke:var(--surface);stroke-width:3}
.sc-tabs{display:flex;border-bottom:1px solid var(--line);gap:0;margin:-4px -16px 10px;padding:0 8px;overflow-x:auto}
.sc-tabs button{border:0;background:none;padding:9px 9px;font-size:12px;font-weight:600;color:var(--muted);border-bottom:2px solid transparent;cursor:pointer;white-space:nowrap}
.sc-tabs button.on{color:var(--ink);border-bottom-color:var(--accent)}
.sc-side{max-height:620px;display:flex;flex-direction:column}
.sc-side>.bd{overflow:auto}
.sc-list{display:flex;flex-direction:column;gap:2px;margin:0;padding:0;list-style:none}
.sc-li{display:flex;align-items:center;gap:8px;width:100%;border:0;background:none;padding:5px 6px;border-radius:5px;text-align:left;font:inherit;font-size:12.5px;cursor:pointer;color:inherit}
.sc-li:hover{background:var(--sunk)}
.sc-li .dot{width:8px;height:8px;border-radius:50%;flex:none}
.sc-li .nm{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.sc-li .r{margin-left:auto;color:var(--faint);font-size:11px;white-space:nowrap;font-variant-numeric:tabular-nums}
.sc-card{border:1px solid var(--line);border-radius:7px;padding:9px 10px;display:flex;flex-direction:column;gap:5px;font-size:12.5px}
.sc-card .meta{font-size:11px;color:var(--muted)}
.sc-card .meta b{color:var(--ink);font-weight:600}
.sc-badges{display:flex;gap:4px;flex-wrap:wrap}
.sc-b{display:inline-flex;align-items:center;gap:3px;height:19px;padding:0 6px;border-radius:4px;font-size:10.5px;font-weight:600;background:var(--sunk);color:var(--muted);white-space:nowrap}
.sc-b.ok{background:var(--up-soft);color:var(--up)}.sc-b.mk{background:#fff6c2;color:#5c4a00}.sc-b.bad{background:var(--down-soft);color:var(--down)}.sc-b.warn{background:var(--warn-soft);color:var(--warn)}
.sc-q{border:0;border-left:2px solid #f2c94c;background:none;padding:2px 0 2px 8px;text-align:left;font:inherit;font-size:12px;font-style:italic;color:var(--muted);cursor:pointer;line-height:1.45}
.sc-q:hover{color:var(--ink);background:#fffbe3}
.sc-q mark{background:#fff1a8;color:inherit;font-style:normal;border-radius:2px;padding:0 1px}
.sc-n{border:0;background:none;padding:0;font:inherit;color:inherit;cursor:pointer;font-variant-numeric:tabular-nums;text-decoration:underline dotted var(--faint);text-underline-offset:3px}
.sc-n:hover{color:var(--accent);text-decoration-color:var(--accent)}
.sc-src-btn{display:inline-flex;align-items:center;gap:4px;height:24px;padding:0 8px;border-radius:5px;border:0;background:#fff1a8;color:#3d3200;font-size:11.5px;font-weight:600;cursor:pointer}
.sc-src-btn:hover{background:#ffe766}
.sc-acts{display:flex;gap:6px;flex-wrap:wrap;align-items:center}
.sc-acts .btn{height:26px;font-size:11.5px;padding:0 9px}
.sc-row2{display:grid;gap:14px}
.sc-row2.a{grid-template-columns:minmax(0,1fr) 380px}.sc-row2.b{grid-template-columns:minmax(0,1.4fr) minmax(0,1fr)}.sc-row2.c{grid-template-columns:minmax(0,1.6fr) minmax(0,1fr)}.sc-row2.d{grid-template-columns:minmax(0,2fr) minmax(0,1fr) minmax(0,1fr)}
.sc-bar{height:6px;border-radius:3px;background:var(--sunk);overflow:hidden;display:flex}
.sc-bar i{display:block;height:100%}
.sc-chips{display:flex;gap:4px;flex-wrap:wrap}
.sc-chip{height:22px;padding:0 9px;border-radius:999px;border:1px solid var(--line);background:var(--surface);font-size:11px;cursor:pointer;color:var(--muted)}
.sc-chip.on{background:var(--ink);color:var(--bg);border-color:var(--ink)}
.sc-tbl{width:100%;border-collapse:collapse;font-size:12px}
.sc-tbl th{font-size:10px;text-transform:uppercase;letter-spacing:.05em;color:var(--faint);text-align:left;padding:5px 6px;border-bottom:1px solid var(--line);font-weight:600;white-space:nowrap;position:sticky;top:0;background:var(--surface)}
.sc-tbl td{padding:5px 6px;border-bottom:1px solid var(--line);vertical-align:middle}
.sc-tbl td.r,.sc-tbl th.r{text-align:right}.sc-tbl tr.click{cursor:pointer}.sc-tbl tr.click:hover td{background:var(--sunk)}.sc-tbl tr.on td{background:#fffbe3}
.sc-gov{display:inline-flex;align-items:center;gap:5px}
.sc-gov .t{position:relative;width:52px;height:6px;border-radius:3px;background:var(--sunk);overflow:hidden}
.sc-gov .t i{position:absolute;top:0;bottom:0}
.sc-gov .t:before{content:"";position:absolute;left:50%;top:0;bottom:0;width:1px;background:var(--line2)}
.sc-tier{display:inline-block;padding:1px 6px;border-radius:4px;font-size:10.5px;font-weight:700}
.sc-tier.high{background:var(--down-soft);color:var(--down)}.sc-tier.elevated{background:var(--warn-soft);color:var(--warn)}.sc-tier.moderate{background:var(--sunk);color:var(--ink)}.sc-tier.low{background:var(--up-soft);color:var(--up)}.sc-tier.unrated{background:var(--sunk);color:var(--faint)}
.sc-sec{margin-top:12px}.sc-sec>.h{font-size:10.5px;font-weight:600;text-transform:uppercase;letter-spacing:.06em;color:var(--faint);margin-bottom:5px;display:flex;gap:6px}
.sc-ins{display:flex;flex-direction:column;gap:8px}
.sc-ins .it{display:grid;grid-template-columns:92px minmax(0,1fr);gap:8px;font-size:12.5px;line-height:1.5}
.sc-ins .k{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.05em;color:var(--faint);padding-top:2px}
.sc-ref{display:inline-flex;align-items:center;height:17px;min-width:17px;justify-content:center;padding:0 4px;margin-left:3px;border-radius:4px;border:0;background:#fff1a8;color:#3d3200;font-size:10px;font-weight:700;cursor:pointer;vertical-align:1px}
.sc-ref:hover{background:#ffe766}
.sc-ailabel svg{width:11px;height:11px;stroke:currentColor;fill:none;stroke-width:1.8}
.sc-ailabel{display:inline-flex;align-items:center;gap:4px;font-size:10.5px;color:var(--ai);background:var(--ai-soft);border:1px solid var(--ai-line);border-radius:4px;padding:1px 6px}
.sc-cover{font-size:11px;color:var(--faint);display:flex;gap:10px;flex-wrap:wrap}
.sc-pick{position:relative}
.sc-pick input{height:32px;width:250px;border:1px solid var(--line2);border-radius:6px;padding:0 10px;font:inherit;font-size:13px;background:var(--surface)}
.sc-pick .dd{position:absolute;right:0;top:36px;width:320px;max-height:360px;overflow:auto;background:var(--surface);border:1px solid var(--line);border-radius:8px;box-shadow:var(--shadow);z-index:20;padding:4px}
.sc-pick .dd button{display:flex;width:100%;gap:8px;align-items:center;border:0;background:none;padding:6px 8px;border-radius:5px;font:inherit;font-size:12.5px;text-align:left;cursor:pointer}
.sc-pick .dd button:hover,.sc-pick .dd button.on{background:var(--sunk)}
.sc-pick .dd .r{margin-left:auto;color:var(--faint);font-size:11px}
.sc-doc{overflow:auto;flex:1;background:#fff;padding:14px 18px;font-size:13px;line-height:1.6;color:#1b1d22}
.sc-doc p{margin:0 0 10px}
.sc-doc .sc-title{font-weight:600}
.sc-doc mark.fu-hit{background:#ffe766;box-shadow:0 0 0 2px #f2b600;border-radius:2px;color:#000}
.sc-doc mark.fu-alt{background:#fff6c2;border-radius:2px;color:inherit}
.sc-gap{font-size:11px;color:#8a8f98;text-align:center;margin:6px 0 12px;font-family:var(--f-mono)}
.sc-view{height:max(420px,calc(100vh - 360px))}
.sc-srcs{display:flex;flex-direction:column;gap:4px}
.sc-src{display:flex;gap:8px;border:1px solid var(--line);border-radius:7px;background:var(--surface);padding:7px 9px;text-align:left;font:inherit;cursor:pointer;color:inherit}
.sc-src.on{border-color:#e0b100;background:#fffbe3}
.sc-src .n{width:18px;height:18px;border-radius:4px;background:var(--sunk);font-size:10.5px;display:grid;place-items:center;flex:none}
.sc-src .b{display:flex;flex-direction:column;gap:2px;min-width:0}
.sc-src .l{font-size:11.5px;font-weight:600}
.sc-src .q{font-size:11.5px;color:var(--muted);font-style:italic;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.sc-role{margin-left:6px;font-weight:500;color:var(--faint)}
.sc-bq{margin:0;border-left:3px solid #f2c94c;padding:4px 10px;font-style:italic;font-size:12.5px;color:var(--muted);background:#fffbe3;border-radius:0 6px 6px 0}
.sc-cross{border-left:3px solid var(--up)}
.sc-ainote{border-left:3px solid var(--ai)}
.sc-miss{padding:16px;color:var(--muted)}
.sc-slider{width:100%}
.sc-imp{border:1px solid var(--line);border-radius:7px;padding:7px 9px;font-size:12px;display:flex;flex-direction:column;gap:2px;cursor:pointer;background:none;text-align:left;font:inherit;color:inherit}
.sc-imp.high{border-color:color-mix(in oklch,var(--down) 40%,transparent);background:var(--down-soft)}
.sc-imp .p{font-size:10.5px;color:var(--faint);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.sc-spark{width:100%;height:220px}
.sc-spark text{font-size:10px;fill:var(--muted)}
.sc-spark .ax line,.sc-spark .ax path{stroke:var(--line)}
.co-supply .sc-kpis{grid-template-columns:repeat(5,minmax(0,1fr))}
@media (max-width:1180px){.sc-main,.sc-row2.a,.sc-row2.b,.sc-row2.c,.sc-row2.d{grid-template-columns:minmax(0,1fr)}.sc-side{max-height:none}.sc-kpis{grid-template-columns:repeat(3,minmax(0,1fr))}}
@media (max-width:860px){.sc-kpis{grid-template-columns:repeat(2,minmax(0,1fr))}.sc-canvas{height:480px}.sc-pick input{width:100%}.sc-pick{width:100%}.sc-pick .dd{width:100%}}
`));

  /* ---------- shared helpers ---------- */
  const names = () => { const o = {}; for (const [t, c] of Object.entries(S.graph?.companies || {})) o[t] = SHORT[t] || c.n; return new Proxy(o, { get: (x, k) => x[k] || SHORT[k] || window.FU_COMPANIES?.get?.(k)?.name || undefined }); };
  const nm = t => SHORT[t] || S.graph?.companies?.[t]?.n || window.FU_COMPANIES?.get?.(t)?.name || t;
  const revenue = () => { const o = {}; for (const [t, c] of Object.entries(IDX)) if (c.revenue_ttm) o[t] = c.revenue_ttm; return o; };
  const cikOf = t => S.graph?.companies?.[t]?.c || D.companies[t]?.cik || window.FU_COMPANIES?.get?.(t)?.cik || null;
  const edgar = l => { const c = cikOf(l.t); return c ? `https://www.sec.gov/Archives/edgar/data/${Number(c)}/${l.accn.replace(/-/g, "")}/${l.doc}` : `https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=${l.t}`; };
  const relWord = { customer: "customer", supplier: "supplier", partner: "partner", competitor: "competitor" };
  const covered = t => !!(window.FU_COMPANIES?.get?.(t) || IDX[t]);
  const mapped = t => !!S.graph?.companies?.[t];
  const N = (text, onclick, title = "Open source") => el("button", { class: "sc-n", title, onclick: e => { e.stopPropagation(); onclick(); } }, text);
  const relDot = r => el("span", { class: "dot", style: `background:${V.REL_COLOR[r]}` });
  const tierBadge = t => el("span", { class: "sc-tier " + (t || "unrated") }, (t || "unrated").replace(/^\w/, c => c.toUpperCase()));
  const research = prompt => { window.dispatchEvent(new CustomEvent("fu:research-request", { detail: prompt })); toast("Sent to the research assistant"); };
  const methodText = m => m === "llm" ? "extracted by model, quote verified verbatim against the filing" : m === "rules+review" ? "rule-based extraction reviewed by hand (pilot), quote verified verbatim against the filing" : "rule-based extraction, quote verified verbatim against the filing";

  function fileSource(l, role) {
    return { kind: "filing", t: l.t, accn: l.accn, doc: l.doc, form: l.form, filed: l.filed, quote: l.q, row: l.row, url: edgar(l), path: `supply/docs/${l.accn}/${l.doc}.json.gz`, label: nm(l.t), role };
  }
  const linkValue = (l, rev) => [l.sh != null ? `${l.sh}% of ${l.rel === "customer" ? "revenue" : "purchases"}` : l.ge10 ? "≥10% of revenue" : null, C.linkDollars(l, rev) != null ? fmtMoney(C.linkDollars(l, rev)) : null].filter(Boolean).join(" · ") || null;

  /** Opens one link with every source that bears on it: the filing quote, the counterparty's mirrored statement,
   *  the same statement in prior annual reports and corroborating news. */
  async function openLink(l, extra = {}) {
    const all = S.graph?.links || [], rev = revenue();
    const ms = C.mirrors(l, all);
    const co = S.co[l.t] || await loadCo(l.t);
    const prior = (co?.history || []).filter(h => h.rel === l.rel && C.entityKey(h.cp, h.cpt) === C.entityKey(l.cp, l.cpt)).sort((a, b) => b.filed.localeCompare(a.filed));
    const news = (co?.intel || []).filter(n => n.link === l.id);
    const sources = [fileSource(l, "stated"), ...ms.map(m => fileSource(m, "counterparty confirms")), ...(extra.prior ? [fileSource(extra.prior, "prior filing")] : []), ...prior.filter(p => p.accn !== extra.prior?.accn).slice(0, 2).map(p => fileSource(p, "prior 10-K")),
      ...news.map(n => ({ kind: "web", url: n.url, title: n.title, host: n.host, date: n.date, quote: n.q, text: n.text, provider: n.provider, role: "news" }))];
    const conf = C.confidence(l, all);
    DRW.openEvidence({
      eyebrow: `${l.rel} link · ${conf}`, title: `${nm(l.t)} → ${l.cp}`, value: linkValue(l, rev), sub: `${l.det}${l.prod ? " · " + l.prod : ""} · ${l.form} filed ${fdate(l.filed)}`,
      sources,
      cross: ms.length ? `Corroborated: ${nm(l.t)}'s ${l.form} names ${l.cp} as a ${l.rel}, and ${nm(ms[0].t)}'s ${ms[0].form} names ${nm(l.t)} as a ${ms[0].rel}.` : prior.length ? `Also stated in ${prior.length} prior annual report${prior.length > 1 ? "s" : ""} (${prior.map(p => p.filed.slice(0, 4)).join(", ")}).` : null,
      footer: `Method: ${methodText(l.m)}. Text copy holds the cited passages; switch to Full filing for the complete document.`,
      actions: [el("button", { class: "btn", onclick: () => research(investigatePrompt(l)) }, svgUse("i-spark"), "Investigate relationship"), l.cpt && mapped(l.cpt) ? el("a", { class: "btn", href: `#supply-${l.cpt}` }, `Map ${l.cpt}`) : null, covered(l.t) ? el("a", { class: "btn", href: `#company-${l.t}` }, `${l.t} research`) : null],
      ...extra,
    });
  }
  const investigatePrompt = l => `Investigate ${nm(l.t)}'s ${l.rel} relationship with ${l.cp}. Read the supply-chain evidence (the ${l.form} filed ${l.filed} states: "${l.q}"), dated agreements, regional and trade-policy risks and the latest public developments. Cross-check sources, quantify only disclosed exposure and distinguish uncertainty.`;

  /** Several links behind one number (KPI tiles, country rows, register cells) as one cross-referenced drawer. */
  function openMany(title, links, eyebrow = "Supply-chain evidence", value = null, extra = {}) {
    if (!links.length) { toast("Nothing stated for this item"); return; }
    DRW.openEvidence({ eyebrow, title, value, sub: `${links.length} statement${links.length > 1 ? "s" : ""}`, sources: links.slice(0, 24).map(l => l.kind ? l : { ...fileSource(l, l.rel), label: `${nm(l.t)} → ${l.cp}` }), ...extra });
  }
  const policySource = p => ({ kind: "filing", t: p.t, accn: p.accn, doc: p.doc, form: p.form, filed: p.filed, quote: p.q, url: edgar(p), path: `supply/docs/${p.accn}/${p.doc}.json.gz`, label: `${nm(p.t)} · ${p.kind.replace("_", " ")}`, role: p.countries.join(", ") });
  const frSource = d => ({ kind: "fr", url: d.url, title: d.title, abstract: d.abstract, quote: d.q || (d.title && d.title.includes(d.country) && d.title.split(/\s+/).length >= 5 ? d.title : null), qsrc: d.qsrc || (d.q ? "abstract" : "title"), agency: d.agency, date: d.date, role: d.type });

  /* ---------- ticker picker ---------- */
  function picker(tk, go) {
    const wrap = el("div", { class: "sc-pick" });
    const input = el("input", { value: `${tk} · ${nm(tk)}`, "aria-label": "Company", placeholder: "Type a ticker or company" });
    const dd = el("div", { class: "dd", hidden: true });
    const all = () => { const m = new Map(); Object.entries(S.graph?.companies || {}).forEach(([t, c]) => m.set(t, { t, n: SHORT[t] || c.n, links: c.links })); (window.FU_COMPANIES?.list?.() || Object.keys(IDX).map(t => ({ ticker: t }))).forEach(c => { const t = c.ticker || c.t; if (t && !m.has(t)) m.set(t, { t, n: c.name || nm(t), links: null }); }); return [...m.values()]; };
    let rows = [], at = 0;
    const render = q => {
      q = q.trim().toLowerCase(); dd.innerHTML = "";
      rows = all().filter(r => !q || r.t.toLowerCase().startsWith(q) || r.n.toLowerCase().includes(q)).sort((a, b) => (b.links != null) - (a.links != null) || (b.links || 0) - (a.links || 0)).slice(0, 40);
      rows.forEach((r, i) => dd.append(el("button", { class: i === at ? "on" : "", onmousedown: e => { e.preventDefault(); go(r.t); } }, miniLogo(r.t, 16), el("b", {}, r.t), el("span", { class: "muted" }, r.n), el("span", { class: "r" }, r.links != null ? `${r.links} links` : "not read yet"))));
      dd.hidden = !rows.length;
    };
    input.addEventListener("focus", () => { input.select(); at = 0; render(""); });
    input.addEventListener("input", () => { at = 0; render(input.value); });
    input.addEventListener("blur", () => setTimeout(() => { dd.hidden = true; input.value = `${tk} · ${nm(tk)}`; }, 120));
    input.addEventListener("keydown", e => { if (e.key === "ArrowDown") { at = Math.min(rows.length - 1, at + 1); render(input.value); e.preventDefault(); } else if (e.key === "ArrowUp") { at = Math.max(0, at - 1); render(input.value); e.preventDefault(); } else if (e.key === "Enter" && rows[at]) go(rows[at].t); else if (e.key === "Escape") input.blur(); });
    wrap.append(input, dd);
    return wrap;
  }

  /* ---------- page ---------- */
  async function page_(arg) {
    const [g, , st] = await Promise.all([loadGraph(), loadRegions(), loadState()]);
    const companies = Object.keys(g.companies || {});
    const tk = (arg || store.get("supply:tk", null) || (companies.includes("NVDA") ? "NVDA" : companies[0]) || "NVDA").toUpperCase();
    if (S.tk !== tk) { S.sel = null; S.shockSrc = null; S.country = null; S.clause = null; }
    S.tk = tk; store.set("supply:tk", tk);
    const co = await loadCo(tk);
    if (tk && covered(tk)) ensureCompany(tk).catch(() => {});
    page.innerHTML = "";
    const root = el("div", { class: "sc-page" });
    page.append(root);
    const go = t => { location.hash = `supply-${t}`; };
    root.append(el("div", { class: "ph" },
      el("div", {}, el("div", { class: "eyebrow" }, "Markets · Supply chain"), el("h1", {}, "Supply chain map"),
        el("div", { class: "sub" }, "Who each company sells to, buys from, works with and competes against, as named in its latest 10-K and newer 10-Q, with disclosed dollar exposure, risk flags, changes across annual reports, earnings sympathy moves, region and trade-policy exposure and a shock simulator. Every link opens the sentence it comes from.")),
      el("div", { class: "right" }, picker(tk, go))));
    root.append(coverageLine(g, st));
    const ctx = build(tk, co);
    if (!st.tickers?.[tk]) root.append(el("div", { class: "note" }, `${nm(tk)}'s own filings have not been read yet; they are queued for the scheduled supply-chain reader. ${ctx.graph.length ? `The ${ctx.graph.length} link${ctx.graph.length > 1 ? "s" : ""} below come from other companies' filings that name ${nm(tk)}.` : ""}`));
    root.append(kpis(ctx));
    root.append(controls(ctx, () => rerender()));
    const main = el("div", { class: "sc-main" });
    const canvas = el("div", { class: "sc-canvas" }), side = el("section", { class: "panel sc-side" });
    main.append(canvas, side); root.append(main);
    const lower = el("div", { class: "sc-page" }); root.append(lower);
    const rerender = (full = false) => {
      const c2 = build(tk, co);
      drawCanvas(canvas, c2, rerender);
      drawSide(side, c2, rerender);
      if (full) drawLower(lower, c2, rerender);
    };
    rerender(true);
    window.FU_SUPPLY_LAST = ctx;
  }

  function coverageLine(g, st) {
    const done = Object.keys(st.tickers || {}).length, total = (window.FU_COMPANIES?.list?.() || []).length || 1100;
    return el("div", { class: "sc-cover" },
      el("span", {}, `${done.toLocaleString()} of ${total.toLocaleString()} covered companies read`),
      el("span", {}, `${(g.counts?.links || 0).toLocaleString()} current links · ${(g.counts?.corroborated || 0).toLocaleString()} confirmed by both filings`),
      el("span", {}, `Graph updated ${g.generated ? fdate(g.generated.slice(0, 10)) : "–"}`),
      st.remaining ? el("span", {}, `${st.remaining.toLocaleString()} companies queued for the scheduled reader`) : null,
      el("span", {}, "Source: SEC EDGAR filings; quotes verified verbatim"));
  }

  function build(tk, co) {
    const all = S.graph?.links || [], nms = names(), rev = revenue();
    const focus = { key: "focus", name: nm(tk), ticker: tk, relation: "focus", links: [] };
    const graph = C.focusGraph(tk, all, nms);
    const hop2 = S.depth === 2 ? C.secondOrder(tk, graph, all, nms) : [];
    const risks = Object.fromEntries([...graph, ...hop2].map(n => [n.key, C.nodeRisk(n.links, rev)]));
    const counts = Object.fromEntries(C.RELATIONS.map(r => [r, graph.filter(n => n.relation === r).length]));
    const src = S.shockSrc || tk;
    const impacts = S.tab === "shock" ? C.simulateShock(src, S.shock / 100, all, nms, rev) : [];
    const impactMap = S.tab === "shock" ? new Map(impacts.map(i => [i.key, i])) : null;
    const flat = graph.flatMap(n => n.links.map(l => ({ n, l, d: C.linkDollars(l, rev) })));
    const exposure = flat.filter(x => x.d != null).sort((a, b) => b.d - a.d);
    const vals = Object.entries(risks).filter(([k]) => !k.startsWith("h2:"));
    return { tk, co, all, nms, rev, focus, graph, hop2, risks, counts, impacts, impactMap, src, exposure,
      single: vals.filter(([, r]) => r.singleSource).map(([k]) => graph.find(n => n.key === k)).filter(Boolean),
      conc: vals.filter(([, r]) => r.concentrated).map(([k]) => graph.find(n => n.key === k)).filter(Boolean),
      hotspots: [...new Set(vals.flatMap(([, r]) => r.hotspots))], hotLinks: flat.filter(x => (x.l.rg || []).some(C.isHot)).map(x => x.l),
      corro: flat.filter(x => C.confidence(x.l, all) === "corroborated").map(x => x.l), sel: [...graph, ...hop2].find(n => n.key === S.sel) || null };
  }

  function kpis(c) {
    const links = c.graph.flatMap(n => n.links), fr = { fresh: 0, aging: 0, stale: 0 };
    links.forEach(l => fr[C.freshness(l.filed, TODAY).status]++);
    const tot = Math.max(1, links.length), newest = links.reduce((m, l) => l.filed > m ? l.filed : m, "");
    const tile = (l, v, s, on, extra) => el("button", { class: "sc-kpi", onclick: on, title: "Open sources" }, el("div", { class: "l" }, l), el("div", { class: "v" }, v), el("div", { class: "s" }, s), extra || null);
    return el("div", { class: "sc-kpis" },
      tile("Disclosed $ exposure", c.exposure.length ? fmtMoney(c.exposure.reduce((s, x) => s + x.d, 0)) : "–", `${c.exposure.length} quantified links`, () => openMany(`${c.focus.name}: disclosed dollar exposure`, c.exposure.map(x => x.l), "Exposure", c.exposure.length ? fmtMoney(c.exposure.reduce((s, x) => s + x.d, 0)) : null, { footer: "Stated dollar amounts, or a stated customer share of revenue times trailing revenue from the filings. Sums mix both; open each source." })),
      tile("Concentrated (≥10%)", String(c.conc.length), "counterparties with a stated share ≥10%", () => openMany(`${c.focus.name}: concentrated counterparties`, c.conc.flatMap(n => n.links.filter(l => (l.sh ?? 0) >= 10 || l.ge10)), "Concentration")),
      tile("Single-source", String(c.single.length), "stated sole or limited source", () => openMany(`${c.focus.name}: single-source dependencies`, c.single.flatMap(n => n.links.filter(l => l.ss)), "Dependency")),
      tile("Hotspot regions", c.hotspots.length ? c.hotspots.slice(0, 3).join(", ") : "None stated", "tied to links in the filing", () => openMany(`${c.focus.name}: links tied to hotspot regions`, c.hotLinks, "Geography")),
      tile("Confirmed both ways", String(c.corro.length), "both companies' filings state it", () => openMany(`${c.focus.name}: links confirmed by both filings`, c.corro.flatMap(l => [l, ...C.mirrors(l, c.all)]), "Corroboration")),
      tile("Data freshness", `${fr.fresh} / ${links.length}`, `fresh · ${fr.aging} aging · ${fr.stale} stale${newest ? " · newest " + fdate(newest) : ""}`, () => openMany(`${c.focus.name}: newest statements`, [...links].sort((a, b) => b.filed.localeCompare(a.filed)), "Freshness"),
        el("div", { class: "sc-fresh" }, el("i", { style: `width:${fr.fresh / tot * 100}%;background:var(--up)` }), el("i", { style: `width:${fr.aging / tot * 100}%;background:var(--warn)` }), el("i", { style: `width:${fr.stale / tot * 100}%;background:var(--down)` }))));
  }

  function controls(c, rerender) {
    const box = el("div", { class: "sc-ctl" });
    const draw = () => {
      box.innerHTML = "";
      C.RELATIONS.forEach(r => box.append(el("button", { class: "sc-rel" + (S.visible.has(r) ? "" : " off"), "aria-pressed": String(S.visible.has(r)), onclick: () => { S.visible.has(r) ? S.visible.delete(r) : S.visible.add(r); draw(); rerender(); } }, el("i", { style: `background:${V.REL_COLOR[r]}` }), V.REL_LABEL[r], el("span", { class: "n" }, c.counts[r]))));
      box.append(el("span", { style: "flex:1" }));
      box.append(seg([["network", "Network"], ["flow", "Flow"], ["world", "World"]], S.view, v => { S.view = v; store.set("supply:view", v); draw(); rerender(); }));
      if (S.view === "network") box.append(seg([["side", "Side"], ["force", "Force"]], S.layout, v => { S.layout = v; store.set("supply:layout", v); draw(); rerender(); }));
      box.append(seg([[1, "Direct"], [2, "+ 2nd order"]], S.depth, v => { S.depth = +v; store.set("supply:depth", +v); draw(); rerender(); }));
      const sel = el("select", { "aria-label": "Overlay", onchange: e => { S.overlay = e.target.value; store.set("supply:overlay", S.overlay); rerender(); } },
        ...[["none", "No overlay"], ["single", "Single-source dependencies"], ["concentration", "Concentration ≥10%"], ["geo", "Geopolitical hotspots"], ["dollars", "Disclosed $ exposure"]].map(([v, l]) => el("option", { value: v, selected: S.overlay === v }, l)));
      box.append(sel);
    };
    draw();
    return box;
  }

  function drawCanvas(canvas, c, rerender) {
    canvas.innerHTML = "";
    const onSelect = k => { S.sel = k; if (S.tab !== "shock") S.tab = "evidence"; rerender(); };
    if (!c.graph.length) {
      const st = S.state?.tickers?.[c.tk];
      canvas.append(el("div", { class: "sc-empty", style: "padding:60px 24px;text-align:center" }, el("b", {}, st ? `No named customers, suppliers, partners or competitors in ${c.focus.name}'s latest filings.` : `${c.focus.name} has not been read yet.`), el("div", { class: "muted", style: "margin-top:6px" }, st ? "Many companies describe counterparties only generically (\"a customer\", \"certain suppliers\"); unnamed concentration appears below when stated." : "The scheduled supply-chain reader processes every covered company; check back after the next run.")));
      return;
    }
    if (S.view === "flow") V.flow(canvas, { focus: c.focus, graph: c.graph, revenue: c.rev, selected: S.sel, onSelect });
    else if (S.view === "world") {
      const rows = C.countryExposure(c.all.filter(l => l.t === c.tk || l.cpt === c.tk), (c.co?.policy || []), c.rev);
      V.worldMap(canvas, { rows, selected: S.country, arcs: true, onSelect: k => { S.country = k; rerender(true); } }).catch(e => canvas.append(el("div", { class: "sv-empty" }, String(e.message || e))));
    } else V.network(canvas, { focus: c.focus, graph: c.graph, hop2: c.hop2, visible: S.visible, selected: S.sel, onSelect, overlay: S.overlay, risks: c.risks, impacts: c.impactMap, shockKey: S.tab === "shock" ? (c.src === c.tk ? "focus" : c.src) : null, layout: S.layout, all: c.all });
    canvas.append(el("div", { class: "lg" }, ...C.RELATIONS.map(r => el("span", {}, el("i", { style: `background:${V.REL_COLOR[r]}` }), V.REL_LABEL[r]))));
    canvas.append(el("div", { class: "cap" }, S.view === "flow" ? "Band width = disclosed dollars, else stated share" : S.view === "world" ? "Country ties stated in filings and counterparty headquarters; click a country for its region shock and dossier" : "Line thickness = stated share · dashed = second-order · ✓✓ = both filings state the link · SOLE = stated single source · drag to pan, scroll to zoom"));
  }

  /* ---------- analysis panel ---------- */
  function drawSide(side, c, rerender) {
    side.innerHTML = "";
    const tabs = [["evidence", "Evidence"], ["moves", "Moves"], ["changes", "Changes"], ["shock", "Shock"], ["exposure", "Exposure"]];
    const hd = el("div", { class: "hd" }, el("h3", {}, "Analysis"), el("div", { class: "r" }, el("button", { class: "btn", style: "height:26px;font-size:11.5px", onclick: () => research(c.sel ? investigatePrompt(c.sel.links[0]) : `Analyse ${c.focus.name}'s supply chain: key customers and suppliers named in its filings, concentration, single-source dependencies, geographic and trade-policy exposure, and what changed versus prior annual reports. Cite the filing quotes.`) }, svgUse("i-spark"), c.sel ? "Investigate relationship" : "Investigate")));
    const body = el("div", { class: "bd" });
    const tb = el("div", { class: "sc-tabs", role: "tablist" }, ...tabs.map(([id, n]) => el("button", { class: S.tab === id ? "on" : "", role: "tab", "aria-selected": String(S.tab === id), onclick: () => { S.tab = id; store.set("supply:tab", id); rerender(); } }, n)));
    body.append(tb);
    side.append(hd, body);
    const host = el("div", { style: "display:flex;flex-direction:column;gap:8px" });
    body.append(host);
    ({ evidence: tabEvidence, moves: tabMoves, changes: tabChanges, shock: tabShock, exposure: tabExposure })[S.tab](host, c, rerender);
  }

  function riskBadges(r) {
    if (!r) return null;
    const b = [r.singleSource && ["Single source", "bad"], r.concentrated && ["Concentrated ≥10%", "bad"], ...r.regions.map(x => [r.hotspots.includes(x) ? `Hotspot · ${x}` : x, r.hotspots.includes(x) ? "bad" : ""])].filter(Boolean);
    return b.length ? el("div", { class: "sc-badges" }, ...b.map(([t, k]) => el("span", { class: "sc-b " + k }, t))) : null;
  }
  const confBadge = conf => el("span", { class: "sc-b " + (conf === "corroborated" ? "ok" : conf === "stated" ? "mk" : ""), title: { corroborated: "The counterparty's own filing names this company back", stated: "Quote carries a share, amount or dependency", mentioned: "Named in the filing" }[conf] }, conf === "corroborated" ? "✓✓ Corroborated by both filings" : conf === "stated" ? "Stated with a figure or dependency" : "Named in filing");
  const freshBadge = filed => { const f = C.freshness(filed, TODAY); return el("span", { class: "sc-b " + (f.status === "fresh" ? "ok" : f.status === "aging" ? "warn" : "bad"), title: `Last stated ${f.days} days ago` }, `${f.status[0].toUpperCase() + f.status.slice(1)} · ${f.days}d`); };

  function linkCard(l, c, label) {
    const conf = C.confidence(l, c.all), ms = C.mirrors(l, c.all), d = C.linkDollars(l, c.rev);
    return el("div", { class: "sc-card" },
      el("div", { class: "meta" }, `From ${nm(l.t)}'s ${l.form}, filed ${fdate(l.filed)}`, l.sh != null ? [" · ", N(`${l.sh}%`, () => openLink(l))] : l.ge10 ? [" · ", N("≥10%", () => openLink(l))] : null, d != null ? [" · ", N(fmtMoney(d), () => openLink(l), l.amt ? "Stated amount" : "Stated share × trailing revenue")] : null),
      el("div", { class: "sc-badges" }, confBadge(conf), freshBadge(l.filed), l.lt ? el("span", { class: "sc-b" }, l.lt.replace(/_/g, " ")) : null, l.prod ? el("span", { class: "sc-b" }, l.prod) : null),
      el("div", {}, l.det),
      ...ms.slice(0, 1).map(m => el("button", { class: "sc-q", style: "font-style:normal;color:var(--up)", onclick: () => openLink(l) }, `${nm(m.t)}'s ${m.form} confirms: “${m.q.slice(0, 120)}${m.q.length > 120 ? "…" : ""}”`)),
      l.risk ? el("div", { style: "font-size:11.5px;color:var(--down)" }, "Risk: " + l.risk) : null,
      el("button", { class: "sc-q", onclick: () => openLink(l) }, "“" + l.q + "”"),
      el("div", { class: "sc-acts" }, el("button", { class: "sc-src-btn", onclick: () => openLink(l) }, `Open in ${l.form}`), label ? el("span", { class: "faint", style: "font-size:11px" }, label) : null));
  }

  function tabEvidence(host, c, rerender) {
    const sel = c.sel;
    if (!sel) {
      host.append(el("div", { class: "faint", style: "font-size:11.5px" }, "Select a company on the map, or pick one below."));
      const ul = el("div", { class: "sc-list" });
      c.graph.slice(0, 40).forEach(n => ul.append(el("button", { class: "sc-li", onclick: () => { S.sel = n.key; rerender(); } }, relDot(n.relation), el("span", { class: "nm" }, n.name), c.risks[n.key]?.singleSource ? el("span", { class: "sc-b bad" }, "SOLE") : null, el("span", { class: "r" }, [n.relation, C.maxShare(n) ? C.maxShare(n) + "%" : null].filter(Boolean).join(" · ")))));
      host.append(ul);
      const unnamed = (c.co?.conc || []).filter(x => x.cur && x.unnamed);
      if (unnamed.length) host.append(el("div", { class: "sc-sec" }, el("div", { class: "h" }, "Unnamed concentration stated"), ...unnamed.slice(0, 4).map(x => el("button", { class: "sc-q", onclick: () => DRW.openEvidence({ eyebrow: "Customer concentration", title: `${c.focus.name}: unnamed customer at ${x.pct}%`, value: `${x.pct}%`, sources: [policySource({ ...x, kind: "concentration", countries: [] })] }) }, `${x.pct}% · “${x.q}”`))));
      return;
    }
    host.append(el("div", { style: "display:flex;gap:10px;align-items:center" }, sel.ticker ? logoTile(sel.ticker, 36) : el("div", { class: "sc-b", style: "height:36px;width:36px;justify-content:center" }, (sel.name || "").slice(0, 3).toUpperCase()),
      el("div", { style: "min-width:0" }, el("div", { style: "font-weight:600" }, sel.name), el("div", { class: "faint", style: "font-size:11.5px" }, `${sel.ticker || "Not in coverage"} · ${sel.relation}${sel.hop ? " (2nd order via " + (c.graph.find(n => n.key === sel.parent)?.name || "") + ")" : ` of ${c.focus.name}`}${sel.hq ? " · HQ " + sel.hq : ""}`)),
      el("button", { class: "iconbtn", style: "margin-left:auto", "aria-label": "Clear selection", onclick: () => { S.sel = null; rerender(); } }, svgUse("i-x"))));
    host.append(riskBadges(c.risks[sel.key]) || el("span"));
    sel.links.forEach(l => host.append(linkCard(l, c, l.t !== c.tk && !sel.hop ? `${nm(l.t)} names ${c.focus.name}` : null)));
    const ag = (c.co?.agreements || []).filter(a => a.parties.some(p => C.entityKey(p.cp, p.cpt) === C.entityKey(sel.name, sel.ticker) || (sel.ticker && p.cpt === sel.ticker)));
    if (ag.length) host.append(el("div", { class: "sc-sec" }, el("div", { class: "h" }, "Agreements naming this counterparty"), ...ag.map(a => el("button", { class: "sc-q", onclick: () => openAgreement(a) }, `${a.form} ${fdate(a.filed)}: “${a.quotes[0].q}”`))));
    host.append(el("div", { class: "sc-acts" },
      el("button", { class: "btn", onclick: () => research(investigatePrompt(sel.links[0])) }, svgUse("i-spark"), "Investigate relationship"),
      sel.ticker && covered(sel.ticker) ? el("a", { class: "btn", href: `#company-${sel.ticker}` }, "Company research") : null,
      sel.ticker && mapped(sel.ticker) ? el("a", { class: "btn", href: `#supply-${sel.ticker}` }, `Map ${sel.name}`) : null,
      sel.ticker ? el("button", { class: "btn", onclick: () => { S.shockSrc = sel.ticker; S.tab = "shock"; rerender(); } }, `Shock ${sel.name}`) : null));
  }

  function tabMoves(host, c, rerender) {
    const mv = c.co?.moves;
    host.append(el("div", { class: "faint", style: "font-size:11.5px" }, `How linked stocks moved over the two trading days around each of ${c.focus.name}'s earnings releases (8-K Item 2.02), last ${mv?.years || 5} years. Beta = move per 1% move in ${c.tk}; "same way" = share of releases where both moved in the same direction.`));
    if (!mv || !mv.rows?.length) { host.append(el("div", { class: "sc-empty" }, !mv ? "Moves are computed by the scheduled job from stored daily prices; not available for this company yet." : (mv.dates?.length || 0) < 3 ? "Not enough earnings dates." : "No covered, listed counterparties to compare.")); return; }
    const t = el("table", { class: "sc-tbl" }, el("thead", {}, el("tr", {}, el("th", {}, "Company"), el("th", { class: "r" }, "Avg |move|"), el("th", { class: "r" }, "Same way"), el("th", { class: "r" }, "Beta"))));
    const tb = el("tbody");
    const open = r => DRW.openEvidence({ eyebrow: "Earnings sympathy (computed)", title: `${r.t} around ${c.focus.name} earnings`, value: `${(r.meanAbs * 100).toFixed(1)}% avg |move|`, sub: `${r.events} releases · mean ${fsgn(r.meanMove)} · beta ${r.beta == null ? "–" : r.beta.toFixed(2)}`,
      sources: [{ kind: "calc", label: "Event returns", status: "Close before the release date to close after it, from stored split-adjusted daily closes", body: el("table", { class: "sc-tbl" }, el("thead", {}, el("tr", {}, el("th", {}, "Release (8-K filed)"), el("th", { class: "r" }, c.tk), el("th", { class: "r" }, r.t))), el("tbody", {}, ...r.rows.slice().reverse().map(([d, f, o]) => { const e = mv.dates.find(x => x.date === d); return el("tr", {}, el("td", {}, e ? el("a", { href: `https://www.sec.gov/Archives/edgar/data/${Number(e.cik)}/${e.accn.replace(/-/g, "")}/${e.doc}`, target: "_blank", rel: "noopener" }, fdate(d)) : fdate(d)), el("td", { class: "r num " + cls(f) }, fsgn(f)), el("td", { class: "r num " + cls(o) }, fsgn(o))); }))) }],
      footer: `${mv.method}. Source: ${mv.source}. Historical co-movement, not causation; includes market-wide moves on those days.` });
    mv.rows.slice(0, 16).forEach(r => tb.append(el("tr", { class: "click", onclick: () => open(r) }, el("td", {}, el("span", { class: "dot", style: `display:inline-block;width:7px;height:7px;border-radius:50%;margin-right:5px;background:${V.REL_COLOR[r.rel]}` }), el("b", {}, r.t), " ", el("span", { class: "faint" }, r.rel)), el("td", { class: "r num" }, (r.meanAbs * 100).toFixed(1) + "%"), el("td", { class: "r num", style: r.sameDir >= 0.65 ? "color:var(--up);font-weight:600" : "" }, Math.round(r.sameDir * 100) + "%"), el("td", { class: "r num", style: r.beta != null && Math.abs(r.beta) >= 0.5 ? "font-weight:600" : "" }, r.beta == null ? "–" : r.beta.toFixed(2)))));
    t.append(tb); host.append(t);
    const top = mv.rows[0];
    host.append(el("div", { style: "font-size:12px" }, "Biggest sympathy mover: ", N(top.t, () => open(top)), `, averaging ${(top.meanAbs * 100).toFixed(1)}% around ${c.tk} earnings (mean ${fsgn(top.meanMove)}, ${top.events} releases).`));
    host.append(el("div", { class: "faint", style: "font-size:10.5px" }, "Historical co-movement, not causation; includes market-wide moves on those days."));
  }

  function tabChanges(host, c) {
    const hist = [...(c.co?.links || []), ...(c.co?.history || [])];
    const filings = C.annualFilings(c.tk, hist);
    if (filings.length < 2) { host.append(el("div", { class: "sc-empty" }, `Only one annual report has been read for ${c.focus.name} so far; changes appear once a prior 10-K is processed.`)); return; }
    S.pair = Math.min(S.pair || 0, filings.length - 2);
    const pairs = el("div", { class: "sc-chips" }, ...filings.slice(0, -1).map((f, i) => el("button", { class: "sc-chip" + (S.pair === i ? " on" : ""), onclick: () => { S.pair = i; host.innerHTML = ""; tabChanges(host, c); } }, `${filings[i + 1].filed.slice(0, 4)} → ${f.filed.slice(0, 4)}`)));
    const newer = filings[S.pair], older = filings[S.pair + 1];
    const ch = C.linkChanges(c.tk, hist, newer.accn, older.accn);
    const q = (c.co?.q10 || [])[0] || (c.co?.links || []).find(l => l.form === "10-Q");
    host.append(pairs, el("div", { class: "faint", style: "font-size:11.5px" }, `Named counterparties in the 10-K filed ${fdate(newer.filed)} vs ${fdate(older.filed)}.`, q && S.pair === 0 ? ` The map also includes the 10-Q filed ${fdate(q.filed)}.` : ""));
    if (!ch.length) { host.append(el("div", { class: "sc-empty" }, "No named counterparties added or dropped.")); return; }
    ch.forEach(x => host.append(el("div", { class: "sc-card" },
      el("div", { style: "display:flex;gap:6px;align-items:center" }, relDot(x.relation), el("b", { style: "min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" }, x.name), el("span", { class: "sc-b " + (x.kind === "added" ? "ok" : x.kind === "dropped" ? "bad" : "mk"), style: "margin-left:auto" }, x.kind === "share" ? `${x.from}% → ${x.to}%` : x.kind)),
      el("div", { class: "meta" }, `${x.relation} · ${x.link.det}`),
      el("button", { class: "sc-q", onclick: () => openLink(x.link, x.prior ? { prior: x.prior, cross: `Share stated as ${x.from}% in the ${older.filed.slice(0, 4)} 10-K and ${x.to}% in the ${newer.filed.slice(0, 4)} 10-K.` } : {}) }, (x.kind === "dropped" ? `Last named in the ${x.link.filed.slice(0, 4)} 10-K: ` : "") + "“" + x.link.q + "”"))));
  }

  function tabShock(host, c, rerender) {
    host.append(el("div", { class: "faint", style: "font-size:11.5px" }, `If ${nm(c.src)}'s demand or output drops, its suppliers lose sales (sized by the revenue share they disclose) and its customers face supply disruption. Effects ripple two steps out; the map pulses from the shocked company.`));
    const opts = [...new Set([c.tk, ...c.graph.flatMap(n => n.ticker ? [n.ticker] : [])])];
    host.append(el("select", { class: "", style: "height:28px;border:1px solid var(--line2);border-radius:6px;font:inherit;font-size:12px", onchange: e => { S.shockSrc = e.target.value; rerender(); } }, ...opts.map(t => el("option", { value: t, selected: t === c.src }, `Shock ${nm(t)}`))));
    const lab = el("b", { class: "num" }, `−${S.shock}%`);
    const range = el("input", { type: "range", min: 5, max: 80, step: 5, value: S.shock, class: "sc-slider", "aria-label": "Size of shock" });
    range.addEventListener("input", () => { lab.textContent = `−${range.value}%`; });
    range.addEventListener("change", () => { S.shock = +range.value; rerender(); });
    host.append(el("div", { style: "display:flex;justify-content:space-between;font-size:12px" }, el("span", {}, "Size of shock"), lab), range);
    if (!c.impacts.length) { host.append(el("div", { class: "sc-empty" }, "No linked companies in the filings yet.")); return; }
    c.impacts.slice(0, 30).forEach(i => host.append(el("button", { class: "sc-imp " + i.severity, onclick: () => openLink(i.link, { eyebrow: `Shock path · ${i.channel}`, cross: `Path: ${i.path.join(" → ")}. ${i.revenuePct != null ? `${(i.revenuePct * 100).toFixed(2)}% of ${i.name}'s revenue at risk = stated share × shock size along the path.` : "No share is stated for this step, so the effect is not quantified."}` }) },
      el("div", { style: "display:flex;gap:6px" }, el("b", {}, i.name), el("span", { class: "faint", style: "margin-left:auto;font-size:11px" }, i.hop === 1 ? "direct" : `${i.hop} steps`)),
      el("div", {}, el("span", { style: i.channel === "lost sales" ? "color:var(--down)" : "" }, i.channel), i.revenuePct != null ? ` · ${(i.revenuePct * 100).toFixed(2)}% of revenue` : "", i.dollars != null ? ` · ≈${fmtMoney(i.dollars)}` : "", el("span", { class: "faint" }, ` · ${i.severity}`)),
      el("div", { class: "p" }, i.path.join(" → ")))));
    host.append(el("div", { class: "faint", style: "font-size:10.5px" }, "Mechanical estimate from disclosed shares only; not a forecast."));
  }

  function tabExposure(host, c) {
    if (!c.exposure.length) { host.append(el("div", { class: "sc-empty" }, `${c.focus.name}'s filings don't put a percentage or dollar figure on any named counterparty, and no other covered filing does for ${c.focus.name}.`)); return; }
    c.exposure.slice(0, 24).forEach(({ n, l, d }) => host.append(el("div", { class: "sc-card" },
      el("div", { style: "display:flex;gap:6px;align-items:center" }, relDot(n.relation), el("b", {}, n.name), el("span", { style: "margin-left:auto" }, N(fmtMoney(d), () => openLink(l), l.amt ? "Stated amount" : "Stated share × trailing revenue"))),
      el("div", { class: "meta" }, n.relation, l.sh != null ? ` · ${l.sh}% of ${nm(l.t)}'s ${l.rel === "customer" ? "revenue" : "purchases"}` : "", l.amt ? " · stated amount" : " · share × trailing revenue"),
      el("button", { class: "sc-src-btn", style: "align-self:flex-start", onclick: () => openLink(l) }, "Source"))));
  }

  /* ---------- lower panels ---------- */
  function drawLower(lower, c, rerender) {
    lower.innerHTML = "";
    const regions = S.regions?.countries || {};
    const ownPolicy = (c.co?.policy || []);
    const focusLinks = c.all.filter(l => l.t === c.tk || l.cpt === c.tk);
    const rows = C.countryExposure(focusLinks, ownPolicy, c.rev);
    const countries = [...new Set([...rows.map(r => r.country), ...Object.keys(regions)])];
    const setCountry = k => { S.country = k; drawLower(lower, c, rerender); };
    const r1 = el("div", { class: "sc-row2 a" }); lower.append(r1);
    r1.append(worldPanel(c, rows, setCountry), regionShockPanel(c, countries, setCountry));
    const r2 = el("div", { class: "sc-row2 b" }); lower.append(r2);
    r2.append(registerPanel(c, rows, regions, setCountry), dossierPanel(c, regions, rows));
    lower.append(insightsPanel(c, regions));
    lower.append(agreementsPanel(c));
    lower.append(policyPanel(c, regions));
    const r3 = el("div", { class: "sc-row2 c" }); lower.append(r3);
    r3.append(basketPanel(c), concHistoryPanel(c));
    const r4 = el("div", { class: "sc-row2 d" }); lower.append(r4);
    r4.append(intelPanel(c), dependencyPanel(c), footprintPanel(c));
    lower.append(fragilityPanel(c));
  }

  function worldPanel(c, rows, setCountry) {
    const scope = S.mapScope || "focus";
    const p = panel("World exposure", seg([["focus", c.focus.name], ["all", "All coverage"]], scope, v => { S.mapScope = v; drawLower(document.querySelector(".sc-page .sc-page"), c); }));
    const host = el("div", { style: "border:1px solid var(--line);border-radius:6px;overflow:hidden" });
    const body = el("div", { class: "bd" }, host);
    p.append(body);
    const data = scope === "all" ? C.countryExposure(c.all, Object.values(S.co).filter(Boolean).flatMap(x => x.policy || []), c.rev) : rows;
    V.worldMap(host, { rows: data, selected: S.country, arcs: scope === "focus", onSelect: setCountry }).then(r => { if (r.unmapped.length) body.append(el("div", { class: "faint", style: "font-size:11px;margin-top:6px" }, "Also named: " + r.unmapped.slice(0, 6).map(u => `${u.country} (${u.links + u.hq + u.policy})`).join(", "))); }).catch(e => host.append(el("div", { class: "sc-empty" }, String(e.message || e))));
    const list = el("div", { style: "display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:2px 16px;margin-top:8px" });
    const max = Math.max(1, ...data.map(r => r.links + r.hq + r.policy));
    data.slice(0, 10).forEach(r => list.append(el("button", { class: "sc-li" + (S.country === r.country ? " on" : ""), style: S.country === r.country ? "background:#fffbe3" : "", onclick: () => setCountry(r.country) }, el("span", { class: "nm", style: "font-weight:600" }, (r.hotspot ? "⚠ " : "") + r.country), el("span", { class: "faint", style: "font-size:11px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" }, r.companies.slice(0, 3).join(" · ")), el("span", { class: "sc-bar", style: "width:80px;margin-left:auto;flex:none" }, el("i", { style: `width:${(r.links + r.hq + r.policy) / max * 100}%;background:${r.hotspot ? "var(--down)" : "var(--c1)"}` })), el("span", { class: "r" }, String(r.links + r.hq + r.policy)))));
    body.append(list, el("div", { class: "faint", style: "font-size:11px;margin-top:6px" }, "Blue = stated tie · red = geopolitical hotspot · dashed ring = single-source · arcs from the U.S.; solid = single-source"));
    return p;
  }

  function regionShockPanel(c, countries, setCountry) {
    const k = S.country || (countries.includes("Taiwan") ? "Taiwan" : countries[0] || "Taiwan");
    const allPolicy = Object.values(S.co).filter(Boolean).flatMap(x => x.policy || []);
    const hits = C.regionShock(k, c.all, allPolicy, c.rev), max = Math.max(1, ...hits.map(h => h.score));
    const p = panel(`Region shock · ${k}`, null);
    const body = el("div", { class: "bd", style: "max-height:560px;overflow:auto" });
    p.append(body);
    body.append(el("div", { class: "sc-chips", style: "margin-bottom:8px" }, ...["Taiwan", "China", "Mexico", "Israel", "South Korea", "Japan", "India", "Germany"].filter(x => countries.includes(x)).map(x => el("button", { class: "sc-chip" + (x === k ? " on" : ""), onclick: () => setCountry(x) }, x))));
    body.append(el("div", { class: "faint", style: "font-size:11.5px;margin-bottom:6px" }, `If ${k} is cut off (export controls, conflict, tariffs), these companies are exposed through their own filings. Score: single-source ties ×25, stated $ vs revenue (max 40), HQ'd counterparties ×6, other links ×4, policy statements ×3.`));
    if (!hits.length) body.append(el("div", { class: "sc-empty" }, `No company read so far ties a link or policy statement to ${k}.`));
    const linkById = new Map(c.all.map(l => [l.id, l])), polById = new Map(allPolicy.map(x => [x.id, x]));
    hits.slice(0, 30).forEach(h => body.append(el("div", { class: "sc-li", style: "cursor:default" }, miniLogo(h.ticker, 20),
      el("div", { style: "min-width:0;flex:1" }, el("div", {}, el("a", { href: `#supply-${h.ticker}`, style: "font-weight:600" }, h.ticker), " ", el("span", { class: "faint", style: "font-size:11px" }, nm(h.ticker))),
        el("div", { class: "sc-bar", style: "margin-top:3px" }, el("i", { style: `width:${h.score / max * 100}%;background:${h.severity === "high" ? "var(--down)" : h.severity === "medium" ? "var(--warn)" : "var(--faint)"}` })),
        el("div", { class: "faint", style: "font-size:10.5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis" }, [h.singleSource && `${h.singleSource} single-source`, h.hq && `${h.hq} HQ-based counterparties`, h.links && `${h.links} stated links`, h.policy && `${h.policy} policy`, h.dollars && fmtMoney(h.dollars)].filter(Boolean).join(" · "))),
      N(String(h.score), () => openMany(`${h.ticker}: ties to ${k}`, h.ids.map(id => linkById.get(id) ? linkById.get(id) : polById.get(id) ? policySource(polById.get(id)) : null).filter(Boolean), `Region shock · ${k}`, `score ${h.score}`)))));
    return p;
  }

  const govBar = (v, label) => v == null ? el("span", { class: "faint" }, "–") : el("span", { class: "sc-gov", title: `${label}: ${v.toFixed(2)} (World Bank WGI, −2.5 weak … +2.5 strong)` }, el("span", { class: "t" }, el("i", { style: v < 0 ? `right:50%;width:${Math.min(50, Math.abs(v) / 2.5 * 50)}%;background:var(--down)` : `left:50%;width:${Math.min(50, v / 2.5 * 50)}%;background:var(--up)` })), el("span", { class: "num faint", style: "font-size:10.5px;width:34px;text-align:right" }, (v > 0 ? "+" : "") + v.toFixed(2)));

  function registerPanel(c, rows, regions, setCountry) {
    const p = panel(`Region risk register · ${c.focus.name}`, null);
    const body = el("div", { class: "bd", style: "overflow:auto;max-height:520px" }); p.append(body);
    const rank = { high: 4, elevated: 3, moderate: 2, unrated: 1, low: 0 };
    const rr = rows.filter(r => r.country !== "United States").map(r => ({ r, s: regions[r.country]?.score })).sort((a, b) => (rank[b.s?.tier || "unrated"] + (b.r.hotspot ? 1.5 : 0) + Math.min(3, b.r.singleSource / 2)) - (rank[a.s?.tier || "unrated"] + (a.r.hotspot ? 1.5 : 0) + Math.min(3, a.r.singleSource / 2)));
    body.append(el("div", { class: "faint", style: "font-size:11.5px;margin-bottom:6px" }, `Every country ${c.focus.name}'s filings, named counterparties or policy statements tie to, ranked by governance risk (World Bank WGI), hotspot status and single-source ties. Pick a row for its dossier.`));
    if (!rr.length) { body.append(el("div", { class: "sc-empty" }, `No non-U.S. country is tied to ${c.tk} in the filings read so far.`)); return p; }
    const linkById = new Map(c.all.map(l => [l.id, l])), polById = new Map((c.co?.policy || []).map(x => [x.id, x]));
    const t = el("table", { class: "sc-tbl", style: "min-width:640px" }, el("thead", {}, el("tr", {}, ...["Country", "Risk", "Stability", "Rule of law", "Regulation", "GDP g.", `Ties in ${c.tk}'s sources`, "Stated $"].map((h, i) => el("th", { class: i >= 5 && i !== 6 ? "r" : "" }, h)))));
    const tb = el("tbody");
    rr.forEach(({ r, s }) => tb.append(el("tr", { class: "click" + (S.country === r.country ? " on" : ""), onclick: () => setCountry(r.country) },
      el("td", { style: "font-weight:600" }, r.country, r.hotspot ? el("span", { class: "sc-b bad", style: "margin-left:5px" }, "hotspot") : null), el("td", {}, s?.url ? el("a", { href: s.url, target: "_blank", rel: "noopener", onclick: e => e.stopPropagation() }, tierBadge(s.tier)) : tierBadge(s?.tier)),
      el("td", {}, govBar(s?.stability, "Political stability")), el("td", {}, govBar(s?.ruleOfLaw, "Rule of law")), el("td", {}, govBar(s?.regulatory, "Regulatory quality")),
      el("td", { class: "r num" }, s?.gdpGrowth != null ? s.gdpGrowth.toFixed(1) + "%" : "–"),
      el("td", { style: "font-size:11px" }, N([r.links && `${r.links} links`, r.hq && `${r.hq} HQ'd there`, r.singleSource && `${r.singleSource} single-src`, r.policy && `${r.policy} policy`].filter(Boolean).join(" · "), () => openMany(`${c.focus.name}: ties to ${r.country}`, r.ids.map(id => linkById.get(id) || (polById.get(id) ? policySource(polById.get(id)) : null)).filter(Boolean), `Region · ${r.country}`))),
      el("td", { class: "r num" }, r.dollars ? fmtMoney(r.dollars) : "–"))));
    t.append(tb); body.append(t);
    const yr = Object.values(regions).find(x => x.score?.year)?.score?.year;
    body.append(el("div", { class: "faint", style: "font-size:10.5px;margin-top:6px" }, `Governance: World Bank Worldwide Governance Indicators${yr ? ` (${yr})` : ""}; the World Bank does not publish Taiwan. Tier uses the weakest of the three estimates. Retrieved by the supply job${S.regions?.generated ? " " + fdate(S.regions.generated.slice(0, 10)) : ""}.`));
    return p;
  }

  function dossierPanel(c, regions, rows) {
    const k = S.country || rows.find(r => r.country !== "United States")?.country || "Taiwan";
    const d = regions[k] || {}, s = d.score || {};
    const p = panel(`Region dossier · ${k}`, null);
    const body = el("div", { class: "bd", style: "max-height:520px;overflow:auto" }); p.append(body);
    const tile = (l, v) => el(s.url ? "a" : "div", { class: "sc-kpi", href: s.url || null, target: "_blank", rel: "noopener", style: "cursor:pointer" }, el("div", { class: "l" }, l), el("div", { class: "v", style: "font-size:14px" }, v));
    body.append(el("div", { style: "display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:6px" }, tile("Governance tier", tierBadge(s.tier)), tile("Political stability", s.stability != null ? s.stability.toFixed(2) : "–"), tile("Rule of law", s.ruleOfLaw != null ? s.ruleOfLaw.toFixed(2) : "–"), tile("Real GDP growth", s.gdpGrowth != null ? s.gdpGrowth.toFixed(1) + "%" : "–")));
    const own = (c.co?.policy || []).filter(x => x.countries.includes(k));
    const linksHere = c.all.filter(l => (l.t === c.tk || l.cpt === c.tk) && ((l.rg || []).map(C.canonCountry).includes(k) || C.hqOf(l) === k));
    body.append(el("div", { class: "sc-sec" }, el("div", { class: "h" }, `What ${c.focus.name}'s filings say about ${k}`, el("span", { class: "num" }, `(${own.length + linksHere.length})`)),
      !own.length && !linksHere.length ? el("div", { class: "sc-empty", style: "padding:4px 0" }, `No statement in ${c.tk}'s filings names ${k}.`) : null,
      ...own.slice(0, 6).map(x => el("button", { class: "sc-q", style: "display:block;width:100%;margin-bottom:4px", onclick: () => DRW.openEvidence({ eyebrow: `Policy factor · ${x.kind.replace("_", " ")}`, title: `${c.focus.name} on ${x.countries.join(", ")}`, sources: [policySource(x), ...(regions[k]?.policy || []).filter(dd => dd.q).slice(0, 3).map(frSource)], cross: (regions[k]?.policy || []).length ? `Cross-reference: ${regions[k].policyTotal} U.S. trade, export-control or sanctions notices naming ${k} in the last 12 months (Federal Register).` : null }) }, el("span", { class: "sc-b warn", style: "font-style:normal;margin-right:5px" }, x.kind.replace("_", " ")), "“" + x.q + "”")),
      ...linksHere.slice(0, 6).map(l => el("button", { class: "sc-q", style: "display:block;width:100%;margin-bottom:4px", onclick: () => openLink(l) }, el("span", { class: "sc-b", style: "font-style:normal;margin-right:5px" }, `${l.rel}: ${l.t === c.tk ? l.cp : nm(l.t)}`), "“" + l.q + "”"))));
    const fr = d.policy || [];
    body.append(el("div", { class: "sc-sec" }, el("div", { class: "h" }, `U.S. trade & sanctions actions naming ${k} · 12 mo`, el("span", { class: "num" }, `(${d.policyTotal ?? fr.length})`)),
      !fr.length ? el("div", { class: "sc-empty", style: "padding:4px 0" }, d.policy ? "No BIS, USTR, OFAC, ITA, ITC or CBP notice named this country in the last year." : "Federal Register actions are collected by the scheduled job; none stored for this country yet.") : null,
      ...fr.slice(0, 10).map(x => el("button", { class: "sc-li", style: "align-items:flex-start", onclick: () => DRW.openEvidence({ eyebrow: `Federal Register · ${x.agency}`, title: x.title, sub: `${x.type} · published ${fdate(x.date)}`, sources: [frSource(x)], footer: `Source: ${x.source}. Retrieved ${fdate((x.retrieved || "").slice(0, 10))}.` }) }, el("span", { class: "num faint", style: "font-size:10.5px;width:72px;flex:none" }, x.date), el("span", { style: "min-width:0;font-size:12px" }, x.title, el("span", { class: "faint", style: "display:block;font-size:10.5px" }, `${x.agency} · ${x.type}`))))));
    body.append(el("div", { class: "faint", style: "font-size:10.5px;margin-top:8px" }, "Sources: World Bank (governance, GDP), Federal Register API (U.S. agency actions), SEC filings (company statements). Region news is collected by the scheduled job (Tavily) when its key is configured."));
    return p;
  }

  function insightsPanel(c, regions) {
    const ai = c.co?.insights;
    const p = panel("Cross-source insights", el("span", { class: "faint", style: "font-size:11px" }, "Each claim links to the statements it rests on"));
    const body = el("div", { class: "bd" }); p.append(body);
    const linkById = new Map([...c.all, ...(c.co?.history || [])].map(l => [l.id, l]));
    const refSource = id => {
      if (linkById.has(id)) return { ...fileSource(linkById.get(id), linkById.get(id).rel), label: `${nm(linkById.get(id).t)} → ${linkById.get(id).cp}` };
      const raw = id.replace(/^L/, "");
      if (linkById.has(raw)) return { ...fileSource(linkById.get(raw), linkById.get(raw).rel), label: `${nm(linkById.get(raw).t)} → ${linkById.get(raw).cp}` };
      if (id.startsWith("F")) { for (const k of Object.keys(regions)) { const d = (regions[k].policy || []).find(x => "F" + x.id === id); if (d) return frSource(d); } }
      const pol = (c.co?.policy || []).find(x => x.id === id); if (pol) return policySource(pol);
      const ag = (c.co?.agreements || []).find(a => id.startsWith(a.id)); if (ag) { const q = ag.quotes[+id.split("q")[1] || 0]; return { kind: "filing", t: ag.t, accn: ag.accn, doc: ag.doc, form: ag.form, filed: ag.filed, quote: q?.q, url: ag.url, path: `supply/docs/${ag.accn}/${ag.doc}.json.gz`, label: `${nm(ag.t)} agreement` }; }
      return null;
    };
    const claim = (it, i, label) => el("div", { class: "it" }, el("span", { class: "k" }, it.kind), el("span", {}, it.text, ...it.refs.map((r, j) => el("button", { class: "sc-ref", title: "Open source", onclick: () => DRW.openEvidence({ eyebrow: label, title: it.text.slice(0, 90) + (it.text.length > 90 ? "…" : ""), sources: it.refs.map(refSource).filter(Boolean), cross: it.refs.length > 1 ? `This claim combines ${it.refs.length} sources.` : null, ai: label === "AI-generated" ? `AI-generated by ${ai?.model || "the scheduled model"} on ${fdate((ai?.generated || "").slice(0, 10))}; ${it.status === "grounded" ? "every number appears in a cited source" : "unverified: " + (it.unverified_numbers || []).join(", ") + " not found in the cited sources"}.` : null }) }, String(j + 1))), it.status === "unverified" ? el("span", { class: "sc-b warn", style: "margin-left:4px" }, "unverified") : null));
    const g = el("div", { class: "sc-row2 b" });
    const left = el("div", {}), right = el("div", {});
    g.append(left, right); body.append(g);
    left.append(el("div", { style: "display:flex;gap:8px;align-items:center;margin-bottom:6px" }, el("span", { class: "sc-ailabel" }, svgUse("i-spark"), "AI-generated"), ai?.generated ? el("span", { class: "faint", style: "font-size:11px" }, `${ai.model} · ${fdate(ai.generated.slice(0, 10))} · from cited records only`) : null));
    if (ai?.insights?.length) {
      if (ai.headline) left.append(el("div", { style: "font-weight:600;margin-bottom:6px" }, ai.headline));
      left.append(el("div", { class: "sc-ins" }, ...ai.insights.map((it, i) => claim(it, i, "AI-generated"))));
      if (ai.watch?.length) left.append(el("div", { class: "note", style: "margin-top:8px" }, el("b", {}, "Watch next: "), ai.watch.join(" · ")));
    } else left.append(el("div", { class: "sc-empty", style: "padding:4px 0" }, "AI insights are written by the scheduled job from this company's cited links, agreements, policy statements and Federal Register actions (never on page load). None stored for this company yet; the computed cross-source signals alongside are always current."));
    const comp = C.crossInsights(c.tk, c.all, c.co?.history || [], regions, c.nms);
    right.append(el("div", { style: "display:flex;gap:8px;align-items:center;margin-bottom:6px" }, el("span", { class: "sc-b mk" }, "Computed"), el("span", { class: "faint", style: "font-size:11px" }, "Rule-based from filings across coverage, not AI")));
    right.append(comp.length ? el("div", { class: "sc-ins" }, ...comp.slice(0, 7).map((it, i) => claim(it, i, "Computed cross-source signal"))) : el("div", { class: "sc-empty", style: "padding:4px 0" }, "No cross-company signal yet: needs another covered filing that names the same counterparty or this company."));
    return p;
  }

  function openAgreement(a, quote) {
    const srcs = [{ kind: "filing", t: a.t, accn: a.accn, doc: a.doc, form: a.form, filed: a.filed, quote: quote || a.quotes[0]?.q, also: a.quotes.map(q => q.q), url: a.url, path: `supply/docs/${a.accn}/${a.doc}.json.gz`, label: `${nm(a.t)} ${a.form} Item 1.01`, full: false },
      ...a.exhibits.map(e => ({ kind: "filing", t: a.t, accn: a.accn, doc: e.doc, form: `${a.form} EX-10`, filed: a.filed, quote: e.quotes[0]?.q, also: e.quotes.map(q => q.q), url: e.url, path: `supply/docs/${a.accn}/${e.doc}.json.gz`, label: `Exhibit ${e.doc}`, full: false }))];
    DRW.openEvidence({ eyebrow: "Material agreement", title: a.title || `${nm(a.t)} ${a.form} filed ${fdate(a.filed)}`, sub: `Parties named: ${a.parties.map(p => p.cp).join(", ") || "not stated"} · filed ${fdate(a.filed)}`, sources: srcs, footer: "Terms are read word-for-word from the 8-K Item 1.01 text and its EX-10 exhibits; highlighted words are exactly what the source says. Many exhibits redact figures." });
  }

  function agreementsPanel(c) {
    const p = panel(`Agreement terms · ${c.focus.name}`, null);
    const body = el("div", { class: "bd" }); p.append(body);
    const rows = (c.co?.agreements || []).map(a => ({ a, t: C.agreementTerms(a.quotes.map(q => q.q).join(" ") + " " + a.exhibits.flatMap(e => e.quotes.map(q => q.q)).join(" ")) }));
    const clauses = new Map(); rows.forEach(r => r.t.clauses.forEach(x => clauses.set(x, (clauses.get(x) || 0) + 1)));
    body.append(el("div", { class: "faint", style: "font-size:11.5px;margin-bottom:6px" }, `Dates, term lengths, dollar amounts, percentages and contract clauses read word-for-word from ${c.tk}'s 8-K Item 1.01 filings (material definitive agreements, last three years) and their EX-10 exhibits.`));
    if (clauses.size) body.append(el("div", { class: "sc-chips", style: "margin-bottom:6px" }, el("button", { class: "sc-chip" + (!S.clause ? " on" : ""), onclick: () => { S.clause = null; p.replaceWith(agreementsPanel(c)); } }, `All ${rows.length}`), ...[...clauses].sort((a, b) => b[1] - a[1]).map(([k, n]) => el("button", { class: "sc-chip" + (S.clause === k ? " on" : ""), onclick: () => { S.clause = k; p.replaceWith(agreementsPanel(c)); } }, `${k} ${n}`))));
    const shown = S.clause ? rows.filter(r => r.t.clauses.includes(S.clause)) : rows;
    if (!shown.length) { body.append(el("div", { class: "sc-empty" }, `No supply, licensing or collaboration agreement filed under 8-K Item 1.01 by ${c.tk} in the last three years (financing agreements are excluded).`)); return p; }
    shown.forEach(({ a, t }) => body.append(el("div", { style: "display:grid;grid-template-columns:170px minmax(0,1fr);gap:10px;padding:8px 0;border-bottom:1px solid var(--line)" },
      el("div", { style: "font-size:11.5px;min-width:0" }, el("div", { style: "font-weight:600" }, a.parties.map(x => x.cp).join(", ") || "Counterparty not named"), el("div", { class: "faint" }, `${a.form} · ${fdate(a.filed)}`), a.exhibits.length ? el("div", { class: "faint" }, `${a.exhibits.length} EX-10 exhibit${a.exhibits.length > 1 ? "s" : ""}`) : null),
      el("div", { style: "min-width:0;display:flex;flex-direction:column;gap:4px" }, a.title ? el("div", { style: "font-size:12px;font-weight:500" }, a.title) : null,
        el("div", { class: "sc-badges" }, ...t.clauses.map(x => el("span", { class: "sc-b" }, x)), ...t.durations.map(x => el("span", { class: "sc-b mk" }, x))),
        ...[...a.quotes, ...a.exhibits.flatMap(e => e.quotes)].slice(0, 3).map(q => el("button", { class: "sc-q", onclick: () => openAgreement(a, q.q) }, "“", ...C.markTerms(q.q, q.terms).map(s => s.hit ? el("mark", {}, s.s) : s.s), "”"))))));
    return p;
  }

  function policyPanel(c, regions) {
    const pol = c.co?.policy || [];
    const p = panel(`Political & external factors · ${c.focus.name}`, el("span", { class: "faint", style: "font-size:11px" }, "Filing statements cross-referenced with U.S. agency actions"));
    const body = el("div", { class: "bd" }); p.append(body);
    if (!pol.length) { body.append(el("div", { class: "sc-empty" }, `${c.tk}'s latest filings tie no export control, tariff, sanction or conflict statement to a named country.`)); return p; }
    const kinds = [...new Set(pol.map(x => x.kind))];
    const grid = el("div", { style: "display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:8px" });
    const limit = S.polAll ? pol.length : 9;
    pol.slice(0, limit).forEach(x => {
      const fr = x.countries.flatMap(k => (regions[k]?.policy || []).filter(d => d.q).slice(0, 2));
      const tot = x.countries.reduce((s, k) => s + (regions[k]?.policyTotal || 0), 0);
      grid.append(el("div", { class: "sc-card" },
        el("div", { class: "sc-badges" }, el("span", { class: "sc-b warn" }, x.kind.replace("_", " ")), ...x.countries.map(k => el("span", { class: "sc-b" + (C.isHot(k) ? " bad" : "") }, k)), el("span", { class: "faint", style: "font-size:10.5px;margin-left:auto" }, `${x.form} ${fdate(x.filed)}`)),
        el("button", { class: "sc-q", onclick: () => DRW.openEvidence({ eyebrow: `Policy factor · ${x.kind.replace("_", " ")}`, title: `${c.focus.name} on ${x.countries.join(", ")}`, sources: [policySource(x), ...fr.map(frSource)], cross: tot ? `Cross-reference: ${tot} U.S. trade, export-control or sanctions notices naming ${x.countries.join(" or ")} in the last 12 months (Federal Register). Shown with the filing statement.` : null }) }, "“" + x.q + "”"),
        tot ? el("div", { class: "faint", style: "font-size:11px" }, N(`${tot} Federal Register actions`, () => DRW.openEvidence({ eyebrow: "Federal Register", title: `U.S. actions naming ${x.countries.join(", ")}`, sources: fr.length ? fr.map(frSource) : x.countries.flatMap(k => (regions[k]?.policy || []).slice(0, 4)).map(frSource) })), " in the last 12 months") : null));
    });
    body.append(el("div", { class: "faint", style: "font-size:11.5px;margin-bottom:8px" }, `${pol.length} statements in the latest 10-K and 10-Q (${kinds.map(k => k.replace("_", " ")).join(", ")}).`), grid);
    if (pol.length > 9) body.append(el("button", { class: "btn", style: "margin-top:8px", onclick: () => { S.polAll = !S.polAll; p.replaceWith(policyPanel(c, regions)); } }, S.polAll ? "Show fewer" : `Show all ${pol.length}`));
    return p;
  }

  function lineChart(host, series, dates, o = {}) {
    host.innerHTML = "";
    const W = host.clientWidth || 600, H = o.h || 220, m = { t: 8, r: 10, b: 20, l: 36 };
    const svg = d3.select(host).append("svg").attr("class", "sc-spark").attr("viewBox", `0 0 ${W} ${H}`).attr("width", W).attr("height", H);
    const vals = series.flatMap(s => s.v.filter(v => v != null));
    if (!vals.length) return;
    const x = d3.scalePoint().domain(dates).range([m.l, W - m.r]), y = d3.scaleLinear().domain(o.domain || d3.extent(vals)).nice().range([H - m.b, m.t]);
    svg.append("g").attr("class", "ax").attr("transform", `translate(0,${H - m.b})`).call(d3.axisBottom(x).tickValues(dates.filter((_, i) => i % Math.max(1, Math.ceil(dates.length / 6)) === 0)).tickFormat(o.xfmt || (d => d)).tickSize(0));
    svg.append("g").attr("class", "ax").attr("transform", `translate(${m.l},0)`).call(d3.axisLeft(y).ticks(4).tickFormat(o.yfmt || (v => v)).tickSize(-(W - m.l - m.r))).selectAll("line").attr("stroke-dasharray", "2 3");
    series.forEach(s => {
      const pts = dates.map((d, i) => [d, s.v[i]]).filter(p => p[1] != null);
      svg.append("path").attr("d", d3.line().x(p => x(p[0])).y(p => y(p[1]))(pts)).attr("fill", "none").attr("stroke", s.color).attr("stroke-width", s.w || 1.8).attr("stroke-dasharray", s.dash || null);
      if (o.dots) pts.forEach(p => svg.append("circle").attr("cx", x(p[0])).attr("cy", y(p[1])).attr("r", 3.5).attr("fill", s.color).style("cursor", "pointer").on("click", () => s.onClick?.(p[0])).on("mouseenter", e => showTip(e, `${esc(s.name)}<br>${o.tfmt ? o.tfmt(p[1]) : p[1]} · ${p[0]}`)).on("mouseleave", hideTip));
    });
  }

  function basketPanel(c) {
    const px = c.co?.px;
    const right = el("div", { style: "display:flex;gap:6px" }, seg([["all", "All"], ["supplier", "Suppliers"], ["customer", "Customers"]], S.basketSide, v => { S.basketSide = v; p.replaceWith(basketPanel(c)); }), seg([[63, "3M"], [126, "6M"], [252, "1Y"], [750, "3Y"]], S.basketWin, v => { S.basketWin = +v; p.replaceWith(basketPanel(c)); }));
    const p = panel(`${c.focus.name} ecosystem basket`, right);
    const body = el("div", { class: "bd" }); p.append(body);
    if (!px?.dates?.length) { body.append(el("div", { class: "sc-empty" }, "Basket prices are assembled by the scheduled job from stored daily closes; not available for this company yet.")); return p; }
    const rel = new Map(c.graph.map(n => [n.ticker, n.relation]));
    const members = [...new Set(c.graph.filter(n => n.ticker && n.relation !== "competitor" && px.c[n.ticker] && (S.basketSide === "all" || n.relation === S.basketSide)).map(n => n.ticker))];
    const returns = Object.fromEntries(Object.entries(px.c).map(([t, v]) => [t, C.closesToReturns(v)]));
    const b = C.ecosystemBasket(c.tk, members, px.dates, returns, S.basketWin);
    if (!b) { body.append(el("div", { class: "sc-empty" }, `No covered, listed ${S.basketSide === "all" ? "suppliers or customers" : S.basketSide + "s"} with stored prices to build a basket from.`)); return p; }
    const g = el("div", { class: "sc-row2 b" }); body.append(g);
    const left = el("div", {}), chart = el("div", {});
    const kpi = (l, v) => el("div", { class: "sc-kpi", style: "cursor:default" }, el("div", { class: "l" }, l), el("div", { class: "v " + cls(v), style: "font-size:14px" }, v == null ? "–" : fsgn(v)));
    left.append(el("div", { style: "display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:6px;margin-bottom:6px" }, kpi(`Basket (${b.members.length})`, b.basketRet), kpi(c.focus.name, b.focusRet), kpi("S&P 500 (SPY)", b.spyRet), el("div", { class: "sc-kpi", style: "cursor:default" }, el("div", { class: "l" }, "Dispersion"), el("div", { class: "v", style: "font-size:14px" }, fpct(b.dispersion, 0)))), chart);
    const right2 = el("div", {}, el("table", { class: "sc-tbl" }, el("thead", {}, el("tr", {}, el("th", {}, "Member"), el("th", { class: "r" }, "Return"), el("th", { class: "r" }, "Vol"), el("th", { class: "r" }, "ρ"))), el("tbody", {}, ...b.members.map(m => el("tr", { class: "click", onclick: () => { location.hash = `company-${m.ticker}`; } }, el("td", {}, miniLogo(m.ticker, 14), " ", el("b", {}, m.ticker), " ", el("span", { class: "faint", style: "font-size:10.5px" }, rel.get(m.ticker))), el("td", { class: "r num " + cls(m.ret) }, fsgn(m.ret, 0)), el("td", { class: "r num faint" }, fpct(m.vol, 0)), el("td", { class: "r num" }, m.corr != null ? m.corr.toFixed(2) : "–"))))),
      el("div", { class: "faint", style: "font-size:10.5px;margin-top:4px" }, `Equal-weight, rebalanced daily. ρ = daily correlation with ${c.tk}. Prices: stored split-adjusted daily closes (tech/bars).`));
    g.append(left, right2);
    requestAnimationFrame(() => lineChart(chart, [{ name: "Basket", v: b.index.map(x => x.basket), color: "var(--c1)", w: 2 }, { name: c.focus.name, v: b.index.map(x => x.focus), color: "var(--ink)", w: 1.5 }, { name: "SPY", v: b.index.map(x => x.spy), color: "var(--faint)", dash: "4 3", w: 1.2 }], b.index.map(x => x.date), { xfmt: d => fmon(d), h: 200 }));
    return p;
  }

  function concHistoryPanel(c) {
    const p = panel("Customer concentration history", null);
    const body = el("div", { class: "bd" }); p.append(body);
    const hist = [...(c.co?.links || []), ...(c.co?.history || [])];
    const h = C.concentrationHistory(c.tk, hist);
    const unnamed = (c.co?.conc || []).filter(x => x.form === "10-K" && x.unnamed);
    if (h.points.length < 2) {
      body.append(el("div", { class: "sc-empty" }, `${c.focus.name}'s 10-Ks don't state a named customer's revenue share across two or more years.`));
      if (unnamed.length) body.append(el("div", { class: "sc-sec" }, el("div", { class: "h" }, "Unnamed customer shares stated"), ...unnamed.slice(0, 5).map(x => el("button", { class: "sc-q", style: "display:block;width:100%;margin-bottom:4px", onclick: () => DRW.openEvidence({ eyebrow: "Customer concentration", title: `${c.focus.name}: ${x.pct}% from one customer (unnamed)`, value: `${x.pct}%`, sources: [policySource({ ...x, kind: "concentration", countries: [] })] }) }, `${x.filed.slice(0, 4)} 10-K · ${x.pct}% · “${x.q}”`))));
      return p;
    }
    const COLORS = ["var(--c1)", "var(--c2)", "var(--c3)", "var(--c4)", "var(--c5)", "var(--down)"];
    const chart = el("div", {}); body.append(chart);
    const linkAt = (s, filed) => hist.find(l => l.cp === s && l.filed === filed && l.form === "10-K") || hist.find(l => C.entityKey(l.cp, l.cpt) === C.entityKey(s) && l.filed === filed);
    requestAnimationFrame(() => lineChart(chart, h.series.map((s, i) => ({ name: s, v: h.points.map(pt => pt[s] ?? null), color: COLORS[i], onClick: filed => { const l = linkAt(s, filed); if (l) openLink(l); } })), h.points.map(pt => pt.filed), { xfmt: d => d.slice(0, 4), yfmt: v => v + "%", tfmt: v => v + "% of revenue", dots: true, h: 190 }));
    body.append(el("div", { class: "sc-chips", style: "margin-top:4px" }, ...h.series.map((s, i) => el("span", { class: "faint", style: "font-size:11px" }, el("span", { style: `display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:4px;background:${COLORS[i]}` }), s))), el("div", { class: "faint", style: "font-size:10.5px;margin-top:4px" }, "Click a point for the 10-K sentence."));
    return p;
  }

  function intelPanel(c) {
    const items = c.co?.intel || [];
    const p = panel("Counterparty evidence · news and press", el("span", { class: "faint", style: "font-size:11px" }, "Corroborating coverage, not filings"));
    const body = el("div", { class: "bd", style: "max-height:420px;overflow:auto" }); p.append(body);
    if (!items.length) { body.append(el("div", { class: "sc-empty" }, "News and press releases about each relationship are collected by the scheduled job (Tavily search, Firecrawl page reads) and kept only when a sentence names both companies verbatim. None stored for this company yet.")); return p; }
    const linkById = new Map(c.all.map(l => [l.id, l]));
    const by = new Map(); items.forEach(n => { const l = linkById.get(n.link); const k = l ? l.cp : "Other"; if (!by.has(k)) by.set(k, []); by.get(k).push(n); });
    for (const [k, ns] of by) body.append(el("div", { class: "sc-sec" }, el("div", { class: "h" }, k, el("span", { class: "num" }, `(${ns.length})`)), ...ns.map(n => el("button", { class: "sc-q", style: "display:block;width:100%;margin-bottom:4px", onclick: () => { const l = linkById.get(n.link); if (l) openLink(l); else DRW.openEvidence({ title: n.title, sources: [{ kind: "web", ...n, quote: n.q }] }); } }, el("span", { class: "sc-b", style: "font-style:normal;margin-right:5px" }, `${n.host}${n.date ? " · " + n.date : ""}`), "“" + n.q + "”"))));
    body.append(el("div", { class: "faint", style: "font-size:10.5px" }, "Provider: Tavily search results; each sentence is verbatim in the returned page text."));
    return p;
  }

  function dependencyPanel(c) {
    const d = C.dependencyScore(c.tk, c.all);
    const p = panel("Dependency score", null);
    const body = el("div", { class: "bd" }); p.append(body);
    const own = c.all.filter(l => l.t === c.tk);
    const partLinks = [own.filter(l => l.rel === "customer" && l.sh != null), own.filter(l => l.ss && l.rel !== "customer"), own.filter(l => (l.rg || []).some(C.isHot))];
    body.append(el("div", { style: "display:flex;align-items:baseline;gap:6px" }, el("span", { class: "num", style: `font-size:28px;font-weight:600;color:${d.score >= 50 ? "var(--down)" : d.score >= 25 ? "var(--warn)" : "var(--up)"}` }, String(d.score)), el("span", { class: "faint", style: "font-size:11.5px" }, "/ 100 · higher = more fragile")));
    d.parts.forEach((x, i) => body.append(el("div", { style: "margin-top:8px" }, el("div", { style: "display:flex;justify-content:space-between;font-size:12px" }, el("b", {}, x.label), N(`${x.points}/${x.max}`, () => openMany(`${c.focus.name}: ${x.label.toLowerCase()}`, partLinks[i], "Dependency score"))), el("div", { class: "sc-bar", style: "margin-top:3px" }, el("i", { style: `width:${x.points / x.max * 100}%;background:var(--ink2)` })), el("div", { class: "faint", style: "font-size:11px;margin-top:2px" }, x.why))));
    body.append(el("div", { class: "faint", style: "font-size:11px;margin-top:8px" }, `Customer HHI ${d.customerHhi.toLocaleString()} · ${d.statedCustomerPct}% of revenue tied to named customers`));
    const un = (c.co?.conc || []).filter(x => x.cur && x.unnamed).sort((a, b) => b.pct - a.pct)[0];
    if (un && d.statedCustomerPct < un.pct) body.append(el("button", { class: "sc-q", style: "margin-top:6px;font-style:normal", onclick: () => DRW.openEvidence({ eyebrow: "Customer concentration", title: `${c.focus.name}: unnamed customer at ${un.pct}%`, value: `${un.pct}%`, sources: [policySource({ ...un, kind: "concentration", countries: [] })] }) }, `Filing also states an unnamed customer at ${un.pct}% of revenue, not counted above because no name is given.`));
    return p;
  }

  function footprintPanel(c) {
    const rows = C.regionExposure(c.graph.filter(n => n.relation !== "competitor"));
    const p = panel("Geographic footprint", null);
    const body = el("div", { class: "bd" }); p.append(body);
    if (!rows.length) { body.append(el("div", { class: "sc-empty" }, "No regions tied to links in the filings.")); return p; }
    const max = Math.max(1, ...rows.map(r => r.suppliers + r.customers + r.other));
    rows.slice(0, 10).forEach(r => body.append(el("button", { class: "sc-li", style: "flex-direction:column;align-items:stretch", onclick: () => openMany(`${c.focus.name}: links tied to ${r.region}`, c.graph.flatMap(n => n.links.filter(l => (l.rg || []).includes(r.region))), "Geography") },
      el("div", { style: "display:flex;justify-content:space-between;font-size:12px" }, el("b", {}, (r.hotspot ? "⚠ " : "") + r.region), el("span", { class: "num faint", style: "font-size:11px" }, `${r.suppliers} sup · ${r.customers} cust · ${r.other} other`)),
      el("div", { class: "sc-bar" }, el("i", { style: `width:${r.suppliers / max * 100}%;background:var(--c3)` }), el("i", { style: `width:${r.customers / max * 100}%;background:var(--c1)` }), el("i", { style: `width:${r.other / max * 100}%;background:var(--up)` })))));
    return p;
  }

  function fragilityPanel(c) {
    const tickers = Object.keys(S.graph?.companies || {});
    const rows = tickers.map(t => ({ t, d: C.dependencyScore(t, c.all) })).filter(r => r.d.score > 0).sort((a, b) => b.d.score - a.d.score);
    const input = el("input", { placeholder: "Filter…", style: "height:26px;width:130px;border:1px solid var(--line2);border-radius:6px;padding:0 8px;font:inherit;font-size:12px" });
    const p = panel("Supply-chain fragility across coverage", input);
    const body = el("div", { class: "bd", style: "max-height:420px;overflow:auto" }); p.append(body);
    const draw = () => {
      body.innerHTML = "";
      const q = input.value.trim().toLowerCase();
      const t = el("table", { class: "sc-tbl" }, el("thead", {}, el("tr", {}, el("th", {}, "#"), el("th", {}, "Company"), el("th", { style: "width:34%" }, "Fragility (concentration · single-source · geography)"), el("th", { class: "r" }, "Top cust."), el("th", { class: "r" }, "Single-src"), el("th", { class: "r" }, "Hotspots"))));
      const tb = el("tbody");
      rows.forEach((r, i) => { if (q && !r.t.toLowerCase().includes(q) && !nm(r.t).toLowerCase().includes(q)) return; tb.append(el("tr", { class: "click" + (r.t === c.tk ? " on" : ""), onclick: () => { location.hash = `supply-${r.t}`; } },
        el("td", { class: "num faint" }, String(i + 1)), el("td", {}, miniLogo(r.t, 14), " ", el("b", {}, r.t), " ", el("span", { class: "faint" }, nm(r.t))),
        el("td", {}, el("div", { style: "display:flex;gap:6px;align-items:center" }, el("div", { class: "sc-bar", style: "flex:1;height:7px" }, el("i", { style: `width:${r.d.parts[0].points}%;background:var(--c1)` }), el("i", { style: `width:${r.d.parts[1].points}%;background:var(--down)` }), el("i", { style: `width:${r.d.parts[2].points}%;background:var(--warn)` })), el("b", { class: "num", style: "width:24px;text-align:right" + (r.d.score >= 50 ? ";color:var(--down)" : "") }, String(r.d.score)))),
        el("td", { class: "r num" }, r.d.topCustomerPct ? r.d.topCustomerPct + "%" : "–"), el("td", { class: "r num" }, r.d.singleSource || "–"), el("td", { class: "r" }, r.d.hotspots.slice(0, 2).map(h => el("span", { class: "sc-b bad", style: "margin-left:2px" }, h)))));
      });
      t.append(tb); body.append(t);
      body.append(el("div", { class: "faint", style: "font-size:10.5px;margin-top:6px" }, `Ranks the ${tickers.length} companies read so far (score > 0). Customer concentration (max 40) · single-source (30) · geopolitical (30), each from the company's own current filing.`));
    };
    input.addEventListener("input", draw); draw();
    return p;
  }

  window.FU_PAGES.supply = async arg => { await page_(arg); };

  /* ---------- company tab ---------- */
  window.FU_COTABS.push({
    id: "supply", name: "Supply chain", after: "segments",
    async render(body, tk) {
      body.append(el("div", { class: "faint", style: "padding:12px 0" }, "Loading supply-chain links…"));
      try { await Promise.all([loadGraph(), loadState()]); } catch { body.innerHTML = ""; body.append(el("div", { class: "empty" }, "Supply-chain data is unavailable right now.")); return; }
      const co = await loadCo(tk);
      body.innerHTML = "";
      const wrap = el("div", { class: "sc-page co-supply", style: "padding-top:12px" }); body.append(wrap);
      const c = build(tk, co);
      wrap.append(el("div", { style: "display:flex;gap:10px;align-items:center;flex-wrap:wrap" }, el("div", { class: "faint", style: "font-size:12px;flex:1;min-width:260px" }, co ? `Counterparties named in ${c.focus.name}'s latest 10-K and newer 10-Q, plus covered companies whose filings name ${c.focus.name}. Every quote opens the filing passage.` : `${c.focus.name}'s filings have not been read by the supply-chain job yet${c.graph.length ? "; links below come from other companies' filings that name it" : ""}.`), el("a", { class: "btn pri", href: `#supply-${tk}` }, "Open full supply chain map")));
      if (!c.graph.length) { wrap.append(el("div", { class: "empty" }, S.state?.tickers?.[tk] ? "No named counterparties in the filings read so far." : "Not read yet; the scheduled reader covers every company.")); return; }
      const k = kpis(c); k.lastChild.remove(); wrap.append(k);
      const grid = el("div", { style: "display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:12px" });
      for (const r of C.RELATIONS) {
        const ns = c.graph.filter(n => n.relation === r);
        if (!ns.length) continue;
        const p = panel(`${V.REL_LABEL[r]} (${ns.length})`, null), bd = el("div", { class: "bd", style: "display:flex;flex-direction:column;gap:6px;max-height:380px;overflow:auto" });
        p.append(bd);
        ns.slice(0, 12).forEach(n => { const l = n.links[0]; bd.append(el("div", { style: "display:flex;flex-direction:column;gap:2px;padding-bottom:6px;border-bottom:1px solid var(--line)" },
          el("div", { style: "display:flex;gap:6px;align-items:center;font-size:12.5px" }, n.ticker ? miniLogo(n.ticker, 16) : null, el("b", {}, n.name), n.ticker && mapped(n.ticker) ? el("a", { href: `#supply-${n.ticker}`, class: "faint", style: "font-size:11px" }, n.ticker) : el("span", { class: "faint", style: "font-size:11px" }, n.ticker || ""), el("span", { style: "margin-left:auto;font-size:11px" }, l.sh != null ? N(`${l.sh}%`, () => openLink(l)) : l.ge10 ? N("≥10%", () => openLink(l)) : null, C.confidence(l, c.all) === "corroborated" ? el("span", { class: "sc-b ok", style: "margin-left:4px" }, "✓✓") : null)),
          el("button", { class: "sc-q", onclick: () => openLink(l) }, "“" + l.q.slice(0, 180) + (l.q.length > 180 ? "…" : "") + "”"), el("div", { class: "faint", style: "font-size:10.5px" }, l.t === tk ? `${l.form} filed ${fdate(l.filed)}` : `Stated in ${nm(l.t)}'s ${l.form}, ${fdate(l.filed)}`))); });
        grid.append(p);
      }
      wrap.append(grid);
      const comp = C.crossInsights(tk, c.all, co?.history || [], S.regions?.countries || {}, c.nms).slice(0, 3);
      if (comp.length) { await loadRegions(); wrap.append(insightsPanel(c, S.regions?.countries || {})); }
    },
  });
})();
