"""Bounded, source-proved repair of legacy financial normalization.

Planning is read-only remotely and writes sealed local candidates. Apply and
rollback are separate commands using the existing aggregate lease. Neither
command fetches SEC, prices, news, documents or model output.

Credentials: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY (server side only).
"""
import argparse
import copy
import datetime as dt
import gzip
import hashlib
import json
import re
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path
from urllib.parse import quote, unquote

from . import fundamentals, jobs, repair, sec
from .common import Supabase, now_iso, request, serialized_aggregates
from .source_provenance import validate_statement_provenance
from .source_quality import partition_supported_periods, retain_proved_filing_tables
from .universe import BY_TICKER, UNIVERSE

MAX_BATCH = 25
FACT_FIELDS = ('ticker', 'statement', 'label', 'kind', 'period_end', 'period_start',
               'concept', 'unit', 'value', 'accession', 'form', 'filed', 'derived')


class RepairSupabase(Supabase):
    """Repair writes never retry beyond a lost lease or an ambiguous response."""
    def _write(self, url, **kwargs):
        abort = getattr(self, '_repair_abort', None)
        if abort is not None and abort.is_set():
            raise RuntimeError('Repair mutations stopped after another worker failed')
        lease = getattr(self, '_aggregate_lease', None)
        if lease is None:
            raise RuntimeError('Repair mutations require the aggregate lease')
        lease.check()
        remaining = lease.valid_until - time.monotonic() - 5
        if remaining < 1:
            raise RuntimeError('Aggregate lease has insufficient time for a bounded repair write')
        result = request(url, timeout=min(30, remaining), retries=1, **kwargs)
        lease.check()
        return result

    def upsert(self, table, rows, on_conflict, batch=500):
        rows = list(rows)
        for offset in range(0, len(rows), batch):
            self._write(f'{self.url}/rest/v1/{table}?on_conflict={on_conflict}', method='POST', data=rows[offset:offset + batch],
                        headers=self._h({'Prefer': 'resolution=merge-duplicates,return=minimal', 'Content-Type': 'application/json'}))
        return len(rows)

    def put_object(self, bucket, path, content, content_type='application/json', cache='max-age=300'):
        body = content.encode() if isinstance(content, str) else content
        self._write(f'{self.url}/storage/v1/object/{bucket}/{quote(path)}', method='POST', data=body,
                    headers=self._h({'Content-Type': content_type, 'x-upsert': 'true', 'cache-control': cache}))
        return len(body)

    def delete(self, table, query):
        if not query:
            raise ValueError('Repair deletion requires exact filters')
        self._write(f'{self.url}/rest/v1/{table}?{query}', method='DELETE', headers=self._h({'Prefer': 'return=minimal'}))

    def insert(self, *args, **kwargs):
        raise RuntimeError('Unscoped inserts are not part of financial repair')

    def update(self, *args, **kwargs):
        raise RuntimeError('Unscoped updates are not part of financial repair')

    def rpc(self, fn, args):
        if fn not in ('acquire_etl_lease', 'release_etl_lease'):
            raise ValueError('Only the aggregate lease RPCs are allowed during repair')
        return json.loads(request(f'{self.url}/rest/v1/rpc/{fn}', method='POST', data=args,
                                  headers=self._h(), timeout=15, retries=1) or 'null')

    def delete_nonadditive_fact_batches(self, rows, backup_path, batch_size=20):
        """Optional exact-filter deletion; only selected with an explicit apply flag.

        The caller must supply the existing per-issuer, content-addressed private
        backup. Each AND tuple retains every single-row guard; only their OR is
        batched. No mutation happens until the complete request set is validated.
        """
        rows = list(rows)
        if not rows:
            return {'deleted': 0, 'protected_reported': 0, 'delete_requests': 0}
        if type(batch_size) is not int or not 1 <= batch_size <= 20 or any(not unsupported(r) for r in rows):
            raise ValueError('Exact deletion batches require 1–20 unsupported derived quarterly rows')
        fact_map(rows)
        tickers = {r['ticker'] for r in rows}
        if len(tickers) != 1:
            raise ValueError('Exact deletion batches cannot span issuers')
        match = re.fullmatch(r'raw/(repairs/nonadditive-facts/([^/]+)/([a-f0-9]{64})\.json)', backup_path)
        if not match or unquote(match[2]) != rows[0]['ticker']:
            raise ValueError('Exact deletion requires the matching private per-issuer backup')
        lease = getattr(self, '_aggregate_lease', None)
        if lease is None:
            raise RuntimeError('Exact deletion requires the aggregate lease')
        lease.check()
        body = self.get_object('raw', match[1]) or b''
        if digest(body) != match[3]:
            raise RuntimeError('Exact deletion backup verification failed')
        backup = json.loads(body)
        if backup.get('ticker') != rows[0]['ticker'] or fact_map(backup.get('rows') or []) != fact_map(rows):
            raise RuntimeError('Exact deletion backup rows do not match the requested removal')
        order = '&order=ticker.asc,statement.asc,label.asc,kind.asc,period_end.asc'
        def request_size(batch):
            prefix = getattr(self, 'url', '') + '/rest/v1/facts?'
            return max(len((prefix + exact_fact_query(batch)).encode()),
                       len((prefix + 'select=*&' + exact_fact_query(batch, guarded=False) + order).encode()))
        batches, current = [], []
        for row in rows:
            proposed = current + [row]
            if current and (len(proposed) > batch_size or request_size(proposed) > 6000):
                batches.append(current)
                current = []
            current.append(row)
            if request_size(current) > 6000:
                raise ValueError('A single exact deletion predicate exceeds the 6000-byte request limit')
        if current:
            batches.append(current)
        deleted = protected = 0
        for batch in batches:
            lease.check()
            self.delete('facts', exact_fact_query(batch))
            remaining = self.select('facts', 'select=*&' + exact_fact_query(batch, guarded=False) + order)
            current = fact_map(remaining)
            if set(current) - set(fact_map(batch)):
                raise RuntimeError('Exact deletion read-back returned rows outside the requested keys')
            if any(r.get('derived') is not False for r in remaining):
                raise RuntimeError('Unsupported derived fact still present after guarded batch deletion')
            protected += len(remaining)
            deleted += len(batch) - len(remaining)
        return {'deleted': deleted, 'protected_reported': protected, 'delete_requests': len(batches)}


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(value).hexdigest()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.part')
    temporary.write_bytes(encoded(value))
    temporary.replace(path)


def run_bounded(items, work, on_complete, workers=1, stop=None):
    """Bounded executor used under a single held lease when explicitly selected.

    Dispatch is bounded; only the coordinator calls on_complete (receipts/fund
    updates). Failure stops new issuers and waits for every in-flight task before
    returning or raising, so the caller cannot release its lease prematurely.
    """
    if type(workers) is not int or not 1 <= workers <= 8:
        raise ValueError('Repair concurrency must be between 1 and 8')
    stop = stop if stop is not None else threading.Event()
    iterator, results = iter(items), []
    def guarded(item):
        if stop.is_set():
            raise RuntimeError('Repair work stopped before starting another issuer')
        try:
            return work(item)
        except BaseException:
            stop.set()
            raise
    with ThreadPoolExecutor(max_workers=workers) as executor:
        pending = {}
        def dispatch():
            while len(pending) < workers and not stop.is_set():
                try:
                    item = next(iterator)
                except StopIteration:
                    return
                pending[executor.submit(guarded, item)] = item
        dispatch()
        try:
            while pending:
                done, _ = wait(pending, return_when=FIRST_COMPLETED)
                # Observe every error before dispatching replacements for this group.
                for future in done:
                    if future.exception() is not None:
                        future.result()
                for future in done:
                    item, result = pending.pop(future), future.result()
                    results.append(result)
                    on_complete(item, result)
                dispatch()
            if stop.is_set():
                raise RuntimeError('Repair work stopped')
        except BaseException:
            stop.set()
            for future in pending:
                future.cancel()
            # Executor context shutdown waits for running tasks before propagating.
            raise
    return results


def fact_key(row):
    return tuple(row[k] for k in jobs.FACT_KEY)


def fact_map(rows):
    result = {}
    for row in rows:
        key = fact_key(row)
        if key in result:
            raise ValueError('Duplicate fact key in repair snapshot; repeat the ordered read')
        result[key] = {field: row.get(field) for field in FACT_FIELDS}
    return result


def unsupported(row):
    return (row.get('kind') == 'Q' and row.get('derived') is True and
            (row.get('label') in jobs.NONADDITIVE_FACT_LABELS or row.get('concept') in jobs.NONADDITIVE_FACT_CONCEPTS))


def exact_fact_query(rows, guarded=True):
    """PostgREST OR of fully quoted AND tuples, with an outer issuer boundary."""
    rows = list(rows)
    if not rows or len({r['ticker'] for r in rows}) != 1 or any(r['kind'] != 'Q' for r in rows):
        raise ValueError('Exact fact filter requires one issuer and quarterly keys')
    groups = []
    for row in rows:
        keys = jobs.FACT_KEY + (('accession', 'concept', 'value') if guarded else ())
        clauses = [f'{key}.eq.' + json.dumps(row[key], ensure_ascii=False, allow_nan=False) for key in keys]
        if guarded:
            clauses.append('derived.is.true')
        groups.append('and(' + ','.join(clauses) + ')')
    # Unlike logical tuple literals, simple equality values do not strip JSON
    # quotes. Keep the outer issuer filter percent-encoded without adding quotes.
    outer = 'ticker=eq.' + quote(rows[0]['ticker'], safe='') + '&kind=eq.Q'
    if guarded:
        outer += '&derived=is.true'
    return outer + '&or=' + quote('(' + ','.join(groups) + ')', safe='(),.')


def company_facts(sb, ticker):
    rows = sb.select('facts', 'select=*&ticker=eq.' + quote(ticker, safe='') +
                     '&order=statement.asc,label.asc,kind.asc,period_end.asc')
    fact_map(rows)  # Detect duplicate pages instead of silently losing backup rows.
    return rows


def rows_for(bundle):
    rows = []
    for statement, lines in bundle['statements'].items():
        for line in lines:
            for kind, bucket in (('A', 'annual'), ('Q', 'quarterly')):
                for point in line.get(bucket, []):
                    rows.append(dict(ticker=bundle['ticker'], statement=statement, label=line['label'], kind=kind,
                        period_end=point['end'], period_start=point.get('start'), concept=point.get('c') or line['concept'],
                        unit=line['unit'], value=point['v'], accession=point['accn'], form=point['form'],
                        filed=point['filed'], derived=bool(point.get('derived'))))
    return rows


def projected_facts(before, replacements):
    result = fact_map(before)
    result.update(fact_map(replacements))
    return [row for _, row in sorted(result.items()) if not unsupported(row)]


def review_database_replacements(before, replacements):
    """Protect directly reported rows even when they never reached the bundle."""
    proposed = fact_map(replacements)
    result = {'blockers': [], 'review_required': []}
    for key, prior in sorted(fact_map(before).items()):
        if prior.get('derived') is not False:
            continue
        candidate = proposed.get(key)
        reason, bucket = None, 'review_required'
        if candidate is None:
            reason = 'reported_database_period_missing'
        elif candidate.get('derived'):
            reason, bucket = 'reported_to_derived', 'blockers'
        else:
            old_date, new_date = prior.get('filed'), candidate.get('filed')
            changed = any(prior.get(k) != candidate.get(k) for k in ('value', 'unit', 'period_start'))
            if old_date and (not new_date or new_date < old_date):
                reason, bucket = 'older_reported_filing', 'blockers'
            elif changed and (not old_date or not new_date):
                reason = 'changed_reported_value_unknown_date'
            elif old_date == new_date and changed:
                reason = 'changed_reported_value_same_date'
        if reason:
            result[bucket].append({'key': list(key), 'reason': reason, 'before': prior, 'after': candidate})
    return result


def inventory(sb):
    fund = sb.get_json('meta/fundamentals.json') or {}
    active = {t for t, *_ in UNIVERSE}
    labels = ','.join('"' + x + '"' for x in sorted(jobs.NONADDITIVE_FACT_LABELS))
    concepts = ','.join(sorted(jobs.NONADDITIVE_FACT_CONCEPTS))
    predicate = quote(f'(label.in.({labels}),concept.in.({concepts}))', safe='(),.')
    rows = sb.select('facts', 'select=ticker,label,concept,kind,derived&kind=eq.Q&derived=is.true&or=' + predicate +
                     '&order=ticker.asc,statement.asc,label.asc,kind.asc,period_end.asc')
    rows = [r for r in rows if r.get('ticker') in active and unsupported(r)]
    legacy = sorted(t for t in active if not fundamentals.normalized(fund.get(t)))
    invalid = sorted({r['ticker'] for r in rows})
    return {'observed_at': now_iso(), 'active_companies': len(active), 'legacy_entries': len(legacy), 'legacy_tickers': legacy,
            'unsupported_derived_rows': len(rows), 'unsupported_tickers': invalid,
            'repair_tickers': sorted(set(legacy) | set(invalid))}


def _artifact(root, ticker, name, value, raw=False):
    path = root / ticker / name
    path.parent.mkdir(parents=True, exist_ok=True)
    body = value if raw else encoded(value)
    path.write_bytes(body)
    return {'path': str(path.relative_to(root)), 'sha256': digest(body), 'bytes': len(body)}


def _load_artifact(root, record, raw=False):
    path = (root / record['path']).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError('Repair artifact leaves the approved directory')
    body = path.read_bytes()
    if len(body) != record['bytes'] or digest(body) != record['sha256']:
        raise ValueError('Repair artifact hash mismatch')
    return body if raw else json.loads(body)


def retain_proved_reported_observations(candidate, before_facts, facts, as_of):
    """Keep a reported source when the alternative derivation has the same value.

    This never trusts the database flag alone: the entire reported observation
    must match the archived source and subsequently pass the full source proof.
    A changed value or unproved reported observation still reaches the blocker.
    """
    before = fact_map(before_facts)
    retained = 0
    for statement, lines in candidate['statements'].items():
        for line in lines:
            allowed = dict(sec.CONCEPTS[statement])[line['label']]
            for kind, bucket in (('A', 'annual'), ('Q', 'quarterly')):
                for index, point in enumerate(line.get(bucket, [])):
                    prior = before.get((candidate['ticker'], statement, line['label'], kind, point['end']))
                    if (not prior or prior['derived'] is not False or not point.get('derived')
                            or prior['value'] != point['v'] or prior['period_start'] != point.get('start')
                            or prior['unit'] != line['unit'] or prior['concept'] not in allowed
                            or not prior['filed'] or prior['filed'] > as_of):
                        continue
                    observations = (facts.get('us-gaap', {}).get(prior['concept'], {}).get('units', {}).get(prior['unit']) or [])
                    matches = [r for r in observations if (r.get('val'), r.get('start'), r.get('end'), r.get('accn'), r.get('filed'), r.get('form')) ==
                               (prior['value'], prior['period_start'], prior['period_end'], prior['accession'], prior['filed'], prior['form'])]
                    if not matches:
                        continue
                    reported = {'v': prior['value'], 'end': prior['period_end'], 'accn': prior['accession'],
                                'form': prior['form'], 'filed': prior['filed'], 'c': prior['concept'],
                                'available_at': prior['filed'], 'accessions': [prior['accession']],
                                'vintage': 'latest', 'validation_status': 'reported',
                                'selection_reason': 'Preserved exact archived reported observation over an equal derived value'}
                    if prior['period_start'] is not None:
                        reported['start'] = prior['period_start']
                    line[bucket][index] = reported
                    history = line.setdefault('filing_history', {}).setdefault(bucket, [])
                    if not any(all(r.get(k) == reported.get(k) for k in ('v', 'start', 'end', 'accn', 'filed', 'c')) and not r.get('derived') for r in history):
                        history.append(dict(reported))
                        history.sort(key=lambda r: (r['end'], r['filed'], r['accn']))
                    retained += 1
    return retained


def _candidate(bundle, raw, as_of, generated, before_facts=()):
    ticker, cik = bundle['ticker'], BY_TICKER[bundle['ticker']]['cik']
    cf = json.loads(raw)
    if int(cf.get('cik') or 0) != cik or int(bundle.get('cik') or 0) != cik:
        raise ValueError('Archived source or company bundle CIK identity mismatch')
    candidate, review = repair.rebuild(bundle, cf['facts'], as_of=as_of)
    candidate['generated'] = generated
    preserved = retain_proved_reported_observations(candidate, before_facts, cf['facts'], as_of)
    candidate['statements'], excluded = partition_supported_periods(candidate['statements'])
    if not any(line.get('annual') or line.get('quarterly') for lines in candidate['statements'].values() for line in lines):
        raise ValueError('No supported source facts; refusing empty replacement')
    sha = digest(raw)
    proof = validate_statement_provenance(raw, candidate['statements'], cik, sha)
    proof_path = f'sources/companies/{ticker}/{sha}.json'
    candidate['source_archive'] = f'sec/companyfacts/archives/CIK{cik:010d}/{sha}.json'
    candidate['provenance'] = {k: proof[k] for k in ('version', 'status', 'cik', 'source_url', 'snapshot_sha256', 'counts')}
    candidate['provenance'].update(proof_path=proof_path, gap_fill='disabled_unarchived_sources')
    candidate['quarantined_statement_points'] = excluded
    candidate['normalization'].update(review_required=False, repair='archived_source_proved')
    tables = sec.filing_tables(cf['facts'], candidate['statements'], candidate.get('filings') or [], cik)
    candidate['filingTables'], omitted = retain_proved_filing_tables(tables, proof)
    candidate['provenance']['reconstructed_tables'] = {'scope': 'verified_source_records_only', 'omitted_cells': omitted}
    candidate['fundamentals'] = fundamentals.compute(candidate['statements'], candidate['profile'], None)
    # A raw cache that predates a reported period is not a complete repair.
    prior_reported = {key for key, row in fact_map(rows_for(bundle)).items() if not row['derived']}
    lost_reported = sorted(prior_reported - set(fact_map(rows_for(candidate))))
    review.update(source_sha256=sha, quarantined_points=len(excluded), lost_reported_periods=[list(k) for k in lost_reported],
                  fundamentals=candidate['fundamentals'], source_proof=proof['counts'], preserved_reported_observations=preserved)
    return candidate, proof, review


def cleanup_bundle(bundle, generated):
    """Remove explicit unsupported nonadditive quarter derivations, never restamp."""
    candidate = copy.deepcopy(bundle)
    removed = 0
    for lines in candidate.get('statements', {}).values():
        for line in lines:
            line_is_nonadditive = line.get('label') in jobs.NONADDITIVE_FACT_LABELS or line.get('concept') in jobs.NONADDITIVE_FACT_CONCEPTS
            for container in (line, line.get('filing_history') or {}):
                points = container.get('quarterly') or []
                kept = [p for p in points if not (p.get('derived') and
                        (line_is_nonadditive or p.get('c') in jobs.NONADDITIVE_FACT_CONCEPTS))]
                removed += len(points) - len(kept)
                if len(points) != len(kept):
                    container['quarterly'] = kept
    if removed:
        if candidate.get('provenance'):
            raise ValueError('Bundle has an indexed source proof; a revalidated proof is required before removing points')
        candidate['nonadditive_cleanup'] = {'version': 1, 'removed_quarterly_points': removed, 'performed_at': generated,
                                            'scope': 'explicit_derived_quarterly_eps_and_share_points_only'}
    return candidate, removed


def plan_cleanup(sb, tickers, output, clean_bundles=False, workers=4):
    """Plan exact removal only: no replacement financial values and no version upgrade."""
    output = Path(output)
    if (output / 'plan.json').exists():
        raise FileExistsError('Refusing to overwrite an existing repair plan')
    tickers = list(dict.fromkeys(tickers))
    active = {t for t, *_ in UNIVERSE}
    if not 1 <= len(tickers) <= MAX_BATCH or any(t not in active for t in tickers) or not 1 <= workers <= 8:
        raise ValueError('Cleanup requires 1–25 active tickers and 1–8 read-only workers')
    fund = sb.get_json('meta/fundamentals.json')
    if not isinstance(fund, dict) or not fund:
        raise ValueError('Fundamentals snapshot is unavailable')
    plan = {'version': 1, 'pipeline_version': jobs.PIPELINE_VERSION, 'mode': 'cleanup_only',
            'created_at': now_iso(), 'clean_bundles': bool(clean_bundles), 'companies': []}
    def one(ticker):
        entry = {'ticker': ticker, 'status': 'blocked'}
        try:
            bundle = sb.get_json(f'companies/{ticker}.json')
            if not bundle or bundle.get('ticker') != ticker or int(bundle.get('cik') or 0) != BY_TICKER[ticker]['cik'] or not fund.get(ticker):
                raise ValueError('Missing or mismatched company identity/aggregate metadata')
            before = company_facts(sb, ticker)
            candidate, removed = cleanup_bundle(bundle, plan['created_at']) if clean_bundles else (copy.deepcopy(bundle), 0)
            obsolete = [r for r in before if unsupported(r)]
            assets = {'before_bundle': ('before-company.json', bundle), 'before_facts': ('before-facts.json', before),
                      'before_fund': ('before-fundamental.json', fund[ticker]), 'candidate_bundle': ('candidate-company.json', candidate),
                      'candidate_facts': ('candidate-facts.json', []), 'candidate_fund': ('candidate-fundamental.json', fund[ticker]),
                      'cleanup_evidence': ('unsupported-derived-rows.json', obsolete)}
            entry.update(status='ready', artifacts={k: _artifact(output, ticker, name, value) for k, (name, value) in assets.items()},
                         review={'database_rows_before': len(before), 'unsupported_deletions': len(obsolete),
                                 'expected_rows_after': len(before) - len(obsolete), 'replacement_rows': 0,
                                 'bundle_points_removed': removed, 'pipeline_version_before': bundle.get('pipeline_version'),
                                 'pipeline_version_after': candidate.get('pipeline_version'),
                                 'full_normalization': 'unchanged; legacy entries remain pending'})
        except Exception as error:
            entry['error'] = str(error)[:500]
        return entry
    with ThreadPoolExecutor(max_workers=workers) as executor:
        plan['companies'] = list(executor.map(one, tickers))
    atomic_json(output / 'plan.json', plan)
    return plan


def plan_repair(sb, tickers, output, as_of=None, workers=4):
    """Read existing raw objects and relational rows; never acquire a write lease."""
    output = Path(output)
    if (output / 'plan.json').exists():
        raise FileExistsError('Refusing to overwrite an existing repair plan')
    tickers = list(dict.fromkeys(tickers))
    active = {t for t, *_ in UNIVERSE}
    if not 1 <= len(tickers) <= MAX_BATCH or any(t not in active for t in tickers):
        raise ValueError(f'Repair requires 1–{MAX_BATCH} explicitly selected active tickers')
    if not 1 <= workers <= 8:
        raise ValueError('Planning workers must be between 1 and 8')
    as_of = as_of or dt.datetime.now(dt.timezone.utc).date().isoformat()
    if dt.date.fromisoformat(as_of).isoformat() != as_of:
        raise ValueError('Expected an ISO as-of date')
    if as_of > dt.datetime.now(dt.timezone.utc).date().isoformat():
        raise ValueError('Repair as-of date cannot be in the future')
    fund = sb.get_json('meta/fundamentals.json')
    if not isinstance(fund, dict) or not fund:
        raise ValueError('Fundamentals snapshot is unavailable')
    plan = {'version': 1, 'pipeline_version': jobs.PIPELINE_VERSION, 'mode': 'dry_run',
            'created_at': now_iso(), 'as_of': as_of, 'companies': []}
    def one(ticker):
        entry = {'ticker': ticker, 'status': 'blocked'}
        try:
            bundle = sb.get_json(f'companies/{ticker}.json')
            if not bundle or bundle.get('ticker') != ticker or not fund.get(ticker):
                raise ValueError('Missing company bundle or fundamental metadata')
            cik = BY_TICKER[ticker]['cik']
            source = bundle.get('source_archive') or f'sec/companyfacts/CIK{cik:010d}.json'
            if not re.fullmatch(rf'sec/companyfacts/(?:archives/CIK{cik:010d}/[a-f0-9]{{64}}|CIK{cik:010d})\.json', source):
                raise ValueError('Source archive path is outside the selected issuer')
            compressed = sb.get_object('raw', source + '.gz')
            if not compressed:
                raise ValueError('Existing archived Company Facts object is unavailable; no SEC refetch attempted')
            raw = gzip.decompress(compressed)
            before = company_facts(sb, ticker)
            candidate, proof, review = _candidate(bundle, raw, as_of, plan['created_at'], before)
            replacements = rows_for(candidate)
            review['unsupported_derived_rows'] = sum(unsupported(r) for r in before)
            review['replacement_rows'] = len(replacements)
            review['expected_rows_after'] = len(projected_facts(before, replacements))
            database_review = review_database_replacements(before, replacements)
            review['database_replacements'] = database_review
            metadata = jobs.company_fundamental(ticker, candidate['statements'], candidate['profile'], candidate.get('filings') or [],
                candidate.get('warnings') or [], {'mode': 'guarded_normalization_repair'}, candidate.get('ix'), candidate.get('release'))
            assets = {'source': ('companyfacts.json', raw, True), 'before_bundle': ('before-company.json', bundle, False),
                      'before_facts': ('before-facts.json', before, False), 'before_fund': ('before-fundamental.json', fund[ticker], False),
                      'candidate_bundle': ('candidate-company.json', candidate, False), 'candidate_facts': ('candidate-facts.json', replacements, False),
                      'candidate_fund': ('candidate-fundamental.json', metadata, False), 'proof': ('source-proof.json', proof, False)}
            status = ('blocked' if database_review['blockers'] else 'review_required'
                      if review['lost_reported_periods'] or database_review['review_required'] else 'ready')
            entry.update(artifacts={k: _artifact(output, ticker, name, value, raw=is_raw) for k, (name, value, is_raw) in assets.items()},
                         source_path='raw/' + source + '.gz', review=review,
                         status=status)
        except Exception as error:
            entry['error'] = str(error)[:500]
        return entry
    with ThreadPoolExecutor(max_workers=workers) as executor:
        plan['companies'] = list(executor.map(one, tickers))
    output.mkdir(parents=True, exist_ok=True)
    atomic_json(output / 'plan.json', plan)
    return plan


def _load_plan(path):
    path = Path(path)
    plan = json.loads(path.read_bytes())
    if plan.get('version') != 1 or plan.get('pipeline_version') != jobs.PIPELINE_VERSION or plan.get('mode') not in ('dry_run', 'cleanup_only'):
        raise ValueError('Unsupported repair plan version')
    companies = plan.get('companies') or []
    if not 1 <= len(companies) <= MAX_BATCH or any(c.get('status') != 'ready' for c in companies):
        raise ValueError('Only a bounded plan with every candidate ready can be applied')
    tickers = [c['ticker'] for c in companies]
    active = {t for t, *_ in UNIVERSE}
    if len(set(tickers)) != len(tickers) or any(t not in active for t in tickers):
        raise ValueError('Repair plan requires unique active issuers')
    results = []
    for entry in companies:
        data = {k: _load_artifact(path.parent, v, raw=k == 'source') for k, v in entry['artifacts'].items()}
        if plan['mode'] == 'cleanup_only':
            candidate, _ = cleanup_bundle(data['before_bundle'], plan['created_at']) if plan.get('clean_bundles') else (data['before_bundle'], 0)
            if (candidate != data['candidate_bundle'] or data['candidate_facts'] != [] or data['candidate_fund'] != data['before_fund']
                    or data['cleanup_evidence'] != [r for r in data['before_facts'] if unsupported(r)]
                    or candidate['ticker'] != entry['ticker'] or any(r['ticker'] != entry['ticker'] for r in data['before_facts'])):
                raise ValueError('Cleanup plan may only remove the exact unsupported derived rows/points')
            data.update(ticker=entry['ticker'])
            results.append(data)
            continue
        candidate, proof, regenerated_review = _candidate(data['before_bundle'], data['source'], plan['as_of'], plan['created_at'], data['before_facts'])
        if regenerated_review['lost_reported_periods']:
            raise ValueError('Candidate omits reported bundle periods; review is required')
        if candidate != data['candidate_bundle'] or proof != data['proof'] or rows_for(candidate) != data['candidate_facts']:
            raise ValueError('Candidate no longer matches the source-proved normalization')
        database_review = review_database_replacements(data['before_facts'], data['candidate_facts'])
        if database_review['blockers'] or database_review['review_required']:
            raise ValueError('Candidate would regress or change reported database observations without review')
        metadata = jobs.company_fundamental(entry['ticker'], candidate['statements'], candidate['profile'], candidate.get('filings') or [],
            candidate.get('warnings') or [], {'mode': 'guarded_normalization_repair'}, candidate.get('ix'), candidate.get('release'))
        if metadata != data['candidate_fund'] or candidate['ticker'] != entry['ticker']:
            raise ValueError('Candidate aggregate metadata does not match the verified statements')
        data.update(ticker=entry['ticker'])
        results.append(data)
    return plan, results


def _wait_equal(read, expected, description):
    for delay in (0, 1, 2, 4, 8):
        if delay:
            time.sleep(delay)
        if read() == expected:
            return
    raise RuntimeError(f'{description} read-back mismatch')


def _publish_aggregate(sb, fund):
    sb.put_json('meta/fundamentals.json', fund, cache='no-store')
    _wait_equal(lambda: sb.get_json('meta/fundamentals.json'), fund, 'Fundamentals')
    snapshot = jobs.build_index(sb, fundamentals_snapshot=fund, return_snapshot=True)
    _wait_equal(lambda: sb.get_json('index.json'), snapshot, 'Public index')
    fields = ('ticker', 'as_of', 'price', 'market_cap', 'eps_ttm', 'pe_ttm', 'revenue_ttm', 'net_income_ttm', 'fcf_ttm', 'ps_ttm')
    expected = {row['ticker']: {key: row.get(key) for key in fields} for row in snapshot['companies']}
    def metrics():
        return {row['ticker']: {key: row.get(key) for key in fields}
                for row in sb.select('company_metrics', 'select=' + ','.join(fields)) if row['ticker'] in expected}
    _wait_equal(metrics, expected, 'Relational index metrics')


@serialized_aggregates
def apply_plan(sb, path, workers=1, delete_batch_size=1, defer_aggregate=False):
    """Apply a reviewed plan; cleanup alone may explicitly defer aggregate writes."""
    if isinstance(sb, Supabase) and not isinstance(sb, RepairSupabase):
        raise TypeError('Use RepairSupabase for bounded, lease-checked production writes')
    if type(workers) is not int or not 1 <= workers <= 8 or type(delete_batch_size) is not int or not 1 <= delete_batch_size <= 20:
        raise ValueError('Apply requires 1–8 issuer workers and 1–20 rows per deletion batch')
    if delete_batch_size > 1 and not callable(getattr(sb, 'delete_nonadditive_fact_batches', None)):
        raise TypeError('Batched fact deletion requires the reviewed repair client')
    plan, entries = _load_plan(path)
    if type(defer_aggregate) is not bool or defer_aggregate and plan['mode'] != 'cleanup_only':
        raise ValueError('Aggregate deferral is an explicit cleanup-only option')
    fund = sb.get_json('meta/fundamentals.json') or {}
    original_fund = copy.deepcopy(fund)
    def preflight(entry):
        ticker = entry['ticker']
        if (fund.get(ticker) != entry['before_fund'] or sb.get_json(f'companies/{ticker}.json') != entry['before_bundle']
                or fact_map(company_facts(sb, ticker)) != fact_map(entry['before_facts'])):
            raise ValueError(f'{ticker}: production inputs changed since planning; create a fresh plan')
    if workers == 1:
        for entry in entries:
            preflight(entry)
    else:
        run_bounded(entries, preflight, lambda *_: None, workers=workers)
    backup = {'version': 1, 'created_at': now_iso(), 'plan_sha256': digest(Path(path).read_bytes()),
              'companies': [{k: v for k, v in entry.items() if k not in ('source', 'proof')} for entry in entries]}
    body = encoded(backup)
    backup_path = 'repairs/financial-normalization/' + digest(body) + '.json.gz'
    compressed = gzip.compress(body, mtime=0)
    sb.put_object('raw', backup_path, compressed, content_type='application/gzip', cache='no-store')
    if sb.get_object('raw', backup_path) != compressed:
        raise RuntimeError('Private repair backup verification failed; no facts or public objects changed')
    receipt = {'version': 1, 'mode': plan['mode'], 'backup': 'raw/' + backup_path, 'repaired': [], 'fact_cleanup': {},
               'status': 'applying', 'workers': workers, 'delete_batch_size': delete_batch_size,
               'defer_aggregate': defer_aggregate, 'aggregate_status': 'deferred' if defer_aggregate else 'pending'}
    # Store the recovery pointer locally before any public object or fact changes.
    receipt_path = Path(path).with_name('apply-receipt.json')
    atomic_json(receipt_path, receipt)
    def one(entry):
        ticker, candidate = entry['ticker'], entry['candidate_bundle']
        if plan['mode'] != 'cleanup_only':
            archive = candidate['source_archive'] + '.gz'
            source_gz = sb.get_object('raw', archive)
            if source_gz is not None:
                if gzip.decompress(source_gz) != entry['source']:
                    raise RuntimeError('Existing immutable source archive verification failed')
            else:
                source_gz = gzip.compress(entry['source'], mtime=0)
                sb.put_object('raw', archive, source_gz, content_type='application/gzip', cache='no-store')
                _wait_equal(lambda: sb.get_object('raw', archive), source_gz, 'Immutable source archive')
            proof_path = candidate['provenance']['proof_path']
            existing_proof = sb.get_json(proof_path)
            if existing_proof is not None and existing_proof != entry['proof']:
                raise RuntimeError('Existing source proof differs; refusing to overwrite immutable evidence')
            if existing_proof is None:
                sb.put_json(proof_path, entry['proof'], cache='max-age=31536000,immutable')
                _wait_equal(lambda: sb.get_json(proof_path), entry['proof'], 'Source proof')
        cleanup = jobs.persist_normalized_facts(sb, ticker, entry['candidate_facts'], delete_batch_size=delete_batch_size)
        expected = fact_map(projected_facts(entry['before_facts'], entry['candidate_facts']))
        if fact_map(company_facts(sb, ticker)) != expected:
            raise RuntimeError(f'{ticker}: repaired relational facts read-back mismatch')
        sb.put_json(f'companies/{ticker}.json', candidate, cache='no-store')
        _wait_equal(lambda: sb.get_json(f'companies/{ticker}.json'), candidate, f'{ticker} company')
        return cleanup
    def completed(entry, cleanup):
        ticker = entry['ticker']
        fund[ticker] = entry['candidate_fund']
        receipt['repaired'].append(ticker)
        receipt['fact_cleanup'][ticker] = cleanup
        atomic_json(receipt_path, receipt)
    stop = threading.Event()
    previous_abort = getattr(sb, '_repair_abort', None)
    sb._repair_abort = stop
    try:
        if workers == 1:
            for entry in entries:
                completed(entry, one(entry))
        else:
            run_bounded(entries, one, completed, workers=workers, stop=stop)
        if defer_aggregate:
            # _load_plan proves every cleanup entry leaves its metadata unchanged.
            # Check the entire live snapshot, including all unselected issuers,
            # before claiming cleanup success without aggregate publication.
            if fund != original_fund or sb.get_json('meta/fundamentals.json') != original_fund:
                raise RuntimeError('Fundamentals changed during deferred cleanup; aggregate publication remains pending')
            receipt['status'] = 'cleanup_complete'
            receipt['unchanged_fundamentals_sha256'] = digest(encoded(original_fund))
        else:
            receipt['status'] = 'publishing_aggregate'
            atomic_json(receipt_path, receipt)
            _publish_aggregate(sb, fund)
            receipt['status'] = 'published'
            receipt['aggregate_status'] = 'published'
        receipt['completed_at'] = now_iso()
        atomic_json(receipt_path, receipt)
    except BaseException as error:
        # run_bounded has already joined all workers before control reaches here.
        stop.set()
        receipt['status'] = 'failed'
        receipt['error'] = str(error)[:500]
        try:
            atomic_json(receipt_path, receipt)
        except OSError:
            pass  # Last atomic receipt still contains the verified backup pointer.
        raise
    finally:
        sb._repair_abort = previous_abort
    return receipt


def _exact_delete(sb, row):
    query = '&'.join(f'{key}=eq.{quote(str(row[key]), safe="")}' for key in jobs.FACT_KEY)
    query += '&derived=is.' + ('true' if row['derived'] else 'false')
    query += ''.join(f'&{key}=eq.{quote(str(row[key]), safe="")}' for key in ('accession', 'concept', 'value'))
    sb._aggregate_lease.check()
    sb.delete('facts', query)


@serialized_aggregates
def rollback(sb, backup_path):
    """Restore only unchanged repair inputs, preserving unrelated company updates.

    Index values are regenerated with the current safety guards; rollback does
    not republish the old fabricated financial aggregates.
    """
    if isinstance(sb, Supabase) and not isinstance(sb, RepairSupabase):
        raise TypeError('Use RepairSupabase for bounded, lease-checked production writes')
    match = re.fullmatch(r'raw/(repairs/financial-normalization/([a-f0-9]{64})\.json\.gz)', backup_path)
    if not match:
        raise ValueError('Expected a private financial-normalization backup path')
    raw = gzip.decompress(sb.get_object('raw', match[1]) or b'')
    if digest(raw) != match[2]:
        raise ValueError('Private backup hash mismatch')
    backup = json.loads(raw)
    if backup.get('version') != 1:
        raise ValueError('Unsupported backup version')
    fund = sb.get_json('meta/fundamentals.json') or {}
    current_rows = {}
    for entry in backup['companies']:
        ticker = entry['ticker']
        before, after = fact_map(entry['before_facts']), fact_map(projected_facts(entry['before_facts'], entry['candidate_facts']))
        current = current_rows[ticker] = fact_map(company_facts(sb, ticker))
        # Permit an interrupted apply (some rows still old); reject later edits.
        if (any(current.get(key) not in (before.get(key), after.get(key)) for key in before.keys() | after.keys() | current.keys())
                or fund.get(ticker) not in (entry['before_fund'], entry['candidate_fund'])
                or sb.get_json(f'companies/{ticker}.json') not in (entry['before_bundle'], entry['candidate_bundle'])):
            raise ValueError(f'{ticker}: repair inputs changed after publication; automatic rollback refused')
    for entry in backup['companies']:
        ticker = entry['ticker']
        before = fact_map(entry['before_facts'])
        for key, row in current_rows[ticker].items():
            if key not in before:
                _exact_delete(sb, row)
        sb.upsert('facts', entry['before_facts'], 'ticker,statement,label,kind,period_end', batch=1000)
        if fact_map(company_facts(sb, ticker)) != before:
            raise RuntimeError(f'{ticker}: rollback fact verification failed')
        sb.put_json(f'companies/{ticker}.json', entry['before_bundle'], cache='no-store')
        _wait_equal(lambda: sb.get_json(f'companies/{ticker}.json'), entry['before_bundle'], f'{ticker} rollback')
        fund[ticker] = entry['before_fund']
    _publish_aggregate(sb, fund)
    return {'status': 'rolled_back', 'tickers': [e['ticker'] for e in backup['companies']], 'backup': backup_path}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('inventory', help='Read-only active-company normalization and unsupported-fact inventory')
    planning = commands.add_parser('plan', help='Read-only remote planning; source-proved candidates stay local')
    planning.add_argument('--tickers', required=True, help='Comma-separated explicit tickers; maximum 25')
    planning.add_argument('--output', required=True, type=Path)
    planning.add_argument('--as-of')
    planning.add_argument('--workers', type=int, default=4, help='Read-only planning concurrency (1–8)')
    cleaning = commands.add_parser('plan-cleanup', help='Plan exact unsupported derived-row removal without full renormalization')
    cleaning.add_argument('--tickers', required=True)
    cleaning.add_argument('--output', required=True, type=Path)
    cleaning.add_argument('--clean-bundles', action='store_true', help='Also remove explicit unsupported quarter points without upgrading pipeline version')
    cleaning.add_argument('--workers', type=int, default=4)
    applying = commands.add_parser('apply', help='Apply a reviewed local plan with verified private backups')
    applying.add_argument('--plan', required=True, type=Path)
    applying.add_argument('--workers', type=int, choices=range(1, 9), default=1,
                          help='Opt-in bounded issuer concurrency under one aggregate lease (default 1)')
    applying.add_argument('--delete-batch-size', type=int, choices=range(1, 21), default=1,
                          help='Opt-in exact-filter deletion batches, additionally capped at 6000 URL bytes (default 1)')
    applying.add_argument('--defer-aggregate', action='store_true',
                          help='Cleanup-only: verify unchanged fundamentals and defer aggregate publication')
    restoring = commands.add_parser('rollback', help='Guarded restoration from a private repair backup')
    restoring.add_argument('--backup', required=True)
    args = parser.parse_args(argv)
    sb = RepairSupabase()
    if args.command == 'inventory':
        result = inventory(sb)
    elif args.command in ('plan', 'plan-cleanup'):
        tickers = [t.strip().upper() for t in args.tickers.split(',') if t.strip()]
        result = (plan_repair(sb, tickers, args.output, args.as_of, args.workers) if args.command == 'plan'
                  else plan_cleanup(sb, tickers, args.output, args.clean_bundles, args.workers))
        result = {'plan': str(args.output / 'plan.json'), 'mode': result['mode'],
                  'companies': [{k: v for k, v in e.items() if k not in ('artifacts', 'review')} |
                                {'review': {k: v for k, v in e.get('review', {}).items() if k != 'changes'}} for e in result['companies']]}
    elif args.command == 'apply':
        result = apply_plan(sb, args.plan, workers=args.workers, delete_batch_size=args.delete_batch_size,
                            defer_aggregate=args.defer_aggregate)
    else:
        result = rollback(sb, args.backup)
    print(json.dumps(result, sort_keys=True, allow_nan=False))


if __name__ == '__main__':
    main()
