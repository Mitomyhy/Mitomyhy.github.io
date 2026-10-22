"""Array-only orchestration of the original MoGe+DA2 relief algorithms.

Runs in CPython for golden tests and Pyodide with numpy/scipy/pillow/opencv-python.
No model loading, HTTP, host filesystem paths, Photoshop, or arbitrary code API.
Model budget/quality is explicit; running all composition stages is not a claim
that a lower-token browser graph equals the native 14400-token UHD model pass.
"""
from __future__ import annotations

import io
import json
import math

import numpy as np
import cv2
from PIL import Image

# The browser loads postprocess/native into an isolated Python module directory.
import depth_math as dm
from geometry_validity import recover_alpha_edge_geometry, repair_tiny_geometry_holes
from relief_detail import enhance_relief
from sculpt_relief import compose_sculpt_relief
from relief_balance import balance_relief_plane
from relief_transition import smooth_relief_transitions
from fullprint_detail import apply_fullprint_detail
import fullprint_prepare as fp
from da2_backend import _normalize_disparity, prepare_rgba

SCHEMA = 1
PIPELINE = "shared-acrylic-ai-v2"
MAX_PIXELS = 40_000_000


def _size(width, height, name):
    if (isinstance(width, bool) or isinstance(height, bool)
            or not isinstance(width, int) or not isinstance(height, int)
            or min(width, height) < 8 or width * height > MAX_PIXELS):
        raise ValueError(name + " dimensions must be >=8 pixels and <=40 megapixels.")
    return width, height


def _rgba(value):
    a = np.asarray(value)
    if a.dtype != np.uint8 or a.ndim != 3 or a.shape[-1] != 4:
        raise ValueError("Source must be a straight-alpha HxWx4 uint8 array.")
    _size(a.shape[1], a.shape[0], "Source")
    if not np.any(a[..., 3]):
        raise ValueError("Source is completely transparent.")
    return np.ascontiguousarray(a)


def _options(value):
    if value is None:
        value = {}
    allowed = {"detail", "flatten", "smoothing", "source_detail", "invert",
               "fullprint", "product_type", "repair_geometry", "geometry_frame",
               "da2_frame", "model_budget", "dpi"}
    if not isinstance(value, dict) or set(value) - allowed:
        raise ValueError("Unsupported browser postprocess options.")
    result = {name: dm.bounded_number(value.get(name, default), 0, 1, name)
              for name, default in (("detail", .91), ("flatten", .65),
                                    ("smoothing", .17), ("source_detail", 1.))}
    for name, default in (("invert", False), ("repair_geometry", True)):
        v = value.get(name, default)
        if not isinstance(v, bool):
            raise ValueError(name + " must be boolean.")
        result[name] = v
    kind = value.get("product_type", "acrylic")
    if kind not in ("acrylic", "ceramic_circle", "ceramic_heart"):
        raise ValueError("Unknown product type.")
    result["product_type"] = kind
    result["fullprint"] = fp.options(value.get("fullprint"))
    # Original ceramic_product.options(..., shared-acrylic-ai-v2) contract.
    if kind != "acrylic":
        if result["fullprint"]["text_lines"]:
            raise ValueError("Ceramic uses template Type; legacy lasso text controls are unsupported.")
        result["fullprint"]["ceramic_flat_enabled"] = False
    elif "ceramic_flat_enabled" in result["fullprint"]:
        raise ValueError("Flat ceramic backgrounds are not an Acrylic control.")
    for name in ("geometry_frame", "da2_frame"):
        result[name] = value.get(name)
    budget = value.get("model_budget")
    if not isinstance(budget, dict) or set(budget) - {"moge_tokens", "moge_side", "da2_side", "label"}:
        raise ValueError("An explicit model_budget is required; no implicit UHD claim.")
    for name in ("moge_tokens", "moge_side", "da2_side"):
        n = budget.get(name)
        if isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= 16384:
            raise ValueError("Invalid explicit model budget " + name)
    label = budget.get("label", "browser-explicit-budget")
    if not isinstance(label, str) or not 1 <= len(label) <= 200:
        raise ValueError("Invalid model budget label.")
    result["model_budget"] = dict(budget, label=label)
    dpi = value.get("dpi", [300., 300.])
    if (not isinstance(dpi, (list, tuple)) or len(dpi) != 2
            or any(isinstance(n, bool) or not isinstance(n, (int, float))
                   or not math.isfinite(n) or not 1 <= n <= 10000 for n in dpi)):
        raise ValueError("DPI must be two finite values between1 and10000.")
    result["dpi"] = tuple(float(n) for n in dpi)
    return result


def _crop(value, frame, name):
    a = np.asarray(value)
    if frame is None:
        return a, (0, 0, a.shape[1], a.shape[0])
    if (not isinstance(frame, dict) or set(frame) != {"left", "top", "width", "height"}
            or any(isinstance(n, bool) or not isinstance(n, int) for n in frame.values())):
        raise ValueError(name + " must be an integer pixel rectangle.")
    x, y, w, h = (frame[k] for k in ("left", "top", "width", "height"))
    if min(x, y) < 0 or min(w, h) < 8 or x + w > a.shape[1] or y + h > a.shape[0]:
        raise ValueError(name + " is outside the model output.")
    return a[y:y+h, x:x+w], (x, y, w, h)


def crop_intrinsics(intrinsics, original_size, rectangle):
    """Keep camera calibration when cropping an explicitly padded model frame."""
    k = np.asarray(intrinsics, np.float32)
    if k.shape != (3, 3) or not np.isfinite(k).all() or k[0, 0] <= 0 or k[1, 1] <= 0:
        raise ValueError("Need valid normalized3x3 MoGe intrinsics.")
    old_w, old_h = original_size
    x, y, w, h = rectangle
    k = k.copy()
    k[0, :] *= old_w
    k[1, :] *= old_h
    k[0, 2] -= x
    k[1, 2] -= y
    k[0, :] /= w
    k[1, :] /= h
    return k


def _png(array, dpi, mode=None):
    image = Image.fromarray(array) if mode is None else Image.fromarray(array, mode)
    stream = io.BytesIO()
    image.save(stream, format="PNG", dpi=dpi)
    return stream.getvalue()


def process_arrays(rgba, raw_depth, raw_normal, model_valid, intrinsics,
                   da2_disparity, options, *, progress=None):
    """Run the native current-generation orchestration, using inferred arrays.

    Camera depth/normals/mask/intrinsics must already be reconstructed from
    MoGe forward outputs. DA2 input is raw relative disparity, not a display PNG.
    Input arrays are copied where mutated; caller can adjust again immutably.
    """
    settings = _options(options)
    rgba = _rgba(rgba)
    source_h, source_w = rgba.shape[:2]
    alpha = rgba[..., 3].copy()
    raw_depth = np.asarray(raw_depth, np.float32)
    raw_normal = np.asarray(raw_normal, np.float32)
    model_valid = np.asarray(model_valid, bool)
    if (raw_depth.ndim != 2 or max(raw_depth.shape) > 2048
            or min(raw_depth.shape) < 8 or raw_normal.shape != (*raw_depth.shape, 3)
            or model_valid.shape != raw_depth.shape):
        raise ValueError("MoGe geometry tensors have incompatible dimensions.")
    original_geometry_size = (raw_depth.shape[1], raw_depth.shape[0])
    raw_depth, rectangle = _crop(raw_depth, settings["geometry_frame"], "geometry_frame")
    raw_normal, _ = _crop(raw_normal, settings["geometry_frame"], "geometry_frame")
    model_valid, _ = _crop(model_valid, settings["geometry_frame"], "geometry_frame")
    raw_depth, raw_normal, raw_valid = raw_depth.copy(), raw_normal.copy(), model_valid.copy()
    k = crop_intrinsics(intrinsics, original_geometry_size, rectangle)
    small_h, small_w = raw_depth.shape
    alpha_small = np.asarray(Image.fromarray(alpha).resize((small_w, small_h), Image.Resampling.NEAREST))
    report = {"schema": SCHEMA, "generation_pipeline": PIPELINE, "width": source_w, "height": source_h,
              "model_budget": settings["model_budget"], "product_type": settings["product_type"],
              "composition_uses_original_native_modules": True,
              "native_uhd_inference": False,
              "native_uhd_note": "Postprocessing cannot attest the caller's model graph; no UHD claim is made.",
              "geometry_frame": list(rectangle), "stages": []}
    def stage(name):
        report["stages"].append(name)
        if progress is not None:
            progress(name)

    stage("geometry-validity")
    geometry_repair = {"enabled": False, "repaired_pixels": 0}
    if settings["repair_geometry"]:
        raw_depth, raw_normal, raw_valid, edge = recover_alpha_edge_geometry(
            raw_depth, raw_normal, raw_valid, alpha_small)
        raw_depth, raw_normal, raw_valid, holes = repair_tiny_geometry_holes(
            raw_depth, raw_normal, raw_valid, alpha_small)
        geometry_repair = dict(holes, enabled=True, alpha_edge_recovery=edge)
    raw_valid &= np.isfinite(raw_depth) & (raw_depth > 0) & (alpha_small > 0)
    if not raw_valid.any():
        raise ValueError("No valid MoGe geometry remains inside the source alpha.")
    base_height, small_valid, normalization = dm.depth_to_height(raw_depth, raw_valid)
    _, valid = dm.restore_height(base_height, small_valid, (source_w, source_h))
    valid &= alpha > 0
    report.update(geometry_repair=geometry_repair, normalization=normalization)

    stage("da2-normalization-and-valid-aware-restore")
    disparity = np.asarray(da2_disparity, np.float32)
    if disparity.ndim != 2 or min(disparity.shape) < 8 or max(disparity.shape) > 2048:
        raise ValueError("DA2 disparity must be an8..2048 pixel2D map.")
    disparity, da_rect = _crop(disparity, settings["da2_frame"], "da2_frame")
    da_valid = np.asarray(Image.fromarray(alpha).resize(
        (disparity.shape[1], disparity.shape[0]), Image.Resampling.NEAREST)) > 0
    semantic_small, da_normalization = _normalize_disparity(disparity, da_valid)
    semantic, semantic_valid = dm.restore_height(semantic_small, da_valid, (small_w, small_h))
    coverage = float(np.count_nonzero(semantic_valid & raw_valid) / max(1, np.count_nonzero(raw_valid)))
    if coverage < .98:
        raise RuntimeError("DA2 does not cover98% of visible MoGe geometry; no substitute map was made.")
    semantic[~raw_valid] = 0
    if not np.isfinite(semantic).all():
        raise RuntimeError("DA2 returned invalid semantic geometry.")
    report.update(da2_normalization=da_normalization, da2_frame=list(da_rect), semantic_coverage=coverage)

    stage("normal-guided-screened-poisson")
    global_height, processing = enhance_relief(raw_depth, raw_normal, raw_valid, k,
        mode="ornament", detail=settings["detail"], flatten=settings["flatten"],
        surface_finish="balanced", smoothing=settings["smoothing"])
    global_processing = {key: processing[key] for key in
                        ("algorithm", "version", "normal_used", "surface_finish") if key in processing}
    stage("native-sculpt-pchip-multiscale-fusion")
    height, sculpt_processing = compose_sculpt_relief(global_height, semantic, raw_valid,
        detail=settings["detail"], flatten=settings["flatten"], smoothing=settings["smoothing"])
    processing.update(sculpt_processing)
    processing.update(global_base_processing=global_processing,
                      local_detail_used=True, local_profile="sculpt",
                      semantic_model="depth-anything/Depth-Anything-V2-Small")
    stage("native-relief-plane-balance")
    height, balance = balance_relief_plane(height, raw_valid, flatten=settings["flatten"])
    processing["broad_height_balance"] = balance
    refined, _ = dm.restore_height(height, raw_valid, (source_w, source_h))
    stage("native-tangential-transition-smoothing")
    refined, transitions = smooth_relief_transitions(refined, valid, smoothing=settings["smoothing"])
    processing["transition_smoothing"] = transitions
    stage("native-signed-fullprint-source-structure")
    if settings["source_detail"] > 0:
        refined, source_processing = apply_fullprint_detail(refined, rgba[..., :3], valid,
            raw_normal, source_detail=settings["source_detail"], mode="ornament")
        processing.update(source_detail=source_processing,
                          source_grayscale_detail_used=source_processing.get("enabled", False),
                          source_grayscale_policy="Opt-in artistic source-aligned shallow marks, not measured geometry.")
    else:
        processing["source_detail"] = {"source_detail": 0., "enabled": False,
                                       "fallback_reason": "source_detail_disabled"}
    refined = np.where(valid, refined, 0).astype(np.float32)
    stage("native-canonical-alpha-and-scalar-controls")
    canonical, final_valid, alpha_info = fp.canonical_height(refined, valid, alpha)
    baseline = dm.apply_relief(canonical, final_valid, alpha, mask=None,
                              strength=.35, feather=2., invert=settings["invert"])
    stage("native-fullprint-support-and-text-composition")
    result, support, composition = fp.compose(baseline, rgba, settings["fullprint"])
    if result.shape != (source_h, source_w) or not np.isfinite(result).all() or result.min() < 0 or result.max() > 1:
        raise RuntimeError("Invalid final composed heights; no export was made.")
    stage("native-16bit-height-and-alpha-export")
    normal_encoded = dm.encode_normal(raw_normal, raw_valid, (source_w, source_h))
    normal_encoded[~valid] = 0
    depth16 = np.rint(result * 65535).astype(np.uint16)
    preview = np.empty((source_h, source_w, 4), np.uint8)
    preview[..., :3] = np.rint(result[..., None] * 255).astype(np.uint8)
    preview[..., 3] = alpha
    report.update(relief_processing=processing, fullprint_processing=composition,
                  fullprint_alpha=alpha_info, fullprint=settings["fullprint"],
                  normals_describe="Original MoGe geometry, not the final artistically refined height.",
                  warning="Relative visual relief, not a measured scan or manufacturing millimetres.")
    return {"height": np.ascontiguousarray(result, np.float32), "depth16": depth16,
            "alpha": alpha, "normal": normal_encoded, "support": support,
            "baseline": np.ascontiguousarray(baseline, np.float32),
            "depth_png": _png(depth16, settings["dpi"]),
            "preview_png": _png(preview, settings["dpi"], "RGBA"),
            "alpha_png": _png(alpha, settings["dpi"]),
            "normal_png": _png(normal_encoded, settings["dpi"], "RGB"),
            "support_png": _png(support, settings["dpi"]), "rgba": rgba.copy(), "metadata": report}


def process_buffers(config_json, rgba_bytes, depth_bytes, normal_bytes, valid_bytes,
                    intrinsics_bytes, da2_bytes):
    """Bounded byte-buffer facade for the browser worker; no paths/eval strings."""
    if not isinstance(config_json, str) or len(config_json) > 100_000:
        raise ValueError("Need bounded JSON configuration.")
    config = json.loads(config_json)
    expected = {"width", "height", "geometry_width", "geometry_height", "da2_width", "da2_height", "options"}
    if not isinstance(config, dict) or set(config) != expected:
        raise ValueError("Unexpected buffer configuration fields.")
    w, h = _size(config["width"], config["height"], "Source")
    gw, gh = _size(config["geometry_width"], config["geometry_height"], "Geometry")
    dw, dh = _size(config["da2_width"], config["da2_height"], "DA2")
    if max(gw, gh, dw, dh) > 2048:
        raise ValueError("Model tensors are bounded to2048 pixels per edge.")
    def array(value, dtype, shape, name):
        raw = bytes(value)
        expected_bytes = int(np.prod(shape)) * np.dtype(dtype).itemsize
        if len(raw) != expected_bytes:
            raise ValueError(name + " byte length does not match its declared dimensions.")
        return np.frombuffer(raw, dtype=dtype).reshape(shape)
    return process_arrays(array(rgba_bytes, "u1", (h, w, 4), "RGBA"),
        array(depth_bytes, "<f4", (gh, gw), "depth"),
        array(normal_bytes, "<f4", (gh, gw, 3), "normal"),
        array(valid_bytes, "u1", (gh, gw), "valid"),
        array(intrinsics_bytes, "<f4", (3, 3), "intrinsics"),
        array(da2_bytes, "<f4", (dh, dw), "DA2"), config["options"])


def prepare_model_inputs(rgba, geometry_side=490, da2_side=224):
    """Explicit browser fixed-square preparation, with reversible frame metadata.

    Original gray127 alpha compositing and PIL Lanczos are preserved. The fixed
    graph requires neutral letterboxing, unlike native MoGe's dynamic-aspect
    graph; this is recorded, never advertised as the unchanged native input.
    Returned CHW values are RGB0..1; MoGe graph owns ImageNet normalization,
    while DA2 caller must normalize by the official mean/std before its graph.
    """
    rgba = _rgba(rgba)
    for n in (geometry_side, da2_side):
        if isinstance(n, bool) or not isinstance(n, int) or not 126 <= n <= 2048 or n % 14:
            raise ValueError("Fixed model sides must be multiples of14 between126 and2048.")
    h, w = rgba.shape[:2]
    source = Image.fromarray(rgba, "RGBA")
    neutral = Image.new("RGBA", source.size, (127, 127, 127, 255))
    rgb = Image.alpha_composite(neutral, source).convert("RGB")
    # Native source fit never upscales before inference. Its encoder may upscale
    # internally; this fixed-square browser preset makes that model budget clear.
    geom_size = dm.fit_size(w, h, geometry_side)
    geom_rgb = rgb.resize(geom_size, Image.Resampling.LANCZOS)
    geom_left, geom_top = (geometry_side - geom_size[0]) // 2, (geometry_side - geom_size[1]) // 2
    square = Image.new("RGB", (geometry_side, geometry_side), (127, 127, 127))
    square.paste(geom_rgb, (geom_left, geom_top))
    da_rgb, _, da_metadata = prepare_rgba(rgba, max_side=da2_side, background=127)
    da_h, da_w = da_rgb.shape[:2]
    # Graph output is the graph input square, so padding is at this exact side.
    da_left, da_top = (da2_side - da_w) // 2, (da2_side - da_h) // 2
    da_square = np.full((da2_side, da2_side, 3), 127, np.uint8)
    da_square[da_top:da_top+da_h, da_left:da_left+da_w] = da_rgb
    chw = lambda a: np.ascontiguousarray(np.asarray(a, np.float32).transpose(2, 0, 1) / 255)
    return {"moge": chw(square), "da2": chw(da_square),
        "geometry_frame": {"left": geom_left, "top": geom_top, "width": geom_size[0], "height": geom_size[1]},
        "da2_frame": {"left": da_left, "top": da_top, "width": da_w, "height": da_h},
        "preparation": {"source_size": [w, h], "neutral_background": 127,
            "geometry": "Original PIL neutral composite/Lanczos plus explicit fixed-square padding",
            "da2": da_metadata, "native_dynamic_aspect_moge_input": False}}


def _source_from_png(png_bytes):
    raw = bytes(png_bytes)
    if len(raw) < 32 or len(raw) > 96 * 1024 * 1024:
        raise ValueError("PNG input must be between32 bytes and96MiB.")
    with Image.open(io.BytesIO(raw)) as opened:
        if opened.format != "PNG" or getattr(opened, "n_frames", 1) != 1:
            raise ValueError("Use a single-frame PNG.")
        _size(opened.width, opened.height, "PNG")
        # Same integer grayscale handling as active DepthEngine._rgba_png.
        if opened.mode == "I" or opened.mode.startswith("I;16"):
            pixels = np.asarray(opened)
            gray = np.rint(np.clip(pixels.astype(np.float32), 0, 65535) / 257).astype(np.uint8)
            image = Image.fromarray(gray).convert("RGBA")
            transparent = opened.info.get("transparency")
            if isinstance(transparent, int):
                image.putalpha(Image.fromarray(np.where(pixels == transparent, 0, 255).astype(np.uint8)))
        else:
            image = opened.convert("RGBA")
        return _rgba(np.asarray(image))


def prepare_model_buffers(config_json, png_bytes=None, *, source_rgba=None):
    """PNG→original PIL preparation + actual OpenCV native DA2 normalization.

    Returns bytes and one bounded JSON metadata string for the worker bridge.
    This intentionally does not evaluate supplied Python or accept file paths.
    """
    config = json.loads(config_json)
    if (not isinstance(config, dict) or set(config) - {"moge_side", "da2_graph_side", "output_max_side"}
            or not {"moge_side", "da2_graph_side"}.issubset(config)):
        raise ValueError("Expected fixed model-side configuration only.")
    moge_side, da2_graph_side = config["moge_side"], config["da2_graph_side"]
    if moge_side not in (224, 490) or da2_graph_side not in (224, 1036):
        raise ValueError("Only explicitly exported browser graph sizes are accepted.")
    rgba = _source_from_png(png_bytes) if source_rgba is None else _rgba(source_rgba)
    original_h, original_w = rgba.shape[:2]
    output_max_side = config.get("output_max_side", 2048)
    if isinstance(output_max_side, bool) or not isinstance(output_max_side, int) or not 128 <= output_max_side <= 2048:
        raise ValueError("Browser output max side must be an explicit128..2048 pixel budget.")
    bounded_size = dm.fit_size(original_w, original_h, output_max_side)
    if bounded_size != (original_w, original_h):
        rgba = np.asarray(Image.fromarray(rgba, "RGBA").convert("RGBa").resize(
            bounded_size, Image.Resampling.LANCZOS).convert("RGBA"), np.uint8)
        _rgba(rgba)
    h, w = rgba.shape[:2]
    source = Image.fromarray(rgba, "RGBA")
    neutral = Image.new("RGBA", source.size, (127, 127, 127, 255))
    rgb = Image.alpha_composite(neutral, source).convert("RGB")
    geom_size = dm.fit_size(w, h, moge_side)
    geom_left, geom_top = (moge_side-geom_size[0])//2, (moge_side-geom_size[1])//2
    square = Image.new("RGB", (moge_side, moge_side), (127, 127, 127))
    square.paste(rgb.resize(geom_size, Image.Resampling.LANCZOS), (geom_left, geom_top))
    moge = np.ascontiguousarray(np.asarray(square, np.float32).transpose(2, 0, 1)/255)
    # Native prepare_rgba bounds at1024; official lower_bound transformer then
    # expands1024 to the closest multiple-of14, i.e.1036 (not max_side1036).
    max_side = 1024 if da2_graph_side == 1036 else 224
    bounded_rgb, _, da_prep = prepare_rgba(rgba, max_side=max_side, background=127)
    dh, dw = bounded_rgb.shape[:2]
    padded_side = max(dh, dw)
    left, top = (padded_side-dw)//2, (padded_side-dh)//2
    da_square = np.full((padded_side, padded_side, 3), 127, np.uint8)
    da_square[top:top+dh, left:left+dw] = bounded_rgb
    # These float64 promotions are intentional and match original dpt.py /
    # NormalizeImage / PrepareForNet exactly before the final FP32 tensor cast.
    da_resized = cv2.resize(da_square/255.0, (da2_graph_side, da2_graph_side), interpolation=cv2.INTER_CUBIC)
    da_normalized = (da_resized - np.array([.485, .456, .406])) / np.array([.229, .224, .225])
    da2 = np.ascontiguousarray(da_normalized.transpose(2, 0, 1), np.float32)
    info = {"width": w, "height": h, "original_width": original_w, "original_height": original_h,
        "output_max_side": output_max_side, "source_output_resized": bounded_size != (original_w, original_h),
        "source_output_resize": "PIL premultiplied RGBA Lanczos; explicit browser output budget, not unchanged native PSD pixel dimensions",
        "moge_side": moge_side, "da2_graph_side": da2_graph_side,
        "geometry_frame": {"left": geom_left, "top": geom_top, "width": geom_size[0], "height": geom_size[1]},
        "da2_restore": {"side": padded_side, "left": left, "top": top, "width": dw, "height": dh},
        "native_da2_preparation": da_prep,
        "moge_preparation": "PIL gray127 alpha composite and Lanczos plus explicit fixed-square letterbox",
        "native_dynamic_aspect_moge_input": False,
        "da2_tensor_is_already_normalized": True}
    return {"metadata_json": json.dumps(info, allow_nan=False), "rgba_u8": rgba.tobytes(),
            "moge_f32": moge.astype("<f4", copy=False).tobytes(),
            "da2_f32": da2.astype("<f4", copy=False).tobytes()}


def prepare_rgba_buffers(config_json, rgba_bytes):
    """Raw RGBA facade, avoiding an unnecessary PNG encode/decode in workers."""
    config = json.loads(config_json)
    if not isinstance(config, dict) or not {"width", "height"}.issubset(config):
        raise ValueError("Raw preparation needs explicit source dimensions.")
    w, h = _size(config.pop("width"), config.pop("height"), "Source")
    raw = bytes(rgba_bytes)
    if len(raw) != w*h*4:
        raise ValueError("RGBA preparation buffer length mismatch.")
    rgba = np.frombuffer(raw, np.uint8).reshape(h, w, 4)
    return prepare_model_buffers(json.dumps(config), source_rgba=rgba)


def recover_camera_buffers(config_json, points_bytes, normal_bytes, mask_bytes, scale_bytes):
    """MoGe forward heads→camera arrays using the original SciPy LM objective.

    Uses native Torch-nearest64x64 index semantics without importing Torch.
    UV linspace and normal camera output remain FP32. Models are never loaded.
    """
    from scipy.optimize import least_squares
    config = json.loads(config_json)
    if not isinstance(config, dict) or set(config) != {"width", "height"}:
        raise ValueError("Expected camera dimensions only.")
    w, h = _size(config["width"], config["height"], "Camera")
    if max(w, h) > 2048:
        raise ValueError("Camera tensors are bounded to2048 pixels per edge.")
    def read(value, shape, name):
        raw = bytes(value)
        if len(raw) != int(np.prod(shape))*4:
            raise ValueError(name + " buffer length mismatch.")
        a = np.frombuffer(raw, "<f4").reshape(shape)
        if not np.isfinite(a).all():
            raise ValueError(name + " contains NaN/Infinity.")
        return a
    points = read(points_bytes, (h, w, 3), "points")
    normal = read(normal_bytes, (h, w, 3), "normal")
    probability = read(mask_bytes, (h, w), "mask")
    metric_scale = read(scale_bytes, (1,), "scale")[0]
    if np.any((probability < 0) | (probability > 1)) or metric_scale <= 0:
        raise ValueError("Forward mask/scale activation contract is invalid.")
    binary = probability > .5
    aspect = w/h
    sx, sy = aspect/math.sqrt(1+aspect**2), 1/math.sqrt(1+aspect**2)
    u = np.linspace(-sx*(w-1)/w, sx*(w-1)/w, w, dtype=np.float32)
    v = np.linspace(-sy*(h-1)/h, sy*(h-1)/h, h, dtype=np.float32)
    uv = np.stack(np.meshgrid(u, v, indexing="xy"), axis=-1)
    yi = np.floor(np.arange(64)*h/64).astype(np.intp)
    xi = np.floor(np.arange(64)*w/64).astype(np.intp)
    sampled_mask = binary[np.ix_(yi, xi)]
    xyz = points[np.ix_(yi, xi)][sampled_mask]
    sampled_uv = uv[np.ix_(yi, xi)][sampled_mask]
    if len(xyz) < 2:
        focal, shift = np.float32(1), np.float32(0)
        status = {"converged": False, "reason": "native-insufficient-samples-default"}
    else:
        xy, z = xyz[:, :2], xyz[:, 2]
        def residual(shift_value):
            projected = xy / (z+shift_value)[:, None]
            f = (projected*sampled_uv).sum() / np.square(projected).sum()
            return (f*projected-sampled_uv).ravel()
        solution = least_squares(residual, x0=0, ftol=1e-3, method="lm")
        shift = solution.x.squeeze().astype(np.float32)
        projected = xy/(z+shift)[:, None]
        focal = np.float32((projected*sampled_uv).sum()/np.square(projected).sum())
        status = {"converged": bool(solution.success), "status": int(solution.status),
                  "rms_projection_residual": float(np.sqrt(np.mean(residual(solution.x)**2)))}
    if not np.isfinite(focal) or not np.isfinite(shift) or focal <= 0:
        raise RuntimeError("Native SciPy recovery did not find finite positive intrinsics.")
    depth = ((points[..., 2]+shift)*metric_scale).astype(np.float32)
    binary &= (points[..., 2]+shift) > 0
    fx = np.float32(focal/2*math.sqrt(1+aspect**2)/aspect)
    fy = np.float32(focal/2*math.sqrt(1+aspect**2))
    k = np.array([[fx, 0, .5], [0, fy, .5], [0, 0, 1]], np.float32)
    return {"depth_f32": depth.astype("<f4", copy=False).tobytes(),
        "normal_f32": normal.astype("<f4", copy=False).tobytes(),
        "valid_u8": binary.astype(np.uint8).tobytes(),
        "intrinsics_f32": k.astype("<f4", copy=False).tobytes(),
        "metadata_json": json.dumps(dict(status, focal=float(focal), shift=float(shift),
            samples=len(xyz), solver="scipy-least_squares-lm-ftol1e-3-native-objective"), allow_nan=False)}


def restore_da2_buffers(config_json, disparity_bytes):
    """Original DA2 final Torch bilinear align_corners=True math in NumPy."""
    config = json.loads(config_json)
    if not isinstance(config, dict) or set(config) != {"graph_side", "restore"}:
        raise ValueError("Expected fixed DA2 graph side and restore rectangle.")
    graph_side, restore = config["graph_side"], config["restore"]
    if graph_side not in (224, 1036) or not isinstance(restore, dict) or set(restore) != {"side", "left", "top", "width", "height"}:
        raise ValueError("Invalid DA2 restore configuration.")
    if any(isinstance(v, bool) or not isinstance(v, int) for v in restore.values()):
        raise ValueError("DA2 restore dimensions must be integers.")
    side, left, top, w, h = (restore[k] for k in ("side", "left", "top", "width", "height"))
    if not 8 <= side <= 1024 or min(left, top) < 0 or min(w, h) < 8 or left+w > side or top+h > side:
        raise ValueError("DA2 restore rectangle is outside bounded source dimensions.")
    raw = bytes(disparity_bytes)
    if len(raw) != graph_side*graph_side*4:
        raise ValueError("DA2 disparity buffer length mismatch.")
    data = np.frombuffer(raw, "<f4").reshape(graph_side, graph_side)
    if not np.isfinite(data).all():
        raise ValueError("DA2 disparity contains NaN/Infinity.")
    if side == graph_side:
        restored = data
    else:
        # ATen's align-corners scale and indices are float32 for float32 input.
        coordinate = np.arange(side, dtype=np.float32) * np.float32((graph_side-1)/(side-1))
        lower = np.floor(coordinate).astype(np.intp)
        upper = np.minimum(lower+1, graph_side-1)
        fraction = (coordinate-lower).astype(np.float32)
        rows = data[lower[:, None], lower[None, :]]*(1-fraction[None, :]) + data[lower[:, None], upper[None, :]]*fraction[None, :]
        rows2 = data[upper[:, None], lower[None, :]]*(1-fraction[None, :]) + data[upper[:, None], upper[None, :]]*fraction[None, :]
        restored = rows*(1-fraction[:, None])+rows2*fraction[:, None]
    cropped = np.ascontiguousarray(restored[top:top+h, left:left+w], dtype="<f4")
    return {"disparity_f32": cropped.tobytes(), "metadata_json": json.dumps({
        "width": w, "height": h, "align_corners": True,
        "restoration": "Native Torch bilinear coordinate/weight math, NumPy FP32 implementation; see golden tolerance tests"})}


def process_packed_buffers(config_json, rgba_bytes, depth_bytes, normal_bytes, valid_bytes,
                           intrinsics_bytes, da2_bytes):
    result = process_buffers(config_json, rgba_bytes, depth_bytes, normal_bytes, valid_bytes,
                             intrinsics_bytes, da2_bytes)
    packed = {name: result[name] for name in ("depth_png", "preview_png", "alpha_png", "normal_png", "support_png")}
    for array_name, buffer_name, dtype in (("height", "height_f32", "<f4"),
        ("baseline", "baseline_f32", "<f4"), ("depth16", "depth16_u16", "<u2"),
        ("alpha", "alpha_u8", "u1"), ("normal", "normal_rgb_u8", "u1"), ("support", "support_u8", "u1"),
        ("rgba", "rgba_u8", "u1")):
        packed[buffer_name] = np.ascontiguousarray(result[array_name], dtype=dtype).tobytes()
    packed["metadata_json"] = json.dumps(result["metadata"], allow_nan=False)
    return packed
