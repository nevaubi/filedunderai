/* Filed Under workspace engine. No eval, network, or DOM required for workbook operations. */
(function (root) {
  "use strict";
  const clone = (x) => JSON.parse(JSON.stringify(x)),
    uid = () =>
      `w${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`;
  const LIMITS = { rows: 2000, cols: 100, cells: 50000 };
  const col = (i) => {
    let s = "";
    for (i++; i; i = Math.floor((i - 1) / 26))
      s = String.fromCharCode(65 + ((i - 1) % 26)) + s;
    return s;
  };
  function address(a) {
    const m = String(a)
      .toUpperCase()
      .match(/^\$?([A-Z]{1,3})\$?([1-9]\d*)$/);
    if (!m) throw Error("Invalid cell address: " + a);
    let c = 0;
    for (const x of m[1]) c = c * 26 + x.charCodeAt(0) - 64;
    const r = +m[2];
    if (c > LIMITS.cols || r > LIMITS.rows)
      throw Error("Cell exceeds workspace range limit");
    return { c: c - 1, r: r - 1, ref: col(c - 1) + r };
  }
  function range(s) {
    const a = String(s).split(":");
    if (a.length > 2) throw Error("Invalid range");
    const x = address(a[0]),
      y = address(a[1] || a[0]);
    return {
      c1: Math.min(x.c, y.c),
      c2: Math.max(x.c, y.c),
      r1: Math.min(x.r, y.r),
      r2: Math.max(x.r, y.r),
    };
  }
  function refs(s) {
    const q = range(s),
      out = [];
    if ((q.c2 - q.c1 + 1) * (q.r2 - q.r1 + 1) > LIMITS.cells)
      throw Error("Selection exceeds cell limit");
    for (let r = q.r1; r <= q.r2; r++)
      for (let c = q.c1; c <= q.c2; c++) out.push(col(c) + (r + 1));
    return out;
  }
  function createSheet(name = "Sheet1") {
    return {
      id: uid(),
      name,
      cells: {},
      rows: 40,
      cols: 14,
      widths: {},
      heights: {},
      hiddenRows: [],
      hiddenCols: [],
    };
  }
  function createWorkbook(title = "Untitled research") {
    const s = createSheet();
    return {
      version: 1,
      id: uid(),
      title,
      sheets: [s],
      activeSheet: s.id,
      names: {},
      document: {
        title: "Research note",
        html: "<h1>Research note</h1><p>Begin with a question. Link filed evidence, record assumptions, and develop your view.</p>",
        margin: 72,
        font: "Georgia",
        size: 12,
      },
      audit: [],
      imports: [],
      updatedAt: new Date().toISOString(),
    };
  }
  function sheet(b, id) {
    const s = b.sheets.find((s) => s.id === id || s.name === id);
    if (!s) throw Error("Unknown sheet: " + id);
    return s;
  }
  function put(b, id, a, value, format, source) {
    const s = sheet(b, id),
      p = address(a),
      old = s.cells[p.ref] || {};
    s.rows = Math.max(s.rows, p.r + 1);
    s.cols = Math.max(s.cols, p.c + 1);
    s.cells[p.ref] = {
      ...old,
      value,
      ...(format ? { format: { ...old.format, ...format } } : {}),
      ...(source ? { source: clone(source) } : {}),
    };
    if (!source && old.value !== value) delete s.cells[p.ref].source;
    if (value === "" && !format) delete s.cells[p.ref];
  }
  function paste(b, id, start, text) {
    const p = address(start),
      rows = String(text)
        .replace(/\r\n?/g, "\n")
        .replace(/\n$/, "")
        .split("\n")
        .map((x) => x.split("\t"));
    if (rows.length * Math.max(...rows.map((r) => r.length)) > LIMITS.cells)
      throw Error("Paste exceeds cell limit");
    address(
      col(p.c + Math.max(...rows.map((r) => r.length)) - 1) +
        (p.r + rows.length),
    );
    rows.forEach((row, i) =>
      row.forEach((v, j) => put(b, id, col(p.c + j) + (p.r + i + 1), v)),
    );
    return `${p.ref}:${col(p.c + Math.max(...rows.map((r) => r.length)) - 1)}${p.r + rows.length}`;
  }
  function translate(formula, dr, dc = 0) {
    if (typeof formula !== "string" || !formula.startsWith("=")) return formula;
    return formula
      .split(/("(?:[^"]|"")*")/g)
      .map((chunk, i) =>
        i % 2
          ? chunk
          : chunk.replace(
              /(\$?)([A-Z]{1,3})(\$?)([1-9]\d*)\b/g,
              (m, ac, c, ar, r) => {
                const p = address(c + r);
                const nr = p.r + (ar ? 0 : dr),
                  nc = p.c + (ac ? 0 : dc);
                if (nr < 0 || nc < 0 || nr >= LIMITS.rows || nc >= LIMITS.cols)
                  return "#REF!";
                return ac + col(nc) + ar + (nr + 1);
              },
            ),
      )
      .join("");
  }
  function fill(b, id, selection, direction = "down") {
    const s = sheet(b, id),
      q = range(selection);
    for (let r = q.r1; r <= q.r2; r++)
      for (let c = q.c1; c <= q.c2; c++) {
        const from =
          direction === "right" ? col(q.c1) + (r + 1) : col(c) + (q.r1 + 1);
        const to = col(c) + (r + 1);
        s.rows = Math.max(s.rows, q.r2 + 1);
        s.cols = Math.max(s.cols, q.c2 + 1);
        const cell = s.cells[from];
        if (cell)
          s.cells[to] = {
            ...clone(cell),
            value: translate(
              cell.value,
              direction === "down" ? r - q.r1 : 0,
              direction === "right" ? c - q.c1 : 0,
            ),
          };
        else delete s.cells[to];
      }
  }
  class CalcError extends Error {
    constructor(code, message) {
      super(message);
      this.code = code;
    }
  }
  const fail = (code, msg) => {
    throw new CalcError(code, msg);
  };
  function evaluate(book, resolve = () => null) {
    const cache = new Map(),
      meta = {},
      active = new Set();
    const number = (v) => {
      if (v === null || v === undefined || v === "")
        fail("#N/A", "Missing input; enter an explicit assumption");
      if (typeof v !== "number" || !Number.isFinite(v))
        fail("#VALUE!", "Expected a finite number");
      return v;
    };
    function value(id, a) {
      const s = sheet(book, id),
        p = address(a),
        key = s.id + "!" + p.ref;
      if (cache.has(key)) return cache.get(key);
      if (active.has(key))
        fail("#CYCLE!", "Circular reference at " + s.name + "!" + p.ref);
      active.add(key);
      let v = null;
      const raw = s.cells[p.ref]?.value;
      try {
        if (typeof raw === "string" && raw.startsWith("="))
          v = parse(raw.slice(1), s, key);
        else if (raw == null || raw === "") v = null;
        else
          v =
            typeof raw === "number"
              ? raw
              : /^[+-]?(?:\d+\.?\d*|\.\d+)(?:e[+-]?\d+)?$/i.test(raw)
                ? Number(raw)
                : raw;
        if (typeof v === "number" && !Number.isFinite(v))
          fail("#NUM!", "Nonfinite result");
      } catch (e) {
        v = e.code || "#ERROR!";
        meta[key] = { ...(meta[key] || {}), error: e.message, code: v };
      } finally {
        active.delete(key);
      }
      cache.set(key, v);
      return v;
    }
    function read(s, a) {
      const v = value(s.id, a);
      const m = meta[s.id + "!" + address(a).ref];
      if (m?.error) fail(m.code, m.error);
      return v;
    }
    function parse(src, s, self) {
      let i = 0;
      const ws = () => {
        while (/\s/.test(src[i] || "") && i < src.length) i++;
      };
      const expr = () => {
        let v = term();
        ws();
        while (src[i] === "+" || src[i] === "-") {
          const op = src[i++],
            r = term();
          v = op === "+" ? number(v) + number(r) : number(v) - number(r);
          ws();
        }
        return v;
      };
      const term = () => {
        let v = power();
        ws();
        while (src[i] === "*" || src[i] === "/") {
          const op = src[i++],
            r = number(power());
          if (op === "/" && r === 0) fail("#DIV/0!", "Division by zero");
          v = op === "*" ? number(v) * r : number(v) / r;
          ws();
        }
        return v;
      };
      const power = () => {
        let v = unary();
        ws();
        if (src[i] === "^") {
          i++;
          v = Math.pow(number(v), number(power()));
        }
        return v;
      };
      const unary = () => {
        ws();
        if (src[i] === "+" || src[i] === "-") {
          const op = src[i++];
          return number(unary()) * (op === "-" ? -1 : 1);
        }
        return atom();
      };
      function atom() {
        ws();
        if (src[i] === "(") {
          i++;
          const v = expr();
          ws();
          if (src[i++] !== ")") fail("#ERROR!", "Missing closing parenthesis");
          return v;
        }
        if (src[i] === '"') {
          i++;
          let v = "";
          while (i < src.length) {
            if (src[i] === '"') {
              if (src[i + 1] === '"') {
                v += '"';
                i += 2;
              } else {
                i++;
                return v;
              }
            } else v += src[i++];
          }
          fail("#ERROR!", "Unclosed string");
        }
        let m = src.slice(i).match(/^(?:\d+\.?\d*|\.\d+)(?:e[+-]?\d+)?%?/i);
        if (m) {
          i += m[0].length;
          return m[0].endsWith("%") ? parseFloat(m[0]) / 100 : Number(m[0]);
        }
        m = src.slice(i).match(/^([A-Za-z_][\w.]*)\s*\(/);
        if (m) {
          i += m[0].length;
          const args = [];
          ws();
          if (src[i] !== ")") {
            for (;;) {
              args.push(expr());
              ws();
              if (src[i] === ",") {
                i++;
                continue;
              }
              break;
            }
          }
          if (src[i++] !== ")") fail("#ERROR!", "Missing closing parenthesis");
          return call(m[1].toUpperCase(), args);
        }
        m = src
          .slice(i)
          .match(
            /^(?:(?:'((?:[^']|'')+)'|([A-Za-z_][\w ]*))!)?(\$?[A-Za-z]{1,3}\$?[1-9]\d*)(?::(\$?[A-Za-z]{1,3}\$?[1-9]\d*))?/,
          );
        if (m) {
          i += m[0].length;
          const target =
            m[1] || m[2] ? sheet(book, (m[1] || m[2]).replace(/''/g, "'")) : s;
          if (m[4]) return refs(m[3] + ":" + m[4]).map((a) => read(target, a));
          return read(target, m[3]);
        }
        m = src.slice(i).match(/^[A-Za-z_][\w.]*/);
        if (m) {
          i += m[0].length;
          const name = Object.keys(book.names || {}).find(
            (k) => k.toLowerCase() === m[0].toLowerCase(),
          );
          if (!name) fail("#NAME?", "Unknown name: " + m[0]);
          const n = book.names[name];
          return read(sheet(book, n.sheet), n.address);
        }
        fail("#ERROR!", "Unexpected input at character " + (i + 1));
      }
      function call(fn, args) {
        if (["SUM", "AVG", "AVERAGE", "MIN", "MAX", "COUNT"].includes(fn)) {
          const raw = args.flat();
          if (fn === "COUNT")
            return raw.filter((x) => typeof x === "number").length;
          const nums = raw.filter(
            (x) => x !== null && x !== "" && typeof x === "number",
          );
          if (!nums.length) fail("#N/A", "No numeric inputs");
          if (fn === "SUM") return nums.reduce((a, b) => a + b, 0);
          if (fn === "MIN") return Math.min(...nums);
          if (fn === "MAX") return Math.max(...nums);
          return nums.reduce((a, b) => a + b, 0) / nums.length;
        }
        if (fn === "SQRT") {
          const v = number(args[0]);
          if (v < 0)
            fail(
              "#NUM!",
              "Square root requires a nonnegative input; discount rate must exceed terminal growth",
            );
          return Math.sqrt(v);
        }
        if (fn === "ABS") return Math.abs(number(args[0]));
        if (fn === "ROUND") {
          const p = 10 ** (args[1] ?? 0);
          return Math.round(number(args[0]) * p) / p;
        }
        if (["FU", "PRICE", "MCAP", "PE"].includes(fn)) {
          const out = resolve(
            String(args[0]).toUpperCase(),
            fn === "FU" ? String(args[1]) : fn,
            fn === "FU" ? String(args[2]) : "Latest",
          );
          const v = out?.value ?? out?.v;
          if (v == null || typeof v !== "number" || !Number.isFinite(v))
            fail("#N/A", `No available ${fn} data for ${args.join(" · ")}`);
          meta[self] = {
            ...(meta[self] || {}),
            source: out.source || out.evidence || null,
            cell: out.cell || null,
          };
          return v;
        }
        fail("#NAME?", "Unsupported function: " + fn);
      }
      const v = expr();
      ws();
      if (i !== src.length)
        fail("#ERROR!", "Unexpected input at character " + (i + 1));
      if (Array.isArray(v))
        fail("#VALUE!", "Range must be used inside an aggregate function");
      return v;
    }
    for (const s of book.sheets)
      for (const a of Object.keys(s.cells)) value(s.id, a);
    return { value, meta, cache };
  }
  class History {
    constructor(book) {
      this.states = [clone(book)];
      this.labels = ["Opened"];
      this.cursor = 0;
    }
    commit(book, label = "Edit") {
      const next = clone(book);
      if (JSON.stringify(next) === JSON.stringify(this.states[this.cursor]))
        return;
      this.states.splice(this.cursor + 1);
      this.labels.splice(this.cursor + 1);
      this.states.push(next);
      this.labels.push(label);
      if (this.states.length > 60) {
        this.states.shift();
        this.labels.shift();
      }
      this.cursor = this.states.length - 1;
    }
    undo() {
      if (this.cursor > 0) this.cursor--;
      return clone(this.states[this.cursor]);
    }
    redo() {
      if (this.cursor < this.states.length - 1) this.cursor++;
      return clone(this.states[this.cursor]);
    }
  }
  function stage(book, proposal) {
    if (!proposal || !Array.isArray(proposal.cells || []))
      throw Error("Expected structured cell patches");
    if ((proposal.cells || []).length > 500)
      throw Error("Maximum 500 proposed cells");
    const cells = (proposal.cells || []).map((p) => {
      const s = sheet(book, p.sheet || book.activeSheet),
        a = address(p.address || p.ref).ref;
      if (!["string", "number"].includes(typeof p.value) && p.value !== null)
        throw Error("Cell value must be text or number");
      return {
        ...clone(p),
        sheet: s.id,
        address: a,
        before: clone(s.cells[a] || null),
      };
    });
    return {
      id: uid(),
      summary: String(proposal.summary || "Proposed research edits"),
      status: "pending",
      createdAt: new Date().toISOString(),
      cells,
      blocks: clone(proposal.blocks || []),
      beforeDocument: clone(book.document),
    };
  }
  function applyProposal(book, p) {
    if (p.status !== "pending") throw Error("Proposal is no longer pending");
    for (const c of p.cells)
      if (
        JSON.stringify(sheet(book, c.sheet).cells[c.address] || null) !==
        JSON.stringify(c.before)
      )
        throw Error(
          "Cell " + c.address + " changed since preview; stage a new proposal",
        );
    if (
      p.blocks.length &&
      JSON.stringify(p.beforeDocument) !== JSON.stringify(book.document)
    )
      throw Error("Document changed since preview; stage a new proposal");
    for (const c of p.cells)
      put(book, c.sheet, c.address, c.value, c.format, c.source);
    p.status = "applied";
    return book;
  }
  function validateBook(b) {
    if (
      !b ||
      b.version !== 1 ||
      !Array.isArray(b.sheets) ||
      !b.sheets.length ||
      b.sheets.length > 30
    )
      throw Error("Unsupported research workbook");
    const out = clone(b);
    let count = 0;
    const ids = new Set(),
      names = new Set();
    for (const s of out.sheets) {
      if (
        !s.id ||
        ids.has(s.id) ||
        !s.name ||
        names.has(s.name) ||
        /[\[\]:*?\/\\]/.test(s.name) ||
        s.name.length > 31
      )
        throw Error("Invalid or duplicate sheet");
      ids.add(s.id);
      names.add(s.name);
      s.cells = s.cells || {};
      for (const [a, c] of Object.entries(s.cells || {})) {
        address(a);
        if (c == null || !["string", "number"].includes(typeof c.value))
          throw Error("Invalid cell value");
        count++;
      }
      if (count > LIMITS.cells) throw Error("Workbook exceeds cell limit");
      s.rows = Math.max(20, Math.min(LIMITS.rows, +s.rows || 40));
      s.cols = Math.max(8, Math.min(LIMITS.cols, +s.cols || 14));
      s.widths = s.widths || {};
      s.heights = s.heights || {};
      s.hiddenRows = s.hiddenRows || [];
      s.hiddenCols = s.hiddenCols || [];
    }
    out.names = out.names || {};
    out.document = out.document || createWorkbook().document;
    out.audit = Array.isArray(out.audit) ? out.audit : [];
    out.imports = Array.isArray(out.imports) ? out.imports : [];
    if (!ids.has(out.activeSheet)) out.activeSheet = out.sheets[0].id;
    return out;
  }
  function save(storage, b) {
    storage.setItem("fu:workspace:v1", JSON.stringify(b));
  }
  function load(storage) {
    const saved = storage.getItem("fu:workspace:v1");
    if (saved) return validateBook(JSON.parse(saved));
    const b = createWorkbook();
    const oldSheet = storage.getItem("fu:sheet"),
      oldMemo = storage.getItem("fu:memo");
    if (oldSheet || oldMemo) {
      if (!storage.getItem("fu:workspace:legacy-backup"))
        storage.setItem(
          "fu:workspace:legacy-backup",
          JSON.stringify({
            sheet: oldSheet,
            memo: oldMemo,
            migratedAt: new Date().toISOString(),
          }),
        );
      try {
        const cells = JSON.parse(oldSheet || "null");
        if (cells)
          for (const [a, v] of Object.entries(cells))
            try {
              put(b, b.activeSheet, a, v);
            } catch {}
      } catch {}
      try {
        const html = JSON.parse(oldMemo || "null");
        if (typeof html === "string") b.document.html = html;
      } catch {}
      b.title = "Migrated research";
      b.audit.push({
        at: new Date().toISOString(),
        action: "Imported legacy sheet and memo; originals retained",
      });
    }
    save(storage, b);
    return b;
  }
  function buildModel({
    ticker,
    periods = [],
    resolve = () => null,
    title,
  } = {}) {
    ticker = String(ticker || "").toUpperCase();
    const b = createWorkbook(title || ticker + " • Operating model"),
      s = b.sheets[0];
    s.name = "Model";
    s.cols = 12;
    s.rows = 32;
    const a = createSheet("Assumptions"),
      src = createSheet("Sources");
    b.sheets.push(a, src);
    const hist = periods.slice(-3);
    if (!hist.length) hist.push("LFY");
    const firstForecast = hist.length + 1;
    const set = (s, a, v, f) => put(b, s.id, a, v, f);
    set(s, "A1", ticker + " | Operating model", { bold: true, size: 15 });
    set(
      s,
      "A2",
      "Filed history • USD in millions • forecasts are user assumptions",
    );
    set(a, "A1", "Editable forecast drivers", { bold: true });
    set(
      a,
      "A2",
      "Neutral starting assumptions; not consensus or guidance. Ratios carry forward latest filed data.",
    );
    const labels = {
      5: "Revenue",
      6: "Revenue growth",
      7: "Operating income",
      8: "Operating margin",
      10: "Operating cash flow",
      11: "Capex",
      12: "Free cash flow",
      13: "FCF margin",
      15: "Net income",
      16: "Net margin",
      18: "Unlevered FCF (assumptions)",
      19: "Discounted unlevered FCF",
      22: "Terminal value",
      23: "Enterprise value",
      25: "Net debt (user input)",
      26: "Equity value",
      27: "Diluted shares (user input)",
      28: "Value / share (USD)",
    };
    for (const [r, l] of Object.entries(labels))
      set(s, "A" + r, l, { bold: [5, 12, 23, 26, 28].includes(+r) });
    const sourceRows = [
      [
        "Metric",
        "Period",
        "Value (native units)",
        "Evidence ID",
        "Source URL",
        "Filed date",
        "Status",
      ],
    ];
    const metrics = {
      5: "Revenue",
      7: "Operating income",
      10: "Operating cash flow",
      11: "Capex",
      15: "Net income",
    };
    for (let i = 0; i < hist.length; i++) {
      const c = col(i + 1);
      set(s, c + "3", hist[i] + " • Filed", { bold: true, fill: "#e9eff5" });
      for (const [r, m] of Object.entries(metrics)) {
        set(s, c + r, `=FU("${ticker}","${m}","${hist[i]}")/1000000`, {
          number: "number",
        });
        const f = resolve(ticker, m, hist[i]);
        if (f?.source) s.cells[c + r].source = clone(f.source);
        sourceRows.push([
          m,
          hist[i],
          f?.value ?? f?.v ?? "Missing",
          f?.source?.id || "",
          f?.source?.sourceUrl || "",
          f?.source?.filedDate || "",
          f ? "Filed / derived; inspect source" : "Unavailable",
        ]);
      }
      set(s, c + "8", `=${c}7/${c}5`, { number: "percent" });
      set(s, c + "12", `=${c}10-${c}11`);
      set(s, c + "13", `=${c}12/${c}5`, { number: "percent" });
      set(s, c + "16", `=${c}15/${c}5`, { number: "percent" });
      if (i) set(s, c + "6", `=${c}5/${col(i)}5-1`, { number: "percent" });
    }
    const last = col(hist.length),
      assumptions = [
        ["Revenue growth", 0, "Growth"],
        ["Operating margin", `=Model!${last}8`, "OperatingMargin"],
        [
          "Operating cash flow / revenue",
          `=Model!${last}10/Model!${last}5`,
          "CashConversion",
        ],
        ["Capex / revenue", `=Model!${last}11/Model!${last}5`, "CapexRatio"],
        ["Net margin", `=Model!${last}16`, "NetMargin"],
        ["Discount rate", 0.1, "DiscountRate"],
        ["Terminal growth", 0.02, "TerminalGrowth"],
        ["Net debt (USD millions)", "", "NetDebt"],
        ["Diluted shares (millions)", "", "DilutedShares"],
        ["Cash tax rate (user input)", "", "TaxRate"],
        ["D&A / revenue (user input)", "", "DepreciationRatio"],
        ["Incremental working capital / revenue", "", "WorkingCapitalRatio"],
      ];
    assumptions.forEach(([label, v, n], i) => {
      set(a, "A" + (i + 4), label);
      set(a, "B" + (i + 4), v, {
        number: i < 7 || i > 8 ? "percent" : "number",
        fill: "#fff3d9",
      });
      b.names[n] = { sheet: a.id, address: "B" + (i + 4) };
    });
    for (let i = 0; i < 3; i++) {
      const c = col(firstForecast + i),
        prev = col(firstForecast + i - 1);
      set(s, c + "3", `Year ${i + 1} • Forecast`, {
        bold: true,
        fill: "#e8f4ef",
      });
      set(s, c + "6", "=Growth", { number: "percent" });
      set(s, c + "5", `=${prev}5*(1+${c}6)`);
      set(s, c + "8", "=OperatingMargin", { number: "percent" });
      set(s, c + "7", `=${c}5*${c}8`);
      set(s, c + "10", `=${c}5*CashConversion`);
      set(s, c + "11", `=${c}5*CapexRatio`);
      set(s, c + "12", `=${c}10-${c}11`);
      set(s, c + "13", `=${c}12/${c}5`, { number: "percent" });
      set(s, c + "16", "=NetMargin", { number: "percent" });
      set(s, c + "15", `=${c}5*${c}16`);
      set(
        s,
        c + "18",
        `=${c}7*(1-TaxRate)+${c}5*DepreciationRatio-${c}11-${c}5*WorkingCapitalRatio`,
      );
      set(s, c + "19", `=${c}18/(1+DiscountRate)^${i + 1}`);
    }
    const fc = col(firstForecast),
      end = col(firstForecast + 2);
    set(
      s,
      end + "22",
      `=${end}18*(1+TerminalGrowth)/(SQRT(DiscountRate-TerminalGrowth)^2)`,
    );
    set(s, end + "23", `=SUM(${fc}19:${end}19)+${end}22/(1+DiscountRate)^3`);
    set(s, end + "25", "=NetDebt");
    set(s, end + "26", `=${end}23-${end}25`);
    set(s, end + "27", "=DilutedShares");
    set(s, end + "28", `=${end}26/${end}27`, { number: "currency" });
    set(
      s,
      "A30",
      "DCF requires tax, D&A, working-capital, debt/share assumptions and a positive discount spread.",
    );
    set(
      s,
      "A31",
      "Generic industrial DCF; financials, REITs and other accounting models require specialist assumptions.",
    );
    set(
      a,
      "A17",
      "Discount rate and terminal growth are illustrative assumptions, not filed forecasts.",
    );
    set(
      a,
      "A18",
      "D&A and incremental working capital are ratios of forecast revenue. Enter explicit assumptions.",
    );
    sourceRows.forEach((row, i) =>
      row.forEach((v, j) => set(src, col(j) + (i + 1), v)),
    );
    src.cols = 8;
    src.widths = { 0: 180, 1: 130, 2: 150, 3: 220, 4: 380, 5: 120, 6: 250 };
    s.widths = { 0: 260 };
    a.widths = { 0: 290, 1: 180 };
    b.model = {
      ticker,
      periods: hist,
      forecastYears: 3,
      createdAt: new Date().toISOString(),
    };
    b.audit.push({
      at: new Date().toISOString(),
      action: "Built filed history and assumption-driven forecast",
    });
    return b;
  }
  root.FU_WORKSPACE_ENGINE = {
    clone,
    uid,
    LIMITS,
    col,
    address,
    range,
    refs,
    createSheet,
    createWorkbook,
    sheet,
    put,
    paste,
    fill,
    translate,
    evaluate,
    History,
    stage,
    applyProposal,
    validateBook,
    save,
    load,
    buildModel,
  };
})(typeof window !== "undefined" ? window : globalThis);
