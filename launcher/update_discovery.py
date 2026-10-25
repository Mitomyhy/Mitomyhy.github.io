"""Idle, metadata-only signed update discovery for the desktop launcher.

Workers never call Tk, install components, download executables, or stop apps.
Only the Tk polling callback applies verified metadata while the app is idle.
"""
from __future__ import annotations

import json
import queue
import threading
import time
import urllib.request

STARTUP_DELAY_MS = 1500
CHECK_INTERVAL_MS = 300000
BUSY_RETRY_MS = 15000
RESULT_POLL_MS = 100
PROBE_TIMEOUT_SECONDS = 12


def probe_signed_release(backend, cancelled=None):
    cancelled = cancelled or threading.Event()
    if cancelled.is_set():
        raise RuntimeError("Update discovery cancelled.")
    key = backend.load_public_key()
    url = backend.validate_https_url(backend.release_feed_url())
    request = urllib.request.Request(url, headers={
        "User-Agent": "MITOM/" + backend.APP_VERSION, "Accept": "application/json",
        "Cache-Control": "no-cache", "Pragma": "no-cache"})
    opener = urllib.request.build_opener(backend.HttpsRedirectHandler())
    deadline = time.monotonic() + PROBE_TIMEOUT_SECONDS
    chunks, size = [], 0
    with opener.open(request, timeout=PROBE_TIMEOUT_SECONDS) as response:
        backend.validate_https_url(response.geturl())
        read = getattr(response, "read1", response.read)
        while True:
            if cancelled.is_set() or time.monotonic() > deadline:
                raise RuntimeError("Update discovery cancelled or timed out.")
            chunk = read(min(65536, backend.FEED_LIMIT + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > backend.FEED_LIMIT:
                raise backend.LauncherError("Update metadata exceeds the supported size.")
    return backend.verify_release_envelope(json.loads(b"".join(chunks).decode("utf-8-sig")), key)


def start_daemon(work):
    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    return thread


def apply_verified_release(app, release, original_check, backend):
    """Reuse the original update callback, without a second network request."""
    if threading.get_ident() != app._r4_ui_thread:
        raise RuntimeError("Update UI results must be applied on the Tk thread.")
    if app.busy:
        raise RuntimeError("Cannot apply update metadata during a transaction.")
    had_override = "_run" in app.__dict__
    previous = app.__dict__.get("_run")

    def dispatch(_message, work, callback=None):
        if work is not backend.fetch_release or not callable(callback):
            raise RuntimeError("Unexpected original update callback contract.")
        callback(release)

    app._run = dispatch
    try:
        original_check(app)
    finally:
        if had_override:
            app._run = previous
        else:
            del app._run


class UpdateDiscovery:
    def __init__(self, root, is_busy, probe, apply, log, worker=start_daemon):
        self.root, self.is_busy, self.probe, self.apply, self.log = root, is_busy, probe, apply, log
        self.worker = worker
        self._closed = self._inflight = False
        self._revision = 0
        self._cancelled = threading.Event()
        self._results = queue.Queue()
        self._timer = self._poll_timer = self._pending = None

    def start(self):
        if not self._closed and self._timer is None:
            self._schedule(STARTUP_DELAY_MS)

    def _schedule(self, delay):
        if not self._closed:
            self._timer = self.root.after(delay, self._begin)

    def _poll_later(self):
        if not self._closed:
            self._poll_timer = self.root.after(RESULT_POLL_MS, self._poll)

    def _begin(self):
        self._timer = None
        if self._closed:
            return
        if self.is_busy() or self._inflight:
            self._schedule(BUSY_RETRY_MS)
            return
        self._inflight = True
        revision = self._revision
        cancelled = self._cancelled = threading.Event()

        def work():
            try:
                result = (revision, True, self.probe(cancelled))
            except Exception as exc:
                result = (revision, False, str(exc)[:320])
            self._results.put(result)

        self.worker(work)
        self._poll_later()

    def _poll(self):
        self._poll_timer = None
        if self._closed:
            return
        if self._pending is None:
            try:
                revision, ok, result = self._results.get_nowait()
            except queue.Empty:
                if self._inflight:
                    self._poll_later()
                return
            self._inflight = False
            if revision != self._revision:
                self._schedule(CHECK_INTERVAL_MS)
                return
            if not ok:
                self.log("Automatic update discovery unavailable: " + result)
                self._schedule(CHECK_INTERVAL_MS)
                return
            self._pending = (revision, result)
        if self.is_busy():
            self._poll_later()
            return
        revision, release = self._pending
        self._pending = None
        if revision == self._revision:
            try:
                self.apply(release)
            except Exception as exc:
                self.log("Automatic update metadata was not applied: " + str(exc)[:320])
        self._schedule(CHECK_INTERVAL_MS)

    def supersede(self):
        self._revision += 1
        self._cancelled.set()
        self._pending = None

    def close(self):
        self._closed = True
        self.supersede()
        for timer in (self._timer, self._poll_timer):
            if timer is not None:
                self.root.after_cancel(timer)
        self._timer = self._poll_timer = None
