// Assistant panel in the real index.html script order (JSDOM): mounting, persistence, handoff, a full turn
// against a stubbed /api/ai, history view, markdown safety, quote highlighting and the pluggable tool registry.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import { JSDOM } from 'jsdom';
import { build } from 'esbuild';

const html = fs.readFileSync('public/index.html', 'utf8');
const officeBundle = (await build({ entryPoints: ['scripts/office-vendor.js'], write: false, bundle: true, minify: true, platform: 'browser', format: 'iife', target: ['es2022'], legalComments: 'eof' })).outputFiles[0].text;
const response = data => ({ ok: true, json: async () => data });
const say = (content, tool_calls) => ({ choices: [{ finish_reason: tool_calls ? 'tool_calls' : 'stop', message: { role: 'assistant', content, tool_calls } }], usage: { completion_tokens: 50 }, research: { providerCalls: 1, chargedTokens: 50 } });
const call = (name, args, id = name) => ({ id, type: 'function', function: { name, arguments: JSON.stringify(args) } });

function setup({ hash = '#markets', storage = {} } = {}) {
  const dom = new JSDOM(html, { url: 'https://filedunder.ai/' + hash, runScripts: 'outside-only', pretendToBeVisual: true });
  const w = dom.window, context = dom.getInternalVMContext();
  for (const [k, v] of Object.entries(storage)) w.localStorage.setItem(k, JSON.stringify(v));
  w.AbortController = AbortController; w.AbortSignal = AbortSignal; w.TextEncoder = TextEncoder; w.TextDecoder = TextDecoder; w.scrollTo = () => {};
  w.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
  w.fetch = (url, init) => url === '/api/ai' ? w.modelFetch(url, init) : new Promise(() => {});
  const deferred = [];
  for (const script of w.document.querySelectorAll('script')) {
    const src = script.getAttribute('src'); if (src && !src.startsWith('/')) continue;
    const source = src === '/vendor/office.js' ? officeBundle : src ? fs.readFileSync('public' + src, 'utf8') : script.textContent;
    if (script.defer) deferred.push([source, src]); else vm.runInContext(source, context, { filename: src || 'inline.js' });
  }
  for (const [source, src] of deferred) vm.runInContext(source, context, { filename: src });
  w.document.dispatchEvent(new w.Event('DOMContentLoaded'));
  return { w, close: () => w.close() };
}
const tick = (ms = 0) => new Promise(r => setTimeout(r, ms));
async function until(fn, ms = 3000) { const t0 = Date.now(); while (!fn()) { if (Date.now() - t0 > ms) throw new Error('timed out'); await tick(10); } }

test('top-right button and shortcut open a docked panel; dock state persists', async () => {
  const h = setup();
  try {
    const { w } = h, d = w.document, btn = d.querySelector('#asst-toggle'), aside = d.querySelector('#fu-asst');
    assert.ok(btn && aside, 'button and panel mounted');
    assert.equal(btn.tagName, 'BUTTON');
    assert.equal(aside.getAttribute('aria-hidden'), 'true');
    btn.click();
    assert.equal(aside.classList.contains('open'), true);
    assert.equal(btn.getAttribute('aria-expanded'), 'true');
    assert.ok(d.documentElement.classList.contains('asst-docked'), 'page reflows beside the docked panel');
    assert.ok(aside.querySelectorAll('.asst-sugg button').length >= 3, 'empty state suggestions');
    aside.querySelector('[data-act="dock"]').click();
    assert.ok(aside.classList.contains('float'));
    assert.ok(!d.documentElement.classList.contains('asst-docked'));
    const ui = JSON.parse(w.localStorage.getItem('fu:assistant:ui'));
    assert.equal(ui.docked, false); assert.equal(ui.open, true); assert.ok(Number.isFinite(ui.box.x));
    d.dispatchEvent(new w.KeyboardEvent('keydown', { key: 'j', ctrlKey: true, bubbles: true }));
    assert.equal(aside.classList.contains('open'), false, 'Ctrl+J toggles');
    assert.equal(JSON.parse(w.localStorage.getItem('fu:assistant:ui')).open, false);
    assert.ok(w.eval('SHORTCUTS').some(s => s[0] === '⌘ J'));
  } finally { h.close(); }
  const again = setup({ storage: { 'fu:assistant:ui': { open: true, docked: false, box: { x: 30, y: 40, w: 500, h: 600 }, deep: true } } });
  try {
    const aside = again.w.document.querySelector('#fu-asst');
    assert.ok(aside.classList.contains('open') && aside.classList.contains('float'));
    assert.equal(aside.style.left, '30px');
    assert.equal(aside.querySelector('[data-depth="deep"]').classList.contains('on'), true);
  } finally { again.close(); }
});

test('a full turn: plan, page context tool, ask_user card, answer with evidence citation, saved to history', async () => {
  const h = setup({ hash: '#macro' });
  try {
    const { w } = h, d = w.document;
    const ev = w.FU_EVIDENCE.capture({ ticker: 'AAA', metric: 'Revenue', value: 2.5e9, unit: 'USD', period: 'FY25', periodStart: '2025-01-01', periodEnd: '2025-12-31', periodType: 'annual', filedDate: '2026-02-01', sourceUrl: 'https://www.sec.gov/a' });
    const bodies = [];
    let n = 0;
    w.modelFetch = async (_, init) => {
      const b = JSON.parse(init.body); bodies.push(b); n++;
      if (n === 1) return response(say('', [call('update_plan', { goal: 'Answer', steps: [{ title: 'Read page', status: 'doing' }] }), call('get_page_context', {})]));
      if (n === 2) return response(say('', [call('ask_user', { question: 'Which year?', options: ['2025', '2024'] })]));
      return response(say(`Revenue was $2.50B [[E:${ev.id}]].\n\n| A | B |\n|---|---:|\n| x | 1 |\n\n<script>alert(1)</script> [bad](javascript:alert(1))`));
    };
    w.FU_ASSISTANT.open();
    const ta = d.querySelector('#fu-asst textarea');
    ta.value = 'What does this page show?';
    d.querySelector('#fu-asst form').dispatchEvent(new w.Event('submit', { cancelable: true }));
    await until(() => d.querySelector('.asst-ask'));
    assert.equal(d.querySelector('.asst-plan .asst-goal').textContent, 'Answer');
    assert.ok([...d.querySelectorAll('.asst-step .asst-slab')].some(s => s.textContent === 'Read your screen'));
    assert.equal(bodies[0].profile, 'agent_fast');
    assert.ok(bodies[0].tools.some(t => t.function.name === 'run_code') && bodies[0].tools.some(t => t.function.name === 'ask_user'));
    assert.match(bodies[0].messages[0].content, /\[\[E:ev_/);
    assert.match(bodies[0].messages.at(-1).content, /^\[Context: the user is viewing #macro/);
    assert.match(bodies[1].messages.find(m => m.role === 'tool' && m.tool_call_id === 'get_page_context').content, /Route: #macro/);
    // A handoff while the turn waits never discards it and never submits.
    w.dispatchEvent(new w.CustomEvent('fu:research-request', { detail: 'Prefill while busy' }));
    assert.equal(ta.value, 'Prefill while busy');
    assert.equal(d.querySelectorAll('.asst-m.u').length, 1);
    [...d.querySelectorAll('.asst-opts button')].find(b => b.textContent === '2025').click();
    await until(() => d.querySelector('.asst-mfoot'));
    assert.ok(bodies[2].messages.some(m => m.role === 'tool' && m.content === 'User answered: 2025'));
    const md = d.querySelector('.asst-m.a .asst-md');
    assert.ok(md.querySelector(`.cite[data-evidence-id="${ev.id}"]`), 'evidence citation chip');
    assert.ok(md.querySelector('table td.n'));
    assert.ok(!md.querySelector('script') && !md.innerHTML.includes('javascript:'), 'markup and unsafe links are inert');
    assert.match(d.querySelector('.asst-mfoot').textContent, /1 cited figure checked/);
    const saved = JSON.parse(w.localStorage.getItem('fu:assistant:history:v1'));
    assert.equal(saved.length, 1);
    assert.equal(saved[0].title, 'What does this page show?');
    assert.equal(saved[0].messages.length, 2);
    assert.equal(saved[0].messages[1].pending, undefined);
    // history view lists, reopens and deletes
    d.querySelector('#page').innerHTML = '';
    await w.FU_PAGES.research();
    assert.equal(d.querySelectorAll('.asst-hrow').length, 1);
    const del = d.querySelector('.asst-hdel'); del.click(); assert.equal(d.querySelectorAll('.asst-hrow').length, 1, 'first click asks to confirm'); del.click();
    assert.equal(JSON.parse(w.localStorage.getItem('fu:assistant:history:v1')).length, 0);
  } finally { h.close(); }
});

test('handoff opens the panel and prefills without submitting; legacy buttons route through it', async () => {
  const h = setup();
  try {
    const { w } = h, d = w.document;
    let calls = 0; w.modelFetch = async () => { calls++; return response(say('x')); };
    w.requestResearch('Write a brief on AAA');
    await tick();
    assert.ok(d.querySelector('#fu-asst').classList.contains('open'));
    assert.equal(d.querySelector('#fu-asst textarea').value, 'Write a brief on AAA');
    assert.equal(calls, 0);
    assert.equal(w.location.hash, '#markets', 'the current page stays open');
  } finally { h.close(); }
});

test('markdown renderer: headings, lists, tables, filing quote links, charts and safe links', () => {
  const h = setup();
  try {
    const { w } = h, r = w.FU_ASSISTANT.renderMarkdown;
    const out = r('## Head\n- one **bold**\n- two\n\n1. first\n\n[10-Q](filing:nvda:0001045810-26-000075 "driven by data center demand") [site](https://example.com/x) [in app](#company-nvda) [bad](javascript:alert(1)) [bad2](filing:../x)\n\n```chart\n{"type":"line","x":"p","series":["A"],"data":[{"p":"Q1","A":1},{"p":"Q2","A":2}]}\n```\n```js\n<b>x</b>\n```\n> quote');
    assert.match(out, /<h4>Head<\/h4>/);
    assert.match(out, /<ul><li>one <b>bold<\/b><\/li><li>two<\/li><\/ul>/);
    assert.match(out, /<ol><li>first<\/li><\/ol>/);
    assert.match(out, /data-tk="NVDA" data-accn="0001045810-26-000075" data-q="driven by data center demand"/);
    assert.match(out, /href="https:\/\/example.com\/x" target="_blank" rel="noopener noreferrer"/);
    assert.match(out, /href="#company-nvda" class="asst-in"/);
    assert.ok(!out.includes('javascript:') && !out.includes('filing:../x'));
    assert.match(out, /<figure class="asst-chart"/);
    assert.match(out, /&lt;b&gt;x&lt;\/b&gt;/);
    assert.match(out, /<blockquote>/);
    assert.equal(w.FU_ASSISTANT.parseChart('{"data":[]}'), null);
    assert.equal(w.FU_ASSISTANT.parseChart('nope'), null);
    assert.deepEqual(JSON.parse(JSON.stringify(w.FU_ASSISTANT.parseChart('{"type":"pie","data":[{"x":"a","v":1,"s":"t"}]}'))).series, ['v']);
  } finally { h.close(); }
});

test('quoted filing phrases are found across markup and normalized quotes, then highlighted', () => {
  const h = setup();
  try {
    const { w } = h, doc = new w.DOMParser().parseFromString('<body><p>Revenue was <b>driven  by</b> the ramp of our\n Blackwell’s platform; other text.</p></body>', 'text/html');
    const hits = w.FU_ASSISTANT.findQuote(doc, "driven by the ramp of our Blackwell's platform");
    assert.equal(hits.length, 2, 'one highlight per text node the quote spans');
    assert.equal(hits.map(s => s.textContent).join(''), "driven  by the ramp of our\n Blackwell’s platform");
    assert.ok(hits[0].classList.contains('fu-hit'));
    assert.equal(w.FU_ASSISTANT.findQuote(doc, 'not in this document at all').length, 0);
    assert.equal(w.FU_ASSISTANT.findQuote(doc, 'short').length, 0);
  } finally { h.close(); }
});

test('pluggable tools: registry validation, page scoping, legacy providers and built-in executors', async () => {
  const h = setup({ hash: '#company-aaa' });
  try {
    const { w } = h, kit = w.FU_ASSISTANT_TOOLKIT;
    w.FU_ASSISTANT_TOOLS.push({ name: 'get_supply_chain', label: 'Mapped supply chain', description: 'd', parameters: { type: 'object', properties: { ticker: { type: 'string' } } }, run: async () => 'links' });
    w.FU_ASSISTANT.registerTool({ name: 'company_only', pages: ['company'], description: 'd', run: async () => 'x' });
    w.FU_ASSISTANT_TOOLS.push({ name: 'get_financials', description: 'shadow', run: async () => 'evil' }, { name: 'Bad Name', run: async () => '' }, { name: 'no_run' });
    const warn = w.console.warn; w.console.warn = () => {};
    const company = kit.toolsFor('company'), macro = kit.toolsFor('macro');
    w.console.warn = warn;
    const names = company.map(t => t.name);
    assert.ok(names.includes('get_supply_chain') && names.includes('company_only'));
    assert.ok(!macro.some(t => t.name === 'company_only'), 'page-scoped tool hidden elsewhere');
    assert.equal(company.filter(t => t.name === 'get_financials').length, 1);
    assert.notEqual(company.find(t => t.name === 'get_financials').description, 'shadow', 'built-ins win');
    assert.ok(!names.includes('Bad Name') && !names.includes('no_run'));
    assert.ok(company.some(t => t.plugin && t.name === 'read_thesis_draft') || company.some(t => t.plugin), 'FU_TOOL_PROVIDERS tools adapt into the panel');
    for (const t of company) assert.equal(t.parameters.type, 'object', t.name);
    // screen_companies over the stored index
    w.eval(`INDEX = { generated: '2026-11-05', companies: [ { ticker: 'AAA', name: 'Alpha', sector: 'Tech', sp500: true, rev_growth_lq: 0.3, fcf_yield: 0.04, market_cap: 2e11 }, { ticker: 'BBB', name: 'Beta', sector: 'Tech', sp500: true, rev_growth_lq: 0.1, fcf_yield: 0.08, market_cap: 1e11 }, { ticker: 'CCC', name: 'Gamma', sector: 'Energy', rev_growth_lq: 0.5, market_cap: 5e10 } ] };`);
    const screen = company.find(t => t.name === 'screen_companies');
    const r = await screen.run({ filters: [{ metric: 'rev_growth_lq', op: '>', value: 0.2 }], index: 'sp500', sort_by: 'fcf_yield', limit: 5 }, {});
    assert.match(r.output, /^1 companies match/);
    assert.match(r.output, /AAA Alpha/);
    const nav = company.find(t => t.name === 'navigate_to');
    await assert.rejects(nav.run({ page: 'nowhere' }), /Unknown page/);
    const cov = company.find(t => t.name === 'get_coverage');
    assert.match((await cov.run({ query: 'gam' })).output, /CCC \| Gamma/);
    const sandbox = await kit.runSandbox('return 1', {});
    assert.equal(sandbox.ok, false, 'JSDOM has no Worker: sandbox reports unavailable instead of running code');
  } finally { h.close(); }
});
