import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

function api() { const context = vm.createContext({ Date, console }); vm.runInContext(readFileSync('public/lib/financial.js', 'utf8'), context); return context.FU_FINANCIAL; }
const shares = { val: 100, end: '2026-09-30', filed: '2026-10-20', available_at: '2026-10-20', basis: 'reported_outstanding', accn: 'source' };

test('company market cap requires valid outstanding shares available by the actual closing date', () => {
  const f = api();
  assert.equal(f.marketCap(12.5, shares, '2026-11-03'), 1250);
  assert.equal(f.marketCap(12.5, { ...shares, end: '2027-07-17' }, '2026-11-03'), null);
  assert.equal(f.marketCap(12.5, shares, '2026-10-19'), null);
  assert.equal(f.marketCap(12.5, { ...shares, end: '2025-01-01' }, '2026-11-03'), null);
  assert.equal(f.marketCap(12.5, { ...shares, end: '2026-02-31' }, '2026-11-03'), null);
  assert.equal(f.marketCap(12.5, { ...shares, basis: 'weighted_average' }, '2026-11-03'), null);
  assert.equal(f.marketCap(12.5, { ...shares, val: -28000000 }, '2026-11-03'), null);
  assert.equal(f.marketCap(Infinity, shares, '2026-11-03'), null);
  assert.equal(f.marketCap(12.5, { val: 100 }, '2026-11-03'), null);
});
