(function () {
  'use strict';
  var LOCAL = 'http://127.0.0.1:4177/';
  var WEB = 'https://mitomyhy.github.io/video-product/';
  var LAUNCH = 'mitom://video';
  var ready = false;
  var checking = false;
  var status = document.getElementById('status');
  function say(text) { status.textContent = text; }
  function open(url) {
    if ([LOCAL, WEB, LAUNCH].indexOf(url) < 0) return;
    if (window.__adobe_cep__ && typeof window.__adobe_cep__.openURLInDefaultBrowser === 'function') {
      window.__adobe_cep__.openURLInDefaultBrowser(url);
    } else { say('Mở MITOM Launcher, rồi chọn Product Video.'); }
  }
  function check(done) {
    if (checking) { if (done) done(ready); return; }
    checking = true;
    function finish(value) {
      if (!checking) return;
      checking = false; ready = value;
      say(ready ? 'Product Video đã sẵn sàng trên máy này.' : 'Bấm Mở Product Video để khởi động bằng Launcher.');
      if (done) done(ready);
    }
    try {
      var http = require('http');
      var request = http.get(LOCAL + 'v1/health', function (response) {
        var data = '';
        response.setEncoding('utf8');
        response.on('data', function (chunk) { data += chunk; if (data.length > 8192) { response.destroy(); finish(false); } });
        response.on('end', function () {
          try {
            var health = JSON.parse(data);
            var server = String(response.headers && response.headers.server || '');
            finish(response.statusCode === 200 && /^MITOMProductVideo\/1(?:\s|$)/.test(server) && health.version === 1 && health.pairing_required === true);
          } catch (error) { finish(false); }
        });
        response.on('error', function () { finish(false); });
      });
      request.setTimeout(1200, function () { request.destroy(); finish(false); });
      request.on('error', function () { finish(false); });
    } catch (error) { finish(false); }
  }
  document.getElementById('openVideo').addEventListener('click', function () {
    check(function (isReady) { open(isReady ? LOCAL : LAUNCH); if (!isReady) say('Đã gửi lệnh mở Product Video tới MITOM Launcher.'); });
  });
  document.getElementById('openWeb').addEventListener('click', function () { open(WEB); });
  document.getElementById('checkAgain').addEventListener('click', function () { check(); });
  check();
}());
