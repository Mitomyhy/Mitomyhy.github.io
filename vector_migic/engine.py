"""Offline raster tracing for Vector Migic.

VTracer fits real vector paths. This module never uploads input or downloads a
model. It accepts raster inputs only and exports path-only SVG, PDF and EPS.
"""
from __future__ import annotations

from copy import deepcopy
from io import BytesIO
import math
import os
from pathlib import Path
import tempfile
import time
from typing import Callable
import warnings
import xml.etree.ElementTree as ET

from PIL import Image, ImageChops, ImageOps, UnidentifiedImageError
import vtracer

MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_INPUT_PIXELS = 40_000_000
MAX_SVG_BYTES = 64 * 1024 * 1024
ALLOWED_RASTER_FORMATS = frozenset({"PNG", "JPEG", "WEBP", "BMP", "TIFF"})
SVG_NS = "http://www.w3.org/2000/svg"
PRESETS = {
    "logo": {"colors": 16, "color_precision": 6, "speckle": 4,
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


def _prepare(image, options, notes):
    image.thumbnail((options["max_dimension"], options["max_dimension"]),
                    Image.Resampling.LANCZOS)
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
        if options["colors"]:
            rgb = rgb.quantize(colors=options["colors"],
                               method=Image.Quantize.MEDIANCUT,
                               dither=Image.Dither.NONE).convert("RGB")
        image = rgb.convert("RGBA")
        image.putalpha(alpha)
    if image.getchannel("A").getbbox() is None:
        raise VectorizationError("No visible foreground remains. Change transparency or B/W threshold.")
    return image


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
    Smoothing 0..1 maps to the native curve subdivision length 3.5..10 px;
    increasing it makes paths simpler and can omit small features.
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
        try:
            svg = vtracer.convert_raw_image_to_svg(
                pixels.getvalue(), img_format="png", colormode="color",
                hierarchical="stacked", mode=options["mode"],
                filter_speckle=options["speckle"], color_precision=options["color_precision"],
                layer_difference=16, corner_threshold=options["corner_threshold"],
                length_threshold=3.5 + options["smoothing"] * 6.5,
                max_iterations=10, splice_threshold=45, path_precision=3)
        except Exception as exc:
            raise VectorizationError("Tracing failed. Try fewer colors or a smaller trace resolution.") from exc
        _report(progress, 85, "Validating vector paths")
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
                  "options": deepcopy(options), "warnings": notes}
        _report(progress, 100, "Vector ready")
        return result
    finally:
        image.close()
