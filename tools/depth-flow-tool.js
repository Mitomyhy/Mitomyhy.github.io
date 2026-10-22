import { checkSize, bytesDataURL } from './depth-pixels.js';
import { createCPUPipeline } from './cpu/client.mjs';

const TYPES = new Set(['acrylic', 'ceramic_circle', 'ceramic_heart']);
const TEMPLATE_URL = new URL('./assets/templates/template-descriptors.json', import.meta.url);
const MANIFEST_URL = new URL('./cpu/manifest.json', import.meta.url).href;
const PHASES = {
  manifest: 'Kiểm tra phiên bản AI', 'python-runtime': 'Tải bộ xử lý Python trên trình duyệt',
  'python-packages': 'Tải thư viện xử lý ảnh', 'native-module-integrity': 'Xác minh thuật toán gốc',
  'native-preparation': 'Chuẩn bị ảnh theo pipeline gốc', 'onnx-runtime': 'Khởi động CPU WASM',
  'moge-download-verify': 'Tải / kiểm tra MoGe', 'moge-session': 'Nạp MoGe vào RAM',
  'moge-inference': 'MoGe đang tính hình học và normal', 'native-scipy-camera-recovery': 'Khôi phục camera / depth',
  'da2-download-verify': 'Tải / kiểm tra DA2', 'da2-session': 'Nạp DA2 vào RAM',
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

function png16(bytes, width, height) {
  if (!(bytes instanceof Uint8Array) || bytes.length < 33 || bytes[0]!==137 || bytes[1]!==80 || bytes[2]!==78 || bytes[3]!==71 || bytes[24]!==16 || bytes[25]!==0)
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
  let viewer=null, descriptor=null, productType='acrylic', artwork=null, source=null, result=null;
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
  const clearResult=()=>{result=null;version++;};
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
    queue=queue.catch(()=>{}).then(async()=>{
      check();if(recreate&&viewer){viewer.dispose();viewer=null;}
      if(!viewer)viewer=await makeRenderer(canvas,{product_type:productType,appearance,maxGrid:384});
      check();if(!source)return;
      const data=result?{width:result.width,height:result.height,rgba:result.rgba}:source;
      const depth=result?.depth||new Float32Array(data.width*data.height).fill(.5);
      viewer.setData({...data,depth,shape_alpha:currentShape(data.width,data.height),support:result?.support,product_type:productType});
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
  async function loadTemplate(type) {
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
    if(version!==token||disposed){bitmap?.close();return api.state();}
    shapeBitmap?.close();shapeBitmap=bitmap;descriptor=next;productType=type;clearResult();fitArtwork();await draw(true);
    onStatus(`Đã mở template ${type==='acrylic'?'Acrylic':type==='ceramic_circle'?'Circle':'Heart'} · ${next.width} × ${next.height} · 300 DPI. ${artwork?'Artwork được đặt lại; cần Generate mới.':'Chọn artwork tiếp theo.'}`);
    return api.state();
  }
  function ready(){check();if(!result||!viewer)throw Error('Generate kết quả hiện tại trước khi xuất.');}
  const api={
    openTemplate:loadTemplate,
    async loadImage(file){noBusy();const selection=++imageSelection;if(!descriptor)await loadTemplate(productType);check();
      if(selection!==imageSelection||!descriptor)return api.state();const token=++version,data=await readImage(file);check();
      if(token!==version||selection!==imageSelection)return api.state();checkSize(data.width,data.height);
      if(!data.rgba||data.rgba.length!==data.width*data.height*4||!data.rgba.some((v,i)=>i%4===3&&v>0))throw Error('Artwork rỗng hoặc không hợp lệ.');
      artwork={width:data.width,height:data.height,rgba:new Uint8ClampedArray(data.rgba)};imageName=String(file.name||'artwork');clearResult();fitArtwork();await draw();
      onStatus('Đã đặt artwork vào In · contain ở giữa canvas, không mô phỏng PSD warp. Bấm Generate.');return api.state();},
    async generate(settings={}){noBusy();if(!artwork||!source)throw Error('Mở template và chọn artwork trước.');
      busy=true;const token=++version;result=null;
      try {
        const value=await pipeline.run({rgba:source.rgba,width:source.width,height:source.height,productType,options:settings,threads:1});check();
        if(version!==token)throw Object.assign(Error('Đã hủy hoặc đổi nguồn; không áp dụng kết quả cũ.'),{code:'CANCELLED'});
        const {width,height,depth,rgba,support}=value||{},depth16PNG=value?.depth16PNG||value?.depthPNG;
        if(!Number.isInteger(width)||!Number.isInteger(height)||Math.min(width,height)<8||Math.max(width,height)>2048)throw Error('CPU trả canvas ngoài giới hạn 2048.');
        const n=width*height;
        if(!(depth instanceof Float32Array)||depth.length!==n||!depth.every(v=>Number.isFinite(v)&&v>=0&&v<=1)||
          !(rgba instanceof Uint8Array||rgba instanceof Uint8ClampedArray)||rgba.length!==n*4||
          (support!==undefined&&(!(support instanceof Uint8Array)||support.length!==n)))throw Error('CPU trả dữ liệu không hợp lệ; không xuất bản thay thế.');
        png16(depth16PNG,width,height);result={...value,depth16PNG,rgba:new Uint8ClampedArray(rgba)};await draw();
        onStatus(`Đã ghép MoGe + DA2 + thuật toán gốc · ${width} × ${height} · Standard 1225 tokens, không phải UHD.`);return api.state();
      } finally {busy=false;}
    },
    setProductType:loadTemplate,
    invalidate(){noBusy();clearResult();draw().catch(error=>onStatus(error.message));return api.state();},
    cancel(){version++;pipeline.cancel();result=null;draw().catch(error=>onStatus(error.message));onStatus('Đã hủy Generate · artwork và template vẫn được giữ.');},
    async setAppearance(value){check();appearance={...appearance,...value};await queue;if(viewer)viewer.setAppearance(appearance);return api.state();},
    preset(value){check();viewer?.preset(value);},render(){check();viewer?.render();},
    exportPNG(size){noBusy();ready();const png=viewer.exportFullPNG({width:size,height:size,padding:.06});if(typeof png!=='string'||!png.startsWith('data:image/png;base64,'))throw Error('GPU không xuất được mockup PNG.');return png;},
    exportDepthPNG(){noBusy();ready();return bytesDataURL(result.depth16PNG);},
    state(){return{hasTemplate:!!descriptor,hasImage:!!artwork,hasDepth:!!result,busy,productType,imageName,template:descriptor&&{width:descriptor.width,height:descriptor.height,dpi:descriptor.dpi},
      width:result?.width||source?.width||0,height:result?.height||source?.height||0,metadata:result?.metadata||null,profile:'cpu-standard-fp32-1225',nativeUHDInference:false,panelEquivalent:false,templateFit:'browser-canvas-contain-not-PSD-warp'};},
    dispose(){if(disposed)return;disposed=true;version++;pipeline.dispose();viewer?.dispose();shapeBitmap?.close();viewer=null;source=artwork=result=null;}
  };return api;
}
