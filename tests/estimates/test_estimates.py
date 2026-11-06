import io
import json
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)

from etl import estimates as E  # noqa: E402
from etl import estimates_ai as AI  # noqa: E402
from etl import estimates_logos as L  # noqa: E402
from etl import estimates_sources as S  # noqa: E402

FIX = json.load(open(os.path.join(os.path.dirname(__file__), "fixtures", "aapl.json")))


def company():
    return {"cik": 320193, "earnings8k": [{"filed": "2026-07-30", "accn": "0000320193-26-000018", "url": "https://sec.example/8k-q3"},
                                          {"filed": "2026-11-02", "accn": "0000320193-26-000024", "url": "https://sec.example/8k-q4"}],
            "statements": {"income": [
                {"label": "EPS diluted", "quarterly": [{"v": 2.02, "end": "2026-06-27", "accn": "0000320193-26-000020", "form": "10-Q", "filed": "2026-07-31"}]},
                {"label": "Revenue", "quarterly": [{"v": 114477000000, "end": "2026-09-26", "accn": "0000320193-26-000026", "form": "10-K", "filed": "2026-11-03"}]}]}}


def prices():
    import datetime as dt
    days = ["2026-07-29", "2026-07-30", "2026-07-31", "2026-11-02", "2026-11-03"]
    closes = [210.0, 212.0, 216.24, 338.32, 331.9]
    return {"d": [[int(dt.datetime.fromisoformat(d + "T20:00:00+00:00").timestamp()), 0, 0, 0, c, 0] for d, c in zip(days, closes)]}


class YahooParsing(unittest.TestCase):
    def test_period_ends_are_reanchored_and_flagged(self):
        y = E.parse_yahoo_earnings(FIX["yahoo"])
        by = {p["code"]: p for p in y["periods"]}
        # the provider listed the already reported Sep-2026 quarter as the current quarter
        self.assertEqual(by["0q"]["end_provider"], "2026-09-30")
        self.assertEqual(by["0q"]["end"], "2026-12-31")
        self.assertIn("already reported", by["0q"]["end_fixed"])
        # next fiscal year repeated the current fiscal year's end
        self.assertEqual(by["+1y"]["end"], "2028-09-30")
        self.assertEqual(by["0y"]["end"], "2027-09-30")
        self.assertNotIn("end_fixed", by["0y"])

    def test_values_are_exact_and_history_deduplicated(self):
        y = E.parse_yahoo_earnings(FIX["yahoo"])
        q0 = next(p for p in y["periods"] if p["code"] == "0q")
        self.assertEqual(q0["eps"]["avg"], 2.90354)
        self.assertEqual(q0["eps"]["n"], 26)
        self.assertEqual(q0["revenue"]["high"], 159565000000)
        ends = [h["quarter_end"] for h in y["history"]]
        self.assertEqual(ends, sorted(set(ends)))  # Yahoo repeats "-4q"; quarters are keyed by date
        self.assertEqual(y["next_earnings"]["date"], "2027-01-28")
        self.assertTrue(y["next_earnings"]["is_estimate"])

    def test_zero_analyst_ranges_become_missing(self):
        d = json.loads(json.dumps(FIX["yahoo"]))
        d["trend"][0]["revenue_estimate"] = {"average": 0, "high": 0, "low": 0, "number_of_analysts": 0, "year_ago": None}
        y = E.parse_yahoo_earnings(d)
        q0 = next(p for p in y["periods"] if p["code"] == "0q")
        self.assertIsNone(q0["revenue"]["avg"])
        self.assertIsNone(q0["revenue"]["n"])


class Revisions(unittest.TestCase):
    def snap(self, day, eps, end="2027-09-30"):
        return {"as_of": day, "at": day + "T10:00:00Z", "p": {"0y": {"end": end, "eps": eps, "rev": 100.0, "eps_n": 30}}}

    def test_revision_needs_same_period_and_old_enough_snapshot(self):
        snaps = [self.snap("2026-08-01", 9.0), self.snap("2026-10-06", 9.2), self.snap("2026-11-05", 9.4)]
        r = E.revisions(snaps)["0y"]
        self.assertIsNone(r["7"])                     # nothing 7 days back
        self.assertEqual(r["30"]["from_date"], "2026-10-06")
        self.assertAlmostEqual(r["30"]["eps_pct"], (9.4 - 9.2) / 9.2)
        self.assertEqual(r["90"]["from_date"], "2026-08-01")
        rolled = [self.snap("2026-10-06", 9.2, end="2026-09-30"), self.snap("2026-11-05", 9.4)]
        self.assertIsNone(E.revisions(rolled)["0y"]["30"])   # fiscal year rolled: not a revision

    def test_sign_safe_change_and_snapshot_merge(self):
        self.assertAlmostEqual(E.pct_change(-0.5, -1.0), 0.5)
        self.assertIsNone(E.pct_change(1, 0))
        merged = E.merge_snapshots([self.snap("2026-11-05", 1.0)], self.snap("2026-11-05", 2.0))
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["p"]["0y"]["eps"], 2.0)


class Actions(unittest.TestCase):
    def test_parse_keeps_printed_targets_and_names(self):
        a = [E.parse_rating(r) for r in FIX["ratings"]]
        first = a[0]
        raw = FIX["ratings"][0]
        self.assertEqual(first["firm"], raw["analyst"])
        self.assertEqual(first["analyst"], raw["analyst_name"])
        self.assertEqual(first["pt_current_printed"], raw["pt_current"])
        self.assertEqual(first["pt_current"], float(raw["pt_current"]))
        kinds = {E.action_kind(x) for x in a}
        self.assertTrue({"upgrade", "downgrade"} <= kinds)

    def test_merge_dedupes_and_applies_removals(self):
        a = [E.parse_rating(r) for r in FIX["ratings"]]
        newer = dict(a[0], rating_current="Hold", updated=(a[0]["updated"] or 0) + 10)
        out = E.merge_actions(a, [newer], removed_ids={a[1]["id"]})
        self.assertEqual(len(out), len(a) - 1)
        self.assertEqual(next(x for x in out if x["id"] == a[0]["id"])["rating_current"], "Hold")
        self.assertEqual(out, sorted(out, key=lambda x: (x["date"], x["time"] or "", x["id"]), reverse=True))

    def test_latest_per_firm_and_coverage_drops(self):
        base = {"pt_prior": None, "adj_pt_current": None, "rating_current": "Buy"}
        acts = [dict(base, id="3", date="2026-10-01", firm="F1", firm_id="f1", action="Terminates Coverage On", pt_current=None),
                dict(base, id="2", date="2026-09-01", firm="F1", firm_id="f1", action="Maintains", pt_current=100.0),
                dict(base, id="1", date="2026-08-01", firm="F2", firm_id="f2", action="Maintains", pt_current=120.0, rating_current="Underweight")]
        t = E.target_stats(acts, "2025-11-05", price=110.0)
        self.assertEqual([r["firm"] for r in t["rows"]], ["F2"])        # F1 ended coverage after its last target
        d = E.rating_distribution(acts, "2025-11-05")
        self.assertEqual(d["n"], 1)                                       # F1 dropped coverage
        self.assertEqual(d["counts"]["sell"], 1)
        self.assertAlmostEqual(t["stats"]["upside_mean"], 120 / 110 - 1)

    def test_rating_buckets(self):
        for r, b in (("Overweight", "buy"), ("Equal-Weight", "hold"), ("Underperform", "sell"), ("Sector Perform", "hold"), ("Speculative Buy", "buy"), ("", None)):
            self.assertEqual(E.rating_bucket(r), b, r)


class Surprises(unittest.TestCase):
    def test_provider_basis_with_gaap_alongside(self):
        y = E.parse_yahoo_earnings(FIX["yahoo"])
        rows = E.surprises(y, company(), E.bars_from_prices(prices()))
        q2 = next(r for r in rows if r["quarter_end"] == "2026-06-30")
        self.assertEqual(q2["basis"], "provider")
        self.assertEqual(q2["eps_actual"], 2.02)           # provider figure
        self.assertEqual(q2["gaap_eps"]["v"], 2.02)        # SEC figure kept separately
        self.assertEqual(q2["report"]["filed"], "2026-07-30")
        self.assertAlmostEqual(q2["reaction"]["r1"], 216.24 / 212.0 - 1)
        q3 = next(r for r in rows if r["quarter_end"] == "2026-09-30")
        self.assertIsNone(q3["gaap_eps"])                  # fiscal Q4 has no quarterly EPS in filings: stays missing


class Guidance(unittest.TestCase):
    def guidance(self, lo, hi, typ="Q", end="2026-12-27"):
        return {"generated": "x", "docs": {"d1": {"id": "d1", "url": "https://sec.example/ex99.htm"}}, "series": [{"key": "revenue|gaap|level|USD|", "metric": "revenue", "label": "Revenue", "unit": "USD",
                "basis": "gaap", "periods": [{"label": "Q1 FY27", "type": typ, "end": end, "points": [{"id": "g1", "d": "2026-10-30", "lo": lo, "hi": hi, "mid": (lo + hi) / 2, "doc": "d1", "pg": 1,
                                                                                                    "q": "revenue between $138 billion and $142 billion"}]}]}]}

    def test_matching_period_and_scale_check(self):
        cons = E.parse_yahoo_earnings(FIX["yahoo"])
        g = E.guidance_section(self.guidance(138e9, 142e9), cons)
        vc = g["vs_consensus"]
        self.assertEqual(len(vc), 1)
        self.assertEqual(vc[0]["code"], "0q")
        self.assertTrue(vc[0]["scale_check"])
        self.assertEqual(vc[0]["position"], "above")       # 155.6B consensus vs 138-142B guide
        self.assertEqual(g["docs"]["d1"]["url"], "https://sec.example/ex99.htm")
        # an "annual" label on a quarterly-sized figure is not matched with a quarter, and fails the scale check vs the year
        g2 = E.guidance_section(self.guidance(138e9, 142e9, typ="FY", end="2027-09-25"), cons)
        self.assertTrue(all(x["scale_check"] is False for x in g2["vs_consensus"]))


class StreetView(unittest.TestCase):
    def bundle(self):
        y = E.parse_yahoo_earnings(FIX["yahoo"])
        acts = [E.parse_rating(r) for r in FIX["ratings"]]
        return E.build_bundle("AAPL", name="Apple", yahoo=y, actions=acts, company=company(), prices=prices(), today="2026-11-05")

    def test_refs_and_numbers_are_validated(self):
        b = self.bundle()
        refs = AI.context(b)
        aid = "A:" + b["actions"][0]["id"]
        fake = {"headline": "x", "points": [{"text": "Current-quarter consensus EPS is $2.90 from 26 analysts.", "refs": ["C:0q"]},
                                            {"text": "Consensus EPS is $9.99.", "refs": ["C:0q"]},
                                            {"text": "Made up.", "refs": ["Z:1"]},
                                            {"text": f"{b['actions'][0]['firm']} set a target.", "refs": [aid]}]}
        sv = AI.street_view(b, "k", completer=lambda prompt, key: (fake, "kimi-k3"))
        self.assertEqual(sv["label"], "AI-generated")
        self.assertEqual(len(sv["points"]), 3)                # the point citing a non-existent ref is dropped
        self.assertTrue(sv["points"][0]["verified"])
        self.assertFalse(sv["points"][1]["verified"])
        self.assertIn("9.99", sv["points"][1]["unsupported_numbers"])
        self.assertTrue(all(r in refs for p in sv["points"] for r in p["refs"]))


class FirecrawlClient(unittest.TestCase):
    def test_batches_retries_and_errors(self):
        calls = [S.yahoo_call("AAPL"), S.yahoo_call("MSFT")]
        envelope = {"success": True, "data": {"creditsCost": 10, "alexandria": [{"data": FIX["yahoo"]}, {"error": {"code": "request_timeout"}}]}}
        seq = [S.HTTPError(503, "busy"), json.dumps(envelope)]

        def fake(url, **kw):
            self.assertEqual(url, S.API)
            self.assertEqual(kw["data"]["alexandria"], calls)
            self.assertTrue(kw["headers"]["Authorization"].startswith("Bearer "))
            x = seq.pop(0)
            if isinstance(x, Exception):
                raise x
            return x
        fc = S.Firecrawl(key="k", sleep=lambda s: None)
        with mock.patch.object(S, "request", side_effect=fake):
            out, env = fc.run(calls)
        self.assertEqual(out[0]["symbol"], "AAPL")
        self.assertIn("_error", out[1])
        self.assertEqual(fc.credits, 10)
        with self.assertRaises(ValueError):
            fc.run([S.yahoo_call("X")] * 11)

    def test_dry_run_makes_no_requests(self):
        fc = S.Firecrawl(key=None, dry_run=True)
        with mock.patch.object(S, "request", side_effect=AssertionError("no network in dry run")):
            out, env = fc.run([S.yahoo_call("AAPL")])
        self.assertEqual(out[0]["_error"]["code"], "dry_run")

    def test_rotation_prioritizes_reporters_then_stalest(self):
        st = {"yahoo": {"AAPL": "2026-11-05", "MSFT": "2026-10-01", "NVDA": "2026-11-03"}}
        due = S.consensus_due(st, {"NVDA": "2026-11-06"}, "2026-11-05", max_n=5000)
        self.assertEqual(due[0], "NVDA")            # reports tomorrow: refreshed daily
        self.assertNotIn("AAPL", due)               # already refreshed today
        self.assertIn("MSFT", due)                  # stale beyond the rotation
        self.assertEqual(S.ratings_call(["BRK-B"], updated=5)["options"]["parameters[tickers]"], "BRK.B")


class Logos(unittest.TestCase):
    def png(self, w, h, uniform=False):
        from PIL import Image, ImageDraw
        im = Image.new("RGBA", (w, h), (255, 255, 255, 255))
        if not uniform:
            ImageDraw.Draw(im).rectangle([w // 4, h // 4, 3 * w // 4, 3 * h // 4], fill=(200, 0, 0, 255))
        b = io.BytesIO()
        im.save(b, "PNG")
        return b.getvalue()

    def test_validation(self):
        out, info = L.validate_png(self.png(128, 128))
        self.assertTrue(out.startswith(b"\x89PNG"))
        self.assertEqual(info["width"], 128)
        self.assertIsNone(L.validate_png(self.png(16, 16))[0])
        self.assertIsNone(L.validate_png(self.png(400, 40))[0])        # wordmark
        self.assertIsNone(L.validate_png(self.png(64, 64, uniform=True))[0])
        self.assertIsNone(L.validate_png(b"<!doctype html><html>" + b" " * 200)[0])

    def test_candidates(self):
        html = '<link rel="icon" href="/f.ico"><link rel="apple-touch-icon" sizes="180x180" href="/apple.png">'
        c = L.candidates_from_html(html, "https://www.x.com/")
        self.assertEqual(c[0], ("apple-touch-icon", "https://www.x.com/apple.png"))
        b = L.candidates_from_branding({"images": {"favicon": "/i.png", "logo": "data:image/svg+xml;utf8,x"}}, "https://www.x.com/")
        self.assertEqual(b, [("favicon", "https://www.x.com/i.png")])


if __name__ == "__main__":
    unittest.main()
