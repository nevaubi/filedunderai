import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { JSDOM } from 'jsdom';

async function renderCoverage(extra = {}) {
  const dom = new JSDOM('<main></main>', { runScripts: 'outside-only' });
  const w = dom.window;
  w.el = (tag, attrs = {}, ...children) => {
    const node = w.document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
      if (value == null || value === false) continue;
      if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
      else node.setAttribute(key, String(value));
    }
    for (const child of children.flat()) if (child != null && child !== false) node.append(child.nodeType ? child : String(child));
    return node;
  };
  w.page = w.document.querySelector('main');
  w.panel = (title, action, ...children) => w.el('section', {}, w.el('h2', {}, title), action, ...children);
  w.srcCard = () => w.el('div');
  w.css = key => ({ '--up': '#008000', '--warn': '#ffa500', '--down': '#ff0000', '--faint': '#808080' })[key];
  w.relTime = value => `as of ${value}`;
  w.Date.now = () => Date.parse('2026-11-05T12:00:00Z');
  w.INDEX = { companies: [] };
  w.MARKET = w.MACRO = w.FEED = {};
  w.getJSON = async path => path === 'meta/coverage_ir.json' ? {
    generated: '2026-11-05T00:00:00Z',
    summary: { segment_tables: 2, companies_with_segments: 1, documents: 3, companies_with_docs: 1,
      bytes: 1024, kpis: 4, kpi_values: 5, companies_with_kpis: 1 },
    ...extra,
  } : {};
  const app = readFileSync('public/app.js', 'utf8');
  w.eval(app.slice(app.indexOf('async function pCoverage('), app.indexOf('async function boot(')));
  await w.pCoverage();
  const row = name => [...w.page.querySelector('table tbody').rows]
    .find(item => item.cells[0]?.textContent.startsWith(name));
  return {
    updated: name => row(name)?.cells[1]?.textContent,
    color: name => row(name)?.querySelector('.status-dot')?.style.background,
    close: () => w.close(),
  };
}

test('Data Status preserves count timestamps and freshness indicators after an IR-only refresh', async () => {
  const h = await renderCoverage({ kpi_counts_as_of: '2026-11-03T10:00:00Z', segment_counts_as_of: '2026-11-01T09:00:00Z' });
  try {
    assert.match(h.updated('KPIs from investor materials'), /as of 2026-11-03T10:00:00Z/);
    assert.match(h.updated('Segment breakdowns'), /as of 2026-11-01T09:00:00Z/);
    assert.match(h.updated('Investor relations documents'), /as of 2026-11-05T00:00:00Z/);
    assert.equal(h.color('KPIs from investor materials'), 'rgb(255, 165, 0)');
    assert.equal(h.color('Segment breakdowns'), 'rgb(255, 0, 0)');
    assert.equal(h.color('Investor relations documents'), 'rgb(0, 128, 0)');
  } finally { h.close(); }
});

test('legacy coverage snapshots use their original generated timestamp when count dates are absent', async () => {
  const h = await renderCoverage();
  try {
    for (const name of ['KPIs from investor materials', 'Segment breakdowns', 'Investor relations documents']) {
      assert.match(h.updated(name), /as of 2026-11-05T00:00:00Z/);
    }
  } finally { h.close(); }
});

test('unknown count timestamps do not inherit the latest IR refresh date', async () => {
  for (const stamp of [null, '', 'not-a-date']) {
    const h = await renderCoverage({ kpi_counts_as_of: stamp, segment_counts_as_of: stamp });
    try {
      for (const name of ['KPIs from investor materials', 'Segment breakdowns']) {
        assert.equal(h.updated(name), '–', `${name}: ${String(stamp)}`);
        assert.equal(h.color(name), 'rgb(128, 128, 128)');
      }
      assert.match(h.updated('Investor relations documents'), /as of 2026-11-05T00:00:00Z/);
    } finally { h.close(); }
  }
});
