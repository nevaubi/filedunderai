// Source drawer for supply-chain evidence. Renders into the shared right-hand drawer (#drawer/#dh/#db): one or more
// cross-referenced sources for a claim, each opened at the cited sentence with the yellow highlight (mark.fu-hit).
// Filing sources read the stored text copy of the cited passages (supply/docs/...), and can switch to the full
// filed document (existing /api/filing viewer). Web and Federal Register sources show the stored text with the quote.
(function () {
  "use strict";
  const C = window.FU_SUPPLY_CORE;
  const DOCS = new Map();
  const gzJSON = async path => {
    if (DOCS.has(path)) return DOCS.get(path);
    const p = fetch(PUB + path).then(r => { if (!r.ok) throw new Error(`Text copy unavailable (${r.status})`); return inflate(r); }).then(t => JSON.parse(t));
    DOCS.set(path, p); p.catch(() => DOCS.delete(path));
    if (DOCS.size > 12) DOCS.delete(DOCS.keys().next().value);
    return p;
  };

  /** Escaped HTML of `text` with each verbatim quote wrapped in <mark>; the first quote gets fu-hit, others fu-alt. */
  function markedHTML(text, quotes) {
    const ranges = [];
    quotes.forEach((q, i) => { const r = q && C.findQuote(text, q); if (r) ranges.push([r[0], r[1], i === 0 ? "fu-hit" : "fu-alt"]); });
    ranges.sort((a, b) => a[0] - b[0]);
    let html = "", at = 0;
    for (const [s, e, cls] of ranges) { if (s < at) continue; html += esc(text.slice(at, s)) + `<mark class="${cls}">` + esc(text.slice(s, e)) + "</mark>"; at = e; }
    return { html: html + esc(text.slice(at)), found: ranges.length };
  }

  /** Wraps a verbatim quote inside a live document (filing iframe) in span.fu-hit, across element boundaries. */
  function highlightInDoc(doc, quote) {
    const q = C.normText(quote);
    if (!q || !doc?.body) return null;
    const walker = doc.createTreeWalker(doc.body, NodeFilter.SHOW_TEXT);
    const nodes = []; let flat = "", map = [], prevSpace = true;
    while (walker.nextNode()) {
      const n = walker.currentNode, v = n.nodeValue;
      for (let i = 0; i < v.length; i++) {
        let c = v[i];
        if (/[‘’‛′]/.test(c)) c = "'"; else if (/[“”‟]/.test(c)) c = '"'; else if (/[‐-―−]/.test(c)) c = "-";
        if (/\s| /.test(c)) { if (prevSpace) continue; c = " "; prevSpace = true; } else prevSpace = false;
        flat += c.toLowerCase(); map.push([nodes.length, i]);
      }
      nodes.push(n);
    }
    const k = flat.indexOf(q);
    if (k < 0) return null;
    const [n0, o0] = map[k], [n1, o1] = map[k + q.length - 1];
    let first = null;
    for (let ni = n1; ni >= n0; ni--) {
      const node = nodes[ni], s = ni === n0 ? o0 : 0, e = ni === n1 ? o1 + 1 : node.nodeValue.length;
      if (e <= s) continue;
      const range = doc.createRange(); range.setStart(node, s); range.setEnd(node, e);
      const span = doc.createElement("span"); span.className = "fu-hit";
      try { range.surroundContents(span); first = span; } catch { /* partially selected element: skip that piece */ }
    }
    return first;
  }

  const centerIn = (box, node) => { if (!node) return; requestAnimationFrame(() => { const b = box.getBoundingClientRect(), r = node.getBoundingClientRect(); box.scrollTop += r.top - b.top - box.clientHeight / 2 + r.height / 2; }); };
  const srcLabel = s => s.kind === "filing" ? `${s.label || (SHORT[s.t] || s.t)} · ${s.form} filed ${fdate(s.filed)}` : s.kind === "fr" ? `Federal Register · ${s.agency || ""} · ${s.date ? fdate(s.date) : ""}` : s.kind === "web" ? `${s.host || domainOf(s.url)}${s.date ? " · " + fdate(s.date) : ""}` : s.label || "Calculation";

  async function renderFiling(view, s, status) {
    view.innerHTML = "";
    if (!s.path) { view.append(el("div", { class: "sc-miss" }, "No stored text copy for this document.")); return; }
    const d = await gzJSON(s.path);
    const box = el("div", { class: "sc-doc" });
    let hit = null, found = 0;
    const quotes = [s.quote, s.row, ...(s.also || [])].filter(Boolean);
    (d.paras || []).forEach(p => {
      if (p == null) { box.append(el("div", { class: "sc-gap" }, "· · · passage omitted · · ·")); return; }
      const m = markedHTML(p, quotes);
      found += m.found;
      box.append(el("p", { html: m.html }));
    });
    if (d.partial) box.prepend(el("div", { class: "sc-gap" }, "Cited passages with surrounding paragraphs. Open the full filing for the whole document."));
    view.append(box);
    hit = box.querySelector("mark.fu-hit") || box.querySelector("mark.fu-alt");
    status.textContent = hit ? "Quote highlighted" : "Quote not located in the text copy";
    centerIn(box, hit);
    return found;
  }

  async function renderFullFiling(view, s, status) {
    view.innerHTML = ""; status.textContent = "Loading full filing…";
    const html = await loadFilingDoc(s.accn);
    const iframe = el("iframe", { sandbox: "allow-same-origin allow-popups allow-popups-to-escape-sandbox", referrerpolicy: "no-referrer", title: "Filing" });
    iframe.srcdoc = html + VIEWER_CSS;
    view.append(iframe);
    iframe.addEventListener("load", () => {
      const doc = iframe.contentDocument;
      const hit = doc && (highlightInDoc(doc, s.quote) || (s.row && highlightInDoc(doc, s.row)));
      if (hit) { hit.scrollIntoView({ block: "center" }); status.textContent = "Quote highlighted in the filed document"; }
      else status.textContent = "Quote not located in this rendering; the text copy shows it";
    }, { once: true });
  }

  function renderWeb(view, s, status) {
    view.innerHTML = "";
    const text = s.text || s.abstract || "";
    const box = el("div", { class: "sc-doc" });
    if (s.title) box.append(el("p", { class: "sc-title", html: markedHTML(s.title, [s.qsrc === "title" ? s.quote : null]).html }));
    const m = markedHTML(text, [s.quote]);
    box.append(el("p", { html: m.html || esc(s.quote || "") }));
    if (!m.found && s.quote) box.append(el("p", {}, el("mark", { class: "fu-hit" }, s.quote)));
    view.append(box);
    status.textContent = s.kind === "fr" ? "Abstract from the Federal Register API" : `Excerpt returned by ${s.provider || "web search"}`;
    centerIn(box, box.querySelector("mark"));
  }

  function renderCalc(view, s) {
    view.innerHTML = "";
    view.append(el("div", { class: "sc-doc" }, s.body));
  }

  /** spec: { eyebrow, title, value, sub, sources: [{kind:"filing"|"web"|"fr"|"calc", ...}], cross, actions } */
  function openEvidence(spec) {
    hideTip();
    const dh = $("#dh"), db = $("#db");
    const wasOpen = drawer.classList.contains("open");
    if (!wasOpen) { const a = document.activeElement; DR.trigger?.classList.remove("src-active"); DR.trigger = a && a !== document.body && !drawer.contains(a) ? a : null; DR.trigger?.classList.add("src-active"); }
    DR.cell = { supply: spec.title };
    dh.innerHTML = ""; db.innerHTML = "";
    const sources = (spec.sources || []).filter(Boolean);
    const first = sources[0];
    dh.append(
      el("div", { class: "row" }, el("span", { class: "eyebrow" }, spec.eyebrow || "Supply-chain evidence"),
        el("div", { class: "x", style: "display:flex;gap:6px" },
          first?.url ? el("a", { class: "btn", href: first.kind === "web" && first.quote ? C_quoteLink(first.url, first.quote) : first.url, target: "_blank", rel: "noopener" }, first.kind === "filing" ? "EDGAR" : "Original", svgUse("i-ext")) : null,
          el("button", { class: "iconbtn", onclick: closeSource, "aria-label": "Close source (Esc)" }, svgUse("i-x")))),
      el("div", { class: "row" }, el("h2", {}, spec.title)),
      spec.value || spec.sub ? el("div", { class: "row" }, spec.value ? el("span", { class: "chip acc mono", style: "font-size:13px;height:24px" }, spec.value) : null, spec.sub ? el("span", { class: "muted", style: "font-size:12px" }, spec.sub) : null) : null);
    if (spec.cross) db.append(el("div", { class: "note sc-cross" }, spec.cross));
    if (spec.ai) db.append(el("div", { class: "note sc-ainote" }, spec.ai));
    const list = el("div", { class: "sc-srcs" });
    const status = el("span", { class: "faint", style: "font-size:12px" }, "");
    const view = el("div", { class: "viewer docv sc-view" });
    const modeBar = el("div", { class: "seg" });
    const bar = el("div", { class: "viewer-bar" }, el("span", { class: "badge-src" }, "Source"), modeBar, el("span", { style: "flex:1" }), status);
    let cur = null;
    const show = async (s, mode = "text") => {
      cur = s;
      list.querySelectorAll(".sc-src").forEach(x => x.classList.toggle("on", x._s === s));
      modeBar.innerHTML = "";
      if (s.kind === "filing") {
        modeBar.append(el("button", { class: mode === "text" ? "on" : "", onclick: () => show(s, "text") }, "Cited passage"));
        if (s.full !== false) modeBar.append(el("button", { class: mode === "full" ? "on" : "", onclick: () => show(s, "full") }, "Full filing"));
      }
      status.textContent = "Loading…";
      try {
        if (s.kind === "filing") await (mode === "full" ? renderFullFiling(view, s, status) : renderFiling(view, s, status));
        else if (s.kind === "calc") { renderCalc(view, s); status.textContent = s.status || "Computed from stored data"; }
        else renderWeb(view, s, status);
      } catch (e) {
        if (s.kind === "filing" && mode === "full") { status.textContent = String(e.message || e); show(s, "text"); return; }
        view.innerHTML = ""; view.append(el("div", { class: "viewer-load" }, el("div", {}, "This source could not be displayed here."), el("div", { class: "faint", style: "font-size:12px" }, String(e.message || e)), s.url ? el("a", { class: "btn", href: s.url, target: "_blank", rel: "noopener" }, "Open the original") : null));
        status.textContent = "";
      }
    };
    if (sources.length > 1) {
      sources.forEach((s, i) => {
        const card = el("button", { class: "sc-src", onclick: () => show(s) },
          el("span", { class: "n mono" }, String(i + 1)),
          el("span", { class: "b" }, el("span", { class: "l" }, srcLabel(s), s.role ? el("span", { class: "sc-role" }, s.role) : null), s.quote ? el("span", { class: "q" }, "“" + s.quote + "”") : null));
        card._s = s; list.append(card);
      });
      db.append(el("div", { class: "eyebrow" }, `${sources.length} sources, cross-referenced`), list);
    } else if (first && first.quote) db.append(el("blockquote", { class: "sc-bq" }, "“" + first.quote + "”"));
    if (spec.actions) db.append(el("div", { class: "sc-acts" }, ...spec.actions));
    db.append(bar, view);
    if (spec.footer) db.append(el("div", { class: "note" }, spec.footer));
    drawer.classList.add("open"); drawer.setAttribute("aria-hidden", "false");
    if (innerWidth < 860) scrim.hidden = false;
    db.scrollTop = 0;
    if (first) show(first);
    if (!wasOpen) { const h = dh.querySelector("h2"); h?.setAttribute("tabindex", "-1"); h?.focus({ preventScroll: true }); }
  }

  /** Browser text-fragment link so a web source opens scrolled to and highlighting the quote. */
  function C_quoteLink(url, quote) {
    const w = String(quote).replace(/[“”]/g, '"').replace(/[‘’]/g, "'").trim().split(/\s+/).filter(Boolean);
    if (!w.length) return url;
    const enc = x => encodeURIComponent(x.join(" ")).replace(/-/g, "%2D").replace(/,/g, "%2C").replace(/&/g, "%26");
    return `${url.split("#")[0]}#:~:text=${w.length <= 12 ? enc(w) : `${enc(w.slice(0, 6))},${enc(w.slice(-6))}`}`;
  }

  window.FU_SUPPLY_DRAWER = { openEvidence, markedHTML, highlightInDoc, quoteLink: C_quoteLink, gzJSON };
})();
