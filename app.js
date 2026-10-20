(() => {
  'use strict';
  const config = window.MITOM_CONFIG || {};
  const apps = Array.isArray(config.apps) ? config.apps : [];
  const $ = (selector) => document.querySelector(selector);
  const dialog = $('#app-dialog');
  const dialogContent = $('#dialog-content');
  let currentFilter = 'all';
  let currentView = 'grid';
  let toastTimer;
  let lastFocus;
  const escapeHTML = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
  const safeUrl = (value, fallback = '#') => {
    if (!value) return fallback;
    try { const url = new URL(value, location.href); return ['https:', 'http:'].includes(url.protocol) ? url.href : fallback; } catch (_) { return fallback; }
  };
  const githubUrl = safeUrl(config.githubUrl, 'https://github.com/Mitomyhy');
  const repositoryUrl = safeUrl(config.repositoryUrl, githubUrl);
  const releaseUrl = safeUrl(config.releaseUrl, repositoryUrl);
  const downloadUrl = safeUrl(config.launcherDownloadUrl, '');

  function showToast(message) {
    $('#toast').textContent = message;
    $('#toast').hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { $('#toast').hidden = true; }, 3500);
  }

  function openDialog(content) {
    lastFocus = document.activeElement;
    dialogContent.innerHTML = content;
    if (!dialog.open) dialog.showModal();
    document.body.style.overflow = 'hidden';
  }

  function closeDialog() { dialog.close(); }
  dialog.addEventListener('close', () => {
    document.body.style.overflow = '';
    if (lastFocus?.isConnected) lastFocus.focus();
  });
  $('.dialog-close').addEventListener('click', closeDialog);
  dialog.addEventListener('click', (event) => {
    const rect = dialog.getBoundingClientRect();
    if (event.target === dialog && (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom)) closeDialog();
  });

  function showDownload() {
    if (downloadUrl) {
      openDialog(`<p class="dialog-kicker">MITOM / WINDOWS LAUNCHER</p><h2>Let’s get<br><span>started.</span></h2><p class="dialog-description">Tải launcher cho Windows, sau đó chọn Photoshop Panel để cài đặt. Cần có Adobe Photoshop trên máy trước khi dùng panel.</p><ol class="dialog-checklist"><li>Tải và cài MITOM Launcher.</li><li>Chọn Photoshop Panel → Cài đặt.</li><li>Mở Photoshop và sử dụng panel.</li></ol><div class="dialog-actions"><a class="button button-dark" href="${escapeHTML(downloadUrl)}">TẢI LAUNCHER <span>↓</span></a><a class="button" href="${escapeHTML(releaseUrl)}" target="_blank" rel="noopener">XEM RELEASE <span>↗</span></a></div><p class="dialog-note">Gói tải được cung cấp từ trang phát hành chính thức của MITOM.</p>`);
    } else {
      openDialog(`<p class="dialog-kicker">MITOM / DOWNLOAD CENTER</p><h2>Launcher đang<br><span>được chuẩn bị.</span></h2><p class="dialog-description">Liên kết tải launcher sẽ xuất hiện tại đây sau khi bản phát hành được đưa lên GitHub. Bạn có thể theo dõi mã nguồn và các bản cập nhật ngay bây giờ.</p><div class="dialog-actions"><a class="button button-dark" href="${escapeHTML(releaseUrl)}" target="_blank" rel="noopener">MỞ GITHUB <span>↗</span></a><button class="button" data-close-dialog>ĐÃ HIỂU</button></div><p class="dialog-note">Photoshop Panel là ứng dụng đầu tiên trong MITOM.</p>`);
    }
  }

  function showLaunch() {
    const protocol = /^[a-z][a-z0-9+.-]*:\/\//i.test(config.launcherProtocol || '') ? config.launcherProtocol : '';
    openDialog(`<p class="dialog-kicker">MITOM / OPEN LAUNCHER</p><h2>Your tools.<br><span>One click away.</span></h2><p class="dialog-description">Nếu đã cài launcher, chọn “Mở launcher” và cho phép trình duyệt mở ứng dụng. Chưa cài? Tải bản dành cho Windows để bắt đầu.</p><div class="dialog-actions">${protocol ? '<button class="button button-dark" data-open-protocol>MỞ LAUNCHER <span>↗</span></button>' : ''}<button class="button" data-action="download">TẢI LAUNCHER <span>↓</span></button></div><p id="launch-help" class="dialog-note">Trình duyệt sẽ yêu cầu xác nhận khi mở ứng dụng trên máy.</p>`);
  }

  function showDetails(id = 'photoshop-panel') {
    const app = apps.find((item) => item.id === id) || apps[0];
    if (!app) return;
    const appLink = safeUrl(app.githubUrl, repositoryUrl);
    openDialog(`<p class="dialog-kicker">MITOM / THE TOOLKIT / ${escapeHTML(app.id.toUpperCase())}</p><div class="dialog-app-heading"><img src="${escapeHTML(safeUrl(app.icon))}" alt=""><h2>${escapeHTML(app.name)}</h2></div><div class="dialog-tags"><span>${escapeHTML(app.platform)}</span><span>${escapeHTML(app.version ? `VERSION ${app.version}` : 'VIA MITOM LAUNCHER')}</span></div><p class="dialog-description">${escapeHTML(app.detail || app.summary)}</p><div class="dialog-actions"><button class="button button-dark" data-action="download">CÀI QUA LAUNCHER <span>↓</span></button><a class="button" href="${escapeHTML(appLink)}" target="_blank" rel="noopener">GITHUB <span>↗</span></a></div><p class="dialog-note">Yêu cầu Adobe Photoshop. Giao diện minh họa trên trang là ảnh chụp panel được cung cấp.</p>`);
  }

  function showUpcoming(section = 'MITOM') {
    const descriptions = {
      IMAGE: 'Không gian cho các công cụ hình ảnh tiếp theo. Hiện bạn có thể bắt đầu với Photoshop Panel.',
      VIDEO: 'Các công cụ video sẽ xuất hiện tại đây khi được phát hành.',
      PRICING: 'Thông tin sử dụng và giá sẽ được cập nhật cùng từng công cụ khi phát hành.',
      COMMUNITY: 'Kênh cộng đồng sẽ được bổ sung sau. Hiện bạn có thể theo dõi MITOM trên GitHub.'
    };
    openDialog(`<p class="dialog-kicker">MITOM / ${escapeHTML(section)} / COMING NEXT</p><h2>More tools.<br><span>More possibilities.</span></h2><p class="dialog-description">${escapeHTML(descriptions[section] || descriptions.IMAGE)}</p><div class="dialog-actions"><button class="button button-dark" data-action="details">KHÁM PHÁ PHOTOSHOP PANEL ↗</button><a class="button" href="${escapeHTML(githubUrl)}" target="_blank" rel="noopener">THEO DÕI GITHUB ↗</a></div>`);
  }

  function renderApps() {
    const query = $('#app-search').value.trim().toLocaleLowerCase('vi');
    const matches = apps.filter((app) => (currentFilter === 'all' || app.status === currentFilter) && `${app.name} ${app.category} ${app.summary}`.toLocaleLowerCase('vi').includes(query));
    const count = apps.length;
    $('#all-count').textContent = String(count).padStart(2, '0');
    $('#available-count').textContent = String(apps.filter((app) => app.status === 'available').length).padStart(2, '0');
    $('#app-lineup').classList.toggle('table-view', currentView === 'table');
    $('#app-lineup').hidden = matches.length === 0;
    $('#no-results').hidden = matches.length !== 0;
    const cards = matches.map((app) => {
      const index = String(apps.indexOf(app) + 1).padStart(2, '0');
      return `<article class="app-card"><div class="app-card-top"><span class="app-index">${index}</span><a href="${escapeHTML(safeUrl(app.githubUrl, repositoryUrl))}" target="_blank" rel="noopener">GITHUB ↗</a></div><div class="app-card-body"><div class="product-title"><img class="product-icon" src="${escapeHTML(safeUrl(app.icon))}" alt="" width="62" height="62"><div><h3>${escapeHTML(app.name)}</h3><p>${escapeHTML(app.category)}<br>${escapeHTML(app.version ? `Version ${app.version}` : 'Made for your workflow')}</p></div></div><span class="status-tag">${escapeHTML(app.statusLabel || app.status)}</span><p class="product-summary">${escapeHTML(app.summary)}</p><div class="app-card-actions"><button class="button button-dark" data-action="${app.status === 'available' ? 'download' : 'details'}" data-app-id="${escapeHTML(app.id)}">${app.status === 'available' ? 'CÀI QUA LAUNCHER ↓' : 'XEM CHI TIẾT ↗'}</button><button class="button details-button" data-action="details" data-app-id="${escapeHTML(app.id)}" aria-label="Chi tiết ${escapeHTML(app.name)}">···</button></div></div></article>`;
    }).join('');
    const note = count === 1 && !query ? `<aside class="suite-note"><p class="eyebrow">THIS IS JUST THE BEGINNING</p><h3>A growing toolkit.<br>Built with purpose.</h3><p>Một công cụ hôm nay. Thêm nhiều ý tưởng cho ngày mai. Những ứng dụng mới sẽ xuất hiện tại đây khi sẵn sàng.</p><a href="${escapeHTML(githubUrl)}" target="_blank" rel="noopener">FOLLOW THE JOURNEY ↗</a></aside>` : '';
    $('#app-lineup').innerHTML = cards + note;
  }

  document.addEventListener('click', (event) => {
    const action = event.target.closest('[data-action]');
    if (action) {
      if (action.dataset.action === 'download') showDownload();
      if (action.dataset.action === 'launch') showLaunch();
      if (action.dataset.action === 'details') showDetails(action.dataset.appId);
      if (action.dataset.action === 'upcoming') showUpcoming(action.dataset.section);
    }
    if (event.target.closest('[data-close-dialog]')) closeDialog();
    if (event.target.closest('[data-open-protocol]')) {
      // Only this explicit user gesture opens the installed application.
      if (/^[a-z][a-z0-9+.-]*:\/\//i.test(config.launcherProtocol || '')) {
        location.href = config.launcherProtocol;
        $('#launch-help').textContent = 'Nếu launcher chưa mở, hãy kiểm tra hộp xác nhận của trình duyệt hoặc cài bản mới nhất.';
      }
    }
    const filter = event.target.closest('[data-filter]');
    if (filter) {
      currentFilter = filter.dataset.filter;
      document.querySelectorAll('[data-filter]').forEach((button) => { const active = button === filter; button.classList.toggle('active', active); button.setAttribute('aria-pressed', String(active)); });
      renderApps();
    }
    const view = event.target.closest('[data-view]');
    if (view) {
      currentView = view.dataset.view;
      document.querySelectorAll('[data-view]').forEach((button) => { const active = button === view; button.classList.toggle('active', active); button.setAttribute('aria-pressed', String(active)); });
      renderApps();
    }
  });
  $('#app-search').addEventListener('input', renderApps);
  const updateThemeLabel = () => { const dark = document.body.classList.contains('dark'); $('#theme-toggle').setAttribute('aria-label', dark ? 'Chuyển giao diện sáng' : 'Chuyển giao diện tối'); $('#theme-toggle').setAttribute('aria-pressed', String(dark)); $('meta[name="theme-color"]').content = dark ? '#191a1c' : '#f2f1ed'; };
  try { if (localStorage.getItem('mitom-theme') === 'dark') document.body.classList.add('dark'); } catch (_) { /* Storage may be unavailable in private contexts. */ }
  updateThemeLabel();
  $('#theme-toggle').addEventListener('click', () => { document.body.classList.toggle('dark'); updateThemeLabel(); try { localStorage.setItem('mitom-theme', document.body.classList.contains('dark') ? 'dark' : 'light'); } catch (_) {} });
  $('.menu-toggle').addEventListener('click', () => { const opened = $('#mobile-nav').hidden; $('#mobile-nav').hidden = !opened; $('.menu-toggle').setAttribute('aria-expanded', String(opened)); $('.menu-toggle').setAttribute('aria-label', opened ? 'Đóng menu' : 'Mở menu'); });
  $('#mobile-nav').addEventListener('click', (event) => { if (event.target.closest('a,button')) { $('#mobile-nav').hidden = true; $('.menu-toggle').setAttribute('aria-expanded', 'false'); $('.menu-toggle').setAttribute('aria-label', 'Mở menu'); } });
  const shareUrl = safeUrl(config.canonicalUrl, location.href.split('#')[0]);
  $('#share-x').href = `https://twitter.com/intent/tweet?text=${encodeURIComponent('MITOM — Creative tools, together.')}&url=${encodeURIComponent(shareUrl)}`;
  $('#share-facebook').href = `https://www.facebook.com/sharer/sharer.php?u=${encodeURIComponent(shareUrl)}`;
  $('#copy-link').addEventListener('click', async () => {
    try { await navigator.clipboard.writeText(shareUrl); showToast('Đã sao chép liên kết MITOM.'); } catch (_) {
      openDialog(`<p class="dialog-kicker">MITOM / SHARE</p><h2>Share the<br><span>toolkit.</span></h2><p class="dialog-description">Sao chép địa chỉ bên dưới để chia sẻ MITOM.</p><input class="share-url-input" type="text" value="${escapeHTML(shareUrl)}" readonly aria-label="Địa chỉ chia sẻ MITOM" style="width:100%;padding:13px;border:1px solid var(--line);background:var(--surface);color:var(--ink);font-size:14px">`);
      const input = $('.share-url-input'); input.focus(); input.select();
    }
  });
  $('#year').textContent = new Date().getFullYear();
  renderApps();
})();
