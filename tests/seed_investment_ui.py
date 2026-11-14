"""Synthetic investment data for isolated browser QA copies only."""
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import sys

from finance.db import connect
from finance.investments import save_holdings, save_activity
from finance.sandbox_preview import initialize_preview


def seed(path):
    with connect(path, write=True) as db:
        initialize_preview(db)
        accounts = [dict(row) for row in db.execute("SELECT * FROM accounts WHERE type='investment' AND active=1")]
        assert accounts
        stamp = datetime.now(timezone.utc).isoformat()
        provider_accounts = [{'account_id': item['id'], 'name': item['name'], 'type': 'investment',
                              'balances': {'current': item['current_minor'] / 100, 'iso_currency_code': item['currency']}}
                             for item in accounts]
        securities = [{'security_id': f'qa-security-{index}', 'name': 'Synthetic international diversified investment fund with a long descriptive name ' + str(index),
                       'ticker_symbol': 'QA' + str(index), 'type': 'cash' if index == 0 else 'etf' if index % 2 else 'mutual fund',
                       'is_cash_equivalent': index == 0} for index in range(18)]
        holdings = [{'account_id': accounts[index % len(accounts)]['id'], 'security_id': security['security_id'],
                     'quantity': '10.12345678', 'institution_price': '123.456789', 'institution_value': 1250 + index * 10,
                     'cost_basis': 1000 if index != 1 else None, 'iso_currency_code': 'USD',
                     'institution_price_as_of': date.today().isoformat(), 'tax_lots': [],
                     'future_field': {'description': 'Synthetic additional detail ' * 20}}
                    for index, security in enumerate(securities)]
        for index in range(4):
            for item in provider_accounts:
                item['balances']['current'] *= 1.01
            save_holdings(db, {'holdings': holdings, 'securities': securities, 'accounts': provider_accounts}, stamp)
            db.execute('UPDATE investment_observations SET date=? WHERE id=(SELECT MAX(id) FROM investment_observations)',
                       ((date.today() - timedelta(days=(3 - index) * 5)).isoformat(),))
        rows = []
        for index in range(32):
            kind = ['buy', 'sell', 'cash', 'fee'][index % 4]
            tx = {'investment_transaction_id': f'qa-investment-{index:02}', 'account_id': accounts[index % len(accounts)]['id'],
                  'date': (date.today() - timedelta(days=index)).isoformat(), 'name': 'Synthetic investment ' + kind,
                  'type': kind, 'subtype': 'dividend' if kind == 'cash' else kind,
                  'amount': -250 if kind in {'sell', 'cash'} else 250,
                  'quantity': -2 if kind == 'sell' else 2, 'price': 125, 'fees': None if kind == 'cash' else 0,
                  'iso_currency_code': 'USD', 'security_id': securities[index % len(securities)]['security_id']}
            rows.append((tx, securities[index % len(securities)]))
        save_activity(db, rows, (date.today() - timedelta(days=31)).isoformat(), date.today().isoformat(), stamp)


if __name__ == '__main__':
    seed(Path(sys.argv[1]))
