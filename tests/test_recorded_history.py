"""Tracking additions retain history without fabricating earlier account balances."""
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from fastapi.testclient import TestClient

from finance.api import create_app
from finance.db import connect

ROOT = Path(__file__).resolve().parents[1]


class RecordedHistoryTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = TemporaryDirectory()
        subprocess.run(['node', str(ROOT / 'node_modules/typescript/bin/tsc'), str(ROOT / 'frontend/recordedHistory.ts'),
                        '--target', 'es2022', '--module', 'commonjs', '--outDir', cls.folder.name], check=True, capture_output=True)
        cls.module = str(Path(cls.folder.name) / 'recordedHistory.js')

    @classmethod
    def tearDownClass(cls):
        cls.folder.cleanup()

    def project(self, history, accounts, measure='net'):
        script = 'const {recordedHistory}=require(' + json.dumps(self.module) + ');console.log(JSON.stringify(recordedHistory(' + ','.join(map(json.dumps, [history, accounts, measure])) + ')))'
        return json.loads(subprocess.check_output(['node', '-e', script], text=True))

    def test_asset_and_debt_join_from_first_snapshot_without_changing_old_points(self):
        history = [dict(date='2026-11-20', accounts=[dict(account_id=1, balance_minor=10000)]),
                   dict(date='2026-11-21', accounts=[dict(account_id=1, balance_minor=11000)]),
                   dict(date='2026-11-22', accounts=[dict(account_id=1, balance_minor=12000), dict(account_id=2, balance_minor=5000)]),
                   dict(date='2026-11-23', accounts=[dict(account_id=1, balance_minor=12000), dict(account_id=2, balance_minor=5000), dict(account_id=3, balance_minor=-21000)])]
        projected = self.project(history, [1, 2, 3])
        self.assertEqual([p['value_minor'] for p in projected], [10000, 11000, 17000, -4000])
        self.assertIn('1 account added', projected[2]['note'])
        self.assertIn('1 account added', projected[3]['note'])
        self.assertEqual([p['value_minor'] for p in self.project(history, [1, 2, 3], 'assets')], [10000, 11000, 17000, 17000])
        self.assertEqual([p['value_minor'] for p in self.project(history, [1, 2, 3], 'debt')], [0, 0, 0, 21000])
        self.assertEqual([p['value_minor'] for p in self.project(history, [2])], [None, None, 5000, 5000])

    def test_known_missing_or_stale_balances_still_leave_gaps(self):
        history = [dict(date='2026-11-20', accounts=[dict(account_id='old', value_minor=10000)]),
                   dict(date='2026-11-21', accounts=[dict(account_id='old', value_minor=None), dict(account_id='new', value_minor=5000)]),
                   dict(date='2026-11-22', accounts=[dict(account_id='new', value_minor=6000)]),
                   dict(date='2026-11-23', accounts=[dict(account_id='old', value_minor=0), dict(account_id='new', value_minor=6000)])]
        self.assertEqual([p['value_minor'] for p in self.project(history, ['old', 'new'])], [10000, None, None, 6000])
        self.assertEqual([p['value_minor'] for p in self.project(history, ['old', 'unrecorded'])], [10000, None, None, 0])
        self.assertTrue(all(p['value_minor'] is None for p in self.project(history, ['unrecorded'])))
        self.assertTrue(all(p['value_minor'] is None for p in self.project(history, [])))

    def test_addition_uses_only_cached_values_captured_in_that_snapshot(self):
        history = [dict(date='2026-11-20', accounts=[dict(account_id=1, balance_minor=10000)]),
                   dict(date='2026-11-21', accounts=[dict(account_id=1, balance_minor=None, saved_balance_minor=10000), dict(account_id=2, balance_minor=5000)]),
                   dict(date='2026-11-22', accounts=[dict(account_id=1, balance_minor=None, saved_balance_minor=10000), dict(account_id=2, balance_minor=6000)])]
        result = self.project(history, [1, 2])
        self.assertEqual([p['value_minor'] for p in result], [10000, 15000, None])
        self.assertIn('cached balances', result[1]['note'])

    def test_adding_manual_account_saves_new_snapshot_without_rewriting_previous(self):
        with TemporaryDirectory() as folder, patch.dict('os.environ', {'APP_ENV': 'production', 'APP_STARTUP_ENABLED': 'false'}):
            database = Path(folder) / 'data/finance.db'
            with TestClient(create_app(database), base_url='http://127.0.0.1', headers={'x-finance-request': '1'}) as client:
                def add(name, balance):
                    response = client.post('/api/accounts', json={'name': name, 'kind': 'checking', 'opening_balance_minor': balance, 'date': '2026-01-01'})
                    self.assertEqual(response.status_code, 201, response.text)
                    self.assertEqual(client.get('/api/plaid/live/preview?month=2026-01').status_code, 200)
                add('Fixture cash', 10000)
                source = database.with_suffix('.plaid-live.db')
                with connect(source) as db:
                    rows = [tuple(r) for r in db.execute('SELECT * FROM preview_snapshot_accounts ORDER BY snapshot_id,account_id')]
                    count = db.execute('SELECT COUNT(*) FROM preview_snapshots').fetchone()[0]
                add('New fixture cash', 5000)
                with connect(source) as db:
                    self.assertGreater(db.execute('SELECT COUNT(*) FROM preview_snapshots').fetchone()[0], count)
                    for row in rows:
                        self.assertEqual(tuple(db.execute('SELECT * FROM preview_snapshot_accounts WHERE snapshot_id=? AND account_id=?', row[:2]).fetchone()), row)
                    latest = db.execute('SELECT MAX(id) FROM preview_snapshots').fetchone()[0]
                    self.assertEqual(db.execute('SELECT COUNT(*) FROM preview_snapshot_accounts WHERE snapshot_id=?', (latest,)).fetchone()[0], 2)
