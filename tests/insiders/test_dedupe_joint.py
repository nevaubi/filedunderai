"""Form 4 joint filers: one economic transaction reported by related owners counts once."""
import datetime as dt
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from etl.insiders import summarize  # noqa: E402

BASE = dict(tx_date="2026-10-29", code="P", security="Common", derivative=False, shares=1444402.0, price=34.5864,
            owned_after=146728468.0, acq_disp="A", ownership="D", plan_10b5=False, role="10% owner", seq=0, filed="2026-11-02")


class JointFilers(unittest.TestCase):
    def test_same_transaction_from_two_filers_counts_once(self):
        rows = [{**BASE, "accession": "a2", "owner": "LOR INC"}, {**BASE, "accession": "a1", "owner": "Voting Trust", "ownership": "I"}]
        s = summarize(rows, today=dt.date(2026, 11, 7))
        self.assertEqual(s["w90"]["buys"], 1)
        self.assertEqual(s["w90"]["buy_value"], round(1444402 * 34.5864))
        self.assertEqual(s["rows"][0]["joint"], ["LOR INC"])

    def test_same_size_lots_with_different_holdings_after_stay_separate(self):
        rows = [{**BASE, "accession": "a", "owner": "Pohlad", "seq": 1, "shares": 45075.0, "owned_after": 377945.0},
                {**BASE, "accession": "a", "owner": "Pohlad", "seq": 2, "shares": 45075.0, "owned_after": 423020.0}]
        self.assertEqual(summarize(rows, today=dt.date(2026, 11, 7))["w90"]["buys"], 2)


if __name__ == "__main__":
    unittest.main()
