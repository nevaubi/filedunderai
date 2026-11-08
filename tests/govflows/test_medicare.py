from etl.medicare import build_mapper, label_matches, top_brands


def test_label_matching():
    assert label_matches("Pfizer US Pharm", "PFIZER INC")
    assert label_matches("Neurocrine Bios", "NEUROCRINE BIOSCIENCES INC")
    assert label_matches("Incyte Corporat", "INCYTE CORP")
    assert label_matches("Eli Lilly & Co.", "ELI LILLY & Co")
    assert not label_matches("Edwards Pharm.", "Edwards Lifesciences Corp")
    assert not label_matches("West-Ward, Inc.", "WEST PHARMACEUTICAL SERVICES INC")
    assert not label_matches("Bausch Health U", "Bausch + Lomb Corp")
    assert label_matches("Biomarin Pharma", "BIOMARIN PHARMACEUTICAL INC")
    assert label_matches("Xeris Pharmaceu", "Xeris Biopharma Holdings, Inc.")
    assert not label_matches("Acadia Pharmaceu", "ACADIA HEALTHCARE COMPANY, INC.")


def test_mapper_owners_and_ambiguity():
    m = build_mapper({"BMY": ["BRISTOL MYERS SQUIBB CO"], "JNJ": ["JOHNSON & JOHNSON"], "PFE": ["PFIZER INC"], "A1": ["ACME BIO INC"], "A2": ["ACME BIO CORP"]})
    assert m("BMS Primarycare") == "BMY" and m("Janssen Pharm.") == "JNJ" and m("Pfizer Labs") == "PFE"
    assert m("Acme Bio") is None          # two companies fit
    assert m("Genentech, Inc.") is None   # external parent


def test_top_brands():
    rows = top_brands({"A": {"2024": 10, "2023": 5}, "B": {"2024": 30}}, "2024", "2023")
    assert [r["brand"] for r in rows] == ["B", "A"] and rows[1]["yoy"] == 1.0
