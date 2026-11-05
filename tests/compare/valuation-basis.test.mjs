import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

test('current comparison EPS honors a valuation hold without hiding pre-event reported periods', () => {
  let basis = null;
  const context = vm.createContext({
    companySummary: () => ({ valuation_basis: basis }),
    dayDiff: (a, b) => (Date.parse(b) - Date.parse(a)) / 86400000,
  });
  const source = readFileSync('public/pages/compare.js', 'utf8');
  vm.runInContext(source.slice(source.indexOf('  function ttmAt('), source.indexOf('  function bal(')), context);
  const model = year => ({ series: () => [
    { end: `${year - 1}-12-31`, cell: { v: 2, unit: 'USD/shares' } },
    { end: `${year}-03-31`, cell: { v: 3, unit: 'USD/shares' } },
    { end: `${year}-06-30`, cell: { v: 4, unit: 'USD/shares' } },
    { end: `${year}-09-30`, cell: { v: 5, unit: 'USD/shares' } },
  ] });
  assert.equal(context.ttmAt(model(2026), 'AAA', 'EPS diluted', '2026-09-30').v, 14);
  basis = { status: 'held', affected_fields: ['eps_ttm', 'pe_ttm'], effective_date: '2026-06-29' };
  assert.equal(context.ttmAt(model(2026), 'AAA', 'EPS diluted', '2026-09-30'), null);
  assert.equal(context.ttmAt(model(2025), 'AAA', 'EPS diluted', '2025-09-30').v, 14);
});
