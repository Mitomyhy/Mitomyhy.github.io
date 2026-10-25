from pathlib import Path
import hashlib
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import backend
import self_update


class SelfUpdateTests(unittest.TestCase):
    def setUp(self):
        workspace_build = Path(__file__).resolve().parents[2] / "build"
        workspace_build.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="self-update-test-", dir=workspace_build)
        self.root = Path(self.temporary.name)
        self.data = self.root / "Launcher"
        self.old = self.root / "old-launcher.exe"
        self.old.write_bytes(b"MZ-old-running-launcher")
        self.panel = self.root / "PhotoshopPanel" / "private.json"
        self.panel.parent.mkdir()
        self.panel.write_bytes(b"private-panel-state-unchanged")
        self.content = b"MZ" + bytes(range(256)) * 6
        self.artifact = {"version": "2026.10.24.6",
                         "url": "https://mitomyhy.github.io/releases/mitom-launcher/2026.10.24.6/setup.exe",
                         "size": len(self.content), "sha256": hashlib.sha256(self.content).hexdigest()}
        self.release = {"schema": 1, "version": "2026.10.23.2", "launcher": self.artifact}
        self.version_patch = mock.patch.object(backend, "APP_VERSION", "2026.10.24.5")
        self.version_patch.start()
        self.update = self_update.select_update(backend, self.release)

        def download(artifact, folder, filename, progress=None):
            target = folder / filename
            target.write_bytes(self.content)
            if progress:
                progress(len(self.content), len(self.content))
            return target

        self.download = mock.Mock(side_effect=download)
        self.download_patch = mock.patch.object(backend, "download_artifact", self.download)
        self.download_patch.start()

    def tearDown(self):
        self.download_patch.stop()
        self.version_patch.stop()
        self.temporary.cleanup()

    def stage(self):
        return self_update.stage_update(backend, self.update, idle=True, data_root=self.data)

    def activate(self):
        target = self.stage()
        process = mock.Mock()
        process.poll.return_value = None
        with mock.patch.object(self_update.subprocess, "Popen", return_value=process) as spawn:
            attempt = self_update.begin_activation(backend, self.update, target, idle=True, data_root=self.data)
        return target, attempt, process, spawn

    def child_request(self, attempt):
        with mock.patch.object(backend, "APP_VERSION", self.update.version):
            return self_update.read_activation_request(backend, attempt.request.path,
                                                       attempt.request.target, data_root=self.data)

    def test_newer_selection_uses_only_launcher_in_verified_payload(self):
        self.assertIsNone(self_update.select_update(backend, dict(self.artifact)))
        self.assertIsNone(self_update.select_update(backend, {"launcher": {**self.artifact, "version": "2026.10.24.4"}}))
        self.assertEqual(self.update.version, "2026.10.24.6")
        for change in ({"url": "http://mitomyhy.github.io/setup.exe"},
                       {"url": "https://unrelated.example/setup.exe"}, {"size": True},
                       {"sha256": "a" * 63}, {"version": "../../evil"}):
            with self.subTest(change=change), self.assertRaises(self_update.SelfUpdateError):
                self_update.select_update(backend, {"launcher": {**self.artifact, **change}})
        self.download.assert_not_called()
        self.assertFalse(self.data.exists())

    def test_idle_guard_prevents_download_and_activation(self):
        with self.assertRaises(self_update.SelfUpdateError):
            self_update.stage_update(backend, self.update, idle=False, data_root=self.data)
        with mock.patch.object(self_update.subprocess, "Popen") as spawn:
            with self.assertRaises(self_update.SelfUpdateError):
                self_update.begin_activation(backend, self.update, self.old, idle=False, data_root=self.data)
            spawn.assert_not_called()
        self.download.assert_not_called()
        self.assertFalse(self.data.exists())

    def test_newest_signed_source_wins_over_stale_independent_channel(self):
        newer = {"launcher": {**self.artifact, "version": "2026.10.24.8"}}
        selected = self_update.select_newest_update(backend, self.release, newer)
        self.assertEqual(selected.version, "2026.10.24.8")
        self.assertEqual(self_update.select_newest_update(backend, newer, self.release), selected)
        with self.assertRaises(self_update.SelfUpdateError):
            self_update.select_newest_update(backend, newer,
                {"launcher": {**newer["launcher"], "sha256": "b" * 64}})

    def test_activation_continuation_is_allowlisted_and_bound_to_readiness(self):
        target = self.stage()
        process = mock.Mock()
        process.poll.return_value = None
        for action in ("panel", "vector", "mifont"):
            with self.subTest(action=action), mock.patch.object(self_update.subprocess, "Popen", return_value=process):
                attempt = self_update.begin_activation(backend, self.update, target, idle=True,
                    data_root=self.data, resume_action=action)
            child = self.child_request(attempt)
            self.assertEqual(child.resume_action, action)
            self_update.mark_activation_ready(child)
            response = child.path.parent / "ready.json"
            value = json.loads(response.read_text())
            value["resume_action"] = "mifont" if action != "mifont" else "panel"
            response.write_text(json.dumps(value))
            with self.assertRaises(self_update.SelfUpdateError):
                self_update.poll_activation(attempt)
        for action in ("--install", "https://example.com", "C:/arbitrary.exe", [], {}):
            with self.subTest(action=action), mock.patch.object(self_update.subprocess, "Popen") as spawn:
                with self.assertRaises(self_update.SelfUpdateError):
                    self_update.begin_activation(backend, self.update, target, idle=True,
                        data_root=self.data, resume_action=action)
                spawn.assert_not_called()

    def test_staging_verifies_new_and_existing_bytes_and_preserves_other_apps(self):
        target = self.stage()
        self.assertEqual(target, self.data / "MITOMLauncher-2026.10.24.6.exe")
        self.assertEqual(target.read_bytes(), self.content)
        self.assertEqual(self.stage(), target)
        self.assertEqual(self.download.call_count, 1)
        target.write_bytes(b"changed")
        with self.assertRaises(self_update.SelfUpdateError):
            self.stage()
        self.assertEqual(target.read_bytes(), b"changed")
        self.assertEqual(self.old.read_bytes(), b"MZ-old-running-launcher")
        self.assertEqual(self.panel.read_bytes(), b"private-panel-state-unchanged")

    def test_wrong_download_size_hash_or_header_never_publishes(self):
        def broken(artifact, folder, filename, progress=None):
            path = folder / filename
            path.write_bytes(b"corrupted-download")
            return path

        self.download.side_effect = broken
        with self.assertRaises(self_update.SelfUpdateError):
            self.stage()
        self.assertFalse((self.data / "MITOMLauncher-2026.10.24.6.exe").exists())

        def wrong_hash(artifact, folder, filename, progress=None):
            path = folder / filename
            path.write_bytes(self.content[:-1] + bytes([self.content[-1] ^ 1]))
            return path

        self.download.side_effect = wrong_hash
        with self.assertRaises(self_update.SelfUpdateError):
            self.stage()
        self.assertFalse((self.data / "MITOMLauncher-2026.10.24.6.exe").exists())
        self.content = b"XX" + bytes(range(256)) * 6
        self.artifact.update(size=len(self.content), sha256=hashlib.sha256(self.content).hexdigest())
        self.update = self_update.select_update(backend, self.release)

        def wrong_header(artifact, folder, filename, progress=None):
            path = folder / filename
            path.write_bytes(self.content)
            return path

        self.download.side_effect = wrong_header
        with self.assertRaises(self_update.SelfUpdateError):
            self.stage()
        self.assertFalse((self.data / "MITOMLauncher-2026.10.24.6.exe").exists())

    def test_racing_unknown_target_is_not_overwritten(self):
        def race(temporary, target):
            target.write_bytes(b"unknown-concurrent-file")
            raise FileExistsError(target)

        with mock.patch.object(self_update, "_publish_new_file", side_effect=race):
            with self.assertRaises(self_update.SelfUpdateError):
                self.stage()
        self.assertEqual((self.data / "MITOMLauncher-2026.10.24.6.exe").read_bytes(), b"unknown-concurrent-file")
        self.assertEqual(list(self.data.glob("launcher-update-*.tmp")), [])

    def test_spawn_resets_frozen_environment_and_requires_ready_commit(self):
        target, attempt, process, spawn = self.activate()
        arguments, options = spawn.call_args
        self.assertEqual(arguments[0], [str(target), "--activate-launcher", str(attempt.request.path)])
        self.assertEqual(options["env"]["PYINSTALLER_RESET_ENVIRONMENT"], "1")
        self.assertEqual(options["cwd"], str(self.data))
        self.assertTrue(options["close_fds"])
        self.assertEqual(self_update.poll_activation(attempt), "waiting")
        with self.assertRaises(self_update.SelfUpdateError):
            self_update.commit_activation(attempt)
        request = self.child_request(attempt)
        self.assertFalse(self_update.activation_committed(request))
        self_update.mark_activation_ready(request)
        self.assertEqual(self_update.poll_activation(attempt), "ready")
        self.assertFalse(self_update.activation_committed(request))
        self.assertEqual(self_update.commit_activation(attempt), (target, process))
        self.assertTrue(self_update.activation_committed(request))
        self.assertTrue(attempt.committed)
        process.terminate.assert_not_called()
        process.kill.assert_not_called()

    def test_failed_or_expired_new_process_does_not_commit_or_kill(self):
        target, attempt, process, _spawn = self.activate()
        process.poll.return_value = 1
        with self.assertRaises(self_update.SelfUpdateError):
            self_update.poll_activation(attempt)
        self.assertFalse((attempt.request.path.parent / "commit.json").exists())
        process.poll.return_value = None
        with mock.patch.object(self_update.time, "monotonic", return_value=attempt.deadline + 1):
            with self.assertRaises(self_update.SelfUpdateError):
                self_update.poll_activation(attempt)
        self_update.cancel_activation(attempt)
        with self.assertRaises(self_update.SelfUpdateError):
            self_update.activation_committed(self.child_request(attempt))
        process.terminate.assert_not_called()
        process.kill.assert_not_called()
        self.assertEqual(self.old.read_bytes(), b"MZ-old-running-launcher")

    def test_child_rejects_wrong_path_binary_version_and_expiry(self):
        target, attempt, _process, _spawn = self.activate()
        with self.assertRaises(self_update.SelfUpdateError):
            self_update.read_activation_request(backend, attempt.request.path, target, data_root=self.data)
        with mock.patch.object(backend, "APP_VERSION", self.update.version):
            for request_path, executable in ((attempt.request.path, self.old),
                                             (self.root / "request.json", target)):
                with self.subTest(path=request_path), self.assertRaises(self_update.SelfUpdateError):
                    self_update.read_activation_request(backend, request_path, executable, data_root=self.data)
            with mock.patch.object(self_update.time, "time", return_value=attempt.request.expires_at + 1):
                with self.assertRaises(self_update.SelfUpdateError):
                    self_update.read_activation_request(backend, attempt.request.path, target, data_root=self.data)
            target.write_bytes(b"tampered-after-parent-stage")
            with self.assertRaises(self_update.SelfUpdateError):
                self_update.read_activation_request(backend, attempt.request.path, target, data_root=self.data)

    def test_forged_ready_response_never_commits(self):
        _target, attempt, process, _spawn = self.activate()
        path = attempt.request.path.parent / "ready.json"
        path.write_text(json.dumps({"schema": 1, "nonce": "wrong"}), encoding="utf-8")
        with self.assertRaises(self_update.SelfUpdateError):
            self_update.commit_activation(attempt)
        self.assertFalse((attempt.request.path.parent / "commit.json").exists())
        self.assertFalse(attempt.committed)
        process.terminate.assert_not_called()

    def test_same_release_activation_is_restricted_to_managed_executable(self):
        target = self.stage()
        other = self.root / "different-location.exe"
        other.write_bytes(target.read_bytes())
        with mock.patch.object(self_update.subprocess, "Popen") as spawn:
            with self.assertRaises(self_update.SelfUpdateError):
                self_update.begin_activation(backend, self.update, other, idle=True, data_root=self.data)
            spawn.assert_not_called()


if __name__ == "__main__":
    unittest.main()
