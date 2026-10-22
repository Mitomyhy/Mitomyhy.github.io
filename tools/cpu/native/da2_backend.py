"""Pinned, offline Depth Anything V2 Small inference helpers.

The engine owns model caching. This module only loads the explicit local source
tree/checkpoint and converts DA-V2 relative disparity to visual relief where
near/raised pixels are white. It never calls a model hub or the network.
"""
from __future__ import annotations

from collections.abc import Mapping
import importlib
from pathlib import Path
import sys
import threading

import numpy as np
from PIL import Image


MODEL_ID = "depth-anything/Depth-Anything-V2-Small"
CHECKPOINT_NAME = "depth-anything-v2-vits.pth"
SOURCE_DIR_NAME = "depth-anything-v2-source"
SOURCE_REVISION = "0a7e2b58a7e378c7863bd7486afc659c41f9ef99"
CHECKPOINT_SIZE = 99_218_434
MODEL_CONFIG = {"encoder": "vits", "features": 64,
                "out_channels": [48, 96, 192, 384]}
MIN_DIMENSION = 8
MAX_SOURCE_PIXELS = 40_000_000
MIN_INPUT_SIZE = 128
MAX_INPUT_SIZE = 1024
MAX_INFERENCE_SIDE = MAX_INPUT_SIZE
MAX_INFERENCE_PIXELS = MAX_INPUT_SIZE * MAX_INPUT_SIZE
_IMPORT_LOCK = threading.RLock()


def _bounded_int(value, low: int, high: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, np.integer)):
        raise ValueError(f"{name} must be an integer between {low} and {high}.")
    value = int(value)
    if not low <= value <= high:
        raise ValueError(f"{name} must be between {low} and {high}.")
    return value


def _is_link(path: Path) -> bool:
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())


def _require_local_file(path: Path, parent: Path, description: str) -> Path:
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(parent.resolve(strict=True))
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"Missing or unsafe {description}: {path}") from exc
    if not resolved.is_file() or _is_link(path):
        raise RuntimeError(f"Missing or unsafe {description}: {path}")
    return resolved


def _source_paths(models_dir) -> tuple[Path, Path]:
    models = Path(models_dir).expanduser()
    try:
        models = models.resolve(strict=True)
    except OSError as exc:
        raise RuntimeError("Depth model directory does not exist.") from exc
    if not models.is_dir():
        raise RuntimeError("Depth model path is not a directory.")
    checkpoint = _require_local_file(models / CHECKPOINT_NAME, models, "DA-V2 Small checkpoint")
    if checkpoint.stat().st_size != CHECKPOINT_SIZE:
        raise RuntimeError("DA-V2 Small checkpoint has the wrong size; run model setup again.")
    source = models / SOURCE_DIR_NAME
    if not source.is_dir() or _is_link(source):
        raise RuntimeError("Pinned DA-V2 source tree is missing; run model setup again.")
    try:
        from download_models import MODELS, source_install_valid, verify
        checkpoint_spec = next(item for item in MODELS if item["name"] == CHECKPOINT_NAME)
        source_spec = next(item for item in MODELS if item.get("extract_to") == SOURCE_DIR_NAME)
    except (ImportError, StopIteration) as exc:
        raise RuntimeError("Pinned DA-V2 asset manifest is unavailable.") from exc
    if not verify(checkpoint, checkpoint_spec):
        raise RuntimeError("DA-V2 Small checkpoint failed integrity verification.")
    if not source_install_valid(source, source_spec):
        raise RuntimeError("Pinned DA-V2 source tree failed integrity verification.")
    for relative in ("depth_anything_v2/dpt.py", "depth_anything_v2/util/blocks.py",
                     "depth_anything_v2/util/transform.py"):
        _require_local_file(source.joinpath(*relative.split("/")), source, "DA-V2 source file")
    return checkpoint, source.resolve(strict=True)


def _official_model_class(source: Path):
    """Import only the downloaded source tree, even if another copy is installed."""
    package_root = (source / "depth_anything_v2").resolve(strict=True)
    with _IMPORT_LOCK:
        cached = sys.modules.get("depth_anything_v2.dpt")
        if cached is not None:
            module_file = Path(getattr(cached, "__file__", "")).resolve()
            if module_file.parent != package_root:
                raise RuntimeError("A different Depth Anything V2 source tree is already loaded.")
            module = cached
        else:
            sys.path.insert(0, str(source))
            previous_bytecode_setting = sys.dont_write_bytecode
            sys.dont_write_bytecode = True
            try:
                importlib.invalidate_caches()
                module = importlib.import_module("depth_anything_v2.dpt")
            except Exception as exc:
                raise RuntimeError("Pinned DA-V2 source could not be imported.") from exc
            finally:
                sys.dont_write_bytecode = previous_bytecode_setting
                try:
                    sys.path.remove(str(source))
                except ValueError:
                    pass
            module_file = Path(getattr(module, "__file__", "")).resolve()
            if module_file.parent != package_root:
                raise RuntimeError("DA-V2 import did not resolve to the pinned source tree.")
    model_class = getattr(module, "DepthAnythingV2", None)
    if model_class is None:
        raise RuntimeError("Pinned DA-V2 source does not expose DepthAnythingV2.")
    return model_class


def load_model(models_dir, torch_module):
    """Load the pinned 24.8M DA-V2 Small model on CPU from explicit local paths."""
    if torch_module is None or not hasattr(torch_module, "load"):
        raise RuntimeError("A loaded PyTorch module is required for DA-V2.")
    checkpoint, source = _source_paths(models_dir)
    model_class = _official_model_class(source)
    model = model_class(**MODEL_CONFIG)
    try:
        state = torch_module.load(str(checkpoint), map_location="cpu", weights_only=True)
    except TypeError as exc:
        raise RuntimeError("DA-V2 requires PyTorch with safe weights_only checkpoint loading.") from exc
    if not isinstance(state, Mapping) or not state or not all(isinstance(key, str) for key in state):
        raise RuntimeError("DA-V2 checkpoint is not a state dictionary.")
    try:
        model.load_state_dict(state, strict=True)
        model = model.to("cpu").eval()
    except Exception as exc:
        raise RuntimeError("DA-V2 Small checkpoint does not match the pinned source/configuration.") from exc
    return model


def _composite_neutral(rgba: np.ndarray, background: int) -> np.ndarray:
    """Composite an already bounded straight-alpha RGBA array deterministically."""
    alpha = rgba[..., 3].astype(np.uint32)
    color = rgba[..., :3].astype(np.uint32)
    # Integer math gives deterministic round-to-nearest compositing and exact
    # background gray in fully transparent pixels without overflowing uint16.
    return ((color * alpha[..., None]
             + background * (255 - alpha[..., None]) + 127) // 255).astype(np.uint8)


def prepare_rgba(rgba_uint8, max_side: int = MAX_INPUT_SIZE, background: int = 127):
    """Bound RGBA first, then composite it over neutral gray without large uint32 copies."""
    max_side = _bounded_int(max_side, MIN_INPUT_SIZE, MAX_INPUT_SIZE, "max_side")
    background = _bounded_int(background, 0, 255, "background")
    rgba = np.asarray(rgba_uint8)
    if rgba.dtype != np.uint8 or rgba.ndim != 3 or rgba.shape[2] != 4:
        raise ValueError("DA-V2 input must be an HxWx4 uint8 RGBA array.")
    height, width = rgba.shape[:2]
    if (width < MIN_DIMENSION or height < MIN_DIMENSION
            or width * height > MAX_SOURCE_PIXELS):
        raise ValueError("RGBA dimensions must be at least 8 x 8 and at most 40 megapixels.")
    scale = min(1.0, max_side / max(width, height))
    output_size = (max(1, round(width * scale)), max(1, round(height * scale)))
    if output_size != (width, height):
        source = Image.fromarray(rgba, "RGBA")
        # Resize premultiplied RGBA so invisible RGB cannot bleed into the edge.
        # Compositing over a constant gray after this linear resample preserves
        # the same neutral-alpha meaning while avoiding full-size uint32 arrays.
        bounded = np.asarray(source.convert("RGBa").resize(
            output_size, Image.Resampling.LANCZOS).convert("RGBA"), dtype=np.uint8)
        valid = np.asarray(source.getchannel("A").resize(
            output_size, Image.Resampling.NEAREST), dtype=np.uint8) > 0
    else:
        bounded = rgba
        valid = bounded[..., 3] > 0
    rgb = _composite_neutral(bounded, background)
    metadata = {"source_width": width, "source_height": height,
                "width": output_size[0], "height": output_size[1],
                "background": background, "scale": float(scale)}
    return np.ascontiguousarray(rgb), np.asarray(valid, dtype=bool), metadata


def _normalize_disparity(disparity, valid_mask, low_percentile=2.0, high_percentile=98.0):
    raw = np.asarray(disparity, dtype=np.float32)
    valid = np.asarray(valid_mask, dtype=bool)
    if raw.ndim != 2 or valid.shape != raw.shape:
        raise RuntimeError("DA-V2 returned dimensions that do not match its input.")
    valid = valid & np.isfinite(raw)
    samples = raw[valid]
    if not samples.size:
        raise RuntimeError("DA-V2 returned no finite depth for visible pixels.")
    low, high = (float(value) for value in np.percentile(
        samples.astype(np.float64), [low_percentile, high_percentile]))
    span = high - low
    tolerance = np.finfo(np.float32).eps * max(1.0, abs(low), abs(high)) * 8
    if not np.isfinite(span) or span <= tolerance:
        height = np.where(valid, 0.5, 0).astype(np.float32)
    else:
        # Official relative-depth output is disparity: larger means nearer.
        # Keep it direct so near/raised is white; metric depth would be inverted.
        height = np.clip((raw - low) / span, 0, 1)
        height = np.where(valid, height, 0).astype(np.float32)
    metadata = {"low_percentile": float(low_percentile),
                "high_percentile": float(high_percentile),
                "low_disparity": low, "high_disparity": high,
                "valid_fraction": float(valid.mean())}
    return height, metadata


def infer_height(model, rgb_uint8, valid_mask, input_size: int = MAX_INPUT_SIZE):
    """Infer HxW visual height, normalized with near/raised pixels white."""
    input_size = _bounded_int(input_size, MIN_INPUT_SIZE, MAX_INPUT_SIZE, "input_size")
    rgb = np.asarray(rgb_uint8)
    if rgb.dtype != np.uint8 or rgb.ndim != 3 or rgb.shape[2] != 3:
        raise ValueError("DA-V2 inference input must be an HxWx3 uint8 RGB array.")
    height, width = rgb.shape[:2]
    if (height < 1 or width < 1
            or max(height, width) > MAX_INFERENCE_SIDE
            or height * width > MAX_INFERENCE_PIXELS):
        raise ValueError("DA-V2 inference image is outside the safe size bounds.")
    valid = np.asarray(valid_mask, dtype=bool)
    if valid.shape != (height, width):
        raise ValueError("DA-V2 valid mask must match the RGB image.")
    if not np.any(valid):
        raise ValueError("DA-V2 valid mask contains no visible pixels.")
    if model is None or not callable(getattr(model, "infer_image", None)):
        raise RuntimeError("A loaded DA-V2 model is required.")
    # Official Resize uses lower_bound: sending a narrow rectangle directly can
    # expand its long edge many times. A bounded neutral square keeps the internal
    # tensor near input_size x input_size, then we crop back before normalization.
    side = max(height, width)
    left = (side - width) // 2
    top = (side - height) // 2
    right, bottom = side - width - left, side - height - top
    if left or top or right or bottom:
        square = np.full((side, side, 3), 127, dtype=np.uint8)
        square[top:top + height, left:left + width] = rgb
    else:
        square = rgb
    # The official source expects OpenCV BGR even though this API is explicitly RGB.
    bgr = np.ascontiguousarray(square[..., ::-1])
    raw_square = np.asarray(model.infer_image(bgr, input_size=input_size), dtype=np.float32)
    if raw_square.shape != (side, side):
        raise RuntimeError("DA-V2 returned dimensions that do not match its padded input.")
    raw = raw_square[top:top + height, left:left + width]
    result, normalization = _normalize_disparity(raw, valid)
    metadata = {"model": MODEL_ID, "source_revision": SOURCE_REVISION,
                "input_size": input_size, "width": width, "height": height,
                "raw_output": "relative disparity; larger values are nearer",
                "orientation": "near/raised=white; far=black",
                "model_input": {"width": side, "height": side,
                                "neutral_padding": 127},
                "official_resize": {"method": "lower_bound on neutral square",
                                    "requested_size": input_size,
                                    "internal_side": ((input_size + 13) // 14) * 14},
                "crop": {"left": left, "top": top, "right": right,
                         "bottom": bottom, "width": width, "height": height},
                "normalization": normalization}
    return result, metadata


def infer_rgba(model, rgba_uint8, max_side: int = MAX_INPUT_SIZE,
               input_size: int = MAX_INPUT_SIZE, background: int = 127):
    """Convenience path for callers that have RGBA instead of prepared RGB/mask."""
    rgb, valid, preparation = prepare_rgba(rgba_uint8, max_side=max_side, background=background)
    height, metadata = infer_height(model, rgb, valid, input_size=input_size)
    metadata["preparation"] = preparation
    return height, metadata
