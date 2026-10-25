"""Two-phase disk budgets for authenticated panel downloads and installation.

The first phase budgets only the allocations that precede a verified manifest:
missing cache objects, a fresh assembled ZIP and the complete expanded stage.
The transactional installer's existing second phase then budgets actual changed
destinations, their backups and atomic staging, before any installed file changes.
An old install receipt is never treated as a list of files that must be copied.
"""
from __future__ import annotations

from pathlib import Path
import shutil

import installer


RESERVE = 128 * 1024 * 1024


def _bytes(backend, value, limit, label):
    if type(value) is not int or not 0 <= value <= limit:
        raise backend.LauncherError("Invalid authenticated " + label + " byte count.")
    return value


def _legacy_caches(backend, cache_base):
    if not cache_base.exists():
        return []
    result = []
    for entry in cache_base.iterdir():
        try:
            backend.version_tuple(entry.name)
        except (ValueError, backend.LauncherError):
            continue
        candidate = installer._plain(entry)
        if candidate.is_dir():
            result.append(candidate)
    return result


def download_preflight(backend, release, cache, base_root=None, include_install=True):
    """Check remaining download/stage allocations without creating any folders.

    Cache credits require the exact signed size and SHA-256. Partial downloads
    receive no credit because their final authenticated digest is not known yet.
    The existing installer performs the exact installation check later, when
    its verified manifest identifies changed files and backup sizes.
    """
    cache = installer._plain(Path(cache))
    cache_base = installer._plain(cache.parent)
    core = release["core"]
    size = _bytes(backend, core["size"], backend.UNPACKED_LIMIT, "core archive")
    expanded = _bytes(backend, core.get("unpacked_size", min(backend.UNPACKED_LIMIT, size * 8)),
                      backend.UNPACKED_LIMIT, "expanded stage")
    if size < 1 or expanded < 1:
        raise backend.LauncherError("The authenticated core archive and stage must not be empty.")
    models, bundles = backend.external_files(release), backend.external_bundles(release)
    if sum(item["size"] for item in models) > expanded:
        raise backend.LauncherError("Authenticated models exceed the expanded stage.")
    default_cache = installer._plain(backend.launcher_data_root() / "Downloads")
    stage = backend.staging_root(None if cache_base == default_cache else cache_base, base_root)
    budgets = {}
    estimates = {"missing_core_parts": 0, "missing_models": 0, "missing_bundles": 0,
                 "assembled_archive": size, "expanded_stage": expanded}

    def add(path, count):
        volume, parent = installer._space_location(path)
        row = budgets.setdefault(volume, {"bytes": 0, "parents": set()})
        row["bytes"] += count
        row["parents"].add(parent)

    # obtain_parts prefers the digest cache, then a matching part at the same
    # index in any managed release cache; duplicate digests are downloaded once.
    legacy = _legacy_caches(backend, cache_base)
    missing = set()
    for index, artifact in enumerate(core["parts"]):
        candidates = [cache_base / "Parts" / (artifact["sha256"] + ".bin")]
        candidates.extend(folder / f"core.part{index + 1:03d}" for folder in legacy)
        if not any(backend.verified_file(candidate, artifact) for candidate in candidates):
            key = (artifact["sha256"], artifact["size"])
            if key not in missing:
                missing.add(key)
                estimates["missing_core_parts"] += artifact["size"]
    add(cache_base / "Parts", estimates["missing_core_parts"])

    for bundle in bundles:
        suffix = ".whl" if bundle["id"] == "torch-cpu" else ".tar.gz"
        path = cache_base / "Bundles" / bundle["id"] / bundle["sha256"] / (bundle["id"] + suffix)
        if not backend.verified_file(path, bundle):
            estimates["missing_bundles"] += bundle["size"]
            add(path, bundle["size"])
    for model in models:
        path = cache_base / "Models" / model["sha256"] / Path(model["path"]).name
        available = backend.verified_file(path, model)
        if not available and include_install:
            available = any(backend.verified_file(candidate, model)
                            for candidate in backend.installed_model_candidates(model["path"], base_root))
        if not available:
            estimates["missing_models"] += model["size"]
            add(path, model["size"])

    # Assembly always writes a new ZIP, even if core.zip already exists. All
    # external members are subsequently copied into the fresh expanded stage.
    add(cache, size)
    add(stage, expanded)
    rows = []
    for volume, row in sorted(budgets.items()):
        required = row["bytes"] + RESERVE
        try:
            available = min(shutil.disk_usage(parent).free for parent in row["parents"])
        except OSError as exc:
            raise backend.LauncherError("Cannot check free space before downloading; no installed files changed.") from exc
        if available < required:
            mib = (required + 1048575) // 1048576
            raise backend.LauncherError("Không đủ dung lượng trống để tải/giải nén: cần tối thiểu "
                + str(mib) + " MiB trên " + volume + ". Chưa thay đổi Panel.")
        rows.append({"volume": volume, "required_bytes": required, "available_bytes": available})
    return {"phase": "download-and-stage", "volumes": rows, "estimated_bytes": estimates,
            "install_check": "verified-changes-before-install" if include_install else "not-requested"}
