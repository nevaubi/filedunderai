"""Insider owner identity and categories; daily window refresh for every company."""
import datetime as dt
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from etl import insiders, jobs  # noqa: E402

TODAY = dt.date(2026, 11, 7)


def tx(**k):
    base = dict(ticker="T", accession="a", seq=0, filed="2026-10-01", owner="Smith Jane", owner_cik=None, role="Director",
                tx_date="2026-10-01", code="P", security="Common", shares=100.0, price=10.0, owned_after=1000.0,
                acq_disp="A", ownership="D", derivative=False, plan_10b5=False)
    base.update(k)
    return base


class Identity(unittest.TestCase):
    def test_co_filer_suffix_variants_are_one_buyer(self):
        rows = [tx(accession="a", owner="Haggart Dylan G. (+1)", owned_after=1100.0),
                tx(accession="b", owner="Haggart Dylan G. (+2)", owned_after=1200.0)]
        w = insiders.summarize(rows, today=TODAY)["w90"]
        self.assertEqual((w["buys"], w["buyers"], w["ind_buyers"]), (2, 1, 1))

    def test_owner_cik_identifies_the_owner(self):
        rows = [tx(accession="a", owner="Smith Jane", owner_cik="0000123", owned_after=1100.0),
                tx(accession="b", owner="SMITH JANE M", owner_cik="123", owned_after=1200.0)]
        self.assertEqual(insiders.summarize(rows, today=TODAY)["w90"]["buyers"], 1)

    def test_funds_with_board_seats_are_not_individual_insiders(self):
        rows = [tx(accession="a", owner="BAKER BROS. ADVISORS LP (+5)", role="Director, 10% owner", shares=1e6, price=61.0),
                tx(accession="b", owner="Cohen Ryan", role="President, CEO and Chairman, Director", shares=1e5, price=20.0, owned_after=5.0)]
        b = insiders.summarize(rows, today=TODAY)
        w = b["w90"]
        self.assertEqual(w["ind_buyers"], 1)
        self.assertEqual(w["ind_buy_value"], 2_000_000)
        self.assertEqual(w["entity_buy_value"], 61_000_000)
        self.assertEqual({r["owner"]: r["category"] for r in b["rows"]}["BAKER BROS. ADVISORS LP (+5)"], "entity")

    def test_categories(self):
        c = insiders.owner_category
        self.assertEqual(c("LOR INC", "10% owner"), "entity")
        self.assertEqual(c("Bertarelli Ernesto (+1)", "10% owner"), "ten_pct")
        self.assertEqual(c("POHLAD ROBERT C", "Director"), "director")
        self.assertEqual(c("Hazelbaker Jill", "See Remarks"), "officer")


class FakeSB:
    def __init__(self, rows, index):
        self.rows, self.files = rows, {"meta/insiders.json": index}

    def select(self, table, query):
        return list(self.rows)

    def get_json(self, path):
        return self.files.get(path)

    def put_json(self, path, obj, cache=None):
        self.files[path] = obj


class Refresh(unittest.TestCase):
    def test_quiet_company_windows_roll_forward(self):
        old_sale = tx(ticker="KO", code="S", tx_date="2025-10-20", filed="2025-10-21", shares=1e7, price=60.0)
        recent = tx(ticker="KO", code="S", accession="r", tx_date="2026-10-20", filed="2026-10-21", shares=1000.0, price=70.0)
        stale = {"companies": {"KO": {"w90": {"sells": 2}, "w365": {"sells": 2, "sell_value": 600070000}}}}
        sb = FakeSB([old_sale, recent], stale)
        orig = jobs._cache
        jobs._cache = lambda sb_, path, default, strict=False: sb_.get_json(path) or default
        try:
            n = jobs.refresh_insider_bundles(sb, (), today=TODAY)
        finally:
            jobs._cache = orig
        self.assertEqual(n, 1)
        self.assertEqual(sb.files["insiders/KO.json"]["w365"]["sell_value"], 70000)  # the 2025-10-20 sale left the 365-day window
        self.assertEqual(sb.files["meta/insiders.json"]["windows_as_of"], "2026-11-07")


if __name__ == "__main__":
    unittest.main()
