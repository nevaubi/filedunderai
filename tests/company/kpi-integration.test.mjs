import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { JSDOM } from 'jsdom';

function setup() {
  const dom = new JSDOM('<main></main>', { url: 'https://research.example/#kpis-arr', runScripts: 'outside-only' });
  const w = dom.window, host = w.document.querySelector('main');
  w.el = (tag, attrs = {}, ...children) => {
    const node = w.document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
      if (value == null || value === false) continue;
      if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
      else node.setAttribute(key, value === true ? '' : String(value));
    }
    for (const child of children.flat()) if (child != null && child !== false) node.append(child.nodeType ? child : String(child));
    return node;
  };
  w.panel = (title, action, ...children) => w.el('section', {}, w.el('h3', {}, title), action, ...children);
  w.SEG = {};
  w.segShow = () => 'all';
  w.segRangeN = () => 100;
  w.fnum = (n, digits = 1) => n.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits });
  w.fmtM = n => String(n / 1e6);
  w.plabel = () => 'Q1 FY27'; // A non-calendar filer: the published KPI basis must win.
  w.openDoc = (ticker, doc, highlight) => { w.opened = { ticker, doc, highlight }; };
  w.IRPUB = 'https://files.example/ir/';
  w.numTokens = () => [];
  w.requestAnimationFrame = callback => callback();
  const app = readFileSync('public/app.js', 'utf8');
  w.eval(app.slice(app.indexOf('function kpiVals('), app.indexOf('/* ---------- IR document library')));
  w.eval(app.slice(app.indexOf('async function docText('), app.indexOf('function colName(')));
  w.eval(app.slice(app.indexOf('function colName('), app.indexOf('async function htmlView(')));
  const peers = readFileSync('public/pages/kpistd.js', 'utf8');
  w.eval(peers.slice(peers.indexOf('  function printedIn('), peers.indexOf('  const numBtn')));
  return { w, host, close: () => w.close() };
}

test('company KPI percentages retain the as-printed scale below one percent', () => {
  const h = setup();
  try {
    for (const unit of ['%', 'pct', 'percent']) {
      assert.equal(h.w.fmtKpi(0.5, unit), '0.5%', unit);
      assert.equal(h.w.fmtKpi(-0.2, unit), '-0.2%', unit);
      assert.equal(h.w.fmtKpi(12.5, unit), '12.5%', unit);
    }
  } finally { h.close(); }
});

test('dollars per barrel retain cents and the source unit in KPI tables', () => {
  const h = setup();
  try {
    assert.equal(h.w.fmtKpi(45.70, 'USD_per_barrel'), '$45.70/bbl');
    assert.equal(h.w.fmtKpi(11.17, 'USD_per_barrel'), '$11.17/bbl');
    assert.equal(h.w.fmtKpi(-0.05, 'USD_per_barrel'), '−$0.05/bbl');
  } finally { h.close(); }
});

test('year-to-date KPI values have a separate table and never replace a quarter', () => {
  const h = setup();
  try {
    h.w.renderKpiGroups(h.host, 'TEST', { groups: [{ category: 'Operating metrics', items: [{
      id: 'orders', name: 'Orders', unit: 'count', values: {
        Q: { '2026-09-30': { v: 25, label: 'Q3 CY26' } },
        Y: { '2026-09-30': { v: 90, label: '9M CY26' } },
      },
    }] }] }, 'Q');
    const panels = [...h.host.querySelectorAll('section')];
    assert.equal(panels.length, 2);
    const ytd = panels.find(p => /Year to date/i.test(p.querySelector('h3').textContent));
    const quarter = panels.find(p => p !== ytd);
    assert.ok(ytd, 'cumulative values are visibly separate');
    assert.match(ytd.querySelector('thead').textContent, /9M CY26/);
    assert.equal(ytd.querySelector('tbody td.c').textContent, '90.0');
    assert.match(quarter.querySelector('thead').textContent, /Q3 CY26/);
    assert.equal(quarter.querySelector('tbody td.c').textContent, '25.0');
    ytd.querySelector('tbody td.c').click();
    assert.match(h.w.opened.highlight.label, /9M CY26/);
    assert.equal(h.w.opened.highlight.value, 90);
  } finally { h.close(); }
});

test('a year-to-date-only KPI remains visible in the company view', () => {
  const h = setup();
  try {
    h.w.renderKpiGroups(h.host, 'TEST', { groups: [{ category: 'Orders', items: [{
      id: 'orders_ytd', name: 'Orders', unit: 'count', values: { Y: { '2026-09-30': { v: 90, label: '9M FY26' } } },
    }] }] }, 'A');
    assert.equal(h.host.querySelectorAll('section').length, 1);
    assert.match(h.host.textContent, /Year to date/);
    assert.match(h.host.textContent, /9M FY26/);
    assert.equal(h.host.querySelector('tbody td.c').textContent, '90.0');
  } finally { h.close(); }
});

test('a KPI source drawer retains the published calendar basis', () => {
  const h = setup();
  try {
    h.w.openKpiSource('TEST', { name: 'Orders', unit: 'count' }, 'Q', '2026-09-30', { v: 25, label: 'Q3 CY26' });
    assert.equal(h.w.opened.highlight.label, 'Orders · Q3 CY26');
  } finally { h.close(); }
});

test('a workbook source opens from KPI peers without visiting its company first', async () => {
  const h = setup();
  try {
    const requests = [];
    h.w.fetch = async url => {
      requests.push(url);
      assert.equal(url, 'https://files.example/ir/TEST/text/workbook.json');
      return { ok: true, json: async () => ({ kind: 'sheets', sheets: { Revenue: [[12.5]] } }) };
    };
    const docs = { workbook: ['Quarterly workbook', 'TEST/workbook.xlsx', '.xlsx', 'https://ir.example/data.xlsx', 'supplement', 'Q3 FY26', 'TEST/text/workbook.json'] };
    h.w.openPoint({ docs }, 'TEST', { name: 'Revenue', unit: 'USD' }, ['2026-09-30', 12500000, 'Q3 FY26', 'workbook', null, '12.5', 'Revenue', 'A1', null, 12500000], 'Q');
    const text = await h.w.docText(h.w.opened.doc);
    assert.equal(text.sheets.Revenue[0][0], 12.5);
    assert.equal(requests.length, 1);
    assert.equal(h.w.opened.highlight.sheet, 'Revenue');
    assert.equal(h.w.opened.highlight.cell, 'A1');
    assert.equal(h.w.opened.doc.url, 'https://ir.example/data.xlsx');
  } finally { h.close(); }
});

test('legacy KPI peer sources resolve the exact workbook from its IR manifest', async () => {
  const h = setup();
  try {
    h.w.getJSON = async path => {
      assert.equal(path, 'ir/TEST.json');
      return { documents: [
        { id: 'other', text_path: 'TEST/text/wrong.json' },
        { id: 'workbook', ext: '.xlsx', storage_path: 'TEST/workbook.xlsx', text_path: 'TEST/text/workbook.json', url: 'https://ir.example/data.xlsx' },
      ] };
    };
    h.w.fetch = async url => {
      assert.equal(url, 'https://files.example/ir/TEST/text/workbook.json');
      return { ok: true, json: async () => ({ kind: 'sheets', sheets: { Revenue: [[12.5]] } }) };
    };
    const docs = { workbook: ['Quarterly workbook', 'TEST/workbook.xlsx', '.xlsx', 'https://ir.example/data.xlsx', 'supplement', 'Q3 FY26'] };
    await h.w.openPoint({ docs }, 'TEST', { name: 'Revenue', unit: 'USD' }, ['2026-09-30', 12500000, 'Q3 FY26', 'workbook', null, '12.5', 'Revenue', 'A1', null, 12500000], 'Q');
    const text = await h.w.docText(h.w.opened.doc);
    assert.equal(text.sheets.Revenue[0][0], 12.5);
    assert.equal(h.w.opened.highlight.cell, 'A1');
  } finally { h.close(); }
});

test('workbook viewer exposes original formulas and source errors without inventing cached values', async () => {
  const h = setup();
  try {
    h.w.fetch = async () => ({ ok: true, json: async () => ({
      kind: 'sheets', sheets: { Financials: [['Percent', 0.125], ['Formula', null], ['Source error', '#DIV/0!']] },
      cell_metadata: { Financials: {
        B1: { data_type: 'n', number_format: '0.0%' },
        B2: { formula: '=SUM(B1,2)', cached: false, number_format: '0.00' },
        B3: { source_error: '#DIV/0!', number_format: 'General' },
      } },
      extraction: { status: 'limited', source_cell_errors: 1, missing_formula_caches: 1 },
    }) });
    const status = h.w.el('div');
    await h.w.sheetView(h.host, { text_path: 'workbook.json' }, { sheet: 'Financials', cell: 'B2' }, status);
    const rows = [...h.host.querySelectorAll('tbody tr')];
    const fraction = rows[0].querySelectorAll('td')[1];
    const uncached = rows[1].querySelectorAll('td')[1];
    const sourceError = rows[2].querySelectorAll('td')[1];
    assert.equal(fraction.textContent, '0.125', 'retain the original raw numeric value');
    assert.match(fraction.title, /0\.0%/);
    assert.equal(uncached.textContent, '', 'a missing formula cache must stay blank, not zero');
    assert.match(uncached.title, /Formula: =SUM\(B1,2\)/);
    assert.match(uncached.title, /missing cached result/i);
    assert.equal(sourceError.textContent, '#DIV/0!');
    assert.match(sourceError.title, /Source error: #DIV\/0!/);
    assert.match(status.textContent, /1 source cell error/);
    assert.match(status.textContent, /1 formula missing a cached result/);
  } finally { h.close(); }
});

test('legacy workbook text keeps the existing viewer fallback without metadata', async () => {
  const h = setup();
  try {
    h.w.fetch = async () => ({ ok: true, json: async () => ({ kind: 'sheets', sheets: { Legacy: [[42, null]] } }) });
    const status = h.w.el('div');
    await h.w.sheetView(h.host, { text_path: 'legacy.json' }, {}, status);
    assert.equal(status.textContent, '1 sheet');
    assert.deepEqual([...h.host.querySelectorAll('tbody td')].map(td => [td.textContent, td.title]), [['42', ''], ['', '']]);
  } finally { h.close(); }
});
