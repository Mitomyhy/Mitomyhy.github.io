/**
 * Bounded, additive PSD transport for the browser 3D workspace.
 *
 * This is NOT a Photoshop renderer or an ag-psd read/write round trip. It reads
 * the saved RGB8 composite and adds raster layers without interpreting or
 * rewriting existing layer/effect/text/smart-object bytes. Unsupported files
 * fail explicitly. Original Photoshop effects are not rerendered in the web.
 * Format: https://www.adobe.com/devnet-apps/photoshop/fileformatashtml/
 */
export const PSD_LIMITS = Object.freeze({inputBytes:256*1024*1024, outputBytes:512*1024*1024,
  pixels:32*1024*1024, edge:16384, layers:1000, addedLayers:12});

const utf8 = new TextDecoder('windows-1252');
const ALT_LAYER_KEYS = new Set(['Layr','Lr16','Lr32']);
const LONG_KEYS = new Set(['LMsk','Lr16','Lr32','Layr','Mt16','Mt32','Mtrn','Alph','FMsk','lnk2','FEid','FXid','PxSD']);
const abort = signal => { if(signal?.aborted) throw Object.assign(new Error('Đã hủy đọc PSD.'),{name:'AbortError',code:'CANCELLED'}); };
function fail(message){ throw Error(message); }
function inputBytes(value){
  if(value instanceof Uint8Array)return value;
  if(value instanceof ArrayBuffer)return new Uint8Array(value);
  if(ArrayBuffer.isView(value))return new Uint8Array(value.buffer,value.byteOffset,value.byteLength);
  fail('Cần dữ liệu nhị phân PSD.');
}
function bounds(bytes,start,size,end=bytes.length){
  if(!Number.isSafeInteger(start)||!Number.isSafeInteger(size)||start<0||size<0||start>end||size>end-start)fail('PSD bị thiếu hoặc sai độ dài dữ liệu.');
}
function reader(bytes,start=0,end=bytes.length){
  let p=start;const view=new DataView(bytes.buffer,bytes.byteOffset,bytes.byteLength);
  return {get pos(){return p;},set pos(v){bounds(bytes,v,0,end);p=v;},
    take(n){bounds(bytes,p,n,end);const a=p;p+=n;return bytes.subarray(a,p);},
    skip(n){this.take(n);},u8(){return this.take(1)[0];},
    u16(){bounds(bytes,p,2,end);const v=view.getUint16(p);p+=2;return v;},
    i16(){bounds(bytes,p,2,end);const v=view.getInt16(p);p+=2;return v;},
    u32(){bounds(bytes,p,4,end);const v=view.getUint32(p);p+=4;return v;},
    u64(){bounds(bytes,p,8,end);const v=view.getBigUint64(p);p+=8;if(v>BigInt(Number.MAX_SAFE_INTEGER))fail('Độ dài PSD vượt số nguyên an toàn.');return Number(v);},
    i32(){bounds(bytes,p,4,end);const v=view.getInt32(p);p+=4;return v;},
    str(n){return String.fromCharCode(...this.take(n));}};
}
function shape(width,height){
  if(!Number.isInteger(width)||!Number.isInteger(height)||width<1||height<1||width>PSD_LIMITS.edge||height>PSD_LIMITS.edge||width*height>PSD_LIMITS.pixels)fail('Canvas PSD vượt giới hạn 32 megapixel / 16384 px.');
}
function section(r,long=false){const at=r.pos,length=long?r.u64():r.u32(),start=r.pos;r.skip(length);return {at,start,length,end:r.pos};}
function zeroPad(bytes,start,end){for(let i=start;i<end;i++)if(bytes[i]!==0)return false;return true;}
function imageResources(bytes,section){
  const r=reader(bytes,section.start,section.end);let iccProfileBytes=null,count=0;
  while(r.pos<section.end){
    if(++count>10000)fail('PSD có quá nhiều image resource.');
    if(section.end-r.pos<12)fail('Image resource PSD bị thiếu.');
    if(r.str(4)!=='8BIM')fail('Chữ ký image resource PSD không hợp lệ.');
    const id=r.u16(),nameLength=r.u8();r.skip(nameLength);if((nameLength+1)%2)r.skip(1);
    const length=r.u32(),payload=r.take(length);if(length%2)r.skip(1);
    if(id===1039){if(iccProfileBytes!==null||length<128||length>16*1024*1024)fail('ICC profile PSD bị trùng hoặc sai kích thước.');iccProfileBytes=payload;}
  }
  return {iccProfileBytes};
}

function parse(value,{allowPSB=false}={}){
  const bytes=inputBytes(value);
  if(bytes.byteLength>PSD_LIMITS.inputBytes)fail('PSD tối đa 256 MB.');
  const r=reader(bytes);
  if(r.str(4)!=='8BPS')fail('Không phải file Photoshop PSD hợp lệ.');
  const version=r.u16();if(version!==1&&!(allowPSB&&version===2))fail('Chỉ nhận PSD chuẩn; PSB chỉ dùng bên trong Smart Object gốc.');
  const reserved=r.take(6);if(reserved.some(v=>v!==0))fail('Header PSD không hợp lệ.');
  const channels=r.u16(),height=r.u32(),width=r.u32(),depth=r.u16(),colorMode=r.u16();
  shape(width,height);
  if(depth!==8||colorMode!==3)fail('Web hiện chỉ nhận PSD RGB 8-bit. Giữ bản gốc và lưu một bản RGB 8-bit để dùng trên web.');
  if(channels<3||channels>56)fail('Số kênh PSD không hợp lệ.');
  if(width*height*channels>PSD_LIMITS.inputBytes)fail('Composite PSD giải nén vượt giới hạn bộ nhớ 256 MB.');
  const color=section(r),resources=section(r),layerMask=section(r,version===2),imageStart=r.pos;
  const {iccProfileBytes}=imageResources(bytes,resources);
  bounds(bytes,imageStart,2);const compression=r.u16();if(compression>3)fail('PSD dùng kiểu nén composite chưa hỗ trợ.');
  let signedCount=0,layerCount=0,recordsStart=0,recordsEnd=0,channelStart=0,channelEnd=0,layerInfo=null,tailStart=layerMask.start;
  const layers=[],globalTags=[];let maxLayerId=0;
  if(layerMask.length){
    const lr=reader(bytes,layerMask.start,layerMask.end);layerInfo=section(lr,version===2);tailStart=layerInfo.end;
    if(layerInfo.length){
      const ir=reader(bytes,layerInfo.start,layerInfo.end);signedCount=ir.i16();layerCount=Math.abs(signedCount);
      if(layerCount>PSD_LIMITS.layers)fail('PSD có quá nhiều layer (tối đa 1000).');
      recordsStart=ir.pos;let totalChannelBytes=0;
      for(let i=0;i<layerCount;i++){
        const recordStart=ir.pos,top=ir.i32(),left=ir.i32(),bottom=ir.i32(),right=ir.i32(),count=ir.u16();
        if(bottom<top||right<left||count>56)fail('Kích thước / kênh layer PSD không hợp lệ.');
        const channelInfo=[];
        for(let c=0;c<count;c++){const id=ir.i16(),length=version===2?ir.u64():ir.u32();channelInfo.push({id,length});totalChannelBytes+=length;}
        if(totalChannelBytes>bytes.length)fail('Dữ liệu kênh layer PSD vượt file.');
        if(ir.str(4)!=='8BIM')fail('Chữ ký blend mode layer PSD không hợp lệ.');
        const blendMode=ir.str(4),opacity=ir.u8(),clipping=ir.u8(),flags=ir.u8();ir.skip(1);
        const extra=section(ir),er=reader(bytes,extra.start,extra.end);
        const layerMaskBytes=section(er).length;section(er); // Mask and blending ranges remain opaque.
        const nameLength=er.u8(),nameBytes=er.take(nameLength);let name=utf8.decode(nameBytes);
        er.skip((4-(nameLength+1)%4)%4);
        const keys=[];let placedID=null,placedTransform=null,placedWarpBytes=null,placedObjectBytes=null;
        while(er.pos<extra.end){
          if(extra.end-er.pos<12){if(zeroPad(bytes,er.pos,extra.end))break;fail('Block layer PSD bị thiếu.');}
          const signature=er.str(4);if(!['8BIM','8B64'].includes(signature))fail('Block thông tin layer PSD không hợp lệ.');
          const key=er.str(4),length=signature==='8B64'||version===2&&LONG_KEYS.has(key)?er.u64():er.u32(),start=er.pos;keys.push(key);er.skip(length);
          if(length%2)er.skip(1);
          if(key==='lyid'&&length>=4){const id=new DataView(bytes.buffer,bytes.byteOffset+start,4).getUint32(0);maxLayerId=Math.max(maxLayerId,id);}
          if(key==='luni'&&length>=4){const ur=reader(bytes,start,start+length),n=ur.u32();if(n<=100000&&n*2<=length-4){let text='';for(let k=0;k<n;k++)text+=String.fromCharCode(ur.u16());name=text.replace(/\0+$/,'');}}
          if(key==='PlLd'&&length>=9){const pr=reader(bytes,start,start+length);if(pr.str(4)==='plcL'&&pr.u32()===3){const n=pr.u8();placedID=pr.str(n);if(start+length-pr.pos>=80){pr.skip(16);const transform=new DataView(bytes.buffer,bytes.byteOffset+pr.pos,64);placedTransform=Array.from({length:8},(_,i)=>transform.getFloat64(i*8));pr.skip(64);placedWarpBytes=bytes.subarray(pr.pos,start+length);}}}
          if(key==='SoLd'||key==='SoLE')placedObjectBytes=bytes.subarray(start,start+length);
        }
        layers.push({index:i,name,top,left,bottom,right,width:right-left,height:bottom-top,visible:!(flags&2),opacity,blendMode,clipping,keys,placedID,placedTransform,placedWarpBytes,placedObjectBytes,layerMaskBytes,channelInfo,recordStart,recordEnd:ir.pos});
      }
      recordsEnd=channelStart=ir.pos;channelEnd=channelStart+totalChannelBytes;
      bounds(bytes,channelStart,totalChannelBytes,layerInfo.end);
      if(layerInfo.end-channelEnd>3||!zeroPad(bytes,channelEnd,layerInfo.end))fail('PSD có dữ liệu layer chưa nhận dạng; không sửa file này.');
    }
    // Duplicate alternate layer stacks would supersede additions; do not guess.
    if(tailStart<layerMask.end){
      const tr=reader(bytes,tailStart,layerMask.end);section(tr);
      while(tr.pos<layerMask.end){
        if(layerMask.end-tr.pos<12){if(zeroPad(bytes,tr.pos,layerMask.end))break;fail('Thông tin layer toàn cục bị thiếu.');}
        const at=tr.pos,sig=tr.str(4);if(!['8BIM','8B64'].includes(sig))fail('Thông tin layer toàn cục chưa được hỗ trợ.');
        const key=tr.str(4),long=sig==='8B64'||version===2&&LONG_KEYS.has(key),length=long?tr.u64():tr.u32(),start=tr.pos;if(ALT_LAYER_KEYS.has(key))fail('PSD có layer stack thay thế; không xuất lại để tránh mất layer.');
        tr.skip(length);tr.skip((4-length%4)%4);
        globalTags.push({at,signature:sig,key,long,start,length,end:tr.pos});
      }
    }
  }
  return {bytes,width,height,channels,depth,colorMode,version,color,resources,layerMask,imageStart,compression,
    signedCount,layerCount,recordsStart,recordsEnd,channelStart,channelEnd,layerInfo,tailStart,layers,maxLayerId,globalTags,iccProfileBytes};
}

export function inspectPSD(bytes,{allowEmbeddedPSB=false}={}){
  const p=parse(bytes,{allowPSB:allowEmbeddedPSB});return {width:p.width,height:p.height,channels:p.channels,depth:p.depth,colorMode:p.colorMode,version:p.version,iccProfileBytes:p.iccProfileBytes,
    compression:p.compression,layerCount:p.layerCount,mergedAlpha:p.signedCount<0,
    layers:p.layers.map(({recordStart,recordEnd,channelInfo,...layer})=>layer),
    transport:'preserve-original-psd-sections-add-raster-layers',artworkSource:'saved-composite-not-smart-object-rerender'};
}

function unpackRow(input,out,offset,width){
  let i=0,p=offset,end=offset+width;
  while(i<input.length){const count=input[i++];if(count===128)continue;
    if(count<128){const n=count+1;if(n>input.length-i||n>end-p)fail('RLE composite PSD sai độ dài.');out.set(input.subarray(i,i+n),p);i+=n;p+=n;}
    else{const n=257-count;if(i>=input.length||n>end-p)fail('RLE composite PSD sai độ dài.');out.fill(input[i++],p,p+n);p+=n;}
  }
  if(p!==end)fail('RLE composite PSD bị thiếu pixel.');
}
async function boundedInflate(bytes,length,signal){
  if(typeof DecompressionStream!=='function')fail('Trình duyệt chưa hỗ trợ giải nén ZIP của PSD; dùng Chrome / Edge mới.');
  const stream=new Blob([bytes]).stream().pipeThrough(new DecompressionStream('deflate')),r=stream.getReader(),out=new Uint8Array(length);let p=0;
  try{while(true){abort(signal);const {done,value}=await r.read();if(done)break;if(value.length>length-p)fail('ZIP composite PSD giải nén vượt kích thước đã khai báo.');out.set(value,p);p+=value.length;}if(p!==length)fail('ZIP composite PSD bị thiếu pixel.');return out;}
  finally{try{await r.cancel();}catch{}r.releaseLock();}
}

/** Reads the Photoshop-saved composite only, never flattens/reinterprets layers. */
export async function decodePSDComposite(value,{signal,allowEmbeddedPSB=false}={}){
  abort(signal);const p=parse(value,{allowPSB:allowEmbeddedPSB}),{bytes,width,height,channels,compression}=p,n=width*height,size=n*channels;
  let planar;
  const r=reader(bytes,p.imageStart+2);
  if(compression===0){planar=r.take(size);if(r.pos!==bytes.length)fail('Raw composite PSD có dữ liệu thừa.');}
  else if(compression===1){
    const rows=channels*height,lengths=new Uint32Array(rows);let total=0;
    for(let i=0;i<rows;i++){lengths[i]=p.version===2?r.u32():r.u16();total+=lengths[i];}
    bounds(bytes,r.pos,total);if(total!==bytes.length-r.pos)fail('RLE composite PSD có dữ liệu thiếu / thừa.');
    planar=new Uint8Array(size);
    for(let i=0;i<rows;i++){if(i%64===0)abort(signal);unpackRow(r.take(lengths[i]),planar,i*width,width);}
  } else {
    planar=await boundedInflate(bytes.subarray(r.pos),size,signal);
    if(compression===3)for(let row=0;row<height*channels;row++){const start=row*width;for(let x=1;x<width;x++)planar[start+x]=(planar[start+x]+planar[start+x-1])&255;}
  }
  abort(signal);const rgba=new Uint8ClampedArray(n*4),hasAlpha=p.signedCount<0&&channels>=4;
  for(let i=0;i<n;i++){rgba[i*4]=planar[i];rgba[i*4+1]=planar[n+i];rgba[i*4+2]=planar[n*2+i];rgba[i*4+3]=hasAlpha?planar[n*3+i]:255;}
  // Photoshop composites (unlike layer channels) store a white matte beneath
  // partial merged transparency. Undo it before Canvas/AI consumes straight RGBA.
  if(hasAlpha)for(let i=0;i<n;i++){const alpha=rgba[i*4+3];if(alpha>0&&alpha<255)for(let c=0;c<3;c++)rgba[i*4+c]=(rgba[i*4+c]-(255-alpha))*255/alpha;}
  return {width,height,rgba,depth:8,source:'photoshop-saved-composite',hasTransparency:hasAlpha};
}

function concat(parts,max=PSD_LIMITS.outputBytes){
  const length=parts.reduce((n,p)=>n+p.length,0);if(length>max)fail('PSD kết quả vượt giới hạn 512 MB.');
  const out=new Uint8Array(length);let offset=0;for(const part of parts){out.set(part,offset);offset+=part.length;}return out;
}
function num(value,size,signed=false){const out=new Uint8Array(size),v=new DataView(out.buffer);if(size===8)v.setBigUint64(0,BigInt(value));else if(size===2)signed?v.setInt16(0,value):v.setUint16(0,value);else signed?v.setInt32(0,value):v.setUint32(0,value);return out;}
const ascii=value=>Uint8Array.from(value,c=>c.charCodeAt(0));
function tagged(key,data){return concat([ascii('8BIM'+key),num(data.length,4),data,...(data.length%2?[new Uint8Array(1)]:[])]);}
function packRow(row){
  const out=[];let i=0;
  while(i<row.length){
    let run=1;while(run<128&&i+run<row.length&&row[i+run]===row[i])run++;
    if(run>=3){out.push(257-run,row[i]);i+=run;continue;}
    const start=i;i+=run;
    while(i<row.length&&i-start<128){run=1;while(run<3&&i+run<row.length&&row[i+run]===row[i])run++;if(run>=3)break;i+=Math.min(run,128-(i-start));}
    out.push(i-start-1,...row.subarray(start,i));
  }
  return Uint8Array.from(out);
}
function encodePlanar(planes,width,height,version=1){
  const rows=[],lengths=[],lengthSize=version===2?4:2;let total=2+planes.length*height*lengthSize;
  for(const plane of planes)for(let y=0;y<height;y++){const row=packRow(plane.subarray(y*width,(y+1)*width));if(version===1&&row.length>65535)fail('Hàng RLE PSD vượt giới hạn.');rows.push(row);lengths.push(num(row.length,lengthSize));total+=row.length;}
  const rawLength=2+width*height*planes.length;
  return total<rawLength?concat([num(1,2),...lengths,...rows]):concat([num(0,2),...planes]);
}
function planesRGBA(rgba,width,height,channelCount=4){
  const n=width*height;
  if(!(rgba instanceof Uint8Array||rgba instanceof Uint8ClampedArray)||rgba.length!==n*4)fail('Layer mới phải có dữ liệu RGBA 8-bit đúng kích thước.');
  const result=[];for(let c=0;c<channelCount;c++){const out=new Uint8Array(n);for(let i=0;i<n;i++)out[i]=rgba[i*4+c];result.push(out);}return result;
}
function compositePlanes(p,rgba){
  const planes=planesRGBA(rgba,p.width,p.height,p.channels);
  if(p.signedCount<0&&p.channels>=4)for(let i=0;i<p.width*p.height;i++){const alpha=rgba[i*4+3];if(alpha>0&&alpha<255)for(let c=0;c<3;c++)planes[c][i]=Math.round(rgba[i*4+c]*alpha/255+(255-alpha));}
  return planes;
}
function rasterRecord(layer,id,document){
  const {width,height}=layer;shape(width,height);
  const left=layer.left??0,top=layer.top??0;
  if(!Number.isInteger(left)||!Number.isInteger(top)||Math.abs(left)>PSD_LIMITS.edge||Math.abs(top)>PSD_LIMITS.edge||left+width>document.width||top+height>document.height||left<0||top<0)fail('Layer mới phải nằm trong canvas PSD.');
  const name=String(layer.name||'MITOM generated map').replace(/[\u0000-\u001f\u007f]/g,' ').slice(0,255);
  const latin=Uint8Array.from(name,c=>c.charCodeAt(0)<127?c.charCodeAt(0):63),pascal=concat([Uint8Array.of(latin.length),latin,new Uint8Array((4-(latin.length+1)%4)%4)]);
  const unicode=new Uint8Array(4+name.length*2),uv=new DataView(unicode.buffer);uv.setUint32(0,name.length);for(let i=0;i<name.length;i++)uv.setUint16(4+i*2,name.charCodeAt(i));
  const extra=concat([num(0,4),num(0,4),pascal,tagged('luni',unicode),tagged('lyid',num(id,4))]);
  const channels=planesRGBA(layer.rgba,width,height),encoded=channels.map(plane=>encodePlanar([plane],width,height,document.version));
  const channelInfo=encoded.flatMap((channel,i)=>[num(i===3?-1:i,2,true),num(channel.length,document.version===2?8:4)]);
  const record=concat([num(top,4,true),num(left,4,true),num(top+height,4,true),num(left+width,4,true),num(4,2),...channelInfo,
    ascii('8BIMnorm'),Uint8Array.of(255,0,layer.visible===true?0:2,0),num(extra.length,4),extra]);
  return {record,channels:encoded};
}

/**
 * Adds raster layers above the original stack. By default all additions are
 * hidden and the saved composite is unchanged. Existing records/channels,
 * resources, linked files, text/effects and global blocks are copied verbatim.
 * To show a new layer immediately, caller MUST provide the complete correct
 * composite RGBA; we never pretend to rerender underlying Photoshop layers.
 */
export function appendPSDLayers(originalBytes,layers,{composite,signal,allowEmbeddedPSB=false,hideOriginalLayers=false}={}){
  abort(signal);const p=parse(originalBytes,{allowPSB:allowEmbeddedPSB}),{bytes}=p;
  if(!Array.isArray(layers)||layers.length<1||layers.length>PSD_LIMITS.addedLayers)fail('Thêm từ 1 đến 12 layer kết quả vào PSD.');
  if(p.layerCount+layers.length>PSD_LIMITS.layers)fail('PSD kết quả có quá nhiều layer.');
  if(p.maxLayerId+layers.length>0x7fffffff)fail('PSD không còn ID layer khả dụng.');
  if(layers.some(layer=>layer.visible===true)&&!composite)fail('Layer hiện cần composite kết quả hoàn chỉnh. Hãy để layer map ẩn hoặc cung cấp composite.');
  if(composite&&(p.channels>4||composite.width!==p.width||composite.height!==p.height))fail('Composite mới phải cùng canvas RGB/RGBA của PSD (không quá 4 kênh).');
  const additions=[];for(let i=0;i<layers.length;i++){abort(signal);additions.push(rasterRecord(layers[i],p.maxLayerId+i+1,p));}
  const oldRecords=p.layerCount?(hideOriginalLayers?bytes.slice(p.recordsStart,p.recordsEnd):bytes.subarray(p.recordsStart,p.recordsEnd)):new Uint8Array(0);
  if(hideOriginalLayers)for(const layer of p.layers){const flagAt=layer.recordStart-p.recordsStart+18+layer.channelInfo.length*(p.version===2?10:6)+10;oldRecords[flagAt]|=2;}
  const oldChannels=p.layerCount?bytes.subarray(p.channelStart,p.channelEnd):new Uint8Array(0);
  // Native IN Smart Objects are commonly saved with 3 composite channels even
  // though individual layers have transparency. A new RGBA composite therefore
  // explicitly promotes its header to 4 channels + negative layer count.
  const count=p.layerCount+layers.length,mergedAlpha=p.signedCount<0||!!composite,signed=mergedAlpha?-count:count;
  // PSD layer records/channel blocks are bottom-to-top. Append at the end so
  // original indices/group nesting remain stable and additions are topmost.
  let info=concat([num(signed,2,true),oldRecords,...additions.map(a=>a.record),oldChannels,...additions.flatMap(a=>a.channels)]);
  if(info.length%2)info=concat([info,new Uint8Array(1)]);
  const tail=p.layerMask.length&&p.tailStart<p.layerMask.end?bytes.subarray(p.tailStart,p.layerMask.end):num(0,4);
  const lengthSize=p.version===2?8:4,mask=concat([num(info.length,lengthSize),info,tail]);
  let image=bytes.subarray(p.imageStart),prefix=bytes.subarray(0,p.layerMask.at);
  if(composite){const compositeInfo={...p,channels:4,signedCount:-count};image=encodePlanar(compositePlanes(compositeInfo,composite.rgba),p.width,p.height,p.version);prefix=prefix.slice();new DataView(prefix.buffer,prefix.byteOffset,prefix.byteLength).setUint16(12,4);}
  abort(signal);
  return concat([prefix,num(mask.length,lengthSize),mask,image]);
}

/** Reads a local File only. No HTTP, uploads, Photoshop bridge or credentials. */
export async function readPSDFile(file,{signal}={}){
  abort(signal);if(!(file instanceof Blob)||file.size>PSD_LIMITS.inputBytes)fail('Chọn PSD RGB 8-bit, tối đa 256 MB.');
  const bytes=new Uint8Array(await file.arrayBuffer());abort(signal);
  const info=inspectPSD(bytes),composite=await decodePSDComposite(bytes,{signal});
  return {bytes,info,...composite,name:String(file.name||'artwork.psd')};
}

function unicodeString(r){const count=r.u32();if(count>100000)fail('Tên tài nguyên PSD quá dài.');let out='';for(let i=0;i<count;i++)out+=String.fromCharCode(r.u16());return out.replace(/\0+$/,'');}
function descriptorKey(r){const n=r.u32();r.skip(n||4);}
function skipDescriptor(r,depth=0){
  if(depth>12)fail('Descriptor Smart Object lồng quá sâu.');unicodeString(r);descriptorKey(r);const count=r.u32();if(count>10000)fail('Descriptor Smart Object quá lớn.');
  for(let i=0;i<count;i++){descriptorKey(r);skipDescriptorValue(r,r.str(4),depth+1);}
}
function skipDescriptorValue(r,type,depth){
  if(depth>12)fail('Descriptor Smart Object lồng quá sâu.');
  if(type==='Objc'||type==='GlbO')return skipDescriptor(r,depth);
  if(type==='TEXT'){unicodeString(r);return;}
  if(type==='long'){r.skip(4);return;}if(type==='comp'||type==='doub'){r.skip(8);return;}if(type==='bool'){r.skip(1);return;}if(type==='UntF'){r.skip(12);return;}
  if(type==='enum'){descriptorKey(r);descriptorKey(r);return;}
  if(type==='type'||type==='GlbC'){unicodeString(r);descriptorKey(r);return;}
  if(type==='VlLs'){const count=r.u32();if(count>10000)fail('Danh sách Descriptor quá lớn.');for(let i=0;i<count;i++)skipDescriptorValue(r,r.str(4),depth+1);return;}
  if(['tdta','alis','Pth '].includes(type)){r.skip(r.u32());return;}
  fail('File-open Descriptor Smart Object dùng kiểu chưa hỗ trợ: '+type+'.');
}
function linkedRecords(p,tag){
  const r=reader(p.bytes,tag.start,tag.start+tag.length),records=[];
  while(r.pos<tag.start+tag.length){
    if(tag.start+tag.length-r.pos<8){if(zeroPad(p.bytes,r.pos,tag.start+tag.length))break;fail('Linked file PSD bị thiếu.');}
    const at=r.pos,length=r.u64(),start=r.pos;if(length===0&&zeroPad(p.bytes,r.pos,tag.start+tag.length))break;
    bounds(p.bytes,start,length,tag.start+tag.length);const lr=reader(p.bytes,start,start+length),kind=lr.str(4),version=lr.u32(),id=lr.str(lr.u8()),name=unicodeString(lr),fileType=lr.str(4),creator=lr.str(4),sizeAt=lr.pos,size=lr.u64(),descriptor=lr.u8();
    if(descriptor){if(lr.u32()!==16)fail('File-open Descriptor không đúng phiên bản.');skipDescriptor(lr);}
    const dataAt=lr.pos;lr.skip(kind==='liFD'?size:0);r.pos=start+length;r.skip((4-length%4)%4);
    records.push({at,start,length,end:r.pos,kind,version,id,name,fileType,creator,sizeAt,size,dataAt,dataEnd:lr.pos});
  }
  return records;
}

/** Exact embedded bytes are returned as views; external linked files are never fetched. */
export function readPSDLinkedFiles(originalBytes){
  const p=parse(originalBytes),files=[];
  for(const tag of p.globalTags)if(['lnkD','lnk2','lnk3'].includes(tag.key))for(const record of linkedRecords(p,tag)){
    files.push({id:record.id,name:record.name,kind:record.kind,version:record.version,fileType:record.fileType,
      bytes:record.kind==='liFD'?p.bytes.subarray(record.dataAt,record.dataEnd):null});
  }
  return files;
}

/** Uses the exact PlLd placed-file ID, not a guessed filename or composite mosaic. */
export async function readPSDArtworkLayer(originalBytes,{layerName='layer in',signal}={}){
  abort(signal);const p=parse(originalBytes),matches=p.layers.filter(layer=>layer.name.trim().toLowerCase()===layerName.trim().toLowerCase());
  if(matches.length!==1||!matches[0].placedID)fail('Không xác định duy nhất Smart Object layer IN trong PSD này.');
  const layer=matches[0],files=readPSDLinkedFiles(originalBytes).filter(file=>file.id===layer.placedID&&file.kind==='liFD');
  if(files.length!==1)fail('Smart Object IN phải được nhúng trong PSD; không dùng đường dẫn file ngoài máy.');
  const file=files[0],image=await decodePSDComposite(file.bytes,{signal,allowEmbeddedPSB:true});
  return {...image,embeddedID:file.id,embeddedName:file.name,embeddedBytes:file.bytes,parentLayer:{name:layer.name,left:layer.left,top:layer.top,width:layer.width,height:layer.height},
    source:'photoshop-saved-embedded-IN-composite'};
}

function rewriteMask(p,mask,image=p.bytes.subarray(p.imageStart),{promoteAlpha=false}={}){
  let prefix=p.bytes.subarray(0,p.layerMask.at);
  if(promoteAlpha){prefix=prefix.slice();new DataView(prefix.buffer,prefix.byteOffset,prefix.byteLength).setUint16(12,4);}
  return concat([prefix,num(mask.length,p.version===2?8:4),mask,image]);
}
function replacementComposite(p,composite){
  if(!composite)return p.bytes.subarray(p.imageStart);
  if(p.channels>4||composite.width!==p.width||composite.height!==p.height)fail('Composite thay thế phải cùng canvas PSD RGB/RGBA.');
  return encodePlanar(compositePlanes(p,composite.rgba),p.width,p.height,p.version);
}

/** Replaces saved composite only; original layer records/channel bytes stay intact. */
export function replacePSDComposite(originalBytes,composite,{allowEmbeddedPSB=false,signal}={}){
  abort(signal);const p=parse(originalBytes,{allowPSB:allowEmbeddedPSB});
  return concat([p.bytes.subarray(0,p.imageStart),replacementComposite(p,composite)]);
}

/**
 * Replaces selected cached raster channels. Optional bounds changes are allowed
 * only for layers without user/vector masks. It does
 * not apply transforms/effects/warps: caller must supply already-correct pixels.
 * All other layer records, channels, masks and global bytes remain unchanged.
 */
export function replacePSDLayerPixels(originalBytes,replacements,{composite,allowEmbeddedPSB=false,signal}={}){
  abort(signal);const p=parse(originalBytes,{allowPSB:allowEmbeddedPSB});
  if(!Array.isArray(replacements)||!replacements.length||replacements.length>PSD_LIMITS.addedLayers)fail('Cần từ 1 đến 12 layer thay thế.');
  const selected=new Map();
  for(const replacement of replacements){
    const found=p.layers.filter(layer=>replacement.index!==undefined?layer.index===replacement.index:layer.name===replacement.name);
    if(found.length!==1)fail('Tên / chỉ số layer thay thế không xác định duy nhất.');const layer=found[0];
    const width=replacement.width,height=replacement.height,left=replacement.left??layer.left,top=replacement.top??layer.top;shape(width,height);
    if(selected.has(layer.index)||!Number.isInteger(left)||!Number.isInteger(top)||left<0||top<0||left+width>p.width||top+height>p.height)fail('Kích thước / vị trí layer thay thế ngoài canvas.');
    if((width!==layer.width||height!==layer.height||left!==layer.left||top!==layer.top)&&(layer.layerMaskBytes||layer.keys.some(key=>['vmsk','vsms'].includes(key))||layer.channelInfo.some(channel=>channel.id<-1)))fail('Không thay đổi bounds layer có mask; cần renderer Photoshop.');
    const planes=planesRGBA(replacement.rgba,width,height),encoded=new Map();
    for(const channel of layer.channelInfo)if(channel.id>=0&&channel.id<3||channel.id===-1)encoded.set(channel.id,encodePlanar([planes[channel.id===-1?3:channel.id]],width,height,p.version));
    if(![0,1,2].every(id=>encoded.has(id)))fail('Layer thay thế thiếu kênh RGB; không chuyển đổi layer kiểu khác.');
    selected.set(layer.index,{encoded,width,height,left,top});
  }
  const records=[],channels=[];let channelOffset=p.channelStart;
  for(const layer of p.layers){
    const change=selected.get(layer.index),selectedChannels=change?.encoded,record=p.bytes.slice(layer.recordStart,layer.recordEnd),view=new DataView(record.buffer);
    if(change){view.setInt32(0,change.top);view.setInt32(4,change.left);view.setInt32(8,change.top+change.height);view.setInt32(12,change.left+change.width);}
    layer.channelInfo.forEach((channel,i)=>{
      const encoded=selectedChannels?.get(channel.id),old=p.bytes.subarray(channelOffset,channelOffset+channel.length);channelOffset+=channel.length;
      channels.push(encoded||old);
      if(encoded){const offset=18+i*(p.version===2?10:6)+2;if(p.version===2)view.setBigUint64(offset,BigInt(encoded.length));else view.setUint32(offset,encoded.length);}
    });
    records.push(record);abort(signal);
  }
  // A Photoshop-saved RGB parent may have no merged alpha even when its IN /
  // DEPTH / CUT layer caches are RGBA. Writing a new transparent layout must
  // promote BOTH the header and the signed layer count before encoding RGBA.
  const promoteAlpha=!!composite&&p.channels<=4;
  const compositeInfo=promoteAlpha?{...p,channels:4,signedCount:-p.layerCount}:p;
  let info=concat([num(compositeInfo.signedCount,2,true),...records,...channels]);if(info.length%2)info=concat([info,new Uint8Array(1)]);
  const tail=p.bytes.subarray(p.tailStart,p.layerMask.end),mask=concat([num(info.length,p.version===2?8:4),info,tail]);
  return rewriteMask(p,mask,replacementComposite(compositeInfo,composite),{promoteAlpha});
}

/** Changes explicit visibility flags only; no layers are deleted or flattened. */
export function setPSDLayerVisibility(originalBytes,changes,{allowEmbeddedPSB=false,signal}={}){
  abort(signal);const p=parse(originalBytes,{allowPSB:allowEmbeddedPSB}),out=p.bytes.slice(),seen=new Set();
  if(!Array.isArray(changes)||changes.length>PSD_LIMITS.layers)fail('Danh sách hiển thị layer không hợp lệ.');
  for(const change of changes){const layer=p.layers[change.index];if(!layer||seen.has(change.index)||typeof change.visible!=='boolean')fail('Chỉ số / trạng thái layer không hợp lệ.');seen.add(change.index);
    const at=layer.recordStart+18+layer.channelInfo.length*(p.version===2?10:6)+10;out[at]=change.visible?out[at]&~2:out[at]|2;
  }
  return out;
}

/**
 * Surgical replacement of liFD embedded PSD/PSB files. Other embedded files,
 * linked metadata, parent layer records and cached parent pixels stay intact.
 * IMPORTANT: parent caches must be explicitly updated separately by the caller.
 */
export function replaceEmbeddedPSDs(originalBytes,replacements,{signal}={}){
  abort(signal);const p=parse(originalBytes);
  if(!Array.isArray(replacements)||!replacements.length||replacements.length>PSD_LIMITS.addedLayers)fail('Cần tài nguyên nhúng thay thế.');
  const targets=new Map();for(const replacement of replacements){if(targets.has(replacement.id))fail('ID tài nguyên nhúng bị trùng.');const data=inputBytes(replacement.bytes);parse(data,{allowPSB:true});targets.set(replacement.id,data);}
  const found=new Set(),parts=[];let offset=p.layerMask.start;
  for(const tag of p.globalTags){
    if(!['lnkD','lnk2','lnk3'].includes(tag.key))continue;
    const records=linkedRecords(p,tag),pieces=[];let recordOffset=tag.start,changed=false;
    for(const record of records){
      const replacement=targets.get(record.id);if(!replacement)continue;
      if(record.kind!=='liFD'||found.has(record.id))fail('Tài nguyên nhúng phải có một bản liFD duy nhất.');
      found.add(record.id);changed=true;pieces.push(p.bytes.subarray(recordOffset,record.at));
      const body=concat([p.bytes.subarray(record.start,record.sizeAt),num(replacement.length,8),p.bytes.subarray(record.sizeAt+8,record.dataAt),replacement,p.bytes.subarray(record.dataEnd,record.start+record.length)]);
      pieces.push(num(body.length,8),body,new Uint8Array((4-body.length%4)%4));recordOffset=record.end;
    }
    if(!changed)continue;
    pieces.push(p.bytes.subarray(recordOffset,tag.start+tag.length));const payload=concat(pieces);
    parts.push(p.bytes.subarray(offset,tag.at),ascii(tag.signature+tag.key),num(payload.length,tag.long?8:4),payload,new Uint8Array((4-payload.length%4)%4));offset=tag.end;
  }
  if(found.size!==targets.size)fail('Không tìm đủ ID tài nguyên nhúng trong PSD gốc.');
  parts.push(p.bytes.subarray(offset,p.layerMask.end));abort(signal);
  return rewriteMask(p,concat(parts));
}

