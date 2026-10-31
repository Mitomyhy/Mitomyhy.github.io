#!/usr/bin/env node
/** A local asset-only renderer. Scene text is data; it is never evaluated as code. */
import {fileURLToPath, pathToFileURL} from 'node:url';
import {dirname, extname, isAbsolute, join, relative, resolve, basename} from 'node:path';
import {lstat, readFile, realpath, stat, mkdtemp, mkdir, writeFile, rename, copyFile, open} from 'node:fs/promises';
import {normalizeScene, sceneDimensions} from './src/scene.mjs';

const ROOT = dirname(fileURLToPath(import.meta.url));
const MAX_IMAGE_BYTES = 24 * 1024 * 1024;

function inside(base, target) {
  const rel = relative(base, target);
  return rel === '' || (!isAbsolute(rel) && rel !== '..' && !rel.startsWith(`..\\`) && !rel.startsWith('../'));
}
async function plain(path) {
  let cursor = resolve(path);
  while (true) {
    const info = await lstat(cursor);
    if (info.isSymbolicLink()) throw new Error('Links are not accepted in render asset paths');
    const parent = dirname(cursor);
    if (cursor === parent) break;
    cursor = parent;
  }
  return realpath(path);
}
function imageType(buffer) {
  if (buffer.length >= 24 && buffer.subarray(0, 8).equals(Buffer.from([137,80,78,71,13,10,26,10]))) {
    const width = buffer.readUInt32BE(16), height = buffer.readUInt32BE(20);
    if (!width || !height || width > 12000 || height > 12000 || width * height > 24_000_000) throw new Error('PNG dimensions exceed the render limit');
    return 'image/png';
  }
  if (buffer.length > 16 && buffer[0] === 255 && buffer[1] === 216 && buffer[2] === 255) return 'image/jpeg';
  if (buffer.length > 20 && buffer.toString('ascii', 0, 4) === 'RIFF' && buffer.toString('ascii', 8, 12) === 'WEBP') return 'image/webp';
  throw new Error('A PNG, JPEG or WebP image is required');
}
async function localImage(src, jobRoot) {
  const path = await plain(isAbsolute(src) ? src : resolve(jobRoot, src));
  if (!inside(jobRoot, path)) throw new Error('Scene assets must remain inside the job directory');
  const info = await stat(path);
  if (!info.isFile() || info.size > MAX_IMAGE_BYTES || info.size < 16) throw new Error('Invalid render image size');
  if (!['.png', '.jpg', '.jpeg', '.webp'].includes(extname(path).toLowerCase())) throw new Error('Unsupported render image extension');
  const buffer = await readFile(path);
  const mime = imageType(buffer);
  return {data: `data:${mime};base64,${buffer.toString('base64')}`,
    // Production matting outputs PNG. Derive this only from validated bytes;
    // callers cannot inject dimensions through the public scene JSON schema.
    sourceAspectRatio: mime === 'image/png' ? buffer.readUInt32BE(16) / buffer.readUInt32BE(20) : undefined};
}

export async function validateJob(requestFile, outputFile, {still = false} = {}) {
  const request = await plain(requestFile);
  const jobRoot = await plain(dirname(request));
  const info = await stat(request);
  if (!info.isFile() || info.size > 64 * 1024 || extname(request).toLowerCase() !== '.json') throw new Error('Invalid render request file');
  const raw = JSON.parse(await readFile(request, 'utf8'));
  const scene = normalizeScene(raw);
  const output = resolve(outputFile);
  const outputParent = await plain(dirname(output));
  if (!inside(jobRoot, outputParent) || !/^[\p{L}\p{N}_. -]{1,120}$/u.test(basename(output)) || extname(output).toLowerCase() !== (still ? '.png' : '.mp4')) throw new Error('Output must be a new local job artifact');
  try { await lstat(output); throw new Error('Output already exists; refusing to overwrite'); } catch (e) { if (e.code !== 'ENOENT') throw e; }
  const productImage = await localImage(scene.product.src, jobRoot);
  const normalized = {...scene, product: {...scene.product, src: productImage.data, sourceAspectRatio: productImage.sourceAspectRatio}, background: {...scene.background}};
  if (scene.background.src) normalized.background.src = (await localImage(scene.background.src, jobRoot)).data;
  let backgroundVideoPath;
  if (scene.background.videoSrc) {
    backgroundVideoPath = await plain(isAbsolute(scene.background.videoSrc) ? scene.background.videoSrc : resolve(jobRoot, scene.background.videoSrc));
    if (!inside(jobRoot, backgroundVideoPath)) throw new Error('Background video must remain inside the job directory');
    const videoInfo = await stat(backgroundVideoPath);
    if (!videoInfo.isFile() || videoInfo.size > 100 * 1024 * 1024 || videoInfo.size < 16 || extname(backgroundVideoPath).toLowerCase() !== '.mp4') throw new Error('Invalid background video');
    const handle = await open(backgroundVideoPath, 'r');
    const videoHead = Buffer.alloc(12);
    try { await handle.read(videoHead, 0, 12, 0); } finally { await handle.close(); }
    if (videoHead.toString('ascii', 4, 8) !== 'ftyp') throw new Error('An MP4 background is required');
    normalized.background.videoSrc = 'background.mp4';
  }
  return {scene: normalized, backgroundVideoPath, jobRoot, output, dimensions: sceneDimensions(scene)};
}

export async function renderJob({requestFile, outputFile, browserExecutable, concurrency = 1, onProgress = () => {}, signal, stillFrame}) {
  if (![1,2].includes(concurrency)) throw new Error('Concurrency must be 1 or 2');
  // A preinstalled local browser is required. No browser download or cloud
  // fallback is initiated by this adapter.
  if (!browserExecutable || !isAbsolute(browserExecutable)) throw new Error('A trusted local Chromium/Chrome/Edge executable is required');
  const browser = await plain(browserExecutable);
  if (!(await stat(browser)).isFile() || (process.platform === 'win32' && extname(browser).toLowerCase() !== '.exe')) throw new Error('Invalid browser executable');
  const job = await validateJob(requestFile, outputFile, {still: stillFrame !== undefined});
  const frames = Math.round(job.scene.durationSeconds * job.scene.fps);
  if (stillFrame !== undefined && (!Number.isInteger(stillFrame) || stillFrame < 0 || stillFrame >= frames)) throw new Error('Invalid still frame');
  if (signal?.aborted) throw new Error('Render cancelled');
  const work = await mkdtemp(join(job.jobRoot, '.remotion-'));
  const [{bundle}, renderer] = await Promise.all([import('@remotion/bundler'), import('@remotion/renderer')]);
  const publicDir = join(work, 'public');
  await mkdir(publicDir);
  if (job.backgroundVideoPath) {
    const metadata = await renderer.getVideoMetadata(job.backgroundVideoPath, {logLevel: 'error'});
    if (!Number.isFinite(metadata.durationInSeconds) || metadata.durationInSeconds <= 0 || metadata.durationInSeconds > 60 || metadata.width * metadata.height > 8_300_000) throw new Error('Background video dimensions or duration exceed the limit');
    job.scene.background.videoDurationSeconds = metadata.durationInSeconds;
    await copyFile(job.backgroundVideoPath, join(publicDir, 'background.mp4'));
  }
  onProgress({phase: 'bundling', progress: 0});
  // The installed renderer is verified as immutable by Launcher. Webpack's
  // default persistent cache would add node_modules/.cache files after the
  // first render, causing the next integrity check to reject a healthy app.
  const serveUrl = await bundle({entryPoint: join(ROOT, 'src/index.tsx'), publicDir, outDir: join(work, 'bundle'),
    enableCaching: false,
    onProgress: (percent) => onProgress({phase: 'bundling', progress: percent / 100})});
  if (signal?.aborted) throw new Error('Render cancelled');
  const cancel = renderer.makeCancelSignal();
  const abort = () => cancel.cancel();
  signal?.addEventListener('abort', abort, {once: true});
  if (signal?.aborted) cancel.cancel();
  const partial = join(work, stillFrame === undefined ? 'render.mp4' : 'frame.png');
  try {
    const composition = await renderer.selectComposition({serveUrl, id: 'ProductListing', inputProps: job.scene, browserExecutable: browser, logLevel: 'error'});
    onProgress({phase: 'rendering', progress: 0, totalFrames: frames});
    if (stillFrame !== undefined) {
      await renderer.renderStill({serveUrl, composition, inputProps: job.scene, output: partial, frame: stillFrame, imageFormat: 'png', browserExecutable: browser, logLevel: 'error'});
    } else {
      await renderer.renderMedia({
        serveUrl, composition, inputProps: job.scene, outputLocation: partial,
        codec: 'h264', pixelFormat: 'yuv420p', colorSpace: 'bt709', crf: 18, x264Preset: 'veryfast',
        muted: true, enforceAudioTrack: false, overwrite: false,
        concurrency, browserExecutable: browser, logLevel: 'error',
        cancelSignal: cancel.cancelSignal,
        onProgress: ({progress, renderedFrames, encodedFrames}) => onProgress({phase: 'rendering', progress, renderedFrames, encodedFrames, totalFrames: frames}),
      });
    }
    if (signal?.aborted) throw new Error('Render cancelled');
    const outputInfo = await stat(partial);
    if (outputInfo.size < 128) throw new Error('Renderer produced an empty artifact');
    // The destination was checked above; Windows rename refuses an existing
    // destination. Callers serialize jobs and own the entire job directory.
    try { await lstat(job.output); throw new Error('Output was created while rendering'); } catch (e) { if (e.code !== 'ENOENT') throw e; }
    await rename(partial, job.output);
    const result = {ok: true, renderer: 'remotion', rendererVersion: '4.0.539', motionMode: 'product-preserving-2.5d', output: job.output, ...job.dimensions, fps: job.scene.fps, durationSeconds: frames / job.scene.fps, frames, audio: false, fileSize: outputInfo.size};
    await writeFile(join(work, 'result.json'), JSON.stringify(result, null, 2) + '\n');
    onProgress({phase: 'complete', progress: 1, ...result});
    return result;
  } finally {
    signal?.removeEventListener('abort', abort);
  }
}

async function main() {
  const args = process.argv.slice(2), values = {};
  for (let i = 0; i < args.length; i += 2) {
    if (!['--request', '--output', '--browser', '--concurrency', '--still'].includes(args[i]) || !args[i+1] || values[args[i]] !== undefined) throw new Error('Usage: node render.mjs --request scene.json --output video.mp4 --browser chromium [--concurrency 1|2] [--still frame]');
    values[args[i]] = args[i+1];
  }
  const controller = new AbortController();
  process.once('SIGINT', () => controller.abort());
  process.once('SIGTERM', () => controller.abort());
  let lastProgressKey = '';
  const emit = (event) => {
    const key = `${event.phase}:${Math.floor((event.progress ?? 0) * 100)}`;
    if (key === lastProgressKey && event.phase !== 'complete') return;
    lastProgressKey = key;
    process.stdout.write(JSON.stringify(event) + '\n');
  };
  await renderJob({requestFile: values['--request'], outputFile: values['--output'], browserExecutable: values['--browser'], concurrency: Number(values['--concurrency'] ?? 1), stillFrame: values['--still'] === undefined ? undefined : Number(values['--still']), signal: controller.signal, onProgress: emit});
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  main().catch(error => {process.stderr.write(JSON.stringify({ok: false, error: error.message}) + '\n'); process.exitCode = 1;});
}
