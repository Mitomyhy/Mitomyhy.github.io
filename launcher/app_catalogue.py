"""Authenticated online app versions, independent of launcher rebuilds.

Only the locally bundled publisher key is trusted. The cache contains the
original signed envelope, and is reverified on every process startup.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import hmac
import json
import os
import re
from pathlib import Path
import tempfile
import threading
import time
import urllib.request

import launcher_channel
import mi_font_app
import vector_app
import video_app

CATALOGUE_URL = "https://mitomyhy.github.io/releases/apps-stable.json"
LIMIT = 262144
TIMEOUT = 12
MODULES = {"mifont": mi_font_app, "vector-migic": vector_app, "product-video": video_app}
REQUIRED_APPS = frozenset({"mifont", "vector-migic"})
ENTRIES = {"mifont": "MiFont.exe", "vector-migic": "VectorMigic.exe", "product-video": "python/python.exe"}


def verify_catalogue(backend, envelope):
    """Verify RSA/SHA256 before interpreting any executable descriptor."""
    try:
        key = launcher_channel.load_launcher_public_key()
        if (not isinstance(envelope, dict) or envelope.get("schema") != 1
                or envelope.get("key_id") != key["key_id"]):
            raise ValueError("publisher identity")
        payload = base64.b64decode(envelope["payload"], validate=True)
        signature = base64.b64decode(envelope["signature"], validate=True)
        if len(payload) > LIMIT:
            raise ValueError("payload limit")
        modulus = int(key["n"], 16)
        length = (modulus.bit_length() + 7) // 8
        if len(signature) != length or int.from_bytes(signature, "big") >= modulus:
            raise ValueError("signature length")
        prefix = bytes.fromhex("3031300d060960864801650304020105000420")
        digest = prefix + hashlib.sha256(payload).digest()
        expected = b"\x00\x01" + b"\xff" * (length - len(digest) - 3) + b"\x00" + digest
        actual = pow(int.from_bytes(signature, "big"), key["e"], modulus).to_bytes(length, "big")
        if not hmac.compare_digest(actual, expected):
            raise ValueError("publisher signature")
        raw = launcher_channel._json_document(payload)
        if (not isinstance(raw, dict) or raw.get("schema") != 1
                or raw.get("purpose") != "mitom-app-catalogue"
                or set(raw) != {"schema", "purpose", "version", "apps", "history"}):
            raise ValueError("catalogue identity")
        backend.version_tuple(raw["version"])
        app_ids = set(raw["apps"])
        if (not REQUIRED_APPS.issubset(app_ids) or not app_ids.issubset(MODULES)
                or set(raw["history"]) != app_ids):
            raise ValueError("unsupported app")
        result = {"version": raw["version"], "apps": {}, "history": {}}
        for app_id in sorted(app_ids):
            module = MODULES[app_id]
            current = module.validate_release(raw["apps"][app_id])
            if not re.fullmatch(r"\d+\.\d+\.\d+", current["version"]):
                raise ValueError("app version")
            if current["entry"] != ENTRIES[app_id]:
                raise ValueError("executable identity")
            if not current["archive"].get("url"):
                raise ValueError("missing online artifact")
            history = raw["history"][app_id]
            if not isinstance(history, list) or len(history) > 32:
                raise ValueError("history limit")
            accepted, versions = [], {current["version"]}
            for previous in history:
                previous = module.validate_release(previous)
                if not re.fullmatch(r"\d+\.\d+\.\d+", previous["version"]):
                    raise ValueError("historical app version")
                if (previous["version"] in versions or previous["entry"] != current["entry"]
                        or backend.version_tuple(previous["version"]) >= backend.version_tuple(current["version"])):
                    raise ValueError("invalid app history")
                versions.add(previous["version"])
                accepted.append(previous)
            result["apps"][app_id] = current
            result["history"][app_id] = accepted
        return result
    except (KeyError, ValueError, TypeError, OverflowError, AttributeError,
            mi_font_app.MiFontAppError, vector_app.VectorAppError, video_app.VideoAppError) as exc:
        raise backend.LauncherError("Không xác thực được danh mục ứng dụng; giữ bản đã kiểm tra trước đó.") from exc


def _cache_path(backend):
    import installer
    return installer._plain(Path(backend.launcher_data_root()) / "apps-catalogue-signed.json")


def load_cached_catalogue(backend):
    path = _cache_path(backend)
    if not path.is_file():
        return None
    if path.stat().st_size > LIMIT:
        raise backend.LauncherError("App catalogue cache is too large.")
    return verify_catalogue(backend, launcher_channel._json_document(path.read_bytes()))


def probe_app_catalogue(backend, cancelled=None):
    cancelled = cancelled or threading.Event()
    deadline = time.monotonic() + TIMEOUT
    launcher_channel._check_active(cancelled, deadline)

    class Redirects(backend.HttpsRedirectHandler):
        def redirect_request(self, request, fp, code, message, headers, newurl):
            if newurl != CATALOGUE_URL:
                raise backend.LauncherError("App catalogue changed location.")
            return super().redirect_request(request, fp, code, message, headers, newurl)

    request = urllib.request.Request(CATALOGUE_URL, headers={
        "User-Agent": "MITOM/" + backend.APP_VERSION, "Accept": "application/json",
        "Cache-Control": "no-cache", "Pragma": "no-cache"})
    chunks, size = [], 0
    with urllib.request.build_opener(Redirects()).open(request, timeout=TIMEOUT) as response:
        if response.geturl() != CATALOGUE_URL:
            raise backend.LauncherError("App catalogue changed location.")
        while True:
            launcher_channel._check_active(cancelled, deadline)
            chunk = getattr(response, "read1", response.read)(min(65536, LIMIT + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > LIMIT:
                raise backend.LauncherError("App catalogue exceeds supported size.")
    raw = b"".join(chunks)
    result = verify_catalogue(backend, launcher_channel._json_document(raw))
    launcher_channel._check_active(cancelled, deadline)
    try:
        cached = load_cached_catalogue(backend)
    except (OSError, ValueError, backend.LauncherError):
        # A valid online signature can repair a damaged cache. Never let an
        # untrusted local JSON file permanently block authenticated updates.
        cached = None
    if cached:
        old, new = backend.version_tuple(cached["version"]), backend.version_tuple(result["version"])
        if new < old or (new == old and result != cached):
            raise backend.LauncherError("App catalogue rollback or conflicting publication rejected.")
        for app_id in cached["apps"]:
            if app_id not in result["apps"]:
                raise backend.LauncherError("An authenticated application disappeared from the catalogue.")
            old_app, new_app = cached["apps"][app_id], result["apps"][app_id]
            old_version = backend.version_tuple(old_app["version"])
            new_version = backend.version_tuple(new_app["version"])
            if new_version < old_version or (new_version == old_version and new_app != old_app):
                raise backend.LauncherError("App release rollback or conflicting identity rejected.")
    # Check all identities before persisting the new signed cache. The UI then
    # applies the same validated set together, so a bad app cannot poison it.
    for app_id in result["apps"]:
        module = MODULES[app_id]
        module.check_catalogue(result["apps"][app_id], result["history"][app_id])
    path = _cache_path(backend)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".apps-catalogue-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
        launcher_channel._check_active(cancelled, deadline)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return copy.deepcopy(result)
