"""Isolated acceptance test for the actual frozen launcher handover.

The normal updater does not depend on smoke mode. The smoke context replaces
only child persistence with an owned receipt; request validation and the real
withdrawn launcher UI/readiness/commit path still execute unchanged.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import time
import tkinter as tk
from types import SimpleNamespace
import uuid
import winreg
from unittest import mock

import installer
import self_update

ROOT_ENV = "MITOM_ACTIVATION_SMOKE_ROOT"
MODE_ENV = "MITOM_ACTIVATION_SMOKE_MODE"
_OWNER_KIND = "mitom-launcher-isolated-activation-smoke-v1"


def _digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _smoke_context(backend, request):
    value = os.environ.get(ROOT_ENV)
    if value is None:
        return None
    root = installer._plain(Path(value))
    if not root.is_absolute() or not re.fullmatch(r'activation-smoke-[0-9a-f]{32}', root.name):
        raise self_update.SelfUpdateError('Invalid isolated activation smoke root.')
    owner_path = installer._plain(root / 'smoke-owner.json')
    if not owner_path.is_file() or owner_path.stat().st_size > 8192:
        raise self_update.SelfUpdateError('Missing isolated activation smoke ownership record.')
    owner = json.loads(owner_path.read_text(encoding='utf-8'))
    expected_root = root / 'Local' / 'UOY' / 'Launcher'
    if (owner.get('schema') != 1 or owner.get('kind') != _OWNER_KIND
            or owner.get('parent_pid') != request.parent_pid
            or owner.get('version') != request.version or request.version != backend.APP_VERSION
            or owner.get('size') != request.size or owner.get('sha256') != request.sha256
            or Path(os.environ.get('LOCALAPPDATA', '')) != root / 'Local'
            or Path(os.environ.get('APPDATA', '')) != root / 'Roaming'
            or Path(backend.launcher_data_root()) != expected_root
            or request.target != expected_root / ('MITOMLauncher-' + request.version + '.exe')
            or request.path.parent.parent != expected_root / 'UpdateActivation'):
        raise self_update.SelfUpdateError('The isolated activation smoke context does not match this request.')
    if request.target.stat().st_size != request.size or _digest(request.target) != request.sha256:
        raise self_update.SelfUpdateError('The smoke launcher does not match its exact reviewed bytes.')
    return root


def _withdrawn_ui():
    root = tk._default_root
    if root is None or root.state() != 'withdrawn' or not root.winfo_children():
        raise self_update.SelfUpdateError('The actual launcher UI must be constructed and withdrawn before activation readiness.')
    return root


def persist_if_smoke(backend, request):
    """Return False normally, or validate smoke ownership and record no OS writes.

    The child calls this only after normal activation request validation and
    actual UI construction. An invalid smoke marker fails closed; it never
    falls back to real per-user persistence/registration.
    """
    if _smoke_context(backend, request) is None:
        return False
    root = _withdrawn_ui()
    self_update._check_deadline(request)
    self_update._write_record(request.path.parent / 'smoke-persistence.json', {
        **self_update._identity(request), 'pid': os.getpid(),
        'actual_launcher_ui_constructed': True, 'ui_withdrawn': root.state() == 'withdrawn',
        'ui_child_widgets': len(root.winfo_children()),
        'registry_written': False, 'shortcut_written': False})
    mode = os.environ.get(MODE_ENV, 'success')
    if mode == 'hang':
        self_update._write_record(request.path.parent / 'smoke-hang-started.json', {
            **self_update._identity(request), 'pid': os.getpid()})
        # Deliberately unready child for the timeout test. It exits cooperatively
        # on the same cancellation/deadline as normal withdrawn activation.
        while True:
            self_update._check_deadline(request)
            time.sleep(.05)
    if mode != 'success':
        raise self_update.SelfUpdateError('Unknown isolated activation smoke scenario.')
    return True


def finish_if_smoke(backend, request):
    """Record actual child commit consumption; caller closes its own hidden UI."""
    if _smoke_context(backend, request) is None:
        return False
    root = _withdrawn_ui()
    if not self_update.activation_committed(request):
        raise self_update.SelfUpdateError('The smoke child cannot finish before a valid parent commit.')
    self_update._write_record(request.path.parent / 'smoke-committed.json', {
        **self_update._identity(request), 'pid': os.getpid(),
        'commit_consumed': True, 'actual_launcher_ui_constructed': True,
        'ui_withdrawn': root.state() == 'withdrawn', 'registry_written': False,
        'shortcut_written': False, 'child_will_close_its_own_ui': True})
    return True


def _protocol_snapshot():
    """Read only the narrow program integration values; never write HKCU."""
    result = {}
    for suffix in ('', 'DefaultIcon', r'shell\open\command'):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Classes\uoy' + ('\\' + suffix if suffix else '')) as key:
                result[suffix] = winreg.QueryValueEx(key, '')[0]
        except FileNotFoundError:
            pass
    return result


def _alive(root):
    try:
        return bool(root.winfo_exists())
    except tk.TclError:
        return False


def _pump(root=None):
    if root is not None and _alive(root):
        root.update()
    time.sleep(.025)


def _wait(predicate, timeout, root=None):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        _pump(root)
    raise TimeoutError('The isolated launcher acceptance condition was not reached.')


@contextlib.contextmanager
def _environment(values):
    previous = {key: os.environ.get(key) for key in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def run(backend, output):
    if not getattr(sys, 'frozen', False):
        raise RuntimeError('The activation acceptance test must use the actual frozen executable.')
    executable = Path(sys.executable).resolve()
    size, checksum = executable.stat().st_size, _digest(executable)
    # Test executables/receipts stay in private build TEMP, never beside public
    # release files even when --result-json points into a release directory.
    base = installer._plain(Path(os.environ.get('TEMP', str(Path(output).resolve().parent))))
    root = base / ('activation-smoke-' + uuid.uuid4().hex)
    root.mkdir(parents=True, exist_ok=False)
    owner = {'schema': 1, 'kind': _OWNER_KIND, 'parent_pid': os.getpid(),
             'version': backend.APP_VERSION, 'size': size, 'sha256': checksum}
    (root / 'smoke-owner.json').write_text(json.dumps(owner, indent=2) + '\n', encoding='utf-8')
    protocol_before = _protocol_snapshot()
    roaming = Path(os.environ.get('APPDATA', ''))
    real_shortcut = roaming / 'Microsoft/Windows/Start Menu/Programs/MITOM Launcher.lnk'
    shortcut_before = _digest(real_shortcut) if real_shortcut.is_file() else None
    report = {'ok': False, 'version': backend.APP_VERSION, 'artifact_size': size,
              'artifact_sha256': checksum, 'actual_frozen_executable': True,
              'network': False, 'writes_panel': False, 'forced_process_kill': False,
              'real_user_registration_written': False}
    applications, attempts = [], []
    with _environment({ROOT_ENV: str(root), MODE_ENV: 'success',
                       'LOCALAPPDATA': str(root / 'Local'), 'APPDATA': str(root / 'Roaming'),
                       'TEMP': str(root / 'Temp'), 'TMP': str(root / 'Temp')}):
        (root / 'Temp').mkdir()
        data_root = root / 'Local' / 'UOY' / 'Launcher'
        if Path(backend.launcher_data_root()) != data_root:
            raise RuntimeError('Launcher data root is not isolated; refuse the acceptance test.')
        previous_version = '.'.join(backend.APP_VERSION.split('.')[:-1] + [str(int(backend.APP_VERSION.split('.')[-1]) - 1)])
        def copy_download(artifact, destination, filename, progress=None):
            destination = Path(destination); destination.mkdir(parents=True, exist_ok=True)
            target = destination / filename; shutil.copyfile(executable, target)
            if progress: progress(size, size)
            return target
        parent_backend = SimpleNamespace(APP_VERSION=previous_version,
            LauncherError=backend.LauncherError, validate_artifact=backend.validate_artifact,
            version_tuple=backend.version_tuple, launcher_data_root=backend.launcher_data_root,
            download_artifact=copy_download)
        artifact = {'version': backend.APP_VERSION,
            'url': 'https://mitomyhy.github.io/releases/mitom-launcher/' + backend.APP_VERSION + '/MITOM-Launcher-Setup-' + backend.APP_VERSION + '.exe',
            'size': size, 'sha256': checksum}
        update = self_update.select_update(parent_backend, {'launcher': artifact})
        target = self_update.stage_update(parent_backend, update, idle=True, data_root=data_root)
        report['staged_exact_bytes'] = _digest(target) == checksum
        def application():
            app = backend.LauncherUI()
            app.root.withdraw()
            discovery = getattr(app, '_update_discovery', None)
            if discovery is not None: discovery.close()
            app.root.update_idletasks()
            applications.append(app)
            return app
        try:
            old = application()
            destroyed = []
            old.root.bind('<Destroy>', lambda event: destroyed.append(True) if event.widget is old.root else None, add=True)
            attempt = self_update.begin_activation(parent_backend, update, target, idle=True, timeout=30, data_root=data_root)
            attempts.append(attempt)
            def ready():
                return self_update.poll_activation(attempt) == 'ready'
            _wait(ready, 35, old.root)
            persistence = json.loads((attempt.request.path.parent / 'smoke-persistence.json').read_text())
            assert persistence['actual_launcher_ui_constructed'] and persistence['ui_withdrawn']
            assert not persistence['registry_written'] and not persistence['shortcut_written']
            assert _alive(old.root), 'Original launcher closed before healthy child readiness.'
            assert not (attempt.request.path.parent / 'commit.json').exists()
            assert not (attempt.request.path.parent / 'smoke-committed.json').exists()
            self_update.commit_activation(attempt)
            old._close()
            assert destroyed and not _alive(old.root)
            _wait(lambda: (attempt.request.path.parent / 'smoke-committed.json').is_file(), 10)
            child = json.loads((attempt.request.path.parent / 'smoke-committed.json').read_text())
            assert child['commit_consumed'] and child['ui_withdrawn']
            attempt.process.wait(timeout=10)
            assert attempt.process.returncode == 0
            report.update(healthy_new_child=True, child_readiness_before_parent_close=True,
                child_commit_consumed=True, old_launcher_ui_resources_released=True,
                successful_child_clean_exit=True)

            old = application()
            with mock.patch.object(self_update.subprocess, 'Popen', side_effect=OSError('Controlled spawn failure')):
                try:
                    self_update.begin_activation(parent_backend, update, target, idle=True, data_root=data_root)
                except OSError:
                    pass
                else:
                    raise AssertionError('Controlled failed spawn was not reported.')
            assert _alive(old.root)
            responsive = []
            old.root.after(0, lambda: responsive.append(True)); old.root.update()
            assert responsive
            report['failed_spawn_preserves_original_launcher'] = True

            os.environ[MODE_ENV] = 'hang'
            attempt = self_update.begin_activation(parent_backend, update, target, idle=True, timeout=10, data_root=data_root)
            attempts.append(attempt)
            _wait(lambda: (attempt.request.path.parent / 'smoke-hang-started.json').exists(), 8, old.root)
            timed_out = False; started = time.monotonic()
            while time.monotonic() - started < 15:
                try:
                    state = self_update.poll_activation(attempt)
                    assert state == 'waiting'
                except self_update.SelfUpdateError:
                    timed_out = True; break
                _pump(old.root)
            assert timed_out and _alive(old.root)
            self_update.cancel_activation(attempt)
            attempt.process.wait(timeout=10)
            assert not (attempt.request.path.parent / 'commit.json').exists()
            assert not (attempt.request.path.parent / 'smoke-committed.json').exists()
            responsive = []
            old.root.after(0, lambda: responsive.append(True)); old.root.update()
            assert responsive
            report.update(unready_child_preserves_original_launcher=True,
                          unready_child_exits_cooperatively=True,
                          original_launcher_responsive_after_failure=True)
        finally:
            for attempt in attempts:
                if attempt.process.poll() is None:
                    self_update.cancel_activation(attempt)
                    attempt.process.wait(timeout=10)
            for app in applications:
                if _alive(app.root): app.root.destroy()
    shortcut_after = _digest(real_shortcut) if real_shortcut.is_file() else None
    assert _protocol_snapshot() == protocol_before
    assert shortcut_after == shortcut_before
    report.update(ok=True, real_uoy_protocol_unchanged=True, real_start_menu_shortcut_unchanged=True,
                  actual_child_ui_never_displayed=True, preserves_old_launcher_files=True)
    return report


def main(backend):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--activation-smoke', action='store_true', required=True)
    parser.add_argument('--result-json', type=Path, required=True)
    options = parser.parse_args()
    try:
        report = run(backend, options.result_json)
    except Exception as exc:
        report = {'ok': False, 'version': backend.APP_VERSION, 'error': backend.failure_details(exc),
                  'network': False, 'writes_panel': False, 'forced_process_kill': False}
    options.result_json.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    if sys.stdout: print(json.dumps(report))
    return 0 if report['ok'] else 1
