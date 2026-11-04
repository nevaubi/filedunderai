/* Shared bounded research profiles and a data-only analysis interpreter. */
(function (root) {
  'use strict';
  const freeze = x => { if (x && typeof x === 'object') { Object.values(x).forEach(freeze); Object.freeze(x); } return x; };
  const profiles = freeze({
    brief: { id: 'brief', label: 'Brief', maxCalls: 4, maxTools: 12, maxTokens: 4000, totalTokens: 12000, finalTokens: 3000, maxMs: 60000, requestMs: 25000, maxChars: 32000, maxClaims: 80, instruction: 'Give the direct answer, key evidence and the material caveat. Use a compact comparison when useful.' },
    detailed: { id: 'detailed', label: 'Detailed', maxCalls: 8, maxTools: 32, maxTokens: 9000, totalTokens: 36000, finalTokens: 6000, maxMs: 150000, requestMs: 45000, maxChars: 100000, maxClaims: 160, instruction: 'Develop an evidence-backed analysis with comparisons, calculations, drivers, risks, counterarguments and missing evidence. Use sections and tables where useful. Match depth to the question; avoid padding.' },
    report: { id: 'report', label: 'Report', maxCalls: 10, maxTools: 48, maxTokens: 12000, totalTokens: 60000, finalTokens: 9000, maxMs: 240000, requestMs: 60000, maxChars: 180000, maxClaims: 320, instruction: 'Write a substantive research report: executive findings, scope and periods, source-backed comparisons, reproducible calculations, scenarios with explicit assumptions, risks, conflicting evidence and research gaps. Develop reasoning fully without repetition. Mark unresolved scope; do not claim exhaustive coverage.' }
  });
  function profile(id = 'detailed') { if (typeof id !== 'string' || !Object.hasOwn(profiles, id)) throw new Error('Unknown research profile'); return profiles[id]; }
  function requestBudget(body = {}) {
    const p = profile(body.profile), capped = (v, max, name) => { if (v == null) return max; if (!Number.isFinite(v) || v <= 0 || !Number.isInteger(v)) throw new Error('Invalid ' + name); return Math.min(v, max); };
    const remainingTokens = capped(body.remainingTokens, p.totalTokens, 'remaining tokens');
    if (body.round != null && (!Number.isInteger(body.round) || body.round < 0 || body.round >= p.maxCalls)) throw new Error('Research call limit reached');
    return { profile: p, remainingTokens, maxTokens: Math.min(remainingTokens, capped(body.maxTokens, p.maxTokens, 'token budget')), timeoutMs: capped(body.timeoutMs, p.requestMs, 'timeout') };
  }
  const dimensions = unit => { if (['ratio','pct','x'].includes(unit)) return {}; if (!/^[A-Za-z]+(?:\/[A-Za-z]+)?$/.test(unit || '')) throw new Error('Unsupported unit: ' + unit); const [a,b] = unit.split('/'), d = { [a]: 1 }; if (b) d[b] = (d[b] || 0) - 1; return d; };
  const dimKey = d => JSON.stringify(Object.entries(d).filter(([,v]) => v).sort(([a],[b]) => a.localeCompare(b)));
  const sameUnit = (a,b) => dimKey(a) === dimKey(b);
  const combine = (a,b,sign) => { const d = { ...a }; for (const [k,v] of Object.entries(b)) d[k] = (d[k] || 0) + sign * v; return d; };
  // Type-check the same bounded arithmetic grammar used by the financial calculator.
  function formulaUnit(expression, variables) {
    const s = String(expression); if (s.length > 2000) throw new Error('Formula too long');
    const tokens = s.match(/(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?|[A-Za-z_][A-Za-z_0-9]*|[()+\-*/^,]/gi) || [];
    if (tokens.join('') !== s.replace(/\s/g, '')) throw new Error('Unsupported formula token');
    let pos = 0, depth = 0;
    const agree = (a,b) => { if (!sameUnit(a,b)) throw new Error('Formula combines incompatible units'); return a; };
    function atom() {
      if (++depth > 40) throw new Error('Formula nesting limit'); const t = tokens[pos++]; let d;
      if (t === '+' || t === '-') d = atom();
      else if (t === '(') { d = expressionAt(0); if (tokens[pos++] !== ')') throw new Error('Missing closing parenthesis'); }
      else if (/^(?:\d|\.)/.test(t || '')) d = {};
      else if (tokens[pos] === '(' && ['SUM','AVG','MIN','MAX','ABS','SQRT'].includes(t?.toUpperCase())) {
        pos++; const args = [expressionAt(0)]; while (tokens[pos] === ',') { pos++; args.push(expressionAt(0)); }
        if (tokens[pos++] !== ')') throw new Error('Missing closing parenthesis');
        if (['ABS','SQRT'].includes(t.toUpperCase()) && args.length !== 1) throw new Error('Function takes one argument');
        d = args.reduce(agree); if (t.toUpperCase() === 'SQRT' && !sameUnit(d, {})) throw new Error('SQRT requires dimensionless units');
      } else if (Object.hasOwn(variables,t)) d = dimensions(variables[t].unit);
      else throw new Error('Unknown formula variable: ' + t);
      depth--; return d;
    }
    function expressionAt(min) { let a = atom(); const precedence = { '+':1, '-':1, '*':2, '/':2, '^':3 }; while (precedence[tokens[pos]] >= min) { const op = tokens[pos++], p = precedence[op], b = expressionAt(op === '^' ? p : p+1); if (op === '+' || op === '-') a = agree(a,b); else if (op === '^') { if (!sameUnit(a,{}) || !sameUnit(b,{})) throw new Error('Powers require dimensionless units'); a = {}; } else a = combine(a,b,op === '*' ? 1 : -1); } return a; }
    const result = expressionAt(0); if (pos !== tokens.length) throw new Error('Invalid formula'); return result;
  }
  function runAnalysis(program, { signal } = {}) {
    const start = Date.now(), check = () => { if (signal?.aborted) throw new DOMException('Cancelled','AbortError'); if (Date.now()-start > 1000) throw new Error('Analysis time limit'); };
    check();
    if (!program || typeof program !== 'object' || JSON.stringify(program).length > 60000) throw new Error('Analysis program exceeds 60000 characters');
    if (!Array.isArray(program.inputs) || !program.inputs.length || program.inputs.length > 12 || !Array.isArray(program.steps) || program.steps.length > 16) throw new Error('Analysis requires 1–12 inputs and at most 16 steps');
    const validName = s => typeof s === 'string' && /^[A-Za-z][A-Za-z0-9_]{0,39}$/.test(s) && !['constructor','prototype','__proto__','value'].includes(s);
    const sets = new Map(), assumptions = new Map(), sources = new Set(); let inputCount = 0;
    if (program.assumptions != null && (!Array.isArray(program.assumptions) || program.assumptions.length > 20)) throw new Error('At most 20 assumptions');
    for (const a of program.assumptions || []) { if (!a || !validName(a.name) || assumptions.has(a.name) || !Number.isFinite(a.value) || typeof a.note !== 'string' || !a.note.trim() || a.note.length > 500) throw new Error('Assumptions need a unique name, finite value, unit and explicit note'); dimensions(a.unit); assumptions.set(a.name,{ name:a.name, value:a.value, unit:a.unit, note:a.note }); }
    const source = id => { sources.add(id); if (sources.size > 200) throw new Error('Analysis evidence record limit is 200, including bindings'); const e = root.FU_EVIDENCE.get(id); if (!e || e.value == null || e.validation.status !== 'valid') throw new Error('Known finite, structurally valid immutable evidence required: ' + id); dimensions(e.unit); if (!['instant','annual','quarter','duration','ttm'].includes(e.periodType) || (e.periodType !== 'instant' && (!e.periodStart || e.periodStart > e.periodEnd))) throw new Error('Evidence period is missing or ambiguous'); return e; };
    const row = e => ({ ticker:e.ticker, metric:e.metric, value:e.value, unit:e.unit, period:e.period, periodStart:e.periodStart, periodEnd:e.periodEnd, periodType:e.periodType, inputEvidenceIds:[e.id], assumptions:[] });
    for (const input of program.inputs) { if (!input || !validName(input.name) || sets.has(input.name) || !Array.isArray(input.evidenceIds) || !input.evidenceIds.length || (inputCount += input.evidenceIds.length) > 200 || new Set(input.evidenceIds).size !== input.evidenceIds.length) throw new Error('Unique input names and evidence IDs required; maximum 200 records'); sets.set(input.name,input.evidenceIds.map(id => row(source(id)))); }
    const fields = ['ticker','metric','value','unit','period','periodStart','periodEnd','periodType'];
    const merge = rows => ({ inputEvidenceIds:[...new Set(rows.flatMap(r => r.inputEvidenceIds))], assumptions:[...new Set(rows.flatMap(r => r.assumptions))] });
    const aligned = (a,b) => ['ticker','periodType','periodStart','periodEnd'].every(k => a[k] === b[k]);
    for (const step of program.steps) {
      check(); if (!step || !validName(step.as) || sets.has(step.as) || !sets.has(step.input)) throw new Error('Each step needs an existing input and unique output name');
      const rows = sets.get(step.input); let result;
      if (step.op === 'filter') {
        if (!fields.includes(step.field) || !['=','!=','>','>=','<','<='].includes(step.operator) || !['string','number'].includes(typeof step.value) || (typeof step.value === 'number' && !Number.isFinite(step.value))) throw new Error('Invalid typed filter');
        if (step.field === 'value' && new Set(rows.map(r => r.unit)).size > 1) throw new Error('Filter by unit before comparing numeric values across mixed units');
        result = rows.filter(r => { const v = r[step.field]; if (v == null) return false; if (typeof v !== typeof step.value || (step.field !== 'value' && !['=','!='].includes(step.operator))) throw new Error('Filter types must align'); return step.operator === '=' ? v === step.value : step.operator === '!=' ? v !== step.value : step.operator === '>' ? v > step.value : step.operator === '>=' ? v >= step.value : step.operator === '<' ? v < step.value : v <= step.value; });
      } else if (step.op === 'derive') {
        if (typeof step.expression !== 'string' || (step.bindings != null && (typeof step.bindings !== 'object' || Array.isArray(step.bindings) || Object.keys(step.bindings).length > 12))) throw new Error('Derived formula and bounded bindings required');
        const bindings = Object.entries(step.bindings || {}).map(([name,id]) => { if (!validName(name) || assumptions.has(name)) throw new Error('Invalid binding name'); return [name,source(id)]; });
        result = rows.map(r => { check(); const variables = Object.fromEntries(assumptions); variables.value = {value:r.value,unit:r.unit}; const evidence = [...r.inputEvidenceIds];
          for (const [name,e] of bindings) { if (!aligned(r,e)) throw new Error('Evidence bindings must align company and period'); variables[name] = e; evidence.push(e.id); }
          if (!sameUnit(formulaUnit(step.expression, variables),dimensions(step.unit))) throw new Error('Derived output unit does not match formula');
          const value = root.FU_FINANCIAL.formula(step.expression,Object.fromEntries(Object.entries(variables).map(([k,v]) => [k,v.value])));
          const used = new Set(step.expression.match(/[A-Za-z_][A-Za-z_0-9]*/g));
          return {...r,value,unit:step.unit,metric:step.as,inputEvidenceIds:[...new Set(evidence)],assumptions:[...new Set([...r.assumptions,...[...assumptions.keys()].filter(k => used.has(k))])]}; });
      } else if (step.op === 'sort') {
        if (!fields.includes(step.field) || !['asc','desc'].includes(step.direction)) throw new Error('Invalid sort');
        result = rows.slice().sort((a,b) => { const x=a[step.field],y=b[step.field]; if(x==null)return y==null?0:1;if(y==null)return -1;return (step.direction==='desc'?-1:1)*(typeof x==='number'?x-y:String(x).localeCompare(String(y))); });
      } else if (step.op === 'group' || step.op === 'statistics') {
        const by = step.op === 'statistics' ? [] : step.by;
        if (!Array.isArray(by) || by.length > 4 || by.some(k => !fields.includes(k) || k === 'value') || !['sum','mean','min','max','median','count'].includes(step.stat)) throw new Error('Invalid grouping or statistic');
        const groups = new Map(); for (const r of rows) { const key=JSON.stringify(by.map(k=>r[k])); if(!groups.has(key))groups.set(key,[]);groups.get(key).push(r); }
        result = [...groups.values()].map(group => { const first=group[0]; if (group.some(r=>r.unit!==first.unit)) throw new Error('Aggregation requires matching units'); if(group.some(r=>['periodStart','periodEnd','periodType'].some(k=>r[k]!==first[k])))throw new Error('Aggregation requires matching periods; group by period first');if(group.some(r=>r.metric!==first.metric))throw new Error('Aggregation requires matching metrics');
          const values=group.map(r=>r.value).sort((a,b)=>a-b),total=values.reduce((a,b)=>a+b,0),n=values.length;
          const value=step.stat==='count'?n:step.stat==='sum'?total:step.stat==='mean'?total/n:step.stat==='min'?values[0]:step.stat==='max'?values[n-1]:n%2?values[(n-1)/2]:(values[n/2-1]+values[n/2])/2;
          if(!Number.isFinite(value))throw new Error('Non-finite statistic');
          return {...first,ticker:group.every(r=>r.ticker===first.ticker)?first.ticker:null,value,unit:step.stat==='count'?'count':first.unit,...merge(group),count:n}; });
      } else throw new Error('Unsupported analysis operation: ' + step.op);
      if (result.length > 200) throw new Error('Analysis row limit'); sets.set(step.as,result);
    }
    if (!sets.has(program.output)) throw new Error('Unknown analysis output');
    const output = {status:'calculated',verification:'Deterministic calculation from structurally valid evidence; source truth and narrative are not independently verified.',rows:sets.get(program.output),assumptions:[...assumptions.values()],program:JSON.parse(JSON.stringify(program)),inputRecords:sources.size};
    check(); if (JSON.stringify(output).length > 60000) throw new Error('Analysis output exceeds context budget; filter or group first'); return freeze(output);
  }
  root.FU_RESEARCH_RUNTIME = Object.freeze({ profiles, profile, requestBudget, runAnalysis });
})(typeof window !== 'undefined' ? window : globalThis);
