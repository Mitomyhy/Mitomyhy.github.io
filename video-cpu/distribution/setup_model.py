"""Download the four pinned MITOM model parts, then run the offline installer.

Standard library only. This file lives beside launch.cmd, model-bundle.json,
install_model.py and the portable python/ directory. No system installation.
"""
from __future__ import annotations

import hashlib
import http.client
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

MANIFEST_SHA256 = "45579bf17bbdd520a8d213e7a8e5b5cda05798098ec6db1a270234a12e9cd24f"
INSTALLER_SHA256 = "2995ed11bd52bdb3ba4f06c965576a13778c84f926e9c0469e945d9ee6bf0095"
RELEASE_BASE = "https://github.com/Mitomyhy/Mitomyhy.github.io/releases/download/video-cpu-2026.10.23.1/"
PART_NAMES = tuple(f"MITOM-Video-CPU-Model-2026.10.23.1.zip.part{index:03d}" for index in range(1, 5))
REDIRECT_HOSTS = {"github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com", "github-releases.githubusercontent.com"}
CHUNK = 1024 * 1024
MAX_ATTEMPTS = 3
DISK_MARGIN = 256 * 1024 * 1024


class SetupError(RuntimeError):
    pass


def say(message):
    print(message, flush=True)


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        while block := source.read(8 * CHUNK):
            digest.update(block)
    return digest.hexdigest()


def safe_child(root, relative):
    relative = str(relative)
    name = PurePosixPath(relative)
    if not relative or name.is_absolute() or "\\" in relative or ":" in relative or any(part in {"", ".", ".."} for part in relative.split("/")):
        raise SetupError("Unsafe setup file path.")
    result = root.joinpath(*name.parts)
    for item in [result, *result.parents]:
        if item == root:
            break
        if item.is_symlink() or item.is_junction():
            raise SetupError(f"Setup does not follow links: {relative}")
    if not result.resolve().is_relative_to(root.resolve()):
        raise SetupError("Setup file leaves the application folder.")
    return result


def matches(path, row):
    return path.is_file() and path.stat().st_size == row["size"] and sha256(path) == row["sha256"]


def load_bundle(app):
    path = safe_child(app, "model-bundle.json")
    if not path.is_file() or path.stat().st_size > 256 * 1024 or sha256(path) != MANIFEST_SHA256:
        raise SetupError("The bundled model manifest is missing or does not match this app. Extract a fresh app ZIP.")
    value = json.loads(path.read_text(encoding="utf8"))
    archive = value.get("archive", {})
    parts = archive.get("parts", [])
    if value.get("schema_version") != 1 or archive.get("format") != "zip-stored-multipart" or tuple(row.get("name") for row in parts) != PART_NAMES:
        raise SetupError("Unexpected model bundle layout.")
    if any(type(row.get("size")) is not int or not 0 < row["size"] <= 1_800_000_000 or not re.fullmatch(r"[a-f0-9]{64}", row.get("sha256", "")) for row in parts):
        raise SetupError("Invalid model-part metadata.")
    if sum(row["size"] for row in parts) != archive.get("size") or type(value.get("total_uncompressed_bytes")) is not int or value["total_uncompressed_bytes"] <= 0:
        raise SetupError("Invalid model size metadata.")
    if value.get("model_subdir") != "models/ltx-video-cpu" or not isinstance(value.get("files"), dict) or not value["files"]:
        raise SetupError("Invalid model file inventory.")
    for name, row in value["files"].items():
        safe_child(app, "models/ltx-video-cpu/" + name)
        if type(row.get("size")) is not int or row["size"] < 0 or not re.fullmatch(r"[a-f0-9]{64}", row.get("sha256", "")):
            raise SetupError("Invalid model file metadata.")
    return value


def part_url(row):
    if row["name"] not in PART_NAMES:
        raise SetupError("This download is not one of the four pinned model parts.")
    return RELEASE_BASE + row["name"]


def check_transport_url(url):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname not in REDIRECT_HOSTS or parsed.username or parsed.password or parsed.port not in {None, 443}:
        raise SetupError("The model download redirected to an unexpected address.")


class PinnedRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        check_transport_url(new_url)
        return super().redirect_request(request, response, code, message, headers, new_url)


def preserve(path):
    if not path.exists():
        return
    saved = path.with_name(path.name + ".corrupt-" + time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8])
    path.rename(saved)
    say(f"Kept the incomplete or invalid file as {saved.name}")


def download_part(row, folder, *, opener=None, sleep=time.sleep):
    """Bounded retries; an interrupted verified-size download can resume later."""
    target = safe_child(folder, row["name"])
    partial = safe_child(folder, row["name"] + ".partial")
    if matches(target, row):
        say(f"Already verified: {row['name']}")
        return target
    if target.exists():
        preserve(target)
    opener = opener or urllib.request.build_opener(PinnedRedirect())
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            if partial.exists() and partial.stat().st_size >= row["size"]:
                if matches(partial, row):
                    partial.rename(target)
                    return target
                preserve(partial)
            offset = partial.stat().st_size if partial.exists() else 0
            headers = {"User-Agent": "MITOM-Model-Setup/2026.10.23.1", "Accept-Encoding": "identity"}
            if offset:
                headers["Range"] = f"bytes={offset}-"
            request = urllib.request.Request(part_url(row), headers=headers)
            say(f"Downloading {row['name']} ({attempt}/{MAX_ATTEMPTS})" + (f"; resuming at {offset / 1048576:.1f} MB" if offset else ""))
            with opener.open(request, timeout=30) as response:
                check_transport_url(response.geturl())
                status = response.status
                if status == 206:
                    content_range = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", response.headers.get("Content-Range", ""))
                    if not content_range or tuple(map(int, content_range.groups())) != (offset, row["size"] - 1, row["size"]):
                        raise SetupError("The server returned an invalid resume range.")
                elif status == 200:
                    if offset:
                        say("The server restarted this part; downloading it from the beginning.")
                    offset = 0
                else:
                    raise SetupError(f"Unexpected download response: {status}")
                if response.headers.get("Content-Encoding", "identity").lower() != "identity":
                    raise SetupError("Unexpected compressed model download.")
                length = response.headers.get("Content-Length")
                if length is not None and int(length) != row["size"] - offset:
                    raise SetupError("The server returned an unexpected model-part size.")
                completed, last_report = offset, 0.0
                with partial.open("ab" if offset else "wb") as destination:
                    while data := response.read(CHUNK):
                        if completed + len(data) > row["size"]:
                            raise SetupError("The download exceeds the pinned model-part size.")
                        destination.write(data)
                        completed += len(data)
                        now = time.monotonic()
                        if now - last_report >= 1:
                            say(f"  {completed * 100 / row['size']:5.1f}%  {completed / 1048576:.1f} / {row['size'] / 1048576:.1f} MB")
                            last_report = now
                if completed != row["size"]:
                    raise SetupError("The model download ended early. Its partial file can be resumed.")
            say("Checking SHA-256...")
            if not matches(partial, row):
                preserve(partial)
                raise SetupError("Model-part checksum mismatch; retrying a fresh download.")
            partial.rename(target)
            say(f"Verified: {row['name']}")
            return target
        except (OSError, ValueError, urllib.error.URLError, http.client.IncompleteRead, SetupError) as error:
            if attempt == MAX_ATTEMPTS:
                raise SetupError(f"Could not finish {row['name']}: {error}. Run install-model.cmd again to resume, or provide this release's offline parts.") from error
            say(f"Download interrupted: {error}. Retrying...")
            sleep(attempt)
    raise AssertionError("Unreachable download state")


def existing_parts(app, rows):
    downloads = safe_child(app, "downloads")
    if downloads.exists() and not downloads.is_dir():
        raise SetupError("The downloads path is not a folder.")
    found = {}
    for row in rows:
        say(f"Checking local part: {row['name']}")
        for folder in (app, downloads):
            candidate = safe_child(folder, row["name"])
            if matches(candidate, row):
                found[row["name"]] = candidate
                break
    folder = app if any(path.parent == app for path in found.values()) else downloads
    return found, folder


def check_disk(app, folder, rows, found, extracted_bytes):
    needed = extracted_bytes + DISK_MARGIN
    for row in rows:
        source = found.get(row["name"])
        if source and source.parent == folder:
            continue
        partial = safe_child(folder, row["name"] + ".partial")
        resumable = partial.stat().st_size if not source and partial.is_file() else 0
        if resumable >= row["size"] and not matches(partial, row):
            resumable = 0
        needed += row["size"] - resumable
    free = shutil.disk_usage(app).free
    if free < needed:
        raise SetupError(f"Not enough free disk space. Setup needs about {needed / 1073741824:.1f} GB free; {free / 1073741824:.1f} GB is available. Move the entire app to a drive with more space.")


def installed(app, bundle):
    root = safe_child(app, bundle["model_subdir"])
    if not root.is_dir():
        return False
    # Avoid hashing an obviously incomplete install before checking the parts.
    files = [(safe_child(root, name), row) for name, row in bundle["files"].items()]
    if not all(path.is_file() and path.stat().st_size == row["size"] for path, row in files):
        return False
    say("Checking the installed model...")
    return all(matches(path, row) for path, row in files)


def install(app, bundle, *, opener=None):
    installer = safe_child(app, "install_model.py")
    python = safe_child(app, "python/python.exe")
    if not python.is_file() or not installer.is_file() or sha256(installer) != INSTALLER_SHA256:
        raise SetupError("The bundled Python or reviewed model installer is missing or changed. Extract a fresh app ZIP.")
    if installed(app, bundle):
        say("The model is already installed and verified. Open launch.cmd.")
        return
    rows = bundle["archive"]["parts"]
    found, folder = existing_parts(app, rows)
    check_disk(app, folder, rows, found, bundle["total_uncompressed_bytes"])
    folder.mkdir(exist_ok=True)
    for row in rows:
        source, target = found.get(row["name"]), safe_child(folder, row["name"])
        if source and source.parent != folder:
            if target.exists():
                preserve(target)
            shutil.copy2(source, target)
            if not matches(target, row):
                raise SetupError("An offline part changed while being copied. Keep the original files and retry.")
        elif not source:
            download_part(row, folder, opener=opener)
    if shutil.disk_usage(app).free < bundle["total_uncompressed_bytes"] + DISK_MARGIN:
        raise SetupError("The parts are ready, but more free disk space is needed to extract the model. Keep the files and retry after making room.")
    say("All four parts are verified. Installing the model; please wait...")
    environment = {name: value for name, value in os.environ.items() if not name.upper().startswith("PYTHON")}
    environment["PYTHONNOUSERSITE"] = "1"
    subprocess.run([str(python), "-I", str(installer), "install", "--app-dir", str(app), "--manifest", str(app / "model-bundle.json"), "--parts-dir", str(folder)], cwd=app, env=environment, check=True)
    say("Setup complete. Open launch.cmd to create a video.")


def main():
    app = Path(__file__).resolve().parent
    say("MITOM Video - model setup\nClose MITOM Video before installing or updating its model.")
    if Path(sys.executable).resolve() != (app / "python/python.exe").resolve():
        raise SetupError("Use install-model.cmd so setup runs with the app's bundled Python.")
    install(app, load_bundle(app))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        say("\nSetup stopped. Run install-model.cmd again to resume.")
        sys.exit(130)
    except (SetupError, OSError, ValueError, subprocess.CalledProcessError) as error:
        say(f"\nSetup could not finish: {error}")
        sys.exit(1)
