"""Corporate PAC money from the FEC bulk data files (www.fec.gov/files/bulk-downloads/{CYCLE}/...; no key).

- cm{yy}.zip  committee master: a PAC is the company's when its connected organization (CONNECTED_ORG_NM) equals the
  company's SEC legal name or an Exhibit 21 subsidiary after normalisation, and its organization type is C
  (corporation) or the committee is a separate segregated fund (type Q/N).
- webk{yy}.zip PAC and party summary: receipts, disbursements, contributions to other committees per cycle.
- pas2{yy}.zip contributions from committees to candidates, joined to cn{yy}.zip for candidate party and office.
  Recipients are linked to sitting members through the FEC candidate ids in congress-legislators.

Employee (individual) contribution totals are not collected: they require the multi-gigabyte individual
contributions file or per-employer API calls that DEMO_KEY rate limits do not allow for 1,100 companies.
"""
import csv
import io
import zipfile

from .altdata import norm_name
from .common import now_iso
from .gov_common import fetch, get_raw_json, put_raw_json, src

BULK = "https://www.fec.gov/files/bulk-downloads/{cycle}/{name}{yy}.zip"
COMMITTEE_URL = "https://www.fec.gov/data/committee/{id}/?cycle={cycle}"
CANDIDATE_URL = "https://www.fec.gov/data/candidate/{id}/"
PARTY = {"DEM": "Democratic", "REP": "Republican", "DFL": "Democratic", "IND": "Independent", "LIB": "Libertarian", "GRE": "Green"}


def _rows(cycle, name):
    yy = str(cycle)[2:]
    body = fetch(BULK.format(cycle=cycle, name=name, yy=yy), raw=True, timeout=300, retries=3)
    z = zipfile.ZipFile(io.BytesIO(body))
    txt = z.read(z.namelist()[0]).decode("latin-1")
    return csv.reader(io.StringIO(txt), delimiter="|", quoting=csv.QUOTE_NONE)


def load_cycle(cycle, log=print):
    """Parses the four files for one cycle into compact dicts."""
    committees = {}
    for r in _rows(cycle, "cm"):
        if len(r) < 15:
            continue
        committees[r[0]] = {"id": r[0], "name": r[1], "designation": r[8], "type": r[9], "org_type": r[12], "connected_org": r[13]}
    summary = {}
    for r in _rows(cycle, "webk"):
        if len(r) < 27:
            continue
        f = lambda i: float(r[i] or 0)
        summary[r[0]] = {"receipts": f(5), "individual_contributions": f(7), "disbursements": f(12), "contrib_to_other": f(22),
                         "independent_expenditures": f(23), "cash_on_hand": f(19), "coverage_end": r[26]}
    cands = {}
    for r in _rows(cycle, "cn"):
        if len(r) < 10:
            continue
        cands[r[0]] = {"id": r[0], "name": r[1], "party": r[2], "office": r[5], "state": r[4], "district": r[6]}
    contribs = {}
    for r in _rows(cycle, "pas2"):
        if len(r) < 22 or not r[16]:
            continue
        if r[5] not in ("24K", "24Z", "24E", "24A"):
            continue
        try:
            amt = float(r[14] or 0)
        except ValueError:
            continue
        contribs.setdefault(r[0], {}).setdefault(r[16], [0.0, r[5]])
        contribs[r[0]][r[16]][0] += amt
    log(f"fec {cycle}: committees {len(committees)}, summaries {len(summary)}, candidates {len(cands)}, contributors {len(contribs)}")
    return {"cycle": cycle, "committees": committees, "summary": summary, "candidates": cands, "contribs": contribs, "retrieved_at": now_iso()}


def company_pacs(cycles_data, names, subsidiaries, fec_to_member, members):
    want = {norm_name(n) for n in list(names) + list(subsidiaries or []) if n}
    out = {"committees": [], "cycles": []}
    ids = {}
    for cd in cycles_data:
        for cid, c in cd["committees"].items():
            if c["connected_org"] and norm_name(c["connected_org"]) in want and (c["org_type"] == "C" or c["type"] in ("Q", "N")):
                ids.setdefault(cid, c)
    for cid, c in ids.items():
        out["committees"].append({"id": cid, "name": c["name"], "connected_org": c["connected_org"], "type": c["type"], "org_type": c["org_type"],
                                  "url": f"https://www.fec.gov/data/committee/{cid}/"})
    all_cands = {}
    for cd in cycles_data:
        all_cands.update(cd["candidates"])
    for cd in cycles_data:
        cyc = cd["cycle"]
        row = {"cycle": cyc, "receipts": 0.0, "disbursements": 0.0, "to_candidates": 0.0, "dem": 0.0, "rep": 0.0, "other": 0.0, "committees": [],
               "recipients": []}
        rec = {}
        for cid in ids:
            s = cd["summary"].get(cid)
            if s:
                row["receipts"] += s["receipts"]
                row["disbursements"] += s["disbursements"]
                row["committees"].append({"id": cid, **s, "url": COMMITTEE_URL.format(id=cid, cycle=cyc)})
            for cand, (amt, tp) in (cd["contribs"].get(cid) or {}).items():
                if tp not in ("24K",):  # direct contributions only; 24E/24A independent spending is reported separately
                    continue
                rec[cand] = rec.get(cand, 0.0) + amt
        for cand, amt in rec.items():
            c = cd["candidates"].get(cand) or all_cands.get(cand) or {"id": cand, "name": cand, "party": "", "office": "", "state": ""}
            party = c.get("party") or ""
            key = "dem" if party in ("DEM", "DFL") else "rep" if party == "REP" else "other"
            row[key] += amt
            row["to_candidates"] += amt
            m = members.get(fec_to_member.get(cand)) if fec_to_member.get(cand) else None
            row["recipients"].append({"cand_id": cand, "name": c.get("name"), "party": PARTY.get(party, party), "office": c.get("office"),
                                      "state": c.get("state"), "district": c.get("district"), "amount": round(amt, 2),
                                      "member": (m or {}).get("name"), "bioguide": (m or {}).get("bioguide"),
                                      "committee_ids": [x["id"] for x in (m or {}).get("committees", [])],
                                      "url": CANDIDATE_URL.format(id=cand)})
        row["recipients"].sort(key=lambda r: -r["amount"])
        row["n_recipients"] = len(row["recipients"])
        row["recipients"] = row["recipients"][:25]
        for k in ("receipts", "disbursements", "to_candidates", "dem", "rep", "other"):
            row[k] = round(row[k], 2)
        row["src"] = src(BULK.format(cycle=cyc, name="webk", yy=str(cyc)[2:]), "FEC bulk files cm/webk/pas2/cn, connected-organization exact match", cd["retrieved_at"])
        if ids:
            out["cycles"].append(row)
    return out


def fec_member_map(members):
    """FEC candidate id -> bioguide, from the ids congress-legislators keeps for each member."""
    out = {}
    for m in members.values():
        for fid in m.get("fec") or []:
            out[fid] = m["bioguide"]
    return out
