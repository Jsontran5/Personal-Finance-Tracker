"""Combined Sandbox workspace: independent Items and a shared reporting ledger."""
from datetime import date, datetime, timedelta
import json
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from .db import connect, database_lock
from .plaid_sandbox import SCHEMA, credentials, plaid_client, PlaidError, now, save_accounts, save_transactions, changes
from .plaid_vault import protect
from .sandbox_preview import initialize_preview, record_preview_snapshot, sandbox_preview_router
from .investments import fetch_activity, history_start, save_holdings, save_activity, project_investments, project_activity
from .debt import save_liabilities, project_liability
from .scenarios import sandbox_path


def initialize_combined(db, *, production=False):
    version = db.execute('PRAGMA user_version').fetchone()[0]
    if version in {9, 10, 13, 14} and not production:
        raise ValueError('A Production database cannot be opened as Sandbox.')
    for statement in SCHEMA:
        db.execute(statement)
    # Existing Live databases also need additive reporting and category upgrades.
    # These must run even when optional startup maintenance is disabled.
    initialize_preview(db)
    db.execute('''CREATE TABLE IF NOT EXISTS multi_connections (
        id INTEGER PRIMARY KEY, institution_id TEXT NOT NULL UNIQUE,name TEXT NOT NULL,
        profile TEXT NOT NULL CHECK(profile IN ('banking','investments')),item_id TEXT UNIQUE,token BLOB,
        cursor TEXT,history_status TEXT,synced_at TEXT,balance_checked_at TEXT,
        needs_reauth INTEGER NOT NULL DEFAULT 0,last_error TEXT,holdings_at TEXT,activity_at TEXT,
        activity_start TEXT,activity_end TEXT,liabilities_at TEXT)''')
    db.execute('''CREATE TABLE IF NOT EXISTS connection_accounts (
        account_id TEXT PRIMARY KEY REFERENCES accounts(id),connection_id INTEGER REFERENCES multi_connections(id),
        provider_id TEXT,manual_id INTEGER,included INTEGER NOT NULL DEFAULT 1)''')
    db.execute('''CREATE TABLE IF NOT EXISTS multi_sessions (
        connection_id INTEGER PRIMARY KEY REFERENCES multi_connections(id),session TEXT NOT NULL UNIQUE,
        mode TEXT NOT NULL,token BLOB NOT NULL,url BLOB NOT NULL,expiration TEXT NOT NULL,public_token BLOB)''')
    db.execute('CREATE TABLE IF NOT EXISTS manual_selection (account_id INTEGER PRIMARY KEY,fingerprint TEXT)')
    db.execute('CREATE TABLE IF NOT EXISTS manual_debt_mirror (account_id TEXT PRIMARY KEY,payload TEXT NOT NULL,updated_at TEXT)')
    from .account_names import initialize_names
    initialize_names(db)
    db.execute('PRAGMA user_version=' + (str(max(version,13)) if production else '12'))


def scoped(identifier, value):
    return f'p{identifier}:{value}'


def owned(db, identifier):
    return [row[0] for row in db.execute('SELECT account_id FROM connection_accounts WHERE connection_id=?', (identifier,))]


def map_accounts(db, identifier, accounts, source='cached', replace=False):
    if replace:
        db.executemany('UPDATE accounts SET active=0 WHERE id=?', [(value,) for value in owned(db, identifier)])
    for original in accounts:
        account = {**original, 'account_id': scoped(identifier, original['account_id'])}
        existing = db.execute('SELECT source FROM accounts WHERE id=?', (account['account_id'],)).fetchone()
        if source == 'fresh' or not existing or existing['source'] != 'fresh':
            save_accounts(db, [account], source)
        from .account_names import save_provider_name
        save_provider_name(db, account['account_id'], account['name'])
        db.execute('UPDATE accounts SET mask=?,type=?,subtype=?,active=1 WHERE id=?',
                   (account.get('mask'), account['type'], account.get('subtype'), account['account_id']))
        db.execute('''INSERT INTO connection_accounts(account_id,connection_id,provider_id) VALUES (?,?,?)
            ON CONFLICT(account_id) DO UPDATE SET provider_id=excluded.provider_id''',
                   (account['account_id'], identifier, original['account_id']))


def mapped_account(identifier, account):
    return {**account, 'account_id': scoped(identifier, account['account_id'])}


def transaction_page(db, identifier, added, modified, removed):
    def mapped(tx):
        return {**tx, 'transaction_id': scoped(identifier, tx['transaction_id']),
                'account_id': scoped(identifier, tx['account_id']),
                **({'pending_transaction_id': scoped(identifier, tx['pending_transaction_id'])} if tx.get('pending_transaction_id') else {})}
    save_transactions(db, [mapped(tx) for tx in added], [mapped(tx) for tx in modified],
                      [{'transaction_id': scoped(identifier, tx['transaction_id'])} for tx in removed])
    # Keep raw provider IDs in the detail record, not our internal namespace.
    for tx in added + modified:
        db.execute('UPDATE transaction_details SET payload=? WHERE transaction_id=?',
                   (json.dumps(tx, default=str, ensure_ascii=False), scoped(identifier, tx['transaction_id'])))


def portfolio_observation(db):
    return [{'account_id': row['id'], 'name': row['name'], 'currency': row['currency'], 'value_minor': row['current_minor']}
            for row in db.execute("SELECT id,name,currency,current_minor FROM accounts WHERE type='investment' AND active=1")]


def import_feed(db, source, identifier):
    """Copy a saved single-Item fixture without changing or relinking it."""
    from .manual_mirror import category
    for row in source.execute('SELECT * FROM accounts'):
        key = scoped(identifier, row['id'])
        db.execute('''INSERT INTO accounts(id,name,mask,type,currency,current_minor,source,checked_at,active,subtype)
            VALUES (?,?,?,?,?,?,?,?,?,?)''', (key, row['name'], row['mask'], row['type'], row['currency'], row['current_minor'], row['source'], row['checked_at'], row['active'], row['subtype']))
        db.execute('INSERT INTO connection_accounts(account_id,connection_id,provider_id) VALUES (?,?,?)', (key, identifier, row['id']))
        original = source.execute('SELECT provider_name,nickname FROM account_names WHERE account_id=?', (row['id'],)).fetchone() if source.execute("SELECT 1 FROM sqlite_schema WHERE name='account_names'").fetchone() else None
        from .account_names import save_provider_name
        save_provider_name(db, key, original['provider_name'] if original else row['name'])
        if original and original['nickname']:
            db.execute('UPDATE account_names SET nickname=? WHERE account_id=?', (original['nickname'],key))
            db.execute('UPDATE accounts SET name=? WHERE id=?', (original['nickname'],key))
    for tx in source.execute('SELECT * FROM transactions'):
        db.execute('INSERT INTO transactions VALUES (?,?,?,?,?,?,?,?,?)',
                   (scoped(identifier, tx['id']), scoped(identifier, tx['account_id']), tx['date'], tx['name'], tx['amount_minor'], tx['currency'], tx['kind'], tx['pending'], tx['removed']))
    for row in source.execute('SELECT * FROM transaction_details'):
        db.execute('INSERT INTO transaction_details VALUES (?,?,?)', (scoped(identifier, row['transaction_id']), row['payload'], row['retrieved_at']))
    for row in source.execute('SELECT * FROM preview_edits'):
        selected = source.execute('SELECT name,kind FROM preview_categories WHERE id=?', (row['category_id'],)).fetchone() if row['category_id'] else None
        db.execute('INSERT INTO preview_edits VALUES (?,?,?,?,?)', (scoped(identifier, row['transaction_id']), row['merchant'], row['kind'], category(db, selected), row['notes']))
    for row in source.execute('SELECT * FROM transaction_reviews'):
        db.execute('INSERT INTO transaction_reviews VALUES (?,?,?,?)', (scoped(identifier, row['transaction_id']), row['state'], row['origin'], row['updated_at']))
    for row in source.execute('SELECT * FROM investment_holdings'):
        db.execute('INSERT INTO investment_holdings VALUES (?,?,?,?,?)', (scoped(identifier, row['account_id']), row['security_id'], row['payload'], row['security'], row['retrieved_at']))
    for row in source.execute('SELECT * FROM investment_activity'):
        db.execute('INSERT INTO investment_activity VALUES (?,?,?,?,?,?)', (scoped(identifier, row['id']), scoped(identifier, row['account_id']), row['date'], row['payload'], row['security'], row['removed']))
    for row in source.execute('SELECT * FROM liability_details'):
        db.execute('INSERT INTO liability_details VALUES (?,?,?,?,?)', (scoped(identifier, row['account_id']), row['category'], row['payload'], row['balances'], row['retrieved_at']))
    investments = source.execute('SELECT * FROM investment_state WHERE id=1').fetchone()
    liabilities = source.execute('SELECT * FROM liability_state WHERE id=1').fetchone()
    if investments:
        db.execute('UPDATE multi_connections SET holdings_at=?,activity_at=?,activity_start=?,activity_end=? WHERE id=?',
                   (investments['holdings_at'], investments['activity_at'], investments['activity_start'], investments['activity_end'], identifier))
        db.execute('INSERT OR IGNORE INTO investment_state(id) VALUES (1)')
        db.execute('UPDATE investment_state SET holdings_at=?,activity_at=?,activity_start=?,activity_end=? WHERE id=1',
                   (investments['holdings_at'], investments['activity_at'], investments['activity_start'], investments['activity_end']))
    if liabilities:
        db.execute('UPDATE multi_connections SET liabilities_at=? WHERE id=?', (liabilities['synced_at'], identifier))


def sync_connection(db, row, call, product, full=False):
    identifier = row['id']
    if not row['token'] or not row['item_id']:
        raise HTTPException(409, 'Finish connecting this institution first.')
    if row['needs_reauth']:
        raise HTTPException(409, 'Reconnect this institution before syncing.')
    if product == 'transactions' and row['profile'] != 'banking':
        raise HTTPException(409, 'Use holdings and activity for this investment connection.')
    column = {'transactions': 'synced_at', 'balances': 'balance_checked_at', 'holdings': 'holdings_at',
              'activity': 'activity_at', 'liabilities': 'liabilities_at'}[product]
    last = row[column]
    if last and (now() - datetime.fromisoformat(last)).total_seconds() < 30:
        raise HTTPException(429, 'Wait 30 seconds between these sync actions for this institution.')
    token = protect(row['token'], False).decode()
    stamp = now().isoformat()
    if product == 'transactions':
        cursor, history, added, modified, removed = changes(call, token, None if full else row['cursor'])
        accounts = call('/accounts/get', access_token=token)['accounts']
        map_accounts(db, identifier, accounts, replace=True)
        transaction_page(db, identifier, added, modified, removed)
        db.execute('UPDATE multi_connections SET cursor=?,history_status=? WHERE id=?', (cursor, history, identifier))
        result = {'count': len(added) + len(modified), 'removed': len(removed)}
    elif product == 'balances':
        result = call('/accounts/balance/get', access_token=token)
        map_accounts(db, identifier, result['accounts'], source='fresh')
        result = {'count': len(result['accounts'])}
    elif product == 'holdings':
        result = call('/investments/holdings/get', access_token=token)
        db.executemany("UPDATE accounts SET active=0 WHERE id=? AND type='investment'", [(value,) for value in owned(db, identifier)])
        map_accounts(db, identifier, [a for a in result['accounts'] if a['type'] == 'investment'])
        normalized = {**result, 'accounts': [mapped_account(identifier, a) for a in result['accounts']],
                      'holdings': [mapped_account(identifier, holding) for holding in result['holdings']]}
        save_holdings(db, normalized, stamp, scope=owned(db, identifier), observation_accounts=portfolio_observation(db))
        for holding in result['holdings']:
            db.execute('UPDATE investment_holdings SET payload=? WHERE account_id=? AND security_id=?',
                       (json.dumps(holding, default=str), scoped(identifier, holding['account_id']), holding['security_id']))
        result = {'count': len(result['holdings'])}
    elif product == 'activity':
        start = max(history_start(date.today()), date.fromisoformat(row['activity_end']) - timedelta(days=7)) if not full and row['activity_end'] else history_start(date.today())
        end = date.today().isoformat()
        rows = fetch_activity(call, token, start.isoformat(), end)
        account_ids = set(owned(db, identifier))
        if any(scoped(identifier, tx['account_id']) not in account_ids for tx, _ in rows):
            raise HTTPException(409, 'Sync holdings first to load these accounts.')
        mapped = [({**mapped_account(identifier, tx), 'investment_transaction_id': scoped(identifier, tx['investment_transaction_id']),
                    **({'cancel_transaction_id': scoped(identifier, tx['cancel_transaction_id'])} if tx.get('cancel_transaction_id') else {})}, security) for tx, security in rows]
        save_activity(db, mapped, start.isoformat(), end, stamp, scope=owned(db, identifier))
        for tx, _ in rows:
            db.execute('UPDATE investment_activity SET payload=? WHERE id=?', (json.dumps(tx, default=str), scoped(identifier, tx['investment_transaction_id'])))
        db.execute('UPDATE multi_connections SET activity_start=COALESCE(activity_start,?),activity_end=? WHERE id=?', (start.isoformat(), end, identifier))
        result = {'count': len(rows)}
    else:
        result = call('/liabilities/get', access_token=token)
        map_accounts(db, identifier, [a for a in result['accounts'] if a['type'] in {'credit', 'loan'}])
        normalized = {**result, 'accounts': [mapped_account(identifier, a) for a in result['accounts']],
                      'liabilities': {key: [mapped_account(identifier, item) for item in values or []] for key, values in (result.get('liabilities') or {}).items()}}
        save_liabilities(db, normalized, stamp, scope=owned(db, identifier))
        for values in (result.get('liabilities') or {}).values():
            for item in values or []:
                raw = {key: value for key, value in item.items() if key not in {'account_number', 'payment_reference_number'}}
                db.execute('UPDATE liability_details SET payload=? WHERE account_id=?', (json.dumps(raw, default=str), scoped(identifier, item['account_id'])))
        result = {'count': sum(len(values or []) for values in normalized['liabilities'].values())}
    db.execute(f'UPDATE multi_connections SET {column}=?,last_error=NULL WHERE id=?', (stamp, identifier))
    if product in {'transactions', 'balances', 'holdings', 'liabilities'}:
        record_preview_snapshot(db)
    return {'updated': product, **result}


class NewConnection(BaseModel):
    institution_id: str = Field(pattern=r'^ins_[A-Za-z0-9_]+$', max_length=100)
    profile: Literal['banking', 'investments'] = 'banking'
    include_investments: bool = False


class ManualSelection(BaseModel):
    account_ids: list[int] = Field(default_factory=list, max_length=1000)


def combined_router(database: Path, *, client_factory=plaid_client, production=False):
    from .app_config import application_config
    unified = production and application_config(database.parent.parent)['unified']
    path = database.with_suffix('.plaid-live.db') if production else sandbox_path(database, 'combined')
    router = APIRouter(prefix='/api/plaid/live' if production else '/api/plaid/combined-sandbox')

    def initialize():
        with connect(path, write=True) as db:
            if production:
                from .live_setup import initialize_live
                initialize_live(db)
            else:
                initialize_combined(db)
        from .manual_mirror import refresh_manual
        refresh_manual(database, path, include_all=unified)

    def client():
        from .live_setup import production_credentials
        keys = production_credentials(database.parent.parent) if production else credentials(database.parent.parent)
        if not keys:
            raise HTTPException(503, 'Configure explicit Production credentials first; Sandbox credentials are never reused.' if production else 'Configure local Sandbox credentials first.')
        return client_factory(keys, environment='production') if production else client_factory(keys)

    if production:
        from .live_setup import LiveSetup, readiness

        @router.get('/readiness')
        def get_readiness():
            initialize()
            with connect(path) as db:
                return readiness(db, database.parent.parent)

        @router.put('/readiness')
        def save_readiness(item: LiveSetup):
            initialize()
            with connect(path, write=True) as db:
                db.execute('INSERT INTO live_setup VALUES (1,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload', (item.model_dump_json(),))
                return readiness(db, database.parent.parent)

        @router.post('/preflight')
        def preflight(item: NewConnection):
            initialize()
            try:
                info = client()('/institutions/get_by_id', institution_id=item.institution_id, country_codes=['US'], options={'include_optional_metadata': True, 'include_status': True})['institution']
                result = {'institution_id': info['institution_id'], 'name': info['name'], 'oauth': info.get('oauth'),
                          'transactions': 'transactions' in info.get('products', []),
                          'products': info.get('products', []), 'checked_at': now().isoformat(),
                          'note': 'Credentials accepted for institution lookup. Coverage is not proof of account-specific access or successful OAuth.'}
                with connect(path, write=True) as db:
                    from .live_setup import credentials_digest
                    db.execute('INSERT INTO live_preflight VALUES (?,?,?,?) ON CONFLICT(institution_id) DO UPDATE SET payload=excluded.payload,checked_at=excluded.checked_at,credentials_digest=excluded.credentials_digest',
                               (item.institution_id, json.dumps(result), result['checked_at'], credentials_digest(database.parent.parent)))
                return result
            except PlaidError as error:
                with connect(path, write=True) as db:
                    db.execute('DELETE FROM live_preflight WHERE institution_id=?', (item.institution_id,))
                raise upstream(error) from None

    def connection(db, identifier):
        row = db.execute('SELECT * FROM multi_connections WHERE id=?', (identifier,)).fetchone()
        if not row:
            raise HTTPException(404, 'Connection not found.')
        return row

    def upstream(error, identifier=None):
        if identifier is not None:
            with connect(path, write=True) as db:
                db.execute('UPDATE multi_connections SET last_error=?,needs_reauth=CASE WHEN ? THEN 1 ELSE needs_reauth END WHERE id=?',
                           (error.code, error.code in {'ITEM_LOGIN_REQUIRED', 'USER_PERMISSION_REVOKED'}, identifier))
        message = 'This institution needs reconnection.' if error.code in {'ITEM_LOGIN_REQUIRED', 'USER_PERMISSION_REVOKED'} else \
            'This product is unavailable or not consented for this institution. Saved data is retained.' if error.code in {'PRODUCTS_NOT_SUPPORTED', 'PRODUCT_NOT_SUPPORTED', 'ADDITIONAL_CONSENT_REQUIRED', 'ACCESS_NOT_GRANTED', 'INVALID_PRODUCT'} else \
            'Plaid is preparing this product; try again later.' if error.code == 'PRODUCT_NOT_READY' else 'Plaid request failed. Saved data is retained; check the terminal request ID.'
        return HTTPException(409 if error.code != 'NETWORK_ERROR' else 502, message + ' (' + error.code + ')')

    @router.get('')
    def status():
        initialize()
        with connect(path) as db:
            rows = [dict(row) for row in db.execute('''SELECT c.id,c.institution_id,c.name,c.profile,
                c.item_id IS NOT NULL AS connected,c.synced_at,c.history_status,c.balance_checked_at,c.needs_reauth,c.last_error,
                c.holdings_at,c.activity_at,c.liabilities_at,s.session,s.expiration,
                (SELECT COUNT(*) FROM connection_accounts m JOIN accounts a ON a.id=m.account_id WHERE m.connection_id=c.id AND a.active=1) AS account_count
                FROM multi_connections c LEFT JOIN multi_sessions s ON s.connection_id=c.id ORDER BY c.name,c.id''')]
            for row in rows:
                row['investments_enabled'] = bool(db.execute('SELECT 1 FROM live_investment_connections WHERE connection_id=?', (row['id'],)).fetchone()) if production else row['profile'] == 'investments'
                row['investment_consent_pending'] = bool(db.execute('SELECT 1 FROM live_investment_connections WHERE connection_id=? AND consented=0', (row['id'],)).fetchone()) if production else False
                pending = db.execute('SELECT url FROM multi_sessions WHERE connection_id=?', (row['id'],)).fetchone()
                row['url'] = protect(pending['url'], False).decode() if pending else None
            manual = [dict(row) for row in db.execute('SELECT a.rowid AS id,a.name,m.manual_id FROM accounts a JOIN connection_accounts m ON m.account_id=a.id WHERE m.manual_id IS NOT NULL AND a.active=1')]
        from .live_setup import production_credentials, settings
        return {'configured': bool(production_credentials(database.parent.parent) if production else credentials(database.parent.parent)),
                'connections': rows, 'manual_accounts': manual, 'environment': 'production' if production else 'sandbox',
                'linking_enabled': (settings(database.parent.parent).get('PLAID_LIVE_ENABLED', '').lower() == 'true') if production else True}

    @router.get('/institutions')
    def search(query: str = Query(min_length=2, max_length=100), profile: Literal['banking', 'investments'] = 'banking', include_investments: bool = False):
        try:
            products = ['investments'] if profile == 'investments' else ['transactions'] + (['investments'] if include_investments else [])
            result = client()('/institutions/search', query=query, country_codes=['US'], products=products)
            return {'institutions': [{'id': item['institution_id'], 'name': item['name']} for item in result['institutions']]}
        except PlaidError as error:
            raise upstream(error) from None

    @router.post('/connections', status_code=201)
    def add(item: NewConnection):
        initialize()
        with database_lock(path):
            with connect(path) as db:
                if db.execute('SELECT 1 FROM multi_connections WHERE institution_id=?', (item.institution_id,)).fetchone():
                    raise HTTPException(409, 'This institution is already listed. Use its existing connection or reconnect action.')
            try:
                info = client()('/institutions/get_by_id', institution_id=item.institution_id, country_codes=['US'], options={'include_optional_metadata': True})['institution']
                required = 'investments' if item.profile == 'investments' else 'transactions'
                if required not in info.get('products', []) or (item.include_investments and 'investments' not in info.get('products', [])):
                    raise HTTPException(409, 'This institution does not support the selected connection type.')
                with connect(path, write=True) as db:
                    if production and (item.profile == 'investments' or item.include_investments):
                        if not readiness(db, database.parent.parent)['setup']['investments_enabled']:
                            raise HTTPException(409, 'Confirm Investments product access in Setup details first.')
                    identifier = db.execute('INSERT INTO multi_connections(institution_id,name,profile) VALUES (?,?,?)', (item.institution_id, info['name'], item.profile)).lastrowid
                    if production and (item.profile == 'investments' or item.include_investments):
                        db.execute('INSERT INTO live_investment_connections(connection_id) VALUES (?)', (identifier,))
                return {'id': identifier}
            except PlaidError as error:
                raise upstream(error) from None

    if production:
        @router.post('/connections/{identifier}/enable-investments')
        def enable_investments(identifier: int):
            initialize()
            with connect(path, write=True) as db:
                row = connection(db, identifier)
                if db.execute('SELECT 1 FROM multi_sessions WHERE connection_id=?', (identifier,)).fetchone():
                    raise HTTPException(409, 'Finish or cancel the pending Link session first.')
                from .live_setup import require_link_ready
                require_link_ready(db, database.parent.parent, row['institution_id'], investments=True, transactions=row['profile']=='banking')
                db.execute('INSERT OR IGNORE INTO live_investment_connections(connection_id) VALUES (?)', (identifier,))
            return {'status': 'Reconnect this institution to grant investment consent. Existing banking data is retained.'}

    @router.post('/import-scenarios')
    def import_scenarios():
        if production:
            raise HTTPException(403, 'Sandbox connections cannot be imported into Live.')
        initialize()
        call = client()
        imported = 0
        try:
            for scenario in ['banking', 'investments']:
                original = sandbox_path(database, scenario)
                if not original.is_file():
                    continue
                with connect(original) as source:
                    found = source.execute('SELECT * FROM connection WHERE id=1').fetchone()
                    saved = dict(found) if found else None
                if not saved:
                    continue
                with connect(path) as db:
                    if db.execute('SELECT 1 FROM multi_connections WHERE item_id=?', (saved['item_id'],)).fetchone():
                        continue
                item = call('/item/get', access_token=protect(saved['token'], False).decode())['item']
                institution = item.get('institution_id')
                if not institution:
                    raise HTTPException(409, 'Saved connection has no verified institution. It was not copied.')
                info = call('/institutions/get_by_id', institution_id=institution, country_codes=['US'], options={'include_optional_metadata': True})['institution']
                with database_lock(path), connect(original) as source, connect(path, write=True) as db:
                    latest = source.execute('SELECT * FROM connection WHERE id=1').fetchone()
                    if not latest or latest['item_id'] != saved['item_id'] or latest['token'] != saved['token']:
                        raise HTTPException(409, 'Saved connection changed during import. Retry with the current connection.')
                    saved = dict(latest)
                    if db.execute('SELECT 1 FROM multi_connections WHERE institution_id=? OR item_id=?', (institution, saved['item_id'])).fetchone():
                        continue
                    identifier = db.execute('''INSERT INTO multi_connections(institution_id,name,profile,item_id,token,cursor,
                        history_status,synced_at,balance_checked_at,needs_reauth) VALUES (?,?,?,?,?,?,?,?,?,?)''',
                        (institution, info['name'], scenario, saved['item_id'], saved['token'], saved['cursor'], saved['history_status'], saved['synced_at'], saved['balance_checked_at'], saved['needs_reauth'])).lastrowid
                    import_feed(db, source, identifier)
                    record_preview_snapshot(db)
                    observed = portfolio_observation(db)
                    db.execute('INSERT INTO investment_observations(date,retrieved_at,accounts) VALUES (?,?,?)', (date.today().isoformat(), now().isoformat(), json.dumps(observed)))
                    imported += 1
            return {'imported': imported}
        except PlaidError as error:
            raise upstream(error) from None

    @router.post('/connections/{identifier}/link')
    def start(identifier: int):
        initialize()
        try:
            with connect(path, write=True) as db:
                row = connection(db, identifier)
                if production:
                    from .live_setup import require_link_ready
                    investment = db.execute('SELECT consented FROM live_investment_connections WHERE connection_id=?', (identifier,)).fetchone()
                    require_link_ready(db, database.parent.parent, row['institution_id'], investments=bool(investment), transactions=row['profile']=='banking')
                    if not row['item_id']:
                        setup = readiness(db, database.parent.parent)
                        pending = db.execute('SELECT COUNT(*) FROM multi_sessions s JOIN multi_connections c ON c.id=s.connection_id WHERE c.item_id IS NULL').fetchone()[0]
                        if setup['connected_items'] + pending >= setup['setup']['item_limit']:
                            raise HTTPException(409, 'The locally configured Item allowance has been reached. Verify your actual Dashboard usage.')
                old = db.execute('SELECT * FROM multi_sessions WHERE connection_id=?', (identifier,)).fetchone()
                if old:
                    raise HTTPException(409, 'Finish or cancel the saved Link session before starting another.')
                options = {'access_token': protect(row['token'], False).decode(), 'update': {'account_selection_enabled': True}} if row['item_id'] else \
                    {'products': ['investments']} if row['profile'] == 'investments' else \
                    {'products': ['transactions'], 'additional_consented_products': ['investments', 'liabilities'], 'transactions': {'days_requested': 730}}
                if production:
                    if not row['item_id']:
                        products = ['transactions'] if row['profile'] == 'banking' else []
                        if investment:
                            products.append('investments')
                        options = {'products': products}
                        if 'transactions' in products:
                            options['transactions'] = {'days_requested': 730}
                    elif investment and not investment['consented']:
                        options['additional_consented_products'] = ['investments']
                if production:
                    from .live_setup import settings
                    redirect = settings(database.parent.parent).get('PLAID_PRODUCTION_REDIRECT_URI')
                    if redirect:
                        options['redirect_uri'] = redirect
                result = client()('/link/token/create', retry=False, client_name='Personal Finance' if production else 'Personal Finance Sandbox', language='en', country_codes=['US'],
                                  user={'client_user_id': 'local-finance-owner'}, hosted_link={}, **options)
                if production:
                    from .live_setup import safe_hosted_url
                    safe_hosted_url(result['hosted_link_url'])
                session = str(uuid4())
                db.execute('INSERT INTO multi_sessions VALUES (?,?,?,?,?,?,NULL)', (identifier, session, 'update' if row['item_id'] else 'new',
                           protect(result['link_token'].encode(), True), protect(result['hosted_link_url'].encode(), True), result['expiration']))
                return {'session': session, 'url': result['hosted_link_url']}
        except PlaidError as error:
            raise upstream(error, identifier) from None

    @router.delete('/connections/{identifier}/link')
    def cancel(identifier: int):
        initialize()
        with connect(path, write=True) as db:
            connection(db, identifier)
            row = db.execute('SELECT public_token FROM multi_sessions WHERE connection_id=?', (identifier,)).fetchone()
            if row and row['public_token']:
                raise HTTPException(409, 'Token exchange is pending. Check the connection again to recover it; cancelling could create a duplicate on retry.')
            db.execute('DELETE FROM multi_sessions WHERE connection_id=?', (identifier,))
        return {'cleared': True}

    @router.post('/connections/{identifier}/link/{session}')
    def finish(identifier: int, session: UUID):
        initialize()
        if production:
            from .live_setup import require_link_ready
            with connect(path) as db:
                owner = connection(db, identifier)
                investment = db.execute('SELECT 1 FROM live_investment_connections WHERE connection_id=?', (identifier,)).fetchone()
                require_link_ready(db, database.parent.parent, owner['institution_id'], investments=bool(investment), transactions=owner['profile']=='banking')
        call = client()
        try:
            with database_lock(path):
                with connect(path) as db:
                    owner = dict(connection(db, identifier))
                    found = db.execute('SELECT * FROM multi_sessions WHERE connection_id=? AND session=?', (identifier, str(session))).fetchone()
                    row = dict(found) if found else None
                if not row:
                    if owner['item_id']:
                        return {'status': 'connected'}
                    raise HTTPException(404, 'Link session not found.')
                if not row['public_token']:
                    result = call('/link/token/get', retry=False, link_token=protect(row['token'], False).decode())
                    for linked in reversed(result.get('link_sessions') or []):
                        if not linked.get('finished_at'):
                            continue
                        success = linked.get('on_success') or {}
                        items = (linked.get('results') or {}).get('item_add_results') or []
                        if row['mode'] == 'update':
                            if not items and not success and not any(event.get('event_name') == 'HANDOFF' for event in linked.get('events') or []):
                                with connect(path, write=True) as db:
                                    db.execute('DELETE FROM multi_sessions WHERE connection_id=?', (identifier,))
                                return {'status': 'closed'}
                            with connect(path, write=True) as db:
                                db.execute('UPDATE multi_connections SET needs_reauth=0,last_error=NULL WHERE id=?', (identifier,))
                                if production:
                                    db.execute('UPDATE live_investment_connections SET consented=1 WHERE connection_id=?', (identifier,))
                                db.execute('DELETE FROM multi_sessions WHERE connection_id=?', (identifier,))
                            return {'status': 'connected'}
                        if len(items) > 1:
                            raise HTTPException(409, 'Choose one institution per connection.')
                        item = items[0] if items else success
                        if not item:
                            with connect(path, write=True) as db:
                                db.execute('DELETE FROM multi_sessions WHERE connection_id=?', (identifier,))
                            return {'status': 'closed'}
                        metadata = item.get('metadata') or item
                        institution = metadata.get('institution') or {}
                        if institution.get('institution_id') != owner['institution_id']:
                            raise HTTPException(409, 'The linked institution does not match your selection. No token was exchanged. Cancel and choose the intended institution.')
                        public = item.get('public_token')
                        if not public:
                            break
                        # Durable before exchange: a timeout can retry the SAME public token.
                        with connect(path, write=True) as db:
                            db.execute('UPDATE multi_sessions SET public_token=? WHERE connection_id=?', (protect(public.encode(), True), identifier))
                        row['public_token'] = protect(public.encode(), True)
                        break
                if row['public_token']:
                    exchanged = call('/item/public_token/exchange', retry=False, public_token=protect(row['public_token'], False).decode())
                    with connect(path, write=True) as db:
                        duplicate = db.execute('SELECT id FROM multi_connections WHERE item_id=? AND id<>?', (exchanged['item_id'], identifier)).fetchone()
                        if duplicate:
                            raise HTTPException(409, 'This Item already belongs to another saved connection; no saved authorization was replaced.')
                        db.execute('UPDATE multi_connections SET item_id=?,token=?,last_error=NULL,needs_reauth=0 WHERE id=?',
                                   (exchanged['item_id'], protect(exchanged['access_token'].encode(), True), identifier))
                        if production:
                            db.execute('UPDATE live_investment_connections SET consented=1 WHERE connection_id=?', (identifier,))
                        db.execute('DELETE FROM multi_sessions WHERE connection_id=?', (identifier,))
                    return {'status': 'connected'}
                if datetime.fromisoformat(row['expiration']) <= now():
                    with connect(path, write=True) as db:
                        db.execute('DELETE FROM multi_sessions WHERE connection_id=?', (identifier,))
                    return {'status': 'expired'}
                return {'status': 'pending'}
        except PlaidError as error:
            raise upstream(error, identifier) from None

    @router.post('/connections/{identifier}/sync')
    def sync(identifier: int, product: Literal['transactions', 'balances', 'holdings', 'activity', 'liabilities'], full: bool = False):
        initialize()
        if production and product == 'liabilities':
            raise HTTPException(409, 'Live debt-provider sync is not enabled yet.')
        attempted_at = now().isoformat()
        from .freshness import record_sync_result
        try:
            with connect(path, write=True) as db:
                if production and product in {'holdings', 'activity'}:
                    consent = db.execute('SELECT consented FROM live_investment_connections WHERE connection_id=?', (identifier,)).fetchone()
                    if not consent or not consent['consented']:
                        raise HTTPException(409, 'Enable investment access and complete the saved Link session first.')
                result = sync_connection(db, connection(db, identifier), client(), product, full)
                if production:
                    record_sync_result(db, identifier, product, attempted_at)
                return result
        except PlaidError as error:
            if production:
                with connect(path, write=True) as db:
                    record_sync_result(db, identifier, product, attempted_at, error.code)
            raise upstream(error, identifier) from None
        except ValueError as error:
            if production:
                with connect(path, write=True) as db:
                    record_sync_result(db, identifier, product, attempted_at, 'INVALID_DATA')
            raise HTTPException(409, str(error)) from None

    @router.get('/investments')
    def positions():
        initialize()
        with connect(path) as db:
            result = project_investments(db)
            names = owner_names(db)
            for account in result['accounts']:
                account['name'] += ' · ' + names.get(account['id'], 'Manual')
            return result

    def owner_names(db):
        return {row['account_id']: row['name'] for row in db.execute('''SELECT m.account_id,COALESCE(c.name,'Manual') AS name
            FROM connection_accounts m LEFT JOIN multi_connections c ON c.id=m.connection_id''')}

    @router.get('/investments/activity')
    def activity(account: str | None = None, start: date | None = None, end: date | None = None,
                 search: str = Query('', max_length=120), kind: str | None = None, page: int = Query(1, ge=1, le=100000)):
        if start and end and start > end:
            raise HTTPException(422, 'Start date must be before end date.')
        initialize()
        with connect(path) as db:
            result = project_activity(db, account, start.isoformat() if start else None, end.isoformat() if end else None, search, kind, page)
            names = owner_names(db)
            for row in result['rows']:
                row['account_name'] += ' · ' + names.get(row['account_id'], 'Manual')
            return result

    @router.get('/debt/{identifier}')
    def debt(identifier: int):
        initialize()
        with connect(path) as db:
            account = db.execute('SELECT * FROM accounts WHERE rowid=? AND active=1', (identifier,)).fetchone()
            if not account or account['type'] not in {'loan', 'credit'}:
                raise HTTPException(404, 'Debt account not found.')
            manual = db.execute('SELECT * FROM manual_debt_mirror WHERE account_id=?', (account['id'],)).fetchone()
            if manual:
                return {'source': 'manual', 'fields': json.loads(manual['payload']), 'updated_at': manual['updated_at'], 'aprs': []}
            row = db.execute('SELECT * FROM liability_details WHERE account_id=?', (account['id'],)).fetchone()
            return project_liability(row, account['currency'])

    @router.post('/liabilities/sync')
    def debt_sync(account_id: int):
        initialize()
        with connect(path) as db:
            owner = db.execute('SELECT m.connection_id FROM connection_accounts m JOIN accounts a ON a.id=m.account_id WHERE a.rowid=?', (account_id,)).fetchone()
            if not owner or owner[0] is None:
                raise HTTPException(409, 'Manage this manual account in Manual.')
        return sync(owner[0], 'liabilities')

    @router.get('/manual')
    def manual_accounts():
        from .ledger import account_balances
        initialize()
        with connect(database) as source, connect(path) as db:
            selected = {row[0] for row in db.execute('SELECT account_id FROM manual_selection')}
            return {'accounts': [{**row, 'selected': row['id'] in selected} for row in account_balances(source, date.today())]}

    @router.put('/manual')
    def select_manual(item: ManualSelection):
        if unified:
            raise HTTPException(409, 'All manual accounts are included automatically in All accounts.')
        initialize()
        with connect(database) as source:
            valid = {row[0] for row in source.execute('SELECT id FROM accounts')}
        if not set(item.account_ids) <= valid:
            raise HTTPException(422, 'Choose existing Manual accounts.')
        with connect(path, write=True) as db:
            db.execute('DELETE FROM manual_selection')
            db.executemany('INSERT INTO manual_selection(account_id) VALUES (?)', [(identifier,) for identifier in sorted(set(item.account_ids))])
        from .manual_mirror import refresh_manual
        refresh_manual(database, path)
        return {'selected': sorted(set(item.account_ids))}

    if production:
        def startup_connections():
            initialize()
            with connect(path) as db:
                return [dict(row) for row in db.execute('''SELECT c.id,c.name,c.profile,c.needs_reauth,
                    EXISTS(SELECT 1 FROM live_investment_connections i WHERE i.connection_id=c.id) AS investments_enabled,
                    EXISTS(SELECT 1 FROM live_investment_connections i WHERE i.connection_id=c.id AND i.consented=0) AS investment_consent_pending
                    FROM multi_connections c WHERE c.item_id IS NOT NULL AND c.token IS NOT NULL ORDER BY c.id''')]

        def startup_snapshot():
            initialize()
            with connect(path, write=True) as db:
                identifier = record_preview_snapshot(db)
                return {'id': identifier, 'message': 'Saved current balances with their original update times. Charts use the latest daily observations.' if identifier else 'No saved account balances to snapshot yet.'}

        # Reuse the exact same server-side sync guards, consent checks and error handling.
        router.startup_operations = {'connections': startup_connections, 'sync': sync, 'snapshot': startup_snapshot}
    router.include_router(sandbox_preview_router(database, initialize, path=path))
    return router
