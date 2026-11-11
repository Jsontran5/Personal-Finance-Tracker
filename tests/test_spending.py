from datetime import date
from unittest import TestCase

from finance.ledger import monthly_activity
from finance.spending import spending_report


def entry(identifier, day, amount, kind='expense', **fields):
    return {'id': identifier, 'date': day, 'amount_minor': amount, 'kind': kind, 'currency': 'USD',
            'merchant': 'Coffee shop', 'category_id': None, 'category_name': None, **fields}


class SpendingTests(TestCase):
    def test_eligibility_refunds_and_drilldowns_reconcile(self):
        rows = [entry(1, '2026-11-02', 1200, bank_category='FOOD_AND_DRINK'),
                entry(2, '2026-11-02', 2000, 'refund', merchant='  COFFEE   SHOP ', category_id=1, category_name='Food'),
                entry(3, '2026-11-03', 300, pending=1), entry(4, '2026-11-03', 300, needs_review=1),
                entry(5, '2026-11-03', 300, 'transfer'), entry(6, '2026-11-03', 300, 'investment'),
                entry(7, '2026-11-03', 300, currency='EUR'), entry(8, '2026-11-03', 300, 'income')]
        report = spending_report(rows, [], '2026-11', date(2026, 11, 11))
        self.assertEqual(report['eligible_ids'], [1, 2])
        self.assertEqual(report['net_minor'], -800)
        self.assertEqual(report['refunds_minor'], 2000)
        self.assertEqual(report['net_minor'], monthly_activity(rows)[1])
        self.assertEqual(report['daily'][1]['transaction_ids'], [1, 2])
        self.assertEqual(report['daily'][10]['cumulative_minor'], -800)
        self.assertIsNone(report['daily'][11]['cumulative_minor'])
        for key in ('categories', 'bank_categories', 'merchants'):
            self.assertEqual(sum(group['net_minor'] for group in report[key]), -800)
            self.assertEqual(sorted(identifier for group in report[key] for identifier in group['transaction_ids']), [1, 2])
        self.assertEqual(len(report['merchants']), 1)
        self.assertEqual(report['bank_categories'][0]['label'], 'Uncategorized')

    def test_partial_month_compares_same_elapsed_days(self):
        current = [entry(1, '2026-11-02', 3000), entry(2, '2026-11-12', 9000)]
        previous = [entry(3, '2026-10-02', 1000), entry(4, '2026-10-12', 8000)]
        report = spending_report(current, previous, '2026-11', date(2026, 11, 11))
        self.assertEqual(report['previous_minor'], 1000)
        self.assertEqual(report['net_minor'], 3000)
        self.assertEqual(report['change_percent'], 200)
        self.assertEqual(report['average_minor'], 273)
        self.assertIsNone(report['daily'][11]['previous_cumulative_minor'])

    def test_completed_month_uses_complete_previous_month(self):
        report = spending_report([], [entry(1, '2024-01-31', 100)], '2024-02', date(2026, 11, 11))
        self.assertEqual(len(report['daily']), 29)
        self.assertEqual(report['comparison_days'], 31)
        self.assertEqual(report['previous_minor'], 100)
        self.assertEqual(report['comparison_daily'][-1], {'day': 31, 'cumulative_minor': 100})

    def test_current_long_month_caps_short_comparison(self):
        report = spending_report([], [entry(1, '2024-02-29', 100)], '2024-03', date(2024, 3, 31))
        self.assertEqual(report['comparison_days'], 29)
        self.assertIsNone(report['daily'][29]['previous_cumulative_minor'])

    def test_empty_zero_negative_and_future_comparisons(self):
        for prior in ([], [entry(1, '2026-10-02', 100, 'refund')], [entry(1, '2026-10-02', 0)]):
            self.assertIsNone(spending_report([], prior, '2026-11', date(2026, 11, 11))['change_percent'])
        future = spending_report([entry(1, '2026-12-02', 100)], [], '2026-12', date(2026, 11, 11))
        self.assertEqual(future['through_day'], 0)
        self.assertEqual(future['eligible_ids'], [])
        self.assertTrue(all(point['cumulative_minor'] is None for point in future['daily']))
        self.assertIsNone(future['average_minor'])

    def test_earliest_month_has_no_previous_period(self):
        report = spending_report([], [], '0001-01', date(2026, 11, 11))
        self.assertIsNone(report['comparison_month'])
        self.assertIsNone(report['previous_minor'])
