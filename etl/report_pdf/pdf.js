const { chromium } = require('playwright');
const [html, out, mode, title] = process.argv.slice(2);
(async () => {
  const b = await chromium.launch(); const p = await b.newPage(); const errs = [];
  p.on('pageerror', e => errs.push(e.message)); p.on('console', m => { if (m.type() === 'error') errs.push(m.text()); });
  await p.goto('file://' + html, { waitUntil: 'networkidle' });
  await p.evaluate(() => document.fonts.ready); await p.waitForTimeout(400);
  const opt = { path: out, format: 'Letter', printBackground: true };
  if (mode === 'footer') Object.assign(opt, { preferCSSPageSize: true, displayHeaderFooter: true, headerTemplate: '<span></span>',
    footerTemplate: `<div style="font-family:Helvetica,Arial,sans-serif;font-size:6.5pt;color:#959BA6;width:100%;padding:0 0.6in;display:flex;justify-content:space-between"><span>Filed Under Research · ${title} · For research purposes only, not investment advice</span><span><span class="pageNumber"></span> / <span class="totalPages"></span></span></div>` });
  else { await p.addStyleTag({ content: '@page { margin: 0 }' }); opt.margin = { top: 0, right: 0, bottom: 0, left: 0 }; }
  await p.pdf(opt);
  if (errs.length) console.log('errors', errs);
  await b.close();
})();
