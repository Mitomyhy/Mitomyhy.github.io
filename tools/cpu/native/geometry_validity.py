"""Repair only tiny enclosed model-mask gaps inside fully opaque source pixels.

This is interpolation of existing geometry, not another depth estimate.  Source
RGB is never read.  Transparent/partly transparent boundaries, open components,
and large unknown regions stay invalid.  All originally valid values are kept
exactly; only newly repaired normal vectors are normalized.
"""
from __future__ import annotations

import numpy as np
from scipy import ndimage


ALGORITHM = "tiny-enclosed-geometry-holes"
VERSION = 1
MAX_HOLE_PIXELS = 64
MAX_PIXELS = 40_000_000
_EIGHT_NEIGHBOURS = np.ones((3, 3), dtype=bool)
ALPHA_EDGE_DISTANCE = 4.0
ALPHA_EDGE_DEPTH_RATIO = (0.8, 1.25)


def _inputs(depth, normal, valid, alpha):
    depth, normal = np.asarray(depth), np.asarray(normal)
    valid, alpha = np.asarray(valid), np.asarray(alpha)
    if (depth.ndim != 2 or min(depth.shape, default=0) < 2
            or depth.size > MAX_PIXELS):
        raise ValueError(f"Depth needs a 2D shape of at most {MAX_PIXELS} pixels.")
    if (normal.shape != depth.shape + (3,) or valid.shape != depth.shape
            or alpha.shape != depth.shape):
        raise ValueError("Depth, normal, validity and alpha dimensions disagree.")
    if not np.issubdtype(depth.dtype, np.floating) or not np.issubdtype(normal.dtype, np.floating):
        raise ValueError("Depth and normals must be floating-point geometry arrays.")
    if valid.dtype != np.bool_:
        if not np.issubdtype(valid.dtype, np.number) or not np.isfinite(valid).all() or np.any((valid != 0) & (valid != 1)):
            raise ValueError("Validity must be a boolean or binary mask.")
    if (not np.issubdtype(alpha.dtype, np.number) or not np.isfinite(alpha).all()
            or np.any((alpha < 0) | (alpha > 255))):
        raise ValueError("Source alpha must contain byte-range values from 0 to 255.")
    valid = valid.astype(bool, copy=True)
    if not np.isfinite(depth[valid]).all() or np.any(depth[valid] <= 0):
        raise ValueError("Originally valid depth must be finite and positive.")
    if (not np.isfinite(normal[valid]).all()
            or np.any(np.linalg.norm(normal[valid].astype(np.float64), axis=1) <= 1e-12)):
        raise ValueError("Originally valid normals must be finite and nonzero.")
    return depth.copy(), normal.copy(), valid, alpha


def _interpolate_component(depth, normal, ys, xs):
    """Solve the <=64-pixel four-neighbour Dirichlet problem exactly."""
    count = len(ys)
    positions = {(int(y), int(x)): i for i, (y, x) in enumerate(zip(ys, xs))}
    operator = np.eye(count, dtype=np.float64) * 4.0
    rhs = np.zeros((count, 4), dtype=np.float64)
    boundary = {}
    for i, (y, x) in enumerate(zip(ys, xs)):
        for by, bx in ((y-1, x), (y+1, x), (y, x-1), (y, x+1)):
            neighbour = positions.get((int(by), int(bx)))
            if neighbour is not None:
                operator[i, neighbour] -= 1.0
                continue
            n = normal[by, bx].astype(np.float64)
            n /= np.linalg.norm(n)
            value = np.r_[float(depth[by, bx]), n]
            rhs[i] += value
            boundary[(int(by), int(bx))] = value
    solved = np.linalg.solve(operator, rhs)
    boundary_positions = np.asarray(list(boundary), dtype=np.int64)
    boundary_values = np.asarray(list(boundary.values()), dtype=np.float64)
    if not np.isfinite(solved).all():
        raise ValueError("Tiny-hole geometry interpolation produced non-finite values.")
    # The harmonic maximum principle already bounds depth; clipping here only
    # removes floating-point roundoff at an exactly constant boundary.
    repaired_depth = np.clip(solved[:, 0], boundary_values[:, 0].min(), boundary_values[:, 0].max())
    repaired_normal = solved[:, 1:]
    length = np.linalg.norm(repaired_normal, axis=1)
    cancelled = length <= 1e-10
    # Opposite boundary normals can cancel.  Use an existing nearest boundary
    # direction in that exceptional case, without flipping original vectors.
    for i in np.flatnonzero(cancelled):
        distance = np.sum((boundary_positions - [ys[i], xs[i]]) ** 2, axis=1)
        repaired_normal[i] = boundary_values[int(np.argmin(distance)), 1:]
    repaired_normal /= np.linalg.norm(repaired_normal, axis=1, keepdims=True)
    return repaired_depth, repaired_normal, int(cancelled.sum())


def repair_tiny_geometry_holes(depth, normal, valid, alpha, max_hole_pixels=MAX_HOLE_PIXELS):
    """Return ``(depth, normal, valid, metadata)`` with tiny model gaps filled.

    ``alpha`` is the source alpha at the geometry dimensions, in the byte range
    0..255.  A repair requires an eight-connected invalid component of at most
    64 pixels whose complete one-pixel neighbourhood is fully opaque and whose
    surrounding ring is valid.  Diagonal channels to a boundary also stay open.

    Harmonic interpolation uses only the component's valid boundary.  It does
    not sample RGB, couple separate components, change silhouette alpha, or
    modify any valid depth/normal sample.  Unrepaired invalid values are copied
    unchanged, including their NaN/inf model sentinels.
    """
    if (isinstance(max_hole_pixels, (bool, np.bool_))
            or not isinstance(max_hole_pixels, (int, np.integer))
            or not 1 <= max_hole_pixels <= MAX_HOLE_PIXELS):
        raise ValueError("max_hole_pixels must be an integer from 1 through 64.")
    depth, normal, valid, alpha = _inputs(depth, normal, valid, alpha)
    metadata = {
        "algorithm": ALGORITHM, "version": VERSION,
        "max_hole_pixels": int(max_hole_pixels), "invalid_connectivity": 8,
        "required_source_alpha": 255, "boundary_guard_pixels": 1,
        "interpolation": "four-neighbour harmonic geometry from valid boundary only",
        "rgb_used": False, "original_valid_preserved": True,
        "repaired_pixels": 0, "repaired_components": 0,
        "normal_direction_fallback_pixels": 0,
        "skipped_components": {"too_large": 0, "image_border": 0,
                               "source_alpha_boundary": 0, "invalid_boundary": 0},
    }
    labels, count = ndimage.label(~valid, structure=_EIGHT_NEIGHBOURS)
    metadata["invalid_components"] = int(count)
    counts = np.bincount(labels.ravel())
    objects = ndimage.find_objects(labels)
    height, width = valid.shape
    for index, bounds in enumerate(objects, start=1):
        if bounds is None:
            continue
        if counts[index] > max_hole_pixels:
            metadata["skipped_components"]["too_large"] += 1
            continue
        y0, y1 = bounds[0].start, bounds[0].stop
        x0, x1 = bounds[1].start, bounds[1].stop
        if y0 == 0 or x0 == 0 or y1 == height or x1 == width:
            metadata["skipped_components"]["image_border"] += 1
            continue
        region = np.s_[y0-1:y1+1, x0-1:x1+1]
        component = labels[region] == index
        neighbourhood = ndimage.binary_dilation(component, structure=_EIGHT_NEIGHBOURS)
        if np.any(alpha[region][neighbourhood] != 255):
            metadata["skipped_components"]["source_alpha_boundary"] += 1
            continue
        ring = neighbourhood & ~component
        if not np.all(valid[region][ring]):
            metadata["skipped_components"]["invalid_boundary"] += 1
            continue
        ys, xs = np.nonzero(component)
        ys, xs = ys + y0 - 1, xs + x0 - 1
        filled_depth, filled_normal, fallback_count = _interpolate_component(depth, normal, ys, xs)
        depth[ys, xs] = filled_depth
        normal[ys, xs] = filled_normal
        valid[ys, xs] = True
        metadata["repaired_pixels"] += len(ys)
        metadata["repaired_components"] += 1
        metadata["normal_direction_fallback_pixels"] += fallback_count
    return depth, normal, valid, metadata


def recover_alpha_edge_geometry(unmasked_depth, unmasked_normal, model_valid, alpha):
    """Accept reliable unmasked predictions immediately beside a source cutout.

    This helper is for callers that requested unmasked model geometry.  It does
    not interpolate or replace any depth value.  A newly accepted pixel must be
    inside source alpha>0, no farther than four pixels from both reliable model
    geometry and alpha<255, and within 0.8..1.25 of the nearest reliable depth.
    Its predicted depth and normal must also be finite, positive/nonzero.

    At least one alpha==0 pixel is required, so partially transparent or opaque
    rectangular images cannot opt into this cutout-edge policy accidentally.
    Alpha-zero pixels are never added.  Existing mask entries and all originally
    valid depth/normal values are preserved exactly.  Only accepted normal
    predictions are normalized; rejected predictions remain unchanged.
    """
    depth, normal, valid, alpha = _inputs(unmasked_depth, unmasked_normal, model_valid, alpha)
    metadata = {
        "algorithm": "source-alpha-edge-unmasked-geometry", "version": 1,
        "max_edge_distance_pixels": ALPHA_EDGE_DISTANCE,
        "depth_ratio_limits": list(ALPHA_EDGE_DEPTH_RATIO),
        "requires_transparent_source_pixel": True,
        "rgb_used": False, "depth_interpolated": False,
        "original_valid_preserved": True, "alpha_zero_pixels_added": 0,
        "candidate_pixels": 0, "accepted_pixels": 0,
        "rejected_prediction_pixels": 0, "rejected_depth_ratio_pixels": 0,
    }
    if not np.any(alpha == 0):
        metadata["fallback_reason"] = "source_has_no_transparent_cutout"
        return depth, normal, valid, metadata
    reliable = valid & (alpha > 0)
    if not reliable.any():
        metadata["fallback_reason"] = "no_reliable_source_geometry"
        return depth, normal, valid, metadata
    distance_to_valid, nearest = ndimage.distance_transform_edt(
        ~reliable, return_indices=True)
    distance_to_alpha_edge = ndimage.distance_transform_edt(alpha == 255)
    candidates = (~valid & (alpha > 0)
                  & (distance_to_valid <= ALPHA_EDGE_DISTANCE)
                  & (distance_to_alpha_edge <= ALPHA_EDGE_DISTANCE))
    metadata["candidate_pixels"] = int(candidates.sum())
    ys, xs = np.nonzero(candidates)
    if not len(ys):
        metadata["fallback_reason"] = "no_nearby_source_edge_candidates"
        return depth, normal, valid, metadata
    predicted_depth = depth[ys, xs].astype(np.float64)
    predicted_normal = normal[ys, xs].astype(np.float64)
    with np.errstate(over="ignore", invalid="ignore"):
        normal_length = np.linalg.norm(predicted_normal, axis=1)
    finite = (np.isfinite(predicted_depth) & (predicted_depth > 0)
              & np.isfinite(predicted_normal).all(axis=1)
              & np.isfinite(normal_length) & (normal_length > 1e-12))
    metadata["rejected_prediction_pixels"] = int((~finite).sum())
    ys, xs = ys[finite], xs[finite]
    predicted_depth = predicted_depth[finite]
    predicted_normal = predicted_normal[finite]
    normal_length = normal_length[finite]
    nearest_depth = depth[nearest[0, ys, xs], nearest[1, ys, xs]].astype(np.float64)
    ratio = predicted_depth / nearest_depth
    agrees = (ratio >= ALPHA_EDGE_DEPTH_RATIO[0]) & (ratio <= ALPHA_EDGE_DEPTH_RATIO[1])
    metadata["rejected_depth_ratio_pixels"] = int((~agrees).sum())
    ys, xs = ys[agrees], xs[agrees]
    normal[ys, xs] = predicted_normal[agrees] / normal_length[agrees, None]
    valid[ys, xs] = True
    metadata["accepted_pixels"] = len(ys)
    if not len(ys):
        metadata["fallback_reason"] = "no_reliable_unmasked_edge_predictions"
    return depth, normal, valid, metadata
