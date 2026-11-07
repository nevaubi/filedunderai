"""IR backfill runner: shard ownership and lane planning."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from etl import ir_backfill_runner as R  # noqa: E402


class ShardTests(unittest.TestCase):
    def test_every_ticker_has_exactly_one_shard_whatever_the_list(self):
        tickers = [f"T{i}" for i in range(500)]
        owners = {t: R.shard_of(t, 19) for t in tickers}
        self.assertTrue(all(0 <= s < 19 for s in owners.values()))
        # Removing tickers (a different skip set on another shard) never moves the rest.
        self.assertEqual({t: R.shard_of(t, 19) for t in tickers[100:]}, {t: owners[t] for t in tickers[100:]})


class SecDueTests(unittest.TestCase):
    def test_open_sec_enumeration_is_due_and_recovered_manifests_count(self):
        index = {"companies": {
            "A": {"docs": 3, "coverage": {"enumeration": {"sec": "complete"}, "updated_at": "2026-11-01"}},
            "B": {"docs": 2, "coverage": {"enumeration": {"sec": "unverified"}, "updated_at": "2026-11-02"}},
            "C": {"docs": 0, "coverage": {}},
            "D": {"docs": 1, "coverage": {"enumeration": {"sec": "failed"}, "updated_at": "2026-10-01"}}}}
        recovered = {"C": {"enumeration": {"sec": "unverified"}, "updated_at": "2026-11-07"}}
        self.assertEqual(R.sec_due(index, recovered), ["D", "B", "C"])


if __name__ == "__main__":
    unittest.main()
