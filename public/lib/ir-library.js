(function (root) {
  'use strict';
  const TYPES = Object.freeze({ release: 'Company release', presentation: 'Presentation', supplement: 'Supplement / data', remarks: 'Prepared remarks / letter', transcript: 'Transcript', annual_report: 'Annual report', other: 'Other' });
  const PAGE_SIZE = 50;
  const count = value => Number.isSafeInteger(value) && value >= 0 ? value : 0;
  const sum = value => ['discovery', 'documents', 'extractions'].reduce((n, key) => n + count(value?.[key]), 0);
  function title(doc) { return String(doc.title || '').split('||')[0].replace(/\(opens in (a )?new (window|tab)\)/gi, '').replace(/\s+/g, ' ').trim() || doc.storage_path?.split('/').pop() || 'Document'; }
  function validDate(value) {
    if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}(?:T.*)?$/.test(value)) return '';
    const day = value.slice(0, 10), time = Date.parse(day + 'T00:00:00Z');
    return Number.isFinite(time) && new Date(time).toISOString().slice(0, 10) === day ? day : '';
  }
  function dateInfo(doc) {
    const published = validDate(doc.published), period = validDate(doc.period_end);
    if (published || period) return { label: published || period, kind: published ? 'Published' : 'Mapped fiscal period end', key: Number((published || period).replaceAll('-', '')) };
    const fiscal = String(doc.fiscal_label || '').trim(), year = fiscal.match(/(?:^|[^0-9])((?:19|20)\d{2})(?!\d)/) || fiscal.match(/\bFY[\s'-]*(\d{2})\b/i), quarter = fiscal.match(/\bQ([1-4])\b/i);
    const fiscalYear = year ? Number(year[1]) + (year[1].length === 2 ? 2000 : 0) : 0;
    // Fiscal labels provide ordering only; they are never presented as actual publication dates.
    return { label: fiscal || 'Date unavailable', kind: fiscal ? 'Fiscal period · publication date unavailable' : '', key: year ? fiscalYear * 10000 + (quarter ? Number(quarter[1]) * 3 : 12) * 100 + 31 : -1 };
  }
  function safeUrl(value) { try { const url = new URL(value); return ['https:', 'http:'].includes(url.protocol) ? url.href : ''; } catch { return ''; } }
  function storedUrl(base, path) {
    const safe = safeUrl(base);
    if (!safe || typeof path !== 'string' || !path || /^(?:\/|\\)|(?:^|[\/\\])\.\.(?:[\/\\]|$)/.test(path)) return '';
    try { const url = new URL(path, safe); return url.href.startsWith(safe) ? url.href : ''; } catch { return ''; }
  }
  function coverageInfo(coverage, documents) {
    let c = coverage && coverage.version === 1 ? coverage : null;
    if (!c) return { label: 'Coverage not yet verified', status: 'unverified', pending: 0, failed: 0, c: null };
    const extraction = { verified: 0, limited: 0, empty: 0, unsupported: 0, failed: 0, unverified: 0 };
    documents.forEach(doc => {
      const status = doc.extraction?.status;
      extraction[Object.hasOwn(extraction, status) ? status : 'unverified']++;
    });
    const countsMatch = Object.keys(extraction).every(key => count(c.extraction?.[key]) === extraction[key]);
    c = { ...c, extraction };
    const pending = sum(c.pending), failed = sum(c.failed);
    const needsRepair = ['limited', 'empty', 'unsupported', 'failed'].some(key => count(extraction[key]) > 0);
    const partial = c.status === 'partial' || pending > 0 || failed > 0 || needsRepair;
    const scopeVerified = c.scope && (c.scope.sec || c.scope.web) && (!c.scope.sec || c.enumeration?.sec === 'complete') && (!c.scope.web || c.enumeration?.web === 'complete');
    const complete = !partial && c.status === 'complete' && scopeVerified && countsMatch && c.total_documents === documents.length && count(extraction.verified) === documents.length && count(extraction.unverified) === 0 && !(c.limits?.length);
    return { label: partial ? 'Backfill incomplete' : complete ? 'Verified within recorded scope' : 'Coverage not yet verified', status: partial ? 'partial' : complete ? 'complete' : 'unverified', pending, failed, c };
  }
  function render(host, ticker, ir, { fileBase = '', openDocument = () => {} } = {}) {
    const document = host.ownerDocument;
    const make = (tag, attrs = {}, ...children) => {
      const node = document.createElement(tag);
      for (const [key, value] of Object.entries(attrs)) {
        if (value == null || value === false) continue;
        if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
        else node.setAttribute(key, value === true ? '' : String(value));
      }
      for (const child of children.flat()) if (child != null && child !== false) node.append(child.nodeType ? child : String(child));
      return node;
    };
    const docs = (Array.isArray(ir?.documents) ? ir.documents : []).filter(doc => doc && typeof doc === 'object').slice();
    docs.sort((a, b) => dateInfo(b).key - dateInfo(a).key || title(a).localeCompare(title(b)) || String(a.id || '').localeCompare(String(b.id || '')));
    const coverage = coverageInfo(ir?.coverage, docs);
    const panel = make('section', { class: 'panel ir-library', 'aria-label': `${ticker} investor relations documents` });
    const search = make('input', { type: 'search', placeholder: 'Search documents…', 'aria-label': 'Search investor relations documents', class: 'ir-search' });
    const type = make('select', { 'aria-label': 'Document type', class: 'ir-type' }, make('option', { value: '' }, 'All document types'));
    const counts = new Map(); docs.forEach(doc => { const kind = doc.doc_type || 'other'; counts.set(kind, (counts.get(kind) || 0) + 1); });
    [...counts.keys()].sort((a, b) => (TYPES[a] || a).localeCompare(TYPES[b] || b)).forEach(kind => type.append(make('option', { value: kind }, `${TYPES[kind] || kind} (${counts.get(kind)})`)));
    panel.append(make('div', { class: 'ir-library-head' }, make('div', {}, make('h3', {}, 'Investor relations'), make('span', { class: 'ir-library-count' }, `${docs.length.toLocaleString()} documents`)), make('div', { class: 'ir-controls' }, search, type)));
    const details = make('details', { class: 'ir-coverage', 'data-status': coverage.status });
    details.append(make('summary', {}, make('span', { class: 'ir-coverage-label' }, coverage.label), make('span', { class: 'ir-coverage-summary' }, [coverage.pending ? `${coverage.pending} pending` : '', coverage.failed ? `${coverage.failed} failed` : ''].filter(Boolean).join(' · ')), make('span', { class: 'ir-coverage-more' }, 'Coverage details')));
    const detailBody = make('div', { class: 'ir-coverage-body' });
    if (!coverage.c) {
      detailBody.append(make('p', {}, 'These are the documents currently collected. Archive enumeration and text quality have not yet been verified; document counts alone do not establish a complete history.'));
    } else {
      const c = coverage.c, scope = c.scope || {}, extraction = c.extraction || {};
      const secScope = scope.sec_since === '1900-01-01' ? 'SEC exhibits across available history' : `SEC exhibits from ${validDate(scope.sec_since) || 'an unrecorded start date'}`;
      const webScope = scope.since_year === 1900 ? 'Available IR website archives' : `IR website archive from ${Number.isInteger(scope.since_year) ? scope.since_year : 'an unrecorded year'}`;
      detailBody.append(make('p', {}, [scope.sec ? secScope : 'SEC discovery not requested', scope.web ? webScope : 'IR website discovery not requested'].join(' · ') + '.'));
      if (scope.history === 'available_archive') detailBody.append(make('p', {}, 'Backfill is set to include the available historical archive. Older or dynamically loaded material may still be undiscovered.'));
      if (scope.web && c.enumeration?.web !== 'complete') detailBody.append(make('p', {}, 'Website archive coverage is unverified. Following the available links does not prove that every historical document was found.'));
      const stats = ['verified', 'limited', 'empty', 'unsupported', 'failed', 'unverified'].map(key => `${count(extraction[key]).toLocaleString()} ${key}`).join(' · ');
      detailBody.append(make('p', {}, `Text extraction: ${stats}.`));
      if (coverage.pending || coverage.failed) detailBody.append(make('p', {}, `Discovery: ${count(c.pending?.discovery)} pending, ${count(c.failed?.discovery)} failed. Documents: ${count(c.pending?.documents)} pending, ${count(c.failed?.documents)} failed. Extraction: ${count(c.pending?.extractions)} pending, ${count(c.failed?.extractions)} failed.`));
      if (coverage.failed) detailBody.append(make('p', {}, 'Retryable failures are also included in pending counts.'));
      detailBody.append(make('p', {}, 'Extraction checks readable text and its location; financial claims still need to be checked against the source.'));
      if (Array.isArray(c.limits) && c.limits.length) detailBody.append(make('ul', {}, c.limits.slice(0, 20).map(limit => make('li', {}, String(limit)))));
      if (validDate(c.updated_at)) detailBody.append(make('p', { class: 'ir-coverage-updated' }, `Coverage checked ${validDate(c.updated_at)}.`));
    }
    details.append(detailBody); panel.append(details);
    const scroll = make('div', { class: 'ir-table-scroll', tabindex: '0', 'aria-label': 'Investor relations document table' });
    const table = make('table', { class: 't ir-table' }, make('thead', {}, make('tr', {}, ['Date / period', 'Document', 'Type', 'File'].map(label => make('th', { scope: 'col' }, label)))));
    const body = make('tbody'); table.append(body); scroll.append(table);
    const empty = make('p', { class: 'ir-empty' });
    const status = make('span', { class: 'ir-range', role: 'status', 'aria-live': 'polite' });
    let page = 0;
    const previous = make('button', { type: 'button', class: 'btn', 'aria-label': 'Previous page', onclick: () => { page--; draw(); } }, 'Previous');
    const next = make('button', { type: 'button', class: 'btn', 'aria-label': 'Next page', onclick: () => { page++; draw(); } }, 'Next');
    const pager = make('div', { class: 'ir-pagination' }, status, make('div', {}, previous, next));
    panel.append(scroll, empty, pager);
    const site = safeUrl(ir?.site), sourceHost = site ? new URL(site).hostname : "the company's IR website";
    panel.append(make('div', { class: 'src ir-library-source' }, `Collected from ${sourceHost} and SEC exhibits. Dates are publication dates when available, otherwise the mapped fiscal period. Open a document to inspect its source.`));
    function draw() {
      const query = search.value.trim().toLocaleLowerCase();
      const filtered = docs.filter(doc => (!type.value || (doc.doc_type || 'other') === type.value) && (!query || [title(doc), doc.fiscal_label, doc.published, doc.period_end, TYPES[doc.doc_type], doc.ext].filter(Boolean).join(' ').toLocaleLowerCase().includes(query)));
      page = Math.min(Math.max(0, page), Math.max(0, Math.ceil(filtered.length / PAGE_SIZE) - 1));
      body.replaceChildren();
      filtered.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE).forEach(doc => {
        const date = dateInfo(doc), name = title(doc), stored = storedUrl(fileBase, doc.storage_path), original = safeUrl(doc.url), size = count(doc.bytes);
        const open = make('button', { type: 'button', class: 'ir-document-title', 'aria-label': `Open document: ${name}`, disabled: !stored && !original && !doc.text_path, onclick: () => openDocument(doc) }, name);
        const kind = (doc.ext || '').replace(/^\./, '').toUpperCase();
        const hostname = original ? new URL(original).hostname : '';
        const origin = hostname === 'sec.gov' || hostname.endsWith('.sec.gov') ? 'SEC exhibit' : 'IR website';
        const textState = { verified: 'Text verified', limited: 'Limited extraction', empty: 'No readable text', unsupported: 'Text format unsupported', failed: 'Extraction failed', unverified: 'Text unverified' }[doc.extraction?.status];
        const meta = make('div', { class: 'ir-document-meta' }, [kind, origin, textState].filter(Boolean).join(' · '));
        const link = stored || original ? make('a', { href: stored || original, target: '_blank', rel: 'noopener noreferrer', 'aria-label': `${stored ? 'Download stored file' : 'Open original source'}: ${name}` }, stored ? 'Download' : 'Original') : make('span', { class: 'faint' }, 'Unavailable');
        const row = make('tr', { 'data-document-id': doc.id || '' },
          make('td', { class: 'ir-document-date' }, make('span', {}, date.label), date.kind ? make('small', {}, date.kind) : null),
          make('td', { class: 'ir-document-name' }, open, meta),
          make('td', { class: 'ir-document-type' }, TYPES[doc.doc_type] || doc.doc_type || TYPES.other),
          make('td', { class: 'ir-document-file' }, link, size ? make('small', {}, size >= 1e6 ? (size / 1e6).toFixed(1) + ' MB' : Math.max(1, Math.round(size / 1e3)) + ' KB') : null));
        body.append(row);
      });
      scroll.hidden = !filtered.length; empty.hidden = !!filtered.length;
      empty.textContent = docs.length ? 'No documents match these filters.' : 'No investor relations documents have been collected for this company yet.';
      status.textContent = filtered.length ? `${page * PAGE_SIZE + 1}–${Math.min((page + 1) * PAGE_SIZE, filtered.length)} of ${filtered.length.toLocaleString()} documents` : '0 documents';
      previous.disabled = page === 0; next.disabled = (page + 1) * PAGE_SIZE >= filtered.length;
    }
    search.addEventListener('input', () => { page = 0; draw(); });
    type.addEventListener('change', () => { page = 0; draw(); });
    draw(); host.append(panel);
    return panel;
  }
  root.FU_IR_LIBRARY = Object.freeze({ render });
})(typeof window !== 'undefined' ? window : globalThis);
