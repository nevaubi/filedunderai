"""Dividend and IPO calendars, each row tied to the SEC filing it came from.

calendars/dividends.json
  Declared dividends: EDGAR full-text search over 8-K filings and their exhibits for dividend declarations, kept for
  tracked companies (matched by CIK, never by name). Each declaration carries the sentence it was read from, the
  amount, the record and payment dates, and the filing. Since the move to T+1 settlement (May 28, 2024) the ex-dividend
  date is the record date when that is a business day, so ex_date is derived from the record date and flagged as such.
  History and cadence: the cash dividends in the stored daily price files (prices/{T}.json "dv"), from which the
  trailing-twelve-month total, the payment frequency and a projected next ex-date are computed. A projection is labelled
  as one and never shown as declared.

calendars/ipos.json
  The IPO pipeline from the EDGAR daily form indexes: initial registration statements (S-1, F-1) and their amendments,
  effectiveness notices, final prospectuses (424B4/424B1) and withdrawals over the last 180 days. A filer is kept only
  when its submission history shows no periodic report (10-K, 10-Q, 20-F, 40-F) before its first registration
  statement, which removes resale and follow-on registrations by companies that are already public. Exchange-traded
  products are dropped. The price range comes from the latest amendment's cover, the final price and share count from
  the final prospectus cover, each with the sentence quoted.
"""
import datetime as dt
import gzip
import html as _html
import json
import re
import statistics
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

from .common import now_iso, request, sec_throttle, SEC_UA
from .universe import UNIVERSE

UA = {"User-Agent": SEC_UA}
EFTS = "https://efts.sec.gov/LATEST/search-index"
ARCHIVES = "https://www.sec.gov/Archives/edgar/data"
DAILY_IDX = "https://www.sec.gov/Archives/edgar/daily-index/{y}/QTR{q}/form.{d}.idx"
SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
DIV_PHRASES = ['"declared a quarterly dividend"', '"declared a quarterly cash dividend"', '"declared a regular quarterly"',
               '"declared a monthly dividend"', '"declared a monthly cash dividend"', '"declared a semi-annual"',
               '"declared a special dividend"', '"declared a special cash dividend"', '"declared an annual dividend"',
               '"declared a cash dividend"', '"declared a dividend"', '"approved a quarterly dividend"',
               '"approved a quarterly cash dividend"', '"declared quarterly dividends"']
DIV_DAYS = 60
IPO_DAYS = 180
IPO_FORMS = {"S-1", "S-1/A", "F-1", "F-1/A", "424B4", "424B1", "EFFECT", "RW", "S-1MEF", "F-1MEF"}
INITIAL = {"S-1", "F-1"}
PERIODIC = {"10-K", "10-Q", "20-F", "40-F", "10-K405", "10-KT", "10-QT", "20-F/A", "10-K/A", "10-Q/A"}
DIV_CACHE = "calendars/div_docs.json.gz"
IPO_CACHE = "calendars/ipo_state.json.gz"
MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"]
MON_RX = r"(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?"
DATE_RX = re.compile(MON_RX + r"\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s*(\d{4}))?", re.I)
T1_START = dt.date(2024, 5, 28)


# ---------------------------------------------------------------- shared helpers
def _sec(url, timeout=60, retries=3):
    sec_throttle()
    return request(url, headers=UA, timeout=timeout, retries=retries)


def _get_cache(sb, path, default):
    raw = sb.get_object("raw", path)
    if not raw:
        return default
    try:
        return json.loads(gzip.decompress(raw))
    except OSError:
        return json.loads(raw)


def _put_cache(sb, path, obj):
    sb.put_object("raw", path, gzip.compress(json.dumps(obj, separators=(",", ":")).encode()), "application/gzip", "no-store")


def html_text(h):
    h = re.sub(r"(?is)<(script|style|head)\b.*?</\1>", " ", h)
    h = re.sub(r"(?i)<br\s*/?>|</(p|div|tr|li|h\d|td)>", "\n", h)
    t = _html.unescape(re.sub(r"<[^>]+>", " ", h))
    t = t.replace("\xa0", " ").replace("’", "'").replace("“", '"').replace("”", '"')
    t = re.sub(r"[ \t\r\f\v]+", " ", t)
    return re.sub(r"\s*\n\s*", "\n", t).strip()


def sentences(text):
    flat = re.sub(r"\s+", " ", text)
    # split after a period that ends a word (not inside $0.26 or "Inc."), before an upper-case start
    parts = re.split(r"(?<=[a-z0-9\)\]%])(?<!\bInc)(?<!\bCo)(?<!\bCorp)(?<!\bNo)(?<!\bLtd)\.\s+(?=[A-Z\"(])", flat)
    parts = [p.strip() for p in parts if p.strip()]
    # the split consumed each sentence's period; put it back for quoting
    return [p if i == len(parts) - 1 or p[-1] in ".!?:;\"" else p + "." for i, p in enumerate(parts)]


def _month(tok):
    t = tok.lower().rstrip(".")[:3]
    return next(i + 1 for i, m in enumerate(MONTHS) if m[:3] == t)


def parse_date(m, ref):
    """A month-day(-year) match to a date; a missing year is the first one that puts the date on or after ref - 45 days."""
    mon, day = _month(m.group(1)), int(m.group(2))
    if m.group(3):
        try:
            return dt.date(int(m.group(3)), mon, day)
        except ValueError:
            return None
    for y in (ref.year, ref.year + 1):
        try:
            d = dt.date(y, mon, day)
        except ValueError:
            return None
        if d >= ref - dt.timedelta(days=45):
            return d
    return None


def _date_after(s, words, ref, span=90):
    for w in words:
        for k in re.finditer(w, s, re.I):
            m = DATE_RX.search(s, k.end(), k.end() + span)
            if m:
                d = parse_date(m, ref)
                if d:
                    return d
    return None


def business_day(d):
    while d.weekday() >= 5:
        d -= dt.timedelta(days=1)
    return d


# ---------------------------------------------------------------- dividend declarations
AMOUNT_RX = [re.compile(r"\$\s?(\d{1,3}(?:\.\d{1,5})?)(?![\d,])\s*(?:per\s+(?:share|common share|ordinary share|unit|class [ab] (?:common )?share)|a\s+share)", re.I),
             re.compile(r"dividends?\s+(?:of|in the amount of)\s+\$\s?(\d{1,3}(?:\.\d{1,5})?)(?![\d,])(?!\s*(?:million|billion|thousand))", re.I),
             re.compile(r"(\d{1,3}(?:\.\d{1,5})?)\s+cents\s+per\s+share", re.I)]
RECORD_W = [r"of record", r"\brecord date\b", r"\brecord\b"]
PAY_W = [r"\bpayable\b", r"\bpaid\b", r"\bpayment date\b", r"\bto be paid\b", r"\bdistributed\b", r"\bdistribute\b"]


def _amount_matches(s):
    """Per-share common dividend amounts in order: not annualized rates, not preferred or depositary share amounts."""
    found, spans = [], []
    for rx in AMOUNT_RX:
        for m in rx.finditer(s):
            if any(a <= m.start() < b or m.start() <= a < m.end() for a, b in spans):
                continue
            before, after = s[max(0, m.start() - 60):m.start()].lower(), s[m.end():m.end() + 140].lower()
            if re.search(r"annuali[sz]ed|annual rate|per annum", before + after[:30]):
                continue
            # "an increase from $0.07 to $0.08 per share": the prior amount is not a declaration
            if re.search(r"\b(from|previous(ly)?|prior)\s+(?:the\s+)?(?:quarterly\s+)?(?:dividend\s+(?:of|rate of)\s+)?$", before[-40:]) or re.search(r"\b(?:last|prior|previous) (?:quarter|year|dividend)", after[:40]):
                continue
            if re.search(r"preferred|\bseries [a-z]\b|depositary", after[:110]) and "common" not in after[:40]:
                continue
            spans.append((m.start(), m.end()))
            found.append(m)
    return sorted(found, key=lambda m: m.start())


def _value(m):
    v = float(m.group(1))
    return v / 100 if "cents" in m.group(0).lower() else v


def _amount(s):
    ms = _amount_matches(s)
    return _value(ms[0]) if ms else None


def parse_declaration(text, filed):
    """Common-stock dividend declarations in a filing's text. Returns dicts with amount, kind, record, payable, quote.

    The declaring sentence must carry the amount; the record and payment dates may follow in the next three sentences
    of the release, and the quote runs from the declaring sentence through the last sentence a date was taken from."""
    ref = dt.date.fromisoformat(filed)
    sents = [x for para in text.split("\n") for x in sentences(para)]
    out = []
    for i, s in enumerate(sents):
        low = s.lower()
        if "dividend" not in low or not re.search(r"\b(declared|approved|declares|approves|authorized|announced)\b", low) or len(s) > 900:
            continue
        if re.search(r"\bpreferred\b|\bseries [a-z]\b|depositary share", low) and "common" not in low:
            continue
        if re.search(r"\b(may|could|intends? to|expects? to|future)\b[^.]{0,40}\bdividend", low) and not re.search(r"\bdeclared\b", low):
            continue
        ams = [m for m in _amount_matches(s) if 0 < _value(m) < 100]
        if not ams:
            continue
        window, end = [s], i
        for j in range(i + 1, min(i + 4, len(sents))):
            nxt = sents[j]
            nl = nxt.lower()
            if re.search(r"\bpreferred\b|\bseries [a-z]\b", nl):
                break  # dates in a preferred-stock sentence belong to that dividend
            if _amount(nxt) is not None and "dividend" in nl and re.search(r"\bdeclared\b", nl):
                break
            window.append(nxt)
        record = payable = None
        for k, w in enumerate(window):
            r_ = _date_after(w, RECORD_W, ref)
            p_ = _date_after(w, PAY_W, ref, 60)
            if r_ and not record:
                record, end = r_, max(end, i + k)
            if p_ and not payable:
                payable, end = p_, max(end, i + k)
            if record and payable:
                break
        later = None
        if not record or not payable:
            # dates stated later for a defined term: (the "Common Dividend") ... The Common Dividend is payable on ...
            label = re.search(r"\(the [\"\u201c]([^\"\u201d]{3,40}(?:Dividend|Distribution)s?)[\"\u201d]\)", s)
            for j in range(i + 1, min(i + 16, len(sents))):
                nxt = sents[j]
                nl = nxt.lower()
                if label:
                    if label.group(1).lower() not in nl:
                        continue
                elif j > i + 8 or "dividend" not in nl or re.search(r"\bpreferred\b|\bseries [a-z]\b", nl) or _amount(nxt) is not None:
                    continue
                r_ = _date_after(nxt, RECORD_W, ref)
                p_ = _date_after(nxt, PAY_W, ref, 60)
                if r_ or p_:
                    record, payable, later = record or r_, payable or p_, j
                    break
        if not record and not payable:
            continue
        quote = " ".join(sents[i:end + 1]) + (" ... " + sents[later] if later and later > end + 1 else " " + sents[later] if later else "")
        k0 = re.search(r"\b(19|20)\d{2}\s*(?:\u2013|\u2014|--|-|/PRNewswire/|/Business Wire/)\s*", s[:300])  # strip the dateline
        if k0:
            quote = quote[k0.end():]
        ex = business_day(record) if record and record >= T1_START else None
        for n, am in enumerate(ams):
            amount = _value(am)
            # the words that describe this amount sit before it, after any earlier amount in the sentence
            lo = max(am.start() - 90, ams[n - 1].end() if n else 0)
            near = s[lo:am.end()].lower()
            kind = ("special" if "special" in near or "one-time" in near else "monthly" if "monthly" in near or "for the month" in near else
                    "semi-annual" if "semi-annual" in near or "semiannual" in near else "annual" if re.search(r"\bannual (cash )?dividend", near) else
                    "quarterly" if "quarter" in near else "regular")
            cls = re.search(r"\bclass ([a-c])\b", s[am.start():am.end() + 60].lower()) or re.search(r"\bclass ([a-c])\b", near)
            out.append({"amount": round(amount, 6), "kind": kind, "share_class": cls and f"Class {cls.group(1).upper()}",
                        "record": record and record.isoformat(), "payable": payable and payable.isoformat(),
                        "ex_date": ex and ex.isoformat(), "increase": bool(re.search(r"\bincrease", " ".join(window).lower())), "quote": quote[:700]})
    return _dedupe(out)


def _dedupe(items):
    """One row per declaration: a headline and the body sentence state the same amount; keep the most complete."""
    items = sorted(items, key=lambda d: -((d.get("record") is not None) + (d.get("payable") is not None)))
    uniq = []
    for d in items:
        dup = next((u for u in uniq if u["amount"] == d["amount"] and (
            (d.get("record") and d["record"] == u.get("record")) or (d.get("payable") and d["payable"] == u.get("payable"))
            or (not d.get("record") and not d.get("payable")))), None)
        if not dup:
            uniq.append(d)
    return uniq


def efts_hits(phrase, start, end, log=print):
    hits, frm = [], 0
    while True:
        q = urllib.parse.urlencode({"q": phrase, "forms": "8-K", "dateRange": "custom", "startdt": start, "enddt": end, "from": frm})
        try:
            d = json.loads(_sec(f"{EFTS}?{q}", timeout=45))
        except Exception as e:
            log(f"efts {phrase} from {frm}: {str(e)[:120]}")
            break
        rows = d.get("hits", {}).get("hits", [])
        hits += rows
        total = d.get("hits", {}).get("total", {}).get("value", 0)
        frm += len(rows)
        if not rows or frm >= min(total, 1000):
            break
    return hits


def declarations(sb, today=None, deadline=None, log=print):
    today = today or dt.date.today()
    start = (today - dt.timedelta(days=DIV_DAYS)).isoformat()
    ciks = {int(c): t for t, c, *_ in UNIVERSE}
    docs = {}
    for ph in DIV_PHRASES:
        if deadline and time.time() > deadline:
            break
        for h in efts_hits(ph, start, today.isoformat(), log=log):
            src = h.get("_source", {})
            cik = next((int(c) for c in src.get("ciks", []) if int(c) in ciks), None)
            if cik is None:
                continue
            adsh, _, fname = h["_id"].partition(":")
            docs[h["_id"]] = {"cik": cik, "ticker": ciks[cik], "accn": adsh, "doc": fname, "filed": src.get("file_date"),
                              "type": src.get("file_type"), "items": src.get("items") or []}
    cache = _get_cache(sb, DIV_CACHE, {})
    todo = [k for k in docs if k not in cache]
    log(f"dividends: {len(docs)} matching documents for tracked companies, {len(todo)} new")

    def one(k):
        if deadline and time.time() > deadline:
            return k, None
        d = docs[k]
        url = f"{ARCHIVES}/{d['cik']}/{d['accn'].replace('-', '')}/{d['doc']}"
        try:
            return k, parse_declaration(html_text(_sec(url)), d["filed"])
        except Exception as e:
            log(f"dividend doc {url}: {str(e)[:120]}")
            return k, None

    with ThreadPoolExecutor(4) as ex:
        for k, got in ex.map(one, todo):
            if got is not None:
                cache[k] = got
    keep_from = (today - dt.timedelta(days=DIV_DAYS + 30)).isoformat()
    cache = {k: v for k, v in cache.items() if k in docs or any((x.get("payable") or x.get("record") or "") >= keep_from for x in v)}
    _put_cache(sb, DIV_CACHE, cache)
    by_t = {}
    for k, d in docs.items():
        for x in cache.get(k) or []:
            url = f"{ARCHIVES}/{d['cik']}/{d['accn'].replace('-', '')}/{d['doc']}"
            by_t.setdefault(d["ticker"], []).append(dict(x, filed=d["filed"], accn=d["accn"], doc=d["doc"], url=url, form="8-K", exhibit=d["type"]))
    for t, xs in by_t.items():
        # the 8-K and its press-release exhibit usually state the same declaration; keep one, preferring the exhibit
        xs.sort(key=lambda x: (0 if (x["exhibit"] or "").startswith("EX-99") else 1))
        by_t[t] = sorted(_dedupe(xs), key=lambda x: (x["record"] or x["payable"] or "", x["amount"]))
    return by_t


def _price_file(sb, t):
    for key in dict.fromkeys([t, t.replace(".", "-")]):
        d = sb.get_json(f"prices/{key.replace('^', 'IDX_').replace('=', '_')}.json")
        if d:
            return d
    return None


def cadence(dv, last_close, today):
    """Frequency, trailing-twelve-month total, yield and projected next ex-date from cash dividend history [[date, amt]]."""
    if not dv:
        return None
    dv = sorted(dv)
    dates = [dt.date.fromisoformat(d) for d, _ in dv]
    yr = today - dt.timedelta(days=365)
    ttm = [a for (d, a), x in zip(dv, dates) if x > yr]
    recent = dates[-9:]
    gaps = [(b - a).days for a, b in zip(recent, recent[1:])]
    med = statistics.median(gaps) if len(gaps) >= 2 else None
    freq = None
    if med:
        freq = "monthly" if med < 45 else "quarterly" if med < 130 else "semi-annual" if med < 250 else "annual" if med < 450 else "irregular"
    per_year = {"monthly": 12, "quarterly": 4, "semi-annual": 2, "annual": 1}.get(freq)
    proj = None
    if med and freq != "irregular" and dates[-1] > today - dt.timedelta(days=int(med * 1.6)):
        nxt = dates[-1] + dt.timedelta(days=int(round(med)))
        while nxt < today:
            nxt += dt.timedelta(days=int(round(med)))
        proj = {"ex_date": business_day(nxt).isoformat(), "amount": dv[-1][1], "basis": f"last ex-date {dv[-1][0]} plus the median gap of {int(round(med))} days between the last {len(gaps) + 1} payments"}
    regular = dv[-1][1] * per_year if per_year else None
    return {"history": dv[-12:], "last": {"ex_date": dv[-1][0], "amount": dv[-1][1]}, "frequency": freq, "median_gap_days": med,
            "ttm": round(sum(ttm), 6), "ttm_count": len(ttm), "forward_annual": regular and round(regular, 6),
            "yield_ttm": round(sum(ttm) / last_close, 6) if last_close and ttm else None,
            "yield_forward": round(regular / last_close, 6) if last_close and regular else None, "projected": proj}


def dividends_job(sb, today=None, time_budget=600, log=print):
    t0 = time.time()
    deadline = t0 + time_budget * 0.75
    today = today or dt.date.today()
    declared = declarations(sb, today=today, deadline=deadline, log=log)
    names = {t: n for t, c, n, *_ in UNIVERSE}

    def one(t):
        try:
            p = _price_file(sb, t)
        except Exception:
            p = None
        dv = (p or {}).get("dv") or []
        bars = (p or {}).get("d") or []
        close = bars[-1][4] if bars else None
        return t, cadence(dv, close, today), close

    rows = []
    with ThreadPoolExecutor(8) as ex:
        for t, cad, close in ex.map(one, [t for t, *_ in UNIVERSE]):
            dec = declared.get(t) or []
            if not cad and not dec:
                continue
            upcoming = [d for d in dec if (d.get("ex_date") or d.get("record") or d.get("payable") or "") >= today.isoformat()]
            rows.append({"ticker": t, "name": names.get(t), "close": close, "declared": dec, "upcoming": upcoming, **(cad or {})})
    out = {"generated": now_iso(), "as_of": today.isoformat(), "declared_window_days": DIV_DAYS, "rows": rows,
           "sources": {"declarations": "EDGAR full-text search of 8-K filings and exhibits (efts.sec.gov)",
                       "history": "Cash dividends in the daily price history (prices/{T}.json)",
                       "ex_date_rule": "Since May 28, 2024 (T+1 settlement) the ex-dividend date is the record date when that is a business day."}}
    sb.put_json("calendars/dividends.json", out, cache="max-age=1800")
    log(f"dividends: {len(rows)} companies, {sum(len(r['declared']) for r in rows)} declarations, {round(time.time() - t0)}s")
    return {"companies": len(rows), "declarations": sum(len(r["declared"]) for r in rows)}


# ---------------------------------------------------------------- IPO pipeline
def daily_index(d):
    q = (d.month - 1) // 3 + 1
    url = DAILY_IDX.format(y=d.year, q=q, d=d.strftime("%Y%m%d"))
    try:
        body = _sec(url, timeout=60, retries=2)
    except Exception as e:
        if "404" in str(e) or "403" in str(e):
            return []  # weekends and holidays have no index
        raise
    rows = []
    for line in body.splitlines():
        m = re.match(r"^(\S+(?: \S+)?)\s{2,}(.+?)\s{2,}(\d{3,10})\s+(\d{8})\s+(edgar/data/\S+)", line)
        if not m or m.group(1) not in IPO_FORMS:
            continue
        accn = m.group(5).rsplit("/", 1)[-1].replace(".txt", "")
        rows.append({"form": m.group(1), "name": m.group(2).strip(), "cik": int(m.group(3)),
                     "date": f"{m.group(4)[:4]}-{m.group(4)[4:6]}-{m.group(4)[6:]}", "accn": accn})
    return rows


def _etp(name, sic):
    """Exchange-traded products register on S-1 too; they are not operating-company IPOs."""
    return bool(re.search(r"\b(ETF|ETP|Exchange[- ]Traded|Bitcoin|Ether(eum)?|Crypto)\b", name, re.I)) or str(sic) in ("6221",)


UNDERWRITER_ROLES = re.compile(r"\b(?:Joint |Sole |Lead |Passive |Senior )*(?:Lead )?(?:Book[- ]?Running|Bookrunning|Book-Runner|Bookrunner|Co-?Lead|Lead|Co-)?\s*(?:Managers?|Bookrunners?|Book-Runners?|Underwriters?|Placement Agents?)\b", re.I)
# Bank names as they appear on prospectus covers; anything left over on a short cover line is kept as written.
BANKS = sorted([
    "Goldman Sachs & Co. LLC", "Goldman Sachs", "J.P. Morgan", "Morgan Stanley", "BofA Securities", "Citigroup", "Jefferies", "Barclays",
    "Evercore ISI", "TD Cowen", "Cowen", "Leerink Partners", "SVB Leerink", "Piper Sandler", "Stifel", "Cantor", "Mizuho", "Wells Fargo Securities",
    "UBS Investment Bank", "Deutsche Bank Securities", "RBC Capital Markets", "Guggenheim Securities", "William Blair", "Needham & Company",
    "Truist Securities", "KeyBanc Capital Markets", "BMO Capital Markets", "Baird", "Raymond James", "Oppenheimer & Co.", "Wolfe | Nomura Alliance",
    "Nomura", "Macquarie Capital", "Santander", "SMBC Nikko", "Societe Generale", "Société Générale", "BTIG", "Clear Street", "Chardan", "LifeSci Capital",
    "Wedbush PacGrow", "Wedbush Securities", "Lake Street", "Roth Capital Partners", "Roth Capital", "Maxim Group LLC", "ThinkEquity", "EF Hutton",
    "Aegis Capital Corp.", "Univest Securities, LLC", "Network 1 Financial Securities", "US Tiger Securities", "Prime Number Capital", "D. Boral Capital",
    "Craft Capital Management", "Joseph Stone Capital", "The Benchmark Company", "Benchmark", "Dominari Securities", "Spartan Capital Securities",
    "Kingswood Capital Partners", "Bancroft Capital", "R.F. Lafferty & Co.", "Boustead Securities", "Revere Securities", "Rodman & Renshaw",
    "H.C. Wainwright & Co.", "Ladenburg Thalmann", "A.G.P.", "Alliance Global Partners", "WestPark Capital", "Brookline Capital Markets",
    "Laidlaw & Company", "Craig-Hallum", "Northland Capital Markets", "Janney Montgomery Scott", "Keefe, Bruyette & Woods", "KBW", "Houlihan Lokey",
    "Capital One Securities", "Stephens Inc.", "Tuohy Brothers", "Titan Partners", "Loop Capital Markets", "Siebert Williams Shank", "Academy Securities",
    "Samuel A. Ramirez & Company", "Drexel Hamilton", "CastleOak Securities", "Tigress Financial Partners", "Blaylock Van", "Telsey Advisory Group",
    "Moelis & Company", "Lazard", "Rothschild & Co", "Centerview Partners", "PJT Partners", "D.A. Davidson & Co.", "Hovde Group", "Piper Jaffray",
    "Performance Trust Capital Partners", "Stephens", "Sidoti & Company", "Bernstein", "Dawson James Securities", "Cathay Securities", "Eddid Securities",
    "Futu", "Tiger Brokers", "Citizens JMP", "Citizens Capital Markets", "JMP Securities", "Cohen & Company Capital Markets", "Mischler Financial Group",
    "Morgan Stanley & Co. LLC", "Credit Suisse", "HSBC", "Natixis", "ING", "Scotiabank", "CIBC Capital Markets", "TD Securities", "Desjardins",
    "Canaccord Genuity", "Cormark Securities", "Eight Capital", "National Bank Financial", "Paradigm Capital", "Haitong International", "CICC",
    "Huatai Securities", "China Renaissance", "Valuable Capital", "AMTD", "Bocom International", "CMB International", "Freedom Capital Markets",
    "Lucid Capital Markets", "Ladenburg", "Brean Capital", "Alexander Capital", "Kingswood", "American Trust Investment Services", "Bancroft",
    "BBVA", "MUFG", "Rosenblatt", "Regions Securities LLC", "Regions Securities", "Strong Capital Markets", "Texas Capital Securities",
    "Joseph Gunnar & Co., LLC", "MDB Capital", "Fortune Securities", "BOCOM International", "ICBC International", "CR Global Markets",
    "JonesTrading", "Deutsche Bank", "Seaport Global Securities", "Wells Fargo", "Morgan Stanley & Co.", "Goldman Sachs & Co.",
    "Fifth Third Securities", "PNC Capital Markets LLC", "Huntington Capital Markets", "Comerica Securities", "US Bancorp", "U.S. Bancorp",
    "Credit Agricole CIB", "Crédit Agricole CIB", "First Citizens Capital Securities", "Blackstone Capital Markets", "Rabo Securities",
    "Roberts & Ryan", "Pickering Energy Partners", "Ramirez & Co., Inc.", "R. Seelaus & Co., LLC", "AmeriVet Securities", "Penserra Securities LLC", "Guzman & Company", "Independence Point Securities",
], key=len, reverse=True)
BANK_RX = re.compile(r"(?<![A-Za-z])(?:" + "|".join(re.escape(b) for b in BANKS) + r")(?![A-Za-z])", re.I)
BANK_NAME = {b.lower(): b for b in BANKS}


def underwriters(head):
    """Bank names on the prospectus cover: the short block between the delivery or SEC legend sentence and the
    "Prospectus dated" / "The date of this prospectus" line. Returns the names in cover order and the block quoted."""
    m = re.search(r"(?:The date of this prospectus is|Prospectus dated|Preliminary Prospectus dated|Subject to Completion)", head[:60000])
    if not m:
        return None
    pre = head[max(0, m.start() - 1500):m.start()]
    # the last sentence end before the bank block; "J.P. Morgan", "& Co. LLC" and "Inc. " are not sentence ends
    ends = [x.end() for x in re.finditer(r"(?<![A-Z])(?<!\bCo)(?<!\bInc)(?<!\bCorp)(?<!\bLtd)(?<!\bNo)\.\s+(?=[A-Z_])|_{3,}\s*|offense\.\s*", pre)]
    block = pre
    for e in reversed(ends):
        if pre[e:].strip(" ._\u200b\u00a0"):
            block = pre[e:]
            break
    block = re.sub(r"[_\u200b\u00a0]+", " ", block).strip(" .")
    if not block or len(block) > 1200:
        return None
    names = []
    scan = re.sub(r"\bA [A-Z][A-Za-z]+ Company\b", " ", block)  # "Keefe, Bruyette & Woods A Stifel Company" names one bank
    rest = scan
    for x in BANK_RX.finditer(scan):
        n = BANK_NAME.get(x.group(0).strip().lower(), x.group(0).strip())
        if n not in names:
            names.append(n)
        rest = rest.replace(x.group(0), " ")
    rest = re.sub(r"\s+", " ", UNDERWRITER_ROLES.sub(" ", rest)).strip(" |,.*")
    # an unknown name left on a short block (e.g. a small-cap sole underwriter): keep it only when it reads as one name
    if (rest and len(rest.split()) <= 5 and re.match(r"[A-Z]", rest) and not re.match(r"(?:LLC|Inc|Co|Corp|Ltd|A|An)\b", rest)
            and not re.search(r"\b(?:the|and|of|our|this|we|is|are)\b|[*]|Prospectus|Table|Contents|Page", rest)):
        names.append(rest)
    if not names:
        return None
    return {"names": names, "quote": block[:1200]}


def price_terms(text):
    """Cover-page terms: price range, final price, shares offered, proposed symbol and exchange, underwriters, each with
    the sentence it was read from."""
    head = re.sub(r"\s+", " ", text[:60000])
    out = {"blank": bool(re.search(r"\bblank[- ]check company\b", head, re.I)),
           "direct": bool(re.search(r"\bdirect listing\b", head[:20000], re.I))}
    unit = r"per\s+((?:Class [A-C] )?(?:ordinary share|common share|share|ADS|American Depositary Share|unit|Ordinary Share))"
    usd = r"(?:US)?\$\s?(\d+(?:\.\d+)?)"
    m = (re.search(r"between " + usd + r" (?:and|to) " + usd + r" " + unit, head, re.I)
         or re.search(r"price range of " + usd + r" (?:to|and|-) " + usd + r" " + unit, head, re.I)
         or re.search(r"in the range of " + usd + r" (?:to|and) " + usd + r" " + unit, head, re.I)
         or re.search(usd + r" (?:to|and) " + usd + r" " + unit + r"[^.]{0,60}(?:initial public offering|offering price)", head, re.I)
         or re.search(r"offering price[^.$]{0,60}(?:will|to) be between " + usd + r" and " + usd + r"()", head, re.I))
    if m:
        out["range"] = {"lo": float(m.group(1)), "hi": float(m.group(2)), "unit": m.group(3) or "share", "quote": _around(head, m)}
    m = (re.search(r"initial public offering price(?: per (?:share|ordinary share|common share|ADS|unit))?(?: (?:of|for) (?:our |the |each )?[A-Za-z ]{0,60}?)?\s+(?:is|will be|of|per (?:share|ADS|unit) is)\s*" + usd, head, re.I)
         or re.search(r"public offering price(?: (?:for|of) (?:our |the |each )?[A-Za-z ]{0,40}?)?\s+(?:is|of|will be)\s*" + usd + r" per (?:share|ADS|American Depositary Share|unit|ordinary share|common share|Ordinary Share)", head, re.I)
         or re.search(r"offering price (?:is|of)\s*" + usd + r" per (?:share|ADS|unit)", head, re.I)
         or re.search(usd + r" per (?:share|unit|ADS)[^.]{0,40}initial public offering", head, re.I))
    fixed = re.search(r"(?:at|of) a fixed (?:offering |public offering )?price of " + usd + r" per", head, re.I) \
        or re.search(r"based upon the " + usd + r" per share offering price", head, re.I)
    assumed = re.search(r"assumed (?:initial )?public offering price of " + usd, head, re.I)
    if m and not re.search(r"assumed|estimated", head[max(0, m.start() - 30):m.start()], re.I):
        out["price"] = {"value": float(m.group(1)), "quote": _around(head, m)}
    elif fixed:
        out["price"] = {"value": float(fixed.group(1)), "fixed": True, "quote": _around(head, fixed)}
    if "range" not in out and fixed:
        v = float(fixed.group(1))
        out["range"] = {"lo": v, "hi": v, "unit": "share", "fixed": True, "quote": _around(head, fixed)}
    if assumed and "range" not in out:
        v = float(assumed.group(1))
        out["range"] = {"lo": v, "hi": v, "unit": "share", "assumed": True, "quote": _around(head, assumed)}
    preliminary = bool(re.search(r"subject to completion|preliminary prospectus", head[:8000], re.I))
    if preliminary and "range" not in out:
        # small offerings state one expected price instead of a range: "is expected to be $5.00", "is assumed to be US$4.00"
        e = re.search(r"offering price(?: (?:of|for|per) [^.$]{0,60}?)?\s+(?:is expected to be|is anticipated to be|is assumed to be|to be|will be|is)\s*" + usd, head, re.I)
        if e:
            v = float(e.group(1))
            out["range"] = {"lo": v, "hi": v, "unit": "share", "assumed": True, "quote": _around(head, e)}
        elif out.get("price"):
            v = out["price"]["value"]
            out["range"] = {"lo": v, "hi": v, "unit": "share", "assumed": True, "quote": out["price"]["quote"]}
    if preliminary:
        out.pop("price", None)  # a preliminary prospectus has no final price
    sh = (r"((?:Class [A-C] )?(?:shares of (?:our )?(?:Class [A-C] )?(?:common stock|ordinary shares|common shares)|ordinary shares|Ordinary Shares|"
          r"common shares|Common Shares|American Depositary Shares|ADSs|units|Units|Shares|shares))")
    m = (re.search(r"(?:we are|is|are) (?:offering|selling)(?:,? on a (?:firm commitment|best[- ]efforts) basis,)? (?:up to |an aggregate of )?([\d,]{5,})\s+" + sh, head, re.I)
         or re.search(r"([\d,]{5,})\s+(shares of (?:our )?(?:Class [A-C] )?(?:common stock|ordinary shares|common shares)|American Depositary Shares|ADSs|units)", head, re.I)
         or re.search(r"([\d,]{5,})\s+" + sh, head[:3000]))
    if m:
        out["shares"] = {"value": int(m.group(1).replace(",", "")), "unit": m.group(2), "quote": _around(head, m)}
    m = (re.search(r"(?:under the|under the trading|under the ticker) symbols? [\"']\s?([A-Z]{1,5}(?:\.[A-Z]{1,2})?)\.?\s?[\"']", head)
         or re.search(r"reserved the (?:ticker )?symbol [\"']\s?([A-Z]{1,5})\.?\s?[\"']", head))
    if m:
        ctx = _around(head, m)
        ex = re.search(r"Nasdaq Global Select|Nasdaq Global Market|Nasdaq Capital Market|Nasdaq|New York Stock Exchange|NYSE American|NYSE|Cboe", ctx)
        out["symbol"] = {"value": m.group(1), "exchange": ex.group(0) if ex else None, "quote": ctx}
    uw = underwriters(head)
    if uw:
        out["underwriters"] = uw
    return out


def _around(s, m, pad=220):
    a = s.rfind(". ", 0, m.start())
    a = a + 2 if a != -1 and m.start() - a < pad * 2 else max(0, m.start() - pad)
    b = s.find(". ", m.end())
    b = b + 1 if b != -1 and b - m.end() < pad * 2 else min(len(s), m.end() + pad)
    return s[a:b].strip()[:700]


def _doc_url(cik, accn, doc):
    return f"{ARCHIVES}/{cik}/{accn.replace('-', '')}/{doc}"


STALL_DAYS = 30


def ipo_status(reg, final, eff, rw, today):
    """priced > withdrawn > effective (stalled once STALL_DAYS pass with no final prospectus) > amended > filed."""
    if final:
        return "priced"
    if rw:
        return "withdrawn"
    if eff:
        return "stalled" if (today - dt.date.fromisoformat(eff[-1]["date"])).days > STALL_DAYS else "effective"
    return "amended" if any("/A" in r["form"] for r in reg) else "filed"


def first_day(row, today, chart=None):
    """First session after pricing from the Yahoo chart API: open and close against the offer price, and the latest
    close. The first bar must fall within ten days of the pricing date, so a reused or wrong symbol is not matched."""
    priced = dt.date.fromisoformat(row["priced"])
    age = (today - priced).days
    if age < 1:
        return None
    rng = "1mo" if age < 25 else "3mo" if age < 85 else "6mo" if age < 175 else "1y"
    d = (chart or _chart_with_splits)(row["symbol"], rng, "1d")
    bars = [b for b in d.get("bars") or [] if b[4]]
    if not bars:
        return None
    day = lambda b: dt.datetime.fromtimestamp(b[0], dt.timezone.utc).date()
    # recomputed each run (not cached) so a later split adjusts the first session and the offer alike
    b0 = bars[0]
    if not (priced <= day(b0) <= priced + dt.timedelta(days=10)):
        return None
    f = {"date": day(b0).isoformat(), "open": b0[1], "close": b0[4]}
    offer = (row.get("price") or {}).get("value")
    # Yahoo bars are split-adjusted; put the offer price on the same basis
    factor = 1.0
    for sp in d.get("splits") or []:
        if sp["date"] > priced.isoformat() and sp.get("numerator") and sp.get("denominator"):
            factor *= sp["numerator"] / sp["denominator"]
    if offer and factor != 1.0:
        offer = offer / factor
    last = bars[-1]
    out = dict(f, symbol=row["symbol"], last_close=last[4], last_date=day(last).isoformat(),
               url=f"https://finance.yahoo.com/quote/{row['symbol'].replace('.', '-')}/history/",
               source="Yahoo Finance chart API, daily bars")
    if factor != 1.0:
        out["split_factor"] = factor
    if offer:
        out["open_ret"] = round(f["open"] / offer - 1, 6)
        out["close_ret"] = round(f["close"] / offer - 1, 6)
        out["since_ret"] = round(last[4] / offer - 1, 6)
    return out


def _chart_with_splits(sym, rng, interval):
    """Daily bars plus split events from the Yahoo chart API (etl.prices.chart reads dividends only)."""
    from .common import get_json
    url = (f"https://query2.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(sym.replace('.', '-'))}"
           f"?range={rng}&interval={interval}&includePrePost=false&events=split")
    r = get_json(url, timeout=30, retries=2)["chart"]["result"][0]
    q = r["indicators"]["quote"][0]
    bars = []
    for i, t in enumerate(r.get("timestamp") or []):
        c = q["close"][i]
        if c is None:
            continue
        o = q["open"][i] or c
        bars.append([t, round(o, 4), round(q["high"][i] or c, 4), round(q["low"][i] or c, 4), round(c, 4), q["volume"][i] or 0])
    splits = [{"date": dt.datetime.fromtimestamp(int(k), dt.timezone.utc).date().isoformat(), "numerator": v.get("numerator"),
               "denominator": v.get("denominator")} for k, v in ((r.get("events") or {}).get("splits") or {}).items()]
    return {"bars": bars, "splits": splits}


def ipos_job(sb, today=None, time_budget=600, log=print):
    t0 = time.time()
    deadline = t0 + time_budget * 0.8
    today = today or dt.date.today()
    state = _get_cache(sb, IPO_CACHE, {"days": {}, "filers": {}, "terms": {}})
    days = state.setdefault("days", {})
    start = today - dt.timedelta(days=IPO_DAYS)
    d = start
    fetched = 0
    while d <= today:
        k = d.isoformat()
        # past days are immutable once read; today and yesterday are re-read
        if d.weekday() < 5 and (k not in days or d >= today - dt.timedelta(days=1)) and time.time() < deadline:
            try:
                days[k] = daily_index(d)
                fetched += 1
            except Exception as e:
                log(f"daily index {k}: {str(e)[:120]}")
        d += dt.timedelta(days=1)
    days = {k: v for k, v in days.items() if k >= start.isoformat()}
    state["days"] = days
    by_cik = {}
    for rows in days.values():
        for r in rows:
            by_cik.setdefault(r["cik"], []).append(r)
    cands = {c: sorted(rs, key=lambda r: (r["date"], r["form"])) for c, rs in by_cik.items() if any(r["form"] in INITIAL or r["form"] in ("S-1/A", "F-1/A", "424B4", "424B1") for r in rs)}
    log(f"ipos: {fetched} index days read, {len(cands)} registrants with registration activity")

    filers = state.setdefault("filers", {})
    def classify(cik):
        sub = json.loads(_sec(SUBMISSIONS.format(cik=cik), timeout=45))
        rec = sub.get("filings", {}).get("recent", {})
        forms = list(zip(rec.get("form", []), rec.get("filingDate", []), rec.get("accessionNumber", []), rec.get("primaryDocument", [])))
        first_reg = min([f[1] for f in forms if f[0] in INITIAL] or ["9999"])
        periodic = sorted(f[1] for f in forms if f[0] in PERIODIC)
        docs = {f[2]: f[3] for f in forms if f[0] in IPO_FORMS}
        return {"name": sub.get("name"), "sic": sub.get("sic"), "sic_desc": sub.get("sicDescription"), "tickers": sub.get("tickers") or [],
                "exchanges": sub.get("exchanges") or [], "state": sub.get("stateOfIncorporation"), "country": (sub.get("addresses", {}).get("business") or {}).get("stateOrCountryDescription"),
                "first_registration": None if first_reg == "9999" else first_reg, "first_periodic": periodic[0] if periodic else None,
                "prospectuses": sorted(f[1] for f in forms if f[0] in ("424B4", "424B1")), "docs": docs, "checked": today.isoformat(),
                "n_seen": len(cands.get(cik, [])), "v": 2}

    todo = [c for c in cands if str(c) not in filers or filers[str(c)].get("n_seen") != len(cands[c]) or filers[str(c)].get("v") != 2]
    def one(c):
        if time.time() > deadline:
            return c, None
        try:
            return c, classify(c)
        except Exception as e:
            log(f"submissions {c}: {str(e)[:120]}")
            return c, None
    with ThreadPoolExecutor(4) as ex:
        for c, info in ex.map(one, todo):
            if info:
                filers[str(c)] = info
    state["filers"] = {k: v for k, v in filers.items() if int(k) in cands}

    terms = state.setdefault("terms", {})
    rows = []
    for c, rs in cands.items():
        info = state["filers"].get(str(c))
        if not info or info.get("v") != 2 or _etp(info.get("name") or rs[-1]["name"], info.get("sic")):
            continue
        # an IPO has no periodic report and no earlier final prospectus before its registration activity in the window
        begin = min(r["date"] for r in rs if r["form"] in IPO_FORMS - {"EFFECT", "RW"})
        if (info.get("first_periodic") and info["first_periodic"] < begin) or any(x < begin for x in info.get("prospectuses") or []):
            continue
        if not any(r["form"] in INITIAL for r in rs) and not info.get("first_registration"):
            continue
        reg = [r for r in rs if r["form"] in INITIAL or r["form"] in ("S-1/A", "F-1/A", "S-1MEF", "F-1MEF")]
        final = [r for r in rs if r["form"] in ("424B4", "424B1")][:1]  # the first final prospectus prices the IPO
        eff = [r for r in rs if r["form"] == "EFFECT" and reg and r["date"] >= reg[0]["date"]]
        rw = [r for r in rs if r["form"] == "RW" and reg and r["date"] >= reg[-1]["date"]]
        status = ipo_status(reg, final, eff, rw, today)
        if not reg and not final:
            continue
        # cover terms: final prospectus if priced, otherwise the latest amendment (ranges appear only in later amendments)
        pick = final[-1] if final else (reg[-1] if reg else None)
        t_ = None
        if pick:
            doc = info["docs"].get(pick["accn"])
            key = pick["accn"]
            if key in terms:
                t_ = terms[key]
            elif doc and time.time() < deadline:
                try:
                    t_ = price_terms(html_text(_sec(_doc_url(c, pick["accn"], doc), timeout=90)))
                    t_["url"] = _doc_url(c, pick["accn"], doc)
                    t_["form"], t_["date"], t_["accn"], t_["doc"] = pick["form"], pick["date"], pick["accn"], doc
                    terms[key] = t_
                except Exception as e:
                    log(f"terms {c} {pick['accn']}: {str(e)[:120]}")
        if final and reg and time.time() < deadline:
            last_reg = next((r for r in reversed(reg) if info["docs"].get(r["accn"]) and "MEF" not in r["form"]), None)
            if last_reg and last_reg["accn"] not in terms:
                try:
                    tr = price_terms(html_text(_sec(_doc_url(c, last_reg["accn"], info["docs"][last_reg["accn"]]), timeout=90)))
                    tr.update(url=_doc_url(c, last_reg["accn"], info["docs"][last_reg["accn"]]), form=last_reg["form"], date=last_reg["date"],
                              accn=last_reg["accn"], doc=info["docs"][last_reg["accn"]])
                    terms[last_reg["accn"]] = tr
                except Exception as e:
                    log(f"terms {c} {last_reg['accn']}: {str(e)[:120]}")
        # the latest range seen across amendments, if the picked document has none
        rng = (t_ or {}).get("range")
        if not rng:
            for r in reversed(reg):
                tr = terms.get(r["accn"])
                if tr and tr.get("range"):
                    rng = dict(tr["range"], url=tr.get("url"), date=tr.get("date"))
                    break
        elif t_:
            rng = dict(rng, url=t_.get("url"), date=t_.get("date"))
        price = (t_ or {}).get("price") if final else None
        uw = (t_ or {}).get("underwriters")
        if not uw:
            for r in reversed(reg):
                tr = terms.get(r["accn"])
                if tr and tr.get("underwriters"):
                    uw = dict(tr["underwriters"], url=tr.get("url"), date=tr.get("date"))
                    break
        elif t_:
            uw = dict(uw, url=t_.get("url"), date=t_.get("date"))
        shares = (t_ or {}).get("shares")
        sym = (t_ or {}).get("symbol") or {}
        filings = [{"form": r["form"], "date": r["date"], "accn": r["accn"], "doc": info["docs"].get(r["accn"]),
                    "url": _doc_url(c, r["accn"], info["docs"][r["accn"]]) if info["docs"].get(r["accn"]) else f"https://www.sec.gov/Archives/edgar/data/{c}/{r['accn'].replace('-', '')}/"}
                   for r in rs if r["form"] in IPO_FORMS]
        rows.append({
            "cik": c, "name": info.get("name") or rs[-1]["name"], "status": status,
            "spac": str(info.get("sic")) == "6770" or bool(re.search(r"\b(Acquisition Corp|Acquisition Company|SPAC)\b", info.get("name") or "", re.I))
                    or bool(re.search(r"\bunits?\b", ((shares or {}).get("unit") or "") + " " + (sym.get("quote") or ""), re.I) and (price or {}).get("value") == 10.0)
                    or bool((t_ or {}).get("blank")),
            "sic": info.get("sic"), "sic_desc": info.get("sic_desc"), "country": info.get("country"),
            "form": next((r["form"] for r in rs if r["form"] in INITIAL), reg[0]["form"] if reg else None),
            "first_filed": info.get("first_registration") or (reg[0]["date"] if reg else None),
            "last_activity": rs[-1]["date"], "effective": eff[-1]["date"] if eff else None, "priced": final[-1]["date"] if final else None,
            "withdrawn": rw[-1]["date"] if rw else None,
            "symbol": sym.get("value") or (info.get("tickers") or [None])[0], "exchange": sym.get("exchange") or (info.get("exchanges") or [None])[0],
            "symbol_quote": sym.get("quote"), "range": rng,
            "price": price and dict(price, url=t_.get("url"), date=t_.get("date")),
            "shares": shares and dict(shares, url=t_.get("url"), date=t_.get("date")),
            "proceeds": round(price["value"] * shares["value"]) if price and shares else None,
            "direct_listing": bool((t_ or {}).get("direct")),
            "underwriters": uw,
            "filings": filings[-12:],
        })
    state.pop("first_day", None)
    for row in rows:
        if row["status"] != "priced" or not row["symbol"] or time.time() > deadline:
            continue
        try:
            fd = first_day(row, today)
        except Exception as e:
            log(f"first day {row['symbol']}: {str(e)[:120]}")
            fd = None
        if fd:
            row["first_day"] = fd
    live = {r["accn"] for rs in cands.values() for r in rs}
    state["terms"] = {k: v for k, v in terms.items() if k in live}
    _put_cache(sb, IPO_CACHE, state)
    rows.sort(key=lambda r: r["last_activity"], reverse=True)
    out = {"generated": now_iso(), "as_of": today.isoformat(), "window_days": IPO_DAYS, "rows": rows,
           "method": "EDGAR daily form indexes; a registrant counts as an IPO only if its submission history has no 10-K, 10-Q, 20-F or 40-F before its first S-1 or F-1. Exchange-traded products are excluded."}
    sb.put_json("calendars/ipos.json", out, cache="max-age=1800")
    counts = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    log(f"ipos: {len(rows)} registrants {counts}, {round(time.time() - t0)}s")
    return {"rows": len(rows), "status": counts, "pending_classification": len([c for c in cands if str(c) not in state["filers"]])}
