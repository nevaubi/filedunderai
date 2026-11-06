import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { JSDOM } from 'jsdom';

function harness(data) {
  const dom = new JSDOM('<main></main><div id="tip"></div><aside id="drawer"><div id="dh"></div><div id="db"></div></aside><div id="scrim"></div><div id="crumb-tab"></div>', { runScripts: 'outside-only', pretendToBeVisual: true });
  const w = dom.window;
  w.el = (tag, attrs = {}, ...children) => {
    const node = w.document.createElement(tag);
    for (const [key, value] of Object.entries(attrs || {})) {
      if (value == null || value === false) continue;
      if (key === 'class') node.className = value;
      else if (key === 'html') node.innerHTML = value;
      else if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
      else node.setAttribute(key, String(value));
    }
    for (const child of children.flat()) if (child != null && child !== false) node.append(child.nodeType ? child : String(child));
    return node;
  };
  w.$ = s => w.document.querySelector(s);
  w.panel = (title, right, ...body) => w.el('section', { class: 'panel' }, w.el('div', { class: 'hd' }, w.el('h3', {}, title), right), ...body);
  w.seg = (options) => w.el('div', { class: 'seg' }, ...options.map(([, label]) => w.el('button', {}, label)));
  w.css = () => '#000'; w.relTime = () => '1h ago'; w.esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  w.MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  w.pd = s => { const [y, m, d] = s.split('-').map(Number); return new Date(Date.UTC(y, m - 1, d || 1)); };
  w.fdate = s => { const d = w.pd(s); return `${w.MONTHS[d.getUTCMonth()]} ${d.getUTCDate()}, ${d.getUTCFullYear()}`; };
  w.fmon = s => { const d = w.pd(s); return `${w.MONTHS[d.getUTCMonth()]} ${String(d.getUTCFullYear()).slice(2)}`; };
  w.dayDiff = (a, b) => Math.round((w.pd(b) - w.pd(a)) / 864e5);
  w.fnum = (v, d = 2) => Number(v).toFixed(d);
  w.insName = s => s; w.store = { get: (k, d) => d, set: () => {} };
  w.showTip = () => {}; w.hideTip = () => {}; w.tip = w.document.getElementById('tip'); w.copyText = () => {};
  w.svgUse = () => w.document.createElement('span'); w.reveal = () => {}; w.closeSource = () => {};
  w.drawer = w.document.getElementById('drawer'); w.scrim = w.document.getElementById('scrim'); w.DR = {};
  w.requestAnimationFrame = () => 0; w.d3 = undefined; w.PX = {}; w.D = { companies: { ACME: { cik: 1 } } };
  w.getJSON = async path => { if (path in data) return data[path]; throw new Error('404'); };
  w.eval(readFileSync(new URL('../../public/lib/insiders-gov.js', import.meta.url), 'utf8'));
  return w;
}

const INS = { codes: { P: 'Open-market buy', S: 'Open-market sale' }, w90: { buys: 3 }, w365: {}, generated: '2026-11-01T00:00:00Z', rows: [
  { accession: 'a1', seq: 0, owner: 'A', code: 'P', tx_date: '2026-10-01', shares: 100, price: 10, derivative: false },
  { accession: 'a2', seq: 0, owner: 'B', code: 'P', tx_date: '2026-10-05', shares: 100, price: 10, derivative: false },
  { accession: 'a3', seq: 0, owner: 'C', code: 'P', tx_date: '2026-10-09', shares: 100, price: 10, derivative: false },
  { accession: 'a4', seq: 0, owner: 'D', code: 'S', tx_date: '2026-10-09', shares: 50, price: 10, derivative: false, plan_10b5: true }] };
const GOV = { generated: '2026-11-05T00:00:00Z', sources: { congress_house: { status: 'ok', count: 1 }, congress_senate: { status: 'not_collected', note: 'affirmation required' } },
  congress_trades: [{ id: 'house:1:0', chamber: 'House', member: 'Jane Doe', party: 'Republican', state_dst: 'VA01', type: 'Purchase', type_code: 'P', tx_date: '2026-10-01', filed: '2026-10-20', lag_days: 19,
    amount: '$1,001 - $15,000', amin: 1001, amax: 15000, ticker_reported: 'ACME', cited: 'Acme Corp (ACME) [ST] P 10/01/2026 $1,001 - $15,000', page: 1, pdf_url: 'https://disclosures-clerk.house.gov/x.pdf',
    committee_flag: true, flag_committees: ['House Committee on Armed Services'], committees: ['House Committee on Armed Services'], member_matched: true, src: { url: 'https://disclosures-clerk.house.gov/x.pdf', method: 'm' } }] };

test('cluster detection needs three distinct insiders within 14 days', () => {
  const w = harness({});
  const c = w.FU_INSIDERS_GOV.clusters(INS.rows, 'P');
  assert.equal(c.length, 1);
  assert.equal(c[0].people.length, 3);
  assert.equal(w.FU_INSIDERS_GOV.clusters(INS.rows, 'S').length, 0);
});

test('markText escapes and highlights cited text', () => {
  const w = harness({});
  assert.equal(w.FU_INSIDERS_GOV.markText('a <b> $1,001 - $15,000', ['$1,001 - $15,000']), 'a &lt;b&gt; <mark class="fu-hit">$1,001 - $15,000</mark>');
});

test('registers as the insiders tab and renders sections with source states and a drawer record', async () => {
  const w = harness({ 'insiders/ACME.json': INS, 'gov/ACME.json': GOV });
  const tab = w.FU_COTABS.find(t => t.id === 'insiders');
  assert.equal(tab.name, 'Insiders & influence');
  assert.equal(tab.after, 'filings');
  const body = w.document.querySelector('main');
  await tab.render(body, 'ACME');
  assert.match(body.textContent, /Cluster events, 12m/);
  [...body.querySelectorAll('.ig-nav button')].find(b => /Congress/.test(b.textContent)).click();
  assert.match(body.textContent, /not collected/);
  body.querySelector('.ig-pane tr.click').click();
  const db = w.document.getElementById('db');
  assert.ok(db.querySelector('mark.fu-hit'), 'cited PTR text is highlighted');
  assert.match(db.textContent, /House Committee on Armed Services/);
  [...body.querySelectorAll('.ig-nav button')].find(b => /Large holders/.test(b.textContent)).click();
  assert.match(body.textContent, /not yet collected|unavailable/);
});
