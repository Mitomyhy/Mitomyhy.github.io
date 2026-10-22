"""Conservative, deterministic fusion for a Sculpt-style relief preview.

The caller supplies two already inferred, near-white height fields:

``global_relief``
    The normal-enhanced global relief.  This is the authoritative shape and
    depth-order anchor.

``da2_relief``
    A robustly normalized relative Depth-Anything-style estimate. It contributes
    only a bounded, low-frequency correction in the shared 0..1 visual-height
    range. It never replaces the global relief.

The final tone preset uses fixed output anchors measured from a reference
style.  The reference image is not loaded, sampled, or otherwise required at
runtime.  A softly limited post-tone residual from the existing global
geometry compensates a little for later resize softness. No RGB/luminance
detail is used: evaluation did not show enough support for a safe generic
grayscale bump map.

This module intentionally performs no I/O, model loading, or network access.
"""
from __future__ import annotations

import math

import numpy as np
from scipy.interpolate import PchipInterpolator


ALGORITHM = "sculpt-conservative-coarse-fusion"
VERSION = 7
MAX_PIXELS = 40_000_000

# Fixed style measurements.  These are output values, not a runtime reference.
TONE_PERCENTILES = (1.0, 50.0, 99.0)
TONE_ANCHORS = (0.169, 0.569, 0.878)
TONE_TAILS = (0.04, 0.97)

_EPSILON = 1e-7


def _control(value, name, low=0.0, high=1.0):
    if isinstance(value, (bool, np.bool_)):
        raise ValueError(f"{name} must be between {low} and {high}.")
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be between {low} and {high}.") from None
    if not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{name} must be between {low} and {high}.")
    return value


def _inputs(global_relief, da2_relief, valid):
    try:
        global_height = np.asarray(global_relief, dtype=np.float32)
        da2_height = np.asarray(da2_relief, dtype=np.float32)
    except (TypeError, ValueError):
        raise ValueError("Global and DA2 relief must be numeric 2D arrays.") from None
    mask_source = np.asarray(valid)
    if global_height.ndim != 2 or min(global_height.shape, default=0) < 2:
        raise ValueError("Global relief must be a 2D array with at least two pixels per edge.")
    if da2_height.shape != global_height.shape or da2_height.ndim != 2:
        raise ValueError("DA2 relief must have the same 2D shape as global relief.")
    if mask_source.shape != global_height.shape or mask_source.ndim != 2:
        raise ValueError("Validity mask must have the same 2D shape as the relief fields.")
    if global_height.size > MAX_PIXELS:
        raise ValueError(f"Sculpt relief accepts at most {MAX_PIXELS} pixels.")
    if not np.isfinite(global_height).all() or not np.isfinite(da2_height).all():
        raise ValueError("Global and DA2 relief must contain only finite values.")
    if (np.any(global_height < 0) or np.any(global_height > 1)
            or np.any(da2_height < 0) or np.any(da2_height > 1)):
        raise ValueError("Global and DA2 relief values must be between 0 and 1.")
    if mask_source.dtype != np.bool_:
        try:
            numeric_mask = np.asarray(mask_source, dtype=np.float64)
        except (TypeError, ValueError):
            raise ValueError("Validity mask must be boolean or binary.") from None
        if (not np.isfinite(numeric_mask).all()
                or np.any((numeric_mask != 0) & (numeric_mask != 1))):
            raise ValueError("Validity mask must be boolean or binary.")
    return global_height, da2_height, mask_source.astype(bool, copy=True)


def _masked_diffuse(values, valid, steps, *, edge_aware=True):
    """Small graph diffusion with zero connectivity across invalid pixels.

    Each update is a convex combination of a pixel and its valid neighbours,
    so this filter cannot create a new numerical minimum, maximum, or spike.
    """
    result = np.where(valid, values, 0).astype(np.float32, copy=True)
    if steps <= 0 or not np.any(valid):
        return result
    edge_x = valid[:, 1:] & valid[:, :-1]
    edge_y = valid[1:, :] & valid[:-1, :]
    if edge_aware:
        differences = np.concatenate((np.abs(np.diff(result, axis=1))[edge_x],
                                      np.abs(np.diff(result, axis=0))[edge_y]))
        positive = differences[differences > 0]
        threshold = max(.01, 4 * float(np.percentile(positive, 70))) if positive.size else .01
        original_dx, original_dy = np.diff(result, axis=1), np.diff(result, axis=0)
        weight_x = edge_x / (1 + np.square(original_dx / threshold))
        weight_y = edge_y / (1 + np.square(original_dy / threshold))
    else:
        # DA2 is guidance for the coarse band only.  Treating a one-pixel
        # outlier as a protected depth discontinuity would preserve exactly the
        # spikes this branch is meant to exclude.
        weight_x = edge_x.astype(np.float32)
        weight_y = edge_y.astype(np.float32)
    amount = np.float32(.20)
    for _ in range(steps):
        flow_x = weight_x * np.diff(result, axis=1)
        flow_y = weight_y * np.diff(result, axis=0)
        update = np.zeros_like(result)
        update[:, :-1] += flow_x
        update[:, 1:] -= flow_x
        update[:-1, :] += flow_y
        update[1:, :] -= flow_y
        result += amount * update
        result[~valid] = 0
    return result


def _deduplicate_anchors(xs, ys):
    """Average target values for coincident source anchors."""
    xs, ys = np.asarray(xs, np.float64), np.asarray(ys, np.float64)
    unique, inverse = np.unique(xs, return_inverse=True)
    totals = np.zeros(unique.size, np.float64)
    counts = np.zeros(unique.size, np.float64)
    np.add.at(totals, inverse, ys)
    np.add.at(counts, inverse, 1)
    targets = totals / counts
    # Both callers supply ordered quantiles, but guard round-off explicitly.
    targets = np.maximum.accumulate(targets)
    return unique, targets


def _align_da2(da2_coarse, global_coarse, valid):
    da2_values = da2_coarse[valid].astype(np.float64)
    global_values = global_coarse[valid].astype(np.float64)
    percentiles = (5.0, 50.0, 95.0)
    da2_quantiles = np.percentile(da2_values, percentiles)
    global_quantiles = np.percentile(global_values, percentiles)
    metadata = {
        "percentiles": list(percentiles),
        "da2_input_quantiles": [float(v) for v in da2_quantiles],
        "global_anchor_quantiles": [float(v) for v in global_quantiles],
        "mapping": "shared robust 0..1 height range; no per-image target fitting",
    }
    if np.array_equal(da2_coarse[valid], global_coarse[valid]):
        metadata["identity_alignment"] = True
        return global_coarse.copy(), float(global_quantiles[-1] - global_quantiles[0]), metadata
    da2_span = float(da2_quantiles[-1] - da2_quantiles[0])
    global_span = float(global_quantiles[-1] - global_quantiles[0])
    if da2_span <= _EPSILON:
        metadata["fallback_reason"] = "constant_or_sparse_da2_coarse"
        return global_coarse.copy(), global_span, metadata
    if global_span <= _EPSILON:
        metadata["fallback_reason"] = "constant_or_sparse_global_anchor"
        return global_coarse.copy(), global_span, metadata
    # infer_height already maps DA2 disparity through robust 2..98 percentiles.
    # Re-fitting those values to the MoGe output percentiles largely erases the
    # complementary semantic shape we loaded DA2 to provide. Keep that shared
    # unit range and bound the actual correction below instead.
    aligned = np.where(valid, da2_coarse, 0).astype(np.float32)
    return aligned, global_span, metadata


def _geometry_detail(global_height, valid, robust_span, detail, flatten, smoothing):
    """Return a small, bounded high-frequency residual from geometry only.

    The residual comes from the authoritative normal-enhanced global relief,
    never from source luminance or the lower-resolution DA2 guide.  Edge-aware
    diffusion supplies the local base.  A one-pixel validity guard prevents an
    unsharp lobe from being created along silhouettes, mask holes, or separate
    components.
    """
    support_steps = 2 + round(3 * detail)
    # A restrained geometry-only boost preserves real normal bands without
    # doubling already-strong fur edges. Optional source detail is a separate,
    # tightly bounded stage and can add image-aligned microtexture afterwards.
    gain = .40 * detail * (1 - .45 * smoothing) * (1 - .15 * flatten)
    limit = max(0.0, robust_span * (.025 + .015 * (1 - flatten)))
    metadata = {
        "source": "normal-enhanced global relief residual; no RGB/luminance",
        "application": "bounded addition after the fixed tone curve",
        "support_steps": int(support_steps),
        "gain": float(gain),
        "soft_limit": float(limit),
        "mask_edge_guard_pixels": 1,
    }
    residual = np.zeros_like(global_height, dtype=np.float32)
    if gain <= _EPSILON or limit <= _EPSILON:
        metadata.update(residual_rms=0.0, maximum_adjustment=0.0,
                        fallback_reason="disabled_or_flat_global")
        return residual, metadata

    # Require every four-connected neighbour used by the diffusion stencil.
    # Image borders are excluded for the same reason as validity boundaries.
    core = np.zeros_like(valid)
    core[1:-1, 1:-1] = (valid[1:-1, 1:-1]
                         & valid[1:-1, :-2] & valid[1:-1, 2:]
                         & valid[:-2, 1:-1] & valid[2:, 1:-1])
    if not np.any(core):
        metadata.update(residual_rms=0.0, maximum_adjustment=0.0,
                        fallback_reason="no_interior_detail_support")
        return residual, metadata

    local_base = _masked_diffuse(global_height, valid, support_steps)
    raw_residual = np.where(core, global_height - local_base, 0).astype(np.float32)
    # tanh is an odd, continuous limiter: unlike hard clipping it cannot add a
    # flat-topped ridge, while bounding even an isolated geometry-model spike.
    limited = np.float32(limit) * np.tanh(raw_residual / np.float32(limit))
    residual = np.where(core, np.float32(gain) * limited, 0).astype(np.float32)
    metadata.update(
        residual_rms=float(np.sqrt(np.mean(np.square(raw_residual[core].astype(np.float64))))),
        maximum_adjustment=float(np.max(np.abs(residual[core]))),
    )
    return residual, metadata


def _tone_sculpt(values, valid):
    result = np.zeros_like(values, dtype=np.float32)
    foreground = values[valid].astype(np.float64)
    quantiles = np.percentile(foreground, TONE_PERCENTILES)
    minimum, maximum = float(np.min(foreground)), float(np.max(foreground))
    metadata = {
        "name": "sculpt-reference-fixed-anchors-c1-v2",
        "percentiles": list(TONE_PERCENTILES),
        "fixed_output_anchors": list(TONE_ANCHORS),
        "tail_output_bounds": list(TONE_TAILS),
        "input_quantiles": [float(v) for v in quantiles],
        "input_range": [minimum, maximum],
        "mapping": "monotonic C1 cubic foreground quantile curve; no spatial blur",
        "runtime_target_used": False,
    }
    if maximum - minimum <= _EPSILON:
        result[valid] = np.float32(TONE_ANCHORS[1])
        metadata["fallback_reason"] = "constant_foreground"
        return result, metadata

    # Give the median priority if a discrete image has coincident percentiles.
    anchors = [(float(quantiles[1]), TONE_ANCHORS[1])]
    if quantiles[0] < quantiles[1] - _EPSILON:
        anchors.insert(0, (float(quantiles[0]), TONE_ANCHORS[0]))
    if quantiles[2] > quantiles[1] + _EPSILON:
        anchors.append((float(quantiles[2]), TONE_ANCHORS[2]))
    if minimum < anchors[0][0] - _EPSILON:
        anchors.insert(0, (minimum, TONE_TAILS[0]))
    if maximum > anchors[-1][0] + _EPSILON:
        anchors.append((maximum, TONE_TAILS[1]))
    source, target = _deduplicate_anchors(*zip(*anchors))
    if source.size < 2:
        # A float32 field can span just over epsilon while both tails remain
        # within epsilon of the median. Preserve the neutral flat fallback
        # instead of constructing an undefined one-anchor cubic.
        result[valid] = np.float32(TONE_ANCHORS[1])
        metadata["fallback_reason"] = "near_constant_quantiles"
        return result, metadata
    # Quantile anchors within epsilon of a real endpoint deliberately omit a
    # separate tail anchor. Extend that endpoint by the sub-epsilon distance
    # so every foreground sample stays in the cubic domain. Do not clip input
    # samples, which would create a tiny flat band at the endpoint.
    source[0], source[-1] = minimum, maximum
    # A straight-segment quantile curve has slope jumps at gray/white anchors.
    # Shape-preserving cubic interpolation joins those slopes continuously,
    # retaining exact anchors and ordering without blurring image features.
    result[valid] = PchipInterpolator(source, target, extrapolate=False)(
        foreground).astype(np.float32)
    return result, metadata


def compose_sculpt_relief(global_relief, da2_relief, valid, *, detail=.65,
                          flatten=.65, smoothing=.25, da2_weight=.20):
    """Fuse a global relief with conservative coarse DA2 guidance.

    ``detail``, ``flatten`` and ``smoothing`` are bounded compatibility
    controls. They change the DA2 correction strength/support and a bounded
    high-frequency residual already present in the global geometry; they never
    select a different base pipeline or derive height from source brightness.

    Returns ``(float32_height, json_safe_metadata)``.  Invalid/background
    pixels are exactly zero.
    """
    detail = _control(detail, "detail")
    flatten = _control(flatten, "flatten")
    smoothing = _control(smoothing, "smoothing")
    da2_weight = _control(da2_weight, "da2_weight", 0.0, .30)
    global_height, da2_height, valid = _inputs(global_relief, da2_relief, valid)
    metadata = {
        "algorithm": ALGORITHM,
        "version": VERSION,
        "units": "relative visual relief height; near/raised is white",
        "global_is_authoritative_macro_anchor": True,
        "source_grayscale_detail_used": False,
        "source_grayscale_policy": "disabled; no reliable generic luminance-to-height evidence",
        "controls": {"detail": detail, "flatten": flatten, "smoothing": smoothing,
                     "requested_da2_weight": da2_weight},
    }
    if not np.any(valid):
        metadata.update(fallback_reason="empty_valid_mask", coarse_smoothing_steps=0,
                        da2_coarse_weight=0.0, global_anchor_weight=1.0,
                        correction_clip=0.0, maximum_pre_tone_global_deviation=0.0,
                        maximum_post_tone_detail_adjustment=0.0,
                        geometry_detail={
                            "source": "normal-enhanced global relief residual; no RGB/luminance",
                            "application": "bounded addition after the fixed tone curve",
                            "support_steps": 0, "gain": 0.0, "soft_limit": 0.0,
                            "mask_edge_guard_pixels": 1, "residual_rms": 0.0,
                            "maximum_adjustment": 0.0,
                            "fallback_reason": "empty_valid_mask"},
                        tone_preset={"name": "sculpt-reference-fixed-anchors-c1-v2",
                                     "percentiles": list(TONE_PERCENTILES),
                                     "fixed_output_anchors": list(TONE_ANCHORS),
                                     "runtime_target_used": False,
                                     "fallback_reason": "empty_valid_mask"})
        return np.zeros(global_height.shape, np.float32), metadata

    # Always retain a small anti-spike support even when the smoothing slider
    # is zero.  The slider only enlarges that support; it cannot turn filtering
    # into sharpening.
    smoothing_steps = 4 + round(12 * smoothing)
    global_coarse = _masked_diffuse(global_height, valid, smoothing_steps)
    identical_guidance = np.array_equal(global_height[valid], da2_height[valid])
    da2_coarse = (global_coarse.copy() if identical_guidance else
                  _masked_diffuse(da2_height, valid, smoothing_steps, edge_aware=False))
    aligned_da2, robust_span, alignment = _align_da2(da2_coarse, global_coarse, valid)

    # Default controls produce about 0.2.  Across every legal slider value the
    # DA2 branch remains a minority contributor (0.1275 through 0.25).
    effective_weight = min(.25, da2_weight * (.75 + .5 * detail) * (1 - .15 * flatten))
    # Backend percentile normalization removed the DA2 tails and the graph pass
    # removed pixel spikes. This last guard is deliberately broad enough
    # that the normal case remains a real ~.2 coarse blend, while a conflicting
    # estimate still cannot move the global field by more than about .17 of its
    # robust span even at the strongest legal controls.
    clip_fraction = .80 - .20 * flatten
    correction_clip = max(0.0, robust_span * clip_fraction)
    correction = np.where(valid, aligned_da2 - global_coarse, 0).astype(np.float32)
    if correction_clip <= _EPSILON or effective_weight == 0:
        correction.fill(0)
    else:
        np.clip(correction, -correction_clip, correction_clip, out=correction)
        correction = _masked_diffuse(correction, valid, 2 + round(4 * smoothing), edge_aware=False)
        np.clip(correction, -correction_clip, correction_clip, out=correction)

    blended = np.where(valid, global_height + np.float32(effective_weight) * correction, 0)
    blended = np.clip(blended, 0, 1).astype(np.float32)
    maximum_deviation = float(np.max(np.abs(blended[valid] - global_height[valid])))
    result, tone_metadata = _tone_sculpt(blended, valid)
    geometry_detail, detail_metadata = _geometry_detail(
        global_height, valid, robust_span, detail, flatten, smoothing)
    # Apply after tone fitting.  Otherwise a tiny new local extremum could
    # become the min/max tail anchor and be expanded into a visible halo.
    result = np.where(valid, result + geometry_detail, 0)
    result = np.clip(result, 0, 1).astype(np.float32)
    result[~valid] = 0
    if not np.isfinite(result).all() or result.dtype != np.float32:
        raise RuntimeError("Sculpt relief fusion failed to produce a finite float32 height map.")

    metadata.update(
        coarse_smoothing_steps=int(smoothing_steps),
        da2_coarse_weight=float(effective_weight),
        global_anchor_weight=float(1 - effective_weight),
        correction_clip=float(correction_clip),
        maximum_pre_tone_global_deviation=maximum_deviation,
        maximum_post_tone_detail_adjustment=detail_metadata["maximum_adjustment"],
        correction_rms=float(np.sqrt(np.mean(np.square(correction[valid].astype(np.float64))))),
        geometry_detail=detail_metadata,
        da2_alignment=alignment,
        tone_preset=tone_metadata,
    )
    if "fallback_reason" in alignment:
        metadata["da2_fallback_reason"] = alignment["fallback_reason"]
    return result, metadata


__all__ = ["compose_sculpt_relief", "ALGORITHM", "VERSION",
           "TONE_PERCENTILES", "TONE_ANCHORS"]
