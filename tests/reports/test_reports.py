"""Report writer: citation and number validation, deterministic metrics, markdown and DOCX rendering."""
import io
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from etl import reports  # noqa: E402


def evidence():
    return {
        "CO": {"kind": "Company", "title": "Company profile", "data": {"name": "Apple Inc."}, "source": {"name": "SEC"}, "as_of": "2026-11-03"},
        "FIN:Revenue": {"kind": "SEC financial statements", "title": "Revenue (income)", "as_of": "2026-11-03",
                        "source": {"name": "10-K filed 2026-11-03", "url": "https://www.sec.gov/x"},
                        "data": {"metric": "Revenue", "statement": "income", "unit": "USD",
                                 "quarterly": [["2025-09-27", 102466000000], ["2026-09-26", 114477000000]],
                                 "annual": [["2025-09-27", 416161000000], ["2026-09-26", 478834000000]],
                                 "derived": {"latest_quarter_yoy_change": 0.1172, "latest_year_yoy_change": 0.1506}}},
        "FIN:Net income": {"kind": "SEC financial statements", "title": "Net income (income)", "as_of": "2026-11-03", "source": {"name": "10-K"},
                           "data": {"metric": "Net income", "statement": "income", "unit": "USD", "quarterly": [["2026-09-26", 31000000000]], "annual": [], "derived": {}}},
        "CONS": {"kind": "Consensus", "title": "Consensus", "as_of": "2026-11-06", "source": {"name": "Yahoo"},
                 "data": {"periods": [{"period": "Current quarter", "eps": {"avg": 2.90354, "n": 26}}]}},
    }


def fake(prompt, key):
    return ({"title": "Apple: earnings preview", "subtitle": "December quarter setup",
             "summary": [{"text": "Consensus expects EPS of $2.90 from 26 estimates.", "cites": ["CONS"]},
                         {"text": "Revenue rose 11.7% to $114.5B.", "cites": ["FIN:Revenue"]},
                         {"text": "Margins will be 99%.", "cites": ["FIN:Revenue"]},
                         {"text": "Uncited claim.", "cites": ["NOPE"]}],
             "sections": [{"id": "setup", "heading": "What the Street expects", "paragraphs": [{"text": "EPS consensus is $2.90.", "cites": ["CONS"]}], "bullets": []},
                          {"id": "last_quarter", "heading": "Where the last quarter left off", "paragraphs": [{"text": "FY revenue was $478.8B, up 15.1%.", "cites": ["FIN:Revenue"]}]},
                          {"id": "bogus", "heading": "Ignored", "paragraphs": [{"text": "x", "cites": ["CO"]}]}]}, "test-model")


class ReportTests(unittest.TestCase):
    def test_write_validates_cites_and_numbers(self):
        doc = reports.write("preview", "AAPL", evidence(), completer=fake, key="k")
        self.assertEqual([s["id"] for s in doc["sections"]], ["setup", "last_quarter"])
        texts = {p["text"]: p for p in doc["summary"]}
        self.assertNotIn("Uncited claim.", texts)
        self.assertTrue(texts["Consensus expects EPS of $2.90 from 26 estimates."]["verified"])
        self.assertTrue(texts["Revenue rose 11.7% to $114.5B."]["verified"])
        self.assertFalse(texts["Margins will be 99%."]["verified"])
        self.assertEqual(doc["quality"]["unverified"], 1)
        self.assertEqual(doc["metrics"][0]["metric"], "Revenue")
        self.assertEqual(doc["metrics"][0]["quarter_yoy"], 0.1172)

    def test_markdown_and_docx_render(self):
        doc = reports.write("preview", "AAPL", evidence(), completer=fake, key="k")
        md = reports.to_markdown(doc)
        self.assertIn("## Key metrics", md)
        self.assertIn("(unverified figure)", md)
        self.assertIn("## Sources", md)
        blob = reports.to_docx(doc)
        from docx import Document
        d = Document(io.BytesIO(blob))
        text = "\n".join(p.text for p in d.paragraphs)
        self.assertIn("What the Street expects", text)
        self.assertIn("[1]", text + "".join(r.text for p in d.paragraphs for r in p.runs))


if __name__ == "__main__":
    unittest.main()


class ModelReplyParsing(unittest.TestCase):
    def test_parses_fenced_and_reasoning_wrapped_json(self):
        from etl import reports
        self.assertEqual(reports.parse_model_json('<think>plan</think>\n```json\n{"title": "x"}\n```'), {"title": "x"})

    def test_empty_reply_raises_a_clear_error(self):
        from etl import reports
        with self.assertRaises(ValueError) as cm:
            reports.parse_model_json("")
        self.assertIn("no JSON object", str(cm.exception))

    def test_truncated_reply_is_reported_and_next_model_tried(self):
        from unittest.mock import patch
        from etl import reports
        replies = iter(['{"choices":[{"finish_reason":"length","message":{"content":"{\\"title\\": \\"x"}}]}',
                        '{"choices":[{"finish_reason":"stop","message":{"content":"{\\"title\\": \\"ok\\"}"}}]}'])
        with patch.object(reports, "request", side_effect=lambda *a, **k: next(replies)):
            out, model = reports.complete("p", "k", models=["m1", "m2"])
        self.assertEqual((out, model), ({"title": "ok"}, "m2"))
