/* Company overview: reported finances and separately attributed professional context. */
(function (root) {
  'use strict';
  const cache = new Map();
  const historyCache = new Map();
  const finite = v => typeof v === 'number' && Number.isFinite(v);
  function node(tag, attrs = {}, ...children) {
    const e = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
      if (value == null || value === false) continue;
      if (key.startsWith('on')) e.addEventListener(key.slice(2), value);
      else e.setAttribute(key, value === true ? '' : String(value));
    }
    for (const child of children.flat()) if (child != null && child !== false) e.append(child.nodeType ? child : String(child));
    return e;
  }
  function safeUrl(value) {
    if (typeof value !== 'string' || !value.trim()) return null;
    try { const url = new URL(value.startsWith('//') ? 'https:' + value : value); return /^https?:$/.test(url.protocol) && !url.username && !url.password ? url.href : null; } catch { return null; }
  }
  const sourceLink = (url, label = 'Source') => safeUrl(url) ? node('a', { href: safeUrl(url), target: '_blank', rel: 'noopener noreferrer', class: 'cp-source-link' }, label + ' ↗') : null;
  const dateLabel = value => {
    if (!value) return 'Date unavailable';
    const d = new Date(value); return Number.isFinite(+d) ? d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' }) : 'Date unavailable';
  };
  function tenureLabel(start, asOf = new Date().toISOString().slice(0, 10)) {
    if (typeof start !== 'string' || !/^\d{4}(?:-\d{2}(?:-\d{2})?)?$/.test(start)) return 'Start date unavailable';
    const full = start.length === 4 ? start + '-01-01' : start.length === 7 ? start + '-01' : start;
    const d = new Date(full + 'T00:00:00Z'), now = new Date(asOf.slice(0, 10) + 'T00:00:00Z');
    if (!Number.isFinite(+d) || !Number.isFinite(+now) || d.toISOString().slice(0, 10) !== full || d > now || d.getUTCFullYear() < 1800) return 'Start date unavailable';
    if (start.length === 4) return 'Since ' + start;
    let months = (now.getUTCFullYear() - d.getUTCFullYear()) * 12 + now.getUTCMonth() - d.getUTCMonth();
    if (start.length === 10 && now.getUTCDate() < d.getUTCDate()) months--;
    const date = d.toLocaleDateString('en-US', { month: 'short', year: 'numeric', timeZone: 'UTC' });
    const duration = months < 1 ? 'under 1 month' : [Math.floor(months / 12) ? Math.floor(months / 12) + 'y' : '', months % 12 ? months % 12 + 'm' : ''].filter(Boolean).join(' ');
    return 'Since ' + date + ' · ' + duration;
  }
  function accepted(profile, ticker) { return profile && profile.ticker && String(profile.ticker).toUpperCase() === ticker.toUpperCase() ? profile : null; }
  async function load(ticker, { dataBase = '', signal } = {}) {
    ticker = String(ticker).toUpperCase();
    if (!/^[A-Z0-9][A-Z0-9.-]{0,14}$/.test(ticker)) return null;
    if (cache.has(ticker)) return cache.get(ticker);
    // The live cached profile takes precedence over the build's fallback snapshot.
    const paths = [dataBase ? `${dataBase}company-profiles/${ticker}.json` : null, `/data/company-profiles/${ticker}.json`].filter(Boolean);
    for (const path of paths) {
      try { const r = await fetch(path, { signal }); if (!r.ok) continue; const result = accepted(await r.json(), ticker); if (result) { cache.set(ticker, result); return result; } }
      catch (error) { if (error.name === 'AbortError') throw error; }
    }
    return null;
  }
  async function loadHistory(ticker, { dataBase = '', signal } = {}) {
    if (!dataBase || !/^[A-Z0-9][A-Z0-9.-]{0,14}$/.test(ticker)) return null;
    if (historyCache.has(ticker)) return historyCache.get(ticker);
    try {
      const response = await fetch(`${dataBase}tech/bars/${ticker}.json`, { signal });
      if (!response.ok) return null;
      const data = await response.json();
      if (!Array.isArray(data.d) || !data.d.length || (data.meta?.symbol && data.meta.symbol !== ticker)) return null;
      historyCache.set(ticker, data); return data;
    } catch (error) { if (error.name === 'AbortError') throw error; return null; }
  }
  function history(ticker, fallback) {
    const cached = historyCache.get(ticker); if (!cached) return fallback;
    const rawDate = cached.d.at(-1), cachedDate = new Date(Array.isArray(rawDate) ? rawDate[0] * 1000 : rawDate);
    const latest = fallback?.bars?.at(-1)?.t;
    const latestDate = new Date(latest);
    if (!Number.isFinite(+cachedDate) || !latest || !Number.isFinite(+latestDate)) return fallback;
    const end = cachedDate.toISOString().slice(0, 10);
    // Require the same latest session and price basis; no unsafe split-history merge.
    const cachedClose = Array.isArray(cached.c) ? cached.c.at(-1) : Array.isArray(rawDate) ? rawDate[4] : null;
    const currentClose = fallback?.bars?.at(-1)?.c;
    if (end !== latestDate.toISOString().slice(0, 10) || !finite(cachedClose) || !finite(currentClose) || Math.abs(cachedClose - currentClose) > Math.max(0.000001, Math.abs(currentClose) * 0.000001)) return fallback;
    return cached;
  }
  const money = value => !finite(value) ? '—' : new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', notation: 'compact', maximumFractionDigits: 2 }).format(value);
  function locationLabel(value) {
    if (typeof value === 'string') return value;
    if (!value || typeof value !== 'object') return null;
    return [value.locality || value.city, value.region || value.state || value.stateOrCountry, value.country].filter(Boolean).join(', ');
  }
  function render(host, options) {
    const { ticker, company = {}, summary = {}, snapshot = {}, model, prices, benchmarkPrices, helpers = {}, onTab, onCompany, dataBase = '' } = options;
    const name = summary.name || company.name || ticker;
    const controller = new AbortController(); let alive = true, chartCleanup = null;
    const rootNode = node('div', { class: 'cp-overview' }); host.append(rootNode);
    const moneyFmt = helpers.fmtMoney || money;
    const card = (title, extra, className = '') => node('section', { class: 'cp-card ' + className }, node('div', { class: 'cp-card-head' }, node('h2', {}, title), extra));
    const button = (text, tab) => node('button', { class: 'cp-text-button', onclick: () => onTab?.(tab) }, text + ' →');
    const chart = node('div', { class: 'cp-price-chart' });
    const facts = card('At a glance', null, 'cp-facts');
    const first = node('div', { class: 'cp-market-grid' }, chart, facts); rootNode.append(first);
    if (root.FU_COMPANY_CHART) chartCleanup = root.FU_COMPANY_CHART.render(chart, { ticker, companyName: name, prices, benchmarkPrices });
    else chart.append(node('div', { class: 'cp-card cp-unavailable' }, node('h2', {}, 'Price history'), node('p', {}, 'Price chart is temporarily unavailable.')));
    const description = card('About ' + name, null, 'cp-description');
    const financials = card('Financial snapshot', button('Full financials', 'financials'), 'cp-financials');
    const second = node('div', { class: 'cp-detail-grid' }, description, financials); rootNode.append(second);
    const leadership = card('Leadership', null, 'cp-leadership');
    const related = card('Related companies', node('a', { href: '#companies', class: 'cp-text-button' }, 'Explore all →'), 'cp-related');
    rootNode.append(node('div', { class: 'cp-leadership-grid' }, leadership, related));
    const recent = card('Company context', button('All coverage', 'news'), 'cp-recent'); rootNode.append(recent);

    function financialTable() {
      const lq = model?.latestQ?.(), annual = model?.latestA?.();
      const current = label => lq ? model?.get?.(label, 'Q', lq) : annual ? model?.get?.(label, 'A', annual) : null;
      const rows = [
        ['Revenue', snapshot.rev, 'TTM'], ['Net income', snapshot.ni, 'TTM'],
        ['Operating cash flow', model?.ttm?.('Operating cash flow'), 'TTM'], ['Free cash flow', snapshot.fcf, 'TTM'],
        ['Cash & equivalents', current('Cash & equivalents'), 'Latest quarter'], ['Total assets', current('Total assets'), 'Latest quarter'],
        ["Shareholders' equity", current("Shareholders' equity"), 'Latest quarter'], ['Long-term debt', current('Long-term debt'), 'Latest quarter'],
      ];
      const table = node('table', { class: 'cp-fin-table' }, node('thead', {}, node('tr', {}, node('th', { scope: 'col' }, 'Metric'), node('th', { scope: 'col' }, 'Value'), node('th', { scope: 'col' }, 'Period'))));
      const tbody = node('tbody');
      for (const [label, cell, period] of rows) {
        const valid = cell && finite(cell.v), end = cell?.end || (period === 'TTM' ? snapshot.rev?.end : lq || annual);
        const periodLabel = end ? (period === 'TTM' ? 'TTM · ' : '') + dateLabel(end) : period;
        const unit = cell?.unit;
        let value = '—';
        if (valid && /^[A-Z]{3}$/.test(unit || '')) value = new Intl.NumberFormat('en-US', { style: 'currency', currency: unit, notation: 'compact', minimumFractionDigits: 0, maximumFractionDigits: 2 }).format(cell.v);
        else if (valid) value = new Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: 2 }).format(cell.v) + ' ' + (unit || '(unit unavailable)');
        tbody.append(node('tr', {}, node('th', { scope: 'row' }, label), node('td', { class: 'num' }, valid ? node('button', { class: 'cp-fin-value', 'data-metric': label, onclick: () => helpers.openSource?.(cell), title: 'Open the source for ' + label + ' (' + (unit || 'unit unavailable') + ')', 'aria-label': label + ': ' + value + '. Open source.' }, value, node('span', { 'aria-hidden': 'true' }, ' ↗')) : node('span', { title: 'A supported value is not available for this period' }, '—')), node('td', {}, periodLabel)));
      }
      table.append(tbody); financials.append(node('div', { class: 'cp-fin-scroll' }, table), node('p', { class: 'cp-footnote' }, 'Amounts retain the reported currency. TTM uses four consecutive reported quarters. Select a value to inspect its filing or calculation.'));
      const signals = (options.signals || []).slice(0, 2);
      if (signals.length) financials.append(node('div', { class: 'cp-fin-insights' }, signals.map(s => node('p', {}, node('span', { class: 'cp-insight-dot ' + (s.tone || '') }), s.text))));
    }
    financialTable();
    for (const c of options.related || []) {
      related.append(node('a', { class: 'cp-related-row', href: '#company-' + c.ticker.toLowerCase(), onclick: () => onCompany?.(c.ticker) }, helpers.miniLogo ? helpers.miniLogo(c.ticker, 30) : node('span', { class: 'cp-related-initial' }, c.ticker[0]), node('span', { class: 'cp-related-name' }, node('strong', {}, c.name), node('small', {}, c.ticker + (c.industry ? ' · ' + c.industry : ''))), node('span', { class: 'cp-related-value' }, finite(c.market_cap) ? moneyFmt(c.market_cap) : 'View →')));
    }
    related.append(node('p', { class: 'cp-footnote' }, (options.related || []).length ? 'Related by sector, with industry matches first. Market capitalization shown where available.' : 'More companies will appear here when sector coverage is available.'));

    function paintProfile(profile) {
      profile = accepted(profile, ticker);
      facts.replaceChildren(facts.firstChild); description.replaceChildren(description.firstChild); leadership.replaceChildren(leadership.firstChild); recent.replaceChildren(recent.firstChild);
      const website = safeUrl(profile?.website) || safeUrl(company.website) || safeUrl(summary.website) || (profile?.website ? safeUrl('https://' + profile.website) : null);
      const location = locationLabel(profile?.headquarters) || locationLabel(company.address);
      const employees = profile?.employees?.count ?? profile?.employees;
      const workforceScope = { full_time_equivalent: 'Full-time equivalent', full_time: 'Full-time', employees: 'Reported workforce' }[profile?.employees?.scope];
      const workforce = finite(employees) ? node('span', {}, (profile?.employees?.approximate ? '≈ ' : '') + employees.toLocaleString('en-US'), profile?.employees?.as_of ? node('small', { class: 'cp-fact-detail' }, [workforceScope, dateLabel(profile.employees.as_of)].filter(Boolean).join(' · ')) : null) : null;
      const exchanges = [...new Set((company.exchanges || []).filter(Boolean))].join(', ');
      const founded = profile?.founded ? node('span', { title: profile.founded_note }, profile.founded) : null;
      const items = [['Sector', summary.sector || company.sector], ['Industry', profile?.industry || company.sicDescription || summary.industry], ['Headquarters', location], ['Exchange', exchanges || summary.exchange], ['Founded', founded], ['Employees', workforce], ['Fiscal year end', /^\d{4}$/.test(company.fiscalYearEnd || '') ? new Date('2000-' + company.fiscalYearEnd.slice(0, 2) + '-' + company.fiscalYearEnd.slice(2) + 'T00:00:00Z').toLocaleDateString('en-US', { month: 'long', day: 'numeric', timeZone: 'UTC' }) : null]];
      const dl = node('dl', { class: 'cp-fact-list' });
      for (const [label, value] of items) dl.append(node('div', {}, node('dt', {}, label), node('dd', {}, value || 'Not available')));
      if (website) dl.append(node('div', {}, node('dt', {}, 'Website'), node('dd', {}, sourceLink(website, new URL(website).hostname.replace(/^www\./, '')))));
      const workforceNote = finite(employees) ? (profile?.employees?.basis === 'reported' ? ' Employee count is company-reported.' : ' Employee count follows its attributed source.') : '';
      facts.append(dl, node('p', { class: 'cp-footnote' }, profile?.updated_at ? 'Company context updated ' + dateLabel(profile.updated_at) + '.' + workforceNote : 'Company identity from SEC filings. Additional context appears when sourced.', finite(employees) ? sourceLink(profile?.employees?.source_url, 'Workforce source') : null));
      const text = typeof profile?.description === 'string' ? profile.description.trim() : profile?.description?.text;
      if (text) {
        const paragraphs = text.split(/\n\s*\n|\n/).map(x => x.trim()).filter(Boolean);
        const prose = node('div', { class: 'cp-business-prose' });
        paragraphs.forEach(p => prose.append(node('p', {}, p)));
        description.append(prose);
      } else {
        description.append(node('p', { class: 'cp-business-prose cp-description-empty' }, 'A sourced business description is not available yet. Read the latest annual report for the company’s operations, products, and markets.'));
      }
      const aboutLinks = node('div', { class: 'cp-about-links' });
      const tenK = company.filings?.find(f => /10-K|20-F|40-F/.test(f.f));
      if (tenK && helpers.filingUrl) aboutLinks.append(sourceLink(helpers.filingUrl(ticker, tenK), 'Business in the annual report'));
      const descSource = profile?.description_source?.url || profile?.description_source || profile?.description?.source_url || profile?.source_url;
      if (safeUrl(descSource)) aboutLinks.append(sourceLink(descSource, profile?.description_provider || 'Business description source'));
      if (website) aboutLinks.append(sourceLink(website, 'Company website'));
      description.append(aboutLinks);
      const identity = node('div', { class: 'cp-identity-note' }, node('span', {}, 'SEC registrant'), node('strong', {}, company.name || name), company.cik ? node('span', {}, 'CIK ' + String(company.cik).padStart(10, '0')) : null);
      description.append(identity);
      if (summary.screener_eligible === false) description.append(node('p', { class: 'cp-coverage-note' }, 'Company profile, price history, and filings are available. Screener coverage is still being validated.'));

      const people = (profile?.leadership || []).filter(p => p && typeof p.name === 'string' && p.name.trim() && (p.role || p.title)).slice(0, 16);
      if (!people.length) leadership.append(node('div', { class: 'cp-empty-state' }, node('div', { class: 'cp-empty-icon', 'aria-hidden': 'true' }, '◎'), node('h3', {}, 'Leadership profiles are not available yet'), node('p', {}, 'Names, roles, tenure, and portraits appear here when supported by a source.'), tenK && helpers.filingUrl ? sourceLink(helpers.filingUrl(ticker, tenK), 'View the latest annual report') : null));
      else {
        const groups = new Map();
        for (const person of people) {
          const role = person.role || person.title;
          const boardRole = person.group === 'board' || person.group === 'Board of directors' || /\bboard (?:of directors|member|chair)|\b(?:non[- ]executive|independent) director\b/i.test(role);
          const group = /\bchief executive|\bceo\b/i.test(role) ? 'Executive leadership' : boardRole ? 'Board of directors' : 'Senior leadership';
          if (!groups.has(group)) groups.set(group, []); groups.get(group).push(person);
        }
        for (const group of ['Executive leadership', 'Senior leadership', 'Board of directors']) {
          if (!groups.has(group)) continue;
          const grid = node('div', { class: 'cp-people-grid' });
          for (const person of groups.get(group)) {
            const initials = person.name.split(/\s+/).filter(Boolean).slice(0, 2).map(x => x[0]).join('').toUpperCase();
            const avatar = node('div', { class: 'cp-avatar', 'aria-hidden': 'true' }, initials);
            if (safeUrl(person.photo_url)) {
              const image = node('img', { src: safeUrl(person.photo_url), alt: '', loading: 'lazy', decoding: 'async', referrerpolicy: 'no-referrer' });
              image.addEventListener('error', () => image.remove(), { once: true }); avatar.append(image);
            }
            const profileUrl = safeUrl(person.profile_url || person.linkedin_url);
            const nameNode = profileUrl ? node('a', { href: profileUrl, target: '_blank', rel: 'noopener noreferrer' }, person.name) : node('strong', {}, person.name);
            const observedAt = person.observed_at || profile.updated_at;
            const tenure = tenureLabel(person.start_date || person.job_start_date, observedAt || new Date().toISOString().slice(0, 10));
            const tenureSource = safeUrl(person.start_date_source_url);
            const tenureNode = node(tenureSource ? 'a' : 'span', { class: 'cp-tenure', href: tenureSource, target: tenureSource ? '_blank' : null, rel: tenureSource ? 'noopener noreferrer' : null, title: [observedAt ? 'Role observed ' + dateLabel(observedAt) : 'Role observation date unavailable', person.tenure_note].filter(Boolean).join('. ') }, tenure);
            const credit = person.photo_credit ? node('span', { class: 'cp-photo-credit' }, 'Photo: ' + person.photo_credit, person.photo_license ? [' · ', sourceLink(person.photo_license_url, person.photo_license) || person.photo_license] : null) : null;
            grid.append(node('article', { class: 'cp-person' }, avatar, node('div', { class: 'cp-person-info' }, node('h4', {}, nameNode), node('p', { class: 'cp-person-role' }, person.role || person.title), tenureNode, sourceLink(person.source_url, person.source_name || 'Profile source'), credit)));
          }
          leadership.append(node('div', { class: 'cp-role-group' }, node('h3', {}, group), grid));
        }
        leadership.append(node('p', { class: 'cp-footnote' }, (profile.coverage?.leadership_scope ? profile.coverage.leadership_scope + ' ' : '') + 'Grouped by role; reporting relationships are shown only when explicitly sourced. Tenure runs from the reported role start to the source observation date.' + (profile.updated_at ? ' Profiles checked ' + dateLabel(profile.updated_at) + '.' : ' Observation dates are unavailable.')));
      }
      const validImages = (profile?.images || []).filter(image => safeUrl(image.url) && safeUrl(image.source_url)).slice(0, 3);
      const publicationTime = item => Date.parse(item.published_at || item.date || '') || 0;
      const news = (options.news || []).filter(item => safeUrl(item.url) && item.title).sort((a, b) => publicationTime(b) - publicationTime(a)).slice(0, 4);
      const context = node('div', { class: 'cp-context-grid' });
      validImages.forEach(image => {
        const picture = node('img', { src: safeUrl(image.url), alt: image.caption || image.title || name, loading: 'lazy', decoding: 'async', referrerpolicy: 'no-referrer' });
        picture.addEventListener('error', () => picture.parentElement.classList.add('cp-image-unavailable'), { once: true });
        context.append(node('a', { class: 'cp-context-card cp-with-image', href: safeUrl(image.source_url), target: '_blank', rel: 'noopener noreferrer' }, node('div', { class: 'cp-context-image' }, picture), node('div', { class: 'cp-context-copy' }, node('span', { class: 'cp-context-category' }, image.kind || 'Company context'), node('h3', {}, image.title || image.caption || name), node('span', { class: 'cp-context-meta' }, [new URL(safeUrl(image.source_url)).hostname.replace(/^www\./, ''), image.published_at ? dateLabel(image.published_at) : null].filter(Boolean).join(' · ')), image.credit ? node('span', { class: 'cp-photo-credit' }, 'Image: ' + image.credit) : null)));
      });
      news.slice(0, Math.max(0, 3 - validImages.length)).forEach(item => {
        const published = item.published_at || item.date;
        context.append(node('a', { class: 'cp-context-card', href: safeUrl(item.url), target: '_blank', rel: 'noopener noreferrer' }, node('div', { class: 'cp-context-copy' }, node('span', { class: 'cp-context-category' }, item.source || new URL(safeUrl(item.url)).hostname.replace(/^www\./, '')), node('h3', {}, item.title), node('span', { class: 'cp-context-meta' }, published ? dateLabel(published) : 'Publication date unavailable'), node('span', { class: 'cp-context-arrow', 'aria-hidden': 'true' }, '↗'))));
      });
      if (!context.childNodes.length) {
        const filings = (company.filings || []).filter(f => /10-K|10-Q|8-K|20-F/.test(f.f)).slice(0, 3);
        for (const f of filings) if (helpers.filingUrl) context.append(node('a', { class: 'cp-context-card', href: helpers.filingUrl(ticker, f), target: '_blank', rel: 'noopener noreferrer' }, node('div', { class: 'cp-context-copy' }, node('span', { class: 'cp-context-category' }, 'SEC filing'), node('h3', {}, f.f + (f.r ? ' · Period ended ' + dateLabel(f.r) : ' · Company disclosure')), node('span', { class: 'cp-context-meta' }, 'Filed ' + dateLabel(f.d)), node('span', { class: 'cp-context-arrow', 'aria-hidden': 'true' }, '↗'))));
      }
      recent.append(context.childNodes.length ? context : node('p', { class: 'cp-footnote' }, 'Recent company context is not available in this snapshot.'));
      if (validImages.length) recent.append(node('p', { class: 'cp-footnote' }, 'Images link to their original company or publisher context. Dates identify the event or publication when known.'));
    }
    paintProfile(options.enrichment || null);
    if (!Object.hasOwn(options, 'enrichment')) load(ticker, { dataBase, signal: controller.signal }).then(profile => { if (alive && profile) paintProfile(profile); }).catch(() => {});
    return () => { if (!alive) return; alive = false; controller.abort(); chartCleanup?.(); };
  }
  root.FU_COMPANY_PROFILE = Object.freeze({ render, load, loadHistory, history, tenureLabel, safeUrl });
})(typeof window !== 'undefined' ? window : globalThis);
