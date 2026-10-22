"""Bounded decorative signed source detail, not measured physical geometry.

Dark local marks are incised and bright marks raised. Connected grayscale-area
backgrounds retain perforated materials; near/far source context protects letter
counters. Threshold persistence changes the permitted ink amplitude, never adds
new feature support. The primary signed layer does not use normals. A bounded
normal-guided compatibility layer can retain pale raised paint, but never fills
any primary incision. Neither layer uses reference targets or modifies geometry.
Native residuals remain source-aligned; only analysis is reduced.
"""
from __future__ import annotations

import math
import cv2
import numpy as np
from scipy.ndimage import distance_transform_edt, gaussian_filter, maximum_filter

ALGORITHM = "signed-area-fullprint-source-structure"
VERSION = 5
MAX_PIXELS = 40_000_000
MAX_SIGNED_ABS = .14
MAX_PRINT_ABS = .09  # Compatibility name: maximum raised stable-ink cue.
MAX_DARK_ABS = .14
MAX_BRIGHT_ABS = .09
THIN_DARK_ABS = .028
THIN_BRIGHT_ABS = .026
MEDIUM_BRIGHT_ABS = .04
ANALYSIS_MAX_EDGE = 512
INK_ANALYSIS_MAX_EDGE = 1024
MAX_SUPPORT_SCALE = 16.
BRIGHT_COMPAT_ANALYSIS_MAX_EDGE = 2048
BRIGHT_COMPAT_MAX_ABS = .09
BRIGHT_COMPAT_THIN_ABS = .0075
BRIGHT_COMPAT_EDGE_REACH = 16.
_WHOLE_NATIVE_PIXELS = 4_200_000
_TILE_EDGE = 1024
_EPS = 1e-6


def _inputs(relief, rgb, valid, amount, mode):
    if isinstance(amount, (bool, np.bool_)):
        raise ValueError("source_detail must be between 0 and 1.")
    try:
        amount = float(amount)
    except (TypeError, ValueError):
        raise ValueError("source_detail must be between 0 and 1.") from None
    if not math.isfinite(amount) or not 0 <= amount <= 1:
        raise ValueError("source_detail must be between 0 and 1.")
    if mode not in ("ornament", "depth"):
        raise ValueError("mode must be ornament or depth.")
    height, colour, mask = np.asarray(relief, np.float32), np.asarray(rgb), np.asarray(valid)
    if height.ndim != 2 or min(height.shape, default=0) < 1:
        raise ValueError("Relief must be a nonempty 2D array.")
    if height.size > MAX_PIXELS:
        raise ValueError(f"Full-print detail accepts at most {MAX_PIXELS} pixels.")
    if not np.isfinite(height).all() or np.any((height < 0) | (height > 1)):
        raise ValueError("Relief must contain finite values between 0 and 1.")
    if colour.dtype != np.uint8 or colour.shape != (*height.shape, 3):
        raise ValueError("Source RGB must be uint8 with shape (height, width, 3).")
    if mask.shape != height.shape:
        raise ValueError("Validity mask must have the same 2D shape as relief.")
    if mask.dtype != np.bool_:
        try:
            numeric = np.asarray(mask, np.float64)
        except (TypeError, ValueError):
            raise ValueError("Validity mask must be boolean or binary.") from None
        if not np.isfinite(numeric).all() or np.any((numeric != 0) & (numeric != 1)):
            raise ValueError("Validity mask must be boolean or binary.")
    return height, colour, mask.astype(bool, copy=True), amount


def _smoothstep(value):
    value = np.clip(value, 0, 1)
    return value*value*(3-2*value)


def _blur(value, valid, sigma):
    return gaussian_filter(np.where(valid, value, 0), sigma) / np.maximum(
        gaussian_filter(valid.astype(np.float32), sigma), _EPS)


def _legacy_curvature(normals, valid, scale):
    if normals is None:
        return None, "normal_missing"
    try:
        normal = np.asarray(normals, np.float32)
    except (TypeError, ValueError):
        return None, "normal_invalid"
    if normal.ndim != 3 or normal.shape[-1] != 3 or min(normal.shape[:2]) < 2 or not np.isfinite(normal).all():
        return None, "normal_invalid"
    normal = cv2.resize(normal, (valid.shape[1], valid.shape[0]), interpolation=cv2.INTER_LINEAR)
    with np.errstate(over="ignore", invalid="ignore"):
        length = np.linalg.norm(normal, axis=2)
    if not np.isfinite(length).all():
        return None, "normal_invalid_length"
    usable = valid & (length > .1)
    if int(usable.sum()) < .95*int(valid.sum()):
        return None, "normal_insufficient_valid_support"
    normal /= np.maximum(length[..., None], _EPS)
    mean = np.stack([_blur(normal[..., c], usable, max(2., 8*scale)) for c in range(3)], axis=2)
    angular = np.sqrt(np.maximum(_blur(np.sum((normal-mean)**2, axis=2), usable, max(1., 4*scale)), 0))
    angular[~usable] = np.inf
    return angular, "normal_curvature"


def _legacy_structure(rgb, valid, core, angular, scale):
    h, w = valid.shape
    lab = cv2.cvtColor(rgb.astype(np.float32)/255, cv2.COLOR_RGB2LAB)
    data = lab[valid]
    sample = np.ascontiguousarray(data[::max(1, math.ceil(len(data)/30000))])
    k = min(12, len(np.unique(sample, axis=0)))
    if k < 2:
        return np.zeros(valid.shape, np.float32), k, 0., 0.
    cv2.setRNGSeed(0)
    _, _, palette = cv2.kmeans(sample, k, None,
        (cv2.TERM_CRITERIA_EPS|cv2.TERM_CRITERIA_MAX_ITER, 30, .2), 1, cv2.KMEANS_PP_CENTERS)
    best = np.full(valid.shape, np.inf, np.float32)
    for color in palette:
        np.minimum(best, np.sum((lab-color)**2, axis=2), out=best)
    denominator = np.zeros(valid.shape, np.float32)
    for color in palette:
        denominator += np.exp(-(np.sum((lab-color)**2, axis=2)-best)/512.)
    def membership(color):
        return np.exp(-(np.sum((lab-color)**2, axis=2)-best)/512.)/np.maximum(denominator, _EPS)
    small = (max(2, w//4), max(2, h//4))
    weight = cv2.resize(valid.astype(np.float32), small, interpolation=cv2.INTER_AREA)
    norm = gaussian_filter(weight, max(1., 12*scale))
    densities, members = [], []
    for color in palette:
        p = membership(color)
        ps = cv2.resize(np.where(valid, p, 0), small, interpolation=cv2.INTER_AREA)
        members.append(ps/np.maximum(weight, _EPS))
        densities.append(gaussian_filter(ps, max(1., 12*scale))/np.maximum(norm, _EPS))
    densities = np.stack(densities)
    difference = np.sqrt(np.sum((palette[:, None]-palette[None, :])**2, axis=2))
    contrast = np.clip((difference-20)/30, 0, 1)
    radius = max(2, round(20*scale/4))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2*radius+1, 2*radius+1))
    score = np.zeros(valid.shape, np.float32)
    for index, color in enumerate(palette):
        eroded = cv2.erode(np.where(weight > .01, members[index], 1).astype(np.float32), kernel)
        opened = cv2.dilate(np.where(weight > .01, eroded, 0).astype(np.float32), kernel)
        opened = cv2.resize(opened, (w, h), interpolation=cv2.INTER_LINEAR)
        narrow = np.maximum(membership(color)-opened, 0)
        evidence = np.sum(densities*contrast[index, :, None, None], axis=0)
        local = cv2.resize((1-densities[index])**2*evidence, (w, h), interpolation=cv2.INTER_LINEAR)
        score += narrow*local
    raw = np.where(core, -np.expm1(-2*score), 0).astype(np.float32)
    raw = np.where(core, -np.expm1(-4*np.maximum(raw-_blur(raw, valid, max(4., 32*scale)), 0)), 0).astype(np.float32)
    angular = np.nan_to_num(angular, nan=1., posinf=1., neginf=1.)
    flat = 1/(1+(angular/.05)**4)
    conservative = np.where(core, raw*flat, 0)
    conservative = np.where(core, gaussian_filter(conservative, .45), 0)
    support = float(np.mean(conservative[valid] > .1))
    confidence = float(np.clip((support-.005)/.015, 0, 1))
    confidence = confidence*confidence*(3-2*confidence)
    flat = cv2.resize(np.where(valid, flat, 0), small, interpolation=cv2.INTER_AREA)
    sigma = max(1., 2*scale)
    flat = gaussian_filter(flat, sigma)/np.maximum(gaussian_filter((weight > .01).astype(np.float32), sigma), _EPS)
    feature = np.where(core, raw*cv2.resize(flat, (w, h), interpolation=cv2.INTER_LINEAR), 0)
    feature = np.where(core, gaussian_filter(feature, .8), 0).astype(np.float32)
    return feature*confidence, k, support, confidence


def _luma(rgb, normalized):
    """Avoid a full HxWx3 floating-point copy for large native sources."""
    output = np.empty(rgb.shape[:2], np.float32)
    rows = max(1, 1_048_576//rgb.shape[1])
    weights = np.array([.299, .587, .114], np.float32)
    for y0 in range(0, rgb.shape[0], rows):
        block = rgb[y0:y0+rows].astype(np.float32)
        if normalized:
            block /= 255.
        output[y0:y0+rows] = block @ weights
    return output


def _legacy_bright_field(rgb, valid, normals):
    """Capped soft-colour/normal support for pale paint, never signed geometry.

    This is the historical continuous palette feature, not hard colour labels.
    Masked RGB is reduced before Lab/normal analysis, so outside-mask colour and
    arbitrarily large native sources cannot contaminate or inflate this stage.
    """
    ratio = min(1., BRIGHT_COMPAT_ANALYSIS_MAX_EDGE/max(valid.shape))
    size = (max(2, round(valid.shape[1]*ratio)), max(2, round(valid.shape[0]*ratio)))
    meta = dict(enabled=False, analysis_size=list(size),
        analysis_max_edge=BRIGHT_COMPAT_ANALYSIS_MAX_EDGE,
        normal_guidance_used=False, palette_size=0, scene_support=0., scene_confidence=0.)
    if normals is None:
        meta["reason"] = "normal_missing"
        return None, meta
    weight = cv2.resize(valid.astype(np.float32), size, interpolation=cv2.INTER_AREA)
    mask = weight > .99
    core = distance_transform_edt(np.pad(mask, 1))[1:-1, 1:-1] > 3
    if not core.any():
        meta["reason"] = "no_interior_detail_support"
        return None, meta
    angular, reason = _legacy_curvature(normals, mask, max(size)/1536.)
    if angular is None:
        meta["reason"] = reason
        return None, meta
    colour = np.empty((size[1], size[0], 3), np.uint8)
    for c in range(3):
        channel = cv2.resize(np.where(valid, rgb[..., c], 0).astype(np.float32), size,
                             interpolation=cv2.INTER_AREA)/np.maximum(weight, _EPS)
        colour[..., c] = np.rint(np.clip(channel, 0, 255)).astype(np.uint8)
    field, palette, support, confidence = _legacy_structure(colour, mask, core, angular, max(size)/1536.)
    meta.update(enabled=bool(confidence > 0), normal_guidance_used=True,
                palette_size=palette, scene_support=support, scene_confidence=confidence)
    if confidence == 0:
        meta["reason"] = "insufficient_scene_support"
        return None, meta
    return field, meta


def _merge_bright_fallback(primary_signed, fallback_positive, positive_context):
    """Max, never sum; even the faintest existing incision is immutable."""
    primary = np.asarray(primary_signed, np.float32)
    fallback = np.clip(np.asarray(fallback_positive, np.float32), 0, BRIGHT_COMPAT_MAX_ABS)
    fallback = fallback*np.clip(np.asarray(positive_context, np.float32), 0, 1)
    return np.where(primary < 0, primary, np.maximum(primary, fallback)).astype(np.float32)


def _source_edge(y, valid, core, scale):
    fine_sigma, coarse_sigma = max(.55, .65*scale), max(1.6, 1.95*scale)
    def gradient_at(sigma):
        gy, gx = np.gradient(_blur(y, valid, sigma))
        return np.sqrt(gx*gx+gy*gy)
    fine, coarse = gradient_at(fine_sigma), gradient_at(coarse_sigma)
    def step_response(sigma):
        return math.erf(1.5/(math.sqrt(2)*sigma))+math.erf(.5/(math.sqrt(2)*sigma))
    ideal = step_response(fine_sigma)/step_response(coarse_sigma)
    edge = _smoothstep((fine/np.maximum(coarse, 1e-5)/ideal-.68)/.27)
    edge *= _smoothstep((fine*max(1., scale)-.012)/.035)
    return np.where(core, edge, 0).astype(np.float32)


def _area_backgrounds(value, areas, lower, upper):
    """Finite grayscale threshold stack with continuous component-area taper.

    Only one component-label map is live per level, not a stack of 255 maps.
    Quantization is analysis-only; source residual amplitude is native float.
    """
    encoded = np.rint(np.clip((value-lower)/(upper-lower)*255, 0, 255)).astype(np.uint8)
    results = [np.full(value.shape, lower, np.float32) for _ in areas]
    step = (upper-lower)/255
    for level in range(1, 256):
        _, labels, stats, _ = cv2.connectedComponentsWithStats(
            (encoded >= level).astype(np.uint8), connectivity=8)
        population = stats[:, cv2.CC_STAT_AREA].astype(np.float32)
        population[0] = 0
        count = population[labels]
        for area, result in zip(areas, results):
            result += step*_smoothstep((count/area-.5)/.5)
    return results


def _ink_fields(rgb, valid):
    """Contrast-level persistence modulates amplitude, never feature support."""
    ratio = min(1., INK_ANALYSIS_MAX_EDGE/max(valid.shape))
    size = (max(2, round(valid.shape[1]*ratio)), max(2, round(valid.shape[0]*ratio)))
    weight = cv2.resize(valid.astype(np.float32), size, interpolation=cv2.INTER_AREA)
    luma = _luma(rgb, False)
    small = cv2.resize(np.where(valid, luma, 0), size, interpolation=cv2.INTER_AREA)
    del luma
    small /= np.maximum(weight, _EPS)
    core = distance_transform_edt(np.pad(weight > .99, 1))[1:-1, 1:-1] > 2
    min_area = max(3., 4*(max(size)/1024.)**2)
    max_area = .06*int(core.sum())
    fields = []
    for bright in (True, False):
        value = small if bright else 255-small
        confidence = np.zeros_like(small, np.float32)
        for threshold in range(24, 233, 8):
            lower = ((value >= threshold-24) & core).astype(np.uint8)
            middle = ((value >= threshold) & core).astype(np.uint8)
            higher = (value >= threshold+24) & core
            _, low_labels, low_stats, _ = cv2.connectedComponentsWithStats(lower, connectivity=8)
            count, labels, stats, _ = cv2.connectedComponentsWithStats(middle, connectivity=8)
            areas = stats[:, cv2.CC_STAT_AREA].astype(np.float64)
            parents = np.rint(np.bincount(labels.ravel(), weights=low_labels.ravel(),
                minlength=count)/np.maximum(areas, 1)).astype(np.int32)
            survivors = np.bincount(labels.ravel(), weights=higher.ravel(), minlength=count)
            parent_areas = low_stats[parents, cv2.CC_STAT_AREA]
            score = _smoothstep((survivors/np.maximum(parent_areas, 1)-.65)/.30)
            score *= _smoothstep((areas-min_area)/max(1., min_area))
            score *= _smoothstep((max_area-parent_areas)/max(1., max_area*.25))
            score[0] = 0
            np.maximum(confidence, score[labels], out=confidence)
        fields.append(confidence)
    return fields, size


def _analysis(y, valid, scale):
    ratio = min(1., ANALYSIS_MAX_EDGE/max(valid.shape))
    size = (max(2, round(valid.shape[1]*ratio)), max(2, round(valid.shape[0]*ratio)))
    weight = cv2.resize(valid.astype(np.float32), size, interpolation=cv2.INTER_AREA)
    small = cv2.resize(np.where(valid, y, 0), size, interpolation=cv2.INTER_AREA)/np.maximum(weight, _EPS)
    trusted = weight > .99
    stable = distance_transform_edt(np.pad(trusted, 1))[1:-1, 1:-1] > 3
    if stable.any():
        trusted = stable
    elif not trusted.any():
        trusted = weight > 0
    nearest = distance_transform_edt(~trusted, return_distances=False, return_indices=True)
    small = small[tuple(nearest)]
    areas = [max(4., a*(max(size)/512)**2) for a in (16, 64, 256, 1024, 4096, 16384, 65536)]
    radii = [max(1, round(math.sqrt(a/math.pi))) for a in areas]
    light = _area_backgrounds(small, areas, math.log(.04), math.log(1.04))
    dark = _area_backgrounds(-small, areas, -math.log(1.04), -math.log(.04))
    encoded = np.rint(np.clip(np.exp(small)-.04, 0, 1)*255).astype(np.uint8)
    context_radius = min(radii[-2], max(1, round(16*scale*ratio)))
    contexts = []
    for multiplier in (4, 8):
        median = cv2.medianBlur(encoded, multiplier*context_radius+1).astype(np.float32)
        # A raw median can switch abruptly between material colours even where
        # source RGB is constant. Smooth context, not source or base geometry.
        contexts.append(gaussian_filter(median, max(1., 4*scale*ratio))/255.)
    return dict(source=small, light=light, dark=dark, contexts=contexts,
                radii=[max(1, round(r/ratio)) for r in radii], size=size)


def _resize_tile(field, shape, box):
    """Pixel-center bilinear resize crop without allocating the full native map."""
    h, w = shape
    y0, y1, x0, x1 = box
    if box == (0, h, 0, w):
        return cv2.resize(field, (w, h), interpolation=cv2.INTER_LINEAR)
    sy = (np.arange(y0, y1, dtype=np.float64)+.5)*(field.shape[0]/h)-.5
    sx = (np.arange(x0, x1, dtype=np.float64)+.5)*(field.shape[1]/w)-.5
    iy, ix = np.floor(sy).astype(np.int32), np.floor(sx).astype(np.int32)
    fy, fx = (sy-iy).astype(np.float32)[:, None], (sx-ix).astype(np.float32)[None, :]
    ya, yb = np.clip(iy, 0, field.shape[0]-1), np.clip(iy+1, 0, field.shape[0]-1)
    xa, xb = np.clip(ix, 0, field.shape[1]-1), np.clip(ix+1, 0, field.shape[1]-1)
    top = field[ya[:, None], xa[None, :]]*(1-fx)+field[ya[:, None], xb[None, :]]*fx
    bottom = field[yb[:, None], xa[None, :]]*(1-fx)+field[yb[:, None], xb[None, :]]*fx
    return top*(1-fy)+bottom*fy


def _tile_delta(y, core, edge, shape, box, analysis, ink, scale, bright_compat=None):
    """Only adjacent area bands are native-sized; all analysis stays capped."""
    sample = lambda field: _resize_tile(field, shape, box)
    def bands(index):
        light, dark = analysis["light"][index], analysis["dark"][index]
        return (np.where(core, np.maximum(y-sample(light), 0), 0),
                np.where(core, np.maximum(-y-sample(dark), 0), 0),
                sample(np.maximum(analysis["source"]-light, 0)))
    lifted, incised, medium = np.zeros_like(y), np.zeros_like(y), np.zeros_like(y)
    current, previous_coherent = bands(0), None
    last = None
    for index, radius in enumerate(analysis["radii"][:-1]):
        following = bands(index+1)
        reach = min(radius, max(1, round(32*scale)))
        support = maximum_filter(edge, size=2*reach+3, mode="nearest")
        for polarity, destination in ((0, lifted), (1, incised)):
            small, large = current[polarity], following[polarity]
            persistence = _smoothstep((small/np.maximum(large, 1e-5)-.45)/.45)
            np.maximum(destination, small*persistence*support, out=destination)
        if index > 0 and radius >= 4*scale:
            small, large = current[2], following[2]
            persistence = _smoothstep((small/np.maximum(large, 1e-5)-.45)/.45)
            novelty = np.maximum(small-previous_coherent, 0)/np.maximum(small, 1e-5)
            novelty = gaussian_filter(novelty, max(.5, .4*scale))
            np.maximum(medium, small*persistence*support*_smoothstep((novelty-.3)/.5), out=medium)
        previous_coherent = current[2]
        last, current = current, following
    near, far = [y-np.log(.04+sample(field)) for field in analysis["contexts"]]
    relative = np.where(near > 0, np.maximum(np.minimum(near, far), 0),
                        np.minimum(np.maximum(near, far), 0))
    lifted *= _smoothstep((relative-.08)/.25)
    incised *= _smoothstep((-relative-.08)/.25)
    bright_ink, dark_ink = [sample(field) for field in ink]
    dark = MAX_DARK_ABS*np.tanh(np.maximum(-relative, 0)/.7)*_smoothstep((incised-.60)/.7)
    fine = (THIN_BRIGHT_ABS+(MAX_BRIGHT_ABS-THIN_BRIGHT_ABS)*bright_ink)
    fine *= np.tanh(np.maximum(relative, 0)/.7)*_smoothstep((lifted-.55)/.7)
    raised = MEDIUM_BRIGHT_ABS*np.tanh(np.maximum(relative, 0)/.5)*_smoothstep((medium-.08)/.20)
    raised *= _smoothstep((relative-.08)/.16)
    region = (last[2] > .09) & (relative > .10) & core
    width = _smoothstep((distance_transform_edt(region)-2*scale)/(2*scale))
    width = maximum_filter(width, size=2*max(1, round(12*scale))+1)
    raised *= width
    dark_region = (last[1] > .60) & (relative < -.60) & core
    dark_width = _smoothstep((distance_transform_edt(dark_region)-2*scale)/(2*scale))
    # Local distance cannot lend a thick eye's deep carving to neighbouring fur.
    # Stable flat ink can still remain legible even when its strokes are thin.
    dark_width = np.maximum(dark_width, dark_ink)
    dark *= THIN_DARK_ABS/MAX_DARK_ABS+(1-THIN_DARK_ABS/MAX_DARK_ABS)*dark_width
    delta = _blur(np.maximum(fine, raised)-dark, core, 1.1)
    if bright_compat is not None:
        feature = sample(bright_compat)
        context = _smoothstep((np.maximum(relative, 0)-.015)/.075)
        region = (feature > .1) & (relative > .015) & core
        width = _smoothstep((distance_transform_edt(region)-2*scale)/(2*scale))
        cap = BRIGHT_COMPAT_THIN_ABS+(BRIGHT_COMPAT_MAX_ABS-BRIGHT_COMPAT_THIN_ABS)*np.maximum(width, bright_ink)
        # Compatibility cannot borrow a distant nose edge to raise a smooth
        # highlight. Its support reach is half the primary signed layer's.
        reach = max(1, round(BRIGHT_COMPAT_EDGE_REACH*scale))
        support = maximum_filter(edge, size=2*reach+3, mode="nearest")
        fallback = np.where(core, cap*np.tanh(.18*feature/cap)*support*context, 0)
        return np.where(core, delta, 0).astype(np.float32), fallback
    return np.where(core, delta, 0).astype(np.float32)


def apply_fullprint_detail(relief, rgb, valid, normals=None, *, source_detail=0., mode="ornament"):
    """Return source-sized float32 height and deterministic JSON-safe metadata.

    ``normals`` affects only optional pale-bright compatibility, never the signed
    primary layer. Missing/invalid normals keep primary marks without fallback.
    Off/depth mode is exact; a three-pixel validity border is never adjusted.
    Large images use overlapped native tiles, not upscaled low-resolution depth.
    """
    height, rgb, valid, amount = _inputs(relief, rgb, valid, source_detail, mode)
    result = np.where(valid, height, 0).astype(np.float32, copy=True)
    ratio = min(1., ANALYSIS_MAX_EDGE/max(valid.shape))
    size = [max(2, round(valid.shape[1]*ratio)), max(2, round(valid.shape[0]*ratio))]
    metadata = dict(algorithm=ALGORITHM, version=VERSION, mode=mode, source_detail=amount,
        enabled=False, physical_depth_claim=False, normal_guidance_used=False,
        primary_normal_guidance_used=False,
        source="source RGB only; never a reference target",
        feature="decorative local dark incisions and bright raised marks",
        analysis_size=size, analysis_max_edge=ANALYSIS_MAX_EDGE,
        native_size=[int(valid.shape[1]), int(valid.shape[0])],
        native_output_preserved=True, effective_source_scale=min(max(valid.shape)/512., MAX_SUPPORT_SCALE),
        maximum_support_scale=MAX_SUPPORT_SCALE,
        ink_analysis_max_edge=INK_ANALYSIS_MAX_EDGE, area_threshold_levels=255,
        ink_threshold_spacing=8, ink_contrast_interval=48,
        ink_policy="threshold persistence changes amplitude only, not feature support",
        context_policy="near/far source median agreement with 4-world-pixel context smoothing",
        source_edge_policy="finite multiscale sharp-edge support; no normal/global scene veto",
        maximum_dark_cue=MAX_DARK_ABS, maximum_bright_cue=MAX_BRIGHT_ABS,
        thin_dark_cue=THIN_DARK_ABS, thin_bright_cue=THIN_BRIGHT_ABS,
        medium_bright_cue=MEDIUM_BRIGHT_ABS, source_bevel_sigma_pixels=1.1,
        mask_edge_guard_pixels=3, maximum_adjustment=0., applied_adjustment_rms=0.,
        native_tiles=0, native_band_policy="adjacent band pairs, bounded native tiles")
    metadata.update(bright_compatibility=dict(enabled=False, normal_guidance_used=False,
        analysis_max_edge=BRIGHT_COMPAT_ANALYSIS_MAX_EDGE),
        bright_compatibility_policy="positive near/far context; max after float32 headroom; every observable primary incision preserved",
        bright_compatibility_maximum=BRIGHT_COMPAT_MAX_ABS,
        bright_compatibility_thin_maximum=BRIGHT_COMPAT_THIN_ABS,
        bright_compatibility_edge_reach=BRIGHT_COMPAT_EDGE_REACH,
        bright_compatibility_cap_policy="local thickness or stable bright ink, never borrowed thickness")
    if mode != "ornament" or amount == 0 or not valid.any():
        metadata["fallback_reason"] = "depth_mode_selected" if mode != "ornament" else "source_detail_disabled" if amount == 0 else "empty_valid_mask"
        return result, metadata
    if min(size) < 4:
        metadata["fallback_reason"] = "source_too_thin_for_two_dimensional_detail_analysis"
        return result, metadata
    core = distance_transform_edt(np.pad(valid, 1))[1:-1, 1:-1] > 3
    if not core.any():
        metadata["fallback_reason"] = "no_interior_detail_support"
        return result, metadata
    first = rgb.reshape(-1, 3)[int(np.argmax(valid))]
    if all(np.all(np.where(valid, rgb[..., c], first[c]) == first[c]) for c in range(3)):
        metadata["fallback_reason"] = "constant_source"
        return result, metadata
    scale = min(max(valid.shape)/512., MAX_SUPPORT_SCALE)
    y = np.log(.04+_luma(rgb, True))
    y = _blur(y, valid, .45)
    edge = _source_edge(y, valid, core, scale)
    if not np.any(edge):
        metadata["fallback_reason"] = "no_sharp_source_support"
        return result, metadata
    analysis = _analysis(y, valid, scale)
    ink, ink_size = _ink_fields(rgb, valid)
    metadata["ink_analysis_size"] = list(ink_size)
    bright_compat, compat_meta = _legacy_bright_field(rgb, valid, normals)
    metadata["bright_compatibility"] = compat_meta
    metadata["normal_guidance_used"] = bool(compat_meta["normal_guidance_used"])
    h, w = valid.shape
    tile_edge = max(h, w) if valid.size <= _WHOLE_NATIVE_PIXELS else _TILE_EDGE
    # Native sharp-edge reach dominates; add novelty and final bevel support.
    halo = int(math.ceil(32*scale+4*max(.5, .4*scale)+8))
    for y0 in range(0, h, tile_edge):
        for x0 in range(0, w, tile_edge):
            y1, x1 = min(h, y0+tile_edge), min(w, x0+tile_edge)
            box = (max(0, y0-halo), min(h, y1+halo), max(0, x0-halo), min(w, x1+halo))
            ay, by, ax, bx = box
            fields = _tile_delta(y[ay:by, ax:bx], core[ay:by, ax:bx], edge[ay:by, ax:bx],
                                valid.shape, box, analysis, ink, scale, bright_compat)
            delta, fallback = fields if bright_compat is not None else (fields, None)
            delta = delta[y0-ay:y1-ay, x0-ax:x1-ax]*amount
            target = result[y0:y1, x0:x1]
            original = target.copy() if fallback is not None else None
            headroom = np.maximum(np.where(delta >= 0, .97-target, target-.03), 0)
            target += headroom*np.tanh(delta/np.maximum(headroom, _EPS))
            if fallback is not None:
                fallback = fallback[y0-ay:y1-ay, x0-ax:x1-ax]*amount
                room = np.maximum(.97-original, 0)
                candidate = (original+room*np.tanh(fallback/np.maximum(room, _EPS))).astype(np.float32)
                # Compare observable float32 heights, not sub-ULP signed dust:
                # a zero primary change must not punch a hole in pale paint.
                target[:] = original+_merge_bright_fallback(target-original, candidate-original, 1.)
            metadata["native_tiles"] += 1
    actual = (result-height)[core]
    metadata.update(enabled=bool(np.any(actual)), maximum_adjustment=float(np.max(np.abs(actual))),
                    applied_adjustment_rms=float(np.sqrt(np.mean(actual**2))))
    return result, metadata


__all__ = ["apply_fullprint_detail", "ALGORITHM", "VERSION", "MAX_PRINT_ABS", "MAX_SIGNED_ABS"]
