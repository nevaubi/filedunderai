"""Screener fund cache held compressed per company; same on-storage format."""
import gzip
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from etl.screener import CompactCache  # noqa: E402


class CompactCacheTests(unittest.TestCase):
    def test_round_trip_keeps_format_and_values(self):
        d = {"_v": 6, "AAA": {"key": "k1", "pipeline_version": 2, "q": {"Revenue": [["2026-09-30", 1.5]]}, "fy": {}},
             "BB.B": {"key": "k2", "pipeline_version": 2, "q": {}, "source_provenance": {"status": "verified"}}}
        raw = gzip.compress(json.dumps(d, separators=(",", ":")).encode())
        c = CompactCache.from_gz(raw)
        self.assertEqual(c.get("_v"), 6)
        self.assertEqual(c["AAA"], d["AAA"])
        self.assertEqual(c.meta("AAA"), {"key": "k1", "pipeline_version": 2, "fy": True})
        self.assertEqual(c.meta("BB.B")["source_provenance"], {"status": "verified"})
        self.assertFalse(c.meta("BB.B")["fy"])
        self.assertEqual(json.loads(gzip.decompress(c.to_gz())), d)

    def test_set_pop_and_missing(self):
        c = CompactCache(6)
        c["X"] = {"key": "a", "pipeline_version": 2}
        self.assertIn("X", c)
        self.assertIsNotNone(c.pop("X"))
        self.assertIsNone(c.get("X"))
        self.assertIsNone(c.meta("X"))
        self.assertEqual(json.loads(gzip.decompress(c.to_gz())), {"_v": 6})


if __name__ == "__main__":
    unittest.main()
