from __future__ import annotations

import hashlib
import io
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import backend
import installer
import panel_payload


class Response(io.BytesIO):
    def __init__(self, data, status=200, headers=None):
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data))}
        self.headers.update(headers or {})
        self.status = status


def artifact(data, name="test"):
    return {"url": "https://example.invalid/" + name, "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest()}


class PanelPayloadTests(unittest.TestCase):
    def setUp(self):
        parent = Path(__file__).resolve().parents[2] / "build/mitom-launcher/tests"
        parent.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="payload-", dir=parent)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_new_release_reuses_cryptographically_verified_parts(self):
        data = [b"same large runtime part", b"changed panel tail"]
        rows = [artifact(value, str(i)) for i, value in enumerate(data)]
        release = {"version": "2026.10.25.1", "core": {"parts": rows, "size": sum(map(len, data))}}
        responses = {row["url"]: value for row, value in zip(rows, data)}
        with mock.patch.object(backend, "open_https", side_effect=lambda url: Response(responses[url])) as opened:
            first = panel_payload.obtain_parts(backend, release, self.root)
        self.assertEqual(opened.call_count, 2)
        changed = b"newer panel tail"
        updated = {"version": "2026.10.26.1", "core": {"parts": [rows[0], artifact(changed, "new")],
                                                             "size": len(data[0]) + len(changed)}}
        with mock.patch.object(backend, "open_https", return_value=Response(changed)) as opened:
            second = panel_payload.obtain_parts(backend, updated, self.root)
        self.assertEqual(opened.call_count, 1)
        self.assertEqual(second[0], first[0])
        self.assertEqual(second[1].read_bytes(), changed)

    def test_legacy_cache_reused_but_same_size_corruption_is_rejected(self):
        value = b"trusted runtime part"
        row = artifact(value)
        legacy = self.root / "2026.10.23.2"
        legacy.mkdir()
        path = legacy / "core.part001"
        path.write_bytes(value)
        release = {"version": "2026.10.25.1", "core": {"parts": [row], "size": len(value)}}
        with mock.patch.object(backend, "open_https", side_effect=AssertionError("cache should avoid network")):
            self.assertEqual(panel_payload.obtain_parts(backend, release, self.root), [path])
        path.write_bytes(b"x" * len(value))
        with mock.patch.object(backend, "open_https", return_value=Response(value)) as opened:
            repaired = panel_payload.obtain_parts(backend, release, self.root)
        self.assertEqual(opened.call_count, 1)
        self.assertEqual(repaired[0].read_bytes(), value)

    def test_complete_panel_retry_reuses_parts_bundles_and_models_without_network(self):
        from test_backend import lean_release_with_external_artifacts
        release, responses, bundle_pins, model_pins, files = lean_release_with_external_artifacts()
        machine = self.root / "machine"
        cache = self.root / "cache"
        with mock.patch.object(backend, "EXTERNAL_BUNDLES", bundle_pins), \
                mock.patch.object(backend, "EXTERNAL_MODELS", model_pins):
            with mock.patch.object(backend, "open_https", side_effect=lambda url: Response(responses[url])) as opened:
                first = backend.obtain_core(release, cache_root=cache, base_root=machine)
            self.assertEqual(opened.call_count, 13)
            self.assertTrue(backend.cleanup_core_extraction(first))
            with mock.patch.object(backend, "open_https", side_effect=AssertionError("retry must use verified cache")):
                second = backend.obtain_core(release, cache_root=cache, base_root=machine)
            self.assertNotEqual(first, second)
            for name, data in files.items():
                self.assertEqual((second / name).read_bytes(), data)
            self.assertEqual(installer.install(second, base_root=machine)["status"], "installed")
            self.assertTrue(backend.cleanup_core_extraction(second))

    def test_duplicate_parts_download_once_without_destination_race(self):
        value = b"repeated content"
        row = artifact(value)
        release = {"version": "2026.10.25.1", "core": {"parts": [row, row], "size": len(value) * 2}}
        with mock.patch.object(backend, "open_https", return_value=Response(value)) as opened:
            result = panel_payload.obtain_parts(backend, release, self.root)
        self.assertEqual(opened.call_count, 1)
        self.assertEqual(result[0], result[1])

    def test_parallel_downloads_are_bounded_and_keep_input_order(self):
        active, peak = 0, 0
        lock = threading.Lock()

        def download(row, folder, filename, progress=None):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.03 * (4 - int(filename) % 4))
            if progress:
                progress(row["size"], row["size"])
            with lock:
                active -= 1
            return folder / filename

        requests = [(artifact(b"value", str(i)), self.root, str(i)) for i in range(8)]
        progress = []
        with mock.patch.object(backend, "download_artifact", side_effect=download):
            result = panel_payload._parallel_downloads(backend, requests, lambda a, b: progress.append((a, b)))
        self.assertEqual(peak, 3)
        self.assertEqual(result, [self.root / str(i) for i in range(8)])
        self.assertEqual(progress[-1], (40, 40))
        self.assertTrue(all(0 <= a <= b for a, b in progress))

    def test_failure_joins_workers_and_retains_verified_completed_cache(self):
        rows = [artifact(b"good", "good"), artifact(b"bad", "bad")]
        def response(url):
            if url.endswith("bad"):
                raise backend.LauncherError("pin failure")
            time.sleep(0.03)
            return Response(b"good")
        requests = [(row, self.root, str(i)) for i, row in enumerate(rows)]
        with mock.patch.object(backend, "open_https", side_effect=response):
            with self.assertRaisesRegex(backend.LauncherError, "pin failure"):
                panel_payload._parallel_downloads(backend, requests)
        self.assertEqual((self.root / "0").read_bytes(), b"good")
        self.assertFalse((self.root / "1").exists())

    def test_large_artifact_resumes_timeout_prefix_and_verifies_final_hash(self):
        value = b"0123456789abcdefghijklmnopqrstuvwxyz"
        row = artifact(value)
        offsets = []
        class Interrupted(Response):
            def read(self, _amount=-1):
                if self.tell() == 0:
                    return super().read(10)
                raise TimeoutError("interrupted after prefix")
        def opened(_backend, _url, offset):
            offsets.append(offset)
            if offset == 0:
                return Interrupted(value)
            return Response(value[offset:], 206,
                            {"Content-Range": f"bytes {offset}-{len(value)-1}/{len(value)}"})
        with mock.patch.object(panel_payload, "RESUME_THRESHOLD", 1), \
                mock.patch.object(panel_payload, "_open_artifact_stream", side_effect=opened), \
                mock.patch.object(panel_payload.time, "sleep"):
            result = backend.download_artifact(row, self.root, "model.bin")
        self.assertEqual(offsets, [0, 10])
        self.assertEqual(result.read_bytes(), value)
        self.assertEqual(list(self.root.glob("*.part")), [])

    def test_large_artifact_range_ignored_restarts_instead_of_appending(self):
        value = b"complete trusted model"
        row = artifact(value)
        partial = panel_payload._partial_path(installer, self.root, "model.bin", row["sha256"])
        partial.write_bytes(value[:5])
        with mock.patch.object(panel_payload, "_open_artifact_stream", return_value=Response(value)) as opened:
            result = panel_payload.download_large_artifact(backend, row, self.root, "model.bin")
        self.assertEqual(opened.call_args.args[-1], 5)
        self.assertEqual(result.read_bytes(), value)

    def test_large_artifact_rejects_wrong_range_and_never_retries_integrity_errors(self):
        value = b"complete trusted model"
        row = artifact(value)
        partial = panel_payload._partial_path(installer, self.root, "model.bin", row["sha256"])
        partial.write_bytes(value[:5])
        wrong = Response(value[5:], 206, {"Content-Range": f"bytes 4-{len(value)-1}/{len(value)}"})
        with mock.patch.object(panel_payload, "_open_artifact_stream", return_value=wrong) as opened:
            with self.assertRaisesRegex(backend.LauncherError, "sai vùng"):
                panel_payload.download_large_artifact(backend, row, self.root, "model.bin")
        self.assertEqual(opened.call_count, 1)
        self.assertFalse(partial.exists())
        self.assertFalse((self.root / "model.bin").exists())

    def test_large_artifact_keeps_partial_after_retry_limit_and_resumes_next_click(self):
        value = b"complete trusted model"
        row = artifact(value)
        partial = panel_payload._partial_path(installer, self.root, "model.bin", row["sha256"])
        partial.write_bytes(value[:5])
        with mock.patch.object(panel_payload, "_open_artifact_stream", side_effect=TimeoutError("offline")) as opened, \
                mock.patch.object(panel_payload.time, "sleep"):
            with self.assertRaisesRegex(backend.LauncherError, "3 lần"):
                panel_payload.download_large_artifact(backend, row, self.root, "model.bin")
        self.assertEqual(opened.call_count, 3)
        self.assertEqual(partial.read_bytes(), value[:5])
        remainder = Response(value[5:], 206, {"Content-Range": f"bytes 5-{len(value)-1}/{len(value)}"})
        with mock.patch.object(panel_payload, "_open_artifact_stream", return_value=remainder):
            self.assertEqual(panel_payload.download_large_artifact(backend, row, self.root, "model.bin").read_bytes(), value)

    def test_large_artifact_rejects_final_hash_and_bad_length(self):
        value = b"good model"
        row = artifact(value)
        for response in [Response(b"x" * len(value)), Response(value, headers={"Content-Length": "1"})]:
            with self.subTest(response=response), \
                    mock.patch.object(panel_payload, "_open_artifact_stream", return_value=response) as opened:
                with self.assertRaises(backend.LauncherError):
                    panel_payload.download_large_artifact(backend, row, self.root, "model.bin")
                self.assertEqual(opened.call_count, 1)
                self.assertFalse((self.root / "model.bin").exists())
                self.assertEqual(list(self.root.glob("*.part")), [])

    def test_large_artifact_never_appends_to_shared_hardlink_partial(self):
        value = b"good model"
        row = artifact(value)
        original = self.root / "private-kept.bin"
        original.write_bytes(b"private data")
        partial = self.root / (row["sha256"] + ".part")
        try:
            os.link(original, partial)
        except PermissionError:
            self.skipTest("This sandbox cannot create hardlinks; the real-user test covers this guard.")
        with mock.patch.object(panel_payload, "_open_artifact_stream", side_effect=AssertionError("network must not start")):
            with self.assertRaisesRegex(installer.InstallError, "unshared"):
                panel_payload.download_large_artifact(backend, row, self.root, "model.bin")
        self.assertEqual(original.read_bytes(), b"private data")
        self.assertEqual(partial.read_bytes(), b"private data")

    def test_process_poll_is_scoped_and_forced_at_receipt_commit(self):
        scans = []
        state = {"next": 0.0, "sandbox": False}
        installer._install_poll.state = state
        try:
            with mock.patch.object(installer, "_original_closed", side_effect=lambda sandbox: scans.append(sandbox)), \
                    mock.patch.object(installer.time, "monotonic", side_effect=[10.0, 10.1, 10.3, 10.31]), \
                    mock.patch.object(installer, "_original_atomic"):
                installer._closed(False)
                installer._closed(False)
                installer._closed(False)
                installer._atomic(self.root / "install-receipt.json", b"new", None)
            self.assertEqual(scans, [False, False, False])
        finally:
            del installer._install_poll.state
        with mock.patch.object(installer, "_original_closed") as check:
            installer._closed(False)
            installer._closed(False)
        self.assertEqual(check.call_count, 2)

    def test_photoshop_start_before_receipt_uses_original_rollback_boundary(self):
        from test_backend import make_payload
        source = self.root / "source"
        make_payload(source)
        machine = self.root / "machine"
        calls = 0
        def closed(_sandbox):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise installer.InstallError("Photoshop started")
        with mock.patch.object(installer, "_original_closed", side_effect=closed), \
                mock.patch.object(installer.time, "monotonic", return_value=10.0):
            with self.assertRaisesRegex(installer.InstallError, "Photoshop started"):
                installer.install(source, base_root=machine)
        self.assertFalse(hasattr(installer._install_poll, "state"))
        roots, _, _ = installer._roots(machine)
        self.assertFalse((roots["panel"] / "index.html").exists())
        self.assertFalse((roots["launcher"] / "install-receipt.json").exists())
        self.assertFalse((roots["launcher"] / "pending-transaction.json").exists())


if __name__ == "__main__":
    unittest.main()
