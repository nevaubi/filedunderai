import datetime as dt
import unittest

from etl import company_brief as B


class Store:
    def __init__(self, data):
        self.data = data

    def get_json(self, p):
        return self.data.get(p)

    def put_json(self, p, o, cache=None):
        self.data[p] = o


DAY = 86400
T0 = int(dt.datetime(2026, 10, 1, tzinfo=dt.timezone.utc).timestamp())
DATA = {
    "prices/T.json": {"d": [[T0 + k * DAY, 0, 0, 0, 100 + k, 0] for k in range(37)]},
    "prices/SPY.json": {"d": [[T0 + k * DAY, 0, 0, 0, 500, 0] for k in range(37)]},
    "estimates/T.json": {"actions": [{"id": "a1", "date": "2026-11-02", "firm": "Firm A", "action": "Upgrades", "rating_prior": "Hold",
                                      "rating_current": "Buy", "pt_prior": 100.0, "pt_current": 130.0}],
                         "consensus": {"next_earnings": {"date": "2026-11-20", "is_estimate": False, "eps": {"avg": 1.25}}},
                         "surprises": [{"quarter_end": "2026-09-30", "eps_actual": 1.1, "eps_estimate": 1.0, "surprise_pct": 0.1}]},
}
TODAY = dt.date(2026, 11, 7)


class CompanyBrief(unittest.TestCase):
    def test_gather(self):
        f = {i: t for i, t, _ in B.gather(Store(dict(DATA)), "T", TODAY)}
        self.assertIn("act:a1", f)
        self.assertIn("Hold to Buy, price target 100 to 130", f["act:a1"])
        self.assertIn("earn:next", f)
        self.assertTrue(f["px:30d"].startswith("Share price change"))

    def test_brief_checks_and_drops_repeats(self):
        sb = Store(dict(DATA))
        draft = {"sentences": [{"text": "Firm A upgraded the stock to Buy with a 130 target.", "refs": ["act:a1"]},
                               {"text": "Earnings are due 2026-11-20 with consensus EPS of 1.25.", "refs": ["earn:next"]},
                               {"text": "EPS was 1.9 last quarter.", "refs": ["earn:last"]}]}

        def post(model, prompt):
            return {"verdicts": [{"n": 1, "supported": True}, {"n": 2, "supported": True}]}
        state = {}
        res, why = B.brief(sb, "T", "T Corp", state, today=TODAY, complete_fn=lambda p: (draft, B.G.PRIMARY), post=post)
        self.assertIsNone(why)
        self.assertEqual(len(res["sentences"]), 2)  # the invented 1.9 fails the number check
        self.assertEqual({f["id"] for f in res["facts"]}, {"act:a1", "earn:next"})
        self.assertEqual(res["facts"][0]["link"], {"tab": "estimates"})
        # a later run with a different fact set drops sentences resting only on facts already used
        sb.data["insiders/T.json"] = {"w90": {"buys": 1, "sells": 0, "buy_value": 50000, "sell_value": 0, "plan_sells": 0}}
        res2, _ = B.brief(sb, "T", "T Corp", state, today=TODAY, complete_fn=lambda p: (draft, B.G.PRIMARY), post=post)
        self.assertEqual(res2["sentences"], [])
        self.assertTrue(all("not new" in d["reason"] or "numbers" in d["reason"] for d in res2["dropped"]))


if __name__ == "__main__":
    unittest.main()
