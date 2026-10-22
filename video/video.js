import {buildVideoPayload, requestVideo, validateEndpoint} from './client.mjs?v=20261022-1';

const copy = {
  en:{title:'Product Video',create:'MITOM / VIDEO',photo:'Product image',file:'Upload image',url:'Image URL',choose:'Choose a product photo',prompt:'Prompt',placeholder:'Describe the scene, lighting, product movement and camera angle…',ratio:'Aspect ratio',landscape:'Landscape · 16:9',portrait:'Portrait · 9:16',generate:'Generate video',check:'Check status',stop:'Stop checking',connection:'Connection settings',notConnected:'Connection required',configured:'n8n configured',generateURL:'Generate webhook URL',statusURL:'Status webhook URL',token:'Webhook token',tokenHelp:'X-MITOM-Token · kept only while this page is open. Your Kie.ai key stays in n8n.',save:'Save connection',setup:'Setup guide',workflow:'Download n8n workflow',guide:'Import the workflow into your n8n, connect Kie.ai, then paste both production webhook URLs here.',ready:'Add your prompt and an optional product photo.',empty:'Your product. In motion.',emptyBody:'Write a prompt to start your next product video.',preview:'PRODUCT VIDEO',engine:'VEO 3.1 · n8n',download:'Open / download MP4 ↗',connectFirst:'Set up your n8n connection before generating.',saved:'Connection saved on this browser. Token is kept for this session only.',sending:'Submitting to n8n…',working:'Video is generating. You can return to this page to check it.',complete:'Video ready. Open the MP4 to save it.',network:'Could not reach n8n. Check that it is running, authentication, CORS and browser local-network access. Check status before submitting again.',stopped:'Automatic checks stopped. The generation continues in n8n.',resume:'A previous task is available. Use Check status.',failedTask:'The previous task failed. You can start a new video.',task:'Task',fileError:'Choose a PNG, JPG or WebP image up to 5 MB.',remove:'Remove image',timeout:'Still processing. Use Check status later.',pending:'Wait for the current task, or check its status before starting another.',urlLabel:'Public HTTPS image URL',powered:'Runs through your n8n connection · Veo generation uses Kie.ai cloud credits.'},
  vi:{title:'Video sản phẩm',create:'MITOM / VIDEO',photo:'Ảnh sản phẩm',file:'Tải ảnh lên',url:'Link ảnh',choose:'Chọn ảnh sản phẩm',prompt:'Prompt',placeholder:'Mô tả khung cảnh, ánh sáng, chuyển động sản phẩm và góc quay…',ratio:'Tỷ lệ khung hình',landscape:'Ngang · 16:9',portrait:'Dọc · 9:16',generate:'Tạo video',check:'Kiểm tra trạng thái',stop:'Dừng kiểm tra',connection:'Cài đặt kết nối',notConnected:'Cần kết nối',configured:'Đã cấu hình n8n',generateURL:'URL webhook tạo video',statusURL:'URL webhook trạng thái',token:'Mã kết nối webhook',tokenHelp:'X-MITOM-Token · chỉ giữ khi trang đang mở. Khóa Kie.ai được lưu trong n8n.',save:'Lưu kết nối',setup:'Hướng dẫn cài đặt',workflow:'Tải workflow n8n',guide:'Import workflow vào n8n, kết nối Kie.ai rồi dán hai URL webhook production vào đây.',ready:'Nhập prompt và chọn ảnh sản phẩm nếu có.',empty:'Sản phẩm của bạn. Trong chuyển động.',emptyBody:'Nhập prompt để bắt đầu video sản phẩm.',preview:'VIDEO SẢN PHẨM',engine:'VEO 3.1 · n8n',download:'Mở / tải MP4 ↗',connectFirst:'Cấu hình kết nối n8n trước khi tạo video.',saved:'Đã lưu kết nối trên trình duyệt này. Mã kết nối chỉ giữ trong phiên hiện tại.',sending:'Đang gửi tới n8n…',working:'Video đang được tạo. Bạn có thể quay lại trang này để kiểm tra.',complete:'Video đã sẵn sàng. Mở MP4 để lưu về.',network:'Không kết nối được n8n. Kiểm tra n8n đang chạy, mã kết nối, CORS và quyền mạng cục bộ của trình duyệt. Kiểm tra trạng thái trước khi gửi lại.',stopped:'Đã dừng kiểm tra tự động. Video vẫn tiếp tục tạo trong n8n.',resume:'Có tác vụ trước đó. Bấm Kiểm tra trạng thái.',failedTask:'Tác vụ trước đã lỗi. Bạn có thể tạo video mới.',task:'Tác vụ',fileError:'Chọn ảnh PNG, JPG hoặc WebP tối đa 5 MB.',remove:'Bỏ ảnh',timeout:'Vẫn đang xử lý. Hãy kiểm tra trạng thái sau.',pending:'Chờ tác vụ hiện tại hoặc kiểm tra trạng thái trước khi tạo thêm.',urlLabel:'Link ảnh HTTPS công khai',powered:'Chạy qua kết nối n8n của bạn · Veo sử dụng credit Kie.ai trên đám mây.'}
};
const $ = id => document.getElementById(id);
const tr = key => copy[document.documentElement.lang === 'vi' ? 'vi' : 'en'][key];
const app = document.createElement('main');
app.id = 'video-app'; app.className = 'video-app'; app.hidden = true; app.setAttribute('aria-label','MITOM Product Video');
app.innerHTML = `<aside class="video-sidebar"><div class="video-eyebrow" data-v="create"></div><h1 data-v="title"></h1><div id="video-connection" class="video-connection"></div>
<form id="video-form"><label class="video-field"><span data-v="prompt"></span><textarea id="video-prompt" maxlength="5000" required data-v-placeholder="placeholder"></textarea></label>
<div class="video-source" role="group" aria-label="Image source"><button type="button" id="video-file-mode" aria-pressed="true" data-v="file"></button><button type="button" id="video-url-mode" aria-pressed="false" data-v="url"></button></div>
<label id="video-file-field" class="video-file"><span data-v="choose"></span><input id="video-file" type="file" accept="image/png,image/jpeg,image/webp"></label>
<label id="video-url-field" class="video-field" hidden><span data-v="urlLabel"></span><input id="video-image-url" type="url" placeholder="https://…"></label>
<button type="button" id="video-remove" class="button" data-v="remove" hidden></button>
<label class="video-field"><span data-v="ratio"></span><select id="video-ratio"><option value="16:9" data-v="landscape"></option><option value="9:16" data-v="portrait"></option></select></label>
<button id="video-generate" class="button button-dark" type="submit" data-v="generate"></button><button id="video-check" class="button" type="button" data-v="check" hidden></button><button id="video-stop" class="button" type="button" data-v="stop" hidden></button><p id="video-task" class="video-task" hidden></p></form>
<details id="video-settings" class="video-settings"><summary data-v="connection"></summary><p data-v="guide"></p><form id="video-settings-form"><label class="video-field"><span data-v="generateURL"></span><input id="video-generate-url" type="url" required placeholder="http://localhost:5678/webhook/mitom-video-generate"></label><label class="video-field"><span data-v="statusURL"></span><input id="video-status-url" type="url" required placeholder="http://localhost:5678/webhook/mitom-video-status"></label><label class="video-field"><span data-v="token"></span><input id="video-token" type="password" autocomplete="off" spellcheck="false"></label><p data-v="tokenHelp"></p><button class="button" type="submit" data-v="save"></button></form><div class="video-settings-links"><a href="video/SETUP.md" target="_blank" rel="noopener" data-v="setup"></a><a href="video/workflows/MITOM-Product-Video.json" download data-v="workflow"></a><a href="https://github.com/Alex-safari/AI-Product-Video-Generator-Using-Veo-3.1-n8n-" target="_blank" rel="noopener">Source ↗</a></div><p data-v="powered"></p></details></aside>
<section class="video-workspace"><div class="video-toolbar"><span data-v="preview"></span><span data-v="engine"></span></div><div class="video-stage"><div id="video-empty" class="video-empty"><img id="video-image-preview" class="video-image-preview" alt="Product reference" hidden><svg id="video-empty-icon" viewBox="0 0 80 80" aria-hidden="true"><rect x="6" y="14" width="68" height="52" rx="4"/><path d="m33 28 20 12-20 12Z"/><path d="M16 14v52m48-52v52M6 25h10M6 40h10M6 55h10m48-30h10M64 40h10M64 55h10"/></svg><h2 data-v="empty"></h2><p data-v="emptyBody"></p></div><div id="video-result" class="video-result" hidden><video id="video-player" class="video-preview" controls playsinline preload="metadata"></video><br><a id="video-download" class="button button-dark" target="_blank" rel="noopener" data-v="download"></a></div></div><div id="video-status" class="video-status" role="status" aria-live="polite"></div></section>`;
$('main').before(app);
let imageMode='file', imageData='', previewURL='', task=null, submitting=false, polling=false, pollTimer=null, pollController=null, submissionController=null, statusKey='ready';
let connection={generate:'',status:''};
try { connection=JSON.parse(localStorage.getItem('mitom-video-connection')||'null')||connection; task=JSON.parse(localStorage.getItem('mitom-video-task')||'null'); } catch (_) {}
if (!task?.taskId || !task?.endpoint) task=null;
if(task)statusKey=task.failed?'failedTask':'resume';
$('video-generate-url').value=connection.generate||''; $('video-status-url').value=connection.status||'';
function showStatus(key,error=false,raw=''){statusKey=key;$('video-status').textContent=raw||tr(key);$('video-status').classList.toggle('is-error',error);}
function update(){
  app.querySelectorAll('[data-v]').forEach(node=>node.textContent=tr(node.dataset.v));
  app.querySelectorAll('[data-v-placeholder]').forEach(node=>node.placeholder=tr(node.dataset.vPlaceholder));
  $('video-connection').textContent=tr(connection.generate&&connection.status?'configured':'notConnected');
  $('video-connection').classList.toggle('is-configured',Boolean(connection.generate&&connection.status));
  $('video-generate').disabled=submitting||Boolean(task&&!task.done);
  $('video-check').hidden=!task||task.done;$('video-check').disabled=submitting||polling;
  $('video-stop').hidden=!polling;$('video-task').hidden=!task;
  $('video-task').textContent=task?`${tr('task')}: ${task.taskId}`:'';
  $('video-settings-form').querySelectorAll('input,button').forEach(node=>node.disabled=submitting||polling);
  if(statusKey)$('video-status').textContent=tr(statusKey);
}
function saveTask(){try {if(task)localStorage.setItem('mitom-video-task',JSON.stringify(task));else localStorage.removeItem('mitom-video-task');}catch(_) {}}
function stopPolling(){clearTimeout(pollTimer);pollTimer=null;polling=false;pollController?.abort();pollController=null;update();}
function showResult(result){
  stopPolling();task={...task,taskId:result.taskId||task?.taskId||'',done:true,videoUrl:result.videoUrl};saveTask();
  $('video-player').src=result.videoUrl;$('video-download').href=result.videoUrl;$('video-result').hidden=false;$('video-empty').hidden=true;showStatus('complete');update();
}
function fail(error){stopPolling();if(error?.terminal&&task){task={...task,done:true,failed:true};saveTask();}showStatus('',true,error instanceof TypeError?tr('network'):error.message);update();}
function clearResult(){const player=$('video-player');player.pause();player.removeAttribute('src');player.load();$('video-download').removeAttribute('href');$('video-result').hidden=true;$('video-empty').hidden=false;}
async function checkStatus(auto=false,attempt=0){
  if(!task||task.done)return;
  if(!auto)stopPolling();polling=true;const requestController=new AbortController();pollController=requestController;update();
  const timeout=setTimeout(()=>requestController.abort(),60000);
  try{
    const result=await requestVideo(task.endpoint,{taskId:task.taskId},$('video-token').value,{signal:requestController.signal});
    if(requestController.signal.aborted||pollController!==requestController)return;
    if(result.state==='success'){showResult(result);return;}
    showStatus('working');
    if(app.hidden){stopPolling();showStatus('working');}
    else if(attempt<90)pollTimer=setTimeout(()=>checkStatus(true,attempt+1),10000);
    else {stopPolling();showStatus('timeout');}
  }catch(error){if(pollController!==requestController)return;if(error.name!=='AbortError')fail(error);else if(polling){stopPolling();showStatus('network',true);}}
  finally{clearTimeout(timeout);}
}
function sourceMode(mode){imageMode=mode;$('video-file-mode').setAttribute('aria-pressed',String(mode==='file'));$('video-url-mode').setAttribute('aria-pressed',String(mode==='url'));$('video-file-field').hidden=mode!=='file';$('video-url-field').hidden=mode!=='url';$('video-image-url').disabled=mode!=='url';clearImage();}
function clearImage(){if(previewURL)URL.revokeObjectURL(previewURL);previewURL='';imageData='';$('video-file').value='';$('video-image-preview').removeAttribute('src');$('video-image-preview').hidden=true;$('video-empty-icon').hidden=false;$('video-remove').hidden=true;}
$('video-file-mode').addEventListener('click',()=>sourceMode('file'));
$('video-url-mode').addEventListener('click',()=>sourceMode('url'));
$('video-remove').addEventListener('click',()=>{clearImage();$('video-image-url').value='';});
$('video-file').addEventListener('change',async()=>{
  const file=$('video-file').files[0];if(!file){clearImage();return;}
  if(!['image/png','image/jpeg','image/webp'].includes(file.type)||file.size>5*1024*1024){clearImage();showStatus('fileError',true);return;}
  const reader=new FileReader();reader.onload=()=>{if($('video-file').files[0]!==file)return;imageData=String(reader.result);if(previewURL)URL.revokeObjectURL(previewURL);previewURL=URL.createObjectURL(file);$('video-image-preview').src=previewURL;$('video-image-preview').hidden=false;$('video-empty-icon').hidden=true;$('video-remove').hidden=false;};reader.onerror=()=>showStatus('fileError',true);reader.readAsDataURL(file);
});
$('video-settings-form').addEventListener('submit',event=>{
  event.preventDefault();try{const next={generate:validateEndpoint($('video-generate-url').value),status:validateEndpoint($('video-status-url').value)};connection=next;try{localStorage.setItem('mitom-video-connection',JSON.stringify(next));}catch(_){}showStatus('saved');update();}catch(error){showStatus('',true,error.message);}
});
$('video-form').addEventListener('submit',async event=>{
  event.preventDefault();if(submitting||task&&!task.done){showStatus('pending',true);return;}
  if(!connection.generate||!connection.status){$('video-settings').open=true;$('video-generate-url').focus();showStatus('connectFirst',true);return;}
  let payload;try{payload=buildVideoPayload({prompt:$('video-prompt').value,aspectRatio:$('video-ratio').value,imageUrl:imageMode==='url'?$('video-image-url').value:'',imageBase64:imageMode==='file'?imageData:'',imageName:$('video-file').files[0]?.name});if(imageMode==='file'&&$('video-file').files[0]&&!imageData)throw Error(tr('fileError'));}catch(error){showStatus('',true,error.message);return;}
  submitting=true;stopPolling();task=null;saveTask();clearResult();showStatus('sending');update();const requestController=new AbortController();submissionController=requestController;const timeout=setTimeout(()=>requestController.abort(),120000);
  try{
    const result=await requestVideo(connection.generate,payload,$('video-token').value,{signal:requestController.signal});
    task={taskId:result.taskId,endpoint:connection.status,done:false};saveTask();
    if(result.state==='success')showResult(result);else{showStatus('working');submitting=false;if(!app.hidden)await checkStatus();}
  }catch(error){if(error.name==='AbortError'){showStatus('network',true);}else fail(error);}
  finally{clearTimeout(timeout);if(submissionController===requestController)submissionController=null;submitting=false;update();}
});
$('video-check').addEventListener('click',()=>checkStatus());
$('video-stop').addEventListener('click',()=>{stopPolling();showStatus('stopped');});
function route(){const open=/^#video(?:\/.*)?$/.test(location.hash);app.hidden=!open;document.body.classList.toggle('video-open',open);document.querySelectorAll('.nav-video').forEach(node=>{if(open)node.setAttribute('aria-current','page');else node.removeAttribute('aria-current');});if(open){$('main').hidden=true;$('tools-app').hidden=true;}else{stopPolling();$('video-player').pause();}}
window.addEventListener('hashchange',route);
window.addEventListener('pagehide',event=>{if(!event.persisted){stopPolling();clearImage();$('video-token').value='';}});
new MutationObserver(update).observe(document.documentElement,{attributes:true,attributeFilter:['lang']});
if(task?.done&&task.videoUrl){try{const url=new URL(task.videoUrl);if(url.protocol==='https:')showResult({taskId:task.taskId,videoUrl:url.href});}catch(_){task=null;saveTask();}}
$('video-image-url').disabled=true;update();route();
