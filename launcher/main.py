"""Desktop/CLI entry point for the MITOM launcher repair."""
from __future__ import annotations

import datetime
import concurrent.futures
import argparse
import json
import os
from pathlib import Path
import sys
import threading
import tkinter as tk
from tkinter import font as tkfont
import webbrowser

import backend
import installer
import vector_app
import mi_font_app
import self_update
import launcher_channel
import app_catalogue
import activation_smoke
from mitom_ui import CAPTION_FONT_FAMILY, CAPTION_TRACKING_SEPARATOR, MITOMLauncherUI
from update_discovery import UpdateDiscovery, apply_verified_release, probe_signed_release

_legacy_update_check = backend.LauncherUI._check_updates


def probe_catalogue_updates(cancelled=None):
    """Check independently authenticated channels without mixing panel trust."""
    cancelled = cancelled or threading.Event()
    result = {"panel": None, "launcher": None, "apps": None, "errors": []}
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as workers:
        checks = {
            "panel": workers.submit(probe_signed_release, backend, cancelled),
            "launcher": workers.submit(launcher_channel.probe_launcher_release, backend, cancelled),
            "apps": workers.submit(app_catalogue.probe_app_catalogue, backend, cancelled),
        }
        for name, future in checks.items():
            try:
                result[name] = future.result()
            except Exception as exc:
                result["errors"].append(name + " update check unavailable: " + str(exc)[:320])
    return result


def install_verified_panel(release, progress=None):
    """Install the authenticated panel without treating a newer bootstrap as a requirement."""
    current = installer.get_installed_version()
    if current and backend.version_tuple(release["version"]) < backend.version_tuple(current):
        raise backend.LauncherError("The signed panel release is older than the installed panel; refusing a downgrade.")
    minimum = release.get("min_launcher_version")
    if minimum and backend.version_tuple(minimum) > backend.version_tuple(backend.APP_VERSION):
        raise backend.LauncherError("The signed panel requires launcher " + minimum + "; update the launcher first.")
    installer._closed(False)
    source = backend.obtain_core(release, progress=progress)
    result = None
    try:
        result = installer.install(source, dry_run=False)
        try:
            result["launcher_path"] = backend.persist_launcher()
        except Exception as exc:
            result.setdefault("warnings", []).append("Panel installed; launcher shortcut could not be refreshed: " + str(exc))
        return result
    finally:
        try:
            backend.cleanup_core_extraction(source)
        except OSError:
            if result is not None:
                result.setdefault("warnings", []).append("Temporary extraction could not be removed; backups are retained.")


class _UpdateAction:
    """Bridge the original update button selection to the new catalogue view."""
    def __init__(self, command):
        self.command = command

    def configure(self, **values):
        if "command" in values:
            self.command = values["command"]


class MITOMAdapter(backend.LauncherUI):
    BG = "#f2f1ed"

    def __init__(self, *args, **kwargs):
        self._activation_request = kwargs.pop("activation_request", None)
        self._launcher_activation = None
        self._launcher_release = None
        self._automatic_updates_attempted = set()
        self._automatic_installs_attempted = set()
        self._automatic_install_queue = []
        self._waiting_panel_retry = False
        self._pending_install_action = getattr(self._activation_request, "resume_action", None)
        super().__init__(*args, **kwargs)
        if {"--ui-smoke", "--self-test", "--verify-only", "--network-smoke", "--help", "-h"}.intersection(sys.argv[1:]):
            return
        self._r4_ui_thread = threading.get_ident()
        self._update_discovery = UpdateDiscovery(
            self.root, lambda: bool(self.busy),
            probe_catalogue_updates,
            self._apply_catalogue_updates,
            self._append)
        self.root.bind("<Destroy>", lambda event: self._update_discovery.close()
                       if event.widget is self.root else None, add=True)
        if self._activation_request is not None:
            self.root.after_idle(self._prepare_activation_child)
        else:
            self._update_discovery.start()

    def _build(self):
        if self._activation_request is not None:
            self.root.withdraw()
        self.version_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Ready. Choose an app to install or open.")
        self.update_button = _UpdateAction(self._install)
        self._vector_release = None
        self._vector_release_error = ""
        self._mifont_release = None
        self._mifont_release_error = ""
        try:
            self._apply_app_catalogue(app_catalogue.load_cached_catalogue(backend))
        except (OSError, ValueError, RuntimeError) as exc:
            self._mifont_release_error = "Cached online app catalogue unavailable: " + str(exc)
        try:
            preferences = json.loads((backend.launcher_data_root() / "launcher-preferences.json").read_text(encoding="utf-8"))
            language = "vi" if preferences.get("language") == "vi" else "en"
        except (OSError, ValueError, AttributeError):
            language = "en"
        try:
            self._vector_release = vector_app.load_release()
        except (OSError, ValueError, vector_app.VectorAppError) as exc:
            self._vector_release_error = str(exc)
        try:
            self._mifont_release = mi_font_app.load_release()
        except (OSError, ValueError, mi_font_app.MiFontAppError) as exc:
            self._mifont_release_error = str(exc)
        self.ui = MITOMLauncherUI(self.root, {
            "install": self._install,
            "verify": self._verify,
            "check_updates": self._check_updates,
            "install_update": self._install,
            "update_launcher": self._download_update,
            "open_photoshop": self._open_photoshop,
            "companion": self._open_companion,
            "chrome": self._open_setup,
            "rollback": self._rollback,
            "website": lambda: webbrowser.open(backend.APPS_URL),
            "github": lambda: webbrowser.open(backend.GITHUB_URL),
            "open_cache": self._open_cache,
            "install_vector": self._install_vector,
            "open_vector": self._open_vector,
            "vector_website": lambda: webbrowser.open(backend.APPS_URL + "#vector-migic"),
            "install_mifont": self._install_mifont,
            "open_mifont": self._open_mifont,
            "mifont_website": lambda: webbrowser.open(backend.APPS_URL + "#mifont"),
            "save_language": self._save_language,
        }, {
            "launcher_version": backend.APP_VERSION,
            "latest_version": backend.CORE_VERSION,
            "website": backend.APPS_URL,
            "github": backend.GITHUB_URL,
            "cache_path": str(backend.launcher_data_root() / "Downloads"),
            "signature_status": "No Authenticode certificate · panel packages verify release signatures and SHA-256",
            "language": language,
            **vector_app.installed_state(backend.launcher_data_root(), self._vector_release),
            **mi_font_app.installed_state(backend.launcher_data_root(), self._mifont_release),
        })
        self.progress = self.ui.progress
        self.log = self.ui.log_text
        self.version_var.trace_add("write", lambda *_: self._refresh_state())
        self.status_var.trace_add("write", lambda *_: self._refresh_state())
        self._append("MITOM Launcher " + backend.APP_VERSION)
        if self._vector_release_error:
            self._append("Vector Migic: " + self._vector_release_error)
        if self._mifont_release_error:
            self._append("MiFont: " + self._mifont_release_error)
        self._refresh_state()

    def _refresh_state(self):
        try:
            current = installer.get_installed_version()
        except Exception:
            current = None
        latest = self.latest or {}
        release_version = latest.get("version", backend.CORE_VERSION)
        update_available = False
        launcher_available = False
        try:
            update_available = bool(current) and backend.version_tuple(release_version) > backend.version_tuple(current)
            launcher_update = self_update.select_newest_update(backend, self._launcher_release, latest)
            launcher_version = launcher_update.version if launcher_update else ""
            launcher_available = launcher_update is not None
        except (backend.LauncherError, self_update.SelfUpdateError):
            launcher_version = ""
        roots, _local, _sandbox = installer._roots(None)
        snapshot = {
            "installed": bool(current), "installed_version": current or "",
            "latest_version": release_version,
            "update_available": update_available,
            "launcher_update_available": launcher_available,
            "launcher_latest_version": launcher_version,
            "status": self.status_var.get(),
            "installed_path": str(roots["panel"]) if current else "",
            "panel_update_waiting": getattr(self, "_waiting_panel_release", None) is not None,
            **vector_app.installed_state(backend.launcher_data_root(), self._vector_release),
            **mi_font_app.installed_state(backend.launcher_data_root(), self._mifont_release),
        }
        self.ui.set_state(snapshot)
        return snapshot

    def _install(self):
        return self._request_install("panel")

    def _request_install(self, action):
        if self.busy:
            return
        if action not in {"panel", "vector", "mifont"}:
            raise ValueError("Unsupported install action.")
        self._pending_install_action = action
        self._automatic_install_queue = []
        # An explicit install/update click is also an explicit retry after a
        # failed automatic bootstrap handover in this session.
        self._automatic_updates_attempted = set()
        discovery = getattr(self, "_update_discovery", None)
        if discovery:
            discovery.supersede()
        return self._run("Checking the latest signed release before installing…", probe_catalogue_updates,
            lambda result: self.root.after_idle(lambda: self._apply_catalogue_updates(result)))

    def _continue_install(self, result):
        action = getattr(self, "_pending_install_action", None)
        if action is None:
            return False
        self._pending_install_action = None
        release = result.get("panel") if action == "panel" else (
            self._mifont_release if action == "mifont" else self._vector_release)
        minimum = (release or {}).get("min_launcher_version")
        if minimum and backend.version_tuple(minimum) > backend.version_tuple(backend.APP_VERSION):
            self.status_var.set("This release requires Launcher " + minimum + ". Retry the automatic launcher update.")
            return True
        if action == "panel":
            release = result.get("panel")
            if release is None:
                self.status_var.set("Panel update check failed. The installed panel is unchanged; retry when connected.")
                return True
            self._start_panel_install(release)
        elif action == "vector":
            self._install_vector_current()
        else:
            self._install_mifont_current()
        return True

    def _defer_panel_install(self, release, manual=False):
        self._waiting_panel_manual = bool(manual or getattr(self, "_waiting_panel_manual", False))
        self._waiting_panel_release = release
        self.status_var.set("Panel " + release["version"] + " is ready. Waiting for Photoshop / Order Studio to close before updating.")
        if not getattr(self, "_waiting_panel_retry", False):
            self._waiting_panel_retry = True
            self.root.after(15000, self._retry_waiting_panel)

    def _start_panel_install(self, release):
        def work():
            try:
                return {"result": install_verified_panel(release,
                    progress=lambda done, total: self.ui.set_progress(done, total))}
            except Exception as exc:
                if installer.is_applications_open(exc):
                    return {"waiting": True}
                raise
        def complete(value):
            if value.get("waiting"):
                self._defer_panel_install(release, manual=True)
            else:
                self._installed(value["result"])
        self._run("Installing the verified Photoshop Panel…", work, complete)

    def _queue_automatic_installs(self, result, snapshot):
        """Update already-installed apps; a missing app is never installed implicitly."""
        jobs = []
        if result.get("apps") is not None:
            for action, prefix, release in (("mifont", "mifont", self._mifont_release),
                                             ("vector", "vector", self._vector_release)):
                if snapshot.get(prefix + "_installed") and snapshot.get(prefix + "_update_available"):
                    jobs.append((action, release["version"], release))
        if result.get("panel") is not None and snapshot.get("installed") and snapshot.get("update_available"):
            jobs.append(("panel", result["panel"]["version"], result["panel"]))
        self._automatic_install_queue = jobs
        if jobs:
            self.root.after_idle(self._automatic_install_next)

    def _automatic_install_next(self):
        if self.busy:
            self.root.after(250, self._automatic_install_next)
            return
        jobs = getattr(self, "_automatic_install_queue", [])
        attempted = getattr(self, "_automatic_installs_attempted", None)
        if attempted is None:
            attempted = self._automatic_installs_attempted = set()
        while jobs:
            action, version, release = jobs.pop(0)
            key = (action, version)
            if key in attempted:
                continue
            if action == "panel":
                try:
                    running = installer._processes()
                except Exception as exc:
                    attempted.add(key)
                    self._append("Automatic panel update deferred: " + str(exc))
                    continue
                if running:
                    self._defer_panel_install(release)
                    continue
            attempted.add(key)
            def work(action=action, release=release):
                try:
                    minimum = release.get("min_launcher_version")
                    if minimum and backend.version_tuple(minimum) > backend.version_tuple(backend.APP_VERSION):
                        raise backend.LauncherError("This release requires Launcher " + minimum)
                    progress = lambda done, total: self.ui.set_progress(done, total)
                    if action == "panel":
                        value = install_verified_panel(release, progress=progress)
                    else:
                        module = mi_font_app if action == "mifont" else vector_app
                        value = module.install_app(backend.launcher_data_root(), release, progress=progress)
                    return {"ok": True, "result": value}
                except Exception as exc:
                    if action == "panel" and installer.is_applications_open(exc):
                        return {"ok": False, "waiting": True}
                    return {"ok": False, "error": backend.failure_details(exc)}
            def complete(value, action=action, version=version, release=release):
                if value.get("waiting"):
                    attempted.discard((action, version))
                    self._defer_panel_install(release)
                elif value["ok"]:
                    self.status_var.set(action + " " + version + " updated automatically.")
                else:
                    self._append("Automatic " + action + " update failed: " + value["error"])
                    self.status_var.set("Automatic " + action + " update could not finish. Use Update to retry; the previous version is retained.")
                self.root.after_idle(self._automatic_install_next)
            return self._run("Updating " + action + " " + version + " automatically…", work, complete)

    def _retry_waiting_panel(self):
        if self.busy:
            self.root.after(250, self._retry_waiting_panel)
            return
        self._waiting_panel_retry = False
        release = getattr(self, "_waiting_panel_release", None)
        manual = getattr(self, "_waiting_panel_manual", False)
        self._waiting_panel_release = None
        self._waiting_panel_manual = False
        # A newer check may have superseded this release while Photoshop was open.
        if release is None or self.latest is None:
            return
        if self.latest.get("version") != release["version"]:
            # A manually requested install follows the newer signed release too.
            if not manual:
                return
            release = self.latest
        if manual:
            self._start_panel_install(release)
        else:
            self._automatic_install_queue.append(("panel", release["version"], release))
            self._automatic_install_next()

    def _install_vector(self):
        return self._request_install("vector")

    def _install_vector_current(self):
        def complete(result):
            self.status_var.set("Vector Migic " + result["version"] + " installed. Ready to open.")
            self._append("Vector Migic verified and installed: " + result["path"])
        self._run("Installing Vector Migic…", lambda: vector_app.install_app(
            backend.launcher_data_root(), self._vector_release,
            progress=lambda value, maximum: self.ui.set_progress(value, maximum)), complete)

    def _open_vector(self):
        self._run("Opening Vector Migic…", lambda: vector_app.open_app(
            backend.launcher_data_root(), self._vector_release),
            lambda _process: self.status_var.set("Vector Migic opened on this computer."))

    def _install_mifont(self):
        return self._request_install("mifont")

    def _install_mifont_current(self):
        def complete(result):
            self.status_var.set("MiFont " + result["version"] + " installed. Ready to open.")
            self._append("MiFont verified and installed: " + result["path"])
        self._run("Installing MiFont…", lambda: mi_font_app.install_app(
            backend.launcher_data_root(), self._mifont_release,
            progress=lambda value, maximum: self.ui.set_progress(value, maximum)), complete)

    def _open_mifont(self):
        self._run("Opening MiFont…", lambda: mi_font_app.open_app(
            backend.launcher_data_root(), self._mifont_release),
            lambda _process: self.status_var.set("MiFont opened on this computer."))

    def _save_language(self, language):
        if language not in {"en", "vi"}:
            raise ValueError("Unsupported launcher language.")
        root = backend.launcher_data_root()
        root.mkdir(parents=True, exist_ok=True)
        destination = root / "launcher-preferences.json"
        temporary = destination.with_suffix(".tmp")
        temporary.write_text(json.dumps({"language": language}), encoding="utf-8")
        os.replace(temporary, destination)

    def _append(self, text):
        self.ui.log(text)

    def _run(self, message, work, callback=None):
        if self.busy:
            return
        self.ui.set_busy(True, message)
        def diagnosed_work():
            try:
                return work()
            except Exception as exc:
                # UI.log queues this text for Tk; the worker never calls Tk.
                self._append(message + "\n" + backend.failure_details(exc))
                raise
        return super()._run(message, diagnosed_work, callback)

    def _finish(self):
        super()._finish()
        self.ui.set_busy(False)
        self._refresh_state()

    def _check_updates(self):
        if self.busy:
            return
        discovery = getattr(self, "_update_discovery", None)
        if discovery:
            discovery.supersede()
        self.ui.set_state({"checked_at": "Checked " + datetime.datetime.now().strftime("%H:%M")})
        return self._run("Checking launcher and app updates…", probe_catalogue_updates,
                         lambda result: self.root.after_idle(lambda: self._apply_catalogue_updates(result)))

    def _apply_app_catalogue(self, catalogue):
        if not catalogue:
            return
        apps = catalogue.get("apps", {})
        history = catalogue.get("history", {})
        for app_id, module in (("mifont", mi_font_app), ("vector-migic", vector_app)):
            if apps.get(app_id) is not None:
                module.check_catalogue(apps[app_id], history.get(app_id, []))
        if apps.get("mifont") is not None:
            mi_font_app.use_catalogue(apps["mifont"], history.get("mifont", []))
            self._mifont_release = mi_font_app.load_release()
        if apps.get("vector-migic") is not None:
            vector_app.use_catalogue(apps["vector-migic"], history.get("vector-migic", []))
            self._vector_release = vector_app.load_release()

    def _apply_catalogue_updates(self, result):
        self._pending_catalogue_result = result
        if result.get("panel") is not None:
            apply_verified_release(self, result["panel"], _legacy_update_check, backend)
        if result.get("launcher") is not None:
            self._launcher_release = result["launcher"]
        self._apply_app_catalogue(result.get("apps"))
        for error in result.get("errors", []):
            self._append(error)
        snapshot = self._refresh_state()
        update = self_update.select_newest_update(backend, self._launcher_release, self.latest)
        action = getattr(self, "_pending_install_action", None)
        requested = result.get("panel") if action == "panel" else (
            self._mifont_release if action == "mifont" else self._vector_release if action == "vector" else None)
        minimum = (requested or {}).get("min_launcher_version")
        requires_bootstrap = bool(minimum) and backend.version_tuple(minimum) > backend.version_tuple(backend.APP_VERSION)
        if action is not None and not requires_bootstrap and self._continue_install(result):
            return
        attempted = getattr(self, "_automatic_updates_attempted", set())
        if update is not None and update.version not in attempted:
            self.status_var.set("Updating Launcher " + update.version + " automatically…")
            self.root.after_idle(lambda: self._automatic_launcher_update(update.version))
        elif self._continue_install(result):
            return
        elif result.get("errors"):
            self.status_var.set("Some update checks could not finish. See Activity Log and retry.")
        elif snapshot.get("vector_update_available"):
            self.status_var.set("Vector Migic " + snapshot.get("vector_latest_version", "") + " is available.")
        elif snapshot.get("mifont_update_available"):
            self.status_var.set("MiFont " + snapshot.get("mifont_latest_version", "") + " is available.")
        elif snapshot.get("update_available"):
            self.status_var.set("Photoshop Panel " + snapshot.get("latest_version", "") + " is available.")
        else:
            self.status_var.set("Launcher " + backend.APP_VERSION + " and installed apps are up to date.")
        if update is None or update.version in attempted:
            self._queue_automatic_installs(result, snapshot)

    def _automatic_launcher_update(self, version):
        if self.busy:
            self.root.after(250, lambda: self._automatic_launcher_update(version))
            return
        attempted = getattr(self, "_automatic_updates_attempted", None)
        if attempted is None:
            attempted = self._automatic_updates_attempted = set()
        if version in attempted:
            return
        update = self_update.select_newest_update(backend, self._launcher_release, self.latest)
        if update is not None and update.version == version:
            attempted.add(version)
            self._download_update()

    def _download_update(self):
        """One update action stages verified bytes and hands over healthy UI."""
        if self.busy:
            return
        update = self_update.select_newest_update(backend, self._launcher_release, self.latest)
        if update is None:
            return self._check_updates()
        discovery = getattr(self, "_update_discovery", None)
        if discovery:
            discovery.supersede()
        self._run("Downloading verified launcher " + update.version + "…",
                  lambda: self._stage_launcher_result(update),
                  lambda result: self.root.after_idle(lambda: self._launcher_download_complete(update, result)))

    def _stage_launcher_result(self, update):
        try:
            return {"target": self_update.stage_update(backend, update, idle=True,
                progress=lambda done, total: self.ui.set_progress(done, total))}
        except Exception as exc:
            return {"error": exc}

    def _launcher_download_complete(self, update, result):
        if "error" in result:
            self._activation_failed(result["error"])
        else:
            self._begin_launcher_activation(update, result["target"])

    def _begin_launcher_activation(self, update, target):
        if self.busy:
            self.root.after(100, lambda: self._begin_launcher_activation(update, target))
            return
        self.busy = True
        self.ui.set_busy(True, "Starting the updated launcher…")
        try:
            self._launcher_activation = self_update.begin_activation(backend, update, target, idle=True,
                resume_action=getattr(self, "_pending_install_action", None))
        except Exception as exc:
            self._activation_failed(exc)
            return
        self.root.after(100, self._poll_launcher_activation)

    def _poll_launcher_activation(self):
        try:
            if self_update.poll_activation(self._launcher_activation) == "waiting":
                self.root.after(100, self._poll_launcher_activation)
                return
            self_update.commit_activation(self._launcher_activation)
        except Exception as exc:
            self._activation_failed(exc)
            return
        # The new UI has constructed successfully and acknowledged its exact
        # executable. Close only this launcher's window, never another app.
        self.root.destroy()

    def _activation_failed(self, exc):
        if self._launcher_activation is not None:
            try:
                self_update.cancel_activation(self._launcher_activation)
            except Exception:
                pass
        self._launcher_activation = None
        self.busy = False
        self._append(backend.failure_details(exc))
        self.status_var.set("The new launcher could not start. This window remains open; retry the update.")
        self.ui.set_busy(False)
        result = getattr(self, "_pending_catalogue_result", None)
        if result is not None:
            # A compatible authenticated panel/app can still update when a
            # bootstrap download fails. Its real minimum is checked again.
            self.root.after_idle(lambda: self._queue_automatic_installs(result, self._refresh_state()))

    def _prepare_activation_child(self):
        try:
            if not activation_smoke.persist_if_smoke(backend, self._activation_request):
                backend.persist_launcher()
            self.root.update_idletasks()
            self_update.mark_activation_ready(self._activation_request)
        except Exception as exc:
            self._append(backend.failure_details(exc))
            self.root.destroy()
            return
        self.root.after(100, self._poll_activation_commit)

    def _poll_activation_commit(self):
        try:
            if not self_update.activation_committed(self._activation_request):
                self.root.after(100, self._poll_activation_commit)
                return
        except self_update.SelfUpdateError:
            self.root.destroy()
            return
        if activation_smoke.finish_if_smoke(backend, self._activation_request):
            self.root.destroy()
            return
        self.root.deiconify()
        self.root.lift()
        self._update_discovery.start()
        if getattr(self, "_pending_install_action", None) is not None:
            self.root.after_idle(self._check_updates)

    def _open_cache(self):
        cache = backend.launcher_data_root() / "Downloads"
        if cache.is_dir():
            os.startfile(str(cache))
        else:
            self.status_var.set("Bộ nhớ đệm sẽ xuất hiện sau lần tải đầu tiên.")


# Keep the original read-only --self-test / --verify-only CLI and error handling.
backend.LauncherUI = MITOMAdapter


def main():
    if "--activation-smoke" in sys.argv[1:]:
        return activation_smoke.main(backend)
    if "--activate-launcher" in sys.argv[1:]:
        if len(sys.argv) != 3 or sys.argv[1] != "--activate-launcher":
            raise backend.LauncherError("Launcher activation accepts only its verified request path.")
        request = self_update.read_activation_request(backend, Path(sys.argv[2]), sys.executable)
        application = MITOMAdapter(activation_request=request)
        application.root.mainloop()
        return 0
    if "--network-smoke" in sys.argv[1:]:
        parser = argparse.ArgumentParser(description="Read-only packaged TLS and signed-feed smoke test")
        parser.add_argument("--network-smoke", action="store_true")
        parser.add_argument("--result-json", type=Path)
        options = parser.parse_args()
        try:
            report = backend.network_smoke()
        except Exception as exc:
            report = {"ok": False, "version": backend.APP_VERSION,
                      "error": backend.failure_details(exc), "writes_panel": False}
        encoded = json.dumps(report, ensure_ascii=False, indent=2)
        if options.result_json:
            options.result_json.write_text(encoded, encoding="utf-8")
        elif sys.stdout:
            print(encoded)
        return 0 if report["ok"] else 1
    if "--ui-smoke" in sys.argv[1:]:
        parser = argparse.ArgumentParser(description="Read-only MITOM UI smoke test")
        parser.add_argument("--ui-smoke", action="store_true")
        parser.add_argument("--result-json", type=Path)
        options = parser.parse_args()
        application = MITOMAdapter()
        application.root.withdraw()
        application.root.update_idletasks()
        fonts = {key: tkfont.Font(application.root, font=application.ui.fonts[key]).actual()
                 for key in ("small", "mono", "button", "body", "body_small")}
        fonts_ok = all(fonts[key]["family"] == CAPTION_FONT_FAMILY
                       and fonts[key]["size"] >= 12 and fonts[key]["weight"] == "normal"
                       for key in ("small", "mono", "button"))
        caption_text = application.ui.primary_button.cget("text")
        tracking_enabled = bool(CAPTION_TRACKING_SEPARATOR)
        tracking_ok = (CAPTION_TRACKING_SEPARATOR in caption_text if tracking_enabled
                       else not any(char in caption_text for char in ("\u2009", "\u200a")))
        report = {"ok": fonts_ok and tracking_ok, "version": backend.APP_VERSION,
                  "title": application.root.title(), "fonts": fonts,
                  "caption_tracking": tracking_enabled,
                  "caption_tracking_verified": tracking_ok,
                  "caption_tracking_px": tkfont.Font(application.root, font=application.ui.fonts["button"]).measure(CAPTION_TRACKING_SEPARATOR),
                  "writes": False, "network": False}
        application.root.destroy()
        encoded = json.dumps(report, ensure_ascii=False, indent=2)
        if options.result_json:
            options.result_json.write_text(encoded, encoding="utf-8")
        elif sys.stdout:
            print(encoded)
        return 0 if fonts_ok and tracking_ok else 1
    if "--open-uri" in sys.argv[1:]:
        if len(sys.argv) != 3 or sys.argv[1] != "--open-uri":
            raise backend.LauncherError("Liên kết launcher không được kèm lệnh cài đặt.")
        backend.validate_open_uri(sys.argv[2])
        sys.argv[:] = sys.argv[:1]
    return backend.main()


if __name__ == "__main__":
    raise SystemExit(main())
