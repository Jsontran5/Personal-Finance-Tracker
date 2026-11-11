from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import uuid4
import sqlite3

from .db import DATABASE, ROOT


def create_backup(source: Path, directory: Path) -> Path:
    if not source.is_file():
        raise FileNotFoundError("Start the app to create its database first.")
    directory.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%SZ")
    destination = directory / f"finance-{timestamp}-{uuid4().hex[:8]}.db"
    with NamedTemporaryFile(dir=directory, suffix=".tmp", delete=False) as file:
        temporary = Path(file.name)
    try:
        with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as original:
            with closing(sqlite3.connect(temporary)) as backup:
                original.backup(backup)
                if backup.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                    raise RuntimeError("The backup failed its integrity check.")
                if backup.execute("PRAGMA foreign_key_check").fetchone() is not None:
                    raise RuntimeError("The backup failed its foreign key check.")
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


if __name__ == "__main__":
    print(create_backup(DATABASE, ROOT / "backups"))

