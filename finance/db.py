from contextlib import contextmanager
from pathlib import Path
import sqlite3
import os
import threading
import time

_locks = {}
_locks_guard = threading.Lock()
_held = threading.local()


@contextmanager
def database_lock(path: Path):
    """Serialize database operations, including restores, across threads and app instances."""
    key = os.path.normcase(str(path.resolve()))
    with _locks_guard:
        lock = _locks.setdefault(key, threading.RLock())
    with lock:
        held = getattr(_held, 'paths', set())
        if key in held:
            yield
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path.with_name(path.name + '.operation-lock'), 'a+b') as handle:
            if handle.seek(0, 2) == 0:
                handle.write(b'\0'); handle.flush()
            handle.seek(0)
            deadline = time.monotonic() + 20
            while True:
                try:
                    if os.name == 'nt':
                        import msvcrt
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        error = sqlite3.OperationalError('The database is busy. Try again shortly.')
                        error.sqlite_errorcode = sqlite3.SQLITE_BUSY
                        raise error
                    time.sleep(.05)
            _held.paths = held | {key}
            try:
                yield
            finally:
                _held.paths = held
                handle.seek(0)
                if os.name == 'nt':
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

ROOT = Path(__file__).resolve().parents[1]
DATABASE = ROOT / "data" / "finance.db"
SCHEMA = Path(__file__).with_name("schema.sql")


@contextmanager
def connect(path: Path, *, write: bool = False):
    with database_lock(path):
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
            version = 2
        if version == 2:
            from .transfers import initialize_transfers
            initialize_transfers(db)
            db.execute('PRAGMA user_version = 3')
        elif version != 3:
            raise RuntimeError(f"Unsupported database version: {version}")

