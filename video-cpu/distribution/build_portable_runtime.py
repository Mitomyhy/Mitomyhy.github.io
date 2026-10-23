"""Plan/build an offline Windows CPU worker from explicitly approved packages.

No downloads, installs, model export, system settings or service registration.
Run with the reviewed export environment's Python. --dry-run only writes JSON.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
from importlib import metadata
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
from urllib.parse import urlsplit
import zipfile

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT_REQUIREMENTS = ["optimum-intel[openvino,diffusers]", "openvino-tokenizers", "numpy", "Pillow", "psutil", "accelerate", "sentencepiece", "protobuf"]
WORKER_FILES = ["worker_server.py", "inference.py", "model_validation.py", "process_limits.py", "sigma_schedule.py", "low_memory_pipeline.py", "mp4_export.py"]
WEB_FILES = ["index.html", "app.js", "style.css", "client.mjs", "assets/mitom-logo.png", "assets/favicon.ico", "assets/svn-helvetica-neue-regular.ttf", "assets/akzidenz-grotesk-bold.ttf"]
OMIT_NAMES = {"direct_url.json", ".env", ".netrc", ".pypirc", ".npmrc", "pytorch_model.bin", "diffusion_pytorch_model.bin"}
OMIT_SUFFIXES = {".pth", ".pyc", ".pyo", ".safetensors", ".ckpt", ".onnx", ".pt", ".gguf", ".ggml", ".h5"}
# Reviewed release DLLs already present in the selected scikit-learn 1.9.1 wheel.
# Do not search System32, PATH, unrelated environments, or arbitrary DLL names.
# OpenVINO/Torch can load before sklearn registers its private .libs directory.
APP_LOCAL_VC_DLLS = {
    "msvcp140.dll": {"source_package_path": "python/Lib/site-packages/sklearn/.libs/msvcp140.dll",
                     "sha256": "7c26614e1d733892c2deac7e245ce115504b1d80592dd0a01b08e3e5a55f89ca"},
    "vcomp140.dll": {"source_package_path": "python/Lib/site-packages/sklearn/.libs/vcomp140.dll",
                     "sha256": "95d4ce4a6802d1e18b5e0e1722cc30ea72ca7e033f83828f05c0b7b993fe7cbf"},
}


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while block := stream.read(8 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf8")


def installed_index(roots):
    index = {}
    for root in roots:
        if not root.is_dir():
            raise ValueError(f"Approved site-packages directory is missing: {root}")
        if root.name.lower() != "site-packages":
            raise ValueError(f"Only explicit site-packages roots are accepted: {root}")
        for dist in metadata.distributions(path=[str(root)]):
            name = dist.metadata.get("Name")
            if name:
                index.setdefault(canonicalize_name(name), (dist, root))
    return index


def resolve_closure(index, python_version):
    environment = default_environment()
    environment.update(os_name="nt", sys_platform="win32", platform_system="Windows", platform_machine="AMD64", python_version="3.12", python_full_version=python_version)
    pending = [Requirement(value) for value in ROOT_REQUIREMENTS]
    selected, extras, processed = {}, {}, {}
    edges = []
    while pending:
        requirement = pending.pop(0)
        name = canonicalize_name(requirement.name)
        if requirement.url:
            raise ValueError(f"Direct dependency URLs require a separately reviewed package: {name}")
        if name not in index:
            raise ValueError(f"Required approved installed distribution is missing: {requirement}")
        dist, root = index[name]
        if requirement.specifier and not requirement.specifier.contains(dist.version, prereleases=True):
            raise ValueError(f"Installed {name}=={dist.version} does not satisfy {requirement}")
        selected[name] = (dist, root)
        extras.setdefault(name, set()).update(requirement.extras)
        active = frozenset(extras[name])
        if processed.get(name) == active:
            continue
        processed[name] = active
        for raw in dist.requires or ():
            dependency = Requirement(raw)
            if dependency.marker and not any(dependency.marker.evaluate({**environment, "extra": extra}) for extra in ({""} | set(active))):
                continue
            pending.append(dependency)
            edges.append({"from": name, "to": canonicalize_name(dependency.name), "requirement": str(dependency)})
    return selected, edges, environment


def record_sha256(file):
    if file.hash and file.hash.mode == "sha256":
        value = file.hash.value
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)).hex()
    return None


def dependency_plan(selected):
    files, packages, omitted, destinations = [], [], [], {}
    for name, (dist, root) in sorted(selected.items()):
        if dist.files is None:
            raise ValueError(f"Installed distribution has no RECORD file inventory: {name}")
        license_paths = []
        for entry in dist.files:
            rel = PurePosixPath(str(entry).replace("\\", "/"))
            if rel.is_absolute() or ".." in rel.parts:
                omitted.append({"distribution": name, "record_path": str(rel), "reason": "outside_site_packages"})
                continue
            source = Path(dist.locate_file(entry)).resolve()
            if not source.is_relative_to(root):
                omitted.append({"distribution": name, "record_path": str(rel), "reason": "resolved_outside_site_packages"})
                continue
            if entry.name in OMIT_NAMES or entry.suffix.lower() in OMIT_SUFFIXES or "__pycache__" in rel.parts:
                omitted.append({"distribution": name, "record_path": str(rel), "reason": "excluded_metadata_cache_pth_or_weights"})
                continue
            if not source.is_file():
                raise ValueError(f"RECORD references a missing package file: {source}")
            destination = "python/Lib/site-packages/" + source.relative_to(root).as_posix()
            destination_key = destination.casefold()
            prior = destinations.get(destination_key)
            expected = record_sha256(entry)
            if prior:
                if prior["source"].casefold() != str(source).casefold():
                    raise ValueError(f"Two approved distributions collide at {destination}")
                continue
            row = {"source": str(source), "path": destination, "distribution": name, "size": source.stat().st_size, "source_record_sha256": expected}
            files.append(row)
            destinations[destination_key] = row
            if any(part.lower() in {"licenses", "license", ".licenses"} or part.upper().startswith(("LICENSE", "COPYING", "NOTICE")) for part in rel.parts):
                license_paths.append(destination)
        packages.append({"name": dist.metadata["Name"], "version": dist.version, "site_root": str(root), "pin": f"{dist.metadata['Name']}=={dist.version}", "license_expression": dist.metadata.get("License-Expression"), "license": dist.metadata.get("License"), "license_files": license_paths})
    return files, packages, omitted


def fixed_sources(folder, names, prefix):
    if folder is None:
        return []
    folder = Path(folder).resolve()
    rows = []
    for name in names:
        source = (folder / name).resolve()
        if not source.is_relative_to(folder) or not source.is_file():
            raise ValueError(f"Required explicit application file is missing: {source}")
        rows.append({"source": str(source), "path": prefix + "/" + name, "size": source.stat().st_size, "source_record_sha256": None})
    return rows


def app_local_vc_sources(files, packages):
    """Plan only reviewed app-local DLL copies, preserving relative provenance."""
    pins = {canonicalize_name(package["name"]): package["pin"] for package in packages}
    if pins.get("scikit-learn") != "scikit-learn==1.9.1":
        raise ValueError("App-local VC runtime source requires reviewed scikit-learn==1.9.1")
    copies, inventory = [], []
    for name, approved in APP_LOCAL_VC_DLLS.items():
        matches = [row for row in files if row["path"].casefold() == approved["source_package_path"].casefold()]
        if len(matches) != 1 or matches[0].get("distribution") != "scikit-learn":
            raise ValueError(f"Reviewed app-local VC runtime package file is missing or ambiguous: {name}")
        row = matches[0]
        expected = approved["sha256"]
        if row.get("source_record_sha256") != expected or sha256(row["source"]) != expected:
            raise ValueError(f"Reviewed app-local VC runtime SHA-256 mismatch: {name}")
        destination = "python/" + name
        if any(other["path"].casefold() == destination.casefold() for other in files):
            raise ValueError(f"App-local VC runtime destination already exists in source plan: {name}")
        copies.append({**row, "path": destination})
        inventory.append({"path": destination, "sha256": expected,
                          "source_package": pins["scikit-learn"],
                          "source_package_path": approved["source_package_path"],
                          "reviewed_file_version": "14.51.36247.0"})
    return copies, inventory


def embedded_vc_inventory(output, python_version, origin, archive_sha256):
    """Record the VC runtime DLLs supplied by the hash-verified CPython ZIP."""
    rows = []
    for name in ("vcruntime140.dll", "vcruntime140_1.dll"):
        file = output / "python" / name
        if not file.is_file():
            raise ValueError(f"Official Python embed VC runtime DLL is missing: {name}")
        rows.append({"path": "python/" + name, "sha256": sha256(file),
                     "source_package": "CPython==" + python_version,
                     "source_archive_origin": origin,
                     "source_archive_sha256": archive_sha256.lower(),
                     "source_archive_member": name})
    return rows


def model_relative(value):
    path = PurePosixPath(value.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts or not path.parts or ":" in value:
        raise ValueError("Model path must be relative to the portable folder")
    for part in path.parts:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._ -]*", part) or part.endswith((".", " ")):
            raise ValueError("Model folder name contains unsupported Windows or shell characters")
        if part.split(".", 1)[0].upper() in {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(1, 10)], *[f"LPT{i}" for i in range(1, 10)]}:
            raise ValueError("Model folder name is reserved by Windows")
    return path.as_posix()


def extract_python(archive, output, expected_hash):
    if not re.fullmatch(r"[a-fA-F0-9]{64}", expected_hash or "") or sha256(archive) != expected_hash.lower():
        raise ValueError("Official Python embed archive SHA-256 mismatch or missing expected hash")
    with zipfile.ZipFile(archive) as package:
        names = package.namelist()
        for required in ("python.exe", "python312.dll", "python312.zip", "vcruntime140.dll", "vcruntime140_1.dll"):
            if required not in names:
                raise ValueError(f"Expected Windows Python3.12 embed archive entry missing: {required}")
        for info in package.infolist():
            rel = PurePosixPath(info.filename)
            if rel.is_absolute() or ".." in rel.parts or ":" in info.filename:
                raise ValueError("Unsafe Python embed ZIP path")
            destination = (output / "python" / Path(*rel.parts)).resolve()
            if not destination.is_relative_to(output):
                raise ValueError("Python embed ZIP leaves output directory")
            if info.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                with package.open(info) as source, destination.open("wb") as target:
                    shutil.copyfileobj(source, target)
    # An explicit isolated path replaces the embed ZIP's optional import-site
    # line. No .pth execution, external venv, user packages, environment paths,
    # registry Python discovery or pip is needed by the portable worker.
    (output / "python" / "python312._pth").write_text("python312.zip\n.\nLib/site-packages\n../worker\n", encoding="utf8")


def launcher_script(model_path):
    """Return the portable launcher; caller supplies a validated relative path."""
    model_path = model_relative(model_path)
    return '@echo off\r\nsetlocal\r\nset "HF_HUB_OFFLINE=1"\r\nset "TRANSFORMERS_OFFLINE=1"\r\nset "DIFFUSERS_OFFLINE=1"\r\nset "HF_HUB_DISABLE_TELEMETRY=1"\r\nset "PYTHONNOUSERSITE=1"\r\nset "OMP_NUM_THREADS=4"\r\ncd /d "%~dp0"\r\n"%~dp0python\\python.exe" "%~dp0worker\\worker_server.py" --python "%~dp0python\\python.exe" --model "%~dp0' + model_path.replace('/', '\\') + '" --ffmpeg "%~dp0ffmpeg\\ffmpeg.exe" --frontend "%~dp0web" --data-dir "%LOCALAPPDATA%\\MITOM\\Video" --port 4176 --open-browser\r\nif errorlevel 1 pause\r\n'


def third_party_notices(packages, external_components):
    """Readable index of retained licenses; does not replace the license texts."""
    lines = ["MITOM Video - third-party notices", "", "This distribution contains the components listed below. Full license and", "notice texts remain at their recorded paths and in package METADATA files.", "This index does not replace or alter those terms.", ""]
    for package in packages:
        lines.append(package["pin"])
        declared = package.get("license_expression") or package.get("license")
        if declared:
            lines.append("  Declared license: " + " ".join(declared.split())[:240])
        paths = package.get("license_files", [])
        lines.extend("  " + path for path in paths)
        if not paths:
            lines.append("  No separate license file was present in the installed RECORD; see METADATA.")
        lines.append("")
    for component in external_components:
        lines.append(component["name"] + (" " + component["version"] if component.get("version") else ""))
        if component.get("origin"):
            lines.append("  Source: " + component["origin"])
        lines.extend("  " + path for path in component.get("license_files", []))
        for binary in component.get("binary_files", []):
            lines.append("  " + binary["path"])
            lines.append("    SHA-256: " + binary["sha256"])
            lines.append("    Source package: " + binary["source_package"])
            source_path = binary.get("source_package_path") or binary.get("source_archive_member")
            if source_path:
                lines.append("    Source file: " + source_path)
        lines.append("")
    lines.extend(["Brand and fonts", "  MITOM logo and application icon: supplied by the MITOM project owner.", "  SVN Helvetica Neue Regular and Akzidenz Grotesk Bold: owner-supplied fonts.", "  Font assets remain subject to their respective owners' terms.", "", "Model weights are distributed separately with their own license and notices.", ""])
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site-root", action="append", required=True, type=Path)
    parser.add_argument("--python-version", default="3.12.0")
    parser.add_argument("--worker-dir", type=Path)
    parser.add_argument("--frontend-dir", type=Path)
    parser.add_argument("--model-relative", default="models/ltx-video-cpu")
    parser.add_argument("--python-embed-zip", type=Path)
    parser.add_argument("--python-sha256")
    parser.add_argument("--python-origin")
    parser.add_argument("--ffmpeg", type=Path)
    parser.add_argument("--ffmpeg-sha256")
    parser.add_argument("--ffmpeg-license", type=Path)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if not re.fullmatch(r"3\.12\.\d+", args.python_version):
        raise ValueError("Choose an exact official Python3.12 patch version")
    roots = [path.resolve() for path in args.site_root]
    selected, edges, environment = resolve_closure(installed_index(roots), args.python_version)
    files, packages, omitted = dependency_plan(selected)
    vc_copies, vc_inventory = app_local_vc_sources(files, packages)
    files += vc_copies
    files += fixed_sources(args.worker_dir, WORKER_FILES, "worker")
    files += fixed_sources(args.frontend_dir, WEB_FILES, "web")
    model_path = model_relative(args.model_relative)
    plan = {"mode": "dry_run" if args.dry_run else "build", "platform": "Windows AMD64 CPU", "python_version": args.python_version, "roots": ROOT_REQUIREMENTS, "packages": packages, "dependency_edges": edges, "marker_environment": environment, "files": files, "omitted_record_entries": omitted, "source_bytes": sum(row["size"] for row in files), "weights_included": False, "models_exported": False, "persistent_service": False, "cloud_fallback": False, "source_file_hashes_verified": False, "app_local_vc_runtime": vc_inventory}
    if args.dry_run:
        write_json(args.manifest, plan)
        print(json.dumps({"mode": "dry_run", "manifest": str(args.manifest.resolve()), "packages": len(packages), "files": len(files), "source_bytes": plan["source_bytes"]}))
        return
    if not all((args.output_dir, args.python_embed_zip, args.python_sha256, args.python_origin, args.worker_dir, args.frontend_dir, args.ffmpeg, args.ffmpeg_sha256, args.ffmpeg_license)):
        raise ValueError("Build requires explicit output, Python archive/hash/origin, worker, frontend and FFmpeg/hash/license")
    origin = urlsplit(args.python_origin)
    expected_archive = f"/python-{args.python_version}-embed-amd64.zip"
    if origin.scheme != "https" or origin.hostname != "www.python.org" or origin.username or origin.password or origin.query or origin.fragment or not origin.path.endswith(expected_archive):
        raise ValueError("Record an official HTTPS www.python.org Windows embed ZIP origin")
    output = args.output_dir.resolve()
    if output.exists():
        raise ValueError("Output directory must not already exist; no existing runtime is overwritten")
    if sha256(args.ffmpeg) != args.ffmpeg_sha256.lower():
        raise ValueError("Explicit FFmpeg SHA-256 does not match")
    if not args.ffmpeg_license.is_file():
        raise ValueError("Provide the reviewed license for this FFmpeg build")
    output.mkdir(parents=True)
    extract_python(args.python_embed_zip, output, args.python_sha256)
    vc_inventory = embedded_vc_inventory(output, args.python_version, args.python_origin, args.python_sha256) + vc_inventory
    audited = []
    print(json.dumps({"phase": "copy_and_verify", "files": len(files)}), flush=True)
    for position, row in enumerate(files, 1):
        destination = output / row["path"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(row["source"], destination)
        digest = sha256(destination)
        if row["source_record_sha256"] and digest != row["source_record_sha256"]:
            raise ValueError(f"Installed RECORD hash mismatch; build remains incomplete: {row['path']}")
        audited.append({"path": row["path"], "size": destination.stat().st_size, "sha256": digest, "distribution": row.get("distribution")})
        if position % 1000 == 0 or position == len(files):
            print(json.dumps({"phase": "copy_and_verify", "completed": position, "total": len(files)}), flush=True)
    (output / "ffmpeg").mkdir()
    shutil.copy2(args.ffmpeg, output / "ffmpeg" / "ffmpeg.exe")
    shutil.copy2(args.ffmpeg_license, output / "ffmpeg" / "LICENSE.txt")
    (output / "launch.cmd").write_bytes(launcher_script(model_path).encode("utf8"))
    (output / model_path).mkdir(parents=True, exist_ok=True)
    (output / "requirements-pinned.txt").write_text("\n".join(row["pin"] for row in packages) + "\n", encoding="utf8")
    license_inventory = {"packages": [{key: value for key, value in row.items() if key != "site_root"} for row in packages], "external_components": [{"name": "CPython", "version": args.python_version, "origin": args.python_origin, "license_files": [file.relative_to(output).as_posix() for file in (output / "python").rglob("*") if file.is_file() and file.name.upper().startswith("LICENSE")]}, {"name": "FFmpeg", "sha256": args.ffmpeg_sha256.lower(), "license_files": ["ffmpeg/LICENSE.txt"]}]}
    license_inventory["external_components"].append({"name": "Microsoft Visual C++ Runtime (app-local)", "origin": "https://learn.microsoft.com/en-us/cpp/windows/redistributing-visual-cpp-files", "binary_files": vc_inventory, "license_files": []})
    write_json(output / "license-inventory.json", license_inventory)
    (output / "THIRD_PARTY_NOTICES.txt").write_text(third_party_notices(packages, license_inventory["external_components"]), encoding="utf8")
    # Inventory ALL resulting files, including the official embed files and
    # launcher. Source absolute paths are excluded from the portable audit.
    print(json.dumps({"phase": "audit_final_files"}), flush=True)
    all_files = [{"path": file.relative_to(output).as_posix(), "size": file.stat().st_size, "sha256": sha256(file)} for file in sorted(output.rglob("*")) if file.is_file()]
    final = {key: value for key, value in plan.items() if key not in {"files", "packages", "omitted_record_entries"}}
    final.update(files=all_files, packages=[{key: value for key, value in row.items() if key != "site_root"} for row in packages], source_file_hashes_verified=True, python_origin=args.python_origin, python_archive_sha256=args.python_sha256.lower(), ffmpeg_sha256=args.ffmpeg_sha256.lower(), model_relative=model_path, inference_verified=False, portable_machine_tested=False, app_local_vc_runtime=vc_inventory)
    write_json(output / "runtime-manifest.json", final)
    write_json(args.manifest, final)
    print(json.dumps({"mode": "build", "output": str(output), "files": len(all_files), "inference_verified": False, "portable_machine_tested": False}))


if __name__ == "__main__":
    main()
