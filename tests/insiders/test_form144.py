import datetime as dt
import pathlib

from etl.form144 import doc_url, match_form4, parse, same_person, status_of, summarize

XML = (pathlib.Path(__file__).parent / "fixtures" / "form144_so.xml").read_text()


def test_parse_notice_fields():
    n = parse(XML)
    assert n["issuer_cik"] == "92122"
    assert n["person"] == "Sterling Spainhour"
    assert n["relationship"] == "Officer"
    assert n["shares"] == 3333 and n["value"] == 306002.73
    assert n["sale_date"] == "2026-11-06"
    assert n["brokers"] == ["Merrill Lynch"]
    assert n["plan"] is True and n["plan_adopted"] == ["2025-11-25"]
    assert n["past3m_shares"] == 3333 and n["past3m_proceeds"] == 313368.66
    assert n["how"] == "Performance Stock Vest"
    assert 0 < n["pct_outstanding"] < 0.001


def test_doc_url_drops_xsl_path():
    u = "https://www.sec.gov/Archives/edgar/data/92122/000162828026071674/xsl144X01/primary_doc.xml"
    assert doc_url({"url": u}) == "https://www.sec.gov/Archives/edgar/data/92122/000162828026071674/primary_doc.xml"


def test_name_order_and_match():
    assert same_person("Sterling Spainhour", "Spainhour Sterling")
    assert same_person("Kimberly S. Greene", "Greene Kimberly S,")
    assert not same_person("Sterling Spainhour", "Kim Matthew M.")
    notice = {"filed": "2026-11-06", "person": "Sterling Spainhour", "shares": 3333}
    rows = [
        {"accession": "a1", "code": "S", "tx_date": "2026-11-07", "owner": "Spainhour Sterling", "shares": 3333, "price": 92.0, "plan_10b5": True},
        {"accession": "a2", "code": "S", "tx_date": "2026-12-30", "owner": "Spainhour Sterling", "shares": 10, "price": 92.0},
        {"accession": "a3", "code": "A", "tx_date": "2026-11-07", "owner": "Spainhour Sterling", "shares": 50, "price": 0},
    ]
    m = match_form4(notice, rows)
    assert m["accessions"] == ["a1"] and m["filled_pct"] == 100.0 and m["plan_10b5"]
    assert match_form4({**notice, "person": "Someone Else"}, rows) is None


def test_status_and_summary():
    today = dt.date(2026, 11, 8)
    assert status_of({"filed": "2026-11-06"}, None, today) == "open"
    assert status_of({"filed": "2026-09-01"}, None, today) == "not_reported"
    assert status_of({"filed": "2026-09-01"}, {"shares": 1}, today) == "executed"
    s = summarize([{"filed": "2026-11-06", "value": 100, "person": "A", "plan": True, "status": "open"},
                   {"filed": "2026-07-01", "value": 50, "person": "B", "plan": False, "status": "executed"}], today)
    assert s["d30"]["n"] == 1 and s["d365"]["value"] == 150 and s["open"]["people"] == ["A"] and s["d365"]["plan_share"] == 0.5
