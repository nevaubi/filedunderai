"""Checks of etl/quant.py and etl/quant_sources.py on synthetic inputs (no network, no storage)."""
import io
import json
import math
import os
import sys
import unittest
import zipfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)
from etl import quant as QN  # noqa: E402
from etl import quant_sources as S  # noqa: E402

DAYS = ["2026-03-02", "2026-03-03", "2026-03-04", "2026-03-05", "2026-03-06", "2026-03-09"]


def hist(c, o=None, days=None, v=None):
    days = days or [f"D{i:04d}" for i in range(len(c))]
    H = {"d": days, "o": o or list(c), "h": [x * 1.01 for x in c], "l": [x * 0.99 for x in c], "c": list(c), "v": v or [100] * len(c), "div": [], "long": True, "file": "x", "meta": {}}
    return QN.prep(H)


class ReactionDay(unittest.TestCase):
    def test_timing_rules(self):
        # 2026-03-04 21:30 UTC = 16:30 EST -> next trading day
        i, w, t = QN.reaction_day(DAYS, {"accepted": "2026-03-04T21:30:00.000Z", "filed": "2026-03-04"})
        self.assertEqual((DAYS[i], w, t), ("2026-03-05", 1, "after close"))
        # 12:00 UTC = 07:00 EST -> same day, pre-market
        i, w, t = QN.reaction_day(DAYS, {"accepted": "2026-03-04T12:00:00.000Z", "filed": "2026-03-04"})
        self.assertEqual((DAYS[i], t), ("2026-03-04", "pre-market"))
        # 15:00 UTC = 10:00 EST -> same day, intraday
        self.assertEqual(QN.reaction_day(DAYS, {"accepted": "2026-03-04T15:00:00.000Z", "filed": "2026-03-04"})[2], "intraday")
        # Friday after the close -> Monday
        i, _, _ = QN.reaction_day(DAYS, {"accepted": "2026-03-06T22:00:00.000Z", "filed": "2026-03-06"})
        self.assertEqual(DAYS[i], "2026-03-09")
        # no acceptance time -> filing day, 2-day window
        i, w, t = QN.reaction_day(DAYS, {"accepted": "", "filed": "2026-03-03"})
        self.assertEqual((DAYS[i], w, t), ("2026-03-03", 2, "timing unknown"))


class Reaction(unittest.TestCase):
    def test_abnormal_return_by_hand(self):
        n = 300
        bench = [100 * (1.001 if k % 2 else 0.999) ** 0 for k in range(n)]
        # benchmark alternates +1% / -1%; the stock is exactly 2x the benchmark plus a +5% jump on day 290
        b, s = [100.0], [50.0]
        for k in range(1, n):
            rb = 0.01 if k % 2 else -0.01
            b.append(b[-1] * (1 + rb))
            s.append(s[-1] * (1 + 2 * rb + (0.05 if k == 290 else 0)))
        H, B = hist(s), hist(b)
        rb_ = QN.bench_returns(H, B)
        x = QN.reaction(H, rb_, None, 290, 1)
        self.assertAlmostEqual(x["beta"], 2.0, places=9)
        self.assertAlmostEqual(x["abn"], 0.05, places=9)            # r - 2 * rb = 0.05 exactly
        self.assertAlmostEqual(x["cc"], s[290] / s[289] - 1, places=12)
        self.assertAlmostEqual(x["a5"], 0.0, places=9)
        self.assertIsNone(x["sigma"])                                 # residual SD is zero -> undefined

    def test_summary_drift_and_continuation(self):
        rs = [{"abn": 0.03, "cc": 0.04, "gap": 0.02, "sigma": 2.0, "a5": 0.01, "d5": 0.01, "a20": 0.02, "d20": 0.02},
              {"abn": -0.02, "cc": -0.03, "gap": -0.01, "sigma": 1.5, "a5": -0.01, "d5": -0.01, "a20": 0.01, "d20": 0.01},
              {"abn": 0.01, "cc": 0.0, "gap": 0.0, "sigma": 0.5, "a5": 0.0, "d5": 0.0, "a20": -0.01, "d20": -0.01}]
        s = QN.summarize_reactions(rs, typical=0.01)
        self.assertAlmostEqual(s["mean_abs_abn"], 0.02)
        self.assertAlmostEqual(s["abs_ratio"], 2.0)
        self.assertAlmostEqual(s["drift20"]["mean"], 0.02 / 3, places=6)
        self.assertAlmostEqual(s["drift20"]["cont"], 1 / 3, places=4)   # only the first drift continues its reaction
        self.assertAlmostEqual(s["drift20"]["signed_mean"], (0.02 - 0.01 - 0.01) / 3, places=6)


class Backtest(unittest.TestCase):
    def test_position_applies_next_day(self):
        # rising then falling series: the 50/200 cross is long only after the 50-day average passes the 200-day
        c = [100 + 0.1 * k for k in range(300)] + [130 - 0.3 * k for k in range(1, 200)]
        H = hist(c)
        out = QN.backtests(H, [0.0] * len(c))
        bh = out["rules"]["bh"]
        self.assertAlmostEqual(bh["total"], round(c[-1] / c[QN.BT_START - 1] - 1, 4), places=4)
        self.assertEqual(bh["trades"], 0)
        self.assertEqual(bh["turnover"], 0.0)
        sc = out["rules"]["sma_cross"]
        self.assertGreater(sc["total"], bh["total"])      # it exits during the decline
        self.assertFalse(sc["in_market"])
        self.assertEqual(sc["trades"], 1)

    def test_rsi_rule_no_lookahead(self):
        c = [100.0] * 260
        for k in range(230, 245):
            c[k] = c[k - 1] * 0.97      # sell-off pushes RSI below 30
        for k in range(245, 260):
            c[k] = c[k - 1] * 1.02
        H = hist(c)
        out = QN.backtests(H, [0.0] * len(c))
        rsi = QN.Q.rsi_wilder(c, 14)
        first = next(i for i in range(len(c)) if rsi[i] is not None and rsi[i] < 30)
        eq = out["rules"]["rsi_mr"]
        self.assertGreater(eq["trades"], 0)
        self.assertEqual(eq["last_trades"][0][0], H["d"][first])


class Regimes(unittest.TestCase):
    def test_trend_rule(self):
        c = [100 + 0.2 * k for k in range(400)]
        r = QN.regimes(hist(c))
        self.assertEqual(r["current"]["trend"], "up")
        c2 = [180 - 0.2 * k for k in range(400)]
        self.assertEqual(QN.regimes(hist(c2))["current"]["trend"], "down")


class Seasonality(unittest.TestCase):
    def test_turn_of_month_and_months(self):
        import datetime as dt
        d0 = dt.date(2020, 1, 1)
        days, c = [], []
        p = 100.0
        k = 0
        while len(days) < 800:
            d = d0 + dt.timedelta(days=k)
            k += 1
            if d.weekday() >= 5:
                continue
            days.append(d.isoformat())
            p *= 1.001
            c.append(p)
        H = hist(c, days=days)

        class Ctx:
            pre_hol_d, post_hol_d = set(), set()
        s = QN.seasonality(H, Ctx())
        self.assertEqual(len(s["month"]), 12)
        self.assertAlmostEqual(s["tom"]["mean"], 0.001, places=9)
        self.assertEqual(s["dow"][0]["n"] + s["dow"][1]["n"] + s["dow"][2]["n"] + s["dow"][3]["n"] + s["dow"][4]["n"], 799)


class Sources(unittest.TestCase):
    def _zip(self, name, text):
        b = io.BytesIO()
        with zipfile.ZipFile(b, "w") as z:
            z.writestr(name, text)
        return b.getvalue()

    def test_french_parse(self):
        ff5 = "This file was created by test.\n\n,Mkt-RF,SMB,HML,RMW,CMA,RF\n20070102,   1.00,  -0.50,   0.20,   0.10,  0.00,   0.02\n20070103,  -0.40,   0.10,   0.00,   0.30, -0.10,   0.02\n\n Annual Factors: January-December\n,Mkt-RF\n2007, 1.0\n"
        mom = "Momentum\n\n,Mom   \n20070102,   0.30\n20070103,  -0.20\n"
        t = S.parse_french(self._zip("a.csv", ff5), self._zip("b.csv", mom), start="20070101")
        self.assertEqual(t["d"], ["2007-01-02", "2007-01-03"])
        self.assertEqual(t["f"][0], [0.01, -0.005, 0.002, 0.001, 0.0, 0.003, 0.0002])

    def test_bls_and_fomc(self):
        html = '<a href="/news.release/archives/cpi_10142026.htm">Sep</a><a href="/news.release/archives/cpi_02202008.htm">x</a>'
        self.assertEqual(S.parse_bls_archive(html, S.BLS_PAGES["CPI"][1]), ["2008-02-20", "2026-10-14"])
        f = ('<p><a href="/newsevents/press/monetary/20080122b.htm">Statement</a></p>'
             '<strong>Statement:</strong><br> <a href="/x.pdf">PDF</a> | <a href="/newsevents/pressreleases/monetary20250129a.htm">HTML</a>'
             '<strong>Implementation Note:</strong><br><a href="/newsevents/pressreleases/monetary20250130a.htm">HTML</a>')
        out = S.parse_fomc(f)
        self.assertEqual(sorted(out), ["2008-01-22", "2025-01-29"])


class AIRead(unittest.TestCase):
    def test_validation_drops_unbacked_numbers(self):
        facts = {"vol60": ("21.9%", "60-day vol"), "beta252": ("0.61", "beta")}
        ok = QN.validate_read([{"text": "Volatility is 21.9% over 60 days.", "refs": ["vol60"]},
                               {"text": "Beta is 0.75.", "refs": ["beta252"]},
                               {"text": "No refs here.", "refs": []}], facts)
        self.assertEqual(len(ok), 1)
        self.assertIn("21.9%", ok[0]["text"])

    def test_quant_read_mocked(self):
        doc = {"as_of": "2026-11-04", "stats": {"vol": {"60": {"now": 0.219, "pct": 0.3}}, "dd": {"top": [{"depth": -0.5988, "peak": "2008-01-03", "trough": "2009-01-20"}]}, "perf": {"3y": {"sharpe": 0.88}}},
               "corr": {"bench": {"SPY": {"b252": 0.61, "c252": 0.32}}, "factors": None}, "events": {}, "regimes": {"current": {"trend": "up", "vol": "normal"}}}
        reply = {"choices": [{"message": {"content": json.dumps({"sentences": [{"text": "60-day volatility is 21.9%.", "refs": ["vol60"]}, {"text": "Beta to SPY is 9.99.", "refs": ["beta252"]}]})}}]}
        out = QN.quant_read(doc, api_key="test", request_fn=lambda url, body, hdr: json.dumps(reply))
        self.assertEqual([s["text"] for s in out["sentences"]], ["60-day volatility is 21.9%."])
        self.assertIsNone(QN.quant_read(doc, api_key=None) if not os.environ.get("FIREWORKS_API_KEY") else None)


if __name__ == "__main__":
    unittest.main()
