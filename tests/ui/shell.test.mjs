import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { JSDOM } from 'jsdom';

const app = readFileSync('public/app.js', 'utf8');
const shell = app.slice(app.indexOf('const PAGES = ['), app.indexOf('function loadingView('));
const router = app.slice(app.indexOf('let ROUTE_SEQ = 0;'), app.indexOf('addEventListener("hashchange", route);'));

function setup() {
  const dom = new JSDOM('<main id="page"></main><nav id="nav"></nav><nav id="mnav"></nav><nav id="hubbar" hidden></nav><div id="wl"></div><nav id="market-tape"></nav><span id="asof"></span><span id="built"></span><button id="kbhelp"></button><button id="kbclose"></button><div id="kbsheet"></div><input id="q">', { url: 'https://research.example/#company-aapl', runScripts: 'outside-only' });
  const w = dom.window;
  w.$ = selector => w.document.querySelector(selector);
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
  w.IDX = { AAPL: { name: 'Apple', price: 123.45, chg_1d: .0123 }, MSFT: { name: 'Microsoft', price: null, chg_1d: null } };
  w.PX = {};
  w.eval(app.slice(app.indexOf('function companySummary('), app.indexOf('async function getJSON(')));
  const values = { watch: ['AAPL', 'MSFT'] };
  w.store = { get: (key, fallback) => values[key] ?? fallback, set: (key, value) => { values[key] = value; } };
  w.miniLogo = ticker => w.el('span', { class: 'mlogo', 'aria-hidden': 'true' }, ticker[0]);
  w.svgUse = () => w.el('svg');
  w.fnum = (value, places = 2) => value == null || !Number.isFinite(value) ? '–' : value.toFixed(places);
  w.fsgn = (value, places = 1) => value == null || !Number.isFinite(value) ? '–' : (value >= 0 ? '+' : '−') + Math.abs(value * 100).toFixed(places) + '%';
  w.cls = value => value > 0 ? 'up' : value < 0 ? 'down' : '';
  w.bars = { SPY: { t: new Date('2026-11-03T00:00:00Z'), c: 766.81 }, QQQ: { t: new Date('2026-11-02T00:00:00Z'), c: 691.95 } };
  w.last = symbol => w.bars[symbol];
  w.changes = { SPY: .0165, QQQ: -.0097 };
  w.dayChg = symbol => w.changes[symbol];
  w.SYMNAME = { SPY: 'S&P 500', QQQ: 'Nasdaq 100', IWM: 'Russell 2000' };
  w.d3 = { utcFormat: () => date => date.toISOString().slice(0, 10) };
  w.D = { generated: '2026-11-04T15:00:00Z' };
  w.openShortcuts = w.openPalette = w.setupPalette = w.setupKeys = w.toast = () => {};
  w.closeSource = w.hideTip = w.loadingView = w.scrollTo = () => {};
  w.SHORT = { AAPL: 'Apple' };
  for (const name of ['pMarkets', 'pScreener', 'pCompare', 'pFeed', 'pTechnicals', 'pSectors', 'pMacro', 'pTheses', 'pWorkbench', 'pResearch', 'pCoverage']) w[name] = () => {};
  w.pCompany = ticker => { w.document.querySelector('#page').textContent = 'Company ' + ticker; };
  w.FU_PAGES = { technicals: symbol => { w.document.querySelector('#page').textContent = 'Technicals ' + symbol; } };
  for (const id of ['analyst', 'watchlists', 'ideas', 'theses']) w.FU_PAGES[id] = () => { w.document.querySelector('#page').textContent = id; };
  w.FU_PAGES.future = () => { w.document.querySelector('#page').textContent = 'Future'; };
  w.FU_PAGES.compare = arg => { w.document.querySelector('#page').textContent = 'Compare ' + (arg || ''); };
  w.FU_PAGES.sectors = arg => { w.document.querySelector('#page').textContent = 'Sectors ' + (arg || ''); };
  w.FU_PAGES.kpis = arg => { w.document.querySelector('#page').textContent = 'KPIs ' + (arg || ''); };
  w.eval(shell + router + '\nPAGES.push({id:"future",name:"Future page",key:"z",icon:"i-home"});');
  return { w, close: () => w.close() };
}

test('navigation is one entry per hub, mirrored on mobile, with module pages kept as fallback entries', () => {
  const h = setup();
  try {
    const { w } = h;
    w.buildShell();
    assert.equal(w.document.querySelectorAll('.nav-heading').length, 0);
    const desktop = Array.from(w.document.querySelectorAll('#nav a'), a => [a.dataset.hub, a.getAttribute('href'), a.textContent]);
    assert.deepEqual(desktop, [
      ['home', '#markets', 'Home'], ['companies', '#companies', 'Companies'], ['screen', '#screener', 'Screen & compare'],
      ['technicals', '#technicals', 'Technicals'], ['supply', '#supply', 'Supply chain'], ['macro', '#macro', 'Macro & sectors'],
      ['workspace', '#theses', 'Workspace'], ['future', '#future', 'Future page'],
    ]);
    const mobile = Array.from(w.document.querySelectorAll('#mnav a'), a => [a.dataset.hub, a.getAttribute('href')]);
    assert.deepEqual(mobile, desktop.map(([hub, href]) => [hub, href]));
    for (const hidden of ['#research', '#coverage', '#compare', '#kpis']) assert.ok(!desktop.some(([, href]) => href === hidden), hidden);
    w.buildShell();
    assert.equal(w.document.querySelectorAll('#nav a').length, desktop.length, 'rebuilding does not duplicate entries');
    assert.match(w.document.querySelector('#asof').textContent, /^Close 2026-11-03$/);
  } finally { h.close(); }
});

test('hub tabs: every old route resolves to its hub and tab, deep links keep their arguments, aliases and unknown ids redirect', async () => {
  const h = setup();
  try {
    const { w } = h;
    w.buildShell();
    const cases = [
      ['#markets', 'home', 'markets', 'Markets,Morning brief,Watchlist,Filings feed', null],
      ['#analyst', 'home', 'analyst', 'Markets,Morning brief,Watchlist,Filings feed', null],
      ['#watchlists', 'home', 'watchlists', null, null],
      ['#feed', 'home', 'feed', null, null],
      ['#compare', 'screen', 'compare', 'Screener,Compare,KPI peers,KPI matrix,Research ideas', null],
      ['#compare-AAPL,MSFT', 'screen', 'compare', null, null, 'Compare AAPL,MSFT'],
      ['#kpis-revenue_growth/aapl', 'screen', 'kpis', null, null, 'KPIs revenue_growth/aapl'],
      ['#ideas', 'screen', 'ideas', null, null],
      ['#screener', 'screen', 'screener', null, null],
      ['#sectors', 'macro', 'sectors', 'Macro,Sectors', null],
      ['#sectors-semiconductors', 'macro', 'sectors', null, null, 'Sectors semiconductors'],
      ['#macro', 'macro', 'macro', null, null],
      ['#theses', 'workspace', 'theses', 'Theses,Workbench', null],
      ['#workbench', 'workspace', 'workbench', null, null],
      ['#technicals-SPY', 'technicals', null, '', null, 'Technicals SPY'],
      ['#supply', 'supply', null, '', null],
      ['#research', null, null, '', null],
      ['#coverage', null, null, '', null],
      ['#home', 'home', 'markets', null, '#markets'],
      ['#screen', 'screen', 'screener', null, '#screener'],
      ['#workspace', 'workspace', 'theses', null, '#theses'],
      ['#watchlist', 'home', 'watchlists', null, '#watchlists'],
      ['#not-a-page', 'home', 'markets', null, '#markets'],
      ['#future', 'future', null, '', null, 'Future'],
    ];
    for (const [hash, hub, tab, tabs, redirect, text] of cases) {
      w.history.replaceState(null, '', hash);
      await w.route();
      const nav = Array.from(w.document.querySelectorAll('#nav a.on'), a => a.dataset.hub);
      assert.deepEqual(nav, hub ? [hub] : [], hash + ' nav');
      assert.deepEqual(Array.from(w.document.querySelectorAll('#mnav a.on'), a => a.dataset.hub), nav, hash + ' mobile nav');
      const bar = w.document.querySelector('#hubbar');
      if (tab) {
        assert.equal(bar.hidden, false, hash + ' hub bar shown');
        assert.equal(bar.querySelector('a.on').getAttribute('href'), '#' + tab, hash + ' active tab');
        assert.equal(bar.querySelector('a.on').getAttribute('aria-current'), 'page');
        assert.equal(bar.querySelectorAll('a[aria-current]').length, 1);
        if (tabs) assert.equal(Array.from(bar.querySelectorAll('a'), a => a.textContent).join(','), tabs, hash + ' tab list');
      } else assert.equal(bar.hidden, true, hash + ' single-page hubs have no tab bar');
      if (redirect) assert.equal(w.location.hash, redirect, hash + ' redirect');
      else assert.equal(w.location.hash, hash, hash + ' keeps its address');
      if (text) assert.equal(w.document.querySelector('#page').textContent, text, hash + ' arguments');
    }
  } finally { h.close(); }
});

test('the supply chain route shows a tidy empty state until its module registers', async () => {
  const h = setup();
  try {
    const { w } = h;
    w.page = w.document.querySelector('#page');
    w.buildShell();
    w.history.replaceState(null, '', '#supply');
    await w.route();
    assert.match(w.document.querySelector('#page').textContent, /Supply chain data is not available yet/);
    w.FU_PAGES.supply = () => { w.document.querySelector('#page').textContent = 'Supply module'; };
    await w.route();
    assert.equal(w.document.querySelector('#page').textContent, 'Supply module');
    w.history.replaceState(null, '', '#supply-AAPL');
    await w.route();
    assert.equal(w.location.hash, '#supply-AAPL');
  } finally { h.close(); }
});

test('header quote tape preserves closing values, dates and routes without inventing absent data', () => {
  const h = setup();
  try {
    const { w } = h;
    w.renderMarketTape();
    const links = w.document.querySelectorAll('#market-tape a');
    assert.equal(links.length, 2);
    assert.equal(links[0].getAttribute('href'), '#technicals-SPY');
    assert.match(links[0].textContent, /SPY766\.81\+1\.65%/);
    assert.match(links[0].title, /close 2026-11-03/);
    assert.match(links[1].textContent, /QQQ691\.95−0\.97%/);
    assert.match(links[1].title, /close 2026-11-02/);
    assert.ok(!w.document.querySelector('#market-tape').textContent.includes('Live'));
    w.changes.SPY = Infinity;
    w.bars.QQQ.c = NaN;
    w.renderMarketTape();
    assert.equal(w.document.querySelectorAll('#market-tape a').length, 1);
    assert.match(w.document.querySelector('#market-tape').textContent, /766\.81–$/);
    assert.equal(w.document.querySelector('#market-tape a .up'), null);
  } finally { h.close(); }
});

test('watchlist keeps the selected company and shows missing prices explicitly after rerender', () => {
  const h = setup();
  try {
    const { w } = h;
    w.renderRail();
    const selected = w.document.querySelector('#wl [data-t="AAPL"]');
    assert.equal(selected.getAttribute('href'), '#company-aapl');
    assert.equal(selected.getAttribute('aria-current'), 'page');
    assert.equal(selected.querySelector('.price').textContent, '$123.45');
    assert.equal(selected.querySelector('.p').textContent, '+1.2%');
    assert.equal(w.document.querySelector('#wl [data-t="MSFT"] .price').textContent, '–');
    assert.equal(w.document.querySelector('#wl [data-t="MSFT"] .p').textContent, '–');
    w.toggleWatch('MSFT');
    assert.equal(w.document.querySelectorAll('#wl a').length, 1);
    assert.equal(w.document.querySelector('#wl a').getAttribute('aria-current'), 'page');
  } finally { h.close(); }
});


test('quote tape reports an unavailable date for a null timestamp while preserving its closing price', () => {
  const h = setup();
  try {
    const { w } = h;
    w.bars.SPY.t = null;
    w.renderMarketTape();
    const quote = w.document.querySelector('#market-tape a[href="#technicals-SPY"]');
    assert.match(quote.textContent, /SPY766\.81\+1\.65%/);
    assert.match(quote.title, /close date unavailable$/);
    assert.match(quote.getAttribute('aria-label'), /date unavailable$/);
    assert.ok(!quote.title.includes('1970'));
  } finally { h.close(); }
});

test('a watched company outside the screener uses its loaded close without joining the index', () => {
  const h = setup();
  try {
    const { w } = h;
    w.FU_COMPANIES = { get: ticker => ticker === 'AAON' ? { ticker, name: 'AAON', screener_eligible: false } : null };
    w.PX.AAON = { bars: [{ t: new Date('2026-11-03'), c: 60 }, { t: new Date('2026-11-04'), c: 58.25 }] };
    w.toggleWatch('AAON');
    assert.equal(w.document.querySelector('#wl [data-t="AAON"] .price').textContent, '$58.25');
    assert.equal(w.document.querySelector('#wl [data-t="AAON"] .p').textContent, '−2.9%');
    assert.equal(w.IDX.AAON, undefined);
    assert.equal(w.companySummary('AAON').screener_eligible, false);
  } finally { h.close(); }
});

test('actual routing keeps technicals and company navigation consistent when the watchlist rerenders', async () => {
  const h = setup();
  try {
    const { w } = h;
    w.buildShell();
    const selection = selector => Array.from(w.document.querySelectorAll(selector), a => ({
      href: a.getAttribute('href'), on: a.classList.contains('on'), current: a.getAttribute('aria-current'),
    }));
    for (const [hash, page, nav, selectedCompany] of [
      ['#technicals-AAPL', 'Technicals AAPL', 'technicals', null],
      ['#company-aapl', 'Company AAPL', 'companies', 'AAPL'],
      ['#technicals-AAPL', 'Technicals AAPL', 'technicals', null],
    ]) {
      w.history.replaceState(null, '', hash);
      await w.route();
      assert.equal(w.document.querySelector('#page').textContent, page);
      const navState = selection('[data-hub]');
      for (const link of navState) {
        assert.equal(link.on, link.href === '#' + nav, hash + ' nav class');
        assert.equal(link.current, link.on ? 'page' : null, hash + ' nav aria-current');
      }
      const watchState = selection('#wl a');
      for (const link of watchState) {
        const current = selectedCompany !== null && link.href === '#company-' + selectedCompany.toLowerCase();
        assert.equal(link.on, current, hash + ' watchlist class');
        assert.equal(link.current, current ? 'page' : null, hash + ' watchlist aria-current');
      }
      w.renderRail();
      assert.deepEqual(selection('#wl a'), watchState, hash + ' watchlist rerender');
      assert.deepEqual(selection('[data-hub]'), navState, hash + ' nav after watchlist rerender');
    }
  } finally { h.close(); }
});
