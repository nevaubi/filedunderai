from etl.doc_index import split_text, table_chunks, text_chunks


def test_split_respects_size_and_boundaries():
    text = ("Revenue grew 8%. " * 200).strip()
    parts = split_text(text, 500)
    assert all(len(p) <= 500 for p in parts)
    assert "".join(p.replace(" ", "") for p in parts) == text.replace(" ", "")
    assert all(p.endswith(".") for p in parts[:-1])


def test_text_chunks_cap_and_pages():
    pages = ["a " * 1000, "b " * 1000, "c " * 1000]
    out = text_chunks(pages, max_chars=3000)
    assert sum(len(c) for _, c in out) <= 3000
    assert out[0][0] == 1 and {p for p, _ in out} <= {1, 2}


def test_table_chunks():
    t = [{"page": 3, "caption": "Key metrics", "rows": [["", "Q1"], ["Orders", "574"], ["", ""]]}, {"rows": []}]
    out = table_chunks(t)
    assert out == [(3, "Key metrics\nQ1\nOrders | 574")]
