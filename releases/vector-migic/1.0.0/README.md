# Vector Migic 1.0.0

An independent MITOM raster-to-vector application for Windows x64. It runs locally on CPU using [VTracer](https://github.com/visioncortex/vtracer), with no API account or credits. It is not affiliated with Vector Magic and does not contain that product's code.

## Install

Download MITOM Launcher 2026.10.24.3, find **Vector Migic**, and click **Install**. The compact launcher downloads the verified application package once. Click **Open** after installation; tracing and exporting work offline.

Alternatively, extract `VectorMigic-1.0.0.zip` into a folder and run `VectorMigic.exe`. Keep `_internal` alongside the executable. Python, Photoshop and a GPU are not required.

## Use

1. Add PNG, JPG, WebP, BMP or TIFF images by dropping, browsing or pasting.
2. Choose Logo, Illustration or Photo, then adjust colors, smoothing, speckles and transparency if needed.
3. Click **Vectorize** or **Trace all**. Compare the source and vector result, zoom and pan.
4. Export genuine vector **SVG, PDF or EPS**, or export the completed batch as a ZIP.

English is the default. Switch to Vietnamese in the app header. Images are processed on the same computer and are not sent to a remote service. Transparency is thresholded into vector regions; tracing does not preserve photographic detail or editable text perfectly. This first version does not include manual node editing.

## Source and licenses

`VectorMigic-source.zip` contains the application and build scripts. The portable application's `_internal/notices` folder includes the same source, dependency licenses, the unmodified svglib source distribution, and `SOURCE-REBUILD.txt` instructions for rebuilding with a replacement library.

Build with Python 3.12 x64, the pinned `requirements.txt` and `requirements-build.txt`, then run `vector_migic/build.ps1`. Ghidra and REA are not required.

See `SHA256SUMS.txt` and `vector-release.json` for the published archive checksum and launcher descriptor.
