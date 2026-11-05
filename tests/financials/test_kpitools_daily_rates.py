"""Published daily averages must not be treated as period totals."""
import unittest
from unittest.mock import patch

from etl import kpitools


class DailyPackageRateTests(unittest.TestCase):
    def validate(self, unit, values, derived):
        data = {'ticker': 'UPS', 'kpis': [{'id': 'packages', 'unit': unit, 'values': values}],
                'derived': [{**derived, 'kpi': 'packages'}]}
        # These are already source-checked inputs. Exercise the separate economic
        # derivation check with arithmetic that would otherwise pass exactly.
        with patch.object(kpitools, 'docs_for', return_value=[]), \
                patch.object(kpitools, 'check_value', return_value=[]):
            return kpitools.validate(None, data)[0]

    def test_daily_average_cannot_derive_quarter_by_subtraction(self):
        report = self.validate('packages_per_day', [
            {'kind': 'Y', 'period_end': '2026-06-30', 'value': 19093000},
            {'kind': 'Q', 'period_end': '2026-03-31', 'value': 19184000},
        ], {'kind': 'Q', 'period_end': '2026-06-30', 'value': -91000,
            'formula': '6M - Q1', 'inputs': [
                {'kind': 'Y', 'period_end': '2026-06-30'},
                {'kind': 'Q', 'period_end': '2026-03-31'}]})
        self.assertEqual(report['bad_values'], [])
        self.assertEqual(len(report['derived_bad']), 1)
        self.assertIn('not additive', report['derived_bad'][0]['error'])

    def test_daily_averages_cannot_be_added(self):
        report = self.validate('packages_per_day', [
            {'kind': 'Q', 'period_end': '2026-03-31', 'value': 19184000},
            {'kind': 'Q', 'period_end': '2026-06-30', 'value': 19006000},
        ], {'kind': 'Y', 'period_end': '2026-06-30', 'value': 38190000,
            'formula': 'Q1 + Q2', 'inputs': [
                {'kind': 'Q', 'period_end': '2026-03-31'},
                {'kind': 'Q', 'period_end': '2026-06-30'}]})
        self.assertEqual(len(report['derived_bad']), 1)
        self.assertIn('not additive', report['derived_bad'][0]['error'])

    def test_total_package_volume_remains_additive(self):
        report = self.validate('packages', [
            {'kind': 'Y', 'period_end': '2026-06-30', 'value': 2406000000},
            {'kind': 'Q', 'period_end': '2026-03-31', 'value': 1189000000},
        ], {'kind': 'Q', 'period_end': '2026-06-30', 'value': 1217000000,
            'formula': '6M - Q1', 'inputs': [
                {'kind': 'Y', 'period_end': '2026-06-30'},
                {'kind': 'Q', 'period_end': '2026-03-31'}]})
        self.assertEqual(report['derived_bad'], [])


if __name__ == '__main__':
    unittest.main()
