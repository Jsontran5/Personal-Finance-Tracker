"""Local display names; provider IDs, source names and financial records stay separate."""


def initialize_names(db):
    db.execute('''CREATE TABLE IF NOT EXISTS account_names (
        account_id TEXT PRIMARY KEY REFERENCES accounts(id),provider_name TEXT NOT NULL,
        nickname TEXT CHECK(nickname IS NULL OR length(nickname) BETWEEN 1 AND 120))''')
    db.execute('INSERT OR IGNORE INTO account_names(account_id,provider_name) SELECT id,name FROM accounts')


def save_provider_name(db, identifier, name):
    initialize_names(db)
    db.execute('''INSERT INTO account_names(account_id,provider_name) VALUES (?,?)
        ON CONFLICT(account_id) DO UPDATE SET provider_name=excluded.provider_name''', (identifier, name))
    db.execute('''UPDATE accounts SET name=(SELECT COALESCE(nickname,provider_name)
        FROM account_names WHERE account_id=?) WHERE id=?''', (identifier, identifier))
