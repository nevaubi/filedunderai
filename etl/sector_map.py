"""Industry groups inside each GICS-style sector, assigned from the company's SEC SIC code.

The sector comes from the universe (etl/universe.py). The industry is the first rule in that sector whose SIC list or
range contains the company's SIC code; codes that match no rule fall to the sector's last (catch-all) industry.
A short override list fixes SIC codes that describe a company's history rather than its business today.
The same tables are published in sectors.json so the page can show exactly how every company was placed.
"""
import re

# sector -> [(industry name, [SIC codes or "lo-hi" ranges])]; the last entry of each sector is its catch-all
INDUSTRIES = {
    "Information Technology": [
        ("Semiconductors & Equipment", ["3674", "3559", "3827", "3825"]),
        ("Technology Hardware & Storage", ["3570-3572"]),
        ("Communications Equipment", ["3576", "3661", "3663", "3669"]),
        ("Software", ["7370", "7372", "7374"]),
        ("IT Services", ["7371", "7373", "7389", "8741"]),
        ("Electronic Equipment & Components", []),
    ],
    "Communication Services": [
        ("Telecommunication Services", ["4812", "4813", "4899"]),
        ("Media", ["2711", "4833", "4841", "7311"]),
        ("Entertainment", ["7372", "7812-7841", "7900-7999"]),
        ("Interactive Media & Services", []),
    ],
    "Consumer Discretionary": [
        ("Automobiles & Components", ["3711-3716"]),
        ("Household Durables", ["1520-1531", "2510-2599", "3630-3639", "3944"]),
        ("Textiles, Apparel & Luxury Goods", ["2300-2399", "3021", "3100-3199", "3910-3915"]),
        ("Hotels, Restaurants & Leisure", ["4400", "4700", "5140", "5810-5812", "7011", "7340", "7990"]),
        ("Consumer Discretionary Retail", []),
    ],
    "Consumer Staples": [
        ("Food, Beverage & Tobacco", ["0100-0999", "2000-2111"]),
        ("Household & Personal Products", ["2670", "2840-2844"]),
        ("Consumer Staples Retail", []),
    ],
    "Energy": [
        ("Oil & Gas Exploration & Production", ["1311", "6792"]),
        ("Integrated Oil & Refining", ["2911"]),
        ("Energy Equipment & Services", ["1381-1389", "3533"]),
        ("Oil & Gas Midstream", []),
    ],
    "Financials": [
        ("Banks", ["6021-6036"]),
        ("Capital Markets", ["6200-6299", "7320", "7370", "6798"]),
        ("Insurance", ["6311-6411"]),
        ("Financial Services", []),
    ],
    "Health Care": [
        ("Pharmaceuticals", ["2834"]),
        ("Biotechnology", ["2836"]),
        ("Health Care Equipment & Supplies", ["2835", "3841-3851"]),
        ("Life Sciences Tools & Services", ["3823", "3826", "3829", "8731"]),
        ("Health Care Technology", ["7370-7379"]),
        ("Health Care Providers & Services", []),
    ],
    "Industrials": [
        ("Aerospace & Defense", ["3480", "3720-3769", "3812"]),
        ("Building Products", ["3430", "3585", "3822", "3630", "5070"]),
        ("Construction & Engineering", ["1600-1799"]),
        ("Electrical Equipment", ["3590", "3600-3679"]),
        ("Transportation", ["4011-4731"]),
        ("Trading Companies & Distributors", ["5000-5099", "5200", "5500"]),
        ("Commercial & Professional Services", ["2320", "4953", "7000-8999"]),
        ("Machinery", []),
    ],
    "Materials": [
        ("Metals & Mining", ["1000-1099", "3312-3399"]),
        ("Construction Materials", ["1400-1499", "3241"]),
        ("Containers, Packaging & Paper", ["2621", "2650", "2670", "3411", "3990"]),
        ("Chemicals", []),
    ],
    "Utilities": [
        ("Gas & Water Utilities", ["4924", "4941"]),
        ("Multi-Utilities", ["4931", "4932"]),
        ("Electric Utilities", []),
    ],
    "Real Estate": [
        ("Real Estate Management & Services", ["6500-6599", "7389"]),
        ("REITs", []),
    ],
}

# SIC codes that describe a company's origins rather than its business today
OVERRIDES = {
    "FTNT": ("Software", "SIC 3577 (computer peripheral equipment) predates its security software business"),
    "PANW": ("Software", "SIC 3577 (computer peripheral equipment); revenue is security software and subscriptions"),
    "CDW": ("IT Services", "SIC 5961 (catalog retail); an IT products reseller and services provider"),
    "TEL": ("Electronic Equipment & Components", "SIC 5065 (electronic parts wholesale); a connector and sensor manufacturer"),
    "SHW": ("Chemicals", "SIC 5200 (building materials retail); a paints and coatings manufacturer"),
    "COHR": ("Electronic Equipment & Components", "SIC 3827 (optical instruments); lasers and optical networking components"),
    "INVH": ("REITs", "SIC 6510 (real estate operators); a single-family rental REIT"),
    "GRMN": ("Household Durables", "SIC 3812 (navigation systems); consumer GPS, wearables and marine electronics"),
    "PCAR": ("Machinery", "SIC 3711 (motor vehicles); heavy trucks, which GICS places in machinery"),
}

SECTOR_SLUG = {
    "Information Technology": "information-technology", "Communication Services": "communication-services",
    "Consumer Discretionary": "consumer-discretionary", "Consumer Staples": "consumer-staples", "Energy": "energy",
    "Financials": "financials", "Health Care": "health-care", "Industrials": "industrials", "Materials": "materials",
    "Utilities": "utilities", "Real Estate": "real-estate",
}


def slugify(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower().replace("&", "and")).strip("-")


def _matches(sic, spec):
    if not sic:
        return False
    try:
        n = int(sic)
    except ValueError:
        return False
    if "-" in spec:
        lo, hi = spec.split("-")
        return int(lo) <= n <= int(hi)
    return int(spec) == n


def industry_of(ticker, sector, sic):
    """Returns (industry name, rule text) for a company."""
    rules = INDUSTRIES.get(sector)
    if not rules:
        return None, "sector not mapped"
    if ticker in OVERRIDES:
        return OVERRIDES[ticker][0], "override: " + OVERRIDES[ticker][1]
    for name, specs in rules[:-1]:
        for sp in specs:
            if _matches(sic, sp):
                return name, f"SIC {sic} in {sp}"
    return rules[-1][0], f"SIC {sic or 'none'}: no specific rule, sector catch-all"


def mapping_table():
    """The rules as published data."""
    return [{"sector": s, "industry": n, "slug": slugify(n), "sic": specs, "catch_all": i == len(rules) - 1}
            for s, rules in INDUSTRIES.items() for i, (n, specs) in enumerate(rules)]


# Segment names that place part of a company's revenue in another industry. A rule applies only when the parent company
# sits in a different industry; the first matching pattern wins. Patterns run against the XBRL segment member label (lower case);
# whole-name patterns where a word alone is ambiguous ("Pharmaceutical and Specialty Solutions" is distribution).
SEGMENT_RULES = [
    (r"\b(cloud|aws|azure|software|saas|intelligent cloud|productivity and business processes)\b", "Software", r"\b(power|hardware|storage|hybrid cloud|platforms?|sensors)\b"),
    (r"\b(semiconductors?|foundry|data center group|compute and networking)\b", "Semiconductors & Equipment", r"\b(equipment rental)\b"),
    (r"^(pharmaceuticals?|innovative medicine|biopharma(ceuticals?)?)$", "Pharmaceuticals", None),
    (r"^(medical ?tech(nology)?|medtech|medical devices?|medical)$", "Health Care Equipment & Supplies", None),
    (r"^(pharmacy (services|and consumer wellness)|health services|health care benefits|optum( health| rx)?)$", "Health Care Providers & Services", None),
    (r"^(insurance|reinsurance|insurance services|property and casualty( insurance)?)$", "Insurance", None),
    (r"^(payments?|card services|merchant services|payment network|global payments)$", "Financial Services", None),
    (r"^(asset management|wealth management|investment management|wealth and investment management)$", "Capital Markets", None),
    (r"\b(aerospace|defen[cs]e|aviation)\b", "Aerospace & Defense", r"\b(services)\b"),
    (r"^(automotive|automotive products)$", "Automobiles & Components", None),
    (r"^(media|advertising|studios|broadcasting|cable networks|content( and studios)?|media networks|entertainment|news media)$", "Media", None),
    (r"^(parks|theme parks|experiences|parks, experiences and products|resorts?|hotels?|casinos?|restaurants?)$", "Hotels, Restaurants & Leisure", None),
    (r"^(upstream|downstream|refining|exploration and production|oil and gas)$", "Integrated Oil & Refining", None),
    (r"^(chemicals?|specialty chemicals|performance materials)$", "Chemicals", None),
    (r"^(electric utilit(y|ies)|regulated utilit(y|ies)|utilities|utility)$", "Electric Utilities", None),
    (r"^(real estate)$", "Real Estate Management & Services", None),
    (r"^(freight|logistics|railroad|freight forwarding)$", "Transportation", None),
    (r"^(beverages?|snacks?|foods?)$", "Food, Beverage & Tobacco", None),
]


def segment_industry(label):
    s = (label or "").lower()
    for pat, ind, excl in SEGMENT_RULES:
        if re.search(pat, s) and not (excl and re.search(excl, s)):
            return ind, pat
    return None, None


# Industry operating KPIs aggregated across companies only where the reported metric is the same thing. Values come
# from the KPI store (kpi_values: company investor materials, each tied to a page and quote) or are computed from XBRL
# facts. `name` is matched against the company's KPI name in full (total-company figures only; segment, regional or
# historical-basis variants are skipped). `how` is how the cross-company figure is built. A spec with `canonical` uses the
# standardized KPI mapping (etl/kpistd.py, kpistd/map.json: one total-company series per company) when it is published,
# and falls back to the name pattern otherwise.
KPI_SPECS = [
    {"id": "bank_nim", "canonical": "nim", "name": "Net interest margin", "industries": ["Banks"], "unit": "%", "how": "median",
     "match": r"^net interest margin( \((nim|fte|taxable[- ]equivalent|fully taxable[- ]equivalent)( basis)?\))?$|^nim$",
     "definition": "Net interest income (annualized) over average interest-earning assets, as printed by each bank. Taxable-equivalent variants count; segment margins do not."},
    {"id": "bank_deposits", "name": "Total deposits", "industries": ["Banks"], "unit": "USD", "how": "sum", "fact": "Deposits",
     "definition": "Period-end deposits from each bank's balance sheet (XBRL us-gaap:Deposits)."},
    {"id": "bank_efficiency", "canonical": "efficiency_ratio", "name": "Efficiency ratio", "industries": ["Banks"], "unit": "%", "how": "median",
     "match": r"^efficiency ratio( \((gaap|reported)\))?$",
     "definition": "Noninterest expense over revenue, as printed by each bank (reported basis only)."},
    {"id": "bank_cet1", "canonical": "cet1_ratio", "name": "CET1 ratio", "industries": ["Banks", "Capital Markets", "Financial Services"], "unit": "%", "how": "median",
     "match": r"^(cet1|common equity tier 1)( capital)? ratio( \((standardized|standardized approach)\))?$",
     "definition": "Common equity tier 1 capital over risk-weighted assets, standardized approach where a bank prints both."},
    {"id": "ins_combined", "canonical": "combined_ratio", "name": "Combined ratio", "industries": ["Insurance"], "unit": "%", "how": "median",
     "match": r"^(gaap |consolidated |total |p&c |property and casualty )?combined ratio( \((gaap|reported)\))?$",
     "definition": "Losses and expenses over net premiums earned, total company. Underlying or ex-catastrophe variants are excluded."},
    {"id": "mlr", "canonical": "medical_care_ratio", "name": "Medical cost ratio", "industries": ["Health Care Providers & Services"], "unit": "%", "how": "median",
     "match": r"^(medical (care|benefits?|loss|cost) ratio|benefit expense ratio)( \((mcr|mbr|mlr)\))?$",
     "definition": "Medical costs over premium revenue (MCR, MBR or MLR as each insurer names it), total company."},
    {"id": "reit_occupancy", "canonical": "occupancy", "name": "Occupancy", "industries": ["REITs"], "unit": "%", "how": "median",
     "match": r"^(period[- ]end |ending |average |total |portfolio |consolidated |same[- ]property )?(portfolio )?occupancy( rate| percentage)?( - (owned and managed|consolidated|total portfolio))?( \(press release\))?$",
     "definition": "Share of the portfolio occupied or leased at period end (or on average, as each REIT prints it), total portfolio only."},
    {"id": "retail_comps", "canonical": "same_store_sales_growth", "name": "Comparable sales growth", "industries": ["Consumer Discretionary Retail", "Consumer Staples Retail", "Hotels, Restaurants & Leisure"], "unit": "%", "how": "median",
     "match": r"^(total |total company |consolidated )?(comparable|comp|same[- ]store)( store)? sales( growth| change| % change| increase)?(,? (total|total company|consolidated))?( \((total|total company|consolidated)\))?$",
     "definition": "Year-over-year change in sales at stores (or restaurants) open at least a year, total company as printed."},
    {"id": "air_load", "canonical": "load_factor", "name": "Passenger load factor", "industries": ["Transportation"], "unit": "%", "how": "median",
     "match": r"^(passenger )?load factor$", "definition": "Revenue passenger miles over available seat miles."},
    {"id": "air_capacity", "canonical": "asm", "name": "Capacity (ASMs)", "industries": ["Transportation"], "unit": "ASM", "how": "sum",
     "match": r"^(available seat miles|capacity)( \(asms?\))?$|^asms?$", "definition": "Available seat miles flown in the quarter."},
    {"id": "semi_inv_days", "name": "Inventory days", "industries": ["Semiconductors & Equipment"], "unit": "days", "how": "median", "derived": "inventory_days",
     "definition": "Period-end inventory over trailing four quarters of cost of revenue, times 365. Computed from each company's XBRL facts."},
    {"id": "book_to_bill", "canonical": "book_to_bill", "name": "Book-to-bill", "industries": ["Semiconductors & Equipment", "Aerospace & Defense", "Electrical Equipment", "Machinery", "Electronic Equipment & Components"], "unit": "x", "how": "median",
     "match": r"^book[- ]to[- ]bill( ratio)?$", "definition": "Orders over revenue for the quarter, as printed by each company (total company)."},
    {"id": "backlog", "canonical": "backlog", "name": "Backlog", "industries": ["Aerospace & Defense", "Electrical Equipment", "Machinery", "Construction & Engineering"], "unit": "USD", "how": "sum",
     "match": r"^(total )?backlog( \(total( at period end)?\)| total)?$|^backlog: total$", "definition": "Total backlog (remaining contracted orders) at period end, as printed."},
    {"id": "bank_rotce", "canonical": "rotce", "name": "Return on tangible common equity", "industries": ["Banks", "Capital Markets", "Financial Services"], "unit": "%", "how": "median",
     "definition": "Net income to common over average tangible common equity, annualized, as printed by each company (standardized KPI mapping)."},
    {"id": "reit_ffo_ps", "canonical": "ffo_per_share", "name": "FFO per share", "industries": ["REITs"], "unit": "USD_per_share", "how": "median",
     "definition": "NAREIT-defined funds from operations per diluted share, as printed (standardized KPI mapping)."},
    {"id": "energy_production", "canonical": "production_boe", "name": "Production (BOE/d)", "industries": ["Oil & Gas Exploration & Production", "Integrated Oil & Refining"], "unit": "boe/d", "how": "sum",
     "definition": "Total oil-equivalent production per day, converted to barrels of oil equivalent per day (standardized KPI mapping)."},
    {"id": "sw_arr", "canonical": "arr", "name": "Annual recurring revenue", "industries": ["Software"], "unit": "USD", "how": "sum",
     "definition": "Annualized recurring subscription revenue at period end, as each company defines it (standardized KPI mapping)."},
]
