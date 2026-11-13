import unittest

from etl.docling_ingest import candidates, out_path, shape_tables_from_json, shard_of, table_rows, ticker_list


def test_shards_are_stable_and_disjoint():
    ids = [f"{i:016x}" for i in range(200)]
    owners = [shard_of(i, 6) for i in ids]
    assert all(0 <= o < 6 for o in owners)
    assert owners == [shard_of(i, 6) for i in ids]
    assert len(set(owners)) == 6


def test_table_shapes_from_serve_json():
    doc = {"tables": [{"prov": [{"page_no": 3}], "data": {"grid": [
        [{"text": "", "column_header": True}, {"text": "Q1  2026", "column_header": True}],
        [{"text": "Revenue"}, {"text": "$ 1,234"}],
        [{"text": ""}, {"text": ""}],
    ]}}]}
    t = shape_tables_from_json(doc)[0]
    assert t["page"] == 3 and t["n_rows"] == 2 and t["n_cols"] == 2
    assert t["header"] == [["", "Q1 2026"]] and t["rows"][1] == ["Revenue", "$ 1,234"]
    assert table_rows([[{"text": " a  b "}]]) == [["a b"]]
    assert out_path({"ticker": "AAPL", "id": "abc"}) == "docling/AAPL/abc.json.gz"


class TickerRunTest(unittest.TestCase):
    def test_ticker_filter_takes_only_ticker_shaped_names(self):
        self.assertIsNone(ticker_list(""))
        self.assertIsNone(ticker_list(" , "))
        self.assertEqual(ticker_list("jpm, BRK-B ,jpm,BF.B"), ["JPM", "BRK-B", "BF.B"])
        for bad in ("JPM)&id=neq.(x", "JPM;drop", "A B"):
            with self.assertRaises(SystemExit, msg=bad):
                ticker_list(bad)

    def test_a_ticker_run_asks_only_for_those_companies(self):
        class Store:
            def select(self, table, query):
                self.query = query
                return [{"id": f"{i:016x}", "ticker": "JPM", "doc_type": t, "bytes": 1000, "pages": 10, "storage_path": "x"}
                        for i, t in enumerate(["release", "supplement", "other"])]
        sb = Store()
        rows = candidates(sb, 0, 1, doc_types=["supplement", "release", "other"], tickers=["JPM", "PNC"])
        self.assertIn("&ticker=in.(JPM,PNC)", sb.query)
        self.assertIn("&doc_type=in.(supplement,release,other)", sb.query)
        self.assertEqual([r["doc_type"] for r in rows], ["supplement", "release", "other"])  # table-dense documents first
