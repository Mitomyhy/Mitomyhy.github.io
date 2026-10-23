import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash, webcrypto} from 'node:crypto';
import {sourceMoonLayout, validateMoonSourceManifest, verifyMoonSourcePNG, renderSourceMoons} from '../moon-source-renderer.js';

const assetRoot = new URL('../assets/sky-moon/moonphases-original/', import.meta.url);
const sourceRoot = 'https://moonphases.co.uk/images/moons/big/';
const originalManifest = JSON.parse(await readFile(new URL('manifest.json', assetRoot), 'utf8'));
const assets = new Map(await Promise.all(originalManifest.images.map(async entry => [entry.file, new Uint8Array(await readFile(new URL(entry.file, assetRoot)))])));
const clone = value => structuredClone(value);
const codeIs = code => error => error.code === code;
const metadata = (date = '2000-05-11', image = '12.png', tilt = 17.7) => ({
  dateISO: date, sourceDateSerial: date.replaceAll('-', ''), phaseImage: image,
  sourceImageURL: sourceRoot + image, phaseName: 'First Quarter', illumination: 57,
  providerTilt: tilt, apiURL: 'https://moonphases.co.uk/service/getMoonDetails.php?day=11&month=5&year=2000'
});
const options = (patch = {}) => ({dates: ['2000-05-11'], providerMetadata: [metadata()], size: 400, transparent: true, ...patch});

// This canvas records native draw operations instead of implementing a second
// renderer. Browser QA separately checks real source and output RGBA pixels.
class RecordingCanvas {
  constructor(width = 37, height = 29) {
    this.width = width; this.height = height; this.operations = [];
    this.context = Object.fromEntries(['save', 'restore', 'translate', 'rotate', 'fillRect', 'fillText', 'drawImage'].map(name => [name, (...args) => this.operations.push([name, ...args])]));
  }
  getContext(kind) { assert.equal(kind, '2d'); return this.context; }
}

function fixtureRuntime(patch = {}) {
  const state = {requests: [], canvases: [], resources: [], active: 0, maximumActive: 0, decodes: 0};
  const runtime = {
    cryptoImpl: webcrypto,
    fetchImpl: async (url, init) => {
      const parsed = new URL(url), name = parsed.pathname.split('/').at(-1);
      state.requests.push({url, init, name});
      assert.equal(new URL(url).href, new URL(name, assetRoot).href, 'only the fixed local asset directory is requested');
      assert.equal(init.method, 'GET'); assert.equal(init.credentials, 'omit'); assert.equal(init.mode, 'same-origin'); assert.equal(init.redirect, 'error');
      const bytes = name === 'manifest.json' ? new TextEncoder().encode(JSON.stringify(originalManifest)) : assets.get(name);
      assert.ok(bytes, 'request must be a known source asset');
      return new Response(bytes, {headers: {'content-length': String(bytes.byteLength)}});
    },
    canvasFactory: (width, height) => {
      const canvas = new RecordingCanvas(width, height); state.canvases.push(canvas); return canvas;
    },
    decodeImage: async bytes => {
      state.decodes++; state.active++; state.maximumActive = Math.max(state.maximumActive, state.active);
      const resource = {image: {type: 'unchanged-source-image', bytes}, width: 1000, height: 1000, closes: 0,
        close() { this.closes++; state.active--; }};
      state.resources.push(resource); return resource;
    },
    yieldTask: async () => {},
    ...patch
  };
  return {runtime, state};
}

test('source layout keeps internal ISO and the exact intended MM/DD/YYYY without machine-local parsing', () => {
  const dates = ['2000-05-11', '2005-06-15', '2015-08-10', '2024-02-29'];
  const layout = sourceMoonLayout(options({dates, providerMetadata: dates.map(date => metadata(date))}));
  assert.deepEqual(layout.items.map(item => item.display), ['05/11/2000', '06/15/2005', '08/10/2015', '02/29/2024']);
  assert.deepEqual(layout.items.map(item => item.sourceDateSerial), ['20000511', '20050615', '20150810', '20240229']);
  assert.equal(layout.columns, 3); assert.equal(layout.rows, 2); assert.equal(layout.width, 1200); assert.equal(layout.height, 904);
  for (const date of ['05/11/2000', '2000-5-11', '2024-02-30', '2023-02-29', '1949-12-31', '2051-01-01', '', null, new Date()]) {
    assert.throws(() => sourceMoonLayout(options({dates: [date], providerMetadata: [metadata(date?.toString?.() ?? '')]})), codeIs('MOON_SOURCE_DATE'), String(date));
  }
  assert.throws(() => sourceMoonLayout(options({dateOrder: 'DMY'})), codeIs('MOON_SOURCE_DATE'));
  assert.equal(sourceMoonLayout(options({dateOrder: 'MDY'})).items[0].display, '05/11/2000');
});

test('source layout rejects missing, reordered, wrong-date and non-normalized provider metadata', () => {
  for (const providerMetadata of [undefined, [], [metadata(), metadata()], [null], [{...metadata(), dateISO: '2000-11-05'}], [{...metadata(), sourceDateSerial: '20001105'}], [{...metadata(), sourceDateSerial: 20000511}]]) {
    assert.throws(() => sourceMoonLayout(options({providerMetadata})));
  }
  for (const patch of [{illumination: '57'}, {illumination: -1}, {illumination: 101}, {illumination: NaN}, {providerTilt: Infinity}, {providerTilt: 181}, {providerTilt: -181}, {phaseName: 'unknown'}]) {
    assert.throws(() => sourceMoonLayout(options({providerMetadata: [{...metadata(), ...patch}]})));
  }
  const list = sourceMoonLayout(options({providerMetadata: [{...metadata(), providerTilt: -180, illumination: 0}]}));
  assert.equal(list.items[0].providerTilt, -180); assert.equal(list.items[0].fraction, 0);
});

test('only phase leaves 0.png through 15.png and their exact provider URLs are accepted', () => {
  for (let phase = 0; phase < 16; phase++) {
    const item = sourceMoonLayout(options({providerMetadata: [metadata('2000-05-11', `${phase}.png`)]})).items[0];
    assert.equal(item.phaseImage, `${phase}.png`); assert.equal(item.originalAssetURL, new URL(`${phase}.png`, assetRoot).href);
  }
  for (const phase of ['16.png', '-1.png', '01.png', '12.PNG', '../12.png', 'https://evil.invalid/12.png', '12.png?q=1', null]) {
    assert.throws(() => sourceMoonLayout(options({providerMetadata: [metadata('2000-05-11', phase)]})), codeIs('MOON_SOURCE_ASSET'));
  }
  for (const sourceImageURL of ['https://evil.invalid/12.png', sourceRoot + '13.png', sourceRoot + '12.png?download', 'http://moonphases.co.uk/images/moons/big/12.png']) {
    assert.throws(() => sourceMoonLayout(options({providerMetadata: [{...metadata(), sourceImageURL}]})), codeIs('MOON_SOURCE_METADATA'));
  }
});

test('canvas limits and option types are validated before any allocation or request', async () => {
  for (const patch of [{dates: [], providerMetadata: []}, {dates: Array(61).fill('2000-05-11'), providerMetadata: Array.from({length: 61}, () => metadata())}, {size: 99}, {size: 1201}, {size: 400.5}, {size: '400'}, {columns: 0}, {columns: 13}, {columns: 2.5}, {background: 'url(evil)'}, {labelColor: '#fff'}]) {
    const {runtime, state} = fixtureRuntime();
    await assert.rejects(renderSourceMoons(new RecordingCanvas(), options({...patch, runtime})));
    assert.equal(state.requests.length, 0); assert.equal(state.canvases.length, 0);
  }
  const dates = Array(60).fill('2000-05-11'), providerMetadata = dates.map(date => metadata(date));
  assert.throws(() => sourceMoonLayout(options({dates, providerMetadata, columns: 12, size: 1200})), codeIs('MOON_SOURCE_LIMIT'));
  assert.throws(() => sourceMoonLayout(options({dates: dates.slice(0, 8), providerMetadata: providerMetadata.slice(0, 8), columns: 8, size: 1200})), codeIs('MOON_SOURCE_LIMIT'), '8192 side cap applies below 16MP');
  const allowed = sourceMoonLayout(options({dates, providerMetadata, columns: 12, size: 100, showLabels: false}));
  assert.equal(allowed.width, 1200); assert.equal(allowed.height, 500); assert.equal(allowed.labelH, 0);
});

test('manifest contains exactly one immutable same-origin leaf for every provider phase', () => {
  const validated = validateMoonSourceManifest(originalManifest);
  assert.equal(validated.size, 16); assert.deepEqual([...validated.keys()], Array.from({length: 16}, (_, i) => `${i}.png`));
  for (const mutate of [m => {m.version = 2;}, m => {m.provider = 'other';}, m => {m.images.pop();}, m => {m.images[15] = m.images[0];}, m => {m.images[0].file = '../0.png';}, m => {m.images[0].sourceURL = 'https://evil.invalid/0.png';}, m => {m.images[0].sha256 = 'wrong';}, m => {m.images[0].bytes = 8 * 1024 * 1024 + 1;}, m => {m.images[0].width = 2049;}, m => {m.images[0].height = 0;}]) {
    const manifest = clone(originalManifest); mutate(manifest); assert.throws(() => validateMoonSourceManifest(manifest));
  }
});

test('all 16 distributed originals match their recorded byte counts, PNG dimensions and SHA-256 pins', async () => {
  for (const entry of originalManifest.images) {
    const bytes = assets.get(entry.file);
    assert.equal(bytes.length, entry.bytes, entry.file); assert.equal(entry.width, 1000); assert.equal(entry.height, 1000);
    assert.equal(entry.bitDepth, 8); assert.equal(entry.colorType, 6, 'original RGBA PNG alpha is kept');
    assert.equal(createHash('sha256').update(bytes).digest('hex'), entry.sha256, entry.file);
    assert.equal(await verifyMoonSourcePNG(bytes, entry, webcrypto), true);
  }
});

test('PNG verification refuses truncation, signature/header/dimension changes and altered original pixels', async () => {
  const entry = originalManifest.images[12], original = assets.get(entry.file);
  for (const bytes of [original.slice(0, -1), (() => {const x = original.slice(); x[0] ^= 1; return x;})(), (() => {const x = original.slice(); x[12] ^= 1; return x;})(), (() => {const x = original.slice(); x[19] ^= 1; return x;})(), (() => {const x = original.slice(); x[x.length - 20] ^= 1; return x;})()]) {
    await assert.rejects(verifyMoonSourcePNG(bytes, entry, webcrypto), codeIs('MOON_SOURCE_ASSET'));
  }
  await assert.rejects(verifyMoonSourcePNG(original, entry, {}), codeIs('MOON_SOURCE_ASSET'));
});

test('original source images are drawn natively and tilted clockwise; combined output matches panel 94% sizing', async () => {
  const {runtime, state} = fixtureRuntime(), target = new RecordingCanvas();
  const result = await renderSourceMoons(target, options({runtime}));
  assert.equal(result.kind, 'moon'); assert.equal(result.sourceImages, 'original-provider-png'); assert.match(result.note, /no reconstructed shading or recoloring/);
  assert.equal(result.width, 400); assert.equal(result.height, 452); assert.equal(target.width, 400); assert.equal(target.height, 452);
  assert.equal(state.requests.length, 2); assert.deepEqual(state.requests.map(request => request.name), ['manifest.json', '12.png']);
  assert.equal(state.resources[0].closes, 1); assert.equal(state.active, 0);
  const tile = result.items[0].canvas, draw = tile.operations.find(op => op[0] === 'drawImage');
  assert.deepEqual(tile.operations.filter(op => ['save', 'translate', 'rotate', 'restore'].includes(op[0])), [['save'], ['translate', 200, 200], ['rotate', 17.7 * Math.PI / 180], ['restore']]);
  assert.equal(draw[1], state.resources[0].image); assert.deepEqual(draw.slice(2), [-200, -200, 400, 400]);
  assert.equal(tile.context.imageSmoothingEnabled, true); assert.equal(tile.context.imageSmoothingQuality, 'high');
  const composite = state.canvases[0];
  assert.deepEqual(composite.operations.find(op => op[0] === 'drawImage'), ['drawImage', tile, 12, 12, 376, 376]);
  assert.deepEqual(composite.operations.filter(op => op[0] === 'fillText').map(op => op[1]), ['05/11/2000', '57.0% · First Quarter']);
  assert.equal(composite.operations.some(op => op[0] === 'fillRect'), false, 'transparent output is not painted black');
  assert.deepEqual(target.operations, [['drawImage', composite, 0, 0]], 'one final target publication only');
  assert.equal(composite.width, 1, 'working canvas is released after publication'); assert.equal(tile.width, 400, 'individual export remains usable');
  assert.equal(result.items[0].sourceImageURL, sourceRoot + '12.png');
});

test('each distinct original is decoded once, reused for matching dates, and closed before the next image', async () => {
  const dates = ['2000-05-11', '2005-06-15', '2015-08-10'];
  const providerMetadata = [metadata(dates[0], '12.png', 0), metadata(dates[1], '13.png', 90), metadata(dates[2], '12.png', -90)];
  const {runtime, state} = fixtureRuntime();
  const result = await renderSourceMoons(new RecordingCanvas(), options({dates, providerMetadata, size: 100, columns: 2, showLabels: false, transparent: false, background: '#123456', runtime}));
  assert.equal(state.decodes, 2); assert.equal(state.maximumActive, 1); assert.equal(state.active, 0); assert.ok(state.resources.every(value => value.closes === 1));
  assert.deepEqual(state.requests.map(request => request.name), ['manifest.json', '12.png', '13.png']);
  assert.equal(result.width, 200); assert.equal(result.height, 200); assert.equal(new Set(result.items.map(item => item.canvas)).size, 3);
  const composed = state.canvases[0]; assert.deepEqual(composed.operations[0], ['fillRect', 0, 0, 200, 200]); assert.equal(composed.context.fillStyle, '#123456');
  assert.equal(composed.operations.some(op => op[0] === 'fillText'), false);
  assert.deepEqual(result.items.map(item => item.canvas.operations.find(op => op[0] === 'rotate')[1]), [0, Math.PI / 2, -Math.PI / 2]);
  assert.deepEqual(composed.operations.filter(op => op[0] === 'drawImage').map(op => op.slice(2)), [[3, 3, 94, 94], [3, 103, 94, 94], [103, 3, 94, 94]], 'composition positions remain in original date order even when source assets are grouped');
});

test('failed original download never mutates a previously visible result and releases temporary canvases', async () => {
  const target = new RecordingCanvas(), {runtime, state} = fixtureRuntime();
  const originalFetch = runtime.fetchImpl;
  runtime.fetchImpl = (url, init) => String(url).endsWith('13.png') ? Promise.resolve(new Response('unavailable', {status: 503})) : originalFetch(url, init);
  await assert.rejects(renderSourceMoons(target, options({dates: ['2000-05-11', '2005-06-15'], providerMetadata: [metadata(), metadata('2005-06-15', '13.png')], runtime})), codeIs('MOON_SOURCE_HTTP'));
  assert.equal(target.width, 37); assert.equal(target.height, 29); assert.deepEqual(target.operations, []);
  assert.ok(state.canvases.length >= 2); assert.ok(state.canvases.every(canvas => canvas.width === 1 && canvas.height === 1));
  assert.equal(state.resources[0].closes, 1);
});

test('bad decoded dimensions or errors dispose their resource and do not publish', async () => {
  for (const kind of ['dimensions', 'draw']) {
    const {runtime, state} = fixtureRuntime(), target = new RecordingCanvas(), baseDecode = runtime.decodeImage;
    runtime.decodeImage = async (...args) => {const resource = await baseDecode(...args); if (kind === 'dimensions') resource.width = 999; return resource;};
    if (kind === 'draw') runtime.canvasFactory = (width, height) => {const canvas = new RecordingCanvas(width, height); state.canvases.push(canvas); if (state.canvases.length === 2) canvas.context.drawImage = () => {throw new Error('canvas failed');}; return canvas;};
    await assert.rejects(renderSourceMoons(target, options({runtime})), codeIs(kind === 'dimensions' ? 'MOON_SOURCE_DECODE' : 'MOON_SOURCE_RENDER'));
    assert.equal(state.resources[0].closes, 1); assert.deepEqual(target.operations, []); assert.equal(target.width, 37);
    assert.ok(state.canvases.every(canvas => canvas.width === 1));
  }
});

test('already-aborted render validates no metadata and performs no network/allocation work', async () => {
  const stopped = new AbortController(); stopped.abort(); const {runtime, state} = fixtureRuntime();
  await assert.rejects(renderSourceMoons(new RecordingCanvas(), {signal: stopped.signal, runtime}), error => error.code === 'CANCELLED' && error.name === 'AbortError');
  assert.equal(state.requests.length, 0); assert.equal(state.canvases.length, 0);
});

test('a non-cooperating in-flight fetch remains immediately cancellable', async () => {
  const controller = new AbortController(), {runtime, state} = fixtureRuntime({fetchImpl: () => new Promise(() => {})});
  const target = new RecordingCanvas(), operation = renderSourceMoons(target, options({runtime, signal: controller.signal}));
  controller.abort(); await assert.rejects(operation, codeIs('CANCELLED'));
  assert.deepEqual(target.operations, []); assert.equal(target.width, 37); assert.equal(state.canvases.length, 0);
});

test('cancellation during composition destroys partial tiles and closes the original before returning', async () => {
  const controller = new AbortController(), {runtime, state} = fixtureRuntime({yieldTask: async () => controller.abort()});
  const target = new RecordingCanvas();
  await assert.rejects(renderSourceMoons(target, options({runtime, signal: controller.signal})), codeIs('CANCELLED'));
  assert.deepEqual(target.operations, []); assert.equal(target.width, 37); assert.equal(state.resources[0].closes, 1);
  assert.ok(state.canvases.every(canvas => canvas.width === 1));
});

test('a decoder that completes after cancellation has its late bitmap closed exactly once', async () => {
  let resolveDecode, decoderStarted;
  const started = new Promise(resolve => {decoderStarted = resolve;}), controller = new AbortController();
  const {runtime, state} = fixtureRuntime({decodeImage: () => {decoderStarted(); return new Promise(resolve => {resolveDecode = resolve;});}});
  const target = new RecordingCanvas(), operation = renderSourceMoons(target, options({runtime, signal: controller.signal}));
  await started; controller.abort(); await assert.rejects(operation, codeIs('CANCELLED'));
  let closes = 0; resolveDecode({image: {}, width: 1000, height: 1000, close() {closes++;}});
  await new Promise(resolve => setImmediate(resolve)); assert.equal(closes, 1);
  assert.deepEqual(target.operations, []); assert.ok(state.canvases.every(canvas => canvas.width === 1));
});

test('declared wrong sizes, oversized manifest and truncated source bodies are refused without decoding', async () => {
  for (const kind of ['declared', 'oversized-manifest', 'truncated', 'malformed-manifest']) {
    const {runtime, state} = fixtureRuntime(), baseFetch = runtime.fetchImpl;
    runtime.fetchImpl = async (url, init) => {
      if (String(url).endsWith('manifest.json')) {
        if (kind === 'oversized-manifest') return new Response('{}', {headers: {'content-length': String(64 * 1024 + 1)}});
        if (kind === 'malformed-manifest') return new Response('<html>not JSON</html>');
        return baseFetch(url, init);
      }
      const bytes = assets.get('12.png');
      if (kind === 'declared') return new Response(bytes, {headers: {'content-length': String(bytes.length + 1)}});
      if (kind === 'truncated') return new Response(bytes.slice(0, -1));
      return baseFetch(url, init);
    };
    const target = new RecordingCanvas();
    await assert.rejects(renderSourceMoons(target, options({runtime})), codeIs(kind === 'malformed-manifest' ? 'MOON_SOURCE_MANIFEST' : 'MOON_SOURCE_ASSET'));
    assert.equal(state.decodes, 0); assert.deepEqual(target.operations, []); assert.equal(target.width, 37);
  }
});

test('oversized streamed source data and cancelled reads cancel the body reader', async () => {
  for (const kind of ['oversized', 'cancelled']) {
    let bodyCancelled = false, readStarted;
    const reading = new Promise(resolve => {readStarted = resolve;}), controller = new AbortController();
    const {runtime, state} = fixtureRuntime(), baseFetch = runtime.fetchImpl;
    runtime.fetchImpl = async (url, init) => {
      if (String(url).endsWith('manifest.json')) return baseFetch(url, init);
      const body = new ReadableStream({start(stream) {if (kind === 'oversized') stream.enqueue(new Uint8Array(assets.get('12.png').length + 1));}, cancel() {bodyCancelled = true;}});
      const reader = body.getReader();
      return {ok: true, headers: new Headers(), body: {getReader: () => ({
        read() {readStarted(); return reader.read();}, cancel: () => reader.cancel(), releaseLock: () => reader.releaseLock()
      })}};
    };
    const target = new RecordingCanvas(), operation = renderSourceMoons(target, options({runtime, signal: controller.signal}));
    await reading; if (kind === 'cancelled') controller.abort();
    await assert.rejects(operation, codeIs(kind === 'cancelled' ? 'CANCELLED' : 'MOON_SOURCE_ASSET'));
    assert.equal(bodyCancelled, true); assert.equal(state.decodes, 0); assert.deepEqual(target.operations, []);
  }
});

test('missing SHA-256 support fails closed instead of displaying an unverified or reconstructed image', async () => {
  const {runtime, state} = fixtureRuntime({cryptoImpl: {}}), target = new RecordingCanvas();
  await assert.rejects(renderSourceMoons(target, options({runtime})), codeIs('MOON_SOURCE_ASSET'));
  assert.equal(state.decodes, 0); assert.deepEqual(target.operations, []);
});

test('the default bitmap decoder preserves original PNG bytes and disables implicit color/orientation conversion', async () => {
  const previous = globalThis.createImageBitmap; let bitmapCloses = 0, actualBytes, actualOptions;
  globalThis.createImageBitmap = async (blob, decodeOptions) => {
    assert.equal(blob.type, 'image/png'); actualBytes = new Uint8Array(await blob.arrayBuffer()); actualOptions = decodeOptions;
    return {width: 1000, height: 1000, close() {bitmapCloses++;}};
  };
  try {
    const {runtime} = fixtureRuntime(); delete runtime.decodeImage;
    const result = await renderSourceMoons(new RecordingCanvas(), options({runtime, providerMetadata: [metadata('2000-05-11', '12.png', 0)], size: 1000}));
    assert.deepEqual(actualOptions, {imageOrientation: 'none', premultiplyAlpha: 'none', colorSpaceConversion: 'none'});
    assert.deepEqual(actualBytes, assets.get('12.png')); assert.equal(bitmapCloses, 1);
    const draw = result.items[0].canvas.operations.find(operation => operation[0] === 'drawImage');
    assert.deepEqual(draw.slice(2), [-500, -500, 1000, 1000], 'native resolution has no resampling before per-Moon export');
  } finally { if (previous === undefined) delete globalThis.createImageBitmap; else globalThis.createImageBitmap = previous; }
});

test('rejected HTTP/length headers cancel unread response bodies before returning', async () => {
  for (const kind of ['status', 'length']) {
    let responseCancelled = false;
    const {runtime} = fixtureRuntime({fetchImpl: async () => ({ok: kind !== 'status', status: 403,
      headers: new Headers(kind === 'length' ? {'content-length': '65537'} : {}),
      body: {cancel() {responseCancelled = true; return Promise.resolve();}}
    })});
    await assert.rejects(renderSourceMoons(new RecordingCanvas(), options({runtime})), codeIs(kind === 'status' ? 'MOON_SOURCE_HTTP' : 'MOON_SOURCE_ASSET'));
    assert.equal(responseCancelled, true);
  }
});

test('late non-cooperating fetch responses are discarded after cancellation', async () => {
  let resolveFetch;
  const controller = new AbortController(), {runtime} = fixtureRuntime({fetchImpl: () => new Promise(resolve => {resolveFetch = resolve;})});
  const operation = renderSourceMoons(new RecordingCanvas(), options({runtime, signal: controller.signal}));
  controller.abort(); await assert.rejects(operation, codeIs('CANCELLED'));
  let cancelled = 0; resolveFetch({body: {cancel() {cancelled++; return Promise.resolve();}}});
  await new Promise(resolve => setImmediate(resolve)); assert.equal(cancelled, 1);
});

test('temporary canvases are released even if their 2D context cannot be allocated', async () => {
  for (const failAt of [1, 2]) {
    const {runtime, state} = fixtureRuntime();
    runtime.canvasFactory = (width, height) => {
      const canvas = new RecordingCanvas(width, height); state.canvases.push(canvas);
      if (state.canvases.length === failAt) canvas.getContext = () => null;
      return canvas;
    };
    const target = new RecordingCanvas();
    await assert.rejects(renderSourceMoons(target, options({runtime})), codeIs('MOON_SOURCE_CANVAS'));
    assert.ok(state.canvases.every(canvas => canvas.width === 1 && canvas.height === 1));
    assert.deepEqual(target.operations, []); assert.equal(target.width, 37);
    assert.ok(state.resources.every(resource => resource.closes === 1));
  }
});
