"""Explicitly retain unsupported period candidates outside published statements."""
import copy
import datetime as dt
from decimal import Decimal

from . import sec


# The screener retains this much financial history for its current growth and
# return calculations. Source conflicts inside it require a comparability decision.
CALCULATION_HISTORY_YEARS = 6.3


SOURCE_OBSERVATION_HOLDS = ({
    'id': 'fdxf_2026_may_debt_rounded_narrative', 'ticker': 'FDXF', 'cik': 2082247,
    'observation': {
        'ticker': 'FDXF', 'statement': 'balance', 'label': 'Long-term debt', 'kind': 'Q',
        'concept': 'LongTermDebt', 'unit': 'USD', 'period_start': None, 'period_end': '2026-05-31',
        'accession': '0001628280-26-068246', 'filed': '2026-10-29', 'form': '10-Q',
        'value': 4300000000, 'derived': False,
    },
    'reason': 'The rounded narrative amount must not replace the more precise reported table amount; preserve prior publication pending source review.',
    'evidence': [
        {'url': 'https://www.sec.gov/Archives/edgar/data/2082247/000162828026068246/fdxf-20260930.htm',
         'sha256': '94ebdb2cdc40153abfb32bef5e5555ca06f6884d7021040e893719244d6f74b0', 'bytes': 980584,
         'narrative': {'inline_id': 'f-506', 'context': 'c-4', 'printed': '4.3', 'scale': 9, 'decimals': -8},
         'table': {'inline_id': 'f-492', 'context': 'c-109', 'printed': '4,264', 'scale': 6, 'decimals': -6}},
        {'url': 'https://www.sec.gov/Archives/edgar/data/2082247/000162828026053359/fdxf-20260531.htm',
         'sha256': '24678beac76e786cf90113b5e062efc9799d6629032f701076c318f1ef3613f0', 'bytes': 2515813,
         'table': {'inline_id': 'f-518', 'context': 'c-4', 'printed': '4,264', 'scale': 6, 'decimals': -6}},
    ],
},)


def check_source_observation_holds(ticker, rows):
    """Reject only exact source-reviewed observations; never choose a replacement.

    CompanyFacts omits the inline precision/context evidence pinned above. No
    numeric tolerance or general assumption about later revisions is applied.
    """
    from .universe import BY_TICKER
    cik = (BY_TICKER.get(ticker) or {}).get('cik')
    holds = [hold for hold in SOURCE_OBSERVATION_HOLDS if (hold['ticker'], hold['cik']) == (ticker, cik)]
    for row in rows:
        for hold in holds:
            if all(row.get(key) == value for key, value in hold['observation'].items()):
                raise ValueError(f"Financial source observation requires review: {hold['id']}: {hold['reason']}")


def comparability_review(statements, as_of=None):
    """Retain source disagreements and identify those affecting current calculations."""
    today = as_of or dt.datetime.now(dt.timezone.utc).date().isoformat()
    from .fundamentals import current_period_end
    latest = current_period_end(statements)
    anchor = min(today, latest) if latest else today
    cutoff = (dt.date.fromisoformat(anchor) - dt.timedelta(days=int(365.25 * CALCULATION_HISTORY_YEARS))).isoformat()
    conflicts = []
    for statement, rows in statements.items():
        for row in rows:
            for bucket in ('annual', 'quarterly'):
                for point in row.get(bucket, []):
                    conflict = point.get('selection_conflict')
                    if conflict:
                        entry = {'statement': statement, 'label': row['label'], 'unit': row['unit'],
                                 'bucket': bucket, 'point': copy.deepcopy(point), 'reason': conflict['reason']}
                        for key in ('alternative', 'additional_alternatives', 'reported_observations'):
                            if key in conflict:
                                entry[key] = copy.deepcopy(conflict[key])
                        conflicts.append(entry)
    return {'calculation_anchor': anchor, 'calculation_cutoff': cutoff, 'conflicts': conflicts,
            'current_conflicts': [r for r in conflicts if r['point']['end'] >= cutoff]}


def partition_supported_periods(statements):
    """Partition two known period defects; every other error still fails the gate.

    The source remains immutable. Excluded candidates retain their complete
    context and reason in the company bundle, rather than becoming silently
    discarded values or being relabeled as an ordinary quarter.
    """
    clean, excluded = copy.deepcopy(statements), []

    def reason(point, quarterly, instant, visited):
        if not isinstance(point, dict) or id(point) in visited or len(visited) >= 32:
            return None  # The strict provenance gate reports malformed derivations.
        visited = visited | {id(point)}
        try:
            end = dt.date.fromisoformat(point["end"])
            filed = dt.date.fromisoformat(point["filed"])
            if end > filed:
                return "period_ends_after_filing"
            if quarterly and not instant and point.get("start"):
                duration = (end - dt.date.fromisoformat(point["start"])).days
                if duration > 111:
                    return "nonstandard_quarter_duration"
        except (KeyError, TypeError, ValueError):
            return None  # Do not transform other invalid source data into omissions.
        derived = point.get("derived")
        if isinstance(derived, dict):
            for key in ("from", "minus"):
                defect = reason(derived.get(key), False, instant, visited)
                if defect:
                    return defect
        return None

    for statement, rows in clean.items():
        for row in rows:
            containers = [(row, "selected")]
            if isinstance(row.get("filing_history"), dict):
                containers.append((row["filing_history"], "filing_history"))
            for container, location in containers:
                for bucket in ("annual", "quarterly"):
                    keep = []
                    for point in container.get(bucket, []):
                        defect = reason(point, bucket == "quarterly", statement == "balance", set())
                        if defect:
                            excluded.append({"statement":statement, "label":row["label"], "concept":row["concept"],
                                             "unit":row["unit"], "location":location, "bucket":bucket,
                                             "reason":defect, "point":point})
                        else:
                            keep.append(point)
                    container[bucket] = keep
    return clean, excluded


def retain_proved_filing_tables(tables, proof):
    """Reconstructed tables expose only observations in the verified source proof.

    The complete original filing remains linked. Per-cell pointers preserve
    concept identity when different columns use different accounting aliases.
    """
    index = {}
    for pointer, record in proof["records"].items():
        key = (record["accn"], record["concept"], record["unit"], record.get("start"), record["end"], Decimal(record["val"]))
        index.setdefault(key,[]).append(pointer)
    result, removed = copy.deepcopy(tables), 0
    for accession, filing in result.items():
        for statement, table in filing.get("statements",{}).items():
            kept_rows = []
            for row in table["rows"]:
                values, sources, concepts = {}, {}, {}
                allowed = dict(sec.CONCEPTS[statement])[row["label"]]
                for column, value in row["vals"].items():
                    start, end = column.split("|",1) if "|" in column else (None,column)
                    matches = [(concept,pointer) for concept in allowed for pointer in index.get(
                        (accession,concept,row["unit"],start,end,Decimal(str(value))),[])]
                    if matches:
                        values[column] = value
                        sources[column] = [pointer for _,pointer in matches]
                        concepts[column] = list(dict.fromkeys(concept for concept,_ in matches))
                    else:
                        removed += 1
                if values:
                    row.update(vals=values,source_records=sources,concepts=concepts)
                    kept_rows.append(row)
            table["rows"] = kept_rows
            table["cols"] = [column for column in table["cols"] if any(column in row["vals"] for row in kept_rows)]
    return result, removed
