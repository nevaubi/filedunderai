import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { JSDOM } from 'jsdom';

function setup(load) {
  const dom = new JSDOM('<main></main>', { url: 'https://research.example/', runScripts: 'outside-only' });
  const w = dom.window, host = w.document.querySelector('main');
  w.el = (tag, attrs = {}, ...children) => {
    const node = w.document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
      if (value == null || value === false) continue;
      if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
      else node.setAttribute(key, value === true ? '' : String(value));
    }
    for (const child of children.flat()) if (child != null && child !== false) node.append(child.nodeType ? child : String(child));
    return node;
  };
  w.panel = (title, action, ...children) => w.el('section', {}, w.el('h3', {}, title), action, ...children);
  w.earningsReactions = () => [];
  w.E = { releases: {}, narratives: {} };
  w.IR = { TEST: 'https://ir.example/' };
  w.IRPUB = 'https://files.example/ir/';
  w.openDoc = (ticker, doc) => { w.opened = { ticker, doc }; };
  w.getJSON = path => { w.requested = path; return load(path); };
  w.eval(readFileSync('public/lib/ir-library.js', 'utf8'));
  const app = readFileSync('public/app.js', 'utf8');
  w.eval(app.slice(app.indexOf('function renderIrDocs('), app.indexOf('function docTitle(')));
  w.eval(app.slice(app.indexOf('function coEarnings('), app.indexOf('function coInsiders(')));
  return { w, host, close: () => w.close() };
}

test('Earnings & IR exposes the full document library before release content', async () => {
  const documents = Array.from({ length: 237 }, (_, i) => ({ id: String(i), title: `Release ${i}`, storage_path: `TEST/${i}.pdf`, ext: '.pdf' }));
  const h = setup(async () => ({ site: 'https://ir.example/', documents }));
  try {
    await h.w.coEarnings(h.host, 'TEST');
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(h.w.requested, 'ir/TEST.json');
    const library = h.host.querySelector('.ir-library');
    assert.ok(library, 'the named Earnings & IR tab exposes collected documents');
    assert.ok(h.host.firstElementChild.contains(library), 'documents are available before the long release text');
    assert.match(library.textContent, /237 documents/);
    assert.doesNotMatch(h.host.textContent, /Investor relations site.*Planned/);
    const next = library.querySelector('[aria-label="Next page"]');
    for (let i = 0; i < 4; i++) next.click();
    assert.match(library.querySelector('[role="status"]').textContent, /201–237 of 237/);
    library.querySelector('.ir-document-title').click();
    assert.equal(h.w.opened.ticker, 'TEST');
    assert.ok(documents.includes(h.w.opened.doc));
  } finally { h.close(); }
});

test('IR load failure is explicit and retry can recover without hiding release content', async () => {
  let attempts = 0;
  const h = setup(async () => { if (++attempts === 1) throw new Error('temporary network failure'); return { documents: [] }; });
  try {
    await h.w.coEarnings(h.host, 'TEST');
    await new Promise(resolve => setImmediate(resolve));
    assert.match(h.host.textContent, /Could not load investor relations documents/);
    assert.match(h.host.textContent, /Release log/);
    assert.ok(!h.host.querySelector('.ir-library'), 'failed reads cannot masquerade as an empty archive');
    [...h.host.querySelectorAll('button')].find(button => button.textContent === 'Retry').click();
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(attempts, 2);
    assert.match(h.host.querySelector('.ir-library').textContent, /No investor relations documents have been collected/);
    assert.doesNotMatch(h.host.textContent, /Could not load investor relations documents/);
  } finally { h.close(); }
});
