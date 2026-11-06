"""Text-rich templates: (A) NVDA Q2 FY27 earnings recap, (B) AI semiconductor KPI scorecard (sector note)."""
import datetime as dt
import json
import statistics as st
import subprocess
import sys
from pathlib import Path

import jinja2

P = Path(__file__).parent
sys.path.insert(0, str(P))
import templates as T  # noqa: E402

D = json.loads((P / 'sample_nvda3.json').read_text())
SEG = json.loads((P / 'sample_raw_segments_NVDA.json').read_text())
CO = json.loads((P / 'sample_co_nvda.json').read_text())
E = json.loads((P / 'sample_est_nvda.json').read_text())
RED = json.loads((P / 'sample_red_nvda.json').read_text())
env = jinja2.Environment(autoescape=False)
B = lambda v: '–' if v is None else f"${v / 1e9:,.1f}B"
B2 = lambda v: '–' if v is None else ('−' if v < 0 else '') + f"${abs(v) / 1e9:,.2f}B"
pct = lambda v, n=1: '–' if v is None else ("+" if v >= 0 else "−") + f"{abs(v) * 100:.{n}f}%"
p0 = lambda v, n=1: '–' if v is None else f"{v * 100:.{n}f}%"
pts_ = lambda v: '–' if v is None else f"{v * 100:+.1f} pts"
xx = lambda v: '–' if v is None else f"{v:.1f}×"
fd = lambda s: dt.date.fromisoformat(s[:10]).strftime('%b %-d, %Y')
d = lambda s: dt.date.fromisoformat(s[:10])
FULL, HALF = 701, 340

R, ends = D['model']['rows'], D['model']['ends']
i2, i1, iy = len(ends) - 1, len(ends) - 2, len(ends) - 5  # Q2 FY27, Q1 FY27, Q2 FY26
rev, gp, oi, ni = (R[k] for k in ('Revenue', 'Gross profit', 'Operating income', 'Net income'))
rd, sga, eps, ocf, capex = R['Research & development'], R['SG&A'], R['EPS diluted'], R['Operating cash flow'], R['Capital expenditures']
opex = [a + b for a, b in zip(rd, sga)]
gm = [g / r for g, r in zip(gp, rev)]
om = [o / r for o, r in zip(oi, rev)]
cf = {r['label']: {x['end']: x['v'] for x in r['quarterly'] if x.get('start') and 80 <= (d(x['end']) - d(x['start'])).days <= 100} for r in CO['statements']['cashflow']}
q2e = ends[i2]
da, sbc, buy, div = (cf[k].get(q2e) for k in ('D&A', 'Stock-based comp', 'Buybacks', 'Dividends paid'))
wc = ocf[i2] - ni[i2] - da - sbc
seg = {r['label']: r for r in next(t for t in SEG['tables'] if t['id'] == 'revenue:Revenues:ProductOrServiceAxis')['rows']}
sv = lambda lab, e: ((seg[lab].get('Q') or {}).get(e) or {}).get('v')
E2, E1, EY = '2026-07-26', '2026-04-26', '2025-07-27'
s_q2 = next(s for s in D['surp'] if s['q'].startswith('2026-07'))
guide_q2 = {(i['metric'], i['basis']): i for i in E['guidance']['items'] if i['period'] == 'Q2 FY27'}
g_rev_q2 = guide_q2[('revenue', 'gaap')]['mid']
g_gm_q2 = guide_q2[('gross_margin', 'gaap')]['mid'] / 100
g_opex_q2 = guide_q2[('opex', 'gaap')]['mid']
g_now = D['guide'][-1]
acts = [a for a in E['actions'] if '2026-08-26' <= a['date'] <= '2026-08-31']
raises = [a for a in acts if a.get('action_pt') == 'Raises']
pt_moves = [a['pt_current'] / a['pt_prior'] - 1 for a in raises if a.get('pt_prior')]
react_q2 = s_q2['r1']
dc_share = sv('Data Center', E2) / rev[i2]

# filing changes from the 10-Q redline (Risk factors), highest-scored first
rf = next(s for s in RED['comparisons'][1]['sections'] if s['id'] == 'item:II-1A')
picks = []
for ch in sorted(rf['changes'], key=lambda x: -(x.get('score') or 0)):
    if ch['kind'] in ('added', 'removed'):
        txt = (ch.get('new') if ch['kind'] == 'added' else ch.get('old')) or ''
    else:
        continue
    if not any(k in txt for k in ('H200', 'AI Diffusion', 'covenants', 'H20')):
        continue
    picks.append({'kind': ch['kind'], 'where': 'Item 1A, Risk factors', 'tags': ', '.join(ch.get('signals') or []), 'text': txt[:430] + ('…' if len(txt) > 430 else '')})
    if len(picks) == 4:
        break
WHY = {0: None}
for p in picks:
    t = p['text']
    if 'H200' in t:
        p['why'] = 'New disclosure: China licences granted for H200 shipments, but PRC restrictions have limited sales. This is the current form of the export-control risk behind the Q1 FY26 charge.'
    elif 'AI Diffusion' in t:
        p['why'] = 'The replacement for the rescinded AI Diffusion rule is still pending, so the licensing regime for data center exports remains open.'
    elif 'covenants' in t:
        p['why'] = 'New debt-covenant risk language, consistent with notes outstanding under an indenture.'
    elif 'H20' in t:
        p['why'] = 'The H20-licence passage from a year ago was dropped, replaced by the H200 language above.'

meta_r = {'title': 'NVIDIA Q2 FY27 earnings recap', 'kind': 'Earnings recap', 'date': fd('2026-08-28'), 'sector': 'Semiconductors', 'company': 'NVIDIA Corporation', 'ticker': 'NVDA', 'exchange': 'Nasdaq'}
first_r = {
    'title': f"Q2 FY27 recap: a {pct(rev[i2] / g_rev_q2 - 1)} revenue beat, a $108B guide and a rare positive reaction",
    'dek': f"Revenue of {B(rev[i2])} beat the {B(g_rev_q2)} guide by {pct(rev[i2] / g_rev_q2 - 1)} and grew {pct(rev[i2] / rev[iy] - 1, 0)} year over year. Q3 was guided to {B(g_now['mid'])}. "
           f"The stock rose {pct(react_q2)} the next day, the first gain after three straight post-report declines, and {len(raises)} of {len(acts)} analyst actions raised targets.",
    'strip': [('Revenue', B(rev[i2]), f"{pct(rev[i2] / rev[iy] - 1, 0)} YoY · guide {B(g_rev_q2)}"), ('EPS, diluted', f"${eps[i2]:.2f}", f"vs ${s_q2['est']:.2f} consensus"),
              ('Gross margin', p0(gm[i2]), f"guide {p0(g_gm_q2)}"), ('Q3 guide', B(g_now['mid']), f"{pct(g_now['mid'] / rev[i2] - 1)} QoQ"),
              ('Next-day move', pct(react_q2), f"{pct(react_q2 - s_q2['spy'])} vs S&P 500")]}

results_tbl = {'head': ['Line item', 'Q2 FY27', 'Guide', 'Consensus', 'vs guide / cons.', 'Q1 FY27', 'Q2 FY26', 'YoY', 'Read'], 'lcols': 1, 'rows': [
    ['Revenue', B(rev[i2]), B(g_rev_q2), '–', {'v': pct(rev[i2] / g_rev_q2 - 1), 'cls': 'n up'}, B(rev[i1]), B(rev[iy]), {'v': pct(rev[i2] / rev[iy] - 1, 0), 'cls': 'n up'},
     {'v': f"Largest dollar beat in the eight-quarter window ({B(rev[i2] - g_rev_q2)} over the midpoint).", 'cls': 'read'}],
    ['Gross margin (GAAP)', p0(gm[i2]), p0(g_gm_q2), '–', pts_(gm[i2] - g_gm_q2), p0(gm[i1]), p0(gm[iy]), pts_(gm[i2] - gm[iy]),
     {'v': 'In line with the guide; flat on Q1. The year-ago comparison is clean of the H20 charge, which fell in Q1 FY26.', 'cls': 'read'}],
    ['Opex (GAAP)', B2(opex[i2]), B2(g_opex_q2), '–', pct(opex[i2] / g_opex_q2 - 1), B2(opex[i1]), B2(opex[iy]), pct(opex[i2] / opex[iy] - 1, 0),
     {'v': f"Slightly under the guide. R&D was {B2(rd[i2])}, {p0(rd[i2] / rev[i2])} of revenue.", 'cls': 'read'}],
    ['Operating income', B(oi[i2]), '–', '–', '–', B(oi[i1]), B(oi[iy]), {'v': pct(oi[i2] / oi[iy] - 1, 0), 'cls': 'n up'}, {'v': f"Operating margin {p0(om[i2])}, up {pts_(om[i2] - om[iy])[1:]} on a year ago.", 'cls': 'read'}],
    ['EPS, diluted', f"${eps[i2]:.2f}", '–', f"${s_q2['est']:.2f}", {'v': pct(s_q2['pct']), 'cls': 'n up'}, f"${eps[i1]:.2f}", f"${eps[iy]:.2f}", {'v': pct(eps[i2] / eps[iy] - 1, 0), 'cls': 'n up'},
     {'v': f"Net income grew only {pct(ni[i2] / ni[i1] - 1)} QoQ against {pct(oi[i2] / oi[i1] - 1)} for operating income; non-operating income was much smaller than in Q1, when net income exceeded operating income.", 'cls': 'read'}],
    ['Operating cash flow', B(ocf[i2]), '–', '–', '–', B(ocf[i1]), B(ocf[iy]), pct(ocf[i2] / ocf[iy] - 1, 0), {'v': f"Fell {pct(ocf[i2] / ocf[i1] - 1, 0)} QoQ; see cash flow below.", 'cls': 'read'}]],
    'rcls': {0: 'tot'}}

seg_rows = []
for lab, short in (('Hyperscale', 'Hyperscale'), ('AI Clouds, Industrial, & Enterprise', 'AI clouds, industrial & enterprise'), ('Data Center', 'Data Center (total)'), ('Edge Computing', 'Edge Computing')):
    a, b, c = sv(lab, E2), sv(lab, E1), sv(lab, EY)
    seg_rows.append([short, B(a), B(b), B(c), pct(a / b - 1), {'v': pct(a / c - 1, 0), 'cls': 'n up'}, p0(a / rev[i2]), B(a - c)])
seg_rows.append(['Total revenue', B(rev[i2]), B(rev[i1]), B(rev[iy]), pct(rev[i2] / rev[i1] - 1), {'v': pct(rev[i2] / rev[iy] - 1, 0), 'cls': 'n up'}, '100%', B(rev[i2] - rev[iy])])
seg_tbl = {'head': ['Reporting line', 'Q2 FY27', 'Q1 FY27', 'Q2 FY26', 'QoQ', 'YoY', 'Share', 'Added YoY'], 'rows': seg_rows, 'rcls': {2: 'med', 4: 'tot'}}
hy, ai_ = (sv('Hyperscale', E2), sv('Hyperscale', E1)), (sv('AI Clouds, Industrial, & Enterprise', E2), sv('AI Clouds, Industrial, & Enterprise', E1))

cash_tbl = {'head': ['Cash flow, Q2 FY27', '$B'], 'rows': [['Net income', B2(ni[i2])], ['+ Depreciation & amortization', B2(da)], ['+ Stock-based compensation', B2(sbc)],
            [{'v': '+ Working capital and other (derived)'}, {'v': B2(wc), 'cls': 'n dn'}], [{'v': '<b>Operating cash flow</b>'}, {'v': f"<b>{B2(ocf[i2])}</b>", 'cls': 'n'}],
            ['− Capital expenditures', B2(capex[i2])], [{'v': '<b>Free cash flow</b>'}, {'v': f"<b>{B2(ocf[i2] - capex[i2])}</b>", 'cls': 'n'}],
            ['Buybacks', B2(buy)], ['Dividends paid', B2(div)]]}

guide_tbl = {'head': ['Q3 FY27 guide', 'Guide', 'Q2 actual', 'Change', 'Q2 guide was'], 'rows': [
    ['Revenue', '$108.0B ±2%', B(rev[i2]), pct(108e9 / rev[i2] - 1), '$91.0B ±2%'], ['Gross margin (GAAP)', '74.0% ±50 bp', p0(gm[i2]), pts_(.74 - gm[i2]), '74.9%'],
    ['Opex (GAAP)', '~$9.2B', B2(opex[i2]), pct(9.2e9 / opex[i2] - 1), '~$8.5B'], ['Opex (non-GAAP)', '~$9.0B', '–', '–', '~$8.3B']]}

act_tbl = {'head': ['Firm', 'Target', 'Change'], 'lcols': 1, 'rows': [[a['firm'], f"${a['pt_prior']:.0f} → ${a['pt_current']:.0f}", {'v': pct(a['pt_current'] / a['pt_prior'] - 1, 0), 'cls': 'n up'}]
                                                                     for a in sorted(raises, key=lambda a: -(a['pt_current'] / a['pt_prior']))[:10]]}

recap_blocks = [
    {'t': 'brief', 'lead': f"NVIDIA beat its own revenue guide by {B(rev[i2] - g_rev_q2)}, held gross margin at the guided level and guided Q3 {pct(g_now['mid'] / rev[i2] - 1)} higher. The quarter's message is breadth: both Data Center customer groups grew more than 60% year over year, and the guide implies the sequential pace holds.",
     'pts': [
         ('Beat', f"Revenue {B(rev[i2])} vs the {B(g_rev_q2)} midpoint ({pct(rev[i2] / g_rev_q2 - 1)}), the sixth beat of 4% or more in eight quarters. EPS ${eps[i2]:.2f} vs ${s_q2['est']:.2f} consensus ({pct(s_q2['pct'])})."),
         ('Mix', f"Hyperscale revenue reached {B(hy[0])} ({pct(hy[0] / hy[1] - 1)} QoQ) and AI clouds, industrial and enterprise {B(ai_[0])} ({pct(ai_[0] / ai_[1] - 1)} QoQ). Data Center was {p0(dc_share)} of revenue."),
         ('Margins', f"Gross margin {p0(gm[i2])} against a {p0(g_gm_q2)} guide; operating margin {p0(om[i2])}, the highest in the nine quarters shown."),
         ('Cash', f"Operating cash flow dropped to {B(ocf[i2])} from {B(ocf[i1])} in Q1 even as net income rose; working capital and other items absorbed about {B(-wc)}. Buybacks were {B(buy)} and dividends {B(div)} after the dividend increase."),
         ('Guide', f"Q3 revenue {B(g_now['mid'])} ±2%, gross margin 74.0% ±50 bp and GAAP opex about $9.2B. The guide is {pct(g_now['mid'] / rev[i2] - 1)} above Q2 and implies {pct(g_now['mid'] / 57006e6 - 1, 0)} year-over-year growth."),
         ('Reaction', f"Shares rose {pct(react_q2)} the next day ({pct(react_q2 - s_q2['spy'])} vs the S&P 500). {len(raises)} of {len(acts)} analyst actions in the following days raised targets, by a median {pct(st.median(pt_moves), 0)}.")]},
    {'t': 'boxes', 'bx': [
        {'k': 'Demand', 'h': f"{pct(sv('Data Center', E2) / sv('Data Center', EY) - 1, 0)} YoY", 'body': f"Data Center revenue of {B(sv('Data Center', E2))}. Hyperscale doubled and AI clouds, industrial and enterprise more than doubled.", 'tone': 'up'},
        {'k': 'Margin', 'h': p0(om[i2]), 'body': f"Operating margin; gross margin held at {p0(gm[i2])} with opex growing {pct(opex[i2] / opex[iy] - 1, 0)} YoY against revenue at {pct(rev[i2] / rev[iy] - 1, 0)}.", 'tone': ''},
        {'k': 'Watch', 'h': f"{B(ocf[i2])} OCF", 'body': f"Cash conversion fell to {p0(ocf[i2] / ni[i2], 0)} of net income from {p0(ocf[i1] / ni[i1], 0)} in Q1. The 10-Q balance sheet is the place to see which working-capital lines moved.", 'tone': 'amber'}]},
    {'t': 'section', 'no': 1, 'title': 'Results against the guide and the Street'},
    {'t': 'exhibit', 'no': 1, 'title': 'Q2 FY27 results vs guidance, consensus and prior periods', 'table': results_tbl, 'src': 'NVIDIA 10-Q (Aug 26, 2026) XBRL; guidance from the May 20, 2026 CFO commentary; Yahoo Finance EPS consensus.'},
    {'t': 'section', 'no': 2, 'title': 'Where the growth came from'},
    {'t': 'text', 'html': f"NVIDIA now splits Data Center into <b>Hyperscale</b> and <b>AI clouds, industrial and enterprise</b>, and reports everything outside the data center as <b>Edge Computing</b>. On that basis the year-over-year gain of {B(rev[i2] - rev[iy])} came almost entirely from Data Center: "
                         f"Hyperscale added {B(hy[0] - sv('Hyperscale', EY))} and AI clouds {B(ai_[0] - sv('AI Clouds, Industrial, & Enterprise', EY))}, while Edge Computing added {B(sv('Edge Computing', E2) - sv('Edge Computing', EY))}. "
                         f"Sequentially the picture is different: Hyperscale grew {pct(hy[0] / hy[1] - 1)} but AI clouds only {pct(ai_[0] / ai_[1] - 1)}, so the quarter's acceleration was concentrated in the largest cloud buyers."},
    {'t': 'exhibit', 'no': 2, 'title': 'Revenue by reporting line', 'table': seg_tbl, 'src': 'NVIDIA 10-Q product and service disclosures (XBRL). Data Center total is the sum of its two customer groups.'},
    {'t': 'split', 'cls': 'two', 'cols': [
        [{'t': 'exhibit', 'no': 3, 'title': f"{B(rev[i2] - rev[iy])} of added revenue in a year", 'sub': 'Bridge, Q2 FY26 to Q2 FY27 ($B)', 'chart': 'bridge', 'w': HALF, 'h': 185, 'src': 'NVIDIA XBRL segment disclosures.'}],
        [{'t': 'exhibit', 'no': 4, 'title': 'Data Center share of revenue', 'sub': 'Quarterly ($B); label = share', 'chart': 'mix', 'w': HALF, 'h': 185, 'src': 'NVIDIA XBRL segment disclosures.'}]]},
    {'t': 'section', 'no': 3, 'title': 'Margins, costs and cash'},
    {'t': 'split', 'cols': [
        [{'t': 'text', 'html': f"Gross margin of <b>{p0(gm[i2])}</b> matched the {p0(g_gm_q2)} guide and was flat on Q1 ({p0(gm[i1])}). Against a year ago it is up {pts_(gm[i2] - gm[iy])[1:]}; the comparison that matters more is the Q1 FY26 trough of {p0(gm[3])}, the quarter of the H20 inventory charge."},
         {'t': 'text', 'html': f"Operating expenses of {B2(opex[i2])} came in under the {B2(g_opex_q2)} guide. R&D grew {pct(rd[i2] / rd[iy] - 1, 0)} year over year to {B2(rd[i2])} but fell as a share of revenue to {p0(rd[i2] / rev[i2])}, which is why operating margin expanded to <b>{p0(om[i2])}</b>."},
         {'t': 'text', 'html': f"Cash is the weak spot in the print. Operating cash flow of <b>{B(ocf[i2])}</b> was less than half of net income, after {B(ocf[i1])} in Q1. Adding back depreciation and stock compensation leaves about <b>{B(-wc)}</b> absorbed by working capital and other items. "
                                f"Shareholder returns did not slow: {B(buy)} of buybacks and {B(div)} of dividends exceeded free cash flow of {B(ocf[i2] - capex[i2])} for the quarter."}],
        [{'t': 'exhibit', 'no': 5, 'title': 'From net income to free cash flow', 'table': cash_tbl, 'src': 'NVIDIA 10-Q cash flow statement; working capital and other is the residual.'}]]},
    {'t': 'exhibit', 'no': 6, 'title': f"Gross margin {p0(gm[i2])}, operating margin {p0(om[i2])}", 'sub': 'Quarterly, % of revenue; shading is operating expense', 'chart': 'margins', 'w': FULL, 'h': 150, 'src': 'NVIDIA XBRL.'},
    {'t': 'section', 'no': 4, 'title': 'Guidance and the Street response'},
    {'t': 'split', 'cols': [
        [{'t': 'exhibit', 'no': 7, 'title': 'Q3 FY27 guidance vs Q2', 'table': guide_tbl, 'src': 'NVIDIA CFO commentary, Aug 26 and May 20, 2026.'},
         {'t': 'text', 'html': f"The {B(g_now['mid'])} guide is {pct(g_now['mid'] / g_rev_q2 - 1)} above the prior quarter's {B(g_rev_q2)} guide and {pct(g_now['mid'] / rev[i2] - 1)} above what NVIDIA actually reported. "
                                "Gross margin is guided down about one point and opex up about 10%, so the guide implies operating margin a little below Q2 unless revenue beats again."}],
        [{'t': 'exhibit', 'no': 8, 'title': f"{len(raises)} target raises in the days after the print", 'table': act_tbl, 'src': 'Benzinga (Powered by Benzinga Data). Largest ten raises shown.'}]]},
    {'t': 'section', 'no': 5, 'title': 'What changed in the 10-Q'},
    {'t': 'text', 'html': f"Filed Under compared the Q2 FY27 10-Q with the Q2 FY26 10-Q paragraph by paragraph. Risk factors had the most substantive changes; the highest-scored are below. Removed text is struck through."},
    {'t': 'redline', 'xs': picks},
    {'t': 'section', 'no': 6, 'title': 'Questions for the Q3 call'},
    {'t': 'qa', 'qs': [
        ('Is the cash flow gap timing or structural?', f"Operating cash flow was {p0(ocf[i2] / ni[i2], 0)} of net income in Q2 against {p0(ocf[i1] / ni[i1], 0)} in Q1. A recovery in Q3 would point to timing; another quarter below net income would suggest NVIDIA is funding customers or inventory at scale."),
        ('Does Hyperscale keep outgrowing AI clouds?', f"Hyperscale grew {pct(hy[0] / hy[1] - 1)} QoQ and AI clouds {pct(ai_[0] / ai_[1] - 1)}. The FY26 10-K shows two direct customers at 22% and 14% of revenue; faster Hyperscale growth raises that concentration."),
        ('How much China is in the guide?', 'The 10-Q says H200 licences were granted but PRC restrictions have limited sales. The answer determines whether China is upside to the $108B guide or excluded from it.'),
        ('Where does opex land in FY28?', f"GAAP opex guided to $9.2B for Q3 after {B2(opex[i2])} in Q2. R&D has grown {pct(rd[i2] / rd[iy] - 1, 0)} in a year; the pace sets how much revenue growth reaches operating income.")]},
    {'t': 'section', 'no': 7, 'title': 'Sources and disclosures'},
    {'t': 'disc', 'html': "<b>Sources.</b> NVIDIA 10-Q filed Aug 26, 2026 and prior filings (XBRL facts, cash flow statement, product disclosures); CFO commentary for guidance; Yahoo Finance consensus (third party); analyst actions Powered by Benzinga Data; filing comparison by Filed Under."},
    {'t': 'disc', 'html': "<b>Disclosures.</b> Sample output for format review. For research purposes only; not investment advice. Verify figures against the cited filings before relying on them."},
]

# ---------------- sector KPI scorecard ----------------
peers = [D['peers'][t] for t in ['NVDA', 'AVGO', 'AMD', 'MU', 'QCOM', 'MRVL', 'INTC', 'TXN', 'ADI'] if t in D['peers']]
names = {'NVDA': 'NVIDIA', 'AVGO': 'Broadcom', 'AMD': 'AMD', 'MU': 'Micron', 'QCOM': 'Qualcomm', 'MRVL': 'Marvell', 'INTC': 'Intel', 'TXN': 'Texas Instruments', 'ADI': 'Analog Devices'}
KEYS = [('g', 'Revenue growth, latest quarter YoY', 'pct', False), ('om', 'Operating margin, latest quarter', 'pct', False), ('gm', 'Gross margin', 'pct', False),
        ('g1', 'Consensus revenue growth, next FY', 'pct', False), ('pe1', 'P/E on next-FY consensus EPS', 'x', True), ('ps1', 'Market cap / next-FY revenue', 'x', True)]
def rank(p, k, asc):
    vs = sorted([q[k] for q in peers if q.get(k) is not None], reverse=not asc)
    return vs.index(p[k]) + 1 if p.get(k) is not None else None
def mid(k):
    return st.median([p[k] for p in peers if p.get(k) is not None])
n = len(peers)
def shade(r):
    if r is None:
        return None
    t = 1 - (r - 1) / (n - 1)
    return f"rgba(43,74,203,{0.06 + 0.42 * t:.2f})"
mx_rows = []
for p in peers:
    row = [f"<b>{p['t']}</b> {names[p['t']]}", B(p['mc'])]
    for k, lab, fmt, asc in KEYS:
        r = rank(p, k, asc)
        v = p.get(k)
        txt = '–' if v is None else (pct(v, 0) if k in ('g', 'g1') else p0(v, 0) if fmt == 'pct' else xx(v))
        row.append({'v': f"{txt} <span style='font-size:6.2pt;color:var(--muted)'>#{r}</span>" if r else txt, 'cls': 'n', 'bg': shade(r), 'dark': False})
    mx_rows.append(row)
mx_rows.append(['Median', B(mid('mc'))] + [{'v': pct(mid(k), 0) if k in ('g', 'g1') else p0(mid(k), 0) if f == 'pct' else xx(mid(k)), 'cls': 'n'} for k, _, f, _ in KEYS])
matrix = {'head': ['Company', 'Mkt cap', 'Rev g LQ', 'Op margin', 'Gross m.', 'Rev g FY+1', 'P/E FY+1', 'P/S FY+1'], 'rows': mx_rows, 'rcls': {0: 'me', n: 'med'}}

def card(p):
    t = p['t']
    pts = []
    rg, ro, rpe = rank(p, 'g', False), rank(p, 'om', False), rank(p, 'pe1', True)
    pts.append(f"Revenue {pct(p['g'], 0)} YoY in the latest quarter, #{rg} of {n}; the group median is {pct(mid('g'), 0)}.")
    if p.get('om') is not None:
        pts.append(f"Operating margin {p0(p['om'], 0)} vs a {p0(mid('om'), 0)} median" + (", the widest in the group." if ro == 1 else "." if ro and ro > 1 else "."))
    if p.get('pe1') and p.get('g1') is not None:
        cheap = p['pe1'] < mid('pe1')
        pts.append(f"{xx(p['pe1'])} next-year EPS with {pct(p['g1'], 0)} consensus revenue growth: {'below' if cheap else 'above'} the {xx(mid('pe1'))} group median.")
    return {'t': t, 'name': names[t], 'rank': f"growth #{rg} · margin #{ro or '–'} · cheapest #{rpe or '–'}", 'me': t == 'NVDA',
            'm': [('Mkt cap', B(p['mc'])), ('Rev TTM', B(p['ttm_rev'])), ('Op m.', p0(p['om'], 0)), ('P/E FY+1', xx(p['pe1']))], 'pts': pts}

nvda = D['peers']['NVDA']
faster = [p['t'] for p in peers if p['g'] > nvda['g']]
cheaper = [p['t'] for p in peers if p.get('pe1') and p['pe1'] < nvda['pe1']]
meta_s = {'title': 'AI semiconductors KPI scorecard', 'kind': 'Sector scorecard', 'date': fd(D['asof']), 'sector': 'Semiconductors', 'company': 'AI and analog semiconductors', 'ticker': f"{n} cos.", 'exchange': 'US listed'}
first_s = {'title': 'Semiconductor scorecard: growth, margins and what the market pays for each',
           'dek': f"Nine chip companies compared on the same computed basis from their own filings. NVIDIA leads on scale; Micron shows the fastest growth and widest margin in its latest quarter, and is the only one cheaper than NVIDIA on next-year earnings.",
           'strip': [('Median growth LQ', pct(mid('g'), 0), 'revenue, YoY'), ('Median op margin', p0(mid('om'), 0), 'latest quarter'), ('Median P/E FY+1', xx(mid('pe1')), 'consensus EPS'),
                     ('Fastest growth', max(peers, key=lambda p: p['g'])['t'], pct(max(p['g'] for p in peers), 0)), ('Widest margin', max([p for p in peers if p.get('om')], key=lambda p: p['om'])['t'], p0(max(p['om'] for p in peers if p.get('om')), 0))]}
sector_blocks = [
    {'t': 'brief', 'lead': 'This scorecard puts the large US-listed chip companies on one basis: revenue, margins and shares from each company’s latest 10-Q or 10-K, market value from the last close, and next-year consensus from Yahoo Finance. It answers three questions: who is growing, who keeps the growth as profit, and what the market pays for both.',
     'pts': [('Growth', f"Median revenue growth was {pct(mid('g'), 0)} in the latest quarter. Memory (MU) and the AI accelerators (NVDA, AVGO) are well above it; Qualcomm shrank {pct(D['peers']['QCOM']['g'], 0)[1:]}."),
             ('Profitability', f"Median operating margin is {p0(mid('om'), 0)}. NVIDIA's {p0(nvda['om'], 0)} and Broadcom's {p0(D['peers']['AVGO']['om'], 0)} are in a different band from AMD ({p0(D['peers']['AMD']['om'], 0)}) and Intel ({p0(D['peers']['INTC']['om'], 0)})."),
             ('Valuation', f"On next-year EPS the group median is {xx(mid('pe1'))}. NVIDIA sits at {xx(nvda['pe1'])}, below AMD ({xx(D['peers']['AMD']['pe1'])}) and the analog names (TXN {xx(D['peers']['TXN']['pe1'])}, ADI {xx(D['peers']['ADI']['pe1'])})."),
             ('Outlier to check', f"Micron's latest quarter shows {pct(D['peers']['MU']['g'], 0)} growth and a {p0(D['peers']['MU']['om'], 0)} operating margin. Those are taken from its filings as extracted; verify against the 10-K before relying on them.")]},
    {'t': 'boxes', 'bx': [{'k': 'How to read the ranks', 'h': '#1 = best', 'body': 'Growth and margin rank highest first; P/E and P/S rank lowest first. Shading darkens with rank so the strongest cells stand out.', 'tone': ''},
                          {'k': 'Basis', 'h': 'Filed figures', 'body': 'Latest fiscal quarter per company. Fiscal calendars differ by up to three months, so the comparison is by most recent report, not by calendar quarter.', 'tone': 'amber'},
                          {'k': 'Not included', 'h': 'TSMC', 'body': 'TSMC files a 20-F rather than 10-Qs, so it is outside the current filing-based universe.', 'tone': 'dn'}]},
    {'t': 'section', 'no': 1, 'title': 'The KPI matrix'},
    {'t': 'exhibit', 'no': 1, 'title': 'Nine semiconductor companies on six measures, ranked', 'table': matrix, 'src': 'Company 10-Q/10-K XBRL; Yahoo Finance consensus (third party); market cap = last close × latest diluted shares. Filed Under.'},
    {'t': 'section', 'no': 2, 'title': 'Growth, profit and price side by side'},
    {'t': 'grid', 'cells': [
        {'t': 'exhibit', 'no': 2, 'title': 'Revenue growth, latest quarter', 'chart': 'rankbar', 'w': HALF, 'h': 175, 'attrs': {'key': 'g', 'fmt': 'pct'}, 'src': 'Company filings.'},
        {'t': 'exhibit', 'no': 3, 'title': 'Operating margin, latest quarter', 'chart': 'rankbar', 'w': HALF, 'h': 175, 'attrs': {'key': 'om', 'fmt': 'pct'}, 'src': 'Company filings.'},
        {'t': 'exhibit', 'no': 4, 'title': 'P/E on next-year consensus EPS', 'chart': 'rankbar', 'w': HALF, 'h': 175, 'attrs': {'key': 'pe1', 'fmt': 'x', 'asc': '1'}, 'src': 'Yahoo Finance consensus; last close.'},
        {'t': 'exhibit', 'no': 5, 'title': 'What the market pays for expected growth', 'sub': 'Next-FY revenue growth vs next-FY P/E; bubble = market cap', 'chart': 'peerval', 'w': HALF, 'h': 175, 'src': 'Yahoo Finance consensus; company filings.'}]},
    {'t': 'section', 'no': 3, 'title': 'Company by company'},
    {'t': 'cards', 'cs': [card(p) for p in peers]},
    {'t': 'section', 'no': 4, 'title': 'Method, sources and disclosures'},
    {'t': 'disc', 'html': "<b>Method.</b> Quarter values come from three-month XBRL contexts; growth compares with the quarter about one year earlier. Market cap uses the latest close and the latest reported diluted share count. Forward multiples use Yahoo Finance consensus for the next fiscal year, which differs by company fiscal calendar. Commentary on each card is generated from the figures shown."},
    {'t': 'disc', 'html': "<b>Disclosures.</b> Sample output for format review. For research purposes only; not investment advice. Verify figures against the cited filings before relying on them."},
]


def keep(blocks):
    out, i = [], 0
    while i < len(blocks):
        if blocks[i].get('t') == 'section' and i + 1 < len(blocks) and blocks[i + 1].get('t') != 'section':
            out.append({'t': 'keep', 'kids': [blocks[i], blocks[i + 1]]}); i += 2
        else:
            out.append(blocks[i]); i += 1
    return out


def render(name, meta, first, blocks, out):
    html = env.from_string(T.BODY).render(css=(P / 'report.css').read_text(), charts=(P / 'charts2.js').read_text(), data_json=json.dumps(D), meta=dict(meta, density='flow'), first=first,
                                          pages=[{'head': '', 'blocks': keep(blocks)}])
    (P / f'{name}.html').write_text(html)
    subprocess.run(['node', str(P / 'pdf.js'), str(P / f'{name}.html'), str(P / out), 'footer', meta['title']], check=True)


render('recap', meta_r, first_r, recap_blocks, 'NVDA-Q2FY27-earnings-recap.pdf')
render('sector', meta_s, first_s, sector_blocks, 'AI-semis-KPI-scorecard.pdf')
print('ok')
