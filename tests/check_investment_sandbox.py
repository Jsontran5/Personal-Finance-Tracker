r"""Opt-in provider check; creates and removes a temporary Sandbox-only Item.

Run: .venv\Scripts\python.exe tests/check_investment_sandbox.py --run
No Production calls or changes to the project's saved connection are made.
"""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient
from finance.api import create_app
from finance.db import connect
from finance.plaid_sandbox import credentials, plaid_client
from finance.plaid_vault import protect


def run():
    keys = credentials(ROOT)
    if not keys:
        raise SystemExit('Local Sandbox credentials are required.')
    call = plaid_client(keys)
    token = None
    try:
        public = call('/sandbox/public_token/create', retry=False, institution_id='ins_109508', initial_products=['investments'])
        exchange = call('/item/public_token/exchange', retry=False, public_token=public['public_token'])
        token = exchange['access_token']
        with TemporaryDirectory(prefix='finance-investment-sandbox-') as temporary:
            database = Path(temporary) / 'data/finance.db'
            with patch('finance.plaid_sandbox.credentials', return_value=keys), TestClient(create_app(database), base_url='http://127.0.0.1', headers={'x-finance-request': '1'}) as client:
                client.get('/api/plaid/sandbox/preview?month=2026-11')
                with connect(database.with_suffix('.plaid-sandbox.db'), write=True) as db:
                    db.execute('INSERT INTO connection(id,item_id,token) VALUES (1,?,?)', (exchange['item_id'], protect(token.encode(), True)))
                for route in ['/api/plaid/sandbox/investments/sync', '/api/plaid/sandbox/investments/sync?activity=true']:
                    result = client.post(route)
                    if result.status_code != 200:
                        raise RuntimeError(f'Investment API returned {result.status_code}: {result.json().get("detail")}')
                portfolio = client.get('/api/plaid/sandbox/investments').json()
                activity = client.get('/api/plaid/sandbox/investments/activity').json()
                assert portfolio['holdings'] and activity['total']
                assert client.get('/api/plaid/sandbox/preview?month=2026-11').json()['transactions'] == []
                print(f'Actual Sandbox API verified: {len(portfolio["accounts"])} investment accounts, {len(portfolio["holdings"])} holdings, {activity["total"]} activity records. Spending ledger unchanged.', flush=True)
    finally:
        if token:
            call('/item/remove', retry=False, access_token=token)
            print('Temporary Sandbox Item removed. Existing project connection was not used or changed.', flush=True)


if __name__ == '__main__':
    if sys.argv[1:] != ['--run']:
        raise SystemExit('Pass --run to explicitly create and remove a temporary Sandbox test Item.')
    run()
