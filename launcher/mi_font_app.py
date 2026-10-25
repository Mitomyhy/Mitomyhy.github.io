"""Verified, offline-capable sidecar installation for MiFont.

The release descriptor comes from a signed online catalogue or the bundled
offline fallback. It pins both the ZIP and the runtime file manifest.
Photoshop installation and its credentials are not used by this module.
"""
from __future__ import annotations

import hashlib
import copy
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

APP_ID = "mifont"
BUNDLE_ROOT = Path(__file__).resolve().parent
RELEASE_PATH = BUNDLE_ROOT / "mifont-release.json"
MAX_ARCHIVE_BYTES = 768 * 1024 * 1024
MAX_RUNTIME_BYTES = 2 * 1024 * 1024 * 1024
_DEFAULT_RELEASE = object()
_online_release = None
_online_history = ()
# Known previous runtimes are trusted only by these exact manifest pins.
# Keep newest first so an older side-by-side installation cannot mask it.
# Never trust arbitrary folders or a manifest supplied from the user's machine.
TRUSTED_PREVIOUS_RELEASES = ({'schema':1,'app_id':'mifont','version':'1.0.2','entry':'MiFont.exe',
    'manifest_sha256':'ca2381e1dd19620a2c68d65a7ee80aa67f01c86901b20c221108a54ae1e509be'},
    {'schema':1,'app_id':'mifont','version':'1.0.1','entry':'MiFont.exe',
    'manifest_sha256':'a0bedd92404c23febe9b75612aea6e23f7f0cd8636de164429ca045a9f4f6902'},
    {'schema':1,'app_id':'mifont','version':'1.0.0','entry':'MiFont.exe',
    'manifest_sha256':'9881638902890043fc979166637d2a23181166b6b016af93718e841054b532ee'},)


class MiFontAppError(RuntimeError):
    pass


def _digest(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _sha(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise MiFontAppError("Invalid MiFont checksum.")
    return value


def _relative(value):
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise MiFontAppError("Invalid MiFont runtime path.")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in value.split("/")):
        raise MiFontAppError("Invalid MiFont runtime path.")
    return path


def _plain_path(path):
    path = Path(path)
    for part in (path, *path.parents):
        if part.is_symlink() or getattr(part, "is_junction", lambda: False)():
            raise MiFontAppError("MiFont cannot install through a linked folder.")
    return path


def load_release(path=RELEASE_PATH):
    """Use authenticated online metadata, with the bundled offline fallback."""
    if path == RELEASE_PATH and _online_release is not None:
        return copy.deepcopy(_online_release)
    path = Path(path)
    if not path.is_file():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    return validate_release(raw)


def validate_release(raw):
    raw = copy.deepcopy(raw)
    if not isinstance(raw, dict) or raw.get("schema") != 1 or raw.get("app_id") != APP_ID:
        raise MiFontAppError("Invalid MiFont release descriptor.")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", raw.get("version", "")):
        raise MiFontAppError("Invalid MiFont release version.")
    _relative(raw.get("entry"))
    if not raw["entry"].lower().endswith(".exe"):
        raise MiFontAppError("This MiFont package has no standalone Windows runtime.")
    _sha(raw.get("manifest_sha256"))
    archive = raw.get("archive", {})
    if not isinstance(archive, dict):
        raise MiFontAppError("Invalid MiFont archive descriptor.")
    _sha(archive.get("sha256"))
    if type(archive.get("size")) is not int or not 0 < archive["size"] <= MAX_ARCHIVE_BYTES:
        raise MiFontAppError("Invalid MiFont download size.")
    if archive.get("bundled"):
        _relative(archive["bundled"])
    if archive.get("url"):
        _download_url(archive["url"])
    if not archive.get("bundled") and not archive.get("url"):
        raise MiFontAppError("MiFont has no installation package.")
    return raw


def check_catalogue(release, history=()):
    current = validate_release(release)
    previous = tuple(validate_release(item) for item in history)
    bundled = load_release()
    if bundled and tuple(map(int, current["version"].split("."))) < tuple(map(int, bundled["version"].split("."))):
        raise MiFontAppError("MiFont catalogue is older than the bundled release.")
    if bundled and current["version"] == bundled["version"] and current != bundled:
        raise MiFontAppError("MiFont release identity changed without a version change.")
    return current, previous


def use_catalogue(release, history=()):
    """Accept only descriptors already authenticated by app_catalogue."""
    global _online_release, _online_history
    current, previous = check_catalogue(release, history)
    _online_release, _online_history = current, previous


def _download_url(url):
    if not isinstance(url, str):
        raise MiFontAppError("Invalid MiFont download URL.")
    parsed = urllib.parse.urlsplit(url)
    allowed = ((parsed.hostname == "mitomyhy.github.io" and parsed.path.startswith(("/tools/downloads/", "/releases/mifont/")))
               or (parsed.hostname == "github.com" and parsed.path.startswith("/Mitomyhy/")))
    if (parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in {None, 443}
            or parsed.fragment or not allowed):
        raise MiFontAppError("MiFont downloads must use the owner's HTTPS release location.")
    return url


def _target(data_root, release):
    return _plain_path(Path(data_root) / "Apps" / "MiFont" / release["version"])


def _manifest(folder, release):
    path = _plain_path(Path(folder) / "mifont-app.json")
    if not path.is_file() or _digest(path) != release["manifest_sha256"]:
        raise MiFontAppError("MiFont file manifest does not match this release.")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise MiFontAppError("Invalid MiFont runtime manifest.")
    if any(manifest.get(key) != release.get(key) for key in ("schema", "app_id", "version", "entry")):
        raise MiFontAppError("MiFont package identity mismatch.")
    files = manifest.get("files")
    if not isinstance(files, list) or not files or len(files) > 15000:
        raise MiFontAppError("Invalid MiFont runtime file list.")
    result, folded, total = {}, set(), 0
    for item in files:
        if not isinstance(item, dict):
            raise MiFontAppError("Invalid MiFont runtime file entry.")
        name = str(_relative(item.get("path")))
        if name.casefold() in folded or name.casefold() == "mifont-app.json":
            raise MiFontAppError("Duplicate MiFont runtime path.")
        folded.add(name.casefold())
        size = item.get("size")
        if type(size) is not int or size < 0:
            raise MiFontAppError("Invalid MiFont runtime file size.")
        total += size
        if total > MAX_RUNTIME_BYTES:
            raise MiFontAppError("MiFont runtime is too large.")
        result[name] = {"size": size, "sha256": _sha(item.get("sha256"))}
    if release["entry"] not in result:
        raise MiFontAppError("MiFont executable is missing from the manifest.")
    return result


def verify_install(folder, release):
    """Verify every installed executable/dependency before it can be launched."""
    folder = _plain_path(folder)
    files = _manifest(folder, release)
    for name, item in files.items():
        path = _plain_path(folder / name)
        if not path.is_file() or path.stat().st_size != item["size"] or _digest(path) != item["sha256"]:
            raise MiFontAppError("MiFont runtime file is missing or changed: " + name)
    actual = {p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file()}
    if actual != set(files) | {"mifont-app.json"}:
        raise MiFontAppError("Unexpected files in the MiFont runtime folder.")
    return folder


def installed_state(data_root, release=_DEFAULT_RELEASE):
    release = load_release() if release is _DEFAULT_RELEASE else release
    state = {"mifont_installed": False, "mifont_version": "", "mifont_available": bool(release),
             "mifont_latest_version": release["version"] if release else "", "mifont_path": "",
             "mifont_current_verified": False, "mifont_update_available": False, "mifont_repair_required": False}
    if not release:
        return state
    try:
        target = _target(data_root, release)
        state["mifont_repair_required"] = target.exists()
        verify_install(target, release)
    except (OSError, ValueError, MiFontAppError):
        pass
    else:
        state.update(mifont_installed=True, mifont_version=release["version"], mifont_path=str(target),
                     mifont_current_verified=True, mifont_repair_required=False)
        return state
    current_version = tuple(int(part) for part in release["version"].split(".")) if re.fullmatch(r"\d+(?:\.\d+)*", release["version"]) else ()
    for previous in sorted((*_online_history, *TRUSTED_PREVIOUS_RELEASES),
                           key=lambda item: tuple(map(int, item["version"].split("."))), reverse=True):
        previous_version = tuple(int(part) for part in previous["version"].split("."))
        if not current_version or previous_version >= current_version:
            continue
        try:
            previous_target = verify_install(_target(data_root, previous), previous)
        except (OSError, ValueError, MiFontAppError):
            continue
        state.update(mifont_installed=True, mifont_version=previous["version"], mifont_path=str(previous_target),
                     mifont_update_available=True)
        return state
    return state


class _ReleaseRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        # GitHub release asset redirects are signed HTTPS CDN URLs. Other
        # redirects must remain on the owner's canonical release locations.
        parsed = urllib.parse.urlsplit(newurl)
        if parsed.hostname == "release-assets.githubusercontent.com":
            if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in {None, 443}:
                raise MiFontAppError("Insecure MiFont release redirect.")
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
        candidate = cache / ("MiFont-" + release["version"] + ".zip")
        if not (candidate.is_file() and candidate.stat().st_size == info["size"]
                and _digest(candidate) == info["sha256"]):
            if not info.get("url"):
                raise MiFontAppError("The offline MiFont package is missing. Download the complete launcher bundle.")
            partial = cache / (".mifont-" + uuid.uuid4().hex + ".part")
            try:
                opener = urllib.request.build_opener(_ReleaseRedirects())
                request = urllib.request.Request(_download_url(info["url"]), headers={"User-Agent": "MITOM-Launcher"})
                with opener.open(request, timeout=120) as response, partial.open("xb") as stream:
                    total = 0
                    while chunk := response.read(1024 * 1024):
                        total += len(chunk)
                        if total > info["size"]:
                            raise MiFontAppError("MiFont download exceeded its verified size.")
                        stream.write(chunk)
                        if progress:
                            progress(total, info["size"])
                if total != info["size"] or _digest(partial) != info["sha256"]:
                    raise MiFontAppError("MiFont download checksum mismatch.")
                os.replace(partial, candidate)
            finally:
                partial.unlink(missing_ok=True)
    if candidate.stat().st_size != info["size"] or _digest(candidate) != info["sha256"]:
        raise MiFontAppError("MiFont package checksum mismatch.")
    return candidate


def install_app(data_root, release=_DEFAULT_RELEASE, *, bundle_root=BUNDLE_ROOT, progress=None):
    """Install verified sidecar bytes; preserve all Photoshop/private data."""
    release = load_release() if release is _DEFAULT_RELEASE else release
    if not release:
        raise MiFontAppError("MiFont is not included in this launcher release.")
    archive = _archive_path(data_root, release, bundle_root, progress)
    target = _target(data_root, release)
    parent = target.parent
    parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".mifont-stage-", dir=parent))
    backup = parent / (".mifont-backup-" + uuid.uuid4().hex)
    try:
        with zipfile.ZipFile(archive) as package:
            members = package.infolist()
            names = []
            for item in members:
                name = str(_relative(item.filename))
                if item.is_dir() or (item.external_attr >> 16) & 0o170000 == 0o120000:
                    raise MiFontAppError("MiFont ZIP must contain regular files only.")
                names.append(name)
            if len({name.casefold() for name in names}) != len(names):
                raise MiFontAppError("Duplicate files in MiFont ZIP.")
            if "mifont-app.json" not in names:
                raise MiFontAppError("MiFont ZIP has no runtime manifest.")
            manifest_info = package.getinfo("mifont-app.json")
            if manifest_info.file_size > 4 * 1024 * 1024:
                raise MiFontAppError("MiFont runtime manifest is too large.")
            (stage / "mifont-app.json").write_bytes(package.read("mifont-app.json"))
            files = _manifest(stage, release)
            if set(names) != set(files) | {"mifont-app.json"}:
                raise MiFontAppError("MiFont ZIP contents do not match its manifest.")
            for item in members:
                if item.filename == "mifont-app.json":
                    continue
                expected = files[item.filename]
                if item.file_size != expected["size"]:
                    raise MiFontAppError("MiFont ZIP runtime size mismatch.")
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
        raise MiFontAppError("MiFont is not included in this launcher release.")
    target = verify_install(_target(data_root, release), release)
    environment = dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1")
    return subprocess.Popen([str(target / release["entry"])], cwd=str(target), env=environment,
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
