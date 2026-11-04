"""Deterministic parsing of earnings-call transcripts and prepared remarks into speakers, turns, sections and Q&A
exchanges. Every turn keeps the character range it came from in the source text (the document's extracted pages
joined with "\\n"), so a quote found inside a turn maps back to an exact position in the original document.

Handles the common layouts: FactSet CallStreet (name line, then "Title, Company" line), S&P Capital IQ (participant
blocks under EXECUTIVES / ANALYSTS, name line then title line), LSEG StreetEvents ("Name - Company - Title"),
colon labels ("AMY HOOD:" or "Amy Hood: text"), dashed labels ("Name -- Title" or "Name – Firm") and line-numbered
PDFs. When the speaker labels cannot be found reliably, `parse` reports low coverage and the caller asks the model
for the label roster (calls.roster_from_llm), then runs `parse` again with those labels.
"""
import collections
import datetime as dt
import re

MONTHS = {m: i + 1 for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}

EXEC_RX = re.compile(r"\b(chief|ceo|cfo|coo|cto|president|chair(man|woman|person)?|officer|vice president|\bvp\b|svp|evp|treasurer|"
                     r"controller|investor relations|head of|general counsel|director,? investor|founder|managing director,? (?!.*(research|securities))|"
                     r"executive|corporate secretary|segment|division president|group president)\b", re.I)
ANALYST_RX = re.compile(r"\b(analyst|research|securities|capital markets|bank|partners|advisors|associates|& co\b|llc|l\.p\.|"
                        r"morgan stanley|goldman|jpmorgan|j\.p\. morgan|ubs|barclays|citi(group|bank)?\b|bofa|bank of america|merrill|"
                        r"wells fargo|jefferies|bernstein|evercore|cowen|piper|raymond james|rbc|bmo|td cowen|deutsche|credit suisse|"
                        r"mizuho|nomura|hsbc|truist|keybanc|stifel|wolfe|oppenheimer|needham|baird|guggenheim|redburn|moffettnathanson|"
                        r"melius|autonomous|kbw|keefe|loop capital|rosenblatt|cantor|d\.a\. davidson|william blair|macquarie|seaport|"
                        r"bairnsfather|exane|mufg|scotia|cibc|wedbush|benchmark|northland|btig|susquehanna|argus|morningstar|"
                        r"atlantic equities|new street|arete|daiwa|smbc|jp morgan|citizens jmp|janney|wolfe research|hsbc)\b", re.I)
OPERATOR_RX = re.compile(r"^(operator|moderator|conference operator|coordinator|presenter|unidentified (speaker|participant)|"
                         r"unknown (speaker|analyst)|voice ?over|host)$", re.I)
QA_HEADER_RX = re.compile(r"^(question[s]?[- ]?(and|&)[- ]?answers?|q\s*&\s*a|questions? (and|&) answers?)( section| session| period)?\s*:?$", re.I)
PREP_HEADER_RX = re.compile(r"^(presentation|management discussion section|prepared remarks|management remarks|opening remarks)\s*:?$", re.I)
QA_START_RX = re.compile(r"(first question|question[- ]and[- ]answer|q\s*&\s*a (session|portion)|open (it |the (call|line|lines|floor) )?(up )?(for|to) (your )?questions|"
                         r"take (your |some )?questions|begin (the )?(question|q&a)|poll for questions|instructions? for asking)", re.I)
NOT_NAME_WORDS = set("""the and of for with our we our this that these those total net revenue income quarter year fiscal results outlook
guidance growth margin operating forward looking statements safe harbor page table contents presentation question answer questions
answers section session call conference participants corporate executives analysts other company overview disclaimer
copyright reserved rights edited transcript event date time note important thank you good morning afternoon evening
summary highlights financial review agenda appendix speakers introduction closing remarks prepared remarks webcast audio
non-gaap gaap reconciliation first second third fourth full ended months three six nine twelve united states america
""".split())

_SPACED = re.compile(r"^(?:[A-Z&] ){3,}[A-Z&](?:\s{2,}(?:[A-Z&] )*[A-Z&])*$")


def strip_md(line):
    """Markdown decoration from firecrawl text copies: headings, bold/italic markers, escaped pipes, bullets."""
    s = (line or "").replace("\\|", "|")
    s = re.sub(r"^\s*#{1,6}\s+", "", s)
    s = re.sub(r"^\s*[•·▪●\-*]\s+(?=[A-Z])", "", s) if re.match(r"^\s*[•·▪●]", s) else s
    s = re.sub(r"(\*\*|__)(.+?)\1", r"\2", s)
    s = re.sub(r"(?<![\w*])\*(?!\s)([^*]+?)\*(?!\w)", r"\1", s)
    return s.strip()


def unspace(line):
    """'Q U E S T I O N S  A N D  A N S W E R S' -> 'QUESTIONS AND ANSWERS'."""
    if _SPACED.match(line.strip()):
        return " ".join(w.replace(" ", "") for w in re.split(r"\s{2,}", line.strip()))
    return line


def name_key(name):
    n = re.sub(r"\([^)]*\)", " ", name or "")
    n = re.sub(r"\b(mr|mrs|ms|dr|prof)\.?\s+", " ", n, flags=re.I)
    n = re.sub(r"[^a-z ]", "", n.lower())
    toks = [t for t in n.split() if len(t) > 1 or t == n.strip()]
    if len(toks) >= 2:
        return toks[0] + " " + toks[-1]
    return " ".join(toks)


def tidy_name(name):
    name = re.sub(r"\s+", " ", (name or "").strip(" :-–—,.\t")).strip()
    if name.isupper() and len(name) > 3:
        name = " ".join(w if len(w) <= 2 and w.endswith(".") else (w[0] + w[1:].lower() if not re.match(r"^(II|III|IV|JR\.?|SR\.?)$", w) else w) for w in name.split())
        name = re.sub(r"\bMc([a-z])", lambda m: "Mc" + m.group(1).upper(), name)
        name = re.sub(r"(?<=['’-])([a-z])", lambda m: m.group(1).upper(), name)
    return name


def looks_like_name(s):
    s = s.strip(" :")
    if OPERATOR_RX.match(s):
        return True
    if not (3 <= len(s) <= 48) or re.search(r"[0-9@$%;!?/|]", s):
        return False
    toks = s.replace(",", " ").split()
    if not (2 <= len(toks) <= 6):
        return False
    caps = 0
    for t in toks:
        tl = t.lower().strip(".")
        if tl in NOT_NAME_WORDS:
            return False
        if re.match(r"^[A-Z][A-Za-z'’.\-]*$", t) or re.match(r"^(de|da|van|von|der|del|la|le|di|du|al|bin|el)$", t) or re.match(r"^[A-Z]\.?$", t) or re.match(r"^(Jr|Sr|II|III|IV)\.?$", t):
            caps += 1
        else:
            return False
    return caps == len(toks)


def find_dates(text):
    out = []
    for m in re.finditer(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(20\d\d|19\d\d)\b", text, re.I):
        out.append((m.start(), int(m.group(3)), MONTHS[m.group(1).lower()[:3]], int(m.group(2))))
    for m in re.finditer(r"\b(\d{1,2})[- ](jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*[- ,]+(20\d\d)\b", text, re.I):
        out.append((m.start(), int(m.group(3)), MONTHS[m.group(2).lower()[:3]], int(m.group(1))))
    for m in re.finditer(r"\b(20\d\d)-(\d\d)-(\d\d)\b", text):
        out.append((m.start(), int(m.group(1)), int(m.group(2)), int(m.group(3))))
    res = []
    for pos, y, mo, d in sorted(out):
        try:
            res.append((pos, dt.date(y, mo, d)))
        except ValueError:
            pass
    return res


class Doc:
    """Source text with per-line offsets, boilerplate flags and a clean text that maps back to the source."""

    def __init__(self, pages):
        self.pages = [p or "" for p in pages]
        self.full = "\n".join(self.pages)
        self.page_starts = []
        pos = 0
        for p in self.pages:
            self.page_starts.append(pos)
            pos += len(p) + 1
        self.lines = []  # (start, end, page, content_start)
        pos = 0
        for pi, p in enumerate(self.pages):
            for ln in p.split("\n"):
                self.lines.append([pos, pos + len(ln), pi, pos])
                pos += len(ln) + 1
        self._strip_line_numbers()
        self._mark_boiler()

    def text(self, i):
        s, e, _p, cs = self.lines[i]
        return self.full[cs:e]

    def page_of(self, off):
        lo, hi = 0, len(self.page_starts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self.page_starts[mid] <= off:
                lo = mid
            else:
                hi = mid - 1
        return lo + 1

    def _strip_line_numbers(self):
        """PDFs printed with line numbers ("12 Welcome to ...")."""
        nonempty = [ln for ln in self.lines if self.full[ln[0]:ln[1]].strip()]
        if not nonempty:
            return
        numbered = sum(1 for ln in nonempty if re.match(r"^\s*\d{1,4}(\s|$)", self.full[ln[0]:ln[1]]))
        if numbered / len(nonempty) < 0.6:
            return
        for ln in self.lines:
            m = re.match(r"^\s*\d{1,4}(\s+|$)", self.full[ln[0]:ln[1]])
            if m:
                ln[3] = ln[0] + m.end()

    def _mark_boiler(self):
        npages = len(self.pages)
        norm = lambda s: re.sub(r"\d+", "#", re.sub(r"\s+", " ", s.strip().lower()))
        seen = collections.defaultdict(set)
        for i, ln in enumerate(self.lines):
            t = norm(self.text(i))
            if t:
                seen[t].add(ln[2])
        rep = {t for t, ps in seen.items() if npages >= 3 and len(ps) >= max(3, 0.3 * npages) and len(t) < 200}
        self.boiler = []
        for i in range(len(self.lines)):
            raw = self.text(i).strip()
            t = norm(raw)
            b = (t in rep and not looks_like_name(raw) and not OPERATOR_RX.match(raw)) or bool(re.match(r"^[.\-_=·•…\s]{8,}$", raw)) \
                or bool(re.match(r"^(page )?#( of #)?$", t)) or bool(re.search(r"copyright|all rights reserved|www\.callstreet\.com|spglobal\.com/marketintelligence|1-877-factset|lseg streetevents \|", t))
            self.boiler.append(b)

    def clean(self, a, b):
        """Clean text of lines [a, b) and a list mapping each clean char to its source offset."""
        out, cmap = [], []
        for i in range(a, b):
            if self.boiler[i]:
                continue
            s, e, _p, cs = self.lines[i]
            seg = self.full[cs:e]
            if out:
                out.append("\n")
                cmap.append(cmap[-1] + 1 if cmap else cs)
            out.append(seg)
            cmap.extend(range(cs, e))
        return "".join(out), cmap


# ---------------------------------------------------------------- speaker labels

_DASH = r"(?:\s+(?:-{1,2}|–|—)\s+|\s*(?:–|—)\s*)"


def label_of(line, roster_names):
    """(name, descriptor, inline_text, form) when a line is a speaker label, else None."""
    raw = unspace(strip_md(line)).strip()
    if not raw or len(raw) > 220:
        return None
    m = re.match(r"^([QA])\s*[-–—]\s*([A-Z][A-Za-z.'’\- ]{2,60})$", raw)  # Bloomberg "Q - Laurent Favre" / "A - Sanjiv Lamba"
    if m and looks_like_name(m.group(2)):
        return m.group(2).strip(), "", "", "qa_" + m.group(1)
    m = re.match(r"^([A-Z][A-Za-z.'’\- ]{1,60}?)\^\s*(.*)$", raw)  # "Gary Smith^ Amit, let me start"
    if m and (looks_like_name(m.group(1)) or OPERATOR_RX.match(m.group(1).strip())):
        return m.group(1).strip(), "", m.group(2).strip(), "colon"
    m = re.match(r"^([A-Z][A-Za-z.'’\- ]{1,60}?|[A-Z][A-Z.'’\- ]{2,60}?)\s*(?:\(([^)]{2,80})\)|,\s*([^:]{2,90}?)|" + _DASH + r"([^:]{2,120}?))?\s*:\s*(.*)$", raw)
    if m:
        name, desc, inline = m.group(1).strip(), (m.group(2) or m.group(3) or m.group(4) or "").strip(), m.group(5).strip()
        if looks_like_name(name) or OPERATOR_RX.match(name) or (roster_names and name_key(name) in roster_names):
            if inline and not desc and not (roster_names and name_key(name) in roster_names) and not OPERATOR_RX.match(name):
                # "Revenue Growth: 5%" style lines are not labels unless the name is a known speaker
                # accepted only when the same name labels at least two passages (see parse)
                if not name.isupper() or len(name.split()) < 2:
                    return name, desc, inline, "icolon"
            return name, desc, inline, "colon"
    m = re.match(r"^([A-Z][A-Za-z.'’\- ]{2,60}?)" + _DASH + r"(.{2,160})$", raw)
    if m and (looks_like_name(m.group(1)) or OPERATOR_RX.match(m.group(1).strip())) and not re.search(r"[.?!]\s*$", raw.rstrip(".") + ("" if raw.endswith("Inc.") or raw.endswith("Co.") or raw.endswith("Ltd.") or raw.endswith("Corp.") or raw.endswith("L.P.") else (raw[-1] if raw[-1] in "?!" else ""))):
        return m.group(1).strip(), m.group(2).strip(), "", "dash"
    m = re.match(r"^([A-Z][A-Za-z.'’\- ]{2,50}?)\s*,\s*(.{3,140})$", raw)
    if m and looks_like_name(m.group(1)) and (EXEC_RX.search(m.group(2)) or re.search(r"\banalyst\b", m.group(2), re.I)):
        return m.group(1).strip(), m.group(2).strip(), "", "dash"
    if looks_like_name(raw) or OPERATOR_RX.match(raw):
        return raw, "", "", "bare"
    return None


def participants(doc, nlines):
    """Names, descriptors and management/analyst flags from participant blocks near the top of the document."""
    out = {}
    mode = None
    limit = min(nlines, max(120, nlines // 6))
    i = 0
    while i < limit:
        t = unspace(strip_md(doc.text(i))).strip()
        u = re.sub(r"\s+", " ", t).upper().rstrip(":")
        if re.match(r"^(CORPORATE PARTICIPANTS|EXECUTIVES|COMPANY PARTICIPANTS|MANAGEMENT|COMPANY SPEAKERS|.*SPEAKERS:?)$", u) and len(u) < 40:
            mode = "management"
        elif re.match(r"^(CONFERENCE CALL PARTICIPANTS|OTHER PARTICIPANTS|ANALYSTS|CALL PARTICIPANTS|PARTICIPANTS):?$", u):
            mode = "analyst" if u != "CALL PARTICIPANTS" else None
        elif PREP_HEADER_RX.match(t) or QA_HEADER_RX.match(t) or u in ("PRESENTATION", "MANAGEMENT DISCUSSION SECTION"):
            if out:
                break
            mode = None
        elif mode and t and not doc.boiler[i]:
            m = re.match(r"^([A-Z][A-Za-z.'’\- ]{2,50}?)\s+(.{3,140}?)\s+-\s+(.{2,140})$", t)  # LSEG "Name Company - Title"
            b = [x.strip() for x in t.split(";")]
            c2 = re.match(r"^([A-Z][A-Za-z.'’\- ]{2,50}?)\s*,\s*(.{3,140})$", t)
            if len(b) >= 2 and looks_like_name(b[0]):  # "Name; Firm; Title"
                firm, role = (b[1], "; ".join(b[2:])) if len(b) > 2 else (None, b[1])
                out[name_key(b[0])] = {"name": tidy_name(b[0]), "desc": t, "kind": mode, "firm": firm, "role": role or None}
            elif c2 and looks_like_name(c2.group(1)) and (EXEC_RX.search(c2.group(2)) or ANALYST_RX.search(c2.group(2))):
                out[name_key(c2.group(1))] = {"name": tidy_name(c2.group(1)), "desc": c2.group(2), "kind": mode}
            elif m and looks_like_name(m.group(1)) and not looks_like_name(t):
                out[name_key(m.group(1))] = {"name": tidy_name(m.group(1)), "desc": f"{m.group(3)}, {m.group(2)}", "kind": mode, "firm": m.group(2), "role": m.group(3)}
            elif looks_like_name(t):
                desc = ""
                if i + 1 < limit:
                    nxt = strip_md(doc.text(i + 1)).strip()
                    if nxt and not looks_like_name(nxt) and len(nxt) < 160 and not QA_HEADER_RX.match(nxt) and not PREP_HEADER_RX.match(nxt) and not re.match(r"^[.\-_\s]{8,}$", nxt):
                        desc = nxt
                        # S&P puts analyst firms on two lines ("Keefe, Bruyette, & Woods, Inc.," / "Research Division")
                        if i + 2 < limit and re.match(r"^research division$", doc.text(i + 2).strip(), re.I):
                            desc += " " + doc.text(i + 2).strip()
                            i += 1
                        i += 1
                out[name_key(t)] = {"name": tidy_name(t), "desc": desc, "kind": mode}
        i += 1
    return out


def split_desc(desc, company_words):
    """(role, firm) from a descriptor such as 'Analyst, Keefe, Bruyette & Woods, Inc.', 'Morgan Stanley - Analyst',
    'Executive VP & CFO' or 'President, Chief Executive Officer & Director, Aon Plc'."""
    d = re.sub(r"\s+", " ", desc or "").strip(" ,-–—:")
    d = re.sub(r",?\s*Research Division$", "", d, flags=re.I)
    if not d:
        return None, None
    parts = [p.strip() for p in re.split(_DASH + r"|\s+-\s+", d) if p.strip()]
    if len(parts) >= 2:
        # "Company - Title" or "Title - Company"
        a, b = parts[0], " - ".join(parts[1:])
        if re.search(r"analyst|officer|president|vp|chief|director|head|treasurer|relations|chair|ceo|cfo|manager|partner|founder|controller", b, re.I) and not re.search(r"analyst|officer|president|chief|director|head|relations|chair|ceo|cfo", a, re.I):
            return b, a
        return a, b
    m = re.match(r"^(Analyst|Research Analyst|Senior Analyst|Equity Analyst|MD|Managing Director)\s*,\s*(.+)$", d, re.I)
    if m:
        return m.group(1), m.group(2)
    # "Title, Company" where the company is the last comma group that looks like an organisation
    m = re.match(r"^(.*\b(?:Officer|President|CEO|CFO|COO|Director|Relations|Treasurer|Controller|Chairman|Chair|Head[^,]*|VP|Counsel|Secretary|Executive|Manager|Founder|Strategy|Finance|Operations)\b)\s*,\s*([^,]{2,}(?:,\s*(?:Inc|Corp|Co|Ltd|LLC|Plc|N\.V|S\.A|L\.P)\.?)?)$", d)
    if m:
        return m.group(1), m.group(2)
    if ANALYST_RX.search(d) and not EXEC_RX.search(d):
        return "Analyst", d
    if company_words and any(w in d.lower() for w in company_words) and not EXEC_RX.search(d):
        return None, d
    return d, None


def speaker_kind(name, role, firm, company_words, hint=None):
    if OPERATOR_RX.match(name or ""):
        return "operator"
    blob = f"{role or ''} {firm or ''}".lower()
    own = bool(company_words) and any(w in blob for w in company_words)
    if hint == "management" or own:
        return "management"
    if hint == "analyst" or re.search(r"\banalyst\b", blob) or (firm and ANALYST_RX.search(firm)):
        return "analyst"
    if role and EXEC_RX.search(role):
        return "management"
    return "other"


def company_words_of(name, display=None):
    words = set()
    for n in (display, name):
        if not n:
            continue
        toks = [t for t in re.findall(r"[a-z][a-z&'.]+", n.lower()) if t not in ("inc", "inc.", "corp", "corporation", "company", "co", "co.", "the", "group", "holdings", "plc", "ltd", "international", "technologies", "global", "&", "and", "of", "systems", "services", "brands", "financial")]
        if toks:
            words.add(toks[0])
    return {w for w in words if len(w) >= 3}


def parse(pages, company_name=None, display_name=None, roster_hint=None):
    """Parses one document. roster_hint: optional model-provided labels [{label, name, role, firm, kind}] used when the
    deterministic pass found too little structure. Returns a dict with speakers, turns, exchanges and quality stats."""
    doc = Doc(pages)
    n = len(doc.lines)
    cw = company_words_of(company_name, display_name)
    parts = participants(doc, n)
    hint_by_label = {}
    for h in roster_hint or []:
        if h.get("label"):
            hint_by_label[re.sub(r"\s+", " ", h["label"]).strip().rstrip(":").strip().lower()] = h
    hint_by_key = {name_key(h.get("name") or ""): h for h in roster_hint or [] if h.get("name")}

    # pass 1: candidate labels and their counts
    cands = []
    for i in range(n):
        if doc.boiler[i]:
            continue
        t = doc.text(i)
        if not t.strip():
            continue
        if hint_by_label:
            st = re.sub(r"\s+", " ", unspace(strip_md(t))).strip()
            low = st.lower()
            hit = None
            for lab, h in hint_by_label.items():
                if low == lab or low.startswith(lab + ":") or (low.startswith(lab) and len(low) > len(lab) and low[len(lab)] in " :-–—(" and (st[len(lab):].lstrip().startswith((":", "-", "–", "—", "(")) or low == lab)):
                    if hit is None or len(lab) > len(hit[0]):
                        hit = (lab, h)
            if hit:
                lab, h = hit
                rest = st[len(lab):].lstrip(" :")
                inline = rest if st[len(lab):].lstrip().startswith(":") else ""
                cands.append((i, h.get("name") or tidy_name(st[:len(lab)]), "", inline, "hint", h))
                continue
        lab = label_of(t, set(parts))
        if lab:
            cands.append((i, lab[0], lab[1], lab[2], lab[3], None))
    counts = collections.Counter(name_key(c[1]) for c in cands)
    forms = collections.Counter(c[4] for c in cands if counts[name_key(c[1])] >= 2 or name_key(c[1]) in parts)

    def accepted(c):
        k = name_key(c[1])
        if c[4] == "hint":
            return True
        if OPERATOR_RX.match(c[1].strip()):
            return True
        if k in parts or c[4] in ("qa_Q", "qa_A"):
            return True
        if c[4] == "icolon":
            return counts[k] >= 2
        if counts[k] >= 2:
            # bare name lines are common in prose headers; require them to recur and to be followed by speech
            if c[4] == "bare":
                return forms["bare"] >= 3 or forms["bare"] >= forms["colon"] + forms["dash"]
            return True
        return c[4] == "colon" and bool(c[3]) is False and c[1].isupper()

    labels = [c for c in cands if accepted(c)]
    # drop label lines inside the participant listing (they are not turns): a label is a turn when speech follows it
    turns_raw = []
    for j, c in enumerate(labels):
        i = c[0]
        nxt = labels[j + 1][0] if j + 1 < len(labels) else n
        a = i + 1
        desc = c[2]
        # FactSet / S&P: the line after a bare name is the title/firm descriptor
        if c[4] == "bare" and a < nxt:
            k = name_key(c[1])
            d1 = strip_md(doc.text(a)).strip()
            known = parts.get(k, {}).get("desc", "")
            if d1 and (d1 == known or (len(d1) < 140 and not re.search(r"[.?!]\"?$", d1) and (ANALYST_RX.search(d1) or EXEC_RX.search(d1) or (cw and any(w in d1.lower() for w in cw))))):
                desc = d1
                a += 1
                if a < nxt and re.match(r"^research division$", doc.text(a).strip(), re.I):
                    a += 1
        text, cmap = doc.clean(a, nxt)
        if c[3]:
            pos = doc.text(i).find(c[3])
            if pos < 0:
                pos = doc.text(i).find(c[3][:40])
            if pos < 0:
                pos = max(0, len(doc.text(i)) - len(c[3]))
            s0 = doc.lines[i][3] + pos
            inline = c[3]
            if text:
                text = inline + "\n" + text
                cmap = list(range(s0, s0 + len(inline))) + [s0 + len(inline)] + cmap
            else:
                text, cmap = inline, list(range(s0, s0 + len(inline)))
        lead = len(text) - len(text.lstrip())
        text, cmap = text[lead:], cmap[lead:]
        trail = len(text.rstrip())
        text, cmap = text[:trail], cmap[:trail]
        turns_raw.append({"line": i, "name": c[1], "desc": desc, "hint": c[5], "text": text, "cmap": cmap, "form": c[4]})
    # participant listings produce labels with no speech; drop empty turns
    turns_raw = [t for t in turns_raw if len(t["text"].split()) >= 1]

    # speakers
    speakers, sidx = [], {}
    for t in turns_raw:
        k = name_key(t["name"])
        if OPERATOR_RX.match(t["name"].strip()):
            k = "operator"
        if k not in sidx:
            p = parts.get(k, {})
            h = t["hint"] or hint_by_key.get(k) or {}
            if h:
                role, firm = h.get("role"), h.get("firm")
                kind = h.get("kind") if h.get("kind") in ("management", "analyst", "operator", "other") else None
            else:
                role, firm = (p.get("role"), p.get("firm")) if p.get("role") else split_desc(p.get("desc") or t["desc"], cw)
                kind = None
            fhint = {"qa_Q": "analyst", "qa_A": "management"}.get(t["form"])
            kind = kind or speaker_kind(t["name"], role, firm, cw, p.get("kind") or fhint)
            if kind == "management" and not firm:
                firm = display_name or company_name
            sidx[k] = len(speakers)
            speakers.append({"idx": len(speakers), "name": "Operator" if k == "operator" else tidy_name(h.get("name") or p.get("name") or t["name"]),
                             "role": (role or None) and role[:120], "firm": (firm or None) and firm[:120], "kind": kind})
        else:
            sp = speakers[sidx[k]]
            if not sp["role"] and not sp["firm"] and t["desc"] and not t["hint"]:
                sp["role"], sp["firm"] = split_desc(t["desc"], cw)
                if sp["kind"] == "other":
                    sp["kind"] = speaker_kind(sp["name"], sp["role"], sp["firm"], cw)
        t["speaker"] = sidx[k]

    # sections: explicit header, else operator opening the Q&A, else the first analyst turn
    qa_line = None
    for i in range(n):
        if not doc.boiler[i] and QA_HEADER_RX.match(unspace(strip_md(doc.text(i))).strip()):
            # skip table-of-contents entries near the top
            if turns_raw and i > turns_raw[0]["line"]:
                qa_line = i
                break
    qa_turn = None
    if qa_line is not None:
        qa_turn = next((j for j, t in enumerate(turns_raw) if t["line"] > qa_line), None)
    if qa_turn is None:
        for j, t in enumerate(turns_raw):
            sp = speakers[t["speaker"]]
            if j > 0 and sp["kind"] == "operator" and QA_START_RX.search(t["text"]):
                qa_turn = j
                break
            if sp["kind"] == "analyst" and j > 0 and any(speakers[x["speaker"]]["kind"] == "management" for x in turns_raw[:j]):
                qa_turn = j
                break
            if sp["kind"] == "management" and j > 0 and QA_START_RX.search(t["text"][-400:]) and j + 1 < len(turns_raw):
                qa_turn = j + 1
                break
    turns = []
    for j, t in enumerate(turns_raw):
        s = t["cmap"][0] if t["cmap"] else doc.lines[t["line"]][3]
        e = (t["cmap"][-1] + 1) if t["cmap"] else s
        turns.append({"idx": j, "speaker": t["speaker"], "section": "qa" if qa_turn is not None and j >= qa_turn else "prepared",
                      "s": s, "e": e, "page": doc.page_of(s), "words": len(t["text"].split()), "text": t["text"], "cmap": t["cmap"]})

    # unresolved speakers: someone who speaks in the prepared remarks, or talks far more than the questioners, is
    # management; the rest ask questions
    for sp in speakers:
        if sp["kind"] != "other":
            continue
        mine = [t for t in turns if t["speaker"] == sp["idx"]]
        prep = any(t["section"] == "prepared" for t in mine) and qa_turn is not None
        an_words = [sum(t["words"] for t in turns if t["speaker"] == o["idx"]) for o in speakers if o["kind"] == "analyst"]
        avg = sum(an_words) / len(an_words) if an_words else 150
        if prep or sum(t["words"] for t in mine) > 4 * avg:
            sp["kind"] = "management"
            sp["firm"] = sp.get("firm") or display_name or company_name
    exchanges = build_exchanges(turns, speakers)
    total_words = len(re.findall(r"\S+", " ".join(doc.clean(0, n)[0].split())))
    turn_words = sum(t["words"] for t in turns)
    return {"speakers": speakers, "turns": turns, "exchanges": exchanges, "doc": doc,
            "quality": {"turns": len(turns), "speakers": len(speakers), "coverage": round(turn_words / total_words, 3) if total_words else 0,
                        "words": total_words, "qa": qa_turn is not None, "forms": dict(forms), "participants": len(parts)}}


IR_RX = re.compile(r"investor relations|\bIR\b|treasurer|corporate development", re.I)


def build_exchanges(turns, speakers):
    """Groups the Q&A section into exchanges: an analyst's question (and follow-ups) and management's answers."""
    ex, cur = [], None
    for t in turns:
        if t["section"] != "qa":
            continue
        sp = speakers[t["speaker"]]
        k = sp["kind"]
        if k == "operator":
            if cur and cur["a_turns"]:
                cur = None
            continue
        if k in ("analyst", "other"):
            if cur and cur["analyst"] == sp["idx"]:
                cur["q_turns"].append(t["idx"])
            else:
                cur = {"idx": len(ex), "analyst": sp["idx"], "q_turns": [t["idx"]], "a_turns": []}
                ex.append(cur)
            continue
        # management: an IR officer reading out a submitted question opens a new exchange
        if IR_RX.search(sp.get("role") or "") and re.search(r"\b(question|next one|from)\b", t["text"][:300], re.I) and re.search(r"\?", t["text"]) and (cur is None or cur["a_turns"]):
            cur = {"idx": len(ex), "analyst": None, "asked_by": sp["idx"], "q_turns": [t["idx"]], "a_turns": []}
            ex.append(cur)
            continue
        if cur is not None:
            cur["a_turns"].append(t["idx"])
    return [e for e in ex if e["q_turns"]]


# ---------------------------------------------------------------- classification

EARNINGS_RX = re.compile(r"earnings (conference )?call|earnings (webcast|release)|quarter(ly)? (\d{4} )?(earnings|results|financial results)|"
                         r"(first|second|third|fourth|1st|2nd|3rd|4th)[- ]quarter|\bq[1-4]\b|\bfq[1-4]\b|full[- ]year (\d{4} )?results|year[- ]end results|"
                         r"results conference call|financial results conference|quarter (fiscal )?(\d{4}|fy\d\d) results", re.I)
INVESTOR_DAY_RX = re.compile(r"investor (day|forum|update|meeting|conference)|analyst (day|meeting)|capital markets day|strategy (day|update)|business update", re.I)
CONFERENCE_RX = re.compile(r"\bconference\b|symposium|summit|fireside|forum|\bat (the )?[A-Z][\w&]+ .*(conference|summit)|investor (meeting|event)", re.I)
LETTER_RX = re.compile(r"(shareholder|stockholder|investor)s?['’]? letter|letter to (our )?(shareholders|stockholders|owners)|dear (fellow )?(shareholders|stockholders|owners)", re.I)
REMARKS_RX = re.compile(r"prepared remarks|cfo commentary|commentary|script|remarks", re.I)
RELEASE_RX = re.compile(r"reports? (first|second|third|fourth|q[1-4]|fiscal|full[- ]year|record|\d{4})|announces (first|second|third|fourth|.*results)|"
                        r"condensed consolidated (statements|balance)|press release|for immediate release|news release", re.I)


def event_type(head, title):
    h = f"{title or ''}\n{head}"
    if re.search(r"earnings (conference )?call|results conference call|earnings call transcript|financial results conference call", h, re.I):
        return "earnings"
    if INVESTOR_DAY_RX.search(h):
        return "investor_day"
    if re.search(r"conference|symposium|summit|fireside", h, re.I) and not re.search(r"earnings conference|conference call", h, re.I):
        return "conference"
    if EARNINGS_RX.search(h):
        return "earnings"
    return "other"


def classify(doc_row, pages, parsed):
    """('transcript' | 'remarks' | None, reason). None for shareholder letters, releases and slide decks."""
    head = "\n".join(pages)[:6000]
    title = doc_row.get("title") or ""
    q = parsed["quality"]
    sp = parsed["speakers"]
    turns = parsed["turns"]
    n_mgmt = sum(1 for s in sp if s["kind"] == "management")
    n_an = sum(1 for s in sp if s["kind"] == "analyst")
    if q["words"] < 600:
        return None, "too short"
    if len(turns) >= 6 and q["coverage"] >= 0.5 and parsed["exchanges"] and (n_an >= 1 or q["qa"]):
        return "transcript", "speaker turns with Q&A"
    if LETTER_RX.search(head[:3000]) and not re.search(r"operator|question", head, re.I):
        return None, "shareholder letter"
    # slide decks and releases: short lines, little running prose
    lines = [ln for ln in "\n".join(pages).split("\n") if ln.strip()]
    prose = sum(len(ln.split()) for ln in lines if len(ln) >= 60) / max(1, sum(len(ln.split()) for ln in lines))
    deckish = doc_row.get("doc_type") in ("presentation", "supplement", "release", "annual_report") and not re.search(r"transcript|remarks|script|commentary", title + " " + (doc_row.get("url") or ""), re.I)
    mgmt_words = max([sum(t["words"] for t in turns if t["speaker"] == s["idx"]) for s in sp if s["kind"] == "management"] or [0])
    if len(turns) >= 2 and q["coverage"] >= (0.85 if deckish else 0.6) and n_mgmt >= 1 and mgmt_words >= 400 and prose >= 0.5:
        return "remarks", "speaker turns, no Q&A"
    if not deckish and prose >= 0.6 and REMARKS_RX.search(title + " " + (doc_row.get("url") or "") + " " + head[:1500]) and not RELEASE_RX.search(head[:2500]):
        return "remarks", "written remarks without speaker labels"
    return None, "no speaker structure"
