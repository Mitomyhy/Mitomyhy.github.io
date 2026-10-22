"""Normal-guided bas-relief, using geometry only (never image luminance).

The MoGe normal is expressed in the OpenCV camera frame.  If P=z*r is a
perspective surface, n.dP/du=0 gives d(-log(z))/du = nx / (fx * dot(n,r)).
Consequently either consistent orientation of the normal gives the same
height gradient; no guessed sign or RGB edge sharpening is involved.
"""
from __future__ import annotations

import math

import numpy as np
from scipy.sparse.linalg import LinearOperator, cg

from depth_math import depth_to_height


def _control(value, name):
    if isinstance(value, bool):
        raise ValueError(f"{name} must be between 0 and 1.")
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be between 0 and 1.") from None
    if not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError(f"{name} must be between 0 and 1.")
    return value


def _screened_solve(rhs, wx, wy, screening, maxiter=96, initial=None):
    """Solve (screening I + D.T W D)x=rhs on a masked pixel graph.

    A zero edge weight truly disconnects pixels, including holes and separate
    objects. Unlike filling holes followed by an FFT, this does not couple
    surfaces through transparent areas or impose a black boundary condition.
    Iterations and working resolution are explicitly bounded for CPU use.
    """
    rhs = np.asarray(rhs, dtype=np.float32)
    shape = rhs.shape
    screening = np.float32(screening)
    diagonal = np.full(shape, screening, dtype=np.float32)
    diagonal[:, :-1] += wx
    diagonal[:, 1:] += wx
    diagonal[:-1, :] += wy
    diagonal[1:, :] += wy

    def matvec(flat):
        values = np.asarray(flat, dtype=np.float32).reshape(shape)
        result = screening * values
        dx = wx * (values[:, 1:] - values[:, :-1])
        dy = wy * (values[1:, :] - values[:-1, :])
        result[:, :-1] -= dx
        result[:, 1:] += dx
        result[:-1, :] -= dy
        result[1:, :] += dy
        return result.ravel()

    count = 0

    def counted(_):
        nonlocal count
        count += 1

    magnitude = float(np.linalg.norm(rhs.ravel()))
    if magnitude < 1e-10:
        return np.zeros(shape, dtype=np.float32), {
            "iterations": 0, "converged": True, "relative_residual": 0.0}
    operator = LinearOperator((rhs.size, rhs.size), matvec=matvec, dtype=np.float32)
    preconditioner = LinearOperator(
        operator.shape, matvec=lambda v: v / diagonal.ravel(), dtype=np.float32)
    solution, info = cg(operator, rhs.ravel(), M=preconditioner, rtol=5e-4,
                        x0=None if initial is None else np.asarray(initial, dtype=np.float32).ravel(),
                        atol=1e-9, maxiter=maxiter, callback=counted)
    residual = float(np.linalg.norm(matvec(solution) - rhs.ravel())) / magnitude
    if info < 0 or not np.all(np.isfinite(solution)):
        raise RuntimeError("Normal-guided height integration failed numerically.")
    return solution.reshape(shape), {"iterations": count, "converged": info == 0,
                                     "relative_residual": residual}


def _divergence(dx, dy, shape):
    result = np.zeros(shape, dtype=np.float32)
    result[:, :-1] -= dx
    result[:, 1:] += dx
    result[:-1, :] -= dy
    result[1:, :] += dy
    return result


def _pixel_intrinsics(intrinsics, width, height):
    try:
        matrix = np.asarray(intrinsics, dtype=np.float64)
    except (ValueError, TypeError):
        return None, "missing_or_invalid_intrinsics"
    if (matrix.shape != (3, 3) or not np.isfinite(matrix).all()
            or matrix[0, 0] <= 0 or matrix[1, 1] <= 0
            or abs(matrix[2, 2] - 1) > 1e-4
            or abs(matrix[0, 1]) > 1e-5 or abs(matrix[1, 0]) > 1e-5
            or np.max(np.abs(matrix[2, :2])) > 1e-5):
        return None, "missing_or_invalid_intrinsics"
    matrix = matrix.copy()
    # MoGe returns focal lengths/principal points in image-normalized units.
    # Pixel K is also accepted for synthetic calibration and future callers.
    normalized = (matrix[0, 0] < 10 and matrix[1, 1] < 10
                  and abs(matrix[0, 2]) <= 2 and abs(matrix[1, 2]) <= 2)
    if normalized:
        matrix[0, :] *= width
        matrix[1, :] *= height
    return matrix, "normalized" if normalized else "pixels"


def _normal_gradients(normal, valid, intrinsics):
    height, width = valid.shape
    if normal is None:
        return None, {"fallback_reason": "normal_map_missing"}
    try:
        normal = np.asarray(normal, dtype=np.float32)
    except (TypeError, ValueError):
        return None, {"fallback_reason": "normal_map_invalid"}
    if normal.shape != (height, width, 3):
        return None, {"fallback_reason": "normal_map_shape_mismatch"}
    matrix, units = _pixel_intrinsics(intrinsics, width, height)
    if matrix is None:
        return None, {"fallback_reason": units}
    length = np.linalg.norm(normal, axis=2)
    finite = np.all(np.isfinite(normal), axis=2) & (length > 1e-5)
    normal = np.divide(normal, length[..., None], out=np.zeros_like(normal),
                       where=finite[..., None])
    x = ((np.arange(width, dtype=np.float32) + .5 - matrix[0, 2])
         / matrix[0, 0])[None, :]
    y = ((np.arange(height, dtype=np.float32) + .5 - matrix[1, 2])
         / matrix[1, 1])[:, None]
    denominator = normal[..., 0] * x + normal[..., 1] * y + normal[..., 2]
    incidence = np.abs(denominator) / np.sqrt(1 + x * x + y * y)
    # Grazing directions have unbounded perspective slopes and are not valid
    # evidence of a sharp ridge. Revert locally to the estimated depth there.
    confidence = np.clip((incidence - .15) / .20, 0, 1).astype(np.float32)
    reliable = valid & finite & np.isfinite(denominator) & (incidence > .15)
    confidence[~reliable] = 0
    usable = int(np.count_nonzero(reliable))
    fraction = usable / max(1, int(np.count_nonzero(valid)))
    metadata = {"normal_valid_fraction": float(fraction), "intrinsics_units": units,
                "normal_convention": "OpenCV camera; invariant to normal sign"}
    if usable < 4 or fraction < .01:
        metadata["fallback_reason"] = "no_reliable_non_grazing_normals"
        return None, metadata
    gx = np.divide(normal[..., 0], matrix[0, 0] * denominator,
                   out=np.zeros_like(denominator), where=reliable)
    gy = np.divide(normal[..., 1], matrix[1, 1] * denominator,
                   out=np.zeros_like(denominator), where=reliable)
    return (gx.astype(np.float32), gy.astype(np.float32), confidence), metadata


def _robust_tilt(base, valid):
    """Fit only the depth anchor, never the newly added normal detail."""
    height, width = base.shape
    ys, xs = np.nonzero(valid)
    if xs.size < 9:
        return np.zeros_like(base), [0.0, 0.0]
    stride = max(1, math.ceil(xs.size / 16000))
    xs, ys = xs[::stride], ys[::stride]
    x = (xs + .5) / width - .5
    y = (ys + .5) / height - .5
    design = np.column_stack((x, y, np.ones_like(x)))
    target = base[ys, xs].astype(np.float64)
    weights = np.ones_like(target)
    coefficients = np.zeros(3)
    for _ in range(4):
        root = np.sqrt(weights)
        coefficients = np.linalg.lstsq(design * root[:, None], target * root,
                                       rcond=None)[0]
        error = target - design @ coefficients
        sigma = max(1e-7, float(np.median(np.abs(error - np.median(error)))) * 1.4826)
        weights = np.minimum(1, 1.5 * sigma / np.maximum(np.abs(error), 1e-8))
    xx = ((np.arange(width, dtype=np.float32) + .5) / width - .5)[None, :]
    yy = ((np.arange(height, dtype=np.float32) + .5) / height - .5)[:, None]
    tilt = (coefficients[0] * xx + coefficients[1] * yy).astype(np.float32)
    tilt -= np.mean(tilt[valid])
    return np.where(valid, tilt, 0), [float(coefficients[0]), float(coefficients[1])]


def enhance_relief(raw_depth, raw_normal, valid, intrinsics, mode="ornament",
                   detail=.65, flatten=.65, local_correction=None,
                   surface_finish="legacy", smoothing=.25):
    """Return (float32 visual height in [0, 1], JSON-safe method metadata).

    ``depth`` mode is the unchanged, linear, near-white MoGe depth display.
    ``ornament`` integrates reliable normal gradients into logarithmic depth,
    preserving depth as a screened low-frequency anchor. ``flatten`` compresses
    the broad shape of the complete integrated surface by at most 98%, without
    fitting or removing a plane from an upright ornament. ``detail`` blends only
    the normal-derived correction. Legacy finishing uses a monotonic soft curve;
    balanced finishing uses a spatial base/detail split with bounded detail
    retention and no hard foreground clipping. No RGB argument or measured units.

    Input arrays must already be at inference resolution, at most 2048 pixels
    on their long edge. Missing/unusable normals explicitly fall back to depth
    geometry; malformed depth fails closed. Values are relative, not mm.
    """
    detail, flatten = _control(detail, "detail"), _control(flatten, "flatten")
    smoothing = _control(smoothing, "smoothing")
    if surface_finish not in ("legacy", "balanced"):
        raise ValueError("Surface finish must be legacy or balanced.")
    if mode not in ("ornament", "depth"):
        raise ValueError("Relief mode must be ornament or depth.")
    depth = np.asarray(raw_depth, dtype=np.float32)
    valid = np.asarray(valid, dtype=bool)
    if depth.ndim != 2 or depth.shape != valid.shape or min(depth.shape) < 2:
        raise ValueError("Depth and validity need matching 2D shapes of at least 2 by 2.")
    if max(depth.shape) > 2048:
        raise ValueError("Relief processing is bounded to 2048 pixels per edge.")
    original, valid, limits = depth_to_height(depth, valid)
    metadata = {"algorithm": "masked-screened-poisson-relief-v2", "version": 2, "mode": mode,
                "detail": detail, "flatten": flatten, "normal_used": False,
                "depth_limits": limits, "units": "relative visual height; not millimetres",
                "detail_source": "surface normal geometry; no RGB/luminance"}
    if mode == "depth":
        metadata["algorithm"] = "linear-depth-percentiles"
        metadata["fallback_reason"] = "depth_mode_selected"
        return original, metadata

    base = np.zeros_like(depth)
    base[valid] = -np.log(depth[valid])
    base[valid] -= np.median(base[valid])
    correction = np.zeros_like(base)
    gradients, normal_metadata = _normal_gradients(raw_normal, valid, intrinsics)
    metadata.update(normal_metadata)
    if gradients is not None and detail:
        gx, gy, confidence = gradients
        wx = np.minimum(confidence[:, 1:], confidence[:, :-1])
        wy = np.minimum(confidence[1:, :], confidence[:-1, :])
        dx = wx * ((gx[:, 1:] + gx[:, :-1]) * .5 - np.diff(base, axis=1))
        dy = wy * ((gy[1:, :] + gy[:-1, :]) * .5 - np.diff(base, axis=0))
        # Native HQ keeps the same spatial band as the former 1536 pipeline.
        # Higher pixel counts must not accidentally change the broad relief.
        native_scale = max(1.0, max(depth.shape) / 1536)
        length = max(6.0, min(48.0 * native_scale, max(depth.shape) * .035))
        correction, solver = _screened_solve(
            _divergence(dx, dy, base.shape), wx, wy, 1 / length ** 2, maxiter=384)
        correction[~valid] = 0
        acceptable = solver["relative_residual"] <= .05
        if not acceptable:
            correction.fill(0)
            metadata["fallback_reason"] = "normal_solver_residual_too_high"
        metadata.update(normal_used=acceptable, normal_solver=solver,
                        normal_screen_length_px=length)
    elif not detail:
        metadata["fallback_reason"] = "normal_detail_disabled"

    # Do not infer a supporting plane from an upright ornament. Its upper loop
    # and lower banner can be at different real camera depths; treating that as
    # camera tilt previously raised the loop and pushed the door below black.
    refined = base + detail * correction
    # Independently inferred overlapping crops provide geometry, not sharpened
    # RGB. Merge in log-depth BEFORE normalization; never edit the prior PNG.
    if local_correction is not None:
        local = np.asarray(local_correction, dtype=np.float32)
        if local.shape != base.shape or not np.isfinite(local).all():
            raise ValueError("Local correction must be finite and match global depth.")
        refined += detail * np.where(valid, local, 0)
        metadata.update(local_detail_used=True, local_detail_weight=detail,
                        local_detail_units="negative log depth, before relief compression")
    metadata.update(global_tilt_removed=False, removed_tilt_log_depth_xy=[0.0, 0.0])
    if flatten:
        wx = (valid[:, 1:] & valid[:, :-1]).astype(np.float32)
        wy = (valid[1:, :] & valid[:-1, :]).astype(np.float32)
        native_scale = max(1.0, max(depth.shape) / 1536)
        length = max(4.0, min(24.0 * native_scale, max(depth.shape) * .020))
        screening = 1 / length ** 2
        smooth, solver = _screened_solve(screening * refined, wx, wy, screening,
                                       maxiter=384, initial=refined)
        # The slider is strength, not a literal amount of removed height. This
        # monotonic response gives useful ornament compression at saved settings
        # (65% -> 92.1%, 95% -> 97.6%) while retaining a nonzero macro anchor.
        compression = 1 - .02 ** flatten
        if solver["relative_residual"] > .05:
            # Do not turn a bounded, insufficient solve into invented relief.
            # Keep the integrated surface; the normal detail is independently
            # useful and coarse compression is skipped explicitly.
            smooth = np.zeros_like(refined)
            compression = 0
            metadata["coarse_fallback_reason"] = "coarse_solver_residual_too_high"
        smooth -= np.mean(smooth[valid])
        refined -= np.float32(compression) * smooth
        metadata.update(coarse_shape_compression=compression, coarse_solver=solver,
                        coarse_source="complete normal-integrated log depth",
                        coarse_screen_length_px=length)
    else:
        metadata["coarse_shape_compression"] = 0.0
    if surface_finish == "balanced":
        from relief_finish import finish_relief
        result, finish_metadata = finish_relief(refined, valid, smoothing=smoothing)
        metadata.update(finish_metadata)
        metadata.update(algorithm="normal-relief-balanced-surface-v3", version=3,
                        surface_finish=surface_finish)
        return result, metadata
    low, high = np.percentile(refined[valid], [1, 99])
    tone_low, tone_high = float(low), float(high)
    tone_scale_basis = "percentiles_1_99"
    if tone_high - tone_low <= 1e-6:
        # A tiny raised glyph can occupy less than 1% of otherwise flat art.
        # Equal percentiles are not proof of a flat surface: retain sparse
        # geometry instead of replacing the entire foreground by a constant.
        tone_low, tone_high = float(np.min(refined[valid])), float(np.max(refined[valid]))
        tone_scale_basis = "full_range_for_sparse_relief"
    result = np.zeros_like(base)
    if tone_high - tone_low <= 1e-6:
        result[valid] = .5
    else:
        center = float(np.median(refined[valid]))
        scale = .20 * (tone_high - tone_low)
        offset = (refined[valid] - center) / scale
        result[valid] = .5 + .46 * offset / (1 + np.abs(offset))
    metadata.update(height_log_range=[float(low), float(high)], height_percentiles=[1, 99],
                    height_tone="monotonic softsign; percentiles set scale, never clip",
                    foreground_tone_bounds=[.04, .96], height_tone_scale_fraction=.20,
                    height_tone_scale_range=[tone_low, tone_high], height_tone_scale_basis=tone_scale_basis)
    return result.astype(np.float32), metadata
