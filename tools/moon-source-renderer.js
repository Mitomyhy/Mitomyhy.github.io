/* Production Moon art: use the provider's original PNG pixels, not a sphere,
 * texture, light mask, tint, blur or reconstructed phase. Only resize and the
 * clockwise whole-image tilt returned by the date-only provider are applied.
 * All asset requests stay on this website; there is no image proxy/fallback. */
const ASSET_ROOT = new URL('./assets/sky-moon/moonphases-original/', import.meta.url);
const SOURCE_ROOT = 'https://moonphases.co.uk/images/moons/big/';
const MAX_PIXELS = 16777216, MAX_BYTES = 8 * 1024 * 1024, MAX_MANIFEST = 64 * 1024;
const TIMEOUT_MS = 30000, PNG_SIGNATURE = [137, 80, 78, 71, 13, 10, 26, 10];
const PHASE_NAMES = new Set(['New Moon', 'Waxing Crescent', 'First Quarter', 'Waxing Gibbous', 'Full Moon', 'Waning Gibbous', 'Last Quarter', 'Third Quarter', 'Waning Crescent']);
function failure(code, message, cause) { const error = new Error(message); error.code = code; if (cause) error.cause = cause; return error; }
function cancelled() { const error = failure('CANCELLED', 'Đã hủy tạo Moon Phase; không xuất kết quả dở dang.'); error.name = 'AbortError'; return error; }
function check(signal) { if (signal?.aborted) throw cancelled(); }
function integer(value, low, high, label) { if (!Number.isInteger(value) || value < low || value > high) throw failure('MOON_SOURCE_INPUT', `${label} phải là số nguyên từ ${low} đến ${high}.`); return value; }
function finite(value, low, high, label) { if (typeof value !== 'number' || !Number.isFinite(value) || value < low || value > high) throw failure('MOON_SOURCE_INPUT', `${label} không hợp lệ.`); return value; }
function phaseImage(value) { if (typeof value !== 'string' || !/^(?:[0-9]|1[0-5])\.png$/.test(value)) throw failure('MOON_SOURCE_ASSET', 'Ảnh Moon phải nằm trong bộ nguồn 0.png–15.png.'); return value; }
function color(value, fallback) { if (value === undefined) return fallback; if (typeof value !== 'string' || !/^#[0-9a-f]{6}$/i.test(value)) throw failure('MOON_SOURCE_INPUT', 'Màu nền/nhãn Moon không hợp lệ.'); return value; }
function dateISO(value) {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) throw failure('MOON_SOURCE_DATE', 'Ngày Moon phải được đọc từ MM/DD/YYYY trước khi tạo ảnh.');
  const [year, month, day] = value.split('-').map(Number), date = new Date(Date.UTC(year, month - 1, day));
  if (year < 1950 || year > 2050 || date.getUTCFullYear() !== year || date.getUTCMonth() !== month - 1 || date.getUTCDate() !== day) throw failure('MOON_SOURCE_DATE', 'Ngày Moon không hợp lệ hoặc nằm ngoài 1950–2050.');
  return {iso: value, serial: value.replaceAll('-', ''), display: `${value.slice(5, 7)}/${value.slice(8)}/${value.slice(0, 4)}`};
}

/** Pure validation/layout, exported for regression tests and preflight UI. */
export function sourceMoonLayout(options = {}) {
  if (options.dateOrder !== undefined && options.dateOrder !== 'MDY') throw failure('MOON_SOURCE_DATE', 'Moon Phase chỉ dùng MM/DD/YYYY.');
  if (!Array.isArray(options.dates) || options.dates.length < 1 || options.dates.length > 60) throw failure('MOON_SOURCE_INPUT', 'Nhập từ 1 đến 60 ngày Moon.');
  if (!Array.isArray(options.providerMetadata) || options.providerMetadata.length !== options.dates.length) throw failure('MOON_SOURCE_METADATA', 'Dữ liệu nguồn Moon không khớp danh sách ngày.');
  const size = integer(options.size ?? 400, 100, 1200, 'Size mỗi Moon'), columns = integer(options.columns ?? 3, 1, 12, 'Số cột');
  const items = options.dates.map((value, i) => {
    const date = dateISO(value), source = options.providerMetadata[i];
    if (!source || source.dateISO !== date.iso || source.sourceDateSerial !== date.serial) throw failure('MOON_SOURCE_DATE', 'Nguồn Moon trả sai ngày; không xuất ảnh ngày khác.');
    const image = phaseImage(source.phaseImage);
    if (source.sourceImageURL !== SOURCE_ROOT + image || !PHASE_NAMES.has(source.phaseName)) throw failure('MOON_SOURCE_METADATA', 'Nguồn ảnh/pha Moon không hợp lệ.');
    const illumination = finite(source.illumination, 0, 100, 'Độ chiếu sáng'), providerTilt = finite(source.providerTilt, -180, 180, 'Góc xoay nguồn');
    return {date: date.iso, dateISO: date.iso, display: date.display, displayMMDD: date.display, sourceDateSerial: date.serial,
      phaseImage: image, sourceImageURL: SOURCE_ROOT + image, originalAssetURL: new URL(image, ASSET_ROOT).href,
      illumination, fraction: illumination / 100, phaseName: source.phaseName, providerTilt,
      apiURL: typeof source.apiURL === 'string' && source.apiURL.startsWith('https://moonphases.co.uk/service/getMoonDetails.php?') ? source.apiURL : undefined,
      model: 'MoonPhases original PNG + provider clockwise tilt'};
  });
  const cols = Math.min(columns, items.length), rows = Math.ceil(items.length / cols), labelH = options.showLabels === false ? 0 : Math.max(34, Math.round(size * .13));
  const width = cols * size, height = rows * (size + labelH);
  if (width > 8192 || height > 8192 || width * height > MAX_PIXELS) throw failure('MOON_SOURCE_LIMIT', 'PNG ghép quá lớn. Giảm size hoặc số ngày (tối đa 16 megapixel / 8192 px).');
  return {items, size, columns: cols, rows, labelH, width, height, transparent: options.transparent === true,
    background: color(options.background, '#080d18'), labelColor: color(options.labelColor, '#d8deec')};
}

/** Every source filename and origin is fixed. Neither provider JSON nor the
 * asset manifest may cause arbitrary remote/path requests. */
export function validateMoonSourceManifest(manifest) {
  if (manifest?.version !== 1 || manifest.provider !== 'MoonPhases.co.uk' || !Array.isArray(manifest.images) || manifest.images.length !== 16) throw failure('MOON_SOURCE_MANIFEST', 'Manifest ảnh MoonPhases không đúng bộ 16 ảnh nguồn.');
  const found = new Map();
  for (const entry of manifest.images) {
    const image = phaseImage(entry?.phaseImage);
    if (found.has(image) || entry.file !== image || entry.sourceURL !== SOURCE_ROOT + image || typeof entry.sha256 !== 'string' || !/^[0-9a-f]{64}$/.test(entry.sha256)) throw failure('MOON_SOURCE_MANIFEST', 'Manifest Moon có ảnh trùng, sai đường dẫn hoặc thiếu mã kiểm tra.');
    if (!Number.isInteger(entry.bytes) || entry.bytes < 33 || entry.bytes > MAX_BYTES || !Number.isInteger(entry.width) || !Number.isInteger(entry.height) || entry.width < 1 || entry.height < 1 || entry.width > 2048 || entry.height > 2048 || entry.width * entry.height > 4194304) throw failure('MOON_SOURCE_MANIFEST', 'Kích thước ảnh nguồn Moon vượt giới hạn.');
    found.set(image, {phaseImage: image, file: image, sourceURL: entry.sourceURL, sha256: entry.sha256, bytes: entry.bytes, width: entry.width, height: entry.height});
  }
  return found;
}

function untilAbort(promise, signal, disposeLate) {
  if (signal.aborted) { Promise.resolve(promise).then(value => disposeLate?.(value), () => {}); return Promise.reject(cancelled()); }
  return new Promise((resolve, reject) => {
    let settled = false;
    const cleanup = () => signal.removeEventListener('abort', onAbort);
    function onAbort() { if (settled) return; settled = true; cleanup(); reject(cancelled()); }
    signal.addEventListener('abort', onAbort, {once: true});
    Promise.resolve(promise).then(value => { if (settled) { disposeLate?.(value); return; } settled = true; cleanup(); resolve(value); }, error => { if (settled) return; settled = true; cleanup(); reject(error); });
  });
}

function discardResponse(response) { try { Promise.resolve(response?.body?.cancel?.()).catch(() => {}); } catch {} }
async function boundedBytes(url, max, signal, fetchImpl, exactBytes) {
  const response = await untilAbort(fetchImpl(url, {method: 'GET', credentials: 'omit', mode: 'same-origin', redirect: 'error', cache: 'force-cache', signal}), signal, discardResponse);
  if (!response?.ok) { discardResponse(response); throw failure('MOON_SOURCE_HTTP', `Không tải được ảnh nguồn Moon (HTTP ${Number(response?.status) || 'không xác định'}). Không thay bằng ảnh dựng.`); }
  const declared = response.headers?.get('content-length');
  if (declared && (!/^\d+$/.test(declared) || Number(declared) > max || (exactBytes !== undefined && Number(declared) !== exactBytes))) { discardResponse(response); throw failure('MOON_SOURCE_ASSET', 'Dung lượng ảnh Moon không khớp bản nguồn.'); }
  if (!response.body?.getReader) { discardResponse(response); throw failure('MOON_SOURCE_ASSET', 'Trình duyệt chưa hỗ trợ đọc ảnh nguồn có giới hạn.'); }
  const reader = response.body.getReader(), chunks = []; let total = 0, done = false;
  try {
    while (true) {
      const next = await untilAbort(reader.read(), signal);
      if (next.done) { done = true; break; }
      if (!(next.value instanceof Uint8Array)) throw failure('MOON_SOURCE_ASSET', 'Luồng ảnh nguồn Moon không hợp lệ.');
      total += next.value.byteLength;
      if (total > max || (exactBytes !== undefined && total > exactBytes)) throw failure('MOON_SOURCE_ASSET', 'Ảnh nguồn Moon vượt giới hạn hoặc bị thay đổi.');
      chunks.push(next.value);
    }
    if (exactBytes !== undefined && total !== exactBytes) throw failure('MOON_SOURCE_ASSET', 'Ảnh nguồn Moon chưa tải đủ.');
    const bytes = new Uint8Array(total); let offset = 0;
    for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
    return bytes;
  } finally {
    if (!done) { try { Promise.resolve(reader.cancel()).catch(() => {}); } catch {} }
    try { reader.releaseLock(); } catch {}
  }
}

export async function verifyMoonSourcePNG(bytes, entry, cryptoImpl = globalThis.crypto) {
  if (!(bytes instanceof Uint8Array) || bytes.byteLength !== entry.bytes || bytes.byteLength < 33 || PNG_SIGNATURE.some((value, i) => bytes[i] !== value)) throw failure('MOON_SOURCE_ASSET', 'Ảnh Moon không phải PNG nguồn hợp lệ.');
  const header = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  if (header.getUint32(8) !== 13 || String.fromCharCode(...bytes.slice(12, 16)) !== 'IHDR' || header.getUint32(16) !== entry.width || header.getUint32(20) !== entry.height) throw failure('MOON_SOURCE_ASSET', 'Kích thước PNG Moon không khớp ảnh nguồn.');
  if (!cryptoImpl?.subtle?.digest) throw failure('MOON_SOURCE_ASSET', 'Trình duyệt chưa hỗ trợ kiểm tra SHA-256 cho ảnh nguồn.');
  const digest = new Uint8Array(await cryptoImpl.subtle.digest('SHA-256', bytes));
  const hash = Array.from(digest, value => value.toString(16).padStart(2, '0')).join('');
  if (hash !== entry.sha256) throw failure('MOON_SOURCE_ASSET', 'Ảnh Moon không khớp SHA-256 nguồn; không dùng ảnh thay thế.');
  return true;
}

async function decodeSource(bytes, signal) {
  const blob = new Blob([bytes], {type: 'image/png'});
  if (typeof globalThis.createImageBitmap === 'function') {
    const bitmap = await untilAbort(globalThis.createImageBitmap(blob, {imageOrientation: 'none', premultiplyAlpha: 'none', colorSpaceConversion: 'none'}), signal, value => value.close());
    return {image: bitmap, width: bitmap.width, height: bitmap.height, close: () => bitmap.close()};
  }
  if (typeof globalThis.Image !== 'function' || !globalThis.URL?.createObjectURL) throw failure('MOON_SOURCE_DECODE', 'Trình duyệt chưa hỗ trợ mở PNG nguồn.');
  return new Promise((resolve, reject) => {
    const image = new Image(), url = URL.createObjectURL(blob); let finished = false;
    const cleanup = () => { signal.removeEventListener('abort', onAbort); image.onload = image.onerror = null; URL.revokeObjectURL(url); };
    function onAbort() { if (finished) return; finished = true; cleanup(); image.src = ''; reject(cancelled()); }
    image.onload = () => { if (finished) return; finished = true; cleanup(); resolve({image, width: image.naturalWidth, height: image.naturalHeight, close: () => { image.src = ''; }}); };
    image.onerror = () => { if (finished) return; finished = true; cleanup(); reject(failure('MOON_SOURCE_DECODE', 'Không giải mã được PNG nguồn Moon.')); };
    signal.addEventListener('abort', onAbort, {once: true});
    if (signal.aborted) onAbort(); else image.src = url;
  });
}

function makeCanvas(width, height, factory) {
  const canvas = factory(width, height); canvas.width = width; canvas.height = height;
  try {
    const ctx = canvas.getContext('2d');
    if (!ctx) throw failure('MOON_SOURCE_CANVAS', 'Trình duyệt không tạo được canvas Moon.');
    return {canvas, ctx};
  } catch (error) { disposeCanvas(canvas); throw error; }
}
function disposeCanvas(canvas) { if (canvas) { try { canvas.width = 1; canvas.height = 1; } catch {} } }
function drawOriginal(ctx, resource, size, tilt) {
  const scale = size / Math.max(resource.width, resource.height), width = resource.width * scale, height = resource.height * scale;
  ctx.imageSmoothingEnabled = true; ctx.imageSmoothingQuality = 'high';
  ctx.save(); ctx.translate(size / 2, size / 2); ctx.rotate(tilt * Math.PI / 180);
  ctx.drawImage(resource.image, -width / 2, -height / 2, width, height); ctx.restore();
}

/** The target is changed only after every requested original image is verified,
 * decoded and composed. Runtime injection is for bounded unit/browser QA only. */
export async function renderSourceMoons(target, options = {}) {
  check(options.signal);
  const layout = sourceMoonLayout(options), runtime = options.runtime ?? {};
  const fetchImpl = runtime.fetchImpl ?? globalThis.fetch, cryptoImpl = runtime.cryptoImpl ?? globalThis.crypto;
  const canvasFactory = runtime.canvasFactory ?? (() => document.createElement('canvas'));
  const decodeImage = runtime.decodeImage ?? decodeSource, yieldTask = runtime.yieldTask ?? (() => new Promise(resolve => setTimeout(resolve, 0)));
  if (typeof fetchImpl !== 'function' || !target?.getContext) throw failure('MOON_SOURCE_INPUT', 'Trình duyệt chưa hỗ trợ tạo ảnh Moon nguồn.');
  const controller = new AbortController(), forwardAbort = () => controller.abort(); let timedOut = false, published = false;
  options.signal?.addEventListener('abort', forwardAbort, {once: true});
  if (options.signal?.aborted) controller.abort();
  const timer = setTimeout(() => { timedOut = true; controller.abort(); }, TIMEOUT_MS);
  const individualCanvases = []; let composed;
  try {
    const rawManifest = await boundedBytes(new URL('manifest.json', ASSET_ROOT).href, MAX_MANIFEST, controller.signal, fetchImpl);
    let json;
    try { json = JSON.parse(new TextDecoder('utf-8', {fatal: true}).decode(rawManifest)); } catch (error) { throw failure('MOON_SOURCE_MANIFEST', 'Manifest ảnh nguồn Moon không đọc được.', error); }
    const manifest = validateMoonSourceManifest(json); check(controller.signal);
    const working = makeCanvas(layout.width, layout.height, canvasFactory); composed = working.canvas;
    if (!layout.transparent) { working.ctx.fillStyle = layout.background; working.ctx.fillRect(0, 0, layout.width, layout.height); }
    // A single decoded original at a time bounds memory even for 60 dates.
    for (const image of new Set(layout.items.map(item => item.phaseImage))) {
      check(controller.signal);
      const entry = manifest.get(image), bytes = await boundedBytes(new URL(entry.file, ASSET_ROOT).href, MAX_BYTES, controller.signal, fetchImpl, entry.bytes);
      await untilAbort(verifyMoonSourcePNG(bytes, entry, cryptoImpl), controller.signal); check(controller.signal);
      let resource;
      try {
        resource = await untilAbort(decodeImage(bytes, controller.signal), controller.signal, value => value.close?.()); check(controller.signal);
        if (!resource?.image || resource.width !== entry.width || resource.height !== entry.height || typeof resource.close !== 'function') throw failure('MOON_SOURCE_DECODE', 'Kích thước ảnh Moon sau giải mã không đúng bản nguồn.');
        for (let i = 0; i < layout.items.length; i++) {
          const item = layout.items[i]; if (item.phaseImage !== image) continue; check(controller.signal);
          const tile = makeCanvas(layout.size, layout.size, canvasFactory); individualCanvases.push(tile.canvas);
          drawOriginal(tile.ctx, resource, layout.size, item.providerTilt); item.canvas = tile.canvas;
          const x = i % layout.columns * layout.size, y = Math.floor(i / layout.columns) * (layout.size + layout.labelH);
          working.ctx.imageSmoothingEnabled = true; working.ctx.imageSmoothingQuality = 'high';
          working.ctx.drawImage(tile.canvas, x + layout.size * .03, y + layout.size * .03, layout.size * .94, layout.size * .94);
          if (layout.labelH) {
            working.ctx.fillStyle = layout.labelColor; working.ctx.textAlign = 'center'; working.ctx.textBaseline = 'top';
            working.ctx.font = `${Math.max(12, Math.round(layout.size * .048))}px sans-serif`;
            working.ctx.fillText(item.display, x + layout.size / 2, y + layout.size + layout.size * .01);
            working.ctx.font = `${Math.max(10, Math.round(layout.size * .03))}px sans-serif`;
            working.ctx.fillText(`${item.illumination.toFixed(1)}% · ${item.phaseName}`, x + layout.size / 2, y + layout.size + Math.max(18, layout.size * .075));
          }
          await untilAbort(yieldTask(), controller.signal);
        }
      } finally { resource?.close?.(); }
    }
    check(controller.signal);
    // No asynchronous step is allowed after this final cancellation check.
    const targetContext = target.getContext('2d');
    if (!targetContext) throw failure('MOON_SOURCE_CANVAS', 'Không mở được canvas xuất Moon.');
    target.width = layout.width; target.height = layout.height; targetContext.drawImage(composed, 0, 0); published = true;
    return {kind: 'moon', items: layout.items, width: layout.width, height: layout.height, model: 'MoonPhases original PNG / date-only metadata',
      texture: 'MoonPhases.co.uk original source PNGs', sourceImages: 'original-provider-png',
      note: 'Original provider image pixels and colors. Only resizing and the provider clockwise whole-image tilt are applied; no reconstructed shading or recoloring.'};
  } catch (error) {
    if (timedOut) throw failure('MOON_SOURCE_TIMEOUT', 'Ảnh nguồn Moon chưa tải xong sau 30 giây. Thử lại; không thay bằng ảnh dựng.');
    if (controller.signal.aborted || options.signal?.aborted) throw cancelled();
    if (error?.code?.startsWith('MOON_SOURCE_')) throw error;
    throw failure('MOON_SOURCE_RENDER', 'Không tạo được ảnh Moon nguồn. Không thay bằng ảnh dựng hoặc ảnh ngày khác.', error);
  } finally {
    clearTimeout(timer); options.signal?.removeEventListener('abort', forwardAbort); controller.abort(); disposeCanvas(composed);
    if (!published) for (const canvas of individualCanvases) disposeCanvas(canvas);
  }
}
