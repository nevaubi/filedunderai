import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { JSDOM } from 'jsdom';

function setup() {
  const dom = new JSDOM('<main></main>', { url: 'https://research.example/#company-aaa', runScripts: 'outside-only' });
  const w = dom.window;
  w.eval(readFileSync('public/lib/company-profile.js', 'utf8'));
  return { w, api: w.FU_COMPANY_PROFILE, close: () => w.close() };
}

test('management tenure preserves month and year precision and never invents a start date', () => {
  const h = setup();
  try {
    assert.equal(h.api.tenureLabel('2020', '2026-11-05'), 'Since 2020');
    assert.equal(h.api.tenureLabel('2020-06', '2026-11-05'), 'Since Jun 2020 · 6y 5m');
    assert.equal(h.api.tenureLabel('2027-01', '2026-11-05'), 'Start date unavailable');
    assert.equal(h.api.tenureLabel('2020-02-31', '2026-11-05'), 'Start date unavailable');
    assert.equal(h.api.tenureLabel(null, '2026-11-05'), 'Start date unavailable');
  } finally { h.close(); }
});

test('public profile renderer keeps exact source callbacks and handles missing enrichment without guessed leadership', () => {
  const h = setup();
  try {
    const source = { label: 'Revenue', v: 120000000, unit: 'USD', end: '2026-09-30', start: '2025-10-01', kind: 'TTM', rec: { accn: 'verified-accession' } };
    let opened = null;
    const cleanup = h.api.render(h.w.document.querySelector('main'), { ticker: 'AAA', company: { name: 'Alpha Corporation', cik: 1, sicDescription: 'Software', filings: [] }, summary: { name: 'Alpha', sector: 'Information Technology' }, snapshot: { rev: source }, model: { latestQ: () => '2026-09-30', latestA: () => '2025-12-31', get: () => null, ttm: () => null }, prices: { bars: [] }, enrichment: null, helpers: { openSource: cell => { opened = cell; } } });
    const button = h.w.document.querySelector('button[data-metric="Revenue"]');
    assert.match(button.textContent, /120/); button.click();
    assert.equal(opened, source);
    assert.match(h.w.document.querySelector('.cp-financials').textContent, /2026/);
    assert.match(h.w.document.querySelector('.cp-leadership').textContent, /Leadership profiles are not available/);
    assert.equal(h.w.document.querySelectorAll('.cp-person').length, 0);
    cleanup();
  } finally { h.close(); }
});

test('professional profiles render sourced names and role groups but reject unsafe URLs and guessed reporting lines', () => {
  const h = setup();
  try {
    h.api.render(h.w.document.querySelector('main'), { ticker: 'AAA', company: { name: 'Alpha', filings: [] }, snapshot: {}, enrichment: { ticker: 'AAA', description: 'Alpha develops enterprise software.', source_url: 'https://alpha.example/about', updated_at: '2026-11-05', leadership: [{ name: 'Jane Example', role: 'Chief Executive Officer', start_date: '2020', photo_url: 'javascript:alert(1)', profile_url: 'https://alpha.example/leadership/jane', source_url: 'https://alpha.example/leadership' }, { name: 'John Example', role: 'Chief Financial Officer', start_date: '2021-04', photo_url: 'https://alpha.example/john.jpg', source_url: 'https://alpha.example/leadership' }] }, helpers: {} });
    const leader = h.w.document.querySelector('.cp-leadership');
    assert.match(leader.textContent, /Jane Example/);
    assert.match(leader.textContent, /Since 2020/);
    assert.ok(!leader.textContent.includes('Reports to'));
    assert.equal(leader.querySelectorAll('img').length, 1);
    assert.equal(leader.querySelector('img').getAttribute('src'), 'https://alpha.example/john.jpg');
    assert.equal(h.w.document.querySelector('a[href^="javascript:"]'), null);
    assert.match(h.w.document.querySelector('.cp-description').textContent, /enterprise software/);
  } finally { h.close(); }
});

test('a profile for a different ticker cannot populate the selected issuer', () => {
  const h = setup();
  try {
    h.api.render(h.w.document.querySelector('main'), { ticker: 'AAA', company: { name: 'Alpha', filings: [] }, snapshot: {}, enrichment: { ticker: 'OTHER', description: 'Incorrect company business.', leadership: [{ name: 'Wrong Person', role: 'CEO' }] }, helpers: {} });
    assert.ok(!h.w.document.body.textContent.includes('Incorrect company business'));
    assert.ok(!h.w.document.body.textContent.includes('Wrong Person'));
  } finally { h.close(); }
});

test('reported currency and protocol-relative source links survive overview rendering without relabeling or crashing', () => {
  const h = setup();
  try {
    h.api.render(h.w.document.querySelector('main'), { ticker: 'AAA', company: { name: 'Alpha', filings: [] }, snapshot: { rev: { v: 120000000, unit: 'EUR', end: '2026-09-30' } }, enrichment: { ticker: 'AAA', images: [{ url: '//alpha.example/product.jpg', source_url: '//alpha.example/product', title: 'Alpha product' }], leadership: [{ name: 'Alex Example', role: 'Director of Investor Relations' }] }, news: [{ title: 'Company news', url: '//alpha.example/news' }], helpers: {} });
    assert.match(h.w.document.querySelector('[data-metric="Revenue"]').textContent, /€120M/);
    assert.ok(!h.w.document.querySelector('.cp-financials').textContent.includes('$120'));
    assert.equal(h.w.document.querySelector('.cp-context-card').href, 'https://alpha.example/product');
    assert.ok(!h.w.document.querySelector('.cp-leadership').textContent.includes('Board of directors'));
  } finally { h.close(); }
});

test('invalid or incompatible long-history dates fall back to current price bars', async () => {
  const h = setup();
  try {
    const current = { bars: [{ t: '2026-11-04', c: 100 }] };
    const cases = [
      ['BAD', { d: [[1e30, 100, 100, 100, 100, 1]] }, current],
      ['MISMATCH', { d: ['2026-11-03'], c: [100] }, current],
      ['BASIS', { d: ['2026-11-04'], c: [10] }, current],
      ['FALLBACK', { d: ['2026-11-04'], c: [100] }, { bars: [{ t: 'invalid-date', c: 100 }] }],
      ['VALID', { d: ['2026-11-04'], c: [100] }, current],
    ];
    for (const [ticker, data, fallback] of cases) {
      h.w.fetch = async () => ({ ok: true, json: async () => data });
      await h.api.loadHistory(ticker, { dataBase: 'https://data.example/' });
      assert.equal(h.api.history(ticker, fallback), ticker === 'VALID' ? data : fallback);
    }
  } finally { h.close(); }
});

test('official portraits retain attribution while professional-profile counts stay separate from workforce', () => {
  const h = setup();
  try {
    h.api.render(h.w.document.querySelector('main'), {
      ticker: 'AAA', company: { name: 'Alpha', filings: [] }, snapshot: {},
      enrichment: { ticker: 'AAA', updated_at: '2026-11-05', employees: null, professional_profile_count: 12345,
        coverage: { leadership_scope: 'Selected leaders; not a complete roster.' },
        leadership: [{ name: 'Jane Example', role: 'CEO', photo_url: 'https://alpha.example/photo.jpg', photo_credit: 'Example Photographer', photo_license: 'CC BY-SA 4.0', photo_license_url: 'https://creativecommons.org/licenses/by-sa/4.0/' }],
        images: [{ url: 'https://alpha.example/event.jpg', source_url: 'https://alpha.example/event', title: 'Company event', credit: 'Alpha' }],
      }, helpers: {},
    });
    assert.match(h.w.document.querySelector('.cp-leadership').textContent, /Photo: Example Photographer/);
    assert.equal(h.w.document.querySelector('.cp-photo-credit a').href, 'https://creativecommons.org/licenses/by-sa/4.0/');
    assert.match(h.w.document.querySelector('.cp-leadership').textContent, /not a complete roster/);
    assert.match(h.w.document.querySelector('.cp-recent').textContent, /Image: Alpha/);
    assert.ok(!h.w.document.querySelector('.cp-facts').textContent.includes('12,345'));
    assert.ok(!h.w.document.querySelector('.cp-facts').textContent.includes('provider estimates'));
  } finally { h.close(); }
});
