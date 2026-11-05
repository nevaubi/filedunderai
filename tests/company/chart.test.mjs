import test from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { JSDOM } from 'jsdom';

const file = 'public/lib/company-chart.js';
const px = bars => ({ meta: { symbol: 'TEST', currency: 'USD' }, bars });
const bar = (date, c, v = 1000) => ({ t: new Date(date + 'T14:30:00Z'), o: c - 1, h: c + 2, l: c - 2, c, v });

function setup({ technicals = true } = {}) {
  const dom = new JSDOM('<main><div id="chart"></div></main>', { runScripts: 'outside-only', pretendToBeVisual: true });
  const w = dom.window;
  if (technicals) w.eval(readFileSync('public/pages/technicals-lib.js', 'utf8'));
  if (existsSync(file)) w.eval(readFileSync(file, 'utf8'));
  assert.equal(typeof w.FU_COMPANY_CHART?.render, 'function', 'the reusable company chart must expose render');
  const container = w.document.querySelector('#chart');
  return {
    w, container,
    render(prices, extras = {}) { return w.FU_COMPANY_CHART.render(container, { ticker: 'TEST', companyName: 'Test Corporation', prices, ...extras }); },
    q: selector => container.querySelector(selector),
    button(label) { return [...container.querySelectorAll('button')].find(node => node.textContent.trim() === label); },
    close: () => dom.window.close(),
  };
}

function key(h, value) {
  h.q('.fcc-plot').dispatchEvent(new h.w.KeyboardEvent('keydown', { key: value, bubbles: true }));
}

test('chart preserves supplied split-adjusted OHLC units and actual dates instead of metadata quotes or adjusted close', () => {
  const h = setup();
  try {
    const prices = px([bar('2025-11-04', 120, 45000), bar('2025-11-03', 100, 20000)]);
    prices.meta.regularMarketPrice = 9999;
    prices.meta.retrieved = '2025-11-05T03:00:00Z';
    prices.splits = [['2025-11-04', 2]];
    prices.bars[1].adj_close = 50;
    const before = JSON.stringify(prices);
    h.render(prices);
    assert.equal(h.q('.fcc-latest-value').textContent, '$120.00');
    assert.match(h.q('.fcc-asof').textContent, /2025-11-04/);
    assert.match(h.q('.fcc-basis').textContent, /split.adjusted/i);
    assert.match(h.q('.fcc-basis').textContent, /dividends excluded/i);
    assert.match(h.q('.fcc-source').textContent, /2025-11-05/);
    key(h, 'Home');
    assert.equal(h.q('[data-field="close"]').textContent, '$100.00');
    assert.equal(h.q('[data-field="open"]').textContent, '$99.00');
    assert.equal(h.q('[data-field="high"]').textContent, '$102.00');
    assert.equal(h.q('[data-field="low"]').textContent, '$98.00');
    assert.equal(h.q('[data-field="volume"]').textContent, '20,000');
    assert.match(h.q('[data-field="date"]').textContent, /2025-11-03/);
    assert.equal(JSON.stringify(prices), before, 'normalization must not mutate cached shared price data');
    assert.doesNotMatch(h.container.textContent, /9,999|\$50\.00/);
  } finally { h.close(); }
});

test('raw prices and technicals columnar history render the same last observed close', () => {
  const fixtures = [
    { meta: { currency: 'USD' }, d: [[1762171200, 99, 102, 98, 100, 20000], [1762257600, 119, 122, 118, 120, 45000]] },
    { meta: { currency: 'USD' }, d: ['2025-11-03', '2025-11-04'], o: [99, 119], h: [102, 122], l: [98, 118], c: [100, 120], v: [20000, 45000] },
  ];
  for (const fixture of fixtures) {
    const h = setup();
    try {
      h.render(fixture);
      assert.equal(h.q('.fcc-latest-value').textContent, '$120.00');
      assert.match(h.q('.fcc-asof').textContent, /2025-11-04/);
      assert.ok(h.q('[data-series="candles"]'));
      assert.equal(h.q('[data-field="volume"]').textContent, '45,000');
    } finally { h.close(); }
  }
});

test('daily technical indicators keep their complete-history warm-up when the visible range changes', () => {
  const h = setup();
  try {
    const bars = [];
    let date = new Date('2024-01-02T14:30:00Z');
    for (let i = 1; i <= 300; date = new Date(+date + 864e5)) {
      if ([0, 6].includes(date.getUTCDay())) continue;
      bars.push({ t: date, o: i, h: i + 1, l: i - 0.5, c: i, v: 1000 + i });
      i++;
    }
    h.render(px(bars));
    assert.equal(h.q('[data-metric="sma50"] .fcc-metric-value').textContent, '$275.50');
    assert.equal(h.q('[data-metric="sma200"] .fcc-metric-value').textContent, '$200.50');
    assert.equal(h.q('[data-metric="rsi"] .fcc-metric-value').textContent, '100.0');
    h.button('1M').click();
    assert.equal(h.button('1M').getAttribute('aria-pressed'), 'true');
    assert.ok(h.q('[data-series="sma200"]'), '200-day average remains available within a one-month view');
    assert.equal(h.q('[data-metric="sma200"] .fcc-metric-value').textContent, '$200.50');
    assert.match(h.q('.fcc-range-summary').textContent, /2025-02-24/);
    assert.doesNotMatch(h.q('.fcc-range-summary').textContent, /2026/);
    const rsi = h.q('input[data-indicator="rsi"]');
    rsi.click();
    assert.equal(h.q('[data-pane="rsi"]'), null);
    const macd = h.q('input[data-indicator="macd"]');
    macd.click();
    assert.ok(h.q('[data-pane="macd"]'));
    h.button('Line').click();
    assert.ok(h.q('[data-series="price"]'));
    assert.equal(h.q('[data-series="candles"]'), null);
    for (const node of h.container.querySelectorAll('svg [d], svg [x], svg [y]')) {
      assert.doesNotMatch([...node.attributes].map(a => a.value).join(' '), /NaN|Infinity/);
    }
  } finally { h.close(); }
});

test('SPY comparison rebases on shared actual sessions and does not invent missing benchmark observations', () => {
  const h = setup();
  try {
    h.render(px([bar('2025-11-03', 100), bar('2025-11-04', 110), bar('2025-11-05', 120), bar('2025-11-06', 130)]), {
      benchmarkPrices: { meta: { symbol: 'SPY', currency: 'USD' }, bars: [bar('2025-11-04', 200), bar('2025-11-06', 220)] },
    });
    h.q('input[data-indicator="benchmark"]').click();
    assert.ok(h.q('[data-series="benchmark"]'));
    const summary = h.q('.fcc-benchmark-summary').textContent;
    assert.match(summary, /\+18\.18%/);
    assert.match(summary, /\+10\.00%/);
    assert.match(summary, /\+8\.18 pp/);
    assert.match(summary, /2025-11-04/);
    assert.match(summary, /2025-11-06/);
    key(h, 'Home');
    key(h, 'ArrowRight');
    assert.match(h.q('[data-field="date"]').textContent, /2025-11-05/);
    assert.equal(h.q('[data-field="benchmark"]').textContent, 'Unavailable');
    assert.equal(h.button('Candles').disabled, true, 'comparison uses a percentage line scale, never dollar candles');
  } finally { h.close(); }
});

test('missing or mismatched benchmark data stays unavailable instead of substituting another series', () => {
  for (const benchmarkPrices of [undefined, { meta: { symbol: 'QQQ' }, bars: [bar('2025-11-03', 10), bar('2025-11-04', 11)] }]) {
    const h = setup();
    try {
      h.render(px([bar('2025-11-03', 100), bar('2025-11-04', 110)]), { benchmarkPrices });
      assert.equal(h.q('input[data-indicator="benchmark"]').disabled, true);
      assert.equal(h.q('[data-series="benchmark"]'), null);
      assert.match(h.container.textContent, /SPY.*unavailable/i);
    } finally { h.close(); }
  }
});

test('close-only summary data never becomes fictitious OHLC or volume and empty data never becomes a zero quote', () => {
  const h = setup();
  try {
    h.render({ ...px([bar('2025-11-03', 100), bar('2025-11-04', 110)]), light: true });
    assert.equal(h.button('Candles').disabled, true);
    assert.ok(h.q('[data-series="price"]'));
    assert.equal(h.q('[data-pane="volume"]'), null);
    assert.equal(h.q('[data-field="open"]').textContent, 'Unavailable');
    assert.equal(h.q('[data-field="volume"]').textContent, 'Unavailable');
    assert.match(h.container.textContent, /close.only/i);
    h.render({ meta: { currency: 'USD' }, bars: [] });
    assert.equal(h.q('svg'), null);
    assert.match(h.container.textContent, /price history.*unavailable/i);
    assert.doesNotMatch(h.container.textContent, /\$0\.00/);
  } finally { h.close(); }
});

test('invalid OHLC stays absent, invalid closes are omitted, and corrected duplicate dates retain one observation', () => {
  const h = setup();
  try {
    h.render(px([
      bar('2025-11-03', 100),
      bar('2025-11-04', 110),
      { ...bar('2025-11-04', 120), h: 50 },
      { ...bar('2025-11-05', 130), c: null },
      { ...bar('2025-11-06', 140), t: 'not-a-date' },
    ]));
    assert.equal(h.q('.fcc-latest-value').textContent, '$120.00');
    assert.equal(h.q('[data-field="high"]').textContent, 'Unavailable');
    assert.match(h.q('.fcc-quality').textContent, /2.*omitted/i);
    key(h, 'Home');
    key(h, 'ArrowRight');
    assert.match(h.q('[data-field="date"]').textContent, /2025-11-04/);
    key(h, 'ArrowRight');
    assert.match(h.q('[data-field="date"]').textContent, /2025-11-04/);
  } finally { h.close(); }
});

test('keyboard inspection and cleanup work without page globals or network dependencies', () => {
  const h = setup({ technicals: false });
  try {
    let disconnected = 0;
    h.w.ResizeObserver = class { observe() {} disconnect() { disconnected++; } };
    h.w.fetch = () => { throw new Error('chart must not fetch'); };
    const dispose = h.render(px([bar('2025-11-03', 100), bar('2025-11-04', 110)]));
    assert.equal(h.q('input[data-indicator="rsi"]').disabled, true);
    assert.match(h.container.textContent, /technical indicators.*unavailable/i);
    key(h, 'Home');
    assert.equal(h.q('[data-field="close"]').textContent, '$100.00');
    key(h, 'End');
    assert.equal(h.q('[data-field="close"]').textContent, '$110.00');
    assert.match(h.q('.fcc-announcement').textContent, /2025-11-04/);
    const old = h.q('.fu-company-chart');
    const disposeSecond = h.render(px([bar('2025-11-05', 130)]));
    assert.equal(old.isConnected, false);
    assert.equal(h.container.querySelectorAll('.fu-company-chart').length, 1);
    dispose();
    assert.equal(h.container.querySelectorAll('.fu-company-chart').length, 1, 'old cleanup cannot remove a replacement chart');
    disposeSecond();
    disposeSecond();
    assert.equal(h.container.children.length, 0);
    assert.equal(disconnected, 2);
  } finally { h.close(); }
});

test('long-range candles aggregate real calendar OHLCV without averaging volume or changing daily indicator periods', () => {
  const h = setup();
  try {
    const bars = [];
    for (let time = Date.parse('2020-01-01T14:30:00Z'); time <= Date.parse('2025-12-31T14:30:00Z'); time += 864e5) {
      const date = new Date(time);
      if ([0, 6].includes(date.getUTCDay())) continue;
      bars.push(bar(date.toISOString().slice(0, 10), 100 + bars.length, 1000));
    }
    h.render(px(bars));
    const originalSMA = h.q('[data-metric="sma200"] .fcc-metric-value').textContent;
    h.button('All').click();
    assert.match(h.q('.fcc-range-summary').textContent, /monthly candles/);
    key(h, 'Home');
    assert.equal(h.q('[data-field="date"]').textContent, '2020-01-01 – 2020-01-31');
    assert.equal(h.q('[data-field="open"]').textContent, '$99.00');
    assert.equal(h.q('[data-field="high"]').textContent, '$124.00');
    assert.equal(h.q('[data-field="low"]').textContent, '$98.00');
    assert.equal(h.q('[data-field="close"]').textContent, '$122.00');
    assert.equal(h.q('[data-field="volume"]').textContent, '23,000');
    assert.equal(h.q('[data-metric="sma200"] .fcc-metric-value').textContent, originalSMA);
  } finally { h.close(); }
});

test('YTD starts at the real prior-year close even when stored history is older than the current calendar year', () => {
  const h = setup();
  try {
    h.render(px([bar('2024-12-31', 100), bar('2025-01-02', 110), bar('2025-01-03', 120)]));
    h.button('YTD').click();
    assert.match(h.q('.fcc-range-summary').textContent, /2024-12-31 – 2025-01-03/);
    assert.match(h.q('.fcc-range-summary').textContent, /\+20\.00%/);
  } finally { h.close(); }
});

test('out-of-range timestamps are omitted rather than crashing an otherwise usable company chart', () => {
  const h = setup();
  try {
    h.render(px([bar('2025-11-03', 100), { ...bar('2025-11-04', 110), t: 1e30 }]));
    assert.equal(h.q('.fcc-latest-value').textContent, '$100.00');
    assert.match(h.q('.fcc-quality').textContent, /1.*omitted/i);
  } finally { h.close(); }
});
