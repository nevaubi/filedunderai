import unittest

from etl import redlines


class Highlights(unittest.TestCase):
    def test_rows_point_at_the_filing_that_holds_the_passage(self):
        doc = {"comparisons": [{"kind": "annual", "form": "10-K",
                                "new": {"accn": "N-1", "doc": "new.htm", "filed": "2026-10-30"},
                                "old": {"accn": "O-1", "doc": "old.htm", "filed": "2025-10-30"},
                                "sections": [{"title": "Item 1A", "id": "item:1A", "changes": [
                                    {"kind": "removed", "old": "We rely on a single supplier for wafers and any disruption would hurt us badly.", "new": None, "score": 9, "signals": []},
                                    {"kind": "modified", "ops": [["=", "Revenue from our largest customer was"], ["-", "18%"], ["+", "27%"], ["=", "of total revenue."]], "score": 20, "signals": []},
                                    {"kind": "numbers", "ops": [["-", "1"], ["+", "2"]], "score": 50, "signals": []}]}]}]}
        h = redlines.highlights(doc)
        self.assertEqual([x["change"] for x in h], ["modified", "removed"])
        self.assertEqual((h[0]["accn"], h[0]["doc"]), ("N-1", "new.htm"))
        self.assertEqual(h[0]["search"], "Revenue from our largest customer was 27% of total revenue.")
        self.assertEqual((h[1]["accn"], h[1]["doc"], h[1]["doc_filed"]), ("O-1", "old.htm", "2025-10-30"))
        self.assertTrue(h[1]["search"].startswith("We rely on a single supplier"))


if __name__ == "__main__":
    unittest.main()
