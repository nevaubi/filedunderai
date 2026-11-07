"""Earnings workflow: release parsing, verification of model output, consensus and guidance matching, call diffs,
season aggregates and the preview's "listen for" questions."""
import datetime as dt
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from etl import earnings as E  # noqa: E402

RELEASE = """EX-99.1
Exhibit 99.1
Acme Corp Reports Third Quarter 2026 Results
Revenues of $1,026.2 million
GAAP diluted EPS of $5.29
Non-GAAP diluted EPS of $5.32
The Company today announced results for the third quarter ended September 30, 2026.
Segment Results
Retail Net Revenues of $865 million compared to $864 million in Q3 2025
An increase in water operating revenues of $7.6 million was largely a result of rate increases.
Year-to-Date 2026 Highlights
Net Revenues of $2,765 million compared to $2,687 million in the comparable prior year period
The Company expects revenue in the range of $1,050 million to $1,080 million for the fourth quarter, with EPS of $5.40 to $5.60.
Compared to the three months ended September 30, 2025, revenue grew."""

SPLIT = """Third Quarter 2026 Highlights
Net Revenues
of $944 million compared to $931 million in Q3 2025
Earnings Per Share
of $0.28 compared to $0.38 in Q3 2025, and
Adjusted Earnings Per Share
of $0.39 vs.
$0.42 in Q3 2025"""

TABLE_ONLY = """DraftKings Reports Third Quarter Revenue Growth of 16%
For the three months ended September 30, 2026, DraftKings reported total revenue of $1,329 million, an increase of $185 million, or 16%, compared to $1,144 million during the same period in 2025.
CONDENSED CONSOLIDATED STATEMENTS OF OPERATIONS (Amounts in thousands, except per share data)
Earnings (loss) per share attributable to common stockholders:
Basic and diluted
$
(0.94)
$
(0.52)
Adjusted Earnings (Loss) Per Share
$
(0.53)
$
(0.26)"""


class Release(unittest.TestCase):
    def test_headline_figures_keep_their_sentence_and_skip_segments_changes_guidance_and_ytd(self):
        h = E.rule_headlines(RELEASE)
        self.assertEqual(h["revenue"]["value"], 1026.2e6)
        self.assertIn("Revenues of $1,026.2 million", h["revenue"]["quote"])
        self.assertEqual(h["eps_gaap"]["value"], 5.29)
        self.assertEqual(h["eps_adjusted"]["value"], 5.32)
        self.assertEqual(E.quarter_end_in(RELEASE, "2026-11-06"), "2026-09-30")

    def test_labels_split_from_values_across_lines_are_joined(self):
        h = E.rule_headlines(SPLIT)
        self.assertEqual(h["revenue"]["value"], 944e6)
        self.assertEqual(h["eps_gaap"]["value"], 0.28)
        self.assertEqual(h["eps_adjusted"]["value"], 0.39)

    def test_table_per_share_rows_are_read_and_marked_unconfirmed(self):
        h = E.rule_headlines(TABLE_ONLY)
        self.assertEqual(h["revenue"]["value"], 1329e6)
        self.assertEqual(h["revenue"]["prior_year"], 1144e6)
        self.assertEqual(h["eps_adjusted"]["value"], -0.53)
        self.assertEqual(h["eps_gaap"]["value"], -0.94)
        self.assertTrue(h["eps_gaap"]["unconfirmed"])
        self.assertEqual(h["eps_gaap"]["prior_year"], -0.52)


class ModelVerification(unittest.TestCase):
    def test_items_need_a_verbatim_quote_and_matching_scale_then_the_second_model(self):
        text = "Revenue was $2.80 billion, up 8%. Subscribers reached 12.5 million. We expect fourth quarter revenue of $2.9 billion to $3.0 billion."

        def draft(prompt):
            return ({"quarter_end": "2026-09-30",
                     "revenue": {"printed": "$2.80", "scale": "billion", "quote": "Revenue was $2.80 billion, up 8%."},
                     "kpis": [{"name": "Subscribers", "printed": "12.5", "scale": "million", "unit": "count", "quote": "Subscribers reached 12.5 million."},
                              {"name": "Churn", "printed": "1.1", "scale": "", "unit": "%", "quote": "Churn was 1.1%."},
                              {"name": "ARPU", "printed": "40", "scale": "billion", "unit": "USD", "quote": "Subscribers reached 12.5 million."}],
                     "guidance": [{"metric": "Revenue", "period": "Q4", "low_printed": "$2.9", "high_printed": "$3.0", "scale": "billion", "unit": "USD",
                                   "quote": "We expect fourth quarter revenue of $2.9 billion to $3.0 billion."}]}, "m1")
        checked = []

        def check(model, prompt):
            checked.append(model)
            return {"verdicts": [{"n": 1, "supported": True}, {"n": 2, "supported": True}, {"n": 3, "supported": False, "reason": "period"}]}
        out, meta = E.model_extract(text, complete_fn=draft, check_fn=check)
        self.assertEqual(out["revenue"]["value"], 2.8e9)
        self.assertEqual([k["name"] for k in out["kpis"]], ["Subscribers"])
        self.assertEqual(out["guidance"], [])
        reasons = " ".join(d["reason"] for d in meta["dropped"])
        self.assertIn("quote not verbatim", reasons)      # Churn
        self.assertIn("printed number not in quote", reasons)  # ARPU
        self.assertIn("check: period", reasons)           # guidance rejected by the second model
        self.assertEqual(len(checked), 1)
        self.assertNotEqual(checked[0], "m1")

    def test_failed_check_publishes_no_model_items(self):
        text = "Revenue was $2.80 billion."

        def boom(model, prompt):
            raise RuntimeError("timeout")
        out, meta = E.model_extract(text, complete_fn=lambda p: ({"revenue": {"printed": "$2.80", "scale": "billion", "quote": "Revenue was $2.80 billion."}}, "m1"),
                                    check_fn=boom)
        self.assertNotIn("revenue", out)
        self.assertIn("check_error", meta)


class Matching(unittest.TestCase):
    def test_consensus_from_the_last_snapshot_before_the_release(self):
        est = {"snapshots": [{"as_of": "2026-10-20", "p": {"0q": {"end": "2026-09-30", "eps": 1.00, "eps_n": 10, "rev": 5e9, "rev_n": 8}}},
                             {"as_of": "2026-11-03", "p": {"0q": {"end": "2026-09-30", "eps": 1.05, "eps_n": 11, "rev": 5.1e9, "rev_n": 8}}},
                             {"as_of": "2026-11-06", "p": {"0q": {"end": "2026-12-31", "eps": 1.20, "eps_n": 11}}}]}
        c = E.consensus_for(est, "2026-11-05")
        self.assertEqual((c["basis"], c["eps"], c["revenue"]), ("snapshot", 1.05, 5.1e9))
        c2 = E.consensus_for({"surprises": [{"quarter_end": "2026-09-30", "eps_estimate": 0.9}]}, "2026-11-05")
        self.assertEqual((c2["basis"], c2["eps"]), ("provider surprise record", 0.9))

    def test_guidance_for_the_reported_quarter_issued_before_the_release(self):
        g = {"series": [{"metric": "revenue", "label": "Revenue", "basis": "gaap", "measure": "level", "unit": "USD", "segment": None,
                         "periods": [{"label": "Q3 FY26", "type": "Q", "end": "2026-09-27", "points": [
                             {"d": "2026-07-30", "lo": 5e9, "hi": 5.2e9, "mid": 5.1e9, "q": "revenue of $5.0 billion to $5.2 billion"},
                             {"d": "2026-11-05", "lo": 9e9, "hi": 9e9, "mid": 9e9, "q": "later"}]}]}]}
        out = E.guidance_for(g, "2026-09-30", "2026-11-05")
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["hi"], 5.2e9)
        self.assertEqual(E._vs_range(5.3e9, out[0]["lo"], out[0]["hi"], out[0]["mid"]), "above")
        self.assertEqual(E._vs_range(5.1e9, 5e9, 5.2e9, None), "within")

    def test_trading_days(self):
        fri = dt.date(2026, 11, 6)
        self.assertEqual(E.trading_days_until(fri, dt.date(2026, 11, 9)), 1)
        self.assertEqual(E.trading_days_until(fri, dt.date(2026, 11, 11)), 3)
        self.assertEqual(E.trading_days_until(fri, dt.date(2026, 11, 4)), -2)


FEED = """<feed><entry>
<title>8-K - Acme Corp (0000320193) (Filer)</title>
<link rel="alternate" type="text/html" href="https://www.sec.gov/Archives/edgar/data/320193/000032019326000024/0000320193-26-000024-index.htm"/>
<summary type="html"> &lt;b&gt;Filed:&lt;/b&gt; 2026-11-02 &lt;b&gt;AccNo:&lt;/b&gt; 0000320193-26-000024 &lt;b&gt;Size:&lt;/b&gt; 2 MB
&lt;br&gt;Item 2.02: Results of Operations and Financial Condition
&lt;br&gt;Item 9.01: Financial Statements and Exhibits</summary>
<updated>2026-11-02T16:30:23-05:00</updated>
<category scheme="https://www.sec.gov/" label="form type" term="8-K"/>
<id>urn:tag:sec.gov,2008:accession-number=0000320193-26-000024</id>
</entry><entry>
<title>8-K - Other Inc (0000000001) (Filer)</title>
<summary type="html">&lt;b&gt;Filed:&lt;/b&gt; 2026-11-02 &lt;br&gt;Item 8.01: Other Events</summary>
<id>urn:tag:sec.gov,2008:accession-number=0000000001-26-000001</id>
</entry></feed>"""


class Feed(unittest.TestCase):
    def test_parse_and_stop_at_seen(self):
        ents = E.parse_feed(FEED)
        self.assertEqual(ents[0]["accession"], "0000320193-26-000024")
        self.assertEqual(ents[0]["cik"], 320193)
        self.assertEqual(ents[0]["items"], ["2.02", "9.01"])
        self.assertEqual(ents[0]["filed"], "2026-11-02")
        self.assertEqual(len(ents), 2)
        new = E.poll_feed({"0000000001-26-000001"}, fetch=lambda u: FEED)
        self.assertEqual([e["accession"] for e in new], ["0000320193-26-000024"])


class Calls(unittest.TestCase):
    def test_started_stopped_unanswered_and_guidance_changes(self):
        calls = {"calls": [
            {"call_id": "B", "event_type": "earnings", "kind": "transcript", "date": "2026-09-10", "label": "Q3", "prev_call": "A",
             "themes": [{"theme": "ai", "label": "AI", "sentiment": 0.9}, {"theme": "fx", "label": "FX", "sentiment": -0.2}],
             "guidance": [{"metric": "Revenue", "period": "FY26", "low": 25.9e9, "high": 26.1e9, "quote": {"q": "FY revenue $25.9 to $26.1 billion"}}],
             "exchanges": [{"concern": "Why did ARR slow?", "answer": "partial", "reason": "no numbers"}, {"concern": "AI?", "answer": "direct"}]},
            {"call_id": "A", "event_type": "earnings", "kind": "transcript", "date": "2026-06-11", "label": "Q2",
             "themes": [{"theme": "ai", "label": "AI", "sentiment": 0.4}, {"theme": "capital_allocation", "label": "Buybacks"}],
             "guidance": [{"metric": "Revenue", "period": "FY26", "low": 25.6e9, "high": 25.9e9}]}]}
        d = E.build_call_diff(calls)
        self.assertEqual([x["theme"] for x in d["started"]], ["fx"])
        self.assertEqual([x["theme"] for x in d["stopped"]], ["capital_allocation"])
        self.assertEqual(d["tone_shifts"][0]["theme"], "ai")
        self.assertEqual(d["guidance_log"][0]["status"], "raised")
        self.assertEqual([x["concern"] for x in d["not_answered"]], ["Why did ARR slow?"])


class Season(unittest.TestCase):
    def test_beat_rates_moves_and_outlier_surprises(self):
        today = dt.date(2026, 11, 7)
        idx = [{"ticker": "A", "earnings": {"date": "2026-11-10", "last": {"report": {"filed": "2026-11-03"}, "beat": 1, "surprise_pct": 0.05, "move": 0.03, "move_date": "2026-11-04"}}},
               {"ticker": "B", "earnings": {"last": {"report": {"filed": "2026-11-04"}, "beat": -1, "surprise_pct": -0.1, "move": -0.05, "move_date": "2026-11-05"}}},
               {"ticker": "C", "earnings": {"last": {"report": {"filed": "2026-11-04"}, "beat": 1, "surprise_pct": -321.0, "move": 0.01}}},
               {"ticker": "D", "earnings": {"last": {"report": {"filed": "2026-08-01"}, "beat": 1}}}]
        names = {t: {"name": t, "sector": "Tech"} for t in "ABCD"}
        s = E.build_season(idx, names, today=today, spy={"2026-11-04": 0.01})
        self.assertEqual([u["ticker"] for u in s["upcoming"]], ["A"])
        self.assertEqual({r["ticker"] for r in s["reported"]}, {"A", "B", "C"})
        sec = s["sectors"][0]
        self.assertEqual((sec["n"], sec["beats"], sec["misses"]), (3, 2, 1))
        self.assertEqual(sec["median_surprise"], -0.025)  # the -321 provider error is left out
        a = next(r for r in s["reported"] if r["ticker"] == "A")
        self.assertAlmostEqual(a["excess"], 0.02)


class Questions(unittest.TestCase):
    def preview(self):
        return {"ticker": "ACME", "name": "Acme", "report": {"date": "2026-11-10"},
                "consensus": {"period": {"end": "2026-09-30"}, "eps": {"avg": 1.05, "n": 11, "low": 0.98, "high": 1.10, "year_ago": 0.90},
                              "revenue": {"avg": 5.1e9, "n": 8, "year_ago": 4.6e9, "yoy": 5.1e9 / 4.6e9 - 1}},
                "guidance": {"open": [{"metric": "revenue", "label": "Revenue", "period": "Q3 FY26", "unit": "USD", "final": {"lo": 5.0e9, "hi": 5.2e9, "issued": "2026-07-30"}, "actions": ["initial"]}],
                             "credibility": {"n_scored": 20, "hit_rate": 0.7, "above_rate": 0.6, "raise_rate": 0.3}},
                "kpis": [{"id": "subs", "name": "Subscribers", "unit": "count", "latest": {"v": 12.5e6, "label": "Q2 FY26"}, "yoy": 0.08}],
                "reactions": {"rows": [{"filed": "2026-07-30", "cc": -0.04, "abn": -0.05}], "median_abs_last8": 0.05},
                "filing_changes": {"form": "10-Q", "filed": "2026-08-01", "totals": {"added": 10, "removed": 4, "modified": 7}, "top_sections": [{"title": "Item 2. MD&A"}]},
                "insiders": {"since": "2026-07-30", "buys": 0, "sells": 3, "sell_value": 2.5e6, "plan_sells": 3},
                "last_call": {"label": "Q2", "date": "2026-07-30", "unanswered": [{"concern": "Why did churn rise?", "answer": "evasive"}]}}

    def test_rule_questions_cite_existing_facts(self):
        p = self.preview()
        facts = E.preview_facts(p)
        ids = {i for i, _ in facts}
        self.assertIn("guide:0", ids)
        self.assertIn("call:0", ids)
        qs = E.rule_questions(p, facts)
        self.assertTrue(3 <= len(qs) <= 5)
        self.assertTrue(all(r in ids for q in qs for r in q["refs"]))

    def test_model_questions_with_unsupported_numbers_are_dropped(self):
        p = self.preview()
        facts = E.preview_facts(p)
        good = [{"text": "Does revenue land inside the $5.00B to $5.20B range against consensus of $5.10B?", "refs": ["guide:0", "cons:rev"]},
                {"text": "Does management answer the churn question it gave an evasive answer to on the Q2 call?", "refs": ["call:0"]},
                {"text": "Do subscribers keep growing from 12.50M?", "refs": ["kpi:subs"]},
                {"text": "Does EPS reach $1.40?", "refs": ["cons:eps"]}]
        verdicts = {"verdicts": [{"n": i, "supported": True} for i in range(1, 4)]}
        qs, meta = E.listen_for(p, facts, complete_fn=lambda prompt: ({"questions": good}, "kimi-k3p1-pro"), post=lambda model, prompt: verdicts)
        self.assertEqual(meta["method"], "model")
        self.assertEqual(len(qs), 3)
        self.assertTrue(any("numbers not in cited facts" in d["reason"] for d in meta["dropped"]))

    def test_without_a_model_rules_are_used(self):
        p = self.preview()
        qs, meta = E.listen_for(p, E.preview_facts(p), allow_model=False)
        self.assertEqual(meta["method"], "rules")
        self.assertTrue(qs)


if __name__ == "__main__":
    unittest.main()
