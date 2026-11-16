from datetime import date
import sqlite3
from .spending import previous_month, spending_report
from .transfers import project_transfers
from .transaction_flow import project_flow

LIABILITIES = {"credit", "loan"}


def monthly_activity(transactions):
    income = spending = 0
    category_actual = {}
    for tx in transactions:
        if tx["currency"] != "USD" or tx.get("pending") or tx.get("needs_review") or tx.get('transfer_pair_id'):
            continue
        if tx["kind"] == "income":
            income += tx["amount_minor"]
        if tx["kind"] in {"expense", "refund"}:
            amount = tx["amount_minor"] * (1 if tx["kind"] == "expense" else -1)
            spending += amount
            key = tx["category_id"]
            category_actual[key] = category_actual.get(key, 0) + amount
    return income, spending, category_actual


def account_balances(db: sqlite3.Connection, day: date):
    return [dict(row) for row in db.execute("""
        SELECT a.*, NULLIF(json_extract(d.payload,'$.subtype'),'other') AS subtype, s.effective_date AS balance_date,
               CASE WHEN s.id IS NULL THEN NULL ELSE s.balance_minor +
                 COALESCE((SELECT SUM(CASE WHEN t.kind IN ('income','refund')
                                           THEN t.amount_minor WHEN t.kind = 'investment' THEN 0 ELSE -t.amount_minor END)
                           FROM transactions t
                           WHERE t.account_id = a.id AND (t.date > s.effective_date OR (t.date = s.effective_date AND t.id > s.transaction_cutoff)) AND t.date <= :day), 0) +
                 COALESCE((SELECT SUM(t.amount_minor) FROM transactions t
                           WHERE t.destination_id = a.id AND (t.date > s.effective_date OR (t.date = s.effective_date AND t.id > s.transaction_cutoff)) AND t.date <= :day), 0)
               END AS balance_minor
        FROM accounts a
        LEFT JOIN manual_debt_details d ON d.account_id=a.id
        LEFT JOIN balance_snapshots s ON s.id = (
            SELECT id FROM balance_snapshots WHERE account_id = a.id AND effective_date <= :day
            ORDER BY effective_date DESC, id DESC LIMIT 1
        )
        ORDER BY a.id
    """, {"day": day.isoformat()})]


def totals(accounts):
    assets = liabilities = 0
    for account in accounts:
        amount = account["balance_minor"]
        if not account["include_in_net_worth"] or account["currency"] != "USD" or amount is None:
            continue
        if amount < 0:
            liabilities -= amount
        else:
            assets += amount
    return {"assets_minor": assets, "liabilities_minor": liabilities,
            "net_worth_minor": assets - liabilities}


def attach_snapshot_accounts(history, accounts):
    by_snapshot = {}
    for account in accounts:
        by_snapshot.setdefault(account["snapshot_id"], []).append({
            "account_id": account["account_id"], "balance_minor": account["balance_minor"],
        })
    for point in history:
        point["accounts"] = by_snapshot.get(point.pop("snapshot_id"), [])
    return history


def record_net_worth(db: sqlite3.Connection, day: date):
    accounts = account_balances(db, day)
    included = [a for a in accounts if a["include_in_net_worth"] and a["currency"] == "USD"
                and a["balance_minor"] is not None]
    if not included:
        # Preserve the transition to zero after excluding the last account.
        if not db.execute("SELECT 1 FROM net_worth_snapshots LIMIT 1").fetchone():
            return
    summary = totals(included)
    snapshot = db.execute("""
        INSERT INTO net_worth_snapshots(effective_date, assets_minor, liabilities_minor) VALUES (?, ?, ?)
    """, (day.isoformat(), summary["assets_minor"], summary["liabilities_minor"])).lastrowid
    db.executemany("""
        INSERT INTO net_worth_snapshot_accounts(snapshot_id, account_id, name, kind, balance_minor)
        VALUES (?, ?, ?, ?, ?)
    """, [(snapshot, a["id"], a["name"], a["kind"], a["balance_minor"]) for a in included])


def month_transactions(db, month):
    return project_flow(project_transfers(db, [dict(row) for row in db.execute("""
        SELECT t.*, a.name AS account_name, a.currency, c.name AS category_name,
               d.name AS destination_name
        FROM transactions t JOIN accounts a ON a.id = t.account_id
        LEFT JOIN categories c ON c.id = t.category_id
        LEFT JOIN accounts d ON d.id = t.destination_id
        WHERE t.date >= ? AND t.date <= ? ORDER BY t.date DESC, t.id DESC
    """, (month + "-01", month + "-31"))], False))


def overview(db: sqlite3.Connection, day: date, month: str):
    accounts = account_balances(db, day)
    transactions = month_transactions(db, month)
    prior = previous_month(month)
    previous_transactions = month_transactions(db, prior) if prior else []
    income, spending, category_actual = monthly_activity(transactions)
    categories = [dict(row) for row in db.execute("SELECT * FROM categories ORDER BY name")]
    limits = dict(db.execute("SELECT category_id, limit_minor FROM monthly_budgets WHERE month=?", (month,)))
    _, _, previous_actual = monthly_activity(previous_transactions)
    budgets = [{**c, "limit_minor": limits.get(c["id"]), "actual_minor": category_actual.get(c["id"], 0),
                "previous_actual_minor": previous_actual.get(c["id"], 0)}
               for c in categories if c["kind"] == "expense"]
    history = [dict(row) for row in db.execute("""
        SELECT s.id AS snapshot_id, s.effective_date AS date, s.assets_minor, s.liabilities_minor,
               s.assets_minor - s.liabilities_minor AS net_worth_minor
        FROM net_worth_snapshots s
        WHERE s.id IN (SELECT MAX(id) FROM net_worth_snapshots GROUP BY effective_date)
        ORDER BY s.effective_date
    """)]
    attach_snapshot_accounts(history, db.execute("""
        SELECT snapshot_id,account_id,balance_minor FROM net_worth_snapshot_accounts
        WHERE snapshot_id IN (SELECT MAX(id) FROM net_worth_snapshots GROUP BY effective_date)
    """))
    return {**totals(accounts), "accounts": accounts, "categories": categories, "budgets": budgets,
            "spending": spending_report(transactions, previous_transactions, month, day),
            "transactions": transactions, "history": history, "income_minor": income,
            "spending_minor": spending, "cash_flow_minor": income - spending,
            "uncategorized_minor": category_actual.get(None, 0),
            "recent_entries": [dict(row) for row in db.execute(
                "SELECT merchant, category_id FROM transactions ORDER BY id DESC LIMIT 100"
            )],
            "primary_account_id": db.execute("SELECT primary_account_id FROM settings").fetchone()[0],
            "templates": [dict(row) for row in db.execute("SELECT * FROM recurring_templates ORDER BY name")]}


