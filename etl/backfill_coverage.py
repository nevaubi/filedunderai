"""Bootstrap the approved 300 IR coverage entries without rescanning legacy KPIs.

Run only after ir-stream-report.json records all 300 successful IR readbacks.
The existing 500 entries and KPI/segment count timestamps remain unchanged.
"""
import argparse
import copy
import datetime as dt
import json
from collections import Counter
from pathlib import Path

from .backfill import atomic_write, digest, encoded
from .common import Supabase, now_iso, serialized_aggregates
from .universe import ADDITIONAL_REGISTRY, SP500

PATH = "meta/coverage_ir.json"
STATUSES = ("complete", "partial", "unverified")


def count(value):
    return type(value) is int and value >= 0


def summarize(companies):
    rows = companies.values()
    return {"companies":len(companies),
        "documents":sum(row["docs"] for row in rows), "bytes":sum(row["bytes"] for row in rows),
        "kpis":sum(row["kpis"] for row in rows), "kpi_values":sum(row["kpi_values"] for row in rows),
        "companies_with_docs":sum(bool(row["docs"]) for row in rows),
        "companies_with_kpis":sum(bool(row["kpis"]) for row in rows),
        "companies_with_segments":sum(bool(row.get("segment_tables")) for row in rows),
        "segment_tables":sum(row.get("segment_tables") or 0 for row in rows),
        "coverage":dict(Counter((row.get("coverage") or {}).get("status", "unverified") for row in rows))}


def check_coverage(value, documents, required=False):
    if value is None and not required:
        return
    if not isinstance(value, dict) or value.get("status") not in STATUSES:
        raise ValueError("Invalid IR coverage status")
    if required or "total_documents" in value:
        if not count(value.get("total_documents")) or value["total_documents"] != documents:
            raise ValueError("IR coverage total_documents differs from document count")


def check_prior(prior, approved):
    if (not isinstance(prior, dict) or not isinstance(prior.get("companies"), dict)
            or not isinstance(prior.get("summary"), dict)
            or set(prior["companies"]) not in (SP500, SP500 | set(approved))):
        raise ValueError("Prior coverage must contain exactly the original 500 or resumed 800 companies")
    for ticker, row in prior["companies"].items():
        if (not isinstance(row, dict)
                or any(not count(row.get(key)) for key in ("docs", "bytes", "kpis", "kpi_values"))
                or not isinstance(row.get("types"), dict)
                or any(not isinstance(key, str) or not count(value) for key, value in row["types"].items())
                or sum(row["types"].values()) != row["docs"]
                or ("segment_tables" in row and not count(row["segment_tables"]))
                or (row.get("segment_ties") is not None and not count(row["segment_ties"]))):
            raise ValueError(f"Invalid prior coverage counts for {ticker}")
        check_coverage(row.get("coverage"), row["docs"])
        if ticker in approved and any(row.get(key) for key in ("kpis", "kpi_values", "segment_tables", "segment_ties")):
            raise ValueError(f"Resume cannot replace recorded KPI or segment counts for {ticker}")
    expected = summarize(prior["companies"])
    for key, value in expected.items():
        actual = prior["summary"].get(key)
        if key == "coverage":
            if key not in prior["summary"]:
                continue
            valid = isinstance(actual, dict) and all(count(n) for n in actual.values())
        else:
            valid = count(actual)
        if not valid or actual != value:
            raise ValueError(f"Inconsistent prior coverage summary: {key}")
    for key in ("kpi_counts_as_of", "segment_counts_as_of"):
        stamp = prior.get(key)
        try:
            if not isinstance(stamp, str) or not stamp:
                raise ValueError("Missing timestamp")
            dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError(f"Invalid prior coverage timestamp: {key}") from error


def check_report(report, approved):
    if (not isinstance(report, dict) or type(report.get("published")) is not int
            or report["published"] != len(approved)
            or report.get("cohort_id", ADDITIONAL_REGISTRY["cohort_id"]) != ADDITIONAL_REGISTRY["cohort_id"]
            or not isinstance(report.get("results"), dict) or set(report["results"]) != set(approved)):
        raise ValueError("IR stream report must contain all 300 approved publication results")
    for ticker, row in report["results"].items():
        if (not isinstance(row, dict) or row.get("ticker") != ticker or row.get("status") != "published"
                or not count(row.get("documents")) or not row["documents"]):
            raise ValueError(f"Incomplete IR stream publication result for {ticker}")
        check_coverage(row.get("coverage"), row["documents"], required=True)


@serialized_aggregates
def bootstrap_coverage(sb, root):
    """Publish only the coverage aggregate, with a verified private prior backup."""
    root = Path(root)
    approved = {row["ticker"]:row["cik"] for row in ADDITIONAL_REGISTRY["companies"]}
    if len(approved) != 300 or len(SP500) != 500 or set(approved) & SP500:
        raise ValueError("Coverage bootstrap requires the approved 300-company addition to the original 500")
    report_body = (root / "ir-stream-report.json").read_bytes()
    report = json.loads(report_body)
    check_report(report, approved)
    prior = sb.get_json(PATH)
    check_prior(prior, approved)

    scope = "ticker=in.(" + ",".join(sorted(approved)) + ")"
    core = sb.select("companies", "select=ticker,cik&" + scope)
    if (len(core) != len(approved) or {row.get("ticker") for row in core} != set(approved)
            or any(type(row.get("cik")) is not int or row["cik"] != approved[row["ticker"]] for row in core)):
        raise ValueError("Core company CIKs must match all 300 approved issuers")
    grouped = {ticker:{"docs":0,"bytes":0,"types":Counter()} for ticker in approved}
    for row in sb.select("ir_documents", "select=ticker,doc_type,bytes&" + scope):
        ticker, doc_type = row.get("ticker"), row.get("doc_type") or "other"
        if ticker not in approved or not count(row.get("bytes")) or not isinstance(doc_type, str):
            raise ValueError("Invalid or out-of-scope IR document count input")
        group = grouped[ticker]
        group["docs"] += 1
        group["bytes"] += row["bytes"]
        group["types"][doc_type] += 1
    for table in ("kpi_definitions", "kpi_values"):
        if sb.select(table, "select=ticker&" + scope + "&limit=1"):
            raise ValueError(f"Unexpected new-company {table}; cannot bootstrap zero counts")
    segments = sb.get_json("meta/segments.json")
    if not isinstance(segments, dict) or not isinstance(segments.get("companies"), dict):
        raise ValueError("Missing current segment count snapshot")
    for ticker in approved.keys() & segments["companies"].keys():
        row = segments["companies"][ticker]
        if (not isinstance(row, dict)
                or any(not count(row.get(key)) or row[key] != 0 for key in ("tables", "recon_ok"))):
            raise ValueError(f"Unexpected new-company segment counts for {ticker}")

    current = copy.deepcopy(prior)
    stamp = now_iso()
    for ticker, group in grouped.items():
        result = report["results"][ticker]
        if group["docs"] != result["documents"]:
            raise ValueError(f"Stored IR document count differs from successful readback receipt for {ticker}")
        current["companies"][ticker] = {**group,"types":dict(group["types"]),"kpis":0,"kpi_values":0,
            "segment_tables":0,"segment_ties":0,"documents_as_of":stamp,"coverage":copy.deepcopy(result["coverage"])}
    current["generated"] = stamp
    current["summary"] = {**prior["summary"], **summarize(current["companies"])}

    # Content-addressing preserves the initial 500-company backup across 800-company resumes.
    before, after = encoded(prior), encoded(current)
    backup_path = f"backfills/{ADDITIONAL_REGISTRY['cohort_id']}/coverage-ir/backups/{digest(before)}.json"
    if sb.get_object("raw", backup_path) is None:
        sb.put_object("raw", backup_path, before, cache="no-store")
    if sb.get_object("raw", backup_path) != before:
        raise RuntimeError("Prior coverage backup read-back mismatch")
    sb.put_object("public-data", PATH, after, cache="max-age=300")
    if sb.get_object("public-data", PATH) != after:
        raise RuntimeError("Published coverage read-back mismatch")
    receipt = {"status":"published","cohort_id":ADDITIONAL_REGISTRY["cohort_id"],"completed_at":now_iso(),
        "prior_companies":len(prior["companies"]),"companies":len(current["companies"]),
        "added_companies":len(set(approved) - set(prior["companies"])),"new_documents":sum(g["docs"] for g in grouped.values()),
        "backup_path":backup_path,"prior_sha256":digest(before),"coverage_sha256":digest(after),
        "ir_report_sha256":digest(report_body),"summary":current["summary"],
        "kpi_counts_as_of":prior["kpi_counts_as_of"],"segment_counts_as_of":prior["segment_counts_as_of"]}
    atomic_write(root / "coverage-publication-report.json", encoded(receipt))
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args(argv)
    print(json.dumps(bootstrap_coverage(Supabase(), args.root), sort_keys=True))


if __name__ == "__main__":
    main()
