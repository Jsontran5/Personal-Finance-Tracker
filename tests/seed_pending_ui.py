"""Serve a disposable, provider-blocked preview for pending reporting QA."""
from datetime import date
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient
from finance.api import create_app
from finance.db import connect
from finance.multi_bank import transaction_page
from tests import check_frontend_refresh_ui as preview

base_seed = preview.seed


def seed(database):
    base_seed(database)
    day = date.today().isoformat()
    with connect(database.with_suffix('.plaid-live.db'), write=True) as db:
        transaction_page(db, 1, [
            dict(transaction_id=identifier, account_id=account, date=day, name=name, amount=amount,
                 iso_currency_code='USD', pending=True, personal_finance_category={'primary': category})
            for identifier, account, name, amount, category in [
                ('pending-gas', 'card', 'Pending gas authorization', 40, 'TRANSPORTATION'),
                ('pending-refund', 'card', 'Pending grocery reimbursement', -8, 'GENERAL_MERCHANDISE'),
                ('pending-pay', '0', 'Pending payroll deposit', -250, 'INCOME'),
                ('pending-transfer', '0', 'Pending internal transfer', 75, 'TRANSFER_OUT'),
            ]], [], [])
    path = '/api/plaid/live/preview'
    with TestClient(create_app(database), base_url='http://127.0.0.1', headers={'x-finance-request': '1'}) as client:
        data = client.get(path, params={'month': day[:7]}).json()
        categories = {c['name']: c['id'] for c in data['categories']}
        for tx in data['transactions']:
            if tx['merchant'] in {'Pending gas authorization', 'Pending grocery reimbursement'}:
                response = client.put(path + '/transactions/' + str(tx['id']), json={'merchant': tx['merchant'], 'kind': 'refund' if 'reimbursement' in tx['merchant'] else 'expense', 'category_id': categories['Transportation' if 'gas' in tx['merchant'] else 'Groceries'], 'notes': 'Synthetic pending UI fixture'})
                assert response.is_success, response.text
        plan = client.get(path + '/plan', params={'month': day[:7]}).json()
        response = client.post(path + '/plan/setup', json={'token': plan['opening_token'], 'method': 'zero',
            'cash_accounts': [a['id'] for a in plan['accounts'] if a['kind'] in {'cash', 'checking', 'savings'}],
            'credit_accounts': [a['id'] for a in plan['accounts'] if a['kind'] == 'credit']})
        assert response.is_success, response.text
        plan = client.get(path + '/plan', params={'month': day[:7]}).json()
        response = client.post(path + '/plan/actions', params={'month': day[:7]}, json={'token': plan['token'], 'kind': 'assign', 'lines': [{'category': 'c:' + str(categories['Transportation']), 'amount_minor': 10000}]})
        assert response.is_success, response.text


if __name__ == '__main__':
    preview.seed = seed
    with TemporaryDirectory(prefix='pending-preview-', dir=ROOT / 'data') as folder:
        preview.serve(preview.free_port(), folder)
