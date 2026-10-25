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
        with mock.patch.object(self.entry.self_update, "select_newest_update", return_value=update):
            self.app._download_update()
        self.app.busy = True
        target = Path("verified-versioned-launcher.exe")
        dispatched[0]({"target": target})
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
                mock.patch.object(self.entry.self_update, "select_newest_update", return_value=None) as select:
            self.app._apply_catalogue_updates({"panel": panel, "launcher": launcher, "errors": []})
            apply_panel.assert_called_once_with(self.app, panel, self.entry._legacy_update_check, self.entry.backend)
            select.assert_called_once_with(self.entry.backend, launcher, panel)
        self.assertIs(self.app.latest, panel)
        self.assertIs(self.app._launcher_release, launcher)
        self.assert_no_panel_or_app_install()

    def test_status_uses_current_snapshot_before_queued_ui_state_is_applied(self):
        self.app._launcher_release = None
        self.app.ui.state = {"vector_update_available": False, "update_available": False}
        self.app._refresh_state.return_value = {"vector_update_available": True, "vector_latest_version": "1.0.2"}
        with mock.patch.object(self.entry.self_update, "select_newest_update", return_value=None):
            self.app._apply_catalogue_updates({"panel": None, "launcher": None, "errors": []})
        self.assertEqual(self.app.status_var.value, "Vector Migic 1.0.2 is available.")
        self.app.ui.state = {"vector_update_available": True, "vector_latest_version": "stale"}
        self.app._refresh_state.return_value = {"vector_update_available": False, "update_available": False}
        with mock.patch.object(self.entry.self_update, "select_newest_update", return_value=None):
            self.app._apply_catalogue_updates({"panel": None, "launcher": None, "errors": []})
        self.assertIn("installed apps are up to date", self.app.status_var.value)

    def test_failed_panel_channel_does_not_hide_valid_launcher_update(self):
        launcher = {"launcher": {"version": "2099.10.24.1", "size": 65536, "sha256": "a" * 64,
                                 "url": "https://mitomyhy.github.io/releases/mitom-launcher/2099.10.24.1/setup.exe"}}
        with mock.patch.object(self.entry, "probe_signed_release", side_effect=OSError("panel feed offline")), \
                mock.patch.object(self.entry.launcher_channel, "probe_launcher_release", return_value=launcher), \
                mock.patch.object(self.entry.app_catalogue, "probe_app_catalogue", return_value=None):
            result = self.entry.probe_catalogue_updates()
        self.assertIsNone(result["panel"])
        self.assertIs(result["launcher"], launcher)
        self.assertEqual(len(result["errors"]), 1)
        self.app._apply_catalogue_updates(result)
        self.assertIs(self.app._launcher_release, launcher)
        self.assertIn("2099.10.24.1 automatically", self.app.status_var.value)
        self.app.ui.log.assert_called_once()
        self.assert_no_panel_or_app_install()

    def test_activation_cli_rejects_extra_options_before_reading_any_request(self):
        with mock.patch.object(self.entry.sys, "argv", ["launcher.exe", "--activate-launcher", "request.json", "--verify-only"]), \
                mock.patch.object(self.entry.self_update, "read_activation_request") as read:
            with self.assertRaises(self.entry.backend.LauncherError):
                self.entry.main()
            read.assert_not_called()

    def test_discovery_upgrades_launcher_automatically_only_once_per_session(self):
        update = types.SimpleNamespace(version="2099.01.01.1")
        self.app._download_update = mock.Mock()
        with mock.patch.object(self.entry.self_update, "select_newest_update", return_value=update):
            self.app._apply_catalogue_updates({"panel": None, "launcher": None, "errors": []})
            self.app._download_update.assert_not_called()
            self.app.root.next()
            self.app._download_update.assert_called_once_with()
            self.app._apply_catalogue_updates({"panel": None, "launcher": None, "errors": []})
            self.assertEqual(self.app.root.tasks, [])
            self.app._download_update.assert_called_once_with()
        self.assert_no_panel_or_app_install()

    def test_pending_install_continues_after_healthy_activation_and_fresh_signed_check(self):
        self.app._activation_request = types.SimpleNamespace(resume_action="panel")
        self.app._pending_install_action = "panel"
        self.app._check_updates = mock.Mock()
        with mock.patch.object(self.entry.activation_smoke, "finish_if_smoke", return_value=False), \
                mock.patch.object(self.entry.self_update, "activation_committed", return_value=True):
            self.app._poll_activation_commit()
        self.app._check_updates.assert_not_called()
        self.app.root.next()
        self.app._check_updates.assert_called_once_with()
        self.assert_no_panel_or_app_install()

    def test_pending_panel_never_installs_stale_metadata_when_online_probe_failed(self):
        self.app._pending_install_action = "panel"
        self.app.latest = {"version": "2026.10.25.1"}
        self.app._run = mock.Mock()
        self.assertTrue(self.app._continue_install({"panel": None}))
        self.app._run.assert_not_called()
        self.assertIsNone(self.app._pending_install_action)
        self.assertIn("unchanged", self.app.status_var.value)

    def test_auto_queue_updates_installed_apps_and_excludes_missing_apps(self):
        self.app._mifont_release = {"version": "1.0.3"}
        self.app._vector_release = {"version": "1.0.4"}
        snapshot = {"installed": True, "update_available": True,
                    "mifont_installed": True, "mifont_update_available": True,
                    "vector_installed": False, "vector_update_available": True}
        panel = {"version": "2026.10.25.1"}
        self.app._queue_automatic_installs({"apps": {}, "panel": panel}, snapshot)
        self.assertEqual([(row[0], row[1]) for row in self.app._automatic_install_queue],
                         [("mifont", "1.0.3"), ("panel", "2026.10.25.1")])

    def test_auto_panel_waits_for_photoshop_without_downloading_or_stopping_any_process(self):
        panel = {"version": "2026.10.25.1"}
        self.app.latest = panel
        self.app._automatic_install_queue = [("panel", panel["version"], panel)]
        self.app._run = mock.Mock()
        with mock.patch.object(self.entry.installer, "_processes", return_value=[{"name": "Photoshop.exe", "pid": 123}]), \
                mock.patch.object(self.entry, "install_verified_panel") as install:
            self.app._automatic_install_next()
            self.app._run.assert_not_called()
            install.assert_not_called()
            self.assertIn("Waiting for Photoshop", self.app.status_var.value)
            self.assertTrue(self.app._waiting_panel_retry)
        with mock.patch.object(self.entry.installer, "_processes", return_value=[]):
            self.app.root.next()
        self.app._run.assert_called_once()

    def test_auto_failure_preserves_session_and_is_not_retried_until_manual_action(self):
        release = {"version": "1.0.3"}
        self.app._automatic_install_queue = [("mifont", "1.0.3", release)]
        dispatched = []
        self.app._run = lambda message, worker, callback: dispatched.append((worker, callback))
        self.app._automatic_install_next()
        with mock.patch.object(self.entry.mi_font_app, "install_app", side_effect=OSError("download interrupted")):
            value = dispatched[0][0]()
        self.assertFalse(value["ok"])
        dispatched[0][1](value)
        self.assertIn("previous version is retained", self.app.status_var.value)
        self.app.root.next()
        self.app._automatic_install_queue = [("mifont", "1.0.3", release)]
        self.app._automatic_install_next()
        self.assertEqual(len(dispatched), 1)

    def test_panel_installs_compatible_release_even_when_it_advertises_newer_launcher(self):
        release = {"version": "2026.10.25.1", "launcher": {"version": "2099.01.01.1"}}
        with mock.patch.object(self.entry.installer, "get_installed_version", return_value="2026.10.23.2"), \
                mock.patch.object(self.entry.installer, "_closed") as closed, \
                mock.patch.object(self.entry.backend, "obtain_core", return_value=Path("verified-staging")) as obtain, \
                mock.patch.object(self.entry.installer, "install", return_value={"version": release["version"]}) as install, \
                mock.patch.object(self.entry.backend, "persist_launcher", return_value="managed.exe"), \
                mock.patch.object(self.entry.backend, "cleanup_core_extraction") as cleanup:
            result = self.entry.install_verified_panel(release)
        closed.assert_called_once_with(False)
        obtain.assert_called_once_with(release, progress=None)
        install.assert_called_once_with(Path("verified-staging"), dry_run=False)
        cleanup.assert_called_once_with(Path("verified-staging"))
        self.assertEqual(result["version"], release["version"])

    def test_real_minimum_and_running_photoshop_are_checked_before_any_panel_download(self):
        with mock.patch.object(self.entry.installer, "get_installed_version", return_value="2026.10.23.2"), \
                mock.patch.object(self.entry.backend, "obtain_core") as obtain:
            with self.assertRaises(self.entry.backend.LauncherError):
                self.entry.install_verified_panel({"version": "2026.10.25.1", "min_launcher_version": "2099.01.01.1"})
            with mock.patch.object(self.entry.installer, "_closed", side_effect=self.entry.installer.InstallError("Photoshop is open")):
                with self.assertRaises(self.entry.installer.InstallError):
                    self.entry.install_verified_panel({"version": "2026.10.25.1"})
            obtain.assert_not_called()

    def test_compatible_manual_install_does_not_wait_for_a_broken_bootstrap(self):
        self.app._pending_install_action = "panel"
        self.app._run = mock.Mock()
        panel = {"version": "2026.10.25.1"}
        with mock.patch.object(self.entry, "apply_verified_release", side_effect=lambda app, value, *_: setattr(app, 'latest', value)), \
                mock.patch.object(self.entry.self_update, "select_newest_update", return_value=types.SimpleNamespace(version="2099.01.01.1")):
            self.app._apply_catalogue_updates({"panel": panel, "launcher": None, "apps": None})
        self.app._run.assert_called_once()
        self.assertIn("Installing", self.app._run.call_args.args[0])
        self.assertEqual(self.app.root.tasks, [])

    def test_failed_launcher_download_keeps_ui_and_queues_compatible_app_work(self):
        self.app._pending_catalogue_result = {"panel": None, "apps": None}
        self.app._queue_automatic_installs = mock.Mock()
        self.app._launcher_download_complete(types.SimpleNamespace(version="2099.01.01.1"),
                                            {"error": OSError("network interrupted")})
        self.app.root.destroy.assert_not_called()
        self.app.root.next()
        self.app._queue_automatic_installs.assert_called_once()


if __name__ == "__main__":
    unittest.main()
