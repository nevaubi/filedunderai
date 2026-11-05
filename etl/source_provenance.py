"""Validate normalized SEC statements against an immutable Company Facts snapshot.

This is a publication gate, not an extractor. It never changes, omits, or repairs
an input point. Source numeric values in the JSON-serializable proof are decimal
strings; the archived bytes and escaped JSON pointers retain the original JSON.
"""
import datetime as dt
from decimal import Decimal, InvalidOperation, localcontext
import hashlib
import json
import math
import re

from . import sec


class ProvenanceError(ValueError):
    """A statement cannot be fully substantiated by the supplied source bytes."""


def _fail(path, reason):
    raise ProvenanceError(f"{path}: {reason}")


def _pointer(*parts):
    return '/' + '/'.join(str(part).replace('~', '~0').replace('/', '~1') for part in parts)


def _number(value, path):
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        _fail(path, 'expected a finite numeric value')
    try:
        number = Decimal(str(value))
    except InvalidOperation as error:
        raise ProvenanceError(f'{path}: invalid numeric value') from error
    if not number.is_finite():
        _fail(path, 'non-finite numeric value')
    return number


def _subtract_exact(left, right):
    # Decimal's process-wide precision defaults to 28 digits. Source evidence
    # must retain all digits, including when subtracting large integer values.
    precision = max(left.adjusted(), right.adjusted()) - min(
        left.as_tuple().exponent, right.as_tuple().exponent) + 2
    with localcontext() as context:
        context.prec = max(precision, 1)
        return left - right


def _date(value, path):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        _fail(path, 'expected an ISO calendar date')
    try:
        return dt.date.fromisoformat(value)
    except ValueError as error:
        raise ProvenanceError(f'{path}: invalid calendar date') from error


def _cik(value, path):
    if isinstance(value, bool) or not isinstance(value, (int, str)) or not re.fullmatch(r'\d{1,10}', str(value)):
        _fail(path, 'invalid SEC issuer CIK')
    result = int(value)
    if result <= 0:
        _fail(path, 'invalid SEC issuer CIK')
    return result


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _fail('snapshot', f'duplicate JSON key {key!r}')
        result[key] = value
    return result


def _invalid_constant(value):
    _fail('snapshot', f'non-finite JSON number {value}')


def _json_context(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {key: _json_context(child) for key, child in value.items()}
    if isinstance(value, list):
        return [_json_context(child) for child in value]
    return value


def validate_statement_provenance(snapshot, statements, expected_cik, snapshot_sha256, *, taxonomy=None):
    """Return a source proof for every selected and historical statement point.

    ``snapshot`` must be the exact uncompressed UTF-8 response bytes (or their
    exact text), not a reconstructed dict. ``statements`` is the real
    ``{name: sec.build_series(snapshot['facts'], name)}`` shape. The caller must
    archive these bytes and the returned proof before promoting the data.
    ``taxonomy`` can explicitly declare additional screener metrics using the
    same statement names; all value, unit, period and derivation checks remain.
    """
    cik = _cik(expected_cik, 'expected_cik')
    if isinstance(snapshot, str):
        try:
            raw = snapshot.encode('utf-8')
        except UnicodeError as error:
            raise ProvenanceError('snapshot: not valid UTF-8 text') from error
    elif isinstance(snapshot, (bytes, bytearray)):
        raw = bytes(snapshot)
    else:
        _fail('snapshot', 'exact source bytes are required; a parsed object cannot prove its byte hash')
    if not isinstance(snapshot_sha256, str) or not re.fullmatch(r'[0-9a-fA-F]{64}', snapshot_sha256):
        _fail('snapshot_sha256', 'invalid SHA-256')
    digest = hashlib.sha256(raw).hexdigest()
    if digest != snapshot_sha256.lower():
        _fail('snapshot_sha256', 'source bytes do not match the supplied SHA-256')
    try:
        source = json.loads(raw.decode('utf-8'), parse_float=Decimal,
                            parse_constant=_invalid_constant, object_pairs_hook=_unique_object)
    except (UnicodeError, json.JSONDecodeError, RecursionError, ValueError) as error:
        if isinstance(error, ProvenanceError):
            raise
        raise ProvenanceError('snapshot: invalid UTF-8 JSON') from error
    if not isinstance(source, dict) or _cik(source.get('cik'), 'snapshot.cik') != cik:
        _fail('snapshot.cik', 'SEC issuer identity mismatch')
    facts = source.get('facts')
    if not isinstance(facts, dict):
        _fail('snapshot.facts', 'missing Company Facts namespaces')
    declared_taxonomy = sec.CONCEPTS if taxonomy is None else taxonomy
    if (not isinstance(declared_taxonomy, dict) or not declared_taxonomy
            or set(declared_taxonomy) - set(sec.CONCEPTS)):
        _fail('taxonomy', 'expected supported statement names')
    if (not isinstance(statements, dict) or not statements or set(statements) - set(declared_taxonomy)):
        _fail('statements', 'expected supported statement names')

    records, points, indexes = {}, {}, {}
    counts = {'statement_points': 0, 'selected_points': 0, 'history_points': 0,
              'reported_points': 0, 'derived_points': 0, 'source_records': 0}

    def identity(record, value_key, path):
        if not isinstance(record, dict):
            _fail(path, 'expected a source observation object')
        start = record.get('start')
        start_date = _date(start, path + '/start') if start is not None else None
        end = _date(record.get('end'), path + '/end')
        filed = _date(record.get('filed'), path + '/filed')
        if start_date is not None and start_date > end:
            _fail(path, 'period start follows period end')
        accession = record.get('accn')
        if not isinstance(accession, str) or not re.fullmatch(r'\d{10}-\d{2}-\d{6}', accession):
            _fail(path, 'invalid SEC accession')
        form = record.get('form')
        if not isinstance(form, str) or not form:
            _fail(path, 'missing filing form')
        return (start, end.isoformat(), accession, filed.isoformat(), form,
                _number(record.get(value_key), path + '/' + value_key))

    def source_index(namespace, concept, unit):
        key = (namespace, concept, unit)
        if key in indexes:
            return indexes[key]
        group = facts.get(namespace)
        definition = group.get(concept) if isinstance(group, dict) else None
        units = definition.get('units') if isinstance(definition, dict) else None
        observations = units.get(unit) if isinstance(units, dict) else None
        base = _pointer('facts', namespace, concept, 'units', unit)
        if not isinstance(observations, list) or not observations:
            _fail(base, 'concept/unit source records are missing')
        index = {}
        for index_number, observation in enumerate(observations):
            location = base + '/' + str(index_number)
            match_key = identity(observation, 'val', location)
            index.setdefault(match_key, []).append((location, observation))
        indexes[key] = index
        return index

    def keep_source(location, record, namespace, concept, unit):
        if location not in records:
            retained = _json_context(record)
            retained['val'] = str(_number(record['val'], location + '/val'))
            retained.update(namespace=namespace, concept=concept, unit=unit, value_encoding='decimal')
            records[location] = retained

    def metadata(point, expected_available, accessions, kind, path):
        if 'available_at' in point and point['available_at'] != expected_available:
            _fail(path, 'availability does not match the source filing dates')
        if 'accessions' in point:
            supplied = point['accessions']
            if (not isinstance(supplied, list) or any(not isinstance(a, str) for a in supplied)
                    or len(supplied) != len(set(supplied)) or set(supplied) != accessions):
                _fail(path, 'accession lineage does not match the verified inputs')
        if 'validation_status' in point and point['validation_status'] != kind:
            _fail(path, 'reported/derived status contradicts the source proof')

    def validate_point(point, row, statement, namespace, allowed, path, active, bucket=None):
        if not isinstance(point, dict):
            _fail(path, 'expected a normalized statement point')
        if id(point) in active or len(active) >= 32:
            _fail(path, 'cyclic or excessively deep source derivation')
        active = active | {id(point)}
        unit, label = row['unit'], row['label']
        concept = point.get('c')
        if not isinstance(concept, str) or concept not in allowed:
            _fail(path, 'point concept is not an allowed alias for this statement metric')
        if point.get('namespace', namespace) != namespace or point.get('unit', unit) != unit:
            _fail(path, 'point namespace/unit contradicts the statement source')
        if 'concept' in point and point['concept'] != concept:
            _fail(path, 'conflicting point concept identities')
        match_key = identity(point, 'v', path)
        start, end, accession, filed, form, value = match_key
        if form not in ('10-K', '10-Q', '10-K/A', '10-Q/A', '20-F', '10-KT'):
            _fail(path, 'unsupported statement filing form')
        if filed < end:
            _fail(path, 'reported period ends after its filing date')
        instant = statement in sec.INSTANT
        if instant and start is not None:
            _fail(path, 'instant balance value has a duration context')
        if not instant and start is None:
            _fail(path, 'flow statement value lacks a duration context')
        if bucket and not instant:
            duration = (_date(end, path) - _date(start, path)).days
            # Retain 14-week fiscal quarters, but do not mislabel four-month
            # transition periods accepted by the legacy normalizer as quarters.
            # A disclosed fiscal quarter can span 16 weeks (CAVA's first quarter).
            # Sixteen inclusive weeks are 111 days apart; a four-month transition
            # such as VAL Jan-Apr 2021 spans 119 and remains outside this range.
            minimum, maximum = (350, 380) if bucket == 'annual' else (80, 111)
            if not minimum <= duration <= maximum:
                _fail(path, f'source duration is not a supported {bucket} period')
        derived = point.get('derived')
        if derived is None:
            matches = source_index(namespace, concept, unit).get(match_key, [])
            matches = [(pointer, observation) for pointer, observation in matches
                       if all(field not in point or point[field] == observation.get(field)
                              for field in ('fy', 'fp', 'frame'))]
            if instant and bucket == 'annual':
                matches = [(pointer, observation) for pointer, observation in matches
                           if observation.get('fp') == 'FY' and form.startswith(('10-K', '20-F'))]
            if not matches:
                _fail(path, 'no exact raw source record matches concept, unit, period, accession, filed date and value')
            metadata(point, filed, {accession}, 'reported', path)
            for pointer, observation in matches:
                keep_source(pointer, observation, namespace, concept, unit)
            proof = {'kind': 'reported', 'source': matches[0][0]}
            if len(matches) > 1:
                proof['equivalent_sources'] = [pointer for pointer, _ in matches[1:]]
            available, accessions = filed, {accession}
        else:
            if (not isinstance(derived, dict) or set(derived) - {'from', 'minus', 'basis'}
                    or not {'from', 'minus'}.issubset(derived)):
                _fail(path, 'expected a complete fixed subtraction derivation')
            if instant or label in sec.NON_ADDITIVE or not re.fullmatch(r'[A-Z]{3}', unit):
                _fail(path, 'instant, average, ratio or per-share values are not additive flows')
            big = validate_point(derived['from'], row, statement, namespace, allowed,
                                 path + '/derived/from', active)
            small = validate_point(derived['minus'], row, statement, namespace, allowed,
                                   path + '/derived/minus', active)
            if (big['concept'] != concept or small['concept'] != concept
                    or big['start'] != small['start'] or not big['start']
                    or small['end'] >= big['end']):
                _fail(path, 'subtraction inputs must share one concept and cumulative start with increasing ends')
            expected_start = (_date(small['end'], path) + dt.timedelta(days=1)).isoformat()
            if start != expected_start or end != big['end']:
                _fail(path, 'derived reporting period does not match the source legs')
            if (accession, filed, form) != (big['accn'], big['filed'], big['form']) or small['filed'] > big['filed']:
                _fail(path, 'derived filing identity or chronology differs from its source legs')
            calculated = _subtract_exact(big['value'], small['value'])
            proof = {'kind': 'derived', 'operation': 'subtract', 'from': big['proof'], 'minus': small['proof']}
            if value != calculated:
                # sec.build_series uses Python subtraction. Permit only its binary
                # floating-point error, never a business-level absolute/relative tolerance.
                operands = (derived['from'].get('v'), derived['minus'].get('v'))
                floating = isinstance(point.get('v'), float) and any(isinstance(v, float) for v in operands)
                try:
                    allowance = sum(Decimal(str(math.ulp(float(v)))) for v in (*operands, point['v'])) if floating else Decimal(0)
                    reproduced = operands[0] - operands[1] if floating else None
                except (OverflowError, ValueError):
                    allowance = Decimal(0)
                    reproduced = None
                if (not floating or point['v'] != reproduced
                        or _subtract_exact(value, calculated).copy_abs() > allowance):
                    _fail(path, 'derived value does not equal the verified source subtraction')
                proof['rounding'] = {'normalized': str(value), 'exact': str(calculated), 'reason': 'binary_float_subtraction'}
            value = calculated
            available = max(big['available'], small['available'])
            accessions = big['accessions'] | small['accessions']
            metadata(point, available, accessions, 'derived', path)
            expected_basis = 'same_filing_same_concept' if len(accessions) == 1 else 'sequential_filings_same_concept_no_observed_revision'
            if 'basis' in derived and derived['basis'] != expected_basis:
                _fail(path, 'derivation basis contradicts its verified filing identities')
        if row.get('as_of') is not None and available > row['as_of']:
            _fail(path, 'source was unavailable at the requested filing-date cutoff')
        if 'vintage' in point and point['vintage'] != row.get('vintage', point['vintage']):
            _fail(path, 'point vintage contradicts its statement series')
        return {'proof': proof, 'value': value, 'concept': concept, 'start': start, 'end': end,
                'accn': accession, 'filed': filed, 'form': form, 'available': available, 'accessions': accessions}

    for statement, rows in statements.items():
        if not isinstance(rows, list):
            _fail(_pointer(statement), 'expected statement rows')
        metric_taxonomy, seen_labels = dict(declared_taxonomy[statement]), set()
        for row_number, row in enumerate(rows):
            row_path = _pointer(statement, row_number)
            if not isinstance(row, dict):
                _fail(row_path, 'expected a statement row')
            label, unit = row.get('label'), row.get('unit')
            if not isinstance(label, str) or label not in metric_taxonomy or label in seen_labels:
                _fail(row_path, 'unsupported or duplicate statement metric')
            seen_labels.add(label)
            namespace = row.get('namespace', 'us-gaap')
            if namespace != 'us-gaap' or not isinstance(unit, str) or not unit:
                _fail(row_path, 'unsupported statement namespace or missing unit')
            unit_pattern = r'[A-Z]{3}/shares' if label.startswith('EPS ') else (r'shares' if label == 'Diluted shares' else r'[A-Z]{3}')
            if not re.fullmatch(unit_pattern, unit):
                _fail(row_path, 'unit dimension does not fit the statement metric')
            allowed = metric_taxonomy[label]
            if row.get('concept') not in allowed:
                _fail(row_path, 'row concept is not an allowed metric alias')
            source_index(namespace, row['concept'], unit)
            if row.get('as_of') is not None:
                _date(row['as_of'], row_path + '/as_of')
            containers = [(row, row_path, False)]
            if 'filing_history' in row:
                history = row['filing_history']
                if not isinstance(history, dict) or set(history) - {'annual', 'quarterly'}:
                    _fail(row_path + '/filing_history', 'expected annual/quarterly history arrays')
                containers.append((history, row_path + '/filing_history', True))
            for container, base_path, historical in containers:
                for bucket in ('annual', 'quarterly'):
                    observations = container.get(bucket)
                    if not isinstance(observations, list):
                        _fail(base_path + '/' + bucket, 'expected a complete observation array')
                    for point_number, point in enumerate(observations):
                        path = base_path + '/' + bucket + '/' + str(point_number)
                        checked = validate_point(point, row, statement, namespace, allowed, path, set(), bucket)
                        points[path] = checked['proof']
                        counts['statement_points'] += 1
                        counts['history_points' if historical else 'selected_points'] += 1
                        counts['derived_points' if checked['proof']['kind'] == 'derived' else 'reported_points'] += 1
    if counts['selected_points'] == 0:
        _fail('statements', 'no selected statement values can be proved')
    counts['source_records'] = len(records)
    return {'version': 1, 'status': 'verified', 'cik': cik,
            'source_url': f'https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json',
            'snapshot_sha256': digest, 'counts': counts, 'records': records, 'points': points}
