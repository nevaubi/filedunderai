"""Stale-run reaper."""
import datetime as dt
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from etl.common import reap_stale_runs  # noqa: E402


class FakeSb:
    def __init__(self, rows):
        self.rows, self.updates = rows, []

    def select(self, table, q):
        return [r for r in self.rows]

    def update(self, table, match, data):
        self.updates.append((match["id"], data["status"]))


class ReaperTests(unittest.TestCase):
    def test_short_jobs_reaped_after_45_min_long_jobs_after_7_hours(self):
        now = dt.datetime(2026, 11, 8, 3, 0, tzinfo=dt.timezone.utc)
        rows = [{"id": 1, "job": "screener", "started_at": "2026-11-07T22:55:00+00:00"},
                {"id": 2, "job": "ir", "started_at": "2026-11-07T22:00:00+00:00"},
                {"id": 3, "job": "ir", "started_at": "2026-11-07T18:00:00+00:00"},
                {"id": 4, "job": "kpi_extract", "started_at": "2026-11-07T21:00:00Z"}]
        sb = FakeSb(rows)
        out = reap_stale_runs(sb, now=now)
        self.assertEqual(sorted(i for i, _ in sb.updates), [1, 3])
        self.assertEqual(out, {"screener": 1, "ir": 1})


if __name__ == "__main__":
    unittest.main()


def test_run_outcome_accepts_skip_reason_strings():
    from etl.common import run_outcome
    assert run_outcome({"skipped": "FIREWORKS_API_KEY is not set; extraction needs the model"}) == "skipped"
    assert run_outcome({"skipped": 3, "done": 2}) == "partial"
    assert run_outcome({"skipped": ["a"], "done": 0}) == "skipped"
