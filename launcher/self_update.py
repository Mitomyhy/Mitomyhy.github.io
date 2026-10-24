"""Verified launcher replacement and a bounded, two-phase UI handover.

The caller must supply release data already authenticated by the signed feed.
This module deliberately never reads latest-download.json or discovers another
executable by trusting a folder. It never stops the current process, deletes old
versions, installs the Photoshop panel, or changes credentials.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import tempfile
import time
import urllib.parse

import installer


class SelfUpdateError(RuntimeError):
    pass


@dataclass(frozen=True)
class LauncherUpdate:
    version: str
    url: str
    size: int
    sha256: str

    def artifact(self):
        return {"version": self.version, "url": self.url, "size": self.size, "sha256": self.sha256}


@dataclass(frozen=True)
class ActivationRequest:
    path: Path
    nonce: str
    target: Path
    version: str
    size: int
    sha256: str
    expires_at: float
    parent_pid: int


@dataclass
class ActivationAttempt:
    request: ActivationRequest
    process: object
    deadline: float
    committed: bool = False


def _idle(idle):
    if idle is not True:
        raise SelfUpdateError("Wait for the current operation to finish before updating the launcher.")


def _validate_update(backend, update):
    if not isinstance(update, LauncherUpdate):
        raise SelfUpdateError("An authenticated launcher update is required.")
    if not isinstance(update.version, str) or not re.fullmatch(r"\d{4}\.\d{2}\.\d{2}\.\d+", update.version):
        raise SelfUpdateError("Invalid launcher update version.")
    try:
        backend.validate_artifact(update.artifact())
        if backend.version_tuple(update.version) <= backend.version_tuple(backend.APP_VERSION):
            raise SelfUpdateError("The launcher update must be newer than this application.")
    except (ValueError, backend.LauncherError) as exc:
        raise SelfUpdateError("Invalid authenticated launcher update.") from exc
    parsed = urllib.parse.urlsplit(update.url)
    allowed = ((parsed.hostname == "mitomyhy.github.io" and parsed.path.startswith("/releases/mitom-launcher/"))
               or (parsed.hostname == "github.com" and parsed.path.startswith("/Mitomyhy/Mitomyhy.github.io/releases/download/")))
    if not allowed or parsed.fragment or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise SelfUpdateError("Launcher updates must use the owner's authenticated release location.")
    if not parsed.path.lower().endswith(".exe"):
        raise SelfUpdateError("The launcher update must be a Windows executable.")


def select_update(backend, verified_release):
    """Select only from the payload returned by signed-feed verification.

    This is a trust boundary: do not pass a raw network JSON document here.
    Authentication stays in backend.fetch_release/probe_signed_release.
    """
    if not isinstance(verified_release, dict):
        raise SelfUpdateError("An authenticated release payload is required.")
    artifact = verified_release.get("launcher")
    if artifact is None:
        return None
    if not isinstance(artifact, dict):
        raise SelfUpdateError("Invalid launcher artifact in the authenticated release.")
    try:
        update = LauncherUpdate(**{key: artifact[key] for key in ("version", "url", "size", "sha256")})
        backend.validate_artifact(update.artifact())
        comparison = backend.version_tuple(update.version) > backend.version_tuple(backend.APP_VERSION)
    except (KeyError, TypeError, ValueError, backend.LauncherError) as exc:
        raise SelfUpdateError("Invalid launcher artifact in the authenticated release.") from exc
    if not comparison:
        return None
    _validate_update(backend, update)
    return update


def _plain(path):
    # Uses the same ancestor/reparse-point checks as panel installation, without
    # calling installation or reading any panel configuration.
    try:
        return installer._plain(Path(path))
    except (OSError, ValueError, installer.InstallError) as exc:
        raise SelfUpdateError("The launcher update location is not a plain local path.") from exc


def _root(backend, data_root):
    return _plain(data_root if data_root is not None else backend.launcher_data_root())


def _target(root, version):
    return _plain(root / ("MITOMLauncher-" + version + ".exe"))


def _verify(path, size, checksum):
    path = _plain(path)
    if not path.is_file() or path.stat().st_size != size:
        raise SelfUpdateError("The launcher executable is missing or has the wrong size.")
    with path.open("rb") as stream:
        header = stream.read(2)
        stream.seek(0)
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    if header != b"MZ" or actual != checksum:
        raise SelfUpdateError("The launcher executable does not match the authenticated release.")
    return path


def _publish_new_file(temporary, destination):
    # Windows rename is atomic within this folder and refuses an existing
    # destination. Avoid hard links, which some Windows profiles prohibit.
    if os.name == "nt":
        os.rename(temporary, destination)
    else:
        os.link(temporary, destination)


def stage_update(backend, update, *, idle, progress=None, data_root=None):
    """Download and publish exact authenticated bytes at a versioned path.

    Safe to run in a worker after the UI has marked its transaction busy. An
    existing target must match the release exactly, even when already installed.
    """
    _idle(idle)
    _validate_update(backend, update)
    root = _root(backend, data_root)
    target = _target(root, update.version)
    if target.exists():
        return _verify(target, update.size, update.sha256)
    cache = _plain(root / "Downloads" / "Launcher" / update.version)
    cache.mkdir(parents=True, exist_ok=True)
    downloaded = backend.download_artifact(update.artifact(), cache,
                                          "MITOM-Launcher-Setup-" + update.version + ".exe", progress)
    downloaded = _verify(downloaded, update.size, update.sha256)
    root.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix="launcher-update-", suffix=".tmp", dir=root)
    os.close(descriptor)
    temporary = Path(name)
    try:
        shutil.copyfile(downloaded, temporary)
        _verify(temporary, update.size, update.sha256)
        _plain(target)
        try:
            _publish_new_file(temporary, target)
        except FileExistsError:
            # Another transaction may have published the same release. Accept
            # only exact bytes; a changed executable remains untouched.
            _verify(target, update.size, update.sha256)
        return _verify(target, update.size, update.sha256)
    finally:
        temporary.unlink(missing_ok=True)


def _read_record(path):
    path = _plain(path)
    if not path.is_file() or path.stat().st_size > 8192:
        raise SelfUpdateError("Invalid launcher activation record.")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SelfUpdateError("Invalid launcher activation record.") from exc
    if not isinstance(value, dict):
        raise SelfUpdateError("Invalid launcher activation record.")
    return value


def _write_record(path, value):
    path = _plain(path)
    payload = (json.dumps(value, sort_keys=True) + "\n").encode("utf-8")
    descriptor, name = tempfile.mkstemp(prefix="activation-", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            _publish_new_file(temporary, path)
        except FileExistsError:
            if _read_record(path) != value:
                raise SelfUpdateError("A different launcher activation record already exists.")
    finally:
        temporary.unlink(missing_ok=True)


def _identity(request):
    return {"schema": 1, "nonce": request.nonce, "version": request.version,
            "sha256": request.sha256, "target": str(request.target)}


def begin_activation(backend, update, target, *, idle, timeout=30, data_root=None):
    """Spawn the new launcher withdrawn; the caller must poll before closing UI."""
    _idle(idle)
    _validate_update(backend, update)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 5 <= timeout <= 60:
        raise SelfUpdateError("Launcher activation timeout must be between 5 and 60 seconds.")
    root = _root(backend, data_root)
    target = _verify(target, update.size, update.sha256)
    if target != _target(root, update.version):
        raise SelfUpdateError("Only the verified versioned launcher can be activated.")
    nonce = secrets.token_hex(32)
    folder = _plain(root / "UpdateActivation" / nonce)
    folder.mkdir(parents=True, exist_ok=False)
    now = time.time()
    request = ActivationRequest(folder / "request.json", nonce, target, update.version,
                                update.size, update.sha256, now + timeout, os.getpid())
    _write_record(request.path, {**_identity(request), "size": request.size, "issued_at": now,
                                "expires_at": request.expires_at, "parent_pid": request.parent_pid})
    process = subprocess.Popen([str(target), "--activate-launcher", str(request.path)],
                               cwd=str(root), env=dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1"),
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               close_fds=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return ActivationAttempt(request, process, time.monotonic() + timeout)


def read_activation_request(backend, path, executable, *, data_root=None):
    """Child-side validation for exactly --activate-launcher <request.json>.

    The entry point must require exactly those two arguments and reject any
    combination with protocol links, install flags, or unrelated CLI options.
    """
    root = _root(backend, data_root)
    path = _plain(path)
    if path.name != "request.json" or path.parent.parent != root / "UpdateActivation":
        raise SelfUpdateError("Launcher activation requests must use their managed location.")
    nonce = path.parent.name
    if not re.fullmatch(r"[0-9a-f]{64}", nonce):
        raise SelfUpdateError("Invalid launcher activation request identity.")
    value = _read_record(path)
    keys = {"schema", "nonce", "version", "sha256", "target", "size", "issued_at", "expires_at", "parent_pid"}
    if set(value) != keys or value.get("schema") != 1 or value.get("nonce") != nonce:
        raise SelfUpdateError("Invalid launcher activation request identity.")
    issued, expires = value["issued_at"], value["expires_at"]
    if (type(issued) not in (int, float) or type(expires) not in (int, float)
            or not 5 <= expires - issued <= 60 or issued > time.time() + 2 or expires < time.time()):
        raise SelfUpdateError("The launcher activation request has expired.")
    version = value["version"]
    if version != backend.APP_VERSION:
        raise SelfUpdateError("The activation request targets a different launcher version.")
    if type(value["size"]) is not int or value["size"] < 1 or not re.fullmatch(r"[0-9a-f]{64}", str(value["sha256"])):
        raise SelfUpdateError("Invalid launcher activation checksum.")
    if type(value["parent_pid"]) is not int or value["parent_pid"] < 1:
        raise SelfUpdateError("Invalid launcher activation parent.")
    target = _target(root, version)
    if _plain(value["target"]) != target or _plain(executable) != target:
        raise SelfUpdateError("The activation request must refer to this exact installed launcher.")
    _verify(target, value["size"], value["sha256"])
    return ActivationRequest(path, nonce, target, version, value["size"], value["sha256"], expires, value["parent_pid"])


def _check_deadline(request):
    if time.time() > request.expires_at:
        raise SelfUpdateError("The new launcher did not finish activation in time. Keep this window open and retry.")
    if _plain(request.path.parent / "cancel.json").exists():
        raise SelfUpdateError("Launcher activation was cancelled; the original launcher remains open.")


def mark_activation_ready(request):
    """Child calls after persistence and UI construction, while still withdrawn."""
    _check_deadline(request)
    _write_record(request.path.parent / "ready.json", _identity(request))


def poll_activation(attempt):
    """Parent polls on the Tk thread: returns waiting/ready, or raises on failure."""
    if attempt.process.poll() is not None:
        raise SelfUpdateError("The updated launcher could not start. The original launcher remains open.")
    if time.monotonic() > attempt.deadline:
        raise SelfUpdateError("The new launcher did not become ready in time. The original launcher remains open.")
    _check_deadline(attempt.request)
    path = _plain(attempt.request.path.parent / "ready.json")
    if not path.exists():
        return "waiting"
    if _read_record(path) != _identity(attempt.request):
        raise SelfUpdateError("The new launcher returned an invalid readiness response.")
    return "ready"


def commit_activation(attempt):
    """Parent acknowledges healthy startup, then closes only its own idle UI."""
    if poll_activation(attempt) != "ready":
        raise SelfUpdateError("The updated launcher is not ready to show.")
    _write_record(attempt.request.path.parent / "commit.json", _identity(attempt.request))
    attempt.committed = True
    return attempt.request.target, attempt.process


def cancel_activation(attempt):
    """Request cooperative child exit without killing either application."""
    _write_record(attempt.request.path.parent / "cancel.json", _identity(attempt.request))


def activation_committed(request):
    """Child polls while withdrawn; timeout/cancellation means close its own UI."""
    _check_deadline(request)
    path = _plain(request.path.parent / "commit.json")
    if not path.exists():
        return False
    if _read_record(path) != _identity(request):
        raise SelfUpdateError("Invalid launcher activation acknowledgement.")
    return True
