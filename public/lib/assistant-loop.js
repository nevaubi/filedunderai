/* Side-panel research agent loop (browser tool execution <-> /api/ai Fireworks chat completions).
   Ported from the reference loop: model-maintained plan, clarifying questions that pause the loop,
   parallel tool calls, transient-error retries, context compaction, an approach-restart hint after
   repeated tool failures and one self-review pass in Deep mode. Plain script; also loads in Node tests. */
(function (root) {
  'use strict';
  const MODES = Object.freeze({
    fast: Object.freeze({ id: 'fast', label: 'Fast', profile: 'agent_fast', maxSteps: 14, review: false }),
    deep: Object.freeze({ id: 'deep', label: 'Deep', profile: 'agent_deep', maxSteps: 32, review: true }),
  });
  const TRANSIENT = /timeout|timed out|fetch failed|failed to fetch|network|load failed|502|503|504|429|rate limit|overloaded|ECONN|did not return a usable/i;
  const TOOL_OUTPUT_CHARS = 14000, STEP_OUTPUT_CHARS = 4000, TOOL_TIMEOUT_MS = 95000;
  const REVIEW = '[Self-review, not from the user] Check your draft critically: any number without a tool source or citation, any part of the question unanswered, any contradiction between sources, any stale data? If more work is needed, call update_plan and then tools. Otherwise reply with the final answer in full (revised if needed). Do not mention this review.';
  const BUDGET = '[Note, not from the user] Tool budget reached. Write the best answer you can from what you have, noting gaps. Do not call tools.';
  const RESTART = '[Note, not from the user] Your last tool calls all failed. Step back, call update_plan with a different approach (other tools, sources or queries) and continue.';

  const clip = (s, n) => (s.length > n ? s.slice(0, n) + '\n…[truncated]' : s);
  const evidenceIds = s => [...new Set(String(s).match(/ev_[a-f0-9]{16}/g) || [])];

  /** Shrink older tool outputs so long runs stay under the request limit; the recent window stays intact.
      Evidence IDs survive compaction so earlier citations keep resolving. Returns the resulting size. */
  function compact(messages, budget = 450000, keep = 8) {
    let size = JSON.stringify(messages).length;
    for (let i = 0; i < messages.length - keep && size > budget; i++) {
      const m = messages[i];
      if (m.role === 'tool' && typeof m.content === 'string' && m.content.length > 700 && !m.compacted) {
        const before = m.content.length, ids = evidenceIds(m.content).slice(0, 120);
        m.content = m.content.slice(0, 500) + '\n…[older output compacted' + (ids.length ? '; evidence ids kept: ' + ids.join(', ') : '') + ']';
        m.compacted = true;
        size -= before - m.content.length;
      }
    }
    return size;
  }

  /** Reasoning summaries: provider reasoning fields or inline <think> blocks. The answer text excludes them. */
  function splitReasoning(message) {
    let text = typeof message?.content === 'string' ? message.content : '';
    const parts = [];
    for (const k of ['reasoning_content', 'reasoning']) if (typeof message?.[k] === 'string' && message[k].trim()) parts.push(message[k].trim());
    text = text.replace(/<think>([\s\S]*?)(<\/think>|$)/g, (_, inner) => { if (inner.trim()) parts.push(inner.trim()); return ''; });
    return { text: text.trim(), reasoning: parts.join('\n\n').slice(0, 6000) };
  }

  function dedupeSources(list) {
    const seen = new Set(), out = [];
    for (const s of list || []) { if (!s?.url || seen.has(s.url)) continue; seen.add(s.url); out.push(s); }
    return out;
  }

  /** Minimal JSON-schema check for tool arguments: types, required keys, enums, array items, bounds.
      Unknown keys are dropped rather than passed to an executor. */
  function validateArgs(schema, args, path = 'arguments') {
    const errors = [];
    const typeOf = v => v === null ? 'null' : Array.isArray(v) ? 'array' : Number.isInteger(v) ? 'integer' : typeof v;
    const check = (s, v, p) => {
      if (!s || typeof s !== 'object') return v;
      const types = [].concat(s.type || []);
      if (types.length) {
        const t = typeOf(v), ok = types.some(x => x === t || (x === 'number' && t === 'integer'));
        if (!ok) { errors.push(`${p} must be ${types.join(' or ')}`); return v; }
      }
      if (v == null) return v;
      if (s.enum && !s.enum.includes(v)) { errors.push(`${p} must be one of ${s.enum.join(', ')}`); return v; }
      if (typeof v === 'number') {
        if (!Number.isFinite(v)) { errors.push(`${p} must be finite`); return v; }
        if (s.minimum != null && v < s.minimum) errors.push(`${p} must be at least ${s.minimum}`);
        if (s.maximum != null && v > s.maximum) errors.push(`${p} must be at most ${s.maximum}`);
      }
      if (typeof v === 'string' && s.maxLength != null && v.length > s.maxLength) errors.push(`${p} is longer than ${s.maxLength} characters`);
      if (Array.isArray(v)) {
        if (s.maxItems != null && v.length > s.maxItems) errors.push(`${p} has more than ${s.maxItems} items`);
        return v.map((x, i) => check(s.items, x, `${p}[${i}]`));
      }
      if (typeOf(v) === 'object' && s.properties) {
        const out = {};
        for (const k of s.required || []) if (v[k] === undefined) errors.push(`${p}.${k} is required`);
        for (const [k, sub] of Object.entries(s.properties)) if (v[k] !== undefined) out[k] = check(sub, v[k], `${p}.${k}`);
        return out;
      }
      return v;
    };
    const value = check(schema || { type: 'object', properties: {} }, args, path);
    return { ok: !errors.length, errors, value };
  }

  function normalizeResult(r) {
    if (r == null) return { output: '(no output)', summary: 'done' };
    if (typeof r === 'string') return { output: r, summary: r.split('\n')[0].slice(0, 100) };
    const output = typeof r.output === 'string' ? r.output : JSON.stringify(r.output ?? r);
    return { output, summary: String(r.summary || 'done').slice(0, 160), sources: dedupeSources(r.sources).slice(0, 12), refs: Array.isArray(r.refs) ? r.refs.slice(0, 12) : undefined };
  }

  function httpError(status, message) { const e = new Error(message || `Assistant request failed (${status})`); e.status = status; return e; }

  /** Default model step: POST to /api/ai with the shared bounded budget fields. */
  async function defaultRequest({ messages, tools, signal, budget }) {
    const r = await (root.fetch)('/api/ai', { method: 'POST', headers: { 'Content-Type': 'application/json' }, signal,
      body: JSON.stringify({ messages: messages.map(({ compacted, ...m }) => m), tools: tools && tools.length ? tools : undefined, ...budget }) });
    if (!r.ok) { const body = await r.json().catch(() => ({})); throw httpError(r.status, body.error || r.statusText); }
    return r.json();
  }

  async function stepWithRetry(run, messages, { stopped, sleep, attempts = 3 }) {
    let last;
    for (let a = 0; a < attempts; a++) {
      if (stopped()) throw new Error('Stopped');
      try { return await run(); }
      catch (e) {
        if (e?.name === 'AbortError' || stopped()) throw new Error('Stopped');
        last = e;
        const status = e?.status || 0, msg = String(e?.message || '');
        if (status === 413 || /too large|too long/i.test(msg)) compact(messages, 220000, 4);
        else if (status && status < 500 && status !== 429 && status !== 408) throw e;
        else if (!status && !TRANSIENT.test(msg)) throw e;
        if (a < attempts - 1) await sleep(1200 * (a + 1));
      }
    }
    throw last;
  }

  /**
   * Run one assistant turn.
   * opts.messages: chat messages (system, prior turns, the new user message); mutated in place.
   * opts.tools: [{ name, description, parameters, run(args, ctx) }]; update_plan and ask_user are handled here.
   * opts.onUpdate(turn) on every change; opts.onAsk(question) resolves with the user's answer.
   */
  async function runAgent(opts) {
    const { messages, tools = [], stopped = () => false, onUpdate = () => {}, onAsk, signal } = opts;
    const mode = MODES[opts.mode] || (opts.deep ? MODES.deep : MODES.fast);
    const request = opts.request || defaultRequest, sleep = opts.sleep || (ms => new Promise(r => setTimeout(r, ms)));
    const now = opts.now || (() => (root.performance ? root.performance.now() : Date.now()));
    const runtime = root.FU_RESEARCH_RUNTIME, p = runtime?.profile ? runtime.profile(mode.profile) : { maxCalls: mode.maxSteps + 4, maxTokens: 6000, totalTokens: 1e9, finalTokens: 0, maxMs: 1e9, requestMs: 60000, maxTools: 1e9 };
    const maxSteps = Math.min(mode.maxSteps, p.maxCalls - 2);
    const byName = new Map(tools.map(t => [t.name, t]));
    const spec = tools.map(t => ({ type: 'function', function: { name: t.name, description: t.description, parameters: t.parameters || { type: 'object', properties: {} } } }));
    const t0 = now();
    const turn = { content: '', steps: [], plan: null, phase: 'Thinking', mode: mode.id, error: null, reviewed: false, stats: { modelCalls: 0, toolCalls: 0, tokens: 0 } };
    const emit = () => onUpdate({ ...turn, steps: turn.steps.map(s => ({ ...s })), plan: turn.plan && { ...turn.plan, steps: turn.plan.steps.slice() } });
    let reviewed = !mode.review, failStreak = 0, tokens = 0, round = 0;
    let stepSeq = 0;
    const addStep = s => { const st = { id: 's' + (++stepSeq), status: 'running', ...s }; turn.steps.push(st); return st; };
    try {
      for (let step = 0; step < maxSteps; step++) {
        if (stopped()) throw new Error('Stopped');
        const elapsed = now() - t0, remainingTokens = p.totalTokens - tokens;
        const final = step === maxSteps - 1 || remainingTokens <= p.finalTokens * 1.5 || elapsed >= p.maxMs - p.requestMs || turn.stats.toolCalls >= p.maxTools;
        if (final) messages.push({ role: 'user', content: BUDGET });
        compact(messages);
        turn.phase = step === 0 ? 'Thinking' : final ? 'Writing the answer' : 'Reasoning over results';
        emit();
        const budget = () => ({ profile: mode.profile, round: Math.min(round, p.maxCalls - 1), remainingCalls: Math.max(1, p.maxCalls - round), maxTokens: Math.max(1, Math.min(p.maxTokens, remainingTokens)), remainingTokens: Math.max(1, remainingTokens), timeoutMs: Math.max(1000, Math.min(p.requestMs, Math.floor(p.maxMs - (now() - t0)))) });
        const d = await stepWithRetry(() => { const b = budget(); round++; return request({ messages, tools: final ? undefined : spec, signal, budget: b, final }); }, messages, { stopped, sleep });
        turn.stats.modelCalls++;
        const charged = d?.research?.chargedTokens ?? d?.usage?.completion_tokens;
        tokens += Number.isFinite(charged) && charged > 0 ? charged : p.maxTokens;
        turn.stats.tokens = tokens;
        const choice = d?.choices?.[0], m = choice?.message;
        if (!m) throw httpError(502, 'The assistant returned an empty response');
        const { text, reasoning } = splitReasoning(m);
        if (reasoning) turn.steps.push({ id: 's' + (++stepSeq), kind: 'think', name: 'reasoning', summary: reasoning.split('\n').find(Boolean).replace(/\*\*/g, '').slice(0, 140), output: reasoning, status: 'done' });
        const calls = Array.isArray(m.tool_calls) ? m.tool_calls.slice(0, 12) : [];
        if (!calls.length || final) {
          messages.push({ role: 'assistant', content: text || '(no answer)' });
          if (!reviewed && turn.steps.filter(s => s.kind !== 'think').length >= 3 && text) {
            reviewed = true; turn.reviewed = true;
            turn.content = text; turn.draft = text;
            turn.phase = 'Reviewing the draft';
            turn.steps.push({ id: 's' + (++stepSeq), kind: 'review', name: 'self_review', label: 'Reviewed the draft', summary: 'Checked the draft for unsourced numbers, gaps and contradictions', status: 'done' });
            emit();
            messages.push({ role: 'user', content: REVIEW });
            continue;
          }
          turn.content = text || turn.content;
          break;
        }
        messages.push({ role: 'assistant', content: text, tool_calls: calls.map(c => ({ id: c.id, type: 'function', function: { name: c.function?.name, arguments: c.function?.arguments || '{}' } })) });
        turn.phase = calls.length > 1 ? `Running ${calls.length} tools in parallel` : 'Working';
        emit();
        let failed = 0;
        const replies = await Promise.all(calls.map(async c => {
          const name = String(c.function?.name || '');
          let args = {};
          try { args = JSON.parse(String(c.function?.arguments || '{}')); if (!args || typeof args !== 'object' || Array.isArray(args)) throw new Error('not an object'); }
          catch { failed++; const st = addStep({ kind: 'tool', name, summary: 'Malformed arguments; not executed', status: 'error' }); st.ms = 0; emit(); return 'ERROR: arguments were not a JSON object; the tool was not executed.'; }
          if (name === 'update_plan') {
            const steps = (Array.isArray(args.steps) ? args.steps : []).slice(0, 12).map(s => ({ title: String(s?.title || '').slice(0, 160), status: ['todo', 'doing', 'done', 'dropped'].includes(s?.status) ? s.status : 'todo' })).filter(s => s.title);
            turn.plan = { goal: String(args.goal || '').slice(0, 300), steps, notes: String(args.notes || '').slice(0, 2000) };
            emit();
            return 'Plan updated.';
          }
          if (name === 'ask_user') {
            const q = { question: String(args.question || '').slice(0, 500), options: (Array.isArray(args.options) ? args.options : []).map(o => String(o).slice(0, 120)).filter(Boolean).slice(0, 4) };
            const st = addStep({ kind: 'ask', name, args, summary: q.question });
            turn.phase = 'Waiting for your answer';
            turn.question = q;
            emit();
            const answer = onAsk ? await onAsk(q) : 'No answer. Make a sensible assumption and state it.';
            turn.question = null;
            st.status = 'done'; st.output = answer; st.summary = q.question + ' → ' + answer;
            turn.phase = 'Working';
            emit();
            return `User answered: ${answer}`;
          }
          const tool = byName.get(name);
          const st = addStep({ kind: tool?.kind || 'tool', name, label: tool?.label, args, summary: '…' });
          emit();
          const start = now();
          try {
            if (!tool) throw new Error(`Unknown tool ${name}`);
            const v = validateArgs(tool.parameters, args);
            if (!v.ok) throw new Error('Invalid arguments: ' + v.errors.slice(0, 4).join('; '));
            turn.stats.toolCalls++;
            let timer;
            const r = normalizeResult(await Promise.race([tool.run(v.value, { signal, turn }), new Promise((_, rej) => { timer = setTimeout(() => rej(new Error('Tool timed out')), TOOL_TIMEOUT_MS); })]).finally(() => clearTimeout(timer)));
            Object.assign(st, { summary: r.summary, sources: r.sources, refs: r.refs, output: clip(r.output, STEP_OUTPUT_CHARS), status: 'done' });
            return clip(r.output, TOOL_OUTPUT_CHARS);
          } catch (e) {
            if (e?.name === 'AbortError' || stopped()) { st.status = 'error'; st.summary = 'Stopped'; return 'ERROR: stopped by the user'; }
            failed++;
            Object.assign(st, { summary: String(e?.message || e).slice(0, 200), status: 'error' });
            return `ERROR: ${String(e?.message || e).slice(0, 600)}`;
          } finally { st.ms = Math.round(now() - start); emit(); }
        }));
        calls.forEach((c, i) => messages.push({ role: 'tool', tool_call_id: c.id, content: replies[i] }));
        failStreak = failed && failed === calls.length ? failStreak + 1 : 0;
        if (failStreak >= 2) { failStreak = 0; messages.push({ role: 'user', content: RESTART }); turn.steps.push({ id: 's' + (++stepSeq), kind: 'note', name: 'restart', label: 'Changed approach', summary: 'Repeated tool failures; asked the model to change approach', status: 'done' }); }
      }
      if (!turn.content) turn.content = "I couldn't complete an answer within the tool budget. Try narrowing the question.";
    } catch (e) {
      const msg = String(e?.message || e);
      turn.content = turn.content || (msg === 'Stopped' ? '_Stopped._' : '');
      turn.error = msg === 'Stopped' ? null : msg;
      turn.stopped = msg === 'Stopped';
    }
    turn.phase = null; turn.question = null;
    turn.steps.forEach(s => { if (s.status === 'running') s.status = 'error'; });
    turn.ms = Math.round(now() - t0);
    emit();
    return turn;
  }

  root.FU_AGENT = Object.freeze({ MODES, runAgent, compact, splitReasoning, dedupeSources, validateArgs, normalizeResult, TRANSIENT });
})(typeof window !== 'undefined' ? window : globalThis);
