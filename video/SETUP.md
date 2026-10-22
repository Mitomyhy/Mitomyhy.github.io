# Connect MITOM Product Video

The Video page is ready to connect. It does not generate a real video until you run n8n, add a Kie API credential, and connect the two webhooks. n8n can run on your own machine; Veo generation and optional image upload use Kie's cloud service and require Kie credits.

## 1. Start n8n

Use your existing n8n instance or [n8n Cloud](https://n8n.io/). For a local Docker installation, follow the [official Docker guide](https://docs.n8n.io/hosting/installation/docker/). A PowerShell example binds the editor and webhooks to this machine:

```powershell
docker volume create mitom_n8n
docker run -d --name mitom-n8n --restart unless-stopped -p 127.0.0.1:5678:5678 -e TZ=Asia/Ho_Chi_Minh -e GENERIC_TIMEZONE=Asia/Ho_Chi_Minh -v mitom_n8n:/home/node/.n8n docker.n8n.io/n8nio/n8n:stable
```

Open `http://localhost:5678` and complete n8n's owner setup. Keep the named volume: it holds your workflows and encrypted credentials. These commands are setup instructions; MITOM does not run them for you.

For a hosted instance, use HTTPS and configure the public `WEBHOOK_URL` behind your reverse proxy as described in [n8n's reverse proxy guide](https://docs.n8n.io/hosting/configuration/configuration-examples/webhook-url/).

## 2. Import one workflow

Download [MITOM-Product-Video.json](./workflows/MITOM-Product-Video.json). In n8n, create a workflow, choose **Import from File**, and select the JSON.

The import contains two POST Webhook triggers in one inactive workflow:

| Purpose | Production path |
| --- | --- |
| Generate a video task | `/webhook/mitom-video-generate` |
| Check an existing task | `/webhook/mitom-video-status` |

It contains no API keys, saved credential IDs, pinned execution data, uploaded products, or sample customer data.

## 3. Add two credentials inside n8n

Create a **Header Auth** credential named **MITOM Video Webhook Token**:

- **Name:** `X-MITOM-Token`
- **Value:** a long random secret that you choose.

Select it in both **Generate webhook** and **Status webhook** nodes. Both nodes already have authentication set to Header Auth. Do not switch them to None.

Create a second **Header Auth** credential named **Kie API**:

- **Name:** `Authorization`
- **Value:** `Bearer YOUR_KIE_API_KEY`

Obtain the provider key from your own Kie account. Select this credential in **Upload product image**, **Create video task**, and **Get video status**. Keep this key only inside n8n credentials. The MITOM connection field takes your webhook token, never the Kie API key.

Save the workflow and **Publish / Activate** it. Copy the **Production URL** from each Webhook node. The `/webhook-test/` URLs only work while n8n listens for a test event and should not be saved in MITOM.

## 4. Connect the Video page

Open [MITOM Video](https://mitomyhy.github.io/#video), open its connection settings, and enter:

- Generate URL, for example `http://localhost:5678/webhook/mitom-video-generate`.
- Status URL, for example `http://localhost:5678/webhook/mitom-video-status`.
- The **MITOM Video Webhook Token** value you chose.

The token stays in the current page's memory. Re-enter it after reloading. Your provider key is never sent to the website.

Both nodes restrict **Allowed Origins (CORS)** to `https://mitomyhy.github.io`. The n8n webhook handler responds to the browser's OPTIONS preflight and allows its requested JSON and `X-MITOM-Token` headers. Reverse proxies must forward OPTIONS and must not redirect webhook requests to a sign-in page.

A browser may ask for local-network access when an HTTPS website contacts your local n8n. Allow it for MITOM if you intend to connect that local instance. If the browser blocks the local connection, use an HTTPS n8n endpoint reachable from that browser. Do not disable browser security. To preview MITOM from another origin, add that exact origin to the nodes' Allowed Origins, separated by a comma, and adjust **Respond JSON → Access-Control-Allow-Origin** for the preview.

## 5. Make a video

Enter a prompt describing the product, scene, camera motion, lighting, and what must remain unchanged. A product image is optional:

- Upload one PNG, JPEG or WebP, at most **5 MiB**.
- Or provide one public **HTTPS image URL** reachable by Kie.
- Leave both empty for text-to-video.

The workflow requests **Veo 3.1 Fast, 720p, 8 seconds**, with the selected **16:9** or **9:16** ratio. One image is used as the first frame. It does not add fixed camera/product presets yet. The prompt is passed through without a second AI rewriting it.

Generation creates one paid provider task. Status checks only query that task; they do not generate another video. Automatic request retries are disabled. If a connection drops after Generate, check your Kie task history before generating again to avoid duplicate charges.

Kie uploads expire after 24 hours and generated files are currently retained for 14 days. Download your result while it is available. See the current [upload](https://docs.kie.ai/file-upload-api/upload-file-base-64) and [task status](https://docs.kie.ai/market/common/get-task-detail) documentation for provider retention.

## Request and response contract

Both routes accept JSON with `Content-Type: application/json` and `X-MITOM-Token`.

Generate:

```json
{
  "prompt": "Slow camera orbit around the product on a neutral studio surface.",
  "aspect_ratio": "16:9",
  "imageUrl": "https://your-image-host.example/product.png"
}
```

`prompt` is required (1–5000 characters). `aspect_ratio` defaults to `16:9`. Omit `imageUrl` for text-only generation. Instead of `imageUrl`, a local upload uses `imageBase64` containing a `data:image/png;base64,...` (or JPEG/WebP) URL and optional `imageName`. Sending both image sources is rejected before any provider call.

A successful Generate returns the original provider object, for example:

```json
{"code":200,"msg":"success","data":{"taskId":"task_from_provider"}}
```

Status:

```json
{"taskId":"task_from_provider"}
```

Status returns the original Kie response. Its `data.state` is `waiting`, `queuing`, `generating`, `success`, or `fail`. On success, parse `data.resultJson` for output URLs. On failure, show `data.failMsg`; a task can fail even when the response has `code: 200`.

Input validation returns HTTP 400. Provider responses with `code != 200` return HTTP 502 with the original provider body, even if Kie sent HTTP 200. Missing/invalid provider responses also return HTTP 502 with a short connection error. A failed upload stops before task creation. Authentication errors are handled by n8n before the workflow runs.

## Verify and troubleshoot

- **401 / authentication error:** select the webhook credential on both triggers and enter the same token in MITOM.
- **404 / webhook not registered:** publish the workflow and use its Production URLs.
- **CORS / Failed to fetch:** check the browser's Network tab for OPTIONS, confirm the exact origin and HTTPS/local connection, and ensure a proxy is not swallowing preflight.
- **Provider code 401:** check the Kie credential on all three HTTP Request nodes.
- **Provider code 402:** add credits in Kie; MITOM cannot supply them.
- **Provider code 422:** inspect the provider message and confirm the product image is accessible.
- **Task state fail:** read `data.failMsg`; do not treat a returned task ID as a finished video.

The workflow disables saved successful/error/manual execution data by default because requests contain product images and prompts. n8n's editor may still show an execution while you test it; do not pin or publicly export that data. Protect your n8n owner account, keep its credential encryption key/volume safe, and keep the webhook token private.

Local graph and code-node checks, with mocked provider responses and no paid calls:

```powershell
node --test video/workflows/workflows.test.mjs
```

This validates the exported graph and request/error handling; importing and executing it in your configured n8n instance is still required to verify your credentials, host, and actual provider access.

## Sources

The [reference project supplied by the user](https://github.com/Alex-safari/AI-Product-Video-Generator-Using-Veo-3.1-n8n-) inspired the video integration. This MITOM workflow was written for the website's two-webhook contract and uses no credentials, customer examples, or bulk workflow code from that project.

API schema checked 2026-10-22 against [Kie Veo 3.1 generation](https://docs.kie.ai/veo3-api/generate-veo-3-video), [Base64 upload](https://docs.kie.ai/file-upload-api/upload-file-base-64), [task details](https://docs.kie.ai/market/common/get-task-detail), and [n8n Webhook](https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.webhook/) documentation. Current generation uses top-level model `veo-3-1`, input tier `veo3_fast`, and `/api/v1/jobs/createTask`; old `/veo/generate` payloads are not used.

