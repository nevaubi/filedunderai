"""Jinja templates for Filed Under research PDFs. A document is a spec:
{meta, cover?, first: {title, dek, strip, ...}, pages: [{head, blocks: [...]}]}. Block types:
text, kp, exhibit, table, callout, two (list of block lists), cols (main blocks + side blocks), section, disc."""

HEAD = """<!doctype html><html><head><meta charset="utf-8"><title>{{ meta.title }}</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Figtree:wght@400;500;600;700&family=Instrument+Serif&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>{{ css }}</style></head><body class="{{ meta.density or '' }}">"""

TAIL = """<script src="https://cdnjs.cloudflare.com/ajax/libs/d3/7.9.0/d3.min.js"></script>
<script>window.__D = {{ data_json }};</script><script>{{ charts }}</script></body></html>"""

MACROS = """
{% macro block(b) -%}
{% if b.t == 'text' %}<p>{{ b.html }}</p>
{% elif b.t == 'kp' %}{% if b.title %}<h3>{{ b.title }}</h3>{% endif %}<ul class="kp">{% for i in b.pts %}<li><b>{{ i[0] }}.</b> {{ i[1] }}</li>{% endfor %}</ul>
{% elif b.t == 'section' %}<div class="sec"><div class="no">{{ '%02d' % b.no }}</div><h2>{{ b.title }}</h2>{% if b.lede %}<div class="lede">{{ b.lede }}</div>{% endif %}</div>
{% elif b.t == 'exhibit' %}<div class="exh"><div class="no">Exhibit {{ b.no }}</div><div class="tt">{{ b.title }}</div>{% if b.sub %}<div class="st">{{ b.sub }}</div>{% endif %}
  {% if b.chart %}<div class="chart" data-kind="{{ b.chart }}" data-w="{{ b.w }}" data-h="{{ b.h or '' }}"{% for k, v in (b.attrs or {}).items() %} data-{{ k }}="{{ v }}"{% endfor %}></div>{% endif %}
  {% if b.table %}{{ table(b.table) }}{% endif %}{% if b.model %}{{ block(b.model) }}{% endif %}<div class="src">Source: {{ b.src }}</div></div>
{% elif b.t == 'table' %}{% if b.title %}<h3>{{ b.title }}</h3>{% endif %}{{ table(b) }}
{% elif b.t == 'callout' %}<div class="callout"><h3>{{ b.title }}</h3>{{ b.html }}</div>
{% elif b.t == 'two' %}<div class="two">{% for col in b.cols %}<div>{% for x in col %}{{ block(x) }}{% endfor %}</div>{% endfor %}</div>
{% elif b.t == 'cols' %}<div class="cols"><div>{% for x in b.main %}{{ block(x) }}{% endfor %}</div><aside class="side">{% for x in b.side %}{{ block(x) }}{% endfor %}</aside></div>
{% elif b.t == 'metrics' %}<div class="metrics" style="--n:{{ b.n or 8 }}">{% for m in b.its %}<div><div class="k">{{ m[0] }}</div><div class="v {{ m[3] if m|length > 3 else '' }}">{{ m[1] }}</div><div class="s">{{ m[2] }}</div></div>{% endfor %}</div>
{% elif b.t == 'secnav' %}<div class="secnav">{% for s in b.its %}<span><b>{{ loop.index }}</b>{{ s }}</span>{% endfor %}</div>
{% elif b.t == 'pair' %}<div class="pair"><div><h3>{{ b.left[0] }}</h3><ol>{% for x in b.left[1] %}<li>{{ x }}</li>{% endfor %}</ol></div><div class="r"><h3>{{ b.right[0] }}</h3><ol>{% for x in b.right[1] %}<li>{{ x }}</li>{% endfor %}</ol></div></div>
{% elif b.t == 'facts' %}<div class="facts"><h3>{{ b.title }}</h3>{{ table(b.table) }}{% if b.note %}<div class="src" style="font-size:6.8pt;color:var(--faint);margin-top:3px">{{ b.note }}</div>{% endif %}</div>
{% elif b.t == 'split' %}<div class="{{ b.cls or 'split' }}">{% for col in b.cols %}<div>{% for x in col %}{{ block(x) }}{% endfor %}</div>{% endfor %}</div>
{% elif b.t == 'keep' %}<div style="break-inside:avoid;page-break-inside:avoid">{% for x in b.kids %}{{ block(x) }}{% endfor %}</div>
{% elif b.t == 'brief' %}<div class="brief">{% if b.lead %}<p class="lead">{{ b.lead }}</p>{% endif %}<ul class="kp">{% for i in b.pts %}<li><b>{{ i[0] }}.</b> {{ i[1] }}</li>{% endfor %}</ul></div>
{% elif b.t == 'boxes' %}<div class="boxes" style="--n:{{ b.bx | length }}">{% for x in b.bx %}<div class="box {{ x.tone or '' }}"><div class="k">{{ x.k }}</div><div class="h">{{ x.h }}</div><p>{{ x.body }}</p></div>{% endfor %}</div>
{% elif b.t == 'cards' %}<div class="cards">{% for c in b.cs %}<div class="card {{ 'me' if c.me else '' }}"><div class="ch"><span class="tk">{{ c.t }}</span><span class="nm">{{ c.name }}</span><span class="rk">{{ c.rank }}</span></div>
  <div class="cm">{% for m in c.m %}<div><div class="k">{{ m[0] }}</div><div class="v">{{ m[1] }}</div></div>{% endfor %}</div><ul>{% for x in c.pts %}<li>{{ x }}</li>{% endfor %}</ul></div>{% endfor %}</div>
{% elif b.t == 'qa' %}<div class="qa">{% if b.title %}<h3>{{ b.title }}</h3>{% endif %}{% for q in b.qs %}<div class="q"><b>{{ q[0] }}</b><p>{{ q[1] }}</p></div>{% endfor %}</div>
{% elif b.t == 'redline' %}<div class="rl">{% if b.title %}<h3>{{ b.title }}</h3>{% endif %}{% for x in b.xs %}<div class="rlc {{ x.kind }}"><div class="m">{{ x.kind | upper }} · {{ x.where }}{% if x.tags %} · {{ x.tags }}{% endif %}</div><p>{{ x.text }}</p>{% if x.why %}<div class="why">{{ x.why }}</div>{% endif %}</div>{% endfor %}</div>
{% elif b.t == 'disc' %}<p class="disc">{{ b.html }}</p>
{% elif b.t == 'feature' %}<div class="feature"><div class="rail">{% for s in b.stats %}<div class="big">{{ s[0] }}</div><div class="cap">{{ s[1] }}</div>{% endfor %}{% for x in b.rail %}{{ block(x) }}{% endfor %}</div><div>{% for x in b.main %}{{ block(x) }}{% endfor %}</div></div>
{% elif b.t == 'grid' %}<div class="grid2">{% for x in b.cells %}<div>{{ block(x) }}</div>{% endfor %}</div>
{% elif b.t == 'tiles' %}<div class="tiles">{% for x in b.tiles %}<div class="tile"><div class="k">{{ x.k }}</div><div class="v">{{ x.v }}</div><div class="d {{ x.cls or '' }}">{{ x.d }}</div><div class="chart" data-kind="spark" data-style="{{ x.style or 'line' }}" data-w="140" data-h="26" data-series='{{ x.series | tojson }}'></div><div class="s">{{ x.s }}</div></div>{% endfor %}</div>
{% elif b.t == 'divider' %}<div class="divider"><div><div class="no">SECTION {{ '%02d' % b.no }}</div><h2>{{ b.title }}</h2><p>{{ b.lede }}</p></div><div class="stat"><div class="v">{{ b.stat[0] }}</div><div class="c">{{ b.stat[1] }}</div></div></div>
{% elif b.t == 'stats' %}<div class="stats" style="--n:{{ b.items | length }}">{% for s in b.items %}<div><div class="v">{{ s[0] }}</div><div class="k">{{ s[1] }}</div></div>{% endfor %}</div>
{% elif b.t == 'model' %}<table class="model"><tr><th></th>{% for h in b.head %}<th class="n {{ 'cur' if loop.last else '' }}">{{ h }}</th>{% endfor %}<th class="n">Trend</th></tr>
{% for r in b.rows %}<tr class="{{ r.cls or '' }}"><td>{{ r.label }}</td>{% for c in r.cells %}<td class="n {{ 'cur' if loop.last else '' }}">{{ c }}</td>{% endfor %}<td class="sp">{% if r.series %}<div class="chart" data-kind="spark" data-style="bar" data-w="70" data-h="14" data-series='{{ r.series | tojson }}'></div>{% endif %}</td></tr>{% endfor %}</table>
{% elif b.t == 'quotes' %}{% if b.title %}<h3>{{ b.title }}</h3>{% endif %}<ul class="quotes">{% for q in b.qs %}<li><span class="m">{{ q[0] }}</span>{{ q[1] }}</li>{% endfor %}</ul>
{% endif %}
{%- endmacro %}
{% macro table(tb) -%}
<table class="{{ tb.cls or '' }}">{% if tb.group %}<tr>{% for g in tb.group %}<th colspan="{{ g[1] }}" class="{{ g[2] if g|length > 2 else '' }}" style="text-align:center;border-bottom:1px solid var(--line)">{{ g[0] }}</th>{% endfor %}</tr>{% endif %}{% if tb.head %}<tr>{% for h in tb.head %}<th class="{{ 'n' if loop.index0 >= (tb.lcols or 1) else '' }} {{ (tb.hcls or {}).get(loop.index0, '') }}">{{ h }}</th>{% endfor %}</tr>{% endif %}
{% for r in tb.rows %}{% set rc = (tb.rcls or {}).get(loop.index0, '') %}<tr class="{{ rc }}">{% for c in r %}{% if c is mapping %}<td class="{{ c.cls or '' }}" {% if c.span %}colspan="{{ c.span }}"{% endif %} {% if c.bg %}style="background:{{ c.bg }};{{ 'color:#fff' if c.dark else '' }}"{% endif %}>{{ c.v }}</td>{% else %}<td class="{{ 'n' if loop.index0 >= (tb.lcols or 1) else '' }}">{{ c }}</td>{% endif %}{% endfor %}</tr>{% endfor %}</table>
{%- endmacro %}
"""

BODY = HEAD + MACROS + """
{% for pg in pages %}<section class="page">
{% if loop.first %}
  <div class="band"><div class="brand">FILED<i>/</i>UNDER RESEARCH</div>
    <div class="meta"><span class="type">{{ meta.kind }}</span><span>{{ meta.date }}</span><span>{{ meta.sector }}</span></div></div>
  <div class="titleblock"><div><div class="co">{{ meta.company }}</div><h1>{{ first.title }}</h1></div>
    <div class="tick"><div class="sym">{{ meta.ticker }}</div><div class="ex">{{ meta.exchange }}</div></div></div>
  <div class="strip">{% for s in first.strip %}<div><div class="k">{{ s[0] }}</div><div class="v">{{ s[1] }}</div><div class="s">{{ s[2] }}</div></div>{% endfor %}</div>
  <p class="dek">{{ first.dek }}</p>
{% else %}
  <div class="rh"><div class="l">FILED<i>/</i>UNDER<span>{{ meta.company }} ({{ meta.ticker }}) · {{ meta.kind }}</span></div><div class="r">{{ pg.head }}</div></div>
{% endif %}
{% for b in pg.blocks %}{{ block(b) }}{% endfor %}
</section>{% endfor %}
""" + TAIL

COVER = HEAD + """
<div class="cover">
  <div class="top"><div class="brand">FILED<i>/</i>UNDER RESEARCH</div><div class="tier">{{ cover.tier }}</div></div>
  <div class="kick">{{ meta.kind }} · {{ meta.company }} · {{ meta.exchange }}: {{ meta.ticker }}</div>
  <h1>{{ cover.title }}</h1>
  <p class="dek">{{ cover.dek }}</p>
  <div class="who">{% for w in cover.facts %}<div>{{ w[0] }}<b>{{ w[1] }}</b></div>{% endfor %}</div>
  <div class="hero"><div class="chart" data-kind="cover" data-w="705" data-h="190"></div></div>
  <div class="bottom">
    <div><ul class="toc">{% for t in cover.toc %}<li><span>{{ '%02d' % loop.index }}</span><span>{{ t[0] }}</span><span>{{ t[1] }}</span></li>{% endfor %}</ul></div>
    <div class="note">{{ meta.date }}<br>{{ cover.note }}</div>
  </div>
</div>
""" + TAIL
