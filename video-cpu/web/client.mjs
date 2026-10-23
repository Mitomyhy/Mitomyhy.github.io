export class LocalVideoError extends Error {
  constructor(code, message, status = 0) { super(message); this.name = 'LocalVideoError'; this.code = code; this.status = status; }
}

export function loopbackEndpoint(value = 'http://127.0.0.1:4176') {
  let url;
  try { url = new URL(value); } catch { throw new LocalVideoError('invalid_endpoint', 'Enter the local MITOM engine address.'); }
  if (url.protocol !== 'http:' || !['127.0.0.1', 'localhost'].includes(url.hostname) || url.username || url.password || url.search || url.hash || url.pathname !== '/') {
    throw new LocalVideoError('invalid_endpoint', 'The video engine must run on this computer.');
  }
  return url.origin;
}

function identifier(value) {
  if (typeof value !== 'string' || !/^[a-f0-9]{32}$/.test(value)) throw new LocalVideoError('invalid_id', 'Invalid local job or image identifier.');
  return value;
}

export class LocalVideoClient {
  #token;
  constructor({endpoint, token = '', fetchImpl = globalThis.fetch} = {}) {
    this.endpoint = loopbackEndpoint(endpoint);
    this.#token = '';
    this.setToken(token);
    this.fetch = fetchImpl.bind(globalThis);
  }
  setToken(token) {
    if (typeof token !== 'string' || token.length > 200 || /[\s\x00-\x1f\x7f]/.test(token)) throw new LocalVideoError('invalid_token', 'Paste the connection code from MITOM Video.');
    this.#token = token;
  }
  async request(path, {method = 'GET', body, contentType, signal, timeout = 15000, video = false, discovery = false} = {}) {
    if (!discovery && !this.#token) throw new LocalVideoError('pairing_required', 'Connect the local video engine first.');
    const abort = new AbortController();
    const externalAbort = () => abort.abort(signal.reason);
    signal?.addEventListener('abort', externalAbort, {once:true});
    if (signal?.aborted) externalAbort();
    const timer = setTimeout(() => abort.abort(new DOMException('The local engine did not respond.', 'TimeoutError')), timeout);
    try {
      const headers = {};
      if (this.#token) headers.Authorization = `Bearer ${this.#token}`;
      if (contentType) headers['Content-Type'] = contentType;
      const response = await this.fetch(this.endpoint + path, {method, headers, body, signal:abort.signal, credentials:'omit', cache:'no-store', redirect:'error', referrerPolicy:'no-referrer'});
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new LocalVideoError(data.error?.code || 'engine_error', data.error?.message || 'The local video engine returned an error.', response.status);
      }
      if (!video) return await response.json();
      if ((response.headers.get('Content-Type') || '').split(';')[0] !== 'video/mp4') throw new LocalVideoError('invalid_video', 'The engine did not return an MP4 video.');
      const max = 128 * 1024 * 1024;
      if (Number(response.headers.get('Content-Length')) > max) throw new LocalVideoError('video_too_large', 'The video exceeds the download limit.');
      const reader = response.body.getReader();
      const chunks = [];
      let length = 0;
      try {
        while (true) {
          const {done, value} = await reader.read();
          if (done) break;
          length += value.byteLength;
          if (length > max) { await reader.cancel(); throw new LocalVideoError('video_too_large', 'The video exceeds the download limit.'); }
          chunks.push(value);
        }
      } finally { reader.releaseLock(); }
      if (length < 64) throw new LocalVideoError('invalid_video', 'The generated video is empty.');
      return new Blob(chunks, {type:'video/mp4'});
    } catch (error) {
      if (error instanceof LocalVideoError || error?.name === 'AbortError' || error?.name === 'TimeoutError') throw error;
      throw new LocalVideoError('engine_unreachable', 'Start MITOM Video on this computer, then reconnect.');
    } finally {
      clearTimeout(timer);
      signal?.removeEventListener('abort', externalAbort);
    }
  }
  discover({signal} = {}) { return this.request('/v1/health', {signal, timeout:4000, discovery:true}); }
  health({signal} = {}) { return this.request('/v1/health', {signal, timeout:4000}); }
  upload(file, {signal} = {}) {
    if (!(file instanceof Blob) || !['image/png','image/jpeg','image/webp'].includes(file.type) || file.size < 1 || file.size > 15 * 1024 * 1024) throw new LocalVideoError('invalid_image', 'Choose one PNG, JPG or WebP photo up to 15 MiB.');
    return this.request('/v1/uploads', {method:'POST', body:file, contentType:file.type, signal, timeout:30000});
  }
  createJob({image_id, prompt, options = {}}, {signal} = {}) {
    identifier(image_id);
    if (typeof prompt !== 'string' || !prompt.trim() || prompt.length > 4000 || prompt.includes('\0')) throw new LocalVideoError('invalid_prompt', 'Enter a prompt of 1–4000 characters.');
    return this.request('/v1/jobs', {method:'POST', contentType:'application/json', body:JSON.stringify({image_id, prompt:prompt.trim(), options}), signal});
  }
  job(id, {signal} = {}) { return this.request(`/v1/jobs/${identifier(id)}`, {signal}); }
  cancel(id, {signal} = {}) { return this.request(`/v1/jobs/${identifier(id)}/cancel`, {method:'POST', contentType:'application/json', body:'{}', signal}); }
  video(id, {signal} = {}) { return this.request(`/v1/jobs/${identifier(id)}/video`, {signal, timeout:60000, video:true}); }
}
