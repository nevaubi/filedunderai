"""SEC EDGAR: company profile, filing index and XBRL statements with filing-level attribution."""
import datetime as dt
from fractions import Fraction
import json

from .common import sec_json
from .fundamentals import norm_fye

CONCEPTS = {
    "income": [
        ("Revenue", ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet", "RevenuesNetOfInterestExpense", "RevenueFromContractWithCustomerIncludingAssessedTax"]),
        ("Cost of revenue", ["CostOfGoodsAndServicesSold", "CostOfRevenue", "CostOfGoodsSold", "CostOfGoodsAndServiceExcludingDepreciationDepletionAndAmortization"]),
        ("Gross profit", ["GrossProfit"]),
        ("Research & development", ["ResearchAndDevelopmentExpense", "ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost"]),
        ("SG&A", ["SellingGeneralAndAdministrativeExpense", "GeneralAndAdministrativeExpense"]),
        ("Operating expenses", ["OperatingExpenses", "CostsAndExpenses", "NoninterestExpense"]),
        ("Operating income", ["OperatingIncomeLoss"]),
        ("Net interest income", ["InterestIncomeExpenseNet", "InterestIncomeExpenseAfterProvisionForLoanLoss"]),
        ("Other income (expense)", ["NonoperatingIncomeExpense", "OtherNonoperatingIncomeExpense"]),
        ("Pretax income", ["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest", "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments", "IncomeLossFromContinuingOperationsBeforeIncomeTaxesDomestic"]),
        ("Income tax", ["IncomeTaxExpenseBenefit"]),
        ("Net income", ["NetIncomeLoss", "ProfitLoss", "NetIncomeLossAvailableToCommonStockholdersBasic"]),
        ("EPS basic", ["EarningsPerShareBasic"]),
        ("EPS diluted", ["EarningsPerShareDiluted", "EarningsPerShareBasicAndDiluted"]),
        ("Diluted shares", ["WeightedAverageNumberOfDilutedSharesOutstanding"]),
    ],
    "balance": [
        ("Cash & equivalents", ["CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents", "Cash"]),
        ("Marketable securities, current", ["MarketableSecuritiesCurrent", "AvailableForSaleSecuritiesDebtSecuritiesCurrent", "ShortTermInvestments"]),
        ("Accounts receivable", ["AccountsReceivableNetCurrent", "ReceivablesNetCurrent"]),
        ("Inventory", ["InventoryNet"]),
        ("Total current assets", ["AssetsCurrent"]),
        ("PP&E, net", ["PropertyPlantAndEquipmentNet", "PropertyPlantAndEquipmentAndFinanceLeaseRightOfUseAssetAfterAccumulatedDepreciationAndAmortization"]),
        ("Goodwill", ["Goodwill"]),
        ("Total assets", ["Assets"]),
        ("Accounts payable", ["AccountsPayableCurrent"]),
        ("Total current liabilities", ["LiabilitiesCurrent"]),
        ("Long-term debt", ["LongTermDebtNoncurrent", "LongTermDebt", "LongTermDebtAndCapitalLeaseObligations"]),
        ("Deposits", ["Deposits"]),
        ("Total liabilities", ["Liabilities"]),
        ("Shareholders' equity", ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"]),
        ("Liabilities & equity", ["LiabilitiesAndStockholdersEquity"]),
    ],
    "cashflow": [
        ("Net income", ["NetIncomeLoss", "ProfitLoss"]),
        ("D&A", ["DepreciationDepletionAndAmortization", "DepreciationAmortizationAndAccretionNet", "DepreciationAndAmortization", "DepreciationAmortizationAndOther", "Depreciation"]),
        ("Stock-based comp", ["ShareBasedCompensation", "AllocatedShareBasedCompensationExpense"]),
        ("Operating cash flow", ["NetCashProvidedByUsedInOperatingActivities", "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"]),
        ("Capex", ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets", "PaymentsToAcquireOtherPropertyPlantAndEquipment"]),
        ("Investing cash flow", ["NetCashProvidedByUsedInInvestingActivities", "NetCashProvidedByUsedInInvestingActivitiesContinuingOperations"]),
        ("Buybacks", ["PaymentsForRepurchaseOfCommonStock"]),
        ("Dividends paid", ["PaymentsOfDividends", "PaymentsOfDividendsCommonStock"]),
        ("Financing cash flow", ["NetCashProvidedByUsedInFinancingActivities", "NetCashProvidedByUsedInFinancingActivitiesContinuingOperations"]),
    ],
}
INSTANT = {"balance"}
# Weighted-average share counts are averages over the period, not flows: a quarter cannot be
# derived by subtracting a shorter year-to-date figure from a longer one.
NON_ADDITIVE = {"Diluted shares", "EPS basic", "EPS diluted"}
TABLE_YEARS = 6  # statement tables kept per filing for the source panel


def days(a, b):
    return (dt.date.fromisoformat(b) - dt.date.fromisoformat(a)).days


def submissions(cik):
    return sec_json(f"https://data.sec.gov/submissions/CIK{cik:010d}.json")


def companyfacts(cik):
    return sec_json(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json")


def pick_concept(facts, names):
    """Keep concept identity until accounting-aware selection; aliases are not interchangeable facts."""
    merged, seen, unit, first, latest = [], set(), None, None, ""
    for n in names:
        units = facts.get("us-gaap", {}).get(n, {}).get("units") or {}
        if not units:
            continue
        u = next((u for u in ("USD", "USD/shares", "shares") if u in units), next(iter(units)))
        if unit is None:
            unit, first = u, n
        if u != unit:
            continue
        for r in units[u]:
            k = (n, r.get("start"), r["end"], r["accn"], r.get("filed"), r["val"])
            if k not in seen:
                seen.add(k)
                merged.append(dict(r, c=n))
                latest = max(latest, r["end"])
    return (first, unit, merged, latest) if first else None


# Issuers whose consolidated "Revenues" total is reported beside a smaller ASC 606 contract-revenue component
# in the same filing context, where the component omits a primary revenue stream (self-mining, commodity
# merchandising, purchased-receivable collections, lending, collaboration/royalty income). Reviewed per CIK;
# for these the reported total is preferred whenever both are reported for the identical start/end/accession.
REPORTED_TOTAL_REVENUE_CIKS = {
    1507605: "MARA: bitcoin mining revenue is outside contract revenue",
    821026: "ANDE: commodity merchandising sales are outside contract revenue",
    1084961: "ECPG: portfolio collections revenue is outside contract revenue",
    1362468: "ALGT: total operating revenue exceeds the contract component",
    1845815: "PAYO: interest income on customer balances is part of total revenues",
    1724521: "RCUS: collaboration and license revenue is part of total revenues",
    1662991: "SEZL: interest and fee income on receivables is part of total revenues",
    873303: "SRPT: collaboration and royalty revenue is part of total revenues",
    1497770: "WD: servicing and loan income is part of total revenues",
    # Existing issuers found by scanning every tracked issuer's latest 10-K (2026-11-06); each total checked
    # against the issuer's reported consolidated revenue. RSG, HAS and COP were left unchanged (their larger
    # "Revenues" contexts do not read as plain consolidated revenue).
    314808: "VAL: reimbursable and other revenues are part of total operating revenues",
    1051470: "CCI: site rental revenues are lease income outside contract revenue",
    39263: "CFR: net interest income is part of total revenue",
    927628: "COF: net interest income is part of total net revenue",
    1592386: "VIRT: trading income is part of total revenues",
    1067701: "URI: equipment rental revenue is lease income outside contract revenue",
    1996862: "BG: commodity sales are outside contract revenue",
    7084: "ADM: commodity merchandising revenue is outside contract revenue",
    840489: "FCFS: pawn loan fees are outside contract revenue",
    1309108: "WEX: finance fee revenue is part of total revenues",
    1609253: "CRC: oil, gas and NGL sales are part of total operating revenues",
    1057706: "FBP: net interest income is part of total revenue",
    793952: "HOG: financial services revenue is part of consolidated revenue",
    1094831: "BGC: principal transactions and interest income are part of total revenues",
    1567094: "CNH: financial services revenue is part of total revenues",
    1868275: "CEG: derivative and other revenues are part of operating revenues",
    1071739: "CNC: premium revenue is outside contract revenue",
    1674168: "HGV: financing and rental revenues are part of total revenues",
    91440: "SNA: financial services revenue is part of total revenues",
    1591698: "PCTY: interest income on funds held for clients is part of total revenues",
    1759509: "LYFT: rental revenue is part of total revenue",
    73756: "OII: lease revenue is part of total revenue",
}


def _revenue_rank(facts, r, names, total_scope=False):
    """Explicit accounting scopes, never largest-value selection.

    ASC 606 contract revenue excludes leases and financial instruments. A reported
    total is preferred when the same filing/context also reports those activities.
    """
    def carries(concepts):
        for c in concepts:
            for x in facts.get("us-gaap", {}).get(c, {}).get("units", {}).get("USD", []):
                if (x.get("start"), x["end"], x["accn"]) == (r.get("start"), r["end"], r["accn"]):
                    return True
        return False
    c = r["c"]
    if c == "RevenuesNetOfInterestExpense":
        return -3, "bank revenue net of interest expense; contract revenue is a component"
    leases = ("OperatingLeaseLeaseIncome", "OperatingLeasesIncomeStatementLeaseRevenue", "OperatingLeaseIncome", "LeaseIncome", "SalesTypeAndDirectFinancingLeasesIncomeStatementLeaseRevenue")
    insurance = ("PremiumsEarnedNet", "PremiumsEarnedNetOfReinsurance", "InsuranceServicesRevenue", "Premiums")
    if c == "Revenues" and carries(leases):
        return -2, "reported consolidated revenue includes lease income outside ASC 606"
    if c == "Revenues" and carries(insurance):
        return -2, "reported consolidated insurance revenue includes premiums outside ASC 606"
    if total_scope and c == "Revenues":
        contract = [x["val"] for x in facts.get("us-gaap", {}).get("RevenueFromContractWithCustomerExcludingAssessedTax", {}).get("units", {}).get("USD", [])
                    if (x.get("start"), x["end"], x["accn"]) == (r.get("start"), r["end"], r["accn"])]
        if contract and r["val"] > max(contract):
            return -2, "reviewed issuer: reported consolidated revenue exceeds its ASC 606 contract-revenue component"
    return names.index(c), "reported concept priority within accounting scope"


def _supported_source_periods(point, cutoff=None, active=frozenset()):
    """Invalid source chronology cannot create a new selection disagreement."""
    if not isinstance(point, dict) or id(point) in active or len(active) >= 32:
        return False
    end, filed = point.get('end'), point.get('filed')
    available = point.get('available_at') or filed
    if not end or not filed or end > filed or (cutoff and available > cutoff):
        return False
    derivation = point.get('derived')
    if isinstance(derivation, dict):
        return all(_supported_source_periods(derivation.get(key), available, active | {id(point)})
                   for key in ('from', 'minus'))
    return True


def _same_reported_amount(reported, alternative):
    if reported['v'] == alternative['v']:
        return True
    derivation = alternative.get('derived')
    if not isinstance(derivation, dict):
        return False
    try:
        big, small = derivation['from']['v'], derivation['minus']['v']
        # Recognize only exact decimal agreement whose normalized discrepancy is
        # reproduced by binary float subtraction. This is not a value tolerance
        # and never rewrites a reported amount or a source derivation.
        return (isinstance(alternative['v'], float)
                and (isinstance(big, float) or isinstance(small, float))
                and alternative['v'] == big - small
                and Fraction(str(reported['v'])) == Fraction(str(big)) - Fraction(str(small)))
    except (KeyError, TypeError, ValueError, OverflowError):
        return False


def prefer_reported_observation(selected, options):
    """Preserve the latest direct observation for one already unit-matched series.

    A later cumulative comparative can be restated while the older subtraction
    leg is not. Keep the complete disagreement for review, never a synthesized
    revision. Conflicting starts or same-date direct values remain explicit
    ambiguities even when an issuer has no previous database observation.
    """
    if selected.get('selection_conflict'):
        return selected
    reported = [r for r in options if not r.get("derived") and r["c"] == selected["c"]
                and r["end"] == selected["end"] and r["end"] <= r["filed"]]
    if not reported:
        return selected
    latest_date = max(r.get("available_at") or r["filed"] for r in reported)
    latest = [r for r in reported if (r.get("available_at") or r["filed"]) == latest_date]
    if (any(r.get("start") != selected.get("start") for r in latest)
            or len({r["v"] for r in latest}) != 1):
        return dict(selected, selection_conflict={
            'reason': 'reported_period_or_value_ambiguity',
            'reported_observations': [{k: v for k, v in r.items() if not k.startswith('_')}
                                      for r in sorted(latest, key=lambda r: (r['accn'], r.get('start') or '', r['v']))]})
    preferred = (dict(max(latest, key=lambda r: r["accn"]),
                      selection_reason="Preserved exact reported observation over an equal later derived value")
                 if selected.get('derived') else dict(selected))
    same_date = latest_date == (selected.get('available_at') or selected['filed'])
    candidates = ([r for r in options if r.get('derived') and r['c'] == preferred['c']
                   and r.get('start') == preferred.get('start') and r['end'] == preferred['end']
                   and (r.get('available_at') or r['filed']) == latest_date]
                  if same_date else [selected] if selected.get('derived') else [])
    alternatives = {}
    for candidate in candidates:
        if _supported_source_periods(candidate) and not _same_reported_amount(preferred, candidate):
            clean = {k: v for k, v in candidate.items() if not k.startswith('_')}
            alternatives[json.dumps(clean, sort_keys=True, separators=(',', ':'))] = clean
    alternatives = [alternatives[key] for key in sorted(alternatives)]
    if alternatives:
        preferred['selection_reason'] = 'Preserved latest reported observation; conflicting derivation requires comparability review'
        preferred['selection_conflict'] = {
            'reason': ('same_date_derived_value_differs_from_reported' if same_date
                       else 'later_derived_value_differs_from_reported'),
            'alternative': alternatives[0]}
        if len(alternatives) > 1:
            preferred['selection_conflict']['additional_alternatives'] = alternatives[1:]
    return preferred


def build_series(facts, stmt, vintage="latest", as_of=None, concepts=None, instant=None, total_revenue_scope=False, pinned=None):
    """Normalize reported observations and retain filing histories for point-in-time use.

    as_of is a filing-date cutoff (daily granularity). Cross-filing subtraction is
    allowed only on an unchanged concept with no subsequently conflicting smaller
    cumulative observation. Such inputs are explicitly identified in derivation.basis.
    """
    if vintage not in ("latest", "original"):
        raise ValueError("vintage must be latest or original")
    out = []
    is_instant = stmt in INSTANT if instant is None else instant
    for label, names in (CONCEPTS[stmt] if concepts is None else concepts):
        pc = pick_concept(facts, names)
        if not pc:
            continue
        concept, unit, raw, _ = pc
        annual, quarterly, cumulative = [], [], []
        for r in raw:
            if r.get("form") not in ("10-K", "10-Q", "10-K/A", "10-Q/A", "20-F", "10-KT"):
                continue
            if as_of and r.get("filed", "") > as_of:
                continue
            rank, reason = (_revenue_rank(facts, r, names, total_revenue_scope) if label == "Revenue"
                            else (names.index(r["c"]), "reported concept priority"))
            # pinned: {(label, period_start or None, period_end): concept} already published for that period.
            # While that concept is still reported for the period, it keeps the cell, so a later XBRL vintage
            # that tags an old period under another concept cannot silently change its accounting scope.
            if (pinned and pinned.get((label, r.get("start"), r["end"])) == r["c"]
                    and not (total_revenue_scope and r["c"] == "RevenueFromContractWithCustomerExcludingAssessedTax")):
                rank, reason = -100, "published concept retained for this period"
            rec = {"v": r["val"], "end": r["end"], "accn": r["accn"], "form": r["form"],
                   "filed": r["filed"], "c": r["c"], "available_at": r["filed"],
                   "accessions": [r["accn"]], "vintage": vintage, "validation_status": "reported",
                   "selection_reason": reason, "_rank": rank}
            if is_instant:
                if "start" not in r:
                    quarterly.append(rec)
                    if r.get("fp") == "FY" and r["form"].startswith(("10-K", "20-F")):
                        annual.append(rec)
                continue
            if "start" not in r:
                continue
            rec["start"] = r["start"]
            d = days(r["start"], r["end"])
            if 350 <= d <= 380:
                annual.append(rec); cumulative.append(rec)
            elif 80 <= d <= 120:
                quarterly.append(rec); cumulative.append(rec)
            elif 160 <= d <= 300:
                cumulative.append(rec)
        if not is_instant and label not in NON_ADDITIVE:
            for big in cumulative:
                candidates = [x for x in cumulative if x["start"] == big["start"] and x["c"] == big["c"]
                              and 80 <= days(x["end"], big["end"]) <= 120 and x["filed"] <= big["filed"]]
                same = [x for x in candidates if x["accn"] == big["accn"]]
                if not candidates:
                    continue
                small = max(same or candidates, key=lambda x: (x["filed"], x["end"], x["accn"]))
                # Retain the historical derivation, but invalidate its current basis when
                # only the smaller cumulative leg was subsequently restated.
                conflicting = [x["filed"] for x in cumulative if not same and x["c"] == small["c"]
                               and x["start"] == small["start"] and x["end"] == small["end"]
                               and x["filed"] > big["filed"] and x["v"] != small["v"]]
                def source(x):
                    return {k: v for k, v in x.items() if not k.startswith("_")}
                quarterly.append(dict(big, v=big["v"]-small["v"],
                    start=(dt.date.fromisoformat(small["end"])+dt.timedelta(days=1)).isoformat(),
                    accessions=sorted({big["accn"], small["accn"]}),
                    available_at=max(big["filed"], small["filed"]), validation_status="derived",
                    superseded_at=min(conflicting) if conflicting else None,
                    derived={"minus": source(small), "from": source(big),
                             "basis": "same_filing_same_concept" if same else "sequential_filings_same_concept_no_observed_revision"}))
        def select(records):
            grouped = {}
            for r in records:
                if vintage == "latest" and r.get("superseded_at") and (not as_of or r["superseded_at"] <= as_of):
                    continue
                grouped.setdefault(r["end"], []).append(r)
            chosen = []
            for end, options in grouped.items():
                date = (max if vintage == "latest" else min)(r["available_at"] for r in options)
                pool = [r for r in options if r["available_at"] == date]
                selected = min(pool, key=lambda r: (r["_rank"], bool(r.get("derived")), r["accn"]))
                # A pinned (already published) concept keeps its period when the normal choice is a directly
                # or derived value under a different concept (a later XBRL vintage re-tagging an old period).
                # Same-concept revisions keep the normal path and its review checks.
                pins = [r for r in options if r["_rank"] == -100 and not r.get("derived")]
                if pins and selected["c"] != pins[0]["c"]:
                    pdate = (max if vintage == "latest" else min)(r["available_at"] for r in pins)
                    selected = min((r for r in pins if r["available_at"] == pdate), key=lambda r: r["accn"])
                if vintage == "latest":
                    selected = prefer_reported_observation(selected, options)
                chosen.append(selected)
            return sorted(chosen, key=lambda r:r["end"])
        def clean(records):
            return [{k:v for k,v in r.items() if not k.startswith("_")} for r in records]
        def history(records, selected):
            grouped, same_period_date = {}, {}
            for r in records:
                key = (r["end"], r["accn"], r["available_at"])
                prev = grouped.get(key)
                if not prev or (r["_rank"], bool(r.get("derived"))) < (prev["_rank"], bool(prev.get("derived"))):
                    grouped[key] = r
                scope = (r['c'], r.get('start'), r['end'], r['available_at'])
                same_period_date.setdefault(scope, []).append(r)
            result = clean(sorted(grouped.values(), key=lambda r:(r["end"], r["available_at"], r["accn"])))
            identity = lambda r: (r['end'], r.get('start'), r['accn'], r['filed'], r['c'], r['v'], bool(r.get('derived')))
            observations = list(selected)
            # A newer report can resolve today's choice while a historical
            # as-of date still needs both sides of an older same-date conflict.
            for options in same_period_date.values():
                direct = [r for r in options if not r.get('derived') and _supported_source_periods(r)]
                derived = [r for r in options if r.get('derived') and _supported_source_periods(r)]
                if any(not _same_reported_amount(r, d) for r in direct for d in derived):
                    observations.extend(clean(direct + derived))
            for point in selected:
                conflict = point.get('selection_conflict') or {}
                if conflict.get('alternative'):
                    observations.append(conflict['alternative'])
                    observations.extend(conflict.get('additional_alternatives', []))
            for point in observations:
                historical = dict(point)
                conflict = historical.get('selection_conflict') or {}
                # A later disagreement must not become known at the direct
                # observation's earlier filing date. Same-date ambiguity is
                # already public then and can be retained in its history row.
                if conflict.get('reason') == 'later_derived_value_differs_from_reported':
                    historical.pop('selection_conflict')
                    historical['selection_reason'] = 'reported source observation'
                found = next((i for i, r in enumerate(result) if identity(r) == identity(historical)), None)
                if found is None:
                    result.append(historical)
                elif historical.get('selection_conflict'):
                    result[found] = historical
            return sorted(result, key=lambda r: (r['end'], r['available_at'], r['accn'], bool(r.get('derived'))))
        selected_annual, selected_quarterly = clean(select(annual)), clean(select(quarterly))
        out.append({"label": label, "concept": concept, "unit": unit, "vintage": vintage, "as_of": as_of,
                    "annual": selected_annual, "quarterly": selected_quarterly,
                    "filing_history": {"annual": history(annual, selected_annual), "quarterly": history(quarterly, selected_quarterly)}})
    return out


def filing_list(sub):
    rec = sub["filings"]["recent"]
    out = []
    for i in range(len(rec["form"])):
        out.append({"f": rec["form"][i], "d": rec["filingDate"][i], "r": rec["reportDate"][i], "a": rec["accessionNumber"][i],
                    "p": rec["primaryDocument"][i], "i": rec.get("items", [""] * len(rec["form"]))[i] or "", "x": rec["primaryDocDescription"][i] or ""})
    return out


def filing_url(cik, accn, doc=""):
    return f"https://www.sec.gov/Archives/edgar/data/{cik}/{accn.replace('-', '')}/{doc}"


def filing_tables(facts, statements, filings, cik):
    """Per-filing statement tables rebuilt from that filing's own XBRL facts."""
    cutoff = (dt.date.today() - dt.timedelta(days=365 * TABLE_YEARS)).isoformat()
    used = set()
    for rows in statements.values():
        for row in rows:
            for per in row["annual"] + row["quarterly"]:
                if per["filed"] >= cutoff:
                    used.add(per["accn"])
                    if per.get("derived"):
                        used.add(per["derived"]["minus"]["accn"])
    meta = {f["a"]: f for f in filings}
    picks = {k: [(lab, pick_concept(facts, names)) for lab, names in CONCEPTS[k]] for k in CONCEPTS}
    out = {}
    for acc in used:
        fst = {}
        filed = None
        for k, lst in picks.items():
            cols, rows = {}, []
            for lab, pc in lst:
                if not pc:
                    continue
                concept, unit, rr, _ = pc
                vals, rc, ranks = {}, concept, {}
                for r in rr:
                    if r["accn"] != acc:
                        continue
                    filed = filed or r.get("filed")
                    if k == "balance":
                        if "start" in r:
                            continue
                        col = r["end"]
                    else:
                        if "start" not in r:
                            continue
                        col = r["start"] + "|" + r["end"]
                    rank = _revenue_rank(facts, r, next(names for label,names in CONCEPTS[k] if label == lab))[0] if lab == "Revenue" else next(names for label,names in CONCEPTS[k] if label == lab).index(r["c"])
                    if col in ranks and ranks[col] <= rank:
                        continue
                    ranks[col] = rank
                    cols[col] = 1
                    vals[col] = r["val"]
                    rc = r["c"]
                if vals:
                    rows.append({"label": lab, "concept": rc, "unit": unit, "vals": vals})
            if rows:
                def ck(c):
                    if "|" in c:
                        a, b = c.split("|")
                        return (b, -days(a, b))
                    return (c, 0)
                fst[k] = {"cols": sorted(cols, key=ck, reverse=True)[:8], "rows": rows}
        f = meta.get(acc)
        out[acc] = {"form": f["f"] if f else None, "filed": f["d"] if f else filed, "period": f["r"] if f else None,
                    "primary": filing_url(cik, acc, f["p"]) if f and f["p"] else "", "index": filing_url(cik, acc), "statements": fst}
    return out


def shares_outstanding(facts, as_of=None):
    """An unambiguous consolidated cover-page observation, never sum duplicate filings.

    Companyfacts strips class dimensions; distinct values in one context are ambiguous.
    Explicit class conversions require a separate dimension-aware source.
    """
    so = facts.get("dei", {}).get("EntityCommonStockSharesOutstanding", {}).get("units", {}).get("shares", [])
    rows = [r for r in so if r.get("form") in ("10-K", "10-Q", "10-K/A", "10-Q/A", "10-KT")
            and (not as_of or r.get("filed", "") <= as_of)]
    if not rows:
        return None
    last = max(rows, key=lambda r:(r["end"], r.get("filed", ""), r.get("accn", "")))
    same = [r for r in rows if (r["end"],r.get("accn")) == (last["end"],last.get("accn"))]
    values = {r["val"] for r in same}
    return {"val": next(iter(values)) if len(values)==1 else None, "end": last["end"],
            "accn": last.get("accn"), "filed": last.get("filed"), "available_at": last.get("filed"),
            "form": last.get("form"), "basis": "reported_outstanding" if len(values)==1 else "ambiguous_share_classes"}


def profile(sub, facts):
    shares = shares_outstanding(facts)
    ba = sub.get("addresses", {}).get("business", {})
    return {
        "name": sub["name"], "sic": sub.get("sic"), "sicDescription": sub.get("sicDescription"), "exchanges": sub.get("exchanges") or [],
        "fiscalYearEnd": norm_fye(sub.get("fiscalYearEnd")), "stateOfIncorporation": sub.get("stateOfIncorporation"), "phone": sub.get("phone"),
        "website": sub.get("website") or "", "address": {k: ba.get(k) for k in ("street1", "street2", "city", "stateOrCountry", "zipCode")}, "sharesOutstanding": shares,
    }


PERIODIC = ("10-K", "10-Q", "10-K/A", "10-Q/A", "10-KT")


def latest_filed(facts):
    """Most recent 'filed' date across all companyfacts entries."""
    best = ""
    for ns in facts.values():
        for f in ns.values():
            for rows in f.get("units", {}).values():
                for r in rows:
                    if r.get("filed", "") > best:
                        best = r["filed"]
    return best


def fill_gaps(cik, facts, sub, max_filings=4, log=print):
    """SEC's companyfacts API can lag the filings themselves by weeks or months. Periodic reports (10-Q/10-K) listed in
    the submissions feed but missing from companyfacts are read from their inline XBRL and merged in the same shape,
    so the statements include the latest quarter. Only facts without dimensions are merged. Returns the accessions added."""
    from .common import sec_get
    from .segments import parse_ixbrl
    have = latest_filed(facts)
    known = set()
    for ns in facts.values():
        for f in ns.values():
            for rows in f.get("units", {}).values():
                for r in rows:
                    known.add(r.get("accn"))
    # any recent periodic report whose accession companyfacts does not carry yet (the latest "filed" date in companyfacts
    # can come from an unrelated later filing, so dates alone do not tell what is missing)
    horizon = (dt.date.today() - dt.timedelta(days=420)).isoformat()
    todo = [f for f in filing_list(sub) if f["f"] in PERIODIC and f["d"] >= horizon and f["a"] not in known and f["p"].lower().endswith((".htm", ".html"))]
    added = []
    for f in sorted(todo, key=lambda x: x["d"])[-max_filings:]:
        try:
            html = sec_get(filing_url(cik, f["a"], f["p"]), timeout=120)
        except Exception as e:
            log(f"  gap filler: {f['a']} fetch failed: {str(e)[:120]}")
            continue
        ctx, ixf = parse_ixbrl(html)
        if not ixf:
            continue
        form = f["f"]
        n = 0
        seen = set()
        for x in ixf:
            c = ctx.get(x["ctx"])
            if not c or c["dims"] or c.get("typed") or not c.get("end"):
                continue
            prefix, _, local = x["name"].partition(":")
            if prefix not in ("us-gaap", "dei", "ifrs-full"):
                continue
            ul = (x["unit"] or "").lower()
            unit = "USD/shares" if ("usd" in ul and "share" in ul) else "USD" if "usd" in ul else "shares" if "share" in ul else "pure"
            key = (x["name"], c.get("start"), c["end"], unit)
            if key in seen:
                continue
            seen.add(key)
            row = {"end": c["end"], "val": x["v"], "accn": f["a"], "form": form, "filed": f["d"],
                   "fp": "FY" if form.startswith("10-K") else None, "frame": None, "src": "ixbrl"}
            if c.get("start"):
                row["start"] = c["start"]
            facts.setdefault(prefix, {}).setdefault(local, {"label": local, "units": {}})["units"].setdefault(unit, []).append(row)
            n += 1
        if n:
            added.append(f["a"])
            log(f"  gap filler: merged {n} facts from {form} {f['a']} filed {f['d']}")
    return added
