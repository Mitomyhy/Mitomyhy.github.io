/* MITOM web -> Photoshop: PNG bytes only. Pairing stays in this page's RAM.
 * Call pair/send only from an explicit user action; never background retry a
 * mutating send. A lost response may mean Photoshop already received the image.
 */
(function (root, factory) {
  var api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.MITOMPhotoshop = api;
}(typeof window !== 'undefined' ? window : this, function () {
  'use strict';
  var ENDPOINT = 'http://127.0.0.1:8791', MAX_BYTES = 60 * 1024 * 1024;
  function failure(code, message) { var e = new Error(message); e.code = code; return e; }
  function create(options) {
    options = options || {};
    var token = '', connected = false, busy = false, pairing = 0, request = options.fetch || (typeof fetch === 'function' ? fetch.bind(globalThis) : null);
    var random = options.crypto || (typeof crypto !== 'undefined' ? crypto : null);
    function notice(message, state) { if (typeof options.onStatus === 'function') options.onStatus(message, state); }
    function offline() { return failure('LOCAL_UNAVAILABLE', 'Chưa kết nối Photoshop. Mở panel MITOM, bấm MITOM Web rồi sao chép mã kết nối. Nếu trình duyệt hỏi quyền truy cập mạng cục bộ, bạn tự cho phép để gửi ảnh.'); }
    async function call(route, init, timeout) {
      if (!request) throw offline();
      var control = typeof AbortController === 'function' ? new AbortController() : null;
      var timer = control ? setTimeout(function () { control.abort(); }, timeout) : null;
      try {
        init = Object.assign({ mode: 'cors', credentials: 'omit', cache: 'no-store', referrerPolicy: 'no-referrer', redirect: 'error' }, init || {});
        if (control) init.signal = control.signal;
        var response = await request(ENDPOINT + route, init), result = await response.json();
        if (!response.ok) throw failure(result.error || 'LOCAL_ERROR', result.message || 'Photoshop chưa nhận ảnh.');
        return result;
      } catch (e) {
        if (e && e.code) throw e;
        connected = false;
        if (route === '/v1/import') throw failure('IMPORT_UNCERTAIN', 'Mất xác nhận từ Photoshop. Kiểm tra Photoshop trước khi bấm Add lại để tránh thêm ảnh hai lần. Ảnh trên web vẫn giữ nguyên.');
        throw offline();
      } finally { if (timer) clearTimeout(timer); }
    }
    return {
      pair: async function (value) {
        if (busy) throw failure('BUSY', 'Đang gửi ảnh vào Photoshop.');
        var epoch = ++pairing;
        token = ''; connected = false;
        value = String(value || '').trim();
        if (!/^[a-f0-9]{64}$/.test(value)) throw failure('TOKEN_INVALID', 'Dán đúng mã 64 ký tự từ MITOM Web trong Photoshop.');
        var result = await call('/v1/health', { headers: { Authorization: 'Bearer ' + value } }, 5000);
        if (epoch !== pairing) throw failure('PAIR_CANCELLED', 'Lượt kết nối đã được hủy; chưa ghép nối lại.');
        if (result.service !== 'mitom-web-image' || result.version !== 1) throw failure('VERSION', 'Cần cập nhật panel để nhận ảnh từ web.');
        token = value; connected = true; notice('Đã kết nối Photoshop trên máy này.', 'connected');
        return { ok: true };
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
      forget: function () { pairing++; token = ''; connected = false; notice('Đã ngắt kết nối web; Photoshop và file gốc giữ nguyên.', 'offline'); },
      status: function () { return { connected: connected, busy: busy }; }
    };
  }
  return { create: create, endpoint: ENDPOINT, version: 1 };
}));
