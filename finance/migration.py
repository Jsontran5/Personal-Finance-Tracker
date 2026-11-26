"""Offline, password-encrypted migration to a fresh installation.

No Plaid requests, credentials from .env, or plaintext token files are involved.
Keep the app stopped on both computers while exporting/importing.
"""
import argparse
from contextlib import ExitStack
from getpass import GetPassWarning, getpass
from io import BytesIO
import json
import os
from pathlib import Path
import sqlite3
import warnings
from tempfile import TemporaryDirectory
import zipfile

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from .db import DATABASE, connect, database_lock
from .plaid_vault import protect
from .recovery import (BackupStore, MAX_SIZE, VERSIONS, check_database, migrate,
                       snapshot, valid_selection, validate_current_schema)

HEADER = b'Finance-Migration-v1\0'
TOKEN_AAD = HEADER + b'token'


class MigrationError(ValueError):
    """Only fixed, intentionally public messages may be shown by the CLI."""


def validate_password(password):
    if not 12 <= len(password) <= 1024:
        raise MigrationError('Use a migration passphrase between 12 and 1024 characters.')


def password_key(password, salt):
    validate_password(password)
    return Scrypt(salt=salt, length=32, n=2**17, r=8, p=1).derive(password.encode('utf-8'))


def seal(key, value, aad):
    nonce = os.urandom(12)
    return nonce + AESGCM(key).encrypt(nonce, value, aad)


def unseal(key, value, aad):
    return AESGCM(key).decrypt(value[:12], value[12:], aad)


def rewrap(path, decode, encode):
    """Rewrite only a private database copy; transient Link sessions never migrate."""
    with connect(path, write=True) as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_schema WHERE type='table'")}
        for table in ('link', 'multi_sessions'):
            if table in tables:
                db.execute(f'DELETE FROM {table}')
        for table in ('connection', 'multi_connections'):
            if table in tables:
                for row in db.execute(f'SELECT id,token FROM {table} WHERE token IS NOT NULL').fetchall():
                    db.execute(f'UPDATE {table} SET token=? WHERE id=?', (encode(decode(bytes(row['token']))), row['id']))


def export_bundle(database, destination, password):
    store = BackupStore(database)
    if destination.suffix != '.finance-migration':
        raise MigrationError('Choose a filename ending in .finance-migration.')
    salt = os.urandom(16)
    key = password_key(password, salt)
    modes = [mode for mode in VERSIONS if store.target(mode).is_file()]
    if not modes:
        raise MigrationError('No saved workspaces to export.')
    database.parent.mkdir(parents=True, exist_ok=True)
    with ExitStack() as locks:
        locks.enter_context(database_lock(store.guard))
        for mode in VERSIONS:
            locks.enter_context(database_lock(store.target(mode)))
        with TemporaryDirectory(dir=database.parent, prefix='.migration-') as temporary:
            folder = Path(temporary)
            memory = BytesIO()
            with zipfile.ZipFile(memory, 'w', zipfile.ZIP_STORED) as archive:
                archive.writestr('manifest.json', json.dumps({'version': 1, 'workspaces': modes}))
                total = 0
                for mode in modes:
                    copy = folder / (mode + '.sqlite')
                    snapshot(store.target(mode), copy)
                    info = check_database(copy)
                    if info['workspace'] != ('sandbox' if mode == 'sandbox_investments' else mode):
                        raise MigrationError('Workspace identity does not match its file.')
                    total += copy.stat().st_size
                    if total > MAX_SIZE - 1048576:
                        raise MigrationError('Migration exceeds the 256 MB limit.')
                    rewrap(copy, lambda value: protect(value, False), lambda value: seal(key, value, TOKEN_AAD))
                    archive.write(copy, mode + '.sqlite')
                    preference = store.preference(mode)
                    settings = valid_selection(json.loads(preference.read_text(encoding='utf-8'))) if preference.is_file() else {'excluded_account_ids': []}
                    archive.writestr(mode + '.json', json.dumps(settings))
            content = memory.getvalue()
            if len(content) > MAX_SIZE:
                raise MigrationError('Migration exceeds the 256 MB limit.')
            encrypted = HEADER + salt + seal(key, content, HEADER + salt)
            # Exclusive creation avoids replacing an existing export or unrelated file.
            fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                with os.fdopen(fd, 'wb') as output:
                    output.write(encrypted)
            except BaseException:
                destination.unlink(missing_ok=True)
                raise
    return modes


def import_bundle(database, source, password):
    store = BackupStore(database)
    if source.stat().st_size > MAX_SIZE + 128:
        raise MigrationError('Migration exceeds the 256 MB limit.')
    content = source.read_bytes()
    if not content.startswith(HEADER) or len(content) < len(HEADER) + 44:
        raise MigrationError('Unsupported or incomplete migration file.')
    salt = content[len(HEADER):len(HEADER) + 16]
    key = password_key(password, salt)
    try:
        raw = unseal(key, content[len(HEADER) + 16:], HEADER + salt)
    except InvalidTag:
        raise MigrationError('Incorrect passphrase or damaged migration file. Nothing was imported.') from None
    database.parent.mkdir(parents=True, exist_ok=True)
    with ExitStack() as locks:
        locks.enter_context(database_lock(store.guard))
        for mode in VERSIONS:
            locks.enter_context(database_lock(store.target(mode)))
        # Intentionally fresh-install only, never merge or overwrite financial data.
        if any(store.target(mode).exists() or store.preference(mode).exists() for mode in VERSIONS):
            raise MigrationError('Import requires a fresh installation. Use a separate clone before its first launch; existing data was not changed.')
        with TemporaryDirectory(dir=database.parent, prefix='.migration-') as temporary:
            folder = Path(temporary)
            staged = []
            with zipfile.ZipFile(BytesIO(raw)) as archive:
                entries = archive.infolist()
                if len(entries) > 11 or len({entry.filename for entry in entries}) != len(entries) or sum(entry.file_size for entry in entries) > MAX_SIZE or any(entry.flag_bits & 1 for entry in entries):
                    raise MigrationError('Invalid migration contents or size.')
                if archive.getinfo('manifest.json').file_size > 65536:
                    raise MigrationError('Invalid migration manifest.')
                manifest = json.loads(archive.read('manifest.json'))
                modes = manifest.get('workspaces') if isinstance(manifest, dict) else None
                if not isinstance(manifest, dict) or manifest.get('version') != 1 or not isinstance(modes, list) or not modes or any(not isinstance(mode, str) or mode not in VERSIONS for mode in modes) or len(set(modes)) != len(modes):
                    raise MigrationError('Unsupported migration manifest.')
                expected = {'manifest.json'} | {mode + suffix for mode in modes for suffix in ('.sqlite', '.json')}
                if {entry.filename for entry in entries} != expected:
                    raise MigrationError('Migration file list does not match its manifest.')
                for mode in modes:
                    work = folder / mode
                    work.mkdir(mode=0o700)
                    copy = work / 'database.sqlite'
                    copy.write_bytes(archive.read(mode + '.sqlite'))
                    copy.chmod(0o600)
                    info = check_database(copy)
                    if info['workspace'] != ('sandbox' if mode == 'sandbox_investments' else mode):
                        raise MigrationError('Migration database identity does not match.')
                    migrate(copy, mode)
                    validate_current_schema(copy, mode, work)
                    rewrap(copy, lambda value: unseal(key, value, TOKEN_AAD), lambda value: protect(value, True))
                    if archive.getinfo(mode + '.json').file_size > 65536:
                        raise MigrationError('Invalid chart settings size.')
                    settings = valid_selection(json.loads(archive.read(mode + '.json')))
                    staged.extend([(store.target(mode), copy.read_bytes()),
                                   (store.preference(mode), json.dumps(settings).encode())])
            created = []
            try:
                for path, value in staged:
                    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    created.append(path)
                    with os.fdopen(fd, 'wb') as output:
                        output.write(value)
            except BaseException:
                for path in reversed(created):
                    path.unlink(missing_ok=True)
                raise
    return modes


def main():
    parser = argparse.ArgumentParser(description='Move saved Finance data and authorizations offline. Stop the app on both computers first.')
    parser.add_argument('action', choices=['export', 'import'])
    parser.add_argument('file', type=Path, help='Password-encrypted .finance-migration file; import requires a fresh installation.')
    args = parser.parse_args()
    try:
        warnings.simplefilter('error', GetPassWarning)
        password = getpass('Migration passphrase (12+ characters): ')
        validate_password(password)
        if args.action == 'export' and getpass('Confirm passphrase: ') != password:
            raise MigrationError('Passphrases do not match. Run export again and enter the same passphrase both times.')
        action = export_bundle if args.action == 'export' else import_bundle
        modes = action(DATABASE, args.file, password)
    except MigrationError as error:
        parser.exit(1, 'Migration failed: ' + str(error) + '\n')
    except FileExistsError:
        parser.exit(1, 'Migration failed: the destination file already exists. Choose a new .finance-migration filename; the existing file was not replaced.\n')
    except FileNotFoundError:
        parser.exit(1, 'Migration failed: the source file or destination folder does not exist. Check the path and try again.\n')
    except PermissionError:
        parser.exit(1, 'Migration failed: file access was denied. Choose a writable destination and close programs locking the file.\n')
    except GetPassWarning:
        parser.exit(1, 'Migration failed: a hidden passphrase prompt is unavailable. Run this command directly in an interactive terminal.\n')
    except KeyboardInterrupt:
        parser.exit(130, '\nMigration cancelled.\n')
    except (ValueError, OSError, RuntimeError, sqlite3.Error, zipfile.BadZipFile, KeyError, TypeError, InvalidTag, Warning):
        # Never print exception contents: database/provider errors may contain private values.
        parser.exit(1, 'Migration failed. Check the passphrase, file, OS vault access and disk permissions. Import requires a fresh clone before first launch. The source computer is unchanged.\n')
    print(f'Migration {args.action} completed for {len(modes)} workspace(s). No bank requests were made.')


if __name__ == '__main__':
    main()
