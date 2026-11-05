"""Bounded bulk enrichment must be safe to resume after a paid timeout."""
import copy
import tempfile
import unittest
from pathlib import Path
from scripts.backfill_company_profiles import BudgetedFetch, charged_credits, safe_record
from scripts.publish_company_profiles import validate_profile, merge_publication


class BulkProfileTests(unittest.TestCase):
    @staticmethod
    def official_profile():
        stamp = '2026-11-05T14:00:00Z'
        return {'schema_version': 1, 'ticker': 'AAPL', 'cik': 320193,
                'updated_at': stamp, 'coverage': {'status': 'official_sources', 'official_updated_at': stamp},
                'sources': [{'name': 'Apple', 'url': 'https://apple.com/leadership', 'observed_at': stamp}],
                'leadership': [{'name': 'Current Executive', 'role': 'Chief Executive Officer',
                                'source_name': 'Apple', 'source_url': 'https://apple.com/leadership',
                                'observed_at': stamp, 'photo_url': 'https://apple.com/old.jpg',
                                'photo_source_url': 'https://apple.com/leadership'}]}

    def test_same_roster_observation_preserves_more_recent_portrait_link_and_tenure_supplements(self):
        previous = self.official_profile()
        incoming = copy.deepcopy(previous)
        incoming['updated_at'] = '2026-11-05T15:00:00Z'
        incoming['leadership'][0].update(
            photo_url='https://apple.com/new.jpg',
            linkedin_url='https://www.linkedin.com/in/current-executive/',
            linkedin_source_url='https://apple.com/executive',
            start_date='2025-01', start_date_source_url='https://apple.com/appointment')
        result = merge_publication(incoming, previous)
        person = result['leadership'][0]
        self.assertEqual(person.get('photo_url'), 'https://apple.com/new.jpg')
        self.assertEqual(person.get('linkedin_url'), 'https://www.linkedin.com/in/current-executive/')
        self.assertEqual(person.get('start_date'), '2025-01')
        self.assertEqual(person['observed_at'], '2026-11-05T14:00:00Z')
        # An older local file must not erase a newer published supplement either.
        reverse = merge_publication(previous, incoming)
        self.assertEqual(reverse['leadership'], result['leadership'])
        self.assertEqual(reverse['updated_at'], '2026-11-05T15:00:00Z')
        self.assertEqual(reverse['coverage']['official_updated_at'], '2026-11-05T14:00:00Z')

    def test_more_recent_cache_timestamp_cannot_override_newer_role_evidence(self):
        previous = self.official_profile()
        previous['leadership'][0].update(name='New Executive', observed_at='2026-11-05T15:00:00Z')
        previous['updated_at'] = '2026-11-05T15:00:00Z'
        incoming = self.official_profile()
        incoming['updated_at'] = '2026-11-05T16:00:00Z'
        result = merge_publication(incoming, previous)
        self.assertEqual(result['leadership'][0]['name'], 'New Executive')

    def test_official_only_publication_retains_independently_sourced_provider_company_facts(self):
        from tests.financials.test_company_profiles import ISSUER, COMPANY, NOW
        from etl.company_profiles import build_profile, merge_official_profile
        provider = build_profile(ISSUER, {**COMPANY, 'size': '10001+'}, observed_at=NOW)
        mixed = merge_official_profile(provider, self.official_profile(), now=NOW)
        for previous in (provider, mixed):
            with self.subTest(status=previous['coverage']['status']):
                incoming = self.official_profile()
                incoming['updated_at'] = '2026-11-05T15:00:00Z'
                result = merge_publication(incoming, previous)
                self.assertEqual(result.get('professional_profile_count'), 201335)
                self.assertEqual(result.get('headquarters'), 'Cupertino, California, United States')
                self.assertEqual(result.get('linkedin_url'), 'https://linkedin.com/company/apple')
                self.assertEqual(result.get('employee_size_range'), '10001+')
                self.assertEqual(result.get('provider_record_id'), 'company-apple')
                self.assertEqual(result.get('description_source', {}).get('name'), 'People Data Labs')
                self.assertEqual(result.get('field_sources', {}).get('headquarters', {}).get('observed_at'), NOW)
                self.assertEqual(result.get('professional_profile_count_source', {}).get('record_id'), 'company-apple')
                self.assertEqual(result['coverage']['status'], 'official_with_provider_metadata')
                self.assertEqual({s['name'] for s in result['sources']}, {'Apple', 'People Data Labs'})
                self.assertIsNone(result.get('employees'))
                self.assertEqual(result['leadership'][0]['name'], 'Current Executive')

    def test_provider_fallback_cannot_replace_official_facts_or_new_provider_facts(self):
        from tests.financials.test_company_profiles import ISSUER, COMPANY, NOW
        from etl.company_profiles import build_profile, merge_official_profile
        previous = build_profile(ISSUER, COMPANY, observed_at=NOW)
        incoming = self.official_profile()
        incoming['headquarters'] = 'Official headquarters'
        incoming['field_sources'] = {'headquarters': incoming['sources'][0]}
        result = merge_publication(incoming, previous)
        self.assertEqual(result['headquarters'], 'Official headquarters')
        self.assertEqual(result['field_sources']['headquarters']['name'], 'Apple')
        new_provider = build_profile(ISSUER, {**COMPANY, 'employee_count': 123456},
                                     observed_at='2026-11-05T15:00:00Z')
        old_mixed = merge_official_profile(previous, self.official_profile(), now=NOW)
        result = merge_publication(new_provider, old_mixed)
        self.assertEqual(result['professional_profile_count'], 123456)
        self.assertEqual(result['professional_profile_count_source']['observed_at'], '2026-11-05T15:00:00Z')

    def test_duplicate_provider_titles_with_one_current_linkedin_url_are_unambiguous(self):
        from tests.financials.test_company_profiles import ISSUER, COMPANY, PERSON, NOW
        from etl.company_profiles import build_profile, merge_official_profile, sanitize_leadership
        records = [{**PERSON, 'full_name': 'Current Executive', 'job_title': 'CEO'},
                   {**PERSON, 'full_name': 'Current Executive', 'job_title': 'Chairman and CEO',
                    'job_last_verified': '2026-11-01'}]
        candidates = sanitize_leadership(records, 'company-apple', NOW, expected_domain='apple.com')
        result = merge_official_profile(build_profile(ISSUER, COMPANY, observed_at=NOW),
                                        self.official_profile(), now=NOW, provider_people=candidates)
        person = result['leadership'][0]
        self.assertEqual(person.get('linkedin_url'), 'https://linkedin.com/in/example-executive')
        self.assertEqual(person['linkedin_source']['provider_verified_at'], '2026-11-01')
        self.assertEqual(person['role'], 'Chief Executive Officer')

    def test_provider_linkedin_deduplication_rejects_conflicts_and_keeps_identity_guards(self):
        from tests.financials.test_company_profiles import ISSUER, COMPANY, PERSON, NOW
        from etl.company_profiles import build_profile, merge_official_profile, sanitize_leadership
        ceo = {**PERSON, 'full_name': 'Current Executive', 'job_title': 'CEO'}
        chairman = {**ceo, 'job_title': 'Chairman and CEO'}
        cases = [
            [ceo, {**chairman, 'linkedin_url': 'https://linkedin.com/in/a-different-person'}],
            [{**ceo, 'job_company_id': 'another-company'}, {**chairman, 'job_company_id': 'another-company'}],
            [{**ceo, 'full_name': 'Another Executive'}, {**chairman, 'full_name': 'Another Executive'}],
            [{**ceo, 'job_title': 'CFO'}, {**chairman, 'job_title': 'EVP and CFO'}],
            [{**ceo, 'job_last_verified': '2025-01-01'}, {**chairman, 'job_last_verified': '2025-01-01'}],
        ]
        for records in cases:
            with self.subTest(records=records):
                candidates = sanitize_leadership(records, 'company-apple', NOW, expected_domain='apple.com')
                result = merge_official_profile(build_profile(ISSUER, COMPANY, observed_at=NOW),
                                                self.official_profile(), now=NOW, provider_people=candidates)
                self.assertIsNone(result['leadership'][0].get('linkedin_url'))

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
                   'coverage': {'status': 'official_sources'},
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

    def test_sourced_leaders_with_provider_only_coverage_are_rejected_instead_of_silently_lost(self):
        profile={'schema_version':1,'ticker':'AAPL','cik':320193,'updated_at':'2026-11-05',
                 'coverage':{'status':'provider_enriched'},'sources':[{'name':'Apple','url':'https://apple.com/leadership'}],
                 'leadership':[{'name':'Official Executive','role':'CEO','source_url':'https://apple.com/leadership'}]}
        with self.assertRaisesRegex(ValueError,'official coverage status'):
            validate_profile(profile)


if __name__ == '__main__':
    unittest.main()
