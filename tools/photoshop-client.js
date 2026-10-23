/* MITOM web <-> Photoshop: bounded PNG / native PSD. Pairing stays in page RAM.
 * Call pair/send only from an explicit user action; never background retry a
 * mutating send. A lost response may mean Photoshop already received the image.
 */
(function (root, factory) {
  var api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.MITOMPhotoshop = api;
}(typeof window !== 'undefined' ? window : this, function () {
  'use strict';
  var ENDPOINT = 'http://127.0.0.1:8791', MAX_BYTES = 60 * 1024 * 1024, MAX_PSD_BYTES = 256 * 1024 * 1024;
  function failure(code, message) { var e = new Error(message); e.code = code; return e; }
  function create(options) {
    options = options || {};
    var token = '', connected = false, busy = false, pairing = 0, capabilities = [], clientID = '', request = options.fetch || (typeof fetch === 'function' ? fetch.bind(globalThis) : null);
    var random = options.crypto || (typeof crypto !== 'undefined' ? crypto : null);
    function notice(message, state) { if (typeof options.onStatus === 'function') options.onStatus(message, state); }
    function offline() { return failure('LOCAL_UNAVAILABLE', 'Chưa kết nối Photoshop. Mở panel MITOM, bấm MITOM Web rồi sao chép mã kết nối. Nếu trình duyệt hỏi quyền truy cập mạng cục bộ, bạn tự cho phép để gửi ảnh.'); }
    async function binary(response, expected) {
      var length = response.headers && response.headers.get('content-length');
      if (length !== null && length !== undefined && Number(length) !== expected) throw failure('TEMPLATE_SIZE', 'PSD từ Photoshop không đúng dung lượng đã xác nhận.');
      if (!response.body || !response.body.getReader) {
        var raw = new Uint8Array(await response.arrayBuffer());
        if (raw.length !== expected) throw failure('TEMPLATE_SIZE', 'PSD từ Photoshop tải chưa đầy đủ.');
        return raw;
      }
      var reader = response.body.getReader(), out = new Uint8Array(expected), offset = 0;
      try {
        while (true) {
          var part = await reader.read(); if (part.done) break;
          if (part.value.length > expected - offset) throw failure('TEMPLATE_SIZE', 'PSD từ Photoshop vượt dung lượng đã xác nhận.');
          out.set(part.value, offset); offset += part.value.length;
        }
        if (offset !== expected) throw failure('TEMPLATE_SIZE', 'PSD từ Photoshop tải chưa đầy đủ.');
        return out;
      } finally { try { await reader.cancel(); } catch (_) {} reader.releaseLock(); }
    }
    async function call(route, init, timeout, expectedBytes) {
      if (!request) throw offline();
      var control = typeof AbortController === 'function' ? new AbortController() : null;
      var timer = control ? setTimeout(function () { control.abort(); }, timeout) : null;
      try {
        init = Object.assign({ mode: 'cors', credentials: 'omit', cache: 'no-store', referrerPolicy: 'no-referrer', redirect: 'error' }, init || {});
        if (control) init.signal = control.signal;
        var response = await request(ENDPOINT + route, init), result;
        if (response.ok && expectedBytes !== undefined) return await binary(response, expectedBytes);
        try { result = await response.json(); } catch (_) { result = {}; }
        if (!response.ok) { var error = failure(result.error || 'LOCAL_ERROR', result.message || 'Photoshop chưa nhận ảnh.'); error.status = response.status; throw error; }
        return result;
      } catch (e) {
        if (e && e.code) throw e;
        connected = false;
        if (route === '/v1/import' || route === '/v1/import-psd') throw failure('IMPORT_UNCERTAIN', 'Mất xác nhận từ Photoshop. Kiểm tra Photoshop trước khi bấm Add lại để tránh thêm kết quả hai lần. Kết quả trên web vẫn giữ nguyên.');
        if (route.indexOf('/v1/handoff/') === 0) throw failure('HANDOFF_UNCERTAIN', 'Chưa xác nhận được kết nối một lần. Bấm Add template trên panel để mở mã mới; file gốc vẫn giữ nguyên.');
        throw offline();
      } finally { if (timer) clearTimeout(timer); }
    }
    function clientNonce() { if (!clientID) clientID = requestID(); return clientID; }
    function templateHeaders() {
      // One page instance owns a claimed template through read and ack. Two
      // paired tabs must never generate the same queued PSD independently.
      return { Authorization: 'Bearer ' + token, 'X-Mitom-Client-Id': clientNonce() };
    }
    function paired() { if (!token || !connected) throw failure('NOT_PAIRED', 'Kết nối Photoshop bằng mã MITOM Web trước.'); }
    function supports(name) { return capabilities.indexOf(name) >= 0; }
    function requestID() {
      if (!random || typeof random.getRandomValues !== 'function') throw failure('RANDOM_UNAVAILABLE', 'Trình duyệt không tạo được mã chuyển an toàn.');
      var bytes = new Uint8Array(16); random.getRandomValues(bytes);
      return Array.prototype.map.call(bytes, function (b) { return ('0' + b.toString(16)).slice(-2); }).join('');
    }
    function psdHeader(bytes) {
      if (bytes.length < 26 || String.fromCharCode.apply(null, bytes.subarray(0, 4)) !== '8BPS') throw failure('PSD_REQUIRED', 'Chọn file Photoshop PSD hợp lệ, tối đa 256 MB.');
      var view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength), width = view.getUint32(18), height = view.getUint32(14);
      if (view.getUint16(4) !== 1 || bytes.subarray(6, 12).some(function (v) { return v !== 0; }) || view.getUint16(22) !== 8 || view.getUint16(24) !== 3 || [3, 4].indexOf(view.getUint16(12)) < 0 || width < 1 || height < 1 || Math.max(width, height) > 16384 || width * height > 32 * 1024 * 1024)
        throw failure('PSD_REQUIRED', 'Cần PSD RGB 8 bit, tối đa 32 megapixel / 16384 px.');
    }
    function descriptor(value) {
      // Older panel snapshots serialized Photoshop's numeric document ID as a
      // decimal string. Normalize that one exact form; never accept paths,
      // scientific notation or unsafe / negative IDs from queue metadata.
      var sourceID = value && value.sourceDocumentId;
      if (typeof sourceID === 'string' && /^(?:0|[1-9][0-9]{0,15})$/.test(sourceID)) sourceID = Number(sourceID);
      if (!value || !/^[a-f0-9]{32}$/.test(value.id) || !Number.isSafeInteger(value.bytes) || value.bytes < 26 || value.bytes > MAX_PSD_BYTES || !/^[a-f0-9]{64}$/.test(value.sha256) || !Number.isSafeInteger(sourceID) || sourceID < 1 || value.autoGenerate !== true || typeof value.name !== 'string' || value.name.length > 200 || /[\x00-\x1f\x7f/\\]/.test(value.name) || !/\.psd$/i.test(value.name))
        throw failure('TEMPLATE_METADATA', 'Thông tin template từ Photoshop không hợp lệ.');
      return { id: value.id, name: value.name, bytes: value.bytes, sha256: value.sha256, sourceDocumentId: sourceID, autoGenerate: true };
    }
    async function finishPair(value, epoch) {
      var result = await call('/v1/health', { headers: { Authorization: 'Bearer ' + value } }, 5000);
      if (epoch !== pairing) throw failure('PAIR_CANCELLED', 'Lượt kết nối đã được hủy; chưa ghép nối lại.');
      if (result.service !== 'mitom-web-image' || result.version !== 1) throw failure('VERSION', 'Cần cập nhật panel để nhận kết quả từ web.');
      capabilities = Array.isArray(result.capabilities) ? result.capabilities.filter(function (v) { return typeof v === 'string'; }) : [];
      token = value; connected = true; notice('Đã kết nối Photoshop trên máy này.', 'connected');
      return { ok: true };
    }
    return {
      pair: async function (value) {
        if (busy) throw failure('BUSY', 'Đang gửi ảnh vào Photoshop.');
        var epoch = ++pairing;
        token = ''; connected = false; capabilities = [];
        value = String(value || '').trim();
        if (!/^[a-f0-9]{64}$/.test(value)) throw failure('TOKEN_INVALID', 'Dán đúng mã 64 ký tự từ MITOM Web trong Photoshop.');
        return finishPair(value, epoch);
      },
      acceptHandoff: async function (code) {
        if (busy) throw failure('BUSY', 'Đang gửi kết quả vào Photoshop.');
        code = String(code || '').trim();
        if (!/^[a-f0-9]{64}$/.test(code)) throw failure('HANDOFF_INVALID', 'Mã nhận template không hợp lệ. Bấm Add template trên panel để mở lại.');
        var epoch = ++pairing; token = ''; connected = false; capabilities = [];
        var result = await call('/v1/handoff/' + code, { method: 'GET', headers: { 'X-Mitom-Client-Id': clientNonce() } }, 5000);
        if (epoch !== pairing) throw failure('PAIR_CANCELLED', 'Lượt kết nối đã được hủy; chưa ghép nối lại.');
        if (result.ok !== true || !/^[a-f0-9]{64}$/.test(result.token)) throw failure('HANDOFF_INVALID', 'Không nhận được mã ghép nối hợp lệ.');
        return finishPair(result.token, epoch);
      },
      nextTemplate: async function () {
        paired(); if (!supports('psd-template-export-v1')) return null;
        var epoch = pairing;
        try {
          var result = await call('/v1/templates/next', { headers: templateHeaders() }, 5000);
          if (epoch !== pairing) throw failure('PAIR_CANCELLED', 'Lượt nhận template đã được hủy.');
          if (result.ok !== true) throw failure('TEMPLATE_METADATA', 'Không đọc được hàng đợi template.');
          return result.template === null ? null : descriptor(result.template);
        } catch (error) {
          if (error.status === 404 || error.status === 405 || error.status === 501) { capabilities = capabilities.filter(function (v) { return v !== 'psd-template-export-v1'; }); return null; }
          throw error;
        }
      },
      readTemplate: async function (value) {
        paired(); if (!supports('psd-template-export-v1')) throw failure('TEMPLATE_UNSUPPORTED', 'Cập nhật panel để nhận template Photoshop.');
        value = descriptor(value); var epoch = pairing;
        var bytes = await call('/v1/templates/' + value.id, { headers: templateHeaders() }, 120000, value.bytes);
        if (epoch !== pairing) throw failure('PAIR_CANCELLED', 'Lượt nhận template đã được hủy.');
        if (!random || !random.subtle || typeof random.subtle.digest !== 'function') throw failure('HASH_UNAVAILABLE', 'Trình duyệt chưa hỗ trợ xác minh SHA-256.');
        var digest = new Uint8Array(await random.subtle.digest('SHA-256', bytes));
        var hash = Array.prototype.map.call(digest, function (b) { return ('0' + b.toString(16)).slice(-2); }).join('');
        if (hash !== value.sha256) throw failure('TEMPLATE_INTEGRITY', 'PSD từ Photoshop không khớp SHA-256; chưa nhận template này.');
        if (epoch !== pairing) throw failure('PAIR_CANCELLED', 'Lượt nhận template đã được hủy.');
        psdHeader(bytes);
        var blob = typeof File === 'function' ? new File([bytes], value.name, { type: 'image/vnd.adobe.photoshop' }) : new Blob([bytes], { type: 'image/vnd.adobe.photoshop' });
        if (!blob.name) Object.defineProperty(blob, 'name', { value: value.name });
        return blob;
      },
      ackTemplate: async function (id) {
        paired(); if (!/^[a-f0-9]{32}$/.test(id)) throw failure('TEMPLATE_METADATA', 'Mã template không hợp lệ.');
        var result = await call('/v1/templates/' + id + '/ack', { method: 'POST', headers: templateHeaders() }, 5000);
        if (result.ok !== true) throw failure('TEMPLATE_ACK', 'Chưa xác nhận được template đã nhận.');
        return result;
      },
      sendPSD: async function (blob, label) {
        paired(); if (!supports('psd-import-v1')) throw failure('PSD_UNSUPPORTED', 'Cập nhật panel để thêm PSD từ web vào Photoshop.');
        if (busy) throw failure('BUSY', 'Đang gửi kết quả vào Photoshop.');
        if (!(blob instanceof Blob) || !Number.isSafeInteger(blob.size) || blob.size < 26 || blob.size > MAX_PSD_BYTES) throw failure('PSD_REQUIRED', 'Kết quả phải là PSD RGB 8 bit nhỏ hơn 256 MB.');
        busy = true; var epoch = pairing;
        try {
          psdHeader(new Uint8Array(await blob.slice(0, 26).arrayBuffer()));
          if (epoch !== pairing || !connected) throw failure('PAIR_CANCELLED', 'Lượt gửi PSD đã được hủy trước khi chuyển.');
          var id = requestID(); label = String(label || 'MITOM 3D').replace(/[\x00-\x1f\x7f]/g, ' ').slice(0, 80);
          notice('Đang mở PSD kết quả trong Photoshop…', 'sending');
          var result = await call('/v1/import-psd', { method: 'POST', headers: { Authorization: 'Bearer ' + token, 'Content-Type': 'application/octet-stream', 'X-Mitom-Request-Id': id, 'X-Mitom-Tool': encodeURIComponent(label) }, body: blob }, 120000);
          if (result.ok !== true || result.requestId !== id || result.mode !== 'document') throw failure('INVALID_RECEIPT', 'Chưa có xác nhận PSD hợp lệ. Kiểm tra Photoshop trước khi gửi lại.');
          notice('Đã mở PSD IN / DEPTH / CUT trong tài liệu Photoshop mới.', 'connected');
          return result;
        } catch (error) { notice(error.message, 'error'); throw error; }
        finally { busy = false; }
      },
      send: async function (blob, label) {
        if (!token || !connected) throw failure('NOT_PAIRED', 'Kết nối Photoshop bằng mã MITOM Web trước khi gửi ảnh.');
        if (busy) throw failure('BUSY', 'Đang gửi ảnh vào Photoshop.');
        if (!blob || blob.type !== 'image/png' || !Number.isSafeInteger(blob.size) || blob.size < 1 || blob.size > MAX_BYTES) throw failure('PNG_REQUIRED', 'Kết quả phải là PNG nhỏ hơn 60 MB.');
        if (!random || typeof random.getRandomValues !== 'function') throw failure('RANDOM_UNAVAILABLE', 'Trình duyệt không tạo được mã chuyển ảnh an toàn.');
        var bytes = new Uint8Array(16); random.getRandomValues(bytes);
        var id = Array.prototype.map.call(bytes, function (b) { return ('0' + b.toString(16)).slice(-2); }).join('');
        label = String(label || 'MITOM Tools').replace(/[\x00-\x1f\x7f]/g, ' ').slice(0, 80);
        busy = true; notice('Đang thêm kết quả vào Photoshop…', 'sending');
        try {
          var result = await call('/v1/import', { method: 'POST', headers: { Authorization: 'Bearer ' + token, 'Content-Type': 'image/png', 'X-Mitom-Request-Id': id, 'X-Mitom-Tool': encodeURIComponent(label) }, body: blob }, 120000);
          if (result.ok !== true || result.requestId !== id || ['layer', 'document'].indexOf(result.mode) < 0) throw failure('INVALID_RECEIPT', 'Chưa có xác nhận hợp lệ từ Photoshop. Kiểm tra Photoshop trước khi gửi lại.');
          notice(result.mode === 'layer' ? 'Đã thêm layer vào Photoshop.' : 'Đã mở kết quả trong tài liệu Photoshop mới.', 'connected');
          return result;
        } catch (e) { notice(e.message, 'error'); throw e; }
        finally { busy = false; }
      },
      forget: function () { pairing++; token = ''; connected = false; capabilities = []; notice('Đã ngắt kết nối web; Photoshop và file gốc giữ nguyên.', 'offline'); },
      status: function () { return { connected: connected, busy: busy, capabilities: capabilities.slice(), supportsPSD: supports('psd-import-v1'), supportsTemplates: supports('psd-template-export-v1') }; }
    };
  }
  return { create: create, endpoint: ENDPOINT, version: 1 };
}));
