import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import { completeWithFallback, validCompletion } from '../../api/ai.js';

const reply = (content, finish = 'stop', tool_calls) => ({ choices: [{ finish_reason: finish, message: { role: 'assistant', content, tool_calls } }], usage: { completion_tokens: 100 } });
const response = data => ({ ok: true, json: async () => data });
function context() {
  const c = vm.createContext({ console, URL, Date, performance, DOMException, AbortController, AbortSignal, JSON, setTimeout, clearTimeout });
  c.window = c;
  for (const file of ['evidence', 'financial', 'research']) vm.runInContext(fs.readFileSync(`public/lib/${file}.js`, 'utf8'), c);
  c.esc = s => String(s ?? '').replace(/[&<>"']/g, x => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[x]));
  const app = fs.readFileSync('public/app.js', 'utf8');
  vm.runInContext(app.slice(app.indexOf('function abortCheck('), app.indexOf('function baseTools(')), c);
  vm.runInContext(app.slice(app.indexOf('function resolveCitation('), app.indexOf('document.addEventListener("click", async e => { const b = e.target.closest(".cite')), c);
  vm.runInContext(app.slice(app.indexOf('function validateMarkdownNumbers('), app.indexOf('function webTools(')), c);
  return c;
}

test('request budgets validate and clamp remaining provider attempts against the profile and consumed round', () => {
  const budget = globalThis.FU_RESEARCH_RUNTIME.requestBudget;
  assert.equal(budget({ profile: 'brief' }).remainingCalls, 4);
  assert.equal(budget({ profile: 'brief', round: 3, remainingCalls: 99 }).remainingCalls, 1);
  assert.equal(budget({ profile: 'report', round: 7, remainingCalls: 2 }).remainingCalls, 2);
  for (const remainingCalls of [0, -1, 1.5, '2', NaN, Infinity]) assert.throws(() => budget({ remainingCalls }), /remaining calls/);
});

test('server allows fallback only when two provider attempts remain', async () => {
  const old = globalThis.fetch;
  try {
    let attempts = 0;
    globalThis.fetch = async () => { attempts++; return { ok: false, status: 503 }; };
    await assert.rejects(completeWithFallback({ profile: 'brief', round: 3, remainingCalls: 1, messages: [] }));
    assert.equal(attempts, 1);
    attempts = 0;
    globalThis.fetch = async () => ++attempts === 1 ? { ok: false, status: 503 } : response(reply('Recovered'));
    const result = await completeWithFallback({ profile: 'brief', round: 2, remainingCalls: 2, messages: [] });
    assert.equal(attempts, 2);
    assert.equal(result.research.providerCalls, 2);
  } finally { globalThis.fetch = old; }
});

test('client and actual server count fallback attempts toward the total profile cap', async () => {
  const old = globalThis.fetch, c = context();
  let attempts = 0, requests = 0;
  try {
    globalThis.fetch = async () => ++attempts % 2 ? { ok: false, status: 503 } : response(reply('A useful continued draft. ', 'length'));
    c.fetch = async (_url, init) => { requests++; return response(await completeWithFallback(JSON.parse(init.body))); };
    const result = await c.agentLoop({ system: 'Use sources', turns: [{ role: 'user', content: 'Research' }], profile: 'brief' });
    assert.equal(attempts, 4);
    assert.equal(requests, 2);
    assert.equal(result.status, 'partial');
    assert.match(result.text, /useful continued draft/);
  } finally { globalThis.fetch = old; }
});

test('fallback accounting leaves synthesis available after collecting evidence', async () => {
  const old = globalThis.fetch, c = context();
  const bodies = []; let attempts = 0, lookups = 0;
  try {
    globalThis.fetch = async () => {
      attempts++;
      if (attempts % 2) return { ok: false, status: 503 };
      return response(attempts === 2 ? reply('', 'tool_calls', [{ id: 'lookup', type: 'function', function: { name: 'lookup', arguments: '{}' } }]) : reply('Synthesized with stated research gaps.'));
    };
    c.fetch = async (_url, init) => { const body = JSON.parse(init.body); bodies.push(body); return response(await completeWithFallback(body)); };
    const result = await c.agentLoop({ system: 'Use sources', turns: [{ role: 'user', content: 'Research' }], profile: 'brief', tools: [{ name: 'lookup', execute: () => { lookups++; return { source: 'collected' }; } }] });
    assert.equal(attempts, 4); assert.equal(lookups, 1);
    assert.equal(bodies[0].remainingCalls, 3, 'one attempt is reserved for synthesis');
    assert.equal(bodies[1].round, 2); assert.equal(bodies[1].remainingCalls, 2); assert.equal(bodies[1].tools, undefined);
    assert.match(result.text, /Synthesized/);
  } finally { globalThis.fetch = old; }
});

test('missing provider-attempt metadata is charged conservatively', async () => {
  const c = context(); let requests = 0;
  c.fetch = async () => { requests++; return response(reply('Partial draft ', 'length')); };
  const result = await c.agentLoop({ system: 'Use sources', turns: [{ role: 'user', content: 'Research' }], profile: 'brief' });
  assert.equal(requests, 2);
  assert.equal(result.status, 'partial');
});

test('null tool-call entries remain inert in server and client validation', async () => {
  assert.equal(validCompletion(reply('', 'tool_calls', [null])), false);
  const c = context(); let executions = 0;
  c.fetch = async () => response({ ...reply('', 'tool_calls', [null]), research: { providerCalls: 1 } });
  const result = await c.agentLoop({ system: 'Use sources', turns: [{ role: 'user', content: 'Research' }], tools: [{ name: 'write', execute: () => { executions++; } }] });
  assert.equal(result.status, 'partial'); assert.equal(result.reason, 'invalid_tool'); assert.equal(executions, 0);
});
