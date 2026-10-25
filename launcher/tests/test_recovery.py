from pathlib import Path
import json
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import installer
from test_backend import make_payload, XML


class SimulatedInterruption(BaseException):
    pass


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parents[2] / 'build', prefix='recovery-test-')
        self.root = Path(self.temp.name)
        self.machine = self.root / 'machine'
        self.source = self.root / 'source'
        make_payload(self.source)
        self.roots, _, _ = installer._roots(self.machine)

    def tearDown(self):
        self.temp.cleanup()

    def interrupt(self):
        atomic = installer._atomic
        target = self.roots['panel'] / 'index.html'
        def write_then_interrupt(file, data, expected):
            value = atomic(file, data, expected)
            if Path(file) == target:
                raise SimulatedInterruption('Power interruption after a destination write')
            return value
        with mock.patch.object(installer, '_atomic', side_effect=write_then_interrupt):
            with self.assertRaises(SimulatedInterruption):
                installer.install(self.source, base_root=self.machine)
        self.assertTrue((self.roots['launcher'] / 'pending-transaction.json').is_file())
        self.assertEqual(target.read_bytes(), b'new panel')

    def test_absent_pending_never_rolls_back_latest_installed_backup(self):
        installer.install(self.source, base_root=self.machine)
        self.assertIsNotNone(installer.get_recovery_backup(self.machine))
        with mock.patch.object(installer, 'rollback', side_effect=AssertionError('must not rollback an installed release')):
            self.assertIsNone(installer.recover_pending(self.machine))

    def test_interrupted_fresh_install_recovers_then_retries_with_empty_directories(self):
        self.interrupt()
        backup = installer.get_recovery_backup(self.machine)
        result = installer.recover_pending(self.machine)
        self.assertEqual(result['status'], 'rolled_back')
        self.assertFalse((self.roots['launcher'] / 'pending-transaction.json').exists())
        self.assertTrue((backup / 'transaction.json').is_file())
        installed = installer.install(self.source, base_root=self.machine)
        self.assertEqual(installed['status'], 'installed')
        self.assertEqual((self.roots['panel'] / 'index.html').read_bytes(), b'new panel')

    def test_install_entry_automatically_recovers_owned_interruption_and_preserves_private_files(self):
        panel = self.roots['panel']
        (panel / 'CSXS').mkdir(parents=True)
        (panel / 'CSXS/manifest.xml').write_bytes(XML)
        (panel / 'index.html').write_bytes(b'old panel')
        private = panel / 'local-config.json'
        private.write_bytes(b'private settings')
        self.interrupt()
        result = installer.install(self.source, base_root=self.machine)
        self.assertEqual(result['status'], 'installed')
        self.assertEqual(private.read_bytes(), b'private settings')
        self.assertEqual((panel / 'index.html').read_bytes(), b'new panel')

    def test_install_entry_recovers_interrupted_first_install_without_manual_rollback(self):
        self.interrupt()
        result = installer.install(self.source, base_root=self.machine)
        self.assertEqual(result['status'], 'installed')
        self.assertFalse((self.roots['launcher'] / 'pending-transaction.json').exists())
        self.assertEqual((self.roots['panel'] / 'index.html').read_bytes(), b'new panel')

    def test_changed_backup_blocks_recovery_before_any_owned_file_changes(self):
        panel = self.roots['panel']
        (panel / 'CSXS').mkdir(parents=True)
        (panel / 'CSXS/manifest.xml').write_bytes(XML)
        (panel / 'index.html').write_bytes(b'old panel')
        self.interrupt()
        backup = installer.get_recovery_backup(self.machine)
        journal = json.loads((backup / 'transaction.json').read_text())
        row = next(row for row in journal['files'] if row['backup'] is not None)
        (backup / row['backup']).write_bytes(b'changed backup')
        with self.assertRaisesRegex(installer.InstallError, 'integrity'):
            installer.recover_pending(self.machine)
        self.assertEqual((panel / 'index.html').read_bytes(), b'new panel')
        self.assertTrue((self.roots['launcher'] / 'pending-transaction.json').exists())

    def test_later_edits_are_preserved_and_retry_is_blocked(self):
        self.interrupt()
        edited = self.roots['panel'] / 'index.html'
        edited.write_bytes(b'later user edit')
        with self.assertRaisesRegex(installer.InstallError, 'later edits'):
            installer.recover_pending(self.machine)
        self.assertEqual(edited.read_bytes(), b'later user edit')
        self.assertTrue((self.roots['launcher'] / 'pending-transaction.json').exists())

    def test_invalid_pending_identity_is_never_deleted_or_rolled_back(self):
        pending = self.roots['launcher'] / 'pending-transaction.json'
        pending.parent.mkdir(parents=True)
        raw = b'{"schema":1,"identity":"other user","backup":"backups/nope"}'
        pending.write_bytes(raw)
        with mock.patch.object(installer, 'rollback', side_effect=AssertionError('invalid pending')):
            with self.assertRaises(installer.InstallError):
                installer.recover_pending(self.machine)
        self.assertEqual(pending.read_bytes(), raw)

    def test_empty_parent_cleanup_never_removes_unowned_user_files(self):
        self.interrupt()
        private = self.roots['panel'] / 'private-note.txt'
        private.write_bytes(b'user file created after interruption')
        result = installer.recover_pending(self.machine)
        self.assertEqual(result['status'], 'rolled_back')
        self.assertEqual(private.read_bytes(), b'user file created after interruption')
        self.assertTrue(self.roots['panel'].is_dir())

    def test_pending_disappearing_cannot_trigger_installed_backup_fallback(self):
        self.interrupt()
        pending = self.roots['launcher'] / 'pending-transaction.json'
        backup = installer.get_recovery_backup(self.machine)
        def vanished(*args, **kwargs):
            pending.unlink()
            return backup
        with mock.patch.object(installer, 'get_recovery_backup', side_effect=vanished), \
                mock.patch.object(installer, 'rollback', side_effect=AssertionError('fallback must not execute')):
            with self.assertRaisesRegex(installer.InstallError, 'changed'):
                installer.recover_pending(self.machine)


if __name__ == '__main__':
    unittest.main()
