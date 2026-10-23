import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {fetchNativeShape} from '../depth-native-shapes.js';
test('literal original Ceramic shape pins verify both images without a mutable descriptor',async()=>{
  for(const family of ['ceramic_circle','ceramic_heart']){
    let target,options;const shape=await fetchNativeShape(family,{fetchImpl:async(url,opts)=>{target=url;options=opts;return new Response(await fs.readFile(url));}});
    assert.equal(options.credentials,'omit');assert.ok(target.pathname.endsWith(family==='ceramic_circle'?'ceramic-circle-shape-rgba.png':'ceramic-heart-shape-rgba.png'));assert.equal(shape.width,2799);assert.equal(shape.height,family==='ceramic_circle'?2799:2568);
  }
});
test('corrupt, oversized and truncated shape bodies fail before raster decoding',async()=>{
  const original=await fs.readFile(new URL('../assets/templates/ceramic-circle-shape-rgba.png',import.meta.url));
  const corrupt=original.slice();corrupt[100]^=255;
  for(const data of [corrupt,original.subarray(1),new Uint8Array(original.length+1)])await assert.rejects(()=>fetchNativeShape('ceramic_circle',{fetchImpl:async()=>new Response(data)}),/shape|Shape/);
});
test('acrylic needs no shape; unknown family and cancellation never fetch',async()=>{
  const options={fetchImpl:()=>{throw Error('unexpected fetch');}};assert.equal(await fetchNativeShape('acrylic',options),null);await assert.rejects(()=>fetchNativeShape('wrong',options));const controller=new AbortController();controller.abort();await assert.rejects(()=>fetchNativeShape('ceramic_circle',{...options,signal:controller.signal}),/hủy/);
});
