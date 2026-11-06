"""Registry discovery must include pending issuers without widening screening."""
import unittest
from etl import universe


class CompanyDirectoryTests(unittest.TestCase):
    def test_all_registered_issuers_are_listed_with_separate_eligibility(self):
        from scripts.generate_company_directory import build_directory
        manifest = build_directory(generated_at='2026-11-05T00:00:00Z')
        rows = manifest['companies']
        self.assertEqual({c['ticker'] for c in rows}, set(universe.BY_TICKER))
        self.assertEqual(len(rows), len(universe.BY_TICKER))
        active = {t for t, *_ in universe.UNIVERSE}
        self.assertEqual(sum(c['screener_eligible'] for c in rows), len(active))
        pending = [c for c in rows if not c['screener_eligible']]
        self.assertEqual({c['ticker'] for c in pending}, set(universe.BY_TICKER) - active)
        self.assertTrue(all(c['cik'] and c['name'] and c['sector'] for c in rows))
        self.assertEqual(len(universe.UNIVERSE), 1485)

    def test_profile_metadata_cannot_override_registry_identity(self):
        from scripts.generate_company_directory import build_directory
        profile = {'ticker':'AAPL', 'cik':123, 'name':'Unrelated', 'website':'https://evil.example'}
        manifest = build_directory(profiles={'AAPL':profile})
        row = next(c for c in manifest['companies'] if c['ticker']=='AAPL')
        self.assertEqual(row['cik'], 320193)
        self.assertNotIn('domain',row)

    def test_verified_profile_fields_enrich_directory(self):
        from scripts.generate_company_directory import build_directory
        profile = {'ticker':'AAPL', 'cik':320193, 'website':'https://www.apple.com', 'industry':'Consumer electronics'}
        manifest = build_directory(profiles={'AAPL':profile})
        row = next(c for c in manifest['companies'] if c['ticker']=='AAPL')
        self.assertEqual(row['domain'],'apple.com')
        self.assertEqual(row['industry'],'Consumer electronics')

    def test_stored_industry_requires_registry_identity_and_preserves_profile_precedence(self):
        from scripts.generate_company_directory import build_directory
        issuer = universe.BY_TICKER['AAPL']
        filed = {'ticker':'AAPL', 'name':issuer['name'], 'sector':issuer['sector'], 'sic':'Electronic Computers'}
        profile = {'ticker':'AAPL', 'cik':320193, 'industry':'Consumer electronics'}
        row = next(c for c in build_directory(profiles={'AAPL':profile}, fundamentals={'AAPL':filed})['companies'] if c['ticker']=='AAPL')
        self.assertEqual(row['sic_description'],'Electronic Computers')
        self.assertEqual(row['industry'],'Consumer electronics')
        self.assertEqual(row['coverage']['profile'],'available')
        for change in ({'ticker':'OTHER'}, {'name':'Unrelated'}, {'sector':'Energy'}, {'sic':'3571'}):
            row = next(c for c in build_directory(fundamentals={'AAPL':{**filed, **change}})['companies'] if c['ticker']=='AAPL')
            self.assertNotIn('industry',row)

if __name__ == '__main__':
    unittest.main()
