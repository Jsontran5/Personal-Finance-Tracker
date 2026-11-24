"""Pending estimates never become funding and reconcile with provider replacements."""
from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from fastapi.testclient import TestClient

from finance.api import create_app
from finance.db import connect
from finance.plaid_sandbox import save_accounts, save_transactions, changes
from finance.spending import spending_report
from tests.test_spending import entry


class PendingReportTests(TestCase):
    def test_status_totals_charts_and_comparison_reconcile(self):
        rows = [entry(1, '2026-11-02', 1000), entry(2, '2026-11-02', 400, pending=1),
                entry(3, '2026-11-03', 100, 'refund', pending=1),
                entry(4, '2026-11-03', 5000, 'income', pending=1),
                entry(5, '2026-11-03', 800, pending=1, needs_review=1),
                entry(6, '2026-11-03', 800, 'transfer', pending=1),
                entry(7, '2026-11-03', 800, pending=1, transfer_pair_id=2),
                entry(8, '2026-11-03', 800, pending=1, removed=1),
                entry(9, '2026-11-03', 800, pending=1, currency='EUR')]
        prior = [entry(10, '2026-10-02', 500), entry(11, '2026-10-03', 200, pending=1)]
        for status, expected, ids in [('posted', 1000, [1]), ('pending', 300, [2, 3]), ('all', 1300, [1, 2, 3])]:
            report = spending_report(rows, prior, '2026-11', date(2026, 11, 10), status=status)
            self.assertEqual(report['net_minor'], expected)
            self.assertEqual(report['eligible_ids'], ids)
            self.assertEqual(report['posted_minor'] + report['pending_minor'], expected)
            self.assertEqual(sum(day['net_minor'] or 0 for day in report['daily']), expected)
            for source in ['categories', 'bank_categories', 'merchants']:
                self.assertEqual(sum(row['net_minor'] for row in report[source]), expected)
            self.assertEqual(report['previous_minor'], {'posted': 500, 'pending': 200, 'all': 700}[status])
            self.assertEqual(sum(row['pending_purchase_count'] for row in report['categories']), 0 if status=='posted' else 1)
        income = spending_report(rows, [], '2026-11', date(2026, 11, 10), status='all', measure='income')
        self.assertEqual((income['posted_minor'], income['pending_minor']), (0, 5000))

    def test_paginated_posting_is_saved_as_one_update(self):
        calls = []
        def call(path, **kwargs):
            calls.append(kwargs.get('cursor'))
            return {'added': [{'transaction_id': 'posted'}] if len(calls) == 1 else [],
                    'modified': [], 'removed': [] if len(calls) == 1 else [{'transaction_id': 'pending'}],
                    'next_cursor': 'page2' if len(calls) == 1 else 'done', 'has_more': len(calls) == 1}
        cursor, _, added, _, removed = changes(call, 'fixture', None)
        self.assertEqual(cursor, 'done')
        self.assertEqual(added, [{'transaction_id': 'posted'}])
        self.assertEqual(removed, [{'transaction_id': 'pending'}])


class PendingLifecycleTests(TestCase):
    def setUp(self):
        self.folder = TemporaryDirectory()
        self.database = Path(self.folder.name) / 'data/finance.db'
        self.env = patch.dict('os.environ', {'APP_ENV': 'development', 'APP_STARTUP_ENABLED': 'false'})
        self.env.start()
        self.client = TestClient(create_app(self.database), base_url='http://127.0.0.1', headers={'x-finance-request': '1'})
        self.client.__enter__()
        self.day = date.today().isoformat()
        self.month = self.day[:7]
        self.path = '/api/plaid/sandbox/preview'
        self.source = self.database.with_suffix('.plaid-sandbox.db')
        self.client.get(self.path, params={'month': self.month})
        with connect(self.source, write=True) as db:
            save_accounts(db, [dict(account_id='cash', name='Fixture checking', type='depository', subtype='checking', balances={'current': 1000, 'iso_currency_code': 'USD'}),
                               dict(account_id='card', name='Fixture card', type='credit', subtype='credit card', balances={'current': 100, 'iso_currency_code': 'USD'})], 'cached')
        data = self.get()
        self.category = next(c['id'] for c in data['categories'] if c['kind'] == 'expense')
        self.cash = next(a['id'] for a in data['accounts'] if a['name'] == 'Fixture checking')
        self.card = next(a['id'] for a in data['accounts'] if a['name'] == 'Fixture card')

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.env.stop()
        self.folder.cleanup()

    def get(self, suffix='', **params):
        response = self.client.get(self.path + suffix, params={'month': self.month, **params})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def tx(self, identifier, amount=40, pending=True, **extra):
        return dict(transaction_id=identifier, account_id='card', name='Fixture purchase', amount=amount,
                    pending=pending, date=self.day, iso_currency_code='USD', personal_finance_category={'primary': 'GENERAL_MERCHANDISE'}, **extra)

    def save(self, added=(), modified=(), removed=()):
        with connect(self.source, write=True) as db:
            save_transactions(db, list(added), list(modified), list(removed))

    def test_replacement_amount_category_month_removal_and_repeated_sync(self):
        self.save([self.tx('pending')])
        data = self.get()
        identifier = data['transactions'][0]['id']
        response = self.client.put(self.path + '/transactions/' + str(identifier), json={'merchant': 'My gas', 'kind': 'expense', 'category_id': self.category, 'notes': 'Keep category'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.get('/analytics', status='all')['net_minor'], 4000)
        self.save(modified=[self.tx('pending', amount=45)])
        self.assertEqual(self.get('/analytics', status='pending')['net_minor'], 4500)
        posted = self.tx('posted', amount=47, pending=False, pending_transaction_id='pending')
        for _ in range(2):
            self.save([posted], removed=[{'transaction_id': 'pending'}])
            report = self.get('/analytics', status='all')
            self.assertEqual((report['posted_minor'], report['pending_minor'], len(report['eligible_ids'])), (4700, 0, 1))
            row = self.get()['transactions'][0]
            self.assertEqual((row['category_id'], row['merchant'], row['notes']), (self.category, 'My gas', 'Keep category'))
        # Provider corrections can move the posted date to another month.
        old_month = (date.today().replace(day=1) - timedelta(days=1)).isoformat()[:7]
        corrected = {**posted, 'date': old_month + '-01', 'amount': 48}
        self.save(modified=[corrected])
        self.assertEqual(self.get('/analytics', status='all')['net_minor'], 0)
        self.assertEqual(self.get('/analytics', status='all', month=old_month)['net_minor'], 4800)
        self.save([self.tx('cancelled')])
        self.save(removed=[{'transaction_id': 'cancelled'}])
        self.assertEqual(self.get('/analytics', status='pending')['eligible_ids'], [])
        self.assertEqual(self.client.get(self.path + '/analytics', params={'month': self.month, 'status': 'invalid'}).status_code, 422)

    def test_pending_does_not_change_funding_activity_or_card_reserves(self):
        plan = self.get('/plan')
        response = self.client.post(self.path + '/plan/setup', json={'token': plan['opening_token'], 'cash_accounts': [self.cash], 'credit_accounts': [self.card], 'method': 'zero'})
        self.assertEqual(response.status_code, 200, response.text)
        plan = self.get('/plan')
        response = self.client.post(self.path + '/plan/actions', params={'month': self.month}, json={'token': plan['token'], 'kind': 'assign', 'lines': [{'category': 'c:' + str(self.category), 'amount_minor': 10000}]})
        self.assertEqual(response.status_code, 200, response.text)
        before = self.get('/plan')
        self.save([self.tx('pending'), {**self.tx('pay', amount=-500), 'account_id': 'cash', 'personal_finance_category': {'primary': 'INCOME'}}])
        identifier = next(t['id'] for t in self.get()['transactions'] if t['kind'] == 'expense')
        self.client.put(self.path + '/transactions/' + str(identifier), json={'merchant': 'Gas', 'kind': 'expense', 'category_id': self.category, 'notes': ''})
        data = self.get('/plan')
        row = next(c for c in data['categories'] if c['id'] == self.category)
        self.assertEqual((row['activity_minor'], row['available_minor'], row['pending_purchases_minor'], row['after_pending_minor']), (0, 10000, 4000, 6000))
        self.assertEqual(data['ready_minor'], before['ready_minor'])
        self.assertEqual(data['receipts'], [])
        self.assertEqual(data['reserves'], before['reserves'])
        self.assertEqual(data['monthly_summary']['income_minor'], 0)
        self.assertEqual(data['preview_summary']['income_minor'], 0)
        self.assertEqual(data['preview_summary']['spending_minor'], 4000)
        self.save([self.tx('posted', amount=43, pending=False, pending_transaction_id='pending')])
        data = self.get('/plan')
        row = next(c for c in data['categories'] if c['id'] == self.category)
        self.assertEqual((row['activity_minor'], row['available_minor'], row['pending_purchases_minor'], row['after_pending_minor']), (-4300, 5700, 0, 5700))
        self.assertEqual(data['reserves'][0]['reserved_minor'], 4300)

    def test_pending_refunds_and_out_of_scope_accounts_do_not_release_budget_cash(self):
        plan = self.get('/plan')
        response = self.client.post(self.path + '/plan/setup', json={'token': plan['opening_token'], 'cash_accounts': [self.cash], 'credit_accounts': [], 'method': 'zero'})
        self.assertEqual(response.status_code, 200, response.text)
        self.save([self.tx('untracked-card'), {**self.tx('refund', amount=-8), 'account_id': 'cash'}])
        for tx in self.get()['transactions']:
            response = self.client.put(self.path + '/transactions/' + str(tx['id']), json={'merchant': 'Fixture', 'kind': 'refund' if tx['kind'] != 'expense' else 'expense', 'category_id': self.category, 'notes': ''})
            self.assertEqual(response.status_code, 200, response.text)
        data = self.get('/plan')
        row = next(c for c in data['categories'] if c['id'] == self.category)
        self.assertEqual((row['available_minor'], row['pending_purchases_minor'], row['after_pending_minor']), (0, 0, 0))
        self.assertEqual(data['ready_minor'], 100000)
        self.assertEqual(data['monthly_summary']['spending_minor'], 0)
        self.assertEqual(data['preview_summary']['spending_minor'], 3200)
