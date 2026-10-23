import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {gunzipSync} from 'node:zlib';
import {createHash} from 'node:crypto';
import {NATIVE_TEMPLATE_CATALOG,fetchNativeTemplate,openNativePSD,buildNativePSD,
  resizeNativeRaster,validateNativeRoleTransform,composeNativeSubstrate} from '../depth-psd-template.js';
import {inspectPSD,readPSDLinkedFiles,decodePSDComposite,replaceEmbeddedPSDs} from '../psd-roundtrip.js';

const assets=new URL('../assets/templates/psd/',import.meta.url);
const hash=bytes=>createHash('sha256').update(bytes).digest('hex');
const fixture=async item=>new Uint8Array(gunzipSync(await readFile(new URL(item.compressedFile,assets))));
const solid=(width,height,color)=>({width,height,rgba:Uint8ClampedArray.from({length:width*height*4},(_,i)=>color[i%4])});

test('three pinned original PSDs and gzip files match the transport manifest',async()=>{
  const manifest=JSON.parse(await readFile(new URL('manifest.json',assets),'utf8'));
  assert.equal(NATIVE_TEMPLATE_CATALOG.length,3);
  for(const item of NATIVE_TEMPLATE_CATALOG){
    const row=manifest.templates.find(t=>t.id===item.id),compressed=await readFile(new URL(item.compressedFile,assets)),bytes=gunzipSync(compressed);
    for(const key of ['bytes','sha256','compressedBytes','compressedSHA256','width','height'])assert.equal(row[key],item[key]);
    assert.equal(compressed.length,item.compressedBytes);assert.equal(hash(compressed),item.compressedSHA256);
    assert.equal(bytes.length,item.bytes);assert.equal(hash(bytes),item.sha256);
    assert.ok(Object.isFrozen(item));assert.ok(Object.isFrozen(item.roles));
  }
});

test('template fetch returns the unchanged original PSD and uses no credentials',async()=>{
  const item=NATIVE_TEMPLATE_CATALOG[0],compressed=await readFile(new URL(item.compressedFile,assets));let called=0;
  const bytes=await fetchNativeTemplate(item.id,{fetchImpl:async(url,options)=>{
    called++;assert.equal(url.protocol,'file:');assert.ok(url.pathname.endsWith(item.compressedFile));
    assert.equal(options.credentials,'omit');assert.equal(options.redirect,'error');
    return new Response(compressed,{headers:{'content-length':String(compressed.length)}});
  }});
  assert.equal(called,1);assert.equal(hash(bytes),item.sha256);
});

test('template fetch rejects corrupt, truncated or wrong-size transport data',async()=>{
  const item=NATIVE_TEMPLATE_CATALOG[2],original=await readFile(new URL(item.compressedFile,assets)),bad=Uint8Array.from(original);bad[100]^=1;
  await assert.rejects(fetchNativeTemplate(item.id,{fetchImpl:async()=>new Response(bad)}),/Mã kiểm tra/);
  await assert.rejects(fetchNativeTemplate(item.id,{fetchImpl:async()=>new Response(original.subarray(0,100))}),/chưa đầy đủ/);
  await assert.rejects(fetchNativeTemplate(item.id,{fetchImpl:async()=>new Response(original,{headers:{'content-length':'1'}})}),/Dung lượng/);
  await assert.rejects(fetchNativeTemplate('not-a-family'),/Chọn Acrylic/);
});

test('three real native PSDs read saved IN contents instead of the parent layout',async()=>{
  for(const item of NATIVE_TEMPLATE_CATALOG){
    const bytes=await fixture(item),before=hash(bytes),opened=await openNativePSD(bytes);
    assert.equal(opened.family,item.id);assert.equal(opened.artwork.width,item.inputWidth);assert.equal(opened.artwork.height,item.inputHeight);
    assert.equal(opened.isBundledBlank,true);
    assert.notEqual(opened.artwork.width,opened.width);assert.equal(opened.artwork.rgba.length,item.inputWidth*item.inputHeight*4);
    assert.deepEqual(Object.keys(opened.roles),item.roles.map(role=>role[0]));assert.equal(hash(bytes),before);
  }
});

test('unknown parent and embedded ICC profiles reject before artwork decode / AI',async()=>{
  const item=NATIVE_TEMPLATE_CATALOG[0];
  for(const target of ['parent','input']){
    const bytes=await fixture(item),info=inspectPSD(bytes);
    const profile=target==='parent'?info.iccProfileBytes:inspectPSD(readPSDLinkedFiles(bytes).find(file=>file.id===info.layers.find(layer=>layer.name==='layer in').placedID).bytes,{allowEmbeddedPSB:true}).iccProfileBytes;
    assert.equal(profile.length,3144);profile[profile.length-1]^=1;
    await assert.rejects(openNativePSD(bytes),error=>error.code==='NATIVE_PSD_COLOR_PROFILE'&&/sRGB/.test(error.message));
    await assert.rejects(buildNativePSD(bytes,{depth:solid(1,1,[100,100,100,255])}),error=>error.code==='NATIVE_PSD_COLOR_PROFILE');
  }
});

test('Circle untagged IN may gain verified sRGB after Photoshop save, but not an unknown profile',async()=>{
  const bytes=await fixture(NATIVE_TEMPLATE_CATALOG[1]),role=inspectPSD(bytes).layers.find(layer=>layer.name==='layer in'),child=readPSDLinkedFiles(bytes).find(file=>file.id===role.placedID).bytes;
  assert.equal(inspectPSD(child,{allowEmbeddedPSB:true}).iccProfileBytes,null);
  const srgb=inspectPSD(await fixture(NATIVE_TEMPLATE_CATALOG[0])).iccProfileBytes;
  function addICC(profile){
    const source=Buffer.from(child),resourcesAt=30+source.readUInt32BE(26),length=source.readUInt32BE(resourcesAt),resource=Buffer.alloc(12+profile.length+(profile.length%2));
    resource.write('8BIM');resource.writeUInt16BE(1039,4);resource.writeUInt32BE(profile.length,8);resource.set(profile,12);
    const size=Buffer.alloc(4);size.writeUInt32BE(length+resource.length);
    return replaceEmbeddedPSDs(bytes,[{id:role.placedID,bytes:Buffer.concat([source.subarray(0,resourcesAt),size,source.subarray(resourcesAt+4,resourcesAt+4+length),resource,source.subarray(resourcesAt+4+length)])}]);
  }
  assert.equal((await openNativePSD(addICC(srgb))).family,'ceramic_circle');
  const unknown=Uint8Array.from(srgb);unknown[unknown.length-1]^=1;
  await assert.rejects(openNativePSD(addICC(unknown)),error=>error.code==='NATIVE_PSD_COLOR_PROFILE');
});

test('pinned Acrylic custom warp is the identity 4×4 source mesh, not a curved warp approximation',async()=>{
  const item=NATIVE_TEMPLATE_CATALOG[0],bytes=await fixture(item);
  for(const role of inspectPSD(bytes).layers.filter(layer=>layer.name.startsWith('layer '))){
    const warp=Buffer.from(role.placedWarpBytes),arrays=[];let at=-1;
    while((at=warp.indexOf('UnFl',at+1))>=0){
      assert.equal(warp.subarray(at+4,at+8).toString('ascii'),'#Pxl');assert.equal(warp.readUInt32BE(at+8),16);
      arrays.push(Array.from({length:16},(_,i)=>warp.readDoubleBE(at+12+i*8)));
    }
    assert.equal(arrays.length,2);
    for(let i=0;i<16;i++){assert.ok(Math.abs(arrays[0][i]-(i%4)*item.inputWidth/3)<1e-9);assert.ok(Math.abs(arrays[1][i]-Math.floor(i/4)*item.inputHeight/3)<1e-9);}
  }
});

test('unknown warps fail even if their corner quad is still rectangular',async()=>{
  const bytes=await fixture(NATIVE_TEMPLATE_CATALOG[0]);
  const role=inspectPSD(bytes).layers.find(layer=>layer.name==='layer in');
  role.placedWarpBytes[role.placedWarpBytes.length-1]^=1;
  await assert.rejects(openNativePSD(bytes),/Warp, transform/);
});

test('changed SoLd geometry is independently rejected',async()=>{
  const bytes=await fixture(NATIVE_TEMPLATE_CATALOG[1]);
  const role=inspectPSD(bytes).layers.find(layer=>layer.name==='layer in');
  role.placedObjectBytes[role.placedObjectBytes.length-1]^=1;
  await assert.rejects(openNativePSD(bytes),/Warp, transform/);
});

test('content-version UUID may change without weakening geometry validation',async()=>{
  const item=NATIVE_TEMPLATE_CATALOG[2],bytes=await fixture(item),role=inspectPSD(bytes).layers.find(layer=>layer.name==='layer in');
  const object=role.placedObjectBytes,pattern=Buffer.from('placedTEXT'),at=Buffer.from(object).indexOf(pattern);
  assert.ok(at>0);const characterAt=at+pattern.length+4+1;
  object[characterAt]=object[characterAt]===97?98:97;
  assert.equal((await openNativePSD(bytes)).family,item.id);
});

test('rotation, perspective, invalid quads and changed layout are not guessed',()=>{
  assert.deepEqual(validateNativeRoleTransform([0,0,10,0,10,10,0,10],[0,0,10,10]),[0,0,10,10]);
  assert.throws(()=>validateNativeRoleTransform([0,0,10,1,10,10,0,10],[0,0,10,10]),/perspective/);
  assert.throws(()=>validateNativeRoleTransform([0,0,12,0,12,10,0,10],[0,0,10,10]),/Bố cục/);
  assert.throws(()=>validateNativeRoleTransform([NaN,0,10,0,10,10,0,10],[0,0,10,10]),/xác minh/);
});

test('registered resizing stretches full canvas and keeps alpha/color edge registration',()=>{
  const source={width:2,height:1,rgba:Uint8ClampedArray.of(255,0,0,255,0,0,0,0)};
  const resized=resizeNativeRaster(source,4,2);
  assert.equal(resized.width,4);assert.equal(resized.height,2);
  assert.deepEqual([...resized.rgba.subarray(0,4)],[255,0,0,255]);
  assert.deepEqual([...resized.rgba.subarray(12,16)],[0,0,0,0]);
  assert.equal(resized.rgba[4],255);assert.equal(resized.rgba[7],191);
  assert.deepEqual([...resized.rgba.subarray(0,16)],[...resized.rgba.subarray(16,32)]);
});

test('Float32 depth produces explicit RGB8 cache pixels and validates inputs',()=>{
  const result=resizeNativeRaster({width:2,height:1,depth:Float32Array.of(.25,.75),alpha:Uint8Array.of(255,128)},2,1);
  assert.deepEqual([...result.rgba],[64,64,64,255,191,191,191,128]);
  assert.throws(()=>resizeNativeRaster({width:1,height:1,depth:Float32Array.of(NaN)},1,1),/Depth có giá trị/);
  assert.throws(()=>resizeNativeRaster({width:1,height:1,rgba:new Uint8Array(3)},1,1),/không khớp/);
  assert.throws(()=>resizeNativeRaster(solid(1,1,[1,2,3,4]),9000,2),/Canvas map đích/);
});

test('Ceramic native substrate fills body gaps but preserves outside and hanging-hole alpha',()=>{
  const foreground={width:4,height:1,rgba:Uint8ClampedArray.of(0,0,0,0,0,0,0,0,180,180,180,128,0,0,0,0)};
  const blackBody={width:4,height:1,rgba:Uint8ClampedArray.of(0,0,0,0,0,0,0,255,0,0,0,255,0,0,0,0)};
  assert.deepEqual([...composeNativeSubstrate(foreground,blackBody).rgba],[0,0,0,0,0,0,0,255,90,90,90,255,0,0,0,0]);
  const whiteBody={...blackBody,rgba:Uint8ClampedArray.of(255,255,255,0,255,255,255,255,255,255,255,255,255,255,255,0)};
  const color={width:4,height:1,rgba:Uint8ClampedArray.of(0,0,0,0,0,0,0,0,20,90,170,255,0,0,0,0)};
  const composite=composeNativeSubstrate(color,whiteBody).rgba;
  assert.deepEqual([...composite.subarray(4,12)],[255,255,255,255,20,90,170,255]);
  assert.equal(composite[3],0);assert.equal(composite[15],0);
});

test('abort is respected before reads, downloads, resampling and PSD export',async()=>{
  const controller=new AbortController();controller.abort();const signal=controller.signal;
  await assert.rejects(openNativePSD(new Uint8Array(0),{signal}),{name:'AbortError'});
  await assert.rejects(fetchNativeTemplate('acrylic',{signal,fetchImpl:()=>{throw Error('must not fetch');}}),{name:'AbortError'});
  await assert.rejects(buildNativePSD(new Uint8Array(0),{signal}),{name:'AbortError'});
  assert.throws(()=>resizeNativeRaster(solid(1,1,[0,0,0,0]),1,1,{signal}),{name:'AbortError'});
});

test('all three real templates export native RGB8 role PSBs with editable originals retained', {timeout:180000},async()=>{
  const started=Date.now();
  for(const item of NATIVE_TEMPLATE_CATALOG){
    const bytes=await fixture(item),before=hash(bytes),originalInfo=inspectPSD(bytes),originalLinks=readPSDLinkedFiles(bytes);
    const artwork={width:2,height:2,rgba:Uint8ClampedArray.of(20,90,170,0,20,90,170,255,20,90,170,0,20,90,170,255)};
    const depth={width:2,height:2,depth:Float32Array.of(.2,.8,.2,.8),alpha:Uint8Array.of(0,255,0,255)};
    const ceramicShape={width:2,height:2,rgba:Uint8ClampedArray.of(255,255,255,0,255,255,255,255,255,255,255,0,255,255,255,255)};
    const output=await buildNativePSD(bytes,{artwork,depth,ceramicShape}),outInfo=inspectPSD(output),reopened=await openNativePSD(output),outLinks=readPSDLinkedFiles(output);
    assert.equal(reopened.isBundledBlank,false);
    assert.equal(hash(bytes),before,'input source was not modified');
    assert.equal(outInfo.width,item.width);assert.equal(outInfo.height,item.height);assert.equal(outInfo.layerCount,originalInfo.layerCount);
    assert.deepEqual(outInfo.layers.map(l=>l.name),originalInfo.layers.map(l=>l.name));
    const roleIndices=new Set(Object.values(reopened.roles).map(r=>r.index));
    assert.ok(outInfo.layers.every(l=>l.visible===roleIndices.has(l.index)),'only map roles visible; guide bytes remain in file');
    assert.equal(reopened.artwork.rgba[3],0,'transparent IN survives promoted saved composite');
    const at=(Math.floor(item.inputHeight/2)*item.inputWidth+item.inputWidth-1)*4;
    assert.deepEqual([...reopened.artwork.rgba.subarray(at,at+4)],[20,90,170,255]);
    for(const role of Object.values(reopened.roles)){
      const original=originalLinks.find(f=>f.id===role.embeddedID),child=outLinks.find(f=>f.id===role.embeddedID);
      assert.ok(child.bytes.length!==original.bytes.length);assert.equal(new DataView(child.bytes.buffer,child.bytes.byteOffset).getUint16(12),4);
      const image=await decodePSDComposite(child.bytes,{allowEmbeddedPSB:true});assert.equal(image.width,item.inputWidth);assert.equal(image.height,item.inputHeight);
      assert.equal(image.rgba[3],0);
      if(role===reopened.roles.cut)assert.equal(image.rgba[at],0,'acrylic Cut defaults to black depth alpha');
      if(role===reopened.roles.depth)assert.equal(image.rgba[at],204,'full-canvas depth is resampled, not refit');
    }
    // Any unrelated linked Smart Object remains byte-identical.
    const ids=new Set(Object.values(reopened.roles).map(r=>r.embeddedID));
    for(const link of originalLinks)if(!ids.has(link.id)&&link.bytes)assert.equal(hash(outLinks.find(f=>f.id===link.id).bytes),hash(link.bytes));
  }
  console.log(JSON.stringify({nativePSDTemplates:3,elapsedMs:Date.now()-started,sourcePSDOverwritten:false,PhotoshopCommands:0}));
});

test('export requires generated depth instead of silently returning the old template',async()=>{
  await assert.rejects(buildNativePSD(await fixture(NATIVE_TEMPLATE_CATALOG[2]),{}),/Chưa có depth/);
});

test('Ceramic export requires the authoritative substrate instead of inventing a geometric shape',async()=>{
  await assert.rejects(buildNativePSD(await fixture(NATIVE_TEMPLATE_CATALOG[2]),{artwork:solid(1,1,[1,2,3,255]),depth:solid(1,1,[10,10,10,255])}),/Cần shape Ceramic gốc/);
});
