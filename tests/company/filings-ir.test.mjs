import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { JSDOM } from 'jsdom';

const FILINGS = [
  { f: '10-K', d: '2026-11-01', r: '2026-09-30', a: '0001-26-000010', i: '' },
  { f: '8-K', d: '2026-10-30', r: '2026-10-30', a: '0001-26-000009', i: '2.02,9.01' },
  { f: '4', d: '2026-10-20', r: '2026-10-18', a: '0001-26-000008', i: '' },
  { f: '4', d: '2026-10-12', r: '2026-10-10', a: '0001-26-000007', i: '' },
  { f: '10-Q', d: '2026-08-01', r: '2026-06-30', a: '0001-26-000006', i: '' },
  { f: 'S-8', d: '2026-03-01', r: null, a: '0001-26-000005', i: '' },
];

function setup(load, { release = null, reactions = [] } = {}) {
  const dom = new JSDOM('<main></main>', { url: 'https://research.example/', runScripts: 'outside-only' });
  const w = dom.window, host = w.document.querySelector('main');
  w.el = (tag, attrs = {}, ...children) => {
    const node = w.document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
      if (value == null || value === false) continue;
      if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
      else if (key === 'html') node.innerHTML = value;
      else node.setAttribute(key, value === true ? '' : String(value));
    }
    for (const child of children.flat()) if (child != null && child !== false) node.append(child.nodeType ? child : String(child));
    return node;
  };
  w.panel = (title, action, ...children) => w.el('section', { class: 'panel' }, w.el('h3', {}, title), action, ...children);
  w.seg = (options, value, onChange) => w.el('div', { class: 'seg' }, ...options.map(([v, l]) => w.el('button', { 'data-v': v, onclick: () => onChange(v) }, l)));
  w.earningsReactions = () => reactions;
  w.E = { releases: release ? { TEST: release } : {}, narratives: release ? { TEST: { release_highlights: ['Revenue rose.'], meta: { at: '2026-10-30T00:00:00Z' } } } : {} };
  w.D = { companies: { TEST: { cik: 1234, filings: FILINGS } } };
  w.IR = { TEST: 'https://ir.example/' };
  w.IRPUB = 'https://files.example/ir/';
  w.fdate = v => String(v); w.fmon = v => String(v); w.fsgn = v => String(v); w.fpct = v => String(v); w.cls = () => ''; w.css = () => '#000';
  w.md = text => text; w.aiCredit = () => w.el('span', { class: 'chip-ai' }, 'AI-generated');
  w.formCls = () => ''; w.filingDesc = f => ({ '10-K': 'Annual report', '10-Q': 'Quarterly report', '8-K': 'Results of operations', '4': 'Insider transaction' })[f.f] || f.f;
  w.filingUrl = (tk, f) => 'https://sec.example/' + f.a;
  w.charted = 0; w.barChart = () => { w.charted++; }; w.requestAnimationFrame = fn => fn();
  w.openDoc = (ticker, doc) => { w.opened = { ticker, doc }; };
  w.getJSON = path => { w.requested = path; return load(path); };
  w.eval(readFileSync('public/lib/ir-library.js', 'utf8'));
  const app = readFileSync('public/app.js', 'utf8');
  // one evaluation so the top-level consts (DOC_TYPES, CALL_DOC_TYPES) share a scope as they do in the page
  w.eval(app.slice(app.indexOf('const DOC_TYPES'), app.indexOf('/* ---------- document viewer')) + '\n' + app.slice(app.indexOf('const CALL_DOC_TYPES'), app.indexOf('function srcCard(')));
  return { w, host, close: () => w.close() };
}
const settle = () => new Promise(resolve => setImmediate(resolve));

test('Filings & IR lays out latest earnings, SEC filings and the full IR library as sections', async () => {
  const documents = Array.from({ length: 237 }, (_, i) => ({ id: String(i), title: `Release ${i}`, storage_path: `TEST/${i}.pdf`, ext: '.pdf' }));
  const release = { filed: '2026-10-30', url: 'https://sec.example/ex99', accn: '0001-26-000009', markdown: 'Test reports record third quarter results today.' };
  const h = setup(async () => ({ site: 'https://ir.example/', documents }), { release, reactions: [{ date: '2026-10-30', r1: .01, s1: .002, r5: .03, url: 'https://sec.example/8k' }] });
  try {
    await h.w.coFilings(h.host, 'TEST');
    await settle();
    assert.equal(h.w.requested, 'ir/TEST.json');
    assert.deepEqual([...h.host.querySelectorAll('.fi-sec')].map(s => s.dataset.sec), ['earnings', 'sec', 'ir']);
    assert.deepEqual([...h.host.querySelectorAll('.fi-jump button')].map(b => b.dataset.sec), ['earnings', 'sec', 'ir']);
    assert.ok(h.host.querySelector('.fi-sec[data-sec="earnings"] .fi-release .release'), 'release text sits in a height-capped box');
    assert.match(h.host.textContent, /Release highlights/);
    assert.match(h.host.textContent, /AI-generated/);
    assert.match(h.host.textContent, /Release log/);
    assert.equal(h.w.charted, 1);
    const library = h.host.querySelector('.fi-sec[data-sec="ir"] .ir-library');
    assert.ok(library, 'the IR document library is part of the tab');
    assert.match(library.textContent, /237 documents/);
    assert.equal(h.host.querySelector('.fi-jump button[data-sec="ir"] .fi-jump-n').textContent, '237');
    const next = library.querySelector('[aria-label="Next page"]');
    for (let i = 0; i < 4; i++) next.click();
    assert.match(library.querySelector('[role="status"]').textContent, /201–237 of 237/);
    library.querySelector('.ir-document-title').click();
    assert.equal(h.w.opened.ticker, 'TEST');
    assert.ok(documents.includes(h.w.opened.doc));
  } finally { h.close(); }
});

test('SEC filings keep report forms first, filter by form and search every field', async () => {
  const h = setup(async () => ({ documents: [] }));
  try {
    await h.w.coFilings(h.host, 'TEST');
    await settle();
    const sec = h.host.querySelector('.fi-sec[data-sec="sec"]');
    assert.deepEqual([...sec.querySelectorAll('.seg button')].map(b => b.dataset.v), ['all', '10-K', '10-Q', '8-K', '4', 'S-8']);
    const rows = () => [...sec.querySelectorAll('tbody tr')].map(r => r.querySelector('td').textContent);
    assert.equal(rows().length, FILINGS.length);
    assert.match(sec.querySelector('.fi-count').textContent, /6 of 6 filings/);
    sec.querySelector('.seg button[data-v="4"]').click();
    assert.deepEqual(rows(), ['4', '4']);
    sec.querySelector('.seg button[data-v="all"]').click();
    const q = sec.querySelector('.fi-search');
    q.value = '000006'; q.dispatchEvent(new h.w.Event('input'));
    assert.deepEqual(rows(), ['10-Q']);
    q.value = 'results of operations'; q.dispatchEvent(new h.w.Event('input'));
    assert.deepEqual(rows(), ['8-K']);
    q.value = 'no such filing'; q.dispatchEvent(new h.w.Event('input'));
    assert.match(sec.textContent, /No filings match these filters/);
    assert.equal(sec.querySelector('a[href*="CIK=1234"]').textContent, 'Full history on EDGAR ↗');
  } finally { h.close(); }
});

test('latest call materials list the newest period only and open in the document panel', async () => {
  const documents = [
    { id: 'r3', title: 'Q3 release', doc_type: 'release', fiscal_label: 'Q3 FY26', period_end: '2026-09-30', published: '2026-10-30', url: 'https://ir.example/r3' },
    { id: 'p3', title: 'Q3 deck', doc_type: 'presentation', fiscal_label: 'Q3 FY26' },
    { id: 't3', title: 'Q3 call transcript', doc_type: 'transcript', fiscal_label: 'Q3 FY26', published: '2026-10-31' },
    { id: 'r2', title: 'Q2 release', doc_type: 'release', fiscal_label: 'Q2 FY26', period_end: '2026-06-30', published: '2026-07-30' },
    { id: 'k', title: 'Annual report', doc_type: 'annual_report', fiscal_label: 'Q3 FY26', published: '2026-11-01' },
  ];
  const h = setup(async () => ({ documents }));
  try {
    await h.w.coFilings(h.host, 'TEST');
    await settle();
    const box = h.host.querySelector('.fi-materials');
    assert.match(box.textContent, /Period Q3 FY26 · 3 documents/);
    assert.deepEqual([...box.querySelectorAll('.fi-mat-title')].map(b => b.textContent), ['Q3 release', 'Q3 deck', 'Q3 call transcript']);
    assert.match(box.textContent, /Date unavailable/, 'an undated document is not given a date');
    box.querySelectorAll('.fi-mat-title')[2].click();
    assert.equal(h.w.opened.doc.id, 't3');
    assert.match(h.host.textContent, /No earnings release exhibit/, 'a missing release is stated, not hidden');
  } finally { h.close(); }
});

test('IR load failure is explicit and retry can recover without hiding release content', async () => {
  let attempts = 0;
  const h = setup(async () => { if (++attempts === 1) throw new Error('temporary network failure'); return { documents: [] }; });
  try {
    await h.w.coFilings(h.host, 'TEST');
    await settle();
    assert.match(h.host.textContent, /Could not load investor relations documents/);
    assert.match(h.host.textContent, /Call materials could not be loaded/);
    assert.match(h.host.textContent, /Release log/);
    assert.ok(!h.host.querySelector('.ir-library'), 'failed reads cannot masquerade as an empty archive');
    [...h.host.querySelectorAll('button')].find(button => button.textContent === 'Retry').click();
    await settle();
    assert.equal(attempts, 2);
    assert.match(h.host.querySelector('.ir-library').textContent, /No investor relations documents have been collected/);
    assert.match(h.host.querySelector('.fi-materials').textContent, /No release, presentation or call documents/);
    assert.doesNotMatch(h.host.textContent, /Could not load investor relations documents/);
  } finally { h.close(); }
});
