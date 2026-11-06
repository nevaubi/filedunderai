// API changes for the assistant panel: tool-spec validation, agent profiles, EDGAR search and Firecrawl modes.
import test from 'node:test';
import assert from 'node:assert/strict';
import aiHandler, { validTools } from '../../api/ai.js';
import searchHandler, { edgarQuery, edgarHits } from '../../api/search.js';
import scrapeHandler, { safeTarget } from '../../api/scrape.js';

const res = () => ({ statusCode: 200, headers: {}, status(n) { this.statusCode = n; return this; }, json(d) { this.data = d; return this; }, setHeader(k, v) { this.headers[k] = v; } });
const req = body => ({ method: 'POST', headers: { origin: 'https://filedunder.ai' }, body });
async function withFetch(fake, fn) { const old = globalThis.fetch; globalThis.fetch = fake; try { return await fn(); } finally { globalThis.fetch = old; } }
const ok = data => ({ ok: true, status: 200, json: async () => data });

test('tool definitions are bounded and well formed before reaching the provider', async () => {
  const fn = name => ({ type: 'function', function: { name, description: 'd', parameters: { type: 'object', properties: {} } } });
  assert.equal(validTools(undefined), true);
  assert.equal(validTools([fn('get_financials'), fn('ask_user')]), true);
  for (const bad of [[fn('bad name')], [fn('a'), fn('a')], [{ type: 'x', function: { name: 'a' } }], [{ type: 'function', function: { name: 'a', parameters: [] } }], Array.from({ length: 65 }, (_, i) => fn('t' + i)), [fn('a'.repeat(70))], 'tools'])
    assert.equal(validTools(bad), false, JSON.stringify(bad).slice(0, 80));
  const old = process.env.FIREWORKS_API_KEY; process.env.FIREWORKS_API_KEY = 'test';
  try {
    await withFetch(async url => { if (String(url).includes('fireworks')) throw new Error('provider must not be called'); return ok(0); }, async () => {
      const r = res(); await aiHandler(req({ messages: [{ role: 'user', content: 'x' }], tools: [fn('bad name')], profile: 'agent_fast' }), r);
      assert.equal(r.statusCode, 400); assert.match(r.data.error, /tool/i);
    });
    let sent;
    await withFetch(async (url, init) => { if (String(url).includes('fireworks')) { sent = JSON.parse(init.body); return ok({ choices: [{ finish_reason: 'stop', message: { role: 'assistant', content: 'done', reasoning_content: 'why' } }], usage: { completion_tokens: 10 } }); } return ok(0); }, async () => {
      const r = res(); await aiHandler(req({ messages: [{ role: 'user', content: 'x' }], tools: [fn('ok_tool')], profile: 'agent_deep', round: 30, remainingCalls: 8, maxTokens: 99999, remainingTokens: 99999 }), r);
      assert.equal(r.statusCode, 200);
      assert.equal(r.data.choices[0].message.reasoning_content, 'why', 'reasoning summaries pass through');
      assert.ok(sent.max_tokens <= 9000, 'deep profile caps tokens per call');
      assert.equal(r.data.research.profile, 'agent_deep');
    });
    await withFetch(async () => ok(0), async () => { const r = res(); await aiHandler(req({ messages: [{ role: 'user', content: 'x' }], profile: 'agent_fast', round: 18 }), r); assert.equal(r.statusCode, 400); });
  } finally { if (old === undefined) delete process.env.FIREWORKS_API_KEY; else process.env.FIREWORKS_API_KEY = old; }
});

test('EDGAR full-text search validates arguments and maps hits to filing URLs', async () => {
  const u = new URL(edgarQuery({ query: '"data center"', ciks: ['1045810', 'x; drop'], forms: ['10-Q', '<script>'], startdt: '2025-01-01' }));
  assert.equal(u.hostname, 'efts.sec.gov');
  assert.equal(u.searchParams.get('q'), '"data center"');
  assert.equal(u.searchParams.get('ciks'), '0001045810');
  assert.equal(u.searchParams.get('forms'), '10-Q');
  assert.equal(u.searchParams.get('dateRange'), 'custom');
  assert.throws(() => edgarQuery({ query: ' ' }), /query/);
  const h = edgarHits({ hits: { total: { value: 42 }, hits: [{ _id: '0001045810-26-000075:nvda-20260726.htm', _source: { ciks: ['0001045810'], display_names: ['NVIDIA CORP  (NVDA)  (CIK 0001045810)'], form: '10-Q', file_date: '2026-08-26', period_ending: '2026-07-26', adsh: '0001045810-26-000075' } }] } });
  assert.equal(h.total, 42);
  assert.equal(h.hits[0].url, 'https://www.sec.gov/Archives/edgar/data/1045810/000104581026000075/nvda-20260726.htm');
  assert.equal(h.hits[0].company, 'NVIDIA CORP (NVDA) (CIK 0001045810)');
  let seen;
  await withFetch(async (url, init) => { if (String(url).includes('efts.sec.gov')) { seen = init.headers['User-Agent']; return ok({ hits: { total: { value: 0 }, hits: [] } }); } return ok(0); }, async () => {
    const r = res(); await searchHandler(req({ source: 'edgar', query: 'buyback' }), r);
    assert.equal(r.statusCode, 200); assert.equal(r.data.total, 0); assert.match(seen, /@/);
  });
  const old = process.env.TAVILY_API_KEY; process.env.TAVILY_API_KEY = 't';
  try {
    let body;
    await withFetch(async (url, init) => { if (String(url).includes('tavily')) { body = JSON.parse(init.body); return ok({ results: [] }); } return ok(0); }, async () => {
      const r = res(); await searchHandler(req({ query: 'q', topic: 'news', days: 7 }), r);
      assert.equal(body.topic, 'news'); assert.equal(body.days, 7);
      await searchHandler(req({ query: 'q', topic: 'finance', days: 7 }), res());
      assert.equal(body.topic, 'finance'); assert.equal(body.days, undefined);
      await searchHandler(req({ query: 'q', news: true }), res());
      assert.equal(body.topic, 'news'); assert.equal(body.days, 21);
    });
  } finally { if (old === undefined) delete process.env.TAVILY_API_KEY; else process.env.TAVILY_API_KEY = old; }
});

test('scrape modes share the guard and URL checks: map, crawl, extract', async () => {
  for (const bad of ['http://localhost:3000', 'http://169.254.169.254/latest', 'http://10.0.0.1', 'http://172.16.0.1', 'file:///etc/passwd', 'https://user:pw@example.com', 'http://metadata.google.internal']) assert.equal(safeTarget(bad), null, bad);
  assert.ok(safeTarget('https://investor.nvidia.com'));
  const old = process.env.FIRECRAWL_API_KEY; process.env.FIRECRAWL_API_KEY = 'fc';
  try {
    const calls = [];
    const fake = async (url, init) => {
      url = String(url); if (!url.includes('firecrawl')) return ok(0);
      calls.push({ url, method: init.method, body: init.body ? JSON.parse(init.body) : null });
      if (url.endsWith('/map')) return ok({ links: [{ url: 'https://investor.nvidia.com/news', title: 'News' }, 'https://investor.nvidia.com/x', { url: 'http://localhost/evil' }] });
      if (url.endsWith('/crawl')) return ok({ id: 'job-12345678' });
      if (url.includes('/crawl/job-12345678')) return ok({ status: 'completed', data: [{ markdown: 'A'.repeat(9000), metadata: { sourceURL: 'https://investor.nvidia.com/a', title: 'A' } }] });
      if (url.endsWith('/scrape')) return ok({ data: { json: { awards: [{ value: 1 }] }, metadata: { title: 'Awards' } } });
      return { ok: false, status: 404, json: async () => ({}) };
    };
    await withFetch(fake, async () => {
      let r = res(); await scrapeHandler(req({ mode: 'map', url: 'https://investor.nvidia.com', search: 'press', limit: 500 }), r);
      assert.equal(r.statusCode, 200); assert.equal(r.data.links.length, 2, 'unsafe links dropped'); assert.equal(calls.at(-1).body.limit, 200);
      r = res(); await scrapeHandler(req({ mode: 'crawl', url: 'https://investor.nvidia.com', limit: 99, includePaths: ['/news/.*', 5] }), r);
      assert.equal(r.statusCode, 200); assert.equal(r.data.status, 'completed'); assert.equal(r.data.pages[0].markdown.length, 6000);
      const start = calls.find(c => c.url.endsWith('/crawl'));
      assert.equal(start.body.limit, 15); assert.deepEqual(start.body.includePaths, ['/news/.*']);
      r = res(); await scrapeHandler(req({ mode: 'extract', url: 'https://example.com/awards', prompt: 'awards' }), r);
      assert.equal(r.statusCode, 200); assert.deepEqual(r.data.json, { awards: [{ value: 1 }] });
      assert.deepEqual(calls.at(-1).body.formats, [{ type: 'json', prompt: 'awards' }]);
      r = res(); await scrapeHandler(req({ mode: 'extract', url: 'https://example.com' }), r); assert.equal(r.statusCode, 400);
      r = res(); await scrapeHandler(req({ mode: 'shell', url: 'https://example.com' }), r); assert.equal(r.statusCode, 400);
      r = res(); await scrapeHandler(req({ mode: 'map', url: 'http://127.0.0.1:8080' }), r); assert.equal(r.statusCode, 400);
    });
  } finally { if (old === undefined) delete process.env.FIRECRAWL_API_KEY; else process.env.FIRECRAWL_API_KEY = old; }
});
