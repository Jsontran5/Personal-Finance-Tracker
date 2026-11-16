"""Fetch timestamps and product-specific failures, without inferring bank freshness."""


def account_freshness(db, accounts):
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_schema WHERE type='table'")}
    multi = 'multi_connections' in tables
    owners = {row['account_id']: row['connection_id'] for row in db.execute('SELECT account_id,connection_id FROM connection_accounts')} if multi else {}
    connections = {row['id']: dict(row) for row in db.execute('SELECT * FROM multi_connections' if multi else 'SELECT * FROM connection')}
    failures = {}
    if 'live_sync_results' in tables:
        for row in db.execute('SELECT * FROM live_sync_results'):
            failures.setdefault(row['connection_id'], {})[row['product']] = dict(row)
    ids = {row['rowid']: row['id'] for row in db.execute('SELECT rowid,id FROM accounts')}
    for account in accounts:
        if account['balance_source'] == 'manual':
            account['sync'] = None
            continue
        owner = owners.get(ids[account['id']]) if multi else 1
        connection = connections.get(owner, {})
        investment = account['provider_type'] == 'investment'
        timestamps = {'balances': account['balance_date']}
        if investment:
            timestamps.update(holdings=connection.get('holdings_at'), activity=connection.get('activity_at'))
        elif account['provider_type'] in {'depository', 'credit'} and connection.get('profile', 'banking') == 'banking':
            timestamps['transactions'] = connection.get('synced_at')
        account['sync'] = {'timestamps': timestamps, 'errors': {
            product: {'code': row['error'], 'attempted_at': row['attempted_at']}
            for product, row in failures.get(owner, {}).items() if product in timestamps and row['error']
        }, 'needs_reauth': bool(connection.get('needs_reauth')), 'connection_error': connection.get('last_error')}
    return accounts


def record_sync_result(db, identifier, product, attempted_at, error=None):
    db.execute('''INSERT INTO live_sync_results VALUES (?,?,?,?)
        ON CONFLICT(connection_id,product) DO UPDATE SET attempted_at=excluded.attempted_at,error=excluded.error''',
        (identifier, product, attempted_at, error))
