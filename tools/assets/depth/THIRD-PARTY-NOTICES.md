# Browser 3D implementation

`depth-viewer.js` is an exact copy of the user's installed MITOM/UOY renderer,
copied without altering the installed Photoshop Panel:

- Source: `com.uoy.orderpanel/js/depth-viewer.js`
- SHA-256: `1c287534e96036c5cbb13c1254105b287e7837c298c2a96d11f2d5de0fb3e1c8`
- Size: 142,424 bytes

The preview is illustrative geometry. It is not a manufacturing mesh. Browser
ceramic circle/heart masks are generated preview blanks, not the Panel's exact
PSD template masks. The original Photoshop workflows remain installed and
untouched.

## Optional on-device AI

No AI library, model, or ONNX runtime is requested until the user presses
Generate AI. Model inference runs in a dedicated browser worker. The application
passes local pixels to this worker; it does not upload the artwork or use a
remote inference API. First-time library/runtime/model requests still disclose
the visitor's IP and ordinary request information to their respective hosts.

- [Transformers.js 3.8.1](https://github.com/huggingface/transformers.js/tree/3.8.1), Apache-2.0.
- [Depth Anything V2 Small ONNX](https://huggingface.co/onnx-community/depth-anything-v2-small), Apache-2.0.
- Immutable model revision: `4472b7362082ad9968fee890ca0f1e5aca36b93d`.
- Variant: `q8` (`onnx/model_quantized.onnx`, 27,258,801 bytes).
- Model SHA-256 published by Hugging Face LFS:
  `fcf51f1b230362b28690bb9d1809bf0431f29cad20534e3f589bd7285547f20d`.
- [Original Depth Anything V2 project](https://github.com/DepthAnything/Depth-Anything-V2).
- ONNX Runtime Web is a transitive runtime of Transformers.js, under the
  [MIT license](https://github.com/microsoft/onnxruntime/blob/main/LICENSE).

The model weights are not included in the website distribution. They are
downloaded from Hugging Face after the explicit click, and may be retained in
browser-managed cache. This implementation pins the library release and exact
model revision; it does not claim a cryptographic runtime attestation.

Primary API references consulted on 2026-10-22:

- [Vanilla JavaScript CDN installation](https://huggingface.co/docs/transformers.js/v3.8.1/en/installation)
- [DepthEstimationPipeline 3.8.1 source](https://github.com/huggingface/transformers.js/blob/3.8.1/src/pipelines.js#L2818-L2888)
- [RawImage 3.8.1 source](https://github.com/huggingface/transformers.js/blob/3.8.1/src/utils/image.js)
- [Quantization API](https://huggingface.co/docs/transformers.js/v3.8.1/en/guides/dtypes)
- [Model usage and license](https://huggingface.co/onnx-community/depth-anything-v2-small)

Browser AI is Depth Anything V2 **Small** alone, not the Panel's MoGe + DA2
pipeline. Inference is limited to a 1024 px longest-side input; its floating-point
depth output is normalized and bilinearly mapped to the source canvas. Artwork
is retained at its loaded resolution (up to 16 megapixels / 8192 px per side).

Imported depth images must exactly match the artwork dimensions. Browser Canvas
decodes these imported files to 8-bit pixels; the interface discloses this.
Exported depth is a true 16-bit grayscale PNG container and preserves the
available normalized Float32 depth to 16-bit precision; it does not invent lost
precision in an imported 8-bit image.

Mockup PNG exports call the existing renderer's native-texture tiled
`exportFullPNG` operation at the requested output resolution. They do not upscale
a screenshot. Unsupported GPU dimensions or exhausted memory are surfaced as
errors rather than silently producing a smaller image.
