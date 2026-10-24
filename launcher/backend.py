"""MITOM launcher backend: isolated staging and reliable verified downloads.

The original signature verification, allowlists, transactional installation,
private-data preservation and recovery logic are retained. See RECOVERY.md.
"""
from __future__ import annotations

# Explicit imports let PyInstaller discover dependencies of the recovered code.
import argparse
import base64
import contextlib
import ctypes
import ctypes.wintypes
import functools
import hashlib
import hmac
import http.client
import json
import os
from pathlib import Path
import queue
import re
import secrets
import shutil
import socket
import ssl
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import traceback
import tkinter
import urllib.error
import urllib.parse
import urllib.request
import uuid
import webbrowser
import winreg
import xml.etree.ElementTree
import zipfile

from legacy_loader import populate as _populate

_populate(globals(), "launcher")

APP_VERSION = "2026.10.24.3"
APP_TITLE = "MITOM Launcher"
# This is a launcher repair. The verified Photoshop panel release is unchanged.
CORE_VERSION = "2026.10.21.2"
APPS_URL = "https://mitomyhy.github.io/"
GITHUB_URL = "https://github.com/Mitomyhy"
NETWORK_TIMEOUT = 120
DOWNLOAD_ATTEMPTS = 3
_original_download_artifact = download_artifact
_owned_staging: dict[Path, Path] = {}
DEFAULT_FEED_URL = APPS_URL + "releases/stable.json"


def release_feed_url():
    """Use the owner's canonical signed GitHub Pages release channel."""
    return validate_https_url(DEFAULT_FEED_URL)


def ps_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _transient_download_error(exc: BaseException) -> bool:
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in {408, 429, 500, 502, 503, 504}
    if isinstance(exc, urllib.error.URLError):
        return _transient_download_error(exc.reason) if isinstance(exc.reason, BaseException) else False
    return isinstance(exc, (TimeoutError, socket.timeout, ConnectionError,
                            http.client.IncompleteRead, http.client.RemoteDisconnected))


def download_artifact(artifact, destination_dir, filename, progress=None):
    """Retry transient failures; never retry a signature, size or hash failure.

    The original downloader verifies an existing cache before use and removes
    each incomplete temporary file. Every retry begins with a new digest.
    """
    for attempt in range(DOWNLOAD_ATTEMPTS):
        try:
            return _original_download_artifact(artifact, destination_dir, filename, progress)
        except Exception as exc:
            if not _transient_download_error(exc):
                raise
            if attempt + 1 == DOWNLOAD_ATTEMPTS:
                raise LauncherError(
                    "Kết nối tải về bị gián đoạn sau 3 lần thử. "
                    "Nhấn Cài / Cập nhật để thử lại; các phần đã kiểm tra được giữ trong bộ nhớ đệm."
                ) from exc
            if progress:
                progress(0, artifact["size"])
            time.sleep(attempt + 1)


def _install_step(name, path, operation, *args, **kwargs):
    """Keep the failing stage and path when Windows returns a bare Errno 2."""
    try:
        return operation(*args, **kwargs)
    except FileNotFoundError as exc:
        raise LauncherError(
            name + ": a required file or folder was not found.\n"
            + str(exc.filename or path)
            + "\nOpen Activity Log for the detailed error."
        ) from exc


def failure_details(exc):
    """Local-only diagnostics; no configuration values or credentials are read."""
    return "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))


def network_smoke():
    """Verify the packaged TLS runtime and signed feed without an installation."""
    context = ssl.create_default_context()
    if not context.check_hostname or context.verify_mode != ssl.CERT_REQUIRED:
        raise LauncherError("TLS certificate and hostname verification must stay enabled.")
    release = fetch_release()
    artifact = release.get("launcher")
    if not artifact:
        raise LauncherError("Signed feed has no launcher download to check.")
    url = validate_https_url(artifact["url"])
    request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "MITOM/" + APP_VERSION})
    opener = urllib.request.build_opener(HttpsRedirectHandler())
    with opener.open(request, timeout=30) as response:
        validate_https_url(response.geturl())
        status = response.status
        length = response.headers.get("Content-Length")
    if status != 200 or (length is not None and int(length) != artifact["size"]):
        raise LauncherError("Signed launcher download is unavailable or has the wrong size.")
    return {"ok": True, "version": APP_VERSION, "openssl": ssl.OPENSSL_VERSION,
            "certificate_verification": True, "hostname_verification": True,
            "feed_url": release_feed_url(), "panel_version": release["version"],
            "download_version": artifact["version"], "download_status": status,
            "network": True, "writes_panel": False, "downloads_artifacts": False}


def staging_root(cache_root=None, base_root=None) -> Path:
    """Keep extracted payloads outside every directory managed by the installer."""
    import installer
    roots, local, _sandbox = installer._roots(base_root)
    if base_root is not None:
        path = local / "UOY" / "InstallStaging"
    elif cache_root is not None:
        cache = installer._plain(cache_root)
        path = cache.parent / (cache.name + "-staging")
    else:
        path = launcher_data_root().parent / "InstallStaging"
    path = installer._plain(path)
    for target in roots.values():
        if path == target or path in target.parents or target in path.parents:
            raise LauncherError("Thư mục giải nén phải nằm ngoài thư mục cài đặt.")
    return path


def obtain_core(release, progress=None, cache_root=None, base_root=None):
    """Assemble authenticated parts and extract into a separate staging tree."""
    import installer
    version_tuple(release["version"])
    models = external_files(release)
    bundles = external_bundles(release)
    core = release["core"]
    cache_base = installer._plain(cache_root or launcher_data_root() / "Downloads")
    cache = installer._plain(cache_base / release["version"])
    staging = installer._plain(staging_root(cache_root, base_root) / release["version"])
    download_space_preflight(release, cache, base_root,
                             include_install=cache_root is None or base_root is not None)
    cache.mkdir(parents=True, exist_ok=True)
    staging.mkdir(parents=True, exist_ok=True)
    parts, previous = [], 0
    for index, part in enumerate(core["parts"]):
        offset = previous
        callback = (lambda done, _size, offset=offset: progress(offset + done, core["size"])) if progress else None
        parts.append(download_artifact(part, cache, f"core.part{index + 1:03d}", progress=callback))
        previous += part["size"]
    archive = cache / "core.zip"
    fd, temporary = tempfile.mkstemp(prefix="core-", suffix=".zip.part", dir=cache)
    try:
        digest, total = hashlib.sha256(), 0
        with os.fdopen(fd, "wb") as output:
            for part_path in parts:
                with part_path.open("rb") as source:
                    while chunk := source.read(1024 * 1024):
                        digest.update(chunk)
                        total += len(chunk)
                        output.write(chunk)
        if total != core["size"] or digest.hexdigest() != core["sha256"]:
            raise LauncherError("Gói cài ghép lại sai SHA-256; không cài đặt.")
        os.replace(temporary, archive)
    finally:
        Path(temporary).unlink(missing_ok=True)
    manifest = core_manifest_from_zip(archive, release) if bundles else None
    expected_bundle_files = bundle_manifest_files(release, manifest) if bundles else {}
    # Inventory entries are grouped by bundle ID, then by archive member path.
    omitted_bundle_bytes = sum(row["size"] for files in expected_bundle_files.values()
                               for row in files.values())
    expected = core.get("unpacked_size")
    if expected is not None:
        expected -= sum(row["size"] for row in models)
        expected -= omitted_bundle_bytes
        if expected < 0:
            raise LauncherError("Các bundle vượt dung lượng phát hành.")
    extraction = installer._plain(Path(tempfile.mkdtemp(prefix="core-verified-", dir=staging)))
    _owned_staging[extraction] = staging
    try:
        _install_step("Extract panel", archive, safe_extract_core,
                      archive, extraction, expected_bytes=expected)
        if bundles:
            _install_step("Attach runtime components", cache_base / "Bundles", attach_external_bundles,
                          release, extraction, cache_base / "Bundles", manifest, progress)
        _install_step("Attach depth models", cache_base / "Models", attach_external_models,
                      release, extraction, cache_base / "Models", base_root, progress,
                      allow_installed=cache_root is None or base_root is not None)
        installer._plain(archive).unlink()
        return extraction
    except BaseException:
        cleanup_core_extraction(extraction)
        raise


def cleanup_core_extraction(path) -> bool:
    """Remove only the exact staging child created by this launcher process."""
    import installer
    candidate = installer._plain(path)
    expected_parent = _owned_staging.get(candidate)
    if (expected_parent is None or candidate.parent != expected_parent
            or not candidate.name.startswith("core-verified-")):
        return False
    if not candidate.is_dir():
        _owned_staging.pop(candidate, None)
        return False
    version_tuple(candidate.parent.name)
    # _plain checks every ancestor for links/reparse points before rmtree.
    installer._plain(expected_parent)
    shutil.rmtree(candidate)
    _owned_staging.pop(candidate, None)
    return True


def persist_launcher():
    """Retain this bootstrap and create a per-user MITOM Start menu shortcut."""
    if not getattr(sys, "frozen", False):
        return "Chạy từ mã nguồn: không tạo lối tắt launcher."
    import installer
    source = Path(sys.executable).resolve()
    destination = installer._plain(launcher_data_root() / f"MITOMLauncher-{APP_VERSION}.exe")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        with source.open("rb") as left, destination.open("rb") as right:
            if hashlib.file_digest(left, "sha256").digest() != hashlib.file_digest(right, "sha256").digest():
                raise LauncherError("Launcher cùng phiên bản đã tồn tại với nội dung khác; không ghi đè.")
    else:
        fd, stage = tempfile.mkstemp(prefix="launcher-stage-", suffix=".exe", dir=destination.parent)
        os.close(fd)
        try:
            shutil.copy2(source, stage)
            # Windows rename never overwrites a destination created concurrently.
            os.rename(stage, destination)
        finally:
            Path(stage).unlink(missing_ok=True)
    roaming = os.environ.get("APPDATA")
    if not roaming:
        raise LauncherError("Không tìm thấy AppData để tạo lối tắt.")
    shortcuts = installer._plain(Path(roaming) / "Microsoft/Windows/Start Menu/Programs")
    shortcuts.mkdir(parents=True, exist_ok=True)
    shortcut = shortcuts / "MITOM Launcher.lnk"
    script = ("$mitomShell = New-Object -ComObject WScript.Shell; "
              "$mitomShortcut = $mitomShell.CreateShortcut(" + ps_literal(str(shortcut)) + "); "
              "$mitomShortcut.TargetPath = " + ps_literal(str(destination)) + "; "
              "$mitomShortcut.WorkingDirectory = " + ps_literal(str(destination.parent)) + "; "
              "$mitomShortcut.Description = 'MITOM - Photoshop Panel'; $mitomShortcut.Save()")
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                   check=True, capture_output=True,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    register_url_protocol(destination)
    return str(destination)


def register_url_protocol(executable):
    """Register the website's launcher link for the current Windows user."""
    executable = Path(executable).resolve(strict=True)
    key_path = r"Software\Classes\uoy"
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, "URL:MITOM Launcher")
        winreg.SetValueEx(key, "URL Protocol", 0, winreg.REG_SZ, "")
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path + r"\DefaultIcon") as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, '"' + str(executable) + '",0')
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path + r"\shell\open\command") as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ,
                          '"' + str(executable) + '" --open-uri "%1"')


def validate_open_uri(value):
    """A website link can open the catalogue, never trigger an installation."""
    parsed = urllib.parse.urlsplit(value)
    if (parsed.scheme.lower() != "uoy" or parsed.netloc.lower() not in {"", "launch", "launcher"}
            or parsed.path not in {"", "/", "/launch"} or parsed.query or parsed.fragment):
        raise LauncherError("Liên kết mở launcher không hợp lệ.")
    return True
