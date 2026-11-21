"""Dashboard projections and local edits for the isolated Plaid Sandbox store."""
from datetime import date, datetime, timezone
import json
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, StringConstraints

from .db import connect
from .transaction_flow import project_flow
from .ledger import monthly_activity, totals
from .spending import previous_month, spending_report
from .analytics import analytics_router
from .balance_history import account_observations, banking_estimates, snapshot_history
from .review import initialize_review, mark, review_router
from .transfers import initialize_transfers, project_transfers, require_unlinked

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
Kind = Literal["expense", "income", "refund", "transfer", "investment"]


class AccountNickname(BaseModel):
    nickname: Name | None = None


class TransactionEdit(BaseModel):
    merchant: Name
    category_id: int | None = None
    kind: Kind
    notes: Annotated[str, StringConstraints(max_length=2000)] = ""


class CategoryCreate(BaseModel):
    name: Name
    kind: Literal["expense", "income"]


class BudgetEdit(BaseModel):
    limit_minor: Annotated[int, Field(strict=True, ge=0, le=1_000_000_000_000)] | None


def initialize_preview(db):
    from .budgeting import initialize_budget
    initialize_budget(db)
    version = db.execute("PRAGMA user_version").fetchone()[0]
    if version in {7, 8, 9, 10, 11, 12, 13, 14}:
        from .account_names import initialize_names
        initialize_names(db)
        if version == 7:
            db.execute('PRAGMA user_version=11')
        return
    if version == 6:
        from .debt import initialize_liabilities
        initialize_liabilities(db)
        db.execute('PRAGMA user_version = 7')
        initialize_preview(db)
        return
    if version == 5:
        from .investments import initialize_investments
        initialize_investments(db)
        db.execute('PRAGMA user_version = 6')
        initialize_preview(db)
        return
    if version == 4:
        initialize_transfers(db)
        db.execute('PRAGMA user_version = 5')
        initialize_preview(db)
        return
    if version == 3:
        initialize_review(db)
        db.execute('PRAGMA user_version = 4')
        initialize_preview(db)
        return
    if version == 2:
        db.execute("ALTER TABLE accounts ADD COLUMN subtype TEXT")
        db.execute("PRAGMA user_version = 3")
        initialize_preview(db)
        return
    if version == 1:
        db.execute("""
            CREATE TABLE transaction_details (
              transaction_id TEXT PRIMARY KEY,payload TEXT NOT NULL,retrieved_at TEXT NOT NULL)
        """)
        db.execute("ALTER TABLE accounts ADD COLUMN active INTEGER NOT NULL DEFAULT 1")
        db.execute("PRAGMA user_version = 2")
        initialize_preview(db)
        return
    if version != 0:
        raise RuntimeError("Unsupported Sandbox schema version.")
    db.execute("""
        CREATE TABLE IF NOT EXISTS preview_categories (
          id INTEGER PRIMARY KEY, name TEXT NOT NULL COLLATE NOCASE UNIQUE,
          kind TEXT NOT NULL CHECK(kind IN ('expense','income')))
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS preview_budgets (
          month TEXT NOT NULL, category_id INTEGER NOT NULL REFERENCES preview_categories(id),
          limit_minor INTEGER NOT NULL CHECK(limit_minor >= 0), PRIMARY KEY(month,category_id))
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS preview_edits (
          transaction_id TEXT PRIMARY KEY REFERENCES transactions(id),
          merchant TEXT NOT NULL, kind TEXT NOT NULL,
          category_id INTEGER REFERENCES preview_categories(id), notes TEXT NOT NULL)
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS preview_snapshots (
          id INTEGER PRIMARY KEY, recorded_at TEXT NOT NULL, date TEXT NOT NULL,
          assets_minor INTEGER NOT NULL, liabilities_minor INTEGER NOT NULL)
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS preview_snapshot_accounts (
          snapshot_id INTEGER NOT NULL REFERENCES preview_snapshots(id),
          account_id TEXT NOT NULL, name TEXT NOT NULL, currency TEXT,
          balance_minor INTEGER, source TEXT, checked_at TEXT,
          PRIMARY KEY(snapshot_id,account_id))
    """)
    if not db.execute("SELECT 1 FROM preview_categories LIMIT 1").fetchone():
        db.executemany("INSERT INTO preview_categories(name,kind) VALUES (?,?)", [
            ("Groceries", "expense"), ("Dining", "expense"), ("Housing", "expense"),
            ("Transportation", "expense"), ("Utilities", "expense"), ("Shopping", "expense"),
            ("Health", "expense"), ("Other spending", "expense"),
            ("Paycheck", "income"), ("Other income", "income"),
        ])
    db.execute("PRAGMA user_version = 1")
    initialize_preview(db)


def preview_accounts(db):
    multi = db.execute("SELECT 1 FROM sqlite_schema WHERE name='connection_accounts'").fetchone()
    inclusion = "COALESCE((SELECT included FROM connection_accounts m WHERE m.account_id=accounts.id),1)" if multi else '1'
    institution = "(SELECT COALESCE(c.name,'Manual') FROM connection_accounts m LEFT JOIN multi_connections c ON c.id=m.connection_id WHERE m.account_id=accounts.id)" if multi else 'NULL'
    manual_id = '(SELECT manual_id FROM connection_accounts m WHERE m.account_id=accounts.id)' if multi else 'NULL'
    accounts = [dict(row) for row in db.execute(f"""
        SELECT rowid AS id, name,
          (SELECT provider_name FROM account_names n WHERE n.account_id=accounts.id) AS provider_name,
          (SELECT nickname FROM account_names n WHERE n.account_id=accounts.id) AS nickname,
          type AS provider_type, subtype,
          CASE type WHEN 'depository' THEN COALESCE(subtype,'depository') WHEN 'loan' THEN 'loan'
            WHEN 'credit' THEN 'credit' WHEN 'investment' THEN 'investment' ELSE 'other' END AS kind,
          currency, current_minor AS balance_minor, checked_at AS balance_date,
          {inclusion} AS include_in_net_worth, source AS balance_source,{institution} AS institution_name,{manual_id} AS manual_id,
          (SELECT COUNT(*) FROM transactions t WHERE t.account_id=accounts.id AND t.removed=0) AS transaction_count
        FROM accounts WHERE active=1 ORDER BY rowid
    """)]
    from .freshness import account_freshness
    return account_freshness(db, accounts)


def record_preview_snapshot(db):
    accounts = preview_accounts(db)
    if not any(a["balance_minor"] is not None for a in accounts):
        return
    summary = totals(accounts)
    recorded_at = datetime.now(timezone.utc).isoformat()
    snapshot = db.execute("""
        INSERT INTO preview_snapshots(recorded_at,date,assets_minor,liabilities_minor) VALUES (?,?,?,?)
    """, (recorded_at, date.today().isoformat(), summary["assets_minor"], summary["liabilities_minor"])).lastrowid
    db.execute("""
        INSERT INTO preview_snapshot_accounts
        SELECT ?,id,name,currency,current_minor,source,checked_at FROM accounts WHERE active=1
    """, (snapshot,))
    return snapshot


def preview_transactions(db, month):
    return project_flow(project_transfers(db, [dict(row) for row in db.execute("""
        SELECT t.amount_minor AS _flow_amount_minor,d.payload AS _flow_payload,t.rowid AS id, a.rowid AS account_id, NULL AS destination_id,
          e.category_id, ABS(t.amount_minor) AS amount_minor,
          COALESCE(e.kind, CASE WHEN t.kind='credit' THEN 'refund' ELSE t.kind END) AS kind,
          COALESCE(e.merchant,t.name) AS merchant, t.name AS source_merchant, COALESCE(e.notes,'') AS notes,
          t.date,t.currency,t.pending,a.name AS account_name,c.name AS category_name,
          json_extract(d.payload,'$.personal_finance_category.primary') AS bank_category,
          NULL AS destination_name, CASE WHEN t.kind='credit' AND e.kind IS NULL THEN 1 ELSE 0 END AS needs_review,
          COALESCE(json_extract(d.payload,'$._finance_manual'),0) AS manual_source,
          CASE WHEN json_extract(d.payload,'$._finance_manual') THEN json_extract(d.payload,'$.id') END AS manual_transaction_id
        FROM transactions t JOIN accounts a ON a.id=t.account_id
        LEFT JOIN preview_edits e ON e.transaction_id=t.id
        LEFT JOIN preview_categories c ON c.id=e.category_id
        LEFT JOIN transaction_details d ON d.transaction_id=t.id
        WHERE t.removed=0 AND t.date>=? AND t.date<=?
        ORDER BY t.date DESC,t.rowid DESC
    """, (month + "-01", month + "-31"))], True), provider=True)


def preview_overview(db, month):
    accounts = preview_accounts(db)
    transactions = preview_transactions(db, month)
    income, spending, actual = monthly_activity(transactions)
    categories = [dict(row) for row in db.execute("SELECT * FROM preview_categories ORDER BY name")]
    limits = dict(db.execute("SELECT category_id,limit_minor FROM preview_budgets WHERE month=?", (month,)))
    previous = previous_month(month)
    previous_transactions = preview_transactions(db, previous) if previous else []
    _, _, previous_actual = monthly_activity(previous_transactions)
    history = snapshot_history(db)
    from .budgeting import overview_budgets
    budgets,method=overview_budgets(db,True,month,[{**c,'limit_minor':limits.get(c['id']),'actual_minor':actual.get(c['id'],0),'previous_actual_minor':previous_actual.get(c['id'],0)} for c in categories if c['kind']=='expense'])
    return {
        **totals(accounts), "accounts": accounts, "transactions": transactions, "categories": categories,
        "spending": spending_report(transactions, previous_transactions, month, date.today()),
        "budgets":budgets,"budget_method":method,
        "history": history, "income_minor": income, "spending_minor": spending,
        "cash_flow_minor": income - spending, "uncategorized_minor": actual.get(None, 0),
        "pending_count": sum(t["pending"] for t in transactions),
        "missing_balance_count": sum(a["balance_minor"] is None for a in accounts),
        "review_count": sum(t["needs_review"] for t in transactions),
        "primary_account_id": None, "recent_entries": [], "templates": [],
    }


def sandbox_preview_router(database, initialize_sandbox, *, path=None):
    path = path or database.with_suffix(".plaid-sandbox.db")
    router = APIRouter(prefix="/preview")

    def initialize():
        initialize_sandbox()
        with connect(path, write=True) as db:
            initialize_preview(db)
            if not db.execute("SELECT 1 FROM preview_snapshots LIMIT 1").fetchone():
                record_preview_snapshot(db)

    router.include_router(review_router(path, sandbox=True, initialize=initialize))
    router.include_router(analytics_router(path, preview_transactions, initialize))
    from .budgeting import budget_router
    router.include_router(budget_router(path, sandbox=True, initialize=initialize, source_database=database))

    @router.get('/balance-history')
    def estimated_history():
        initialize()
        with connect(path) as db:
            return banking_estimates(db)

    def require_category(db, category_id, kind):
        row = db.execute("SELECT kind FROM preview_categories WHERE id=?", (category_id,)).fetchone()
        if row is None or row["kind"] != kind:
            raise HTTPException(422, "Choose a category matching this transaction type.")

    @router.get("")
    def get_preview(month: Annotated[str, Query(pattern=r"^[0-9]{4}-(0[1-9]|1[0-2])$")]):
        if month.startswith("0000"):
            raise HTTPException(422, "Choose a valid reporting month.")
        initialize()
        with connect(path) as db:
            return preview_overview(db, month)

    @router.put("/transactions/{row_id}")
    def edit_transaction(row_id: int, item: TransactionEdit):
        initialize()
        with connect(path, write=True) as db:
            row = db.execute("SELECT id,amount_minor FROM transactions WHERE rowid=? AND removed=0", (row_id,)).fetchone()
            if row is None:
                raise HTTPException(404, "Transaction no longer available. Refresh this view.")
            manual = db.execute("SELECT 1 FROM transaction_details WHERE transaction_id=? AND json_extract(payload,'$._finance_manual')=1", (row['id'],)).fetchone()
            if manual:
                raise HTTPException(409, 'Manage this manual transaction in Manual.')
            require_unlinked(db, row['id'])
            if (row["amount_minor"] > 0 and item.kind in {"income", "refund"}) or (
                row["amount_minor"] < 0 and item.kind == "expense"
            ):
                raise HTTPException(422, "Classification must match the direction of this transaction.")
            if item.category_id is not None:
                if item.kind in {"transfer", "investment"}:
                    raise HTTPException(422, "Transfers and investment activity have no budget category.")
                require_category(db, item.category_id, "income" if item.kind == "income" else "expense")
            db.execute("""
                INSERT INTO preview_edits VALUES (?,?,?,?,?)
                ON CONFLICT(transaction_id) DO UPDATE SET merchant=excluded.merchant,kind=excluded.kind,
                  category_id=excluded.category_id,notes=excluded.notes
            """, (row["id"], item.merchant, item.kind, item.category_id, item.notes))
            mark(db, row['id'])
        return {"id": row_id}

    @router.get("/transactions/{row_id}/details")
    def transaction_details(row_id: int):
        initialize()
        with connect(path) as db:
            row = db.execute("""
                SELECT d.payload,d.retrieved_at FROM transactions t
                LEFT JOIN transaction_details d ON d.transaction_id=t.id
                WHERE t.rowid=? AND t.removed=0
            """, (row_id,)).fetchone()
            if row is None:
                raise HTTPException(404, "Transaction no longer available.")
            return {"provider": json.loads(row["payload"]) if row["payload"] else None,
                    "retrieved_at": row["retrieved_at"]}

    @router.put('/accounts/{row_id}/name')
    def rename_account(row_id: int, item: AccountNickname):
        initialize()
        import unicodedata
        if item.nickname and any(unicodedata.category(c).startswith('C') for c in item.nickname):
            raise HTTPException(422, 'Use a single-line account name without control characters.')
        with connect(path, write=True) as db:
            row = db.execute('SELECT id,source FROM accounts WHERE rowid=?', (row_id,)).fetchone()
            if row is None:
                raise HTTPException(404, 'Account not found.')
            if row['source'] == 'manual':
                raise HTTPException(409, 'Rename this account in Manual.')
            db.execute('UPDATE account_names SET nickname=? WHERE account_id=?', (item.nickname, row['id']))
            db.execute('UPDATE accounts SET name=(SELECT COALESCE(nickname,provider_name) FROM account_names WHERE account_id=?) WHERE id=?', (row['id'],row['id']))
            return dict(db.execute('SELECT a.name,n.provider_name,n.nickname FROM accounts a JOIN account_names n ON n.account_id=a.id WHERE a.id=?', (row['id'],)).fetchone())

    @router.get("/accounts/{row_id}/balances")
    def account_history(row_id: int):
        initialize()
        with connect(path) as db:
            account = db.execute("SELECT id FROM accounts WHERE rowid=?", (row_id,)).fetchone()
            if account is None:
                raise HTTPException(404, "Account not found.")
            return account_observations(db, account['id'])

    @router.post("/categories", status_code=201)
    def add_category(item: CategoryCreate):
        initialize()
        with connect(path, write=True) as db:
            cursor = db.execute("INSERT OR IGNORE INTO preview_categories(name,kind) VALUES (?,?)", (item.name, item.kind))
            if not cursor.rowcount:
                raise HTTPException(409, "That category already exists.")
            return {"id": cursor.lastrowid}

    @router.put("/budgets/{month}/{category_id}")
    def set_budget(month: str, category_id: int, item: BudgetEdit):
        try:
            if date.fromisoformat(month + "-01").isoformat()[:7] != month:
                raise ValueError
        except ValueError:
            raise HTTPException(422, "Choose a valid budget month.") from None
        initialize()
        with connect(path, write=True) as db:
            require_category(db, category_id, "expense")
            if item.limit_minor is None:
                db.execute("DELETE FROM preview_budgets WHERE month=? AND category_id=?", (month, category_id))
            else:
                db.execute("""
                    INSERT INTO preview_budgets VALUES (?,?,?)
                    ON CONFLICT(month,category_id) DO UPDATE SET limit_minor=excluded.limit_minor
                """, (month, category_id, item.limit_minor))
        return {"id": category_id}

    return router
