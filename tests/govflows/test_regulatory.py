import datetime as dt

from etl.regulatory import name_ok, phase_rank, query_phrases, search_words, summarize_trials, tokens


def test_tokens_and_name_matching():
    assert tokens("VERTEX PHARMS INC") == ["VERTEX", "PHARMACEUTICALS"]
    assert name_ok("VERTEX PHARMS INC", ["VERTEX PHARMACEUTICALS INC / MA"])
    assert name_ok("Eli Lilly and Company", ["ELI LILLY & Co"])
    assert not name_ok("Vertex Aerospace LLC", ["VERTEX PHARMACEUTICALS INC / MA"])
    assert not name_ok("AMG Inc", ["AMG"])


def test_search_words_and_phrases():
    names = ["JOHNSON & JOHNSON", "Janssen", "Laboratoires Janssen SA", "Abiomed, Inc."]
    assert search_words(names) == ["JOHNSON", "JANSSEN", "ABIOMED"]
    assert query_phrases(["ELI LILLY & Co", "LILLY ELI & CO"]) == ["ELI LILLY", "LILLY ELI"]


def test_trial_summary():
    today = dt.date(2026, 11, 8)
    rows = [
        {"nct": "A", "status": "RECRUITING", "phase": "P3", "primary_completion": "2027-02-01", "updated": "2026-10-01"},
        {"nct": "B", "status": "TERMINATED", "phase": "P2", "updated": "2026-10-20", "why_stopped": "Business decision"},
        {"nct": "C", "status": "ACTIVE_NOT_RECRUITING", "phase": "P1", "primary_completion": "2028-01-01"},
    ]
    s = summarize_trials(rows, today)
    assert s["active"] == 2 and s["late_stage_active"] == 1
    assert [r["nct"] for r in s["upcoming"]] == ["A"]
    assert [r["nct"] for r in s["recent_changes"]] == ["B"] and s["stopped_2y"] == 1
    assert phase_rank("P2/P3") == 2.5
