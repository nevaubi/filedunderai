"""Form 4 insider transactions: fetch the XML behind each filing and flatten it to rows."""
import datetime as dt
import re
import xml.etree.ElementTree as ET

from .common import sec_get

CODES = {
    "P": "Open-market buy", "S": "Open-market sale", "A": "Grant or award", "M": "Option exercise", "F": "Tax withholding",
    "G": "Gift", "D": "Disposition to issuer", "C": "Conversion", "X": "Option exercise", "J": "Other", "W": "Will or inheritance",
    "I": "Discretionary", "K": "Equity swap", "L": "Small acquisition", "U": "Tender of shares", "E": "Expiration", "H": "Expiration",
    "O": "Out-of-money exercise", "Z": "Voting trust", "V": "Voluntary report",
}

def xml_url(cik, accession, primary_doc):
    doc = (primary_doc or "").split("/")[-1]
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}/{doc}"


def _t(node, path):
    if node is None:
        return None
    el = node.find(path)
    if el is None:
        return None
    v = el.find("value")
    txt = (v.text if v is not None else el.text) or ""
    txt = txt.strip()
    return txt or None


def _num(s):
    try:
        return float(s) if s not in (None, "") else None
    except ValueError:
        return None


def _bool(s):
    return str(s or "").strip().lower() in ("1", "true")


def parse(xml_text):
    """Returns (issuer_cik, owners, rows) from a Form 4 XML document."""
    root = ET.fromstring(xml_text.encode() if isinstance(xml_text, str) else xml_text)
    owners = []
    for ro in root.findall("reportingOwner"):
        name = _t(ro, "reportingOwnerId/rptOwnerName") or ""
        rel = ro.find("reportingOwnerRelationship")
        roles = []
        if rel is not None:
            if _bool(_t(rel, "isOfficer")):
                roles.append(_t(rel, "officerTitle") or "Officer")
            if _bool(_t(rel, "isDirector")):
                roles.append("Director")
            if _bool(_t(rel, "isTenPercentOwner")):
                roles.append("10% owner")
            if _bool(_t(rel, "isOther")):
                roles.append(_t(rel, "otherText") or "Other")
        owners.append({"name": name, "cik": _t(ro, "reportingOwnerId/rptOwnerCik"), "role": ", ".join(roles)})
    plan = _bool(_t(root, "aff10b5One"))
    rows = []
    for table, deriv in (("nonDerivativeTable/nonDerivativeTransaction", False), ("derivativeTable/derivativeTransaction", True)):
        for tx in root.findall(table):
            code = _t(tx, "transactionCoding/transactionCode")
            rows.append({
                "security": _t(tx, "securityTitle"),
                "tx_date": _t(tx, "transactionDate"),
                "code": code,
                "shares": _num(_t(tx, "transactionAmounts/transactionShares")),
                "price": _num(_t(tx, "transactionAmounts/transactionPricePerShare")),
                "acq_disp": _t(tx, "transactionAmounts/transactionAcquiredDisposedCode"),
                "owned_after": _num(_t(tx, "postTransactionAmounts/sharesOwnedFollowingTransaction")),
                "ownership": _t(tx, "ownershipNature/directOrIndirectOwnership"),
                "derivative": deriv,
                "plan_10b5": plan,
            })
    issuer = _t(root, "issuer/issuerCik")
    return (int(issuer) if issuer and issuer.isdigit() else None), owners, rows


def fetch(ticker, cik, filing):
    """Fetches and flattens one Form 4 filing into insider_transactions rows."""
    txt = sec_get(xml_url(cik, filing["accession"], filing["primary_doc"]), timeout=40)
    issuer, owners, rows = parse(txt)
    if issuer and issuer != int(cik):
        # The company is the reporting owner here (a stake it holds in another issuer), not the issuer.
        rows = []
    owner = owners[0] if owners else {"name": "", "cik": None, "role": ""}
    if len(owners) > 1:
        owner = {"name": owner["name"] + f" (+{len(owners) - 1})", "cik": owner["cik"], "role": owner["role"]}
    out = []
    for i, r in enumerate(rows):
        out.append({"accession": filing["accession"], "seq": i, "ticker": ticker, "cik": int(cik), "filed": filing["filed"],
                    "owner": owner["name"][:200], "owner_cik": owner["cik"], "role": owner["role"][:200], **r})
    if not out:
        # Keep a marker so the filing is not fetched again (holdings-only or amendment with no table rows).
        out.append({"accession": filing["accession"], "seq": -1, "ticker": ticker, "cik": int(cik), "filed": filing["filed"],
                    "owner": owner["name"][:200], "owner_cik": owner["cik"], "role": owner["role"][:200], "security": None, "tx_date": None,
                    "code": None, "shares": None, "price": None, "acq_disp": None, "owned_after": None, "ownership": None,
                    "derivative": False, "plan_10b5": False})
    return out


def dedupe_joint(rows):
    """One economic transaction reported on separate Forms 4 by related owners (a holding company and the trust or
    family member that controls it, e.g. LOR Inc and the R. Randall Rollins Voting Trust) appears once per filer with
    the same date, code, shares, price and shares owned afterwards. Count it once; the other filers are kept as
    `joint` on the surviving row."""
    out, seen = [], {}
    for r in sorted(rows, key=lambda r: (r.get("filed") or "", r.get("accession") or "", r.get("seq") or 0)):
        if r.get("shares") is None or r.get("owned_after") is None:
            out.append(r)
            continue
        key = (r.get("tx_date"), r.get("code"), r.get("security"), bool(r.get("derivative")), float(r["shares"]),
               round(float(r.get("price") or 0), 4), float(r["owned_after"]))
        first = seen.get(key)
        if first is not None and first.get("owner") != r.get("owner"):
            first.setdefault("joint", [])
            if r.get("owner") not in first["joint"]:
                first["joint"].append(r.get("owner"))
            continue
        seen[key] = r
        out.append(r)
    return out


_CO_FILERS = re.compile(r"\s*\(\+\d+\)\s*$")
_ENTITY = re.compile(r"\b(L\.?P\.?|LLC|L\.L\.C\.|INC\.?|INCORPORATED|CORP\.?|CORPORATION|CO\.|LTD\.?|LIMITED|PLC|N\.V\.|S\.A\.|AG|GMBH|"
                     r"FUND|FUNDS|TRUST|TRUSTEES?|CAPITAL|PARTNERS|PARTNERSHIP|ADVISORS?|ADVISERS?|MANAGEMENT|HOLDINGS?|INVESTMENTS?|"
                     r"INVESTORS|GROUP|FOUNDATION|ASSOCIATES|VENTURES|EQUITY|ASSET|ASSETS|BANK|SECURITIES|OPPORTUNITIES|MASTER|OFFSHORE|"
                     r"ENDOWMENT|PENSION|SOVEREIGN|ESTATE OF)\b", re.I)


def canonical_owner(name):
    """Owner name without the "(+N)" co-filer suffix this module appends for multi-owner filings."""
    return _CO_FILERS.sub("", name or "").strip()


def owner_key(row):
    """One identity per reporting owner: the owner CIK when the filing gave one, else the canonical name, so
    "Haggart Dylan G. (+1)" and "Haggart Dylan G. (+2)" are one buyer."""
    cik = str(row.get("owner_cik") or "").lstrip("0")
    return f"cik:{cik}" if cik else "name:" + canonical_owner(row.get("owner")).upper()


def owner_category(name, role):
    """officer | director (individuals) | entity (a fund, company or trust, including one holding a board seat,
    e.g. an investment adviser that designates a director) | ten_pct (a 10% holder that is a person) | other."""
    role = (role or "").lower()
    entity = bool(_ENTITY.search(canonical_owner(name)))
    if entity:
        return "entity"
    if any(x in role for x in ("officer", "chief", "president", "ceo", "cfo", "coo", "vp", "vice", "treasurer", "secretary",
                               "controller", "counsel", "chair", "head", "evp", "svp", "general manager", "principal",
                               "remarks")):
        return "officer"
    if "director" in role:
        return "director"
    if "10%" in role:
        return "ten_pct"
    return "other"


INDIVIDUAL = ("officer", "director")


def summarize(rows, today=None):
    """Bundle for the site: open-market activity and the latest transactions.

    Window counts use one identity per owner (owner_key) and each joint-filed transaction once. Individual
    officers and directors are counted separately from entities and 10% holders (`ind_*` fields): a fund buying
    through its board designee is fund flow, not an executive's own purchase."""
    today = today or dt.date.today()
    real = dedupe_joint(r for r in rows if r.get("seq", 0) >= 0 and r.get("tx_date"))
    real.sort(key=lambda r: (r["tx_date"], r.get("accession") or "", r.get("seq") or 0), reverse=True)
    for r in real:
        r["category"] = owner_category(r.get("owner"), r.get("role"))

    def window(days):
        cut = (today - dt.timedelta(days=days)).isoformat()
        buys = [r for r in real if r["code"] == "P" and not r.get("derivative") and r["tx_date"] >= cut]
        sells = [r for r in real if r["code"] == "S" and not r.get("derivative") and r["tx_date"] >= cut]
        val = lambda rs: sum((r.get("shares") or 0) * (r.get("price") or 0) for r in rs)
        ib = [r for r in buys if r["category"] in INDIVIDUAL]
        isl = [r for r in sells if r["category"] in INDIVIDUAL]
        return {"buys": len(buys), "sells": len(sells), "buy_value": round(val(buys)), "sell_value": round(val(sells)),
                "buyers": len({owner_key(r) for r in buys}), "sellers": len({owner_key(r) for r in sells}),
                "plan_sells": sum(1 for r in sells if r.get("plan_10b5")),
                "plan_sell_value": round(val([r for r in sells if r.get("plan_10b5")])),
                "ind_buys": len(ib), "ind_buy_value": round(val(ib)), "ind_buyers": len({owner_key(r) for r in ib}),
                "ind_sells": len(isl), "ind_sell_value": round(val(isl)), "ind_sellers": len({owner_key(r) for r in isl}),
                "ind_disc_sell_value": round(val([r for r in isl if not r.get("plan_10b5")])),
                "entity_buy_value": round(val([r for r in buys if r["category"] not in INDIVIDUAL])),
                "entity_sell_value": round(val([r for r in sells if r["category"] not in INDIVIDUAL]))}

    by_owner = {}
    for r in real:
        if r.get("derivative") or r["code"] not in ("P", "S"):
            continue
        o = by_owner.setdefault(owner_key(r), {"owner": canonical_owner(r.get("owner")), "role": r.get("role"), "category": r["category"],
                                               "buy_value": 0, "sell_value": 0, "n": 0, "last": r["tx_date"]})
        v = (r["shares"] or 0) * (r["price"] or 0)
        o["buy_value" if r["code"] == "P" else "sell_value"] += v
        o["n"] += 1
    owners = sorted(by_owner.values(), key=lambda o: -(o["buy_value"] + o["sell_value"]))[:15]
    for o in owners:
        o["buy_value"], o["sell_value"] = round(o["buy_value"]), round(o["sell_value"])
    keep = ("accession", "seq", "filed", "owner", "role", "category", "tx_date", "code", "security", "shares", "price", "acq_disp",
            "owned_after", "ownership", "derivative", "plan_10b5", "joint")
    return {
        "w90": window(90), "w365": window(365), "owners": owners,
        "rows": [{k: r.get(k) for k in keep} for r in real[:400]],
    }
