"""Canonical KPI taxonomy and the rules that map a company's KPI definitions onto it.

Each company KPI id (one per reporting basis, so an older-basis series has its own id) is classified on its own:
  canonical id   which standard KPI it is (or none)
  scope          total (the whole company), segment (a segment, region, product or brand), or variant (a narrower or
                 differently adjusted version of the same measure, e.g. ex-fuel, underlying, managed basis)
  member         the segment / region / variant text for non-total scopes
  era            the reporting-basis label when the definition says it is an older or alternative basis
Values are never combined across ids: a canonical series for a company is always the values of one company KPI id.
"""
import re

VERSION = 3

# unit classes and how a company's printed unit converts to the canonical unit (factor applied to the stored value)
UNIT_FACTORS = {
    "pct": {"%": 1, "pct": 1, "percent": 1},
    "pp": {"pp": 1, "ppt": 1, "pts": 1, "%": 1},
    "usd": {"usd": 1, "$": 1},
    "usd_ps": {"usd_per_share": 1, "usd/share": 1, "usd per share": 1, "usd_per_unit": 1, "$/share": 1},
    "usd_pu": {"usd_per_unit": 1, "usd": 1, "cents": 0.01, "$/unit": 1},
    "cents": {"cents": 1, "usd_per_unit": 100},
    "count": {"count": 1, "units": 1, "customers": 1, "users": 1, "clients": 1, "stores": 1, "homes": 1, "members": 1, "subscribers": 1,
              "accounts": 1, "aircraft": 1, "vehicles": 1, "cars": 1, "passengers": 1, "locations": 1, "restaurants": 1, "people": 1},
    "x": {"x": 1, "ratio": 1},
    "days": {"days": 1},
    "boe_d": {"boe/d": 1, "boe_per_day": 1, "mboe/d": 1e3},
    "bbl_d": {"bbl/d": 1, "bo/d": 1, "mbbl/d": 1e3},
    "cf_d": {"cf/d": 1, "mcf/d": 1e3, "mmcf/d": 1e6, "bcf/d": 1e9},
    "asm": {"asm": 1, "asms": 1, "count": 1},
    "rpm": {"rpm": 1, "rpms": 1, "count": 1},
}
UNIT_NAME = {"pct": "%", "pp": "pp", "usd": "USD", "usd_ps": "USD_per_share", "usd_pu": "USD_per_unit", "cents": "cents", "count": "count",
             "x": "x", "days": "days", "boe_d": "boe/d", "bbl_d": "bbl/d", "cf_d": "cf/d", "asm": "ASM", "rpm": "RPM"}


def unit_factor(uclass, unit):
    """Factor from the company's unit to the canonical unit, or None when they are not the same kind of quantity."""
    u = re.sub(r"\s+", " ", (unit or "").strip().lower())
    return UNIT_FACTORS.get(uclass, {}).get(u)


# id, name, family, unit class, how peers aggregate (median or sum), core pattern (matched against the normalized name),
# allowed variant words (still the total-company measure), exclusions, definition, industries where it is a sector KPI
T = []


def kpi(id, name, family, uc, pat, desc, how="median", var=None, excl=None, ind=None, kw=None, higher=True):
    T.append({"id": id, "name": name, "family": family, "uclass": uc, "unit": UNIT_NAME[uc], "how": how, "pat": pat, "var": var, "excl": excl,
              "definition": desc, "industries": ind or [], "kw": kw or pat, "higher_is_better": higher})


GROWTH_EXCL = r"\b(change|growth|increase|decrease|decline|y/y|yoy|q/q|vs\.?|versus|bps|basis points|contribution|impact|effect|delta|variance)\b"

# ---- growth
kpi("same_store_sales_growth", "Comparable sales growth", "Growth", "pct",
    r"\b(comparable|comp|same[- ]store|like[- ]for[- ]like)( store| restaurant| club)? (sales|revenues?)( growth| change| % change| increase| \(?%\)?)?\b|\bcomps\b",
    "Year-over-year change in sales at stores, restaurants or clubs open at least a year, total company as printed.",
    var=r"\b(including|incl\.?|with|excluding|ex\.?) (gasoline|gas|fuel)( prices)?\b|\btotal company\b|\bglobal\b|\bsystemwide\b|\bsystem[- ]wide\b",
    excl=r"\b(ticket|traffic|transactions?|average ticket|eps|ebit|operating|margin|noi|basis)\b",
    ind=["Consumer Discretionary Retail", "Consumer Staples Retail", "Hotels, Restaurants & Leisure"])
kpi("organic_growth", "Organic revenue growth", "Growth", "pct",
    r"\borganic (revenue|sales|net sales|net revenue|growth)( growth| change)?\b|\borganic growth\b",
    "Revenue growth excluding acquisitions, divestitures and currency, total company as printed.",
    excl=r"\b(volume|price|pricing|mix|contribution|impact|effect|eps|margin|operating)\b")
kpi("revenue_growth_cc", "Revenue growth, constant currency", "Growth", "pct",
    r"\b(revenue|revenues|net sales|sales)( growth| change)?,? (in |at )?(constant currency|constant[- ]currency|currency[- ]neutral|fx[- ]neutral|ex[- ]fx|excluding (the impact of )?(foreign )?(currency|fx)|adjusted for foreign exchange|fixed currency)",
    "Total revenue growth with currency effects removed, as printed by the company.",
    excl=r"\b(organic)\b")

# ---- recurring revenue, bookings, backlog
kpi("arr", "Annual recurring revenue", "Recurring revenue", "usd",
    r"\b(annual(ized)? recurring revenue|arr)\b", "Annualized value of recurring subscription contracts at period end, as defined by each company.",
    excl=GROWTH_EXCL + r"|\b(per|customers?|number|count)\b", ind=["Software"])
kpi("net_revenue_retention", "Net revenue retention", "Recurring revenue", "pct",
    r"\b(net (dollar |revenue |expansion )?retention( rate)?|dollar[- ]based net (expansion|retention)( rate)?|ndr|nrr)\b",
    "Recurring revenue this period from customers a year ago over their recurring revenue a year ago.", ind=["Software"])
kpi("rpo", "Remaining performance obligations", "Backlog and orders", "usd",
    r"\b(remaining performance obligations?|rpo)\b", "Contracted revenue not yet recognized (ASC 606 RPO) at period end.",
    var=r"\btotal\b", excl=GROWTH_EXCL + r"|\b(current|crpo|next 12|twelve)\b", how="sum")
kpi("backlog", "Backlog", "Backlog and orders", "usd",
    r"\b(total )?(order )?backlog\b", "Unfilled contracted orders at period end, as printed.",
    excl=GROWTH_EXCL + r"|\b(units|homes|mw|days|months|conversion)\b", how="sum",
    ind=["Aerospace & Defense", "Electrical Equipment", "Machinery", "Construction & Engineering"])
kpi("orders", "Orders / bookings", "Backlog and orders", "usd",
    r"^(total |net |new )?(orders|bookings|new orders|orders received|net bookings|order intake)( received)?$",
    "Value of new orders or bookings in the period.", excl=GROWTH_EXCL, how="sum")
kpi("book_to_bill", "Book-to-bill", "Backlog and orders", "x",
    r"\bbook[- ]to[- ]bill( ratio)?\b", "Orders over revenue for the period.",
    ind=["Semiconductors & Equipment", "Aerospace & Defense", "Electrical Equipment", "Machinery", "Electronic Equipment & Components"])

# ---- users, subscribers, units
kpi("subscribers", "Subscribers / paid memberships", "Users and units", "count",
    r"\b(total |paid |global )?(subscribers|paid memberships|paid members|paid subscribers|memberships)\b",
    "Paying subscribers or members at period end.", excl=GROWTH_EXCL + r"|\b(net (adds|additions|subscriber additions)|revenue|fees?|average|per|churn)\b",
    var=r"\b(end of period|period end|at period end)\b")
kpi("subscriber_net_adds", "Subscriber net additions", "Users and units", "count",
    r"\b(net (subscriber |member(ship)? |customer )?(adds|additions)|paid net (adds|additions|membership additions))\b",
    "Subscribers or paid members gained in the period, net of losses.", higher=True, how="sum")
kpi("customers", "Customers", "Users and units", "count",
    r"^(total )?(customers|active customers|customer accounts|number of customers|customer count|active clients|clients)$",
    "Customers or active customers at period end.", excl=GROWTH_EXCL)
kpi("mau", "Monthly active users", "Users and units", "count",
    r"\b(monthly active (users|people|uniques)|maus?|monthly actives)\b", "Users active in the last month at period end.",
    excl=GROWTH_EXCL + r"|\bper\b")
kpi("dau", "Daily active users", "Users and units", "count",
    r"\b(daily active (users|people|uniques)|daus?|daily actives)\b", "Average daily active users.", excl=GROWTH_EXCL + r"|\bper\b")
kpi("arpu", "Average revenue per user", "Users and units", "usd_pu",
    r"\b(arpu|arpa|arpm|average (monthly )?revenue per (user|member|membership|paying (streaming )?membership|subscriber|account|unit|customer|paid membership)|revenue per (user|member|subscriber|account))\b",
    "Revenue per user, member or account for the period, as defined by each company.", excl=GROWTH_EXCL)
kpi("units_shipped", "Units shipped / delivered", "Users and units", "count",
    r"^(total )?(units? (sold|shipped|delivered)|deliveries|vehicle deliveries|unit shipments|shipments|units)$",
    "Units shipped, sold or delivered in the period.", excl=GROWTH_EXCL, how="sum")
kpi("store_count", "Store / location count", "Users and units", "count",
    r"^(total )?(number of )?(stores|restaurants|locations|clubs|warehouses|units|hotels|properties)( open)?( at (period|quarter|year) end| \(end of period\)| at end of (period|quarter|year))?$|^(total )?(store|restaurant|location|warehouse) count$|^(stores|restaurants|warehouses) open at end of (period|quarter|year)$",
    "Stores, restaurants, clubs or other locations open at period end.", excl=GROWTH_EXCL + r"|\b(opened|closed|new|net)\b")

# ---- marketplaces and payments
kpi("gross_bookings", "Gross bookings", "Marketplaces and payments", "usd",
    r"^(total )?gross (bookings|booking value)$", "Total value of bookings transacted on the platform.", how="sum")
kpi("gmv", "Gross merchandise volume", "Marketplaces and payments", "usd",
    r"^(total )?(gross merchandise (volume|value|sales)|gmv)$", "Total value of goods sold through the platform.", how="sum")
kpi("take_rate", "Take rate", "Marketplaces and payments", "pct",
    r"^(total |transaction |net )?take rate$", "Platform revenue over the value transacted.")
kpi("payment_volume", "Total payment volume", "Marketplaces and payments", "usd",
    r"^(total )?(payment volume|payments volume|tpv|total payment volume|purchase volume|payments volume \(tpv\)|nominal payments volume)$",
    "Value of payments processed in the period.", how="sum")

# ---- banks
kpi("nim", "Net interest margin", "Banks", "pct",
    r"\b(net interest margin|nim)\b", "Net interest income (annualized) over average interest-earning assets.",
    var=r"\b(fte|te|taxable[- ]equivalent|fully taxable[- ]equivalent|tax[- ]equivalent|non-gaap|on a fte basis)\b",
    excl=GROWTH_EXCL + r"|\b(ex(cluding)?|markets|managed)\b", ind=["Banks"])
kpi("efficiency_ratio", "Efficiency ratio", "Banks", "pct",
    r"^(gaap |reported )?efficiency ratio$", "Noninterest expense over revenue.", ind=["Banks"], higher=False)
kpi("cet1_ratio", "CET1 ratio", "Banks", "pct",
    r"\b(cet1|common equity tier 1)( capital)? ratio\b", "Common equity tier 1 capital over risk-weighted assets.",
    var=r"\b(standardized|standardized approach|transitional|fully phased[- ]in)\b", excl=GROWTH_EXCL + r"|\b(advanced|requirement|minimum)\b",
    ind=["Banks", "Capital Markets", "Financial Services"])
kpi("rotce", "Return on tangible common equity", "Banks", "pct",
    r"\b(return on (average )?tangible common (equity|shareholders'? equity)|rotce)\b", "Net income to common over average tangible common equity, annualized.",
    excl=GROWTH_EXCL, var=r"\b(non-gaap|annualized)\b", ind=["Banks", "Capital Markets", "Financial Services"])
kpi("roe", "Return on common equity", "Banks", "pct",
    r"^(return on (average )?(common )?(shareholders'? |stockholders'? )?equity|roe|return on equity \(roe\))$", "Net income over average common equity, annualized.",
    excl=r"\btangible\b", var=r"\b(annualized)\b")
kpi("net_charge_off_rate", "Net charge-off rate", "Banks", "pct",
    r"^(total )?net charge[- ]offs? (rate|ratio|as a % of average loans|to average loans)$|^net charge[- ]off rate$",
    "Net charge-offs (annualized) over average loans.", higher=False)
kpi("loans", "Total loans", "Banks", "usd",
    r"^(total |period[- ]end |end of period )?(loans|loans and leases|loans held for investment|loans, net)$", "Loans outstanding at period end.", how="sum")
kpi("avg_loans", "Average loans", "Banks", "usd", r"^(total )?average (total )?(loans|loans and leases)$", "Average loans outstanding in the period.", how="sum")
kpi("deposits", "Total deposits", "Banks", "usd", r"^(total |period[- ]end )?deposits$", "Deposits at period end.", how="sum")
kpi("avg_deposits", "Average deposits", "Banks", "usd", r"^(total )?average (total )?deposits$", "Average deposits in the period.", how="sum")
kpi("book_value_per_share", "Book value per share", "Banks", "usd_ps", r"^book value per (common |diluted )?share$", "Common equity over common shares outstanding at period end.")
kpi("tbv_per_share", "Tangible book value per share", "Banks", "usd_ps", r"^tangible book value per (common |diluted )?share$", "Tangible common equity over common shares outstanding.")
kpi("aum", "Assets under management", "Asset management", "usd",
    r"^(total )?(assets under management|aum|total aum)( \(aum\))?$", "Client assets the company manages at period end.", how="sum")
kpi("net_flows", "Net flows", "Asset management", "usd",
    r"^(total )?(long[- ]term )?net (flows|inflows|new assets|asset flows)$", "Client inflows minus outflows in the period.", how="sum")

# ---- insurance
kpi("combined_ratio", "Combined ratio", "Insurance", "pct",
    r"\bcombined ratio\b", "Losses, loss expenses and underwriting expenses over net premiums earned.",
    var=r"\b(gaap|reported|p&c|property and casualty|property casualty|statutory)\b",
    excl=GROWTH_EXCL + r"|\b(underlying|ex(cluding)?|accident year|current year|core|catastrophe|cat|points?|prior year|development)\b",
    ind=["Insurance"], higher=False)
kpi("loss_ratio", "Loss ratio", "Insurance", "pct", r"^(total |gaap )?(loss and lae|loss and loss adjustment expense|loss)( expense)? ratio$",
    "Losses and loss adjustment expenses over net premiums earned.", higher=False, ind=["Insurance"])
kpi("net_premiums_written", "Net premiums written", "Insurance", "usd", r"^(total )?net (premiums|written premiums) written$|^net written premiums$|^total net premiums written$",
    "Premiums written net of reinsurance in the period.", how="sum", ind=["Insurance"])
kpi("net_premiums_earned", "Net premiums earned", "Insurance", "usd", r"^(total )?net (premiums|earned premiums) earned$|^net earned premiums$",
    "Premiums earned net of reinsurance in the period.", how="sum", ind=["Insurance"])
kpi("medical_care_ratio", "Medical cost ratio", "Insurance", "pct",
    r"^(total |consolidated )?(medical (care|benefits?|loss|cost) ratio|benefit expense ratio|medical care ratio|mlr|mcr|mbr)( \((mcr|mbr|mlr)\))?$",
    "Medical costs over premium revenue.", higher=False, ind=["Health Care Providers & Services"])

# ---- real estate, lodging
kpi("ffo_per_share", "FFO per share", "Real estate", "usd_ps",
    r"^(nareit |nareit[- ]defined |reported )?(ffo|funds from operations)( \(ffo\))?( attributable to common (stockholders|shareholders))?,? per (diluted |common |basic and diluted )?(share|unit)( - diluted)?$|^(basic and )?diluted (ffo|funds from operations) per (share|unit)( \(ffops\))?$|^ffo per share( \(ffops\))?$",
    "NAREIT-defined funds from operations per diluted share.", var=r"\b(nareit|diluted|ffops|as defined)\b", excl=r"\b(core|normalized|adjusted|affo|growth|change)\b",
    ind=["REITs"])
kpi("core_ffo_per_share", "Core / normalized FFO per share", "Real estate", "usd_ps",
    r"^(core|normalized|adjusted|comparable) (ffo|funds from operations)( \(core ffo\))?,? per (diluted |common )?(share|unit)$",
    "FFO adjusted for items each company defines as non-core, per diluted share.", excl=r"\b(affo|excluding|growth|change)\b", ind=["REITs"])
kpi("affo_per_share", "AFFO per share", "Real estate", "usd_ps",
    r"^(affo|adjusted funds from operations)( \(affo\))?,? per (diluted |common )?(share|unit)$", "Adjusted funds from operations per diluted share.", ind=["REITs"])
kpi("occupancy", "Occupancy", "Real estate", "pct",
    r"^(period[- ]end |ending |average |total |portfolio |consolidated |operating portfolio |in[- ]service )?(portfolio )?(occupancy|occupancy rate|percent (leased|occupied)|leased percentage|percentage leased)( rate| percentage)?( at (period|quarter) end)?$",
    "Share of the portfolio occupied or leased (period end or average, as printed).",
    var=r"\b(owned and managed|consolidated|total portfolio|operating portfolio|press release|release narrative)\b", excl=r"\b(same|change|growth)\b",
    ind=["REITs", "Hotels, Restaurants & Leisure"])
kpi("same_property_noi_growth", "Same-property NOI growth", "Real estate", "pct",
    r"^(same[- ](store|property|center|asset)|comparable|same[- ]store cash) (cash )?(noi|net operating income)( growth| change)?( - cash)?$",
    "Year-over-year change in net operating income for properties owned in both periods.", ind=["REITs"])
kpi("revpar", "RevPAR", "Lodging", "usd_pu",
    r"^(system[- ]?wide |comparable |systemwide comparable )?(revpar|revenue per available room)$", "Room revenue per available room night.",
    ind=["Hotels, Restaurants & Leisure"])
kpi("revpar_growth", "RevPAR growth", "Lodging", "pct",
    r"^(system[- ]?wide |comparable |systemwide comparable )?(revpar|revenue per available room) (growth|change|increase)$",
    "Year-over-year change in RevPAR, as printed.", ind=["Hotels, Restaurants & Leisure"])
kpi("adr", "Average daily rate", "Lodging", "usd_pu", r"^(average daily rate|adr)$", "Room revenue per room night sold.",
    ind=["Hotels, Restaurants & Leisure"])

# ---- airlines
kpi("load_factor", "Passenger load factor", "Airlines", "pct", r"^(passenger )?load factor$", "Revenue passenger miles over available seat miles.",
    ind=["Transportation"])
kpi("asm", "Available seat miles", "Airlines", "asm", r"^(available seat miles|capacity)( \(asms?\))?$|^asms?$", "Seats flown times miles flown in the period.",
    how="sum", ind=["Transportation"])
kpi("rpm", "Revenue passenger miles", "Airlines", "rpm", r"^(revenue passenger miles|traffic)( \(rpms?\))?$|^rpms?$", "Paying passengers times miles flown.",
    how="sum", ind=["Transportation"])
kpi("casm_ex", "CASM excluding fuel", "Airlines", "cents",
    r"^(casm|cost per available seat mile)[- ,]*(ex|excluding)[- ](fuel|fuel and special items|fuel and special charges|fuel, special items)( \(casm-ex\))?$|^casm-ex$",
    "Operating cost excluding fuel and special items per available seat mile.", higher=False, ind=["Transportation"])
kpi("trasm", "Total revenue per ASM", "Airlines", "cents", r"^(trasm|total revenue per available seat mile|tras m)( \(trasm\))?$|^revenue per available seat mile( \(rasm\))?$|^rasm$",
    "Total operating revenue per available seat mile.", ind=["Transportation"])

# ---- energy
kpi("production_boe", "Total production (BOE/d)", "Energy", "boe_d",
    r"^(total |net )?(production|production volumes?|oil equivalent production)(,|:| -)? ?(oil equivalent|boe|total)?(,|:| -)? ?(total)?( \(boe/d\)| \(mboe/d\))?$|^production, oil equivalent, total$|^total production - total$|^total production$",
    "Total oil-equivalent production per day.", var=r"\b(total|oil equivalent|boe|reported|net|average|daily)\b",
    excl=r"\b(oil|natural gas|ngl|liquids|gas)\b(?!.*equivalent)|\b(adjusted|excluding|growth|change)\b", how="sum", ind=["Oil & Gas Exploration & Production", "Integrated Oil & Refining"])
kpi("oil_production", "Oil production (bbl/d)", "Energy", "bbl_d",
    r"^(crude )?oil production( - total|, total)?$|^production, oil, total$|^(total )?oil( production)?$|^crude oil( and condensate)?( production)?$",
    "Crude oil (and condensate) production per day.", excl=r"\b(equivalent|adjusted|growth)\b", how="sum", ind=["Oil & Gas Exploration & Production", "Integrated Oil & Refining"])
kpi("gas_production", "Natural gas production (cf/d)", "Energy", "cf_d",
    r"^(natural )?gas production( - total|, total)?$|^production, natural gas, total$|^natural gas( production)?$",
    "Natural gas production per day.", excl=r"\b(liquids|ngl|adjusted|growth)\b", how="sum", ind=["Oil & Gas Exploration & Production", "Integrated Oil & Refining"])

# ---- telecom
kpi("postpaid_phone_net_adds", "Postpaid phone net additions", "Telecom", "count",
    r"^(total )?postpaid phone net (adds|additions|customer additions)$", "Postpaid phone customers gained in the period, net of disconnects.")
kpi("postpaid_phone_churn", "Postpaid phone churn", "Telecom", "pct",
    r"^(total )?postpaid phone churn$", "Postpaid phone disconnects over average postpaid phone customers, monthly.", higher=False)

# ---- company-wide financial measures printed in releases
FIN_EXCL = r"\b(reconciliation|adjustments?|items|impact|effect|tax effects?|less|plus|starting measure|growth|change|y/y|yoy|q/q|guidance|outlook|conversion|per share|eps)\b"
kpi("total_revenue", "Total revenue", "Financial", "usd",
    r"^(total )?(net )?(revenues?|net sales|sales|net revenues?|operating revenues?)$|^total company (net sales|revenues?)$",
    "Total revenue or net sales for the period, as printed in the release.", var=r"\b(consolidated|reported|gaap|total company|worldwide)\b",
    excl=FIN_EXCL + r"|\b(fixed currency|constant currency|other|fee|interest)\b", how="sum")
kpi("adjusted_ebitda", "Adjusted EBITDA", "Financial", "usd",
    r"^(total |consolidated )?(adjusted|non-gaap adjusted|non-gaap) ebitda( \(non-gaap\))?$", "EBITDA adjusted for items each company excludes.",
    excl=FIN_EXCL + r"|\b(margin|%|ratio)\b", how="sum")
kpi("adjusted_ebitda_margin", "Adjusted EBITDA margin", "Financial", "pct",
    r"^(adjusted|non-gaap) ebitda margin( \(non-gaap\))?$", "Adjusted EBITDA over revenue.")
kpi("adjusted_operating_income", "Adjusted operating income", "Financial", "usd",
    r"^(total |consolidated )?(adjusted|non-gaap) operating (income|profit|earnings)( \(non-gaap\))?$", "Operating income adjusted for items each company excludes.",
    excl=FIN_EXCL + r"|\b(margin|%)\b", how="sum")
kpi("adjusted_operating_margin", "Adjusted operating margin", "Financial", "pct",
    r"^(adjusted|non-gaap) operating (margin|profit margin|income margin)( \(non-gaap\))?$", "Adjusted operating income over revenue.")
kpi("adjusted_eps", "Adjusted EPS", "Financial", "usd_ps",
    r"^(adjusted|non-gaap|core|operating|comparable|adjusted diluted|non-gaap diluted)( diluted)? (eps|earnings per (diluted )?share|earnings \(loss\) per (diluted )?share|net (income|earnings) per (diluted )?share)( \(diluted\))?( \((non-gaap|diluted|adjusted)\))?$|^(diluted )?(eps|earnings per share),? (adjusted|non-gaap)$|^non-gaap earnings per diluted share$|^adjusted eps \(diluted\)$",
    "Diluted earnings per share excluding items each company adjusts out.", var=r"\b(diluted|non-gaap|adjusted)\b",
    excl=r"\b(basic|adjustments?|items|impact|effect|growth|change|guidance|outlook|constant currency|currency[- ]neutral)\b")
kpi("adjusted_net_income", "Adjusted net income", "Financial", "usd",
    r"^(adjusted|non-gaap)( net)? (income|earnings)( \(non-gaap\))?$|^(adjusted|non-gaap) net (income|earnings) attributable to [a-z .,&]+$", "Net income excluding items each company adjusts out.",
    excl=FIN_EXCL + r"|\b(margin|before)\b", how="sum")
kpi("fcf", "Free cash flow", "Cash flow", "usd",
    r"^(free cash flow|fcf)( \(non-gaap\)| \(company definition\))?$", "Operating cash flow less capital expenditures, as each company defines it.",
    excl=FIN_EXCL + r"|\b(margin|%|percentage|adjusted|upstream|segment|trailing|last twelve|ttm|continuing)\b", how="sum")
kpi("fcf_margin", "Free cash flow margin", "Cash flow", "pct",
    r"^free cash flow (margin|as a percentage of (revenue|net sales|sales))$", "Free cash flow over revenue.")
kpi("capex", "Capital expenditures", "Cash flow", "usd",
    r"^(total )?(capital expenditures|capital spending|capex|purchases of property,? (plant )?and equipment|additions to property,? (plant )?and equipment)( \(.*\))?$|^capital expenditures, total( capital)?$",
    "Cash spent on property, plant and equipment in the period (stored as a positive outflow).",
    excl=r"\b(sustaining|growth|maintenance|percent|%|ratio|guidance|outlook|non-gaap|segment|acquisitions)\b", how="sum", higher=False)
kpi("operating_cash_flow", "Operating cash flow", "Cash flow", "usd",
    r"^(net )?cash (provided by|from|generated from|flow from) operating activities$|^operating cash flow$|^cash flow from operations$",
    "Net cash provided by operating activities.", excl=FIN_EXCL, how="sum")
kpi("share_repurchases", "Share repurchases", "Capital returns", "usd",
    r"^(share repurchases|repurchases? of common stock|stock repurchases|shares? repurchased \(\$\)|common stock repurchased|purchases of common stock|buybacks?)$",
    "Cash spent repurchasing the company's shares in the period.", how="sum")
kpi("dividends_paid", "Dividends paid", "Capital returns", "usd", r"^(cash )?dividends paid( to (common )?(shareholders|stockholders))?$|^common dividends paid$",
    "Cash dividends paid to common shareholders in the period.", how="sum")
kpi("net_debt", "Net debt", "Balance sheet", "usd", r"^net debt$", "Total debt less cash and equivalents, as each company defines it.", how="sum", higher=False)
kpi("net_leverage", "Net leverage", "Balance sheet", "x",
    r"^(net leverage( ratio)?|net debt (to|/) (adjusted )?ebitda( ratio)?)$", "Net debt over trailing adjusted EBITDA.", higher=False)

# ---- segment-scope measures (mapped with scope=segment; not compared across companies as one number)
kpi("segment_revenue", "Segment revenue", "Segments", "usd",
    r"\b(revenues?|net sales|sales|net revenues?)\b", "Revenue of one reportable segment, region or product line.", how="sum")
kpi("segment_operating_income", "Segment operating income", "Segments", "usd",
    r"\b(operating (income|profit|earnings)|segment (profit|earnings|income))\b", "Operating income or segment profit of one reportable segment.", how="sum")

BY_ID = {t["id"]: t for t in T}
SEGMENT_ONLY = {"segment_revenue", "segment_operating_income"}
for t in T:
    t["_rx"] = re.compile(t["pat"], re.I)
    t["_var"] = re.compile(t["var"], re.I) if t.get("var") else None
    t["_excl"] = re.compile(t["excl"], re.I) if t.get("excl") else None
    t["_kw"] = re.compile(t["kw"], re.I)


def nn(s):
    s = str(s or "").replace("’", "'").replace("&", " and ").replace("–", "-").replace("—", "-")
    s = re.sub(r"\bu\.\s?s\.(\s?a\.)?", "us ", s, flags=re.I)
    return re.sub(r"\s+", " ", s).strip().lower()


GENERIC = set("""total consolidated company companywide firmwide overall reported as gaap the of for and in a an at on quarter quarterly q period end ending
rate percentage percent pct metric metrics kpi worldwide enterprise corporation corp inc release press narrative supplement deck presentation earnings
summary highlights key defined by is ttm basis value values amount amounts net reported: figure figures stated print printed""".split())
ERA_RX = re.compile(r"(\b(pre|before|prior to|through|until|thru)[- ]?(fy|q[1-4] ?)?'?\d{2,4}\b|\b(fy|q[1-4] ?fy|cy)?'?\d{2,4}\s*(-|to|through)\s*(q[1-4] ?)?(fy|cy)?'?\d{2,4}\b|"
                    r"\bas originally reported\b|\boriginally reported\b|\bprior (basis|presentation|methodology|definition)\b|\bprevious (basis|definition)\b|\bold basis\b|"
                    r"\blegacy (basis|presentation|segments?)\b|\b(former|historical) (basis|segments?|presentation)\b|\b[a-z0-9-]+ basis\b(?=.*\b(19|20)\d{2}\b)|\bera\b)", re.I)
SEG_CTX = re.compile(r"\bsegments?\b|\bby (region|geograph\w*|product\w*|business\w*|brand|division|market|country|channel|program|line|unit|service|platform|franchise|end market|customer type)\b|"
                     r"\bdivisions?\b|\bbusiness units?\b|\breportable\b|\bregional\b|\bgeograph\w*\b|\bproduct lines?\b|\bbrands?\b", re.I)
TOTAL_CTX = re.compile(r"\b(total|consolidated|company|overall|firmwide|enterprise)\b", re.I)
SEGMENT_SEP = re.compile(r"\s(-|–|—)\s|:\s|,\s|\s\(|\)")


ABBR = set("""nim fte te mcr mbr mlr ffops ffo affo asm asms rpm rpms arr aum auc rpo gmv tpv roe rotce ebitda eps cet1 nrr ndr arpu arpa arm casm casm-ex
trasm rasm adr boe nareit niw dau mau daus maus revpar kpi kpis ytd ltm""".split())


def _strip_paren_meta(s):
    """Parenthetical notes that do not change what is measured: (non-GAAP), (press release), (FTE), abbreviations of the measure."""
    s = re.sub(r"\((non-gaap|gaap|press release|release|release narrative|supplement|deck|presentation|reported|as reported|unaudited|in millions|in billions|in thousands|"
               r"millions|billions|thousands|%|percent|\$|usd|end of period|eop|period end|annualized|diluted|basic and diluted|total|consolidated|company definition|"
               r"non-gaap measure|a non-gaap measure|see description|as defined|as stated in (the )?release( text)?|release text|outflow|outflow, stored positive|"
               r"outflow, shown positive|stored positive|shown positive|cash flow statement)\)", " ", s)
    return re.sub(r"\(([a-z0-9-]{2,8})\)", lambda m: " " if m.group(1) in ABBR else m.group(0), s)


ERA_WORDS = {"basis", "to", "through", "thru", "structure", "segments", "recast", "era", "original", "originally", "definition", "restated", "as", "from", "on",
             "until", "before", "pre", "prior", "legacy", "former", "previous", "old", "historical", "onward", "onwards", "since"}
PERIODISH = re.compile(r"^((fy|cy|q[1-4]|h[12])?'?\d{2,4}(q[1-4]|h[12])?|[1-4]q\d{2,4}|q[1-4]|fy|cy|\d{4}s)$")
ERA_PAREN = re.compile(r"[\(\[]([^)\]]*(\b(19|20)\d{2}\b|\b\d?q\d{2,4}\b|\b[1-4]q\d{2}\b|\bfy\s?'?\d{2,4}\b|\bq[1-4] (fy|cy)?\s?'?\d{2,4}\b)[^)\]]*)[\)\]]", re.I)


# "(AMB basis)", "(ProLogis basis)", "[volumes basis]": a named reporting basis. Methodological bases (managed, cash,
# accrual, constant currency ...) are variants, not eras.
METHOD_BASIS = r"managed|cash|accrual|volumes?|gaap|non-gaap|reported|as[- ]reported|adjusted|operating|fte|taxable[- ]equivalent|tax[- ]equivalent|constant[- ]currency|" \
               r"currency[- ]neutral|fixed[- ]currency|same[- ]store|comparable|pro[- ]forma|proforma|organic|economic|statutory|consolidated|segment|net|gross"
BASIS_PAREN = re.compile(r"[\(\[](?!(?:" + METHOD_BASIS + r")\s+basis)([a-z0-9&.'\- ]{2,40}?\s+basis)[\)\]]", re.I)


def core_text(name):
    """The name without notes that do not change the measure and without reporting-basis labels."""
    s = BASIS_PAREN.sub(" ", ERA_PAREN.sub(" ", _strip_paren_meta(name)))
    return re.sub(r"\s+", " ", s).strip(" ,;:-")


def residual(t, name, extra_generic=()):
    """Text left in the name after the canonical phrase, allowed variants and generic words are removed."""
    era = bool(ERA_RX.search(name) or ERA_PAREN.search(name) or BASIS_PAREN.search(name))
    s = core_text(name)
    m = t["_rx"].search(s)
    if not m:
        return None
    rest = s[:m.start()] + " " + s[m.end():]
    if t["_var"]:
        rest = t["_var"].sub(" ", rest)
    rest = ERA_RX.sub(" ", rest)
    words = [w for w in re.findall(r"[a-z0-9'+]+", rest) if w not in GENERIC and w not in extra_generic and len(w) > 1 and not PERIODISH.match(w)
             and not (era and w in ERA_WORDS)]
    return " ".join(words)


def era_of(*texts):
    for s in texts:
        m = ERA_PAREN.search(s or "")
        if m:
            return m.group(1).strip()
        m = ERA_RX.search(s or "")
        if m:
            return m.group(0).strip(" ()")
        m = BASIS_PAREN.search(s or "")
        if m:
            return m.group(1).strip()
    return None


def classify(d, parent=None, company_words=()):
    """Rule mapping for one KPI definition. Returns a dict (canonical, scope, member, era, confidence, reason) or None.
    d: {name, category, subcategory, unit, parent_id, sums_to_parent}; parent: the parent definition when there is one."""
    name = nn(d.get("name"))
    cat, sub = nn(d.get("category")), nn(d.get("subcategory"))
    pname = nn(parent.get("name")) if parent else ""
    era = era_of(d.get("name"), d.get("category"), d.get("subcategory"))
    best = None
    for t in T:
        if t["id"] in SEGMENT_ONLY:
            continue
        if not t["_rx"].search(core_text(name)):
            continue
        f = unit_factor(t["uclass"], d.get("unit"))
        if f is None:
            continue
        if t["_excl"] and t["_excl"].search(re.sub(r"\(.*?\)", " ", name) if t["id"] != "capex" else name):
            continue
        res = residual(t, name, company_words)
        if res is None:
            continue
        reason = [f"name matches {t['name']!r}", f"unit {d.get('unit')} is {t['unit']}" + (f" (x{f})" if f != 1 else "")]
        scope, member, conf = "total", None, 0.95
        if res:
            # leftover words: a segment, region, product, or a narrower variant of the measure
            scope, member, conf = ("segment" if _segmentish(res) else "variant"), res, 0.85
            reason.append(f"qualifier {res!r} makes it a {scope} figure")
        elif d.get("sums_to_parent"):
            scope, member, conf = "segment", pname or sub or cat, 0.8
            reason.append("it is a component that sums to its parent")
        elif parent and t["_rx"].search(core_text(pname)):
            scope, member, conf = "segment", pname, 0.75
            reason.append(f"its parent {pname!r} is the same measure")
        elif SEG_CTX.search(cat + " " + sub) and not TOTAL_CTX.search(sub or "") and not re.search(r"\b(total|consolidated|company)\b", name):
            # the member is whichever of category / subcategory is not the "by segment" header
            mem = (d.get("subcategory") if d.get("subcategory") and not SEG_CTX.search(sub) else
                   d.get("category") if not SEG_CTX.search(cat) else None)
            if not mem or t["_rx"].search(nn(mem)) or SEG_CTX.search(nn(mem)):
                # a "by segment" table with no member named: this may be the table's total line; left to the model
                continue
            scope, member, conf = "segment", mem, 0.7
            reason.append(f"listed under {d.get('category')!r}" + (f" / {d.get('subcategory')!r}" if d.get("subcategory") else ""))
        else:
            reason.append("no segment, region or variant qualifier")
        if era:
            reason.append(f"reporting basis {era!r}")
        cand = {"canonical": t["id"], "scope": scope, "member": member, "era": era, "confidence": conf, "factor": f, "reason": "; ".join(reason)}
        # the first matching taxonomy entry wins unless a later one maps to total scope and this one does not
        if best is None or (best["scope"] != "total" and scope == "total"):
            best = cand
    if best:
        return best
    # segment-level revenue and operating income: the name is the measure plus a member (segment, region, product)
    if unit_factor("usd", d.get("unit")) and not re.search(GROWTH_EXCL + r"|\b(margin|per|%|ratio|reconciliation|adjustments?|impact|eliminations?|intersegment|corporate|unallocated|other)\b", name):
        for sid in ("segment_revenue", "segment_operating_income"):
            t = BY_ID[sid]
            res = residual(t, name, company_words)
            if res is None:
                continue
            seg_ctx = SEG_CTX.search(cat + " " + sub) or d.get("sums_to_parent")
            if res and (seg_ctx or SEGMENT_SEP.search(d.get("name") or "")) and not re.search(r"\b(total|consolidated)\b", res):
                return {"canonical": sid, "scope": "segment", "member": res, "era": era, "confidence": 0.8 if seg_ctx else 0.7, "factor": 1,
                        "reason": f"{t['name'].lower()} for {res!r}" + (f", listed under {d.get('subcategory') or d.get('category')!r}" if seg_ctx else "")
                        + (f"; reporting basis {era!r}" if era else "")}
    return None


GEO = r"americas?|north america|latin america|europe|emea|eme?ia|asia|apac|asia pacific|china|greater china|japan|international|domestic|u\.?s\.?|united states|canada|mexico|brazil|india|uk|germany|france|rest of world|row|west|east|south|north|central|pacific"


def _segmentish(res):
    """Leftover words look like a segment or region rather than a methodological variant."""
    if re.search(r"\b(" + GEO + r")\b", res):
        return True
    if re.search(r"\b(excluding|ex|underlying|core|adjusted|managed|cash|constant|currency|organic|reported|average|trailing|last|annualized|net|gross|fuel|"
                 r"catastrophe|cat|accident|current|prior|per|incremental|pro forma|proforma|run[- ]rate|guidance|outlook|target)\b", res):
        return False
    return True


def candidates(d):
    """Taxonomy entries a definition could plausibly be (keyword hit and compatible unit), for the LLM to choose from."""
    name = nn(d.get("name"))
    out = []
    for t in T:
        if t["id"] in SEGMENT_ONLY:
            continue
        if unit_factor(t["uclass"], d.get("unit")) is None:
            continue
        if t["_excl"] and t["_excl"].search(re.sub(r"\(.*?\)", " ", name)):
            continue
        if re.search(r"\b(change|vs\.?|versus|impact|effect|benefit to|adjustments? to|reconciliation|tax effects?)\b", name) and t["uclass"] != "pct":
            continue
        if t["_kw"].search(name) or _loose(t, name):
            out.append(t)
    return out


LOOSE = {
    "same_store_sales_growth": r"\b(comparable|comp|same[- ]store|like[- ]for[- ]like)\b.*\bsales\b",
    "organic_growth": r"\borganic\b",
    "arr": r"recurring revenue|\barr\b",
    "net_revenue_retention": r"retention",
    "nim": r"interest margin",
    "efficiency_ratio": r"efficiency",
    "cet1_ratio": r"\bcet\s?1\b|common equity tier",
    "rotce": r"tangible common equity|rotce",
    "combined_ratio": r"combined ratio",
    "medical_care_ratio": r"medical (care|cost|loss|benefit)|\bmlr\b|\bmcr\b|\bmbr\b",
    "ffo_per_share": r"\bffo\b|funds from operations",
    "core_ffo_per_share": r"\bffo\b|funds from operations",
    "affo_per_share": r"\baffo\b",
    "occupancy": r"occupan|leased",
    "revpar": r"revpar|per available room",
    "load_factor": r"load factor",
    "subscribers": r"subscriber|member",
    "arpu": r"\barpu|per (user|member|subscriber|account)",
    "backlog": r"backlog",
    "book_to_bill": r"book.to.bill",
    "take_rate": r"take rate",
    "gross_bookings": r"gross booking",
    "units_shipped": r"units|deliveries|shipments",
    "production_boe": r"oil equivalent|\bboe\b|total production",
    "adjusted_eps": r"\beps\b|per (diluted )?share",
    "adjusted_ebitda": r"ebitda",
    "fcf": r"free cash flow",
    "capex": r"capital expenditure|capex",
}


def _loose(t, name):
    p = LOOSE.get(t["id"])
    return bool(p and re.search(p, name, re.I))


def public_taxonomy():
    return [{k: v for k, v in t.items() if not k.startswith("_") and k not in ("pat", "var", "excl", "kw")} | {"match": t["pat"]} for t in T]
