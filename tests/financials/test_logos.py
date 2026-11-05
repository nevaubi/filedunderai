"""Additive logo publication must not erase metadata or replace existing assets."""
import hashlib
import io
import json
import unittest
from unittest.mock import patch
from urllib.parse import unquote, urlsplit

from PIL import Image, ImageDraw
from etl import common, logos


def png(size=64, blank=False, color='navy'):
    im = Image.new('RGBA', (size, size))
    if not blank:
        ImageDraw.Draw(im).rectangle((8, 8, size - 9, size - 9), fill=color)
    out = io.BytesIO()
    im.save(out, format='PNG')
    return out.getvalue()


def entry(ticker='AAON', body=None):
    body = png() if body is None else body
    return {'ticker': ticker, 'source': 'reviewed', 'body': body,
            'metadata': {'cik': logos.BY_TICKER[ticker]['cik'],
                         'source_url': 'https://example.com/company-logo.png',
                         'sha256': hashlib.sha256(body).hexdigest()}}


class Storage:
    """HTTP boundary double: actual publisher, PNG decoder and lease remain real."""
    def __init__(self):
        self.before = {'tickers': {'AAPL': 'legacy', 'BK': 'legacy-alias'},
                       'assets': {'AAPL': {'custom': 'preserved'}},
                       'missing': ['AAON', 'SKYD'], 'unknown': {'retain': [1, 2]}}
        self.raw = json.dumps(self.before, indent=2).encode()
        self.objects = {('public-data', 'meta/logos.json'): self.raw,
                        ('public-data', 'logos/AAPL.png'): png(color='red')}
        self.calls = []
        self.read_error = None
        self.after_asset = None
        self.corrupt = False

    def request(self, url, method='GET', data=None, **kw):
        path = unquote(urlsplit(url).path)
        self.calls.append((path, method, kw))
        if '/rpc/' in path:
            if data['lock_name'] != 'company-logos':
                raise AssertionError('Wrong lease')
            return 'true'
        if method == 'GET':
            bucket, name = path.split('/object/authenticated/', 1)[1].split('/', 1)
            if name == 'meta/logos.json' and self.read_error:
                raise self.read_error
            value = self.objects.get((bucket, name))
            if value is None:
                raise common.HTTPError(404, 'Object not found')
            return value
        if method != 'POST':
            raise AssertionError('Unexpected mutation')
        bucket, name = path.split('/object/', 1)[1].split('/', 1)
        key = (bucket, name)
        if kw['headers']['x-upsert'] == 'false' and key in self.objects:
            raise common.HTTPError(409, 'The resource already exists')
        self.objects[key] = bytes(data)
        if name.startswith('logos/'):
            if self.corrupt:
                self.objects[key] = b'corrupt upload'
            if self.after_asset:
                self.after_asset()
        return '{}'

    def client(self):
        sb = common.Supabase.__new__(common.Supabase)
        sb.url, sb.key = 'https://example.supabase.co', 'test-key'
        return sb

    @property
    def writes(self):
        return [c for c in self.calls if c[1] == 'POST' and '/object/' in c[0]]


class LogoPublicationTests(unittest.TestCase):
    def test_registry_read_failure_never_becomes_an_empty_registry(self):
        store = Storage()
        store.read_error = common.HTTPError(503, 'unavailable')
        with patch.object(logos, 'request', side_effect=store.request), \
             patch.object(common, 'request', side_effect=store.request), \
             patch.object(logos, 'fetch_logo', return_value=(None, None)):
            with self.assertRaises(common.HTTPError):
                logos.logos_job(store.client(), ['AAON'])
        self.assertEqual(store.writes, [])
        self.assertEqual(store.objects['public-data', 'meta/logos.json'], store.raw)

    def test_bad_small_and_empty_pngs_are_rejected_before_any_write(self):
        for body in (b'\x89PNG\r\n\x1a\n' + b'x' * 400, png(32), png(blank=True)):
            with self.subTest(length=len(body)):
                store = Storage()
                with patch.object(logos, 'request', side_effect=store.request):
                    with self.assertRaises(ValueError):
                        logos.publish_logo_assets(store.client(), [entry(body=body)])
                self.assertEqual(store.writes, [])

    def test_addition_preserves_legacy_alias_and_unknown_metadata(self):
        store = Storage()
        with patch.object(logos, 'request', side_effect=store.request):
            result = logos.publish_logo_assets(store.client(), [entry(), entry('AAPL')])
        after = json.loads(store.objects['public-data', 'meta/logos.json'])
        self.assertEqual(result['added'], ['AAON'])
        self.assertEqual(after['tickers'], {'AAPL': 'legacy', 'BK': 'legacy-alias', 'AAON': 'reviewed'})
        self.assertEqual(after['unknown'], {'retain': [1, 2]})
        self.assertEqual(after['assets']['AAPL'], {'custom': 'preserved'})
        self.assertEqual(len(after['missing']), len(logos.BY_TICKER) - 2)
        self.assertIn('SKYD', after['missing'])
        self.assertIn('ZWS', after['missing'])
        self.assertNotIn('AAON', after['missing'])
        self.assertNotIn('AAPL', after['missing'])
        self.assertEqual(store.objects['public-data', 'logos/AAPL.png'], png(color='red'))
        self.assertEqual(store.objects['raw', result['backup_path']], store.raw)
        self.assertEqual(after['assets']['AAON']['sha256'], hashlib.sha256(png()).hexdigest())
        for path, method, kw in store.calls:
            if method != 'GET':
                self.assertEqual((kw['retries'], kw['timeout']), (1, 30))
            if '/object/' in path and method == 'POST':
                self.assertEqual(kw['headers']['x-upsert'], 'true' if path.endswith('/meta/logos.json') else 'false')
                if path.endswith('/meta/logos.json'):
                    self.assertEqual(kw['headers']['cache-control'], 'max-age=120')

    def test_existing_different_object_is_preserved_and_identical_object_is_adopted(self):
        store = Storage()
        store.objects['public-data', 'logos/AAON.png'] = png()
        store.objects['public-data', 'logos/ABCB.png'] = png(color='green')
        with patch.object(logos, 'request', side_effect=store.request):
            result = logos.publish_logo_assets(store.client(), [entry(), entry('ABCB')])
        self.assertEqual(result['added'], ['AAON'])
        self.assertEqual([r['ticker'] for r in result['conflicts']], ['ABCB'])
        self.assertEqual(store.objects['public-data', 'logos/ABCB.png'], png(color='green'))
        self.assertNotIn('ABCB', json.loads(store.objects['public-data', 'meta/logos.json'])['tickers'])

    def test_malformed_registry_and_bad_readback_do_not_publish_metadata(self):
        for malformed in (b'not json', b'null', b'{"tickers": []}'):
            store = Storage()
            store.objects['public-data', 'meta/logos.json'] = malformed
            with patch.object(logos, 'request', side_effect=store.request), self.assertRaises(ValueError):
                logos.publish_logo_assets(store.client(), [entry()])
            self.assertEqual(store.writes, [])
        store = Storage()
        store.corrupt = True
        with patch.object(logos, 'request', side_effect=store.request), self.assertRaises(RuntimeError):
            logos.publish_logo_assets(store.client(), [entry()])
        self.assertEqual(store.objects['public-data', 'meta/logos.json'], store.raw)

    def test_registry_drift_is_not_overwritten(self):
        store = Storage()
        changed = json.dumps({**store.before, 'concurrent': True}).encode()
        store.after_asset = lambda: store.objects.__setitem__(('public-data', 'meta/logos.json'), changed)
        with patch.object(logos, 'request', side_effect=store.request), self.assertRaises(RuntimeError):
            logos.publish_logo_assets(store.client(), [entry()])
        self.assertEqual(store.objects['public-data', 'meta/logos.json'], changed)

    def test_lost_lease_stops_registry_publication(self):
        store, leases = Storage(), []
        def make_lease(*args, **kwargs):
            lease = common.AggregateLease(*args, **kwargs)
            leases.append(lease)
            return lease
        store.after_asset = lambda: setattr(leases[0], 'lost', 'heartbeat failed')
        with patch.object(logos, 'request', side_effect=store.request), \
             patch.object(logos, 'AggregateLease', side_effect=make_lease), \
             patch('builtins.print'), self.assertRaises(RuntimeError):
            logos.publish_logo_assets(store.client(), [entry()])
        self.assertEqual(store.objects['public-data', 'meta/logos.json'], store.raw)

    def test_newest_cohort_is_eligible_and_force_never_replaces_registered_logo(self):
        store = Storage()
        with patch.object(logos, 'request', side_effect=store.request), \
             patch.object(logos, 'fetch_logo', return_value=('parqet', png())):
            result = logos.logos_job(store.client(), ['AAON'], workers=2)
            self.assertEqual(result['added'], ['AAON'])
            result = logos.logos_job(store.client(), ['AAPL'], force=True, workers=2)
        self.assertEqual(result['added'], [])
        self.assertEqual(store.objects['public-data', 'logos/AAPL.png'], png(color='red'))


if __name__ == '__main__':
    unittest.main()
