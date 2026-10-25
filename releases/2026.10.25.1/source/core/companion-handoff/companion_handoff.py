"""Pinned same-ID Chrome Companion code handoff; never edits Chrome preferences.

Only eight public code files are eligible. Browser/account storage, pairing,
permissions, processes and Photoshop documents are not read or changed.
"""
from __future__ import annotations
import argparse, base64, hashlib, json, os, re, stat, sys, time, uuid
from pathlib import Path

MANIFEST_SHA256 = '487684c5bb7cfea04b73dae17e59e413121747e1869cf7750ea5bab7c69280c1'
EXTENSION_ID = 'nbmhofblkjlmlijiokgdeojjomjcphhj'
FILES = ('README.txt', 'background.js', 'content.js', 'core.js', 'manifest.json', 'popup.css', 'popup.html', 'popup.js')
OWN_BACKUP = re.compile(r'^\.uoy-base-(?:r(?:55|56|57|58|59|60|61)|popup-d[123])-backups$')
PROFILE = re.compile(r'^(?:Default|Profile [0-9]{1,3})$')

class Pending(Exception):
    pass

def safe(path, missing=False):
    path = Path(path)
    raw = str(path)
    if not path.is_absolute() or raw.startswith(('\\\\', '//')) or path == Path(path.anchor) or '..' in path.parts or len(raw) > 220:
        raise Pending('UNSAFE_PATH')
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            value = current.lstat()
        except FileNotFoundError:
            if missing:
                continue
            raise Pending('MISSING_PATH') from None
        if stat.S_ISLNK(value.st_mode) or getattr(value, 'st_file_attributes', 0) & 0x400:
            raise Pending('REPARSE_PATH')
    return path

def read(path, limit=2 * 1024 * 1024):
    path = safe(path)
    value = path.lstat()
    if not stat.S_ISREG(value.st_mode) or value.st_size > limit:
        raise Pending('UNSAFE_FILE')
    data = path.read_bytes()
    if len(data) != value.st_size or len(data) > limit:
        raise Pending('FILE_CHANGED')
    return data

def sha(data):
    return hashlib.sha256(data).hexdigest()

def current_hash(path):
    try:
        return sha(read(path))
    except FileNotFoundError:
        return None
    except Pending as exc:
        if str(exc) == 'MISSING_PATH':
            return None
        raise

def load_manifest():
    raw = read(Path(__file__).with_name('companion-handoff-manifest.json'), 65536)
    if sha(raw) != MANIFEST_SHA256:
        raise Pending('HANDOFF_MANIFEST_INTEGRITY')
    value = json.loads(raw)
    if value['schema'] != 1 or value['extension_id'] != EXTENSION_ID or set(value['target']) != set(FILES):
        raise Pending('HANDOFF_MANIFEST_SHAPE')
    return value

def identity(data, expected):
    try:
        manifest = json.loads(data)
        key = base64.b64decode(manifest['key'], validate=True)
        derived = ''.join(chr(97 + int(n, 16)) for n in hashlib.sha256(key).hexdigest()[:32])
        if derived != EXTENSION_ID or manifest.get('name') != 'UOY Base Companion' or manifest.get('manifest_version') != 3:
            raise ValueError()
        return {key: value for key, value in manifest.items() if key != 'version'} == expected
    except (ValueError, KeyError, TypeError):
        return False

def source_bytes(root, manifest):
    root = safe(root)
    data = {name: read(root / name) for name in FILES}
    for name, raw in data.items():
        pin = manifest['target'][name]
        if len(raw) != pin['size'] or sha(raw) != pin['sha256']:
            raise Pending('SOURCE_INTEGRITY')
    expected = json.loads(data['manifest.json'])
    if expected.get('version') != '1.0.27':
        raise Pending('SOURCE_VERSION')
    expected.pop('version')
    if not identity(data['manifest.json'], expected):
        raise Pending('SOURCE_IDENTITY')
    return data, expected

def discover(chrome_root):
    roots, pending = {}, []
    try:
        chrome_root = safe(chrome_root)
    except Pending as exc:
        if str(exc) == 'MISSING_PATH':
            return [], []
        return [], [{'reason': 'CHROME_' + str(exc)}]
    profiles = sorted((p for p in chrome_root.iterdir() if PROFILE.fullmatch(p.name)), key=lambda p: p.name)
    if len(profiles) > 32:
        return [], [{'reason': 'PROFILE_LIMIT'}]
    for profile in profiles:
        found = set()
        for name in ('Preferences', 'Secure Preferences'):
            file = profile / name
            if not file.exists():
                continue
            try:
                prefs = json.loads(read(file, 32 * 1024 * 1024))
                entry = prefs.get('extensions', {}).get('settings', {}).get(EXTENSION_ID)
                if entry is None:
                    continue
                if not isinstance(entry, dict) or entry.get('location') != 4 or not isinstance(entry.get('path'), str):
                    raise Pending('NOT_UNPACKED')
                root = safe(entry['path'], missing=True)
                found.add(str(root))
            except (Pending, ValueError, TypeError) as exc:
                pending.append({'profile': profile.name, 'reason': str(exc) if isinstance(exc, Pending) else 'PREFERENCE_SHAPE'})
        if len(found) > 1:
            pending.append({'profile': profile.name, 'reason': 'CONFLICTING_REGISTRATION'})
            continue
        for root in found:
            roots.setdefault(os.path.normcase(root), {'root': root, 'profiles': []})['profiles'].append(profile.name)
    if len(roots) > 16:
        return [], pending + [{'reason': 'ROOT_LIMIT'}]
    return list(roots.values()), pending

def plan_root(row, source, expected_identity, manifest):
    root = safe(row['root'])
    if not root.is_dir():
        raise Pending('NOT_DIRECTORY')
    for child in root.iterdir():
        if child.name in FILES:
            continue
        if OWN_BACKUP.fullmatch(child.name) and safe(child).is_dir():
            continue  # Prior known code-only patch backups are preserved, never read.
        raise Pending('UNKNOWN_ROOT_CONTENT')
    before = {name: read(root / name) for name in FILES}
    if not identity(before['manifest.json'], expected_identity):
        raise Pending('EXTENSION_IDENTITY')
    hashes = {name: sha(raw) for name, raw in before.items()}
    accepted = next((v['label'] for v in manifest['accepted_vectors'] if hashes == v['files']), None)
    if accepted is None:
        raise Pending('UNKNOWN_CODE_VECTOR')
    return {'root': root, 'profiles': row['profiles'], 'before': before, 'hashes': hashes,
            'baseline': accepted, 'changed': any(before[name] != source[name] for name in FILES)}

def atomic(path, data, expected):
    path = safe(path, missing=True)
    if current_hash(path) != expected:
        raise Pending('DESTINATION_CHANGED')
    stage = path.with_name('.' + path.name + '.uoy-' + uuid.uuid4().hex + '.tmp')
    created = False
    try:
        descriptor = os.open(stage, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        created = True
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        safe(path.parent)
        if current_hash(path) != expected or sha(read(stage)) != sha(data):
            raise Pending('DESTINATION_CHANGED')
        os.replace(stage, path)
    finally:
        if created and stage.exists() and current_hash(stage) == sha(data):
            safe(stage).unlink()

def make_directory(path):
    path = safe(path, missing=True)
    path.mkdir(parents=True, exist_ok=True)
    safe(path)
    return path

def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8')

def run(source_root, chrome_root, backup_root, *, dry_run=False, writer=atomic):
    manifest = load_manifest()
    source, expected = source_bytes(source_root, manifest)
    rows, pending = discover(chrome_root)
    plans, roots = [], []
    for row in rows:
        try:
            plan = plan_root(row, source, expected, manifest)
            roots.append({'root': str(plan['root']), 'status': 'update_available' if plan['changed'] else 'already_current', 'baseline': plan['baseline']})
            if plan['changed']:
                plans.append(plan)
        except (Pending, OSError, ValueError) as exc:
            pending.append({'root': row['root'], 'reason': str(exc) if isinstance(exc, Pending) else 'ROOT_UNAVAILABLE'})
    result = {'schema': 1, 'target_version': '1.0.27', 'status': 'pending' if pending else 'already_current' if rows else 'not_registered',
              'roots': roots, 'pending': pending, 'updated_roots': 0, 'reload_required': False,
              'activation': 'not_observed', 'chrome_preferences_changed': False, 'pairing_changed': False}
    if not plans:
        return result
    backup_root = safe(backup_root, missing=True)
    for plan in plans:
        if backup_root == plan['root'] or plan['root'] in backup_root.parents or backup_root in plan['root'].parents:
            raise Pending('BACKUP_OVERLAP')
    chrome_root = safe(chrome_root)
    if backup_root == chrome_root or chrome_root in backup_root.parents or backup_root in chrome_root.parents:
        raise Pending('BACKUP_CHROME_OVERLAP')
    if dry_run:
        result['status'] = 'preflight_pending' if pending else 'preflight'
        return result
    make_directory(backup_root)
    lock = backup_root / 'update.lock'
    try:
        descriptor = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        result['pending'].append({'reason': 'HANDOFF_BUSY_OR_INTERRUPTED'}); result['status'] = 'pending'; return result
    lock_data = uuid.uuid4().hex.encode('ascii')
    os.write(descriptor, lock_data); os.close(descriptor)
    marker = backup_root / 'pending.json'
    changed = []
    transaction = None
    marker_data = None
    try:
        if marker.exists():
            result['pending'].append({'reason': 'INTERRUPTED_HANDOFF_PRESERVED'}); result['status'] = 'pending'; return result
        transaction = make_directory(backup_root / (time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex))
        journal = {'schema': 1, 'target_version': '1.0.27', 'completed': False, 'roots': [], 'files': []}
        for index, plan in enumerate(plans):
            # Re-read the registration and the entire allowed vector immediately
            # before mutation, not merely filenames or a remembered old path.
            current_rows, current_pending = discover(chrome_root)
            if not any(os.path.normcase(row['root']) == os.path.normcase(str(plan['root'])) for row in current_rows):
                raise Pending('REGISTRATION_CHANGED')
            fresh = plan_root({'root': plan['root'], 'profiles': plan['profiles']}, source, expected, manifest)
            if fresh['hashes'] != plan['hashes']:
                raise Pending('DESTINATION_CHANGED')
            directory = make_directory(transaction / 'files' / str(index))
            journal['roots'].append(str(plan['root']))
            for name in FILES:
                atomic(directory / name, plan['before'][name], None)
                journal['files'].append({'root_index': index, 'name': name, 'before': plan['hashes'][name], 'after': sha(source[name])})
        journal_path = transaction / 'transaction.json'
        initial_journal = json_bytes(journal)
        atomic(journal_path, initial_journal, None)
        marker_data = json_bytes({'schema': 1, 'transaction': transaction.name})
        atomic(marker, marker_data, None)
        for plan in plans:
            for name in FILES:
                if plan['hashes'][name] == sha(source[name]):
                    continue
                changed.append((plan, name))
                writer(plan['root'] / name, source[name], plan['hashes'][name])
                if current_hash(plan['root'] / name) != sha(source[name]):
                    raise Pending('WRITE_VERIFICATION')
        journal['completed'] = True
        atomic(journal_path, json_bytes(journal), sha(initial_journal))
        if read(marker) == marker_data:
            marker.unlink()
        else:
            pending.append({'reason': 'INTENT_CHANGED_PRESERVED'})
        result['updated_roots'] = len(plans); result['reload_required'] = True
        result['status'] = 'updated_pending' if pending else 'updated'
        for row in roots:
            if row['status'] == 'update_available': row['status'] = 'updated'
        result['backup'] = str(transaction)
        return result
    except Exception as exc:
        retained = []
        # Restore only our known after bytes; never overwrite a later edit.
        for plan, name in reversed(changed):
            try:
                if current_hash(plan['root'] / name) == sha(source[name]):
                    atomic(plan['root'] / name, plan['before'][name], sha(source[name]))
                elif current_hash(plan['root'] / name) != plan['hashes'][name]:
                    retained.append(name)
            except Exception:
                retained.append(name)
        if not retained and marker_data is not None and current_hash(marker) == sha(marker_data):
            marker.unlink()
        result['status'] = 'pending'; result['pending'].append({'reason': str(exc) if isinstance(exc, Pending) else 'WRITE_FAILED', 'retained_file_count': len(retained)})
        if transaction is not None: result['backup'] = str(transaction)
        return result
    finally:
        if current_hash(lock) == sha(lock_data):
            safe(lock).unlink()
        else:
            result['pending'].append({'reason': 'LOCK_CHANGED_PRESERVED'})
            result['status'] = 'updated_pending' if result['updated_roots'] else 'pending'

def main():
    local = Path(os.environ.get('LOCALAPPDATA', ''))
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, default=local/'UOY/Companion')
    parser.add_argument('--chrome-user-data', type=Path, default=local/'Google/Chrome/User Data')
    parser.add_argument('--backup-root', type=Path, default=local/'UOY/CompanionHandoff')
    parser.add_argument('--preflight', action='store_true')
    args = parser.parse_args()
    try:
        value = run(args.source, args.chrome_user_data, args.backup_root, dry_run=args.preflight)
    except Exception as exc:
        value = {'schema': 1, 'status': 'pending', 'target_version': '1.0.27', 'updated_roots': 0, 'reload_required': False,
                 'pending': [{'reason': str(exc) if isinstance(exc, Pending) else 'HANDOFF_UNAVAILABLE'}]}
    print(json.dumps(value, ensure_ascii=True))

if __name__ == '__main__':
    main()
