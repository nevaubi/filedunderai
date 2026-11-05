"""Keep workbook table headings from changing the actual measured quantity."""
import unittest

from etl import kpi_taxonomy as K


class RevenueHeadingConflicts(unittest.TestCase):
    def definition(self, name):
        return {'name': name, 'unit': 'USD', 'category': 'Workbook: 2. Entertainment',
                'subcategory': 'Revenues', 'sums_to_parent': False}

    def test_published_disney_nonrevenue_labels_remain_unmapped_for_review(self):
        # Ten distinct DIS labels cover 22 published mapping rows; the last case
        # also occurs at MAA. The parenthesis is a heading, not the measured quantity.
        labels = [
            'Depreciation and amortization', 'Equity in the income of investees',
            'Operating expenses', 'Operating Income', 'Total costs and expenses',
            'Entertainment SVOD operating income(2)', 'Programming and production costs',
            'Operating Loss', 'Total Entertainment DTC Operating Expenses',
            'Total programming and production costs', 'Expense',
        ]
        for label in labels:
            with self.subTest(label=label):
                definition = self.definition(label + ' (Revenues)')
                self.assertIsNone(K.classify(definition))
                self.assertEqual(K.candidates(definition), [], 'a conflicting heading must not invite generic model inference')

    def test_real_revenue_members_keep_their_existing_mapping(self):
        for label in ['Advertising', 'Content sales', 'Subscription and affiliate fees',
                      'Subscription fees', 'Theme park admissions', 'Resorts and vacations',
                      'Media Networks', 'Fixed income', 'Expense management']:
            with self.subTest(label=label):
                result = K.classify(self.definition(label + ' (Revenues)'))
                self.assertEqual(result['canonical'], 'segment_revenue')
                self.assertEqual(result['scope'], 'segment')

    def test_unambiguous_operating_income_and_unknown_measures_are_preserved(self):
        result = K.classify({'name': 'Entertainment operating income', 'unit': 'USD',
                             'category': 'By segment', 'subcategory': 'Entertainment'})
        self.assertEqual(result['canonical'], 'segment_operating_income')
        self.assertEqual(result['member'], 'entertainment')
        self.assertIsNone(K.classify({'name': 'Unspecified operational count', 'unit': 'count'}))


if __name__ == '__main__':
    unittest.main()
