/* Immutable, content-addressed financial evidence. Plain browser script / Node side-effect import. */
(function (root) {
  'use strict';
  const records = new Map(), cells = new Map();
  const clone = x => x == null ? x : JSON.parse(JSON.stringify(x, (_, v) => typeof v === 'function' ? undefined : v));
  const freeze = x => { if (x && typeof x === 'object') { Object.values(x).forEach(freeze); Object.freeze(x); } return x; };
  const canonical = x => x && typeof x === 'object' ? Array.isArray(x) ? '[' + x.map(canonical).join(',') + ']' : '{' + Object.keys(x).sort().map(k => JSON.stringify(k) + ':' + canonical(x[k])).join(',') + '}' : JSON.stringify(x);
  // Two independent 32-bit hashes; collision guard compares the full canonical payload.
  const hash = s => { let a = 2166136261, b = 5381; for (const c of s) { a = Math.imul(a ^ c.charCodeAt(0), 16777619); b = Math.imul(b, 33) ^ c.charCodeAt(0); } return (a >>> 0).toString(16).padStart(8, '0') + (b >>> 0).toString(16).padStart(8, '0'); };
  const safeUrl = x => { try { const u = new URL(x); return /^https?:$/.test(u.protocol) ? u.href : null; } catch { return null; } };
  let persistenceError = null, dirty = false, scheduled = false;
  function flush() {
    if (!dirty || !root.localStorage) return { ok: !persistenceError, error: persistenceError };
    dirty = false; const previous = persistenceError;
    try { root.localStorage.setItem('fu:evidence:v1', JSON.stringify({ records: [...records.values()], cells: [...cells] })); persistenceError = null; }
    catch (e) { persistenceError = String(e.message || e); }
    const state = { ok: !persistenceError, error: persistenceError };
    if (previous !== persistenceError && root.dispatchEvent && root.CustomEvent) root.dispatchEvent(new root.CustomEvent('fu:evidence-persistence', { detail: state }));
    return state;
  }
  function save() {
    if (!root.localStorage) return;
    dirty = true;
    if (!scheduled) { scheduled = true; const enqueue = root.queueMicrotask || (fn => Promise.resolve().then(fn)); enqueue(() => { scheduled = false; flush(); }); }
  }
  root.addEventListener?.('pagehide', flush);
  function capture(input) {
    const value = typeof input.value === 'number' && Number.isFinite(input.value) ? input.value : null;
    const sourceUrl = safeUrl(input.sourceUrl);
    const inputEvidenceIds = [...new Set(input.inputEvidenceIds || [])];
    const gaps = [];
    if (value == null) gaps.push('Missing finite numeric value');
    if (!input.unit) gaps.push('Missing unit');
    if (!input.ticker || !input.metric) gaps.push('Missing company or metric');
    if (!input.availabilityDate && !input.filedDate) gaps.push('Unknown availability date');
    if (!input.periodEnd) gaps.push('Missing period end');
    if (!sourceUrl && !inputEvidenceIds.length) gaps.push('Missing source');
    if (inputEvidenceIds.some(id => !records.has(id) || records.get(id).validation.status !== 'valid')) gaps.push('Unverified derivation inputs');
    if (input.validation?.status && input.validation.status !== 'valid') gaps.push(...(input.validation.issues || [input.validation.status]));
    const rec = { version: 1, ticker: String(input.ticker || '').toUpperCase(), metric: String(input.metric || ''), value, unit: input.unit || null,
      period: input.period || input.periodEnd || null, periodStart: input.periodStart || null, periodEnd: input.periodEnd || null, periodType: input.periodType || 'unknown',
      concept: input.concept || null, accession: input.accession || null, filedDate: input.filedDate || null, sourceUrl,
      availabilityDate: input.availabilityDate || input.filedDate || null, inputEvidenceIds, derivation: input.derivation || null,
      validation: { status: gaps.length ? 'unverified' : 'valid', scope: 'record_structure_and_provenance; not independent source reconciliation', issues: gaps }, quality: clone(input.quality || {}) };
    const body = canonical(rec), id = 'ev_' + hash(body);
    if (records.has(id)) { const old = { ...records.get(id) }; delete old.id; if (canonical(old) !== body) throw new Error('Evidence identity collision'); return records.get(id); }
    const result = freeze({ id, ...rec }); records.set(id, result); save(); return result;
  }
  function fromCell(cell, context = {}) {
    if (!cell) return null;
    const rec = cell.rec || {}, inputs = (cell.calc?.inputs || []).map(c => fromCell(c, context.resolveContext ? context.resolveContext(c) : {})).filter(Boolean);
    if (cell.derived?.from && cell.derived?.minus) {
      for (const r of [cell.derived.from, cell.derived.minus]) {
        const child = { ...cell, kind: 'YTD', periodType: 'duration', v: r.v, start: r.start, end: r.end, derived: null, rec: { ...rec, ...r, url: r.url || (r.accn === rec.accn ? rec.url : null), accessions:r.accessions || (r.accn?[r.accn]:[]), filed: r.filed || (r.accn === rec.accn ? rec.filed : null), available_at: r.available_at || r.filed || (r.accn === rec.accn ? rec.available_at : null) } };
        inputs.push(fromCell(child, context.resolveContext ? context.resolveContext(child) : { sourceUrl: r.url, period: `${r.start} to ${r.end}` }));
      }
    }
    const periodType = ['instant', 'quarter', 'annual', 'duration', 'ttm'].includes(cell.periodType) ? cell.periodType : cell.kind === 'TTM' ? 'ttm' : cell.stmt === 'balance' ? 'instant' : cell.kind === 'A' ? 'annual' : cell.kind === 'Q' ? 'quarter' : cell.kind === 'YTD' ? 'duration' : 'unknown';
    const result = capture({ ticker: cell.tk, metric: cell.label, value: cell.v, unit: cell.unit, period: context.period || cell.end,
      periodStart: cell.start || inputs[0]?.periodStart, periodEnd: cell.end || inputs.at(-1)?.periodEnd, periodType,
      concept: cell.conceptFull || cell.concept, accession: rec.accn, filedDate: rec.filed || context.filedDate, sourceUrl: context.sourceUrl || rec.url,
      availabilityDate: inputs.length ? inputs.every(x => x.availabilityDate) ? inputs.map(x => x.availabilityDate).sort().at(-1) : null : rec.available_at || rec.filed || context.filedDate,
      inputEvidenceIds: inputs.map(x => x.id), derivation: cell.calc?.formula || (cell.derived ? 'Reported cumulative value minus prior cumulative value' : null),
      quality: { derived: !!cell.derived, calculated: !!cell.calc, accessions: rec.accessions || (rec.accn ? [rec.accn] : []), vintage: rec.vintage || "legacy_unknown", selectionReason: rec.selection_reason || null, sourceValidationStatus: rec.validation_status || null, ...(rec.quality || {}) }, validation: rec.validation || (rec.validation_status && !["valid", "reported", "derived"].includes(rec.validation_status) ? {status: rec.validation_status} : null) });
    if (!cells.has(result.id)) { cells.set(result.id, freeze(clone({ ...cell, evidenceId: result.id, evidenceSource: context.filing || { primary: result.sourceUrl, filed: result.filedDate } }))); save(); }
    return result;
  }
  function validateClaim(claim) {
    const e = records.get(claim.evidenceId);
    const result = (status, reason) => ({ status, reason, evidenceId: claim.evidenceId || null });
    if (!e) return result('unverified', 'Unknown evidence ID');
    for (const key of ['ticker', 'metric', 'period']) if (claim[key] != null && claim[key] !== e[key]) return result('contradicted', key + ' does not match evidence');
    if (typeof claim.value !== 'number' || !Number.isFinite(claim.value) || !claim.unit) return result('unverified', 'Claim needs exact numeric value and unit');
    if (claim.unit !== e.unit || claim.value !== e.value) return result('contradicted', 'Value or unit does not match evidence');
    return e.validation.status === 'valid' ? result('verified', 'Exact value and unit match this evidence record only; prose is not verified') : result('unverified', e.validation.issues.join('; '));
  }
  function list({ ticker, metric, offset = 0, limit = 20 } = {}) {
    const all = [...records.values()].filter(e => (!ticker || e.ticker === ticker.toUpperCase()) && (!metric || e.metric.toLowerCase().includes(metric.toLowerCase())));
    offset = Math.max(0, Math.floor(Number(offset) || 0)); limit = Math.max(1, Math.min(40, Math.floor(Number(limit) || 20)));
    return { records: all.slice(offset, offset + limit), total: all.length, nextOffset: offset + limit < all.length ? offset + limit : null, truncated: offset + limit < all.length };
  }
  try { const saved = JSON.parse(root.localStorage?.getItem('fu:evidence:v1') || 'null'); for (const r of saved?.records || []) { const { id, ...data } = r; if (id === 'ev_' + hash(canonical(data))) records.set(id, freeze(r)); } for (const [id, cell] of saved?.cells || []) if (records.has(id)) cells.set(id, freeze(cell)); } catch { /* Corrupt storage does not prevent fresh research. */ }
  root.FU_EVIDENCE = Object.freeze({ capture, fromCell, get: id => records.get(id) || null, cell: id => cells.get(id) || null, list, validateClaim,
    citation: id => '[[E:' + id + ']]', flush, persistence: () => ({ ok: !persistenceError, error: persistenceError }), safeUrl });
})(typeof window !== 'undefined' ? window : globalThis);
