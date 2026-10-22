"""Deterministic relief processing; never infer depth from RGB brightness."""
from __future__ import annotations

import math

import numpy as np
from PIL import Image, ImageFilter


def bounded_number(value, low, high, name):
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a number.")
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a number.") from None
    if not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{name} must be between {low} and {high}.")
    return value


def fit_size(width, height, long_side):
    scale = min(1.0, float(long_side) / max(width, height))
    return max(1, round(width * scale)), max(1, round(height * scale))


def depth_to_height(depth, valid, low_percentile=2.0, high_percentile=98.0):
    """Near/raised = white. Percentiles describe visual relief, not physical mm."""
    depth = np.asarray(depth, dtype=np.float32)
    valid = np.asarray(valid, dtype=bool) & np.isfinite(depth) & (depth > 0)
    if depth.ndim != 2 or valid.shape != depth.shape:
        raise ValueError("Depth and validity maps must have matching 2D shapes.")
    values = depth[valid]
    if not values.size:
        raise ValueError("MoGe did not find any valid depth pixels; no map was generated.")
    low, high = np.percentile(values, [low_percentile, high_percentile])
    result = np.zeros_like(depth)
    if high - low <= max(abs(float(high)), 1.0) * 1e-6:
        result[valid] = 0.5
    else:
        result[valid] = np.clip((high - values) / (high - low), 0.0, 1.0)
    return result, valid, {"near_depth_estimate": float(low), "far_depth_estimate": float(high),
                           "percentiles": [low_percentile, high_percentile]}


def resize_float(values, size):
    return np.asarray(Image.fromarray(np.asarray(values, dtype=np.float32)).resize(
        size, Image.Resampling.BILINEAR), dtype=np.float32)


def restore_height(height, valid, size):
    """Interpolate only valid samples, avoiding black holes pulling down edges."""
    if tuple(size) == (height.shape[1], height.shape[0]):
        return height.copy(), valid.copy()
    weights = resize_float(valid.astype(np.float32), size)
    numerator = resize_float(np.where(valid, height, 0), size)
    restored = np.divide(numerator, weights, out=np.zeros_like(numerator), where=weights > 1e-6)
    final_valid = np.asarray(Image.fromarray(valid.astype(np.uint8) * 255).resize(
        size, Image.Resampling.NEAREST)) > 0
    return np.where(final_valid, np.clip(restored, 0, 1), 0).astype(np.float32), final_valid


def encode_normal(normal, valid, size):
    normal = np.asarray(normal, dtype=np.float32)
    if normal.shape != (*valid.shape, 3):
        raise ValueError("MoGe did not return a matching normal map.")
    normal_valid = valid & np.all(np.isfinite(normal), axis=2)
    channels = [resize_float(np.where(normal_valid, normal[..., c], 0), size) for c in range(3)]
    result = np.stack(channels, axis=2)
    length = np.linalg.norm(result, axis=2, keepdims=True)
    result = np.divide(result, length, out=np.zeros_like(result), where=length > 1e-6)
    return np.rint((np.clip(result, -1, 1) + 1) * 127.5).astype(np.uint8)


def apply_relief(base, valid, alpha, mask=None, strength=0.35, feather=2.0, invert=False):
    """Always recompute from immutable base, so repeated adjustments cannot drift."""
    strength = bounded_number(strength, 0, 1, "strength")
    feather = bounded_number(feather, 0, 20, "feather")
    base = np.asarray(base, dtype=np.float32)
    valid = np.asarray(valid, dtype=bool)
    alpha = np.asarray(alpha, dtype=np.uint8)
    if base.ndim != 2 or valid.shape != base.shape or alpha.shape != base.shape:
        raise ValueError("Relief arrays must have matching 2D shapes.")
    result = 1 - base if invert else base.copy()
    if mask is not None:
        mask = np.asarray(mask, dtype=bool)
        if mask.shape != base.shape:
            raise ValueError("Mask shape must match the source image.")
        selected = (mask & valid & (alpha > 0)).astype(np.uint8) * 255
        softened = Image.fromarray(selected)
        if feather:
            softened = softened.filter(ImageFilter.GaussianBlur(feather))
        result -= strength * (np.asarray(softened, dtype=np.float32) / 255)
    result = np.clip(result, 0, 1) * (alpha.astype(np.float32) / 255)
    return np.where(valid & (alpha > 0), result, 0).astype(np.float32)


def prompt_coordinates(points, width, height):
    if not isinstance(points, list) or not 1 <= len(points) <= 128:
        raise ValueError("Click between 1 and 128 points, including one positive point.")
    coordinates, labels = [], []
    for point in points:
        if not isinstance(point, dict) or point.get("label") not in (0, 1):
            raise ValueError("Each point needs x, y and label 0 or 1.")
        x = bounded_number(point.get("x"), 0, 1, "point x")
        y = bounded_number(point.get("y"), 0, 1, "point y")
        coordinates.append([x * (width - 1), y * (height - 1)])
        labels.append(int(point["label"]))
    if 1 not in labels:
        raise ValueError("At least one positive point is needed to select a concave area.")
    return np.asarray(coordinates, dtype=np.float32), np.asarray(labels, dtype=np.int32)
