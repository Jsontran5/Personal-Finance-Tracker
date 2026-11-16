from datetime import date, datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from fastapi.testclient import TestClient

from finance.api import create_app
from finance.balance_history import banking_estimates, local_day, snapshot_history
from finance.db import connect
from finance.investments import project_investments
from finance.plaid_sandbox import SCHEMA, save_accounts
from finance.sandbox_preview import initialize_preview


class BalanceHistoryTests(TestCase):
    def setUp(self):
        self.folder = TemporaryDirectory()
        self.path = Path(self.folder.name) / 'fixture.db'
        with connect(self.path, write=True) as db:
            for statement in SCHEMA:
                db.execute(statement)
            initialize_preview(db)
            db.execute("INSERT INTO connection(id,item_id,token,history_status) VALUES (1,'fixture',?,'HISTORICAL_UPDATE_COMPLETE')", (b'not-a-token',))

    def tearDown(self):
        self.folder.cleanup()

    def account(self, db, key, amount, kind='depository', currency='USD'):
        save_accounts(db, [{'account_id': key, 'name': key, 'type': kind, 'subtype': 'checking',
                            'balances': {'current': amount, 'iso_currency_code': currency}}], 'fresh')
        db.execute("UPDATE accounts SET checked_at='2026-11-15T12:00:00' WHERE id=?", (key,))

    def snapshot(self, db, day):
        identifier = db.execute('INSERT INTO preview_snapshots(recorded_at,date,assets_minor,liabilities_minor) VALUES (?,?,0,0)',
                                (day+'T12:00:00', day)).lastrowid
        db.execute('INSERT INTO preview_snapshot_accounts SELECT ?,id,name,currency,current_minor,source,checked_at FROM accounts WHERE active=1', (identifier,))

    def tx(self, db, key, day, amount, *, account='bank', kind='expense', pending=0, removed=0, currency='USD'):
        db.execute('INSERT INTO transactions VALUES (?,?,?,?,?,?,?,?,?)',
                   (key, account, day, key, amount, currency, kind, pending, removed))

    def test_signed_posted_movements_transfers_credits_and_bounded_coverage(self):
        with connect(self.path, write=True) as db:
            self.account(db, 'bank', 1000)
            self.snapshot(db, '2026-11-15')
            self.tx(db, 'first', '2026-11-12', 100)
            self.tx(db, 'transfer', '2026-11-13', 5000, kind='transfer')
            self.tx(db, 'credit', '2026-11-14', -20000, kind='credit')
            self.tx(db, 'last', '2026-11-15', 1000)
            self.tx(db, 'pending', '2026-11-15', 90000, pending=1)
            self.tx(db, 'removed', '2026-11-15', 90000, removed=1)
            self.tx(db, 'newer', '2026-11-16', 90000)
            before = db.total_changes
            report = banking_estimates(db)['accounts'][0]
            self.assertEqual(db.total_changes, before)
            self.assertEqual(report['points'], [{'date': '2026-11-12', 'balance_minor': 86000},
                {'date': '2026-11-13', 'balance_minor': 81000}, {'date': '2026-11-14', 'balance_minor': 101000},
                {'date': '2026-11-15', 'balance_minor': 100000}])
            self.assertEqual(report['newer_transaction_count'], 1)
            self.assertEqual(report['transaction_count'], 4)
            db.execute("INSERT INTO preview_edits VALUES ('last','Edited','transfer',NULL,'')")
            self.assertEqual(banking_estimates(db)['accounts'][0]['points'], report['points'])

    def test_credit_liability_signs_and_snapshot_disagreement_are_preserved(self):
        with connect(self.path, write=True) as db:
            self.account(db, 'bank', 500, 'credit')
            db.execute("UPDATE accounts SET current_minor=-46000,checked_at='2026-11-13T12:00:00'")
            self.snapshot(db, '2026-11-13')
            db.execute("UPDATE accounts SET current_minor=-50000,checked_at='2026-11-15T12:00:00'")
            self.snapshot(db, '2026-11-15')
            self.tx(db, 'first', '2026-11-13', 0)
            self.tx(db, 'payment', '2026-11-14', -5000, kind='transfer')
            self.tx(db, 'charge', '2026-11-15', 10000)
            report = banking_estimates(db)['accounts'][0]
            self.assertEqual([p['balance_minor'] for p in report['points']], [-45000, -40000, -50000])
            self.assertEqual(report['max_difference_minor'], 1000)
            self.assertEqual(snapshot_history(db)[0]['accounts'][0]['balance_minor'], -46000)

    def test_partial_history_and_unsupported_accounts_have_explicit_reasons(self):
        with connect(self.path, write=True) as db:
            for key, kind, currency in [('bank', 'depository', 'USD'), ('broker', 'investment', 'USD'),
                                        ('loan', 'loan', 'USD'), ('foreign', 'depository', 'EUR')]:
                self.account(db, key, 100, kind, currency)
            db.execute("UPDATE connection SET history_status='INITIAL_UPDATE_COMPLETE'")
            self.snapshot(db, '2026-11-15')
            reports = banking_estimates(db)['accounts']
            self.assertTrue(all(not r['supported'] and r['reason'] for r in reports))
            self.assertIn('historical update', reports[0]['reason'])
            db.execute("UPDATE connection SET history_status='HISTORICAL_UPDATE_COMPLETE'")
            self.tx(db, 'eur', '2026-11-14', 100, currency='EUR')
            self.assertIn('Mixed', banking_estimates(db)['accounts'][0]['reason'])

    def test_snapshot_daily_latest_and_stale_peer_gaps_with_investment_balances(self):
        with connect(self.path, write=True) as db:
            self.account(db, 'bank', 100)
            self.account(db, 'broker', 200, 'investment')
            self.snapshot(db, '2026-11-15')
            db.execute("UPDATE accounts SET current_minor=12000,checked_at='2026-11-16T12:00:00' WHERE id='bank'")
            self.snapshot(db, '2026-11-16')
            self.assertIsNone(snapshot_history(db)[1]['accounts'][1]['balance_minor'])
            self.assertIsNone(project_investments(db)['history'][1]['accounts'][0]['value_minor'])
            db.execute("UPDATE accounts SET current_minor=25000,checked_at='2026-11-16T13:00:00' WHERE id='broker'")
            self.snapshot(db, '2026-11-16')
            history = snapshot_history(db)
            self.assertEqual(len(history), 2)
            self.assertEqual(history[-1]['accounts'][1]['balance_minor'], 25000)
            self.assertEqual(project_investments(db)['history'][-1]['accounts'][0]['value_minor'], 25000)
            self.assertEqual(history[0]['accounts'][1]['balance_minor'], 20000)

    def test_estimates_api_isolated_read_only_and_no_new_snapshot(self):
        with TestClient(create_app(Path(self.folder.name)/'app.db'), base_url='http://127.0.0.1') as client:
            url = '/api/plaid/sandbox/preview'
            client.get(url+'?month=2026-11')
            result = client.get(url+'/balance-history')
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(result.json()['accounts'], [])
            self.assertNotIn('token', result.text)
        with connect(self.path) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM preview_snapshots').fetchone()[0], 0)

    def test_local_day_handles_offset_midnight_and_invalid_dates(self):
        stamp = datetime(2026, 11, 16, 1, tzinfo=timezone.utc)
        self.assertEqual(local_day(stamp.isoformat()), stamp.astimezone().date())
        self.assertEqual(local_day('2026-11-15'), date(2026, 11, 15))
        self.assertIsNone(local_day('not-a-date'))
