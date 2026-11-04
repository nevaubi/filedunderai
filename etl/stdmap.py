"""Standardized statement templates and the tag mapping behind them.

Each covered filing gets one template from the SIC code in its SEC submission header (point in time):

| Template   | SIC codes                                                      |
|------------|----------------------------------------------------------------|
| bank       | 6020-6036 banks and thrifts, 6099, 6111, 6141, 6153, 6159, 6172, 6199 credit and card lenders, 6211 broker-dealers |
| insurer    | 6311-6399 insurance carriers, 6411 agents and brokers           |
| reit       | 6798 real estate investment trusts                              |
| utility    | 4900-4991 electric, gas, water and combination utilities        |
| industrial | everything else                                                 |

A template is a list of lines. Each line names the statement it belongs to (IS income statement, BS balance sheet,
CF cash flow) and the US GAAP tags that feed it, in priority order. For a filing, a line takes the first listed tag
that the filer presented on a statement of that kind with a consolidated value (no axis/member, no co-registrant).
A filed line feeds at most one template line. Filed lines that match no template line are listed as not mapped;
they are never summed into a template line, because the data sets carry presentation order but not the calculation
tree. sign -1 flips a value whose tag is reported as a positive outflow (capital expenditures, buybacks, dividends,
treasury stock), so standardized cash flow lines read as inflows positive, outflows negative.

Lines marked "calc" are derived from other template lines in the same filing and period when the filer does not
present them: gross profit = revenue - cost of revenue; free cash flow = operating cash flow + capital expenditures
(the latter already negative).
"""

# (line_id, label, stmt, [tags], sign)
_CASH_FLOW = [
    ("cf_net_income", "Net income (incl. noncontrolling interests)", "CF", ["ProfitLoss", "NetIncomeLoss", "NetIncomeLossAvailableToCommonStockholdersBasic"], 1),
    ("cf_da", "Depreciation and amortization", "CF", ["DepreciationDepletionAndAmortization", "DepreciationAmortizationAndAccretionNet", "DepreciationAndAmortization", "Depreciation", "DepreciationAmortizationAndOther"], 1),
    ("cf_sbc", "Stock-based compensation", "CF", ["ShareBasedCompensation", "AllocatedShareBasedCompensationExpense", "StockIssuedDuringPeriodValueShareBasedCompensation"], 1),
    ("cf_deferred_tax", "Deferred income taxes", "CF", ["DeferredIncomeTaxExpenseBenefit", "DeferredIncomeTaxesAndTaxCredits", "IncreaseDecreaseInDeferredIncomeTaxes"], 1),
    ("cf_chg_receivables", "Change in receivables", "CF", ["IncreaseDecreaseInAccountsReceivable", "IncreaseDecreaseInReceivables", "IncreaseDecreaseInAccountsAndNotesReceivable"], -1),
    ("cf_chg_inventory", "Change in inventories", "CF", ["IncreaseDecreaseInInventories"], -1),
    ("cf_chg_payables", "Change in payables", "CF", ["IncreaseDecreaseInAccountsPayable", "IncreaseDecreaseInAccountsPayableAndAccruedLiabilities", "IncreaseDecreaseInAccountsPayableTrade"], 1),
    ("cf_operating", "Cash from operating activities", "CF", ["NetCashProvidedByUsedInOperatingActivities", "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"], 1),
    ("cf_capex", "Capital expenditures", "CF", ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets", "PaymentsForCapitalImprovements", "PaymentsToAcquireOtherPropertyPlantAndEquipment", "PaymentsToDevelopRealEstateAssets", "PaymentsToAcquireAndDevelopRealEstate"], -1),
    ("cf_acquisitions", "Acquisitions, net of cash acquired", "CF", ["PaymentsToAcquireBusinessesNetOfCashAcquired", "PaymentsToAcquireBusinessesGross", "PaymentsToAcquireBusinessesAndInterestInAffiliates", "PaymentsToAcquireRealEstate"], -1),
    ("cf_asset_sales", "Proceeds from asset sales", "CF", ["ProceedsFromSaleOfPropertyPlantAndEquipment", "ProceedsFromSaleOfRealEstateHeldforinvestment", "ProceedsFromSaleOfProductiveAssets", "ProceedsFromSaleOfRealEstate"], 1),
    ("cf_investing", "Cash from investing activities", "CF", ["NetCashProvidedByUsedInInvestingActivities", "NetCashProvidedByUsedInInvestingActivitiesContinuingOperations"], 1),
    ("cf_debt_issued", "Debt issued", "CF", ["ProceedsFromIssuanceOfLongTermDebt", "ProceedsFromIssuanceOfDebt", "ProceedsFromIssuanceOfSeniorLongTermDebt", "ProceedsFromDebtNetOfIssuanceCosts", "ProceedsFromIssuanceOfUnsecuredDebt"], 1),
    ("cf_debt_repaid", "Debt repaid", "CF", ["RepaymentsOfLongTermDebt", "RepaymentsOfDebt", "RepaymentsOfSeniorDebt", "RepaymentsOfLongTermDebtAndCapitalSecuritiesFinanceLeaseObligations", "RepaymentsOfUnsecuredDebt"], -1),
    ("cf_stock_issued", "Stock issued", "CF", ["ProceedsFromIssuanceOfCommonStock", "ProceedsFromStockOptionsExercised", "ProceedsFromIssuanceOfSharesUnderIncentiveAndShareBasedCompensationPlansIncludingStockOptions"], 1),
    ("cf_buybacks", "Share repurchases", "CF", ["PaymentsForRepurchaseOfCommonStock", "PaymentsForRepurchaseOfEquity"], -1),
    ("cf_dividends", "Dividends paid", "CF", ["PaymentsOfDividends", "PaymentsOfDividendsCommonStock", "PaymentsOfOrdinaryDividends", "DividendsCommonStockCash"], -1),
    ("cf_financing", "Cash from financing activities", "CF", ["NetCashProvidedByUsedInFinancingActivities", "NetCashProvidedByUsedInFinancingActivitiesContinuingOperations"], 1),
    ("cf_fx", "Effect of exchange rates", "CF", ["EffectOfExchangeRateOnCashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents", "EffectOfExchangeRateOnCashAndCashEquivalents", "EffectOfExchangeRateOnCashCashEquivalentsRestrictedCashAndRestrictedCashEquivalentsIncludingDisposalGroupAndDiscontinuedOperations"], 1),
    ("cf_net_change", "Net change in cash", "CF", ["CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalentsPeriodIncreaseDecreaseIncludingExchangeRateEffect", "CashAndCashEquivalentsPeriodIncreaseDecrease", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalentsPeriodIncreaseDecreaseExcludingExchangeRateEffect", "CashAndCashEquivalentsPeriodIncreaseDecreaseExcludingExchangeRateEffect"], 1),
    ("cf_fcf", "Free cash flow (operating cash flow + capital expenditures)", "CF", [], 1),
]

_EQUITY = [
    ("bs_preferred", "Preferred stock", "BS", ["PreferredStockValue", "PreferredStockValueOutstanding", "PreferredStockIncludingAdditionalPaidInCapitalNetOfDiscount"], 1),
    ("bs_common_stock", "Common stock", "BS", ["CommonStockValue", "CommonStocksIncludingAdditionalPaidInCapital", "CommonStockValueOutstanding"], 1),
    ("bs_apic", "Additional paid-in capital", "BS", ["AdditionalPaidInCapital", "AdditionalPaidInCapitalCommonStock"], 1),
    ("bs_retained", "Retained earnings (deficit)", "BS", ["RetainedEarningsAccumulatedDeficit"], 1),
    ("bs_aoci", "Accumulated other comprehensive income (loss)", "BS", ["AccumulatedOtherComprehensiveIncomeLossNetOfTax"], 1),
    ("bs_treasury", "Treasury stock", "BS", ["TreasuryStockValue", "TreasuryStockCommonValue", "TreasuryStockValueAcquiredCostMethod"], -1),
    ("bs_equity_parent", "Shareholders' equity", "BS", ["StockholdersEquity"], 1),
    ("bs_nci", "Noncontrolling interests", "BS", ["MinorityInterest"], 1),
    ("bs_equity_total", "Total equity", "BS", ["StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"], 1),
    ("bs_liab_equity", "Total liabilities and equity", "BS", ["LiabilitiesAndStockholdersEquity"], 1),
]

_EPS = [
    ("is_eps_basic", "EPS, basic", "IS", ["EarningsPerShareBasic", "EarningsPerShareBasicAndDiluted", "IncomeLossFromContinuingOperationsPerBasicShare"], 1),
    ("is_eps_diluted", "EPS, diluted", "IS", ["EarningsPerShareDiluted", "EarningsPerShareBasicAndDiluted", "IncomeLossFromContinuingOperationsPerDilutedShare"], 1),
    ("is_shares_basic", "Weighted average shares, basic", "IS", ["WeightedAverageNumberOfSharesOutstandingBasic", "WeightedAverageNumberOfShareOutstandingBasicAndDiluted"], 1),
    ("is_shares_diluted", "Weighted average shares, diluted", "IS", ["WeightedAverageNumberOfDilutedSharesOutstanding", "WeightedAverageNumberOfShareOutstandingBasicAndDiluted"], 1),
    ("is_dps", "Dividends declared per share", "IS", ["CommonStockDividendsPerShareDeclared", "CommonStockDividendsPerShareCashPaid"], 1),
]

_BOTTOM = [
    ("is_pretax", "Pretax income", "IS", ["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest", "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments", "IncomeLossFromContinuingOperationsBeforeIncomeTaxesDomestic", "IncomeLossBeforeIncomeTaxes"], 1),
    ("is_tax", "Income tax expense (benefit)", "IS", ["IncomeTaxExpenseBenefit", "IncomeTaxExpenseBenefitContinuingOperations"], 1),
    ("is_equity_method", "Equity-method income", "IS", ["IncomeLossFromEquityMethodInvestments", "IncomeLossFromEquityMethodInvestmentsNetOfDividendsOrDistributions"], 1),
    ("is_cont_ops", "Income from continuing operations", "IS", ["IncomeLossFromContinuingOperations", "IncomeLossFromContinuingOperationsIncludingPortionAttributableToNoncontrollingInterest"], 1),
    ("is_disc_ops", "Discontinued operations, net of tax", "IS", ["IncomeLossFromDiscontinuedOperationsNetOfTax", "IncomeLossFromDiscontinuedOperationsNetOfTaxAttributableToReportingEntity", "DiscontinuedOperationIncomeLossFromDiscontinuedOperationNetOfTax"], 1),
    ("is_net_income_total", "Net income (incl. noncontrolling interests)", "IS", ["ProfitLoss"], 1),
    ("is_nci", "Net income to noncontrolling interests", "IS", ["NetIncomeLossAttributableToNoncontrollingInterest"], 1),
    ("is_net_income", "Net income", "IS", ["NetIncomeLoss"], 1),
    ("is_pref_div", "Preferred dividends and other adjustments", "IS", ["PreferredStockDividendsAndOtherAdjustments", "PreferredStockDividendsIncomeStatementImpact", "DividendsPreferredStock"], 1),
    ("is_ni_common", "Net income to common shareholders", "IS", ["NetIncomeLossAvailableToCommonStockholdersBasic"], 1),
] + _EPS

TEMPLATES = {
    "industrial": [
        ("is_revenue", "Revenue", "IS", ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet", "SalesRevenueGoodsNet", "SalesRevenueServicesNet", "RevenuesNetOfInterestExpense", "OperatingLeasesIncomeStatementLeaseRevenue"], 1),
        ("is_cogs", "Cost of revenue", "IS", ["CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold", "CostOfGoodsAndServiceExcludingDepreciationDepletionAndAmortization", "CostOfServices", "CostOfGoodsSoldExcludingDepreciationDepletionAndAmortization"], 1),
        ("is_gross_profit", "Gross profit", "IS", ["GrossProfit"], 1),
        ("is_rnd", "Research and development", "IS", ["ResearchAndDevelopmentExpense", "ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost", "ResearchAndDevelopmentExpenseSoftwareExcludingAcquiredInProcessCost"], 1),
        ("is_sga", "Selling, general and administrative", "IS", ["SellingGeneralAndAdministrativeExpense", "GeneralAndAdministrativeExpense"], 1),
        ("is_selling", "Selling and marketing", "IS", ["SellingAndMarketingExpense", "MarketingAndAdvertisingExpense", "SellingExpense"], 1),
        ("is_da", "Depreciation and amortization", "IS", ["DepreciationDepletionAndAmortization", "DepreciationAndAmortization", "DepreciationAmortizationAndAccretionNet", "AmortizationOfIntangibleAssets"], 1),
        ("is_restructuring", "Restructuring", "IS", ["RestructuringCharges", "RestructuringSettlementAndImpairmentProvisions", "RestructuringCosts"], 1),
        ("is_impairment", "Impairments", "IS", ["GoodwillImpairmentLoss", "AssetImpairmentCharges", "ImpairmentOfIntangibleAssetsExcludingGoodwill", "GoodwillAndIntangibleAssetImpairment"], 1),
        ("is_opex_total", "Total operating costs and expenses", "IS", ["CostsAndExpenses", "OperatingExpenses", "OperatingCostsAndExpenses"], 1),
        ("is_operating_income", "Operating income", "IS", ["OperatingIncomeLoss"], 1),
        ("is_interest_expense", "Interest expense", "IS", ["InterestExpense", "InterestExpenseNonoperating", "InterestExpenseDebt", "InterestAndDebtExpense", "InterestIncomeExpenseNonoperatingNet"], 1),
        ("is_interest_income", "Interest and investment income", "IS", ["InvestmentIncomeInterest", "InvestmentIncomeInterestAndDividend", "InterestIncomeOther", "InvestmentIncomeNonoperating"], 1),
        ("is_other_nonop", "Other nonoperating income (expense)", "IS", ["OtherNonoperatingIncomeExpense", "NonoperatingIncomeExpense", "OtherNonoperatingIncome"], 1),
    ] + _BOTTOM + [
        ("bs_cash", "Cash and equivalents", "BS", ["CashAndCashEquivalentsAtCarryingValue", "Cash", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents", "CashAndDueFromBanks"], 1),
        ("bs_st_investments", "Short-term investments", "BS", ["ShortTermInvestments", "MarketableSecuritiesCurrent", "AvailableForSaleSecuritiesDebtSecuritiesCurrent", "AvailableForSaleSecuritiesCurrent", "OtherShortTermInvestments"], 1),
        ("bs_receivables", "Receivables", "BS", ["AccountsReceivableNetCurrent", "ReceivablesNetCurrent", "AccountsNotesAndLoansReceivableNetCurrent", "AccountsReceivableNet"], 1),
        ("bs_inventory", "Inventories", "BS", ["InventoryNet", "InventoryGross", "InventoryFinishedGoodsNetOfReserves"], 1),
        ("bs_other_current_assets", "Other current assets", "BS", ["OtherAssetsCurrent", "PrepaidExpenseAndOtherAssetsCurrent", "PrepaidExpenseCurrent"], 1),
        ("bs_dta_current", "Deferred tax assets, current", "BS", ["DeferredTaxAssetsNetCurrent"], 1),
        ("bs_current_assets", "Total current assets", "BS", ["AssetsCurrent"], 1),
        ("bs_ppe", "Property, plant and equipment, net", "BS", ["PropertyPlantAndEquipmentNet", "PropertyPlantAndEquipmentAndFinanceLeaseRightOfUseAssetAfterAccumulatedDepreciationAndAmortization"], 1),
        ("bs_rou", "Operating lease right-of-use assets", "BS", ["OperatingLeaseRightOfUseAsset"], 1),
        ("bs_goodwill", "Goodwill", "BS", ["Goodwill"], 1),
        ("bs_intangibles", "Intangible assets, net", "BS", ["IntangibleAssetsNetExcludingGoodwill", "FiniteLivedIntangibleAssetsNet", "IntangibleAssetsNetIncludingGoodwill"], 1),
        ("bs_lt_investments", "Long-term investments", "BS", ["LongTermInvestments", "MarketableSecuritiesNoncurrent", "AvailableForSaleSecuritiesDebtSecuritiesNoncurrent", "EquityMethodInvestments"], 1),
        ("bs_dta", "Deferred tax assets", "BS", ["DeferredIncomeTaxAssetsNet", "DeferredTaxAssetsNetNoncurrent"], 1),
        ("bs_other_assets", "Other noncurrent assets", "BS", ["OtherAssetsNoncurrent", "OtherAssets"], 1),
        ("bs_total_assets", "Total assets", "BS", ["Assets"], 1),
        ("bs_payables", "Accounts payable", "BS", ["AccountsPayableCurrent", "AccountsPayableAndAccruedLiabilitiesCurrent", "AccountsPayableTradeCurrent"], 1),
        ("bs_accrued", "Accrued liabilities", "BS", ["AccruedLiabilitiesCurrent", "EmployeeRelatedLiabilitiesCurrent"], 1),
        ("bs_deferred_revenue", "Deferred revenue, current", "BS", ["ContractWithCustomerLiabilityCurrent", "DeferredRevenueCurrent"], 1),
        ("bs_st_debt", "Short-term debt and current maturities", "BS", ["LongTermDebtCurrent", "DebtCurrent", "ShortTermBorrowings", "CommercialPaper", "LongTermDebtAndCapitalLeaseObligationsCurrent", "ShortTermDebtAndCurrentPortionOfLongTermDebt"], 1),
        ("bs_other_current_liabilities", "Other current liabilities", "BS", ["OtherLiabilitiesCurrent", "OtherAccruedLiabilitiesCurrent"], 1),
        ("bs_current_liabilities", "Total current liabilities", "BS", ["LiabilitiesCurrent"], 1),
        ("bs_lt_debt", "Long-term debt", "BS", ["LongTermDebtNoncurrent", "LongTermDebtAndCapitalLeaseObligations", "LongTermDebt", "SeniorLongTermNotes"], 1),
        ("bs_lease_liab", "Operating lease liabilities, noncurrent", "BS", ["OperatingLeaseLiabilityNoncurrent"], 1),
        ("bs_dtl", "Deferred tax liabilities", "BS", ["DeferredIncomeTaxLiabilitiesNet", "DeferredTaxLiabilitiesNoncurrent"], 1),
        ("bs_other_liabilities", "Other noncurrent liabilities", "BS", ["OtherLiabilitiesNoncurrent", "OtherLiabilities"], 1),
        ("bs_total_liabilities", "Total liabilities", "BS", ["Liabilities"], 1),
        ("bs_temp_equity", "Redeemable equity (temporary)", "BS", ["TemporaryEquityCarryingAmountAttributableToParent", "RedeemableNoncontrollingInterestEquityCarryingAmount", "TemporaryEquityCarryingAmountIncludingPortionAttributableToNoncontrollingInterests"], 1),
    ] + _EQUITY + _CASH_FLOW,

    "bank": [
        ("is_interest_income", "Total interest income", "IS", ["InterestAndDividendIncomeOperating", "InterestIncome", "InterestAndFeeIncomeLoansAndLeases", "InterestIncomeOperating"], 1),
        ("is_interest_expense", "Total interest expense", "IS", ["InterestExpense", "InterestExpenseOperating"], 1),
        ("is_nii", "Net interest income", "IS", ["InterestIncomeExpenseNet", "InterestIncomeExpenseOperatingNet"], 1),
        ("is_provision", "Provision for credit losses", "IS", ["ProvisionForLoanLeaseAndOtherLosses", "ProvisionForLoanAndLeaseLosses", "ProvisionForLoanLossesExpensed", "CreditLossExpenseReversal", "ProvisionForCreditLosses", "FinancingReceivableCreditLossExpenseReversal"], 1),
        ("is_nii_after_provision", "Net interest income after provision", "IS", ["InterestIncomeExpenseAfterProvisionForLoanLoss"], 1),
        ("is_noninterest_income", "Noninterest income", "IS", ["NoninterestIncome", "NoninterestIncomeOther"], 1),
        ("is_revenue", "Total net revenue", "IS", ["Revenues", "RevenuesNetOfInterestExpense", "RevenueFromContractWithCustomerExcludingAssessedTax"], 1),
        ("is_comp", "Compensation and benefits", "IS", ["LaborAndRelatedExpense", "SalariesAndWages", "EmployeeBenefitsAndShareBasedCompensation"], 1),
        ("is_occupancy", "Occupancy and equipment", "IS", ["OccupancyNet", "NoninterestExpenseOccupancyAndEquipment", "OccupancyAndEquipmentExpense"], 1),
        ("is_noninterest_expense", "Noninterest expense", "IS", ["NoninterestExpense", "OperatingExpenses", "CostsAndExpenses"], 1),
    ] + _BOTTOM + [
        ("bs_cash", "Cash and due from banks", "BS", ["CashAndDueFromBanks", "CashAndCashEquivalentsAtCarryingValue", "Cash", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"], 1),
        ("bs_deposits_banks", "Interest-bearing deposits with banks", "BS", ["InterestBearingDepositsInBanks", "InterestBearingDepositsInBanksAndOtherFinancialInstitutions", "CashAndSecuritiesSegregatedUnderFederalAndOtherRegulations"], 1),
        ("bs_resale", "Fed funds sold and resale agreements", "BS", ["FederalFundsSoldAndSecuritiesPurchasedUnderAgreementsToResell", "SecuritiesPurchasedUnderAgreementsToResell", "SecuritiesBorrowed"], 1),
        ("bs_trading_assets", "Trading assets", "BS", ["TradingSecurities", "FinancialInstrumentsOwnedAtFairValue", "TradingAssets", "AssetsFairValueDisclosure"], 1),
        ("bs_afs", "Securities available for sale", "BS", ["AvailableForSaleSecuritiesDebtSecurities", "AvailableForSaleSecurities", "DebtSecuritiesAvailableForSaleExcludingAccruedInterest"], 1),
        ("bs_htm", "Securities held to maturity", "BS", ["HeldToMaturitySecurities", "DebtSecuritiesHeldToMaturityAmortizedCostAfterAllowanceForCreditLoss", "HeldToMaturitySecuritiesFairValue", "DebtSecuritiesHeldToMaturityExcludingAccruedInterestAfterAllowanceForCreditLoss"], 1),
        ("bs_loans_gross", "Loans", "BS", ["LoansAndLeasesReceivableNetOfDeferredIncome", "FinancingReceivableExcludingAccruedInterestBeforeAllowanceForCreditLoss", "LoansAndLeasesReceivableGrossCarryingAmount", "NotesReceivableGross"], 1),
        ("bs_allowance", "Allowance for credit losses", "BS", ["LoansAndLeasesReceivableAllowance", "FinancingReceivableAllowanceForCreditLossExcludingAccruedInterest", "FinancingReceivableAllowanceForCreditLosses"], -1),
        ("bs_loans_net", "Loans, net", "BS", ["LoansAndLeasesReceivableNetReportedAmount", "FinancingReceivableExcludingAccruedInterestAfterAllowanceForCreditLoss", "NotesReceivableNet", "LoansReceivableNet"], 1),
        ("bs_ppe", "Premises and equipment", "BS", ["PropertyPlantAndEquipmentNet"], 1),
        ("bs_goodwill", "Goodwill", "BS", ["Goodwill"], 1),
        ("bs_intangibles", "Intangible assets", "BS", ["IntangibleAssetsNetExcludingGoodwill", "FiniteLivedIntangibleAssetsNet"], 1),
        ("bs_other_assets", "Other assets", "BS", ["OtherAssets", "OtherAssetsNoncurrent"], 1),
        ("bs_total_assets", "Total assets", "BS", ["Assets"], 1),
        ("bs_deposits", "Deposits", "BS", ["Deposits"], 1),
        ("bs_st_borrowings", "Short-term borrowings and repurchase agreements", "BS", ["ShortTermBorrowings", "FederalFundsPurchasedAndSecuritiesSoldUnderAgreementsToRepurchase", "SecuritiesSoldUnderAgreementsToRepurchase", "OtherShortTermBorrowings"], 1),
        ("bs_trading_liabilities", "Trading liabilities", "BS", ["TradingLiabilities", "FinancialInstrumentsSoldNotYetPurchasedAtFairValue"], 1),
        ("bs_lt_debt", "Long-term debt", "BS", ["LongTermDebt", "LongTermDebtNoncurrent", "UnsecuredLongTermDebt", "OtherLongTermDebt"], 1),
        ("bs_other_liabilities", "Other liabilities", "BS", ["AccruedLiabilitiesAndOtherLiabilities", "OtherLiabilities", "AccountsPayableAndAccruedLiabilitiesCurrentAndNoncurrent"], 1),
        ("bs_total_liabilities", "Total liabilities", "BS", ["Liabilities"], 1),
    ] + _EQUITY + _CASH_FLOW + [
        ("cf_chg_deposits", "Net change in deposits", "CF", ["IncreaseDecreaseInDeposits"], 1),
        ("cf_chg_loans", "Net change in loans", "CF", ["PaymentsForProceedsFromLoansAndLeases", "PaymentsForProceedsFromLoansReceivable"], -1),
    ],

    "insurer": [
        ("is_premiums", "Premiums earned", "IS", ["PremiumsEarnedNet", "PremiumsEarnedNetPropertyAndCasualty", "PremiumsEarnedNetLife", "InsurancePremiumsRevenueRecognized"], 1),
        ("is_policy_fees", "Policy charges and fee income", "IS", ["InsuranceServicesRevenue", "PolicyChargesAndFeeIncome", "FeesAndCommissions", "InsuranceCommissionsAndFees"], 1),
        ("is_net_investment_income", "Net investment income", "IS", ["NetInvestmentIncome", "InvestmentIncomeInterestAndDividend"], 1),
        ("is_realized_gains", "Realized investment gains (losses)", "IS", ["RealizedInvestmentGainsLosses", "GainLossOnInvestments", "DebtAndEquitySecuritiesGainLoss", "MarketableSecuritiesRealizedGainLossExcludingOtherThanTemporaryImpairments"], 1),
        ("is_revenue", "Total revenues", "IS", ["Revenues"], 1),
        ("is_benefits", "Losses, benefits and claims", "IS", ["PolicyholderBenefitsAndClaimsIncurredNet", "IncurredClaimsPropertyCasualtyAndLiability", "BenefitsLossesAndExpensesIncurred", "LiabilityForFuturePolicyBenefitsPeriodExpense", "PolicyholderBenefitsAndClaimsIncurredHealthCare"], 1),
        ("is_acquisition_costs", "Policy acquisition costs", "IS", ["DeferredPolicyAcquisitionCostAmortizationExpense", "AmortizationOfDeferredPolicyAcquisitionCosts", "PolicyAcquisitionCosts"], 1),
        ("is_operating_expenses", "Other operating expenses", "IS", ["OtherCostAndExpenseOperating", "GeneralAndAdministrativeExpense", "OtherUnderwritingExpense", "SellingGeneralAndAdministrativeExpense"], 1),
        ("is_interest_expense", "Interest expense", "IS", ["InterestExpense", "InterestExpenseDebt"], 1),
        ("is_total_expenses", "Total benefits and expenses", "IS", ["BenefitsLossesAndExpenses", "CostsAndExpenses", "OperatingExpenses"], 1),
    ] + _BOTTOM + [
        ("bs_fixed_maturities", "Fixed maturity securities", "BS", ["AvailableForSaleSecuritiesDebtSecurities", "DebtSecuritiesAvailableForSaleExcludingAccruedInterest", "AvailableForSaleSecurities"], 1),
        ("bs_equity_securities", "Equity securities", "BS", ["EquitySecuritiesFvNi", "AvailableForSaleSecuritiesEquitySecurities", "MarketableSecuritiesEquitySecurities"], 1),
        ("bs_mortgage_loans", "Mortgage and other loans", "BS", ["MortgageLoansOnRealEstateCommercialAndConsumerNet", "LoansAndLeasesReceivableNetReportedAmount", "MortgageLoansOnRealEstate"], 1),
        ("bs_investments", "Total investments", "BS", ["Investments", "InvestmentsFairValueDisclosure"], 1),
        ("bs_cash", "Cash and equivalents", "BS", ["CashAndCashEquivalentsAtCarryingValue", "Cash", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"], 1),
        ("bs_premiums_receivable", "Premiums and other receivables", "BS", ["PremiumsAndOtherReceivablesNet", "PremiumsReceivableAtCarryingValue", "AccountsReceivableNet"], 1),
        ("bs_reinsurance", "Reinsurance recoverables", "BS", ["ReinsuranceRecoverables", "ReinsuranceRecoverablesOnPaidAndUnpaidLosses", "ReinsuranceRecoverablesIncludingReinsurancePremiumPaid", "ReinsuranceRecoverablesOnUnpaidLosses"], 1),
        ("bs_dac", "Deferred policy acquisition costs", "BS", ["DeferredPolicyAcquisitionCosts", "DeferredPolicyAcquisitionCostsAndValueOfBusinessAcquired"], 1),
        ("bs_separate_assets", "Separate account assets", "BS", ["SeparateAccountAssets"], 1),
        ("bs_goodwill", "Goodwill", "BS", ["Goodwill"], 1),
        ("bs_other_assets", "Other assets", "BS", ["OtherAssets"], 1),
        ("bs_total_assets", "Total assets", "BS", ["Assets"], 1),
        ("bs_claims_reserves", "Unpaid losses and loss expenses", "BS", ["LiabilityForClaimsAndClaimsAdjustmentExpense", "LiabilityForUnpaidClaimsAndClaimsAdjustmentExpenseNet", "LiabilityForUnpaidClaimsAndClaimsAdjustmentExpenseIncurredButNotReportedClaimsAmount"], 1),
        ("bs_future_benefits", "Future policy benefits", "BS", ["LiabilityForFuturePolicyBenefits", "LiabilityForFuturePolicyBenefitsAndUnpaidClaimsAndClaimsAdjustmentExpense"], 1),
        ("bs_policyholder_accounts", "Policyholder account balances", "BS", ["PolicyholderContractDeposits", "PolicyholderFunds"], 1),
        ("bs_unearned_premiums", "Unearned premiums", "BS", ["UnearnedPremiums"], 1),
        ("bs_separate_liabilities", "Separate account liabilities", "BS", ["SeparateAccountsLiability"], 1),
        ("bs_debt", "Debt", "BS", ["LongTermDebt", "DebtInstrumentCarryingAmount", "SeniorNotes", "LongTermDebtNoncurrent", "DebtAndCapitalLeaseObligations"], 1),
        ("bs_other_liabilities", "Other liabilities", "BS", ["OtherLiabilities", "AccountsPayableAndAccruedLiabilitiesCurrentAndNoncurrent"], 1),
        ("bs_total_liabilities", "Total liabilities", "BS", ["Liabilities"], 1),
    ] + _EQUITY + _CASH_FLOW,

    "reit": [
        ("is_rental_revenue", "Rental revenue", "IS", ["OperatingLeaseLeaseIncome", "OperatingLeasesIncomeStatementLeaseRevenue", "RealEstateRevenueNet", "LeaseIncome", "OperatingLeasesIncomeStatementMinimumLeaseRevenue"], 1),
        ("is_revenue", "Total revenues", "IS", ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax"], 1),
        ("is_property_expenses", "Property operating expenses", "IS", ["DirectCostsOfLeasedAndRentedPropertyOrEquipment", "CostOfOtherPropertyOperatingExpense", "OperatingCostsAndExpenses", "CostOfRevenue", "RealEstateTaxExpense"], 1),
        ("is_da", "Depreciation and amortization", "IS", ["DepreciationAndAmortization", "DepreciationDepletionAndAmortization", "DepreciationAmortizationAndAccretionNet", "CostOfGoodsAndServicesSoldDepreciationAndAmortization"], 1),
        ("is_ga", "General and administrative", "IS", ["GeneralAndAdministrativeExpense", "SellingGeneralAndAdministrativeExpense"], 1),
        ("is_impairment", "Impairments", "IS", ["ImpairmentOfRealEstate", "AssetImpairmentCharges", "ImpairmentOfLongLivedAssetsHeldForUse"], 1),
        ("is_opex_total", "Total expenses", "IS", ["CostsAndExpenses", "OperatingExpenses"], 1),
        ("is_operating_income", "Operating income", "IS", ["OperatingIncomeLoss"], 1),
        ("is_interest_expense", "Interest expense", "IS", ["InterestExpense", "InterestExpenseNonoperating", "InterestExpenseDebt", "InterestAndDebtExpense"], 1),
        ("is_gain_on_sale", "Gains on sale of real estate", "IS", ["GainLossOnSaleOfProperties", "GainsLossesOnSalesOfInvestmentRealEstate", "GainLossOnDispositionOfAssets", "GainLossOnSaleOfPropertyPlantEquipment", "GainLossOnSaleOfRealEstateInvestmentProperty"], 1),
    ] + _BOTTOM + [
        ("bs_re_gross", "Real estate, at cost", "BS", ["RealEstateInvestmentPropertyAtCost", "RealEstateGrossAtCarryingValue", "PropertyPlantAndEquipmentGross"], 1),
        ("bs_accum_depr", "Accumulated depreciation", "BS", ["RealEstateInvestmentPropertyAccumulatedDepreciation", "AccumulatedDepreciationDepletionAndAmortizationPropertyPlantAndEquipment"], -1),
        ("bs_re_net", "Real estate, net", "BS", ["RealEstateInvestmentPropertyNet", "PropertyPlantAndEquipmentNet", "RealEstateInvestments"], 1),
        ("bs_cash", "Cash and equivalents", "BS", ["CashAndCashEquivalentsAtCarryingValue", "Cash", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"], 1),
        ("bs_jv", "Investments in unconsolidated entities", "BS", ["EquityMethodInvestments", "InvestmentsInAffiliatesSubsidiariesAssociatesAndJointVentures"], 1),
        ("bs_receivables", "Tenant and other receivables", "BS", ["AccountsReceivableNet", "TenantReceivables", "AccountsAndOtherReceivablesNetCurrent", "AccountsReceivableNetCurrent"], 1),
        ("bs_goodwill", "Goodwill", "BS", ["Goodwill"], 1),
        ("bs_intangibles", "Lease intangibles and other intangibles", "BS", ["IntangibleAssetsNetExcludingGoodwill", "FiniteLivedIntangibleAssetsNet", "BelowMarketLeaseNet"], 1),
        ("bs_other_assets", "Other assets", "BS", ["OtherAssets", "OtherAssetsNoncurrent"], 1),
        ("bs_total_assets", "Total assets", "BS", ["Assets"], 1),
        ("bs_secured_debt", "Secured debt (mortgages)", "BS", ["SecuredDebt", "MortgageLoansOnRealEstate", "NotesPayableSecured"], 1),
        ("bs_unsecured_debt", "Unsecured debt", "BS", ["UnsecuredDebt", "SeniorNotes", "SeniorLongTermNotes", "LongTermDebt", "LongTermDebtNoncurrent", "NotesPayable", "DebtInstrumentCarryingAmount"], 1),
        ("bs_credit_facility", "Credit facility and commercial paper", "BS", ["LineOfCredit", "LongTermLineOfCredit", "CommercialPaper", "ShortTermBorrowings"], 1),
        ("bs_other_liabilities", "Other liabilities", "BS", ["AccountsPayableAndAccruedLiabilitiesCurrentAndNoncurrent", "OtherLiabilities", "AccountsPayableAndAccruedLiabilitiesCurrent"], 1),
        ("bs_total_liabilities", "Total liabilities", "BS", ["Liabilities"], 1),
        ("bs_temp_equity", "Redeemable interests (temporary)", "BS", ["RedeemableNoncontrollingInterestEquityCarryingAmount", "TemporaryEquityCarryingAmountAttributableToParent"], 1),
    ] + _EQUITY + _CASH_FLOW,

    "utility": [
        ("is_revenue", "Operating revenues", "IS", ["Revenues", "RegulatedAndUnregulatedOperatingRevenue", "RevenueFromContractWithCustomerExcludingAssessedTax", "ElectricUtilityRevenue", "RegulatedOperatingRevenue"], 1),
        ("is_fuel_power", "Fuel and purchased power", "IS", ["FuelCosts", "CostOfPurchasedPower", "UtilitiesOperatingExpenseFuelAndPurchasedPower", "FuelAndPurchasedPowerCost", "CostOfNaturalGasPurchases", "UtilitiesOperatingExpensePurchasedPower"], 1),
        ("is_om", "Operations and maintenance", "IS", ["UtilitiesOperatingExpenseMaintenanceAndOperations", "OtherCostAndExpenseOperating", "UtilitiesOperatingExpenseMaintenanceOperationsAndOtherCostsAndExpenses", "OperatingCostsAndExpenses"], 1),
        ("is_da", "Depreciation and amortization", "IS", ["DepreciationDepletionAndAmortization", "DepreciationAndAmortization", "UtilitiesOperatingExpenseDepreciationAndAmortization", "DepreciationAmortizationAndAccretionNet"], 1),
        ("is_taxes_other", "Taxes other than income taxes", "IS", ["TaxesExcludingIncomeAndExciseTaxes", "UtilitiesOperatingExpenseTaxes", "TaxesOther"], 1),
        ("is_opex_total", "Total operating expenses", "IS", ["CostsAndExpenses", "OperatingExpenses", "UtilitiesOperatingExpense"], 1),
        ("is_operating_income", "Operating income", "IS", ["OperatingIncomeLoss"], 1),
        ("is_afudc", "Allowance for equity funds used during construction", "IS", ["PublicUtilitiesAllowanceForFundsUsedDuringConstructionCapitalizedCostOfEquity", "PublicUtilitiesAllowanceForFundsUsedDuringConstructionAdditions"], 1),
        ("is_interest_expense", "Interest expense", "IS", ["InterestExpense", "InterestExpenseDebt", "InterestAndDebtExpense", "InterestExpenseNonoperating"], 1),
        ("is_other_nonop", "Other income (expense)", "IS", ["OtherNonoperatingIncomeExpense", "NonoperatingIncomeExpense"], 1),
    ] + _BOTTOM + [
        ("bs_plant_gross", "Utility plant in service", "BS", ["PublicUtilitiesPropertyPlantAndEquipmentPlantInService", "PropertyPlantAndEquipmentGross", "PublicUtilitiesPropertyPlantAndEquipmentGross"], 1),
        ("bs_accum_depr", "Accumulated depreciation", "BS", ["PublicUtilitiesPropertyPlantAndEquipmentAccumulatedDepreciation", "AccumulatedDepreciationDepletionAndAmortizationPropertyPlantAndEquipment"], -1),
        ("bs_plant_net", "Property, plant and equipment, net", "BS", ["PublicUtilitiesPropertyPlantAndEquipmentNet", "PropertyPlantAndEquipmentNet"], 1),
        ("bs_cash", "Cash and equivalents", "BS", ["CashAndCashEquivalentsAtCarryingValue", "Cash", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"], 1),
        ("bs_receivables", "Receivables", "BS", ["AccountsReceivableNetCurrent", "ReceivablesNetCurrent", "AccountsReceivableNet"], 1),
        ("bs_inventory", "Fuel, materials and supplies", "BS", ["InventoryNet", "PublicUtilitiesInventory", "EnergyRelatedInventory"], 1),
        ("bs_reg_assets_current", "Regulatory assets, current", "BS", ["RegulatoryAssetsCurrent"], 1),
        ("bs_current_assets", "Total current assets", "BS", ["AssetsCurrent"], 1),
        ("bs_reg_assets", "Regulatory assets, noncurrent", "BS", ["RegulatoryAssetsNoncurrent", "RegulatoryAssets"], 1),
        ("bs_goodwill", "Goodwill", "BS", ["Goodwill"], 1),
        ("bs_nuclear_trust", "Nuclear decommissioning trust", "BS", ["DecommissioningFundInvestments", "DecommissioningTrustAssetsAmount"], 1),
        ("bs_other_assets", "Other noncurrent assets", "BS", ["OtherAssetsNoncurrent", "OtherAssets"], 1),
        ("bs_total_assets", "Total assets", "BS", ["Assets"], 1),
        ("bs_payables", "Accounts payable", "BS", ["AccountsPayableCurrent"], 1),
        ("bs_st_debt", "Short-term debt and current maturities", "BS", ["LongTermDebtCurrent", "ShortTermBorrowings", "CommercialPaper", "DebtCurrent", "LongTermDebtAndCapitalLeaseObligationsCurrent"], 1),
        ("bs_current_liabilities", "Total current liabilities", "BS", ["LiabilitiesCurrent"], 1),
        ("bs_lt_debt", "Long-term debt", "BS", ["LongTermDebtNoncurrent", "LongTermDebtAndCapitalLeaseObligations", "LongTermDebt"], 1),
        ("bs_dtl", "Deferred income taxes", "BS", ["DeferredIncomeTaxLiabilitiesNet", "DeferredTaxLiabilitiesNoncurrent", "AccumulatedDeferredInvestmentTaxCredit"], 1),
        ("bs_reg_liabilities", "Regulatory liabilities", "BS", ["RegulatoryLiabilityNoncurrent", "RegulatoryLiabilities"], 1),
        ("bs_aro", "Asset retirement obligations", "BS", ["AssetRetirementObligationsNoncurrent", "AssetRetirementObligation"], 1),
        ("bs_other_liabilities", "Other noncurrent liabilities", "BS", ["OtherLiabilitiesNoncurrent", "OtherLiabilities"], 1),
        ("bs_total_liabilities", "Total liabilities", "BS", ["Liabilities"], 1),
    ] + _EQUITY + _CASH_FLOW,
}

# Calculated template lines: line_id -> (formula text, [(line_id, coefficient)]).
CALCS = {
    "is_gross_profit": ("Revenue - Cost of revenue", [("is_revenue", 1), ("is_cogs", -1)]),
    "cf_fcf": ("Cash from operating activities + Capital expenditures", [("cf_operating", 1), ("cf_capex", 1)]),
}

BANK_SIC = set(range(6020, 6037)) | {6099, 6111, 6141, 6153, 6159, 6172, 6199, 6211}
INSURER_SIC = set(range(6311, 6400)) | {6411}


def template_for(sic):
    try:
        s = int(sic or 0)
    except ValueError:
        s = 0
    if s in BANK_SIC:
        return "bank"
    if s in INSURER_SIC:
        return "insurer"
    if s == 6798:
        return "reit"
    if 4900 <= s <= 4991:
        return "utility"
    return "industrial"


def map_rows():
    """Rows for the fs_std_map table: one per (template, line, candidate tag)."""
    out = []
    for tpl, lines in TEMPLATES.items():
        for ord_, (lid, label, stmt, tags, sign) in enumerate(lines):
            for pri, tag in enumerate(tags):
                out.append({"template": tpl, "line_id": lid, "line_label": label, "stmt": stmt, "ord": ord_, "tag": tag, "priority": pri, "sign": sign})
            if not tags and lid in CALCS:
                out.append({"template": tpl, "line_id": lid, "line_label": label, "stmt": stmt, "ord": ord_, "tag": "calc:" + CALCS[lid][0], "priority": 0, "sign": sign})
    return out


def published_map():
    """The mapping as published for the site: templates with lines, tags and calculations."""
    return {
        "templates": {tpl: [{"id": lid, "label": label, "stmt": stmt, "tags": tags, "sign": sign, **({"calc": CALCS[lid][0]} if lid in CALCS and not tags else {})}
                            for lid, label, stmt, tags, sign in lines] for tpl, lines in TEMPLATES.items()},
        "sic_rules": {"bank": "SIC 6020-6036, 6099, 6111, 6141, 6153, 6159, 6172, 6199, 6211", "insurer": "SIC 6311-6399, 6411", "reit": "SIC 6798", "utility": "SIC 4900-4991", "industrial": "all other SIC codes"},
        "method": "A template line takes the first listed tag the filer presented on a statement of the same kind with a consolidated value (no axis/member, no co-registrant). Each filed line feeds at most one template line. Unmatched filed lines are listed as not mapped and are not summed.",
    }


def standardize(template, reports):
    """Maps one filing's presented statements onto a template.

    reports: [{report, stmt, lines: [[line, tag, custom, plabel, negating, iord, crdr]], facts: [[tag, ddate, qtrs, uom, seg, coreg, value, fn]]}]
    Returns {"lines": {line_id: [[ddate, qtrs, value, tag, report, line, plabel]]}, "unmapped": [[report, line, tag, plabel, stmt]]}.
    """
    kind = {"IS": ("IS", "CI"), "BS": ("BS",), "CF": ("CF",)}
    # candidate filed lines per statement kind: tag -> (report, line, plabel, {(ddate, qtrs): value})
    avail = {}
    for r in reports:
        st = r["stmt"]
        if st not in ("IS", "BS", "CF", "CI", "UN") or r.get("inpth"):
            continue
        vals = {}
        for tag, ddate, qtrs, uom, seg, coreg, value, fn in r["facts"]:
            if seg or coreg or value is None:
                continue
            vals.setdefault(tag, {})[(ddate, qtrs)] = value
        for ln in r["lines"]:
            tag = ln[1]
            if tag in vals:
                avail.setdefault(st, {}).setdefault(tag, (r["report"], ln[0], ln[3], vals[tag]))
    used = set()
    out = {}
    for lid, label, stmt, tags, sign in TEMPLATES[template]:
        hit = None
        for tag in tags:
            for st in kind[stmt] + ("UN",):
                cand = avail.get(st, {}).get(tag)
                if cand and (st, cand[0], cand[1]) not in used:
                    hit = (st, tag, cand)
                    break
            if hit:
                break
        if not hit:
            continue
        st, tag, (rep, line, plabel, vals) = hit
        used.add((st, rep, line))
        out[lid] = [[d, q, v * sign, tag, rep, line, plabel] for (d, q), v in sorted(vals.items())]
    for lid, (formula, parts) in CALCS.items():
        if lid in out or not all(p in out for p, _ in parts):
            continue
        if not any(x[0] == lid for x in TEMPLATES[template]):
            continue
        maps = [{(d, q): v for d, q, v, *_ in out[p]} for p, _ in parts]
        keys = set(maps[0]).intersection(*maps[1:])
        if keys:
            out[lid] = [[d, q, round(sum(m[(d, q)] * c for m, (_, c) in zip(maps, parts)), 4), "calc", None, None, formula] for d, q in sorted(keys)]
    unmapped = []
    for r in reports:
        if r["stmt"] not in ("IS", "BS", "CF") or r.get("inpth"):
            continue
        for ln in r["lines"]:
            if (r["stmt"], r["report"], ln[0]) not in used and ln[1] in avail.get(r["stmt"], {}) and avail[r["stmt"]][ln[1]][:2] == (r["report"], ln[0]):
                unmapped.append([r["report"], ln[0], ln[1], ln[3], r["stmt"]])
    return {"lines": out, "unmapped": unmapped}
