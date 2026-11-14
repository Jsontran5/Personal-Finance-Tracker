from datetime import date
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from fastapi.testclient import TestClient
from finance.api import create_app
from finance.db import connect
from finance.plaid_sandbox import PlaidError, sandbox_router
from finance.plaid_vault import protect


def liability_fixture():
    accounts = [{'account_id': identifier, 'name': name, 'type': kind, 'subtype': subtype,
                 'balances': {'current': 1000, 'iso_currency_code': 'USD', 'limit': 5000 if kind == 'credit' else None,
                              'available': 4000 if kind == 'credit' else None}}
                for identifier, name, kind, subtype in [('credit', 'Test credit card', 'credit', 'credit card'),
                    ('student', 'Test student loan', 'loan', 'student'), ('mortgage', 'Test mortgage', 'loan', 'mortgage'),
                    ('auto', 'Test auto loan', 'loan', 'auto'), ('line', 'Test credit line', 'loan', 'line of credit')]]
    return {'accounts': accounts, 'liabilities': {
        'credit': [{'account_id': 'credit', 'aprs': [{'apr_percentage': 0, 'apr_type': 'special', 'balance_subject_to_apr': 1000, 'interest_charge_amount': 0},
                    {'apr_percentage': 24.99, 'apr_type': 'purchase_apr', 'balance_subject_to_apr': None, 'interest_charge_amount': None}],
                    'minimum_payment_amount': 25, 'last_statement_balance': 1100, 'is_overdue': False}],
        'student': [{'account_id': 'student', 'account_number': 'synthetic-unused-identifier', 'payment_reference_number': 'synthetic-unused-reference',
                     'loan_name': 'Subsidized loan', 'guarantor': 'Department of Education', 'interest_rate_percentage': Decimal('5.1250'),
                     'origination_principal_amount': 5000, 'outstanding_interest_amount': 12.34,
                     'loan_status': {'type': 'repayment'}, 'repayment_plan': {'description': 'Standard'}, 'future_field': {'keep': True}}],
        'mortgage': [{'account_id': 'mortgage', 'interest_rate': {'percentage': 6.25, 'type': 'fixed'}, 'origination_principal_amount': 200000}],
        'loan': [{'account_id': 'auto', 'interest_rate': {'percentage': 4.5, 'type': 'fixed'}, 'principal_balance': 1000, 'loan_term_months': 60}],
        'line_of_credit': [{'account_id': 'line', 'interest_rate': {'percentage': 8.25, 'type': 'variable'}, 'credit_limit': 10000, 'available_credit': 9000}]
    }}


class DebtTests(TestCase):
    def setUp(self):
        self.folder = TemporaryDirectory()
        self.database = Path(self.folder.name) / 'data/finance.db'
        self.response = liability_fixture()
        self.failure = None
        self.calls = []

        def provider(endpoint, **payload):
            self.calls.append((endpoint, payload))
            if self.failure:
                raise PlaidError(self.failure)
            self.assertEqual(endpoint, '/liabilities/get')
            return self.response

        self.patches = [patch('finance.plaid_sandbox.credentials', return_value=('synthetic-client', 'synthetic-secret')),
                        patch('finance.api.sandbox_router', lambda db: sandbox_router(db, client_factory=lambda keys: provider))]
        for item in self.patches:
            item.start()
        self.client = self.open()

    def open(self):
        client = TestClient(create_app(self.database), base_url='http://127.0.0.1', headers={'x-finance-request': '1'})
        client.__enter__()
        return client

    def tearDown(self):
        self.client.__exit__(None, None, None)
        for item in reversed(self.patches):
            item.stop()
        self.folder.cleanup()

    def account(self, name='Nelnet — Group AA', kind='loan', balance=2000000):
        result = self.client.post('/api/accounts', json={'name': name, 'kind': kind, 'currency': 'USD',
            'opening_balance_minor': balance, 'date': date.today().isoformat(), **({'debt_subtype': 'student'} if kind == 'loan' else {})})
        self.assertEqual(result.status_code, 201, result.text)
        return result.json()['id']

    def snapshot(self):
        return self.client.get('/api/overview?month=' + date.today().isoformat()[:7]).json()

    def prepare_provider(self):
        self.client.get('/api/plaid/sandbox/preview?month=' + date.today().isoformat()[:7])
        with connect(self.database.with_suffix('.plaid-sandbox.db'), write=True) as db:
            db.execute('INSERT INTO connection(id,item_id,token,cursor) VALUES (1,?,?,?)',
                       ('saved-item', protect(b'synthetic-access', True), 'saved-bank-cursor'))

    def sync(self):
        result = self.client.post('/api/plaid/sandbox/liabilities/sync')
        self.assertEqual(result.status_code, 200, result.text)
        return result

    def provider_details(self, identifier):
        with connect(self.database.with_suffix('.plaid-sandbox.db')) as db:
            rowid = db.execute('SELECT rowid FROM accounts WHERE id=?', (identifier,)).fetchone()[0]
        result = self.client.get('/api/plaid/sandbox/debt/' + str(rowid))
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()

    def test_manual_nelnet_groups_exact_rates_and_no_plaid_required(self):
        first, second = self.account(), self.account('Nelnet — Group AB', balance=500000)
        before = self.snapshot()
        fields = {'subtype': 'student', 'servicer': 'Nelnet', 'apr_percent': '5.1250', 'rate_type': 'fixed',
                  'original_principal_minor': 2500000, 'principal_balance_minor': 1900000, 'outstanding_interest_minor': 100000,
                  'minimum_payment_minor': 0, 'loan_status': 'Repayment', 'repayment_plan': 'Standard', 'notes': 'Group AA notes'}
        self.assertEqual(self.client.put(f'/api/accounts/{first}/debt', json=fields).status_code, 200)
        self.client.put(f'/api/accounts/{second}/debt', json={**fields, 'apr_percent': '3.5'})
        details = self.client.get(f'/api/accounts/{first}/debt').json()
        self.assertEqual(details['fields']['apr_percent'], '5.1250')
        self.assertEqual(details['fields']['minimum_payment_minor'], 0)
        self.assertEqual(self.snapshot()['net_worth_minor'], before['net_worth_minor'])
        self.assertEqual(self.snapshot()['transactions'], [])
        self.assertEqual(self.snapshot()['accounts'][0]['subtype'], 'student')
        self.assertEqual(self.calls, [])
        self.client.__exit__(None, None, None); self.client = self.open()
        self.assertEqual(self.client.get(f'/api/accounts/{second}/debt').json()['fields']['apr_percent'], '3.5')
        self.assertEqual(self.client.get(f'/api/accounts/{first}/debt').json()['fields']['notes'], 'Group AA notes')

    def test_principal_transfer_and_interest_expense_change_balances_once(self):
        loan = self.account()
        checking = self.account('Checking', 'checking', 100000)
        before = self.snapshot()['net_worth_minor']
        for kind, amount, destination in [('transfer', 8000, loan), ('expense', 2000, None)]:
            result = self.client.post('/api/transactions', json={'account_id': checking, 'destination_id': destination,
                'kind': kind, 'amount_minor': amount, 'date': date.today().isoformat(), 'merchant': 'Nelnet payment'})
            self.assertEqual(result.status_code, 201, result.text)
        after = self.snapshot()
        self.assertEqual(after['net_worth_minor'], before - 2000)
        self.assertEqual(after['spending_minor'], 2000)
        self.assertEqual(next(row['balance_minor'] for row in after['accounts'] if row['id'] == loan), -1992000)

    def test_manual_validation_kind_dates_clearing_and_missing_values(self):
        loan, credit = self.account(), self.account('Card', 'credit', 10000)
        for fields in [{'apr_percent': -1}, {'apr_percent': 'NaN'}, {'principal_balance_minor': -1}, {'minimum_payment_minor': True},
                       {'origination_date': '2020-01-01', 'maturity_date': '2019-01-01'}, {'account_number': 'unused'}]:
            self.assertEqual(self.client.put(f'/api/accounts/{loan}/debt', json={'subtype': 'student', **fields}).status_code, 422)
        self.assertEqual(self.client.put(f'/api/accounts/{credit}/debt', json={'subtype': 'student'}).status_code, 422)
        self.client.put(f'/api/accounts/{loan}/debt', json={'subtype': 'student', 'servicer': 'Nelnet', 'apr_percent': '0'})
        self.client.put(f'/api/accounts/{loan}/debt', json={'subtype': 'student', 'apr_percent': None})
        self.assertIsNone(self.client.get(f'/api/accounts/{loan}/debt').json()['fields']['apr_percent'])
        self.assertEqual(self.client.get(f'/api/accounts/{credit}/debt').json()['fields']['subtype'], 'credit card')
        cash = self.account('Cash', 'cash', 0)
        self.assertEqual(self.client.get(f'/api/accounts/{cash}/debt').status_code, 422)

    def test_manual_backup_restore_keeps_terms_and_balances_separate(self):
        loan = self.account()
        self.client.put(f'/api/accounts/{loan}/debt', json={'subtype': 'student', 'servicer': 'Nelnet', 'apr_percent': '4.5'})
        saved = self.client.post('/api/backups', json={'workspace': 'personal'}).json()
        self.client.put(f'/api/accounts/{loan}/debt', json={'subtype': 'student', 'apr_percent': '9'})
        preview = self.client.get('/api/backups/preview', params={'id': saved['id']}).json()
        self.assertEqual(preview['counts']['debt_details'], 1)
        restored = self.client.post('/api/backups/restore', json={key: preview[key] for key in ['id', 'preview_token', 'confirmation']})
        self.assertEqual(restored.status_code, 200, restored.text)
        self.assertEqual(self.client.get(f'/api/accounts/{loan}/debt').json()['fields']['apr_percent'], '4.5')
        self.assertEqual(self.snapshot()['accounts'][0]['balance_minor'], -2000000)

    def test_provider_five_categories_rates_nulls_and_exact_source_fields(self):
        self.prepare_provider()
        self.assertEqual(self.sync().json()['count'], 5)
        card = self.provider_details('credit')
        self.assertEqual(card['fields']['credit_limit_minor'], 500000)
        self.assertEqual(card['fields']['available_credit_minor'], 400000)
        self.assertEqual(card['aprs'][0]['percent'], 0)
        self.assertEqual(card['aprs'][0]['interest_minor'], 0)
        self.assertIsNone(card['aprs'][1]['interest_minor'])
        student = self.provider_details('student')
        self.assertEqual(student['fields']['apr_percent'], '5.1250')
        self.assertEqual(student['fields']['outstanding_interest_minor'], 1234)
        self.assertEqual(student['fields']['servicer'], '')  # Loan name/guarantor aren't a servicer.
        self.assertNotIn('account_number', student['provider'])
        self.assertNotIn('payment_reference_number', student['provider'])
        self.assertTrue(student['provider']['future_field']['keep'])
        self.assertEqual(self.provider_details('mortgage')['fields']['apr_percent'], 6.25)
        self.assertEqual(self.provider_details('auto')['provider']['loan_term_months'], 60)
        self.assertEqual(self.provider_details('line')['fields']['credit_limit_minor'], 1000000)
        self.assertEqual(len(self.calls), 1)
        with connect(self.database.with_suffix('.plaid-sandbox.db')) as db:
            self.assertEqual(tuple(db.execute('SELECT item_id,cursor FROM connection').fetchone()), ('saved-item', 'saved-bank-cursor'))
            self.assertEqual(db.execute('SELECT COUNT(*) FROM transactions').fetchone()[0], 0)

    def test_provider_replay_removal_failure_and_cooldown(self):
        self.prepare_provider(); self.sync()
        self.assertEqual(self.client.post('/api/plaid/sandbox/liabilities/sync').status_code, 429)
        with connect(self.database.with_suffix('.plaid-sandbox.db'), write=True) as db:
            db.execute("UPDATE liability_state SET synced_at='2000-01-01T00:00:00+00:00'")
        self.failure = 'PRODUCTS_NOT_SUPPORTED'
        self.assertEqual(self.client.post('/api/plaid/sandbox/liabilities/sync').status_code, 409)
        self.assertIsNotNone(self.provider_details('student')['fields'])
        self.failure = None
        self.response['liabilities']['student'] = []
        self.sync()
        self.assertIsNone(self.provider_details('student')['fields'])
        self.assertIsNotNone(self.provider_details('student')['last_sync'])

    def test_provider_failure_rolls_back_and_fresh_balance_is_preserved(self):
        self.prepare_provider(); self.sync()
        with connect(self.database.with_suffix('.plaid-sandbox.db'), write=True) as db:
            db.execute("UPDATE accounts SET source='fresh',current_minor=-12345 WHERE id='credit'")
            db.execute("UPDATE liability_state SET synced_at='2000-01-01T00:00:00+00:00'")
        self.response['liabilities']['student'][0]['account_id'] = 'missing'
        self.assertEqual(self.client.post('/api/plaid/sandbox/liabilities/sync').status_code, 409)
        self.assertIsNotNone(self.provider_details('student')['fields'])
        with connect(self.database.with_suffix('.plaid-sandbox.db')) as db:
            self.assertEqual(db.execute("SELECT current_minor FROM accounts WHERE id='credit'").fetchone()[0], -12345)
        self.response['liabilities']['student'][0]['account_id'] = 'student'
        self.sync()
        with connect(self.database.with_suffix('.plaid-sandbox.db')) as db:
            self.assertEqual(db.execute("SELECT current_minor FROM accounts WHERE id='credit'").fetchone()[0], -12345)

    def test_missing_currency_is_not_silently_valued_as_usd(self):
        self.prepare_provider()
        self.response['accounts'][0]['balances']['iso_currency_code'] = None
        self.sync()
        card = self.provider_details('credit')
        self.assertIsNone(card['currency'])
        self.assertIsNone(card['fields']['credit_limit_minor'])
        self.assertIsNone(card['aprs'][0]['interest_minor'])

    def test_sandbox_backup_restore_includes_liabilities(self):
        self.prepare_provider(); self.sync()
        saved = self.client.post('/api/backups', json={'workspace': 'sandbox'}).json()
        with connect(self.database.with_suffix('.plaid-sandbox.db'), write=True) as db:
            db.execute('DELETE FROM liability_details')
        preview = self.client.get('/api/backups/preview', params={'id': saved['id']}).json()
        self.assertEqual(preview['counts']['debt_details'], 5)
        restored = self.client.post('/api/backups/restore', json={key: preview[key] for key in ['id', 'preview_token', 'confirmation']})
        self.assertEqual(restored.status_code, 200, restored.text)
        self.assertEqual(self.provider_details('student')['fields']['apr_percent'], '5.1250')
