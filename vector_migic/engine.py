"""Offline raster tracing for Vector Migic.

VTracer fits real vector paths. This module never uploads input or downloads a
model. It accepts raster inputs only and exports path-only SVG, PDF and EPS.
"""
from __future__ import annotations

from collections import Counter, deque
from copy import deepcopy
import ctypes
import hashlib
from io import BytesIO
import math
import os
from pathlib import Path
import re
import tempfile
import time
import subprocess
import sys
from typing import Callable
import warnings
import xml.etree.ElementTree as ET

from PIL import Image, ImageChops, ImageFilter, ImageMath, ImageOps, UnidentifiedImageError
import vtracer

MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_INPUT_PIXELS = 40_000_000
MAX_SVG_BYTES = 64 * 1024 * 1024
ALLOWED_RASTER_FORMATS = frozenset({"PNG", "JPEG", "WEBP", "BMP", "TIFF"})
SVG_NS = "http://www.w3.org/2000/svg"
PRESETS = {
    "logo": {"colors": 16, "color_precision": 8, "speckle": 1,
             "smoothing": 0.5, "corner_threshold": 60},
    "illustration": {"colors": 32, "color_precision": 7, "speckle": 4,
                     "smoothing": 0.45, "corner_threshold": 60},
    "photo": {"colors": 64, "color_precision": 7, "speckle": 8,
              "smoothing": 0.55, "corner_threshold": 75},
}
DEFAULT_OPTIONS = {"preset": "logo", "color_mode": "color", **PRESETS["logo"],
                   "mode": "spline", "max_dimension": 2048,
                   "transparent": True, "alpha_threshold": 128,
                   "bw_threshold": 128}
Progress = Callable[[dict], None]
POTRACE_SHA256 = 'f1bcde040ae6576dc9c5868be7a72064957c5aea08e1dece240428319fc672a4'
POTRACE_TIMEOUT_SECONDS = 120
_POTRACE_JOB = None


class VectorizationError(ValueError):
    """A user-correctable input, configuration or conversion error."""


def _number(value, name, low, high, integer=True):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise VectorizationError(f"{name} must be a number.")
    if not math.isfinite(value) or not low <= value <= high:
        raise VectorizationError(f"{name} must be between {low} and {high}.")
    if integer and value != int(value):
        raise VectorizationError(f"{name} must be a whole number.")
    return int(value) if integer else float(value)


def normalize_options(options=None):
    """Return validated options, with the selected preset applied first."""
    if options is None:
        options = {}
    if not isinstance(options, dict):
        raise VectorizationError("Options must be an object.")
    unknown = set(options) - set(DEFAULT_OPTIONS)
    if unknown:
        raise VectorizationError("Unknown option: " + ", ".join(sorted(unknown)))
    preset = options.get("preset", "logo")
    if preset not in PRESETS:
        raise VectorizationError("Preset must be logo, illustration or photo.")
    result = {**DEFAULT_OPTIONS, **PRESETS[preset], **options, "preset": preset}
    if result["color_mode"] not in {"color", "bw"}:
        raise VectorizationError("Color mode must be color or bw.")
    if result["mode"] not in {"spline", "polygon", "none"}:
        raise VectorizationError("Path mode must be spline, polygon or none.")
    for name, low, high in (("colors", 0, 256), ("color_precision", 1, 8),
                            ("speckle", 0, 128), ("corner_threshold", 0, 180),
                            ("max_dimension", 64, 4096),
                            ("alpha_threshold", 1, 255), ("bw_threshold", 1, 255)):
        result[name] = _number(result[name], name, low, high)
    if result["colors"] == 1:
        raise VectorizationError("Colors must be 0 (automatic) or 2 through 256.")
    result["smoothing"] = _number(result["smoothing"], "smoothing", 0, 1, False)
    if not isinstance(result["transparent"], bool):
        raise VectorizationError("transparent must be true or false.")
    return result


def _read_raster(input_path):
    path = Path(input_path)
    try:
        if not path.is_file():
            raise VectorizationError("Input image does not exist.")
        if path.stat().st_size > MAX_FILE_BYTES:
            raise VectorizationError("Input image exceeds the 64 MiB file limit.")
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(path) as opened:
                fmt = opened.format
                if fmt not in ALLOWED_RASTER_FORMATS:
                    raise VectorizationError("Use a PNG, JPG, WebP, BMP or TIFF image.")
                width, height = opened.size
                if width < 1 or height < 1 or width * height > MAX_INPUT_PIXELS:
                    raise VectorizationError("Input image exceeds the 40 megapixel limit.")
                notes = []
                if getattr(opened, "n_frames", 1) > 1:
                    notes.append("Only the first frame or TIFF page is traced.")
                source_mode = opened.mode
                had_alpha = "A" in opened.getbands() or "transparency" in opened.info
                # Copy pixel data into a fresh image; EXIF and unrelated metadata
                # never enter output SVG/PDF/EPS files.
                opened.load()
                oriented = ImageOps.exif_transpose(opened)
                rgba = oriented.convert("RGBA")
                rgba = Image.frombytes("RGBA", rgba.size, rgba.tobytes())
                return rgba, {"width": rgba.width, "height": rgba.height,
                              "format": fmt, "mode": source_mode,
                              "has_alpha": had_alpha, "warnings": notes}
    except VectorizationError:
        raise
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError,
            Image.DecompressionBombWarning, ValueError) as exc:
        raise VectorizationError("This image could not be decoded safely.") from exc


def inspect_image(input_path):
    image, info = _read_raster(input_path)
    image.close()
    return info


def _atomic_write(path, data):
    path = Path(path)
    if not path.parent.is_dir():
        raise VectorizationError("Output folder does not exist.")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".vector-migic-",
                                         suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def create_preview(input_path, out_png, max_dimension=1600):
    max_dimension = _number(max_dimension, "max_dimension", 64, 4096)
    image, info = _read_raster(input_path)
    try:
        image.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
        data = BytesIO()
        image.save(data, format="PNG")
        _atomic_write(out_png, data.getvalue())
        return {**info, "preview_width": image.width, "preview_height": image.height,
                "output_bytes": len(data.getvalue())}
    finally:
        image.close()


def _report(callback, progress, message):
    if callback is not None:
        callback({"progress": progress, "message": message})


def _logo_palette(image, limit):
    """Find repeated flat colors without treating blended JPEG edges as inks."""
    histogram = image.getcolors(65536)
    if histogram is None:
        return None
    histogram = sorted(((count, color[:3]) for count, color in histogram if color[3] >= 128), reverse=True)
    total = sum(count for count, _color in histogram)
    if not total:
        return None
    def distance(a, b):
        return sum((x-y)**2 for x,y in zip(a,b))
    rgb = image.convert('RGB')
    span = ImageChops.subtract(rgb.filter(ImageFilter.MaxFilter(3)), rgb.filter(ImageFilter.MinFilter(3)))
    red, green, blue = span.split()
    largest = ImageChops.lighter(ImageChops.lighter(red, green), blue)
    interior = ImageChops.multiply(largest.point(lambda value: 255 if value <= 12 else 0),
                                   image.getchannel('A').point(lambda value: 255 if value >= 128 else 0))
    rgba = rgb.convert('RGBA')
    rgba.putalpha(interior)
    flat_histogram = rgba.getcolors(65536)
    if flat_histogram is None:
        return None
    bins = {}
    for count, color in flat_histogram:
        if color[3] == 0:
            continue
        key = tuple(channel // 8 for channel in color[:3])
        weight, sums = bins.get(key, (0, [0,0,0]))
        bins[key] = weight + count, [s + count * c for s,c in zip(sums, color[:3])]
    groups = []
    for weight, sums in sorted(bins.values(), reverse=True):
        if weight < 4:
            continue
        color = tuple(round(value / weight) for value in sums)
        nearest = min(range(len(groups)), key=lambda i: distance(color, groups[i][1]), default=None)
        if nearest is not None and distance(color, groups[nearest][1]) < 32**2:
            previous_weight, previous = groups[nearest]
            combined = previous_weight + weight
            groups[nearest] = combined, tuple(round((a*previous_weight+b*weight)/combined) for a,b in zip(previous,color))
        elif len(groups) < limit:
            groups.append((weight, color))
    colors = [color for _weight, color in sorted(groups, reverse=True)]
    if not colors:
        return None
    # A small distinct ink can be real even when its stroke is only one pixel
    # wide. Keep it unless its color can be explained by edge blending between
    # the already established flat colors.
    def is_blend(color):
        if min(distance(color, previous) for previous in colors) <= 20**2:
            return True
        for i, a in enumerate(colors):
            for b in colors[i+1:]:
                length = distance(a,b)
                if not length:
                    continue
                t = max(0., min(1., sum((c-x)*(y-x) for c,x,y in zip(color,a,b))/length))
                if sum((c-(x+t*(y-x)))**2 for c,x,y in zip(color,a,b)) <= 40**2:
                    return True
        return False
    # A one-pixel gray mark on white can have the same RGB as antialiasing
    # beside a black edge. Retain a repeated mark spatially separated from
    # the two endpoint inks; do not promote the ordinary edge blend itself.
    endpoint_fields = {}
    separate_checks = 0
    def separate_mark(color):
        nonlocal separate_checks
        # Neutral lettering needs this conservative ambiguity guard. Do not
        # promote chroma-shifted JPEG edge samples into additional logo inks.
        if max(color)-min(color)>4 or separate_checks >= 32 or min(distance(color,c) for c in colors)<=64**2:
            return False
        separate_checks += 1
        pairs=[]
        for i,a in enumerate(colors):
            for b in colors[i+1:]:
                length=distance(a,b)
                t=sum((c-x)*(y-x) for c,x,y in zip(color,a,b))/length if length else 0
                error=sum((c-(x+t*(y-x)))**2 for c,x,y in zip(color,a,b))
                if 0<t<1 and error<=12**2:pairs.append((error,-length,a,b))
        if not pairs:return False
        difference=ImageChops.difference(rgb,Image.new('RGB',rgb.size,color))
        r,g,b=difference.split()
        exact=ImageChops.lighter(ImageChops.lighter(r,g),b).point(lambda v:255 if v==0 else 0)
        mixed=Image.new('L',rgb.size,0)
        for _error,_length,a,b in sorted(pairs):
            for endpoint in (a,b):
                if endpoint not in endpoint_fields:
                    squared=_rgb_distance_image(rgb,Image.new('RGB',rgb.size,endpoint))
                    close=ImageMath.lambda_eval(lambda v:(v['d']<=40**2)*255,d=squared).convert('L')
                    endpoint_fields[endpoint]=ImageChops.multiply(close,image.getchannel('A')).filter(ImageFilter.MaxFilter(7))
            mixed=ImageChops.lighter(mixed,ImageChops.multiply(endpoint_fields[a],endpoint_fields[b]))
            distinct=ImageChops.multiply(ImageChops.subtract(exact,mixed),image.getchannel('A'))
            if distinct.histogram()[255]<max(3,total*.00005):return False
        return distinct.histogram()[255]>=max(3,total*.00005)
    for count, color in histogram:
        if len(colors) == limit or count < max(3, total * .00005):
            break
        if not is_blend(color) or separate_mark(color):
            colors.append(color)
    covered = sum(count for count, color in histogram if min(distance(color, previous) for previous in colors) < 24**2)
    if covered < .88 * total:
        return None  # A photograph or gradient is not a flat-color logo.
    return colors


def _exact_mask(image,color):
    bands=ImageChops.difference(image,Image.new('RGBA',image.size,color)).split()
    maximum=bands[0]
    for band in bands[1:]:maximum=ImageChops.lighter(maximum,band)
    return maximum.point(lambda value:255 if value==0 else 0)

def _safe_background_fragments(mask,core,unsupported):
    """Allow short corner tips, but protect narrow holes and long thin gaps."""
    if unsupported.histogram()[255]>4096:return False
    width,height=mask.size
    remaining={i for i,value in enumerate(unsupported.tobytes()) if value}
    source=mask.tobytes();trusted=core.tobytes()
    while remaining:
        start=remaining.pop();todo=[start];component=[start]
        while todo:
            p=todo.pop();x,y=p%width,p//width
            for dx,dy in ((1,0),(-1,0),(0,1),(0,-1),(1,1),(-1,1),(1,-1),(-1,-1)):
                xx,yy=x+dx,y+dy;q=yy*width+xx
                if 0<=xx<width and 0<=yy<height and q in remaining:
                    remaining.remove(q);todo.append(q);component.append(q)
        xs=[p%width for p in component];ys=[p//width for p in component]
        if max(max(xs)-min(xs),max(ys)-min(ys))>=3:return False
        # A tiny detached counter has no 3x3 core. It must retain AA2 even
        # though its bbox resembles an unimportant single corner pixel.
        todo=deque([start]);seen={start};connected=False
        while todo and len(seen)<=4096:
            p=todo.popleft()
            if trusted[p]:connected=True;break
            x,y=p%width,p//width
            for dx,dy in ((1,0),(-1,0),(0,1),(0,-1)):
                xx,yy=x+dx,y+dy;q=yy*width+xx
                if 0<=xx<width and 0<=yy<height and source[q] and q not in seen:
                    seen.add(q);todo.append(q)
        if not connected:return False
    return True

def _safe_aliased_grid(image,palette):
    """Native tracing is safe only for hard inks with supported thickness."""
    histogram=image.getcolors(257)
    if not palette or histogram is None:return False
    if any(color[3] not in (0,255) or (color[3] and color[:3] not in palette) for count,color in histogram):return False
    width,height=image.size
    pixels=image.load()
    border=Counter([pixels[x,0] for x in range(width)]+[pixels[x,height-1] for x in range(width)]+
                   [pixels[0,y] for y in range(height)]+[pixels[width-1,y] for y in range(height)])
    background=border.most_common(1)[0][0]
    for count,color in histogram:
        mask=_exact_mask(image,color);core=mask.filter(ImageFilter.MinFilter(3))
        unsupported=ImageChops.subtract(mask,core.filter(ImageFilter.MaxFilter(7)))
        if not unsupported.getbbox():continue
        if color!=background or not _safe_background_fragments(mask,core,unsupported):return False
    return True

def _color_distance(a,b):
    return sum((x-y)**2 for x,y in zip(a,b))


def _blend_inks(palette):
    """Identify inks that could also occur as blends of two other inks."""
    blends={}
    for color in palette:
        others=[c for c in palette if c!=color]
        for i,a in enumerate(others):
            for b in others[i+1:]:
                denom=_color_distance(a,b)
                t=sum((c-x)*(y-x) for c,x,y in zip(color,a,b))/denom if denom else 0
                if .05<t<.95 and sum((c-(x+t*(y-x)))**2 for c,x,y in zip(color,a,b))<12**2:
                    blends[color]=(a,b);break
            if color in blends:break
    return blends


def _rgb_distance_image(rgb,candidate):
    r,g,b=rgb.split();cr,cg,cb=candidate.split()
    return ImageMath.lambda_eval(lambda a:(a['r']-a['cr'])**2+(a['g']-a['cg'])**2+(a['b']-a['cb'])**2,
                                 r=r,g=g,b=b,cr=cr,cg=cg,cb=cb)


def _local_ink_assignment(rgb, alpha, palette, working_scale, source_rgb=None, source_alpha=None):
    """Use nearby trusted inks instead of unrelated global palette colors.

    For example, gray can be a real fill elsewhere in a logo and also appear
    as antialiasing between black and white. Only flat gray interiors seed
    that ink; distinct thin accent inks can seed from their exact color.
    Per-color distance fields are bounded by the validated processing canvas.
    """
    if len(palette)<3 or len(palette)>16:
        return None
    blends=_blend_inks(palette)
    span=ImageChops.subtract(rgb.filter(ImageFilter.MaxFilter(3)),rgb.filter(ImageFilter.MinFilter(3)))
    r,g,b=span.split();maximum=ImageChops.lighter(ImageChops.lighter(r,g),b)
    flat=ImageChops.multiply(maximum.point(lambda n:255 if n<=12 else 0),alpha)
    radius=max(1,round(3*working_scale))
    fixed=Image.new('P',(1,1))
    fixed.putpalette([channel for color in palette+[palette[-1]]*(256-len(palette)) for channel in color])
    fallback=rgb.quantize(palette=fixed,dither=Image.Dither.NONE).convert('RGB')
    source_rgb=source_rgb if source_rgb is not None else rgb
    source_alpha=source_alpha if source_alpha is not None else alpha
    endpoint_seeds={}
    trusted={}
    for color in palette:
        dist=_rgb_distance_image(rgb,Image.new('RGB',rgb.size,color))
        close=ImageMath.lambda_eval(lambda a:(a['d']<=20**2)*255,d=dist).convert('L')
        trusted[color]=ImageChops.multiply(close,flat if color in blends else alpha)
    distances=[]
    first=Image.new('L',rgb.size,255);second=first.copy()
    for color in palette:
        seeds=trusted[color]
        if color in blends:
            a,b=blends[color]
            for endpoint in (a,b):
                if endpoint not in endpoint_seeds:
                    d=_rgb_distance_image(source_rgb,Image.new('RGB',source_rgb.size,endpoint))
                    close=ImageMath.lambda_eval(lambda v:(v['d']<=64**2)*255,d=d).convert('L')
                    endpoint_seeds[endpoint]=ImageChops.multiply(close,source_alpha).filter(ImageFilter.MaxFilter(5))
            mixed=ImageChops.multiply(endpoint_seeds[a],endpoint_seeds[b])
            d=_rgb_distance_image(source_rgb,Image.new('RGB',source_rgb.size,color))
            exact=ImageMath.lambda_eval(lambda v:(v['d']<=12**2)*255,d=d).convert('L')
            distinct=ImageChops.multiply(ImageChops.subtract(exact,mixed),source_alpha)
            if distinct.size!=rgb.size:distinct=distinct.resize(rgb.size,Image.Resampling.NEAREST)
            seeds=ImageChops.lighter(seeds,distinct)
        if not seeds.getbbox():return None
        spatial=Image.new('L',rgb.size,255)
        for d in range(radius+1):
            spatial=Image.composite(Image.new('L',rgb.size,d),spatial,ImageChops.subtract(seeds,spatial.point(lambda n:255 if n!=255 else 0)))
            seeds=seeds.filter(ImageFilter.MaxFilter(3))
        second=ImageChops.darker(second,ImageChops.lighter(first,spatial))
        first=ImageChops.darker(first,spatial)
        distances.append(spatial)
    best=fallback
    best_dist=Image.new('I',rgb.size,1_000_000)
    for color,spatial in zip(palette,distances):
        candidate=Image.new('RGB',rgb.size,color);dist=_rgb_distance_image(rgb,candidate)
        choose=ImageMath.lambda_eval(lambda a:((a['d']<a['best'])*(a['spatial']<=a['second'])*(a['spatial']<255))*255,
                                    d=dist,best=best_dist,spatial=spatial,second=second).convert('L')
        best=Image.composite(candidate,best,choose);best_dist=Image.composite(dist,best_dist,choose)
    return best

def _prepare(image, options, notes):
    input_width=image.width
    logo_palette = (_logo_palette(image, options['colors'] or 32)
                    if options['preset'] == 'logo' and options['color_mode'] == 'color'
                    else None)
    image.thumbnail((options["max_dimension"], options["max_dimension"]),
                    Image.Resampling.LANCZOS)
    ink_source=image.copy() if logo_palette and options['mode']!='none' else None
    if ink_source is not None and not options['transparent']:
        matte=Image.new('RGBA',ink_source.size,'white')
        matte.alpha_composite(ink_source)
        ink_source=matte
    native_grid = (options['mode']!='none' and _safe_aliased_grid(image, logo_palette))
    if options['preset'] == 'logo' and options['mode']!='none' and not native_grid and max(image.size) <= options['max_dimension'] // 2:
        # Interpolate the original blended RGB/alpha before palette reduction.
        # At 2x, color coverage can locate boundaries at half-pixel positions;
        # quantizing first would discard that antialiasing information.
        image = image.resize((image.width * 2, image.height * 2), Image.Resampling.BICUBIC)
    alpha = image.getchannel("A")
    # Vector regions have hard boundaries. Partial alpha must be made explicit,
    # rather than silently changing the exported appearance across formats.
    alpha_values = alpha.getcolors(257)
    has_partial_alpha = alpha_values is None or any(0 < a < 255 for _, a in alpha_values)
    if options["transparent"]:
        alpha = alpha.point(lambda a: 255 if a >= options["alpha_threshold"] else 0)
        if has_partial_alpha:
            notes.append("Partial transparency is converted to solid/transparent edges using the alpha threshold.")
    else:
        white = Image.new("RGBA", image.size, "white")
        white.alpha_composite(image)
        image = white
        alpha = image.getchannel("A")
    if options["color_mode"] == "bw":
        luminance = image.convert("L")
        # Trace dark foreground in color mode so transparent pixels and holes are
        # not turned into black regions by VTracer's binary decoder.
        dark = luminance.point(lambda v: 255 if v < options["bw_threshold"] else 0)
        if options["transparent"]:
            foreground = ImageChops.multiply(dark, alpha)
            image = Image.new("RGBA", image.size, (0, 0, 0, 0))
            image.putalpha(foreground)
        else:
            # When transparency is disabled, keep white regions as actual vector
            # shapes as well; PDF/EPS/SVG then share the same opaque background.
            image = dark.point(lambda value: 255 - value).convert("RGBA")
    else:
        rgb = image.convert("RGB")
        if options["colors"] or logo_palette:
            if logo_palette:
                loss = 8-options['color_precision']
                if loss:
                    logo_palette = [tuple((channel>>loss)<<loss for channel in color) for color in logo_palette]
                assigned = (_local_ink_assignment(rgb, alpha, logo_palette, image.width/input_width,
                            ink_source.convert('RGB'),ink_source.getchannel('A'))
                            if options['mode']!='none' else None)
                if assigned is not None:
                    rgb = assigned
                else:
                    palette_image = Image.new('P', (1,1))
                    palette = logo_palette + [logo_palette[-1]] * (256-len(logo_palette))
                    palette_image.putpalette([channel for color in palette for channel in color])
                    rgb = rgb.quantize(palette=palette_image, dither=Image.Dither.NONE).convert('RGB')
            else:
                rgb = rgb.quantize(colors=options["colors"],
                                   method=Image.Quantize.MEDIANCUT,
                                   dither=Image.Dither.NONE).convert("RGB")
        image = rgb.convert("RGBA")
        image.putalpha(alpha)
    if image.getchannel("A").getbbox() is None:
        raise VectorizationError("No visible foreground remains. Change transparency or B/W threshold.")
    return image


def _ensure_cli_job():
    """Make every owned CLI descendant die if this worker is terminated.

    The job handle intentionally remains open for the worker's lifetime. Closing
    it earlier would terminate the worker itself before its result is delivered.
    Windows closes it even on TerminateProcess, killing all CLI descendants.
    """
    global _POTRACE_JOB
    if _POTRACE_JOB is not None:
        return
    if os.name != 'nt':
        raise VectorizationError('The packaged precision tracer requires Windows.')
    from ctypes import wintypes
    size_t = ctypes.c_size_t
    class BasicLimits(ctypes.Structure):
        _fields_ = [('process_time',ctypes.c_int64),('job_time',ctypes.c_int64),
                    ('flags',wintypes.DWORD),('minimum_working',size_t),('maximum_working',size_t),
                    ('active_processes',wintypes.DWORD),('affinity',size_t),
                    ('priority',wintypes.DWORD),('scheduling',wintypes.DWORD)]
    class Counters(ctypes.Structure):
        _fields_ = [(name,ctypes.c_uint64) for name in ('read_ops','write_ops','other_ops','read_bytes','write_bytes','other_bytes')]
    class ExtendedLimits(ctypes.Structure):
        _fields_ = [('basic',BasicLimits),('io',Counters),('process_memory',size_t),
                    ('job_memory',size_t),('peak_process_memory',size_t),('peak_job_memory',size_t)]
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateJobObjectW.argtypes = (ctypes.c_void_p,wintypes.LPCWSTR)
    kernel.CreateJobObjectW.restype = ctypes.c_void_p
    kernel.SetInformationJobObject.argtypes = (ctypes.c_void_p,ctypes.c_int,ctypes.c_void_p,wintypes.DWORD)
    kernel.SetInformationJobObject.restype = wintypes.BOOL
    kernel.AssignProcessToJobObject.argtypes = (ctypes.c_void_p,ctypes.c_void_p)
    kernel.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = (ctypes.c_void_p,)
    handle = kernel.CreateJobObjectW(None,None)
    if not handle:
        raise VectorizationError('Could not initialize precision tracer process cleanup.')
    limits = ExtendedLimits()
    limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not kernel.SetInformationJobObject(handle,9,ctypes.byref(limits),ctypes.sizeof(limits)) or not kernel.AssignProcessToJobObject(handle,kernel.GetCurrentProcess()):
        kernel.CloseHandle(handle)
        raise VectorizationError('Could not protect precision tracer child processes.')
    _POTRACE_JOB = (kernel,handle)


def _precision_executable():
    resources = Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parent))
    executable = resources / 'vendor/potrace/potrace.exe'
    if not executable.is_file() or hashlib.sha256(executable.read_bytes()).hexdigest() != POTRACE_SHA256:
        raise VectorizationError('The precision tracing component is missing or changed. Repair Vector Migic from the launcher.')
    return executable


def _run_precision_mask(executable, bitmap, output, options, working_scale):
    _ensure_cli_job()
    alphamax = (min(1.333, max(0., options['corner_threshold']/60 * (.8 + .4*options['smoothing'])))
                if options['mode']=='spline' else 0.)
    tolerance = .05 + .3 * options['smoothing']
    arguments = [str(executable),'--svg','--flat','--unit','100',
                 '--alphamax',str(alphamax),'--opttolerance',str(tolerance),
                 '--turdsize',str(round(options['speckle']**2 * working_scale**2)),
                 '--output',str(output),str(bitmap)]
    process = subprocess.Popen(arguments,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
                               stderr=subprocess.PIPE,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    deadline = time.monotonic() + POTRACE_TIMEOUT_SECONDS
    try:
        while True:
            if time.monotonic() >= deadline:
                raise VectorizationError('Precision tracing timed out. Try a smaller processing size.')
            if output.exists() and output.stat().st_size > MAX_SVG_BYTES:
                raise VectorizationError('The vector result exceeds 64 MiB. Reduce colors or processing size.')
            try:
                _stdout, error = process.communicate(timeout=.1)
                break
            except subprocess.TimeoutExpired:
                pass
        if process.returncode:
            raise VectorizationError('The precision tracer could not process this image.')
        if not output.is_file() or output.stat().st_size > MAX_SVG_BYTES:
            raise VectorizationError('The precision tracer produced an invalid result.')
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=3)


def _opaque_color_layers(image, colors):
    """Paint border inks first and preserve deeper foreground underpainting.

    Frequency alone can place background shades underneath inner letters,
    exposing unrelated colors when separately fitted vector boundaries differ.
    Same-depth siblings never underpaint one another.
    """
    inks=[color for count,color in colors]
    fixed=Image.new('P',(1,1))
    fixed.putpalette([channel for color in inks+[inks[-1]]*(256-len(inks)) for channel in color])
    labels=image.convert('RGB').quantize(palette=fixed,dither=Image.Dither.NONE)
    actual=labels.getpalette()
    ranks=[inks.index(tuple(actual[i*3:i*3+3])) for i in range(256)]
    raw=labels.point(ranks,'L').tobytes()
    width,height=image.size
    border=Counter(raw[:width]+raw[-width:]+raw[::width]+raw[width-1::width])
    contacts=Counter()
    previous=None
    for y in range(height):
        row=raw[y*width:(y+1)*width]
        for a,b in zip(row,row[1:]):
            if a!=b:contacts[tuple(sorted((a,b)))]+=1
        if previous is not None:
            for a,b in zip(row,previous):
                if a!=b:contacts[tuple(sorted((a,b)))]+=1
        previous=row
    adjacency={i:set() for i in range(len(inks))}
    for (a,b),count in contacts.items():
        if count>=max(3,round(width*height*.00001)):
            adjacency[a].add(b);adjacency[b].add(a)
    depths={i:0 for i,count in border.items() if count>=3}
    todo=deque(depths)
    while todo:
        parent=todo.popleft()
        for child in adjacency[parent]:
            if child not in depths:depths[child]=depths[parent]+1;todo.append(child)
    for i in range(len(inks)):depths.setdefault(i,100)
    ordered=sorted(range(len(inks)),key=lambda i:(depths[i],-border[i] if depths[i]==0 else colors[i][0]))
    depth_background=ordered[0]
    ordered_colors=[colors[i] for i in ordered]
    return inks, labels, ranks, depths, ordered_colors, depth_background


def _trace_precision(image, options, working_scale, progress=None, work_directory=None):
    executable = _precision_executable()
    rgba_colors = image.getcolors(65536)
    if rgba_colors is None:
        return None
    colors = sorted(((count,color[:3]) for count,color in rgba_colors if color[3]),reverse=True)
    if len(colors)>256:
        return None
    result = ET.Element(f'{{{SVG_NS}}}svg',{'width':str(image.width),'height':str(image.height)})
    rgb, alpha = image.convert('RGB'), image.getchannel('A')
    opaque = alpha.getextrema() == (255,255)
    if opaque:
        inks,labels,ranks,depths,ordered_colors,depth_background = _opaque_color_layers(image, colors)
        # A solid underpainting prevents antialias hairlines where separately
        # traced adjacent colored regions meet. It cannot fill transparent holes.
        background = inks[depth_background]
        ET.SubElement(result,f'{{{SVG_NS}}}path',{'d':f'M0 0 H{image.width} V{image.height} H0 Z',
                      'fill':'#%02X%02X%02X'%background})
    with tempfile.TemporaryDirectory(prefix='vector-migic-masks-',dir=work_directory) as directory:
        folder=Path(directory)
        tasks=[]
        if not opaque and len(colors)>1:
            # A global dominant-ink silhouette can expose that unrelated ink
            # between fitted boundaries elsewhere in transparent artwork.
            # Local support overlaps each region by one working pixel while
            # the original alpha keeps exterior space and true holes clear.
            tasks.extend(('local',color) for count,color in colors)
        for _count,color in (ordered_colors[1:] if opaque else colors):
            tasks.append((False,color))
        for index,(support,color) in enumerate(tasks):
            if support=='local':
                difference=ImageChops.difference(rgb,Image.new('RGB',image.size,color))
                red,green,blue=difference.split()
                maximum=ImageChops.lighter(ImageChops.lighter(red,green),blue)
                exact=ImageChops.multiply(maximum.point(lambda value:255 if value==0 else 0),alpha)
                mask=ImageChops.multiply(exact.filter(ImageFilter.MaxFilter(3)),alpha)
            else:
                difference=ImageChops.difference(rgb,Image.new('RGB',image.size,color))
                red,green,blue=difference.split()
                maximum=ImageChops.lighter(ImageChops.lighter(red,green),blue)
                if opaque:
                    rank=inks.index(color)
                    included={rank}
                    if depths[rank]>0:
                        included.update(i for i in range(len(inks)) if depths[i]>depths[rank])
                    mask=labels.point([255 if value in included else 0 for value in ranks],'L')
                else:
                    mask=ImageChops.multiply(maximum.point(lambda value:255 if value==0 else 0),alpha)
            bitmap,output=folder/f'{index}.pbm',folder/f'{index}.svg'
            ImageOps.invert(mask).convert('1',dither=Image.Dither.NONE).save(bitmap,format='PPM')
            _run_precision_mask(executable,bitmap,output,options,working_scale)
            root=ET.fromstring(output.read_bytes())
            view=list(map(float,root.get('viewBox','').split()))
            if len(view)!=4 or view[2]<=0 or view[3]<=0:
                raise VectorizationError('The precision tracer produced an invalid canvas.')
            wrapper=ET.SubElement(result,f'{{{SVG_NS}}}g',{'transform':f'scale({image.width/view[2]:.12g},{image.height/view[3]:.12g}) translate({-view[0]:.12g},{-view[1]:.12g})'})
            for child in root:
                if child.tag not in {f'{{{SVG_NS}}}g',f'{{{SVG_NS}}}path'}:
                    continue
                for element in child.iter():
                    if element.tag==f'{{{SVG_NS}}}path':
                        element.set('fill','#%02X%02X%02X'%color)
                wrapper.append(child)
            _report(progress,30+round(50*(index+1)/len(tasks)),'Tracing precision vector paths on CPU')
            if len(ET.tostring(result))>MAX_SVG_BYTES:
                raise VectorizationError('The vector result exceeds 64 MiB. Reduce colors or processing size.')
    return ET.tostring(result,encoding='unicode')


def _bounded_cubics(root, tolerance, corner_threshold):
    """Fit closed polygon rings without the native sparse-spline overshoot.

    RDP reduces smooth runs with 53% of the error budget and pins sharp corners.
    Handles use the remaining 47% inside a capsule around each reduced segment.
    The convex-hull property bounds cubic bulges; the two budgets add, rather
    than allowing an unconstrained fit to swing beyond the sampled outline.
    """
    def unit(vector):
        size = math.hypot(*vector)
        return (vector[0] / size, vector[1] / size) if size else (0., 0.)

    def tangent(points, index):
        previous, point, following = points[index - 1], points[index], points[(index + 1) % len(points)]
        before = unit((point[0] - previous[0], point[1] - previous[1]))
        after = unit((following[0] - point[0], following[1] - point[1]))
        cosine = max(-1., min(1., before[0] * after[0] + before[1] * after[1]))
        sharp = math.degrees(math.acos(cosine)) >= corner_threshold
        return None if sharp else unit((before[0] + after[0], before[1] + after[1]))

    def handle(start, end, direction, reverse=False):
        dx, dy = end[0] - start[0], end[1] - start[1]
        length = math.hypot(dx, dy)
        if not length:
            return start
        axis = dx / length, dy / length
        if direction is None:
            return (start[0] + dx / 3., start[1] + dy / 3.)
        sign = -1 if reverse else 1
        vx, vy = direction[0] * sign * length / 3., direction[1] * sign * length / 3.
        along = max(0., min(length, vx * axis[0] + vy * axis[1]))
        limit = tolerance * .47
        across = max(-limit, min(limit, -vx * axis[1] + vy * axis[0]))
        return (start[0] + along * axis[0] - across * axis[1],
                start[1] + along * axis[1] + across * axis[0])

    def fmt(point):
        return f"{point[0]:.4f}".rstrip('0').rstrip('.') + ' ' + f"{point[1]:.4f}".rstrip('0').rstrip('.')

    def simplify(points):
        if tolerance <= 0 or len(points) <= 4:
            return points
        def point_distance(point, a, b):
            vx, vy = b[0]-a[0], b[1]-a[1]
            length = vx*vx+vy*vy
            t = max(0.,min(1.,((point[0]-a[0])*vx+(point[1]-a[1])*vy)/length)) if length else 0.
            return math.hypot(point[0]-a[0]-t*vx,point[1]-a[1]-t*vy)
        pinned = [i for i in range(len(points)) if tangent(points,i) is None]
        if len(pinned) < 2:
            first = pinned[0] if pinned else 0
            opposite = max(range(len(points)), key=lambda i: math.hypot(points[i][0]-points[first][0],points[i][1]-points[first][1]))
            pinned = sorted({first,opposite})
        reduced = []
        for anchor, start in enumerate(pinned):
            end = pinned[(anchor+1)%len(pinned)]
            run = points[start:end+1] if end>start else points[start:]+points[:end+1]
            keep = {0,len(run)-1}
            stack = [(0,len(run)-1)]
            while stack:
                left,right = stack.pop()
                if right-left <= 1:
                    continue
                peak = max(range(left+1,right), key=lambda i: point_distance(run[i],run[left],run[right]))
                if point_distance(run[peak],run[left],run[right]) > tolerance*.53:
                    keep.add(peak);stack.extend(((left,peak),(peak,right)))
            reduced.extend(run[i] for i in sorted(keep)[:-1])
        return reduced if len(reduced)>=3 else points

    for path in root.iter(f"{{{SVG_NS}}}path"):
        data = path.get('d', '')
        tokens = re.findall(r'[A-Za-z]|[-+]?(?:\d*\.\d+|\d+\.?\d*)(?:[eE][-+]?\d+)?', data)
        if any(token.isalpha() and token not in {'M', 'L', 'Z'} for token in tokens):
            raise VectorizationError('Unexpected polygon outline from the tracing engine.')
        rings, ring, index = [], [], 0
        while index < len(tokens):
            command = tokens[index]
            index += 1
            if command in {'M', 'L'}:
                if index + 1 >= len(tokens):
                    raise VectorizationError('Incomplete polygon outline.')
                point = float(tokens[index]), float(tokens[index + 1])
                index += 2
                if command == 'M' and ring:
                    rings.append(ring)
                    ring = []
                if not ring or ring[-1] != point:
                    ring.append(point)
            elif command == 'Z':
                if ring:
                    rings.append(ring)
                    ring = []
            else:
                raise VectorizationError('Unexpected polygon coordinate.')
        if ring:
            rings.append(ring)
        commands = []
        for points in rings:
            if len(points) > 1 and points[-1] == points[0]:
                points = points[:-1]
            if len(points) < 3:
                continue
            points = simplify(points)
            tangents = [tangent(points, i) for i in range(len(points))]
            commands.append('M' + fmt(points[0]))
            for i, start in enumerate(points):
                following = (i + 1) % len(points)
                end = points[following]
                first = handle(start, end, tangents[i])
                second = handle(end, start, tangents[following], reverse=True)
                commands.append('C' + fmt(first) + ' ' + fmt(second) + ' ' + fmt(end))
            commands.append('Z')
        path.set('d', ' '.join(commands))


def _constrain_generated_svg(svg, image, options, curve_tolerance):
    """Discard degenerate native paths and keep a requested palette a limit."""
    root = ET.fromstring(svg)
    for element in list(root):
        if element.tag == f"{{{SVG_NS}}}path" and not element.get('d', '').strip():
            root.remove(element)
    if options['mode'] == 'spline' and options['preset']=='logo':
        _bounded_cubics(root, tolerance=curve_tolerance,
                        corner_threshold=options['corner_threshold'])
    if options['colors'] and options['color_mode'] == 'color':
        colors = image.convert('RGB').getcolors(256)
        if colors:
            palette = [color for _count, color in colors]
            for path in root.iter(f"{{{SVG_NS}}}path"):
                fill = path.get('fill', '')
                if re.fullmatch(r'#[0-9a-fA-F]{6}', fill):
                    color = tuple(int(fill[i:i + 2], 16) for i in (1, 3, 5))
                    closest = min(palette, key=lambda candidate: sum((a-b)**2 for a,b in zip(color, candidate)))
                    path.set('fill', '#%02X%02X%02X' % closest)
    return ET.tostring(root, encoding='unicode')


def _validated_svg(svg_data):
    if isinstance(svg_data, str):
        svg_data = svg_data.encode("utf-8")
    if len(svg_data) > MAX_SVG_BYTES:
        raise VectorizationError("Vector result exceeds 64 MiB. Reduce colors or trace resolution.")
    if b"<!DOCTYPE" in svg_data.upper() or b"<!ENTITY" in svg_data.upper():
        raise VectorizationError("Unsupported SVG declaration.")
    try:
        root = ET.fromstring(svg_data)
    except ET.ParseError as exc:
        raise VectorizationError("Generated SVG is invalid.") from exc
    if root.tag != f"{{{SVG_NS}}}svg":
        raise VectorizationError("Expected an SVG document.")
    # Only the controlled, path-only output of this engine is accepted for export.
    allowed = {f"{{{SVG_NS}}}svg", f"{{{SVG_NS}}}g", f"{{{SVG_NS}}}path"}
    for element in root.iter():
        if element.tag not in allowed:
            raise VectorizationError("Only path-only Vector Migic SVGs may be exported.")
        for name, value in element.attrib.items():
            if name.lower().startswith("on") or "href" in name.lower() or "url(" in value.lower():
                raise VectorizationError("External references are not allowed in vector output.")
    paths = list(root.iter(f"{{{SVG_NS}}}path"))
    if not paths or not all(path.get("d", "").strip() for path in paths):
        raise VectorizationError("No vector paths were produced. Lower the speckle filter.")
    return root, paths


def _export_data(svg_data, output_format):
    _validated_svg(svg_data)
    if output_format == "svg":
        return svg_data if isinstance(svg_data, bytes) else svg_data.encode("utf-8")
    from svglib.svglib import svg2rlg
    from reportlab.graphics import renderPDF, renderPS
    from reportlab.graphics.shapes import Image as DrawingImage
    drawing = svg2rlg(BytesIO(svg_data if isinstance(svg_data, bytes) else svg_data.encode("utf-8")))
    if drawing is None:
        raise VectorizationError("Could not convert SVG paths for export.")
    def reject_raster(node):
        if isinstance(node, DrawingImage):
            raise VectorizationError("Raster images are not allowed in vector exports.")
        for child in getattr(node, "contents", ()):
            reject_raster(child)
    reject_raster(drawing)
    if output_format == "pdf":
        data = renderPDF.drawToString(drawing)
        if not data.startswith(b"%PDF-"):
            raise VectorizationError("PDF export failed.")
        return data
    if output_format == "eps":
        data = renderPS.drawToString(drawing)
        if isinstance(data, str):
            data = data.encode("utf-8")
        if not data.startswith(b"%!PS-Adobe-") or b"EPSF" not in data[:80]:
            raise VectorizationError("EPS export failed.")
        return data
    raise VectorizationError("Export format must be SVG, PDF or EPS.")


def _output_format(output_path, format=None):
    suffix = Path(output_path).suffix.lower().lstrip(".")
    selected = str(format).lower().lstrip(".") if format is not None else suffix
    if selected not in {"svg", "pdf", "eps"} or suffix != selected:
        raise VectorizationError("Output extension must match SVG, PDF or EPS format.")
    return selected


def export_vector(svg_path, output_path, format=None):
    """Export our own path-only SVG to actual SVG/PDF/EPS, without rasterization."""
    selected = _output_format(output_path, format)
    source = Path(svg_path)
    if source.stat().st_size > MAX_SVG_BYTES:
        raise VectorizationError("SVG exceeds the 64 MiB export limit.")
    svg = source.read_bytes()
    _, paths = _validated_svg(svg)
    data = _export_data(svg, selected)
    _atomic_write(output_path, data)
    return {"format": selected, "output_format": selected,
            "output_bytes": len(data), "path_count": len(paths)}


def vectorize(input_path, output_path, options=None, progress: Progress | None = None):
    """Trace a local raster image and return metrics. Native tracing is one stage.

    ``progress`` receives ``{progress: 0..100, message: str}`` dictionaries.
    Logos and B/W use the local Potrace precision backend when a bounded palette
    is available; photographs retain the independent VTracer color engine.
    """
    start = time.perf_counter()
    options = normalize_options(options)
    selected = _output_format(output_path)
    if Path(input_path).resolve() == Path(output_path).resolve():
        raise VectorizationError("Output must be different from the input image.")
    _report(progress, 5, "Reading image")
    image, info = _read_raster(input_path)
    notes = list(info["warnings"])
    try:
        _report(progress, 15, "Preparing colors and transparency")
        image = _prepare(image, options, notes)
        trace_width, trace_height = image.size
        if image.size != (info["width"], info["height"]):
            notes.append(f"Tracing resized to {trace_width} × {trace_height}; SVG keeps the original canvas size.")
        pixels = BytesIO()
        image.save(pixels, format="PNG")
        _report(progress, 30, "Tracing vector paths on CPU")
        working_scale = max(trace_width / info['width'], trace_height / info['height'])
        svg = None
        trace_backend = 'vtracer-0.6.15'
        if options['mode']!='none' and (options['preset']=='logo' or options['color_mode']=='bw'):
            svg = _trace_precision(image,options,working_scale,progress,Path(output_path).parent)
            if svg is not None:
                trace_backend = 'potrace-1.16'
        if svg is None:
            try:
                svg = vtracer.convert_raw_image_to_svg(
                    pixels.getvalue(), img_format="png", colormode="color",
                    hierarchical="stacked", mode='polygon' if options['mode'] == 'spline' and options['preset']=='logo' else options['mode'],
                    filter_speckle=round(options["speckle"] * working_scale), color_precision=options["color_precision"],
                    layer_difference=0 if options['preset']=='logo' else 16, corner_threshold=options["corner_threshold"],
                    length_threshold=4.0,
                    max_iterations=10, splice_threshold=45, path_precision=3)
            except Exception as exc:
                raise VectorizationError("Tracing failed. Try fewer colors or a smaller trace resolution.") from exc
        _report(progress, 85, "Validating vector paths")
        original_per_trace = max(info['width'] / trace_width, info['height'] / trace_height)
        curve_tolerance = options['smoothing'] * 1.5 / original_per_trace
        if trace_backend.startswith('vtracer'):
            svg = _constrain_generated_svg(svg, image, options, curve_tolerance)
        root, paths = _validated_svg(svg)
        root.set("width", str(info["width"]))
        root.set("height", str(info["height"]))
        root.set("viewBox", f"0 0 {trace_width} {trace_height}")
        ET.register_namespace("", SVG_NS)
        svg_data = ET.tostring(root, encoding="utf-8", xml_declaration=True)
        _report(progress, 95, f"Writing {selected.upper()}")
        data = _export_data(svg_data, selected)
        _atomic_write(output_path, data)
        result = {"width": info["width"], "height": info["height"],
                  "input_width": info["width"], "input_height": info["height"],
                  "trace_width": trace_width, "trace_height": trace_height,
                  "source_format": info["format"], "output_format": selected,
                  "path_count": len(paths),
                  "color_count": len({path.get("fill") for path in paths}),
                  "output_bytes": len(data), "svg_bytes": len(svg_data),
                  "elapsed_seconds": round(time.perf_counter() - start, 3),
                  "trace_backend": trace_backend,
                  "curve_backend": ('potrace' if trace_backend=='potrace-1.16' else
                                    'bounded-cubic' if options['mode']=='spline' and options['preset']=='logo' else options['mode']),
                  "curve_deviation_limit_px": None if trace_backend=='potrace-1.16' else options['smoothing'] * 1.5 if options['mode']=='spline' and options['preset']=='logo' else None,
                  "options": deepcopy(options), "warnings": notes}
        _report(progress, 100, "Vector ready")
        return result
    finally:
        image.close()
