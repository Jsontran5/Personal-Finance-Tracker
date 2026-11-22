"""Read-only, explicitly selected Personal accounts in the combined test view."""
from datetime import date
import hashlib
import json

from .db import connect, database_lock
from .ledger import account_balances
from .sandbox_preview import record_preview_snapshot
from .debt import ManualDebt


def category(db, row):
    if not row:
        return None
    name, kind = row['name'], row['kind']
    link=db.execute('SELECT local_id FROM category_sync WHERE manual_id=?',(row['id'],)).fetchone()
    if link and db.execute('SELECT 1 FROM preview_categories WHERE id=?',(link[0],)).fetchone():
        return link[0]
    found = db.execute('SELECT id,kind FROM preview_categories WHERE name=?', (name,)).fetchone()
    if found and found['kind'] != kind:
        name = 'Personal · ' + name
        found = db.execute('SELECT id,kind FROM preview_categories WHERE name=?', (name,)).fetchone()
    if found:
        if found['kind'] != kind:
            raise ValueError('Manual category conflicts with an existing combined category.')
        identifier=found['id']
    else:
        identifier=db.execute('INSERT INTO preview_categories(name,kind) VALUES (?,?)', (name, kind)).lastrowid
    db.execute('INSERT OR REPLACE INTO category_sync VALUES (?,?)',(row['id'],identifier))
    return identifier


def refresh_manual(database, combined, *, include_all=False):
    if not database.is_file():
        return
    with database_lock(database), database_lock(combined), connect(database, write=include_all) as source, connect(combined, write=True) as db:
        if include_all:
            db.executemany('INSERT OR IGNORE INTO manual_selection(account_id) VALUES (?)', [(r[0],) for r in source.execute('SELECT id FROM accounts')])
            # A connected-only restore can contain links to an older Manual catalog.
            # Rebuild those links below instead of leaving management pointed at missing IDs.
            manual_ids={r[0] for r in source.execute('SELECT id FROM categories')}
            for link in db.execute('SELECT manual_id FROM category_sync').fetchall():
                if link[0] not in manual_ids:db.execute('DELETE FROM category_sync WHERE manual_id=?',(link[0],))
            # Shared category names, with no changes to existing classifications.
            for row in source.execute('SELECT * FROM categories').fetchall():
                identifier=category(db, dict(row))
                duplicate=db.execute('SELECT 1 FROM preview_categories WHERE name=? AND id<>?',(row['name'],identifier)).fetchone()
                if not duplicate:db.execute('UPDATE preview_categories SET name=? WHERE id=?',(row['name'],identifier))
                archived=source.execute('SELECT archived FROM category_lifecycle WHERE category_id=?',(row['id'],)).fetchone()
                db.execute('INSERT INTO category_lifecycle VALUES (?,?) ON CONFLICT(category_id) DO UPDATE SET archived=excluded.archived',(identifier,archived[0] if archived else 0))
            for row in db.execute('SELECT id,name,kind FROM preview_categories').fetchall():
                if db.execute('SELECT 1 FROM category_sync WHERE local_id=?',(row['id'],)).fetchone():continue
                name=row['name']
                found=source.execute('SELECT id,kind FROM categories WHERE name=?',(name,)).fetchone()
                if found and found['kind']!=row['kind']:name='Connected · '+name;found=source.execute('SELECT id,kind FROM categories WHERE name=?',(name,)).fetchone()
                manual_id=found['id'] if found else source.execute('INSERT INTO categories(name,kind) VALUES (?,?)',(name,row['kind'])).lastrowid
                db.execute('INSERT INTO category_sync VALUES (?,?)',(manual_id,row['id']))
                archived=db.execute('SELECT archived FROM category_lifecycle WHERE category_id=?',(row['id'],)).fetchone()
                source.execute('INSERT OR IGNORE INTO category_lifecycle VALUES (?,?)',(manual_id,archived[0] if archived else 0))
        selected = dict(db.execute('SELECT account_id,fingerprint FROM manual_selection'))
        accounts = {row['id']: row for row in account_balances(source, date.today())}
        transactions = [dict(row) for row in source.execute('SELECT * FROM transactions ORDER BY id')]
        if include_all:
            from .transfers import project_transfers
            transactions = project_transfers(source, transactions, False)
        categories = {row['id']: dict(row) for row in source.execute('SELECT * FROM categories')}
        changed = False
        for mapped in db.execute('SELECT account_id,manual_id FROM connection_accounts WHERE manual_id IS NOT NULL').fetchall():
            if mapped['manual_id'] not in selected or mapped['manual_id'] not in accounts:
                if db.execute('SELECT active FROM accounts WHERE id=?', (mapped['account_id'],)).fetchone()[0]:
                    changed = True
                db.execute('UPDATE accounts SET active=0 WHERE id=?', (mapped['account_id'],))
                db.execute('UPDATE transactions SET removed=1 WHERE account_id=?', (mapped['account_id'],))
        for identifier, previous in selected.items():
            if identifier not in accounts:
                continue
            account = accounts[identifier]
            debt = source.execute('SELECT payload,updated_at FROM manual_debt_details WHERE account_id=?', (identifier,)).fetchone()
            entries = [tx for tx in transactions if identifier in {tx['account_id'], tx['destination_id']}]
            fingerprint = hashlib.sha256(json.dumps([account, entries, categories, dict(debt) if debt else None], sort_keys=True).encode()).hexdigest()
            if fingerprint == previous:
                continue
            changed = True
            key = f'm:{identifier}'
            kind = account['kind']
            provider_type = 'depository' if kind in {'cash', 'checking', 'savings'} else kind
            db.execute('''INSERT INTO accounts(id,name,mask,type,currency,current_minor,source,checked_at,active,subtype)
                VALUES (?,?,NULL,?,?,?,'manual',?,1,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,
                type=excluded.type,currency=excluded.currency,current_minor=excluded.current_minor,source='manual',
                checked_at=excluded.checked_at,active=1,subtype=excluded.subtype''',
                       (key, account['name'], provider_type, account['currency'], account['balance_minor'], account['balance_date'], account.get('subtype') or kind))
            db.execute('''INSERT INTO connection_accounts(account_id,manual_id,included) VALUES (?,?,?)
                ON CONFLICT(account_id) DO UPDATE SET included=excluded.included''', (key, identifier, account['include_in_net_worth']))
            if kind in {'loan', 'credit'}:
                payload = debt['payload'] if debt else ManualDebt(subtype='credit card' if kind == 'credit' else 'other').model_dump_json()
                db.execute('''INSERT INTO manual_debt_mirror VALUES (?,?,?) ON CONFLICT(account_id)
                    DO UPDATE SET payload=excluded.payload,updated_at=excluded.updated_at''', (key, payload, debt['updated_at'] if debt else None))
            db.execute('UPDATE transactions SET removed=1 WHERE account_id=?', (key,))
            for tx in entries:
                incoming = tx['destination_id'] == identifier
                entry_id = f'm:{tx["id"]}:' + ('in' if incoming else 'out')
                amount = tx['amount_minor'] * (-1 if incoming or tx['kind'] in {'income', 'refund'} else 1)
                mirror_kind = 'transfer' if tx.get('transfer_pair_id') else tx['kind']
                db.execute('''INSERT INTO transactions VALUES (?,?,?,?,?,?,?,0,0) ON CONFLICT(id)
                    DO UPDATE SET date=excluded.date,name=excluded.name,amount_minor=excluded.amount_minor,
                    currency=excluded.currency,kind=excluded.kind,removed=0''',
                           (entry_id, key, tx['date'], tx['merchant'], amount, account['currency'], mirror_kind))
                category_id = category(db, categories.get(tx['category_id'])) if mirror_kind not in {'transfer', 'investment'} else None
                db.execute('''INSERT INTO preview_edits VALUES (?,?,?,?,?) ON CONFLICT(transaction_id)
                    DO UPDATE SET merchant=excluded.merchant,kind=excluded.kind,category_id=excluded.category_id,notes=excluded.notes''',
                           (entry_id, tx['merchant'], mirror_kind, category_id, tx['notes']))
                db.execute('''INSERT INTO transaction_details VALUES (?,?,?) ON CONFLICT(transaction_id)
                    DO UPDATE SET payload=excluded.payload,retrieved_at=excluded.retrieved_at''',
                           (entry_id, json.dumps({**tx, '_finance_manual': True, 'amount': amount / (1 if account['currency'] == 'JPY' else 100)}), tx['created_at']))
            db.execute('UPDATE manual_selection SET fingerprint=? WHERE account_id=?', (fingerprint, identifier))
        if changed:
            record_preview_snapshot(db)
