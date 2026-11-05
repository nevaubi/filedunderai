import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { JSDOM } from 'jsdom';

const source = 'https://www.sec.gov/files/sec-stats-ipos-20260922.xlsx';
const sourcePage = 'https://www.sec.gov/data-research/statistics-data-visualizations/initial-public-offerings-ipos';
const metrics = ['COUNT', 'TOTAL_PROCEEDS', 'AVERAGE_PROCEEDS', 'MEDIAN_PROCEEDS'];
const groups = ['TOTAL', 'US', 'NON_US', 'CORPORATE', 'SPAC', 'FUND'];

function catalog() {
  const series = {};
  for (const metric of metrics) for (const group of groups) for (const frequency of ['A', 'Q']) {
    const id = `SEC_IPO:${metric}_${group}:${frequency}`, count = metric === 'COUNT';
    const d = frequency === 'A' ? '2025-12-31' : '2026-06-30';
    const label = `${metric} ${group} source label`;
    series[id] = { id, group: 'sec_ipo', section: 'credit', frequency, provider: 'U.S. Securities and Exchange Commission',
      source: 'SEC IPO summary statistics', source_url: sourcePage, data_url: source,
      source_label: label, title: `${label} — ${frequency === 'A' ? 'annual' : 'quarterly'}`,
      units: count ? 'Offerings' : 'US$ millions', dp: count ? 0 : 1, source_key: `Stats Table!B:${frequency}`,
      source_range_heading: 'IPOs: Number and Proceeds of Offerings (2000 - 2025 and 2000:Q1 - 2026:Q2)',
      stats: { last: [d, count ? 375 : 1061.6], vintage: '2026-11-05', published: null } };
  }
  return { generated: '2026-11-05T00:00:00Z', series, calendar: [], changes: [] };
}

function setup(c = catalog(), histories = {}) {
  const dom = new JSDOM('<main></main><aside id="drawer"><div id="dh"></div><div id="db"></div></aside><div id="scrim"></div>',
    { url: 'https://research.example/#macro', runScripts: 'outside-only' });
  const w = dom.window, requests = [];
  w.el = (tag, attrs = {}, ...children) => {
    const node = w.document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
      if (value == null || value === false) continue;
      if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
      else if (key === 'html') node.innerHTML = value;
      else node.setAttribute(key, value === true ? '' : String(value));
    }
    for (const child of children.flat(Infinity)) if (child != null && child !== false) node.append(child.nodeType ? child : String(child));
    return node;
  };
  w.panel = (title, action, ...children) => w.el('section', { class: 'panel' }, w.el('h3', {}, title), action, ...children);
  w.store = { get: (key, fallback) => fallback, set() {} };
  w.page = w.document.querySelector('main');
  w.drawer = w.document.querySelector('#drawer');
  w.scrim = w.document.querySelector('#scrim');
  w.$ = selector => w.document.querySelector(selector);
  w.MACRO = {}; w.E = null; w.DR = { cell: null };
  w.MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  w.pd = d => new Date(d + 'T00:00:00Z'); w.fdate = d => String(d);
  w.hideTip = () => {}; w.closeSource = () => {}; w.svgUse = () => w.el('span');
  w.requestAnimationFrame = callback => callback(); w.css = () => '#000';
  w.getJSON = async path => {
    requests.push(path);
    if (path === 'macro/catalog.json') return structuredClone(c);
    const match = Object.values(c.series).find(m => path.endsWith(m.id.replace(/[^A-Za-z0-9_.-]/g, '_') + '.json'));
    assert.ok(match, `unexpected fixture request ${path}`);
    const obs = histories[match.id] || (match.stats?.last ? [match.stats.last] : []);
    if (path.startsWith('macro/s/')) return { obs, vin: Object.fromEntries(obs.map(([d]) => [d, '2026-11-05'])), retrieved: '2026-11-05T00:00:00Z', published: null };
    if (path.startsWith('macro/snap/')) return { url: source, cols: ['Time Period', match.source_label], keys: obs.map(([d]) => d),
      rows: obs.map(([d, value]) => [match.frequency === 'A' ? d.slice(0, 4) : `${d.slice(0, 4)}:Q${Math.ceil(+d.slice(5, 7) / 3)}`, String(value)]), hl: 1, source_sheet: 'Stats Table' };
    throw new Error(`unexpected fixture request ${path}`);
  };
  w.eval(readFileSync('public/pages/macro.js', 'utf8'));
  return { w, requests, close: () => w.close() };
}

test('all48 source IPO series are discoverable in separate annual and quarterly tables', async () => {
  const h = setup();
  try {
    await h.w.FU_PAGES.macro();
    const rows = [...h.w.page.querySelectorAll('[data-series-id^="SEC_IPO:"]')];
    assert.equal(rows.length, 48);
    assert.equal(new Set(rows.map(row => row.dataset.seriesId)).size, 48);
    const panels = [...h.w.page.querySelectorAll('section.panel')].filter(p => p.querySelector('[data-series-id^="SEC_IPO:"]'));
    assert.equal(panels.length, 2);
    assert.ok(panels.some(p => /annual/i.test(p.querySelector('h3').textContent) && p.querySelectorAll('tbody tr').length === 24));
    assert.ok(panels.some(p => /quarterly/i.test(p.querySelector('h3').textContent) && p.querySelectorAll('tbody tr').length === 24));
    for (const row of rows) {
      assert.match(row.textContent, row.dataset.seriesId.endsWith(':A') ? /2025/ : /Q2 2026/);
      assert.match(row.textContent, row.dataset.seriesId.includes(':COUNT_') ? /Offerings/ : /US\$ millions/);
    }
    assert.match(h.w.page.textContent, /Annual and quarterly periods remain separate/);
    assert.ok([...h.w.page.querySelectorAll('a')].some(a => a.href === source));
    assert.ok([...h.w.page.querySelectorAll('a')].some(a => a.href === sourcePage));
    assert.deepEqual(h.requests, ['macro/catalog.json'], 'catalog rendering does not fetch48 histories');
  } finally { h.close(); }
});

test('a displayed IPO value opens its exact period and original workbook source row', async () => {
  const h = setup();
  try {
    await h.w.FU_PAGES.macro();
    const row = h.w.page.querySelector('[data-series-id="SEC_IPO:TOTAL_PROCEEDS_TOTAL:Q"]');
    assert.ok(row, 'quarterly proceeds row must be rendered');
    assert.match(row.textContent, /1,061\.6/);
    row.querySelector('button').click();
    await new Promise(resolve => setTimeout(resolve, 0));
    assert.match(h.w.drawer.textContent, /Q2 2026/);
    assert.match(h.w.drawer.textContent, /2026:Q2/);
    assert.equal(h.w.drawer.querySelector('td.hit').textContent, '1061.6');
    assert.ok([...h.w.drawer.querySelectorAll('a')].some(a => a.href === source));
    assert.ok(h.requests.includes('macro/s/SEC_IPO_TOTAL_PROCEEDS_TOTAL_Q.json'));
    assert.ok(h.requests.includes('macro/snap/SEC_IPO_TOTAL_PROCEEDS_TOTAL_Q.json'));
    assert.ok(!h.requests.some(path => path.endsWith('_A.json')));
  } finally { h.close(); }
});

test('all rows share the total-count period and missing measures never carry an older value forward', async () => {
  const c = catalog();
  c.series['SEC_IPO:COUNT_FUND:Q'].stats = null;
  c.series['SEC_IPO:TOTAL_PROCEEDS_FUND:Q'].stats.last = ['2025-12-31', 12.5];
  c.series['SEC_IPO:AVERAGE_PROCEEDS_FUND:Q'].stats.last = ['2026-03-31', 315.4];
  const h = setup(c);
  try {
    await h.w.FU_PAGES.macro();
    await new Promise(resolve => setTimeout(resolve, 0));
    assert.equal(h.w.page.querySelectorAll('[data-series-id^="SEC_IPO:"]').length, 48);
    for (const id of ['SEC_IPO:COUNT_FUND:Q', 'SEC_IPO:TOTAL_PROCEEDS_FUND:Q', 'SEC_IPO:AVERAGE_PROCEEDS_FUND:Q']) {
      const missing = h.w.page.querySelector(`[data-series-id="${id}"]`);
      assert.equal(missing.dataset.period, '2026-06-30');
      assert.match(missing.textContent, /Q2 2026/);
      assert.equal(missing.querySelector('td.num').textContent, '–');
      assert.equal(missing.querySelector('button'), null);
      assert.doesNotMatch(missing.textContent, /Q4 2025|Q1 2026|12\.5|315\.4/);
    }
  } finally { h.close(); }
});

test('an exact historical anchor value opens that series and period, while zero remains a number', async () => {
  const c = catalog(), id = 'SEC_IPO:AVERAGE_PROCEEDS_FUND:Q';
  c.series[id].stats.last = ['2026-09-30', 99.9];
  c.series['SEC_IPO:COUNT_FUND:Q'].stats.last[1] = 0;
  const h = setup(c, { [id]: [['2026-03-31', 11.1], ['2026-06-30', 22.2], ['2026-09-30', 99.9]] });
  try {
    await h.w.FU_PAGES.macro();
    await new Promise(resolve => setTimeout(resolve, 0));
    const row = h.w.page.querySelector(`[data-series-id="${id}"]`);
    assert.equal(row.dataset.period, '2026-06-30');
    assert.match(row.textContent, /Q2 2026/);
    assert.equal(row.querySelector('button').textContent, '22.2');
    assert.equal(h.w.page.querySelector('[data-series-id="SEC_IPO:COUNT_FUND:Q"] button').textContent, '0');
    row.querySelector('button').click();
    await new Promise(resolve => setTimeout(resolve, 0));
    assert.equal(h.w.drawer.querySelector('td.hit').textContent, '22.2');
    assert.match(h.w.drawer.textContent, /2026:Q2/);
    assert.ok(h.requests.includes('macro/snap/SEC_IPO_AVERAGE_PROCEEDS_FUND_Q.json'));
  } finally { h.close(); }
});

test('an existing catalog without the IPO source has no empty IPO panels', async () => {
  const h = setup({ generated: '2026-11-05T00:00:00Z', series: {}, calendar: [], changes: [] });
  try {
    await h.w.FU_PAGES.macro();
    assert.equal(h.w.page.querySelectorAll('[data-series-id^="SEC_IPO:"]').length, 0);
    assert.doesNotMatch(h.w.page.textContent, /IPO summary statistics/);
  } finally { h.close(); }
});
