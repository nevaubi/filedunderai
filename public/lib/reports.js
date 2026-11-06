// Reports tab on the company page: pre-generated earnings preview, earnings recap and company primer.
// Documents are written in the background by the reports cron (etl/reports.py) from cited evidence records; the page
// only opens the latest version. Every paragraph cites numbered sources that open the stored record in the right-hand
// drawer; items whose figures were not found in their sources are marked. Downloads: Word (.docx) and Markdown.
// "Customize" hands the document to the assistant panel instead of regenerating it from scratch.
// Data: reports/index.json, reports/{T}/{kind}.json|.docx|.md. Registers in window.FU_COTABS after Overview.
(function () {
  "use strict";
  const KINDS = [["preview", "Earnings preview"], ["recap", "Earnings recap"], ["primer", "Company primer"]];
  const WHEN = { preview: "Written in the days before each scheduled report date.", recap: "Written after each earnings release (8-K Item 2.02).",
    primer: "Refreshed on a rotation, largest companies first." };
  let INDEX = null;
  const loadIndex = () => INDEX || (INDEX = getJSON("reports/index.json").then(d => d.companies || {}).catch(() => { INDEX = null; return {}; }));
  const CACHE = {};
  const loadDoc = (tk, k) => CACHE[tk + k] || (CACHE[tk + k] = getJSON(`reports/${tk}/${k}.json`).catch(e => { delete CACHE[tk + k]; throw e; }));
  const PREF = "fu:reports-kind";
  const getPref = () => { try { return localStorage.getItem(PREF); } catch { return null; } };
  const setPref = v => { try { localStorage.setItem(PREF, v); } catch { /* storage unavailable */ } };

  const usd = (v, unit) => v == null ? "–" : /share/.test(unit || "") ? "$" + v.toFixed(2) : fmtMoney(v);
  const pct = v => v == null ? "–" : fsgn(v, 1);

  function openEvidence(doc, id, n) {
    const e = doc.evidence[id];
    if (!e) return;
    const dh = $("#dh"), db = $("#db");
    dh.innerHTML = ""; db.innerHTML = "";
    dh.append(
      el("div", { class: "row" }, el("span", { class: "eyebrow" }, `Source [${n}] · ${e.kind}`),
        el("div", { class: "x", style: "display:flex;gap:6px" },
          e.source?.url ? el("a", { class: "btn", href: e.source.url, target: "_blank", rel: "noopener" }, "Open source", svgUse("i-ext")) : null,
          el("button", { class: "iconbtn", onclick: closeSource, "aria-label": "Close source (Esc)" }, svgUse("i-x")))),
      el("div", { class: "row" }, el("h2", {}, e.title)),
      el("div", { class: "row" }, el("span", { class: "muted", style: "font-size:12.5px" }, [e.source?.name, e.as_of ? "as of " + String(e.as_of).slice(0, 10) : null].filter(Boolean).join(" · "))));
    db.append(el("div", { class: "note" }, "This is the stored record the report cites. Report text is AI-written from these records and checked against them."));
    const d = e.data;
    if (d && d.quarterly && d.annual) {
      const t = el("table", { class: "t" }, el("thead", {}, el("tr", {}, el("th", {}, "Period end"), el("th", {}, "Period"), el("th", { class: "num" }, d.metric))));
      const tb = el("tbody");
      [...d.annual.map(r => [r[0], "Fiscal year", r[1]]), ...d.quarterly.map(r => [r[0], "Quarter", r[1]])].sort((a, b) => b[0].localeCompare(a[0]))
        .forEach(([end, p, v]) => tb.append(el("tr", {}, el("td", { class: "mono" }, end), el("td", {}, p), el("td", { class: "num mono" }, usd(v, d.unit)))));
      t.append(tb);
      db.append(t);
      if (d.derived) db.append(el("div", { class: "src" }, d.derived_note || "", " ", Object.entries(d.derived).map(([k, v]) => `${k.replace(/_/g, " ")}: ${/margin|change/.test(k) ? pct(v) : usd(v, d.unit)}`).join(" · ")));
    } else if (d && d.text) {
      db.append(el("div", { class: "rp-quote" }, d.text));
    } else {
      db.append(el("pre", { class: "rp-json" }, JSON.stringify(d, null, 2)));
    }
    drawer.classList.add("open"); drawer.setAttribute("aria-hidden", "false");
    if (innerWidth < 860) scrim.hidden = false;
    db.scrollTop = 0;
  }

  function citedText(doc, item, order) {
    const p = el("span", {}, item.text);
    item.cites.forEach(c => {
      const n = order[c];
      if (!n) return;
      p.append(el("button", { class: "rp-cite", title: doc.evidence[c]?.title || c, onclick: () => openEvidence(doc, c, n) }, String(n)));
    });
    if (!item.verified) p.append(el("span", { class: "rp-unv", title: `Figure not found in the cited records: ${(item.unsupported_numbers || []).join(", ")}` }, "unverified figure"));
    return p;
  }

  function customize(tk, doc) {
    const prompt = `Using the stored ${doc.kind_label.toLowerCase()} for ${doc.name} (${tk}) dated ${doc.generated_at.slice(0, 10)} as the base, ` +
      "revise it as follows (keep every figure cited to filings or stored records):\n- ";
    window.dispatchEvent(new CustomEvent("fu:research-request", { detail: prompt }));
  }

  function docView(tk, doc) {
    const order = {};
    Object.keys(doc.evidence).forEach((k, i) => { order[k] = i + 1; });
    const base = `${PUB}reports/${tk}/${doc.kind}`;
    const q = doc.quality || {};
    const head = el("div", { class: "rp-head" },
      el("div", {}, el("div", { class: "eyebrow" }, `${doc.kind_label} · ${doc.ticker}`), el("h2", { class: "rp-title" }, doc.title),
        doc.subtitle ? el("div", { class: "rp-sub" }, doc.subtitle) : null,
        el("div", { class: "rp-meta" }, el("span", { class: "chip ai" }, "AI-generated"), ` ${fdate(doc.generated_at.slice(0, 10))} · ${doc.model} · ${q.cited_records || 0} sources cited`,
          q.unverified ? el("span", { class: "rp-unv" }, `${q.unverified} unverified figure${q.unverified > 1 ? "s" : ""}`) : el("span", { class: "chip" }, "all figures matched to sources"))),
      el("div", { class: "rp-actions" },
        el("a", { class: "btn", href: base + ".docx", download: `${tk}-${doc.kind}-${doc.generated_at.slice(0, 10)}.docx` }, "Word"),
        el("a", { class: "btn", href: base + ".md", download: `${tk}-${doc.kind}-${doc.generated_at.slice(0, 10)}.md` }, "Markdown"),
        el("button", { class: "btn", onclick: () => window.print() }, "Print / PDF"),
        el("button", { class: "btn acc", onclick: () => customize(tk, doc) }, "Customize")));
    const body = el("article", { class: "rp-doc" });
    if (doc.summary?.length) body.append(el("h3", {}, "Key points"), el("ul", { class: "rp-kp" }, ...doc.summary.map(p => el("li", {}, citedText(doc, p, order)))));
    if (doc.metrics?.length) {
      const t = el("table", { class: "t rp-metrics" }, el("thead", {}, el("tr", {}, ...["Metric", "Latest quarter", "YoY", "Latest year", "YoY", "TTM", ""].map((h, i) => el("th", { class: i ? "num" : "" }, h)))));
      const tb = el("tbody");
      doc.metrics.forEach(r => tb.append(el("tr", {},
        el("td", {}, r.metric), el("td", { class: "num mono" }, usd(r.quarter, r.unit), el("span", { class: "faint" }, r.quarter_end ? " " + r.quarter_end.slice(0, 7) : "")),
        el("td", { class: "num mono" }, pct(r.quarter_yoy)), el("td", { class: "num mono" }, usd(r.year, r.unit), el("span", { class: "faint" }, r.year_end ? " FY" + r.year_end.slice(0, 4) : "")),
        el("td", { class: "num mono" }, pct(r.year_yoy)), el("td", { class: "num mono" }, usd(r.ttm, r.unit)),
        el("td", {}, el("button", { class: "rp-cite", onclick: () => openEvidence(doc, r.cite, order[r.cite]) }, String(order[r.cite] || ""))))));
      t.append(tb);
      body.append(el("h3", {}, "Key metrics"), t, el("div", { class: "src" }, "Computed from SEC filings by Filed Under, not by the model."));
    }
    doc.sections.forEach(s => {
      body.append(el("h3", {}, s.heading));
      s.paragraphs.forEach(p => body.append(el("p", {}, citedText(doc, p, order))));
      if (s.bullets?.length) body.append(el("ul", {}, ...s.bullets.map(b => el("li", {}, citedText(doc, b, order)))));
    });
    body.append(el("h3", {}, "Sources"), el("ol", { class: "rp-src" }, ...Object.entries(doc.evidence).map(([k, e]) =>
      el("li", {}, el("button", { class: "linkish", onclick: () => openEvidence(doc, k, order[k]) }, e.title), ` · ${e.kind}${e.source?.name ? " · " + e.source.name : ""}${e.as_of ? " · " + String(e.as_of).slice(0, 10) : ""}`))));
    body.append(el("div", { class: "src" }, "Generated by Filed Under from stored SEC filings and cited third-party data. Not investment advice."));
    return el("div", { class: "rp-view" }, head, body);
  }

  function render(host, tk) {
    injectCSS();
    const wrap = el("div", { class: "rp-tab" }, el("div", { class: "muted", style: "padding:16px 4px" }, "Loading reports…"));
    host.append(wrap);
    loadIndex().then(idx => {
      wrap.innerHTML = "";
      const mine = idx[tk] || {};
      const avail = KINDS.filter(([k]) => mine[k]);
      let cur = avail.find(([k]) => k === getPref())?.[0] || avail[0]?.[0] || null;
      const cards = el("div", { class: "rp-cards" });
      const view = el("div", {});
      function paint() {
        cards.innerHTML = "";
        KINDS.forEach(([k, label]) => {
          const m = mine[k];
          cards.append(el("button", { class: "rp-card" + (k === cur ? " on" : "") + (m ? "" : " off"), disabled: m ? null : true,
            onclick: () => { cur = k; setPref(k); paint(); } },
            el("div", { class: "rp-k" }, label), el("div", { class: "rp-t" }, m ? m.title : "Not generated yet"),
            el("div", { class: "faint" }, m ? `Updated ${relTime(m.generated_at)}` : WHEN[k])));
        });
        view.innerHTML = "";
        if (!cur) { view.append(panel("Reports", null, el("div", { class: "bd muted" }, "No report has been generated for this company yet. Previews are written before scheduled earnings, recaps after each release, and primers on a rotation."))); return; }
        view.append(el("div", { class: "muted", style: "padding:12px 4px" }, "Loading document…"));
        loadDoc(tk, cur).then(doc => { view.innerHTML = ""; view.append(docView(tk, doc)); })
          .catch(() => { view.innerHTML = ""; view.append(el("div", { class: "muted" }, "This document could not be loaded.")); });
      }
      wrap.append(cards, view);
      paint();
    });
  }

  let cssDone = false;
  function injectCSS() {
    if (cssDone || document.querySelector('link[href*="reports.css"]')) { cssDone = true; return; }
    cssDone = true;
    document.head.append(el("link", { rel: "stylesheet", href: "/reports.css" }));
  }

  window.FU_REPORTS = { loadIndex, loadDoc, docView };
  window.FU_COTABS = window.FU_COTABS || [];
  window.FU_COTABS.push({ id: "reports", name: "Reports", after: "overview", render });
})();
