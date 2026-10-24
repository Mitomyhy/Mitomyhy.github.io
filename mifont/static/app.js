'use strict';
(() => {
  const $ = id => document.getElementById(id);
  const autoThreshold=document.createElement('label');autoThreshold.className='check';
  const autoThresholdInput=document.createElement('input');autoThresholdInput.type='checkbox';autoThresholdInput.id='threshold-auto';autoThresholdInput.checked=true;
  const autoThresholdText=document.createElement('span');autoThresholdText.textContent='Automatic threshold';autoThresholdText.dataset.i18n='automaticThreshold';
  autoThreshold.append(autoThresholdInput,autoThresholdText);$('threshold').closest('label').after(autoThreshold);
  const licenseChoice=document.createElement('label');licenseChoice.className='check';licenseChoice.id='reference-license-field';licenseChoice.hidden=true;
  const licenseInput=document.createElement('input');licenseInput.type='checkbox';licenseInput.id='reference-license';
  const licenseText=document.createElement('span');licenseText.textContent='Use this OFL reference for missing letters; exported fonts follow OFL 1.1.';licenseText.dataset.i18n='referenceLicense';
  licenseChoice.append(licenseInput,licenseText);$('selected-reference').after(licenseChoice);
  const fragmentToken=new URLSearchParams(location.hash.slice(1)).get('token');
  let storedToken='';try{storedToken=sessionStorage.getItem('mifont-session')||'';if(fragmentToken)sessionStorage.setItem('mifont-session',fragmentToken);}catch{}
  const token = fragmentToken || storedToken;
  history.replaceState(null, '', location.pathname);
  const translations = {
    vi: {project:'01 / DỰ ÁN',new:'Tạo mới',family:'Tên bộ font',saveName:'Lưu tên',openProject:'Mở dự án',images:'02 / ẢNH THAM KHẢO',addImages:'Thêm ảnh',pasteHint:'Kéo thả ảnh hoặc dán ảnh chụp màn hình',separate:'03 / TÁCH KÝ TỰ',ink:'Màu nét chữ',auto:'Tự động',dark:'Chữ tối',light:'Chữ sáng',color:'Chọn màu nét',sampleInk:'Chọn trên ảnh',threshold:'Ngưỡng nét chữ',shownText:'Nội dung chữ trong ảnh',textHint:'Không bắt buộc. Giữ đúng thứ tự dòng; nội dung này giúp tách chữ viết liền.',ocr:'Gợi ý nhãn bằng OCR trên máy',analyze:'Tìm ký tự ↗',sources:'04 / THAM KHẢO TRỰC TUYẾN',searchQuery:'Tên font hoặc kiểu chữ',research:'Tìm nguồn tham khảo ↗',researchHint:'Tra cứu nguồn công khai. Ảnh của bạn luôn ở trên máy này.',imageTab:'Ảnh',glyphTab:'Bộ ký tự',previewTab:'Gõ thử',fit:'Vừa khung',emptyTitle:'Một bộ font bắt đầu từ vài chữ.',emptyText:'Thêm ảnh tham khảo. Kiểm tra ký tự. Tạo bộ font.',canvasHint:'Bấm vào khung để gán chữ. Kéo vùng trống để vẽ khung mới. Giữ Space và kéo để di chuyển.',observed:'Lấy từ ảnh',generated:'Chữ tự dựng',missing:'Còn thiếu',size:'Cỡ chữ',previewHint:'Tạo font để gõ thử đúng file TTF hoặc OTF ở đây.',ready:'Sẵn sàng.',cancel:'Hủy',coverage:'ĐỘ PHỦ KÝ TỰ',observedShort:'từ ảnh',generatedShort:'tự dựng',missingShort:'thiếu',selected:'KÝ TỰ ĐANG CHỌN',character:'Ký tự',baseline:'Đường chân chữ Y',refHeight:'Chiều cao tham chiếu',advance:'Bước chữ',bearing:'Lề trái',saveGlyph:'Lưu ký tự',remove:'Bỏ gán ký tự',mapRow:'GÁN CHỮ CHO MỘT DÒNG',assignLine:'Gán vào dòng đang chọn',extractSelected:'Vector chữ này',extractAll:'Vector chữ đã gán',complete:'HOÀN THIỆN BỘ FONT',completeButton:'Tự dựng các ký tự còn thiếu ↗',completeHint:'Chữ chưa có là ước lượng. Kiểm tra các ô màu xanh trước khi xuất.',build:'Tạo TTF + OTF ↗',saveProject:'Tải dự án ↓',dropFiles:'Thả ảnh tham khảo vào đây'},
  };
  let language = localStorage.getItem('mifont-language') || 'en';
  const original = new Map();
  document.querySelectorAll('[data-i18n]').forEach(el => original.set(el, el.textContent));
  function localize() {
    document.documentElement.lang = language;
    $('language').value = language;
    original.forEach((text, el) => el.textContent = translations[language]?.[el.dataset.i18n] || text);
    autoThresholdText.textContent=t('Automatic threshold','Tự chọn ngưỡng nét chữ');
    licenseText.textContent=t('Use this OFL reference for missing letters; exported fonts follow OFL 1.1.','Dùng nguồn OFL này cho chữ còn thiếu; font xuất ra tuân theo OFL 1.1.');
    renderProject();
  }
  const t = (en, vi) => language === 'vi' ? vi : en;
  const state = {project:null, image:null, bitmap:null, tab:'image', selection:null, job:null,
    candidates:new Map(), blobs:new Map(), imageSequence:0, scale:1, x:0, y:0, drag:null,
    space:false, picking:false, threshold:'auto', reference:null, font:null,fontSequence:0};
  const canvas = $('canvas'), ctx = canvas.getContext('2d');
  function status(text, error=false) { $('status-text').textContent=text; $('status-dot').classList.toggle('error',error); }
  async function api(path, data, rawHeaders) {
    const headers = {'X-MiFont-Token':token,...rawHeaders};
    const options = {headers,cache:'no-store'};
    if (data !== undefined) {
      options.method='POST';
      if (data instanceof Blob) options.body=data;
      else {headers['Content-Type']='application/json';options.body=JSON.stringify(data);}
    }
    const response=await fetch(path,options);
    if(!response.ok) {let message;try{message=(await response.json()).error;}catch{}throw new Error(message || `HTTP ${response.status}`);}
    return response.headers.get('Content-Type')?.includes('application/json') ? response.json() : response.blob();
  }
  function action(id, fn) { $(id).addEventListener('click', async () => {const exclusive=['new-project','save-meta','save-glyph','remove-glyph'].includes(id);if(exclusive)setBusy(true);try{await fn();}catch(error){showError(error);}finally{if(exclusive)setBusy(false);}}); }
  function showError(error) {status(error.message || String(error),true);}
  function requireProject() {if(!state.project)throw new Error(t('Create a project first.','Tạo dự án trước.'));return state.project;}
  function imageOptions() {return {foreground:$('foreground').value,ink_color:$('ink-color').value,
    threshold:state.threshold,text:$('shown-text').value};}
  function currentCandidates() {return state.image ? state.candidates.get(state.image.id)||[] : [];}
  function setBusy(busy) {
    document.body.classList.toggle('busy',busy);
    const ids=['projects','new-project','save-meta','upload','upload-empty','open-project','analyze','research','save-glyph','remove-glyph','assign-line','extract-selected','extract-all','complete','build','project-file','image-files'];
    ids.forEach(id=>$(id).disabled=busy);
    $('cancel').hidden=!busy;$('progress').hidden=!busy;
  }
  async function runJob(route, body) {
    if(state.job) throw new Error(t('Wait for this job or cancel it.','Chờ tác vụ hoàn tất hoặc hủy.'));
    setBusy(true);
    try {
      let job=await api(route,body);state.job=job.id;
      while(['queued','running'].includes(job.status)) {
        $('progress').value=Math.max(0,Math.min(100,job.progress || 0));status(job.message || 'Processing…');
        await new Promise(resolve=>setTimeout(resolve,350));job=await api('/api/job/'+state.job);
      }
      if(job.status==='error')throw new Error(job.message || 'Processing failed.');
      if(job.status==='cancelled'){status(t('Cancelled. Your previous result is saved.','Đã hủy. Kết quả trước được giữ nguyên.'));return null;}
      if(job.project) await applyProject(job.project);
      status(t('Done. Review the result.','Hoàn tất. Kiểm tra kết quả.'));
      return job;
    } finally {state.job=null;setBusy(false);}
  }
  async function blobUrl(path) {
    if(!path)return null;
    if(!state.blobs.has(path))state.blobs.set(path,api(path).then(blob=>URL.createObjectURL(blob)));
    try{return await state.blobs.get(path);}catch(error){state.blobs.delete(path);throw error;}
  }
  async function updateProjects() {
    const data=await api('/api/projects');$('projects').replaceChildren();
    data.projects.forEach(p=>{const option=document.createElement('option');option.value=p.id;option.textContent=p.name;$('projects').append(option);});
    if(state.project)$('projects').value=state.project.id;
  }
  async function applyProject(project) {
    const changed=state.project?.id!==project.id;
    if(state.project?.revision!==project.revision||changed){for(const[path,promise]of state.blobs)if(path.startsWith('/api/glyph/')){promise.then(URL.revokeObjectURL).catch(()=>{});state.blobs.delete(path);}}
    state.project=project;
    if(state.reference&&!project.references?.some(r=>r.id===state.reference.id)){state.reference=null;licenseChoice.hidden=true;licenseInput.checked=false;$('selected-reference').textContent='';$('complete').textContent=t('Generate missing proposals ↗','Tự dựng các ký tự còn thiếu ↗');}
    if(changed){state.imageSequence++;state.fontSequence++;state.image=null;state.bitmap=null;state.candidates.clear();state.selection=null;state.reference=null;if(state.font)document.fonts.delete(state.font.face);state.font=null;$('selected-reference').textContent='';licenseChoice.hidden=true;licenseInput.checked=false;}
    for(const image of project.images) if(!state.candidates.has(image.id)||!state.job || state.job && project.id===state.project.id) {
      // Keep unsaved label/box edits except when a successful analysis supplied new regions.
      if(!state.candidates.has(image.id)||image.candidates?.length && state.refreshCandidates)
        state.candidates.set(image.id,(image.candidates||[]).map(region=>({...region,box:[...region.box]})));
    }
    state.refreshCandidates=false;
    $('family').value=project.family;
    renderProject();await updateProjects();
    if(project.images.length && (changed || !state.image || !project.images.some(i=>i.id===state.image.id))) await selectImage(project.images[0].id);
    else if(state.image) {state.image=project.images.find(i=>i.id===state.image.id);draw();}
    if(changed&&!project.images.length){state.image=null;state.bitmap=null;$('empty').hidden=false;draw();}
    renderInspector();
  }
  function renderProject() {
    const p=state.project;if(!p)return;
    $('image-count').textContent=p.images.length;
    const coverage=p.coverage || {};const observed=Array.from(coverage.observed_chars||'').length, generated=Array.from(coverage.generated_chars||'').length;
    $('mapped-count').textContent=observed+generated;$('observed-count').textContent=observed;$('generated-count').textContent=generated;
    $('missing-count').textContent=Array.from(coverage.missing_chars??p.charset??'').length;
    $('observed-bar').style.width=100*observed/(p.charset.length||95)+'%';$('generated-bar').style.width=100*generated/(p.charset.length||95)+'%';
    $('project-info').textContent=`${p.name} · ${p.images.length} ${t('images','ảnh')} · ${t('saved locally','đã lưu trên máy')}`;
    $('image-list').replaceChildren();
    p.images.forEach(image=>{const button=document.createElement('button');button.className='image-row'+(image.id===state.image?.id?' active':'');
      const thumb=document.createElement('img'),name=document.createElement('span');name.textContent=image.name;button.append(thumb,name);
      blobUrl(image.preview_url).then(url=>thumb.src=url).catch(showError);
      button.addEventListener('click',()=>selectImage(image.id).catch(showError));$('image-list').append(button);});
    renderGrid();renderReferences();
    const build=p.builds.at(-1),valid=!!build;
    $('export-ttf').disabled=!valid;$('export-otf').disabled=!valid;
    $('build-summary').textContent=build?t('Font files are ready. Later edits need a new build.','Đã có file font. Sau khi sửa chữ cần tạo lại font.'):'';
    const notes=p.completion?.warnings || p.completion?.notes || [];
    $('completion-notes').textContent=Array.isArray(notes)?notes.join(' '):String(notes);
  }
  function renderGrid() {
    const p=state.project;if(!p)return;$('glyph-grid').replaceChildren();
    const glyphs=new Map(p.glyphs.filter(g=>g.char).map(g=>[g.char,g]));
    for(const char of p.charset) {
      const glyph=glyphs.get(char),button=document.createElement('button');
      button.className='glyph-cell '+(glyph?.origin||'missing')+(glyph?.id===state.selection?.glyphId?' active':'');
      button.title=`${char===' '?'Space':char} — ${glyph?glyph.origin:'missing'}`;
      if(glyph && char!==' '){const img=document.createElement('img');img.alt=char;blobUrl(glyph.svg_url).then(url=>img.src=url).catch(showError);button.append(img);}
      else {const span=document.createElement('span');span.className='placeholder';span.textContent=char===' '?'␣':char;button.append(span);}
      const label=document.createElement('small');label.textContent=char===' '?t('Space','Khoảng trắng'):char;button.append(label);
      button.addEventListener('click',()=>{state.selection=glyph?{glyphId:glyph.id}:{char};renderInspector();renderGrid();});$('glyph-grid').append(button);
    }
  }
  function renderReferences() {
    $('references').replaceChildren();
    for(const reference of state.project?.references||[]) {
      const card=document.createElement('div');card.className='reference-card';
      const strong=document.createElement('strong');strong.textContent=reference.family;card.append(strong);
      const score=document.createElement('small');score.textContent=reference.match_score==null?t('Public reference','Nguồn công khai'):`${t('Local similarity','Độ tương đồng cục bộ')}: ${(reference.match_score*100).toFixed(0)}%`;card.append(score);
      const link=document.createElement('a');link.textContent=reference.license || 'Source';link.target='_blank';link.rel='noopener noreferrer';
      try {const u=new URL(reference.license_url||reference.source_url);if(u.protocol==='https:')link.href=u.href;}catch{}card.append(link);
      if(reference.id){const choose=document.createElement('button');choose.textContent=t('Use for missing letters','Dùng cho chữ còn thiếu');choose.addEventListener('click',()=>{state.reference=reference;licenseChoice.hidden=false;licenseInput.checked=false;$('selected-reference').textContent=`${t('Reference','Tham khảo')}: ${reference.family} · ${reference.license}`;$('complete').textContent=t('Rebuild proposals from reference ↗','Dựng lại chữ thiếu theo nguồn ↗');$('complete').scrollIntoView({block:'nearest',behavior:'smooth'});});card.append(choose);}
      $('references').append(card);
    }
  }
  async function selectImage(id) {
    const image=state.project?.images.find(item=>item.id===id);if(!image)return;
    const sequence=++state.imageSequence;state.image=image;state.selection=null;state.bitmap=null;
    const url=await blobUrl(image.source_url),bitmap=new Image();bitmap.src=url;await bitmap.decode();if(sequence!==state.imageSequence)return;
    state.bitmap=bitmap;$('empty').hidden=true;renderProject();switchTab('image');fit();renderInspector();
  }
  function switchTab(tab) {
    state.tab=tab;document.querySelectorAll('[data-tab]').forEach(button=>button.classList.toggle('active',button.dataset.tab===tab));
    for(const name of ['image','glyph','preview'])$(name+'-view').hidden=(name==='glyph'?'glyphs':name)!==tab;
    $('image-tools').hidden=tab!=='image';
    if(tab==='preview')loadFontPreview().catch(showError);else if(tab==='image')requestAnimationFrame(draw);
  }
  function viewport(){return{width:canvas.clientWidth,height:canvas.clientHeight};}
  function fit(){if(!state.image)return;const{width,height}=viewport();state.scale=Math.min((width-70)/state.image.width,(height-70)/state.image.height);state.scale=Math.max(.02,state.scale);state.x=(width-state.image.width*state.scale)/2;state.y=(height-state.image.height*state.scale)/2;draw();}
  function zoom(factor,point){if(!state.image)return;const{width,height}=viewport();point=point||{x:width/2,y:height/2};const old=state.scale;state.scale=Math.max(.02,Math.min(16,state.scale*factor));state.x=point.x-(point.x-state.x)*state.scale/old;state.y=point.y-(point.y-state.y)*state.scale/old;draw();}
  function selectionRegion(){return state.selection?.candidateId?currentCandidates().find(r=>r.id===state.selection.candidateId):state.selection?.manual;}
  function selectionGlyph(){return state.project?.glyphs.find(g=>g.id===state.selection?.glyphId);}
  function draw() {
    const{width,height}=viewport();const dpr=devicePixelRatio||1;
    if(canvas.width!==Math.round(width*dpr)||canvas.height!==Math.round(height*dpr)){canvas.width=Math.round(width*dpr);canvas.height=Math.round(height*dpr);}
    ctx.setTransform(dpr,0,0,dpr,0,0);ctx.clearRect(0,0,width,height);
    if(!state.bitmap||!state.image)return;
    ctx.drawImage(state.bitmap,state.x,state.y,state.image.width*state.scale,state.image.height*state.scale);
    const selected=selectionRegion();
    for(const region of [...currentCandidates(),...(state.selection?.manual?[state.selection.manual]:[])]) {
      const[x,y,w,h]=region.box,active=region===selected;
      ctx.strokeStyle=active?'#285bd2':region.char?'#208860':'#da8627';ctx.lineWidth=active?2:1;
      const px=state.x+x*state.scale,py=state.y+y*state.scale,pw=w*state.scale,ph=h*state.scale;
      ctx.fillStyle=active?'#285bd21c':'#ffffff06';ctx.fillRect(px,py,pw,ph);ctx.strokeRect(px,py,pw,ph);
      if(region.char){ctx.font='12px Arial';ctx.fillStyle=ctx.strokeStyle;ctx.fillRect(px,py-17,Math.max(19,ctx.measureText(region.char).width+9),17);ctx.fillStyle='#fff';ctx.fillText(region.char===' '?'␣':region.char,px+4,py-4);}
      if(active){ctx.fillStyle='#285bd2';for(const[dx,dy]of[[0,0],[pw,0],[0,ph],[pw,ph]])ctx.fillRect(px+dx-4,py+dy-4,8,8);}
    }
    $('zoom-value').textContent=Math.round(state.scale*100)+'%';
  }
  function point(event){const r=canvas.getBoundingClientRect();return{x:event.clientX-r.left,y:event.clientY-r.top};}
  function source(point){return{x:(point.x-state.x)/state.scale,y:(point.y-state.y)/state.scale};}
  function clampBox(box) {const image=state.image;let[x,y,w,h]=box.map(Math.round);x=Math.max(0,Math.min(image.width-1,x));y=Math.max(0,Math.min(image.height-1,y));w=Math.max(1,Math.min(image.width-x,w));h=Math.max(1,Math.min(image.height-y,h));return[x,y,w,h];}
  canvas.addEventListener('pointerdown',event=>{
    if(!state.bitmap||state.job)return;const pos=point(event),p=source(pos);canvas.setPointerCapture(event.pointerId);
    if(state.picking){const c=document.createElement('canvas');c.width=state.bitmap.naturalWidth;c.height=state.bitmap.naturalHeight;const cx=c.getContext('2d');cx.drawImage(state.bitmap,0,0);const pixel=cx.getImageData(Math.max(0,Math.min(c.width-1,Math.round(p.x))),Math.max(0,Math.min(c.height-1,Math.round(p.y))),1,1).data;
      $('ink-color').value='#'+Array.from(pixel).slice(0,3).map(v=>v.toString(16).padStart(2,'0')).join('');state.picking=false;canvas.style.cursor='';status(t('Ink selected. Find letters to update regions.','Đã chọn màu. Bấm Tìm ký tự để cập nhật.'));return;}
    if(state.space||event.button===1){state.drag={type:'pan',pos,x:state.x,y:state.y};return;}
    const active=selectionRegion();if(active){const[x,y,w,h]=active.box;const near=(a,b)=>Math.abs(a-b)*state.scale<9;
      for(const[dx,dy,corner]of[[x,y,'nw'],[x+w,y,'ne'],[x,y+h,'sw'],[x+w,y+h,'se']])if(near(p.x,dx)&&near(p.y,dy)){state.drag={type:'resize',corner,start:p,box:[...active.box],region:active};return;}}
    const hit=[...currentCandidates(),...(state.selection?.manual?[state.selection.manual]:[])].reverse().find(r=>p.x>=r.box[0]&&p.x<=r.box[0]+r.box[2]&&p.y>=r.box[1]&&p.y<=r.box[1]+r.box[3]);
    if(hit){state.selection=hit.id?{candidateId:hit.id}:{manual:hit};state.drag={type:'move',start:p,box:[...hit.box],region:hit};}
    else if(p.x>=0&&p.y>=0&&p.x<state.image.width&&p.y<state.image.height){const region={box:clampBox([p.x,p.y,1,1]),char:'',line_id:-1};state.selection={manual:region};state.drag={type:'new',start:p,region};}
    renderInspector();draw();
  });
  canvas.addEventListener('pointermove',event=>{const d=state.drag;if(!d)return;const pos=point(event),p=source(pos);
    if(d.type==='pan'){state.x=d.x+pos.x-d.pos.x;state.y=d.y+pos.y-d.pos.y;}
    else if(d.type==='new')d.region.box=clampBox([Math.min(d.start.x,p.x),Math.min(d.start.y,p.y),Math.abs(p.x-d.start.x),Math.abs(p.y-d.start.y)]);
    else if(d.type==='move')d.region.box=clampBox([d.box[0]+p.x-d.start.x,d.box[1]+p.y-d.start.y,d.box[2],d.box[3]]);
    else {let[x,y,w,h]=d.box;let x2=x+w,y2=y+h;if(d.corner.includes('w'))x=p.x;else x2=p.x;if(d.corner.includes('n'))y=p.y;else y2=p.y;d.region.box=clampBox([Math.min(x,x2),Math.min(y,y2),Math.abs(x2-x),Math.abs(y2-y)]);}
    if(d.type!=='pan')renderInspector();draw();});
  const stopDrag=()=>{state.drag=null;};canvas.addEventListener('pointerup',stopDrag);canvas.addEventListener('pointercancel',stopDrag);
  canvas.addEventListener('wheel',event=>{event.preventDefault();zoom(Math.exp(-event.deltaY*.0015),point(event));},{passive:false});
  document.addEventListener('keydown',event=>{if(event.code==='Space'&&!/INPUT|TEXTAREA|SELECT/.test(event.target.tagName)&&!event.target.isContentEditable){state.space=true;event.preventDefault();}});
  document.addEventListener('keyup',event=>{if(event.code==='Space')state.space=false;});window.addEventListener('blur',()=>{state.space=false;state.drag=null;});
  new ResizeObserver(()=>{if(state.bitmap)draw();}).observe($('image-view'));
  function renderInspector() {
    const region=selectionRegion(),glyph=selectionGlyph(),char=region?.char??glyph?.char??state.selection?.char??'';
    $('char').value=char;$('selected-kind').textContent=glyph?(glyph.origin==='observed'?t('FROM IMAGE','TỪ ẢNH'):t('PROPOSAL','TỰ DỰNG')):region?t('REGION','VÙNG CHỮ'):'—';
    $('crop-fields').hidden=!region;$('metric-fields').hidden=!glyph;$('remove-glyph').hidden=!glyph;
    $('glyph-large').replaceChildren();
    if(glyph){const img=document.createElement('img');img.alt=char;blobUrl(glyph.svg_url).then(url=>{if(selectionGlyph()?.id===glyph.id)img.src=url;}).catch(showError);$('glyph-large').append(img);$('advance').value=glyph.advance_width;$('bearing').value=glyph.lsb;}
    else if(region&&state.bitmap){const crop=document.createElement('canvas');const[x,y,w,h]=region.box;crop.width=Math.max(1,w);crop.height=Math.max(1,h);crop.getContext('2d').drawImage(state.bitmap,x,y,w,h,0,0,w,h);crop.style.cssText='max-height:130px;max-width:100%;width:auto;height:auto;object-fit:contain';$('glyph-large').append(crop);}
    else {const span=document.createElement('span');span.textContent=char||'?';$('glyph-large').append(span);}
    if(region){['box-x','box-y','box-w','box-h'].forEach((id,index)=>$(id).value=region.box[index]);$('baseline').value=region.baseline_y??region.box[1]+region.box[3];$('reference-height').value=region.reference_height??region.box[3];}
  }
  function readRegion() {const region=selectionRegion();if(!region)throw new Error(t('Select or draw a letter region first.','Chọn hoặc vẽ vùng chữ trước.'));
    region.box=clampBox(['box-x','box-y','box-w','box-h'].map(id=>Number($(id).value)));
    const char=$('char').value;if(Array.from(char).length>1)throw new Error(t('Assign one character per region.','Mỗi vùng chỉ gán một ký tự.'));
    region.char=char;region.baseline_y=Number($('baseline').value);region.reference_height=Number($('reference-height').value);
    if(!Number.isFinite(region.baseline_y)||!Number.isFinite(region.reference_height)||region.reference_height<1)throw new Error('Invalid baseline or reference height.');return region;
  }
  ['box-x','box-y','box-w','box-h'].forEach(id=>$(id).addEventListener('change',()=>{try{readRegion();draw();renderInspector();}catch(e){showError(e);}}));
  async function trace(regions) {
    if(!regions.length)throw new Error(t('Label the letter boxes first.','Gán chữ vào các khung trước.'));
    const imageId=state.image.id;const job=await runJob('/api/extract',{project_id:requireProject().id,image_id:imageId,regions,options:imageOptions()});
    if(job){const glyph=state.project.glyphs.filter(g=>g.image_id===imageId&&g.char===regions[0].char).at(-1);if(glyph){state.selection={glyphId:glyph.id};renderInspector();}renderGrid();}
  }
  async function saveGlyph() {
    const glyph=selectionGlyph();if(glyph)await applyProject(await api('/api/glyph/update',{project_id:requireProject().id,glyph_id:glyph.id,revision:state.project.revision,char:$('char').value,advance_width:Number($('advance').value),lsb:Number($('bearing').value)}));
    else {const region=readRegion();if(!region.char)throw new Error(t('Type the character shown in this region.','Nhập ký tự trong vùng này.'));await trace([region]);}
  }
  async function upload(files) {
    if(state.job)throw new Error(t('Wait for processing before adding images.','Chờ tác vụ hoàn tất trước khi thêm ảnh.'));
    setBusy(true);
    try {
    for(const file of files){if(!file.type.startsWith('image/')&&!/\.(png|jpe?g|webp|bmp|tiff?)$/i.test(file.name))continue;
      status(t('Adding ','Đang thêm ')+file.name);const result=await api('/api/upload',file,{'X-Project-Id':requireProject().id,'X-File-Name':encodeURIComponent(file.name)});
      await applyProject(result.project);await selectImage(result.image.id);}
    status(t('Image ready. Find letters or draw a crop.','Ảnh đã sẵn sàng. Tìm ký tự hoặc vẽ vùng chữ.'));
    } finally {setBusy(false);}
  }
  function safeName(text){return(text||'MiFont').replace(/[<>:"/\\|?*\x00-\x1f]/g,'_').trim().slice(0,80)||'MiFont';}
  function download(blob,name){const url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download=name;link.click();setTimeout(()=>URL.revokeObjectURL(url),30000);}
  async function exportFile(format){const p=requireProject(),blob=await api('/api/export',{project_id:p.id,format});download(blob,safeName(p.family)+(format==='project'?'.mifont.zip':'.'+format));}
  async function loadFontPreview() {
    const p=requireProject(),build=p.builds.at(-1);if(!build)return;
    const sequence=++state.fontSequence,format=$('preview-format').value.toLowerCase(),key=p.id+build.id+format;
    if(state.font?.key!==key){const blob=await api(`/api/font-preview/${p.id}/${build.id}/${format}`),font=new FontFace('MiFontPreview_'+build.id+'_'+format,await blob.arrayBuffer());await font.load();if(sequence!==state.fontSequence||state.project?.id!==p.id||$('preview-format').value.toLowerCase()!==format)return;document.fonts.add(font);if(state.font)document.fonts.delete(state.font.face);state.font={key,face:font};}
    if(sequence!==state.fontSequence)return;
    $('font-preview').style.fontFamily='"'+state.font.face.family+'"';
    const missing=build.coverage?.missing_chars??build.metrics?.missing_chars??state.project.coverage?.missing_chars??'';
    $('preview-hint').textContent=t('Showing your actual '+format.toUpperCase()+'.','Đang hiển thị đúng file '+format.toUpperCase()+' của bạn.')+(missing?t(' Missing characters may appear in a system fallback font: ',' Ký tự còn thiếu có thể hiển thị bằng font hệ thống: ')+missing:'');
  }
  $('language').addEventListener('change',()=>{language=$('language').value;localStorage.setItem('mifont-language',language);localize();});
  $('projects').addEventListener('change',()=>api('/api/project/'+$('projects').value).then(applyProject).catch(showError));
  document.querySelectorAll('[data-tab]').forEach(button=>button.addEventListener('click',()=>switchTab(button.dataset.tab)));
  $('foreground').addEventListener('change',()=>{$('color-field').hidden=$('foreground').value!=='color';});
  $('threshold').addEventListener('input',()=>{state.threshold=Number($('threshold').value);$('threshold-value').textContent=state.threshold;});
  autoThresholdInput.addEventListener('change',()=>{state.threshold=autoThresholdInput.checked?'auto':Number($('threshold').value);$('threshold').disabled=autoThresholdInput.checked;$('threshold-value').textContent=autoThresholdInput.checked?'Auto':state.threshold;});
  action('new-project',async()=>{await applyProject(await api('/api/project/new',{name:'MiFont Draft',family:'MiFont Draft'}));status(t('New project. Add reference images.','Dự án mới. Thêm ảnh tham khảo.'));});
  action('save-meta',async()=>{const p=requireProject();await applyProject(await api('/api/project/update',{project_id:p.id,revision:p.revision,family:$('family').value,name:$('family').value}));status(t('Font name saved.','Đã lưu tên font.'));});
  action('upload',()=>$('image-files').click());action('upload-empty',()=>$('image-files').click());
  $('image-files').addEventListener('change',()=>{upload($('image-files').files).catch(showError).finally(()=>$('image-files').value='');});
  action('open-project',()=>$('project-file').click());$('project-file').addEventListener('change',async()=>{try{const file=$('project-file').files[0];if(file)await applyProject(await api('/api/project/import',file));}catch(e){showError(e);}finally{$('project-file').value='';}});
  action('analyze',async()=>{if(!state.image)throw new Error(t('Add an image first.','Thêm ảnh trước.'));state.refreshCandidates=true;
    const job=await runJob('/api/analyze',{project_id:requireProject().id,image_ids:[state.image.id],options:{...imageOptions(),ocr:$('ocr').checked}});
    if(job){const regions=currentCandidates();if(regions.length){state.selection={candidateId:regions[0].id};renderInspector();draw();}status(t(`Found ${regions.length} regions. Review cuts and character labels.`,`Tìm được ${regions.length} vùng. Kiểm tra khung và nhãn chữ.`));}});
  action('pick-ink',()=>{if(!state.bitmap)throw new Error('Add an image first.');state.picking=true;canvas.style.cursor='crosshair';status(t('Click a solid part of a letter.','Bấm vào phần nét chữ đặc.'));});
  action('save-glyph',saveGlyph);action('extract-selected',()=>{readRegion();return trace([selectionRegion()]);});
  action('extract-all',()=>{if(selectionRegion())readRegion();return trace(currentCandidates().filter(r=>r.char));});
  action('remove-glyph',async()=>{const glyph=selectionGlyph();if(!glyph)return;await applyProject(await api('/api/glyph/remove',{project_id:requireProject().id,glyph_id:glyph.id,revision:state.project.revision}));state.selection=null;renderInspector();});
  action('assign-line',()=>{const region=selectionRegion();if(!region)throw new Error(t('Select a box in the line first.','Chọn một khung trong dòng trước.'));
    const line=currentCandidates().filter(r=>r.line_id===region.line_id).sort((a,b)=>a.box[0]-b.box[0]);const chars=Array.from($('map-text').value.replace(/[\r\n]/g,''));
    if(chars.length!==line.length)throw new Error(t(`This line has ${line.length} boxes, but ${chars.length} characters were entered.`,`Dòng này có ${line.length} khung nhưng bạn nhập ${chars.length} ký tự.`));
    line.forEach((r,i)=>r.char=chars[i]);renderInspector();draw();status(t('Line labels updated. Trace mapped to save the outlines.','Đã gán chữ. Bấm Vector chữ đã gán để lưu đường nét.'));});
  action('research',async()=>{const job=await runJob('/api/research',{project_id:requireProject().id,query:$('research-query').value,options:{candidate_limit:6}});if(job)renderReferences();});
  action('complete',async()=>{const options={mode:'proposal',allow_approximate:true};if(state.reference){if(!licenseInput.checked)throw new Error(t('Read and accept the reference license before generating these missing letters.','Đọc và chọn đồng ý giấy phép nguồn trước khi tạo chữ còn thiếu.'));options.mode='reference';options.reference_id=state.reference.id;options.accept_reference_license=true;options.replace_generated=true;}
    await runJob('/api/complete',{project_id:requireProject().id,options});switchTab('glyphs');});
  action('build',async()=>{const p=requireProject();if($('family').value!==p.family)await applyProject(await api('/api/project/update',{project_id:p.id,revision:p.revision,family:$('family').value,name:$('family').value}));
    const job=await runJob('/api/build',{project_id:p.id,metadata:{family:$('family').value}});if(job){switchTab('preview');const coverage=state.project.coverage;status(t(`Font built: ${coverage.observed_chars.length} traced, ${coverage.generated_chars.length} proposed, ${coverage.missing_chars.length} missing.`,`Đã tạo font: ${coverage.observed_chars.length} từ ảnh, ${coverage.generated_chars.length} tự dựng, ${coverage.missing_chars.length} còn thiếu.`));}});
  action('export-ttf',()=>exportFile('ttf'));action('export-otf',()=>exportFile('otf'));action('save-project',()=>exportFile('project'));
  action('cancel',()=>state.job?api('/api/cancel',{job_id:state.job}):null);
  action('fit',fit);action('zoom-in',()=>zoom(1.25));action('zoom-out',()=>zoom(.8));
  $('preview-size').addEventListener('input',()=>$('font-preview').style.fontSize=$('preview-size').value+'px');$('preview-format').addEventListener('change',()=>loadFontPreview().catch(showError));
  action('close',async()=>{if(state.job)await api('/api/cancel',{job_id:state.job});await api('/api/shutdown',{});document.body.classList.add('closed');status(t('MiFont closed. Your projects are saved.','MiFont đã đóng. Các dự án đã được lưu.'));});
  document.addEventListener('paste',event=>{if(event.target.matches('input,textarea')||event.target.isContentEditable)return;const files=Array.from(event.clipboardData?.items||[]).filter(i=>i.type.startsWith('image/')).map(i=>i.getAsFile());if(files.length){event.preventDefault();upload(files).catch(showError);}});
  let dragDepth=0;document.addEventListener('dragenter',event=>{if(event.dataTransfer?.types.includes('Files')){event.preventDefault();dragDepth++;$('drop-overlay').hidden=false;}});
  document.addEventListener('dragover',event=>{if(event.dataTransfer?.types.includes('Files'))event.preventDefault();});
  document.addEventListener('dragleave',()=>{if(--dragDepth<=0){dragDepth=0;$('drop-overlay').hidden=true;}});
  document.addEventListener('drop',event=>{event.preventDefault();dragDepth=0;$('drop-overlay').hidden=true;upload(event.dataTransfer.files).catch(showError);});
  async function init(){localize();if(!token)throw new Error(t('Open MiFont from the launcher to start a secure local session.','Mở MiFont từ launcher để bắt đầu phiên cục bộ.'));
    const config=await api('/api/config');$('version').textContent='MiFont '+config.version+' · LOCAL CPU';$('threshold-value').textContent='Auto';$('threshold').disabled=true;$('threshold').min=1;$('threshold').max=254;$('research-query').maxLength=120;
    const list=await api('/api/projects');await applyProject(list.projects.length?await api('/api/project/'+list.projects[0].id):await api('/api/project/new',{}));
    setInterval(()=>{if(!document.body.classList.contains('closed'))api('/api/ping').catch(()=>status(t('Local session ended. Reopen MiFont to continue.','Phiên cục bộ đã kết thúc. Mở lại MiFont để tiếp tục.'),true));},30000);}
  init().catch(showError);
})();
