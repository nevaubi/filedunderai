import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { setImmediate } from 'node:timers/promises';
import { JSDOM } from 'jsdom';

const app = readFileSync('public/app.js', 'utf8');
const base = 'https://data.example/public-data/';
const active = [{ ticker: 'OLD', name: 'Old Company', logo: true }];
const directory = { schema_version: 1, companies: [
  ...active,
  { ticker: 'NEW', name: 'New Industries', coverage: { profile: 'available' }, leadership_count: 2 },
] };

function setup(registry) {
  const dom = new JSDOM('<main></main>', { url: 'https://research.example/#companies', runScripts: 'outside-only' });
  const w = dom.window, context = dom.getInternalVMContext(), timers = new Map();
  let nextTimer = 0;
  w.FU_CONFIG = { dataBase: base };
  w.NAME = {}; w.SHORT = {}; w.WEB = {};
  w.page = w.document.querySelector('main');
  w.applyMarket = w.applyMacro = w.buildShell = () => {};
  w.setTimeout = callback => { timers.set(++nextTimer, callback); return nextTimer; };
  w.clearTimeout = id => timers.delete(id);
  w.fetch = async url => {
    const path = String(url).replace(base, '');
    if (path === 'meta/logos.json') return { ok: true, json: async () => registry() };
    const data = { 'index.json': { companies: active }, 'market.json': {}, 'macro.json': {}, 'feed.json': {}, 'company-directory.json': directory }[path];
    assert.ok(data, 'Unexpected request: ' + url);
    return { ok: true, json: async () => data };
  };
  vm.runInContext(readFileSync('public/lib/company-directory.js', 'utf8'), context);
  for (const source of [
    app.slice(0, app.indexOf('function applyMarket(')),
    app.slice(app.indexOf('const $ ='), app.indexOf('/* ---------- formatting')),
    app.slice(app.indexOf('const LOGO_COLORS ='), app.indexOf('const STMT_NAME =')),
    app.slice(app.indexOf('const logoUrl ='), app.indexOf('pCompany.prep = null;')),
    app.slice(app.indexOf('async function boot('), app.indexOf('// Wait for all classic page modules')),
  ]) vm.runInContext(source, context);
  w.route = () => w.FU_COMPANIES.render(w.page, { logo: w.miniLogo });
  return {
    w, context,
    identity: ticker => w.document.querySelector('a[href="#company-' + ticker.toLowerCase() + '"]'),
    expireTimers: () => { const callbacks = [...timers.values()]; timers.clear(); callbacks.forEach(fn => fn()); },
    close: () => w.close(),
  };
}

test('published logos reach directory-only companies without changing screener or company metadata', async () => {
  const h = setup(() => ({ tickers: { NEW: 'svgapi' } }));
  try {
    await h.w.boot();
    assert.equal(h.identity('NEW').querySelector('img')?.getAttribute('src'), base + 'logos/NEW.png');
    assert.equal(h.identity('OLD').querySelector('img')?.getAttribute('src'), base + 'logos/OLD.png');
    assert.equal(h.w.FU_COMPANIES.get('NEW').leadership_count, 2);
    assert.equal(h.w.FU_COMPANIES.get('NEW').screener_eligible, false);
    assert.deepEqual(Array.from(vm.runInContext('Object.keys(IDX)', h.context)), ['OLD']);
    assert.equal(vm.runInContext('INDEX.companies.length', h.context), 1);
  } finally { h.close(); }
});

test('failed or malformed optional registry leaves the directory and legacy logos usable', async () => {
  for (const registry of [() => { throw new Error('offline'); }, () => null, () => ({ tickers: [] }), () => ({ tickers: 'https://untrusted.example/logo.png' })]) {
    const h = setup(registry);
    try {
      await h.w.boot();
      assert.equal(h.w.document.querySelectorAll('tbody tr').length, 2);
      assert.equal(h.identity('OLD').querySelector('img')?.getAttribute('src'), base + 'logos/OLD.png');
      assert.equal(h.identity('NEW').querySelector('img'), null);
      assert.equal(h.identity('NEW').querySelector('.mlogo.mono')?.textContent, 'N');
    } finally { h.close(); }
  }
});

test('a slow registry cannot block boot and late logos preserve active directory filters', async () => {
  let finish;
  const registry = new Promise(resolve => { finish = resolve; });
  const h = setup(() => registry);
  try {
    const booted = h.w.boot();
    await setImmediate();
    h.expireTimers();
    await booted;
    assert.equal(h.identity('NEW').querySelector('img'), null);
    const input = h.w.document.querySelector('input[type="search"]');
    input.value = 'New Industries'; input.dispatchEvent(new h.w.Event('input')); input.focus();
    const identity = h.identity('NEW');
    finish({ tickers: { NEW: 'https://untrusted.example/logo.png' } });
    await setImmediate();
    assert.equal(identity.querySelector('img')?.getAttribute('src'), base + 'logos/NEW.png');
    assert.equal(h.identity('NEW'), identity);
    assert.equal(h.w.document.activeElement, input);
    assert.equal(input.value, 'New Industries');
    assert.equal(h.w.document.querySelectorAll('tbody tr').length, 1);
  } finally { h.close(); }
});

test('unavailable PNGs still fall back to monograms for small and large logos', async () => {
  const h = setup(() => ({ tickers: { NEW: 'svgapi' } }));
  try {
    await h.w.boot();
    const small = h.identity('NEW').querySelector('img');
    assert.ok(small);
    small.dispatchEvent(new h.w.Event('error'));
    assert.equal(h.identity('NEW').querySelector('.mlogo.mono')?.textContent, 'N');
    const host = h.w.document.createElement('div');
    host.append(h.w.logoTile('NEW', 56)); h.w.document.body.append(host);
    host.querySelector('img').dispatchEvent(new h.w.Event('error'));
    assert.equal(host.querySelector('img'), null);
    assert.equal(host.querySelector('.logo')?.textContent, 'NEW');
  } finally { h.close(); }
});
