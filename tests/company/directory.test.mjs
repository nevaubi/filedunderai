import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { JSDOM } from 'jsdom';

function setup() {
  const dom = new JSDOM('<main></main>', { url: 'https://research.example/#companies', runScripts: 'outside-only' });
  dom.window.eval(readFileSync('public/lib/company-directory.js', 'utf8'));
  return { w: dom.window, api: dom.window.FU_COMPANIES, close: () => dom.window.close() };
}
const registry = [
  { ticker: 'AAA', name: 'Alpha Software', cik: 1, sector: 'Information Technology', industry: 'Software' },
  { ticker: 'NEW', name: 'New Industries', cik: 2, sector: 'Industrials', screener_eligible: false },
  { ticker: 'BBB', name: 'Beta Software', cik: 3, sector: 'Information Technology', industry: 'Software' },
];

test('registered pending issuers remain discoverable without activating screener membership or fabricating quotes', () => {
  const h = setup();
  try {
    const active = [{ ticker: 'AAA', name: 'Alpha', price: 12.5, market_cap: 500, sector: 'Information Technology' }];
    h.api.apply({ companies: registry }, active);
    assert.equal(h.api.list().length, 3);
    assert.equal(h.api.get('new').name, 'New Industries');
    assert.equal(h.api.get('NEW').screener_eligible, false);
    assert.equal(h.api.get('NEW').price, undefined);
    assert.equal(h.api.get('AAA').price, 12.5);
    assert.equal(active.length, 1);
    assert.equal(registry[0].price, undefined);
    assert.deepEqual(Array.from(h.api.query({ q: 'new' }), x => x.ticker), ['NEW']);
  } finally { h.close(); }
});

test('company search supports name and sector filters and ranks exact tickers ahead of name matches', () => {
  const h = setup();
  try {
    h.api.apply({ companies: [...registry, { ticker: 'ZZZ', name: 'AAA Equipment', cik: 4, sector: 'Industrials' }] }, []);
    assert.deepEqual(Array.from(h.api.query({ q: 'aaa' }), x => x.ticker), ['AAA', 'ZZZ']);
    assert.deepEqual(Array.from(h.api.query({ q: 'software', sector: 'Information Technology', sort: 'name' }), x => x.ticker), ['AAA', 'BBB']);
    assert.equal(h.api.query({ q: 'no-match' }).length, 0);
    assert.deepEqual(Array.from(h.api.related('AAA'), x => x.ticker), ['BBB']);
  } finally { h.close(); }
});

test('directory renders real links, filters results and resets pagination on search', () => {
  const h = setup();
  try {
    h.api.apply({ companies: [...registry, ...Array.from({ length: 60 }, (_, i) => ({ ticker: 'T' + i, name: 'Issuer ' + i, cik: i + 10, sector: 'Energy' }))] }, []);
    const host = h.w.document.querySelector('main');
    h.api.render(host);
    assert.ok(host.querySelector('a[href="#company-aaa"]'));
    const next = host.querySelector('[data-action="next"]');
    next.click();
    assert.match(host.querySelector('[data-count]').textContent, /51–63/);
    const input = host.querySelector('input[type="search"]');
    input.value = 'new industries'; input.dispatchEvent(new h.w.Event('input'));
    assert.equal(host.querySelectorAll('tbody tr').length, 1);
    assert.ok(host.querySelector('a[href="#company-new"]'));
    assert.match(host.querySelector('[data-count]').textContent, /1–1/);
  } finally { h.close(); }
});

test('SEC industry descriptions remain searchable and prioritize actual industry peers without changing coverage', () => {
  const h = setup();
  try {
    h.api.apply({ companies: [
      { ticker: 'AAA', name: 'Alpha', cik: 1, sector: 'Information Technology', industry: 'Enterprise platforms', sic_description: 'Services-Prepackaged Software' },
      { ticker: 'BBB', name: 'Beta', cik: 2, sector: 'Information Technology' },
      { ticker: 'ZZZ', name: 'Zeta', cik: 3, sector: 'Information Technology', sic_description: 'Semiconductors' },
    ] }, [{ ticker: 'BBB', sic: 'Services-Prepackaged Software', market_cap: 10 }, { ticker: 'ZZZ', market_cap: 1000 }]);
    assert.equal(h.api.get('AAA').industry, 'Enterprise platforms');
    assert.equal(h.api.get('BBB').industry, 'Services-Prepackaged Software');
    assert.deepEqual(Array.from(h.api.query({ q: 'prepackaged' }), x => x.ticker), ['BBB', 'AAA']);
    assert.deepEqual(Array.from(h.api.related('AAA'), x => x.ticker), ['BBB', 'ZZZ']);
    assert.equal(h.api.get('AAA').screener_eligible, false);
  } finally { h.close(); }
});
