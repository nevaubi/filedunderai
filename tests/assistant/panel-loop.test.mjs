// Unit tests for the side-panel agent loop (public/lib/assistant-loop.js). No network, no model.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

function ctx() {
  const c = vm.createContext({ console, URL, Date, performance, DOMException, AbortController, AbortSignal, JSON, setTimeout, clearTimeout, Promise });
  c.window = c;
  for (const f of ['public/lib/research.js', 'public/lib/assistant-loop.js']) vm.runInContext(fs.readFileSync(f, 'utf8'), c);
  return c;
}
const call = (name, args, id = name) => ({ id, type: 'function', function: { name, arguments: typeof args === 'string' ? args : JSON.stringify(args) } });
const say = (content, tool_calls, extra = {}) => ({ choices: [{ finish_reason: tool_calls ? 'tool_calls' : 'stop', message: { role: 'assistant', content, tool_calls, ...extra } }], usage: { completion_tokens: 100 }, research: { chargedTokens: 100 } });
const tool = (name, run, parameters = { type: 'object', properties: {} }) => ({ name, description: name, parameters, run });
function scripted(replies) {
  const sent = [];
  const request = async req => { sent.push(JSON.parse(JSON.stringify({ messages: req.messages, tools: req.tools, budget: req.budget, final: req.final }))); const r = replies[sent.length - 1]; if (typeof r === 'function') return r(req); if (r instanceof Error) throw r; return r; };
  return { request, sent };
}
const base = () => [{ role: 'system', content: 'sys' }, { role: 'user', content: '[Context: test]\nquestion' }];

test('plan, parallel tools, ask_user pause and final answer', async () => {
  const c = ctx();
  let active = 0, peak = 0;
  const slow = name => tool(name, async () => { active++; peak = Math.max(peak, active); await new Promise(r => setTimeout(r, 40)); active--; return { output: name + ' output ev_0123456789abcdef', summary: name + ' ok', sources: [{ url: 'https://example.com/a', title: 'A' }, { url: 'https://example.com/a', title: 'dupe' }] }; });
  const { request, sent } = scripted([
    say('', [call('update_plan', { goal: 'Goal', steps: [{ title: 'One', status: 'doing' }, { title: 'Two', status: 'todo' }], notes: '' }), call('a', {}), call('b', {})]),
    say('', [call('ask_user', { question: 'Which period?', options: ['4Q', '8Q', '12Q', '16Q', 'extra'] })]),
    say('Final answer [[E:ev_0123456789abcdef]]'),
  ]);
  const asked = [], updates = [];
  const turn = await c.FU_AGENT.runAgent({ messages: base(), tools: [slow('a'), slow('b')], request, onUpdate: t => updates.push(t), onAsk: async q => { asked.push(q); return '8Q'; }, sleep: async () => {} });
  assert.equal(turn.content, 'Final answer [[E:ev_0123456789abcdef]]');
  assert.equal(turn.error, null);
  assert.equal(peak, 2, 'tools run in parallel');
  assert.equal(turn.plan.goal, 'Goal');
  assert.equal(turn.plan.steps.length, 2);
  assert.equal(asked.length, 1);
  assert.equal(asked[0].options.length, 4, 'options capped at 4');
  const toolMsgs = sent[1].messages.filter(m => m.role === 'tool');
  assert.deepEqual(Array.from(toolMsgs, m => m.tool_call_id), ['update_plan', 'a', 'b'], 'tool replies keep call order');
  assert.ok(sent[2].messages.some(m => m.role === 'tool' && m.content === 'User answered: 8Q'));
  assert.ok(updates.some(u => u.phase === 'Running 3 tools in parallel'));
  assert.ok(updates.some(u => u.phase === 'Waiting for your answer' && u.question?.question === 'Which period?'));
  const steps = turn.steps.filter(s => s.kind === 'tool');
  assert.deepEqual(Array.from(steps, s => s.status), ['done', 'done']);
  assert.ok(steps.every(s => Number.isInteger(s.ms)));
  assert.equal(steps[0].sources.length, 1, 'sources deduplicated per step');
});

test('every request carries a budget the server accepts, and the last step forces an answer', async () => {
  for (const deep of [false, true]) {
    const c = ctx(), mode = deep ? c.FU_AGENT.MODES.deep : c.FU_AGENT.MODES.fast;
    const { request, sent } = scripted(Array.from({ length: 60 }, (_, i) => say('', [call('t', {}, 'c' + i)])));
    const turn = await c.FU_AGENT.runAgent({ messages: base(), deep, tools: [tool('t', async () => 'ok')], request, sleep: async () => {} });
    assert.equal(sent.length, mode.maxSteps, `${mode.id}: one request per step`);
    for (const s of sent) assert.doesNotThrow(() => c.FU_RESEARCH_RUNTIME.requestBudget(s.budget), `${mode.id} budget ${JSON.stringify(s.budget)}`);
    assert.ok(sent.every(s => s.budget.profile === mode.profile));
    assert.equal(sent.at(-1).tools, undefined, 'final request offers no tools');
    assert.match(sent.at(-1).messages.at(-1).content, /Tool budget reached/);
    assert.match(turn.content, /couldn't complete|budget/i);
  }
});

test('deep mode runs one self-review pass; fast mode does not', async () => {
  const c = ctx(), t = tool('t', async () => 'data');
  const three = say('', [call('t', {}, '1'), call('t', {}, '2'), call('t', {}, '3')]);
  let r = scripted([three, say('Draft'), say('Revised final')]);
  let turn = await c.FU_AGENT.runAgent({ messages: base(), deep: true, tools: [t], request: r.request, sleep: async () => {} });
  assert.equal(turn.content, 'Revised final');
  assert.equal(turn.reviewed, true);
  assert.match(r.sent[2].messages.at(-1).content, /^\[Self-review/);
  assert.ok(turn.steps.some(s => s.kind === 'review'));
  r = scripted([three, say('Fast final')]);
  turn = await c.FU_AGENT.runAgent({ messages: base(), deep: false, tools: [t], request: r.request, sleep: async () => {} });
  assert.equal(turn.content, 'Fast final');
  assert.equal(r.sent.length, 2);
  assert.equal(turn.reviewed, false);
});

test('transient errors retry with backoff; client errors do not', async () => {
  const c = ctx(), waits = [];
  const e502 = Object.assign(new Error('The assistant did not return a usable response'), { status: 502 });
  let r = scripted([e502, Object.assign(new Error('rate'), { status: 429 }), say('ok')]);
  let turn = await c.FU_AGENT.runAgent({ messages: base(), request: r.request, sleep: async ms => { waits.push(ms); } });
  assert.equal(turn.content, 'ok');
  assert.deepEqual(waits, [1200, 2400]);
  r = scripted([Object.assign(new Error('Invalid tool definitions'), { status: 400 }), say('never')]);
  turn = await c.FU_AGENT.runAgent({ messages: base(), request: r.request, sleep: async () => {} });
  assert.equal(r.sent.length, 1);
  assert.equal(turn.error, 'Invalid tool definitions');
  r = scripted([new TypeError('Failed to fetch'), new TypeError('Failed to fetch'), new TypeError('Failed to fetch')]);
  turn = await c.FU_AGENT.runAgent({ messages: base(), request: r.request, sleep: async () => {} });
  assert.equal(r.sent.length, 3);
  assert.match(turn.error, /Failed to fetch/);
});

test('oversized requests are compacted and retried; evidence ids survive compaction', async () => {
  const c = ctx();
  const msgs = base();
  for (let i = 0; i < 12; i++) msgs.push({ role: 'assistant', content: '', tool_calls: [call('t', {}, 'x' + i)] }, { role: 'tool', tool_call_id: 'x' + i, content: `ev_${String(i).padStart(16, 'a')} ` + 'x'.repeat(40000) });
  const r = scripted([Object.assign(new Error('Request too large'), { status: 413 }), say('ok')]);
  const turn = await c.FU_AGENT.runAgent({ messages: msgs, request: r.request, sleep: async () => {} });
  assert.equal(turn.content, 'ok');
  assert.ok(JSON.stringify(r.sent[1].messages).length < JSON.stringify(r.sent[0].messages).length);
  const first = r.sent[1].messages.find(m => m.tool_call_id === 'x0');
  assert.match(first.content, /older output compacted; evidence ids kept: ev_aaaaaaaaaaaaaaa0/);
  const lastTool = r.sent[1].messages.filter(m => m.role === 'tool').at(-1);
  assert.ok(lastTool.content.length > 40000, 'recent window stays intact');
  const small = [{ role: 'tool', content: 'y'.repeat(5000) }];
  assert.equal(c.FU_AGENT.compact(small, 10, 0) < 5000, true);
});

test('invalid, malformed and unknown tool calls never execute; repeated failures add a restart hint', async () => {
  const c = ctx();
  let executed = 0;
  const strict = tool('strict', async a => { executed++; return JSON.stringify(a); }, { type: 'object', properties: { ticker: { type: 'string' }, n: { type: 'integer', minimum: 1, maximum: 5 } }, required: ['ticker'] });
  const r = scripted([
    say('', [call('strict', { n: 3 }, 'a'), call('strict', '{"ticker":', 'b')]),
    say('', [call('missing', {}, 'c'), call('strict', { ticker: 'X', n: 9 }, 'd')]),
    say('', [call('strict', { ticker: 'NVDA', n: 2, extra: 'dropped' }, 'e')]),
    say('done'),
  ]);
  const turn = await c.FU_AGENT.runAgent({ messages: base(), tools: [strict], request: r.request, sleep: async () => {} });
  assert.equal(executed, 1);
  const replies = Object.fromEntries(r.sent[3].messages.filter(m => m.role === 'tool').map(m => [m.tool_call_id, m.content]));
  assert.match(replies.a, /Invalid arguments: arguments.ticker is required/);
  assert.match(replies.b, /not executed/);
  assert.match(replies.c, /Unknown tool missing/);
  assert.match(replies.d, /at most 5/);
  assert.equal(JSON.parse(replies.e).extra, undefined, 'unknown keys are dropped before execution');
  assert.ok(r.sent[2].messages.some(m => m.role === 'user' && /all failed/.test(m.content)), 'restart hint after two failing rounds');
  assert.ok(turn.steps.some(s => s.kind === 'note'));
  assert.equal(turn.content, 'done');
});

test('stop ends the turn, resolves a pending question and marks running steps', async () => {
  const c = ctx();
  let stop = false;
  const r = scripted([say('', [call('t', {}, 'a')]), say('never')]);
  const turn = await c.FU_AGENT.runAgent({ messages: base(), tools: [tool('t', async () => { stop = true; return 'x'; })], request: r.request, stopped: () => stop, sleep: async () => {} });
  assert.equal(turn.content, '_Stopped._');
  assert.equal(turn.stopped, true);
  assert.equal(turn.error, null);
  assert.equal(r.sent.length, 1);
  stop = false;
  const r2 = scripted([say('', [call('ask_user', { question: 'Q?', options: ['a', 'b'] })]), say('never')]);
  const t2 = await c.FU_AGENT.runAgent({ messages: base(), request: r2.request, stopped: () => stop, onAsk: async () => { stop = true; return '(user stopped)'; }, sleep: async () => {} });
  assert.equal(t2.stopped, true);
  assert.equal(r2.sent.length, 1);
  const ctl = new AbortController();
  const r3 = scripted([async () => { ctl.abort(); throw new DOMException('aborted', 'AbortError'); }]);
  const t3 = await c.FU_AGENT.runAgent({ messages: base(), request: r3.request, signal: ctl.signal, stopped: () => ctl.signal.aborted, sleep: async () => {} });
  assert.equal(t3.content, '_Stopped._');
});

test('reasoning summaries come from provider fields or think blocks and are excluded from the answer', async () => {
  const c = ctx();
  assert.deepEqual(JSON.parse(JSON.stringify(c.FU_AGENT.splitReasoning({ content: '<think>Plan the work</think>Answer' }))), { text: 'Answer', reasoning: 'Plan the work' });
  assert.equal(c.FU_AGENT.splitReasoning({ content: 'A', reasoning_content: 'R' }).reasoning, 'R');
  const r = scripted([say('', [call('t', {})], { reasoning_content: '**Scoping** first line\nmore' }), say('<think>check</think>Final')]);
  const turn = await c.FU_AGENT.runAgent({ messages: base(), tools: [tool('t', async () => 'x')], request: r.request, sleep: async () => {} });
  assert.equal(turn.content, 'Final');
  const thinks = turn.steps.filter(s => s.kind === 'think');
  assert.equal(thinks.length, 2);
  assert.equal(thinks[0].summary, 'Scoping first line');
  assert.ok(!r.sent[1].messages.some(m => /<think>/.test(m.content || '')));
});

test('argument validation covers types, enums, nullable, arrays and bounds', () => {
  const c = ctx(), v = c.FU_AGENT.validateArgs;
  const schema = { type: 'object', properties: { s: { type: 'string', maxLength: 3 }, e: { type: 'string', enum: ['a', 'b'] }, n: { type: ['integer', 'null'] }, list: { type: 'array', maxItems: 2, items: { type: 'object', properties: { x: { type: 'number' } }, required: ['x'] } } }, required: ['s'] };
  assert.equal(v(schema, { s: 'abc', e: 'a', n: null, list: [{ x: 1.5 }] }).ok, true);
  const bad = v(schema, { s: 'abcd', e: 'z', n: 1.5, list: [{}, { x: '1' }, { x: 2 }] });
  assert.equal(bad.ok, false);
  for (const msg of ['longer than 3', 'one of a, b', 'integer or null', 'more than 2 items', 'list[0].x is required', 'list[1].x must be number']) assert.ok(bad.errors.some(e => e.includes(msg)), msg + ' in ' + bad.errors.join(' | '));
  assert.equal(v(schema, []).ok, false);
});
