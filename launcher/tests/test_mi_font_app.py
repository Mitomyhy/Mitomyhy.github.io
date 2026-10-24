from pathlib import Path
import hashlib
import json
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import mi_font_app
from mitom_ui import catalogue_entries, catalogue_selection, MITOMLauncherUI


def sha(value):
    return hashlib.sha256(value).hexdigest()


class MiFontFixture(unittest.TestCase):
    def setUp(self):
        # Windows AppContainer's redirected Temp rejects directory renames;
        # exercise the real transaction in this writable workspace instead.
        workspace_build = Path(__file__).resolve().parents[2] / "build"
        workspace_build.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="mifont-launcher-test-", dir=workspace_build)
        self.root = Path(self.temporary.name)
        self.bundle = self.root / "bundle"
        self.bundle.mkdir()
        self.machine = self.root / "machine"
        self.panel = self.machine / "PhotoshopPanel" / "personal.json"
        self.panel.parent.mkdir(parents=True)
        self.panel.write_bytes(b"do-not-change-panel-or-private-state")
        self.files = {"MiFont.exe": b"standalone-test-fixture", "_internal/core.dll": b"dependency"}
        manifest = {"schema": 1, "app_id": "mifont", "version": "1.0.0", "entry": "MiFont.exe",
                    "files": [{"path": name, "size": len(content), "sha256": sha(content)}
                              for name, content in self.files.items()]}
        self.manifest = json.dumps(manifest).encode()
        self.archive = self.bundle / "app.zip"
        self.write_archive()
        self.release = {"schema": 1, "app_id": "mifont", "version": "1.0.0", "entry": "MiFont.exe",
                        "manifest_sha256": sha(self.manifest), "archive": {"bundled": "app.zip",
                        "size": self.archive.stat().st_size, "sha256": sha(self.archive.read_bytes())}}
        self.release_path = self.bundle / "mifont-release.json"
        self.release_path.write_text(json.dumps(self.release), encoding="utf-8")

    def tearDown(self):
        self.temporary.cleanup()

    def write_archive(self, extra=None):
        with zipfile.ZipFile(self.archive, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("mifont-app.json", self.manifest)
            for name, content in self.files.items():
                archive.writestr(name, content)
            if extra:
                archive.writestr(*extra)

    def repin_archive(self):
        self.release["archive"].update(size=self.archive.stat().st_size, sha256=sha(self.archive.read_bytes()))

    def next_release(self, version="1.0.1"):
        manifest = json.loads(self.manifest)
        manifest["version"] = version
        self.manifest = json.dumps(manifest).encode()
        self.release = {**self.release, "version": version, "manifest_sha256": sha(self.manifest),
                        "archive": dict(self.release["archive"])}
        self.write_archive()
        self.repin_archive()
        return self.release

    def test_offline_install_verifies_runtime_and_preserves_panel(self):
        release = mi_font_app.load_release(self.release_path)
        self.assertFalse(mi_font_app.installed_state(self.machine, release)["mifont_installed"])
        with mock.patch("urllib.request.build_opener", side_effect=AssertionError("offline install used network")):
            result = mi_font_app.install_app(self.machine, release, bundle_root=self.bundle)
        state = mi_font_app.installed_state(self.machine, release)
        self.assertTrue(state["mifont_installed"])
        self.assertEqual(state["mifont_version"], "1.0.0")
        self.assertEqual(Path(result["path"]) / release["entry"], Path(state["mifont_path"]) / release["entry"])
        self.assertEqual(self.panel.read_bytes(), b"do-not-change-panel-or-private-state")

    def test_tampered_runtime_disables_open_and_repair_restores_it(self):
        result = mi_font_app.install_app(self.machine, self.release, bundle_root=self.bundle)
        binary = Path(result["path"]) / "MiFont.exe"
        binary.write_bytes(b"tampered")
        self.assertFalse(mi_font_app.installed_state(self.machine, self.release)["mifont_installed"])
        with mock.patch("subprocess.Popen") as launch:
            with self.assertRaises(mi_font_app.MiFontAppError):
                mi_font_app.open_app(self.machine, self.release)
            launch.assert_not_called()
        mi_font_app.install_app(self.machine, self.release, bundle_root=self.bundle)
        self.assertEqual(binary.read_bytes(), self.files["MiFont.exe"])

    def test_zip_hash_mismatch_preserves_existing_install(self):
        result = mi_font_app.install_app(self.machine, self.release, bundle_root=self.bundle)
        original = (Path(result["path"]) / "MiFont.exe").read_bytes()
        self.archive.write_bytes(b"changed archive")
        with self.assertRaises(mi_font_app.MiFontAppError):
            mi_font_app.install_app(self.machine, self.release, bundle_root=self.bundle)
        self.assertEqual((Path(result["path"]) / "MiFont.exe").read_bytes(), original)

    def test_pinned_archive_still_rejects_traversal_and_extra_members(self):
        for name in ("../escape.exe", "_internal/extra.dll"):
            with self.subTest(name=name):
                self.write_archive((name, b"unexpected"))
                self.repin_archive()
                with self.assertRaises(mi_font_app.MiFontAppError):
                    mi_font_app.install_app(self.machine, self.release, bundle_root=self.bundle)
                self.assertFalse((self.root / "escape.exe").exists())

    def test_changed_internal_manifest_is_rejected(self):
        self.manifest += b" "
        self.write_archive()
        self.repin_archive()
        with self.assertRaises(mi_font_app.MiFontAppError):
            mi_font_app.install_app(self.machine, self.release, bundle_root=self.bundle)

    def test_open_runs_only_verified_standalone_executable(self):
        result = mi_font_app.install_app(self.machine, self.release, bundle_root=self.bundle)
        with mock.patch("subprocess.Popen") as launch:
            mi_font_app.open_app(self.machine, self.release)
        arguments, options = launch.call_args
        self.assertEqual(arguments[0], [str(Path(result["path"]) / "MiFont.exe")])
        self.assertEqual(options["cwd"], result["path"])
        self.assertEqual(options["env"]["PYINSTALLER_RESET_ENVIRONMENT"], "1")

    def test_verified_previous_release_offers_update_and_never_opens_old_runtime(self):
        pinned=mi_font_app.TRUSTED_PREVIOUS_RELEASES[0]
        self.assertEqual(pinned['version'],'1.0.0')
        self.assertEqual(pinned['manifest_sha256'],'9881638902890043fc979166637d2a23181166b6b016af93718e841054b532ee')
        previous = dict(self.release)
        installed=mi_font_app.install_app(self.machine, previous, bundle_root=self.bundle)
        release = self.next_release()
        with mock.patch.object(mi_font_app, "TRUSTED_PREVIOUS_RELEASES", (previous,)):
            state = mi_font_app.installed_state(self.machine, release)
            self.assertTrue(state["mifont_installed"])
            self.assertTrue(state["mifont_update_available"])
            self.assertFalse(state["mifont_current_verified"])
            self.assertEqual(state["mifont_version"], "1.0.0")
            self.assertEqual(state["mifont_latest_version"], "1.0.1")
            counts, updates = catalogue_selection(state, "updates")
            self.assertEqual(counts["updates"], 1)
            self.assertEqual([item["id"] for item in updates], ["mifont"])
            ui=object.__new__(MITOMLauncherUI);ui.state=state
            self.assertEqual(ui._mifont_key(),'install_mifont')
            old_executable=(Path(state['mifont_path'])/'MiFont.exe').read_bytes()
            with mock.patch("subprocess.Popen") as launch:
                with self.assertRaises(mi_font_app.MiFontAppError):
                    mi_font_app.open_app(self.machine, release)
                launch.assert_not_called()
            mi_font_app.install_app(self.machine, release, bundle_root=self.bundle)
            current = mi_font_app.installed_state(self.machine, release)
            self.assertTrue(current["mifont_current_verified"])
            self.assertFalse(current["mifont_update_available"])
            self.assertEqual(current["mifont_version"], "1.0.1")
            mi_font_app.verify_install(Path(installed['path']),previous)
            self.assertEqual((Path(installed['path'])/'MiFont.exe').read_bytes(),old_executable)

    def test_changed_or_extra_files_in_previous_release_are_not_trusted(self):
        previous = dict(self.release)
        installed = mi_font_app.install_app(self.machine, previous, bundle_root=self.bundle)
        release = self.next_release()
        target = Path(installed["path"])
        with mock.patch.object(mi_font_app, "TRUSTED_PREVIOUS_RELEASES", (previous,)):
            for change in ("tampered", "extra"):
                with self.subTest(change=change):
                    if change == "tampered":
                        (target / "MiFont.exe").write_bytes(b"changed")
                    else:
                        (target / "MiFont.exe").write_bytes(self.files["MiFont.exe"])
                        (target / "unknown.dll").write_bytes(b"extra")
                    state = mi_font_app.installed_state(self.machine, release)
                    self.assertFalse(state["mifont_installed"])
                    self.assertFalse(state["mifont_update_available"])


    def test_corrupt_current_runtime_requires_repair_without_claiming_installed(self):
        release = self.next_release()
        installed = mi_font_app.install_app(self.machine, release, bundle_root=self.bundle)
        (Path(installed["path"]) / "MiFont.exe").write_bytes(b"corrupt current runtime")
        state = mi_font_app.installed_state(self.machine, release)
        self.assertTrue(state["mifont_repair_required"])
        self.assertFalse(state["mifont_current_verified"])
        self.assertFalse(state["mifont_installed"])
        ui = object.__new__(MITOMLauncherUI)
        ui.state = state
        self.assertEqual(ui._mifont_key(), "install_mifont")






if __name__ == "__main__":
    unittest.main()
