"""Stage and apply an explicit, source-checked workbook text refresh.

No discovery, original replacement, KPI publication, or unrelated queue draining.
Stage performs remote reads only. Apply requires the reviewed plan and explicit IDs.
"""
import argparse
import copy
import datetime as dt
import gzip
import json
from pathlib import Path
import re
import time
from types import SimpleNamespace
from urllib.parse import quote

from . import ir
from .backfill import atomic_write, digest, encoded
from .common import AggregateLease, Supabase, now_iso, request
from .kpitools import _cell_equal, _number, parse_ref


GUARDS = ('id', 'ticker', 'url', 'ext', 'bytes', 'sha256', 'storage_path', 'text_path', 'extracted_at', 'pages')
REQUIRED = ('id', 'ticker', 'url', 'ext', 'bytes', 'sha256', 'storage_path', 'text_path')


def _snapshot(row):
    out = {key: row.get(key) for key in GUARDS}
    if out['extracted_at']:
        out['extracted_at'] = dt.datetime.fromisoformat(str(out['extracted_at']).replace('Z', '+00:00')).astimezone(dt.timezone.utc).isoformat()
    return out


def _ids(values):
    values = list(values)
    if not values or len(values) != len(set(values)) or any(not re.fullmatch(r'[0-9a-f]{16}', value) for value in values):
        raise ValueError('Explicit unique document IDs are required')
    return values


def _rows(sb, ids):
    out = {}
    for start in range(0, len(ids), 80):
        rows = sb.select('ir_documents', 'select=*&id=in.(' + ','.join(ids[start:start + 80]) + ')&order=id')
        for row in rows:
            if row['id'] in out or row['id'] not in ids:
                raise ValueError('Unexpected or duplicate document in readback')
            out[row['id']] = row
    if set(out) != set(ids):
        raise ValueError('A listed document is missing')
    return out


def _text(raw):
    if raw[:2] == b'\x1f\x8b':
        raw = gzip.decompress(raw)
    value = json.loads(raw)
    if not isinstance(value, dict) or value.get('kind') != 'sheets' or not isinstance(value.get('sheets'), dict):
        raise ValueError('Workbook text must have sheet/cell coordinates')
    return value


def _cells(text):
    return {(sheet, r + 1, c + 1): value for sheet, rows in text['sheets'].items()
            for r, row in enumerate(rows) for c, value in enumerate(row)
            if value is not None and str(value).strip()}


def _same_cell(left, right):
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    a, b = _number(left, allow_text=True), _number(right, allow_text=True)
    return a == b if a is not None and b is not None else left == right


def _containment(old, new):
    previous, current = _cells(old), _cells(new)
    missing = [list(key) for key in previous if key not in current]
    changed = [{'cell': list(key), 'old': value, 'new': current[key]} for key, value in previous.items()
               if key in current and not _same_cell(value, current[key])]
    return {'old_populated_cells': len(previous), 'new_populated_cells': len(current),
            'missing_cells': missing, 'changed_cells': changed}


def _complete_capture(text):
    quality = text.get('extraction') or {}
    if not quality.get('nonempty') or quality.get('status') not in ('verified', 'limited'):
        return False
    if any(not str(limit).startswith('cell errors:') for limit in quality.get('limits', [])):
        return False
    if any(':source cell error ' not in error and ':formula cache missing' not in error for error in quality.get('errors', [])):
        return False
    for name, meta in text.get('sheet_metadata', {}).items():
        rows = text['sheets'].get(name)
        if rows is None or len(rows) < (meta.get('data_rows', meta.get('rows')) or 0):
            return False
        if max(map(len, rows), default=0) < (meta.get('data_columns', meta.get('columns')) or 0):
            return False
    return True


def _local(root, relative):
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise ValueError('Plan payload escapes its directory')
    return path


def _sealed(path):
    path = Path(path).resolve()
    body = path.read_bytes()
    if digest(body) != path.with_suffix('.sha256').read_text().strip():
        raise ValueError('Plan or evidence hash mismatch')
    return body, json.loads(body)


def _precision_proof(entry, source_raw, relative):
    containment = entry.get('containment') or {}
    if (not entry.get('complete_coordinate_capture') or containment.get('missing_cells')
            or not containment.get('changed_cells')):
        raise ValueError('Precision review requires complete coordinates with only value changes')
    source = json.loads(source_raw)
    document = source.get('document') or {}
    if any(document.get(key) != entry['expected'][key] for key in ('id','ticker','sha256','bytes','storage_path')):
        raise ValueError('Independent source evidence has a different original identity or hash')
    cells = {}
    for sheet in source['sheets']:
        for cell in sheet['cells']:
            rc = parse_ref(cell['cell'])
            if rc is None:
                raise ValueError('Invalid independent source coordinate')
            key = (sheet['name'], rc[0]+1, rc[1]+1)
            if key in cells:
                raise ValueError('Duplicate independent source coordinate')
            cells[key] = cell
    changes = []
    for change in containment['changed_cells']:
        cell = cells.get(tuple(change['cell']), {})
        old, new, original = (_number(change['old']), _number(change['new']), _number(cell.get('value')))
        if (old is None or new is None or original is None or new != original
                or cell.get('type') not in ('n', 2) or (cell.get('is_formula') and not cell.get('cached_value_present'))):
            raise ValueError('Changed numeric value does not exactly match the independently parsed source cell')
        if (old > 0) - (old < 0) != (new > 0) - (new < 0):
            raise ValueError('Precision restoration cannot change sign or zero status')
        if old == _number(round(change['new'], 6)):
            reason = 'exact_six_decimal_rounding'
        elif _cell_equal(old, new):
            reason = 'within_four_binary_ulps'
        else:
            raise ValueError('Change exceeds the explicitly permitted source-precision restoration')
        changes.append({**change, 'source':cell['value'], 'reason':reason})
    return {'ticker':entry['ticker'], 'original_sha256':entry['expected']['sha256'],
            'old_text_sha256':entry['old_text_sha256'], 'new_text_sha256':entry['new_text_sha256'],
            'source_cells_sha256':digest(source_raw), 'source_cells_snapshot':relative, 'changes':changes}


def prepare_precision_evidence(plan_path, source_cells, directory):
    """Seal enumerated, independently evidenced text-only precision restorations.

    This local preparation does not make held documents publishable by default.
    Apply requires the explicit --precision-evidence file after its review.
    """
    raw, plan = _sealed(plan_path)
    root = Path(directory).resolve()
    path = root/'approval.json'
    if path.exists():
        raise ValueError('A sealed precision approval already exists; use a new directory')
    approval = {'version':1, 'scope':'workbook_text_precision_only', 'plan_sha256':digest(raw), 'documents':{}, 'held':[]}
    for entry in plan['documents']:
        if not (entry.get('containment') or {}).get('changed_cells'):
            continue
        try:
            relative = f"sources/{entry['ticker']}/{entry['id']}.json"
            body = (Path(source_cells)/entry['ticker']/(entry['id']+'.json')).read_bytes()
            proof = _precision_proof(entry, body, relative)
            atomic_write(root/relative, body)
            approval['documents'][entry['id']] = proof
        except (ValueError, KeyError, TypeError, OSError) as error:
            approval['held'].append({'id':entry['id'], 'reason':str(error)[:800]})
    body = encoded(approval)
    atomic_write(path, body); atomic_write(path.with_suffix('.sha256'), digest(body).encode())
    return path


def stage_plan(sb, expected_documents, directory, originals=None, log=None):
    """Read-only remote staging; expected metadata and IDs must be supplied explicitly."""
    root = Path(directory).resolve()
    if (root / 'plan.json').exists():
        raise ValueError('A reviewed plan already exists; use a new staging directory')
    ids = _ids(row['id'] for row in expected_documents)
    expected = {row['id']: row for row in expected_documents}
    current = _rows(sb, ids)
    plan = {'version': 1, 'created_at': now_iso(), 'documents': [], 'companies': {}}
    for did in ids:
        row, supplied = current[did], expected[did]
        if any(key not in supplied for key in REQUIRED):
            raise ValueError('Expected source metadata is incomplete')
        if not re.fullmatch(r'[A-Z0-9][A-Z0-9.-]{0,14}', row['ticker']) or row['ext'] not in ('.xls', '.xlsx', '.xlsm', '.csv'):
            raise ValueError('Unsupported workbook identity or format')
        wanted, observed = _snapshot(supplied), _snapshot(row)
        if any(wanted[key] != observed[key] for key in GUARDS if key in supplied):
            raise ValueError(f'Document precondition changed for {did}')
        ticker = row['ticker']
        if ticker not in plan['companies']:
            checkpoint = ir._stored_object(sb, 'raw', f'ir/checkpoints/{ticker}.json')
            manifest = ir._stored_object(sb, 'public-data', f'ir/{ticker}.json')
            checked = ir._read_checkpoint(sb, ticker)
            if checkpoint is not None and json.loads(checkpoint) != checked:
                raise ValueError('Checkpoint changed during staging')
            company = {}
            for kind, body in (('checkpoint', checkpoint), ('manifest', manifest)):
                relative = f'before/{ticker}/{kind}.json' if body is not None else None
                if body is not None:
                    atomic_write(root / relative, body)
                company[kind + '_snapshot'] = relative
                company[kind + '_sha256'] = digest(body) if body is not None else None
            plan['companies'][ticker] = company
        entry = {'id': did, 'ticker': ticker, 'expected': observed, 'status': 'held'}
        try:
            source = (Path(originals) / ticker / (did + row['ext'])).read_bytes() if originals is not None else ir._stored_object(sb, 'ir', row['storage_path'])
            if source is None or digest(source) != row['sha256'] or len(source) != row['bytes']:
                raise ValueError('Original byte/hash verification failed')
            old_raw = ir._stored_object(sb, 'ir', row['text_path']) if row.get('text_path') else None
            if row.get('text_path') and old_raw is None:
                raise ValueError('Existing text object is missing')
            old = _text(old_raw) if old_raw is not None else {'kind': 'sheets', 'sheets': {}}
            result = ir.extract_text(source, row['ext'], deadline=time.monotonic() + 60)
            # Match the existing IR JSON contract for Excel dates/times while
            # keeping nonfinite numeric values a hard serialization failure.
            body = json.dumps(result, sort_keys=True, separators=(',', ':'), default=str, allow_nan=False).encode()
            # Compare the exact JSON representation that will be published.
            result = _text(body)
            stamp = now_iso()
            entry.update(quality={**result['extraction'], 'attempted_at': stamp}, extracted_at=stamp,
                         old_text_sha256=digest(old_raw) if old_raw is not None else None,
                         new_text_sha256=digest(body), complete_coordinate_capture=_complete_capture(result),
                         containment=_containment(old, result), pages=len(result['sheets']))
            relative = f'text/{ticker}/{did}.json'
            atomic_write(root / relative, body)
            entry.update(payload=relative, new_text_path=f"{ticker}/text/{did}-v2-{digest(body)}.json")
            if old_raw is not None:
                atomic_write(root / f'before/{ticker}/{did}-text.json', old_raw)
            if not entry['complete_coordinate_capture']:
                entry['reason'] = 'Incomplete coordinate capture or extraction limit'
            elif entry['containment']['missing_cells'] or entry['containment']['changed_cells']:
                entry['reason'] = 'Existing populated-cell containment failed'
            else:
                entry['status'] = 'ready'
        except Exception as error:
            entry['reason'] = f'{type(error).__name__}: {error}'[:800]
        plan['documents'].append(entry)
        if log:
            log({'id': did, 'ticker': ticker, 'status': entry['status'], 'reason': entry.get('reason')})
    plan['summary'] = {'selected': len(ids), 'ready': sum(row['status'] == 'ready' for row in plan['documents']),
                       'held': sum(row['status'] == 'held' for row in plan['documents'])}
    path = root / 'plan.json'
    atomic_write(path, encoded(plan))
    atomic_write(root / 'plan.sha256', digest(path.read_bytes()).encode())
    return path


def _patch_document(sb, previous, changes, lease):
    # Conditional PATCH includes every guarded original/text-pointer field, with
    # explicit null predicates; a changed pointer cannot be overwritten after a read.
    conditions = []
    for key in GUARDS:
        value = previous.get(key)
        conditions.append(key + '=' + ('is.null' if value is None else 'eq.' + quote(str(value), safe='')))
    lease.check()
    response = request(sb.url + '/rest/v1/ir_documents?' + '&'.join(conditions), method='PATCH', data=changes,
                       headers=sb._h({'Prefer': 'return=representation', 'Content-Type': 'application/json'}), timeout=30, retries=1)
    rows = json.loads(response)
    if not isinstance(rows, list) or len(rows) != 1 or rows[0].get('id') != previous['id']:
        raise RuntimeError('Workbook conditional update failed; pointer changed or no exact row matched')


def _put_immutable(sb, bucket, path, body, lease, cache='no-store'):
    stored = ir._stored_object(sb, bucket, path)
    if stored is not None and digest(stored) != digest(body):
        raise ValueError('Immutable repair object already contains different bytes')
    if stored is None:
        lease.check()
        sb.put_object(bucket, path, body, 'application/json', cache)
    readback = ir._stored_object(sb, bucket, path)
    if readback is None or digest(readback) != digest(body):
        raise RuntimeError('Immutable repair object hash readback failed')


def _after_state(ticker, before, chosen):
    """Deterministic after-state permits an exact interrupted-write retry."""
    state = ir._read_checkpoint(SimpleNamespace(get_object=lambda *_: before['checkpoint']), ticker)
    manifest = json.loads(before['manifest']) if before['manifest'] is not None else None
    if not isinstance(manifest, dict) or manifest.get('ticker') != ticker or not isinstance(manifest.get('documents'), list):
        raise ValueError('Manifest precondition is missing or invalid; reconcile it separately')
    manifest_rows = {row['id']: row for row in manifest['documents']}
    if len(manifest_rows) != len(manifest['documents']) or any(entry['id'] not in manifest_rows for entry in chosen):
        raise ValueError('Manifest membership precondition failed')
    for entry in chosen:
        did = entry['id']
        state['outcomes'][did] = copy.deepcopy(entry['quality'])
        task = state['queues']['extractions'].setdefault(did, {'id':did,'attempts':0})
        task['workbook_version'] = ir.WORKBOOK_EXTRACTION_VERSION
        if entry['quality']['status'] == 'verified':
            task.update(status='done', completed_at=entry['extracted_at'])
            task.pop('error', None)
        else:
            attempts = task.get('attempts', 0) + 1
            task.update(status='blocked' if attempts >= ir.MAX_ATTEMPTS else 'retry', attempts=attempts,
                        attempted_at=entry['extracted_at'], error='Source workbook errors or missing formula caches remain')
        manifest_rows[did].update(text_path=entry['new_text_path'], extracted_at=entry['extracted_at'],
                                  pages=entry['pages'], extraction=copy.deepcopy(entry['quality']))
    # The coverage calculator needs an outcome on every record. Supply a separate
    # copy: an unrelated manifest document must remain exactly as it was.
    coverage_rows = [{**row, 'extraction': row.get('extraction') or state['outcomes'].get(
        row['id'], {'status':'unverified','limits':[],'errors':[]})} for row in manifest['documents']]
    stamp = max(entry['extracted_at'] for entry in chosen)
    coverage = ir._coverage(state, coverage_rows)
    coverage['updated_at'] = stamp
    state.update(coverage=coverage, updated_at=stamp)
    manifest.update(generated=stamp, coverage=coverage)
    result = {'checkpoint':encoded(state), 'manifest':encoded(manifest)}
    if len(result['checkpoint']) > ir.CHECKPOINT_BYTES:
        raise ValueError('IR checkpoint size limit; refusing replacement')
    return result


def apply_plan(sb, plan_path, document_ids, precision_evidence=None):
    """Apply explicit IDs, accepting only exact before/after states on a retry.

    An immutable intent and original snapshots are uploaded and read back before
    the first pointer update. Thus retries do not need to discard the original
    before-state or infer whether an unrelated writer's change belongs to this run.
    """
    plan_path = Path(plan_path).resolve(); root = plan_path.parent
    raw, plan = _sealed(plan_path)
    if plan.get('version') != 1:
        raise ValueError('Unsupported workbook repair plan')
    ids = _ids(document_ids)
    by_id = {entry['id']: entry for entry in plan['documents']}
    if len(by_id) != len(plan['documents']):
        raise ValueError('Duplicate document IDs in plan')
    if any(did not in by_id for did in ids):
        raise ValueError('Requested document is not listed in this plan')
    precision, precision_sources, approval_raw = {}, {}, None
    if precision_evidence is not None:
        approval_raw, approval = _sealed(precision_evidence)
        if (approval.get('version') != 1 or approval.get('scope') != 'workbook_text_precision_only'
                or approval.get('plan_sha256') != digest(raw)):
            raise ValueError('Precision evidence is not bound to this workbook plan')
        evidence_root = Path(precision_evidence).resolve().parent
        for did in ids:
            proof = approval.get('documents', {}).get(did)
            if proof is None:
                continue
            body = _local(evidence_root, proof['source_cells_snapshot']).read_bytes()
            if digest(body) != proof['source_cells_sha256']:
                raise ValueError('Independent source evidence hash mismatch')
            if _precision_proof(by_id[did], body, proof['source_cells_snapshot']) != proof:
                raise ValueError('Precision evidence differs from exact changed cells and source values')
            precision[did], precision_sources[did] = proof, body
    if any(by_id[did].get('status') != 'ready' and did not in precision for did in ids):
        raise ValueError('Every selected document must be ready; held entries cannot be applied')
    payloads = {}
    for did in ids:
        entry = by_id[did]
        body = _local(root, entry['payload']).read_bytes()
        if digest(body) != entry['new_text_sha256']:
            raise ValueError('Staged text payload hash mismatch')
        text = _text(body)
        if not _complete_capture(text) or text.get('extraction') != {k:v for k,v in entry['quality'].items() if k != 'attempted_at'}:
            raise ValueError('Staged extraction proof differs from the payload')
        payloads[did] = body
    receipts = []
    prefix = f'repairs/workbooks/{digest(raw)}/'
    for ticker in sorted({by_id[did]['ticker'] for did in ids}):
        chosen = sorted((by_id[did] for did in ids if by_id[did]['ticker'] == ticker), key=lambda entry: entry['id'])
        receipt = {'ticker': ticker, 'ids': [entry['id'] for entry in chosen], 'at': now_iso(), 'documents': [], 'status': 'started'}
        receipt_path = root / f'apply-receipts/{ticker}.json'
        with AggregateLease(sb, f'ir-company:{ticker}') as lease:
            saved = plan['companies'][ticker]
            before = {}
            for kind, bucket, path in (('checkpoint', 'raw', f'ir/checkpoints/{ticker}.json'), ('manifest', 'public-data', f'ir/{ticker}.json')):
                snapshot = _local(root, saved[kind + '_snapshot']).read_bytes() if saved[kind + '_snapshot'] else None
                if (digest(snapshot) if snapshot is not None else None) != saved[kind + '_sha256']:
                    raise ValueError('Local source snapshot hash mismatch')
                before[kind] = snapshot
            after = _after_state(ticker, before, chosen)
            intent = encoded({'plan_sha256':digest(raw), 'ticker':ticker, 'ids':receipt['ids'],
                              'precision_sha256':digest(approval_raw) if any(entry['id'] in precision for entry in chosen) else None,
                              'before':{key: digest(body) if body is not None else None for key,body in before.items()},
                              'after':{key:digest(body) for key,body in after.items()}})
            intent_path = prefix + f'intents/{ticker}/{digest(encoded(receipt["ids"]))}.json'
            recorded_intent = ir._stored_object(sb, 'raw', intent_path)
            if recorded_intent is not None and recorded_intent != intent:
                raise ValueError('Immutable repair intent differs from this plan')
            for kind, bucket, path in (('checkpoint','raw',f'ir/checkpoints/{ticker}.json'), ('manifest','public-data',f'ir/{ticker}.json')):
                remote = ir._stored_object(sb, bucket, path)
                if remote != before[kind] and not (recorded_intent == intent and remote == after[kind]):
                    raise ValueError(f'{kind} precondition changed; unrelated state cannot be replaced')
            current = _rows(sb, [entry['id'] for entry in chosen])
            for entry in chosen:
                did = entry['id']; row = current[did]
                changes = {'text_path':entry['new_text_path'], 'extracted_at':entry['extracted_at'], 'pages':entry['pages']}
                advanced = _snapshot({**entry['expected'], **changes})
                if _snapshot(row) != entry['expected'] and not (recorded_intent == intent and _snapshot(row) == advanced):
                    raise ValueError(f'Document precondition changed for {did}')
                if entry['new_text_path'] != f"{ticker}/text/{did}-v2-{digest(payloads[did])}.json":
                    raise ValueError('Immutable text path does not match its content hash')
                original = ir._stored_object(sb, 'ir', entry['expected']['storage_path'])
                if original is None or digest(original) != entry['expected']['sha256'] or len(original) != entry['expected']['bytes']:
                    raise ValueError('Original hash precondition failed')
                old_path = entry['expected']['text_path']
                old_text = ir._stored_object(sb, 'ir', old_path) if old_path else None
                if (digest(old_text) if old_text is not None else None) != entry['old_text_sha256']:
                    raise ValueError('Existing text hash precondition failed')
                previous = _text(old_text) if old_text is not None else {'kind':'sheets','sheets':{}}
                containment = _containment(previous, _text(payloads[did]))
                if (containment != entry['containment'] or containment['missing_cells']
                        or (containment['changed_cells'] and did not in precision)):
                    raise ValueError('Existing populated-cell containment precondition failed')
            try:
                # All preconditions have passed. Retain immutable provenance before
                # the first mutable pointer/checkpoint/manifest publication.
                _put_immutable(sb, 'raw', prefix+'plan.json', raw, lease)
                for kind, body in before.items():
                    if body is not None:
                        _put_immutable(sb, 'raw', prefix+f'before/{ticker}/{kind}.json', body, lease)
                if any(entry['id'] in precision for entry in chosen):
                    evidence_prefix = prefix + f'precision/{digest(approval_raw)}/'
                    _put_immutable(sb, 'raw', evidence_prefix+'approval.json', approval_raw, lease)
                    for entry in chosen:
                        did = entry['id']
                        if did in precision:
                            _put_immutable(sb, 'raw', evidence_prefix+precision[did]['source_cells_snapshot'], precision_sources[did], lease)
                _put_immutable(sb, 'raw', intent_path, intent, lease)
                for entry in chosen:
                    did = entry['id']; body = payloads[did]; path = entry['new_text_path']
                    _put_immutable(sb, 'ir', path, body, lease, 'public, max-age=31536000, immutable')
                    changes = {'text_path': path, 'extracted_at': entry['extracted_at'], 'pages': entry['pages']}
                    if _snapshot(current[did]) != _snapshot({**entry['expected'], **changes}):
                        _patch_document(sb, current[did], changes, lease)
                    readback = _rows(sb, [did])[did]
                    if _snapshot(readback) != _snapshot({**entry['expected'], **changes}):
                        raise RuntimeError('Document pointer readback failed')
                    receipt['documents'].append({'id':did,'text_path':path,'sha256':digest(body),'status':'pointer_verified'})
                    atomic_write(receipt_path, encoded(receipt))
                for kind, bucket, path, cache in (('checkpoint','raw',f'ir/checkpoints/{ticker}.json','no-store'),
                                                  ('manifest','public-data',f'ir/{ticker}.json','max-age=600')):
                    remote = ir._stored_object(sb, bucket, path)
                    if remote != before[kind] and remote != after[kind]:
                        raise ValueError(f'{kind} precondition changed during publication')
                    if remote != after[kind]:
                        lease.check()
                        sb.put_object(bucket, path, after[kind], 'application/json', cache)
                    if ir._stored_object(sb, bucket, path) != after[kind]:
                        raise RuntimeError(f'{kind} readback failed')
                    receipt[kind+'_verified'] = True
                    atomic_write(receipt_path, encoded(receipt))
                receipt.update(status='verified', backup_prefix=prefix, intent_path=intent_path)
            except Exception as error:
                receipt.update(status='partial', error=str(error)[:1000])
                atomic_write(receipt_path, encoded(receipt))
                raise
            atomic_write(receipt_path, encoded(receipt))
            receipts.append(receipt)
    return receipts


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    stage = sub.add_parser('stage'); stage.add_argument('--snapshot', type=Path, required=True)
    stage.add_argument('--root', type=Path, required=True); stage.add_argument('--originals', type=Path)
    stage.add_argument('--ids', nargs='+', required=True)
    apply = sub.add_parser('apply'); apply.add_argument('--plan', type=Path, required=True); apply.add_argument('--ids', nargs='+', required=True)
    apply.add_argument('--precision-evidence', type=Path)
    precision = sub.add_parser('prepare-precision'); precision.add_argument('--plan', type=Path, required=True)
    precision.add_argument('--source-cells', type=Path, required=True); precision.add_argument('--root', type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == 'prepare-precision':
        print(prepare_precision_evidence(args.plan, args.source_cells, args.root))
        return
    sb = Supabase()
    if args.command == 'stage':
        rows = json.loads(args.snapshot.read_text()); wanted = _ids(args.ids)
        by_id = {row['id']:row for row in rows}
        path = stage_plan(sb, [by_id[did] for did in wanted], args.root, args.originals, log=lambda row: print(json.dumps(row), flush=True))
        print(json.dumps({'plan':str(path),'summary':json.loads(path.read_text())['summary']}))
    else:
        print(json.dumps(apply_plan(sb, args.plan, args.ids, precision_evidence=args.precision_evidence)))


if __name__ == '__main__':
    main()
