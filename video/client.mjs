export function validateEndpoint(value) {
  const url = new URL(String(value).trim());
  if (url.username || url.password || url.hash || !(
    url.protocol === 'https:' || (url.protocol === 'http:' && ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname))
  )) throw Error('Use an HTTPS webhook URL or localhost HTTP URL.');
  return url.href;
}

export function buildVideoPayload({prompt, aspectRatio = '16:9', imageUrl = '', imageBase64 = '', imageName = ''}) {
  prompt = String(prompt || '').trim();
  if (!prompt || prompt.length > 5000) throw Error('Enter a prompt of 1–5,000 characters.');
  if (!['16:9', '9:16'].includes(aspectRatio)) throw Error('Choose 16:9 or 9:16.');
  const payload = {prompt, aspect_ratio: aspectRatio};
  if (imageBase64) {
    if (!/^data:image\/(png|jpeg|webp);base64,[A-Za-z0-9+/=]+$/.test(imageBase64) || imageBase64.length > 7 * 1024 * 1024) throw Error('Choose a PNG, JPG or WebP image up to 5 MB.');
    payload.imageBase64 = imageBase64;
    payload.imageName = String(imageName || 'product.png').replace(/[^a-zA-Z0-9._-]/g, '_').slice(-100);
  } else if (String(imageUrl).trim()) {
    const url = new URL(String(imageUrl).trim());
    if (url.protocol !== 'https:' || url.username || url.password) throw Error('Use a public HTTPS image URL.');
    payload.imageUrl = url.href;
  }
  return payload;
}

function terminalError(message) {
  const error = Error(message);
  error.terminal = true;
  return error;
}

export function parseVideoResponse(raw) {
  if (Array.isArray(raw)) raw = raw[0];
  if (!raw || typeof raw !== 'object') throw Error('The workflow returned an empty response.');
  if (raw.code !== undefined && Number(raw.code) !== 200) throw Error(String(raw.msg || raw.message || 'Video provider rejected the request.'));
  if (raw.error) throw Error(typeof raw.error === 'string' ? raw.error : String(raw.error.message || 'Workflow failed.'));
  const data = raw.data || raw;
  const state = String(data.state || '').toLowerCase();
  const completed = state === 'success' || Number(data.successFlag) === 1;
  if (['fail', 'failed', 'error'].includes(state) || [2,3].includes(Number(data.successFlag))) throw terminalError(String(data.failMsg || data.errorMessage || raw.msg || 'Video generation failed.'));
  let result = data.resultJson || data.response || data;
  if (typeof result === 'string') { try { result = JSON.parse(result); } catch (_) { throw (completed ? terminalError : Error)('The workflow returned invalid video data.'); } }
  const urlVariants = [result?.resultUrls, result?.data?.result_urls, result?.data?.resultUrls, data.resultUrls, data.result_urls, data.videoUrl ? [data.videoUrl] : undefined];
  const urls = urlVariants.find(value => value !== undefined && value !== null) ?? [];
  const invalidResult = completed ? terminalError : Error;
  if (!Array.isArray(urls)) throw invalidResult('The workflow returned an invalid video URL list.');
  if (urls.length) {
    let url;
    try { if (typeof urls[0] !== 'string') throw Error(); url = new URL(urls[0]); }
    catch (_) { throw invalidResult('The workflow returned an invalid video URL.'); }
    if (url.protocol !== 'https:' || url.username || url.password) throw invalidResult('The workflow returned an unsafe video URL.');
    return {state:'success', videoUrl:url.href, taskId:String(data.taskId || '')};
  }
  if (completed) throw terminalError('The video completed without a download URL. Check the n8n execution.');
  if (!data.taskId) throw Error('No task ID was returned. Check the n8n workflow.');
  return {state:'pending', taskId:String(data.taskId)};
}

export async function requestVideo(endpoint, payload, token, {fetchFn = globalThis.fetch, signal} = {}) {
  const url = validateEndpoint(endpoint);
  const response = await fetchFn(url, {method:'POST', mode:'cors', credentials:'omit', cache:'no-store',
    headers:{'Content-Type':'application/json', ...(token ? {'X-MITOM-Token':token} : {})}, body:JSON.stringify(payload), signal});
  if (!response.ok) throw Error(`Workflow HTTP ${response.status}. Check its URL, authentication and CORS settings.`);
  return parseVideoResponse(await response.json());
}
