"""Congressional hearing transcripts (GovInfo CHRG collection) and verbatim quotes that name a covered company.

Discovery uses GovInfo's public CHRG sitemaps (https://www.govinfo.gov/sitemap/CHRG_{YEAR}_sitemap.xml), which
need no API key; api.govinfo.gov with DEMO_KEY rate-limits quickly from shared IPs and is used only as a fallback
for nothing. Per hearing the pipeline reads:
- the MODS record (metadata/pkg/{pkg}/mods.xml): title, held date, chamber, committees, witnesses, members;
- the HTML rendition (content/pkg/{pkg}/html/{pkg}.htm): the printed transcript as plain text.
The transcript text is archived gzipped under govraw/chrg/{pkg}.txt in the private raw bucket.

Quotes: each mention of a company alias is cut from its paragraph at sentence boundaries, attributed to the speaker
whose turn the paragraph belongs to (GPO marks a turn as "Mr. Smith." / "Senator Warren." / "Chairman Wicker." at the
start of a paragraph; prepared statements by their "STATEMENT OF ..." heading). The quote is then re-checked against
the archived text after whitespace/quote normalisation; anything that fails is dropped.
"""
import html as htmllib
import re
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor

from .common import now_iso
from .gov_common import fetch, get_raw, get_raw_json, norm_text, put_raw, put_raw_json, verbatim, Deadline

SITEMAP = "https://www.govinfo.gov/sitemap/CHRG_{year}_sitemap.xml"
MODS = "https://www.govinfo.gov/metadata/pkg/{pkg}/mods.xml"
HTML = "https://www.govinfo.gov/content/pkg/{pkg}/html/{pkg}.htm"
DETAILS = "https://www.govinfo.gov/app/details/{pkg}"
INDEX = "govraw/chrg/index.json"
NS = {"m": "http://www.loc.gov/mods/v3"}
GAP = 0.12  # seconds between govinfo requests per process

SECTOR_TOPICS = {
    "Information Technology": r"semiconductor|chips?\b|artificial intelligence|\bAI\b|cyber|technology|software|data privacy|broadband|quantum|cloud",
    "Communication Services": r"telecommunication|broadband|spectrum|social media|online|media|internet|television|wireless|children online",
    "Health Care": r"drug|pharmac|health|medicare|medicaid|hospital|biomedical|FDA|insulin|PBM|pharmacy benefit|medical device|vaccine",
    "Financials": r"bank|financial|insurance|capital market|securities|SEC\b|crypto|digital asset|stablecoin|credit|lending|mortgage|payment",
    "Energy": r"energy|oil|natural gas|pipeline|LNG|petroleum|offshore|drilling|refin",
    "Utilities": r"electric|grid|utilit|power|nuclear|hydropower|transmission",
    "Materials": r"mining|critical mineral|chemical|steel|aluminum|copper|lithium|forest|timber|fertilizer",
    "Industrials": r"aviation|airline|rail|infrastructure|manufactur|trucking|shipping|freight|aerospace|defense|construction|supply chain",
    "Consumer Discretionary": r"consumer|retail|automo|vehicle|travel|tourism|e-commerce|restaurant|toy|gaming",
    "Consumer Staples": r"food|agricultur|farm|nutrition|tobacco|beverage|grocery|SNAP",
    "Real Estate": r"housing|real estate|mortgage|commercial property|REIT|homeownership|rent",
}
DEFENSE_TOPIC = r"defense|NDAA|military|armed services|weapon|munition|shipbuilding|missile|Pentagon|national security"


# ---------------------------------------------------------------- corpus

def list_packages(years, log=print):
    out = {}
    for y in years:
        try:
            x = fetch(SITEMAP.format(year=y), host_gap=GAP, timeout=90)
        except Exception as e:
            log(f"chrg sitemap {y} failed: {str(e)[:120]}")
            continue
        for loc, mod in re.findall(r"<loc>https://www\.govinfo\.gov/app/details/(CHRG-[^<]+)</loc>\s*(?:<lastmod>([^<]*)</lastmod>)?", x):
            out[loc] = mod or ""
    return out


def _t(node, path):
    el = node.find(path, NS)
    return (el.text or "").strip() if el is not None and el.text else None


def parse_mods(xml_text):
    root = ET.fromstring(xml_text.encode() if isinstance(xml_text, str) else xml_text)
    ext = {}
    committees, members, witnesses = [], [], []
    for e in root.findall("m:extension", NS):
        for k in ("heldDate", "chamber", "congress", "session", "docClass"):
            v = _t(e, f"m:{k}")
            if v and k not in ext:
                ext[k] = v
        for c in e.findall("m:congCommittee", NS):
            names = {n.get("type"): (n.text or "").strip() for n in c.findall("m:name", NS)}
            subs = [(_t(s, "m:name") or "") for s in c.findall("m:subCommittee", NS)]
            aid = (c.get("authorityId") or "")
            committees.append({"id": aid[:4].upper() if aid else None, "authority_id": aid, "chamber": c.get("chamber"),
                               "name": names.get("authority-standard") or names.get("authority-short"),
                               "subcommittees": [s for s in subs if s]})
        for m in e.findall("m:congMember", NS):
            names = {n.get("type"): (n.text or "").strip() for n in m.findall("m:name", NS)}
            members.append({"bioguide": m.get("bioGuideId"), "party": m.get("party"), "state": m.get("state"), "chamber": m.get("chamber"),
                            "name": names.get("authority-fnf") or names.get("parsed"), "last": (names.get("authority-lnf") or "").split(",")[0]})
        witnesses += [(w.text or "").strip() for w in e.findall("m:witness", NS) if w.text]
    title = None
    for ti in root.findall("m:titleInfo", NS):
        if ti.get("type") is None:
            title = _t(ti, "m:title")
            break
    return {"title": title, "date": ext.get("heldDate") or _t(root, "m:originInfo/m:dateIssued"), "chamber": ext.get("chamber"),
            "congress": ext.get("congress"), "committees": committees, "members": members, "witnesses": witnesses[:60]}


def html_to_text(h):
    m = re.search(r"<pre>(.*)</pre>", h, re.S | re.I)
    body = m.group(1) if m else re.sub(r"<[^>]+>", " ", h)
    body = re.sub(r"<[^>]+>", "", body)
    return htmllib.unescape(body)


def sync_corpus(sb, years, deadline=None, workers=4, log=print, max_new=None):
    """Adds new hearings to the archived corpus. Returns the index {pkg: meta}."""
    index = get_raw_json(sb, INDEX, {"packages": {}}) or {"packages": {}}
    pk = index["packages"]
    listed = list_packages(years, log=log)
    todo = [p for p, mod in sorted(listed.items(), key=lambda kv: kv[1], reverse=True)
            if p not in pk or (pk[p].get("status") == "error") or (mod and pk[p].get("lastmod") and mod > pk[p]["lastmod"])]
    if max_new:
        todo = todo[:max_new]
    log(f"chrg: {len(listed)} listed, {len(todo)} to fetch")

    def one(p):
        rec = {"lastmod": listed.get(p), "retrieved_at": now_iso(), "html_url": HTML.format(pkg=p), "details_url": DETAILS.format(pkg=p)}
        try:
            meta = parse_mods(fetch(MODS.format(pkg=p), host_gap=GAP, timeout=90, retries=3))
            rec.update(meta)
            h = fetch(HTML.format(pkg=p), host_gap=GAP, timeout=120, retries=3)
            text = html_to_text(h)
            put_raw(sb, f"govraw/chrg/{p}.txt", text, "application/gzip")
            rec.update(status="ok", chars=len(text))
        except Exception as e:
            rec.update(status="error", error=str(e)[:200])
        return p, rec

    done = 0
    with ThreadPoolExecutor(workers) as ex:
        futs = [ex.submit(one, p) for p in todo]
        for fu in futs:
            if deadline and deadline.passed(20):
                fu.cancel()
                continue
            p, rec = fu.result()
            pk[p] = rec
            done += 1
            if done % 100 == 0:
                index["updated_at"] = now_iso()
                put_raw_json(sb, INDEX, index)
                log(f"  chrg {done}/{len(todo)}")
    index["updated_at"] = now_iso()
    put_raw_json(sb, INDEX, index)
    return index


# ---------------------------------------------------------------- speakers and quotes

SPEAKER = re.compile(
    r"^\s{2,}((?:Mr|Ms|Mrs|Miss|Dr)\.\s+(?:[A-Z][A-Za-z'\-]+\s){0,2}[A-Z][A-Za-z'\-]+|"
    r"(?:Senator|Chairman|Chairwoman|Chair|Representative|Secretary|General|Admiral|Commissioner|Administrator|Director|Judge|"
    r"Governor|Ambassador|Ranking Member|Vice Chairman|Vice Chair|Delegate|Speaker|Leader)\s+(?:[A-Z][A-Za-z'\-]+\s){0,2}[A-Z][A-Za-z'\-]+|"
    r"The Chairman|The Chairwoman|The Chair|The Clerk|Voice|Chairman|Chairwoman)\.\s")
STATEMENT = re.compile(r"^\s*(?:PREPARED\s+)?STATEMENT\s+OF\s+(?:THE\s+HONORABLE\s+|HON\.\s+)?([A-Z][A-Z.'\- ]{2,60}?)(?:,|\n|$)", re.M)


def paragraphs(text):
    """Splits a GPO transcript into paragraphs: (start offset, end offset)."""
    out, start = [], 0
    for m in re.finditer(r"\n(?=\s{4}\S)|\n\s*\n", text):
        if m.start() > start:
            out.append((start, m.start()))
        start = m.end()
    if start < len(text):
        out.append((start, len(text)))
    return out


def speaker_turns(text):
    """Sorted (offset, speaker label) markers for turns and prepared statements."""
    marks = []
    for s, e in paragraphs(text):
        para = text[s:e]
        m = SPEAKER.match(para if para.startswith("  ") else "    " + para.lstrip("\n"))
        if m:
            marks.append((s, m.group(1).strip()))
            continue
        st = STATEMENT.match(para)
        if st:
            name = " ".join(w.capitalize() if len(w) > 2 else w for w in st.group(1).strip().split())
            marks.append((s, f"Prepared statement of {name}"))
    return marks


def speaker_at(marks, offset):
    lo, hi, best = 0, len(marks) - 1, None
    while lo <= hi:
        mid = (lo + hi) // 2
        if marks[mid][0] <= offset:
            best = marks[mid][1]
            lo = mid + 1
        else:
            hi = mid - 1
    return best


def resolve_speaker(label, members):
    """Links 'Mr. Comer' / 'Senator Warren' to a committee member listed in MODS when the last name is unique."""
    if not label or label.startswith("Prepared statement") or label in ("The Chairman", "The Chair", "Voice", "The Clerk"):
        return None
    last = label.split()[-1].lower()
    cands = [m for m in members if (m.get("last") or "").lower() == last]
    return cands[0] if len(cands) == 1 else None


def sentence_span(text, s, e, max_len=700):
    """Expands [s, e) to the surrounding sentence within its paragraph."""
    para_start = max(text.rfind("\n\n", 0, s), text.rfind("\n    ", 0, s))
    para_start = 0 if para_start < 0 else para_start + 1
    nxt = [x for x in (text.find("\n\n", e), text.find("\n    ", e)) if x >= 0]
    para_end = min(nxt) if nxt else len(text)
    left = para_start
    for m in re.finditer(r"[.?!]\s+(?=[A-Z\"'(])", text[para_start:s + 1]):
        left = para_start + m.end()
    right = para_end
    m = re.search(r"[.?!](?=\s|$)", text[e:para_end])
    if m:
        right = e + m.end()
    if right - left > max_len:
        left = max(left, s - max_len // 2)
        right = min(right, e + max_len // 2)
    return left, right, para_start, para_end


def extract_quotes(text, aliases, members=None, max_quotes=6):
    from .gov_common import find_mentions
    hits = find_mentions(text, aliases, limit=200)
    if not hits:
        return [], 0
    marks = speaker_turns(text)
    out, seen = [], set()
    for s, e, label, kind in hits:
        left, right, ps, pe = sentence_span(text, s, e)
        if (left, right) in seen:
            continue
        seen.add((left, right))
        quote = norm_text(text[left:right])
        if re.match(r"^\\\d+\\", quote) or re.match(r"^\s*\\\d+\\", text[ps:pe]):
            continue  # footnote text, not testimony
        context = norm_text(text[max(ps, left - 900):min(pe, right + 900)])
        if not verbatim(quote, text) or len(quote) < 25:
            continue
        spk = speaker_at(marks, s)
        mem = resolve_speaker(spk, members or [])
        out.append({"text": quote, "context": context, "match": norm_text(text[s:e]), "alias": label, "alias_kind": kind,
                    "speaker": spk, "speaker_bioguide": (mem or {}).get("bioguide"), "speaker_party": (mem or {}).get("party"),
                    "offset": s})
        if len(out) >= max_quotes:
            break
    return out, len(hits)


# ---------------------------------------------------------------- per-company scan

def _phrases(aliases):
    """Lower-cased plain phrases for a fast substring pre-filter before the exact regexes run."""
    out = set()
    for label, rx, kind in aliases:
        ph = re.sub(r"\s*\(legal name\)$", "", label).strip().lower()
        if ph:
            out.add(re.sub(r"\s+", " ", ph))
    return out


def scan(texts, metas, company_aliases, log=print):
    """texts: {pkg: text}; company_aliases: {tk: aliases}. Returns {tk: [hearing records with quotes]}."""
    low = {p: re.sub(r"\s+", " ", t.lower()) for p, t in texts.items()}
    phrases = {tk: _phrases(al) for tk, al in company_aliases.items()}
    out = {}
    for tk, al in company_aliases.items():
        recs = []
        ph = phrases[tk]
        for p, t in texts.items():
            lt = low[p]
            if not any(x in lt for x in ph):
                continue
            meta = metas.get(p) or {}
            quotes, n = extract_quotes(t, al, meta.get("members"))
            if not quotes:
                continue
            recs.append(hearing_record(p, meta, quotes, n))
        recs.sort(key=lambda r: r.get("date") or "", reverse=True)
        if recs:
            out[tk] = recs
    return out


def hearing_record(p, meta, quotes, n):
    from .gov_common import src
    comm = [c.get("name") for c in meta.get("committees") or [] if c.get("name")]
    subs = [s for c in meta.get("committees") or [] for s in c.get("subcommittees") or []]
    return {"id": p, "title": meta.get("title"), "date": meta.get("date"), "chamber": (meta.get("chamber") or "").title() or None,
            "congress": meta.get("congress"), "committees": comm, "committee_ids": [c.get("id") for c in meta.get("committees") or [] if c.get("id")],
            "subcommittees": subs[:3], "mentions": n, "quotes": quotes, "url": meta.get("details_url") or DETAILS.format(pkg=p),
            "text_url": meta.get("html_url") or HTML.format(pkg=p), "raw_path": f"govraw/chrg/{p}.txt.gz",
            "src": src(meta.get("html_url") or HTML.format(pkg=p), "GovInfo CHRG HTML transcript + MODS metadata; quote verified verbatim", meta.get("retrieved_at"))}


def sector_hearings(metas, sector, defense=False, limit=12):
    rx = re.compile(SECTOR_TOPICS.get(sector, r"$^") + (("|" + DEFENSE_TOPIC) if defense else ""), re.I)
    rows = []
    for p, m in metas.items():
        if m.get("status") != "ok" or not m.get("title"):
            continue
        mt = rx.search(m["title"])
        if mt:
            rows.append({"id": p, "title": m["title"], "date": m.get("date"), "chamber": (m.get("chamber") or "").title() or None,
                         "committees": [c.get("name") for c in m.get("committees") or [] if c.get("name")], "match": mt.group(0),
                         "url": m.get("details_url") or DETAILS.format(pkg=p), "text_url": m.get("html_url"),
                         "witnesses": (m.get("witnesses") or [])[:6]})
    rows.sort(key=lambda r: r.get("date") or "", reverse=True)
    return rows[:limit]


def load_texts(sb, pkgs, workers=8):
    out = {}

    def one(p):
        b = get_raw(sb, f"govraw/chrg/{p}.txt")
        return p, (b.decode("utf-8", "replace") if b else None)
    with ThreadPoolExecutor(workers) as ex:
        for p, t in ex.map(one, pkgs):
            if t:
                out[p] = t
    return out
