/* Only started by an explicit Generate click. Artwork is never sent to a server. */
import { normalizedDepth } from './depth-pixels.js';

const LIBRARY = 'https://cdn.jsdelivr.net/npm/@huggingface/transformers@3.8.1';
const MODEL = 'onnx-community/depth-anything-v2-small';
const REVISION = '4472b7362082ad9968fee890ca0f1e5aca36b93d';
let estimatorPromise, libraryPromise, running = false;

async function estimator(id) {
  libraryPromise ||= import(LIBRARY);
  const lib = await libraryPromise;
  // Single-thread WASM works without COOP/COEP on GitHub Pages. No implicit
  // WebGPU->CPU retry/download: a failed attempt is surfaced for user choice.
  lib.env.allowLocalModels = false;
  lib.env.backends.onnx.wasm.numThreads = 1;
  estimatorPromise ||= lib.pipeline('depth-estimation', MODEL, {
    revision: REVISION, dtype: 'q8', device: 'wasm',
    progress_callback: p => self.postMessage({id, type: 'progress', status: p.status, file: p.file,
      progress: Number.isFinite(p.progress) ? p.progress : null})
  }).catch(error => { estimatorPromise = undefined; throw error; });
  return { lib, pipe: await estimatorPromise };
}

self.onmessage = async event => {
  const {id, rgba, width, height} = event.data || {};
  if (running) { self.postMessage({id, type: 'error', message: 'Đang chạy AI, vui lòng đợi.'}); return; }
  if (!Number.isInteger(width) || !Number.isInteger(height) || width < 2 || height < 2 ||
      width > 1024 || height > 1024 || !(rgba instanceof ArrayBuffer) || rgba.byteLength !== width * height * 4) {
    self.postMessage({id, type: 'error', message: 'Dữ liệu ảnh AI không hợp lệ.'}); return;
  }
  running = true;
  try {
    const {lib, pipe} = await estimator(id);
    self.postMessage({id, type: 'progress', status: 'inference'});
    // A local RawImage from transferred pixels, not a remote URL or API call.
    const result = await pipe(new lib.RawImage(new Uint8ClampedArray(rgba), width, height, 4));
    const tensor = result.predicted_depth;
    const dims = tensor.dims.slice(-2), output = normalizedDepth(tensor.data, dims[1], dims[0]);
    self.postMessage({id, type: 'result', width: dims[1], height: dims[0], depth: output.buffer}, [output.buffer]);
  } catch (error) {
    self.postMessage({id, type: 'error', message: error && error.message || 'Không chạy được AI trên trình duyệt này.'});
  } finally { running = false; }
};
