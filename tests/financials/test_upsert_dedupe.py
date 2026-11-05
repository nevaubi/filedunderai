"""Upserts collapse duplicate conflict keys so Postgres never sees the same row twice in one statement."""
import json
import unittest
from unittest.mock import patch

from etl import common
from etl.common import dedupe_conflict_rows


class DedupeConflictRowsTests(unittest.TestCase):
    def test_last_row_wins_for_composite_key(self):
        rows = [
            {"ticker": "KO", "accession": "a1", "value": 1},
            {"ticker": "KO", "accession": "a2", "value": 2},
            {"ticker": "KO", "accession": "a1", "value": 3},
        ]
        out = dedupe_conflict_rows(rows, "ticker,accession")
        self.assertEqual(out, [
            {"ticker": "KO", "accession": "a2", "value": 2},
            {"ticker": "KO", "accession": "a1", "value": 3},
        ])

    def test_distinct_keys_are_untouched(self):
        rows = [{"accession": "a1"}, {"accession": "a2"}, {"accession": "a3"}]
        self.assertEqual(dedupe_conflict_rows(rows, "accession"), rows)

    def test_whitespace_in_conflict_spec(self):
        rows = [{"a": 1, "b": 2, "v": "x"}, {"a": 1, "b": 2, "v": "y"}]
        self.assertEqual(dedupe_conflict_rows(rows, " a , b "), [{"a": 1, "b": 2, "v": "y"}])

    def test_rows_missing_a_key_column_pass_through(self):
        rows = [{"ticker": "KO"}, {"ticker": "KO"}, {"ticker": "KO", "d": "2026-01-02"}]
        self.assertEqual(dedupe_conflict_rows(rows, "ticker,d"), rows)

    def test_values_that_differ_only_by_type_stay_separate(self):
        rows = [{"k": 1, "v": "int"}, {"k": "1", "v": "str"}]
        self.assertEqual(dedupe_conflict_rows(rows, "k"), rows)

    def test_null_key_values_compare_equal(self):
        rows = [{"k": None, "v": 1}, {"k": None, "v": 2}]
        self.assertEqual(dedupe_conflict_rows(rows, "k"), [{"k": None, "v": 2}])

    def test_empty_conflict_spec_returns_rows(self):
        rows = [{"k": 1}, {"k": 1}]
        self.assertEqual(dedupe_conflict_rows(rows, ""), rows)

    def test_accepts_generators(self):
        out = dedupe_conflict_rows(({"k": i % 2, "v": i} for i in range(4)), "k")
        self.assertEqual(out, [{"k": 0, "v": 2}, {"k": 1, "v": 3}])


class SupabaseUpsertTests(unittest.TestCase):
    def client(self):
        sb = common.Supabase.__new__(common.Supabase)
        sb.url, sb.key = "https://example.supabase.co", "key"
        return sb

    def test_each_request_carries_unique_conflict_keys(self):
        sb = self.client()
        rows = [{"accession": f"a{i % 3}", "n": i} for i in range(9)]
        with patch.object(common, "request", return_value="") as req:
            written = sb.upsert("filings", rows, "accession", batch=2)
        self.assertEqual(written, 3)
        sent = [row for call in req.call_args_list for row in call.kwargs["data"]]
        self.assertEqual(sent, [{"accession": "a0", "n": 6}, {"accession": "a1", "n": 7}, {"accession": "a2", "n": 8}])
        for call in req.call_args_list:
            keys = [row["accession"] for row in call.kwargs["data"]]
            self.assertEqual(len(keys), len(set(keys)))
            self.assertIn("on_conflict=accession", call.args[0])
            self.assertEqual(call.kwargs["method"], "POST")

    def test_payload_is_json_serializable(self):
        sb = self.client()
        with patch.object(common, "request", return_value="") as req:
            sb.upsert("facts", [{"ticker": "KO", "end": "2026-09-30", "v": 1.5}], "ticker,end")
        json.dumps(req.call_args.kwargs["data"])


if __name__ == "__main__":
    unittest.main()
