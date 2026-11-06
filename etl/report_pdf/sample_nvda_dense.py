"""Dense desk-note variant: NVDA Q3 FY27 pre-earnings review. Continuous flow, small type, table-heavy."""
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
env = jinja2.Environment(autoescape=False)
B = lambda v: '–' if v is None else f"${v / 1e9:,.1f}B"
Bn = lambda v: '–' if v is None else f"{v / 1e9:,.1f}"
pct = lambda v, n=1: '–' if v is None else ("+" if v >= 0 else "−") + f"{abs(v) * 100:.{n}f}%"
p0 = lambda v, n=1: '–' if v is None else f"{v * 100:.{n}f}%"
xx = lambda v: '–' if v is None else f"{v:.1f}×"
fd = lambda s: dt.date.fromisoformat(s[:10]).strftime('%b %-d, %Y')
mon = lambda s: dt.date.fromisoformat(s).strftime("%b '%y")
FULL, HALF = 701, 340

cons = {p['code']: p for p in D['cons_periods']}
q0, q1, y0, y1 = cons['0q'], cons['+1q'], cons['0y'], cons['+1y']
g_now = D['guide'][-1]
hist = [g for g in D['guide'] if g['act']]
beats = [g['act'] / g['mid'] - 1 for g in hist]
avg_beat = st.mean(beats)
price = D['price']['close']
react, surp, last = D['react'], D['surp'], D['rev'][-1]
mabs = st.mean(abs(r['cc']) for r in react)
pts = sorted(t['pt'] for t in D['targets'])
med_pt = st.median(pts)
R, ends = D['model']['rows'], D['model']['ends']
rat = D['ratings']
nv = D['peers']['NVDA']
pe_vals = sorted(x['pe'] for x in D['pe_hist'])
pe_now = D['pe_hist'][-1]['pe']
pe_med = st.median(pe_vals)
pe_z = (pe_now - st.mean(pe_vals)) / st.pstdev(pe_vals)
opex_lq = R['Research & development'][-1] + R['SG&A'][-1]
fell = sum(1 for s in surp if s['r1'] < 0)
disp = (q0['revenue']['high'] - q0['revenue']['low']) / q0['revenue']['avg']
ttm_fcf = sum((o or 0) - (c or 0) for o, c in zip(R['Operating cash flow'][-4:], R['Capital expenditures'][-4:]))
gm_guides = [i for i in [] if i]

meta = {'title': 'NVIDIA Q3 FY27 pre-earnings review', 'kind': 'Pre-earnings review', 'date': fd(D['asof']), 'sector': 'Semiconductors',
        'company': 'NVIDIA Corporation', 'ticker': 'NVDA', 'exchange': 'Nasdaq', 'density': 'dense'}
first = {'title': 'Q3 FY27: an eighth straight beat is the base case; the Q4 guide decides the reaction',
         'dek': f"Consensus revenue of {B(q0['revenue']['avg'])} sits {pct(q0['revenue']['avg'] / g_now['mid'] - 1)} above the {B(g_now['mid'])} guide. NVIDIA has beaten its own midpoint by "
                f"{pct(min(beats))} to {pct(max(beats))} for eight quarters, yet the stock fell after {fell} of the last {len(surp)} reports. The read is on Q4: consensus wants {B(q1['revenue']['avg'])}.",
         'strip': []}

metrics = {'t': 'metrics', 'n': 8, 'its': [
    ('Price', f"${price:.2f}", D['price']['date']), ('Market cap', f"${nv['mc'] / 1e12:.2f}T", 'close × diluted shares'),
    ('Q3 rev guide', B(g_now['mid']), '±2%, Aug 26'), ('Q3 cons. revenue', B(q0['revenue']['avg']), f"{int(q0['revenue']['n'])} est."),
    ('Q3 cons. EPS', f"${q0['eps']['avg']:.2f}", f"{int(q0['eps']['n'])} est."), ('Q4 cons. revenue', B(q1['revenue']['avg']), f"{pct(q1['revenue']['avg'] / q0['revenue']['avg'] - 1)} QoQ"),
    ('Avg beat vs guide', pct(avg_beat), f"{len(hist)} quarters"), ('Avg move after', f"±{mabs * 100:.1f}%", f"next day, {len(react)} reports"),
    ('YTD / 1Y', f"{pct(D['ret_ytd'], 0)} / {pct(D['ret_1y'], 0)}", 'price return'), ('52-week range', f"${D['l52']:.0f}–${D['h52']:.0f}", f"{pct(price / D['h52'] - 1, 0)} from high"),
    ('P/E TTM', xx(nv['pe_ttm']), f"2-yr median {pe_med:.0f}×"), ('Fwd P/E 27E / 28E', f"{nv['pe0']:.1f} / {nv['pe1']:.1f}×", 'consensus EPS'),
    ('FCF TTM', B(ttm_fcf), 'OCF less capex'), ('Ratings', f"{rat['buy']} / {rat['hold']} / {rat['sell']}", 'buy / hold / sell'),
    ('Median target', f"${med_pt:.0f}", f"{pct(med_pt / price - 1, 0)} vs close"), ('Beta (1Y)', f"{D['beta']:.2f}", 'vs S&P 500')]}

secnav = {'t': 'secnav', 'its': ['Setup', 'Expectations', 'Beat and reaction history', 'Financial model', 'Valuation and peers', 'Scenarios', 'Sources']}

setup_facts = {'t': 'facts', 'title': 'Setup factors', 'table': {'head': ['Factor', 'Reading'], 'lcols': 2, 'rows': [
    ['Street vs guide', f"Consensus {pct(q0['revenue']['avg'] / g_now['mid'] - 1)} above midpoint"],
    ['Beat history', f"{len(hist)} of {len(hist)} above midpoint; avg {pct(avg_beat)}"],
    ['Estimate spread', f"High–low {disp * 100:.0f}% of consensus"],
    ['Reaction history', f"Fell after {fell} of last {len(surp)}"],
    ['Positioning', f"{rat['buy']} of {sum(rat.values())} at Buy"],
    ['Valuation', f"{pe_now:.0f}× TTM vs {pe_med:.0f}× 2-yr median"]]}}
guide_facts = {'t': 'facts', 'title': 'Q3 FY27 guidance (Aug 26, 2026)', 'table': {'head': ['Item', 'Guide', 'Q2 actual'], 'rows': [
    ['Revenue', '$108.0B ±2%', B(R['Revenue'][-1])], ['Gross margin (GAAP)', '74.0% ±50 bp', p0(R['Gross profit'][-1] / R['Revenue'][-1])],
    ['Opex (GAAP)', '~$9.2B', B(opex_lq)], ['Opex (non-GAAP)', '~$9.0B', '–']]}, 'note': 'Source: NVIDIA CFO commentary, quoted sentences stored by Filed Under.'}

exp_tbl = {'head': ['Metric', 'Guide', 'Consensus', 'Cons. high / low', 'Year-ago', 'Implied YoY', 'Q2 actual'], 'rows': [
    ['Q3 revenue', '$108.0B', B(q0['revenue']['avg']), f"{B(q0['revenue']['high'])} / {B(q0['revenue']['low'])}", B(q0['revenue']['year_ago']), {'v': pct(q0['revenue']['avg'] / q0['revenue']['year_ago'] - 1), 'cls': 'n up'}, B(R['Revenue'][-1])],
    ['Q3 EPS (diluted)', '–', f"${q0['eps']['avg']:.2f}", f"${q0['eps']['high']:.2f} / ${q0['eps']['low']:.2f}", f"${q0['eps']['year_ago']:.2f}", {'v': pct(q0['eps']['avg'] / q0['eps']['year_ago'] - 1), 'cls': 'n up'}, f"${R['EPS diluted'][-1]:.2f}"],
    ['Q3 gross margin', '74.0%', '–', '–', p0(R['Gross profit'][-4] / R['Revenue'][-4]), '–', p0(R['Gross profit'][-1] / R['Revenue'][-1])],
    ['Q3 opex (GAAP)', '$9.2B', '–', '–', B(R['Research & development'][-4] + R['SG&A'][-4]), pct(9.2e9 / (R['Research & development'][-4] + R['SG&A'][-4]) - 1), B(opex_lq)],
    ['Q4 revenue (next guide)', 'to be issued', B(q1['revenue']['avg']), f"{B(q1['revenue']['high'])} / {B(q1['revenue']['low'])}", B(q1['revenue']['year_ago']), {'v': pct(q1['revenue']['avg'] / q1['revenue']['year_ago'] - 1), 'cls': 'n up'}, '–'],
    ['FY27 revenue', '–', B(y0['revenue']['avg']), f"{B(y0['revenue']['high'])} / {B(y0['revenue']['low'])}", B(y0['revenue']['year_ago']), pct(y0['revenue']['avg'] / y0['revenue']['year_ago'] - 1), '–'],
    ['FY28 EPS', '–', f"${y1['eps']['avg']:.2f}", f"${y1['eps']['high']:.2f} / ${y1['eps']['low']:.2f}", f"${y1['eps']['year_ago']:.2f}", pct(y1['eps']['avg'] / y1['eps']['year_ago'] - 1), '–']],
    'rcls': {0: 'tot'}}

pair = {'t': 'pair', 'left': ('Three things to watch', [
    f"<b>Q4 revenue guide vs {B(q1['revenue']['avg'])}.</b> A guide at or above the consensus high ({B(q1['revenue']['high'])}) is what the last three reactions suggest is needed for the stock to rise.",
    f"<b>Gross margin vs the 74.0% guide.</b> Q2 printed {p0(R['Gross profit'][-1] / R['Revenue'][-1])}. A print above 74.5% would be a beat on the guided band; below 73.5% would be the first miss on margin in the window.",
    f"<b>Hyperscale vs AI clouds mix.</b> Hyperscale was {B(D['bridge']['parts'][0]['b'])} and AI clouds, industrial and enterprise {B(D['bridge']['parts'][1]['b'])} in Q2. A shift between them changes customer concentration (22% and 14% top two in FY26)."]),
    'right': ('What would change the read', [
    f"<b>A beat below {pct(min(beats))}.</b> That would be the smallest in the eight-quarter window and would put revenue near {B(g_now['mid'] * (1 + min(beats)))}.",
    "<b>Export-control or tariff language.</b> The 10-K flags China licensing and tariffs; any change to the H20-type restrictions would move the guide.",
    f"<b>Opex step-up.</b> GAAP opex guided to $9.2B vs {B(opex_lq)} in Q2 ({pct(9.2e9 / opex_lq - 1)} QoQ)."])}

surp_tbl = {'head': ['Quarter', 'EPS est.', 'Actual', 'Surprise', 'Revenue', 'vs guide mid', 'Next day', 'vs SPY'], 'rows': []}
gmap = {g['end'][:7]: g for g in D['guide']}
for s in reversed(surp):
    g = next((x for x in D['guide'] if x['act'] and abs((dt.date.fromisoformat(x['end']) - dt.date.fromisoformat(s['q'])).days) <= 10), None)
    surp_tbl['rows'].append([s['q'][:7], f"${s['est']:.2f}", f"${s['act']:.2f}", {'v': pct(s['pct']), 'cls': 'n up'}, B(s['rev']),
                             {'v': pct(g['act'] / g['mid'] - 1) if g else '–', 'cls': 'n up'}, {'v': pct(s['r1']), 'cls': 'n up' if s['r1'] > 0 else 'n dn'}, pct(s['r1'] - s['spy'])])


def heat(v, lo=0.0, hi=1.2):
    if v is None:
        return None
    t = max(0, min(1, (v - lo) / (hi - lo)))
    a = 0.08 + 0.55 * t
    return f"rgba(10,135,84,{a:.2f})" if v >= 0 else "rgba(209,47,53,0.25)"


def wide_model():
    qh = [dt.date.fromisoformat(e).strftime("%b '%y") for e in ends[-8:]]
    A = D['annual']
    aends = sorted(A['Revenue'])
    ah = ['FY' + e[2:4] if False else 'FY' + str(int(e[:4]))[2:] for e in aends]
    head = ['$B'] + qh + ah + ['FY27E', 'FY28E']
    est = {len(head) - 2, len(head) - 1}
    hcls = {i: 'est' for i in est}
    def cells(key, fmt, ann_key=None, cons_key=None):
        q = [fmt(v) for v in R[key][-8:]]
        a = [fmt(A[ann_key][e]) if ann_key and e in A[ann_key] else '–' for e in aends]
        c = [{'v': fmt(y0[cons_key]['avg']) if cons_key else '–', 'cls': 'n est'}, {'v': fmt(y1[cons_key]['avg']) if cons_key else '–', 'cls': 'n est'}]
        return q + a + c
    money = lambda v: '–' if v is None else f"{v / 1e9:,.1f}"
    eps = lambda v: '–' if v is None else f"{v:.2f}"
    def yoy_row(key, ann_key=None, cons_key=None):
        out = []
        for i in range(len(R[key]) - 8, len(R[key])):
            a, b = R[key][i], R[key][i - 4] if i >= 4 else None
            v = None if a is None or b is None else a / b - 1
            out.append({'v': pct(v, 0), 'cls': 'n', 'bg': heat(v)})
        for j, e in enumerate(aends):
            if j == 0:
                out.append({'v': '–', 'cls': 'n'})
                continue
            a, b = A[ann_key][e], A[ann_key][aends[j - 1]]
            v = a / b - 1
            out.append({'v': pct(v, 0), 'cls': 'n', 'bg': heat(v)})
        if cons_key:
            v0 = y0[cons_key]['avg'] / y0[cons_key]['year_ago'] - 1
            v1 = y1[cons_key]['avg'] / y1[cons_key]['year_ago'] - 1
            out += [{'v': pct(v0, 0), 'cls': 'n est', 'bg': heat(v0)}, {'v': pct(v1, 0), 'cls': 'n est', 'bg': heat(v1)}]
        else:
            out += [{'v': '–', 'cls': 'n est'}, {'v': '–', 'cls': 'n est'}]
        return out
    def margin_row(num, den, ann_num):
        q = [p0(R[num][i] / R[den][i]) if R[num][i] and R[den][i] else '–' for i in range(len(R[num]) - 8, len(R[num]))]
        a = [p0(A[ann_num][e] / A['Revenue'][e]) for e in aends]
        return q + a + [{'v': '–', 'cls': 'n est'}, {'v': '–', 'cls': 'n est'}]
    fcf = [None if o is None or c is None else o - c for o, c in zip(R['Operating cash flow'], R['Capital expenditures'])]
    R['FCF'] = fcf
    rows = [['Revenue'] + cells('Revenue', money, 'Revenue', 'revenue'), ['  YoY'] + yoy_row('Revenue', 'Revenue', 'revenue'),
            ['Gross profit'] + cells('Gross profit', money, 'Gross profit'), ['  Gross margin'] + margin_row('Gross profit', 'Revenue', 'Gross profit'),
            ['Operating income'] + cells('Operating income', money, 'Operating income'), ['  Operating margin'] + margin_row('Operating income', 'Revenue', 'Operating income'),
            ['Net income'] + cells('Net income', money, 'Net income'), ['EPS, diluted ($)'] + cells('EPS diluted', eps, 'EPS diluted', 'eps'),
            ['  YoY'] + yoy_row('EPS diluted', 'EPS diluted', 'eps'),
            ['Free cash flow'] + [money(v) for v in fcf[-8:]] + ['–'] * len(aends) + [{'v': '–', 'cls': 'n est'}, {'v': '–', 'cls': 'n est'}]]
    return {'cls': 'wide', 'head': head, 'hcls': hcls, 'rows': rows, 'rcls': {0: 'tot', 1: 'sub', 3: 'sub', 5: 'sub', 7: 'tot', 8: 'sub'},
            'group': [('', 1), ('Quarterly (fiscal quarter end)', 8), ('Fiscal year', len(aends)), ('Consensus', 2, 'est')]}


peer_order = ['NVDA', 'AVGO', 'AMD', 'MU', 'QCOM', 'MRVL', 'INTC', 'TXN', 'ADI']
pr = [D['peers'][t] for t in peer_order if t in D['peers']]
def med(k):
    vs = [p[k] for p in pr if p['t'] != 'NVDA' and p.get(k) is not None]
    return st.median(vs) if vs else None
peer_tbl = {'head': ['Company', 'Mkt cap', 'Price', 'Rev TTM', 'Rev g LQ', 'Gross m.', 'Op m.', 'P/E TTM', 'P/E FY1', 'P/E FY2', 'EV proxy P/S FY2', 'Rev g FY2', 'YTD'], 'rows': [
    [f"{p['t']}", B(p['mc']), f"${p['price']:.2f}", B(p['ttm_rev']), pct(p['g'], 0), p0(p['gm'], 0), p0(p['om'], 0), xx(p['pe_ttm']), xx(p['pe0']), xx(p['pe1']), xx(p['ps1']), pct(p['g1'], 0), pct(p['ytd'], 0)] for p in pr]
    + [['Median ex-NVDA', B(med('mc')), '', B(med('ttm_rev')), pct(med('g'), 0), p0(med('gm'), 0), p0(med('om'), 0), xx(med('pe_ttm')), xx(med('pe0')), xx(med('pe1')), xx(med('ps1')), pct(med('g1'), 0), pct(med('ytd'), 0)]],
    'rcls': {0: 'me', len(pr): 'med'}}
peer_tbl['head'][10] = 'P/S FY2'

pe_tbl = {'head': ['Trailing P/E, 2 years', 'Value'], 'rows': [
    ['Minimum', xx(pe_vals[0])], ['25th percentile', xx(st.quantiles(pe_vals, n=4)[0])], ['Median', xx(pe_med)], ['75th percentile', xx(st.quantiles(pe_vals, n=4)[2])],
    ['Maximum', xx(pe_vals[-1])], [{'v': '<b>Current</b>'}, {'v': f"<b>{pe_now:.1f}×</b>", 'cls': 'n'}], ['Z-score vs 2-yr mean', f"{pe_z:+.2f}"]]}

eps_pts = [y1['eps']['low'], 14.0, y1['eps']['avg'], 17.5, y1['eps']['high']]
mults = [15, 20, 25, 30, 35]
grid = {'cls': 'grid', 'head': ['FY28E EPS → / multiple ↓'] + [f"${e:.2f}" + (' (low)' if i == 0 else ' (cons.)' if i == 2 else ' (high)' if i == 4 else '') for i, e in enumerate(eps_pts)], 'rows': []}
for m in mults:
    row = [f"{m}×"]
    for i, e in enumerate(eps_pts):
        v = e * m
        diff = v / price - 1
        row.append({'v': f"${v:,.0f}<br><span style='font-size:6.4pt;color:var(--muted)'>{pct(diff, 0)}</span>", 'cls': 'n base' if (m == 15 and i == 2) else 'n',
                    'bg': f"rgba(10,135,84,{min(.5, .06 + diff * .25):.2f})" if diff >= 0 else f"rgba(209,47,53,{min(.45, .06 - diff * .5):.2f})"})
    grid['rows'].append(row)

scen = []
for lab, v in [('Bottom of guided band', g_now['lo']), ('Guided midpoint', g_now['mid']), ('Consensus', q0['revenue']['avg']), ('Smallest beat in window', g_now['mid'] * (1 + min(beats))),
               ('Average beat', g_now['mid'] * (1 + avg_beat)), ('Largest beat in window', g_now['mid'] * (1 + max(beats))), ('Consensus high', q0['revenue']['high'])]:
    scen.append([lab, B(v), pct(v / g_now['mid'] - 1), pct(v / q0['revenue']['avg'] - 1), pct(v / q0['revenue']['year_ago'] - 1, 0), pct(v / R['Revenue'][-1] - 1)])
scen_tbl = {'head': ['Q3 revenue outcome', 'Revenue', 'vs guide mid', 'vs consensus', 'YoY', 'QoQ'], 'rows': scen, 'rcls': {2: 'me', 4: 'med'}}

SOURCES = ("<b>Sources and method.</b> Reported figures: XBRL facts in NVIDIA 10-Q and 10-K filings on SEC EDGAR; quarter values from three-month contexts; Q4 values derived as fiscal year less three quarters. "
           f"Guidance: CFO commentary and 8-K exhibits, stored with the quoted sentence. Consensus: Yahoo Finance as of {D['cons_asof'][:10]} (third party). Ratings and targets: Powered by Benzinga Data. "
           "Peer figures are computed the same way from each company's filings; market cap is last close times latest diluted shares; forward P/E uses consensus EPS. Trailing P/E history uses net income known at each month end. "
           "Scenario and sensitivity tables are arithmetic on the stated inputs, not forecasts or price targets.")
DISC = ("<b>Disclosures.</b> Sample output generated by Filed Under for format review. For research purposes only; not investment advice or a recommendation to buy or sell any security. "
        "Verify figures against the cited filings before relying on them.")

blocks = [
    metrics, secnav,
    {'t': 'section', 'no': 1, 'title': 'The setup'},
    {'t': 'split', 'cols': [
        [{'t': 'text', 'html': f"NVIDIA reports Q3 FY27 (the quarter ending in late October) after guiding revenue to <b>{B(g_now['mid'])} ±2%</b>, gross margin to <b>74.0% ±50 bp</b> and GAAP opex to about <b>$9.2B</b>. "
                                f"Consensus revenue is <b>{B(q0['revenue']['avg'])}</b> ({int(q0['revenue']['n'])} estimates) and EPS <b>${q0['eps']['avg']:.2f}</b>, implying {pct(q0['revenue']['avg'] / q0['revenue']['year_ago'] - 1, 0)} and {pct(q0['eps']['avg'] / q0['eps']['year_ago'] - 1, 0)} year-over-year growth."},
         {'t': 'text', 'html': f"The history favours a beat: reported revenue cleared the guided midpoint in all {len(hist)} quarters since Q3 FY25, by {pct(min(beats))} to {pct(max(beats))}. "
                                f"The average beat on this guide would mean about <b>{B(g_now['mid'] * (1 + avg_beat))}</b>, {pct(g_now['mid'] * (1 + avg_beat) / q0['revenue']['avg'] - 1)} above consensus. "
                                f"Beats have not been enough for the stock: it fell the next day after {fell} of the last {len(surp)} reports, including a {pct(surp[1]['r1'])} move after a {pct(surp[1]['pct'])} EPS beat in February."},
         {'t': 'text', 'html': f"Shares are {pct(price / D['h52'] - 1, 0)} from the 52-week high at {pe_now:.0f}× trailing earnings, against a {pe_med:.0f}× two-year median, and {nv['pe1']:.1f}× FY28 consensus EPS. "
                                f"The Street is close to uniformly positive ({rat['buy']} of {sum(rat.values())} at Buy, median target ${med_pt:.0f})."}],
        [setup_facts, guide_facts]]},
    {'t': 'exhibit', 'no': 1, 'title': 'NVDA, trailing 12 months, with 20- and 50-day averages and results-day moves', 'sub': 'Daily OHLC and volume', 'chart': 'candles', 'w': FULL, 'h': 230,
     'src': 'Daily prices; results dates from 8-K Item 2.02 acceptance times. Filed Under.'},
    {'t': 'section', 'no': 2, 'title': 'Expectations: guide, consensus and the bar'},
    {'t': 'exhibit', 'no': 2, 'title': 'Q3 FY27 expectations', 'table': exp_tbl, 'src': 'NVIDIA guidance; Yahoo Finance consensus (third party); NVIDIA XBRL for year-ago and Q2 actuals.'},
    pair,
    {'t': 'split', 'cls': 'two', 'cols': [
        [{'t': 'exhibit', 'no': 3, 'title': 'Consensus range vs the guided band', 'sub': 'Next two quarters, $B', 'chart': 'range', 'w': HALF, 'h': 150, 'src': 'Yahoo Finance; NVIDIA guidance.'}],
        [{'t': 'exhibit', 'no': 4, 'title': 'Guided midpoint vs reported revenue', 'sub': '$B; label = beat vs midpoint', 'chart': 'dumbbell', 'w': HALF, 'h': 150, 'src': 'NVIDIA guidance; XBRL.'}]]},
    {'t': 'section', 'no': 3, 'title': 'Beat and reaction history'},
    {'t': 'split', 'cols': [
        [{'t': 'exhibit', 'no': 5, 'title': 'Surprises and the next-day move', 'table': surp_tbl, 'src': 'Yahoo Finance consensus; SEC filings; daily prices.'}],
        [{'t': 'exhibit', 'no': 6, 'title': f"Next-day moves, last {len(react)} reports", 'chart': 'react', 'w': 270, 'h': 135, 'src': 'Filed Under event study.'}]]},
    {'t': 'section', 'no': 4, 'title': 'Financial model'},
    {'t': 'exhibit', 'no': 7, 'title': 'Reported quarters and fiscal years, with consensus', 'sub': 'YoY cells shaded by growth; consensus columns shaded beige', 'table': wide_model(),
     'src': 'NVIDIA XBRL; Yahoo Finance consensus for FY27E and FY28E revenue and EPS. FY24 EPS restated for the June 2024 ten-for-one split; Q4 FY26 EPS derived from the fiscal year.'},
    {'t': 'section', 'no': 5, 'title': 'Valuation and peers'},
    {'t': 'exhibit', 'no': 8, 'title': 'Semiconductor peers, computed from filings', 'table': peer_tbl,
     'src': 'Company 10-Q and 10-K XBRL (latest fiscal quarter per company); Yahoo Finance consensus for FY1/FY2 EPS and revenue; market cap = last close × latest diluted shares.'},
    {'t': 'split', 'cols': [
        [{'t': 'exhibit', 'no': 9, 'title': f"Trailing P/E has compressed from {max(pe_vals):.0f}× to {pe_now:.0f}× as earnings outgrew the price", 'sub': 'Month-end, price × diluted shares ÷ trailing four quarters of net income', 'chart': 'pehist', 'w': 430, 'h': 160, 'src': 'Filed Under from XBRL and daily prices.'}],
        [{'t': 'exhibit', 'no': 10, 'title': 'Two-year trailing P/E range', 'table': pe_tbl, 'src': 'Filed Under.'}]]},
    {'t': 'section', 'no': 6, 'title': 'Scenarios'},
    {'t': 'split', 'cls': 'two', 'cols': [
        [{'t': 'exhibit', 'no': 11, 'title': 'Q3 revenue outcomes against guide and consensus', 'table': scen_tbl, 'src': 'Arithmetic on NVIDIA guidance, consensus and the eight-quarter beat range.'}],
        [{'t': 'exhibit', 'no': 12, 'title': 'FY28E EPS × multiple, vs the last close', 'table': grid, 'src': 'Arithmetic only: consensus EPS range times multiple; not a price target. Boxed cell: consensus EPS at 15×.'}]]},
    {'t': 'section', 'no': 7, 'title': 'Sources and disclosures'}, {'t': 'disc', 'html': SOURCES}, {'t': 'disc', 'html': DISC},
]

out = []
i = 0
while i < len(blocks):
    if blocks[i].get('t') == 'section' and i + 1 < len(blocks) and blocks[i + 1].get('t') != 'section':
        out.append({'t': 'keep', 'kids': [blocks[i], blocks[i + 1]]}); i += 2
    else:
        out.append(blocks[i]); i += 1
blocks = out
html = env.from_string(T.BODY).render(css=(P / 'report.css').read_text(), charts=(P / 'charts2.js').read_text(), data_json=json.dumps(D), meta=meta, first=first,
                                      pages=[{'head': 'Review', 'blocks': blocks}])
html = html.replace('<div class="strip"></div>', '')
(P / 'dense.html').write_text(html)
subprocess.run(['node', str(P / 'pdf.js'), str(P / 'dense.html'), str(P / 'NVDA-pre-earnings-review-dense.pdf'), 'footer', meta['title']], check=True)
print('ok')
