import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { JSDOM } from 'jsdom';

async function setup() {
  const dom = new JSDOM('<main id="page"></main>', { url: 'https://research.example/#analyst', runScripts: 'outside-only' });
  const w = dom.window;
  w.page = w.document.querySelector('#page');
  w.el = (tag, attrs = {}, ...children) => {
    const node = w.document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
      if (value == null || value === false) continue;
      if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
      else node.setAttribute(key, value === true ? '' : value);
    }
    for (const child of children.flat()) if (child != null && child !== false) node.append(child.nodeType ? child : String(child));
    return node;
  };
  w.IDX = { AMD: { name: 'AMD', sector: 'Technology' }, EMPTY: { name: 'No history' }, FAIL: { name: 'Unavailable' } };
  w.TICKERS = Object.keys(w.IDX);
  w.SHORT = { AMD: 'AMD', EMPTY: 'No history', FAIL: 'Unavailable' };
  w.PAGES = [];
  w.PX = {};
  w.watchlist = () => w.TICKERS;
  w.store = {
    get: (key, fallback) => JSON.parse(w.localStorage.getItem('fu:' + key) || 'null') ?? fallback,
    set: (key, value) => w.localStorage.setItem('fu:' + key, JSON.stringify(value)),
  };
  w.store.set('analyst:briefSince', '2026-10-20');
  w.toast = message => { w.lastToast = message; };
  w.route = () => {};
  w.cls = value => value >= 0 ? 'up' : 'down';
  w.fmtMoney = value => '$' + value;
  w.openSource = value => { w.openedSource = value; };
  w.ensureCompany = async () => ({});
  w.FU_WORKSPACE = {
    openModel: ticker => { w.modelTicker = ticker; },
    openReport: (ticker, report) => { w.reportDraft = { ticker, report }; },
  };
  const record = (end, start, value, filed) => ({ end, start, v: value, filed, accn: '0000000001-26-000001' });
  w.getJSON = async path => {
    if (path === 'companies/FAIL.json') throw new Error('Test source unavailable');
    if (path === 'companies/EMPTY.json') return { ticker: 'EMPTY', cik: 2, filings: [], statements: { income: [], balance: [], cashflow: [] } };
    return {
      ticker: 'AMD', cik: 1,
      filings: [{ f: '10-Q', d: '2026-11-01', r: '2026-09-30', a: '0000000001-26-000001' }],
      statements: {
        income: [
          { label: 'Revenue', unit: 'USD', annual: [], quarterly: [record('2025-09-30', '2025-07-01', 100000000, '2025-11-01'), record('2026-09-30', '2026-07-01', 120000000, '2026-11-01')] },
          { label: 'Operating income', unit: 'USD', annual: [], quarterly: [record('2025-09-30', '2025-07-01', 20000000, '2025-11-01'), record('2026-09-30', '2026-07-01', 30000000, '2026-11-01')] },
        ], balance: [], cashflow: [],
      },
    };
  };
  for (const path of ['public/lib/evidence.js', 'public/lib/analyst.js', 'public/pages/analyst.js']) w.eval(readFileSync(path, 'utf8'));
  await w.FU_PAGES.analyst();
  return { w, close: () => dom.window.close() };
}

test('brief overview preserves exact evidence, percentage-point changes, and accessible detail actions', async () => {
  const h = await setup();
  try {
    const { w } = h;
    const row = w.document.querySelector('[data-ticker="AMD"]');
    assert.ok(row);
    assert.match(row.textContent, /Quarter ended 2026-09-30/);
    assert.match(row.textContent, /Available 2026-11-01/);
    assert.match(row.textContent, /\+20\.0%/);
    assert.match(row.textContent, /5\.00 pp/);
    assert.match(row.textContent, /1 new/);
    assert.match(row.textContent, /Since 2026-10-20/);

    const toggle = row.querySelector('[aria-expanded]');
    const detail = w.document.getElementById(toggle.getAttribute('aria-controls'));
    assert.equal(detail.hidden, true);
    row.querySelector('.an-value').click();
    assert.equal(w.openedSource.v, 120000000);
    assert.equal(w.openedSource.unit, 'USD');
    assert.equal(w.openedSource.end, '2026-09-30');
    assert.equal(toggle.getAttribute('aria-expanded'), 'false', 'source inspection must not expand the company row');

    toggle.click();
    assert.equal(toggle.getAttribute('aria-expanded'), 'true');
    assert.equal(detail.hidden, false);
    assert.equal(detail.querySelector('[role="region"]').getAttribute('aria-label'), 'AMD quarterly details');
    assert.match(detail.textContent, /Quarterly changes/);
    assert.match(detail.textContent, /Free cash flow/);
    assert.ok(detail.querySelector('a[href^="https://www.sec.gov/"]'), 'the original filing remains linked');
    const buttons = [...detail.querySelectorAll('button')];
    buttons.find(button => button.textContent === 'Build model').click();
    assert.equal(w.modelTicker, 'AMD');
    buttons.find(button => button.textContent === 'Draft report').click();
    assert.equal(w.reportDraft.ticker, 'AMD');
    assert.ok(w.reportDraft.report.evidence.length > 0);
    const count = detail.querySelectorAll('.an-table').length;
    toggle.click();
    assert.equal(detail.hidden, true);
    toggle.click();
    assert.equal(detail.querySelectorAll('.an-table').length, count, 'reopening must not duplicate details');
  } finally { h.close(); }
});

test('brief overview keeps unavailable figures and incomplete watchlist coverage explicit', async () => {
  const h = await setup();
  try {
    const { w } = h;
    assert.equal(w.document.querySelectorAll('.an-overview-row').length, 2);
    assert.match(w.page.textContent, /Could not load: FAIL\. The brief does not cover these companies\./);
    const row = w.document.querySelector('[data-ticker="EMPTY"]');
    assert.match(row.textContent, /Reporting period unavailable/);
    assert.match(row.textContent, /date unavailable/);
    assert.match(row.textContent, /No new activity/);
    assert.equal(row.querySelectorAll('.an-value').length, 0);
    assert.equal(row.querySelectorAll('.an-missing').length, 4);
    row.querySelector('[aria-expanded]').click();
    const detail = w.document.querySelector('#brief-detail-empty');
    assert.equal(detail.hidden, false);
    assert.match(detail.textContent, /No new filings or earnings releases in this window/);
    assert.equal(detail.querySelectorAll('.an-value').length, 0);
  } finally { h.close(); }
});
