import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { JSDOM } from 'jsdom';

const BUNDLE = JSON.parse(readFileSync('tests/estimates/fixtures/bundle.json', 'utf8'));

function harness() {
  const dom = new JSDOM('<main></main><div id="tip"></div><aside id="drawer"><div id="dh"></div><div id="db"></div></aside><div id="scrim"></div>', { runScripts: 'outside-only', pretendToBeVisual: true });
  const w = dom.window;
  w.eval(`
    var el = (tag, attrs = {}, ...kids) => { const e = document.createElement(tag); for (const [k, v] of Object.entries(attrs || {})) { if (v == null || v === false) continue; if (k === 'class') e.className = v; else if (k.startsWith('on')) e.addEventListener(k.slice(2), v); else e.setAttribute(k, v === true ? '' : v); } for (const k of kids.flat()) if (k != null && k !== false) e.append(k.nodeType ? k : document.createTextNode(String(k))); return e; };
    var $ = (s, r = document) => r.querySelector(s);
    var panel = (title, right, ...body) => el('section', { class: 'panel' }, el('div', { class: 'hd' }, el('h3', {}, title), right), ...body);
    var seg = (options, value, onChange) => el('div', { class: 'seg' }, ...options.map(([k, l]) => el('button', { class: k === value ? 'on' : '', onclick: () => onChange(k) }, l)));
    var MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    var pd = s => { const [y, m, d] = s.split('-').map(Number); return new Date(Date.UTC(y, m - 1, d || 1)); };
    var fdate = s => { if (!s) return ''; const d = pd(s); return MONTHS[d.getUTCMonth()] + ' ' + d.getUTCDate() + ', ' + d.getUTCFullYear(); };
    var fnum = (v, d = 2) => v.toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
    var fsgn = (v, d = 1) => v == null || !isFinite(v) ? '–' : (v >= 0 ? '+' : '−') + Math.abs(v * 100).toFixed(d) + '%';
    var fmtMoney = v => '$' + (v / 1e9).toFixed(2) + 'B';
    var cls = v => v > 0 ? 'up' : v < 0 ? 'down' : '';
    var css = () => '#000'; var relTime = () => '1h ago'; var esc = s => String(s);
    var store = { get: (k, d) => d, set: () => {} }; var showTip = () => {}; var hideTip = () => {}; var reveal = () => {};
    var svgUse = () => document.createElement('span'); var PUB = 'https://data.example/';
    var drawer = document.getElementById('drawer'), scrim = document.getElementById('scrim'); var DR = {};
    var closeSource = () => drawer.classList.remove('open');
    var OPENED = []; var openDoc = (tk, d, hl) => OPENED.push({ tk, d, hl }); var openSource = () => {}; var model = () => ({ get: () => null });
    var D = { companies: {} };
    window.requestAnimationFrame = () => 0; window.d3 = {};
  `);
  return w;
}

test('Estimates tab registers after Financials and renders consensus, actions and guidance with sources', async () => {
  const w = harness();
  w.getJSON = async path => { if (path === 'estimates/AAPL.json') return BUNDLE; if (path === 'estimates/firms.json') return { firms: [] }; throw new Error(path); };
  w.eval(readFileSync('public/lib/estimates.js', 'utf8'));
  const tab = w.FU_COTABS.find(t => t.id === 'estimates');
  assert.equal(tab.after, 'financials');
  assert.equal(tab.name, 'Estimates & guidance');
  const body = w.document.querySelector('main');
  tab.render(body, 'AAPL');
  await new Promise(r => setTimeout(r, 10));
  const text = body.textContent;
  assert.match(text, /Consensus estimates/);
  assert.match(text, /\$2\.90/);                         // current-quarter EPS consensus from the provider
  assert.match(text, /Powered by Benzinga Data/);
  assert.match(text, /A dash means no snapshot that old exists yet/);
  assert.match(text, /never mixed into the surprise/);
  const rows = body.querySelectorAll('.es-act tbody tr');
  assert.ok(rows.length >= 9);
  // a consensus value opens the provider record in the shared drawer, highlighted
  body.querySelector('.es-cons .es-v').click();
  const drawer = w.document.getElementById('drawer');
  assert.ok(drawer.classList.contains('open'));
  assert.match(drawer.textContent, /Yahoo Finance/);
  assert.ok(drawer.querySelector('tr.es-hit'));
  // an analyst row opens the Benzinga record with attribution
  rows[0].click();
  assert.match(drawer.textContent, /Powered by Benzinga Data/);
  // a guidance quote opens the source document with the quote to highlight
  body.querySelector('.es-quote').click();
  assert.equal(w.OPENED.at(-1).d.url, 'https://sec.example/ex99.htm');
  assert.match(w.OPENED.at(-1).hl.quote, /138 billion/);
  w.close();
});

test('Estimates tab shows an unavailable state when the bundle is missing', async () => {
  const w = harness();
  w.getJSON = async () => { throw new Error('404'); };
  w.eval(readFileSync('public/lib/estimates.js', 'utf8'));
  const body = w.document.querySelector('main');
  w.FU_COTABS.find(t => t.id === 'estimates').render(body, 'NONE');
  await new Promise(r => setTimeout(r, 10));
  assert.match(body.textContent, /not available yet/);
  w.close();
});
