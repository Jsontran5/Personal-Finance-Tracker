from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
import json

from fastapi.testclient import TestClient
from finance.api import create_app
from finance.db import connect
from finance.investments import fetch_activity, history_start, project_investments, save_activity
from finance.plaid_sandbox import PlaidError, sandbox_router
from finance.plaid_vault import protect
from finance.recovery import BackupStore


def positions():
    return {'accounts': [{'account_id': 'broker', 'name': 'Existing brokerage', 'type': 'investment',
                          'subtype': 'brokerage', 'balances': {'iso_currency_code': 'USD', 'current': 15500}}],
            'holdings': [
                {'account_id': 'broker', 'security_id': 'fund', 'quantity': '50.1256789', 'institution_price': '299.24782',
                 'institution_value': 15000, 'cost_basis': 10000, 'iso_currency_code': 'USD', 'institution_price_as_of': '2026-11-13',
                 'tax_lots': [{'cost_basis': 10000, 'acquired_date': '2015-01-01'}]},
                {'account_id': 'broker', 'security_id': 'cash', 'quantity': 500, 'institution_price': 1,
                 'institution_value': 500, 'cost_basis': None, 'iso_currency_code': 'USD'},
            ],
            'securities': [{'security_id': 'fund', 'name': 'Existing ETF', 'ticker_symbol': 'TEST', 'type': 'etf'},
                           {'security_id': 'cash', 'name': 'US Dollar', 'type': 'cash', 'is_cash_equivalent': True}]}


class InvestmentTests(TestCase):
    def setUp(self):
        self.folder = TemporaryDirectory()
        self.database = Path(self.folder.name) / 'data/finance.db'
        self.calls = []
        self.holdings = positions()
        self.failure = None
        self.transactions = [{'investment_transaction_id': 'buy-1', 'account_id': 'broker',
                              'date': date.today().isoformat(), 'name': 'Buy ETF', 'type': 'buy', 'security_id': 'fund',
                              'amount': 600, 'quantity': 2, 'price': 300, 'fees': 0, 'iso_currency_code': 'USD'}]

        def provider(endpoint, **payload):
            self.calls.append((endpoint, payload))
            if self.failure:
                raise PlaidError(self.failure)
            if endpoint == '/investments/holdings/get':
                return self.holdings
            if endpoint == '/investments/transactions/get':
                offset = payload['options']['offset']
                return {'investment_transactions': self.transactions[offset:offset + 500],
                        'total_investment_transactions': len(self.transactions), 'securities': self.holdings['securities']}
            raise AssertionError('Unexpected endpoint: ' + endpoint)

        self.patches = [patch('finance.plaid_sandbox.credentials', return_value=('synthetic-client', 'synthetic-secret')),
                        patch('finance.api.sandbox_router', lambda db: sandbox_router(db, client_factory=lambda keys: provider))]
        for item in self.patches:
            item.start()
        self.client = TestClient(create_app(self.database), base_url='http://127.0.0.1', headers={'x-finance-request': '1'})
        self.client.__enter__()
        self.client.get('/api/plaid/sandbox/preview?month=2026-11')
        self.path = self.database.with_suffix('.plaid-sandbox.db')
        with connect(self.path, write=True) as db:
            db.execute('INSERT INTO connection(id,item_id,token,cursor) VALUES (1,?,?,?)', ('original-item', protect(b'synthetic-access', True), 'bank-cursor'))

    def tearDown(self):
        self.client.__exit__(None, None, None)
        for item in reversed(self.patches):
            item.stop()
        self.folder.cleanup()

    def sync(self, activity=False, full=False):
        return self.client.post('/api/plaid/sandbox/investments/sync', params={'activity': activity, 'full': full})

    def clear_cooldown(self):
        with connect(self.path, write=True) as db:
            db.execute("UPDATE investment_state SET holdings_at='2000-01-01T00:00:00+00:00',activity_at='2000-01-01T00:00:00+00:00'")

    def test_existing_portfolio_cash_basis_precision_and_no_spending_or_duplicate_item(self):
        self.assertEqual(self.sync().status_code, 200)
        result = self.client.get('/api/plaid/sandbox/investments').json()
        fund = next(row for row in result['holdings'] if row['security_id'] == 'fund')
        self.assertEqual(fund['gain_minor'], 500000)
        self.assertEqual(fund['quantity'], '50.1256789')
        self.assertEqual(fund['price'], '299.24782')
        self.assertEqual(fund['provider']['tax_lots'][0]['acquired_date'], '2015-01-01')
        self.assertEqual(result['history'][0]['accounts'][0]['value_minor'], 1550000)
        self.assertEqual(len(result['holdings']), 2)
        self.assertTrue(next(row for row in result['holdings'] if row['security_id'] == 'cash')['cash_equivalent'])
        preview = self.client.get('/api/plaid/sandbox/preview?month=2026-11').json()
        self.assertEqual(preview['accounts'][0]['balance_minor'], 1550000)
        self.assertEqual(preview['transactions'], [])
        self.assertEqual(self.sync(True).status_code, 200)
        activity = self.client.get('/api/plaid/sandbox/investments/activity').json()
        self.assertEqual(activity['rows'][0]['amount_minor'], 60000)
        self.assertEqual(activity['rows'][0]['ticker'], 'TEST')
        with connect(self.path) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM transactions').fetchone()[0], 0)
            self.assertEqual(tuple(db.execute('SELECT item_id,cursor FROM connection').fetchone()), ('original-item', 'bank-cursor'))
        self.assertEqual([call[0] for call in self.calls], ['/investments/holdings/get', '/investments/transactions/get'])

    def test_null_unknown_currency_and_sold_positions(self):
        self.holdings['holdings'][0].update(iso_currency_code=None, unofficial_currency_code='TESTCOIN', cost_basis=None)
        self.assertEqual(self.sync().status_code, 200)
        rows = self.client.get('/api/plaid/sandbox/investments').json()['holdings']
        fund = next(row for row in rows if row['security_id'] == 'fund')
        self.assertIsNone(fund['value_minor'])
        self.assertIsNone(fund['gain_minor'])
        self.assertEqual(fund['currency'], 'TESTCOIN')
        self.clear_cooldown()
        self.holdings['holdings'] = self.holdings['holdings'][1:]
        self.assertEqual(self.sync().status_code, 200)
        self.assertEqual(len(self.client.get('/api/plaid/sandbox/investments').json()['holdings']), 1)

    def test_pagination_filters_replay_and_corrected_removal(self):
        self.sync()
        self.transactions = [{**self.transactions[0], 'investment_transaction_id': f'buy-{index}'} for index in range(501)]
        self.assertEqual(self.sync(True).status_code, 200)
        calls = [payload for path, payload in self.calls if path.endswith('/transactions/get')]
        self.assertEqual([call['options']['offset'] for call in calls], [0, 500])
        self.assertEqual(calls[0]['start_date'], history_start(date.today()).isoformat())
        result = self.client.get('/api/plaid/sandbox/investments/activity?account=broker&search=TEST&kind=buy&page=2').json()
        self.assertEqual(result['total'], 501)
        self.assertEqual(len(result['rows']), 25)
        self.assertEqual(self.client.get('/api/plaid/sandbox/investments/activity?account=other').json()['total'], 0)
        self.clear_cooldown()
        self.transactions = [{**self.transactions[0], 'amount': 700}]
        self.assertEqual(self.sync(True).status_code, 200)
        result = self.client.get('/api/plaid/sandbox/investments/activity').json()
        self.assertEqual(result['total'], 1)
        self.assertEqual(result['rows'][0]['amount_minor'], 70000)

    def test_failures_retain_saved_data_and_refresh_requires_explicit_action(self):
        self.sync()
        self.assertEqual(self.sync().status_code, 429)
        self.clear_cooldown()
        for code in ['PRODUCT_NOT_READY', 'ADDITIONAL_CONSENT_REQUIRED', 'PRODUCT_NOT_SUPPORTED']:
            self.failure = code
            self.assertEqual(self.sync().status_code, 409)
            self.assertEqual(len(self.client.get('/api/plaid/sandbox/investments').json()['holdings']), 2)
        self.failure = 'ITEM_LOGIN_REQUIRED'
        self.assertEqual(self.sync().status_code, 409)
        with connect(self.path) as db:
            self.assertEqual(db.execute('SELECT needs_reauth FROM connection').fetchone()[0], 1)
        before = len(self.calls)
        self.assertEqual(self.sync().status_code, 409)
        self.assertEqual(len(self.calls), before)

    def test_invalid_pagination_does_not_commit_partial_activity(self):
        self.sync()
        self.sync(True)
        self.clear_cooldown()
        self.transactions *= 501
        result = self.sync(True)
        self.assertEqual(result.status_code, 409)
        self.assertEqual(self.client.get('/api/plaid/sandbox/investments/activity').json()['total'], 1)

    def test_cancelled_activity_and_same_response_security_join(self):
        self.sync()
        self.sync(True)
        with connect(self.path, write=True) as db:
            save_activity(db, [({'investment_transaction_id': 'cancel', 'account_id': 'broker', 'date': date.today().isoformat(),
                                'type': 'cancel', 'cancel_transaction_id': 'buy-1'}, {'name': 'From this response'})],
                          date.today().isoformat(), date.today().isoformat(), '2026-11-14T00:00:00+00:00')
        result = self.client.get('/api/plaid/sandbox/investments/activity').json()
        self.assertEqual(result['total'], 1)
        self.assertEqual(result['rows'][0]['security_name'], 'From this response')
        self.assertIsNone(result['rows'][0]['amount_minor'])

    def test_snapshot_backup_restore_and_schema5_migration(self):
        self.sync()
        self.sync(True)
        store = BackupStore(self.database)
        saved = self.client.post('/api/backups', json={'workspace': 'sandbox'}).json()
        with connect(self.path, write=True) as db:
            db.execute('DELETE FROM investment_holdings')
        preview = self.client.get('/api/backups/preview', params={'id': saved['id']}).json()
        result = self.client.post('/api/backups/restore', json={key: preview[key] for key in ['id', 'preview_token', 'confirmation']})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(len(self.client.get('/api/plaid/sandbox/investments').json()['holdings']), 2)
        with connect(self.path, write=True) as db:
            for table in ['investment_holdings', 'investment_activity', 'investment_state', 'investment_observations']:
                db.execute('DROP TABLE ' + table)
            db.execute('PRAGMA user_version=5')
        self.assertEqual(self.client.get('/api/plaid/sandbox/investments').status_code, 200)
        with connect(self.path) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 6)
            self.assertEqual(db.execute('SELECT cursor FROM connection').fetchone()[0], 'bank-cursor')

    def test_date_validation_and_leap_year(self):
        self.assertEqual(history_start(date(2024, 2, 29)), date(2022, 2, 28))
        self.assertEqual(self.client.get('/api/plaid/sandbox/investments/activity?start=2026-11-02&end=2026-11-01').status_code, 422)
        self.assertEqual(self.client.get('/api/plaid/sandbox/investments/activity?page=0').status_code, 422)
        with TestClient(create_app(self.database), base_url='http://127.0.0.1') as untrusted:
            self.assertEqual(untrusted.post('/api/plaid/sandbox/investments/sync').status_code, 403)
