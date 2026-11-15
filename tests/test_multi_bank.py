from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from fastapi.testclient import TestClient
from finance.api import create_app
from finance.db import connect
from finance.multi_bank import combined_router
from finance.plaid_sandbox import SCHEMA, PlaidError, save_accounts, save_transactions
from finance.plaid_vault import protect
from finance.sandbox_preview import initialize_preview
from finance.investments import save_holdings
from finance.scenarios import sandbox_path


class MultiBankTests(TestCase):
    def setUp(self):
        self.folder = TemporaryDirectory()
        self.database = Path(self.folder.name) / 'data/finance.db'
        self.calls = []
        self.fail = None
        self.exchange_fail = False
        self.selected_institution = 'ins_new'

        def provider(endpoint, **payload):
            self.calls.append((endpoint, payload))
            if self.fail and payload.get('access_token') == 'one':
                raise PlaidError(self.fail)
            if endpoint == '/item/get':
                return {'item': {'institution_id': 'ins_' + payload['access_token']}}
            if endpoint == '/institutions/get_by_id':
                return {'institution': {'institution_id': payload['institution_id'], 'name': 'Institution ' + payload['institution_id'], 'products': ['transactions', 'investments']}}
            if endpoint == '/institutions/search':
                return {'institutions': [{'institution_id': 'ins_new', 'name': 'New bank'}]}
            if endpoint == '/link/token/create':
                return {'link_token': 'synthetic-link', 'expiration': '2099-01-01T00:00:00+00:00', 'hosted_link_url': 'https://example.test/link'}
            if endpoint == '/link/token/get':
                return {'link_sessions': [{'finished_at': '2026-11-14T00:00:00Z', 'results': {'item_add_results': [
                    {'public_token': 'synthetic-public', 'institution': {'institution_id': self.selected_institution}}]}, 'events': [{'event_name': 'HANDOFF'}]}]}
            if endpoint == '/item/public_token/exchange':
                if self.exchange_fail:
                    self.exchange_fail = False
                    raise PlaidError('NETWORK_ERROR')
                return {'item_id': 'item-new', 'access_token': 'synthetic-new'}
            if endpoint == '/transactions/sync':
                return {'added': [self.tx('same')], 'modified': [], 'removed': [], 'has_more': False, 'next_cursor': 'new-cursor'}
            if endpoint in {'/accounts/get', '/accounts/balance/get'}:
                return {'accounts': [self.account(payload['access_token'])]}
            if endpoint == '/investments/holdings/get':
                return {'accounts': [self.account('two')], 'holdings': [self.holding()], 'securities': [{'security_id': 'security', 'name': 'ETF', 'type': 'etf'}]}
            if endpoint == '/investments/transactions/get':
                return {'total_investment_transactions': 1, 'investment_transactions': [{'investment_transaction_id': 'same', 'account_id': 'a', 'date': date.today().isoformat(), 'type': 'buy', 'amount': 1, 'iso_currency_code': 'USD'}], 'securities': []}
            raise AssertionError(endpoint)
        self.provider = provider
        self.patches = [patch('finance.multi_bank.credentials', return_value=('synthetic-client', 'synthetic-secret')),
                        patch('finance.api.combined_router', lambda db: combined_router(db, client_factory=lambda keys: provider))]
        for item in self.patches:
            item.start()
        self.client = self.open()
        for scenario, token in [('banking', 'one'), ('investments', 'two')]:
            with connect(sandbox_path(self.database, scenario), write=True) as db:
                for statement in SCHEMA:
                    db.execute(statement)
                initialize_preview(db)
                db.execute('INSERT INTO connection(id,item_id,token,cursor) VALUES (1,?,?,?)', ('item-' + token, protect(token.encode(), True), 'cursor-' + token))
                save_accounts(db, [self.account(token)], 'cached')
                if scenario == 'banking':
                    save_transactions(db, [self.tx('existing')], [], [])
                else:
                    save_holdings(db, {'accounts': [self.account(token)], 'holdings': [self.holding()], 'securities': [{'security_id': 'security', 'name': 'ETF', 'type': 'etf'}]}, '2020-01-01T00:00:00+00:00')
        self.base = '/api/plaid/combined-sandbox'
        self.path = sandbox_path(self.database, 'combined')

    def open(self):
        client = TestClient(create_app(self.database), base_url='http://127.0.0.1', headers={'x-finance-request': '1'})
        client.__enter__()
        return client

    def tearDown(self):
        self.client.__exit__(None, None, None)
        for item in reversed(self.patches):
            item.stop()
        self.folder.cleanup()

    def account(self, token):
        return {'account_id': 'a', 'name': 'Same account name', 'type': 'investment' if token == 'two' else 'depository',
                'subtype': 'brokerage' if token == 'two' else 'checking', 'balances': {'current': 20 if token == 'two' else 10, 'iso_currency_code': 'USD'}}

    def holding(self):
        return {'account_id': 'a', 'security_id': 'security', 'quantity': 2, 'institution_value': 20, 'iso_currency_code': 'USD'}

    def tx(self, identifier):
        return {'transaction_id': identifier, 'account_id': 'a', 'date': date.today().isoformat(), 'name': 'Shop', 'amount': 1, 'iso_currency_code': 'USD', 'pending': False}

    def imported(self):
        result = self.client.post(self.base + '/import-scenarios')
        self.assertEqual(result.status_code, 200, result.text)
        return result

    def overview(self):
        result = self.client.get(self.base + '/preview?month=' + date.today().isoformat()[:7])
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()

    def test_import_two_saved_items_namespaces_and_idempotence(self):
        self.assertEqual(self.imported().json()['imported'], 2)
        data = self.overview()
        self.assertEqual(len(data['accounts']), 2)
        self.assertEqual(data['net_worth_minor'], 3000)
        self.assertNotEqual(data['accounts'][0]['id'], data['accounts'][1]['id'])
        self.assertEqual(self.imported().json()['imported'], 0)
        self.assertEqual(len(self.client.get(self.base).json()['connections']), 2)
        self.assertFalse(any(endpoint == '/item/public_token/exchange' for endpoint, _ in self.calls))
        with connect(sandbox_path(self.database)) as db:
            self.assertEqual(db.execute('SELECT cursor FROM connection').fetchone()[0], 'cursor-one')

    def test_per_item_sync_does_not_deactivate_peer_accounts_or_holdings(self):
        self.imported()
        self.assertEqual(self.client.post(self.base + '/connections/1/sync?product=transactions').status_code, 200)
        self.assertEqual(len(self.overview()['accounts']), 2)
        self.assertEqual(self.client.post(self.base + '/connections/2/sync?product=holdings').status_code, 200)
        self.assertEqual(self.client.post(self.base + '/connections/2/sync?product=activity').status_code, 200)
        self.assertEqual(len(self.client.get(self.base + '/investments').json()['holdings']), 1)
        with connect(self.path) as db:
            self.assertEqual(db.execute('SELECT cursor FROM multi_connections WHERE id=1').fetchone()[0], 'new-cursor')
            self.assertEqual(db.execute('SELECT cursor FROM multi_connections WHERE id=2').fetchone()[0], 'cursor-two')
            raw = db.execute("SELECT payload FROM transaction_details WHERE transaction_id='p1:same'").fetchone()[0]
            self.assertNotIn('p1:a', raw)

    def test_login_failure_isolated_and_saved_data_retained(self):
        self.imported(); self.fail = 'ITEM_LOGIN_REQUIRED'
        result = self.client.post(self.base + '/connections/1/sync?product=transactions')
        self.assertEqual(result.status_code, 409)
        rows = self.client.get(self.base).json()['connections']
        self.assertEqual([row['needs_reauth'] for row in rows], [1, 0])
        self.assertEqual(self.overview()['net_worth_minor'], 3000)
        self.assertEqual(self.client.post(self.base + '/connections/2/sync?product=holdings').status_code, 200)

    def test_duplicate_institution_blocked_before_link_and_wrong_metadata_before_exchange(self):
        self.imported()
        self.assertEqual(self.client.post(self.base + '/connections', json={'institution_id': 'ins_one'}).status_code, 409)
        added = self.client.post(self.base + '/connections', json={'institution_id': 'ins_new'})
        self.assertEqual(added.status_code, 201, added.text)
        identifier = added.json()['id']
        started = self.client.post(f'{self.base}/connections/{identifier}/link').json()
        self.selected_institution = 'ins_one'
        result = self.client.post(f'{self.base}/connections/{identifier}/link/{started["session"]}')
        self.assertEqual(result.status_code, 409, result.text)
        self.assertFalse(any(endpoint == '/item/public_token/exchange' for endpoint, _ in self.calls))

    def test_exchange_retry_after_restart_reuses_same_public_token(self):
        identifier = self.client.post(self.base + '/connections', json={'institution_id': 'ins_new'}).json()['id']
        started = self.client.post(f'{self.base}/connections/{identifier}/link').json()
        self.exchange_fail = True
        endpoint = f'{self.base}/connections/{identifier}/link/{started["session"]}'
        self.assertEqual(self.client.post(endpoint).status_code, 502)
        self.assertEqual(self.client.delete(f'{self.base}/connections/{identifier}/link').status_code, 409)
        self.client.__exit__(None, None, None); self.client = self.open()
        self.assertEqual(self.client.post(endpoint).status_code, 200)
        self.assertEqual(self.client.post(endpoint).status_code, 200)
        exchanges = [payload['public_token'] for name, payload in self.calls if name.endswith('/exchange')]
        self.assertEqual(exchanges, ['synthetic-public', 'synthetic-public'])
        self.assertEqual(sum(name == '/link/token/get' for name, _ in self.calls), 1)
        self.assertNotIn('synthetic-new', self.client.get(self.base).text)

    def test_reconnect_never_exchanges_or_replaces_item(self):
        self.imported()
        start = self.client.post(self.base + '/connections/1/link').json()
        self.assertEqual(self.client.post(self.base + '/connections/1/link/' + start['session']).status_code, 200)
        self.assertFalse(any(name.endswith('/exchange') for name, _ in self.calls))
        with connect(self.path) as db:
            self.assertEqual(tuple(db.execute('SELECT item_id,cursor FROM multi_connections WHERE id=1').fetchone()), ('item-one', 'cursor-one'))
        payload = next(payload for name, payload in self.calls if name == '/link/token/create')
        self.assertNotIn('products', payload)
        self.assertNotIn('additional_consented_products', payload)

    def test_selected_manual_loan_and_spending_share_view_without_mutating_personal(self):
        self.imported()
        created = self.client.post('/api/accounts', json={'name': 'Nelnet', 'kind': 'loan', 'debt_subtype': 'student', 'currency': 'USD', 'date': date.today().isoformat(), 'opening_balance_minor': 2500}).json()['id']
        self.client.put(f'/api/accounts/{created}/debt', json={'subtype': 'student', 'servicer': 'Nelnet', 'apr_percent': '5.1250'})
        self.assertEqual(self.client.put(self.base + '/manual', json={'account_ids': [created]}).status_code, 200)
        data = self.overview()
        self.assertEqual(data['net_worth_minor'], 500)
        self.assertEqual(len(data['accounts']), 3)
        manual_id = next(account['id'] for account in data['accounts'] if account['name'] == 'Nelnet')
        self.assertEqual(self.client.get(f'{self.base}/debt/{manual_id}').json()['fields']['apr_percent'], '5.1250')
        self.client.post(f'/api/accounts/{created}/balances', json={'balance_minor': 2000, 'date': date.today().isoformat()})
        self.assertEqual(self.overview()['net_worth_minor'], 1000)
        self.assertEqual(self.client.put(self.base + '/manual', json={'account_ids': []}).status_code, 200)
        self.assertEqual(self.overview()['net_worth_minor'], 3000)
        self.assertEqual(len(self.client.get('/api/overview?month=' + date.today().isoformat()[:7]).json()['accounts']), 1)

    def test_combined_backup_restore_keeps_authorizations_and_other_workspaces(self):
        self.imported()
        saved = self.client.post('/api/backups', json={'workspace': 'sandbox_combined'})
        self.assertEqual(saved.status_code, 201, saved.text)
        with connect(self.path, write=True) as db:
            db.execute("UPDATE accounts SET current_minor=9999 WHERE id='p1:a'")
        preview = self.client.get('/api/backups/preview', params={'id': saved.json()['id']})
        self.assertEqual(preview.status_code, 200, preview.text)
        checked = preview.json()
        result = self.client.post('/api/backups/restore', json={key: checked[key] for key in ['id', 'preview_token', 'confirmation']})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(self.overview()['net_worth_minor'], 3000)
        with connect(self.path) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM multi_connections WHERE token IS NOT NULL').fetchone()[0], 2)

    def test_pending_link_blocks_combined_restore(self):
        self.imported()
        saved = self.client.post('/api/backups', json={'workspace': 'sandbox_combined'}).json()
        self.client.post(self.base + '/connections/1/link')
        self.assertEqual(self.client.get('/api/backups/preview', params={'id': saved['id']}).status_code, 422)
