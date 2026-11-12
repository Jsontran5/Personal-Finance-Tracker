"""Local transaction review and exact-merchant rules. No provider requests."""
from datetime import datetime, timezone
import hashlib
import json
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .db import connect
from .transfers import project_transfers, transfer_router, has_confirmed_link

KINDS = Literal['expense', 'income', 'refund', 'transfer', 'investment']


def initialize_review(db):
    db.execute("""CREATE TABLE IF NOT EXISTS transaction_reviews (
        transaction_id TEXT PRIMARY KEY,state TEXT NOT NULL CHECK(state IN ('flagged','reviewed')),
        origin TEXT NOT NULL CHECK(origin IN ('user','rule')),updated_at TEXT NOT NULL)""")
    db.execute("""CREATE TABLE IF NOT EXISTS merchant_rules (
        id INTEGER PRIMARY KEY,merchant TEXT NOT NULL,account_id TEXT,match_kind TEXT NOT NULL,
        kind TEXT NOT NULL,category_id INTEGER,active INTEGER NOT NULL DEFAULT 1)""")


def mark(db, identifier, state='reviewed', origin='user'):
    db.execute("""INSERT INTO transaction_reviews VALUES (?,?,?,?) ON CONFLICT(transaction_id)
        DO UPDATE SET state=excluded.state,origin=excluded.origin,updated_at=excluded.updated_at""",
               (str(identifier), state, origin, datetime.now(timezone.utc).isoformat()))


def normalized(value):
    return ' '.join(value.split()).casefold()


def review_rows(db, sandbox):
    if sandbox:
        sql = """SELECT t.rowid AS id,t.id AS source_id,a.rowid AS account_id,t.account_id AS source_account,
          t.name AS source_merchant,COALESCE(e.merchant,t.name) AS merchant,
          COALESCE(e.kind,CASE WHEN t.kind='credit' THEN 'refund' ELSE t.kind END) AS kind,
          e.category_id,COALESCE(e.notes,'') AS notes,t.date,t.amount_minor,t.currency,t.pending,
          CASE WHEN t.kind='credit' AND e.kind IS NULL THEN 1 ELSE 0 END AS needs_review,
          CASE WHEN e.transaction_id IS NOT NULL THEN 1 ELSE 0 END AS has_edit,
          NULL AS destination_id,r.state,r.origin,a.name AS account_name
          FROM transactions t JOIN accounts a ON a.id=t.account_id
          LEFT JOIN preview_edits e ON e.transaction_id=t.id
          LEFT JOIN transaction_reviews r ON r.transaction_id=t.id WHERE t.removed=0"""
    else:
        sql = """SELECT t.id,t.id AS source_id,t.account_id,t.account_id AS source_account,
          t.merchant AS source_merchant,t.merchant,t.kind,t.category_id,t.notes,t.date,t.amount_minor,a.currency,
          0 AS pending,0 AS needs_review,0 AS has_edit,t.destination_id,r.state,r.origin,a.name AS account_name
          FROM transactions t JOIN accounts a ON a.id=t.account_id
          LEFT JOIN transaction_reviews r ON r.transaction_id=CAST(t.id AS TEXT)"""
    return project_transfers(db, [dict(row) for row in db.execute(sql + ' ORDER BY t.date DESC,t.rowid DESC')], sandbox)


def status(row):
    if row['state'] != 'flagged' and row.get('transfer_pair_id'):
        return 'reviewed'
    return 'needs_review' if row['needs_review'] or row['state'] == 'flagged' else 'reviewed' if row['state'] == 'reviewed' else 'not_required'


def category_valid(db, sandbox, category_id, kind):
    if category_id is None:
        return True
    if kind in {'transfer', 'investment'}:
        return False
    table = 'preview_categories' if sandbox else 'categories'
    row = db.execute(f'SELECT kind FROM {table} WHERE id=?', (category_id,)).fetchone()
    return bool(row and row['kind'] == ('income' if kind == 'income' else 'expense'))


class RuleMatch(BaseModel):
    merchant: str = Field(min_length=1, max_length=120)
    account_id: int | None = Field(default=None, gt=0)
    match_kind: KINDS
    future: bool = False


class Action(BaseModel):
    operation: Literal['classify', 'review', 'flag'] = 'classify'
    kind: KINDS | None = None
    category_id: int | None = Field(default=None, gt=0)
    rule: RuleMatch | None = None


class Batch(BaseModel):
    ids: list[int] = Field(default_factory=list, max_length=10000)
    action: Action
    preview_token: str | None = None


def matches(row, merchant, account_id, kind):
    return normalized(row['source_merchant']) == normalized(merchant) and (account_id is None or row['account_id'] == account_id) and row['kind'] == kind


def reason(db, row, action, sandbox, rule=False):
    if row['pending']:
        return 'Pending: wait until posted'
    protected = (row['has_edit'] and row['origin'] != 'rule') if sandbox else (row['origin'] == 'user' or (row['category_id'] is not None and row['origin'] != 'rule'))
    if rule and protected:
        return 'Individual edit preserved'
    if action.operation == 'review':
        return 'Classify ambiguous credit first' if row['needs_review'] else None
    if action.operation == 'flag':
        return None
    if has_confirmed_link(db, row['source_id']):
        return 'Unlink confirmed transfer before changing classification'
    if action.kind is None:
        return 'Choose a transaction type'
    if sandbox and ((row['amount_minor'] > 0 and action.kind in {'income', 'refund'}) or (row['amount_minor'] < 0 and action.kind == 'expense')):
        return 'Type does not match money direction'
    if not sandbox and action.kind == 'transfer' and row['destination_id'] is None:
        return 'Choose a transfer destination in the individual editor'
    if rule and not sandbox and action.kind != row['kind']:
        return 'Manual transaction type preserved'
    if not category_valid(db, sandbox, action.category_id, action.kind):
        return 'Category does not match type'
    return None


def write_action(db, row, action, sandbox, origin='user'):
    if action.operation == 'classify':
        if sandbox:
            db.execute("""INSERT INTO preview_edits VALUES (?,?,?,?,?) ON CONFLICT(transaction_id)
                DO UPDATE SET kind=excluded.kind,category_id=excluded.category_id""",
                       (row['source_id'], row['merchant'], action.kind, action.category_id, row['notes']))
        else:
            db.execute('UPDATE transactions SET kind=?,category_id=?,destination_id=? WHERE id=?',
                       (action.kind, action.category_id, row['destination_id'] if action.kind == 'transfer' else None, row['id']))
    mark(db, row['source_id'], 'flagged' if action.operation == 'flag' else 'reviewed', origin)


def apply_rules(db, sandbox, source_ids):
    if not source_ids:
        return
    wanted = {str(identifier) for identifier in source_ids}
    if sandbox:
        # A provider correction must not leave an automatic income/refund override on a debit (or vice versa).
        for row in review_rows(db, sandbox):
            if str(row['source_id']) in wanted and row['origin'] == 'rule' and ((row['amount_minor'] > 0 and row['kind'] in {'income', 'refund'}) or (row['amount_minor'] < 0 and row['kind'] == 'expense')):
                db.execute('DELETE FROM preview_edits WHERE transaction_id=?', (row['source_id'],))
                db.execute('DELETE FROM transaction_reviews WHERE transaction_id=?', (row['source_id'],))
    rules = [dict(row) for row in db.execute('SELECT * FROM merchant_rules WHERE active=1 ORDER BY account_id IS NULL,id')]
    if not rules:
        return
    for row in review_rows(db, sandbox):
        if str(row['source_id']) not in wanted:
            continue
        for rule in rules:
            # Match against source classification, not the result of a previous rule.
            if sandbox:
                source_kind = db.execute('SELECT kind FROM transactions WHERE id=?', (row['source_id'],)).fetchone()[0]
                source_kind = 'refund' if source_kind == 'credit' else source_kind
            else:
                source_kind = row['kind']
            if normalized(row['source_merchant']) != normalized(rule['merchant']) or (rule['account_id'] is not None and str(row['source_account']) != rule['account_id']) or source_kind != rule['match_kind']:
                continue
            action = Action(kind=rule['kind'], category_id=rule['category_id'])
            if reason(db, row, action, sandbox, rule=True) is None:
                write_action(db, row, action, sandbox, 'rule')
            break


def review_router(database, sandbox=False, initialize=None, after_write=None):
    router = APIRouter(prefix='/review')
    router.include_router(transfer_router(database, sandbox, initialize))

    def prepare():
        if initialize:
            initialize()

    def preview(db, batch):
        rows = review_rows(db, sandbox)
        action = batch.action
        if action.rule:
            if action.operation != 'classify' or not normalized(action.rule.merchant):
                raise HTTPException(422, 'A merchant rule needs a merchant and classification.')
            rows = [row for row in rows if matches(row, action.rule.merchant, action.rule.account_id, action.rule.match_kind)]
        else:
            wanted = set(batch.ids)
            if not wanted or any(identifier <= 0 for identifier in wanted):
                raise HTTPException(422, 'Select transactions first.')
            rows = [row for row in rows if row['id'] in wanted]
            if {row['id'] for row in rows} != wanted:
                raise HTTPException(409, 'Some selected transactions changed or were removed. Refresh and select again.')
        if len(rows) > 10000:
            raise HTTPException(422, 'More than 10,000 matches. Narrow this rule to one account.')
        results = [{**row, 'skip_reason': reason(db, row, action, sandbox, rule=bool(action.rule))} for row in rows]
        digest = hashlib.sha256(json.dumps([results, action.model_dump()], sort_keys=True).encode()).hexdigest()
        return results, digest

    @router.get('')
    def get_review():
        prepare()
        with connect(database) as db:
            rows = review_rows(db, sandbox)
            counts = {name: sum(status(row) == name for row in rows) for name in ['needs_review', 'reviewed', 'not_required']}
            categories = dict(db.execute('SELECT id,name FROM ' + ('preview_categories' if sandbox else 'categories')))
            accounts = dict(db.execute('SELECT id,name FROM accounts'))
            fields = ['id', 'account_id', 'destination_id', 'category_id', 'kind', 'currency', 'merchant', 'notes', 'date', 'account_name', 'pending', 'needs_review', 'source_merchant', 'transfer_pair_id', 'transfer_kind', 'transfer_role', 'transfer_other_account']
            items = [{**{key: row[key] for key in fields}, 'amount_minor': abs(row['amount_minor']),
                      'category_name': categories.get(row['category_id']), 'destination_name': accounts.get(row['destination_id']),
                      'review_status': status(row)} for row in rows]
            return {'counts': counts, 'uncategorized': sum(row['category_id'] is None and row['kind'] in {'expense', 'income', 'refund'} for row in rows),
                    'items': items,
                    'rows': [{'id': row['id'], 'review_status': status(row)} for row in rows],
                    'rules': [dict(row) for row in db.execute('SELECT * FROM merchant_rules ORDER BY id')]}

    @router.post('/preview')
    def preview_action(batch: Batch):
        prepare()
        with connect(database) as db:
            rows, token = preview(db, batch)
            return {'preview_token': token, 'matched': len(rows), 'applicable': sum(row['skip_reason'] is None for row in rows),
                    'rows': [{'id': row['id'], 'date': row['date'], 'merchant': row['merchant'], 'account_name': row['account_name'],
                              'kind': row['kind'], 'skip_reason': row['skip_reason']} for row in rows[:50]]}

    @router.post('/apply')
    def apply_action(batch: Batch):
        prepare()
        with connect(database, write=True) as db:
            rows, token = preview(db, batch)
            if batch.preview_token != token:
                raise HTTPException(409, 'Preview is missing or out of date. Preview again before applying.')
            action = batch.action
            if action.rule:
                match = action.rule
                if action.kind is None or not category_valid(db, sandbox, action.category_id, action.kind):
                    raise HTTPException(422, 'Choose a matching type and category.')
                source_account = None
                if match.account_id is not None:
                    column = 'rowid' if sandbox else 'id'
                    account = db.execute(f'SELECT id FROM accounts WHERE {column}=?', (match.account_id,)).fetchone()
                    if not account:
                        raise HTTPException(422, 'Choose an existing account.')
                    source_account = str(account['id'])
                if db.execute('SELECT 1 FROM merchant_rules WHERE merchant=? AND account_id IS ? AND match_kind=? AND active=1',
                              (normalized(match.merchant), source_account, match.match_kind)).fetchone():
                    raise HTTPException(409, 'This merchant rule already exists. Disable it before replacing it with a different match.')
                db.execute('INSERT INTO merchant_rules(merchant,account_id,match_kind,kind,category_id,active) VALUES (?,?,?,?,?,?)',
                           (normalized(match.merchant), source_account, match.match_kind, action.kind, action.category_id, int(match.future)))
            applied = 0
            for row in rows:
                if row['skip_reason'] is None:
                    write_action(db, row, action, sandbox, 'rule' if action.rule else 'user')
                    applied += 1
            if after_write and applied and action.operation == 'classify':
                after_write(db)
        return {'applied': applied, 'skipped': len(rows) - applied}

    @router.put('/rules/{rule_id}')
    def toggle_rule(rule_id: int, enabled: dict):
        if type(enabled.get('active')) is not bool:
            raise HTTPException(422, 'Supply active as true or false.')
        prepare()
        with connect(database, write=True) as db:
            if enabled['active']:
                current = db.execute('SELECT * FROM merchant_rules WHERE id=?', (rule_id,)).fetchone()
                if current and db.execute('SELECT 1 FROM merchant_rules WHERE merchant=? AND account_id IS ? AND match_kind=? AND active=1 AND id<>?',
                                          (current['merchant'], current['account_id'], current['match_kind'], rule_id)).fetchone():
                    raise HTTPException(409, 'Disable the other active rule with this exact match first.')
            if not db.execute('UPDATE merchant_rules SET active=? WHERE id=?', (int(enabled['active']), rule_id)).rowcount:
                raise HTTPException(404, 'Rule not found.')
        return {'id': rule_id}

    return router
