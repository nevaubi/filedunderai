import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { JSDOM } from 'jsdom';

async function setup({ minimal = false } = {}) {
  const dom = new JSDOM('<main id="page"></main>', { url: 'https://research.example/#company/TEST', runScripts: 'outside-only' });
  const w = dom.window;
  w.page = w.document.querySelector('#page');
  w.$ = selector => w.document.querySelector(selector);
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
  w.IDX = { TEST: { name: 'Test Corporation', sector: 'Technology' } };
  w.SHORT = minimal ? {} : { TEST: 'Test' };
  w.IR = minimal ? {} : { TEST: 'https://ir.example/' };
  w.WEB = minimal ? {} : { TEST: 'example.com' };
  w.PX = {};
  w.STATE = { coTab: 'overview' };
  w.store = { set: (key, value) => { w.persisted = [key, value]; } };
  w.ensureCompany = async () => {};
  const record = { tk: 'TEST', label: 'Revenue', v: 120000000, rec: { accn: 'source-10q', end: '2026-09-30' } };
  w.snapshot = () => ({ c: minimal ? {} : {
    name: 'TEST CORPORATION', cik: 1234, exchanges: ['Nasdaq', 'Nasdaq'], sic: '7372', sicDescription: 'Software',
    address: { street1: '100 MAIN STREET', street2: 'SUITE 200', city: 'BOSTON', stateOrCountry: 'MA', zipCode: '02110' },
    phone: '617-555-0100', fiscalYearEnd: '1231', stateOfIncorporation: 'DE',
    filings: [{ f: '10-Q', a: 'q-accession' }, { f: '10-K', a: 'k-accession' }],
  }, px: 42.5, mcap: 1000000000, rev: record, ni: record, eps: { ...record, v: 2.5 }, pe: 17, ps: 8, fcfy: 2, fcf: record, gm: { ...record, v: 30 }, growth: { ...record, v: 20 } });
  w.priceStats = () => ({ date: '2026-10-30', chg1d: 1.25, hi52: 50, lo52: 30 });
  w.logoTile = () => w.el('div', { class: 'logo' }, 'T');
  w.filingUrl = (ticker, filing) => 'https://filings.example/' + filing.a;
  w.watchlist = () => [];
  w.toggleWatch = ticker => { w.watched = ticker; return true; };
  w.openSource = cell => { w.openedSource = cell; };
  w.fdate = value => value;
  w.fnum = value => String(value);
  w.fsgn = value => '+' + value + '%';
  w.fpct = value => value == null ? '–' : value + '%';
  w.fmtMoney = value => value == null ? '–' : '$' + value;
  w.cls = () => 'up';
  w.css = () => '#222';
  w.sparkSVG = () => w.el('svg');
  w.svgUse = () => w.el('svg');
  w.RESEARCH = { ask: prompt => { w.briefPrompt = prompt; } };
  w.setTimeout = callback => callback();
  for (const id of ['Overview', 'Financials', 'Segments', 'News', 'Filings', 'Earnings', 'Insiders']) {
    w['co' + id] = (body, ticker) => { body.append(w.el('div', {}, id + ' ' + ticker)); };
  }
  w.FU_COTABS = [{ id: 'valuation', name: 'Valuation', after: 'financials', render: (body, ticker) => { body.textContent = 'Valuation ' + ticker; } }];
  const app = readFileSync('public/app.js', 'utf8');
  w.eval(app.slice(app.indexOf('pCompany.prep = null;'), app.indexOf('function coNews(body, tk)')) + '\nwindow.renderCompany = pCompany;');
  await w.renderCompany('TEST');
  return { w, record, close: () => dom.window.close() };
}

test('company presentation preserves official details, actual quote date, filing actions, watch and brief', async () => {
  const h = await setup();
  try {
    const { w } = h;
    assert.equal(w.document.querySelector('.co-title-row h1').textContent, 'Test');
    assert.match(w.document.querySelector('.co-ticker').textContent, /^Nasdaq: TEST$/);
    assert.match(w.document.querySelector('.co-registrant').textContent, /TEST CORPORATION/);
    const profile = w.document.querySelector('details');
    assert.equal(profile.open, false);
    profile.open = true;
    for (const value of ['100 Main Street', 'Suite 200', 'Boston', '02110', '617-555-0100', '0000001234', 'DE', 'example.com']) assert.ok(profile.textContent.includes(value), value);
    assert.match(w.document.querySelector('.co-price-date').textContent, /2026-10-30/);
    assert.equal(w.document.querySelector('.co-quote .big').textContent, '$42.5');
    for (const accession of ['q-accession', 'k-accession']) assert.ok(w.document.querySelector(`.co-actions a[href="https://filings.example/${accession}"]`));
    assert.ok(w.document.querySelector('.co-actions a[href="https://ir.example/"]'));
    assert.ok(w.document.querySelector('.co-actions a[href*="CIK=1234"]'));
    w.document.querySelector('#star').click();
    assert.equal(w.watched, 'TEST');
    assert.ok(w.document.querySelector('#star').classList.contains('pri'));
    [...w.document.querySelectorAll('.co-actions button')].find(node => node.textContent.includes('Write a brief')).click();
    assert.equal(w.location.hash, '#research');
    assert.match(w.briefPrompt, /one-page brief on TEST/);
  } finally { h.close(); }
});

test('company metric buttons retain exact source records and extension tabs retain state and rendering', async () => {
  const h = await setup();
  try {
    const { w, record } = h;
    const source = w.document.querySelector('.co-stats button[aria-label^="Revenue TTM:"]');
    assert.ok(source, 'source metrics are native keyboard-accessible buttons');
    source.click();
    assert.equal(w.openedSource, record);
    assert.equal(w.openedSource.rec.accn, 'source-10q');
    const tabs = [...w.document.querySelectorAll('[role="tab"]')];
    assert.deepEqual(tabs.map(node => node.dataset.id), ['overview', 'financials', 'valuation', 'segments', 'news', 'earnings', 'filings', 'insiders']);
    tabs[2].click();
    assert.equal(w.STATE.coTab, 'valuation');
    assert.deepEqual(w.persisted, ['coTab', 'valuation']);
    assert.equal(w.document.querySelector('#crumb-tab').textContent, 'Valuation');
    assert.equal(tabs[2].getAttribute('aria-selected'), 'true');
    assert.equal(tabs[0].getAttribute('aria-selected'), 'false');
    assert.equal(w.document.querySelector('.co-body').textContent, 'Valuation TEST');
    assert.equal(w.document.querySelector('.co-stats'), null, 'other tabs start immediately without the overview metrics');
    tabs[0].click();
    assert.ok(w.document.querySelector('.co-body .co-stats'), 'Overview restores the metrics');
    w.document.querySelector('.co-stats button[aria-label^="Revenue TTM:"]').click();
    assert.equal(w.openedSource, record, 'the restored metric keeps its source callback');
  } finally { h.close(); }
});

test('company profile handles absent optional metadata and filings without losing research tabs', async () => {
  const h = await setup({ minimal: true });
  try {
    const { w } = h;
    assert.equal(w.document.querySelector('.co-title-row h1').textContent, 'Test Corporation');
    assert.match(w.document.querySelector('.co-profile').textContent, /Unavailable/);
    assert.equal(w.document.querySelectorAll('.co-actions a[href^="https://filings.example/"]').length, 0);
    assert.ok(w.document.querySelector('#star'));
    assert.equal(w.document.querySelectorAll('[role="tab"]').length, 8);
    assert.match(w.document.querySelector('.co-body').textContent, /Overview TEST$/);
  } finally { h.close(); }
});
