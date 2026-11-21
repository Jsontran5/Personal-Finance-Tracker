"""One background maintenance pass per production backend launch, never per browser load."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory
import threading
from uuid import uuid4

from fastapi import HTTPException

from .db import database_lock
from .recovery import BackupStore, atomic_write


def automatic_sets(store):
    root = store.directory / 'automatic'
    result = []
    if not root.is_dir() or root.is_symlink():
        return result
    for folder in root.iterdir():
        if not re.fullmatch('[a-f0-9]{32}', folder.name) or folder.is_symlink() or not folder.is_dir():
            continue
        try:
            manifest = json.loads((folder / 'set.json').read_text(encoding='utf-8'))
            files = manifest['files']
            created = datetime.fromisoformat(manifest['created_at'])
            if created.tzinfo is None or len(files) != 2 or not files[0].startswith('personal-') or not files[1].startswith('live-'):
                continue
            for name in files:
                store.resolve(f'automatic/{folder.name}/{name}')
            result.append((created, folder, files))
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return sorted(result, key=lambda row: row[0], reverse=True)


def automatic_backup(database, *, clock=None, keep=14):
    """Publish the pair atomically; prune only our completed automatic sets."""
    stamp = clock or datetime.now(timezone.utc)
    store = BackupStore(database)
    root = store.directory / 'automatic'
    with database_lock(store.guard):
        sets = automatic_sets(store)
        if sets and stamp - sets[0][0] < timedelta(hours=24):
            return {'status': 'Skipped', 'message': 'An automatic backup succeeded within the last 24 hours.', 'created_at': sets[0][0].isoformat()}
        root.mkdir(parents=True, exist_ok=True)
        if root.is_symlink() or root.resolve().parent != store.directory.resolve():
            raise ValueError('Automatic backup folder must stay inside the project backup folder.')
        identifier = uuid4().hex
        with TemporaryDirectory(dir=root, prefix='.creating-') as temporary:
            staged = BackupStore(database)
            staged.directory = Path(temporary)
            pair = staged.create_pair()
            files = [pair[key]['id'] for key in ['manual', 'connected']]
            manifest = {'created_at': stamp.isoformat(), 'files': files}
            atomic_write(staged.directory / 'set.json', json.dumps(manifest).encode())
            staged.directory.rename(root / identifier)
        # An interrupted pair is never published; manual and recovery bundles are untouched.
        for _, folder, files in automatic_sets(store)[keep:]:
            for name in files:
                store.resolve(f'automatic/{folder.name}/{name}').unlink()
            (folder / 'set.json').unlink()
            try:
                folder.rmdir()
            except OSError:
                pass  # Never remove unrecognized files recursively.
        return {'status': 'Updated', 'message': 'Saved Manual and Connected backups. Latest 14 automatic sets retained.',
                'created_at': stamp.isoformat(), 'files': [f'automatic/{identifier}/{name}' for name in files]}


class StartupMaintenance:
    def __init__(self, database, operations, enabled):
        self.database, self.operations = database, operations
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.thread = None
        self.state = {'enabled': enabled, 'running': False, 'phase': 'Waiting' if enabled else 'Disabled',
                      'started_at': None, 'finished_at': None, 'results': [], 'version': 0}

    def status(self):
        with self.lock:
            return deepcopy(self.state)

    def update(self, **values):
        with self.lock:
            self.state.update(values)
            self.state['version'] += 1

    def result(self, institution, product, status, message):
        with self.lock:
            self.state['results'].append(dict(institution=institution, product=product, status=status, message=message))
            self.state['version'] += 1

    def start(self):
        if not self.state['enabled'] or self.thread is not None:
            return
        self.update(running=True, phase='Backing up', started_at=datetime.now(timezone.utc).isoformat())
        self.thread = threading.Thread(target=self.run, name='finance-startup', daemon=True)
        self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join()  # Finish the bounded in-flight request/transaction safely.

    def run(self):
        try:
            # Serialize separate local app instances without locking browsing for the whole run.
            with database_lock(self.database.with_suffix('.startup-run')):
                try:
                    backup = automatic_backup(self.database)
                    self.result('Your data', 'backup', backup['status'], backup['message'])
                except Exception:
                    self.result('Your data', 'backup', 'Failed', 'Automatic backup could not finish. Existing backups are retained; check Backup & restore.')
                self.update(phase='Syncing')
                try:
                    connections = self.operations['connections']()
                    for connection in connections:
                        products = ['transactions'] if connection['profile'] == 'banking' else []
                        if connection['investments_enabled']:
                            products.extend(['holdings', 'activity'])
                        products.append('balances')
                        for product in products:
                            if self.stop.is_set():
                                return
                            self.update(phase=connection['name'] + ' · ' + product)
                            if connection['needs_reauth']:
                                self.result(connection['name'], product, 'Skipped', 'Reconnect this institution first.')
                                continue
                            if product in {'holdings', 'activity'} and connection.get('investment_consent_pending'):
                                self.result(connection['name'], product, 'Skipped', 'Investment consent is pending; reconnect this institution first.')
                                continue
                            try:
                                outcome = self.operations['sync'](connection['id'], product)
                                self.result(connection['name'], product, 'Updated', f"{outcome['count']} records retrieved.")
                            except HTTPException as error:
                                self.result(connection['name'], product, 'Skipped' if error.status_code == 429 else 'Failed', str(error.detail))
                            except Exception:
                                self.result(connection['name'], product, 'Failed', 'Sync could not finish. Saved data is retained.')
                    if not connections:
                        self.result('Your accounts', 'sync', 'Skipped', 'No authorized bank connections yet. Manual accounts remain available.')
                except Exception:
                    self.result('Your accounts', 'sync', 'Failed', 'Could not load connections. Saved data is retained.')
                finally:
                    self.update(phase='Saving snapshot')
                    try:
                        snapshot = self.operations['snapshot']()
                        self.result('Your accounts', 'snapshot', 'Updated' if snapshot['id'] else 'Skipped', snapshot['message'])
                    except Exception:
                        self.result('Your accounts', 'snapshot', 'Failed', 'Could not save a startup snapshot. Existing history is retained.')
        except Exception:
            self.result('Your data', 'startup', 'Failed', 'Startup maintenance could not acquire its local lock or access saved data.')
        finally:
            self.update(running=False, phase='Interrupted' if self.stop.is_set() else 'Finished', finished_at=datetime.now(timezone.utc).isoformat())
