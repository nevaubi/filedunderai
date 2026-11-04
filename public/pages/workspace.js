/* Office-familiar research workspace. Globals are supplied by app.js and lib/workspace.js. */
window.FU_PAGES = window.FU_PAGES || {};
(function () {
  "use strict";
  const W = window.FU_WORKSPACE_ENGINE;
  let book,
    history,
    host,
    editor,
    grid,
    formula,
    addressInput,
    status,
    sourceButton,
    proposalHost,
    aiHost,
    chat,
    ev;
  let mode = "sheet",
    tab = "Home",
    selection = "A1",
    anchor = "A1",
    zoom = 100,
    proposal = null,
    docRange = null,
    docTimer = null,
    aiVisible = true,
    saveState = "Saved in this browser";
  const h = (tag, attrs = {}, ...kids) => {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (v == null || v === false) continue;
      if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
      else if (k === "class") n.className = v;
      else n.setAttribute(k, v === true ? "" : v);
    }
    for (const x of kids.flat())
      if (x != null && x !== false) n.append(x.nodeType ? x : String(x));
    return n;
  };
  const button = (text, fn, title = text, cls = "") =>
    h(
      "button",
      {
        type: "button",
        class: "ws-button " + cls,
        title,
        "aria-label": title,
        onclick: () => guard(fn),
      },
      text,
    );
  const guard = (fn) => {
    try {
      const r = fn();
      if (r?.catch) r.catch(notice);
    } catch (e) {
      notice(e);
    }
  };
  function notice(e) {
    const message = e?.message || String(e);
    if (typeof toast === "function") toast(message);
    else alert(message);
    const out = host?.querySelector(".ws-notice");
    if (out) {
      out.textContent = message;
      out.hidden = false;
    }
  }
  function storageRead(k, fallback) {
    try {
      return JSON.parse(localStorage.getItem(k)) || fallback;
    } catch {
      return fallback;
    }
  }
  function init() {
    if (book) return;
    const assistantPreference = storageRead("fu:workspace:assistant", null);
    aiVisible = assistantPreference === null
      ? window.innerWidth > 1100
      : assistantPreference === "visible";
    try {
      book = W.load(localStorage);
    } catch (e) {
      book = W.createWorkbook();
      saveState = "Recovery copy opened; prior saved data retained";
      notice(e);
    }
    book.document.html = sanitize(book.document.html);
    history = new W.History(book);
  }
  function sourceResolve(ticker, metric, period) {
    if (["PRICE", "MCAP", "PE"].includes(metric)) {
      const c = typeof IDX !== "undefined" ? IDX[ticker] : null;
      const v =
        c?.[{ PRICE: "price", MCAP: "market_cap", PE: "pe_ttm" }[metric]];
      if (v == null) return null;
      const source = window.FU_EVIDENCE?.capture({
        ticker,
        metric,
        value: v,
        unit: metric === "PE" ? "x" : "USD",
        period: "Latest",
        availabilityDate: new Date().toISOString().slice(0, 10),
        validation: "unverified",
        sourceUrl: "",
        notes: "Market snapshot; exact observation time unavailable",
      });
      return { value: v, source };
    }
    const cell =
      typeof findCell === "function" ? findCell(ticker, metric, period) : null;
    if (!cell || cell.v == null) return null;
    const evidence =
      typeof evidenceForCell === "function"
        ? evidenceForCell(cell)
        : window.FU_EVIDENCE?.fromCell(cell, { period });
    return { value: cell.v, source: evidence, cell };
  }
  async function preload() {
    const tickers = [
      ...new Set(
        book.sheets
          .flatMap((s) => Object.values(s.cells))
          .map((c) => String(c.value))
          .join(" ")
          .match(/(?:FU|PRICE|MCAP|PE)\(\s*"([A-Z.-]+)"/gi)
          ?.map((x) => x.match(/"([A-Z.-]+)"/i)[1].toUpperCase()) || [],
      ),
    ];
    await Promise.all(tickers.map((t) => ensureCompany(t).catch(() => null)));
  }
  function persist(label, record = true) {
    const evaluated = W.evaluate(book, sourceResolve);
    ev = evaluated;
    for (const s of book.sheets)
      for (const [a, c] of Object.entries(s.cells)) {
        const source = evaluated.meta[s.id + "!" + a]?.source;
        if (source) c.source = W.clone(source);
      }
    book.updatedAt = new Date().toISOString();
    if (record) {
      book.audit.push({ at: book.updatedAt, action: label || "Edit" });
      book.audit = book.audit.slice(-300);
      history.commit(book, label);
    }
    try {
      W.save(localStorage, book);
      localStorage.setItem(
        "fu:workspace:book:" + book.id,
        JSON.stringify(book),
      );
      const entries = storageRead("fu:workspace:index", []).filter(
        (x) => x.id !== book.id,
      );
      entries.unshift({
        id: book.id,
        title: book.title,
        updatedAt: book.updatedAt,
      });
      localStorage.setItem(
        "fu:workspace:index",
        JSON.stringify(entries.slice(0, 30)),
      );
      saveState = "Saved in this browser";
    } catch (e) {
      saveState = "Unsaved — browser storage is full";
      notice(
        "Browser could not save this change. Export a research backup to keep your work.",
      );
    }
    const n = host?.querySelector(".ws-save");
    if (n) n.textContent = saveState;
    updateStatus();
  }
  function transact(label, fn) {
    flushDoc();
    fn();
    persist(label);
    drawBody();
    drawRibbon();
  }
  function sanitize(html) {
    const t = document.createElement("template");
    t.innerHTML = String(html || "");
    const allowed = new Set([
      "P",
      "DIV",
      "H1",
      "H2",
      "H3",
      "H4",
      "H5",
      "H6",
      "SPAN",
      "B",
      "STRONG",
      "I",
      "EM",
      "U",
      "S",
      "STRIKE",
      "UL",
      "OL",
      "LI",
      "BR",
      "TABLE",
      "THEAD",
      "TBODY",
      "TR",
      "TD",
      "TH",
      "A",
      "BLOCKQUOTE",
      "SUB",
      "SUP",
    ]);
    const blocked = new Set([
      "SCRIPT",
      "STYLE",
      "IFRAME",
      "OBJECT",
      "EMBED",
      "SVG",
      "MATH",
      "FORM",
      "INPUT",
      "BUTTON",
      "VIDEO",
      "AUDIO",
      "IMG",
      "LINK",
      "META",
      "BASE",
    ]);
    function walk(parent) {
      for (const n of [...parent.childNodes]) {
        if (n.nodeType === 8) {
          n.remove();
          continue;
        }
        if (n.nodeType !== 1) continue;
        if (blocked.has(n.tagName)) {
          n.remove();
          continue;
        }
        walk(n);
        if (!allowed.has(n.tagName)) {
          n.replaceWith(...n.childNodes);
          continue;
        }
        const style = n.getAttribute("style") || "",
          href = n.getAttribute("href") || "",
          id = n.getAttribute("data-block-id"),
          eid = n.getAttribute("data-evidence-id");
        for (const a of [...n.attributes]) n.removeAttribute(a.name);
        const safe = [];
        for (const part of style.split(";")) {
          const [key, ...rest] = part.split(":");
          const val = rest.join(":").trim();
          if (
            /^(font-weight|font-style|text-decoration|text-align|font-family|font-size|color|background-color)$/i.test(
              key?.trim(),
            ) &&
            /^[\w\s#(),.%"'-]+$/.test(val) &&
            !/(url|expression|behavior)/i.test(val)
          )
            safe.push(key.trim() + ":" + val);
        }
        if (safe.length) n.setAttribute("style", safe.join(";"));
        if (n.tagName === "A" && /^https?:\/\//i.test(href)) {
          n.setAttribute("href", href);
          n.setAttribute("target", "_blank");
          n.setAttribute("rel", "noopener noreferrer");
        }
        if (id && /^[\w-]+$/.test(id)) n.dataset.blockId = id;
        if (eid && /^[\w:.-]+$/.test(eid)) n.dataset.evidenceId = eid;
      }
    }
    walk(t.content);
    for (const n of [...t.content.children])
      if (!n.dataset.blockId) n.dataset.blockId = W.uid();
    return t.innerHTML;
  }
  function evidenceRecord(id) {
    return (
      window.FU_EVIDENCE?.get(id) ||
      (Array.isArray(book?.document?.evidence)
        ? book.document.evidence
        : []
      ).find((r) => r.id === id) ||
      null
    );
  }
  function evidenceClosure(seeds) {
    const known = new Map(
      (Array.isArray(book?.document?.evidence)
        ? book.document.evidence
        : []
      ).map((r) => [r.id, r]),
    );
    for (const r of seeds || []) if (r?.id) known.set(r.id, r);
    const found = new Map();
    const visit = (r) => {
      if (!r?.id || found.has(r.id)) return;
      found.set(r.id, r);
      for (const id of r.inputEvidenceIds || [])
        visit(known.get(id) || window.FU_EVIDENCE?.get(id));
    };
    for (const r of seeds || [])
      visit(
        typeof r === "string" ? known.get(r) || window.FU_EVIDENCE?.get(r) : r,
      );
    return [...found.values()];
  }
  function evidenceElement(record, label) {
    const derived = !!record.inputEvidenceIds?.length,
      records = evidenceClosure([record]),
      urls = new Map();
    for (const r of records)
      if (/^https?:\/\//i.test(r.sourceUrl || "") && !urls.has(r.sourceUrl))
        urls.set(r.sourceUrl, r);
    const rootLabel =
      label || `${record.ticker} · ${record.metric} · ${record.period}`;
    if (!derived && urls.size)
      return h(
        "a",
        { href: [...urls.keys()][0], "data-evidence-id": record.id },
        rootLabel,
      );
    const span = h(
      "span",
      { "data-evidence-id": record.id },
      rootLabel + (derived ? " · calculated" : ""),
    );
    let index = 0;
    for (const [url, r] of urls) {
      span.append(
        " ",
        h(
          "a",
          {
            href: url,
            "data-evidence-id": record.id,
            title: `Original input source: ${r.metric || r.id}`,
          },
          `[${derived ? "input source" : "source"} ${++index}]`,
        ),
      );
    }
    if (!urls.size)
      span.append(
        derived
          ? " · input source URLs unavailable"
          : " · source URL unavailable",
      );
    if (
      (record.inputEvidenceIds || []).some(
        (id) => !records.some((r) => r.id === id),
      )
    )
      span.append(" · one or more input records are unavailable");
    return span;
  }
  function rememberEvidence(record) {
    book.document.evidence = evidenceClosure([
      ...(book.document.evidence || []),
      record,
    ]).map(W.clone);
  }
  function htmlFromMarkdown(text) {
    if (typeof md === "function") {
      const t = document.createElement("template");
      t.innerHTML = md(String(text || ""));
      for (const n of t.content.querySelectorAll(".cite")) {
        const r = n.dataset.evidenceId
          ? evidenceRecord(n.dataset.evidenceId)
          : null;
        n.replaceWith(
          r
            ? evidenceElement(r, n.textContent)
            : h("span", {}, n.textContent || "Unresolved citation"),
        );
      }
      return sanitize(t.innerHTML);
    }
    return String(text || "")
      .split("\n")
      .map((line) => {
        const m = line.match(/^(#{1,3}) (.*)/);
        const n = h(m ? "h" + m[1].length : "p", {}, m ? m[2] : line);
        return n.outerHTML;
      })
      .join("");
  }
  function sheet() {
    return W.sheet(book, book.activeSheet);
  }
  function current() {
    return W.refs(selection);
  }
  function display(v, c, meta) {
    if (v == null) return "";
    if (typeof v !== "number") return String(v);
    const f = c?.format?.number;
    if (f === "percent")
      return (
        (v * 100).toLocaleString("en-US", { maximumFractionDigits: 2 }) + "%"
      );
    if (f === "currency")
      return v.toLocaleString("en-US", {
        style: "currency",
        currency: "USD",
        maximumFractionDigits: 2,
      });
    if (f === "integer")
      return v.toLocaleString("en-US", { maximumFractionDigits: 0 });
    if (f === "multiple") return v.toFixed(2) + "×";
    if (meta?.source?.unit === "pct") return (v * 100).toFixed(2) + "%";
    return v.toLocaleString("en-US", { maximumFractionDigits: 2 });
  }
  function formatRange(format) {
    transact("Format " + selection, () => {
      for (const a of current()) {
        const c = sheet().cells[a] || { value: "" };
        sheet().cells[a] = { ...c, format: { ...c.format, ...format } };
      }
    });
  }
  function command(name, value) {
    if (!editor) return;
    editor.focus();
    if (docRange) {
      try {
        const sel = getSelection();
        sel.removeAllRanges();
        sel.addRange(docRange);
      } catch {}
    }
    document.execCommand(name, false, value);
    captureDoc("Format document");
  }
  function captureDoc(label = "Edit document") {
    if (!editor) return;
    for (const n of [...editor.children])
      if (!n.dataset.blockId) n.dataset.blockId = W.uid();
    for (const n of [...editor.querySelectorAll("font")]) {
      const span = document.createElement("span");
      if (n.face) span.style.fontFamily = n.face;
      if (n.getAttribute("size"))
        span.style.fontSize =
          ({ 1: 8, 2: 10, 3: 12, 4: 14, 5: 18, 6: 24, 7: 36 }[
            n.getAttribute("size")
          ] || 12) + "pt";
      if (n.style.fontSize) span.style.fontSize = n.style.fontSize;
      span.append(...n.childNodes);
      n.replaceWith(span);
    }
    book.document.html = sanitize(editor.innerHTML);
    persist(label);
    updateStatus();
  }
  function flushDoc() {
    if (docTimer) {
      clearTimeout(docTimer);
      docTimer = null;
      captureDoc();
    }
  }
  function selectionSave() {
    const s = getSelection();
    if (s?.rangeCount && editor?.contains(s.anchorNode)) {
      docRange = s.getRangeAt(0).cloneRange();
      updateStatus();
    }
  }
  function updateStatus() {
    if (!status) return;
    const s = sheet();
    const selected = current();
    const values = ev
      ? selected
          .map((a) => ev.value(s.id, a))
          .filter((x) => typeof x === "number")
      : [];
    const words = (
      editor?.innerText || book.document.html.replace(/<[^>]+>/g, " ")
    )
      .trim()
      .split(/\s+/)
      .filter(Boolean).length;
    status.textContent =
      mode === "doc"
        ? `${words.toLocaleString()} words · ${book.document.margin} pt margins · ${saveState}`
        : `${s.name} · ${selection} · ${selected.length} cells${values.length ? " · Sum " + display(values.reduce((a, b) => a + b, 0)) + " · Average " + display(values.reduce((a, b) => a + b, 0) / values.length) : ""} · ${saveState}`;
    const n = host?.querySelector(".ws-context");
    if (n)
      n.textContent =
        mode === "doc"
          ? `Document selection: ${getSelection()?.toString().slice(0, 160) || "No text selected"}`
          : `${s.name}!${selection} · ${selected.length} selected cell${selected.length === 1 ? "" : "s"}`;
  }
  function choose(ref, extend = false) {
    const p = W.address(ref);
    if (extend) selection = anchor + ":" + p.ref;
    else selection = anchor = p.ref;
    if (addressInput) addressInput.value = selection;
    if (formula) formula.value = sheet().cells[p.ref]?.value ?? "";
    const m = ev?.meta[sheet().id + "!" + p.ref];
    if (sourceButton) {
      sourceButton.disabled = !m?.source && !m?.cell;
      sourceButton.onclick = () => openEvidence(m);
    }
    const bounds = W.range(selection);
    grid?.querySelectorAll("[data-cell]").forEach((n) => {
      const cell = W.address(n.dataset.cell),
        on =
          cell.c >= bounds.c1 &&
          cell.c <= bounds.c2 &&
          cell.r >= bounds.r1 &&
          cell.r <= bounds.r2;
      n.classList.toggle("ws-selected", on);
      n.setAttribute("aria-selected", String(on));
      n.tabIndex = n.dataset.cell === p.ref ? 0 : -1;
    });
    updateStatus();
  }
  function openEvidence(meta) {
    if (meta?.cell && typeof openSource === "function") {
      openSource(meta.cell);
      return;
    }
    const record = meta?.source;
    if (record) {
      const cell = window.FU_EVIDENCE?.cell(record.id);
      if (cell && typeof openSource === "function") openSource(cell);
      else
        dialog(
          "Source evidence",
          h(
            "div",
            {},
            h(
              "p",
              {},
              `${record.ticker || ""} · ${record.metric || ""} · ${record.period || ""}`,
            ),
            h("pre", {}, JSON.stringify(record, null, 2)),
            record.sourceUrl
              ? h(
                  "a",
                  {
                    href: record.sourceUrl,
                    target: "_blank",
                    rel: "noopener noreferrer",
                  },
                  "Open source document",
                )
              : h("p", {}, "Source URL unavailable."),
          ),
        );
    }
  }
  function dialog(title, body) {
    const d = h("dialog", { class: "ws-dialog", "aria-label": title });
    const close = button("Close", () => d.close());
    d.append(
      h("div", { class: "ws-dialog-head" }, h("h2", {}, title), close),
      body,
    );
    document.body.append(d);
    d.addEventListener("close", () => d.remove());
    d.showModal();
    return d;
  }
  function field(label, input) {
    return h("label", { class: "ws-field" }, h("span", {}, label), input);
  }
  function selectControl(label, options, fn, value) {
    const n = h(
      "select",
      { "aria-label": label, onchange: (e) => guard(() => fn(e.target.value)) },
      options.map((o) =>
        h(
          "option",
          { value: Array.isArray(o) ? o[0] : o },
          Array.isArray(o) ? o[1] : o,
        ),
      ),
    );
    if (value != null) n.value = value;
    return n;
  }
  function group(label, ...nodes) {
    return h(
      "div",
      { class: "ws-ribbon-group" },
      h("div", { class: "ws-ribbon-actions" }, nodes),
      h("small", {}, label),
    );
  }
  function toggleAssistant() {
    setAssistantVisible(!aiVisible);
  }
  function setAssistantVisible(visible) {
    aiVisible = visible;
    try {
      localStorage.setItem(
        "fu:workspace:assistant",
        JSON.stringify(aiVisible ? "visible" : "hidden"),
      );
    } catch {
      // The visibility control remains usable when browser storage is unavailable.
    }
    host.classList.toggle("ws-ai-hidden", !aiVisible);
    host.querySelectorAll("[data-assistant-toggle]").forEach((control) => {
      control.setAttribute("aria-pressed", String(aiVisible));
    });
  }
  function drawRibbon() {
    const ribbon = host.querySelector(".ws-ribbon");
    ribbon.setAttribute("aria-label", tab + " commands");
    ribbon.replaceChildren();
    const doc = mode === "doc";
    const withSelection = (btn) => {
      btn.addEventListener("mousedown", (e) => {
        if (doc) e.preventDefault();
      });
      return btn;
    };
    const act = (text, fn, title, cls) =>
      withSelection(button(text, fn, title, cls));
    if (tab === "File") {
      ribbon.append(
        group(
          "Create",
          act("New workbook", newBook),
          act("New note", () =>
            transact("New note", () => {
              book.document = {
                ...W.createWorkbook().document,
                title: "Untitled note",
              };
              mode = "doc";
            }),
          ),
          act("Open saved", openSaved),
        ),
        group(
          "Import · originals preserved",
          act(
            "Import…",
            importFile,
            "Import XLSX, research JSON, TSV, CSV or plain-text document",
          ),
        ),
        group(
          "Export",
          act("Excel .xlsx", exportXlsx),
          act("Word .docx", exportDocx),
          act("Research backup", exportJson),
          act("Original imports", showImports),
        ),
      );
    } else if (tab === "Home") {
      ribbon.append(
        group(
          "History",
          act("↶ Undo", undo, "Undo (Ctrl/⌘ Z)"),
          act("↷ Redo", redo, "Redo (Ctrl/⌘ Shift Z)"),
        ),
        group(
          "Font",
          selectControl(
            "Font",
            ["Aptos", "Arial", "Georgia", "Times New Roman"],
            (v) => (doc ? command("fontName", v) : formatRange({ font: v })),
            doc ? book.document.font : "Arial",
          ),
          selectControl(
            "Font size",
            [10, 11, 12, 14, 16, 18, 24, 28, 36],
            (v) => (doc ? setDocFontSize(v) : formatRange({ size: +v })),
            12,
          ),
          act(
            "B",
            () =>
              doc
                ? command("bold")
                : formatRange({
                    bold: !sheet().cells[current()[0]]?.format?.bold,
                  }),
            "Bold",
            "ws-bold",
          ),
          act(
            "I",
            () =>
              doc
                ? command("italic")
                : formatRange({
                    italic: !sheet().cells[current()[0]]?.format?.italic,
                  }),
            "Italic",
            "ws-italic",
          ),
          act(
            "U",
            () =>
              doc
                ? command("underline")
                : formatRange({
                    underline: !sheet().cells[current()[0]]?.format?.underline,
                  }),
            "Underline",
            "ws-underline",
          ),
        ),
        group(
          "Alignment",
          act("Left", () =>
            doc ? command("justifyLeft") : formatRange({ align: "left" }),
          ),
          act("Center", () =>
            doc ? command("justifyCenter") : formatRange({ align: "center" }),
          ),
          act("Right", () =>
            doc ? command("justifyRight") : formatRange({ align: "right" }),
          ),
        ),
        doc
          ? group(
              "Paragraph",
              selectControl(
                "Paragraph style",
                [
                  ["p", "Normal"],
                  ["h1", "Title"],
                  ["h2", "Heading 1"],
                  ["h3", "Heading 2"],
                  ["blockquote", "Quote"],
                ],
                (v) => command("formatBlock", v),
                "p",
              ),
              act("• List", () => command("insertUnorderedList")),
              act("1. List", () => command("insertOrderedList")),
            )
          : group(
              "Number format",
              selectControl(
                "Number format",
                [
                  ["general", "General"],
                  ["number", "Number"],
                  ["currency", "Currency"],
                  ["percent", "Percent"],
                  ["integer", "Integer"],
                  ["multiple", "Multiple"],
                ],
                (v) => formatRange({ number: v }),
              ),
              act("Highlight", () => formatRange({ fill: "#fff3d9" })),
            ),
        doc
          ? group("Editing", act("Find / replace", findReplace))
          : group(
              "Editing",
              act("Fill ↓", () =>
                transact("Fill down " + selection, () =>
                  W.fill(book, book.activeSheet, selection),
                ),
              ),
              act("Fill →", () =>
                transact("Fill right " + selection, () =>
                  W.fill(book, book.activeSheet, selection, "right"),
                ),
              ),
              act("Clear", () =>
                transact("Clear " + selection, () =>
                  current().forEach((a) => delete sheet().cells[a]),
                ),
              ),
            ),
      );
    } else if (tab === "Insert") {
      ribbon.append(
        doc
          ? group(
              "Content",
              act("Table", insertTable),
              act("Heading", () => command("formatBlock", "h2")),
              act("Source citation", insertCitation),
            )
          : group(
              "Workbook",
              act("+ Sheet", addSheet),
              act("Rename sheet", renameSheet),
              act("+ 10 rows", () =>
                transact(
                  "Add rows",
                  () =>
                    (sheet().rows = Math.min(W.LIMITS.rows, sheet().rows + 10)),
                ),
              ),
              act("+ Column", () =>
                transact(
                  "Add column",
                  () =>
                    (sheet().cols = Math.min(W.LIMITS.cols, sheet().cols + 1)),
                ),
              ),
            ),
        group(
          "Research",
          act("Build model", modelDialog),
          act("Company report", reportDialog),
        ),
        group(
          "Document",
          act("Date", () =>
            doc
              ? insertDocText(new Date().toISOString().slice(0, 10))
              : transact("Insert date", () =>
                  W.put(
                    book,
                    book.activeSheet,
                    current()[0],
                    new Date().toISOString().slice(0, 10),
                  ),
                ),
          ),
        ),
      );
    } else if (tab === "Formulas") {
      ribbon.append(
        group(
          "Functions",
          act("Σ Sum", () => formulaInsert("SUM")),
          act("Average", () => formulaInsert("AVERAGE")),
          act("Filed value", insertFU),
        ),
        group(
          "Inputs",
          act("Name selection", nameSelection),
          act("Named inputs", namedInputs),
        ),
        group(
          "Calculation",
          act("Recalculate", async () => {
            await preload();
            drawBody();
            notice("Recalculated using the loaded filed-data snapshot.");
          }),
          act("Formula guide", formulaHelp),
        ),
      );
    } else if (tab === "Review") {
      const assistantToggle = act("AI assistant", toggleAssistant);
      assistantToggle.setAttribute("data-assistant-toggle", "");
      assistantToggle.setAttribute("aria-pressed", String(aiVisible));
      ribbon.append(
        group(
          "Research",
          assistantToggle,
          act("Review proposal", () => {
            if (proposal) {
              setAssistantVisible(true);
              showProposal();
              proposalHost.focus();
            } else {
              notice(
                "No pending AI proposal. Ask the assistant to propose cell or document changes.",
              );
            }
          }),
          act("Cell evidence", () => {
            const p = W.address(selection.split(":")[0]);
            const m = ev.meta[sheet().id + "!" + p.ref];
            m?.source || m?.cell
              ? openEvidence(m)
              : notice("Selected cell has no linked filing evidence.");
          }),
        ),
        group(
          "Editing",
          act("Find / replace", findReplace),
          act("Change history", showHistory),
        ),
      );
    } else if (tab === "View") {
      ribbon.append(
        group(
          "Workspace",
          act("Spreadsheet", () => switchMode("sheet")),
          act("Document", () => switchMode("doc")),
          act("Research split", () => switchMode("split")),
        ),
        group(
          "Zoom",
          act("−", () => changeZoom(-10), "Zoom out"),
          h("span", { class: "ws-zoom-label" }, zoom + "%"),
          act("+", () => changeZoom(10), "Zoom in"),
        ),
        doc
          ? group(
              "Page layout",
              selectControl(
                "Page margins",
                [
                  [36, "Narrow · 0.5 in"],
                  [72, "Normal · 1 in"],
                  [108, "Wide · 1.5 in"],
                ],
                (v) =>
                  transact("Page margins", () => (book.document.margin = +v)),
                book.document.margin,
              ),
            )
          : group(
              "Grid layout",
              act("Column width", columnWidth),
              act("Row height", rowHeight),
              act("Hide columns", () =>
                transact("Hide columns", () => {
                  const q = W.range(selection);
                  sheet().hiddenCols = [
                    ...new Set([
                      ...sheet().hiddenCols,
                      ...Array.from(
                        { length: q.c2 - q.c1 + 1 },
                        (_, i) => q.c1 + i,
                      ),
                    ]),
                  ];
                }),
              ),
              act("Show all", () =>
                transact("Show all rows and columns", () => {
                  sheet().hiddenCols = [];
                  sheet().hiddenRows = [];
                }),
              ),
            ),
      );
    }
  }
  function setDocFontSize(size) {
    if (!editor) return;
    editor.focus();
    if (docRange) {
      const sel = getSelection();
      sel.removeAllRanges();
      sel.addRange(docRange);
    }
    document.execCommand("fontSize", false, "7");
    for (const n of editor.querySelectorAll('font[size="7"]')) {
      n.removeAttribute("size");
      n.style.fontSize = size + "pt";
    }
    captureDoc("Font size");
  }
  function switchMode(next) {
    flushDoc();
    mode = next;
    drawBody();
    drawRibbon();
    host.querySelectorAll("[data-mode]").forEach((n) => {
      n.classList.toggle("active", n.dataset.mode === mode);
      n.setAttribute("aria-pressed", String(n.dataset.mode === mode));
    });
  }
  function changeZoom(delta) {
    zoom = Math.max(60, Math.min(150, zoom + delta));
    drawBody();
    drawRibbon();
    host.querySelector(".ws-zoom-value").textContent = zoom + "%";
  }
  function drawBody() {
    if (!host) return;
    ev = W.evaluate(book, sourceResolve);
    const surface = host.querySelector(".ws-surface");
    surface.className = "ws-surface ws-mode-" + mode;
    surface.replaceChildren();
    editor = null;
    grid = null;
    if (mode !== "doc") surface.append(drawSheet());
    if (mode !== "sheet") surface.append(drawDocument());
    updateStatus();
  }
  function drawSheet() {
    const s = sheet(),
      pane = h("section", {
        class: "ws-sheet-pane",
        "aria-label": "Spreadsheet editor",
      });
    addressInput = h("input", {
      class: "ws-address",
      "aria-label": "Cell or range address",
      value: selection,
      onkeydown: (e) => {
        if (e.key === "Enter")
          guard(() => {
            const next = e.target.value.toUpperCase();
            W.range(next);
            selection = next;
            chooseRange();
          });
      },
    });
    formula = h("input", {
      class: "ws-formula",
      "aria-label": "Formula or cell value",
      placeholder: 'Enter a value or =FU("AAPL","Revenue","LFY")',
      onkeydown: (e) => {
        if (e.key === "Enter")
          guard(() => commitCell(selection.split(":").pop(), formula.value));
        if (e.key === "Escape") choose(selection.split(":")[0]);
      },
    });
    sourceButton = button(
      "Source",
      () => {},
      "Inspect source for selected cell",
    );
    pane.append(
      h(
        "div",
        { class: "ws-formula-bar" },
        addressInput,
        h("span", { class: "ws-fx", "aria-hidden": "true" }, "ƒx"),
        formula,
        sourceButton,
      ),
    );
    grid = h("div", {
      class: "ws-grid-wrap",
      tabindex: "0",
      "aria-label":
        "Worksheet grid. Arrow keys move; Shift selects a range; Enter edits.",
    });
    const table = h("table", {
      class: "ws-grid",
      role: "grid",
      "aria-label": s.name,
      "aria-rowcount": s.rows,
      "aria-colcount": s.cols,
    });
    table.style.fontSize = (12 * zoom) / 100 + "px";
    const cg = h("colgroup", {}, h("col", { style: "width:42px" }));
    let tableWidth = 42;
    for (let c = 0; c < s.cols; c++) {
      if (s.hiddenCols.includes(c)) continue;
      const width = ((s.widths[c] || 118) * zoom) / 100;
      tableWidth += width;
      cg.append(h("col", { style: `width:${width}px` }));
    }
    // Fixed table layout needs a definite width; cell text must not size columns.
    table.style.width = tableWidth + "px";
    table.style.minWidth = "0";
    table.append(cg);
    const head = h(
      "tr",
      {},
      h(
        "th",
        {
          class: "ws-corner",
          "aria-label": "Select all",
          onclick: () => {
            selection = "A1:" + W.col(s.cols - 1) + s.rows;
            chooseRange();
          },
        },
        "◩",
      ),
    );
    for (let c = 0; c < s.cols; c++)
      if (!s.hiddenCols.includes(c))
        head.append(
          h(
            "th",
            {
              scope: "col",
              onclick: () => {
                selection = W.col(c) + "1:" + W.col(c) + s.rows;
                chooseRange();
              },
              ondblclick: () => {
                choose(W.col(c) + "1");
                columnWidth();
              },
            },
            W.col(c),
          ),
        );
    table.append(h("thead", {}, head));
    const body = h("tbody");
    const q = W.range(selection);
    for (let r = 0; r < s.rows; r++) {
      if (s.hiddenRows.includes(r)) continue;
      const row = h(
        "tr",
        { style: s.heights[r] ? `height:${s.heights[r]}px` : "" },
        h(
          "th",
          {
            scope: "row",
            onclick: () => {
              selection = "A" + (r + 1) + ":" + W.col(s.cols - 1) + (r + 1);
              chooseRange();
            },
          },
          r + 1,
        ),
      );
      for (let c = 0; c < s.cols; c++) {
        if (s.hiddenCols.includes(c)) continue;
        const a = W.col(c) + (r + 1),
          cell = s.cells[a],
          v = ev.value(s.id, a),
          meta = ev.meta[s.id + "!" + a],
          f = cell?.format || {};
        const on = c >= q.c1 && c <= q.c2 && r >= q.r1 && r <= q.r2;
        const td = h(
          "td",
          {
            "data-cell": a,
            role: "gridcell",
            tabindex: a === selection.split(":")[0] ? "0" : "-1",
            "aria-selected": String(on),
            class: [
              on ? "ws-selected" : "",
              typeof v === "number" ? "ws-numeric" : "",
              meta?.error ? "ws-error" : "",
              meta?.source ? "ws-linked" : "",
            ].join(" "),
            title:
              meta?.error ||
              (String(cell?.value || "").startsWith("=")
                ? cell.value
                : typeof v === "string" ? v : ""),
          },
          display(v, cell, meta),
        );
        Object.assign(td.style, {
          fontWeight: f.bold ? "700" : "",
          fontStyle: f.italic ? "italic" : "",
          textDecoration: f.underline ? "underline" : "",
          textAlign: f.align || "",
          backgroundColor: f.fill || "",
          fontFamily: f.font || "",
          fontSize: f.size ? (f.size * zoom) / 100 + "px" : "",
        });
        td.addEventListener("mousedown", (e) => {
          if (e.button !== 0) return;
          choose(a, e.shiftKey);
        });
        td.addEventListener("mouseenter", (e) => {
          if (e.buttons === 1) choose(a, true);
        });
        td.addEventListener("dblclick", () => editCell(a, td));
        row.append(td);
      }
      body.append(row);
    }
    table.append(body);
    grid.append(table);
    if (!Object.keys(s.cells).length)
      grid.append(
        h(
          "div",
          { class: "ws-grid-empty" },
          h("span", { class: "ws-empty-eyebrow" }, "A NEW RESEARCH WORKBOOK"),
          h("h2", {}, "Start with filed evidence."),
          h(
            "p",
            {},
            "Create a linked financial model, write a source-backed note, or bring in your own data.",
          ),
          h(
            "div",
            {},
            button(
              "Build financial model",
              modelDialog,
              "Build a source-linked operating model",
              "ws-primary",
            ),
            button("Write a report", reportDialog),
            button("Import data", importFile),
          ),
          h("small", {}, "Or select any cell and start typing."),
        ),
      );
    grid.addEventListener("keydown", gridKeys);
    grid.addEventListener("copy", copyCells);
    grid.addEventListener("paste", pasteCells);
    pane.append(grid);
    const tabs = h(
      "div",
      { class: "ws-sheet-tabs" },
      button("+", addSheet, "Add worksheet"),
    );
    for (const sh of book.sheets)
      tabs.append(
        button(
          sh.name,
          () => {
            flushDoc();
            book.activeSheet = sh.id;
            selection = anchor = "A1";
            persist("Switch worksheet", false);
            drawBody();
          },
          "Open " + sh.name,
          sh.id === s.id ? "active" : "",
        ),
      );
    tabs.append(
      h(
        "span",
        { class: "ws-sheet-hint" },
        "Double-click a cell to edit · Shift + arrow to select",
      ),
    );
    pane.append(tabs);
    queueMicrotask(() => chooseRange());
    return pane;
  }
  function chooseRange() {
    const q = W.range(selection),
      last = W.col(q.c2) + (q.r2 + 1),
      saved = selection;
    choose(last);
    selection = saved;
    anchor = W.col(q.c1) + (q.r1 + 1);
    if (addressInput) addressInput.value = selection;
    const bounds = W.range(selection);
    grid?.querySelectorAll("[data-cell]").forEach((n) => {
      const cell = W.address(n.dataset.cell);
      n.classList.toggle(
        "ws-selected",
        cell.c >= bounds.c1 &&
          cell.c <= bounds.c2 &&
          cell.r >= bounds.r1 &&
          cell.r <= bounds.r2,
      );
    });
    updateStatus();
  }
  async function commitCell(a, value) {
    W.put(book, book.activeSheet, a, value);
    book.audit.push({
      at: new Date().toISOString(),
      action: "Cell " + sheet().name + "!" + a,
      value,
    });
    persist("Edit " + a);
    await preload();
    drawBody();
    choose(a);
    grid?.querySelector(`[data-cell="${a}"]`)?.focus();
  }
  function editCell(a, td, initial) {
    const input = h("input", {
      "aria-label": "Edit " + a,
      value: initial ?? sheet().cells[a]?.value ?? "",
    });
    td.replaceChildren(input);
    input.focus();
    if (initial == null) input.select();
    let done = false;
    const commit = () => {
      if (done) return;
      done = true;
      guard(() => commitCell(a, input.value));
    };
    input.addEventListener("keydown", (e) => {
      e.stopPropagation();
      if (e.key === "Enter" || e.key === "Tab") {
        e.preventDefault();
        commit();
      }
      if (e.key === "Escape") {
        done = true;
        drawBody();
      }
    });
    input.addEventListener("blur", commit);
  }
  function gridKeys(e) {
    if (e.target.tagName === "INPUT") return;
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "a") {
      e.preventDefault();
      selection = "A1:" + W.col(sheet().cols - 1) + sheet().rows;
      chooseRange();
      return;
    }
    const p = W.address(selection.split(":").pop());
    const moves = {
      ArrowUp: [-1, 0],
      ArrowDown: [1, 0],
      ArrowLeft: [0, -1],
      ArrowRight: [0, 1],
      Tab: [0, e.shiftKey ? -1 : 1],
    };
    if (moves[e.key]) {
      e.preventDefault();
      const [dr, dc] = moves[e.key];
      const a =
        W.col(Math.max(0, Math.min(sheet().cols - 1, p.c + dc))) +
        (Math.max(0, Math.min(sheet().rows - 1, p.r + dr)) + 1);
      choose(a, e.shiftKey && e.key !== "Tab");
      grid.querySelector(`[data-cell="${a}"]`)?.focus();
    } else if (e.key === "Enter" || e.key === "F2") {
      e.preventDefault();
      editCell(p.ref, grid.querySelector(`[data-cell="${p.ref}"]`));
    } else if (e.key === "Delete" || e.key === "Backspace") {
      e.preventDefault();
      transact("Clear selection", () =>
        current().forEach((a) => delete sheet().cells[a]),
      );
    } else if (!e.ctrlKey && !e.metaKey && !e.altKey && e.key.length === 1) {
      e.preventDefault();
      editCell(p.ref, grid.querySelector(`[data-cell="${p.ref}"]`), e.key);
    }
  }
  function copyCells(e) {
    if (e.target.tagName === "INPUT") return;
    const q = W.range(selection),
      rows = [];
    for (let r = q.r1; r <= q.r2; r++) {
      const row = [];
      for (let c = q.c1; c <= q.c2; c++)
        row.push(sheet().cells[W.col(c) + (r + 1)]?.value ?? "");
      rows.push(row.join("\t"));
    }
    e.clipboardData.setData("text/plain", rows.join("\n"));
    e.preventDefault();
  }
  function pasteCells(e) {
    if (e.target.tagName === "INPUT") return;
    e.preventDefault();
    guard(() =>
      transact("Paste range", () => {
        selection = W.paste(
          book,
          book.activeSheet,
          selection.split(":")[0],
          e.clipboardData.getData("text/plain"),
        );
      }),
    );
  }
  function drawDocument() {
    const pane = h("section", {
      class: "ws-document-pane",
      "aria-label": "Document editor",
    });
    const ruler = h(
      "div",
      { class: "ws-ruler", "aria-hidden": "true" },
      ...Array.from({ length: 9 }, (_, i) => h("span", {}, i ? String(i) : "")),
    );
    const canvas = h("div", { class: "ws-document-canvas" });
    editor = h("div", {
      class: "ws-document",
      contenteditable: "true",
      role: "textbox",
      "aria-label": "Research document",
      "aria-multiline": "true",
      spellcheck: "true",
    });
    editor.innerHTML = sanitize(book.document.html);
    editor.style.setProperty("--ws-margin", book.document.margin + "pt");
    editor.style.fontFamily = book.document.font;
    editor.style.fontSize = book.document.size + "pt";
    editor.style.zoom = zoom / 100;
    editor.addEventListener("input", () => {
      clearTimeout(docTimer);
      docTimer = setTimeout(() => {
        docTimer = null;
        captureDoc();
      }, 600);
    });
    editor.addEventListener("keyup", selectionSave);
    editor.addEventListener("mouseup", selectionSave);
    editor.addEventListener("blur", () => {
      selectionSave();
      flushDoc();
    });
    editor.addEventListener("paste", (e) => {
      e.preventDefault();
      const text = e.clipboardData.getData("text/plain");
      document.execCommand("insertText", false, text);
      captureDoc("Paste text");
    });
    canvas.append(editor);
    pane.append(ruler, canvas);
    return pane;
  }
  function insertDocText(text) {
    if (!editor) {
      switchMode("doc");
    }
    editor.focus();
    if (docRange) {
      const s = getSelection();
      s.removeAllRanges();
      s.addRange(docRange);
    }
    document.execCommand("insertText", false, text);
    captureDoc("Insert text");
  }
  function insertTable() {
    const rows = +prompt("Rows (1–30)", "3"),
      cols = +prompt("Columns (1–12)", "3");
    if (!rows || !cols) return;
    if (rows > 30 || cols > 12 || rows < 1 || cols < 1)
      throw Error("Table size must be 1–30 rows and 1–12 columns");
    editor.focus();
    const t = h(
      "table",
      {},
      h(
        "tbody",
        {},
        ...Array.from({ length: rows }, () =>
          h(
            "tr",
            {},
            ...Array.from({ length: cols }, () => h("td", {}, h("br"))),
          ),
        ),
      ),
    );
    document.execCommand("insertHTML", false, t.outerHTML + "<p><br></p>");
    captureDoc("Insert table");
  }
  function insertCitation() {
    const id = prompt("Evidence ID (from a source record)");
    if (!id) return;
    const record = evidenceRecord(id);
    if (!record) throw Error("Evidence ID could not be resolved");
    const link = evidenceElement(record);
    editor.focus();
    if (docRange) {
      const selection = getSelection();
      selection.removeAllRanges();
      selection.addRange(docRange);
    }
    document.execCommand("insertHTML", false, link.outerHTML);
    rememberEvidence(record);
    captureDoc("Insert source citation");
  }

  function addSheet() {
    const name = prompt("Worksheet name", `Sheet${book.sheets.length + 1}`);
    if (!name) return;
    if (
      name.length > 31 ||
      /[\[\]:*?\/\\]/.test(name) ||
      book.sheets.some((s) => s.name.toLowerCase() === name.toLowerCase())
    )
      throw Error(
        "Use a unique sheet name of 31 characters or fewer, without [] : * ? / \\",
      );
    transact("Add worksheet", () => {
      const s = W.createSheet(name);
      book.sheets.push(s);
      book.activeSheet = s.id;
      selection = anchor = "A1";
    });
  }
  function renameSheet() {
    const s = sheet(),
      name = prompt("Worksheet name", s.name);
    if (!name || name === s.name) return;
    if (
      name.length > 31 ||
      /[\[\]:*?\/\\]/.test(name) ||
      book.sheets.some(
        (sh) => sh !== s && sh.name.toLowerCase() === name.toLowerCase(),
      )
    )
      throw Error("Invalid or duplicate worksheet name");
    transact("Rename sheet", () => {
      const old = s.name;
      for (const sh of book.sheets)
        for (const c of Object.values(sh.cells))
          if (typeof c.value === "string" && c.value.startsWith("="))
            c.value = c.value
              .split("'" + old + "'!")
              .join("'" + name.replace(/'/g, "''") + "'!")
              .replace(
                new RegExp(
                  "\\b" + old.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "!",
                  "g",
                ),
                "'" + name.replace(/'/g, "''") + "'!",
              );
      s.name = name;
    });
  }
  function columnWidth() {
    const q = W.range(selection),
      v = +prompt(
        "Column width in pixels (50–600)",
        sheet().widths[q.c1] || 118,
      );
    if (!v) return;
    if (v < 50 || v > 600) throw Error("Column width must be 50–600 px");
    transact("Resize columns", () => {
      for (let c = q.c1; c <= q.c2; c++) sheet().widths[c] = v;
    });
  }
  function rowHeight() {
    const q = W.range(selection),
      v = +prompt("Row height in pixels (24–200)", sheet().heights[q.r1] || 28);
    if (!v) return;
    if (v < 24 || v > 200) throw Error("Row height must be 24–200 px");
    transact("Resize rows", () => {
      for (let r = q.r1; r <= q.r2; r++) sheet().heights[r] = v;
    });
  }
  function formulaInsert(fn) {
    const target = prompt(
      "Result cell",
      W.col(W.range(selection).c1) + (W.range(selection).r2 + 2),
    );
    if (!target) return;
    commitCell(target, `=${fn}(${selection})`);
  }
  function insertFU() {
    const ticker = prompt("Ticker", "AAPL");
    if (!ticker) return;
    const metric = prompt("Metric label", "Revenue");
    if (!metric) return;
    const period = prompt("Period: LFY, LQ, TTM, FY2025 or Q2 FY26", "LFY");
    if (!period) return;
    commitCell(
      selection.split(":")[0],
      `=FU(${JSON.stringify(ticker.toUpperCase())},${JSON.stringify(metric)},${JSON.stringify(period)})`,
    );
  }
  function nameSelection() {
    const name = prompt(
      "Input name (letters, digits, underscores; start with a letter)",
    );
    if (!name) return;
    if (
      !/^[A-Za-z_][A-Za-z0-9_]*$/.test(name) ||
      /^[A-Za-z]{1,3}[0-9]+$/.test(name)
    )
      throw Error("Use a valid name that is not a cell address");
    transact(
      "Define input " + name,
      () =>
        (book.names[name] = {
          sheet: book.activeSheet,
          address: selection.split(":")[0],
        }),
    );
  }
  function namedInputs() {
    dialog(
      "Named model inputs",
      h(
        "div",
        { class: "ws-input-list" },
        ...Object.entries(book.names).map(([n, v]) =>
          h(
            "div",
            {},
            h("strong", {}, n),
            h("span", {}, W.sheet(book, v.sheet).name + "!" + v.address),
            button("Go to", () => {
              book.activeSheet = v.sheet;
              selection = anchor = v.address;
              drawBody();
            }),
          ),
        ),
      ),
    );
  }
  function formulaHelp() {
    dialog(
      "Workbook formulas",
      h(
        "div",
        {},
        h(
          "p",
          {},
          "Supported: arithmetic + − × / ^, parentheses, percentages, cross-sheet references, named inputs, SUM, AVERAGE / AVG, MIN, MAX, COUNT, ABS, SQRT, ROUND, FU, PRICE, MCAP, PE.",
        ),
        h(
          "pre",
          {},
          '=FU("AAPL","Revenue","LFY")\n=SUM(B5:E5)\n=Model!D5*(1+Growth)\n=\'Other sheet\'!A1',
        ),
        h(
          "p",
          {},
          "Missing filed data propagates #N/A. Circular references show #CYCLE!. Unsupported functions show #NAME?. FU and market functions are exported as source-linked snapshots; standard formulas remain editable in Excel.",
        ),
      ),
    );
  }
  function findReplace() {
    flushDoc();
    const find = h("input", { "aria-label": "Find text" }),
      replacement = h("input", { "aria-label": "Replacement text" }),
      result = h("p", { role: "status" });
    const d = dialog(
      "Find and replace",
      h(
        "div",
        {},
        field("Find", find),
        field("Replace with", replacement),
        button("Replace all", () => {
          if (!find.value) return;
          let count = 0;
          transact("Replace text", () => {
            if (mode === "doc") {
              const t = document.createElement("template");
              t.innerHTML = sanitize(book.document.html);
              const walker = document.createTreeWalker(
                t.content,
                NodeFilter.SHOW_TEXT,
              );
              const nodes = [];
              while (walker.nextNode()) nodes.push(walker.currentNode);
              for (const n of nodes) {
                const parts = n.textContent.split(find.value);
                count += parts.length - 1;
                n.textContent = parts.join(replacement.value);
              }
              book.document.html = sanitize(t.innerHTML);
            } else
              for (const c of Object.values(sheet().cells)) {
                if (typeof c.value === "string" && !c.value.startsWith("=")) {
                  const parts = c.value.split(find.value);
                  count += parts.length - 1;
                  c.value = parts.join(replacement.value);
                }
              }
          });
          result.textContent = `Replaced ${count} occurrence${count === 1 ? "" : "s"}.`;
        }),
        h(
          "small",
          {},
          "Literal, case-sensitive text. Spreadsheet formulas are excluded.",
        ),
        result,
      ),
    );
    find.focus();
  }
  function undo() {
    flushDoc();
    book = history.undo();
    persist("Undo", false);
    drawBody();
    drawRibbon();
  }
  function redo() {
    flushDoc();
    book = history.redo();
    persist("Redo", false);
    drawBody();
    drawRibbon();
  }
  function showHistory() {
    dialog(
      "Change history",
      h(
        "div",
        { class: "ws-history" },
        ...book.audit
          .slice()
          .reverse()
          .slice(0, 100)
          .map((a) =>
            h(
              "div",
              {},
              h("time", {}, new Date(a.at).toLocaleString()),
              h("strong", {}, a.action),
              a.value != null ? h("code", {}, String(a.value)) : null,
            ),
          ),
      ),
    );
  }
  function newBook() {
    flushDoc();
    persist("Save before new workbook", false);
    book = W.createWorkbook();
    history = new W.History(book);
    selection = anchor = "A1";
    proposal = null;
    persist("New workbook");
    render();
  }
  function openSaved() {
    const entries = storageRead("fu:workspace:index", []);
    const d = dialog(
      "Open research workspace",
      h(
        "div",
        { class: "ws-saved" },
        ...entries.map((x) =>
          button(
            x.title + " · " + new Date(x.updatedAt).toLocaleDateString(),
            () => {
              const saved = localStorage.getItem("fu:workspace:book:" + x.id);
              if (!saved) throw Error("Saved workbook is unavailable");
              flushDoc();
              persist("Save before open", false);
              book = W.validateBook(JSON.parse(saved));
              book.document.html = sanitize(book.document.html);
              history = new W.History(book);
              selection = anchor = "A1";
              proposal = null;
              persist("Open workbook", false);
              d.close();
              render();
            },
          ),
        ),
      ),
    );
  }
  async function companyLoading(ticker) {
    const n = host?.isConnected ? host.querySelector(".ws-notice") : null;
    if (n) {
      n.hidden = false;
      n.textContent = "Loading filed data for " + ticker + "…";
      host.setAttribute("aria-busy", "true");
    }
    try {
      return await ensureCompany(ticker);
    } finally {
      if (n) {
        n.hidden = true;
        host.removeAttribute("aria-busy");
      }
    }
  }
  function modelDialog() {
    const ticker = prompt("Build a filed financial model for ticker", "AAPL");
    if (ticker) openModel(ticker);
  }
  function reportDialog() {
    const ticker = prompt(
      "Create a source-linked research note for ticker",
      "AAPL",
    );
    if (ticker) openReport(ticker);
  }
  async function openModel(ticker, options = {}) {
    init();
    ticker = String(ticker || "").toUpperCase();
    await companyLoading(ticker);
    flushDoc();
    persist("Save before building model", false);
    const periods = model(ticker)
      .periods("A")
      .slice(-3)
      .map((e) => plabel(ticker, e, "A"));
    book = W.buildModel({
      ticker,
      periods,
      resolve: sourceResolve,
      title: options.title,
    });
    history = new W.History(book);
    mode = "sheet";
    selection = anchor = "A1";
    proposal = null;
    persist("Create " + ticker + " financial model", false);
    await navigate();
    return W.clone(book);
  }
  async function openReport(ticker, options = {}) {
    init();
    ticker = String(ticker || "").toUpperCase();
    await companyLoading(ticker);
    flushDoc();
    persist("Save before creating report", false);
    book = W.createWorkbook(options.title || ticker + " • Research report");
    book.document.title = options.title || ticker + " research report";
    const evidence = (options.evidence || [])
      .map((x) => (typeof x === "string" ? window.FU_EVIDENCE?.get(x) : x))
      .filter(Boolean);
    book.document.evidence = evidenceClosure(evidence).map(W.clone);
    let html;
    if (options.markdown) html = htmlFromMarkdown(options.markdown);
    else {
      const container = h(
        "div",
        {},
        h("h1", {}, book.document.title),
        h("p", {}, "Research draft · " + new Date().toISOString().slice(0, 10)),
        h("h2", {}, "Investment question"),
        h(
          "p",
          {},
          "State the question, time horizon, and conditions that would change your view.",
        ),
        h("h2", {}, "Filed evidence"),
      );
      for (const metric of [
        "Revenue",
        "Operating income",
        "Net income",
        "Free cash flow",
      ]) {
        const fact = sourceResolve(ticker, metric, "LFY");
        if (!fact) continue;
        const r = fact.source;
        if (r) evidence.push(r);
        container.append(
          h(
            "p",
            {},
            metric +
              ": " +
              display(fact.value, { format: { number: "currency" } }) +
              " · latest filed fiscal year ",
            r
              ? evidenceElement(r, "Source")
              : h("span", {}, "Source record unavailable"),
          ),
        );
      }
      for (const section of options.sections || [])
        container.append(
          h("h2", {}, section.heading || section.title || "Analysis"),
          h("p", {}, section.text || section.content || ""),
        );
      container.append(
        h("h2", {}, "Thesis and risks"),
        h(
          "p",
          {},
          "Add your interpretation. Keep reported facts separate from assumptions.",
        ),
        h("h2", {}, "What to monitor"),
        h(
          "ul",
          {},
          h(
            "li",
            {},
            "Define measurable checks and evidence needed to resolve the question.",
          ),
        ),
      );
      html = container.innerHTML;
    }
    book.document.html = sanitize(html);
    book.document.evidence = evidenceClosure(evidence).map(W.clone);
    book.document.claims = W.clone(options.claims || []);
    history = new W.History(book);
    mode = "doc";
    selection = anchor = "A1";
    proposal = null;
    persist("Create " + ticker + " report", false);
    await navigate();
    return W.clone(book.document);
  }
  async function navigate() {
    if (location.hash !== "#workbench") {
      location.hash = "workbench";
      await new Promise((r) => setTimeout(r, 0));
    } else if (document.querySelector(".ws-workspace")) render();
  }
  function stageProposal(p) {
    init();
    flushDoc();
    const staged = W.stage(book, p);
    for (const c of staged.cells) {
      if (c.source) {
        const record = window.FU_EVIDENCE?.get(
          c.source.id || c.source.evidenceId,
        );
        if (!record)
          throw Error(
            "Proposed source does not resolve to an existing evidence record",
          );
        c.source = W.clone(record);
      }
    }
    for (const block of staged.blocks) {
      if (!["string", "undefined"].includes(typeof block.text))
        throw Error("Document patch text must be plain text");
      if (block.id) {
        const t = document.createElement("template");
        t.innerHTML = sanitize(book.document.html);
        if (
          ![...t.content.children].some((n) => n.dataset.blockId === block.id)
        )
          throw Error("Unknown document block ID: " + block.id);
      }
    }
    proposal = staged;
    if (host?.isConnected) showProposal();
    return W.clone(proposal);
  }
  function appendCitationText(node, text) {
    let last = 0;
    for (const m of String(text).matchAll(/\[\[E:([^\]]+)\]\]/g)) {
      node.append(text.slice(last, m.index));
      const r = evidenceRecord(m[1]);
      if (r) {
        node.append(evidenceElement(r));
        rememberEvidence(r);
      } else node.append("[Unresolved evidence: " + m[1] + "]");
      last = m.index + m[0].length;
    }
    node.append(text.slice(last));
  }

  function showProposal() {
    if (!proposalHost || !proposal) return;
    proposalHost.replaceChildren();
    proposalHost.hidden = false;
    const p = proposal;
    proposalHost.append(
      h(
        "div",
        { class: "ws-proposal-title" },
        h("strong", {}, p.summary),
        h("span", { class: "ws-pill" }, p.status),
      ),
      h(
        "p",
        {},
        "Review each change. Your workbook and document stay editable until you apply.",
      ),
    );
    for (const c of p.cells)
      proposalHost.append(
        h(
          "div",
          { class: "ws-patch" },
          h("strong", {}, W.sheet(book, c.sheet).name + "!" + c.address),
          h("del", {}, String(c.before?.value ?? "(blank)")),
          h("ins", {}, String(c.value ?? "(blank)")),
        ),
      );
    const t = document.createElement("template");
    t.innerHTML = sanitize(book.document.html);
    for (const block of p.blocks) {
      const before = [...t.content.children].find(
        (n) => n.dataset.blockId === block.id,
      );
      proposalHost.append(
        h(
          "div",
          { class: "ws-patch" },
          h(
            "strong",
            {},
            before ? "Replace document block" : "Append document block",
          ),
          h("del", {}, before?.textContent || "(new block)"),
          h("ins", {}, block.text || ""),
        ),
      );
    }
    if (p.status === "pending")
      proposalHost.append(
        h(
          "div",
          { class: "ws-proposal-buttons" },
          button(
            "Apply changes",
            () => {
              flushDoc();
              W.applyProposal(book, p);
              const t = document.createElement("template");
              t.innerHTML = sanitize(book.document.html);
              for (const block of p.blocks) {
                const node = h(
                  ["h1", "h2", "h3", "p", "blockquote"].includes(block.type)
                    ? block.type
                    : "p",
                  { "data-block-id": block.id || W.uid() },
                  "",
                );
                appendCitationText(node, block.text || "");
                const old = [...t.content.children].find(
                  (n) => n.dataset.blockId === block.id,
                );
                if (old) old.replaceWith(node);
                else t.content.append(node);
              }
              if (p.blocks.length) book.document.html = sanitize(t.innerHTML);
              persist("Applied AI proposal: " + p.summary);
              drawBody();
              showProposal();
            },
            "Apply reviewed changes",
            "ws-primary",
          ),
          button("Reject", () => {
            p.status = "rejected";
            showProposal();
          }),
        ),
      );
  }
  function contextSnapshot(options = {}) {
    flushDoc();
    const s = sheet(),
      selected = current(),
      docs = documentBlocks();
    const limit = {
      sheets: 4,
      cells: 60,
      selection: 40,
      blocks: 20,
      text: 500,
      documentText: 1200,
      names: 40,
    };
    const offset = {
      sheet: Math.max(0, +options.sheetOffset || 0),
      cell: Math.max(0, +options.cellOffset || 0),
      selection: Math.max(0, +options.selectionOffset || 0),
      document: Math.max(0, +options.documentOffset || 0),
      name: Math.max(0, +options.nameOffset || 0),
    };
    const clip = (v) => (typeof v === "string" ? v.slice(0, limit.text) : v);
    const cell = (sh, address, c) => {
      const value = ev?.value(sh.id, address);
      return {
        address,
        raw: clip(c?.value ?? null),
        value: clip(value),
        textTruncated:
          (typeof c?.value === "string" && c.value.length > limit.text) ||
          (typeof value === "string" && value.length > limit.text),
        evidence: c?.source?.id || ev?.meta[sh.id + "!" + address]?.source?.id,
      };
    };
    const names = Object.entries(book.names),
      sheets = book.sheets
        .slice(offset.sheet, offset.sheet + limit.sheets)
        .map((sh) => {
          const entries = Object.entries(sh.cells),
            next = offset.cell + limit.cells;
          return {
            id: sh.id,
            name: sh.name,
            totalCells: entries.length,
            truncated: entries.length > next,
            nextOffset: entries.length > next ? next : null,
            cells: entries
              .slice(offset.cell, next)
              .map(([a, c]) => cell(sh, a, c)),
          };
        });
    const next = {
      sheet:
        book.sheets.length > offset.sheet + limit.sheets
          ? offset.sheet + limit.sheets
          : null,
      selection:
        selected.length > offset.selection + limit.selection
          ? offset.selection + limit.selection
          : null,
      document:
        docs.length > offset.document + limit.blocks
          ? offset.document + limit.blocks
          : null,
      name:
        names.length > offset.name + limit.names
          ? offset.name + limit.names
          : null,
    };
    return {
      title: book.title.slice(0, 150),
      limits: limit,
      offset,
      truncated:
        Object.values(next).some((v) => v !== null) ||
        sheets.some((s) => s.truncated),
      nextOffset: next,
      selection: {
        sheet: s.name,
        range: selection,
        totalCells: selected.length,
        cells: selected
          .slice(offset.selection, offset.selection + limit.selection)
          .map((a) => cell(s, a, s.cells[a])),
      },
      sheets,
      document: docs
        .slice(offset.document, offset.document + limit.blocks)
        .map((d) => ({
          ...d,
          text: d.text.slice(0, limit.documentText),
          textTruncated: d.text.length > limit.documentText,
        })),
      names: Object.fromEntries(
        names.slice(offset.name, offset.name + limit.names),
      ),
    };
  }
  function documentBlocks() {
    const t = document.createElement("template");
    t.innerHTML = sanitize(book.document.html);
    return [...t.content.children].map((n) => ({
      id: n.dataset.blockId,
      type: n.tagName.toLowerCase(),
      text: n.textContent,
    }));
  }
  function mountAI() {
    if (!aiHost || chat) return;
    if (typeof chatPanel !== "function") {
      aiHost.append(
        h(
          "p",
          {},
          "The assistant is unavailable in this environment. Your workbook and document editors remain available.",
        ),
      );
      return;
    }
    chat = chatPanel(aiHost, {
      id: "workspace-v1",
      intro:
        "Read filed evidence, build a model, or draft a research note. Proposed edits appear above for your review.",
      placeholder: "Ask about your selection, model or document…",
      suggestions: [
        "Review the selected cells for formula or source gaps",
        "Propose a concise thesis paragraph based on filed evidence",
      ],
      rules:
        'You are a careful research editor. Use read_workspace before editing. Never claim edits were applied: propose_workspace_changes stages patches for user review. Preserve other blocks. Never fabricate figures, consensus, forecasts, or sources. Forecasts must be explicit editable assumptions. Formula errors and missing data are visible. Supported formulas: FU("TICKER","Metric","LFY"), SUM, AVERAGE, MIN, MAX, COUNT, ABS, SQRT, ROUND, arithmetic, sheet references and named inputs. Source citations must refer to evidence records returned by tools. Document patch text is plain text; use block ID to replace only a specific block or omit ID to append.',
      context: () => JSON.stringify(contextSnapshot()),
      tools: (trace) => [
        {
          name: "read_workspace",
          description:
            "Read selected cells, workbook formulas, named inputs, and document blocks with stable IDs.",
          inputSchema: {
            type: "object",
            properties: {
              sheetOffset: { type: "number" },
              cellOffset: { type: "number" },
              selectionOffset: { type: "number" },
              documentOffset: { type: "number" },
              nameOffset: { type: "number" },
            },
          },
          execute: async (options) => contextSnapshot(options),
        },
        {
          name: "propose_workspace_changes",
          description:
            "Stage cell and document block edits for preview. Does not apply edits. Use raw formulas or strings for cell values; plain text for document blocks.",
          inputSchema: {
            type: "object",
            properties: {
              summary: { type: "string" },
              cells: {
                type: "array",
                items: {
                  type: "object",
                  properties: {
                    sheet: { type: "string" },
                    address: { type: "string" },
                    value: { type: ["string", "number"] },
                    source: { type: "object" },
                  },
                  required: ["address", "value"],
                },
              },
              blocks: {
                type: "array",
                items: {
                  type: "object",
                  properties: {
                    id: { type: "string" },
                    type: {
                      type: "string",
                      enum: ["p", "h1", "h2", "h3", "blockquote"],
                    },
                    text: { type: "string" },
                  },
                  required: ["text"],
                },
              },
            },
            required: ["summary"],
          },
          execute: async (p) => {
            const st = trace("propose_workspace_changes", p);
            try {
              const result = stageProposal(p);
              st.done(result);
              return {
                status: "pending_review",
                id: result.id,
                cellChanges: result.cells.length,
                blockChanges: result.blocks.length,
              };
            } catch (e) {
              st.fail(e);
              throw e;
            }
          },
        },
      ],
    });
  }
  function download(data, name, type) {
    const blob = data instanceof Blob ? data : new Blob([data], { type });
    const url = URL.createObjectURL(blob),
      a = h("a", { href: url, download: name });
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 3000);
  }
  const filename = (ext) =>
    book.title
      .replace(/[^a-zA-Z0-9_. -]/g, "")
      .trim()
      .replace(/ +/g, "-")
      .slice(0, 90) +
    "." +
    ext;
  function exportJson() {
    flushDoc();
    download(
      JSON.stringify(book, null, 2),
      filename("fu.json"),
      "application/json",
    );
  }
  async function exportXlsx() {
    flushDoc();
    if (!window.ExcelJS)
      throw Error(
        "Excel export dependency is unavailable. Use Research backup to preserve formulas and evidence.",
      );
    await preload();
    ev = W.evaluate(book, sourceResolve);
    const x = new ExcelJS.Workbook();
    x.creator = "Filed Under";
    x.created = new Date();
    x.calcProperties.fullCalcOnLoad = true;
    const provenance = [
      [
        "Sheet",
        "Cell",
        "Original formula / input",
        "Value",
        "Evidence ID",
        "Ticker",
        "Metric",
        "Period",
        "Source URL",
        "Filing accession",
        "Filed date",
        "Validation",
      ],
    ];
    const numfmt = {
      number: "#,##0.00;[Red](#,##0.00)",
      integer: "#,##0",
      percent: "0.0%",
      currency: "$#,##0.00;[Red]($#,##0.00)",
      multiple: '0.00"x"',
    };
    for (const s of book.sheets) {
      const sh = x.addWorksheet(s.name, {
        views: [{ state: "frozen", xSplit: 1, ySplit: 3 }],
      });
      for (let c = 0; c < s.cols; c++) {
        sh.getColumn(c + 1).width = (s.widths[c] || 118) / 7;
        sh.getColumn(c + 1).hidden = s.hiddenCols.includes(c);
      }
      for (let r = 0; r < s.rows; r++) {
        if (s.heights[r]) sh.getRow(r + 1).height = s.heights[r] * 0.75;
        sh.getRow(r + 1).hidden = s.hiddenRows.includes(r);
      }
      for (const [address, cell] of Object.entries(s.cells)) {
        const out = sh.getCell(address),
          v = ev.value(s.id, address),
          m = ev.meta[s.id + "!" + address],
          raw = cell.value;
        let formula =
          typeof raw === "string" && raw.startsWith("=") ? raw.slice(1) : null;
        if (formula) {
          formula = formula.replace(
            /\b(FU|PRICE|MCAP|PE)\(\s*"([^"]+)"(?:\s*,\s*"([^"]+)"\s*,\s*"([^"]+)")?\s*\)/gi,
            (_, fn, t, metric, period) => {
              const fact = sourceResolve(
                t.toUpperCase(),
                fn.toUpperCase() === "FU" ? metric : fn.toUpperCase(),
                period || "Latest",
              );
              return fact?.value == null ? "NA()" : String(fact.value);
            },
          );
          if (m?.error)
            out.value = {
              error: [
                "#N/A",
                "#DIV/0!",
                "#VALUE!",
                "#NAME?",
                "#NUM!",
                "#REF!",
              ].includes(v)
                ? v
                : "#REF!",
            };
          else out.value = { formula, result: v ?? undefined };
        } else out.value = typeof raw === "number" ? raw : (v ?? raw);
        const f = cell.format || {};
        out.font = {
          name: f.font || "Aptos",
          size: f.size || 11,
          bold: !!f.bold,
          italic: !!f.italic,
          underline: !!f.underline,
          color: { argb: "FF18283E" },
        };
        out.alignment = {
          horizontal: f.align || (typeof v === "number" ? "right" : "left"),
          vertical: "middle",
        };
        if (numfmt[f.number]) out.numFmt = numfmt[f.number];
        if (f.fill)
          out.fill = {
            type: "pattern",
            pattern: "solid",
            fgColor: { argb: "FF" + f.fill.replace("#", "") },
          };
        const source = m?.source || cell.source;
        out.note = [
          formula ? "Workspace formula: " + raw : "",
          m?.error ? "Calculation: " + m.error : "",
          source
            ? "Evidence: " + source.id + "\n" + (source.sourceUrl || "")
            : "",
        ]
          .filter(Boolean)
          .join("\n");
        if (source || formula)
          provenance.push([
            s.name,
            address,
            String(raw),
            typeof v === "number" ? v : String(v ?? ""),
            source?.id || "",
            source?.ticker || "",
            source?.metric || "",
            source?.period || "",
            source?.sourceUrl || "",
            source?.accession || "",
            source?.filedDate || "",
            (typeof source?.validation === "object"
              ? source.validation.status
              : source?.validation) || "",
          ]);
      }
    }
    for (const [name, n] of Object.entries(book.names)) {
      const s = W.sheet(book, n.sheet),
        p = W.address(n.address);
      x.definedNames.add(
        "'" + s.name.replace(/'/g, "''") + "'!$" + W.col(p.c) + "$" + (p.r + 1),
        name,
      );
    }
    const unique = (base) => {
      let n = base,
        i = 2;
      while (x.getWorksheet(n)) n = base + " " + i++;
      return n;
    };
    const src = x.addWorksheet(unique("Cell evidence"));
    provenance.forEach((row, r) =>
      row.forEach((v, c) => (src.getCell(r + 1, c + 1).value = v)),
    );
    src.getRow(1).font = { bold: true };
    src.columns.forEach((c) => (c.width = 24));
    src.getColumn(9).width = 65;
    const hist = x.addWorksheet(unique("Change history"));
    ["Timestamp", "Change", "Value"].forEach(
      (v, c) => (hist.getCell(1, c + 1).value = v),
    );
    book.audit.forEach((a, r) =>
      [a.at, a.action, a.value == null ? "" : String(a.value)].forEach(
        (v, c) => (hist.getCell(r + 2, c + 1).value = v),
      ),
    );
    hist.columns.forEach((c) => (c.width = 42));
    download(
      await x.xlsx.writeBuffer(),
      filename("xlsx"),
      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    );
  }
  function citationRecords() {
    const seeds = [...(book.document.evidence || [])];
    const t = document.createElement("template");
    t.innerHTML = sanitize(book.document.html);
    for (const link of t.content.querySelectorAll("[data-evidence-id]")) {
      const r = evidenceRecord(link.dataset.evidenceId);
      if (r) seeds.push(r);
    }
    return evidenceClosure(seeds);
  }
  async function exportDocx() {
    flushDoc();
    if (!window.docx)
      throw Error(
        "Word export dependency is unavailable. Use Research backup to preserve your note.",
      );
    const d = window.docx,
      t = document.createElement("template");
    t.innerHTML = sanitize(book.document.html);
    function runs(node, inherited = {}) {
      const out = [];
      for (const n of node.childNodes) {
        if (n.nodeType === 3) {
          out.push(new d.TextRun({ text: n.textContent, ...inherited }));
          continue;
        }
        if (n.nodeType !== 1) continue;
        const props = {
          ...inherited,
          bold: inherited.bold || /^(B|STRONG)$/.test(n.tagName),
          italics: inherited.italics || /^(I|EM)$/.test(n.tagName),
          underline: /^(U)$/.test(n.tagName) ? {} : inherited.underline,
        };
        if (n.style.fontFamily) props.font = n.style.fontFamily;
        if (n.style.fontSize)
          props.size = Math.round(parseFloat(n.style.fontSize) * 2);
        if (n.tagName === "BR") {
          out.push(new d.TextRun({ break: 1 }));
          continue;
        }
        if (n.tagName === "A" && n.href)
          out.push(
            new d.ExternalHyperlink({
              link: n.href,
              children: runs(n, { ...props, style: "Hyperlink" }),
            }),
          );
        else out.push(...runs(n, props));
      }
      return out;
    }
    function para(n, extra = {}) {
      const level = {
        H1: d.HeadingLevel.TITLE,
        H2: d.HeadingLevel.HEADING_1,
        H3: d.HeadingLevel.HEADING_2,
        H4: d.HeadingLevel.HEADING_3,
      }[n.tagName];
      return new d.Paragraph({
        children: runs(n),
        heading: level,
        spacing: { after: 160, line: 280 },
        alignment: {
          left: d.AlignmentType.LEFT,
          center: d.AlignmentType.CENTER,
          right: d.AlignmentType.RIGHT,
          justify: d.AlignmentType.JUSTIFIED,
        }[n.style.textAlign],
        ...extra,
      });
    }
    const children = [];
    function blocks(nodes) {
      for (const n of nodes) {
        if (n.nodeType !== 1) {
          if (n.textContent.trim())
            children.push(new d.Paragraph(n.textContent));
          continue;
        }
        if (n.tagName === "TABLE") {
          children.push(
            new d.Table({
              width: { size: 100, type: d.WidthType.PERCENTAGE },
              rows: [...n.querySelectorAll("tr")].map(
                (row) =>
                  new d.TableRow({
                    children: [...row.children].map(
                      (cell) => new d.TableCell({ children: [para(cell)] }),
                    ),
                  }),
              ),
            }),
          );
        } else if (n.tagName === "UL" || n.tagName === "OL") {
          [...n.children].forEach((li) =>
            children.push(
              para(
                li,
                n.tagName === "UL"
                  ? { bullet: { level: 0 } }
                  : { numbering: { reference: "list", level: 0 } },
              ),
            ),
          );
        } else if (
          n.tagName === "DIV" &&
          n.querySelector("p,h1,h2,h3,table,ul,ol")
        )
          blocks(n.childNodes);
        else children.push(para(n));
      }
    }
    blocks(t.content.childNodes);
    const evidence = citationRecords();
    if (evidence.length) {
      children.push(
        new d.Paragraph({ text: "Sources", heading: d.HeadingLevel.HEADING_1 }),
      );
      for (const r of evidence) {
        const runs = [
          new d.TextRun(
            `${r.ticker || ""} · ${r.metric || ""} · ${r.period || ""} · ${r.id}. Value: ${r.value ?? "Unavailable"} ${r.unit || ""}. `,
          ),
        ];
        if (r.derivation)
          runs.push(new d.TextRun(`Calculation: ${r.derivation}. `));
        if (r.inputEvidenceIds?.length)
          runs.push(
            new d.TextRun(`Input evidence: ${r.inputEvidenceIds.join(", ")}. `),
          );
        if (/^https?:\/\//i.test(r.sourceUrl || ""))
          runs.push(
            new d.ExternalHyperlink({
              link: r.sourceUrl,
              children: [
                new d.TextRun({ text: r.sourceUrl, style: "Hyperlink" }),
              ],
            }),
          );
        else
          runs.push(
            new d.TextRun(
              r.inputEvidenceIds?.length
                ? "Calculated from the original input records listed in this appendix."
                : "Source URL unavailable.",
            ),
          );
        children.push(
          new d.Paragraph({ children: runs, spacing: { after: 140 } }),
        );
      }
    }
    const margin = book.document.margin * 20;
    const doc = new d.Document({
      creator: "Filed Under",
      title: book.document.title,
      styles: {
        default: {
          document: {
            run: { font: book.document.font, size: book.document.size * 2 },
          },
        },
      },
      numbering: {
        config: [
          {
            reference: "list",
            levels: [
              {
                level: 0,
                format: d.LevelFormat.DECIMAL,
                text: "%1.",
                alignment: d.AlignmentType.LEFT,
                style: { paragraph: { indent: { left: 720, hanging: 260 } } },
              },
            ],
          },
        ],
      },
      sections: [
        {
          properties: {
            page: {
              size: { width: 12240, height: 15840 },
              margin: {
                top: margin,
                right: margin,
                bottom: margin,
                left: margin,
              },
            },
          },
          children,
        },
      ],
    });
    download(await d.Packer.toBlob(doc), filename("docx"));
  }
  function csvParse(text, delimiter = ",") {
    const rows = [[]];
    let word = "",
      quoted = false;
    for (let i = 0; i < text.length; i++) {
      const c = text[i];
      if (c === '"') {
        if (quoted && text[i + 1] === '"') {
          word += '"';
          i++;
        } else quoted = !quoted;
      } else if (c === delimiter && !quoted) {
        rows.at(-1).push(word);
        word = "";
      } else if ((c === "\n" || c === "\r") && !quoted) {
        rows.at(-1).push(word);
        word = "";
        if (c === "\r" && text[i + 1] === "\n") i++;
        rows.push([]);
      } else word += c;
    }
    if (quoted) throw Error("CSV contains an unclosed quoted field");
    rows.at(-1).push(word);
    if (rows.at(-1).length === 1 && rows.at(-1)[0] === "") rows.pop();
    return rows;
  }
  function importFile() {
    const input = h("input", {
      type: "file",
      accept: ".xlsx,.json,.tsv,.csv,.txt,.md",
    });
    input.addEventListener("change", () =>
      guard(async () => {
        const file = input.files[0];
        if (!file) return;
        if (file.size > 2 * 1024 * 1024)
          throw Error(
            "Import limit is 2 MB; larger files can exceed browser storage.",
          );
        const original = await new Promise((resolve, reject) => {
          const r = new FileReader();
          r.onload = () => resolve(r.result);
          r.onerror = reject;
          r.readAsDataURL(file);
        });
        const ext = file.name.split(".").pop().toLowerCase();
        let next = W.clone(book);
        if (ext === "json")
          next = W.validateBook(JSON.parse(await file.text()));
        else if (ext === "xlsx") {
          if (!window.ExcelJS) throw Error("Excel importer is unavailable.");
          const x = new ExcelJS.Workbook();
          await x.xlsx.load(await file.arrayBuffer());
          next = W.createWorkbook(file.name.replace(/\.xlsx$/i, ""));
          next.sheets = [];
          for (const sh of x.worksheets.slice(0, 30)) {
            const s = W.createSheet(sh.name);
            next.sheets.push(s);
            sh.eachRow((row, r) => {
              row.eachCell((cell, c) => {
                if (r > W.LIMITS.rows || c > W.LIMITS.cols)
                  throw Error(
                    "Imported sheet exceeds 2,000 rows or 100 columns",
                  );
                let v = cell.value;
                if (v == null) return;
                if (typeof v === "object") {
                  if (v.formula) v = "=" + v.formula;
                  else if (v.richText)
                    v = v.richText.map((t) => t.text).join("");
                  else if (v.text) v = v.text;
                  else if (v.error) v = v.error;
                  else if (v instanceof Date) v = v.toISOString().slice(0, 10);
                  else
                    throw Error(
                      "Unsupported Excel cell type at " + cell.address,
                    );
                }
                W.put(next, s.id, cell.address, v, {
                  bold: !!cell.font?.bold,
                  italic: !!cell.font?.italic,
                  number: cell.numFmt?.includes("%") ? "percent" : "general",
                });
              });
            });
          }
          if (!next.sheets.length) throw Error("No readable worksheets");
          next.activeSheet = next.sheets[0].id;
          notice(
            "Imported values, formulas and basic formats. Charts, macros, pivots, merged cells and external links are not imported. Unsupported formulas show #NAME?. Original file retained.",
          );
        } else if (ext === "txt" || ext === "md") {
          next.document.html =
            ext === "md"
              ? htmlFromMarkdown(await file.text())
              : h("p", {}, await file.text()).outerHTML;
          mode = "doc";
        } else if (ext === "csv" || ext === "tsv") {
          const rows = csvParse(await file.text(), ext === "tsv" ? "\t" : ",");
          const s = W.createSheet("Import " + (next.sheets.length + 1));
          next.sheets.push(s);
          next.activeSheet = s.id;
          rows.forEach((row, r) =>
            row.forEach((v, c) => W.put(next, s.id, W.col(c) + (r + 1), v)),
          );
          mode = "sheet";
        } else
          throw Error(
            "Supported imports: XLSX, research JSON, CSV, TSV, TXT and Markdown. DOCX import is not supported.",
          );
        next.document.html = sanitize(next.document.html);
        next.imports = (next.imports || []).concat([
          {
            name: file.name,
            type: file.type,
            at: new Date().toISOString(),
            data: original,
          },
        ]);
        const serialized = JSON.stringify(next);
        try {
          localStorage.setItem("fu:workspace:import-check", serialized);
          localStorage.removeItem("fu:workspace:import-check");
        } catch {
          throw Error(
            "Not enough browser storage to preserve this original. Export current work and free storage before importing.",
          );
        }
        flushDoc();
        persist("Save before import", false);
        book = next;
        history = new W.History(book);
        selection = anchor = "A1";
        persist("Import " + file.name);
        await preload();
        render();
      }),
    );
    input.click();
  }
  function showImports() {
    dialog(
      "Original imported files",
      h(
        "div",
        { class: "ws-saved" },
        ...(book.imports || []).map((f) =>
          button(f.name + " · " + new Date(f.at).toLocaleDateString(), () => {
            const a = h("a", { href: f.data, download: f.name });
            a.click();
          }),
        ),
        !(book.imports || []).length
          ? h("p", {}, "No files have been imported into this workbook.")
          : null,
      ),
    );
  }
  async function render() {
    init();
    if (!page || location.hash.split("-")[0] !== "#workbench") return;
    page.replaceChildren();
    host = h("section", {
      class: "ws-workspace" + (!aiVisible ? " ws-ai-hidden" : ""),
      "aria-label": "Research workspace",
    });
    chat = null;
    const title = h("input", {
      class: "ws-title",
      "aria-label": "Workspace title",
      value: book.title,
      onchange: (e) => {
        book.title = e.target.value.trim() || "Untitled research";
        persist("Rename workspace");
      },
    });
    host.append(
      h(
        "div",
        { class: "ws-titlebar" },
        h("div", { class: "ws-title-info" }, title),
        h("span", { class: "ws-save", role: "status" }, saveState),
        h(
          "button",
          {
            class: "ws-button ws-assistant-toggle",
            type: "button",
            "data-assistant-toggle": "",
            "aria-pressed": String(aiVisible),
            "aria-label": "Research assistant",
            onclick: toggleAssistant,
          },
          "Assistant",
        ),
        button(
          "Build model",
          modelDialog,
          "Create a filed-data financial model",
          "ws-primary",
        ),
      ),
    );
    const viewbar = h(
      "div",
      { class: "ws-viewbar" },
      h(
        "div",
        { class: "ws-mode-switch", "aria-label": "Editor mode" },
        ...[
          ["sheet", "Spreadsheet"],
          ["doc", "Document"],
          ["split", "Research split"],
        ].map(([id, label]) =>
          h(
            "button",
            {
              "data-mode": id,
              type: "button",
              class: id === mode ? "active" : "",
              "aria-pressed": String(id === mode),
              onclick: () => switchMode(id),
            },
            label,
          ),
        ),
      ),
    );
    const tabs = h("div", {
      class: "ws-ribbon-tabs",
      role: "tablist",
      "aria-label": "Ribbon",
    });
    for (const name of ["File", "Home", "Insert", "Formulas", "Review", "View"])
      tabs.append(
        h(
          "button",
          {
            type: "button",
            role: "tab",
            "aria-selected": String(tab === name),
            class: tab === name ? "active" : "",
            onclick: () => {
              selectionSave();
              tab = name;
              tabs.querySelectorAll("button").forEach((n) => {
                n.classList.toggle("active", n.textContent === name);
                n.setAttribute("aria-selected", String(n.textContent === name));
              });
              drawRibbon();
            },
          },
          name,
        ),
      );
    viewbar.append(tabs);
    host.append(
      viewbar,
      h("div", {
        class: "ws-ribbon",
        role: "toolbar",
        "aria-label": tab + " commands",
      }),
      h("div", { class: "ws-notice", role: "status", hidden: true }),
    );
    const body = h("div", { class: "ws-body" }),
      main = h(
        "div",
        { class: "ws-main-editor" },
        h("div", { class: "ws-surface" }),
      );
    const side = h(
      "aside",
      { class: "ws-ai-sidebar", "aria-label": "Research assistant" },
      h(
        "div",
        { class: "ws-ai-heading" },
        h("strong", {}, "Research assistant"),
        button("Hide", toggleAssistant, "Hide research assistant"),
      ),
      h("div", { class: "ws-context" }),
    );
    proposalHost = h("div", {
      class: "ws-proposal",
      hidden: true,
      tabindex: "-1",
      "aria-label": "Proposed workspace changes",
    });
    aiHost = h("div", { class: "ws-chat" });
    side.append(proposalHost, aiHost);
    body.append(main, side);
    host.append(body);
    status = h("span", { class: "ws-status-text", role: "status" });
    host.append(
      h(
        "div",
        { class: "ws-statusbar" },
        h("span", { class: "ws-ready" }, "● Ready"),
        status,
        h(
          "div",
          { class: "ws-status-zoom" },
          button("−", () => changeZoom(-10), "Zoom out"),
          h("span", { class: "ws-zoom-value" }, zoom + "%"),
          button("+", () => changeZoom(10), "Zoom in"),
        ),
      ),
    );
    page.append(host);
    drawRibbon();
    drawBody();
    mountAI();
    if (proposal) showProposal();
    await preload();
    if (host?.isConnected) {
      ev = W.evaluate(book, sourceResolve);
      if (mode !== "doc") drawBody();
    }
    host.addEventListener("keydown", (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z") {
        e.preventDefault();
        e.shiftKey ? redo() : undo();
      }
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
        e.preventDefault();
        flushDoc();
        persist("Save", false);
      }
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "f") {
        e.preventDefault();
        findReplace();
      }
    });
  }
  window.addEventListener("beforeunload", flushDoc);
  window.FU_PAGES.workbench = render;
  window.FU_WORKSPACE = {
    openModel,
    openReport,
    stageProposal,
    getWorkbook: () => {
      init();
      flushDoc();
      return W.clone(book);
    },
    getSelection: () => ({
      sheet: book?.activeSheet,
      range: selection,
      documentText: editor && getSelection()?.toString(),
    }),
    exportXlsx,
    exportDocx,
    sanitizeHTML: sanitize,
  };
})();
