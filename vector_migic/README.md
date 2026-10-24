# Vector Migic

Independent, local raster-to-vector application by MITOM for Windows x64. It does not contain Vector Magic's proprietary engine, and no claim of identical results is made.

## Use

Open Vector Migic from MITOM Launcher, add an image, choose its type, and click **Vectorize image**. Compare the result at different zoom levels before exporting SVG, PDF or EPS. Multiple inputs can be queued and exported in a batch ZIP.

English is the default. Vietnamese is available from the header. Processing runs on the same computer, without API credits or a GPU requirement. Installing the compact launcher/application initially requires a download; tracing and exporting work offline afterward.

For logos, use **Logo & lettering** and begin with its default settings. The small-region control displays an area in square pixels: increasing it can remove dots, small holes and fine lettering. Changes to settings do not alter an already generated vector; click **Update vector** to apply them.

## Engine and preview

Version 1.0.2 improves the independent precision logo pipeline. Antialiased edges choose between nearby trusted inks, so a gray fill elsewhere in a logo does not become a false gray outline around black-and-white lettering. Opaque regions paint from border colors toward inner regions, with underpainting that prevents unrelated background inks from appearing in small gaps between fitted curves. Hard-edged artwork can use its native pixel grid for smoother, simpler contours; narrow strokes and tiny counter holes retain the antialias-aware path. These rules use image content rather than sample names or fixed customer palettes.

Transparent artwork uses local ink support constrained by the source alpha, preserving exterior space and counter holes without placing a distant dominant color beneath every internal edge. Distinct thin neutral-gray marks are retained using their source pixels, rather than treating every gray pixel as an antialiasing blend.

Suitable logos use the unmodified Potrace command-line program. Other input types and path styles use VTracer. The application preserves the original canvas, compound paths and transparency while fitting vector curves. It does not implement a manual node editor. Results depend on the source and settings, and are not claimed to reproduce another application's proprietary engine exactly.

The preview waits for the selected source and vector to finish loading, and displays them in one shared canvas coordinate frame. Zoom, pan, resize and the comparison divider therefore use the same geometry. Exports are vector paths, not a bitmap embedded inside an SVG/PDF/EPS container.

## Development and redistribution

Use Python 3.12 x64 and install `requirements.txt` plus `requirements-build.txt` in a virtual environment. Run `python -m vector_migic.main` from the parent folder or build with `vector_migic/build.ps1`. The shared release version is in `app_version.py`.

The portable app includes a complete source archive, upstream notices, licenses and build instructions under `_internal/notices`. Potrace remains an unmodified, separately invoked command-line program; its GPL license and corresponding source are included. VTracer and the remaining Python dependencies retain their upstream licenses.

Tests generate synthetic fixtures. User artwork, official comparison fixtures, browser profiles, session tokens and local QA results must not be bundled in the application or source archive.
