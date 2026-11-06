"""Deterministic disclosure redlines between two periodic filings (10-K vs prior 10-K, 10-Q vs prior 10-Q).

Pure functions, no network. A filing's plain text (one paragraph per line, from supply_core.html_to_text) is split
into Items (1, 1A, 1C, 3, 7, 7A, 9A for 10-K; Part I Item 2/3/4 and Part II Item 1/1A for 10-Q) and, inside the
financial statements, into numbered notes. Sections are matched across filings by item code or note title,
paragraphs are aligned with difflib on a digit-masked form, and each changed paragraph is classified:

  added        paragraph only in the new filing
  removed      paragraph only in the old filing
  modified     same paragraph with wording changes (word-level ops kept)
  numbers      same wording, only figures changed (kept separately so language changes stand out)

Every change carries the exact old/new paragraph text, so the page can show it verbatim and link the filing.
"""
import difflib
import re

VERSION = 1

ITEMS_10K = [
    ("1", "Business", r"business"),
    ("1A", "Risk factors", r"risk\s+factors"),
    ("1C", "Cybersecurity", r"cybersecurity"),
    ("3", "Legal proceedings", r"legal\s+proceedings"),
    ("7", "MD&A", r"management['’]?s\s+discussion\s+and\s+analysis"),
    ("7A", "Market risk", r"quantitative\s+and\s+qualitative\s+disclosures?\s+about\s+market\s+risk"),
    ("8", "Financial statements", r"financial\s+statements\s+and\s+supplementary\s+data"),
    ("9A", "Controls and procedures", r"controls\s+and\s+procedures"),
]
ITEMS_10Q = [
    ("I-1", "Financial statements", r"financial\s+statements"),
    ("I-2", "MD&A", r"management['’]?s\s+discussion\s+and\s+analysis"),
    ("I-3", "Market risk", r"quantitative\s+and\s+qualitative\s+disclosures?\s+about\s+market\s+risk"),
    ("I-4", "Controls and procedures", r"controls\s+and\s+procedures"),
    ("II-1", "Legal proceedings", r"legal\s+proceedings"),
    ("II-1A", "Risk factors", r"risk\s+factors"),
]

# Notes whose changes matter most to analysts; the topic key also matches renamed notes across years.
NOTE_TOPICS = [
    ("accounting_policies", r"(summary\s+of\s+)?(significant\s+)?accounting\s+policies|basis\s+of\s+presentation"),
    ("revenue", r"revenue"),
    ("segments", r"segment"),
    ("debt", r"debt|borrowings|credit\s+facilit|notes\s+payable|financing\s+arrangements"),
    ("contingencies", r"commitments|contingenc|legal\s+(matters|proceedings)|litigation"),
    ("income_taxes", r"income\s+tax"),
    ("leases", r"lease"),
    ("goodwill", r"goodwill|intangible"),
    ("stock_comp", r"stock[-\s]based|share[-\s]based|equity\s+(incentive|compensation)|stock\s+compensation"),
    ("fair_value", r"fair\s+value"),
    ("derivatives", r"derivative|hedg"),
    ("acquisitions", r"acquisition|business\s+combination|divestiture|discontinued"),
    ("restructuring", r"restructuring|impairment"),
    ("earnings_per_share", r"(earnings|net\s+income|loss)\s+per\s+share"),
    ("related_party", r"related\s+part"),
    ("subsequent_events", r"subsequent\s+event"),
]

# Wording that analysts look for when a disclosure changes; each hit raises a change's score and is listed.
SIGNALS = [
    ("material weakness", r"material\s+weakness"), ("going concern", r"going\s+concern"),
    ("restatement", r"restat(e|ed|ement)"), ("impairment", r"impair"), ("covenant", r"covenant"),
    ("default", r"\bdefault"), ("waiver", r"\bwaiver"), ("litigation", r"litigation|lawsuit|class\s+action"),
    ("investigation", r"investigation|subpoena|consent\s+decree"), ("cybersecurity", r"cyber|breach|ransomware"),
    ("tariffs/trade", r"tariff|export\s+control|sanction|trade\s+restriction"), ("customer concentration", r"(significant|major|largest)\s+customer|concentration"),
    ("supply", r"supplier|sole\s+source|single\s+source|shortage"), ("liquidity", r"liquidity|going\s+forward\s+funding|refinanc"),
    ("guidance/outlook", r"\boutlook\b|we\s+expect|anticipate"), ("regulation", r"regulat|FDA|FTC|antitrust|DOJ"),
    ("AI", r"artificial\s+intelligence|\bAI\b|generative"), ("accounting change", r"adopt(ed|ion)\s+of\s+ASU|change\s+in\s+accounting"),
]

_WORD = re.compile(r"\S+")
_DIGITS = re.compile(r"\d[\d,]*(\.\d+)?")


def norm(p):
    p = p.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"').replace("—", "-").replace("–", "-")
    return re.sub(r"\s+", " ", p).strip()


def masked(p):
    """Paragraph identity for alignment: lowercase, figures masked, so changed numbers do not break matching."""
    return _DIGITS.sub("#", norm(p).lower())


def _compact(text):
    """Lowercase letters only: EDGAR text often letter-spaces headings ("FINANCI AL STATEMENTS")."""
    return re.sub(r"[^a-z&]", "", text.lower().replace("’", "'").replace("'", ""))


def _title_match(pattern, text):
    return bool(re.match(pattern.replace(r"\s+", "").replace("['’]?", "").lower(), _compact(text)))


def _is_heading(line, pattern, code, following=""):
    """'Item 1A. Risk Factors' style heading line (not a table-of-contents row with a page number). A bare
    'Item 1.' line counts only when the next line carries the expected title."""
    if len(line) > 160:
        return False
    m = re.match(r"(?i)^\s*item\s*" + re.escape(code.split("-")[-1]) + r"(?![0-9A-Za-z])\s*[\.:\-–—]?\s*(.*)$", line)
    if not m:
        return False
    rest = m.group(1)
    if re.search(r"\b\d{1,3}\s*$", rest) and len(rest) < 120:  # trailing page number: table of contents
        return False
    if rest.strip(" .:-–—") == "":
        return _title_match(pattern, following or "")
    return _title_match(pattern, rest)


def split_items(text, form):
    """{code: {"title", "paras": [...]}} for the Items of a 10-K or 10-Q. The longest candidate body wins, which
    skips the table of contents and repeated running headers."""
    lines = [l for l in text.split("\n") if l.strip()]
    spec = ITEMS_10K if form.startswith("10-K") else ITEMS_10Q
    marks = []
    for i, line in enumerate(lines):
        for code, title, pat in spec:
            if _is_heading(line, pat, code, lines[i + 1] if i + 1 < len(lines) else ""):
                marks.append((i, code, title))
    # Another Item heading ends a section; running page headers that repeat the same Item ("PART I Item 2")
    # and bare "PART II" lines do not.
    item_line = re.compile(r"(?i)^\s*(?:part\s+(?:i|ii|iii|iv)\b[\s,.\-–—]*)?item\s*(\d{1,2}[A-C]?)\b")
    ends = []
    for i, line in enumerate(lines):
        m = item_line.match(line)
        if m and len(line) < 160:
            ends.append((i, m.group(1).upper()))
        elif re.match(r"(?i)^\s*signatures?\s*$", line):
            ends.append((i, "SIG"))
    best = {}
    for i, code, title in marks:
        num = code.split("-")[-1].upper()
        nxt = next((e for e, c in ends if e > i and c != num), len(lines))
        bare = re.match(r"(?i)^\s*item\s*\S+\s*[\.:\-–—]?\s*$", lines[i])
        body = [l for l in lines[i + (2 if bare else 1):nxt] if len(l) >= 3]
        size = sum(len(l) for l in body)
        if code not in best or size > best[code]["size"]:
            best[code] = {"title": title, "paras": body, "size": size, "start": i}
    return {k: {"title": v["title"], "paras": v["paras"]} for k, v in best.items()}


_NOTE = re.compile(r"(?i)^\s*(?:note|notes)\s+(\d{1,2}|[a-z])\s*[\.:\-–—]?\s*(.{2,120})$")
_NOTE_NUM = re.compile(r"^\s*(\d{1,2})\s*[\.:\-–—]\s*([A-Z][A-Za-z ,&'’\-()/]{3,100})$")


def split_notes(paras):
    """Numbered notes inside the financial statements Item: [(number, title, topic, paras)]."""
    heads = []
    for i, p in enumerate(paras):
        m = _NOTE.match(p) or _NOTE_NUM.match(p)
        if m and len(p) < 140 and not re.search(r"\d{4}\s*$", m.group(2)):
            heads.append((i, m.group(1), m.group(2).strip(" .:-—–")))
    # keep the run of headings that counts upward (drops cross-references such as "see Note 7 - Debt")
    out, last = [], 0
    for idx, (i, num, title) in enumerate(heads):
        try:
            n = int(num)
        except ValueError:
            continue
        if n == last + 1 or (not out and n == 1):
            out.append((i, n, title))
            last = n
    notes = []
    for k, (i, n, title) in enumerate(out):
        end = out[k + 1][0] if k + 1 < len(out) else len(paras)
        notes.append((n, title, note_topic(title), paras[i + 1:end]))
    return notes


def note_topic(title):
    t = title.lower()
    for key, pat in NOTE_TOPICS:
        if re.search(pat, t):
            return key
    return None


def word_ops(old, new):
    """Word-level diff as [["=", text] | ["-", text] | ["+", text]] runs."""
    a, b = _WORD.findall(old), _WORD.findall(new)
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    ops = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            ops.append(["=", " ".join(a[i1:i2])])
        else:
            if i2 > i1:
                ops.append(["-", " ".join(a[i1:i2])])
            if j2 > j1:
                ops.append(["+", " ".join(b[j1:j2])])
    return ops


def signals(text):
    return [name for name, pat in SIGNALS if re.search(pat, text, re.I)]


def score(kind, old, new, ops=None):
    """Rough importance: size of the wording change plus analyst signal words that appear in the changed words."""
    if kind == "numbers":
        return 1
    changed = " ".join(t for op, t in (ops or []) if op != "=") if ops else (new or old)
    words = len(changed.split())
    sig = signals(changed)
    return min(100, int(words ** 0.5 * 4) + 12 * len(sig) + (6 if kind in ("added", "removed") else 0))


def diff_paras(old, new, min_len=40, pair_ratio=0.55):
    """Aligned changes between two paragraph lists (paragraphs shorter than min_len, such as headers and table
    cells, are ignored)."""
    a = [norm(p) for p in old if len(p) >= min_len]
    b = [norm(p) for p in new if len(p) >= min_len]
    am, bm = [masked(p) for p in a], [masked(p) for p in b]
    aw, bw = [x.split() for x in am], [x.split() for x in bm]   # word sequences: pairing ratios on words, not characters
    sm = difflib.SequenceMatcher(None, am, bm, autojunk=False)
    changes, unchanged = [], 0
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for i, j in zip(range(i1, i2), range(j1, j2)):
                if a[i] == b[j]:
                    unchanged += 1
                else:
                    changes.append({"kind": "numbers", "old": a[i], "new": b[j], "ops": word_ops(a[i], b[j])})
            continue
        olds, news = list(range(i1, i2)), list(range(j1, j2))
        used = set()
        for i in olds:
            best, br = None, pair_ratio
            for j in news:
                if j in used:
                    continue
                la, lb = len(aw[i]), len(bw[j])
                if not la or not lb or 2 * min(la, lb) / (la + lb) < br:
                    continue
                m = difflib.SequenceMatcher(None, aw[i], bw[j], autojunk=False)
                if m.quick_ratio() >= br:
                    r = m.ratio()
                    if r >= br:
                        best, br = j, r
            if best is None:
                changes.append({"kind": "removed", "old": a[i], "new": None})
            else:
                used.add(best)
                kind = "numbers" if am[i] == bm[best] else "modified"
                changes.append({"kind": kind, "old": a[i], "new": b[best], "ops": word_ops(a[i], b[best]),
                                "similarity": round(br, 3)})
        for j in news:
            if j not in used:
                changes.append({"kind": "added", "old": None, "new": b[j]})
    for c in changes:
        c["score"] = score(c["kind"], c.get("old") or "", c.get("new") or "", c.get("ops"))
        c["signals"] = signals(" ".join(t for op, t in c.get("ops", []) if op != "=") if c.get("ops") else (c.get("new") or c.get("old") or ""))
    return changes, unchanged


def compare(old_text, new_text, form, max_changes=120):
    """Section-by-section redline of two filings of the same form."""
    old_items, new_items = split_items(old_text, form), split_items(new_text, form)
    fin = "8" if form.startswith("10-K") else "I-1"
    sections = []
    for code, title, _ in (ITEMS_10K if form.startswith("10-K") else ITEMS_10Q):
        if code == fin:
            continue
        o, n = old_items.get(code), new_items.get(code)
        if not o and not n:
            continue
        sections.append(_section(f"item:{code}", f"Item {code.split('-')[-1]}. {title}", o and o["paras"], n and n["paras"], max_changes))
    on = split_notes((old_items.get(fin) or {}).get("paras") or [])
    nn = split_notes((new_items.get(fin) or {}).get("paras") or [])
    matched_old = set()
    for n_num, n_title, n_topic, n_paras in nn:
        best = None
        for k, (o_num, o_title, o_topic, o_paras) in enumerate(on):
            if k in matched_old:
                continue
            r = difflib.SequenceMatcher(None, o_title.lower(), n_title.lower()).ratio()
            if r >= 0.75 or (n_topic and n_topic == o_topic and r >= 0.4):
                if not best or r > best[1]:
                    best = (k, r)
        o_paras = None
        if best:
            matched_old.add(best[0])
            o_paras = on[best[0]][3]
        s = _section(f"note:{n_topic or n_num}", f"Note {n_num}. {n_title}", o_paras, n_paras, max_changes)
        s["topic"] = n_topic
        if not best:
            s["status"] = "new note"
        sections.append(s)
    for k, (o_num, o_title, o_topic, o_paras) in enumerate(on):
        if k not in matched_old:
            s = _section(f"note:{o_topic or o_num}:removed", f"Note {o_num}. {o_title}", o_paras, None, max_changes)
            s["topic"], s["status"] = o_topic, "note removed"
            sections.append(s)
    return {"version": VERSION, "sections": sections,
            "totals": {k: sum(s["counts"][k] for s in sections) for k in ("added", "removed", "modified", "numbers")},
            "parsed": {"old_items": sorted(old_items), "new_items": sorted(new_items), "old_notes": len(on), "new_notes": len(nn)}}


def _section(sid, title, old, new, max_changes):
    if old is None or new is None:
        status = "only in new filing" if old is None else "only in old filing"
        paras = new if old is None else old
        n = len([p for p in paras or [] if len(p) >= 40])
        return {"id": sid, "title": title, "status": status, "counts": {"added": n if old is None else 0, "removed": n if new is None else 0,
                "modified": 0, "numbers": 0}, "unchanged": 0, "changes": [], "score": 0}
    changes, unchanged = diff_paras(old, new)
    counts = {k: sum(1 for c in changes if c["kind"] == k) for k in ("added", "removed", "modified", "numbers")}
    ranked = sorted(changes, key=lambda c: -c["score"])
    keep = set(id(c) for c in ranked[:max_changes])
    kept = [c for c in changes if id(c) in keep]
    lang = [c for c in changes if c["kind"] != "numbers"]
    return {"id": sid, "title": title, "status": "compared", "counts": counts, "unchanged": unchanged,
            "changes": kept, "truncated": max(0, len(changes) - len(kept)),
            "score": sum(c["score"] for c in lang[:40]), "signals": sorted({s for c in lang for s in c["signals"]})}
