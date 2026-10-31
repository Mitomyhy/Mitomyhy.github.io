# MITOM Product Video 1.0.0

Create silent Etsy listing MP4s from a product photo and a prompt, entirely on
the computer after the initial model download. Choose 16:9 or 9:16; the default
is 10 seconds at 1080p. This is a separate app from the older LTX preview.

## Use

Install **MITOM Product Video** in MITOM Launcher and click Open. Add a product
image, describe the scene and movement, choose the ratio, then Generate. A
transparent product PNG gives the most reliable outline; photographs use the
included local U2netP mask model. More options include a background photograph,
duration and output resolution. The result is H.264/yuv420p MP4 without audio,
limited to 100 MB and 5–15 seconds.

The Photoshop entry is **Window > Extensions > MITOM Product Video**. Its
separate CEP extension does not replace the existing Order/3D panel. The public
website can open the local app or pair with it using the local app's session
code. Never send that code to other people.

## What the AI does

The pinned local Qwen vision model reads the product image and prompt and
selects validated scene, palette, material and movement parameters. Remotion
animates the original isolated product with turns, sway, depth, highlights and
shadows. Printed text is retained from the source rather than regenerated.

The standard small package draws a themed backdrop from the AI's scene plan.
It does not claim to generate a photorealistic room from an arbitrary prompt.
You can provide your own background photograph. An explicitly configured local
LTX installation can additionally generate a moving AI backplate; this is
defocused behind the original product. LTX weights are not included in the
standard package.

Motion is 2.5D from one image. It cannot reconstruct an unseen back or provide
an accurate full 360-degree model. A poor mask or transparent acrylic reflection
may need a cleaner source PNG.

## First use and hardware

The app includes its own Python, Node and renderer dependencies. Edge or Chrome
must already be installed. A first Generate downloads two pinned Qwen0.8B
model files, together 737,504,352 bytes (about703 MiB), then verifies their full
SHA-256 hashes. Subsequent generation works offline. Image and prompt data are
not part of these downloads. An already installed supported pinned Qwen9B
runtime can be reused.

CPU inference is supported. No paid API, credit account or cloud fallback is
used. A GPU option is not advertised by this CPU runtime. Jobs are serialized
and contained in a Windows Job with a memory bound and deadline. Cancel stops
the job's descendant processes. Performance on i3/16GB has not been measured;
development-machine timings are not promises for other computers.

## Licensing and update delivery

Remotion4.0.539 is pinned. Each user's eligibility for the Remotion Free License
applies; the requested personal use is covered. See the included Remotion
license and https://www.remotion.dev/license for current terms. No licensing
usage key is passed to the optional usage-event API.

Third-party licenses and model provenance ship with the app. Model weights are
downloaded from a fixed revision and verified. Launcher updates use its signed
catalogue, per-part and aggregate archive hashes, and a complete runtime file
inventory. The app's sessions and model cache live outside the replaceable
application folder. Unknown or modified files are preserved on update.
