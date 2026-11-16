"""Production configuration and readiness, without creating a Plaid Item."""
import json
import os
import hashlib
from urllib.parse import urlsplit

from fastapi import HTTPException
from pydantic import BaseModel, Field

from .plaid_sandbox import now


def settings(directory):
    values = {}
    try:
        lines = (directory / '.env').read_text(encoding='utf-8-sig').splitlines()
    except FileNotFoundError:
        lines = []
    for line in lines:
        key, separator, value = line.partition('=')
        if separator and key.strip().startswith('PLAID_'):
            values[key.strip()] = value.strip().strip('"').strip("'")
    return {**values, **{key: value for key, value in os.environ.items() if key.startswith('PLAID_')}}


def production_credentials(directory):
    values = settings(directory)
    client = values.get('PLAID_PRODUCTION_CLIENT_ID') or values.get('PLAID_CLIENT_ID')
    secret = values.get('PLAID_PRODUCTION_SECRET')
    return (client, secret) if client and secret and not any('replace_with' in value for value in (client, secret)) else None


def initialize_live(db):
    from .multi_bank import initialize_combined
    if db.execute('PRAGMA user_version').fetchone()[0] not in {0, 9, 10}:
        raise ValueError('Sandbox or Manual data cannot be opened as Live.')
    initialize_combined(db, production=True)
    db.execute("CREATE TABLE IF NOT EXISTS workspace_environment (id INTEGER PRIMARY KEY CHECK(id=1),environment TEXT NOT NULL CHECK(environment='production'))")
    db.execute("INSERT OR IGNORE INTO workspace_environment VALUES (1,'production')")
    db.execute('CREATE TABLE IF NOT EXISTS live_setup (id INTEGER PRIMARY KEY CHECK(id=1),payload TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS live_preflight (institution_id TEXT PRIMARY KEY,payload TEXT NOT NULL,checked_at TEXT NOT NULL,credentials_digest TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS live_investment_connections (connection_id INTEGER PRIMARY KEY REFERENCES multi_connections(id),consented INTEGER NOT NULL DEFAULT 0 CHECK(consented IN (0,1)))')
    db.execute('PRAGMA user_version=10')


class LiveSetup(BaseModel):
    trial_confirmed: bool = False
    item_limit: int | None = Field(None, ge=1, le=1000)
    transactions_enabled: bool = False
    oauth_confirmed: bool = False
    investments_enabled: bool = False


def readiness(db, directory):
    row = db.execute('SELECT payload FROM live_setup WHERE id=1').fetchone()
    confirmed = LiveSetup.model_validate_json(row[0]) if row else LiveSetup()
    values = settings(directory)
    configured = bool(production_credentials(directory))
    enabled = values.get('PLAID_LIVE_ENABLED', '').lower() == 'true'
    redirect = values.get('PLAID_PRODUCTION_REDIRECT_URI', '')
    try:
        uri = urlsplit(redirect)
        redirect_valid = not redirect or (uri.scheme == 'https' and bool(uri.hostname) and not any((uri.query, uri.fragment, uri.username, uri.password)) and '*' not in redirect and not any(c.isspace() for c in redirect))
    except ValueError:
        redirect_valid = False
    connected = db.execute('SELECT COUNT(*) FROM multi_connections WHERE item_id IS NOT NULL').fetchone()[0]
    checks = [
        {'label': 'Production credentials configured', 'ok': configured},
        {'label': 'Free Trial and connection allowance confirmed in Dashboard', 'ok': confirmed.trial_confirmed and confirmed.item_limit is not None},
        {'label': 'Transactions enabled in Dashboard', 'ok': confirmed.transactions_enabled},
        {'label': 'OAuth access confirmed in Dashboard', 'ok': confirmed.oauth_confirmed},
    ]
    if redirect:
        checks.append({'label': 'Custom OAuth redirect is valid HTTPS', 'ok': redirect_valid})
    return {'environment': 'production', 'configured': configured, 'linking_enabled': enabled,
            'setup': confirmed.model_dump(), 'checks': checks, 'ready': all(check['ok'] for check in checks),
            'connected_items': connected, 'allowance_is_dashboard_confirmed': confirmed.trial_confirmed and confirmed.item_limit is not None,
            'limit_note': 'Dashboard allowance is authoritative. Local connected Items do not include Items created by other apps or removed Items.',
            'preflights': [json.loads(row[0]) for row in db.execute('SELECT payload FROM live_preflight ORDER BY checked_at DESC')]}


def require_link_ready(db, directory, institution, *, investments=False, transactions=True):
    state = readiness(db, directory)
    if not state['linking_enabled']:
        raise HTTPException(403, 'Real-bank linking is locked. Complete readiness and separately approve the live pilot before enabling PLAID_LIVE_ENABLED.')
    if not state['ready']:
        raise HTTPException(409, 'Complete all Live readiness checks before opening a real-bank Link session.')
    if investments and not state['setup']['investments_enabled']:
        raise HTTPException(409, 'Confirm Investments product access in Setup details before connecting investments.')
    checked = db.execute('SELECT payload,checked_at,credentials_digest FROM live_preflight WHERE institution_id=?', (institution,)).fetchone()
    from datetime import datetime
    if not checked or checked['credentials_digest'] != credentials_digest(directory) or (now() - datetime.fromisoformat(checked['checked_at'])).total_seconds() > 86400 or (transactions and not json.loads(checked['payload']).get('transactions')) or (investments and 'investments' not in json.loads(checked['payload']).get('products', [])):
        raise HTTPException(409, 'Run a successful coverage check for the selected products within 24 hours.')


def credentials_digest(directory):
    return hashlib.sha256(json.dumps(production_credentials(directory)).encode()).hexdigest()


def safe_hosted_url(value):
    try:
        uri = urlsplit(value)
        valid = uri.scheme == 'https' and uri.hostname in {'secure.plaid.com', 'hosted.plaid.com'} and not uri.username and not uri.password and uri.port in {None, 443}
    except ValueError:
        valid = False
    if not valid:
        raise HTTPException(502, 'Plaid returned an unexpected Hosted Link URL. No session was saved.')
    return value


def live_router(database, *, client_factory=None):
    from .multi_bank import combined_router
    from .plaid_sandbox import plaid_client
    return combined_router(database, production=True, client_factory=client_factory or plaid_client)
