"""SEC EDGAR: company profile, filing index and XBRL statements with filing-level attribution."""
import datetime as dt

from .common import sec_json

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
TABLE_YEARS = 6  # statement tables kept per filing for the source panel


def days(a, b):
    return (dt.date.fromisoformat(b) - dt.date.fromisoformat(a)).days


def submissions(cik):
    return sec_json(f"https://data.sec.gov/submissions/CIK{cik:010d}.json")


def companyfacts(cik):
    return sec_json(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json")


def pick_concept(facts, names):
    merged, seen, unit, first, latest = [], set(), None, None, ""
    for n in names:
        f = facts.get("us-gaap", {}).get(n)
        if not f:
            continue
        units = f["units"]
        u = "USD" if "USD" in units else ("USD/shares" if "USD/shares" in units else ("shares" if "shares" in units else list(units)[0]))
        if unit is None:
            unit = u
        if u != unit or u not in units:
            continue
        if first is None:
            first = n
        for r in units[u]:
            k = (r.get("start"), r["end"], r["accn"])
            if k in seen:
                continue
            seen.add(k)
            rr = dict(r)
            rr["c"] = n
            merged.append(rr)
            latest = max(latest, r["end"])
    if first is None:
        return None
    return first, unit, merged, latest


def build_series(facts, stmt):
    out = []
    for label, names in CONCEPTS[stmt]:
        pc = pick_concept(facts, names)
        if not pc:
            continue
        concept, unit, rows, _ = pc
        rows = [r for r in rows if r.get("form") in ("10-K", "10-Q", "10-K/A", "10-Q/A", "20-F", "10-KT")]
        annual, quarterly, ytd = {}, {}, {}
        for r in rows:
            end = r["end"]
            rec = {"v": r["val"], "end": end, "accn": r["accn"], "form": r["form"], "filed": r["filed"], "c": r["c"]}
            if stmt in INSTANT:
                if "start" in r:
                    continue
                if r.get("fp") == "FY" and r["form"].startswith("10-K"):
                    if end not in annual or r["filed"] < annual[end]["filed"]:
                        annual[end] = rec
                if end not in quarterly or r["filed"] < quarterly[end]["filed"]:
                    quarterly[end] = rec
                continue
            if "start" not in r:
                continue
            rec["start"] = r["start"]
            d = days(r["start"], end)
            key = (r["start"], end)
            if 350 <= d <= 380:
                if key not in annual or r["filed"] < annual[key]["filed"]:
                    annual[key] = rec
            elif 80 <= d <= 120:
                if key not in quarterly or r["filed"] < quarterly[key]["filed"]:
                    quarterly[key] = rec
            elif 160 <= d <= 300:
                if key not in ytd or r["filed"] < ytd[key]["filed"]:
                    ytd[key] = rec
        A, Q = {}, {}
        for rec in annual.values():
            if rec["end"] not in A or rec["filed"] < A[rec["end"]]["filed"]:
                A[rec["end"]] = rec
        for rec in quarterly.values():
            if rec["end"] not in Q or rec["filed"] < Q[rec["end"]]["filed"]:
                Q[rec["end"]] = rec
        if stmt not in INSTANT:
            allper = {}
            for rec in list(Q.values()) + list(ytd.values()) + list(A.values()):
                allper[(rec["start"], rec["end"])] = rec
            by_start = {}
            for rec in allper.values():
                by_start.setdefault(rec["start"], []).append(rec)
            for recs in by_start.values():
                recs.sort(key=lambda x: x["end"])
                for i in range(1, len(recs)):
                    big, small = recs[i], recs[i - 1]
                    if 80 <= days(small["end"], big["end"]) <= 120 and big["end"] not in Q:
                        Q[big["end"]] = {
                            "v": big["v"] - small["v"], "start": small["end"], "end": big["end"],
                            "accn": big["accn"], "form": big["form"], "filed": big["filed"], "c": big["c"],
                            "derived": {"minus": {"v": small["v"], "accn": small["accn"], "form": small["form"], "start": small["start"], "end": small["end"]},
                                        "from": {"v": big["v"], "start": big["start"], "end": big["end"]}},
                        }
        out.append({"label": label, "concept": concept, "unit": unit,
                    "annual": sorted(A.values(), key=lambda x: x["end"]),
                    "quarterly": sorted(Q.values(), key=lambda x: x["end"])})
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
                vals, rc = {}, concept
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


def profile(sub, facts):
    dei = facts.get("dei", {})
    so = dei.get("EntityCommonStockSharesOutstanding", {}).get("units", {}).get("shares", [])
    shares = None
    if so:
        latest_end = max(r["end"] for r in so)
        shares = {"val": sum(r["val"] for r in so if r["end"] == latest_end and r.get("form") in ("10-K", "10-Q")) or max(so, key=lambda r: r["end"])["val"], "end": latest_end}
    ba = sub.get("addresses", {}).get("business", {})
    return {
        "name": sub["name"], "sic": sub.get("sic"), "sicDescription": sub.get("sicDescription"), "exchanges": sub.get("exchanges") or [],
        "fiscalYearEnd": sub.get("fiscalYearEnd") or "1231", "stateOfIncorporation": sub.get("stateOfIncorporation"), "phone": sub.get("phone"),
        "website": sub.get("website") or "", "address": {k: ba.get(k) for k in ("street1", "street2", "city", "stateOrCountry", "zipCode")}, "sharesOutstanding": shares,
    }
