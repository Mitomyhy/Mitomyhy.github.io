"""First-use setup of pinned CPU vision weights, with no upload or cloud inference."""
from pathlib import Path
from contextlib import contextmanager
import hashlib, json, os, re, secrets, shutil, stat, urllib.request

REVISION = '6ab461498e2023f6e3c1baea90a8f0fe38ab64d0'
FILES = (
    ('Qwen3.5-0.8B-Q4_K_M.gguf', 532517120, 'bd258782e35f7f458f8aced1adc053e6e92e89bc735ba3be89d38a06121dc517'),
    ('mmproj-F16.gguf', 204987232, '56e4c6cfe73b0c82e3e82bc518d7591997e61d81f723fc41a586f4fa69ea2453'),
)
BINARY_MANIFEST_SHA256 = '4bdf42d213db6a894e53052a7061171b978436d337f91a001085b79f4791356d'
CHUNK_BYTES = 1024**2
MAX_STALE_PARTS = 128
MAX_DIRECTORY_ENTRIES = 4096


def plain(path):
    if str(path).startswith(('\\\\', '//')):
        raise ValueError('The local AI runtime cannot use a network share')
    path = Path(path).absolute()
    for part in (path, *path.parents):
        if part.is_symlink() or getattr(part, 'is_junction', lambda: False)():
            raise ValueError('Linked runtime paths are not accepted')
    return path


def stub_manifest(stub):
    stub = plain(stub)
    source = plain(stub / 'runtime-template.json')
    if source.stat().st_size > 65536:
        raise ValueError('The bundled CPU runtime manifest is too large')
    manifest = json.loads(source.read_text('utf-8-sig'))
    rows = sorted(manifest['binaryFiles'], key=lambda row: row['path'])
    digest = hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    if digest != BINARY_MANIFEST_SHA256:
        raise ValueError('The bundled CPU runtime manifest differs')
    return manifest, rows


def _cancelled(marker):
    if marker.exists():
        raise InterruptedError('AI setup cancelled')


@contextmanager
def _setup_lock(destination):
    """An OS lock is released even when the parent kills the entire worker Job.

    This lets the next attempt remove abandoned owned parts without racing a
    second running setup. The small lock file is intentionally retained.
    """
    path = plain(destination / '.mitom-ai-setup.lock')
    with path.open('a+b') as stream:
        if stream.seek(0, 2) == 0:
            stream.write(b'\0')
            stream.flush()
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError('Another local AI setup is running; retry after it finishes') from exc
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _temporary(target):
    return plain(target.with_name(target.name + '.' + secrets.token_hex(8) + '.part'))


def _cleanup_temporaries(destination, targets):
    """Remove a bounded set of exact owned stale names, never recurse/follow links."""
    parents = {}
    for target, size in targets:
        target = plain(target)
        if not target.is_relative_to(destination):
            raise ValueError('Temporary cleanup escaped the local runtime directory')
        parents.setdefault(target.parent, {})[target.name] = size
    removed = 0
    for parent, names in parents.items():
        parent = plain(parent)
        if not parent.is_dir():
            continue
        for scanned, entry in enumerate(parent.iterdir()):
            if scanned >= MAX_DIRECTORY_ENTRIES or removed >= MAX_STALE_PARTS:
                break
            match = re.fullmatch(r'(.+)\.([0-9a-f]{16})\.part', entry.name)
            if not match or match[1] not in names:
                continue
            info = entry.lstat()
            if not stat.S_ISREG(info.st_mode) or entry.is_symlink() or getattr(entry, 'is_junction', lambda: False)():
                continue
            if info.st_size > names[match[1]]:
                continue  # Not a possible partial file created by this installer.
            plain(entry).unlink()
            removed += 1


def _digest(path, marker):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        while chunk := stream.read(CHUNK_BYTES):
            _cancelled(marker)
            digest.update(chunk)
    return digest.hexdigest()


def _verify_existing(target, size, checksum, marker, message):
    if not target.exists():
        return False
    target = plain(target)
    if not target.is_file() or target.stat().st_size != size or _digest(target, marker) != checksum:
        raise ValueError(message)
    return True


def _publish_new(temporary, target, marker):
    _cancelled(marker)
    plain(target)
    if target.exists():
        raise ValueError('Runtime destination appeared during setup; it was preserved')
    # This is the Windows-only deployed worker: rename atomically refuses an
    # existing destination. The setup lock also serializes our own instances.
    temporary.rename(target)


def _copy_binary(source, target, size, checksum, marker):
    temporary = _temporary(target)
    digest, written = hashlib.sha256(), 0
    try:
        with source.open('rb') as stream, temporary.open('xb') as output:
            while chunk := stream.read(CHUNK_BYTES):
                _cancelled(marker)
                written += len(chunk)
                if written > size:
                    raise ValueError('Bundled CPU runtime checksum mismatch')
                output.write(chunk)
                digest.update(chunk)
            output.flush()
            os.fsync(output.fileno())
        if written != size or digest.hexdigest() != checksum:
            raise ValueError('Bundled CPU runtime checksum mismatch')
        _publish_new(temporary, target, marker)
    finally:
        temporary.unlink(missing_ok=True)


def _write_manifest(target, manifest, marker):
    temporary = _temporary(target)
    try:
        with temporary.open('xb') as output:
            output.write((json.dumps(manifest, indent=2) + '\n').encode('utf-8'))
            output.flush()
            os.fsync(output.fileno())
        _cancelled(marker)
        plain(target)
        # Only the owned runtime manifest is replaced, after every pinned
        # binary and weight is complete. An interruption preserves the old one.
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


class HTTPSOnly(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        if not newurl.startswith('https://'):
            raise ValueError('Model download redirect must remain HTTPS')
        return super().redirect_request(request, fp, code, message, headers, newurl)


def prepare_runtime(destination, stub, cancel_marker, emit):
    destination, stub = plain(destination), plain(stub)
    cancel_marker = Path(cancel_marker)
    _cancelled(cancel_marker)
    if destination == stub or destination.is_relative_to(stub) or stub.is_relative_to(destination):
        raise ValueError('The runtime destination and bundled template must be separate directories')
    manifest, binaries = stub_manifest(stub)
    destination.mkdir(parents=True, exist_ok=True)
    # Validate the complete cleanup/copy layout before any temporary deletion.
    layout = []
    for row in binaries:
        relative = Path(row['path'])
        if relative.is_absolute() or relative.drive or '..' in relative.parts:
            raise ValueError('Invalid bundled runtime path')
        source, target = plain(stub / relative), plain(destination / relative)
        if not source.is_relative_to(stub) or not target.is_relative_to(destination):
            raise ValueError('Invalid bundled runtime path')
        layout.append((source, target, row['size'], row['sha256']))
    model_targets = [(plain(destination / 'models' / name), size, checksum) for name, size, checksum in FILES]
    manifest_target = plain(destination / 'runtime-template.json')
    with _setup_lock(destination):
        _cancelled(cancel_marker)
        _cleanup_temporaries(destination, [(target, size) for _, target, size, _ in layout]
                             + [(target, size) for target, size, _ in model_targets] + [(manifest_target, 65536)])
        for source, target, size, checksum in layout:
            _cancelled(cancel_marker)
            target.parent.mkdir(parents=True, exist_ok=True)
            if _verify_existing(target, size, checksum, cancel_marker, 'Existing CPU runtime was modified; it was preserved'):
                if source.stat().st_size != size or _digest(source, cancel_marker) != checksum:
                    raise ValueError('Bundled CPU runtime checksum mismatch')
            else:
                _copy_binary(source, target, size, checksum, cancel_marker)
        total, completed = sum(row[1] for row in FILES), 0
        opener = urllib.request.build_opener(HTTPSOnly())
        for target, size, checksum in model_targets:
            _cancelled(cancel_marker)
            target.parent.mkdir(parents=True, exist_ok=True)
            if _verify_existing(target, size, checksum, cancel_marker, 'An existing model file differs; it was preserved'):
                completed += size
                continue
            if shutil.disk_usage(target.parent).free < size + 128 * 1024**2:
                raise OSError('Not enough free disk space for the local AI model')
            temporary = _temporary(target)
            url = f'https://huggingface.co/unsloth/Qwen3.5-0.8B-GGUF/resolve/{REVISION}/{target.name}'
            digest, received = hashlib.sha256(), 0
            try:
                with opener.open(urllib.request.Request(url, headers={'User-Agent': 'MITOM-Product-Video/1'}), timeout=60) as response, temporary.open('xb') as output:
                    if response.status != 200:
                        raise OSError('Local AI model download failed')
                    while chunk := response.read(CHUNK_BYTES):
                        _cancelled(cancel_marker)
                        received += len(chunk)
                        if received > size:
                            raise ValueError('Model download exceeds its pinned size')
                        output.write(chunk)
                        digest.update(chunk)
                        emit(phase='model_setup', progress=(completed + received) / total,
                             downloaded_bytes=completed + received, total_bytes=total)
                    output.flush()
                    os.fsync(output.fileno())
                if received != size or digest.hexdigest() != checksum:
                    raise ValueError('Local AI model download checksum mismatch')
                _publish_new(temporary, target, cancel_marker)
                completed += size
            finally:
                temporary.unlink(missing_ok=True)
        manifest.update(model='models/Qwen3.5-0.8B-Q4_K_M.gguf', modelFile='models/Qwen3.5-0.8B-Q4_K_M.gguf', modelSha256=FILES[0][2], modelSize=FILES[0][1],
                        mmproj='models/mmproj-F16.gguf', mmprojFile='models/mmproj-F16.gguf', mmprojSha256=FILES[1][2], mmprojSize=FILES[1][1], modelRevision=REVISION)
        _write_manifest(manifest_target, manifest, cancel_marker)
    return destination
