"""Verified local Product Video installation and fixed bundled-runtime launch.

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
import stat
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET
import zipfile

APP_ID = "product-video"
BUNDLE_ROOT = Path(__file__).resolve().parent
RELEASE_PATH = BUNDLE_ROOT / "product-video-release.json"
MAX_ARCHIVE_BYTES = 1536 * 1024 * 1024
MAX_RUNTIME_BYTES = 4 * 1024 * 1024 * 1024
MAX_PART_BYTES = 95 * 1024 * 1024
_DEFAULT_RELEASE = object()
_online_release = None
_online_history = ()
TRUSTED_PREVIOUS_RELEASES = ()
REQUIRED_RUNTIME_FILES = frozenset({
    "python/python.exe", "node/node.exe", "worker/product_server.py",
    "worker/product_assets.py", "worker/prompt_planner.py", "worker/process_limits.py",
    "renderer/render.mjs", "web/index.html",
    "cep/com.mitom.productvideo/CSXS/manifest.xml", "cep/com.mitom.productvideo/index.html",
})


class VideoAppError(RuntimeError):
    pass


def _digest(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _sha(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise VideoAppError("Invalid MITOM Product Video checksum.")
    return value


def _relative(value):
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise VideoAppError("Invalid MITOM Product Video runtime path.")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in value.split("/")):
        raise VideoAppError("Invalid MITOM Product Video runtime path.")
    for part in value.split("/"):
        if (part.endswith((" ", ".")) or any(ord(char) < 32 for char in part)
                or any(char in '<>"|?*' for char in part)
                or re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", part)):
            raise VideoAppError("Unsafe Windows path in the Product Video runtime.")
    return path


def _plain_path(path):
    path = Path(path)
    for part in (path, *path.parents):
        if part.is_symlink() or getattr(part, "is_junction", lambda: False)():
            raise VideoAppError("MITOM Product Video cannot install through a linked folder.")
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
        raise VideoAppError("Invalid MITOM Product Video release descriptor.")
    if not isinstance(raw.get("version"), str) or not re.fullmatch(r"\d+\.\d+\.\d+", raw["version"]):
        raise VideoAppError("Invalid MITOM Product Video release version.")
    _relative(raw.get("entry"))
    if raw["entry"] != "python/python.exe":
        raise VideoAppError("Product Video must use its verified bundled Python entry.")
    _sha(raw.get("manifest_sha256"))
    archive = raw.get("archive", {})
    if not isinstance(archive, dict):
        raise VideoAppError("Invalid MITOM Product Video archive descriptor.")
    _sha(archive.get("sha256"))
    if type(archive.get("size")) is not int or not 0 < archive["size"] <= MAX_ARCHIVE_BYTES:
        raise VideoAppError("Invalid MITOM Product Video download size.")
    if archive.get("bundled"):
        _relative(archive["bundled"])
    if archive.get("url"):
        _download_url(archive["url"])
    if "parts" in archive:
        parts = archive["parts"]
        if not isinstance(parts, list) or not 2 <= len(parts) <= 32:
            raise VideoAppError("Invalid Product Video archive parts.")
        seen, total = set(), 0
        for part in parts:
            if not isinstance(part, dict) or set(part) != {"url", "size", "sha256"}:
                raise VideoAppError("Invalid Product Video archive part descriptor.")
            _sha(part["sha256"])
            _download_url(part["url"])
            if part["url"] in seen or type(part["size"]) is not int or not 0 < part["size"] <= MAX_PART_BYTES:
                raise VideoAppError("Invalid Product Video archive part identity or size.")
            seen.add(part["url"])
            total += part["size"]
        if total != archive["size"]:
            raise VideoAppError("Product Video archive parts do not total the verified ZIP size.")
    if not archive.get("bundled") and not archive.get("url"):
        raise VideoAppError("MITOM Product Video has no installation package.")
    return raw


def check_catalogue(release, history=()):
    current = validate_release(release)
    previous = tuple(validate_release(item) for item in history)
    bundled = load_release()
    if bundled and tuple(map(int, current["version"].split("."))) < tuple(map(int, bundled["version"].split("."))):
        raise VideoAppError("MITOM Product Video catalogue is older than the bundled release.")
    if bundled and current["version"] == bundled["version"] and current != bundled:
        raise VideoAppError("MITOM Product Video release identity changed without a version change.")
    return current, previous


def use_catalogue(release, history=()):
    """Accept only descriptors already authenticated by app_catalogue."""
    global _online_release, _online_history
    current, previous = check_catalogue(release, history)
    _online_release, _online_history = current, previous


def _download_url(url):
    if not isinstance(url, str):
        raise VideoAppError("Invalid MITOM Product Video download URL.")
    parsed = urllib.parse.urlsplit(url)
    _relative(urllib.parse.unquote(parsed.path).lstrip("/"))
    allowed = ((parsed.hostname == "mitomyhy.github.io" and parsed.path.startswith(("/tools/downloads/", "/releases/product-video/")))
               or (parsed.hostname == "github.com" and parsed.path.startswith("/Mitomyhy/Mitomyhy.github.io/releases/download/")))
    if (parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in {None, 443}
            or parsed.query or parsed.fragment or not allowed):
        raise VideoAppError("MITOM Product Video downloads must use the owner's HTTPS release location.")
    return url


def _target(data_root, release):
    return _plain_path(Path(data_root) / "Apps" / "ProductVideo" / release["version"])


def _manifest(folder, release):
    path = _plain_path(Path(folder) / "product-video-app.json")
    if not path.is_file() or _digest(path) != release["manifest_sha256"]:
        raise VideoAppError("MITOM Product Video file manifest does not match this release.")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise VideoAppError("Invalid MITOM Product Video runtime manifest.")
    if any(manifest.get(key) != release.get(key) for key in ("schema", "app_id", "version", "entry")):
        raise VideoAppError("MITOM Product Video package identity mismatch.")
    files = manifest.get("files")
    if not isinstance(files, list) or not files or len(files) > 30000:
        raise VideoAppError("Invalid MITOM Product Video runtime file list.")
    result, folded, total = {}, set(), 0
    for item in files:
        if not isinstance(item, dict):
            raise VideoAppError("Invalid MITOM Product Video runtime file entry.")
        name = str(_relative(item.get("path")))
        if name.casefold() in folded or name.casefold() == "product-video-app.json":
            raise VideoAppError("Duplicate MITOM Product Video runtime path.")
        folded.add(name.casefold())
        size = item.get("size")
        if type(size) is not int or size < 0:
            raise VideoAppError("Invalid MITOM Product Video runtime file size.")
        total += size
        if total > MAX_RUNTIME_BYTES:
            raise VideoAppError("MITOM Product Video runtime is too large.")
        result[name] = {"size": size, "sha256": _sha(item.get("sha256"))}
    if not REQUIRED_RUNTIME_FILES.issubset(result):
        raise VideoAppError("Product Video is missing its fixed local runtime components.")
    return result


def verify_install(folder, release):
    """Verify every installed executable/dependency before it can be launched."""
    folder = _plain_path(folder)
    files = _manifest(folder, release)
    actual = _runtime_inventory(folder)
    if set(actual) != set(files) | {"product-video-app.json"}:
        raise VideoAppError("Unexpected files in the MITOM Product Video runtime folder.")
    for name, item in files.items():
        path = folder / name
        if actual[name].st_size != item["size"] or _digest(path) != item["sha256"]:
            raise VideoAppError("MITOM Product Video runtime file is missing or changed: " + name)
    return folder


def _runtime_inventory(folder):
    """Check each directory/file once, without rewalking every parent chain.

    This inventory is local to one verification. No prior hash/metadata result
    can authorize a later launch. Every reparse point and special file is denied.
    """
    result = {}
    def visit(directory):
        with os.scandir(directory) as entries:
            for entry in entries:
                info = entry.stat(follow_symlinks=False)
                if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                    raise VideoAppError("Linked files/folders are not allowed in the Product Video runtime.")
                path = Path(entry.path)
                if stat.S_ISDIR(info.st_mode):
                    visit(path)
                elif stat.S_ISREG(info.st_mode):
                    result[path.relative_to(folder).as_posix()] = info
                else:
                    raise VideoAppError("Special files are not allowed in the Product Video runtime.")
    visit(folder)
    return result


def _identity(folder, release):
    """Fast catalogue state only. Execution always uses full verify_install."""
    folder = _plain_path(folder)
    files = _manifest(folder, release)
    for name in REQUIRED_RUNTIME_FILES:
        path = _plain_path(folder / name)
        if not path.is_file() or path.stat().st_size != files[name]["size"]:
            raise VideoAppError("A fixed Product Video runtime component is missing or incomplete.")
    return files


def _failure_path(data_root, release):
    return _plain_path(Path(data_root) / "Apps/ProductVideo" / (".integrity-failure-" + release["version"] + ".json"))


def _failed_integrity(data_root, release):
    try:
        path = _failure_path(data_root, release)
        if not path.is_file() or path.stat().st_size > 4096:
            return False
        return json.loads(path.read_text("utf-8")).get("manifest_sha256") == release["manifest_sha256"]
    except (OSError, ValueError, VideoAppError):
        return False


def _note_failed_integrity(data_root, release):
    # This is a negative UI hint outside the runtime, never an execution permit.
    try:
        path = _failure_path(data_root, release)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
        try:
            temporary.write_text(json.dumps({"manifest_sha256": release["manifest_sha256"]}), "utf-8")
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    except (OSError, ValueError, VideoAppError):
        pass


def _clear_failed_integrity(data_root, release):
    try:
        _failure_path(data_root, release).unlink(missing_ok=True)
    except (OSError, ValueError, VideoAppError):
        pass


def _verify_cep(folder, files):
    folder = _plain_path(folder)
    for name, row in files.items():
        path = _plain_path(folder / name)
        if not _verified_file(path, row):
            raise VideoAppError("Product Video Photoshop extension file differs: " + name)
    actual = {p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file()}
    if actual != set(files):
        raise VideoAppError("Unexpected files in the Product Video Photoshop extension.")


def install_cep(folder, release):
    """Install only this app's pinned extension; preserve the main Panel/core."""
    prefix = "cep/com.mitom.productvideo/"
    files = {name[len(prefix):]: row for name, row in _manifest(folder, release).items() if name.startswith(prefix)}
    if not {"CSXS/manifest.xml", "index.html"}.issubset(files):
        raise VideoAppError("The Product Video Photoshop extension is incomplete.")
    source = _plain_path(Path(folder) / "cep/com.mitom.productvideo")
    _verify_cep(source, files)
    document = (source / "CSXS/manifest.xml").read_bytes()
    if len(document) > 65536 or b"<!DOCTYPE" in document.upper():
        raise VideoAppError("Invalid Product Video Photoshop extension manifest.")
    try:
        manifest = ET.fromstring(document)
        if manifest.get("ExtensionBundleId") != "com.mitom.productvideo":
            raise ValueError("extension identity")
        ids = {extension.get("Id") for extension in manifest.findall("./ExtensionList/Extension")}
        if ids != {"com.mitom.productvideo.panel"}:
            raise ValueError("extension identity")
    except (ET.ParseError, ValueError) as exc:
        raise VideoAppError("The Product Video Photoshop extension identity differs.") from exc
    roaming = os.environ.get("APPDATA")
    if not roaming:
        raise VideoAppError("AppData is required to install the Photoshop Video extension.")
    cep = _plain_path(Path(roaming) / "Adobe/CEP")
    parent = _plain_path(cep / "extensions")
    target = _plain_path(parent / "com.mitom.productvideo")
    try:
        _verify_cep(target, files)
    except (OSError, ValueError, VideoAppError):
        pass
    else:
        return {"path": str(target), "changed": False}
    parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".product-video-stage-", dir=cep))
    backup = _plain_path(cep / "MITOMProductVideoBackups" / uuid.uuid4().hex)
    try:
        for name in files:
            destination = stage / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / name, destination)
        _verify_cep(stage, files)
        if target.exists():
            backup.parent.mkdir(parents=True, exist_ok=True)
            target.rename(backup)
        try:
            stage.rename(target)
        except BaseException:
            if backup.exists() and not target.exists():
                backup.rename(target)
            raise
        result = {"path": str(target), "changed": True}
        if backup.exists():
            result["backup"] = str(backup)
        return result
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def verify_installed_cep(folder, release, runtime_files=None):
    prefix = "cep/com.mitom.productvideo/"
    all_files = runtime_files if runtime_files is not None else _manifest(folder, release)
    files = {name[len(prefix):]: row for name, row in all_files.items() if name.startswith(prefix)}
    roaming = os.environ.get("APPDATA")
    if not roaming:
        raise VideoAppError("The Photoshop Video extension is not installed for this user.")
    _verify_cep(Path(roaming) / "Adobe/CEP/extensions/com.mitom.productvideo", files)


def installed_state(data_root, release=_DEFAULT_RELEASE):
    release = load_release() if release is _DEFAULT_RELEASE else release
    state = {"video_installed": False, "video_version": "", "video_available": bool(release),
             "video_latest_version": release["version"] if release else "", "video_path": "",
             "video_current_verified": False, "video_update_available": False, "video_repair_required": False}
    if not release:
        return state
    try:
        target = _target(data_root, release)
        state["video_repair_required"] = target.exists()
        files = _identity(target, release)
        verify_installed_cep(target, release, files)
        if _failed_integrity(data_root, release):
            raise VideoAppError("A full runtime verification previously failed; repair is required.")
    except (OSError, ValueError, VideoAppError):
        pass
    else:
        state.update(video_installed=True, video_version=release["version"], video_path=str(target),
                     video_current_verified=True, video_repair_required=False)
        return state
    current_version = tuple(int(part) for part in release["version"].split(".")) if re.fullmatch(r"\d+(?:\.\d+)*", release["version"]) else ()
    for previous in sorted((*_online_history, *TRUSTED_PREVIOUS_RELEASES),
                           key=lambda item: tuple(map(int, item["version"].split("."))), reverse=True):
        previous_version = tuple(int(part) for part in previous["version"].split("."))
        if not current_version or previous_version >= current_version:
            continue
        try:
            previous_target = _target(data_root, previous)
            previous_files = _identity(previous_target, previous)
            verify_installed_cep(previous_target, previous, previous_files)
            if _failed_integrity(data_root, previous):
                continue
        except (OSError, ValueError, VideoAppError):
            continue
        state.update(video_installed=True, video_version=previous["version"], video_path=str(previous_target),
                     video_update_available=True)
        return state
    return state


class _ReleaseRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        # GitHub release asset redirects are signed HTTPS CDN URLs. Other
        # redirects must remain on the owner's canonical release locations.
        parsed = urllib.parse.urlsplit(newurl)
        if parsed.hostname == "release-assets.githubusercontent.com":
            if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in {None, 443}:
                raise VideoAppError("Insecure MITOM Product Video release redirect.")
        else:
            _download_url(newurl)
        return super().redirect_request(request, fp, code, message, headers, newurl)


def _verified_file(path, info):
    return path.is_file() and path.stat().st_size == info["size"] and _digest(path) == info["sha256"]


def _download_piece(cache, candidate, info, progress, offset, maximum):
    if _verified_file(candidate, info):
        if progress:
            progress(offset + info["size"], maximum)
        return candidate
    for attempt in range(3):
        partial = cache / (".product-video-" + uuid.uuid4().hex + ".part")
        try:
            opener = urllib.request.build_opener(_ReleaseRedirects())
            request = urllib.request.Request(_download_url(info["url"]), headers={"User-Agent": "MITOM-Launcher"})
            with opener.open(request, timeout=120) as response, partial.open("xb") as stream:
                total = 0
                while chunk := response.read(1024 * 1024):
                    total += len(chunk)
                    if total > info["size"]:
                        raise VideoAppError("MITOM Product Video download exceeded its verified size.")
                    stream.write(chunk)
                    if progress:
                        progress(offset + total, maximum)
            if total != info["size"]:
                raise ConnectionError("MITOM Product Video download ended before its verified size.")
            if _digest(partial) != info["sha256"]:
                raise VideoAppError("MITOM Product Video download checksum mismatch.")
            os.replace(partial, candidate)
            return candidate
        except urllib.error.HTTPError as exc:
            if attempt == 2 or exc.code not in {408, 429, 500, 502, 503, 504}:
                raise
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if attempt == 2:
                raise
        finally:
            partial.unlink(missing_ok=True)


def _archive_path(data_root, release, bundle_root, progress):
    info = release["archive"]
    bundle = Path(bundle_root) / info.get("bundled", "__not_bundled__")
    if info.get("bundled") and bundle.is_file():
        candidate = _plain_path(bundle)
    else:
        cache = _plain_path(Path(data_root) / "Downloads")
        cache.mkdir(parents=True, exist_ok=True)
        candidate = _plain_path(cache / ("ProductVideo-" + release["version"] + ".zip"))
        if not _verified_file(candidate, info):
            if not info.get("url"):
                raise VideoAppError("The offline MITOM Product Video package is missing. Download the complete launcher bundle.")
            if info.get("parts"):
                pieces, offset = [], 0
                for index, part in enumerate(info["parts"], 1):
                    piece = _plain_path(cache / ("ProductVideo-" + release["version"] + f".part{index:03d}"))
                    pieces.append(_download_piece(cache, piece, part, progress, offset, info["size"]))
                    offset += part["size"]
                joined = cache / (".product-video-" + uuid.uuid4().hex + ".part")
                try:
                    with joined.open("xb") as stream:
                        for piece in pieces:
                            with piece.open("rb") as source:
                                shutil.copyfileobj(source, stream, 1024 * 1024)
                    if not _verified_file(joined, info):
                        raise VideoAppError("Product Video combined archive checksum mismatch.")
                    os.replace(joined, candidate)
                finally:
                    joined.unlink(missing_ok=True)
            else:
                _download_piece(cache, candidate, info, progress, 0, info["size"])
    if candidate.stat().st_size != info["size"] or _digest(candidate) != info["sha256"]:
        raise VideoAppError("MITOM Product Video package checksum mismatch.")
    return candidate


def install_app(data_root, release=_DEFAULT_RELEASE, *, bundle_root=BUNDLE_ROOT, progress=None):
    """Install verified sidecar bytes; preserve all Photoshop/private data."""
    release = load_release() if release is _DEFAULT_RELEASE else release
    if not release:
        raise VideoAppError("MITOM Product Video is not included in this launcher release.")
    release = validate_release(release)
    archive = _archive_path(data_root, release, bundle_root, progress)
    target = _target(data_root, release)
    parent = target.parent
    parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".product-video-stage-", dir=parent))
    backup = parent / (".product-video-backup-" + uuid.uuid4().hex)
    try:
        with zipfile.ZipFile(archive) as package:
            members = package.infolist()
            names = []
            for item in members:
                name = str(_relative(item.filename))
                kind = (item.external_attr >> 16) & 0o170000
                if item.is_dir() or kind not in (0, 0o100000) or item.flag_bits & 1:
                    raise VideoAppError("MITOM Product Video ZIP must contain regular files only.")
                names.append(name)
            if len({name.casefold() for name in names}) != len(names):
                raise VideoAppError("Duplicate files in MITOM Product Video ZIP.")
            if "product-video-app.json" not in names:
                raise VideoAppError("MITOM Product Video ZIP has no runtime manifest.")
            manifest_info = package.getinfo("product-video-app.json")
            if manifest_info.file_size > 8 * 1024 * 1024:
                raise VideoAppError("MITOM Product Video runtime manifest is too large.")
            (stage / "product-video-app.json").write_bytes(package.read("product-video-app.json"))
            files = _manifest(stage, release)
            if set(names) != set(files) | {"product-video-app.json"}:
                raise VideoAppError("MITOM Product Video ZIP contents do not match its manifest.")
            for item in members:
                if item.filename == "product-video-app.json":
                    continue
                expected = files[item.filename]
                if item.file_size != expected["size"]:
                    raise VideoAppError("MITOM Product Video ZIP runtime size mismatch.")
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
        try:
            cep_result = install_cep(target, release)
        except BaseException:
            # Both components retain their prior working bytes if the add-on
            # cannot activate. Only this just-created, owned stage is removed.
            target.rename(stage)
            if backup.exists():
                backup.rename(target)
            raise
        # Keep an exact prior runtime (including user-added files) as a
        # recoverable backup. Never erase a modified/unknown old installation.
        result = {"version": release["version"], "path": str(target)}
        result["photoshop_extension"] = cep_result
        if backup.exists():
            result["backup"] = str(backup)
        _clear_failed_integrity(data_root, release)
        return result
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def open_app(data_root, release=_DEFAULT_RELEASE):
    release = load_release() if release is _DEFAULT_RELEASE else release
    if not release:
        raise VideoAppError("MITOM Product Video is not included in this launcher release.")
    try:
        target = verify_install(_target(data_root, release), release)
    except (OSError, ValueError, VideoAppError):
        _note_failed_integrity(data_root, release)
        raise
    _clear_failed_integrity(data_root, release)
    # No command shell, runtime download, arbitrary feed-supplied arguments,
    # system Python or global Node can be substituted into this launch path.
    environment = dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1")
    for name in ("PYTHONHOME", "PYTHONPATH", "NODE_OPTIONS", "NODE_PATH"):
        environment.pop(name, None)
    return subprocess.Popen([str(target / "python/python.exe"), "-I", "-B",
                             str(target / "worker/product_server.py"),
                             "--frontend", str(target / "web"), "--open-browser"],
                            cwd=str(target), env=environment,
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
