"""An exact original-source precision hold must never become a value heuristic."""
import copy
import gzip
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from etl import jobs, repair_normalization, sec
from test_company_fact_safety import CompanyStore


FIXTURE = json.loads((Path(__file__).parent / 'fixtures/fdxf_rounded_debt_source.json').read_text())
TARGET = FIXTURE['demonstrated_replacement']['after']
HOLD = 'fdxf_2026_may_debt_rounded_narrative'


class ReadOnlyCapture:
    def get_json(self, path):
        if path == 'meta/fundamentals.json':
            return {'FDXF': copy.deepcopy(FIXTURE['before_fund'])}
        if path == 'companies/FDXF.json':
            return copy.deepcopy(FIXTURE['bundle'])
        raise AssertionError(path)

    def get_object(self, bucket, path):
        assert (bucket, path) == ('raw', 'sec/companyfacts/CIK0002082247.json.gz')
        return gzip.compress(json.dumps(FIXTURE['companyfacts']).encode(), mtime=0)

    def select(self, table, query):
        assert table == 'facts'
        return copy.deepcopy(FIXTURE['before_facts'])


class SourceObservationHoldTests(unittest.TestCase):
    def test_actual_source_selection_is_held_before_scheduled_publication(self):
        source = copy.deepcopy(FIXTURE['companyfacts'])
        statements = {'balance': sec.build_series(source['facts'], 'balance', as_of='2026-11-05')}
        point = next(p for p in statements['balance'][0]['quarterly'] if p['end'] == '2026-05-31')
        self.assertEqual((point['v'], point['accn']), (4300000000, TARGET['accession']))
        self.assertEqual(FIXTURE['demonstrated_replacement']['before']['value'], 4264000000)
        store = CompanyStore(FIXTURE['before_facts'])
        prior = copy.deepcopy(FIXTURE['bundle'])
        store.data['companies/FDXF.json'] = copy.deepcopy(prior)
        sub = {'cik': 2082247, 'name': FIXTURE['bundle']['profile']['name'], 'tickers': ['FDXF'],
               'fiscalYearEnd': FIXTURE['bundle']['profile']['fiscalYearEnd'],
               'filings': {'recent': {'form': []}}}
        inputs = {'companyfacts': json.dumps(source).encode(), 'submissions': json.dumps(sub).encode()}
        with patch.object(jobs.sec, 'fill_gaps', return_value=[]), \
                patch.object(jobs.news, 'company_news', return_value=[]), \
                patch.object(jobs.summaries, 'company', return_value=None):
            with self.assertRaisesRegex(ValueError, HOLD):
                jobs.process_company(store, 'FDXF', facts_only=False, fetch_docs=False,
                                     source_inputs=inputs, log=lambda *_: None)
        self.assertEqual(store.data['companies/FDXF.json'], prior)
        self.assertEqual(store.facts, FIXTURE['before_facts'])
        self.assertEqual((store.writes, store.db), ([], []))

    def test_repair_planning_holds_actual_source_without_emitting_ready_candidate(self):
        with tempfile.TemporaryDirectory() as directory:
            plan = repair_normalization.plan_repair(ReadOnlyCapture(), ['FDXF'], directory,
                                                    as_of='2026-11-05', workers=1)
        entry = plan['companies'][0]
        self.assertEqual(entry['status'], 'blocked')
        self.assertIn(HOLD, entry['error'])
        self.assertNotIn('artifacts', entry)

    def test_known_observation_is_held_without_existing_rows_or_in_local_stage(self):
        for stage_only in (False, True):
            with self.subTest(stage_only=stage_only):
                store = CompanyStore()
                store.stage_only = stage_only
                with self.assertRaisesRegex(ValueError, HOLD):
                    jobs.check_reported_fact_replacements(store, 'FDXF', [copy.deepcopy(TARGET)])
                self.assertEqual((store.writes, store.db, store.facts), ([], [], []))

    def test_near_miss_identity_fields_do_not_inherit_the_hold(self):
        changes = [
            {'ticker': 'AAPL'}, {'statement': 'cashflow'}, {'label': 'Other debt'}, {'kind': 'A'},
            {'concept': 'LongTermDebtNoncurrent'}, {'unit': 'EUR'}, {'period_end': '2026-05-30'},
            {'period_start': '2026-01-01'}, {'filed': '2026-10-30'}, {'form': '10-K'},
            {'accession': '0001628280-26-068247'}, {'value': 4300000001},
            {'value': 4264000000}, {'derived': True},
        ]
        for change in changes:
            with self.subTest(change=change):
                point = dict(TARGET, **change)
                before = copy.deepcopy(point)
                store = CompanyStore()
                jobs.check_reported_fact_replacements(store, point['ticker'], [point])
                self.assertEqual(point, before)
                self.assertEqual((store.writes, store.db, store.facts), ([], [], []))


if __name__ == '__main__':
    unittest.main()
