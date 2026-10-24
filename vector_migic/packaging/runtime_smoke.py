"""Exercise the frozen app's real HTTP session and spawned tracing worker."""
from __future__ import annotations

import argparse
import colorsys
from io import BytesIO
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import re
import urllib.error
import urllib.request
import zipfile

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app_version import VERSION


def run(executable: Path) -> dict:
    with tempfile.TemporaryDirectory(prefix="vector-migic-packaged-smoke-") as directory:
        folder = Path(directory)
        info = folder / "session.json"
        process = subprocess.Popen([str(executable), "--no-browser", "--info-json", str(info), "--idle-timeout", "90"],
                                   cwd=folder, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        request = None
        try:
            deadline = time.monotonic() + 30
            while not info.is_file():
                if process.poll() is not None:
                    raise RuntimeError(f"Packaged local server exited before startup: {process.returncode}")
                if time.monotonic() > deadline:
                    raise TimeoutError("Packaged local server did not start.")
                time.sleep(.1)
            session = json.loads(info.read_text(encoding="utf-8"))
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            def request(path, data=None, extra=None, authorized=True):
                headers = {"Origin": session["origin"]}
                if authorized:
                    headers["X-Migic-Token"] = session["token"]
                if isinstance(data, dict):
                    data = json.dumps(data).encode()
                    headers["Content-Type"] = "application/json"
                headers.update(extra or {})
                with opener.open(urllib.request.Request(session["origin"] + path, data=data, headers=headers), timeout=30) as response:
                    return response.status, response.read(), response.headers
            for path in ("/", "/static/app.js", "/static/style.css", "/assets/icon.png", "/assets/body.ttf", "/assets/heading.ttf"):
                status, data, _ = request(path, authorized=False)
                assert status == 200 and len(data) > 100, path
            try:
                request("/api/config", authorized=False)
            except urllib.error.HTTPError as error:
                assert error.code == 403
            else:
                raise AssertionError("Private API accepted a request without the session token.")
            status, config, _ = request("/api/config")
            config = json.loads(config)
            assert status == 200 and config["version"] == VERSION
            logo_defaults = {"preset": "logo", "color_mode": "color", "colors": 0,
                             "mode": "spline", "max_dimension": 2048,
                             "bw_threshold": 128, "alpha_threshold": 128,
                             "color_precision": 8, "corner_threshold": 60,
                             "smoothing": .5, "speckle": 1, "transparent": True}
            assert config["presets"]["logo"] == logo_defaults, "Config must expose complete normalized Logo defaults."
            image = Image.new("RGBA", (160, 120), (0, 0, 0, 0))
            draw = ImageDraw.Draw(image)
            draw.rectangle((12, 15, 90, 100), fill="#1559df")
            draw.ellipse((45, 20, 145, 110), fill="#d8660a")
            raster = BytesIO()
            image.save(raster, "PNG")
            _, raw, _ = request("/api/upload", raster.getvalue(), {"X-File-Name": "packaged-synthetic.png", "Content-Type": "application/octet-stream"})
            upload = json.loads(raw)
            started = time.monotonic()
            def trace(image_id):
                _, raw, _ = request("/api/vectorize", {"image_id": image_id, "options": {"preset": "logo"}})
                current = json.loads(raw)
                deadline = time.monotonic() + 90
                while current["status"] not in {"done", "error", "cancelled"}:
                    if time.monotonic() > deadline:
                        raise TimeoutError("Frozen tracing worker did not complete.")
                    time.sleep(.1)
                    _, raw, _ = request("/api/job/" + current["id"])
                    current = json.loads(raw)
                assert current["status"] == "done", current.get("message")
                assert current.get("metrics", {}).get("trace_backend") == "potrace-1.16", "Frozen app did not execute its reviewed Potrace tracing backend."
                assert current.get("metrics", {}).get("curve_backend") == "potrace", "Unexpected frozen curve backend."
                assert current["metrics"]["options"] == logo_defaults, "Default Logo options changed between config and execution."
                return current
            job = trace(upload["id"])
            identity = job["id"]
            export_sizes = {}
            for format, signature in (("svg", b"<svg"), ("pdf", b"%PDF"), ("eps", b"%!PS"), ("ai", b"%!PS-Adobe-3.0")):
                _, raw, headers = request("/api/export", {"job_id": identity, "format": format})
                assert signature in raw[:1024], format
                if format == "svg":
                    assert b"<path" in raw and b"<image" not in raw
                if format == "ai":
                    assert raw.startswith(b"%!PS-Adobe-3.0\n") and b"EPSF" not in raw.splitlines()[0]
                    assert b"%AI5_FileFormat 4.0" in raw and b"VectorMigic_AI8" in raw
                    assert re.search(rb"\bXa\b", raw) and re.search(rb"\bC\b", raw)
                    assert b"%PDF" not in raw[:1024]
                    assert headers.get_content_type() == "application/postscript"
                    assert ".ai" in headers["Content-Disposition"]
                export_sizes[format] = len(raw)

            # More than sixteen independent inks catches a silent fixed-palette
            # regression. It is generated in temporary storage, never bundled.
            rich = Image.new("RGBA", (240, 160), (0, 0, 0, 0))
            rich_draw = ImageDraw.Draw(rich)
            for index in range(24):
                rgb = tuple(round(channel * 255) for channel in colorsys.hsv_to_rgb(index / 24, .9, .9))
                column, row = index % 6, index // 6
                rich_draw.rectangle((column * 40 + 3, row * 40 + 3, column * 40 + 35, row * 40 + 35), fill=(*rgb, 255))
            rich_raster = BytesIO()
            rich.save(rich_raster, "PNG")
            _, raw, _ = request("/api/upload", rich_raster.getvalue(), {"X-File-Name": "automatic-palette-synthetic.png", "Content-Type": "application/octet-stream"})
            rich_job = trace(json.loads(raw)["id"])
            _, rich_svg, _ = request("/api/export", {"job_id": rich_job["id"], "format": "svg"})
            fills = set(re.findall(rb'fill=[\"\'](#[0-9A-Fa-f]{6}|rgb\([^)]+\))[\"\']', rich_svg))
            assert len(fills) > 16, f"Auto palette retained only {len(fills)} distinct vector inks."
            for format in ("svg", "ai"):
                _, batch, headers = request("/api/export-batch", {"job_ids": [identity, rich_job["id"]], "format": format})
                with zipfile.ZipFile(BytesIO(batch)) as archive:
                    assert len(archive.namelist()) == 2 and archive.testzip() is None
                    assert all(name.endswith("." + format) for name in archive.namelist())
                    if format == "ai":
                        assert "VectorMigic-ai.zip" in headers["Content-Disposition"]
                        assert all(archive.read(name).startswith(b"%!PS-Adobe-3.0\n") and b"%AI5_FileFormat 4.0" in archive.read(name) for name in archive.namelist())
            request("/api/shutdown", {})
            process.wait(timeout=10)
            assert process.returncode == 0
            return {"ok": True, "version": VERSION, "no_browser": True, "all_static_assets": True,
                    "authenticated_loopback_api": True, "spawned_worker": True,
                    "trace_backend": job["metrics"]["trace_backend"],
                    "curve_backend": job["metrics"]["curve_backend"],
                    "path_only_svg": True, "exports": export_sizes, "batch_zip": True,
                    "ai_batch_zip": True, "editable_illustrator_paths": True,
                    "complete_normalized_logo_defaults": True,
                    "default_logo_auto_palette": True, "automatic_palette_inks": len(fills),
                    "conversion_seconds": round(time.monotonic() - started, 3),
                    "external_services_required": False, "clean_shutdown": True}
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--executable", type=Path, required=True)
    parser.add_argument("--result-json", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.executable.resolve())
    args.result_json.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result))
