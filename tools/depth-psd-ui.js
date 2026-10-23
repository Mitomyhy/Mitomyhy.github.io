import {createPSDJobs} from './depth-psd-jobs.js?v=20261023-psd-r9';
import {callPSDWorker} from './depth-psd-client.js?v=20261023-psd-r9';
import {createCPUPipeline} from './cpu/client.mjs?v=20261022-flow-r5';
import {fetchNativeShape} from './depth-native-shapes.js?v=20261023-psd-r8';

const TITLES={acrylic:'Acrylic',ceramic_circle:'Circle Ceramic',ceramic_heart:'Heart Ceramic'};
function abortError(){return Object.assign(Error('Đã hủy tác vụ PSD.'),{code:'CANCELLED',name:'AbortError'});}
function download(bytes,name,type){const url=URL.createObjectURL(new Blob([bytes],{type})),a=document.createElement('a');a.href=url;a.download=name;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),60000);}
function canvasPixels(pixels,document){const c=document.createElement('canvas');c.width=pixels.width;c.height=pixels.height;c.getContext('2d').putImageData(new ImageData(new Uint8ClampedArray(pixels.rgba),pixels.width,pixels.height),0,0);return c;}
async function prepareArtwork(file,native,{signal}={}){
  if(!(file instanceof Blob)||file.size>64*1024*1024||!/^image\/(png|jpeg|webp)$/.test(file.type))throw Error('Chọn artwork PNG / JPG / WebP, tối đa 64 MB.');
  const bitmap=await createImageBitmap(file);let shape;
  try{
    if(signal?.aborted)throw abortError();if(bitmap.width*bitmap.height>40_000_000)throw Error('Artwork vượt 40 megapixel.');
    const c=document.createElement('canvas');c.width=native.artwork.width;c.height=native.artwork.height;const ctx=c.getContext('2d',{willReadFrequently:true});
    const scale=Math.min(c.width/bitmap.width,c.height/bitmap.height),w=bitmap.width*scale,h=bitmap.height*scale;ctx.drawImage(bitmap,(c.width-w)/2,(c.height-h)/2,w,h);
    if(native.family!=='acrylic'){
      const spec=await fetchNativeShape(native.family,{signal});shape=await createImageBitmap(new Blob([spec.bytes],{type:'image/png'}));
      if(shape.width!==c.width||shape.height!==c.height)throw Error('Shape Ceramic không khớp canvas IN.');
      ctx.globalCompositeOperation='destination-in';ctx.drawImage(shape,0,0,c.width,c.height);
    }
    if(signal?.aborted)throw abortError();const rgba=ctx.getImageData(0,0,c.width,c.height).data;
    if(!rgba.some((value,index)=>index%4===3&&value>0))throw Error('Artwork trong suốt hoàn toàn hoặc nằm ngoài shape Ceramic. Chọn ảnh khác trước khi chạy AI.');
    return {width:c.width,height:c.height,rgba};
  }finally{bitmap.close();shape?.close();}
}

export function initPSDWorkspace({document=globalThis.document,getOptions=()=>({}),isExternalBusy=()=>false,beforeGenerate=()=>{}}={}){
  const $=id=>document.getElementById(id),dialog=$('depth-psd-dialog'),cards=[...dialog.querySelectorAll('[data-psd-slot]')];
  const views=['template','template'],previousArt=[null,null],previousPreview=[null,null],previousDisplay=[null,null];
  function paint(index,job){
    const query=role=>cards[index].querySelector(`[data-psd-role="${role}"]`),data=views[index]==='template'?job?.preview:job?.artwork;
    query('canvas').hidden=!data;
    for(const view of ['template','artwork']){
      query('view-'+view).disabled=!(view==='template'?job?.preview:job?.artwork);
      query('view-'+view).setAttribute('aria-pressed',String(views[index]===view));
    }
    const roles=job?.preview&&Object.keys(job.preview.roles).map(key=>({input:'IN',depth:'DEPTH',cut:'CUT'}[key])).join(' · ');
    query('preview-label').textContent=views[index]==='template'?(job?.preview?`${roles} · ${job.preview.sourceWidth} × ${job.preview.sourceHeight} · ${job.previewIsResult?'PSD kết quả':'PSD nguồn đã lưu'}`:'Bố cục PSD gốc'):'Artwork / IN · chỉ phần này được đưa vào AI';
    if(previousDisplay[index]===data)return;previousDisplay[index]=data||null;
    const c=query('canvas');if(!data){c.width=c.height=1;return;}
    const source=canvasPixels(data,document),ratio=Math.min(1,1000/Math.max(source.width,source.height));
    c.width=Math.max(1,Math.round(source.width*ratio));c.height=Math.max(1,Math.round(source.height*ratio));
    c.getContext('2d').drawImage(source,0,0,c.width,c.height);source.width=source.height=1;
  }
  const report=message=>{$('depth-psd-message').textContent=message;};
  const jobs=createPSDJobs({
    isExternalBusy,prepareArtwork,
    async open(input,{signal}){
      if(input.file&&(!/\.psd$/i.test(input.file.name)||input.file.size>256*1024*1024))throw Error('Chọn PSD RGB 8 bit từ ba template MITOM, tối đa 256 MB. PSB / CMYK / 16 bit chưa được hỗ trợ.');
      return callPSDWorker('open',input.family?{family:input.family}:{bytes:new Uint8Array(await input.file.arrayBuffer())},{signal});
    },
    createPipeline(onProgress){return createCPUPipeline({manifestURL:new URL('./cpu/manifest.json',import.meta.url).href,onProgress});},
    build(native,options){return callPSDWorker('build',{bytes:native.bytes,family:native.family,artwork:options.artwork,depth:options.depth},{signal:options.signal});},
    onChange(state){
      $('depth-psd-running').textContent=state.running?`${state.running} tác vụ đang chạy · có thể đóng cửa sổ này và làm việc khác.`:'Generate chạy trên máy bạn · không cần Photoshop.';
      $('depth-psd-open').textContent=state.running?`PSD · ${state.running} đang chạy ↗`:'PSD · 2 tác vụ độc lập ↗';
      state.jobs.forEach((job,index)=>{
        const card=cards[index],query=role=>card.querySelector(`[data-psd-role="${role}"]`),active=job&&['loading','queued','running'].includes(job.status);
        query('name').textContent=job?.name||`Tác vụ ${index+1}`;query('status').textContent=job?.message||'Chọn template trống hoặc upload PSD đã có artwork trong IN.';card.classList.toggle('is-error',!!job?.error);
        if(job?.family)query('template').value=job.family;query('source-download').disabled=!job?.family;
        for(const role of ['template','open','file'])query(role).disabled=!!active;
        query('art').disabled=!job?.family||!!active;query('generate').disabled=!job?.hasArtwork||!!active;
        query('cancel').disabled=!active;query('download').disabled=!job?.hasPSD;query('map').disabled=!job?.hasMap;query('remove').disabled=!job;
        const progress=query('progress');progress.hidden=!active;if(job?.progress==null)progress.removeAttribute('value');else progress.value=job.progress;
        if(previousPreview[index]!==job?.preview)views[index]='template';
        else if(previousArt[index]!==job?.artwork)views[index]='artwork';
        previousPreview[index]=job?.preview;previousArt[index]=job?.artwork;paint(index,job);
      });
    }
  });
  function protect(fn){return async()=>{try{await fn();report('');}catch(error){report(error.message);}};}
  cards.forEach((card,index)=>{
    const query=role=>card.querySelector(`[data-psd-role="${role}"]`);
    for(const view of ['template','artwork'])query('view-'+view).addEventListener('click',()=>{views[index]=view;paint(index,jobs.snapshot().jobs[index]);});
    query('open').addEventListener('click',protect(()=>jobs.load(index,{family:query('template').value,name:TITLES[query('template').value]+' · template trống'})));
    query('file').addEventListener('change',protect(()=>{const file=query('file').files[0];return file&&jobs.load(index,{file,name:file.name});}));
    query('art').addEventListener('change',protect(()=>{const file=query('art').files[0];return file&&jobs.setArtwork(index,file);}));
    query('generate').addEventListener('click',protect(()=>{if(!$('depth-psd-consent').checked)throw Error('Cho phép tải model AI và dùng CPU / RAM trước khi Generate.');if(isExternalBusy())throw Error('Generate PNG đang chạy; hãy hoàn tất hoặc hủy trước.');const settings=getOptions();beforeGenerate();jobs.generate(index,settings);}));
    query('cancel').addEventListener('click',()=>jobs.cancel(index));query('remove').addEventListener('click',()=>{jobs.remove(index);query('file').value=query('art').value='';});
    query('source-download').addEventListener('click',protect(()=>download(jobs.source(index),`MITOM-${jobs.snapshot().jobs[index].family}-source.psd`,'image/vnd.adobe.photoshop')));
    query('download').addEventListener('click',protect(()=>download(jobs.result(index),`${jobs.snapshot().jobs[index].name.replace(/\.psd$/i,'').replace(/[<>:"/\\|?*]/g,'-')}-3D.psd`,'image/vnd.adobe.photoshop')));
    query('map').addEventListener('click',protect(()=>download(jobs.result(index,'map'),`MITOM-${index+1}-depth-16bit.png`,'image/png')));
  });
  $('depth-psd-parallel').addEventListener('change',()=>jobs.setParallel(Number($('depth-psd-parallel').value)));
  $('depth-psd-open').addEventListener('click',()=>dialog.showModal());$('depth-psd-close').addEventListener('click',()=>dialog.close());
  globalThis.addEventListener('beforeunload',event=>{if(jobs.busy){event.preventDefault();event.returnValue='';}});
  return jobs;
}
