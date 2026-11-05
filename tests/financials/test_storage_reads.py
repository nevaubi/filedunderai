"""Authenticated reads request freshness without weakening readback or IR guards."""
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from etl import common, ir


class StorageReadPathTests(unittest.TestCase):
    def client(self):
        sb = common.Supabase.__new__(common.Supabase)
        sb.url, sb.key = "https://example.supabase.co", "key"
        return sb

    def assert_fresh_authenticated_reads(self, read, module, expected_path):
        current, cached = [b"before"], {}

        def cached_transport(url, **kwargs):
            return cached.setdefault(url, current[0])

        with patch.object(module, "request", side_effect=cached_transport) as req:
            self.assertEqual(read(), b"before")
            current[0] = b"after"
            self.assertEqual(read(), b"after")
        nonces = []
        for call in req.call_args_list:
            url = urlsplit(call.args[0])
            self.assertEqual(url.path, expected_path)
            query = parse_qs(url.query)
            self.assertEqual(set(query), {"cacheNonce"})
            self.assertEqual(len(query["cacheNonce"]), 1)
            nonces.append(query["cacheNonce"][0])
            headers = call.kwargs["headers"]
            self.assertEqual(headers["Cache-Control"], "no-cache, no-store")
            self.assertEqual(headers["Pragma"], "no-cache")
            self.assertEqual(headers["Authorization"], "Bearer key")
            self.assertEqual(headers["apikey"], "key")
            self.assertTrue(call.kwargs["raw"])
        self.assertEqual(len(nonces), 2)
        self.assertNotEqual(nonces[0], nonces[1])
        return req.call_args_list

    def test_private_reads_revalidate_after_an_object_changes(self):
        sb = self.client()
        self.assert_fresh_authenticated_reads(
            lambda: sb.get_object("public-data", "kpis/DIS.json"), common,
            "/storage/v1/object/authenticated/public-data/kpis/DIS.json")

    def test_ir_strict_reads_revalidate_after_an_object_changes(self):
        sb = self.client()
        calls = self.assert_fresh_authenticated_reads(
            lambda: ir._stored_object(sb, "raw", "ir/checkpoints/DIS.json"), ir,
            "/storage/v1/object/authenticated/raw/ir/checkpoints/DIS.json")
        for call in calls:
            self.assertEqual(call.kwargs["retries"], 1)
            self.assertEqual(call.kwargs["timeout"], 30)

    def test_bounded_ir_reads_keep_the_deadline_while_requesting_freshness(self):
        sb = ir.bounded_client(self.client(), time.monotonic() + 2)
        calls = self.assert_fresh_authenticated_reads(
            lambda: ir._stored_object(sb, "public-data", "ir/DIS.json"), ir,
            "/storage/v1/object/authenticated/public-data/ir/DIS.json")
        for call in calls:
            self.assertEqual(call.kwargs["retries"], 1)
            self.assertGreater(call.kwargs["timeout"], 0)
            self.assertLessEqual(call.kwargs["timeout"], 2)

    def test_public_reads_preserve_the_existing_url_and_headers(self):
        for bounded in (False, True):
            with self.subTest(bounded=bounded):
                sb = self.client()
                if bounded:
                    sb = ir.bounded_client(sb, time.monotonic() + 2)
                with patch.object(ir if bounded else common, "request", return_value=b"x") as req:
                    self.assertEqual(sb.get_object("public-data", "prices/AAPL.json", public=True), b"x")
                self.assertEqual(req.call_args.args[0], "https://example.supabase.co/storage/v1/object/public/public-data/prices/AAPL.json")
                self.assertEqual(req.call_args.kwargs["headers"], {"apikey": "key", "Authorization": "Bearer key"})

    def test_ir_read_errors_are_not_misclassified_as_missing(self):
        for bounded in (False, True):
            with self.subTest(bounded=bounded):
                sb = self.client()
                if bounded:
                    sb = ir.bounded_client(sb, time.monotonic() + 2)
                with patch.object(ir, "request", side_effect=common.HTTPError(400, '{"message":"bad credentials"}')):
                    with self.assertRaises(common.HTTPError):
                        ir._stored_object(sb, "raw", "ir/checkpoints/DIS.json")
                missing = '{"statusCode":"404","error":"not_found","message":"Object not found"}'
                with patch.object(ir, "request", side_effect=common.HTTPError(400, missing)):
                    self.assertIsNone(ir._stored_object(sb, "raw", "ir/checkpoints/DIS.json"))

    def test_non_supabase_store_keeps_its_two_argument_interface(self):
        calls = []

        def get_object(bucket, path):
            calls.append((bucket, path))
            return b"unchanged"

        sb = SimpleNamespace(get_object=get_object)
        with patch.object(ir, "request", side_effect=AssertionError("Unexpected transport")):
            self.assertEqual(ir._stored_object(sb, "raw", "ir/checkpoints/DIS.json"), b"unchanged")
        self.assertEqual(calls, [("raw", "ir/checkpoints/DIS.json")])


if __name__ == "__main__":
    unittest.main()
