"""Prepare one reviewed, synchronized online release; never upload anything.

Future Launcher/app maintenance should use this single staging entry point so
the website, signed Launcher channel and signed app catalogue cannot drift.
The public website uses root-level files, not the old mitom-web workflow.
Without --sign this emits a preview and unsigned payloads only. --sign uses the
existing local DPAPI publisher key; it never initializes or rotates that key.
The original Panel channel and both verification keys are always read-only.

For an app-only release, reuse the exact published Launcher executable and its
--version, then increase --catalogue-version. No Launcher rebuild is needed:
  ... --artifact <existing.exe> --version 2026.10.25.7
      --catalogue-version 2026.10.26.1 --mifont-release <new-descriptor> ...
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import urllib.parse
import zipfile

LAUNCHER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LAUNCHER))
import app_catalogue
import backend
import launcher_channel
import mi_font_app
import vector_app

MODULES = {"mifont": mi_font_app, "vector-migic": vector_app}
DESCRIPTOR_NAMES = {"mifont": "mifont-release.json", "vector-migic": "vector-release.json"}
MANIFEST_NAMES = {"mifont": "mifont-app.json", "vector-migic": "vector-app.json"}
BASE_FILES = ("releases/stable.json", "releases/launcher-stable.json",
              "releases/latest-download.json", "site-config.js", "index.html")


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read_json(path):
    return launcher_channel._json_document(Path(path).read_bytes())


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def write_json(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(json_bytes(value))


def _version(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}\.\d{2}\.\d{2}\.\d+", value):
        raise ValueError("Release version must use YYYY.MM.DD.N.")
    return backend.version_tuple(value)


def _field(text, name, value):
    pattern = re.compile(r"(?m)^(\s*" + re.escape(name) + r"\s*:\s*)(['\"])(.*?)\2(\s*,?\s*)$")
    result, count = pattern.subn(lambda m: m[1] + json.dumps(value, ensure_ascii=False) + m[4], text)
    if count != 1:
        raise ValueError("Expected exactly one website field: " + name)
    return result


def _card(text, app_id):
    matches = list(re.finditer(r"(?m)^\s*id\s*:\s*(['\"])" + re.escape(app_id) + r"\1\s*,", text))
    if len(matches) != 1:
        raise ValueError("Expected exactly one website app card: " + app_id)
    match = matches[0]
    end = re.search(r"(?m)^\s{2}\}(?:,\s*\{|\])", text[match.end():])
    if not end:
        raise ValueError("Cannot locate website app card boundary: " + app_id)
    return match.start(), match.end() + end.start()


def _card_version(text, app_id):
    start, end = _card(text, app_id)
    match = re.search(r"(?m)^\s*version\s*:\s*(['\"])(.*?)\1\s*,?\s*$", text[start:end])
    if not match:
        raise ValueError("Missing website app version: " + app_id)
    return match[2]


def update_site(config, index, version, artifact, panel, apps, cache_version=None):
    config = _field(config, "releaseUrl", "https://github.com/Mitomyhy/Mitomyhy.github.io/tree/main/releases/mitom-launcher/" + version)
    config = _field(config, "launcherVersion", version)
    config = _field(config, "launcherDownloadUrl", artifact["url"])
    for app_id, app_version in {"photoshop-panel": panel["version"], **{key: app["version"] for key, app in apps.items()}}.items():
        start, end = _card(config, app_id)
        card = _field(config[start:end], "version", app_version)
        if app_id != "photoshop-panel":
            card = _field(card, "githubUrl", "https://github.com/Mitomyhy/Mitomyhy.github.io/tree/main/releases/" + app_id + "/" + app_version)
        config = config[:start] + card + config[end:]
    pattern = re.compile(r"(?P<prefix>\bsrc\s*=\s*['\"])(?P<path>(?:\./)?site-config\.js)(?:\?[^'\"]*)?(?P<end>['\"])")
    cache_version = cache_version or version
    index, count = pattern.subn(lambda m: m["prefix"] + m["path"] + "?v=release-" + cache_version.replace(".", "-") + m["end"], index)
    if count != 1:
        raise ValueError("Expected one site-config.js script in index.html.")
    return config, index


def _archive_for(descriptor, path, explicit=None):
    archive = descriptor["archive"]
    candidates = [Path(explicit)] if explicit else []
    if archive.get("bundled"):
        candidates.append(path.parent / archive["bundled"])
    basename = Path(urllib.parse.urlsplit(archive["url"]).path).name
    candidates.extend((path.parent / basename, LAUNCHER / "apps" / basename))
    existing = next((item for item in candidates if item.is_file()), None)
    if existing is None:
        raise ValueError("Supply a local app archive for " + descriptor["app_id"])
    if existing.stat().st_size != archive["size"] or sha(existing) != archive["sha256"]:
        raise ValueError("App archive size/SHA-256 differs from descriptor: " + descriptor["app_id"])
    with zipfile.ZipFile(existing) as package:
        manifest_name = MANIFEST_NAMES[descriptor["app_id"]]
        if package.namelist().count(manifest_name) != 1:
            raise ValueError("App archive must contain exactly one runtime manifest.")
        content = package.read(manifest_name)
        if hashlib.sha256(content).hexdigest() != descriptor["manifest_sha256"]:
            raise ValueError("App runtime manifest differs from descriptor.")
        manifest = launcher_channel._json_document(content)
        if any(manifest.get(key) != descriptor[key] for key in ("schema", "app_id", "version", "entry")):
            raise ValueError("App runtime identity differs from descriptor.")
    return existing


def _history(apps, previous, descriptor_paths):
    candidates = {app_id: [] for app_id in MODULES}
    if previous:
        for app_id in MODULES:
            candidates[app_id] += [previous["apps"][app_id], *previous["history"][app_id]]
    for path in descriptor_paths:
        raw = read_json(path)
        app_id = raw.get("app_id")
        if app_id not in MODULES:
            raise ValueError("Unsupported previous app descriptor.")
        candidates[app_id].append(MODULES[app_id].validate_release(raw))
    result = {}
    for app_id, current in apps.items():
        entries = {current["version"]: current}
        for raw in candidates[app_id]:
            raw = MODULES[app_id].validate_release(raw)
            old = entries.get(raw["version"])
            if old is not None:
                # Bundled locations are local packaging hints, not identity.
                left, right = copy.deepcopy(old), copy.deepcopy(raw)
                left["archive"].pop("bundled", None)
                right["archive"].pop("bundled", None)
                if left != right:
                    raise ValueError("Conflicting descriptors for " + app_id + " " + raw["version"])
                continue
            if backend.version_tuple(raw["version"]) >= backend.version_tuple(current["version"]):
                raise ValueError("App rollback rejected: " + app_id)
            if raw["entry"] != current["entry"]:
                raise ValueError("Previous app executable identity changed.")
            entries[raw["version"]] = raw
        ordered = sorted(entries.values(), key=lambda row: backend.version_tuple(row["version"]), reverse=True)
        result[app_id] = [row for row in ordered if row["version"] != current["version"]][:32]
    return result


def _sign(payload_path, destination):
    result = subprocess.run(["pwsh.exe", "-NoProfile", "-File", str(LAUNCHER / "publishing/sign-launcher-channel.ps1"),
                             "-PayloadFile", str(payload_path), "-OutputFile", str(destination)],
                            capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError("Existing publisher key could not sign this release: " + result.stderr[-1000:])


def _check_launcher_artifact(artifact, *authenticated_releases):
    """An app release may reuse exact published bytes, but cannot replace them."""
    candidates = []
    for prior in authenticated_releases:
        raw = prior.get("launcher")
        if raw:
            candidates.append({key: raw[key] for key in ("version", "url", "size", "sha256")})
    if not candidates:
        return
    highest = max(candidates, key=lambda row: _version(row["version"]))
    if any(row["version"] == highest["version"] and row != highest for row in candidates):
        raise ValueError("Authenticated channels disagree about the current Launcher identity.")
    comparison = _version(artifact["version"])
    if comparison < _version(highest["version"]):
        raise ValueError("Launcher release rollback rejected.")
    if comparison == _version(highest["version"]) and artifact != highest:
        raise ValueError("Existing Launcher version must retain its exact authenticated URL, size and SHA-256.")


def prepare(*, baseline_dir, artifact_path, version, mifont_release, vector_release,
            self_test_receipt, output, previous_descriptors=(), app_archives=None, sign=False,
            catalogue_version=None):
    """Validate immutable inputs, then create a new reviewable stage directory."""
    _version(version)
    catalogue_version = catalogue_version or version
    _version(catalogue_version)
    baseline_dir, artifact_path, output = map(Path, (baseline_dir, artifact_path, output))
    if output.exists():
        raise ValueError("Output must be a new directory; an existing stage is never overwritten.")
    snapshots = {name: (baseline_dir / name).read_bytes() for name in BASE_FILES}
    key_snapshots = {path: path.read_bytes() for path in
                     (LAUNCHER / "release-public-key.json", LAUNCHER / "launcher-update-public-key.json")}
    catalogue_path = baseline_dir / "releases/apps-stable.json"
    if catalogue_path.exists():
        snapshots["releases/apps-stable.json"] = catalogue_path.read_bytes()
    panel = backend.verify_release_envelope(launcher_channel._json_document(snapshots["releases/stable.json"]), backend.load_public_key())
    previous_launcher = backend.verify_release_envelope(launcher_channel._json_document(snapshots["releases/launcher-stable.json"]), launcher_channel.load_launcher_public_key())
    previous = app_catalogue.verify_catalogue(backend, read_json(catalogue_path)) if catalogue_path.exists() else None
    if previous and _version(catalogue_version) <= _version(previous["version"]):
        raise ValueError("App catalogue revision must increase.")
    if not artifact_path.is_file():
        raise ValueError("Launcher artifact is not a Windows executable.")
    with artifact_path.open("rb") as stream:
        if stream.read(2) != b"MZ":
            raise ValueError("Launcher artifact is not a Windows executable.")
    receipt = read_json(self_test_receipt)
    if receipt.get("ok") is not True or receipt.get("version") != version:
        raise ValueError("Frozen Launcher self-test receipt does not match release version.")
    artifact = {"version": version,
                "url": "https://mitomyhy.github.io/releases/mitom-launcher/" + version + "/MITOM-Launcher-Setup-" + version + ".exe",
                "size": artifact_path.stat().st_size, "sha256": sha(artifact_path)}
    backend.validate_artifact(artifact)
    _check_launcher_artifact(artifact, panel, previous_launcher)
    for key, expected in (("artifact_sha256", artifact["sha256"]), ("artifact_size", artifact["size"])):
        if key in receipt and receipt[key] != expected:
            raise ValueError("Frozen self-test receipt differs from Launcher artifact: " + key)
    apps, archives = {}, {}
    for app_id, path in (("mifont", Path(mifont_release)), ("vector-migic", Path(vector_release))):
        apps[app_id] = MODULES[app_id].validate_release(read_json(path))
        if not apps[app_id]["archive"].get("url"):
            raise ValueError("Online catalogue requires app archive URLs.")
        archives[app_id] = _archive_for(apps[app_id], path, (app_archives or {}).get(app_id))
    history = _history(apps, previous, previous_descriptors)
    launcher_payload = copy.deepcopy(panel)
    launcher_payload["launcher"] = artifact
    catalogue = {"schema": 1, "purpose": "mitom-app-catalogue", "version": catalogue_version,
                 "apps": apps, "history": history}
    if len(json_bytes(catalogue)) > app_catalogue.LIMIT:
        raise ValueError("App catalogue exceeds runtime size limit.")
    config, index = update_site(snapshots["site-config.js"].decode("utf-8-sig"), snapshots["index.html"].decode("utf-8-sig"), version, artifact, panel, apps, cache_version=catalogue_version)
    metadata = launcher_channel._json_document(snapshots["releases/latest-download.json"])
    metadata.update(artifact)
    metadata.update(panel_version=panel["version"], core_size=panel["core"]["size"],
                    model_size=sum(row["size"] for row in panel.get("external_files", [])),
                    runtime_size=sum(row["size"] for row in panel.get("external_bundles", [])),
                    installed_size=panel["core"].get("unpacked_size", metadata.get("installed_size")))
    site = output / "site"
    (site / "releases").mkdir(parents=True)
    (output / "payloads").mkdir()
    launcher_unsigned, apps_unsigned = output / "payloads/launcher.json", output / "payloads/apps.json"
    write_json(launcher_unsigned, launcher_payload)
    write_json(apps_unsigned, catalogue)
    (site / "site-config.js").write_text(config, encoding="utf-8", newline="")
    (site / "index.html").write_text(index, encoding="utf-8", newline="")
    write_json(site / "releases/latest-download.json", metadata)
    artifact_relative = urllib.parse.urlsplit(artifact["url"]).path.lstrip("/")
    (site / artifact_relative).parent.mkdir(parents=True)
    shutil.copyfile(artifact_path, site / artifact_relative)
    for app_id, release in apps.items():
        # Unchanged app bytes already exist publicly; new versions travel in the
        # same stage and commit as the catalogue that makes them discoverable.
        if _card_version(snapshots["site-config.js"].decode("utf-8-sig"), app_id) == release["version"]:
            continue
        url = urllib.parse.urlsplit(release["archive"]["url"])
        if url.hostname != "mitomyhy.github.io" or not url.path.startswith("/releases/" + app_id + "/"):
            raise ValueError("Stage new app archives at the canonical Pages release location.")
        relative = url.path.lstrip("/")
        (site / relative).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(archives[app_id], site / relative)
        write_json((site / relative).parent / DESCRIPTOR_NAMES[app_id], release)
    if sign:
        _sign(launcher_unsigned, site / "releases/launcher-stable.json")
        _sign(apps_unsigned, site / "releases/apps-stable.json")
        if ((site / "releases/launcher-stable.json").stat().st_size > backend.FEED_LIMIT
                or (site / "releases/apps-stable.json").stat().st_size > app_catalogue.LIMIT):
            raise ValueError("Signed envelope exceeds the runtime transport/cache size limit.")
        verified_launcher = backend.verify_release_envelope(read_json(site / "releases/launcher-stable.json"), launcher_channel.load_launcher_public_key())
        verified_apps = app_catalogue.verify_catalogue(backend, read_json(site / "releases/apps-stable.json"))
        if verified_launcher != launcher_payload or verified_apps != {key: catalogue[key] for key in ("version", "apps", "history")}:
            raise ValueError("Signed output differs from reviewed payloads.")
        if launcher_channel._launcher_projection(backend, verified_launcher) != {"launcher": artifact}:
            raise ValueError("Signed Launcher projection differs from website download.")
    if any((baseline_dir / name).read_bytes() != content for name, content in snapshots.items()):
        raise ValueError("Baseline changed during staging; fetch a fresh baseline and retry.")
    if any(path.read_bytes() != content for path, content in key_snapshots.items()):
        raise ValueError("A publisher verification key changed during staging.")
    if sha(artifact_path) != artifact["sha256"] or sha(site / artifact_relative) != artifact["sha256"]:
        raise ValueError("Launcher artifact changed during staging.")
    rows = []
    for path in sorted(item for item in site.rglob("*") if item.is_file()):
        data = path.read_bytes()
        try:
            data.decode("utf-8")
            encoding = "utf-8"
        except UnicodeDecodeError:
            encoding = "base64"
        rows.append({"path": path.relative_to(site).as_posix(), "local_path": str(path.resolve()),
                     "size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                     "git_sha": hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest(), "encoding": encoding})
    report = {"ok": True, "ready_to_publish": bool(sign), "version": version,
              "catalogue_version": catalogue_version,
              "launcher": artifact, "panel_version": panel["version"],
              "app_versions": {app_id: release["version"] for app_id, release in apps.items()},
              "original_panel_feed_unchanged": True, "publisher_key_unchanged": True,
              "self_test_receipt_sha256": sha(self_test_receipt),
              "self_test_artifact_bound": receipt.get("artifact_sha256") == artifact["sha256"]
                                          and receipt.get("artifact_size") == artifact["size"],
              "baseline_sha256": {name: hashlib.sha256(data).hexdigest() for name, data in snapshots.items()},
              "files": len(rows)}
    write_json(output / "publication-verification.json", report)
    if sign:
        write_json(output / "publish-manifest.json", {"files": rows, "baseline_sha256": report["baseline_sha256"]})
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("baseline-dir", "artifact", "version", "mifont-release", "vector-release", "self-test-receipt", "output"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--catalogue-version", help="Independent app catalogue revision (YYYY.MM.DD.N); defaults to --version. Increase it for app-only releases while reusing exact published Launcher bytes.")
    parser.add_argument("--previous-descriptor", action="append", default=[])
    parser.add_argument("--app-archive", action="append", default=[], metavar="APP_ID=PATH")
    parser.add_argument("--sign", action="store_true", help="Use existing protected publisher key; never initialize it.")
    args = parser.parse_args(argv)
    archives = {}
    for item in args.app_archive:
        app_id, separator, path = item.partition("=")
        if not separator or app_id not in MODULES or app_id in archives or not path:
            parser.error("Each --app-archive must uniquely name mifont or vector-migic.")
        archives[app_id] = path
    try:
        report = prepare(baseline_dir=args.baseline_dir, artifact_path=args.artifact, version=args.version,
                         mifont_release=args.mifont_release, vector_release=args.vector_release,
                         self_test_receipt=args.self_test_receipt, output=args.output,
                         previous_descriptors=args.previous_descriptor, app_archives=archives, sign=args.sign,
                         catalogue_version=args.catalogue_version)
        print(json.dumps(report, ensure_ascii=True))
        return 0
    except (OSError, ValueError, RuntimeError, backend.LauncherError,
            mi_font_app.MiFontAppError, vector_app.VectorAppError) as exc:
        print("Publication preparation stopped: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
