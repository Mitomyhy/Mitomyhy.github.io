"""Conservative, geometry-only finishing for relative ornament heights.

This module does not infer or invent anatomy. It makes existing geometry easier
to inspect: tiny pixel-scale fluctuations can be reduced without sampling across
mask holes, a monotonic scalar curve balances the broad surface, and spatial
detail retention preserves existing fine geometry. The complete finish is not
a globally monotonic metric-depth remap. Neither image luminance nor RGB edges
are accepted as input.
"""
from __future__ import annotations

import math

import numpy as np


def _unit_interval(value, name):
    if isinstance(value, (bool, np.bool_)):
        raise ValueError(f"{name} must be between 0 and 1.")
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be between 0 and 1.") from None
    if not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError(f"{name} must be between 0 and 1.")
    return value


def _inputs(height, valid):
    height = np.asarray(height, dtype=np.float32)
    valid = np.asarray(valid, dtype=bool)
    if height.ndim != 2 or height.shape != valid.shape or min(height.shape) < 2:
        raise ValueError("Height and validity need matching 2D shapes of at least 2 by 2.")
    if max(height.shape) > 2048:
        raise ValueError("Relief finishing is bounded to 2048 pixels per edge.")
    if not np.isfinite(height[valid]).all():
        raise ValueError("Foreground height must be finite.")
    return np.where(valid, height, 0).astype(np.float32), valid


def smooth_geometry(height, valid, strength=.25):
    """Two bounded edge-aware Jacobi steps, never crossing invalid pixels.

    Each update is a convex average of a pixel and its immediate valid
    neighbours. Large geometry jumps are down-weighted; no interpolation through
    holes, external black padding, repeated wide blur or edge sharpening occurs.
    A zero strength returns the original valid geometry exactly.
    """
    strength = _unit_interval(strength, "smoothing")
    values, valid = _inputs(height, valid)
    metadata = {"geometry_smoothing": strength, "geometry_smoothing_steps": 0,
                "geometry_smoothing_method": "masked edge-aware 4-neighbour diffusion"}
    if not strength or not np.any(valid):
        return values, metadata
    edge_x = valid[:, 1:] & valid[:, :-1]
    edge_y = valid[1:, :] & valid[:-1, :]
    differences = np.concatenate((np.abs(np.diff(values, axis=1))[edge_x],
                                  np.abs(np.diff(values, axis=0))[edge_y]))
    positive = differences[differences > 0]
    if not positive.size:
        return values, metadata
    # Use only local geometry differences, not overall brightness or the
    # foreground/background jump. A low cap keeps the support pixel-scale.
    threshold = max(1e-12, 1.5 * float(np.percentile(positive, 50)))
    for _ in range(2):
        dx, dy = np.diff(values, axis=1), np.diff(values, axis=0)
        wx = np.exp(-np.square(dx / threshold)) * edge_x
        wy = np.exp(-np.square(dy / threshold)) * edge_y
        neighbour_sum = np.zeros_like(values)
        weight_sum = np.zeros_like(values)
        neighbour_sum[:, :-1] += wx * values[:, 1:]
        neighbour_sum[:, 1:] += wx * values[:, :-1]
        neighbour_sum[:-1, :] += wy * values[1:, :]
        neighbour_sum[1:, :] += wy * values[:-1, :]
        weight_sum[:, :-1] += wx
        weight_sum[:, 1:] += wx
        weight_sum[:-1, :] += wy
        weight_sum[1:, :] += wy
        # Fixed support denominator: one valid neighbour should not have the
        # same influence as four. Invalid/background pixels never contribute.
        values += np.float32(strength / 4) * (neighbour_sum - weight_sum * values)
        values[~valid] = 0
    metadata.update(geometry_smoothing_steps=2, geometry_smoothing_range_log=threshold)
    return values.astype(np.float32), metadata


def _tone_parameters(values, valid):
    metadata = {"height_tone": "asinh with C1 rational shoulders; no percentile clipping",
                "foreground_tone_bounds": [.02, .90],
                "finish_source": "relative surface geometry only; no RGB/luminance"}
    if not np.any(valid):
        metadata["height_tone_scale_basis"] = "empty_foreground"
        return None, metadata
    foreground = values[valid].astype(np.float64)
    low, high = np.percentile(foreground, [1, 99])
    full_low, full_high = float(np.min(foreground)), float(np.max(foreground))
    full_span = full_high - full_low
    if full_span <= 1e-12:
        metadata.update(height_tone_scale_basis="constant_foreground",
                        height_log_range=[full_low, full_high])
        return None, metadata
    robust_span = float(high - low)
    basis = "percentiles_1_99"
    if robust_span <= 1e-12:
        robust_span = full_span
        basis = "full_range_for_sparse_relief"
    scale = .10 * robust_span
    center = float(np.median(foreground))
    transformed = np.arcsinh((foreground - center) / scale)
    transformed_low, transformed_high = np.percentile(transformed, [.1, 99.9])
    anchor_basis = "percentiles_0.1_99.9"
    if transformed_high - transformed_low <= 1e-9:
        transformed_low, transformed_high = float(np.min(transformed)), float(np.max(transformed))
        anchor_basis = "full_range_for_sparse_relief"
    metadata.update(height_log_range=[full_low, full_high], height_percentiles=[1, 99],
                    height_tone_center_log=center, height_tone_scale_log=scale,
                    height_tone_scale_fraction=.10, height_tone_scale_range=[float(low), float(high)],
                    height_tone_scale_basis=basis, height_tone_anchor_basis=anchor_basis,
                    height_tone_anchor_percentiles=[.1, 99.9], height_tone_anchor_outputs=[.08, .85])
    return {"center": center, "scale": scale, "span": robust_span,
            "low": float(transformed_low), "high": float(transformed_high)}, metadata


def _evaluate_tone(foreground, parameters):
    offset = (np.asarray(foreground, dtype=np.float64) - parameters["center"]) / parameters["scale"]
    factor = .77 / (parameters["high"] - parameters["low"])
    linear = .08 + factor * (np.arcsinh(offset) - parameters["low"])
    tone = linear.copy()
    derivative = factor / parameters["scale"] / np.sqrt(1 + np.square(offset))
    lower, upper = linear < .08, linear > .85
    lo_den = 1 + (.08 - linear[lower]) / .06
    hi_den = 1 + (linear[upper] - .85) / .05
    tone[lower] = .02 + .06 / lo_den
    tone[upper] = .90 - .05 / hi_den
    derivative[lower] /= np.square(lo_den)
    derivative[upper] /= np.square(hi_den)
    return tone, derivative


def scalar_tone(height, valid):
    """Strictly monotonic, non-spatial tone curve; useful for analytic checks."""
    values, valid = _inputs(height, valid)
    parameters, metadata = _tone_parameters(values, valid)
    output = np.zeros_like(values)
    if parameters is None:
        output[valid] = .5
    else:
        output[valid] = _evaluate_tone(values[valid], parameters)[0]
    metadata["height_finish"] = "scalar monotonic tone only"
    return output, metadata


def _masked_base(values, valid):
    """Geometry-only, bounded graph diffusion with fixed edge conductance.

    At 1536px, 96 explicit stable steps have approximately 6px Gaussian support
    inside continuous geometry. True depth jumps resist diffusion; invalid
    pixels and disconnected pieces never exchange height through the graph.
    The base is temporary: its removed fine geometry is added back below.
    """
    dx, dy = np.diff(values, axis=1), np.diff(values, axis=0)
    ex = valid[:, 1:] & valid[:, :-1]
    ey = valid[1:, :] & valid[:-1, :]
    differences = np.concatenate((np.abs(dx[ex]), np.abs(dy[ey])))
    positive = differences[differences > 0]
    if not positive.size:
        return np.where(valid, values, 0).astype(np.float32), {"finish_base_steps": 0}
    threshold = max(1e-12, 4 * float(np.percentile(positive, 70)))
    wx = ex / (1 + np.square(dx / threshold))
    wy = ey / (1 + np.square(dy / threshold))
    # Same physical support at native 2K; the previous <=1536 path is exact.
    steps = max(4, min(171, round(96 * (max(values.shape) / 1536) ** 2)))
    base = values.copy()
    for _ in range(steps):
        flow_x, flow_y = wx * np.diff(base, axis=1), wy * np.diff(base, axis=0)
        change = np.zeros_like(base)
        change[:, :-1] += flow_x
        change[:, 1:] -= flow_x
        change[:-1, :] += flow_y
        change[1:, :] -= flow_y
        base += np.float32(.20) * change
    base[~valid] = 0
    return base, {"finish_base_steps": steps, "finish_base_range_log": threshold,
                  "finish_base_method": "masked edge-aware graph diffusion; no background coupling"}


def finish_relief(height, valid, smoothing=.25):
    """Balance broad tones while retaining the existing fine relief geometry.

    An optional subpixel smoothing pass precedes a masked base/detail split.
    The smooth base receives the calibrated asinh curve; its geometry detail is
    retained with at least the legacy curve's local gain, bounded by that
    curve's maximum slope. A continuous headroom shoulder prevents clipping.

    This is a *spatial* bas-relief finish, not a globally monotonic remapping of
    metric depth. It cannot reconstruct missing anatomy or manufacture detail
    absent from the local AI depth/normal estimate. No RGB image is accepted.
    """
    values, metadata = smooth_geometry(height, valid, strength=smoothing)
    valid = np.asarray(valid, dtype=bool)
    parameters, tone_metadata = _tone_parameters(values, valid)
    metadata.update(tone_metadata)
    metadata.update(height_finish="spatial base/detail tone with bounded legacy detail retention",
                    finish_global_monotonic=False)
    output = np.zeros_like(values)
    if parameters is None:
        output[valid] = .5
        return output, metadata
    base, base_metadata = _masked_base(values, valid)
    metadata.update(base_metadata)
    tone, derivative = _evaluate_tone(base[valid], parameters)
    legacy_scale = .20 * parameters["span"]
    legacy_offset = (base[valid] - parameters["center"]) / legacy_scale
    peak_gain = .46 / legacy_scale
    legacy_derivative = peak_gain / np.square(1 + np.abs(legacy_offset))
    gain = np.minimum(np.maximum(derivative, legacy_derivative), peak_gain)
    detail = gain * (values[valid].astype(np.float64) - base[valid])
    headroom = np.where(detail >= 0, .90 - tone, tone - .02)
    output[valid] = tone + headroom * np.tanh(detail / np.maximum(headroom, 1e-12))
    metadata.update(finish_detail_peak_gain=float(peak_gain),
                    finish_detail_source="existing inferred geometry minus masked base; no RGB",
                    finish_detail_shoulder="continuous tanh headroom; no hard clipping")
    return output.astype(np.float32), metadata
