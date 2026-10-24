# Vector Migic

Independent, local raster-to-vector application by MITOM for Windows x64. It does not contain Vector Magic's proprietary engine, and no claim of identical results is made.

## Use

Open Vector Migic from MITOM Launcher, add an image, choose its type, and click **Vectorize image**. Compare the result at different zoom levels before exporting AI, SVG, PDF or EPS. Multiple inputs can be queued and exported in a batch ZIP.

English is the default. Vietnamese is available from the header. Processing runs on the same computer, without API credits or a GPU requirement. Installing the compact launcher/application initially requires a download; tracing and exporting work offline afterward.

For logos, begin with **Logo & lettering**, **Auto** colors, smooth curves, 50% smoothing, a 1 px² small-region filter, color precision 8 and 2048 px processing size. Transparency is preserved by default. Auto adapts the palette to image content; it does not retain every incidental source color. The same engine preset initializes the interface and Reset, so the visible controls and submitted options agree.

The small-region control displays an area in square pixels: increasing it can remove dots, small holes and fine lettering. Changes to settings do not alter an already generated vector; click **Update vector** to apply them.

## Engine and preview

Version 1.0.4 cleans short, low-contrast edge fragments before fitting curves. A small region can merge into a larger adjacent ink when the local colors indicate a quantized shadow or blend beside a contrasting shape. The cleanup protects stable interiors, flat source-authored thin marks and the alpha mask. It does not blur the whole image or increase the processing size. This reduces hardened shadow specks around lettering while retaining the existing curve and export controls.

Version 1.0.3 fixes high-color Logo Auto inputs that previously bypassed palette reduction and fell back to thousands of fragmented color regions. A bounded adaptive palette keeps the precision curve backend available for blended lettering and artwork with color bands. Spatial ink assignment prevents a distant green or red fill from becoming a false fringe around white letters. Gradient artwork becomes bands of solid vector color; it is not reconstructed as live gradient meshes.

The earlier precision improvements remain: antialiased edges choose between nearby trusted inks; opaque regions paint from border colors toward inner regions; hard-edged artwork can use its native pixel grid for simpler contours. Narrow strokes and tiny counter holes retain the antialias-aware path. These rules use image content rather than sample names or fixed customer palettes.

Transparent artwork uses local ink support constrained by the source alpha, preserving exterior space and counter holes without placing a distant dominant color beneath every internal edge. Distinct thin neutral-gray marks are retained using their source pixels, rather than treating every gray pixel as an antialiasing blend.

Suitable logos use the unmodified Potrace command-line program. Other input types and path styles use VTracer. The application preserves the original canvas, compound paths and transparency while fitting vector curves. It does not implement a manual node editor. Results depend on the source and settings, and are not claimed to reproduce another application's proprietary engine exactly.

The preview waits for the selected source and vector to finish loading, and displays them in one shared canvas coordinate frame. Zoom, pan, resize and the comparison divider therefore use the same geometry. Exports contain vector paths rather than an embedded bitmap.

## Illustrator AI export

**Save AI** writes legacy Illustrator 8-compatible path records with an original PostScript prolog. It preserves editable filled Bézier paths, compound holes, RGB fills, transforms and the artboard. It runs without an Illustrator installation and is not a renamed PDF or EPS. The writer is checked against actual Illustrator imports as well as geometry tests.

This format does not contain modern Illustrator private editing data, live effects or partial opacity. Unsupported strokes, clipping or partial opacity are rejected rather than silently flattened or lost; use SVG/PDF for those features. The current tracing engine already produces filled paths and a binary transparent background. AI is also available for batch ZIP export.

## Development and redistribution

Use Python 3.12 x64 and install `requirements.txt` plus `requirements-build.txt` in a virtual environment. Run `python -m vector_migic.main` from the parent folder or build with `vector_migic/build.ps1`. The shared release version is in `app_version.py`.

The portable app includes a complete source archive, upstream notices, licenses and build instructions under `_internal/notices`. Potrace remains an unmodified, separately invoked command-line program; its GPL license and corresponding source are included. VTracer and the remaining Python dependencies retain their upstream licenses.

Tests generate synthetic fixtures. User artwork, official comparison fixtures, browser profiles, session tokens and local QA results must not be bundled in the application or source archive.
