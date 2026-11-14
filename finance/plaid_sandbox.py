"""Read-only Plaid Sandbox integration, isolated from the manual ledger."""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import json
import logging
import os
from pathlib import Path
from time import sleep
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Query

from .db import connect
from .plaid_vault import protect
from .sandbox_preview import initialize_preview, record_preview_snapshot, sandbox_preview_router
from .review import apply_rules
from .investments import fetch_activity, history_start, save_holdings, save_activity, project_investments, project_activity
from .scenarios import sandbox_path

LOG = logging.getLogger("uvicorn.error")
SCHEMA = (
    """CREATE TABLE IF NOT EXISTS connection (
       id INTEGER PRIMARY KEY CHECK(id=1), item_id TEXT NOT NULL, token BLOB NOT NULL,
       cursor TEXT, history_status TEXT, synced_at TEXT, balance_checked_at TEXT,
       needs_reauth INTEGER NOT NULL DEFAULT 0)""",
    """CREATE TABLE IF NOT EXISTS link (
       id INTEGER PRIMARY KEY CHECK(id=1), session TEXT NOT NULL, mode TEXT NOT NULL,
       token BLOB NOT NULL, expiration TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS accounts (
       id TEXT PRIMARY KEY, name TEXT NOT NULL, mask TEXT, type TEXT NOT NULL, currency TEXT,
       current_minor INTEGER, source TEXT, checked_at TEXT)""",
    """CREATE TABLE IF NOT EXISTS transactions (
       id TEXT PRIMARY KEY, account_id TEXT NOT NULL REFERENCES accounts(id), date TEXT NOT NULL,
       name TEXT NOT NULL, amount_minor INTEGER NOT NULL, currency TEXT NOT NULL,
       kind TEXT NOT NULL, pending INTEGER NOT NULL, removed INTEGER NOT NULL DEFAULT 0)""",
    "CREATE INDEX IF NOT EXISTS recent_transactions ON transactions(removed,date DESC,id)",
)


class PlaidError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def credentials(directory):
    values = {}
    try:
        lines = (directory / ".env").read_text(encoding="utf-8-sig").splitlines()
    except FileNotFoundError:
        lines = []
    for line in lines:
        key, sep, value = line.partition("=")
        if sep and key.strip() in {"PLAID_CLIENT_ID", "PLAID_SECRET", "PLAID_ENV"}:
            values[key.strip()] = value.strip().strip('"').strip("'")
    if os.environ.get("PLAID_ENV", values.get("PLAID_ENV", "sandbox")) != "sandbox":
        return None
    client = os.environ.get("PLAID_CLIENT_ID", values.get("PLAID_CLIENT_ID"))
    secret = os.environ.get("PLAID_SECRET", values.get("PLAID_SECRET"))
    return (client, secret) if client and secret else None


def plaid_client(keys):
    def call(path, *, retry=True, **payload):
        body = json.dumps({"client_id": keys[0], "secret": keys[1], **payload}).encode()
        request = Request("https://sandbox.plaid.com" + path, data=body, headers={
            "Content-Type": "application/json", "Plaid-Version": "2020-09-14",
        })
        attempts = 3 if retry else 1
        for attempt in range(attempts):
            try:
                with urlopen(request, timeout=120 if path.startswith('/investments/') else 12) as response:
                    result = json.load(response, parse_float=Decimal)
                LOG.info("Plaid Sandbox %s request_id=%s", path, result.get("request_id"))
                return result
            except HTTPError as error:
                with error:
                    try:
                        result = json.load(error)
                    except (ValueError, OSError):
                        result = {}
                code = result.get("error_code", "UPSTREAM_ERROR")
                LOG.info("Plaid Sandbox %s failed code=%s request_id=%s", path, code, result.get("request_id"))
                if error.code not in {429, 500, 502, 503, 504} and code not in {
                    "INSTITUTION_DOWN", "INSTITUTION_NOT_RESPONDING", "RATE_LIMIT_EXCEEDED",
                }:
                    raise PlaidError(code) from None
            except (URLError, OSError, ValueError):
                code = "NETWORK_ERROR"
            if attempt + 1 < attempts:
                sleep(2 ** attempt)
        raise PlaidError(code)
    return call


def now():
    return datetime.now(timezone.utc)


def minor(value, currency):
    if value is None or currency not in {"USD", "CAD", "EUR", "GBP", "AUD", "CHF", "JPY"}:
        return None
    return int(round(Decimal(str(value)) * (1 if currency == "JPY" else 100)))


def save_accounts(db, accounts, source):
    for a in accounts:
        currency = a["balances"].get("iso_currency_code")
        current = minor(a["balances"].get("current"), currency)
        if current is not None and a["type"] in {"credit", "loan"}:
            current = -current
        db.execute("""
            INSERT INTO accounts(id,name,mask,type,currency,current_minor,source,checked_at,subtype) VALUES (?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET name=excluded.name, mask=excluded.mask, type=excluded.type,
              currency=CASE WHEN excluded.current_minor IS NOT NULL THEN excluded.currency ELSE accounts.currency END,
              current_minor=COALESCE(excluded.current_minor,accounts.current_minor),
              source=COALESCE(excluded.source,accounts.source),
              checked_at=COALESCE(excluded.checked_at,accounts.checked_at),
              subtype=excluded.subtype
        """, (a["account_id"], a["name"], a.get("mask"), a["type"], currency, current,
              source if current is not None else None, now().isoformat() if current is not None else None,
              a.get("subtype")))


def changes(call, token, checkpoint):
    for _ in range(3):
        cursor = checkpoint
        seen = {cursor}
        added, modified, removed = [], [], []
        try:
            while True:
                page = call("/transactions/sync", access_token=token, count=500,
                            options={"include_original_description": True, "personal_finance_category_version": "v2"},
                            **({"cursor": cursor} if cursor else {}))
                added.extend(page["added"])
                modified.extend(page["modified"])
                removed.extend(page["removed"])
                cursor = page["next_cursor"]
                if not page["has_more"]:
                    return cursor, page.get("transactions_update_status"), added, modified, removed
                if cursor in seen:
                    raise PlaidError("INVALID_PAGINATION")
                seen.add(cursor)
        except PlaidError as error:
            if error.code != "TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION":
                raise
    raise PlaidError("TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION")


def save_transactions(db, added, modified, removed):
    superseded = []
    for tx in added + modified:
        db.execute("""
            INSERT INTO transaction_details VALUES (?,?,?)
            ON CONFLICT(transaction_id) DO UPDATE SET payload=excluded.payload,retrieved_at=excluded.retrieved_at
        """, (tx["transaction_id"], json.dumps(tx, default=str, ensure_ascii=False), now().isoformat()))
        currency = tx.get("iso_currency_code")
        amount = minor(tx["amount"], currency)
        if amount is None:
            continue
        if not db.execute("SELECT 1 FROM accounts WHERE id=?", (tx["account_id"],)).fetchone():
            # Never advance the cursor if the account list couldn't explain the feed.
            raise PlaidError("MISSING_TRANSACTION_ACCOUNT")
        pfc = tx.get("personal_finance_category") or {}
        primary = pfc.get("primary")
        if primary in {"TRANSFER_IN", "TRANSFER_OUT"} or pfc.get("detailed") == "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT":
            kind = "transfer"
        elif amount < 0:
            kind = "income" if primary == "INCOME" else "credit"
        else:
            kind = "expense"
        db.execute("""
            INSERT INTO transactions VALUES (?,?,?,?,?,?,?,?,0)
            ON CONFLICT(id) DO UPDATE SET account_id=excluded.account_id,date=excluded.date,
              name=excluded.name,amount_minor=excluded.amount_minor,currency=excluded.currency,
              kind=excluded.kind,pending=excluded.pending,removed=0
        """, (tx["transaction_id"], tx["account_id"], tx["date"], tx.get("merchant_name") or tx["name"],
              amount, currency, kind, int(tx["pending"])))
        if tx.get("pending_transaction_id"):
            superseded.append(tx["pending_transaction_id"])
    db.executemany("UPDATE transactions SET removed=1 WHERE id=?",
                   [(value,) for value in superseded] + [(tx["transaction_id"],) for tx in removed])
    if db.execute("SELECT 1 FROM sqlite_schema WHERE name='preview_edits'").fetchone():
        for tx in added + modified:
            if tx.get("pending_transaction_id"):
                db.execute("""
                    INSERT OR IGNORE INTO preview_edits
                    SELECT ?,merchant,kind,category_id,notes FROM preview_edits
                    WHERE transaction_id=? AND EXISTS (SELECT 1 FROM transactions WHERE id=?)
                """, (tx["transaction_id"], tx["pending_transaction_id"], tx["transaction_id"]))
                db.execute("""INSERT OR IGNORE INTO transaction_reviews
                    SELECT ?,state,origin,updated_at FROM transaction_reviews WHERE transaction_id=?
                    AND EXISTS (SELECT 1 FROM transactions WHERE id=?)""",
                           (tx['transaction_id'], tx['pending_transaction_id'], tx['transaction_id']))
    apply_rules(db, True, [tx['transaction_id'] for tx in added + modified])


def sandbox_router(database: Path, *, client_factory=plaid_client, scenario='banking'):
    path = sandbox_path(database, scenario)
    router = APIRouter(prefix='/api/plaid/investments-sandbox' if scenario == 'investments' else '/api/plaid/sandbox')

    def keys():
        return credentials(database.parent.parent)

    def client():
        value = keys()
        if not value:
            raise HTTPException(503, "Set the local Plaid Sandbox credentials first.")
        return client_factory(value)

    def initialize():
        with connect(path, write=True) as db:
            for statement in SCHEMA:
                db.execute(statement)
            initialize_preview(db)

    def upstream(error):
        if error.code in {"ITEM_LOGIN_REQUIRED", "USER_PERMISSION_REVOKED"}:
            with connect(path, write=True) as db:
                db.execute("UPDATE connection SET needs_reauth=1 WHERE id=1")
            return HTTPException(409, "Reconnect the existing Sandbox Item.")
        if error.code == "PRODUCT_NOT_READY":
            return HTTPException(409, "Plaid is preparing history. Try again shortly.")
        if error.code in {'INVALID_FIELD', 'PRODUCTS_NOT_SUPPORTED', 'INVALID_PRODUCT'}:
            return HTTPException(409, f'Plaid could not start this request ({error.code}). Check product support for this test institution. Your saved connection is unchanged; the terminal includes the request ID.')
        return HTTPException(502, "Plaid Sandbox request failed. Check the terminal for the request ID.")

    @router.get("")
    def status():
        if not path.exists():
            return {"configured": bool(keys()), "connection": None, "pending_session": None,
                    "accounts": [], "transactions": []}
        initialize()
        with connect(path) as db:
            connection = db.execute("""
                SELECT history_status,synced_at,balance_checked_at,needs_reauth FROM connection WHERE id=1
            """).fetchone()
            accounts = [dict(row) for row in db.execute("SELECT * FROM accounts WHERE active=1 ORDER BY name,id")]
            transactions = [dict(row) for row in db.execute("""
                SELECT t.id,t.date,t.name,t.amount_minor,t.currency,t.kind,t.pending,a.name AS account_name
                FROM transactions t JOIN accounts a ON a.id=t.account_id
                WHERE t.removed=0 ORDER BY t.date DESC,t.id DESC LIMIT 50
            """)]
            pending = db.execute("SELECT session FROM link WHERE id=1").fetchone()
        return {"configured": bool(keys()), "connection": dict(connection) if connection else None,
                "pending_session": pending["session"] if pending else None,
                "accounts": accounts, "transactions": transactions}

    @router.post("/link")
    def start_link(reconnect: bool = False):
        call = client()
        initialize()
        try:
            with connect(path, write=True) as db:
                connection = db.execute("SELECT token FROM connection WHERE id=1").fetchone()
                if bool(connection) != reconnect:
                    raise HTTPException(409, "Use Reconnect for an existing Item, or connect a test bank first.")
                existing = db.execute("SELECT expiration FROM link WHERE id=1").fetchone()
                if existing and datetime.fromisoformat(existing["expiration"]) > now():
                    raise HTTPException(409, "A Link session is already open. Clear it before starting another.")
                options = {"access_token": protect(connection["token"], False).decode(),
                           "update": {"account_selection_enabled": True}} if reconnect else {
                    **({'products': ['investments']} if scenario == 'investments' else {
                        "products": ["transactions"], "additional_consented_products": ["investments", "liabilities"],
                        "transactions": {"days_requested": 730}}),
                }
                result = call("/link/token/create", retry=False, client_name="Personal Finance Sandbox",
                              language="en", country_codes=["US"], user={"client_user_id": "local-finance-owner"},
                              hosted_link={}, **options)
                session = str(uuid4())
                db.execute("DELETE FROM link")
                db.execute("INSERT INTO link VALUES (1,?,?,?,?)", (
                    session, "update" if reconnect else "new",
                    protect(result["link_token"].encode(), True), result["expiration"],
                ))
                return {"session": session, "url": result["hosted_link_url"]}
        except PlaidError as error:
            raise upstream(error) from None

    @router.delete("/link")
    def clear_link():
        if path.exists():
            initialize()
            with connect(path, write=True) as db:
                db.execute("DELETE FROM link")
        return {"cleared": True}

    @router.post("/link/{session}")
    def finish_link(session: UUID):
        call = client()
        initialize()
        try:
            with connect(path, write=True) as db:
                row = db.execute("SELECT * FROM link WHERE session=?", (str(session),)).fetchone()
                if not row:
                    raise HTTPException(404, "This Link session is no longer available.")
                result = call("/link/token/get", retry=False, link_token=protect(row["token"], False).decode())
                for linked in reversed(result.get("link_sessions") or []):
                    if not linked.get("finished_at"):
                        continue
                    items = (linked.get("results") or {}).get("item_add_results") or []
                    success = linked.get("on_success")
                    if not items and success is None and not any(
                        event.get("event_name") == "HANDOFF" for event in linked.get("events") or []
                    ):
                        db.execute("DELETE FROM link")
                        return {"status": "closed"}
                    if row["mode"] == "new":
                        public = (items[0] if items else success or {}).get("public_token")
                        if not public:
                            return {"status": "pending"}
                        exchanged = call("/item/public_token/exchange", retry=False, public_token=public)
                        db.execute("INSERT INTO connection(id,item_id,token) VALUES (1,?,?)", (
                            exchanged["item_id"], protect(exchanged["access_token"].encode(), True),
                        ))
                    else:
                        db.execute("UPDATE connection SET needs_reauth=0 WHERE id=1")
                    db.execute("DELETE FROM link")
                    return {"status": "connected"}
                if datetime.fromisoformat(row["expiration"]) <= now():
                    db.execute("DELETE FROM link")
                    return {"status": "expired"}
                return {"status": "pending"}
        except PlaidError as error:
            raise upstream(error) from None

    @router.post("/sync")
    def sync(fresh: bool = False, details: bool = False):
        if scenario == 'investments' and not fresh:
            raise HTTPException(409, 'Use Investments to sync holdings and activity for this scenario.')
        if fresh and details:
            raise HTTPException(422, "Choose either balance refresh or transaction details.")
        call = client()
        initialize()
        try:
            # One writer across app instances. Data and cursor commit together.
            with connect(path, write=True) as db:
                connection = db.execute("SELECT * FROM connection WHERE id=1").fetchone()
                if not connection:
                    raise HTTPException(404, "Connect a test bank first.")
                if connection["needs_reauth"]:
                    raise HTTPException(409, "Reconnect the existing Item first.")
                token = protect(connection["token"], False).decode()
                if fresh:
                    last = connection["balance_checked_at"]
                    if last and (now() - datetime.fromisoformat(last)).total_seconds() < 30:
                        raise HTTPException(429, "Wait 30 seconds between fresh balance checks.")
                    result = call("/accounts/balance/get", access_token=token)
                    save_accounts(db, result["accounts"], "fresh")
                    db.execute("UPDATE connection SET balance_checked_at=? WHERE id=1", (now().isoformat(),))
                    record_preview_snapshot(db)
                    return {"updated": "balances"}
                cursor, history, added, modified, removed = changes(call, token, None if details else connection["cursor"])
                result = call("/accounts/get", access_token=token)
                db.execute("UPDATE accounts SET active=0")
                db.executemany("UPDATE accounts SET active=1 WHERE id=?", [(a["account_id"],) for a in result["accounts"]])
                # Metadata changes must survive even when retaining a fresher balance observation.
                db.executemany("UPDATE accounts SET name=?,mask=?,type=?,subtype=? WHERE id=?", [
                    (a["name"], a.get("mask"), a["type"], a.get("subtype"), a["account_id"])
                    for a in result["accounts"]
                ])
                # Keep the last explicit live observation rather than replace it with an older cache.
                cached = [a for a in result["accounts"] if not db.execute(
                    "SELECT 1 FROM accounts WHERE id=? AND source='fresh'", (a["account_id"],)
                ).fetchone()]
                save_accounts(db, cached, "cached")
                save_transactions(db, added, modified, removed)
                db.execute("UPDATE connection SET cursor=?,history_status=?,synced_at=? WHERE id=1",
                           (cursor or None, history, now().isoformat()))
                record_preview_snapshot(db)
                return {"updated": "transactions", "added": len(added), "modified": len(modified), "removed": len(removed)}
        except PlaidError as error:
            raise upstream(error) from None

    @router.get('/investments')
    def investment_positions():
        if not path.exists():
            return {'state': None, 'accounts': [], 'holdings': [], 'history': []}
        initialize()
        with connect(path) as db:
            return project_investments(db)

    @router.get('/investments/activity')
    def investment_activity(account: str | None = None, start: date | None = None, end: date | None = None,
                            search: str = Query('', max_length=120), kind: str | None = None,
                            page: int = Query(1, ge=1, le=100000)):
        if start and end and start > end:
            raise HTTPException(422, 'Start date must be before end date.')
        if not path.exists():
            return {'rows': [], 'total': 0, 'page': page, 'page_size': 25}
        initialize()
        with connect(path) as db:
            return project_activity(db, account, start.isoformat() if start else None,
                                    end.isoformat() if end else None, search, kind, page)

    @router.post('/investments/sync')
    def sync_investments(activity: bool = False, full: bool = False):
        call = client()
        initialize()
        try:
            with connect(path, write=True) as db:
                connection = db.execute('SELECT * FROM connection WHERE id=1').fetchone()
                if not connection:
                    raise HTTPException(404, 'Connect a test bank first.')
                if connection['needs_reauth']:
                    raise HTTPException(409, 'Reconnect the existing Item first.')
                state = db.execute('SELECT * FROM investment_state WHERE id=1').fetchone()
                last = state['activity_at' if activity else 'holdings_at'] if state else None
                if last and (now() - datetime.fromisoformat(last)).total_seconds() < 30:
                    raise HTTPException(429, 'Wait 30 seconds between investment syncs.')
                token = protect(connection['token'], False).decode()
                stamp = now().isoformat()
                if activity:
                    # Reconcile an overlap for recent corrections; initial sync backfills two years.
                    start = max(history_start(date.today()), date.fromisoformat(state['activity_end']) - timedelta(days=7)) if not full and state and state['activity_end'] else history_start(date.today())
                    end = date.today().isoformat()
                    rows = fetch_activity(call, token, start.isoformat(), end)
                    known = {row[0] for row in db.execute('SELECT id FROM accounts')}
                    if any(tx['account_id'] not in known for tx, _ in rows):
                        raise HTTPException(409, 'Sync holdings first to load the investment accounts.')
                    save_activity(db, rows, start.isoformat(), end, stamp)
                    return {'updated': 'activity', 'count': len(rows)}
                result = call('/investments/holdings/get', access_token=token)
                # Preserve an explicit fresh balance, just as the normal bank sync does.
                cached = [a for a in result['accounts'] if a['type'] == 'investment' and not db.execute(
                    "SELECT 1 FROM accounts WHERE id=? AND source='fresh'", (a['account_id'],)).fetchone()]
                save_accounts(db, cached, 'cached')
                save_holdings(db, result, stamp)
                record_preview_snapshot(db)
                return {'updated': 'holdings', 'count': len(result['holdings'])}
        except ValueError as error:
            raise HTTPException(409, str(error)) from None
        except PlaidError as error:
            if error.code in {'PRODUCT_NOT_SUPPORTED', 'PRODUCTS_NOT_SUPPORTED', 'NO_INVESTMENT_ACCOUNTS', 'NO_ACCOUNTS'}:
                raise HTTPException(409, 'This test bank does not provide supported investment data. Saved data and bank sync are unchanged. Use an investment-capable Sandbox institution in a separate test setup; do not replace your saved connection.') from None
            if error.code in {'ADDITIONAL_CONSENT_REQUIRED', 'ACCESS_NOT_GRANTED', 'INVALID_PRODUCT'}:
                raise HTTPException(409, 'Investments access or consent is unavailable. Check Plaid product access and update consent for the existing Item; do not create a duplicate connection.') from None
            raise upstream(error) from None

    router.include_router(sandbox_preview_router(database, initialize, path=path))
    return router


def investment_sandbox_router(database):
    return sandbox_router(database, scenario='investments')

