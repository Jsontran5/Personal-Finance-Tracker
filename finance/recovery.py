"""Project-local SQLite snapshots, validated previews, and reversible restores."""
from contextlib import closing, contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory, NamedTemporaryFile
from typing import Literal
from uuid import uuid4
import hashlib
import json
import re
import sqlite3
import time
import zipfile

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .db import connect, database_lock, initialize
from .scenarios import sandbox_path, chart_path

MAX_SIZE = 256 * 1024 * 1024
VERSIONS = {'personal': 4, 'sandbox': 7, 'sandbox_investments': 7, 'sandbox_combined': 8, 'live': 9}


def checksum(path):
    digest = hashlib.sha256()
    with path.open('rb') as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


@contextmanager
def readonly(path):
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro&immutable=1', uri=True)) as db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA trusted_schema=OFF')
        yield db


def snapshot(source, destination):
    with closing(sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True)) as original, closing(sqlite3.connect(destination)) as target:
        deadline = time.monotonic() + 30
        def progress(status, remaining, total):
            if status != sqlite3.SQLITE_DONE and time.monotonic() > deadline:
                raise TimeoutError('Database snapshot timed out. Close other database tools and retry.')
        original.backup(target, pages=256, progress=progress, sleep=.01)


def check_database(path):
    with readonly(path) as db:
        if [tuple(row) for row in db.execute('PRAGMA integrity_check')] != [('ok',)] or db.execute('PRAGMA foreign_key_check').fetchone():
            raise ValueError('Database integrity or foreign-key validation failed.')
        if db.execute("SELECT 1 FROM sqlite_schema WHERE type IN ('trigger','view')").fetchone():
            raise ValueError('Backups containing triggers or views are not supported.')
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        mode = 'live' if 'workspace_environment' in tables else 'sandbox_combined' if 'multi_connections' in tables else 'sandbox' if {'connection', 'link', 'accounts', 'transactions'} <= tables else 'personal' if {'settings', 'balance_snapshots', 'net_worth_snapshots'} <= tables else None
        if mode is None:
            raise ValueError('This is not a recognized Finance database.')
        version = db.execute('PRAGMA user_version').fetchone()[0]
        if version > VERSIONS[mode] or version < (0 if mode == 'sandbox' else 1):
            raise ValueError('Unsupported schema version. Open this backup with a compatible app version.')
        counts = {name: db.execute(f'SELECT COUNT(*) FROM {name}').fetchone()[0] for name in ['accounts', 'transactions']}
        if 'manual_debt_details' in tables:
            counts['debt_details'] = db.execute('SELECT COUNT(*) FROM manual_debt_details').fetchone()[0]
        if mode != 'personal':
            counts['active_transactions'] = db.execute('SELECT COUNT(*) FROM transactions WHERE removed=0').fetchone()[0]
            if 'investment_holdings' in tables:
                counts['holdings'] = db.execute('SELECT COUNT(*) FROM investment_holdings').fetchone()[0]
                counts['investment_activity'] = db.execute('SELECT COUNT(*) FROM investment_activity WHERE removed=0').fetchone()[0]
            if 'liability_details' in tables:
                counts['debt_details'] = db.execute('SELECT COUNT(*) FROM liability_details').fetchone()[0]
            if mode in {'sandbox_combined', 'live'}:
                counts['connections'] = db.execute('SELECT COUNT(*) FROM multi_connections WHERE item_id IS NOT NULL').fetchone()[0]
        return {'workspace': mode, 'schema_version': version, 'counts': counts}


def migrate(path, mode):
    if mode == 'live':
        from .live_setup import initialize_live
        with connect(path, write=True) as db:
            initialize_live(db)
    elif mode == 'sandbox_combined':
        from .multi_bank import initialize_combined
        with connect(path, write=True) as db:
            initialize_combined(db)
    elif mode == 'personal':
        initialize(path)
    else:
        from .sandbox_preview import initialize_preview
        with connect(path, write=True) as db:
            initialize_preview(db)


def validate_current_schema(path, mode, directory):
    """Compare all required table/column types with a newly initialized reference."""
    mode = 'sandbox' if mode == 'sandbox_investments' else mode
    reference = directory / 'reference.db'
    if mode in {'sandbox', 'sandbox_combined', 'live'}:
        from .plaid_sandbox import SCHEMA
        with connect(reference, write=True) as db:
            for statement in SCHEMA:
                db.execute(statement)
    migrate(reference, mode)
    with readonly(reference) as expected, readonly(path) as candidate:
        tables = {row[0] for row in expected.execute("SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        actual_tables = {row[0] for row in candidate.execute("SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        if actual_tables != tables:
            raise ValueError('Backup tables do not match the supported workspace schema.')
        for table in tables:
            required = {row['name']: row['type'].upper() for row in expected.execute(f'PRAGMA table_info({table})')}
            actual = {row['name']: row['type'].upper() for row in candidate.execute(f'PRAGMA table_info({table})')}
            if actual != required:
                raise ValueError('Backup columns do not match the supported workspace schema.')
        if mode != 'personal' and candidate.execute('SELECT 1 FROM transaction_details WHERE NOT json_valid(payload) LIMIT 1').fetchone():
            raise ValueError('Saved transaction metadata is not valid JSON.')
        if mode != 'personal':
            for table, columns in [('investment_holdings', ['payload', 'security']),
                                   ('investment_activity', ['payload', 'security']),
                                   ('investment_observations', ['accounts']), ('liability_details', ['payload', 'balances'])]:
                for column in columns:
                    if candidate.execute(f'SELECT 1 FROM {table} WHERE NOT json_valid({column}) LIMIT 1').fetchone():
                        raise ValueError('Saved provider metadata is not valid JSON.')
        if mode == 'live':
            environment = candidate.execute('SELECT environment FROM workspace_environment WHERE id=1').fetchone()
            if not environment or environment[0] != 'production':
                raise ValueError('Live backup environment marker is invalid.')
            from .live_setup import LiveSetup
            for row in candidate.execute('SELECT payload FROM live_setup'):
                LiveSetup.model_validate_json(row[0])
        if mode == 'personal':
            from .debt import ManualDebt
            for row in candidate.execute('SELECT payload FROM manual_debt_details'):
                ManualDebt.model_validate_json(row[0])
    # Exercise the application projections before touching live data.
    with connect(path) as db:
        if mode == 'personal':
            from .ledger import overview
            overview(db, date.today(), date.today().isoformat()[:7])
        else:
            from .sandbox_preview import preview_overview
            preview_overview(db, date.today().isoformat()[:7])
            from .investments import project_investments, project_activity
            project_investments(db)
            project_activity(db, None, None, None, '', None, 1)


def valid_selection(value):
    if not isinstance(value, dict) or set(value) != {'excluded_account_ids'}:
        raise ValueError('Invalid account-selection settings in backup.')
    ids = value['excluded_account_ids']
    if not isinstance(ids, list) or len(ids) > 1000 or any(type(item) is not int or item <= 0 for item in ids):
        raise ValueError('Invalid account-selection settings in backup.')
    return {'excluded_account_ids': sorted(set(ids))}


def atomic_write(path, content):
    with NamedTemporaryFile(dir=path.parent, delete=False) as file:
        temporary = Path(file.name)
        file.write(content)
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


class BackupStore:
    def __init__(self, database):
        self.database = database
        self.directory = database.parent.parent / 'backups'
        self.guard = database.parent / 'backup-management'

    def target(self, mode):
        return self.database if mode == 'personal' else self.database.with_suffix('.plaid-live.db') if mode == 'live' else sandbox_path(self.database, 'combined' if mode == 'sandbox_combined' else 'investments' if mode == 'sandbox_investments' else 'banking')

    def preference(self, mode):
        return chart_path(self.database, mode)

    def resolve(self, identifier):
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,179}', identifier) or not identifier.endswith(('.db', '.finance-backup.zip')):
            raise ValueError('Choose a backup from the project backup list.')
        path = (self.directory / identifier).resolve()
        if path.parent != self.directory.resolve() or not path.is_file() or path.stat().st_size > MAX_SIZE:
            raise ValueError('Backup is missing, outside the backup folder, or too large.')
        return path

    def list(self):
        if not self.directory.is_dir():
            return []
        records = []
        for path in self.directory.iterdir():
            if path.name.endswith(('.finance-backup.zip', '.db')) and path.is_file() and path.resolve().parent == self.directory.resolve():
                stat = path.stat()
                records.append({'id': path.name, 'size_bytes': stat.st_size,
                                'file_date': datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
                                'format': 'bundle' if path.name.endswith('.zip') else 'legacy_database'})
        return sorted(records, key=lambda row: row['file_date'], reverse=True)[:500]

    def create(self, mode, recovery=False):
        self.directory.mkdir(parents=True, exist_ok=True)
        source = self.target(mode)
        if not source.is_file():
            raise ValueError('Open this workspace first to initialize its database.')
        with database_lock(self.guard), database_lock(source), TemporaryDirectory(dir=self.directory, prefix='.building-') as temporary:
            folder = Path(temporary)
            copy = folder / 'database.sqlite'
            snapshot(source, copy)
            if copy.stat().st_size > MAX_SIZE - 131072:
                raise ValueError('Workspace exceeds the 256 MB backup size limit.')
            info = check_database(copy)
            if info['workspace'] != ('sandbox' if mode == 'sandbox_investments' else mode):
                raise ValueError('Source database workspace does not match.')
            validate_current_schema(copy, mode, folder)
            selection = self.preference(mode)
            settings = valid_selection(json.loads(selection.read_text(encoding='utf-8'))) if selection.is_file() else {'excluded_account_ids': []}
            settings_bytes = json.dumps(settings, sort_keys=True).encode()
            if len(settings_bytes) > 65536:
                raise ValueError('Account-selection settings exceed the supported backup size.')
            created = datetime.now(timezone.utc)
            manifest = {'format_version': 1, 'workspace': mode, 'created_at': created.isoformat(),
                        'schema_version': info['schema_version'], 'database_sha256': checksum(copy),
                        'settings_sha256': hashlib.sha256(settings_bytes).hexdigest(), 'recovery': recovery}
            identifier = ('recovery-' if recovery else '') + mode + '-' + created.strftime('%Y%m%dT%H%M%SZ') + '-' + uuid4().hex[:8] + '.finance-backup.zip'
            archive = folder / 'bundle.zip'
            with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as bundle:
                bundle.write(copy, 'database.sqlite')
                bundle.writestr('manifest.json', json.dumps(manifest))
                bundle.writestr('settings.json', settings_bytes)
            archive.replace(self.directory / identifier)
            return {'id': identifier, 'created_at': manifest['created_at'], **info, 'workspace': mode, 'recovery': recovery}

    @contextmanager
    def staged(self, identifier):
        source = self.resolve(identifier)
        with TemporaryDirectory(dir=self.database.parent, prefix='.restore-check-') as temporary:
            folder = Path(temporary)
            copy = folder / 'database.sqlite'
            manifest = None
            settings = None
            if source.name.endswith('.zip'):
                with zipfile.ZipFile(source) as bundle:
                    files = bundle.infolist()
                    if len(files) != 3 or {item.filename for item in files} != {'database.sqlite', 'manifest.json', 'settings.json'} or any(item.flag_bits & 1 for item in files):
                        raise ValueError('Backup bundle contents are not supported.')
                    if sum(item.file_size for item in files) > MAX_SIZE or bundle.getinfo('manifest.json').file_size > 65536 or bundle.getinfo('settings.json').file_size > 65536:
                        raise ValueError('Backup expands beyond the supported size.')
                    manifest = json.loads(bundle.read('manifest.json'))
                    if not isinstance(manifest, dict) or manifest.get('format_version') != 1:
                        raise ValueError('Unsupported backup format.')
                    copy.write_bytes(bundle.read('database.sqlite'))
                    settings_bytes = bundle.read('settings.json')
                    if checksum(copy) != manifest.get('database_sha256') or hashlib.sha256(settings_bytes).hexdigest() != manifest.get('settings_sha256'):
                        raise ValueError('Backup checksum failed. The bundle may be damaged.')
                    settings = valid_selection(json.loads(settings_bytes))
            else:
                # Legacy databases are read as immutable files, never opened as writable backups.
                copy.write_bytes(source.read_bytes())
            info = check_database(copy)
            mode = info['workspace']
            declared = manifest.get('workspace') if manifest else mode
            if manifest and (declared not in VERSIONS or ('sandbox' if declared == 'sandbox_investments' else declared) != mode or manifest.get('schema_version') != info['schema_version']):
                raise ValueError('Backup manifest does not match its database.')
            mode = declared
            info['workspace'] = mode
            migrate(copy, mode)
            validate_current_schema(copy, mode, folder)
            check_database(copy)
            yield source, copy, info, manifest, settings

    def bank_policy(self, candidate, target):
        with readonly(candidate) as check:
            multi = check.execute("SELECT 1 FROM sqlite_schema WHERE name='multi_connections'").fetchone()
        if multi:
            with readonly(candidate) as db:
                saved = {row['item_id']: dict(row) for row in db.execute('SELECT * FROM multi_connections WHERE item_id IS NOT NULL')}
            current = {}
            if target.is_file():
                with readonly(target) as db:
                    if db.execute('SELECT 1 FROM multi_sessions LIMIT 1').fetchone():
                        raise ValueError('Finish or cancel all pending Combined Link sessions before restoring.')
                    current = {row['item_id']: dict(row) for row in db.execute('SELECT * FROM multi_connections WHERE item_id IS NOT NULL')}
            if current and set(current) != set(saved):
                raise ValueError('Combined backup has a different set of Items. Choose a backup matching the current connections; no authorization was changed.')
            from .plaid_vault import protect
            if not current:
                for row in saved.values():
                    try:
                        protect(row['token'], False)
                    except Exception:
                        raise ValueError('Combined saved authorization cannot be unlocked by this Windows user.') from None
            return ('keep_multiple_authorizations' if current else 'restore_multiple_authorizations'), current
        with readonly(candidate) as db:
            saved = db.execute('SELECT * FROM connection WHERE id=1').fetchone()
            saved = dict(saved) if saved else None
        current = None
        if target.is_file():
            with readonly(target) as db:
                if db.execute('SELECT 1 FROM link LIMIT 1').fetchone():
                    raise ValueError('Finish or cancel the current bank authorization in Bank sync before restoring.')
                row = db.execute('SELECT * FROM connection WHERE id=1').fetchone()
                current = dict(row) if row else None
        if current and not saved:
            with readonly(candidate) as db:
                empty = not db.execute('SELECT 1 FROM accounts LIMIT 1').fetchone() and not db.execute('SELECT 1 FROM transactions LIMIT 1').fetchone()
            if empty:
                return 'keep_authorization_reset_feed', current
        if current and (not saved or current['item_id'] != saved['item_id']):
            raise ValueError('This backup belongs to a different or unconnected Sandbox Item. Choose a backup of the currently connected Item.')
        if current:
            return 'keep_current_authorization', current
        if saved:
            from .plaid_vault import protect
            try:
                protect(saved['token'], False)
            except Exception:
                raise ValueError('This saved bank authorization cannot be unlocked by this Windows user. Restore it on the original Windows profile.') from None
            return 'restore_saved_authorization', None
        return 'no_saved_connection', None

    def state_hash(self, mode):
        target = self.target(mode)
        selection = self.preference(mode)
        parts = [checksum(target) if target.is_file() else None, selection.read_bytes().hex() if selection.is_file() else None]
        wal = target.with_name(target.name + '-wal')
        parts.append(checksum(wal) if wal.is_file() else None)
        return hashlib.sha256(json.dumps(parts).encode()).hexdigest()

    def token(self, source, mode):
        return hashlib.sha256((checksum(source) + mode + self.state_hash(mode)).encode()).hexdigest()

    def preview(self, identifier):
        with database_lock(self.guard), self.staged(identifier) as (source, copy, info, manifest, settings):
            mode = info['workspace']
            with database_lock(self.target(mode)):
                policy, _ = self.bank_policy(copy, self.target(mode)) if mode != 'personal' else ('not_applicable', None)
                current = check_database(self.target(mode)) if self.target(mode).is_file() else None
                return {'id': identifier, **info, 'restored_schema_version': VERSIONS[mode],
                        'created_at': manifest.get('created_at') if manifest else None,
                        'file_date': datetime.fromtimestamp(source.stat().st_mtime, timezone.utc).isoformat(),
                        'legacy': manifest is None, 'restores_chart_selection': settings is not None,
                        'current_counts': current['counts'] if current else None, 'bank_policy': policy,
                        'preview_token': self.token(source, mode), 'confirmation': 'RESTORE ' + mode.upper(),
                        'contents': self.contents(copy)}

    def contents(self, path):
        with readonly(path) as db:
            return {'accounts': [dict(row) for row in db.execute('SELECT name,currency FROM accounts ORDER BY rowid LIMIT 8')],
                    'transaction_dates': dict(db.execute('SELECT MIN(date) AS first,MAX(date) AS last FROM transactions').fetchone())}

    def restore(self, identifier, preview_token, confirmation):
        with database_lock(self.guard), self.staged(identifier) as (source, copy, info, manifest, settings):
            mode = info['workspace']
            target = self.target(mode)
            with database_lock(target):
                if confirmation != 'RESTORE ' + mode.upper():
                    raise ValueError('Type the restore confirmation exactly.')
                if preview_token != self.token(source, mode):
                    raise ValueError('Backup or current workspace changed. Preview the restore again.')
                if not target.is_file():
                    if mode != 'personal':
                        from .plaid_sandbox import SCHEMA
                        with connect(target, write=True) as db:
                            for statement in SCHEMA:
                                db.execute(statement)
                    migrate(target, mode)
                if mode != 'personal':
                    policy, current = self.bank_policy(copy, target)
                    if mode in {'sandbox_combined', 'live'}:
                        with connect(copy, write=True) as db:
                            for item_id, row in current.items():
                                db.execute('UPDATE multi_connections SET token=?,needs_reauth=?,balance_checked_at=? WHERE item_id=?', (row['token'], row['needs_reauth'], row['balance_checked_at'], item_id))
                            db.execute('DELETE FROM multi_sessions')
                    elif current:
                        with connect(copy, write=True) as db:
                            if db.execute('SELECT 1 FROM connection WHERE id=1').fetchone():
                                db.execute('UPDATE connection SET item_id=?,token=?,needs_reauth=?,balance_checked_at=? WHERE id=1',
                                           (current['item_id'], current['token'], current['needs_reauth'], current['balance_checked_at']))
                            else:
                                db.execute('INSERT INTO connection(id,item_id,token,needs_reauth,balance_checked_at) VALUES (1,?,?,?,?)',
                                           (current['item_id'], current['token'], current['needs_reauth'], current['balance_checked_at']))
                    with connect(copy, write=True) as db:
                        db.execute('DELETE FROM link')
                recovery = self.create(mode, recovery=True)
                preference = self.preference(mode)
                old_settings = preference.read_bytes() if preference.is_file() else None
                try:
                    # SQLite backup commits destination pages atomically and cooperates with existing readers.
                    snapshot(copy, target)
                    if settings is not None:
                        atomic_write(preference, json.dumps(settings).encode())
                except Exception:
                    try:
                        with self.staged(recovery['id']) as (_, rollback, _, _, _):
                            snapshot(rollback, target)
                        if old_settings is None:
                            preference.unlink(missing_ok=True)
                        elif not preference.is_file() or preference.read_bytes() != old_settings:
                            atomic_write(preference, old_settings)
                    except Exception:
                        raise RuntimeError('Restore failed and automatic recovery could not finish. Recovery backup: ' + recovery['id']) from None
                    raise RuntimeError('Restore failed; the original workspace was recovered. Recovery backup: ' + recovery['id']) from None
                return {'restored': identifier, 'workspace': mode, 'recovery_backup': recovery['id']}


class CreateBackup(BaseModel):
    workspace: Literal['personal', 'sandbox', 'sandbox_investments', 'sandbox_combined', 'live']


class RestoreBackup(BaseModel):
    id: str = Field(max_length=180)
    preview_token: str = Field(pattern=r'^[a-f0-9]{64}$')
    confirmation: str = Field(max_length=40)


def backup_router(database):
    store = BackupStore(database)
    router = APIRouter(prefix='/api/backups')

    def guarded(work):
        try:
            return work()
        except (ValueError, sqlite3.DatabaseError, zipfile.BadZipFile, KeyError, UnicodeError, TypeError, OverflowError) as error:
            if isinstance(error, sqlite3.DatabaseError) and getattr(error, 'sqlite_errorcode', None) in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
                raise HTTPException(409, 'The workspace is busy. Wait for other operations to finish and try again.') from None
            detail = str(error) if isinstance(error, ValueError) else 'Backup validation failed. The file may be damaged or incompatible.'
            raise HTTPException(422, detail) from None
        except (OSError, RuntimeError) as error:
            raise HTTPException(409, str(error) if isinstance(error, RuntimeError) else 'Backup files could not be accessed. Check permissions and try again.') from None

    @router.get('')
    def list_backups():
        return {'backups': guarded(store.list)}

    @router.post('', status_code=201)
    def create(item: CreateBackup):
        return guarded(lambda: store.create(item.workspace))

    @router.get('/preview')
    def preview(id: str):
        return guarded(lambda: store.preview(id))

    @router.post('/restore')
    def restore(item: RestoreBackup):
        return guarded(lambda: store.restore(item.id, item.preview_token, item.confirmation))

    return router
