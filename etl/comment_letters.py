"""SEC staff comment letters (UPLOAD) and company responses (CORRESP).

The Division of Corporation Finance publishes its review letters and the company's replies on EDGAR at least 20
business days after a review closes. A letter on a 10-K or 10-Q shows what the staff questioned (non-GAAP measures,
segments, revenue recognition, MD&A trends) and the reply often adds disclosure the filing did not have.

Inputs: the filings table (forms CORRESP and UPLOAD). Each document is read once (HTML or PDF) and classified:
  UPLOAD  comments | review_complete | no_review | other
  CORRESP response | acceleration_request | other
Letters are grouped into threads by the filing under review (form and period, or SEC file number).

Outputs (public-data):
  comments/{T}.json     threads for one company with letters, topics and the numbered comments' opening sentences
  comments/recent.json  substantive letters across covered companies in the last 180 days
Raw cache: govraw/comments/parsed.json (parsed letters by accession)."""
import concurrent.futures as cf
import datetime as dt
import io
import re
import time

from .common import now_iso, sec_get, sec_get_raw
from .gov_common import get_raw_json, put_raw_json

CACHE_PATH = "govraw/comments/parsed.json"
PERIODIC = {"10-K", "10-Q", "8-K", "20-F", "40-F", "6-K", "DEF 14A", "10-K/A", "10-Q/A", "20-F/A"}

TOPICS = [
    ("non_gaap", "Non-GAAP measures", r"non-gaap|adjusted ebitda|regulation g\b|item 10\(e\)|non-gaap financial measure"),
    ("revenue", "Revenue recognition", r"revenue recognition|asc 606|performance obligation|principal versus agent|gross versus net"),
    ("segments", "Segment reporting", r"\bsegment|asc 280|chief operating decision maker|\bcodm\b"),
    ("mdna", "MD&A and known trends", r"management.s discussion|results of operations|known trends|item 303"),
    ("liquidity", "Liquidity and capital resources", r"liquidity and capital resources|\bliquidity\b|cash requirements"),
    ("kpi", "Key metrics", r"key performance indicator|operating metric|key metric|\bkpis?\b"),
    ("impairment", "Goodwill and impairment", r"goodwill|impairment"),
    ("estimates", "Critical accounting estimates", r"critical accounting (estimate|polic)"),
    ("tax", "Income taxes", r"income tax|valuation allowance|effective tax rate"),
    ("controls", "Internal controls", r"internal control over financial reporting|disclosure controls"),
    ("contingencies", "Loss contingencies and legal", r"contingenc|asc 450|legal proceedings|litigation"),
    ("china", "China-based operations", r"\bprc\b|people.s republic of china|hong kong|\bvie\b|holding foreign companies accountable"),
    ("crypto", "Crypto assets", r"crypto|digital asset|bitcoin"),
    ("climate", "Climate", r"climate"),
    ("cyber", "Cybersecurity", r"cybersecurity|item 1c\b|item 106"),
    ("compensation", "Executive compensation", r"compensation discussion|pay versus performance|executive compensation"),
    ("reserves", "Oil and gas reserves", r"proved reserves|proved undeveloped|\bpuds?\b"),
    ("fair_value", "Fair value", r"fair value"),
    ("leases", "Leases", r"\blease"),
    ("derivatives", "Derivatives and hedging", r"derivative|hedg"),
    ("acquisitions", "Acquisitions", r"business combination|acquisition|purchase price allocation|pro forma"),
    ("risk_factors", "Risk factors", r"risk factor"),
]
TOPICS = [(k, n, re.compile(rx, re.I)) for k, n, rx in TOPICS]
FORM_RX = re.compile(r"\bForm\s+(10-K/A|10-Q/A|20-F/A|10-K|10-Q|8-K|20-F|40-F|6-K|S-1|S-3|S-4|F-1|F-3|F-4|S-8|S-11|10-12B|DEF 14A|PRE 14A|SC TO-T|SC 13E-3)\b", re.I)
PERIOD_RX = re.compile(r"(?:fiscal\s+)?(?:year|quarter|period)\s+ended\s+([A-Z][a-z]+\s+\d{1,2},\s+\d{4})", re.I)
FILENO_RX = re.compile(r"File\s+No\.?:?\s*(\d{3}-\d{4,6})", re.I)
DATED_RX = re.compile(r"letter\s+dated\s+([A-Z][a-z]+\s+\d{1,2},\s+\d{4})", re.I)


def to_text(raw, url):
    """Plain text of an HTML, text or PDF letter."""
    if url.lower().endswith(".pdf") or raw[:5] == b"%PDF-":
        import pdfplumber
        with pdfplumber.open(io.BytesIO(raw)) as pdf:
            return "\n".join((pg.extract_text() or "") for pg in pdf.pages[:40])
    s = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
    if "<" in s[:2000]:
        from bs4 import BeautifulSoup
        return BeautifulSoup(s, "lxml").get_text("\n", strip=True)
    return s


def _iso(s):
    try:
        return dt.datetime.strptime(re.sub(r"\s+", " ", s.strip()), "%B %d, %Y").date().isoformat()
    except (ValueError, AttributeError):
        return None


def kind_of(form, text):
    low = re.sub(r"\s+", " ", text.lower())
    if form == "UPLOAD":
        if "have not reviewed and will not review" in low or "will not review your" in low:
            return "no_review"
        if "completed our review" in low:
            return "review_complete"
        if re.search(r"following comments?|we have (limited our review|reviewed your)|please respond to (this|these) comment", low):
            return "comments"
        return "other"
    if "acceleration" in low and ("rule 461" in low or "effective date" in low):
        return "acceleration_request"
    if re.search(r"in response to|responds? to the comment|comment letter dated|staff.s (letter|comments)|letter dated .{0,40} from the staff", low):
        return "response"
    return "other"


def numbered_comments(text, limit=30):
    """Opening sentence of each numbered comment ('1. Please revise ...'), with the heading line above it."""
    lines = [l.strip() for l in text.splitlines()]
    out = []
    for i, l in enumerate(lines):
        m = re.match(r"^(\d{1,2})\.\s+(.{20,})", l)
        if not m:
            continue
        body = m.group(2)
        j = i + 1
        while len(body) < 320 and j < len(lines) and lines[j] and not re.match(r"^\d{1,2}\.\s", lines[j]):
            body += " " + lines[j]
            j += 1
        first = re.split(r"(?<=[.?])\s+(?=[A-Z])", body, maxsplit=1)[0][:320]
        head = lines[i - 1] if i and lines[i - 1] and len(lines[i - 1]) < 120 and not lines[i - 1].endswith(".") else None
        out.append({"n": int(m.group(1)), "heading": head, "text": first})
        if len(out) >= limit:
            break
    # keep one ascending run (page numbers and addresses also start with digits)
    run, last = [], 0
    for c in out:
        if c["n"] == last + 1:
            run.append(c)
            last = c["n"]
    return run


def topics_of(text):
    return [k for k, _n, rx in TOPICS if rx.search(text)]


def parse(form, text):
    head = text[:2500]
    subj = None
    m = re.search(r"Re:\s*(.+?)(?:\n\s*Dear|\n\s*Ladies|Dear\s)", head, re.S)
    if m:
        subj = re.sub(r"\s+", " ", m.group(1)).strip()[:300]
    scope = subj or head
    fm = FORM_RX.search(scope)
    pm = PERIOD_RX.search(scope)
    fn = FILENO_RX.search(scope) or FILENO_RX.search(head)
    kind = kind_of(form, text)
    comments = numbered_comments(text) if kind in ("comments", "response") else []
    body = " ".join([c["text"] + " " + (c.get("heading") or "") for c in comments]) or (text[:20000] if kind in ("comments", "response") else "")
    return {
        "kind": kind,
        "subject": subj,
        "subject_form": fm.group(1).upper() if fm else None,
        "period": _iso(pm.group(1)) if pm else None,
        "file_no": fn.group(1) if fn else None,
        "replies_to": _iso(DATED_RX.search(text[:4000]).group(1)) if DATED_RX.search(text[:4000]) else None,
        "n_comments": len(comments),
        "comments": comments,
        "topics": topics_of(body),
        "chars": len(text),
    }


def thread_key(r):
    if r.get("subject_form") and r.get("period"):
        return f"{r['subject_form']}|{r['period']}"
    if r.get("file_no"):
        return f"file|{r['file_no']}|{(r.get('subject_form') or '')}"
    return f"date|{(r.get('filed') or '')[:7]}"


def threads_for(letters):
    """Groups a company's letters into review threads, newest first. Acceleration requests and no-review notices
    are kept out (they are procedural)."""
    keep = [l for l in letters if l.get("kind") not in ("acceleration_request", "no_review") and not l.get("error")]
    by = {}
    for l in sorted(keep, key=lambda x: x.get("filed") or ""):
        by.setdefault(thread_key(l), []).append(l)
    out = []
    for k, ls in by.items():
        sec_letters = [l for l in ls if l["form"] == "UPLOAD"]
        status = "complete" if any(l["kind"] == "review_complete" for l in sec_letters) else "open"
        topics = sorted({t for l in ls for t in l.get("topics") or []})
        first = next((l for l in ls if l["kind"] in ("comments", "response")), ls[0])
        out.append({"key": k, "subject": first.get("subject"), "form": first.get("subject_form"), "period": first.get("period"),
                    "first": ls[0]["filed"], "last": ls[-1]["filed"], "status": status, "rounds": sum(1 for l in sec_letters if l["kind"] == "comments"),
                    "n_comments": sum(l.get("n_comments") or 0 for l in sec_letters), "topics": topics,
                    "periodic": (first.get("subject_form") or "") in PERIODIC,
                    "letters": [{k2: l.get(k2) for k2 in ("accession", "form", "filed", "kind", "url", "n_comments", "comments", "topics", "replies_to")}
                                for l in reversed(ls)]})
    out.sort(key=lambda t: t["last"], reverse=True)
    return out


TOPIC_NAME = {k: n for k, n, _ in TOPICS}


def comments_job(sb, days=1100, workers=4, time_budget=600, log=print):
    t0 = time.time()
    today = dt.date.today()
    since = (today - dt.timedelta(days=days)).isoformat()
    rows = sb.select("filings", f"select=accession,ticker,form,filed,url&form=in.(CORRESP,UPLOAD)&filed=gte.{since}&order=filed.desc")
    cache = get_raw_json(sb, CACHE_PATH, {}) or {}
    todo = [r for r in rows if r["accession"] not in cache and r.get("url")]
    log(f"comments: {len(rows)} letters since {since}, {len(todo)} to read")

    def one(r):
        if time.time() - t0 > time_budget:
            return r, None, "budget"
        try:
            url = r["url"]
            raw = sec_get_raw(url) if url.lower().endswith(".pdf") else sec_get(url)
            return r, parse(r["form"], to_text(raw, url)), None
        except Exception as e:
            return r, None, str(e)[:160]
    n = 0
    with cf.ThreadPoolExecutor(workers) as ex:
        for r, rec, err in ex.map(one, todo):
            base = {"ticker": r["ticker"], "form": r["form"], "filed": r["filed"], "accession": r["accession"], "url": r["url"]}
            if rec:
                cache[r["accession"]] = {**base, **rec}
                n += 1
            elif err != "budget":
                cache[r["accession"]] = {**base, "error": err}
    put_raw_json(sb, CACHE_PATH, cache)

    by_t = {}
    for v in cache.values():
        by_t.setdefault(v["ticker"], []).append(v)
    recent_since = (today - dt.timedelta(days=180)).isoformat()
    recent = []

    def build(item):
        t, letters = item
        th = threads_for(letters)
        if th:
            sb.put_json(f"comments/{t}.json", {"ticker": t, "generated": now_iso(), "threads": th[:60],
                                                "topics": TOPIC_NAME, "n_threads": len(th)}, cache="max-age=3600")
        rec = []
        for x in th:
            for l in x["letters"]:
                if l["form"] == "UPLOAD" and l["kind"] in ("comments", "review_complete") and l["filed"] >= recent_since:
                    rec.append({"ticker": t, "filed": l["filed"], "accession": l["accession"], "url": l["url"], "kind": l["kind"],
                                "subject": x["subject"], "form": x["form"], "period": x["period"], "periodic": x["periodic"],
                                "n_comments": l.get("n_comments"), "topics": l.get("topics") or x["topics"], "thread_status": x["status"]})
        return bool(th), rec
    written = 0
    with cf.ThreadPoolExecutor(12) as ex:
        for ok, rec in ex.map(build, sorted(by_t.items())):
            written += ok
            recent += rec
    recent.sort(key=lambda r: r["filed"], reverse=True)
    sb.put_json("comments/recent.json", {"generated": now_iso(), "since": recent_since, "n": len(recent), "topics": TOPIC_NAME,
                                          "rows": recent[:500]}, cache="max-age=3600")
    return {"letters": len(rows), "read": n, "companies": written, "recent": len(recent), "seconds": round(time.time() - t0)}
