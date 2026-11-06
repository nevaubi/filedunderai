// Ported from the reference platform's src/lib/supply/*.test.ts, on our compact link records.
import test from 'node:test';
import assert from 'node:assert/strict';
await import('../../public/pages/supply-core.js');
const C = globalThis.FU_SUPPLY_CORE;
const L = p => ({ id: Math.random().toString(36).slice(2), t: 'AAPL', cp: 'TSMC', cpt: null, rel: 'supplier', det: '', sh: null, q: '', accn: 'a', doc: 'd', form: '10-K', filed: '2025-10-31', rg: [], ...p });
const close = (a, b, e = 1e-9) => assert.ok(Math.abs(a - b) < e, `${a} != ${b}`);

test('rejects a quote that is not verbatim, and quotes outside 8-40 words', () => {
  const text = 'Substantially all of the Company’s hardware products are manufactured by outsourcing partners in Asia today.';
  assert.equal(C.quoteInText('the Company\'s hardware products are manufactured by outsourcing partners', text), true);
  assert.equal(C.quoteInText('the Company\'s hardware products are built by outsourcing partners', text), false);
  assert.equal(C.quoteInText('outsourcing partners in Asia', text), false);
});
test('findQuote maps a normalised match back to the original characters', () => {
  const text = 'We   rely on “TSMC”\nfor wafers — and others.';
  const [s, e] = C.findQuote(text, 'rely on "TSMC" for wafers - and');
  assert.equal(text.slice(s, e), 'rely on “TSMC”\nfor wafers — and');
  assert.equal(C.findQuote(text, 'not there'), null);
});
test('a supplier of A shows A as its customer', () => {
  assert.equal(C.inverse('supplier'), 'customer');
  const g = C.focusGraph('NVDA', [L({ t: 'MSFT', cp: 'NVIDIA', cpt: 'NVDA', rel: 'supplier' })], { MSFT: 'Microsoft' });
  assert.equal(g[0].ticker, 'MSFT'); assert.equal(g[0].relation, 'customer');
});
test('customer share converts to dollars using the filer revenue', () => {
  assert.equal(C.linkDollars(L({ t: 'NVDA', rel: 'customer', sh: 12 }), { NVDA: 100e9 }), 12e9);
  assert.equal(C.linkDollars(L({ rel: 'supplier', sh: 30 }), { AAPL: 400e9 }), null);
});
test('a 20% shock at a customer worth 12% of a supplier puts 2.4% of that supplier at risk', () => {
  const r = C.simulateShock('MSFT', 0.2, [L({ t: 'NVDA', cp: 'Microsoft', cpt: 'MSFT', rel: 'customer', sh: 12 })], { NVDA: 'NVIDIA' }, { NVDA: 100e9 });
  assert.equal(r[0].ticker, 'NVDA'); assert.equal(r[0].channel, 'lost sales'); close(r[0].revenuePct, 0.024); close(r[0].dollars, 2.4e9, 1);
});
test('single-sourced customers are high-severity supply disruptions', () => {
  const r = C.simulateShock('TSM', 0.2, [L({ t: 'AAPL', cp: 'TSMC', cpt: 'TSM', rel: 'supplier', ss: true })], {}, {});
  assert.equal(r[0].ticker, 'AAPL'); assert.equal(r[0].severity, 'high');
});
test('finds added, dropped and share moves between two 10-Ks', () => {
  const all = [L({ accn: 'new', cp: 'TSMC', sh: 12 }), L({ accn: 'new', cp: 'Foxconn' }), L({ accn: 'old', cp: 'TSMC', sh: 9 }), L({ accn: 'old', cp: 'Samsung' })];
  const c = C.linkChanges('AAPL', all, 'new', 'old');
  assert.deepEqual(c.map(x => `${x.kind}:${x.name}`), ['share:TSMC', 'added:Foxconn', 'dropped:Samsung']);
  assert.equal(c[0].from, 9); assert.equal(c[0].to, 12);
});
test('earnings reactions: close before to close after, and a 2x beta', () => {
  const cl = [['2025-01-02', 100], ['2025-01-03', 100], ['2025-01-06', 110], ['2025-01-07', 121]];
  close(C.eventReturn(cl, '2025-01-06'), 0.21); close(C.eventReturn(cl, '2025-01-04'), 0.21);
  const f = [], o = [], moves = [0.05, -0.03, 0.02, -0.04]; let pf = 100, po = 100;
  moves.forEach((m, k) => { for (const [d, mm] of [[`2025-0${k + 1}-01`, 0], [`2025-0${k + 1}-02`, m], [`2025-0${k + 1}-03`, 0]]) { pf *= 1 + mm; po *= 1 + 2 * mm; f.push([d, pf]); o.push([d, po]); } });
  const s = C.spillover(f, o, moves.map((_, k) => `2025-0${k + 1}-02`));
  assert.equal(s.events, 4); assert.equal(s.sameDir, 1); assert.ok(s.beta > 1.9);
});
test('merges TSMC spellings and strips suffixes', () => {
  assert.equal(C.entityKey('Taiwan Semiconductor Manufacturing Company Limited', null), 'TSM');
  assert.equal(C.entityKey('TSMC', null), 'TSM');
  assert.equal(C.entityKey('Acme Holdings, Inc.', null), 'acme');
});
test('corroborated when the counterparty filing names the filer back', () => {
  const a = L({ t: 'NVDA', cp: 'Microsoft', cpt: 'MSFT', rel: 'customer' }), b = L({ t: 'MSFT', cp: 'NVIDIA', cpt: 'NVDA', rel: 'supplier' });
  assert.equal(C.confidence(a, [a, b]), 'corroborated'); assert.equal(C.confidence(a, [a]), 'mentioned'); assert.equal(C.confidence({ ...a, sh: 12 }, [a]), 'stated');
});
test('dependency score for a 30% customer, a sole-source supplier and a Taiwan link', () => {
  const d = C.dependencyScore('X', [L({ t: 'X', rel: 'customer', cp: 'Big', sh: 30 }), L({ t: 'X', rel: 'supplier', cp: 'Fab', ss: true, rg: ['Taiwan'] })]);
  assert.equal(d.topCustomerPct, 30); assert.equal(d.customerHhi, 900); assert.deepEqual(d.parts.map(p => p.points), [28, 10, 6]); assert.equal(d.score, 44);
});
test('country spellings and region shock scoring', () => {
  assert.equal(C.canonCountry('United States of America'), 'United States'); assert.equal(C.canonCountry('PRC'), 'China');
  assert.equal(C.regionShock('Taiwan', [L({ t: 'AAA', cp: 'X', rg: ['Taiwan'], ss: true })])[0].score, 25);
  const h = C.regionShock('China', [L({ t: 'AAA', cp: 'X', rel: 'customer', sh: 50, rg: ['China'] })], [], { AAA: 100 });
  assert.equal(h[0].score, 44);
  const q = C.regionShock('Taiwan', [L({ t: 'AAA', cp: 'TSMC' })]);
  assert.equal(q[0].hq, 1); assert.equal(q[0].score, 6);
});
test('concentration history from 10-K shares only; freshness bands', () => {
  const h = C.concentrationHistory('AAA', [L({ t: 'AAA', rel: 'customer', cp: 'Walmart Inc.', sh: 12, filed: '2023-02-01' }), L({ t: 'AAA', rel: 'customer', cp: 'Walmart', sh: 15, filed: '2024-02-01' }), L({ t: 'AAA', rel: 'customer', cp: 'Walmart', sh: 30, filed: '2024-05-01', form: '10-Q' })]);
  assert.deepEqual(h.series, ['Walmart Inc.']); assert.deepEqual(h.points.map(p => p['Walmart Inc.']), [12, 15]);
  assert.equal(C.freshness('2026-01-01', '2026-07-20').status, 'fresh'); assert.equal(C.freshness('2025-01-01', '2026-01-01').status, 'aging'); assert.equal(C.freshness('2024-01-01', '2026-01-01').status, 'stale');
});
test('equal-weight basket compounds daily', () => {
  const b = C.ecosystemBasket('F', ['A', 'B'], ['d0', 'd1', 'd2'], { F: [0, 0.1, 0], A: [0, 0.1, 0.1], B: [0, -0.1, 0.1] }, 2);
  assert.equal(b.index[0].basket, 100); close(b.basketRet, 0.1, 1e-6); close(b.focusRet, 0.1, 1e-6);
});
test('agreement terms and exact-word marking', () => {
  const q = 'In January 2025 we entered into a three-year capacity agreement with prepayments of $1.5 billion through fiscal 2028, representing 12% of purchases; the agreement is non-cancellable.';
  const a = C.agreementTerms(q);
  assert.ok(a.dates.includes('January 2025') && a.dates.includes('fiscal 2028')); assert.ok(a.amounts.includes('$1.5 billion')); assert.ok(a.percents.includes('12%'));
  for (const c of ['Prepayment / deposit', 'Non-cancellable', 'Capacity reservation']) assert.ok(a.clauses.includes(c));
  const parts = C.markTerms(q, a); assert.equal(parts.map(p => p.s).join(''), q); assert.ok(parts.filter(p => p.hit).map(p => p.s).includes('$1.5 billion'));
  assert.equal(C.riskTier({ stability: -1.2, ruleOfLaw: 1, regulatory: 1 }), 'high'); assert.equal(C.riskTier({}), 'unrated');
});
test('cross-source insights: shared supplier and corroboration carry every ref', () => {
  const all = [L({ id: 'a', t: 'AAPL', cp: 'TSMC', cpt: 'TSM' }), L({ id: 'b', t: 'NVDA', cp: 'TSMC', cpt: 'TSM' }), L({ id: 'c', t: 'AMD', cp: 'Taiwan Semiconductor', cpt: 'TSM' }),
    L({ id: 'd', t: 'AAPL', cp: 'Qualcomm', cpt: 'QCOM', rel: 'supplier' }), L({ id: 'e', t: 'QCOM', cp: 'Apple', cpt: 'AAPL', rel: 'customer' })];
  const ins = C.crossInsights('AAPL', all, [], {}, {});
  const shared = ins.find(i => i.kind === 'shared supplier'); assert.deepEqual(shared.refs.sort(), ['a', 'b', 'c']);
  const cor = ins.find(i => i.kind === 'corroborated'); assert.deepEqual(cor.refs, ['d', 'e']);
});
test('second-order expansion follows the same relation outward', () => {
  const all = [L({ t: 'AAPL', cp: 'Qualcomm', cpt: 'QCOM' }), L({ t: 'QCOM', cp: 'TSMC', cpt: 'TSM' })];
  const first = C.focusGraph('AAPL', all, {});
  const h2 = C.secondOrder('AAPL', first, all, {});
  assert.equal(h2.length, 1); assert.equal(h2[0].ticker, 'TSM'); assert.equal(h2[0].hop, 2);
});
