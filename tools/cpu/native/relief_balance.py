"""Compress dominant planar height drift for an artistic ornament relief.

This is a flattening operation, not camera calibration or a depth correction.
It does not use source colours, recognize subjects, or blur local features.
"""
from __future__ import annotations

import math

import numpy as np

from relief_detail import _robust_tilt


def balance_relief_plane(height, valid, *, flatten=.65):
    try:
        values = np.asarray(height, dtype=np.float32)
        mask_source = np.asarray(valid)
    except (TypeError, ValueError):
        raise ValueError("Relief balance requires numeric height and a binary mask.") from None
    if (values.ndim != 2 or mask_source.shape != values.shape
            or not np.isfinite(values).all() or np.any(values < 0)
            or np.any(values > 1)):
        raise ValueError("Relief balance requires finite matching 0..1 height and mask.")
    if mask_source.dtype != np.bool_:
        try:
            numeric_mask = np.asarray(mask_source, dtype=np.float64)
        except (TypeError, ValueError):
            raise ValueError("Relief balance mask must be boolean or binary.") from None
        if (not np.isfinite(numeric_mask).all()
                or np.any((numeric_mask != 0) & (numeric_mask != 1))):
            raise ValueError("Relief balance mask must be boolean or binary.")
    mask = mask_source.astype(bool, copy=False)
    if isinstance(flatten, (bool, np.bool_)):
        raise ValueError("flatten must be between 0 and 1.")
    try:
        flatten = float(flatten)
    except (TypeError, ValueError):
        raise ValueError("flatten must be between 0 and 1.") from None
    if not math.isfinite(flatten) or not 0 <= flatten <= 1:
        raise ValueError("flatten must be between 0 and 1.")
    result = np.where(mask, values, 0).astype(np.float32)
    metadata = {"algorithm": "dominant-plane-artistic-compression-v1",
                "applied": False, "flatten": float(flatten),
                "plane_variance_fraction": 0., "confidence": 0.,
                "strength": 0., "maximum_adjustment": 0.,
                "source_colours_used": False,
                "policy": "artistic broad-height compression; not measured geometry"}
    if flatten == 0 or np.count_nonzero(mask) < 64:
        metadata["fallback_reason"] = "disabled_or_small_support"
        return result, metadata
    variance = float(np.var(values[mask].astype(np.float64)))
    if variance < 1e-8:
        metadata["fallback_reason"] = "constant_foreground"
        return result, metadata
    plane, coefficients = _robust_tilt(values, mask)
    residual_variance = float(np.var((values - plane)[mask].astype(np.float64)))
    explained = float(np.clip(1 - residual_variance / variance, 0, 1))
    ramp = float(np.clip((explained - .35) / .30, 0, 1))
    confidence = ramp * ramp * (3 - 2 * ramp)
    metadata.update(plane_variance_fraction=explained, confidence=confidence,
                    plane_coefficients_xy=coefficients)
    if confidence <= 1e-7:
        metadata["fallback_reason"] = "no_dominant_planar_drift"
        return result, metadata
    strength = min(.90, .85 * flatten / .65) * confidence
    delta = -strength * plane
    # Apply one global safety multiplier, not pointwise clipping: this keeps
    # the correction planar and cannot flatten individual white/gray details.
    selected = delta[mask].astype(np.float64)
    source = values[mask].astype(np.float64)
    lower = min(.02, float(source.min()))
    upper = max(.98, float(source.max()))
    safety = 1.
    positive, negative = selected > 1e-9, selected < -1e-9
    if np.any(positive):
        safety = min(safety, float(np.min((upper - source[positive]) / selected[positive])))
    if np.any(negative):
        safety = min(safety, float(np.min((lower - source[negative]) / selected[negative])))
    maximum = float(np.max(np.abs(selected)))
    if maximum > .35:
        safety = min(safety, .35 / maximum)
    safety = max(0., safety)
    adjustment = np.float32(safety) * delta
    result = np.where(mask, values + adjustment, 0).astype(np.float32)
    # Round-off protection only; the safety multiplier already bounds values.
    np.clip(result, 0, 1, out=result)
    metadata.update(applied=bool(safety > 0), strength=float(strength * safety),
                    maximum_adjustment=float(np.max(np.abs(adjustment[mask]))))
    return result, metadata
