import test from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { JSDOM } from 'jsdom';

function setup(ir) {
  const dom = new JSDOM('<main></main>', { url: 'https://research.example/', runScripts: 'outside-only' });
  const w = dom.window;
  if (existsSync('public/lib/ir-library.js')) w.eval(readFileSync('public/lib/ir-library.js', 'utf8'));
  assert.equal(typeof w.FU_IR_LIBRARY?.render, 'function', 'the full-history IR library is available');
  const host = w.document.querySelector('main');
  let selected;
  w.FU_IR_LIBRARY.render(host, 'TEST', ir, { fileBase: 'https://files.example/ir/', openDocument: doc => { selected = doc; } });
  return { w, host, selected: () => selected, close: () => dom.window.close() };
}
const rows = host => [...host.querySelectorAll('tbody tr')];
const button = (host, name) => [...host.querySelectorAll('button')].find(node => node.getAttribute('aria-label') === name || node.textContent === name);

test('IR history remains reachable beyond 200 documents and filtering resets pagination', () => {
  const documents = Array.from({ length: 237 }, (_, i) => ({ id: String(i), title: `Document ${i}`, doc_type: i === 0 ? 'transcript' : 'release', published: new Date(Date.UTC(2026, 0, i + 1)).toISOString().slice(0, 10), storage_path: `TEST/${i}.pdf`, ext: '.pdf' }));
  const h = setup({ documents });
  try {
    const { host, w } = h;
    assert.equal(rows(host).length, 50);
    assert.match(host.querySelector('[role="status"]').textContent, /1–50 of 237/);
    for (let i = 0; i < 4; i++) button(host, 'Next page').click();
    assert.equal(rows(host).length, 37);
    assert.match(host.querySelector('[role="status"]').textContent, /201–237 of 237/);
    assert.equal(button(host, 'Next page').disabled, true);
    assert.ok(rows(host).some(row => row.textContent.includes('Document 0')));
    const filter = host.querySelector('select'); filter.value = 'transcript'; filter.dispatchEvent(new w.Event('change'));
    assert.equal(rows(host).length, 1);
    assert.match(host.querySelector('[role="status"]').textContent, /1–1 of 1/);
    const search = host.querySelector('input[type="search"]'); search.value = 'no match'; search.dispatchEvent(new w.Event('input'));
    assert.equal(rows(host).length, 0);
    assert.match(host.textContent, /No documents match/);
  } finally { h.close(); }
});

test('IR date ordering uses actual dates and numeric fiscal years, never collection time', () => {
  const documents = [
    { id: 'old-quarter', title: 'Alpha quarter', fiscal_label: 'Q4 FY24', fetched_at: '2026-11-04' },
    { id: 'new-year', title: 'Zebra year', fiscal_label: 'FY2025', fetched_at: '2025-01-01' },
    { id: 'new-date', title: 'Dated release', published: '2026-10-01', period_end: '2026-06-30' },
    { id: 'invalid-date', title: 'Unknown date', published: '2026-02-31', fetched_at: '2026-11-04' },
  ];
  const h = setup({ site: 'not a valid URL', documents });
  try {
    assert.deepEqual(rows(h.host).map(row => row.dataset.documentId), ['new-date', 'new-year', 'old-quarter', 'invalid-date']);
    assert.match(rows(h.host)[3].textContent, /Date unavailable/);
    assert.doesNotMatch(rows(h.host)[3].textContent, /2026-11-04/);
    assert.equal(h.host.querySelector('a[href="not a valid URL"]'), null);
  } finally { h.close(); }
});

test('IR source buttons preserve the exact record and unsafe file URLs remain inert', () => {
  const document = { id: 'source', title: '<img src=x onerror=alert(1)>', storage_path: 'TEST/source.pdf', doc_type: 'release', ext: '.pdf' };
  const h = setup({ documents: [document, { id: 'unsafe', title: 'Unsafe original', url: 'javascript:alert(1)' }] });
  try {
    button(h.host, 'Open document: <img src=x onerror=alert(1)>').click();
    assert.equal(h.selected(), document);
    assert.equal(h.host.querySelector('img'), null);
    assert.equal(h.host.querySelector('a[href^="javascript:"]'), null);
    assert.equal(h.host.querySelector('a[href="https://files.example/ir/TEST/source.pdf"]').getAttribute('rel'), 'noopener noreferrer');
  } finally { h.close(); }
});

test('IR legacy manifests and empty libraries do not imply complete archives', () => {
  const h = setup({ documents: [] });
  try {
    assert.match(h.host.textContent, /Coverage not yet verified/);
    assert.match(h.host.textContent, /No investor relations documents/);
    assert.doesNotMatch(h.host.textContent, /Every file is stored|Archive complete/);
    assert.equal(h.host.querySelector('details').open, false);
  } finally { h.close(); }
});

test('IR coverage surfaces retries and limited extraction even if a producer says complete', () => {
  const h = setup({ documents: [{ id: 'one', title: 'One', extraction: { status: 'limited', limits: ['row limit'] } }], coverage: {
    version: 1, status: 'complete', scope: { web: true, sec: true, since_year: 2020, sec_since: '2020-01-01', history: 'available_archive' },
    enumeration: { sec: 'complete', web: 'link_traversal_unverified' },
    pending: { discovery: 2, documents: 3, extractions: 1 }, failed: { discovery: 1, documents: 0, extractions: 0 },
    extraction: { verified: 0, limited: 1, empty: 0, unsupported: 0, failed: 0, unverified: 0 }, total_documents: 1, limits: ['Dynamic archives may require additional discovery.'],
  } });
  try {
    assert.match(h.host.textContent, /Backfill incomplete/);
    assert.match(h.host.textContent, /6 pending/);
    assert.match(h.host.textContent, /1 failed/);
    assert.match(h.host.textContent, /1 limited/);
    assert.match(h.host.textContent, /2020/);
    assert.match(h.host.textContent, /Dynamic archives/);
    assert.match(rows(h.host)[0].textContent, /Limited extraction/);
  } finally { h.close(); }
});

test('IR complete status is scoped to verified enumeration and text coverage', () => {
  const h = setup({ documents: [{ id: 'one', title: 'One', text_path: 'one.json', extraction: { status: 'verified' } }], coverage: {
    version: 1, status: 'complete', scope: { web: false, sec: true, since_year: 2024, sec_since: '2024-01-01', history: 'recent' },
    enumeration: { sec: 'complete', web: 'not_requested' }, pending: { discovery: 0, documents: 0, extractions: 0 }, failed: { discovery: 0, documents: 0, extractions: 0 },
    extraction: { verified: 1, limited: 0, empty: 0, unsupported: 0, failed: 0, unverified: 0 }, total_documents: 1, limits: [],
  } });
  try {
    assert.match(h.host.textContent, /Verified within recorded scope/);
    assert.match(h.host.textContent, /SEC exhibits from 2024-01-01/);
    assert.match(h.host.textContent, /IR website discovery not requested/);
  } finally { h.close(); }
});


test('IR coverage cannot mark contradictory document extraction states complete', () => {
  const h = setup({ documents: [{ id: 'one', title: 'One', extraction: { status: 'limited' } }], coverage: {
    version: 1, status: 'complete', scope: { web: false, sec: true, sec_since: '2024-01-01' },
    enumeration: { sec: 'complete', web: 'not_requested' }, pending: {}, failed: {},
    extraction: { verified: 1, limited: 0, unverified: 0 }, total_documents: 1, limits: [],
  } });
  try {
    assert.match(h.host.textContent, /Backfill incomplete/);
    assert.match(h.host.textContent, /1 limited/);
    assert.doesNotMatch(h.host.textContent, /Verified within recorded scope/);
  } finally { h.close(); }
});

test('IR coverage requires per-document extraction evidence and cannot trust counts alone', () => {
  const h = setup({ documents: [{ id: 'one', title: 'Legacy text', text_path: 'legacy.json' }], coverage: {
    version: 1, status: 'complete', scope: { web: false, sec: true, sec_since: '2024-01-01' },
    enumeration: { sec: 'complete', web: 'not_requested' }, pending: {}, failed: {},
    extraction: { verified: 1, unverified: 0 }, total_documents: 1, limits: [],
  } });
  try {
    assert.match(h.host.textContent, /Coverage not yet verified/);
    assert.match(h.host.textContent, /1 unverified/);
    assert.doesNotMatch(h.host.textContent, /Verified within recorded scope/);
  } finally { h.close(); }
});
