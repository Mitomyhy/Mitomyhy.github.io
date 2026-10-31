import {LocalVideoClient, DEFAULT_ENDPOINT, MAX_IMAGE_BYTES, capabilities, imageDimensions} from './client.mjs';

const $ = id => document.getElementById(id);
const words = {
  vi:{eyebrow:'VIDEO LISTING ETSY',title:'Để sản phẩm chuyển động.',lead:'Thêm ảnh, mô tả cảnh bạn muốn và tạo video trên máy này.',product:'Ảnh sản phẩm',remove:'Bỏ ảnh',choose:'Chọn ảnh sản phẩm',drop:'Hoặc kéo thả / dán ảnh vào đây',fileTypes:'PNG · JPG · WebP — tối đa 15 MB',prompt:'Bạn muốn video như thế nào?',promptPlaceholder:'Ví dụ: Ornament đung đưa nhẹ, xoay trái rồi phải. Nền phòng khách Giáng sinh ấm áp, ánh đèn vàng và tuyết ngoài cửa sổ. Giữ rõ tên và hình in.',promptHint:'Mô tả chuyển động, khung cảnh và ánh sáng.',aspect:'Tỷ lệ khung hình',landscape:'Ngang',portrait:'Dọc',advanced:'Tùy chỉnh thêm',duration:'Thời lượng',resolution:'Độ phân giải',device:'Thiết bị tạo video',background:'Ảnh nền riêng (tùy chọn)',backgroundHint:'Nếu có ảnh nền của riêng bạn, thêm tại đây để dùng làm khung cảnh.',generate:'Tạo video sản phẩm',cancel:'Hủy tạo video',stopping:'Đang dừng video…',privacy:'Ảnh và video được xử lý trên máy này.',connect:'Kết nối',reconnect:'Kết nối lại',connecting:'Đang kết nối…',connectionOff:'Kết nối ứng dụng',connectionReady:'Đã kết nối · Cục bộ',connectionUnavailable:'Ứng dụng chưa sẵn sàng',connectionTitle:'Kết nối MITOM Product Video',connectionDescription:'Mở MITOM Product Video từ Launcher trên máy này, sau đó nhập mã kết nối của ứng dụng.',openLocal:'Mở ứng dụng đang chạy trên máy',engineAddress:'Địa chỉ ứng dụng',connectionCode:'Mã kết nối',connectionPrivacy:'Mã chỉ dùng trong phiên này, không được đưa vào đường dẫn hay lưu lên web.',outputEyebrow:'XEM TRƯỚC',outputTitle:'Thước phim của bạn.',silent:'Không âm thanh',emptyTitle:'Một sản phẩm. Một câu chuyện.',emptyBody:'Nền phù hợp, chuyển động nhẹ và sản phẩm ở trung tâm.',noVideo:'Video sẽ xuất hiện tại đây.',download:'Tải MP4',retryDownload:'Tải lại video',startEngine:'Mở MITOM Product Video trên máy này để bắt đầu.',ready:'Sẵn sàng tạo video.',loaded:'Ảnh đã sẵn sàng. Thêm prompt để tạo video.',uploading:'Đang nạp ảnh sản phẩm',backgroundUploading:'Đang nạp ảnh nền',submitting:'Đang bắt đầu video',queued:'Đang chờ đến lượt',planning:'Đang đọc prompt và chọn khung cảnh',preparing:'Đang chuẩn bị sản phẩm',rendering:'Đang dựng chuyển động',encoding:'Đang xuất MP4',working:'Đang tạo video',completed:'Video đã sẵn sàng.',cancelled:'Đã hủy tạo video.',failed:'Chưa tạo được video.',downloading:'Đang nạp video của bạn',resume:'Kết nối lại để kiểm tra video đang tạo.',downloadFailed:'Video đã hoàn tất. Kết nối lại rồi tải MP4.',newEngine:'Cần bản MITOM Product Video mới để tạo video listing.',engineNotReady:'Ứng dụng đang thiếu thành phần cần thiết. Mở MITOM Product Video để kiểm tra.',step:'Khung hình {step} / {total}',progress:'Đã xử lý {value}%',seconds:'giây',errors:{invalid_endpoint:'Dùng địa chỉ trên máy này, ví dụ http://127.0.0.1:4177.',invalid_token:'Dán mã kết nối hiện tại từ MITOM Product Video.',pairing_required:'Kết nối MITOM Product Video trước khi tạo.',unauthorized:'Mã kết nối đã thay đổi. Dùng mã hiện tại trong ứng dụng.',engine_unreachable:'Mở MITOM Product Video từ Launcher trên máy này rồi kết nối lại.',invalid_image:'Chọn ảnh PNG, JPG hoặc WebP, mỗi cạnh ít nhất 32 px, tối đa 15 MB và 24 megapixel.',unsupported_image:'Chọn ảnh PNG, JPG hoặc WebP.',image_too_large:'Chọn ảnh tối đa 15 MB.',invalid_prompt:'Nhập prompt từ 1–4000 ký tự.',invalid_options:'Chọn lại tỷ lệ, thời lượng hoặc độ phân giải được hỗ trợ.',invalid_id:'Ảnh hoặc video không thuộc phiên hiện tại. Chọn lại ảnh.',image_not_found:'Chọn lại ảnh sau khi khởi động lại ứng dụng.',background_not_found:'Chọn lại ảnh nền sau khi khởi động lại ứng dụng.',job_not_found:'Video này không còn trong phiên hiện tại.',queue_full:'Hàng đợi đã đầy. Chờ một video hoàn tất.',upload_limit:'Khởi động lại ứng dụng để bắt đầu phiên ảnh mới.',job_limit:'Khởi động lại ứng dụng để bắt đầu phiên video mới.',video_not_ready:'Video vẫn đang được tạo.',video_not_found:'Video không còn trong phiên hiện tại. Hãy tạo lại.',invalid_video:'Ứng dụng chưa trả về MP4 hoàn chỉnh.',video_too_large:'MP4 vượt giới hạn 100 MB.',memory_limit:'Video đã dừng vì giới hạn bộ nhớ. Thử 720p hoặc thời lượng ngắn hơn.',deadline_exceeded:'Đã hết thời gian dựng. Thử 720p hoặc thời lượng ngắn hơn.',timeout:'Ứng dụng chưa phản hồi. Kết nối lại để kiểm tra.',renderer_missing:'Chưa cài bộ dựng video. Mở MITOM Product Video trong Launcher để hoàn tất cài đặt.',render_failed:'Chưa dựng được video. Mở ứng dụng để kiểm tra rồi thử lại.',remotion_missing:'Chưa cài bộ dựng video. Mở MITOM Product Video trong Launcher để hoàn tất cài đặt.',planner_unavailable:'Bộ đọc prompt trên máy chưa sẵn sàng. Mở ứng dụng để kiểm tra.',gpu_unavailable:'Ứng dụng chưa có bộ tạo video GPU khả dụng trên máy này. Chọn CPU.',containment_failed:'Không khởi động được bộ dựng trên máy. Mở lại ứng dụng rồi thử lại.',origin_forbidden:'Mở trang video từ MITOM hoặc từ ứng dụng trên máy.',host_forbidden:'Dùng trang video trên máy này.',engine_error:'Ứng dụng gặp lỗi. Kết nối lại rồi thử lại.'}},
  en:{eyebrow:'ETSY LISTING VIDEO',title:'Let your product move.',lead:'Add a photo, describe your scene, and create a video on this computer.',product:'Product photo',remove:'Remove',choose:'Choose a product photo',drop:'Or drop / paste an image here',fileTypes:'PNG · JPG · WebP — up to 15 MB',prompt:'What should your video look like?',promptPlaceholder:'For example: The ornament gently sways and turns left, then right. A warm Christmas living room, golden lights, and snow outside the window. Keep the name and printed design clear.',promptHint:'Describe the movement, setting, and lighting.',aspect:'Aspect ratio',landscape:'Landscape',portrait:'Portrait',advanced:'More settings',duration:'Length',resolution:'Resolution',device:'Video device',background:'Your background image (optional)',backgroundHint:'Add your own background photo here when you want to use a specific setting.',generate:'Create product video',cancel:'Cancel video',stopping:'Stopping video…',privacy:'Images and video are processed on this computer.',connect:'Connect',reconnect:'Reconnect',connecting:'Connecting…',connectionOff:'Connect local app',connectionReady:'Connected · Local',connectionUnavailable:'App not ready',connectionTitle:'Connect MITOM Product Video',connectionDescription:'Open MITOM Product Video from Launcher on this computer, then enter its connection code.',openLocal:'Open the app running on this computer',engineAddress:'Local app address',connectionCode:'Connection code',connectionPrivacy:'The code stays in this session. It is never put in a URL or saved to the website.',outputEyebrow:'PREVIEW',outputTitle:'Your next take.',silent:'No audio',emptyTitle:'One product. A little story.',emptyBody:'A fitting scene, gentle movement, and your product at the center.',noVideo:'Your video will appear here.',download:'Download MP4',retryDownload:'Retry download',startEngine:'Open MITOM Product Video on this computer to begin.',ready:'Ready to create your video.',loaded:'Photo ready. Add a prompt to create your video.',uploading:'Opening product photo',backgroundUploading:'Opening background photo',submitting:'Starting video',queued:'Waiting in queue',planning:'Reading your prompt and setting the scene',preparing:'Preparing your product',rendering:'Rendering product motion',encoding:'Exporting MP4',working:'Creating video',completed:'Your video is ready.',cancelled:'Video cancelled.',failed:'Video not created.',downloading:'Loading your video',resume:'Reconnect to check this video.',downloadFailed:'Your video is complete. Reconnect and download the MP4.',newEngine:'Install the latest MITOM Product Video to create listing videos.',engineNotReady:'A required component is missing. Open MITOM Product Video to check setup.',step:'Frame {step} / {total}',progress:'{value}% processed',seconds:'seconds',errors:{invalid_endpoint:'Use a local address such as http://127.0.0.1:4177.',invalid_token:'Paste the current MITOM Product Video connection code.',pairing_required:'Connect MITOM Product Video first.',unauthorized:'The connection code changed. Use the current code from the app.',engine_unreachable:'Open MITOM Product Video from Launcher on this computer, then reconnect.',invalid_image:'Choose a PNG, JPG or WebP image, at least 32 px per side, up to 15 MB and 24 megapixels.',unsupported_image:'Choose a PNG, JPG or WebP image.',image_too_large:'Choose an image up to 15 MB.',invalid_prompt:'Enter a prompt of 1–4000 characters.',invalid_options:'Choose a supported aspect ratio, length, or resolution.',invalid_id:'The image or video is not part of this session. Choose your image again.',image_not_found:'Choose your image again after restarting the app.',background_not_found:'Choose your background again after restarting the app.',job_not_found:'This video is no longer in the current session.',queue_full:'The queue is full. Wait for a video to finish.',upload_limit:'Restart the app to start a new image session.',job_limit:'Restart the app to start a new video session.',video_not_ready:'Your video is still being created.',video_not_found:'This video is no longer available. Create it again.',invalid_video:'The app did not return a complete MP4.',video_too_large:'The MP4 exceeds 100 MB.',memory_limit:'Video stopped at the memory limit. Try 720p or a shorter video.',deadline_exceeded:'The render reached its time limit. Try 720p or a shorter video.',timeout:'The app did not respond. Reconnect to check its status.',renderer_missing:'The video renderer is not installed. Open MITOM Product Video in Launcher to finish setup.',render_failed:'Video could not be rendered. Check the app, then try again.',remotion_missing:'The video renderer is not installed. Open MITOM Product Video in Launcher to finish setup.',planner_unavailable:'The local prompt planner is not ready. Open the app to check setup.',gpu_unavailable:'No GPU video backend is available on this computer. Choose CPU.',containment_failed:'The local renderer could not start. Reopen the app, then try again.',origin_forbidden:'Open the video page from MITOM or the local app.',host_forbidden:'Use the video page on this computer.',engine_error:'The app returned an error. Reconnect and try again.'}}
};

let language = 'vi';
try { const saved = localStorage.getItem('mitom.productVideo.language'); if (saved === 'en' || saved === 'vi') language = saved; } catch {}
const state = {client:null,token:'',endpoint:DEFAULT_ENDPOINT,connected:false,enabled:false,health:null,caps:capabilities(null),connecting:false,file:null,background:null,imageUrl:null,videoUrl:null,job:null,activeId:null,busy:false,polling:false,cancelling:false,phase:null,notice:'startEngine',errorCode:null,downloadError:false};
const t = key => words[language][key] || words.en[key] || key;
const setText = (id, value) => { $(id).textContent = value; };
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
let imageSelection = 0;
Object.assign(words.vi,{copyCode:'Sao chép mã kết nối',codeCopied:'Đã sao chép mã',copyFailed:'Không sao chép được. Mở lại hộp kết nối và sao chép mã trong ô mã kết nối.'});
Object.assign(words.en,{copyCode:'Copy connection code',codeCopied:'Connection code copied',copyFailed:'Could not copy. Reopen the connection dialog and copy the code from its connection code field.'});
Object.assign(words.vi,{launchLocal:'Mở Product Video qua Launcher'});
Object.assign(words.en,{launchLocal:'Open Product Video with Launcher'});
Object.assign(words.vi,{needsSetup:'Chuẩn bị AI local lần đầu (~704 MB) khi bấm Tạo video.',model_setup:'Đang chuẩn bị AI local lần đầu',download:'Tải MP4',modelDownload:'Đang tải model AI về máy',background_ai:'Đang tạo nền bằng AI local',extracting:'Đang tách sản phẩm',downloadBytes:'Đã tải {done} / {total} MB'});
Object.assign(words.en,{needsSetup:'The first video sets up local AI (~704 MB).',model_setup:'Setting up local AI for the first time',modelDownload:'Downloading the local AI model',background_ai:'Creating a background with local AI',extracting:'Extracting your product',downloadBytes:'Downloaded {done} / {total} MB'});
Object.assign(words.vi.errors,{planner_missing:'Chưa cài model đọc ảnh và prompt. Mở MITOM Product Video để hoàn tất cài đặt.',cutout_model_missing:'Chưa cài model tách sản phẩm. Mở MITOM Product Video để hoàn tất cài đặt.',mask_model_missing:'Chưa cài model tách sản phẩm. Mở MITOM Product Video để hoàn tất cài đặt.'});
Object.assign(words.en.errors,{planner_missing:'The image and prompt model is not installed. Open MITOM Product Video to finish setup.',cutout_model_missing:'The product extraction model is not installed. Open MITOM Product Video to finish setup.',mask_model_missing:'The product extraction model is not installed. Open MITOM Product Video to finish setup.'});
Object.assign(words.vi.errors,{planner_failed:'AI chưa đọc được cảnh này. Thử prompt ngắn và ảnh sản phẩm rõ hơn.',planner_invalid_output:'AI chưa trả về cảnh hoàn chỉnh. Thử lại với mô tả ngắn hơn.',model_download_failed:'Chưa tải xong model AI. Kiểm tra mạng rồi bấm tạo lại; các tệp hoàn chỉnh được giữ lại.',model_checksum_failed:'Model tải về chưa qua kiểm tra. Bấm tạo lại để tải đúng tệp.',device_unavailable:'Bộ AI đã cài trên máy này hiện hỗ trợ CPU. Chọn CPU để tiếp tục.'});
Object.assign(words.en.errors,{planner_failed:'AI could not read this scene. Try a shorter prompt and a clear product photo.',planner_invalid_output:'AI did not return a complete scene. Try again with a shorter description.',model_download_failed:'The local model download did not finish. Check your connection and retry; complete files are kept.',model_checksum_failed:'The downloaded model did not pass verification. Retry to download the correct file.',device_unavailable:'The installed AI runtime supports CPU. Choose CPU to continue.'});

function errorCode(error) { return error?.name === 'TimeoutError' ? 'timeout' : error?.code || 'engine_error'; }
function errorText(error) { const code = typeof error === 'string' ? error : errorCode(error); return words[language].errors[code] || words.en.errors[code] || words[language].errors.engine_error; }
function phaseKey(phase) {
  if (phase === 'model_setup') return 'model_setup';
  if (/model.?download|^download$/.test(phase)) return 'modelDownload';
  if (phase === 'background_ai' || phase === 'extracting') return phase;
  if (['uploading','backgroundUploading','submitting','queued','planning','preparing','rendering','encoding','completed','cancelled','failed','downloading'].includes(phase)) return phase;
  if (/plan|prompt|scene|niche/.test(phase)) return 'planning';
  if (/cutout|extract|segment|background|prepare|load|start/.test(phase)) return 'preparing';
  if (/encode|save|export|mux/.test(phase)) return 'encoding';
  if (/render|frame|compose/.test(phase)) return 'rendering';
  return 'working';
}
function selectedOptions() { return {aspect_ratio:$('aspect').value,duration_seconds:Number($('duration').value),resolution:Number($('resolution').value),device:$('device').value}; }

function render() {
  document.documentElement.lang = language; $('language').value = language;
  document.querySelectorAll('[data-t]').forEach(node => { node.textContent = t(node.dataset.t); });
  document.querySelectorAll('[data-placeholder]').forEach(node => { node.placeholder = t(node.dataset.placeholder); });
  const connectedKey = state.connected ? state.enabled ? 'connectionReady' : 'connectionUnavailable' : 'connectionOff';
  setText('connectionLabel',t(connectedKey));
  $('connectionDot').className = `connection-dot${state.connected && state.enabled ? ' ready' : state.errorCode ? ' error' : ''}`;
  const locked = state.busy || Boolean(state.activeId);
  $('inputFields').disabled = locked;
  $('generate').disabled = locked || !state.connected || !state.enabled || !state.file || !$('prompt').value.trim();
  $('cancel').hidden = !state.activeId;
  $('cancel').disabled = !state.connected || state.cancelling;
  setText('cancel',t(state.cancelling ? 'stopping' : 'cancel'));
  $('connectionButton').disabled = state.connecting;
  $('connectSubmit').disabled = state.connecting;
  $('copyCode').hidden = !state.localToken;
  if (state.connecting) setText('connectSubmit',t('connecting'));
  $('notice').classList.toggle('error',Boolean(state.errorCode));
  setText('noticeText',state.errorCode ? errorText(state.errorCode) : t(state.busy && state.phase ? phaseKey(state.phase) : state.notice));
  $('noticeAction').hidden = state.connected && state.enabled;
  setText('noticeAction',t(state.activeId || state.connected ? 'reconnect' : 'connect'));
  setText('characterCount',`${$('prompt').value.length} / 4000`);
  setText('aspectBadge',$('aspect').value);
  $('previewStage').classList.toggle('is-portrait',$('aspect').value === '9:16');
  $('previewStage').classList.toggle('is-landscape',$('aspect').value !== '9:16');
  $('retryVideo').hidden = !state.downloadError;
  const done = state.videoUrl ? 'completed' : state.job?.status === 'cancelled' ? 'cancelled' : state.phase ? phaseKey(state.phase) : 'noVideo';
  setText('outputStatus',t(done));
  if (state.phase) setText('phaseLabel',t(state.cancelling ? 'stopping' : phaseKey(state.phase)));
  document.querySelectorAll('[data-aspect]').forEach(button => { button.setAttribute('aria-pressed',String(button.dataset.aspect === $('aspect').value)); button.disabled = locked || state.caps.available && !state.caps.aspect_ratios.includes(button.dataset.aspect); });
}

function applyCapabilities(health) {
  state.health = health; state.caps = capabilities(health); state.enabled = state.caps.enabled;
  const devices = state.caps.devices;
  const before = $('device').value;
  $('device').replaceChildren(...devices.map(device => { const option = document.createElement('option'); option.value = device; option.textContent = device.toUpperCase(); return option; }));
  $('device').value = devices.includes(before) ? before : devices.includes('cpu') ? 'cpu' : devices[0] || '';
  $('deviceField').hidden = devices.length < 2;
  for (const [id, allowed] of [['duration',state.caps.durations],['resolution',state.caps.resolutions]]) {
    const input = $(id);
    for (const option of input.options) option.disabled = state.caps.available && !allowed.includes(Number(option.value));
    if (state.caps.available && !allowed.includes(Number(input.value)) && allowed.length) input.value = String(allowed[0]);
  }
  if (state.caps.available && !state.caps.aspect_ratios.includes($('aspect').value) && state.caps.aspect_ratios.length) $('aspect').value = state.caps.aspect_ratios[0];
  state.notice = !state.caps.available ? 'newEngine' : state.caps.needs_setup && state.caps.can_setup ? 'needsSetup' : state.enabled ? 'ready' : 'engineNotReady';
}

function openConnection() { $('endpoint').value = state.endpoint; $('pairCode').value = state.token; $('connectionError').hidden = true; $('connectionDialog').showModal(); }
async function connect(endpoint, token, {automatic = false} = {}) {
  if (state.connecting) return;
  state.connecting = true; render();
  try {
    const client = new LocalVideoClient({endpoint,token});
    const health = await client.health();
    state.client = client; state.endpoint = client.endpoint; state.token = token;
    state.connected = true; state.errorCode = null; applyCapabilities(health);
    $('connectionDialog').close();
    $('pairCode').value = '';
    if (state.activeId && !state.polling) void pollJob(state.activeId);
  } catch (error) {
    state.connected = false; state.enabled = false; state.errorCode = errorCode(error);
    if (!automatic) { setText('connectionError',errorText(error)); $('connectionError').hidden = false; }
  } finally { state.connecting = false; render(); }
}

function validateImage(file) {
  if (!file || !['image/png','image/jpeg','image/webp'].includes(file.type) || !file.size || file.size > MAX_IMAGE_BYTES) throw {code:'invalid_image'};
}
async function acceptFile(file, background = false) {
  if (state.busy || state.activeId) return;
  let nextUrl;
  const selection = background ? null : ++imageSelection;
  try {
    validateImage(file); imageDimensions(await file.arrayBuffer(),file.type); nextUrl = URL.createObjectURL(file);
    const image = new Image(); image.decoding = 'async'; image.src = nextUrl; await image.decode();
    if (image.naturalWidth < 32 || image.naturalHeight < 32 || Math.max(image.naturalWidth,image.naturalHeight) > 12000 || image.naturalWidth * image.naturalHeight > 24000000) throw {code:'invalid_image'};
    if (state.busy || state.activeId || !background && selection !== imageSelection) return;
    if (background) { state.background = file; setText('backgroundName',file.name); $('backgroundSelection').hidden = false; }
    else {
      if (state.imageUrl) URL.revokeObjectURL(state.imageUrl);
      state.file = file; state.imageUrl = nextUrl; nextUrl = null;
      $('imagePreview').src = state.imageUrl; $('imagePreview').hidden = false;
      $('uploadPlaceholder').hidden = true; $('removeImage').hidden = false;
      setText('imageName',`${file.name} · ${image.naturalWidth} × ${image.naturalHeight}`); $('imageName').hidden = false;
      state.notice = 'loaded';
    }
    state.errorCode = null;
  } catch (error) { state.errorCode = errorCode(error); }
  finally { if (nextUrl) URL.revokeObjectURL(nextUrl); render(); }
}

function showProgress(phase, job = null) {
  state.phase = phase; $('progressPanel').hidden = false;
  const progress = job?.progress || {};
  const frame = Number(progress.rendered_frames ?? progress.frame ?? progress.step);
  const total = Number(progress.total_frames ?? progress.frames ?? progress.total_steps);
  const ratio = typeof progress === 'number' ? progress : Number(progress.ratio ?? progress.fraction ?? progress.value ?? job?.render_progress);
  const downloaded = Number(progress.downloaded_bytes ?? progress.bytes_downloaded ?? job?.downloaded_bytes ?? job?.bytes_downloaded);
  const downloadTotal = Number(progress.total_bytes ?? job?.total_bytes);
  if (phase === 'completed') { $('progress').max = 100; $('progress').value = 100; setText('stepLabel',''); }
  else if (Number.isFinite(downloaded) && Number.isFinite(downloadTotal) && downloaded >= 0 && downloadTotal > 0 && downloaded <= downloadTotal) { $('progress').max = downloadTotal; $('progress').value = downloaded; setText('stepLabel',t('downloadBytes').replace('{done}',(downloaded/1024**2).toFixed(1)).replace('{total}',(downloadTotal/1024**2).toFixed(1))); }
  else if (Number.isFinite(frame) && Number.isFinite(total) && total > 0 && frame >= 0) { $('progress').max = total; $('progress').value = Math.min(total,frame); setText('stepLabel',t('step').replace('{step}',Math.round(frame)).replace('{total}',Math.round(total))); }
  else if (Number.isFinite(ratio) && ratio >= 0 && ratio <= 1) { $('progress').max = 100; $('progress').value = Math.round(ratio * 100); setText('stepLabel',t('progress').replace('{value}',Math.round(ratio * 100))); }
  else { $('progress').removeAttribute('value'); setText('stepLabel',''); }
  const width = job?.export_resolution?.width ?? job?.width;
  const height = job?.export_resolution?.height ?? job?.height;
  const duration = job?.duration_seconds ?? job?.duration;
  setText('jobDetail',[Number.isFinite(width) && Number.isFinite(height) ? `${width} × ${height}` : '',Number.isFinite(duration) ? `${duration} ${t('seconds')}` : ''].filter(Boolean).join(' · '));
  render();
}
function updateJob(job) { state.job = job; state.errorCode = null; showProgress(job.phase || job.status,job); }
function clearVideo() { if (state.videoUrl) URL.revokeObjectURL(state.videoUrl); state.videoUrl = null; $('video').pause(); $('video').removeAttribute('src'); $('video').load(); $('video').hidden = true; $('download').hidden = true; $('download').removeAttribute('href'); $('emptyOutput').hidden = false; }
async function loadVideo(id) {
  state.downloadError = false; state.busy = true; showProgress('downloading');
  try {
    const blob = await state.client.video(id);
    if (state.videoUrl) URL.revokeObjectURL(state.videoUrl);
    state.videoUrl = URL.createObjectURL(blob); $('video').src = state.videoUrl; $('video').hidden = false; $('emptyOutput').hidden = true;
    $('download').href = state.videoUrl; $('download').download = `MITOM-Product-Video-${state.job?.options?.aspect_ratio === '9:16' ? '9x16' : '16x9'}-${new Date().toISOString().replace(/[:.]/g,'-')}.mp4`; $('download').hidden = false;
    state.errorCode = null; state.notice = 'completed'; showProgress('completed',state.job);
  } catch (error) { state.downloadError = true; state.errorCode = errorCode(error); state.notice = 'downloadFailed'; if (['engine_unreachable','unauthorized'].includes(state.errorCode)) { state.connected = false; state.enabled = false; } }
  finally { state.busy = false; render(); }
}
async function pollJob(id) {
  if (state.polling) return;
  state.polling = true; state.busy = true;
  let failures = 0;
  try {
    while (state.activeId === id) {
      let job;
      try { job = await state.client.job(id); failures = 0; }
      catch (error) { if (++failures < 3 && ['engine_unreachable','timeout'].includes(errorCode(error))) { await delay(1400); continue; } throw error; }
      updateJob(job);
      if (['completed','failed','cancelled'].includes(job.status)) {
        state.activeId = null; state.cancelling = false; state.busy = false;
        if (job.status === 'completed') await loadVideo(id);
        else { state.notice = job.status; state.phase = job.status; state.errorCode = job.status === 'failed' ? job.error?.code || 'render_failed' : null; $('progressPanel').hidden = true; }
        break;
      }
      await delay(800);
    }
  } catch (error) {
    state.errorCode = errorCode(error);
    if (state.errorCode === 'job_not_found') { state.activeId = null; state.cancelling = false; state.phase = 'failed'; $('progressPanel').hidden = true; }
    else { state.connected = false; state.enabled = false; state.notice = 'resume'; }
  } finally { state.polling = false; state.busy = false; render(); }
}

$('videoForm').addEventListener('submit',async event => {
  event.preventDefault(); if ($('generate').disabled) return;
  state.busy = true; state.errorCode = null; state.downloadError = false; state.job = null; clearVideo(); showProgress('uploading');
  const options = selectedOptions();
  try {
    const image = await state.client.upload(state.file);
    let background;
    if (state.background) { showProgress('backgroundUploading'); background = await state.client.upload(state.background); }
    showProgress('submitting');
    const job = await state.client.createProductJob({image_id:image.image_id,background_id:background?.image_id,prompt:$('prompt').value,options});
    state.activeId = job.job_id; updateJob(job); await pollJob(job.job_id);
  } catch (error) { state.busy = false; state.phase = 'failed'; $('progressPanel').hidden = true; state.errorCode = errorCode(error); if (['engine_unreachable','unauthorized'].includes(state.errorCode)) { state.connected = false; state.enabled = false; } render(); }
});
$('cancel').addEventListener('click',async () => {
  if (!state.activeId || state.cancelling || !state.connected) return;
  state.cancelling = true; render();
  try { updateJob(await state.client.cancel(state.activeId)); }
  catch (error) { state.cancelling = false; state.errorCode = errorCode(error); render(); }
});
$('retryVideo').addEventListener('click',() => { if (state.job?.status === 'completed' && state.connected && !state.busy) void loadVideo(state.job.job_id); });
$('connectionButton').addEventListener('click',openConnection);
$('noticeAction').addEventListener('click',openConnection);
$('closeConnection').addEventListener('click',() => $('connectionDialog').close());
$('copyCode').addEventListener('click',async () => { if (!state.localToken) return; try { await navigator.clipboard.writeText(state.localToken); setText('copyCode',t('codeCopied')); } catch { setText('connectionError',t('copyFailed')); $('connectionError').hidden = false; } });
$('connectionForm').addEventListener('submit',event => { event.preventDefault(); void connect($('endpoint').value.trim(),$('pairCode').value.trim()); });
$('imageInput').addEventListener('change',event => { if (event.target.files[0]) void acceptFile(event.target.files[0]); });
$('backgroundInput').addEventListener('change',event => { if (event.target.files[0]) void acceptFile(event.target.files[0],true); });
$('removeImage').addEventListener('click',() => { ++imageSelection; if (state.imageUrl) URL.revokeObjectURL(state.imageUrl); state.imageUrl = null; state.file = null; $('imagePreview').hidden = true; $('imagePreview').removeAttribute('src'); $('imageInput').value = ''; $('uploadPlaceholder').hidden = false; $('removeImage').hidden = true; $('imageName').hidden = true; render(); });
$('removeBackground').addEventListener('click',() => { state.background = null; $('backgroundInput').value = ''; $('backgroundSelection').hidden = true; render(); });
$('prompt').addEventListener('input',render);
document.querySelectorAll('[data-aspect]').forEach(button => button.addEventListener('click',() => { if (!button.disabled) { $('aspect').value = button.dataset.aspect; render(); } }));
$('language').addEventListener('change',() => { language = $('language').value === 'en' ? 'en' : 'vi'; try { localStorage.setItem('mitom.productVideo.language',language); } catch {} render(); });
['dragenter','dragover'].forEach(name => $('dropzone').addEventListener(name,event => { event.preventDefault(); if (!state.busy && !state.activeId) $('dropzone').classList.add('dragging'); }));
['dragleave','drop'].forEach(name => $('dropzone').addEventListener(name,event => { event.preventDefault(); $('dropzone').classList.remove('dragging'); if (name === 'drop' && event.dataTransfer.files[0]) void acceptFile(event.dataTransfer.files[0]); }));
document.addEventListener('paste',event => { if (event.target.closest('textarea,input,select') || state.busy || state.activeId) return; const item = [...(event.clipboardData?.items || [])].find(item => item.kind === 'file' && /^image\/(png|jpeg|webp)$/.test(item.type)); const file = item?.getAsFile(); if (file) { event.preventDefault(); void acceptFile(file); } });
window.addEventListener('pagehide',() => { if (state.imageUrl) URL.revokeObjectURL(state.imageUrl); if (state.videoUrl) URL.revokeObjectURL(state.videoUrl); state.token = ''; });

render();
const injected = document.querySelector('meta[name="mitom-session-token"]');
const token = injected?.content.trim() || '';
if (injected) injected.content = '';
if (token && location.protocol === 'http:' && ['127.0.0.1','localhost'].includes(location.hostname)) { state.localToken = token; void connect(location.origin,token,{automatic:true}); }
else { try { await new LocalVideoClient().discover(); } catch {} }
