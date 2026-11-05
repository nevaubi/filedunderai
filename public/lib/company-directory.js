/* Registered-company discovery is deliberately independent of screener membership. */
(function (root) {
  'use strict';
  let records = [], byTicker = new Map(), manifest = null, pending = null;
  const finite = value => typeof value === 'number' && Number.isFinite(value);
  const node = (tag, attrs = {}, ...children) => {
    const e = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
      if (value == null || value === false) continue;
      if (key.startsWith('on')) e.addEventListener(key.slice(2), value);
      else e.setAttribute(key, value === true ? '' : String(value));
    }
    for (const child of children.flat()) if (child != null && child !== false) e.append(child.nodeType ? child : String(child));
    return e;
  };
  function apply(directory, activeCompanies = []) {
    manifest = directory || { companies: [] };
    const active = new Map(activeCompanies.map(c => [c.ticker, c]));
    const merged = new Map();
    for (const c of [...(manifest.companies || []), ...activeCompanies]) {
      const ticker = String(c.ticker || '').toUpperCase();
      if (!/^[A-Z0-9][A-Z0-9.-]{0,14}$/.test(ticker)) continue;
      merged.set(ticker, { ...merged.get(ticker), ...c, ...active.get(ticker), ticker, screener_eligible: active.has(ticker) });
    }
    records = [...merged.values()].map(c => {
      const sic = typeof c.sic === 'string' && c.sic.trim() && !/^\d+$/.test(c.sic.trim()) ? c.sic : c.sic_description;
      const industry = c.industry_source === 'SEC Standard Industrial Classification' ? sic : c.industry || sic;
      return Object.freeze({ ...c, name: c.name || c.display_name || c.ticker, sic_description: sic, industry, industry_source: c.industry_source || (!c.industry && sic ? 'SEC Standard Industrial Classification' : null) });
    });
    byTicker = new Map(records.map(c => [c.ticker, c]));
    return records;
  }
  async function load(activeCompanies = []) {
    if (!pending) pending = (async () => {
      const base = root.FU_CONFIG?.dataBase;
      const paths = [typeof base === 'string' && base ? `${base}company-directory.json` : null, '/data/company-directory.json'].filter(Boolean);
      for (const path of paths) {
        try {
          const response = await fetch(path);
          if (!response.ok) continue;
          const directory = await response.json();
          if (directory?.schema_version === 1 && Array.isArray(directory.companies) && directory.companies.length) return directory;
        } catch { /* Fall back to the bundled, verified registry. */ }
      }
      throw new Error('Company directory is unavailable');
    })().catch(error => { pending = null; if (!activeCompanies.length) throw error; return { companies: [] }; });
    return apply(await pending, activeCompanies);
  }
  const get = ticker => byTicker.get(String(ticker || '').toUpperCase());
  const list = () => records.slice();
  function query({ q = '', sector = '', sort = 'market_cap', coverage = '' } = {}) {
    const terms = String(q).trim().toLowerCase().split(/\s+/).filter(Boolean);
    const score = c => !terms.length ? 0 : c.ticker.toLowerCase() === terms.join(' ') ? 3 : c.ticker.toLowerCase().startsWith(terms.join(' ')) ? 2 : 1;
    const covered = c => !coverage || (coverage === 'screener' ? c.screener_eligible : coverage === 'leadership' ? c.leadership_count > 0 : coverage === 'profile' ? c.coverage?.profile === 'available' : false);
    return records.filter(c => (!sector || c.sector === sector) && covered(c) && terms.every(term => [c.ticker, c.name, c.legal_name, c.sector, c.industry, c.sic_description].filter(Boolean).join(' ').toLowerCase().includes(term))).sort((a, b) => {
      const rank = score(b) - score(a); if (rank) return rank;
      if (sort === 'ticker') return a.ticker.localeCompare(b.ticker);
      if (sort === 'name') return a.name.localeCompare(b.name);
      if (sort === 'change') { const delta = (finite(b.chg_1d) ? b.chg_1d : -Infinity) - (finite(a.chg_1d) ? a.chg_1d : -Infinity); if (delta && !Number.isNaN(delta)) return delta; }
      const delta = (finite(b.market_cap) ? b.market_cap : -Infinity) - (finite(a.market_cap) ? a.market_cap : -Infinity);
      return (Number.isNaN(delta) ? 0 : delta) || a.name.localeCompare(b.name);
    });
  }
  function related(ticker, limit = 5) {
    const company = get(ticker); if (!company) return [];
    return records.filter(c => c.ticker !== company.ticker && c.cik !== company.cik && company.sector && c.sector === company.sector).sort((a, b) => {
      const exact = c => company.industry && c.industry === company.industry ? 2 : company.sic_description && c.sic_description === company.sic_description ? 1 : 0;
      return exact(b) - exact(a) || Number(b.screener_eligible) - Number(a.screener_eligible) || (b.market_cap || 0) - (a.market_cap || 0) || a.name.localeCompare(b.name);
    }).slice(0, limit);
  }
  const price = value => finite(value) ? '$' + value.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '—';
  const change = value => finite(value) ? (value >= 0 ? '+' : '−') + Math.abs(value * 100).toFixed(2) + '%' : '—';
  const money = value => !finite(value) ? '—' : value >= 1e12 ? '$' + (value / 1e12).toFixed(2) + 'T' : value >= 1e9 ? '$' + (value / 1e9).toFixed(2) + 'B' : '$' + (value / 1e6).toFixed(0) + 'M';
  function render(host, { logo, onOpen, initialQuery = '' } = {}) {
    host.replaceChildren(); host.classList.add('companies-page');
    const state = { q: initialQuery, sector: '', sort: 'market_cap', coverage: '', page: 0 }, size = 50;
    const activeCount = records.filter(c => c.screener_eligible).length;
    const heading = node('div', { class: 'directory-heading' }, node('div', {}, node('div', { class: 'directory-eyebrow' }, 'COMPANY RESEARCH'), node('h1', {}, 'Companies'), node('p', {}, 'A clear view of the business, the numbers, and the people behind them.')), node('div', { class: 'directory-total' }, node('strong', {}, records.length.toLocaleString('en-US')), node('span', {}, 'companies to explore')));
    const input = node('input', { type: 'search', placeholder: 'Search a company, ticker, or industry', 'aria-label': 'Search companies', autocomplete: 'off', value: initialQuery });
    const sector = node('select', { 'aria-label': 'Filter companies by sector' }, node('option', { value: '' }, 'All sectors'), [...new Set(records.map(c => c.sector).filter(Boolean))].sort().map(s => node('option', { value: s }, s)));
    const sorting = node('select', { 'aria-label': 'Sort companies' }, node('option', { value: 'market_cap' }, 'Market cap · largest'), node('option', { value: 'name' }, 'Company · A to Z'), node('option', { value: 'ticker' }, 'Ticker · A to Z'), node('option', { value: 'change' }, 'Daily change · highest'));
    const coverage = node('select', { 'aria-label': 'Filter company coverage' }, node('option', { value: '' }, 'All companies'), node('option', { value: 'screener' }, 'In the screener'), node('option', { value: 'profile' }, 'Company metadata'), node('option', { value: 'leadership' }, 'Leadership available'));
    const count = node('span', { 'data-count': '', 'aria-live': 'polite' });
    const countLine = node('div', { class: 'directory-results-meta' }, count, node('span', {}, 'Closing prices · USD'));
    const tbody = node('tbody');
    const table = node('table', { class: 'directory-table' }, node('thead', {}, node('tr', {}, ['Company', 'Sector / industry', 'Last close', '1D change', 'Market cap', 'Coverage'].map((text, i) => node('th', { scope: 'col', class: i >= 2 && i <= 4 ? 'num' : '' }, text)))), tbody);
    const previous = node('button', { class: 'btn', 'data-action': 'previous', onclick: () => { state.page--; draw(); } }, '← Previous');
    const next = node('button', { class: 'btn', 'data-action': 'next', onclick: () => { state.page++; draw(); } }, 'Next →');
    const pageLabel = node('span', { class: 'directory-page-number' });
    const footer = node('div', { class: 'directory-pagination' }, node('span', {}, `${activeCount.toLocaleString('en-US')} companies also available in the screener`), node('div', {}, previous, pageLabel, next));
    const clear = node('button', { class: 'directory-clear', onclick: () => { state.q = ''; state.sector = ''; state.coverage = ''; state.page = 0; input.value = sector.value = coverage.value = ''; draw(); input.focus(); } }, 'Clear filters');
    const tools = node('div', { class: 'directory-tools' }, node('label', { class: 'directory-search' }, node('span', { 'aria-hidden': 'true' }, '⌕'), input), sector, coverage, sorting);
    host.append(heading, tools, countLine, node('div', { class: 'directory-table-wrap' }, table), footer);
    function draw() {
      const rows = query(state); const pages = Math.max(1, Math.ceil(rows.length / size)); state.page = Math.min(Math.max(0, state.page), pages - 1);
      const start = state.page * size, shown = rows.slice(start, start + size);
      count.replaceChildren(...[`${rows.length ? start + 1 : 0}–${Math.min(start + size, rows.length)} of ${rows.length.toLocaleString('en-US')} companies`, state.q || state.sector || state.coverage ? clear : null].filter(Boolean));
      tbody.replaceChildren();
      for (const c of shown) {
        const identity = node('a', { href: '#company-' + c.ticker.toLowerCase(), class: 'directory-identity', onclick: () => onOpen?.(c.ticker) }, logo ? logo(c.ticker, 32) : node('span', { class: 'directory-logo', 'aria-hidden': 'true' }, c.ticker.slice(0, 2)), node('span', {}, node('strong', {}, c.name), node('span', { class: 'directory-ticker' }, c.ticker)));
        const date = c.price_date || c.price_as_of || c.as_of;
        const hasPeople = c.leadership_count > 0, hasMetadata = c.coverage?.profile === 'available';
        const coverageLabel = hasPeople ? 'Leadership' : hasMetadata ? 'Company details' : c.screener_eligible ? 'Screener' : 'Registered';
        const coverageTitle = [hasPeople ? `${c.leadership_count} sourced leaders · ${c.portrait_count || 0} portraits` : hasMetadata ? 'Sourced company metadata; leadership is not yet available' : 'Company registered; additional company metadata is not yet published', c.screener_eligible ? 'Screener available' : 'Screener validation pending'].join('. ');
        tbody.append(node('tr', {}, node('td', {}, identity), node('td', { class: 'directory-sector' }, node('span', {}, c.sector || 'Sector unavailable'), c.industry ? node('small', { title: [c.industry, c.industry_source].filter(Boolean).join(' · ') }, c.industry) : null), node('td', { class: 'num', title: date ? 'Close ' + date : 'Latest published close' }, price(c.price)), node('td', { class: 'num ' + (c.chg_1d > 0 ? 'up' : c.chg_1d < 0 ? 'down' : '') }, change(c.chg_1d)), node('td', { class: 'num' }, money(c.market_cap)), node('td', {}, node('span', { class: 'directory-coverage' + (hasPeople ? ' complete' : ''), title: coverageTitle }, coverageLabel))));
      }
      if (!shown.length) tbody.append(node('tr', {}, node('td', { colspan: 6, class: 'directory-empty' }, node('strong', {}, 'No companies found'), node('p', {}, 'Try a different name, ticker, or sector.'))));
      previous.disabled = state.page === 0; next.disabled = state.page >= pages - 1; pageLabel.textContent = `${state.page + 1} / ${pages}`;
    }
    input.addEventListener('input', () => { state.q = input.value; state.page = 0; draw(); });
    for (const [control, key] of [[sector, 'sector'], [coverage, 'coverage'], [sorting, 'sort']]) control.addEventListener('change', () => { state[key] = control.value; state.page = 0; draw(); });
    draw();
  }
  root.FU_COMPANIES = Object.freeze({ apply, load, get, list, query, related, render });
})(typeof window !== 'undefined' ? window : globalThis);
