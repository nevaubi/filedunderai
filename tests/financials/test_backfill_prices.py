"""Price backfill integrity, ordering and additive-publication regressions."""
import copy
import datetime as dt
import gzip
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs

try:
    from etl import backfill_prices as bp
except ImportError:
    bp = None


def encoded(value):
    return json.dumps(value, separators=(",", ":"), allow_nan=False).encode()


def sha(body):
    return hashlib.sha256(body).hexdigest()


def fixture(root):
    """Three independently specified source rows, with the final close missing."""
    company = {"ticker": "OKTA", "cik": 1660134, "name": "Okta, Inc.", "currency": "USD"}
    raw_doc = {"chart": {"error": None, "result": [{
        "meta": {"symbol": "OKTA", "currency": "USD", "range": "10y", "dataGranularity": "1d",
                 "longName": "Okta, Inc.", "shortName": "Okta", "exchangeName": "NMS",
                 "regularMarketPrice": 12, "fiftyTwoWeekHigh": 15, "fiftyTwoWeekLow": 8,
                 "gmtoffset": -18000, "regularMarketTime": 1793802600,
                 "exchangeTimezoneName": "America/New_York", "instrumentType": "EQUITY"},
        "timestamp": [1793629800, 1793716200, 1793802600],
        "indicators": {"quote": [{"open": [10, 11, 12], "high": [12, 13, 14],
            "low": [9, 10, 11], "close": [11, 12, None], "volume": [100, 200, 300]}]},
        "events": {"dividends": {"1793629800": {"date": 1793629800, "amount": 0.25}},
                   "splits": {"1793716200": {"date": 1793716200, "numerator": 2, "denominator": 1}}}
    }]}}
    body = encoded(raw_doc)
    packed = gzip.compress(body, mtime=0)
    raw_path = root / "raw" / "OKTA.bin.gz"
    raw_path.parent.mkdir(parents=True)
    raw_path.write_bytes(packed)
    raw = {"path": str(raw_path), "body_sha256": sha(body), "body_bytes": len(body),
           "gzip_sha256": sha(packed), "gzip_bytes": len(packed), "http_status": 200,
           "retrieved_at": "2026-11-05T02:00:00+00:00", "headers": {}, "attempt": 1,
           "url": "https://query2.finance.yahoo.com/v8/finance/chart/OKTA?range=10y&interval=1d&includePrePost=false&events=div%2Csplits"}
    raw["requested_url"] = raw["url"]
    meta = {k: raw_doc["chart"]["result"][0]["meta"].get(k) for k in (
        "symbol", "longName", "shortName", "currency", "exchangeName", "regularMarketPrice",
        "fiftyTwoWeekHigh", "fiftyTwoWeekLow", "gmtoffset", "regularMarketTime", "exchangeTimezoneName", "instrumentType")}
    public = {"meta": meta, "d": [[1793629800, 10, 12, 9, 11, 100], [1793716200, 11, 13, 10, 12, 200]],
              "i": [], "dv": [["2026-11-02", 0.25]]}
    screen = {"ticker": "OKTA", "closes": {"2026-11-02": 11, "2026-11-03": 12},
              "splits": [["2026-11-03", 2]], "fetched": "2026-11-05", "source": "Yahoo Finance", "raw_body_sha256": sha(body)}
    derived = {}
    for kind, obj in (("public", public), ("screener", screen)):
        path = root / kind / "OKTA.json"
        path.parent.mkdir()
        data = encoded(obj)
        path.write_bytes(data)
        derived[kind] = {"path": str(path), "sha256": sha(data), "bytes": len(data)}
    missing = {f: {"array_absent": False, "array_length": 3, "null_count": int(f == "close"),
                  "nonfinite_or_nonnumeric_count": 0, "out_of_range_count": 0} for f in ("open", "high", "low", "close", "volume")}
    issues = {f: 0 for f in ("invalid_timestamp_count", "duplicate_timestamp_count", "duplicate_date_count",
        "nonincreasing_timestamp_count", "nonpositive_ohlc_count", "negative_volume_count", "inconsistent_ohlc_count",
        "invalid_dividend_count", "invalid_split_count")}
    manifest = {"schema_version": 1, "cohort_id": "additional-300-mdy-2026-11-03-r1", "cohort_sha256": "cohort-hash",
                **company, "expected_currency": "USD", "status": "ok", "errors": [],
                "source": {"provider": "Yahoo Finance"}, "url": raw["url"], "retrieved_at": raw["retrieved_at"],
                "raw": raw, "attempts": [raw], "derived": derived,
                "metadata": raw_doc["chart"]["result"][0]["meta"], "timestamp_count": 3, "daily_bar_count": 2,
                "valid_close_count": 2, "first_date": "2026-11-02", "latest_date": "2026-11-03",
                "first_complete_bar_date": "2026-11-02", "latest_complete_bar_date": "2026-11-03",
                "dividend_count": 1, "split_count": 1, "missing_fields": missing, "data_issues": issues,
                "omitted_public_bars": [{"index": 2, "timestamp": 1793802600, "date": "2026-11-04", "reasons": ["close_null"]}]}
    path = root / "manifests" / "OKTA.json"
    path.parent.mkdir()
    path.write_bytes(encoded(manifest))
    return company, manifest, public, screen


class MemoryPrices:
    """In-memory Storage/PostgREST boundary; real publication/lease logic still runs."""
    def __init__(self):
        self.objects = {}
        self.rows = {"companies": [{"ticker": "OKTA", "cik": 1660134}], "prices_daily": [{"symbol": "AAPL", "d": "2026-11-03", "o": 50, "h": 60, "l": 40, "c": 55, "v": 10}]}
        self.writes = []
        self.leases = {}
        self.corrupt_raw = False
        self.put_json("prices/SPY.json", {"meta": {"symbol": "SPY", "currency": "USD"}, "d": [[1793629800, 100, 110, 90, 100, 500], [1793716200, 101, 111, 91, 101, 600]]})
        self.put_json("meta/pricestats.json", {"AAPL": {"price": 55, "as_of": "2026-11-03"}})
        cache = {"dates": ["2026-11-02", "2026-11-03", "2026-11-04"], "s": {"AAPL": [54, 55, 56], "SPY": [100, 101, 102]},
                 "splits": {"AAPL": [["2020-08-31", 4]]}, "fetched": {"AAPL": "original"}, "extra": "keep"}
        self.objects[("raw", "screener/closes.json.gz")] = gzip.compress(encoded(cache), mtime=0)
        self.writes.clear()
    def rpc(self, fn, args):
        key, owner = args["lock_name"], args["owner"]
        if fn == "acquire_etl_lease":
            if key in self.leases and self.leases[key] != owner:
                return False
            self.leases[key] = owner
            return True
        if self.leases.get(key) == owner:
            self.leases.pop(key)
        return True
    def put_object(self, bucket, path, body, content_type="application/json", cache=None):
        lease = getattr(self, "_aggregate_lease", None)
        if lease:
            lease.check()
        self.objects[(bucket, path)] = body.encode() if isinstance(body, str) else bytes(body)
        self.writes.append((bucket, path))
    def get_object(self, bucket, path, public=False):
        body = self.objects.get((bucket, path))
        if body and self.corrupt_raw and bucket == "raw" and "yahoo" in path:
            return b"corrupt"
        return body
    def put_json(self, path, obj, **kwargs):
        self.put_object("public-data", path, encoded(obj), **kwargs)
    def get_json(self, path):
        b = self.get_object("public-data", path)
        return json.loads(b) if b else None
    def upsert(self, table, rows, on_conflict, **kwargs):
        rows = list(rows)
        fields = on_conflict.split(",")
        key = lambda row: tuple(row[k] for k in fields)
        merged = {key(row): row for row in self.rows.get(table, [])}
        merged.update({key(row): copy.deepcopy(row) for row in rows})
        self.rows[table] = list(merged.values())
        self.writes.append(("table", table))
    def select(self, table, query="select=*"):
        rows = self.rows.get(table, [])
        for key, values in parse_qs(query).items():
            v = values[0]
            if v.startswith("in.("):
                allowed = set(v[4:-1].split(","))
                rows = [r for r in rows if str(r.get(key)) in allowed]
            elif v.startswith("eq."):
                rows = [r for r in rows if str(r.get(key)) == v[3:]]
            elif v.startswith("gte."):
                rows = [r for r in rows if r.get(key, "") >= v[4:]]
            elif v.startswith("lte."):
                rows = [r for r in rows if r.get(key, "") <= v[4:]]
        return copy.deepcopy(rows)


class BackfillPricesTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(bp, "The additive price publisher is not implemented")

    def prepared(self, root):
        company, manifest, public, screen = fixture(root)
        item = bp.validate_company(root, company, manifest, "cohort-hash")
        return item, public, screen

    def test_missing_source_close_is_never_filled(self):
        with tempfile.TemporaryDirectory() as tmp:
            item, public, screen = self.prepared(Path(tmp))
            self.assertEqual(item["public"], public)
            self.assertEqual(item["screener"], screen)
            self.assertEqual(item["rows"][-1], {"symbol": "OKTA", "d": "2026-11-03", "o": 11, "h": 13, "l": 10, "c": 12, "v": 200})
            merged = bp.merge_close_cache(MemoryPrices().get_object("raw", "screener/closes.json.gz"), [item])
            self.assertEqual(merged["dates"], ["2026-11-02", "2026-11-03", "2026-11-04"])
            self.assertEqual(merged["s"]["OKTA"], [11, 12, None])
            self.assertEqual(merged["s"]["AAPL"], [54, 55, 56])
            self.assertEqual(merged["fetched"]["AAPL"], "original")
            self.assertEqual(merged["splits"]["AAPL"], [["2020-08-31", 4]])
            self.assertEqual(merged["extra"], "keep")

    def test_rehashed_derived_values_cannot_disagree_with_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            company, manifest, public, _ = fixture(root)
            public["d"][0][4] = 999
            meta = manifest["derived"]["public"]
            body = encoded(public)
            Path(meta["path"]).write_bytes(body)
            meta.update(sha256=sha(body), bytes=len(body))
            with self.assertRaisesRegex(ValueError, "correspond|source"):
                bp.validate_company(root, company, manifest, "cohort-hash")

    def test_archive_hash_symbol_currency_and_cik_are_enforced(self):
        for change in ("gzip", "symbol", "currency", "cik", "path"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                company, manifest, _, _ = fixture(root)
                if change == "gzip":
                    Path(manifest["raw"]["path"]).write_bytes(b"wrong")
                elif change == "cik":
                    manifest["cik"] = 320193
                elif change == "path":
                    manifest["raw"]["path"] = "/tmp/outside-scope.gz"
                else:
                    raw = manifest["raw"]
                    doc = json.loads(gzip.decompress(Path(raw["path"]).read_bytes()))
                    doc["chart"]["result"][0]["meta"][change] = "AAPL" if change == "symbol" else "EUR"
                    body = encoded(doc); packed = gzip.compress(body, mtime=0)
                    Path(raw["path"]).write_bytes(packed)
                    raw.update(body_sha256=sha(body), body_bytes=len(body), gzip_sha256=sha(packed), gzip_bytes=len(packed))
                with self.assertRaises(ValueError):
                    bp.validate_company(root, company, manifest, "cohort-hash")

    def test_old_universe_symbol_is_rejected_before_data_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            company, manifest, _, _ = fixture(root)
            company.update(ticker="AAPL", cik=320193)
            manifest.update(ticker="AAPL", cik=320193)
            with self.assertRaisesRegex(ValueError, "scope|additional|approved"):
                bp.validate_company(root, company, manifest, "cohort-hash")

    def test_corrupt_source_readback_blocks_public_prices_and_bars(self):
        with tempfile.TemporaryDirectory() as tmp:
            item, _, _ = self.prepared(Path(tmp))
            sb = MemoryPrices(); sb.corrupt_raw = True
            before = copy.deepcopy(sb.rows)
            with self.assertRaisesRegex(RuntimeError, "read.back"):
                bp.publish_prepared(sb, [item], "cohort-hash", workers=1)
            self.assertEqual(sb.rows, before)
            self.assertNotIn(("public-data", "prices/OKTA.json"), sb.objects)
            self.assertNotIn(("public-data", "meta/pricestats.json"), sb.writes)
            self.assertEqual(sb.leases, {})

    def test_global_document_id_owned_by_another_issuer_blocks_all_writes(self):
        for field in ("raw_path", "source_path"):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as tmp:
                item, _, _ = self.prepared(Path(tmp))
                sb = MemoryPrices()
                sb.rows["documents"] = [{"id": item[field], "ticker": "AAPL", "kind": "old-source"}]
                before = copy.deepcopy(sb.rows)
                with self.assertRaisesRegex(ValueError, "document|Document|issuer|ownership"):
                    bp.publish_prepared(sb, [item], "cohort-hash", workers=2)
                self.assertEqual(sb.writes, [])
                self.assertEqual(sb.rows, before)
                self.assertEqual(sb.leases, {})

    def test_mutated_prepared_rows_paths_or_bytes_cannot_escape_cohort_scope(self):
        for field in ("rows", "raw_path", "source_path", "public_bytes"):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as tmp:
                item, _, _ = self.prepared(Path(tmp))
                sb = MemoryPrices()
                before = copy.deepcopy(sb.rows)
                if field == "rows":
                    item[field][0]["symbol"] = "AAPL"
                elif field == "public_bytes":
                    item[field] = b"{}"
                else:
                    item[field] = "screener/closes.json.gz"
                with self.assertRaisesRegex(ValueError, "prepared|Prepared|scope|correspond"):
                    bp.publish_prepared(sb, [item], "cohort-hash", workers=1)
                self.assertEqual(sb.rows, before)
                self.assertEqual(sb.writes, [])
                self.assertEqual(sb.leases, {})

    def test_publish_adds_new_bars_and_respects_later_stats_part_precedence(self):
        from etl import jobs
        with tempfile.TemporaryDirectory() as tmp:
            item, public, _ = self.prepared(Path(tmp))
            sb = MemoryPrices()
            sb.put_json("meta/pricestats/p6.json", {"parts": 8, "stats": {"OKTA": {"price": 999, "as_of": "2026-11-03"}, "AAPL": {"price": 55, "as_of": "2026-11-03", "spark": [54, 55]}}})
            old_aapl = copy.deepcopy(sb.rows["prices_daily"])
            old_part = sb.get_json("meta/pricestats/p6.json")
            result = bp.publish_prepared(sb, [item], "cohort-hash", workers=2)
            self.assertEqual(result["published"], 1)
            self.assertEqual(result["daily_bars"], 2)
            self.assertEqual(sb.get_json("prices/OKTA.json"), public)
            self.assertEqual([r for r in sb.rows["prices_daily"] if r["symbol"] == "AAPL"], old_aapl)
            self.assertEqual(sb.get_json("meta/pricestats/p6.json"), old_part)
            self.assertEqual(sb.get_json("meta/pricestats.json")["AAPL"], {"price": 55, "as_of": "2026-11-03"})
            self.assertEqual(jobs.load_pricestats(sb)["OKTA"]["price"], 12)
            self.assertEqual(jobs.load_pricestats(sb)["AAPL"]["spark"], [54, 55])
            again = bp.publish_prepared(sb, [item], "cohort-hash", workers=2)
            self.assertEqual(again["published"], 1)
            self.assertEqual(len(sb.rows["prices_daily"]), 3)
            self.assertFalse(any(bucket == "public-data" and path in ("prices/AAPL.json", "prices/SPY.json") for bucket, path in sb.writes))

    def test_aggregate_read_error_or_busy_lease_cannot_start_publication(self):
        from etl.common import AggregateLease, LeaseBusy
        with tempfile.TemporaryDirectory() as tmp:
            item, _, _ = self.prepared(Path(tmp))
            sb = MemoryPrices()
            with AggregateLease(sb):
                with self.assertRaises(LeaseBusy):
                    bp.publish_prepared(sb, [item], "cohort-hash", workers=1)
            self.assertEqual(sb.writes, [])
            original = sb.get_object
            def fail(bucket, path, public=False):
                if path == "screener/closes.json.gz":
                    raise RuntimeError("read failed")
                return original(bucket, path, public)
            sb.get_object = fail
            with self.assertRaisesRegex(RuntimeError, "read failed"):
                bp.publish_prepared(sb, [item], "cohort-hash", workers=1)
            self.assertEqual(sb.writes, [])


if __name__ == "__main__":
    unittest.main()
