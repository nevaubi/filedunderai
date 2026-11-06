"""Unit tests for the Insiders & influence pipeline (no network)."""
import io
import os
import sys
import unittest
import zipfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "x")

from etl import gov_common, gov_congress, gov_hearings, gov_lobby, gov_insights, flows_holders, flows_market, gov_regs  # noqa: E402

PTR_PAGE = """Filing ID #20034201
ID Owner Asset Transaction Date Notification Amount Cap.
Type Date Gains >
Amazon.com, Inc. - Common Stock S (partial) 03/16/2026 03/16/2026 $1,001 - $15,000
(AMZN) [ST]
F S: New
S O: Putnam Investments
SP NVIDIA Corporation - Common Stock P 10/23/2026 11/01/2026 $15,001 -
$50,000
(NVDA) [ST]
F S: New
JT Cavall [OI] P 04/14/2026 04/15/2026 $1,001 - $15,000
F S: New
"""

TRANSCRIPT = """
    Chairman Wicker. The committee will come to order. We are here
to discuss shipbuilding.

    Mr. Smith. Thank you, Mr. Chairman. Lockheed Martin delivered
the first aircraft late, and the Navy paid for the delay. We need
answers.

    Senator Warren. I agree. Target limited resources wisely.

\\12\\ Lockheed Martin annual report, page 4.
"""


class PTRTests(unittest.TestCase):
    def test_parse_lines(self):
        tx = gov_congress.parse_ptr_text([PTR_PAGE])
        self.assertEqual(len(tx), 3)
        a, n, c = tx
        self.assertEqual((a["ticker_reported"], a["type"], a["tx_date"], a["amount"], a["amin"], a["amax"]),
                         ("AMZN", "Partial sale", "2026-03-16", "$1,001 - $15,000", 1001, 15000))
        self.assertEqual(n["owner"], "SP")
        self.assertEqual(n["amount"], "$15,001 - $50,000")
        self.assertEqual(n["ticker_reported"], "NVDA")
        self.assertIn("(NVDA) [ST]", n["cited"])
        self.assertIsNone(c["ticker_reported"])
        self.assertEqual(c["asset_type"], "OI")

    def test_fix_wrap_and_names(self):
        self.assertEqual(gov_congress.fix_wrap("S&P Global Inc. (SPGI) [ST] $50,000", "$15,001 -"), ("S&P Global Inc. (SPGI) [ST]", "$15,001 - $50,000"))
        self.assertTrue(gov_congress.name_ok("NVDA", "NVIDIA Corporation - Common Stock (NVDA) [ST]"))
        self.assertTrue(gov_congress.name_ok("MTB", "M&T Bank Corporation Common Stock (MTB) [ST]", ["M&T BANK CORP"]))
        self.assertFalse(gov_congress.name_ok("F", "Fidelity Contrafund (F) [MF]"))
        self.assertEqual(gov_congress.map_ticker("BRK.B", gov_common.symbol_map()), "BRK-B")

    def test_member_match(self):
        members = {"A": {"bioguide": "A", "chamber": "House", "state": "FL", "district": 27, "last": "Salazar", "first": "Maria"},
                   "B": {"bioguide": "B", "chamber": "House", "state": "FL", "district": 2, "last": "Salazar", "first": "Other"}}
        self.assertEqual(gov_congress.match_member(members, "Salazar", "Maria Elvira", "FL27")["bioguide"], "A")
        self.assertIsNone(gov_congress.match_member(members, "Nobody", "X", "FL27"))

    def test_build_trades_flags_committee(self):
        house = {"docs": {"1": {"status": "parsed", "last": "Doe", "first": "Jane", "state_dst": "VA01", "filed": "2026-02-01", "url": "u",
                                "transactions": [{"owner": "Self", "asset": "Lockheed Martin Corporation (LMT) [ST]", "ticker_reported": "LMT", "asset_type": "ST",
                                                  "type": "Purchase", "type_code": "P", "tx_date": "2026-01-10", "amount": "$1,001 - $15,000", "amin": 1001, "amax": 15000, "page": 1, "cited": "x"}]}}}
        md = {"members": {"D1": {"bioguide": "D1", "name": "Jane Doe", "chamber": "House", "state": "VA", "district": 1, "last": "Doe", "first": "Jane",
                                 "party": "Republican", "committees": [{"id": "HSAS", "name": "House Committee on Armed Services"}]}},
              "committee_names": {"HSAS": "House Committee on Armed Services"}}
        per, unmatched, counts = gov_congress.build_trades(house, {}, md)
        r = per["LMT"][0]
        self.assertTrue(r["committee_flag"])
        self.assertEqual(r["lag_days"], 22)
        self.assertEqual(counts["matched"], 1)


class AliasTests(unittest.TestCase):
    def test_ambiguous_names_need_legal_form(self):
        al = gov_common.company_aliases("TGT", ["TARGET CORP"])
        self.assertFalse(gov_common.find_mentions("better target limited resources and a target group", al))
        self.assertTrue(gov_common.find_mentions("Target Corporation said", al))

    def test_distinctive_name_and_longest_match(self):
        al = gov_common.company_aliases("LMT", ["LOCKHEED MARTIN CORP"])
        hits = gov_common.find_mentions("the Lockheed Martin F-35", al)
        self.assertEqual(hits[0][2], "Lockheed Martin")

    def test_verbatim(self):
        self.assertTrue(gov_common.verbatim("the “best”  plan", 'We have the "best" plan today'))
        self.assertFalse(gov_common.verbatim("worst plan", "best plan"))

    def test_write_guard(self):
        with self.assertRaises(ValueError):
            gov_common.put_public(None, "companies/AAPL.json", {})
        with self.assertRaises(ValueError):
            gov_common.put_raw(None, "sec/x.json", b"x")


class HearingTests(unittest.TestCase):
    def test_quotes_and_speakers(self):
        al = gov_common.company_aliases("LMT", ["LOCKHEED MARTIN CORP"])
        quotes, n = gov_hearings.extract_quotes(TRANSCRIPT, al, [{"last": "Smith", "bioguide": "S1", "party": "R"}])
        self.assertEqual(len(quotes), 1)  # the footnote mention is skipped
        q = quotes[0]
        self.assertEqual(q["speaker"], "Mr. Smith")
        self.assertEqual(q["speaker_bioguide"], "S1")
        self.assertTrue(q["text"].startswith("Lockheed Martin delivered the first aircraft late"))
        self.assertTrue(gov_common.verbatim(q["text"], TRANSCRIPT))

    def test_scan_prefilter(self):
        al = {"LMT": gov_common.company_aliases("LMT", ["LOCKHEED MARTIN CORP"]), "TGT": gov_common.company_aliases("TGT", ["TARGET CORP"])}
        out = gov_hearings.scan({"CHRG-1": TRANSCRIPT}, {"CHRG-1": {"title": "T", "date": "2026-01-01", "committees": []}}, al)
        self.assertIn("LMT", out)
        self.assertNotIn("TGT", out)

    def test_mods(self):
        x = """<mods xmlns="http://www.loc.gov/mods/v3"><titleInfo><title>OVERSIGHT</title></titleInfo><extension><heldDate>2025-02-06</heldDate><chamber>HOUSE</chamber>
        <congress>119</congress><congCommittee authorityId="hsgo00" chamber="H"><name type="authority-standard">Committee on Oversight</name></congCommittee>
        <congMember bioGuideId="A1" party="D" chamber="H"><name type="authority-fnf">Ann Lee</name><name type="authority-lnf">Lee, Ann</name></congMember>
        <witness>Mr. X</witness></extension></mods>"""
        m = gov_hearings.parse_mods(x)
        self.assertEqual((m["title"], m["date"], m["committees"][0]["id"], m["members"][0]["last"]), ("OVERSIGHT", "2025-02-06", "HSGO", "Lee"))


class LobbyTests(unittest.TestCase):
    def test_bills(self):
        b = [x[0] for x in gov_lobby.bills_in("S (not yet introduced)/HR 9495; S 4784/HR 8800; H.R. 1; S.J.Res. 12; 5 percent")]
        self.assertEqual(b, ["H.R. 9495", "S. 4784", "H.R. 8800", "H.R. 1", "S.J.Res. 12"])

    def test_summarize(self):
        f = {"filing_uuid": "u1", "filing_year": 2026, "filing_period": "first_quarter", "expenses": 1000000, "income": None, "dt_posted": "2026-04-20",
             "registrant": {"id": 1, "name": "ACME CORP"}, "client": {"name": "ACME CORP"}, "filing_document_url": "https://lda.gov/u1",
             "lobbying_activities": [{"general_issue_code": "DEF", "general_issue_code_display": "Defense", "description": "H.R. 8800 NDAA",
                                      "government_entities": [{"name": "SENATE"}], "lobbyists": []}]}
        s = gov_lobby.summarize({"u1": f}, ["ACME"])
        self.assertEqual(s["quarters"][0]["amount"], 1000000.0)
        self.assertEqual(s["bills"][0]["bill"], "H.R. 8800")
        self.assertEqual(s["bills"][0]["congress"], 119)
        laws = [{"id": "PLAW-119publ60", "label": "Public Law 119-60", "bills": ["H.R. 8800"], "congress": 119, "date": "2026-12-01", "title": "NDAA", "url": "u"}]
        linked = gov_regs.link_laws(laws, s, None)
        self.assertEqual(linked[0]["link"], "lobbied")


XML13G = b"""<?xml version="1.0"?><edgarSubmission xmlns="http://www.sec.gov/edgar/schedule13g"><formData><coverPageHeader>
<eventDateRequiresFilingThisStatement>03/31/2026</eventDateRequiresFilingThisStatement><issuerInfo><issuerCik>0000320193</issuerCik>
<issuerCusips><issuerCusipNumber>037833100</issuerCusipNumber></issuerCusips></issuerInfo></coverPageHeader>
<coverPageHeaderReportingPersonDetails><reportingPersonName>Vanguard</reportingPersonName>
<reportingPersonBeneficiallyOwnedAggregateNumberOfShares>1099168953</reportingPersonBeneficiallyOwnedAggregateNumberOfShares>
<classPercent>7.48</classPercent><typeOfReportingPerson>IA</typeOfReportingPerson></coverPageHeaderReportingPersonDetails></formData></edgarSubmission>"""


def _zip13f():
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("SUBMISSION.tsv", "ACCESSION_NUMBER\tFILING_DATE\tSUBMISSIONTYPE\tCIK\tPERIODOFREPORT\n"
                   "A1\t10-AUG-2026\t13F-HR\t0000001\t30-JUN-2026\nA0\t10-MAY-2026\t13F-HR\t0000001\t31-MAR-2026\n"
                   "A2\t12-AUG-2026\t13F-HR/A\t0000001\t30-JUN-2026\n")
        z.writestr("COVERPAGE.tsv", "ACCESSION_NUMBER\tAMENDMENTTYPE\tFILINGMANAGER_NAME\tREPORTTYPE\n"
                   "A1\t\tBig Fund\t13F HOLDINGS REPORT\nA0\t\tBig Fund\t13F HOLDINGS REPORT\nA2\tNEW HOLDINGS\tBig Fund\t13F HOLDINGS REPORT\n")
        z.writestr("INFOTABLE.tsv", "ACCESSION_NUMBER\tNAMEOFISSUER\tCUSIP\tVALUE\tSSHPRNAMT\tSSHPRNAMTTYPE\tPUTCALL\n"
                   "A1\tAPPLE INC\t037833100\t1000\t10\tSH\t\nA1\tAPPLE INC\t037833100\t500\t5\tSH\tPut\nA2\tAPPLE INC\t037833100\t200\t2\tSH\t\n"
                   "A0\tAPPLE INC\t037833100\t800\t8\tSH\t\n")
    return b.getvalue()


class HolderTests(unittest.TestCase):
    def test_13g_xml(self):
        p = flows_holders.parse_13dg_xml(XML13G)
        self.assertEqual(p["issuer_cik"], 320193)
        self.assertEqual(p["cusips"], ["037833100"])
        self.assertEqual(p["persons"][0]["percent"], 7.48)

    def test_13f_build(self):
        out, rejected = flows_holders.build_13f([_zip13f()], {"AAPL": ["037833100"]}, ["2026-03-31", "2026-06-30"])
        h = out["AAPL"]["holders"][0]
        self.assertEqual((h["shares"], h["prev_shares"], h["change"]), (12.0, 8.0, 4.0))  # put row excluded, NEW HOLDINGS added
        self.assertEqual(rejected, [])


class FlowTests(unittest.TestCase):
    def test_offexchange_share(self):
        import datetime as dt
        mon = dt.datetime(2026, 10, 12, 16, tzinfo=dt.timezone.utc)
        bars = [[int((mon + dt.timedelta(days=i)).timestamp()), 1, 1, 1, 1, 1000] for i in range(5)]
        raw = {"weeks": {"2026-10-12": {"week": "2026-10-12", "ats_shares": 500, "otc_shares": 1000}}}
        f = flows_market.company_flows("AAPL", raw, {"retrieved_at": "t"}, bars)
        w = f["ats_weekly"][0]
        self.assertAlmostEqual(w["offex_share"], 0.3)
        self.assertAlmostEqual(w["ats_share"], 0.1)


class InsightTests(unittest.TestCase):
    def test_verify_drops_unsourced(self):
        facts = [("a", "Federal lobbying spend 2026 Q1: $4.1M (in-house report)."), ("b", 'Senator X said "we need answers on the delay"')]
        ok, dropped = gov_insights.verify([
            {"text": "Lobbying spend was $4.1M in 2026 Q1.", "refs": ["a"]},
            {"text": "Lobbying spend was $9.9M.", "refs": ["a"]},
            {"text": "Senator X said \"we need answers on the delay\".", "refs": ["b"]},
            {"text": "Senator X said \"we invented this quote\".", "refs": ["b"]},
            {"text": "Unreferenced claim.", "refs": ["zzz"]}], facts)
        self.assertEqual(len(ok), 2)
        self.assertEqual(len(dropped), 3)


if __name__ == "__main__":
    unittest.main()
