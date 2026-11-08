"""Counterparty normalization for the supply-chain graph.

Filings name the same counterparty many ways ('U.S. Government', 'Department of Defense', 'U.S. Army'; 'Fannie Mae',
'Federal National Mortgage Association (Fannie Mae)'; 'Citibank, N.A.' for Citigroup). This module gives every
link a canonical key, display name and class, and resolves covered companies through brand aliases and the company
registry (etl/entities.py: legal, former and subsidiary names, GLEIF children).

Fields added to each graph link (only when known):
  cpk  canonical key (ticker for covered companies, otherwise a stable lower-case key)
  cpn  canonical display name
  cpc  class: company | government | gse | regulator | payer | auditor | rating_agency | exchange
  cpm  how it was resolved: alias | canon | registry:<method>
  cpt  covered ticker, filled when the registry or an alias resolves it with certainty and the extractor left it blank

Links whose counterparty resolves to the filer itself (a bank naming its own bank subsidiary) are dropped."""
import collections
import re

from .common import now_iso
from .supply_core import entity_key

# (pattern on the lower-cased name, canonical key, display name, class). First match wins, so specific agencies come
# before the catch-all government pattern.
CANON = [
    # government-sponsored enterprises
    (r"\bfannie mae\b|federal national mortgage", "fannie mae", "Fannie Mae", "gse"),
    (r"\bfreddie mac\b|federal home loan mortgage", "freddie mac", "Freddie Mac", "gse"),
    (r"\bginnie mae\b|government national mortgage|\bgnma\b", "ginnie mae", "Ginnie Mae", "gse"),
    (r"federal home loan banks?\b|\bfhlb\b", "fhlb", "Federal Home Loan Banks", "gse"),
    (r"\bfarmer mac\b|federal agricultural mortgage", "farmer mac", "Farmer Mac", "gse"),
    # regulators and central bank
    (r"federal deposit insurance|\bfdic\b", "fdic", "FDIC", "regulator"),
    (r"federal reserve", "federal reserve", "Federal Reserve", "regulator"),
    (r"office of the comptroller|\bocc\b", "occ", "OCC", "regulator"),
    (r"securities and exchange commission|\bsec\b$", "sec", "SEC", "regulator"),
    (r"food and drug administration|\bfda\b", "fda", "FDA", "regulator"),
    (r"federal energy regulatory|\bferc\b", "ferc", "FERC", "regulator"),
    (r"federal communications commission|\bfcc\b", "fcc", "FCC", "regulator"),
    (r"nuclear regulatory commission", "nrc", "Nuclear Regulatory Commission", "regulator"),
    # public payers
    (r"centers for medicare|\bcms\b$", "cms", "Centers for Medicare & Medicaid Services", "payer"),
    (r"^medicare( advantage| part [a-d])?$", "medicare", "Medicare", "payer"),
    (r"^(state )?medicaid( programs?)?$", "medicaid", "Medicaid", "payer"),
    (r"tricare", "tricare", "TRICARE", "payer"),
    # federal agencies named as customers
    (r"department of (defense|war)\b|\bdod\b|^pentagon$", "us dod", "U.S. Department of Defense", "government"),
    (r"^(the )?u ?s army\b|^united states army\b|^army$", "us army", "U.S. Army", "government"),
    (r"^(the )?u ?s navy\b|^united states navy\b|^navy$", "us navy", "U.S. Navy", "government"),
    (r"^(the )?u ?s air force\b|^united states air force\b|^air force$", "us air force", "U.S. Air Force", "government"),
    (r"space force", "us space force", "U.S. Space Force", "government"),
    (r"missile defense agency", "us mda", "Missile Defense Agency", "government"),
    (r"department of energy\b|\bdoe\b$", "us doe", "U.S. Department of Energy", "government"),
    (r"veterans affairs|\bva\b$", "us va", "U.S. Department of Veterans Affairs", "government"),
    (r"health and human services|\bhhs\b$", "us hhs", "U.S. Department of Health and Human Services", "government"),
    (r"homeland security|\bdhs\b$", "us dhs", "U.S. Department of Homeland Security", "government"),
    (r"\bnasa\b|national aeronautics and space", "nasa", "NASA", "government"),
    (r"general services administration|\bgsa\b$", "us gsa", "General Services Administration", "government"),
    (r"federal housing administration|\bfha\b$", "fha", "Federal Housing Administration", "government"),
    (r"postal service|\busps\b", "usps", "U.S. Postal Service", "government"),
    (r"^(the )?(u ?s|united states)( federal)? government|^(the )?federal government$|^(u ?s|united states) government agencies",
     "us government", "U.S. government", "government"),
    # audit firms (they appear in filings as service providers, rarely as supply-chain counterparties)
    (r"^kpmg\b", "kpmg", "KPMG", "auditor"),
    (r"^ernst (and|&) young\b|^ey\b", "ernst & young", "Ernst & Young", "auditor"),
    (r"^pricewaterhousecoopers\b|^pwc\b", "pwc", "PricewaterhouseCoopers", "auditor"),
    (r"^deloitte\b", "deloitte", "Deloitte", "auditor"),
    (r"^grant thornton\b", "grant thornton", "Grant Thornton", "auditor"),
    (r"^bdo\b", "bdo", "BDO", "auditor"),
    (r"^rsm( us)?\b", "rsm", "RSM", "auditor"),
    (r"^crowe\b", "crowe", "Crowe", "auditor"),
    (r"^moss adams\b", "moss adams", "Moss Adams", "auditor"),
    (r"^marcum\b", "marcum", "Marcum", "auditor"),
    # rating agencies
    (r"^a ?m best\b", "am best", "AM Best", "rating_agency"),
    (r"^moody", "moodys", "Moody's", "rating_agency"),
    (r"^s ?(and|&) ?p\b|^standard (and|&) poor", "s&p", "S&P Global Ratings", "rating_agency"),
    (r"^fitch\b", "fitch", "Fitch Ratings", "rating_agency"),
    (r"^kroll bond", "kbra", "Kroll Bond Rating Agency", "rating_agency"),
    # external companies whose subsidiaries are named separately
    (r"^genentech\b", "roche", "Roche", "company"),
    (r"^tiktok\b|^bytedance\b", "bytedance", "ByteDance (TikTok)", "company"),
]
CANON = [(re.compile(rx), k, n, c) for rx, k, n, c in CANON]

# Brands and units of covered companies that registries do not list under these names.
BRANDS = {
    "google": "GOOGL", "google cloud": "GOOGL", "youtube": "GOOGL", "alphabet": "GOOGL",
    "facebook": "META", "instagram": "META", "whatsapp": "META", "meta": "META",
    "amazon web services": "AMZN", "aws": "AMZN", "whole foods": "AMZN",
    "microsoft azure": "MSFT", "azure": "MSFT", "linkedin": "MSFT",
    "bofa securities": "BAC", "merrill lynch": "BAC", "bank of america": "BAC",
    "j p morgan securities": "JPM", "jp morgan securities": "JPM", "jpmorgan chase bank": "JPM",
    "citibank": "C", "citigroup global markets": "C",
    "wells fargo bank": "WFC", "wells fargo securities": "WFC",
    "goldman sachs and co": "GS", "goldman sachs & co": "GS",
    "morgan stanley and co": "MS", "morgan stanley & co": "MS",
    "pnc bank": "PNC", "truist bank": "TFC", "truist securities": "TFC", "u s bank": "USB",
    "keybank": "KEY", "keybanc capital markets": "KEY", "fifth third bank": "FITB", "regions bank": "RF",
    "huntington national bank": "HBAN", "citizens bank": "CFG", "m and t bank": "MTB", "m&t bank": "MTB",
    "capital one": "COF", "american express": "AXP", "charles schwab": "SCHW",
    "sam s club": "WMT", "walmart": "WMT", "optum": "UNH", "unitedhealthcare": "UNH",
    "aetna": "CVS", "caremark": "CVS", "cvs caremark": "CVS", "evernorth": "CI", "express scripts": "CI",
    "janssen": "JNJ", "nbcuniversal": "CMCSA", "pratt and whitney": "RTX", "pratt & whitney": "RTX",
    "collins aerospace": "RTX", "raytheon": "RTX", "ge aerospace": "GE", "ge vernova": "GEV",
}
BANK_TAIL = re.compile(r"\b(national association|n a|na|n\.a\.|inc|llc|usa|corporation|corp|company|co)\b")


def _key(name):
    return re.sub(r"\s+", " ", re.sub(r"[.,'()’]", " ", (name or "").lower())).strip()


def _brand(name):
    k = _key(name)
    if k in BRANDS:
        return BRANDS[k]
    short = re.sub(r"\s+", " ", BANK_TAIL.sub(" ", k)).strip()
    return BRANDS.get(short)


def classify(name):
    """(canonical key, display, class) for agencies, GSEs, regulators, payers, auditors and rating agencies."""
    k = _key(name)
    for rx, ck, cn, cc in CANON:
        if rx.search(k):
            return ck, cn, cc
    return None


class Normalizer:
    def __init__(self, matcher=None, covered=None):
        self.matcher = matcher
        self.covered = covered or {}   # {ticker: display name}
        self.cache = {}

    def resolve(self, name, ticker=None):
        """{'cpk','cpn','cpc','cpm', optional 'cpt'} for one counterparty name."""
        ck = (name or "", ticker or "")
        if ck in self.cache:
            return self.cache[ck]
        out = None
        if ticker:
            out = {"cpk": ticker.upper(), "cpn": self.covered.get(ticker.upper(), name), "cpc": "company", "cpm": "extractor"}
        if out is None:
            c = classify(name)
            if c:
                out = {"cpk": c[0], "cpn": c[1], "cpc": c[2], "cpm": "canon"}
        if out is None:
            b = _brand(name)
            if b:
                out = {"cpk": b, "cpn": self.covered.get(b, name), "cpc": "company", "cpm": "alias", "cpt": b}
        if out is None and self.matcher is not None:
            r = self.matcher.resolve(name, context="supply")
            if r:
                tk = r["ticker"]
                out = {"cpk": tk, "cpn": self.covered.get(tk, name), "cpc": "company", "cpm": f"registry:{r['method']}", "cpt": tk}
        if out is None:
            out = {"cpk": entity_key(name), "cpn": name, "cpc": "company", "cpm": "name"}
        self.cache[ck] = out
        return out

    def apply(self, link):
        """Returns the link with canonical fields, or None when the counterparty is the filer itself."""
        r = self.resolve(link.get("cp"), link.get("cpt"))
        tk = r.get("cpt") or link.get("cpt")
        if tk and tk == link.get("t"):
            return None
        out = dict(link)
        out.update({k: v for k, v in r.items() if k != "cpt"})
        if (link.get("cpm") or "").startswith(("alias", "registry")) and link.get("cpt"):
            out["cpm"] = link["cpm"]   # keep how an earlier pass resolved it
        if not link.get("cpt") and r.get("cpt"):
            out["cpt"] = r["cpt"]
        return out


def normalize_links(links, normalizer):
    out, dropped = [], 0
    for l in links:
        n = normalizer.apply(l)
        if n is None:
            dropped += 1
        else:
            out.append(n)
    return out, dropped


def counterparty_index(links, limit=400):
    """Most-cited counterparties by number of filers, with class, relation counts and the filers naming them."""
    agg = {}
    for l in links:
        k = l.get("cpk") or entity_key(l.get("cp"))
        a = agg.setdefault(k, {"key": k, "name": l.get("cpn") or l.get("cp"), "class": l.get("cpc") or "company",
                               "ticker": l.get("cpt"), "filers": set(), "rel": collections.Counter(), "names": collections.Counter()})
        a["filers"].add(l["t"])
        a["rel"][l["rel"]] += 1
        a["names"][l.get("cp")] += 1
    rows = []
    for a in agg.values():
        rows.append({"key": a["key"], "name": a["name"], "class": a["class"], "ticker": a["ticker"], "n_filers": len(a["filers"]),
                     "filers": sorted(a["filers"])[:60], "rel": dict(a["rel"]),
                     "aliases": [n for n, _ in a["names"].most_common(8) if n and n != a["name"]]})
    rows.sort(key=lambda r: (-r["n_filers"], r["name"] or ""))
    by_class = collections.Counter(r["class"] for r in rows)
    return {"generated": now_iso(), "n": len(rows), "by_class": dict(by_class), "rows": rows[:limit]}
