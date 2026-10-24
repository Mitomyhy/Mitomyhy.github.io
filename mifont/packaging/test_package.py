from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

spec = importlib.util.spec_from_file_location("vector_portable_package", Path(__file__).with_name("package.py"))
package = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package)


class PortablePackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "launcher").mkdir()
        self.app = self.root / "app"
        self.app.mkdir()
        (self.app / package.ENTRY).write_bytes(b"MZ-test-fixture")
        (self.app / "_internal").mkdir()
        (self.app / "_internal" / "engine.dat").write_bytes(b"local-runtime")
        self.smoke = self.root / "smoke.json"
        self.smoke.write_text(json.dumps({"ok": True, "version": package.VERSION}), encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def build(self):
        return package.create_package(self.app, self.root / "out", self.root, self.smoke)

    def test_archive_matches_all_runtime_files_and_launcher_index(self):
        report = self.build()
        self.assertTrue(report["ok"])
        archive = self.root / "out" / package.ARCHIVE
        manifest = package.validate_archive(archive)
        self.assertEqual({row["path"] for row in manifest["files"]}, {package.ENTRY, "_internal/engine.dat"})
        self.assertEqual(archive.read_bytes(), (self.root / "launcher" / "apps" / package.ARCHIVE).read_bytes())
        index = json.loads((self.root / "launcher" / "mifont-release.json").read_bytes())
        self.assertEqual(index["archive"]["sha256"], report["sha256"])
        self.assertEqual(index["archive"]["bundled"], "apps/" + package.ARCHIVE)

    def test_safe_paths(self):
        for value in ("", "../a", "/a", "a/../b", "a\\b", "a//b", "C:/app.exe", "./a"):
            with self.subTest(path=value):
                self.assertFalse(package.safe_member(value))
        self.assertTrue(package.safe_member("_internal/reportlab/fonts/a.ttf"))

    def test_mutable_cache_rejected(self):
        (self.app / "cache").mkdir()
        (self.app / "cache" / "user-image.png").write_bytes(b"private")
        with self.assertRaisesRegex(ValueError, "Mutable or private"):
            self.build()

    def test_missing_executable_rejected(self):
        (self.app / package.ENTRY).unlink()
        with self.assertRaisesRegex(ValueError, "executable is missing"):
            self.build()

    def test_failed_runtime_not_packaged(self):
        self.smoke.write_text('{"ok":false}', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "must pass its self-test"):
            self.build()
        self.assertFalse((self.root / "launcher" / "mifont-release.json").exists())

    def test_wrong_runtime_version_not_packaged(self):
        self.smoke.write_text(json.dumps({"ok": True, "version": "0.0.0"}), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "version does not match"):
            self.build()
        self.assertFalse((self.root / "launcher" / "mifont-release.json").exists())

    def test_file_tampering_detected(self):
        self.build()
        original = self.root / "out" / package.ARCHIVE
        damaged = self.root / "tampered.zip"
        with zipfile.ZipFile(original) as src, zipfile.ZipFile(damaged, "w") as dst:
            for info in src.infolist():
                dst.writestr(info, b"tampered" if info.filename == "_internal/engine.dat" else src.read(info.filename))
        with self.assertRaisesRegex(ValueError, "Content hash mismatch"):
            package.validate_archive(damaged)

    def test_extra_zip_member_rejected(self):
        self.build()
        archive = self.root / "out" / package.ARCHIVE
        with zipfile.ZipFile(archive, "a") as output:
            output.writestr("surprise.txt", "unlisted")
        with self.assertRaisesRegex(ValueError, "membership"):
            package.validate_archive(archive)

    def test_traversal_zip_member_rejected(self):
        self.build()
        archive = self.root / "out" / package.ARCHIVE
        with zipfile.ZipFile(archive, "a") as output:
            output.writestr("../escape.txt", "no")
        with self.assertRaisesRegex(ValueError, "Unsafe ZIP record"):
            package.validate_archive(archive)


if __name__ == "__main__":
    unittest.main()
