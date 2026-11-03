"""Segment and breakdown data from the inline XBRL in 10-K and 10-Q filings.

Every value keeps the accession, the inline element id, its concept and its context, so the site can open the
filed document and highlight the exact number. Quarterly series use the three-month facts; missing quarters are
derived from year-to-date facts (Q2 = 6M - Q1, Q3 = 9M - 6M, Q4 = FY - 9M) with both inputs recorded. Each table is
reconciled against the consolidated total for the same concept and period; an explicit residual line carries any
difference so that every table sums to its parent.
"""
import datetime as dt
import gzip
import html as htmllib
import json
import re
from collections import defaultdict

from .common import sec_get

AXES = {
    "StatementBusinessSegmentsAxis": ("segment", "Business segments"),
    "ProductOrServiceAxis": ("product", "Products and services"),
    "StatementGeographicalAxis": ("geo", "Geography"),
    "SubsegmentsAxis": ("subsegment", "Sub-segments"),
    "MajorCustomersAxis": ("customer", "Major customers"),
}
AXIS_ORDER = ["segment", "product", "geo", "subsegment", "customer", "custom"]
EXT_AXIS_HINT = re.compile(r"(Segment|Product|Revenue|Geograph|Market|Platform|Channel|Brand|Categor|Line|Business|Service|Region|Division|Portfolio)", re.I)
RECON_MEMBERS = {
    "CorporateNonSegmentMember": "Corporate and other",
    "IntersegmentEliminationMember": "Intersegment eliminations",
    "SegmentReconcilingItemsMember": "Reconciling items",
    "AllOtherSegmentsMember": "All other",
}
FAMILIES = [
    ("revenue", "Revenue", "USD", ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "RevenueFromContractWithCustomerIncludingAssessedTax",
                                  "SalesRevenueNet", "SalesRevenueGoodsNet", "SalesRevenueServicesNet", "RevenuesNetOfInterestExpense",
                                  "RegulatedAndUnregulatedOperatingRevenue", "RealEstateRevenueNet", "OperatingLeasesIncomeStatementLeaseRevenue"]),
    ("operating_income", "Operating income", "USD", ["OperatingIncomeLoss", "SegmentReportingInformationOperatingIncomeLoss"]),
    ("gross_profit", "Gross profit", "USD", ["GrossProfit"]),
    ("cost_of_revenue", "Cost of revenue", "USD", ["CostOfRevenue", "CostOfGoodsAndServicesSold"]),
    ("net_income", "Net income", "USD", ["NetIncomeLoss"]),
    ("da", "Depreciation and amortization", "USD", ["DepreciationDepletionAndAmortization", "DepreciationAndAmortization", "DepreciationAmortizationAndAccretionNet"]),
    ("capex", "Capital expenditures", "USD", ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"]),
    ("assets", "Total assets", "USD", ["Assets"]),
]
EXT_METRIC = re.compile(r"(SegmentOperatingIncome|SegmentProfit|SegmentEBITDA|AdjustedEBITDA|SegmentIncome|SegmentEarnings|OperatingProfit|SegmentContribution|SegmentAdjustedOperatingIncome)", re.I)
TABLE_YEARS = 3.4


# ---------------------------------------------------------------- parsing
_CTX = re.compile(r"<xbrli:context\b[^>]*\bid=\"([^\"]+)\"[^>]*>(.*?)</xbrli:context>", re.I | re.S)
_DIM = re.compile(r"<xbrldi:explicitmember\b[^>]*dimension=\"([^\"]+)\"[^>]*>\s*([^<\s]+)\s*</xbrldi:explicitmember>", re.I)
_TYPED = re.compile(r"<xbrldi:typedmember\b", re.I)
_FACT = re.compile(r"<ix:nonfraction\b([^>]*)>(.*?)</ix:nonfraction>", re.I | re.S)
_ATTR = re.compile(r"([\w:.-]+)\s*=\s*\"([^\"]*)\"")
_TAG = re.compile(r"<[^>]+>")


def _period(body):
    def g(tag):
        m = re.search(rf"<xbrli:{tag}>\s*([0-9-]+)\s*</xbrli:{tag}>", body, re.I)
        return m.group(1) if m else None
    return g("startdate"), g("enddate") or g("instant")


def parse_ixbrl(html):
    """Returns (contexts, facts). contexts: id -> {start, end, dims}. facts: numeric inline facts."""
    ctx = {}
    for cid, body in _CTX.findall(html):
        start, end = _period(body)
        dims = {d: m for d, m in _DIM.findall(body)}
        ctx[cid] = {"start": start, "end": end, "dims": dims, "typed": bool(_TYPED.search(body))}
    facts = []
    for attrs, inner in _FACT.findall(html):
        a = {k.lower(): v for k, v in _ATTR.findall(attrs)}
        name, cref = a.get("name"), a.get("contextref")
        if not name or cref not in ctx:
            continue
        txt = htmllib.unescape(_TAG.sub("", inner)).strip()
        fmt = a.get("format", "")
        digits = re.sub(r"[^0-9.,]", "", txt)
        if not digits:
            if "zero" in fmt or txt in ("-", "—", "–"):
                val = 0.0
            else:
                continue
        else:
            if "comma-decimal" in fmt or "numcommadecimal" in fmt:
                digits = digits.replace(".", "").replace(",", ".")
            else:
                digits = digits.replace(",", "")
            try:
                val = float(digits)
            except ValueError:
                continue
        scale = int(a.get("scale") or 0)
        val *= 10 ** scale
        if a.get("sign") == "-":
            val = -val
        facts.append({"id": a.get("id"), "name": name, "ctx": cref, "unit": (a.get("unitref") or "").lower(), "v": val})
    return ctx, facts


def humanize(qname):
    local = qname.split(":")[-1]
    local = re.sub(r"Member$", "", local)
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", local)
    s = re.sub(r"\bSegment\b$", "", s).strip()
    small = {"And", "Of", "The", "For", "In", "To", "On", "Or"}
    words = s.split()
    return " ".join(w.lower() if i and w in small else w for i, w in enumerate(words)) or local


# ---------------------------------------------------------------- labels
_LOC = re.compile(r"<(?:link:)?loc\b[^>]*xlink:href=\"[^\"#]*#([^\"]+)\"[^>]*xlink:label=\"([^\"]+)\"", re.I)
_LOC2 = re.compile(r"<(?:link:)?loc\b[^>]*xlink:label=\"([^\"]+)\"[^>]*xlink:href=\"[^\"#]*#([^\"]+)\"", re.I)
_ARC = re.compile(r"<(?:link:)?labelArc\b[^>]*xlink:from=\"([^\"]+)\"[^>]*xlink:to=\"([^\"]+)\"", re.I)
_ARC2 = re.compile(r"<(?:link:)?labelArc\b[^>]*xlink:to=\"([^\"]+)\"[^>]*xlink:from=\"([^\"]+)\"", re.I)
_LAB = re.compile(r"<(?:link:)?label\b([^>]*)>(.*?)</(?:link:)?label>", re.I | re.S)


def parse_labels(xml):
    """Element id (prefix_Name) -> preferred label text, from a label linkbase."""
    loc = {lab: href for href, lab in _LOC.findall(xml)}
    loc.update({lab: href for lab, href in _LOC2.findall(xml)})
    arcs = _ARC.findall(xml) + [(f, t) for t, f in _ARC2.findall(xml)]
    labels = defaultdict(dict)
    by_res = {}
    for attrs, text in _LAB.findall(xml):
        a = {k.lower(): v for k, v in _ATTR.findall(attrs)}
        by_res.setdefault(a.get("xlink:label"), []).append((a.get("xlink:role", ""), htmllib.unescape(_TAG.sub("", text)).strip()))
    for frm, to in arcs:
        el = loc.get(frm)
        for role, text in by_res.get(to, []):
            if el:
                labels[el][role.rsplit("/", 1)[-1]] = text
    out = {}
    for el, roles in labels.items():
        t = roles.get("terseLabel") or roles.get("label") or next(iter(roles.values()))
        t = re.sub(r"\s*\[Member\]\s*$", "", t)
        out[el.replace("_", ":", 1)] = t
    return out


def label_linkbase(cik, accession, primary_doc):
    """Element labels for a filing: MetaLinks.json (present for inline XBRL filings), else the label linkbase."""
    folder = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}/"
    out = {}
    try:
        meta = json.loads(sec_get(folder + "MetaLinks.json", timeout=60))
        for inst in meta.get("instance", {}).values():
            for el, v in inst.get("tag", {}).items():
                roles = ((v.get("lang") or {}).get("en-us") or {}).get("role") or {}
                t = roles.get("terseLabel") or roles.get("label")
                if t:
                    out[el.replace("_", ":", 1)] = re.sub(r"\s*\[(Member|Axis|Domain)\]\s*$", "", htmllib.unescape(t)).strip()
        if out:
            return out
    except Exception:
        pass
    idx = json.loads(sec_get(folder + "index.json", timeout=40))
    names = [i["name"] for i in idx.get("directory", {}).get("item", []) if i["name"].lower().endswith("_lab.xml")]
    if not names:
        return {}
    return parse_labels(sec_get(folder + names[0], timeout=60))


# ---------------------------------------------------------------- model
def _days(s, e):
    return (dt.date.fromisoformat(e) - dt.date.fromisoformat(s)).days


def _kind(s, e):
    if not s:
        return "I"
    d = _days(s, e)
    if 80 <= d <= 100:
        return "Q"
    if 170 <= d <= 192:
        return "H"
    if 260 <= d <= 285:
        return "9M"
    if 350 <= d <= 380:
        return "FY"
    return None


def _axis_info(axis_q):
    local = axis_q.split(":")[-1]
    if local in AXES:
        return AXES[local]
    prefix = axis_q.split(":")[0]
    if prefix not in ("us-gaap", "srt", "dei") and EXT_AXIS_HINT.search(local):
        return ("custom", humanize(local.replace("Axis", "")))
    return None


def _family(name):
    local, prefix = name.split(":")[-1], name.split(":")[0]
    for fid, label, unit, concepts in FAMILIES:
        if local in concepts:
            return fid, label
    if prefix not in ("us-gaap", "srt", "dei") and EXT_METRIC.search(local):
        return "x_" + local, humanize(local)
    if prefix not in ("us-gaap", "srt", "dei") and re.search(r"Revenue|Sales", local) and not re.search(r"Percent|Ratio|Growth|Deferred|Receivable|Unearned|Backlog|Remaining", local):
        return "x_" + local, humanize(local)
    return None


def collect(filings_parsed):
    """filings_parsed: list of (filing meta, contexts, facts) newest first.
    Returns observations keyed by (family, concept, axis, member path, start, end) -> value with anchor.
    The newest filing that reports a period wins, so recast segment histories stay consistent."""
    obs, totals = {}, {}
    for f, ctx, facts in filings_parsed:
        for x in facts:
            fam = _family(x["name"])
            if not fam or x["unit"] not in ("usd", "u_usd", "iso4217_usd", "iso4217:usd") and "usd" not in x["unit"]:
                continue
            c = ctx[x["ctx"]]
            if c["typed"] or not c["end"]:
                continue
            k = _kind(c["start"], c["end"])
            if k is None:
                continue
            anchor = {"accn": f["a"], "form": f["f"], "filed": f["d"], "id": x["id"], "name": x["name"], "ctx": x["ctx"]}
            dims = c["dims"]
            base = (fam[0], x["name"], c["start"], c["end"])
            if not dims:
                totals.setdefault(base, {"v": x["v"], **anchor})
                continue
            cons = next((m for a, m in dims.items() if a.split(":")[-1] == "ConsolidationItemsAxis"), None)
            rest = {a: m for a, m in dims.items() if a.split(":")[-1] != "ConsolidationItemsAxis"}
            if cons and cons.split(":")[-1] != "OperatingSegmentsMember" and not rest:
                # corporate / eliminations lines belong to the segment table
                path = (("segment", "Business segments", "StatementBusinessSegmentsAxis", cons),)
            else:
                axes = []
                for axis_q, m in rest.items():
                    info = _axis_info(axis_q)
                    if not info:
                        axes = None
                        break
                    axes.append((info[0], info[1], axis_q.split(":")[-1], m))
                if not axes or len(axes) > 2:
                    continue
                axes.sort(key=lambda t: AXIS_ORDER.index(t[0]))
                path = tuple(axes)
            key = base + (path,)
            if key not in obs:
                obs[key] = {"v": x["v"], **anchor, "fam_label": fam[1]}
    return obs, totals


def is_recon(member):
    local = member.split(":")[-1]
    return local in RECON_MEMBERS or bool(re.search(r"Reconciling|Elimination|CorporateNonSegment|Unallocated|CorporateAndOther|CorporateReconciling", local))


def _norm(label):
    return re.sub(r"[^a-z0-9]+", "", (label or "").lower().replace("&", "and"))


def build_tables(obs, totals, fye, labels, fy_label):
    """Groups observations into tables (family, concept, primary axis), applies the latest presentation for each
    period, merges renamed members, nests subtotals, splits alternative breakdowns and marks memo lines."""
    groups = defaultdict(list)
    for (fam, concept, start, end, path), rec in obs.items():
        groups[(fam, concept, path[0][2])].append((start, end, path, rec))
    tables = []
    for (fam, concept, axis), items in groups.items():
        cat_key, cat_label = items[0][2][0][0], items[0][2][0][1]
        fam_label = items[0][3]["fam_label"]
        # 1. latest presentation wins per period: when a later filing recasts segments, older members drop out
        newest = {}
        for start, end, path, rec in items:
            k = (start, end)
            if k not in newest or (rec["filed"], rec["accn"]) > newest[k]:
                newest[k] = (rec["filed"], rec["accn"])
        items = [it for it in items if (it[3]["filed"], it[3]["accn"]) == newest[(it[0], it[1])]]
        if cat_key == "segment" and all(is_recon(p[0][3]) for _, _, p, _ in items):
            continue
        # 2. rows, merging members that carry the same label (renamed element, same line)
        rows, alias = {}, {}
        for start, end, path, rec in sorted(items, key=lambda it: it[3]["filed"], reverse=True):
            top = path[0][3]
            top_label = RECON_MEMBERS.get(top.split(":")[-1]) or ("Reconciling items" if is_recon(top) else member_label(top, labels))
            tkey = alias.setdefault(top, alias.get("label:" + _norm(top_label)) or top)
            alias.setdefault("label:" + _norm(top_label), tkey)
            if tkey not in rows:
                rows[tkey] = {"key": tkey, "label": top_label, "member": top, "parent": None, "axis2": None, "recon": is_recon(top), "obs": {}}
            if len(path) == 1:
                rows[tkey]["obs"].setdefault((start, end), rec)
                continue
            child = path[1][3]
            clabel = member_label(child, labels)
            ckey = f"{tkey}|{_norm(clabel)}"
            if ckey not in rows:
                rows[ckey] = {"key": ckey, "label": clabel, "member": child, "parent": tkey, "axis2": path[1][1], "recon": False, "obs": {}}
            rows[ckey]["obs"].setdefault((start, end), rec)
        for r in rows.values():
            if not r["obs"] and not r["parent"]:
                r["implied"] = True
        series = {rk: _quarterize(r["obs"], fye, fy_label) for rk, r in rows.items()}
        tot = {(s, e): rec for (f2, c2, s, e), rec in totals.items() if f2 == fam and c2 == concept}
        total_series = _quarterize(tot, fye, fy_label)
        _nest(rows, series)
        _drop_total_alias(rows, series, total_series)
        _synth_parents(rows, series)
        for part_rows, suffix in _partitions(rows, series, total_series):
            t = _finish_table(fam, concept, axis, cat_key, cat_label, fam_label, part_rows, series, total_series)
            if t:
                if suffix:
                    t["id"] += ":" + suffix
                    t["view"] = suffix
                tables.append(t)
    return tables


def _vals(ser):
    """Flat {(kind, end): value} for a row series."""
    return {(k, e): c["v"] for k in ("A", "Q", "I") for e, c in ser[k].items()}


def _close(a, b):
    return abs(a - b) <= max(1.5e6, abs(b) * 0.002)


def _subset_for(target, cands, min_size=2):
    """cands: [(key, value)]. Returns keys whose values sum to target (within rounding), smallest subset first."""
    sums = {0: ()}
    for key, v in cands:
        nxt = dict(sums)
        for sv, keys in sums.items():
            t = sv + v
            if abs(t) <= abs(target) * 1.003 + 2e6 + sum(abs(x) for _, x in cands if x < 0) and t not in nxt:
                nxt[t] = keys + (key,)
        sums = nxt
        if len(sums) > 250000:
            break
    hits = [keys for sv, keys in sums.items() if len(keys) >= min_size and _close(sv, target)]
    return min(hits, key=len) if hits else None


def _ref_periods(pv, limit=6):
    return sorted(pv, key=lambda k: (k[1], k[0] == "A"), reverse=True)[:limit]


def _nest(rows, series):
    """Finds members that are subtotals of other members on the same axis (for example Data Center = Compute +
    Networking, or Data Center = Hyperscale + AI Clouds in later periods) from the reported numbers and nests the
    parts under the subtotal. A grouping is accepted only if it holds in every period where all parts are reported."""
    changed, guard = True, 0
    while changed and guard < 40:
        changed, guard = False, guard + 1
        tops = [r for r in rows.values() if not r["parent"] and not r["recon"] and not r.get("implied")]
        vals = {r["key"]: _vals(series[r["key"]]) for r in tops}
        tops.sort(key=lambda r: -_latest_abs(series[r["key"]]))
        for p in tops:
            pv = vals[p["key"]]
            for ref in _ref_periods(pv):
                cands = [(r["key"], vals[r["key"]][ref]) for r in tops if r is not p and ref in vals[r["key"]] and abs(vals[r["key"]][ref]) < abs(pv[ref])]
                if len(cands) < 2:
                    continue
                keys = _subset_for(pv[ref], cands[:22])
                if not keys:
                    continue
                checks = [k for k in pv if k != ref and all(k in vals[c] for c in keys)]
                if any(not _close(sum(vals[c][k] for c in keys), pv[k]) for k in checks):
                    continue
                # a one-period coincidence is only trusted when the parts exist in no other period
                if not checks and any(len(vals[c]) > 1 for c in keys) and len(pv) > 1:
                    continue
                for c in keys:
                    rows[c]["parent"] = p["key"]
                changed = True
                break
            if changed:
                break
    # a corporate line that equals the sum of the segments is a mis-tagged subtotal, not a reconciling item
    # removed per period, since filers often tag the segment subtotal with the corporate member in some tables only
    tops = [r for r in rows.values() if not r["parent"] and not r["recon"]]
    tvals = [_vals(series[t["key"]]) for t in tops]
    for r in [r for r in rows.values() if r["recon"]]:
        for (kind, end), v in list(_vals(series[r["key"]]).items()):
            parts = [tv[(kind, end)] for tv in tvals if (kind, end) in tv]
            if len(parts) >= 2 and _close(v, sum(parts)):
                series[r["key"]][kind].pop(end, None)
        if not _vals(series[r["key"]]):
            r["drop"] = True
    for k in [k for k, r in rows.items() if r.get("drop")]:
        rows.pop(k)


def _drop_total_alias(rows, series, total_series):
    """A member that equals the consolidated total in every period (for example "Total products and services") is the
    total itself, not a line of the breakdown."""
    tot = _vals(total_series)
    for k, r in list(rows.items()):
        if r["parent"] or r["recon"]:
            continue
        rv = _vals(series[k])
        common = [p for p in rv if p in tot]
        if len(common) >= 2 and all(_close(rv[p], tot[p]) for p in common):
            for c in [c for c in rows.values() if c["parent"] == k]:
                c["parent"] = None
            rows.pop(k)


def _synth_parents(rows, series):
    """Parents reported only through their components (no fact of their own) get a value equal to the sum of one
    complete group of components, marked as computed and linked to the components."""
    for k, r in rows.items():
        if not r.get("implied"):
            continue
        kids = [c for c in rows.values() if c["parent"] == k]
        if not kids:
            continue
        groups = defaultdict(list)
        for c in kids:
            groups[c.get("axis2")].append(c)
        grp = max(groups.values(), key=len)
        out = {"A": {}, "Q": {}, "I": {}}
        for kind in ("A", "Q", "I"):
            ends = set.intersection(*[set(series[c["key"]][kind]) for c in grp]) if grp else set()
            for e in ends:
                cells = [series[c["key"]][kind][e] for c in grp]
                first = cells[0]
                out[kind][e] = {**first, "v": sum(x["v"] for x in cells), "synth": True, "components": [c["key"] for c in grp], "derived": None}
        series[k] = out
        r["implied"] = False
        r["synth"] = True


def _partitions(rows, series, total_series):
    """When the top-level lines add up to more than the total, the axis carries more than one breakdown (for example
    product type and product line on the same axis). Finds the subset that sums to the total; if the remainder also
    sums to the total it becomes a second table, otherwise the remainder is marked as memo lines (included above)."""
    tot = _vals(total_series)
    tops = [r for r in rows.values() if not r["parent"] and not r.get("implied")]
    if not tot or len(tops) < 3:
        return [(rows, None)]
    vals = {r["key"]: _vals(series[r["key"]]) for r in tops}

    def sums_ok(keys, k):
        return all(k in vals[c] for c in keys) and _close(sum(vals[c][k] for c in keys), tot[k])

    over = [k for k in tot if sum(1 for r in tops if k in vals[r["key"]]) >= 3 and not sums_ok([r["key"] for r in tops if k in vals[r["key"]]], k)]
    if not over:
        return [(rows, None)]
    ref = max(over, key=lambda k: (sum(1 for r in tops if k in vals[r["key"]]), k[1]))
    present = [r["key"] for r in tops if ref in vals[r["key"]]]
    best = None
    cands = [(c, vals[c][ref]) for c in present]
    # prefer the most detailed partition: try large subsets first by excluding few members
    keys = _subset_for(tot[ref], sorted(cands, key=lambda kv: -abs(kv[1]))[:22])
    if keys:
        alt = [c for c in present if c not in keys]
        checks = [k for k in over if k != ref and all(k in vals[c] for c in keys)]
        if all(sums_ok(keys, k) for k in checks):
            best = (list(keys), alt)
    if not best:
        return [(rows, None)]
    main, alt = best
    alt_sums = len(alt) >= 2 and all(_close(sum(vals[c][k] for c in alt), tot[k]) for k in [ref])
    if alt_sums:
        # larger set of lines is the detailed view; the other is the summary view
        detail, summary = (main, alt) if len(main) >= len(alt) else (alt, main)
        def subset(keys):
            keep = set(keys)
            for r in rows.values():
                if r["parent"] in keep:
                    keep.add(r["key"])
            keep |= {r["key"] for r in rows.values() if r["recon"] and not r["parent"]}
            return {k: rows[k] for k in keep if k in rows}
        return [(subset(detail), None), (subset(summary), "summary")]
    for c in alt:
        rows[c]["memo"] = True
    return [(rows, None)]


def _quarterize(obs, fye, fy_label):
    """obs: (start, end) -> rec. Returns {"A": {end: cell}, "Q": {end: cell}, "I": {end: cell}}."""
    out = {"A": {}, "Q": {}, "I": {}}
    by_kind = defaultdict(dict)
    for (s, e), rec in obs.items():
        k = _kind(s, e)
        by_kind[k][(s, e)] = rec
    for (s, e), rec in by_kind.get("I", {}).items():
        out["I"][e] = _cell(rec, e, None, fy_label(fye, e, "Q"))
    for (s, e), rec in by_kind.get("FY", {}).items():
        out["A"][e] = _cell(rec, e, s, fy_label(fye, e, "A"))
    for (s, e), rec in by_kind.get("Q", {}).items():
        out["Q"][e] = _cell(rec, e, s, fy_label(fye, e, "Q"))
    ytd = {**{k: ("H", v) for k, v in by_kind.get("H", {}).items()}, **{k: ("9M", v) for k, v in by_kind.get("9M", {}).items()},
           **{k: ("FY", v) for k, v in by_kind.get("FY", {}).items()}}
    # derive missing quarters from cumulative facts sharing a fiscal-year start
    for (s, e), (kind, rec) in sorted(ytd.items(), key=lambda kv: kv[0][1]):
        if e in out["Q"]:
            continue
        prior_end, prior = None, None
        for (s2, e2), (k2, r2) in ytd.items():
            if s2 == s and e2 < e and (prior_end is None or e2 > prior_end):
                prior_end, prior = e2, (k2, r2, s2)
        q1 = None
        if prior is None:
            continue
        expect = {"H": 90, "9M": 90, "FY": 90}[kind]
        gap = _days(prior_end, e)
        if not (75 <= gap <= 105):
            continue
        v = rec["v"] - prior[1]["v"]
        start_q = (dt.date.fromisoformat(prior_end) + dt.timedelta(days=1)).isoformat()
        cell = _cell(rec, e, start_q, fy_label(fye, e, "Q"))
        cell["v"] = v
        cell["derived"] = {"from": {"v": rec["v"], "start": s, "end": e, "accn": rec["accn"], "id": rec["id"], "name": rec["name"], "ctx": rec["ctx"]},
                           "minus": {"v": prior[1]["v"], "start": s, "end": prior_end, "accn": prior[1]["accn"], "id": prior[1]["id"], "name": prior[1]["name"], "ctx": prior[1]["ctx"]}}
        out["Q"][e] = cell
    return out


def _cell(rec, end, start, label):
    return {"v": rec["v"], "end": end, "start": start, "p": label, "accn": rec["accn"], "form": rec["form"], "filed": rec["filed"],
            "id": rec["id"], "name": rec["name"], "ctx": rec["ctx"]}


def _finish_table(fam, concept, axis, cat_key, cat_label, fam_label, rows, series, total_series):
    all_tops = [r for r in rows.values() if not r["parent"]]
    tops = [r for r in all_tops if not r.get("memo")]
    if len([r for r in tops if not r["recon"]]) < 2 and not any(r["parent"] for r in rows.values()):
        return None
    out_rows = []
    order = sorted(all_tops, key=lambda r: (bool(r.get("memo")), r["recon"], -_latest_abs(series[r["key"]])))
    for r in order:
        out_rows.append(_row_out(r, series[r["key"]], 0))
        kids = sorted([c for c in rows.values() if c["parent"] == r["key"]], key=lambda c: -_latest_abs(series[c["key"]]))
        for c in kids:
            out_rows.append(_row_out(c, series[c["key"]], 1))
    recon = {}
    for kind in ("A", "Q", "I"):
        for end, tcell in total_series[kind].items():
            vals = [series[r["key"]][kind].get(end) for r in tops]
            present = [v for v in vals if v]
            if len(present) < 2:
                continue
            s = sum(v["v"] for v in present)
            diff = tcell["v"] - s
            ok = abs(diff) <= max(1.0, abs(tcell["v"]) * 0.005)
            recon[f"{kind}:{end}"] = {"total": tcell["v"], "sum": s, "diff": diff, "ok": ok, "n": len(present), "of": len(tops),
                                      "partial": sum(1 for r, v in zip(tops, vals) if v and not r["recon"]) < sum(1 for r in tops if not r["recon"])}
        # second level: children sum to their parent
    child_recon = {}
    for r in tops:
        kids = [c for c in rows.values() if c["parent"] == r["key"]]
        if len(kids) < 2:
            continue
        for kind in ("A", "Q"):
            for end, pc in series[r["key"]][kind].items():
                present = [series[c["key"]][kind].get(end) for c in kids]
                present = [v for v in present if v]
                if len(present) < 2:
                    continue
                s = sum(v["v"] for v in present)
                child_recon[f"{r['key']}@{kind}:{end}"] = {"total": pc["v"], "sum": s, "diff": pc["v"] - s, "ok": abs(pc["v"] - s) <= max(1.0, abs(pc["v"]) * 0.005)}
    total = {"label": f"Total {fam_label.lower()}", "A": total_series["A"], "Q": total_series["Q"], "I": total_series["I"]}
    return {"id": f"{fam}:{concept.split(':')[-1]}:{axis}", "family": fam, "metric": fam_label, "concept": concept, "axis": axis,
            "category": cat_key, "category_label": cat_label, "rows": out_rows, "total": total, "recon": recon, "child_recon": child_recon}


def _latest_abs(s):
    for kind in ("A", "Q", "I"):
        if s[kind]:
            return abs(s[kind][max(s[kind])]["v"])
    return 0


def _row_out(r, s, depth):
    return {"key": r["key"], "label": r["label"], "member": r["member"], "parent": r["parent"], "depth": depth, "recon": r["recon"],
            "implied": r.get("implied", False), "memo": bool(r.get("memo")), "synth": bool(r.get("synth")), "A": s["A"], "Q": s["Q"], "I": s["I"]}


def add_residuals(tables):
    """Adds an explicit residual row where reported members do not sum to the consolidated total, so every
    table adds up. The residual is labelled as derived and links to the total's source."""
    for t in tables:
        res = {"A": {}, "Q": {}, "I": {}}
        for k, rc in t["recon"].items():
            if rc["ok"] or rc.get("partial"):
                continue
            kind, end = k.split(":", 1)
            tc = t["total"][kind][end]
            res[kind][end] = {**tc, "v": rc["diff"], "residual": True}
            rc["residual"] = rc["diff"]
        if any(res.values()):
            t["rows"].append({"key": "__residual__", "label": "Other, unallocated and eliminations (derived)", "member": None, "parent": None, "depth": 0,
                              "recon": True, "implied": False, "residual": True, **res})
    return tables


def pick_tables(tables):
    """Keeps the most useful concept per (family, axis) and orders the result for display."""
    best = {}
    for t in tables:
        k = (t["family"], t["axis"], t.get("view"))
        score = sum(len(r["A"]) + len(r["Q"]) for r in t["rows"]) + 5 * sum(1 for rc in t["recon"].values() if rc["ok"])
        if k not in best or score > best[k][0]:
            best[k] = (score, t)
    fam_order = [f[0] for f in FAMILIES]
    out = [t for _, t in best.values()]
    out.sort(key=lambda t: (AXIS_ORDER.index(t["category"]), fam_order.index(t["family"]) if t["family"] in fam_order else 50, t["metric"]))
    return out


ISO = {"US": "United States", "CN": "China", "TW": "Taiwan", "HK": "Hong Kong", "JP": "Japan", "KR": "South Korea", "SG": "Singapore", "IN": "India",
       "GB": "United Kingdom", "DE": "Germany", "FR": "France", "IE": "Ireland", "NL": "Netherlands", "CH": "Switzerland", "CA": "Canada", "MX": "Mexico",
       "BR": "Brazil", "AU": "Australia", "IL": "Israel", "IT": "Italy", "ES": "Spain", "SE": "Sweden", "BE": "Belgium", "DK": "Denmark", "NO": "Norway",
       "FI": "Finland", "AT": "Austria", "PL": "Poland", "MY": "Malaysia", "TH": "Thailand", "VN": "Vietnam", "PH": "Philippines", "ID": "Indonesia",
       "AE": "United Arab Emirates", "SA": "Saudi Arabia", "ZA": "South Africa", "AR": "Argentina", "CL": "Chile", "CO": "Colombia", "PE": "Peru",
       "PR": "Puerto Rico", "NZ": "New Zealand", "LU": "Luxembourg", "BM": "Bermuda", "KY": "Cayman Islands", "RU": "Russia", "TR": "Turkey", "EG": "Egypt"}


def member_label(m, labels):
    if m in labels:
        t = labels[m]
        return t.title() if t.isupper() and len(t) > 3 else t
    local = m.split(":")[-1]
    if m.split(":")[0] in ("country", "srt") and local in ISO:
        return ISO[local]
    if local.endswith("Member") and local[:-6] in ISO:
        return ISO[local[:-6]]
    return humanize(m)
