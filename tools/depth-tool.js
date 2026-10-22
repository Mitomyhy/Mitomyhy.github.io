import {checkSize, grayscaleDepth, resizeDepth, substrateMask, encodeGray16PNG, bytesDataURL} from './depth-pixels.js';

export const DEPTH_MODEL = Object.freeze({
  id: 'onnx-community/depth-anything-v2-small', revision: '4472b7362082ad9968fee890ca0f1e5aca36b93d',
  library: '@huggingface/transformers@3.8.1', dtype: 'q8', device: 'wasm', license: 'Apache-2.0'
});

async function rendererFactory(canvas, settings) {
  await import('./assets/depth/depth-viewer.js');
  if (!globalThis.UoyDepthViewer) throw new Error('Không tải được bộ xem 3D.');
  return globalThis.UoyDepthViewer.create(canvas, settings);
}

async function readPixels(file, doc) {
  if (!(file instanceof Blob) || !/^image\/(png|jpeg|webp)$/.test(file.type) || file.size > 64 * 1024 * 1024) {
    throw new Error('Chọn ảnh PNG, JPG hoặc WebP, tối đa 64 MB.');
  }
  const bitmap = await createImageBitmap(file);
  try {
    checkSize(bitmap.width, bitmap.height);
    const scratch = doc.createElement('canvas'); scratch.width = bitmap.width; scratch.height = bitmap.height;
    const ctx = scratch.getContext('2d', {willReadFrequently: true});
    if (!ctx) throw new Error('Trình duyệt không hỗ trợ Canvas 2D.');
    ctx.drawImage(bitmap, 0, 0);
    const rgba = ctx.getImageData(0, 0, scratch.width, scratch.height).data;
    const width = scratch.width, height = scratch.height; scratch.width = scratch.height = 1;
    return {width, height, rgba};
  } finally { bitmap.close(); }
}

/** Browser-only renderer. No Photoshop, upload, or third-party model request
 * occurs until the caller explicitly invokes generate(). */
export function createDepthTool(canvas, options = {}) {
  if (!canvas || !canvas.ownerDocument) throw new Error('Cần Canvas để mở 3D tool.');
  const doc = canvas.ownerDocument, onStatus = options.onStatus || (() => {});
  const makeRenderer = options.rendererFactory || rendererFactory;
  const decode = options.readPixels || (file => readPixels(file, doc));
  let viewer, source, depth, depthSource = null, productType = 'acrylic', disposed = false, busy = false;
  let worker, pending, requestId = 0, version = 0, imageName = '', inference = null;
  let appearance = {rimBoost: 1.2, reliefBoost: 6, gloss: 0.65};
  let dimensions = {heightMm: 100, totalMm: 3, reliefMm: 0.6};
  let rendererQueue = Promise.resolve();
  const check = () => { if (disposed) throw new Error('3D tool đã đóng.'); };
  function status(message, kind = 'info') { if (!disposed) onStatus(message, kind); }
  function noBusy() { check(); if (busy) throw new Error('Đang tạo depth AI, vui lòng đợi hoặc chuyển tool để hủy.'); }
  async function redraw(recreate = false) {
    rendererQueue = rendererQueue.catch(() => {}).then(async () => {
      check();
      if (recreate && viewer) { viewer.dispose(); viewer = null; }
      if (!viewer) viewer = await makeRenderer(canvas, {product_type: productType, appearance, dimensions, maxGrid: 256});
      if (disposed) { viewer.dispose(); viewer = null; check(); }
      if (!source) return;
      const values = depth || new Float32Array(source.width * source.height).fill(0.5);
      const shape = substrateMask(source.width, source.height, productType);
      viewer.setData({...source, depth: values, shape_alpha: shape, product_type: productType});
    });
    return rendererQueue;
  }
  function ready() { check(); if (!source || !depth || !viewer) throw new Error('Nạp ảnh và tạo AI depth hoặc nạp depth map trước.'); }
  function stopWorker(message) {
    if (worker) worker.terminate(); worker = null;
    if (pending) { clearTimeout(pending.timer); pending.reject(new Error(message)); pending = null; }
  }
  function runAI(rgba, width, height) {
    if (!worker) {
      worker = options.workerFactory ? options.workerFactory() : new Worker(new URL('./depth-ai-worker.js', import.meta.url), {type: 'module'});
      worker.onmessage = event => {
        const value = event.data || {};
        if (!pending || value.id !== pending.id || disposed) return;
        if (value.type === 'progress') {
          const message = value.status === 'inference' ? 'Đang tạo depth AI trên máy này…' :
            'Đang tải AI một lần' + (value.progress === null || value.progress === undefined ? '…' : ' · ' + Math.round(value.progress) + '%');
          status(message); return;
        }
        const current = pending; pending = null; clearTimeout(current.timer);
        if (value.type === 'result') current.resolve(value);
        else current.reject(new Error(value.message || 'Không chạy được AI.'));
      };
      worker.onerror = event => stopWorker(event.message || 'Không tải được AI worker. Thử lại hoặc nạp depth map.');
    }
    return new Promise((resolve, reject) => {
      const id = ++requestId;
      pending = {id, resolve, reject, timer: setTimeout(() => stopWorker('AI chưa hoàn tất sau 10 phút. Thử lại hoặc nạp depth map.'), 600000)};
      worker.postMessage({id, rgba: rgba.buffer, width, height}, [rgba.buffer]);
    });
  }
  const api = {
    async loadImage(file) {
      noBusy(); const token = ++version, data = await decode(file); check();
      if (version !== token) return api.state();
      checkSize(data.width, data.height);
      if (!data.rgba || data.rgba.length !== data.width * data.height * 4) throw new Error('Ảnh nguồn không hợp lệ.');
      if (!data.rgba.some((value, index) => index % 4 === 3 && value >= 128)) throw new Error('Ảnh hoàn toàn trong suốt, chưa có hình để tạo 3D.');
      source = {width: data.width, height: data.height, rgba: new Uint8ClampedArray(data.rgba)};
      imageName = String(file.name || 'artwork'); depth = null; depthSource = null; inference = null;
      await redraw(); status('Đã nạp ảnh · bản xem phẳng. Bấm Generate AI hoặc nạp depth map.'); return api.state();
    },
    async loadDepth(file) {
      noBusy(); if (!source) throw new Error('Nạp ảnh thiết kế trước.');
      const token = version, data = await decode(file); check();
      if (token !== version) throw new Error('Ảnh thiết kế đã đổi, nạp lại depth map.');
      // No hidden crop, stretch, registration guess, or color-as-AI fallback.
      if (data.width !== source.width || data.height !== source.height) throw new Error('Depth map phải có đúng kích thước ảnh thiết kế (' + source.width + ' × ' + source.height + ').');
      depth = grayscaleDepth(data.rgba, data.width, data.height); depthSource = 'imported-8bit'; inference = null;
      await redraw(); status('Đã nạp depth map · trắng nổi / đen lõm. Trình duyệt đọc ảnh depth ở 8 bit.'); return api.state();
    },
    async generate() {
      noBusy(); if (!source) throw new Error('Nạp ảnh thiết kế trước.');
      busy = true; const token = version;
      status('Đang chuẩn bị AI Depth Anything V2 Small · ảnh chỉ xử lý trên máy này.');
      try {
        // Bound inference work independently from full-size artwork/export.
        const scale = Math.min(1, 1024 / Math.max(source.width, source.height));
        const width = Math.max(2, Math.round(source.width * scale)), height = Math.max(2, Math.round(source.height * scale));
        const scratch = doc.createElement('canvas'); scratch.width = source.width; scratch.height = source.height;
        const ctx = scratch.getContext('2d'); ctx.putImageData(new ImageData(source.rgba, source.width, source.height), 0, 0);
        const small = doc.createElement('canvas'); small.width = width; small.height = height;
        const target = small.getContext('2d'); target.fillStyle = '#ffffff'; target.fillRect(0, 0, width, height);
        target.drawImage(scratch, 0, 0, width, height);
        const rgba = target.getImageData(0, 0, width, height).data;
        scratch.width = scratch.height = small.width = small.height = 1;
        const result = await runAI(rgba, width, height); check();
        if (token !== version) throw new Error('Ảnh thiết kế đã đổi trong lúc tạo depth.');
        depth = resizeDepth(new Float32Array(result.depth), result.width, result.height, source.width, source.height);
        depthSource = 'ai-da2-small'; inference = {width, height};
        await redraw(); busy = false;
        status('Đã tạo 3D bằng DA2 Small trên trình duyệt · không phải bộ MoGe + DA2 của Panel.'); return api.state();
      } catch (error) { status(error.message, 'error'); throw error; }
      finally { busy = false; }
    },
    async setProductType(value) {
      noBusy(); if (!['acrylic', 'ceramic_circle', 'ceramic_heart'].includes(value)) throw new Error('Loại sản phẩm không hợp lệ.');
      if (value !== productType) { productType = value; await redraw(true); }
      if (value !== 'acrylic') status('Ceramic dùng phôi hình học xem trước, không thay template PSD chuẩn trong Panel.');
      return api.state();
    },
    async setAppearance(value) {
      noBusy(); const next = {...appearance, ...value};
      for (const key of ['rimBoost', 'reliefBoost']) if (!Number.isFinite(next[key]) || next[key] < 0 || next[key] > 12) throw new Error('Độ nổi cần từ 0 đến 12.');
      if (!Number.isFinite(next.gloss) || next.gloss < 0 || next.gloss > 1) throw new Error('Độ bóng cần từ 0 đến 1.');
      appearance = next; await rendererQueue; if (viewer) viewer.setAppearance(next); return api.state();
    },
    async setDimensions(value) {
      noBusy(); const next = {...dimensions, ...value, totalMm: 3};
      if (!Number.isFinite(next.heightMm) || next.heightMm < 10 || next.heightMm > 1000 || !Number.isFinite(next.reliefMm) || next.reliefMm < 0.4 || next.reliefMm > 0.8) throw new Error('Kích thước phôi cần 10–1000 mm; relief 0.4–0.8 mm.');
      dimensions = next; await rendererQueue; if (viewer) viewer.setDimensions(next); return api.state();
    },
    async invertDepth() { noBusy(); ready(); for (let i = 0; i < depth.length; i++) depth[i] = 1 - depth[i]; await redraw(); return api.state(); },
    preset(name) { check(); if (!viewer) throw new Error('Nạp ảnh thiết kế trước.'); viewer.preset(name); },
    render() { check(); if (viewer) viewer.render(); },
    exportPNG(size) {
      noBusy(); ready(); const settings = typeof size === 'number' ? {width: size, height: size} : (size || {width: source.width, height: source.height});
      const png = viewer.exportFullPNG({...settings, padding: 0.06});
      if (typeof png !== 'string' || !png.startsWith('data:image/png;base64,') || png.length < 32) throw new Error('GPU chưa xuất được PNG, không có file giả thay thế.');
      return png;
    },
    exportDepthPNG() { noBusy(); ready(); return bytesDataURL(encodeGray16PNG(depth, source.width, source.height)); },
    state() {
      return {hasImage: !!source, hasDepth: !!depth, busy, productType, imageName,
        width: source ? source.width : 0, height: source ? source.height : 0,
        depthSource, importedDepthBits: depthSource === 'imported-8bit' ? 8 : null,
        inference, appearance: {...appearance}, dimensions: {...dimensions}, model: {...DEPTH_MODEL},
        illustrationOnly: true, panelEquivalent: false};
    },
    dispose() {
      if (disposed) return; disposed = true; version++; stopWorker('Đã đóng 3D tool.');
      if (viewer) viewer.dispose(); viewer = null; source = depth = null;
    }
  };
  return api;
}
