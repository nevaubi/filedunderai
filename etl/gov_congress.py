"""Members of Congress, committee assignments and their stock trades (periodic transaction reports).

House: the Clerk's yearly financial-disclosure index ZIP (disclosures-clerk.house.gov/public_disc/financial-pdfs/
{YEAR}FD.zip) lists every filing; FilingType P is a periodic transaction report whose PDF lives at
public_disc/ptr-pdfs/{YEAR}/{DocID}.pdf. Electronically filed PTRs are text PDFs and are parsed line by line;
scanned paper filings (7-digit DocIDs) carry no text layer and are listed as unparsed with their link.

Senate: efdsearch.senate.gov requires the requester to affirm the use restrictions in 5 U.S.C. app. 105(c) before
any report can be read. The pipeline never affirms that on the owner's behalf: Senate collection only runs when the
deployment sets FU_EFD_ACKNOWLEDGED=1 (the owner's own affirmation) and FIRECRAWL_API_KEY (Alexandria
efdsearch-senate-gov provider). Otherwise the source is reported as not collected.

Members and committees: the unitedstates/congress-legislators project (legislators-current.json,
committee-membership-current.json, committees-current.json), which is compiled from official House/Senate sources.

Trades are mapped to our tickers only when the report names a ticker in parentheses that is one of ours AND the
asset name shares the company's first distinctive name word. Everything else stays in the unmatched log.
"""
import datetime as dt
import io
import json
import os
import re
import zipfile
from concurrent.futures import ThreadPoolExecutor

from .common import now_iso
from .gov_common import (UA_GOV, fetch, get_raw, get_raw_json, overseeing_committees, put_public, put_raw,
                         put_raw_json, src, symbol_map, Deadline)
from .universe import BY_TICKER

LEG_BASE = "https://unitedstates.github.io/congress-legislators/"
HOUSE_ZIP = "https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{year}FD.zip"
HOUSE_PTR = "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/{year}/{doc}.pdf"
HOUSE_SEARCH = "https://disclosures-clerk.house.gov/FinancialDisclosure"
PTR_CACHE = "govraw/house-ptr/parsed.json"
LEGAL_NOTE = ("Financial disclosure reports are public under the Ethics in Government Act. 5 U.S.C. app. 105(c) makes it "
              "unlawful to obtain or use a report for any unlawful purpose, for any commercial purpose other than by news "
              "and communications media for dissemination to the general public, for determining anyone's credit rating, "
              "or for soliciting money.")
TX_TYPES = {"P": "Purchase", "S": "Sale", "S (partial)": "Partial sale", "E": "Exchange"}
ASSET_TYPES = {"ST": "Stock", "OP": "Stock option", "OT": "Other", "MF": "Mutual fund", "EF": "ETF", "CS": "Corporate bond",
               "GS": "Government security", "PS": "Stock (private)", "OI": "Other investment", "AB": "Asset-backed",
               "CT": "Cryptocurrency", "HN": "Hedge fund", "FN": "Fund", "RE": "Real estate", "PE": "Private equity"}


# ---------------------------------------------------------------- members and committees

def load_members(log=print):
    """bioguide -> {name, party, chamber, state, district, committees: [{id, name, title}]} plus lookups."""
    leg = json.loads(fetch(LEG_BASE + "legislators-current.json", timeout=90))
    comm = json.loads(fetch(LEG_BASE + "committees-current.json", timeout=90))
    mem = json.loads(fetch(LEG_BASE + "committee-membership-current.json", timeout=90))
    names = {}
    for c in comm:
        names[c["thomas_id"]] = c["name"]
        for s in c.get("subcommittees") or []:
            names[c["thomas_id"] + s["thomas_id"]] = f"{c['name']}: {s['name']} Subcommittee"
    members = {}
    for p in leg:
        t = p["terms"][-1]
        bid = p["id"]["bioguide"]
        members[bid] = {"bioguide": bid, "name": p["name"].get("official_full") or f"{p['name']['first']} {p['name']['last']}",
                        "first": p["name"]["first"], "last": p["name"]["last"], "party": t.get("party"),
                        "chamber": "House" if t["type"] == "rep" else "Senate", "state": t.get("state"),
                        "district": t.get("district"), "fec": p["id"].get("fec") or [], "committees": [], "subcommittees": []}
    for cid, rows in mem.items():
        full = re.match(r"^[A-Z]{4}$", cid) is not None
        for r in rows:
            m = members.get(r.get("bioguide"))
            if not m:
                continue
            entry = {"id": cid, "name": names.get(cid, cid)}
            if r.get("title"):
                entry["title"] = r["title"]
            (m["committees"] if full else m["subcommittees"]).append(entry)
    retrieved = now_iso()
    return {"generated": retrieved, "members": members, "committee_names": {k: v for k, v in names.items() if len(k) == 4},
            "src": src(LEG_BASE + "legislators-current.json", "unitedstates/congress-legislators JSON (current members, committee membership)", retrieved)}


def _norm(s):
    return re.sub(r"[^a-z]", "", (s or "").lower())


def match_member(members, last, first, state_dst, chamber="House"):
    """Finds the current member for a disclosure filer by state(+district) and last name. None when not certain."""
    st = (state_dst or "")[:2]
    dist = state_dst[2:] if state_dst and len(state_dst) > 2 else None
    cands = [m for m in members.values() if m["chamber"] == chamber and m["state"] == st and _norm(m["last"]) == _norm(last)]
    if dist and chamber == "House":
        exact = [m for m in cands if m.get("district") is not None and int(m["district"]) == int(dist or 0)]
        cands = exact or cands
    if len(cands) > 1:
        cands = [m for m in cands if _norm(m["first"])[:3] == _norm(first)[:3]] or cands
    return cands[0] if len(cands) == 1 else None


# ---------------------------------------------------------------- House PTR index and parsing

def house_index(year):
    """PTR filings listed in the Clerk's yearly index: [{doc, last, first, state_dst, filed, year}]."""
    body = fetch(HOUSE_ZIP.format(year=year), raw=True, timeout=120)
    z = zipfile.ZipFile(io.BytesIO(body))
    name = next(n for n in z.namelist() if n.lower().endswith(".xml"))
    x = z.read(name).decode("utf-8-sig", "replace")
    out = []
    for m in re.findall(r"<Member>(.*?)</Member>", x, re.S):
        g = lambda tag: (re.search(rf"<{tag}>(.*?)</{tag}>", m, re.S) or [None, ""])[1].strip()
        if g("FilingType") != "P":
            continue
        try:
            filed = dt.datetime.strptime(g("FilingDate"), "%m/%d/%Y").date().isoformat()
        except ValueError:
            filed = None
        out.append({"doc": g("DocID"), "last": g("Last"), "first": g("First"), "prefix": g("Prefix"), "state_dst": g("StateDst"),
                    "filed": filed, "year": int(g("Year") or year)})
    return out


ANCHOR = re.compile(r"^(?:(SP|JT|DC)\s+)?(.*?)\s*(?<![A-Za-z])(P|S \(partial\)|S|E)\s+(\d{2}/\d{2}/\d{4})\s+(\d{2}/\d{2}/\d{4})\s+(.*)$")
AMOUNT = re.compile(r"(Over \$[\d,]+|\$[\d,]+\s*-\s*\$[\d,]+|\$[\d,]+\s*-\s*$|\$[\d,]+)")
FIELD = re.compile(r"^(?:F\s*S\s*:|S\s*O\s*:|D\s*:|L\s*:|C\s*:|ID\s+Owner|Type\s+Date|Gains\s*>|\$200\?|Filing ID|\* For|I\s*P\s*O|I\s*V\s*D|Filing Status|Subholding|Description|Location|Comments)")


def fix_wrap(asset, amount):
    """An amount range broken across lines ("$15,001 -" ... "$50,000") leaves its upper bound in the asset text."""
    if (amount or "").rstrip().endswith("-"):
        tail = re.search(r"\s(\$[\d,]+)(?=\s|$)", asset or "")
        if tail:
            return (asset[:tail.start()] + asset[tail.end():]).strip(), amount.rstrip() + " " + tail.group(1)
    return asset, amount


def _date(s):
    try:
        return dt.datetime.strptime(s, "%m/%d/%Y").date().isoformat()
    except ValueError:
        return None


def _amount_bounds(text):
    nums = [int(x.replace(",", "")) for x in re.findall(r"\$([\d,]+)", text or "")]
    if not nums:
        return None, None
    if text.strip().lower().startswith("over"):
        return nums[0] + 1, None
    return nums[0], nums[1] if len(nums) > 1 else nums[0]


def parse_ptr_text(pages):
    """Transactions from the text of an electronically filed House PTR. pages: list of page strings.

    Returns [{owner, asset, ticker_reported, asset_type, type, tx_date, notified, amount, amin, amax, page, cited}].
    `cited` is the exact text block (anchor line plus continuation lines) as extracted from the PDF.
    """
    out = []
    for pi, text in enumerate(pages):
        lines = [l.rstrip() for l in (text or "").splitlines()]
        i = 0
        while i < len(lines):
            m = ANCHOR.match(lines[i].strip())
            if not m or not m.group(2):
                i += 1
                continue
            owner, asset, typ, d1, d2, rest = m.groups()
            block = [lines[i].strip()]
            am = AMOUNT.search(rest)
            amount = am.group(1).strip() if am else rest.strip()
            j = i + 1
            if amount.endswith("-") and j < len(lines):
                nxt = re.match(r"^\s*(\$[\d,]+)(.*)$", lines[j])
                if nxt:
                    amount = amount + " " + nxt.group(1)
                    block.append(lines[j].strip())
                    asset_tail = nxt.group(2).strip()
                    if asset_tail:
                        asset = (asset + " " + asset_tail).strip()
                    j += 1
            cont = 0 if not re.search(r"\[[A-Z]{2}\]", asset) else 3
            while j < len(lines) and cont < 3:
                s = lines[j].strip()
                if not s or FIELD.match(s) or ANCHOR.match(s):
                    break
                block.append(s)
                asset = (asset + " " + s).strip()
                j += 1
                cont += 1
                if re.search(r"\[[A-Z]{2}\]", s):
                    break
            asset, amount = fix_wrap(asset, amount)
            tick = re.search(r"\(([A-Z][A-Z0-9.\-/]{0,7})\)", asset)
            at = re.search(r"\[([A-Z]{2})\]", asset)
            amin, amax = _amount_bounds(amount)
            out.append({"owner": owner or "Self", "asset": re.sub(r"\s+", " ", asset).strip(), "ticker_reported": tick.group(1) if tick else None,
                        "asset_type": at.group(1) if at else None, "type": TX_TYPES.get(typ, typ), "type_code": typ,
                        "tx_date": _date(d1), "notified": _date(d2), "amount": re.sub(r"\s+", " ", amount), "amin": amin, "amax": amax,
                        "page": pi + 1, "cited": "\n".join(block)})
            i = j
    return out


def pdf_pages(data):
    import pdfplumber
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        # glyphs without a Unicode mapping come out as NUL; drop them so field labels read "F S: New"
        return [(p.extract_text() or "").replace("\x00", "") for p in pdf.pages]


def name_ok(tk, asset, legal=()):
    """The asset name must contain the first distinctive word of the company's display or SEC legal name
    (guards against ticker collisions and recycled symbols)."""
    from .altdata import core_name, norm_name
    info = BY_TICKER.get(tk) or {}
    a = norm_name(asset or "")
    flat = a.replace(" ", "")
    from .gov_common import BRANDS
    for n in [info.get("name")] + list(legal or []) + BRANDS.get(tk, []):
        words = [w for w in core_name(n or "").split() if w]
        if not words:
            continue
        first = re.sub(r"[^A-Z0-9]", "", words[0])
        if len(words) > 1 and len(first) <= 2:
            first = re.sub(r"[^A-Z0-9]", "", "".join(words[:3]))  # "S AND P GLOBAL" -> SANDPGLOBAL
        if not first:
            continue
        if re.search(r"\b" + re.escape(words[0]) + r"\b", a) or (len(first) >= 4 and first in flat):
            return True
    return False


def map_ticker(t, syms):
    if not t:
        return None
    t = t.upper().strip()
    return syms.get(t) or syms.get(t.replace(".", "-")) or syms.get(t.replace("/", "-"))


# ---------------------------------------------------------------- collection

def collect_house(sb, years, deadline=None, workers=4, log=print, store_raw=True):
    """Downloads and parses new House PTRs. The parse cache (raw bucket) keeps every parsed DocID, so later runs only
    fetch new filings. Returns the cache: {"docs": {doc: {...meta, transactions, status}}}."""
    cache = get_raw_json(sb, PTR_CACHE, {"docs": {}}) or {"docs": {}}
    docs = cache["docs"]
    todo = []
    for y in years:
        try:
            idx = house_index(y)
        except Exception as e:
            log(f"house index {y} failed: {str(e)[:160]}")
            cache.setdefault("index_errors", {})[str(y)] = str(e)[:200]
            continue
        cache.setdefault("index_checked", {})[str(y)] = now_iso()
        for f in idx:
            if f["doc"] not in docs or docs[f["doc"]].get("status") == "error":
                todo.append(f)
            else:
                docs[f["doc"]].update({k: f[k] for k in ("last", "first", "prefix", "state_dst", "filed", "year")})
    log(f"house PTRs to fetch: {len(todo)}")

    def one(f):
        url = HOUSE_PTR.format(year=f["year"], doc=f["doc"])
        rec = dict(f, url=url, retrieved_at=now_iso())
        if len(f["doc"]) < 8:
            rec.update(status="paper", transactions=[], note="Scanned paper filing without a text layer; not parsed.")
            return rec
        try:
            data = fetch(url, host_gap=0.15, raw=True, timeout=90)
            if not data.startswith(b"%PDF"):
                rec.update(status="error", transactions=[], note="Clerk returned a non-PDF response")
                return rec
            if store_raw:
                rec["raw_path"] = put_raw(sb, f"govraw/house-ptr/{f['year']}/{f['doc']}.pdf", data, "application/gzip")
            pages = pdf_pages(data)
            tx = parse_ptr_text(pages)
            rec.update(status="parsed" if tx else "empty", transactions=tx, pages=len(pages))
            if not tx:
                rec["note"] = "No transaction lines recognised in the text layer."
        except Exception as e:
            rec.update(status="error", transactions=[], note=str(e)[:200])
        return rec

    done = 0
    with ThreadPoolExecutor(workers) as ex:
        futs = []
        for f in todo:
            futs.append(ex.submit(one, f))
        for fu in futs:
            if deadline and deadline.passed(30):
                fu.cancel()
                continue
            try:
                r = fu.result()
            except Exception as e:
                log("ptr failed", str(e)[:120])
                continue
            docs[r["doc"]] = r
            done += 1
            if done % 100 == 0:
                put_raw_json(sb, PTR_CACHE, cache)
                log(f"  ptr parsed {done}/{len(todo)}")
    cache["updated_at"] = now_iso()
    put_raw_json(sb, PTR_CACHE, cache)
    return cache


def senate_enabled():
    return os.environ.get("FU_EFD_ACKNOWLEDGED") == "1" and bool(os.environ.get("FIRECRAWL_API_KEY"))


def _alexandria(provider, capability, options, timeout=120):
    """Executes one Firecrawl Alexandria capability (production path; requires FIRECRAWL_API_KEY)."""
    from .common import request
    key = os.environ["FIRECRAWL_API_KEY"]
    body = {"alexandria": {"provider": provider, "capability": capability, "options": options}}
    out = json.loads(request("https://api.firecrawl.dev/v2/scrape", method="POST", data=body,
                             headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"}, timeout=timeout, retries=2))
    res = ((out.get("data") or {}).get("alexandria") or [{}])[0]
    if res.get("error"):
        raise RuntimeError(str(res["error"])[:200])
    return res.get("data") or {}


def collect_senate(sb, since, deadline=None, log=print, max_reports=400):
    """Senate PTR transactions via the Alexandria efdsearch-senate-gov provider. Only runs when senate_enabled()."""
    cache = get_raw_json(sb, "govraw/senate-ptr/parsed.json", {"reports": {}}) or {"reports": {}}
    if not senate_enabled():
        cache["status"] = "not_collected"
        return cache
    start, seen = 0, 0
    while seen < max_reports and not (deadline and deadline.passed(60)):
        page = _alexandria("efdsearch-senate-gov", "financial-disclosures/search_reports",
                           {"acknowledge_use_prohibitions": True, "report_types": ["ptr"], "submitted_from": since,
                            "page_size": 100, **({"continuation": str(start)} if start else {})})
        reps = page.get("reports") or []
        if not reps:
            break
        for r in reps:
            seen += 1
            if r["report_id"] in cache["reports"] or r.get("report_kind") != "ptr":
                if r.get("report_kind") == "paper" and r["report_id"] not in cache["reports"]:
                    cache["reports"][r["report_id"]] = {**r, "status": "paper", "transactions": []}
                continue
            try:
                d = _alexandria("efdsearch-senate-gov", "financial-disclosures/get_report",
                                {"acknowledge_use_prohibitions": True, "report_id": r["report_id"], "report_kind": "ptr"})
                cache["reports"][r["report_id"]] = {**r, "status": "parsed", "filer_name": d.get("filer_name"),
                                                     "filed": d.get("filed_date"), "transactions": d.get("transactions") or [],
                                                     "retrieved_at": now_iso()}
            except Exception as e:
                log("senate report", r["report_id"], str(e)[:120])
        start += len(reps)
        if start >= (page.get("records_total") or 0):
            break
    cache["status"] = "ok"
    cache["updated_at"] = now_iso()
    put_raw_json(sb, "govraw/senate-ptr/parsed.json", cache)
    return cache


# ---------------------------------------------------------------- distribute to companies

def build_trades(house_cache, senate_cache, members_doc, legal=None, log=print):
    """Per-ticker trade lists plus the unmatched log and coverage counts."""
    members = members_doc["members"]
    names = members_doc["committee_names"]
    syms = symbol_map()
    per, unmatched = {}, []
    counts = {"house_filings": 0, "house_parsed": 0, "house_paper": 0, "house_errors": 0, "house_transactions": 0,
              "matched": 0, "senate_filings": 0, "senate_transactions": 0}
    for doc, d in (house_cache.get("docs") or {}).items():
        counts["house_filings"] += 1
        st = d.get("status")
        counts["house_parsed" if st in ("parsed", "empty") else "house_paper" if st == "paper" else "house_errors"] += 1
        mem = match_member(members, d.get("last"), d.get("first"), d.get("state_dst"))
        for k, t in enumerate(d.get("transactions") or []):
            counts["house_transactions"] += 1
            if (t.get("amount") or "").rstrip().endswith("-"):
                a2, m2 = fix_wrap(t["asset"], t["amount"])
                if m2 != t["amount"]:
                    lo, hi = _amount_bounds(m2)
                    t = {**t, "asset": a2, "amount": m2, "amin": lo, "amax": hi}
            tk = map_ticker(t.get("ticker_reported"), syms)
            ok = tk and t.get("asset_type") in (None, "ST", "OP") and name_ok(tk, t["asset"], (legal or {}).get(tk, ()))
            if not ok:
                if t.get("ticker_reported") and t.get("asset_type") in (None, "ST", "OP"):
                    unmatched.append({"chamber": "House", "doc": doc, "asset": t["asset"][:160], "ticker_reported": t.get("ticker_reported"),
                                      "reason": "ticker not in coverage" if not tk else "asset name does not match the company"})
                continue
            counts["matched"] += 1
            lag = None
            if t.get("tx_date") and d.get("filed"):
                lag = (dt.date.fromisoformat(d["filed"]) - dt.date.fromisoformat(t["tx_date"])).days
            rec = {"id": f"house:{doc}:{k}", "chamber": "House", "member": (mem or {}).get("name") or f"{d.get('first')} {d.get('last')}".strip(),
                   "bioguide": (mem or {}).get("bioguide"), "party": (mem or {}).get("party"), "state_dst": d.get("state_dst"),
                   "owner": t["owner"], "asset": t["asset"], "asset_type": ASSET_TYPES.get(t.get("asset_type"), t.get("asset_type")),
                   "ticker_reported": t.get("ticker_reported"), "type": t["type"], "type_code": t.get("type_code"),
                   "tx_date": t.get("tx_date"), "notified": t.get("notified"), "filed": d.get("filed"), "lag_days": lag,
                   "amount": t["amount"], "amin": t.get("amin"), "amax": t.get("amax"), "doc_id": doc, "page": t.get("page"),
                   "pdf_url": d.get("url"), "cited": t.get("cited"),
                   "member_matched": bool(mem), "committees": [c["name"] for c in (mem or {}).get("committees", [])],
                   "committee_ids": [c["id"] for c in (mem or {}).get("committees", [])],
                   "src": src(d.get("url"), "House Clerk PTR PDF, text layer parsed (pdfplumber)", d.get("retrieved_at"))}
            per.setdefault(tk, []).append(rec)
    for rid, r in (senate_cache.get("reports") or {}).items():
        counts["senate_filings"] += 1
        mem = None
        for m in members.values():
            if m["chamber"] == "Senate" and _norm(m["last"]) == _norm(r.get("last_name")) and _norm(m["first"])[:3] == _norm(r.get("first_name"))[:3]:
                mem = m
        for k, t in enumerate(r.get("transactions") or []):
            counts["senate_transactions"] += 1
            tk = map_ticker(t.get("ticker"), syms)
            if not tk or not name_ok(tk, t.get("asset_name"), (legal or {}).get(tk, ())):
                if t.get("ticker"):
                    unmatched.append({"chamber": "Senate", "doc": rid, "asset": (t.get("asset_name") or "")[:160], "ticker_reported": t.get("ticker"),
                                      "reason": "ticker not in coverage" if not tk else "asset name does not match the company"})
                continue
            counts["matched"] += 1
            txd = t.get("transaction_date") or t.get("date")
            lag = None
            try:
                lag = (dt.date.fromisoformat(r.get("filed")) - dt.date.fromisoformat(txd)).days
            except (TypeError, ValueError):
                pass
            per.setdefault(tk, []).append({
                "id": f"senate:{rid}:{k}", "chamber": "Senate", "member": (mem or {}).get("name") or r.get("filer_name"),
                "bioguide": (mem or {}).get("bioguide"), "party": (mem or {}).get("party"), "state_dst": (mem or {}).get("state"),
                "owner": t.get("owner"), "asset": t.get("asset_name"), "asset_type": t.get("asset_type"), "ticker_reported": t.get("ticker"),
                "type": t.get("transaction_type") or t.get("type"), "tx_date": txd, "filed": r.get("filed"), "lag_days": lag,
                "amount": t.get("amount") or t.get("amount_text"), "amin": t.get("amount_min"), "amax": t.get("amount_max"),
                "doc_id": rid, "pdf_url": r.get("report_url"), "cited": t.get("asset_name"), "member_matched": bool(mem),
                "committees": [c["name"] for c in (mem or {}).get("committees", [])], "committee_ids": [c["id"] for c in (mem or {}).get("committees", [])],
                "src": src(r.get("report_url"), "Senate eFD PTR via Firecrawl Alexandria efdsearch-senate-gov", r.get("retrieved_at"))})
    for tk, rows in per.items():
        over = set(overseeing_committees(tk))
        for r in rows:
            hits = [cid for cid in r.get("committee_ids", []) if cid in over]
            r["committee_flag"] = bool(hits)
            r["flag_committees"] = [names.get(c, c) for c in hits]
        rows.sort(key=lambda r: (r.get("tx_date") or "", r["id"]), reverse=True)
    return per, unmatched, counts


def publish_members(sb, members_doc):
    put_public(sb, "gov/members.json", {"generated": members_doc["generated"], "src": members_doc["src"],
                                        "members": list(members_doc["members"].values()),
                                        "committees": members_doc["committee_names"]}, cache="max-age=3600")
