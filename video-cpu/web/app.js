import {LocalVideoClient} from './client.mjs';

const $ = id => document.getElementById(id);
const words = {
  en: {
    createTitle:'Create your next take.',productImage:'Product photo',remove:'Remove',choosePhoto:'Choose or drop a photo',imageTypes:'PNG, JPG or WebP · up to 15 MB',prompt:'Describe the movement',promptPlaceholder:'Describe what moves, the camera angle, and the lighting…',format:'Format',length:'Length',landscape:'Landscape · 320 × 192',portrait:'Portrait · 192 × 320',square:'Square · 256 × 256',shortLength:'1.1 seconds',longLength:'2.1 seconds',generate:'Generate video',cancel:'Cancel',connect:'Connect',reconnect:'Reconnect',yourVideo:'Your video',download:'Download MP4',emptyTitle:'A photo. Your direction.',emptyDescription:'Choose a product photo and describe the video you want to create.',noVideo:'No video generated yet',connectionTitle:'Connect MITOM Video',connectionDescription:'Open MITOM Video on this computer and enter its connection code.',engineAddress:'Engine address',connectionCode:'Connection code',close:'Close',retryDownload:'Retry download',connecting:'Connecting…',connectionOff:'Connect engine',connectionReady:'Connected',connectionNeedsModel:'Model unavailable',startEngine:'Start MITOM Video on this computer, then connect.',ready:'Ready when you are.',uploading:'Uploading photo',submitting:'Starting video',queued:'Waiting in queue',starting:'Starting video',loading:'Loading model',compiling:'Preparing video',encoding:'Saving MP4',working:'Creating video',denoising:'Generating motion',downloading:'Loading your video',completed:'Video ready',cancelled:'Video cancelled',stopping:'Stopping video…',step:'Step {step} / {total}',model_missing_or_invalid:'Install the local video model in MITOM Video, then reconnect.',model_missing:'Install the local video model in MITOM Video, then reconnect.',model_unverified:'The model is ready for its first video.',encoder_missing:'Install the MP4 encoder in MITOM Video, then reconnect.',resume:'Reconnect to check this video.',downloadFailed:'Your video is ready. Reconnect and retry the download.',
    errors:{invalid_endpoint:'Enter a local address such as http://127.0.0.1:4176.',invalid_token:'Paste the connection code from MITOM Video.',pairing_required:'Connect MITOM Video before generating.',unauthorized:'The connection code has changed. Copy the current code from MITOM Video.',engine_unreachable:'Start MITOM Video on this computer, then reconnect.',invalid_image:'Choose a PNG, JPG or WebP photo, at least 32 px per side and up to 15 MB.',unsupported_image:'Choose a PNG, JPG or WebP photo.',image_too_large:'Choose a photo smaller than 15 MB.',invalid_prompt:'Enter a description of 1–4000 characters.',invalid_options:'Choose one of the available video formats.',image_not_found:'Choose your photo again after restarting MITOM Video.',job_not_found:'This video is no longer in the current session.',video_not_ready:'Your video is still being created.',video_not_found:'The video is no longer available. Generate it again.',invalid_video:'The engine did not return a complete MP4. Try generating again.',video_too_large:'The MP4 is too large to download here.',queue_full:'The queue is full. Wait for a video to finish.',upload_limit:'Restart MITOM Video to start a new photo session.',job_limit:'Restart MITOM Video to start a new video session.',runtime_missing:'Install the local video runtime, then reconnect.',memory_limit:'Generation stopped at the memory limit. Try the shorter video.',deadline_exceeded:'Generation reached its time limit. Try the shorter video.',timeout:'The engine did not respond. Reconnect and check its status.',inference_failed:'The video could not be generated. Check MITOM Video and try again.',worker_failed:'MITOM Video stopped. Open it again and reconnect.',containment_failed:'The local worker could not start. Restart MITOM Video.',origin_forbidden:'Open the video page from MITOM Video.',host_forbidden:'Use the video page on this computer.',model_missing_or_invalid:'Install the local video model in MITOM Video, then reconnect.',model_missing:'Install the local video model in MITOM Video, then reconnect.',encoder_missing:'Install the MP4 encoder in MITOM Video, then reconnect.',engine_error:'The local engine returned an error. Reconnect and try again.'}
  },
  vi: {
    createTitle:'Tạo thước phim tiếp theo.',productImage:'Ảnh sản phẩm',remove:'Bỏ ảnh',choosePhoto:'Chọn hoặc kéo ảnh vào đây',imageTypes:'PNG, JPG hoặc WebP · tối đa 15 MB',prompt:'Mô tả chuyển động',promptPlaceholder:'Mô tả chuyển động, góc quay và ánh sáng bạn muốn…',format:'Khung hình',length:'Thời lượng',landscape:'Ngang · 320 × 192',portrait:'Dọc · 192 × 320',square:'Vuông · 256 × 256',shortLength:'1,1 giây',longLength:'2,1 giây',generate:'Tạo video',cancel:'Hủy',connect:'Kết nối',reconnect:'Kết nối lại',yourVideo:'Video của bạn',download:'Tải MP4',emptyTitle:'Một bức ảnh. Ý tưởng của bạn.',emptyDescription:'Chọn ảnh sản phẩm và mô tả video bạn muốn tạo.',noVideo:'Chưa tạo video',connectionTitle:'Kết nối MITOM Video',connectionDescription:'Mở MITOM Video trên máy này rồi nhập mã kết nối.',engineAddress:'Địa chỉ ứng dụng',connectionCode:'Mã kết nối',close:'Đóng',retryDownload:'Tải lại video',connecting:'Đang kết nối…',connectionOff:'Kết nối ứng dụng',connectionReady:'Đã kết nối',connectionNeedsModel:'Chưa có model',startEngine:'Mở MITOM Video trên máy này rồi kết nối.',ready:'Sẵn sàng tạo video.',uploading:'Đang nạp ảnh',submitting:'Đang bắt đầu video',queued:'Đang chờ đến lượt',starting:'Đang bắt đầu video',loading:'Đang nạp model',compiling:'Đang chuẩn bị video',encoding:'Đang lưu MP4',working:'Đang tạo video',denoising:'Đang tạo chuyển động',downloading:'Đang nạp video của bạn',completed:'Video đã sẵn sàng',cancelled:'Đã hủy video',stopping:'Đang dừng video…',step:'Bước {step} / {total}',model_missing_or_invalid:'Cài model video trong MITOM Video rồi kết nối lại.',model_missing:'Cài model video trong MITOM Video rồi kết nối lại.',model_unverified:'Model đã sẵn sàng tạo video đầu tiên.',encoder_missing:'Cài bộ xuất MP4 trong MITOM Video rồi kết nối lại.',resume:'Kết nối lại để kiểm tra video này.',downloadFailed:'Video đã tạo xong. Kết nối lại rồi tải video.',
    errors:{invalid_endpoint:'Nhập địa chỉ trên máy này, ví dụ http://127.0.0.1:4176.',invalid_token:'Dán mã kết nối từ MITOM Video.',pairing_required:'Kết nối MITOM Video trước khi tạo.',unauthorized:'Mã kết nối đã thay đổi. Sao chép mã hiện tại từ MITOM Video.',engine_unreachable:'Mở MITOM Video trên máy này rồi kết nối lại.',invalid_image:'Chọn ảnh PNG, JPG hoặc WebP, mỗi cạnh ít nhất 32 px và tối đa 15 MB.',unsupported_image:'Chọn ảnh PNG, JPG hoặc WebP.',image_too_large:'Chọn ảnh nhỏ hơn 15 MB.',invalid_prompt:'Nhập mô tả từ 1–4000 ký tự.',invalid_options:'Chọn một trong các khung hình có sẵn.',image_not_found:'Chọn lại ảnh sau khi khởi động lại MITOM Video.',job_not_found:'Video này không còn trong phiên hiện tại.',video_not_ready:'Video vẫn đang được tạo.',video_not_found:'Video không còn khả dụng. Hãy tạo lại.',invalid_video:'Ứng dụng chưa trả về MP4 hoàn chỉnh. Hãy thử tạo lại.',video_too_large:'MP4 quá lớn để tải ở đây.',queue_full:'Hàng đợi đã đầy. Chờ một video hoàn tất.',upload_limit:'Khởi động lại MITOM Video để bắt đầu phiên ảnh mới.',job_limit:'Khởi động lại MITOM Video để bắt đầu phiên video mới.',runtime_missing:'Cài bộ chạy video trên máy rồi kết nối lại.',memory_limit:'Đã dừng do giới hạn bộ nhớ. Thử video ngắn hơn.',deadline_exceeded:'Đã hết thời gian tạo. Thử video ngắn hơn.',timeout:'Ứng dụng chưa phản hồi. Kết nối lại để kiểm tra.',inference_failed:'Không tạo được video. Kiểm tra MITOM Video rồi thử lại.',worker_failed:'MITOM Video đã dừng. Mở lại ứng dụng rồi kết nối.',containment_failed:'Không khởi động được bộ chạy. Khởi động lại MITOM Video.',origin_forbidden:'Mở trang video từ MITOM Video.',host_forbidden:'Dùng trang video trên máy này.',model_missing_or_invalid:'Cài model video trong MITOM Video rồi kết nối lại.',model_missing:'Cài model video trong MITOM Video rồi kết nối lại.',encoder_missing:'Cài bộ xuất MP4 trong MITOM Video rồi kết nối lại.',engine_error:'Ứng dụng gặp lỗi. Kết nối lại rồi thử lại.'}
  }
};

Object.assign(words.en,{sectionVideo:'01 / VIDEO',sectionOutput:'02 / OUTPUT',failed:'Video not created',landscape:'Landscape · 512 × 320',portrait:'Portrait · 320 × 512',square:'Square · 384 × 384',higherDetail:'Higher detail (experimental) · 704 × 448',fastLandscape:'Fast · 320 × 192',shortLength:'2.1 seconds',longLength:'6.1 seconds',exportSize:'720p short side'});
Object.assign(words.vi,{sectionVideo:'01 / VIDEO',sectionOutput:'02 / KẾT QUẢ',failed:'Chưa tạo được video',landscape:'Ngang · 512 × 320',portrait:'Dọc · 320 × 512',square:'Vuông · 384 × 384',higherDetail:'Chi tiết hơn (thử nghiệm) · 704 × 448',fastLandscape:'Nhanh · 320 × 192',shortLength:'2,1 giây',longLength:'6,1 giây',exportSize:'Cạnh ngắn 720p'});
let imageSelection = 0;
let language = 'en';
try { const saved = localStorage.getItem('mitom.video.language'); if (saved === 'vi' || saved === 'en') language = saved; } catch {}
const state = {client:null,token:'',endpoint:'http://127.0.0.1:4176',connected:false,enabled:false,file:null,imageUrl:null,videoUrl:null,job:null,activeId:null,busy:false,polling:false,cancelling:false,notice:'startEngine',errorCode:null,phase:null,downloadError:false,connecting:false};
const t = key => words[language][key] || words.en[key] || key;
const text = (id, value) => { $(id).textContent = value; };
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));

function errorText(error) {
  const code = typeof error === 'string' ? error : error?.name === 'TimeoutError' ? 'timeout' : error?.code || 'engine_error';
  return words[language].errors[code] || words.en.errors[code] || words[language].errors.engine_error;
}

function render() {
  document.documentElement.lang = language;
  document.querySelectorAll('[data-t]').forEach(node => { node.textContent = t(node.dataset.t); });
  document.querySelectorAll('[data-placeholder]').forEach(node => { node.placeholder = t(node.dataset.placeholder); });
  document.querySelectorAll('[data-aria]').forEach(node => { node.setAttribute('aria-label', t(node.dataset.aria)); });
  document.querySelectorAll('[data-lang]').forEach(node => { node.setAttribute('aria-pressed', String(node.dataset.lang === language)); });
  const label = state.connected ? (state.enabled ? 'connectionReady' : 'connectionNeedsModel') : 'connectionOff';
  text('connectionLabel', t(label));
  $('connectionDot').className = `status-dot${state.connected && state.enabled ? ' ready' : state.errorCode ? ' error' : ''}`;
  text('noticeText', state.errorCode ? errorText(state.errorCode) : t(state.busy && state.phase ? phaseKey(state.phase) : state.notice));
  $('notice').classList.toggle('error', Boolean(state.errorCode));
  $('noticeAction').hidden = state.connected && state.enabled;
  $('noticeAction').textContent = t(state.activeId || state.connected ? 'reconnect' : 'connect');
  $('generate').disabled = state.busy || Boolean(state.activeId) || !state.connected || !state.enabled || !state.file || !$('prompt').value.trim();
  $('inputFields').disabled = state.busy || Boolean(state.activeId);
  $('cancel').hidden = !state.activeId;
  $('cancel').disabled = !state.connected || state.cancelling;
  $('connectionButton').disabled = state.busy && state.connected;
  $('connectSubmit').disabled = state.connecting;
  if (state.connecting) text('connectSubmit', t('connecting'));
  text('characterCount', `${$('prompt').value.length} / 4000`);
  $('retryVideo').hidden = !state.downloadError;
  if (state.phase) text('phaseLabel', t(state.cancelling ? 'stopping' : phaseKey(state.phase)));
  if (state.job?.progress) {
    const {step, total_steps:total} = state.job.progress;
    text('stepLabel', Number.isInteger(step) && step > 0 && Number.isInteger(total) ? t('step').replace('{step}', step).replace('{total}', total) : '');
  }
  text('outputStatus', state.videoUrl ? t('completed') : state.job?.status === 'cancelled' ? t('cancelled') : state.phase ? t(phaseKey(state.phase)) : t('noVideo'));
}

function phaseKey(phase) {
  if (['uploading','submitting','queued','starting','denoising','completed','cancelled','failed','downloading'].includes(phase)) return phase;
  if (/encode|save|export/.test(phase)) return 'encoding';
  if (/compile|reshape/.test(phase)) return 'compiling';
  if (/load|read_model|import|validate/.test(phase)) return 'loading';
  return 'working';
}

function showProgress(phase, job = null) {
  state.phase = phase;
  $('progressPanel').hidden = false;
  const step = Number(job?.progress?.step);
  const total = Number(job?.progress?.total_steps);
  if (phase === 'completed') { $('progress').max = total || 8; $('progress').value = total || 8; }
  else if (phase === 'denoising' && Number.isFinite(step) && total > 0) { $('progress').max = total; $('progress').value = Math.min(total, Math.max(0, step)); }
  else $('progress').removeAttribute('value');
  render();
}

function openConnection() {
  $('endpoint').value = state.endpoint;
  $('pairCode').value = state.token;
  $('connectionError').hidden = true;
  if (!$('connectionDialog').open) $('connectionDialog').showModal();
  (state.token ? $('endpoint') : $('pairCode')).focus();
}

async function connect(endpoint, token, {automatic = false} = {}) {
  if (state.connecting) return;
  state.connecting = true;
  render();
  try {
    const client = new LocalVideoClient({endpoint,token});
    const health = await client.health();
    state.client = client;
    state.endpoint = client.endpoint;
    state.token = token;
    state.connected = true;
    state.enabled = health.generation_enabled === true;
    state.errorCode = null;
    state.notice = state.enabled ? 'ready' : words.en[health.status] ? health.status : 'model_missing_or_invalid';
    $('connectionDialog').close();
    if (state.activeId && !state.polling) void pollJob(state.activeId);
  } catch (error) {
    state.connected = false;
    state.enabled = false;
    state.errorCode = error.code || (error.name === 'TimeoutError' ? 'timeout' : 'engine_unreachable');
    if (!automatic) { text('connectionError', errorText(error)); $('connectionError').hidden = false; }
  } finally { state.connecting = false; render(); }
}

function acceptFile(file) {
  if (!file || state.busy || state.activeId) return;
  if (!['image/png','image/jpeg','image/webp'].includes(file.type) || file.size < 1 || file.size > 15 * 1024 * 1024) { state.errorCode = 'invalid_image'; render(); return; }
  const imageUrl = URL.createObjectURL(file);
  const selection = ++imageSelection;
  const image = new Image();
  image.onload = () => {
    if (selection !== imageSelection) { URL.revokeObjectURL(imageUrl); return; }
    if (image.naturalWidth < 32 || image.naturalHeight < 32 || Math.max(image.naturalWidth,image.naturalHeight) > 12000 || image.naturalWidth * image.naturalHeight > 24000000) { URL.revokeObjectURL(imageUrl); state.errorCode = 'invalid_image'; render(); return; }
    if (state.imageUrl) URL.revokeObjectURL(state.imageUrl);
    state.file = file;
    state.imageUrl = imageUrl;
    $('imagePreview').src = imageUrl;
    $('imagePreview').hidden = false;
    $('uploadPlaceholder').hidden = true;
    $('removeImage').hidden = false;
    text('imageName',file.name);
    $('imageName').hidden = false;
    state.errorCode = null;
    render();
  };
  image.onerror = () => { URL.revokeObjectURL(imageUrl); state.errorCode = 'invalid_image'; render(); };
  image.src = imageUrl;
}

function updateJob(job) {
  state.job = job;
  state.errorCode = null;
  showProgress(job.phase || job.status, job);
}

async function loadVideo(id) {
  state.downloadError = false;
  state.busy = true;
  showProgress('downloading');
  try {
    const blob = await state.client.video(id);
    if (state.videoUrl) URL.revokeObjectURL(state.videoUrl);
    state.videoUrl = URL.createObjectURL(blob);
    $('video').src = state.videoUrl;
    $('video').hidden = false;
    $('emptyOutput').hidden = true;
    $('download').href = state.videoUrl;
    $('download').download = `MITOM-Video-${new Date().toISOString().replace(/[:.]/g,'-')}.mp4`;
    $('download').hidden = false;
    state.errorCode = null;
    state.notice = 'completed';
    showProgress('completed',state.job);
  } catch (error) { state.downloadError = true; state.errorCode = error.code || 'engine_error'; state.notice = 'downloadFailed'; if (['engine_unreachable','unauthorized'].includes(error.code)) { state.connected = false; state.enabled = false; } }
  finally { state.busy = false; render(); }
}

async function pollJob(id) {
  if (state.polling) return;
  state.polling = true;
  state.busy = true;
  let failures = 0;
  try {
    while (state.activeId === id) {
      let job;
      try { job = await state.client.job(id); failures = 0; }
      catch (error) { if (++failures < 3 && error.code === 'engine_unreachable') { await delay(1400); continue; } throw error; }
      updateJob(job);
      if (['completed','failed','cancelled'].includes(job.status)) {
        state.activeId = null;
        state.cancelling = false;
        state.busy = false;
        if (job.status === 'completed') await loadVideo(id);
        else if (job.status === 'failed') { state.errorCode = job.error?.code || 'inference_failed'; $('progressPanel').hidden = true; }
        else { state.notice = 'cancelled'; $('progressPanel').hidden = true; }
        break;
      }
      await delay(900);
    }
  } catch (error) { state.errorCode = error.code || 'engine_unreachable'; if (error.code === 'job_not_found') { state.activeId = null; state.phase = 'failed'; $('progressPanel').hidden = true; } else { state.connected = false; state.enabled = false; state.notice = 'resume'; } }
  finally { state.polling = false; state.busy = false; render(); }
}

$('videoForm').addEventListener('submit',async event => {
  event.preventDefault();
  if ($('generate').disabled) return;
  state.busy = true;
  state.errorCode = null;
  state.downloadError = false;
  state.job = null;
  if (state.videoUrl) { URL.revokeObjectURL(state.videoUrl); state.videoUrl = null; }
  $('video').removeAttribute('src'); $('video').hidden = true; $('download').hidden = true; $('emptyOutput').hidden = false;
  showProgress('uploading');
  try {
    const image = await state.client.upload(state.file);
    showProgress('submitting');
    const [width,height] = $('resolution').value.split('x').map(Number);
    const job = await state.client.createJob({image_id:image.image_id,prompt:$('prompt').value,options:{width,height,frames:Number($('frames').value),fps:8,seed:42}});
    state.activeId = job.job_id;
    updateJob(job);
    await pollJob(job.job_id);
  } catch (error) { state.busy = false; state.phase = 'failed'; $('progressPanel').hidden = true; state.errorCode = error.code || 'engine_error'; if (['engine_unreachable','unauthorized'].includes(error.code)) { state.connected = false; state.enabled = false; } render(); }
});

$('cancel').addEventListener('click',async () => {
  if (!state.activeId || state.cancelling) return;
  state.cancelling = true;
  render();
  try { const job = await state.client.cancel(state.activeId); updateJob(job); }
  catch (error) { state.cancelling = false; state.errorCode = error.code || 'engine_error'; render(); }
});
$('retryVideo').addEventListener('click',() => { if (state.job?.status === 'completed') void loadVideo(state.job.job_id); });
$('connectionButton').addEventListener('click',openConnection);
$('noticeAction').addEventListener('click',openConnection);
$('closeConnection').addEventListener('click',() => $('connectionDialog').close());
$('connectionForm').addEventListener('submit',event => { event.preventDefault(); void connect($('endpoint').value.trim(),$('pairCode').value.trim()); });
$('imageInput').addEventListener('change',event => acceptFile(event.target.files[0]));
$('removeImage').addEventListener('click',() => { ++imageSelection; if (state.imageUrl) URL.revokeObjectURL(state.imageUrl); state.imageUrl = null; state.file = null; $('imagePreview').hidden = true; $('imagePreview').removeAttribute('src'); $('imageInput').value = ''; $('uploadPlaceholder').hidden = false; $('removeImage').hidden = true; $('imageName').hidden = true; render(); });
$('prompt').addEventListener('input',render);
['dragenter','dragover'].forEach(name => $('dropzone').addEventListener(name,event => { event.preventDefault(); if (!state.busy) $('dropzone').classList.add('dragging'); }));
['dragleave','drop'].forEach(name => $('dropzone').addEventListener(name,event => { event.preventDefault(); $('dropzone').classList.remove('dragging'); if (name === 'drop') acceptFile(event.dataTransfer.files[0]); }));
document.querySelectorAll('[data-lang]').forEach(button => button.addEventListener('click',() => { language = button.dataset.lang; try { localStorage.setItem('mitom.video.language',language); } catch {} render(); }));
window.addEventListener('pagehide',() => { if (state.imageUrl) URL.revokeObjectURL(state.imageUrl); if (state.videoUrl) URL.revokeObjectURL(state.videoUrl); });

render();
const injected = document.querySelector('meta[name="mitom-session-token"]');
const token = injected?.content.trim() || '';
if (injected) injected.content = '';
if (token && location.protocol === 'http:' && ['127.0.0.1','localhost'].includes(location.hostname)) {
  void connect(location.origin,token,{automatic:true});
} else {
  try {
    const discovery = await new LocalVideoClient().discover();
    if (['model_missing_or_invalid','model_missing','encoder_missing'].includes(discovery.model_state)) state.notice = discovery.model_state;
    render();
  } catch { /* The connection action remains available while the engine is closed. */ }
}
