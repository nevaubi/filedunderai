"""Validate and add archived Yahoo prices for the approved additional cohort.

The default/dry-run path is entirely offline and never imports credential-loading
plumbing. --publish uses the existing Supabase client and aggregate lease. Raw
responses and their exact manifests must pass remote readback before any price
file or database bar is exposed. No provider fetch or missing-value fill occurs.
"""
import argparse
import copy
import datetime as dt
import gzip
import hashlib
import json
import math
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from .universe import BY_TICKER, COHORT_OF, SP500, registry_by_id, registry_for

META_KEYS = ("symbol", "longName", "shortName", "currency", "exchangeName", "regularMarketPrice",
             "fiftyTwoWeekHigh", "fiftyTwoWeekLow", "gmtoffset", "regularMarketTime",
             "exchangeTimezoneName", "instrumentType")
CLOSES_PATH = "screener/closes.json.gz"
STATS_PARTS = ["all"] + [f"p{i}" for i in range(8)]
ATTRIBUTION = "Market prices, quotes, dividends and splits: Yahoo Finance chart API. These are not SEC financial facts."


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(body):
    return hashlib.sha256(body).hexdigest()


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def atomic_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".part")
    temp.write_bytes(encoded(obj))
    temp.replace(path)


def norm(ticker):
    return str(ticker).strip().upper().replace(".", "-")


def finite(value, positive=False):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and (not positive or value > 0))


def day_at(value):
    if not finite(value):
        return None
    try:
        return dt.datetime.fromtimestamp(value, dt.timezone.utc).date().isoformat()
    except (ValueError, OverflowError, OSError):
        return None


def local_bytes(root, path):
    root = Path(root).resolve()
    path = Path(path)
    path = (path if path.is_absolute() else root / path).resolve()
    if not path.is_relative_to(root):
        raise ValueError("Source path leaves the approved prices directory")
    return path.read_bytes()


def approved(company):
    ticker = company.get("ticker")
    if ticker not in COHORT_OF or ticker in SP500:
        raise ValueError("Price backfill scope requires an approved additional issuer")
    if company.get("cik") != BY_TICKER[ticker]["cik"] or company.get("currency") != "USD":
        raise ValueError("Price backfill issuer CIK or currency mismatch")


def parse_chart(company, body, raw):
    """Pure form of the collection parser; omit incomplete bars without filling.

    Valid quote.close values may still enter the screener when another OHLCV
    field is absent. Neither adjusted-close nor an extra split adjustment is used.
    """
    encoding = raw.get("headers", {}).get("content-encoding", "").lower()
    if encoding == "gzip":
        body = gzip.decompress(body)
    elif encoding not in ("", "identity"):
        raise ValueError("Unsupported source HTTP content encoding")
    chart = json.loads(body).get("chart") or {}
    results = chart.get("result")
    if chart.get("error") or not isinstance(results, list) or len(results) != 1:
        raise ValueError("Expected one successful Yahoo chart source result")
    result = results[0]
    meta = result.get("meta") or {}
    if (norm(meta.get("symbol", "")) != norm(company["ticker"])
            or meta.get("currency") != company["currency"]
            or meta.get("range") != "10y" or meta.get("dataGranularity") != "1d"):
        raise ValueError("Yahoo source symbol, currency, range or interval mismatch")
    timestamps = result.get("timestamp") or []
    quote = (result.get("indicators") or {}).get("quote") or []
    if not isinstance(timestamps, list) or not quote or not isinstance(quote[0], dict):
        raise ValueError("Missing source timestamps or OHLCV arrays")
    quote = quote[0]
    fields = ("open", "high", "low", "close", "volume")
    arrays = {f: quote.get(f) if isinstance(quote.get(f), list) else [] for f in fields}
    missing = {f: {"array_absent": f not in quote, "array_length": len(arrays[f]), "null_count": 0,
                   "nonfinite_or_nonnumeric_count": 0, "out_of_range_count": 0} for f in fields}
    issues = {k: 0 for k in ("invalid_timestamp_count", "duplicate_timestamp_count", "duplicate_date_count",
        "nonincreasing_timestamp_count", "nonpositive_ohlc_count", "negative_volume_count",
        "inconsistent_ohlc_count", "invalid_dividend_count", "invalid_split_count")}
    bars, omitted, closes = [], [], {}
    seen_times, seen_days, previous = set(), set(), None
    for i, timestamp in enumerate(timestamps):
        day, values, reasons = day_at(timestamp), {}, []
        for field in fields:
            value = arrays[field][i] if i < len(arrays[field]) else None
            if i >= len(arrays[field]):
                missing[field]["out_of_range_count"] += 1
                reasons.append(field + "_out_of_range")
            elif value is None:
                missing[field]["null_count"] += 1
                reasons.append(field + "_null")
            elif not finite(value):
                missing[field]["nonfinite_or_nonnumeric_count"] += 1
                reasons.append(field + "_invalid")
            values[field] = value
        if day is None:
            issues["invalid_timestamp_count"] += 1
            reasons.append("timestamp_invalid")
        else:
            for condition, key, reason in (
                (timestamp in seen_times, "duplicate_timestamp_count", "duplicate_timestamp"),
                (day in seen_days, "duplicate_date_count", "duplicate_date"),
                (previous is not None and timestamp <= previous, "nonincreasing_timestamp_count", "nonincreasing_timestamp")):
                if condition:
                    issues[key] += 1
                    reasons.append(reason)
            seen_times.add(timestamp)
            seen_days.add(day)
            previous = timestamp
        if any(finite(values[f]) and values[f] <= 0 for f in fields[:-1]):
            issues["nonpositive_ohlc_count"] += 1
            reasons.append("nonpositive_ohlc")
        if finite(values["volume"]) and values["volume"] < 0:
            issues["negative_volume_count"] += 1
            reasons.append("negative_volume")
        if all(finite(values[f], positive=True) for f in fields[:-1]):
            o, h, low, c = [values[f] for f in fields[:-1]]
            if h < max(o, low, c) or low > min(o, h, c):
                issues["inconsistent_ohlc_count"] += 1
                reasons.append("inconsistent_ohlc")
        if day and finite(values["close"], positive=True):
            closes[day] = round(values["close"], 4)
        if reasons:
            omitted.append({"index": i, "timestamp": timestamp, "date": day, "reasons": reasons})
        else:
            bars.append([timestamp, *[round(values[f], 4) for f in fields[:-1]], values["volume"]])
    events, dividends, splits = result.get("events") or {}, [], []
    for key, event in (events.get("dividends") or {}).items():
        day = day_at(event.get("date"))
        if day is None:
            try:
                day = day_at(int(key))
            except (TypeError, ValueError):
                pass
        amount = event.get("amount")
        if day and finite(amount) and amount >= 0:
            dividends.append([day, round(float(amount), 6)])
        else:
            issues["invalid_dividend_count"] += 1
    for event in (events.get("splits") or {}).values():
        day = day_at(event.get("date"))
        num, den = event.get("numerator"), event.get("denominator")
        if day and finite(num, True) and finite(den, True):
            splits.append([day, num / den])
        else:
            issues["invalid_split_count"] += 1
    if not bars or not closes:
        raise ValueError("No complete source price bars")
    # Ambiguous dates/corporate actions cannot safely enter relational history.
    if any(issues.values()):
        raise ValueError("Invalid/ambiguous source price or corporate-action data")
    retrieved = dt.datetime.fromisoformat(raw["retrieved_at"])
    if retrieved.tzinfo is None:
        raise ValueError("Source retrieval timestamp needs a timezone")
    public = {"meta": {k: meta.get(k) for k in META_KEYS}, "d": bars, "i": [], "dv": sorted(dividends)}
    screen = {"ticker": company["ticker"], "closes": dict(sorted(closes.items())), "splits": sorted(splits),
              "fetched": day_at(retrieved.timestamp()), "source": "Yahoo Finance", "raw_body_sha256": raw["body_sha256"]}
    quality = {"metadata": meta, "timestamp_count": len(timestamps), "daily_bar_count": len(bars),
               "valid_close_count": len(closes), "first_date": min(closes), "latest_date": max(closes),
               "first_complete_bar_date": day_at(bars[0][0]), "latest_complete_bar_date": day_at(bars[-1][0]),
               "dividend_count": len(dividends), "split_count": len(splits), "missing_fields": missing,
               "data_issues": issues, "omitted_public_bars": omitted}
    return public, screen, quality


def validate_company(root, company, manifest, cohort_hash):
    approved(company)
    ticker = company["ticker"]
    if (manifest.get("ticker") != ticker or manifest.get("cik") != company["cik"]
            or manifest.get("name") != company["name"] or manifest.get("expected_currency") != company["currency"]
            or manifest.get("cohort_id") != registry_for(ticker)["cohort_id"]
            or manifest.get("cohort_sha256") != cohort_hash or manifest.get("status") != "ok"
            or manifest.get("errors") or manifest.get("source", {}).get("provider") != "Yahoo Finance"):
        raise ValueError("Price manifest issuer, source or cohort identity mismatch")
    raw = manifest["raw"]
    url = urlsplit(raw["url"])
    query = parse_qs(url.query)
    if (url.scheme != "https" or url.hostname != "query2.finance.yahoo.com" or url.username or url.password
            or url.fragment or unquote(url.path) != "/v8/finance/chart/" + norm(ticker)
            or query != {"range": ["10y"], "interval": ["1d"], "includePrePost": ["false"], "events": ["div,splits"]}
            or raw.get("requested_url", raw["url"]) != raw["url"] or raw.get("http_status") != 200
            or manifest.get("url") != raw["url"] or manifest.get("retrieved_at") != raw["retrieved_at"]):
        raise ValueError("Yahoo source URL, retrieval metadata or HTTP status mismatch")
    packed = local_bytes(root, raw["path"])
    if digest(packed) != raw["gzip_sha256"] or len(packed) != raw["gzip_bytes"]:
        raise ValueError("Raw gzip archive SHA/length mismatch")
    try:
        body = gzip.decompress(packed)
    except (OSError, EOFError) as error:
        raise ValueError("Invalid raw gzip archive") from error
    if digest(body) != raw["body_sha256"] or len(body) != raw["body_bytes"]:
        raise ValueError("Raw response body SHA/length mismatch")
    public, screen, quality = parse_chart(company, body, raw)
    for key, value in quality.items():
        if manifest.get(key) != value:
            raise ValueError(f"Manifest {key} does not correspond to the source body")
    derived_bytes = {}
    for kind, expected in (("public", public), ("screener", screen)):
        meta = manifest["derived"][kind]
        data = local_bytes(root, meta["path"])
        if digest(data) != meta["sha256"] or len(data) != meta["bytes"]:
            raise ValueError(f"Derived {kind} SHA/length mismatch")
        if json.loads(data) != expected:
            raise ValueError(f"Derived {kind} does not correspond to the source body")
        derived_bytes[kind] = data
    manifest_path = Path(root) / "manifests" / f"{ticker}.json"
    manifest_bytes = manifest_path.read_bytes()
    if json.loads(manifest_bytes) != manifest:
        raise ValueError("Company manifest differs from the aggregate manifest")
    manifest_sha = digest(manifest_bytes)
    archive_path = f"market-prices/yahoo/{ticker}/{raw['body_sha256']}.response.bin.gz"
    source_path = f"backfills/{registry_for(ticker)['cohort_id']}/prices/{ticker}/{manifest_sha}.source.json"
    rows = [{"symbol": ticker, "d": day_at(b[0]), "o": b[1], "h": b[2], "l": b[3], "c": b[4], "v": b[5]} for b in public["d"]]
    return {"source_root": str(Path(root).resolve()), "company": copy.deepcopy(company),
            "ticker": ticker, "cik": company["cik"], "cohort_id": manifest["cohort_id"], "cohort_sha256": cohort_hash,
            "manifest": manifest, "manifest_bytes": manifest_bytes, "manifest_sha256": manifest_sha,
            "raw_gzip": packed, "raw_path": archive_path, "source_path": source_path,
            "public": public, "screener": screen, "public_bytes": derived_bytes["public"], "rows": rows}


def validate_bundle(cohort_path, prices_dir, tickers=None, log=None):
    cohort_body = Path(cohort_path).read_bytes()
    cohort, cohort_hash = json.loads(cohort_body), digest(cohort_body)
    try:
        registry = registry_by_id(cohort.get("cohort_id"))
    except ValueError:
        raise ValueError("Cohort file differs from the approved registry SHA/identity")
    if cohort_hash != registry["cohort_sha256"]:
        raise ValueError("Cohort file differs from the approved registry SHA/identity")
    companies = cohort["companies"]
    actual = {c["ticker"]: c["cik"] for c in companies}
    expected = {c["ticker"]: c["cik"] for c in registry["companies"]}
    size = len(registry["companies"])
    if len(companies) != size or len(actual) != size or actual != expected:
        raise ValueError(f"Expected exactly the approved {size}-company cohort")
    tickers = list(actual) if tickers is None else list(tickers)
    if not tickers or len(tickers) != len(set(tickers)) or not set(tickers) <= set(actual):
        raise ValueError("Requested ticker scope is outside the approved cohort")
    root = Path(prices_dir).resolve()
    aggregate = json.loads((root / "manifest.json").read_bytes())
    records = aggregate.get("companies") or []
    if (aggregate.get("status") != "complete" or aggregate.get("cohort_sha256") != cohort_hash
            or aggregate.get("cohort_id") != cohort["cohort_id"] or len(records) != size
            or {r["ticker"] for r in records} != set(actual)):
        raise ValueError("Collection manifest is incomplete or outside the approved cohort")
    by_ticker = {c["ticker"]: c for c in companies}
    manifests = {r["ticker"]: r for r in records}
    prepared = []
    for ticker in tickers:
        prepared.append(validate_company(root, by_ticker[ticker], manifests[ticker], cohort_hash))
        if log and (len(prepared) % 50 == 0 or len(prepared) == len(tickers)):
            log({"phase": "validated", "companies": len(prepared), "expected": len(tickers)})
    return prepared, cohort_hash


def merge_close_cache(raw_cache, items):
    if not raw_cache:
        raise ValueError("Existing screener close cache is required; refusing an empty replacement")
    try:
        cache = json.loads(gzip.decompress(raw_cache))
    except OSError:
        cache = json.loads(raw_cache)
    dates = cache.get("dates")
    if (not isinstance(dates, list) or not dates or dates != sorted(set(dates))
            or not isinstance(cache.get("s"), dict)):
        raise ValueError("Invalid existing screener close calendar/series")
    for day in dates:
        dt.date.fromisoformat(day)
    if any(not isinstance(v, list) or len(v) != len(dates) for v in cache["s"].values()):
        raise ValueError("Existing screener series length differs from its calendar")
    out = copy.deepcopy(cache)
    for key in ("splits", "fetched"):
        if key not in out:
            out[key] = {}
        if not isinstance(out[key], dict):
            raise ValueError("Invalid existing screener metadata")
    for item in items:
        ticker, screen = item["ticker"], item["screener"]
        aligned = [screen["closes"].get(day) for day in dates]
        if ticker in out["s"] and out["s"][ticker] != aligned:
            raise ValueError(f"Existing screener prices conflict with the archived source for {ticker}")
        out["s"][ticker] = aligned
        out["splits"][ticker] = screen["splits"]
        out["fetched"][ticker] = screen["fetched"]
    out["updated"] = now()
    return out


def _stats_plan(sb, items):
    from . import prices
    spy = sb.get_json("prices/SPY.json")
    if (not isinstance(spy, dict) or not spy.get("d")
            or norm((spy.get("meta") or {}).get("symbol", "")) != "SPY"):
        raise ValueError("Existing SPY daily price file is required for statistics")
    paths = ["meta/pricestats.json"] + [f"meta/pricestats/{key}.json" for key in STATS_PARTS]
    with ThreadPoolExecutor(max_workers=10) as pool:
        docs = list(pool.map(sb.get_json, paths))
    base = docs[0] if docs[0] is not None else {}
    if not isinstance(base, dict):
        raise ValueError("Invalid existing base price statistics")
    parts, visible = {}, dict(base)
    for key, doc in zip(STATS_PARTS, docs[1:]):
        if doc is None:
            continue
        if not isinstance(doc, dict) or not isinstance(doc.get("stats"), dict):
            raise ValueError("Invalid existing price statistics part")
        parts[key] = doc
        for ticker, values in doc["stats"].items():
            if isinstance(values, dict) and (ticker not in visible or not isinstance(visible[ticker], dict)
                    or (values.get("as_of") or "") >= (visible[ticker].get("as_of") or "")):
                visible[ticker] = values
    merged, overlay = copy.deepcopy(base), copy.deepcopy(parts.get("p7", {"parts": 8, "stats": {}}))
    incoming, need_overlay = {}, False
    for item in items:
        ticker, bars = item["ticker"], item["public"]["d"]
        value = prices.stats(bars, spy["d"])
        value["spark"] = [round(b[4], 2) for b in bars[-63:]]
        prior = visible.get(ticker)
        if isinstance(prior, dict) and (prior.get("as_of") or "") > value["as_of"]:
            # A later existing quote must never be replaced by the archived run.
            incoming[ticker] = prior
            continue
        merged[ticker] = value
        incoming[ticker] = value
        # The last recognized part wins equal dates. An arbitrary new part is
        # ignored by load_pricestats; preserve all old entries in this overlay.
        conflicting_part = any(isinstance(doc["stats"].get(ticker), dict)
            and (doc["stats"][ticker].get("as_of") or "") >= value["as_of"]
            and doc["stats"][ticker] != value for doc in parts.values())
        if conflicting_part:
            overlay["stats"][ticker] = value
            need_overlay = True
    plan = [("meta/pricestats.json", merged)]
    if need_overlay:
        overlay["generated"] = now()
        plan.append(("meta/pricestats/p7.json", overlay))
    return plan, incoming


def _parallel(items, fn, workers, phase, log=None):
    results, errors = [], []
    with ThreadPoolExecutor(max_workers=max(1, min(12, workers))) as pool:
        futures = {pool.submit(fn, item): item["ticker"] for item in items}
        for future in as_completed(futures):
            ticker = futures[future]
            try:
                results.append(future.result())
            except Exception as error:
                errors.append({"ticker": ticker, "error": str(error)[:400]})
            done = len(results) + len(errors)
            if log and (done % 25 == 0 or done == len(items)):
                log({"phase": phase, "completed": done, "expected": len(items), "failed": len(errors)})
    if errors:
        raise RuntimeError(f"{phase} failed; completed {len(results)}/{len(items)}: " + json.dumps(errors, separators=(",", ":")))
    return results


def _verified_put(sb, bucket, path, body, content_type="application/json"):
    cache = "no-store" if bucket == "raw" or path.startswith("meta/") else "max-age=300"
    sb.put_object(bucket, path, body, content_type=content_type, cache=cache)
    if digest(sb.get_object(bucket, path) or b"") != digest(body):
        raise RuntimeError(f"Source/public read-back SHA mismatch: {bucket}/{path}")


def _publish(sb, items, cohort_hash, workers=10, log=None):
    from . import jobs
    if not items or len(items) != len({i["ticker"] for i in items}):
        raise ValueError("Publish requires unique approved additional issuers")
    for item in items:
        approved({"ticker": item["ticker"], "cik": item["cik"], "currency": "USD"})
        if item["cohort_sha256"] != cohort_hash or item["cohort_id"] != registry_for(item["ticker"])["cohort_id"]:
            raise ValueError("Prepared price cohort mismatch")
    tickers = [i["ticker"] for i in items]
    # Preflight every shared input before any writes. Missing core companies or
    # a failed cache read cannot silently become an empty aggregate replacement.
    companies = sb.select("companies", "select=ticker,cik&ticker=in.(" + ",".join(tickers) + ")")
    if {r["ticker"]: r["cik"] for r in companies} != {i["ticker"]: i["cik"] for i in items}:
        raise ValueError("Publish verified core company identities before their prices")
    documents = []
    for item in items:
        raw = item["manifest"]["raw"]
        for path, kind, size, sha in (
            (item["raw_path"], "yahoo_prices", raw["body_bytes"], raw["body_sha256"]),
            (item["source_path"], "yahoo_price_manifest", len(item["manifest_bytes"]), item["manifest_sha256"])):
            documents.append({"id": path, "ticker": item["ticker"], "accession": None, "kind": kind,
                              "source_url": raw["url"], "storage_path": "raw/" + path,
                              "bytes": size, "sha256": sha, "fetched_at": raw["retrieved_at"]})
    owners = {row["id"]: row["ticker"] for row in documents}
    if len(owners) != len(documents):
        raise ValueError("Generated global document identities are not unique")
    # Document IDs are global, even though the path includes a ticker. Verify
    # ownership rather than relying on that naming convention. Read failures
    # propagate, and every ownership query finishes before any archive write.
    ids = list(owners)
    def check_document_owners(batch):
        existing = sb.select("documents", "select=id,ticker&id=in.(" + ",".join(batch) + ")")
        if any(row.get("id") not in owners or row.get("ticker") != owners[row["id"]] for row in existing):
            raise ValueError("Global price document identity belongs to another issuer")
    with ThreadPoolExecutor(max_workers=max(1, min(12, workers))) as pool:
        list(pool.map(check_document_owners, [ids[i:i + 25] for i in range(0, len(ids), 25)]))
    stats_plan, expected_stats = _stats_plan(sb, items)
    closes = merge_close_cache(sb.get_object("raw", CLOSES_PATH), items)
    def inspect_existing(item):
        old = sb.get_object("public-data", f"prices/{item['ticker']}.json")
        if old and json.loads(old) != item["public"]:
            raise ValueError("Existing public prices conflict with the archived batch")
        return item["ticker"]
    _parallel(items, inspect_existing, workers, "preflight", log)

    def archive(item):
        _verified_put(sb, "raw", item["raw_path"], item["raw_gzip"], "application/gzip")
        _verified_put(sb, "raw", item["source_path"], item["manifest_bytes"])
        # The source body hash is checked independently of the gzip transport.
        raw = item["manifest"]["raw"]
        return {"ticker": item["ticker"], "raw_path": item["raw_path"], "source_path": item["source_path"],
                "raw_sha256": raw["body_sha256"], "manifest_sha256": item["manifest_sha256"]}
    source_checks = _parallel(items, archive, workers, "source_readbacks", log)
    # All source objects have now passed readback; only cohort-owned document IDs
    # and ticker/date keys are eligible for the following upserts.
    sb.upsert("documents", documents, "id", batch=500)

    def publish_one(item):
        ticker = item["ticker"]
        sb.upsert("prices_daily", item["rows"], "symbol,d", batch=2000)
        rows = sb.select("prices_daily", f"select=symbol,d,o,h,l,c,v&symbol=eq.{ticker}&d=gte.{item['rows'][0]['d']}&d=lte.{item['rows'][-1]['d']}&order=d.asc")
        if sorted(rows, key=lambda r: r["d"]) != item["rows"]:
            raise RuntimeError("prices_daily read-back differs from validated source bars")
        _verified_put(sb, "public-data", f"prices/{ticker}.json", item["public_bytes"])
        return {"ticker": ticker, "status": "published", "bars": len(rows),
                "latest_date": item["rows"][-1]["d"], "manifest_sha256": item["manifest_sha256"]}
    results = _parallel(items, publish_one, workers, "price_readbacks", log)
    # Merge only after every selected company has been read back successfully.
    for path, obj in stats_plan:
        _verified_put(sb, "public-data", path, encoded(obj))
    _verified_put(sb, "raw", CLOSES_PATH, gzip.compress(encoded(closes), mtime=0), "application/gzip")
    visible = jobs.load_pricestats(sb)
    if any(visible.get(t) != value for t, value in expected_stats.items()):
        raise RuntimeError("Price statistics are hidden by an existing part after publication")
    cohort_id = registry_for(items[0]["ticker"])["cohort_id"]
    if any(registry_for(i["ticker"])["cohort_id"] != cohort_id for i in items):
        raise ValueError("A price batch must contain issuers from exactly one cohort")
    report = {"schema_version": 1, "mode": "published", "cohort_id": cohort_id,
              "cohort_sha256": cohort_hash, "completed_at": now(), "published": len(results),
              "daily_bars": sum(r["bars"] for r in results), "source_objects_verified": 2 * len(items),
              "source": "Yahoo Finance", "attribution": ATTRIBUTION,
              "latest_date_counts": dict(Counter(r["latest_date"] for r in results)),
              "screener_calendar_preserved": True, "screener_dates": len(closes["dates"]),
              "stats_paths": [p for p, _ in stats_plan], "source_readbacks": source_checks,
              "companies": sorted(results, key=lambda r: r["ticker"])}
    checkpoint = f"backfills/{cohort_id}/prices/publication-report.json"
    _verified_put(sb, "raw", checkpoint, encoded(report))
    return report


def publish_prepared(sb, items, cohort_hash, workers=10, log=None):
    """Reconstitute prepared items from their sources before acquiring a lease.

    This callable boundary cannot trust a mutable dictionary's paths, serialized
    payload or row.symbol. Use fresh, independently checked objects for every
    subsequent write; reject any changed prepared item before remote operations.
    """
    canonical = []
    for item in items:
        fresh = validate_company(item["source_root"], copy.deepcopy(item["company"]),
                                 json.loads(item["manifest_bytes"]), cohort_hash)
        if fresh != item:
            raise ValueError("Prepared price data no longer corresponds to its validated source/scope")
        canonical.append(fresh)
    from .common import serialized_aggregates
    return serialized_aggregates(_publish)(sb, canonical, cohort_hash, workers=workers, log=log)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cohort", type=Path, required=True)
    parser.add_argument("--prices-dir", type=Path, required=True)
    parser.add_argument("--tickers", nargs="+")
    parser.add_argument("--workers", type=int, default=10)
    parser.add_argument("--report", type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--publish", action="store_true")
    args = parser.parse_args(argv)
    log = lambda row: print(json.dumps(row, separators=(",", ":")), flush=True)
    report_path = args.report or args.prices_dir / ("publication-report.json" if args.publish else "publisher-validation.json")
    try:
        items, cohort_hash = validate_bundle(args.cohort, args.prices_dir, args.tickers, log)
        if not items:
            raise ValueError("No validated price items")
        cohort_ids = {registry_for(i["ticker"])["cohort_id"] for i in items}
        if len(cohort_ids) != 1:
            raise ValueError("Validated price items span more than one cohort")
        report = {"schema_version": 1, "mode": "offline_validation", "validated_at": now(),
                  "cohort_id": cohort_ids.pop(), "cohort_sha256": cohort_hash,
                  "companies": len(items), "daily_bars": sum(len(i["rows"]) for i in items),
                  "source": "Yahoo Finance", "attribution": ATTRIBUTION,
                  "latest_date_counts": dict(Counter(i["rows"][-1]["d"] for i in items)),
                  "omitted_bars": sum(len(i["manifest"]["omitted_public_bars"]) for i in items),
                  "tickers": [i["ticker"] for i in items], "all_checks_passed": True}
        if args.publish:
            from .common import Supabase
            report = publish_prepared(Supabase(), items, cohort_hash, args.workers, log)
        atomic_json(report_path, report)
        log({k: v for k, v in report.items() if k not in ("tickers", "source_readbacks", "companies")})
    except Exception as error:
        atomic_json(report_path, {"mode": "publication_failed" if args.publish else "validation_failed",
                                 "at": now(), "error": str(error), "all_checks_passed": False})
        raise


if __name__ == "__main__":
    main()
