"""One small-artifact transfer, separating transport truncation from bad pins."""
from __future__ import annotations

import hashlib
import hmac
import http.client
import os
from pathlib import Path
import re
import tempfile


def download_once(backend, artifact, destination_dir, filename, progress=None):
    """Publish only complete, flushed bytes matching the authenticated artifact.

    ``HTTPResponse.read(amount)`` can return EOF before Content-Length without
    raising IncompleteRead. Detect that case explicitly so the existing bounded
    network retry policy applies. A full-sized hash mismatch remains a hard
    integrity failure and is not converted into a retryable transport error.
    """
    import installer
    backend.validate_artifact(artifact)
    if (Path(filename).name != filename or filename in {".", ".."}
            or not re.fullmatch(r"[A-Za-z0-9._-]+", filename)):
        raise backend.LauncherError("Tên file tải không hợp lệ.")
    directory = installer._plain(Path(destination_dir))
    directory.mkdir(parents=True, exist_ok=True)
    destination = installer._plain(directory / filename)
    if backend.verified_file(destination, artifact):
        if progress:
            progress(artifact["size"], artifact["size"])
        return destination
    descriptor, name = tempfile.mkstemp(prefix="uoy-download-", suffix=".part", dir=directory)
    temporary = Path(name)
    total, digest = 0, hashlib.sha256()
    try:
        with os.fdopen(descriptor, "wb") as output:
            with backend.open_https(artifact["url"]) as response:
                length = response.headers.get("Content-Length")
                if length is not None and (not re.fullmatch(r"[0-9]+", str(length))
                                            or int(length) != artifact["size"]):
                    raise backend.LauncherError("Kích thước tải về không đúng bản phát hành.")
                while chunk := response.read(1024 * 1024):
                    total += len(chunk)
                    if total > artifact["size"] or total > backend.DOWNLOAD_LIMIT:
                        raise backend.LauncherError("Bộ cài vượt kích thước đã xác thực.")
                    digest.update(chunk)
                    output.write(chunk)
                    if progress:
                        progress(total, artifact["size"])
                if total != artifact["size"]:
                    incomplete = http.client.IncompleteRead(b"", artifact["size"] - total)
                    incomplete.add_note(f"Received {total} of {artifact['size']} authenticated bytes before HTTP EOF.")
                    raise incomplete
                if not hmac.compare_digest(digest.hexdigest(), artifact["sha256"]):
                    raise backend.LauncherError("File tải đủ dung lượng nhưng sai SHA-256. Không chạy bộ cài.")
            output.flush()
            os.fsync(output.fileno())
        # Re-read the actual file before promotion, rather than relying only on
        # the digest computed from bytes received from the network.
        if not backend.verified_file(temporary, artifact):
            raise backend.LauncherError("File lưu trên ổ đĩa sai SHA-256. Không chạy bộ cài.")
        installer._plain(temporary)
        installer._plain(destination)
        os.replace(temporary, destination)
        return destination
    finally:
        temporary.unlink(missing_ok=True)
