"""Large holders: Schedule 13D/13G beneficial-ownership reports and Form 13F institutional holdings (SEC).

13D/13G: the filings table already lists every Schedule 13D/13G filed about a covered company (EDGAR submissions).
Since December 2024 these are structured XML (SCHEDULE 13D / SCHEDULE 13G, primary_doc.xml). Each is parsed for
the reporting persons, aggregate shares, percent of class, person type, the event date and (13D) the Item 4 purpose
text. Filings where the company is the filer about another issuer are skipped (issuer CIK must equal the company's).

13F: the SEC Form 13F data sets (www.sec.gov/data-research/sec-markets-data/form-13f-data-sets), quarterly ZIPs of
every 13F-HR filed in a three-month window. INFOTABLE rows are matched to companies by CUSIP. CUSIPs come from the
company's own 13D/13G cover pages (issuerCUSIP, authoritative) and otherwise from the SEC fails-to-deliver files
(CUSIP + symbol), and every matched row must also carry an issuer name that contains the company's first name word.
Options (PUTCALL set) and principal-amount rows are excluded. Per manager the latest original or restated report for a
period is used; NEW HOLDINGS amendments add to it. Values are as filed (dollars since 2023 per the SEC readme).
"""
import collections
import csv
import datetime as dt
import io
import re
import xml.etree.ElementTree as ET
import zipfile
from concurrent.futures import ThreadPoolExecutor

from .common import now_iso, sec_get, sec_get_raw
from .gov_common import get_raw_json, put_raw_json, src, symbol_map
from .universe import BY_TICKER

FORMS_13DG = ("SC 13D", "SC 13D/A", "SC 13G", "SC 13G/A", "SCHEDULE 13D", "SCHEDULE 13D/A", "SCHEDULE 13G", "SCHEDULE 13G/A")
F13_PAGE = "https://www.sec.gov/data-research/sec-markets-data/form-13f-data-sets"
FTD_URL = "https://www.sec.gov/files/data/fails-deliver-data/cnsfails{p}.zip"
PERSON_TYPES = {"IA": "Investment adviser", "BD": "Broker-dealer", "BK": "Bank", "IC": "Insurance company", "IV": "Investment company",
                "EP": "Employee benefit plan", "HC": "Parent holding company", "CO": "Corporation", "PN": "Partnership", "IN": "Individual",
                "OO": "Other", "FI": "Non-US institution", "SA": "Savings association", "CP": "Church plan", "TR": "Trust"}


def _local(tag):
    return tag.split("}", 1)[-1]


def _find_all(root, name):
    return [e for e in root.iter() if _local(e.tag) == name]


def _text(e):
    return re.sub(r"\s+", " ", "".join(e.itertext())).strip() if e is not None else None


def parse_13dg_xml(xml_bytes):
    root = ET.fromstring(xml_bytes)
    first = lambda name: next(iter(_find_all(root, name)), None)
    issuer_cik = _text(first("issuerCik")) or _text(first("issuerCIK"))
    cusips = [_text(e) for e in _find_all(root, "issuerCusipNumber") + _find_all(root, "issuerCUSIP") if _text(e)]
    persons = []
    blocks = _find_all(root, "reportingPersonInfo") or _find_all(root, "coverPageHeaderReportingPersonDetails")
    for b in blocks:
        g = lambda name: next((_text(e) for e in b.iter() if _local(e.tag) == name), None)
        shares = g("aggregateAmountOwned") or g("reportingPersonBeneficiallyOwnedAggregateNumberOfShares")
        pct = g("percentOfClass") or g("classPercent")
        try:
            shares = float(shares) if shares not in (None, "") else None
        except ValueError:
            shares = None
        try:
            pct = float(pct) if pct not in (None, "") else None
        except ValueError:
            pct = None
        persons.append({"name": g("reportingPersonName"), "cik": g("reportingPersonCIK"), "shares": shares, "percent": pct,
                        "type": g("typeOfReportingPerson"), "sole_voting": g("soleVotingPower")})
    purpose = None
    for e in _find_all(root, "item4"):
        t = _text(e)
        if t and len(t) > 20 and "amountBeneficiallyOwned" not in [_local(c.tag) for c in e]:
            purpose = t[:2000]
            break
    event = _text(first("dateOfEvent")) or _text(first("eventDateRequiresFilingThisStatement"))
    return {"issuer_cik": int(issuer_cik) if issuer_cik and issuer_cik.isdigit() else None, "cusips": cusips, "persons": persons,
            "purpose": purpose, "event_date": event, "class_title": _text(first("securitiesClassTitle")),
            "rule": _text(first("designateRulePursuantThisScheduleFiled"))}


def fetch_13dg(ticker, f):
    cik = BY_TICKER[ticker]["cik"]
    acc = f["accession"]
    base = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc.replace('-', '')}/"
    doc = (f.get("primary_doc") or "").split("/")[-1]
    rec = {"id": f"13dg:{acc}", "accession": acc, "form": f["form"], "filed": f["filed"], "index_url": base + f"{acc}-index.htm"}
    if doc.endswith(".xml"):
        url = base + doc
        p = parse_13dg_xml(sec_get_raw(url, timeout=60))
        rec.update(p, url=url, display_url=base + (f.get("primary_doc") or doc), status="ok")
        if p["issuer_cik"] and p["issuer_cik"] != int(cik):
            rec["status"] = "other_issuer"
    else:
        rec.update(url=base + doc, status="legacy", persons=[], note="Pre-2025 text/HTML schedule; listed without parsed amounts.")
    rec["retrieved_at"] = now_iso()
    return rec


def collect_13dg(sb, cache=None, since="2025-01-01", per_company=24, workers=4, deadline=None, log=print):
    cache = cache if cache is not None else (get_raw_json(sb, "govraw/flows/13dg.json", {"filings": {}}) or {"filings": {}})
    q = "select=ticker,form,filed,accession,primary_doc&form=in.(" + ",".join(f'"{x}"' for x in FORMS_13DG) + f")&filed=gte.{since}&order=filed.desc"
    rows = sb.select("filings", q.replace(" ", "%20"))
    per = collections.Counter()
    todo = []
    for r in rows:
        if r["ticker"] not in BY_TICKER:
            continue
        per[r["ticker"]] += 1
        if per[r["ticker"]] > per_company or r["accession"] in cache["filings"]:
            continue
        todo.append(r)
    log(f"13D/G: {len(rows)} filings listed, {len(todo)} to fetch")

    def one(r):
        try:
            return r, fetch_13dg(r["ticker"], r), None
        except Exception as e:
            return r, None, str(e)[:160]
    done = 0
    with ThreadPoolExecutor(workers) as ex:
        for r, rec, err in ex.map(one, todo):
            if rec:
                rec["ticker"] = r["ticker"]
                cache["filings"][r["accession"]] = rec
                done += 1
            if done and done % 500 == 0:
                put_raw_json(sb, "govraw/flows/13dg.json", cache)
                log(f"  13dg {done}")
            if deadline and deadline.passed(30):
                break
    cache["updated_at"] = now_iso()
    put_raw_json(sb, "govraw/flows/13dg.json", cache)
    return cache


def holders_for(cache, tk):
    """Latest report per reporting person for one company, newest first."""
    rows = [r for r in cache["filings"].values() if r.get("ticker") == tk and r.get("status") in ("ok", "legacy")]
    rows.sort(key=lambda r: (r["filed"], r["accession"]), reverse=True)
    latest, history = {}, []
    for r in rows:
        persons = r.get("persons") or []
        lead = persons[0] if persons else {}
        key = (lead.get("cik") or lead.get("name") or r["accession"]).upper() if lead else r["accession"]
        item = {"id": r["id"], "accession": r["accession"], "form": r["form"], "filed": r["filed"], "event_date": r.get("event_date"),
                "filer": lead.get("name"), "filer_type": PERSON_TYPES.get(lead.get("type"), lead.get("type")), "shares": lead.get("shares"),
                "percent": lead.get("percent"), "persons": persons[:6], "activist": r["form"].startswith(("SC 13D", "SCHEDULE 13D")),
                "purpose": r.get("purpose"), "url": r.get("display_url") or r.get("url"), "index_url": r.get("index_url"),
                "status": r.get("status"), "note": r.get("note"),
                "src": src(r.get("url"), "EDGAR Schedule 13D/13G primary document" + (" (structured XML)" if r.get("status") == "ok" else ""), r.get("retrieved_at"))}
        history.append(item)
        if key not in latest:
            latest[key] = item
    cur = sorted(latest.values(), key=lambda x: -(x.get("percent") or 0))
    return {"current": cur[:30], "history": history[:60]}


def cusips_from_13dg(cache):
    out = {}
    for r in sorted(cache["filings"].values(), key=lambda r: r.get("filed") or ""):
        if r.get("status") == "ok" and r.get("cusips"):
            for c in r["cusips"]:
                c = re.sub(r"[^0-9A-Z]", "", c.upper())
                if len(c) == 9:
                    out.setdefault(r["ticker"], collections.Counter())[c] += 1
    return {tk: [c for c, _ in v.most_common(3)] for tk, v in out.items()}


def cusips_from_ftd(periods, log=print):
    """Symbol -> CUSIP from SEC fails-to-deliver files (each line: date|CUSIP|SYMBOL|QTY|DESCRIPTION|PRICE)."""
    syms = symbol_map()
    seen = {}
    for p in periods:
        try:
            body = sec_get_raw(FTD_URL.format(p=p), timeout=120)
        except Exception as e:
            log("ftd", p, str(e)[:80])
            continue
        z = zipfile.ZipFile(io.BytesIO(body))
        for line in z.read(z.namelist()[0]).decode("latin-1").splitlines()[1:]:
            q = line.split("|")
            if len(q) >= 5 and q[2] in syms and len(q[1]) == 9:
                seen.setdefault(syms[q[2]], {})[q[1]] = (q[0], q[4])
    return {tk: [c for c, _ in sorted(v.items(), key=lambda kv: kv[1][0], reverse=True)] for tk, v in seen.items()}, \
           {tk: {c: d[1] for c, d in v.items()} for tk, v in seen.items()}


# ---------------------------------------------------------------- 13F

def list_13f_sets():
    page = sec_get(F13_PAGE, timeout=60)
    links = re.findall(r'href="(/files/[^"]*form-13f-data-sets/[^"]*form13f\.zip)"', page)
    return ["https://www.sec.gov" + l for l in dict.fromkeys(links)]


def _d(s):
    try:
        return dt.datetime.strptime(s, "%d-%b-%Y").date().isoformat()
    except (TypeError, ValueError):
        return None


def read_13f_zip(data, cusip_to_tk, periods):
    """Returns {period: {cik: {"filing": meta, "rows": {tk: [shares, value]}}}} for the given report periods."""
    z = zipfile.ZipFile(io.BytesIO(data) if isinstance(data, (bytes, bytearray)) else data)
    sub = {}
    for r in csv.DictReader(io.TextIOWrapper(z.open("SUBMISSION.tsv"), "utf-8", errors="replace"), delimiter="\t", quoting=csv.QUOTE_NONE):
        per = _d(r["PERIODOFREPORT"])
        if per in periods and r["SUBMISSIONTYPE"] in ("13F-HR", "13F-HR/A"):
            sub[r["ACCESSION_NUMBER"]] = {"accession": r["ACCESSION_NUMBER"], "cik": r["CIK"].lstrip("0"), "period": per,
                                          "filed": _d(r["FILING_DATE"]), "type": r["SUBMISSIONTYPE"]}
    for r in csv.DictReader(io.TextIOWrapper(z.open("COVERPAGE.tsv"), "utf-8", errors="replace"), delimiter="\t", quoting=csv.QUOTE_NONE):
        s = sub.get(r["ACCESSION_NUMBER"])
        if s:
            s.update(manager=r["FILINGMANAGER_NAME"], amendment=r["AMENDMENTTYPE"] or None, report_type=r["REPORTTYPE"])
    rows = collections.defaultdict(lambda: collections.defaultdict(lambda: [0.0, 0.0]))
    names = collections.defaultdict(collections.Counter)
    for r in csv.DictReader(io.TextIOWrapper(z.open("INFOTABLE.tsv"), "utf-8", errors="replace"), delimiter="\t", quoting=csv.QUOTE_NONE):
        acc = r["ACCESSION_NUMBER"]
        if acc not in sub:
            continue
        tk = cusip_to_tk.get((r["CUSIP"] or "").upper())
        if not tk or r["PUTCALL"] or r["SSHPRNAMTTYPE"] != "SH":
            continue
        try:
            sh, val = float(r["SSHPRNAMT"] or 0), float(r["VALUE"] or 0)
        except ValueError:
            continue
        cell = rows[acc][tk]
        cell[0] += sh
        cell[1] += val
        names[tk][r["NAMEOFISSUER"]] += 1
    return sub, rows, names


def choose_reports(subs):
    """Per (manager CIK, period): the latest original or restatement plus NEW HOLDINGS amendments filed after it."""
    by = collections.defaultdict(list)
    for s in subs.values():
        by[(s["cik"], s["period"])].append(s)
    out = {}
    for k, lst in by.items():
        lst.sort(key=lambda s: (s["filed"] or "", s["accession"]))
        base = [s for s in lst if (s.get("amendment") or "").upper() != "NEW HOLDINGS" and (s.get("report_type") or "").upper() != "13F NOTICE"]
        if not base:
            continue
        b = base[-1]
        adds = [s for s in lst if (s.get("amendment") or "").upper() == "NEW HOLDINGS" and (s["filed"] or "") >= (b["filed"] or "")]
        out[k] = [b] + adds
    return out


def name_matches(tk, issuer_names):
    from .altdata import core_name
    words = core_name(BY_TICKER[tk]["name"]).split()
    if not words:
        return False
    w = re.sub(r"[^A-Z0-9]", "", words[0])
    return any(w and w in re.sub(r"[^A-Z0-9]", "", n.upper()) for n in issuer_names)


def build_13f(zips, cusips, periods, log=print):
    """zips: list of local paths/bytes of data sets; periods: [prior, latest] report dates. Returns per-ticker holders."""
    cusip_to_tk = {}
    for tk, cs in cusips.items():
        for c in cs:
            cusip_to_tk.setdefault(c.upper(), tk)
    subs, rows, names = {}, {}, collections.defaultdict(collections.Counter)
    for zp in zips:
        s, r, n = read_13f_zip(zp, cusip_to_tk, set(periods))
        subs.update(s)
        rows.update(r)
        for tk, c in n.items():
            names[tk].update(c)
        log(f"13F set read: {len(s)} filings in periods")
    chosen = choose_reports(subs)
    rejected = sorted(tk for tk in names if not name_matches(tk, names[tk]))
    hold = collections.defaultdict(lambda: collections.defaultdict(dict))  # tk -> cik -> period -> [sh, val, accs]
    managers = {}
    for (cik, per), reps in chosen.items():
        for s in reps:
            managers[cik] = s.get("manager") or managers.get(cik)
            for tk, (sh, val) in (rows.get(s["accession"]) or {}).items():
                if tk in rejected:
                    continue
                cell = hold[tk][cik].setdefault(per, [0.0, 0.0, []])
                cell[0] += sh
                cell[1] += val
                cell[2].append(s["accession"])
    prior, latest = periods
    out = {}
    for tk, by in hold.items():
        lst = []
        tot = {latest: [0.0, 0.0, 0], prior: [0.0, 0.0, 0]}
        for cik, pp in by.items():
            a, b = pp.get(latest), pp.get(prior)
            for per, c in ((latest, a), (prior, b)):
                if c:
                    tot[per][0] += c[0]
                    tot[per][1] += c[1]
                    tot[per][2] += 1
            sh, psh = (a or [0])[0], (b or [0])[0]
            lst.append({"cik": cik, "manager": managers.get(cik), "shares": sh, "value": (a or [0, 0])[1], "prev_shares": psh,
                        "change": sh - psh, "pct_change": ((sh - psh) / psh) if psh else None, "new": bool(a and not b), "exited": bool(b and not a),
                        "accessions": (a or [None, None, []])[2][:3], "prev_accessions": (b or [None, None, []])[2][:3]})
        lst.sort(key=lambda x: -x["shares"])
        buyers = sorted([x for x in lst if x["change"] > 0], key=lambda x: -x["change"])[:15]
        sellers = sorted([x for x in lst if x["change"] < 0], key=lambda x: x["change"])[:15]
        out[tk] = {"period": latest, "prior_period": prior, "holders": lst[:40], "buyers": buyers, "sellers": sellers,
                   "totals": {"managers": tot[latest][2], "shares": tot[latest][0], "value": tot[latest][1],
                              "prior_managers": tot[prior][2], "prior_shares": tot[prior][0], "prior_value": tot[prior][1],
                              "new_positions": sum(1 for x in lst if x["new"]), "exits": sum(1 for x in lst if x["exited"])},
                   "cusips": cusips.get(tk), "issuer_names": [n for n, _ in names[tk].most_common(3)]}
    return out, rejected
