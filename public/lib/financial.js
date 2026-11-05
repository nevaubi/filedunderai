(function (root) {
  'use strict';
  const day = s => Date.parse(s + 'T00:00:00Z') / 86400000;
  function marketCap(price, shares, priceDate) {
    const validDate = value => typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value) && Number.isFinite(day(value)) && new Date(value + 'T00:00:00Z').toISOString().slice(0, 10) === value;
    const asOf = priceDate || new Date().toISOString().slice(0, 10);
    if (!Number.isFinite(price) || price <= 0 || !shares || !Number.isFinite(shares.val) || shares.val <= 0 || shares.basis !== 'reported_outstanding' || shares.derived || !validDate(asOf) || !validDate(shares.end)) return null;
    const age = day(asOf) - day(shares.end);
    if (age < 0 || age > 400) return null;
    for (const field of ['filed', 'available_at']) if (shares[field] && (!validDate(shares[field]) || day(shares[field]) > day(asOf) || day(shares[field]) < day(shares.end))) return null;
    const value = price * shares.val;
    return Number.isFinite(value) ? value : null;
  }
  function ttm(cells) {
    if (!Array.isArray(cells) || cells.length !== 4 || cells.some(x => !x || ![x.start, x.end].every(d => typeof d === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(d) && Number.isFinite(day(d)) && new Date(d + 'T00:00:00Z').toISOString().slice(0, 10) === d))) return null;
    const c = cells.slice().sort((a, b) => a.end.localeCompare(b.end)), first = c[0];
    // EPS is not additive across changing weighted share denominators. No fabricated TTM EPS.
    if (!['income', 'cashflow', 'ratios'].includes(first.stmt) || first.unit !== 'USD' || (first.stmt === 'ratios' && !['Free cash flow', 'Capital returned'].includes(first.label))) return null;
    // Retail fiscal years may end with a reported sixteen-week quarter (112 inclusive days).
    if (c.some(x => x.tk !== first.tk || x.stmt !== first.stmt || x.unit !== first.unit || x.label !== first.label || !Number.isFinite(x.v) || !x.start || !x.end || day(x.end) - day(x.start) < 70 || day(x.end) - day(x.start) > 111)) return null;
    if (c.some((x, i) => i && day(x.start) - day(c[i - 1].end) !== 1)) return null;
    const duration = day(c[3].end) - day(first.start) + 1;
    if (duration < 350 || duration > 380) return null;
    return { v: c.reduce((a, x) => a + x.v, 0), tk: first.tk, label: first.label, unit: first.unit, kind: 'TTM', start: first.start, end: c[3].end, calc: { formula: 'Sum of four contiguous reported quarters', inputs: c } };
  }
  // Recursive descent arithmetic; no eval, Function, property access, or host capabilities.
  function formula(expression, variables = {}) {
    const s = String(expression); if (s.length > 2000) throw new Error('Formula too long');
    const tokens = s.match(/(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?|[A-Za-z_][A-Za-z_0-9]*|[()+\-*/^,]/gi) || [];
    if (tokens.join('') !== s.replace(/\s/g, '')) throw new Error('Unsupported formula token');
    let pos = 0, depth = 0;
    const funcs = { SUM: a => a.reduce((x, y) => x + y, 0), AVG: a => a.reduce((x, y) => x + y, 0) / a.length, MIN: a => Math.min(...a), MAX: a => Math.max(...a), ABS: a => Math.abs(a[0]), SQRT: a => Math.sqrt(a[0]) };
    function atom() { if (++depth > 40) throw new Error('Formula nesting limit'); let out; const t = tokens[pos++];
      if (t === '+' || t === '-') out = (t === '-' ? -1 : 1) * atom();
      else if (t === '(') { out = expressionAt(0); if (tokens[pos++] !== ')') throw new Error('Missing closing parenthesis'); }
      else if (/^(?:\d|\.)/.test(t || '')) out = Number(t);
      else if (tokens[pos] === '(' && funcs[t?.toUpperCase()]) { pos++; const a = [expressionAt(0)]; while (tokens[pos] === ',') { pos++; a.push(expressionAt(0)); } if (tokens[pos++] !== ')') throw new Error('Missing closing parenthesis'); out = funcs[t.toUpperCase()](a); }
      else if (Object.hasOwn(variables, t) && Number.isFinite(variables[t])) out = variables[t];
      else throw new Error('Unknown variable or function: ' + t);
      depth--; return out;
    }
    function expressionAt(min) { let a = atom(); const precedence = { '+': 1, '-': 1, '*': 2, '/': 2, '^': 3 }; while (precedence[tokens[pos]] >= min) { const op = tokens[pos++], p = precedence[op], b = expressionAt(op === '^' ? p : p + 1); a = op === '+' ? a + b : op === '-' ? a - b : op === '*' ? a * b : op === '/' ? a / b : a ** b; } return a; }
    const result = expressionAt(0); if (pos !== tokens.length || !Number.isFinite(result)) throw new Error('Formula has no finite result'); return result;
  }
  function calculate({ operation = 'formula', expression, values = [], periods, variables = {} } = {}) {
    if (operation === 'formula') return formula(expression, variables);
    if (!Array.isArray(values) || !values.length || values.length > 1000 || values.some(x => typeof x !== 'number' || !Number.isFinite(x))) throw new Error('Finite values required (max 1000)');
    const a = values[0], b = values.at(-1); let result;
    if (operation === 'growth') { if (values.length !== 2 || a <= 0) throw new Error('Growth needs positive base and two values'); result = b / a - 1; }
    else if (operation === 'cagr') { if (values.length !== 2 || a <= 0 || b <= 0 || !(periods > 0)) throw new Error('CAGR needs positive endpoints and periods'); result = (b / a) ** (1 / periods) - 1; }
    else if (operation === 'sum') result = values.reduce((x, y) => x + y, 0);
    else if (operation === 'mean') result = values.reduce((x, y) => x + y, 0) / values.length;
    else throw new Error('Unknown operation');
    if (!Number.isFinite(result)) throw new Error('Non-finite result'); return result;
  }
  root.FU_FINANCIAL = Object.freeze({ ttm, formula, calculate, marketCap });
})(typeof window !== 'undefined' ? window : globalThis);
