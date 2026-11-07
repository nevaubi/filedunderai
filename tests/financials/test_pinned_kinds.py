import unittest

from etl import sec as s

CASH = ("Cash & equivalents", ["CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"])


def obs(val, end, accn, form, filed, fp):
    return {"val": val, "end": end, "accn": accn, "form": form, "filed": filed, "fp": fp}


FACTS = {"us-gaap": {
    "CashAndCashEquivalentsAtCarryingValue": {"units": {"USD": [obs(1011, "2024-12-31", "k26", "10-K", "2026-02-13", "FY")]}},
    "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents": {"units": {"USD": [
        obs(1015, "2024-12-31", "k26", "10-K", "2026-02-13", "FY"),
        obs(1015, "2024-12-31", "q26", "10-Q", "2026-10-29", "Q3")]}},
}}


class PinnedByKind(unittest.TestCase):
    def test_annual_and_quarterly_cells_keep_their_own_published_concepts(self):
        pinned = {("A", "Cash & equivalents", None, "2024-12-31"): "CashAndCashEquivalentsAtCarryingValue",
                  ("Q", "Cash & equivalents", None, "2024-12-31"): "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"}
        line = s.build_series(FACTS, "balance", concepts=[CASH], pinned=pinned)[0]
        self.assertEqual(line["annual"][-1]["c"], "CashAndCashEquivalentsAtCarryingValue")
        self.assertEqual(line["annual"][-1]["v"], 1011)
        self.assertEqual(line["quarterly"][-1]["c"], "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents")

    def test_legacy_key_without_kind_still_pins_both(self):
        pinned = {("Cash & equivalents", None, "2024-12-31"): "CashAndCashEquivalentsAtCarryingValue"}
        line = s.build_series(FACTS, "balance", concepts=[CASH], pinned=pinned)[0]
        self.assertEqual(line["annual"][-1]["c"], "CashAndCashEquivalentsAtCarryingValue")
        self.assertEqual(line["quarterly"][-1]["c"], "CashAndCashEquivalentsAtCarryingValue")


if __name__ == "__main__":
    unittest.main()
