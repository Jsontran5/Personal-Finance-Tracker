from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
import json
import zipfile

from fastapi.testclient import TestClient
from finance.api import create_app
from finance.db import connect
from finance.plaid_vault import protect
from finance.recovery import BackupStore


class RecoveryTests(TestCase):
    def setUp(self):
        self.folder = TemporaryDirectory()
        self.database = Path(self.folder.name) / 'data/finance.db'
        self.client = TestClient(create_app(self.database), base_url='http://127.0.0.1', headers={'x-finance-request': '1'})
        self.client.__enter__()
        self.client.get('/api/plaid/sandbox/preview?month=2026-11')
        self.store = BackupStore(self.database)

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.folder.cleanup()

    def account(self, name):
        response = self.client.post('/api/accounts', json={'name': name, 'kind': 'checking', 'currency': 'USD', 'date': '2026-01-01', 'opening_balance_minor': 10000})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()['id']

    def backup(self, mode='personal'):
        result = self.client.post('/api/backups', json={'workspace': mode})
        self.assertEqual(result.status_code, 201, result.text)
        return result.json()['id']

    def preview(self, identifier):
        result = self.client.get('/api/backups/preview', params={'id': identifier})
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()

    def restore(self, preview):
        return self.client.post('/api/backups/restore', json={key: preview[key] for key in ['id', 'preview_token', 'confirmation']})

    def test_restore_roundtrip_restores_selection_and_creates_recoverable_current_copy(self):
        self.account('Original')
        self.client.put('/api/chart-selection/personal', json={'excluded_account_ids': [1]})
        identifier = self.backup()
        self.account('Later')
        self.client.put('/api/chart-selection/personal', json={'excluded_account_ids': []})
        preview = self.preview(identifier)
        self.assertEqual(preview['counts']['accounts'], 1)
        self.assertEqual(preview['current_counts']['accounts'], 2)
        result = self.restore(preview)
        self.assertEqual(result.status_code, 200, result.text)
        data = self.client.get('/api/overview?month=2026-11').json()
        self.assertEqual([row['name'] for row in data['accounts']], ['Original'])
        self.assertEqual(self.client.get('/api/chart-selection/personal').json()['excluded_account_ids'], [1])
        recovery = self.preview(result.json()['recovery_backup'])
        self.assertEqual(recovery['counts']['accounts'], 2)
        self.assertEqual(self.restore(recovery).status_code, 200)
        self.assertEqual(len(self.client.get('/api/overview?month=2026-11').json()['accounts']), 2)

    def test_stale_live_data_and_wrong_confirmation_never_restore(self):
        identifier = self.backup()
        preview = self.preview(identifier)
        self.account('Newer')
        self.assertEqual(self.restore(preview).status_code, 422)
        preview = self.preview(identifier)
        preview['confirmation'] = 'RESTORE SANDBOX'
        self.assertEqual(self.restore(preview).status_code, 422)
        self.assertEqual(len(self.client.get('/api/overview?month=2026-11').json()['accounts']), 1)

    def test_sandbox_keeps_current_token_and_restores_backup_cursor_without_new_connection(self):
        sandbox = self.database.with_suffix('.plaid-sandbox.db')
        old_token, new_token = protect(b'old-test-token', True), protect(b'new-test-token', True)
        with connect(sandbox, write=True) as db:
            db.execute('INSERT INTO connection(id,item_id,token,cursor) VALUES (1,?,?,?)', ('same-item', old_token, 'backup-cursor'))
            db.execute("INSERT INTO link VALUES (1,'session','update',?,'2030-01-01')", (protect(b'link-test', True),))
        identifier = self.backup('sandbox')
        with connect(sandbox, write=True) as db:
            db.execute("UPDATE connection SET token=?,cursor='newer-cursor',needs_reauth=1", (new_token,))
            db.execute('DELETE FROM link')
        preview = self.preview(identifier)
        self.assertEqual(preview['bank_policy'], 'keep_current_authorization')
        result = self.restore(preview)
        self.assertEqual(result.status_code, 200, result.text)
        with connect(sandbox) as db:
            row = db.execute('SELECT item_id,token,cursor,needs_reauth FROM connection').fetchone()
            self.assertEqual(tuple(row), ('same-item', new_token, 'backup-cursor', 1))
            self.assertEqual(db.execute('SELECT COUNT(*) FROM link').fetchone()[0], 0)

    def test_foreign_item_corruption_and_path_traversal_are_rejected(self):
        sandbox = self.database.with_suffix('.plaid-sandbox.db')
        with connect(sandbox, write=True) as db:
            db.execute('INSERT INTO connection(id,item_id,token) VALUES (1,?,?)', ('first', protect(b'test-token', True)))
        identifier = self.backup('sandbox')
        with connect(sandbox, write=True) as db:
            db.execute("UPDATE connection SET item_id='second'")
        self.assertEqual(self.client.get('/api/backups/preview', params={'id': identifier}).status_code, 422)
        self.assertEqual(self.client.get('/api/backups/preview', params={'id': '../data/finance.db'}).status_code, 422)
        self.assertEqual(self.client.get('/api/backups/preview', params={'id': 'not-a-backup.db'}).status_code, 422)
        corrupt = self.store.directory / 'corrupt.finance-backup.zip'
        corrupt.write_bytes(b'broken')
        self.assertEqual(self.client.get('/api/backups/preview', params={'id': corrupt.name}).status_code, 422)

    def test_checksum_mismatch_and_unsupported_schema_are_rejected(self):
        identifier = self.backup()
        path = self.store.directory / identifier
        with zipfile.ZipFile(path) as original:
            data = {item.filename: original.read(item.filename) for item in original.infolist()}
        manifest = json.loads(data['manifest.json'])
        manifest['database_sha256'] = '0' * 64
        data['manifest.json'] = json.dumps(manifest).encode()
        with zipfile.ZipFile(path, 'w') as changed:
            for name, content in data.items():
                changed.writestr(name, content)
        self.assertEqual(self.client.get('/api/backups/preview', params={'id': identifier}).status_code, 422)

    def test_preferences_failure_rolls_database_back_to_original(self):
        self.account('Original')
        self.client.put('/api/chart-selection/personal', json={'excluded_account_ids': [1]})
        identifier = self.backup()
        self.account('Later')
        self.client.put('/api/chart-selection/personal', json={'excluded_account_ids': [2]})
        preview = self.preview(identifier)
        with patch('finance.recovery.atomic_write', side_effect=OSError('simulated write failure')):
            result = self.restore(preview)
        self.assertEqual(result.status_code, 409)
        self.assertIn('original workspace was recovered', result.json()['detail'])
        self.assertEqual(len(self.client.get('/api/overview?month=2026-11').json()['accounts']), 2)
        self.assertEqual(self.client.get('/api/chart-selection/personal').json()['excluded_account_ids'], [2])

    def test_legacy_database_is_migrated_on_copy_and_preview_never_changes_live_data(self):
        from finance.backup import create_backup
        self.account('Original')
        legacy = create_backup(self.database, self.store.directory)
        with connect(legacy, write=True) as db:
            db.execute('DROP TABLE transfer_links')
            db.execute('DROP TABLE merchant_rules')
            db.execute('DROP TABLE transaction_reviews')
            db.execute('PRAGMA user_version=1')
        original = legacy.read_bytes()
        preview = self.preview(legacy.name)
        self.assertTrue(preview['legacy'])
        self.assertEqual(preview['schema_version'], 1)
        self.assertEqual(preview['restored_schema_version'], 3)
        self.assertEqual(legacy.read_bytes(), original)
        self.assertEqual(self.restore(preview).status_code, 200)

    def test_incomplete_schema_and_pending_authorization_are_rejected(self):
        from finance.backup import create_backup
        legacy = create_backup(self.database, self.store.directory)
        with connect(legacy, write=True) as db:
            db.execute('DROP TABLE merchant_rules')
        self.assertEqual(self.client.get('/api/backups/preview', params={'id': legacy.name}).status_code, 422)
        identifier = self.backup('sandbox')
        with connect(self.database.with_suffix('.plaid-sandbox.db'), write=True) as db:
            db.execute("INSERT INTO link VALUES (1,'session','new',?,'2030-01-01')", (protect(b'link-test', True),))
        result = self.client.get('/api/backups/preview', params={'id': identifier})
        self.assertEqual(result.status_code, 422)
        self.assertIn('authorization', result.json()['detail'])

    def test_existing_write_guards_protect_restore_endpoints(self):
        response = self.client.post('/api/backups', json={'workspace': 'personal'}, headers={'x-finance-request': ''})
        self.assertEqual(response.status_code, 403)
