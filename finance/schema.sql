CREATE TABLE accounts (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL CHECK(length(trim(name)) > 0),
    kind TEXT NOT NULL CHECK(kind IN ('cash','checking','savings','investment','property','credit','loan','other')),
    currency TEXT NOT NULL CHECK(length(currency) = 3),
    include_in_net_worth INTEGER NOT NULL CHECK(include_in_net_worth IN (0,1))
);
CREATE TABLE settings (
    id INTEGER PRIMARY KEY CHECK(id = 1),
    primary_account_id INTEGER REFERENCES accounts(id)
);
INSERT INTO settings(id) VALUES (1);

CREATE TABLE categories (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL COLLATE NOCASE UNIQUE,
    kind TEXT NOT NULL CHECK(kind IN ('expense','income'))
);
INSERT INTO categories(name, kind) VALUES
    ('Groceries','expense'), ('Dining','expense'), ('Housing','expense'),
    ('Transportation','expense'), ('Utilities','expense'), ('Shopping','expense'),
    ('Health','expense'), ('Other spending','expense'), ('Paycheck','income'), ('Other income','income');

CREATE TABLE categorization_rules (
    id INTEGER PRIMARY KEY,
    pattern TEXT NOT NULL,
    category_id INTEGER NOT NULL REFERENCES categories(id)
);

CREATE TABLE balance_snapshots (
    id INTEGER PRIMARY KEY,
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    effective_date TEXT NOT NULL,
    balance_minor INTEGER NOT NULL,
    transaction_cutoff INTEGER NOT NULL,
    recorded_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    notes TEXT NOT NULL DEFAULT ''
);
CREATE INDEX balances_by_account ON balance_snapshots(account_id, effective_date DESC, id DESC);

CREATE TABLE transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    destination_id INTEGER REFERENCES accounts(id),
    category_id INTEGER REFERENCES categories(id),
    kind TEXT NOT NULL CHECK(kind IN ('expense','income','refund','transfer','investment')),
    amount_minor INTEGER NOT NULL CHECK(amount_minor > 0),
    date TEXT NOT NULL,
    merchant TEXT NOT NULL,
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    CHECK((kind = 'transfer' AND destination_id IS NOT NULL AND destination_id <> account_id)
          OR (kind <> 'transfer' AND destination_id IS NULL))
);
CREATE INDEX transactions_by_date ON transactions(date DESC, id DESC);
CREATE INDEX transactions_by_account ON transactions(account_id, date);
CREATE INDEX transfers_by_destination ON transactions(destination_id, date) WHERE destination_id IS NOT NULL;

CREATE TABLE monthly_budgets (
    month TEXT NOT NULL,
    category_id INTEGER NOT NULL REFERENCES categories(id),
    limit_minor INTEGER NOT NULL CHECK(limit_minor >= 0),
    PRIMARY KEY(month, category_id)
);

CREATE TABLE recurring_templates (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    category_id INTEGER REFERENCES categories(id),
    kind TEXT NOT NULL CHECK(kind IN ('expense','income')),
    amount_minor INTEGER NOT NULL CHECK(amount_minor > 0),
    merchant TEXT NOT NULL,
    notes TEXT NOT NULL DEFAULT ''
);

CREATE TABLE net_worth_snapshots (
    id INTEGER PRIMARY KEY,
    effective_date TEXT NOT NULL,
    recorded_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    assets_minor INTEGER NOT NULL,
    liabilities_minor INTEGER NOT NULL
);
CREATE INDEX net_worth_by_date ON net_worth_snapshots(effective_date, id);
CREATE TABLE net_worth_snapshot_accounts (
    snapshot_id INTEGER NOT NULL REFERENCES net_worth_snapshots(id),
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    balance_minor INTEGER NOT NULL,
    PRIMARY KEY(snapshot_id, account_id)
);

