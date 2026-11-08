"""Regulatory and clinical data for covered companies (no keys, public APIs).

Sources
  ClinicalTrials.gov API v2   trials where the company (or a registry-listed subsidiary) is the lead sponsor
  openFDA drugsfda            approved drug applications and their submissions (approvals by date)
  openFDA enforcement         drug and device recalls (Class I is the most serious)
  openFDA device 510(k)       device clearances

Names come from the company registry (etl/entities.py via altdata.entity_names): legal and former names plus
subsidiaries. A record is kept only when its sponsor or firm name matches one of those names token for token after
abbreviation expansion ('PHARMS' -> 'PHARMACEUTICALS'), so a keyword hit on a similar name is dropped.

Outputs (public-data)
  regulatory/{T}.json         trials (summary, upcoming readouts, recent status changes), approvals, recalls, clearances
  regulatory/catalysts.json   upcoming primary completions across companies (next 12 months), phase 3 first
State (raw): govraw/regulatory/state.json (rotation cursor)."""
import datetime as dt
import json
import os
import re
import unicodedata
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

from .altdata import core_name, norm_name
from .common import now_iso, request
from .gov_common import get_raw_json, put_raw_json

CT = "https://clinicaltrials.gov/api/v2/studies"
FDA = "https://api.fda.gov"
UA = {"User-Agent": "FiledUnder research (regulatory)", "Accept": "application/json"}
STATE = "govraw/regulatory/state.json"
CT_FIELDS = ",".join(["NCTId", "BriefTitle", "Acronym", "OverallStatus", "WhyStopped", "Phase", "StartDate", "PrimaryCompletionDate",
                      "CompletionDate", "Condition", "InterventionName", "EnrollmentCount", "LastUpdatePostDate", "LeadSponsorName",
                      "StudyType", "ResultsFirstPostDate", "StatusVerifiedDate"])
ACTIVE = {"RECRUITING", "ACTIVE_NOT_RECRUITING", "NOT_YET_RECRUITING", "ENROLLING_BY_INVITATION"}
STOPPED = {"TERMINATED", "WITHDRAWN", "SUSPENDED"}
ABBREV = {"PHARMS": "PHARMACEUTICALS", "PHARMA": "PHARMACEUTICALS", "PHARM": "PHARMACEUTICAL", "LABS": "LABORATORIES", "LAB": "LABORATORIES",
          "MFG": "MANUFACTURING", "INTL": "INTERNATIONAL", "CORP": "CORPORATION", "CO": "COMPANY", "THERAP": "THERAPEUTICS",
          "THERAPS": "THERAPEUTICS", "BIOSCI": "BIOSCIENCES", "TECH": "TECHNOLOGIES", "HLTHCARE": "HEALTHCARE", "MEDCL": "MEDICAL"}
FILLER = {"INC", "CORPORATION", "COMPANY", "LLC", "LTD", "LIMITED", "PLC", "AG", "SA", "NV", "LP", "THE", "AND", "OF", "GROUP", "HOLDINGS",
          "HOLDING", "US", "USA", "AMERICA", "NORTH", "DIV", "DIVISION", "SUB", "A"}


def tokens(name):
    name = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode()   # 'LABORATÓRIOS' -> 'LABORATORIOS'
    name = re.sub(r"\s*/\s*[A-Za-z]{2,3}\s*/?\s*$", "", name or "")   # SEC state tags: 'INC / MA', 'CORP /DE/'
    t = [ABBREV.get(w, w) for w in norm_name(name).replace("/", " ").split()]
    return [w for w in t if w not in FILLER and len(w) > 1]


def name_ok(candidate, phrases):
    """True when every distinctive token of one company phrase appears in the candidate (and the phrase has at least
    one token of 4+ letters, so 'AMG' alone never matches)."""
    ct = set(tokens(candidate))
    for p in phrases:
        pt = tokens(p)
        if pt and any(len(w) >= 4 for w in pt) and set(pt) <= ct:
            return True
    return False


def _get(url, retries=3):
    for i in range(retries):
        try:
            return json.loads(request(url, headers=UA, timeout=60, retries=2))
        except Exception as e:
            msg = str(e)
            if "404" in msg or "NOT_FOUND" in msg:
                return {}
            if i == retries - 1:
                raise
            time.sleep(2 * (i + 1))
    return {}


def _date(s):
    if not s:
        return None
    s = str(s)
    if re.fullmatch(r"\d{8}", s):
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    if re.fullmatch(r"\d{4}-\d{2}", s):
        return s + "-01"
    return s[:10]


# ---------------------------------------------------------------- ClinicalTrials.gov

def query_phrases(phrases, n=4):
    """Search phrases without legal tails or filler ('ELI LILLY & Co' -> 'ELI LILLY'), unique, in order."""
    out = []
    for p in phrases:
        q = " ".join(tokens(p))
        if q and q not in out and any(len(w) >= 4 for w in q.split()):
            out.append(q)
    return out[:n]


def trials(phrases, since_year=2015, max_pages=6):
    out = {}
    for p in query_phrases(phrases):
        token = None
        for _ in range(max_pages):
            q = {"query.lead": p, "pageSize": 200, "fields": CT_FIELDS, "format": "json"}
            if token:
                q["pageToken"] = token
            d = _get(CT + "?" + urllib.parse.urlencode(q))
            for s in d.get("studies") or []:
                ps = s.get("protocolSection") or {}
                idm, st = ps.get("identificationModule") or {}, ps.get("statusModule") or {}
                sponsor = ((ps.get("sponsorCollaboratorsModule") or {}).get("leadSponsor") or {}).get("name") or ""
                if not name_ok(sponsor, phrases):
                    continue
                start = _date((st.get("startDateStruct") or {}).get("date"))
                if start and start[:4] < str(since_year) and st.get("overallStatus") not in ACTIVE:
                    continue
                des = ps.get("designModule") or {}
                out[idm.get("nctId")] = {
                    "nct": idm.get("nctId"), "title": idm.get("briefTitle"), "acronym": idm.get("acronym"),
                    "status": st.get("overallStatus"), "why_stopped": st.get("whyStopped"),
                    "phase": "/".join(x.replace("PHASE", "P") for x in des.get("phases") or []) or None,
                    "type": des.get("studyType"), "enrollment": (des.get("enrollmentInfo") or {}).get("count"),
                    "start": start, "primary_completion": _date((st.get("primaryCompletionDateStruct") or {}).get("date")),
                    "primary_completion_type": (st.get("primaryCompletionDateStruct") or {}).get("type"),
                    "completion": _date((st.get("completionDateStruct") or {}).get("date")),
                    "updated": _date((st.get("lastUpdatePostDateStruct") or {}).get("date")),
                    "results_posted": _date((st.get("resultsFirstPostDateStruct") or {}).get("date")),
                    "conditions": (ps.get("conditionsModule") or {}).get("conditions", [])[:5],
                    "interventions": [i.get("name") for i in (ps.get("armsInterventionsModule") or {}).get("interventions", [])[:6]],
                    "sponsor": sponsor}
            token = d.get("nextPageToken")
            if not token:
                break
    return list(out.values())


def phase_rank(p):
    return {"P3": 3, "P2/P3": 2.5, "P2": 2, "P1/P2": 1.5, "P1": 1, "P4": 4}.get(p or "", 0)


def summarize_trials(rows, today):
    act = [r for r in rows if r["status"] in ACTIVE]
    soon = (today + dt.timedelta(days=365)).isoformat()
    recent = (today - dt.timedelta(days=180)).isoformat()
    upcoming = sorted([r for r in act if r.get("primary_completion") and today.isoformat() <= r["primary_completion"] <= soon],
                      key=lambda r: (r["primary_completion"], -phase_rank(r.get("phase"))))
    changes = sorted([r for r in rows if r.get("updated") and r["updated"] >= recent and (r["status"] in STOPPED or r["status"] == "COMPLETED")],
                     key=lambda r: r["updated"], reverse=True)
    by_phase = {}
    for r in act:
        by_phase[r.get("phase") or "N/A"] = by_phase.get(r.get("phase") or "N/A", 0) + 1
    return {"n": len(rows), "active": len(act), "by_phase_active": by_phase,
            "late_stage_active": sum(1 for r in act if r.get("phase") in ("P3", "P2/P3")),
            "upcoming": upcoming[:40], "recent_changes": changes[:30],
            "stopped_2y": sum(1 for r in rows if r["status"] in STOPPED and (r.get("updated") or "") >= (today - dt.timedelta(days=730)).isoformat())}


# ---------------------------------------------------------------- openFDA

def _fda_search(path, field, word, extra="", limit=100, pages=3, sort=None):
    out = []
    for i in range(pages):
        q = f"{FDA}/{path}.json?search={field}:{urllib.parse.quote(word)}*{extra}&limit={limit}&skip={i * limit}"
        if os.environ.get("OPENFDA_API_KEY"):
            # without a key openFDA allows 1,000 requests a day per IP; a free key raises that to 120,000
            q += "&api_key=" + os.environ["OPENFDA_API_KEY"]
        if sort:
            q += f"&sort={sort}"
        d = _get(q)
        res = d.get("results") or []
        out += res
        if len(res) < limit:
            break
    return out


GENERIC_WORDS = {"PHARMACEUTICALS", "PHARMACEUTICAL", "THERAPEUTICS", "BIOSCIENCES", "LABORATORIES", "MEDICAL", "HEALTHCARE", "HEALTH",
                 "TECHNOLOGIES", "INTERNATIONAL", "SCIENCES", "SCIENTIFIC", "BIOTECH", "BIOLOGICS", "SYSTEMS", "GLOBAL", "MANUFACTURING",
                 "LABORATOIRES", "LABORATORIOS", "COMPANIA", "COMERCIAL", "PRODUTOS", "INDIA", "CHINA", "JAPAN"}


def search_words(phrases, n=4):
    """First words of the company's names, for openFDA's prefix search (its firm-name fields match from the start
    of the name, so 'LILLY*' misses 'ELI LILLY AND CO'). Generic first words are skipped; up to n distinct words
    (a parent and its operating brands: JOHNSON, JANSSEN)."""
    out = []
    for p in phrases:
        ts = tokens(p)
        if ts and ts[0] not in GENERIC_WORDS and len(ts[0]) >= 3 and ts[0] not in out:
            out.append(ts[0])
    return out[:n]


def approvals(phrases, since):
    out, seen = [], set()
    rows = [r for w in search_words(phrases) for r in _fda_search("drug/drugsfda", "sponsor_name", w, pages=4)]
    for r in rows:
        if r.get("application_number") in seen:
            continue
        seen.add(r.get("application_number"))
        if not name_ok(r.get("sponsor_name") or "", phrases):
            continue
        brands = sorted({p.get("brand_name") for p in r.get("products") or [] if p.get("brand_name")})
        ingredients = sorted({a.get("name") for p in r.get("products") or [] for a in p.get("active_ingredients") or [] if a.get("name")})
        for s in r.get("submissions") or []:
            d = _date(s.get("submission_status_date"))
            if s.get("submission_status") != "AP" or not d or d < since:
                continue
            out.append({"application": r.get("application_number"), "sponsor": r.get("sponsor_name"), "brands": brands[:4], "ingredients": ingredients[:4],
                        "type": s.get("submission_type"), "number": s.get("submission_number"), "date": d,
                        "class": s.get("submission_class_code_description") or s.get("submission_class_code"),
                        "priority": s.get("review_priority"),
                        "url": f"https://www.accessdata.fda.gov/scripts/cder/daf/index.cfm?event=overview.process&ApplNo={re.sub(r'[^0-9]', '', r.get('application_number') or '')}"})
    out.sort(key=lambda x: x["date"], reverse=True)
    return out


def recalls(phrases, since):
    out = []
    for kind in ("drug", "device", "food"):
        for r in [x for w in search_words(phrases, 2) for x in _fda_search(f"{kind}/enforcement", "recalling_firm", w,
                                                                       extra=f"+AND+report_date:[{since.replace('-', '')}+TO+29991231]", pages=2)]:
            if not name_ok(r.get("recalling_firm") or "", phrases):
                continue
            out.append({"kind": kind, "number": r.get("recall_number"), "class": r.get("classification"), "status": r.get("status"),
                        "date": _date(r.get("report_date")), "initiated": _date(r.get("recall_initiation_date")), "firm": r.get("recalling_firm"),
                        "product": (r.get("product_description") or "")[:240], "reason": (r.get("reason_for_recall") or "")[:300],
                        "quantity": (r.get("product_quantity") or "")[:80], "distribution": (r.get("distribution_pattern") or "")[:120]})
    seen, uniq = set(), []
    for r in sorted(out, key=lambda x: x["date"] or "", reverse=True):
        if r["number"] in seen:
            continue
        seen.add(r["number"])
        uniq.append(r)
    return uniq


def clearances(phrases, since):
    out, seen = [], set()
    rows = [r for w in search_words(phrases, 2) for r in _fda_search("device/510k", "applicant", w, extra=f"+AND+decision_date:[{since}+TO+2999-12-31]",
                                                                  pages=3, sort="decision_date:desc")]
    for r in rows:
        if not name_ok(r.get("applicant") or "", phrases) or r.get("k_number") in seen:
            continue
        seen.add(r.get("k_number"))
        out.append({"k": r.get("k_number"), "device": (r.get("device_name") or "")[:200], "date": _date(r.get("decision_date")),
                    "decision": r.get("decision_description") or r.get("decision_code"), "applicant": r.get("applicant"),
                    "product_code": r.get("product_code"),
                    "url": f"https://www.accessdata.fda.gov/scripts/cdrh/cfdocs/cfpmn/pmn.cfm?ID={r.get('k_number')}"})
    return out


# ---------------------------------------------------------------- job

def build(sb, tk, phrases, today):
    since3 = (today - dt.timedelta(days=3 * 365)).isoformat()
    since2 = (today - dt.timedelta(days=2 * 365)).isoformat()
    tr = trials(phrases)
    ap = approvals(phrases, since3)
    rc = recalls(phrases, since2)
    cl = clearances(phrases, since2)
    doc = {"ticker": tk, "generated": now_iso(), "names": phrases,
           "trials": {"summary": summarize_trials(tr, today), "rows": sorted(tr, key=lambda r: (r["status"] not in ACTIVE, -(phase_rank(r.get("phase"))),
                                                                                               r.get("primary_completion") or ""))[:300]},
           "approvals": ap[:120], "recalls": rc[:120], "clearances": cl[:150],
           "counts": {"trials": len(tr), "approvals_3y": len(ap), "original_approvals_3y": sum(1 for a in ap if a["type"] == "ORIG"),
                      "recalls_2y": len(rc), "class1_recalls_2y": sum(1 for r in rc if r["class"] == "Class I"), "clearances_2y": len(cl)},
           "sources": {"clinicaltrials": "https://clinicaltrials.gov/data-api/api", "openfda": "https://open.fda.gov/apis/"}}
    if any(doc["counts"].values()):
        sb.put_json(f"regulatory/{tk}.json", doc, cache="max-age=3600")
    return doc


def company_phrases(sb, tk):
    """Registry names plus the brand and operating-unit names filings use (Janssen for JNJ, Genentech is external)."""
    from .altdata import entity_names
    from .supply_core import COVERED_ALIASES
    reg = list(entity_names(sb, tk, max_phrases=6))
    names = reg[:1] + [a for a in COVERED_ALIASES.get(tk, []) if a not in reg] + reg[1:]
    return names


def targets():
    from .universe import UNIVERSE
    return [t for t, _c, _n, s in UNIVERSE if s == "Health Care"]


def regulatory_job(sb, tickers=None, per_run=40, workers=4, time_budget=600, log=print):
    t0 = time.time()
    today = dt.date.today()
    st = get_raw_json(sb, STATE, {}) or {}
    universe = tickers or targets()
    if not tickers:
        cur = int(st.get("cursor", 0)) % max(1, len(universe))
        universe = (universe[cur:] + universe[:cur])[:per_run]
    done, errors, cats = [], {}, []

    def one(tk):
        if time.time() - t0 > time_budget - 60:
            return tk, None, "budget"
        try:
            phrases = company_phrases(sb, tk)
            return tk, build(sb, tk, phrases, today), None
        except Exception as e:
            return tk, None, str(e)[:200]
    with ThreadPoolExecutor(workers) as ex:
        for tk, doc, err in ex.map(one, universe):
            if doc:
                done.append(tk)
            elif err != "budget":
                errors[tk] = err
    if not tickers:
        st["cursor"] = (int(st.get("cursor", 0)) + len(done) + len(errors)) % max(1, len(targets()))
    st["updated_at"] = now_iso()
    put_raw_json(sb, STATE, st)
    publish_catalysts(sb, today, log=log)
    return {"companies": len(done), "errors": errors, "seconds": round(time.time() - t0)}


def publish_catalysts(sb, today, log=print):
    """Universe files from the per-company files: regulatory/catalysts.json (upcoming primary completions) and
    regulatory/recent.json (original approvals, Class I recalls and stopped late-stage trials in the last 90 days)."""
    rows, recent = [], []
    since = (today - dt.timedelta(days=90)).isoformat()

    def read(tk):
        try:
            return tk, sb.get_json(f"regulatory/{tk}.json")
        except Exception:
            return tk, None
    with ThreadPoolExecutor(12) as ex:
        for tk, d in ex.map(read, targets()):
            if not d:
                continue
            summ = ((d.get("trials") or {}).get("summary") or {})
            for r in summ.get("upcoming") or []:
                rows.append({"ticker": tk, **{k: r.get(k) for k in ("nct", "title", "acronym", "phase", "status", "primary_completion",
                                                                     "primary_completion_type", "enrollment", "conditions", "interventions")}})
            for a in d.get("approvals") or []:
                if a.get("type") == "ORIG" and (a.get("date") or "") >= since:
                    recent.append({"ticker": tk, "kind": "approval", "date": a["date"], "title": ", ".join(a.get("brands") or a.get("ingredients") or [])
                                   or a.get("application"), "detail": a.get("class"), "priority": a.get("priority"), "url": a.get("url"),
                                   "id": f"{a.get('application')}-{a.get('number')}"})
            for r in d.get("recalls") or []:
                if r.get("class") == "Class I" and (r.get("date") or "") >= since:
                    recent.append({"ticker": tk, "kind": "recall", "date": r["date"], "title": r.get("product"), "detail": r.get("reason"),
                                   "recall_kind": r.get("kind"), "id": r.get("number"),
                                   "url": "https://www.accessdata.fda.gov/scripts/ires/index.cfm"})
            for t in summ.get("recent_changes") or []:
                if t.get("status") in STOPPED and phase_rank(t.get("phase")) >= 2 and (t.get("updated") or "") >= since:
                    recent.append({"ticker": tk, "kind": "trial_stopped", "date": t["updated"], "title": t.get("title"), "detail": t.get("why_stopped"),
                                   "phase": t.get("phase"), "status": t.get("status"), "id": t.get("nct"),
                                   "url": f"https://clinicaltrials.gov/study/{t.get('nct')}"})
    rows.sort(key=lambda r: (r.get("phase") not in ("P3", "P2/P3"), r["primary_completion"] or ""))
    recent.sort(key=lambda r: r["date"], reverse=True)
    sb.put_json("regulatory/catalysts.json", {"generated": now_iso(), "as_of": today.isoformat(), "n": len(rows), "rows": rows[:1500]},
                cache="max-age=3600")
    sb.put_json("regulatory/recent.json", {"generated": now_iso(), "since": since, "n": len(recent), "rows": recent[:800]}, cache="max-age=3600")
    return len(rows)
