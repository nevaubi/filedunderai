"""Jinja templates for Filed Under research PDFs. A document is a spec:
{meta, cover?, first: {title, dek, strip, ...}, pages: [{head, blocks: [...]}]}. Block types:
text, kp, exhibit, table, callout, two (list of block lists), cols (main blocks + side blocks), section, disc."""

HEAD = """<!doctype html><html><head><meta charset="utf-8"><title>{{ meta.title }}</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Figtree:wght@400;500;600;700&family=Instrument+Serif&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>{{ css }}</style></head><body>"""

TAIL = """<script src="https://cdnjs.cloudflare.com/ajax/libs/d3/7.9.0/d3.min.js"></script>
<script>window.__D = {{ data_json }};</script><script>{{ charts }}</script></body></html>"""

MACROS = """
{% macro block(b) -%}
{% if b.t == 'text' %}<p>{{ b.html }}</p>
{% elif b.t == 'kp' %}{% if b.title %}<h3>{{ b.title }}</h3>{% endif %}<ul class="kp">{% for i in b.pts %}<li><b>{{ i[0] }}.</b> {{ i[1] }}</li>{% endfor %}</ul>
{% elif b.t == 'section' %}<div class="sec"><div class="no">{{ '%02d' % b.no }}</div><h2>{{ b.title }}</h2>{% if b.lede %}<div class="lede">{{ b.lede }}</div>{% endif %}</div>
{% elif b.t == 'exhibit' %}<div class="exh"><div class="no">Exhibit {{ b.no }}</div><div class="tt">{{ b.title }}</div>{% if b.sub %}<div class="st">{{ b.sub }}</div>{% endif %}
  {% if b.chart %}<div class="chart" data-kind="{{ b.chart }}" data-w="{{ b.w }}" data-h="{{ b.h or '' }}"></div>{% endif %}
  {% if b.table %}{{ table(b.table) }}{% endif %}<div class="src">Source: {{ b.src }}</div></div>
{% elif b.t == 'table' %}{% if b.title %}<h3>{{ b.title }}</h3>{% endif %}{{ table(b) }}
{% elif b.t == 'callout' %}<div class="callout"><h3>{{ b.title }}</h3>{{ b.html }}</div>
{% elif b.t == 'two' %}<div class="two">{% for col in b.cols %}<div>{% for x in col %}{{ block(x) }}{% endfor %}</div>{% endfor %}</div>
{% elif b.t == 'cols' %}<div class="cols"><div>{% for x in b.main %}{{ block(x) }}{% endfor %}</div><aside class="side">{% for x in b.side %}{{ block(x) }}{% endfor %}</aside></div>
{% elif b.t == 'disc' %}<p class="disc">{{ b.html }}</p>
{% endif %}
{%- endmacro %}
{% macro table(tb) -%}
<table>{% if tb.head %}<tr>{% for h in tb.head %}<th class="{{ 'n' if loop.index0 >= (tb.lcols or 1) else '' }}">{{ h }}</th>{% endfor %}</tr>{% endif %}
{% for r in tb.rows %}<tr>{% for c in r %}{% if c is mapping %}<td class="{{ c.cls or '' }}" {% if c.span %}colspan="{{ c.span }}"{% endif %}>{{ c.v }}</td>{% else %}<td class="{{ 'n' if loop.index0 >= (tb.lcols or 1) else '' }}">{{ c }}</td>{% endif %}{% endfor %}</tr>{% endfor %}</table>
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
