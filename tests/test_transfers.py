from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from fastapi.testclient import TestClient
from finance.api import create_app
from finance.db import connect
from finance.plaid_sandbox import save_accounts, save_transactions


class TransferTests(TestCase):
    def setUp(self):
        self.folder = TemporaryDirectory()
        self.database = Path(self.folder.name) / 'data/finance.db'
        self.client = TestClient(create_app(self.database), base_url='http://127.0.0.1', headers={'x-finance-request': '1'})
        self.client.__enter__()
        self.path = self.database.with_suffix('.plaid-sandbox.db')
        self.prefix = '/api/plaid/sandbox/preview/review/transfers'
        self.client.get('/api/plaid/sandbox/preview?month=2026-11')
        with connect(self.path, write=True) as db:
            save_accounts(db, [{'account_id': name, 'name': name, 'type': kind, 'subtype': subtype,
                                'balances': {'iso_currency_code': 'USD', 'current': 1000}}
                               for name, kind, subtype in [('checking', 'depository', 'checking'), ('saving', 'depository', 'savings'), ('card', 'credit', 'credit card')]], 'cached')
            save_transactions(db, [self.tx('out', 'checking', 100), self.tx('in', 'saving', -100)], [], [])

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.folder.cleanup()

    def tx(self, identifier, account, amount, day='2026-11-01', **extra):
        return {'transaction_id': identifier, 'account_id': account, 'name': 'Transfer payment ' + identifier,
                'date': day, 'amount': amount, 'iso_currency_code': 'USD', 'pending': False, **extra}

    def overview(self, month='2026-11'):
        return self.client.get('/api/plaid/sandbox/preview?month=' + month).json()

    def confirm(self, candidate=None):
        candidate = candidate or self.client.get(self.prefix).json()['suggestions'][0]
        body = {'outgoing_id': candidate['outgoing']['id'], 'incoming_id': candidate['incoming']['id']}
        preview = self.client.post(self.prefix + '/preview', json=body)
        self.assertEqual(preview.status_code, 200, preview.text)
        result = self.client.post(self.prefix + '/confirm', json={**body, 'preview_token': preview.json()['preview_token']})
        self.assertEqual(result.status_code, 200, result.text)
        return body

    def test_confirmation_is_reversible_and_preserves_source_and_balances(self):
        before = self.overview()
        with connect(self.path) as db:
            records = [tuple(row) for row in db.execute('SELECT * FROM transactions ORDER BY id')]
        self.confirm()
        after = self.overview()
        self.assertEqual(after['spending_minor'], 0)
        self.assertEqual(after['income_minor'], 0)
        self.assertEqual(after['spending']['eligible_ids'], [])
        self.assertTrue(all(row['transfer_pair_id'] for row in after['transactions']))
        self.assertEqual(self.client.get('/api/plaid/sandbox/preview/review').json()['counts']['needs_review'], 0)
        self.assertEqual(before['net_worth_minor'], after['net_worth_minor'])
        with connect(self.path) as db:
            self.assertEqual(records, [tuple(row) for row in db.execute('SELECT * FROM transactions ORDER BY id')])
        link = self.client.get(self.prefix).json()['saved'][0]
        self.client.delete(self.prefix + '/' + str(link['id']))
        restored = self.overview()
        self.assertEqual(restored['spending_minor'], before['spending_minor'])
        self.assertEqual(restored['review_count'], before['review_count'])

    def test_suggestions_skip_pending_currency_same_account_and_date_mismatches(self):
        with connect(self.path, write=True) as db:
            save_transactions(db, [self.tx('pending', 'card', -100, pending=True), self.tx('currency', 'card', -100, iso_currency_code='EUR'),
                                   self.tx('same', 'checking', -100), self.tx('late', 'card', -100, day='2026-11-10'),
                                   self.tx('alternate', 'card', -100)], [], [])
        result = self.client.get(self.prefix).json()
        self.assertEqual(len(result['suggestions']), 2)
        self.assertTrue(all(row['multiple_options'] for row in result['suggestions']))
        card = next(row for row in result['suggestions'] if row['kind'] == 'card_payment')
        body = self.confirm(card)
        other = next(row for row in result['suggestions'] if row['kind'] != 'card_payment')
        self.assertEqual(self.client.post(self.prefix + '/preview', json={**body, 'incoming_id': other['incoming']['id']}).status_code, 409)

    def test_dismissal_persists_and_can_be_restored_without_changing_totals(self):
        pair = self.client.get(self.prefix).json()['suggestions'][0]
        body = {'outgoing_id': pair['outgoing']['id'], 'incoming_id': pair['incoming']['id']}
        self.client.post(self.prefix + '/dismiss', json=body)
        result = self.client.get(self.prefix).json()
        self.assertEqual(result['suggestions'], [])
        self.assertEqual(self.overview()['spending_minor'], 10000)
        self.client.delete(self.prefix + '/' + str(result['saved'][0]['id']))
        self.assertEqual(len(self.client.get(self.prefix).json()['suggestions']), 1)

    def test_stale_preview_and_source_corrections_do_not_silently_exclude_activity(self):
        pair = self.client.get(self.prefix).json()['suggestions'][0]
        body = {'outgoing_id': pair['outgoing']['id'], 'incoming_id': pair['incoming']['id']}
        preview = self.client.post(self.prefix + '/preview', json=body).json()
        with connect(self.path, write=True) as db:
            save_transactions(db, [], [self.tx('out', 'checking', 101)], [])
        self.assertEqual(self.client.post(self.prefix + '/confirm', json={**body, 'preview_token': preview['preview_token']}).status_code, 409)
        with connect(self.path, write=True) as db:
            save_transactions(db, [], [self.tx('out', 'checking', 100)], [])
        self.confirm()
        with connect(self.path, write=True) as db:
            save_transactions(db, [], [], [{'transaction_id': 'in'}])
        link = self.client.get(self.prefix).json()['saved'][0]
        self.assertFalse(link['valid'])
        self.assertEqual(self.overview()['spending_minor'], 10000)

    def test_linked_edits_and_bulk_classification_are_blocked_until_unlinked(self):
        body = self.confirm()
        result = self.client.put('/api/plaid/sandbox/preview/transactions/' + str(body['outgoing_id']), json={
            'merchant': 'Edited', 'kind': 'expense', 'notes': '', 'category_id': 1,
        })
        self.assertEqual(result.status_code, 409)
        result = self.client.post('/api/plaid/sandbox/preview/review/preview', json={
            'ids': [body['outgoing_id']], 'action': {'kind': 'expense', 'category_id': 1},
        }).json()
        self.assertEqual(result['applicable'], 0)

    def test_unrelated_equal_purchase_and_refund_do_not_create_suggestion(self):
        with connect(self.path, write=True) as db:
            save_transactions(db, [self.tx('ordinary', 'checking', 250, name='Coffee shop'), self.tx('refund', 'card', -250, name='Clothing shop refund')], [], [])
        result = self.client.get(self.prefix).json()
        self.assertEqual(len(result['suggestions']), 1)

    def test_personal_pairs_keep_both_account_balances_and_original_categories(self):
        ids = []
        for name in ['Manual checking', 'Manual saving']:
            ids.append(self.client.post('/api/accounts', json={'name': name, 'kind': 'checking', 'currency': 'USD', 'date': '2026-01-01', 'opening_balance_minor': 100000}).json()['id'])
        out = self.client.post('/api/transactions', json={'account_id': ids[0], 'kind': 'expense', 'amount_minor': 10000, 'date': '2026-11-01', 'merchant': 'Transfer out', 'category_id': 1}).json()['id']
        inc = self.client.post('/api/transactions', json={'account_id': ids[1], 'kind': 'income', 'amount_minor': 10000, 'date': '2026-11-02', 'merchant': 'Transfer in', 'category_id': 9}).json()['id']
        before = self.client.get('/api/overview?month=2026-11').json()
        prefix = '/api/review/transfers'
        body = {'outgoing_id': out, 'incoming_id': inc}
        preview = self.client.post(prefix + '/preview', json=body).json()
        self.assertEqual(self.client.post(prefix + '/confirm', json={**body, 'preview_token': preview['preview_token']}).status_code, 200)
        after = self.client.get('/api/overview?month=2026-11').json()
        self.assertEqual(after['accounts'], before['accounts'])
        self.assertEqual(after['income_minor'], 0)
        self.assertEqual(after['spending_minor'], 0)
        self.assertEqual({row['id']: row['category_id'] for row in after['transactions']}, {out: 1, inc: 9})
        self.assertEqual(self.client.delete('/api/transactions/' + str(out)).status_code, 409)
