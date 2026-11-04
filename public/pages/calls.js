// Earnings calls. Registers window.FU_PAGES.calls(arg) for the cross-company page (#calls, #calls-<theme id>,
// #calls-executives, #calls-analysts, #calls-coverage) and window.FU_PAGES.calls.tab(body, tk) for the company "Calls" tab.
// Data: calls/{T}.json (calls with themes, guidance, exchanges, speakers), calls/{T}/{call_id}.json (passage text),
// calls/themes.json, calls/themes/{theme}.json, calls/people.json, calls/coverage.json, built by etl/calls.py.
// Every quote carries character offsets (s, e) into the stored document text, and the source panel opens that text
// scrolled to the passage with the quote highlighted.
window.FU_PAGES = window.FU_PAGES || {};
(() => {
  "use strict";
  const S = { co: {}, turns: {}, themes: null, theme: {}, people: null, cov: null, pages: {}, sel: {} };
  const miss = e => /\b(400|404)\b/.test(String(e && e.message));
  const loadCo = tk => S.co[tk] ? Promise.resolve(S.co[tk]) : getJSON(`calls/${tk}.json`).then(j => (S.co[tk] = j)).catch(e => { if (miss(e)) return (S.co[tk] = { calls: [], docs: {} }); throw e; });
  const loadTurns = (tk, cid) => S.turns[cid] ? Promise.resolve(S.turns[cid]) : getJSON(`calls/${tk}/${cid}.json`).then(j => (S.turns[cid] = j));
  const loadThemes = () => S.themes ? Promise.resolve(S.themes) : getJSON("calls/themes.json").then(j => (S.themes = j));
  const loadTheme = id => S.theme[id] ? Promise.resolve(S.theme[id]) : getJSON(`calls/themes/${id}.json`).then(j => (S.theme[id] = j));
  const loadPeople = () => S.people ? Promise.resolve(S.people) : getJSON("calls/people.json").then(j => (S.people = j));
  const loadCov = () => S.cov ? Promise.resolve(S.cov) : getJSON("calls/coverage.json").then(j => (S.cov = j));

  const TAX = {};
  const themeName = id => TAX[id]?.name || id;
  const setTax = list => (list || []).forEach(t => { TAX[t.id] = t; });
  const ETYPE = { earnings: "Earnings call", conference: "Conference", investor_day: "Investor day", annual_meeting: "Annual meeting", other: "Other event" };
  const ANS = { direct: ["Direct", "up"], partial: ["Partial", "warn"], deflected: ["Not answered", "down"] };
  const fsen = v => v == null ? "–" : (v > 0 ? "+" : v < 0 ? "−" : "") + Math.abs(v).toFixed(1);
  const senCls = v => v >= 0.25 ? "up" : v <= -0.25 ? "down" : "";
  const callName = c => [c.label, ETYPE[c.event_type] || c.event_type].filter(Boolean).join(" · ");
  const coName = t => SHORT[t] || IDX[t]?.name || t;

  /* ---------- styles (app.css variables only, so any theme applies) ---------- */
  if (!document.getElementById("cl-css")) document.head.append(el("style", { id: "cl-css" }, `
.cl-n{border:0;background:none;padding:0;font:inherit;color:inherit;cursor:pointer;font-variant-numeric:tabular-nums;text-decoration:underline dotted var(--faint);text-underline-offset:3px}
.cl-n:hover{color:var(--accent);text-decoration-color:var(--accent)}
.cl-n.up{color:var(--up)}.cl-n.down{color:var(--down)}
.cl-grid{display:grid;grid-template-columns:minmax(220px,300px) minmax(0,1fr);gap:16px;align-items:start}
.cl-list{display:flex;flex-direction:column;max-height:720px;overflow:auto}
.cl-item{display:flex;flex-direction:column;gap:2px;text-align:left;border:0;border-bottom:1px solid var(--line);background:none;padding:10px 14px;cursor:pointer}
.cl-item:hover{background:var(--sunk)}
.cl-item.on{background:var(--accent-soft)}
.cl-item .t1{font-weight:600;font-size:13px;display:flex;gap:6px;align-items:center;flex-wrap:wrap}
.cl-item .t2{font-size:11.5px;color:var(--muted)}
.cl-q{border:0;background:none;padding:0;text-align:left;font:inherit;color:var(--ink2);cursor:pointer;line-height:1.5}
.cl-q::before{content:"\\201C"}.cl-q::after{content:"\\201D"}
.cl-q:hover{color:var(--accent);text-decoration:underline;text-underline-offset:3px}
.cl-sum{line-height:1.6;margin:0}
.cl-th{display:grid;grid-template-columns:150px 64px 64px minmax(0,1fr);gap:4px 12px;padding:10px 0;border-bottom:1px solid var(--line);align-items:baseline}
.cl-th:last-child{border-bottom:0}
.cl-th .nm{font-weight:600}
.cl-th .lab{font-size:12px;color:var(--muted)}
.cl-th .qs{grid-column:1/-1;display:flex;flex-direction:column;gap:4px;font-size:12.5px;padding-left:12px;border-left:2px solid var(--line2)}
.cl-th .r{text-align:right}
.cl-ex{border:1px solid var(--line);border-radius:10px;padding:10px 12px;display:flex;flex-direction:column;gap:6px;background:var(--surface)}
.cl-ex .hd{display:flex;gap:6px;align-items:center;flex-wrap:wrap;font-size:12.5px}
.cl-ex .who{font-weight:600}
.cl-ex .cn{margin:0;line-height:1.5}
.cl-ex .turns{display:flex;flex-direction:column;gap:8px;border-top:1px solid var(--line);padding-top:8px}
.cl-turn{font-size:12.5px;line-height:1.55}
.cl-turn .sp{font-weight:600;font-size:12px;display:flex;gap:6px;align-items:center}
.cl-turn p{margin:2px 0 0;white-space:pre-wrap}
.cl-tools{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.cl-tools input,.cl-tools select{height:32px;border:1px solid var(--line);border-radius:7px;padding:0 10px;background:var(--surface);min-width:0}
.cl-tools input{flex:1 1 200px}
.cl-tl{border-collapse:separate;border-spacing:3px;font-size:11.5px}
.cl-tl th{font-weight:600;color:var(--muted);white-space:nowrap;padding:2px 6px;text-align:left}
.cl-tl thead th{font-size:11px;text-align:center;vertical-align:bottom}
.cl-tl td{width:46px;min-width:46px;height:26px;text-align:center;border-radius:5px;padding:0}
.cl-tl td button{width:100%;height:100%;border:0;border-radius:5px;cursor:pointer;font:inherit;font-variant-numeric:tabular-nums;color:var(--ink)}
.cl-tl td button:hover{outline:2px solid var(--accent)}
.cl-heat td{min-width:54px}
.cl-sel{outline:2px solid var(--accent)}
.cl-tiles{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px}
.cl-tile{border:1px solid var(--line);border-radius:10px;padding:10px 12px;background:var(--surface)}
.cl-tile .l{font-size:11.5px;color:var(--muted)}.cl-tile .v{font-size:18px;font-weight:600}
.cl-chips{display:flex;gap:6px;flex-wrap:wrap}
.cl-chips .chip{cursor:pointer;border:1px solid transparent}
.cl-chips .chip.on{background:var(--accent-soft);color:var(--accent);border-color:var(--accent)}
.cl-men{display:flex;flex-direction:column;gap:10px}
.cl-men .m{border-bottom:1px solid var(--line);padding-bottom:10px;display:flex;flex-direction:column;gap:4px}
.cl-men .m:last-child{border-bottom:0}
.cl-men .h{display:flex;gap:8px;align-items:center;flex-wrap:wrap;font-size:12.5px}
.cl-hl{background:var(--mark);color:var(--mark-ink);border-radius:2px}
.textdoc .cl-rng{background:var(--accent-soft)}
.cl-spark svg{width:90px;height:22px;vertical-align:middle}
.cl-src{font-size:11px;color:var(--faint);padding:0 16px 12px}
@media (max-width:860px){.cl-grid{grid-template-columns:minmax(0,1fr)}.cl-list{max-height:260px}.cl-th{grid-template-columns:minmax(0,1fr) 56px 56px}.cl-th .lab{grid-column:1/-1}}
`));

  /* ---------- source panel: the stored document text, scrolled to the quote ---------- */
  async function docPages(d) {
    if (S.pages[d.id]) return S.pages[d.id];
    if (!d.text_path) throw new Error("No extracted text for this document");
    const r = await fetch(IRPUB + d.text_path);
    if (!r.ok) throw new Error(`Text not available (${r.status})`);
    const j = await r.json();
    return (S.pages[d.id] = j.pages || []);
  }
  const normQ = s => String(s || "").replace(/[\u2018\u2019]/g, "'").replace(/[\u201c\u201d]/g, '"').replace(/[\u2013\u2014\u2212]/g, "-").replace(/\s+/g, " ").trim().toLowerCase();
  function findQuote(full, q) {
    // the stored offsets come from the same text; if the document was re-fetched since, find the quote again
    if (q.s != null && normQ(full.slice(q.s, q.e)) === normQ(q.q)) return [q.s, q.e];
    const n = normQ(q.q);
    if (!n) return q.s != null ? [q.s, q.e] : null;
    const words = n.split(" ");
    const rx = new RegExp(words.map(w => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&").replace(/'/g, "['\u2019\u2018]").replace(/"/g, "[\"\u201c\u201d]").replace(/-/g, "[-\u2013\u2014\u2212]")).join("\\s+"), "i");
    const m = rx.exec(full);
    return m ? [m.index, m.index + m[0].length] : (q.s != null ? [q.s, q.e] : null);
  }
  // opts: {eyebrow, title, display, sub, range: [s, e] (lightly shaded), term (highlighted inside the range)}
  async function openQuote(d, q, opts = {}) {
    if (!d) { toast("Source document not found"); return; }
    hideTip();
    DR.cell = { doc: d.id, quote: q };
    const dh = $("#dh"), db = $("#db");
    dh.innerHTML = ""; db.innerHTML = "";
    const fileUrl = d.storage_path ? IRPUB + d.storage_path : d.url;
    const pdf = /\.pdf$/i.test(d.ext || "");
    const pageLink = u => pdf && q?.p ? `${u}#page=${q.p}` : u;
    dh.append(
      el("div", { class: "row" }, el("span", { class: "eyebrow" }, opts.eyebrow || "Said on the call"),
        el("div", { class: "x", style: "display:flex;gap:6px" },
          fileUrl ? el("a", { class: "btn", href: pageLink(fileUrl), target: "_blank", rel: "noopener" }, "Stored file", svgUse("i-dl")) : null,
          d.url ? el("a", { class: "btn", href: pageLink(d.url), target: "_blank", rel: "noopener" }, "Original", svgUse("i-ext")) : null,
          el("button", { class: "iconbtn", onclick: closeSource, "aria-label": "Close (Esc)" }, svgUse("i-x")))),
      el("div", { class: "row" }, el("h2", {}, opts.title || docTitle(d))),
      el("div", { class: "row" }, opts.display ? el("span", { class: "chip acc mono", style: "font-size:13px;height:26px" }, opts.display) : null,
        el("span", { class: "muted", style: "font-size:12.5px" }, [opts.sub, docTitle(d).slice(0, 70), q?.p ? "page " + q.p : ""].filter(Boolean).join(" · "))));
    if (q?.q && !opts.range) db.append(el("div", { class: "note", style: "font-size:13px;color:var(--ink2);line-height:1.55" }, "\u201C" + q.q + "\u201D"));
    const status = el("span", { class: "faint", style: "font-size:12px" }, "Loading…");
    const view = el("div", { class: "viewer docv" });
    db.append(el("div", { class: "viewer-bar" }, el("span", { class: "badge-src" }, "Document text"), el("span", { class: "eyebrow" }, docTitle(d).slice(0, 60)), el("span", { style: "flex:1" }), status), view);
    drawer.classList.add("open"); drawer.setAttribute("aria-hidden", "false");
    if (innerWidth < 860) scrim.hidden = false;
    try {
      const pages = await docPages(d);
      if (DR.cell?.quote !== q) return;
      const full = pages.join("\n");
      const marks = [];
      if (opts.range) {
        marks.push([opts.range[0], opts.range[1], "cl-rng"]);
        if (opts.term) {
          const seg = full.slice(opts.range[0], opts.range[1]).toLowerCase(), t = opts.term.toLowerCase();
          let i = seg.indexOf(t);
          while (i >= 0) { marks.push([opts.range[0] + i, opts.range[0] + i + t.length, "fu-hit"]); i = seg.indexOf(t, i + t.length); }
        }
      } else if (q) {
        const r = findQuote(full, q);
        if (r) marks.push([r[0], r[1], "fu-hit"]);
      }
      const pre = el("div", { class: "textdoc" });
      view.innerHTML = ""; view.append(pre);
      let g = 0;
      pages.forEach((p, i) => {
        const a = g, b = g + p.length;
        g = b + 1;
        const cuts = new Set([a, b]);
        marks.forEach(([s, e]) => { if (e > a && s < b) { cuts.add(Math.max(a, s)); cuts.add(Math.min(b, e)); } });
        const pts = [...cuts].sort((x, y) => x - y);
        let html = "";
        for (let k = 0; k < pts.length - 1; k++) {
          const s = pts[k], e = pts[k + 1];
          const cl = marks.filter(m => m[0] <= s && m[1] >= e).map(m => m[2]);
          const txt = esc(full.slice(s, e));
          html += cl.includes("fu-hit") ? `<mark class="fu-hit">${txt}</mark>` : cl.length ? `<span class="${cl[0]}">${txt}</span>` : txt;
        }
        const sec = el("section", {}, pages.length > 1 ? el("div", { class: "pdf-num" }, `Page ${i + 1}`) : null);
        sec.append(el("pre", { html }));
        pre.append(sec);
      });
      const hit = pre.querySelector("mark.fu-hit") || pre.querySelector(".cl-rng");
      status.textContent = hit ? (marks.length ? (opts.range ? "Passage shaded" + (marks.length > 1 ? ", matches highlighted" : "") : "Quote highlighted") : "") + (q?.p ? ` · page ${q.p}` : "") : "Quote not found in the stored text";
      if (hit) requestAnimationFrame(() => { pre.scrollTop = Math.max(0, hit.offsetTop - pre.clientHeight / 3); });
    } catch (e) {
      status.textContent = "";
      view.innerHTML = "";
      view.append(el("div", { class: "viewer-load" }, el("div", {}, "This document could not be displayed here."), el("div", { class: "faint", style: "font-size:12px" }, String(e.message || e)), fileUrl ? el("a", { class: "btn", href: fileUrl, target: "_blank", rel: "noopener" }, "Open the stored file") : null));
    }
  }
  const quoteBtn = (d, q, opts, text) => el("button", { class: "cl-q", title: "Open the passage in the source document", onclick: e => { e.stopPropagation(); openQuote(d, q, opts); } }, text || q.q);
  const numBtn = (text, onclick, extra = "") => el("button", { class: "cl-n " + extra, title: "Open source", onclick: e => { e.stopPropagation(); onclick(); } }, text);

  /* ---------- company tab ---------- */
  async function tab(body, tk) {
    const host = el("div", { style: "display:flex;flex-direction:column;gap:16px" }, el("div", { class: "skel", style: "height:240px" }));
    body.append(host);
    let B;
    try { B = await loadCo(tk); } catch (e) { host.innerHTML = ""; host.append(el("div", { class: "empty" }, "Call data could not be loaded. ", String(e.message || e))); return; }
    setTax(B.taxonomy);
    host.innerHTML = "";
    const calls = B.calls || [];
    if (!calls.length) {
      host.append(panel("Earnings calls", null, el("div", { class: "empty" }, el("p", {}, `No call transcripts or prepared remarks are stored for ${tk} yet.`), el("p", { class: "faint", style: "font-size:12px" }, "Transcripts come from the company's investor relations site and 8-K exhibits. New ones are processed after each collection run."))));
      return;
    }
    const done = calls.filter(c => c.themes);
    const pending = calls.length - done.length;
    host.append(el("div", { class: "cl-tiles" },
      tile("Calls stored", calls.length, `${calls.filter(c => c.event_type === "earnings").length} earnings calls`),
      tile("Analysed", done.length, pending ? `${pending} waiting` : "all"),
      tile("With Q&A", calls.filter(c => c.has_qa).length, "analyst questions parsed"),
      tile("Date range", calls.at(-1).date === calls[0].date ? fdate(calls[0].date) : `${fmon(calls.at(-1).date)} – ${fmon(calls[0].date)}`, "by call date")));
    const listP = el("div", { class: "cl-list" });
    const detail = el("div", { style: "display:flex;flex-direction:column;gap:16px;min-width:0" });
    host.append(el("div", { class: "cl-grid" }, panel("Calls", null, listP), detail));
    let sel = S.sel[tk] && calls.find(c => c.call_id === S.sel[tk]) ? S.sel[tk] : (done[0] || calls[0]).call_id;
    const drawList = () => {
      listP.innerHTML = "";
      calls.forEach(c => listP.append(el("button", { class: "cl-item" + (c.call_id === sel ? " on" : ""), onclick: () => { sel = S.sel[tk] = c.call_id; drawList(); drawCall(); } },
        el("span", { class: "t1" }, fdate(c.date), c.label ? el("span", { class: "chip" }, c.label) : null),
        el("span", { class: "t2" }, [ETYPE[c.event_type] || c.event_type, c.kind === "remarks" ? "Prepared remarks" : c.has_qa ? "Transcript with Q&A" : "Transcript", c.themes ? null : "analysis pending"].filter(Boolean).join(" · ")))));
    };
    const drawCall = () => { detail.innerHTML = ""; renderCall(detail, tk, B, calls.find(c => c.call_id === sel)); };
    drawList(); drawCall();
    const tl = el("div", { class: "bd", style: "overflow-x:auto" });
    host.append(panel("Theme timeline", el("span", { class: "faint", style: "font-size:12px" }, "Sentiment of each theme per call; shading shows how certain management sounded"), tl, el("div", { class: "cl-src" }, "Click a cell to open the passage it is based on. Sentiment runs from −1 to +1 and was assigned by a language model from the quoted passages.")));
    timeline(tl, tk, B);
    const gd = el("div", { class: "bd", style: "overflow-x:auto" });
    host.append(panel("Guidance statements", null, gd, el("div", { class: "cl-src" }, "Forward-looking figures management stated on calls. Each value opens the sentence it was taken from.")));
    guidanceTable(gd, B, null);
    const sr = el("div", { class: "bd" });
    host.append(panel("Search the Q&A", null, sr));
    searchQA(sr, tk, B);
    const ex = el("div", { class: "bd", style: "overflow-x:auto" });
    host.append(panel("Executives across calls", null, ex, el("div", { class: "cl-src" }, "Talk share: the speaker's words as a share of all words spoken (operator excluded). Hedging: hedge words (may, could, approximately, we believe...) per 1,000 words. Tone: positive minus negative finance words per 1,000 words. Counted from the transcript text.")));
    execTable(ex, tk, B);
  }
  const tile = (l, v, s) => el("div", { class: "cl-tile" }, el("div", { class: "l" }, l), el("div", { class: "v num" }, String(v)), el("div", { class: "faint", style: "font-size:11px" }, s));

  function renderCall(host, tk, B, c) {
    const d = B.docs[c.doc];
    const head = el("div", { class: "bd", style: "display:flex;flex-direction:column;gap:10px" },
      el("div", { style: "display:flex;gap:8px;flex-wrap:wrap;align-items:center" },
        el("span", { class: "chip acc" }, ETYPE[c.event_type] || c.event_type), c.label ? el("span", { class: "chip" }, c.label) : null,
        el("span", { class: "chip" }, c.kind === "remarks" ? "Prepared remarks" : c.has_qa ? "Transcript with Q&A" : "Transcript"),
        el("span", { class: "muted", style: "font-size:12.5px" }, `${fdate(c.date)} · ${(c.words || 0).toLocaleString("en-US")} words · ${c.n_turns || 0} passages`),
        el("span", { style: "flex:1" }),
        d ? el("button", { class: "btn", onclick: () => openQuote(d, null, { eyebrow: "Call document", title: callName(c) + " · " + fdate(c.date) }) }, "Open document") : null),
      c.summary ? el("p", { class: "cl-sum" }, c.summary) : el("div", { class: "note" }, "This call has been parsed into speakers and Q&A exchanges but not analysed yet, so themes, guidance and answer ratings are not shown. Analysis runs after each document collection."),
      c.summary ? el("div", { class: "faint", style: "font-size:11.5px" }, `Summary written by ${c.model || "a language model"} from the transcript${c.analyzed_at ? ", " + relTime(c.analyzed_at) : ""}. Themes, guidance and Q&A items below are kept only when their quote was found in the document.`) : null);
    host.append(panel(c.title || callName(c), null, head));
    if (!c.themes) { if (c.exchanges?.length) renderQA(host, tk, B, c); renderSpeakers(host, tk, B, c); return; }
    // themes
    const th = el("div", { class: "bd" });
    c.themes.forEach(t => {
      const o = { eyebrow: "Theme · " + themeName(t.theme), title: `${coName(tk)} · ${callName(c)}`, sub: t.label };
      th.append(el("div", { class: "cl-th" },
        el("span", { class: "nm" }, themeName(t.theme)),
        el("span", { class: "r", title: "Sentiment, −1 to +1" }, numBtn(fsen(t.sentiment), () => openQuote(d, t.quotes[0], { ...o, display: "sentiment " + fsen(t.sentiment) }), senCls(t.sentiment))),
        el("span", { class: "r", title: "How certain management sounded, 0 to 1" }, numBtn(t.confidence.toFixed(1), () => openQuote(d, t.quotes[0], { ...o, display: "confidence " + t.confidence.toFixed(1) }))),
        el("span", { class: "lab" }, el("b", { style: "color:var(--ink2)" }, t.label), " · ", t.summary),
        el("div", { class: "qs" }, t.quotes.map(q => quoteBtn(d, q, o)))));
    });
    host.append(panel("Themes", el("span", { class: "faint", style: "font-size:12px" }, "Sentiment · Confidence"), th));
    if (c.changes?.length) {
      const ch = el("div", { class: "bd", style: "display:flex;flex-direction:column;gap:10px" });
      const prev = B.calls.find(x => x.call_id === c.prev_call);
      c.changes.forEach(x => ch.append(el("div", {}, el("div", { style: "display:flex;gap:6px;align-items:center" }, el("span", { class: "chip" }, themeName(x.theme)), el("span", {}, x.text)), el("div", { style: "font-size:12.5px;margin-top:4px;padding-left:12px;border-left:2px solid var(--line2)" }, quoteBtn(d, x.quote, { eyebrow: "Change vs previous call", title: `${coName(tk)} · ${callName(c)}`, sub: themeName(x.theme) })))));
      host.append(panel("Changes since the previous call", prev ? el("button", { class: "chip link", onclick: () => { S.sel[tk] = prev.call_id; route(); } }, "Previous: " + fdate(prev.date)) : null, ch));
    }
    if (c.guidance?.length) {
      const g = el("div", { class: "bd", style: "overflow-x:auto" });
      guidanceTable(g, B, c.call_id);
      host.append(panel("Guidance on this call", null, g));
    }
    if (c.exchanges?.length) renderQA(host, tk, B, c);
    renderSpeakers(host, tk, B, c);
  }

  function fmtVal(v, unit) {
    if (v == null) return null;
    const u = (unit || "").toLowerCase();
    if (u === "%" || u === "pct" || u === "percent") return fnum(v, Math.abs(v) < 10 && v % 1 ? 1 : v % 1 ? 1 : 0) + "%";
    if (u === "usd") return fmtMoney(v);
    if (u === "usd_per_share") return "$" + fnum(v, 2);
    if (u === "x") return fnum(v, 1) + "×";
    return Math.abs(v) >= 1e6 ? fmtMoney(v).replace(/^\$/, "") : fnum(v, v % 1 ? 2 : 0);
  }
  function fmtRange(g) {
    const a = fmtVal(g.low, g.unit), b = fmtVal(g.high, g.unit);
    if (a && b && a !== b) return `${a} to ${b}`;
    return a || b || "see quote";
  }
  function guidanceTable(host, B, onlyCall) {
    const rows = [];
    B.calls.forEach(c => (c.guidance || []).forEach(g => { if (!onlyCall || c.call_id === onlyCall) rows.push({ c, g }); }));
    if (!rows.length) { host.append(el("div", { class: "empty" }, "No quantitative guidance was found on the analysed calls.")); return; }
    let q = "";
    const tools = el("div", { class: "cl-tools", style: "margin-bottom:8px" }, el("input", { placeholder: "Filter by metric or period", oninput: e => { q = e.target.value.toLowerCase(); draw(); } }));
    const box = el("div");
    if (!onlyCall && rows.length > 8) host.append(tools);
    host.append(box);
    const BAS = { raised: "up", lowered: "down", new: "acc", maintained: "", reiterated: "", unclear: "" };
    const draw = () => {
      box.innerHTML = "";
      const t = el("table", { class: "t" }, el("thead", {}, el("tr", {}, onlyCall ? null : el("th", {}, "Call"), el("th", {}, "Metric"), el("th", {}, "Period"), el("th", { class: "r" }, "Value"), el("th", {}, "Basis"))));
      const tb = el("tbody");
      rows.filter(({ g }) => !q || `${g.metric} ${g.period}`.toLowerCase().includes(q)).slice(0, onlyCall ? 100 : 400).forEach(({ c, g }) => {
        const d = B.docs[c.doc];
        tb.append(el("tr", {}, onlyCall ? null : el("td", { style: "white-space:nowrap" }, fdate(c.date), c.label ? el("span", { class: "faint" }, " · " + c.label) : null),
          el("td", {}, g.metric), el("td", { class: "muted" }, g.period || "–"),
          el("td", { class: "r", style: "white-space:nowrap" }, numBtn(fmtRange(g), () => openQuote(d, g.quote, { eyebrow: "Guidance", title: `${coName(B.ticker)} · ${g.metric}${g.period ? " · " + g.period : ""}`, display: fmtRange(g), sub: callName(c) + " · " + fdate(c.date) }))),
          el("td", {}, el("span", { class: "chip " + (BAS[g.basis] || "") }, g.basis))));
      });
      t.append(tb); box.append(t);
    };
    draw();
  }

  function renderQA(host, tk, B, c) {
    const d = B.docs[c.doc];
    const sp = c.speakers || [];
    let topic = "", ans = "", q = "";
    const topics = [...new Set(c.exchanges.flatMap(x => x.topics || []))];
    const list = el("div", { style: "display:flex;flex-direction:column;gap:10px" });
    const tools = el("div", { class: "cl-tools" },
      el("input", { placeholder: "Search this call's questions and answers", oninput: e => { q = e.target.value.trim().toLowerCase(); draw(); } }),
      el("select", { onchange: e => { topic = e.target.value; draw(); } }, el("option", { value: "" }, "All topics"), ...topics.map(t => el("option", { value: t }, themeName(t)))),
      el("select", { onchange: e => { ans = e.target.value; draw(); } }, el("option", { value: "" }, "All answers"), el("option", { value: "direct" }, "Direct"), el("option", { value: "partial" }, "Partial"), el("option", { value: "deflected" }, "Not answered")));
    const bd = el("div", { class: "bd", style: "display:flex;flex-direction:column;gap:12px" }, tools, list);
    const nDef = c.exchanges.filter(x => x.answer === "deflected" || x.answer === "partial").length;
    host.append(panel(`Q&A · ${c.exchanges.length} exchanges`, nDef ? el("span", { class: "chip warn" }, `${nDef} not answered directly`) : null, bd,
      el("div", { class: "cl-src" }, "Concerns and answer ratings were written by a language model; each opens the quoted words in the transcript. Search reads the full passage text.")));
    let T = null;
    const draw = async () => {
      if (q && !T) { try { T = await loadTurns(tk, c.call_id); } catch { T = { turns: [] }; } }
      list.innerHTML = "";
      const xs = c.exchanges.filter(x => (!topic || (x.topics || []).includes(topic)) && (!ans || x.answer === ans) && (!q || (T && [...x.q_turns, ...x.a_turns].some(i => (T.turns[i]?.x || "").toLowerCase().includes(q))) || (x.concern || "").toLowerCase().includes(q)));
      if (!xs.length) list.append(el("div", { class: "empty" }, "No exchanges match."));
      xs.forEach(x => list.append(exchangeCard(tk, B, c, x, d, sp, q)));
    };
    draw();
  }
  function exchangeCard(tk, B, c, x, d, sp, term) {
    const a = x.analyst != null ? sp[x.analyst] : x.asked_by != null ? sp[x.asked_by] : null;
    const o = { eyebrow: "Analyst question", title: `${coName(tk)} · ${callName(c)}`, sub: a ? `${a.name}${a.firm ? ", " + a.firm : ""}` : "" };
    const turnsBox = el("div", { class: "turns", hidden: true });
    const card = el("div", { class: "cl-ex" },
      el("div", { class: "hd" }, el("span", { class: "who" }, a ? a.name : "Question"), a?.firm ? el("span", { class: "muted" }, a.firm) : null,
        ...(x.topics || []).map(t => el("span", { class: "chip" }, themeName(t))),
        x.answer ? el("span", { class: "chip " + ANS[x.answer][1], title: x.reason || "" }, ANS[x.answer][0]) : null,
        el("span", { style: "flex:1" }),
        el("button", { class: "chip link", onclick: async () => { if (turnsBox.hidden && !turnsBox.childElementCount) await fillTurns(turnsBox, tk, B, c, x, d, sp, term); turnsBox.hidden = !turnsBox.hidden; } }, "Full exchange")),
      x.concern ? el("p", { class: "cn" }, x.concern) : null,
      x.concern_quote ? el("div", { style: "font-size:12.5px;padding-left:12px;border-left:2px solid var(--line2)" }, quoteBtn(d, x.concern_quote, o)) : null,
      x.reason ? el("div", { style: "font-size:12.5px;display:flex;flex-direction:column;gap:4px" }, el("span", { class: "muted" }, x.reason), x.answer_quote ? el("div", { style: "padding-left:12px;border-left:2px solid var(--warn)" }, quoteBtn(d, x.answer_quote, { ...o, eyebrow: "Answer" })) : null) : null,
      turnsBox);
    if (term) card.querySelector(".chip.link").click();
    return card;
  }
  async function fillTurns(box, tk, B, c, x, d, sp, term) {
    box.append(el("div", { class: "faint", style: "font-size:12px" }, "Loading…"));
    let T;
    try { T = await loadTurns(tk, c.call_id); } catch (e) { box.innerHTML = ""; box.append(el("div", { class: "faint" }, "Passages could not be loaded.")); return; }
    box.innerHTML = "";
    [...x.q_turns, ...x.a_turns].sort((p, q) => p - q).forEach(i => {
      const t = T.turns[i]; if (!t) return;
      const s = sp[t.sp] || {};
      const p = el("p");
      const txt = t.x;
      if (term) {
        let html = "", pos = 0; const low = txt.toLowerCase();
        let k = low.indexOf(term);
        while (k >= 0) { html += esc(txt.slice(pos, k)) + `<mark class="cl-hl">${esc(txt.slice(k, k + term.length))}</mark>`; pos = k + term.length; k = low.indexOf(term, pos); }
        p.innerHTML = html + esc(txt.slice(pos));
      } else p.textContent = txt;
      box.append(el("div", { class: "cl-turn" }, el("div", { class: "sp" }, s.name || "Speaker", s.role ? el("span", { class: "faint", style: "font-weight:400" }, s.role) : null,
        el("button", { class: "chip link", onclick: () => openQuote(d, { s: t.s, e: t.e, p: t.p, q: null }, { eyebrow: "Passage", title: `${s.name || "Speaker"} · ${callName(c)}`, sub: fdate(c.date), range: [t.s, t.e], term }) }, "Source")), p));
    });
  }

  function renderSpeakers(host, tk, B, c) {
    const sp = (c.speakers || []).filter(s => s.kind !== "operator" && s.words);
    if (!sp.length) return;
    const d = B.docs[c.doc];
    const box = el("div", { class: "bd", style: "overflow-x:auto" });
    const t = el("table", { class: "t" }, el("thead", {}, el("tr", {}, el("th", {}, "Speaker"), el("th", {}, "Role"), el("th", { class: "r" }, "Words"), el("th", { class: "r" }, "Talk share"), el("th", { class: "r" }, "Hedging /1k"), el("th", { class: "r" }, "Tone /1k"))));
    const tb = el("tbody");
    const openFirst = async (s, display) => {
      try {
        const T = await loadTurns(tk, c.call_id);
        const ts = T.turns.filter(x => x.sp === s.idx);
        if (!ts.length) return toast("No passages for this speaker");
        openQuote(d, { s: ts[0].s, e: ts[0].e, p: ts[0].p, q: null }, { eyebrow: "Speaker", title: `${s.name} · ${callName(c)}`, display, sub: `${ts.length} passage${ts.length === 1 ? "" : "s"}, ${s.words.toLocaleString("en-US")} words; first passage shaded`, range: [ts[0].s, ts[0].e] });
      } catch { toast("Passages could not be loaded"); }
    };
    sp.sort((a, b) => b.words - a.words).forEach(s => tb.append(el("tr", {},
      el("td", { style: "font-weight:600" }, s.name), el("td", { class: "muted", style: "font-size:12.5px" }, [s.kind === "analyst" ? "Analyst" : s.role, s.firm].filter(Boolean).join(", ") || "–"),
      el("td", { class: "r" }, numBtn(s.words.toLocaleString("en-US"), () => openFirst(s, s.words.toLocaleString("en-US") + " words"))),
      el("td", { class: "r" }, s.share != null ? numBtn(fpct(s.share), () => openFirst(s, "talk share " + fpct(s.share))) : "–"),
      el("td", { class: "r" }, s.hedge != null ? numBtn(s.hedge.toFixed(1), () => openFirst(s, "hedging " + s.hedge.toFixed(1) + " per 1,000 words")) : "–"),
      el("td", { class: "r" }, s.tone != null ? numBtn(fsen(s.tone).replace(".0", ""), () => openFirst(s, "tone " + s.tone.toFixed(1) + " per 1,000 words"), senCls(s.tone / 10)) : "–"))));
    t.append(tb); box.append(t);
    host.append(panel("Speakers", null, box));
  }

  function senColor(v, conf) {
    const a = 0.18 + 0.55 * Math.max(0, Math.min(1, conf ?? 0.5));
    const base = v >= 0.15 ? css("--up") : v <= -0.15 ? css("--down") : css("--faint");
    return `color-mix(in srgb, ${base} ${Math.round(a * 100 * Math.max(0.35, Math.abs(v)))}%, var(--surface))`;
  }
  function timeline(host, tk, B) {
    const calls = B.calls.filter(c => c.themes).slice().sort((a, b) => a.date < b.date ? -1 : 1);
    if (!calls.length) { host.append(el("div", { class: "empty" }, "No analysed calls yet.")); return; }
    const cnt = {};
    calls.forEach(c => c.themes.forEach(t => { cnt[t.theme] = (cnt[t.theme] || 0) + 1; }));
    const rows = Object.keys(cnt).sort((a, b) => cnt[b] - cnt[a]);
    const t = el("table", { class: "cl-tl" });
    t.append(el("thead", {}, el("tr", {}, el("th", {}, ""), ...calls.map(c => el("th", { title: `${callName(c)} · ${fdate(c.date)}` }, c.label ? c.label.replace(" FY", " ") : fmon(c.date), el("div", { class: "faint", style: "font-weight:400" }, c.event_type === "earnings" ? fmon(c.date) : (ETYPE[c.event_type] || "").split(" ")[0]))))));
    const tb = el("tbody");
    rows.forEach(r => {
      const tr = el("tr", {}, el("th", {}, themeName(r)));
      calls.forEach(c => {
        const th = c.themes.find(x => x.theme === r);
        tr.append(el("td", {}, th ? el("button", { style: `background:${senColor(th.sentiment, th.confidence)}`, title: `${th.label}: sentiment ${fsen(th.sentiment)}, confidence ${th.confidence.toFixed(1)}`, onclick: () => openQuote(B.docs[c.doc], th.quotes[0], { eyebrow: "Theme · " + themeName(r), title: `${coName(tk)} · ${callName(c)}`, display: "sentiment " + fsen(th.sentiment), sub: th.label }) }, fsen(th.sentiment)) : null));
      });
      tb.append(tr);
    });
    t.append(tb); host.append(t);
  }

  function searchQA(host, tk, B) {
    const withQA = B.calls.filter(c => c.has_qa && c.exchanges?.length);
    if (!withQA.length) { host.append(el("div", { class: "muted" }, "None of the stored calls has a parsed Q&A section.")); return; }
    const out = el("div", { style: "display:flex;flex-direction:column;gap:8px;margin-top:10px" });
    let tmr;
    const inp = el("input", { placeholder: `Search ${withQA.length} calls' questions and answers (e.g. pricing, tariffs, a name)`, oninput: () => { clearTimeout(tmr); tmr = setTimeout(run, 250); } });
    host.append(el("div", { class: "cl-tools" }, inp), out);
    async function run() {
      const q = inp.value.trim().toLowerCase();
      out.innerHTML = "";
      if (q.length < 3) return;
      out.append(el("div", { class: "faint", style: "font-size:12px" }, "Searching…"));
      const files = await Promise.all(withQA.map(c => loadTurns(tk, c.call_id).catch(() => null)));
      if (inp.value.trim().toLowerCase() !== q) return;
      out.innerHTML = "";
      let n = 0;
      withQA.forEach((c, ci) => {
        const T = files[ci]; if (!T) return;
        const sp = c.speakers || [];
        c.exchanges.forEach(x => {
          [...x.q_turns, ...x.a_turns].forEach(i => {
            const t = T.turns[i]; if (!t || n >= 80) return;
            const k = t.x.toLowerCase().indexOf(q); if (k < 0) return;
            n++;
            const a = Math.max(0, k - 110), b = Math.min(t.x.length, k + q.length + 160);
            const snip = el("span", { html: (a ? "…" : "") + esc(t.x.slice(a, k)) + `<mark class="cl-hl">${esc(t.x.slice(k, k + q.length))}</mark>` + esc(t.x.slice(k + q.length, b)) + (b < t.x.length ? "…" : "") });
            const s = sp[t.sp] || {};
            out.append(el("button", { class: "cl-item", style: "border:1px solid var(--line);border-radius:8px", onclick: () => openQuote(B.docs[c.doc], { s: t.s, e: t.e, p: t.p, q: null }, { eyebrow: "Q&A search", title: `${s.name || "Speaker"} · ${callName(c)}`, sub: fdate(c.date), range: [t.s, t.e], term: q }) },
              el("span", { class: "t1" }, fdate(c.date), c.label ? el("span", { class: "chip" }, c.label) : null, el("span", { class: "muted", style: "font-weight:500" }, `${s.name || ""}${s.firm && s.kind === "analyst" ? ", " + s.firm : ""}`)),
              el("span", { style: "font-size:12.5px;color:var(--ink2);line-height:1.5" }, snip)));
          });
        });
      });
      if (!n) out.append(el("div", { class: "muted" }, "No passage in the Q&A contains that text."));
      else if (n >= 80) out.append(el("div", { class: "faint", style: "font-size:12px" }, "First 80 matches shown."));
    }
  }

  function execTable(host, tk, B) {
    const by = {};
    B.calls.filter(c => c.themes).slice().sort((a, b) => a.date < b.date ? -1 : 1).forEach(c => (c.speakers || []).forEach(s => {
      if (s.kind !== "management" || s.words < 150) return;
      const k = s.name.toLowerCase().replace(/[^a-z ]/g, "").split(" ").filter(Boolean);
      const key = k[0] + " " + k.at(-1);
      (by[key] = by[key] || { name: s.name, role: s.role, rows: [] }).rows.push({ c, s });
      if (s.role) by[key].role = s.role;
    }));
    const ex = Object.values(by).sort((a, b) => b.rows.length - a.rows.length);
    if (!ex.length) { host.append(el("div", { class: "empty" }, "No executive statistics yet.")); return; }
    const t = el("table", { class: "t" }, el("thead", {}, el("tr", {}, el("th", {}, "Executive"), el("th", { class: "r" }, "Calls"), el("th", {}, "Talk share"), el("th", { class: "r" }, "Latest"), el("th", {}, "Hedging /1k"), el("th", { class: "r" }, "Latest"), el("th", {}, "Tone /1k"), el("th", { class: "r" }, "Latest"))));
    const tb = el("tbody");
    const open = (r, disp) => loadTurns(tk, r.c.call_id).then(T => { const ts = T.turns.filter(x => x.sp === r.s.idx); if (ts.length) openQuote(B.docs[r.c.doc], { s: ts[0].s, e: ts[0].e, p: ts[0].p, q: null }, { eyebrow: "Executive", title: `${r.s.name} · ${callName(r.c)}`, display: disp, sub: `${fdate(r.c.date)} · ${ts.length} passages; first shaded`, range: [ts[0].s, ts[0].e] }); }).catch(() => toast("Passages could not be loaded"));
    ex.forEach(e => {
      const L = e.rows.at(-1);
      tb.append(el("tr", {}, el("td", {}, el("div", { style: "font-weight:600" }, e.name), el("div", { class: "faint", style: "font-size:11.5px" }, e.role || "")),
        el("td", { class: "r" }, String(e.rows.length)),
        el("td", { class: "cl-spark" }, sparkSVG(e.rows.map(r => r.s.share), css("--c1"), 90, 22)),
        el("td", { class: "r" }, L.s.share != null ? numBtn(fpct(L.s.share), () => open(L, "talk share " + fpct(L.s.share))) : "–"),
        el("td", { class: "cl-spark" }, sparkSVG(e.rows.map(r => r.s.hedge), css("--c3"), 90, 22)),
        el("td", { class: "r" }, L.s.hedge != null ? numBtn(L.s.hedge.toFixed(1), () => open(L, "hedging " + L.s.hedge.toFixed(1))) : "–"),
        el("td", { class: "cl-spark" }, sparkSVG(e.rows.map(r => r.s.tone), css("--c2"), 90, 22)),
        el("td", { class: "r" }, L.s.tone != null ? numBtn(fsen(L.s.tone), () => open(L, "tone " + L.s.tone.toFixed(1)), senCls(L.s.tone / 10)) : "–")));
    });
    t.append(tb); host.append(t);
  }

  /* ---------- cross-company page ---------- */
  const VIEWS = [["themes", "Themes"], ["executives", "Executives"], ["analysts", "Analysts"], ["coverage", "Coverage"]];
  async function render(arg) {
    const a = (arg || "").toLowerCase();
    const view = VIEWS.some(v => v[0] === a) ? a : "themes";
    const themeArg = !VIEWS.some(v => v[0] === a) && a ? a : null;
    page.append(el("div", { class: "ph" }, el("div", {}, el("div", { class: "eyebrow" }, "Earnings calls"), el("h1", {}, "What companies said on their calls"),
      el("div", { class: "sub muted", style: "max-width:760px;margin-top:6px" }, "Themes, guidance, analyst questions and how they were answered, from stored transcripts and prepared remarks. Every mention opens the passage in the source document."))));
    const body = el("div", { style: "display:flex;flex-direction:column;gap:16px;margin-top:16px" });
    page.append(el("div", { style: "margin-top:14px" }, seg(VIEWS, view, v => { location.hash = v === "themes" ? "calls" : "calls-" + v; })), body);
    try {
      if (view === "themes") await themesView(body, themeArg);
      else if (view === "executives") await execView(body);
      else if (view === "analysts") await analystView(body);
      else await coverageView(body);
    } catch (e) {
      body.append(el("div", { class: "empty" }, miss(e) ? "Call data has not been published yet." : "Call data could not be loaded: " + (e.message || e)));
    }
  }

  async function themesView(body, themeArg) {
    const T = await loadThemes();
    setTax(T.taxonomy);
    const qs = T.quarters.slice(-12);
    let theme = themeArg && TAX[themeArg] ? themeArg : (Object.entries(T.totals || {}).sort((a, b) => b[1] - a[1])[0] || ["ai"])[0];
    let mode = "share", pick = null;
    const share = (th, sec, q) => {
      const secs = sec ? [sec] : T.sectors;
      let n = 0, s = 0, tot = 0;
      secs.forEach(x => { const v = T.cells[th]?.[x]?.[q]; if (v) { n += v[0]; s += v[0] * v[1]; } tot += T.calls[x]?.[q] || 0; });
      return { n, tot, share: tot ? n / tot : null, sen: n ? s / n : null };
    };
    // latest quarter ranking
    const lastQ = qs.at(-1), prevQ = qs.at(-2);
    const rank = el("div", { class: "bd", style: "overflow-x:auto" });
    const rt = el("table", { class: "t" }, el("thead", {}, el("tr", {}, el("th", {}, "Theme"), el("th", { class: "r" }, `Share of calls ${lastQ || ""}`), el("th", { class: "r" }, `${prevQ || ""}`), el("th", { class: "r" }, "Avg sentiment"), el("th", {}, "Last 12 quarters"))));
    const rtb = el("tbody");
    T.taxonomy.map(t => ({ t, l: share(t.id, null, lastQ), p: share(t.id, null, prevQ) })).filter(x => x.l.n || x.p.n).sort((a, b) => (b.l.share || 0) - (a.l.share || 0)).forEach(({ t, l, p }) => {
      rtb.append(el("tr", {}, el("td", { style: "font-weight:600" }, t.name, el("div", { class: "faint", style: "font-size:11px;font-weight:400" }, t.desc)),
        el("td", { class: "r" }, l.n ? numBtn(`${fpct(l.share, 0)} (${l.n})`, () => { theme = t.id; pick = { sec: null, q: lastQ }; drawAll(); }) : "–"),
        el("td", { class: "r" }, p.n ? numBtn(`${fpct(p.share, 0)} (${p.n})`, () => { theme = t.id; pick = { sec: null, q: prevQ }; drawAll(); }) : "–"),
        el("td", { class: "r" }, l.sen != null ? el("span", { class: senCls(l.sen) }, fsen(l.sen)) : "–"),
        el("td", { class: "cl-spark" }, sparkSVG(qs.map(q => share(t.id, null, q).share), css("--c1"), 90, 22))));
    });
    rt.append(rtb); rank.append(rt);
    const chips = el("div", { class: "cl-chips" });
    const heat = el("div", { class: "bd", style: "overflow-x:auto" });
    const men = el("div", { class: "bd" });
    const hpanel = panel("By sector and quarter", seg([["share", "Share of calls"], ["sen", "Sentiment"]], mode, v => { mode = v; drawHeat(); }), el("div", { class: "bd", style: "padding-bottom:0" }, chips), heat, el("div", { class: "cl-src" }, "Share of calls: analysed calls in that sector and quarter that discussed the theme. Sentiment: average of the per-call theme sentiment (−1 to +1). Click a cell to read the mentions."));
    const mpanel = panel("Mentions", null, men);
    body.append(hpanel, mpanel, panel(`Themes in ${lastQ || "the latest quarter"}`, null, rank, el("div", { class: "cl-src" }, "Counts in brackets are calls. Click a figure to list the calls behind it.")));
    const drawChips = () => { chips.innerHTML = ""; T.taxonomy.forEach(t => chips.append(el("button", { class: "chip" + (t.id === theme ? " on" : ""), onclick: () => { theme = t.id; pick = null; drawAll(); }, title: t.desc }, t.name, el("span", { class: "faint" }, String(T.totals?.[t.id] || 0))))); };
    const drawHeat = () => {
      heat.innerHTML = "";
      const t = el("table", { class: "cl-tl cl-heat" });
      t.append(el("thead", {}, el("tr", {}, el("th", {}, ""), ...qs.map(q => el("th", {}, q.replace("Q", " Q"))))));
      const tb = el("tbody");
      [null, ...T.sectors].forEach(sec => {
        const tr = el("tr", {}, el("th", { style: sec ? "" : "color:var(--ink)" }, sec || "All sectors"));
        qs.forEach(q => {
          const v = share(theme, sec, q);
          const on = pick && pick.q === q && pick.sec === sec;
          if (!v.n) { tr.append(el("td", { style: "background:var(--sunk)", title: v.tot ? `0 of ${v.tot} calls` : "No analysed calls" })); return; }
          const bg = mode === "share" ? `color-mix(in srgb, var(--accent) ${Math.round(12 + 70 * Math.min(1, v.share / 0.8))}%, var(--surface))` : senColor(v.sen, 0.8);
          const txt = mode === "share" ? fpct(v.share, 0) : fsen(v.sen);
          tr.append(el("td", { class: on ? "cl-sel" : "" }, el("button", { style: `background:${bg};color:${mode === "share" && v.share > 0.45 ? "#fff" : "var(--ink)"}`, title: `${v.n} of ${v.tot} calls, average sentiment ${fsen(v.sen)}`, onclick: () => { pick = { sec, q }; drawHeat(); drawMentions(); } }, txt)));
        });
        tb.append(tr);
      });
      t.append(tb); heat.append(t);
    };
    const drawMentions = async () => {
      men.innerHTML = "";
      mpanel.querySelector("h3").textContent = `Mentions · ${themeName(theme)}${pick ? " · " + (pick.sec || "All sectors") + " · " + pick.q : " · latest"}`;
      men.append(el("div", { class: "faint" }, "Loading…"));
      let M;
      try { M = await loadTheme(theme); } catch (e) { men.innerHTML = ""; men.append(el("div", { class: "empty" }, "Mentions could not be loaded.")); return; }
      men.innerHTML = "";
      const ms = M.mentions.filter(m => !pick || (m.q === pick.q && (!pick.sec || m.sec === pick.sec))).slice(0, 120);
      if (!ms.length) { men.append(el("div", { class: "empty" }, "No mentions in this cell.")); return; }
      const box = el("div", { class: "cl-men" });
      ms.forEach(m => {
        const d = M.docs[m.doc];
        const o = { eyebrow: "Theme · " + themeName(theme), title: `${coName(m.t)} · ${[m.l, ETYPE[m.et]].filter(Boolean).join(" · ")}`, sub: m.lab, display: "sentiment " + fsen(m.sen) };
        box.append(el("div", { class: "m" },
          el("div", { class: "h" }, el("a", { class: "mono tkc", href: "#company-" + m.t.toLowerCase(), onclick: () => { goCall(m.t, m.c); } }, miniLogo(m.t, 16), m.t), el("span", { class: "muted" }, `${fdate(m.d)}${m.l ? " · " + m.l : ""} · ${m.sec}`),
            el("span", {}, el("b", {}, m.lab)), el("span", { style: "flex:1" }),
            numBtn(fsen(m.sen), () => openQuote(d, m.quotes[0], o), senCls(m.sen))),
          el("div", { style: "font-size:13px;color:var(--ink2)" }, m.sum),
          el("div", { style: "font-size:12.5px;padding-left:12px;border-left:2px solid var(--line2);display:flex;flex-direction:column;gap:4px" }, m.quotes.map(q => quoteBtn(d, q, o)))));
      });
      men.append(box);
      if (ms.length === 120) men.append(el("div", { class: "faint", style: "font-size:12px;margin-top:8px" }, "Latest 120 mentions shown. Pick a cell to narrow."));
    };
    const drawAll = () => { drawChips(); drawHeat(); drawMentions(); };
    drawAll();
  }
  function goCall(t, cid) { S.sel[t] = cid; STATE.coTab = "calls"; store.set("coTab", "calls"); }

  async function execView(body) {
    const P = await loadPeople();
    let q = "";
    const box = el("div", { class: "bd", style: "overflow-x:auto" });
    const tools = el("div", { class: "cl-tools" }, el("input", { placeholder: "Filter by name, ticker or role", oninput: e => { q = e.target.value.toLowerCase(); draw(); } }));
    body.append(panel("Executives", null, el("div", { class: "bd", style: "padding-bottom:0" }, tools), box, el("div", { class: "cl-src" }, "Executives who spoke at least 150 words on an analysed call. Trends run oldest to newest. Hedging: hedge words per 1,000 words; tone: positive minus negative finance words per 1,000 words. Click a latest value to open that executive's first passage on the call.")));
    const open = async (e, c, disp) => {
      try {
        const B = await loadCo(e.t); const call = B.calls.find(x => x.call_id === c.c); const T = await loadTurns(e.t, c.c);
        const ts = T.turns.filter(x => x.sp === c.i);
        if (!call || !ts.length) return toast("Passages could not be found");
        openQuote(B.docs[call.doc], { s: ts[0].s, e: ts[0].e, p: ts[0].p, q: null }, { eyebrow: "Executive", title: `${e.name} · ${coName(e.t)}`, display: disp, sub: `${callName(call)} · ${fdate(call.date)} · first of ${ts.length} passages shaded`, range: [ts[0].s, ts[0].e] });
      } catch { toast("Passages could not be loaded"); }
    };
    const draw = () => {
      box.innerHTML = "";
      const rows = P.executives.filter(e => !q || `${e.name} ${e.t} ${e.role || ""}`.toLowerCase().includes(q)).sort((a, b) => b.calls.length - a.calls.length || (a.t < b.t ? -1 : 1)).slice(0, 300);
      const t = el("table", { class: "t" }, el("thead", {}, el("tr", {}, el("th", {}, "Executive"), el("th", {}, "Company"), el("th", { class: "r" }, "Calls"), el("th", {}, "Talk share"), el("th", { class: "r" }, "Latest"), el("th", {}, "Hedging"), el("th", { class: "r" }, "Latest"), el("th", {}, "Tone"), el("th", { class: "r" }, "Latest"))));
      const tb = el("tbody");
      rows.forEach(e => {
        const L = e.calls.at(-1);
        tb.append(el("tr", {}, el("td", {}, el("div", { style: "font-weight:600" }, e.name), el("div", { class: "faint", style: "font-size:11.5px" }, e.role || "")),
          el("td", {}, el("a", { class: "mono", href: "#company-" + e.t.toLowerCase(), onclick: () => goCall(e.t, L.c) }, e.t)),
          el("td", { class: "r" }, String(e.calls.length)),
          el("td", { class: "cl-spark" }, sparkSVG(e.calls.map(c => c.sh), css("--c1"), 90, 22)),
          el("td", { class: "r" }, L.sh != null ? numBtn(fpct(L.sh), () => open(e, L, "talk share " + fpct(L.sh))) : "–"),
          el("td", { class: "cl-spark" }, sparkSVG(e.calls.map(c => c.h), css("--c3"), 90, 22)),
          el("td", { class: "r" }, L.h != null ? numBtn(L.h.toFixed(1), () => open(e, L, "hedging " + L.h.toFixed(1))) : "–"),
          el("td", { class: "cl-spark" }, sparkSVG(e.calls.map(c => c.tn), css("--c2"), 90, 22)),
          el("td", { class: "r" }, L.tn != null ? numBtn(fsen(L.tn), () => open(e, L, "tone " + L.tn.toFixed(1)), senCls(L.tn / 10)) : "–")));
      });
      t.append(tb); box.append(t);
      if (!rows.length) box.append(el("div", { class: "empty" }, "No executives match."));
    };
    draw();
  }

  async function analystView(body) {
    const P = await loadPeople();
    setTax((await loadThemes().catch(() => ({}))).taxonomy);
    let q = "", by = "analyst";
    const box = el("div", { class: "bd", style: "display:flex;flex-direction:column;gap:0" });
    const tools = el("div", { class: "cl-tools" }, el("input", { placeholder: "Filter by analyst, firm or ticker", oninput: e => { q = e.target.value.toLowerCase(); draw(); } }), seg([["analyst", "By analyst"], ["firm", "By firm"]], by, v => { by = v; draw(); }));
    body.append(panel("Sell-side analysts on calls", null, el("div", { class: "bd", style: "padding-bottom:0" }, tools), box, el("div", { class: "cl-src" }, "Analysts who asked questions on analysed calls, the companies they asked about and the topics of their questions (topics assigned by a language model per exchange). Open an analyst to see each question and its quote.")));
    const openQ = async (x) => {
      try {
        const B = await loadCo(x.t); const c = B.calls.find(k => k.call_id === x.c); const ex = c?.exchanges?.find(k => k.idx === x.x);
        if (!ex?.concern_quote) return toast("Quote not available for this question");
        const a = c.speakers[ex.analyst] || {};
        openQuote(B.docs[c.doc], ex.concern_quote, { eyebrow: "Analyst question", title: `${coName(x.t)} · ${callName(c)}`, sub: `${a.name || ""}${a.firm ? ", " + a.firm : ""} · ${fdate(c.date)}` });
      } catch { toast("Call data could not be loaded"); }
    };
    const chipsOf = (obj, n, fn) => el("div", { class: "cl-chips" }, Object.entries(obj || {}).slice(0, n).map(([k, v]) => el("span", { class: "chip" }, fn(k), el("span", { class: "faint" }, String(v)))));
    const draw = () => {
      box.innerHTML = "";
      const list = by === "analyst" ? P.analysts.filter(a => !q || `${a.name} ${a.firm || ""} ${Object.keys(a.covers).join(" ")}`.toLowerCase().includes(q)) : P.firms.filter(f => !q || `${f.firm} ${f.analysts.join(" ")} ${Object.keys(f.covers).join(" ")}`.toLowerCase().includes(q));
      list.slice(0, 150).forEach(a => {
        const more = el("div", { hidden: true, style: "display:flex;flex-direction:column;gap:6px;padding:8px 0 4px 12px" });
        const row = el("div", { style: "border-bottom:1px solid var(--line);padding:10px 0;display:flex;flex-direction:column;gap:6px" },
          el("div", { style: "display:flex;gap:8px;align-items:baseline;flex-wrap:wrap" }, el("b", {}, by === "analyst" ? a.name : a.firm), el("span", { class: "muted", style: "font-size:12.5px" }, by === "analyst" ? (a.firm || "") : `${a.analysts.length} analyst${a.analysts.length === 1 ? "" : "s"}`),
            el("span", { style: "flex:1" }), el("span", { class: "faint", style: "font-size:12px" }, `${a.n} question${a.n === 1 ? "" : "s"}`),
            by === "analyst" ? el("button", { class: "chip link", onclick: () => { if (!more.childElementCount) fill(); more.hidden = !more.hidden; } }, "Questions") : null),
          el("div", { style: "display:flex;gap:6px;flex-wrap:wrap;align-items:center" }, el("span", { class: "faint", style: "font-size:11.5px" }, "Covers"), ...Object.entries(a.covers).slice(0, 12).map(([t, n]) => el("a", { class: "chip mono", href: "#company-" + t.toLowerCase(), onclick: () => { STATE.coTab = "calls"; store.set("coTab", "calls"); } }, t, el("span", { class: "faint" }, String(n))))),
          Object.keys(a.topics || {}).length ? el("div", { style: "display:flex;gap:6px;flex-wrap:wrap;align-items:center" }, el("span", { class: "faint", style: "font-size:11.5px" }, "Asks about"), chipsOf(a.topics, 8, themeName)) : null,
          by === "firm" ? el("div", { class: "faint", style: "font-size:12px" }, a.analysts.slice(0, 20).join(", ")) : null,
          more);
        const fill = () => a.q.forEach(x => more.append(el("div", { style: "display:flex;gap:8px;align-items:baseline;flex-wrap:wrap;font-size:12.5px" },
          el("span", { class: "mono" }, x.t), el("span", { class: "muted" }, fdate(x.d)), x.ans ? el("span", { class: "chip " + ANS[x.ans][1] }, ANS[x.ans][0]) : null,
          x.cn ? el("button", { class: "cl-q", style: "font-style:normal", onclick: () => openQ(x) }, x.cn) : el("span", { class: "faint" }, "No concern recorded"))));
        box.append(row);
      });
      if (!list.length) box.append(el("div", { class: "empty" }, "No match."));
    };
    draw();
  }

  async function coverageView(body) {
    const C = await loadCov();
    const cs = C.companies;
    body.append(el("div", { class: "cl-tiles" },
      tile("Companies with calls", cs.length, "S&P 500 members"),
      tile("Calls stored", cs.reduce((s, c) => s + c.calls, 0), `${cs.reduce((s, c) => s + c.earnings, 0)} earnings calls`),
      tile("Analysed", cs.reduce((s, c) => s + c.analysed, 0), "with themes and quotes"),
      tile("Recent calls", cs.reduce((s, c) => s + c.recent, 0), "since " + fdate(C.recent_from))));
    const box = el("div", { class: "bd", style: "overflow-x:auto" });
    const t = el("table", { class: "t" }, el("thead", {}, el("tr", {}, el("th", {}, "Company"), el("th", {}, "Sector"), el("th", { class: "r" }, "Calls"), el("th", { class: "r" }, "Earnings"), el("th", { class: "r" }, "With Q&A"), el("th", { class: "r" }, "Analysed"), el("th", { class: "r" }, "Last 8 quarters"), el("th", {}, "First"), el("th", {}, "Latest"))));
    const tb = el("tbody");
    cs.slice().sort((a, b) => b.recent - a.recent || b.calls - a.calls).forEach(c => tb.append(el("tr", { class: "click", onclick: () => { STATE.coTab = "calls"; store.set("coTab", "calls"); location.hash = "company-" + c.t.toLowerCase(); } },
      el("td", {}, el("span", { class: "mono tkc" }, miniLogo(c.t, 16), c.t), el("span", { class: "muted", style: "margin-left:6px" }, coName(c.t))), el("td", { class: "muted" }, c.sector),
      el("td", { class: "r" }, String(c.calls)), el("td", { class: "r" }, String(c.earnings)), el("td", { class: "r" }, String(c.qa)), el("td", { class: "r" }, String(c.analysed)), el("td", { class: "r" }, String(c.recent)),
      el("td", { class: "muted" }, c.first ? fdate(c.first) : "–"), el("td", { class: "muted" }, c.last ? fdate(c.last) : "–"))));
    t.append(tb); box.append(t);
    body.append(panel("Coverage by company", null, box, el("div", { class: "cl-src" }, "Calls come from transcripts and prepared remarks stored from investor relations sites and 8-K exhibits. Companies that publish neither do not appear.")));
  }

  render.tab = tab;
  window.FU_PAGES.calls = render;
})();
