"""Hash-locked, flat-root portable package for the MITOM launcher."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import stat
import zipfile

VERSION = "1.0.0"
APP_ID = "vector-migic"
ENTRY = "VectorMigic.exe"
ARCHIVE = f"VectorMigic-{VERSION}.zip"
MANIFEST = "vector-app.json"
MAX_FILES = 1024
MAX_TOTAL_BYTES = 300_000_000


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_member(name: str) -> bool:
    value = PurePosixPath(name)
    return bool(name) and "\\" not in name and ":" not in name and not value.is_absolute() and all(part not in {"", ".", ".."} for part in value.parts) and str(value) == name


def json_bytes(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def create_package(app_dir: Path, output: Path, workspace: Path, smoke_report: Path) -> dict:
    smoke = json.loads(smoke_report.read_text(encoding="utf-8-sig"))
    if smoke.get("ok") is not True:
        raise ValueError("Packaged runtime must pass its self-test before publication.")
    source_files = sorted((file for file in app_dir.rglob("*") if file.is_file() and file.name != MANIFEST), key=lambda file: file.relative_to(app_dir).as_posix())
    if not (app_dir / ENTRY).is_file():
        raise ValueError("Portable executable is missing.")
    rows, seen = [], set()
    for file in source_files:
        path = file.relative_to(app_dir).as_posix()
        if file.is_symlink() or not safe_member(path) or path.casefold() in seen:
            raise ValueError(f"Unsafe or duplicate package path: {path}")
        if any(part.lower() in {"__pycache__", ".git", ".work", "logs", "cache"} for part in PurePosixPath(path).parts):
            raise ValueError(f"Mutable or private build directory in runtime package: {path}")
        seen.add(path.casefold())
        data = file.read_bytes()
        rows.append({"path": path, "size": len(data), "sha256": sha256(data)})
    if len(rows) > MAX_FILES or sum(row["size"] for row in rows) > MAX_TOTAL_BYTES:
        raise ValueError("Portable runtime exceeds package size limits.")
    manifest = {"schema": 1, "app_id": APP_ID, "version": VERSION, "entry": ENTRY, "files": rows}
    manifest_bytes = json_bytes(manifest)
    (app_dir / MANIFEST).write_bytes(manifest_bytes)
    output.mkdir(parents=True, exist_ok=True)
    archive_path = output / ARCHIVE
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for file in [app_dir / MANIFEST] + source_files:
            member = file.relative_to(app_dir).as_posix()
            info = zipfile.ZipInfo(member, (2026, 10, 24, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, file.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    validate_archive(archive_path, manifest_bytes)
    archive_data = archive_path.read_bytes()
    archive_hash = sha256(archive_data)
    index = {"schema": 1, "app_id": APP_ID, "version": VERSION, "entry": ENTRY,
             "manifest_sha256": sha256(manifest_bytes),
             "archive": {"url": f"https://mitomyhy.github.io/releases/vector-migic/{VERSION}/{ARCHIVE}",
                         "size": len(archive_data), "sha256": archive_hash,
                         "bundled": f"apps/{ARCHIVE}"}}
    (output / "vector-release.json").write_bytes(json_bytes(index))
    (output / (ARCHIVE + ".sha256")).write_text(archive_hash + "  " + ARCHIVE + "\n", encoding="ascii")
    (output / MANIFEST).write_bytes(manifest_bytes)
    (output / "SHA256SUMS.txt").write_text(archive_hash + "  " + ARCHIVE + "\n", encoding="ascii")
    launcher = workspace / "launcher"
    (launcher / "apps").mkdir(exist_ok=True)
    shutil.copyfile(archive_path, launcher / "apps" / ARCHIVE)
    (launcher / "vector-release.json").write_bytes(json_bytes(index))
    report = {"ok": bool(smoke.get("ok")), "version": VERSION,
              "archive": ARCHIVE, "size": len(archive_data), "sha256": archive_hash,
              "manifest_sha256": sha256(manifest_bytes), "runtime_files": len(rows),
              "uncompressed_bytes": sum(row["size"] for row in rows),
              "checks": {"all_entries_hash_verified": True, "safe_flat_root": True,
                         "entry_exists": True, "packaged_self_test": bool(smoke.get("ok")),
                         "no_build_caches_or_private_inputs": True}}
    (output / "package-verification.json").write_bytes(json_bytes(report))
    print(json.dumps(report, ensure_ascii=False))
    return report


def validate_archive(archive_path: Path, expected_manifest: bytes | None = None) -> dict:
    with zipfile.ZipFile(archive_path, "r") as archive:
        infos = archive.infolist()
        names = [info.filename for info in infos]
        if len(infos) > MAX_FILES + 1 or len({name.casefold() for name in names}) != len(names):
            raise ValueError("Duplicate or excessive ZIP records.")
        if any(info.is_dir() or not safe_member(info.filename) or stat.S_ISLNK(info.external_attr >> 16) for info in infos):
            raise ValueError("Unsafe ZIP record.")
        raw_manifest = archive.read(MANIFEST)
        if expected_manifest is not None and raw_manifest != expected_manifest:
            raise ValueError("Archive manifest differs from the prepared bytes.")
        manifest = json.loads(raw_manifest)
        if (manifest.get("schema"), manifest.get("app_id"), manifest.get("version"), manifest.get("entry")) != (1, APP_ID, VERSION, ENTRY):
            raise ValueError("Unexpected manifest identity.")
        expected_paths = {row["path"] for row in manifest["files"]}
        if expected_paths | {MANIFEST} != set(names) or ENTRY not in expected_paths:
            raise ValueError("ZIP membership does not match the complete manifest.")
        if sum(row["size"] for row in manifest["files"]) > MAX_TOTAL_BYTES:
            raise ValueError("Uncompressed archive exceeds the size limit.")
        for row in manifest["files"]:
            data = archive.read(row["path"])
            if len(data) != row["size"] or sha256(data) != row["sha256"]:
                raise ValueError(f"Content hash mismatch: {row['path']}")
        if archive.testzip() is not None:
            raise ValueError("ZIP checksum verification failed.")
        return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--app-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--smoke-report", required=True, type=Path)
    args = parser.parse_args()
    create_package(args.app_dir.resolve(), args.output.resolve(), args.workspace.resolve(), args.smoke_report.resolve())
