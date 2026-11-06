"""Republish reviewed revenue-scope corrections for cohort 3 issuers published earlier the same day.

Only Revenue fact rows change (same keys); the new proof object and company bundle are uploaded with read-back,
then meta/fundamentals.json entries are replaced under the aggregate lease. A receipt goes to the raw bucket."""
import hashlib, json, sys
from pathlib import Path
sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[1]))
from etl.common import Supabase, now_iso, serialized_aggregates
from etl.backfill import validate_stage, encoded, safe_path
OLD, NEW = Path(sys.argv[1]), Path(sys.argv[2]); tickers = sys.argv[3:]
sb = Supabase(); dig = lambda b: hashlib.sha256(b).hexdigest()

@serialized_aggregates
def run(sb):
    receipt = {'version': 1, 'kind': 'cohort3_revenue_scope_repair', 'started_at': now_iso(), 'companies': {}}
    fund = sb.get_json('meta/fundamentals.json')
    for t in tickers:
        new_r, old_r = validate_stage(NEW / 'staged' / t), validate_stage(OLD / 'staged' / t)
        key = lambda r: (r['statement'], r['label'], r['kind'], r['period_end'])
        old_f = {key(r): r for r in json.loads((OLD / 'staged' / t / 'tables/facts.json').read_bytes())}
        new_f = {key(r): r for r in json.loads((NEW / 'staged' / t / 'tables/facts.json').read_bytes())}
        assert set(old_f) == set(new_f), t
        changed = [new_f[k] for k in new_f if new_f[k] != old_f[k]]
        assert changed and all(r['label'] == 'Revenue' for r in changed), t
        live = sb.get_json(f'companies/{t}.json')
        old_bundle = json.loads((OLD / 'staged' / t / f'objects/public-data/companies/{t}.json').read_bytes())
        assert live and live.get('provenance') == old_bundle.get('provenance'), f'{t}: live bundle changed since publication'
        for item in new_r['objects']:
            if item['bucket'] == 'public-data' and item['path'].startswith('sources/companies/'):
                body = safe_path(NEW / 'staged' / t, item['local_path']).read_bytes()
                sb.put_object('public-data', item['path'], body, cache='max-age=31536000,immutable')
                assert dig(sb.get_object('public-data', item['path']) or b'') == item['sha256']
        sb.upsert('facts', changed, 'ticker,statement,label,kind,period_end', batch=500)
        bitem = next(i for i in new_r['objects'] if i['path'] == f'companies/{t}.json')
        body = safe_path(NEW / 'staged' / t, bitem['local_path']).read_bytes()
        sb.put_object('public-data', bitem['path'], body, cache='max-age=300')
        assert dig(sb.get_object('public-data', bitem['path']) or b'') == bitem['sha256']
        fund[t] = new_r['fundamental']
        receipt['companies'][t] = {'revenue_rows_replaced': len(changed), 'bundle_sha256': bitem['sha256'],
                                   'before_bundle_sha256': next(i['sha256'] for i in old_r['objects'] if i['path'] == f'companies/{t}.json')}
        print(t, len(changed), flush=True)
    sb.put_json('meta/fundamentals.json', fund, cache='no-store')
    assert all(sb.get_json('meta/fundamentals.json').get(t) == fund[t] for t in tickers)
    receipt['completed_at'] = now_iso()
    sb.put_object('raw', 'backfills/additional-399-spsm-2026-11-04-r1/revenue-scope-repair.json', encoded(receipt), cache='no-store')
    print(json.dumps({k: v for k, v in receipt.items() if k != 'companies'}))
run(sb)
