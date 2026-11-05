import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { JSDOM } from 'jsdom';

function harness() {
  const dom = new JSDOM('<main></main><div id="tip"></div>', { runScripts: 'outside-only', pretendToBeVisual: true });
  const w = dom.window;
  w.el = (tag, attrs = {}, ...children) => {
    const node = w.document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
      if (value == null || value === false) continue;
      if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
      else node.setAttribute(key, String(value));
    }
    for (const child of children.flat()) if (child != null && child !== false) node.append(child.nodeType ? child : String(child));
    return node;
  };
  w.panel = (title, right, ...body) => w.el('section', { class: 'panel' }, w.el('div', { class: 'hd' }, w.el('h3', {}, title), right), ...body);
  w.seg = (options) => w.el('div', { class: 'seg' }, ...options.map(([, label]) => w.el('button', {}, label)));
  w.css = () => '#000';
  w.relTime = () => '1h ago';
  w.MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  w.pd = s => { const [y, m, d] = s.split('-').map(Number); return new Date(Date.UTC(y, m - 1, d || 1)); };
  w.fdate = s => { const d = w.pd(s); return `${w.MONTHS[d.getUTCMonth()]} ${d.getUTCDate()}, ${d.getUTCFullYear()}`; };
  w.fnum = (v, d = 2) => v.toFixed(d);
  w.esc = s => String(s);
  w.store = { get: (k, d) => d, set: () => {} };
  w.showTip = () => {};
  w.tip = w.document.getElementById('tip');
  w.requestAnimationFrame = () => 0; // charts need layout; the tests cover the tables and summary strip
  w.d3 = {};
  return w;
}

const ALT = {
  ticker: 'ACME', updated_at: '2026-11-05T06:00:00Z',
  short_volume: { url: 'https://finra.example', rows: [['2026-11-03', 400, 0, 1000], ['2026-11-04', 600, 2, 1000]] },
  ftd: { url: 'https://sec.example', through: '202608b', rows: [['2026-08-20', 12000, 50.5, 'X'], ['2026-08-27', 3000, 51, 'X']] },
  lobbying: { client_names: ['ACME CORP'], rows: [
    { year: 2025, quarter: 4, amount: 1000000, basis: 'in-house report', firms: ['Firm A'], reports: [{ url: 'https://lda.example/1' }] },
    { year: 2026, quarter: 1, amount: 1500000, basis: 'in-house report', firms: [], reports: [{ url: 'https://lda.example/2' }] }] },
  contracts: { entities: ['ACME CORP', 'ACME FEDERAL LLC'], rows: [
    { fy: 2025, amount: 2.5e9, partial: false }, { fy: 2026, amount: 3e9, partial: false }, { fy: 2027, amount: 1e8, partial: true }],
    awards: [{ id: 'W1', url: 'https://usaspending.example/W1', agency: 'Department of Defense', sub_agency: 'Department of the Army', recipient: 'ACME FEDERAL LLC', amount: 4.2e8, description: 'Widgets' }] },
};

test('Alt data tab registers after Insiders and renders summary, fails and source notes', async () => {
  const w = harness();
  w.getJSON = async path => { assert.equal(path, 'altdata/ACME.json'); return ALT; };
  w.eval(readFileSync('public/pages/altdata.js', 'utf8'));
  const tab = w.FU_COTABS.find(t => t.id === 'altdata');
  assert.equal(tab.name, 'Alt data');
  assert.equal(tab.after, 'insiders');
  const body = w.document.querySelector('main');
  tab.render(body, 'ACME');
  await new Promise(r => setTimeout(r, 0));
  const stats = [...body.querySelectorAll('.ad-stats > div')].map(d => [d.querySelector('.l').textContent, d.querySelector('.v').textContent]);
  assert.deepEqual(stats.map(s => s[0]), ['Short volume, latest day', 'Short volume, 20 days', 'Fails to deliver, latest', 'Fails, peak in 90 days', 'Lobbying, last 4 quarters', 'Federal awards, FY2026']);
  assert.equal(stats[0][1], '60.0%');
  assert.equal(stats[2][1], '3,000');
  assert.equal(stats[4][1], '$2.5M');
  assert.equal(stats[5][1], '$3.00B');
  const text = body.textContent;
  assert.match(text, /is not short interest/);
  assert.match(text, /Client names matched: ACME CORP/);
  assert.match(text, /Entities: ACME CORP; ACME FEDERAL LLC/);
  assert.ok(body.querySelector('a[href="https://usaspending.example/W1"]'));
  assert.ok(body.querySelector('a[href="https://lda.example/1"]'));
  w.close();
});

test('Alt data tab shows a collecting state when the file is missing', async () => {
  const w = harness();
  w.getJSON = async () => { throw new Error('404'); };
  w.eval(readFileSync('public/pages/altdata.js', 'utf8'));
  const body = w.document.querySelector('main');
  w.FU_COTABS.find(t => t.id === 'altdata').render(body, 'NONE');
  await new Promise(r => setTimeout(r, 0));
  assert.match(body.textContent, /still being collected/);
  w.close();
});

test('Insider names and values use the shared compact format', () => {
  const w = harness();
  const app = readFileSync('public/app.js', 'utf8');
  w.eval(app.slice(app.indexOf('function insMoney('), app.indexOf('function renderInsiders(')));
  assert.equal(w.insName('LEVINSON ARTHUR D'), 'Levinson Arthur D');
  assert.equal(w.insName("O'BRIEN DEIRDRE"), "O'Brien Deirdre");
  assert.equal(w.insName('Newstead Jennifer'), 'Newstead Jennifer');
  assert.equal(w.insMoney(823720.64), '$824K');
  assert.equal(w.insMoney(13897.2), '$13.9K');
  assert.equal(w.insMoney(-209e6), '−$209M');
  assert.equal(w.insMoney(0), '–');
  w.close();
});
