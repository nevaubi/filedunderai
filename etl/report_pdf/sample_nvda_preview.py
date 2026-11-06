"""Sample report build (NVDA Q3 FY27 preview) used to iterate the PDF template. Writes PDFs next to this file."""
import datetime as dt
import json
import statistics as st
import subprocess
import sys
from pathlib import Path

import jinja2
from pypdf import PdfReader, PdfWriter

sys.path.insert(0, str(Path(__file__).parent))
import templates as T  # noqa: E402

P = Path(__file__).parent
D = json.loads((P / 'sample_nvda.json').read_text())
env = jinja2.Environment(autoescape=False)

B = lambda v: f"${v / 1e9:,.1f}B"
pct = lambda v, n=1: ("+" if v >= 0 else "−") + f"{abs(v) * 100:.{n}f}%"
fd = lambda s: dt.date.fromisoformat(s[:10]).strftime('%b %-d, %Y')
q0, q1, y0, y1 = (D['cons'][k] for k in ('0q', '+1q', '0y', '+1y'))
g_now = D['guide'][-1]
hist = [g for g in D['guide'] if g['act']]
beats = [g['act'] / g['mid'] - 1 for g in hist]
avg_beat = st.mean(beats)
implied = g_now['mid'] * (1 + avg_beat)
pts = sorted(t['pt'] for t in D['targets'])
med_pt = st.median(pts)
price = D['price']['close']
react, surp, last = D['react'], D['surp'], D['rev'][-1]
mabs = st.mean(abs(r['cc']) for r in react)
ups = sum(r['cc'] > 0 for r in react)
fell = sum(1 for s in surp if s['r1'] < 0)
s90, rat = D['summary']['d90'], D['ratings']
fwd27, fwd28 = price / y0['eps']['avg'], price / y1['eps']['avg']
rev_ttm = sum(x['rev'] for x in D['rev'][-4:])
SRC_XBRL = "NVIDIA 10-Q and 10-K XBRL filings (SEC EDGAR); Filed Under calculations."

meta = {'title': 'NVIDIA Q3 FY27 earnings preview', 'kind': 'Earnings preview', 'date': fd(D['asof']), 'sector': 'Semiconductors',
        'company': 'NVIDIA Corporation', 'ticker': 'NVDA', 'exchange': 'Nasdaq'}
first = {
    'title': 'Q3 FY27 preview: the Street sits just above a guide NVIDIA has beaten eight straight times',
    'dek': f"Consensus revenue of {B(q0['revenue']['avg'])} is {pct(q0['revenue']['avg'] / g_now['mid'] - 1)} above the {B(g_now['mid'])} guided midpoint. "
           f"Over the last eight quarters NVIDIA landed {pct(min(beats))} to {pct(max(beats))} above its own midpoint, an average of {pct(avg_beat)}.",
    'strip': [
        ('Price', f"${price:.2f}", f"close {D['price']['date']}"),
        ('Q3 guide', B(g_now['mid']), f"±2% · {fd(g_now['issued'])}"),
        ('Q3 consensus', B(q0['revenue']['avg']), f"{int(q0['revenue']['n'])} estimates"),
        ('Avg beat vs guide', pct(avg_beat), f"last {len(hist)} quarters"),
        ('Avg next-day move', f"±{mabs * 100:.1f}%", f"last {len(react)} reports"),
    ],
}

kp = {'t': 'kp', 'title': 'Key points', 'pts': [
    ('Guide vs Street', f"Management guided Q3 revenue to {B(g_now['mid'])} ±2% ({B(g_now['lo'])} to {B(g_now['hi'])}) on {fd(g_now['issued'])}. The {int(q0['revenue']['n'])}-estimate consensus is {B(q0['revenue']['avg'])}, with a range of {B(q0['revenue']['low'])} to {B(q0['revenue']['high'])}."),
    ('Beat history', f"Applying the eight-quarter average beat to the midpoint gives about {B(implied)}, {pct(implied / q0['revenue']['avg'] - 1)} above consensus. The smallest beat in the window, {pct(min(beats))}, would still land at {B(g_now['mid'] * (1 + min(beats)))}."),
    ('EPS', f"Consensus is ${q0['eps']['avg']:.2f} ({int(q0['eps']['n'])} estimates), up {pct(q0['eps']['avg'] / q0['eps']['year_ago'] - 1, 0)} from ${q0['eps']['year_ago']:.2f} a year ago. NVIDIA beat EPS consensus by {pct(min(s['pct'] for s in surp))} to {pct(max(s['pct'] for s in surp))} in each of the last four quarters."),
    ('Reaction risk', f"Despite those beats the stock fell the next day after {fell} of the last {len(surp)} reports. Across the last {len(react)} reports the average next-day move was ±{mabs * 100:.1f}%, up {ups} times."),
    ('Positioning', f"{rat['buy']} of {sum(rat.values())} rated firms are at Buy. In the last 90 days there were {s90['target_raises']} target raises and {s90['target_cuts']} cut; the median published target is ${med_pt:.0f} against a ${price:.2f} close."),
]}
side = [
    {'t': 'table', 'title': 'Snapshot', 'rows': [
        ['52-week range', f"${D['l52']:.0f}–${D['h52']:.0f}"], ['Return YTD / 1Y', f"{pct(D['ret_ytd'])} / {pct(D['ret_1y'])}"],
        ['Market cap', f"${D['mcap_est'] / 1e12:.2f}T"], ['Revenue TTM', B(rev_ttm)],
        ['Gross / op margin LQ', f"{last['gm'] * 100:.1f}% / {last['om'] * 100:.1f}%"], ['P/E FY27E / FY28E', f"{fwd27:.1f}× / {fwd28:.1f}×"], ['Beta (1Y)', f"{D['beta']:.2f}"]]},
    {'t': 'table', 'title': 'Consensus', 'head': ['', 'Q3 FY27', 'FY27'], 'rows': [
        ['Revenue', B(q0['revenue']['avg']), B(y0['revenue']['avg'])],
        ['YoY', pct(q0['revenue']['avg'] / q0['revenue']['year_ago'] - 1, 0), pct(y0['revenue']['avg'] / y0['revenue']['year_ago'] - 1, 0)],
        ['EPS', f"${q0['eps']['avg']:.2f}", f"${y0['eps']['avg']:.2f}"], ['Estimates', int(q0['revenue']['n']), int(y0['revenue']['n'])],
        ['Next Q revenue', {'v': B(q1['revenue']['avg']), 'cls': 'n', 'span': 2}]]},
]
guide_tbl = {'head': ['Quarter', 'Guided range', 'Midpoint', 'Reported', 'vs mid'], 'rows': [
    [g['p'], f"{B(g['lo'])}–{B(g['hi'])[1:]}", B(g['mid']), B(g['act']) if g['act'] else '–',
     {'v': pct(g['act'] / g['mid'] - 1) if g['act'] else 'Pending', 'cls': 'n up' if g['act'] else 'n'}] for g in reversed(D['guide'])]}
surp_tbl = {'head': ['Quarter', 'Est.', 'Actual', 'Surprise', 'Revenue', 'Next day', 'vs SPY'], 'rows': [
    [s['q'][:7], f"${s['est']:.2f}", f"${s['act']:.2f}", {'v': pct(s['pct']), 'cls': 'n up'}, B(s['rev']),
     {'v': pct(s['r1']), 'cls': 'n up' if s['r1'] > 0 else 'n dn'}, pct(s['r1'] - s['spy'])] for s in reversed(surp)]}
act_tbl = {'head': ['Date', 'Firm', 'Action', 'Rating', 'Target'], 'lcols': 4, 'rows': [
    [a['date'], a['firm'], a['action'], a['r'] or '',
     (f"${a['pt0']:.0f} → " if a['pt0'] and a['pt0'] != a['pt1'] else '') + (f"${a['pt1']:.0f}" if a['pt1'] else '–')] for a in D['acts'][:9]]}

FULL, HALF = 701, 336
sections = ['Summary', 'Guidance track record', 'Street positioning', 'Margins and peers', 'Sources and disclosures']
pages = [
    {'head': 'Summary', 'blocks': [
        {'t': 'cols', 'main': [kp], 'side': side},
        {'t': 'exhibit', 'no': 1, 'title': f"Revenue reached {B(last['rev'])} last quarter, up {pct(last['yoy'], 0)} year over year",
         'sub': 'Quarterly revenue ($B, bars) and YoY growth (line, right axis). The last bar is the Q3 FY27 guided midpoint with its ±2% range.',
         'chart': 'rev', 'w': FULL, 'h': 205, 'src': "NVIDIA 10-Q and 10-K XBRL filings (SEC EDGAR); Q3 FY27 guidance from the Aug 26, 2026 CFO commentary. Filed Under calculations."}]},
    {'head': 'Guidance and estimates', 'blocks': [
        {'t': 'section', 'no': 2, 'title': 'Guidance track record',
         'lede': f"NVIDIA gives one revenue figure with a ±2% band each quarter. Reported revenue came in above the top of that band in {sum(1 for g in hist if g['act'] > g['hi'])} of the last {len(hist)} quarters. Beats narrowed to {pct(min(beats))} in Q1 FY26, the quarter hit by the April 2025 H20 export restrictions, and have run between {pct(min(beats[-4:]))} and {pct(max(beats[-4:]))} since."},
        {'t': 'exhibit', 'no': 2, 'title': 'Reported revenue has cleared the guided midpoint every quarter since Q3 FY25', 'sub': 'Reported revenue vs guided range ($B). Label shows the beat vs the guided midpoint.',
         'chart': 'guide', 'w': FULL, 'h': 195, 'src': 'guidance from NVIDIA CFO commentary and 8-K exhibits as extracted by Filed Under; reported revenue from 10-Q and 10-K XBRL.'},
        {'t': 'two', 'cols': [
            [{'t': 'exhibit', 'no': 3, 'title': 'Guidance vs reported revenue', 'table': guide_tbl, 'src': 'company guidance and filings; Filed Under.'}],
            [{'t': 'exhibit', 'no': 4, 'title': 'EPS surprises and next-day reaction', 'table': surp_tbl, 'src': 'consensus from Yahoo Finance (third party); actuals from SEC filings; daily prices.'},
             {'t': 'callout', 'title': 'What to watch', 'html': f"Beats of around 5% met flat to negative next-day moves in three of the last four reports. The bar for a positive reaction looks closer to the top of the consensus range ({B(q0['revenue']['high'])}) and to a Q4 guide above the {B(q1['revenue']['avg'])} next-quarter consensus."}]]}]},
    {'head': 'Street and stock reaction', 'blocks': [
        {'t': 'section', 'no': 3, 'title': 'Street positioning',
         'lede': f"Coverage is close to uniformly positive: {rat['buy']} Buy-equivalent ratings and {rat['hold']} Hold among firms with a rating in the last year, and no downgrades in 90 days. Published targets range from ${pts[0]:.0f} to ${pts[-1]:.0f}."},
        {'t': 'two', 'cols': [
            [{'t': 'exhibit', 'no': 5, 'title': 'Published price targets vs the last close', 'sub': 'Most recent target per firm, last 12 months ($)', 'chart': 'targets', 'w': HALF, 'h': 170, 'src': 'Benzinga analyst ratings (Powered by Benzinga Data).'}],
            [{'t': 'exhibit', 'no': 6, 'title': 'Recent analyst actions', 'table': act_tbl, 'src': 'Benzinga analyst ratings (Powered by Benzinga Data).'}]]},
        {'t': 'exhibit', 'no': 7, 'title': f"The stock has moved ±{mabs * 100:.1f}% on average the day after results", 'sub': f"Close-to-close move on the first trading day after each results release, last {len(react)} reports",
         'chart': 'react', 'w': FULL, 'h': 165, 'src': '8-K Item 2.02 acceptance times (SEC EDGAR); daily prices; Filed Under event study.'}]},
    {'head': 'Margins and peers', 'blocks': [
        {'t': 'section', 'no': 4, 'title': 'Margins and peer context',
         'lede': f"Gross margin has held near {last['gm'] * 100:.0f}% for four quarters and operating margin reached {last['om'] * 100:.1f}% last quarter. Among IT companies above $5B market cap, only NVIDIA combines triple-digit revenue growth with an operating margin above 60%. Broadcom is closest at 86% growth and a 54% margin."},
        {'t': 'two', 'cols': [
            [{'t': 'exhibit', 'no': 8, 'title': 'Gross and operating margin', 'sub': 'Quarterly, % of revenue', 'chart': 'margins', 'w': HALF, 'h': 175, 'src': SRC_XBRL}],
            [{'t': 'exhibit', 'no': 9, 'title': 'Growth and margin vs large IT peers', 'sub': 'Latest quarter, IT companies above $5B market cap', 'chart': 'peers', 'w': HALF, 'h': 175,
              'src': 'company filings; market data; Filed Under. Dashed lines are peer medians. NVIDIA market cap is last close times diluted shares.'}]]},
        {'t': 'section', 'no': 5, 'title': 'Sources and disclosures'},
        {'t': 'disc', 'html': f"<b>Sources.</b> Reported figures come from XBRL facts in NVIDIA's 10-Q and 10-K filings on SEC EDGAR, quarter values from three-month contexts. Guidance is extracted from CFO commentary and 8-K exhibits and stored with the quoted sentence and page. Consensus estimates are from Yahoo Finance as of {D['cons_asof'][:10]} and are third-party data, not company figures. Analyst ratings and targets are Powered by Benzinga Data. Next-day reactions use the first close after the 8-K acceptance time. Every figure links to its stored source record in the Filed Under app."},
        {'t': 'disc', 'html': "<b>Disclosures.</b> This document is a sample generated by Filed Under to review report format and styling. It is for research purposes only and is not investment advice, a recommendation or an offer to buy or sell any security. Figures are believed accurate as of the dates shown and may change; verify against the cited filings before relying on them."}]},
]

cover = {
    'tier': 'Premium research', 'title': 'NVIDIA: the guide, the beat and the reaction',
    'dek': first['dek'], 'note': 'Filed Under Research. Figures from SEC filings, company guidance, Yahoo Finance consensus and Benzinga analyst data. Not investment advice.',
    'facts': [('Price', f"${price:.2f}"), ('Q3 guide', B(g_now['mid'])), ('Consensus', B(q0['revenue']['avg'])), ('Median target', f"${med_pt:.0f}")],
    'toc': [(s, str(i)) for s, i in zip(sections, [1, 2, 3, 4, 4])],
}


def render(name, tpl, footer, **ctx):
    html = env.from_string(tpl).render(css=(P / 'report.css').read_text(), charts=(P / 'charts.js').read_text(), data_json=json.dumps(D), meta=meta, **ctx)
    (P / f'{name}.html').write_text(html)
    subprocess.run(['node', str(P / 'pdf.js'), str(P / f'{name}.html'), str(P / f'{name}.pdf'), 'footer' if footer else 'none', meta['title']], check=True)
    return P / f'{name}.pdf'


body = render('body', T.BODY, True, first=first, pages=pages)
cov = render('cover', T.COVER, False, cover=cover)
std = P / 'NVDA-Q3FY27-preview-standard.pdf'
std.write_bytes(body.read_bytes())
w = PdfWriter()
for f in (cov, body):
    for pg in PdfReader(str(f)).pages:
        w.add_page(pg)
with open(P / 'NVDA-Q3FY27-preview-premium.pdf', 'wb') as fh:
    w.write(fh)
print('ok')
