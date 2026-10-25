"""Independent, signed launcher-only update discovery.

This channel cannot supply panel installation metadata. The Photoshop panel
continues to use its original feed and verification key. No unsigned download
index or network-supplied verification key is accepted here.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import sys
import threading
import time
import urllib.parse
import urllib.request

LAUNCHER_FEED_URL = "https://mitomyhy.github.io/releases/launcher-stable.json"
PUBLIC_KEY_FILENAME = "launcher-update-public-key.json"
PUBLIC_KEY_LIMIT = 16 * 1024
PROBE_TIMEOUT_SECONDS = 12
_FIELDS = ("version", "url", "size", "sha256")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate field in launcher metadata.")
        result[key] = value
    return result


def _json_document(data):
    def invalid_constant(_value):
        raise ValueError("Invalid numeric value in launcher metadata.")
    return json.loads(data.decode("utf-8-sig"), object_pairs_hook=_unique_object,
                      parse_constant=invalid_constant)


def load_launcher_public_key():
    """Load only the independent verification key bundled with this launcher."""
    resources = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    path = resources / PUBLIC_KEY_FILENAME
    if not path.is_file() or path.stat().st_size > PUBLIC_KEY_LIMIT:
        raise ValueError("The launcher update verification key is missing or invalid.")
    with path.open("rb") as stream:
        data = stream.read(PUBLIC_KEY_LIMIT + 1)
    if len(data) > PUBLIC_KEY_LIMIT:
        raise ValueError("The launcher update verification key is too large.")
    key = _json_document(data)
    if (not isinstance(key, dict) or set(key) != {"key_id", "n", "e"}
            or not isinstance(key["key_id"], str) or not key["key_id"].strip()
            or len(key["key_id"]) > 128 or key["e"] != 65537
            or isinstance(key["e"], bool) or not isinstance(key["n"], str)
            or not re.fullmatch(r"[0-9a-fA-F]{1024}", key["n"])
            or int(key["n"], 16).bit_length() != 4096):
        raise ValueError("Invalid independent launcher update verification key.")
    return key


def _feed_location(backend, url):
    validated = backend.validate_https_url(url)
    parsed = urllib.parse.urlsplit(validated)
    if (parsed.scheme != "https" or parsed.hostname != "mitomyhy.github.io"
            or parsed.path != "/releases/launcher-stable.json"
            or parsed.port not in (None, 443) or parsed.username or parsed.password
            or parsed.query or parsed.fragment):
        raise backend.LauncherError("The launcher update channel changed location.")
    return validated


def _check_active(cancelled, deadline=None):
    if cancelled.is_set():
        raise RuntimeError("Launcher update discovery cancelled.")
    if deadline is not None and time.monotonic() >= deadline:
        raise RuntimeError("Launcher update discovery timed out.")


def _launcher_projection(backend, release):
    if not isinstance(release, dict) or not isinstance(release.get("launcher"), dict):
        raise backend.LauncherError("The signed launcher channel has no launcher artifact.")
    try:
        artifact = {key: release["launcher"][key] for key in _FIELDS}
        version = artifact["version"]
        if not isinstance(version, str) or not re.fullmatch(r"\d{4}\.\d{2}\.\d{2}\.\d+", version):
            raise ValueError("Invalid launcher version.")
        backend.version_tuple(version)
        if isinstance(artifact["size"], bool):
            raise ValueError("Invalid launcher size.")
        backend.validate_artifact({key: artifact[key] for key in ("url", "size", "sha256")})
        parsed = urllib.parse.urlsplit(artifact["url"])
        owner_location = ((parsed.hostname == "mitomyhy.github.io"
                           and parsed.path.startswith("/releases/mitom-launcher/"))
                          or (parsed.hostname == "github.com"
                              and parsed.path.startswith("/Mitomyhy/Mitomyhy.github.io/releases/download/")))
        if (parsed.scheme != "https" or not owner_location
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.port not in (None, 443) or not parsed.path.lower().endswith(".exe")):
            raise ValueError("The launcher artifact is outside the owner's executable release location.")
    except (KeyError, TypeError, ValueError, backend.LauncherError) as exc:
        raise backend.LauncherError("Invalid artifact in the authenticated launcher channel.") from exc
    # Deliberately discard core, panel version, bundles, models, notes and any
    # additional fields, even when the independent signature authenticates them.
    return {"launcher": artifact}


def probe_launcher_release(backend, cancelled=None):
    """Fetch bounded metadata, verify its signature, expose only the launcher."""
    cancelled = cancelled or threading.Event()
    _check_active(cancelled)
    try:
        key = load_launcher_public_key()
    except (OSError, ValueError, TypeError) as exc:
        raise backend.LauncherError("Cannot load the launcher update verification key.") from exc
    _check_active(cancelled)
    url = _feed_location(backend, LAUNCHER_FEED_URL)

    class ChannelRedirects(backend.HttpsRedirectHandler):
        def redirect_request(self, request, fp, code, message, headers, newurl):
            _feed_location(backend, newurl)
            return super().redirect_request(request, fp, code, message, headers, newurl)

    request = urllib.request.Request(url, headers={
        "User-Agent": "MITOM/" + backend.APP_VERSION, "Accept": "application/json",
        "Cache-Control": "no-cache", "Pragma": "no-cache"})
    opener = urllib.request.build_opener(ChannelRedirects())
    deadline = time.monotonic() + PROBE_TIMEOUT_SECONDS
    chunks, size = [], 0
    with opener.open(request, timeout=PROBE_TIMEOUT_SECONDS) as response:
        _feed_location(backend, response.geturl())
        length = response.headers.get("Content-Length")
        if length is not None:
            if not re.fullmatch(r"[0-9]+", str(length)) or int(length) > backend.FEED_LIMIT:
                raise backend.LauncherError("Launcher update metadata exceeds the supported size.")
        read = getattr(response, "read1", response.read)
        while True:
            _check_active(cancelled, deadline)
            chunk = read(min(65536, backend.FEED_LIMIT + 1 - size))
            _check_active(cancelled, deadline)
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > backend.FEED_LIMIT:
                raise backend.LauncherError("Launcher update metadata exceeds the supported size.")
    _check_active(cancelled, deadline)
    try:
        envelope = _json_document(b"".join(chunks))
    except (ValueError, UnicodeError) as exc:
        raise backend.LauncherError("The launcher update channel is not valid JSON.") from exc
    verified = backend.verify_release_envelope(envelope, key)
    _check_active(cancelled, deadline)
    return _launcher_projection(backend, verified)
