"""Stage and verify additive company imports from immutable SEC snapshots.

Staging is deliberately local and has no remote credentials, provider calls or
lease emulation. Publication uses the normal Supabase client and lease below.
"""
import argparse
import gzip
import hashlib
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote

from . import fundamentals, jobs, sec
from .common import Supabase, now_iso, serialized_aggregates
from .universe import ADDITIONAL_REGISTRIES, ADDITIONAL_REGISTRY, BY_TICKER, SP500, cohort_registry, registry_by_id, registry_for

TABLE_KEYS = {"companies": "ticker", "documents": "id", "filings": "accession",
              "facts": "ticker,statement,label,kind,period_end"}


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(body):
    return hashlib.sha256(body).hexdigest()


def atomic_write(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".part")
    temporary.write_bytes(body)
    temporary.replace(path)


def safe_path(root, relative):
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("Stage path leaves its company directory")
    return path


def allowed_object(ticker, bucket, path):
    cik = BY_TICKER[ticker]["cik"]
    if bucket == "public-data":
        return path == f"companies/{ticker}.json" or bool(re.fullmatch(
            rf"sources/companies/{re.escape(ticker)}/[a-f0-9]{{64}}\.json", path))
    if bucket == "raw":
        return path == f"sec/companyfacts/CIK{cik:010d}.json.gz" or bool(re.fullmatch(
            rf"sec/(?:companyfacts|submissions)/archives/CIK{cik:010d}/[a-f0-9]{{64}}\.json\.gz", path))
    return False


class StageStore:
    """Only records writes for one approved additional issuer on disk."""
    stage_only = True

    def __init__(self, root, ticker):
        if ticker not in BY_TICKER or ticker in SP500:
            raise ValueError("Additive staging requires an approved new issuer")
        self.root, self.ticker = Path(root), ticker
        self.objects, self.tables = {}, {}

    def put_object(self, bucket, path, content, content_type="application/json", cache="max-age=300"):
        if not allowed_object(self.ticker, bucket, path):
            raise ValueError("Object is outside this issuer's approved staging paths")
        body = content.encode() if isinstance(content, str) else bytes(content)
        relative = f"objects/{bucket}/{path}"
        atomic_write(safe_path(self.root, relative), body)
        self.objects[(bucket, path)] = {"bucket": bucket, "path": path, "local_path": relative,
                                       "sha256": digest(body), "bytes": len(body),
                                       "content_type": content_type, "cache": cache}
        return len(body)

    def get_object(self, bucket, path, public=False):
        target = safe_path(self.root, f"objects/{bucket}/{path}")
        return target.read_bytes() if target.exists() else None

    def put_json(self, path, obj, cache="max-age=300"):
        return self.put_object("public-data", path, encoded(obj), cache=cache)

    def get_json(self, path):
        body = self.get_object("public-data", path)
        return json.loads(body) if body else None

    put_raw = Supabase.put_raw

    def upsert(self, table, rows, on_conflict, batch=500):
        if table not in TABLE_KEYS:
            raise ValueError("Unexpected table in facts-only stage")
        if on_conflict != TABLE_KEYS[table]:
            raise ValueError("Unexpected table conflict key")
        rows = list(rows)
        if any(r.get("ticker") != self.ticker for r in rows):
            raise ValueError("Stage attempted to write another issuer")
        current = self.tables.setdefault(table, {"on_conflict": on_conflict, "rows": {}})
        if current["on_conflict"] != on_conflict:
            raise ValueError("Conflicting staged table keys")
        keys = on_conflict.split(",")
        current["rows"].update({tuple(row[k] for k in keys): row for row in rows})
        return len(rows)

    def seal(self, fundamental, sources):
        table_meta = []
        for table, data in sorted(self.tables.items()):
            rows = list(data["rows"].values())
            body = encoded(rows)
            relative = f"tables/{table}.json"
            atomic_write(safe_path(self.root, relative), body)
            table_meta.append({"table": table, "on_conflict": data["on_conflict"], "rows": len(rows),
                               "path": relative, "sha256": digest(body)})
        receipt = {"version": 1, "status": "staged", "ticker": self.ticker, "cik": BY_TICKER[self.ticker]["cik"],
                   "cohort_id": registry_for(self.ticker)["cohort_id"], "created_at": now_iso(), "sources": sources,
                   "objects": list(self.objects.values()), "tables": table_meta, "fundamental": fundamental}
        atomic_write(self.root / "receipt.json", encoded(receipt))
        return receipt


def stage_company(root, ticker):
    root = Path(root)
    manifest = json.loads((root / "sec-manifests" / f"{ticker}.json").read_bytes())
    if manifest["status"] != "collected" or manifest["cik"] != BY_TICKER[ticker]["cik"]:
        raise ValueError("SEC source collection is incomplete or has the wrong identity")
    bodies = {}
    for name, source in manifest["sources"].items():
        source_path = Path(source["path"]).resolve()
        if not source_path.is_relative_to(root.resolve()):
            raise ValueError("SEC input is outside the approved batch")
        body = gzip.decompress(source_path.read_bytes())
        if digest(body) != source["sha256"]:
            raise ValueError("Immutable SEC source hash mismatch")
        bodies[name] = body
    stage = StageStore(root / "staged" / ticker, ticker)
    fundamental = jobs.process_company(stage, ticker, facts_only=True, fetch_docs=False, source_inputs=bodies,
                                      source_retrieved_at={k:v["retrieved_at"] for k,v in manifest["sources"].items()},
                                      log=lambda *_: None)
    receipt = stage.seal(fundamental, manifest["sources"])
    validate_stage(stage.root)
    return {"ticker": ticker, "status": "verified_stage", "tables": {m["table"]: m["rows"] for m in receipt["tables"]}}


def validate_stage(root):
    from .source_provenance import validate_statement_provenance
    root = Path(root)
    receipt = json.loads((root / "receipt.json").read_bytes())
    ticker = receipt["ticker"]
    if ticker in SP500 or ticker not in BY_TICKER or receipt["cik"] != BY_TICKER[ticker]["cik"]:
        raise ValueError("Staged issuer is not an approved additional company")
    if receipt["cohort_id"] != registry_for(ticker)["cohort_id"] or receipt["status"] != "staged":
        raise ValueError("Staged cohort or status mismatch")
    objects = {}
    for item in receipt["objects"]:
        if not allowed_object(ticker, item["bucket"], item["path"]):
            raise ValueError("Staged object targets another issuer or aggregate")
        body = safe_path(root, item["local_path"]).read_bytes()
        if digest(body) != item["sha256"] or len(body) != item["bytes"]:
            raise ValueError("Staged object hash mismatch")
        objects[(item["bucket"], item["path"])] = body
    tables = {}
    for item in receipt["tables"]:
        if item["table"] not in TABLE_KEYS or item["on_conflict"] != TABLE_KEYS[item["table"]]:
            raise ValueError("Staged table or conflict key is not allowed")
        body = safe_path(root, item["path"]).read_bytes()
        if digest(body) != item["sha256"]:
            raise ValueError("Staged relational hash mismatch")
        rows = json.loads(body)
        if len(rows) != item["rows"] or any(row.get("ticker") != ticker for row in rows):
            raise ValueError("Staged relational scope mismatch")
        tables[item["table"]] = rows
    if set(tables) != set(TABLE_KEYS) or len(receipt["tables"]) != len(TABLE_KEYS):
        raise ValueError("Incomplete or duplicate staged tables")
    bundle = json.loads(objects[("public-data", f"companies/{ticker}.json")])
    if bundle["cik"] != receipt["cik"] or bundle["ticker"] != ticker:
        raise ValueError("Staged bundle issuer mismatch")
    raw = gzip.decompress(objects[("raw", bundle["source_archive"] + ".gz")])
    sub_archive = bundle["provenance"]["submissions"]["source_archive"]
    sub_raw = gzip.decompress(objects[("raw", sub_archive + ".gz")])
    required_objects = {("raw", bundle["source_archive"] + ".gz"), ("raw", sub_archive + ".gz"),
                        ("raw", f"sec/companyfacts/CIK{receipt['cik']:010d}.json.gz"),
                        ("public-data", f"companies/{ticker}.json"),
                        ("public-data", bundle["provenance"]["proof_path"])}
    if set(objects) != required_objects or len(receipt["objects"]) != len(required_objects):
        raise ValueError("Incomplete, duplicate or extraneous staged objects")
    if (digest(raw) != receipt["sources"]["companyfacts"]["sha256"]
            or digest(sub_raw) != receipt["sources"]["submissions"]["sha256"]
            or digest(sub_raw) != bundle["provenance"]["submissions"]["sha256"]
            or int(json.loads(sub_raw)["cik"]) != receipt["cik"]
            or gzip.decompress(objects[("raw", f"sec/companyfacts/CIK{receipt['cik']:010d}.json.gz")]) != raw):
        raise ValueError("Staged snapshot identity or hash mismatch")
    if len(tables["companies"]) != 1 or tables["companies"][0]["cik"] != receipt["cik"]:
        raise ValueError("Staged company row identity mismatch")
    document_sources = {
        bundle["source_archive"]:(raw,"companyfacts_snapshot",f"https://data.sec.gov/api/xbrl/companyfacts/CIK{receipt['cik']:010d}.json"),
        f"sec/companyfacts/CIK{receipt['cik']:010d}.json":(raw,"companyfacts",f"https://data.sec.gov/api/xbrl/companyfacts/CIK{receipt['cik']:010d}.json"),
        sub_archive:(sub_raw,"submissions_snapshot",f"https://data.sec.gov/submissions/CIK{receipt['cik']:010d}.json")}
    if {r["id"] for r in tables["documents"]} != set(document_sources) or len(tables["documents"]) != len(document_sources):
        raise ValueError("Staged document IDs differ from archived sources")
    for document in tables["documents"]:
        body,kind,url = document_sources[document["id"]]
        if (document["sha256"] != digest(body) or document["bytes"] != len(body) or document["kind"] != kind
                or document["storage_path"] != "raw/" + document["id"] + ".gz" or document["source_url"] != url):
            raise ValueError("Staged document metadata differs from its source")
    proof = validate_statement_provenance(raw, bundle["statements"], receipt["cik"], bundle["provenance"]["snapshot_sha256"])
    if proof != json.loads(objects[("public-data", bundle["provenance"]["proof_path"])]):
        raise ValueError("Staged provenance proof mismatch")
    expected = {}
    for statement, series in bundle["statements"].items():
        for row in series:
            for kind, key in (("A", "annual"), ("Q", "quarterly")):
                for point in row[key]:
                    expected[(statement, row["label"], kind, point["end"])] = {
                        "ticker": ticker, "statement": statement, "label": row["label"], "kind": kind,
                        "period_end": point["end"], "period_start": point.get("start"),
                        "concept": point.get("c") or row["concept"], "unit": row["unit"], "value": point["v"],
                        "accession": point["accn"], "form": point["form"], "filed": point["filed"], "derived": bool(point.get("derived"))}
    actual = {(r["statement"], r["label"], r["kind"], r["period_end"]): r for r in tables["facts"]}
    if actual != expected or len(actual) != len(tables["facts"]):
        raise ValueError("Relational facts differ from verified statement values")
    source_facts, submissions = json.loads(raw)["facts"], json.loads(sub_raw)
    source_filings = sec.filing_list(submissions)
    expected_filings = {f["a"]:{"accession":f["a"],"ticker":ticker,"form":f["f"],"filed":f["d"],
        "report_date":f["r"] or None,"primary_doc":f["p"],"items":f["i"],"description":jobs.describe(f),
        "url":sec.filing_url(receipt["cik"],f["a"],f["p"]),"has_source_tables":False} for f in source_filings[:250]}
    actual_filings = {r["accession"]:{k:v for k,v in r.items() if k != "updated_at"} for r in tables["filings"]}
    if actual_filings != expected_filings or len(tables["filings"]) != len(expected_filings):
        raise ValueError("Staged filing ownership or metadata differs from SEC submissions")
    profile = sec.profile(submissions,source_facts)
    if fundamentals.FYE_HISTORY.get(ticker):
        profile["fyeHistory"] = fundamentals.FYE_HISTORY[ticker]
    if ticker in fundamentals.FY_NAME_PRIOR:
        profile["fyNameOffset"] = -1
    if fundamentals.TRANSITIONS.get(ticker):
        profile["fyTransitions"] = fundamentals.TRANSITIONS[ticker]
    # JSON transport turns the declared tuple histories into arrays.
    profile = json.loads(encoded(profile))
    if bundle["profile"] != profile:
        raise ValueError("Staged profile values differ from archived sources")
    from .source_quality import retain_proved_filing_tables
    expected_tables,_ = retain_proved_filing_tables(sec.filing_tables(source_facts,bundle["statements"],source_filings,receipt["cik"]),proof)
    if bundle["filingTables"] != expected_tables:
        raise ValueError("Reconstructed filing tables differ from verified source records")
    if bundle.get("ix") or bundle.get("release") or bundle.get("summary") or bundle.get("news"):
        raise ValueError("Unexpected enrichment outside this facts-only source stage")
    expected_fundamental = jobs.company_fundamental(ticker,bundle["statements"],profile,source_filings,bundle["warnings"],
        {"deleted":0,"backup":None,"mode":"additive"})
    if receipt["fundamental"] != json.loads(encoded(expected_fundamental)):
        raise ValueError("Staged fundamental aggregate differs from verified statement/profile inputs")
    company = tables["companies"][0]
    if (company["shares_outstanding"] != (profile["sharesOutstanding"] or {}).get("val")
            or company["shares_as_of"] != (profile["sharesOutstanding"] or {}).get("end")):
        raise ValueError("Relational company shares differ from the archived source profile")
    return receipt


def _same_shared_ownership_filing(first, second, financial_accessions):
    """Preserve matching, financially unused shared notices under their first owner.

    SEC submissions can list the same ownership or transaction notice under
    multiple issuers. Exact Form 144 and 425 references are eligible too, but
    reporting forms, document IDs and accessions used by any financial proof
    remain blocked. The secondary issuer keeps its archived filing reference.
    """
    def form(row):
        match = re.fullmatch(r"(?:(?:SC|SCHEDULE)\s+(13[DG](?:/A)?)|([345](?:/A)?|144|425))",
                             str(row.get("form", "")).strip().upper())
        return (match.group(1) or match.group(2)) if match else None
    fields = ("accession", "filed", "report_date", "primary_doc", "items")
    return (form(first) is not None and form(first) == form(second)
            and first.get("accession") not in financial_accessions
            and all(key in first and key in second and first[key] == second[key] for key in fields))


def _publication_ownership(sb, root, receipts, workers):
    """Resolve global keys before writes; existing owners always retain their rows."""
    sources = {"documents":{}, "filings":{}}
    financial_accessions = set()
    for ticker in sorted(receipts):
        stage = root / "staged" / ticker
        for item in receipts[ticker]["tables"]:
            if item["table"] == "companies":
                continue
            rows = json.loads(safe_path(stage,item["path"]).read_bytes())
            if item["table"] == "facts":
                financial_accessions.update(row["accession"] for row in rows)
            else:
                key = TABLE_KEYS[item["table"]]
                for row in rows:
                    sources[item["table"]].setdefault(row[key],[]).append((ticker,row))
        # Include historical observations and derived legs, not just selected DB facts.
        proof_item = next(item for item in receipts[ticker]["objects"]
                          if item["bucket"] == "public-data" and item["path"].startswith("sources/companies/"))
        proof = json.loads(safe_path(stage,proof_item["local_path"]).read_bytes())
        financial_accessions.update(row["accn"] for row in proof["records"].values())
    for table, groups in sources.items():
        for group in groups.values():
            if len(group) > 1 and (table != "filings" or any(
                    not _same_shared_ownership_filing(group[0][1],row,financial_accessions) for _,row in group[1:])):
                raise ValueError("Global source identity is shared by multiple new issuers")

    queries = []
    for table, groups in sources.items():
        identifiers = list(groups)
        batch_size = 25 if table == "documents" else 250
        for offset in range(0,len(identifiers),batch_size):
            key = TABLE_KEYS[table]
            columns = "accession,ticker,form,filed,report_date,primary_doc,items" if table == "filings" else "id,ticker"
            query = f"select={columns}&{key}=in.(" + quote(",".join(identifiers[offset:offset+batch_size]),safe=",") + ")"
            queries.append((table,query))
    existing = {"documents":{}, "filings":{}}
    def read_owners(item):
        table,query = item
        return table,sb.select(table,query)
    with ThreadPoolExecutor(max_workers=max(1,min(8,workers))) as pool:
        for table,rows in pool.map(read_owners,queries):
            existing[table].update({row[TABLE_KEYS[table]]:row for row in rows})

    plans = {ticker:{"filings":[], "filings_staged":0, "skipped":[], "error":None} for ticker in receipts}
    conflict = "Global document/filing identity belongs to another issuer"
    for identity,group in sources["documents"].items():
        ticker,_ = group[0]
        if identity in existing["documents"] and existing["documents"][identity]["ticker"] != ticker:
            plans[ticker]["error"] = conflict
    for accession,group in sources["filings"].items():
        old = existing["filings"].get(accession)
        owner = old["ticker"] if old else group[0][0]  # Groups follow sorted tickers.
        reference = old if old else group[0][1]
        for ticker,row in group:
            plan = plans[ticker]
            plan["filings_staged"] += 1
            if ticker == owner:
                plan["filings"].append(row)
            elif not _same_shared_ownership_filing(reference,row,financial_accessions):
                plan["error"] = conflict
            else:
                plan["skipped"].append({
                    **{key:row[key] for key in ("accession","form","filed","report_date","primary_doc","items")},
                    "owner_ticker":owner,
                    "reason":"shared_issuer_reference" if str(row["form"]).strip() == "425" else "shared_ownership_filing",
                    "ownership_basis":"existing_row" if old else "new_batch_owner"})
    return plans


@serialized_aggregates
def publish_staged(sb, root, tickers, workers=4, time_budget=None):
    """Publish only reviewed new-company stages; never replace KPI snapshots.

The checkpoint binds every allowed resume to the exact staged receipt. Source
objects are read back before relational values and company bundles are exposed.
The public universe is activated separately after batch counts are reconciled.
"""
    root = Path(root)
    if not tickers or len(tickers) != len(set(tickers)) or any(t not in BY_TICKER or t in SP500 for t in tickers):
        raise ValueError("Publish requires unique approved additional issuers")
    receipts = {t: validate_stage(root / "staged" / t) for t in tickers}
    if any(receipts[t]["ticker"] != t or receipts[t]["cik"] != BY_TICKER[t]["cik"] for t in tickers):
        raise ValueError("Staged receipt does not match the requested ticker scope")
    ownership = _publication_ownership(sb,root,receipts,workers)
    receipt_hashes = {t:digest((root / "staged" / t / "receipt.json").read_bytes()) for t in tickers}
    cohort = cohort_registry(tickers)["cohort_id"]
    state_path = f"backfills/{cohort}/publish-state.json"
    previous = sb.get_object("raw", state_path)
    state = json.loads(previous) if previous else {"version":1, "cohort_id":cohort, "created_at":now_iso(), "companies":{}}
    if state.get("version") != 1 or state.get("cohort_id") != cohort or not isinstance(state.get("companies"), dict):
        raise ValueError("Invalid backfill checkpoint")
    existing = sb.select("companies", "select=ticker,cik&ticker=in.(" + ",".join(tickers) + ")")
    existing += sb.select("companies", "select=ticker,cik&cik=in.(" + ",".join(str(BY_TICKER[t]["cik"]) for t in tickers) + ")")
    for row in existing:
        old = state["companies"].get(row["ticker"], {})
        if (row["ticker"] not in receipts or row["cik"] != receipts[row["ticker"]]["cik"]
                or old.get("receipt_sha256") != receipt_hashes[row["ticker"]]):
            raise ValueError("Existing company is outside this exact resumable batch")
    for ticker in tickers:
        old = state["companies"].get(ticker)
        if old and old.get("receipt_sha256") != receipt_hashes[ticker]:
            raise ValueError("Staged receipt changed after publication began")
        state["companies"].setdefault(ticker, {"cik":receipts[ticker]["cik"], "receipt_sha256":receipt_hashes[ticker], "status":"planned"})
    lock = threading.Lock()
    def checkpoint():
        state["updated_at"] = now_iso()
        body = encoded(state)
        sb.put_object("raw", state_path, body, cache="no-store")
        atomic_write(root / "publish-state.json", body)
    checkpoint()
    start = time.monotonic()

    def one(ticker):
        plan = ownership[ticker]
        filing_counts = {"filings_staged":plan["filings_staged"], "filings_upserted":0,
                         "skipped_shared_ownership_filings":plan["skipped"]}
        if time_budget is not None and time.monotonic() - start >= time_budget:
            result = {"status":"pending", "reason":"time budget", **filing_counts}
            with lock:
                state["companies"][ticker].update(result)
            return ticker,result
        receipt = receipts[ticker]
        stage_root = root / "staged" / ticker
        try:
            if plan["error"]:
                raise ValueError(plan["error"])
            tables = {m["table"]:json.loads(safe_path(stage_root,m["path"]).read_bytes()) for m in receipt["tables"]}
            tables["filings"] = plan["filings"]
            # This issuer was absent before the checkpoint, or belongs to its exact
            # prior partial run. All tables are constrained to this ticker above.
            sb.upsert("companies", tables["companies"], TABLE_KEYS["companies"])
            objects = sorted(receipt["objects"], key=lambda m:(m["bucket"] == "public-data", m["path"].startswith("companies/")))
            company_object = None
            for item in objects:
                if item["bucket"] == "public-data" and item["path"].startswith("companies/"):
                    company_object = item
                    continue
                body = safe_path(stage_root,item["local_path"]).read_bytes()
                sb.put_object(item["bucket"],item["path"],body,content_type=item["content_type"],cache=item["cache"])
                if digest(sb.get_object(item["bucket"],item["path"]) or b"") != item["sha256"]:
                    raise RuntimeError("Published source object read-back hash mismatch")
            for table in ("documents", "filings", "facts"):
                if table == "filings":
                    filing_counts["filings_upserted"] = None  # Unknown if acknowledgement fails after commit.
                sb.upsert(table, tables[table], TABLE_KEYS[table], batch=1000)
                if table == "filings":
                    filing_counts["filings_upserted"] = len(tables[table])
            body = safe_path(stage_root,company_object["local_path"]).read_bytes()
            sb.put_object("public-data", company_object["path"], body, cache="max-age=300")
            if digest(sb.get_object("public-data",company_object["path"]) or b"") != company_object["sha256"]:
                raise RuntimeError("Published company bundle read-back hash mismatch")
            result = {"status":"published", "verified_at":now_iso(), "facts":len(tables["facts"]),
                      "filings":len(tables["filings"]), "objects_verified":len(objects)}
        except Exception as error:
            result = {"status":"partial", "error":str(error)[:400]}
        result.update(filing_counts)
        with lock:
            state["companies"][ticker].update(result)
            if result["status"] == "published":
                state["companies"][ticker].pop("error",None)
                state["companies"][ticker].pop("reason",None)
        return ticker, result

    results = {}
    with ThreadPoolExecutor(max_workers=max(1,min(12,workers))) as pool:
        for ticker,result in pool.map(one,sorted(tickers)):
            results[ticker] = result
            print(json.dumps({"ticker":ticker, "done":len(results), **result}), flush=True)
            if len(results) % 5 == 0:
                with lock:
                    checkpoint()
    with lock:
        for result in results.values():
            for skipped in result["skipped_shared_ownership_filings"]:
                if skipped["ownership_basis"] == "new_batch_owner":
                    skipped["owner_publication_status"] = state["companies"][skipped["owner_ticker"]]["status"]
        checkpoint()
    published = [t for t in tickers if state["companies"][t]["status"] == "published"]
    if published:
        fundamental = sb.get_json("meta/fundamentals.json")
        if not isinstance(fundamental,dict) or not fundamental:
            raise ValueError("Existing fundamentals unavailable; preserving public indexes")
        backup_path = f"backfills/{cohort}/before-fundamentals.json"
        if sb.get_object("raw",backup_path) is None:
            backup = encoded(fundamental)
            sb.put_object("raw",backup_path,backup,cache="no-store")
            if digest(sb.get_object("raw",backup_path) or b"") != digest(backup):
                raise RuntimeError("Aggregate backup read-back failed")
        fundamental.update({t:receipts[t]["fundamental"] for t in published})
        sb.put_json("meta/fundamentals.json",fundamental,cache="no-store")
    return {"cohort_id":cohort, "published":len(published), "requested":len(tickers), "results":results,
            "public_index_activated":False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["stage", "verify", "publish"])
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--tickers", nargs="*")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--budget", type=float)
    parser.add_argument("--cohort-id", help="default ticker scope when --tickers is omitted (default: the first cohort)")
    args = parser.parse_args(argv)
    registry = registry_by_id(args.cohort_id) if args.cohort_id else ADDITIONAL_REGISTRIES[0]
    tickers = args.tickers or [r["ticker"] for r in registry["companies"]]
    if len(tickers) != len(set(tickers)) or any(t not in BY_TICKER or t in SP500 for t in tickers):
        raise ValueError("Batch must contain unique approved additional companies")
    if args.action == "publish":
        report = publish_staged(Supabase(),args.root,tickers,workers=args.workers,time_budget=args.budget)
        atomic_write(args.root / "publish-report.json",encoded(report))
        print(json.dumps({k:v for k,v in report.items() if k != "results"}),flush=True)
        if report["published"] != len(tickers):
            raise SystemExit(1)
        return
    def one(ticker):
        try:
            if args.action == "stage":
                return stage_company(args.root, ticker)
            receipt = validate_stage(args.root / "staged" / ticker)
            return {"ticker": ticker, "status": "verified_stage", "tables": {m["table"]:m["rows"] for m in receipt["tables"]}}
        except Exception as error:
            return {"ticker": ticker, "status": "failed", "error": str(error)[:400]}
    results = []
    with ThreadPoolExecutor(max_workers=max(1, min(8, args.workers))) as pool:
        for result in pool.map(one, tickers):
            results.append(result)
            print(json.dumps({"done":len(results), **result}), flush=True)
    report = {"action": args.action, "completed_at":now_iso(), "companies":len(results),
              "verified":sum(r["status"] == "verified_stage" for r in results), "results":results}
    atomic_write(args.root / f"{args.action}-report.json", encoded(report))
    if report["verified"] != len(results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
