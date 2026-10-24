"""Isolated transport and real signature tests for launcher-only discovery.

The historical fixture is already-public signed metadata. Its old public key
is injected only to exercise the real verifier; production loads the separate
bundled4096-bit key and never reads this fixture or the panel verification key.
"""
import base64
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import backend
import launcher_channel as channel


class Response(io.BytesIO):
    def __init__(self, data, url=channel.LAUNCHER_FEED_URL, length=None):
        super().__init__(data)
        self.url = url
        self.headers = {} if length is None else {"Content-Length": str(length)}

    def geturl(self):
        return self.url


class LauncherChannelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = (Path(__file__).parent / "fixtures/public-signed-release.json").read_bytes()
        cls.public_key = backend.load_public_key()
        cls.authenticated = backend.verify_release_envelope(json.loads(cls.raw), cls.public_key)

    def setUp(self):
        self.key_loader = mock.patch.object(channel, "load_launcher_public_key", return_value=self.public_key)
        self.key_loader.start()
        self.addCleanup(self.key_loader.stop)

    def probe(self, raw=None, response=None, cancelled=None):
        response = response if response is not None else Response(self.raw if raw is None else raw)
        opener = mock.Mock()
        opener.open.return_value = response
        with mock.patch.object(channel.urllib.request, "build_opener", return_value=opener), \
                mock.patch.object(backend, "fetch_release", side_effect=AssertionError("panel feed was read")), \
                mock.patch.object(backend, "load_public_key", side_effect=AssertionError("panel key was read")):
            result = channel.probe_launcher_release(backend, cancelled)
        return result, opener

    def test_real_signature_projects_only_launcher_and_uses_canonical_feed(self):
        result, opener = self.probe()
        self.assertEqual(result, {"launcher": self.authenticated["launcher"]})
        self.assertEqual(set(result), {"launcher"})
        self.assertEqual(set(result["launcher"]), {"version", "url", "size", "sha256"})
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, channel.LAUNCHER_FEED_URL)
        self.assertEqual(opener.open.call_args.kwargs["timeout"], 12)
        result["launcher"]["version"] = "changed only in the returned projection"
        self.assertNotEqual(result["launcher"]["version"], self.authenticated["launcher"]["version"])

    def test_real_verifier_rejects_payload_tampering_without_unsigned_fallback(self):
        envelope = json.loads(self.raw)
        payload = json.loads(base64.b64decode(envelope["payload"]))
        payload["launcher"]["size"] += 1
        envelope["payload"] = base64.b64encode(json.dumps(payload).encode()).decode()
        with self.assertRaises(backend.LauncherError):
            self.probe(json.dumps(envelope).encode())

    def test_real_verifier_rejects_signature_and_server_key_replacement(self):
        envelope = json.loads(self.raw)
        signature = bytearray(base64.b64decode(envelope["signature"]))
        signature[0] ^= 1
        envelope["signature"] = base64.b64encode(signature).decode()
        envelope["public_key"] = self.public_key
        with self.assertRaises(backend.LauncherError):
            self.probe(json.dumps(envelope).encode())

    def test_pre_cancelled_probe_performs_no_key_load_or_network(self):
        cancelled = threading.Event()
        cancelled.set()
        with mock.patch.object(channel, "load_launcher_public_key") as loaded, \
                mock.patch.object(channel.urllib.request, "build_opener") as opened:
            with self.assertRaisesRegex(RuntimeError, "cancelled"):
                channel.probe_launcher_release(backend, cancelled)
        loaded.assert_not_called()
        opened.assert_not_called()

    def test_cancellation_after_read_never_verifies_or_applies_metadata(self):
        cancelled = threading.Event()
        response = Response(self.raw)
        original = response.read1
        def read(size):
            cancelled.set()
            return original(size)
        response.read1 = read
        with mock.patch.object(backend, "verify_release_envelope") as verifier:
            with self.assertRaisesRegex(RuntimeError, "cancelled"):
                self.probe(response=response, cancelled=cancelled)
        verifier.assert_not_called()

    def test_declared_and_streamed_feed_limits(self):
        for response in (Response(b"{}", length=backend.FEED_LIMIT + 1),
                         Response(b"x" * (backend.FEED_LIMIT + 1))):
            with self.subTest(length=response.headers), \
                    mock.patch.object(backend, "verify_release_envelope") as verifier:
                with self.assertRaisesRegex(backend.LauncherError, "size"):
                    self.probe(response=response)
                verifier.assert_not_called()

    def test_elapsed_deadline_rejects_a_completed_read(self):
        with mock.patch.object(channel.time, "monotonic", side_effect=[0, 0, 13]), \
                mock.patch.object(backend, "verify_release_envelope") as verifier:
            with self.assertRaisesRegex(RuntimeError, "timed out"):
                self.probe()
        verifier.assert_not_called()

    def test_final_redirect_and_redirect_handler_cannot_change_channel(self):
        for target in ("http://mitomyhy.github.io/releases/launcher-stable.json",
                       "https://example.invalid/releases/launcher-stable.json",
                       "https://mitomyhy.github.io/releases/latest-download.json"):
            with self.subTest(target=target), self.assertRaises(backend.LauncherError):
                self.probe(response=Response(self.raw, url=target))
        with mock.patch.object(channel.urllib.request, "build_opener") as factory:
            factory.return_value.open.return_value = Response(self.raw)
            channel.probe_launcher_release(backend)
            handler = factory.call_args.args[0]
            with self.assertRaises(backend.LauncherError):
                handler.redirect_request(None, None, 302, "Found", {}, "https://example.invalid/metadata")

    def test_malformed_unsigned_and_duplicate_json_are_hard_failures(self):
        bodies = (b"{}", b'{"launcher":{"version":"2099.01.01.1"}}',
                  b'{"schema":1,"schema":1}', b"NaN", b"not-json")
        for raw in bodies:
            with self.subTest(raw=raw), self.assertRaises((backend.LauncherError, ValueError, TypeError)):
                self.probe(raw)

    def test_even_verified_panel_fields_never_cross_the_projection(self):
        malicious = dict(self.authenticated)
        malicious["version"] = "2099.01.01.1"
        malicious["core"] = {"url": "https://example.invalid/panel"}
        malicious["external_bundles"] = [{"id": "untrusted-panel-change"}]
        with mock.patch.object(backend, "verify_release_envelope", return_value=malicious):
            result, _opener = self.probe()
        self.assertEqual(result, {"launcher": self.authenticated["launcher"]})


class BundledLauncherKeyTests(unittest.TestCase):
    def test_separate_bundled_key_is_required_and_old_panel_key_is_not_a_fallback(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            old = Path(backend.resource_root()) / "release-public-key.json"
            (root / "release-public-key.json").write_bytes(old.read_bytes())
            with mock.patch.object(channel.sys, "_MEIPASS", str(root), create=True):
                with self.assertRaises(ValueError):
                    channel.load_launcher_public_key()
                (root / channel.PUBLIC_KEY_FILENAME).write_bytes(old.read_bytes())
                with self.assertRaises(ValueError):
                    channel.load_launcher_public_key()
                public = {"key_id": "synthetic-public-shape-only", "n": "a" + "0" * 1022 + "1", "e": 65537}
                (root / channel.PUBLIC_KEY_FILENAME).write_text(json.dumps(public))
                self.assertEqual(channel.load_launcher_public_key(), public)


if __name__ == "__main__":
    unittest.main()
