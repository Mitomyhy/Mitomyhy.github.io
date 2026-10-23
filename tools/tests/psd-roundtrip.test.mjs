import test from 'node:test';
import assert from 'node:assert/strict';
import {deflateSync} from 'node:zlib';
import {readFileSync,existsSync} from 'node:fs';
import {spawnSync} from 'node:child_process';
import {inspectPSD,decodePSDComposite,decodePSDLayerPixels,appendPSDLayers,readPSDLinkedFiles,readPSDArtworkLayer,
  replacePSDComposite,replacePSDLayerPixels,replaceEmbeddedPSDs,setPSDLayerVisibility,readPSDFile} from '../psd-roundtrip.js';

const cat=(...parts)=>Buffer.concat(parts.map(p=>Buffer.from(p)));
function n(value,size=4,signed=false){const b=Buffer.alloc(size);if(size===8)b.writeBigUInt64BE(BigInt(value));else if(size===2)signed?b.writeInt16BE(value):b.writeUInt16BE(value);else signed?b.writeInt32BE(value):b.writeUInt32BE(value);return b;}
const text=value=>Buffer.from(value,'ascii');
const uni=value=>cat(n(value.length),...Array.from(value,c=>n(c.charCodeAt(0),2)));
function tag(key,data,pad=4){return cat(text('8BIM'+key),n(data.length),data,Buffer.alloc((pad-data.length%pad)%pad));}
const pixels=(w,h,offset=0)=>Uint8Array.from({length:w*h*4},(_,i)=>i%4===3?255:(i*17+offset)%256);
function planar(rgba,w,h,channels=4){return cat(...Array.from({length:channels},(_,c)=>Uint8Array.from({length:w*h},(_,i)=>rgba[i*4+c])));}
function flat({width=4,height=3,rgba=pixels(width,height),channels=4,version=1,compression=0,mode=3,depth=8,global=Buffer.alloc(0),resourceData=Buffer.alloc(0)}={}){
  const header=cat(text('8BPS'),n(version,2),Buffer.alloc(6),n(channels,2),n(height),n(width),n(depth,2),n(mode,2));
  const planes=planar(rgba,width,height,channels);let image;
  if(compression===0)image=cat(n(0,2),planes);
  else if(compression===1){const rows=[];for(let c=0;c<channels;c++)for(let y=0;y<height;y++)rows.push(cat(Uint8Array.of(width-1),planes.subarray((c*height+y)*width,(c*height+y+1)*width)));image=cat(n(1,2),...rows.map(row=>n(row.length,version===2?4:2)),...rows);}
  else{const data=Buffer.from(planes);if(compression===3)for(let c=0;c<channels;c++)for(let y=0;y<height;y++)for(let x=width-1;x>0;x--){const i=(c*height+y)*width+x;data[i]=(data[i]-data[i-1])&255;}image=cat(n(compression,2),deflateSync(data));}
  const mask=global.length?cat(n(0,version===2?8:4),n(0),global):Buffer.alloc(0);
  return cat(header,n(0),n(resourceData.length),resourceData,n(mask.length,version===2?8:4),mask,image);
}
// Independent, minimal section/record reader: it does not reuse production parse.
function layout(data){
  const b=Buffer.from(data);let pos=26;const version=b.readUInt16BE(4),size=version===2?8:4;
  const u=bytes=>{const n=bytes===8?Number(b.readBigUInt64BE(pos)):b.readUInt32BE(pos);pos+=bytes;return n;};
  let length=u(4);pos+=length;length=u(4);pos+=length;const maskAt=pos,maskLength=u(size),maskStart=pos,maskEnd=pos+maskLength;
  let infoAt=pos,infoLength=0,count=0,recordStart=pos,recordEnd=pos,channelsStart=pos,channelsEnd=pos,tailStart=pos,records=[];
  if(maskLength){infoLength=u(size);const end=pos+infoLength;tailStart=end;if(infoLength){count=b.readInt16BE(pos);pos+=2;recordStart=pos;
    for(let i=0;i<Math.abs(count);i++){const at=pos;pos+=16;const n=b.readUInt16BE(pos);pos+=2;const channels=[];for(let c=0;c<n;c++){const id=b.readInt16BE(pos);pos+=2;channels.push({id,length:u(size)});}pos+=12;const extra=u(4);pos+=extra;records.push({at,end:pos,channels});}
    recordEnd=channelsStart=pos;channelsEnd=pos+records.reduce((s,r)=>s+r.channels.reduce((t,c)=>t+c.length,0),0);
  }}
  return {b,version,size,maskAt,maskStart,maskEnd,maskLength,infoAt,infoLength,count,recordStart,recordEnd,channelsStart,channelsEnd,tailStart,records,imageStart:maskEnd};
}
function source({width=4,height=3,version=1,compression=0,global=Buffer.alloc(0)}={}){
  const rgba=pixels(width,height),base=flat({width,height,rgba,version,compression,global});
  let bytes=appendPSDLayers(base,[{name:'Original editable art',width,height,rgba,visible:true}],{composite:{width,height,rgba},allowEmbeddedPSB:version===2});
  bytes=Buffer.from(bytes);const l=layout(bytes);bytes.writeInt16BE(-1,l.infoAt+l.size);
  return {bytes,rgba,width,height};
}
function withGlobal(bytes,key,payload){const p=layout(bytes),tail=cat(n(0),tag(key,payload)),info=Buffer.from(bytes).subarray(p.maskStart,p.tailStart),mask=cat(info,tail);return cat(p.b.subarray(0,p.maskAt),n(mask.length,p.size),mask,p.b.subarray(p.imageStart));}
function linkedRecord(id,name,bytes,{descriptor=false}={}){
  const open=descriptor?cat(n(16),uni(''),n(0),text('null'),n(1),n(0),text('test'),text('TEXT'),uni('fixture')):Buffer.alloc(0);
  const body=cat(text('liFD'),n(3),Uint8Array.of(id.length),text(id),uni(name),text('8BPS8BIM'),n(bytes.length,8),Uint8Array.of(descriptor?1:0),open,bytes);
  return cat(n(body.length,8),body,Buffer.alloc((4-body.length%4)%4));
}
function addPlacedLink(bytes,index,id){
  const p=layout(bytes),layer=p.records[index],old=p.b.subarray(layer.at,layer.end),channels=layer.channels.length,extraAt=18+channels*p.size+channels*2+12;
  // Record extra-length offset = rectangle + channel count + channel entries + blend fields.
  const lenAt=18+channels*(p.size+2)+12,extraLength=old.readUInt32BE(lenAt),quad=[0,0,4,0,4,3,0,3],qb=Buffer.alloc(64);quad.forEach((v,i)=>qb.writeDoubleBE(v,i*8));
  const placed=cat(text('plcL'),n(3),Uint8Array.of(id.length),text(id),n(1),n(1),n(16),n(2),qb),addition=tag('PlLd',placed,2);
  const record=cat(old.subarray(0,lenAt),n(extraLength+addition.length),old.subarray(lenAt+4),addition);
  let info=cat(n(p.count,2,true),...p.records.map((r,i)=>i===index?record:p.b.subarray(r.at,r.end)),p.b.subarray(p.channelsStart,p.channelsEnd));if(info.length%2)info=cat(info,Buffer.alloc(1));
  const mask=cat(n(info.length,p.size),info,p.b.subarray(p.tailStart,p.maskEnd));return cat(p.b.subarray(0,p.maskAt),n(mask.length,p.size),mask,p.b.subarray(p.imageStart));
}

test('bounded RGB8 header accepts exact saved composite and rejects unsupported/truncated formats',async()=>{
  const f=source(),info=inspectPSD(f.bytes);assert.equal(info.width,4);assert.equal(info.layerCount,1);assert.equal(info.layers[0].name,'Original editable art');
  assert.deepEqual((await decodePSDComposite(f.bytes)).rgba,new Uint8ClampedArray(f.rgba));
  for(const options of [{depth:16},{mode:4},{version:2}])assert.throws(()=>inspectPSD(flat(options)),/8-bit|PSB/);
  assert.throws(()=>inspectPSD(f.bytes.subarray(0,30)),/thiếu|độ dài/);
  const enormous=Buffer.from(flat());enormous.writeUInt32BE(100000,14);assert.throws(()=>inspectPSD(enormous),/giới hạn/);
});

test('ICC resource bytes are exposed unchanged with strict signature, bounds and duplicate checks',()=>{
  const profile=Buffer.alloc(128,1),resource=cat(text('8BIM'),n(1039,2),Uint8Array.of(0,0),n(profile.length),profile);
  assert.deepEqual(Buffer.from(inspectPSD(flat({resourceData:resource})).iccProfileBytes),profile);
  assert.equal(inspectPSD(flat()).iccProfileBytes,null);
  assert.throws(()=>inspectPSD(flat({resourceData:cat(resource,resource)})),/ICC profile PSD bị trùng/);
  assert.throws(()=>inspectPSD(flat({resourceData:resource.subarray(0,-1)})),/thiếu|độ dài/);
  const bad=Buffer.from(resource);bad.write('8BAD');assert.throws(()=>inspectPSD(flat({resourceData:bad})),/Chữ ký image resource/);
  assert.deepEqual(Buffer.from(inspectPSD(flat({version:2,resourceData:resource}),{allowEmbeddedPSB:true}).iccProfileBytes),profile);
});

test('raw, RLE, ZIP and ZIP prediction composites decode same pixels',async()=>{
  const rgba=pixels(5,3);for(const compression of [0,1,2,3]){
    const actual=await decodePSDComposite(flat({width:5,height:3,rgba,compression}));
    assert.deepEqual(Array.from(actual.rgba.filter((_,i)=>i%4!==3)),Array.from(rgba.filter((_,i)=>i%4!==3)));
    assert.ok(actual.rgba.filter((_,i)=>i%4===3).every(v=>v===255));
  }
  const short=flat({compression:1}).subarray(0,-1);await assert.rejects(decodePSDComposite(short),/thiếu|độ dài/);
});

function layerCompressionFixture(version,compression) {
  const width=4,height=3,rgba=pixels(width,height);rgba[3]=0;rgba[7]=128;
  const f=source({version}),l=layout(f.bytes),record=Buffer.from(l.b.subarray(l.records[0].at,l.records[0].end)),channels=[];
  for(let c=0;c<4;c++) {
    const plane=Uint8Array.from({length:width*height},(_,i)=>rgba[i*4+c]);let encoded;
    if(compression===0)encoded=cat(n(0,2),plane);
    else if(compression===1) {
      const rows=Array.from({length:height},(_,y)=>cat(Uint8Array.of(width-1),plane.subarray(y*width,(y+1)*width)));
      encoded=cat(n(1,2),...rows.map(row=>n(row.length,version===2?4:2)),...rows);
    } else {
      const data=Buffer.from(plane);
      if(compression===3)for(let y=0;y<height;y++)for(let x=width-1;x>0;x--)data[y*width+x]=(data[y*width+x]-data[y*width+x-1])&255;
      encoded=cat(n(compression,2),deflateSync(data));
    }
    const at=18+c*(l.size+2)+2;
    if(version===2)record.writeBigUInt64BE(BigInt(encoded.length),at);else record.writeUInt32BE(encoded.length,at);
    channels.push(encoded);
  }
  let info=cat(n(l.count,2,true),record,...channels);if(info.length%2)info=cat(info,Buffer.alloc(1));
  const mask=cat(n(info.length,l.size),info,l.b.subarray(l.tailStart,l.maskEnd));
  return {rgba,bytes:cat(l.b.subarray(0,l.maskAt),n(mask.length,l.size),mask,l.b.subarray(l.imageStart))};
}

test('Photoshop layer caches decode raw/RLE/ZIP/prediction with straight partial alpha in PSD and embedded PSB',async()=>{
  for(const version of [1,2])for(const compression of [0,1,2,3]) {
    const f=layerCompressionFixture(version,compression),image=await decodePSDLayerPixels(f.bytes,{index:0,allowEmbeddedPSB:version===2});
    assert.deepEqual(image.rgba,new Uint8ClampedArray(f.rgba));
    assert.equal(image.source,'photoshop-saved-layer-cache');assert.equal(image.hasTransparency,true);
  }
  await assert.rejects(decodePSDLayerPixels(source().bytes,{index:1}),/Không xác định/);
  await assert.rejects(decodePSDLayerPixels(source().bytes,{index:0,signal:{aborted:true}}),{name:'AbortError'});
});

test('add hidden map preserves every original record, channel, metadata, and original composite byte',async()=>{
  const unknown=tag('zzzz',Uint8Array.of(1,2,3,4,5)),f=source({global:unknown}),before=layout(f.bytes),rgba=Uint8Array.from({length:4*3*4},(_,i)=>i%4===3?255:100);
  const out=appendPSDLayers(f.bytes,[{name:'MITOM depth / độ sâu',width:4,height:3,rgba}]),after=layout(out),info=inspectPSD(out);
  assert.equal(info.layerCount,2);assert.equal(info.layers[1].visible,false);assert.equal(info.layers[1].name,'MITOM depth / độ sâu');
  assert.deepEqual(after.b.subarray(after.records[0].at,after.records[0].end),before.b.subarray(before.records[0].at,before.records[0].end));
  const oldChannelLength=before.channelsEnd-before.channelsStart;
  assert.deepEqual(after.b.subarray(after.channelsStart,after.channelsStart+oldChannelLength),before.b.subarray(before.channelsStart,before.channelsEnd));
  assert.deepEqual(after.b.subarray(after.tailStart,after.maskEnd),before.b.subarray(before.tailStart,before.maskEnd));
  assert.deepEqual(after.b.subarray(after.imageStart),before.b.subarray(before.imageStart));
  assert.deepEqual((await decodePSDComposite(out)).rgba,new Uint8ClampedArray(f.rgba));
});

test('visible result requires explicit correct composite; hideOriginalLayers changes flags only',async()=>{
  const f=source(),rgba=pixels(4,3,33);assert.throws(()=>appendPSDLayers(f.bytes,[{name:'Visible',width:4,height:3,rgba,visible:true}]),/composite/);
  const out=appendPSDLayers(f.bytes,[{name:'Visible',width:4,height:3,rgba,visible:true}],{composite:{width:4,height:3,rgba},hideOriginalLayers:true});
  const before=layout(f.bytes),after=layout(out),info=inspectPSD(out);assert.equal(info.layers[1].visible,true);assert.equal(info.layers[0].visible,false);
  const old=Buffer.from(before.b.subarray(before.records[0].at,before.records[0].end)),changed=after.b.subarray(after.records[0].at,after.records[0].end),flagAt=18+4*6+10;old[flagAt]|=2;assert.deepEqual(changed,old);
  assert.deepEqual((await decodePSDComposite(out)).rgba,new Uint8ClampedArray(rgba));
});

test('merged partial transparency removes/writes Photoshop white matte while layer channels stay straight',async()=>{
  const f=source(),rgba=new Uint8Array(4*3*4);for(let i=0;i<12;i++)rgba.set([64,128,192,i===0?128:255],i*4);
  const out=appendPSDLayers(f.bytes,[{name:'Alpha edge',width:4,height:3,rgba,visible:true}],{composite:{width:4,height:3,rgba},hideOriginalLayers:true}),decoded=await decodePSDComposite(out);
  for(let i=0;i<rgba.length;i++)assert.ok(Math.abs(decoded.rgba[i]-rgba[i])<=1,'matte roundtrip ±1 quantization at '+i);
  assert.deepEqual(Array.from(decoded.rgba.subarray(0,4)),[64,128,191,128]);
});

test('cached raster replacement keeps extra metadata, unrelated channels and supports explicit mask-free new bounds',async()=>{
  const f=source(),second=appendPSDLayers(f.bytes,[{name:'Other',width:1,height:1,rgba:Uint8Array.of(1,2,3,4)}]),before=layout(second),rgba=pixels(2,2,11);
  const out=replacePSDLayerPixels(second,[{index:0,left:1,top:1,width:2,height:2,rgba}]),after=layout(out),info=inspectPSD(out);
  assert.deepEqual([info.layers[0].left,info.layers[0].top,info.layers[0].width,info.layers[0].height],[1,1,2,2]);
  assert.deepEqual(after.b.subarray(after.records[1].at,after.records[1].end),before.b.subarray(before.records[1].at,before.records[1].end));
  assert.deepEqual(after.b.subarray(after.imageStart),before.b.subarray(before.imageStart));
  const shown=setPSDLayerVisibility(out,[{index:1,visible:true}]),shownInfo=inspectPSD(shown);assert.equal(shownInfo.layers[1].visible,true);
  const countDiff=Buffer.from(shown).reduce((n,v,i)=>n+(v!==out[i]),0);assert.equal(countDiff,1);
});

test('embedded RGB8 PSB uses 64-bit section/channel lengths and 32-bit RLE rows',async()=>{
  const f=source({version:2}),rgba=pixels(4,3,77),out=replacePSDComposite(f.bytes,{width:4,height:3,rgba},{allowEmbeddedPSB:true});
  assert.equal(layout(out).version,2);assert.deepEqual((await decodePSDComposite(out,{allowEmbeddedPSB:true})).rgba,new Uint8ClampedArray(rgba));
  const added=appendPSDLayers(out,[{name:'Native result',width:4,height:3,rgba,visible:true}],{allowEmbeddedPSB:true,composite:{width:4,height:3,rgba},hideOriginalLayers:true});
  assert.equal(layout(added).count,-2);assert.deepEqual((await decodePSDComposite(added,{allowEmbeddedPSB:true})).rgba,new Uint8ClampedArray(rgba));
  const rle=flat({version:2,compression:1});assert.equal((await decodePSDComposite(rle,{allowEmbeddedPSB:true})).width,4);
});

test('RGB3 embedded PSB promotes merged transparency instead of reopening as opaque artwork',async()=>{
  const base=flat({version:2,channels:3}),rgba=pixels(4,3);rgba[3]=0;rgba[7]=128;
  const out=appendPSDLayers(base,[{name:'MITOM IN artwork',width:4,height:3,rgba,visible:true}],{allowEmbeddedPSB:true,composite:{width:4,height:3,rgba},hideOriginalLayers:true});
  assert.equal(Buffer.from(out).readUInt16BE(12),4);assert.equal(layout(out).count,-1);
  const decoded=await decodePSDComposite(out,{allowEmbeddedPSB:true});assert.equal(decoded.rgba[3],0);assert.equal(decoded.rgba[7],128);assert.equal(decoded.hasTransparency,true);
  for(let c=0;c<3;c++)assert.ok(Math.abs(decoded.rgba[4+c]-rgba[4+c])<=1);
});

test('embedded liFD replacement preserves all other linked files and metadata; IN selection uses exact PlLd ID',async()=>{
  const child=source({version:2}),other=source(),id='11111111-2222-3333-4444-555555555555',otherId='aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee';
  let parent=source().bytes;parent=addPlacedLink(parent,0,id);
  parent=withGlobal(parent,'lnkD',cat(linkedRecord(id,'IN.psb',child.bytes,{descriptor:true}),linkedRecord(otherId,'Other.psd',other.bytes)));
  const linked=readPSDLinkedFiles(parent);assert.equal(linked.length,2);assert.deepEqual(Buffer.from(linked[0].bytes),child.bytes);
  const art=await readPSDArtworkLayer(parent,{layerName:'Original editable art'});assert.equal(art.embeddedID,id);assert.deepEqual(art.rgba,new Uint8ClampedArray(child.rgba));
  const replacement=appendPSDLayers(child.bytes,[{name:'Map',width:1,height:1,rgba:Uint8Array.of(10,20,30,255)}],{allowEmbeddedPSB:true});
  const out=replaceEmbeddedPSDs(parent,[{id,bytes:replacement}]),outLinked=readPSDLinkedFiles(out);assert.deepEqual(Buffer.from(outLinked[0].bytes),Buffer.from(replacement));assert.deepEqual(Buffer.from(outLinked[1].bytes),other.bytes);
  const before=layout(parent),after=layout(out);assert.deepEqual(after.b.subarray(after.recordStart,after.channelsEnd),before.b.subarray(before.recordStart,before.channelsEnd));assert.deepEqual(after.b.subarray(after.imageStart),before.b.subarray(before.imageStart));
  assert.deepEqual(Buffer.from(replaceEmbeddedPSDs(parent,[{id,bytes:child.bytes}])),Buffer.from(parent));
  assert.throws(()=>replaceEmbeddedPSDs(parent,[{id:'missing',bytes:child.bytes}]),/Không tìm/);
});

test('abort and type/size checks happen before any source mutation',async()=>{
  const f=source(),copy=Buffer.from(f.bytes),signal=AbortSignal.abort();
  await assert.rejects(decodePSDComposite(f.bytes,{signal}),{name:'AbortError'});
  assert.throws(()=>appendPSDLayers(f.bytes,[{name:'x',width:1,height:1,rgba:Uint8Array.of(0,0,0,255)}],{signal}),{name:'AbortError'});
  assert.throws(()=>appendPSDLayers(f.bytes,[{width:1,height:1,rgba:new Uint8Array(3)}]),/RGBA/);
  await assert.rejects(readPSDFile({size:1}),/Chọn PSD/);assert.deepEqual(f.bytes,copy);
});

const installed='C:/Users/ADMIN/AppData/Roaming/Adobe/CEP/extensions/com.uoy.orderpanel/assets/';
for(const [name,width,height,inWidth,inHeight] of [['Template acrylic.psd',3543,1181,3000,3001],['Circleceramic-Template.psd',1866,933,2799,2799],['Heartceramic-Template.psd',1866,856,2799,2568]]){
  test('installed original template '+name+' parses and exact embedded identity replacement preserves file',{skip:!existsSync(installed+name)},async()=>{
    const bytes=readFileSync(installed+name),info=inspectPSD(bytes),files=readPSDLinkedFiles(bytes),art=await readPSDArtworkLayer(bytes);
    assert.deepEqual([info.width,info.height],[width,height]);assert.deepEqual([art.width,art.height],[inWidth,inHeight]);assert.ok(info.layers.filter(l=>l.placedID).every(l=>l.placedTransform?.length===8));
    const out=replaceEmbeddedPSDs(bytes,files.filter(f=>f.bytes).map(f=>({id:f.id,bytes:f.bytes})));assert.deepEqual(Buffer.from(out),bytes);
  });
}

const python=process.env.MITOM_PSD_TEST_PYTHON||'D:/Tool for me/PS Panel/depth-runtime/.venv/Scripts/python.exe';
test('independent Pillow parser reads newly generated PSD composite and layers',{skip:!existsSync(python)},()=>{
  const f=source(),rgba=pixels(4,3,31),out=appendPSDLayers(f.bytes,[{name:'MITOM visible render',width:4,height:3,rgba,visible:true}],{composite:{width:4,height:3,rgba},hideOriginalLayers:true});
  const script='import sys,io,json; from PIL import Image; b=sys.stdin.buffer.read(); im=Image.open(io.BytesIO(b)); im.load(); print(json.dumps({"format":im.format,"size":list(im.size),"pixel":list(im.getpixel((1,1))),"layers":len(im.layers)}))';
  const result=spawnSync(python,['-c',script],{input:out,encoding:'utf8'});assert.equal(result.status,0,result.stderr);const data=JSON.parse(result.stdout);assert.equal(data.format,'PSD');assert.deepEqual(data.size,[4,3]);assert.equal(data.layers,2);assert.deepEqual(data.pixel,Array.from(rgba.subarray((4+1)*4,(4+1)*4+4)));
});

