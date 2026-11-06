"""Federal awards to the company and its subsidiaries from USAspending.gov (api.usaspending.gov/api/v2, no key).

Entities: USAspending recipient records whose normalized name equals the company's SEC legal name (current or former)
or an Exhibit 21 subsidiary. Parent records (level P) carry their SAM.gov child UEIs; searching by the parent UEI
returns awards to the parent and its children. Stand-alone records (level R) are added by their own UEI.

Per company: obligations by federal fiscal year split into contracts, grants, loans and other assistance; awarding
agencies for the latest complete and the current fiscal year; and the largest contract and assistance awards of the
last three fiscal years. Awards whose description names the CHIPS Act or the Inflation Reduction Act are flagged.
"""
import datetime as dt
import json

from .altdata import core_name, norm_name
from .common import now_iso, request
from .gov_common import UA_GOV, polite, src

API = "https://api.usaspending.gov/api/v2/"
GROUPS = {"contracts": ["A", "B", "C", "D"], "grants": ["02", "03", "04", "05"], "loans": ["07", "08"],
          "other": ["06", "09", "10", "11"]}
PROGRAM_FLAGS = (("CHIPS", "CHIPS and Science Act"), ("CHIPS ACT", "CHIPS and Science Act"),
                 ("INFLATION REDUCTION ACT", "Inflation Reduction Act"), ("IRA ", "Inflation Reduction Act"),
                 ("BIPARTISAN INFRASTRUCTURE", "Infrastructure Investment and Jobs Act"), ("IIJA", "Infrastructure Investment and Jobs Act"))


def _post(path, body):
    polite("api.usaspending.gov", 0.25)
    return json.loads(request(API + path, method="POST", data=body, headers={**UA_GOV, "Content-Type": "application/json"}, timeout=120, retries=5))


def fiscal_year(d=None):
    d = d or dt.date.today()
    return d.year + (1 if d.month >= 10 else 0)


def find_entities(names, subsidiaries=()):
    want = {norm_name(n): n for n in list(names) + list(subsidiaries) if n}
    phrases = list(dict.fromkeys(core_name(n) for n in names if n and core_name(n)))
    found = {}
    for phrase in phrases:
        for page in range(1, 4):
            d = _post("recipient/", {"keyword": phrase, "award_type": "all", "limit": 100, "page": page, "sort": "amount", "order": "desc"})
            for r in d.get("results", []):
                k = norm_name(r.get("name"))
                if k in want and r.get("recipient_level") in ("P", "R", "C") and r.get("uei"):
                    found[r["id"]] = {"id": r["id"], "uei": r["uei"], "name": r["name"], "level": r["recipient_level"],
                                      "amount_all_time": r.get("amount"), "matched_on": want[k],
                                      "url": f"https://www.usaspending.gov/recipient/{r['id']}/latest"}
            if not (d.get("page_metadata") or {}).get("hasNext"):
                break
    return list(found.values()), phrases


def company(names, subsidiaries=(), fy_from=None, today=None, log=print):
    today = today or dt.date.today()
    cur = fiscal_year(today)
    fy_from = fy_from or cur - 7
    retrieved = now_iso()
    ents, phrases = find_entities(names, subsidiaries)
    base = {"entities": ents, "search": phrases, "retrieved_at": retrieved,
            "src": src("https://api.usaspending.gov/api/v2/", "USAspending API: recipient search, exact normalized-name match, then UEI-filtered award search", retrieved)}
    if not ents:
        return {**base, "fy": [], "agencies": {}, "awards": [], "programs": [], "status": "no_match"}
    # Large companies have dozens of same-name parent records; the API times out on long UEI lists, so the
    # largest records (by all-time amount) are used and the rest are listed as matched but not queried.
    ents.sort(key=lambda e: -(e.get("amount_all_time") or 0))
    ueis = list(dict.fromkeys(e["uei"] for e in ents))[:12]
    for e in ents:
        e["queried"] = e["uei"] in ueis
    period = [{"start_date": f"{fy_from - 1}-10-01", "end_date": today.isoformat()}]
    fy = {}
    try:
        d = _post("search/spending_over_time/", {"group": "fiscal_year", "filters": {"recipient_search_text": ueis, "time_period": period}})
    except Exception:
        ueis = ueis[:4]
        for e in ents:
            e["queried"] = e["uei"] in ueis
        d = _post("search/spending_over_time/", {"group": "fiscal_year", "filters": {"recipient_search_text": ueis, "time_period": period}})
    for r in d.get("results", []):
        y = int(r["time_period"]["fiscal_year"])
        row = {"fy": y, "contracts": float(r.get("Contract_Obligations") or 0) + float(r.get("Idv_Obligations") or 0),
               "grants": float(r.get("Grant_Obligations") or 0), "loans": float(r.get("Loan_Obligations") or 0),
               "other": float(r.get("Direct_Obligations") or 0) + float(r.get("Other_Obligations") or 0), "partial": y >= cur}
        row = {k: (round(v, 2) if isinstance(v, float) else v) for k, v in row.items()}
        row["total"] = round(float(r.get("aggregated_amount") or 0), 2)
        if row["total"]:
            fy[y] = row
    agencies = {}
    for y in (cur - 1, cur):
        d = _post("search/spending_by_category/awarding_agency/", {"filters": {"recipient_search_text": ueis,
                                                                               "time_period": [{"start_date": f"{y - 1}-10-01", "end_date": min(today, dt.date(y, 9, 30)).isoformat()}]},
                                                                   "limit": 10})
        agencies[str(y)] = [{"name": a.get("name"), "code": a.get("code"), "amount": a.get("amount")} for a in d.get("results", [])]
    awards = []
    start = f"{cur - 3}-10-01"
    for group in ("contracts", "grants", "loans"):
        fields = ["Award ID", "Recipient Name", "Award Amount", "Awarding Agency", "Awarding Sub Agency", "Start Date", "End Date",
                  "Description", "generated_internal_id"]
        if group == "loans":
            fields = ["Award ID", "Recipient Name", "Loan Value", "Awarding Agency", "Awarding Sub Agency", "Issued Date", "Description", "generated_internal_id"]
        d = _post("search/spending_by_award/", {"filters": {"recipient_search_text": ueis, "award_type_codes": GROUPS[group],
                                                            "time_period": [{"start_date": start, "end_date": today.isoformat()}]},
                                                "fields": fields, "sort": "Loan Value" if group == "loans" else "Award Amount", "order": "desc",
                                                "limit": 10, "page": 1})
        for a in d.get("results", []):
            desc = (a.get("Description") or "")[:400]
            flags = sorted({label for key, label in PROGRAM_FLAGS if key in desc.upper()})
            gid = a.get("generated_internal_id")
            awards.append({"id": f"usa:{gid or a.get('Award ID')}", "award_id": a.get("Award ID"), "group": group, "recipient": a.get("Recipient Name"),
                           "amount": a.get("Award Amount") if group != "loans" else a.get("Loan Value"), "agency": a.get("Awarding Agency"),
                           "sub_agency": a.get("Awarding Sub Agency"), "start": a.get("Start Date") or a.get("Issued Date"), "end": a.get("End Date"),
                           "description": desc, "programs": flags,
                           "url": f"https://www.usaspending.gov/award/{gid}" if gid else None,
                           "src": src(f"https://www.usaspending.gov/award/{gid}" if gid else API, "USAspending spending_by_award (UEI filter)", retrieved)})
    awards.sort(key=lambda a: -(a.get("amount") or 0))
    base["entities"] = ents[:40]
    base["entity_count"] = len(ents)
    has = bool(fy) or bool(awards)
    return {**base, "status": "ok" if has else "no_awards", "fy": [fy[k] for k in sorted(fy)], "agencies": agencies, "awards": awards[:24], "ueis": ueis,
            "programs": sorted({p for a in awards for p in a["programs"]})}
