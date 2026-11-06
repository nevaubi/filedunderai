"""Federal lobbying detail from the Senate/House Lobbying Disclosure Act API (lda.gov, formerly lda.senate.gov).

Clients are matched on the exact normalized legal name (current or former SEC name, or an Exhibit 21 subsidiary),
the same rule etl/altdata.py uses for quarterly spend. This module adds what the quarterly totals do not carry:
general issue areas, the agencies and chambers lobbied, and the bills named in the activity descriptions, each with
the filing it came from and the verbatim description text.
"""
import re

from .altdata import _lda_get, core_name, norm_name
from .common import now_iso
from .gov_common import src

LDA_URL = "https://lda.gov/api/v1/filings/"
QUARTERS = {"first_quarter": 1, "second_quarter": 2, "third_quarter": 3, "fourth_quarter": 4}
BILL_RX = re.compile(r"(?<![A-Za-z.])(H\.?\s?R\.?|S\.?|H\.?\s?J\.?\s?Res\.?|S\.?\s?J\.?\s?Res\.?|H\.?\s?Res\.?|S\.?\s?Res\.?|H\.?\s?Con\.?\s?Res\.?|S\.?\s?Con\.?\s?Res\.?)\s?(\d{1,5})(?![\d,.%])(?!\s*(?:percent|million|billion))")


def norm_bill(prefix, num):
    p = re.sub(r"\s", "", prefix).upper().replace(".", "")
    return {"HR": "H.R.", "S": "S.", "HJRES": "H.J.Res.", "SJRES": "S.J.Res.", "HRES": "H.Res.", "SRES": "S.Res.",
            "HCONRES": "H.Con.Res.", "SCONRES": "S.Con.Res."}.get(p, prefix) + " " + str(int(num))


def bills_in(text):
    out = []
    for m in BILL_RX.finditer(text or ""):
        out.append((norm_bill(m.group(1), m.group(2)), m.group(0)))
    return out


def fetch_filings(names, years, log=print):
    want = {norm_name(n) for n in names if n}
    phrases = list(dict.fromkeys(core_name(n) for n in names if n and core_name(n)))
    filings = {}
    for phrase in phrases:
        for year in years:
            url, params = LDA_URL, {"client_name": phrase, "filing_year": year, "page_size": 25}
            pages = 0
            while url and pages < 12:
                d = _lda_get(url, params)
                params = None
                pages += 1
                for f in d.get("results", []):
                    if norm_name((f.get("client") or {}).get("name")) in want:
                        filings[f["filing_uuid"]] = f
                url = d.get("next")
    return filings, phrases


def summarize(filings, phrases, retrieved_at=None):
    retrieved_at = retrieved_at or now_iso()
    latest = {}
    for f in filings.values():
        if f.get("filing_period") not in QUARTERS:
            continue
        k = ((f.get("registrant") or {}).get("id"), f["filing_year"], f["filing_period"])
        if k not in latest or (f.get("dt_posted") or "") > (latest[k].get("dt_posted") or ""):
            latest[k] = f
    issues, agencies, bills, rows, quarters = {}, {}, {}, [], {}
    for f in latest.values():
        q = QUARTERS[f["filing_period"]]
        reg = (f.get("registrant") or {}).get("name") or ""
        amount = f.get("expenses") if f.get("expenses") is not None else f.get("income")
        basis = "in-house expenses" if f.get("expenses") is not None else "outside firm income"
        qk = (f["filing_year"], q)
        qq = quarters.setdefault(qk, {"year": f["filing_year"], "quarter": q, "in_house": 0.0, "outside": 0.0, "filings": []})
        if f.get("expenses") is not None:
            qq["in_house"] += float(f["expenses"])
        elif f.get("income") is not None:
            qq["outside"] += float(f["income"])
        qq["filings"].append(f["filing_uuid"])
        acts = []
        for a in f.get("lobbying_activities") or []:
            code, disp = a.get("general_issue_code"), a.get("general_issue_code_display")
            desc = (a.get("description") or "").strip()
            ents = [e.get("name") for e in a.get("government_entities") or [] if e.get("name")]
            if code:
                i = issues.setdefault(code, {"code": code, "name": disp, "filings": 0, "latest": None, "latest_filing": None})
                i["filings"] += 1
                if not i["latest"] or (f["filing_year"], q) >= tuple(i["latest"]):
                    i["latest"], i["latest_filing"], i["latest_desc"] = [f["filing_year"], q], f["filing_uuid"], desc[:600]
            for e in ents:
                g = agencies.setdefault(e, {"name": e, "filings": 0, "latest_filing": None})
                g["filings"] += 1
                g["latest_filing"] = f["filing_uuid"]
            cong = (int(f["filing_year"]) - 1789) // 2 + 1
            for b, raw in bills_in(desc):
                bb = bills.setdefault((b, cong), {"bill": b, "congress": cong, "filings": 0, "issues": set(), "first": None, "last": None, "cites": []})
                bb["filings"] += 1
                bb["issues"].add(disp or code)
                per = f"{f['filing_year']}-Q{q}"
                bb["first"] = min(bb["first"] or per, per)
                bb["last"] = max(bb["last"] or per, per)
                if len(bb["cites"]) < 3:
                    bb["cites"].append({"filing": f["filing_uuid"], "text": desc[:1500], "match": raw})
            acts.append({"issue": disp or code, "code": code, "description": desc[:1500], "entities": ents,
                         "lobbyists": len(a.get("lobbyists") or [])})
        rows.append({"id": f"lda:{f['filing_uuid']}", "uuid": f["filing_uuid"], "year": f["filing_year"], "quarter": q,
                     "type": f.get("filing_type_display") or f.get("filing_type"), "registrant": reg,
                     "client": (f.get("client") or {}).get("name"), "amount": float(amount) if amount is not None else None, "basis": basis,
                     "posted": (f.get("dt_posted") or "")[:10], "url": f.get("filing_document_url"), "activities": acts,
                     "src": src(f.get("filing_document_url") or f.get("url"), "LDA REST API filings endpoint, exact client-name match", retrieved_at)})
    rows.sort(key=lambda r: (r["year"], r["quarter"], r["posted"]), reverse=True)
    qrows = []
    for k in sorted(quarters):
        v = quarters[k]
        amt = v["in_house"] if v["in_house"] > 0 else v["outside"]
        qrows.append({"year": v["year"], "quarter": v["quarter"], "amount": round(amt, 2), "basis": "in-house report" if v["in_house"] > 0 else "outside firms",
                      "filings": v["filings"][:30], "n": len(v["filings"])})
    for b in bills.values():
        b["issues"] = sorted(x for x in b["issues"] if x)
    return {"quarters": qrows, "issues": sorted(issues.values(), key=lambda x: -x["filings"]),
            "agencies": sorted(agencies.values(), key=lambda x: -x["filings"])[:40],
            "bills": sorted(bills.values(), key=lambda x: (-x["filings"], x["bill"]))[:80],
            "filings": rows[:120], "filing_count": len(rows), "clients": sorted({r["client"] for r in rows if r.get("client")}),
            "search": phrases, "retrieved_at": retrieved_at,
            "src": src("https://lda.gov/api/v1/filings/", "LDA REST API (anonymous), client_name search then exact normalized-name filter", retrieved_at)}


def company(names, years, log=print):
    filings, phrases = fetch_filings(names, years, log=log)
    return summarize(filings, phrases)
