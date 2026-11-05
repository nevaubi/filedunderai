"""Regression for official leadership added to an existing provider profile."""
import copy
import datetime as dt
import json
import unittest
from pathlib import Path

from etl.company_profiles import build_search_profile, merge_official_profile
from etl.universe import BY_TICKER


class PriorityRosterPreservationTests(unittest.TestCase):
    def test_all_fifty_verified_leaders_survive_a_new_provider_profile(self):
        root = Path(__file__).resolve().parents[2]
        expected = {'MO': 8, 'BKNG': 8, 'COF': 1, 'BNY': 8,
                    'MMM': 9, 'GD': 8, 'EMR': 8}
        retained = 0
        for ticker, count in expected.items():
            with self.subTest(ticker=ticker):
                official = json.loads((root / 'public/data/company-profiles' /
                                       (ticker + '.json')).read_text())
                self.assertEqual(len(official['leadership']), count)
                self.assertEqual(official['coverage']['status'],
                                 'official_with_provider_metadata')
                self.assertEqual(official['coverage']['leadership'], 'official_sources')
                original = copy.deepcopy(official)
                update = dt.datetime.fromisoformat(official['updated_at'].replace('Z', '+00:00'))
                fresh = (update + dt.timedelta(seconds=1)).isoformat().replace('+00:00', 'Z')
                issuer = BY_TICKER[ticker]
                # A new provider result has no authoritative leadership of its own.
                record = {'id': 'regression-' + ticker.lower(), 'ticker': ticker,
                          'name': issuer['name'], 'website': official.get('website'),
                          'industry': official.get('industry'), 'size': '10001+'}
                provider = build_search_profile(issuer, record, observed_at=fresh)
                self.assertEqual(provider['leadership'], [])
                merged = merge_official_profile(provider, official, now=fresh)
                self.assertEqual(merged['leadership'], official['leadership'])
                self.assertEqual(merged['coverage']['leadership'], 'official_sources')
                self.assertEqual(merged['coverage']['official_updated_at'],
                                 official['coverage']['official_updated_at'])
                self.assertEqual(merged['field_sources']['website']['observed_at'], fresh)
                self.assertEqual(merged['field_sources']['industry']['observed_at'], fresh)
                self.assertEqual(official, original, 'Merge mutated original provenance')
                retained += len(merged['leadership'])
        self.assertEqual(retained, 50)


if __name__ == '__main__':
    unittest.main()
