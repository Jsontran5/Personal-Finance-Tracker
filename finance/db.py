from contextlib import contextmanager
from pathlib import Path
import sqlite3

ROOT = Path(__file__).resolve().parents[1]
DATABASE = ROOT / "data" / "finance.db"
SCHEMA = Path(__file__).with_name("schema.sql")


@contextmanager
def connect(path: Path, *, write: bool = False):
    db = sqlite3.connect(path, timeout=5, isolation_level=None)
    db.row_factory = sqlite3.Row
    try:
        db.execute("PRAGMA foreign_keys = ON")
        db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def initialize(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with connect(path, write=True) as db:
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version == 0:
            if db.execute("SELECT 1 FROM sqlite_schema WHERE type='table'").fetchone():
                raise RuntimeError("An unversioned database already exists at the configured path.")
            statement = ""
            for line in SCHEMA.read_text(encoding="utf-8").splitlines(keepends=True):
                statement += line
                if sqlite3.complete_statement(statement):
                    db.execute(statement)
                    statement = ""
            db.execute("PRAGMA user_version = 1")
            version = 1
        if version == 1:
            from .review import initialize_review
            initialize_review(db)
            db.execute('PRAGMA user_version = 2')
        elif version != 2:
            raise RuntimeError(f"Unsupported database version: {version}")

