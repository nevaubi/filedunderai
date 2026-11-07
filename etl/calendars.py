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
PAY_W = [r"\bpayable\b", r"\bpaid\b", r"\bpayment date\b", r"\bto be paid\b", r"\bwill be distributed\b"]


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
            if re.search(r"\bpreferred\b|\bseries [a-z]\b", nl) and _amount(nxt) is not None:
                break
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
        if not record and not payable:
            continue
        quote = " ".join(sents[i:end + 1])
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
            out.append({"amount": round(amount, 6), "kind": kind, "record": record and record.isoformat(), "payable": payable and payable.isoformat(),
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


def price_terms(text):
    """Cover-page terms: price range, final price, shares offered, proposed symbol and exchange, each with its sentence."""
    head = re.sub(r"\s+", " ", text[:60000])
    out = {}
    m = re.search(r"between \$\s?(\d+(?:\.\d+)?) and \$\s?(\d+(?:\.\d+)?) per (share|ADS|American Depositary Share|unit)", head, re.I)
    if m:
        out["range"] = {"lo": float(m.group(1)), "hi": float(m.group(2)), "unit": m.group(3), "quote": _around(head, m)}
    m = (re.search(r"initial public offering price (?:is|of|per (?:share|ADS|unit) is)\s*(?:US)?\$\s?(\d+(?:\.\d+)?)", head, re.I)
         or re.search(r"public offering price (?:is|of)\s*(?:US)?\$\s?(\d+(?:\.\d+)?) per (?:share|ADS|American Depositary Share|unit|ordinary share)", head, re.I)
         or re.search(r"offering price (?:is|of)\s*(?:US)?\$\s?(\d+(?:\.\d+)?) per (?:share|ADS|unit)", head, re.I)
         or re.search(r"(?:US)?\$\s?(\d+(?:\.\d+)?) per (?:share|unit|ADS)[^.]{0,40}initial public offering", head, re.I))
    assumed = re.search(r"assumed (?:initial )?public offering price of (?:US)?\$\s?(\d+(?:\.\d+)?)", head, re.I)
    if m and not re.search(r"assumed", head[max(0, m.start() - 30):m.start()], re.I):
        out["price"] = {"value": float(m.group(1)), "quote": _around(head, m)}
    if assumed and "range" not in out:
        v = float(assumed.group(1))
        out["range"] = {"lo": v, "hi": v, "unit": "share", "assumed": True, "quote": _around(head, assumed)}
    m = re.search(r"([\d,]{5,})\s+(shares of (?:our )?(?:Class [A-C] )?(?:common stock|ordinary shares|common shares)|American Depositary Shares|ADSs|units)", head, re.I)
    if m:
        out["shares"] = {"value": int(m.group(1).replace(",", "")), "unit": m.group(2), "quote": _around(head, m)}
    m = re.search(r"(?:under the|under the trading|under the ticker) symbols? [\"']\s?([A-Z]{1,5}(?:\.[A-Z]{1,2})?)\.?\s?[\"']", head)
    if m:
        ctx = _around(head, m)
        ex = re.search(r"Nasdaq Global Select|Nasdaq Global Market|Nasdaq Capital Market|Nasdaq|New York Stock Exchange|NYSE American|NYSE|Cboe", ctx)
        out["symbol"] = {"value": m.group(1), "exchange": ex.group(0) if ex else None, "quote": ctx}
    return out


def _around(s, m, pad=220):
    a = s.rfind(". ", 0, m.start())
    a = a + 2 if a != -1 and m.start() - a < pad * 2 else max(0, m.start() - pad)
    b = s.find(". ", m.end())
    b = b + 1 if b != -1 and b - m.end() < pad * 2 else min(len(s), m.end() + pad)
    return s[a:b].strip()[:700]


def _doc_url(cik, accn, doc):
    return f"{ARCHIVES}/{cik}/{accn.replace('-', '')}/{doc}"


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
        status = "priced" if final else "withdrawn" if rw else "effective" if eff else "amended" if any("/A" in r["form"] for r in reg) else "filed"
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
        shares = (t_ or {}).get("shares")
        sym = (t_ or {}).get("symbol") or {}
        filings = [{"form": r["form"], "date": r["date"], "accn": r["accn"], "doc": info["docs"].get(r["accn"]),
                    "url": _doc_url(c, r["accn"], info["docs"][r["accn"]]) if info["docs"].get(r["accn"]) else f"https://www.sec.gov/Archives/edgar/data/{c}/{r['accn'].replace('-', '')}/"}
                   for r in rs if r["form"] in IPO_FORMS]
        rows.append({
            "cik": c, "name": info.get("name") or rs[-1]["name"], "status": status, "spac": str(info.get("sic")) == "6770" or bool(re.search(r"\b(Acquisition Corp|Acquisition Company|SPAC)\b", info.get("name") or "", re.I)),
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
            "filings": filings[-12:],
        })
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
