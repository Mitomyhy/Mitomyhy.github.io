"""MITOM Product Video: a session-authenticated loopback service on port4177."""
from pathlib import Path
import argparse, collections, io, json, os, queue, re, secrets, shutil, subprocess, sys, threading, time, warnings
import urllib.request
import webbrowser

sys.path.insert(0, str(Path(__file__).resolve().parent))
import worker_server as base
from process_limits import start_contained_process, ProcessLimitError

VERSION = '1.0.0'


def reopen_running_service(port):
    """Reuse our authenticated loopback app without disturbing active jobs."""
    url = f'http://127.0.0.1:{port}'
    try:
        # Bypass system proxies for this fixed loopback endpoint only.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(url + '/', timeout=2) as response:
            document = response.read(128 * 1024).decode('utf-8')
        match = re.search(r'name="mitom-session-token" content="([A-Za-z0-9_-]{32,128})"', document)
        if not match:
            return False
        request = urllib.request.Request(url + '/v1/health', headers={'Authorization': 'Bearer ' + match[1]})
        with opener.open(request, timeout=2) as response:
            health = json.loads(response.read(65536))
        if health.get('service') != 'MITOM Product Video' or health.get('api_version') != 1:
            return False
    except (OSError, ValueError, UnicodeError):
        return False
    webbrowser.open(url + '/')
    return True


def options_for(body):
    options = body.get('options', {})
    if not isinstance(options, dict) or set(options) - {'aspect_ratio', 'duration_seconds', 'device', 'resolution', 'seed'}:
        raise base.APIError(400, 'invalid_options', 'Unknown video option')
    ratio = options.get('aspect_ratio', '16:9')
    duration = options.get('duration_seconds', 10)
    resolution = options.get('resolution', 1080)
    if ratio not in ('16:9', '9:16') or type(duration) is not int or duration not in (5, 8, 10, 15) or type(resolution) is not int or resolution not in (720, 1080):
        raise base.APIError(400, 'invalid_options', 'Choose 16:9 or 9:16, 5/8/10/15 seconds, and 720/1080p')
    if options.get('device', 'cpu') != 'cpu':
        raise base.APIError(400, 'device_unavailable', 'This installed local AI runtime supports CPU')
    return {'aspect_ratio': ratio, 'duration_seconds': duration, 'resolution': resolution,
            'device': 'cpu', 'seed': base.integer(options.get('seed', 42), 'seed', 0, 2**31-1)}


class ProductManager(base.JobManager):
    def health(self):
        settings = self.settings
        render_ready = all(Path(x).is_file() for x in (settings.node, settings.browser, settings.renderer))
        from prompt_planner import planner_status
        ai = planner_status(settings.ai_runtime) if settings.ai_runtime else {'available': False}
        ai_ready = ai['available']
        can_setup = False
        if render_ready and not ai_ready and getattr(settings, 'ai_stub', None):
            try:
                from product_setup import stub_manifest
                stub_manifest(settings.ai_stub)
                can_setup = True
            except (OSError, ValueError, KeyError):
                pass
        enabled = render_ready and (ai_ready or can_setup)
        state = 'ready' if enabled else 'planner_missing' if not ai_ready else 'renderer_missing'
        return {'service': 'MITOM Product Video', 'version': VERSION, 'api_version': 1,
                'status': state, 'generation_enabled': enabled, 'device': 'CPU', 'cloud': False,
                'product_studio': {'generation_enabled': enabled, 'needs_setup': not ai_ready, 'can_setup': can_setup,
                    'engine': 'remotion', 'renderer_version': '4.0.539',
                    'devices': ['cpu'], 'aspect_ratios': ['16:9', '9:16'], 'durations': [5, 8, 10, 15],
                    'resolutions': [720, 1080], 'prompt_planner': 'local_qwen_vision' if ai_ready else 'missing',
                    'background_mode': 'ltx_local_cpu' if settings.ltx else 'rendered_scene',
                    'product_motion': 'artwork_preserving_2.5d', 'model_download_required': not ai_ready},
                'memory_limit_bytes': settings.memory_bytes, 'deadline_seconds': settings.deadline_seconds}

    def upload(self, data, content_type):
        result = super().upload(data, content_type)
        # The legacy worker normalizes RGB for diffusion. This separate service
        # retains normalized alpha for product-preserving composition.
        from PIL import Image, ImageOps
        with Image.open(io.BytesIO(data)) as image:
            normalized = ImageOps.exif_transpose(image).convert('RGBA')
            normalized.thumbnail((2048, 2048))
            normalized.save(self.uploads[result['image_id']], format='PNG')
        return result

    def create(self, body):
        if not isinstance(body, dict) or set(body) - {'image_id', 'background_id', 'prompt', 'options'}:
            raise base.APIError(400, 'invalid_request', 'Invalid product video request')
        image_id = base.safe_id(body.get('image_id'))
        background_id = base.safe_id(body['background_id']) if body.get('background_id') else None
        prompt = body.get('prompt')
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 4000 or '\0' in prompt:
            raise base.APIError(400, 'invalid_prompt', 'Enter a prompt of 1–4000 characters')
        options = options_for(body)
        health = self.health()
        if not health['generation_enabled']:
            raise base.APIError(503, health['status'], 'Prepare the local AI/runtime before generating. Images are not sent to the cloud.')
        with self.lock:
            if self.closed.is_set():
                raise base.APIError(503, 'shutting_down', 'Worker is shutting down')
            if image_id not in self.uploads or background_id and background_id not in self.uploads:
                raise base.APIError(404, 'image_not_found', 'Image is not in this session')
            if sum(job['status'] not in base.TERMINAL for job in self.jobs.values()) >= 4 or len(self.jobs) >= 40:
                raise base.APIError(429, 'queue_full', 'The local queue is full')
            job_id = secrets.token_hex(16)
            folder = self.jobs_dir / job_id
            folder.mkdir()
            job = {'job_id': job_id, 'status': 'queued', 'phase': 'queued', 'created_at': time.time(),
                   'options': options, 'device': 'CPU', 'progress': {}, 'memory': {}, 'cancel': threading.Event(), 'folder': folder}
            request = {'image': str(self.uploads[image_id]), 'prompt': prompt.strip(), 'options': options,
                       'background_image': str(self.uploads[background_id]) if background_id else None,
                       'ai_runtime': str(self.settings.ai_runtime), 'node': str(self.settings.node),
                       'needs_setup': health['product_studio']['needs_setup'],
                       'ai_stub': str(getattr(self.settings, 'ai_stub', '')),
                       'browser': str(self.settings.browser), 'renderer': str(self.settings.renderer),
                       'mask_model': str(self.settings.mask_model), 'ltx': self.settings.ltx}
            (folder / 'request.json').write_text(json.dumps(request), encoding='utf-8')
            self.jobs[job_id] = job
            self.pending.put_nowait(job_id)
            return self.snapshot(job_id)

    def _run(self, job):
        folder, process = job['folder'], None
        completion = None
        events = queue.Queue(maxsize=2048)
        try:
            job.update(status='running', phase='starting', started_at=time.time())
            command = [self.settings.python, '-I', '-B', '-u', str(Path(__file__).with_name('product_job.py')),
                       '--request', str(folder / 'request.json'), '--start-marker', str(folder / 'start')]
            with (folder / 'runtime.log').open('w', encoding='utf-8') as log:
                process, containment = start_contained_process(command, self.settings.memory_bytes,
                    stdout=subprocess.PIPE, stderr=log, stdin=subprocess.DEVNULL, text=True, encoding='utf-8', errors='replace')
                with containment as limits:
                    reader = threading.Thread(target=self._event_reader, args=(process.stdout, events), daemon=True)
                    reader.start()
                    (folder / 'start').touch()
                    deadline = time.monotonic() + self.settings.deadline_seconds
                    while True:
                        if job['cancel'].is_set() or self.closed.is_set():
                            raise InterruptedError('Generation cancelled')
                        if time.monotonic() > deadline:
                            raise TimeoutError('Local video generation timed out')
                        while not events.empty():
                            event = events.get_nowait()
                            phase = event.get('phase')
                            if isinstance(phase, str):
                                job['phase'] = phase[:80]
                            if isinstance(event.get('progress'), (int, float)):
                                job['progress'] = {'ratio': max(0, min(1, event['progress'])), 'stage': phase,
                                                   **{key: event[key] for key in ('downloaded_bytes', 'total_bytes') if type(event.get(key)) is int}}
                            if phase == 'completed' and event.get('ok') is True:
                                completion = event
                            if phase == 'failed':
                                job['runtime_error'] = str(event.get('error', 'Generation failed'))[:1000]
                        if process.poll() is not None:
                            reader.join(timeout=1)
                            if not events.empty():
                                continue
                            break
                        memory = limits.memory()
                        job['memory'] = {key: max(value, job['memory'].get(key, 0)) for key, value in memory.items()}
                        if max(memory.get(key, 0) for key in ('rss_bytes', 'private_bytes', 'peak_job_private_bytes')) >= self.settings.memory_bytes:
                            raise MemoryError('Video job exceeded its memory limit')
                        time.sleep(.1)
                    output = folder / 'video.mp4'
                    if process.returncode or not completion or not output.is_file() or output.stat().st_size < 128:
                        raise RuntimeError(job.get('runtime_error', 'Local render did not complete'))
                    if output.stat().st_size > 100 * 1024**2:
                        raise RuntimeError('MP4 exceeds the Etsy 100 MB limit')
                    job.update(status='completed', phase='completed', progress={'ratio': 1, 'stage': 'completed'},
                               result={key: value for key, value in completion.items() if key not in ('phase', 'ok')})
        except BaseException as exc:
            code = 'cancelled' if isinstance(exc, InterruptedError) else 'deadline_exceeded' if isinstance(exc, TimeoutError) else 'memory_limit' if isinstance(exc, MemoryError) else 'generation_failed'
            job.update(status='cancelled' if code == 'cancelled' else 'failed', phase=code,
                       error={'code': code, 'message': str(exc)[:1200]})
        finally:
            if process is not None:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=10)
                if process.stdout:
                    process.stdout.close()
            job['finished_at'] = time.time()
            (folder / 'request.json').unlink(missing_ok=True)


class ProductHandler(base.Handler):
    server_version = 'MITOMProductVideo/1'

    def do_POST(self):
        if self.path == '/v1/product-jobs':
            self.path = '/v1/jobs'
        return super().do_POST()


class ProductServer(base.WorkerHTTPServer):
    def __init__(self, settings, manager=None, token=None):
        self.settings = settings
        self.manager = manager or ProductManager(settings)
        self.token = token or secrets.token_urlsafe(32)
        base.ThreadingHTTPServer.__init__(self, ('127.0.0.1', settings.port), ProductHandler)
        self.origins = settings.allowed_origins | {f'http://127.0.0.1:{self.server_port}', f'http://localhost:{self.server_port}'}


def main():
    root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frontend', type=Path, default=root / 'web')
    parser.add_argument('--data-dir', type=Path, default=Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'MITOM/ProductVideo/session-data')
    parser.add_argument('--ai-runtime', type=Path)
    parser.add_argument('--ai-stub', type=Path, default=root / 'ai-runtime')
    parser.add_argument('--ltx-root', type=Path)
    parser.add_argument('--ltx-model', type=Path)
    parser.add_argument('--ltx-python', type=Path)
    parser.add_argument('--ffmpeg', type=Path)
    parser.add_argument('--node', type=Path, default=root / 'node/node.exe')
    parser.add_argument('--browser', type=Path)
    parser.add_argument('--renderer', type=Path, default=root / 'renderer/render.mjs')
    parser.add_argument('--mask-model', type=Path, default=root / 'models/u2netp.onnx')
    parser.add_argument('--port', type=int, default=4177)
    parser.add_argument('--open-browser', action='store_true')
    args = parser.parse_args()
    if args.open_browser and reopen_running_service(args.port):
        return
    browser = args.browser or next((Path(p) for p in [os.environ.get('PROGRAMFILES(X86)', 'C:/Program Files (x86)') + '/Microsoft/Edge/Application/msedge.exe', os.environ.get('PROGRAMFILES', 'C:/Program Files') + '/Google/Chrome/Application/chrome.exe'] if Path(p).is_file()), root / 'browser-missing.exe')
    settings = base.Settings(args.data_dir, root / 'models', root / 'ffmpeg/ffmpeg.exe',
                             python=sys.executable, port=args.port, frontend_dir=args.frontend.resolve(), deadline_seconds=1800)
    settings.node, settings.browser, settings.renderer = args.node.resolve(), browser.resolve(), args.renderer.resolve()
    settings.mask_model = args.mask_model.resolve()
    from prompt_planner import discover_runtime, PlannerError
    try:
        settings.ai_runtime = discover_runtime(args.ai_runtime)
    except (PlannerError, OSError):
        settings.ai_runtime = (args.ai_runtime or Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'MITOM/ProductVideo/ai-runtime').resolve()
    settings.ai_stub = args.ai_stub.resolve()
    settings.ltx = None
    if args.ltx_root:
        ltx = args.ltx_root.resolve()
        model, python, ffmpeg = ltx / 'models/ltx-video-cpu', ltx / 'python/python.exe', ltx / 'ffmpeg/ffmpeg.exe'
        if all(path.is_file() for path in (model / 'mitom-model.json', python, ffmpeg)):
            settings.ltx = {'model': str(model), 'python': str(python), 'ffmpeg': str(ffmpeg)}
    if args.ltx_model and args.ltx_python and args.ffmpeg:
        if all(path.is_file() for path in (args.ltx_model / 'mitom-model.json', args.ltx_python, args.ffmpeg)):
            settings.ltx = {'model': str(args.ltx_model.resolve()), 'python': str(args.ltx_python.resolve()), 'ffmpeg': str(args.ffmpeg.resolve())}
    base.FRONTEND_ASSETS = {'/app.js': 'application/javascript', '/client.mjs': 'application/javascript', '/style.css': 'text/css'}
    server = ProductServer(settings)
    print(f'MITOM Product Video {VERSION}: http://127.0.0.1:{server.server_port}/', flush=True)
    if args.frontend:
        print('Pairing is confined to the local document.', flush=True)
    else:
        print('Pairing token: ' + server.token, flush=True)
    base.open_local_browser_once(server, args.open_browser)
    try:
        server.serve_forever(poll_interval=.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
