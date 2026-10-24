"""Exercise the real adapter callbacks without creating Tk or starting apps."""
from pathlib import Path
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class CallbackRoot:
    def __init__(self):
        self.tasks = []
        self.destroy = mock.Mock()
        self.deiconify = mock.Mock()
        self.lift = mock.Mock()
        self.update_idletasks = mock.Mock()

    def after_idle(self, callback):
        self.tasks.append(callback)

    def after(self, delay, callback):
        self.tasks.append(callback)

    def next(self):
        self.tasks.pop(0)()


class Value:
    def __init__(self):
        self.value = ""

    def set(self, value):
        self.value = value

    def get(self):
        return self.value


class AdapterUpdateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import main
        cls.entry = main

    def setUp(self):
        self.app = object.__new__(self.entry.MITOMAdapter)
        self.app.root = CallbackRoot()
        self.app.busy = False
        self.app.latest = None
        self.app._launcher_release = {"launcher": {"version": "2099.01.01.1"}}
        self.app._launcher_activation = None
        self.app._activation_request = object()
        self.app._update_discovery = mock.Mock()
        self.app.status_var = Value()
        self.app.ui = types.SimpleNamespace(state={}, set_busy=mock.Mock(), set_progress=mock.Mock(),
                                            set_state=mock.Mock(), log=mock.Mock())
        self.app._refresh_state = mock.Mock(return_value={})
        self.app._install = mock.Mock()
        self.app._verify = mock.Mock()
        self.app._rollback = mock.Mock()
        self.app._install_vector = mock.Mock()

    def assert_no_panel_or_app_install(self):
        for name in ("_install", "_verify", "_rollback", "_install_vector"):
            getattr(self.app, name).assert_not_called()

    def test_manual_metadata_result_waits_until_worker_transaction_finishes(self):
        received = []
        self.app._apply_catalogue_updates = lambda result: received.append((self.app.busy, result))
        dispatched = []
        self.app._run = lambda message, worker, callback: dispatched.append(callback)
        self.app._check_updates()
        self.app.busy = True
        result = {"panel": {"version": "fixture"}, "launcher": None, "errors": []}
        dispatched[0](result)
        self.assertEqual(received, [])
        self.app.busy = False
        self.app.root.next()
        self.assertEqual(received, [(False, result)])
        self.assert_no_panel_or_app_install()

    def test_download_completion_defers_activation_until_busy_is_cleared(self):
        update = types.SimpleNamespace(version="2099.01.01.1")
        dispatched = []
        self.app._run = lambda message, worker, callback: dispatched.append(callback)
        activations = []
        self.app._begin_launcher_activation = lambda selected, target: activations.append((self.app.busy, selected, target))
        with mock.patch.object(self.entry.self_update, "select_update", return_value=update):
            self.app._download_update()
        self.app.busy = True
        target = Path("verified-versioned-launcher.exe")
        dispatched[0](target)
        self.assertEqual(activations, [])
        self.app.busy = False
        self.app.root.next()
        self.assertEqual(activations, [(False, update, target)])
        self.assert_no_panel_or_app_install()

    def test_parent_closes_only_after_readiness_and_commit(self):
        attempt = object()
        self.app._launcher_activation = attempt
        with mock.patch.object(self.entry.self_update, "poll_activation", side_effect=["waiting", "ready"]) as poll, \
                mock.patch.object(self.entry.self_update, "commit_activation") as commit:
            self.app._poll_launcher_activation()
            self.app.root.destroy.assert_not_called()
            commit.assert_not_called()
            self.app.root.next()
            self.app.root.destroy.assert_called_once_with()
            commit.assert_called_once_with(attempt)
            self.assertEqual(poll.call_count, 2)
        self.assert_no_panel_or_app_install()

    def test_failed_new_process_keeps_parent_open_and_releases_ui_busy_state(self):
        self.app.busy = True
        attempt = object()
        self.app._launcher_activation = attempt
        with mock.patch.object(self.entry.self_update, "poll_activation", side_effect=self.entry.self_update.SelfUpdateError("child failed")), \
                mock.patch.object(self.entry.self_update, "cancel_activation") as cancel:
            self.app._poll_launcher_activation()
            cancel.assert_called_once_with(attempt)
        self.assertFalse(self.app.busy)
        self.app.root.destroy.assert_not_called()
        self.assertIsNone(self.app._launcher_activation)
        self.app.ui.set_busy.assert_called_with(False)
        self.assertIn("remains open", self.app.status_var.value)
        self.assert_no_panel_or_app_install()

    def test_child_stays_withdrawn_until_parent_commits_then_starts_discovery(self):
        with mock.patch.object(self.entry.activation_smoke, "persist_if_smoke", return_value=False), \
                mock.patch.object(self.entry.activation_smoke, "finish_if_smoke", return_value=False), \
                mock.patch.object(self.entry.backend, "persist_launcher") as persist, \
                mock.patch.object(self.entry.self_update, "mark_activation_ready") as ready, \
                mock.patch.object(self.entry.self_update, "activation_committed", side_effect=[False, True]):
            self.app._prepare_activation_child()
            persist.assert_called_once_with()
            ready.assert_called_once_with(self.app._activation_request)
            self.app.root.deiconify.assert_not_called()
            self.app.root.next()
            self.app.root.deiconify.assert_not_called()
            self.app._update_discovery.start.assert_not_called()
            self.app.root.next()
            self.app.root.deiconify.assert_called_once_with()
            self.app._update_discovery.start.assert_called_once_with()
            self.app.root.destroy.assert_not_called()
        self.assert_no_panel_or_app_install()

    def test_child_persistence_failure_never_acknowledges_health_or_shows_ui(self):
        with mock.patch.object(self.entry.activation_smoke, "persist_if_smoke", return_value=False), \
                mock.patch.object(self.entry.backend, "persist_launcher", side_effect=OSError("shortcut unavailable")), \
                mock.patch.object(self.entry.self_update, "mark_activation_ready") as ready:
            self.app._prepare_activation_child()
            ready.assert_not_called()
            self.app.root.destroy.assert_called_once_with()
            self.app.root.deiconify.assert_not_called()
            self.app._update_discovery.start.assert_not_called()
        self.assert_no_panel_or_app_install()

    def test_launcher_channel_does_not_replace_signed_panel_payload(self):
        panel = {"version": "panel-fixture", "launcher": {"version": "old-bootstrap"}}
        launcher = {"launcher": {"version": "2099.01.01.1"}}

        def apply(app, payload, original, backend):
            app.latest = payload

        with mock.patch.object(self.entry, "apply_verified_release", side_effect=apply) as apply_panel, \
                mock.patch.object(self.entry.self_update, "select_update", return_value=None) as select:
            self.app._apply_catalogue_updates({"panel": panel, "launcher": launcher, "errors": []})
            apply_panel.assert_called_once_with(self.app, panel, self.entry._legacy_update_check, self.entry.backend)
            select.assert_called_once_with(self.entry.backend, launcher)
        self.assertIs(self.app.latest, panel)
        self.assertIs(self.app._launcher_release, launcher)
        self.assert_no_panel_or_app_install()

    def test_status_uses_current_snapshot_before_queued_ui_state_is_applied(self):
        self.app._launcher_release = None
        self.app.ui.state = {"vector_update_available": False, "update_available": False}
        self.app._refresh_state.return_value = {"vector_update_available": True, "vector_latest_version": "1.0.2"}
        with mock.patch.object(self.entry.self_update, "select_update", return_value=None):
            self.app._apply_catalogue_updates({"panel": None, "launcher": None, "errors": []})
        self.assertEqual(self.app.status_var.value, "Vector Migic 1.0.2 is available.")
        self.app.ui.state = {"vector_update_available": True, "vector_latest_version": "stale"}
        self.app._refresh_state.return_value = {"vector_update_available": False, "update_available": False}
        with mock.patch.object(self.entry.self_update, "select_update", return_value=None):
            self.app._apply_catalogue_updates({"panel": None, "launcher": None, "errors": []})
        self.assertIn("installed apps are up to date", self.app.status_var.value)

    def test_failed_panel_channel_does_not_hide_valid_launcher_update(self):
        launcher = {"launcher": {"version": "2099.10.24.1", "size": 65536, "sha256": "a" * 64,
                                 "url": "https://mitomyhy.github.io/releases/mitom-launcher/2099.10.24.1/setup.exe"}}
        with mock.patch.object(self.entry, "probe_signed_release", side_effect=OSError("panel feed offline")), \
                mock.patch.object(self.entry.launcher_channel, "probe_launcher_release", return_value=launcher):
            result = self.entry.probe_catalogue_updates()
        self.assertIsNone(result["panel"])
        self.assertIs(result["launcher"], launcher)
        self.assertEqual(len(result["errors"]), 1)
        self.app._apply_catalogue_updates(result)
        self.assertIs(self.app._launcher_release, launcher)
        self.assertIn("2099.10.24.1 is available", self.app.status_var.value)
        self.assertIn("Update & restart", self.app.status_var.value)
        self.app.ui.log.assert_called_once()
        self.assert_no_panel_or_app_install()

    def test_activation_cli_rejects_extra_options_before_reading_any_request(self):
        with mock.patch.object(self.entry.sys, "argv", ["launcher.exe", "--activate-launcher", "request.json", "--verify-only"]), \
                mock.patch.object(self.entry.self_update, "read_activation_request") as read:
            with self.assertRaises(self.entry.backend.LauncherError):
                self.entry.main()
            read.assert_not_called()


if __name__ == "__main__":
    unittest.main()
