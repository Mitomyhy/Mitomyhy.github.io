"""Offline desktop UI served exclusively on authenticated IPv4 loopback."""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import multiprocessing
import os
from pathlib import Path
import queue
import re
import secrets
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import unquote, urlsplit
import uuid
import webbrowser
import zipfile

try:
    from . import engine
    from .app_version import VERSION
    from .worker import run_engine
except ImportError:
    import engine
    from app_version import VERSION
    from worker import run_engine

RESOURCES = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
MAX_UPLOAD = 32 * 1024 * 1024
FORMATS = {'svg', 'pdf', 'eps'}
ID_RE = re.compile(r'^[a-f0-9]{32}$')

class Session:
    def __init__(self):
        self.token = secrets.token_hex(32)
        self.temp = tempfile.TemporaryDirectory(prefix='vector-migic-')
        self.root = Path(self.temp.name)
        self.images, self.jobs = {}, {}
        self.lock = threading.RLock()
        self.export_lock = threading.Lock()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='vector-migic')
        self.last_request = time.monotonic()
        self.closed = False

    def upload(self, name, data):
        suffix = Path(name.replace('\\', '/')).suffix.lower()
        if suffix not in {'.png', '.jpg', '.jpeg', '.webp', '.bmp', '.tif', '.tiff'}:
            raise ValueError('Choose a PNG, JPG, WebP, BMP, TIFF image.')
        with self.lock:
            if len(self.images) >= 30:
                raise ValueError('This session has 30 images. Restart the app to begin a fresh session.')
        identity = uuid.uuid4().hex
        folder = self.root / identity
        folder.mkdir()
        source, preview = folder / ('source' + suffix), folder / 'preview.png'
        try:
            source.write_bytes(data)
            info = engine.inspect_image(source)
            engine.create_preview(source, preview, max_dimension=1600)
        except Exception:
            source.unlink(missing_ok=True)
            preview.unlink(missing_ok=True)
            folder.rmdir()
            raise
        clean_name = Path(name.replace('\\', '/')).name[:180] or 'Image'
        value = {'id': identity, 'name': clean_name, 'info': info, 'source': source, 'preview': preview}
        with self.lock:
            self.images[identity] = value
        return {'id': identity, 'name': clean_name, 'info': info}

    def submit(self, identity, options):
        with self.lock:
            image = self.images.get(identity)
            if image is None:
                raise ValueError('Choose an image first.')
            if len(self.jobs) >= 100:
                raise ValueError('This session has 100 conversions. Restart the app for a fresh session.')
            if sum(j['status'] in {'queued', 'running'} for j in self.jobs.values()) >= 30:
                raise ValueError('The conversion queue is full.')
            normalized = engine.normalize_options(options)
            key = uuid.uuid4().hex
            folder = self.root / key
            folder.mkdir()
            job = {'id': key, 'image_id': identity, 'name': image['name'], 'status': 'queued',
                   'progress': 0, 'message': 'Waiting in queue', 'metrics': None,
                   'output': folder / 'result.svg', 'cancel': threading.Event(), 'options': normalized}
            self.jobs[key] = job
            self.executor.submit(self._convert, job, image)
            return self.snapshot(key)

    def _convert(self, job, image):
        if job['cancel'].is_set():
            return
        process, messages = None, None
        with self.lock:
            job.update(status='running', message='Preparing image')
        try:
            context = multiprocessing.get_context('spawn')
            messages = context.Queue()
            process = context.Process(target=run_engine,
                args=(str(image['source']), str(job['output']), job['options'], messages), daemon=True)
            process.start()
            deadline = time.monotonic() + 900
            while True:
                if job['cancel'].is_set():
                    process.terminate()
                    with self.lock:
                        job.update(status='cancelled', message='Cancelled')
                    break
                if time.monotonic() >= deadline:
                    raise TimeoutError('This conversion exceeded 15 minutes. Try fewer colors or a smaller processing size.')
                try:
                    event = messages.get(timeout=.2)
                except queue.Empty:
                    if not process.is_alive():
                        try:
                            event = messages.get(timeout=.5)
                        except queue.Empty:
                            raise RuntimeError('The tracing process stopped. Try a smaller image or fewer colors.')
                    else:
                        continue
                with self.lock:
                    if event['kind'] == 'progress':
                        job['progress'] = max(0, min(99, int(event.get('progress', 0))))
                        job['message'] = str(event.get('message', 'Tracing'))[:300]
                    elif event['kind'] == 'done':
                        job.update(status='done', progress=100, message='Vector ready', metrics=event['metrics'])
                        break
                    else:
                        raise RuntimeError(event.get('message', 'Conversion failed.'))
        except Exception as exc:
            with self.lock:
                job.update(status='error', message=str(exc)[:1200])
        finally:
            if process is not None and process.pid is not None:
                process.join(timeout=2)
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=2)
                process.close()
            if messages is not None:
                messages.close()

    def snapshot(self, identity):
        with self.lock:
            job = self.jobs.get(identity)
            if not job:
                raise ValueError('Conversion not found.')
            return {key: job[key] for key in ('id', 'image_id', 'name', 'status', 'progress', 'message', 'metrics', 'options')}

    def cancel(self, identity):
        with self.lock:
            job = self.jobs.get(identity)
            if not job:
                raise ValueError('Conversion not found.')
            if job['status'] in {'running', 'queued'}:
                job['cancel'].set()
                job.update(status='cancelled', message='Cancelled')
        return self.snapshot(identity)

    def export(self, identity, format):
        if format not in FORMATS:
            raise ValueError('Export format must be SVG, PDF or EPS.')
        with self.lock:
            job = self.jobs.get(identity)
            if not job or job['status'] != 'done':
                raise ValueError('Vectorize the image before exporting.')
        with self.export_lock:
            output = job['output'].with_suffix('.' + format)
            if not output.exists():
                engine.export_vector(job['output'], output, format)
            data = output.read_bytes()
        name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', Path(job['name']).stem)[:120] or 'VectorMigic'
        return data, name + '.' + format

    def close(self):
        self.closed = True
        for job in list(self.jobs.values()):
            job['cancel'].set()
        self.executor.shutdown(wait=True, cancel_futures=True)
        self.temp.cleanup()

def handler_for(session):
    class Handler(BaseHTTPRequestHandler):
        server_version = 'VectorMigic/' + VERSION
        def log_message(self, *args):
            pass

        def send_data(self, status, data, mime, filename=None):
            self.send_response(status)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob: data:; font-src 'self'; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'")
            if filename:
                self.send_header('Content-Disposition', 'attachment; filename="' + filename.encode('ascii', 'replace').decode() + '"')
            self.end_headers()
            self.wfile.write(data)

        def json(self, status, value):
            self.send_data(status, json.dumps(value, ensure_ascii=False).encode(), 'application/json; charset=utf-8')

        def authorize(self):
            expected = 'http://127.0.0.1:' + str(self.server.server_port)
            if self.headers.get('Origin') not in (None, expected):
                raise PermissionError('This desktop session only accepts its own window.')
            host = self.headers.get('Host', '')
            if host != '127.0.0.1:' + str(self.server.server_port):
                raise PermissionError('Invalid local host.')
            supplied = self.headers.get('X-Migic-Token', '')
            if not secrets.compare_digest(supplied, session.token):
                raise PermissionError('Reopen Vector Migic from the launcher to reconnect this window.')
            session.last_request = time.monotonic()

        def read_body(self, maximum):
            if self.headers.get('Transfer-Encoding'):
                raise ValueError('Chunked uploads are not supported.')
            length = int(self.headers.get('Content-Length', '0'))
            if length < 1 or length > maximum:
                raise ValueError('File is empty or exceeds the 32 MB upload limit.')
            data = self.rfile.read(length)
            if len(data) != length:
                raise ValueError('The upload was interrupted.')
            return data

        def do_GET(self):
            try:
                route = urlsplit(self.path).path
                if route in ('/', '/index.html'):
                    return self.send_data(200, (RESOURCES / 'static/index.html').read_bytes(), 'text/html; charset=utf-8')
                static = {'/static/app.js': ('static/app.js', 'text/javascript; charset=utf-8'),
                          '/static/style.css': ('static/style.css', 'text/css; charset=utf-8'),
                          '/assets/icon.png': ('assets/icon.png', 'image/png'),
                          '/assets/body.ttf': ('assets/body.ttf', 'font/ttf'),
                          '/assets/heading.ttf': ('assets/heading.ttf', 'font/ttf')}
                if route in static:
                    file, mime = static[route]
                    return self.send_data(200, (RESOURCES / file).read_bytes(), mime)
                self.authorize()
                if route == '/api/config':
                    return self.json(200, {'version': VERSION, 'presets': engine.PRESETS, 'max_upload': MAX_UPLOAD})
                if route == '/api/ping':
                    return self.json(200, {'ok': True})
                parts = route.strip('/').split('/')
                if len(parts) == 3 and parts[0] == 'api' and ID_RE.fullmatch(parts[2]):
                    key = parts[2]
                    if parts[1] == 'job':
                        return self.json(200, session.snapshot(key))
                    if parts[1] == 'source' and key in session.images:
                        return self.send_data(200, session.images[key]['preview'].read_bytes(), 'image/png')
                    if parts[1] == 'vector':
                        data, _ = session.export(key, 'svg')
                        return self.send_data(200, data, 'image/svg+xml')
                self.json(404, {'error': 'Not found.'})
            except PermissionError as exc:
                self.json(403, {'error': str(exc)})
            except (ValueError, KeyError) as exc:
                self.json(400, {'error': str(exc)})
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception:
                self.json(500, {'error': 'The local app could not complete this request.'})

        def do_POST(self):
            try:
                self.authorize()
                route = urlsplit(self.path).path
                if route == '/api/upload':
                    data = self.read_body(MAX_UPLOAD)
                    return self.json(200, session.upload(unquote(self.headers.get('X-File-Name', 'image.png')), data))
                request = json.loads(self.read_body(65536))
                if route == '/api/vectorize':
                    return self.json(202, session.submit(request.get('image_id'), request.get('options', {})))
                if route == '/api/cancel':
                    return self.json(200, session.cancel(request.get('job_id')))
                if route == '/api/export':
                    format = request.get('format', 'svg')
                    data, name = session.export(request.get('job_id'), format)
                    return self.send_data(200, data, {'svg': 'image/svg+xml', 'pdf': 'application/pdf', 'eps': 'application/postscript'}[format], name)
                if route == '/api/export-batch':
                    identities, format = request.get('job_ids'), request.get('format', 'svg')
                    if not isinstance(identities, list) or not 1 <= len(identities) <= 30 or len(set(identities)) != len(identities):
                        raise ValueError('Select up to 30 completed vectors.')
                    import io
                    output, names = io.BytesIO(), set()
                    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
                        for key in identities:
                            data, name = session.export(key, format)
                            if name in names:
                                name = key[:6] + '-' + name
                            names.add(name)
                            archive.writestr(name, data)
                    return self.send_data(200, output.getvalue(), 'application/zip', 'VectorMigic-' + format + '.zip')
                if route == '/api/shutdown':
                    self.json(200, {'ok': True})
                    threading.Thread(target=self.server.shutdown, daemon=True).start()
                    return
                self.json(404, {'error': 'Not found.'})
            except PermissionError as exc:
                self.json(403, {'error': str(exc)})
            except (ValueError, KeyError, TypeError) as exc:
                self.json(400, {'error': str(exc)})
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception as exc:
                self.json(500, {'error': str(exc)[:800]})
    return Handler

def open_window(url):
    for base in (os.environ.get('ProgramFiles(x86)'), os.environ.get('ProgramFiles')):
        if base:
            edge = Path(base) / 'Microsoft/Edge/Application/msedge.exe'
            if edge.is_file():
                subprocess.Popen([str(edge), '--app=' + url, '--new-window'], close_fds=True,
                                 creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                return
    webbrowser.open(url)

def self_test():
    from PIL import Image, ImageDraw
    with tempfile.TemporaryDirectory(prefix='vector-migic-test-') as directory:
        folder = Path(directory)
        image = Image.new('RGBA', (160, 120), (255, 255, 255, 0))
        draw = ImageDraw.Draw(image)
        draw.ellipse((15, 15, 110, 105), fill='#e06712')
        draw.rectangle((70, 35, 145, 90), fill='#1559df')
        source, svg = folder / 'input.png', folder / 'output.svg'
        image.save(source)
        metrics = engine.vectorize(source, svg, {'preset': 'logo', 'colors': 8})
        assert '<path' in svg.read_text() and '<image' not in svg.read_text()
        formats = {}
        for format in ('pdf', 'eps'):
            output = folder / ('output.' + format)
            engine.export_vector(svg, output, format)
            assert output.stat().st_size > 100
            formats[format] = output.stat().st_size
        return {'ok': True, 'version': VERSION, 'path_count': metrics['path_count'], 'exports': formats,
                'offline': True, 'resources_present': (RESOURCES / 'static/index.html').is_file()}

def main(argv=None):
    multiprocessing.freeze_support()
    parser = argparse.ArgumentParser()
    parser.add_argument('--self-test', action='store_true')
    parser.add_argument('--result-json')
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--info-json')
    parser.add_argument('--port', type=int, default=0)
    parser.add_argument('--idle-timeout', type=int, default=300)
    args = parser.parse_args(argv)
    if args.self_test:
        try:
            value = self_test()
        except Exception as exc:
            value = {'ok': False, 'error': str(exc)}
        if args.result_json:
            Path(args.result_json).write_text(json.dumps(value, indent=2), encoding='utf-8')
        if sys.stdout:
            print(json.dumps(value))
        return 0 if value['ok'] else 1
    session = Session()
    server = ThreadingHTTPServer(('127.0.0.1', args.port), handler_for(session))
    server.daemon_threads = True
    origin = 'http://127.0.0.1:' + str(server.server_port)
    url = origin + '/#token=' + session.token
    if args.info_json:
        Path(args.info_json).write_text(json.dumps({'url': url, 'origin': origin, 'token': session.token,
                                                  'pid': os.getpid()}), encoding='utf-8')
    def watchdog():
        while not session.closed:
            time.sleep(5)
            with session.lock:
                active = any(j['status'] in {'queued', 'running'} for j in session.jobs.values())
            if args.idle_timeout > 0 and not active and time.monotonic() - session.last_request > args.idle_timeout:
                server.shutdown()
                return
    threading.Thread(target=watchdog, daemon=True).start()
    if not args.no_browser:
        open_window(url)
    if sys.stdout:
        print('Vector Migic is ready at ' + origin, flush=True)
    try:
        server.serve_forever(poll_interval=.2)
    finally:
        server.server_close()
        session.close()
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
