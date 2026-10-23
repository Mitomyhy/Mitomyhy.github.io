"""Allowlisted model delivery and a Python-stdlib-only safe multipart installer.

Builds a new isolated directory. It never changes the active portable app or the
source model. Install joins parts virtually, verifies all hashes, then extracts
into app/models staging and atomically swaps a complete model, preserving backup.
"""
from __future__ import annotations
import argparse
import bisect
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile
import time
import uuid
import zipfile

MODEL_SUBDIR = "models/ltx-video-cpu"
MAX_PART = 1_800_000_000  # Under1.8GiB and GitHub's2GiB release-asset limit.
CHUNK = 8 * 1024 * 1024
COMPONENTS = ("text_encoder", "transformer", "vae_encoder", "vae_decoder")
REQUIRED = {"model_index.json", "openvino_config.json", "mitom-model.json", "scheduler/scheduler_config.json", "tokenizer/tokenizer_config.json", "tokenizer/tokenizer.json"}
REQUIRED |= {f"{name}/{file}" for name in COMPONENTS for file in ("config.json", "openvino_model.xml", "openvino_model.bin")}
OPTIONAL = {"tokenizer/spiece.model", "tokenizer/added_tokens.json", "tokenizer/special_tokens_map.json"}
LICENSE_FILES = {"licenses/LTX-Video-2B-0.9.6-distilled-LICENSE.txt", "licenses/T5-Apache-2.0.txt", "MODEL-NOTICES.txt"}
ALLOWLIST = REQUIRED | OPTIONAL | LICENSE_FILES
PRIVATE_PATH = re.compile(r"(?<![A-Za-z0-9])(?:[A-Za-z]:[\\/]|\\\\[^\\]+\\|/(?:Users|home|mnt|workspace)/)")
METADATA_PATH_KEYS = {"_name_or_path", "name_or_path", "model_save_dir", "cache_dir", "statistics_path", "_commit_hash"}
PINNED_LTX_LICENSE_SHA = "7cbd654fa79a98f0d2588fb5ac1d7ff459010b085c2bd6c572f800ac026d7bfe"


def hash_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while data := handle.read(CHUNK):
            digest.update(data)
    return digest.hexdigest()


def write_json(path, data):
    Path(path).write_text(json.dumps(data, sort_keys=True, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def safe_relative(name):
    if not isinstance(name, str) or not name or "\\" in name or ":" in name or "\0" in name:
        raise ValueError("Invalid relative archive name")
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in ("", ".", "..") for part in name.split("/")):
        raise ValueError("Unsafe relative archive name")
    if any(part.rstrip(" .") != part for part in path.parts):
        raise ValueError("Ambiguous Windows archive name")
    return path


def is_linklike(path):
    return path.is_symlink() or bool(getattr(path, "is_junction", lambda: False)())


def local_file(root, relative):
    safe_relative(relative)
    result = root / relative
    for part in [result, *result.parents]:
        if part == root.parent:
            break
        if is_linklike(part):
            raise ValueError(f"Symlink/junction not permitted: {relative}")
    if not result.resolve().is_relative_to(root.resolve()) or not result.is_file():
        raise ValueError(f"Missing/unsafe file: {relative}")
    return result


def sanitize_json(value):
    if isinstance(value, dict):
        return {key: sanitize_json(item) for key, item in value.items() if key not in METADATA_PATH_KEYS}
    if isinstance(value, list):
        return [sanitize_json(item) for item in value]
    if isinstance(value, str) and PRIVATE_PATH.search(value):
        raise ValueError("Unexpected private absolute path in runtime configuration")
    return value


class SplitArchiveWriter(io.RawIOBase):
    def __init__(self, directory, name, part_size):
        self.directory, self.name, self.part_size = directory, name, part_size
        self.total = self.current_size = 0
        self.whole = hashlib.sha256()
        self.part_hash = hashlib.sha256()
        self.handle = None
        self.parts = []

    def writable(self):
        return True

    def seekable(self):
        return False

    def tell(self):
        return self.total

    def _open_part(self):
        self.part_name = f"{self.name}.zip.part{len(self.parts) + 1:03d}"
        self.handle = (self.directory / self.part_name).open("xb")
        self.current_size = 0
        self.part_hash = hashlib.sha256()

    def _close_part(self):
        if self.handle:
            self.handle.flush()
            os.fsync(self.handle.fileno())
            self.handle.close()
            self.parts.append({"name": self.part_name, "size": self.current_size, "sha256": self.part_hash.hexdigest()})
            self.handle = None

    def write(self, data):
        view = memoryview(data)
        length = len(view)
        position = 0
        while position < length:
            if self.handle is None:
                self._open_part()
            count = min(length - position, self.part_size - self.current_size)
            piece = view[position:position + count]
            self.handle.write(piece)
            self.whole.update(piece)
            self.part_hash.update(piece)
            self.current_size += count
            self.total += count
            position += count
            if self.current_size == self.part_size:
                self._close_part()
        return length

    def finish(self):
        self._close_part()
        return {"format": "zip-stored-multipart", "size": self.total, "sha256": self.whole.hexdigest(), "parts": self.parts}


class JoinedParts(io.RawIOBase):
    """Seekable virtual concatenation: no full-size temporary join file."""
    def __init__(self, paths):
        self.handles = [path.open("rb") for path in paths]
        self.offsets = [0]
        for path in paths:
            self.offsets.append(self.offsets[-1] + path.stat().st_size)
        self.position = 0

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self.position

    def seek(self, offset, whence=io.SEEK_SET):
        position = offset if whence == io.SEEK_SET else self.position + offset if whence == io.SEEK_CUR else self.offsets[-1] + offset
        if not 0 <= position <= self.offsets[-1]:
            raise ValueError("Seek outside multipart archive")
        self.position = position
        return position

    def read(self, size=-1):
        size = self.offsets[-1] - self.position if size < 0 else min(size, self.offsets[-1] - self.position)
        if size > 64 * 1024 * 1024:
            raise ValueError("Unexpected oversized archive read")
        chunks = []
        while size:
            index = bisect.bisect_right(self.offsets, self.position) - 1
            count = min(size, self.offsets[index + 1] - self.position)
            handle = self.handles[index]
            handle.seek(self.position - self.offsets[index])
            piece = handle.read(count)
            if len(piece) != count:
                raise ValueError("Truncated multipart archive")
            chunks.append(piece)
            self.position += count
            size -= count
        return b"".join(chunks)

    def close(self):
        for handle in self.handles:
            handle.close()
        super().close()


def build(args):
    started = time.monotonic()
    model, source, output = args.model_dir.resolve(), args.source_dir.resolve(), args.output_dir.resolve()
    if output.exists() or output.is_relative_to(model) or output.is_relative_to(source):
        raise ValueError("Use a new output directory separate from model/checkpoint sources")
    for name in REQUIRED:
        local_file(model, name)
    profile = json.loads((model / "mitom-model.json").read_text(encoding="utf-8"))
    if profile.get("profile") != "ltxv-2b-0.9.6-distilled" or profile.get("device") != "CPU" or profile.get("engine") != "optimum-openvino":
        raise ValueError("Unexpected runtime model profile")
    source_marker = json.loads((model / "mitom-cpu-export-stage.json").read_text(encoding="utf-8"))
    if source_marker.get("complete") is not True or source_marker.get("role") != "quantized":
        raise ValueError("Source model has no complete quantized export marker")
    output.mkdir(parents=True)
    portable = output / "portable" / MODEL_SUBDIR
    portable.mkdir(parents=True)
    stats = {"hardlinked_bytes": 0, "copied_bytes": 0, "hardlinked_files": [], "sanitized_json_files": []}
    for name in sorted(REQUIRED | {name for name in OPTIONAL if (model / name).is_file()}):
        origin, target = local_file(model, name), portable / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if origin.suffix.lower() in (".bin", ".xml"):
            expected = source_marker["files"].get(name)
            if not expected or origin.stat().st_size != expected["size"] or hash_file(origin) != expected["sha256"]:
                raise ValueError(f"Immutable IR differs from completed source marker: {name}")
            if origin.suffix.lower() == ".xml" and PRIVATE_PATH.search(origin.read_text(encoding="utf-8")):
                raise ValueError(f"Unexpected private path in immutable XML: {name}")
            try:
                os.link(origin, target)
                stats["hardlinked_bytes"] += target.stat().st_size
                stats["hardlinked_files"].append(name)
            except OSError:
                shutil.copyfile(origin, target)
                stats["copied_bytes"] += target.stat().st_size
        elif origin.suffix.lower() == ".json":
            value = json.loads(origin.read_text(encoding="utf-8"))
            # Vocabulary entries are data, not metadata paths; preserve exactly.
            if name == "tokenizer/tokenizer.json":
                shutil.copyfile(origin, target)
            else:
                write_json(target, sanitize_json(value))
                stats["sanitized_json_files"].append(name)
            stats["copied_bytes"] += target.stat().st_size
        else:
            shutil.copyfile(origin, target)
            stats["copied_bytes"] += target.stat().st_size
    ltx_license = local_file(source, "licenses/LTX-Video-2B-0.9.6-distilled-LICENSE.txt")
    if hash_file(ltx_license) != PINNED_LTX_LICENSE_SHA:
        raise ValueError("Pinned applicable distilled LTX license hash mismatch")
    (portable / "licenses").mkdir()
    shutil.copyfile(ltx_license, portable / "licenses/LTX-Video-2B-0.9.6-distilled-LICENSE.txt")
    apache = args.apache_license.resolve()
    if "Apache License" not in apache.read_text(encoding="utf-8") or "Version 2.0" not in apache.read_text(encoding="utf-8"):
        raise ValueError("Expected the unmodified Apache2.0 license text")
    shutil.copyfile(apache, portable / "licenses/T5-Apache-2.0.txt")
    notices = """MITOM CPU video model - component notices\n\nLTX-Video-2B-0.9.6-Distilled-04-25 by Lightricks Ltd.\nPinned source: https://huggingface.co/Lightricks/LTX-Video/tree/8984fa25007f376c1a299016d0957a37a2f797bb\nApplicable license: licenses/LTX-Video-2B-0.9.6-distilled-LICENSE.txt\nLicense source: https://huggingface.co/Lightricks/LTX-Video-2B-0.9.6-Distilled-04-25/blob/416c86a16b0186b4fde93fbf57070fe0b7139716/LICENSE\nThis model and derivatives remain subject to the complete included license, including its use restrictions and commercial-entity terms.\n\nT5XXL encoder from comfyanonymous/flux_text_encoders\nPinned source: https://huggingface.co/comfyanonymous/flux_text_encoders/tree/6af2a98e3f615bdfa612fbd85da93d1ed5f69ef5\nThe pinned maintainer model card identifies Apache2.0. Included text: licenses/T5-Apache-2.0.txt\nT5 model architecture by Google Research.\n\nModification notice: MITOM converted the original checkpoint components to OpenVINO IR using FP32 tracing/FP16 weight storage, compressed the T5 encoder to asymmetric INT4(group128,ratio1), and the transformer to asymmetric INT8. VAE weights retain FP16 storage and the original timestep-conditioned behavior. No training or model finetuning was performed.\n\nThis package contains runtime model assets only. It includes no reference images, user prompts, generated videos, credentials, machine paths or diagnostic logs. Model outputs require visual review; package presence is not proof of exact product text/design preservation or universal CPU/RAM compatibility.\n"""
    (portable / "MODEL-NOTICES.txt").write_text(notices, encoding="utf-8")
    files = {}
    for path in sorted(portable.rglob("*")):
        if not path.is_file():
            continue
        name = path.relative_to(portable).as_posix()
        if name not in ALLOWLIST or is_linklike(path):
            raise ValueError(f"Unexpected file in runtime allowlist: {name}")
        if path.suffix.lower() in (".json", ".xml", ".txt") and name != "tokenizer/tokenizer.json" and PRIVATE_PATH.search(path.read_text(encoding="utf-8")):
            raise ValueError(f"Private absolute path leaked into public model file: {name}")
        files[name] = {"size": path.stat().st_size, "sha256": hash_file(path)}
    writer = SplitArchiveWriter(output, args.name, args.part_size)
    with zipfile.ZipFile(writer, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
        for name, row in sorted(files.items()):
            info = zipfile.ZipInfo(f"{MODEL_SUBDIR}/{name}", date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            info.compress_type = zipfile.ZIP_STORED
            info.file_size = row["size"]
            digest = hashlib.sha256()
            with (portable / name).open("rb") as source_handle, archive.open(info, "w", force_zip64=row["size"] >= 2 ** 31) as destination:
                while data := source_handle.read(CHUNK):
                    digest.update(data)
                    destination.write(data)
            if digest.hexdigest() != row["sha256"]:
                raise ValueError(f"Immutable file changed while archiving: {name}")
    archive_info = writer.finish()
    manifest = {"schema_version": 1, "bundle_name": args.name, "profile": profile["profile"], "model_subdir": MODEL_SUBDIR, "source_revision": profile["source_revision"], "archive": archive_info, "files": files, "total_uncompressed_bytes": sum(row["size"] for row in files.values()), "license_notice": "Read MODEL-NOTICES.txt and the included component licenses before use or redistribution."}
    write_json(output / "model-bundle.json", manifest)
    shutil.copyfile(Path(__file__).resolve(), output / "install_model.py")
    instructions = """MITOM CPU model offline installation\n\nKeep model-bundle.json and every .zip.partNNN file together. Use Python3.12 or the portable app's included Python:\n\n  python install_model.py install --app-dir PATH_TO_MITOM_APP --manifest model-bundle.json\n\nThe installer verifies each part, the complete virtual archive, and every extracted file. It does not require network access or7GB of temporary disk space for joining the parts. It does require enough space to stage the complete extracted model inside the app's models directory. A previous model is preserved as a backup before the atomic directory switch. Stop active video generation before replacing a model.\n\nThe portable/models/ltx-video-cpu directory is also a ready-to-copy unpacked model directory. Its immutable XML/BIN files may be hardlinked to the development source on this build machine; do not modify them. All redistribution assets are independent archive bytes. No reference media or diagnostics are included.\n"""
    (output / "INSTALL.txt").write_text(instructions, encoding="utf-8")
    stats.update({"schema_version": 1, "elapsed_seconds": time.monotonic() - started, "public_files": len(files), "total_uncompressed_bytes": manifest["total_uncompressed_bytes"], "archive_bytes": archive_info["size"], "parts": archive_info["parts"], "archive_sha256": archive_info["sha256"], "ready_model_relative": f"portable/{MODEL_SUBDIR}", "published": False})
    write_json(output / "build-summary.json", stats)
    print(json.dumps(stats, indent=2))


def load_manifest(path):
    if path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("Oversized bundle manifest")
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schema_version") != 1 or value.get("model_subdir") != MODEL_SUBDIR or value.get("profile") != "ltxv-2b-0.9.6-distilled":
        raise ValueError("Unexpected bundle schema/model target")
    files = value["files"]
    if not isinstance(files, dict) or not REQUIRED.issubset(files) or not set(files).issubset(ALLOWLIST):
        raise ValueError("Bundle files differ from the reviewed runtime allowlist")
    for name, row in files.items():
        safe_relative(name)
        if not isinstance(row["size"], int) or not 0 < row["size"] <= 4 * 1024 ** 3 or not re.fullmatch("[a-f0-9]{64}", row["sha256"]):
            raise ValueError("Invalid file size/hash")
    if sum(row["size"] for row in files.values()) != value["total_uncompressed_bytes"] or value["total_uncompressed_bytes"] > 10 * 1024 ** 3:
        raise ValueError("Unexpected model total size")
    if value["archive"].get("format") != "zip-stored-multipart" or not value["total_uncompressed_bytes"] < value["archive"]["size"] <= value["total_uncompressed_bytes"] + 1024 * 1024:
        raise ValueError("Unexpected stored archive size/format")
    parts = value["archive"]["parts"]
    if not isinstance(parts, list) or not 1 <= len(parts) <= 16:
        raise ValueError("Invalid archive part count")
    names = set()
    for row in parts:
        if len(safe_relative(row["name"]).parts) != 1 or row["name"] in names or not 0 < row["size"] <= MAX_PART or not re.fullmatch("[a-f0-9]{64}", row["sha256"]):
            raise ValueError("Invalid multipart archive metadata")
        names.add(row["name"])
    if sum(row["size"] for row in parts) != value["archive"]["size"] or not re.fullmatch("[a-f0-9]{64}", value["archive"]["sha256"]):
        raise ValueError("Invalid complete archive metadata")
    return value


def verified_parts(manifest, directory):
    whole = hashlib.sha256()
    paths = []
    for row in manifest["archive"]["parts"]:
        path = local_file(directory, row["name"])
        if path.stat().st_size != row["size"]:
            raise ValueError(f"Archive part size mismatch: {row['name']}")
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while data := handle.read(CHUNK):
                digest.update(data)
                whole.update(data)
        if digest.hexdigest() != row["sha256"]:
            raise ValueError(f"Archive part SHA-256 mismatch: {row['name']}")
        paths.append(path)
    if whole.hexdigest() != manifest["archive"]["sha256"]:
        raise ValueError("Whole archive SHA-256 mismatch")
    return paths


def extract_verified(manifest, paths, stage):
    with JoinedParts(paths) as joined, zipfile.ZipFile(joined, "r") as archive:
        entries = archive.infolist()
        expected = {f"{MODEL_SUBDIR}/{name}": row for name, row in manifest["files"].items()}
        if len(entries) != len(expected) or {entry.filename for entry in entries} != set(expected):
            raise ValueError("Archive contains missing, duplicate or unlisted entries")
        for entry in entries:
            safe_relative(entry.filename)
            mode = entry.external_attr >> 16
            if entry.is_dir() or stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in (0, stat.S_IFREG)) or entry.flag_bits & 1 or entry.compress_type != zipfile.ZIP_STORED:
                raise ValueError("Archive contains a link, special, encrypted or unsupported entry")
            row = expected[entry.filename]
            if entry.file_size != row["size"]:
                raise ValueError("Archive uncompressed file size differs from manifest")
            relative = entry.filename[len(MODEL_SUBDIR) + 1:]
            target = stage / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.resolve().is_relative_to(stage.resolve()) or any(is_linklike(parent) for parent in [target, *target.parents] if parent != stage.parent and parent.is_relative_to(stage)):
                raise ValueError("Extraction path leaves staging or follows a link")
            digest, written = hashlib.sha256(), 0
            with archive.open(entry, "r") as source, target.open("xb") as destination:
                while data := source.read(CHUNK):
                    written += len(data)
                    if written > row["size"]:
                        raise ValueError("Archive file exceeds declared size")
                    digest.update(data)
                    destination.write(data)
            if written != row["size"] or digest.hexdigest() != row["sha256"]:
                raise ValueError(f"Extracted file SHA-256 mismatch: {relative}")


def install(args):
    started = time.monotonic()
    manifest_path = args.manifest.resolve()
    manifest = load_manifest(manifest_path)
    parts_dir = (args.parts_dir or manifest_path.parent).resolve()
    paths = verified_parts(manifest, parts_dir)
    app = args.app_dir.resolve()
    if not app.is_dir():
        raise ValueError("Application directory does not exist")
    models = app / "models"
    if is_linklike(models) or not models.resolve().is_relative_to(app):
        raise ValueError("Application models directory leaves app or is a link")
    models.mkdir(exist_ok=True)
    target = app / MODEL_SUBDIR
    if is_linklike(target) or not target.resolve().is_relative_to(models.resolve()):
        raise ValueError("Model target leaves application models directory")
    stage = Path(tempfile.mkdtemp(prefix=".ltx-install-", dir=models))
    backup = None
    try:
        extract_verified(manifest, paths, stage)
        if target.exists():
            backup = models / (".ltx-backup-" + uuid.uuid4().hex)
            target.rename(backup)
        try:
            stage.rename(target)
        except BaseException:
            if backup and backup.exists() and not target.exists():
                backup.rename(target)
            raise
    except BaseException:
        # Preserve failed staging for inspection; no recursive deletion or loss of
        # a previously installed model is part of this installer.
        raise
    print(json.dumps({"status": "installed", "model_subdir": MODEL_SUBDIR, "backup_subdir": backup.relative_to(app).as_posix() if backup else None, "verified_files": len(manifest["files"]), "elapsed_seconds": time.monotonic() - started, "network_used": False}, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    package = commands.add_parser("build")
    package.add_argument("--model-dir", type=Path, required=True)
    package.add_argument("--source-dir", type=Path, required=True)
    package.add_argument("--output-dir", type=Path, required=True)
    package.add_argument("--apache-license", type=Path, required=True)
    package.add_argument("--name", default="MITOM-Video-CPU-Model-2026.10.23.1")
    package.add_argument("--part-size", type=int, default=MAX_PART)
    receiver = commands.add_parser("install")
    receiver.add_argument("--app-dir", type=Path, required=True)
    receiver.add_argument("--manifest", type=Path, required=True)
    receiver.add_argument("--parts-dir", type=Path)
    args = parser.parse_args()
    if args.command == "build":
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,100}", args.name) or not 1 <= args.part_size <= MAX_PART:
            parser.error("Unsafe bundle name or invalid part size")
        build(args)
    else:
        install(args)


if __name__ == "__main__":
    main()
