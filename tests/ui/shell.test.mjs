import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { JSDOM } from 'jsdom';

const app = readFileSync('public/app.js', 'utf8');
const shell = app.slice(app.indexOf('const PAGES = ['), app.indexOf('function loadingView('));
const router = app.slice(app.indexOf('let ROUTE_SEQ = 0;'), app.indexOf('addEventListener("hashchange", route);'));

function setup() {
  const dom = new JSDOM('<main id="page"></main><nav id="nav"></nav><nav id="mnav"></nav><div id="wl"></div><nav id="market-tape"></nav><span id="asof"></span><span id="built"></span><button id="kbhelp"></button><button id="kbclose"></button><div id="kbsheet"></div><input id="q">', { url: 'https://research.example/#company-aapl', runScripts: 'outside-only' });
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
  w.eval(shell + router + '\nPAGES.push({id:"analyst",name:"Morning brief",key:"a",icon:"i-home"},{id:"watchlists",name:"Watchlists",key:"l",icon:"i-feed"},{id:"ideas",name:"Research ideas",key:"i",icon:"i-ag"},{id:"future",name:"Future page",key:"z",icon:"i-home"});');
  return { w, close: () => w.close() };
}

test('grouped navigation retains all visible routes, fallback entries and mobile links', () => {
  const h = setup();
  try {
    const { w } = h;
    w.buildShell();
    assert.deepEqual(Array.from(w.document.querySelectorAll('.nav-heading'), el => el.textContent), ['Research', 'Markets', 'Workspace']);
    const desktop = Array.from(w.document.querySelectorAll('#nav a'), el => el.getAttribute('href'));
    const mobile = Array.from(w.document.querySelectorAll('#mnav a'), el => el.getAttribute('href'));
    assert.equal(new Set(desktop).size, desktop.length);
    assert.deepEqual([...desktop].sort(), [...mobile].sort());
    assert.ok(desktop.includes('#future'));
    assert.ok(desktop.includes('#workbench'));
    assert.ok(desktop.includes('#research'));
    assert.ok(!desktop.includes('#coverage'));
    assert.match(w.document.querySelector('#asof').textContent, /^Close 2026-11-03$/);
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
      const navState = selection('[data-p]');
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
      assert.deepEqual(selection('[data-p]'), navState, hash + ' nav after watchlist rerender');
    }
  } finally { h.close(); }
});
