"""Small pinned fixtures test atomic local-AI setup; no model/network inference."""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

import product_setup as setup


TEST_ROOT = Path(__file__).parent / '.test-tmp'
TEST_ROOT.mkdir(exist_ok=True)


class Response(io.BytesIO):
    status = 200


class InterruptedResponse(Response):
    def __init__(self, content):
        super().__init__(content)
        self.reads = 0

    def read(self, count=-1):
        self.reads += 1
        if self.reads > 1:
            raise ConnectionResetError('fixture network interruption')
        return super().read(count)


class FakeOpener:
    def __init__(self, payloads):
        self.payloads = payloads
        self.urls = []
        self.response_class = Response

    def open(self, request, timeout):
        self.urls.append(request.full_url)
        if request.data is not None:
            raise AssertionError('Setup must never upload a request body')
        if not request.full_url.startswith('https://huggingface.co/unsloth/Qwen3.5-0.8B-GGUF/resolve/'):
            raise AssertionError('Unexpected setup source')
        return self.response_class(self.payloads[Path(urlsplit(request.full_url).path).name])


class ProductSetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=TEST_ROOT)
        self.root = Path(self.temp.name)
        self.stub = self.root / 'bundled-template'
        self.stub.mkdir()
        self.destination = self.root / 'installed-runtime'
        self.cancel = self.root / 'cancel'
        self.binary_path = 'bin/b10883/llama-mtmd-cli.exe'
        self.binary_bytes = b'MZ-unit-fixture-not-executed\0' + b'x' * 180000
        source = self.stub / self.binary_path
        source.parent.mkdir(parents=True)
        source.write_bytes(self.binary_bytes)
        rows = [{'path': self.binary_path, 'size': len(self.binary_bytes),
                 'sha256': hashlib.sha256(self.binary_bytes).hexdigest()}]
        self.manifest = {'binaryFiles': rows, 'fixture': 'Not an actual runtime/model'}
        (self.stub / 'runtime-template.json').write_text(json.dumps(self.manifest), encoding='utf-8')
        pins_digest = hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        self.weights = {'Qwen3.5-0.8B-Q4_K_M.gguf': b'fixture-vision-weights\0' * 6000,
                        'mmproj-F16.gguf': b'fixture-projector-weights\0' * 6000}
        pins = tuple((name, len(content), hashlib.sha256(content).hexdigest()) for name, content in self.weights.items())
        self.opener = FakeOpener(self.weights)
        self.patchers = [patch.object(setup, 'BINARY_MANIFEST_SHA256', pins_digest),
                         patch.object(setup, 'FILES', pins), patch.object(setup, 'CHUNK_BYTES', 65536),
                         patch.object(setup.urllib.request, 'build_opener', return_value=self.opener)]
        for patcher in self.patchers:
            patcher.start()
        self.events = []

    def tearDown(self):
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp.cleanup()

    def run_setup(self, emit=None):
        return setup.prepare_runtime(self.destination, self.stub, self.cancel,
                                     emit or (lambda **event: self.events.append(event)))

    def assert_complete(self):
        self.assertEqual((self.destination / self.binary_path).read_bytes(), self.binary_bytes)
        for name, content in self.weights.items():
            self.assertEqual((self.destination / 'models' / name).read_bytes(), content)
        manifest = json.loads((self.destination / 'runtime-template.json').read_text())
        self.assertEqual(manifest['modelSize'], len(self.weights['Qwen3.5-0.8B-Q4_K_M.gguf']))
        self.assertFalse(list(self.destination.rglob('*.part')))

    def test_success_publishes_only_complete_pinned_files_and_repeat_is_offline(self):
        self.assertEqual(self.run_setup(), self.destination.absolute())
        self.assert_complete()
        self.assertEqual(len(self.opener.urls), 2)
        binary = self.destination / self.binary_path
        previous = binary.stat().st_mtime_ns
        self.opener.urls.clear()
        self.run_setup()
        self.assert_complete()
        self.assertEqual(self.opener.urls, [])
        self.assertEqual(binary.stat().st_mtime_ns, previous, 'Valid existing binaries should not be recopied')

    def test_cancel_before_start_creates_no_installation(self):
        self.cancel.touch()
        with self.assertRaises(InterruptedError):
            self.run_setup()
        self.assertFalse(self.destination.exists())
        self.assertFalse(self.opener.urls)

    def test_binary_read_failure_cannot_publish_partial_and_retry_recovers(self):
        source = self.stub / self.binary_path
        original_open = Path.open

        class BrokenReader:
            def __init__(self, stream):
                self.stream = stream
                self.reads = 0
            def __enter__(self):
                return self
            def __exit__(self, *args):
                self.stream.close()
            def read(self, count):
                self.reads += 1
                if self.reads > 1:
                    raise OSError('fixture interrupted binary copy')
                return self.stream.read(count)

        def failing_open(path, *args, **kwargs):
            stream = original_open(path, *args, **kwargs)
            return BrokenReader(stream) if path == source and args and args[0] == 'rb' else stream

        with patch.object(Path, 'open', failing_open):
            with self.assertRaisesRegex(OSError, 'interrupted binary copy'):
                self.run_setup()
        self.assertFalse((self.destination / self.binary_path).exists())
        self.assertFalse(list(self.destination.rglob('*.part')))
        self.assertFalse((self.destination / 'runtime-template.json').exists())
        self.run_setup()
        self.assert_complete()

    def test_download_disconnect_keeps_no_final_partial_and_retry_works(self):
        self.opener.response_class = InterruptedResponse
        with self.assertRaises(ConnectionResetError):
            self.run_setup()
        self.assertTrue((self.destination / self.binary_path).is_file())
        self.assertFalse((self.destination / 'models/Qwen3.5-0.8B-Q4_K_M.gguf').exists())
        self.assertFalse((self.destination / 'runtime-template.json').exists())
        self.assertFalse(list(self.destination.rglob('*.part')))
        self.opener.response_class = Response
        self.run_setup()
        self.assert_complete()

    def test_cancel_after_download_chunk_does_not_publish_model(self):
        def cancel_on_progress(**event):
            self.cancel.touch()
        with self.assertRaises(InterruptedError):
            self.run_setup(cancel_on_progress)
        self.assertFalse((self.destination / 'models/Qwen3.5-0.8B-Q4_K_M.gguf').exists())
        self.assertFalse(list(self.destination.rglob('*.part')))
        self.cancel.unlink()
        self.run_setup()
        self.assert_complete()

    def test_bad_download_hash_is_never_published(self):
        self.opener.payloads = {**self.weights, 'Qwen3.5-0.8B-Q4_K_M.gguf': b'wrong' * 100}
        with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
            self.run_setup()
        self.assertFalse((self.destination / 'models/Qwen3.5-0.8B-Q4_K_M.gguf').exists())
        self.assertFalse((self.destination / 'runtime-template.json').exists())
        self.assertFalse(list(self.destination.rglob('*.part')))

    def test_modified_existing_binary_is_preserved(self):
        target = self.destination / self.binary_path
        target.parent.mkdir(parents=True)
        target.write_bytes(b'user-modified-existing-binary')
        with self.assertRaisesRegex(ValueError, 'modified; it was preserved'):
            self.run_setup()
        self.assertEqual(target.read_bytes(), b'user-modified-existing-binary')
        self.assertFalse(self.opener.urls)

    def test_modified_existing_model_and_manifest_are_preserved(self):
        target = self.destination / 'models/Qwen3.5-0.8B-Q4_K_M.gguf'
        target.parent.mkdir(parents=True)
        target.write_bytes(b'user-modified-existing-model')
        manifest = self.destination / 'runtime-template.json'
        manifest.write_bytes(b'previous installation manifest')
        with self.assertRaisesRegex(ValueError, 'existing model file differs; it was preserved'):
            self.run_setup()
        self.assertEqual(target.read_bytes(), b'user-modified-existing-model')
        self.assertEqual(manifest.read_bytes(), b'previous installation manifest')

    def test_abandoned_owned_parts_are_removed_but_foreign_names_and_directories_survive(self):
        bin_target = self.destination / self.binary_path
        model_target = self.destination / 'models/Qwen3.5-0.8B-Q4_K_M.gguf'
        owned = [bin_target.with_name(bin_target.name + '.0123456789abcdef.part'),
                 model_target.with_name(model_target.name + '.0123456789abcdef.part'),
                 self.destination / 'runtime-template.json.0123456789abcdef.part']
        for path in owned:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'partial')
        foreign = self.destination / 'models/other-model.gguf.0123456789abcdef.part'
        foreign.write_bytes(b'not ours')
        bad_suffix = model_target.with_name(model_target.name + '.not-a-generated-token.part')
        bad_suffix.write_bytes(b'not ours either')
        directory = model_target.with_name(model_target.name + '.ffffffffffffffff.part')
        directory.mkdir()
        self.run_setup()
        self.assertTrue(all(not path.exists() for path in owned))
        self.assertEqual(foreign.read_bytes(), b'not ours')
        self.assertEqual(bad_suffix.read_bytes(), b'not ours either')
        self.assertTrue(directory.is_dir())

    def test_stale_cleanup_is_bounded_and_preserves_oversized_lookalike(self):
        target = self.destination / 'models/Qwen3.5-0.8B-Q4_K_M.gguf'
        target.parent.mkdir(parents=True)
        parts = [target.with_name(target.name + f'.{index:016x}.part') for index in range(5)]
        for path in parts:
            path.write_bytes(b'old')
        oversized = target.with_name(target.name + '.ffffffffffffffff.part')
        oversized.write_bytes(b'x' * (len(self.weights[target.name]) + 1))
        with patch.object(setup, 'MAX_STALE_PARTS', 2):
            self.run_setup()
        self.assertEqual(sum(not path.exists() for path in parts), 2)
        self.assertTrue(oversized.exists())

    def test_manifest_publish_failure_preserves_previous_then_retry_commits(self):
        self.destination.mkdir()
        manifest = self.destination / 'runtime-template.json'
        old = b'{"previous":"complete manifest preserved until commit"}'
        manifest.write_bytes(old)
        original_replace = Path.replace

        def fail_manifest_commit(source, target):
            if Path(target) == manifest:
                raise OSError('fixture manifest commit interrupted')
            return original_replace(source, target)

        with patch.object(Path, 'replace', fail_manifest_commit):
            with self.assertRaisesRegex(OSError, 'manifest commit interrupted'):
                self.run_setup()
        self.assertEqual(manifest.read_bytes(), old)
        self.assertFalse(list(self.destination.rglob('*.part')))
        self.opener.urls.clear()
        self.run_setup()
        self.assert_complete()
        self.assertFalse(self.opener.urls)

    def test_second_setup_lock_cannot_clean_first_setups_parts(self):
        self.destination.mkdir()
        with setup._setup_lock(self.destination):
            with self.assertRaisesRegex(RuntimeError, 'Another local AI setup is running'):
                self.run_setup()
        self.run_setup()
        self.assert_complete()

    def test_destination_cannot_overlap_template_or_use_network_share(self):
        for destination in (self.stub, self.stub / 'nested'):
            with self.assertRaisesRegex(ValueError, 'separate directories'):
                setup.prepare_runtime(destination, self.stub, self.cancel, lambda **event: None)
        with self.assertRaisesRegex(ValueError, 'network share'):
            setup.plain('\\\\untrusted-server\\runtime')
        self.assertEqual((self.stub / self.binary_path).read_bytes(), self.binary_bytes)

    @unittest.skipUnless(os.name == 'nt', 'Forced-kill containment is reviewed on Windows')
    def test_real_worker_kill_releases_setup_lock_and_retry_recovers_abandoned_download(self):
        from process_limits import start_contained_process
        for name, content in self.weights.items():
            (self.root / name).write_bytes(content)
        config = self.root / 'fixture.json'
        config.write_text(json.dumps({'pins': setup.FILES, 'binary_digest': setup.BINARY_MANIFEST_SHA256,
            'source': str(Path(setup.__file__).parent), 'stub': str(self.stub),
            'destination': str(self.destination), 'folder': str(self.root)}), encoding='utf-8')
        script = self.root / 'interrupted-setup.py'
        script.write_text('''import io,json,pathlib,sys,time
config=json.loads(pathlib.Path(sys.argv[1]).read_text())
sys.path.insert(0,config['source'])
import product_setup as setup
setup.FILES=tuple(tuple(row) for row in config['pins'])
setup.BINARY_MANIFEST_SHA256=config['binary_digest']
setup.CHUNK_BYTES=65536
folder=pathlib.Path(config['folder'])
class Response(io.BytesIO): status=200
class Opener:
 def open(self,request,timeout):
  return Response((folder/request.full_url.rsplit('/',1)[1]).read_bytes())
setup.urllib.request.build_opener=lambda *args: Opener()
def pause_after_first_download_chunk(**event):
 (folder/'download-started').touch()
 while True: time.sleep(.1)
setup.prepare_runtime(pathlib.Path(config['destination']),pathlib.Path(config['stub']),folder/'cancel',pause_after_first_download_chunk)
''', encoding='utf-8')
        process, limits = start_contained_process([sys.executable, '-I', '-B', '-u', str(script), str(config)],
            256 * 1024**2, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            until = time.monotonic() + 6
            while not (self.root / 'download-started').exists() and process.poll() is None and time.monotonic() < until:
                time.sleep(.025)
            self.assertTrue((self.root / 'download-started').exists(), 'Setup fixture never reached its actual download write')
            self.assertEqual(len(list((self.destination / 'models').glob('*.part'))), 1)
        finally:
            limits.close()  # Same abrupt subtree termination as production cancellation.
            process.wait(timeout=5)
        self.assertFalse((self.destination / 'models/Qwen3.5-0.8B-Q4_K_M.gguf').exists())
        self.assertEqual(len(list((self.destination / 'models').glob('*.part'))), 1)
        self.run_setup()  # Must acquire the released lock and remove the abandoned part.
        self.assert_complete()


if __name__ == '__main__':
    unittest.main()
