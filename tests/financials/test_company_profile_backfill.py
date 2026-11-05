"""Bounded bulk enrichment must be safe to resume after a paid timeout."""
import tempfile
import unittest
from pathlib import Path
from scripts.backfill_company_profiles import BudgetedFetch, charged_credits, safe_record
from scripts.publish_company_profiles import validate_profile, merge_publication


class BulkProfileTests(unittest.TestCase):
    def test_uncertain_paid_transport_is_reserved_and_never_automatically_replayed(self):
        calls = []
        def failed(*args):
            calls.append(args[0])
            raise TimeoutError()
        with tempfile.TemporaryDirectory() as directory:
            run = BudgetedFetch(directory, {'company/enrich': 1}, 'test', transport=failed)
            run.remaining['company/enrich'] = 1
            self.assertIsNone(run.call('one', 'company/enrich', {'ticker': 'AAPL'}, 1, 'GET'))
            resumed = BudgetedFetch(directory, {'company/enrich': 1}, 'test', transport=failed)
            resumed.remaining['company/enrich'] = 1
            self.assertIsNone(resumed.call('one', 'company/enrich', {'ticker': 'AAPL'}, 1, 'GET'))
            self.assertEqual(calls, ['GET'])
            self.assertEqual(charged_credits(resumed.state['operations']['one']), 1)

    def test_successful_search_retains_paid_results_and_releases_only_receipted_unused_credit(self):
        def success(*args):
            return 200, {'data': [{'full_name': 'Jane Executive', 'job_company_id': 'one',
                                   'job_title': 'CEO', 'emails': ['private@example.test'],
                                   'mobile_phone': '+1-private'}]}, {
                'x-call-credits-spent': '1', 'x-totallimit-remaining': '9',
                'x-totallimit-purchased-remaining': '9', 'x-totallimit-overages-remaining': '0',
            }
        with tempfile.TemporaryDirectory() as directory:
            run = BudgetedFetch(directory, {'person/search': 10}, 'test', transport=success)
            run.remaining['person/search'] = 10
            result = run.call('people', 'person/search', {'size': 5}, 5)
            self.assertEqual(run.remaining['person/search'], 9)
            self.assertEqual(charged_credits(run.state['operations']['people']), 1)
            self.assertEqual(result['data'][0]['full_name'], 'Jane Executive')
            self.assertNotIn('emails', result['data'][0])
            self.assertNotIn('mobile_phone', result['data'][0])

    def test_unproven_credit_receipts_never_expand_the_budget(self):
        for spent in ('-1', 'Infinity', 'NaN', '1.5', '1000', None):
            with self.subTest(spent=spent):
                self.assertEqual(charged_credits({'status': '200', 'credits_reserved': 4,
                                                 'quota': {'x-call-credits-spent': spent}}), 4)
        self.assertEqual(charged_credits({'status': '404', 'credits_reserved': 4,
                                         'quota': {'x-call-credits-spent': '0'}}), 0)

    def test_company_raw_cache_excludes_unneeded_street_address_and_funding_metrics(self):
        record = safe_record({'ticker': 'AAA', 'location': {'locality': 'Chicago',
                              'street_address': 'private or irrelevant address'},
                              'total_funding_raised': 100, 'emails': ['irrelevant@example.test']}, 'company')
        self.assertEqual(record['location'], {'locality': 'Chicago'})
        self.assertNotIn('total_funding_raised', record)
        self.assertNotIn('emails', record)

    def test_reusing_operation_with_a_changed_paid_query_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            run=BudgetedFetch(directory, {'company/enrich': 1}, 'test', transport=lambda *args: (_ for _ in ()).throw(TimeoutError()))
            run.remaining['company/enrich']=1
            run.call('one','company/enrich',{'ticker':'AAPL'},1,'GET')
            with self.assertRaisesRegex(ValueError,'different endpoint or query'):
                run.call('one','company/enrich',{'ticker':'MSFT'},1,'GET')

    def test_new_company_metadata_survives_publication_beside_newer_official_roster(self):
        from tests.financials.test_company_profiles import ISSUER,COMPANY,NOW
        from etl.company_profiles import build_profile
        prior={'schema_version':1,'ticker':'AAPL','cik':320193,'updated_at':NOW,
               'coverage':{'status':'official_sources'},
               'sources':[{'name':'Apple','url':'https://apple.com/leadership','observed_at':NOW}],
               'leadership':[{'name':'Current Executive','role':'CEO','source_url':'https://apple.com/leadership','observed_at':NOW}]}
        incoming=build_profile(ISSUER,COMPANY,observed_at=NOW)
        result=merge_publication(incoming,prior)
        self.assertEqual(result['professional_profile_count'],COMPANY['employee_count'])
        self.assertEqual(result['linkedin_url'],'https://linkedin.com/company/apple')
        self.assertEqual(result['leadership'][0]['name'],'Current Executive')

    def test_provider_only_current_title_cannot_be_published(self):
        from tests.financials.test_company_profiles import ISSUER,COMPANY,PERSON,NOW
        from etl.company_profiles import build_profile,sanitize_leadership
        profile=build_profile(ISSUER,COMPANY,observed_at=NOW)
        profile['leadership']=sanitize_leadership([PERSON],'company-apple',NOW)
        with self.assertRaisesRegex(ValueError,'official corroboration'):
            validate_profile(profile)

    def test_publication_rejects_unsourced_tenure_and_portraits_before_writing(self):
        profile = {'ticker': 'AAPL', 'cik': 320193, 'schema_version': 1, 'updated_at': '2026-11-05',
                   'sources': [{'url': 'https://apple.com/leadership'}], 'leadership': [
                       {'name': 'Example Executive', 'role': 'CEO', 'source_url': 'https://apple.com/leadership',
                        'start_date': '2020'}]}
        with self.assertRaisesRegex(ValueError, 'appointment source'):
            validate_profile(profile)
        leader = profile['leadership'][0]
        leader['start_date_source_url'] = 'https://apple.com/appointment'
        leader['photo_url'] = 'https://apple.com/portrait.png'
        with self.assertRaisesRegex(ValueError, 'Portrait'):
            validate_profile(profile)
        leader['photo_source_url'] = 'https://apple.com/biography'
        self.assertIs(validate_profile(profile), profile)
        leader['start_date'] = '2027'
        with self.assertRaisesRegex(ValueError, 'future'):
            validate_profile(profile)


if __name__ == '__main__':
    unittest.main()
