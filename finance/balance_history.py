"""Read-only observed balances and explicitly estimated banking history.

Estimates never enter snapshot tables or change current balances. Provider
transaction signs, including transfers, are used independently of report edits.
"""
from datetime import date, datetime, timedelta


def local_day(value):
    if not value:
        return None
    try:
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return stamp.astimezone().date() if stamp.tzinfo else stamp.date()
    except (ValueError, TypeError):
        return None


def snapshot_history(db, *, investments=False):
    """One latest daily snapshot, retaining only that day's observed values.

    Copies of another institution's older balance remain stored for recovery,
    but are null in this day's chart rather than presented as a new observation.
    """
    points = {row['id']: {'date': row['date'], 'net_worth_minor': row['assets_minor'] - row['liabilities_minor'],
                          'assets_minor': row['assets_minor'], 'liabilities_minor': row['liabilities_minor'],
                          'accounts': []}
              for row in db.execute('''SELECT * FROM preview_snapshots
                  WHERE id IN (SELECT MAX(id) FROM preview_snapshots GROUP BY date) ORDER BY date''')}
    for row in db.execute('''SELECT s.*,a.rowid AS row_id,a.type FROM preview_snapshot_accounts s
                             JOIN accounts a ON a.id=s.account_id ORDER BY s.snapshot_id,a.rowid'''):
        point = points.get(row['snapshot_id'])
        if point is None or investments and row['type'] != 'investment':
            continue
        observed = local_day(row['checked_at'])
        valid = row['currency'] == 'USD' and observed is not None and observed.isoformat() == point['date']
        value = row['balance_minor'] if valid else None
        point['accounts'].append({'account_id': row['account_id'] if investments else row['row_id'],
                                  'currency': row['currency'], 'value_minor': value, 'balance_minor': value,
                                  'observed_at': row['checked_at'], 'source': row['source']})
    return list(points.values())


def banking_estimates(db):
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_schema WHERE type='table'")}
    results = []
    for account in db.execute('SELECT rowid AS row_id,* FROM accounts WHERE active=1 ORDER BY rowid'):
        result = {'id': account['row_id'], 'name': account['name'], 'supported': False, 'reason': None,
                  'anchor_date': None, 'anchor_at': None, 'anchor_source': None, 'start_date': None,
                  'transaction_count': 0, 'newer_transaction_count': 0, 'points': [], 'max_difference_minor': None}
        results.append(result)
        if account['currency'] != 'USD':
            result['reason'] = 'Only USD banking estimates are supported.'
        elif account['type'] not in {'depository', 'credit'}:
            result['reason'] = 'Investments, loans and other assets need different historical data.'
        elif account['source'] == 'manual':
            result['reason'] = 'Manual accounts use their recorded balances; bank estimates use synced accounts.'
        if result['reason']:
            continue
        if 'connection_accounts' in tables:
            connection = db.execute('''SELECT c.history_status FROM connection_accounts m
                JOIN multi_connections c ON c.id=m.connection_id WHERE m.account_id=?''', (account['id'],)).fetchone()
        else:
            connection = db.execute('SELECT history_status FROM connection WHERE id=1').fetchone()
        if not connection or connection['history_status'] != 'HISTORICAL_UPDATE_COMPLETE':
            result['reason'] = 'Sync transactions until the historical update is complete.'
            continue
        observations = {}
        for row in db.execute('''SELECT s.*,p.date FROM preview_snapshot_accounts s
            JOIN preview_snapshots p ON p.id=s.snapshot_id WHERE s.account_id=?
            AND s.currency='USD' AND s.balance_minor IS NOT NULL ORDER BY s.snapshot_id''', (account['id'],)):
            observed = local_day(row['checked_at'])
            if observed and observed.isoformat() == row['date']:
                observations[observed] = dict(row)
        if not observations:
            result['reason'] = 'Refresh balances to save a dated banking balance first.'
            continue
        anchor_day = max(observations)
        anchor = observations[anchor_day]
        result.update(anchor_date=anchor_day.isoformat(), anchor_at=anchor['checked_at'], anchor_source=anchor['source'])
        rows = [dict(row) for row in db.execute('''SELECT date,amount_minor,currency FROM transactions
            WHERE account_id=? AND removed=0 AND pending=0 ORDER BY date,id''', (account['id'],))]
        result['newer_transaction_count'] = sum(row['date'] > anchor_day.isoformat() for row in rows)
        cutoff = max(date.min, anchor_day - timedelta(days=min(730, anchor_day.toordinal() - 1)))
        rows = [row for row in rows if cutoff.isoformat() <= row['date'] <= anchor_day.isoformat()]
        if not rows:
            result['reason'] = 'No posted transactions are available on or before the balance anchor.'
            continue
        if any(row['currency'] != 'USD' for row in rows):
            result['reason'] = 'Mixed transaction currencies prevent a reliable USD reconstruction.'
            continue
        first = date.fromisoformat(rows[0]['date'])
        amounts = {}
        for row in rows:
            amounts[row['date']] = amounts.get(row['date'], 0) + row['amount_minor']
        result.update(supported=True, start_date=first.isoformat(), transaction_count=len(rows))
        balance, current, difference = anchor['balance_minor'], anchor_day, []
        while current >= first:
            result['points'].append({'date': current.isoformat(), 'balance_minor': balance})
            if current in observations:
                difference.append(abs(balance - observations[current]['balance_minor']))
            # Raw Plaid outflows are positive for both deposits and credit cards;
            # the app already stores credit liabilities as negative balances.
            balance += amounts.get(current.isoformat(), 0)
            if current == date.min:
                break
            current -= timedelta(days=1)
        result['points'].reverse()
        result['max_difference_minor'] = max(difference) if len(difference) > 1 else None
    return {'accounts': results, 'maximum_days': 730,
            'method': 'Reverse posted signed banking movements from the latest saved balance. Coverage may be incomplete; anchors may be intraday or cached.'}
