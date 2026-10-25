(() => {
  'use strict';
  const config = window.MITOM_CONFIG || {};
  const dictionaries = window.MITOM_I18N || { en: {}, vi: {} };
  const apps = Array.isArray(config.apps) ? config.apps : [];
  const $ = (selector) => document.querySelector(selector);
  const dialog = $('#app-dialog');
  const dialogContent = $('#dialog-content');
  let language = 'en';
  try { if (localStorage.getItem('mitom-language') === 'vi') language = 'vi'; } catch (_) { /* English is the default when storage is unavailable. */ }
  let currentFilter = 'all';
  let currentView = 'grid';
  let toastTimer;
  let toastKey;
  let lastFocus;
  let downloadDialogOpen = false;

  const escapeHTML = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
  const t = (key, values = {}) => (dictionaries[language]?.[key] ?? dictionaries.en?.[key] ?? key).replace(/\{(\w+)\}/g, (match, name) => values[name] ?? match);
  // Existing string fields still work; new apps can provide { en, vi } fields.
  const localized = (value) => typeof value === 'string' ? value : (value?.[language] ?? value?.en ?? value?.vi ?? '');
  const safeUrl = (value, fallback = '#') => {
    if (!value) return fallback;
    try { const url = new URL(value, location.href); return ['https:', 'http:'].includes(url.protocol) ? url.href : fallback; } catch (_) { return fallback; }
  };
  const githubUrl = safeUrl(config.githubUrl, 'https://github.com/Mitomyhy');
  const repositoryUrl = safeUrl(config.repositoryUrl, githubUrl);
  let releaseUrl = safeUrl(config.releaseUrl, repositoryUrl);
  let downloadUrl = safeUrl(config.launcherDownloadUrl, '');
  let launcherVersion = config.launcherVersion || '';
  const shareUrl = safeUrl(config.canonicalUrl, location.href.split('#')[0]);
  const arrow = (symbol) => `<span aria-hidden="true">${symbol}</span>`;
  const buttonLabel = (key, symbol = '') => `<span>${escapeHTML(t(key))}</span>${symbol ? ` ${arrow(symbol)}` : ''}`;

  const versionParts = value => typeof value === 'string' && /^\d{4}\.\d{2}\.\d{2}\.\d+$/.test(value) && value.split('.').every(part => Number.isSafeInteger(Number(part))) ? value.split('.').map(Number) : null;
  const versionAtLeast = (next, current) => {
    const a = versionParts(next), b = versionParts(current);
    if (!a) return false;
    if (!b) return true;
    for (let i = 0; i < a.length; i++) { if (a[i] !== b[i]) return a[i] > b[i]; }
    return true;
  };
  const panelApp = () => apps.find(app => app.id === 'photoshop-panel');
  const releaseVersions = () => `Launcher ${launcherVersion || '—'} · Photoshop Panel ${panelApp()?.version || '—'}`;
  function renderReleaseVersions() {
    document.querySelectorAll('[data-release-versions]').forEach(node => { node.textContent = releaseVersions(); });
    document.querySelectorAll('[data-launcher-version]').forEach(node => { node.textContent = launcherVersion; });
  }
  async function refreshLatestDownload() {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 5000);
    try {
      const response = await fetch('releases/latest-download.json', { cache: 'no-store', credentials: 'omit', signal: controller.signal });
      if (!response.ok) return;
      const latest = await response.json();
      if (!latest || !versionAtLeast(latest.version, launcherVersion) || !Number.isSafeInteger(latest.size) || latest.size <= 0 || typeof latest.sha256 !== 'string' || !/^[a-f0-9]{64}$/i.test(latest.sha256)) return;
      const url = new URL(latest.url);
      const expectedPath = `/releases/mitom-launcher/${latest.version}/MITOM-Launcher-Setup-${latest.version}.exe`;
      if (url.origin !== 'https://mitomyhy.github.io' || url.username || url.password || url.pathname !== expectedPath || url.search || url.hash) return;
      downloadUrl = url.href;
      launcherVersion = latest.version;
      releaseUrl = `https://github.com/Mitomyhy/Mitomyhy.github.io/tree/main/releases/mitom-launcher/${latest.version}`;
      if (panelApp() && versionAtLeast(latest.panel_version, panelApp().version)) panelApp().version = latest.panel_version;
      renderReleaseVersions();
      renderApps();
      if (dialog.open && downloadDialogOpen) showDownload();
    } catch (_) { /* Keep the current, pinned download when metadata is unavailable or invalid. */ }
    finally { clearTimeout(timer); }
  }

  function translateNodes(root = document) {
    root.querySelectorAll('[data-i18n]').forEach((element) => { element.textContent = t(element.dataset.i18n); });
    // Only the static dictionary has HTML; config and app fields never enter this path.
    root.querySelectorAll('[data-i18n-html]').forEach((element) => { element.innerHTML = t(element.dataset.i18nHtml); });
    ['aria-label', 'title', 'placeholder', 'alt', 'content'].forEach((attribute) => {
      root.querySelectorAll(`[data-i18n-${attribute}]`).forEach((element) => { element.setAttribute(attribute, t(element.getAttribute(`data-i18n-${attribute}`))); });
    });
  }

  function updateThemeLabel() {
    const dark = document.body.classList.contains('dark');
    $('#theme-toggle').setAttribute('aria-label', t(dark ? 'theme.light' : 'theme.dark'));
    $('#theme-toggle').title = t(dark ? 'theme.light' : 'theme.dark');
    $('#theme-toggle').setAttribute('aria-pressed', String(dark));
    $('meta[name="theme-color"]').content = dark ? '#191a1c' : '#f2f1ed';
  }

  function updateMenuLabel() {
    $('.menu-toggle').setAttribute('aria-label', t($('#mobile-nav').hidden ? 'nav.menuOpen' : 'nav.menuClose'));
  }

  function updateShareLinks() {
    $('#share-x').href = `https://twitter.com/intent/tweet?text=${encodeURIComponent(t('meta.title'))}&url=${encodeURIComponent(shareUrl)}`;
    $('#share-facebook').href = `https://www.facebook.com/sharer/sharer.php?u=${encodeURIComponent(shareUrl)}`;
  }

  function applyLanguage(nextLanguage, persist = false) {
    language = nextLanguage === 'vi' ? 'vi' : 'en';
    document.documentElement.lang = language;
    let saved = true;
    if (persist) {
      try { localStorage.setItem('mitom-language', language); } catch (_) { saved = false; }
    }
    translateNodes();
    updateThemeLabel();
    updateMenuLabel();
    updateShareLinks();
    renderApps();
    renderReleaseVersions();
    // Keep the same select DOM node so switching languages preserves keyboard focus.
    const select = $('#settings-language');
    if (select) select.value = language;
    const status = $('#settings-status');
    if (status && persist) {
      status.dataset.i18n = saved ? 'settings.saved' : 'settings.unsaved';
      status.textContent = t(status.dataset.i18n);
    }
    if (toastKey && !$('#toast').hidden) $('#toast').textContent = t(toastKey);
  }

  function showToast(key) {
    toastKey = key;
    $('#toast').textContent = t(key);
    $('#toast').hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { $('#toast').hidden = true; }, 3500);
  }

  function openDialog(content) {
    downloadDialogOpen = false;
    if (!dialog.open) lastFocus = document.activeElement;
    dialogContent.innerHTML = content;
    if (!dialog.open) dialog.showModal();
    document.body.style.overflow = 'hidden';
  }

  function closeDialog() { dialog.close(); }
  dialog.addEventListener('close', () => {
    document.body.style.overflow = '';
    if (lastFocus?.isConnected && lastFocus.getClientRects().length) lastFocus.focus();
    else if ($('.menu-toggle').getClientRects().length) $('.menu-toggle').focus();
  });
  $('.dialog-close').addEventListener('click', closeDialog);
  dialog.addEventListener('click', (event) => {
    const rect = dialog.getBoundingClientRect();
    if (event.target === dialog && (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom)) closeDialog();
  });

  function showSettings() {
    openDialog(`<p class="dialog-kicker" data-i18n="settings.kicker">${escapeHTML(t('settings.kicker'))}</p><h2 id="dialog-title" data-i18n="settings.title">${escapeHTML(t('settings.title'))}</h2><p class="dialog-description" data-i18n="settings.description">${escapeHTML(t('settings.description'))}</p><div class="settings-field"><label for="settings-language" data-i18n="settings.language">${escapeHTML(t('settings.language'))}</label><select id="settings-language" class="settings-language" aria-describedby="settings-language-help"><option value="en" lang="en">English</option><option value="vi" lang="vi">Tiếng Việt</option></select><p id="settings-language-help" class="dialog-note" data-i18n="settings.help">${escapeHTML(t('settings.help'))}</p></div><p id="settings-status" class="dialog-note" role="status" aria-live="polite"></p>`);
    $('#settings-language').value = language;
    $('#settings-language').focus();
  }

  function showDownload() {
    if (downloadUrl) {
      openDialog(`<p class="dialog-kicker">${escapeHTML(t('dialog.downloadKicker'))}</p><h2 id="dialog-title">${t('dialog.downloadTitle')}</h2><p class="dialog-description">${escapeHTML(t('dialog.downloadDescription'))}</p><ol class="dialog-checklist"><li>${escapeHTML(t('dialog.downloadStep1'))}</li><li>${escapeHTML(t('dialog.downloadStep2'))}</li><li>${escapeHTML(t('dialog.downloadStep3'))}</li></ol><div class="dialog-actions"><a class="button button-dark" href="${escapeHTML(downloadUrl)}">${buttonLabel('download.short', '↓')}</a><a class="button" href="${escapeHTML(releaseUrl)}" target="_blank" rel="noopener">${buttonLabel('download.release', '↗')}</a></div><p class="dialog-note">${escapeHTML(t('dialog.downloadNote'))}</p>`);
    } else {
      openDialog(`<p class="dialog-kicker">${escapeHTML(t('dialog.preparingKicker'))}</p><h2 id="dialog-title">${t('dialog.preparingTitle')}</h2><p class="dialog-description">${escapeHTML(t('dialog.preparingDescription'))}</p><div class="dialog-actions"><a class="button button-dark" href="${escapeHTML(releaseUrl)}" target="_blank" rel="noopener">${buttonLabel('dialog.github', '↗')}</a><button class="button" data-close-dialog>${buttonLabel('dialog.ok')}</button></div><p class="dialog-note">${escapeHTML(t('dialog.preparingNote'))}</p>`);
    }
    if (downloadUrl) {
      dialogContent.innerHTML = `<div class="dialog-tags"><span>Launcher ${escapeHTML(launcherVersion || '—')}</span><span>Photoshop Panel ${escapeHTML(panelApp()?.version || '—')}</span></div>` + dialogContent.innerHTML;
      downloadDialogOpen = true;
    }
  }

  function showLaunch() {
    const protocol = /^[a-z][a-z0-9+.-]*:\/\//i.test(config.launcherProtocol || '') ? config.launcherProtocol : '';
    openDialog(`<p class="dialog-kicker">${escapeHTML(t('dialog.launchKicker'))}</p><h2 id="dialog-title">${t('dialog.launchTitle')}</h2><p class="dialog-description">${escapeHTML(t('dialog.launchDescription'))}</p><div class="dialog-actions">${protocol ? `<button class="button button-dark" data-open-protocol>${buttonLabel('download.open', '↗')}</button>` : ''}<button class="button" data-action="download">${buttonLabel('download.short', '↓')}</button></div><p id="launch-help" class="dialog-note">${escapeHTML(t('dialog.launchNote'))}</p>`);
  }

  function showDetails(id = 'photoshop-panel') {
    const app = apps.find((item) => item.id === id) || apps[0];
    if (!app) return;
    const appLink = safeUrl(app.githubUrl, repositoryUrl);
    openDialog(`<p class="dialog-kicker">${escapeHTML(t('dialog.detailsKicker'))} / ${escapeHTML(app.id.toUpperCase())}</p><div class="dialog-app-heading"><img src="${escapeHTML(safeUrl(app.icon))}" alt=""><h2 id="dialog-title">${escapeHTML(localized(app.name))}</h2></div><div class="dialog-tags"><span>${escapeHTML(localized(app.platform))}</span><span>${escapeHTML(app.version ? t('apps.version', { version: app.version }) : t('apps.viaLauncher'))}</span></div><p class="dialog-description">${escapeHTML(localized(app.detail || app.summary))}</p><div class="dialog-actions"><button class="button button-dark" data-action="download">${buttonLabel('apps.install', '↓')}</button><a class="button" href="${escapeHTML(appLink)}" target="_blank" rel="noopener"><span>GITHUB</span> ${arrow('↗')}</a></div><p class="dialog-note">${escapeHTML(localized(app.note) || t('dialog.detailsNote'))}</p>`);
  }

  function showUpcoming(section = 'IMAGE') {
    const supported = ['IMAGE', 'VIDEO', 'PRICING', 'COMMUNITY'];
    const selected = supported.includes(section) ? section : 'IMAGE';
    openDialog(`<p class="dialog-kicker">MITOM / ${escapeHTML(t(`nav.${selected.toLowerCase()}`))} / ${escapeHTML(t('upcoming.kicker'))}</p><h2 id="dialog-title">${t('upcoming.title')}</h2><p class="dialog-description">${escapeHTML(t(`upcoming.${selected}`))}</p><div class="dialog-actions"><button class="button button-dark" data-action="details">${buttonLabel('apps.explore', '↗')}</button><a class="button" href="${escapeHTML(githubUrl)}" target="_blank" rel="noopener">${buttonLabel('upcoming.follow', '↗')}</a></div>`);
  }

  function renderApps() {
    const query = $('#app-search').value.trim().toLocaleLowerCase(language);
    const matches = apps.filter((app) => {
      const searchable = [app.name, app.category, app.summary].flatMap((value) => typeof value === 'object' && value !== null ? Object.values(value) : [value]).join(' ').toLocaleLowerCase(language);
      return (currentFilter === 'all' || app.status === currentFilter) && searchable.includes(query);
    });
    const count = apps.length;
    $('#all-count').textContent = String(count).padStart(2, '0');
    $('#available-count').textContent = String(apps.filter((app) => app.status === 'available').length).padStart(2, '0');
    $('#app-lineup').classList.toggle('table-view', currentView === 'table');
    $('#app-lineup').hidden = matches.length === 0;
    $('#no-results').hidden = matches.length !== 0;
    const cards = matches.map((app) => {
      const index = String(apps.indexOf(app) + 1).padStart(2, '0');
      const name = localized(app.name);
      const available = app.status === 'available';
      return `<article class="app-card"><div class="app-card-top"><span class="app-index">${index}</span><a href="${escapeHTML(safeUrl(app.githubUrl, repositoryUrl))}" target="_blank" rel="noopener">GITHUB ${arrow('↗')}</a></div><div class="app-card-body"><div class="product-title"><img class="product-icon" src="${escapeHTML(safeUrl(app.icon))}" alt="" width="62" height="62"><div><h3>${escapeHTML(name)}</h3><p>${escapeHTML(localized(app.category))}<br>${escapeHTML(app.version ? t('apps.version', { version: app.version }) : t('apps.madeFor'))}</p></div></div><span class="status-tag">${escapeHTML(localized(app.statusLabel) || t(available ? 'apps.availableStatus' : 'apps.upcomingStatus'))}</span><p class="product-summary">${escapeHTML(localized(app.summary))}</p><div class="app-card-actions"><button class="button button-dark" data-action="${available ? 'download' : 'details'}" data-app-id="${escapeHTML(app.id)}">${buttonLabel(available ? 'apps.install' : 'apps.details', available ? '↓' : '↗')}</button><button class="button details-button" data-action="details" data-app-id="${escapeHTML(app.id)}" aria-label="${escapeHTML(t('apps.detailsLabel', { name }))}"><span aria-hidden="true">···</span></button></div></div></article>`;
    }).join('');
    const note = count === 1 && !query ? `<aside class="suite-note"><p class="eyebrow">${escapeHTML(t('apps.growingEyebrow'))}</p><h3>${t('apps.growingTitle')}</h3><p>${escapeHTML(t('apps.growingDescription'))}</p><a href="${escapeHTML(githubUrl)}" target="_blank" rel="noopener">${buttonLabel('apps.follow', '↗')}</a></aside>` : '';
    $('#app-lineup').innerHTML = cards + note;
  }

  document.addEventListener('click', (event) => {
    const action = event.target.closest('[data-action]');
    if (action) {
      if (action.dataset.action === 'download') showDownload();
      if (action.dataset.action === 'launch') showLaunch();
      if (action.dataset.action === 'details') showDetails(action.dataset.appId);
      if (action.dataset.action === 'upcoming') showUpcoming(action.dataset.section);
      if (action.dataset.action === 'settings') showSettings();
    }
    if (event.target.closest('[data-close-dialog]')) closeDialog();
    if (event.target.closest('[data-open-protocol]')) {
      // Only this explicit user gesture opens the installed application.
      if (/^[a-z][a-z0-9+.-]*:\/\//i.test(config.launcherProtocol || '')) {
        location.href = config.launcherProtocol;
        $('#launch-help').textContent = t('dialog.launchHelp');
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
  document.addEventListener('change', (event) => {
    if (event.target.id === 'settings-language') applyLanguage(event.target.value, true);
  });
  $('#app-search').addEventListener('input', renderApps);
  try { if (localStorage.getItem('mitom-theme') === 'dark') document.body.classList.add('dark'); } catch (_) { /* Theme also works without storage. */ }
  $('#theme-toggle').addEventListener('click', () => {
    document.body.classList.toggle('dark'); updateThemeLabel();
    try { localStorage.setItem('mitom-theme', document.body.classList.contains('dark') ? 'dark' : 'light'); } catch (_) {}
  });
  $('.menu-toggle').addEventListener('click', () => {
    const opened = $('#mobile-nav').hidden;
    $('#mobile-nav').hidden = !opened;
    $('.menu-toggle').setAttribute('aria-expanded', String(opened));
    updateMenuLabel();
  });
  $('#mobile-nav').addEventListener('click', (event) => {
    if (event.target.closest('a,button')) {
      $('#mobile-nav').hidden = true;
      $('.menu-toggle').setAttribute('aria-expanded', 'false');
      updateMenuLabel();
    }
  });
  $('#copy-link').addEventListener('click', async () => {
    try { await navigator.clipboard.writeText(shareUrl); showToast('share.copied'); } catch (_) {
      openDialog(`<p class="dialog-kicker">${escapeHTML(t('share.kicker'))}</p><h2 id="dialog-title">${t('share.title')}</h2><p class="dialog-description">${escapeHTML(t('share.description'))}</p><input class="share-url-input" type="text" value="${escapeHTML(shareUrl)}" readonly aria-label="${escapeHTML(t('share.address'))}" style="width:100%;padding:13px;border:1px solid var(--line);background:var(--surface);color:var(--ink);font-size:14px">`);
      const input = $('.share-url-input'); input.focus(); input.select();
    }
  });
  $('#year').textContent = new Date().getFullYear();
  applyLanguage(language);
  refreshLatestDownload();
})();

