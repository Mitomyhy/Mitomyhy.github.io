from __future__ import annotations

import hashlib
import http.client
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import backend


def http_response(body, declared_size=None):
    """Use the real HTTP parser: read(amount) silently accepts premature EOF."""
    class Socket:
        def makefile(self, *_args):
            length = len(body) if declared_size is None else declared_size
            return io.BytesIO(f"HTTP/1.1 200 OK\r\nContent-Length: {length}\r\n\r\n".encode() + body)
    response = http.client.HTTPResponse(Socket())
    response.begin()
    return response


class VerifiedDownloadTests(unittest.TestCase):
    def setUp(self):
        parent = Path(__file__).resolve().parents[2] / "build/mitom-launcher/tests"
        parent.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="short-read-", dir=parent)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.value = b"abcdefgh"
        self.artifact = {"url": "https://example.invalid/launcher.exe", "size": len(self.value),
                         "sha256": hashlib.sha256(self.value).hexdigest()}

    def test_silent_http_eof_retries_fresh_and_only_publishes_complete_verified_file(self):
        progress = []
        with mock.patch.object(backend, "open_https", side_effect=[http_response(b"abc", 8), http_response(self.value)]) as opened, \
                mock.patch.object(backend.time, "sleep") as backoff:
            result = backend.download_artifact(self.artifact, self.root, "launcher.exe",
                                                lambda done, total: progress.append((done, total)))
        self.assertEqual(opened.call_count, 2)
        backoff.assert_called_once_with(1)
        self.assertEqual(result.read_bytes(), self.value)
        self.assertEqual(progress, [(3, 8), (0, 8), (8, 8)])
        self.assertEqual(list(self.root.glob("*.part")), [])

    def test_three_short_reads_are_bounded_keep_existing_files_and_report_transport_cause(self):
        previous = self.root / "launcher.exe"
        previous.write_bytes(b"previous cached file")
        with mock.patch.object(backend, "open_https", side_effect=lambda _url: http_response(b"abc", 8)) as opened, \
                mock.patch.object(backend.time, "sleep") as backoff:
            with self.assertRaisesRegex(backend.LauncherError, "3") as caught:
                backend.download_artifact(self.artifact, self.root, "launcher.exe")
        self.assertEqual(opened.call_count, 3)
        self.assertEqual(backoff.call_args_list, [mock.call(1), mock.call(2)])
        self.assertIsInstance(caught.exception.__cause__, http.client.IncompleteRead)
        self.assertEqual(caught.exception.__cause__.expected, 5)
        self.assertIn("Received 3 of 8", caught.exception.__cause__.__notes__[0])
        self.assertEqual(previous.read_bytes(), b"previous cached file")
        self.assertEqual(list(self.root.glob("*.part")), [])

    def test_full_sized_hash_mismatch_is_hard_failure_without_retry_or_publication(self):
        with mock.patch.object(backend, "open_https", return_value=http_response(b"12345678")) as opened, \
                mock.patch.object(backend.time, "sleep") as backoff:
            with self.assertRaisesRegex(backend.LauncherError, "SHA-256"):
                backend.download_artifact(self.artifact, self.root, "launcher.exe")
        self.assertEqual(opened.call_count, 1)
        backoff.assert_not_called()
        self.assertFalse((self.root / "launcher.exe").exists())
        self.assertEqual(list(self.root.glob("*.part")), [])

    def test_wrong_declared_size_stays_non_retryable(self):
        with mock.patch.object(backend, "open_https", return_value=http_response(b"abc", 3)) as opened:
            with self.assertRaises(backend.LauncherError):
                backend.download_artifact(self.artifact, self.root, "launcher.exe")
        self.assertEqual(opened.call_count, 1)
        self.assertEqual(list(self.root.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
