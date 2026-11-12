"""Suggested pairs and reversible reporting overlays; never move or rewrite money."""
from bisect import bisect_left, bisect_right
from collections import defaultdict, Counter
from datetime import date, datetime, timezone
import hashlib
import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .db import connect


def initialize_transfers(db):
    db.execute("""CREATE TABLE IF NOT EXISTS transfer_links (
        id INTEGER PRIMARY KEY,outgoing_id TEXT NOT NULL,incoming_id TEXT NOT NULL,
        kind TEXT NOT NULL CHECK(kind IN ('transfer','card_payment')),
        state TEXT NOT NULL CHECK(state IN ('confirmed','dismissed')),signature TEXT NOT NULL,
        recorded_at TEXT NOT NULL,UNIQUE(outgoing_id,incoming_id))""")


def transfer_rows(db, sandbox):
    if sandbox:
        sql = """SELECT t.rowid AS id,t.id AS source_id,a.rowid AS account_id,t.account_id AS source_account,
          a.name AS account_name,a.type AS account_type,t.amount_minor,t.currency,t.date,t.pending,t.removed,
          CASE WHEN t.kind='transfer' THEN 1 ELSE 0 END AS transfer_hint,
          COALESCE(e.kind,CASE WHEN t.kind='credit' THEN 'refund' ELSE t.kind END) AS kind,
          CASE WHEN t.kind='credit' AND e.kind IS NULL THEN 1 ELSE 0 END AS needs_review,
          COALESCE(e.merchant,t.name) AS merchant,COALESCE(e.notes,'') AS notes,c.name AS category_name
          FROM transactions t JOIN accounts a ON a.id=t.account_id
          LEFT JOIN preview_edits e ON e.transaction_id=t.id LEFT JOIN preview_categories c ON c.id=e.category_id"""
    else:
        sql = """SELECT t.id,t.id AS source_id,t.account_id,t.account_id AS source_account,
          a.name AS account_name,a.kind AS account_type,
          CASE WHEN t.kind IN ('income','refund') THEN -t.amount_minor ELSE t.amount_minor END AS amount_minor,
          a.currency,t.date,0 AS pending,0 AS removed,0 AS transfer_hint,0 AS needs_review,t.kind,t.merchant,t.notes,c.name AS category_name
          FROM transactions t JOIN accounts a ON a.id=t.account_id LEFT JOIN categories c ON c.id=t.category_id
          WHERE t.kind NOT IN ('transfer','investment')"""
    return [{**dict(row), 'source_id': str(row['source_id'])} for row in db.execute(sql)]


def compatible(outgoing, incoming):
    if not outgoing or not incoming:
        return False
    try:
        gap = abs((date.fromisoformat(outgoing['date']) - date.fromisoformat(incoming['date'])).days)
    except (ValueError, TypeError):
        return False
    return (not any(row['pending'] or row['removed'] for row in (outgoing, incoming))
            and outgoing['amount_minor'] > 0 and outgoing['amount_minor'] == -incoming['amount_minor']
            and outgoing['source_account'] != incoming['source_account'] and outgoing['currency'] is not None
            and outgoing['currency'] == incoming['currency'] and gap <= 5
            and outgoing['account_type'] != 'loan' and incoming['account_type'] != 'loan'
            and outgoing['kind'] != 'investment' and incoming['kind'] != 'investment')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def likely(outgoing, incoming):
    return any(row['transfer_hint'] for row in (outgoing, incoming)) or any(word in (outgoing['merchant'] + ' ' + incoming['merchant']).casefold() for word in ['transfer', 'payment', 'autopay'])


def signature(outgoing, incoming):
    fields = ['source_id', 'source_account', 'account_type', 'amount_minor', 'currency', 'date', 'pending', 'removed']
    return digest([[row[key] for key in fields] for row in (outgoing, incoming)])


def has_confirmed_link(db, identifier):
    return db.execute("SELECT 1 FROM transfer_links WHERE state='confirmed' AND (outgoing_id=? OR incoming_id=?)", (str(identifier), str(identifier))).fetchone() is not None


def require_unlinked(db, identifier):
    if has_confirmed_link(db, identifier):
        raise HTTPException(409, 'Unlink this confirmed transfer in Transfer matches before editing or deleting it.')


def project_transfers(db, transactions, sandbox):
    links = db.execute("SELECT * FROM transfer_links WHERE state='confirmed'").fetchall()
    rows = {row['source_id']: row for row in transfer_rows(db, sandbox)} if links else {}
    paired = {}
    for link in links:
        out, inc = rows.get(link['outgoing_id']), rows.get(link['incoming_id'])
        if compatible(out, inc) and link['signature'] == signature(out, inc):
            paired[out['id']] = (link, 'outgoing', inc['account_name'])
            paired[inc['id']] = (link, 'incoming', out['account_name'])
    for tx in transactions:
        item = paired.get(tx['id'])
        tx.update({'transfer_pair_id': item[0]['id'] if item else None,
                   'transfer_kind': item[0]['kind'] if item else None,
                   'transfer_role': item[1] if item else None,
                   'transfer_other_account': item[2] if item else None})
        if item:
            tx['needs_review'] = 0
    return transactions


def view_pair(out, inc):
    fields = ['id', 'account_id', 'account_name', 'account_type', 'merchant', 'date', 'currency', 'kind', 'category_name', 'notes', 'needs_review']
    def leg(row):
        return {**{key: row[key] for key in fields}, 'amount_minor': abs(row['amount_minor'])}
    days = abs((date.fromisoformat(out['date']) - date.fromisoformat(inc['date'])).days)
    kind = 'card_payment' if inc['account_type'] == 'credit' and out['account_type'] != 'credit' else 'transfer'
    clues = ['Equal amount and currency', 'Different accounts', f'Posted {days} day(s) apart']
    if any(row['transfer_hint'] for row in (out, inc)):
        clues.append('A source entry is classified as a transfer')
    if any(word in (out['merchant'] + ' ' + inc['merchant']).casefold() for word in ['transfer', 'payment', 'autopay']):
        clues.append('Transfer/payment wording in a description')
    return {'outgoing': leg(out), 'incoming': leg(inc), 'kind': kind, 'reasons': clues}


class PairRequest(BaseModel):
    outgoing_id: int = Field(gt=0)
    incoming_id: int = Field(gt=0)
    preview_token: str | None = None


def transfer_router(database, sandbox=False, initialize=None):
    router = APIRouter(prefix='/transfers')

    def prepare():
        if initialize:
            initialize()

    def pair(db, request, require_available=True):
        rows = {row['id']: row for row in transfer_rows(db, sandbox)}
        out, inc = rows.get(request.outgoing_id), rows.get(request.incoming_id)
        if not compatible(out, inc):
            raise HTTPException(409, 'This pair no longer matches. Refresh suggestions; use different accounts, equal currency/amount, and posted dates within five days.')
        if require_available and any(has_confirmed_link(db, row['source_id']) for row in (out, inc)):
            raise HTTPException(409, 'One of these transactions is already linked. Unlink its existing pair first.')
        return out, inc

    @router.get('')
    def get_pairs():
        prepare()
        with connect(database) as db:
            rows = transfer_rows(db, sandbox)
            by_source = {row['source_id']: row for row in rows}
            links = [dict(row) for row in db.execute('SELECT * FROM transfer_links ORDER BY id DESC')]
            occupied = {link[key] for link in links if link['state'] == 'confirmed' for key in ['outgoing_id', 'incoming_id']}
            dismissed = {(link['outgoing_id'], link['incoming_id']): link['signature'] for link in links if link['state'] == 'dismissed'}
            buckets = defaultdict(list)
            for row in rows:
                if row['pending'] or row['removed'] or row['kind'] == 'investment' or row['source_id'] in occupied or row['currency'] is None:
                    continue
                if row['amount_minor'] < 0:
                    buckets[(row['currency'], -row['amount_minor'])].append(row)
            for bucket in buckets.values():
                bucket.sort(key=lambda row: row['date'])
            candidates = []
            truncated = False
            for out in sorted(rows, key=lambda row: (row['date'], row['id']), reverse=True):
                if out['amount_minor'] <= 0 or out['source_id'] in occupied or out['pending'] or out['removed']:
                    continue
                bucket = buckets.get((out['currency'], out['amount_minor']), [])
                dates = [date.fromisoformat(row['date']).toordinal() for row in bucket]
                day = date.fromisoformat(out['date']).toordinal()
                options = sorted(bucket[bisect_left(dates, day - 5):bisect_right(dates, day + 5)], key=lambda row: abs(date.fromisoformat(row['date']).toordinal() - day))
                for inc in options:
                    if compatible(out, inc) and likely(out, inc) and dismissed.get((out['source_id'], inc['source_id'])) != signature(out, inc):
                        candidates.append(view_pair(out, inc))
                        if len(candidates) >= 500:
                            truncated = True
                            break
                if truncated:
                    break
            degrees = Counter(identifier for item in candidates for identifier in [item['outgoing']['id'], item['incoming']['id']])
            for item in candidates:
                item['multiple_options'] = degrees[item['outgoing']['id']] > 1 or degrees[item['incoming']['id']] > 1
            saved = []
            for link in links:
                out, inc = by_source.get(link['outgoing_id']), by_source.get(link['incoming_id'])
                valid = compatible(out, inc) and link['signature'] == signature(out, inc)
                saved.append({'id': link['id'], 'state': link['state'], 'valid': valid,
                              'pair': view_pair(out, inc) if out and inc else None,
                              'description': 'Source changed or removed; reporting exclusion is inactive until you unlink and review again.' if not valid and link['state'] == 'confirmed' else None})
            return {'suggestions': candidates, 'saved': saved, 'truncated': truncated}

    @router.post('/preview')
    def preview(request: PairRequest):
        prepare()
        with connect(database) as db:
            out, inc = pair(db, request)
            return {'pair': view_pair(out, inc), 'preview_token': digest([out, inc]),
                    'effect': 'Confirming excludes both entries from income and spending while this link remains valid. Original types, categories, notes and balances are retained. Unlink restores normal reporting.'}

    @router.post('/confirm')
    def confirm(request: PairRequest):
        prepare()
        with connect(database, write=True) as db:
            out, inc = pair(db, request)
            if request.preview_token != digest([out, inc]):
                raise HTTPException(409, 'Preview is missing or stale. Review this pair again before confirming.')
            db.execute("""INSERT INTO transfer_links(outgoing_id,incoming_id,kind,state,signature,recorded_at)
                VALUES (?,?,?,'confirmed',?,?) ON CONFLICT(outgoing_id,incoming_id) DO UPDATE SET
                kind=excluded.kind,state=excluded.state,signature=excluded.signature,recorded_at=excluded.recorded_at""",
                       (out['source_id'], inc['source_id'], view_pair(out, inc)['kind'], signature(out, inc), datetime.now(timezone.utc).isoformat()))
        return {'confirmed': True}

    @router.post('/dismiss')
    def dismiss(request: PairRequest):
        prepare()
        with connect(database, write=True) as db:
            out, inc = pair(db, request)
            db.execute("""INSERT INTO transfer_links(outgoing_id,incoming_id,kind,state,signature,recorded_at)
                VALUES (?,?,?,'dismissed',?,?) ON CONFLICT(outgoing_id,incoming_id) DO UPDATE SET
                state=excluded.state,signature=excluded.signature,recorded_at=excluded.recorded_at""",
                       (out['source_id'], inc['source_id'], view_pair(out, inc)['kind'], signature(out, inc), datetime.now(timezone.utc).isoformat()))
        return {'dismissed': True}

    @router.delete('/{link_id}')
    def remove(link_id: int):
        prepare()
        with connect(database, write=True) as db:
            if not db.execute('DELETE FROM transfer_links WHERE id=?', (link_id,)).rowcount:
                raise HTTPException(404, 'Link not found. Refresh the list.')
        return {'removed': True}

    return router
