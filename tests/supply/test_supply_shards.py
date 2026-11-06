"""Sharded supply rotation: disjoint companies per shard, and state merges keep other shards' updates."""
import contextlib
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from etl import supply  # noqa: E402


class FakeStore:
    def __init__(self):
        self.files = {"supply/state.json": {"tickers": {}}}


class ShardTests(unittest.TestCase):
    def run_shard(self, store, shard, shards, extra_writer=None):
        seen = []

        def get(sb, path):
            import copy
            return copy.deepcopy(store.files.get(path))

        def put(sb, path, doc, cache=None):
            if extra_writer and path == "supply/state.json":
                extra_writer(store)
            store.files[path] = doc

        def process(sb, t, mode=None, deadline=None, p=None):
            seen.append(t)
            return [], {"method": "llm", "links": []}, None

        with patch.dict(os.environ, {"FIREWORKS_API_KEY": "x"}), \
                patch.object(supply, "UNIVERSE", [(t,) for t in ["A", "B", "C", "D", "E", "F"]]), \
                patch.object(supply, "public_get", side_effect=get), patch.object(supply, "public_put", side_effect=put), \
                patch.object(supply, "plan", return_value={"filings": [{"form": "10-K", "accn": "k1"}]}), \
                patch.object(supply, "process_company", side_effect=process), \
                patch.object(supply, "merge_graph", return_value={}), \
                patch.object(supply, "latest_periodic", return_value=("k1", None)), \
                patch.object(supply, "AggregateLease", lambda *a, **k: contextlib.nullcontext()):
            stats = supply.rotate_job(None, workers=1, shard=shard, shards=shards)
        return sorted(seen), stats

    def test_shards_take_disjoint_companies(self):
        store = FakeStore()
        a, _ = self.run_shard(store, 0, 3)
        b, _ = self.run_shard(store, 1, 3)
        c, stats = self.run_shard(store, 2, 3)
        self.assertEqual((a, b, c), (["A", "D"], ["B", "E"], ["C", "F"]))
        self.assertEqual(sorted(store.files["supply/state.json"]["tickers"]), list("ABCDEF"))
        self.assertEqual(stats["remaining"], 0)

    def test_merge_keeps_concurrent_shard_updates(self):
        store = FakeStore()
        def other(st):
            st.files["supply/state.json"]["tickers"]["Z"] = {"k": "z"}
        self.run_shard(store, 0, 2, extra_writer=None)
        store.files["supply/state.json"]["tickers"]["Z"] = {"k": "z"}
        self.run_shard(store, 1, 2)
        self.assertIn("Z", store.files["supply/state.json"]["tickers"])
        self.assertEqual(sorted(t for t in store.files["supply/state.json"]["tickers"] if t != "Z"), list("ABCDEF"))


if __name__ == "__main__":
    unittest.main()
