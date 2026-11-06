"""api/export.py: origin check, body checks, and a successful build with stubbed data."""
import importlib.util
import io
import json
import os
import unittest
from email.message import Message

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
spec = importlib.util.spec_from_file_location("export_api", os.path.join(ROOT, "api", "export.py"))
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)

from test_matrix_export import INDEX, SERIES, TAX  # noqa: E402


class Fake(api.handler):
    def __init__(self, headers, body=b""):
        self.headers = Message()
        for k, v in headers.items():
            self.headers[k] = v
        self.rfile = io.BytesIO(body)
        self.wfile = io.BytesIO()
        self.status = None
        self.sent = {}

    def send_response(self, code, message=None):
        self.status = code

    def send_header(self, k, v):
        self.sent[k] = v

    def end_headers(self):
        pass


def call(headers, body=b""):
    h = Fake(headers, body)
    h.do_POST()
    return h


class Handler(unittest.TestCase):
    def setUp(self):
        self._fetch, self._bump = api._fetch, api._bump
        data = {"kpistd/taxonomy.json": TAX, "index.json": INDEX, "kpistd/latest.json": {"kpis": {}}}
        data.update({f"kpistd/series/{k}.json": v for k, v in SERIES.items()})
        api._fetch = lambda p: data[p]
        api._bump = lambda b: 1

    def tearDown(self):
        api._fetch, api._bump = self._fetch, self._bump

    def test_foreign_origin_rejected(self):
        self.assertEqual(call({"Origin": "https://evil.example"}).status, 403)
        self.assertEqual(call({}).status, 403)

    def test_bad_bodies(self):
        o = {"Origin": "https://www.filedunder.ai"}
        self.assertEqual(call({**o, "Content-Length": "0"}).status, 400)
        self.assertEqual(call({**o, "Content-Length": "3"}, b"{x}").status, 400)
        b = json.dumps({"tickers": []}).encode()
        self.assertEqual(call({**o, "Content-Length": str(len(b))}, b).status, 400)

    def test_rate_limited(self):
        api._bump = lambda b: 10 ** 6
        b = json.dumps({"tickers": ["AAA"]}).encode()
        self.assertEqual(call({"Origin": "https://filedunder.ai", "Content-Length": str(len(b))}, b).status, 429)

    def test_builds_workbook(self):
        b = json.dumps({"tickers": ["AAA", "BBB"], "kpis": ["nim"], "label": "Banks"}).encode()
        h = call({"Origin": "https://www.filedunder.ai", "Content-Length": str(len(b))}, b)
        self.assertEqual(h.status, 200)
        self.assertIn("spreadsheetml", h.sent["Content-Type"])
        self.assertIn("filedunder-comps-banks", h.sent["Content-Disposition"])
        self.assertTrue(h.wfile.getvalue().startswith(b"PK"))


if __name__ == "__main__":
    unittest.main()
