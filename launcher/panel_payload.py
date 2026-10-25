"""Bounded panel downloads with a cache shared across release versions.

Every reused byte is verified against the authenticated release, including old
version caches. This module never handles installation targets or user data.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import hashlib
import http.client
import os
import re
import stat
import threading
import time
import urllib.request


DOWNLOAD_WORKERS = 3
RESUME_THRESHOLD = 32 * 1024 * 1024


def _open_artifact_stream(backend, url, offset):
    url = backend.validate_https_url(url)
    headers = {"User-Agent": "MITOM/" + backend.APP_VERSION,
               "Accept-Encoding": "identity"}
    if offset:
        headers["Range"] = f"bytes={offset}-"
    request = urllib.request.Request(url, headers=headers)
    opener = urllib.request.build_opener(backend.HttpsRedirectHandler())
    response = opener.open(request, timeout=min(backend.NETWORK_TIMEOUT, 30))
    try:
        backend.validate_https_url(response.geturl())
    except BaseException:
        response.close()
        raise
    return response


def _partial_path(installer, directory, filename, digest):
    if (not re.fullmatch(r"[A-Za-z0-9._-]{1,160}", filename)
            or filename in {".", ".."}
            or not re.fullmatch(r"[a-f0-9]{64}", digest)):
        raise installer.InstallError("Invalid verified download cache name.")
    path = installer._plain(directory / (digest + ".part"))
    if path.exists():
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise installer.InstallError("The partial download must be an unshared regular cache file.")
    return path


def download_large_artifact(backend, artifact, destination_dir, filename, progress=None):
    """Resume only a hash-bound cache file, verifying the complete final bytes.

    A server ignoring Range restarts the same artifact safely. A wrong range,
    size, or digest is an integrity failure and is never retried as a timeout.
    """
    import installer
    directory = installer._plain(destination_dir)
    directory.mkdir(parents=True, exist_ok=True)
    partial = _partial_path(installer, directory, filename, artifact["sha256"])
    destination = installer._plain(directory / filename)
    if backend.verified_file(destination, artifact):
        if progress:
            progress(artifact["size"], artifact["size"])
        return destination
    backend.validate_https_url(artifact["url"])
    for attempt in range(backend.DOWNLOAD_ATTEMPTS):
        try:
            partial = _partial_path(installer, directory, filename, artifact["sha256"])
            digest = hashlib.sha256()
            offset = 0
            if partial.exists():
                with partial.open("rb") as source:
                    while chunk := source.read(1024 * 1024):
                        offset += len(chunk)
                        if offset > artifact["size"]:
                            raise backend.LauncherError("Gói tải dở vượt dung lượng đã ký; không cài đặt.")
                        digest.update(chunk)
            if offset < artifact["size"]:
                with _open_artifact_stream(backend, artifact["url"], offset) as response:
                    status = response.status
                    if offset and status == 200:
                        # Some hosts do not support Range. Do not append a full
                        # response to the old prefix or mix two representations.
                        offset, digest = 0, hashlib.sha256()
                    elif status == 206:
                        match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)",
                                             response.headers.get("Content-Range", ""))
                        if (not match or tuple(map(int, match.groups()))
                                != (offset, artifact["size"] - 1, artifact["size"])):
                            raise backend.LauncherError("Máy chủ trả sai vùng tải tiếp; không cài đặt.")
                    elif status != 200:
                        raise backend.LauncherError("Máy chủ trả trạng thái tải không hợp lệ.")
                    length = response.headers.get("Content-Length")
                    if length is not None and (not str(length).isdigit()
                                                or int(length) != artifact["size"] - offset):
                        raise backend.LauncherError("Dung lượng tải tiếp không khớp gói đã ký.")
                    mode = "r+b" if partial.exists() else "xb"
                    with partial.open(mode) as output:
                        installer._plain(partial)
                        if os.fstat(output.fileno()).st_nlink != 1:
                            raise installer.InstallError("The partial download cache file became shared.")
                        output.truncate(offset)
                        output.seek(offset)
                        if progress:
                            progress(offset, artifact["size"])
                        while chunk := response.read(1024 * 1024):
                            if offset + len(chunk) > artifact["size"]:
                                raise backend.LauncherError("Gói tải về vượt dung lượng đã ký.")
                            output.write(chunk)
                            digest.update(chunk)
                            offset += len(chunk)
                            if progress:
                                progress(offset, artifact["size"])
                        output.flush()
                        os.fsync(output.fileno())
                    if offset != artifact["size"]:
                        raise http.client.IncompleteRead(b"", artifact["size"] - offset)
            if (digest.hexdigest() != artifact["sha256"]
                    or not backend.verified_file(partial, artifact)):
                raise backend.LauncherError("Gói tải về sai SHA-256; không cài đặt.")
            installer._plain(partial)
            installer._plain(destination)
            os.replace(partial, destination)
            if progress:
                progress(artifact["size"], artifact["size"])
            return destination
        except Exception as exc:
            if not backend._transient_download_error(exc):
                partial.unlink(missing_ok=True)
                raise
            if attempt + 1 == backend.DOWNLOAD_ATTEMPTS:
                raise backend.LauncherError(
                    "Kết nối tải về bị gián đoạn sau 3 lần thử. Phần đã tải được giữ lại; "
                    "nhấn Cài / Cập nhật để tải tiếp."
                ) from exc
            time.sleep(attempt + 1)


def _parallel_downloads(backend, requests, progress=None):
    """Run at most three verified downloads and report aggregate byte progress."""
    if not requests:
        return []
    lock = threading.Lock()
    completed = [0] * len(requests)
    total = sum(row[0]["size"] for row in requests)

    def obtain(index, request):
        artifact, directory, filename = request

        def report(done, _size):
            if progress:
                with lock:
                    completed[index] = min(artifact["size"], max(0, done))
                    progress(sum(completed), total)

        path = backend.download_artifact(artifact, directory, filename,
                                         progress=report if progress else None)
        report(artifact["size"], artifact["size"])
        return index, path

    results = [None] * len(requests)
    # The executor joins before errors propagate: no download continues after
    # the caller cleans staging, and verified completed artifacts remain cached.
    with ThreadPoolExecutor(max_workers=min(DOWNLOAD_WORKERS, len(requests))) as pool:
        futures = [pool.submit(obtain, index, row)
                   for index, row in enumerate(requests)]
        try:
            for future in as_completed(futures):
                index, path = future.result()
                results[index] = path
        except BaseException:
            for future in futures:
                future.cancel()
            raise
    return results


def obtain_parts(backend, release, cache_base, progress=None):
    """Keep parts by digest, reusing matching legacy per-version caches too."""
    import installer
    cache_base = installer._plain(cache_base)
    shared = installer._plain(cache_base / "Parts")
    shared.mkdir(parents=True, exist_ok=True)
    parts = release["core"]["parts"]
    paths = [None] * len(parts)
    missing, positions = [], []
    # Limit migration to valid release directory names; do not recursively scan
    # arbitrary folders or follow reparse points from an old cache.
    legacy = []
    if cache_base.exists():
        for entry in cache_base.iterdir():
            try:
                backend.version_tuple(entry.name)
            except (ValueError, backend.LauncherError):
                continue
            candidate = installer._plain(entry)
            if candidate.is_dir():
                legacy.append(candidate)
    grouped = {}
    for index, artifact in enumerate(parts):
        filename = artifact["sha256"] + ".bin"
        destination = installer._plain(shared / filename)
        candidates = [destination]
        candidates.extend(installer._plain(directory / f"core.part{index + 1:03d}")
                          for directory in legacy)
        for candidate in candidates:
            if backend.verified_file(candidate, artifact):
                paths[index] = candidate
                break
        else:
            if filename not in grouped:
                grouped[filename] = len(missing)
                missing.append((artifact, shared, filename))
                positions.append([])
            positions[grouped[filename]].append(index)
    for indices, path in zip(positions, _parallel_downloads(backend, missing, progress)):
        for index in indices:
            paths[index] = path
    if progress:
        progress(release["core"]["size"], release["core"]["size"])
    return paths


def prefetch_dependencies(backend, release, cache_base, base_root=None,
                          allow_installed=True, progress=None):
    """Fetch independent runtime archives/models together, preserving pins.

    The existing attach functions subsequently check the payload manifest and
    every archive member. Existing installed model candidates are verified and
    keep their existing no-download path.
    """
    import installer
    cache_base = installer._plain(cache_base)
    requests = []
    for bundle in backend.external_bundles(release):
        suffix = ".whl" if bundle["id"] == "torch-cpu" else ".tar.gz"
        requests.append((bundle, cache_base / "Bundles" / bundle["id"] / bundle["sha256"],
                         bundle["id"] + suffix))
    for item in backend.external_files(release):
        if allow_installed and any(backend.verified_file(candidate, item)
                                   for candidate in backend.installed_model_candidates(item["path"], base_root)):
            continue
        requests.append((item, cache_base / "Models" / item["sha256"],
                         Path(item["path"]).name))
    return _parallel_downloads(backend, requests, progress)
