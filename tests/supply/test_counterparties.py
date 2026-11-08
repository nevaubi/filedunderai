from etl.counterparties import Normalizer, classify, counterparty_index, normalize_links
from etl.entities import Matcher


def reg():
    return {"companies": {
        "C": {"name": "CITIGROUP INC", "subsidiaries": ["Citibank, N.A."]},
        "JPM": {"name": "JPMORGAN CHASE & CO", "subsidiaries": ["JPMorgan Chase Bank, National Association"]},
        "XYZ": {"name": "XYZ CORP", "subsidiaries": ["Acme Widgets LLC"]},
    }}


def test_government_variants_share_one_key():
    keys = {classify(n)[0] for n in ["U.S. Government", "United States government", "the U.S. federal government"]}
    assert keys == {"us government"}
    assert classify("U.S. Department of War")[0] == "us dod"
    assert classify("Department of Defense")[0] == "us dod"
    assert classify("U.S. Army")[:2] == ("us army", "U.S. Army")


def test_gse_and_auditor_classes():
    assert classify("Federal National Mortgage Association (Fannie Mae)")[0] == "fannie mae"
    assert classify("Federal Home Loan Bank of Atlanta")[0] == "fhlb"
    assert classify("FHLB")[0] == "fhlb"
    assert classify("Ernst & Young LLP")[2] == "auditor"
    assert classify("KPMG LLP")[2] == "auditor"
    assert classify("A.M. Best")[2] == "rating_agency"
    assert classify("Acme Widgets") is None


def test_brands_and_registry_resolve_covered_companies():
    n = Normalizer(Matcher(reg()), {"GOOGL": "Alphabet", "C": "Citigroup"})
    assert n.resolve("Google")["cpt"] == "GOOGL"
    assert n.resolve("Citibank, N.A.")["cpt"] == "C"
    r = n.resolve("Acme Widgets LLC")
    assert r["cpt"] == "XYZ" and r["cpm"].startswith("registry:")
    assert "cpt" not in n.resolve("Some Private Supplier Ltd")


def test_self_links_dropped_and_index_groups_aliases():
    n = Normalizer(Matcher(reg()), {})
    links = [
        {"t": "C", "cp": "Citibank, N.A.", "rel": "partner"},
        {"t": "AAA", "cp": "U.S. Government", "rel": "customer"},
        {"t": "BBB", "cp": "Department of Defense", "rel": "customer"},
        {"t": "CCC", "cp": "United States government", "rel": "customer"},
    ]
    out, dropped = normalize_links(links, n)
    assert dropped == 1 and len(out) == 3
    idx = counterparty_index(out)
    top = idx["rows"][0]
    assert top["key"] == "us government" and top["n_filers"] == 2 and top["class"] == "government"
    assert "United States government" in top["aliases"]
