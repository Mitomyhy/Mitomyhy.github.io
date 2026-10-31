# MITOM product video renderer

This component renders product-preserving **2.5D** listing scenes locally using
Remotion 4.0.539. A cut-out PNG is turned left and right with a small hanging sway,
thin silhouette edge, moving reflection and separate background parallax. It does
not infer unseen geometry or claim to create a genuine 3D back view from one photo.
The original artwork and lettering are never repainted by the motion renderer.

Use a transparent, tightly trimmed product PNG. An optional local background image
can be produced by an installed AI model or selected by the user. Without one,
the renderer uses a deterministic, locally drawn setting for the chosen niche.
There is no image upload, remote URL, cloud render, credit system or paid API.
`background.videoSrc` may instead name a local MP4 background (for example, the
existing local video model's output). Its actual duration is read from the file
and looped silently to fill the listing duration. Only that one validated MP4 is
copied into the render bundle; the surrounding job folder is never exposed.

## Installation and integration

Install this directory's exact dependencies using the included lockfile, e.g.
`pnpm install --prod --frozen-lockfile --ignore-scripts`. The included `.npmrc`
uses a physical, hoisted dependency tree for portable Windows packaging; no
global virtual-store links are required. Do not use a globally installed mutable
Remotion. All Remotion packages must remain at the same version. Keep dependency
licenses and the native Windows compositor package, which contains its prebuilt
encoder and required DLLs.

```text
node render.mjs --request <JOB_DIR>/scene.json --output <JOB_DIR>/video.mp4 --browser <TRUSTED_CHROME_OR_EDGE_EXE> --concurrency 1
```

The browser is provided by the local runtime configuration, never by a prompt.
No browser is downloaded implicitly. Progress is emitted as JSON lines on stdout;
failures use stderr and a nonzero exit code. The Python worker should place the
renderer and all browser/encoder descendants inside its existing process and
memory containment and kill the process tree on cancellation/deadline.

`--still 60 --output <JOB_DIR>/preview.png` renders a real frame for preview/QA.
Exports have exact 1280×720 / 720×1280 or 1920×1080 / 1080×1920 dimensions,
H.264/yuv420p, 24 or 30 fps, 5–15 seconds and no audio track.

Scene assets must be PNG/JPEG/WebP descendants of the JSON request's directory;
network shares, remote/data URLs, symlinks, outside paths and existing outputs are
rejected. Image contents are passed to Remotion as data images. Scene values are
bounded data and are never interpreted as JavaScript, shell or HTML.

Example request:

```json
{
  "schemaVersion": 1,
  "aspectRatio": "9:16",
  "durationSeconds": 10,
  "fps": 30,
  "resolution": 1080,
  "seed": 42,
  "product": {"src": "product.png", "anchor": "hanging", "scale": 0.76, "material": "acrylic"},
  "background": {"src": "background.png", "theme": "christmas"},
  "motion": {"orbitDegrees": 42, "swayDegrees": 3, "floatPixels": 9, "zoom": 0.045, "cycles": 1, "direction": "left"},
  "lighting": {"warmth": 0.25, "shadow": 0.55, "glint": 0.35}
}
```

All controls besides `schemaVersion` and `product.src` have defaults. An optional
three-color `background.palette` accepts exactly three `#rrggbb` colors.

## Remotion license

The user confirmed personal use for this implementation. Remotion's Free License
permits individuals and qualifying companies of up to three people to render
commercial videos. Larger organizations need their own Remotion license; local
execution does not remove that requirement. This project does not activate a
subscription, submit a license key, or bypass Remotion license checks. Remotion
4.x server-side rendering can work offline without opting into usage telemetry.
Review the applicable terms before redistributing a company product.
The exact Remotion license text is retained as `REMOTION-LICENSE.md`.

This adapter omits both `licenseKey` and `apiKey`. In the pinned 4.0.539 Node
renderer, the resulting null key returns before the voluntary usage-event
request. Passing the literal `free-license` would opt into that request; it is
deliberately not passed. This follows Remotion's documented 4.x telemetry option
and does not remove or bypass the applicable license terms.

- https://www.remotion.dev/docs/license/pricing
- https://www.remotion.dev/docs/license/terms
- https://www.remotion.dev/docs/licensing
- https://github.com/remotion-dev/remotion/blob/main/LICENSE.md

## Checks

`node --test test/*.test.mjs` verifies aspect ratios, real left/right motion bounds,
deterministic results and the job asset/output boundary. `pnpm typecheck` validates
the React composition. Real rendering and codec verification are separate QA:
unit tests alone are not proof of a successful MP4 or visual quality.

`test/make-qa-fixture.py` creates neutral, generated test artwork, then the CLI
can produce real 720p landscape and 1080p portrait MP4s. `test/verify-qa.mjs`
checks codec, pixel format, exact dimensions/duration/frame count, absence of
audio, and every decoded frame. The full FFmpeg path for frame decoding is
configured in that QA script; the runtime compositor's minimal FFmpeg omits the
frame-hash muxer.

After creating the QA fixture, run
`node test/offline-render.mjs <TRUSTED_LOCAL_BROWSER_EXE>` to render a real MP4
while denying external Node HTTP/fetch/socket and composition-page requests.
The audit result is written to `qa/offline-network-verification.json`. This
checks application render traffic; it does not audit Chromium's own background
services or make an operating-system-wide network claim. QA assets and outputs
are excluded from distribution.
