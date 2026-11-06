/* Assistant tools: the reference tool set mapped onto Filed Under's data bundles and API routes.
   Executors run in the browser and read app.js globals at call time (ensureCompany, model, evidenceForCell,
   ensurePrices, priceStats, loadFilingDoc, ensureSegments, ...). Every financial value carries an evidence ID
   so answers can cite [[E:ev_…]] and open the source drawer.

   Pluggable tools (contract, also in docs/assistant.md):
     window.FU_ASSISTANT_TOOLS = window.FU_ASSISTANT_TOOLS || [];
     window.FU_ASSISTANT_TOOLS.push({
       name: "get_supply_chain",            // /^[a-z][a-z0-9_]{1,63}$/, must not collide with a built-in
       label: "Mapped supply chain",         // past-tense step label shown in the timeline
       kind: "tool",                          // "tool" | "web" | "filing" | "code" (icon only)
       description: "…",                      // what it returns and when to use it (sent to the model)
       parameters: { type: "object", properties: {…}, required: […] },   // JSON schema; arguments are validated
       pages: ["company", "supply"],          // optional: only offered when the current route id is listed
       run: async (args, { signal }) => ({ output: "text for the model", summary: "short line",
                                            sources: [{ url, title }], refs: [{ ticker, accn, label }] })
     });
   Registration can happen before or after this file loads; the panel reads the registry at the start of each turn. */
(function (root) {
  'use strict';
  const has = name => { try { return typeof root.eval(name) !== 'undefined'; } catch { return false; } };
  const G = name => { try { return root.eval(name); } catch { return undefined; } };
  const MAX = 12000;
  const clip = (s, n = MAX) => (s.length > n ? s.slice(0, n) + '\n…[truncated; request a narrower slice]' : s);
  const hostOf = u => { try { return new URL(u).hostname.replace(/^www\./, ''); } catch { return String(u || ''); } };
  const obj = (properties, required = []) => ({ type: 'object', properties, required });
  const str = (description, extra = {}) => ({ type: 'string', description, ...extra });
  const int = (description, minimum, maximum) => ({ type: 'integer', description, minimum, maximum });
  const TICKER = /^[A-Z][A-Z0-9.-]{0,9}$/;
  const ACCN = /^\d{10}-\d{2}-\d{6}$/;

  function covered(t) {
    const tk = String(t || '').trim().toUpperCase();
    if (!TICKER.test(tk)) throw new Error(`"${t}" is not a valid ticker`);
    const ok = G('companySummary')?.(tk);
    if (!ok) throw new Error(`${tk} is not in Filed Under coverage. Use get_coverage to find covered tickers, or web_research for other companies.`);
    return tk;
  }
  async function company(t, signal) { const tk = covered(t); await G('ensureCompany')(tk, { signal }); return tk; }
  const raw = v => (v == null || !Number.isFinite(v) ? 'n/a' : Number.isInteger(v) ? String(v) : String(+v.toPrecision(10)));
  function pretty(v, unit) {
    if (v == null || !Number.isFinite(v)) return 'n/a';
    if (unit === 'pct') return (v * 100).toFixed(2) + '%';
    if (unit === 'x') return v.toFixed(2) + 'x';
    if (unit === 'USD/shares') return '$' + v.toFixed(2);
    if (unit === 'shares') return (v / 1e6).toFixed(1) + 'M shares';
    const a = Math.abs(v), s = v < 0 ? '-' : '';
    return a >= 1e12 ? `${s}$${(a / 1e12).toFixed(3)}T` : a >= 1e9 ? `${s}$${(a / 1e9).toFixed(3)}B` : a >= 1e6 ? `${s}$${(a / 1e6).toFixed(2)}M` : `${s}$${a.toLocaleString('en-US')}`;
  }
  function edgarFolder(tk, accn) {
    const cik = G('D')?.companies?.[tk]?.cik || G('IDX')?.[tk]?.cik;
    return cik && ACCN.test(accn) ? `https://www.sec.gov/Archives/edgar/data/${Number(cik)}/${accn.replace(/-/g, '')}/` : `https://www.sec.gov/edgar/search/#/q=${encodeURIComponent(accn)}`;
  }
  async function post(path, body, signal) {
    const r = await root.fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) { const e = new Error(d.error || `${path} returned ${r.status}`); e.status = r.status; throw e; }
    return d;
  }
  function passages(text, query, before = 500, after = 1100, max = 6) {
    const q = String(query || '').trim().toLowerCase(), lower = text.toLowerCase(), out = [];
    const terms = q ? [q, ...q.split(/\s+/).filter(w => w.length > 4)] : [];
    for (const term of terms) {
      let from = 0;
      while (out.length < max) { const i = lower.indexOf(term, from); if (i < 0) break; out.push(text.slice(Math.max(0, i - before), i + after)); from = i + before + after; }
      if (out.length) break;
    }
    return out;
  }
  function htmlToText(html) {
    const doc = new root.DOMParser().parseFromString(html, 'text/html');
    doc.querySelectorAll('script, style, [style*="display:none"], [style*="display: none"]').forEach(e => e.remove());
    doc.querySelectorAll('ix\\:header').forEach(e => e.remove());
    doc.querySelectorAll('tr').forEach(tr => tr.append(doc.createTextNode('\n')));
    doc.querySelectorAll('td, th').forEach(td => td.append(doc.createTextNode(' | ')));
    doc.querySelectorAll('p, div, br, h1, h2, h3, h4, li').forEach(e => e.append(doc.createTextNode('\n')));
    return (doc.body?.textContent || '').replace(/ /g, ' ').replace(/[ \t]+/g, ' ').replace(/( \| )+\n/g, '\n').replace(/\n\s*\n+/g, '\n').trim();
  }

  const SCREEN_FIELDS = ['market_cap', 'price', 'chg_1d', 'ret_1m', 'ret_ytd', 'ret_1y', 'pe_ttm', 'ps_ttm', 'eps_ttm', 'revenue_ttm', 'net_income_ttm', 'fcf_ttm', 'fcf_yield', 'gross_margin_lq', 'op_margin_lq', 'net_margin_ttm', 'rev_growth_lq', 'dividend_yield', 'buyback_yield', 'beta_1y', 'vol_60d', 'insider_net_90d'];
  const PCT_FIELDS = /margin|growth|yield|ret_|chg_|vol_/;
  const fmtField = (k, v) => v == null || !Number.isFinite(v) ? 'n/a' : PCT_FIELDS.test(k) ? (v * 100).toFixed(2) + '%' : /_ttm$|market_cap|insider/.test(k) && !/^p[es]_/.test(k) ? pretty(v, 'USD') : +v.toFixed(3) + '';
  function universe() { return (G('INDEX')?.companies || []); }

  function statementRows(tk, statement) {
    const m = G('model')(tk);
    if (statement === 'ratios') return m.ratioDefs.map(d => ({ label: d.label, unit: d.unit, ratio: true }));
    return (m.rows[statement] || []).map(r => ({ label: r.label, unit: r.unit }));
  }
  function seriesLines(tk, labels, kind, n) {
    const m = G('model')(tk), plabel = G('plabel'), evidenceForCell = G('evidenceForCell');
    const ends = m.periods(kind).slice(-n), lines = [], accns = new Set();
    for (const { label, unit } of labels) {
      const cells = ends.map(e => [e, m.metric(label, kind, e)]).filter(([, c]) => c && c.v != null);
      if (!cells.length) continue;
      const parts = cells.map(([e, c]) => {
        const ev = evidenceForCell(c);
        if (c.rec?.accn) accns.add(c.rec.accn);
        let period = e; try { period = plabel(tk, e, kind); } catch { /* keep end date */ }
        return `${period} (${c.start ? c.start + '→' : ''}${e}) = ${raw(ev.value)} ${ev.unit || unit} ≈ ${pretty(ev.value, ev.unit || unit)}${c.derived ? ' [derived from cumulative]' : ''}${c.calc ? ' [calculated: ' + c.calc.formula + ']' : ''} [${ev.id}]`;
      });
      lines.push(`${label} (${unit}):\n  ${parts.join('\n  ')}`);
    }
    return { lines, accns: [...accns] };
  }

  /* -------- built-in tools -------- */
  function builtins() {
    const T = [];
    const add = t => T.push(t);

    add({ name: 'get_coverage', label: 'Checked coverage', description: 'List covered companies (SEC data on file) with sector, market cap, TTM revenue, latest quarter and latest filing date. Filter by a name/ticker query, sector substring or index (sp500, sp100, all).', parameters: obj({ query: str('Name or ticker substring'), sector: str('Sector substring'), index: { type: 'string', enum: ['all', 'sp500', 'sp100'] }, limit: int('Max rows, 1-120', 1, 120) }),
      run: async a => {
        let rows = universe();
        if (a.index && a.index !== 'all') rows = rows.filter(c => c[a.index]);
        if (a.sector) rows = rows.filter(c => String(c.sector || '').toLowerCase().includes(a.sector.toLowerCase()));
        if (a.query) { const q = a.query.toLowerCase(); rows = rows.filter(c => c.ticker.toLowerCase() === q || String(c.name || '').toLowerCase().includes(q) || String(c.legal_name || '').toLowerCase().includes(q) || c.ticker.toLowerCase().startsWith(q)); }
        const n = a.limit || 40;
        return { output: `${rows.length} covered companies match (of ${universe().length}). Data from SEC filings and exchange closes; index.json generated ${G('INDEX')?.generated || 'n/a'}.\n` + rows.slice(0, n).map(c => `${c.ticker} | ${c.name} | ${c.sector || ''} | mkt cap ${fmtField('market_cap', c.market_cap)} | revenue TTM ${fmtField('revenue_ttm', c.revenue_ttm)} | latest quarter ${c.latest_quarter || 'n/a'} | latest filing ${c.latest_filing || 'n/a'}`).join('\n'), summary: `${rows.length} covered companies` };
      } });

    add({ name: 'screen_companies', label: 'Screened companies', description: 'Screen all covered companies on stored metrics. Fields: ' + SCREEN_FIELDS.join(', ') + '. Margins, growth, yields and returns are fractions (0.25 = 25%). Values are from the screener index (TTM from SEC statements, prices from the latest close); open get_financials for evidence-linked figures before citing exact numbers.',
      parameters: obj({ filters: { type: 'array', maxItems: 12, items: obj({ metric: { type: 'string', enum: SCREEN_FIELDS }, op: { type: 'string', enum: ['>', '>=', '<', '<=', '='] }, value: { type: 'number' } }, ['metric', 'op', 'value']) }, index: { type: 'string', enum: ['all', 'sp500', 'sp100'] }, sector: str('Sector substring'), sort_by: { type: 'string', enum: SCREEN_FIELDS }, descending: { type: 'boolean' }, limit: int('1-100', 1, 100), columns: { type: 'array', maxItems: 8, items: { type: 'string', enum: SCREEN_FIELDS } } }, ['sort_by']),
      run: async a => {
        let rows = universe();
        if (a.index && a.index !== 'all') rows = rows.filter(c => c[a.index]);
        if (a.sector) rows = rows.filter(c => String(c.sector || '').toLowerCase().includes(a.sector.toLowerCase()));
        for (const f of a.filters || []) rows = rows.filter(c => { const v = c[f.metric]; if (typeof v !== 'number' || !Number.isFinite(v)) return false; return f.op === '>' ? v > f.value : f.op === '>=' ? v >= f.value : f.op === '<' ? v < f.value : f.op === '<=' ? v <= f.value : v === f.value; });
        const sk = a.sort_by, dir = a.descending === false ? 1 : -1;
        rows = rows.filter(c => Number.isFinite(c[sk])).sort((x, y) => dir * (x[sk] - y[sk]));
        const cols = [...new Set([sk, ...(a.columns || [])])].slice(0, 8), n = a.limit || 15;
        return { output: `${rows.length} companies match${(a.filters || []).length ? ' ' + a.filters.map(f => `${f.metric} ${f.op} ${f.value}`).join(' and ') : ''}. Source: Filed Under screener index (generated ${G('INDEX')?.generated || 'n/a'}).\n` + rows.slice(0, n).map(c => `${c.ticker} ${c.name} | ${c.sector || ''} | ${cols.map(k => `${k} ${fmtField(k, c[k])} (raw ${raw(c[k])})`).join(' | ')}`).join('\n'), summary: `${rows.length} matches` };
      } });

    add({ name: 'get_financials', kind: 'filing', label: 'Pulled financials', description: 'Financial statement rows from SEC XBRL filings for one covered company, newest periods last. Every value carries an evidence id [ev_…]; cite it as [[E:ev_…]] right after the number. statement: income, balance, cashflow or ratios (margins, growth, FCF, ROE…). metrics optionally limits rows by label (e.g. ["Revenue","Gross margin"]). Quarterly fourth quarters may be derived from annual minus nine months.',
      parameters: obj({ ticker: str('Covered ticker, e.g. NVDA'), statement: { type: 'string', enum: ['income', 'balance', 'cashflow', 'ratios'] }, frequency: { type: 'string', enum: ['annual', 'quarterly'] }, periods: int('Most recent periods, 1-16', 1, 16), metrics: { type: 'array', maxItems: 20, items: { type: 'string' } } }, ['ticker']),
      run: async (a, { signal }) => {
        const tk = await company(a.ticker, signal), kind = a.frequency === 'annual' ? 'A' : 'Q', n = a.periods || 8;
        let labels;
        const m = G('model')(tk);
        if (a.metrics?.length) {
          const all = [...Object.keys(m.byLabel).map(l => ({ label: l, unit: m.byLabel[l].unit })), ...m.ratioDefs.map(d => ({ label: d.label, unit: d.unit }))];
          labels = a.metrics.map(x => all.find(l => l.label.toLowerCase() === String(x).toLowerCase())).filter(Boolean);
          if (!labels.length) throw new Error(`No matching metrics for ${tk}. Available: ${all.map(l => l.label).join(', ')}`);
        } else labels = statementRows(tk, a.statement || 'income');
        const { lines, accns } = seriesLines(tk, labels, kind, n);
        const filings = (G('D').companies[tk].filings || []).filter(f => accns.includes(f.a)).slice(0, 12).map(f => `${f.a} = ${f.f} for period ${f.r || 'n/a'} filed ${f.d}`);
        return { output: clip(`${tk} ${a.statement || (a.metrics?.length ? 'selected metrics' : 'income')} (${kind === 'A' ? 'annual' : 'quarterly'}), ${lines.length} rows. Values are exact as filed (USD unless noted).\n${lines.join('\n') || 'No values on file for these rows.'}\nSource filings (cite text from these as filing:${tk}:ACCESSION):\n${filings.join('\n')}`), summary: `${tk} ${a.statement || 'metrics'}, ${lines.length} rows × ${n} ${kind === 'A' ? 'years' : 'quarters'}`,
          refs: accns.slice(-3).map(accn => ({ ticker: tk, accn, label: (G('D').companies[tk].filings || []).find(f => f.a === accn)?.f || 'Filing' })) };
      } });

    add({ name: 'get_segments', kind: 'filing', label: 'Pulled segments', description: 'Segment, product and geographic revenue (and segment operating income) from inline-XBRL dimensional facts, with evidence ids for each value.', parameters: obj({ ticker: str('Covered ticker'), frequency: { type: 'string', enum: ['annual', 'quarterly'] }, periods: int('1-12', 1, 12), metric: str('Optional metric family filter, e.g. Revenue or Operating income') }, ['ticker']),
      run: async (a, { signal }) => {
        const tk = await company(a.ticker, signal), K = a.frequency === 'annual' ? 'A' : 'Q', n = a.periods || 6;
        const { seg } = await G('ensureSegments')(tk);
        const tables = (seg?.tables || []).filter(t => t.rows.length && (!a.metric || String(t.metric).toLowerCase().includes(a.metric.toLowerCase())));
        if (!tables.length) return { output: `No segment tables on file for ${tk}.`, summary: 'no segments' };
        const out = [], segCell = G('segCell'), evidenceForCell = G('evidenceForCell');
        for (const t of tables.slice(0, 6)) {
          const ends = G('segPeriods')(t, K, n);
          out.push(`## ${t.category_label} · ${t.metric} (${t.concept})`);
          for (const r of t.rows.filter(r => !r.memo).slice(0, 14)) {
            const vals = ends.map(e => r[K]?.[e] ? (() => { const c = segCell(tk, t, r, K, r[K][e], r[K]), ev = evidenceForCell(c); return `${r[K][e].p || e} = ${raw(ev.value)} ≈ ${pretty(ev.value, 'USD')} [${ev.id}]`; })() : null).filter(Boolean);
            if (vals.length) out.push(`${'  '.repeat(r.depth || 0)}${r.label}: ${vals.join(' | ')}`);
          }
        }
        return { output: clip(out.join('\n')), summary: `${tk} ${K === 'A' ? 'annual' : 'quarterly'} segments, ${tables.length} tables` };
      } });

    add({ name: 'list_filings', kind: 'filing', label: 'Listed filings', description: 'Recent SEC filings for a covered company with accession numbers, forms, periods and filing dates. Use the accession with read_filing.', parameters: obj({ ticker: str('Covered ticker'), forms: { type: 'array', maxItems: 8, items: { type: 'string' } }, limit: int('1-40', 1, 40) }, ['ticker']),
      run: async (a, { signal }) => {
        const tk = await company(a.ticker, signal), forms = (a.forms || []).map(f => f.toUpperCase());
        const list = (G('D').companies[tk].filings || []).filter(f => !forms.length || forms.includes(String(f.f).toUpperCase())).slice(0, a.limit || 15);
        return { output: list.map(f => `${f.a} | ${f.f}${f.i ? ' items ' + f.i : ''} | period ${f.r || 'n/a'} | filed ${f.d} | ${f.x || ''}`).join('\n') || 'No filings match.', summary: `${list.length} ${tk} filings`, sources: list.slice(0, 3).map(f => ({ url: edgarFolder(tk, f.a), title: `${tk} ${f.f} filed ${f.d}` })) };
      } });

    add({ name: 'read_filing', kind: 'filing', label: 'Read filing', description: 'Read passages from an SEC filing document by searching for a phrase (e.g. "risk factors", "Data Center", "gross margin"). Returns plain text around each match. Cite statements as [label](filing:TICKER:ACCESSION "4-10 word verbatim phrase") so the source drawer highlights the quote.', parameters: obj({ ticker: str('Covered ticker of the filer'), accession: str('Accession number, e.g. 0001045810-26-000123'), query: str('Phrase to search for inside the document') }, ['ticker', 'accession', 'query']),
      run: async (a, { signal }) => {
        const tk = await company(a.ticker, signal);
        if (!ACCN.test(a.accession)) throw new Error('accession must look like 0000000000-00-000000; call list_filings first');
        const filing = (G('D').companies[tk].filings || []).find(f => f.a === a.accession);
        const text = htmlToText(await G('loadFilingDoc')(a.accession));
        const hits = passages(text, a.query);
        return { output: clip(`${filing?.f || 'Filing'} ${a.accession}, filed ${filing?.d || 'n/a'}, ${text.length.toLocaleString('en-US')} characters. Cite as filing:${tk}:${a.accession}\n\n${hits.length ? hits.map((w, i) => `--- passage ${i + 1} ---\n${w}`).join('\n\n') : `No match for "${a.query}". Opening text:\n${text.slice(0, 4000)}`}`),
          summary: `${tk} ${filing?.f || ''} "${a.query}" (${hits.length} passages)`, refs: [{ ticker: tk, accn: a.accession, label: `${filing?.f || 'Filing'} ${filing?.d || ''}`.trim() }], sources: [{ url: edgarFolder(tk, a.accession), title: `${tk} ${filing?.f || 'filing'} ${a.accession}` }] };
      } });

    add({ name: 'search_filings', kind: 'filing', label: 'Searched EDGAR', description: 'SEC EDGAR full-text search across filings since 2001. Quote exact phrases. Optionally restrict to one covered ticker, form types and a date range.', parameters: obj({ query: str('Search phrase; quote exact phrases', { maxLength: 300 }), ticker: str('Optional covered ticker'), forms: { type: 'array', maxItems: 8, items: { type: 'string' } }, start_date: str('YYYY-MM-DD'), end_date: str('YYYY-MM-DD') }, ['query']),
      run: async (a, { signal }) => {
        let ciks;
        if (a.ticker) { const tk = covered(a.ticker), cik = G('D')?.companies?.[tk]?.cik || G('IDX')?.[tk]?.cik || G('FU_COMPANIES')?.get?.(tk)?.cik; if (cik) ciks = [String(cik)]; }
        const r = await post('/api/search', { source: 'edgar', query: a.query, ciks, forms: a.forms, startdt: a.start_date, enddt: a.end_date }, signal);
        return { output: clip(`${r.total} documents match on SEC EDGAR full-text search (${r.searchUrl}).\n` + r.hits.map(h => `${h.company} | ${h.form} | filed ${h.filed} | period ${h.period || 'n/a'} | accession ${h.accession} | doc ${h.document} | ${h.url}`).join('\n')), summary: `"${a.query}": ${Number(r.total).toLocaleString('en-US')} hits`, sources: r.hits.slice(0, 6).filter(h => h.url).map(h => ({ url: h.url, title: `${h.company} ${h.form} ${h.filed}` })) };
      } });

    add({ name: 'get_price_history', label: 'Analyzed prices', description: 'Daily price history for a covered ticker or market symbol (SPY, QQQ, IWM, DIA, SMH, sector SPDRs, ^VIX, ^TNX, DX=F, CL=F, GC=F, HG=F, BTC-USD): returns, volatility, drawdown, moving averages, 52-week range and month-start closes. About two years of daily bars.', parameters: obj({ symbol: str('Symbol'), range: { type: 'string', enum: ['3mo', '6mo', '1y', '2y'] } }, ['symbol']),
      run: async (a, { signal }) => {
        const s = String(a.symbol).toUpperCase(), px = await G('ensurePrices')(s, { signal });
        const days = { '3mo': 63, '6mo': 126, '1y': 252, '2y': 520 }[a.range || '1y'];
        const bars = (px?.bars || []).slice(-days);
        if (bars.length < 5) throw new Error(`No price history for ${s}`);
        const c = bars.map(b => b.c), d = b => b.t.toISOString().slice(0, 10);
        const ret = n => c.length > n ? c.at(-1) / c.at(-1 - n) - 1 : null, pct = x => x == null ? 'n/a' : (x * 100).toFixed(2) + '%';
        const r = c.slice(1).map((x, i) => x / c[i] - 1), mean = r.reduce((x, y) => x + y, 0) / r.length, vol = Math.sqrt(r.reduce((x, y) => x + (y - mean) ** 2, 0) / (r.length - 1) * 252);
        let pk = c[0], dd = 0; for (const x of c) { pk = Math.max(pk, x); dd = Math.min(dd, x / pk - 1); }
        const sma = n => c.length >= n ? c.slice(-n).reduce((x, y) => x + y, 0) / n : null;
        const monthly = []; let lm = '';
        for (const b of bars) { const m = d(b).slice(0, 7); if (m !== lm) { monthly.push(`${d(b)}: ${b.c.toFixed(2)}`); lm = m; } }
        return { output: clip(`${s} daily closes ${d(bars[0])} → ${d(bars.at(-1))} (Filed Under price store; close prices, may be delayed). Last close ${c.at(-1).toFixed(2)}.\nReturns: 1M ${pct(ret(21))}, 3M ${pct(ret(63))}, 6M ${pct(ret(126))}, period ${pct(c.at(-1) / c[0] - 1)}.\nAnnualized volatility ${pct(vol)}, max drawdown ${pct(dd)}. 50-day SMA ${sma(50)?.toFixed(2) ?? 'n/a'}, 200-day SMA ${sma(200)?.toFixed(2) ?? 'n/a'}. High ${Math.max(...bars.map(b => b.h)).toFixed(2)}, low ${Math.min(...bars.map(b => b.l)).toFixed(2)}.\nMonth-start closes:\n${monthly.join('\n')}`), summary: `${s} ${a.range || '1y'} price history` };
      } });

    add({ name: 'get_technicals', label: 'Computed technicals', description: 'Quant and technical statistics for a symbol from daily bars: last close, 1D/1W/1M/3M/YTD/1Y returns, 20/60-day annualized volatility, 52-week range, beta and correlation to SPY over 1 year, max drawdown, RSI14, 50/200-day moving averages.', parameters: obj({ symbol: str('Symbol') }, ['symbol']),
      run: async (a, { signal }) => {
        const s = String(a.symbol).toUpperCase();
        await Promise.all([G('ensurePrices')(s, { signal }), G('ensurePrices')('SPY', { signal })]);
        const st = G('priceStats')(s);
        if (!st) throw new Error('No price data for ' + s);
        return { output: `${s} technical statistics (computed in the browser from stored daily closes):\n` + Object.entries(st).map(([k, v]) => `${k}: ${typeof v === 'number' ? +v.toFixed(4) : v instanceof Date ? v.toISOString().slice(0, 10) : JSON.stringify(v)}`).join('\n'), summary: `${s} technicals` };
      } });

    add({ name: 'get_quotes', label: 'Fetched quotes', description: 'Latest stored closing prices and day change for up to 10 symbols (covered tickers or market symbols).', parameters: obj({ symbols: { type: 'array', maxItems: 10, items: { type: 'string' } } }, ['symbols']),
      run: async (a, { signal }) => {
        const syms = a.symbols.map(s => String(s).toUpperCase()).slice(0, 10);
        await Promise.all(syms.map(s => G('ensurePrices')(s, { signal }).catch(() => null)));
        return { output: syms.map(s => { const b = G('PX')[s]?.bars, l = b?.at(-1), p = b?.at(-2); return l ? `${s}: close ${l.c.toFixed(2)} on ${l.t.toISOString().slice(0, 10)}${p ? ` (${((l.c / p.c - 1) * 100).toFixed(2)}% vs prior close)` : ''}` : `${s}: no price data`; }).join('\n'), summary: syms.join(', ') };
      } });

    add({ name: 'get_insider_trades', label: 'Pulled insider trades', description: 'Form 4 insider transactions for a covered company from the last 12 months (open-market buys P / sales S, grants, exercises, tax withholding), with 90-day and 365-day summaries. Each row links to its SEC filing.', parameters: obj({ ticker: str('Covered ticker'), open_market_only: { type: 'boolean' }, limit: int('1-60', 1, 60) }, ['ticker']),
      run: async (a, { signal }) => {
        const tk = covered(a.ticker);
        const ins = await G('getJSON')(`insiders/${tk}.json`, { signal });
        const rows = (ins.rows || []).filter(r => !a.open_market_only || r.code === 'P' || r.code === 'S').slice(0, a.limit || 25);
        const sum = w => w ? `buys ${w.buys} ($${Math.round(w.buy_value || 0).toLocaleString('en-US')}), sales ${w.sells} ($${Math.round(w.sell_value || 0).toLocaleString('en-US')}), ${w.plan_sells ?? 0} under 10b5-1 plans` : 'n/a';
        return { output: clip(`${tk} Form 4 transactions (SEC filings, generated ${ins.generated}). Last 90 days: ${sum(ins.w90)}. Last 365 days: ${sum(ins.w365)}.\n` + rows.map(r => `${r.tx_date} | ${r.owner} (${r.role || 'n/a'}) | code ${r.code} ${ins.codes?.[r.code] || ''} | ${r.acq_disp === 'A' ? '+' : '-'}${Number(r.shares).toLocaleString('en-US')} sh @ ${r.price || 'n/a'} | owned after ${r.owned_after != null ? Number(r.owned_after).toLocaleString('en-US') : 'n/a'}${r.plan_10b5 ? ' | 10b5-1 plan' : ''} | filed ${r.filed} | filing:${tk}:${r.accession} | ${edgarFolder(tk, r.accession)}`).join('\n')),
          summary: `${rows.length} ${tk} insider transactions`, sources: [...new Set(rows.map(r => r.accession))].slice(0, 4).map(accn => ({ url: edgarFolder(tk, accn), title: `${tk} Form 4 ${accn}` })) };
      } });

    add({ name: 'get_macro', label: 'Pulled macro series', description: 'Macro series stored by Filed Under. Names: CPI, CORE_CPI, UNRATE (%), PAYEMS (thousands), EFFR (%), CURVE (latest Treasury par curve), SPREAD_2S10S (daily, %). Returns the last n observations (default 24).', parameters: obj({ name: { type: 'string', enum: ['CPI', 'CORE_CPI', 'UNRATE', 'PAYEMS', 'EFFR', 'CURVE', 'SPREAD_2S10S'] }, n: int('Observations, 1-120', 1, 120) }, ['name']),
      run: async a => {
        if (!G('MACRO')) await G('getJSON')('macro.json').then(G('applyMacro')).catch(() => {});
        const out = G('macroSeries')(a.name, a.n || 24);
        const src = { CPI: 'https://fred.stlouisfed.org/series/CPIAUCSL', CORE_CPI: 'https://fred.stlouisfed.org/series/CPILFESL', UNRATE: 'https://fred.stlouisfed.org/series/UNRATE', PAYEMS: 'https://fred.stlouisfed.org/series/PAYEMS', EFFR: 'https://fred.stlouisfed.org/series/EFFR' }[a.name] || 'https://home.treasury.gov/resource-center/data-chart-center/interest-rates';
        return { output: clip(`${a.name} (source ${src}; macro.json generated ${G('MACRO')?.generated || 'n/a'}):\n${JSON.stringify(out)}`), summary: `${a.name}, ${a.n || 24} observations`, sources: [{ url: src, title: a.name }] };
      } });

    add({ name: 'get_news', kind: 'web', label: 'Read headlines', description: 'Stored recent news for a covered company (titles, URLs, dates) or, with no ticker, recent macro/market headlines. Use web_research for anything newer or broader.', parameters: obj({ ticker: str('Covered ticker (optional)'), query: str('Optional keyword filter') }),
      run: async (a, { signal }) => {
        let items = [];
        if (a.ticker) { const tk = await company(a.ticker, signal); items = (G('E').news[tk]?.news || []).map(x => ({ ...x, tk })); }
        else { if (!G('MACRO')) await G('getJSON')('macro.json').then(G('applyMacro')).catch(() => {}); items = G('E').macro_news || []; }
        if (a.query) items = items.filter(x => `${x.title} ${x.snippet || ''}`.toLowerCase().includes(a.query.toLowerCase()));
        items = items.slice(0, 15);
        if (!items.length) return { output: 'No stored headlines match. Use web_research with topic "news" instead.', summary: 'no stored headlines' };
        return { output: items.map(x => `${x.date || ''} | ${hostOf(x.url)} | ${x.title} | ${x.url}`).join('\n'), summary: `${items.length} headlines`, sources: items.slice(0, 8).map(x => ({ url: x.url, title: x.title })) };
      } });

    add({ name: 'get_earnings_reactions', label: 'Measured earnings reactions', description: 'For one covered company: earnings-release dates (8-K Item 2.02) with the next-session stock return, the same-day SPY return and the 5-session drift.', parameters: obj({ ticker: str('Covered ticker') }, ['ticker']),
      run: async (a, { signal }) => {
        const tk = await company(a.ticker, signal);
        const rows = G('earningsReactions')(tk);
        return { output: rows.length ? `${tk} earnings reactions (8-K Item 2.02 filing dates; returns from stored closes):\n` + rows.map(r => `filed ${r.date}: next 1d ${(r.r1 * 100).toFixed(2)}% | SPY ${r.s1 == null ? 'n/a' : (r.s1 * 100).toFixed(2) + '%'} | 5d drift ${r.r5 == null ? 'n/a' : (r.r5 * 100).toFixed(2) + '%'}`).join('\n') : 'No earnings releases with price data.', summary: `${rows.length} ${tk} earnings reactions` };
      } });

    add({ name: 'web_research', kind: 'web', label: 'Researched the web', description: 'Search the web (Tavily) for recent news, earnings-call coverage, investor-relations pages, analyst commentary and industry data. Returns titles, URLs, dates and snippets. Cite pages you rely on as normal markdown links.', parameters: obj({ query: str('Search query', { maxLength: 400 }), topic: { type: 'string', enum: ['general', 'news', 'finance'] }, days: int('Only results from the last N days (news topic)', 1, 365) }, ['query']),
      run: async (a, { signal }) => {
        const r = await post('/api/search', { query: a.query, topic: a.topic || 'general', days: a.days }, signal);
        if (!r.results?.length) return { output: 'No web results. Try different terms.', summary: `no results for "${a.query}"` };
        return { output: clip(r.results.map((x, i) => `[${i + 1}] ${x.title}\n${x.url}${x.date ? ' · ' + x.date : ''}\n${x.snippet}`).join('\n\n')), summary: `${r.results.length} results for "${a.query}"`, sources: r.results.map(x => ({ url: x.url, title: x.title })) };
      } });

    add({ name: 'read_webpage', kind: 'web', label: 'Read web page', description: 'Read a public web page or PDF as markdown (Firecrawl), 12,000 characters per page of output. Give query to return only passages around a phrase. Continue long pages with offset and the returned content_id.', parameters: obj({ url: str('Full https URL'), query: str('Optional phrase to focus on'), offset: int('Continuation offset', 0, 240000), content_id: str('contentId from the first page') }, ['url']),
      run: async (a, { signal }) => {
        const r = await post('/api/scrape', { url: a.url, offset: a.offset || 0, contentId: a.content_id }, signal);
        let text = r.markdown || '';
        if (a.query) { const hits = passages(text, a.query, 900, 1500); if (hits.length) text = hits.join('\n…\n'); }
        return { output: clip(`${r.title || ''} (${r.url})${r.nextOffset != null ? `\nMore text: offset ${r.nextOffset}, content_id ${r.contentId}` : ''}\n\n${text}`), summary: `Read ${hostOf(r.url)}`, sources: [{ url: r.url, title: r.title || hostOf(r.url) }] };
      } });

    add({ name: 'web_map', kind: 'web', label: 'Mapped site', description: 'List URLs on a website (sitemap discovery), optionally ranked by a keyword, e.g. to find investor-relations press releases before reading them.', parameters: obj({ url: str('Site root, e.g. https://investor.nvidia.com'), search: str('Keyword to rank/filter URLs') }, ['url']),
      run: async (a, { signal }) => {
        const r = await post('/api/scrape', { mode: 'map', url: a.url, search: a.search, limit: 120 }, signal);
        return { output: clip(r.links.map(l => `${l.url}${l.title ? ' | ' + l.title : ''}`).join('\n') || 'No URLs found.'), summary: `${r.links.length} URLs on ${hostOf(a.url)}` };
      } });

    add({ name: 'web_crawl', kind: 'web', label: 'Crawled site', description: 'Read several pages of one site at once (bounded crawl, at most 15 pages, about a minute). Prefer web_map + read_webpage for targeted reads.', parameters: obj({ url: str('Start URL'), limit: int('Max pages 1-15', 1, 15), include_paths: { type: 'array', maxItems: 10, items: { type: 'string' } } }, ['url']),
      run: async (a, { signal }) => {
        const r = await post('/api/scrape', { mode: 'crawl', url: a.url, limit: a.limit || 6, includePaths: a.include_paths }, signal);
        if (!r.pages?.length) return { output: `Crawl ${r.status}; no pages.`, summary: 'crawl returned nothing' };
        return { output: clip(r.pages.map(p => `## ${p.title} (${p.url})\n${p.markdown}`).join('\n\n'), MAX * 1.5), summary: `${r.pages.length} pages from ${hostOf(a.url)}${r.status !== 'completed' ? ' (partial)' : ''}`, sources: r.pages.filter(p => p.url).slice(0, 8).map(p => ({ url: p.url, title: p.title })) };
      } });

    add({ name: 'web_extract', kind: 'web', label: 'Extracted data', description: 'Extract structured data from one web page with a prompt (e.g. "all contract awards with date, value and counterparty"). Returns JSON extracted by a model; verify key values against the page.', parameters: obj({ url: str('Page URL'), prompt: str('What to extract and in what shape', { maxLength: 2000 }) }, ['url', 'prompt']),
      run: async (a, { signal }) => {
        const r = await post('/api/scrape', { mode: 'extract', url: a.url, prompt: a.prompt }, signal);
        return { output: clip(`Source: ${r.url}${r.truncated ? ' (extraction too large; narrow the prompt)' : ''}\n${JSON.stringify(r.json, null, 1)}`), summary: `Extracted from ${hostOf(r.url)}`, sources: [{ url: r.url, title: r.title || hostOf(r.url) }] };
      } });

    add({ name: 'get_page_context', kind: 'nav', label: 'Read your screen', description: 'What the user is looking at right now: route, page title, open source panel and visible text. Call this first when the user says "this", "here", "this page" or "this company".', parameters: obj({}),
      run: async () => {
        const page = root.document.querySelector('#page'), drawer = root.document.querySelector('#drawer.open');
        const text = s => (s?.innerText ?? s?.textContent ?? '').replace(/\n{2,}/g, '\n').trim();
        return { output: clip(`Route: ${root.location.hash || '#markets'}\nTitle: ${root.document.title}\n${drawer ? 'Source panel open: ' + text(drawer.querySelector('.dh')).slice(0, 400) + '\n' : ''}Visible content:\n${text(page).slice(0, 7000)}`, 9000), summary: root.location.hash || '#markets' };
      } });

    add({ name: 'navigate_to', kind: 'nav', label: 'Opened page', description: 'Open a page in the user\'s Filed Under app. Use when the user asks to show/open/go to something, or when a page shows the answer best (say which page you opened). Company pages take a ticker and optional tab; technicals takes an optional symbol.', parameters: obj({ page: str('Page id, e.g. company, companies, screener, compare, technicals, sectors, macro, kpis, theses, workbench, research, analyst, watchlists, ideas, markets'), ticker: str('Ticker for company pages'), tab: { type: 'string', enum: ['overview', 'financials', 'segments', 'news', 'earnings', 'filings', 'insiders', 'changes'] }, symbol: str('Symbol for technicals') }, ['page']),
      run: async a => {
        const pages = new Set([...(G('PAGES') || []).map(p => p.id), ...Object.keys(root.FU_PAGES || {}), 'company']);
        const page = String(a.page).toLowerCase();
        if (!pages.has(page)) throw new Error(`Unknown page ${page}. Pages: ${[...pages].join(', ')}`);
        let hash = '#' + page;
        if (page === 'company') { const tk = covered(a.ticker); hash = '#company-' + tk.toLowerCase(); if (a.tab && G('STATE')) { G('STATE').coTab = a.tab; G('store')?.set('coTab', a.tab); } }
        else if (page === 'technicals' && a.symbol) hash = '#technicals-' + encodeURIComponent(String(a.symbol).toUpperCase());
        if (root.location.hash === hash && G('route')) G('route')(); else root.location.hash = hash;
        return { output: `Opened ${hash} for the user.`, summary: hash };
      } });

    add({ name: 'run_code', kind: 'code', label: 'Ran code', description: 'Code interpreter: run JavaScript (async function body; use return for the result and console.log for output) in an isolated Web Worker with no network and an 8 s limit. data.universe: every covered company\'s screener metrics (same fields as screen_companies plus ticker, name, sector). data.companies[TICKER]: {annual, quarterly} each {periods: string[], ends: string[], rows: {label: number[]}} for requested tickers (statement and ratio labels). data.prices[SYMBOL]: [[date, close], …] daily. stats has mean, median, sum, stdev, cagr(a,b,years), corr(a,b). Use for regressions, DCFs, scenario tables and aggregations; results are calculations, not filed facts.',
      parameters: obj({ code: str('JavaScript function body', { maxLength: 20000 }), tickers: { type: 'array', maxItems: 12, items: { type: 'string' } }, price_symbols: { type: 'array', maxItems: 8, items: { type: 'string' } } }, ['code']),
      run: async (a, { signal }) => {
        const companies = {};
        for (const t of a.tickers || []) {
          const tk = await company(t, signal), m = G('model')(tk), labels = [...Object.keys(m.byLabel), ...m.ratioDefs.map(d => d.label)];
          const pack = kind => { const ends = m.periods(kind); return { ends, periods: ends.map(e => { try { return G('plabel')(tk, e, kind); } catch { return e; } }), rows: Object.fromEntries(labels.map(l => [l, ends.map(e => m.metric(l, kind, e)?.v ?? null)])) }; };
          companies[tk] = { annual: pack('A'), quarterly: pack('Q') };
        }
        const prices = {};
        for (const s0 of a.price_symbols || []) { const s = String(s0).toUpperCase(), px = await G('ensurePrices')(s, { signal }).catch(() => null); if (px?.bars?.length) prices[s] = px.bars.map(b => [b.t.toISOString().slice(0, 10), b.c]); }
        const uni = universe().map(({ spark, shares_metadata, ...c }) => c);
        const r = await runSandbox(a.code, { universe: uni, companies, prices });
        const out = [r.logs.length ? 'console:\n' + r.logs.join('\n') : '', r.result ? 'result:\n' + r.result : '', r.error ? 'ERROR: ' + r.error : ''].filter(Boolean).join('\n\n');
        if (!r.ok && !r.logs.length) throw new Error(String(r.error || 'Code failed').split('\n')[0]);
        return { output: clip(out || '(no output; use return or console.log)'), summary: r.ok ? `ran in ${r.ms} ms` : `error: ${String(r.error).split('\n')[0].slice(0, 80)}` };
      } });

    add({ name: 'inspect_evidence', kind: 'filing', label: 'Inspected evidence', description: 'Inspect an exact evidence record (value, unit, period, filing URL, derivation inputs) by its ev_ id.', parameters: obj({ evidence_id: str('Evidence id, ev_…') }, ['evidence_id']),
      run: async a => { const e = root.FU_EVIDENCE?.get(a.evidence_id); if (!e) return { output: `Unknown evidence id ${a.evidence_id}`, summary: 'unknown evidence' }; return { output: JSON.stringify({ evidence: e, inputs: e.inputEvidenceIds.map(id => root.FU_EVIDENCE.get(id)) }), summary: `${e.ticker} ${e.metric} ${e.period}`, sources: e.sourceUrl ? [{ url: e.sourceUrl, title: `${e.ticker} ${e.metric}` }] : [] }; } });

    add({ name: 'update_plan', kind: 'plan', label: 'Planned', description: 'Publish or revise your working plan and notes (shown live to the user). Call at the start of any multi-step task, and again when a step finishes, the plan changes or you restart with a different approach. Keep it short.', parameters: obj({ goal: str('One-line restatement of what the user needs'), steps: { type: 'array', maxItems: 12, items: obj({ title: str('Step'), status: { type: 'string', enum: ['todo', 'doing', 'done', 'dropped'] } }, ['title', 'status']) }, notes: str('Key findings so far, open questions, contradictions (empty if none)') }, ['goal', 'steps']), run: async () => 'Plan updated.' });

    add({ name: 'ask_user', kind: 'ask', label: 'Asked you', description: 'Pause and ask the user a clarifying question only when the request is genuinely ambiguous and the choice materially changes the work (ticker, period, scenario, which of several readings). Offer 2-4 short options. Do not ask what you can find out yourself.', parameters: obj({ question: str('The question'), options: { type: 'array', maxItems: 4, items: { type: 'string' } } }, ['question', 'options']), run: async () => 'No answer.' });
    return T;
  }

  /* -------- sandbox: model-written JavaScript in a throwaway Worker with network removed -------- */
  const WORKER_SRC = `
self.fetch = undefined; self.XMLHttpRequest = undefined; self.WebSocket = undefined; self.EventSource = undefined; self.indexedDB = undefined; self.caches = undefined;
self.importScripts = () => { throw new Error("imports are disabled"); };
self.onmessage = async (e) => {
  const logs = [];
  const fmt = (v) => typeof v === "string" ? v : (() => { try { return JSON.stringify(v, null, 1); } catch { return String(v); } })();
  const push = (s) => { if (logs.length < 400) logs.push(String(s).slice(0, 4000)); };
  const console = { log: (...a) => push(a.map(fmt).join(" ")), table: (v) => push(fmt(v)), error: (...a) => push("ERROR " + a.map(fmt).join(" ")), warn: (...a) => push("WARN " + a.map(fmt).join(" ")) };
  const stats = {
    mean: (xs) => xs.reduce((s, x) => s + x, 0) / xs.length,
    sum: (xs) => xs.reduce((s, x) => s + x, 0),
    median: (xs) => { const s = [...xs].sort((a, b) => a - b); const m = s.length >> 1; return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2; },
    stdev: (xs) => { const m = xs.reduce((s, x) => s + x, 0) / xs.length; return Math.sqrt(xs.reduce((s, x) => s + (x - m) ** 2, 0) / (xs.length - 1)); },
    cagr: (a, b, years) => Math.pow(b / a, 1 / years) - 1,
    corr: (a, b) => { const n = Math.min(a.length, b.length); const ma = a.slice(0, n).reduce((s, x) => s + x, 0) / n, mb = b.slice(0, n).reduce((s, x) => s + x, 0) / n; let c = 0, va = 0, vb = 0; for (let i = 0; i < n; i++) { c += (a[i] - ma) * (b[i] - mb); va += (a[i] - ma) ** 2; vb += (b[i] - mb) ** 2; } return c / Math.sqrt(va * vb); },
  };
  try {
    const fn = new Function("data", "console", "stats", '"use strict";\\nreturn (async () => {\\n' + e.data.code + '\\n})();');
    const r = await fn(e.data.data, console, stats);
    self.postMessage({ ok: true, logs, result: r === undefined ? "" : fmt(r).slice(0, 20000) });
  } catch (err) {
    self.postMessage({ ok: false, logs, result: "", error: String(err && err.stack || err).slice(0, 2000) });
  }
};`;
  function runSandbox(code, data, timeoutMs = 8000) {
    const t0 = Date.now();
    if (typeof root.Worker !== 'function' || !root.URL?.createObjectURL) return Promise.resolve({ ok: false, logs: [], result: '', error: 'Code sandbox is not available in this browser', ms: 0 });
    const url = root.URL.createObjectURL(new root.Blob([WORKER_SRC], { type: 'text/javascript' }));
    const w = new root.Worker(url);
    return new Promise(resolve => {
      const done = r => { clearTimeout(timer); w.terminate(); root.URL.revokeObjectURL(url); resolve({ ...r, ms: Date.now() - t0 }); };
      const timer = setTimeout(() => done({ ok: false, logs: [], result: '', error: `Timed out after ${timeoutMs / 1000}s` }), timeoutMs);
      w.onmessage = e => done(e.data);
      w.onerror = e => { e.preventDefault?.(); done({ ok: false, logs: [], result: '', error: e.message || 'Worker error' }); };
      w.postMessage({ code: String(code), data });
    });
  }

  /* -------- registry -------- */
  const NAME = /^[a-z][a-z0-9_]{1,63}$/;
  root.FU_ASSISTANT_TOOLS = Array.isArray(root.FU_ASSISTANT_TOOLS) ? root.FU_ASSISTANT_TOOLS : [];
  function adaptProvider(t) {
    // Existing FU_TOOL_PROVIDERS tools ({name, description, inputSchema, execute}) keep working in the panel.
    return { name: t.name, label: t.label || t.name.replace(/_/g, ' '), kind: 'tool', description: t.description || '', parameters: t.inputSchema || { type: 'object', properties: {} },
      run: async (args, ctx) => { const r = await t.execute(args, ctx); return { output: typeof r === 'string' ? r : JSON.stringify(r ?? null), summary: 'done' }; } };
  }
  /** Tools for the current route: built-ins, then plugins (validated, built-ins win), then legacy providers. */
  function toolsFor(routeId) {
    const out = builtins(), names = new Set(out.map(t => t.name)), skipped = [];
    const accept = t => {
      if (!t || typeof t !== 'object' || !NAME.test(t.name || '') || typeof t.run !== 'function' || names.has(t.name)) { skipped.push(t?.name || '(unnamed)'); return; }
      if (Array.isArray(t.pages) && t.pages.length && !t.pages.includes(routeId)) return;
      const p = t.parameters && typeof t.parameters === 'object' && t.parameters.type === 'object' ? t.parameters : { type: 'object', properties: {} };
      names.add(t.name); out.push({ name: t.name, label: String(t.label || t.name.replace(/_/g, ' ')), kind: t.kind || 'tool', description: String(t.description || '').slice(0, 3000), parameters: p, run: t.run, plugin: true });
    };
    root.FU_ASSISTANT_TOOLS.forEach(accept);
    const trace = () => ({ done() {}, fail() {} });
    for (const provider of root.FU_TOOL_PROVIDERS || []) { try { (provider(trace) || []).forEach(t => t?.execute && accept(adaptProvider(t))); } catch { /* a broken provider never blocks the panel */ } }
    if (skipped.length && root.console) root.console.warn('Assistant tools skipped (invalid or duplicate name):', skipped.join(', '));
    return out;
  }
  function registerTool(def) { root.FU_ASSISTANT_TOOLS.push(def); return def; }
  const LABELS = () => Object.fromEntries(toolsFor('').map(t => [t.name, t.label]));

  root.FU_ASSISTANT_TOOLKIT = Object.freeze({ toolsFor, registerTool, runSandbox, htmlToText, passages, labels: LABELS, SCREEN_FIELDS });
})(typeof window !== 'undefined' ? window : globalThis);
