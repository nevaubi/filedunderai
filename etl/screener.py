"""Screener factor table: one row per company with 150+ fields, each with a definition, unit, as-of date and source.

Inputs (all already collected by the other jobs):
  companies/{T}.json statements (SEC XBRL facts with accession, form, filing date), the archived companyfacts JSON
  (interest expense, retained earnings, current debt, cover-page share count), prices/{SYM}.json plus a 10-year
  close history cache, meta/insiders.json, news_items, filings, macro_observations, kpis/{T}.json, segments/{T}.json.

Outputs (public-data bucket):
  screener.json            field catalogue + compact rows (one array per company)
  screener/src/{T}.json    provenance per field: the filed facts behind it (accession, concept, period, filing date),
                           the price dates and closes for price-derived fields, the document id for KPI fields
  screener/corr.json       pairwise 1-year daily return correlations (int8, upper triangle) for clustering
Caches (raw bucket): screener/closes.json.gz, screener/fund.json.gz, screener/kpi.json.gz.
"""
import base64
import datetime as dt
import gzip
import json
import math
import re
import statistics
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from operator import mul

from . import sec, fundamentals
from .common import HTTPError, Run, get_json, now_iso, request, serialized_aggregates
from .prices import path_key
from .universe import ADDITIONAL_LIST, BY_TICKER, MARKET_SYMBOLS, SECTOR_ETF, SP100, SP500, UNIVERSE, UNIVERSE_METADATA

TICKERS = [t for t, *_ in UNIVERSE]
STRICT_SOURCE_TICKERS = {t for t, *_ in ADDITIONAL_LIST}
FACTOR_SYMS = ["SPY", "QQQ", "^TNX", "CL=F", "GC=F", "DX-Y.NYB", "BTC-USD"]
EXTRA_SYMS = ["DX-Y.NYB"]  # not carried by the prices job
HIST_SYMS = list(dict.fromkeys(MARKET_SYMBOLS + EXTRA_SYMS + TICKERS))
from .source_quality import CALCULATION_HISTORY_YEARS
HIST_YEARS = CALCULATION_HISTORY_YEARS
CLOSES_PATH, FUND_PATH, KPI_PATH = "screener/closes.json.gz", "screener/fund.json.gz", "screener/kpi.json.gz"
FUND_VERSION = 6
YAHOO = "https://query2.finance.yahoo.com/v8/finance/chart/"

# statement lines read from the company bundles (label as stored by etl/sec.py)
LABELS = ["Revenue", "Cost of revenue", "Gross profit", "Research & development", "Operating income", "Pretax income", "Income tax",
          "Net income", "EPS diluted", "Diluted shares", "Cash & equivalents", "Marketable securities, current", "Total current assets",
          "Goodwill", "Total assets", "Total current liabilities", "Long-term debt", "Total liabilities", "Shareholders' equity",
          "D&A", "Stock-based comp", "Operating cash flow", "Capex", "Buybacks", "Dividends paid"]
STMT_OF = {"Cash & equivalents": "balance", "Marketable securities, current": "balance", "Total current assets": "balance", "Goodwill": "balance",
           "Total assets": "balance", "Total current liabilities": "balance", "Long-term debt": "balance", "Total liabilities": "balance",
           "Shareholders' equity": "balance", "D&A": "cashflow", "Stock-based comp": "cashflow", "Operating cash flow": "cashflow", "Capex": "cashflow",
           "Buybacks": "cashflow", "Dividends paid": "cashflow"}
INSTANT = {l for l, s in STMT_OF.items() if s == "balance"}
# extra us-gaap concepts read from the archived companyfacts (not in the statement bundles)
EXTRA_FLOW = [("Interest expense", ["InterestExpense", "InterestExpenseNonoperating", "InterestExpenseDebt", "InterestAndDebtExpense"])]
EXTRA_BAL = [("Debt, current", ["DebtCurrent", "LongTermDebtAndCapitalLeaseObligationsCurrent"]),
             ("Long-term debt, current", ["LongTermDebtCurrent"]),
             ("Short-term borrowings", ["ShortTermBorrowings"]),
             ("Commercial paper", ["CommercialPaper"]),
             ("Retained earnings", ["RetainedEarningsAccumulatedDeficit"])]
for _l, _ in EXTRA_FLOW:
    STMT_OF[_l] = "income"
for _l, _ in EXTRA_BAL:
    STMT_OF[_l] = "balance"
    INSTANT.add(_l)
STMT_OF["Shares outstanding (cover page)"] = "cover"
PER_SHARE = {"EPS diluted"}
OUTFLOWS = {"Capex", "Buybacks", "Dividends paid"}  # payments, read as positive amounts whatever the sign tagged
# share classes that convert at a ratio: Berkshire Class A counts as 1,500 Class B shares for market cap
CLASS_RATIO = {"BRK-B": {"below": 5_000_000, "small": 1500}}
SHARE_COUNT = {"Diluted shares"}

# ------------------------------------------------------------------ field catalogue
GROUPS = [("id", "Identity"), ("val", "Valuation"), ("grw", "Growth"), ("qual", "Quality and profitability"), ("risk", "Balance sheet and risk"),
          ("px", "Price and technicals"), ("corr", "Correlations"), ("act", "Ownership and activity"), ("kpi", "Company KPIs")]
F = []


def fld(fid, g, name, unit, d, src):
    F.append({"id": fid, "g": g, "n": name, "u": unit, "d": d, "src": src})


SEC = "SEC filings (latest compatible XBRL vintage; filing history for as-of analysis)"
PX = "Daily closes (Yahoo Finance chart API, split-adjusted)"
fld("price", "px", "Price", "usd", "Last daily close.", PX)
fld("mcap", "val", "Market cap", "usd", "Price × shares outstanding from the cover page of the latest 10-K or 10-Q.", SEC + "; " + PX)
fld("ev", "val", "Enterprise value", "usd", "Market cap + total debt (long-term debt + current debt) − cash and current marketable securities, latest balance sheet. Not computed for Financials.", SEC + "; " + PX)
VAL = [("pe", "P/E", "x", "Price / diluted EPS, trailing four quarters (split-adjusted). Empty when EPS ≤ 0."),
       ("ev_ebit", "EV/EBIT", "x", "Enterprise value / operating income, trailing four quarters. Empty when EBIT ≤ 0 or for Financials."),
       ("ev_sales", "EV/Sales", "x", "Enterprise value / revenue, trailing four quarters. Not computed for Financials."),
       ("ev_fcf", "EV/FCF", "x", "Enterprise value / free cash flow (operating cash flow − capex), trailing four quarters. Empty when FCF ≤ 0 or for Financials."),
       ("pb", "P/B", "x", "Market cap / shareholders' equity at the latest quarter end. Empty when equity ≤ 0."),
       ("ps", "P/S", "x", "Market cap / revenue, trailing four quarters."),
       ("fcf_yield", "FCF yield", "pct", "Free cash flow (trailing four quarters) / market cap."),
       ("earn_yield", "Earnings yield", "pct", "Diluted EPS (trailing four quarters) / price. Negative when the company lost money.")]
for fid, n, u, d in VAL:
    fld(fid, "val", n, u, d, SEC + "; " + PX)
fld("div_yield", "val", "Dividend yield", "pct", "Dividends paid (cash flow statement, trailing four quarters) / market cap.", SEC + "; " + PX)
fld("bb_yield", "val", "Buyback yield", "pct", "Share repurchases (cash flow statement, trailing four quarters) / market cap.", SEC + "; " + PX)
fld("sh_yield", "val", "Shareholder yield", "pct", "(Dividends paid + share repurchases, trailing four quarters) / market cap.", SEC + "; " + PX)
for fid, n, *_ in VAL:
    fld(fid + "_p5", "val", n + " 5y percentile", "pctl",
        f"Where today's {n} sits in its own monthly history over five years (0 = cheapest month on record, 1 = most expensive; for yields 1 = highest yield). "
        "History is a weighted diluted-share valuation proxy using filings available at each month end, not exact historical market cap. Needs 24 months.", SEC + "; " + PX)
GROWTH = [("rev", "Revenue"), ("eps", "EPS"), ("fcf", "Free cash flow"), ("oi", "Operating income")]
for k, n in GROWTH:
    fld(f"{k}_g_q", "grw", f"{n} growth, latest quarter YoY", "pct", f"Latest quarter {n.lower()} / same quarter a year earlier − 1. Empty when the base is ≤ 0.", SEC)
    fld(f"{k}_g_ttm", "grw", f"{n} growth, TTM YoY", "pct", f"Trailing four quarters {n.lower()} / the four quarters before − 1. Empty when the base is ≤ 0.", SEC)
    fld(f"{k}_cagr3", "grw", f"{n} 3y CAGR", "pct", f"Compound annual growth of trailing-four-quarter {n.lower()} over 3 years. Empty when either end is ≤ 0.", SEC)
    fld(f"{k}_cagr5", "grw", f"{n} 5y CAGR", "pct", f"Compound annual growth of trailing-four-quarter {n.lower()} over 5 years. Empty when either end is ≤ 0.", SEC)
    fld(f"{k}_accel", "grw", f"{n} growth acceleration", "pct", f"Latest quarter YoY growth minus the prior quarter's YoY growth (in percentage points).", SEC)
    fld(f"{k}_streak", "grw", f"{n} growth streak", "num", f"Consecutive quarters, counting back from the latest, with {n.lower()} above the same quarter a year earlier.", SEC)
MARG = [("gm", "Gross margin", "Gross profit (or revenue − cost of revenue) / revenue"), ("om", "Operating margin", "Operating income / revenue"),
        ("nm", "Net margin", "Net income / revenue"), ("fcfm", "FCF margin", "(Operating cash flow − capex) / revenue")]
for k, n, d in MARG:
    fld(k, "qual", n, "pct", d + ", trailing four quarters.", SEC)
    fld(k + "_chg3", "qual", n + " 3y change", "pct", f"{n} (trailing four quarters) now minus three years earlier, in percentage points.", SEC)
for fid, n, u, d in [
        ("roe", "ROE", "pct", "Net income (trailing four quarters) / average shareholders' equity (now and a year earlier). Empty when equity ≤ 0."),
        ("roic", "ROIC", "pct", "Operating income × (1 − effective tax rate) / (equity + total debt − cash), trailing four quarters. Tax rate is income tax / pretax income, clamped to 0–50%."),
        ("roa", "ROA", "pct", "Net income (trailing four quarters) / average total assets."),
        ("accruals", "Accruals ratio", "pct", "(Net income − operating cash flow), trailing four quarters, / average total assets. Lower is better."),
        ("cash_conv", "Cash conversion", "x", "Operating cash flow / net income, trailing four quarters. Empty when net income ≤ 0."),
        ("sbc_pct", "SBC % of revenue", "pct", "Stock-based compensation / revenue, trailing four quarters."),
        ("capex_int", "Capex intensity", "pct", "Capital expenditures / revenue, trailing four quarters."),
        ("rnd_int", "R&D intensity", "pct", "Research and development expense / revenue, trailing four quarters. Empty when R&D is not reported.")]:
    fld(fid, "qual", n, u, d, SEC)
for fid, n, u, d in [
        ("net_debt", "Net debt", "usd", "Total debt (long-term + current) − cash and current marketable securities, latest quarter end."),
        ("nd_ebitda", "Net debt / EBITDA", "x", "Net debt / (operating income + D&A), trailing four quarters. Empty when EBITDA ≤ 0. Not computed for Financials."),
        ("de", "Debt / equity", "x", "Total debt / shareholders' equity. Empty when equity ≤ 0."),
        ("int_cov", "Interest coverage", "x", "Operating income / interest expense, trailing four quarters. Empty when interest expense is not reported."),
        ("current_ratio", "Current ratio", "x", "Total current assets / total current liabilities."),
        ("altman_z", "Altman Z", "score", "1.2·working capital/assets + 1.4·retained earnings/assets + 3.3·EBIT/assets + 0.6·market cap/total liabilities + 1.0·sales/assets (trailing four quarters). Below 1.8 distress, above 3 safe. Not computed for Financials."),
        ("pio_f", "Piotroski F-score", "score", "Sum of nine pass/fail tests on profitability, leverage, liquidity and efficiency, trailing four quarters vs the four before (0–9)."),
        ("shr_chg1", "Share count change 1y", "pct", "Diluted weighted shares, latest quarter vs a year earlier (split-adjusted). Positive is dilution."),
        ("shr_chg3", "Share count change 3y", "pct", "Diluted weighted shares, latest quarter vs three years earlier (split-adjusted)."),
        ("gw_assets", "Goodwill / assets", "pct", "Goodwill / total assets, latest quarter end.")]:
    fld(fid, "risk", n, u, d, SEC + (("; " + PX) if fid == "altman_z" else ""))
PIO = [("f_roa", "Positive ROA", "Net income > 0 (trailing four quarters)."), ("f_cfo", "Positive operating cash flow", "Operating cash flow > 0."),
       ("f_droa", "ROA improved", "ROA higher than a year earlier."), ("f_accrual", "Cash flow above earnings", "Operating cash flow > net income."),
       ("f_dlever", "Leverage fell", "Long-term debt / total assets lower than a year earlier."), ("f_dliquid", "Liquidity improved", "Current ratio higher than a year earlier."),
       ("f_noissue", "No dilution", "Diluted shares not higher than a year earlier."), ("f_dmargin", "Gross margin improved", "Gross margin higher than a year earlier."),
       ("f_dturn", "Asset turnover improved", "Revenue / total assets higher than a year earlier.")]
for fid, n, d in PIO:
    fld("pio_" + fid, "risk", "Piotroski: " + n.lower(), "bool", d, SEC)
RET = [("ret_1w", "Return 1W", 7), ("ret_1m", "Return 1M", 30), ("ret_3m", "Return 3M", 91), ("ret_6m", "Return 6M", 182), ("ret_ytd", "Return YTD", None),
       ("ret_1y", "Return 1Y", 365), ("ret_3y", "Return 3Y", 1095), ("ret_5y", "Return 5Y", 1826)]
for fid, n, days in RET:
    fld(fid, "px", n, "pct", "Price change " + ("since the last close of the prior year." if days is None else f"over {days} calendar days (close to close, split-adjusted, dividends excluded)."), PX)
for fid, n, u, d in [
        ("dist_hi52", "From 52w high", "pct", "Price / highest close of the last 365 days − 1."),
        ("dist_lo52", "From 52w low", "pct", "Price / lowest close of the last 365 days − 1."),
        ("above_sma20", "Above 20-day average", "bool", "Last close above the 20-day simple moving average."),
        ("above_sma50", "Above 50-day average", "bool", "Last close above the 50-day simple moving average."),
        ("above_sma200", "Above 200-day average", "bool", "Last close above the 200-day simple moving average."),
        ("dist_sma20", "From 20-day average", "pct", "Price / 20-day simple moving average − 1."),
        ("dist_sma50", "From 50-day average", "pct", "Price / 50-day simple moving average − 1."),
        ("dist_sma200", "From 200-day average", "pct", "Price / 200-day simple moving average − 1."),
        ("cross_type", "Last 50/200 cross", "num", "1 = golden cross (50-day crossed above 200-day), −1 = death cross, for the most recent cross in two years."),
        ("cross_days", "Days since 50/200 cross", "days", "Calendar days since the most recent 50/200-day cross."),
        ("rsi14", "RSI 14", "num", "Wilder relative strength index, 14 days."),
        ("macd_state", "MACD state", "num", "1 when MACD (12, 26) is above its 9-day signal line, −1 when below."),
        ("macd_hist", "MACD histogram", "pct", "(MACD − signal) / price."),
        ("vol_60d", "Volatility 60d", "pct", "Standard deviation of daily returns over 60 days, annualized (×√252)."),
        ("vol_1y", "Volatility 1y", "pct", "Standard deviation of daily returns over 252 days, annualized."),
        ("mdd_1y", "Max drawdown 1y", "pct", "Largest peak-to-trough fall in close over the last 365 days."),
        ("beta", "Beta 1y", "num", "Slope of daily returns on SPY daily returns, last 252 days."),
        ("rs_spy_3m", "Relative strength vs SPY 3M", "pct", "(1 + 3-month return) / (1 + SPY 3-month return) − 1."),
        ("rs_spy_6m", "Relative strength vs SPY 6M", "pct", "(1 + 6-month return) / (1 + SPY 6-month return) − 1."),
        ("rs_sec_3m", "Relative strength vs sector 3M", "pct", "(1 + 3-month return) / (1 + sector ETF 3-month return) − 1."),
        ("rs_sec_6m", "Relative strength vs sector 6M", "pct", "(1 + 6-month return) / (1 + sector ETF 6-month return) − 1."),
        ("mom_rank_3m", "3-month momentum rank", "pctl", "Percentile of the 3-month return across tracked companies (1 = strongest).")]:
    fld(fid, "px", n, u, d, PX)
CORR_D = [("c_spy", "SPY", "S&P 500 ETF"), ("c_qqq", "QQQ", "Nasdaq 100 ETF"), ("c_sector", None, "the company's sector SPDR ETF"),
          ("c_tnx", "^TNX", "the 10-year Treasury yield (daily change in yield)"), ("c_oil", "CL=F", "WTI crude oil futures"),
          ("c_gold", "GC=F", "gold futures"), ("c_dxy", "DX-Y.NYB", "the US dollar index"), ("c_btc", "BTC-USD", "bitcoin")]
for fid, sym, n in CORR_D:
    fld(fid, "corr", "Correlation to " + {"c_spy": "SPY", "c_qqq": "QQQ", "c_sector": "sector ETF", "c_tnx": "10Y yield", "c_oil": "oil", "c_gold": "gold", "c_dxy": "dollar", "c_btc": "bitcoin"}[fid],
        "corr", f"Pearson correlation of daily returns with {n}, last 252 trading days.", PX)
CORR_M = [("cm_cpi", "FRED:CPIAUCSL", "pct", "CPI", "monthly % change in CPI (all items)"), ("cm_core", "FRED:CPILFESL", "pct", "core CPI", "monthly % change in core CPI"),
          ("cm_unrate", "FRED:UNRATE", "diff", "unemployment", "monthly change in the unemployment rate"), ("cm_payems", "FRED:PAYEMS", "pct", "payrolls", "monthly % change in nonfarm payrolls"),
          ("cm_ffr", "FRED:DFF", "diff", "fed funds", "monthly change in the effective fed funds rate (month end)"),
          ("cm_2s10s", "FRED:T10Y2Y", "diff", "2s10s spread", "monthly change in the 10-year minus 2-year Treasury spread"),
          ("cm_10y", "TSY:10Y", "diff", "10Y Treasury (monthly)", "monthly change in the 10-year Treasury par yield"),
          ("cm_2y", "TSY:2Y", "diff", "2Y Treasury (monthly)", "monthly change in the 2-year Treasury par yield")]
for fid, ser, kind, n, d in CORR_M:
    fld(fid, "corr", "Correlation to " + n, "corr", f"Pearson correlation of monthly stock returns with the {d}, last 60 months.", "Monthly closes; FRED / U.S. Treasury via macro_observations")
fld("corr_n", "corr", "Correlation window (days)", "num", "Number of daily return pairs used for the daily correlations.", PX)
fld("corrm_n", "corr", "Correlation window (months)", "num", "Number of monthly return pairs used for the macro correlations.", PX)
for fid, n, u, d in [
        ("ins_net_90d", "Insider net buying 90d", "usd", "Open-market purchases minus sales by insiders (Form 4, codes P and S, non-derivative), last 90 days, in dollars."),
        ("ins_net_90d_n", "Insider net transactions 90d", "num", "Number of open-market purchases minus sales by insiders, last 90 days."),
        ("ins_buyers_90d", "Insider buyers 90d", "num", "Distinct individual officers and directors with an open-market purchase in the last 90 days (funds, companies and 10% holders not counted)."),
        ("ins_net_1y", "Insider net buying 1y", "usd", "Open-market purchases minus sales by insiders, last 365 days, in dollars."),
        ("ins_net_1y_n", "Insider net transactions 1y", "num", "Number of open-market purchases minus sales by insiders, last 365 days."),
        ("ins_buyers_1y", "Insider buyers 1y", "num", "Distinct individual officers and directors with an open-market purchase in the last 365 days (funds, companies and 10% holders not counted)."),
        ("news_30d", "News items 30d", "num", "Stored news items about the company published in the last 30 days."),
        ("days_10k", "Days since 10-K", "days", "Days since the latest 10-K was filed."),
        ("days_10q", "Days since 10-Q", "days", "Days since the latest 10-Q was filed."),
        ("days_8k", "Days since 8-K", "days", "Days since the latest 8-K was filed."),
        ("next_earn_days", "Next earnings (estimated)", "days", "Estimated days until the next earnings release: last results 8-K (Item 2.02) plus the median gap between recent ones. Negative means overdue. An estimate, not a company date.")]:
    fld(fid, "act", n, u, d, "SEC EDGAR (Form 4, 8-K, 10-K, 10-Q)" if fid != "news_30d" else "News search results stored by Filed Under")
KPI_FAM = [("units", "Unit volumes", r"units? (sold|shipped|delivered)|deliveries|shipments|unit sales|vehicles delivered"),
           ("customers", "Customers / subscribers", r"customers?\b(?! deposit)|subscribers?|members\b|memberships?|paid (seats|users)|monthly active|daily active|\bmau\b|\bdau\b|accounts\b|households"),
           ("arr", "Recurring revenue (ARR)", r"\barr\b|annual(ized)? recurring revenue|subscription revenue"),
           ("backlog", "Backlog / RPO", r"backlog|remaining performance obligation|\brpo\b|bookings|orders received|new orders"),
           ("comps", "Same-store sales", r"same[- ]store|comparable (store )?sales|comp(arable)? sales|like-for-like"),
           ("nim", "Net interest margin", r"net interest margin"),
           ("combined", "Combined ratio", r"combined ratio"),
           ("arpu", "Revenue per user", r"\barpu\b|average revenue per|revenue per (user|member|customer|subscriber|unit)"),
           ("production", "Production volumes", r"production|\bboe\b|barrels|\bmmcf|\bbcf"),
           ("occupancy", "Occupancy", r"occupancy|occupied"),
           ("stores", "Store / location count", r"store count|number of (stores|restaurants|locations|clubs|hotels)|stores open|total stores|restaurants|rooms\b"),
           ("retention", "Retention / churn", r"retention|churn|renewal rate"),
           ("loans", "Loans / deposits", r"total loans|loans and leases|average loans|total deposits|average deposits"),
           ("aum", "Assets under management", r"assets under (management|custody|administration|supervision)|\baum\b|\bauc\b"),
           ("installed", "Installed base", r"installed base")]
fld("kpi_count", "kpi", "Investor KPIs tracked", "num", "Number of KPIs collected from the company's earnings releases and investor documents.", "Company IR documents and 8-K exhibits (kpis/{T}.json)")
fld("seg_count", "kpi", "Business segments", "num", "Number of reportable business segments in the latest revenue-by-segment table (XBRL dimensions).", "SEC filings (XBRL segment dimensions)")
fld("seg_top_share", "kpi", "Largest segment share", "pct", "Largest business segment's share of total revenue, latest fiscal year (or latest quarter when no annual table).", "SEC filings (XBRL segment dimensions)")
fld("geo_count", "kpi", "Geographic regions", "num", "Number of geographic revenue lines in the latest geography table.", "SEC filings (XBRL segment dimensions)")
for k, n, _rx in KPI_FAM:
    fld(f"kpi_{k}", "kpi", "Reports " + n.lower(), "bool", f"The company's investor materials report a {n.lower()} KPI.", "Company IR documents (kpis/{T}.json)")
    fld(f"kpi_{k}_yoy", "kpi", n + " YoY", "pct", f"Latest reported {n.lower()} value vs the same period a year earlier − 1 (company-specific definition; first matching KPI).", "Company IR documents (kpis/{T}.json)")
    fld(f"kpi_{k}_v", "kpi", n + ", latest", "num", f"Latest reported {n.lower()} value in the company's own unit (see the source panel).", "Company IR documents (kpis/{T}.json)")
# standardized KPIs (etl/kpistd.py): one total-company series per company and measure, comparable across companies
FAM_CANON = {"units": ["units_shipped"], "customers": ["subscribers", "customers", "mau"], "arr": ["arr"], "backlog": ["backlog", "rpo"],
             "comps": ["same_store_sales_growth"], "nim": ["nim"], "combined": ["combined_ratio"], "arpu": ["arpu"], "production": ["production_boe"],
             "occupancy": ["occupancy"], "stores": ["store_count"], "retention": ["net_revenue_retention"], "loans": ["loans", "avg_loans", "deposits", "avg_deposits"],
             "aum": ["aum"]}
STD_FIELDS = ["same_store_sales_growth", "organic_growth", "revenue_growth_cc", "arr", "net_revenue_retention", "rpo", "backlog", "book_to_bill", "subscribers", "arpu",
              "nim", "efficiency_ratio", "cet1_ratio", "rotce", "net_charge_off_rate", "combined_ratio", "medical_care_ratio", "ffo_per_share", "occupancy",
              "load_factor", "production_boe", "adjusted_ebitda_margin", "adjusted_operating_margin", "fcf_margin", "net_leverage"]
STD_UNIT = {"%": "pct", "pp": "pct", "USD": "usd", "x": "x"}
try:
    from .kpi_taxonomy import BY_ID as _STD
except Exception:   # the taxonomy ships with the kpistd workstream; without it the fields are simply absent
    _STD = {}
for cid in STD_FIELDS:
    t = _STD.get(cid)
    if not t:
        continue
    u = STD_UNIT.get(t["unit"], "num")
    fld(f"std_{cid}", "kpi", t["name"] + " (standardized)", u, t["definition"] + " Latest reported value; the company's own KPI series mapped to this standard measure (one series, never joined across reporting bases).",
        "Company IR documents (kpistd/t/{T}.json)")
    fld(f"std_{cid}_yoy", "kpi", t["name"] + (" change YoY" if t["unit"] == "%" else " YoY"), "pct",
        ("Latest value minus the value a year earlier, in percentage points." if t["unit"] == "%" else "Latest value over the value a year earlier − 1.") + " Standardized series.",
        "Company IR documents (kpistd/t/{T}.json)")
FIELD_IDS = [f["id"] for f in F]
ID_COLS = ["ticker", "name", "sector", "industry", "sp100", "sp500", "cik"]


# ------------------------------------------------------------------ small helpers
def sig(v, n=4):
    if v is None or isinstance(v, bool):
        return v
    if isinstance(v, int):
        return v
    if not math.isfinite(v):
        return None
    if v == 0:
        return 0
    d = n - int(math.floor(math.log10(abs(v)))) - 1
    r = round(v, d)
    return int(r) if d <= 0 else r


def D(s):
    return dt.date.fromisoformat(s)


def days_between(a, b):
    return (D(b) - D(a)).days


def safe_div(a, b):
    if a is None or b is None or b == 0:
        return None
    return a / b


def pearson(a, b):
    n = len(a)
    if n < 10:
        return None
    ma, mb = sum(a) / n, sum(b) / n
    xa = [x - ma for x in a]
    xb = [x - mb for x in b]
    va, vb = sum(map(mul, xa, xa)), sum(map(mul, xb, xb))
    if va <= 0 or vb <= 0:
        return None
    return sum(map(mul, xa, xb)) / math.sqrt(va * vb)


def gz_json(sb, path):
    raw = sb.get_object("raw", path)
    if not raw:
        return None
    try:
        return json.loads(gzip.decompress(raw))
    except OSError:
        return json.loads(raw)


def put_gz(sb, path, obj):
    sb.put_object("raw", path, gzip.compress(json.dumps(obj, separators=(",", ":")).encode()), "application/gzip", cache="no-store")


# ------------------------------------------------------------------ close history
def yahoo_daily(sym, rng):
    ysym = sym.replace(".", "-") if sym in BY_TICKER else sym
    url = f"{YAHOO}{urllib.parse.quote(ysym)}?range={rng}&interval=1d&events=split&includePrePost=false"
    for attempt in range(4):
        try:
            j = get_json(url, timeout=40, retries=2)
            break
        except HTTPError as e:
            if e.code == 429 and attempt < 3:
                time.sleep(5 * (attempt + 1))
                continue
            raise
    r = j["chart"]["result"][0]
    q = r["indicators"]["quote"][0]
    out = {}
    for i, t in enumerate(r.get("timestamp") or []):
        c = q["close"][i]
        if c is not None:
            out[dt.datetime.fromtimestamp(t, dt.timezone.utc).date().isoformat()] = round(c, 4)
    splits = []
    for s in ((r.get("events") or {}).get("splits") or {}).values():
        if s.get("numerator") and s.get("denominator"):
            splits.append([dt.datetime.fromtimestamp(s["date"], dt.timezone.utc).date().isoformat(), s["numerator"] / s["denominator"]])
    return out, sorted(splits)


def _from_price_file(sb, sym):
    try:
        b = sb.get_json(f"prices/{path_key(sym)}.json")
    except Exception:
        return None
    if not b:
        return None
    return {dt.datetime.fromtimestamp(x[0], dt.timezone.utc).date().isoformat(): x[4] for x in b.get("d") or []}


def update_closes(sb, full=False, log=print, deadline=None):
    """10-year daily closes for every symbol, aligned to SPY trading days. Full mode fetches from Yahoo; otherwise the
    cache is extended from the prices job's files, and any symbol whose history no longer lines up (a split re-based
    the series) is fetched again in full."""
    cache = None if full else gz_json(sb, CLOSES_PATH)
    series, splits, fetched = {}, {}, {}
    if cache:
        for s, arr in cache["s"].items():
            series[s] = {d: v for d, v in zip(cache["dates"], arr) if v is not None}
        splits, fetched = cache.get("splits", {}), cache.get("fetched", {})
    refetch = [s for s in HIST_SYMS if s not in series]
    if cache:
        def fresh(s):
            return s, (_from_price_file(sb, s) if s not in EXTRA_SYMS else None)
        with ThreadPoolExecutor(12) as ex:
            for s, recent in ex.map(fresh, [s for s in HIST_SYMS if s in series]):
                if s in EXTRA_SYMS:
                    try:
                        recent, _sp = yahoo_daily(s, "3mo")
                    except Exception as e:
                        log("closes", s, str(e)[:120])
                        continue
                if not recent:
                    continue
                old = series[s]
                common = sorted(set(old) & set(recent))
                probe = common[-25:-5] or common[-5:]
                drift = [abs(old[d] / recent[d] - 1) for d in probe if old[d] and recent[d]]
                if drift and max(drift) > 0.005:
                    refetch.append(s)
                    continue
                old.update(recent)
    def fetch(s):
        if deadline and time.time() > deadline:
            return s, None, "deadline"
        time.sleep(0.35)
        try:
            closes, sp = yahoo_daily(s, "10y")
            return s, (closes, sp), None
        except Exception as e:
            return s, None, str(e)[:160]

    got = 0
    with ThreadPoolExecutor(3) as ex:
        for s, res, err in ex.map(fetch, refetch):
            if res:
                series[s], splits[s] = res
                fetched[s] = now_iso()
                got += 1
                if got % 50 == 0:
                    log(f"closes: fetched {got} of {len(refetch)}")
            elif err != "deadline":
                log("closes fetch failed", s, err)
    spy = series.get("SPY") or {}
    cut = (dt.date.today() - dt.timedelta(days=int(365.25 * HIST_YEARS))).isoformat()
    dates = sorted(d for d in spy if d >= cut)
    out = {"dates": dates, "s": {}, "splits": splits, "fetched": fetched, "updated": now_iso()}
    for s, m in series.items():
        out["s"][s] = [m.get(d) for d in dates]
    put_gz(sb, CLOSES_PATH, out)
    log(f"closes: {len(out['s'])} symbols, {len(dates)} days ({dates[0] if dates else '-'} to {dates[-1] if dates else '-'}), refetched {len(refetch)}")
    return out


# ------------------------------------------------------------------ fact series per company (cached by filing)
def _rec(r, concept):
    rec = [r["end"], r.get("start") or "", r["v"], r["accn"], r.get("filed") or "", r.get("form") or ""]
    c = r.get("c") or concept
    rec.append(c)
    rec.append(1 if r.get("derived") else 0)
    rec.append({k:r[k] for k in ("available_at", "validation_status", "derived", "selection_reason", "selection_conflict", "superseded_at") if k in r})
    return rec


def _share_observation_key(record):
    value = record.get('val')
    reported = (record.get('basis') == 'reported_outstanding' and not record.get('derived')
                and type(value) in (int, float) and math.isfinite(value) and value > 0)
    return (reported, record.get('end') or '', record.get('available_at') or record.get('filed') or '', record.get('accn') or '')


_SOURCE_KEYS = ("v", "start", "end", "filed", "available_at", "accn", "c")


def _compact_source(x):
    """A derivation leg kept to what period checks and amount comparisons read; nested legs likewise."""
    if not isinstance(x, dict):
        return x
    out = {k: x[k] for k in _SOURCE_KEYS if k in x}
    if isinstance(x.get("derived"), dict):
        out["derived"] = {k: (_compact_source(v) if k in ("from", "minus") else v) for k, v in x["derived"].items()}
    return out


def _compact(rec):
    """Fund-cache record without the repeated source documents inside a derivation (the cache for ~1,400 companies
    otherwise outgrows the function's memory)."""
    if len(rec) > 8 and isinstance(rec[8].get("derived"), dict):
        d = rec[8]["derived"]
        rec[8] = dict(rec[8], derived={k: (_compact_source(v) if k in ("from", "minus") else v) for k, v in d.items()})
    return rec


def _dedupe_history(q, history):
    """Filing-history rows identical to the latest-vintage row are not stored twice; Facts rebuilds the full history
    for point-in-time reads from q plus these extra rows."""
    out = {}
    for lab, rows in history.items():
        latest = {json.dumps(r, sort_keys=True, default=str) for r in q.get(lab, [])}
        out[lab] = [r for r in rows if json.dumps(r, sort_keys=True, default=str) not in latest]
    return out


def extract_company(sb, tk, since):
    """Quarterly statement records since `since` for the screener labels, plus extra concepts from companyfacts."""
    b = sb.get_json(f"companies/{tk}.json") or {}
    if not fundamentals.normalized(b):
        raise ValueError(f"{tk}: company bundle is not normalized; withholding financial cache")
    st = b.get("statements") or {}
    q, history, fy = {}, {}, {}
    for stmt in ("income", "balance", "cashflow"):
        for row in st.get(stmt, []):
            lab = row["label"]
            if lab not in LABELS or (lab == "Net income" and stmt == "cashflow"):
                continue
            if STMT_OF.get(lab, "income") != stmt:
                continue
            q[lab] = [_compact(_rec(r, row["concept"])) for r in row["quarterly"] if r["end"] >= since]
            history[lab] = [_compact(_rec(r, row["concept"])) for r in (row.get("filing_history") or {}).get("quarterly", []) if r["end"] >= since]
            if lab not in INSTANT and lab not in SHARE_COUNT:
                # Annual values close the fourth fiscal quarter, which 10-Ks do not report on its own.
                # Latest-vintage rows only (no filing history): point-in-time reads filter them by filing date.
                fy[lab] = [_rec(r, row["concept"])[:8] for r in row.get("annual") or [] if r["end"] >= since]
    prof = b.get("profile") or {}
    shares = prof.get("sharesOutstanding") or {}
    out = {"ticker": tk, "pipeline_version": b["pipeline_version"], "q": q, "filing_history": history, "fy": fy,
           "fye": prof.get("fiscalYearEnd"),
           "valuation_basis": fundamentals.valuation_hold(tk, prof.get("valuation_basis")),
           "shares": dict(shares, v=shares.get("val"))}
    cik = BY_TICKER[tk]["cik"]
    strict = tk in STRICT_SOURCE_TICKERS
    cf_raw = None
    if strict:
        # Additional-company values must use the same archived snapshot that
        # passed the company proof; missing or changed sources fail closed.
        cf_raw = gzip.decompress(sb.get_object("raw", f"sec/companyfacts/CIK{cik:010d}.json.gz"))
        cf = json.loads(cf_raw)
    else:
        try:
            cf = gz_json(sb, f"sec/companyfacts/CIK{cik:010d}.json.gz")
        except Exception:
            cf = None
    if cf:
        facts = cf.get("facts") or {}
        want = {n for _l, names in EXTRA_FLOW + EXTRA_BAL for n in names}
        # only the concepts used here, and only those with reported units (a few filers carry empty unit maps)
        facts = {"us-gaap": {n: v for n, v in (facts.get("us-gaap") or {}).items() if n in want and (v.get("units") or {})}, "dei": facts.get("dei") or {}}
        extra = {stmt:sec.build_series(facts, stmt, concepts=concepts, instant=instant)
                 for stmt,concepts,instant in (("income",EXTRA_FLOW,False),("balance",EXTRA_BAL,True))}
        if strict:
            from .source_quality import partition_supported_periods
            from .source_provenance import validate_statement_provenance, proof_object_path
            extra, excluded = partition_supported_periods(extra)
            sha = b["provenance"]["snapshot_sha256"]
            proof = validate_statement_provenance(cf_raw,extra,cik,sha,
                                                  taxonomy={"income":EXTRA_FLOW,"balance":EXTRA_BAL})
            proof_path = proof_object_path(tk, proof, scope='screener')
            sb.put_json(proof_path,proof,cache="max-age=31536000,immutable")
            out["source_provenance"] = {"status":"verified","snapshot_sha256":sha,"proof_path":proof_path,
                                         "counts":proof["counts"],"quarantined_points":excluded}
        for rows in extra.values():
            for row in rows:
                q[row["label"]] = [_compact(_rec(r, row["concept"])) for r in row["quarterly"] if r["end"] >= since]
                history[row["label"]] = [_compact(_rec(r, row["concept"])) for r in row["filing_history"]["quarterly"] if r["end"] >= since]
        # A newer filing/iXBRL profile count may precede its DEI appearance in
        # Company Facts. Do not regress it to an older archived observation.
        reported = sec.shares_outstanding(facts) or {}
        if _share_observation_key(reported) > _share_observation_key(shares):
            shares = reported
        out["shares"] = dict(shares, v=shares.get("val"))
    out["filing_history"] = _dedupe_history(q, history)
    out["history_dedup"] = 1
    return out


def update_fund(sb, fundmeta, closes, full=False, workers=6, log=print, deadline=None):
    cache = (None if full else gz_json(sb, FUND_PATH)) or {}
    if cache.get("_v") != FUND_VERSION:
        cache = {"_v": FUND_VERSION}
    since = (dt.date.today() - dt.timedelta(days=int(365.25 * 7.3))).isoformat()
    todo, invalidated = [], False
    for tk in TICKERS:
        fm = fundmeta.get(tk) or {}
        if not fundamentals.normalized(fm):
            invalidated = cache.pop(tk, None) is not None or invalidated
            continue
        # Same-filing normalization and corrected share dates must invalidate
        # the cache even when the accession, quarter and share value did not change.
        key = json.dumps([fm["pipeline_version"], fm.get("material_accession"),
                          (fm.get("fund") or {}).get("latest_quarter_end"),
                          fm.get("shares_metadata")], sort_keys=True, separators=(",", ":"))
        if (cache.get(tk) or {}).get("key") != key or not fundamentals.normalized(cache.get(tk)):
            invalidated = cache.pop(tk, None) is not None or invalidated
            todo.append((tk, key))
        elif "fy" not in cache[tk]:
            # Entries extracted before annual rows were kept ("fy") are re-read from the same bundle; the current
            # entry stays in use until the re-read lands, so a run that hits its deadline loses nothing.
            todo.append((tk, key))
    log(f"fund cache: {len(todo)} companies to (re)extract")

    def one(item):
        tk, key = item
        if deadline and time.time() > deadline:
            return tk, None, "deadline"
        try:
            x = extract_company(sb, tk, since)
            x["key"] = key
            return tk, x, None
        except Exception as e:
            return tk, None, str(e)[:200]

    done = 0
    with ThreadPoolExecutor(workers) as ex:
        for tk, x, err in ex.map(one, todo):
            if x:
                cache[tk] = x
                done += 1
            elif err != "deadline":
                log("fund extract failed", tk, err)
    if todo or invalidated:
        put_gz(sb, FUND_PATH, cache)
    log(f"fund cache: extracted {done}")
    return cache


# ------------------------------------------------------------------ company calculations
class Prov:
    """Provenance for one company: deduplicated input records plus, per field, a formula and input references."""

    def __init__(self):
        self.refs, self.idx, self.f = [], {}, {}

    def ref(self, label, rec):
        key = (label, rec[0], rec[3])
        if key not in self.idx:
            self.idx[key] = len(self.refs)
            self.refs.append([STMT_OF.get(label, "income"), label, rec[0], rec[1], rec[2], rec[3], rec[5], rec[4], rec[6], rec[7], rec[8] if len(rec)>8 else {}])
        return self.idx[key]

    def put(self, fid, formula, recs=(), **kw):
        d = {"x": formula}
        ii = [self.ref(lab, r) for lab, r in recs if r]
        if ii:
            d["i"] = sorted(set(ii))
        d.update({k: v for k, v in kw.items() if v is not None})
        self.f[fid] = d


def _contiguous_start(r):
    """A quarter derived as year-to-date minus the earlier year-to-date starts the day after the earlier period ends.
    Some company bundles carry the earlier period's end date as the start instead, which breaks the four-consecutive-
    quarter check behind every trailing-twelve-month figure; restore the real start."""
    meta = r[8] if len(r) > 8 and isinstance(r[8], dict) else {}
    minus = (meta.get("derived") or {}).get("minus") if isinstance(meta.get("derived"), dict) else None
    if r[7] and r[1] and minus and minus.get("end") == r[1]:
        r = list(r)
        r[1] = (D(r[1]) + dt.timedelta(days=1)).isoformat()
    return r


class Facts:
    def __init__(self, data, splits, as_of=None):
        self.data, self.as_of = data, as_of
        self.valuation_basis = fundamentals.valuation_hold(data.get("ticker"), data.get("valuation_basis"), as_of)
        source = data.get("filing_history") if as_of else data.get("q")
        if as_of and data.get("history_dedup"):
            hist = data.get("filing_history") or {}
            source = {lab: list((data.get("q") or {}).get(lab, [])) + list(hist.get(lab, [])) for lab in set(data.get("q") or {}) | set(hist)}
        self.q = {}
        self.comparability_holds = []
        visible_ends = set()
        for lab, records in (source or {}).items():
            picked, visible = {}, {}
            for r in map(_contiguous_start, records):
                if (lab in SHARE_COUNT or lab in PER_SHARE) and r[7]:
                    continue
                available = (r[8].get("available_at") if len(r)>8 else None) or r[4]
                superseded = r[8].get("superseded_at") if len(r)>8 else None
                if superseded and (not as_of or superseded <= as_of):
                    continue
                if as_of and (not available or available > as_of):
                    continue
                visible.setdefault(r[0], []).append(r)
                prev = picked.get(r[0])
                if not prev or (available, not bool(r[7])) > (((prev[8].get("available_at") if len(prev)>8 else None) or prev[4]), not bool(prev[7])):
                    picked[r[0]] = r
            # A held value is still evidence of the reporting period. Removing
            # that date would silently recalculate current metrics one quarter
            # earlier. Availability and supersession were checked above.
            if lab in ("Net income", "Revenue"):
                visible_ends.update(visible)
            for end, record in list(picked.items()):
                def point(r):
                    metadata = r[8] if len(r) > 8 else {}
                    p = dict(metadata, end=r[0], start=r[1] or None, v=r[2], accn=r[3],
                             filed=r[4], form=r[5], c=r[6])
                    if r[7] and not p.get('derived'):
                        p['derived'] = True
                    return p
                selected = sec.prefer_reported_observation(point(record), [point(r) for r in visible[end]])
                if selected.get('selection_conflict'):
                    self.comparability_holds.append({'label': lab, 'point': selected,
                                                     **selected['selection_conflict']})
                    del picked[end]
                elif selected != point(record):
                    picked[end] = _rec(selected, selected['c'])
            self.q[lab] = sorted(picked.values(), key=lambda r:r[0])
        self.splits = splits or []
        self.ends = sorted(visible_ends)
        self.fy = {}
        for lab, records in (data.get("fy") or {}).items():
            picked = {}
            for r in records:
                available = (r[8].get("available_at") if len(r) > 8 else None) or r[4]
                if r[7] or (as_of and (not available or available > as_of)):
                    continue
                if r[0] not in picked or available > ((picked[r[0]][8].get("available_at") if len(picked[r[0]]) > 8 else None) or picked[r[0]][4]):
                    picked[r[0]] = r
            self.fy[lab] = sorted(picked.values(), key=lambda r: r[0])
        self.q_aug = {lab: self._with_q4(lab) for lab in self.fy}

    def _held(self, label, start, end):
        """A quarter withheld for conflicting observations must not come back through the annual statement."""
        return any(h["label"] == label and start <= h["point"]["end"] <= end for h in self.comparability_holds)

    def _with_q4(self, label):
        """Quarters plus each missing fourth quarter as the reported annual value minus the three quarters of that
        fiscal year (the 10-K states the year, not the quarter). Kept apart from self.q so
        quarter-on-quarter fields still use reported quarters only."""
        quarters = list(self.q.get(label, []))
        have = {r[0] for r in quarters}
        if not quarters:
            return quarters
        for a in self.fy.get(label, []):
            if not a[1] or any(abs(days_between(e, a[0])) <= 7 for e in have) or self._held(label, a[1], a[0]):
                continue
            inside = [r for r in quarters if r[1] and a[1] <= r[1] and r[0] < a[0]]
            if len(inside) != 3 or not fundamentals.consecutive_quarters(
                    [{"start": r[1], "end": r[0]} for r in inside] + [{"start": (D(inside[-1][0]) + dt.timedelta(days=1)).isoformat(), "end": a[0]}]) \
                    or inside[0][1] != a[1]:
                continue
            parts = [self.adj(label, a)] + [self.adj(label, r) for r in inside]
            if any(x is None for x in parts):
                continue
            v = parts[0] - sum(parts[1:])
            meta = dict(a[8] if len(a) > 8 else {}, derived={"from": {"v": a[2], "start": a[1], "end": a[0], "accn": a[3], "form": a[5]},
                                                             "minus": [{"v": r[2], "start": r[1], "end": r[0], "accn": r[3]} for r in inside],
                                                             "basis": "annual minus three reported quarters"})
            # stored on today's split basis already: no split date applies to it again
            quarters.append([a[0], (D(inside[-1][0]) + dt.timedelta(days=1)).isoformat(), v, a[3], "", a[5], a[6], 1, meta])
        return sorted(quarters, key=lambda r: r[0])

    def adj(self, label, rec):
        """Split-adjusted value: per-share figures and share counts filed before a split are restated to today's basis."""
        if not rec:
            return None
        if label in PER_SHARE and fundamentals.basis_blocks(self.valuation_basis, "eps_ttm"):
            return None
        v = abs(rec[2]) if label in OUTFLOWS else rec[2]
        if label in PER_SHARE or label in SHARE_COUNT:
            f = 1.0
            for d, ratio in self.splits:
                if rec[4] and d > rec[4]:
                    f *= ratio
            v = v / f if label in PER_SHARE else v * f
        return v

    def at(self, label, end, tol=20):
        best = None
        for r in self.q.get(label, []):
            dd = abs(days_between(r[0], end))
            if dd <= tol and (best is None or dd < best[0]):
                best = (dd, r)
        return best[1] if best else None

    def shift(self, end, quarters):
        return (D(end) - dt.timedelta(days=round(91.31 * quarters))).isoformat()

    def ttm(self, label, end):
        """Sum of the four quarters ending at `end` and the records used."""
        if label in INSTANT or label in SHARE_COUNT:
            return None, []
        if label in PER_SHARE and fundamentals.basis_blocks(self.valuation_basis, "eps_ttm"):
            return None, []
        # Historical growth queries shift calendar days; resolve that request to
        # the actual common fiscal quarter before requiring exact coverage.
        if self.ends:
            nearest = min(self.ends, key=lambda value: abs(days_between(value, end)))
            if abs(days_between(nearest, end)) <= 20:
                end = nearest
        if label in PER_SHARE:
            # Per-share amounts do not add up across a year exactly: when the window is a fiscal year, use the
            # annual value as reported.
            a = next((r for r in self.fy.get(label, []) if abs(days_between(r[0], end)) <= 7 and r[1]), None)
            if a and 350 <= days_between(a[1], a[0]) <= 380 and not self._held(label, a[1], a[0]):
                return self.adj(label, a), [a]
        # Fiscal quarters need not be 91 days apart (retailers may report a
        # 16-week final quarter). Select actual records, then prove continuity.
        eligible = [r for r in (self.q_aug.get(label) or self.q.get(label, [])) if r[0] <= end]
        recs = list(reversed(eligible[-4:]))
        if len(recs) != 4 or recs[0][0] != end or not fundamentals.consecutive_quarters([{ "start":r[1], "end":r[0]} for r in recs]):
            # A window that is exactly a fiscal year can still be read from the annual statement.
            a = next((r for r in self.fy.get(label, []) if abs(days_between(r[0], end)) <= 7 and r[1]), None)
            if a and 350 <= days_between(a[1], a[0]) <= 380 and self.adj(label, a) is not None and not self._held(label, a[1], a[0]):
                return self.adj(label, a), [a]
            return None, []
        return sum(self.adj(label, r) for r in recs), recs

    def fcf_ttm(self, end):
        o, ro = self.ttm("Operating cash flow", end)
        c, rc = self.ttm("Capex", end)
        if o is None or c is None or [(r[0],r[1]) for r in ro] != [(r[0],r[1]) for r in rc]:
            return None, []
        return o - c, [("Operating cash flow", r) for r in ro] + [("Capex", r) for r in rc]

    def fcf_q(self, end):
        o, c = self.at("Operating cash flow", end), self.at("Capex", end)
        if not o or not c or (o[0],o[1]) != (c[0],c[1]):
            return None, []
        return o[2] - abs(c[2]), [("Operating cash flow", o), ("Capex", c)]

    def bal(self, label, end, tol=20):
        r = self.at(label, end, tol)
        return (r[2], r) if r else (None, None)


def growth_block(fx, out, prov, key, label, end):
    """Growth fields for one metric: quarter YoY, TTM YoY, 3y/5y CAGR, acceleration and streak."""
    def qv(e):
        if key == "fcf":
            v, recs = fx.fcf_q(e)
            return v, recs
        r = fx.at(label, e)
        return (fx.adj(label, r), [(label, r)]) if r else (None, [])

    def ttmv(e):
        if key == "fcf":
            return fx.fcf_ttm(e)
        v, recs = fx.ttm(label, e)
        return v, [(label, r) for r in recs]

    def yoy(e):
        a, ra = qv(e)
        b, rb = qv(fx.shift(e, 4))
        if a is None or b is None or b <= 0:
            return None, ra + rb
        return a / b - 1, ra + rb

    g, recs = yoy(end)
    out[f"{key}_g_q"] = g
    if g is not None:
        prov.put(f"{key}_g_q", f"{label} latest quarter / same quarter a year earlier − 1", recs)
    t0, r0 = ttmv(end)
    t1, r1 = ttmv(fx.shift(end, 4))
    if t0 is not None and t1 and t1 > 0:
        out[f"{key}_g_ttm"] = t0 / t1 - 1
        prov.put(f"{key}_g_ttm", f"Sum of the last four quarters of {label} / sum of the four before − 1", r0 + r1)
    for yrs in (3, 5):
        tb, rb = ttmv(fx.shift(end, 4 * yrs))
        if t0 is not None and tb and tb > 0 and t0 > 0:
            out[f"{key}_cagr{yrs}"] = (t0 / tb) ** (1 / yrs) - 1
            prov.put(f"{key}_cagr{yrs}", f"(Trailing four quarters {label} now / {yrs} years earlier)^(1/{yrs}) − 1", r0 + rb)
    gp, rp = yoy(fx.shift(end, 1))
    if g is not None and gp is not None:
        out[f"{key}_accel"] = g - gp
        prov.put(f"{key}_accel", f"{label} YoY growth this quarter − YoY growth last quarter", recs + rp)
    streak, srecs = 0, []
    for k in range(0, 24):
        gk, rk = yoy(fx.shift(end, k))
        if gk is None or gk <= 0:
            break
        streak += 1
        srecs += rk
    if g is not None or streak:
        out[f"{key}_streak"] = streak
        prov.put(f"{key}_streak", f"Quarters in a row with {label} above the same quarter a year earlier", srecs[:16])


def fundamentals_for(tk, data, splits, price, sector):
    """All fact-based fields that do not depend on price history; returns (values, provenance, valuation inputs)."""
    # Warm caches may predate the embedded issuer/policy fields; the caller's
    # canonical ticker still applies current sourced valuation holds.
    fx = Facts(dict(data, ticker=tk), splits)
    out, prov = {}, Prov()
    if fundamentals.basis_blocks(fx.valuation_basis, "eps_ttm"):
        for field in ("pe", "earn_yield"):
            prov.put(field, fx.valuation_basis.get("reason") or "A consistent share basis is required",
                     status="held", valuation_basis=fx.valuation_basis)
    if not fx.ends:
        return out, prov, fx, None
    end = fx.ends[-1]
    fin = sector == "Financials"
    rev, rrev = fx.ttm("Revenue", end)
    ni, rni = fx.ttm("Net income", end)
    oi, roi = fx.ttm("Operating income", end)
    eps, reps = fx.ttm("EPS diluted", end)
    fcf, rfcf = fx.fcf_ttm(end)
    ocf, rocf = fx.ttm("Operating cash flow", end)
    capex, rcap = fx.ttm("Capex", end)
    gp, rgp = fx.ttm("Gross profit", end)
    if gp is None:
        cogs, rcogs = fx.ttm("Cost of revenue", end)
        if cogs is not None and rev is not None:
            gp, rgp = rev - cogs, [("Revenue", r) for r in rrev] + [("Cost of revenue", r) for r in rcogs]
        else:
            rgp = []
    else:
        rgp = [("Gross profit", r) for r in rgp]
    L = lambda lab, recs: [(lab, r) for r in recs]
    da, rda = fx.ttm("D&A", end)
    sbc, rsbc = fx.ttm("Stock-based comp", end)
    rnd, rrnd = fx.ttm("Research & development", end)
    div, rdiv = fx.ttm("Dividends paid", end)
    bb, rbb = fx.ttm("Buybacks", end)
    tax, rtax = fx.ttm("Income tax", end)
    pre, rpre = fx.ttm("Pretax income", end)
    intx, rint = fx.ttm("Interest expense", end)
    eq, req = fx.bal("Shareholders' equity", end)
    ta, rta = fx.bal("Total assets", end)
    tl, rtl = fx.bal("Total liabilities", end)
    tca, rtca = fx.bal("Total current assets", end)
    tcl, rtcl = fx.bal("Total current liabilities", end)
    cash, rcash = fx.bal("Cash & equivalents", end)
    ms, rms = fx.bal("Marketable securities, current", end)
    ltd, rltd = fx.bal("Long-term debt", end)
    gw, rgw = fx.bal("Goodwill", end)
    re_, rre = fx.bal("Retained earnings", end)
    eq1, req1 = fx.bal("Shareholders' equity", fx.shift(end, 4))
    ta1, rta1 = fx.bal("Total assets", fx.shift(end, 4))

    # total debt: long-term debt + current debt (DebtCurrent, else current portion of LTD + short-term borrowings or commercial paper)
    debt_recs, cur = [], None
    dc, rdc = fx.bal("Debt, current", end)
    if dc is not None:
        cur, debt_recs = dc, [("Debt, current", rdc)]
    else:
        parts = []
        if not (rltd and rltd[6] == "LongTermDebt"):
            v, r = fx.bal("Long-term debt, current", end)
            if v is not None:
                parts.append(("Long-term debt, current", v, r))
        sb_, rsb = fx.bal("Short-term borrowings", end)
        cp, rcp = fx.bal("Commercial paper", end)
        pick = max([x for x in [("Short-term borrowings", sb_, rsb), ("Commercial paper", cp, rcp)] if x[1] is not None], key=lambda x: x[1], default=None)
        if pick:
            parts.append(pick)
        if parts:
            cur = sum(p[1] for p in parts)
            debt_recs = [(p[0], p[2]) for p in parts]
    debt = None
    if ltd is not None or cur is not None:
        debt = (ltd or 0) + (cur or 0)
        debt_recs = ([("Long-term debt", rltd)] if rltd else []) + debt_recs
    cashall = (cash or 0) + (ms or 0) if cash is not None else None
    cash_recs = [("Cash & equivalents", rcash)] + ([("Marketable securities, current", rms)] if rms else [])

    tot = lambda a, b: a + b if a is not None and b is not None else None
    if debt is not None and cashall is not None:
        out["net_debt"] = debt - cashall
        prov.put("net_debt", "Total debt − (cash & equivalents + current marketable securities)", debt_recs + cash_recs)
    # margins
    for k, num, recs in (("gm", gp, rgp), ("om", oi, L("Operating income", roi)), ("nm", ni, L("Net income", rni)), ("fcfm", fcf, rfcf)):
        if num is not None and rev:
            out[k] = num / rev
            prov.put(k, {"gm": "Gross profit / revenue", "om": "Operating income / revenue", "nm": "Net income / revenue", "fcfm": "(Operating cash flow − capex) / revenue"}[k] + ", trailing four quarters", recs + L("Revenue", rrev))
    e3 = fx.shift(end, 12)
    rev3, rrev3 = fx.ttm("Revenue", e3)
    for k, lab in (("gm", "Gross profit"), ("om", "Operating income"), ("nm", "Net income"), ("fcfm", None)):
        if k not in out or not rev3:
            continue
        if k == "fcfm":
            n3, r3 = fx.fcf_ttm(e3)
        elif k == "gm":
            n3, r3x = fx.ttm("Gross profit", e3)
            r3 = L("Gross profit", r3x)
            if n3 is None:
                c3, rc3 = fx.ttm("Cost of revenue", e3)
                n3, r3 = (rev3 - c3, L("Cost of revenue", rc3)) if c3 is not None else (None, [])
        else:
            n3, r3x = fx.ttm(lab, e3)
            r3 = L(lab, r3x)
        if n3 is not None:
            out[k + "_chg3"] = out[k] - n3 / rev3
            prov.put(k + "_chg3", "Margin (trailing four quarters) now − three years earlier", r3 + L("Revenue", rrev3))
    if ni is not None and eq is not None and eq > 0:
        avg = (eq + eq1) / 2 if eq1 and eq1 > 0 else eq
        out["roe"] = ni / avg
        prov.put("roe", "Net income (trailing four quarters) / average shareholders' equity", L("Net income", rni) + [("Shareholders' equity", req), ("Shareholders' equity", req1)])
    if ni is not None and ta:
        avg = (ta + ta1) / 2 if ta1 else ta
        out["roa"] = ni / avg
        prov.put("roa", "Net income (trailing four quarters) / average total assets", L("Net income", rni) + [("Total assets", rta), ("Total assets", rta1)])
        if ocf is not None:
            out["accruals"] = (ni - ocf) / avg
            prov.put("accruals", "(Net income − operating cash flow) / average total assets", L("Net income", rni) + L("Operating cash flow", rocf) + [("Total assets", rta), ("Total assets", rta1)])
    if oi is not None and eq is not None and debt is not None and cashall is not None:
        rate = 0.21
        if tax is not None and pre and pre > 0:
            rate = min(0.5, max(0.0, tax / pre))
        ic = eq + debt - cashall
        if ic > 0:
            out["roic"] = oi * (1 - rate) / ic
            prov.put("roic", f"Operating income × (1 − tax rate {rate:.1%}) / (equity + total debt − cash)", L("Operating income", roi) + L("Income tax", rtax) + L("Pretax income", rpre) + [("Shareholders' equity", req)] + debt_recs + cash_recs)
    if ocf is not None and ni and ni > 0:
        out["cash_conv"] = ocf / ni
        prov.put("cash_conv", "Operating cash flow / net income, trailing four quarters", L("Operating cash flow", rocf) + L("Net income", rni))
    for k, v, lab, recs in (("sbc_pct", sbc, "Stock-based comp", rsbc), ("capex_int", capex, "Capex", rcap), ("rnd_int", rnd, "Research & development", rrnd)):
        if v is not None and rev:
            out[k] = v / rev
            prov.put(k, f"{lab} / revenue, trailing four quarters", L(lab, recs) + L("Revenue", rrev))
    ebitda = oi + da if oi is not None and da is not None else None
    if not fin and out.get("net_debt") is not None and ebitda and ebitda > 0:
        out["nd_ebitda"] = out["net_debt"] / ebitda
        prov.put("nd_ebitda", "Net debt / (operating income + D&A), trailing four quarters", debt_recs + cash_recs + L("Operating income", roi) + L("D&A", rda))
    if debt is not None and eq and eq > 0:
        out["de"] = debt / eq
        prov.put("de", "Total debt / shareholders' equity", debt_recs + [("Shareholders' equity", req)])
    if oi is not None and intx and intx > 0:
        out["int_cov"] = oi / intx
        prov.put("int_cov", "Operating income / interest expense, trailing four quarters", L("Operating income", roi) + L("Interest expense", rint))
    if tca is not None and tcl:
        out["current_ratio"] = tca / tcl
        prov.put("current_ratio", "Total current assets / total current liabilities", [("Total current assets", rtca), ("Total current liabilities", rtcl)])
    if gw is not None and ta:
        out["gw_assets"] = gw / ta
        prov.put("gw_assets", "Goodwill / total assets", [("Goodwill", rgw), ("Total assets", rta)])
    elif ta and fx.q.get("Total assets") and not fx.q.get("Goodwill"):
        out["gw_assets"] = 0.0
        prov.put("gw_assets", "No goodwill reported on the balance sheet", [("Total assets", rta)])
    sh = fx.at("Diluted shares", end)
    for k, n in (("shr_chg1", 4), ("shr_chg3", 12)):
        sp = fx.at("Diluted shares", fx.shift(end, n))
        if sh and sp and fx.adj("Diluted shares", sp):
            out[k] = fx.adj("Diluted shares", sh) / fx.adj("Diluted shares", sp) - 1
            prov.put(k, f"Diluted weighted shares latest quarter / {n // 4} year(s) earlier − 1 (split-adjusted)", [("Diluted shares", sh), ("Diluted shares", sp)])

    # Piotroski: trailing four quarters vs the four before; balance sheet now vs a year earlier
    e1 = fx.shift(end, 4)
    ni1, rni1 = fx.ttm("Net income", e1)
    rev1, rrev1 = fx.ttm("Revenue", e1)
    gp1, rgp1 = fx.ttm("Gross profit", e1)
    if gp1 is None:
        c1, rc1 = fx.ttm("Cost of revenue", e1)
        gp1, rgp1 = (rev1 - c1, rc1) if c1 is not None and rev1 is not None else (None, [])
    ta2, rta2 = fx.bal("Total assets", fx.shift(end, 8))
    ltd1, rltd1 = fx.bal("Long-term debt", e1)
    tca1, rtca1 = fx.bal("Total current assets", e1)
    tcl1, rtcl1 = fx.bal("Total current liabilities", e1)
    sh1 = fx.at("Diluted shares", e1)
    tests = {}
    if ni is not None and ta1:
        tests["f_roa"] = (ni / ta1 > 0, L("Net income", rni) + [("Total assets", rta1)])
    if ocf is not None:
        tests["f_cfo"] = (ocf > 0, L("Operating cash flow", rocf))
    if ni is not None and ni1 is not None and ta1 and ta2:
        tests["f_droa"] = (ni / ta1 > ni1 / ta2, L("Net income", rni + rni1) + [("Total assets", rta1), ("Total assets", rta2)])
    if ocf is not None and ni is not None:
        tests["f_accrual"] = (ocf > ni, L("Operating cash flow", rocf) + L("Net income", rni))
    if ltd is not None and ltd1 is not None and ta and ta1:
        tests["f_dlever"] = (ltd / ta <= ltd1 / ta1, [("Long-term debt", rltd), ("Long-term debt", rltd1), ("Total assets", rta), ("Total assets", rta1)])
    elif ta and ta1 and not fx.q.get("Long-term debt"):
        tests["f_dlever"] = (True, [("Total assets", rta)])
    if tca is not None and tcl and tca1 is not None and tcl1:
        tests["f_dliquid"] = (tca / tcl > tca1 / tcl1, [("Total current assets", rtca), ("Total current liabilities", rtcl), ("Total current assets", rtca1), ("Total current liabilities", rtcl1)])
    if sh and sh1:
        tests["f_noissue"] = (fx.adj("Diluted shares", sh) <= fx.adj("Diluted shares", sh1) * 1.0005, [("Diluted shares", sh), ("Diluted shares", sh1)])
    if gp is not None and rev and gp1 is not None and rev1:
        tests["f_dmargin"] = (gp / rev > gp1 / rev1, rgp + L("Revenue", rrev) + L("Revenue", rrev1))
    if rev and rev1 and ta1 and ta2:
        tests["f_dturn"] = (rev / ta1 > rev1 / ta2, L("Revenue", rrev + rrev1) + [("Total assets", rta1), ("Total assets", rta2)])
    for k, (ok, recs) in tests.items():
        out["pio_" + k] = bool(ok)
        prov.put("pio_" + k, dict((a, c) for a, _n, c in PIO)[k], recs)
    if len(tests) == 9:
        out["pio_f"] = sum(1 for ok, _ in tests.values() if ok)
        prov.put("pio_f", "Count of the nine Piotroski tests passed (see each pio_ field)", [x for _ok, recs in tests.values() for x in recs][:40])
    for key, label in (("rev", "Revenue"), ("eps", "EPS diluted"), ("fcf", None), ("oi", "Operating income")):
        growth_block(fx, out, prov, key, label or "Free cash flow", end)

    # Explain missing fields caused by an anchor-period hold. The source
    # references retain the reported value and conflicting alternative; they
    # are evidence of the hold, not inputs to a substitute calculation.
    # Current P/E and earnings yield use independently reported diluted EPS.
    held_fields = {}
    dependents = {
        "Revenue": ("gm om nm fcfm gm_chg3 om_chg3 nm_chg3 fcfm_chg3 sbc_pct capex_int rnd_int "
                    "ps ev_sales altman_z ps_p5 ev_sales_p5 pio_f_dmargin pio_f_dturn pio_f "
                    "rev_g_q rev_g_ttm rev_cagr3 rev_cagr5 rev_accel rev_streak"),
        "Net income": ("nm nm_chg3 roe roa accruals cash_conv pio_f_roa pio_f_droa pio_f_accrual "
                       "pio_f pe_p5 earn_yield_p5"),
    }
    for hold in fx.comparability_holds:
        if hold['point']['end'] != end:
            continue
        for field in dependents.get(hold['label'], '').split():
            held_fields.setdefault(field, []).append((hold['label'], _rec(hold['point'], hold['point']['c'])))
    for field, records in held_fields.items():
        if out.get(field) is None:
            labels = ', '.join(dict.fromkeys(label for label, _ in records))
            prov.put(field, f"Conflicting {labels} observations for {end}; metric withheld pending review",
                     records, status="held", period_end=end)

    inputs = {"end": end, "rev": rev, "ni": ni, "oi": oi, "eps": eps, "fcf": fcf, "eq": eq, "ta": ta, "tl": tl, "tca": tca, "tcl": tcl, "re": re_,
              "debt": debt, "cash": cashall, "div": div, "bb": bb, "fin": fin,
              "recs": {"rev": L("Revenue", rrev), "ni": L("Net income", rni), "oi": L("Operating income", roi), "eps": L("EPS diluted", reps), "fcf": rfcf,
                       "eq": [("Shareholders' equity", req)], "ta": [("Total assets", rta)], "tl": [("Total liabilities", rtl)], "re": [("Retained earnings", rre)] if rre else [],
                       "wc": [("Total current assets", rtca), ("Total current liabilities", rtcl)], "debt": debt_recs, "cash": cash_recs,
                       "div": L("Dividends paid", rdiv), "bb": L("Buybacks", rbb)}}
    return out, prov, fx, inputs


def valuation_history(fx, inputs, dates, closes):
    """Monthly valuation ratios over five years, point in time: each month uses the latest quarter whose filings were
    public by then, diluted shares (split-adjusted) times that day's close as market cap."""
    if not fx.ends:
        return {}
    if not fx.data.get("filing_history") and not fx.data.get("history_dedup"):
        return {}  # Latest-only bundles cannot support an honest historical reconstruction.
    def snapshot(cutoff):
        past = Facts(fx.data, fx.splits, as_of=cutoff)
        if not past.ends:
            return None
        e = past.ends[-1]
        rev, rr = past.ttm("Revenue", e)
        ni, rn = past.ttm("Net income", e)
        oi, ro = past.ttm("Operating income", e)
        fcf, rf = past.fcf_ttm(e)
        sh = past.at("Diluted shares", e)
        eq = past.at("Shareholders' equity", e)
        ltd = past.at("Long-term debt", e)
        dc = past.at("Debt, current", e)
        cash = past.at("Cash & equivalents", e)
        ms = past.at("Marketable securities, current", e)
        if not sh:
            return None
        return {"rev":rev, "ni":ni, "oi":oi, "fcf":fcf, "sh":past.adj("Diluted shares",sh),
                "eq":eq[2] if eq else None, "debt":ltd[2]+dc[2] if ltd and dc else None,
                "cash":cash[2]+ms[2] if cash and ms else None}

    def ratios(r, px):
        mc = px * r["sh"]
        ev = mc + r["debt"] - r["cash"] if r["debt"] is not None and r["cash"] is not None and not inputs["fin"] else None
        g = lambda a, b, pos=True: a / b if a is not None and b and (b > 0 or not pos) else None
        return {"pe": g(mc, r["ni"]), "ev_ebit": g(ev, r["oi"]), "ev_sales": g(ev, r["rev"]), "ev_fcf": g(ev, r["fcf"]), "pb": g(mc, r["eq"]),
                "ps": g(mc, r["rev"]), "fcf_yield": r["fcf"] / mc if r["fcf"] is not None and mc else None, "earn_yield": r["ni"] / mc if r["ni"] is not None and mc else None}

    month_ends = {}
    for i, d in enumerate(dates):
        month_ends[d[:7]] = i
    cut = (D(dates[-1]) - dt.timedelta(days=int(365.25 * 5))).isoformat()
    hist = {k: [] for k in ("pe", "ev_ebit", "ev_sales", "ev_fcf", "pb", "ps", "fcf_yield", "earn_yield")}
    for ym, i in sorted(month_ends.items()):
        d = dates[i]
        if d < cut or closes[i] is None:
            continue
        snap = snapshot(d)
        if not snap:
            continue
        for k, v in ratios(snap, closes[i]).items():
            if v is not None:
                hist[k].append(v)
    last = len(dates) - 1
    while last >= 0 and closes[last] is None:
        last -= 1
    if last < 0:
        return {}
    snap = snapshot(dates[last])
    if not snap:
        return {}
    now = ratios(snap, closes[last])
    out = {}
    for k, arr in hist.items():
        if now.get(k) is None or len(arr) < 24:
            continue
        allv = arr + [now[k]]
        rank = sum(1 for x in allv if x <= now[k]) / len(allv)
        out[k] = (rank, len(arr), min(arr), statistics.median(arr), max(arr))
    return out


# ------------------------------------------------------------------ price calculations
def ret_since(dates, c, days):
    if c[-1] is None:
        return None, None
    t = (D(dates[-1]) - dt.timedelta(days=days)).isoformat()
    for i in range(len(dates) - 1, -1, -1):
        if dates[i] <= t:
            for j in range(i, -1, -1):
                if c[j] is not None:
                    return c[-1] / c[j] - 1, j
            return None, None
    return None, None


def sma_at(c, n, i):
    w = [x for x in c[max(0, i - n + 1):i + 1] if x is not None]
    return sum(w) / len(w) if len(w) >= n * 0.9 else None


def price_block(tk, dates, c, spy, sect, ranks):
    out, prov = {}, {}
    end = next((i + 1 for i in range(len(c) - 1, -1, -1) if c[i] is not None), 0)
    if not end:
        return out, prov
    # A missing latest provider bar does not erase the last reported close.
    # Trim the calculation window, preserving the real date and missing source.
    dates, c = dates[:end], c[:end]
    spy, sect = (spy[:end] if spy else None), (sect[:end] if sect else None)
    last = dates[-1]
    p = c[-1]
    out["price"] = p
    prov["price"] = {"x": "Last daily close", "p": [[last, p]]}
    for fid, _n, days in RET:
        if days is None:
            y0 = [i for i, d in enumerate(dates) if d[:4] < last[:4] and c[i] is not None]
            if y0:
                out[fid] = p / c[y0[-1]] - 1
                prov[fid] = {"x": "Close / last close of the prior year − 1", "p": [[dates[y0[-1]], c[y0[-1]]], [last, p]]}
            continue
        r, j = ret_since(dates, c, days)
        if r is not None and (D(last) - D(dates[j])).days >= days - 7:
            out[fid] = r
            prov[fid] = {"x": f"Close / close {days} days earlier − 1", "p": [[dates[j], c[j]], [last, p]]}
    y1 = [(dates[i], c[i]) for i in range(len(dates)) if dates[i] >= (D(last) - dt.timedelta(days=365)).isoformat() and c[i] is not None]
    if len(y1) > 200:
        hi = max(y1, key=lambda x: x[1])
        lo = min(y1, key=lambda x: x[1])
        out["dist_hi52"] = p / hi[1] - 1
        out["dist_lo52"] = p / lo[1] - 1
        prov["dist_hi52"] = {"x": "Close / highest close in 365 days − 1", "p": [list(hi), [last, p]], "r": [y1[0][0], last]}
        prov["dist_lo52"] = {"x": "Close / lowest close in 365 days − 1", "p": [list(lo), [last, p]], "r": [y1[0][0], last]}
        peak, mdd, pk_d, tr = y1[0][1], 0.0, y1[0][0], None
        best = (None, None)
        for d, v in y1:
            if v > peak:
                peak, pk_d = v, d
            dd = v / peak - 1
            if dd < mdd:
                mdd, best = dd, (pk_d, d)
        out["mdd_1y"] = mdd
        prov["mdd_1y"] = {"x": "Largest fall from a running peak close, last 365 days", "r": [y1[0][0], last], "p": [[best[0], next((v for d, v in y1 if d == best[0]), None)], [best[1], next((v for d, v in y1 if d == best[1]), None)]] if best[0] else None}
    n = len(c) - 1
    for k in (20, 50, 200):
        s = sma_at(c, k, n)
        if s:
            out[f"above_sma{k}"] = p > s
            out[f"dist_sma{k}"] = p / s - 1
            prov[f"above_sma{k}"] = prov[f"dist_sma{k}"] = {"x": f"Close vs average of the last {k} closes ({sig(s, 5)})", "r": [dates[max(0, n - k + 1)], last], "p": [[last, p]]}
    # last 50/200 cross within two years
    prev_sign, cross = None, None
    for i in range(max(200, n - 504), n + 1):
        a, b = sma_at(c, 50, i), sma_at(c, 200, i)
        if a is None or b is None:
            continue
        sgn = 1 if a > b else -1
        if prev_sign is not None and sgn != prev_sign:
            cross = (i, sgn)
        prev_sign = sgn
    if cross:
        out["cross_type"] = cross[1]
        out["cross_days"] = (D(last) - D(dates[cross[0]])).days
        prov["cross_type"] = prov["cross_days"] = {"x": ("Golden" if cross[1] > 0 else "Death") + f" cross on {dates[cross[0]]}: 50-day average moved {'above' if cross[1] > 0 else 'below'} the 200-day average", "r": [dates[cross[0]], last]}
    cc = [x for x in c[-400:] if x is not None]
    if len(cc) > 60:
        g = l = 0.0
        for i in range(1, 15):
            d = cc[i] - cc[i - 1]
            g += max(d, 0)
            l += max(-d, 0)
        g /= 14
        l /= 14
        for i in range(15, len(cc)):
            d = cc[i] - cc[i - 1]
            g = (g * 13 + max(d, 0)) / 14
            l = (l * 13 + max(-d, 0)) / 14
        out["rsi14"] = 100 - 100 / (1 + g / (l or 1e-9))
        prov["rsi14"] = {"x": "Wilder RSI over 14 days, seeded on the window start", "r": [dates[-len(cc)], last]}

        def ema(arr, k):
            a, o = 2 / (k + 1), []
            e = arr[0]
            for x in arr:
                e = x * a + e * (1 - a)
                o.append(e)
            return o
        e12, e26 = ema(cc, 12), ema(cc, 26)
        macd = [x - y for x, y in zip(e12, e26)]
        sigl = ema(macd, 9)
        out["macd_state"] = 1 if macd[-1] > sigl[-1] else -1
        out["macd_hist"] = (macd[-1] - sigl[-1]) / p
        prov["macd_state"] = prov["macd_hist"] = {"x": f"MACD {sig(macd[-1], 4)} vs signal {sig(sigl[-1], 4)} (EMA 12 − EMA 26 of closes, 9-day signal)", "r": [dates[-len(cc)], last]}
    rets = [(dates[i], c[i] / c[i - 1] - 1) for i in range(1, len(c)) if c[i] is not None and c[i - 1] is not None]
    for fid, k in (("vol_60d", 60), ("vol_1y", 252)):
        w = [r for _, r in rets[-k:]]
        if len(w) >= k * 0.9:
            out[fid] = statistics.stdev(w) * math.sqrt(252)
            # fewer than k returns can still meet the 90% threshold: the window starts at the first one used
            prov[fid] = {"x": f"Standard deviation of {len(w)} daily returns × √252", "r": [rets[-len(w)][0], last], "n": len(w)}
    if spy:
        pairs = [(c[i] / c[i - 1] - 1, spy[i] / spy[i - 1] - 1) for i in range(max(1, len(c) - 252), len(c)) if None not in (c[i], c[i - 1], spy[i], spy[i - 1])]
        if len(pairs) > 120:
            ra, rb = [x for x, _ in pairs], [y for _, y in pairs]
            mb = sum(rb) / len(rb)
            ma = sum(ra) / len(ra)
            var = sum((y - mb) ** 2 for y in rb)
            if var:
                out["beta"] = sum((x - ma) * (y - mb) for x, y in pairs) / var
                prov["beta"] = {"x": "Covariance with SPY daily returns / variance of SPY daily returns", "r": [dates[max(1, len(c) - 252)], last], "n": len(pairs)}
    for fid, other, days, nm in (("rs_spy_3m", spy, 91, "SPY"), ("rs_spy_6m", spy, 182, "SPY"), ("rs_sec_3m", sect, 91, "sector ETF"), ("rs_sec_6m", sect, 182, "sector ETF")):
        if not other:
            continue
        a, j = ret_since(dates, c, days)
        b, j2 = ret_since(dates, other, days)
        if a is not None and b is not None:
            out[fid] = (1 + a) / (1 + b) - 1
            prov[fid] = {"x": f"(1 + return) / (1 + {nm} return) − 1 over {days} days", "p": [[dates[j], c[j]], [last, p]], "r": [dates[j], last]}
    if tk in ranks:
        out["mom_rank_3m"] = ranks[tk]
        prov["mom_rank_3m"] = {"x": "Percentile of the 3-month return among tracked companies", "p": (prov.get("ret_3m") or {}).get("p")}
    return out, prov


def daily_returns(c, n=252):
    """Last n daily returns as a list aligned to positions (None where missing)."""
    start = max(1, len(c) - n)
    return [(c[i] / c[i - 1] - 1) if c[i] is not None and c[i - 1] is not None and c[i - 1] else None for i in range(start, len(c))]


def daily_changes(c, n=252):
    start = max(1, len(c) - n)
    return [(c[i] - c[i - 1]) if c[i] is not None and c[i - 1] is not None else None for i in range(start, len(c))]


def corr_pairs(a, b):
    xs, ys = [], []
    for x, y in zip(a, b):
        if x is not None and y is not None:
            xs.append(x)
            ys.append(y)
    return pearson(xs, ys), len(xs)


def monthly_returns(dates, c, months=61):
    """Month-end closes over the last `months` months → {YYYY-MM: return}."""
    me = {}
    for d, v in zip(dates, c):
        if v is not None:
            me[d[:7]] = v
    keys = sorted(me)[-months:]
    return {keys[i]: me[keys[i]] / me[keys[i - 1]] - 1 for i in range(1, len(keys))}


def macro_monthly(sb):
    cut = (dt.date.today() - dt.timedelta(days=int(365.25 * 6.5))).isoformat()
    want = [s for _f, s, *_ in CORR_M]
    rows = sb.select("macro_observations", f"select=series,d,value&series=in.({','.join(want)})&d=gte.{cut}&order=d.asc")
    by = {}
    for r in rows:
        by.setdefault(r["series"], {})[r["d"][:7]] = (r["d"], float(r["value"]))  # last observation of each month wins
    out = {}
    for fid, ser, kind, *_ in CORR_M:
        m = by.get(ser) or {}
        keys = sorted(m)
        ch = {}
        for i in range(1, len(keys)):
            a, b = m[keys[i - 1]][1], m[keys[i]][1]
            ch[keys[i]] = (b / a - 1) if kind == "pct" and a else (b - a)
        out[fid] = {"chg": ch, "last": m[keys[-1]][0] if keys else None}
    return out


# ------------------------------------------------------------------ KPI families and segments
def _kpi_yoy(vals):
    """Latest value and YoY change from a KPI's quarterly (else annual) values."""
    for K in ("Q", "A"):
        ser = vals.get(K) or {}
        if not ser:
            continue
        ends = sorted(ser)
        e = ends[-1]
        cur = ser[e]
        prior = None
        for p in ends[:-1]:
            if abs(days_between(p, e) - 365) <= 25:
                prior = ser[p]
        yoy = cur["v"] / prior["v"] - 1 if prior and prior.get("v") and cur.get("v") is not None and prior["v"] > 0 else None
        return K, e, cur, prior, yoy
    return None


def _std_fields(tf, out, prov):
    """std_<id> and std_<id>_yoy from the company's standardized series, with the source documents of both points."""
    docs = (tf or {}).get("docs") or {}

    def doc(p, s):
        d = docs.get(p[3]) or [None] * 6
        return {"doc": p[3], "title": d[0], "path": d[1], "ext": d[2], "url": d[3], "page": p[4], "quote": p[5], "printed": None, "sheet": p[6], "cell": p[7],
                "v": p[9], "end": p[0], "label": p[2]}
    for cid in STD_FIELDS:
        t = _STD.get(cid)
        s = ((tf or {}).get("series") or {}).get(cid)
        if not t or not s or s.get("current") is False:
            continue
        pts = s["Q"] if s["Q"] and (not s["A"] or s["Q"][-1][0] >= s["A"][-1][0]) else s["A"]
        if not pts:
            continue
        p = pts[-1]
        if days_between(p[0], dt.date.today().isoformat()) > 550:
            continue   # the company stopped reporting it
        scale = 0.01 if t["unit"] in ("%", "pp") else 1
        out[f"std_{cid}"] = p[1] * scale
        prov[f"std_{cid}"] = {"x": f"{s['name']} ({s.get('unit') or ''}), {p[2] or p[0]}; standardized as {t['name']}", "kpi": s["kpi_id"], "unit": s.get("unit"), "docs": [doc(p, s)]}
        prior = next((x for x in reversed(pts[:-1]) if abs(days_between(x[0], p[0]) - 365) <= 25), None)
        if prior and prior[1] is not None and p[1] is not None:
            if t["unit"] in ("%", "pp"):
                yoy = (p[1] - prior[1]) / 100
                how = f"{p[2] or p[0]} minus {prior[2] or prior[0]}, percentage points"
            elif prior[1] > 0:
                yoy = p[1] / prior[1] - 1
                how = f"{p[2] or p[0]} / {prior[2] or prior[0]} − 1"
            else:
                continue
            out[f"std_{cid}_yoy"] = yoy
            prov[f"std_{cid}_yoy"] = {"x": f"{s['name']}: {how}", "kpi": s["kpi_id"], "unit": s.get("unit"), "docs": [doc(p, s), doc(prior, s)]}


def kpi_extract(kpi, seg, std=None):
    out, prov = {}, {}
    std_ids = {cid: s["kpi_id"] for cid, s in ((std or {}).get("series") or {}).items()}
    if std:
        _std_fields(std, out, prov)
    items = []
    for g in (kpi or {}).get("groups") or []:
        for it in g.get("items") or []:
            if re.search(r"_orig$|_pre_\d{4}|before \d{4}|as originally reported", (it.get("id") or "") + " " + (it.get("name") or ""), re.I):
                continue
            items.append((g, it))
    if kpi:
        out["kpi_count"] = len(items)
        prov["kpi_count"] = {"x": "KPIs collected from earnings releases and investor documents", "src": "kpis"}
    for fam, name, rx in KPI_FAM:
        pat = re.compile(rx, re.I)
        hits = [(g, it) for g, it in items if pat.search(f"{it.get('name', '')} {it.get('id', '')} {g.get('category', '')} {g.get('subcategory') or ''}")]
        if not kpi:
            continue
        out[f"kpi_{fam}"] = bool(hits)
        if not hits:
            continue
        # the company's standardized total-company series for this family comes first when it exists
        canon = {std_ids[c] for c in FAM_CANON.get(fam, []) if c in std_ids}
        hits.sort(key=lambda x: (x[1].get("id") not in canon, x[1].get("depth") or 0, not x[1].get("is_total"), -len((x[1].get("values") or {}).get("Q") or {})))
        g, it = hits[0]
        r = _kpi_yoy(it.get("values") or {})
        prov[f"kpi_{fam}"] = {"x": ("Standardized KPI first. " if it.get("id") in canon else "") + "Matched KPIs: " + "; ".join(h[1].get("name", "") for h in hits[:6]), "kpi": it.get("id")}
        if not r:
            continue
        K, e, cur, prior, yoy = r
        doc = lambda v: {"doc": v.get("doc"), "title": v.get("doc_title"), "path": v.get("path"), "ext": v.get("ext"), "url": v.get("url"), "page": v.get("page"),
                         "quote": v.get("quote"), "printed": v.get("printed"), "sheet": v.get("sheet"), "cell": v.get("cell"), "v": v.get("v"), "end": None, "label": v.get("label")}
        d0 = doc(cur)
        d0["end"] = e
        out[f"kpi_{fam}_v"] = cur.get("v")
        prov[f"kpi_{fam}_v"] = {"x": f"{it.get('name')} ({it.get('unit') or ''}), {cur.get('label') or e}", "kpi": it.get("id"), "unit": it.get("unit"), "docs": [d0]}
        if yoy is not None:
            d1 = doc(prior)
            d1["end"] = next((p for p, v in (it["values"][K]).items() if v is prior), None)
            out[f"kpi_{fam}_yoy"] = yoy
            prov[f"kpi_{fam}_yoy"] = {"x": f"{it.get('name')}: {cur.get('label') or e} / {prior.get('label') or 'a year earlier'} − 1", "kpi": it.get("id"), "unit": it.get("unit"), "docs": [d0, d1]}
    tabs = (seg or {}).get("tables") or []
    segt = next((t for t in tabs if t.get("category") == "segment" and t.get("family") == "revenue"), None)
    geot = next((t for t in tabs if t.get("category") == "geo" and t.get("family") == "revenue"), None)
    if segt:
        for K in ("A", "Q"):
            tot = (segt.get("total") or {}).get(K) or {}
            if not tot:
                continue
            e = max(tot)
            rows = [r for r in segt["rows"] if (r.get("depth") or 0) == 0 and not r.get("memo") and not r.get("synth") and (r.get(K) or {}).get(e)]
            if not rows:
                continue
            out["seg_count"] = len(rows)
            top = max(rows, key=lambda r: r[K][e]["v"])
            if tot[e].get("v"):
                out["seg_top_share"] = top[K][e]["v"] / tot[e]["v"]
            cell = lambda c: {"v": c.get("v"), "a": c.get("accn"), "f": c.get("form"), "d": c.get("filed"), "e": c.get("end"), "b": c.get("start"), "id": c.get("id"), "c": (c.get("name") or "").split(":")[-1]}
            prov["seg_count"] = {"x": f"Business segments in the {('annual' if K == 'A' else 'quarterly')} revenue table for the period ended {e}: " + ", ".join(r["label"] for r in rows[:12]), "xbrl": [cell(r[K][e]) for r in rows[:12]]}
            prov["seg_top_share"] = {"x": f"{top['label']} revenue / total revenue, period ended {e}", "xbrl": [dict(cell(top[K][e]), l=top["label"]), dict(cell(tot[e]), l="Total revenue")]}
            break
    if geot:
        for K in ("A", "Q"):
            tot = (geot.get("total") or {}).get(K) or {}
            if not tot:
                continue
            e = max(tot)
            rows = [r for r in geot["rows"] if (r.get("depth") or 0) == 0 and not r.get("memo") and not r.get("synth") and (r.get(K) or {}).get(e)]
            if rows:
                out["geo_count"] = len(rows)
                prov["geo_count"] = {"x": f"Geographic revenue lines, period ended {e}: " + ", ".join(r["label"] for r in rows[:12])}
                break
    return out, prov


def list_objects(sb, prefix):
    out, off = [], 0
    while True:
        page = json.loads(request(f"{sb.url}/storage/v1/object/list/public-data", method="POST",
                                  data={"prefix": prefix, "limit": 1000, "offset": off}, headers=sb._h()))
        out += page
        if len(page) < 1000:
            return out
        off += 1000


def update_kpis(sb, full=False, workers=6, log=print, deadline=None):
    cache = (None if full else gz_json(sb, KPI_PATH)) or {}
    stamps = {}
    for pre in ("kpis", "segments", "kpistd/t"):
        for o in list_objects(sb, pre):
            if o.get("name", "").endswith(".json"):
                stamps.setdefault(o["name"][:-5], {})[pre.split("/")[0]] = o.get("updated_at")
    todo = [tk for tk in TICKERS if (cache.get(tk) or {}).get("stamp") != stamps.get(tk)]

    def one(tk):
        if deadline and time.time() > deadline:
            return tk, None
        st = stamps.get(tk) or {}
        try:
            kpi = sb.get_json(f"kpis/{tk}.json") if st.get("kpis") else None
            seg = sb.get_json(f"segments/{tk}.json") if st.get("segments") else None
            std = sb.get_json(f"kpistd/t/{tk}.json") if st.get("kpistd") else None
            o, p = kpi_extract(kpi, seg, std)
            return tk, {"stamp": st, "out": o, "prov": p}
        except Exception as e:
            log("kpi extract failed", tk, str(e)[:160])
            return tk, None

    n = 0
    with ThreadPoolExecutor(workers) as ex:
        for tk, x in ex.map(one, todo):
            if x:
                cache[tk] = x
                n += 1
    if todo:
        put_gz(sb, KPI_PATH, cache)
    log(f"kpi cache: {n} of {len(todo)} refreshed")
    return cache


# ------------------------------------------------------------------ activity
def activity(sb, fundcache, today):
    fl_rows = sb.select("filings", f"select=ticker,form,filed,accession,items,primary_doc&form=in.(10-K,10-Q,8-K)&filed=gte.{(today - dt.timedelta(days=800)).isoformat()}&order=filed.desc")
    filings = {}
    for r in fl_rows:
        filings.setdefault(r["ticker"], {}).setdefault(r["form"], []).append([r["filed"], r["accession"], r.get("items") or "", r.get("primary_doc") or ""])
    news = sb.select("news_items", f"select=ticker,url,title,published_at&published_at=gte.{(today - dt.timedelta(days=30)).isoformat()}&ticker=neq.MACRO")
    by_news = {}
    for n in news:
        by_news.setdefault(n["ticker"], []).append(n)
    cut = (today - dt.timedelta(days=365)).isoformat()
    tx = sb.select("insider_transactions", "select=ticker,accession,seq,filed,owner,owner_cik,role,tx_date,code,security,shares,price,owned_after,"
                   f"derivative,plan_10b5&tx_date=gte.{cut}&code=in.(P,S)&derivative=is.false&seq=gte.0&order=ticker.asc,accession.asc,seq.asc")
    from . import insiders as _ins
    by_tk = {}
    for r in tx:
        by_tk.setdefault(r["ticker"], []).append(r)
    ins = {}
    for t, rows_t in by_tk.items():
        b = _ins.summarize(rows_t, today=today)
        # Joint filings count once; buyers are individual officers and directors (funds and 10% holders excluded).
        ins[t] = {k: dict(b[k], buyers=b[k]["ind_buyers"]) for k in ("w90", "w365")}
    for t in TICKERS:
        for key in ("w90", "w365"):
            ins.setdefault(t, {}).setdefault(key, {"buys": 0, "sells": 0, "buy_value": 0, "sell_value": 0, "buyers": 0, "sellers": 0})
    out, prov = {}, {}
    for tk in TICKERS:
        o, p = {}, {}
        w90, w365 = (ins.get(tk) or {}).get("w90"), (ins.get(tk) or {}).get("w365")
        for sfx, w, days in (("90d", w90, 90), ("1y", w365, 365)):
            # The additional cohort proves financial filings only. Stored activity
            # rows (or their absence) do not establish collection for these windows.
            # Keep these fields unavailable until a window-coverage gate exists.
            if not w or tk in STRICT_SOURCE_TICKERS:
                continue
            rng = [(today - dt.timedelta(days=days)).isoformat(), today.isoformat()]
            o[f"ins_net_{sfx}"] = (w.get("buy_value") or 0) - (w.get("sell_value") or 0)
            o[f"ins_net_{sfx}_n"] = (w.get("buys") or 0) - (w.get("sells") or 0)
            o[f"ins_buyers_{sfx}"] = w.get("buyers") or 0
            meta = {"r": rng, "src": "insiders", "w": {k: w.get(k) for k in ("buys", "sells", "buy_value", "sell_value", "buyers", "sellers")}}
            p[f"ins_net_{sfx}"] = dict(meta, x=f"Open-market purchases ${w.get('buy_value') or 0:,.0f} − sales ${w.get('sell_value') or 0:,.0f} (Form 4, codes P and S)")
            p[f"ins_net_{sfx}_n"] = dict(meta, x=f"{w.get('buys') or 0} purchases − {w.get('sells') or 0} sales")
            p[f"ins_buyers_{sfx}"] = dict(meta, x=f"{w.get('buyers') or 0} distinct individual officers and directors buying")
        # Financial cache eligibility is independent of the established original
        # cohort's stored news window. Additional-company coverage stays held.
        if tk not in STRICT_SOURCE_TICKERS:
            items = sorted(by_news.get(tk, []), key=lambda n: n.get("published_at") or "", reverse=True)
            o["news_30d"] = len(items)
            p["news_30d"] = {"x": f"News items published since {(today - dt.timedelta(days=30)).isoformat()}", "u": [[n["url"], n["title"][:140], (n.get("published_at") or "")[:10]] for n in items[:12]]}
        fl = filings.get(tk) or {}
        for form, fid in (("10-K", "days_10k"), ("10-Q", "days_10q"), ("8-K", "days_8k")):
            lst = fl.get(form) or []
            if lst:
                d, a, _i, pdoc = lst[0]
                o[fid] = (today - D(d)).days
                p[fid] = {"x": f"{form} filed {d}", "a": [[a, form, d, pdoc]]}
        earn = [x for x in fl.get("8-K") or [] if "2.02" in (x[2] or "")][:9]
        base = earn if len(earn) >= 3 else (fl.get("10-Q") or []) + (fl.get("10-K") or [])
        base = sorted(base, key=lambda x: x[0], reverse=True)[:9]
        if len(base) >= 3:
            gaps = [(D(base[i][0]) - D(base[i + 1][0])).days for i in range(len(base) - 1)]
            gaps = [g for g in gaps if 40 <= g <= 140] or gaps
            med = statistics.median(gaps)
            nxt = D(base[0][0]) + dt.timedelta(days=round(med))
            while nxt < today - dt.timedelta(days=21):  # cadence missed: roll to the next expected date
                nxt += dt.timedelta(days=round(med))
            o["next_earn_days"] = (nxt - today).days
            via8k = len(earn) >= 3
            p["next_earn_days"] = {"x": f"Estimated {nxt.isoformat()}: last {'results 8-K (Item 2.02)' if via8k else 'periodic report'} filed {base[0][0]} + median gap of {round(med)} days. An estimate, not a company date.",
                                   "est": nxt.isoformat(), "a": [[x[1], "8-K" if via8k else "10-Q/10-K", x[0], x[3]] for x in base[:6]]}
        out[tk], prov[tk] = o, p
    return out, prov


# ------------------------------------------------------------------ assembly
@serialized_aggregates
def screener_job(sb, full=False, time_budget=None, workers=6, log=print):
    t0 = time.time()
    deadline = t0 + time_budget * 0.55 if time_budget else None
    with Run(sb, "screener") as run:
        today = dt.date.today()
        fundmeta = sb.get_json("meta/fundamentals.json") or {}
        hist = update_closes(sb, full=full, log=log, deadline=deadline)
        dates = hist["dates"]
        S = hist["s"]
        splits = hist.get("splits") or {}
        fundc = update_fund(sb, fundmeta, hist, full=full, workers=workers, log=log, deadline=deadline)
        kpic = update_kpis(sb, full=full, workers=workers, log=log, deadline=deadline)
        act, actp = activity(sb, fundc, today)
        macro = macro_monthly(sb)
        log("inputs loaded", round(time.time() - t0, 1), "s")

        spy = S.get("SPY")
        # 3-month momentum ranks
        r3 = {}
        for tk in TICKERS:
            if S.get(tk):
                end = next((i + 1 for i in range(len(S[tk]) - 1, -1, -1) if S[tk][i] is not None), 0)
                r, _j = ret_since(dates[:end], S[tk][:end], 91) if end else (None,None)
                if r is not None:
                    r3[tk] = r
        order = sorted(r3, key=lambda t: r3[t])
        ranks = {t: (i + 1) / len(order) for i, t in enumerate(order)}
        # factor return series
        fac = {}
        for fid, sym, _n in CORR_D:
            if sym and S.get(sym):
                fac[fid] = daily_changes(S[sym]) if sym == "^TNX" else daily_returns(S[sym])
        sect_r = {s: daily_returns(S[e]) for s, e in SECTOR_ETF.items() if S.get(e)}

        rows, provs, values = [], {}, {}
        for tk, _cik, name, sector in UNIVERSE:
            fm = fundmeta.get(tk) or {}
            c = S.get(tk) or []
            out, prov = {}, Prov()
            if fundc.get(tk):
                fo, prov, fx, inp = fundamentals_for(tk, fundc[tk], splits.get(tk), c[-1] if c else None, sector)
                out.update(fo)
            else:
                fx, inp = None, None
            po, pp = price_block(tk, dates, c, spy, S.get(SECTOR_ETF.get(sector, "")), ranks)
            out.update(po)
            prov.f.update(pp)
            price = out.get("price")
            price_date = pp["price"]["p"][-1][0] if price is not None else None
            # Per-share earnings valuation does not require a reported share count.
            if inp and price is not None and math.isfinite(price) and price > 0 and inp["eps"] is not None:
                eps = inp["eps"]
                if eps > 0:
                    out["pe"] = price / eps
                    prov.put("pe", f"Close {price} on {price_date} / diluted EPS of the last four quarters ({eps:.2f}, split-adjusted)", inp["recs"]["eps"], p=[[price_date, price]])
                out["earn_yield"] = eps / price
                prov.put("earn_yield", f"Diluted EPS of the last four quarters ({eps:.2f}) / close {price}", inp["recs"]["eps"], p=[[price_date, price]])
            # Current capitalization uses the same canonical, independently
            # reported observation as the index. Financial normalization can
            # remain pending without erasing a separately evidenced share count.
            sh = dict(fm.get("shares_metadata") or {})
            sh["v"] = sh.get("val")
            mcap = fundamentals.market_cap(price, sh, price_date)
            if mcap is not None:
                out["mcap"] = mcap
                share_ref = None
                if sh.get("accn"):
                    share_ref = ("Shares outstanding (cover page)", [sh.get("end"), "", sh["v"], sh["accn"], sh.get("filed") or "", sh.get("form") or "", "EntityCommonStockSharesOutstanding", 0])
                classes = f" (share classes {' + '.join(f'{x:,.0f}' for x in sh['parts'])})" if len(sh.get("parts") or []) > 1 else ""
                basis = f"shares outstanding{classes} (cover page, as of {sh.get('end')})"
                prov.put("mcap", f"Close {price} on {price_date} × {sh['v']:,.0f} {basis}", [share_ref] if share_ref else [], p=[[price_date, price]])
                if inp:
                    R = inp["recs"]
                    srefs = [share_ref] if share_ref else []
                    ev = None
                    if not inp["fin"] and inp["debt"] is not None and inp["cash"] is not None:
                        ev = mcap + inp["debt"] - inp["cash"]
                        out["ev"] = ev
                        prov.put("ev", "Market cap + total debt − cash and current marketable securities", srefs + R["debt"] + R["cash"], p=[[price_date, price]])
                    pairs = [("ev_ebit", ev, inp["oi"], R["oi"], "Enterprise value / operating income (trailing four quarters)"),
                             ("ev_sales", ev, inp["rev"], R["rev"], "Enterprise value / revenue (trailing four quarters)"),
                             ("ev_fcf", ev, inp["fcf"], R["fcf"], "Enterprise value / free cash flow (trailing four quarters)"),
                             ("pb", mcap, inp["eq"], R["eq"], "Market cap / shareholders' equity"),
                             ("ps", mcap, inp["rev"], R["rev"], "Market cap / revenue (trailing four quarters)")]
                    for fid, a, b, recs, x in pairs:
                        if a is not None and b and b > 0:
                            out[fid] = a / b
                            prov.put(fid, x, srefs + recs + (R["debt"] + R["cash"] if a is ev else []), p=[[price_date, price]])
                    for fid, num, recs, x in (("fcf_yield", inp["fcf"], R["fcf"], "Free cash flow (trailing four quarters) / market cap"),
                                              ("div_yield", inp["div"], R["div"], "Dividends paid (trailing four quarters) / market cap"),
                                              ("bb_yield", inp["bb"], R["bb"], "Share repurchases (trailing four quarters) / market cap")):
                        if num is not None:
                            out[fid] = num / mcap
                            prov.put(fid, x, srefs + recs, p=[[price_date, price]])
                    if inp["div"] is not None and inp["bb"] is not None:
                        out["sh_yield"] = (inp["div"] + inp["bb"]) / mcap
                        prov.put("sh_yield", "(Dividends paid + share repurchases, trailing four quarters) / market cap", srefs + R["div"] + R["bb"], p=[[price_date, price]])
                    if not inp["fin"] and inp["ta"] and inp["tl"] and None not in (inp["tca"], inp["tcl"], inp["re"], inp["oi"], inp["rev"]):
                        ta = inp["ta"]
                        z = 1.2 * (inp["tca"] - inp["tcl"]) / ta + 1.4 * inp["re"] / ta + 3.3 * inp["oi"] / ta + 0.6 * mcap / inp["tl"] + 1.0 * inp["rev"] / ta
                        out["altman_z"] = z
                        prov.put("altman_z", "1.2·(current assets − current liabilities)/assets + 1.4·retained earnings/assets + 3.3·EBIT/assets + 0.6·market cap/total liabilities + 1.0·revenue/assets",
                                 R["wc"] + R["re"] + R["oi"] + R["rev"] + R["ta"] + R["tl"] + srefs, p=[[price_date, price]])
                    vh = valuation_history(fx, inp, dates, c)
                    for k, (rank, n, lo, med, hi) in vh.items():
                        out[k + "_p5"] = rank
                        prov.put(k + "_p5", f"Rank of today's value among {n} month-end values over five years (low {sig(lo, 3)}, median {sig(med, 3)}, high {sig(hi, 3)}); point in time, diluted shares",
                                 [], r=[dates[max(0, len(dates) - 1260)], dates[-1]], n=n)
            # correlations
            if c:
                mine = daily_returns(c)
                best_n = 0
                for fid, sym, _n in CORR_D:
                    other = sect_r.get(sector) if fid == "c_sector" else fac.get(fid)
                    if not other:
                        continue
                    r, n = corr_pairs(mine, other)
                    if r is not None and n >= 120:
                        out[fid] = r
                        best_n = max(best_n, n)
                        prov.f[fid] = {"x": f"Pearson correlation of {n} daily {'yield changes' if sym == '^TNX' else 'returns'} with {sym or SECTOR_ETF.get(sector)}", "r": [dates[-len(mine)], dates[-1]], "n": n}
                if best_n:
                    out["corr_n"] = best_n
                mr = monthly_returns(dates, c)
                mn = 0
                for fid, ser, kind, n_, d_ in CORR_M:
                    ch = macro[fid]["chg"]
                    ks = [k for k in mr if k in ch]
                    if len(ks) >= 24:
                        r = pearson([mr[k] for k in ks], [ch[k] for k in ks])
                        if r is not None:
                            out[fid] = r
                            mn = max(mn, len(ks))
                            prov.f[fid] = {"x": f"Pearson correlation of {len(ks)} monthly stock returns with the {d_} ({ser})", "r": [ks[0], ks[-1]], "n": len(ks), "series": ser}
                if mn:
                    out["corrm_n"] = mn
            out.update(act.get(tk, {}))
            prov.f.update(actp.get(tk, {}))
            kc = kpic.get(tk) or {}
            out.update(kc.get("out") or {})
            prov.f.update(kc.get("prov") or {})
            values[tk] = out
            provs[tk] = prov
        log("fields computed", round(time.time() - t0, 1), "s")

        # pairwise correlation matrix and peers
        tick = [t for t in TICKERS if S.get(t)]
        vecs = {}
        for t in tick:
            r = daily_returns(S[t])
            xs = [x for x in r if x is not None]
            if len(xs) < 150:
                continue
            m = sum(xs) / len(xs)
            sd = math.sqrt(sum((x - m) ** 2 for x in xs) / len(xs)) or 1.0
            vecs[t] = [((x - m) / sd) if x is not None else 0.0 for x in r]
        tick = [t for t in tick if t in vecs]
        L = len(next(iter(vecs.values()))) if vecs else 0
        mat = bytearray()
        peers = {t: [] for t in tick}
        for i, a in enumerate(tick):
            va = vecs[a]
            for j in range(i + 1, len(tick)):
                b = tick[j]
                r = sum(map(mul, va, vecs[b])) / L
                r = max(-1.0, min(1.0, r))
                mat.append((round(r * 100)) & 0xFF)
                peers[a].append((r, b))
                peers[b].append((r, a))
        peer_out = {}
        for t, lst in peers.items():
            lst.sort(reverse=True)
            peer_out[t] = [[b for _r, b in lst[:5]], [b for _r, b in lst[-5:][::-1]], [round(r, 3) for r, _b in lst[:5]], [round(r, 3) for r, _b in lst[-5:][::-1]]]
            provs[t].f["peers"] = {"x": "Highest and lowest correlations of 1-year daily returns among tracked companies", "r": [dates[-L], dates[-1]], "n": L}
        sb.put_json("screener/corr.json", {"generated": now_iso(), "tickers": tick, "n": L, "from": dates[-L] if L else None, "to": dates[-1],
                                           "m": base64.b64encode(bytes(mat)).decode()}, cache="max-age=3600")

        # catalogue stats
        fields = []
        for f in F:
            vals = sorted(v[f["id"]] for v in values.values() if v.get(f["id"]) is not None and not isinstance(v.get(f["id"]), bool))
            bools = [v[f["id"]] for v in values.values() if isinstance(v.get(f["id"]), bool)]
            ff = dict(f)
            ff["cov"] = len(vals) + len(bools)
            if vals:
                q = lambda p: vals[min(len(vals) - 1, int(p * (len(vals) - 1)))]
                ff["q"] = [sig(q(0.05)), sig(q(0.5)), sig(q(0.95))]
                ff["min"], ff["max"] = sig(vals[0]), sig(vals[-1])
            if bools:
                ff["true"] = sum(1 for b in bools if b)
            ff["asof"] = dates[-1] if f["g"] in ("px", "corr") else None
            fields.append(ff)
        latest_q = {}
        for tk in TICKERS:
            fm = fundmeta.get(tk) or {}
            latest_q[tk] = [(fm.get("fund") or {}).get("latest_quarter"), (fm.get("fund") or {}).get("latest_quarter_end")]
        cols = ID_COLS + ["lq", "lq_end"] + FIELD_IDS
        out_rows = []
        for tk, _cik, name, sector in UNIVERSE:
            if tk not in values:
                continue
            fm = fundmeta.get(tk) or {}
            v = values[tk]
            row = [tk, name, sector, fm.get("sic") or "", 1 if tk in SP100 else 0, 1 if tk in SP500 else 0, _cik, latest_q[tk][0], latest_q[tk][1]]
            for fid in FIELD_IDS:
                x = v.get(fid)
                if isinstance(x, bool):
                    row.append(1 if x else 0)
                elif fid == "price":
                    # Retain the exact stored close shared by index/provenance.
                    # Display formatting belongs to the client.
                    row.append(x)
                else:
                    row.append(sig(x))
            out_rows.append(row)
        doc = {"generated": now_iso(), "universe": UNIVERSE_METADATA, "asof": {"prices": dates[-1], "facts": max((x[1] or "") for x in latest_q.values())},
               "groups": [{"id": g, "n": n} for g, n in GROUPS], "fields": fields, "cols": cols, "rows": out_rows, "peers": peer_out,
               "sector_etf": SECTOR_ETF, "kpi_families": [[k, n] for k, n, _ in KPI_FAM]}
        size = sb.put_json("screener.json", doc, cache="max-age=300")
        log(f"screener.json {size / 1e6:.2f} MB, {len(out_rows)} rows, {len(FIELD_IDS)} fields")

        def put_src(tk):
            p = provs[tk]
            body = {"tk": tk, "generated": doc["generated"], "asof": dates[-1], "refs": p.refs, "f": p.f}
            if (fundc.get(tk) or {}).get("source_provenance"):
                body["source_provenance"] = fundc[tk]["source_provenance"]
            sb.put_json(f"screener/src/{tk}.json", body, cache="max-age=300")
            return len(json.dumps(body))
        with ThreadPoolExecutor(12) as ex:
            sizes = list(ex.map(put_src, list(provs)))
        log(f"provenance files {len(sizes)}, avg {sum(sizes) / max(1, len(sizes)) / 1e3:.1f} KB")
        cov = {f["id"]: f["cov"] for f in fields}
        run.stats.update({"rows": len(out_rows), "fields": len(FIELD_IDS), "bytes": size, "prices_asof": dates[-1], "seconds_to_fields": round(time.time() - t0, 1),
                          "low_coverage": sorted([k for k, n in cov.items() if n < 25])[:40]})
        return {"rows": len(out_rows), "fields": len(FIELD_IDS), "bytes": size}
