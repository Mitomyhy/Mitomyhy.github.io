/**
 * Adapter for the three bundled 2D Photoshop 3D-production map templates.
 * This is not a generic Photoshop renderer or a PSD 3D-mesh exporter.
 *
 * IN / DEPTH / CUT remain embedded, editable Smart Objects. An uploaded IN is
 * preserved byte-for-byte unless replacement artwork is explicitly supplied.
 * Replaced child layers remain stored, hidden, below generated RGB8 layers.
 * The separate pipeline PNG retains 16 bits.
 */
import {inspectPSD, readPSDLinkedFiles, decodePSDComposite, appendPSDLayers,
  decodePSDLayerPixels, replaceEmbeddedPSDs, replacePSDLayerPixels, setPSDLayerVisibility} from './psd-roundtrip.js?v=20261023-psd-gpu-r11';

const BASE = new URL('./assets/templates/psd/', import.meta.url);
const PINNED = [
  {id:'acrylic', title:'Acrylic', sourceFile:'Template acrylic.psd', compressedFile:'template-acrylic.psd.gz',
    compressedBytes:3751998, compressedSHA256:'4d77f1a9dac9ea4ce0b4055de99d3f1a35e763acd48d59ade99329b4fa6f8496',
    bytes:14782298, sha256:'6161e431321c4925dfa3f2c3d7d536febb225c426a961a8181214187fd70d2b8',
    width:3543, height:1181, inputWidth:3000, inputHeight:3001, parentLayers:4,
    warpSHA256:'22df94f3d7c5de46fa009d80a72a5dfd98c8841281952892a922483db65c6b82',
    geometrySHA256:['347929c29d64b0c49002071841d199b29b9479d5c7ec64ed7f92d1af95ffb66e','1df1cbc11c9915571bc328d377a15f521ce4af99f2606d28ee4019579c9786ab','c2d079e7578015a336fbabbe4f5f438da5d85871f02cdbb110932061113bbe35'],
    roles:[['input','layer in',1,[0,0,1180,1181]], ['depth','layer depth map',2,[1182,0,2361,1181]], ['cut','layer cắt',3,[2363,0,3542,1181]]]},
  {id:'ceramic_circle', title:'Circle Ceramic', sourceFile:'Circleceramic-Template.psd', compressedFile:'template-circle-ceramic.psd.gz',
    compressedBytes:6032855, compressedSHA256:'73f49696670e43dd2d3f7b437164940ea246952fa99abb8ca8829a24f6822505',
    bytes:14311793, sha256:'79b758e54cc3abf3b7675f8385e3b8fd9212237989767760be7bcdc292386976',
    width:1866, height:933, inputWidth:2799, inputHeight:2799, parentLayers:17,
    warpSHA256:'51883eda2fc306f9276b05d6bf6abe34f4d12c3d6aee6f06a9f7a9ef4fda6bff',
    geometrySHA256:['4da6553b57d9d44a64bd0e9d6448d7b0466bbf2cc5ba5a313d1628281b5f1ea6','7ce08f5ad278ffde405140082b4b8e9ec31f5d4889db3b6af283cef884fd018a'],
    roles:[['input','layer in',15,[0,0,933,933]], ['depth','layer depth map',16,[933,0,1866,933]]]},
  {id:'ceramic_heart', title:'Heart Ceramic', sourceFile:'Heartceramic-Template.psd', compressedFile:'template-heart-ceramic.psd.gz',
    compressedBytes:4645918, compressedSHA256:'a7fac4667634a0cbf6dd6b047a89daef2e1d1fc8e3ff4017ea4efa9ad5d14240',
    bytes:11307375, sha256:'768102a90a3f0a2701239b4dc6bf1d8bdf61e9399ca5bb6b0ea22caffaac5e16',
    width:1866, height:856, inputWidth:2799, inputHeight:2568, parentLayers:3,
    warpSHA256:'93f814fef429ebbf82d4a18767481bf7cf297722550053c2d2c5b600f865962b',
    geometrySHA256:['e918abf675de3575b330f213e9262166ff3af0f27e1077991cb5204c2a59ffb3','f5b51cc3f69763be1098b23106907b8d3e24b6645694d4b2fa6694d383771e05'],
    roles:[['input','layer in',1,[0,0,933,856]], ['depth','layer depth map',2,[933,0,1866,856]]]},
];
for (const item of PINNED) {
  for (const role of item.roles) { Object.freeze(role[3]); Object.freeze(role); }
  Object.freeze(item.roles); Object.freeze(item.geometrySHA256); Object.freeze(item);
}
export const NATIVE_TEMPLATE_CATALOG = Object.freeze(PINNED);
const FORBIDDEN_ROLE_TAGS = new Set(['lrFX','lfx2','lfxs','vmsk','vsms','LMsk','TySh','lsct']);
// Exact original IEC 61966-2.1 sRGB profile. Raw RGB from Adobe RGB / Display P3
// cannot be treated as browser sRGB without a real ICC transform.
const NATIVE_SRGB_SHA256='2b3aa1645779a9e634744faf9b01e9102b0c9b88fd6deced7934df86b949af7e';
// Photoshop may save Acrylic's identity 4×4 custom envelope as warpNone after
// editing IN. These paired fingerprints are derived from that exact no-op
// rewrite of the PUBLIC original, not from artwork or a whole user PSD.
// Every other SoLd geometry field, unit, bound, transform and padding stays
// pinned. A PlLd/SoLd mismatch or any curved/unknown warp still fails closed.
const ACRYLIC_IDENTITY_NONE=Object.freeze({
  warpSHA256:'bc377cfaf627d6b4012f394cb2d20cdb328e75422e3aa73649a212fae2d4b6fc',
  geometrySHA256:Object.freeze(['4049d6d34211b5f09bdd0e41ab088531cd69269ef7a985480c04957e0b87fb71','1eef5c2c60b541e757b426c8daa7ef43b3725eae4f3d14e4cf8144da5b3cd249','a0b1f6bcf63d4c35e039a922241952168e3d7a25fe52629eba127ed1b169bdbf'])
});
const normalize = value => String(value).normalize('NFC').trim().toLowerCase().replace(/\s+/g,' ');
function fail(message,code='NATIVE_PSD_UNSUPPORTED') { throw Object.assign(Error(message),{code}); }
function abort(signal) { if(signal?.aborted) throw Object.assign(Error('Đã hủy xử lý template PSD.'),{name:'AbortError',code:'CANCELLED'}); }
function bytesOf(value) {
  if(value instanceof Uint8Array)return value;
  if(value instanceof ArrayBuffer)return new Uint8Array(value);
  if(ArrayBuffer.isView(value))return new Uint8Array(value.buffer,value.byteOffset,value.byteLength);
  fail('Cần dữ liệu nhị phân của template PSD.');
}
function catalogItem(id) {
  const item=PINNED.find(value=>value.id===id);
  if(!item)fail('Chọn Acrylic, Circle Ceramic hoặc Heart Ceramic.');
  return item;
}
async function sha256(bytes) {
  if(!globalThis.crypto?.subtle)fail('Trình duyệt cần HTTPS để xác minh template PSD.');
  return [...new Uint8Array(await globalThis.crypto.subtle.digest('SHA-256',bytes))].map(v=>v.toString(16).padStart(2,'0')).join('');
}
async function readBounded(stream,expected,signal) {
  if(!stream?.getReader)fail('Không đọc được dữ liệu template PSD.');
  const reader=stream.getReader(), result=new Uint8Array(expected);let offset=0;
  try {
    while(true) {
      abort(signal);const {done,value}=await reader.read();if(done)break;
      if(value.length>expected-offset)fail('Template PSD vượt kích thước đã xác minh.','TEMPLATE_INTEGRITY');
      result.set(value,offset);offset+=value.length;
    }
    if(offset!==expected)fail('Template PSD tải chưa đầy đủ.','TEMPLATE_INTEGRITY');
    return result;
  } finally { try {await reader.cancel();}catch{}reader.releaseLock(); }
}

/** Loads only a pinned, same-origin, losslessly compressed original template. */
export async function fetchNativeTemplate(family,{signal,fetchImpl=globalThis.fetch}={}) {
  const item=catalogItem(family);abort(signal);
  if(typeof fetchImpl!=='function'||typeof DecompressionStream!=='function')fail('Dùng Chrome / Edge mới để tải template Photoshop.');
  const control=new AbortController(), cancel=()=>control.abort(signal?.reason);
  signal?.addEventListener('abort',cancel,{once:true});
  const timer=setTimeout(()=>control.abort(),90000);
  try {
    const response=await fetchImpl(new URL(item.compressedFile,BASE),{signal:control.signal,credentials:'omit',cache:'force-cache',redirect:'error'});
    if(!response.ok)fail(`Không tải được template ${item.title} (${response.status}).`,'TEMPLATE_DOWNLOAD');
    const length=response.headers?.get?.('content-length');
    if(length!==null&&length!==undefined&&Number(length)!==item.compressedBytes)fail('Dung lượng template tải về không khớp bản phát hành.','TEMPLATE_INTEGRITY');
    const compressed=await readBounded(response.body,item.compressedBytes,control.signal);
    if(await sha256(compressed)!==item.compressedSHA256)fail('Mã kiểm tra của template nén không đúng.','TEMPLATE_INTEGRITY');
    const stream=new Blob([compressed]).stream().pipeThrough(new DecompressionStream('gzip'));
    const bytes=await readBounded(stream,item.bytes,control.signal);
    if(await sha256(bytes)!==item.sha256)fail('Mã kiểm tra của PSD gốc không đúng.','TEMPLATE_INTEGRITY');
    abort(signal);return bytes;
  } finally {clearTimeout(timer);signal?.removeEventListener('abort',cancel);}
}

/** Pure geometry check. Perspective, rotation, changed role layout are not guessed. */
export function validateNativeRoleTransform(transform,expected) {
  if(!Array.isArray(transform)||transform.length!==8||transform.some(v=>!Number.isFinite(v)))fail('Không xác minh được transform Smart Object.');
  const [x0,y0,x1,y1,x2,y2,x3,y3]=transform;
  if(Math.abs(y0-y1)>.00001||Math.abs(y2-y3)>.00001||Math.abs(x0-x3)>.00001||Math.abs(x1-x2)>.00001||x1<=x0||y3<=y0)
    fail('Template đã xoay, perspective hoặc warp; web không thay đổi Smart Object này.');
  const actual=[x0,y0,x1,y3];
  if(actual.some((v,i)=>Math.abs(v-expected[i])>1.25))fail('Bố cục IN / DEPTH / CUT đã đổi so với template gốc.');
  return actual;
}

function inspectNative(value) {
  const bytes=bytesOf(value), info=inspectPSD(bytes);
  const item=PINNED.find(c=>c.width===info.width&&c.height===info.height);
  // Photoshop can add helpers, reorder the stack, or save an opaque document
  // with three merged RGB channels. Those changes do not turn its embedded IN
  // into a flat artwork image. Identify the native roles by their unique names
  // and verified embedded geometry, rather than the original record offsets.
  if(!item||info.version!==1||info.depth!==8||info.colorMode!==3||![3,4].includes(info.channels)||info.layerCount<item.roles.length)
    fail('Web nhận PSD RGB 8-bit của đúng 3 template MITOM, giữ nguyên canvas và bố cục gốc.');
  const linked=readPSDLinkedFiles(bytes), roles={};
  for(const [role,name,index,expected] of item.roles) {
    const matches=info.layers.filter(l=>normalize(l.name)===normalize(name));
    if(matches.length!==1)fail(`Không xác định duy nhất Smart Object ${name} trong template gốc.`);
    const layer=matches[0];
    if(!layer.placedID||!layer.keys.includes('PlLd')||!layer.keys.includes('SoLd')||layer.layerMaskBytes||layer.keys.some(k=>FORBIDDEN_ROLE_TAGS.has(k))||layer.blendMode!=='norm'||layer.opacity!==255||layer.clipping||!layer.visible)
      fail(`Smart Object ${name} có mask/effect/blend hoặc cấu trúc chưa được hỗ trợ.`);
    if(info.layers.filter(l=>l.placedID===layer.placedID).length!==1)fail('Nhiều role đang dùng cùng một nội dung Smart Object.');
    const transform=validateNativeRoleTransform(layer.placedTransform,expected);
    const contents=linked.filter(f=>f.id===layer.placedID);
    if(contents.length!==1||contents[0].kind!=='liFD'||!contents[0].bytes)fail(`Smart Object ${name} phải được nhúng trong PSD, không phải file liên kết ngoài.`);
    const embedded=contents[0], child=inspectEmbeddedHeader(embedded.bytes);
    if(child.width!==item.inputWidth||child.height!==item.inputHeight)fail(`Canvas trong ${name} đã đổi; giữ đúng kích thước template gốc.`);
    roles[role]={role,index:layer.index,name:layer.name,embeddedID:embedded.id,transform,layer,embeddedBytes:embedded.bytes,child};
  }
  return {bytes,info,item,roles};
}

function inspectEmbeddedHeader(value) {
  const bytes=bytesOf(value);
  if(bytes.length<26||String.fromCharCode(...bytes.subarray(0,4))!=='8BPS')fail('Nội dung Smart Object không phải PSD/PSB.');
  const v=new DataView(bytes.buffer,bytes.byteOffset,bytes.byteLength),version=v.getUint16(4),channels=v.getUint16(12),height=v.getUint32(14),width=v.getUint32(18),depth=v.getUint16(22),mode=v.getUint16(24);
  if(![1,2].includes(version)||![3,4].includes(channels)||depth!==8||mode!==3)fail('Nội dung Smart Object cần RGB 8-bit, không nhận PSB Gray16 thay cho template gốc.');
  return {version,channels,width,height,depth,colorMode:mode};
}

// The complete geometry/warp descriptor must match the audited source, not
// merely have a rectangular corner quad. Two leading UUIDs may change when
// Photoshop saves edited contents; everything after them remains pinned.
function geometryDescriptorBytes(layer) {
  const bytes=layer.placedObjectBytes;
  if(!(bytes instanceof Uint8Array)||bytes.length>32768)fail('Thiếu descriptor hình học của Smart Object.');
  const view=new DataView(bytes.buffer,bytes.byteOffset,bytes.byteLength);let p=0;
  const take=n=>{if(!Number.isInteger(n)||n<0||n>bytes.length-p)fail('Descriptor Smart Object bị thiếu.');const at=p;p+=n;return bytes.subarray(at,p);};
  const text=n=>String.fromCharCode(...take(n));
  const u32=()=>{take(4);return view.getUint32(p-4);};
  const key=()=>{const n=u32();if(n>256)fail('Khóa descriptor Smart Object quá dài.');return text(n||4);};
  const unicode=()=>{const n=u32();if(n>256)fail('Tên descriptor Smart Object quá dài.');const data=take(n*2),v=new DataView(data.buffer,data.byteOffset,data.byteLength);let out='';for(let i=0;i<n;i++)out+=String.fromCharCode(v.getUint16(i*2));return out.replace(/\0+$/,'');};
  if(text(4)!=='soLD'||u32()!==4||u32()!==16||unicode()!==''||key()!=='null'||u32()!==18)fail('Phiên bản descriptor Smart Object chưa hỗ trợ.');
  if(key()!=='Idnt'||text(4)!=='TEXT'||unicode()!==layer.placedID)fail('UUID descriptor Smart Object không khớp nội dung được nhúng.');
  if(key()!=='placed'||text(4)!=='TEXT'||!/^[-0-9a-f]{36}$/i.test(unicode()))fail('Phiên bản nội dung Smart Object không hợp lệ.');
  return bytes.subarray(p);
}
async function validateNativeGeometry(native,signal) {
  async function profile(info,label,allowMissing=false){
    abort(signal);const bytes=info.iccProfileBytes;
    if(bytes===null&&allowMissing)return;
    if(!(bytes instanceof Uint8Array)||await sha256(bytes)!==NATIVE_SRGB_SHA256)
      fail(`${label} không dùng profile sRGB gốc đã xác minh. Web không tự đổi màu Adobe RGB / Display P3; giữ profile sRGB IEC 61966-2.1 của template rồi lưu lại IN.`, 'NATIVE_PSD_COLOR_PROFILE');
  }
  await profile(native.info,'PSD');
  for(let i=0;i<native.item.roles.length;i++) {
    abort(signal);const role=native.roles[native.item.roles[i][0]],layer=role.layer;
    // Original Circle children are untagged sRGB. A subsequent Photoshop save
    // may tag them with the verified sRGB profile; no other profile is accepted.
    await profile(inspectPSD(role.embeddedBytes,{allowEmbeddedPSB:true}),role.name,native.item.id==='ceramic_circle');
    if(!(layer.placedWarpBytes instanceof Uint8Array))fail('Thiếu descriptor warp của Smart Object.');
    const warp=await sha256(layer.placedWarpBytes),geometry=await sha256(geometryDescriptorBytes(layer));
    const original=warp===native.item.warpSHA256&&geometry===native.item.geometrySHA256[i];
    const identityNone=native.item.id==='acrylic'&&warp===ACRYLIC_IDENTITY_NONE.warpSHA256&&geometry===ACRYLIC_IDENTITY_NONE.geometrySHA256[i];
    if(!original&&!identityNone)
      fail('Warp, transform, kích thước hoặc thiết lập Smart Object đã đổi; web không đoán lại hình học template này.');
  }
}

function opaqueWhiteComposite(image) {
  for(let i=0;i<image.rgba.length;i+=4)if(image.rgba[i]!==255||image.rgba[i+1]!==255||image.rgba[i+2]!==255||image.rgba[i+3]!==255)return false;
  return true;
}

async function nativeArtwork(native,signal) {
  const role=native.roles.input,embedded=await decodePSDComposite(role.embeddedBytes,{allowEmbeddedPSB:true,signal});
  // With compatibility compositing disabled, Photoshop can save an embedded
  // PSB's merged image as an opaque white placeholder while its editable
  // layers and the parent's rendered Smart Object cache are fully current.
  // Reading that placeholder erased real uploaded artwork and sent white to
  // AI. Recover the exact Photoshop-rendered IN cache, including its alpha,
  // through the already-validated, axis-aligned native transform instead.
  if(!opaqueWhiteComposite(embedded))return {artwork:embedded};
  const cache=await decodePSDLayerPixels(native.bytes,{index:role.index,signal});
  const [x0,y0,x1,y1]=role.transform,width=role.child.width,height=role.child.height;
  const rgba=new Uint8ClampedArray(width*height*4);
  for(let y=0;y<height;y++) {
    if(y%32===0)abort(signal);
    for(let x=0;x<width;x++)sample(cache,x0+(x+.5)*(x1-x0)/width-.5-cache.left,y0+(y+.5)*(y1-y0)/height-.5-cache.top,rgba,(y*width+x)*4);
  }
  return {artwork:{width,height,rgba,source:'photoshop-saved-IN-layer-cache'},cache};
}

/** Reads saved IN artwork, never the complete IN / DEPTH / CUT mosaic. */
export async function openNativePSD(bytes,{signal}={}) {
  abort(signal);const native=inspectNative(bytes);
  await validateNativeGeometry(native,signal);
  const {artwork}=await nativeArtwork(native,signal);
  const isBundledBlank=await sha256(native.bytes)===native.item.sha256;
  abort(signal);
  return {family:native.item.id,title:native.item.title,width:native.info.width,height:native.info.height,
    artwork:{width:artwork.width,height:artwork.height,rgba:artwork.rgba},artworkSource:artwork.source,isBundledBlank,bytes:native.bytes,info:native.info,
    roles:Object.fromEntries(Object.entries(native.roles).map(([key,r])=>[key,{index:r.index,name:r.name,embeddedID:r.embeddedID,width:r.child.width,height:r.child.height,transform:r.transform}]))};
}

function validateRaster(value) {
  if(!value||!Number.isInteger(value.width)||!Number.isInteger(value.height)||value.width<1||value.height<1||value.width>8192||value.height>8192||value.width*value.height>16000000)fail('Ảnh map vượt giới hạn 16 megapixel / 8192 px.');
  const n=value.width*value.height;
  if(value.rgba instanceof Uint8Array||value.rgba instanceof Uint8ClampedArray) {
    if(value.rgba.length!==n*4)fail('Dữ liệu RGBA map không khớp canvas.');
    return {width:value.width,height:value.height,rgba:value.rgba};
  }
  if(!(value.depth instanceof Float32Array)||value.depth.length!==n||value.alpha&&(!(value.alpha instanceof Uint8Array||value.alpha instanceof Uint8ClampedArray)||value.alpha.length!==n))fail('Cần RGBA 8-bit hoặc depth Float32 + alpha 8-bit cùng canvas.');
  const rgba=new Uint8ClampedArray(n*4);
  for(let i=0;i<n;i++) {
    const depth=value.depth[i];if(!Number.isFinite(depth))fail('Depth có giá trị không hợp lệ.');
    const gray=Math.round(Math.max(0,Math.min(1,depth))*255);rgba[i*4]=rgba[i*4+1]=rgba[i*4+2]=gray;rgba[i*4+3]=value.alpha?value.alpha[i]:255;
  }
  return {width:value.width,height:value.height,rgba};
}

// Premultiplied-alpha bilinear samples avoid black/white fringes on transparent art.
function sample(source,x,y,target,offset) {
  if(x<-.5||y<-.5||x>source.width-.5||y>source.height-.5)return;
  x=Math.max(0,Math.min(source.width-1,x));y=Math.max(0,Math.min(source.height-1,y));
  const x0=Math.floor(x),y0=Math.floor(y),x1=Math.min(source.width-1,x0+1),y1=Math.min(source.height-1,y0+1),fx=x-x0,fy=y-y0;
  const indices=[(y0*source.width+x0)*4,(y0*source.width+x1)*4,(y1*source.width+x0)*4,(y1*source.width+x1)*4];
  const weights=[(1-fx)*(1-fy),fx*(1-fy),(1-fx)*fy,fx*fy];let a=0,r=0,g=0,b=0;
  for(let k=0;k<4;k++){const at=indices[k],w=weights[k]*source.rgba[at+3];a+=w;r+=source.rgba[at]*w;g+=source.rgba[at+1]*w;b+=source.rgba[at+2]*w;}
  if(a>0){target[offset]=Math.round(r/a);target[offset+1]=Math.round(g/a);target[offset+2]=Math.round(b/a);target[offset+3]=Math.round(a);}
}

/** Registered full-canvas resize, not a second contain/fit of the artwork. */
export function resizeNativeRaster(value,width,height,{signal}={}) {
  const source=validateRaster(value);abort(signal);
  if(!Number.isInteger(width)||!Number.isInteger(height)||width<1||height<1||width*height>16000000||width>8192||height>8192)fail('Canvas map đích không hợp lệ.');
  if(source.width===width&&source.height===height)return source;
  const rgba=new Uint8ClampedArray(width*height*4);
  for(let y=0;y<height;y++){if(y%32===0)abort(signal);for(let x=0;x<width;x++)sample(source,(x+.5)*source.width/width-.5,(y+.5)*source.height/height-.5,rgba,(y*width+x)*4);}
  return {width,height,rgba};
}

function blackAlpha(value) {
  const source=validateRaster(value),rgba=new Uint8ClampedArray(source.rgba.length);
  for(let i=3;i<rgba.length;i+=4)rgba[i]=source.rgba[i];
  return {width:source.width,height:source.height,rgba};
}

function parentTile(source,role,parent,{signal}={}) {
  const [x0,y0,x1,y1]=role.transform;
  const left=Math.max(0,Math.floor(x0)),top=Math.max(0,Math.floor(y0)),right=Math.min(parent.width,Math.ceil(x1)),bottom=Math.min(parent.height,Math.ceil(y1));
  const width=right-left,height=bottom-top;if(width<1||height<1)fail('Role template nằm ngoài canvas.');
  const rgba=new Uint8ClampedArray(width*height*4);
  for(let y=0;y<height;y++){if(y%32===0)abort(signal);for(let x=0;x<width;x++)sample(source,(left+x+.5-x0)*source.width/(x1-x0)-.5,(top+y+.5-y0)*source.height/(y1-y0)-.5,rgba,(y*width+x)*4);}
  return {index:role.index,left,top,width,height,rgba};
}
function sourceOver(destination,parentWidth,tile) {
  for(let y=0;y<tile.height;y++)for(let x=0;x<tile.width;x++) {
    const at=(y*tile.width+x)*4,to=((tile.top+y)*parentWidth+tile.left+x)*4,sa=tile.rgba[at+3]/255,da=destination[to+3]/255,a=sa+da*(1-sa);
    if(!a)continue;
    for(let k=0;k<3;k++)destination[to+k]=Math.round((tile.rgba[at+k]*sa+destination[to+k]*da*(1-sa))/a);
    destination[to+3]=Math.round(a*255);
  }
}

/** Native Ceramic body below art; original alpha/cutouts remain authoritative. */
export function composeNativeSubstrate(foreground,substrate,{signal}={}) {
  const front=validateRaster(foreground),back=resizeNativeRaster(substrate,front.width,front.height,{signal});
  abort(signal);const rgba=Uint8ClampedArray.from(back.rgba);
  sourceOver(rgba,front.width,{...front,left:0,top:0});
  abort(signal);return {width:front.width,height:front.height,rgba};
}

/**
 * Returns a native family PSD with role contents and cached tiles updated.
 * No source file is written. Different map resolutions are resized by registered
 * full-canvas coordinates, retaining alignment with the original IN canvas.
 * Omit artwork to keep the uploaded IN PSB bytes, layers and visibility exact.
 * A real embedded composite refreshes the parent IN tile. A Photoshop white
 * compatibility placeholder uses and preserves the already-rendered IN cache.
 */
export async function buildNativePSD(bytes,{artwork,depth,cut,ceramicShape,signal}={}) {
  abort(signal);const native=inspectNative(bytes),{item,info,roles}=native;
  await validateNativeGeometry(native,signal);
  if(!depth)fail('Chưa có depth đã Generate để tạo PSD kết quả.','NO_DEPTH_RESULT');
  if(item.id!=='acrylic'&&!ceramicShape)fail('Cần shape Ceramic gốc đã xác minh để giữ nền IN và nền đen Depth.','CERAMIC_SHAPE_REQUIRED');
  const preserveInput=artwork===undefined;
  const savedInput=preserveInput?await nativeArtwork(native,signal):null;
  if(preserveInput)artwork=savedInput.artwork;
  const input=resizeNativeRaster(artwork,item.inputWidth,item.inputHeight,{signal});
  const depthImage=resizeNativeRaster(depth,item.inputWidth,item.inputHeight,{signal});
  const images={input,depth:depthImage},contents={
    input:[{name:'MITOM Artwork — web',...input,visible:true}],
    depth:[{name:'MITOM Depth RGB8 — web',...depthImage,visible:true}],
  };
  if(item.id!=='acrylic') {
    const shape=resizeNativeRaster(ceramicShape,item.inputWidth,item.inputHeight,{signal}),black=blackAlpha(shape);
    if(!preserveInput)images.input=composeNativeSubstrate(input,shape,{signal});
    images.depth=composeNativeSubstrate(depthImage,black,{signal});
    // Keep the base and the generated content as separate visible layers.
    // Original editable template helpers remain stored, hidden, below them.
    if(!preserveInput)contents.input.unshift({name:'MITOM Ceramic Body — web',...shape,visible:true});
    contents.depth.unshift({name:'MITOM Ceramic Black Base — web',...black,visible:true});
  }
  if(roles.cut) {
    images.cut=resizeNativeRaster(cut||blackAlpha(depthImage),item.inputWidth,item.inputHeight,{signal});
    contents.cut=[{name:'MITOM Cut — web',...images.cut,visible:true}];
  }
  const embedded=[],tiles=[],composite={width:info.width,height:info.height,rgba:new Uint8ClampedArray(info.width*info.height*4)};
  for(const [key,role] of Object.entries(roles)) {
    abort(signal);const image=images[key];
    if(key!=='input'||!preserveInput) {
      const generated=appendPSDLayers(role.embeddedBytes,contents[key],{allowEmbeddedPSB:true,hideOriginalLayers:true,composite:image,signal});
      embedded.push({id:role.embeddedID,bytes:generated});
    }
    // A placeholder PSB must not overwrite its good rendered parent cache.
    // Preserve those original cache channels byte-for-byte as well as the
    // editable IN PSB. Use the same original cache in the output composite.
    const tile=key==='input'&&savedInput?.cache?savedInput.cache:parentTile(image,role,info,{signal});
    if(!(key==='input'&&savedInput?.cache))tiles.push(tile);
    sourceOver(composite.rgba,info.width,tile);
  }
  let result=replaceEmbeddedPSDs(native.bytes,embedded,{signal});
  const roleIndices=new Set(Object.values(roles).map(role=>role.index));
  result=setPSDLayerVisibility(result,info.layers.map(layer=>({index:layer.index,visible:roleIndices.has(layer.index)})),{signal});
  result=replacePSDLayerPixels(result,tiles,{composite,signal});
  abort(signal);return result;
}

