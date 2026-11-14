from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from fastapi.testclient import TestClient
from finance.api import create_app
from finance.db import connect
from finance.plaid_sandbox import sandbox_router
from finance.plaid_vault import protect
from finance.scenarios import sandbox_path
from tests.test_investments import positions


class ScenarioTests(TestCase):
    def setUp(self):
        self.folder = TemporaryDirectory()
        self.database = Path(self.folder.name) / 'data/finance.db'
        self.calls = []

        def provider(endpoint, **payload):
            self.calls.append((endpoint, payload))
            if endpoint == '/link/token/create':
                return {'link_token': 'synthetic-link', 'expiration': '2099-01-01T00:00:00+00:00', 'hosted_link_url': 'https://example.test/link'}
            if endpoint == '/investments/holdings/get':
                return positions()
            if endpoint == '/investments/transactions/get':
                return {'investment_transactions': [{'investment_transaction_id': 'synthetic-buy', 'account_id': 'broker',
                         'date': date.today().isoformat(), 'type': 'buy', 'amount': 100, 'quantity': 1,
                         'security_id': 'fund', 'iso_currency_code': 'USD'}],
                        'securities': positions()['securities'], 'total_investment_transactions': 1}
            raise AssertionError('Unexpected endpoint ' + endpoint)

        self.patches = [patch('finance.plaid_sandbox.credentials', return_value=('synthetic-client', 'synthetic-secret')),
                        patch('finance.api.sandbox_router', lambda db: sandbox_router(db, client_factory=lambda _: provider)),
                        patch('finance.api.investment_sandbox_router', lambda db: sandbox_router(db, client_factory=lambda _: provider, scenario='investments'))]
        for item in self.patches:
            item.start()
        self.client = self.start()
        for scenario, prefix in [('banking', 'sandbox'), ('investments', 'investments-sandbox')]:
            self.client.get('/api/plaid/' + prefix + '/preview?month=2026-11')
            with connect(sandbox_path(self.database, scenario), write=True) as db:
                db.execute('INSERT INTO connection(id,item_id,token,cursor) VALUES (1,?,?,?)',
                           ('item-' + scenario, protect(('synthetic-' + scenario).encode(), True), 'cursor-' + scenario))

    def start(self):
        client = TestClient(create_app(self.database), base_url='http://127.0.0.1', headers={'x-finance-request': '1'})
        client.__enter__()
        return client

    def tearDown(self):
        self.client.__exit__(None, None, None)
        for item in reversed(self.patches):
            item.stop()
        self.folder.cleanup()

    def test_account_management_no_longer_requests_unsupported_product_consent(self):
        result = self.client.post('/api/plaid/sandbox/link?reconnect=true')
        self.assertEqual(result.status_code, 200)
        endpoint, payload = self.calls[-1]
        self.assertEqual(endpoint, '/link/token/create')
        self.assertNotIn('additional_consented_products', payload)
        self.assertNotIn('products', payload)
        self.assertEqual(payload['update'], {'account_selection_enabled': True})
        self.assertEqual(payload['access_token'], 'synthetic-banking')

    def test_investment_link_requires_investments_and_cannot_replace_saved_item(self):
        self.assertEqual(self.client.post('/api/plaid/investments-sandbox/link').status_code, 409)
        self.assertEqual(self.calls, [])
        with connect(sandbox_path(self.database, 'investments'), write=True) as db:
            db.execute('DELETE FROM connection')
        result = self.client.post('/api/plaid/investments-sandbox/link')
        self.assertEqual(result.status_code, 200)
        self.assertEqual(self.calls[-1][1]['products'], ['investments'])
        self.assertNotIn('transactions', self.calls[-1][1])
        self.assertEqual(self.client.post('/api/plaid/investments-sandbox/link').status_code, 409)

    def test_scenario_preference_survives_restart_and_bad_names_are_rejected(self):
        self.assertEqual(self.client.get('/api/sandbox-scenario').json()['scenario'], 'banking')
        self.assertEqual(self.client.put('/api/sandbox-scenario', json={'scenario': '../outside'}).status_code, 422)
        self.client.put('/api/sandbox-scenario', json={'scenario': 'investments'})
        self.client.__exit__(None, None, None)
        self.client = self.start()
        self.assertEqual(self.client.get('/api/sandbox-scenario').json()['scenario'], 'investments')
        with connect(sandbox_path(self.database)) as db:
            self.assertEqual(db.execute('SELECT cursor FROM connection').fetchone()[0], 'cursor-banking')

    def test_sync_isolation_and_connection_persistence(self):
        prefix = '/api/plaid/investments-sandbox/investments'
        self.assertEqual(self.client.post(prefix + '/sync').status_code, 200)
        self.assertEqual(self.client.post(prefix + '/sync?activity=true').status_code, 200)
        self.assertEqual(self.client.get('/api/plaid/sandbox/investments').json()['holdings'], [])
        self.assertEqual(self.client.get(prefix + '/activity').json()['total'], 1)
        self.assertEqual(self.client.post('/api/plaid/investments-sandbox/sync').status_code, 409)
        self.client.__exit__(None, None, None)
        self.client = self.start()
        self.assertEqual(len(self.client.get(prefix).json()['holdings']), 2)
        with connect(sandbox_path(self.database, 'investments')) as db:
            self.assertEqual(tuple(db.execute('SELECT item_id,cursor FROM connection').fetchone()), ('item-investments', 'cursor-investments'))
        self.assertEqual(self.client.get('/api/overview?month=2026-11').json()['accounts'], [])

    def test_chart_preferences_are_separate(self):
        for mode, ids in [('sandbox', [1]), ('sandbox_investments', [2]), ('personal', [3])]:
            self.assertEqual(self.client.put('/api/chart-selection/' + mode, json={'excluded_account_ids': ids}).status_code, 200)
        for mode, ids in [('sandbox', [1]), ('sandbox_investments', [2]), ('personal', [3])]:
            self.assertEqual(self.client.get('/api/chart-selection/' + mode).json()['excluded_account_ids'], ids)

    def test_investment_backup_restore_targets_its_scenario_even_when_banking_selected(self):
        prefix = '/api/plaid/investments-sandbox/investments'
        self.client.post(prefix + '/sync')
        self.client.post(prefix + '/sync?activity=true')
        self.client.put('/api/chart-selection/sandbox_investments', json={'excluded_account_ids': [1]})
        saved = self.client.post('/api/backups', json={'workspace': 'sandbox_investments'})
        self.assertEqual(saved.status_code, 201, saved.text)
        self.assertEqual(saved.json()['workspace'], 'sandbox_investments')
        with connect(sandbox_path(self.database, 'investments'), write=True) as db:
            db.execute('DELETE FROM investment_holdings')
            db.execute('DELETE FROM investment_activity')
        self.client.put('/api/chart-selection/sandbox_investments', json={'excluded_account_ids': []})
        preview = self.client.get('/api/backups/preview', params={'id': saved.json()['id']})
        self.assertEqual(preview.status_code, 200, preview.text)
        data = preview.json()
        self.assertEqual(data['workspace'], 'sandbox_investments')
        self.assertEqual(data['confirmation'], 'RESTORE SANDBOX_INVESTMENTS')
        self.assertEqual(self.client.post('/api/backups/restore', json={key: data[key] for key in ['id', 'preview_token', 'confirmation']}).status_code, 200)
        self.assertEqual(len(self.client.get(prefix).json()['holdings']), 2)
        self.assertEqual(self.client.get(prefix + '/activity').json()['total'], 1)
        self.assertEqual(self.client.get('/api/chart-selection/sandbox_investments').json()['excluded_account_ids'], [1])
        with connect(sandbox_path(self.database)) as db:
            self.assertEqual(tuple(db.execute('SELECT item_id,cursor FROM connection').fetchone()), ('item-banking', 'cursor-banking'))
            self.assertEqual(db.execute('SELECT COUNT(*) FROM accounts').fetchone()[0], 0)

    def test_pending_session_blocks_only_its_own_scenario_restore(self):
        saved = self.client.post('/api/backups', json={'workspace': 'sandbox_investments'}).json()
        self.client.post('/api/plaid/sandbox/link?reconnect=true')
        self.assertEqual(self.client.get('/api/backups/preview', params={'id': saved['id']}).status_code, 200)
        self.client.post('/api/plaid/investments-sandbox/link?reconnect=true')
        self.assertEqual(self.client.get('/api/backups/preview', params={'id': saved['id']}).status_code, 422)
