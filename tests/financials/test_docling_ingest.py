from etl.docling_ingest import out_path, shape_tables_from_json, shard_of, table_rows


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
