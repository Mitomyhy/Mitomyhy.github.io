from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import installer
from test_backend import make_payload, XML


OPEN_MESSAGE = "Save your work and close these applications: Photoshop.exe (PID 38696). Chrome may remain open. No process was stopped."


class InstallerWaitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="installer-wait-",
            dir=Path(__file__).resolve().parents[2] / "build")
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_only_controlled_original_process_guard_is_typed(self):
        with mock.patch.object(installer, "_original_closed", side_effect=installer.InstallError(OPEN_MESSAGE)):
            with self.assertRaises(installer.ApplicationsOpen) as caught:
                installer._closed(False)
        self.assertTrue(installer.is_applications_open(caught.exception))
        for message in ("Cannot check running applications. No process was stopped.",
                        "Cannot check Photoshop in the current Windows session.", "File integrity changed"):
            with self.subTest(message=message), \
                    mock.patch.object(installer, "_original_closed", side_effect=installer.InstallError(message)):
                with self.assertRaises(installer.InstallError) as caught:
                    installer._closed(False)
                self.assertNotIsInstance(caught.exception, installer.ApplicationsOpen)
                self.assertFalse(installer.is_applications_open(caught.exception))

    def test_wait_classifier_rejects_incomplete_rollback_arbitrary_wrappers_and_context(self):
        guard = installer.ApplicationsOpen(OPEN_MESSAGE)
        restored = installer.InstallError("Installation stopped. Previous files restored. Backup: fixture. " + OPEN_MESSAGE)
        restored.__cause__ = guard
        self.assertTrue(installer.is_applications_open(restored))
        for message in ("Installation stopped. Recovery retained later edits: panel/index.html. Backup: fixture.",
                        "Unrelated write error", "Installation stopped. Previous files restored."):
            wrapped = installer.InstallError(message)
            wrapped.__cause__ = guard
            self.assertFalse(installer.is_applications_open(wrapped))
        context = installer.InstallError("Unrelated write error")
        context.__context__ = guard
        self.assertFalse(installer.is_applications_open(context))

    def test_process_start_at_receipt_is_waitable_only_after_real_complete_rollback(self):
        source = self.root / "source"
        make_payload(source)
        machine = self.root / "machine"
        roots, _, _ = installer._roots(machine)
        (roots["panel"] / "CSXS").mkdir(parents=True)
        (roots["panel"] / "CSXS/manifest.xml").write_bytes(XML)
        body = roots["panel"] / "index.html"
        body.write_bytes(b"old panel")
        private = roots["panel"] / "private.json"
        private.write_bytes(b"private settings")
        calls, observed_new_body = 0, []
        def closed(_sandbox):
            nonlocal calls
            calls += 1
            if calls == 2:
                observed_new_body.append(body.read_bytes())
                raise installer.InstallError(OPEN_MESSAGE)
        with mock.patch.object(installer, "_original_closed", side_effect=closed), \
                mock.patch.object(installer.time, "monotonic", return_value=10.0):
            with self.assertRaises(installer.InstallError) as caught:
                installer.install(source, base_root=machine)
        self.assertEqual(observed_new_body, [b"new panel"])
        self.assertTrue(installer.is_applications_open(caught.exception))
        self.assertEqual(body.read_bytes(), b"old panel")
        self.assertEqual(private.read_bytes(), b"private settings")
        self.assertFalse((roots["launcher"] / "pending-transaction.json").exists())
        self.assertFalse((roots["launcher"] / "install-receipt.json").exists())
        self.assertFalse(hasattr(installer._install_poll, "state"))

    def test_persistently_open_process_during_copy_keeps_recovery_error_visible(self):
        source = self.root / "source"
        make_payload(source)
        machine = self.root / "machine"
        calls = 0
        def closed(_sandbox):
            nonlocal calls
            calls += 1
            if calls >= 2:
                raise installer.InstallError(OPEN_MESSAGE)
        with mock.patch.object(installer, "_original_closed", side_effect=closed), \
                mock.patch.object(installer.time, "monotonic", return_value=10.0):
            with self.assertRaises(installer.InstallError) as caught:
                installer.install(source, base_root=machine)
        self.assertIn("Recovery retained", str(caught.exception))
        self.assertFalse(installer.is_applications_open(caught.exception))
        roots, _, _ = installer._roots(machine)
        self.assertTrue((roots["launcher"] / "pending-transaction.json").exists())
        self.assertFalse(hasattr(installer._install_poll, "state"))


if __name__ == "__main__":
    unittest.main()
