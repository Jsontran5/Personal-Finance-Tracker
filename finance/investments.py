"""Broker-reported investment positions and activity, separate from the spending ledger."""
from calendar import monthrange
from datetime import date
from decimal import Decimal, InvalidOperation
import json
from .balance_history import snapshot_history


def initialize_investments(db):
    for statement in (
        "CREATE TABLE IF NOT EXISTS investment_holdings (account_id TEXT NOT NULL, security_id TEXT NOT NULL, payload TEXT NOT NULL, security TEXT NOT NULL, retrieved_at TEXT NOT NULL, PRIMARY KEY(account_id,security_id))",
        "CREATE TABLE IF NOT EXISTS investment_activity (id TEXT PRIMARY KEY, account_id TEXT NOT NULL, date TEXT NOT NULL, payload TEXT NOT NULL, security TEXT NOT NULL, removed INTEGER NOT NULL DEFAULT 0)",
        "CREATE INDEX IF NOT EXISTS investment_activity_dates ON investment_activity(removed,date DESC,id)",
        "CREATE TABLE IF NOT EXISTS investment_state (id INTEGER PRIMARY KEY CHECK(id=1), holdings_at TEXT, activity_at TEXT, activity_start TEXT, activity_end TEXT)",
        "CREATE TABLE IF NOT EXISTS investment_observations (id INTEGER PRIMARY KEY, date TEXT NOT NULL, retrieved_at TEXT NOT NULL, accounts TEXT NOT NULL)",
    ):
        db.execute(statement)


def encode(value):
    return json.dumps(value, default=str, ensure_ascii=False)


def money_minor(value, currency):
    if value is None or currency not in {'USD', 'CAD', 'EUR', 'GBP', 'AUD', 'CHF', 'JPY'}:
        return None
    try:
        number = Decimal(str(value))
        return int(round(number * (1 if currency == 'JPY' else 100))) if number.is_finite() else None
    except (InvalidOperation, ValueError):
        return None


def history_start(today):
    return today.replace(year=today.year - 2, day=min(today.day, monthrange(today.year - 2, today.month)[1]))


def fetch_activity(call, token, start, end):
    """Finish every page before publishing a replacement of the requested range."""
    rows = []
    seen = set()
    total = None
    while total is None or len(rows) < total:
        page = call('/investments/transactions/get', access_token=token,
                    start_date=start, end_date=end, options={'count': 500, 'offset': len(rows)})
        if total is not None and total != page['total_investment_transactions']:
            raise ValueError('Investment history changed during pagination. Sync again.')
        total = page['total_investment_transactions']
        securities = {security['security_id']: security for security in page.get('securities', [])}
        batch = page['investment_transactions']
        if (not batch and len(rows) < total) or len(rows) + len(batch) > total:
            raise ValueError('Investment history pagination was incomplete. Sync again.')
        for tx in batch:
            identifier = tx['investment_transaction_id']
            if identifier in seen or not start <= tx['date'] <= end:
                raise ValueError('Investment history pagination was inconsistent. Sync again.')
            seen.add(identifier)
            rows.append((tx, securities.get(tx.get('security_id'), {})))
    return rows


def save_holdings(db, result, retrieved_at, *, scope=None, observation_accounts=None):
    securities = {security['security_id']: security for security in result.get('securities', [])}
    accounts = {account['account_id']: account for account in result['accounts'] if account['type'] == 'investment'}
    rows = []
    for holding in result['holdings']:
        if holding['account_id'] not in accounts:
            raise ValueError('A holding has no matching investment account.')
        rows.append((holding['account_id'], holding['security_id'], encode(holding),
                     encode(securities.get(holding['security_id'], {})), retrieved_at))
    if scope is None:
        db.execute('DELETE FROM investment_holdings')
    else:
        db.executemany('DELETE FROM investment_holdings WHERE account_id=?', [(identifier,) for identifier in scope])
    db.executemany('INSERT INTO investment_holdings VALUES (?,?,?,?,?)', rows)
    db.execute('INSERT OR IGNORE INTO investment_state(id) VALUES (1)')
    db.execute('UPDATE investment_state SET holdings_at=? WHERE id=1', (retrieved_at,))
    observations = [{'account_id': key, 'name': account['name'],
                     'currency': account['balances'].get('iso_currency_code'),
                     'value_minor': money_minor(account['balances'].get('current'), account['balances'].get('iso_currency_code'))}
                    for key, account in accounts.items()] if observation_accounts is None else observation_accounts
    db.execute('INSERT INTO investment_observations(date,retrieved_at,accounts) VALUES (?,?,?)',
               (date.today().isoformat(), retrieved_at, encode(observations)))


def save_activity(db, rows, start, end, retrieved_at, *, scope=None):
    # Absences in a complete queried range remove corrected/cancelled source records.
    if scope is None:
        db.execute('UPDATE investment_activity SET removed=1 WHERE date BETWEEN ? AND ?', (start, end))
    else:
        db.executemany('UPDATE investment_activity SET removed=1 WHERE account_id=? AND date BETWEEN ? AND ?', [(identifier, start, end) for identifier in scope])
    for tx, security in rows:
        db.execute('''INSERT INTO investment_activity VALUES (?,?,?,?,?,0)
            ON CONFLICT(id) DO UPDATE SET account_id=excluded.account_id,date=excluded.date,
              payload=excluded.payload,security=excluded.security,removed=0''',
                   (tx['investment_transaction_id'], tx['account_id'], tx['date'], encode(tx), encode(security)))
    for tx, _ in rows:
        if tx.get('cancel_transaction_id'):
            db.execute('UPDATE investment_activity SET removed=1 WHERE id=?', (tx['cancel_transaction_id'],))
    db.execute('INSERT OR IGNORE INTO investment_state(id) VALUES (1)')
    db.execute('''UPDATE investment_state SET activity_at=?,activity_start=COALESCE(activity_start,?),activity_end=? WHERE id=1''',
               (retrieved_at, start, end))


def project_investments(db):
    state = db.execute('SELECT holdings_at,activity_at,activity_start,activity_end FROM investment_state WHERE id=1').fetchone()
    accounts = [dict(row) for row in db.execute("SELECT id,name,subtype,currency,current_minor,checked_at FROM accounts WHERE type='investment' AND active=1 ORDER BY name,id")]
    active = {account['id'] for account in accounts}
    holdings = []
    for row in db.execute('SELECT * FROM investment_holdings ORDER BY account_id,security_id'):
        if row['account_id'] not in active:
            continue
        payload, security = json.loads(row['payload']), json.loads(row['security'])
        currency = payload.get('iso_currency_code')
        value = money_minor(payload.get('institution_value'), currency)
        basis = money_minor(payload.get('cost_basis'), currency)
        cash = security.get('type') == 'cash' or bool(security.get('is_cash_equivalent'))
        holdings.append({'account_id': row['account_id'], 'security_id': row['security_id'],
                         'name': security.get('name') or 'Unidentified security', 'ticker': security.get('ticker_symbol'),
                         'type': security.get('type'), 'cash_equivalent': cash,
                         'currency': currency or payload.get('unofficial_currency_code'),
                         'quantity': payload.get('quantity'), 'value_minor': value,
                         'price': payload.get('institution_price'),
                         'price_minor': money_minor(payload.get('institution_price'), currency),
                         'cost_basis_minor': basis,
                         'gain_minor': value - basis if not cash and value is not None and basis is not None else None,
                         'price_as_of': payload.get('institution_price_datetime') or payload.get('institution_price_as_of'),
                         'retrieved_at': row['retrieved_at'], 'provider': payload, 'security': security})
    history = []
    for row in db.execute('SELECT date,retrieved_at,accounts FROM investment_observations ORDER BY date,retrieved_at,id'):
        observation = {'date': row['date'], 'retrieved_at': row['retrieved_at'], 'accounts': json.loads(row['accounts'])}
        if history and history[-1]['date'] == row['date']:
            history[-1] = observation
        else:
            history.append(observation)
    # Balance refreshes and holdings syncs both record account values. Use those
    # observations for charts, excluding copied older values from peer Items.
    if db.execute('SELECT 1 FROM preview_snapshots LIMIT 1').fetchone():
        history = snapshot_history(db, investments=True)
    return {'state': dict(state) if state else None, 'accounts': accounts, 'holdings': holdings, 'history': history}


def project_activity(db, account, start, end, search, kind, page):
    where = ["t.removed=0", "a.active=1"]
    args = []
    for clause, value in [('t.account_id=?', account), ('t.date>=?', start), ('t.date<=?', end)]:
        if value:
            where.append(clause); args.append(value)
    if search:
        where.append("(instr(lower(json_extract(t.payload,'$.name')),lower(?))>0 OR instr(lower(json_extract(t.security,'$.name')),lower(?))>0 OR instr(lower(json_extract(t.security,'$.ticker_symbol')),lower(?))>0)")
        args.extend([search] * 3)
    if kind:
        where.append("json_extract(t.payload,'$.type')=?"); args.append(kind)
    source = ' FROM investment_activity t JOIN accounts a ON a.id=t.account_id WHERE ' + ' AND '.join(where)
    count = db.execute('SELECT COUNT(*)' + source, args).fetchone()[0]
    rows = []
    for row in db.execute('SELECT t.*,a.name AS account_name' + source + ' ORDER BY t.date DESC,t.id LIMIT 25 OFFSET ?', [*args, (page - 1) * 25]):
        tx, security = json.loads(row['payload']), json.loads(row['security'])
        currency = tx.get('iso_currency_code')
        rows.append({'id': row['id'], 'account_id': row['account_id'], 'account_name': row['account_name'],
                     'date': row['date'], 'name': tx.get('name') or tx.get('subtype') or 'Investment activity',
                     'type': tx.get('type'), 'subtype': tx.get('subtype'), 'quantity': tx.get('quantity'),
                     'price': tx.get('price'),
                     'security_name': security.get('name'), 'ticker': security.get('ticker_symbol'),
                     'amount_minor': money_minor(tx.get('amount'), currency),
                     'price_minor': money_minor(tx.get('price'), currency), 'fees_minor': money_minor(tx.get('fees'), currency),
                     'currency': currency or tx.get('unofficial_currency_code'), 'provider': tx, 'security': security})
    return {'rows': rows, 'total': count, 'page': page, 'page_size': 25}
