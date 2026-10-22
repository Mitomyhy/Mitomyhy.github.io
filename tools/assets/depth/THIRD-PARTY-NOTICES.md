# MITOM browser CPU 3D — 2026-10-22

The Photoshop installation is not modified by this website. All inference and
image processing take place in browser workers on the visitor's CPU. Photoshop
and the optional local PNG receiver are not required to generate or download.

## Models and modifications

- MoGe-2 Large + Normal, original FP32 weights from
  https://huggingface.co/Ruicheng/moge-2-vitl-normal . Static 490×490 input,
  1225 image tokens. Query chunks64 retain every key/value; no quantization,
  attention window, or model substitution. Conversion SHA256:
  `dbf975ba6cd84a95b51333fa4cee0f6b80c63306a46df3805937584c653e92ce`.
  1,325,313,091 bytes. [Upstream notices](../../cpu/LICENSE-MoGe).
- Depth Anything V2 Small, original FP32 weights from
  https://github.com/DepthAnything/Depth-Anything-V2 . Native1024 lower-bound
  preprocessing produces1036×1036 normalized input. Positional embedding is
  frozen at its exact74×74 grid. Conversion SHA256:
  `b4027ef3d5dc332cb2a756ea0f2209e9d20b454f4a794c5cc391a62f09fac961`.
  105,249,387 bytes. [Apache-2.0](../../cpu/LICENSE-DA2).
- Full conversion files are distributed as byte-preserving48MiB parts in
  https://github.com/Mitomyhy/mitom-browser-models at immutable commit
  `dd0af7edd5744e0ed71f8c64b38d5cad62ce27cb`. Every part and the assembled
  graph are SHA-256 checked before inference. Completed verified parts can be
  retained in origin-private browser storage; cancel/retry reuses valid parts.

These weights total1,430,562,478 bytes, excluding runtimes and browser cache
overhead. Download requires explicit consent and Generate. No AI file or model
request is made merely by opening a template or loading an artwork.
CDN/model hosts receive ordinary request/IP information, never artwork pixels.

## Runtime and original algorithms

- ONNX Runtime Web1.23.2, explicitly WASM CPU; MIT:
  https://github.com/microsoft/onnxruntime .
- Pyodide314.0.7, official distribution:
  https://pyodide.org/en/stable/ . Package licenses accompany its wheels.
- NumPy2.4.6, SciPy1.18.0, Pillow12.2.0 and OpenCV Python4.11.0.86.
- Reviewed original relief modules are copied unchanged and individually
  size/SHA-256 checked. Native geometry validity, normal-guided Poisson,
  PCHIP/multiscale fusion, plane balancing, transition smoothing, source detail,
  support/text composition and16-bit PNG export run in Pyodide.
- Camera recovery uses the original SciPy LM objective. DA2 uses actual
  Pillow/OpenCV preprocessing and FP32 align-corners bilinear restoration.

## Templates, renderer, and limits

The original panel WebGL renderer is unchanged. Circle/Heart cut masks are the
actual authored raster transparency extracted from the template's embedded In
Smart Object; no mathematical heart/circle is substituted. Template descriptors
record original canvas dimensions and source hashes. Artwork uses a centered
contain placement; this is not a general PSD warp/action/effect/font renderer.

This complete composition workflow has an explicit **Standard** model budget.
It is **not** the native panel's14400-token UHD inference, nor a promise of
pixel-identical PSD composition. Source/output preparation is bounded to2048px;
larger mockup exports do not add AI detail. Dedicated workers support cancel.
Processing time and peak memory on16/32GB weaker machines are not guaranteed.

Native and browser regression fixtures passed within stated numerical
tolerances; newer SciPy/Pillow builds are not asserted bit-identical. Depth PNG
is genuine grayscale16-bit. Relative relief and illustrative sealed WebGL
geometry are not calibrated manufacturing millimetres or a measured3D scan.

The former R1 DA2-only q8 implementation is retained as unused source history;
the active workbench imports `depth-flow-tool.js`, not `depth-tool.js`.
