from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import shutil
import sys
import tarfile
import tempfile
import unittest
from unittest import mock
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import backend
import installer

XML = b'<ExtensionManifest ExtensionBundleId="com.uoy.orderpanel" />'


def make_payload(root: Path, version="2026.10.20.3", body=b"new panel"):
    files = {"panel/CSXS/manifest.xml": XML, "panel/index.html": body}
    rows = []
    for name, data in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        rows.append({"path": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    manifest = json.dumps({"schema": 1, "version": version, "files": rows}).encode()
    (root / "payload-manifest.json").write_bytes(manifest)
    files["payload-manifest.json"] = manifest
    return files


def release_for(files, version="2026.10.20.3"):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    content = stream.getvalue()
    artifact = {"url": "https://example.invalid/fixture.bin", "size": len(content),
                "sha256": hashlib.sha256(content).hexdigest()}
    return {"version": version, "core": {"parts": [artifact], "size": len(content),
            "sha256": artifact["sha256"],
            "unpacked_size": sum(len(data) for name, data in files.items() if name != "payload-manifest.json")}}, content


def lean_release_with_external_artifacts(version="2026.10.20.3"):
    """Tiny production-shaped core: nine parts, two bundles and two models.

    Only test pins and network bytes are replaced. The real inventory mapping,
    archive/hash checks, extraction and sandbox installer all run unchanged.
    """
    files = {"panel/CSXS/manifest.xml": XML, "panel/index.html": b"lean fixture panel"}
    bundle_bytes, bundle_pins = {}, {}
    for key, pin in backend.EXTERNAL_BUNDLES.items():
        names = ("torch/__init__.py", "torch/lib/torch_cpu.dll") if key == "torch-cpu" else sorted(backend.CODEX_BUNDLE_FILES)
        members = {name: ("fixture " + key + ":" + name).encode() for name in names}
        for name, data in members.items():
            files[backend.bundle_destination(key, name)] = data
        stream = io.BytesIO()
        if key == "torch-cpu":
            with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for name, data in members.items():
                    archive.writestr(name, data)
        else:
            with tarfile.open(fileobj=stream, mode="w:gz") as archive:
                for name, data in members.items():
                    info = tarfile.TarInfo(name)
                    info.size = len(data)
                    archive.addfile(info, io.BytesIO(data))
        content = stream.getvalue()
        bundle_bytes[pin["url"]] = content
        bundle_pins[key] = {"url": pin["url"], "size": len(content),
                            "sha256": hashlib.sha256(content).hexdigest()}
    model_pins = {}
    for path, pin in backend.EXTERNAL_MODELS.items():
        data = ("fixture model:" + path).encode()
        files[path] = data
        model_pins[path] = {"url": pin["url"], "size": len(data),
                            "sha256": hashlib.sha256(data).hexdigest()}
        bundle_bytes[pin["url"]] = data
    rows = [{"path": path, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
            for path, data in files.items()]
    manifest = {"schema": 1, "version": version, "files": rows}
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            if name.startswith("panel/"):
                archive.writestr(name, data)
        archive.writestr("payload-manifest.json", json.dumps(manifest).encode())
    core_bytes = stream.getvalue()
    part_size = (len(core_bytes) + 8) // 9
    parts = []
    for index in range(9):
        content = core_bytes[index * part_size:(index + 1) * part_size]
        url = f"https://example.invalid/core.part{index + 1:03d}"
        parts.append({"url": url, "size": len(content), "sha256": hashlib.sha256(content).hexdigest()})
        bundle_bytes[url] = content
    release = {"version": version, "core": {"parts": parts, "size": len(core_bytes),
                "sha256": hashlib.sha256(core_bytes).hexdigest(), "files": len(rows),
                "unpacked_size": sum(row["size"] for row in rows)},
               "external_bundles": [{"id": key, **pin} for key, pin in bundle_pins.items()],
               "external_files": [{"path": path, **pin} for path, pin in model_pins.items()]}
    return release, bundle_bytes, bundle_pins, model_pins, files


class Response(io.BytesIO):
    def __init__(self, data):
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data))}


class BackendTests(unittest.TestCase):
    def setUp(self):
        # Put every test mutation inside this workspace, including machine roots.
        work = Path(__file__).resolve().parents[2] / "build/mitom-launcher/tests"
        work.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="case-", dir=work)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.machine = self.root / "machine"

    def test_original_overlap_guard_reproduces_reported_error(self):
        old_stage = self.machine / "Local/UOY/Launcher/Downloads/2026.10.20.3/core-verified-fixture"
        make_payload(old_stage)
        with self.assertRaisesRegex(installer.InstallError, "Payload and installed directories must be separate"):
            installer.install(old_stage, base_root=self.machine, dry_run=True)

    def test_lean_core_bundle_inventory_downloads_and_installs_in_sandbox(self):
        release, responses, bundle_pins, model_pins, files = lean_release_with_external_artifacts()
        progress = []
        # The live 2026.10.20.3 release uses this lean-core layout. Test-only
        # pins keep archives small while retaining every verification step.
        with mock.patch.object(backend, "EXTERNAL_BUNDLES", bundle_pins), \
                mock.patch.object(backend, "EXTERNAL_MODELS", model_pins), \
                mock.patch.object(backend, "open_https", side_effect=lambda url: Response(responses[url])) as opened:
            extraction = backend.obtain_core(release, cache_root=self.root / "cache", base_root=self.machine,
                                               progress=lambda done, total: progress.append((done, total)))
        self.assertEqual(opened.call_count, 13)  # Nine core parts + two bundles + two models.
        self.assertTrue(progress)
        for path, data in files.items():
            self.assertEqual((extraction / path).read_bytes(), data)
        roots, _, _ = installer._roots(self.machine)
        for target in roots.values():
            self.assertNotIn(target, extraction.parents)
            self.assertNotIn(extraction, target.parents)
        result = installer.install(extraction, base_root=self.machine, dry_run=False)
        self.assertEqual(result["status"], "installed")
        for path, data in files.items():
            category, relative = path.split("/", 1)
            self.assertEqual((roots[category] / relative).read_bytes(), data)
        self.assertTrue(backend.cleanup_core_extraction(extraction))

    def test_staged_install_update_rollback_preserves_private_data(self):
        roots, _, _ = installer._roots(self.machine)
        installed = roots["panel"]
        (installed / "CSXS").mkdir(parents=True)
        (installed / "CSXS/manifest.xml").write_bytes(XML)
        (installed / "index.html").write_bytes(b"old panel")
        private = installed / "local-config.json"
        private_bytes = b'{"exportRoot":"D:/fixture-output","account":"fixture"}'
        private.write_bytes(private_bytes)
        profile = roots["core"] / "profiles/fixture/account.txt"
        profile.parent.mkdir(parents=True)
        profile.write_bytes(b"FIXTURE-ACCOUNT")
        source = self.root / "source"
        files = make_payload(source)
        release, content = release_for(files)
        cache = self.root / "cache"
        with mock.patch.object(backend, "open_https", side_effect=lambda _url: Response(content)):
            extraction = backend.obtain_core(release, cache_root=cache, base_root=self.machine)
        for target in roots.values():
            self.assertNotIn(target, extraction.parents)
            self.assertNotIn(extraction, target.parents)
        self.assertEqual(extraction.parent.parent.name, "InstallStaging")
        result = installer.install(extraction, base_root=self.machine, dry_run=False)
        self.assertEqual(result["status"], "installed")
        self.assertEqual((installed / "index.html").read_bytes(), b"new panel")
        self.assertEqual(private.read_bytes(), private_bytes)
        self.assertEqual(profile.read_bytes(), b"FIXTURE-ACCOUNT")
        self.assertTrue(backend.cleanup_core_extraction(extraction))
        self.assertFalse(extraction.exists())
        # A second release updates in place and its rollback restores the first.
        files = make_payload(source, version="2026.10.20.4", body=b"second panel")
        release, content = release_for(files, version="2026.10.20.4")
        with mock.patch.object(backend, "open_https", side_effect=lambda _url: Response(content)):
            extraction = backend.obtain_core(release, cache_root=cache, base_root=self.machine)
        update = installer.install(extraction, base_root=self.machine, dry_run=False)
        self.assertEqual((installed / "index.html").read_bytes(), b"second panel")
        self.assertEqual(private.read_bytes(), private_bytes)
        self.assertEqual(profile.read_bytes(), b"FIXTURE-ACCOUNT")
        rolled_back = installer.rollback(Path(update["backup"]), base_root=self.machine)
        self.assertEqual(rolled_back["status"], "rolled_back")
        self.assertEqual((installed / "index.html").read_bytes(), b"new panel")
        self.assertEqual(private.read_bytes(), private_bytes)
        self.assertEqual(profile.read_bytes(), b"FIXTURE-ACCOUNT")
        self.assertTrue(backend.cleanup_core_extraction(extraction))

    def test_download_retries_timeout_and_reuses_verified_cache(self):
        content = b"verified fixture"
        artifact = {"url": "https://example.invalid/fixture", "size": len(content),
                    "sha256": hashlib.sha256(content).hexdigest()}
        destination = self.root / "downloads"
        with mock.patch.object(backend, "open_https", side_effect=[TimeoutError("fixture"), Response(content)]) as opened, \
                mock.patch.object(backend.time, "sleep"):
            path = backend.download_artifact(artifact, destination, "fixture.bin")
        self.assertEqual(opened.call_count, 2)
        self.assertEqual(path.read_bytes(), content)
        self.assertEqual(list(destination.glob("*.part")), [])
        with mock.patch.object(backend, "open_https", side_effect=AssertionError("network must not be used")):
            self.assertEqual(backend.download_artifact(artifact, destination, "fixture.bin"), path)

    def test_download_never_retries_corrupt_bytes(self):
        good = b"good"
        artifact = {"url": "https://example.invalid/fixture", "size": len(good),
                    "sha256": hashlib.sha256(good).hexdigest()}
        with mock.patch.object(backend, "open_https", return_value=Response(b"evil")) as opened:
            with self.assertRaises(backend.LauncherError):
                backend.download_artifact(artifact, self.root / "downloads", "fixture.bin")
        self.assertEqual(opened.call_count, 1)
        self.assertEqual(list((self.root / "downloads").iterdir()), [])

    def test_failed_download_keeps_other_verified_parts(self):
        destination = self.root / "downloads"
        destination.mkdir()
        previous = destination / "previous.bin"
        previous.write_bytes(b"previous verified part")
        artifact = {"url": "https://example.invalid/fixture", "size": 4,
                    "sha256": hashlib.sha256(b"good").hexdigest()}
        with mock.patch.object(backend, "open_https", side_effect=TimeoutError("fixture")) as opened, \
                mock.patch.object(backend.time, "sleep"):
            with self.assertRaisesRegex(backend.LauncherError, "3"):
                backend.download_artifact(artifact, destination, "fixture.bin")
        self.assertEqual(opened.call_count, 3)
        self.assertEqual(previous.read_bytes(), b"previous verified part")
        self.assertEqual(list(destination.glob("*.part")), [])

    def test_cleanup_refuses_unowned_directories(self):
        other = self.root / "2026.10.20.3/core-verified-someone-else"
        other.mkdir(parents=True)
        (other / "keep.txt").write_text("keep")
        self.assertFalse(backend.cleanup_core_extraction(other))
        self.assertTrue((other / "keep.txt").is_file())

    def test_protocol_only_accepts_open_catalogue_links(self):
        for uri in ("uoy://", "uoy://launch", "uoy://launcher/", "uoy:///launch"):
            self.assertTrue(backend.validate_open_uri(uri))
        for uri in ("https://example.invalid", "uoy://install", "uoy://launch?install=1", "uoy://launch#install",
                    'uoy://launch" --verify-only', "uoy://account@launch"):
            with self.assertRaises(backend.LauncherError):
                backend.validate_open_uri(uri)

    def test_preflight_handles_fresh_unicode_and_long_profile_without_creating_it(self):
        release, _ = release_for({"panel/CSXS/manifest.xml": XML, "panel/index.html": b"fixture"})
        profile = self.root / ("profile-" + "x" * 80) / ("Tiếng Việt-" + "y" * 80) / ("z" * 80)
        environment = {"APPDATA": str(profile / "Roaming"), "LOCALAPPDATA": str(profile / "Local"),
                       "USERPROFILE": str(profile)}
        with mock.patch.dict(backend.os.environ, environment):
            report = backend.download_space_preflight(
                release, backend.launcher_data_root() / "Downloads" / release["version"], include_install=True)
        self.assertTrue(report["volumes"])
        self.assertFalse(profile.exists())


if __name__ == "__main__":
    unittest.main()
