from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from fastapi.testclient import TestClient

from finance.api import create_app
from finance.db import connect
from finance.plaid_sandbox import save_accounts, save_transactions


class ReviewTests(TestCase):
    def setUp(self):
        self.folder = TemporaryDirectory()
        self.database = Path(self.folder.name) / 'data/finance.db'
        self.client = TestClient(create_app(self.database), base_url='http://127.0.0.1', headers={'x-finance-request': '1'})
        self.client.__enter__()
        self.path = self.database.with_suffix('.plaid-sandbox.db')
        self.prefix = '/api/plaid/sandbox/preview/review'
        self.client.get('/api/plaid/sandbox/preview?month=2026-11')
        with connect(self.path, write=True) as db:
            save_accounts(db, [{'account_id': 'bank', 'name': 'Bank', 'type': 'depository', 'subtype': 'checking',
                                'balances': {'iso_currency_code': 'USD', 'current': 100}}], 'cached')
            save_transactions(db, [self.tx('purchase'), self.tx('credit', -20), self.tx('pending', pending=True)], [], [])

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.folder.cleanup()

    def tx(self, identifier, amount=10, pending=False):
        return {'transaction_id': identifier, 'account_id': 'bank', 'name': 'COFFEE SHOP', 'date': '2026-11-01',
                'amount': amount, 'iso_currency_code': 'USD', 'pending': pending}

    def rows(self):
        return self.client.get('/api/plaid/sandbox/preview?month=2026-11').json()['transactions']

    def apply(self, payload):
        preview = self.client.post(self.prefix + '/preview', json=payload)
        self.assertEqual(preview.status_code, 200, preview.text)
        return self.client.post(self.prefix + '/apply', json={**payload, 'preview_token': preview.json()['preview_token']})

    def test_backfill_is_not_blanket_review_and_marks_do_not_bypass_credit_classification(self):
        review = self.client.get(self.prefix).json()
        self.assertEqual(review['counts'], {'needs_review': 1, 'reviewed': 0, 'not_required': 2})
        self.assertEqual(review['uncategorized'], 3)
        ids = [row['id'] for row in self.rows()]
        result = self.apply({'ids': ids, 'action': {'operation': 'review'}})
        self.assertEqual(result.json(), {'applied': 1, 'skipped': 2})
        self.assertEqual(self.client.get(self.prefix).json()['counts']['needs_review'], 1)
        self.assertEqual(self.client.get('/api/review').json()['counts']['reviewed'], 0)
        purchase = next(row['id'] for row in self.rows() if not row['pending'] and not row['needs_review'])
        self.apply({'ids': [purchase], 'action': {'operation': 'flag'}})
        overview = self.client.get('/api/plaid/sandbox/preview?month=2026-11').json()
        self.assertEqual(overview['spending_minor'], 1000)
        self.assertEqual(self.client.get(self.prefix).json()['counts']['needs_review'], 2)

    def test_preview_is_required_and_stale_previews_are_rejected_atomically(self):
        purchase = next(row['id'] for row in self.rows() if not row['pending'] and not row['needs_review'])
        body = {'ids': [purchase], 'action': {'kind': 'expense', 'category_id': 1}}
        self.assertEqual(self.client.post(self.prefix + '/apply', json=body).status_code, 409)
        preview = self.client.post(self.prefix + '/preview', json=body).json()
        with connect(self.path, write=True) as db:
            db.execute("UPDATE transactions SET amount_minor=2000 WHERE id='purchase'")
        result = self.client.post(self.prefix + '/apply', json={**body, 'preview_token': preview['preview_token']})
        self.assertEqual(result.status_code, 409)
        with connect(self.path) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM preview_edits').fetchone()[0], 0)

    def test_rule_preview_preserves_individual_edits_and_applies_on_future_sync(self):
        purchase = next(row for row in self.rows() if not row['pending'] and not row['needs_review'])
        self.client.put('/api/plaid/sandbox/preview/transactions/' + str(purchase['id']), json={
            'merchant': 'My shop', 'kind': 'expense', 'category_id': 2, 'notes': 'Keep this',
        })
        body = {'action': {'kind': 'expense', 'category_id': 1, 'rule': {
            'merchant': ' coffee   SHOP ', 'match_kind': 'expense', 'future': True,
        }}}
        preview = self.client.post(self.prefix + '/preview', json=body).json()
        self.assertEqual(preview['applicable'], 0)
        self.assertEqual(self.apply(body).status_code, 200)
        with connect(self.path, write=True) as db:
            save_transactions(db, [self.tx('new')], [], [])
            save_transactions(db, [], [self.tx('new', 12)], [])
        rows = self.rows()
        new = next(row for row in rows if row['merchant'] == 'COFFEE SHOP' and row['amount_minor'] == 1200)
        self.assertEqual(new['category_id'], 1)
        old = next(row for row in rows if row['id'] == purchase['id'])
        self.assertEqual((old['merchant'], old['category_id'], old['notes']), ('My shop', 2, 'Keep this'))
        rule = self.client.get(self.prefix).json()['rules'][0]
        self.client.put(self.prefix + '/rules/' + str(rule['id']), json={'active': False})
        with connect(self.path, write=True) as db:
            save_transactions(db, [self.tx('disabled')], [], [])
            self.assertIsNone(db.execute("SELECT 1 FROM preview_edits WHERE transaction_id='disabled'").fetchone())

    def test_credit_classification_resolves_review_and_direction_mismatches_skip(self):
        credit = next(row['id'] for row in self.rows() if row['needs_review'])
        payload = {'ids': [credit], 'action': {'kind': 'expense', 'category_id': 1}}
        self.assertEqual(self.apply(payload).json()['applied'], 0)
        payload['action'] = {'kind': 'income', 'category_id': 9}
        self.assertEqual(self.apply(payload).json()['applied'], 1)
        self.assertEqual(self.client.get(self.prefix).json()['counts']['needs_review'], 0)
        self.assertEqual(self.client.get('/api/plaid/sandbox/preview?month=2026-11').json()['income_minor'], 2000)

    def test_pending_to_posted_preserves_local_review_metadata(self):
        payload = {'ids': [next(row['id'] for row in self.rows() if not row['pending'] and not row['needs_review'])], 'action': {'operation': 'flag'}}
        self.apply(payload)
        with connect(self.path, write=True) as db:
            posted = {**self.tx('posted'), 'pending_transaction_id': 'purchase'}
            save_transactions(db, [posted], [], [])
            self.assertEqual(db.execute("SELECT state FROM transaction_reviews WHERE transaction_id='posted'").fetchone()[0], 'flagged')
