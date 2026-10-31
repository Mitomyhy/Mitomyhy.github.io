/** QA only: a real MP4 while external Node and composition HTTP is denied.
 * Does not modify browser security settings, operating-system firewall, or
 * Remotion licensing. Chromium's own background services are outside this audit.
 */
import assert from 'node:assert/strict';
import http from 'node:http';
import https from 'node:https';
import net from 'node:net';
import {syncBuiltinESMExports} from 'node:module';
import {mkdir, mkdtemp, writeFile, stat} from 'node:fs/promises';
import {join, resolve} from 'node:path';

const qa = resolve('qa');
await mkdir(qa, {recursive: true});
const work = await mkdtemp(join(qa, 'offline-'));
const audit = {scope: 'Node outbound sockets/fetch and composition page requests',
  chromiumBackgroundServicesAudited: false, allowedNodeConnections: 0,
  allowedPageRequests: 0, blockedExternalRequests: [], rendererVersion: '4.0.539'};
const localHost = host => !host || ['localhost', '127.0.0.1', '::1', '[::1]'].includes(String(host).toLowerCase());
function deny(host, transport) {
  audit.blockedExternalRequests.push({host: String(host), transport});
  throw new Error(`Offline QA denied ${transport} to ${host}`);
}
function checkURL(input, transport) {
  const url = new URL(typeof input === 'string' || input instanceof URL ? input : input.url);
  if (['data:', 'blob:', 'about:'].includes(url.protocol)) return;
  if (!['http:', 'https:', 'ws:', 'wss:'].includes(url.protocol) || !localHost(url.hostname)) deny(url.hostname, transport);
}
const originalFetch = globalThis.fetch;
globalThis.fetch = function(input, ...rest) {
  checkURL(input, 'fetch');
  return originalFetch.call(this, input, ...rest);
};
for (const [module, transport] of [[http, 'http'], [https, 'https']]) {
  for (const method of ['request', 'get']) {
    const original = module[method];
    module[method] = function(input, ...rest) {
      if (typeof input === 'string' || input instanceof URL) checkURL(input, transport);
      else if (!localHost(input?.hostname ?? input?.host?.split(':')[0])) deny(input.hostname ?? input.host, transport);
      return original.call(this, input, ...rest);
    };
  }
}
const originalConnect = net.Socket.prototype.connect;
net.Socket.prototype.connect = function(...args) {
  let options = args[0];
  if (Array.isArray(options)) options = options[0];
  if (typeof options === 'object' && options?.path) {
    // CDP transport may use a local named pipe, never a network share.
    if (!String(options.path).startsWith('\\\\.\\pipe\\')) deny('nonlocal-pipe', 'socket');
  } else {
    const host = typeof options === 'object' ? options?.host : typeof args[1] === 'string' ? args[1] : undefined;
    if (!localHost(host)) deny(host, 'socket');
  }
  audit.allowedNodeConnections++;
  return originalConnect.apply(this, args);
};
syncBuiltinESMExports();

const browserExecutable = process.argv[2];
if (!browserExecutable) throw new Error('Provide a trusted local Chromium/Edge executable as argument');
const [{bundle}, renderer, {validateJob}] = await Promise.all([
  import('@remotion/bundler'), import('@remotion/renderer'), import('../render.mjs'),
]);
const output = join(qa, `offline-verified-${Date.now()}.mp4`);
const job = await validateJob(join(qa, 'landscape.json'), output);
const browser = await renderer.openBrowser('chrome', {browserExecutable, logLevel: 'error'});
const newPage = browser.newPage.bind(browser);
browser.newPage = async options => {
  const page = await newPage(options);
  const client = page._client();
  client.on('Fetch.requestPaused', async event => {
    try {
      checkURL(event.request.url, 'browser-page');
      audit.allowedPageRequests++;
      await client.send('Fetch.continueRequest', {requestId: event.requestId});
    } catch {
      await client.send('Fetch.failRequest', {requestId: event.requestId, errorReason: 'BlockedByClient'});
    }
  });
  await client.send('Fetch.enable', {patterns: [{urlPattern: '*', requestStage: 'Request'}]});
  return page;
};
try {
  const publicDir = join(work, 'public');
  await mkdir(publicDir);
  const serveUrl = await bundle({entryPoint: resolve('src/index.tsx'), publicDir,
    outDir: join(work, 'bundle'), enableCaching: false});
  const composition = await renderer.selectComposition({serveUrl, id: 'ProductListing', inputProps: job.scene,
    puppeteerInstance: browser, logLevel: 'error'});
  // No licenseKey or apiKey is supplied. Remotion 4.x supports voluntary
  // telemetry opt-out; this test does not bypass a license check.
  await renderer.renderMedia({serveUrl, composition, inputProps: job.scene, outputLocation: output,
    puppeteerInstance: browser, logLevel: 'error', codec: 'h264', pixelFormat: 'yuv420p', colorSpace: 'bt709',
    crf: 18, x264Preset: 'veryfast', muted: true, enforceAudioTrack: false, concurrency: 1});
  assert.deepEqual(audit.blockedExternalRequests, [], 'Rendering attempted an external request');
  assert.ok(audit.allowedPageRequests > 0, 'Page interception did not observe the composition');
  assert.ok((await stat(output)).size > 128);
  audit.output = output;
  audit.ok = true;
} finally {
  await browser.close({silent: true});
  await writeFile(join(qa, 'offline-network-verification.json'), JSON.stringify(audit, null, 2) + '\n');
}
process.stdout.write(JSON.stringify(audit) + '\n');
