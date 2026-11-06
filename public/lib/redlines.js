// Disclosure redlines tab on the company page: what changed in the wording of the latest 10-K against the prior
// 10-K, and of the latest 10-Q against the same quarter a year earlier, section by section (risk factors, MD&A,
// legal proceedings, controls, cybersecurity and every numbered note). Deterministic: paragraphs are aligned and
// diffed word by word from the SEC primary documents (etl/redlines_core.py); no AI is involved in finding changes.
// Data: redlines/{T}.json. Registers itself in window.FU_COTABS after the Filings & IR tab.
(function () {
  "use strict";
  const CACHE = {};
  const load = tk => CACHE[tk] || (CACHE[tk] = getJSON(`redlines/${tk}.json`).catch(e => { delete CACHE[tk]; throw e; }));
  const KIND = { added: "Added", removed: "Removed", modified: "Reworded", numbers: "Figures only" };
  const PREF = "fu:redlines";
  const pref = (k, d) => { try { const v = JSON.parse(localStorage.getItem(PREF) || "{}")[k]; return v === undefined ? d : v; } catch { return d; } };
  const setPref = (k, v) => { try { const o = JSON.parse(localStorage.getItem(PREF) || "{}"); o[k] = v; localStorage.setItem(PREF, JSON.stringify(o)); } catch { /* storage unavailable */ } };

  const oldText = c => c.old ?? (c.ops || []).filter(([op]) => op !== "+").map(([, t]) => t).join(" ");
  const newText = c => c.new ?? (c.ops || []).filter(([op]) => op !== "-").map(([, t]) => t).join(" ");

  function opsView(c, ctx = 40) {
    // Inline redline: deletions struck through, insertions underlined; long unchanged runs are shortened.
    const box = el("p", { class: "rl-text" });
    if (!c.ops) {
      box.append(el(c.kind === "removed" ? "del" : "ins", {}, c.kind === "removed" ? oldText(c) : newText(c)));
      return box;
    }
    c.ops.forEach(([op, t], i) => {
      if (op === "=") {
        const w = t.split(" ");
        const first = i === 0, last = i === c.ops.length - 1;
        if (w.length > ctx * 2 + 4 && !(first && last)) {
          const head = first ? "" : w.slice(0, ctx).join(" ");
          const tail = last ? "" : w.slice(-ctx).join(" ");
          const hidden = el("span", { class: "rl-gap", tabindex: "0", role: "button", title: "Show unchanged text" }, ` … ${w.length - (first ? 0 : ctx) - (last ? 0 : ctx)} unchanged words … `);
          hidden.onclick = hidden.onkeydown = e => { if (e.type === "click" || e.key === "Enter") hidden.replaceWith(document.createTextNode(" " + w.slice(first ? 0 : ctx, last ? undefined : -ctx).join(" ") + " ")); };
          box.append(head ? head + " " : "", hidden, tail ? " " + tail + " " : "");
        } else box.append(t + " ");
      } else box.append(el(op === "-" ? "del" : "ins", {}, t), " ");
    });
    return box;
  }

  function sourceDrawer(tk, comp, sec, c) {
    // Old and new paragraph verbatim, side by side, in the shared right-hand drawer.
    hideTip?.();
    const dh = $("#dh"), db = $("#db");
    dh.innerHTML = ""; db.innerHTML = "";
    dh.append(
      el("div", { class: "row" }, el("span", { class: "eyebrow" }, `Disclosure change · ${comp.form} ${comp.kind === "annual" ? "vs prior 10-K" : "vs same quarter last year"}`),
        el("div", { class: "x", style: "display:flex;gap:6px" },
          el("a", { class: "btn", href: comp.new.url, target: "_blank", rel: "noopener" }, `${comp.form} ${comp.new.filed}`, svgUse("i-ext")),
          el("a", { class: "btn", href: comp.old.url, target: "_blank", rel: "noopener" }, `${comp.old.form} ${comp.old.filed}`, svgUse("i-ext")),
          el("button", { class: "iconbtn", onclick: closeSource, "aria-label": "Close source (Esc)" }, svgUse("i-x")))),
      el("div", { class: "row" }, el("h2", {}, sec.title)),
      el("div", { class: "row" }, el("span", { class: `chip rl-k rl-${c.kind}` }, KIND[c.kind]),
        ...(c.signals || []).map(s => el("span", { class: "chip" }, s))));
    db.append(el("div", { class: "note" }, "Paragraph text as filed. Changes are found by aligning paragraphs and words between the two filings; nothing here is AI-generated."));
    db.append(el("h4", { class: "rl-h" }, "Redline"), opsView(c, 10000));
    if (c.kind !== "added") db.append(el("h4", { class: "rl-h" }, `Prior · ${comp.old.form} filed ${fdate(comp.old.filed)}`), el("p", { class: "rl-text rl-plain" }, oldText(c)));
    if (c.kind !== "removed") db.append(el("h4", { class: "rl-h" }, `Latest · ${comp.form} filed ${fdate(comp.new.filed)}`), el("p", { class: "rl-text rl-plain" }, el("mark", { class: "fu-hit" }, newText(c))));
    if (c.clipped) db.append(el("div", { class: "note" }, "Long paragraph shortened here; the full text is in the filing."));
    drawer.classList.add("open"); drawer.setAttribute("aria-hidden", "false");
    if (innerWidth < 860) scrim.hidden = false;
    db.scrollTop = 0;
  }

  function counts(o) {
    const lang = o.added + o.removed + o.modified;
    return el("span", { class: "rl-counts mono" },
      el("span", { class: "rl-c add", title: "Paragraphs added" }, "+" + o.added),
      el("span", { class: "rl-c del", title: "Paragraphs removed" }, "−" + o.removed),
      el("span", { class: "rl-c mod", title: "Paragraphs reworded" }, "~" + o.modified),
      o.numbers ? el("span", { class: "rl-c num", title: "Same wording, figures changed" }, "#" + o.numbers) : null,
      lang ? null : el("span", { class: "faint" }, " no wording changes"));
  }

  function render(body, tk) {
    injectCSS();
    const host = el("div", { class: "rl-tab" }, el("div", { class: "muted", style: "padding:16px 4px" }, "Loading disclosure redlines…"));
    body.append(host);
    load(tk).then(d => { host.innerHTML = ""; draw(host, tk, d); })
      .catch(() => { host.innerHTML = ""; host.append(panel("Disclosure redlines", null, el("div", { class: "bd muted" }, "Redlines for this company have not been computed yet. They are built from each new 10-K and 10-Q."))); });
  }

  function draw(host, tk, d) {
    if (!d.comparisons?.length) {
      host.append(panel("Disclosure redlines", null, el("div", { class: "bd muted" }, "No comparable pair of filings yet (two 10-Ks, or a 10-Q and the same quarter a year earlier).")));
      return;
    }
    let ci = Math.min(pref("comp", 0), d.comparisons.length - 1);
    let showNumbers = pref("numbers", false);
    let filter = "";
    let secId = null;
    const head = el("div", { class: "rl-head" });
    const main = el("div", { class: "rl-main" });
    host.append(head, main);

    function paint() {
      const comp = d.comparisons[ci];
      head.innerHTML = "";
      const seg = el("div", { class: "seg", role: "tablist" }, ...d.comparisons.map((c, i) =>
        el("button", { class: i === ci ? "on" : "", role: "tab", "aria-selected": String(i === ci), onclick: () => { ci = i; setPref("comp", i); secId = null; paint(); } },
          c.kind === "annual" ? `10-K ${c.new.filed.slice(0, 4)} vs ${c.old.filed.slice(0, 4)}` : `10-Q ${c.new.report?.slice(0, 7) || c.new.filed} vs ${c.old.report?.slice(0, 7) || c.old.filed}`)));
      const t = comp.totals;
      head.append(
        el("div", { class: "rl-kpis" },
          el("div", { class: "rl-kpi" }, el("div", { class: "k" }, "Wording changes"), el("div", { class: "v mono" }, String(t.added + t.removed + t.modified)),
            el("div", { class: "s" }, `${t.added} added · ${t.removed} removed · ${t.modified} reworded`)),
          el("div", { class: "rl-kpi" }, el("div", { class: "k" }, "Figure-only updates"), el("div", { class: "v mono" }, String(t.numbers)), el("div", { class: "s" }, "same wording, new numbers")),
          el("div", { class: "rl-kpi" }, el("div", { class: "k" }, "Compared"), el("div", { class: "v" }, `${comp.form} ${fdate(comp.new.filed)}`),
            el("div", { class: "s" }, `against ${comp.old.form} ${fdate(comp.old.filed)}`)),
          el("div", { class: "rl-kpi" }, el("div", { class: "k" }, "Flags in changed text"), el("div", { class: "rl-flags" },
            ...[...new Set(comp.sections.flatMap(s => s.signals || []))].slice(0, 8).map(s => el("span", { class: "chip" }, s))))),
        el("div", { class: "rl-tools" }, seg,
          el("label", { class: "rl-chk" }, el("input", { type: "checkbox", checked: showNumbers || null, onchange: e => { showNumbers = e.target.checked; setPref("numbers", showNumbers); paint(); } }), " Include figure-only changes"),
          el("input", { class: "inp rl-q", type: "search", placeholder: "Filter changed text…", value: filter, oninput: e => { filter = e.target.value.toLowerCase(); paintList(); } }),
          el("a", { class: "btn", href: comp.new.url, target: "_blank", rel: "noopener" }, "Latest filing", svgUse("i-ext"))));
      paintList();
    }

    function paintList() {
      const comp = d.comparisons[ci];
      main.innerHTML = "";
      const secs = comp.sections.filter(s => s.status !== "compared" || s.counts.added + s.counts.removed + s.counts.modified + (showNumbers ? s.counts.numbers : 0) > 0);
      if (!secId || !secs.some(s => s.id === secId)) secId = null;
      const nav = el("nav", { class: "rl-nav", "aria-label": "Sections" },
        el("button", { class: "rl-sec" + (secId ? "" : " on"), onclick: () => { secId = null; paintList(); } }, el("span", {}, "Most significant"), el("span", { class: "faint mono" }, "")),
        ...secs.map(s => el("button", { class: "rl-sec" + (s.id === secId ? " on" : ""), onclick: () => { secId = s.id; paintList(); } },
          el("span", {}, s.title.replace(/^Item (\S+)\. /, "$1 · ").replace(/^Note (\d+)\. /, "Note $1 · ")),
          s.status === "compared" ? counts(s.counts) : el("span", { class: "chip" }, s.status))));
      const list = el("div", { class: "rl-list" });
      let items = [];
      comp.sections.forEach(s => s.changes.forEach(c => items.push([s, c])));
      if (secId) items = items.filter(([s]) => s.id === secId);
      if (!showNumbers) items = items.filter(([, c]) => c.kind !== "numbers");
      if (filter) items = items.filter(([s, c]) => (newText(c) + " " + oldText(c) + " " + s.title).toLowerCase().includes(filter));
      if (!secId) items.sort((a, b) => b[1].score - a[1].score);
      const shown = items.slice(0, secId ? 400 : 60);
      if (secId) {
        const s = comp.sections.find(x => x.id === secId);
        list.append(el("div", { class: "rl-sechd" }, el("h3", {}, s.title), s.status === "compared" ? counts(s.counts) : el("span", { class: "chip" }, s.status),
          el("span", { class: "faint" }, ` ${s.unchanged} paragraphs unchanged${s.truncated ? ` · ${s.truncated} lower-ranked changes not stored` : ""}`)));
        if (s.status !== "compared") list.append(el("div", { class: "muted rl-empty" }, s.status === "new note" ? "This note is new in the latest filing." : s.status === "note removed" ? "This note is not in the latest filing." : `Section found ${s.status}.`));
      }
      if (!shown.length && (!secId || comp.sections.find(x => x.id === secId).status === "compared")) list.append(el("div", { class: "muted rl-empty" }, filter ? "No changes match the filter." : "No wording changes in this selection."));
      shown.forEach(([s, c]) => {
        const card = el("article", { class: `rl-card rl-${c.kind}`, tabindex: "0" },
          el("div", { class: "rl-meta" }, el("span", { class: `chip rl-k rl-${c.kind}` }, KIND[c.kind]),
            secId ? null : el("span", { class: "rl-where" }, s.title),
            ...(c.signals || []).slice(0, 4).map(x => el("span", { class: "chip" }, x)),
            el("button", { class: "btn rl-open", onclick: e => { e.stopPropagation(); sourceDrawer(tk, comp, s, c); } }, "Compare text")),
          opsView(c));
        const more = el("button", { class: "rl-more", onclick: e => { e.stopPropagation(); card.classList.toggle("open"); more.textContent = card.classList.contains("open") ? "Show less" : "Show full paragraph"; } }, "Show full paragraph");
        card.append(more);
        requestAnimationFrame(() => { const t = card.querySelector(".rl-text"); if (t && t.scrollHeight <= t.clientHeight + 2) more.remove(); });
        card.onkeydown = e => { if (e.key === "Enter" && e.target === card) sourceDrawer(tk, comp, s, c); };
        list.append(card);
      });
      if (items.length > shown.length) list.append(el("div", { class: "faint rl-empty" }, `${items.length - shown.length} more; pick a section on the left to see all of its changes.`));
      main.append(nav, list);
    }
    paint();
    host.append(el("div", { class: "src" }, `Source: SEC EDGAR primary documents (${d.comparisons.map(c => `${c.form} ${c.new.accn}`).join(", ")}). `,
      "Changes are aligned by paragraph and word; figure-only updates are separated from wording changes. Flags mark analyst terms (litigation, covenant, impairment and similar) in the changed text. ",
      `Computed ${relTime(d.generated)}.`));
  }

  let cssDone = false;
  function injectCSS() {
    if (cssDone || document.querySelector('link[href*="redlines.css"]')) { cssDone = true; return; }
    cssDone = true;
    document.head.append(el("link", { rel: "stylesheet", href: "/redlines.css" }));
  }

  window.FU_REDLINES = { load, opsView };
  window.FU_COTABS = window.FU_COTABS || [];
  window.FU_COTABS.push({ id: "redlines", name: "Redlines", after: "filings", render });
})();
