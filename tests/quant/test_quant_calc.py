"""Hand-computed checks of etl/quant_calc.py. Run: python3 -m unittest discover -s tests/quant -p "test_*.py" """
import math
import os
import random
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)
from etl import quant_calc as Q  # noqa: E402


class Moments(unittest.TestCase):
    def test_moments_by_hand(self):
        # x = 1,2,3,4,10: mean 4, deviations -3,-2,-1,0,6; m2 = 50/5 = 10, m3 = 180/5 = 36, m4 = 1394/5 = 278.8
        m = Q.moments([1, 2, 3, 4, 10])
        self.assertAlmostEqual(m["mean"], 4)
        self.assertAlmostEqual(m["sd"], math.sqrt(50 / 4))
        self.assertAlmostEqual(m["skew"], 36 / 10 ** 1.5)
        self.assertAlmostEqual(m["kurt"], 278.8 / 100 - 3)

    def test_percentile_type7(self):
        s = [1, 2, 3, 4]
        self.assertAlmostEqual(Q.percentile(s, 25), 1.75)   # h = 3 * 0.25 = 0.75
        self.assertAlmostEqual(Q.percentile(s, 50), 2.5)
        self.assertAlmostEqual(Q.percentile(s, 100), 4)
        self.assertAlmostEqual(Q.percentile([5], 30), 5)

    def test_pct_rank(self):
        self.assertAlmostEqual(Q.pct_rank([1, 2, 3, 4], 3), 0.75)

    def test_corr_beta(self):
        self.assertAlmostEqual(Q.beta([1, 2, 3, 4], [2, 4, 6, 8]), 0.5)
        self.assertAlmostEqual(Q.corr([1, 2, 3, 4], [2, 4, 6, 8]), 1.0)
        self.assertAlmostEqual(Q.corr([1, 2, 3, 4], [8, 6, 4, 2]), -1.0)


class Distributions(unittest.TestCase):
    def test_pvalues_against_tables(self):
        self.assertAlmostEqual(Q.t_pvalue(2.228, 10), 0.05, places=3)     # t(0.975, 10) = 2.228
        self.assertAlmostEqual(Q.t_pvalue(1.96, 1e6), 0.05, places=3)
        self.assertAlmostEqual(Q.chi2_sf(18.307, 10), 0.05, places=3)     # chi2(0.95, 10) = 18.307
        self.assertAlmostEqual(Q.chi2_sf(3.841, 1), 0.05, places=3)
        self.assertAlmostEqual(Q.norm_sf2(1.959964), 0.05, places=5)

    def test_mean_test_and_welch(self):
        a = [1.0, 2.0, 3.0, 4.0, 5.0]
        mt = Q.mean_test(a)   # mean 3, sd sqrt(2.5), t = 3 / (sqrt(2.5)/sqrt(5)) = 4.2426
        self.assertAlmostEqual(mt["t"], 3 / (math.sqrt(2.5) / math.sqrt(5)))
        w = Q.welch([1.0, 2.0, 3.0, 4.0], [2.0, 4.0, 6.0, 8.0])
        # means 2.5 and 5, variances 5/3 and 20/3, se^2 = 5/12 + 20/12 = 25/12
        self.assertAlmostEqual(w["t"], -2.5 / math.sqrt(25 / 12))
        df = (25 / 12) ** 2 / ((5 / 12) ** 2 / 3 + (20 / 12) ** 2 / 3)
        self.assertAlmostEqual(w["df"], df)

    def test_var_cvar(self):
        r = [-0.10, -0.05] + [0.01] * 18
        v, c, n = Q.var_cvar(r, 0.95)
        # 5th percentile: h = 19 * 0.05 = 0.95 -> -0.10 + 0.95 * 0.05 = -0.0525; tail = [-0.10]
        self.assertAlmostEqual(v, 0.0525)
        self.assertAlmostEqual(c, 0.10)
        self.assertEqual(n, 20)

    def test_distribution_counts(self):
        d = Q.distribution([0.01, -0.02, 0.03, None, 0.0])
        self.assertEqual(d["n"], 4)
        self.assertAlmostEqual(d["up"], 0.5)
        self.assertAlmostEqual(d["min"], -0.02)


class Series(unittest.TestCase):
    def test_total_return_index(self):
        tr = Q.total_return_index([10, 10, 11], ["a", "b", "c"], [["b", 0.5]])
        self.assertAlmostEqual(tr[1], 1.05)          # (10 + 0.5) / 10
        self.assertAlmostEqual(tr[2], 1.05 * 1.1)
        self.assertEqual(Q.total_return_index([10, -1, 5], ["a", "b", "c"], []), [1.0, 1.0, 1.0])

    def test_rolling_matches_direct(self):
        rnd = random.Random(3)
        a = [rnd.gauss(0, 1) for _ in range(120)]
        b = [x * 0.7 + rnd.gauss(0, 0.5) for x in a]
        rc, rb = Q.rolling_corr_beta(a, b, 30)
        for i in (29, 64, 119):
            self.assertAlmostEqual(rc[i], Q.corr(a[i - 29:i + 1], b[i - 29:i + 1]), places=9)
            self.assertAlmostEqual(rb[i], Q.beta(a[i - 29:i + 1], b[i - 29:i + 1]), places=9)
        sd = Q.rolling_sd(a, 20)
        self.assertAlmostEqual(sd[50], Q.sd(a[31:51]), places=9)

    def test_rolling_with_gaps(self):
        a = [1.0, 2.0, None, 4.0, 5.0, 6.0]
        b = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
        rc, rb = Q.rolling_corr_beta(a, b, 4, min_frac=0.75)
        self.assertAlmostEqual(rb[3], 1.0)    # pairs (1,1),(2,2),(4,4)
        self.assertIsNone(rc[1])

    def test_drawdowns(self):
        tr = [1, 1.2, 0.9, 1.0, 1.3, 1.1]
        days = list("abcdef")
        dd, top, cur = Q.drawdowns(tr, days)
        self.assertAlmostEqual(top[0]["depth"], 0.9 / 1.2 - 1)
        self.assertEqual((top[0]["peak"], top[0]["trough"], top[0]["recovered"]), ("b", "c", "e"))
        self.assertEqual((top[0]["to_trough"], top[0]["to_recover"]), (1, 2))
        self.assertAlmostEqual(top[1]["depth"], 1.1 / 1.3 - 1)
        self.assertIsNone(top[1]["recovered"])
        self.assertAlmostEqual(cur["current"], 1.1 / 1.3 - 1)
        self.assertEqual(cur["peak_date"], "e")

    def test_capture_geometric(self):
        c = Q.capture([0.02, -0.01, 0.03, -0.02], [0.01, -0.02, 0.02, -0.01])
        up = (math.sqrt(1.02 * 1.03) - 1) / (math.sqrt(1.01 * 1.02) - 1)
        dn = (math.sqrt(0.99 * 0.98) - 1) / (math.sqrt(0.98 * 0.99) - 1)
        self.assertAlmostEqual(c["up"], up)
        self.assertAlmostEqual(c["down"], dn)
        self.assertEqual((c["n_up"], c["n_down"]), (2, 2))

    def test_sharpe_sortino(self):
        r = [0.01, -0.01, 0.02, 0.0] * 5
        s = Q.sharpe_sortino(r, [0.0] * 20)
        # mean 0.005; sum of squared deviations 5 x 0.0005 = 0.0025, sd = sqrt(0.0025/19)
        self.assertAlmostEqual(s["sharpe"], 0.005 / math.sqrt(0.0025 / 19) * math.sqrt(252))
        # downside deviation sqrt(5 x 0.0001 / 20) = 0.005 -> Sortino = 0.005 x 252 / (0.005 x sqrt(252))
        self.assertAlmostEqual(s["sortino"], math.sqrt(252))

    def test_month_and_week_returns(self):
        days = ["2026-01-29", "2026-01-30", "2026-02-02", "2026-02-27", "2026-03-02"]
        tr = [1.0, 1.1, 1.2, 1.21, 1.3]
        self.assertEqual([k for k, _ in Q.month_end_returns(tr, days)], ["2026-02"])
        self.assertAlmostEqual(Q.month_end_returns(tr, days)[0][1], 1.21 / 1.1 - 1)
        w = Q.week_returns([1.0, 1.1, 1.2, 1.3], ["2026-01-02", "2026-01-09", "2026-01-12", "2026-01-16"])
        self.assertAlmostEqual(w[0], 0.1)
        self.assertAlmostEqual(w[1], 1.3 / 1.1 - 1)

    def test_rsi_seed(self):
        c = [1, 2, 3, 2, 3, 4, 5, 4, 5, 6, 7, 6, 7, 8, 9, 8]
        r = Q.rsi_wilder(c, 14)
        # first 14 changes: +1 +1 -1 +1 +1 +1 -1 +1 +1 +1 -1 +1 +1 +1 -> 11 gains, 3 losses; RS = 11/3 -> RSI = 78.57
        self.assertAlmostEqual(r[14], 100 - 100 / (1 + 11 / 3))
        ag, al = (11 / 14 * 13 + 0) / 14, (3 / 14 * 13 + 1) / 14
        self.assertAlmostEqual(r[15], 100 - 100 / (1 + ag / al))


class Dependence(unittest.TestCase):
    def test_acf_alternating(self):
        x = [1.0, -1.0] * 50
        a = Q.acf(x, 3)
        self.assertAlmostEqual(a["rho"][0], -99 / 100)
        self.assertAlmostEqual(a["rho"][1], 98 / 100)
        q = 100 * 102 * ((0.99 ** 2) / 99 + (0.98 ** 2) / 98 + (0.97 ** 2) / 97)
        self.assertAlmostEqual(a["lb_q"], q)

    def test_variance_ratio_hand(self):
        x = [0.01, -0.02, 0.03, 0.0, 0.01, -0.01, 0.02, 0.0]
        out = Q.variance_ratio(x, 2)
        n = 8
        mu = sum(x) / n
        d = [v - mu for v in x]
        s2a = sum(v * v for v in d) / (n - 1)
        s2c = sum((d[t] + d[t - 1]) ** 2 for t in range(1, n)) / (2 * (n - 1) * (1 - 2 / n))
        self.assertAlmostEqual(out["vr"], s2c / s2a)
        ss = sum(v * v for v in d)
        delta = n * sum(d[t] ** 2 * d[t - 1] ** 2 for t in range(1, n)) / ss ** 2
        self.assertAlmostEqual(out["z"], math.sqrt(n) * (s2c / s2a - 1) / math.sqrt(delta))

    def test_variance_ratio_random_walk(self):
        rnd = random.Random(11)
        x = [rnd.gauss(0, 0.01) for _ in range(3000)]
        out = Q.variance_ratio(x, 5)
        self.assertLess(abs(out["vr"] - 1), 0.12)
        self.assertLess(abs(out["z"]), 3)
        mr = Q.variance_ratio([v - 0.8 * w for v, w in zip(x[1:], x)], 2)
        self.assertLess(mr["vr"], 0.7)
        self.assertLess(mr["z"], -10)

    def test_hurst_iid(self):
        rnd = random.Random(5)
        x = [rnd.gauss(0, 1) for _ in range(4000)]
        h = Q.hurst_rs(x)
        self.assertGreater(h["h"], 0.45)
        self.assertLess(h["h"], 0.65)


class Regression(unittest.TestCase):
    def test_exact_fit(self):
        X = [(1, 2), (2, 1), (3, 5), (4, 3), (5, 8), (6, 1)]
        y = [1 + 2 * a - 3 * b for a, b in X]
        o = Q.ols(y, X)
        for got, want in zip(o["coef"], [1, 2, -3]):
            self.assertAlmostEqual(got, want, places=9)
        self.assertAlmostEqual(o["r2"], 1.0)

    def test_simple_regression_and_newey_west(self):
        x = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0]
        y = [1.2, 1.9, 3.4, 3.8, 5.3, 5.7, 7.4, 7.9]
        o = Q.ols(y, [(v,) for v in x], nw_lags=1)
        n = len(x)
        mx, my = sum(x) / n, sum(y) / n
        sxx = sum((a - mx) ** 2 for a in x)
        b = sum((a - mx) * (c - my) for a, c in zip(x, y)) / sxx
        a0 = my - b * mx
        self.assertAlmostEqual(o["coef"][1], b)
        self.assertAlmostEqual(o["coef"][0], a0)
        e = [c - a0 - b * a for a, c in zip(x, y)]
        s2 = sum(v * v for v in e) / (n - 2)
        self.assertAlmostEqual(o["se"][1], math.sqrt(s2 / sxx))
        # Newey-West slope variance with one lag, written out for a single centred regressor:
        # Var(b) = [sum u_t^2 + 2 * 0.5 * sum u_t u_{t-1}] / sxx^2 with u_t = (x_t - mx) e_t  (exact for the slope
        # because the intercept column is orthogonal to the centred regressor)
        u = [(a - mx) * v for a, v in zip(x, e)]
        S = sum(v * v for v in u) + 2 * 0.5 * sum(u[t] * u[t - 1] for t in range(1, n))
        self.assertAlmostEqual(o["nw_se"][1], math.sqrt(S / sxx ** 2))

    def test_rolling_ols_matches_full(self):
        rnd = random.Random(2)
        X = [(rnd.gauss(0, 1), rnd.gauss(0, 1)) for _ in range(80)]
        y = [0.1 + 0.5 * a - 0.2 * b + rnd.gauss(0, 0.1) for a, b in X]
        out = Q.rolling_ols(y, X, 40, 10)
        i, c, r2 = out[-1]
        full = Q.ols(y[i - 39:i + 1], X[i - 39:i + 1])
        for got, want in zip(c, full["coef"]):
            self.assertAlmostEqual(got, want, places=8)
        self.assertAlmostEqual(r2, full["r2"], places=8)


class Calendars(unittest.TestCase):
    def test_eastern(self):
        self.assertEqual(Q.to_eastern("2026-11-02T21:30:23.000Z"), ("2026-11-02", 16 * 60 + 30))   # EST (DST ended Nov 1)
        self.assertEqual(Q.to_eastern("2026-07-30T20:30:28.000Z"), ("2026-07-30", 16 * 60 + 30))   # EDT
        self.assertEqual(Q.to_eastern("2026-03-08T06:59:00.000Z"), ("2026-03-08", 1 * 60 + 59))    # still EST
        self.assertEqual(Q.to_eastern("2026-03-08T07:00:00.000Z"), ("2026-03-08", 3 * 60))         # EDT from 02:00 local
        self.assertEqual(Q.to_eastern("2026-01-15T03:10:00.000Z"), ("2026-01-14", 22 * 60 + 10))   # previous ET day

    def test_holidays(self):
        days = ["2026-11-23", "2026-11-24", "2026-11-25", "2026-11-27", "2026-11-30"]   # Thanksgiving 26th closed
        pre, post = Q.holidays_from_calendar(days)
        self.assertEqual(pre, {2})
        self.assertEqual(post, {3})


if __name__ == "__main__":
    unittest.main()
