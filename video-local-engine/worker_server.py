"""MITOM unpublished local AI video worker. Bind 127.0.0.1 only.

All stateful API calls require a fresh per-process bearer token. This server
never downloads models, runs remote code, or uses a cloud fallback.
"""
from __future__ import annotations

import argparse
import collections
import hmac
import html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import queue
import re
import secrets
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit
import warnings
import webbrowser

from model_validation import ModelValidationError, validate_model
from mp4_export import export_metadata
from process_limits import ProcessLimitError, ProcessLimits, start_contained_process

ID = re.compile(r"^[a-f0-9]{32}$")
MAX_IMAGE_BYTES = 15 * 1024 * 1024
MAX_JSON_BYTES = 32 * 1024
MAX_PIXELS = 24_000_000
MAX_SIDE = 12_000
MAX_PROMPT = 4000
TERMINAL = {"completed", "cancelled", "failed"}
SIZES = {(320, 192), (192, 320), (256, 256), (512, 320), (320, 512), (384, 384), (704, 448), (448, 704)}
FRAME_COUNTS = {9, 17, 49}
FRAME_RATES = {8, 12}
FRONTEND_ASSETS = {
    "/app.js": "application/javascript; charset=utf-8",
    "/client.mjs": "application/javascript; charset=utf-8",
    "/style.css": "text/css; charset=utf-8",
    "/assets/mitom-logo.png": "image/png",
    "/assets/favicon.ico": "image/x-icon",
    "/assets/svn-helvetica-neue-regular.ttf": "font/ttf",
    "/assets/akzidenz-grotesk-bold.ttf": "font/ttf",
}


class APIError(Exception):
    def __init__(self, status, code, message):
        self.status, self.code, self.message = status, code, message
        super().__init__(message)


def safe_id(value):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise APIError(400, "invalid_id", "Invalid resource identifier")
    return value


def integer(value, name, low, high):
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise APIError(400, "invalid_options", f"Invalid {name}")
    return value


def generation_options(body):
    opts = body.get("options", {})
    if not isinstance(opts, dict) or set(opts) - {"width", "height", "frames", "fps", "seed"}:
        raise APIError(400, "invalid_options", "Unknown generation options")
    width = integer(opts.get("width", 512), "width", 32, 704)
    height = integer(opts.get("height", 320), "height", 32, 704)
    if (width, height) not in SIZES:
        raise APIError(400, "invalid_options", "Resolution is outside the bounded CPU profile")
    frames = integer(opts.get("frames", 49), "frames", 9, 49)
    if frames not in FRAME_COUNTS:
        raise APIError(400, "invalid_options", "Frames must be 9, 17, or 49")
    fps = integer(opts.get("fps", 8), "fps", 8, 12)
    if fps not in FRAME_RATES:
        raise APIError(400, "invalid_options", "Frame rate must be 8 or 12")
    return {"width": width, "height": height, "frames": frames,
            "fps": fps,
            "seed": integer(opts.get("seed", 42), "seed", 0, 2**31 - 1)}


class Settings:
    def __init__(self, data_dir, model_dir, ffmpeg, *, python=sys.executable,
                 port=4176, memory_bytes=10 * 1024**3, deadline_seconds=1800,
                 allowed_origins=("https://mitomyhy.github.io",), frontend_dir=None):
        self.data_dir = Path(data_dir).resolve()
        self.model_dir = Path(model_dir).resolve()
        self.ffmpeg = Path(ffmpeg).resolve()
        self.python = str(Path(python).resolve())
        self.frontend_dir = Path(frontend_dir).resolve() if frontend_dir is not None else None
        self.port = integer(port, "port", 0, 65535)
        self.memory_bytes = int(memory_bytes)
        self.deadline_seconds = int(deadline_seconds)
        if not 128 * 1024**2 <= self.memory_bytes <= 10 * 1024**3:
            raise ValueError("Memory limit must be between 128 MiB and 10 GiB")
        if not 1 <= self.deadline_seconds <= 7200:
            raise ValueError("Deadline must be between 1 second and 2 hours")
        self.allowed_origins = frozenset(allowed_origins)
        for origin in self.allowed_origins:
            parsed = urlsplit(origin)
            if (parsed.scheme not in ("https", "http") or not parsed.netloc
                    or parsed.path or parsed.query or parsed.fragment
                    or parsed.username or parsed.password):
                raise ValueError("Allowed origins must be exact HTTP(S) origins")


class JobManager:
    def __init__(self, settings, *, command_factory=None, limit_factory=ProcessLimits):
        self.settings = settings
        self.session_dir = settings.data_dir / secrets.token_hex(16)
        self.upload_dir = self.session_dir / "uploads"
        self.jobs_dir = self.session_dir / "jobs"
        self.upload_dir.mkdir(parents=True)
        self.jobs_dir.mkdir()
        self.uploads = {}
        self.jobs = collections.OrderedDict()
        # Active-job accounting below owns the bound; a separate Queue maxsize
        # would race with the consumer taking the first pending job.
        self.pending = queue.Queue()
        self.lock = threading.RLock()
        self.closed = threading.Event()
        self.command_factory = command_factory or self._command
        self.limit_factory = limit_factory
        self.inference_verified = False
        self.thread = threading.Thread(target=self._loop, name="mitom-generation", daemon=True)
        self.thread.start()

    def health(self):
        try:
            model = validate_model(self.settings.model_dir)
            model_status = "inference_verified" if self.inference_verified else "model_unverified"
        except ModelValidationError as exc:
            model, model_status = {"package_present": False, "reason": str(exc)}, "model_missing_or_invalid"
        if not self.settings.ffmpeg.is_file():
            model_status = "encoder_missing"
        return {"service": "MITOM Local Video", "api_version": 1, "device": "CPU",
                "status": model_status, "model": model,
                "inference_verified": self.inference_verified,
                "generation_enabled": model.get("package_present", False) and self.settings.ffmpeg.is_file(),
                "memory_limit_bytes": self.settings.memory_bytes,
                "deadline_seconds": self.settings.deadline_seconds,
                "threads": 4, "cloud": False, "persistent_service": False,
                "mp4_export": {"codec": "h264", "pixel_format": "yuv420p", "audio": False,
                               "short_side": 720, "scaling": "upscale_preserve_aspect_even",
                               "native_resolution_reported_per_job": True}}

    def upload(self, data, content_type):
        if content_type not in ("image/jpeg", "image/png", "image/webp"):
            raise APIError(415, "unsupported_image", "Use a PNG, JPEG, or WebP image")
        if not data or len(data) > MAX_IMAGE_BYTES:
            raise APIError(413, "image_too_large", "Image must be at most 15 MiB")
        with self.lock:
            if len(self.uploads) >= 20:
                raise APIError(429, "upload_limit", "Session upload limit reached; restart the worker")
            try:
                from PIL import Image
                with warnings.catch_warnings():
                    warnings.simplefilter("error", Image.DecompressionBombWarning)
                    with Image.open(io.BytesIO(data)) as image:
                        if image.format not in ("PNG", "JPEG", "WEBP"):
                            raise ValueError("Unsupported image format")
                        expected_type = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}[image.format]
                        if content_type != expected_type:
                            raise ValueError("Image data does not match Content-Type")
                        width, height = image.size
                        if width < 32 or height < 32 or max(width, height) > MAX_SIDE or width * height > MAX_PIXELS:
                            raise ValueError("Image must be 32–12000 px per side and at most 24 megapixels")
                        if getattr(image, "n_frames", 1) != 1:
                            raise ValueError("Upload one still image")
                        image.load()
                        normalized = image.convert("RGB")
                        # Retain only a bounded working copy; metadata is discarded.
                        normalized.thumbnail((2048, 2048))
            except ImportError as exc:
                raise APIError(503, "runtime_missing", "Image runtime is not installed") from exc
            except Exception as exc:
                raise APIError(400, "invalid_image", str(exc)[:300]) from exc
            image_id = secrets.token_hex(16)
            path = self.upload_dir / f"{image_id}.png"
            normalized.save(path, format="PNG")
            self.uploads[image_id] = path
            return {"image_id": image_id, "width": width, "height": height}

    def create(self, body):
        if not isinstance(body, dict) or set(body) - {"image_id", "prompt", "options"}:
            raise APIError(400, "invalid_request", "Expected image_id, prompt, and optional options")
        image_id = safe_id(body.get("image_id"))
        prompt = body.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > MAX_PROMPT or "\0" in prompt:
            raise APIError(400, "invalid_prompt", "Prompt must contain 1–4000 characters")
        options = generation_options(body)
        health = self.health()
        if not health["generation_enabled"]:
            raise APIError(503, health["status"], "Install a validated local model and MP4 encoder first")
        with self.lock:
            if self.closed.is_set():
                raise APIError(503, "shutting_down", "Worker is shutting down")
            if image_id not in self.uploads:
                raise APIError(404, "image_not_found", "Image is not in this worker session")
            active = sum(job["status"] not in TERMINAL for job in self.jobs.values())
            if active >= 4:
                raise APIError(429, "queue_full", "One running job and three waiting jobs are allowed")
            if len(self.jobs) >= 40:
                raise APIError(429, "job_limit", "Session job limit reached; restart the worker")
            job_id = secrets.token_hex(16)
            folder = self.jobs_dir / job_id
            folder.mkdir()
            job = {"job_id": job_id, "status": "queued", "created_at": time.time(),
                   "phase": "queued", "options": options, "device": "CPU",
                   "progress": {"step": 0, "total_steps": 8}, "memory": {},
                   "cancel": threading.Event(), "folder": folder,
                   **export_metadata(options["width"], options["height"], options["frames"], options["fps"])}
            request = {"model": str(self.settings.model_dir), "ffmpeg": str(self.settings.ffmpeg),
                       "image": str(self.uploads[image_id]), "prompt": prompt.strip(),
                       "options": options, "threads": 4, "output": str(folder / "video.mp4"),
                       "cancel_marker": str(folder / "cancel")}
            (folder / "request.json").write_text(json.dumps(request), encoding="utf-8")
            self.jobs[job_id] = job
            self.pending.put_nowait(job_id)
            return self.snapshot(job_id)

    def snapshot(self, job_id):
        safe_id(job_id)
        with self.lock:
            if job_id not in self.jobs:
                raise APIError(404, "job_not_found", "Job is not in this worker session")
            job = self.jobs[job_id]
            value = {key: val for key, val in job.items() if key not in {"cancel", "folder"}}
            if value["status"] == "completed":
                value["download_path"] = f"/v1/jobs/{job_id}/video"
            return json.loads(json.dumps(value))

    def cancel(self, job_id):
        safe_id(job_id)
        with self.lock:
            self.snapshot(job_id)
            job = self.jobs[job_id]
            if job["status"] not in TERMINAL:
                job["cancel"].set()
                (job["folder"] / "cancel").touch()
                if job["status"] == "queued":
                    job.update(status="cancelled", phase="cancelled", finished_at=time.time())
                    (job["folder"] / "request.json").unlink(missing_ok=True)
            return self.snapshot(job_id)

    def video(self, job_id):
        with self.lock:
            if self.snapshot(job_id)["status"] != "completed":
                raise APIError(409, "video_not_ready", "Video generation has not completed")
            path = self.jobs[job_id]["folder"] / "video.mp4"
            if not path.is_file() or path.resolve().parent != self.jobs[job_id]["folder"]:
                raise APIError(404, "video_not_found", "Video is unavailable")
            return path

    def _command(self, folder):
        return [self.settings.python, "-u", str(Path(__file__).with_name("inference.py")),
                "--request", str(folder / "request.json"),
                "--start-marker", str(folder / "start")]

    def _event_reader(self, stream, events):
        try:
            for line in iter(stream.readline, ""):
                if len(line) > 8192:
                    continue
                try:
                    event = json.loads(line)
                    if isinstance(event, dict):
                        events.put(event)
                except ValueError:
                    pass
        finally:
            stream.close()

    def _run(self, job):
        folder = job["folder"]
        events = queue.Queue(maxsize=128)
        process = None
        completion_seen = False
        try:
            with self.lock:
                if job["cancel"].is_set() or self.closed.is_set():
                    job.update(status="cancelled", phase="cancelled")
                    return
                job.update(status="running", phase="starting", started_at=time.time())
            with (folder / "runtime.log").open("w", encoding="utf-8") as log:
                process, containment = start_contained_process(
                    self.command_factory(folder), self.settings.memory_bytes,
                    limit_factory=self.limit_factory, stdout=subprocess.PIPE,
                    stderr=log, text=True, encoding="utf-8", errors="replace",
                    stdin=subprocess.DEVNULL)
                with containment as limits:
                    reader = threading.Thread(target=self._event_reader,
                                              args=(process.stdout, events), daemon=True)
                    reader.start()
                    (folder / "start").touch()
                    deadline = time.monotonic() + self.settings.deadline_seconds
                    failure = None
                    while True:
                        while True:
                            try:
                                event = events.get_nowait()
                            except queue.Empty:
                                break
                            with self.lock:
                                phase = event.get("phase")
                                if isinstance(phase, str):
                                    job["phase"] = phase[:80]
                                if phase == "denoising":
                                    job["progress"] = {"step": int(event.get("step", 0)), "total_steps": 8}
                                if phase == "completed" and event.get("scheduler_verified") is True:
                                    completion_seen = True
                                if phase == "failed":
                                    job["runtime_error"] = str(event.get("error", "Generation failed"))[:1500]
                        if job["cancel"].is_set() or self.closed.is_set():
                            failure = ("cancelled", "cancelled", "Generation cancelled")
                            break
                        if time.monotonic() >= deadline:
                            failure = ("failed", "deadline_exceeded", "Generation exceeded its time limit")
                            break
                        if process.poll() is not None:
                            reader.join(timeout=1)
                            # Drain final events after the writer has closed.
                            while not events.empty():
                                event = events.get_nowait()
                                if event.get("phase") == "completed" and event.get("scheduler_verified") is True:
                                    completion_seen = True
                                elif event.get("phase") == "failed":
                                    job["runtime_error"] = str(event.get("error", "Generation failed"))[:1500]
                            break
                        try:
                            memory = limits.memory()
                        except ProcessLimitError:
                            if process.poll() is not None:
                                continue
                            raise
                        with self.lock:
                            job["memory"] = {key: max(value, job["memory"].get(key, 0)) for key, value in memory.items()}
                        if max(memory.get(key, 0) for key in ("rss_bytes", "private_bytes", "peak_job_private_bytes")) >= self.settings.memory_bytes:
                            failure = ("failed", "memory_limit", "Generation exceeded its memory limit")
                            break
                        time.sleep(0.1)
                    if failure:
                        status, code, message = failure
                        with self.lock:
                            job.update(status=status, phase=status, error={"code": code, "message": message})
                    else:
                        output = folder / "video.mp4"
                        if process.returncode == 0 and completion_seen and output.is_file() and output.stat().st_size >= 64:
                            with self.lock:
                                job.update(status="completed", phase="completed")
                                self.inference_verified = True
                        else:
                            message = job.get("runtime_error", "Model generation failed; inspect local runtime.log")
                            memory_failure = (job["memory"].get("peak_job_private_bytes", 0) >= self.settings.memory_bytes * .94
                                              or any(word in message.lower() for word in ("bad_alloc", "out of memory", "cannot allocate", "not enough memory")))
                            with self.lock:
                                job.update(status="failed", phase="failed", error={
                                    "code": "memory_limit" if memory_failure else "inference_failed", "message": message})
        except ProcessLimitError as exc:
            with self.lock:
                job.update(status="failed", phase="failed", error={"code": "containment_failed", "message": str(exc)})
        except Exception as exc:
            with self.lock:
                job.update(status="failed", phase="failed", error={"code": "worker_failed", "message": str(exc)[:500]})
        finally:
            if process is not None:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=10)
                if process.stdout:
                    process.stdout.close()
            with self.lock:
                job["finished_at"] = time.time()
            # Sensitive prompts only need to exist while the child is running.
            (folder / "request.json").unlink(missing_ok=True)
            (folder / "video.partial.mp4").unlink(missing_ok=True)

    def _loop(self):
        while not self.closed.is_set():
            try:
                job_id = self.pending.get(timeout=.2)
            except queue.Empty:
                continue
            try:
                with self.lock:
                    job = self.jobs[job_id]
                    cancelled = job["cancel"].is_set()
                if not cancelled:
                    self._run(job)
                else:
                    (job["folder"] / "request.json").unlink(missing_ok=True)
            finally:
                self.pending.task_done()

    def close(self):
        self.closed.set()
        with self.lock:
            for job in self.jobs.values():
                if job["status"] not in TERMINAL:
                    job["cancel"].set()
        self.thread.join(timeout=15)
        for request in self.jobs_dir.glob("*/request.json"):
            request.unlink(missing_ok=True)


class WorkerHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 8

    def __init__(self, settings, manager=None, token=None):
        self.settings = settings
        self.manager = manager or JobManager(settings)
        self.token = token or secrets.token_urlsafe(32)
        super().__init__(("127.0.0.1", settings.port), Handler)
        self.origins = settings.allowed_origins | {f"http://127.0.0.1:{self.server_port}",
                                                   f"http://localhost:{self.server_port}"}

    def server_close(self):
        super().server_close()
        self.manager.close()


class Handler(BaseHTTPRequestHandler):
    server_version = "MITOMLocalVideo/1"
    sys_version = ""
    protocol_version = "HTTP/1.0"

    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def log_message(self, *_):
        pass

    def validate_request(self, *, authenticate=True):
        hosts = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
        if self.headers.get("Host") not in hosts or len(self.headers.get_all("Host", [])) != 1:
            raise APIError(403, "host_forbidden", "Only this loopback host is accepted")
        origin = self.headers.get("Origin")
        if len(self.headers.get_all("Origin", [])) > 1:
            raise APIError(403, "origin_forbidden", "Only one Origin is accepted")
        if origin is not None and origin not in self.server.origins:
            raise APIError(403, "origin_forbidden", "Origin is not allowed")
        if authenticate:
            if len(self.headers.get_all("Authorization", [])) > 1:
                raise APIError(401, "unauthorized", "Only one Authorization header is accepted")
            supplied = self.headers.get("Authorization", "")
            expected = "Bearer " + self.server.token
            if not hmac.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8")):
                raise APIError(401, "unauthorized", "Pair this client with the current worker token")
        path = urlsplit(self.path)
        if path.query or path.fragment or "%" in path.path or "\\" in path.path:
            raise APIError(400, "invalid_path", "Invalid API path")
        return path.path

    def cors_headers(self):
        origin = self.headers.get("Origin")
        if origin in self.server.origins:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")

    def respond(self, status, value):
        data = json.dumps(value, ensure_ascii=True).encode("utf-8")
        self.send_response(status)
        if getattr(self, "_static_request", False):
            self.document_headers()
        else:
            self.cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def document_headers(self):
        # In particular, do not add API CORS headers to the token-bearing HTML.
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; font-src 'self'; connect-src 'self'; img-src 'self' blob: data:; media-src 'self' blob:; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")

    def frontend(self, path):
        root = self.server.settings.frontend_dir
        document = path in ("/", "/index.html")
        if document:
            own_origin = "http://" + self.headers["Host"]
            origin = self.headers.get("Origin")
            if origin is not None and origin != own_origin:
                raise APIError(403, "document_origin_forbidden", "Open the local application directly")
            mode = self.headers.get("Sec-Fetch-Mode", "")
            destination = self.headers.get("Sec-Fetch-Dest", "")
            site = self.headers.get("Sec-Fetch-Site", "")
            navigation = mode == "navigate" and destination == "document" and origin is None
            if site in ("cross-site", "same-site") and not navigation:
                raise APIError(403, "document_fetch_forbidden", "Cross-origin document fetch is forbidden")
            if mode in ("cors", "no-cors") and origin != own_origin and site != "same-origin":
                raise APIError(403, "document_fetch_forbidden", "Cross-origin document fetch is forbidden")
        relative = "index.html" if document else path.lstrip("/")
        file = (root / relative).resolve()
        if not file.is_relative_to(root) or not file.is_file() or file.stat().st_size > 8 * 1024**2:
            raise APIError(404, "frontend_file_not_found", "Application resource is unavailable")
        if document:
            try:
                source = file.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as exc:
                raise APIError(503, "frontend_invalid", "Local application document is invalid") from exc
            meta = '<meta name="mitom-session-token" content="' + html.escape(self.server.token, quote=True) + '">'
            pattern = r'<meta\s+name=[\"\']mitom-session-token[\"\']\s+content=[\"\'][^\"\']*[\"\']\s*/?>'
            source, replaced = re.subn(pattern, lambda _: meta, source, count=1, flags=re.IGNORECASE)
            if not replaced:
                source, replaced = re.subn(r"</head\s*>", lambda match: meta + match.group(), source, count=1, flags=re.IGNORECASE)
            if not replaced:
                raise APIError(503, "frontend_invalid", "Local application document has no HTML head")
            data = source.encode("utf-8")
            content_type = "text/html; charset=utf-8"
        else:
            data = file.read_bytes()
            content_type = FRONTEND_ASSETS[path]
        self.send_response(200)
        self.document_headers()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def body(self, limit, expected_type=None):
        if self.headers.get("Transfer-Encoding"):
            raise APIError(400, "invalid_framing", "Transfer-Encoding is unsupported")
        lengths = self.headers.get_all("Content-Length", [])
        if not lengths:
            raise APIError(411, "length_required", "Content-Length is required")
        if len(lengths) != 1 or not re.fullmatch(r"[0-9]+", lengths[0]):
            raise APIError(400, "invalid_framing", "Invalid Content-Length")
        size = int(lengths[0])
        if size > limit:
            raise APIError(413, "body_too_large", "Request exceeds the size limit")
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if expected_type and content_type != expected_type:
            raise APIError(415, "unsupported_content_type", f"Use {expected_type}")
        data = self.rfile.read(size)
        if len(data) != size:
            raise APIError(400, "incomplete_body", "Request body was incomplete")
        return data, content_type

    def do_OPTIONS(self):
        try:
            self.validate_request(authenticate=False)
            if self.headers.get("Origin") not in self.server.origins:
                raise APIError(403, "origin_forbidden", "Preflight requires an allowed origin")
            method = self.headers.get("Access-Control-Request-Method", "")
            requested_headers = {value.strip().lower() for value in self.headers.get("Access-Control-Request-Headers", "").split(",") if value.strip()}
            if method not in {"GET", "POST"} or requested_headers - {"authorization", "content-type"}:
                raise APIError(403, "preflight_forbidden", "Unsupported preflight")
            self.send_response(204)
            self.cors_headers()
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
            if self.headers.get("Access-Control-Request-Private-Network") == "true":
                self.send_header("Access-Control-Allow-Private-Network", "true")
            self.send_header("Content-Length", "0")
            self.end_headers()
        except APIError as exc:
            self.respond(exc.status, {"error": {"code": exc.code, "message": exc.message}})

    def do_GET(self):
        try:
            raw_path = urlsplit(self.path).path
            self._static_request = (self.server.settings.frontend_dir is not None
                                    and (raw_path in ("/", "/index.html") or raw_path in FRONTEND_ASSETS))
            path = self.validate_request(authenticate=False)
            if self._static_request:
                self.frontend(path)
                return
            if path == "/v1/health":
                health = self.server.manager.health()
                if self.headers.get("Authorization") is None:
                    self.respond(200, {"version": 1, "model_state": health["status"],
                                       "pairing_required": True})
                else:
                    self.validate_request()
                    self.respond(200, health)
                return
            self.validate_request()
            matched = re.fullmatch(r"/v1/jobs/([a-f0-9]{32})(/video)?", path)
            if not matched:
                raise APIError(404, "not_found", "API endpoint not found")
            job_id, video = matched.groups()
            if not video:
                self.respond(200, self.server.manager.snapshot(job_id))
                return
            file = self.server.manager.video(job_id)
            self.send_response(200)
            self.cors_headers()
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Disposition", f'attachment; filename="MITOM-{job_id}.mp4"')
            self.send_header("Content-Length", str(file.stat().st_size))
            self.end_headers()
            with file.open("rb") as stream:
                while data := stream.read(64 * 1024):
                    self.wfile.write(data)
        except APIError as exc:
            self.respond(exc.status, {"error": {"code": exc.code, "message": exc.message}})
        except (ConnectionError, TimeoutError):
            pass

    def do_POST(self):
        try:
            path = self.validate_request()
            if path == "/v1/uploads":
                data, content_type = self.body(MAX_IMAGE_BYTES)
                self.respond(201, self.server.manager.upload(data, content_type))
            elif path == "/v1/jobs":
                data, _ = self.body(MAX_JSON_BYTES, "application/json")
                try:
                    value = json.loads(data.decode("utf-8"))
                except (UnicodeError, ValueError) as exc:
                    raise APIError(400, "invalid_json", "Body must be valid JSON") from exc
                self.respond(202, self.server.manager.create(value))
            elif matched := re.fullmatch(r"/v1/jobs/([a-f0-9]{32})/cancel", path):
                data, _ = self.body(MAX_JSON_BYTES, "application/json")
                if data.strip() not in (b"{}", b""):
                    raise APIError(400, "invalid_request", "Cancel body must be an empty object")
                self.respond(200, self.server.manager.cancel(matched.group(1)))
            else:
                raise APIError(404, "not_found", "API endpoint not found")
        except APIError as exc:
            self.respond(exc.status, {"error": {"code": exc.code, "message": exc.message}})
        except (ConnectionError, TimeoutError):
            pass


def open_local_browser_once(server, enabled):
    if not enabled or getattr(server, "_browser_open_requested", False):
        return None
    server._browser_open_requested = True
    # The server is already bound/listening. Opening in a daemon thread lets
    # serve_forever begin immediately, even if the OS browser helper blocks.
    thread = threading.Thread(target=webbrowser.open_new_tab,
                              args=(f"http://127.0.0.1:{server.server_port}/",),
                              name="mitom-open-local-page", daemon=True)
    thread.start()
    return thread


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--ffmpeg", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, default=Path(__file__).parent / "data")
    parser.add_argument("--python", default=sys.executable, help="Python with the reviewed CPU inference dependencies")
    parser.add_argument("--port", type=int, default=4176)
    parser.add_argument("--memory-gib", type=float, default=10)
    parser.add_argument("--deadline-seconds", type=int, default=1800)
    parser.add_argument("--frontend", type=Path, help="Fixed local application directory; only explicit application assets are served")
    parser.add_argument("--open-browser", action="store_true", help="Open the local application once after the server binds")
    args = parser.parse_args()
    if args.open_browser and args.frontend is None:
        parser.error("--open-browser requires --frontend")
    settings = Settings(args.data_dir, args.model, args.ffmpeg, python=args.python,
                        port=args.port, memory_bytes=int(args.memory_gib * 1024**3),
                        deadline_seconds=args.deadline_seconds, frontend_dir=args.frontend)
    server = WorkerHTTPServer(settings)
    print(f"MITOM Local Video (unpublished CPU probe): http://127.0.0.1:{server.server_port}")
    if settings.frontend_dir is None:
        print("Pairing token: " + server.token)
    else:
        print("Open the local application URL above; pairing is confined to its local document.")
    print(json.dumps(server.manager.health(), indent=2))
    print("This is a foreground process. Ctrl+C stops it and cancels generation.")
    open_local_browser_once(server, args.open_browser)
    try:
        server.serve_forever(poll_interval=.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
