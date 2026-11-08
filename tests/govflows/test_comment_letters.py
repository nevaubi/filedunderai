import pathlib

from etl.comment_letters import kind_of, parse, threads_for

TEXT = (pathlib.Path(__file__).parent / "fixtures_comment_letter_dash.txt").read_text()


def test_parse_comment_letter():
    p = parse("UPLOAD", TEXT)
    assert p["kind"] == "comments"
    assert p["subject_form"] == "10-K" and p["period"] == "2025-12-31" and p["file_no"] == "001-39759"
    assert p["n_comments"] == 1
    assert p["comments"][0]["heading"].startswith("Report of Independent Registered Public Accounting Firm")
    assert "controls" in p["topics"]


def test_kinds():
    assert kind_of("UPLOAD", "This is to advise you that we have not reviewed and will not review your registration statement.") == "no_review"
    assert kind_of("UPLOAD", "We have completed our review of your filing.") == "review_complete"
    assert kind_of("CORRESP", "Pursuant to Rule 461 ... requests that the effective date be accelerated (acceleration request)") == "acceleration_request"
    assert kind_of("CORRESP", "This letter is in response to the comment letter dated April 30, 2026.") == "response"


def test_threads_group_by_subject_and_close():
    base = {"subject_form": "10-K", "period": "2025-12-31", "subject": "Form 10-K", "topics": ["controls"]}
    letters = [
        {**base, "accession": "a", "form": "UPLOAD", "filed": "2026-04-30", "kind": "comments", "n_comments": 1},
        {**base, "accession": "b", "form": "CORRESP", "filed": "2026-05-08", "kind": "response", "n_comments": 1},
        {**base, "accession": "c", "form": "UPLOAD", "filed": "2026-05-12", "kind": "review_complete", "n_comments": 0, "topics": []},
        {"accession": "d", "form": "CORRESP", "filed": "2026-06-01", "kind": "acceleration_request", "subject_form": "S-3"},
    ]
    th = threads_for(letters)
    assert len(th) == 1
    t = th[0]
    assert t["status"] == "complete" and t["rounds"] == 1 and t["n_comments"] == 1 and t["periodic"]
    assert [l["accession"] for l in t["letters"]] == ["c", "b", "a"]
