"""Exercise the frozen app's real HTTP session and spawned tracing worker."""
from __future__ import annotations

import argparse
from io import BytesIO
import json
from pathlib import Path
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import zipfile

from PIL import Image, ImageDraw


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
            assert status == 200 and json.loads(config)["version"] == "1.0.0"
            image = Image.new("RGBA", (160, 120), (0, 0, 0, 0))
            draw = ImageDraw.Draw(image)
            draw.rectangle((12, 15, 90, 100), fill="#1559df")
            draw.ellipse((45, 20, 145, 110), fill="#d8660a")
            raster = BytesIO()
            image.save(raster, "PNG")
            _, raw, _ = request("/api/upload", raster.getvalue(), {"X-File-Name": "packaged-synthetic.png", "Content-Type": "application/octet-stream"})
            upload = json.loads(raw)
            _, raw, _ = request("/api/vectorize", {"image_id": upload["id"], "options": {"preset": "logo", "colors": 8}})
            job = json.loads(raw)
            identity = job["id"]
            started = time.monotonic()
            deadline = started + 60
            while job["status"] not in {"done", "error", "cancelled"}:
                if time.monotonic() > deadline:
                    raise TimeoutError("Frozen tracing worker did not complete.")
                time.sleep(.1)
                _, raw, _ = request("/api/job/" + identity)
                job = json.loads(raw)
            assert job["status"] == "done", job.get("message")
            export_sizes = {}
            for format, signature in (("svg", b"<svg"), ("pdf", b"%PDF"), ("eps", b"%!PS")):
                _, raw, _ = request("/api/export", {"job_id": identity, "format": format})
                assert signature in raw[:1024], format
                if format == "svg":
                    assert b"<path" in raw and b"<image" not in raw
                export_sizes[format] = len(raw)
            _, batch, _ = request("/api/export-batch", {"job_ids": [identity], "format": "svg"})
            with zipfile.ZipFile(BytesIO(batch)) as archive:
                assert len(archive.namelist()) == 1 and archive.testzip() is None
            request("/api/shutdown", {})
            process.wait(timeout=10)
            assert process.returncode == 0
            return {"ok": True, "version": "1.0.0", "no_browser": True, "all_static_assets": True,
                    "authenticated_loopback_api": True, "spawned_worker": True,
                    "path_only_svg": True, "exports": export_sizes, "batch_zip": True,
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
