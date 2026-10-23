import { checkSize, bytesDataURL, encodeGray16PNG } from './depth-pixels.js?v=20261022-flow-r5';
import { createCPUPipeline } from './cpu/client.mjs?v=20261023-psd-gpu-r11';
import { createLocalDepthEdits } from './depth-local-edit.js?v=20261022-flow-r5';
import { classifyDepthInput } from './depth-input.js?v=20261023-psd-gpu-r11';

const TYPES = new Set(['acrylic', 'ceramic_circle', 'ceramic_heart']);
const TEMPLATE_URL = new URL('./assets/templates/template-descriptors.json', import.meta.url);
const MANIFEST_URL = new URL('./cpu/manifest.json', import.meta.url).href;
const PHASES = {
  manifest: 'Kiểm tra phiên bản AI', 'python-runtime': 'Tải bộ xử lý Python trên trình duyệt',
  'python-packages': 'Tải thư viện xử lý ảnh', 'native-module-integrity': 'Xác minh thuật toán gốc',
  'native-preparation': 'Chuẩn bị ảnh theo pipeline gốc', 'onnx-runtime': 'Khởi động ONNX CPU / GPU',
  'moge-download-verify': 'Tải / kiểm tra MoGe', 'moge-session': 'Nạp MoGe',
  'moge-inference': 'MoGe đang tính hình học và normal', 'native-scipy-camera-recovery': 'Khôi phục camera / depth',
  'da2-download-verify': 'Tải / kiểm tra DA2', 'da2-session': 'Nạp DA2',
  'da2-inference': 'DA2 đang tính chi tiết', 'native-relief-composition': 'Ghép depth, chi tiết và support', complete: 'Hoàn tất'
};

async function decodeImage(file, document) {
  if (!(file instanceof Blob) || !/^image\/(png|jpeg|webp)$/.test(file.type) || file.size > 64 * 1024 * 1024)
    throw Error('Chọn PNG, JPG hoặc WebP, tối đa 64 MB.');
  const bitmap = await createImageBitmap(file);
  try {
    checkSize(bitmap.width, bitmap.height);
    const canvas = document.createElement('canvas'); canvas.width = bitmap.width; canvas.height = bitmap.height;
    const context = canvas.getContext('2d', {willReadFrequently:true});
    if (!context) throw Error('Không có Canvas 2D.');
    context.drawImage(bitmap, 0, 0);
    const result = {width:bitmap.width, height:bitmap.height, rgba:context.getImageData(0,0,canvas.width,canvas.height).data};
    canvas.width = canvas.height = 1; return result;
  } finally { bitmap.close(); }
}

async function rendererFactory(canvas, settings) {
  await import('./assets/depth/depth-viewer.js');
  if (!globalThis.UoyDepthViewer) throw Error('Không tải được bộ xem 3D.');
  return globalThis.UoyDepthViewer.create(canvas, settings);
}

async function templatePreviewLoader(family) {
  const {callPSDWorker}=await import('./depth-psd-client.js?v=20261023-psd-gpu-r11');
  return callPSDWorker('preview',{family});
}

/** Saved parent composite, never the AI input. Role rects stay in the original
 * parent coordinates even when the worker returns a downsampled preview. */
function checkedTemplatePreview(value) {
  const {width,height,sourceWidth,sourceHeight,rgba,roles}=value||{};
  if(!Number.isInteger(width)||!Number.isInteger(height)||Math.min(width,height)<1||Math.max(width,height)>1600||
    !Number.isInteger(sourceWidth)||!Number.isInteger(sourceHeight)||Math.min(sourceWidth,sourceHeight)<1||Math.max(sourceWidth,sourceHeight)>8192||sourceWidth*sourceHeight>16000000||
    !(rgba instanceof Uint8Array||rgba instanceof Uint8ClampedArray)||rgba.length!==width*height*4)
    throw Error('Bản xem PSD gốc không hợp lệ hoặc vượt giới hạn.');
  const checked={};
  for(const key of ['input','depth','cut']) {
    const role=roles?.[key];if(!role){if(key==='input')throw Error('Bản xem PSD thiếu vị trí IN đã xác nhận.');continue;}
    const rect=role.transform;
    if(!Array.isArray(rect)||rect.length!==4||!rect.every(Number.isFinite)||rect[0]<-1.25||rect[1]<-1.25||rect[2]>sourceWidth+1.25||rect[3]>sourceHeight+1.25||rect[2]<=rect[0]||rect[3]<=rect[1])
      throw Error('Vị trí layer trong bản xem PSD không hợp lệ.');
    checked[key]={transform:rect.slice()};
  }
  return {width,height,sourceWidth,sourceHeight,rgba:new Uint8ClampedArray(rgba),roles:checked};
}

function png16(bytes, width, height) {
  if (!(bytes instanceof Uint8Array) || bytes.length < 33 || bytes[0]!==137 || bytes[1]!==80 || bytes[2]!==78 || bytes[3]!==71 || bytes[24]!==16 || ![0,4].includes(bytes[25]))
    throw Error('CPU chưa trả về PNG depth grayscale 16 bit hợp lệ.');
  const view = new DataView(bytes.buffer,bytes.byteOffset,bytes.byteLength);
  if (view.getUint32(16)!==width || view.getUint32(20)!==height) throw Error('PNG depth và kết quả không cùng canvas.');
}

/** Original FP32 networks + native postprocess on CPU WASM. No local service,
 * Photoshop requirement, silent model fallback or automatic Generate. */
export function createDepthTool(canvas, options={}) {
  if (!canvas?.ownerDocument) throw Error('Cần canvas để mở 3D tool.');
  const document=canvas.ownerDocument, onStatus=options.onStatus||(()=>{}), onProgress=options.onProgress||(()=>{});
  const makeRenderer=options.rendererFactory||rendererFactory, readImage=options.readPixels||(file=>decodeImage(file,document));
  const readTemplatePreview=options.templatePreviewLoader||templatePreviewLoader;
  const nativeWork=options.nativePSDWorker|| (async(op,data,settings)=>{const {callPSDWorker}=await import('./depth-psd-client.js?v=20261023-psd-gpu-r11');return callPSDWorker(op,data,settings);});
  let nativeSource=null,artworkReplaced=false,psdCache=null,psdAbort=null,previewIsResult=false;
  let viewer=null, descriptor=null, templatePreview=null, productType='acrylic', artwork=null, source=null, result=null, edits=null, resultMode=null;
  let imageName='', busy=false, disposed=false, version=0, imageSelection=0, queue=Promise.resolve(), templateCatalog=null, shapeBitmap=null;
  let appearance={rimBoost:1.2,reliefBoost:6,gloss:.65};
  const pipeline=(options.pipelineFactory||createCPUPipeline)({manifestURL:MANIFEST_URL,onProgress(value){
    if(disposed)return;onProgress(value);
    if(value.type==='phase')onStatus((PHASES[value.phase]||String(value.phase).replace(/-/g,' '))+'…');
    if(value.type==='download') {
      const percent=value.total>0?` · ${Math.min(100,Math.round(value.received/value.total*100))}%`:'';
      onStatus(`Đang tải / kiểm tra ${value.kind==='moge'?'MoGe':'DA2'}${percent} · ảnh không rời máy này.`);
    }
  }});
  const check=()=>{if(disposed)throw Error('3D tool đã đóng.');};
  const noBusy=()=>{check();if(busy)throw Error('Đang Generate; bấm Hủy nếu muốn thay template hoặc artwork.');};
  const clearResult=()=>{pipeline.cancel();psdAbort?.abort();psdCache=null;previewIsResult=false;result=null;edits=null;resultMode=null;viewer?.dispose();viewer=null;version++;};
  function pixelsCanvas(data) {
    const out=document.createElement('canvas');out.width=data.width;out.height=data.height;
    out.getContext('2d').putImageData(new ImageData(new Uint8ClampedArray(data.rgba),data.width,data.height),0,0);return out;
  }
  function currentShape(width,height) {
    if(productType==='acrylic')return undefined;
    if(!shapeBitmap)throw Error('Chưa tải shape gốc của template Ceramic.');
    const out=document.createElement('canvas');out.width=width;out.height=height;
    const context=out.getContext('2d',{willReadFrequently:true});context.drawImage(shapeBitmap,0,0,width,height);
    const rgba=context.getImageData(0,0,width,height).data, alpha=new Uint8Array(width*height);
    for(let i=0;i<alpha.length;i++)alpha[i]=rgba[i*4+3];
    out.width=out.height=1;return alpha;
  }
  async function draw(recreate=false) {
    const token=version;
    queue=queue.catch(()=>{}).then(async()=>{
      check();if(token!==version||!result)return;
      if(recreate&&viewer){viewer.dispose();viewer=null;}
      if(!viewer){
        const candidate=await makeRenderer(canvas,{product_type:productType,appearance,maxGrid:384});
        if(disposed||token!==version||!result){candidate.dispose();throw Object.assign(Error('Đã đổi nguồn hoặc đóng bộ xem 3D.'),{code:'CANCELLED'});}
        viewer=candidate;
      }
      check();if(token!==version||!result)return;
      const data=edits?.current()||result;
      viewer.setData({...data,shape_alpha:currentShape(data.width,data.height),product_type:productType});
    });return queue;
  }
  function fitArtwork() {
    const out=document.createElement('canvas');out.width=descriptor.width;out.height=descriptor.height;
    const context=out.getContext('2d',{willReadFrequently:true});
    if(artwork) {
      const image=pixelsCanvas(artwork),scale=Math.min(out.width/artwork.width,out.height/artwork.height);
      const w=artwork.width*scale,h=artwork.height*scale;
      context.drawImage(image,(out.width-w)/2,(out.height-h)/2,w,h);image.width=image.height=1;
      if(shapeBitmap){context.globalCompositeOperation='destination-in';context.drawImage(shapeBitmap,0,0,out.width,out.height);context.globalCompositeOperation='source-over';}
    } else if(shapeBitmap)context.drawImage(shapeBitmap,0,0,out.width,out.height);
    else {context.fillStyle='#ffffff';context.fillRect(0,0,out.width,out.height);}
    source={width:out.width,height:out.height,rgba:context.getImageData(0,0,out.width,out.height).data};
    out.width=out.height=1;
  }
  async function loadTemplate(type,{nativeInput=null,selection=null,name=''}={}) {
    noBusy();if(!TYPES.has(type))throw Error('Template không hợp lệ.');
    const token=++version;
    templateCatalog||=fetch(TEMPLATE_URL,{credentials:'omit'}).then(async response=>{
      if(!response.ok)throw Error('Chưa tải được template gốc.');const value=await response.json();
      if(value.schema!=='mitom.native-template-blueprints.v1'||!value.products)throw Error('Template descriptor sai phiên bản.');return value;
    }).catch(error=>{templateCatalog=null;throw error;});
    const catalog=await templateCatalog, next=catalog.products[type];check();
    if(!next||next.product_type!==type||next.dpi!==300)throw Error('Template chưa xác nhận đúng loại / DPI.');
    checkSize(next.width,next.height);
    let bitmap=null;
    if(type!=='acrylic') {
      const entry=next.shape?.rgba;
      if(!entry||!/^[a-z0-9-]+\.png$/.test(entry.file))throw Error('Thiếu shape RGBA gốc, không dùng shape hình học thay thế.');
      const response=await fetch(new URL(entry.file,TEMPLATE_URL),{credentials:'omit'});
      if(!response.ok)throw Error('Không tải được shape gốc.');const blob=await response.blob();
      if(blob.size!==entry.bytes)throw Error('Shape gốc không đúng dung lượng.');
      const digest=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',await blob.arrayBuffer())),n=>n.toString(16).padStart(2,'0')).join('');
      if(digest!==entry.sha256)throw Error('Shape gốc không qua kiểm tra SHA-256.');
      bitmap=await createImageBitmap(blob);
      if(bitmap.width!==next.width||bitmap.height!==next.height){bitmap.close();throw Error('Shape gốc sai canvas.');}
    }
    let preview;
    try{preview=checkedTemplatePreview(nativeInput?.preview||await readTemplatePreview(type));}
    catch(error){bitmap?.close();throw error;}
    if(version!==token||disposed||(selection!==null&&selection!==imageSelection)){bitmap?.close();return api.state();}
    if(nativeInput){const input=nativeInput.artwork;artwork=nativeInput.isBundledBlank?null:{width:input.width,height:input.height,rgba:new Uint8ClampedArray(input.rgba)};imageName=name;}
    shapeBitmap?.close();shapeBitmap=bitmap;descriptor=next;templatePreview=preview;productType=type;nativeSource=nativeInput;artworkReplaced=nativeInput?false:!!artwork;clearResult();fitArtwork();
    viewer?.dispose();viewer=null;
    onStatus(`Đã mở bố cục PSD gốc ${type==='acrylic'?'Acrylic':type==='ceramic_circle'?'Circle':'Heart'} · IN ${next.width} × ${next.height} · 300 DPI. ${artwork?'Artwork được đặt lại vào IN; DEPTH/CUT vẫn là bản gốc, cần Generate mới.':'Chọn artwork tiếp theo.'}`);
    return api.state();
  }
  function ready(){check();if(!result)throw Error('Generate map hoặc nhập PNG depth trước khi xuất.');}
  function adopt(value,mode,rebase=false){
    const {width,height,depth,rgba,support}=value||{},depth16PNG=value?.depth16PNG||value?.depthPNG;
    if(!Number.isInteger(width)||!Number.isInteger(height)||Math.min(width,height)<8||Math.max(width,height)>2048)throw Error('CPU trả canvas ngoài giới hạn 2048.');
    const n=width*height;
    if(!(depth instanceof Float32Array)||depth.length!==n||!depth.every(v=>Number.isFinite(v)&&v>=0&&v<=1)||
      !(rgba instanceof Uint8Array||rgba instanceof Uint8ClampedArray)||rgba.length!==n*4||
      (support!==undefined&&(!(support instanceof Uint8Array)||support.length!==n)))throw Error('CPU trả dữ liệu không hợp lệ; không xuất bản thay thế.');
    for(const key of ['alpha','mapAlpha','mogeAlpha','da2Alpha'])if(value[key]!==undefined&&(!(value[key] instanceof Uint8Array||value[key] instanceof Uint8ClampedArray)||value[key].length!==n))throw Error('CPU trả alpha không hợp lệ.');
    for(const key of ['mapDepth','mogeDepth','da2Depth'])if(value[key]!==undefined&&(!(value[key] instanceof Float32Array)||value[key].length!==n||!value[key].every(v=>Number.isFinite(v)&&v>=0&&v<=1)))throw Error('CPU trả map không hợp lệ.');
    png16(depth16PNG,width,height);const next={...value,depth16PNG,rgba:new Uint8ClampedArray(rgba)};
    let nextEdits=edits;if(rebase&&nextEdits)nextEdits.rebase(next);else nextEdits=createLocalDepthEdits(next,document);
    result=next;edits=nextEdits;resultMode=mode;psdCache=null;previewIsResult=false;
  }
  function mapData(kind='depth'){
    ready();const current=edits?.current()||result;
    if(kind==='depth')return{depth:current.depth,alpha:current.alpha||result.mapAlpha||null,width:result.width,height:result.height};
    if(kind==='map'){
      const depth=current.mapDepth||result.mapDepth||current.depth;
      return{depth,alpha:result.mapAlpha||current.alpha||null,width:result.width,height:result.height};
    }
    const field=kind==='moge'?'moge':kind==='da2'?'da2':null;
    if(!field||!(result[field+'Depth'] instanceof Float32Array))throw Error('Chưa có map '+kind+' ở lượt này.');
    return{depth:result[field+'Depth'],alpha:result[field+'Alpha']||null,width:result.width,height:result.height};
  }
  const api={
    openTemplate(type){imageSelection++;return loadTemplate(type);},
    async openPSD(file){
      noBusy();const selection=++imageSelection;if(await classifyDepthInput(file)!=='psd')throw Error('Chọn template PSD có IN / DEPTH / CUT.');
      if(selection!==imageSelection)return api.state();
      const native=await nativeWork('open',{bytes:new Uint8Array(await file.arrayBuffer())});check();
      if(selection!==imageSelection)return api.state();
      const preview=checkedTemplatePreview(native.preview),input=native.artwork;
      checkSize(input?.width,input?.height);
      if(!input?.rgba||input.rgba.length!==input.width*input.height*4)throw Error('PSD chưa có composite IN hợp lệ. Lưu Smart Object IN trong Photoshop trước.');
      await loadTemplate(native.family,{nativeInput:{...native,preview},selection,name:String(file.name||'Photoshop-template.psd')});check();if(selection!==imageSelection)return api.state();
      onStatus('Đã nhận PSD và đọc riêng Smart Object IN. IN / DEPTH / CUT vẫn được giữ trong template.');return api.state();
    },
    async loadImage(file){noBusy();const selection=++imageSelection;if(file instanceof Blob){const kind=await classifyDepthInput(file);if(selection!==imageSelection)return api.state();if(kind==='psd')return api.openPSD(file);}if(!descriptor)await loadTemplate(productType);check();
      if(selection!==imageSelection||!descriptor)return api.state();const token=++version,data=await readImage(file);check();
      if(token!==version||selection!==imageSelection)return api.state();checkSize(data.width,data.height);
      if(!data.rgba||data.rgba.length!==data.width*data.height*4||!data.rgba.some((v,i)=>i%4===3&&v>0))throw Error('Artwork rỗng hoặc không hợp lệ.');
      artwork={width:data.width,height:data.height,rgba:new Uint8ClampedArray(data.rgba)};artworkReplaced=true;imageName=String(file.name||'artwork');clearResult();fitArtwork();
      onStatus('Artwork đã hiện trên canvas. Tạo PNG map trước; xem / xuất 3D là bước riêng.');return api.state();},
    async generate(settings={},mode='full',device='cpu'){noBusy();if(!artwork||!source)throw Error('Mở template và chọn artwork trước.');
      if(!['maps','full'].includes(mode))throw Error('Chọn map hoặc workflow 3D.');
      if(!['cpu','gpu'].includes(device))throw Error('Chọn CPU hoặc GPU.');
      busy=true;const token=++version;
      try {
        const value=await pipeline.run({rgba:source.rgba,width:source.width,height:source.height,productType,options:settings,threads:1,outputMode:mode,device});check();
        if(version!==token)throw Object.assign(Error('Đã hủy hoặc đổi nguồn; không áp dụng kết quả cũ.'),{code:'CANCELLED'});
        adopt(value,mode);if(mode==='full')await draw();
        onStatus(mode==='maps'?`Đã có PNG map ${result.width} × ${result.height} · tải map ngay, không cần dựng 3D.`:`Đã ghép MoGe + DA2 + thuật toán gốc · ${result.width} × ${result.height} · Standard, không phải UHD.`);return api.state();
      } finally {busy=false;}
    },
    async compose(settings={},render3D=true){noBusy();ready();if(resultMode==='imported')return api.show3D();if(typeof pipeline.compose!=='function'||!pipeline.canCompose)throw Error('Cache AI không còn. Tạo map lại trước khi ghép 3D.');
      busy=true;const token=++version;
      try{const value=await pipeline.compose({options:settings,productType});check();if(version!==token)throw Object.assign(Error('Đã hủy ghép 3D.'),{code:'CANCELLED'});adopt(value,'full',true);if(render3D)await draw();else{viewer?.dispose();viewer=null;}return api.state();}finally{busy=false;}
    },
    async show3D(){noBusy();ready();await draw();return api.state();},
    render2D(target,view='artwork'){
      check();if(!target?.getContext)throw Error('Thiếu canvas 2D.');
      if(view==='template'){
        if(!templatePreview)throw Error('Chưa có bản xem bố cục PSD gốc.');
        const data=templatePreview,max=Math.min(1200,Math.max(data.width,data.height)),scale=max/Math.max(data.width,data.height);
        target.width=Math.max(1,Math.round(data.width*scale));target.height=Math.max(1,Math.round(data.height*scale));
        const context=target.getContext('2d'),image=pixelsCanvas(data);
        context.drawImage(image,0,0,target.width,target.height);image.width=image.height=1;
        // Replace only the registered IN tile; never put the parent mosaic in
        // source or fabricate a DEPTH/CUT result from a merely opened template.
        if(artwork&&source&&!previewIsResult){
          const [left,top,right,bottom]=data.roles.input.transform,sx=target.width/data.sourceWidth,sy=target.height/data.sourceHeight;
          const x=left*sx,y=top*sy,w=(right-left)*sx,h=(bottom-top)*sy,input=pixelsCanvas(source);
          context.clearRect(x,y,w,h);context.drawImage(input,x,y,w,h);input.width=input.height=1;
        }
        return;
      }
      const data=view==='artwork'?(result?(edits?.current()||result):source):mapData(view);if(!data)return;
      const max=Math.min(1200,Math.max(data.width,data.height)),scale=max/Math.max(data.width,data.height);
      target.width=Math.max(1,Math.round(data.width*scale));target.height=Math.max(1,Math.round(data.height*scale));
      const image=document.createElement('canvas');image.width=data.width;image.height=data.height;
      const context=image.getContext('2d');let rgba;
      if(view==='artwork')rgba=new Uint8ClampedArray(data.rgba);else{rgba=new Uint8ClampedArray(data.width*data.height*4);for(let i=0;i<data.depth.length;i++){const shade=Math.round(data.depth[i]*255);rgba[i*4]=rgba[i*4+1]=rgba[i*4+2]=shade;rgba[i*4+3]=data.alpha?data.alpha[i]:255;}}
      context.putImageData(new ImageData(rgba,data.width,data.height),0,0);target.getContext('2d').drawImage(image,0,0,target.width,target.height);image.width=image.height=1;
    },
    setText(value){noBusy();ready();psdCache=null;edits.setText(value);return api.state();},
    beginStroke(){noBusy();ready();psdCache=null;edits.beginStroke();},paint(value){noBusy();ready();return edits.paint(value);},endStroke(){edits?.endStroke();},
    undo(){noBusy();ready();psdCache=null;return edits.undo();},resetEdits(){noBusy();ready();psdCache=null;edits.reset();return api.state();},
    setProductType(type){imageSelection++;return loadTemplate(type);},
    invalidate(){noBusy();clearResult();return api.state();},
    releaseCPUCache(){noBusy();pipeline.cancel();return api.state();},
    cancel(){version++;pipeline.cancel();psdAbort?.abort();onStatus('Đã hủy Generate · nguồn và kết quả trước vẫn được giữ.');},
    async setAppearance(value){check();appearance={...appearance,...value};await queue;if(viewer)viewer.setAppearance(appearance);return api.state();},
    preset(value){check();viewer?.preset(value);},render(){check();viewer?.render();},
    getView(){check();if(!viewer)throw Error('Mở 3D trước.');return viewer.getView();},setView(value){check();if(!viewer)throw Error('Mở 3D trước.');viewer.setView(value);},
    exportPNG(size){noBusy();ready();if(!viewer)throw Error('Bấm Xem 3D trước khi xuất mockup.');const png=viewer.exportFullPNG({width:size,height:size,padding:.06});if(typeof png!=='string'||!png.startsWith('data:image/png;base64,'))throw Error('GPU không xuất được mockup PNG.');return png;},
    exportDepthPNG(kind='depth',transparent=false){noBusy();ready();if(kind==='depth'&&!transparent&&!edits?.changed&&resultMode!=='full')return bytesDataURL(result.depth16PNG);const data=mapData(kind);let values=data.depth;if(data.alpha&&!transparent){values=values.slice();for(let i=0;i<values.length;i++)if(data.alpha[i]===0)values[i]=0;}return bytesDataURL(encodeGray16PNG(values,data.width,data.height,transparent?data.alpha:null));},
    async exportPSD(settings={}){
      noBusy();ready();if(psdCache)return psdCache;
      if(resultMode==='maps')await api.compose(settings,false);
      const token=version;busy=true;psdAbort=new AbortController();
      try{
        const native=nativeSource||await nativeWork('open',{family:productType},{signal:psdAbort.signal});
        check();if(version!==token)throw Object.assign(Error('Đã hủy xuất PSD.'),{code:'CANCELLED'});
        nativeSource=native;
        const data=edits?.current()||result;
        const built=await nativeWork('build',{bytes:native.bytes,family:productType,artwork:artworkReplaced?source:undefined,depth:{width:data.width,height:data.height,depth:data.depth,alpha:data.alpha}},{signal:psdAbort.signal});
        check();if(version!==token)throw Object.assign(Error('Đã hủy xuất PSD.'),{code:'CANCELLED'});
        if(!(built?.bytes instanceof Uint8Array)||built.bytes.length<26)throw Error('Chưa tạo được PSD hoàn chỉnh.');
        const nextPreview=built.preview?checkedTemplatePreview(built.preview):null;
        psdCache=built.bytes;if(nextPreview){templatePreview=nextPreview;previewIsResult=true;}
        onStatus('PSD hoàn chỉnh đã có IN / DEPTH / CUT; artwork trong IN được giữ nguyên.');return psdCache;
      }finally{busy=false;psdAbort=null;}
    },
    state(){return{hasTemplate:!!descriptor,hasTemplatePreview:!!templatePreview,hasImage:!!artwork,hasDepth:!!result,busy,productType,imageName,template:descriptor&&{width:descriptor.width,height:descriptor.height,dpi:descriptor.dpi},
      nativePSD:!!nativeSource,nativeArtworkSource:nativeSource?.artworkSource||null,hasPSD:!!psdCache,templatePreview:templatePreview&&{width:templatePreview.width,height:templatePreview.height,sourceWidth:templatePreview.sourceWidth,sourceHeight:templatePreview.sourceHeight,source:previewIsResult?'generated-native-PSD':'photoshop-saved-parent-composite',inputReplaced:artworkReplaced,depthCut:previewIsResult?'generated':'saved-original'},
      has3D:!!result&&!!viewer,resultMode,hasMaps:!!result?.mogeDepth&&!!result?.da2Depth,canCompose:!!pipeline.canCompose,canUndo:!!edits?.canUndo,edited:!!edits?.changed,
      width:result?.width||source?.width||0,height:result?.height||source?.height||0,metadata:result?.metadata||null,profile:'cpu-standard-fp32-1225',nativeUHDInference:false,panelEquivalent:false,templateFit:'browser-canvas-contain-not-PSD-warp'};},
    dispose(){if(disposed)return;disposed=true;version++;psdAbort?.abort();pipeline.dispose();viewer?.dispose();shapeBitmap?.close();viewer=null;source=artwork=result=edits=templatePreview=nativeSource=psdCache=null;}
  };return api;
}

