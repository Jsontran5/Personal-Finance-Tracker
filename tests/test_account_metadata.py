from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from fastapi.testclient import TestClient

from finance.api import create_app
from finance.db import connect
from finance.plaid_sandbox import SCHEMA, sandbox_router, save_accounts
from finance.plaid_vault import protect
from finance.sandbox_preview import initialize_preview, preview_accounts


class AccountMetadataTests(TestCase):
    def test_v2_migration_preserves_identity_and_balances(self):
        with TemporaryDirectory() as temporary:
            with connect(Path(temporary) / 'sandbox.db', write=True) as db:
                for statement in SCHEMA:
                    db.execute(statement)
                initialize_preview(db)
                save_accounts(db, [{
                    'account_id': 'savings', 'name': 'My savings', 'type': 'depository',
                    'subtype': 'savings', 'balances': {'iso_currency_code': 'USD', 'current': 123},
                }], 'fresh')
                before = db.execute('SELECT rowid,current_minor,checked_at FROM accounts').fetchone()
                db.execute('ALTER TABLE accounts DROP COLUMN subtype')
                db.execute('PRAGMA user_version=2')
                initialize_preview(db)
                initialize_preview(db)
                after = db.execute('SELECT rowid,current_minor,checked_at FROM accounts').fetchone()
                self.assertEqual(tuple(before), tuple(after))
                self.assertEqual(preview_accounts(db)[0]['kind'], 'depository')
                self.assertIsNone(preview_accounts(db)[0]['subtype'])
                self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 5)

    def test_normal_sync_updates_metadata_without_replacing_fresh_balance(self):
        calls = []

        def provider(endpoint, **payload):
            calls.append(endpoint)
            if endpoint == '/transactions/sync':
                self.assertEqual(payload['cursor'], 'saved-cursor')
                return {'next_cursor': 'next', 'has_more': False, 'added': [], 'modified': [], 'removed': []}
            self.assertEqual(endpoint, '/accounts/get')
            return {'accounts': [{
                'account_id': 'card', 'name': 'Updated name', 'type': 'credit',
                'subtype': 'credit card', 'balances': {'iso_currency_code': 'USD', 'current': 999},
            }]}

        with TemporaryDirectory() as temporary:
            database = Path(temporary) / 'data' / 'finance.db'
            with patch('finance.plaid_sandbox.credentials', return_value=('test-client', 'test-secret')), patch(
                'finance.api.sandbox_router', lambda db: sandbox_router(db, client_factory=lambda keys: provider),
            ):
                with TestClient(create_app(database), base_url='http://127.0.0.1', headers={'x-finance-request': '1'}) as client:
                    client.get('/api/plaid/sandbox/preview?month=2026-11')
                    with connect(database.with_suffix('.plaid-sandbox.db'), write=True) as db:
                        save_accounts(db, [{
                            'account_id': 'card', 'name': 'Original', 'type': 'credit',
                            'balances': {'iso_currency_code': 'USD', 'current': 20},
                        }], 'fresh')
                        db.execute('INSERT INTO connection(id,item_id,token,cursor) VALUES (1,?,?,?)',
                                   ('same-item', protect(b'synthetic-access', True), 'saved-cursor'))
                    response = client.post('/api/plaid/sandbox/sync')
                    self.assertEqual(response.status_code, 200)
                    data = client.get('/api/plaid/sandbox/preview?month=2026-11').json()
                    account = data['accounts'][0]
                    self.assertEqual(account['name'], 'Updated name')
                    self.assertEqual(account['subtype'], 'credit card')
                    self.assertEqual(account['balance_minor'], -2000)
                    self.assertEqual(account['balance_source'], 'fresh')
                    with connect(database.with_suffix('.plaid-sandbox.db')) as db:
                        self.assertEqual(db.execute('SELECT item_id,cursor FROM connection').fetchone()[:], ('same-item', 'next'))
                    self.assertEqual(calls, ['/transactions/sync', '/accounts/get'])
