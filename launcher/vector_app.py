"""Verified, offline-capable sidecar installation for Vector Migic.

The release descriptor is packaged with the launcher, never accepted from an
unverified web response. It pins both the ZIP and the runtime file manifest.
Photoshop installation and its credentials are not used by this module.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tempfile
import urllib.parse
import urllib.request
import uuid
import zipfile

APP_ID = "vector-migic"
BUNDLE_ROOT = Path(__file__).resolve().parent
RELEASE_PATH = BUNDLE_ROOT / "vector-release.json"
MAX_ARCHIVE_BYTES = 768 * 1024 * 1024
MAX_RUNTIME_BYTES = 2 * 1024 * 1024 * 1024
_DEFAULT_RELEASE = object()
# Known previous runtimes are trusted only by these exact manifest pins.
# Keep newest first so an older side-by-side installation cannot mask it.
# Never trust arbitrary folders or a manifest supplied from the user's machine.
TRUSTED_PREVIOUS_RELEASES = (
    {"schema": 1, "app_id": APP_ID, "version": "1.0.3", "entry": "VectorMigic.exe",
     "manifest_sha256": "c53b390ec119ff9c26fe4d2e58b02fe987fb843cc902f37daedc7f8f77af734e"},
    {"schema": 1, "app_id": APP_ID, "version": "1.0.2", "entry": "VectorMigic.exe",
     "manifest_sha256": "abe3f2a20a15b8b5481e6aaea52bd8194fbd9d4201a9d5e900bbddca284872ac"},
    {"schema": 1, "app_id": APP_ID, "version": "1.0.1", "entry": "VectorMigic.exe",
     "manifest_sha256": "d0b6152cac4a1311c4cecc55999c340bc600ece69bfde3dee19cf5cb2c10be95"},
    {"schema": 1, "app_id": APP_ID, "version": "1.0.0", "entry": "VectorMigic.exe",
     "manifest_sha256": "76c2230f72edc5ff8765af5a3a0c44882cc3bc79c5000baa7a97b1f922cf034d"},
)


class VectorAppError(RuntimeError):
    pass


def _digest(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _sha(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise VectorAppError("Invalid Vector Migic checksum.")
    return value


def _relative(value):
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise VectorAppError("Invalid Vector Migic runtime path.")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in value.split("/")):
        raise VectorAppError("Invalid Vector Migic runtime path.")
    return path


def _plain_path(path):
    path = Path(path)
    for part in (path, *path.parents):
        if part.is_symlink() or getattr(part, "is_junction", lambda: False)():
            raise VectorAppError("Vector Migic cannot install through a linked folder.")
    return path


def load_release(path=RELEASE_PATH):
    """Read the immutable release descriptor bundled with this launcher."""
    path = Path(path)
    if not path.is_file():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("schema") != 1 or raw.get("app_id") != APP_ID:
        raise VectorAppError("Invalid Vector Migic release descriptor.")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", raw.get("version", "")):
        raise VectorAppError("Invalid Vector Migic release version.")
    _relative(raw.get("entry"))
    if not raw["entry"].lower().endswith(".exe"):
        raise VectorAppError("This Vector Migic package has no standalone Windows runtime.")
    _sha(raw.get("manifest_sha256"))
    archive = raw.get("archive", {})
    if not isinstance(archive, dict):
        raise VectorAppError("Invalid Vector Migic archive descriptor.")
    _sha(archive.get("sha256"))
    if type(archive.get("size")) is not int or not 0 < archive["size"] <= MAX_ARCHIVE_BYTES:
        raise VectorAppError("Invalid Vector Migic download size.")
    if archive.get("bundled"):
        _relative(archive["bundled"])
    if archive.get("url"):
        _download_url(archive["url"])
    if not archive.get("bundled") and not archive.get("url"):
        raise VectorAppError("Vector Migic has no installation package.")
    return raw


def _download_url(url):
    if not isinstance(url, str):
        raise VectorAppError("Invalid Vector Migic download URL.")
    parsed = urllib.parse.urlsplit(url)
    allowed = ((parsed.hostname == "mitomyhy.github.io" and parsed.path.startswith(("/tools/downloads/", "/releases/vector-migic/")))
               or (parsed.hostname == "github.com" and parsed.path.startswith("/Mitomyhy/")))
    if (parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in {None, 443}
            or parsed.fragment or not allowed):
        raise VectorAppError("Vector Migic downloads must use the owner's HTTPS release location.")
    return url


def _target(data_root, release):
    return _plain_path(Path(data_root) / "Apps" / "VectorMigic" / release["version"])


def _manifest(folder, release):
    path = _plain_path(Path(folder) / "vector-app.json")
    if not path.is_file() or _digest(path) != release["manifest_sha256"]:
        raise VectorAppError("Vector Migic file manifest does not match this release.")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise VectorAppError("Invalid Vector Migic runtime manifest.")
    if any(manifest.get(key) != release.get(key) for key in ("schema", "app_id", "version", "entry")):
        raise VectorAppError("Vector Migic package identity mismatch.")
    files = manifest.get("files")
    if not isinstance(files, list) or not files or len(files) > 15000:
        raise VectorAppError("Invalid Vector Migic runtime file list.")
    result, folded, total = {}, set(), 0
    for item in files:
        if not isinstance(item, dict):
            raise VectorAppError("Invalid Vector Migic runtime file entry.")
        name = str(_relative(item.get("path")))
        if name.casefold() in folded or name.casefold() == "vector-app.json":
            raise VectorAppError("Duplicate Vector Migic runtime path.")
        folded.add(name.casefold())
        size = item.get("size")
        if type(size) is not int or size < 0:
            raise VectorAppError("Invalid Vector Migic runtime file size.")
        total += size
        if total > MAX_RUNTIME_BYTES:
            raise VectorAppError("Vector Migic runtime is too large.")
        result[name] = {"size": size, "sha256": _sha(item.get("sha256"))}
    if release["entry"] not in result:
        raise VectorAppError("Vector Migic executable is missing from the manifest.")
    return result


def verify_install(folder, release):
    """Verify every installed executable/dependency before it can be launched."""
    folder = _plain_path(folder)
    files = _manifest(folder, release)
    for name, item in files.items():
        path = _plain_path(folder / name)
        if not path.is_file() or path.stat().st_size != item["size"] or _digest(path) != item["sha256"]:
            raise VectorAppError("Vector Migic runtime file is missing or changed: " + name)
    actual = {p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file()}
    if actual != set(files) | {"vector-app.json"}:
        raise VectorAppError("Unexpected files in the Vector Migic runtime folder.")
    return folder


def installed_state(data_root, release=_DEFAULT_RELEASE):
    release = load_release() if release is _DEFAULT_RELEASE else release
    state = {"vector_installed": False, "vector_version": "", "vector_available": bool(release),
             "vector_latest_version": release["version"] if release else "", "vector_path": "",
             "vector_current_verified": False, "vector_update_available": False, "vector_repair_required": False}
    if not release:
        return state
    try:
        target = _target(data_root, release)
        state["vector_repair_required"] = target.exists()
        verify_install(target, release)
    except (OSError, ValueError, VectorAppError):
        pass
    else:
        state.update(vector_installed=True, vector_version=release["version"], vector_path=str(target),
                     vector_current_verified=True, vector_repair_required=False)
        return state
    current_version = tuple(int(part) for part in release["version"].split(".")) if re.fullmatch(r"\d+(?:\.\d+)*", release["version"]) else ()
    for previous in TRUSTED_PREVIOUS_RELEASES:
        previous_version = tuple(int(part) for part in previous["version"].split("."))
        if not current_version or previous_version >= current_version:
            continue
        try:
            previous_target = verify_install(_target(data_root, previous), previous)
        except (OSError, ValueError, VectorAppError):
            continue
        state.update(vector_installed=True, vector_version=previous["version"], vector_path=str(previous_target),
                     vector_update_available=True)
        return state
    return state


class _ReleaseRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        # GitHub release asset redirects are signed HTTPS CDN URLs. Other
        # redirects must remain on the owner's canonical release locations.
        parsed = urllib.parse.urlsplit(newurl)
        if parsed.hostname == "release-assets.githubusercontent.com":
            if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in {None, 443}:
                raise VectorAppError("Insecure Vector Migic release redirect.")
        else:
            _download_url(newurl)
        return super().redirect_request(request, fp, code, message, headers, newurl)


def _archive_path(data_root, release, bundle_root, progress):
    info = release["archive"]
    bundle = Path(bundle_root) / info.get("bundled", "__not_bundled__")
    if info.get("bundled") and bundle.is_file():
        candidate = _plain_path(bundle)
    else:
        cache = _plain_path(Path(data_root) / "Downloads")
        cache.mkdir(parents=True, exist_ok=True)
        candidate = cache / ("VectorMigic-" + release["version"] + ".zip")
        if not (candidate.is_file() and candidate.stat().st_size == info["size"]
                and _digest(candidate) == info["sha256"]):
            if not info.get("url"):
                raise VectorAppError("The offline Vector Migic package is missing. Download the complete launcher bundle.")
            partial = cache / (".vector-" + uuid.uuid4().hex + ".part")
            try:
                opener = urllib.request.build_opener(_ReleaseRedirects())
                request = urllib.request.Request(_download_url(info["url"]), headers={"User-Agent": "MITOM-Launcher"})
                with opener.open(request, timeout=120) as response, partial.open("xb") as stream:
                    total = 0
                    while chunk := response.read(1024 * 1024):
                        total += len(chunk)
                        if total > info["size"]:
                            raise VectorAppError("Vector Migic download exceeded its verified size.")
                        stream.write(chunk)
                        if progress:
                            progress(total, info["size"])
                if total != info["size"] or _digest(partial) != info["sha256"]:
                    raise VectorAppError("Vector Migic download checksum mismatch.")
                os.replace(partial, candidate)
            finally:
                partial.unlink(missing_ok=True)
    if candidate.stat().st_size != info["size"] or _digest(candidate) != info["sha256"]:
        raise VectorAppError("Vector Migic package checksum mismatch.")
    return candidate


def install_app(data_root, release=_DEFAULT_RELEASE, *, bundle_root=BUNDLE_ROOT, progress=None):
    """Install verified sidecar bytes; preserve all Photoshop/private data."""
    release = load_release() if release is _DEFAULT_RELEASE else release
    if not release:
        raise VectorAppError("Vector Migic is not included in this launcher release.")
    archive = _archive_path(data_root, release, bundle_root, progress)
    target = _target(data_root, release)
    parent = target.parent
    parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".vector-stage-", dir=parent))
    backup = parent / (".vector-backup-" + uuid.uuid4().hex)
    try:
        with zipfile.ZipFile(archive) as package:
            members = package.infolist()
            names = []
            for item in members:
                name = str(_relative(item.filename))
                if item.is_dir() or (item.external_attr >> 16) & 0o170000 == 0o120000:
                    raise VectorAppError("Vector Migic ZIP must contain regular files only.")
                names.append(name)
            if len({name.casefold() for name in names}) != len(names):
                raise VectorAppError("Duplicate files in Vector Migic ZIP.")
            if "vector-app.json" not in names:
                raise VectorAppError("Vector Migic ZIP has no runtime manifest.")
            manifest_info = package.getinfo("vector-app.json")
            if manifest_info.file_size > 4 * 1024 * 1024:
                raise VectorAppError("Vector Migic runtime manifest is too large.")
            (stage / "vector-app.json").write_bytes(package.read("vector-app.json"))
            files = _manifest(stage, release)
            if set(names) != set(files) | {"vector-app.json"}:
                raise VectorAppError("Vector Migic ZIP contents do not match its manifest.")
            for item in members:
                if item.filename == "vector-app.json":
                    continue
                expected = files[item.filename]
                if item.file_size != expected["size"]:
                    raise VectorAppError("Vector Migic ZIP runtime size mismatch.")
                destination = stage / item.filename
                destination.parent.mkdir(parents=True, exist_ok=True)
                with package.open(item) as source, destination.open("xb") as stream:
                    shutil.copyfileobj(source, stream, 1024 * 1024)
        verify_install(stage, release)
        if target.exists():
            target.rename(backup)
        try:
            stage.rename(target)
        except BaseException:
            if backup.exists() and not target.exists():
                backup.rename(target)
            raise
        if backup.exists():
            shutil.rmtree(backup)
        return {"version": release["version"], "path": str(target)}
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def open_app(data_root, release=_DEFAULT_RELEASE):
    release = load_release() if release is _DEFAULT_RELEASE else release
    if not release:
        raise VectorAppError("Vector Migic is not included in this launcher release.")
    target = verify_install(_target(data_root, release), release)
    environment = dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1")
    return subprocess.Popen([str(target / release["entry"])], cwd=str(target), env=environment,
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
