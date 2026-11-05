import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { JSDOM } from 'jsdom';

const fixture = JSON.parse(readFileSync('tests/company/fixtures/cop-percent-evidence.json', 'utf8'));

function setup() {
  const dom = new JSDOM('<aside id="drawer"><header id="dh"></header><div id="db"></div></aside><div id="scrim"></div>',
    { url: 'https://research.example/#company-COP', runScripts: 'outside-only' });
  const w = dom.window;
  w.el = (tag, attrs = {}, ...children) => {
    const node = w.document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
      if (value == null || value === false) continue;
      if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
      else node.setAttribute(key, value === true ? '' : String(value));
    }
    for (const child of children.flat(Infinity)) if (child != null && child !== false) node.append(child.nodeType ? child : String(child));
    return node;
  };
  w.$ = selector => w.document.querySelector(selector);
  w.drawer = w.$('#drawer'); w.scrim = w.$('#scrim'); w.DR = {};
  w.SEG = {}; w.IRPUB = 'https://source.example/ir/';
  w.QMARK = {}; w.DOC_TYPES = {};
  w.hideTip = () => {}; w.closeSource = () => {}; w.svgUse = () => w.el('span');
  w.docTitle = () => 'COP workbook'; w.kpiPeriodLabel = (_tk, end) => end;
  w.fnum = (n, digits = 1) => n.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits });
  w.fmtM = n => '$' + n; w.sheetView = async (_view, _doc, highlight) => { w.sourceHighlight = highlight; };
  const code = readFileSync('public/app.js', 'utf8');
  w.eval(code.slice(code.indexOf('function fmtKpi('), code.indexOf('/* ---------- IR document library')));
  w.eval(code.slice(code.indexOf('function openDoc('), code.indexOf('async function pdfView(')));
  return { w, close: () => w.close() };
}

function sourcePoint(point) {
  return { ...point, ext: '.xlsx', path: 'COP/original.xlsx', text_path: 'COP/text/verified.json',
    url: 'https://issuer.example/original.xlsx', doc_title: 'COP workbook', label: point.period_end };
}

test('all14 reviewed ratio points show printed, cached and normalized precision distinctly', async () => {
  const h = setup();
  try {
    assert.equal(fixture.points.length, 14);
    for (const record of fixture.points) {
      const point = sourcePoint(record), before = JSON.stringify(point);
      await h.w.openKpiSource('COP', { name: 'Debt-to-capital ratio', unit: '%' }, record.period_kind, record.period_end, point);
      const note = h.w.document.querySelector('[data-workbook-percent-evidence]');
      assert.ok(note, `${record.doc} ${record.cell} has its explicit evidence note`);
      const rows = Object.fromEntries([...note.querySelectorAll('dt')].map(dt => [dt.textContent, dt.nextElementSibling.textContent]));
      assert.equal(rows['Workbook display'], record.printed);
      assert.equal(rows['Cached fraction'], String(record.source_cached_value));
      assert.equal(rows['Normalized KPI'], String(record.v) + ' percentage points');
      assert.equal(rows['Original numeric token'], record.raw_numeric_token);
      assert.match(note.textContent, /0%/);
      assert.match(note.textContent, /100/);
      assert.equal(h.w.sourceHighlight.value, record.v, 'highlight retains the unchanged normalized number');
      assert.equal(h.w.sourceHighlight.printed, record.printed);
      assert.equal(JSON.stringify(point), before, 'rendering cannot rewrite point values or metadata');
    }
  } finally { h.close(); }
});

test('the source note does not present the display-rounded number as the workbook print', async () => {
  const h = setup();
  try {
    const point = sourcePoint(fixture.points[0]);
    await h.w.openKpiSource('COP', { name: 'Debt-to-capital ratio', unit: '%' }, 'I', point.period_end, point);
    assert.match(h.w.$('#dh').textContent, /25\.8%/);
    const note = h.w.document.querySelector('[data-workbook-percent-evidence]');
    assert.ok(note);
    assert.match(note.textContent, /Workbook display26%/);
    assert.match(note.textContent, /25\.763601901625083 percentage points/);
  } finally { h.close(); }
});

test('legacy or incomplete evidence keeps generic behavior without inferring a source format', async () => {
  const h = setup();
  try {
    const original = sourcePoint(fixture.points[0]);
    const cases = [
      { ...original, source_number_format: undefined },
      { ...original, source_number_format: '0.0%' },
      { ...original, scale: 1 },
      { ...original, normalization_rule: 'inferred' },
      { ...original, source_cached_value: null },
      { ...original, source_cached_value: true },
      { ...original, raw_numeric_token: '<not a number>' },
      { ...original, source_displayed_value: '27%' },
      { ...original, derived: { formula: 'unreviewed ratio calculation' } },
    ];
    for (const point of cases) {
      await h.w.openKpiSource('COP', { name: 'Ratio', unit: '%' }, 'I', point.period_end, point);
      assert.equal(h.w.document.querySelector('[data-workbook-percent-evidence]'), null);
      assert.equal(h.w.sourceHighlight.value, original.v);
      assert.match(h.w.$('#dh').textContent, /25\.8%/);
    }
    await h.w.openKpiSource('COP', { name: 'Currency', unit: 'USD' }, 'I', original.period_end, original);
    assert.equal(h.w.document.querySelector('[data-workbook-percent-evidence]'), null);
  } finally { h.close(); }
});

test('evidence descriptions are escaped text rather than executable markup', async () => {
  const h = setup();
  try {
    const point = { ...sourcePoint(fixture.points[0]), precision_note: '<img src=x onerror="globalThis.bad=true">' };
    await h.w.openKpiSource('COP', { name: 'Ratio', unit: '%' }, 'I', point.period_end, point);
    const note = h.w.document.querySelector('[data-workbook-percent-evidence]');
    assert.ok(note);
    assert.match(note.textContent, /<img src=x/);
    assert.equal(note.querySelector('img'), null);
    assert.equal(h.w.bad, undefined);
  } finally { h.close(); }
});
