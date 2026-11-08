"""Alternative data per company, published to altdata/{T}.json for the company page's Alt data tab.

Sources, all public and tied to the company without fuzzy matching:
- Off-exchange short-sale volume: FINRA Reg SHO daily files (consolidated NMS), matched by symbol.
- Fails-to-deliver: SEC half-monthly fails-to-deliver files, matched by symbol.
- Federal lobbying: Lobbying Disclosure Act filings (lda.gov), clients matched on the exact normalized legal name
  (current or former SEC name). Each quarter uses the client's own in-house expense report when it filed one,
  otherwise the sum of outside firms' reported income; amendments replace the report they amend.
- Federal contract obligations: USAspending.gov, recipients matched on the exact normalized legal name.

Market-file caches (raw bucket, altdata/) hold only universe symbols, so a publish reads two objects, not hundreds.
"""
import datetime as dt
import gzip
import io
import json
import re
import time
import urllib.parse
import zipfile
from concurrent.futures import ThreadPoolExecutor

from .common import Run, now_iso, request, sec_throttle, SEC_UA
from .universe import BY_TICKER, UNIVERSE

UA = {"User-Agent": SEC_UA}
FINRA_URL = "https://cdn.finra.org/equity/regsho/daily/CNMSshvol{d}.txt"
FTD_URL = "https://www.sec.gov/files/data/fails-deliver-data/cnsfails{p}.zip"
LDA_URL = "https://lda.gov/api/v1/filings/"
USA_URL = "https://api.usaspending.gov/api/v2/"
SHORT_CACHE = "altdata/finra_short_volume.json.gz"
FTD_CACHE = "altdata/sec_ftd.json.gz"
SHORT_DAYS = 400            # calendar days of FINRA history kept
FTD_MONTHS = 14             # months of fails-to-deliver files kept
LOBBY_FROM = 2019           # first filing year collected
CONTRACT_FY_FROM = 2017     # first federal fiscal year collected
REFRESH_DAYS = 7            # lobbying/contracts refresh cadence per company
CONTRACT_TYPES = ["A", "B", "C", "D"]


# ---------- names and symbols ----------

SUFFIX = {"CORPORATION": "CORP", "INCORPORATED": "INC", "COMPANY": "CO", "LIMITED": "LTD", "HOLDING": "HOLDINGS"}
LEGAL_TAIL = {"INC", "CORP", "CO", "LTD", "LLC", "LP", "PLC", "NV", "SA", "AG", "SE", "HOLDINGS", "GROUP", "TRUST", "THE"}


def norm_name(s):
    """Upper-case legal name with punctuation, state tags ('/DE/') and suffix spellings normalized."""
    s = (s or "").upper().replace("&", " AND ")
    s = re.sub(r"\s*/(?:[A-Z]{2,3}/?)?\s*$", "", s.strip())
    s = s.replace("-", " ")
    s = re.sub(r"\bN\.?\s?V\.?\b", "NV", s)
    s = re.sub(r"\bP\.?\s?L\.?\s?C\.?\b", "PLC", s)
    s = re.sub(r"[.,'’\"()]", " ", s)
    words = [SUFFIX.get(w, w) for w in s.split()]
    if words and words[0] == "THE":
        words = words[1:]
    return " ".join(words)


def core_name(s):
    """Legal name without trailing entity words, used as the search phrase ('LOCKHEED MARTIN')."""
    words = norm_name(s).split()
    while len(words) > 1 and words[-1] in LEGAL_TAIL:
        words.pop()
    return " ".join(words)


def symbol_variants(tk):
    return list(dict.fromkeys([tk, tk.replace("-", "/"), tk.replace("-", "."), tk.replace("-", "")]))


def legal_names(sb, tk):
    """Current and former SEC registrant names (former names changed after 2014)."""
    names = []
    row = sb.select("companies", f"select=name&ticker=eq.{urllib.parse.quote(tk)}")
    if row and row[0].get("name"):
        names.append(row[0]["name"])
    try:
        from . import sec
        sub = sec.submissions(BY_TICKER[tk]["cik"])
        if sub.get("name"):
            names.append(sub["name"])
        for f in sub.get("formerNames") or []:
            if (f.get("to") or "")[:4] >= "2015" and f.get("name"):
                names.append(f["name"])
    except Exception as e:
        print("altdata: submissions failed", tk, str(e)[:120])
    seen, out = set(), []
    for n in names:
        k = norm_name(n)
        if k and k not in seen:
            seen.add(k)
            out.append(n)
    return out


_REGISTRY = {}


def entity_names(sb, tk, max_phrases=8):
    """Names to match federal awards and lobbying clients for one company: SEC legal and former names, plus the
    subsidiaries (Exhibit 21, GLEIF children) that belong to this company alone, from the company registry
    (etl/entities.py). Subsidiaries carry most federal awards (Raytheon Company under RTX, Electric Boat under
    General Dynamics). Capped to `max_phrases` distinct search phrases so the API calls stay bounded. Falls back to
    the legal names when the registry has no record."""
    base = legal_names(sb, tk)
    try:
        from . import entities
        if "m" not in _REGISTRY:
            _REGISTRY["m"], _REGISTRY["reg"] = entities.load_matcher(sb)
        m, reg = _REGISTRY["m"], _REGISTRY["reg"]
        if tk not in (reg.get("companies") or {}):
            return base
        phrases = set(entities.search_phrases(base + m.names_for(tk, reg), max_phrases))
        extra = [n for n in m.names_for(tk, reg) if core_name(n) in phrases]
    except Exception as e:
        print("altdata: registry unavailable", str(e)[:120])
        return base
    seen, out = set(), []
    for n in base + extra:
        k = norm_name(n)
        if k and k not in seen:
            seen.add(k)
            out.append(n)
    return out


# ---------- storage helpers ----------

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


def _universe_symbols():
    out = {}
    for tk, *_ in UNIVERSE:
        for v in symbol_variants(tk):
            out[v] = tk
    return out


# ---------- FINRA short-sale volume ----------

def _trading_days(start, end):
    d = start
    while d <= end:
        if d.weekday() < 5:
            yield d
        d += dt.timedelta(days=1)


def sync_short_volume(sb, deadline=None, today=None):
    """Adds missing FINRA daily files to the cache. Returns days added."""
    today = today or dt.date.today()
    cache = _get_cache(sb, SHORT_CACHE, {"days": {}, "missing": {}})
    syms = _universe_symbols()
    start = today - dt.timedelta(days=SHORT_DAYS)
    added = 0
    for d in sorted(_trading_days(start, today), reverse=True):
        key = d.strftime("%Y%m%d")
        if key in cache["days"] or (cache["missing"].get(key) and d < today - dt.timedelta(days=5)):
            continue
        if deadline and time.monotonic() > deadline:
            break
        try:
            body = request(FINRA_URL.format(d=key), headers=UA, timeout=60, retries=2, raw=True).decode("latin-1")
        except Exception as e:
            if "404" in str(e) or "403" in str(e):
                cache["missing"][key] = True  # market holiday or not yet posted
                continue
            print("finra", key, str(e)[:100])
            continue
        day = {}
        for line in body.splitlines()[1:]:
            p = line.split("|")
            if len(p) < 5 or p[1] not in syms:
                continue
            try:
                day[syms[p[1]]] = [round(float(p[2])), round(float(p[3])), round(float(p[4]))]
            except ValueError:
                continue
        if day:
            cache["days"][key] = day
            added += 1
    keep = (today - dt.timedelta(days=SHORT_DAYS)).strftime("%Y%m%d")
    cache["days"] = {k: v for k, v in cache["days"].items() if k >= keep}
    cache["missing"] = {k: v for k, v in cache["missing"].items() if k >= keep}
    cache["updated_at"] = now_iso()
    if added:
        _put_cache(sb, SHORT_CACHE, cache)
    return added, cache


# ---------- SEC fails-to-deliver ----------

def _ftd_periods(today):
    out = []
    y, m = today.year, today.month
    for _ in range(FTD_MONTHS + 1):
        out += [f"{y}{m:02d}b", f"{y}{m:02d}a"]
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return out


def sync_ftd(sb, deadline=None, today=None):
    today = today or dt.date.today()
    cache = _get_cache(sb, FTD_CACHE, {"files": {}, "missing": {}})
    syms = _universe_symbols()
    added = 0
    periods = _ftd_periods(today)
    for p in periods:
        if p in cache["files"]:
            continue
        if cache["missing"].get(p, "")[:10] == today.isoformat():
            continue  # checked today
        if deadline and time.monotonic() > deadline:
            break
        try:
            sec_throttle()
            body = request(FTD_URL.format(p=p), headers=UA, timeout=120, retries=2, raw=True)
        except Exception as e:
            cache["missing"][p] = now_iso()
            if "404" not in str(e):
                print("ftd", p, str(e)[:100])
            continue
        z = zipfile.ZipFile(io.BytesIO(body))
        text = z.read(z.namelist()[0]).decode("latin-1")
        rows = {}
        for line in text.splitlines()[1:]:
            q = line.split("|")
            if len(q) < 6 or q[2] not in syms:
                continue
            try:
                d = f"{q[0][:4]}-{q[0][4:6]}-{q[0][6:8]}"
                price = float(q[5]) if q[5].strip() not in ("", ".") else None
                rows.setdefault(syms[q[2]], []).append([d, int(q[3]), price, q[1]])
            except ValueError:
                continue
        cache["files"][p] = rows
        cache["missing"].pop(p, None)
        added += 1
    cache["files"] = {k: v for k, v in cache["files"].items() if k in periods}
    cache["updated_at"] = now_iso()
    if added:
        _put_cache(sb, FTD_CACHE, cache)
    return added, cache


# ---------- lobbying ----------

def _lda_get(url, params=None, attempts=8):
    """GET from lda.gov, waiting out its throttle ('Expected available in N seconds') instead of failing."""
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    for attempt in range(attempts):
        try:
            return json.loads(request(url, headers=UA, timeout=60, retries=1))
        except Exception as e:
            msg = str(e)
            if "429" not in msg or attempt == attempts - 1:
                raise
            m = re.search(r"available in (\d+) second", msg)
            time.sleep(min(60, (int(m.group(1)) if m else 5) + 1))


def lobbying(names, years, log=print):
    """Quarterly federal lobbying spend for clients whose normalized name equals one of names."""
    want = {norm_name(n) for n in names}
    phrases = list(dict.fromkeys(core_name(n) for n in names if core_name(n)))
    filings = {}
    for phrase in phrases:
        for year in years:
            url, params = LDA_URL, {"client_name": phrase, "filing_year": year, "page_size": 25}
            while url:
                d = _lda_get(url, params)
                params = None
                for f in d.get("results", []):
                    if norm_name((f.get("client") or {}).get("name")) in want:
                        filings[f["filing_uuid"]] = f
                url = d.get("next")
    latest = {}
    for f in filings.values():
        if f.get("filing_period") not in ("first_quarter", "second_quarter", "third_quarter", "fourth_quarter"):
            continue
        k = ((f.get("registrant") or {}).get("id"), f["filing_year"], f["filing_period"])
        if k not in latest or (f.get("dt_posted") or "") > (latest[k].get("dt_posted") or ""):
            latest[k] = f
    per = {}
    for (_, year, period), f in latest.items():
        q = {"first_quarter": 1, "second_quarter": 2, "third_quarter": 3, "fourth_quarter": 4}[period]
        r = per.setdefault((year, q), {"year": year, "quarter": q, "in_house": 0.0, "outside": 0.0, "reports": [], "firms": set()})
        reg = (f.get("registrant") or {}).get("name") or ""
        if f.get("expenses") is not None:
            r["in_house"] += float(f["expenses"])
        if f.get("income") is not None:
            r["outside"] += float(f["income"])
            r["firms"].add(reg)
        r["reports"].append({"registrant": reg, "type": f.get("filing_type"), "expenses": f.get("expenses"), "income": f.get("income"),
                             "url": f.get("filing_document_url"), "posted": (f.get("dt_posted") or "")[:10]})
    rows = []
    for (year, q), r in sorted(per.items()):
        amount = r["in_house"] if r["in_house"] > 0 else r["outside"]
        rows.append({"year": year, "quarter": q, "amount": round(amount, 2), "basis": "in-house report" if r["in_house"] > 0 else "outside firms",
                     "in_house": round(r["in_house"], 2), "outside": round(r["outside"], 2), "firms": sorted(x for x in r["firms"] if x),
                     "reports": sorted(r["reports"], key=lambda x: (x["registrant"], x["posted"]))})
    matched = sorted({(f.get("client") or {}).get("name") for f in filings.values() if (f.get("client") or {}).get("name")})
    return {"rows": rows, "client_names": matched, "search": phrases}


# ---------- federal contracts ----------

def _usa_post(path, body):
    return json.loads(request(USA_URL + path, method="POST", data=body, headers=UA, timeout=90, retries=3))


def _fy_bounds(fy):
    return {"start_date": f"{fy - 1}-10-01", "end_date": f"{fy}-09-30"}


def _usa_get(path):
    return json.loads(request(USA_URL + path, headers=UA, timeout=90, retries=3))


def contracts(names, fys, today=None, log=print):
    """Federal award obligations by federal fiscal year to the company's registered entities.

    Entities are USAspending parent records (SAM.gov parent linkage) and stand-alone records whose normalized name
    equals one of names. A parent's total covers its subsidiaries (e.g. CACI INTERNATIONAL INC covers
    'CACI, INC. - FEDERAL'); children are de-duplicated across parents so a subsidiary listed under two parents
    counts once. When the children list is capped (100 rows) the parent total is used instead."""
    today = today or dt.date.today()
    want = {norm_name(n) for n in names}
    phrases = list(dict.fromkeys(core_name(n) for n in names if core_name(n)))
    parents, standalone = {}, {}
    for phrase in phrases:
        for page in range(1, 6):
            d = _usa_post("recipient/", {"keyword": phrase, "award_type": "all", "limit": 100, "page": page, "sort": "amount", "order": "desc"})
            for r in d.get("results", []):
                if norm_name(r.get("name")) not in want:
                    continue
                if r.get("recipient_level") == "P":
                    parents[r["id"]] = r
                elif r.get("recipient_level") == "R":
                    standalone[r["id"]] = r
            if not (d.get("page_metadata") or {}).get("hasNext"):
                break
    rows, entity_names = [], set()
    for fy in fys:
        children, capped_total = {}, 0.0
        for pid, p in parents.items():
            uei = p.get("uei")
            kids = _usa_get(f"recipient/children/{uei}/?year={fy}") if uei else []
            if len(kids) >= 100 or not uei:
                capped_total += float(_usa_get(f"recipient/{pid}/?year={fy}").get("total_transaction_amount") or 0)
                continue
            for k in kids:
                children[k.get("recipient_id") or k.get("uei") or k.get("name")] = float(k.get("amount") or 0)
                if k.get("amount"):
                    entity_names.add(k.get("name"))
        alone = 0.0
        for rid in standalone:
            alone += float(_usa_get(f"recipient/{rid}/?year={fy}").get("total_transaction_amount") or 0)
        total = sum(children.values()) + capped_total + alone
        rows.append({"fy": fy, "amount": round(total, 2), "partial": dt.date(fy, 9, 30) > today})
    entity_names |= {p["name"] for p in parents.values()} | {r["name"] for r in standalone.values()}
    entity_keys = {norm_name(n) for n in entity_names}
    # largest contract awards with activity in the last 12 months, for those entities
    awards = []
    if parents or standalone:
        start = (today - dt.timedelta(days=365)).isoformat()
        for phrase in phrases:
            d = _usa_post("search/spending_by_award/", {
                "filters": {"recipient_search_text": [phrase], "time_period": [{"start_date": start, "end_date": today.isoformat()}], "award_type_codes": CONTRACT_TYPES},
                "fields": ["Award ID", "Recipient Name", "Award Amount", "Awarding Agency", "Awarding Sub Agency", "Start Date", "End Date", "Description", "generated_internal_id"],
                "sort": "Award Amount", "order": "desc", "limit": 100, "page": 1})
            for a in d.get("results", []):
                if norm_name(a.get("Recipient Name")) in entity_keys:
                    awards.append({"id": a.get("Award ID"), "recipient": a.get("Recipient Name"), "amount": a.get("Award Amount"),
                                   "agency": a.get("Awarding Agency"), "sub_agency": a.get("Awarding Sub Agency"), "start": a.get("Start Date"),
                                   "end": a.get("End Date"), "description": (a.get("Description") or "")[:200],
                                   "url": f"https://www.usaspending.gov/award/{a['generated_internal_id']}" if a.get("generated_internal_id") else None})
        seen, uniq = set(), []
        for a in sorted(awards, key=lambda a: -(a["amount"] or 0)):
            if a["id"] not in seen:
                seen.add(a["id"]); uniq.append(a)
        awards = uniq[:10]
    return {"rows": rows, "entities": sorted(x for x in entity_names if x)[:60], "parents": len(parents), "standalone": len(standalone),
            "search": phrases, "awards": awards}


# ---------- publish ----------

def _stale(block, today):
    try:
        return (today - dt.date.fromisoformat(block["fetched_at"][:10])).days >= REFRESH_DAYS
    except (KeyError, TypeError, ValueError):
        return True


def build_company(sb, tk, short_cache, ftd_cache, refresh=True, today=None, log=print):
    today = today or dt.date.today()
    prev = sb.get_json(f"altdata/{tk}.json") or {}
    out = {"ticker": tk, "updated_at": now_iso(), "version": 1}
    days = sorted(short_cache.get("days", {}))
    rows = [[f"{k[:4]}-{k[4:6]}-{k[6:]}", *short_cache["days"][k][tk]] for k in days if tk in short_cache["days"][k]]
    out["short_volume"] = {
        "source": "FINRA Reg SHO daily short sale volume, consolidated NMS file (off-exchange trades reported to FINRA facilities)",
        "url": "https://www.finra.org/finra-data/browse-catalog/short-sale-volume-data/daily-short-sale-volume-files",
        "fields": ["date", "short_volume", "short_exempt_volume", "total_volume"], "rows": rows}
    ftd = []
    for p, by in ftd_cache.get("files", {}).items():
        ftd += by.get(tk, [])
    ftd.sort(key=lambda r: r[0])
    out["ftd"] = {"source": "SEC fails-to-deliver data (aggregate net balance of fails per settlement date)",
                  "url": "https://www.sec.gov/data-research/sec-markets-data/fails-deliver-data",
                  "fields": ["settlement_date", "quantity", "price", "cusip"], "rows": ftd,
                  "through": max(ftd_cache.get("files", {}) or [""])}
    names = None
    cur_fy = today.year + (1 if today.month >= 10 else 0)
    for key, fn, first, rkey in (("lobbying", lambda n, start: lobbying(n, range(start, today.year + 1)), LOBBY_FROM, ("year", "quarter")),
                                 ("contracts", lambda n, start: contracts(n, range(start, cur_fy + 1), today), CONTRACT_FY_FROM, ("fy",))):
        block = prev.get(key)
        if refresh and (not block or _stale(block, today)):
            try:
                names = names or entity_names(sb, tk)
                # a complete earlier history only needs its latest two years re-fetched
                full = not block or block.get("first") != first or sorted(map(norm_name, block.get("legal_names") or [])) != sorted(map(norm_name, names))
                latest = today.year if key == "lobbying" else cur_fy
                start = first if full else latest - 1
                fresh = fn(names, start)
                if not full:
                    keep = [r for r in block.get("rows", []) if r[rkey[0]] < start]
                    fresh["rows"] = sorted(keep + fresh["rows"], key=lambda r: tuple(r[k] for k in rkey))
                    if key == "lobbying":
                        fresh["client_names"] = sorted(set(fresh["client_names"]) | set(block.get("client_names") or []))
                block = {**fresh, "legal_names": names, "first": first, "fetched_at": now_iso()}
            except Exception as e:
                log(f"altdata {tk} {key} failed: {str(e)[:160]}")
                block = prev.get(key)
        if block:
            out[key] = block
    out["lobbying_source"] = {"name": "Lobbying Disclosure Act reports (LD-2), lda.gov", "url": "https://lda.gov/filings/public/filing/search/"}
    out["contracts_source"] = {"name": "USAspending.gov, prime contract obligations (award types A–D)", "url": "https://www.usaspending.gov/search"}
    sb.put_json(f"altdata/{tk}.json", out, cache="max-age=3600")
    return {"short_days": len(rows), "ftd_rows": len(ftd), "lobbying_q": len((out.get("lobbying") or {}).get("rows", [])),
            "contract_fy": sum(1 for r in (out.get("contracts") or {}).get("rows", []) if r["amount"])}


def altdata_job(sb, tickers=None, workers=4, time_budget=None, refresh=True, sync=True):
    started = time.monotonic()
    deadline = started + time_budget if time_budget else None
    with Run(sb, "altdata") as run:
        if sync:
            s_added, short_cache = sync_short_volume(sb, deadline=deadline - 120 if deadline else None)
            f_added, ftd_cache = sync_ftd(sb, deadline=deadline - 100 if deadline else None)
        else:
            s_added, short_cache = 0, _get_cache(sb, SHORT_CACHE, {"days": {}})
            f_added, ftd_cache = 0, _get_cache(sb, FTD_CACHE, {"files": {}})
        run.stats.update({"finra_days_added": s_added, "ftd_files_added": f_added})
        tickers = tickers or [t for t, *_ in UNIVERSE]
        done, failed = {}, {}

        def one(tk):
            if deadline and time.monotonic() > deadline - 10:
                return tk, None, "skipped: time budget"
            try:
                return tk, build_company(sb, tk, short_cache, ftd_cache, refresh=refresh), None
            except Exception as e:
                return tk, None, str(e)[:200]
        with ThreadPoolExecutor(max(1, workers)) as ex:
            for tk, res, err in ex.map(one, tickers):
                if res is not None:
                    done[tk] = res
                else:
                    failed[tk] = err
        run.stats.update({"done": len(done), "failed": failed})
        return done, failed


def rotation_job(sb, step="refresh", per_run=12, time_budget=240):
    """Cron entry.
    step=market: sync FINRA/SEC files; when a new file arrived, republish every company's market series
    (lobbying/contracts are carried from the previous file).
    step=refresh: refresh lobbying and contracts for the next per_run companies in rotation."""
    if step == "market":
        started = time.monotonic()
        s_added, short_cache = sync_short_volume(sb, deadline=started + time_budget * 0.3)
        f_added, ftd_cache = sync_ftd(sb, deadline=started + time_budget * 0.4)
        if not (s_added or f_added):
            return {"finra_days_added": 0, "ftd_files_added": 0}
        remaining = max(30, time_budget - (time.monotonic() - started))
        done, failed = altdata_job(sb, None, workers=8, time_budget=remaining, refresh=False, sync=False)
        return {"finra_days_added": s_added, "ftd_files_added": f_added, "published": len(done), "failed": len(failed)}
    state = sb.get_json("meta/altdata_rotation.json") or {"cursor": 0}
    tickers = [t for t, *_ in UNIVERSE]
    start = state.get("cursor", 0) % len(tickers)
    pick = (tickers[start:] + tickers[:start])[:per_run]
    done, failed = altdata_job(sb, pick, workers=4, time_budget=time_budget, sync=False)
    state["cursor"] = (start + len(done)) % len(tickers)
    state["updated_at"] = now_iso()
    sb.put_json("meta/altdata_rotation.json", state, cache="no-store")
    return {"done": len(done), "failed": failed, "cursor": state["cursor"]}
