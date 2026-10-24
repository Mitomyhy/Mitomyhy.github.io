"""Prepare deterministic executable icon and upstream redistribution notices."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import shutil
import struct
import sys
import zipfile

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app_version import VERSION

RUNTIME_PACKAGES = (
    "vtracer", "Pillow", "svglib", "reportlab", "cssselect2", "lxml",
    "tinycss2", "charset-normalizer", "webencodings",
)


def verify_branding_png(icon: Path) -> None:
    """Prevent authoring-document metadata entering public app artifacts."""
    data = icon.read_bytes()
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("The branding icon must be a PNG file.")
    offset = 8
    allowed = {b"IHDR", b"PLTE", b"tRNS", b"IDAT", b"IEND", b"sRGB", b"gAMA", b"cHRM", b"pHYs"}
    while offset < len(data):
        if offset + 12 > len(data):
            raise ValueError("The branding PNG has a truncated chunk.")
        length = struct.unpack_from(">I", data, offset)[0]
        kind = data[offset + 4:offset + 8]
        if kind not in allowed:
            raise ValueError("Remove non-rendering metadata from the branding PNG before packaging.")
        offset += length + 12
        if offset > len(data):
            raise ValueError("The branding PNG has an invalid chunk length.")
        if kind == b"IEND":
            break
    if offset != len(data):
        raise ValueError("The branding PNG has trailing data.")


def prepare(source: Path, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    icon = source / "assets" / "icon.png"
    if not icon.is_file():
        raise FileNotFoundError(f"Expected user-provided app icon: {icon}")
    verify_branding_png(icon)
    with Image.open(icon) as original:
        # File-format conversion for the Windows executable. The supplied PNG
        # remains byte-for-byte unchanged as the app's visual brand asset.
        rgba = original.convert("RGBA")
        rgba.thumbnail((256, 256), Image.Resampling.LANCZOS)
        square = Image.new("RGBA", (256, 256), (255, 255, 255, 0))
        square.alpha_composite(rgba, ((256-rgba.width)//2, (256-rgba.height)//2))
        square.save(output / "VectorMigic.ico", sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])
    notices = output / "notices"
    notices.mkdir(exist_ok=True)
    index: list[dict] = []
    for name in RUNTIME_PACKAGES:
        distribution = importlib.metadata.distribution(name)
        target = notices / name
        target.mkdir(exist_ok=True)
        metadata = distribution.metadata
        licenses = []
        for relative in distribution.files or []:
            lower = relative.name.lower()
            if any(term in lower for term in ("license", "licence", "copying", "notice")):
                source_file = Path(distribution.locate_file(relative))
                if source_file.is_file() and source_file.stat().st_size <= 2_000_000:
                    destination = target / str(relative).replace("/", "__").replace("\\", "__")
                    shutil.copyfile(source_file, destination)
                    licenses.append(destination.name)
        # Read the wheel metadata verbatim; rendering Message objects treats
        # Markdown descriptions as mail headers on some Python 3.12 builds.
        metadata_text = distribution.read_text("METADATA") or "\n".join(f"{key}: {value}" for key, value in metadata.items())
        (target / "PACKAGE-METADATA.txt").write_text(metadata_text, encoding="utf-8")
        index.append({"name": name, "version": distribution.version,
                      "license": metadata.get("License-Expression") or metadata.get("License") or "See upstream package metadata",
                      "license_files": licenses})
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if not python_license.is_file():
        raise FileNotFoundError("Python redistribution license was not found.")
    shutil.copyfile(python_license, notices / "PYTHON-LICENSE.txt")
    potrace = source / "vendor" / "potrace"
    potrace_metadata = json.loads((potrace / "upstream.json").read_text(encoding="utf-8"))
    potrace_exe = potrace / "potrace.exe"
    potrace_source = potrace / "source" / "potrace-1.16.tar.gz"
    if hashlib.sha256(potrace_exe.read_bytes()).hexdigest() != "f1bcde040ae6576dc9c5868be7a72064957c5aea08e1dece240428319fc672a4":
        raise ValueError("The bundled Potrace CLI does not match the reviewed official binary.")
    if hashlib.sha256(potrace_source.read_bytes()).hexdigest() != "be8248a17dedd6ccbaab2fcc45835bb0502d062e40fbded3bc56028ce5eb7acc":
        raise ValueError("The matching Potrace upstream source archive is missing or changed.")
    potrace_notices = notices / "potrace"
    potrace_notices.mkdir(exist_ok=True)
    for name in ("COPYING", "AUTHORS", "README", "README-WIN", "potrace.1", "LICENSE-NOTICE.txt", "upstream.json"):
        shutil.copyfile(potrace / name, potrace_notices / name)
    shutil.copyfile(potrace_source, potrace_notices / potrace_source.name)
    index.append({"name": "Potrace CLI", "version": "1.16", "license": "GPL-2.0-or-later",
                  "binary_sha256": potrace_metadata["executable_sha256"],
                  "source_archive": "potrace/potrace-1.16.tar.gz",
                  "upstream": "https://potrace.sourceforge.net/"})
    (notices / "packages.json").write_text(json.dumps(index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (notices / "README.txt").write_text(
        f"Vector Migic {VERSION}\n"
        "An independent local raster-to-vector application by MITOM.\n"
        "This application is not affiliated with Vector Magic.\n\n"
        "The bundled runtime uses Python, the separate Potrace CLI, VTracer, Pillow, svglib, ReportLab, lxml,\n"
        "cssselect2, tinycss2, charset-normalizer and webencodings.\n"
        "See packages.json and each package folder for upstream license notices.\n"
        "Potrace's GPL license, authors and complete unmodified source archive\n"
        "are included in potrace/ and vendor/potrace/source/. Its official binary\n"
        "is unmodified; source URLs and hashes are included.\n"
        "The unmodified svglib source distribution and its checksum are included.\n"
        f"VectorMigic-source-{VERSION}.zip and SOURCE-REBUILD.txt allow rebuilding this app\n"
        "with a modified or replacement svglib library.\n"
        "PyInstaller bootloader redistribution uses the GPL exception documented at\n"
        "https://pyinstaller.org/en/stable/license.html\n", encoding="utf-8")
    bootloader = importlib.metadata.distribution("pyinstaller")
    for relative in bootloader.files or []:
        if relative.name.lower() in {"copying.txt", "license", "license.txt"}:
            original = Path(bootloader.locate_file(relative))
            if original.is_file():
                shutil.copyfile(original, notices / ("PYINSTALLER-" + relative.name))
    source_metadata_path = source / "packaging" / "upstream" / "svglib-source.json"
    source_metadata = json.loads(source_metadata_path.read_text(encoding="utf-8"))
    svglib_archive = source_metadata_path.parent / source_metadata["file"]
    if source_metadata["version"] != importlib.metadata.version("svglib") or hashlib.sha256(svglib_archive.read_bytes()).hexdigest() != source_metadata["sha256"]:
        raise ValueError("The redistributable svglib source must match the installed library.")
    shutil.copyfile(svglib_archive, notices / "svglib" / svglib_archive.name)
    shutil.copyfile(source_metadata_path, notices / "svglib" / source_metadata_path.name)
    (notices / "SOURCE-REBUILD.txt").write_text(
        f"Vector Migic {VERSION} reproducible-source instructions (Windows x64)\n\n"
        f"1. Install CPython 3.12 x64 and extract VectorMigic-source-{VERSION}.zip into a\n"
        "   folder named vector_migic. Open PowerShell in its parent directory.\n"
        "2. py -3.12 -m venv .work/vector-migic-env\n"
        "3. .work/vector-migic-env/Scripts/python.exe -m pip install -r vector_migic/requirements.txt -r vector_migic/requirements-build.txt\n"
        "4. To replace svglib, install your replacement with this interpreter.\n"
        "   Update requirements.txt and packaging/upstream/svglib-source.json\n"
        "   plus its source archive so the bundled notices match the replacement.\n"
        "5. The separate Potrace CLI is under vendor/potrace/. Its complete\n"
        "   upstream source archive and build/replacement instructions are there.\n"
        "   Follow its LICENSE-NOTICE.txt and preserve all GPL source/notices.\n"
        "6. powershell -NoProfile -ExecutionPolicy Bypass -File vector_migic/build.ps1\n\n"
        "No original app object code is withheld: the complete application\n"
        "source and packaging code are included for relinking/rebuilding.\n"
        "The svglib library is unmodified and retains its upstream LGPL license.\n"
        "You may modify/replace that library for your own use and debug those\n"
        "modifications under its license.\n", encoding="utf-8")
    source_members = sorted(file for file in source.rglob("*") if file.is_file() and not any(part in {"__pycache__", ".git", ".work"} for part in file.relative_to(source).parts))
    with zipfile.ZipFile(notices / f"VectorMigic-source-{VERSION}.zip", "w", zipfile.ZIP_DEFLATED, compresslevel=6) as source_zip:
        for file in source_members:
            member = "vector_migic/" + file.relative_to(source).as_posix()
            info = zipfile.ZipInfo(member, (2026, 10, 24, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            source_zip.writestr(info, file.read_bytes())


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    prepare(args.source.resolve(), args.output.resolve())
