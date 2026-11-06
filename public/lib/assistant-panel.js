/* Global AI assistant side panel: docked to the right edge (content reflows) or popped out as a draggable,
   resizable window; full screen on phones. Runs the FU_AGENT loop with FU_ASSISTANT_TOOLKIT tools, renders the
   live plan, step timeline, clarifying questions and cited markdown answers, and keeps conversations in this
   browser. Handoff: window.dispatchEvent(new CustomEvent("fu:research-request", { detail: "prompt" })) opens the
   panel and prefills the prompt for review; it never submits and never discards an active turn.
   Depends on app.js globals at call time (el, md helpers, openSource, loadFilingDoc, drawer, DR, toast, copyText). */
(function (root) {
  'use strict';
  const doc = root.document;
  const UI_KEY = 'fu:assistant:ui', HIST_KEY = 'fu:assistant:history:v1', CUR_KEY = 'fu:assistant:current';
  const MAX_SESSIONS = 40, MAX_SESSION_BYTES = 220000, DOCK_DEFAULT = 420;
  const read = (k, d) => { try { const v = root.localStorage.getItem(k); return v == null ? d : JSON.parse(v); } catch { return d; } };
  const write = (k, v) => { try { root.localStorage.setItem(k, JSON.stringify(v)); return true; } catch { return false; } };
  const escH = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const hostOf = u => { try { return new URL(u).hostname.replace(/^www\./, ''); } catch { return String(u || ''); } };
  const favicon = u => `https://www.google.com/s2/favicons?domain=${encodeURIComponent(hostOf(u))}&sz=32`;
  const uid = () => Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
  const mobile = () => typeof root.matchMedia === 'function' && root.matchMedia('(max-width: 860px)').matches;
  const routeId = () => { const h = (root.location.hash || '#markets').slice(1); if (h.startsWith('company-')) return 'company'; const i = h.indexOf('-'); return i > 0 ? h.slice(0, i) : h; };
  const routeTicker = () => (root.location.hash.match(/^#company-([a-z0-9.-]+)/i)?.[1] || '').toUpperCase();
  const toastMsg = m => { if (typeof toast === 'function') toast(m); };

  const ICON = {
    spark: '<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z"/>',
    dock: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M15 4v16"/>',
    float: '<rect x="3" y="4" width="18" height="16" rx="2"/><rect x="11" y="11" width="8" height="6" rx="1"/>',
    plus: '<path d="M12 5v14M5 12h14"/>', x: '<path d="M6 6l12 12M18 6 6 18"/>', history: '<path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5M12 7v5l3 2"/>',
    send: '<path d="M12 19V5M5 12l7-7 7 7"/>', stop: '<rect x="7" y="7" width="10" height="10" rx="1.5"/>', copy: '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V5a1 1 0 0 0-1-1H5a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h3"/>',
    grip: '<circle cx="9" cy="8" r="1"/><circle cx="15" cy="8" r="1"/><circle cx="9" cy="16" r="1"/><circle cx="15" cy="16" r="1"/><circle cx="9" cy="12" r="1"/><circle cx="15" cy="12" r="1"/>',
    chev: '<path d="M9 6l6 6-6 6"/>', check: '<path d="M5 12l5 5 9-10"/>', circle: '<circle cx="12" cy="12" r="7"/>', dot: '<circle cx="12" cy="12" r="7"/><circle cx="12" cy="12" r="2.5"/>',
    warn: '<path d="M12 4 2.5 20h19z"/><path d="M12 10v4M12 17v.5"/>', brain: '<path d="M9 4a3 3 0 0 0-3 3 3 3 0 0 0-2 5 3 3 0 0 0 2 5 3 3 0 0 0 6 1V5a3 3 0 0 0-3-1z"/><path d="M15 4a3 3 0 0 1 3 3 3 3 0 0 1 2 5 3 3 0 0 1-2 5 3 3 0 0 1-6 1"/>',
    code: '<path d="M8 8l-4 4 4 4M16 8l4 4-4 4M13 5l-2 14"/>', compass: '<circle cx="12" cy="12" r="9"/><path d="M15.5 8.5l-2 5-5 2 2-5z"/>', question: '<circle cx="12" cy="12" r="9"/><path d="M9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.6.3-1 .9-1 1.6v.6M12 17v.5"/>',
    globe: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c3 3 3 15 0 18M12 3c-3 3-3 15 0 18"/>', file: '<path d="M6 3h9l4 4v14H6z"/><path d="M14 3v5h5M9 13h7M9 17h5"/>', wrench: '<path d="M14.5 6.5a4 4 0 0 0-5.3 5.3L4 17l3 3 5.2-5.2a4 4 0 0 0 5.3-5.3l-2.5 2.5-2.5-.5-.5-2.5z"/>',
    list: '<path d="M9 6h11M9 12h11M9 18h11M4 6h.5M4 12h.5M4 18h.5"/>', network: '<circle cx="5" cy="12" r="2"/><circle cx="19" cy="6" r="2"/><circle cx="19" cy="18" r="2"/><path d="M7 12h5l5-5M12 12l5 5"/>', review: '<path d="M4 12l5 5L20 6"/><path d="M4 6h7M4 18h4"/>',
  };
  const icon = (n, cls = '') => `<svg class="ai-ic ${cls}" viewBox="0 0 24 24" aria-hidden="true">${ICON[n] || ICON.wrench}</svg>`;
  function stepIcon(s) {
    if (s.status === 'error') return icon('warn', 'bad');
    if (s.kind === 'think') return icon('brain');
    if (s.kind === 'review') return icon('review');
    if (s.kind === 'note') return icon('warn');
    if (s.name === 'run_code') return icon('code');
    if (s.name === 'navigate_to' || s.name === 'get_page_context') return icon('compass');
    if (s.name === 'ask_user') return icon('question');
    if (s.name === 'web_map' || s.name === 'web_crawl') return icon('network');
    if (s.kind === 'filing' || /filing|financial|segment|evidence/.test(s.name)) return icon('file');
    if (s.kind === 'web' || /^web_|webpage|news/.test(s.name)) return icon('globe');
    return icon('wrench');
  }

  /* ---------------- history store ---------------- */
  const History = {
    all() { const v = read(HIST_KEY, []); return Array.isArray(v) ? v.filter(s => s && s.id && Array.isArray(s.messages)) : []; },
    get(id) { return this.all().find(s => s.id === id) || null; },
    save(session) {
      const slim = { ...session, messages: session.messages.map(slimMsg) };
      let json = JSON.stringify(slim);
      if (json.length > MAX_SESSION_BYTES) { slim.messages = slim.messages.map(m => ({ ...m, steps: (m.steps || []).map(({ output, args, ...s }) => s) })); json = JSON.stringify(slim); }
      let list = this.all().filter(s => s.id !== session.id);
      list.unshift(slim);
      list = list.sort((a, b) => (b.updated || 0) - (a.updated || 0)).slice(0, MAX_SESSIONS);
      while (list.length && !write(HIST_KEY, list)) list.pop();
      return !!list.length;
    },
    remove(id) { write(HIST_KEY, this.all().filter(s => s.id !== id)); if (read(CUR_KEY, null) === id) write(CUR_KEY, null); },
  };
  function slimMsg(m) {
    const { pending, phase, question, ...rest } = m;
    return { ...rest, steps: (m.steps || []).slice(-60).map(s => ({ ...s, output: s.output ? String(s.output).slice(0, 1500) : undefined, args: s.args && JSON.stringify(s.args).length < 1500 ? s.args : undefined })) };
  }

  /* ---------------- markdown with citations, filing quotes and charts ---------------- */
  function safeHref(u) {
    const s = String(u || '').trim();
    if (/^#[A-Za-z0-9._~-]+$/.test(s)) return { kind: 'hash', href: s };
    if (/^filing:/i.test(s)) { const [, tk, accn, ...docp] = s.split(':'); if (/^[A-Za-z][A-Za-z0-9.-]{0,9}$/.test(tk || '') && /^\d{10}-\d{2}-\d{6}$/.test(accn || '')) return { kind: 'filing', tk: tk.toUpperCase(), accn, doc: docp.join(':') }; return null; }
    try { const x = new URL(s); return /^https?:$/.test(x.protocol) ? { kind: 'web', href: x.href } : null; } catch { return null; }
  }
  function citeHTML(token) {
    if (typeof citationHTML === 'function') { try { return citationHTML(token); } catch { /* fall through */ } }
    return `<span class="cite unresolved" title="No matching evidence record">Unresolved: ${escH(token)}</span>`;
  }
  function inline(src) {
    // Protect code spans and citations, then links, then emphasis. Everything else is escaped.
    const slots = [];
    const keep = html => { slots.push(html); return `${slots.length - 1}`; };
    let s = String(src);
    s = s.replace(/`([^`]+)`/g, (_, c) => keep(`<code>${escH(c)}</code>`));
    s = s.replace(/\[\[(E:[^\]\s]+|[A-Z.-]+\|[^\]]+)\]\]/g, (_, t) => keep(citeHTML(t)));
    s = s.replace(/\[([^\]]{1,200})\]\(\s*([^)\s]+)(?:\s+"([^"]{0,300})")?\s*\)/g, (m, label, url, title) => {
      const h = safeHref(url);
      if (!h) return keep(escH(label));
      if (h.kind === 'filing') return keep(`<button type="button" class="cite asst-fcite" data-tk="${escH(h.tk)}" data-accn="${escH(h.accn)}"${title ? ` data-q="${escH(title)}"` : ''} title="${escH(title ? '“' + title + '” · open the filing with this passage highlighted' : 'Open the filing in the source panel')}">${icon('file')}${escH(label)}</button>`);
      if (h.kind === 'hash') return keep(`<a href="${escH(h.href)}" class="asst-in">${escH(label)}</a>`);
      return keep(`<a href="${escH(h.href)}" target="_blank" rel="noopener noreferrer"${title ? ` title="${escH(title)}"` : ''}>${escH(label)}</a>`);
    });
    s = s.replace(/https?:\/\/[^\s<>()\]]+[^\s<>()\].,;:!?'"]/g, u => { const h = safeHref(u); return h ? keep(`<a href="${escH(h.href)}" target="_blank" rel="noopener noreferrer">${escH(hostOf(u))}</a>`) : u; });
    s = escH(s).replace(/\*\*(.+?)\*\*/g, '<b>$1</b>').replace(/(^|[^*\w])\*([^*\s][^*]*?)\*(?!\w)/g, '$1<i>$2</i>').replace(/(^|\W)_([^_\s][^_]*?)_(?!\w)/g, '$1<i>$2</i>');
    return s.replace(/(\d+)/g, (_, n) => slots[+n]);
  }
  function renderMarkdown(text) {
    const lines = String(text || '').replace(/\r/g, '').split('\n');
    let html = '', i = 0, chartN = 0;
    const isTable = l => /^\s*\|.*\|\s*$/.test(l);
    while (i < lines.length) {
      const ln = lines[i];
      const fence = ln.match(/^\s{0,3}(`{3,}|~{3,})\s*([\w-]*)\s*$/);
      if (fence) {
        const body = []; i++;
        while (i < lines.length && !new RegExp('^\\s{0,3}' + fence[1][0] + '{' + fence[1].length + ',}\\s*$').test(lines[i])) body.push(lines[i++]);
        i++;
        if (fence[2] === 'chart') { const spec = parseChart(body.join('\n')); if (spec) { html += `<figure class="asst-chart" data-chart="${chartN++}" data-spec="${escH(JSON.stringify(spec))}"></figure>`; continue; } }
        html += `<pre><code>${escH(body.join('\n'))}</code></pre>`; continue;
      }
      if (isTable(ln) && i + 1 < lines.length && /^\s*\|[\s:|-]+\|\s*$/.test(lines[i + 1])) {
        const cells = l => { const slots = []; const p = l.replace(/\[\[[^\]]+\]\]|\[[^\]]*\]\([^)]*\)/g, t => { slots.push(t); return `${slots.length - 1}`; }); return p.trim().replace(/^\||\|$/g, '').split('|').map(c => c.replace(/(\d+)/g, (_, n) => slots[+n]).trim()); };
        const head = cells(ln), align = cells(lines[i + 1]).map(c => /^:-+:$/.test(c) ? 'center' : /-+:$/.test(c) ? 'right' : '');
        const numeric = col => col >= 0;
        let t = '<div class="asst-tw"><table><thead><tr>' + head.map((c, k) => `<th${align[k] ? ` style="text-align:${align[k]}"` : ''}>${inline(c)}</th>`).join('') + '</tr></thead><tbody>';
        i += 2;
        while (i < lines.length && isTable(lines[i])) { const r = cells(lines[i++]); t += '<tr>' + r.map((c, k) => `<td${align[k] ? ` style="text-align:${align[k]}"` : /^[-−+$€£(]?\d[\d,.]*[%xBMKT)]*\b/.test(c) && numeric(k) ? ' class="n"' : ''}>${inline(c)}</td>`).join('') + '</tr>'; }
        html += t + '</tbody></table></div>'; continue;
      }
      let m;
      if ((m = ln.match(/^(#{1,6})\s+(.*)/))) { const lv = Math.min(6, m[1].length + 2); html += `<h${lv}>${inline(m[2])}</h${lv}>`; i++; continue; }
      if (/^\s*(---|\*\*\*|___)\s*$/.test(ln)) { html += '<hr>'; i++; continue; }
      if (/^\s*>/.test(ln)) { const q = []; while (i < lines.length && /^\s*>/.test(lines[i])) q.push(lines[i++].replace(/^\s*>\s?/, '')); html += `<blockquote>${renderMarkdown(q.join('\n'))}</blockquote>`; continue; }
      if (/^\s*([-*+]|\d+[.)])\s+/.test(ln)) {
        const ordered = /^\s*\d+[.)]/.test(ln), tag = ordered ? 'ol' : 'ul';
        html += `<${tag}>`;
        while (i < lines.length && /^\s*([-*+]|\d+[.)])\s+/.test(lines[i])) {
          const item = lines[i].match(/^(\s*)([-*+]|\d+[.)])\s+(.*)/);
          if (item[1].length >= 2 && html.endsWith('</li>')) { html = html.slice(0, -5) + `<ul><li>${inline(item[3])}</li></ul></li>`; i++; continue; }
          html += `<li>${inline(item[3])}</li>`; i++;
        }
        html += `</${tag}>`; continue;
      }
      if (!ln.trim()) { i++; continue; }
      const para = [];
      while (i < lines.length && lines[i].trim() && !/^(#{1,6}\s|\s*([-*+]|\d+[.)])\s+|\s*>|\s{0,3}(`{3,}|~{3,}))/.test(lines[i]) && !(isTable(lines[i]) && /^\s*\|[\s:|-]+\|\s*$/.test(lines[i + 1] || ''))) para.push(lines[i++]);
      html += `<p>${para.map(inline).join('<br>')}</p>`;
    }
    return html;
  }
  function parseChart(src) {
    try {
      const s = JSON.parse(src);
      if (!s || !Array.isArray(s.data) || !s.data.length || s.data.length > 80) return null;
      const x = typeof s.x === 'string' ? s.x : Object.keys(s.data[0])[0];
      const series = (Array.isArray(s.series) && s.series.length ? s.series : Object.keys(s.data[0]).filter(k => k !== x && typeof s.data[0][k] === 'number')).filter(k => typeof k === 'string').slice(0, 5);
      if (!series.length) return null;
      const data = s.data.map(r => ({ [x]: String(r?.[x] ?? ''), ...Object.fromEntries(series.map(k => [k, Number.isFinite(r?.[k]) ? r[k] : null])) }));
      return { type: s.type === 'bar' ? 'bar' : 'line', title: String(s.title || '').slice(0, 120), unit: ['$', '%', ''].includes(s.unit) ? s.unit : '', x, series, data };
    } catch { return null; }
  }
  function fmtAxis(v, unit) {
    if (!Number.isFinite(v)) return '';
    const a = Math.abs(v), n = a >= 1e12 ? (v / 1e12).toFixed(1) + 'T' : a >= 1e9 ? (v / 1e9).toFixed(1) + 'B' : a >= 1e6 ? (v / 1e6).toFixed(1) + 'M' : a >= 1e3 ? (v / 1e3).toFixed(1) + 'K' : String(+v.toFixed(2));
    return unit === '%' ? n + '%' : unit === '$' ? '$' + n : n;
  }
  function drawCharts(host) {
    const d3 = root.d3;
    host.querySelectorAll('figure.asst-chart[data-spec]:not([data-drawn])').forEach(fig => {
      fig.setAttribute('data-drawn', '');
      let spec; try { spec = JSON.parse(fig.getAttribute('data-spec')); } catch { return; }
      const colors = ['var(--c1)', 'var(--c2)', 'var(--c3)', 'var(--c4)', 'var(--c5)'];
      fig.innerHTML = (spec.title ? `<figcaption>${escH(spec.title)}</figcaption>` : '') + `<div class="asst-legend">${spec.series.map((k, i) => `<span><i style="background:${colors[i]}"></i>${escH(k)}</span>`).join('')}</div>`;
      if (!d3) { fig.insertAdjacentHTML('beforeend', '<div class="faint">Chart unavailable (chart library did not load).</div>'); return; }
      const W = 400, H = 170, m = { t: 8, r: 8, b: 22, l: 44 };
      const svg = d3.select(fig).append('svg').attr('viewBox', `0 0 ${W} ${H}`).attr('role', 'img').attr('aria-label', spec.title || 'Chart');
      const xs = spec.data.map(r => r[spec.x]), vals = spec.data.flatMap(r => spec.series.map(k => r[k])).filter(Number.isFinite);
      const lo = Math.min(0, d3.min(vals) ?? 0), hi = Math.max(0, d3.max(vals) ?? 1);
      const y = d3.scaleLinear().domain([lo, hi === lo ? lo + 1 : hi]).nice().range([H - m.b, m.t]);
      const x = d3.scaleBand().domain(xs).range([m.l, W - m.r]).padding(spec.type === 'bar' ? 0.2 : 0);
      const xp = v => x(v) + x.bandwidth() / 2;
      svg.append('g').attr('class', 'grid').selectAll('line').data(y.ticks(4)).join('line').attr('x1', m.l).attr('x2', W - m.r).attr('y1', d => y(d)).attr('y2', d => y(d));
      svg.append('g').attr('class', 'ax').attr('transform', `translate(${m.l},0)`).call(d3.axisLeft(y).ticks(4).tickSize(0).tickFormat(v => fmtAxis(v, spec.unit))).call(g => g.select('.domain').remove());
      const every = Math.ceil(xs.length / 8);
      svg.append('g').attr('class', 'ax').attr('transform', `translate(0,${H - m.b})`).call(d3.axisBottom(x).tickSize(0).tickValues(xs.filter((_, i) => i % every === 0))).call(g => g.select('.domain').remove());
      if (spec.type === 'bar') {
        const inner = d3.scaleBand().domain(spec.series).range([0, x.bandwidth()]).padding(0.08);
        spec.series.forEach((k, si) => svg.append('g').selectAll('rect').data(spec.data.filter(r => Number.isFinite(r[k]))).join('rect').attr('x', r => x(r[spec.x]) + inner(k)).attr('width', inner.bandwidth()).attr('y', r => Math.min(y(r[k]), y(0))).attr('height', r => Math.abs(y(r[k]) - y(0))).attr('fill', colors[si]).append('title').text(r => `${k} · ${r[spec.x]}: ${fmtAxis(r[k], spec.unit)}`));
      } else spec.series.forEach((k, si) => {
        const pts = spec.data.filter(r => Number.isFinite(r[k]));
        svg.append('path').datum(pts).attr('fill', 'none').attr('stroke', colors[si]).attr('stroke-width', 1.8).attr('d', d3.line().x(r => xp(r[spec.x])).y(r => y(r[k])));
        svg.append('g').selectAll('circle').data(pts).join('circle').attr('cx', r => xp(r[spec.x])).attr('cy', r => y(r[k])).attr('r', 2.2).attr('fill', colors[si]).append('title').text(r => `${k} · ${r[spec.x]}: ${fmtAxis(r[k], spec.unit)}`);
      });
      fig.insertAdjacentHTML('beforeend', '<div class="asst-chart-note">Chart drawn from values the assistant collected with its tools; check the cited sources.</div>');
    });
  }

  /* ---------------- filing passage opener (renders into the existing source drawer) ---------------- */
  function findQuote(d, quote) {
    const norm = ch => /\s/.test(ch) ? ' ' : ch.replace(/[‘’‛′]/, "'").replace(/[“”]/, '"').replace(/[‐-―]/, '-').toLowerCase();
    const q = String(quote || '').split('').map(norm).join('').replace(/ +/g, ' ').trim();
    if (q.length < 8 || !d?.body) return [];
    const walker = d.createTreeWalker(d.body, 4, { acceptNode: n => n.parentElement && !/^(SCRIPT|STYLE)$/.test(n.parentElement.tagName) ? 1 : 2 });
    let full = ''; const map = [];
    while (walker.nextNode()) { const n = walker.currentNode, v = n.nodeValue; for (let j = 0; j < v.length; j++) { const c = norm(v[j]); if (c === ' ' && (full.endsWith(' ') || !full)) continue; full += c; map.push([n, j]); } }
    const at = full.indexOf(q);
    if (at < 0) return [];
    const segs = new Map();
    for (let k = at; k < at + q.length; k++) { const [n, j] = map[k]; const s = segs.get(n); if (s) s[1] = j + 1; else segs.set(n, [j, j + 1]); }
    const spans = [];
    for (const [n, [s, e]] of segs) {
      const mid = n.splitText(s); mid.splitText(e - s);
      const span = d.createElement('span'); span.className = spans.length ? 'fu-hit fu-hit-cont' : 'fu-hit';
      mid.parentNode.replaceChild(span, mid); span.append(mid); spans.push(span);
    }
    return spans;
  }
  function openFilingQuote(tk, accn, quote) {
    if (typeof loadFilingDoc !== 'function' || typeof drawer === 'undefined') { root.open(`https://www.sec.gov/edgar/search/#/q=${encodeURIComponent(accn)}`, '_blank', 'noopener'); return; }
    if (typeof hideTip === 'function') hideTip();
    const dh = doc.querySelector('#dh'), db = doc.querySelector('#db');
    const filing = (typeof D !== 'undefined' ? D.companies?.[tk]?.filings || [] : []).find(f => f.a === accn);
    const cik = typeof D !== 'undefined' ? D.companies?.[tk]?.cik : null;
    const edgar = cik ? `https://www.sec.gov/Archives/edgar/data/${Number(cik)}/${accn.replace(/-/g, '')}/` : `https://www.sec.gov/edgar/search/#/q=${encodeURIComponent(accn)}`;
    const marker = { assistantFiling: accn, quote };
    if (typeof DR !== 'undefined') { const active = doc.activeElement; if (!drawer.classList.contains('open')) DR.trigger = active && active !== doc.body && !drawer.contains(active) ? active : null; DR.cell = marker; }
    const close = typeof closeSource === 'function' ? closeSource : () => drawer.classList.remove('open');
    const btn = (cls, attrs, inner) => { const b = doc.createElement(attrs.href ? 'a' : 'button'); b.className = cls; Object.entries(attrs).forEach(([k, v]) => b.setAttribute(k, v)); b.innerHTML = inner; return b; };
    dh.innerHTML = ''; db.innerHTML = '';
    const row1 = doc.createElement('div'); row1.className = 'row';
    row1.innerHTML = `<span class="eyebrow">Filing passage cited by the assistant</span>`;
    const acts = doc.createElement('div'); acts.className = 'x'; acts.style.cssText = 'display:flex;gap:6px';
    acts.append(btn('btn', { href: edgar, target: '_blank', rel: 'noopener' }, 'EDGAR'), btn('iconbtn', { 'aria-label': 'Close source (Esc)', type: 'button' }, icon('x')));
    acts.lastChild.onclick = close; row1.append(acts);
    const row2 = doc.createElement('div'); row2.className = 'row'; row2.innerHTML = `<h2 tabindex="-1">${escH((typeof SHORT !== 'undefined' && SHORT[tk]) || tk)} · ${escH(filing ? `${filing.f} filed ${filing.d}` : 'Filing ' + accn)}</h2>`;
    const row3 = doc.createElement('div'); row3.className = 'row';
    row3.innerHTML = quote ? `<span class="asst-quote">“${escH(quote)}”</span>` : `<span class="muted" style="font-size:12.5px">Accession ${escH(accn)}</span>`;
    dh.append(row1, row2, row3);
    const status = doc.createElement('span'); status.className = 'faint'; status.style.fontSize = '12px'; status.textContent = 'Loading the filing…';
    db.insertAdjacentHTML('beforeend', `<div class="note">AI-cited passage. The quote is located by exact phrase match in the filing text; if it is not found, treat the assistant's statement as unverified.</div>`);
    const bar = doc.createElement('div'); bar.className = 'viewer-bar'; bar.innerHTML = '<span class="badge-src">Original filing</span><span style="flex:1"></span>'; bar.append(status);
    const wrap = doc.createElement('div'); wrap.className = 'viewer'; wrap.innerHTML = '<div class="viewer-load"><div class="spin"></div><div>Opening the full filing</div></div>';
    db.append(bar, wrap);
    drawer.classList.add('open'); drawer.setAttribute('aria-hidden', 'false');
    if (root.innerWidth < 860 && typeof scrim !== 'undefined') scrim.hidden = false;
    row2.querySelector('h2').focus({ preventScroll: true });
    loadFilingDoc(accn).then(html => {
      if (typeof DR !== 'undefined' && DR.cell !== marker) return;
      const iframe = doc.createElement('iframe');
      iframe.title = `${tk} filing ${accn}`; iframe.setAttribute('sandbox', 'allow-same-origin allow-popups allow-popups-to-escape-sandbox'); iframe.setAttribute('referrerpolicy', 'no-referrer');
      iframe.srcdoc = html + (typeof VIEWER_CSS === 'string' ? VIEWER_CSS : '<style>.fu-hit{background:#ffe766;box-shadow:0 0 0 2px #f2b600}</style>');
      wrap.append(iframe);
      iframe.addEventListener('load', () => {
        wrap.querySelector('.viewer-load')?.remove();
        const d = iframe.contentDocument; if (!d) return;
        const hits = quote ? findQuote(d, quote) : [];
        if (hits.length) {
          const go = () => { const keep = db.scrollTop; hits[0].scrollIntoView({ block: 'center' }); db.scrollTop = keep; };
          requestAnimationFrame(go); setTimeout(go, 700);
          status.textContent = 'Quoted passage highlighted';
        } else status.textContent = quote ? 'Quoted phrase not found verbatim; the statement is unverified' : 'Full filing';
        d.addEventListener('keydown', e => { if (e.key === 'Escape') close(); });
      }, { once: true });
    }).catch(err => { wrap.innerHTML = ''; wrap.insertAdjacentHTML('beforeend', `<div class="viewer-load"><div>This filing could not be opened here.</div><div class="faint" style="font-size:12px">${escH(err.message || err)}</div><a class="btn" href="${escH(edgar)}" target="_blank" rel="noopener">Open on EDGAR</a></div>`); status.textContent = ''; });
  }

  /* ---------------- suggestions ---------------- */
  const EXTRA_SUGGESTIONS = {};
  function suggestionsFor() {
    const id = routeId(), tk = routeTicker();
    if (EXTRA_SUGGESTIONS[id]?.length) return EXTRA_SUGGESTIONS[id].map(s => typeof s === 'function' ? s(tk) : s).slice(0, 4);
    if (id === 'company' && tk) return [`Summarize what matters on this page for ${tk}`, `How have ${tk}'s gross margin and free cash flow trended over the last 8 quarters? Chart it`, `What did ${tk}'s latest 10-Q say about risks and guidance? Quote the filing`, `Have ${tk} insiders been buying or selling, and how did the stock react to recent earnings?`];
    if (/supply/.test(id)) return ['Explain the biggest risks on this map', 'Which covered companies are most exposed to TSMC?', 'Simulate a 25% drop in Apple demand'];
    if (id === 'screener') return ['Screen for >20% revenue growth and >25% FCF margins', 'Explain what drives the top 5 names in this screen', 'Which S&P 500 names have the highest FCF yield with positive revenue growth?'];
    if (id === 'technicals' || id === 'ideas') return ['Compare 1-year volatility, beta and drawdown for NVDA, AMD and SMH', 'Which megacaps trade above their 200-day average? Run the numbers', 'Regress NVDA daily returns on SMH over the last year with run_code'];
    if (id === 'macro') return ['Is the 2s10s curve still inverted, and since when?', 'How has core CPI trended against the fed funds rate over two years? Chart it', 'Read the latest FOMC statement and tell me what changed'];
    if (id === 'sectors' || id === 'kpis' || id === 'compare') return ['Which sector had the strongest revenue growth last quarter, and why?', 'Compare capex intensity at Microsoft, Amazon and Alphabet over 8 quarters', 'Which semis have the highest R&D intensity? Chart it'];
    if (id === 'theses' || id === 'analyst' || id === 'watchlists') return ['Stress-test my newest thesis against the latest filings', 'What changed in my watchlist companies this week?', 'Find insider buying across my watchlist in the last 90 days'];
    return ['Screen for >20% revenue growth and >25% FCF margins', 'Which semis have the highest R&D intensity? Chart it', 'Read the latest Fed statement and tell me what changed', 'Compare JPM, BAC and WFC net income over eight quarters'];
  }

  /* ---------------- system prompt ---------------- */
  function systemPrompt() {
    const n = (typeof INDEX !== 'undefined' && INDEX?.companies?.length) || 0;
    return `You are Filed Under's research analyst: a careful, numerate equity researcher working inside the Filed Under app.
Today is ${new Date().toISOString().slice(0, 10)}.

You have structured SEC data for ${n || 'the'} covered US companies. For covered companies always use the tools (get_financials, get_segments, list_filings, read_filing, get_insider_trades) rather than memory. get_price_history/get_technicals/get_quotes for markets, get_macro for stored macro series, get_news for stored headlines, search_filings for EDGAR full-text search, web_research to find coverage, IR releases and recent news, read_webpage to read a page or PDF, web_map/web_crawl/web_extract for sites and tables, screen_companies to rank the universe, run_code (sandboxed JavaScript with statements, universe and prices loaded) for any non-trivial math, get_page_context to see what the user is looking at, navigate_to to open a page for the user when they ask to see something.

Agency:
- For anything beyond a one-tool lookup, call update_plan first (goal + steps) and update it as steps finish or the plan changes. Keep key findings in its notes.
- Call several independent tools in the same turn when possible; they run in parallel.
- Iterate: check what is missing, contradictory or unverified, then do targeted work. If an approach fails, change approach and say so in the plan.
- Use ask_user only when the request is genuinely ambiguous and the answer changes the work; otherwise make a sensible assumption and state it.
- Never invent numbers. If data is missing, say so plainly.

Citations (required; the app verifies them):
- Every financial value from get_financials or get_segments must be followed by its evidence id as [[E:ev_…]], e.g. "Q2 FY27 revenue of $46.74B [[E:ev_0123456789abcdef]]". Show the value as filed or rounded from it, with its period. The app checks displayed numbers against the cited record.
- Cite SEC filing text as markdown links [10-Q filed 2026-08-27](filing:TICKER:ACCESSION "4-10 word verbatim phrase") using accession numbers from tool output. Only use a quoted phrase you actually read with read_filing; it is highlighted in the source panel.
- Cite web pages and insider filings as normal markdown links to the URLs from tool output.
- Calculations (growth rates, ratios, run_code results) are labeled as calculated and show their inputs.

Answer format (Markdown): lead with a 1-2 sentence answer, then short sections, bullets and compact tables. When a trend or comparison is clearer as a picture, add a fenced code block with language "chart" containing JSON {"type":"line"|"bar","title":"…","unit":"$"|"%"|"","x":"period","series":["NVDA","AMD"],"data":[{"period":"FY24","NVDA":60.9e9,"AMD":22.7e9}]} using only numbers from tool outputs (full values, at most 40 points and 5 series). End with "What to watch" when the question is forward-looking. This is research, not personalized investment advice.`;
  }

  /* ---------------- the panel ---------------- */
  const S = { open: false, docked: true, deep: false, box: null, width: DOCK_DEFAULT, session: null, busy: false, question: null, answer: null, stop: false, ctl: null, scrollPinned: true };
  let P = null; // DOM refs

  function loadUI() {
    const u = read(UI_KEY, {}) || {};
    S.docked = u.docked !== false; S.deep = !!u.deep; S.open = !!u.open && !mobile();
    S.width = Number.isFinite(u.width) ? u.width : DOCK_DEFAULT;
    S.box = u.box && Number.isFinite(u.box.x) ? u.box : null;
  }
  const saveUI = () => write(UI_KEY, { open: S.open, docked: S.docked, deep: S.deep, width: S.width, box: S.box });
  function clampBox(b) {
    const W = root.innerWidth, H = root.innerHeight;
    const w = Math.min(Math.max(b.w, 340), W - 16), h = Math.min(Math.max(b.h, 360), H - 16);
    return { w, h, x: Math.min(Math.max(b.x, 8), W - w - 8), y: Math.min(Math.max(b.y, 8), H - h - 8) };
  }
  const clampWidth = w => Math.round(Math.min(Math.max(w, 340), Math.max(340, Math.min(720, root.innerWidth - 520))));

  function build() {
    const aside = doc.createElement('aside');
    aside.id = 'fu-asst'; aside.className = 'asst'; aside.setAttribute('aria-label', 'AI assistant'); aside.setAttribute('aria-hidden', 'true'); aside.tabIndex = -1;
    aside.innerHTML = `
      <div class="asst-edge" aria-hidden="true" title="Drag to resize"></div>
      <div class="asst-h">
        <span class="asst-grip">${icon('grip')}</span>
        <span class="asst-tile">${icon('spark')}</span>
        <b class="asst-title">Assistant</b>
        <span class="asst-route mono"></span>
        <span class="asst-hsp"></span>
        <button type="button" class="asst-ib" data-act="dock"></button>
        <a class="asst-ib" data-act="history" href="#research" title="Research history" aria-label="Research history">${icon('history')}</a>
        <button type="button" class="asst-ib" data-act="new" title="New chat" aria-label="New chat">${icon('plus')}</button>
        <button type="button" class="asst-ib" data-act="close" title="Close (⌘J)" aria-label="Close assistant">${icon('x')}</button>
      </div>
      <div class="asst-body" aria-live="polite"></div>
      <div class="asst-foot">
        <form class="asst-form">
          <textarea rows="1" maxlength="4000" aria-label="Message the assistant" placeholder="Ask, research, model, or “take me to…”"></textarea>
          <div class="asst-row">
            <div class="seg asst-depth" role="radiogroup" aria-label="Research depth">
              <button type="button" role="radio" data-depth="fast" title="Quick answers (up to 14 steps)">Fast</button>
              <button type="button" role="radio" data-depth="deep" title="More loops, cross-checks and a self-review pass (up to 32 steps)">Deep</button>
            </div>
            <span class="asst-hint faint">Enter to send · Shift+Enter for a new line</span>
            <button type="submit" class="asst-send" aria-label="Send">${icon('send')}</button>
          </div>
        </form>
        <p class="asst-note">AI-generated research, saved in this browser · <a href="#research">History</a> · Check the cited sources. Not investment advice.</p>
      </div>
      <div class="asst-resize" aria-hidden="true"></div>`;
    doc.body.append(aside);
    P = { aside, head: aside.querySelector('.asst-h'), body: aside.querySelector('.asst-body'), form: aside.querySelector('form'), ta: aside.querySelector('textarea'), send: aside.querySelector('.asst-send'), route: aside.querySelector('.asst-route'), dock: aside.querySelector('[data-act="dock"]'), depth: aside.querySelector('.asst-depth') };
    aside.querySelector('[data-act="close"]').onclick = () => setOpen(false);
    aside.querySelector('[data-act="new"]').onclick = () => newChat();
    aside.querySelector('[data-act="history"]').onclick = () => { if (mobile()) setOpen(false); };
    P.dock.onclick = () => setDocked(!S.docked);
    P.depth.querySelectorAll('button').forEach(b => b.onclick = () => { if (S.busy) return; S.deep = b.dataset.depth === 'deep'; saveUI(); syncChrome(); });
    P.form.addEventListener('submit', e => { e.preventDefault(); submit(); });
    P.ta.addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); submit(); } });
    P.ta.addEventListener('input', fit);
    P.body.addEventListener('scroll', () => { S.scrollPinned = P.body.scrollHeight - P.body.scrollTop - P.body.clientHeight < 240; });
    P.body.addEventListener('click', onBodyClick);
    P.head.addEventListener('pointerdown', e => drag(e, 'move'));
    aside.querySelector('.asst-resize').addEventListener('pointerdown', e => drag(e, 'resize'));
    aside.querySelector('.asst-edge').addEventListener('pointerdown', edgeDrag);
  }

  function syncChrome() {
    const html = doc.documentElement, float = !S.docked && !mobile();
    P.aside.classList.toggle('open', S.open);
    P.aside.classList.toggle('float', float);
    P.aside.classList.toggle('docked', !float);
    P.aside.setAttribute('aria-hidden', String(!S.open));
    P.aside.inert = !S.open;
    html.classList.toggle('asst-open', S.open);
    html.classList.toggle('asst-docked', S.open && S.docked && !mobile());
    html.style.setProperty('--asst-w', clampWidth(S.width) + 'px');
    if (float) { S.box = clampBox(S.box || { x: root.innerWidth - 470, y: 64, w: 440, h: Math.min(680, root.innerHeight - 96) }); Object.assign(P.aside.style, { left: S.box.x + 'px', top: S.box.y + 'px', width: S.box.w + 'px', height: S.box.h + 'px' }); }
    else Object.assign(P.aside.style, { left: '', top: '', width: '', height: '' });
    P.dock.innerHTML = S.docked ? icon('float') : icon('dock');
    P.dock.title = S.docked ? 'Pop out and move' : 'Dock to the right edge';
    P.dock.setAttribute('aria-label', P.dock.title);
    P.dock.hidden = mobile();
    P.depth.querySelectorAll('button').forEach(b => { const on = (b.dataset.depth === 'deep') === S.deep; b.classList.toggle('on', on); b.setAttribute('aria-checked', String(on)); b.disabled = S.busy; });
    const asking = !!S.question;
    P.ta.disabled = S.busy && !asking;
    P.ta.placeholder = asking ? 'Type your answer…' : S.busy ? 'Working…' : 'Ask, research, model, or “take me to…”';
    P.send.innerHTML = S.busy && !asking ? icon('stop') : icon('send');
    P.send.setAttribute('aria-label', S.busy && !asking ? 'Stop' : 'Send');
    P.send.title = S.busy && !asking ? 'Stop' : 'Send (Enter)';
    P.send.classList.toggle('stop', S.busy && !asking);
    P.route.textContent = (root.location.hash || '#markets').slice(0, 28);
    const btn = doc.querySelector('#asst-toggle');
    if (btn) { btn.setAttribute('aria-expanded', String(S.open)); btn.classList.toggle('on', S.open); }
  }
  function setOpen(open, { focus = true } = {}) {
    S.open = !!open; saveUI(); syncChrome();
    if (S.open && !P.body.childElementCount) renderAll();
    if (S.open && focus) setTimeout(() => P.ta.disabled ? P.aside.focus({ preventScroll: true }) : P.ta.focus({ preventScroll: true }), 30);
    if (!S.open) doc.querySelector('#asst-toggle')?.focus({ preventScroll: true });
  }
  function setDocked(d) { S.docked = !!d; saveUI(); syncChrome(); }
  function fit() { P.ta.style.height = 'auto'; P.ta.style.height = Math.min(160, Math.max(40, P.ta.scrollHeight)) + 'px'; }

  function drag(e, mode) {
    if (S.docked || mobile() || e.button !== 0 || (mode === 'move' && e.target.closest('button, a'))) return;
    e.preventDefault();
    const sx = e.clientX, sy = e.clientY, start = { ...S.box };
    P.aside.classList.add('dragging');
    const move = ev => { const dx = ev.clientX - sx, dy = ev.clientY - sy; S.box = clampBox(mode === 'move' ? { ...start, x: start.x + dx, y: start.y + dy } : { ...start, w: start.w + dx, h: start.h + dy }); Object.assign(P.aside.style, { left: S.box.x + 'px', top: S.box.y + 'px', width: S.box.w + 'px', height: S.box.h + 'px' }); };
    const up = () => { root.removeEventListener('pointermove', move); root.removeEventListener('pointerup', up); P.aside.classList.remove('dragging'); saveUI(); };
    root.addEventListener('pointermove', move); root.addEventListener('pointerup', up);
  }
  function edgeDrag(e) {
    if (!S.docked || mobile() || e.button !== 0) return;
    e.preventDefault();
    const sx = e.clientX, w0 = clampWidth(S.width);
    P.aside.classList.add('dragging');
    const move = ev => { S.width = clampWidth(w0 + (sx - ev.clientX)); doc.documentElement.style.setProperty('--asst-w', S.width + 'px'); };
    const up = () => { root.removeEventListener('pointermove', move); root.removeEventListener('pointerup', up); P.aside.classList.remove('dragging'); saveUI(); };
    root.addEventListener('pointermove', move); root.addEventListener('pointerup', up);
  }

  /* ---------------- rendering ---------------- */
  function renderAll() {
    P.body.innerHTML = '';
    const msgs = S.session?.messages || [];
    if (!msgs.length) { renderEmpty(); return; }
    const list = doc.createElement('div'); list.className = 'asst-msgs';
    P.body.append(list);
    msgs.forEach(m => list.append(renderMsg(m)));
    drawCharts(P.body);
    stick(true);
  }
  function renderEmpty() {
    const box = doc.createElement('div'); box.className = 'asst-empty';
    box.innerHTML = `<p class="asst-desk">Your research desk</p><p class="asst-sub">Reads SEC filings, statements, prices, insiders, macro and the web, then cites each source. Answers are AI-generated.</p><p class="eyebrow">Suggested investigations</p>`;
    const list = doc.createElement('div'); list.className = 'asst-sugg';
    suggestionsFor().forEach(s => { const b = doc.createElement('button'); b.type = 'button'; b.innerHTML = `<span>${escH(s)}</span>${icon('chev')}`; b.onclick = () => prefill(s); list.append(b); });
    box.append(list);
    const recent = History.all().slice(0, 3);
    if (recent.length) {
      box.insertAdjacentHTML('beforeend', '<p class="eyebrow" style="margin-top:18px">Recent</p>');
      const r = doc.createElement('div'); r.className = 'asst-recent';
      recent.forEach(s => { const b = doc.createElement('button'); b.type = 'button'; b.innerHTML = `<span>${escH(s.title)}</span><span class="faint mono">${escH(rel(s.updated))}</span>`; b.onclick = () => openSession(s.id); r.append(b); });
      box.append(r);
    }
    P.body.append(box);
  }
  function rel(t) { if (!t) return ''; const m = Math.max(0, (Date.now() - t) / 6e4); return m < 1 ? 'just now' : m < 60 ? `${Math.round(m)}m ago` : m < 1440 ? `${Math.round(m / 60)}h ago` : `${Math.round(m / 1440)}d ago`; }
  function stick(force) { if (force || S.scrollPinned) P.body.scrollTop = P.body.scrollHeight; }

  function renderMsg(m) {
    const node = doc.createElement('div');
    node.className = 'asst-m ' + (m.role === 'user' ? 'u' : 'a');
    node.dataset.id = m.id;
    if (m.role === 'user') { node.textContent = m.content; return node; }
    fillTurn(node, m);
    return node;
  }
  function updateTurn(m) {
    const node = P.body.querySelector(`.asst-m[data-id="${String(m.id).replace(/[^a-z0-9]/gi, '')}"]`);
    if (!node) return renderAll();
    fillTurn(node, m); drawCharts(node); stick();
  }
  function planCard(plan, live) {
    const done = plan.steps.filter(s => s.status === 'done').length, total = plan.steps.length;
    const d = doc.createElement('details'); d.className = 'asst-plan'; if (live || plan._open) d.open = true;
    d.innerHTML = `<summary>${icon('list')}<span>Research plan</span><span class="mono faint">${done}/${total}</span></summary>
      <div class="asst-bar"><i style="width:${total ? (done / total) * 100 : 0}%"></i></div>
      ${plan.goal ? `<p class="asst-goal">${escH(plan.goal)}</p>` : ''}
      <ul>${plan.steps.map(s => `<li class="${s.status}">${s.status === 'done' ? icon('check', 'ok') : s.status === 'doing' ? icon('dot') : icon('circle', 'faint')}<span class="${s.status === 'doing' && live ? 'shimmer' : ''}">${escH(s.title)}</span></li>`).join('')}</ul>
      ${plan.notes ? `<details class="asst-notes"><summary>Working notes</summary><p>${escH(plan.notes)}</p></details>` : ''}`;
    d.addEventListener('toggle', () => { plan._open = d.open; });
    return d;
  }
  function stepRow(s, m) {
    const li = doc.createElement('li'); li.className = 'asst-step ' + s.status + (s.kind === 'think' ? ' think' : '');
    const labels = m._labels || {};
    const label = s.kind === 'think' ? s.summary : s.label || labels[s.name] || s.name.replace(/_/g, ' ');
    const detail = s.kind === 'think' ? s.output : s.output || (s.args && Object.keys(s.args).length ? JSON.stringify(s.args, null, 2) : '');
    li.innerHTML = `<span class="asst-sic">${s.status === 'running' ? '<i class="pulse"></i>' : stepIcon(s)}</span>
      <button type="button" class="asst-srow"${detail ? '' : ' disabled'} aria-expanded="${m._openSteps?.includes(s.id) ? 'true' : 'false'}">
        <span class="asst-slab${s.status === 'running' ? ' shimmer' : ''}">${escH(label)}</span>
        ${s.kind !== 'think' ? `<span class="asst-ssum">${escH(s.summary || '')}</span>` : ''}
        ${s.ms != null && s.ms > 800 ? `<span class="asst-sms mono">${(s.ms / 1000).toFixed(1)}s</span>` : ''}
      </button>`;
    if (s.sources?.length || s.refs?.length) {
      const chips = doc.createElement('div'); chips.className = 'asst-chips';
      (s.refs || []).slice(0, 4).forEach(r => chips.insertAdjacentHTML('beforeend', `<button type="button" class="asst-chip asst-fcite" data-tk="${escH(r.ticker)}" data-accn="${escH(r.accn)}" title="Open ${escH(r.ticker)} ${escH(r.accn)} in the source panel">${icon('file')}<span>${escH(r.ticker)} ${escH(r.label || r.accn)}</span></button>`));
      (s.sources || []).slice(0, 6).forEach(x => chips.insertAdjacentHTML('beforeend', `<a class="asst-chip" href="${escH(x.url)}" target="_blank" rel="noopener noreferrer" title="${escH(x.title || x.url)}"><img src="${escH(favicon(x.url))}" alt="" loading="lazy" width="12" height="12"><span>${escH(hostOf(x.url))}</span></a>`));
      li.append(chips);
    }
    if (detail && m._openSteps?.includes(s.id)) { const pre = doc.createElement('pre'); pre.className = 'asst-raw' + (s.kind === 'think' ? ' think' : ''); pre.textContent = detail; li.append(pre); }
    li.querySelector('.asst-srow').onclick = () => { if (!detail) return; m._openSteps = m._openSteps || []; m._openSteps = m._openSteps.includes(s.id) ? m._openSteps.filter(x => x !== s.id) : [...m._openSteps, s.id]; updateTurn(m); };
    return li;
  }
  function numericChecks(text) {
    if (typeof validateMarkdownNumbers !== 'function') return { verified: 0, contradicted: [], unresolved: 0 };
    try { const c = validateMarkdownNumbers(text); return { verified: c.filter(x => x.status === 'verified').length, contradicted: c.filter(x => x.status === 'contradicted').map(x => x.evidenceId), unresolved: c.filter(x => x.status === 'unverified').length }; }
    catch { return { verified: 0, contradicted: [], unresolved: 0 }; }
  }
  function fillTurn(node, m) {
    node.innerHTML = '';
    if (m.plan?.steps?.length) node.append(planCard(m.plan, !!m.pending));
    const steps = (m.steps || []).filter(s => s.name !== 'sources');
    const sources = window.FU_AGENT ? FU_AGENT.dedupeSources(steps.flatMap(s => s.sources || [])) : [];
    const refs = [...new Map(steps.flatMap(s => s.refs || []).map(r => [r.accn, r])).values()];
    if (steps.length) {
      const tools = steps.filter(s => !['think', 'review', 'note', 'ask'].includes(s.kind)).length;
      const open = m._stepsOpen ?? !!m.pending;
      const t = doc.createElement('div'); t.className = 'asst-steps';
      const toggle = doc.createElement('button'); toggle.type = 'button'; toggle.className = 'asst-stoggle' + (open ? ' open' : ''); toggle.setAttribute('aria-expanded', String(open));
      const parallel = m.pending && m.phase && /parallel/.test(m.phase);
      toggle.innerHTML = `${icon('chev')}<span>${m.pending ? 'Working' : 'Worked'} · ${tools} tool${tools === 1 ? '' : 's'}${sources.length ? ` · ${sources.length} source${sources.length === 1 ? '' : 's'}` : ''}${m.mode === 'deep' ? ' · Deep' : ''}${m.ms >= 1000 ? ` · ${(m.ms / 1000).toFixed(0)}s` : ''}${parallel ? ' · parallel' : ''}</span>`;
      toggle.onclick = () => { m._stepsOpen = !open; updateTurn(m); };
      t.append(toggle);
      if (open || m.pending) {
        const ol = doc.createElement('ol');
        (open ? steps : steps.slice(-3)).forEach(s => ol.append(stepRow(s, m)));
        t.append(ol);
      }
      node.append(t);
    }
    if (m.pending && m.phase) node.insertAdjacentHTML('beforeend', `<p class="asst-phase shimmer">${escH(m.phase)}…</p>`);
    if (m.pending && S.question && S.questionFor === m.id) node.append(questionCard(S.question));
    if (m.content) {
      const checks = !m.pending ? numericChecks(m.content) : null;
      if (checks?.contradicted.length) node.insertAdjacentHTML('beforeend', `<div class="asst-alert">${icon('warn')}<span><b>${checks.contradicted.length} displayed number${checks.contradicted.length === 1 ? '' : 's'} do not match the cited evidence.</b> They are marked below; rely on the source panel values.</span></div>`);
      const md = doc.createElement('div'); md.className = 'asst-md' + (m.pending ? ' draft' : '');
      md.innerHTML = renderMarkdown(m.content);
      if (checks?.contradicted.length) md.querySelectorAll('.cite[data-evidence-id]').forEach(b => { if (checks.contradicted.includes(b.dataset.evidenceId)) { b.classList.add('contradicted'); b.title = 'The number next to this citation does not match the evidence record. ' + b.title; } });
      node.append(md);
      m._checks = checks;
    }
    if (m.error) node.insertAdjacentHTML('beforeend', `<p class="asst-err">${icon('warn')}<span>${escH(m.error)}</span></p>`);
    if (!m.pending && m.content && !(m.stopped && m.content === '_Stopped._')) {
      const f = doc.createElement('div'); f.className = 'asst-mfoot';
      const c = m._checks || {};
      f.innerHTML = `<span class="chip chip-ai">${icon('spark')}AI-generated</span><span class="faint">${escH(new Date(m.at || Date.now()).toLocaleString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }))}${c.verified || c.contradicted?.length ? ` · ${c.verified} cited figure${c.verified === 1 ? '' : 's'} checked${c.contradicted?.length ? `, ${c.contradicted.length} mismatched` : ''}` : ''}${m.stopped ? ' · stopped' : ''} · uncited statements are unverified</span>`;
      const copy = doc.createElement('button'); copy.type = 'button'; copy.className = 'asst-ib sm'; copy.title = 'Copy answer'; copy.setAttribute('aria-label', 'Copy answer'); copy.innerHTML = icon('copy');
      copy.onclick = () => { if (typeof copyText === 'function') copyText(m.content, 'Answer copied'); else root.navigator.clipboard?.writeText(m.content); };
      f.prepend(copy);
      node.append(f);
      if (sources.length || refs.length) {
        const srow = doc.createElement('div'); srow.className = 'asst-srcs';
        refs.slice(0, 4).forEach(r => srow.insertAdjacentHTML('beforeend', `<button type="button" class="asst-chip asst-fcite" data-tk="${escH(r.ticker)}" data-accn="${escH(r.accn)}">${icon('file')}<span>${escH(r.ticker)} ${escH(r.label || r.accn)}</span></button>`));
        sources.slice(0, 5).forEach(x => srow.insertAdjacentHTML('beforeend', `<a class="asst-fav" href="${escH(x.url)}" target="_blank" rel="noopener noreferrer" title="${escH(x.title || x.url)}"><img src="${escH(favicon(x.url))}" alt="${escH(hostOf(x.url))}" loading="lazy" width="16" height="16"></a>`));
        if (sources.length) srow.insertAdjacentHTML('beforeend', `<details class="asst-all"><summary>All ${sources.length} source${sources.length === 1 ? '' : 's'}</summary><ol>${sources.map(x => `<li><a href="${escH(x.url)}" target="_blank" rel="noopener noreferrer">${escH(x.title || hostOf(x.url))}</a> <span class="faint">${escH(hostOf(x.url))}</span></li>`).join('')}</ol></details>`);
        node.append(srow);
      }
    }
  }
  function questionCard(q) {
    const card = doc.createElement('div'); card.className = 'asst-ask'; card.setAttribute('role', 'group'); card.setAttribute('aria-label', 'Needs your input');
    card.innerHTML = `<p class="eyebrow">${icon('question')} Needs your input</p><p class="asst-q">${escH(q.question)}</p>`;
    const opts = doc.createElement('div'); opts.className = 'asst-opts';
    q.options.forEach(o => { const b = doc.createElement('button'); b.type = 'button'; b.textContent = o; b.onclick = () => answer(o); opts.append(b); });
    card.append(opts);
    card.insertAdjacentHTML('beforeend', '<p class="faint asst-or">Or type your own answer below.</p>');
    return card;
  }
  function onBodyClick(e) {
    const f = e.target.closest('.asst-fcite');
    if (f) { e.preventDefault(); openFilingQuote(f.dataset.tk, f.dataset.accn, f.dataset.q || ''); return; }
    const a = e.target.closest('a.asst-in');
    if (a && mobile()) setOpen(false, { focus: false });
  }

  /* ---------------- conversation ---------------- */
  function newSession() { return { id: uid(), title: 'New research', created: Date.now(), updated: Date.now(), messages: [] }; }
  function persist() { if (S.session?.messages.length) { S.session.updated = Date.now(); History.save(S.session); write(CUR_KEY, S.session.id); } }
  function newChat() {
    if (S.busy) { toastMsg('Stop the current answer before starting a new chat'); return; }
    S.session = newSession(); write(CUR_KEY, null); renderAll(); P.ta.value = ''; fit(); P.ta.focus();
  }
  function openSession(id) {
    if (S.busy) { toastMsg('Stop the current answer before opening another conversation'); return false; }
    const s = History.get(id); if (!s) { toastMsg('That conversation is no longer saved in this browser'); return false; }
    s.messages.forEach(m => { if (m.pending) { m.pending = false; m.error = m.error || 'Interrupted before it finished.'; } });
    S.session = s; write(CUR_KEY, id);
    if (!S.open) setOpen(true); else renderAll();
    return true;
  }
  function prefill(text) {
    if (!P) return;
    if (!S.open) setOpen(true, { focus: false });
    P.ta.value = String(text).slice(0, 4000); fit();
    if (!P.ta.disabled) { P.ta.focus({ preventScroll: true }); P.ta.setSelectionRange(0, 0); P.ta.scrollTop = 0; }
  }
  function answer(text) {
    const t = String(text || '').trim();
    if (!t || !S.answer) return;
    const resolve = S.answer; S.answer = null; S.question = null; S.questionFor = null;
    P.ta.value = ''; fit();
    resolve(t); syncChrome();
  }
  function halt() { S.stop = true; S.ctl?.abort(); if (S.answer) answer('(user stopped)'); }

  function submit() {
    if (S.question) return answer(P.ta.value);
    if (S.busy) return halt();
    ask(P.ta.value);
  }
  async function ask(text) {
    const q = String(text || '').trim();
    if (!q || S.busy || !root.FU_AGENT || !root.FU_ASSISTANT_TOOLKIT) return;
    if (!S.session) S.session = newSession();
    const sess = S.session;
    const history = sess.messages.filter(m => !m.pending);
    const user = { id: 'u' + uid(), role: 'user', content: q, at: Date.now() };
    const turnMsg = { id: 'a' + uid(), role: 'assistant', content: '', steps: [], pending: true, phase: 'Thinking', mode: S.deep ? 'deep' : 'fast', at: Date.now(), route: root.location.hash || '#markets' };
    if (!history.length) sess.title = q.replace(/\s+/g, ' ').slice(0, 72);
    sess.messages.push(user, turnMsg);
    S.busy = true; S.stop = false; S.ctl = new AbortController(); S.scrollPinned = true;
    P.ta.value = ''; fit();
    persist(); renderAll(); syncChrome();
    const tools = FU_ASSISTANT_TOOLKIT.toolsFor(routeId());
    turnMsg._labels = Object.fromEntries(tools.map(t => [t.name, t.label]));
    const messages = [
      { role: 'system', content: systemPrompt() },
      ...history.slice(-14).map(m => ({ role: m.role, content: (m.content || '(no answer)') + (m.role === 'assistant' && m.plan?.notes ? `\n\n[Earlier working notes: ${m.plan.notes.slice(0, 1200)}]` : '') })),
      { role: 'user', content: `[Context: the user is viewing ${root.location.hash || '#markets'} ("${doc.title}"); mode: ${S.deep ? 'deep research' : 'fast'}; local time ${new Date().toLocaleString()}. Call get_page_context if the question refers to the page.]\n${q}` },
    ];
    const turn = await FU_AGENT.runAgent({
      messages, tools, deep: S.deep, signal: S.ctl.signal, stopped: () => S.stop,
      onUpdate: t => { Object.assign(turnMsg, { content: t.content, steps: t.steps, plan: t.plan ? { ...t.plan, _open: turnMsg.plan?._open } : turnMsg.plan, phase: t.phase, error: t.error }); if (S.session === sess && S.open) updateTurn(turnMsg); },
      onAsk: qq => new Promise(resolve => { S.question = qq; S.questionFor = turnMsg.id; S.answer = resolve; syncChrome(); if (S.session === sess) updateTurn(turnMsg); setTimeout(() => P.ta.focus({ preventScroll: true }), 40); }),
    });
    Object.assign(turnMsg, { content: turn.content, steps: turn.steps, plan: turn.plan || null, error: turn.error, phase: null, pending: false, stopped: !!turn.stopped, ms: turn.ms, reviewed: turn.reviewed, stats: turn.stats, at: Date.now() });
    S.busy = false; S.ctl = null; S.question = null; S.answer = null;
    persist();
    if (S.session === sess) updateTurn(turnMsg);
    syncChrome();
    root.dispatchEvent(new root.CustomEvent('fu:assistant-turn', { detail: { sessionId: sess.id, status: turn.error ? 'error' : turn.stopped ? 'stopped' : 'done' } }));
    if (routeId() === 'research' && root.FU_PAGES?.research) renderHistoryList();
  }

  /* ---------------- #research: history and browse view ---------------- */
  let historyHost = null;
  function renderHistoryList() {
    if (!historyHost?.isConnected) return;
    const q = (historyHost.querySelector('.asst-hsearch')?.value || '').toLowerCase();
    const list = historyHost.querySelector('.asst-hlist'); list.innerHTML = '';
    const all = History.all().filter(s => !q || s.title.toLowerCase().includes(q) || s.messages.some(m => String(m.content).toLowerCase().includes(q)));
    if (!all.length) { list.innerHTML = `<div class="asst-hempty">${History.all().length ? 'No conversations match.' : 'Your research conversations are saved here, in this browser. Open the assistant from any page with ⌘J or the Assistant button.'}</div>`; return; }
    all.forEach(s => {
      const lastA = [...s.messages].reverse().find(m => m.role === 'assistant' && m.content);
      const preview = String(lastA?.content || '').replace(/```[\s\S]*?```/g, ' ').replace(/\[\[[^\]]+\]\]/g, '').replace(/\[([^\]]+)\]\([^)]*\)/g, '$1').replace(/[#*_`>|-]/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 180);
      const tools = s.messages.reduce((n, m) => n + (m.steps || []).filter(x => !['think', 'review', 'note', 'ask'].includes(x.kind)).length, 0);
      const row = doc.createElement('div'); row.className = 'asst-hrow' + (S.session?.id === s.id ? ' on' : '');
      row.innerHTML = `<button type="button" class="asst-hopen"><b>${escH(s.title)}</b><span class="muted">${escH(preview || 'No answer yet')}</span><span class="faint mono">${escH(rel(s.updated))} · ${s.messages.filter(m => m.role === 'user').length} question${s.messages.filter(m => m.role === 'user').length === 1 ? '' : 's'} · ${tools} tool calls${s.messages.some(m => m.mode === 'deep') ? ' · Deep' : ''}</span></button><button type="button" class="btn asst-hdel" aria-label="Delete conversation">Delete</button>`;
      row.querySelector('.asst-hopen').onclick = () => { if (openSession(s.id)) renderHistoryList(); };
      const del = row.querySelector('.asst-hdel');
      del.onclick = () => { if (del.dataset.confirm) { History.remove(s.id); if (S.session?.id === s.id && !S.busy) { S.session = newSession(); renderAll(); } renderHistoryList(); } else { del.dataset.confirm = '1'; del.textContent = 'Confirm delete'; del.classList.add('danger'); setTimeout(() => { if (del.isConnected) { delete del.dataset.confirm; del.textContent = 'Delete'; del.classList.remove('danger'); } }, 3500); } };
      list.append(row);
    });
  }
  async function researchPage(arg) {
    if (arg === 'classic' && typeof pResearch === 'function') return pResearch();
    const page = doc.querySelector('#page');
    page.insertAdjacentHTML('beforeend', `<div class="ph"><div><div class="eyebrow">Research</div><h1>Research agent</h1><div class="sub">An AI analyst that reads SEC filings, statements, prices, insider trades, macro series and the web, plans its work, and cites every source. It opens from any page with the Assistant button or ⌘J; conversations are kept in this browser.</div></div></div>`);
    historyHost = doc.createElement('section'); historyHost.className = 'panel asst-hist';
    historyHost.innerHTML = `<div class="hd"><h3>History</h3><div class="r"><input class="asst-hsearch" type="search" placeholder="Search conversations" aria-label="Search conversations"><button type="button" class="btn pri asst-hnew">New research</button></div></div><div class="asst-hlist"></div>`;
    const side = doc.createElement('section'); side.className = 'panel asst-hside';
    side.innerHTML = `<div class="hd"><h3>Start an investigation</h3></div><div class="bd"></div><div class="bd asst-hfoot"><p class="faint">Answers are AI-generated and dated. Financial figures cite evidence records that open the filing in the source panel; uncited statements are unverified. <a href="#research-classic">Classic research chat</a></p></div>`;
    const sb = side.querySelector('.bd');
    ['How fast is NVIDIA\'s Data Center business growing, and what did management say drives it?', 'Compare capex intensity at Microsoft, Amazon and Alphabet over the last 8 quarters', 'What are Tesla\'s biggest risk factors in its latest 10-Q, and how have insiders traded?', 'How do today\'s yield curve and inflation compare with a year ago?'].forEach(s => { const b = doc.createElement('button'); b.type = 'button'; b.className = 'asst-hsugg'; b.textContent = s; b.onclick = () => { if (S.busy) { toastMsg('An answer is still running in the assistant'); setOpen(true); return; } S.session = S.session?.messages.length ? newSession() : S.session || newSession(); renderAll(); prefill(s); }; sb.append(b); });
    const grid = doc.createElement('div'); grid.className = 'asst-hgrid'; grid.append(historyHost, side); page.append(grid);
    historyHost.querySelector('.asst-hsearch').addEventListener('input', renderHistoryList);
    historyHost.querySelector('.asst-hnew').onclick = () => { setOpen(true); newChat(); renderHistoryList(); };
    renderHistoryList();
    if (arg && History.get(arg)) openSession(arg);
  }

  /* ---------------- public API and wiring ---------------- */
  function init() {
    if (P || !doc?.body) return;
    loadUI(); build();
    const cur = read(CUR_KEY, null);
    S.session = (cur && History.get(cur)) || newSession();
    S.session.messages.forEach(m => { if (m.pending) { m.pending = false; m.error = m.error || 'Interrupted before it finished.'; } });
    syncChrome();
    if (S.open) renderAll();
    const btn = doc.querySelector('#asst-toggle');
    if (btn) btn.addEventListener('click', e => { e.preventDefault(); setOpen(!S.open); });
    doc.addEventListener('keydown', e => {
      if ((e.metaKey || e.ctrlKey) && !e.altKey && !e.shiftKey && e.key.toLowerCase() === 'j') { e.preventDefault(); setOpen(!S.open); return; }
      if (e.key === 'Escape' && S.open && P.aside.contains(doc.activeElement) && !S.busy && mobile()) setOpen(false);
    });
    root.addEventListener('fu:research-request', e => { if (typeof e.detail === 'string' && e.detail.trim()) prefill(e.detail); });
    root.addEventListener('hashchange', () => { syncChrome(); if (!S.busy && S.open && !S.session?.messages.length) renderAll(); });
    let rt; root.addEventListener('resize', () => { clearTimeout(rt); rt = setTimeout(syncChrome, 120); });
    if (typeof SHORTCUTS !== 'undefined' && Array.isArray(SHORTCUTS) && !SHORTCUTS.some(s => s[1] === 'Open or close the assistant')) SHORTCUTS.splice(1, 0, ['⌘ J', 'Open or close the assistant']);
  }
  root.FU_PAGES = root.FU_PAGES || {};
  root.FU_PAGES.research = researchPage;
  root.FU_ASSISTANT = Object.freeze({
    open: () => setOpen(true), close: () => setOpen(false), toggle: () => setOpen(!S.open), prefill, ask: q => prefill(q),
    newChat, openSession, setDocked, state: () => ({ open: S.open, docked: S.docked, deep: S.deep, busy: S.busy, sessionId: S.session?.id || null, width: S.width, box: S.box, question: S.question }),
    history: History, registerTool: def => root.FU_ASSISTANT_TOOLKIT?.registerTool(def),
    registerSuggestions: (pageId, list) => { if (typeof pageId === 'string' && Array.isArray(list)) EXTRA_SUGGESTIONS[pageId] = list.slice(0, 6); },
    renderMarkdown, parseChart, findQuote, openFilingQuote, systemPrompt, suggestionsFor,
  });
  if (doc) { if (doc.readyState === 'loading') doc.addEventListener('DOMContentLoaded', init, { once: true }); else init(); }
})(typeof window !== 'undefined' ? window : globalThis);
