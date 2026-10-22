"""Fast, explicitly artistic fullprint controls; no ML, downloads or OCR.

The estimate is a source-relief sketch, not inferred or measured geometry.
White support detection is deliberately conservative and can be disabled.
Text boxes/lassos select printed pixels, never solid embossing regions.
"""
import math

import numpy as np
from PIL import Image, ImageDraw
from scipy.ndimage import (binary_propagation, distance_transform_edt,
                           find_objects, gaussian_filter, label)

TEXT_MASK_VERSION = 4
SUPPORT_VERSION = 4


def _polygon(value, box):
    """Validate a bounded normalized lasso; its box is only an acceleration ROI."""
    if not isinstance(value, list) or not 3 <= len(value) <= 512:
        raise ValueError("Text polygon needs 3 to 512 normalized points.")
    points = []
    for point in value:
        if not isinstance(point, list) or len(point) != 2:
            raise ValueError("Text polygon points must be [x, y].")
        point = [_number(n, 0, 1, "Text polygon") for n in point]
        if not points or point != points[-1]:
            points.append(point)
    if len(points) > 1 and points[0] == points[-1]:
        points.pop()
    if len(points) < 3 or len(set(map(tuple, points))) < 3:
        raise ValueError("Text polygon needs at least three distinct points.")
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    bounds = [min(xs), min(ys), max(xs)-min(xs), max(ys)-min(ys)]
    if any(abs(a-b) > 1e-6 for a, b in zip(bounds, box)):
        raise ValueError("Text box must match the polygon bounds.")
    area = sum(a[0]*b[1]-b[0]*a[1] for a, b in zip(points, points[1:]+points[:1]))
    if abs(area) <= 1e-12:
        raise ValueError("Text polygon has no usable enclosed area.")
    return points


def _number(value, lo, hi, name):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or not lo <= value <= hi):
        raise ValueError(name + " is outside the supported numeric range.")
    return float(value)


def options(value=None):
    if value is None:
        value = {}
    if not isinstance(value, dict) or set(value) - {"support_enabled", "support_depth", "text_lines", "ceramic_flat_enabled"}:
        raise ValueError("Invalid fullprint options.")
    enabled = value.get("support_enabled", True)
    if not isinstance(enabled, bool):
        raise ValueError("support_enabled must be true or false.")
    lines = value.get("text_lines", [])
    if not isinstance(lines, list) or len(lines) > 48:
        raise ValueError("Use at most 48 text lines.")
    canonical, seen, polygon_points = [], set(), 0
    for line in lines:
        if not isinstance(line, dict) or set(line) - {"id", "text", "box", "polygon", "mode", "strength", "polarity"}:
            raise ValueError("Invalid text-line options.")
        identifier = line.get("id")
        if not isinstance(identifier, str) or not 1 <= len(identifier) <= 100 or identifier in seen:
            raise ValueError("Text lines require unique short IDs.")
        seen.add(identifier)
        text = line.get("text", "")
        if not isinstance(text, str) or len(text) > 500:
            raise ValueError("Text labels must be short strings.")
        box = line.get("box")
        if not isinstance(box, list) or len(box) != 4:
            raise ValueError("Text box must be normalized [x, y, width, height].")
        box = [_number(n, 0, 1, "Text box") for n in box]
        if box[2] <= 0 or box[3] <= 0 or box[0]+box[2] > 1.000001 or box[1]+box[3] > 1.000001:
            raise ValueError("Text box must stay inside the source image.")
        mode, polarity = line.get("mode", "off"), line.get("polarity", "auto")
        if mode not in ("raised", "recessed", "off") or polarity not in ("auto", "dark", "light"):
            raise ValueError("Choose raised/recessed/off and auto/dark/light text polarity.")
        result = dict(id=identifier, text=text, box=box, mode=mode, polarity=polarity,
                      strength=_number(line.get("strength", .08), 0, .3, "Text strength"))
        if "polygon" in line:
            result["polygon"] = _polygon(line["polygon"], box)
            polygon_points += len(line["polygon"])
            if polygon_points > 4096:
                raise ValueError("Use at most 4096 polygon points across all text regions.")
        canonical.append(result)
    result = dict(support_enabled=enabled,
                  support_depth=_number(value.get("support_depth", .12), .01, .4, "Support depth"),
                  text_lines=canonical)
    if "ceramic_flat_enabled" in value:
        if not isinstance(value["ceramic_flat_enabled"], bool):
            raise ValueError("ceramic_flat_enabled must be true or false.")
        result["ceramic_flat_enabled"] = value["ceramic_flat_enabled"]
    return result


def _small_rgba(rgba, limit=1024):
    h, w = rgba.shape[:2]
    scale = min(1., limit / max(h, w))
    if scale == 1:
        return rgba
    size = (max(8, round(w*scale)), max(8, round(h*scale)))
    return np.asarray(Image.fromarray(rgba, "RGBA").resize(size, Image.Resampling.LANCZOS))


def _resize_float(values, shape):
    if values.shape == shape:
        return values.astype(np.float32)
    return np.asarray(Image.fromarray(values.astype(np.float32)).resize(
        (shape[1], shape[0]), Image.Resampling.BILINEAR)).copy()


def canonical_height(height, valid, alpha):
    """Keep explicit fullprint alpha; fill only model-invalid visible pixels.

    Filling is a continuity estimate, recorded separately from raw AI validity.
    It does not turn transparent source holes into foreground.
    """
    visible = alpha > 0
    trustworthy = np.asarray(valid, bool) & visible & np.isfinite(height)
    missing = visible & ~trustworthy
    result = np.where(trustworthy, height, 0).astype(np.float32)
    if missing.any():
        if not trustworthy.any():
            raise ValueError("No valid geometry remains inside the fullprint alpha.")
        indices = distance_transform_edt(~trustworthy, return_distances=False, return_indices=True)
        result[missing] = result[tuple(indices[:, missing])]
    result[~visible] = 0
    return np.clip(result, 0, 1), visible, {"policy": "original-fullprint-alpha",
        "filled_model_invalid_pixels": int(missing.sum()), "alpha_pixels": int(visible.sum()),
        "fill_is_estimate": bool(missing.any())}


def estimate(rgba):
    """Immediate shape/luminance preview, with bounded shallow source detail."""
    small = _small_rgba(rgba)
    rgb = small[..., :3].astype(np.float32) / 255
    a = small[..., 3].astype(np.float32) / 255
    visible = a > 0
    luma = rgb @ np.array([.2126, .7152, .0722], np.float32)
    sigma = max(1., min(a.shape) / 160)
    weights = gaussian_filter(a, sigma)
    broad = gaussian_filter(luma*a, sigma) / np.maximum(weights, 1e-5)
    distance = distance_transform_edt(np.pad(visible, 1))[1:-1, 1:-1]
    radius = max(3., min(a.shape) * .07)
    dome = np.sqrt(np.clip(distance / radius, 0, 1))
    base = .28 + .20*dome + .14*broad + .07*np.clip(luma-broad, -.5, .5)
    base[~visible] = 0
    restored = _resize_float(base, rgba.shape[:2])
    # Normalize interpolated silhouette weights rather than darkening an edge.
    weight = _resize_float(visible.astype(np.float32), rgba.shape[:2])
    restored = np.divide(restored, np.maximum(weight, 1e-6))
    restored[rgba[..., 3] == 0] = 0
    return np.clip(restored, 0, 1).astype(np.float32)


def approximate_normal(height, alpha):
    dy, dx = np.gradient(height)
    normals = np.stack((-dx*3, -dy*3, -np.ones_like(height)), axis=-1)
    normals /= np.maximum(np.linalg.norm(normals, axis=-1, keepdims=True), 1e-6)
    result = np.rint((normals + 1)*127.5).astype(np.uint8)
    result[alpha == 0] = 0
    return result


def _detect_support_v1(rgba):
    """Only a thin, mostly enclosing white band connected to the cutout edge.

    Opaque input, white-only art, broad white forms and ambiguous partial white
    outlines are rejected. Bright interior artwork is never selected by color.
    """
    small = _small_rgba(rgba)
    rgb, alpha = small[..., :3].astype(np.int16), small[..., 3]
    visible = alpha >= 128
    empty = np.zeros(rgba.shape[:2], np.uint8)
    info = dict(detected=False, confidence=0., pixel_count=0, method="conservative-external-white-band",
                reason="No confidently enclosing white support was detected.")
    if not (alpha == 0).any() or visible.sum() < 32:
        info["reason"] = "Transparent fullprint background is required for support detection."
        return empty, info
    white = (rgb.min(axis=-1) >= 236) & (np.ptp(rgb, axis=-1) <= 18) & visible
    core = visible & ~white
    if core.sum() < max(24, .2*visible.sum()):
        info["reason"] = "Mostly white artwork is ambiguous; no automatic support change."
        return empty, info
    # External background only, not holes or internal white details.
    outside = ~visible
    seed = np.zeros_like(outside)
    seed[0] = outside[0]; seed[-1] = outside[-1]
    seed[:, 0] |= outside[:, 0]; seed[:, -1] |= outside[:, -1]
    exterior = binary_propagation(seed, mask=outside)
    from scipy.ndimage import binary_dilation
    boundary = visible & binary_dilation(exterior)
    coverage = float((white & boundary).sum() / max(1, boundary.sum()))
    if coverage < .65:
        info["confidence"] = round(coverage*.5, 3)
        return empty, info
    edge_distance = distance_transform_edt(visible)
    core_distance = distance_transform_edt(~core)
    thickness = max(2., min(visible.shape)*.022)
    thin = white & (edge_distance <= thickness) & (core_distance <= thickness*1.8)
    band = binary_propagation(white & boundary & thin, mask=thin)
    if band.sum() < 8 or band.sum() > .35*visible.sum():
        return empty, info
    # Broad connected white artwork can touch the edge. Do not retain the tips
    # of a broad white form merely because the thin threshold clipped its body.
    components, count = label(white)
    sizes = np.bincount(components.ravel(), minlength=count+1)
    broad = np.bincount(components[white & ~thin], minlength=count+1)
    rejected = broad > .45*sizes
    band[rejected[components]] = False
    retained = float((band & boundary).sum() / max(1, boundary.sum()))
    if retained < .60:
        return empty, info
    mask = np.asarray(Image.fromarray(band.astype(np.uint8)*255).resize(
        (rgba.shape[1], rgba.shape[0]), Image.Resampling.NEAREST)).copy()
    original = rgba[..., :3].astype(np.int16)
    mask[(rgba[..., 3] == 0) | (original.min(axis=-1) < 230) | (np.ptp(original, axis=-1) > 22)] = 0
    info.update(detected=bool(mask.any()), confidence=round(min(.98, .65+.32*retained), 3),
                pixel_count=int((mask > 0).sum()), reason="Thin enclosing external white band detected.")
    return mask, info


def _repair_stroke_runs(boundary, trusted, palette, widths, width, reach, strong=None):
    """Reconnect short contour runs between two distinct trusted ends.

    Connectivity is along the exterior contour, not proximity across opposing
    branches. Long/broad white arcs are not repaired. A width field rather than
    a zero/one veto is projected inward from each accepted exterior sample.
    """
    from scipy.ndimage import binary_dilation
    structure = np.ones((3, 3), bool)
    runs, count = label(boundary & ~trusted, structure=structure)
    sizes = np.bincount(runs.ravel(), minlength=count+1)
    accepted = trusted.copy()
    reach_field = np.zeros(boundary.shape, np.float32)
    reach_field[trusted] = reach
    stats = dict(repaired_boundary_pixels=0, repaired_boundary_runs=0,
                 maximum_run_pixels=round(max(2., 4*width), 3))
    # Reject impossible components in vectorized form before creating Python
    # bounding boxes. Noisy 40MP alpha edges can contain many tiny components.
    bad_color = np.bincount(runs[boundary & ~palette], minlength=count+1)
    bad_width = np.bincount(runs[boundary & (widths > max(reach, 3*width+1.5))], minlength=count+1)
    eligible = sizes <= max(2., 4*width)
    if strong is not None:
        # A longer COLOR-only AA/shading run is safe to reconnect when every
        # sample is directly backed by the existing inner stroke (<=sqrt(2)
        # pixels). This never widens the geometric contour-run allowance or
        # crosses a transparent gap to an opposing branch.
        backed = binary_dilation(strong & ~boundary, structure=structure)
        backed &= widths <= reach
        unbacked = np.bincount(runs[boundary & ~backed], minlength=count+1)
        color_run_limit = max(2., min(256., 16*width))
        eligible |= (sizes <= color_run_limit) & (unbacked == 0)
        stats["maximum_color_only_run_pixels"] = round(color_run_limit, 3)
    eligible &= (bad_color == 0) & (bad_width == 0)
    eligible[0] = False
    candidates = np.flatnonzero(eligible)
    if not candidates.size:
        return accepted, reach_field, stats
    if candidates.size > 4096:
        stats["repair_skipped_complex_contour"] = True
        return accepted, reach_field, stats
    if candidates.size != count:
        remap = np.zeros(count+1, np.int32)
        remap[candidates] = np.arange(1, candidates.size+1)
        runs = remap[runs]
        sizes = np.concatenate(([0], sizes[candidates]))
    objects = find_objects(runs)
    for identifier, bounds in enumerate(objects, 1):
        if bounds is None:
            continue
        region_bounds = tuple(slice(max(0, part.start-2), min(boundary.shape[i], part.stop+2))
                              for i, part in enumerate(bounds))
        region = runs[region_bounds] == identifier
        if not palette[region_bounds][region].all():
            continue
        values = widths[region_bounds][region]
        if values.max() > max(reach, 3*width+1.5):
            continue
        contacts = binary_dilation(region, structure=structure) & trusted[region_bounds]
        contact_ids, contact_count = label(contacts, structure=structure)
        # Two adjacent trusted pixels at the same end are not two anchors.
        if contact_count != 2:
            # At a one-pixel digital right-angle corner the two true contour
            # ends touch diagonally, so 8-connected labeling merges them.
            # Accept only strong-color, marginal-width corners with trusted
            # directions >=90 degrees apart; same-end 45-degree contacts fail.
            corner = False
            if (contact_count == 1 and int(region.sum()) == 1 and strong is not None
                    and strong[region_bounds][region].all() and values.max() <= reach+1.5):
                directions = np.argwhere(contacts)-np.argwhere(region)[0]
                corner = len(directions) >= 2 and bool(np.any(directions @ directions.T <= 0))
            if not corner:
                continue
        # Shortest boundary paths are unnecessary here: the rejected run is a
        # single 8-connected contour component bounded by those two endpoints.
        # Continuous finite reach follows observed local stroke thickness.
        local_reach = np.maximum(reach, np.minimum(values+1.5, 3*width+1.5))
        accepted[region_bounds][region] = True
        reach_field[region_bounds][region] = local_reach
        stats["repaired_boundary_pixels"] += int(region.sum())
        stats["repaired_boundary_runs"] += 1
    return accepted, reach_field, stats


def _detect_support_v4(rgba):
    """Extend a proven outer printed stroke around transparent inner contours.

    Exterior v3 detection remains the calibration and an unchanged subset of
    the result. A large unoutlined hole cannot dilute that evidence. Interior
    white artwork is never a seed: an actual alpha-zero contour is required.
    """
    from scipy.ndimage import binary_dilation
    outer, info = detect_support(rgba, support_version=3)
    info = dict(info)
    info.update(algorithm_version=4, method="native-continuous-all-contour-stroke",
                inner_boundary_pixels=0, inner_stroke_pixels=0,
                inner_contours_detected=0,
                inner_policy="outer-calibrated neutral thin bands at transparent holes")
    if not info["detected"]:
        info["inner_reason"] = "No confident exterior stroke calibration; inner artwork left unchanged."
        return outer, info
    visible = rgba[..., 3] > 0
    outside = ~visible
    seed = np.zeros_like(outside)
    seed[0] = outside[0]; seed[-1] = outside[-1]
    seed[:, 0] |= outside[:, 0]; seed[:, -1] |= outside[:, -1]
    exterior = binary_propagation(seed, mask=outside)
    holes = outside & ~exterior
    if not holes.any():
        info["inner_reason"] = "No enclosed transparent contours."
        return outer, info
    boundary = visible & binary_dilation(holes)
    info["inner_boundary_pixels"] = int(boundary.sum())
    if not boundary.any():
        return outer, info
    color = np.asarray(info["stroke_color"], np.float32)
    low, high = info["neutral_brightness_range"]
    width, reach = info["stroke_width_pixels"], info["maximum_reach_pixels"]
    rgb = rgba[..., :3].astype(np.int16)
    neutral_spread = max(6., min(8., float(np.ptp(color))+4.))
    neutral = np.ptp(rgb, axis=-1) <= neutral_spread
    level = rgb.mean(axis=-1)
    plateau = visible & neutral & (level >= max(236., low-2.)) & (level <= min(255., high+2.))
    weak_stroke = visible & neutral & (level >= max(236., low-12.)) & (level <= min(255., high+12.))
    core = visible & ~plateau
    core_distance = distance_transform_edt(~core)
    trusted = plateau & boundary & (core_distance <= reach)
    trusted, reach_field, repair_info = _repair_stroke_runs(
        boundary, trusted, weak_stroke, core_distance, width, reach, strong=plateau)
    matching_width = plateau & boundary & (core_distance >= max(1., .45*width)) & (core_distance <= reach)
    del core_distance
    # Test each disconnected inner contour rather than averaging it together
    # with the already-proven outside. Unoutlined/mostly broad white contours
    # cannot become support just because a few bright object tips touch them.
    contour_ids, count = label(boundary, structure=np.ones((3, 3), bool))
    sizes = np.bincount(contour_ids.ravel(), minlength=count+1)
    accepted = np.bincount(contour_ids[trusted], minlength=count+1)
    keep = (sizes >= 8) & (accepted >= .60*sizes)
    keep[0] = False
    # A window may have an outlined row of bulbs but intentionally unoutlined
    # lower edges. Accept a sufficiently long continuous alpha-boundary arc
    # with the already-learned stroke width, not only mostly enclosed loops.
    # Broad-material rejection below must retain almost the entire arc.
    run_ids, run_count = label(trusted, structure=np.ones((3, 3), bool))
    run_sizes = np.bincount(run_ids.ravel(), minlength=run_count+1)
    matching = np.bincount(run_ids[matching_width & trusted], minlength=run_count+1)
    partial = (run_sizes >= max(12., 4*width)) & (matching >= .80*run_sizes)
    partial[0] = False
    run_contours = np.zeros(run_count+1, np.int32)
    np.maximum.at(run_contours, run_ids[trusted], contour_ids[trusted])
    keep_runs = keep[run_contours] | partial
    keep_runs[0] = False
    trusted &= keep_runs[run_ids]
    del matching_width
    reach_field[~trusted] = 0
    if not trusted.any():
        info["inner_reason"] = "No coherent narrow inner stroke matched the exterior."
        return outer, info
    # Project from the nearest TRUE alpha contour, including exterior edges.
    # This prevents jumping across opposite branches or following a broad
    # white region farther inward simply because it shares a white component.
    all_boundary = visible & binary_dilation(outside)
    nearest = distance_transform_edt(~all_boundary, return_distances=False,
                                      return_indices=True)
    coherent = trusted[tuple(nearest)]
    local_reach = reach_field[tuple(nearest)]
    projected_runs = run_ids[tuple(nearest)]
    del nearest, reach_field
    boundary_distance = distance_transform_edt(visible)
    thin = plateau & coherent & (boundary_distance <= local_reach)
    thin |= trusted
    band = binary_propagation(trusted, mask=thin)
    # Preserve the legacy broad-material rejection, counting proven outer
    # support as thin rather than misclassifying it as unselected white art.
    components, component_count = label(plateau)
    component_sizes = np.bincount(components.ravel(), minlength=component_count+1)
    broad = np.bincount(components[plateau & ~thin & (outer == 0)], minlength=component_count+1)
    rejected = broad > .45*component_sizes
    band[rejected[components]] = False
    if not band.any():
        info["inner_reason"] = "Inner white regions were too broad for a printed stroke."
        return outer, info
    fringe = (distance_transform_edt(~band) <= 1.5) & visible & weak_stroke
    fringe &= np.ptp(rgb, axis=-1) <= max(6., float(np.ptp(color))+4.)
    fringe &= coherent & (boundary_distance <= local_reach)
    band |= fringe
    # Recheck actual retained contour coverage after broad-material rejection;
    # a contour must not survive as a few shaved-off white tips.
    retained = np.bincount(contour_ids[band & boundary], minlength=count+1)
    keep &= retained >= .60*sizes
    retained_runs = np.bincount(run_ids[band & boundary], minlength=run_count+1)
    partial &= retained_runs >= .90*run_sizes
    keep_runs = keep[run_contours] | partial
    keep_runs[0] = False
    if not keep_runs.any():
        info["inner_reason"] = "Inner stroke lost coherent coverage after material rejection."
        return outer, info
    # Reject entire removed contours, including their projected inner pixels.
    band &= keep_runs[projected_runs]
    del projected_runs
    combined = (outer > 0) | band
    if combined.sum() > .35*visible.sum():
        info["inner_reason"] = "Combined white coverage is ambiguous; retained the proven outer stroke only."
        return outer, info
    info.update(pixel_count=int(combined.sum()), inner_stroke_pixels=int((band & (outer == 0)).sum()),
                inner_contours_detected=int(np.unique(run_contours[keep_runs]).size),
                inner_partial_runs_detected=int((partial & ~keep[run_contours]).sum()),
                inner_repaired_boundary_pixels=repair_info["repaired_boundary_pixels"],
                inner_repaired_boundary_runs=repair_info["repaired_boundary_runs"],
                reason="Coherent outer and inner transparent-contour white strokes detected.",
                boundary_policy="all alpha contours; two-ended runs and finite local width",
                confidence_basis="outer calibration plus coherent inner contour/arc width and coverage")
    return combined.astype(np.uint8)*255, info


def detect_support(rgba, *, support_version=SUPPORT_VERSION):
    """Find coherent printed strokes, not arbitrary white artwork.

    Versions 1/2/3 are frozen for reconstruction of saved jobs/manual deltas.
    Version 3 adds neutral-color hysteresis and bounded two-ended contour-run
    continuity to the native stroke evidence. It deliberately leaves ambiguous
    broad white material alone. This is not semantic object segmentation and
    never changes the source alpha or colors. Version 4 extends proven outer
    stroke evidence around enclosed transparent contours and long narrow arcs.
    """
    if type(support_version) is not int:
        raise ValueError("Unsupported saved support-mask version.")
    if support_version == 4:
        return _detect_support_v4(rgba)
    if support_version == 1:
        return _detect_support_v1(rgba)
    if support_version not in (2, 3):
        raise ValueError("Unsupported saved support-mask version.")
    modern = support_version >= 3
    from scipy.ndimage import binary_dilation
    alpha = rgba[..., 3]
    visible = alpha > 0
    empty = np.zeros(alpha.shape, np.uint8)
    info = dict(detected=False, confidence=0., pixel_count=0,
        method="native-continuous-exterior-stroke" if modern else "native-coherent-exterior-stroke",
        algorithm_version=support_version,
        reason="No confidently enclosing neutral-white stroke was detected.")
    if not (alpha == 0).any() or visible.sum() < 32:
        info["reason"] = "Transparent fullprint background is required for support detection."
        return empty, info
    outside = ~visible
    seed = np.zeros_like(outside)
    seed[0] = outside[0]; seed[-1] = outside[-1]
    seed[:, 0] |= outside[:, 0]; seed[:, -1] |= outside[:, -1]
    exterior = binary_propagation(seed, mask=outside)
    boundary = visible & binary_dilation(exterior)
    if not boundary.any():
        return empty, info
    # Straight PNG RGB remains the stroke color even at partially covered
    # outer pixels. Do not downsample/re-threshold away these AA edge pixels.
    colors = rgba[boundary, :3].astype(np.int16)
    neutral = (colors.min(axis=1) >= 236) & (np.ptp(colors, axis=1) <= 12)
    if neutral.mean() < .65:
        return empty, info
    # Quantized native boundary mode tolerates encoding noise, not cream fur.
    keys = ((colors[neutral].astype(np.int32)+2)//4).clip(0, 64)
    keys = keys[:, 0]*4225 + keys[:, 1]*65 + keys[:, 2]
    values, counts = np.unique(keys, return_counts=True)
    mode = values[counts.argmax()]
    color = np.median(colors[neutral][keys == mode], axis=0)
    rgb = rgba[..., :3].astype(np.int16)
    delta = np.max(np.abs(rgb-color), axis=-1)
    if modern:
        # Learn a neutral brightness envelope from the observed exterior.
        # Shaded gray strokes need not equal a single RGB mode. Chroma stays
        # tightly bounded so cream fur is not included as a white stroke.
        edge_levels = colors[neutral].mean(axis=1)
        low, high = np.percentile(edge_levels, [5, 95])
        neutral_spread = max(6., min(8., float(np.ptp(color))+4.))
        level = rgb.mean(axis=-1)
        plateau = visible & (np.ptp(rgb, axis=-1) <= neutral_spread)
        plateau &= (level >= max(236., low-2.)) & (level <= min(255., high+2.))
        weak_stroke = visible & (np.ptp(rgb, axis=-1) <= neutral_spread)
        weak_stroke &= (level >= max(236., low-12.)) & (level <= min(255., high+12.))
    else:
        plateau = (delta <= 4) & visible
    coverage = float((plateau & boundary).sum()/boundary.sum())
    if coverage < .60:
        info["reason"] = "The exterior white pixels do not form one coherent stroke color."
        return empty, info
    core = visible & ~plateau
    if core.sum() < max(24, .2*visible.sum()):
        info["reason"] = "Mostly white artwork is ambiguous; no automatic support change."
        return empty, info
    # The distance to non-stroke visible artwork estimates printed stroke
    # width without treating the transparent exterior as the inner boundary.
    core_distance = distance_transform_edt(~core)
    widths = core_distance[plateau & boundary]
    lower, width, upper = np.percentile(widths, [25, 50, 75])
    variation_limit = max(3., lower*3.5) if modern else max(3., lower*2.5)
    if width > max(3., min(alpha.shape)*(.04 if modern else .012)) or upper > variation_limit:
        info["reason"] = "White edge width is too broad or inconsistent for a printed stroke."
        return empty, info
    # A few pixels of curvature/AA tolerance, measured from EXTERIOR only.
    # Internal transparent holes cannot seed or extend a support stroke.
    reach = max(2., width*1.75+1.5) if modern else max(2., min(width*1.75+1.5, min(alpha.shape)*.016))
    stroke_boundary = plateau & boundary & (core_distance <= reach)
    if modern:
        stroke_boundary, reach_field, repair_info = _repair_stroke_runs(
            boundary, stroke_boundary, weak_stroke, core_distance, width, reach, strong=plateau)
    del core_distance
    # A broad white form can be connected to the same plateau as the stroke.
    # Its broad exterior section is not a valid seed, nor may the stroke grow
    # laterally into pixels whose nearest exterior boundary is that section.
    nearest_boundary = distance_transform_edt(~boundary, return_distances=False,
                                             return_indices=True)
    coherent_edge = stroke_boundary[tuple(nearest_boundary)]
    if modern:
        local_reach = reach_field[tuple(nearest_boundary)]
        del reach_field
    del nearest_boundary
    exterior_distance = distance_transform_edt(~exterior)
    thin = plateau & (exterior_distance <= (local_reach if modern else reach)) & coherent_edge
    if modern:
        thin |= stroke_boundary
    band = binary_propagation(stroke_boundary, mask=thin)
    # Reject connected broad white artwork instead of shaving off its tips.
    components, count = label(plateau)
    sizes = np.bincount(components.ravel(), minlength=count+1)
    broad = np.bincount(components[plateau & ~thin], minlength=count+1)
    rejected = broad > .45*sizes
    band[rejected[components]] = False
    if not band.any():
        return empty, info
    # Only a finite antialiased fringe may grow from the coherent plateau.
    # No flood through cream highlights or similarly bright connected fur.
    fringe = (distance_transform_edt(~band) <= 1.5) & visible
    if modern:
        fringe &= weak_stroke
    else:
        fringe &= delta <= 10
    fringe &= np.ptp(rgb, axis=-1) <= max(6., float(np.ptp(color))+4.)
    fringe &= exterior_distance <= (local_reach if modern else reach)
    fringe &= coherent_edge
    band |= fringe
    retained = float((band & boundary).sum()/boundary.sum())
    if retained < .60 or band.sum() > .35*visible.sum():
        return empty, info
    info.update(detected=True, confidence=round(min(.98, .65+.32*retained), 3),
        pixel_count=int(band.sum()), reason="Native coherent exterior white stroke detected.",
        stroke_color=[int(n) for n in color], stroke_width_pixels=round(float(width), 3),
        maximum_reach_pixels=round(float(reach), 3), fringe_pixels=1.5,
        confidence_basis="exterior stroke coverage; not semantic material certainty")
    if modern:
        info.update(repair_info, neutral_brightness_range=[round(float(low), 3), round(float(high), 3)],
                    boundary_policy="two-ended short contour runs; finite local width field")
    return band.astype(np.uint8)*255, info


def _support_blend_v2(bed, visible, transition):
    """Flatten the outside fully; blend only toward neighboring artwork."""
    from scipy.ndimage import binary_dilation
    artwork = visible & ~bed
    if not artwork.any():
        return bed.astype(np.float32)
    inward = distance_transform_edt(~artwork)
    outside = ~visible
    seed = np.zeros_like(outside)
    seed[0] = outside[0]; seed[-1] = outside[-1]
    seed[:, 0] |= outside[:, 0]; seed[:, -1] |= outside[:, -1]
    exterior = binary_propagation(seed, mask=outside)
    outward = distance_transform_edt(~exterior)
    # Adapt a short transition to locally thinner strokes as well. At the
    # true outer edge outward=1, so no original-height ridge is reintroduced.
    span = np.maximum(1., inward+outward-1.)
    blend = np.clip(inward/np.minimum(transition, span), 0, 1)
    blend = blend*blend*(3-2*blend)
    blend[~bed] = 0
    blend[bed & binary_dilation(exterior)] = 1
    return blend.astype(np.float32)


def _support_blend_v3(bed, visible, transition):
    """Repair mask topology, retaining the proven one-sided v2 transition.

    Do not normalize a blur by bed weights: that increases the last inner
    coefficient and steepens the step into artwork, despite a smoother mask.
    """
    return _support_blend_v2(bed, visible, transition)


def _support_blend_v4(bed, visible, transition):
    """Keep support flat against every alpha hole; blend only into artwork."""
    from scipy.ndimage import binary_dilation
    artwork = visible & ~bed
    if not artwork.any():
        return bed.astype(np.float32)
    inward = distance_transform_edt(~artwork)
    outward = distance_transform_edt(visible)
    span = np.maximum(1., inward+outward-1.)
    blend = np.clip(inward/np.minimum(transition, span), 0, 1)
    blend = blend*blend*(3-2*blend)
    blend[~bed] = 0
    blend[bed & binary_dilation(~visible)] = 1
    return blend.astype(np.float32)


def polygon_mask(line, bounds, canvas_size):
    """Raster lasso in its small ROI; legacy rectangles cover that whole ROI."""
    x0, y0, x1, y1 = bounds
    w, h = canvas_size
    if "polygon" not in line:
        return np.ones((y1-y0, x1-x0), bool)
    mask = Image.new("L", (x1-x0, y1-y0), 0)
    points = [(x*w-x0, y*h-y0) for x, y in line["polygon"]]
    ImageDraw.Draw(mask).polygon(points, fill=255)
    return np.asarray(mask) > 0


def _printed_ink(chosen, rgb, visible, roi, contrast):
    """Keep coherent printed ink, not equally dark/light shaded material.

    A single midtone threshold admits red trim, dark shadows and bright wicker.
    Printed text has a repeated interior color with a narrow antialias fringe.
    Find that color locally (it may be gray/colored, not just black or white),
    seed it, and limit growth. Never flood an attached material component.
    """
    quantized = (rgb.astype(np.uint16) >> 3)
    codes = (quantized[..., 0] << 10) | (quantized[..., 1] << 5) | quantized[..., 2]
    counts = np.bincount(codes[chosen], minlength=32768)
    peak = int(np.argmax(counts))
    samples = rgb[chosen & (codes == peak)]
    if samples.shape[0] < 2:
        return np.zeros_like(chosen), "No stable printed-ink color. Tighten the lasso around the lettering."
    ink = np.median(samples, axis=0)
    tolerance = max(2., min(15., contrast * 255 * .08))
    distance = np.max(np.abs(rgb.astype(np.float32)-ink), axis=-1)
    seeds = chosen & (distance <= tolerance)
    if seeds.sum() < 2:
        return np.zeros_like(chosen), "No consistent printed ink; tighten the lasso or select differently colored text separately."
    components, count = label(chosen)
    sizes = np.bincount(components.ravel(), minlength=count+1)
    seed_counts = np.bincount(components[seeds], minlength=count+1)
    # A tight lasso may touch real letters or a woven strand attached to them.
    # Classify the strong ink islands, not the entire midtone component: the
    # latter would erase valid white digits when they touch bright wicker.
    from scipy.ndimage import binary_erosion
    boundary = visible & ~binary_erosion(roi, border_value=0)
    touching = np.unique(components[boundary])
    mixed = np.zeros(count+1, bool)
    mixed[touching] = seed_counts[touching] < .5*sizes[touching]
    mixed[0] = False
    islands, island_count = label(seeds)
    island_sizes = np.bincount(islands.ravel(), minlength=island_count+1)
    ordered = np.sort(island_sizes[1:])
    typical = float(ordered[np.searchsorted(np.cumsum(ordered), ordered.sum()*.5)])
    retain = island_sizes >= max(2., typical*.15)
    retain[0] = False
    # Small standalone dots already survive in coherent components. A compact
    # dot attached to textured material must survive too; unlike scattered
    # shadow islands it is a nearly filled, compact patch of the same ink.
    small = np.flatnonzero((island_sizes >= 4) & ~retain)
    small = small[small > 0]
    if small.size:
        lookup = np.zeros(island_count+1, np.int32)
        lookup[small] = np.arange(1, small.size+1)
        boxes = find_objects(lookup[islands], max_label=int(small.size))
        for identifier, box in zip(small, boxes):
            if box is None:
                continue
            ys, xs = box
            ih, iw = ys.stop-ys.start, xs.stop-xs.start
            if min(ih, iw)/max(ih, iw) >= .5 and island_sizes[identifier] >= .8*ih*iw:
                retain[identifier] = True
    seeds &= ~mixed[components] | retain[islands]
    if not seeds.any():
        return np.zeros_like(chosen), "The selected tones look like edge material rather than consistent text ink. Tighten the lasso."
    # Fixed subpixel-scale growth preserves antialiasing without following
    # bright woven strands or a shadow arbitrarily far from the true glyph.
    close = distance_transform_edt(~seeds) <= 1.5
    result = chosen & close
    if result.sum() < max(2, .005*visible.sum()):
        return np.zeros_like(chosen), "Too little consistent printed ink; tighten the lasso around one same-colored text region."
    return result, None


def glyph_mask(rgba, line, *, reject_edge_rules=True, sparse_strokes=True, refine_ink=True):
    """Return a local pixel mask, never the supplied rectangle itself."""
    h, w = rgba.shape[:2]
    x, y, bw, bh = line["box"]
    x0, y0 = max(0, int(x*w)), max(0, int(y*h))
    x1, y1 = min(w, math.ceil((x+bw)*w)), min(h, math.ceil((y+bh)*h))
    region = rgba[y0:y1, x0:x1]
    blank = np.zeros(region.shape[:2], np.float32)
    if region.size == 0 or min(region.shape[:2]) < 3 or region.shape[0]*region.shape[1] > 4_000_000:
        return (x0, y0, x1, y1), blank, "Text box is empty, too small or too large."
    gray = region[..., :3].astype(np.float32) @ np.array([.2126, .7152, .0722], np.float32) / 255
    roi = polygon_mask(line, (x0, y0, x1, y1), (w, h))
    visible = (region[..., 3] >= 128) & roi
    if visible.sum() < 4:
        return (x0, y0, x1, y1), blank, "No opaque glyph pixels."
    # The accepted occupancy floor is 0.5%. A 2% percentile erased valid
    # one-pixel/thin lettering in a spacious lasso before reconstruction ran.
    # Saved v1/v2 compositions explicitly retain the original percentiles.
    low, high = np.percentile(gray[visible], [.5, 99.5] if sparse_strokes else [2, 98])
    if high-low < .12:
        return (x0, y0, x1, y1), blank, "Insufficient glyph/background contrast."
    threshold = (low+high)*.5
    dark, light = (gray < threshold) & visible, (gray > threshold) & visible
    polarity = line["polarity"]
    if polarity == "auto":
        # Smaller tonal cluster is usually lettering, rather than its plaque.
        chosen = dark if dark.sum() < light.sum() else light
    else:
        chosen = dark if polarity == "dark" else light
    occupancy = float(chosen.sum() / max(1, visible.sum()))
    if not .005 <= occupancy <= .60:
        return (x0, y0, x1, y1), blank, "Ambiguous glyph coverage; choose polarity or tighten the text box."
    labels, count = label(chosen)
    sizes = np.bincount(labels.ravel())
    keep = (sizes >= 2)
    keep[0] = False
    if reject_edge_rules:
        # OCR rectangles can include the plaque's thin top/bottom rule. Reject
        # only a long, shallow component touching that ROI edge, never an
        # interior underline, narrow glyph, dot, or connected letter stem.
        rh, rw = chosen.shape
        boundary = np.unique(np.concatenate((labels[:2].ravel(), labels[-2:].ravel())))
        candidates = boundary[(boundary > 0) & keep[boundary] & (sizes[boundary] >= .75*rw)]
        if candidates.size:
            # Remap only candidates, avoiding one Python bbox object per tiny
            # source-noise component in a large allowed OCR rectangle.
            lookup = np.zeros(count+1, np.int32)
            lookup[candidates] = np.arange(1, candidates.size+1)
            boxes = find_objects(lookup[labels], max_label=int(candidates.size))
            for identifier, box in zip(candidates, boxes):
                if box is None:
                    continue
                ys, xs = box
                shallow = ys.stop-ys.start <= max(2, math.ceil(rh*.08))
                edge = ys.start <= 1 or ys.stop >= rh-1
                if shallow and edge and xs.stop-xs.start >= rw*.75:
                    keep[identifier] = False
    chosen = keep[labels]
    if not chosen.any():
        return (x0, y0, x1, y1), blank, "No connected glyph strokes."
    if refine_ink:
        chosen, reason = _printed_ink(chosen, region[..., :3], visible, roi, float(high-low))
        if reason:
            return (x0, y0, x1, y1), blank, reason
    # Subpixel antialiasing only, with a finite footprint. Never flood the box.
    softened = gaussian_filter(chosen.astype(np.float32), .55, truncate=2.)
    # Clip again AFTER feathering: convolution must never change a pixel
    # outside the user's lasso, including concave cut-ins and thin exclusions.
    softened *= visible & roi
    return (x0, y0, x1, y1), softened, None


def glyph_surface(height, glyph, visible, roi):
    """Estimate the supporting surface from nearby NON-glyph material.

    A signed boost alone cannot turn an existing deep luminance incision into
    raised text. Exclude the printed strokes and their one-pixel edge halo,
    interpolate the local background, then remove the source-mark height.
    This is a bounded artistic reconstruction, not a measured missing surface.
    """
    core = glyph >= .5
    if not core.any():
        return None, "No stable glyph core to reconstruct."
    distance = distance_transform_edt(~core)
    known = visible & roi & (distance > 1.25)
    if known.sum() < 4:
        known = visible & roi & (glyph <= .01)
    if known.sum() < 4:
        return None, "Not enough surrounding material; widen the lasso around the letters."
    gaps, indices = distance_transform_edt(~known, return_indices=True)
    # Nearest valid material is a bounded fallback only where normalized
    # convolution has no nearby support; the smoothed field follows curvature.
    filled = height[tuple(indices)]
    sigma = float(np.clip(np.percentile(gaps[core], 90) * 1.2, 1.0, 32.0))
    weights = gaussian_filter(known.astype(np.float32), sigma, truncate=3.)
    numerator = gaussian_filter(np.where(known, height, 0), sigma, truncate=3.)
    smooth = gaussian_filter(filled.astype(np.float32), max(1., sigma*.55), truncate=3.)
    surface = np.where(weights > .02, numerator/np.maximum(weights, 1e-6), smooth)
    return np.clip(surface, 0, 1).astype(np.float32), None


def compose(base, rgba, settings, *, glyph_mask_version=TEXT_MASK_VERSION,
            support_version=SUPPORT_VERSION):
    """Compose from an immutable alpha-premultiplied baseline in native pixels."""
    settings = options(settings)
    if glyph_mask_version not in (1, 2, 3, 4):
        raise ValueError("Unsupported saved text-mask version.")
    if glyph_mask_version < 3 and any("polygon" in line for line in settings["text_lines"]):
        raise ValueError("Legacy text-mask versions do not support polygon selections.")
    alpha = rgba[..., 3].astype(np.float32) / 255
    visible = alpha > 0
    height = np.divide(base, np.maximum(alpha, 1e-6)).astype(np.float32)
    support, info = detect_support(rgba, support_version=support_version)
    info["enabled"] = settings["support_enabled"]
    info["applied"] = bool(info["detected"] and settings["support_enabled"])
    if info["applied"]:
        # Low external bed with a short transition into the neighboring artwork.
        bed = support > 0
        transition = max(.8, min(base.shape)*.0015)
        if support_version == 1:
            inside_distance = distance_transform_edt(bed)
            blend = np.clip(inside_distance / transition, 0, 1)
            blend = blend*blend*(3-2*blend)
        elif support_version == 2:
            blend = _support_blend_v2(bed, visible, transition)
        elif support_version == 3:
            blend = _support_blend_v3(bed, visible, transition)
        else:
            blend = _support_blend_v4(bed, visible, transition)
        height = height*(1-blend) + settings["support_depth"]*blend
    else:
        support[:] = 0
    text_info = []
    # Every ROI estimates from the same unmodified underlying surface, not a
    # previously applied overlapping line. Repeated Apply never compounds.
    reference = height.copy() if glyph_mask_version >= 3 and settings["text_lines"] else height
    for line in settings["text_lines"]:
        record = dict(id=line["id"], mode=line["mode"], applied=False)
        if line["mode"] == "off" or line["strength"] == 0:
            record["reason"] = "Disabled."
        else:
            bounds, glyph, reason = glyph_mask(rgba, line, reject_edge_rules=glyph_mask_version >= 2,
                                               sparse_strokes=glyph_mask_version >= 3,
                                               refine_ink=glyph_mask_version >= 4)
            if reason:
                record["reason"] = reason
            else:
                x0, y0, x1, y1 = bounds
                direction = 1 if line["mode"] == "raised" else -1
                region = height[y0:y1, x0:x1]
                if glyph_mask_version < 3:
                    # Exact legacy reconstruction is required to separate
                    # user retouch deltas when upgrading old saved jobs.
                    region += direction * line["strength"] * glyph
                else:
                    roi = polygon_mask(line, bounds, (rgba.shape[1], rgba.shape[0]))
                    surface, reason = glyph_surface(reference[y0:y1, x0:x1], glyph,
                        rgba[y0:y1, x0:x1, 3] >= 128, roi)
                    if reason:
                        record["reason"] = reason
                        text_info.append(record)
                        continue
                    # Fully remove the old source-mark depression/bump on
                    # every hard glyph pixel, even a one-pixel stroke. The
                    # outer feather blends that repair; it cannot leak the ROI.
                    repair = np.minimum(1., glyph*2.)
                    region += (surface-region)*repair + direction*line["strength"]*glyph
                    record.update(surface_reconstructed=True,
                        surface_method="local-non-glyph-normalized-convolution",
                        normalized_height_limit=True)
                record.update(applied=True, pixel_count=int((glyph > .01).sum()),
                    mask_kind="source-glyph-pixels", selection="polygon" if "polygon" in line else "rectangle")
                if glyph_mask_version >= 4:
                    record["ink_segmentation"] = "coherent-local-color-seeds-bounded-fringe-v1"
        text_info.append(record)
    result = np.clip(height, .001, 1) * alpha
    result[~visible] = 0
    return result.astype(np.float32), support, dict(support=info, text_lines=text_info,
        physical_depth_claim=False, algorithm="fullprint-layout-controls-v2" if glyph_mask_version >= 3 else "fullprint-layout-controls-v1",
        glyph_mask_version=glyph_mask_version, support_version=support_version)
