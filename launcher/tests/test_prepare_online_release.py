"""Release consistency checks: no network, key access, or real installation."""
from pathlib import Path
import copy
import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

LAUNCHER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LAUNCHER))
spec = importlib.util.spec_from_file_location("prepare_online_release", LAUNCHER / "publishing/prepare_online_release.py")
publisher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publisher)


class OnlineReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=LAUNCHER.parents[0] / "build", prefix="online-release-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.baseline = self.root / "baseline"
        (self.baseline / "releases").mkdir(parents=True)
        fixture = (LAUNCHER / "tests/fixtures/public-signed-release.json").read_bytes()
        for name in ("stable.json", "launcher-stable.json"):
            (self.baseline / "releases" / name).write_bytes(fixture)
        publisher.write_json(self.baseline / "releases/latest-download.json", {"version": "old", "components": ["Preserved component"]})
        config = """window.MITOM_CONFIG = {
  releaseUrl: 'https://example.invalid/old',
  launcherVersion: '2026.10.20.1',
  launcherDownloadUrl: 'https://example.invalid/old.exe',
  apps: [{
    id: 'photoshop-panel',
    version: '2026.10.20.1',
    detail: { en: 'Preserve this' }
  }, {
    id: 'mifont',
    githubUrl: 'https://example.invalid/mifont',
    version: '1.0.0',
    detail: { en: 'Preserve MiFont description' }
  }, {
    id: 'vector-migic',
    githubUrl: 'https://example.invalid/vector',
    version: '1.0.0',
    detail: { en: 'Preserve Vector description' }
  }]
};
"""
        (self.baseline / "site-config.js").write_text(config, encoding="utf-8")
        (self.baseline / "index.html").write_text('<html><script src="site-config.js?v=old"></script></html>', encoding="utf-8")
        self.version = "2026.10.30.7"
        self.artifact = self.root / "checked.exe"
        self.artifact.write_bytes(b"MZ-isolated-test-executable")
        self.receipt = self.root / "runtime-smoke.json"
        publisher.write_json(self.receipt, {"ok": True, "version": self.version,
                                             "artifact_sha256": publisher.sha(self.artifact),
                                             "artifact_size": self.artifact.stat().st_size})
        self.descriptors = {}
        for app_id in publisher.MODULES:
            self.descriptors[app_id] = self.make_app(app_id, "1.0.1")
        # Both signed fixture copies use the historical Panel key. This replaces
        # only the test's independent key source; real RSA verification stays on.
        patch = mock.patch.object(publisher.launcher_channel, "load_launcher_public_key", return_value=publisher.backend.load_public_key())
        patch.start()
        self.addCleanup(patch.stop)

    def make_app(self, app_id, version):
        folder = self.root / app_id / version
        folder.mkdir(parents=True)
        entry = "MiFont.exe" if app_id == "mifont" else "VectorMigic.exe"
        manifest = {"schema": 1, "app_id": app_id, "version": version, "entry": entry, "files": []}
        content = publisher.json_bytes(manifest)
        archive = folder / (app_id + "-" + version + ".zip")
        with zipfile.ZipFile(archive, "w") as package:
            package.writestr(publisher.MANIFEST_NAMES[app_id], content)
        descriptor = {"schema": 1, "app_id": app_id, "version": version, "entry": entry,
                      "manifest_sha256": hashlib.sha256(content).hexdigest(),
                      "archive": {"url": "https://mitomyhy.github.io/releases/" + app_id + "/" + version + "/" + archive.name,
                                  "size": archive.stat().st_size, "sha256": publisher.sha(archive)}}
        path = folder / publisher.DESCRIPTOR_NAMES[app_id]
        publisher.write_json(path, descriptor)
        return path

    def prepare(self, **changes):
        args = dict(baseline_dir=self.baseline, artifact_path=self.artifact, version=self.version,
                    mifont_release=self.descriptors["mifont"], vector_release=self.descriptors["vector-migic"],
                    self_test_receipt=self.receipt, output=self.root / "stage")
        args.update(changes)
        return publisher.prepare(**args)

    def test_unsigned_preview_keeps_originals_and_synchronizes_all_versions(self):
        before = {path: path.read_bytes() for path in self.baseline.rglob("*") if path.is_file()}
        with mock.patch.object(publisher, "_sign", side_effect=AssertionError("Signing requires explicit --sign")):
            report = self.prepare()
        self.assertTrue(report["ok"])
        self.assertFalse(report["ready_to_publish"])
        stage = self.root / "stage"
        self.assertFalse((stage / "publish-manifest.json").exists())
        self.assertFalse((stage / "site/releases/stable.json").exists())
        self.assertFalse((stage / "site/releases/launcher-stable.json").exists())
        launcher = publisher.read_json(stage / "payloads/launcher.json")
        apps = publisher.read_json(stage / "payloads/apps.json")
        metadata = publisher.read_json(stage / "site/releases/latest-download.json")
        self.assertEqual(launcher["launcher"], report["launcher"])
        self.assertEqual({key: metadata[key] for key in ("version", "url", "size", "sha256")}, report["launcher"])
        self.assertTrue(report["self_test_artifact_bound"])
        self.assertEqual(metadata["panel_version"], launcher["version"])
        self.assertEqual(metadata["components"], ["Preserved component"])
        self.assertEqual(apps["purpose"], "mitom-app-catalogue")
        config = (stage / "site/site-config.js").read_text(encoding="utf-8")
        for app_id in publisher.MODULES:
            self.assertEqual(publisher._card_version(config, app_id), "1.0.1")
            relative = apps["apps"][app_id]["archive"]["url"].split("github.io/")[1]
            self.assertTrue((stage / "site" / relative).is_file())
        self.assertIn('launcherVersion: "' + self.version + '"', config)
        self.assertIn("Preserve MiFont description", config)
        self.assertIn("site-config.js?v=release-2026-10-30-7", (stage / "site/index.html").read_text())
        self.assertEqual(before, {path: path.read_bytes() for path in before})

    def test_frozen_receipt_version_and_exact_artifact_pins_are_required(self):
        for change in ({"version": "2026.10.30.8"}, {"artifact_sha256": "0" * 64}, {"artifact_size": 999}):
            publisher.write_json(self.receipt, {"ok": True, "version": self.version, **change})
            with self.assertRaisesRegex(ValueError, "receipt"):
                self.prepare()
            self.assertFalse((self.root / "stage").exists())

    def test_wrong_archive_bytes_are_rejected_before_staging(self):
        raw = publisher.read_json(self.descriptors["mifont"])
        raw["archive"]["sha256"] = "0" * 64
        publisher.write_json(self.descriptors["mifont"], raw)
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            self.prepare()
        self.assertFalse((self.root / "stage").exists())

    def test_history_merges_full_descriptors_and_rejects_conflicts_and_rollback(self):
        apps = {app_id: publisher.read_json(path) for app_id, path in self.descriptors.items()}
        old_path = self.make_app("mifont", "1.0.0")
        result = publisher._history(apps, None, [old_path, old_path])
        self.assertEqual([row["version"] for row in result["mifont"]], ["1.0.0"])
        previous = {"apps": copy.deepcopy(apps), "history": result}
        previous["apps"]["mifont"]["manifest_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "Conflicting"):
            publisher._history(apps, previous, [])
        future_path = self.make_app("mifont", "1.0.2")
        with self.assertRaisesRegex(ValueError, "rollback"):
            publisher._history(apps, None, [future_path])

    def test_app_only_release_advances_catalogue_and_reuses_exact_launcher(self):
        artifact = {"version": self.version,
                    "url": "https://mitomyhy.github.io/releases/mitom-launcher/" + self.version + "/MITOM-Launcher-Setup-" + self.version + ".exe",
                    "size": self.artifact.stat().st_size, "sha256": publisher.sha(self.artifact)}
        authenticated = publisher.backend.verify_release_envelope(
            publisher.read_json(self.baseline / "releases/stable.json"), publisher.backend.load_public_key())
        authenticated["launcher"] = artifact
        prior_apps = {app_id: publisher.read_json(self.make_app(app_id, "1.0.0")) for app_id in publisher.MODULES}
        previous = {"version": self.version, "apps": prior_apps,
                    "history": {app_id: [] for app_id in publisher.MODULES}}
        publisher.write_json(self.baseline / "releases/apps-stable.json", {})
        catalogue_version = "2026.10.31.1"
        # This test isolates release selection after signature authentication.
        # The other preview test exercises real signed baseline verification.
        with mock.patch.object(publisher.backend, "verify_release_envelope", return_value=authenticated), \
                mock.patch.object(publisher.app_catalogue, "verify_catalogue", return_value=previous):
            report = self.prepare(catalogue_version=catalogue_version)
        stage = self.root / "stage"
        apps = publisher.read_json(stage / "payloads/apps.json")
        launcher = publisher.read_json(stage / "payloads/launcher.json")
        self.assertEqual(report["version"], self.version)
        self.assertEqual(report["catalogue_version"], catalogue_version)
        self.assertEqual(launcher["launcher"], artifact)
        self.assertEqual(apps["version"], catalogue_version)
        self.assertEqual(apps["apps"]["mifont"]["version"], "1.0.1")
        self.assertEqual(apps["history"]["mifont"][0]["version"], "1.0.0")
        self.assertIn('launcherVersion: "' + self.version + '"', (stage / "site/site-config.js").read_text())
        self.assertIn("site-config.js?v=release-2026-10-31-1", (stage / "site/index.html").read_text())
        relative = artifact["url"].split("github.io/")[1]
        self.assertEqual((stage / "site" / relative).read_bytes(), self.artifact.read_bytes())

    def test_same_launcher_version_cannot_replace_bytes_or_downgrade(self):
        current = {"version": self.version, "url": "https://mitomyhy.github.io/current.exe",
                   "size": 12, "sha256": "a" * 64}
        publisher._check_launcher_artifact(current, {"launcher": current})
        for key, changed in (("url", "https://mitomyhy.github.io/other.exe"), ("size", 13), ("sha256", "b" * 64)):
            with self.subTest(field=key), self.assertRaisesRegex(ValueError, "exact authenticated"):
                publisher._check_launcher_artifact({**current, key: changed}, {"launcher": current})
        with self.assertRaisesRegex(ValueError, "rollback"):
            publisher._check_launcher_artifact({**current, "version": "2026.10.30.6"}, {"launcher": current})
        with self.assertRaisesRegex(ValueError, "disagree"):
            publisher._check_launcher_artifact({**current, "version": "2026.10.30.8"}, {"launcher": current},
                                              {"launcher": {**current, "sha256": "b" * 64}})

    def test_failed_signed_output_never_creates_publish_manifest(self):
        def unsigned_output(_payload, destination):
            publisher.write_json(destination, {"schema": 1, "key_id": "invalid", "payload": "e30=", "signature": ""})
        with mock.patch.object(publisher, "_sign", side_effect=unsigned_output):
            with self.assertRaises(publisher.backend.LauncherError):
                self.prepare(sign=True)
        self.assertFalse((self.root / "stage/publish-manifest.json").exists())

    def test_existing_output_and_ambiguous_website_are_rejected(self):
        (self.root / "stage").mkdir()
        with self.assertRaisesRegex(ValueError, "never overwritten"):
            self.prepare()
        text = (self.baseline / "site-config.js").read_text()
        with self.assertRaisesRegex(ValueError, "exactly one"):
            publisher.update_site(text + "\nlauncherVersion: 'duplicate',\n", "", self.version, {"url": "x"}, {"version": "x"}, {})


if __name__ == "__main__":
    unittest.main()
