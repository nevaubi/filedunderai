"""Quant reads: only symbols whose statistics changed since their last read call the model."""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from etl import quant  # noqa: E402


class FakeSB:
    def __init__(self, files):
        self.files = dict(files)

    def get_json(self, path):
        return self.files.get(path)

    def put_json(self, path, obj, cache=None):
        self.files[path] = obj


class RunReads(unittest.TestCase):
    def test_only_changed_skips_unchanged_symbols(self):
        sb = FakeSB({quant.qpath("AAA"): {"as_of": "2026-11-06"}, quant.qpath("BBB"): {"as_of": "2026-11-06"},
                     "quant/read/AAA.json": {"as_of": "2026-11-06"}, "quant/read/BBB.json": {"as_of": "2026-11-05"}})
        calls = []
        with patch.dict(os.environ, {"FIREWORKS_API_KEY": "k"}), \
             patch.object(quant, "quant_read", side_effect=lambda doc: calls.append(doc) or {"as_of": doc["as_of"], "sentences": []}):
            out = quant.run_reads(sb, ["AAA", "BBB"], limit=10, workers=2, only_changed=True)
        self.assertEqual(len(calls), 1)
        self.assertEqual(out["written"], 1)
        self.assertEqual(out["unchanged"], 1)
        self.assertEqual(sb.files["quant/read/BBB.json"]["as_of"], "2026-11-06")


if __name__ == "__main__":
    unittest.main()
