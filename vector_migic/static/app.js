'use strict';
const $=id=>document.getElementById(id);
const words={en:{local:'LOCAL · CPU',input:'INPUT',open:'Open an image',drop:'or drop files / paste an image',traceSettings:'TRACE SETTINGS',reset:'Reset',preset:'Image type',logo:'Logo & lettering',illustration:'Illustration',photo:'Photograph',colorMode:'Color mode',color:'Color',bw:'Black & white',colors:'Colors',auto:'Auto',smoothing:'Curve smoothing',speckle:'Remove small regions',transparency:'Preserve transparent areas',advanced:'Advanced settings',curveMode:'Path style',smooth:'Smooth curves',polygon:'Straight segments',pixel:'Pixel outlines',resolution:'Processing size',corners:'Corner threshold',precision:'Color precision',threshold:'Black & white threshold',vectorize:'Vectorize image',batch:'Trace all',cancel:'Cancel',privacy:'Your images stay on this computer.',original:'Original',vector:'Vector',compare:'Compare',fit:'Fit',tagline:'FROM PIXELS TO POSSIBILITIES',emptyTitle:'Small pixels.\nEndless possibilities.',emptyText:'Open a bitmap. Make a real vector.',choose:'Choose an image ↗',dropHere:'Drop your images here',ready:'Ready when you are.',copySvg:'Copy SVG',result:'RESULT',readyExport:'Ready for\nyour next idea.',paths:'Paths',size:'SVG size',time:'Trace time',svgInfo:'Scalable vector graphics',pdfInfo:'Vector document',epsInfo:'Vector artwork',batchFormat:'Batch format',downloadAll:'Download all (.zip)',engine:'Potrace · VTracer',inputHint:'PNG, JPG, WebP, BMP, TIFF · up to 32 MB',uploading:'Opening image…',loaded:'Image ready. Choose your settings and vectorize.',converting:'Tracing image…',done:'Vector ready. Compare or export your result.',cancelled:'Conversion cancelled.',exporting:'Preparing vector export…',downloaded:'Vector exported.',changed:'Settings changed. Vectorize to update the result.',copied:'SVG copied to clipboard.',closed:'Vector Migic is closed. You can close this window.',session:'Reopen Vector Migic from the launcher to reconnect.',queued:'Queued',running:'Tracing',failed:'Failed',complete:'Ready',idle:'Image',batchReady:'Batch vectors exported.',copyError:'Clipboard is unavailable. Export SVG instead.'},vi:{local:'LOCAL · CPU',input:'ẢNH NGUỒN',open:'Mở ảnh',drop:'hoặc kéo thả / dán ảnh',traceSettings:'THIẾT LẬP VECTOR',reset:'Đặt lại',preset:'Loại ảnh',logo:'Logo & chữ',illustration:'Hình minh họa',photo:'Ảnh chụp',colorMode:'Chế độ màu',color:'Có màu',bw:'Đen trắng',colors:'Số màu',auto:'Tự động',smoothing:'Độ mịn đường cong',speckle:'Bỏ vùng nhỏ',transparency:'Giữ vùng trong suốt',advanced:'Thiết lập nâng cao',curveMode:'Kiểu đường',smooth:'Đường cong mịn',polygon:'Đoạn thẳng',pixel:'Viền pixel',resolution:'Kích thước xử lý',corners:'Ngưỡng góc',precision:'Độ chính xác màu',threshold:'Ngưỡng đen trắng',vectorize:'Chuyển sang vector',batch:'Chuyển tất cả',cancel:'Hủy',privacy:'Ảnh chỉ được xử lý trên máy này.',original:'Ảnh gốc',vector:'Vector',compare:'So sánh',fit:'Vừa khung',tagline:'TỪ PIXEL ĐẾN SÁNG TẠO',emptyTitle:'Từ ảnh nhỏ.\nĐến mọi kích thước.',emptyText:'Mở ảnh bitmap. Tạo vector thực.',choose:'Chọn ảnh ↗',dropHere:'Thả ảnh vào đây',ready:'Sẵn sàng.',copySvg:'Sao chép SVG',result:'KẾT QUẢ',readyExport:'Sẵn sàng cho\ný tưởng tiếp theo.',paths:'Đường vector',size:'Dung lượng SVG',time:'Thời gian',svgInfo:'Đồ họa vector',pdfInfo:'Tài liệu vector',epsInfo:'Artwork vector',batchFormat:'Định dạng hàng loạt',downloadAll:'Tải tất cả (.zip)',engine:'Potrace · VTracer',inputHint:'PNG, JPG, WebP, BMP, TIFF · tối đa 32 MB',uploading:'Đang mở ảnh…',loaded:'Đã mở ảnh. Chọn thiết lập rồi chuyển vector.',converting:'Đang chuyển vector…',done:'Vector đã sẵn sàng. So sánh hoặc xuất kết quả.',cancelled:'Đã hủy chuyển đổi.',exporting:'Đang chuẩn bị file vector…',downloaded:'Đã xuất vector.',changed:'Thiết lập đã đổi. Chuyển lại để cập nhật kết quả.',copied:'Đã sao chép mã SVG.',closed:'Vector Migic đã đóng. Bạn có thể đóng cửa sổ này.',session:'Mở lại Vector Migic từ launcher để kết nối.',queued:'Đang chờ',running:'Đang chạy',failed:'Lỗi',complete:'Hoàn tất',idle:'Ảnh',batchReady:'Đã xuất các vector.',copyError:'Không truy cập được clipboard. Hãy xuất SVG.'}};
const fragment=new URLSearchParams(location.hash.slice(1));
let token=fragment.get('token')||sessionStorage.getItem('migic.token')||'';
if(token){sessionStorage.setItem('migic.token',token);history.replaceState(null,'',location.pathname);}
const state={images:[],selected:null,view:'original',zoom:1,panX:0,panY:0,drag:null,lang:localStorage.getItem('migic.language')==='vi'?'vi':'en',exporting:false,sourceUrl:null,vectorUrl:null,sourceReady:null,vectorReady:null,selectionEpoch:0};
let presets={};
const t=key=>words[state.lang][key]||key;
const current=()=>state.images.find(image=>image.id===state.selected);
const doneImages=()=>state.images.filter(image=>image.job?.status==='done');
function status(message,error=false,busy=false){$('status').textContent=message;$('status-dot').className='status-dot'+(error?' error':busy?' busy':'');}
async function api(path,body,extra={}){const headers={'X-Migic-Token':token,...extra.headers};const init={...extra,headers};if(body!==undefined){init.method='POST';init.headers['Content-Type']='application/json';init.body=JSON.stringify(body);}const response=await fetch(path,init);if(!response.ok){let text;try{text=(await response.json()).error;}catch(_){text=response.statusText;}throw Error(text||'Request failed');}return response;}
async function json(path,body,extra){return(await api(path,body,extra)).json();}
function translate(){document.documentElement.lang=state.lang;$('language').value=state.lang;document.querySelectorAll('[data-i18n]').forEach(el=>el.textContent=t(el.dataset.i18n));renderFiles();renderControls();}
function labelStatus(image){return image.job?({queued:'queued',running:'running',done:'complete',error:'failed',cancelled:'cancelled'}[image.job.status]||'idle'):'idle';}
function renderFiles(){const host=$('files');host.replaceChildren();for(const image of state.images){const button=document.createElement('button');button.className='file-row'+(image.id===state.selected?' active':'');button.title=image.name;const name=document.createElement('span');name.textContent=image.name;const value=document.createElement('small');value.textContent=t(labelStatus(image));button.append(name,value);button.addEventListener('click',()=>selectImage(image.id).catch(fail));host.append(button);}}
function previewReady(image=current()){
 return image?.job?.status==='done'&&state.sourceReady===image.id&&state.vectorReady===image.job.id;
}
function resultStale(image=current()){
 if(image?.job?.status!=='done')return false;
 const generated=image.job.options||image.traceOptions;
 return !!generated&&Object.entries(options()).some(([key,value])=>generated[key]!==value);
}
function staleMessage(){return state.lang==='vi'?'Thiết lập đã đổi. Ảnh xem trước và file xuất vẫn là kết quả lần chạy trước. Chuyển lại để áp dụng.':'Settings changed. Preview and exports show the last completed result. Vectorize again to apply them.';}
function renderControls(){const image=current(),job=image?.job,active=image?.submitting||(job&&['queued','running'].includes(job.status)),done=job?.status==='done';$('vectorize').disabled=!image||active||state.exporting;$('batch').disabled=state.images.length<2||state.exporting||state.images.some(i=>i.submitting||['queued','running'].includes(i.job?.status));$('cancel').disabled=!state.images.some(i=>['queued','running'].includes(i.job?.status));document.querySelectorAll('[data-export]').forEach(button=>button.disabled=!done||state.exporting);$('copy-svg').disabled=!done||state.exporting;document.querySelectorAll('[data-view]').forEach(button=>{button.disabled=button.dataset.view!=='original'&&!previewReady(image);button.classList.toggle('active',button.dataset.view===state.view);});$('batch-export').hidden=doneImages().length<2;$('download-all').disabled=state.exporting;if(active){$('progress').hidden=false;$('progress').value=job?.progress||0;}else $('progress').hidden=true;if(done){const m=job.metrics;$('stat-paths').textContent=Number(m.path_count).toLocaleString();$('stat-colors').textContent=m.color_count;$('stat-size').textContent=(m.output_bytes/1024).toFixed(1)+' KB';$('stat-time').textContent=Number(m.elapsed_seconds).toFixed(2)+' s';$('warnings').textContent=[resultStale(image)?staleMessage():'',...(m.warnings||[])].filter(Boolean).join(' ');}else{for(const id of ['stat-paths','stat-colors','stat-size','stat-time'])$(id).textContent='—';$('warnings').textContent='';}document.querySelector('.result-panel').dataset.resultState=resultStale(image)?'stale':done?'ready':'empty';$('vectorize').querySelector('[data-i18n]').textContent=resultStale(image)?(state.lang==='vi'?'Cập nhật vector':'Update vector'):t('vectorize');applyView();}
function applyView(){const ready=previewReady();$('source-image').style.visibility=state.view==='vector'&&ready?'hidden':'visible';$('vector-layer').hidden=!ready||state.view==='original';const compare=ready&&state.view==='compare';$('compare-slider').hidden=$('divider').hidden=!compare;const box=$('viewport').getBoundingClientRect(),layer=$('stage').getBoundingClientRect();const split=layer.width?Math.max(0,Math.min(100,100*(box.left+box.width*Number($('compare-slider').value)/100-layer.left)/layer.width)):50;$('vector-layer').style.clipPath=compare?`inset(0 ${100-split}% 0 0)`:'none';$('divider').style.left=$('compare-slider').value+'%';}
function layoutStage(){
 const image=current();if(!image)return;
 const width=Number(image.info.width),height=Number(image.info.height),box=$('viewport');
 const scale=Math.min(Math.max(1,box.clientWidth-56)/width,Math.max(1,box.clientHeight-56)/height);
 // Both previews use this single source-canvas rectangle. A 1600px raster
 // thumbnail must not independently letterbox against the full-size SVG.
 const frameWidth=width*scale,frameHeight=height*scale,stage=$('stage');
 stage.style.width=frameWidth+'px';stage.style.height=frameHeight+'px';
 stage.style.left=(box.clientWidth-frameWidth)/2+'px';stage.style.top=(box.clientHeight-frameHeight)/2+'px';
 applyView();
}
function fit(){state.zoom=1;state.panX=state.panY=0;transform();}
function transform(){$('stage').style.transform=`translate(${state.panX}px,${state.panY}px) scale(${state.zoom})`;$('zoom-value').textContent=Math.round(state.zoom*100)+'%';layoutStage();}
function zoom(factor){state.zoom=Math.max(.2,Math.min(12,state.zoom*factor));transform();}
function fail(error){status(error.message||String(error),true);renderControls();}
function revoke(kind){const key=kind+'Url';if(state[key])URL.revokeObjectURL(state[key]);state[key]=null;}
async function decodedUrl(blob){
 const url=URL.createObjectURL(blob),image=new Image();image.src=url;
 try{await image.decode();return url;}catch(error){URL.revokeObjectURL(url);throw error;}
}
async function selectImage(identity){
 const image=state.images.find(i=>i.id===identity);if(!image)return;
 const epoch=++state.selectionEpoch;state.selected=identity;state.view='original';
 state.sourceReady=state.vectorReady=null;$('stage').hidden=true;$('empty').hidden=true;
 revoke('vector');$('vector-image').removeAttribute('src');fit();renderFiles();renderControls();status(t('uploading'),false,true);
 try{
 const response=await api('/api/source/'+identity),url=await decodedUrl(await response.blob());
 if(state.selected!==identity||state.selectionEpoch!==epoch){URL.revokeObjectURL(url);return;}
 revoke('source');state.sourceUrl=url;$('source-image').src=url;await $('source-image').decode();
 if(state.selected!==identity||state.selectionEpoch!==epoch)return;
 state.sourceReady=identity;$('stage').hidden=false;layoutStage();
 $('file-info').textContent=`${image.name} · ${image.info.width} × ${image.info.height} px`;
 if(image.job?.status==='done')await showVector(image,epoch);
 else if(['running','queued'].includes(image.job?.status))status(t(image.job.status==='running'?'converting':'queued'),false,true);
 else status(t('loaded'));renderControls();
 }catch(error){if(state.selected===identity&&state.selectionEpoch===epoch)throw error;}
}
async function showVector(image,epoch=state.selectionEpoch){
 const identity=image.job?.id;if(!identity)return;
 try{
 const response=await api('/api/vector/'+identity),url=await decodedUrl(await response.blob());
 if(state.selected!==image.id||image.job?.id!==identity||state.selectionEpoch!==epoch){URL.revokeObjectURL(url);return;}
 revoke('vector');state.vectorUrl=url;$('vector-image').src=url;await $('vector-image').decode();
 if(state.selected!==image.id||image.job?.id!==identity||state.selectionEpoch!==epoch)return;
 state.vectorReady=identity;state.view='compare';layoutStage();renderControls();status(resultStale(image)?t('changed'):t('done'));
 }catch(error){if(state.selected===image.id&&image.job?.id===identity&&state.selectionEpoch===epoch)throw error;}
}
async function addFiles(files){const list=Array.from(files).slice(0,30-state.images.length);let first=null;for(const file of list){try{status(t('uploading'),false,true);if(file.size>32*1024*1024)throw Error('Maximum image size: 32 MB.');const image=await json('/api/upload',undefined,{method:'POST',headers:{'X-File-Name':encodeURIComponent(file.name||'clipboard.png'),'Content-Type':file.type||'application/octet-stream'},body:file});state.images.push(image);first??=image.id;renderFiles();}catch(error){fail(error);}}if(first)await selectImage(first);$('file').value='';renderControls();}
function options(){return{preset:$('preset').value,color_mode:$('color-mode').value,colors:Number($('colors').value),smoothing:Number($('smoothing').value)/100,speckle:Number($('speckle').value),transparent:$('transparent').checked,mode:$('curve-mode').value,max_dimension:Number($('max-dimension').value),corner_threshold:Number($('corner').value),color_precision:Number($('precision').value),bw_threshold:Number($('threshold').value)};}
function applyPreset(){const p=presets[$('preset').value]||{};const names={colors:'colors',color_mode:'color-mode',mode:'curve-mode',max_dimension:'max-dimension',corner_threshold:'corner',color_precision:'precision',bw_threshold:'threshold',speckle:'speckle'};for(const[key,id]of Object.entries(names))if(p[key]!==undefined)$(id).value=p[key];if(p.smoothing!==undefined)$('smoothing').value=p.smoothing*100;$('transparent').checked=p.transparent!==false;syncSliders();}
function syncSliders(){$('smoothing-value').textContent=$('smoothing').value+'%';$('speckle-value').textContent=Number($('speckle').value)**2+' px²';$('corner-value').textContent=$('corner').value+'°';$('precision-value').textContent=$('precision').value;$('threshold-value').textContent=$('threshold').value;}
async function trace(image){
 if(!image||image.submitting||['running','queued'].includes(image.job?.status))return;
 image.submitting=true;renderControls();const requested=options();
 try{image.job=await json('/api/vectorize',{image_id:image.id,options:requested});image.traceOptions=requested;
  if(state.selected===image.id)state.vectorReady=null;
  renderFiles();renderControls();status(t('converting'),false,true);poll(image,image.job.id);
 }finally{image.submitting=false;renderControls();}
}
async function poll(image,jobId){try{while(image.job?.id===jobId){const job=await json('/api/job/'+jobId);if(image.job?.id!==jobId)return;image.job=job;renderFiles();renderControls();if(state.selected===image.id){if(job.status==='done'){await showVector(image);return;}if(job.status==='error'){status(job.message,true);return;}if(job.status==='cancelled'){status(t('cancelled'));return;}status(t(job.status==='queued'?'queued':'converting')+' '+job.progress+'%',false,true);}if(!['running','queued'].includes(job.status))return;await new Promise(resolve=>setTimeout(resolve,400));}}catch(error){fail(error);}}
function download(blob,name){const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=name;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),10000);}
async function exportOne(format){const image=current();if(image?.job?.status!=='done')return;state.exporting=true;renderControls();status(t('exporting'),false,true);try{const response=await api('/api/export',{job_id:image.job.id,format});download(await response.blob(),image.name.replace(/\.[^.]*$/,'')+'.'+format);status(t('downloaded'));}catch(error){fail(error);}finally{state.exporting=false;renderControls();}}
$('open-file').onclick=$('open-empty').onclick=()=>$('file').click();$('file').onchange=()=>addFiles($('file').files).catch(fail);
$('language').onchange=()=>{state.lang=$('language').value;localStorage.setItem('migic.language',state.lang);translate();status(t(current()?.job?.status==='done'?'done':current()?'loaded':'ready'));};
$('vectorize').onclick=()=>trace(current()).catch(fail);$('batch').onclick=async()=>{try{for(const image of state.images)await trace(image);}catch(error){fail(error);}};
$('cancel').onclick=async()=>{for(const image of state.images){if(['running','queued'].includes(image.job?.status)){try{image.job=await json('/api/cancel',{job_id:image.job.id});}catch(error){fail(error);}}}renderFiles();renderControls();status(t('cancelled'));};
document.querySelectorAll('[data-view]').forEach(button=>button.onclick=()=>{state.view=button.dataset.view;renderControls();});
document.querySelectorAll('[data-export]').forEach(button=>button.onclick=()=>exportOne(button.dataset.export));
$('compare-slider').oninput=applyView;$('fit').onclick=fit;$('zoom-in').onclick=()=>zoom(1.25);$('zoom-out').onclick=()=>zoom(.8);
$('preset').onchange=()=>{applyPreset();renderControls();status(t('changed'));};$('reset').onclick=()=>{$('preset').value='logo';applyPreset();renderControls();status(t('changed'));};
for(const id of ['color-mode','colors','smoothing','speckle','transparent','curve-mode','max-dimension','corner','precision','threshold'])$(id).addEventListener('input',()=>{syncSliders();renderControls();if(current())status(t('changed'));});
$('download-all').onclick=async()=>{state.exporting=true;renderControls();try{status(t('exporting'),false,true);const format=$('batch-format').value.toLowerCase();const response=await api('/api/export-batch',{job_ids:doneImages().map(i=>i.job.id),format});download(await response.blob(),'VectorMigic-'+format+'.zip');status(t('batchReady'));}catch(error){fail(error);}finally{state.exporting=false;renderControls();}};
$('copy-svg').onclick=async()=>{try{const response=await api('/api/vector/'+current().job.id);await navigator.clipboard.writeText(await response.text());status(t('copied'));}catch(_){status(t('copyError'),true);}};
$('quit').onclick=async()=>{try{await json('/api/shutdown',{});document.body.classList.add('closed');status(t('closed'));window.close();}catch(error){fail(error);}};
const viewport=$('viewport');viewport.addEventListener('wheel',event=>{if(!current())return;event.preventDefault();zoom(event.deltaY<0?1.12:1/1.12);},{passive:false});viewport.addEventListener('pointerdown',event=>{if(event.target===$('compare-slider')||!current()||event.button!==0)return;state.drag={x:event.clientX,y:event.clientY,panX:state.panX,panY:state.panY};viewport.setPointerCapture(event.pointerId);});viewport.addEventListener('pointermove',event=>{if(!state.drag)return;state.panX=state.drag.panX+event.clientX-state.drag.x;state.panY=state.drag.panY+event.clientY-state.drag.y;transform();});viewport.addEventListener('pointerup',()=>state.drag=null);viewport.addEventListener('pointercancel',()=>state.drag=null);
let dragDepth=0;document.addEventListener('dragenter',event=>{if(event.dataTransfer?.types.includes('Files')){event.preventDefault();dragDepth++;$('drop-overlay').hidden=false;}});document.addEventListener('dragover',event=>event.preventDefault());document.addEventListener('dragleave',()=>{if(--dragDepth<=0){dragDepth=0;$('drop-overlay').hidden=true;}});document.addEventListener('drop',event=>{event.preventDefault();dragDepth=0;$('drop-overlay').hidden=true;if(event.dataTransfer?.files.length)addFiles(event.dataTransfer.files).catch(fail);});document.addEventListener('paste',event=>{const files=Array.from(event.clipboardData?.items||[]).filter(item=>item.type.startsWith('image/')).map(item=>item.getAsFile()).filter(Boolean);if(files.length){event.preventDefault();addFiles(files).catch(fail);}});
document.addEventListener('keydown',event=>{if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='o'){event.preventDefault();$('file').click();}if(event.ctrlKey&&event.key==='Enter'){event.preventDefault();trace(current()).catch(fail);}if(event.key==='Escape'&&!$('cancel').disabled)$('cancel').click();});
translate();syncSliders();json('/api/config').then(config=>{presets=config.presets;applyPreset();const version=document.querySelector('.result-foot>span:last-child');if(version&&config.version)version.textContent='v'+config.version;status(t('ready'));}).catch(fail);setInterval(()=>json('/api/ping').catch(()=>{}),30000);

window.addEventListener('resize',layoutStage);
new ResizeObserver(layoutStage).observe($('viewport'));
