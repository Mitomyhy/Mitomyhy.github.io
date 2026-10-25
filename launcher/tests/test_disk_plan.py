from pathlib import Path
import hashlib
import json
import sys
import tempfile
import types
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import backend
import disk_plan
import installer


MIB = 1024 * 1024


class DiskPlanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[2] / "build",
                                                 prefix="disk-plan-")
        self.root = Path(self.temp.name)
        self.cache = self.root / "Downloads" / "2026.10.25.1"
        self.stage = self.root / "Stage"
        self.parts = [{"size": 120 * MIB, "sha256": "a" * 64},
                      {"size": 80 * MIB, "sha256": "b" * 64}]
        self.release = {"version": "2026.10.25.1", "core": {"size": 200 * MIB,
                        "unpacked_size": 1200 * MIB, "parts": self.parts}}
        self.patches = [mock.patch.object(backend, "staging_root", return_value=self.stage),
                        mock.patch.object(backend, "external_files", return_value=[]),
                        mock.patch.object(backend, "external_bundles", return_value=[])]
        for patch in self.patches:
            patch.start()

    def tearDown(self):
        for patch in reversed(self.patches):
            patch.stop()
        self.temp.cleanup()

    def preflight(self, free, include_install=True):
        with mock.patch.object(disk_plan.shutil, "disk_usage", return_value=types.SimpleNamespace(free=free)):
            return backend.download_space_preflight(self.release, self.cache, include_install=include_install)

    def test_cached_large_update_counts_stage_but_not_entire_old_install_receipt(self):
        # The prior installer receipt can cover many GB of unchanged runtimes.
        # Those bytes already exist; the final manifest decides which need backup.
        receipt = self.root / "install-receipt.json"
        receipt.write_text(json.dumps({"files": [{"size": 12 * 1024 * MIB}]}))
        with mock.patch.object(backend, "verified_file", return_value=True), \
                mock.patch.object(installer, "_read_json", side_effect=AssertionError("must not sum the old receipt")):
            result = self.preflight(1600 * MIB)
        self.assertEqual(sum(row["required_bytes"] for row in result["volumes"]), 1528 * MIB)
        self.assertEqual(result["estimated_bytes"]["missing_core_parts"], 0)
        self.assertEqual(result["estimated_bytes"]["assembled_archive"], 200 * MIB)
        self.assertEqual(result["estimated_bytes"]["expanded_stage"], 1200 * MIB)
        self.assertEqual(result["install_check"], "verified-changes-before-install")

    def test_fresh_install_with_missing_cache_fails_before_creating_any_directory(self):
        with mock.patch.object(backend, "verified_file", return_value=False):
            with self.assertRaises(backend.LauncherError):
                self.preflight(1600 * MIB)
        self.assertFalse(self.cache.parent.exists())
        self.assertFalse(self.stage.exists())

    def test_only_exact_hash_cache_credit_and_legacy_version_cache_are_accepted(self):
        content = b"authenticated core part"
        artifact = {"size": len(content), "sha256": hashlib.sha256(content).hexdigest()}
        self.release["core"] = {"size": len(content), "unpacked_size": 100, "parts": [artifact]}
        shared = self.cache.parent / "Parts" / (artifact["sha256"] + ".bin")
        shared.parent.mkdir(parents=True)
        shared.write_bytes(b"x" * len(content))
        result = self.preflight(1000 * MIB)
        self.assertEqual(result["estimated_bytes"]["missing_core_parts"], len(content))
        old = self.cache.parent / "2026.10.23.2" / "core.part001"
        old.parent.mkdir()
        old.write_bytes(content)
        result = self.preflight(1000 * MIB)
        self.assertEqual(result["estimated_bytes"]["missing_core_parts"], 0)
        old.write_bytes(b"z" * len(content))
        result = self.preflight(1000 * MIB)
        self.assertEqual(result["estimated_bytes"]["missing_core_parts"], len(content))

    def test_model_cache_and_verified_installed_models_do_not_count_as_downloads(self):
        models = [{"path": "core/model-a.onnx", "size": 100 * MIB, "sha256": "c" * 64},
                  {"path": "core/model-b.onnx", "size": 200 * MIB, "sha256": "d" * 64}]
        bundle = {"id": "torch-cpu", "size": 50 * MIB, "sha256": "e" * 64}
        installed = self.root / "Installed/model-b.onnx"
        def verified(path, artifact):
            return (artifact in self.parts or path == installed or
                    artifact["sha256"] == models[0]["sha256"] or artifact == bundle)
        with mock.patch.object(backend, "external_files", return_value=models), \
                mock.patch.object(backend, "external_bundles", return_value=[bundle]), \
                mock.patch.object(backend, "installed_model_candidates", return_value=[installed]), \
                mock.patch.object(backend, "verified_file", side_effect=verified):
            result = self.preflight(1600 * MIB)
        self.assertEqual(result["estimated_bytes"]["missing_models"], 0)
        self.assertEqual(result["estimated_bytes"]["missing_bundles"], 0)
        self.assertEqual(result["estimated_bytes"]["expanded_stage"], 1200 * MIB)

    def test_distinct_volumes_are_checked_separately_and_shared_volume_is_summed(self):
        def locate(path):
            return ("stage-volume" if Path(path) == self.stage else "cache-volume", self.root)
        with mock.patch.object(backend, "verified_file", return_value=True), \
                mock.patch.object(installer, "_space_location", side_effect=locate):
            result = self.preflight(1500 * MIB)
        self.assertEqual({row["volume"]: row["required_bytes"] for row in result["volumes"]},
                         {"cache-volume": 328 * MIB, "stage-volume": 1328 * MIB})
        with mock.patch.object(backend, "verified_file", return_value=True), \
                mock.patch.object(installer, "_space_location", return_value=("same-volume", self.root)):
            with self.assertRaises(backend.LauncherError):
                self.preflight(1500 * MIB)

    def test_final_installer_preflight_budgets_only_manifest_changes_and_real_backup(self):
        roots = {name: self.root / name for name in ("launcher", "panel", "coexist")}
        target = self.root / "panel/changed.js"
        target.parent.mkdir()
        target.write_bytes(b"old" * 100)
        changes = [{"entry": {"size": 900}, "target": target, "before": hashlib.sha256(target.read_bytes()).hexdigest()}]
        with mock.patch.object(installer.shutil, "disk_usage", return_value=types.SimpleNamespace(free=40 * MIB)):
            result = installer._space_preflight(changes, roots)
        # Existing implementation already checks exact changed destinations,
        # backup bytes and one atomic staging file; no override is necessary.
        self.assertEqual(result[0]["required_bytes"], installer.SPACE_RESERVE + 900 + 300 + 900)


if __name__ == "__main__":
    unittest.main()
