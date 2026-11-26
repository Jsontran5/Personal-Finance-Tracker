"""Synthetic-only OS vault and offline migration regression checks."""
from io import BytesIO, StringIO
from types import SimpleNamespace
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
import zipfile

from finance.db import connect, initialize
from finance import migration, plaid_vault
from finance.recovery import BackupStore
from finance.plaid_sandbox import SCHEMA
from finance.multi_bank import initialize_combined
from finance.live_setup import initialize_live


class MigrationCLITests(TestCase):
    def run_cli(self, passwords, failure=None):
        with patch('sys.argv', ['finance.migration', 'export', 'fixture.finance-migration']), patch.object(migration, 'getpass', side_effect=passwords), patch.object(migration, 'export_bundle', side_effect=failure) as export, patch('sys.stderr', new_callable=StringIO) as stderr:
            with self.assertRaises(SystemExit) as stopped:
                migration.main()
            self.assertEqual(stopped.exception.code, 1)
            return stderr.getvalue(), export.called

    def test_short_or_mismatched_passphrase_has_actionable_error_without_echo(self):
        message, called = self.run_cli(['short'])
        self.assertIn('between 12 and 1024 characters', message)
        self.assertNotIn('short', message)
        self.assertFalse(called)
        message, called = self.run_cli(['synthetic first passphrase', 'synthetic other passphrase'])
        self.assertIn('Passphrases do not match', message)
        self.assertNotIn('synthetic', message)
        self.assertFalse(called)

    def test_expected_file_errors_are_specific_and_untrusted_errors_stay_hidden(self):
        for failure, expected in [(FileExistsError('private filename'), 'already exists'),
                                  (PermissionError('private filename'), 'access was denied'),
                                  (FileNotFoundError('private filename'), 'does not exist'),
                                  (migration.MigrationError('Migration exceeds the 256 MB limit.'), '256 MB'),
                                  (ValueError('sensitive provider value'), 'Check the passphrase')]:
            with self.subTest(error=type(failure).__name__):
                message, called = self.run_cli(['synthetic passphrase', 'synthetic passphrase'], failure)
                self.assertTrue(called)
                self.assertIn(expected, message)
                self.assertNotIn('private filename', message)
                self.assertNotIn('sensitive provider value', message)


class VaultTests(TestCase):
    def test_keychain_creation_missing_key_and_denial_fail_closed(self):
        with TemporaryDirectory() as directory:
            values = {}
            backend = SimpleNamespace(get_password=lambda *args: values.get(args),
                                      set_password=lambda service, account, value: values.update({(service, account): value}))
            with patch.dict('sys.modules', {'keyring.backends.macOS': SimpleNamespace(Keyring=lambda: backend)}), patch('finance.db.ROOT', Path(directory)):
                with self.assertRaises(RuntimeError):
                    plaid_vault.mac_key()
                self.assertEqual(values, {})
                first = plaid_vault.mac_key(create=True)
                self.assertEqual(len(first), 32)
                self.assertEqual(plaid_vault.mac_key(create=True), first)
                self.assertEqual(len(values), 1)
                old_id = plaid_vault.mac_key_id()
                with patch('finance.db.ROOT', Path(directory) / 'different-clone'):
                    second = plaid_vault.mac_key(create=True)
                    self.assertNotEqual(first, second)
                    self.assertEqual(plaid_vault.mac_key(key_id=old_id), first)
                self.assertEqual(len(values), 2)
                backend.get_password = lambda *args: 'invalid base64'
                with self.assertRaises(RuntimeError):
                    plaid_vault.mac_key()
                def denied(*args):
                    raise OSError('synthetic denial')
                backend.get_password = denied
                with self.assertRaises(RuntimeError):
                    plaid_vault.mac_key(create=True)
    def test_mac_authenticated_roundtrip_and_wrong_platform(self):
        with patch.object(plaid_vault.sys, 'platform', 'darwin'), patch.object(plaid_vault, 'mac_key', return_value=b'x' * 32):
            encrypted = plaid_vault.protect(b'synthetic-only\x00\xff', True)
            self.assertNotIn(b'synthetic-only', encrypted)
            self.assertEqual(plaid_vault.protect(encrypted, False), b'synthetic-only\x00\xff')
            self.assertNotEqual(encrypted, plaid_vault.protect(b'synthetic-only\x00\xff', True))
            with self.assertRaises(RuntimeError):
                plaid_vault.protect(encrypted[:-1] + bytes([encrypted[-1] ^ 1]), False)
            with self.assertRaises(RuntimeError):
                plaid_vault.protect(b'foreign-dpapi-data', False)
            with patch.object(plaid_vault, 'mac_key', return_value=b'y' * 32), self.assertRaises(RuntimeError):
                plaid_vault.protect(encrypted, False)
        with patch.object(plaid_vault.sys, 'platform', 'win32'), self.assertRaises(RuntimeError):
            plaid_vault.protect(encrypted, False)
        with patch.object(plaid_vault.sys, 'platform', 'linux'), self.assertRaises(RuntimeError):
            plaid_vault.protect(b'synthetic', True)

    def test_native_windows_legacy_roundtrip(self):
        if os.name != 'nt':
            self.skipTest('Native Windows DPAPI check')
        value = plaid_vault.windows_protect(b'synthetic-dpapi', True)
        self.assertEqual(plaid_vault.protect(value, False), b'synthetic-dpapi')


class MigrationTests(TestCase):
    password = 'synthetic migration passphrase only'

    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / 'original/data/finance.db'
        self.target = self.root / 'destination/data/finance.db'
        self.bundle = self.root / 'test.finance-migration'
        initialize(self.source)
        self.store = BackupStore(self.source)

    def seed(self, mode, token):
        path = self.store.target(mode)
        with connect(path, write=True) as db:
            for statement in SCHEMA:
                db.execute(statement)
            if mode == 'live':
                initialize_live(db)
            elif mode == 'sandbox_combined':
                initialize_combined(db)
            if mode in {'live', 'sandbox_combined'}:
                db.execute("INSERT INTO multi_connections(id,institution_id,name,profile,item_id,token,cursor) VALUES (1,'fixture-bank','Fixture Bank','banking','fixture-item',?,'fixture-cursor')", (plaid_vault.protect(token, True),))
            else:
                db.execute("INSERT INTO connection(id,item_id,token,cursor) VALUES (1,'fixture-item',?,'fixture-cursor')", (plaid_vault.protect(token, True),))
        self.store.preference(mode).write_text(json.dumps({'excluded_account_ids': [2, 3]}))

    def test_windows_export_to_simulated_mac_preserves_every_workspace_and_cursor(self):
        # Two distinct bank personas: single legacy feed and multiple live/sandbox feeds.
        modes = ('sandbox', 'sandbox_investments', 'sandbox_combined', 'live')
        with patch.object(plaid_vault, 'mac_key', return_value=b'a' * 32):
            for mode in modes:
                self.seed(mode, ('synthetic-' + mode).encode())
            before = {mode: self.store.target(mode).read_bytes() for mode in modes}
            migration.export_bundle(self.source, self.bundle, self.password)
            self.assertNotIn(b'synthetic-', self.bundle.read_bytes())
            self.assertFalse(zipfile.is_zipfile(self.bundle))
            for mode in modes:
                self.assertEqual(before[mode], self.store.target(mode).read_bytes())
        with patch.object(plaid_vault.sys, 'platform', 'darwin'), patch.object(plaid_vault, 'mac_key', return_value=b'b' * 32):
            restored = migration.import_bundle(self.target, self.bundle, self.password)
            self.assertEqual(set(restored), set(modes) | {'personal'})
            target_store = BackupStore(self.target)
            for mode in modes:
                table = 'multi_connections' if mode in {'live', 'sandbox_combined'} else 'connection'
                with connect(target_store.target(mode)) as db:
                    row = db.execute(f'SELECT item_id,token,cursor FROM {table}').fetchone()
                    self.assertEqual(row['item_id'], 'fixture-item')
                    self.assertEqual(row['cursor'], 'fixture-cursor')
                    self.assertEqual(plaid_vault.protect(row['token'], False), ('synthetic-' + mode).encode())
                self.assertEqual(json.loads(target_store.preference(mode).read_text()), {'excluded_account_ids': [2, 3]})
            with self.assertRaisesRegex(ValueError, 'fresh installation'):
                migration.import_bundle(self.target, self.bundle, self.password)

    def test_password_damage_and_existing_destination_never_overwrite(self):
        migration.export_bundle(self.source, self.bundle, self.password)
        with self.assertRaisesRegex(ValueError, 'Incorrect passphrase'):
            migration.import_bundle(self.target, self.bundle, 'wrong password long enough')
        self.assertFalse(self.target.exists())
        original = self.bundle.read_bytes()
        self.bundle.write_bytes(original[:-1] + bytes([original[-1] ^ 1]))
        with self.assertRaises(ValueError):
            migration.import_bundle(self.target, self.bundle, self.password)
        self.assertFalse(self.target.exists())
        self.bundle.write_bytes(original)
        initialize(self.target)
        before = self.target.read_bytes()
        with self.assertRaises(ValueError):
            migration.import_bundle(self.target, self.bundle, self.password)
        self.assertEqual(before, self.target.read_bytes())
        with self.assertRaises(FileExistsError):
            migration.export_bundle(self.source, self.bundle, self.password)

    def test_simulated_mac_export_to_native_windows(self):
        if os.name != 'nt':
            self.skipTest('Native Windows destination check')
        with patch.object(plaid_vault.sys, 'platform', 'darwin'), patch.object(plaid_vault, 'mac_key', return_value=b'z' * 32):
            self.seed('live', b'synthetic-return-to-windows')
            migration.export_bundle(self.source, self.bundle, self.password)
        migration.import_bundle(self.target, self.bundle, self.password)
        with connect(BackupStore(self.target).target('live')) as db:
            row = db.execute('SELECT item_id,cursor,token FROM multi_connections').fetchone()
            self.assertEqual((row['item_id'], row['cursor']), ('fixture-item', 'fixture-cursor'))
            self.assertEqual(plaid_vault.protect(row['token'], False), b'synthetic-return-to-windows')

    def test_import_rejects_extra_paths_and_rolls_back_write_failure(self):
        migration.export_bundle(self.source, self.bundle, self.password)
        original = self.bundle.read_bytes()
        salt = original[len(migration.HEADER):len(migration.HEADER) + 16]
        key = migration.password_key(self.password, salt)
        raw = migration.unseal(key, original[len(migration.HEADER) + 16:], migration.HEADER + salt)
        memory = BytesIO(raw)
        with zipfile.ZipFile(memory, 'a') as archive:
            archive.writestr('../escape', b'no')
        self.bundle.write_bytes(migration.HEADER + salt + migration.seal(key, memory.getvalue(), migration.HEADER + salt))
        with self.assertRaises(ValueError):
            migration.import_bundle(self.target, self.bundle, self.password)
        self.assertFalse(self.target.exists())
        self.bundle.write_bytes(original)
        native_open = os.open
        def failing_open(path, flags, *args, **kwargs):
            if Path(path) == BackupStore(self.target).preference('personal'):
                raise OSError('synthetic write failure')
            return native_open(path, flags, *args, **kwargs)
        with patch.object(migration.os, 'open', side_effect=failing_open), self.assertRaises(OSError):
            migration.import_bundle(self.target, self.bundle, self.password)
        self.assertFalse(self.target.exists())

    def test_short_password_and_no_saved_data_rejected(self):
        with self.assertRaises(ValueError):
            migration.export_bundle(self.source, self.bundle, 'short')
        with self.assertRaises(ValueError):
            migration.export_bundle(self.target, self.bundle, self.password)
