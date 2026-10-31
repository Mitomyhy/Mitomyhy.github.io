"""Product API contracts and real Windows cancellation; no AI quality claims.

The HTTP tests hold jobs before inference. The process tests replace only the
expensive command with a small parent/grandchild fixture, while exercising the
production ProductManager, Windows Job containment, HTTP cancel, and deadline.
"""
from __future__ import annotations

import http.client
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw

import product_server
from product_server import ProductManager, ProductServer
from process_limits import start_contained_process
from worker_server import Settings


TEST_ROOT = Path(__file__).parent / '.test-tmp'
TEST_ROOT.mkdir(exist_ok=True)


def transparent_product():
    image = Image.new('RGBA', (96, 80), (0, 0, 0, 0))
    ImageDraw.Draw(image).ellipse((15, 7, 80, 73), fill=(211, 80, 30, 177))
    stream = io.BytesIO()
    image.save(stream, format='PNG')
    return stream.getvalue()


class HeldManager(ProductManager):
    """Pause at the worker boundary, so API tests never load fake model files."""
    def _run(self, job):
        job.update(status='running', phase='fixture_wait')
        while not (job['cancel'].wait(.02) or self.closed.is_set()):
            pass
        job.update(status='cancelled', phase='cancelled', finished_at=time.time())
        (job['folder'] / 'request.json').unlink(missing_ok=True)


class ProductHTTPHarness:
    manager_class = HeldManager

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=TEST_ROOT)
        self.root = Path(self.temp.name)
        self.settings = Settings(self.root / 'data', self.root / 'model', self.root / 'ffmpeg.exe',
                                 port=0, memory_bytes=512 * 1024**2)
        # Presence-only readiness fixtures. None is ever executed or called an
        # actual model; inference is held or replaced with the tiny process tree.
        self.settings.ai_runtime = self.root / 'ai'
        for relative in ('models/Qwen3.5-9B-Q4_K_M.gguf', 'models/mmproj-F16.gguf',
                         'bin/b10883/llama-mtmd-cli.exe'):
            target = self.settings.ai_runtime / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b'contract-fixture-not-a-model')
        self.settings.node = self.root / 'node.exe'
        self.settings.browser = self.root / 'browser.exe'
        self.settings.renderer = self.root / 'render.mjs'
        for path in (self.settings.node, self.settings.browser, self.settings.renderer):
            path.write_bytes(b'contract-fixture-not-executed')
        self.settings.mask_model = self.root / 'mask.onnx'
        self.settings.ai_stub = None
        self.settings.ltx = None
        # Test availability is explicitly isolated from real model verification.
        # Production planner pin/manifest/hash tests live in test_prompt_planner.
        # The file predicate still lets the missing-model API regression exercise
        # the disabled state, without allocating multi-GB dummy model files.
        def fixture_planner_status(runtime):
            available = all((Path(runtime) / relative).is_file() for relative in
                ('models/Qwen3.5-9B-Q4_K_M.gguf', 'models/mmproj-F16.gguf',
                 'bin/b10883/llama-mtmd-cli.exe'))
            return {'available': available, 'device': 'cpu', 'offline': True}
        self.planner_patch = patch('prompt_planner.planner_status', side_effect=fixture_planner_status)
        self.planner_patch.start()
        self.manager = self.manager_class(self.settings)
        self.server = ProductServer(self.settings, self.manager, token='product-test-session')
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)
        self.planner_patch.stop()
        self.temp.cleanup()

    def request(self, method, path, body=None, headers=None, auth=True):
        headers = dict(headers or {})
        if auth:
            headers.setdefault('Authorization', 'Bearer product-test-session')
        if isinstance(body, dict):
            body = json.dumps(body)
            headers.setdefault('Content-Type', 'application/json')
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        try:
            connection.request(method, path, body, headers)
            response = connection.getresponse()
            content = response.read()
            result = json.loads(content) if content and response.getheader('Content-Type', '').startswith('application/json') else content
            return response.status, dict(response.getheaders()), result
        finally:
            connection.close()

    def upload(self):
        status, _, result = self.request('POST', '/v1/uploads', transparent_product(), {'Content-Type': 'image/png'})
        self.assertEqual(status, 201)
        return result['image_id']

    def create(self, **overrides):
        body = {'image_id': self.upload(), 'prompt': 'Turn left and right gently, Christmas bokeh.', **overrides}
        status, _, result = self.request('POST', '/v1/product-jobs', body)
        self.assertEqual(status, 202, result)
        return result['job_id']

    def wait_state(self, job_id, expected, timeout=6):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            state = self.manager.snapshot(job_id)
            if state['status'] in expected:
                return state
            time.sleep(.025)
        self.fail(f'Job did not reach {expected}: {self.manager.snapshot(job_id)}')


class ProductContracts(ProductHTTPHarness, unittest.TestCase):
    def test_reopen_reuses_running_service_without_interrupting_job(self):
        job_id = self.create()
        self.wait_state(job_id, {'running'})
        frontend = self.root / 'frontend'
        frontend.mkdir()
        (frontend / 'index.html').write_text('<html><head></head><body>Video</body></html>')
        self.settings.frontend_dir = frontend
        self.server.token = 'x' * 43
        with patch('product_server.webbrowser.open') as opened:
            self.assertTrue(product_server.reopen_running_service(self.server.server_port))
        opened.assert_called_once_with(f'http://127.0.0.1:{self.server.server_port}/')
        self.assertEqual(self.manager.snapshot(job_id)['status'], 'running')

    def test_alias_preserves_authentication_host_and_origin_checks(self):
        body = {'image_id': 'a' * 32, 'prompt': 'Motion'}
        for path in ('/v1/jobs', '/v1/product-jobs'):
            for headers, auth, expected in (({}, False, 401), ({'Authorization': 'Bearer wrong'}, True, 401),
                                            ({'Origin': 'https://untrusted.example'}, True, 403),
                                            ({'Host': 'untrusted.example'}, True, 403)):
                with self.subTest(path=path, headers=headers, auth=auth):
                    self.assertEqual(self.request('POST', path, body, headers, auth)[0], expected)
        self.assertFalse(self.manager.jobs)

    def test_public_health_is_minimal_and_wrong_token_is_rejected(self):
        self.assertEqual(self.request('GET', '/v1/health', auth=False)[2],
                         {'version': 1, 'model_state': 'ready', 'pairing_required': True})
        self.assertEqual(self.request('GET', '/v1/health', headers={'Authorization': 'Bearer wrong'})[0], 401)
        health = self.request('GET', '/v1/health')[2]
        self.assertEqual(health['product_studio']['devices'], ['cpu'])
        self.assertFalse(health['cloud'])

    def test_upload_retains_transparent_and_partial_alpha(self):
        image_id = self.upload()
        with Image.open(self.manager.uploads[image_id]) as image:
            self.assertEqual(image.mode, 'RGBA')
            self.assertEqual(image.getpixel((0, 0))[3], 0)
            self.assertEqual(image.getpixel((48, 40)), (211, 80, 30, 177))

    def test_upload_rejects_content_type_spoof_and_non_image(self):
        for content, mime, expected in ((transparent_product(), 'image/jpeg', 400),
                                        (b'<script>bad</script>', 'image/png', 400),
                                        (transparent_product(), 'application/octet-stream', 415)):
            self.assertEqual(self.request('POST', '/v1/uploads', content, {'Content-Type': mime})[0], expected)

    def test_oversized_upload_and_json_are_rejected_before_reading(self):
        # Declared length exceeds the bound. The handler must reject immediately,
        # without waiting for a malicious client to send or allocate that body.
        for path, mime, length in (('/v1/uploads', 'image/png', 15 * 1024**2 + 1),
                                    ('/v1/product-jobs', 'application/json', 32 * 1024 + 1)):
            status, _, _ = self.request('POST', path, b'',
                {'Content-Type': mime, 'Content-Length': str(length)})
            self.assertEqual(status, 413)
        self.assertFalse(self.manager.uploads)
        self.assertFalse(self.manager.jobs)

    def test_options_and_paths_cannot_select_gpu_executable_or_files(self):
        image_id = self.upload()
        invalid = ({'image_id': '../private.png'}, {'background_id': 'C:/private.png'},
                   {'image': 'C:/private.png'}, {'renderer': 'cmd.exe'}, {'prompt': ''},
                   {'prompt': 'x\0y'}, {'options': {'device': 'gpu'}},
                   {'options': {'provider': 'cuda'}}, {'options': {'browser': 'cmd.exe'}},
                   {'options': {'aspect_ratio': '4:3'}}, {'options': {'duration_seconds': 600}},
                   {'options': {'resolution': 4000}}, {'options': {'seed': True}})
        for change in invalid:
            with self.subTest(change=change):
                status, _, result = self.request('POST', '/v1/product-jobs',
                    {'image_id': image_id, 'prompt': 'Motion', **change})
                self.assertEqual(status, 400, result)
        self.assertFalse(self.manager.jobs)

    def test_paths_and_query_do_not_bypass_alias_boundary(self):
        for path in ('/v1/product-jobs?token=x', '/v1/product-jobs%2f',
                     '/v1/jobs/../request.json', '/v1/health\\private'):
            self.assertIn(self.request('POST', path, {})[0], (400, 404))

    def test_missing_planner_disables_generation_without_cloud_fallback(self):
        (self.settings.ai_runtime / 'models/mmproj-F16.gguf').unlink()
        health = self.request('GET', '/v1/health')[2]
        self.assertFalse(health['generation_enabled'])
        self.assertEqual(health['status'], 'planner_missing')
        status, _, result = self.request('POST', '/v1/product-jobs', {'image_id': self.upload(), 'prompt': 'Motion'})
        self.assertEqual(status, 503, result)
        self.assertFalse(self.manager.jobs)

    def test_aspect_options_are_persisted_and_prompt_is_data(self):
        for ratio in ('16:9', '9:16'):
            job_id = self.create(prompt='Keep text literal: $(echo private) ; <script> ; a/b',
                                 options={'aspect_ratio': ratio, 'duration_seconds': 5,
                                          'resolution': 720, 'device': 'cpu', 'seed': 88})
            self.wait_state(job_id, {'running'})
            request = json.loads((self.manager.jobs[job_id]['folder'] / 'request.json').read_text())
            self.assertEqual(request['options']['aspect_ratio'], ratio)
            self.assertIn('$(echo private)', request['prompt'])
            self.assertEqual(request['node'], str(self.settings.node))
            self.request('POST', f'/v1/jobs/{job_id}/cancel', {})
            self.wait_state(job_id, {'cancelled'})

    def test_queue_bound_and_cancelled_request_cleanup(self):
        first = self.create()
        self.wait_state(first, {'running'})
        queued = [self.create() for _ in range(3)]
        status, _, _ = self.request('POST', '/v1/product-jobs', {'image_id': self.upload(), 'prompt': 'Motion'})
        self.assertEqual(status, 429)
        state = self.request('POST', f'/v1/jobs/{queued[0]}/cancel', {})[2]
        self.assertEqual(state['status'], 'cancelled')
        self.assertFalse((self.manager.jobs[queued[0]]['folder'] / 'request.json').exists())
        self.assertEqual(self.request('GET', f'/v1/jobs/{queued[0]}/video')[0], 409)


@unittest.skipUnless(os.name == 'nt', 'Reviewed containment is Windows-specific')
class ProductProcessCancellation(ProductHTTPHarness, unittest.TestCase):
    manager_class = ProductManager

    def fixture_start(self, command, memory_bytes, **kwargs):
        request_index = command.index('--request')
        folder = Path(command[request_index + 1]).parent
        fixture = self.root / 'contained-fixture.py'
        fixture.write_text("""import json, pathlib, subprocess, sys, time
folder = pathlib.Path(sys.argv[1])
child_code = '''import pathlib,sys,time
p=pathlib.Path(sys.argv[1])
counter=0
while True:
 counter+=1
 p.write_text(str(counter))
 time.sleep(.04)
'''
child = subprocess.Popen([sys.executable, '-I', '-B', '-u', '-c', child_code, str(folder/'heartbeat')])
(folder/'grandchild.pid').write_text(str(child.pid))
print(json.dumps({'phase':'fixture_process_tree','progress':.1}), flush=True)
while True: time.sleep(.1)
""", encoding='utf-8')
        # Only the fixture command changes. The actual Job assignment-before-
        # resume, descendant memory accounting, and kill-on-close stay enabled.
        return start_contained_process([sys.executable, '-I', '-B', '-u', str(fixture), str(folder)],
                                       memory_bytes, **kwargs)

    def wait_heartbeat(self, folder):
        deadline = time.monotonic() + 6
        heartbeat = folder / 'heartbeat'
        while time.monotonic() < deadline:
            if heartbeat.exists() and heartbeat.stat().st_size:
                return heartbeat
            time.sleep(.03)
        self.fail(f'Native fixture never started: {self.manager.jobs}')

    def assert_tree_stopped(self, folder, heartbeat):
        # Both no surviving process and a stable heartbeat are checked. A fake
        # cancelled status alone would not satisfy this regression test.
        import ctypes
        from ctypes import wintypes
        pid = int((folder / 'grandchild.pid').read_text())
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.GetExitCodeProcess.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if handle:
            try:
                exit_code = wintypes.DWORD()
                self.assertTrue(kernel.GetExitCodeProcess(handle, ctypes.byref(exit_code)))
                self.assertNotEqual(exit_code.value, 259, 'Grandchild remained running after cancellation')
            finally:
                kernel.CloseHandle(handle)
        before = heartbeat.read_bytes()
        time.sleep(.2)
        self.assertEqual(heartbeat.read_bytes(), before)
        self.assertFalse((folder / 'request.json').exists())

    def test_http_cancel_stops_actual_native_process_subtree(self):
        with patch.object(product_server, 'start_contained_process', side_effect=self.fixture_start):
            job_id = self.create()
            folder = self.manager.jobs[job_id]['folder']
            heartbeat = self.wait_heartbeat(folder)
            self.assertEqual(self.request('POST', f'/v1/jobs/{job_id}/cancel', {})[0], 200)
            state = self.wait_state(job_id, {'cancelled'})
            self.assertEqual(state['error']['code'], 'cancelled')
            self.assert_tree_stopped(folder, heartbeat)

    def test_deadline_stops_actual_native_process_subtree(self):
        self.settings.deadline_seconds = 1
        with patch.object(product_server, 'start_contained_process', side_effect=self.fixture_start):
            job_id = self.create()
            folder = self.manager.jobs[job_id]['folder']
            heartbeat = self.wait_heartbeat(folder)
            state = self.wait_state(job_id, {'failed'})
            self.assertEqual(state['error']['code'], 'deadline_exceeded')
            self.assert_tree_stopped(folder, heartbeat)


class ProductAssetContracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=TEST_ROOT)
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_trim_preserves_original_product_rgba(self):
        from product_assets import cutout_product
        source, output = self.root / 'source.png', self.root / 'product.png'
        source.write_bytes(transparent_product())
        result = cutout_product(source, output)
        self.assertEqual(result['method'], 'original_alpha')
        with Image.open(output) as image:
            self.assertEqual(image.getpixel((0, 0))[3], 0)
            self.assertEqual(image.getpixel((image.width // 2, image.height // 2)), (211, 80, 30, 177))

    def test_flat_background_removal_retains_enclosed_white_artwork(self):
        from product_assets import cutout_product
        source, output = self.root / 'source.png', self.root / 'product.png'
        image = Image.new('RGB', (100, 100), 'white')
        draw = ImageDraw.Draw(image)
        draw.ellipse((15, 15, 85, 85), fill=(200, 20, 40))
        draw.ellipse((30, 30, 70, 70), fill='white')
        image.save(source)
        result = cutout_product(source, output)
        self.assertEqual(result['method'], 'connected_flat_background')
        with Image.open(output) as product:
            self.assertEqual(product.getpixel((0, 0))[3], 0)
            self.assertEqual(product.getpixel((product.width // 2, product.height // 2)), (255, 255, 255, 255))

    def test_complex_background_without_mask_model_is_explicit_error(self):
        from product_assets import AssetError, cutout_product
        image = Image.new('RGB', (96, 80), 'white')
        image.putpixel((0, 0), (0, 0, 0))
        source, output = self.root / 'source.png', self.root / 'product.png'
        image.save(source)
        with self.assertRaisesRegex(AssetError, 'transparent product PNG'):
            cutout_product(source, output)
        self.assertFalse(output.exists())

if __name__ == '__main__':
    unittest.main()
