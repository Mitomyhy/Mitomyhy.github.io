"""Bounded contour relaxation for inferred ornament geometry.

This is applied BEFORE the independent signed fullprint layer. It smooths small
irregularities along equal-height contours rather than blurring across a depth
step. It cannot correct incorrect anatomy or guarantee a manufacturable shape.
No source RGB, reference image, model, or file I/O is used.
"""
from __future__ import annotations

import math

import cv2
import numpy as np


ALGORITHM = "bounded-tangential-geometry-relaxation"
VERSION = 1
MAX_PIXELS = 40_000_000
MAX_ADJUSTMENT = .025
WHOLE_PIXELS = 4_200_000
TILE_SIZE = 1024


def _inputs(height, valid, smoothing):
    if isinstance(smoothing, (bool, np.bool_)):
        raise ValueError("Transition smoothing must be between 0 and 1.")
    try:
        smoothing = float(smoothing)
        values = np.asarray(height, dtype=np.float32)
        mask = np.asarray(valid)
    except (ValueError, TypeError, OverflowError):
        raise ValueError("Transition inputs must be numeric height, binary mask and smoothing.") from None
    if not math.isfinite(smoothing) or not 0 <= smoothing <= 1:
        raise ValueError("Transition smoothing must be between 0 and 1.")
    if values.ndim != 2 or min(values.shape, default=0) < 2 or mask.shape != values.shape:
        raise ValueError("Transition height and validity need matching 2D shapes, at least 2 by 2.")
    if values.size > MAX_PIXELS:
        raise ValueError(f"Transition smoothing accepts at most {MAX_PIXELS} pixels.")
    if mask.dtype != np.bool_:
        try:
            numeric_mask = np.asarray(mask, dtype=np.float64)
        except (ValueError, TypeError):
            raise ValueError("Transition validity must be boolean or binary.") from None
        if not np.isfinite(numeric_mask).all() or np.any((numeric_mask != 0) & (numeric_mask != 1)):
            raise ValueError("Transition validity must be boolean or binary.")
    mask = mask.astype(bool, copy=False)
    if not np.isfinite(values[mask]).all() or np.any(values[mask] < 0) or np.any(values[mask] > 1):
        raise ValueError("Valid transition heights must be finite and between 0 and 1.")
    return np.where(mask, values, 0).astype(np.float32), mask, smoothing


def _relax(values, valid, steps, amount):
    # A fixed direction field keeps the operation local and its tiled support
    # exact. The small Gaussian chooses direction only, not output heights.
    # Invalid values are zeroed before entry and the three-pixel stencil guard
    # fixes silhouettes, holes, image borders and narrow disconnected features.
    guard = cv2.erode(valid.astype(np.uint8), np.ones((7, 7), np.uint8),
                      borderType=cv2.BORDER_CONSTANT, borderValue=0).astype(bool)
    if not np.any(guard):
        return values.copy()
    guide = cv2.GaussianBlur(values, (7, 7), 1., borderType=cv2.BORDER_REPLICATE)
    gx = cv2.Sobel(guide, cv2.CV_32F, 1, 0, ksize=3, scale=1/8,
                   borderType=cv2.BORDER_REPLICATE)
    gy = cv2.Sobel(guide, cv2.CV_32F, 0, 1, ksize=3, scale=1/8,
                   borderType=cv2.BORDER_REPLICATE)
    magnitude = np.hypot(gx, gy)
    usable = magnitude > 1e-7
    tx = np.divide(-gy, magnitude, out=np.zeros_like(values), where=usable)
    ty = np.divide(gx, magnitude, out=np.zeros_like(values), where=usable)
    # OpenCV's linear remap uses a 1/32-pixel table. Quantize the offsets before
    # adding coordinates, so tile-local coordinate magnitudes cannot change a
    # tie at a table boundary through float32 addition round-off.
    tx = np.rint(tx*np.float32(32))/np.float32(32)
    ty = np.rint(ty*np.float32(32))/np.float32(32)
    ys, xs = np.mgrid[:values.shape[0], :values.shape[1]].astype(np.float32)
    positive_x, positive_y = xs+tx, ys+ty
    negative_x, negative_y = xs-tx, ys-ty
    del guide, gx, gy, magnitude, tx, ty, xs, ys
    current = values.copy()
    for _ in range(steps):
        positive = cv2.remap(current, positive_x, positive_y, cv2.INTER_LINEAR,
                             borderMode=cv2.BORDER_REPLICATE)
        negative = cv2.remap(current, negative_x, negative_y, cv2.INTER_LINEAR,
                             borderMode=cv2.BORDER_REPLICATE)
        # Convex, symmetric sampling cannot create a new global extreme.
        candidate = current + np.float32(amount) * ((positive+negative)*np.float32(.5)-current)
        current[guard] = candidate[guard]
    # Continuous shoulder, not a hard clipping plateau. This is a convex blend
    # back toward the input, so it preserves the same extrema guarantee while
    # preventing cumulative rounding from changing macro anatomy by >2.5%.
    result = values + np.float32(MAX_ADJUSTMENT)*np.tanh(
        (current-values)/np.float32(MAX_ADJUSTMENT))
    result[~valid] = 0
    return result.astype(np.float32)


def smooth_relief_transitions(height, valid, *, smoothing=.25):
    """Relax uneven geometry contours, leaving signed source marks to follow.

    Zero is an exact no-op on valid input. The height change is <.025 in the
    relative 0..1 range; this is not a measured millimetre tolerance. Large
    native inputs use independent overlapped tiles with the full dependency
    halo, not resized output or unblended tile seams.
    """
    values, valid, smoothing = _inputs(height, valid, smoothing)
    metadata = dict(algorithm=ALGORITHM, version=VERSION, enabled=False,
                    smoothing=smoothing, source="inferred geometry only; no RGB",
                    placement="before signed fullprint, before alpha multiplication",
                    maximum_allowed_adjustment=MAX_ADJUSTMENT,
                    mask_guard_pixels=3, native_size=list(values.shape[::-1]),
                    direction="fixed tangent of locally smoothed height contours",
                    steps=0, tiles=0, maximum_adjustment=0., adjustment_rms=0.)
    if not smoothing or not np.any(valid):
        metadata["fallback_reason"] = "disabled" if not smoothing else "empty_valid_mask"
        return values, metadata
    if float(np.ptp(values[valid])) <= 1e-7:
        metadata["fallback_reason"] = "constant_valid_height"
        return values, metadata
    steps = max(4, min(114, round(64*(max(values.shape)/1536)**2)))
    amount = .8*smoothing
    if values.size <= WHOLE_PIXELS and max(values.shape) < 32760:
        result = _relax(values, valid, steps, amount)
        tiles = 1
    else:
        result = np.empty_like(values)
        # Each frozen bilinear advection step reaches at most one pixel in
        # either axis; four extra pixels cover the direction estimate. The
        # extra two keep the fixed local guard outside all output dependencies.
        halo = steps+6
        tiles = 0
        for y in range(0, values.shape[0], TILE_SIZE):
            for x in range(0, values.shape[1], TILE_SIZE):
                y1, x1 = min(y+TILE_SIZE, values.shape[0]), min(x+TILE_SIZE, values.shape[1])
                top, left = max(0, y-halo), max(0, x-halo)
                bottom, right = min(values.shape[0], y1+halo), min(values.shape[1], x1+halo)
                patch = _relax(values[top:bottom,left:right], valid[top:bottom,left:right], steps, amount)
                result[y:y1,x:x1] = patch[y-top:y1-top,x-left:x1-left]
                tiles += 1
    delta = result[valid]-values[valid]
    metadata.update(enabled=True, steps=steps, tiles=tiles,
                    maximum_adjustment=float(np.max(np.abs(delta))),
                    adjustment_rms=float(np.sqrt(np.mean(np.square(delta.astype(np.float64))))))
    return result, metadata


__all__ = ["smooth_relief_transitions", "ALGORITHM", "VERSION", "MAX_ADJUSTMENT"]
