export const DEFAULT_ENDPOINT = 'http://127.0.0.1:4177';
export const MAX_VIDEO_BYTES = 100 * 1024 * 1024;
export const MAX_IMAGE_BYTES = 15 * 1024 * 1024;

export class LocalVideoError extends Error {
  constructor(code, message, status = 0) { super(message); this.name = 'LocalVideoError'; this.code = code; this.status = status; }
}

export function loopbackEndpoint(value = DEFAULT_ENDPOINT) {
  let url;
  try { url = new URL(value); } catch { throw new LocalVideoError('invalid_endpoint', 'Enter the local MITOM Product Video address.'); }
  if (url.protocol !== 'http:' || !['127.0.0.1', 'localhost'].includes(url.hostname) || url.username || url.password || url.search || url.hash || url.pathname !== '/') {
    throw new LocalVideoError('invalid_endpoint', 'The product video engine must run on this computer.');
  }
  return url.origin;
}

function identifier(value) {
  if (typeof value !== 'string' || !/^[a-f0-9]{32}$/.test(value)) throw new LocalVideoError('invalid_id', 'Invalid local image or video identifier.');
  return value;
}

export function productOptions(options = {}) {
  const value = {aspect_ratio:options.aspect_ratio ?? '16:9', duration_seconds:options.duration_seconds ?? 10, resolution:options.resolution ?? 1080, device:options.device ?? 'cpu'};
  if (!['16:9','9:16'].includes(value.aspect_ratio) || ![5,8,10,15].includes(value.duration_seconds) || ![720,1080].includes(value.resolution) || !['cpu','gpu'].includes(value.device)) {
    throw new LocalVideoError('invalid_options', 'Choose one of the available product video settings.');
  }
  return value;
}

// Check the encoded image header before asking the browser to allocate decoded
// pixels. This keeps a small compressed image from allocating an unbounded bitmap.
export function imageDimensions(buffer, type) {
  const view = new DataView(buffer), bytes = new Uint8Array(buffer);
  const text = (at, count) => String.fromCharCode(...bytes.slice(at,at+count));
  let width = 0, height = 0;
  if (type === 'image/png' && bytes.length >= 24 && bytes[0] === 137 && text(1,3) === 'PNG' && text(12,4) === 'IHDR') {
    width = view.getUint32(16); height = view.getUint32(20);
  } else if (type === 'image/jpeg' && bytes[0] === 255 && bytes[1] === 216) {
    let at = 2;
    while (at + 3 < bytes.length) {
      if (bytes[at++] !== 255) continue;
      while (bytes[at] === 255) at++;
      const marker = bytes[at++];
      if (marker === 218 || marker === 217) break;
      if (marker === 1 || marker >= 208 && marker <= 215) continue;
      if (at + 2 > bytes.length) break;
      const length = view.getUint16(at);
      if (length < 2 || at + length > bytes.length) break;
      if (marker >= 192 && marker <= 207 && ![196,200,204].includes(marker) && length >= 7) { height = view.getUint16(at+3); width = view.getUint16(at+5); break; }
      at += length;
    }
  } else if (type === 'image/webp' && bytes.length >= 25 && text(0,4) === 'RIFF' && text(8,4) === 'WEBP') {
    for (let at = 12; at + 8 <= bytes.length;) {
      const chunk = text(at,4), length = view.getUint32(at+4,true), data = at+8;
      if (data + length > bytes.length) break;
      if (chunk === 'VP8X' && length >= 10) { width = 1+bytes[data+4]+(bytes[data+5]<<8)+(bytes[data+6]<<16); height = 1+bytes[data+7]+(bytes[data+8]<<8)+(bytes[data+9]<<16); break; }
      if (chunk === 'VP8 ' && length >= 10 && bytes[data+3] === 157 && bytes[data+4] === 1 && bytes[data+5] === 42) { width = view.getUint16(data+6,true)&16383; height = view.getUint16(data+8,true)&16383; break; }
      if (chunk === 'VP8L' && length >= 5 && bytes[data] === 47) { const packed = view.getUint32(data+1,true); width = 1+(packed&16383); height = 1+((packed>>>14)&16383); break; }
      at = data+length+(length%2);
    }
  }
  if (width < 32 || height < 32 || width > 12000 || height > 12000 || width*height > 24000000) throw new LocalVideoError('invalid_image','Choose an image up to 24 megapixels and 12,000 px per side.');
  return {width,height};
}

export function capabilities(health) {
  const source = health?.product_studio;
  if (!source || typeof source !== 'object') return {available:false,enabled:false,devices:[],aspect_ratios:[],durations:[],resolutions:[]};
  const supported = (values, allow, fallback) => Array.isArray(values) ? [...new Set(values.filter(value => allow.includes(value)))] : fallback;
  const devices = supported(source.devices, ['cpu','gpu'], ['cpu']);
  const ratios = supported(source.aspect_ratios, ['16:9','9:16'], ['16:9','9:16']);
  const durations = supported(source.durations, [5,8,10,15], [5,8,10,15]);
  const resolutions = supported(source.resolutions, [720,1080], [720,1080]);
  const canSetup = source.can_setup === true || health?.can_setup === true;
  const needsSetup = source.needs_setup === true || health?.status === 'needs_setup';
  return {available:true,enabled:source.generation_enabled === true || canSetup,can_setup:canSetup,needs_setup:needsSetup,devices,aspect_ratios:ratios,durations,resolutions,engine:source.engine || 'remotion',prompt_planner:source.prompt_planner || '',background_mode:source.background_mode || ''};
}

export class LocalVideoClient {
  #token = '';
  constructor({endpoint = DEFAULT_ENDPOINT, token = '', fetchImpl = globalThis.fetch} = {}) {
    this.endpoint = loopbackEndpoint(endpoint);
    this.setToken(token);
    this.fetch = fetchImpl.bind(globalThis);
  }
  setToken(token) {
    if (typeof token !== 'string' || token.length > 200 || /[\s\x00-\x1f\x7f]/.test(token)) throw new LocalVideoError('invalid_token', 'Paste the current connection code from MITOM Product Video.');
    this.#token = token;
  }
  async request(path, {method = 'GET', body, contentType, signal, timeout = 15000, video = false, discovery = false} = {}) {
    if (!discovery && !this.#token) throw new LocalVideoError('pairing_required', 'Connect MITOM Product Video first.');
    const controller = new AbortController();
    const abort = () => controller.abort(signal.reason);
    signal?.addEventListener('abort', abort, {once:true});
    if (signal?.aborted) abort();
    const timer = setTimeout(() => controller.abort(new DOMException('The local engine did not respond.', 'TimeoutError')), timeout);
    try {
      const headers = {};
      if (this.#token) headers.Authorization = `Bearer ${this.#token}`;
      if (contentType) headers['Content-Type'] = contentType;
      const response = await this.fetch(this.endpoint + path, {method,headers,body,signal:controller.signal,credentials:'omit',cache:'no-store',redirect:'error',referrerPolicy:'no-referrer'});
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new LocalVideoError(data.error?.code || 'engine_error', data.error?.message || 'The local engine returned an error.', response.status);
      }
      if (!video) return await response.json();
      if ((response.headers.get('Content-Type') || '').split(';')[0].toLowerCase() !== 'video/mp4') throw new LocalVideoError('invalid_video', 'The local engine did not return an MP4.');
      const declared = Number(response.headers.get('Content-Length'));
      if (Number.isFinite(declared) && declared > MAX_VIDEO_BYTES) throw new LocalVideoError('video_too_large', 'The video exceeds 100 MB.');
      const reader = response.body?.getReader();
      if (!reader) throw new LocalVideoError('invalid_video', 'The local engine returned an empty video.');
      const chunks = []; let length = 0;
      try {
        while (true) {
          const part = await reader.read();
          if (part.done) break;
          length += part.value.byteLength;
          if (length > MAX_VIDEO_BYTES) { await reader.cancel(); throw new LocalVideoError('video_too_large', 'The video exceeds 100 MB.'); }
          chunks.push(part.value);
        }
      } finally { reader.releaseLock(); }
      if (length < 64) throw new LocalVideoError('invalid_video', 'The local engine returned an incomplete video.');
      return new Blob(chunks, {type:'video/mp4'});
    } catch (error) {
      if (error instanceof LocalVideoError) throw error;
      if (controller.signal.aborted) throw controller.signal.reason || error;
      throw new LocalVideoError('engine_unreachable', 'Start MITOM Product Video on this computer, then reconnect.');
    } finally { clearTimeout(timer); signal?.removeEventListener('abort', abort); }
  }
  discover(options = {}) { return this.request('/v1/health', {...options,timeout:4000,discovery:true}); }
  health(options = {}) { return this.request('/v1/health', {...options,timeout:4000}); }
  upload(file, {signal} = {}) {
    if (!(file instanceof Blob) || !['image/png','image/jpeg','image/webp'].includes(file.type) || file.size < 1 || file.size > MAX_IMAGE_BYTES) throw new LocalVideoError('invalid_image', 'Choose one PNG, JPG or WebP image up to 15 MB.');
    return this.request('/v1/uploads', {method:'POST',body:file,contentType:file.type,signal,timeout:30000});
  }
  createProductJob({image_id, background_id, prompt, options = {}}, {signal} = {}) {
    identifier(image_id); if (background_id) identifier(background_id);
    if (typeof prompt !== 'string' || !prompt.trim() || prompt.length > 4000 || prompt.includes('\0')) throw new LocalVideoError('invalid_prompt', 'Enter a description of 1–4000 characters.');
    return this.request('/v1/product-jobs', {method:'POST',contentType:'application/json',body:JSON.stringify({image_id,...(background_id?{background_id}:{}),prompt:prompt.trim(),options:productOptions(options)}),signal});
  }
  job(id, {signal} = {}) { return this.request(`/v1/jobs/${identifier(id)}`, {signal}); }
  cancel(id, {signal} = {}) { return this.request(`/v1/jobs/${identifier(id)}/cancel`, {method:'POST',contentType:'application/json',body:'{}',signal}); }
  video(id, {signal} = {}) { return this.request(`/v1/jobs/${identifier(id)}/video`, {signal,timeout:60000,video:true}); }
}
