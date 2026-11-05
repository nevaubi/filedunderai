"""A source conflict withholds values without moving the reporting calendar back."""
import calendar
import copy
import datetime as dt
import unittest

from etl import screener


def company():
    data = {'ticker': 'TEST', 'q': {}, 'filing_history': {}}
    measures = [('Revenue', 'Revenues', 100), ('Net income', 'NetIncomeLoss', 20),
                ('EPS diluted', 'EarningsPerShareDiluted', 1),
                ('Diluted shares', 'WeightedAverageNumberOfDilutedSharesOutstanding', 10),
                ("Shareholders' equity", 'StockholdersEquity', 200)]
    for label, concept, value in measures:
        points, history = [], []
        for year in range(2022, 2027):
            for quarter in range(1, 5):
                month = quarter * 3
                end = dt.date(year, month, calendar.monthrange(year, month)[1])
                if end > dt.date(2026, 9, 30):
                    continue
                start = dt.date(year, month - 2, 1).isoformat()
                point = {'end': end.isoformat(), 'start': start, 'v': value,
                         'accn': f'{year}-{quarter}', 'filed': (end + dt.timedelta(days=31)).isoformat(),
                         'form': '10-Q', 'c': concept}
                if label == "Shareholders' equity":
                    point.pop('start')
                if end == dt.date(2026, 9, 30) and label == 'EPS diluted':
                    point['v'] = 9
                history.append(screener._rec(point, concept))
                if end == dt.date(2026, 9, 30) and label in ('Revenue', 'Net income'):
                    alternative = dict(point, v=value + 5, filed='2026-11-01', accn='later', derived=True)
                    history.append(screener._rec(alternative, concept))
                    point['selection_conflict'] = {
                        'reason': 'later_derived_value_differs_from_reported', 'alternative': alternative}
                points.append(screener._rec(point, concept))
        data['q'][label] = points
        data['filing_history'][label] = history
    return data


class ScreenerPeriodHolds(unittest.TestCase):
    def test_latest_conflicts_hold_metrics_without_rolling_back_the_period(self):
        data = company()
        before = copy.deepcopy(data)
        values, proof, facts, inputs = screener.fundamentals_for('TEST', data, [], 10, 'Industrials')
        self.assertEqual(inputs['end'], '2026-09-30')
        self.assertIsNone(inputs['rev'])
        self.assertIsNone(inputs['ni'])
        self.assertEqual(inputs['eps'], 12, 'independent EPS must use the latest four quarters')
        self.assertNotIn('nm', values)
        for field in ('nm', 'roe', 'ps', 'ev_sales', 'rev_g_q'):
            with self.subTest(field=field):
                held = proof.f[field]
                self.assertEqual(held['status'], 'held')
                self.assertEqual(held['period_end'], '2026-09-30')
                self.assertTrue(all(proof.refs[i][-1]['selection_conflict'] for i in held['i']))
        self.assertNotIn('pe', proof.f, 'valid diluted EPS is independent of the income hold')
        self.assertEqual(facts.ttm('Revenue', '2026-06-30')[0], 400)
        self.assertEqual(data, before)

    def test_asof_calendar_keeps_the_visible_quarter_when_a_later_comparison_conflicts(self):
        data = company()
        earlier = screener.Facts(data, [], as_of='2026-09-30')
        self.assertEqual(earlier.ends[-1], '2026-06-30', 'future filings must not anchor an earlier snapshot')
        reported = screener.Facts(data, [], as_of='2026-10-31')
        self.assertEqual(reported.ttm('Revenue', reported.ends[-1])[0], 400)
        conflicted = screener.Facts(data, [], as_of='2026-11-01')
        self.assertEqual(conflicted.ends[-1], '2026-09-30')
        self.assertIsNone(conflicted.ttm('Revenue', conflicted.ends[-1])[0])
        self.assertEqual(conflicted.ttm('EPS diluted', conflicted.ends[-1])[0], 12)

    def test_valuation_history_does_not_substitute_prior_ratios_for_the_held_latest_quarter(self):
        data = company()
        _, _, facts, inputs = screener.fundamentals_for('TEST', data, [], 10, 'Industrials')
        dates = [f'{year}-{month:02d}-{calendar.monthrange(year, month)[1]}'
                 for year in range(2023, 2027) for month in range(1, 13)
                 if (year, month) <= (2026, 10)] + ['2026-11-01']
        before = screener.valuation_history(facts, inputs, dates[:-1], [10] * (len(dates) - 1))
        self.assertIn('pe', before)
        self.assertIn('ps', before)
        held = screener.valuation_history(facts, inputs, dates, [10] * len(dates))
        self.assertNotIn('pe', held)
        self.assertNotIn('ps', held)
        self.assertIn('pb', held, 'independent balance sheet history remains usable')


if __name__ == '__main__':
    unittest.main()
