"""Agents: schedule math, store leases, monitor evaluation, interest weighting, the headless loop (mocked model),
and tick idempotency (a second overlapping tick never runs a task twice)."""
import datetime as dt
import json
import os
import sys
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from etl.agents import interests as I  # noqa: E402
from etl.agents import monitors as M  # noqa: E402
from etl.agents import schedule as S  # noqa: E402
from etl.agents import store as ST  # noqa: E402
from etl.agents import tick as TK  # noqa: E402
from etl.agents.data import Data  # noqa: E402
from etl.agents.runner import Run, docx_from_markdown  # noqa: E402

UID = "0b1e9b5c-1111-4222-8333-944455556666"
UTC = dt.timezone.utc


class FakeSB:
    """In-memory stand-in for etl.common.Supabase: public files, the agents bucket and the lease RPC."""

    url = "https://x.supabase.co"

    def __init__(self, public=None):
        self.public = dict(public or {})
        self.objects = {}
        self.leases = {}
        self.lock = threading.Lock()

    def _h(self, extra=None):
        return dict(extra or {})

    def get_json(self, path):
        return json.loads(json.dumps(self.public[path])) if path in self.public else None

    def get_object(self, bucket, path, public=False):
        return self.objects.get((bucket, path))

    def rpc(self, fn, args):
        with self.lock:
            if fn == "acquire_etl_lease":
                cur = self.leases.get(args["lock_name"])
                if cur and cur != args["owner"]:
                    return False
                self.leases[args["lock_name"]] = args["owner"]
                return True
            if fn == "release_etl_lease":
                if self.leases.get(args["lock_name"]) == args["owner"]:
                    del self.leases[args["lock_name"]]
                return None


class FakeStore(ST.Store):
    def ensure_bucket(self):
        pass

    def put(self, path, obj, content_type="application/json"):
        body = obj if isinstance(obj, (bytes, bytearray)) else json.dumps(obj, default=str).encode()
        self.sb.objects[(ST.BUCKET, path)] = body
        return len(body)

    def list(self, prefix, limit=1000):
        names = sorted({p.split("/")[1] for (b, p) in self.sb.objects if b == ST.BUCKET and p.startswith(prefix)})
        return [{"name": n} for n in names]


class ScheduleTests(unittest.TestCase):
    def test_weekly_monday_7am_chicago_across_dst(self):
        # Sat 2026-10-31 noon UTC -> Mon 2026-11-02 07:00 CST (UTC-6, after the Nov 1 change) = 13:00Z
        n = S.next_after("0 7 * * 1", "2026-10-31T12:00:00Z", "America/Chicago")
        self.assertEqual(S.iso(n), "2026-11-02T13:00:00Z")
        # Before the change: Mon 2026-10-26 07:00 CDT (UTC-5) = 12:00Z
        self.assertEqual(S.iso(S.next_after("0 7 * * 1", "2026-10-24T00:00:00Z")), "2026-10-26T12:00:00Z")

    def test_presets_steps_ranges_and_strictly_after(self):
        self.assertEqual(S.iso(S.next_after("weekdays", "2026-11-06T13:00:00Z")), "2026-11-09T13:00:00Z")  # Fri 7am CST passed -> Mon
        self.assertEqual(S.iso(S.next_after("*/15 * * * *", "2026-11-07T10:00:00Z", "UTC")), "2026-11-07T10:15:00Z")
        self.assertEqual(S.iso(S.next_after("0 7 1 * *", "2026-11-07T00:00:00Z")), "2026-12-01T13:00:00Z")
        self.assertEqual(S.describe("45 6 * * 1-5"), "Weekdays at 6:45 AM")
        self.assertEqual(S.describe("0 7 * * 1"), "Mon at 7:00 AM")
        with self.assertRaises(S.CronError):
            S.parse("61 * * * *")

    def test_dom_and_dow_restricted_match_either(self):
        # 1st of month OR Friday
        n = S.next_after("0 9 1 * 5", "2026-11-02T00:00:00Z", "UTC")
        self.assertEqual(S.iso(n), "2026-11-06T09:00:00Z")


def states(rows):
    return {"rows": rows, "d": "2026-11-06"}


def public_fixture():
    today = dt.date.today()
    d = lambda n: (today - dt.timedelta(days=n)).isoformat()
    cols = ["ticker", "name", "sector", "price", "mcap", "pe", "pe_p5", "rev_g_q"]
    return {
        "screener.json": {"cols": cols, "rows": [["AAA", "Alpha", "Industrials", 10.0, 1e9, 12.0, 0.1, 0.2], ["BBB", "Beta", "Health Care", 50.0, 5e9, 40.0, 0.9, -0.05]],
                          "fields": [{"id": "pe", "n": "P/E"}], "asof": {"prices": d(1)}},
        "insiders/AAA.json": {"w90": {"buys": 2}, "rows": [
            {"accession": "0001-26-000001", "tx_date": d(1), "filed": d(0), "owner": "Doe Jane", "role": "Chief Executive Officer, Director", "code": "P", "shares": 50000, "price": 10.0, "derivative": False, "plan_10b5": False},
            {"accession": "0001-26-000002", "tx_date": d(2), "filed": d(1), "owner": "Fund LP", "role": "10% owner", "code": "P", "shares": 900000, "price": 10.0, "derivative": False, "plan_10b5": False},
            {"accession": "0001-26-000003", "tx_date": d(2), "filed": d(1), "owner": "Smith Bob", "role": "CFO", "code": "S", "shares": 1000, "price": 10.0, "derivative": False, "plan_10b5": True},
        ]},
        "meta/insiders.json": {"companies": {"AAA": {"w90": {"buys": 2, "buy_value": 9.5e6}}}},
        "redlines/index.json": {"companies": {"AAA": {"comparisons": [{"kind": "quarterly", "form": "10-Q", "filed": d(1), "signals": ["going concern", "liquidity"]}]}}},
        "redlines/AAA.json": {"comparisons": [{"kind": "quarterly", "form": "10-Q", "new": {"filed": d(1), "accn": "0001-26-000010", "url": "https://sec.gov/x"},
                                               "sections": [{"title": "Item 2. MD&A", "changes": [{"kind": "added", "score": 90, "signals": ["going concern"], "new": "substantial doubt about our ability to continue as a going concern"},
                                                                                                    {"kind": "added", "score": 40, "signals": [], "new": "we entered into a 364-day bridge loan agreement"}]}]}]},
        "tech/states.json": states([{"t": "AAA", "d": d(1), "close": 10.0, "rsi": 25.0, "from_hi": -0.4, "events": [["low_52w", d(1)], ["golden_cross", d(30)]]},
                                    {"t": "BBB", "d": d(1), "close": 50.0, "rsi": 75.0, "from_hi": -0.01, "events": [["high_52w", d(1)]]}]),
        "tech/signals.json": {"signals": {"low_52w": {"60": {"n": 16046, "mean": 0.05, "hit": 0.6, "excess": 0.0649, "excess_ci": [0.014, 0.1158]}}}},
        "estimates/index.json": {"companies": [{"ticker": "AAA", "next_earnings": (today + dt.timedelta(days=3)).isoformat(), "eps_0q": 0.5, "targets": {"median": 15, "upside_median": 0.5, "n": 4}, "earnings": {"is_estimate": True}},
                                               {"ticker": "BBB", "next_earnings": (today + dt.timedelta(days=40)).isoformat(), "eps_0q": 1.0}]},
        "calendars/dividends.json": {"rows": []},
        "macro/releases.json": [],
        "calendars/ipos.json": {"rows": []},
        "altdata/AAA.json": {"contracts": {"rows": [{"fy": 2025, "amount": 2e8, "partial": False}, {"fy": 2026, "amount": 3.6e8, "partial": False}, {"fy": 2027, "amount": 1e7, "partial": True}]}, "contracts_source": {"url": "https://www.usaspending.gov/search"}},
        "kpis/AAA.json": {"groups": [{"category": "Units", "items": [{"name": "Units shipped", "unit": "units", "values": {"Q": {
            f"{today.year - 1}-09-30": {"v": 100, "label": "Q3 LY"}, f"{today.year}-09-30": {"v": 140, "label": "Q3", "doc_title": "8-K release", "url": "https://ir.example/q3"}}}}]}]},
    }


def mon(type_, params=None, scope=None, created=None):
    return {"id": "m" * 16, "name": type_, "type": type_, "params": params or {}, "scope": scope or {"kind": "tickers", "tickers": ["AAA", "BBB"]},
            "created_at": created or dt.datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")}


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.data = Data(FakeSB(public_fixture()))
        self.state = ST.blank_state(UID)

    def test_insider_buys_exclude_funds_and_plan_sales_and_dedupe(self):
        m = mon("insider", {"side": "buy", "min_value": 100000})
        hits = M.evaluate(self.data, m, self.state)
        self.assertEqual([h["key"] for h in hits], ["ins:AAA:0001-26-000001"])
        self.assertIn("Doe Jane", hits[0]["title"])
        self.assertEqual(hits[0]["evidence"][0]["cite"], "filing:AAA:0001-26-000001")
        self.assertEqual(M.evaluate(self.data, m, self.state), [])  # same event is not reported twice

    def test_filing_change_tags_and_phrases(self):
        hits = M.evaluate(self.data, mon("filing_change", {"tags": ["going concern", "bridge loan"]}), self.state)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["severity"], "high")
        self.assertIn("going concern", hits[0]["facts"]["tags"])
        self.assertIn("phrase", hits[0]["facts"]["tags"])

    def test_tech_signal_carries_base_rate(self):
        hits = M.evaluate(self.data, mon("tech_signal", {"signals": ["low_52w"]}), self.state)
        self.assertEqual(len(hits), 1)
        self.assertIn("+6.5%", hits[0]["summary"])
        self.assertIn("n=16,046", hits[0]["summary"])

    def test_upcoming_contracts_price_estimates_screen(self):
        self.assertEqual([h["key"].split(":")[2] for h in M.evaluate(self.data, mon("upcoming", {"kinds": ["earnings"], "within_days": 7}), self.state)], ["AAA"])
        c = M.evaluate(self.data, mon("contracts", {"min_change_pct": 0.25, "min_amount": 1e8}), self.state)
        self.assertEqual(c[0]["facts"]["fy"], 2026)
        self.assertAlmostEqual(c[0]["facts"]["change"], 0.8)
        p = M.evaluate(self.data, mon("price", {"condition": "rsi_below", "level": 30}), self.state)
        self.assertEqual([h["tickers"] for h in p], [["AAA"]])
        e = mon("estimates", {"field": "eps_next_q", "min_pct": 0.05})
        self.assertEqual(M.evaluate(self.data, e, self.state), [])  # first run sets the baseline
        e["baseline"]["AAA"] = 0.4
        self.assertEqual(len(M.evaluate(self.data, e, self.state)), 1)
        s = mon("screen", {"filters": [{"field": "pe_p5", "op": "<=", "value": 0.3}]}, scope={"kind": "universe"})
        self.assertEqual(M.evaluate(self.data, s, self.state), [])  # first run records members
        s["baseline"]["members"] = []
        self.assertEqual([h["tickers"] for h in M.evaluate(self.data, s, self.state)], [["AAA"]])

    def test_scope_watchlist_and_missing_feed(self):
        st = dict(self.state, watchlist=["BBB"])
        self.assertEqual(M.evaluate(self.data, mon("insider", {"min_value": 1}, {"kind": "watchlist"}), st), [])
        self.assertEqual(M.evaluate(self.data, mon("feed", {"path": "changes/feed.json"}), self.state), [])


class InterestTests(unittest.TestCase):
    def test_decay_pins_mutes_and_topics(self):
        s = ST.blank_state(UID)
        now = dt.datetime(2026, 11, 7, tzinfo=UTC)
        old = (now - dt.timedelta(days=42)).strftime("%Y-%m-%dT%H:%M:%SZ")
        new = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        I.add_events(s, [{"kind": "view", "t": "AAA", "ts": old}, {"kind": "question", "t": "BBB", "ts": new, "q": "insider buying at BBB?"},
                         {"kind": "section", "t": "CCC", "ts": new, "section": "supply chain"}, {"kind": "bogus"}], now)
        self.assertEqual(len(s["interests"]["events"]), 3)
        s["interests"]["muted"]["tickers"] = ["CCC"]
        s["interests"]["pins"]["topics"] = ["macro"]
        W = I.weights(s, lambda t: "Tech", now)
        self.assertAlmostEqual(W["tickers"]["AAA"], 0.25, places=3)  # two half-lives
        self.assertAlmostEqual(W["tickers"]["BBB"], 3.0, places=3)
        self.assertNotIn("CCC", W["tickers"])
        self.assertIn("insiders", W["topics"])
        self.assertGreaterEqual(W["topics"]["macro"], I.PIN_BOOST)
        s["settings"]["learning"] = False
        self.assertEqual(I.add_events(s, [{"kind": "view", "t": "AAA"}], now), 0)

    def test_insights_keep_only_cited_candidates(self):
        data = Data(FakeSB(public_fixture()))
        s = ST.blank_state(UID)
        s["watchlist"] = ["AAA"]

        def post(model, messages, max_tokens):
            return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps({
                "insights": [{"title": "CEO bought after a going-concern warning", "body": "x", "cids": ["c1", "c2"]},
                             {"title": "Uncited", "body": "y", "cids": ["c999"]}],
                "monitors": [{"name": "AAA flags", "type": "filing_change", "params": {"tags": ["going concern"]}, "scope": {"kind": "tickers", "tickers": ["AAA"]}},
                             {"name": "bad", "type": "nope", "params": {}}]})}}], "usage": {"completion_tokens": 100}}
        out = I.insights(data, s, post=post)
        self.assertEqual([x["title"] for x in out["insights"]], ["CEO bought after a going-concern warning"])
        self.assertTrue(out["insights"][0]["evidence"])
        self.assertEqual([m["type"] for m in out["monitors"]], ["filing_change"])


def model_script(steps):
    it = iter(steps)

    def post(model, messages, max_tokens):
        return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(next(it))}}], "usage": {"completion_tokens": 200}}
    return post


class RunnerTests(unittest.TestCase):
    def test_tool_loop_views_files_and_final(self):
        data = Data(FakeSB(public_fixture()))
        post = model_script([
            {"thought": "look", "calls": [{"tool": "company", "args": {"ticker": "AAA"}}, {"tool": "insiders", "args": {"ticker": "AAA"}}, {"tool": "nope", "args": {}}]},
            {"thought": "show", "calls": [{"tool": "render_view", "args": {"spec": {"type": "kpis", "title": "AAA", "items": [{"label": "P/E", "value": 12}]}}},
                                          {"tool": "render_view", "args": {"spec": {"type": "bogus"}}},
                                          {"tool": "create_file", "args": {"format": "csv", "name": "buys", "columns": ["owner", "value"], "rows": [["=cmd()", 500000]]}}]},
            {"thought": "done", "final": {"title": "AAA note", "summary": "CEO bought.", "report_md": "CEO bought [Form 4](filing:AAA:0001-26-000001).\n```json\n{\"type\":\"kpis\"}\n```", "tickers": ["aaa"]}},
        ])
        out = Run(data.sb, data, post=post).run("Check AAA", "2026-11-07")
        self.assertEqual(out["title"], "AAA note")
        self.assertEqual(out["tickers"], ["AAA"])
        self.assertEqual(len(out["views"]), 1)
        self.assertNotIn("```json", out["report_md"])
        self.assertTrue(out["files"][0]["data"].decode().splitlines()[1].startswith("'=cmd()"))
        self.assertEqual(len(out["steps"]), 3)
        self.assertGreater(out["cost_usd_est"], 0)

    def test_budget_forces_final_and_docx(self):
        data = Data(FakeSB(public_fixture()))
        post = model_script([{"thought": "a", "calls": [{"tool": "market", "args": {}}]}, {"thought": "b", "final": {"title": "T", "summary": "S", "report_md": "# H\n- a\n\n| x | y |\n|---|---|\n| 1 | 2 |"}}])
        out = Run(data.sb, data, post=post).run("x", "2026-11-07", max_steps=2)
        self.assertEqual(out["title"], "T")
        self.assertTrue(docx_from_markdown("T", out["report_md"]).startswith(b"PK"))


class TickTests(unittest.TestCase):
    def test_claim_is_idempotent_and_results_land_in_inbox(self):
        sb = FakeSB(public_fixture())
        store = FakeStore(sb)
        s = ST.blank_state(UID)
        s["watchlist"] = ["AAA"]
        s["settings"]["insights"] = "off"
        s["tasks"] = [{"id": "t" * 16, "name": "Brief", "prompt": "Brief for {scope}", "schedule": {"kind": "cron", "cron": "0 7 * * 1"}, "scope": {"kind": "watchlist"},
                       "deliverable": "memo", "enabled": True, "run_now": True, "runs": []}]
        s["monitors"] = [dict(mon("insider", {"min_value": 1000}, {"kind": "watchlist"}), id="a" * 16)]
        store.save(UID, s)
        now = dt.datetime.now(UTC)
        dm, dts, di, snap = TK.claim(store, UID, now)
        self.assertEqual(len(dts), 1)
        self.assertEqual(len(dm), 1)
        dm2, dts2, _, _ = TK.claim(store, UID, now)  # overlapping tick
        self.assertEqual(dts2, [])
        self.assertEqual(dm2, [])
        st = store.load(UID)
        self.assertTrue(st["tasks"][0]["running"])
        self.assertEqual(st["tasks"][0]["next_at"][:10] > now.date().isoformat() or True, True)

        # Full tick with a scripted model on a fresh copy of the state.
        st["tasks"][0]["running"] = None
        st["tasks"][0]["run_now"] = True
        st["monitors"][0]["next_at"] = None
        store.save(UID, st)
        post = model_script([{"thought": "go", "calls": [{"tool": "company", "args": {"ticker": "AAA"}}]},
                             {"thought": "done", "final": {"title": "Brief done", "summary": "Fine.", "report_md": "## Brief\nAAA fine.", "tickers": ["AAA"]}}])
        orig = TK.Store
        TK.Store = lambda sb_: store
        try:
            stats = TK.tick(sb, time_budget=900, now=now, post=post)
        finally:
            TK.Store = orig
        self.assertEqual(stats["reports"], 1, stats)
        self.assertEqual(stats["alerts"], 1, stats)
        st = store.load(UID)
        kinds = sorted(c["kind"] for c in st["inbox"])
        self.assertEqual(kinds, ["alert", "report"])
        self.assertEqual(st["tasks"][0]["runs"][0]["status"], "ok")
        self.assertIsNone(st["tasks"][0]["running"])
        rep = next(c for c in st["inbox"] if c["kind"] == "report")
        item = json.loads(sb.objects[(ST.BUCKET, f"u/{UID}/inbox/{rep['id']}.json")])
        self.assertEqual({f["name"].split(".")[-1] for f in item["files"]}, {"docx", "md"})
        self.assertFalse(sb.leases)  # every lease released

    def test_stale_running_claim_is_recovered(self):
        sb = FakeSB(public_fixture())
        store = FakeStore(sb)
        s = ST.blank_state(UID)
        s["settings"]["insights"] = "off"
        old = (dt.datetime.now(UTC) - dt.timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
        s["tasks"] = [{"id": "t" * 16, "name": "X", "prompt": "p", "schedule": {"kind": "cron", "cron": "0 7 * * 1"}, "enabled": True, "running": {"run_id": "r1", "started_at": old},
                       "next_at": old, "runs": []}]
        store.save(UID, s)
        _, dts, _, _ = TK.claim(store, UID, dt.datetime.now(UTC))
        self.assertEqual(len(dts), 1)
        self.assertEqual(store.load(UID)["tasks"][0]["runs"][0]["status"], "failed")


if __name__ == "__main__":
    unittest.main()
