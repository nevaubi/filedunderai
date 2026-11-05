"""Authenticated storage reads must bypass the CDN copy of the bare object path."""
import unittest
from unittest.mock import patch

from etl import common, ir


class StorageReadPathTests(unittest.TestCase):
    def client(self):
        sb = common.Supabase.__new__(common.Supabase)
        sb.url, sb.key = "https://example.supabase.co", "key"
        return sb

    def test_private_reads_use_the_authenticated_endpoint(self):
        sb = self.client()
        with patch.object(common, "request", return_value=b"x") as req:
            sb.get_object("raw", "screener/closes.json.gz")
            sb.get_object("public-data", "meta/pricestats.json")
            sb.get_object("public-data", "prices/AAPL.json", public=True)
        urls = [c.args[0] for c in req.call_args_list]
        self.assertEqual(urls, [
            "https://example.supabase.co/storage/v1/object/authenticated/raw/screener/closes.json.gz",
            "https://example.supabase.co/storage/v1/object/authenticated/public-data/meta/pricestats.json",
            "https://example.supabase.co/storage/v1/object/public/public-data/prices/AAPL.json"])

    def test_ir_strict_reads_use_the_authenticated_endpoint(self):
        sb = self.client()
        with patch.object(ir, "request", return_value=b"x") as req:
            ir._stored_object(sb, "ir", "OKTA/manifest.json")
        self.assertEqual(req.call_args.args[0], "https://example.supabase.co/storage/v1/object/authenticated/ir/OKTA/manifest.json")


if __name__ == "__main__":
    unittest.main()
