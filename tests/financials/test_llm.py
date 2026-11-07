import unittest

from etl import llm


def reply(content, finish="stop"):
    return {"choices": [{"message": {"content": content}, "finish_reason": finish}], "usage": {"completion_tokens": 10}}


class LLM(unittest.TestCase):
    def test_parse_variants(self):
        self.assertEqual(llm.parse_json('<think>x {"a": 1}</think>```json\n{"a": 2, "b": [1,2,],}\n```'), {"a": 2, "b": [1, 2]})
        self.assertEqual(llm.parse_json('prefix {"s": "a } b"} trailing {"x":1}'), {"s": "a } b"})
        with self.assertRaises(ValueError):
            llm.parse_json('{"a": [1, 2')

    def test_retries_cutoff_then_falls_back(self):
        calls = []

        def post(model, messages, budget):
            calls.append((model, budget))
            if model == "m1":
                return reply('{"a": ', "length") if budget < 4000 else reply("")
            return reply('{"ok": true}')
        obj, model, meta = llm.chat_json(["m1", "m2"], [], max_tokens=2000, post=post)
        self.assertEqual((obj, model), ({"ok": True}, "m2"))
        self.assertEqual(calls[:2], [("m1", 2000), ("m1", 4000)])

    def test_all_fail_raises_with_attempts(self):
        with self.assertRaises(llm.LLMError) as e:
            llm.chat_json(["m1"], [], post=lambda m, msgs, b: reply("not json"))
        self.assertEqual(len(e.exception.attempts), 2)


if __name__ == "__main__":
    unittest.main()
