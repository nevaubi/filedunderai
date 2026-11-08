"""Company registry matcher: exact normalized names, subsidiary matches, ambiguity goes to review."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from etl.entities import Matcher, generic, search_phrases  # noqa: E402

REG = {"companies": {
    "RTX": {"name": "RTX Corp", "former": ["Raytheon Technologies Corp"], "subsidiaries": ["Raytheon Company", "Pratt & Whitney", "Holdings LLC"],
            "lei": {"name": "RTX Corporation"}, "gleif_children": [{"name": "Collins Aerospace"}]},
    "GD": {"name": "GENERAL DYNAMICS CORP", "former": [], "subsidiaries": ["Electric Boat Corporation", "Holdings LLC", "Shared Services Inc"]},
    "XYZ": {"name": "XYZ Inc", "former": [], "subsidiaries": ["Shared Services Inc"]},
}}


class MatcherTests(unittest.TestCase):
    def setUp(self):
        self.m = Matcher(REG)

    def test_legal_former_and_lei_names_are_strong(self):
        self.assertEqual(self.m.resolve("RAYTHEON TECHNOLOGIES CORPORATION")["method"], "former")
        self.assertEqual(self.m.resolve("RTX CORPORATION")["confidence"], "high")
        self.assertEqual(self.m.resolve("General Dynamics Corporation")["ticker"], "GD")

    def test_subsidiaries_and_gleif_children_are_medium(self):
        r = self.m.resolve("ELECTRIC BOAT CORP")
        self.assertEqual((r["ticker"], r["method"], r["confidence"]), ("GD", "subsidiary", "medium"))
        self.assertEqual(self.m.resolve("Collins Aerospace")["method"], "gleif_child")
        self.assertEqual(self.m.resolve("PRATT AND WHITNEY")["ticker"], "RTX")

    def test_shared_names_go_to_review_and_generic_names_are_ignored(self):
        self.assertIsNone(self.m.resolve("Shared Services, Inc.", context="usaspending"))
        self.assertEqual(self.m.review["SHARED SERVICES INC"]["candidates"], ["GD", "XYZ"])
        self.assertIsNone(self.m.resolve("Holdings LLC"))
        self.assertTrue(generic("Holdings LLC"))
        self.assertTrue(generic("International Holdings Inc"))
        self.assertFalse(generic("Electric Boat Corporation"))

    def test_names_for_keeps_only_unique_non_generic_subsidiaries(self):
        names = self.m.names_for("GD", REG)
        self.assertIn("Electric Boat Corporation", names)
        self.assertNotIn("Shared Services Inc", names)
        self.assertNotIn("Holdings LLC", names)
        self.assertEqual(search_phrases(self.m.names_for("RTX", REG))[:3], ["RTX", "RAYTHEON TECHNOLOGIES", "RAYTHEON"])


if __name__ == "__main__":
    unittest.main()
