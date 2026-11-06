"""Reviewer decisions for the pilot seed (2026-11-05). Every rule-based candidate was read in context; the default is
accept. Keys are "TICKER|counterparty|relation". drop = not a business relationship (biographies, peer groups, other
companies' deals, subsidiaries, litigation); rel = corrected relation; set = field corrections; forms = keep only for
these forms (the quote picked for other forms did not describe the relationship); cp = canonical counterparty."""

DROP = set("""
MSFT|News|customer
MSFT|Apple|customer
GOOGL|Waymo|customer
AVGO|TSMC|competitor
AMD|Intel|customer
AMD|NVIDIA|customer
AMD|Intel|partner
AMD|NVIDIA|partner
INTC|Flex|customer
INTC|Altera|supplier
INTC|Mobileye|supplier
INTC|Mobileye|customer
INTC|Mobileye|partner
INTC|TSMC|partner
INTC|Boeing|customer
INTC|IBM|customer
INTC|Microsoft|customer
INTC|NVIDIA|customer
INTC|Northrop Grumman|customer
QCOM|Apple|competitor
QCOM|Nokia|partner
LRCX|Huawei|partner
KLAC|Huawei|partner
KLAC|Block|partner
HPQ|Nokia|partner
F|Amazon|customer
F|Apple|partner
BA|Airbus|partner
BA|Ford|partner
LMT|General Dynamics|supplier
LMT|Block|customer
RTX|Block|customer
GE|Boeing|supplier
GE|GE HealthCare|partner
GE|GE HealthCare|supplier
GE|GE Vernova|supplier
GE|GE Vernova|customer
TGT|Amazon|customer
TGT|Costco|customer
TGT|CVS Health|customer
TGT|Home Depot|partner
TGT|Walmart|partner
TGT|Best Buy|customer
TGT|Dollar General|customer
TGT|Dollar Tree|customer
TGT|Kroger|partner
TGT|Ross Stores|partner
TGT|TJX Companies|partner
TGT|BJ's Wholesale Club Holdings, Inc.|customer
TGT|BJ's Wholesale Club Holdings, Inc.|partner
TGT|Macy's, Inc.|partner
TGT|Albertsons Companies, Inc.|customer
TGT|Walgreens|partner
HD|Walmart|customer
KO|3M|partner
MRK|Organon & Co.|customer
JNJ|Merck|competitor
UNH|Optum Rx|partner
UNH|Optum Rx|customer
CVX|Block|partner
NKE|Chevron|partner
SBUX|Baxter International|customer
SBUX|PPG Industries|customer
DIS|Hulu|customer
DIS|Hulu|partner
DIS|Fox|partner
MCK|Walgreens|partner
COR|Walgreens|partner
CAH|CVS Health|supplier
CAH|Optum Rx|supplier
CMCSA|Morgan Stanley|partner
CMCSA|Flex|customer
CMCSA|Wells Fargo|partner
CMCSA|Bank of America|partner
CMCSA|JPMorgan Chase|partner
CHTR|News|partner
CHTR|News|customer
MRVL|Cisco|partner
MRVL|Micron Technology|supplier
MRVL|Western Digital|partner
FDX|Alphabet|customer
FDX|Microsoft|customer
FDX|Flex|customer
CI|Express Scripts|supplier
CI|Express Scripts|partner
CI|Express Scripts|customer
CI|Capital One|customer
CI|Chubb|customer
""".strip().splitlines())

REL = {
    "MSFT|Apple|supplier": "competitor", "MSFT|Cisco|supplier": "competitor",
    "AMD|Broadcom|supplier": "competitor", "AMD|Analog Devices|supplier": "competitor", "AMD|Marvell Technology|supplier": "competitor",
    "AMD|Microsoft|supplier": "partner", "AMD|Meta Platforms|customer": "partner", "AMD|OpenAI|customer": "partner",
    "INTC|Apple|supplier": "competitor", "INTC|AMD|supplier": "competitor", "INTC|Qualcomm|supplier": "competitor", "INTC|UMC|supplier": "competitor",
    "INTC|Synopsys|customer": "partner", "INTC|Synopsys|supplier": "partner",
    "QCOM|Huawei|partner": "customer", "QCOM|Broadcom|partner": "competitor",
    "ORCL|Microsoft|customer": "partner", "DELL|Broadcom|customer": "partner",
    "F|BlueOval SK|supplier": "partner", "RTX|Boeing|supplier": "customer",
    "GE|Boeing|partner": "customer", "GE|Airbus|partner": "customer",
    "KO|Monster Beverage|customer": "partner", "PEP|Walmart|competitor": "customer",
    "PFE|BioNTech|supplier": "partner", "JNJ|Kenvue|supplier": "partner",
    "DIS|Apple|partner": "customer", "DIS|Amazon|partner": "customer", "DIS|Comcast|partner": "customer",
    "COR|Cigna|supplier": "customer", "COR|Express Scripts|supplier": "customer",
    "CMCSA|Verizon|customer": "supplier", "CMCSA|T-Mobile US|customer": "supplier",
    "CHTR|Verizon|partner": "supplier", "CHTR|T-Mobile US|customer": "supplier", "CHTR|Apple|customer": "partner", "CHTR|Samsung|customer": "partner",
    "CHTR|Comcast|customer": "partner", "CHTR|Verizon|customer": "partner", "CHTR|Alphabet|customer": "partner", "CHTR|Disney|customer": "supplier",
    "MRVL|TSMC|partner": "supplier", "FDX|Dollar General|customer": "partner", "FDX|Walgreens|customer": "partner", "FDX|Salesforce|customer": "partner",
    "CVS|Amazon|customer": "competitor", "CVS|Walmart|customer": "competitor",
}

# Evernorth (formerly Express Scripts) is Cigna's health services business.
CP = {"COR|Express Scripts": ("Cigna", "CI")}
# The combined Walgreens and Boots share is not attributable to Evernorth; drop the share the rule assigned by proximity.
SET = {"COR|Cigna|customer": {"share": None}}
FORMS = {"INTC|Altera|customer": {"10-Q"}}

# quotes that name a company without describing this filer's relationship with it
QDROP = ["Chinese government announced restrictions"]
