"""Fail-closed validation of an explicitly installed, local model package."""
from __future__ import annotations

import json
from pathlib import Path
import re

MODEL_PROFILE = "ltxv-2b-0.9.6-distilled"
MANIFEST_NAME = "mitom-model.json"
COMPONENTS = ("text_encoder", "transformer", "vae_encoder", "vae_decoder")


class ModelValidationError(ValueError):
    pass


def local_file(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root) or not candidate.is_file():
        raise ModelValidationError(f"Missing or unsafe model file: {relative}")
    if candidate.stat().st_size < 1:
        raise ModelValidationError(f"Empty model file: {relative}")
    return candidate


def read_json(root: Path, relative: str, *, max_bytes=1024 * 1024) -> dict:
    path = local_file(root, relative)
    if path.stat().st_size > max_bytes:
        raise ModelValidationError(f"Oversized model configuration: {relative}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ModelValidationError(f"Invalid model configuration: {relative}") from exc
    if not isinstance(value, dict):
        raise ModelValidationError(f"Expected object model configuration: {relative}")
    return value


def validate_model(model_path: Path) -> dict:
    root = model_path.resolve()
    if not root.is_dir():
        raise ModelValidationError("Model package is not installed")
    manifest = read_json(root, MANIFEST_NAME)
    if manifest.get("schema_version") != 1 or manifest.get("profile") != MODEL_PROFILE:
        raise ModelValidationError("Model package is not the reviewed distilled 2B profile")
    if manifest.get("engine") != "optimum-openvino" or manifest.get("device") != "CPU":
        raise ModelValidationError("Model package is not configured for the CPU engine")
    if not re.fullmatch(r"[a-f0-9]{40}", str(manifest.get("source_revision", ""))):
        raise ModelValidationError("Model package must record its pinned source revision")
    index = read_json(root, "model_index.json")
    if index.get("_class_name") not in ("LTXImageToVideoPipeline", "OVLTXImageToVideoPipeline"):
        raise ModelValidationError("Model package is not an LTX image-to-video pipeline")
    for name in COMPONENTS:
        read_json(root, f"{name}/config.json")
        local_file(root, f"{name}/openvino_model.xml")
        local_file(root, f"{name}/openvino_model.bin")
    read_json(root, "scheduler/scheduler_config.json")
    read_json(root, "tokenizer/tokenizer_config.json")
    # Transformers 5 can save the actual T5 tokenizer as a self-contained fast
    # tokenizer JSON instead of copying the legacy SentencePiece model. Both
    # are local supported formats; configuration alone is not a tokenizer.
    sentencepiece = root / "tokenizer/spiece.model"
    fast_tokenizer = root / "tokenizer/tokenizer.json"
    if sentencepiece.exists():
        path = local_file(root, "tokenizer/spiece.model")
        if path.stat().st_size > 16 * 1024 * 1024:
            raise ModelValidationError("Oversized local SentencePiece tokenizer")
    elif fast_tokenizer.exists():
        read_json(root, "tokenizer/tokenizer.json", max_bytes=16 * 1024 * 1024)
    else:
        raise ModelValidationError("Missing local tokenizer: expected spiece.model or tokenizer.json")
    return {"profile": MODEL_PROFILE, "source_revision": manifest["source_revision"],
            "package_present": True, "inference_verified": False}
