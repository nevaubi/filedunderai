// Screen spec: the JSON a screen is described by (filters, universe, sort, columns, grouping). Shared by the
// screener page (classic script, window.FU_SCREENSPEC) and the /api/screen route (imported for its side effect).
(function (root) {
  const OPS = [">", ">=", "<", "<=", "between", "=", "!=", "top", "bottom", "true", "false", "exists"];
  const GROUPS = ["sector", "industry", "mcap_bucket", "corr_cluster"];
  const INDEXES = ["sp500", "sp100"];
  const num = v => (typeof v === "number" && isFinite(v)) ? v : (typeof v === "string" && v.trim() !== "" && isFinite(+v) ? +v : null);
  const uniq = a => [...new Set(a)];

  // catalogue: { fields: [{id, u, q:[p5,p50,p95]}], sectors: [..], industries: [..], tickers: [..] }
  function index(cat) {
    if (cat._ix) return cat._ix;
    const f = new Map(cat.fields.map(x => [x.id, x]));
    const lower = (arr) => new Map((arr || []).map(s => [String(s).toLowerCase(), s]));
    cat._ix = { f, sectors: lower(cat.sectors), industries: lower(cat.industries), tickers: new Set(cat.tickers || []) };
    return cat._ix;
  }

  function matchName(map, s, errors, kind) {
    const k = String(s || "").toLowerCase().trim();
    if (map.has(k)) return map.get(k);
    const hits = [...map.keys()].filter(x => x.includes(k) || k.includes(x));
    if (hits.length === 1) return map.get(hits[0]);
    errors.push(`Unknown ${kind} "${s}"`);
    return null;
  }

  // Returns { spec, errors, warnings }. errors mean the spec references things the catalogue does not have.
  function validate(input, cat) {
    const ix = index(cat);
    const errors = [], warnings = [];
    const s = input && typeof input === "object" ? input : {};
    const out = { filters: [], sectors: [], exclude_sectors: [], industries: [], exclude_industries: [], index: "sp500", tickers: [], exclude_tickers: [],
      similar_to: null, sort: null, columns: [], group_by: null, limit: null, explanation: "", assumptions: [] };
    const field = (id, where) => {
      if (typeof id !== "string" || !ix.f.has(id)) { errors.push(`Unknown field "${id}"${where ? " in " + where : ""}`); return null; }
      return ix.f.get(id);
    };
    (Array.isArray(s.filters) ? s.filters : []).forEach((f0, i) => {
      if (!f0 || typeof f0 !== "object") { errors.push(`Filter ${i + 1} is not an object`); return; }
      const fd = field(f0.field, `filter ${i + 1}`);
      if (!fd) return;
      let op = String(f0.op || "").trim().toLowerCase();
      if (op === "is") op = f0.value === false || f0.value === 0 || f0.value === "false" ? "false" : "true";
      if (op === "==") op = "=";
      if (op === "gt") op = ">"; if (op === "gte") op = ">="; if (op === "lt") op = "<"; if (op === "lte") op = "<=";
      if (op === "top_pct") op = "top"; if (op === "bottom_pct") op = "bottom";
      if (!OPS.includes(op)) { errors.push(`Filter on ${fd.id}: unknown op "${f0.op}"`); return; }
      const f = { field: fd.id, op };
      if (fd.u === "bool" && !["true", "false", "exists"].includes(op)) {
        const v = num(f0.value);
        if ((op === "=" && v === 1) || (op === "!=" && v === 0) || f0.value === true) f.op = "true";
        else if ((op === "=" && v === 0) || (op === "!=" && v === 1) || f0.value === false) f.op = "false";
        else { errors.push(`Filter on ${fd.id}: a yes/no field takes op "true" or "false"`); return; }
      } else if (op === "between") {
        const v = Array.isArray(f0.value) ? f0.value.map(num) : [num(f0.min), num(f0.max)];
        if (v.length !== 2 || (v[0] == null && v[1] == null)) { errors.push(`Filter on ${fd.id}: between needs [low, high]`); return; }
        f.value = v[0] != null && v[1] != null && v[0] > v[1] ? [v[1], v[0]] : v;
      } else if (op === "top" || op === "bottom") {
        const p = num(f0.pct ?? f0.value);
        if (p == null || p <= 0 || p > 100) { errors.push(`Filter on ${fd.id}: ${op} needs pct between 0 and 100`); return; }
        f.pct = p <= 1 && f0.pct == null && f0.value != null && p < 1 ? p * 100 : p;
        f.scope = ["sector", "industry"].includes(f0.scope) ? f0.scope : "all";
      } else if (op !== "true" && op !== "false" && op !== "exists") {
        const v = num(f0.value);
        if (v == null) { errors.push(`Filter on ${fd.id}: value must be a number`); return; }
        f.value = v;
      }
      // percentages are fractions (0.15 = 15%); fix an obvious 15 → 0.15 slip and say so
      if ((fd.u === "pct" || fd.u === "pctl" || fd.u === "corr") && fd.q) {
        const span = Math.max(Math.abs(fd.q[0] ?? 0), Math.abs(fd.q[2] ?? 0), fd.u === "corr" || fd.u === "pctl" ? 1 : 0);
        const fix = x => (x != null && Math.abs(x) > 1.5 && Math.abs(x) > span * 3 && Math.abs(x) <= 1000) ? x / 100 : x;
        if (Array.isArray(f.value)) { const nv = f.value.map(fix); if (nv.some((x, k) => x !== f.value[k])) { warnings.push(`Read ${fd.id} bounds as percentages`); f.value = nv; } }
        else if (f.value != null) { const nv = fix(f.value); if (nv !== f.value) { warnings.push(`Read ${fd.id} ${f.value} as ${f.value}%`); f.value = nv; } }
      }
      out.filters.push(f);
    });
    for (const [k, map, kind] of [["sectors", ix.sectors, "sector"], ["exclude_sectors", ix.sectors, "sector"], ["industries", ix.industries, "industry"], ["exclude_industries", ix.industries, "industry"]]) {
      out[k] = uniq((Array.isArray(s[k]) ? s[k] : s[k] ? [s[k]] : []).map(x => matchName(map, x, errors, kind)).filter(Boolean));
    }
    if (s.index != null) { const v = String(s.index).toLowerCase().replace(/[^a-z0-9]/g, ""); if (INDEXES.includes(v)) out.index = v; else errors.push(`Unknown index "${s.index}" (use sp500 or sp100)`); }
    for (const k of ["tickers", "exclude_tickers"]) {
      out[k] = uniq((Array.isArray(s[k]) ? s[k] : []).map(t => String(t).toUpperCase().replace(".", "-")).filter(t => { if (ix.tickers.has(t)) return true; errors.push(`Unknown ticker "${t}"`); return false; }));
    }
    if (s.similar_to) {
      const t = String((typeof s.similar_to === "string" ? s.similar_to : s.similar_to.ticker) || "").toUpperCase().replace(".", "-");
      if (!ix.tickers.has(t)) errors.push(`Unknown ticker "${t}" in similar_to`);
      else {
        const n = Math.max(3, Math.min(100, Math.round(num(s.similar_to.n) || 25)));
        const fields = Array.isArray(s.similar_to.fields) ? s.similar_to.fields.filter(id => field(id, "similar_to.fields")) : null;
        out.similar_to = { ticker: t, n, ...(fields && fields.length ? { fields } : {}) };
      }
    }
    if (s.sort) {
      const sf = typeof s.sort === "string" ? s.sort : s.sort.field;
      if (sf === "similarity" && out.similar_to) out.sort = { field: "similarity", dir: "asc" };
      else if (sf && field(sf, "sort")) out.sort = { field: sf, dir: String(s.sort.dir || "desc").toLowerCase() === "asc" ? "asc" : "desc" };
    }
    out.columns = uniq((Array.isArray(s.columns) ? s.columns : []).filter(id => field(id, "columns")));
    if (s.group_by) {
      const g = String(s.group_by);
      if (GROUPS.includes(g)) out.group_by = g;
      else if (/^quintile:/.test(g) && field(g.slice(9), "group_by")) out.group_by = g;
      else errors.push(`Unknown group_by "${g}"`);
    }
    if (s.limit != null) { const l = num(s.limit); if (l != null && l > 0) out.limit = Math.min(500, Math.round(l)); }
    out.explanation = typeof s.explanation === "string" ? s.explanation.slice(0, 1200) : "";
    out.assumptions = (Array.isArray(s.assumptions) ? s.assumptions : []).filter(x => typeof x === "string").map(x => x.slice(0, 400)).slice(0, 8);
    return { spec: out, errors: uniq(errors), warnings: uniq(warnings) };
  }

  const empty = () => validate({}, { fields: [], sectors: [], industries: [], tickers: [] }).spec;
  root.FU_SCREENSPEC = { OPS, GROUPS, validate, empty };
})(typeof window !== "undefined" ? window : globalThis);
