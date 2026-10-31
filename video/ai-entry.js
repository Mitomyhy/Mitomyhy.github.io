const words = {
  en: { title:'Direct the movement.', lead:'Your product photo. Your prompt. Generated on your computer.', tag:'MITOM PRODUCT VIDEO / LOCAL AI', open:'Open Product Video', download:'Open MITOM Launcher', photo:'Photo animation ↗', step1:'Open MITOM Launcher and install Product Video.', step2:'Open Product Video. The first video prepares local AI.', step3:'Choose a photo, describe the scene and movement, then export MP4.', footer:'Windows 10/11 · 16 GB RAM · CPU', detail:'No API key. No credits. Works offline after setup.', model:'Legacy LTX Video ↗', heading:'Make the next take yours.', label:'PHOTO + PROMPT → VIDEO', art:'Image to video', pending:'Preparing the download', launchHelp:'Start Product Video from Launcher on this computer, then connect the studio.', files:'App + model download' },
  vi: { title:'Chuyển động theo ý bạn.', lead:'Ảnh sản phẩm. Prompt của bạn. Tạo ngay trên máy.', tag:'MITOM PRODUCT VIDEO / AI CỤC BỘ', open:'Mở Product Video', download:'Mở MITOM Launcher', photo:'Chuyển động ảnh ↗', step1:'Mở MITOM Launcher và cài Product Video.', step2:'Mở Product Video. Lần tạo đầu chuẩn bị AI local.', step3:'Chọn ảnh, mô tả nền và chuyển động, rồi xuất MP4.', footer:'Windows 10/11 · RAM 16 GB · CPU', detail:'Không API key. Không credit. Dùng offline sau khi cài.', model:'LTX Video trước đây ↗', heading:'Tạo thước phim tiếp theo.', label:'ẢNH + PROMPT → VIDEO', art:'Ảnh thành video', pending:'Đang chuẩn bị bản tải', launchHelp:'Mở Product Video từ Launcher trên máy này, rồi kết nối studio.', files:'Tải ứng dụng + model' }
};
const productStudio = 'https://mitomyhy.github.io/video-product/';
const release = 'https://github.com/Mitomyhy/Mitomyhy.github.io/releases/tag/video-cpu-2026.10.23.1';
const view = document.createElement('main');
view.id = 'video-ai-entry';
view.className = 'video-ai-entry';
view.hidden = true;
view.innerHTML = `<section class="video-ai-intro"><span class="video-ai-kicker" data-ai="tag"></span><h1 data-ai="title"></h1><p class="video-ai-lead" data-ai="lead"></p><div class="video-ai-actions"><a class="button button-dark" href="${productStudio}" data-ai="open"></a><a class="button" href="mitom://video" data-ai="download"></a></div><p class="video-ai-launch-help" data-ai="launchHelp"></p><ol><li data-ai="step1"></li><li data-ai="step2"></li><li data-ai="step3"></li></ol><p class="video-ai-meta" data-ai="footer"></p><div class="video-ai-secondary"><a href="#video/photo" data-ai="photo"></a><a href="${release}" target="_blank" rel="noopener" data-ai="model"></a></div></section><section class="video-ai-art" aria-label="Image to video"><div class="video-ai-film"><svg viewBox="0 0 120 120" aria-hidden="true"><rect x="16" y="20" width="88" height="80"/><path d="m48 40 31 20-31 20Z"/><path d="M28 20v80m64-80v80M16 36h12m-12 16h12m-12 16h12m-12 16h12m64-48h12M92 52h12M92 68h12M92 84h12"/></svg><span data-ai="label"></span><h2 data-ai="heading"></h2></div><p data-ai="detail"></p></section>`;
document.getElementById('main').before(view);
function translate() {
  const copy = words[document.documentElement.lang === 'vi' ? 'vi' : 'en'];
  view.querySelectorAll('[data-ai]').forEach(node => { node.textContent = copy[node.dataset.ai]; });
  view.querySelector('.video-ai-art').setAttribute('aria-label',copy.art);
}
function route() {
  const active = /^#video\/?$/.test(location.hash);
  const photo = /^#video\/photo\/?$/.test(location.hash);
  const tools = /^#tools(?:\/(?:depth|scrabble|sky|moon))?$/.test(location.hash);
  view.hidden = !active;
  document.body.classList.toggle('video-ai-open', active);
  document.body.classList.toggle('video-open', photo);
  document.body.classList.toggle('tools-open', tools);
  for (const [id, hidden] of [['main',active || photo || tools],['tools-app',!tools],['video-app',!photo]]) {
    const section = document.getElementById(id);
    if (section) section.hidden = hidden;
  }
  document.querySelectorAll('.nav-video').forEach(node => {
    if (active || photo) node.setAttribute('aria-current','page');
    else node.removeAttribute('aria-current');
  });
  document.querySelectorAll('.nav-tools').forEach(node => {
    if (tools) node.setAttribute('aria-current','page');
    else node.removeAttribute('aria-current');
  });
}
let routeScheduled = false;
function scheduleRoute() {
  if (routeScheduled) return;
  routeScheduled = true;
  requestAnimationFrame(() => { routeScheduled = false; route(); });
}
new MutationObserver(translate).observe(document.documentElement,{attributes:true,attributeFilter:['lang']});
// Imported workspaces can initialize after this entry. Reconcile after their
// synchronous route handlers and when a workspace is first inserted.
new MutationObserver(scheduleRoute).observe(document.body,{childList:true});
window.addEventListener('hashchange',scheduleRoute);
window.addEventListener('load',scheduleRoute,{once:true});
translate(); route();

