"""Add source-checked original financial releases from the reviewed discovery batch."""
import argparse
import datetime as dt
import gzip
import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit

from . import ir, sec
from .backfill import atomic_write, digest, encoded
from .common import AggregateLease, Supabase, now_iso
from .universe import ADDITIONAL_REGISTRY, BY_TICKER, SP500


def prepare_documents(root, ticker):
    root = Path(root).resolve()
    if ticker in SP500 or ticker not in BY_TICKER:
        raise ValueError("IR backfill requires an approved additional issuer")
    manifest = json.loads((root / "ir-discovery" / "companies" / f"{ticker}.json").read_bytes())
    if (manifest["ticker"] != ticker or manifest["cik"] != BY_TICKER[ticker]["cik"]
            or manifest["cohort_id"] != ADDITIONAL_REGISTRY["cohort_id"]):
        raise ValueError("IR discovery manifest identity mismatch")
    cik = manifest["cik"]
    source = root / "raw" / "sec" / "submissions" / f"CIK{cik:010d}.json.gz"
    sub_raw = gzip.decompress(source.read_bytes())
    source_manifest = json.loads((root / "sec-manifests" / f"{ticker}.json").read_bytes())
    submissions = json.loads(sub_raw)
    if (source_manifest["cik"] != cik or int(submissions["cik"]) != cik
            or digest(sub_raw) != source_manifest["sources"]["submissions"]["sha256"]):
        raise ValueError("Archived submissions identity or hash mismatch")
    filing_dates = {f["a"].replace("-",""): f["d"] for f in sec.filing_list(submissions)}
    prepared, seen = [], set()
    for document in manifest.get("documents",[]):
        validation = document.get("original_validation") or {}
        if (document.get("publication_ready") is not True or document.get("document_type") != "financial_release"
                or validation.get("status") != "verified_financial_content"
                or document.get("official_identity_status") not in ("sec_cik_verified","issuer_identity_supported")):
            continue
        original = document["original"]
        if (original.get("ticker") != ticker or validation.get("target_cik") != cik
                or validation.get("target_ticker") != ticker):
            raise ValueError("Original financial document validation issuer mismatch")
        if (original.get("url") != document["source_url"]
                or validation.get("source_url") != original.get("url")):
            raise ValueError("Financial source URL differs from the retrieved original URL")
        path = Path(original["original_path"]).resolve()
        if not path.is_relative_to(root / "ir-discovery" / "originals" / ticker):
            raise ValueError("Original document leaves its issuer directory")
        body = path.read_bytes()
        sha = digest(body)
        if sha != original["sha256"] or sha != validation.get("original_sha256") or len(body) != original["bytes"]:
            raise ValueError("Original financial document hash mismatch")
        if sha in seen:
            continue
        url = document["source_url"]
        parsed = urlsplit(url)
        redirect = None
        if parsed.username is not None or parsed.password is not None or not parsed.hostname:
            raise ValueError("Invalid financial source URL")
        if parsed.scheme == "http":
            # Some issuer archives publish HTTP links which were actually fetched
            # over HTTPS after a redirect. Use only that recorded successful
            # upgrade, preserving its requested URL and the exact body hash.
            final_url = original.get("final_url")
            final = urlsplit(final_url or "")
            if (original.get("status") != "archived" or original.get("http_status") != 200
                    or final.scheme != "https" or final.username is not None or final.password is not None
                    or final.hostname != parsed.hostname or final.port not in (None,443)
                    or parsed.port not in (None,80)
                    or final.path.rstrip("/") != parsed.path.rstrip("/")
                    or final.query != parsed.query or final.fragment != parsed.fragment):
                raise ValueError("Invalid financial source URL: unverified HTTPS redirect")
            redirect = {"requested_url":url,"final_url":final_url,"original_sha256":sha,
                        "basis":"Archived HTTP 200 response after same-issuer HTTPS upgrade"}
            url,parsed = final_url,final
        if parsed.scheme != "https" or parsed.username is not None or parsed.password is not None:
            raise ValueError("Invalid financial source URL")
        published = None
        if parsed.hostname in ("www.sec.gov","sec.gov"):
            match = re.fullmatch(r"/Archives/edgar/data/([0-9]+)/([0-9]{18})/[^/]+",parsed.path)
            if not match or int(match.group(1)) != cik:
                raise ValueError("SEC exhibit issuer identity mismatch")
            published = filing_dates.get(match.group(2))
        elif document.get("publication_date_status") == "verified" and document.get("publication_date"):
            published = document["publication_date"]
        if published:
            dt.date.fromisoformat(published)
        ext = ir.sniff_ext(body,url)
        # SEC exhibits can preserve their SGML submission envelope before HTML.
        # Keep the exact response bytes; classify only an explicit HTML document.
        if ext is None and parsed.hostname in ("www.sec.gov","sec.gov"):
            prefix = body.lstrip()[:4096]
            if prefix.upper().startswith(b"<DOCUMENT>") and re.search(br"<html(?:\s|>)",prefix,re.I):
                ext = ".html"
        if ext not in (".htm",".html",".pdf",".xlsx",".xls"):
            raise ValueError("Unsupported original financial document format")
        text = ir.extract_text(body,ext)
        quality = {**text["extraction"],"attempted_at":now_iso()}
        if not quality.get("nonempty"):
            raise ValueError("Original financial document extraction is empty")
        did = ir.doc_id(url)
        storage_path = f"{ticker}/{did}-{ir.safe_name(url,ext)}"
        text_path = f"{ticker}/text/{did}.json"
        title = document.get("title") or f"{manifest['name']} financial results"
        record = {"id":did,"ticker":ticker,"url":url,"title":title[:400],"doc_type":"release",
                  "fiscal_label":None,"period_end":None,"published":published,"ext":ext,"content_type":ir.CT.get(ext),
                  "bytes":len(body),"sha256":sha,"storage_path":storage_path,"source_page":original.get("source_page") or url,
                  "pages":len(text.get("pages") or text.get("sheets") or []),"text_path":text_path,
                  "extracted_at":now_iso() if quality.get("status") == "verified" else None,
                  "fetched_at":original["retrieved_at"]}
        prepared.append({"record":record,"body":body,"text":encoded(text),"quality":quality,
                         "validation":validation,"redirect":redirect})
        seen.add(sha)
    return prepared


def restore_missing_extraction(sb, item, existing, state):
    """Recover an interrupted row-before-checkpoint commit only after both readbacks."""
    if existing["id"] in state["outcomes"]:
        return
    ticker = item["record"]["ticker"]
    if (any(existing.get(key) != item["record"][key]
            for key in ("id","ticker","url","sha256","storage_path","text_path"))
            or any(not str(existing.get(key) or "").startswith(ticker + "/")
                   for key in ("storage_path","text_path"))):
        raise ValueError("Existing IR document identity differs from the reviewed source")
    if digest(sb.get_object("ir",existing["storage_path"]) or b"") != item["record"]["sha256"]:
        raise RuntimeError("Resumed IR original read-back hash mismatch")
    if digest(sb.get_object("ir",existing["text_path"]) or b"") != digest(item["text"]):
        raise RuntimeError("Resumed IR extracted-text read-back hash mismatch")
    state["outcomes"][existing["id"]] = item["quality"]


def publish_company(root, ticker):
    prepared = prepare_documents(root,ticker)
    if not prepared:
        return {"ticker":ticker,"status":"pending","reason":"No original financial document passed all source checks"}
    sb = Supabase()
    with AggregateLease(sb,name=f"ir-company:{ticker}") as lease:
        sb._aggregate_lease = lease
        companies = sb.select("companies",f"select=ticker,cik&ticker=eq.{ticker}")
        if len(companies) != 1 or companies[0]["cik"] != BY_TICKER[ticker]["cik"]:
            raise ValueError("Publish verified core company before its IR documents")
        rows = sb.select("ir_documents",f"select=*&ticker=eq.{ticker}")
        state = ir._read_checkpoint(sb,ticker)
        have = {r["sha256"] for r in rows}
        added = 0
        for item in prepared:
            record = item["record"]
            if record["sha256"] in have:
                existing = next(row for row in rows if row["sha256"] == record["sha256"])
                restore_missing_extraction(sb,item,existing,state)
                continue
            collisions = sb.select("ir_documents",f"select=id,ticker&id=eq.{record['id']}")
            if any(row["ticker"] != ticker for row in collisions):
                raise ValueError("Financial document ID belongs to another issuer")
            sb.put_object("ir",record["storage_path"],item["body"],content_type=record["content_type"],cache="max-age=31536000,immutable")
            sb.put_object("ir",record["text_path"],item["text"],cache="max-age=31536000,immutable")
            if digest(sb.get_object("ir",record["storage_path"]) or b"") != record["sha256"]:
                raise RuntimeError("IR original read-back hash mismatch")
            if digest(sb.get_object("ir",record["text_path"]) or b"") != digest(item["text"]):
                raise RuntimeError("IR extracted-text read-back hash mismatch")
            sb.upsert("ir_documents",[record],"id")
            state["outcomes"][record["id"]] = item["quality"]
            have.add(record["sha256"])
            added += 1
        if not state["scope"]:
            state["scope"] = {"web":True,"sec":True,"since_year":2024,"sec_since":"2023-11-05","history":"recent"}
        limitation = "Initial source-checked financial releases; historical archive enumeration remains incomplete."
        if limitation not in state["limits"]:
            state["limits"].append(limitation)
        rows = sb.select("ir_documents",f"select=*&ticker=eq.{ticker}")
        for row in rows:
            row["extraction"] = state["outcomes"].get(row["id"],{"status":"unverified","limits":[],"errors":[]})
        rows.sort(key=lambda r:(r.get("published") or "",r["id"]),reverse=True)
        coverage = ir._coverage(state,rows)
        state["coverage"] = coverage
        ir._save_checkpoint(sb,state)
        bundle = {"ticker":ticker,"generated":now_iso(),"site":None,"results_page":None,"documents":rows,"coverage":coverage}
        sb.put_json(f"ir/{ticker}.json",bundle,cache="max-age=600")
        published = sb.get_json(f"ir/{ticker}.json")
        if published != bundle:
            raise RuntimeError("IR library read-back mismatch")
        result = {"ticker":ticker,"status":"published","added":added,"documents":len(rows),"coverage":coverage}
        redirects = [item["redirect"] for item in prepared if item["redirect"]]
        if redirects:
            result["redirects"] = redirects
        atomic_write(Path(root)/"ir-publication"/f"{ticker}.json",encoded(result))
        return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root",type=Path,required=True)
    parser.add_argument("--tickers",nargs="*")
    parser.add_argument("--workers",type=int,default=4)
    args=parser.parse_args(argv)
    tickers=args.tickers or [r["ticker"] for r in ADDITIONAL_REGISTRY["companies"]]
    def one(ticker):
        try:
            return publish_company(args.root,ticker)
        except Exception as error:
            return {"ticker":ticker,"status":"failed","error":str(error)[:400]}
    results=[]
    with ThreadPoolExecutor(max_workers=max(1,min(6,args.workers))) as pool:
        for result in pool.map(one,tickers):
            results.append(result)
            print(json.dumps({k:v for k,v in result.items() if k!="coverage"}),flush=True)
    report={"cohort_id":ADDITIONAL_REGISTRY["cohort_id"],"at":now_iso(),"companies":len(results),
            "published":sum(r["status"]=="published" for r in results),"results":results}
    atomic_write(args.root/"ir-publication-report.json",encoded(report))
    if report["published"]!=len(results):
        raise SystemExit(1)


if __name__=="__main__":
    main()
