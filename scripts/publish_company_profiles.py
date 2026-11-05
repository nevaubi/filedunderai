"""Publish verified company profiles with backups, a lease, and exact readback."""
import argparse
import concurrent.futures
import datetime as dt
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from etl.common import AggregateLease, Supabase, now_iso
from etl.company_profiles import merge_official_profile, safe_url, partial_date, _provider_source
from etl.universe import BY_TICKER
from scripts.backfill_company_profiles import read_json, write_json
from scripts.generate_company_directory import build_directory


def validate_profile(profile):
    ticker = profile.get('ticker')
    issuer = BY_TICKER.get(ticker)
    if not issuer or profile.get('schema_version') != 1 or profile.get('cik') != issuer['cik']:
        raise ValueError('Profile issuer identity is invalid')
    if not profile.get('sources') or not profile.get('updated_at'):
        raise ValueError('Profile provenance is required')
    for person in profile.get('leadership', []):
        if _provider_source({'name': person.get('source_name'), 'url': person.get('source_url')}):
            raise ValueError('Corporate leadership requires official corroboration, not a provider-only title')
        if not person.get('name') or not person.get('role') or not safe_url(person.get('source_url')):
            raise ValueError('Leader lacks name, role, or provenance')
        if person.get('photo_url') and (not safe_url(person['photo_url']) or not safe_url(person.get('photo_source_url'))):
            raise ValueError('Portrait lacks safe URL or source')
        if person.get('start_date') and not safe_url(person.get('start_date_source_url') or (person.get('start_date_source') or {}).get('url')):
            raise ValueError('Role tenure requires an explicit appointment source')
        for date_key in ('start_date', 'employment_start_date', 'company_start_date'):
            if person.get(date_key) and partial_date(person[date_key], person.get('observed_at') or profile['updated_at']) != person[date_key]:
                raise ValueError('Invalid or future tenure date')
        for forbidden in ('emails', 'email', 'phone', 'mobile_phone', 'location_street_address', 'birth_date'):
            if forbidden in person:
                raise ValueError('Unrelated personal fields cannot be published')
    return profile


def merge_publication(incoming, previous):
    if not previous:
        return incoming
    validate_profile(previous)
    if previous['ticker'] != incoming['ticker'] or previous['cik'] != incoming['cik']:
        raise ValueError('Published issuer identity mismatch')
    # Start with the new metadata; the per-field merge protects newer official
    # evidence already live without dropping incoming provider supplements.
    return merge_official_profile(incoming, previous)


def publish(directory, fundamentals, sb, report_path):
    files = sorted(path for path in Path(directory).glob('*.json') if path.name != 'index.json')
    proposed = {path.stem: validate_profile(read_json(path)) for path in files}
    stamp = now_iso()
    backup_root = 'company-profiles/backups/' + stamp.replace(':', '-') + '/'
    results = []
    with AggregateLease(sb, name='company-profiles', seconds=300) as lease:
        previous_lease = getattr(sb, '_aggregate_lease', None)
        sb._aggregate_lease = lease
        existing_index = sb.get_json('company-profiles/index.json') or {'schema_version': 1, 'tickers': []}
        if existing_index.get('schema_version') != 1 or not isinstance(existing_index.get('tickers'), list):
            raise ValueError('Invalid live profile index; refusing to replace coverage')
        preserved = {}
        for ticker in existing_index['tickers']:
            if ticker not in proposed:
                remote = validate_profile(sb.get_json('company-profiles/' + ticker + '.json'))
                preserved[ticker] = remote
        def one(ticker):
            lease.check()
            path = 'company-profiles/' + ticker + '.json'
            previous = sb.get_json(path)
            result = validate_profile(merge_publication(proposed[ticker], previous))
            if previous == result:
                return {'ticker': ticker, 'status': 'unchanged', 'profile': result}
            if previous:
                sb.put_object('raw', backup_root + ticker + '.json', json.dumps(previous), cache='no-store')
            lease.check()
            sb.put_json(path, result, cache='max-age=120')
            observed = sb.get_json(path)
            if observed != result:
                raise RuntimeError('Published profile readback mismatch: ' + ticker)
            # Do not overwrite an official-source collector that finished while
            # this publication was in flight; its update belongs to the next pass.
            local_path=Path(directory) / (ticker + '.json')
            local_changed=read_json(local_path)!=proposed[ticker]
            if not local_changed:
                write_json(local_path, result)
            return {'ticker': ticker, 'status': 'published', 'profile': result, 'local_changed': local_changed}
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                for result in pool.map(one, proposed):
                    results.append(result)
                    print(json.dumps({'ticker': result['ticker'], 'status': result['status']}), flush=True)
            all_profiles = {**preserved, **{r['ticker']: r['profile'] for r in results}}
            manifest = build_directory(profiles=all_profiles,
                                       fundamentals=read_json(fundamentals, {}), generated_at=stamp)
            sb.put_json('company-directory.json', manifest, cache='max-age=120')
            if sb.get_json('company-directory.json') != manifest:
                raise RuntimeError('Published directory readback mismatch')
            write_json(ROOT / 'public/data/company-directory.json', manifest)
            index = {'schema_version': 1, 'generated_at': stamp,
                     'tickers': sorted(all_profiles), 'counts': manifest['counts']}
            write_json(Path(directory) / 'index.json', index)
            sb.put_json('company-profiles/index.json', index, cache='max-age=120')
            if sb.get_json('company-profiles/index.json') != index:
                raise RuntimeError('Published profile index readback mismatch')
            report = {'generated_at': stamp, 'counts': manifest['counts'],
                      'published': [r['ticker'] for r in results if r['status'] == 'published'],
                      'unchanged': [r['ticker'] for r in results if r['status'] == 'unchanged'],
                      'remote_profiles_preserved': sorted(preserved),
                      'local_updates_pending': [r['ticker'] for r in results if r.get('local_changed')],
                      'readback': 'all changed profiles and directory matched exactly',
                      'backup_prefix': backup_root}
            write_json(report_path, report)
            print(json.dumps(report), flush=True)
        finally:
            sb._aggregate_lease = previous_lease
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profiles', type=Path, default=ROOT / 'public/data/company-profiles')
    parser.add_argument('--fundamentals', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    publish(args.profiles, args.fundamentals, Supabase(), args.report)


if __name__ == '__main__':
    main()
