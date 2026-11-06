"""ETL jobs. Each job is resumable and writes both Postgres rows and the JSON bundles the site reads."""
import datetime as dt
import json
import hashlib
import time
from urllib.parse import quote
from concurrent.futures import ThreadPoolExecutor

from . import docs, fundamentals, insiders, macro, news, prices, sec, summaries
from .common import Run, Supabase, now_iso, sec_get_raw, serialized_aggregates
from .universe import ADDITIONAL_LIST, BY_TICKER, MARKET_SYMBOLS, SECTOR_ETF, SP100, SP500, UNIVERSE, UNIVERSE_METADATA

PIPELINE_VERSION = fundamentals.NORMALIZATION_VERSION

IR_URLS = {
    "AAPL": "https://investor.apple.com/", "MSFT": "https://www.microsoft.com/en-us/investor", "NVDA": "https://investor.nvidia.com/",
    "AMZN": "https://ir.aboutamazon.com/", "GOOGL": "https://abc.xyz/investor/", "META": "https://investor.atmeta.com/", "TSLA": "https://ir.tesla.com/",
    "COST": "https://investor.costco.com/", "JPM": "https://www.jpmorganchase.com/ir", "AVGO": "https://investors.broadcom.com/",
}
ITEMS = {"1.01": "Material agreement", "2.02": "Results of operations", "2.03": "Debt obligation", "5.02": "Officer/director change", "5.03": "Bylaw amendment",
         "5.07": "Shareholder vote", "7.01": "Reg FD disclosure", "8.01": "Other events", "9.01": "Exhibits"}


def describe(f):
    if f["f"] == "10-K":
        return "Annual report"
    if f["f"] == "10-Q":
        return "Quarterly report"
    if f["f"].startswith("8-K"):
        return " · ".join(ITEMS.get(x, "Item " + x) for x in (f["i"] or "").split(",") if x and x != "9.01") or "Current report"
    return {"4": "Insider transaction", "144": "Proposed insider sale", "DEF 14A": "Proxy statement", "S-8": "Employee plan registration",
            "SC 13G": "Beneficial owner >5%", "SC 13G/A": "Beneficial owner >5%", "3": "Initial insider holdings"}.get(f["f"], f["x"] or f["f"])


def _cache(sb, path, default, strict=False):
    try:
        return sb.get_json(path) or default
    except Exception:
        if strict:
            raise
        return default


def preserve_filing_owners(sb, rows):
    """Preserve filing ownership and emit one unambiguous row per accession.

    Local StageStore writes are checked independently by the backfill publisher.
    Live stores must provide a successful owner lookup; never treat its failure
    as an empty table. Unique existing-universe rows retain their original path.
    A shared incoming accession requires its existing owner, or a review hold.
    """
    rows = list(rows)
    if getattr(sb, "stage_only", False) is True:
        return rows, []
    grouped = {}
    for row in rows:
        grouped.setdefault(row["accession"], []).append(row)
    repeated = {accession for accession, group in grouped.items() if len(group) > 1}
    additional = {t for t, *_ in ADDITIONAL_LIST}
    guarded = [r for r in rows if r["ticker"] in additional or r["accession"] in repeated]
    if not guarded:
        return rows, []
    lookup = getattr(sb, "select", None)
    if not callable(lookup):
        raise RuntimeError("Live filing ownership lookup is unavailable")
    accessions = list(dict.fromkeys(r["accession"] for r in guarded))
    owners = {}
    for offset in range(0, len(accessions), 250):
        batch = accessions[offset:offset + 250]
        existing = lookup("filings", "select=accession,ticker&accession=in.(" + quote(",".join(batch), safe=",") + ")")
        if not isinstance(existing, list):
            raise RuntimeError("Invalid live filing ownership lookup result")
        for row in existing:
            if (not isinstance(row, dict) or row.get("accession") not in batch or not row.get("ticker")
                    or (row["accession"] in owners and owners[row["accession"]] != row["ticker"])):
                raise RuntimeError("Incomplete or conflicting live filing ownership lookup")
            owners[row["accession"]] = row["ticker"]
    kept, held = [], []
    for accession, group in grouped.items():
        owner = owners.get(accession)
        eligible, held_tickers = [], set()
        for row in group:
            if (row["ticker"] in additional or accession in repeated) and owner is not None and owner != row["ticker"]:
                if row["ticker"] not in held_tickers:
                    held.append({"accession": accession, "ticker": row["ticker"], "owner_ticker": owner})
                    held_tickers.add(row["ticker"])
            else:
                eligible.append(row)
        candidates = sorted({row["ticker"] for row in eligible})
        if len(candidates) > 1:
            held.extend({"accession": accession, "ticker": ticker, "owner_ticker": owner,
                         "reason": "ambiguous_incoming_owner", "candidate_tickers": candidates} for ticker in candidates)
            continue
        if not eligible:
            continue
        # Fetch timestamps do not change filing identity. Other disagreement
        # needs review; choosing whichever worker completed last is unsafe.
        identity = {k: v for k, v in eligible[0].items() if k != "updated_at"}
        if any({k: v for k, v in row.items() if k != "updated_at"} != identity for row in eligible[1:]):
            held.append({"accession": accession, "ticker": candidates[0], "owner_ticker": owner,
                         "reason": "conflicting_incoming_filing"})
            continue
        kept.append(eligible[0])
    return kept, held


def _held_filing_message(row):
    if row.get("reason") == "ambiguous_incoming_owner":
        return (f"Held filing alias {row['accession']} for {row['ticker']}; owner unassigned because the incoming "
                f"batch lists multiple issuers ({', '.join(row['candidate_tickers'])}).")
    if row.get("reason") == "conflicting_incoming_filing":
        return f"Held filing {row['accession']} for {row['ticker']}; incoming filing details disagree."
    return f"Held filing alias {row['accession']} for {row['ticker']}; existing owner {row['owner_ticker']} preserved."


def _filing_owner_stats(stats, held):
    if stats is not None and held:
        stats.setdefault("held_filing_aliases", []).extend(held)
        stats["held_filing_alias_count"] = len(stats["held_filing_aliases"])


FACT_KEY = ("ticker", "statement", "label", "kind", "period_end")
NONADDITIVE_FACT_LABELS = sec.NON_ADDITIVE | {"Basic shares", "Weighted average shares", "Weighted average diluted shares"}
NONADDITIVE_FACT_CONCEPTS = {"EarningsPerShareBasic", "EarningsPerShareDiluted", "EarningsPerShareBasicAndDiluted",
                            "WeightedAverageNumberOfDilutedSharesOutstanding", "WeightedAverageNumberOfSharesOutstandingBasic"}


def statement_fact_rows(ticker, statements):
    rows = {}
    for statement, lines in statements.items():
        for line in lines:
            for kind, bucket in (("A", "annual"), ("Q", "quarterly")):
                for point in line[bucket]:
                    rows[(statement, line["label"], kind, point["end"])] = {
                        "ticker": ticker, "statement": statement, "label": line["label"], "kind": kind,
                        "period_end": point["end"], "period_start": point.get("start"),
                        "concept": point.get("c") or line["concept"], "unit": line["unit"], "value": point["v"],
                        "accession": point["accn"], "form": point["form"], "filed": point["filed"],
                        "derived": bool(point.get("derived"))}
    return list(rows.values())


def published_concepts(sb, ticker):
    """Concepts of directly reported facts already published, by statement then (label, start, end).

    Local stages and stores without a select (tests, additive staging of new issuers) have none."""
    if getattr(sb, "stage_only", False) is True or not callable(getattr(sb, "select", None)):
        return {}
    try:
        rows = sb.select("facts", f"select=statement,label,period_start,period_end,concept&ticker=eq.{quote(ticker, safe='')}&derived=is.false&order=statement.asc,label.asc,kind.asc,period_end.asc")
    except Exception:
        return {}
    out = {}
    for r in rows if isinstance(rows, list) else []:
        if r.get("concept") and r.get("period_end"):
            out.setdefault(r["statement"], {})[(r["label"], r.get("period_start"), r["period_end"])] = r["concept"]
    return out


def check_reported_fact_replacements(sb, ticker, rows):
    """Reject unsafe overwrites; absent historical rows are never deleted here.

    Local additive stages have no production rows. Live reads must succeed before
    publication, and direct persistence callers receive the same protection.
    """
    from .source_quality import check_source_observation_holds
    rows = list(rows)
    check_source_observation_holds(ticker, rows)
    if getattr(sb, "stage_only", False) is True:
        return
    lookup = getattr(sb, "select", None)
    if not callable(lookup):
        raise RuntimeError("Live fact replacement lookup unavailable")
    existing = lookup("facts", f"select=*&ticker=eq.{quote(ticker, safe='')}&derived=is.false"
                      "&order=statement.asc,label.asc,kind.asc,period_end.asc")
    if not isinstance(existing, list):
        raise RuntimeError("Invalid live fact replacement lookup result")
    before = {tuple(row[key] for key in FACT_KEY): row for row in existing if row.get("derived") is False}
    for candidate in rows:
        prior = before.get(tuple(candidate[key] for key in FACT_KEY))
        if prior is None:
            continue
        reason = None
        if candidate.get("derived"):
            reason = "reported_to_derived"
        else:
            old_date, new_date = prior.get("filed"), candidate.get("filed")
            changed = any(prior.get(key) != candidate.get(key) for key in ("value", "unit", "period_start", "concept"))
            if old_date and (not new_date or new_date < old_date):
                reason = "older_reported_filing"
            elif (candidate.get("label") == "Revenue" and prior.get("concept") == "RevenueFromContractWithCustomerExcludingAssessedTax"
                  and candidate.get("concept") == "Revenues" and BY_TICKER[ticker]["cik"] in sec.REPORTED_TOTAL_REVENUE_CIKS
                  and all(prior.get(key) == candidate.get(key) for key in ("unit", "period_start"))):
                pass  # Reviewed revenue-scope correction (sec.REPORTED_TOTAL_REVENUE_CIKS): component -> reported total.
            elif any(prior.get(key) != candidate.get(key) for key in ("concept", "unit", "period_start")):
                # A newer source date does not establish equal accounting scope.
                reason = "reported_accounting_scope_changed"
            elif changed and (not old_date or not new_date):
                reason = "changed_reported_value_unknown_date"
            elif changed and old_date == new_date:
                reason = "changed_reported_value_same_date"
        if reason:
            key = "/".join(str(candidate[field]) for field in FACT_KEY)
            raise ValueError(f"Financial replacement requires review: {reason}: {key}; prior reported fact preserved")


def persist_normalized_facts(sb, ticker, rows, delete_batch_size=1):
    """Upsert valid replacements, then remove only unsupported legacy nonadditive quarters.

    Every deletion uses the exact primary key plus the backed-up derived flag,
    accession, concept and value. A direct reported replacement at that key survives.
    No other absent historical facts are reconciled here: those need a reviewed repair.
    """
    if type(delete_batch_size) is not int or not 1 <= delete_batch_size <= 20:
        raise ValueError("Fact deletion batches must contain 1–20 rows")
    if delete_batch_size > 1 and not callable(getattr(sb, "delete_nonadditive_fact_batches", None)):
        raise TypeError("Batched fact deletion requires the reviewed repair client")
    rows = list(rows)
    check_reported_fact_replacements(sb, ticker, rows)
    sb.upsert("facts", rows, "ticker,statement,label,kind,period_end", batch=1000)
    existing = sb.select("facts", f"select=*&ticker=eq.{quote(ticker, safe='')}&kind=eq.Q&derived=is.true"
                         "&order=statement.asc,label.asc,kind.asc,period_end.asc")
    obsolete = [r for r in existing if r.get("ticker") == ticker and r.get("kind") == "Q" and r.get("derived") is True
                and (r.get("label") in NONADDITIVE_FACT_LABELS or r.get("concept") in NONADDITIVE_FACT_CONCEPTS)]
    if not obsolete:
        return {"deleted": 0, "backup": None}
    payload = {"ticker": ticker, "pipeline_version": PIPELINE_VERSION, "created_at": now_iso(),
               "reason": "Unsupported cumulative subtraction of per-share or weighted-share observations",
               "keys": [{k:r[k] for k in FACT_KEY} for r in obsolete], "rows": obsolete}
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    digest = hashlib.sha256(body).hexdigest()
    backup = f"repairs/nonadditive-facts/{quote(ticker, safe='')}/{digest}.json"
    sb.put_object("raw", backup, body, cache="no-store")
    if hashlib.sha256(sb.get_object("raw", backup) or b"").hexdigest() != digest:
        raise RuntimeError(f"Nonadditive fact backup verification failed: raw/{backup}; no facts deleted")
    if delete_batch_size > 1:
        result = sb.delete_nonadditive_fact_batches(obsolete, "raw/" + backup, batch_size=delete_batch_size)
        return {**result, "backup": "raw/" + backup}
    deleted, protected = 0, 0
    for row in obsolete:
        key_query = "&".join(f"{k}=eq.{quote(str(row[k]), safe='')}" for k in FACT_KEY)
        guard = "&derived=is.true" + "".join(f"&{k}=eq.{quote(str(row[k]), safe='')}" for k in ("accession", "concept", "value"))
        lease = getattr(sb, "_aggregate_lease", None)
        if lease:
            lease.check()
        sb.delete("facts", key_query + guard)
        remaining = sb.select("facts", "select=*&" + key_query)
        if remaining and any(r.get("derived") is not False for r in remaining):
            raise RuntimeError(f"Unsupported derived fact still present after exact-key deletion: {ticker}/{row['label']}/{row['period_end']}; backup raw/{backup}")
        if remaining:
            protected += 1  # a concurrent direct reported replacement must never be deleted
        else:
            deleted += 1
    return {"deleted": deleted, "protected_reported": protected, "backup": "raw/" + backup}


# ---------------------------------------------------------------- companies
def process_company(sb, tk, refresh_news=False, refresh_summary=False, fetch_docs=True, store_raw=True, log=print,
                    facts_only=None, source_inputs=None, source_retrieved_at=None, retain_reported_conflicts=False):
    """retain_reported_conflicts is an explicit opt-in (additive staging and the scheduled companies job): when every
    current conflict is a reported-versus-derived disagreement, the directly reported SEC observation is published,
    each disagreement stays attached to its point (selection_conflict) and a warning records the count. Two reported
    observations that disagree on value or period still block publication, as do the separate replacement gates."""
    u = BY_TICKER[tk]
    cik = u["cik"]
    # Newly onboarded issuers retain strict provenance on subsequent scheduled runs.
    facts_only = tk in {t for t, *_ in ADDITIONAL_LIST} if facts_only is None else facts_only
    if facts_only and not store_raw:
        raise ValueError("Facts-only imports require immutable source archiving")
    # Archived-source imports (explicit source_inputs) never call providers. Scheduled facts-only refreshes keep
    # strict financial provenance but may add news and the AI summary, which are separate, labeled enrichment.
    staged_import = source_inputs is not None
    if facts_only and staged_import and (refresh_news or refresh_summary):
        raise ValueError("Facts-only imports cannot request provider enrichment")
    enrich = not facts_only or not staged_import
    warnings = []
    def warn(message):
        warnings.append(message.strip())
        log(message)
    prev = _cache(sb, f"companies/{tk}.json", {}, strict=True)
    source_urls = {"submissions": f"https://data.sec.gov/submissions/CIK{cik:010d}.json",
                   "companyfacts": f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"}
    if facts_only or source_inputs is not None:
        source_inputs = source_inputs if source_inputs is not None else {k: sec_get_raw(url) for k, url in source_urls.items()}
        if set(source_inputs) != set(source_urls) or any(not isinstance(v, bytes) for v in source_inputs.values()):
            raise ValueError("Source inputs must contain exact submissions and companyfacts bytes")
        sub, cf = (json.loads(source_inputs[k]) for k in ("submissions", "companyfacts"))
        if any(str(x.get("cik", "")).lstrip("0") != str(cik) for x in (sub, cf)):
            raise ValueError("SEC source issuer CIK identity mismatch")
        original = source_inputs["companyfacts"]
    else:
        sub, cf = sec.submissions(cik), sec.companyfacts(cik)
        original = json.dumps(cf).encode()
    facts = cf["facts"]
    source_archive = None
    filings = sec.filing_list(sub)
    if not facts_only:
        try:
            sec.fill_gaps(cik, facts, sub, log=log)
        except Exception as e:
            warn(f"  {tk} gap filler failed: {str(e)[:200]}")
    total_scope = cik in sec.REPORTED_TOTAL_REVENUE_CIKS
    pinned = published_concepts(sb, tk)
    statements = {k: sec.build_series(facts, k, total_revenue_scope=total_scope, pinned=pinned.get(k)) for k in sec.CONCEPTS}
    from .source_quality import partition_supported_periods, comparability_review
    statements, quarantined = partition_supported_periods(statements)
    if quarantined:
        warn(f"{len(quarantined)} selected/history statement candidates have unsupported reporting periods; "
             "retained in quarantined_statement_points and excluded from published financial values.")
    if not any(line["annual"] or line["quarterly"] for lines in statements.values() for line in lines):
        raise ValueError("No usable statement facts; preserving prior company bundle")
    comparability = comparability_review(statements)
    if comparability['current_conflicts']:
        ambiguous = [c for c in comparability['current_conflicts'] if c['reason'] == 'reported_period_or_value_ambiguity']
        if not retain_reported_conflicts or ambiguous:
            raise ValueError(f"{len(comparability['current_conflicts'])} reported/derived source conflicts require comparability review; preserving prior financial publication")
        warn(f"{len(comparability['current_conflicts'])} current reported/derived disagreements retained for comparability review; "
             "published values are the directly reported SEC observations and each alternative stays on its point (selection_conflict).")
    digest = hashlib.sha256(original).hexdigest()
    provenance = None
    if facts_only:
        from .source_provenance import validate_statement_provenance, proof_object_path
        proof = validate_statement_provenance(original, statements, cik, digest)
        proof_path = proof_object_path(tk, proof)
        provenance = {k: proof[k] for k in ("version", "status", "cik", "source_url", "snapshot_sha256", "counts")}
        provenance.update(proof_path=proof_path, retrieved_at=(source_retrieved_at or {}).get("companyfacts") or now_iso(),
                          gap_fill="disabled_unarchived_sources")
    filing_plan, held_aliases = preserve_filing_owners(sb, [
        {"accession": f["a"], "ticker": tk} for f in filings[:250]])
    allowed_filings = {row["accession"] for row in filing_plan}
    for alias in held_aliases:
        warn(_held_filing_message(alias))
    rows = statement_fact_rows(tk, statements)
    check_reported_fact_replacements(sb, tk, rows)
    # Validation precedes all mutations, including source object publication.
    if store_raw:
        source_archive = f"sec/companyfacts/archives/CIK{cik:010d}/{digest}.json"
        sb.put_raw(source_archive, original, ticker=tk, kind="companyfacts_snapshot", source_url=source_urls["companyfacts"])
        sb.put_raw(f"sec/companyfacts/CIK{cik:010d}.json", original, ticker=tk, kind="companyfacts", source_url=source_urls["companyfacts"])
        if source_inputs:
            sub_digest = hashlib.sha256(source_inputs["submissions"]).hexdigest()
            sub_archive = f"sec/submissions/archives/CIK{cik:010d}/{sub_digest}.json"
            sb.put_raw(sub_archive, source_inputs["submissions"], ticker=tk, kind="submissions_snapshot", source_url=source_urls["submissions"])
            if provenance:
                provenance["submissions"] = {"source_archive": sub_archive, "sha256": sub_digest,
                    "url": source_urls["submissions"], "retrieved_at": (source_retrieved_at or {}).get("submissions") or now_iso()}
    if provenance:
        sb.put_json(proof_path, proof, cache="max-age=31536000,immutable")
    prof = sec.profile(sub, facts)
    ftables = sec.filing_tables(facts, statements, filings, cik)
    if facts_only:
        from .source_quality import retain_proved_filing_tables
        ftables, unproved_table_cells = retain_proved_filing_tables(ftables, proof)
        provenance["reconstructed_tables"] = {"scope":"verified_source_records_only", "omitted_cells":unproved_table_cells}
    earn = [{"filed": f["d"], "accn": f["a"], "url": sec.filing_url(cik, f["a"], f["p"])} for f in filings if f["f"] == "8-K" and "2.02" in (f["i"] or "")][:12]

    # original statement tables for the latest 10-K and 10-Q
    ix = dict(prev.get("ix") or {})
    targets = [f for f in filings if f["f"] == "10-K"][:1] + [f for f in filings if f["f"] == "10-Q"][:1]
    keep = {f["a"] for f in targets}
    ix = {a: v for a, v in ix.items() if a in keep}
    if fetch_docs:
        for f in targets:
            if f["a"] in ix:
                continue
            url = sec.filing_url(cik, f["a"], f["p"])
            try:
                html = docs.filing_html(url)
                if store_raw and html:
                    sb.put_raw(f"sec/filings/{f['a']}/{f['p']}", html, ticker=tk, accession=f["a"], kind="filing_html", source_url=url)
                if html:
                    try:
                        docs.publish_filing(sb, html, cik, f["a"])
                    except Exception as e:
                        warn(f"  {tk} publish {f['a']} failed: {str(e)[:160]}")
                tabs = docs.ix_tables(html)
                if tabs:
                    ix[f["a"]] = {"form": f["f"], "url": url, "tables": tabs}
            except Exception as e:
                warn(f"  {tk} ix {f['a']} failed: {str(e)[:160]}")

    # latest earnings release text
    release = prev.get("release")
    if fetch_docs and earn and (not release or release.get("accn") != earn[0]["accn"]):
        e0 = earn[0]
        f0 = next(f for f in filings if f["a"] == e0["accn"])
        try:
            url, md = docs.release_markdown(sec.filing_url(cik, e0["accn"]), f0["p"])
            if md:
                release = {"filed": e0["filed"], "accn": e0["accn"], "url": url, "markdown": md}
                if store_raw:
                    sb.put_raw(f"sec/releases/{e0['accn']}.md", md, ticker=tk, accession=e0["accn"], kind="earnings_release", source_url=url)
        except Exception as e:
            warn(f"  {tk} release failed: {str(e)[:160]}")

    coverage = prev.get("news") or {}
    if enrich and (refresh_news or not coverage.get("items")):
        try:
            coverage = {"items": news.company_news(u["name"], tk), "fetched_at": now_iso()}
        except Exception as e:
            warn(f"  {tk} news failed: {str(e)[:160]}")

    summary = prev.get("summary")
    release_changed = (release or {}).get("accn") != ((prev.get("release") or {}).get("accn"))
    if enrich and (refresh_summary or not summary or release_changed or refresh_news):
        try:
            summary = summaries.company(u["name"], tk, statements, prof["fiscalYearEnd"], (release or {}).get("markdown"), coverage.get("items", []))
        except Exception as e:
            warn(f"  {tk} summary failed: {str(e)[:160]}")

    if fundamentals.FYE_HISTORY.get(tk):
        prof["fyeHistory"] = fundamentals.FYE_HISTORY[tk]
    if tk in fundamentals.FY_NAME_PRIOR:
        prof["fyNameOffset"] = -1
    if fundamentals.TRANSITIONS.get(tk):
        prof["fyTransitions"] = fundamentals.TRANSITIONS[tk]
    bundle = {
        "pipeline_version": PIPELINE_VERSION, "ticker": tk, "cik": cik, "displayName": u["name"], "sector": u["sector"], "irUrl": IR_URLS.get(tk), "generated": now_iso(),
        "profile": prof, "statements": statements, "source_archive": source_archive, "normalization": {"version": 2, "vintage": "latest", "availability_resolution": "filing_date"}, "warnings": warnings, "filings": [f for f in filings][:250], "filingTables": ftables,
        "earnings8k": earn, "ix": ix, "release": release, "news": coverage, "summary": summary,
        "quarantined_statement_points": quarantined,
        "comparability_review": comparability,
    }
    if provenance:
        bundle["provenance"] = provenance

    # relational copies
    sb.upsert("companies", [{
        "ticker": tk, "cik": cik, "name": prof["name"], "display_name": u["name"], "sector": u["sector"], "sic": prof["sic"], "sic_description": prof["sicDescription"],
        "exchange": (prof["exchanges"] or [None])[0], "fiscal_year_end": prof["fiscalYearEnd"], "state_of_inc": prof["stateOfIncorporation"], "address": prof["address"],
        "phone": prof["phone"], "website": prof["website"], "ir_url": IR_URLS.get(tk), "shares_outstanding": (prof["sharesOutstanding"] or {}).get("val"),
        "shares_as_of": (prof["sharesOutstanding"] or {}).get("end"), "latest_accession": filings[0]["a"] if filings else None, "updated_at": now_iso()}], "ticker")
    sb.upsert("filings", [{
        "accession": f["a"], "ticker": tk, "form": f["f"], "filed": f["d"], "report_date": f["r"] or None, "primary_doc": f["p"], "items": f["i"],
        "description": describe(f), "url": sec.filing_url(cik, f["a"], f["p"]), "has_source_tables": f["a"] in ix, "updated_at": now_iso()} for f in filings[:250] if f["a"] in allowed_filings], "accession")
    if facts_only:
        sb.upsert("facts", rows, "ticker,statement,label,kind,period_end", batch=1000)
        fact_cleanup = {"deleted": 0, "backup": None, "mode": "additive"}
    else:
        fact_cleanup = persist_normalized_facts(sb, tk, rows)
    # A failed fact write must not advertise a new public financial bundle.
    sb.put_json(f"companies/{tk}.json", bundle)
    if coverage.get("items"):
        lines = (summary or {}).get("news_lines") or {}
        sb.upsert("news_items", [{"url": x["url"], "ticker": tk, "title": x["title"][:500], "source": x["source"], "published_at": _ts(x.get("date")),
                                  "snippet": x["snippet"], "summary_line": lines.get(str(i + 1)), "fetched_at": coverage.get("fetched_at") or now_iso()} for i, x in enumerate(coverage["items"])], "url")
    if summary:
        sb.upsert("summaries", [{"id": f"company:{tk}", "kind": "company", "ticker": tk, "content": summary, "generated_at": summary.get("generated_at") or now_iso()}], "id")

    result = company_fundamental(tk, statements, prof, filings, warnings, fact_cleanup, ix, release)
    if held_aliases:
        result["held_filing_aliases"] = held_aliases
    return result


def company_fundamental(tk, statements, prof, filings, warnings, fact_cleanup, ix=None, release=None):
    """Deterministic public-index inputs, also reconstructed at backfill promotion."""
    u = BY_TICKER[tk]
    fund = fundamentals.compute(statements, prof, None, ticker=tk)
    return {"pipeline_version": PIPELINE_VERSION, "ticker": tk, "warnings": warnings, "fact_cleanup": fact_cleanup, "name": u["name"], "legal_name": prof["name"], "sector": u["sector"], "sic": prof["sicDescription"], "exchange": (prof["exchanges"] or [None])[0],
            "fye": prof["fiscalYearEnd"], "shares": (prof["sharesOutstanding"] or {}).get("val"), "shares_metadata": prof["sharesOutstanding"], "latest_filing": filings[0]["d"] if filings else None,
            "latest_accession": filings[0]["a"] if filings else None, "material_accession": material_accession(filings), "fund": fund, "has_source_tables": bool(ix), "has_release": bool(release),
            "div_ttm": fundamentals.ttm(statements, "Dividends paid", ("cashflow",), end=fund["latest_quarter_end"]),
            "bb_ttm": fundamentals.ttm(statements, "Buybacks", ("cashflow",), end=fund["latest_quarter_end"])}


def _ts(s):
    if not s:
        return None
    try:
        from email.utils import parsedate_to_datetime
        return parsedate_to_datetime(s).astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        return s if "T" in s else None


MATERIAL = ("10-K", "10-Q", "10-K/A", "10-Q/A", "8-K", "20-F", "40-F", "6-K")


def material_accession(filings):
    return next((f["a"] for f in filings if f["f"] in MATERIAL), None)


def detect_changes(sb, tickers, fund, deadline, ownership_stats=None):
    """Checks SEC submissions for each ticker. New material filings (10-K, 10-Q, 8-K) mark the company for
    reprocessing, as do legacy normalization versions even without a new filing. The caller
    applies its ordinary max_companies/time budget. Other new filings sync to the filings table."""
    state = _cache(sb, "meta/check_cursor.json", {"cursor": 0})
    start = state.get("cursor", 0) % max(len(tickers), 1)
    order = tickers[start:] + tickers[:start]
    changed, checked, light, latest_updates = [], 0, [], []

    def check(tk):
        if time.time() > deadline:
            return tk, None, None
        try:
            sub = sec.submissions(BY_TICKER[tk]["cik"])
            return tk, sec.filing_list(sub), None
        except Exception as e:
            return tk, None, str(e)[:160]

    with ThreadPoolExecutor(4) as ex:
        for tk, fl, err in ex.map(check, order):
            if fl is None:
                if err:
                    print("submissions check failed", tk, err)
                continue
            checked += 1
            prev = fund.get(tk) or {}
            mat = material_accession(fl)
            known = prev.get("material_accession") or prev.get("latest_accession")
            needs_upgrade = (prev.get("pipeline_version") or 0) < PIPELINE_VERSION
            if not prev or needs_upgrade or (mat and mat != known):
                changed.append(tk)
            elif fl and fl[0]["a"] != prev.get("latest_accession"):
                cik = BY_TICKER[tk]["cik"]
                for f in fl[:40]:
                    if f["a"] == prev.get("latest_accession"):
                        break
                    light.append({"accession": f["a"], "ticker": tk, "form": f["f"], "filed": f["d"], "report_date": f["r"] or None, "primary_doc": f["p"],
                                  "items": f["i"], "description": describe(f), "url": sec.filing_url(cik, f["a"], f["p"]), "updated_at": now_iso()})
                latest_updates.append((tk, fl[0]["a"], fl[0]["d"]))
    light, held_aliases = preserve_filing_owners(sb, light)
    for alias in held_aliases:
        message = _held_filing_message(alias)
        print(message)
        warnings = fund[alias["ticker"]].setdefault("warnings", [])
        if message not in warnings:
            warnings.append(message)
    _filing_owner_stats(ownership_stats, held_aliases)
    if light:
        sb.upsert("filings", light, "accession")
    for tk, accession, filed in latest_updates:
        fund[tk]["latest_accession"] = accession
        fund[tk]["latest_filing"] = filed
    state["cursor"] = (start + checked) % max(len(tickers), 1)
    state["updated_at"] = now_iso()
    sb.put_json("meta/check_cursor.json", state, cache="no-store")
    print(f"checked {checked}/{len(tickers)}, {len(changed)} with new reports or legacy pipeline versions, {len(light)} other filings synced")
    return changed


@serialized_aggregates
def companies_job(sb, tickers=None, workers=4, refresh_news=False, refresh_summary=False, fetch_docs=True, time_budget=None, only_changed=False, max_companies=None, facts_only=None):
    with Run(sb, "companies") as run:
        t0 = time.time()
        fund = _cache(sb, "meta/fundamentals.json", {}, strict=True)
        tickers = tickers or [t for t, *_ in UNIVERSE]
        if only_changed:
            tickers = detect_changes(sb, tickers, fund, deadline=t0 + (time_budget or 600) * 0.4, ownership_stats=run.stats)
        if max_companies:
            tickers = tickers[:max_companies]
        done, failed, skipped, enrichment_errors = [], {}, {}, {}

        def one(tk):
            if time_budget and time.time() - t0 > time_budget:
                return tk, None, "skipped: time budget"
            try:
                return tk, process_company(sb, tk, refresh_news=refresh_news, refresh_summary=refresh_summary, fetch_docs=fetch_docs,
                                           facts_only=facts_only, retain_reported_conflicts=True), None
            except Exception as e:
                return tk, None, str(e)[:300]

        with ThreadPoolExecutor(workers) as ex:
            for tk, res, err in ex.map(one, tickers):
                if res:
                    fund[tk] = res
                    _filing_owner_stats(run.stats, res.get("held_filing_aliases") or [])
                    if res.get("warnings"):
                        enrichment_errors[tk] = res["warnings"]
                    done.append(tk)
                    print("ok", tk, flush=True)
                    if len(done) % 20 == 0:
                        sb.put_json("meta/fundamentals.json", fund, cache="no-store")
                else:
                    if (err or "").startswith("skipped:"):
                        skipped[tk] = err
                    else:
                        failed[tk] = err
                    print("fail", tk, err, flush=True)
        sb.put_json("meta/fundamentals.json", fund, cache="no-store")
        run.stats.update({"done": len(done), "failed": failed, "skipped": skipped, "errors": enrichment_errors, "tickers": done})
        # Storage overwrites can still read as the previous object. Publish the
        # same validated snapshot that this run processed and persisted.
        build_index(sb, fundamentals_snapshot=fund)
        build_feed(sb)
        return done, failed


# ---------------------------------------------------------------- prices
@serialized_aggregates
def prices_job(sb, intraday=True, full_history=False, part=None, parts=1, time_budget=None):
    """Daily and 5-minute bars. With parts > 1 the stock list is split so each run fits a function time limit;
    part 0 also carries the market symbols. Each part writes its own stats file, merged by build_index."""
    with Run(sb, "prices") as run:
        comp = [t for t, *_ in UNIVERSE]
        if part is not None and parts > 1:
            comp = comp[part::parts]
        with_market = part in (None, 0)
        syms = (MARKET_SYMBOLS if with_market else []) + comp
        t0 = time.time()
        daily, err1 = prices.fetch_many(syms, "2y", "1d", deadline=t0 + time_budget * 0.6 if time_budget else None)
        intra, err2 = prices.fetch_many((["SPY", "QQQ"] if with_market else []) + comp, "5d", "5m", deadline=t0 + time_budget * 0.8 if time_budget else None) if intraday else ({}, {})
        spy = daily.get("SPY", {}).get("bars")
        if not spy:
            try:
                spy = prices.chart("SPY", "2y", "1d")["bars"]
            except Exception:
                spy = None
        pstats, db_rows = {}, []
        for s_, d in daily.items():
            pstats[s_] = prices.stats(d["bars"], spy if s_ != "SPY" else None)
            pstats[s_]["spark"] = [round(b[4], 2) for b in d["bars"][-63:]]
            # Providers revise all adjusted prices after splits; replace the full fetched window.
            rows = d["bars"]
            db_rows += [{"symbol": s_, "d": dt.datetime.fromtimestamp(b[0], dt.timezone.utc).date().isoformat(), "o": b[1], "h": b[2], "l": b[3], "c": b[4], "v": b[5]} for b in rows]

        def put(item):
            s_, d = item
            i_bars = (intra.get(s_) or {}).get("bars")
            if i_bars is None and intraday:
                prev = _cache(sb, f"prices/{prices.path_key(s_)}.json", {}) or {}
                i_bars = prev.get("i", [])
            sb.put_json(f"prices/{prices.path_key(s_)}.json", {"meta": d["meta"], "d": d["bars"], "i": i_bars or [], "dv": d.get("dv", [])}, cache="max-age=300")

        with ThreadPoolExecutor(8) as ex:
            list(ex.map(put, daily.items()))
        if db_rows:
            sb.upsert("prices_daily", db_rows, "symbol,d", batch=2000)
        if with_market:
            market = _cache(sb, "market.json", {}, strict=True)
            market = {**market, "generated": now_iso(), "symbols": dict(market.get("symbols") or {})}
            for s_ in MARKET_SYMBOLS:
                if s_ in daily:
                    b = daily[s_]["bars"]
                    market["symbols"][s_] = {"bars": [[x[0], x[4]] for x in b[-270:]], "o": b[-1][1], "h": b[-1][2], "l": b[-1][3]}
            sb.put_json("market.json", market, cache="max-age=300")
        key = "all" if part is None or parts <= 1 else f"p{part}"
        previous = _cache(sb, f"meta/pricestats/{key}.json", {}, strict=True).get("stats") or {}
        sb.put_json(f"meta/pricestats/{key}.json", {"generated": now_iso(), "parts": parts, "stats": {**previous, **pstats}}, cache="no-store")
        run.stats.update({"part": key, "symbols": len(daily), "errors": {**err1, **{k + "@5m": v for k, v in err2.items()}}})
        build_index(sb)
        return len(daily), {**err1, **err2}


def load_pricestats(sb):
    """Merges per-part stats files, keeping the newest bar date per symbol."""
    merged = {}
    sources = [_cache(sb, "meta/pricestats.json", {}, strict=True)]
    for key in ["all"] + [f"p{i}" for i in range(8)]:
        doc = _cache(sb, f"meta/pricestats/{key}.json", None, strict=True)
        if doc:
            sources.append(doc.get("stats") or {})
    for src in sources:
        for sym, st in src.items():
            if not isinstance(st, dict):
                continue
            cur = merged.get(sym)
            if not cur or (st.get("as_of") or "") >= (cur.get("as_of") or ""):
                merged[sym] = st
    return merged


# ---------------------------------------------------------------- technicals
def technicals_job(sb, part=None, parts=4, merge=None, time_budget=None):
    """Long-history technicals. With a part: update that slice's history files from the prices job's bars and compute its
    partial statistics. Merge combines the parts into tech/signals.json, tech/breadth.json and tech/states.json.
    Without a part (local runs) every part runs and then the merge."""
    from . import technicals
    with Run(sb, "technicals") as run:
        deadline = time.time() + time_budget if time_budget else None
        if part is not None:
            out = technicals.run_part(sb, part, parts, deadline=deadline)
            run.stats.update({"part": part, "stocks": len(out["symbols"]), "errors": out["errors"], "notes": out["notes"]})
            if merge:
                run.stats["merge"] = technicals.merge(sb, parts)
            return run.stats
        if merge is None or not merge:
            for k in range(parts):
                out = technicals.run_part(sb, k, parts)
                run.stats[f"p{k}"] = {"stocks": len(out["symbols"]), "errors": len(out["errors"])}
        run.stats["merge"] = technicals.merge(sb, parts)
        return run.stats


# ---------------------------------------------------------------- index + feed
def _ins_net(entry):
    w = (entry or {}).get("w90") or {}
    if not w:
        return None
    return (w.get("buy_value") or 0) - (w.get("sell_value") or 0)


@serialized_aggregates
def build_index(sb, pstats=None, fundamentals_snapshot=None, return_snapshot=False):
    fund = (_cache(sb, "meta/fundamentals.json", {}, strict=True)
            if fundamentals_snapshot is None else fundamentals_snapshot)
    if not isinstance(fund, dict) or not fund:
        raise ValueError("Fundamentals missing; refusing to replace index with an empty bundle")
    pstats = load_pricestats(sb) if pstats is None else pstats
    ins = (_cache(sb, "meta/insiders.json", {}) or {}).get("companies", {})
    logos = (_cache(sb, "meta/logos.json", {}) or {}).get("tickers", {})
    rows, metrics = [], []
    for tk, *_ in UNIVERSE:
        f = fund.get(tk)
        if not f:
            continue
        ps = pstats.get(tk) or {}
        is_normalized = fundamentals.normalized(f)
        # Legacy aggregate values can contain synthetic EPS and share quarters.
        # Keep the company discoverable while its bounded normalization runs.
        fd = dict(f.get("fund") or {}) if is_normalized else {}
        price = ps.get("price")
        mcap = fundamentals.market_cap(price, f.get("shares_metadata"), ps.get("as_of"))
        basis = fundamentals.valuation_hold(tk, fd.get("valuation_basis"), ps.get("as_of"))
        rev, eps, fcf = fd.get("revenue_ttm"), fundamentals.current_ttm_value(fd, "eps_ttm", basis), fd.get("fcf_ttm")
        m = {
            "ticker": tk, "as_of": ps.get("as_of"), "price": price, "chg_1d": ps.get("chg_1d"), "ret_1m": ps.get("ret_1m"), "ret_ytd": ps.get("ret_ytd"), "ret_1y": ps.get("ret_1y"),
            "market_cap": mcap, "pe_ttm": price / eps if price and eps and eps > 0 else None, "ps_ttm": mcap / rev if mcap and rev else None, "eps_ttm": eps,
            "revenue_ttm": rev, "net_income_ttm": fd.get("net_income_ttm"), "fcf_ttm": fcf, "fcf_yield": fcf / mcap if fcf is not None and mcap else None,
            "gross_margin_lq": fd.get("gross_margin_lq"), "op_margin_lq": fd.get("op_margin_lq"), "net_margin_ttm": fd.get("net_margin_ttm"), "rev_growth_lq": fd.get("rev_growth_lq"),
            "dividend_yield": None, "buyback_yield": None, "beta_1y": ps.get("beta_1y"), "vol_60d": ps.get("vol_60d"), "high_52w": ps.get("high_52w"), "low_52w": ps.get("low_52w"),
            "latest_quarter": fd.get("latest_quarter"), "latest_quarter_end": fd.get("latest_quarter_end"), "updated_at": now_iso(),
        }
        if is_normalized and f.get("div_ttm") is not None and mcap:
            m["dividend_yield"] = f["div_ttm"] / mcap
        if is_normalized and f.get("bb_ttm") is not None and mcap:
            m["buyback_yield"] = f["bb_ttm"] / mcap
        metrics.append({k: (None if isinstance(v, float) and v != v else v) for k, v in m.items()})
        rows.append({**{k: v for k, v in m.items() if k not in ("updated_at",)}, "name": f["name"], "legal_name": f.get("legal_name"), "sector": f["sector"], "sic": f.get("sic"),
                     "exchange": f.get("exchange"), "fye": f.get("fye"), "latest_filing": f.get("latest_filing"), "has_source_tables": f.get("has_source_tables"), "sp100": tk in SP100, "sp500": tk in SP500, "logo": tk in logos, "insider_net_90d": _ins_net(ins.get(tk)), "spark": ps.get("spark", []), "shares_metadata": f.get("shares_metadata"),
                     "pipeline_version": f.get("pipeline_version"), "financial_status": "normalized" if is_normalized else "normalization_pending",
                     "valuation_basis": basis})
    if metrics:
        sb.upsert("company_metrics", metrics, "ticker")
    snapshot = {"generated": now_iso(), "universe": UNIVERSE_METADATA, "sectors": SECTOR_ETF, "companies": rows}
    sb.put_json("index.json", snapshot, cache="max-age=120")
    return snapshot if return_snapshot else len(rows)


def build_feed(sb, limit=400):
    rows = sb.select("filings", f"select=accession,ticker,form,filed,report_date,primary_doc,items,description,url&order=filed.desc,accession.desc&limit={limit}")
    sb.put_json("feed.json", {"generated": now_iso(), "filings": rows[:limit]}, cache="max-age=300")
    return len(rows)


# ---------------------------------------------------------------- macro
@serialized_aggregates
def macro_job(sb, refresh_news=True, brief=True):
    with Run(sb, "macro") as run:
        prev = _cache(sb, "macro.json", {})
        curve = macro.treasury_curve()
        fred = macro.fred()
        try:
            effr = macro.effr_monthly()
        except Exception as e:
            print("effr failed", e)
            effr = prev.get("effr", [])
        items = prev.get("news", [])
        if refresh_news:
            try:
                items = news.macro_news()
            except Exception as e:
                print("macro news failed", e)
        b = prev.get("brief")
        if brief and items:
            try:
                b = summaries.macro_brief(curve[-1], macro.CURVE_HDR, effr[-6:], fred.get("CPIAUCSL", [])[-6:], items)
            except Exception as e:
                print("macro brief failed", e)
        out = {"generated": now_iso(), "curve_hdr": macro.CURVE_HDR, "curve": curve, "fred": fred, "effr": effr, "news": items, "brief": b}
        sb.put_json("macro.json", out, cache="max-age=600")
        obs = []
        for sid, pts in fred.items():
            obs += [{"series": "FRED:" + sid, "d": d, "value": v} for d, v in pts]
        obs += [{"series": "FED:EFFR", "d": d, "value": v} for d, v in effr]
        i10, i2 = macro.CURVE_HDR.index("10 Yr") + 1, macro.CURVE_HDR.index("2 Yr") + 1
        obs += [{"series": "TSY:10Y", "d": r[0], "value": r[i10]} for r in curve if r[i10] is not None]
        obs += [{"series": "TSY:2Y", "d": r[0], "value": r[i2]} for r in curve if r[i2] is not None]
        sb.upsert("macro_observations", obs, "series,d", batch=1000)
        try:
            from . import macrojobs
            macrojobs.legacy_store(sb, obs)
        except Exception as e:
            print("macro series store mirror failed", str(e)[:200])
        if b:
            sb.upsert("summaries", [{"id": "macro:brief", "kind": "macro", "content": b, "generated_at": b.get("generated_at") or now_iso()}], "id")
        if items:
            sb.upsert("news_items", [{"url": x["url"], "ticker": "MACRO", "title": x["title"][:500], "source": x["source"], "published_at": _ts(x.get("date")), "snippet": x["snippet"], "fetched_at": now_iso()} for x in items], "url")
        run.stats.update({"curve_rows": len(curve), "fred": {k: len(v) for k, v in fred.items()}, "news": len(items)})
        return out


# ---------------------------------------------------------------- rotation for news + summaries
@serialized_aggregates
def news_rotation_job(sb, per_run=12, workers=4, time_budget=240):
    state = _cache(sb, "meta/rotation.json", {"cursor": 0})
    tickers = [t for t, *_ in UNIVERSE]
    start = state.get("cursor", 0) % len(tickers)
    batch = (tickers[start:] + tickers[:start])[:per_run]
    done, failed = companies_job(sb, batch, workers=workers, refresh_news=True, fetch_docs=False, time_budget=time_budget)
    state["cursor"] = (start + len(done) + len(failed)) % len(tickers)
    state["updated_at"] = now_iso()
    sb.put_json("meta/rotation.json", state, cache="no-store")
    return done, failed


# ---------------------------------------------------------------- insiders
def insiders_job(sb, tickers=None, days=365, per_company=120, workers=4, time_budget=None):
    """Parses new Form 4 filings into insider_transactions and rewrites insiders/{T}.json for touched companies."""
    t0 = time.time()
    with Run(sb, "insiders") as run:
        cut = (dt.date.today() - dt.timedelta(days=days)).isoformat()
        q = f"select=ticker,accession,primary_doc,filed&form=eq.4&filed=gte.{cut}&order=filed.desc,accession.desc"
        if tickers:
            q += "&ticker=in.(" + ",".join(tickers) + ")"
        filings = sb.select("filings", q)
        known = {r["accession"] for r in sb.select("insider_transactions", f"select=accession&filed=gte.{cut}")}
        per, todo = {}, []
        for f in filings:
            if f["accession"] in known or not f.get("primary_doc"):
                continue
            known.add(f["accession"])
            if per.get(f["ticker"], 0) >= per_company:
                continue
            per[f["ticker"]] = per.get(f["ticker"], 0) + 1
            todo.append(f)
        run.stats["pending"] = len(todo)
        done, failed, buf, touched = 0, 0, [], set(tickers or [])

        def one(f):
            return insiders.fetch(f["ticker"], BY_TICKER[f["ticker"]]["cik"], f)

        with ThreadPoolExecutor(workers) as ex:
            futs = []
            for f in todo:
                if f["ticker"] not in BY_TICKER:
                    continue
                futs.append((f, ex.submit(one, f)))
            for f, fu in futs:
                if time_budget and time.time() - t0 > time_budget:
                    fu.cancel()
                    continue
                try:
                    buf += fu.result()
                    done += 1
                    touched.add(f["ticker"])
                except Exception as e:
                    failed += 1
                    print("form4", f["ticker"], f["accession"], str(e)[:160])
                if len(buf) >= 400:
                    sb.upsert("insider_transactions", list({(r["accession"], r["seq"]): r for r in buf}.values()), "accession,seq")
                    buf = []
        if buf:
            sb.upsert("insider_transactions", list({(r["accession"], r["seq"]): r for r in buf}.values()), "accession,seq")
        index = _cache(sb, "meta/insiders.json", {"companies": {}})
        for tk in sorted(touched):
            rows = sb.select("insider_transactions", f"select=*&ticker=eq.{tk}&filed=gte.{cut}&order=tx_date.desc")
            bundle = insiders.summarize(rows)
            bundle.update({"ticker": tk, "generated": now_iso(), "codes": insiders.CODES})
            sb.put_json(f"insiders/{tk}.json", bundle, cache="max-age=600")
            index["companies"][tk] = {"w90": bundle["w90"], "w365": bundle["w365"],
                                      "last": bundle["rows"][0]["tx_date"] if bundle["rows"] else None}
        index["generated"] = now_iso()
        sb.put_json("meta/insiders.json", index, cache="max-age=600")
        run.stats.update({"parsed": done, "failed": failed, "companies": len(touched)})
        return done, failed


# ---------------------------------------------------------------- filing viewer copies
def publish_filings_job(sb, tickers=None, workers=6):
    """Publishes viewer copies of each company's latest 10-K and 10-Q from the archived originals."""
    import gzip as _gz
    tickers = tickers or [t for t, *_ in UNIVERSE]

    def one(tk):
        b = _cache(sb, f"companies/{tk}.json", {}, strict=True)
        cik = BY_TICKER[tk]["cik"]
        n = 0
        for accn, meta in (b.get("ix") or {}).items():
            f = next((x for x in b.get("filings", []) if x["a"] == accn), None)
            if not f:
                continue
            raw = sb.get_object("raw", f"sec/filings/{accn}/{f['p']}.gz")
            if raw:
                try:
                    html = _gz.decompress(raw).decode("utf-8", "replace")
                except OSError:
                    html = raw.decode("utf-8", "replace")
            else:
                html = docs.filing_html(sec.filing_url(cik, accn, f["p"]))
            if html:
                docs.publish_filing(sb, html, cik, accn)
                n += 1
        return tk, n

    total = 0
    with ThreadPoolExecutor(workers) as ex:
        for tk, n in ex.map(one, tickers):
            total += n
    return total


# ---------------------------------------------------------------- segments (XBRL dimensions)
def _viewer_or_fetch(sb, cik, f):
    """Filing HTML for parsing: the stored viewer copy when present, otherwise EDGAR (and store the viewer copy)."""
    import gzip as _gz
    try:
        raw = sb.get_object("public-data", f"filings/{f['a']}.html.gz", public=True)
    except Exception:
        raw = None
    if raw:
        try:
            return _gz.decompress(raw).decode("utf-8", "replace")
        except OSError:
            return raw.decode("utf-8", "replace")
    html = docs.filing_html(sec.filing_url(cik, f["a"], f["p"]))
    if html:
        try:
            docs.publish_filing(sb, html, cik, f["a"])
        except Exception as e:
            print("publish failed", f["a"], str(e)[:120])
    return html


def periodic_filings(cik, cut, forms=("10-K", "10-Q"), ticker=None):
    """10-K and 10-Q filings since cut, newest first, following the paged submissions files for heavy filers. With a
    ticker, filings of predecessor registrants (holding-company reorganisations) up to their cut-off are included,
    each carrying its own "cik"."""
    from .universe import PREDECESSORS
    res = _periodic(cik, cut, forms)
    for pcik, until in PREDECESSORS.get(ticker or "", []):
        try:
            res += [dict(f, cik=pcik) for f in _periodic(pcik, cut, forms) if f["d"] <= until]
        except Exception as e:
            print(f"  predecessor {pcik} filings failed: {str(e)[:120]}")
    return sorted(res, key=lambda f: f["d"], reverse=True)


def _periodic(cik, cut, forms):
    sub = sec.submissions(cik)
    out = [f for f in sec.filing_list(sub) if f["f"] in forms and f["d"] >= cut and f.get("p")]
    recent = sub["filings"]["recent"]["filingDate"]
    if recent and min(recent) > cut:
        for extra in sub["filings"].get("files", []):
            if extra.get("filingTo", "9999") < cut:
                continue
            page = sec.sec_json(f"https://data.sec.gov/submissions/{extra['name']}")
            out += [f for f in sec.filing_list({"filings": {"recent": page}}) if f["f"] in forms and f["d"] >= cut and f.get("p")]
    seen, res = set(), []
    for f in sorted(out, key=lambda f: f["d"], reverse=True):
        if f["a"] not in seen:
            seen.add(f["a"])
            res.append(f)
    return res


def segments_for(sb, tk, years=None, log=print):
    from . import segments as S
    years = years or S.TABLE_YEARS
    u = BY_TICKER[tk]
    cik = u["cik"]
    b = _cache(sb, f"companies/{tk}.json", {}, strict=True)
    fye = (b.get("profile") or {}).get("fiscalYearEnd") or "1231"
    cut = (dt.date.today() - dt.timedelta(days=int(365 * years))).isoformat()
    fl = periodic_filings(cik, cut, ticker=tk)
    parsed = []
    keep_names = {n for _f, _l, _u, names in S.FAMILIES for n in names}

    def slim(ctx, facts):
        """Only what segment tables use: dimensional facts and the family totals."""
        fs = [x for x in facts if ctx.get(x["ctx"], {}).get("dims") or x["name"].split(":")[-1] in keep_names or S.EXT_METRIC.search(x["name"])]
        used = {x["ctx"] for x in fs}
        return {k: v for k, v in ctx.items() if k in used}, fs

    import gzip as _gz
    for f in fl:
        cache_path = f"segcache/{f['a']}.json.gz"
        try:
            raw = sb.get_object("raw", cache_path)
            if raw:
                c = json.loads(_gz.decompress(raw))
                parsed.append((f, c["ctx"], c["facts"]))
                continue
        except Exception:
            pass
        try:
            facts = None
            # inline XBRL became mandatory for large filers in mid-2019; older reports carry a separate instance file
            if f["d"] >= "2019-06-01":
                html = _viewer_or_fetch(sb, f.get("cik", cik), f)
                if html:
                    ctx, facts = S.parse_ixbrl(html)
            if not facts:
                url = S.instance_url(f.get("cik", cik), f["a"])
                if url:
                    ctx, facts = S.parse_instance(S.sec_get(url, timeout=120))
            if facts:
                ctx, facts = slim(ctx, facts)
                parsed.append((f, ctx, facts))
                try:
                    sb.put_object("raw", cache_path, _gz.compress(json.dumps({"ctx": ctx, "facts": facts}, separators=(",", ":")).encode()),
                                  "application/gzip", cache="max-age=31536000")
                except Exception as e:
                    log(f"  {tk} cache write {f['a']} failed: {str(e)[:100]}")
        except Exception as e:
            log(f"  {tk} {f['a']} parse failed: {str(e)[:140]}")
    labels = {}
    tenks = [x for x in fl if x["f"] == "10-K"]
    # member labels from older 10-Ks first (members dropped later), then the latest filings override
    older = [next((x for x in tenks if x["d"] < cutoff), None) for cutoff in ("2013-06-01", "2016-06-01", "2019-06-01")]
    for f in [x for x in older if x] + tenks[:1] + [x for x in fl if x["f"] == "10-Q"][:1]:
        try:
            labels.update(S.label_linkbase(f.get("cik", cik), f["a"], f["p"]))
        except Exception as e:
            log(f"  {tk} labels {f['a']} failed: {str(e)[:120]}")
    obs, totals = S.collect(parsed)
    def _fyl(f, e, k):
        return fundamentals.period_label(tk, f, e, k)
    tables = S.pick_tables(S.add_residuals(S.build_tables(obs, totals, fye, labels, _fyl)))
    bundle = {"ticker": tk, "generated": now_iso(), "fye": fye, "filings": len(parsed), "tables": tables}
    sb.put_json(f"segments/{tk}.json", bundle, cache="max-age=600")
    rows = []
    for t in tables:
        for r in t["rows"]:
            kid = f"xbrl:{t['id']}:{r['key']}"
            for kind in ("A", "Q", "I"):
                for end, c in r[kind].items():
                    rows.append({"ticker": tk, "kpi_id": kid[:300], "period_kind": kind, "period_end": end, "fiscal_label": c.get("p"), "value": c["v"],
                                 "unit": "USD", "source_kind": "xbrl", "source_id": c.get("accn") or "", "anchor": {"id": c.get("id"), "name": c.get("name"), "ctx": c.get("ctx")},
                                 "derived": c.get("derived"), "reconciled": (t["recon"].get(f"{kind}:{end}") or {}).get("ok"), "updated_at": now_iso()})
    if rows:
        try:
            sb.upsert("kpi_values", rows, "ticker,kpi_id,period_kind,period_end,source_kind", batch=1000)
        except Exception as e:
            log(f"  {tk} kpi_values upsert failed: {str(e)[:160]}")
    ok = sum(1 for t in tables for rc in t["recon"].values() if rc["ok"])
    bad = sum(1 for t in tables for rc in t["recon"].values() if not rc["ok"])
    return {"tables": len(tables), "filings": len(parsed), "recon_ok": ok, "recon_residual": bad}


def segments_job(sb, tickers=None, workers=5, time_budget=None):
    t0 = time.time()
    with Run(sb, "segments") as run:
        tickers = tickers or [t for t, *_ in UNIVERSE]
        done, failed = {}, {}

        def one(tk):
            if time_budget and time.time() - t0 > time_budget:
                return tk, None, "skipped: time budget"
            try:
                return tk, segments_for(sb, tk), None
            except Exception as e:
                return tk, None, str(e)[:300]

        with ThreadPoolExecutor(workers) as ex:
            for tk, res, err in ex.map(one, tickers):
                if res:
                    done[tk] = res
                    print("ok", tk, res, flush=True)
                else:
                    failed[tk] = err
                    print("fail", tk, err, flush=True)
        idx = _cache(sb, "meta/segments.json", {"companies": {}})
        idx["companies"].update(done)
        idx["generated"] = now_iso()
        sb.put_json("meta/segments.json", idx, cache="max-age=600")
        run.stats.update({"done": len(done), "failed": failed})
        return done, failed


# ---------------------------------------------------------------- investor relations documents
def ir_job(sb, tickers=None, workers=4, time_budget=None, refresh_site=False, web=True, sec_years=3,
           max_docs=90, max_pages=40, repair_existing=False, publish_only=False, backfill=False):
    from . import ir
    started = time.monotonic()
    # The total deadline covers storage/leases too; providers stop early for persistence.
    io_deadline = started + max(0, time_budget) if time_budget is not None else None
    sb = ir.bounded_client(sb, io_deadline)
    io_deadline = getattr(sb, "io_deadline", io_deadline)
    reserve = min(5, max(0, io_deadline - started) / 5) if io_deadline is not None else 0
    deadline = io_deadline - reserve if io_deadline is not None else None
    with Run(sb, "ir") as run:
        tickers = list(dict.fromkeys(tickers if tickers is not None else [t for t, *_ in UNIVERSE]))
        done, failed = {}, {}

        def one(tk):
            if deadline is not None and time.monotonic() >= deadline:
                return tk, None, "skipped: time budget"
            try:
                b = _cache(sb, f"companies/{tk}.json", {}, strict=True) or {}
                fye = (b.get("profile") or {}).get("fiscalYearEnd") or "1231"
                ir.PERIOD_ENDS[tk] = sorted({dt.date.fromisoformat(x["end"]) for rows in (b.get("statements") or {}).values() for r in rows
                                             for x in r.get("quarterly", []) + r.get("annual", [])})
                result = ir.process_company(sb, tk, BY_TICKER[tk]["name"], BY_TICKER[tk]["cik"], refresh_site=refresh_site,
                                            fye=fye, web=web, sec_years=sec_years, deadline=deadline, max_docs=max_docs,
                                            max_pages=max_pages, repair_existing=repair_existing, publish_only=publish_only, backfill=backfill, io_deadline=io_deadline)
                return tk, result, None
            except Exception as e:
                return tk, None, str(e)[:300]

        with ThreadPoolExecutor(max(1, workers)) as ex:
            for tk, res, err in ex.map(one, tickers):
                if res is not None:
                    done[tk] = res
                    if res.get("failed") or res.get("deferred") or res.get("status") == "partial":
                        failed[tk] = f"partial: {res.get('failed', 0)} failed attempts, {res.get('deferred', 0)} deferred; see coverage"
                    print(res.get("status", "processed"), tk, {k:v for k,v in res.items() if k != "coverage"}, flush=True)
                else:
                    failed[tk] = err
                    print("fail", tk, err, flush=True)
        run.stats.update({"done": len(done), "failed": failed,
                          "deferred": sum(r.get("deferred", 0) for r in done.values()),
                          "coverage": {t: r.get("coverage") for t, r in done.items()}})
        return done, failed

def segments_changed_job(sb, time_budget=240, workers=4):
    """Recomputes segment tables for companies whose latest 10-K/10-Q differs from the one last processed."""
    fund = _cache(sb, "meta/fundamentals.json", {}, strict=True)
    idx = _cache(sb, "meta/segments.json", {"companies": {}})
    todo = [tk for tk, *_ in UNIVERSE if (fund.get(tk) or {}).get("material_accession") and
            (idx["companies"].get(tk) or {}).get("material_accession") != fund[tk]["material_accession"]]
    done, failed = segments_job(sb, todo[:12], workers=workers, time_budget=time_budget)
    idx = _cache(sb, "meta/segments.json", {"companies": {}})
    for tk in done:
        idx["companies"].setdefault(tk, {})["material_accession"] = (fund.get(tk) or {}).get("material_accession")
    sb.put_json("meta/segments.json", idx, cache="max-age=600")
    return done, failed


def ir_rotation_job(sb, per_run=8, time_budget=240):
    """Start the total I/O budget before acquiring the distributed aggregate lease."""
    from .ir import IRClient, _timeout, bounded_client
    started = time.monotonic()
    deadline = started + max(0, time_budget) if time_budget is not None else None
    client = bounded_client(sb, deadline)
    if isinstance(client, IRClient):
        _timeout(client.io_deadline)
    return _ir_rotation_job(client, per_run, time_budget, started)


@serialized_aggregates
def _ir_rotation_job(sb, per_run, time_budget, started):
    """Resume failed/skipped companies fairly alongside recent earnings and the rolling universe."""
    import copy
    state = _cache(sb, "meta/ir_rotation.json", {"cursor": 0, "done_hot": {}, "retry": []}, strict=True)
    if not isinstance(state, dict) or not isinstance(state.get("done_hot"), dict) or not isinstance(state.get("retry", []), list):
        raise ValueError("Invalid IR rotation checkpoint; refusing replacement")
    tickers = [t for t, *_ in UNIVERSE]
    pending = copy.deepcopy(state.get("coverage_updates", {}))
    if (not isinstance(pending, dict) or not set(pending).issubset(tickers)
            or (state.get("coverage_pending") and "coverage_updates" not in state)):
        raise ValueError("Invalid or legacy pending IR coverage; reconcile the coverage checkpoint explicitly")
    def coverage_time(value):
        try:
            if not isinstance(value, dict) or value.get("status") not in ("complete", "partial", "unverified"):
                raise ValueError("invalid coverage")
            stamp = dt.datetime.fromisoformat(value["updated_at"].replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                raise ValueError("coverage timestamp requires a timezone")
            return stamp
        except (KeyError, TypeError, AttributeError, ValueError) as error:
            raise ValueError("Invalid pending IR coverage timestamp or status") from error
    for value in pending.values():
        coverage_time(value)
    if len(json.dumps(pending, separators=(",", ":"), allow_nan=False).encode()) > 4 * 1024 * 1024:
        raise ValueError("Pending IR coverage exceeds checkpoint size limit")
    recent = sb.select("filings", "select=ticker,items,filed&form=eq.8-K&filed=gte." + (dt.date.today() - dt.timedelta(days=4)).isoformat())
    if not tickers or per_run <= 0:
        return {}, {}
    retry = [t for t in dict.fromkeys(state.get("retry", [])) if t in tickers]
    picked_retry = retry[:max(1, per_run // 2)]
    hot = [r["ticker"] for r in recent if "2.02" in (r.get("items") or "")]
    hot = [t for t in dict.fromkeys(hot) if t in tickers and t not in picked_retry and state["done_hot"].get(t) != dt.date.today().isoformat()][:max(0, per_run - len(picked_retry))]
    # Newly activated issuers with no IR coverage entry take up to half the remaining slots, so a cohort's first
    # document pass finishes in days instead of one full cursor cycle. The cursor still advances only over `rest`.
    covered = set(((_cache(sb, "meta/coverage_ir.json", {}) or {}).get("companies") or {}))
    slots = max(0, per_run - len(hot) - len(picked_retry))
    fresh = [t for t in tickers if t not in covered and t not in pending and t not in hot and t not in picked_retry][:slots // 2]
    start = state.get("cursor", 0) % len(tickers)
    rest = [t for t in tickers[start:] + tickers[:start]
            if t not in hot and t not in picked_retry and t not in fresh][:max(0, slots - len(fresh))]
    selected = picked_retry + hot + fresh + rest
    remaining = max(0, time_budget - (time.monotonic() - started)) if time_budget is not None else None
    done, failed = ir_job(sb, selected, workers=4, time_budget=remaining)
    for tk, result in done.items():
        if tk not in tickers:
            raise ValueError("IR coverage result outside the current universe")
        value = result.get("coverage")
        incoming_time = coverage_time(value)
        if tk not in pending or incoming_time >= coverage_time(pending[tk]):
            pending[tk] = copy.deepcopy(value)
    if len(json.dumps(pending, separators=(",", ":"), allow_nan=False).encode()) > 4 * 1024 * 1024:
        raise ValueError("Pending IR coverage exceeds checkpoint size limit")
    for tk in hot:
        if tk in done and tk not in failed:
            state["done_hot"][tk] = dt.date.today().isoformat()
    state["retry"] = [t for t in retry if t not in selected] + [t for t in selected if t in failed or t not in done]
    if rest:
        state["cursor"] = (tickers.index(rest[-1]) + 1) % len(tickers)
    state["updated_at"] = now_iso()
    state["coverage_pending"] = True
    state["coverage_updates"] = pending
    sb.put_json("meta/ir_rotation.json", state, cache="no-store")
    if time_budget is not None and time.monotonic() - started >= time_budget:
        failed["coverage_index"] = "skipped: time budget"
        return done, failed
    try:
        from .kpitools import build_index
        # Bound replay work while retaining older dirty companies until acknowledged.
        replay = dict(list(pending.items())[:min(32, max(8, per_run * 2))])
        build_index(sb, coverage_updates=copy.deepcopy(replay))
        remaining_updates = {tk: value for tk, value in pending.items() if tk not in replay}
        acknowledged = {**state, "coverage_pending": bool(remaining_updates), "coverage_updates": remaining_updates}
        sb.put_json("meta/ir_rotation.json", acknowledged, cache="no-store")
        if remaining_updates:
            failed["coverage_index"] = f"deferred: {len(remaining_updates)} pending company coverage updates"
    except Exception as e:
        failed["coverage_index"] = str(e)[:300]
        print("coverage index failed", e)
    return done, failed

# ---------------------------------------------------------------- sectors
def sectors_job(sb, step="publish", time_budget=None):
    """step: "prices" refreshes the monthly price cache, "publish" rebuilds every sector and industry file,
    "notes" writes the weekly sector notes."""
    from . import sectors
    from .universe import UNIVERSE as _U
    with Run(sb, "sectors" if step == "publish" else f"sectors_{step}") as run:
        if step == "prices":
            syms = [t for t, *_ in _U] + list(SECTOR_ETF.values()) + ["SPY"]
            _, st = sectors.refresh_pxm(sb, syms, time_budget=time_budget or 240)
        elif step == "notes":
            st = sectors.notes(sb)
        else:
            st = sectors.publish(sb, time_budget=time_budget)
        run.stats.update(st if isinstance(st, dict) else {"result": st})
        return st


# ---------------------------------------------------------------- screener factor table
def screener_job(sb, full=False, time_budget=None, workers=6):
    """Builds screener.json (150+ sourced fields per company), screener/src/{T}.json provenance and screener/corr.json.
    Runs after prices, companies and segments; full=True refetches the 10-year close history and every company."""
    from .screener import screener_job as build
    return build(sb, full=full, time_budget=time_budget, workers=workers)


# ---------------------------------------------------------------- standardized KPIs
def kpistd_job(sb, tickers=None, full=False, use_llm=True, time_budget=None):
    """Maps company KPI ids onto the canonical taxonomy (rules plus reviewed model proposals) and publishes kpistd/*.json.
    Incremental: only companies whose KPI definitions changed since the last run are remapped."""
    from .kpistd import map_job
    with Run(sb, "kpistd") as run:
        st = map_job(sb, tickers=tickers, full=full, use_llm=use_llm, time_budget=time_budget)
        run.stats.update(st)
        return st
