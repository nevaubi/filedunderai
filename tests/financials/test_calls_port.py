"""Earnings-call pipeline on main: model calls go through etl.llm.chat_json; a refused account fails the run fast."""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from etl import calls, llm  # noqa: E402


class ModelCalls(unittest.TestCase):
    def setUp(self):
        calls.LLM_DOWN.clear()

    def test_reply_and_metadata(self):
        with patch.object(llm, "chat_json", return_value=({"themes": []}, "m1", {"seconds": 1.5})) as cj:
            j, meta = calls._model("p", 2000)
        self.assertEqual((j, meta["model"], meta["seconds"]), ({"themes": []}, "m1", 1.5))
        self.assertEqual(cj.call_args.args[0], [calls.PRIMARY, calls.FALLBACK])

    def test_refused_account_stops_later_calls(self):
        err = llm.LLMError("all models failed", [{"model": "m1", "error": "HTTP 402: payment required"}])
        with patch.object(llm, "chat_json", side_effect=err):
            with self.assertRaises(llm.LLMError):
                calls._model("p", 2000)
        self.assertTrue(calls.LLM_DOWN)
        with patch.object(llm, "chat_json") as cj, self.assertRaises(RuntimeError):
            calls._model("p", 2000)
        cj.assert_not_called()


if __name__ == "__main__":
    unittest.main()
