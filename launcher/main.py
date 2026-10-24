"""Desktop/CLI entry point for the MITOM launcher repair."""
from __future__ import annotations

import datetime
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
from mitom_ui import CAPTION_FONT_FAMILY, CAPTION_TRACKING_SEPARATOR, MITOMLauncherUI
from update_discovery import UpdateDiscovery, apply_verified_release, probe_signed_release

_legacy_update_check = backend.LauncherUI._check_updates


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
        super().__init__(*args, **kwargs)
        if {"--ui-smoke", "--self-test", "--verify-only", "--network-smoke", "--help", "-h"}.intersection(sys.argv[1:]):
            return
        self._r4_ui_thread = threading.get_ident()
        self._update_discovery = UpdateDiscovery(
            self.root, lambda: bool(self.busy),
            lambda cancelled: probe_signed_release(backend, cancelled),
            lambda release: apply_verified_release(self, release, _legacy_update_check, backend),
            self._append)
        self.root.bind("<Destroy>", lambda event: self._update_discovery.close()
                       if event.widget is self.root else None, add=True)
        self._update_discovery.start()

    def _build(self):
        self.version_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Ready. Choose an app to install or open.")
        self.update_button = _UpdateAction(self._install)
        self._vector_release = None
        self._vector_release_error = ""
        try:
            preferences = json.loads((backend.launcher_data_root() / "launcher-preferences.json").read_text(encoding="utf-8"))
            language = "vi" if preferences.get("language") == "vi" else "en"
        except (OSError, ValueError, AttributeError):
            language = "en"
        try:
            self._vector_release = vector_app.load_release()
        except (OSError, ValueError, vector_app.VectorAppError) as exc:
            self._vector_release_error = str(exc)
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
        })
        self.progress = self.ui.progress
        self.log = self.ui.log_text
        self.version_var.trace_add("write", lambda *_: self._refresh_state())
        self.status_var.trace_add("write", lambda *_: self._refresh_state())
        self._append("MITOM Launcher " + backend.APP_VERSION)
        if self._vector_release_error:
            self._append("Vector Migic: " + self._vector_release_error)
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
            launcher_version = latest.get("launcher", {}).get("version", "")
            launcher_available = bool(launcher_version) and backend.version_tuple(launcher_version) > backend.version_tuple(backend.APP_VERSION)
        except backend.LauncherError:
            launcher_version = ""
        roots, _local, _sandbox = installer._roots(None)
        self.ui.set_state({
            "installed": bool(current), "installed_version": current or "",
            "latest_version": release_version,
            "update_available": update_available,
            "launcher_update_available": launcher_available,
            "launcher_latest_version": launcher_version,
            "status": self.status_var.get(),
            "installed_path": str(roots["panel"]) if current else "",
            **vector_app.installed_state(backend.launcher_data_root(), self._vector_release),
        })

    def _install_vector(self):
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
        discovery = getattr(self, "_update_discovery", None)
        if discovery:
            discovery.supersede()
        self.ui.set_state({"checked_at": "Checked " + datetime.datetime.now().strftime("%H:%M")})
        return super()._check_updates()

    def _open_cache(self):
        cache = backend.launcher_data_root() / "Downloads"
        if cache.is_dir():
            os.startfile(str(cache))
        else:
            self.status_var.set("Bộ nhớ đệm sẽ xuất hiện sau lần tải đầu tiên.")


# Keep the original read-only --self-test / --verify-only CLI and error handling.
backend.LauncherUI = MITOMAdapter


def main():
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
