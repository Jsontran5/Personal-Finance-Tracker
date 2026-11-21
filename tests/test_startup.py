from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import time
from unittest import TestCase
from unittest.mock import patch

from fastapi.testclient import TestClient

from finance.api import create_app
from finance.app_config import startup_enabled
from finance.db import connect, initialize
from finance.live_setup import initialize_live, live_router
from finance.multi_bank import map_accounts
from finance.plaid_sandbox import PlaidError
from finance.plaid_vault import protect
from finance.recovery import BackupStore
from finance.startup import StartupMaintenance, automatic_backup, automatic_sets


class StartupTests(TestCase):
    def setUp(self):
        self.folder = TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.database = self.root / 'data/finance.db'
        self.live = self.database.with_suffix('.plaid-live.db')
        self.env = patch.dict('os.environ', {'APP_ENV': 'production', 'APP_STARTUP_ENABLED': 'true',
            'PLAID_CLIENT_ID': 'synthetic-client', 'PLAID_PRODUCTION_SECRET': 'synthetic-secret'})
        self.env.start()
        initialize(self.database)
        with connect(self.live, write=True) as db:
            initialize_live(db)
        self.calls = []
        self.balance_failure = False

    def tearDown(self):
        self.env.stop()
        self.folder.cleanup()

    def seed(self):
        with connect(self.live, write=True) as db:
            db.execute("INSERT INTO multi_connections(id,institution_id,name,profile,item_id,token) VALUES (1,'ins_test','Synthetic bank','banking','synthetic-item',?)", (protect(b'synthetic-authorization', True),))
            map_accounts(db, 1, [self.account(100)], source='fresh')
            old = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
            db.execute('UPDATE accounts SET checked_at=?', (old,))

    def account(self, amount):
        return {'account_id': 'checking', 'name': 'Synthetic checking', 'type': 'depository', 'subtype': 'checking',
                'balances': {'current': amount, 'iso_currency_code': 'USD'}}

    def provider(self, endpoint, **payload):
        self.calls.append(endpoint)
        if endpoint == '/transactions/sync':
            return {'added': [], 'modified': [], 'removed': [], 'has_more': False, 'next_cursor': 'synthetic-cursor'}
        if endpoint == '/accounts/get':
            return {'accounts': [self.account(100)]}
        if endpoint == '/accounts/balance/get':
            if self.balance_failure:
                raise PlaidError('INTERNAL_SERVER_ERROR')
            return {'accounts': [self.account(200)]}
        raise AssertionError('Unexpected provider operation: ' + endpoint)

    def app(self):
        with patch('finance.api.live_router', lambda database: live_router(database, client_factory=lambda *args, **kw: self.provider)):
            return create_app(self.database)

    def wait(self, app):
        deadline = time.monotonic() + 12
        while app.state.startup_maintenance.status()['running'] and time.monotonic() < deadline:
            time.sleep(.02)
        self.assertFalse(app.state.startup_maintenance.status()['running'])
        return app.state.startup_maintenance.status()

    def test_backend_launch_backs_up_before_sync_and_snapshots_without_browser_trigger(self):
        self.seed()
        app = self.app()
        with TestClient(app, base_url='http://127.0.0.1') as client:
            state = self.wait(app)
            self.assertEqual(self.calls, ['/transactions/sync', '/accounts/get', '/accounts/balance/get'])
            self.assertTrue(all(row['status'] == 'Updated' for row in state['results']))
            self.assertEqual(state['results'][0]['product'], 'backup')
            self.assertEqual(state['results'][-1]['product'], 'snapshot')
            count = len(self.calls)
            for _ in range(3):
                self.assertEqual(client.get('/api/startup').status_code, 200)
            self.assertEqual(len(self.calls), count)
            store = BackupStore(self.database)
            identifier = next(row['id'] for row in store.list() if '/live-' in row['id'])
            with store.staged(identifier) as (_, copy, *rest):
                with connect(copy) as db:
                    self.assertEqual(db.execute('SELECT current_minor FROM accounts').fetchone()[0], 10000)
            with connect(self.live) as db:
                self.assertEqual(db.execute('SELECT current_minor FROM accounts').fetchone()[0], 20000)
                latest = db.execute('SELECT MAX(id) FROM preview_snapshots').fetchone()[0]
                self.assertEqual(db.execute('SELECT balance_minor FROM preview_snapshot_accounts WHERE snapshot_id=?', (latest,)).fetchone()[0], 20000)
        # Another backend launch always attempts the pass, while retaining the product cooldown.
        second = self.app()
        with TestClient(second, base_url='http://127.0.0.1'):
            state = self.wait(second)
            self.assertEqual(state['results'][0]['status'], 'Skipped')
            self.assertEqual(state['results'][-1]['status'], 'Updated')
            self.assertEqual(len(self.calls), count)
            self.assertEqual(len(automatic_sets(BackupStore(self.database))), 1)

    def test_failed_balance_sync_keeps_observation_time_and_does_not_invent_history(self):
        from finance.balance_history import snapshot_history
        self.seed()
        with connect(self.live) as db:
            checked = db.execute('SELECT checked_at FROM accounts').fetchone()[0]
        self.balance_failure = True
        app = self.app()
        with TestClient(app, base_url='http://127.0.0.1'):
            state = self.wait(app)
            self.assertTrue(any(row['product'] == 'balances' and row['status'] == 'Failed' for row in state['results']))
            self.assertEqual(state['results'][-1]['status'], 'Updated')
            with connect(self.live) as db:
                self.assertEqual(db.execute('SELECT checked_at FROM accounts').fetchone()[0], checked)
                self.assertIsNone(snapshot_history(db)[-1]['accounts'][0]['value_minor'])

    def test_automatic_pairs_are_restorable_and_retention_never_deletes_manual_backups(self):
        store = BackupStore(self.database)
        manual = store.create('personal')['id']
        base = datetime.now(timezone.utc) - timedelta(days=16)
        for day in range(16):
            result = automatic_backup(self.database, clock=base + timedelta(days=day))
            self.assertEqual(result['status'], 'Updated')
        self.assertEqual(len(automatic_sets(store)), 14)
        self.assertTrue(store.resolve(manual).is_file())
        result = automatic_backup(self.database, clock=base + timedelta(days=15, hours=1))
        self.assertEqual(result['status'], 'Skipped')
        with patch.dict('os.environ', {'APP_STARTUP_ENABLED': 'false'}):
            with TestClient(create_app(self.database), base_url='http://127.0.0.1', headers={'x-finance-request': '1'}) as client:
                backups = client.get('/api/backups').json()['backups']
                identifier = next(row['id'] for row in backups if row['id'].startswith('automatic/'))
                preview = client.get('/api/backups/preview', params={'id': identifier})
                self.assertEqual(preview.status_code, 200, preview.text)
                data = preview.json()
                restored = client.post('/api/backups/restore', json={key: data[key] for key in ['id', 'preview_token', 'confirmation']})
                self.assertEqual(restored.status_code, 200, restored.text)
        for invalid in ['automatic/../../.env', 'automatic/' + 'a'*32 + '/../.env']:
            with self.assertRaises(ValueError):
                store.resolve(invalid)

    def test_partial_pair_failure_is_not_published_or_treated_as_recent_backup(self):
        original = BackupStore.create
        def fail_live(store, mode, recovery=False):
            if mode == 'live':
                raise OSError('synthetic disk error')
            return original(store, mode, recovery)
        with patch.object(BackupStore, 'create', fail_live):
            with self.assertRaises(OSError):
                automatic_backup(self.database)
        self.assertEqual(automatic_sets(BackupStore(self.database)), [])
        self.assertEqual(automatic_backup(self.database)['status'], 'Updated')

    def test_worker_continues_after_backup_and_product_failures_and_stops_between_requests(self):
        calls = []
        def sync(identifier, product):
            calls.append(product)
            if product == 'transactions':
                raise HTTPException(409, 'Synthetic failure')
            return {'count': 0}
        from fastapi import HTTPException
        operations = {'connections': lambda: [{'id': 1, 'name': 'Bank', 'profile': 'banking', 'investments_enabled': True, 'needs_reauth': False}],
            'sync': sync, 'snapshot': lambda: {'id': 1, 'message': 'Saved'}}
        worker = StartupMaintenance(self.database, operations, True)
        with patch('finance.startup.automatic_backup', side_effect=OSError('synthetic failure')):
            worker.start(); self.wait_worker(worker)
        self.assertEqual(calls, ['transactions', 'holdings', 'activity', 'balances'])
        self.assertEqual(worker.status()['results'][-1]['product'], 'snapshot')
        blocker, release = threading.Event(), threading.Event()
        def waiting_sync(identifier, product):
            blocker.set(); release.wait(5); return {'count': 0}
        operations['sync'] = waiting_sync
        worker = StartupMaintenance(self.database, operations, True)
        worker.start(); self.assertTrue(blocker.wait(5)); worker.stop.set(); release.set(); worker.close()
        self.assertEqual(worker.status()['phase'], 'Interrupted')
        self.assertEqual(sum(row['product'] in {'transactions', 'holdings', 'activity', 'balances'} for row in worker.status()['results']), 1)
        self.assertEqual(worker.status()['results'][-1]['product'], 'snapshot')

    def wait_worker(self, worker):
        worker.thread.join(timeout=12)
        self.assertFalse(worker.thread.is_alive())

    def test_disabled_and_development_modes_do_not_run_startup(self):
        for values in [{'APP_STARTUP_ENABLED': 'false'}, {'APP_ENV': 'development'}]:
            with patch.dict('os.environ', values):
                app = self.app()
                with TestClient(app, base_url='http://127.0.0.1') as client:
                    self.assertFalse(client.get('/api/startup').json()['enabled'])
                    self.assertIsNone(app.state.startup_maintenance.thread)
        (self.root / '.env').write_text('APP_STARTUP_ENABLED=false\n', encoding='utf-8')
        with patch.dict('os.environ', {'APP_STARTUP_ENABLED': 'invalid'}):
            with self.assertRaises(ValueError):
                startup_enabled(self.root)

    def test_startup_respects_pending_consent_and_blocks_manual_sync_or_restore_while_running(self):
        self.seed()
        with connect(self.live, write=True) as db:
            db.execute('INSERT INTO live_investment_connections(connection_id,consented) VALUES (1,0)')
        app = self.app()
        entered, release = threading.Event(), threading.Event()
        original = app.state.startup_maintenance.operations['sync']
        def delayed(identifier, product):
            entered.set(); release.wait(5)
            return original(identifier, product)
        app.state.startup_maintenance.operations['sync'] = delayed
        with TestClient(app, base_url='http://127.0.0.1', headers={'x-finance-request': '1'}) as client:
            try:
                self.assertTrue(entered.wait(5))
                self.assertTrue(client.get('/api/startup').json()['running'])
                self.assertEqual(client.post('/api/plaid/live/connections/1/sync?product=balances').status_code, 409)
                self.assertEqual(client.post('/api/backups/restore', json={}).status_code, 409)
            finally:
                release.set()
            state = self.wait(app)
            investments = [row for row in state['results'] if row['product'] in {'holdings', 'activity'}]
            self.assertEqual(len(investments), 2)
            self.assertTrue(all(row['status'] == 'Skipped' and 'consent' in row['message'] for row in investments))
            self.assertNotIn('/investments/holdings/get', self.calls)
