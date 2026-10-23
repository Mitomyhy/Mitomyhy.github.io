# MITOM local video worker — CPU preview 2026.10.23.1

This worker generates **real image + prompt → video** with LTX Video 2B 0.9.6
distilled and OpenVINO on CPU. The portable app includes its own isolated Python,
software MP4 encoder and local browser interface. A separate setup command downloads
the four hash-pinned model parts once; generation then works offline on each PC.

Actual browser upload, free-text prompt, model generation and MP4 download passed
with the portable runtime: 512×320 native AI, 49 frames at 8 fps, 6.125 seconds,
75.6 seconds elapsed and 5.71 GiB peak aggregate RSS on an i7-14700K using four
CPU threads. The MP4 is upscaled to 1152×720; this does not add native AI detail.
An independent multipart installation into a path containing spaces also passed,
followed by actual generation from that installed, sanitized model package.

This is a preview. Motion can be subtle and lettering/product details can change.
The clean-machine and i3/16 GB configurations have not been tested. Development-PC
measurements are not performance promises for those computers. The API stays
`model_unverified` until its own process completes a real CPU generation.

The worker binds loopback only and has no cloud fallback, credit system or
persistent service. The model's exact license and component notices are included
in its package; see the release guide for installation and offline transfer.

## Foreground start after model preparation

```powershell
python video-local-engine/worker_server.py --model <LOCAL_MODEL_FOLDER> --ffmpeg <LOCAL_FFMPEG_EXE> --python <CPU_RUNTIME_PYTHON>
```

Optionally add `--frontend <FIXED_APPLICATION_FOLDER>` for the prepared local UI.
With that option, `--open-browser` opens the loopback application once after a
successful server bind. It creates no persistent service and never opens an
external URL. API-only mode does not enable automatic browser opening.
Only `/`, `/index.html`, `/app.js`, `/client.mjs`, `/style.css` and the explicit
four logo/favicon/font assets are exposed. The local HTML receives its own
session token through a meta element, so this mode does not print the token.
Cross-origin HTML fetches are denied; a normal top-level link from MITOM can
open the application. The document has no CORS grant, forbids framing, and uses
a same-origin CSP, COOP and CORP. The external website must use manual pairing
for API calls and cannot fetch the local document to discover its token.

The server binds **127.0.0.1:4176 only**. In API-only mode its console prints a fresh pairing token.
Keep it in the client session and send `Authorization: Bearer TOKEN` headers;
never put it in URLs, query strings, browser history or download links. Ctrl+C
stops the server and its generation subprocess. No persistent service is created.

Default child bounds: four CPU threads, one inference stream, **10 GiB private
job memory**, independent RSS watchdog, 30-minute deadline. The Windows Job
memory cap also contains the ffmpeg encoder and kills children when closed.
Containment failure prevents model startup. `--memory-gib` can lower the cap;
`--deadline-seconds` allows 1–7200 seconds. No request can alter these limits.

The process is created **suspended**, assigned to its memory Job, then resumed
using documented Windows thread APIs. This matters for Windows virtual-env
launchers: attaching an already running launcher would not contain a Python
child it had already spawned. Memory accounting sums **every Job member**, not
only the launcher. A regression using the actual venv proves that its real
Python child, launcher and any intermediate launcher are all in the Job; a
16 MiB touched allocation appears in measured RSS/private memory. A second real
test denies a128MiB allocation under a64MiB cap with `MemoryError`. Windows can
report a Job peak above the cap after the denied allocation; this is retained as
a conservative failure metric, not reported as a successful RAM measurement.

The model package must include `mitom-model.json` (see the intentionally invalid
example), the LTX image-to-video model index, text encoder, transformer, VAE
encoder and decoder IR/config files, scheduler config and local T5 tokenizer.
The source revision must be pinned. This checks presence and profile, not weight
quality or authenticity; a release package still needs a signed/hash-verified
delivery manifest and the model's applicable license. No remote code is trusted.

Current candidate inference environment is **Python 3.12**, Optimum Intel source
commit `c46fb912366bc9d25b8395621d885a457369228d` (`2.3.0.dev0`), OpenVINO
`2026.5.0.dev20261021`, Diffusers `0.40.0`, Transformers `5.10.4`, CPU-only Torch
`2.7.1+cpu`, NumPy, Pillow and a local ffmpeg with libx264. These include prerelease
components and are not a final distributable dependency lock. Do not substitute
OpenVINO GenAI for this recipe: its reviewed VAE path cannot accept this model's
timestep-conditioned decoder. The engine forces CPU and offline model loading.

The inference child keeps the original dynamic IR and uses sequential CPU
component residency instead of `pipe.reshape()` or compiling everything at once.
Its `low_memory_pipeline.py` is an exact copy of the CPU probe's reviewed helper,
SHA256 `63c7c507f09876e3afa1d61bd1c698b7c2243b47ddad243716bcc659698bcd41`.
Attribution: `build/video-ai-cpu-probe/low_memory_pipeline.py`, with its separate
tiny-model proof in `low-memory-tiny-20261022-234529/report.json`. That proof
compared eight real CPU denoising steps against the original pipeline and found
identical finite output. It does not prove real model RAM use or video quality.
The helper clones outputs before releasing T5/image-VAE requests, retains the
transformer across eight steps, releases it before decoder compilation, and
rejects reuse or guidance other than1. Each actual job loads a fresh pipeline.

## API contract

Only exact origins `https://mitomyhy.github.io`, `http://127.0.0.1:4176` and
`http://localhost:4176` are allowed by default (local port follows `--port`).
Host validation also rejects DNS rebinding. No wildcard CORS or cookies.

| Endpoint | Body / response |
| --- | --- |
| `GET /v1/health` without token | Minimal `{version:1, model_state, pairing_required:true}` |
| `GET /v1/health` with token | Full CPU/model state, caps, `inference_verified`, `generation_enabled` |
| `POST /v1/uploads` | Raw image bytes; `Content-Type: image/png`, `image/jpeg` or `image/webp`; returns `201 {image_id,width,height}` |
| `POST /v1/jobs` | JSON `{image_id,prompt,options?}`; returns `202` job snapshot |
| `GET /v1/jobs/ID` | Job state, phase, step progress, peak memory and errors |
| `POST /v1/jobs/ID/cancel` | JSON `{}`; queued or running cancellation |
| `GET /v1/jobs/ID/video` | Authenticated real `video/mp4` download, available only after completion |

All endpoints except minimal health discovery require the bearer token. Fetch
the MP4 with its Authorization header, then make a client-side Blob download.
The server does not accept query tokens. Preflight only allows GET/POST and
Authorization/Content-Type; private-network preflight is granted to allowed
origins only. Browser local-network access may also require browser permission.

Upload limit: 15 MiB; one still PNG/JPEG/WebP, at most 24 MP, each side32–12000px.
The runtime copy is RGB PNG with metadata discarded and longest side2048px.
Prompt: 1–4000 characters. Arbitrary natural-language instructions are passed to
the actual model; no keyword-to-effect mapping. The default is512×320 with
49frames at8fps (6.125seconds);12fps remains available for shorter previews.
All49 frames are generated by the model, without artificial frame repeats.

```json
{"image_id":"32-lowercase-hex-id","prompt":"The hanging ornament gently sways and turns slightly.","options":{"width":512,"height":320,"frames":49,"fps":8,"seed":42}}
```

Allowed native sizes:512×320,320×512,384×384,704×448,448×704,320×192,192×320,256×256.
Frames9,17or49; fps8or12; seed0–2147483647. The smaller profiles remain available
for faster previews. The512×320 default was measured; portrait and square are
bounded alternatives, without a separate speed or quality guarantee. The larger
704×448/448×704 profiles are explicitly allowed after the larger CPU probe;
512×320 remains the conservative default.

Final MP4 export uses H.264/yuv420p with no audio and scales the short side to
720px using Lanczos, preserving aspect ratio with an even long side. For example,
native512×320 exports1152×720, and native704×448 exports1132×720. This is an
upscale, not native720p generation. Each job reports `native_resolution`,
`export_resolution`, `upscaled`, exact frame count, fps and duration separately;
authenticated health describes the export policy. Encoding keeps the generated
frame count and frame rate unchanged. A synthetic-frame real ffmpeg encode and
full decode test checks dimensions,6.125s duration, H.264/yuv420p, absent audio and
changing first/last frames. It is not an AI motion-quality test.
Eight official distilled sigmas and guidance1 are fixed internally. No device,
model path, steps, files, executable, URL or runtime options are accepted from
requests. One generation runs at a time with at most three queued jobs; each
process session allows twenty uploads and forty jobs. Progress is by phase and
actual denoising callback, without an invented ETA. All session files remain in
the selected local data folder; prompt request files are removed after execution
or shutdown. Uploaded images and outputs remain for the user to remove locally.

Structured failure codes include `memory_limit`, `deadline_exceeded`,
`containment_failed`, `inference_failed`, `model_missing_or_invalid`,
`encoder_missing`, `queue_full`, `invalid_image`, `unauthorized` and
`origin_forbidden`. A runtime/model error cannot yield a successful fake video.

## Verification performed

Control-plane tests exercise HTTP auth, host/origin checks, exact CORS,
image validation, parameter/path bounds, absent-model disabling, queue/cancel,
memory/deadline errors and prompt cleanup using tiny test subprocesses. A real
Windows Job object test measures private memory and verifies kill-on-close.
Frontend tests check that cross-origin fetch cannot read the injected token,
normal top-level navigation works, and fixed assets cannot expose private files.
These are not AI quality or performance tests.

```powershell
python -m unittest discover -s video-local-engine -p 'test_*.py' -v
```

`test_sigma_schedule.py` additionally uses the real installed Diffusers scheduler
without model weights to verify the exact distilled sigmas, its first Euler step,
and rejection of unsupported scheduler transforms. It skips only when that ML
environment is absent.

Remaining release gates: worker-to-MP4/browser integration and visual review,
16 GB/i3 validation, final pinned runtime/model packaging and license inclusion,
ffmpeg distribution license, client pairing UX, and end-to-end browser checks.
