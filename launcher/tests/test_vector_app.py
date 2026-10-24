from pathlib import Path
import hashlib
import json
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import vector_app
from mitom_ui import catalogue_entries, catalogue_selection, MITOMLauncherUI


def sha(value):
    return hashlib.sha256(value).hexdigest()


class VectorFixture(unittest.TestCase):
    def setUp(self):
        # Windows AppContainer's redirected Temp rejects directory renames;
        # exercise the real transaction in this writable workspace instead.
        workspace_build = Path(__file__).resolve().parents[2] / "build"
        workspace_build.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="vector-launcher-test-", dir=workspace_build)
        self.root = Path(self.temporary.name)
        self.bundle = self.root / "bundle"
        self.bundle.mkdir()
        self.machine = self.root / "machine"
        self.panel = self.machine / "PhotoshopPanel" / "personal.json"
        self.panel.parent.mkdir(parents=True)
        self.panel.write_bytes(b"do-not-change-panel-or-private-state")
        self.files = {"VectorMigic.exe": b"standalone-test-fixture", "_internal/core.dll": b"dependency"}
        manifest = {"schema": 1, "app_id": "vector-migic", "version": "1.0.0", "entry": "VectorMigic.exe",
                    "files": [{"path": name, "size": len(content), "sha256": sha(content)}
                              for name, content in self.files.items()]}
        self.manifest = json.dumps(manifest).encode()
        self.archive = self.bundle / "app.zip"
        self.write_archive()
        self.release = {"schema": 1, "app_id": "vector-migic", "version": "1.0.0", "entry": "VectorMigic.exe",
                        "manifest_sha256": sha(self.manifest), "archive": {"bundled": "app.zip",
                        "size": self.archive.stat().st_size, "sha256": sha(self.archive.read_bytes())}}
        self.release_path = self.bundle / "vector-release.json"
        self.release_path.write_text(json.dumps(self.release), encoding="utf-8")

    def tearDown(self):
        self.temporary.cleanup()

    def write_archive(self, extra=None):
        with zipfile.ZipFile(self.archive, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("vector-app.json", self.manifest)
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
        release = vector_app.load_release(self.release_path)
        self.assertFalse(vector_app.installed_state(self.machine, release)["vector_installed"])
        with mock.patch("urllib.request.build_opener", side_effect=AssertionError("offline install used network")):
            result = vector_app.install_app(self.machine, release, bundle_root=self.bundle)
        state = vector_app.installed_state(self.machine, release)
        self.assertTrue(state["vector_installed"])
        self.assertEqual(state["vector_version"], "1.0.0")
        self.assertEqual(Path(result["path"]) / release["entry"], Path(state["vector_path"]) / release["entry"])
        self.assertEqual(self.panel.read_bytes(), b"do-not-change-panel-or-private-state")

    def test_tampered_runtime_disables_open_and_repair_restores_it(self):
        result = vector_app.install_app(self.machine, self.release, bundle_root=self.bundle)
        binary = Path(result["path"]) / "VectorMigic.exe"
        binary.write_bytes(b"tampered")
        self.assertFalse(vector_app.installed_state(self.machine, self.release)["vector_installed"])
        with mock.patch("subprocess.Popen") as launch:
            with self.assertRaises(vector_app.VectorAppError):
                vector_app.open_app(self.machine, self.release)
            launch.assert_not_called()
        vector_app.install_app(self.machine, self.release, bundle_root=self.bundle)
        self.assertEqual(binary.read_bytes(), self.files["VectorMigic.exe"])

    def test_zip_hash_mismatch_preserves_existing_install(self):
        result = vector_app.install_app(self.machine, self.release, bundle_root=self.bundle)
        original = (Path(result["path"]) / "VectorMigic.exe").read_bytes()
        self.archive.write_bytes(b"changed archive")
        with self.assertRaises(vector_app.VectorAppError):
            vector_app.install_app(self.machine, self.release, bundle_root=self.bundle)
        self.assertEqual((Path(result["path"]) / "VectorMigic.exe").read_bytes(), original)

    def test_pinned_archive_still_rejects_traversal_and_extra_members(self):
        for name in ("../escape.exe", "_internal/extra.dll"):
            with self.subTest(name=name):
                self.write_archive((name, b"unexpected"))
                self.repin_archive()
                with self.assertRaises(vector_app.VectorAppError):
                    vector_app.install_app(self.machine, self.release, bundle_root=self.bundle)
                self.assertFalse((self.root / "escape.exe").exists())

    def test_changed_internal_manifest_is_rejected(self):
        self.manifest += b" "
        self.write_archive()
        self.repin_archive()
        with self.assertRaises(vector_app.VectorAppError):
            vector_app.install_app(self.machine, self.release, bundle_root=self.bundle)

    def test_open_runs_only_verified_standalone_executable(self):
        result = vector_app.install_app(self.machine, self.release, bundle_root=self.bundle)
        with mock.patch("subprocess.Popen") as launch:
            vector_app.open_app(self.machine, self.release)
        arguments, options = launch.call_args
        self.assertEqual(arguments[0], [str(Path(result["path"]) / "VectorMigic.exe")])
        self.assertEqual(options["cwd"], result["path"])
        self.assertEqual(options["env"]["PYINSTALLER_RESET_ENVIRONMENT"], "1")

    def test_verified_previous_release_offers_update_and_never_opens_old_runtime(self):
        previous = dict(self.release)
        vector_app.install_app(self.machine, previous, bundle_root=self.bundle)
        release = self.next_release()
        with mock.patch.object(vector_app, "TRUSTED_PREVIOUS_RELEASES", (previous,)):
            state = vector_app.installed_state(self.machine, release)
            self.assertTrue(state["vector_installed"])
            self.assertTrue(state["vector_update_available"])
            self.assertFalse(state["vector_current_verified"])
            self.assertEqual(state["vector_version"], "1.0.0")
            self.assertEqual(state["vector_latest_version"], "1.0.1")
            counts, updates = catalogue_selection(state, "updates")
            self.assertEqual(counts["updates"], 1)
            self.assertEqual([item["id"] for item in updates], ["vector"])
            with mock.patch("subprocess.Popen") as launch:
                with self.assertRaises(vector_app.VectorAppError):
                    vector_app.open_app(self.machine, release)
                launch.assert_not_called()
            vector_app.install_app(self.machine, release, bundle_root=self.bundle)
            current = vector_app.installed_state(self.machine, release)
            self.assertTrue(current["vector_current_verified"])
            self.assertFalse(current["vector_update_available"])
            self.assertEqual(current["vector_version"], "1.0.1")

    def test_changed_or_extra_files_in_previous_release_are_not_trusted(self):
        previous = dict(self.release)
        installed = vector_app.install_app(self.machine, previous, bundle_root=self.bundle)
        release = self.next_release()
        target = Path(installed["path"])
        with mock.patch.object(vector_app, "TRUSTED_PREVIOUS_RELEASES", (previous,)):
            for change in ("tampered", "extra"):
                with self.subTest(change=change):
                    if change == "tampered":
                        (target / "VectorMigic.exe").write_bytes(b"changed")
                    else:
                        (target / "VectorMigic.exe").write_bytes(self.files["VectorMigic.exe"])
                        (target / "unknown.dll").write_bytes(b"extra")
                    state = vector_app.installed_state(self.machine, release)
                    self.assertFalse(state["vector_installed"])
                    self.assertFalse(state["vector_update_available"])

    def test_verified_1_0_1_offers_1_0_2_update_with_old_release_also_present(self):
        oldest = dict(self.release)
        oldest_install = vector_app.install_app(self.machine, oldest, bundle_root=self.bundle)
        previous = dict(self.next_release("1.0.1"))
        previous_install = vector_app.install_app(self.machine, previous, bundle_root=self.bundle)
        release = self.next_release("1.0.2")
        history = (previous, oldest)
        with mock.patch.object(vector_app, "TRUSTED_PREVIOUS_RELEASES", history):
            state = vector_app.installed_state(self.machine, release)
            self.assertEqual(state["vector_version"], "1.0.1")
            self.assertEqual(state["vector_latest_version"], "1.0.2")
            self.assertTrue(state["vector_update_available"])
            self.assertFalse(state["vector_current_verified"])
            self.assertEqual(state["vector_path"], previous_install["path"])
            counts, apps = catalogue_selection(state, "updates")
            self.assertEqual(counts, {"all": 2, "installed": 1, "updates": 1, "available": 1})
            self.assertEqual([app["id"] for app in apps], ["vector"])
            ui = object.__new__(MITOMLauncherUI)
            ui.state = state
            self.assertEqual(ui._vector_key(), "install_vector")
            with mock.patch("subprocess.Popen") as launch:
                with self.assertRaises(vector_app.VectorAppError):
                    vector_app.open_app(self.machine, release)
                launch.assert_not_called()
            vector_app.install_app(self.machine, release, bundle_root=self.bundle)
            current = vector_app.installed_state(self.machine, release)
            self.assertEqual(current["vector_version"], "1.0.2")
            self.assertTrue(current["vector_current_verified"])
            self.assertFalse(current["vector_update_available"])
            self.assertEqual(catalogue_selection(current, "updates")[1], [])
            vector_app.verify_install(Path(previous_install["path"]), previous)
            vector_app.verify_install(Path(oldest_install["path"]), oldest)
            self.assertEqual(self.panel.read_bytes(), b"do-not-change-panel-or-private-state")

    def test_corrupt_current_runtime_requires_repair_without_claiming_installed(self):
        release = self.next_release()
        installed = vector_app.install_app(self.machine, release, bundle_root=self.bundle)
        (Path(installed["path"]) / "VectorMigic.exe").write_bytes(b"corrupt current runtime")
        state = vector_app.installed_state(self.machine, release)
        self.assertTrue(state["vector_repair_required"])
        self.assertFalse(state["vector_current_verified"])
        self.assertFalse(state["vector_installed"])
        ui = object.__new__(MITOMLauncherUI)
        ui.state = state
        self.assertEqual(ui._vector_key(), "install_vector")

    def test_verified_1_0_2_offers_1_0_3_update_and_preserves_previous_runtime(self):
        pinned = next(row for row in vector_app.TRUSTED_PREVIOUS_RELEASES if row['version']=='1.0.2')
        self.assertEqual(pinned["version"], "1.0.2")
        self.assertEqual(pinned["manifest_sha256"],
                         "abe3f2a20a15b8b5481e6aaea52bd8194fbd9d4201a9d5e900bbddca284872ac")
        previous = dict(self.next_release("1.0.2"))
        installed = vector_app.install_app(self.machine, previous, bundle_root=self.bundle)
        original = (Path(installed["path"]) / "VectorMigic.exe").read_bytes()
        release = self.next_release("1.0.3")
        with mock.patch.object(vector_app, "TRUSTED_PREVIOUS_RELEASES", (previous,)):
            state = vector_app.installed_state(self.machine, release)
            self.assertEqual(state["vector_version"], "1.0.2")
            self.assertEqual(state["vector_latest_version"], "1.0.3")
            self.assertTrue(state["vector_update_available"])
            self.assertFalse(state["vector_current_verified"])
            self.assertEqual([app["id"] for app in catalogue_selection(state, "updates")[1]], ["vector"])
            ui = object.__new__(MITOMLauncherUI)
            ui.state = state
            self.assertEqual(ui._vector_key(), "install_vector")
            with mock.patch("subprocess.Popen") as launch:
                with self.assertRaises(vector_app.VectorAppError):
                    vector_app.open_app(self.machine, release)
                launch.assert_not_called()
            vector_app.install_app(self.machine, release, bundle_root=self.bundle)
            current = vector_app.installed_state(self.machine, release)
            self.assertTrue(current["vector_current_verified"])
            self.assertFalse(current["vector_update_available"])
            self.assertEqual(current["vector_version"], "1.0.3")
            vector_app.verify_install(Path(installed["path"]), previous)
            self.assertEqual((Path(installed["path"]) / "VectorMigic.exe").read_bytes(), original)
            self.assertEqual(self.panel.read_bytes(), b"do-not-change-panel-or-private-state")

    def test_verified_1_0_3_offers_1_0_4_update_and_preserves_previous_runtime(self):
        pinned = vector_app.TRUSTED_PREVIOUS_RELEASES[0]
        self.assertEqual(pinned["version"], "1.0.3")
        self.assertEqual(pinned["manifest_sha256"],
                         "c53b390ec119ff9c26fe4d2e58b02fe987fb843cc902f37daedc7f8f77af734e")
        previous = dict(self.next_release("1.0.3"))
        installed = vector_app.install_app(self.machine, previous, bundle_root=self.bundle)
        original = (Path(installed["path"]) / "VectorMigic.exe").read_bytes()
        release = self.next_release("1.0.4")
        with mock.patch.object(vector_app, "TRUSTED_PREVIOUS_RELEASES", (previous,)):
            state = vector_app.installed_state(self.machine, release)
            self.assertEqual(state["vector_version"], "1.0.3")
            self.assertEqual(state["vector_latest_version"], "1.0.4")
            self.assertTrue(state["vector_update_available"])
            self.assertFalse(state["vector_current_verified"])
            self.assertEqual([app["id"] for app in catalogue_selection(state, "updates")[1]], ["vector"])
            ui = object.__new__(MITOMLauncherUI)
            ui.state = state
            self.assertEqual(ui._vector_key(), "install_vector")
            with mock.patch("subprocess.Popen") as launch:
                with self.assertRaises(vector_app.VectorAppError):
                    vector_app.open_app(self.machine, release)
                launch.assert_not_called()
            vector_app.install_app(self.machine, release, bundle_root=self.bundle)
            current = vector_app.installed_state(self.machine, release)
            self.assertTrue(current["vector_current_verified"])
            self.assertFalse(current["vector_update_available"])
            self.assertEqual(current["vector_version"], "1.0.4")
            vector_app.verify_install(Path(installed["path"]), previous)
            self.assertEqual((Path(installed["path"]) / "VectorMigic.exe").read_bytes(), original)
            self.assertEqual(self.panel.read_bytes(), b"do-not-change-panel-or-private-state")


class CatalogueTests(unittest.TestCase):
    def test_independent_install_counts_filters_and_search(self):
        state = {"installed": True, "installed_version": "2026.10.24.2", "vector_installed": False,
                 "vector_latest_version": "1.0.0", "vector_available": True}
        counts, apps = catalogue_selection(state)
        self.assertEqual(counts, {"all": 2, "installed": 1, "updates": 0, "available": 1})
        self.assertEqual([item["id"] for item in apps], ["panel", "vector"])
        self.assertEqual([item["id"] for item in catalogue_selection(state, "installed")[1]], ["panel"])
        self.assertEqual([item["id"] for item in catalogue_selection(state, "available", "svg")[1]], ["vector"])
        self.assertEqual(catalogue_selection(state, "installed", "svg")[1], [])
        state.update(vector_installed=True, update_available=True)
        self.assertEqual(catalogue_selection(state, "updates")[0]["updates"], 1)
        self.assertEqual([item["id"] for item in catalogue_selection(state, "updates")[1]], ["panel"])

    def test_adding_vector_keeps_existing_panel_primary_actions(self):
        ui = object.__new__(MITOMLauncherUI)
        ui.actions = {"install_update": lambda: None}
        for installed, update, panel_key in ((False, False, "install"), (True, False, "open_photoshop"),
                                               (True, True, "install_update")):
            ui.state = {"installed": installed, "update_available": update, "vector_installed": not installed,
                        "vector_current_verified": not installed}
            self.assertEqual(ui._primary_key(), panel_key)
            self.assertEqual(ui._vector_key(), "open_vector" if not installed else "install_vector")
        self.assertEqual(len(catalogue_entries(ui.state)), 2)


if __name__ == "__main__":
    unittest.main()
