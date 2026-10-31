"""Control-plane/security tests. No model, download, or generated-AI claims."""
from __future__ import annotations

import http.client
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from model_validation import COMPONENTS, MANIFEST_NAME, ModelValidationError, validate_model
from process_limits import ProcessLimitError, ProcessLimits, start_contained_process
from worker_server import APIError, JobManager, Settings, WorkerHTTPServer, generation_options, open_local_browser_once

TEST_ROOT = Path(__file__).parent / ".test-tmp"
TEST_ROOT.mkdir(exist_ok=True)


def fixture_model(path):
    path.mkdir()
    (path / MANIFEST_NAME).write_text(json.dumps({"schema_version": 1,
        "profile": "ltxv-2b-0.9.6-distilled", "engine": "optimum-openvino", "device": "CPU",
        "source_revision": "a" * 40}))
    (path / "model_index.json").write_text('{"_class_name":"LTXImageToVideoPipeline"}')
    for component in COMPONENTS:
        (path / component).mkdir()
        for name, content in (("config.json", "{}"), ("openvino_model.xml", "fixture"),
                              ("openvino_model.bin", "fixture")):
            (path / component / name).write_text(content)
    (path / "scheduler").mkdir()
    (path / "scheduler/scheduler_config.json").write_text("{}")
    (path / "tokenizer").mkdir()
    (path / "tokenizer/tokenizer_config.json").write_text("{}")
    (path / "tokenizer/spiece.model").write_text("fixture")


def image_bytes(size=(64, 64), fmt="PNG"):
    from PIL import Image
    stream = io.BytesIO()
    Image.new("RGB", size, "red").save(stream, format=fmt)
    return stream.getvalue()


class NoOpLimits:
    """Only for tiny control-plane subprocess fixtures; never the production path."""
    def __init__(self, process, limit):
        self.limit = limit
    def __enter__(self):
        return self
    def __exit__(self, *_):
        pass
    def close(self):
        pass
    def memory(self):
        return {"rss_bytes": 100, "private_bytes": 200, "peak_job_private_bytes": 300}


class AtMemoryLimit(NoOpLimits):
    def memory(self):
        return {"rss_bytes": self.limit, "private_bytes": self.limit,
                "peak_job_private_bytes": self.limit}


def sleeper_command(folder):
    code = "import time; time.sleep(10)"
    return [sys.executable, "-u", "-c", code]


def failure_command(folder):
    code = 'import json; print(json.dumps({"phase":"failed","error":"Runtime intentionally absent in fixture"}),flush=True); raise SystemExit(1)'
    return [sys.executable, "-u", "-c", code]


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=TEST_ROOT)
        self.root = Path(self.temp.name)
        fixture_model(self.root / "model")
        (self.root / "ffmpeg.exe").write_bytes(b"fixture-not-executed")
        self.settings = Settings(self.root / "data", self.root / "model", self.root / "ffmpeg.exe", port=0)
        self.manager = JobManager(self.settings, command_factory=sleeper_command, limit_factory=NoOpLimits)
        self.server = WorkerHTTPServer(self.settings, self.manager, token="test-token")
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)
        self.temp.cleanup()

    def request(self, method, path, data=None, headers=None, auth=True):
        headers = dict(headers or {})
        if auth:
            headers.setdefault("Authorization", "Bearer test-token")
        if isinstance(data, dict):
            data = json.dumps(data)
            headers.setdefault("Content-Type", "application/json")
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=4)
        try:
            conn.request(method, path, data, headers)
            response = conn.getresponse()
            raw = response.read()
            body = json.loads(raw) if raw and response.getheader("Content-Type", "").startswith("application/json") else raw
            return response.status, dict(response.getheaders()), body if raw else None
        finally:
            conn.close()

    def uploaded(self):
        status, _, body = self.request("POST", "/v1/uploads", image_bytes(), {"Content-Type": "image/png"})
        self.assertEqual(status, 201)
        return body["image_id"]

    def job(self):
        image_id = self.uploaded()
        status, _, body = self.request("POST", "/v1/jobs", {"image_id": image_id, "prompt": "The ornament gently sways."})
        self.assertEqual(status, 202)
        return body["job_id"]

    def wait_job(self, job_id, statuses):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            state = self.manager.snapshot(job_id)
            if state["status"] in statuses:
                return state
            time.sleep(.03)
        self.fail("Job did not reach expected state")

    def test_public_discovery_does_not_expose_paths_or_token(self):
        status, _, body = self.request("GET", "/v1/health", auth=False)
        self.assertEqual(status, 200)
        self.assertEqual(body, {"version": 1, "model_state": "model_unverified", "pairing_required": True})

    def test_full_health_reports_unverified_model_and_cpu_bounds(self):
        status, _, body = self.request("GET", "/v1/health")
        self.assertEqual(status, 200)
        self.assertFalse(body["inference_verified"])
        self.assertEqual(body["device"], "CPU")
        self.assertEqual(body["memory_limit_bytes"], 10 * 1024**3)
        self.assertEqual(body["mp4_export"]["short_side"], 720)
        self.assertFalse(body["mp4_export"]["audio"])

    def test_job_reports_native_AI_and_separate_upscaled_export(self):
        state = self.manager.snapshot(self.job())
        self.assertEqual(state["native_resolution"], {"width": 512, "height": 320})
        self.assertEqual(state["export_resolution"], {"width": 1152, "height": 720})
        self.assertTrue(state["upscaled"])
        self.assertEqual(state["duration_seconds"], 49 / 8)
        self.assertEqual(state["frames"], 49)
        self.assertFalse(state["audio"])

    def test_optional_browser_open_is_once_and_loopback_only(self):
        with patch("worker_server.webbrowser.open_new_tab", return_value=True) as opened:
            self.assertIsNone(open_local_browser_once(self.server, False))
            opened.assert_not_called()
            thread = open_local_browser_once(self.server, True)
            thread.join(timeout=2)
            self.assertIsNone(open_local_browser_once(self.server, True))
            opened.assert_called_once_with(f"http://127.0.0.1:{self.server.server_port}/")

    def test_unauthenticated_upload_and_job_are_rejected(self):
        for path in ("/v1/uploads", "/v1/jobs"):
            status, _, _ = self.request("POST", path, b"x", auth=False)
            self.assertEqual(status, 401)

    def test_bad_auth_not_downgraded_to_public_health(self):
        status, _, _ = self.request("GET", "/v1/health", headers={"Authorization": "Bearer wrong"})
        self.assertEqual(status, 401)

    def test_host_and_origin_protect_against_rebinding(self):
        for headers in ({"Host": "evil.example"}, {"Origin": "https://evil.example"}, {"Origin": "null"}):
            status, response_headers, _ = self.request("GET", "/v1/health", headers=headers)
            self.assertEqual(status, 403)
            if "Origin" in headers:
                self.assertNotIn("Access-Control-Allow-Origin", response_headers)

    def test_cors_preflight_exact_allowlist(self):
        headers = {"Origin": "https://mitomyhy.github.io", "Access-Control-Request-Method": "POST",
                   "Access-Control-Request-Headers": "authorization,content-type",
                   "Access-Control-Request-Private-Network": "true"}
        status, response_headers, _ = self.request("OPTIONS", "/v1/jobs", headers=headers, auth=False)
        self.assertEqual(status, 204)
        self.assertEqual(response_headers["Access-Control-Allow-Origin"], "https://mitomyhy.github.io")
        self.assertEqual(response_headers["Access-Control-Allow-Private-Network"], "true")
        self.assertNotIn("Access-Control-Allow-Credentials", response_headers)
        headers["Access-Control-Request-Headers"] = "x-unapproved"
        self.assertEqual(self.request("OPTIONS", "/v1/jobs", headers=headers, auth=False)[0], 403)

    def test_upload_decodes_and_normalizes_image(self):
        image_id = self.uploaded()
        self.assertTrue(self.manager.uploads[image_id].is_file())
        self.assertEqual(self.manager.uploads[image_id].suffix, ".png")

    def test_invalid_images_and_wrong_content_type_are_rejected(self):
        cases = [(b"not an image", "image/png", 400), (image_bytes(), "image/jpeg", 400),
                 (image_bytes(), "application/octet-stream", 415),
                 (image_bytes((16, 16)), "image/png", 400)]
        for data, content_type, expected in cases:
            self.assertEqual(self.request("POST", "/v1/uploads", data, {"Content-Type": content_type})[0], expected)

    def test_json_and_resource_bounds(self):
        self.assertEqual(self.request("POST", "/v1/jobs", b"{}", {"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.request("POST", "/v1/jobs", b"{", {"Content-Type": "application/json"})[0], 400)
        image_id = self.uploaded()
        for change in ({"prompt": ""}, {"prompt": "a" * 4001}, {"image_id": "../x"},
                       {"options": {"width": 1920, "height": 1080}}, {"options": {"threads": 64}},
                       {"options": {"frames": 16}}, {"options": {"frames": 50}},
                       {"options": {"fps": 10}}, {"options": {"fps": 24}},
                       {"options": {"width": 704, "height": 480}},
                       {"options": {"seed": True}}, {"device": "GPU"}):
            body = {"image_id": image_id, "prompt": "Natural product motion", **change}
            self.assertEqual(self.request("POST", "/v1/jobs", body)[0], 400, change)

    def test_paths_and_identifier_traversal_rejected(self):
        for path in ("/v1/jobs/../request.json", "/v1/jobs/%2e%2e", "/v1/health?token=x", "/v1/health\\x"):
            self.assertIn(self.request("GET", path)[0], (400, 404))

    def test_missing_model_disables_generation(self):
        (self.root / "model/vae_encoder/openvino_model.bin").unlink()
        self.assertEqual(self.request("GET", "/v1/health")[2]["status"], "model_missing_or_invalid")
        image_id = self.uploaded()
        self.assertEqual(self.request("POST", "/v1/jobs", {"image_id": image_id, "prompt": "Motion"})[0], 503)

    def test_queue_bounds_and_cancel_both_running_and_queued(self):
        first = self.job()
        self.wait_job(first, {"running"})
        queued = [self.job() for _ in range(3)]
        image_id = self.uploaded()
        self.assertEqual(self.request("POST", "/v1/jobs", {"image_id": image_id, "prompt": "Motion"})[0], 429)
        self.assertEqual(self.request("POST", f"/v1/jobs/{queued[0]}/cancel", {})[2]["status"], "cancelled")
        self.assertFalse((self.manager.jobs[queued[0]]["folder"] / "request.json").exists())
        self.request("POST", f"/v1/jobs/{first}/cancel", {})
        self.assertEqual(self.wait_job(first, {"cancelled"})["error"]["code"], "cancelled")
        self.assertEqual(self.request("GET", f"/v1/jobs/{first}/video")[0], 409)

    def test_memory_limit_failure_and_prompt_cleanup(self):
        self.manager.limit_factory = AtMemoryLimit
        job_id = self.job()
        state = self.wait_job(job_id, {"failed"})
        self.assertEqual(state["error"]["code"], "memory_limit")
        self.assertFalse((self.manager.jobs[job_id]["folder"] / "request.json").exists())
        self.assertFalse(self.manager.inference_verified)

    def test_deadline_failure(self):
        self.settings.deadline_seconds = 1
        state = self.wait_job(self.job(), {"failed"})
        self.assertEqual(state["error"]["code"], "deadline_exceeded")

    def test_runtime_error_does_not_create_false_completed_job(self):
        self.manager.command_factory = failure_command
        state = self.wait_job(self.job(), {"failed"})
        self.assertEqual(state["error"]["code"], "inference_failed")
        self.assertIn("intentionally absent", state["error"]["message"])
        self.assertFalse(self.manager.inference_verified)

    def prepare_frontend(self):
        path = self.root / "frontend"
        path.mkdir()
        (path / "index.html").write_text('<!doctype html><head><meta name="mitom-session-token" content=""></head><body>MITOM</body>')
        (path / "app.js").write_text('console.log("application fixture");')
        (path / "private.txt").write_text("DO_NOT_SERVE")
        self.settings.frontend_dir = path

    def test_local_frontend_injects_token_only_in_protected_document(self):
        self.prepare_frontend()
        status, headers, body = self.request("GET", "/", auth=False)
        self.assertEqual(status, 200)
        self.assertIn(b'<meta name="mitom-session-token" content="test-token">', body)
        self.assertNotIn("Access-Control-Allow-Origin", headers)
        self.assertEqual(headers["X-Frame-Options"], "DENY")
        self.assertEqual(headers["Cross-Origin-Opener-Policy"], "same-origin")
        self.assertEqual(headers["Cross-Origin-Resource-Policy"], "same-origin")
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.assertIn("connect-src 'self'", headers["Content-Security-Policy"])

    def test_cross_origin_fetch_cannot_read_frontend_token(self):
        self.prepare_frontend()
        cases = [
            {"Origin": "https://mitomyhy.github.io", "Sec-Fetch-Mode": "cors", "Sec-Fetch-Site": "cross-site"},
            {"Sec-Fetch-Mode": "cors", "Sec-Fetch-Site": "cross-site"},
            {"Sec-Fetch-Mode": "no-cors", "Sec-Fetch-Site": "same-site"},
            {"Origin": f"http://localhost:{self.server.server_port}"},
        ]
        for request_headers in cases:
            status, headers, body = self.request("GET", "/index.html", headers=request_headers, auth=False)
            self.assertEqual(status, 403, request_headers)
            self.assertNotIn("Access-Control-Allow-Origin", headers)
            self.assertNotIn("test-token", json.dumps(body))

    def test_frontend_allows_top_navigation_and_same_origin(self):
        self.prepare_frontend()
        cases = [
            {"Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document", "Sec-Fetch-Site": "cross-site"},
            {"Origin": f"http://127.0.0.1:{self.server.server_port}", "Sec-Fetch-Mode": "cors", "Sec-Fetch-Site": "same-origin"},
        ]
        for headers in cases:
            self.assertEqual(self.request("GET", "/", headers=headers, auth=False)[0], 200)

    def test_frontend_serves_fixed_assets_without_directory_or_data_access(self):
        self.prepare_frontend()
        status, headers, body = self.request("GET", "/app.js", auth=False)
        self.assertEqual(status, 200)
        self.assertEqual(body, b'console.log("application fixture");')
        self.assertNotIn("Access-Control-Allow-Origin", headers)
        for path in ("/private.txt", "/assets/", "/../private.txt", "/%2e%2e/private.txt", "/data/request.json", "/index.html?token=x"):
            status, _, body = self.request("GET", path)
            self.assertIn(status, (400, 404), path)
            self.assertNotIn("DO_NOT_SERVE", json.dumps(body))


class ModelTests(unittest.TestCase):
    def test_accepts_sentencepiece_or_modern_fast_tokenizer_and_rejects_missing_both(self):
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as folder:
            model = Path(folder) / "model"
            fixture_model(model)
            self.assertTrue(validate_model(model)["package_present"])
            (model / "tokenizer/spiece.model").unlink()
            with self.assertRaisesRegex(ModelValidationError, "Missing local tokenizer"):
                validate_model(model)
            (model / "tokenizer/tokenizer.json").write_text('{"version":"1.0","model":{"type":"Unigram"}}')
            self.assertTrue(validate_model(model)["package_present"])

    def test_rejects_oversized_fast_tokenizer_before_parsing(self):
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as folder:
            model = Path(folder) / "model"
            fixture_model(model)
            (model / "tokenizer/spiece.model").unlink()
            with (model / "tokenizer/tokenizer.json").open("wb") as stream:
                stream.seek(16 * 1024 * 1024)
                stream.write(b"0")
            with self.assertRaisesRegex(ModelValidationError, "Oversized model configuration"):
                validate_model(model)

    def test_rejects_wrong_profile_and_missing_components(self):
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as folder:
            model = Path(folder) / "model"
            fixture_model(model)
            self.assertFalse(validate_model(model)["inference_verified"])
            (model / "model_index.json").write_text('{"_class_name":"LTXPipeline"}')
            with self.assertRaises(ModelValidationError):
                validate_model(model)

    def test_resource_option_defaults(self):
        self.assertEqual(generation_options({}), {"width": 512, "height": 320, "frames": 49, "fps": 8, "seed": 42})

    def test_all_bounded_generation_profiles_and_fast_legacy_options(self):
        for width, height in ((320, 192), (192, 320), (256, 256), (512, 320), (320, 512), (384, 384), (704, 448), (448, 704)):
            for frames in (9, 17, 49):
                for fps in (8, 12):
                    with self.subTest(size=(width, height), frames=frames, fps=fps):
                        options = {"width": width, "height": height, "frames": frames, "fps": fps, "seed": 123}
                        self.assertEqual(generation_options({"options": options}), options)
        for options in ({"width": 512, "height": 512}, {"frames": 48},
                        {"fps": 9}, {"fps": True}, {"width": 704, "height": 480}):
            with self.subTest(rejected=options), self.assertRaises(APIError):
                generation_options({"options": options})

    @unittest.skipUnless(os.name == "nt", "Windows Job object containment")
    def test_real_windows_job_memory_accounting_and_kill_on_close(self):
        process, limits = start_contained_process(
            [sys.executable, "-c", "import time; data=bytearray(2*1024*1024); time.sleep(10)"],
            128 * 1024**2, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            with limits:
                time.sleep(.2)
                memory = limits.memory()
                self.assertGreater(memory["private_bytes"], 1024**2)
                self.assertGreater(memory["peak_job_private_bytes"], 1024**2)
            process.wait(timeout=3)
            self.assertIsNotNone(process.returncode)
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()

    @unittest.skipUnless(os.name == "nt", "Windows venv launcher containment")
    def test_actual_venv_child_allocation_is_inside_job(self):
        venv_python = Path(__file__).resolve().parent.parent / ".work/video-ai-export-env/Scripts/python.exe"
        self.assertTrue(venv_python.is_file(), "This regression requires the actual Windows venv launcher")
        code = ('import json,os,time; data=bytearray(16*1024*1024); '
                'data[::4096]=b"x"*4096; print(json.dumps({"real_pid":os.getpid()}),flush=True); time.sleep(15)')
        process, limits = start_contained_process(
            [str(venv_python), "-u", "-c", code], 128 * 1024**2,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            with limits:
                line = process.stdout.readline()
                self.assertTrue(line, process.stderr.read() if process.poll() is not None else "No child report")
                child = json.loads(line)["real_pid"]
                self.assertNotEqual(child, process.pid, "Expected venv launcher to spawn its real Python child")
                self.assertIn(child, limits.member_pids())
                self.assertIn(process.pid, limits.member_pids())
                memory = limits.memory()
                self.assertGreater(memory["private_bytes"], 8 * 1024**2)
                self.assertGreater(memory["rss_bytes"], 8 * 1024**2)
                self.assertGreater(memory["peak_job_private_bytes"], 8 * 1024**2)
                self.assertGreaterEqual(memory["job_process_count"], 2)
                evidence = {"launcher_pid": process.pid, "real_python_pid": child,
                            "job_pids": limits.member_pids(), "memory": memory,
                            "allocation_bytes": 16 * 1024**2,
                            "created_suspended_before_job_assignment": True,
                            "model_weights_loaded": False}
            process.wait(timeout=3)
            # EOF proves the actual Python child closed its inherited stdout too.
            self.assertEqual(process.stdout.read(), "")
            evidence["job_close_killed_launcher_and_child"] = True
            (Path(__file__).parent / "containment-proof.json").write_text(json.dumps(evidence, indent=2))
        finally:
            limits.close()
            if process.poll() is None:
                process.kill()
            process.wait()
            process.stdout.close()
            process.stderr.close()

    @unittest.skipUnless(os.name == "nt", "Windows Job memory cap")
    def test_actual_venv_child_allocation_cannot_exceed_job_cap(self):
        venv_python = Path(__file__).resolve().parent.parent / ".work/video-ai-export-env/Scripts/python.exe"
        self.assertTrue(venv_python.is_file())
        code = ('import time\ntry:\n data=bytearray(128*1024*1024)\n print("UNEXPECTED_ALLOCATION",flush=True)'
                '\nexcept MemoryError:\n print("ALLOCATION_BLOCKED",flush=True)\n time.sleep(1)')
        process, limits = start_contained_process(
            [str(venv_python), "-u", "-c", code], 64 * 1024**2,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            output = process.stdout.readline()
            self.assertEqual(output.strip(), "ALLOCATION_BLOCKED")
            memory = limits.memory()
            self.assertLess(memory["private_bytes"], 64 * 1024**2)
            remaining, errors = process.communicate(timeout=5)
            self.assertEqual(process.returncode, 0, errors)
            self.assertEqual(remaining, "")
            proof = {"attempted_allocation_bytes": 128 * 1024**2,
                     "job_cap_bytes": 64 * 1024**2, "allocation_blocked": True,
                     "memory_after_denial_while_child_alive": memory,
                     "note": "Windows reported Job peak may exceed cap after a denied allocation; actual allocation raised MemoryError and live private memory remained below cap.",
                     "model_weights_loaded": False}
            (Path(__file__).parent / "hard-cap-proof.json").write_text(json.dumps(proof, indent=2))
        finally:
            limits.close()
            if process.poll() is None:
                process.kill()
            process.wait()


if __name__ == "__main__":
    unittest.main(verbosity=2)
