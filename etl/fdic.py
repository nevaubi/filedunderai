"""FDIC BankFind Suite API (https://api.fdic.gov/banks/docs/): annual industry aggregates and bank failures."""
import json
import urllib.parse

from .common import request

API = "https://api.fdic.gov/banks/"
PROVIDER = "Federal Deposit Insurance Corporation"
FIELDS = {
    "ASSET": ("Total assets, FDIC-insured institutions", "Thousands of dollars", dict(scale=1e-9, dunits="Trillions of dollars", short_units="tn", dp=2)),
    "DEP": ("Total deposits, FDIC-insured institutions", "Thousands of dollars", dict(scale=1e-9, dunits="Trillions of dollars", short_units="tn", dp=2)),
    "NETINC": ("Net income, FDIC-insured institutions", "Thousands of dollars", dict(scale=1e-6, dunits="Billions of dollars", short_units="bn", dp=1)),
    "NTLNLS": ("Net charge-offs on loans and leases, FDIC-insured institutions", "Thousands of dollars", dict(scale=1e-6, dunits="Billions of dollars", short_units="bn", dp=1)),
    "BANKS": ("Number of FDIC-insured institutions", "Institutions", dict(dp=0)),
}


def get(path, params):
    return json.loads(request(API + path + "?" + urllib.parse.urlencode(params), timeout=120, retries=4))


def collect(since=1990, log=print):
    params = {"filters": f'STNAME:"All States and Territories" AND YEAR:[{since} TO 2100]', "fields": "YEAR,STNAME,CB_SI," + ",".join(FIELDS),
              "limit": 1000, "sort_by": "YEAR", "sort_order": "ASC"}
    rows = [x["data"] for x in get("summary", params)["data"]]
    url = API + "summary?" + urllib.parse.urlencode({k: v for k, v in params.items() if k != "limit"})
    by_year = {}
    for r in rows:
        by_year.setdefault(r["YEAR"], {})[r["CB_SI"]] = r
    out = []
    cols = ["YEAR", "STNAME", "CB_SI"] + list(FIELDS)
    snap = [[str(r.get(c, "")) for c in cols] for r in sorted(rows, key=lambda r: (r["YEAR"], r["CB_SI"]))]
    for f, (title, units, extra) in FIELDS.items():
        obs = []
        for y, parts in sorted(by_year.items()):
            if "CB" in parts and "SI" in parts and parts["CB"].get(f) is not None and parts["SI"].get(f) is not None:
                obs.append((f"{y}-12-31", str(float(parts["CB"][f]) + float(parts["SI"][f]))))
        meta = {"id": f"FDIC:{f}", "source": "BankFind Suite, historical summary of bank data", "provider": PROVIDER, "title": title, "units": units,
                "frequency": "A", "seasonal_adj": None, "geography": "United States", "source_url": "https://banks.data.fdic.gov/bankfind-suite/historical",
                "data_url": url, "source_key": f"summary {f}", "section": "credit", "release": "FDIC:SUMMARY", "group": "fdic",
                "derived": f"{f} for commercial banks (CB) plus savings institutions (SI), \"All States and Territories\" rows",
                "notes": "Annual, year-end. The API publishes separate rows for commercial banks and savings institutions; the series adds the two."}
        meta.update(extra)
        out.append({"meta": meta, "obs": obs, "snap": {"url": url, "series": f, "cols": cols, "rows": snap, "keys": [f"{s[0]}-12-31" for s in snap], "hl": cols.index(f)}})
    fp = {"fields": "NAME,CITYST,FAILDATE,FAILYR,QBFASSET,QBFDEP,COST,RESTYPE", "limit": 10000, "sort_by": "FAILDATE", "sort_order": "ASC"}
    fails = [x["data"] for x in get("failures", fp)["data"]]
    furl = API + "failures?" + urllib.parse.urlencode({k: v for k, v in fp.items() if k != "limit"})
    years = {}
    for r in fails:
        y = str(r.get("FAILYR") or "")
        if y and y >= str(since):
            c = years.setdefault(y, [0, 0.0])
            c[0] += 1
            c[1] += float(r.get("QBFASSET") or 0)
    fcols = ["FAILDATE", "NAME", "CITYST", "QBFASSET", "QBFDEP", "COST", "RESTYPE"]
    frows = [r for r in fails if str(r.get("FAILYR") or "") >= str(since)]
    fsnap = [[str(r.get(c, "")) for c in fcols] for r in frows]
    fkeys = [f"{r.get('FAILYR')}-12-31" for r in frows]
    for sid, title, idx, units, extra in [("FAILURES", "Bank failures per year", 0, "Institutions", {"dp": 0}),
                                          ("FAILED_ASSETS", "Total assets of failed banks", 1, "Thousands of dollars", dict(scale=1e-6, dunits="Billions of dollars", short_units="bn", dp=1))]:
        meta = {"id": f"FDIC:{sid}", "source": "BankFind Suite, bank failures and assistance", "provider": PROVIDER, "title": title, "units": units,
                "frequency": "A", "seasonal_adj": None, "geography": "United States", "source_url": "https://banks.data.fdic.gov/bankfind-suite/failures",
                "data_url": furl, "source_key": "failures", "section": "credit", "release": "FDIC:FAILURES", "group": "fdicfail",
                "derived": "Count of failures by FAILYR" if idx == 0 else "Sum of QBFASSET (assets at failure, thousands of dollars) by FAILYR",
                "notes": "Calendar years. The current year is partial until December 31.", "no_changes": True}
        meta.update(extra)
        out.append({"meta": meta, "obs": [(f"{y}-12-31", str(v[idx])) for y, v in sorted(years.items())],
                    "snap": {"url": furl, "series": "failures", "cols": fcols, "rows": fsnap, "keys": fkeys, "hl": 3 if idx else 1}})
    log(f"fdic: {len(rows)} summary rows, {len(fails)} failures")
    return out
