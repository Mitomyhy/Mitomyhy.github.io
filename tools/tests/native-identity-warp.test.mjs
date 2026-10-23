import test from 'node:test';
import assert from 'node:assert/strict';
import {inspectPSD,readPSDLinkedFiles} from '../psd-roundtrip.js';
import {openNativePSD,buildNativePSD} from '../depth-psd-template.js';
import {publicAcrylic,syntheticNonePSD,geometryBytes,hash} from './helpers/native-warp-fixture.mjs';

const names=['layer in','layer depth map','layer cắt'];
function layer(bytes,name='layer in'){return inspectPSD(bytes).layers.find(value=>value.name===name);}
function doubleField(bytes,field,value){const block=Buffer.from(bytes.buffer,bytes.byteOffset,bytes.byteLength),key=block.indexOf(field);assert.ok(key>=0);assert.equal(block.subarray(key+field.length,key+field.length+4).toString(),'doub');block.writeDoubleBE(value,key+field.length+4);}
function expectRejected(bytes){return assert.rejects(openNativePSD(bytes),/Warp, transform|bố cục|Template đã xoay/i);}

test('public Acrylic source is a zero-amount custom warp with a regular identity4x4 grid',async()=>{
  const original=await publicAcrylic();
  for(const name of names){const warp=Buffer.from(layer(original,name).placedWarpBytes);assert.ok(warp.includes(Buffer.from('warpCustom')));const arrays=[];let at=-1;while((at=warp.indexOf('UnFl',at+1))>=0){assert.equal(warp.subarray(at+4,at+8).toString(),'#Pxl');assert.equal(warp.readUInt32BE(at+8),16);arrays.push(Array.from({length:16},(_,i)=>warp.readDoubleBE(at+12+i*8)));}assert.equal(arrays.length,2);for(let i=0;i<16;i++){assert.equal(arrays[0][i],i%4*1000);assert.ok(Math.abs(arrays[1][i]-Math.floor(i/4)*3001/3)<1e-9);}for(const key of ['warpValue','warpPerspective','warpPerspectiveOther']){const p=warp.indexOf(key)+key.length+4;assert.equal(warp.readDoubleBE(p),0);}}
});
test('known Photoshop none rewrite accepts each Acrylic role independently and all three together',async()=>{
  const original=await publicAcrylic();
  const expected=['4049d6d34211b5f09bdd0e41ab088531cd69269ef7a985480c04957e0b87fb71','1eef5c2c60b541e757b426c8daa7ef43b3725eae4f3d14e4cf8144da5b3cd249','a0b1f6bcf63d4c35e039a922241952168e3d7a25fe52629eba127ed1b169bdbf'];
  for(const selected of names.map(name=>[name]).concat([names])){const bytes=syntheticNonePSD(original,{roles:selected}),before=hash(bytes),opened=await openNativePSD(bytes);assert.equal(opened.family,'acrylic');assert.equal(hash(bytes),before);for(const name of selected){const role=layer(bytes,name);assert.equal(hash(role.placedWarpBytes),'bc377cfaf627d6b4012f394cb2d20cdb328e75422e3aa73649a212fae2d4b6fc');assert.equal(hash(geometryBytes(role.placedObjectBytes)),expected[names.indexOf(name)]);}}
});
for(const target of ['placedWarpBytes','placedObjectBytes']){
  for(const [field,value]of [['warpValue',1],['warpPerspective',1],['warpPerspectiveOther',1],['Top ',1],['Rght',3001],['Wdth',2999]]){
    if(target==='placedWarpBytes'&&field==='Wdth')continue;
    test('none rewrite rejects changed '+target+' '+field,async()=>{const bytes=syntheticNonePSD(await publicAcrylic(),{roles:['layer in']});doubleField(layer(bytes)[target],field,value);await expectRejected(bytes);});
  }
  test('none rewrite rejects changed orientation, style, order and padding in '+target,async()=>{
    for(const mutate of [block=>{const at=block.indexOf('Hrzn');assert.ok(at>0);block.write('Vrtc',at);},block=>{const at=block.indexOf('warpNone');assert.ok(at>0);block.write('warpFish',at);},block=>{const at=block.indexOf('uOrder');assert.ok(at>0);block.writeInt32BE(5,at+6+4);},block=>{block[block.length-1]=1;}]){const bytes=syntheticNonePSD(await publicAcrylic(),{roles:['layer in']}),view=layer(bytes)[target],block=Buffer.from(view.buffer,view.byteOffset,view.length);mutate(block);await expectRejected(bytes);}
  });
}
test('PlLd and SoLd must use the same accepted variant, never a mixed custom/none pair',async()=>{for(const counterpart of ['PlLd','SoLd'])await expectRejected(syntheticNonePSD(await publicAcrylic(),{roles:['layer in'],counterpart}));});
test('actual custom mesh deformation remains rejected even with rectangular corner transform',async()=>{const bytes=await publicAcrylic(),view=layer(bytes).placedWarpBytes,block=Buffer.from(view.buffer,view.byteOffset,view.length),at=block.indexOf('UnFl');block.writeDoubleBE(.5,at+12);await expectRejected(bytes);});
test('none rewrite does not relax rotation, perspective or changed native role layout',async()=>{for(const offset of [0,1,2]){const bytes=syntheticNonePSD(await publicAcrylic(),{roles:['layer in']}),view=layer(bytes).placedWarpBytes,quad=new DataView(view.buffer,view.byteOffset-64,64);quad.setFloat64(offset*8,quad.getFloat64(offset*8)+5);await expectRejected(bytes);}});
test('none rewrite still rejects changed SoLd affine transform and source dimensions',async()=>{for(const key of ['Trnf','nonAffineTransform']){const bytes=syntheticNonePSD(await publicAcrylic(),{roles:['layer in']}),view=layer(bytes).placedObjectBytes,block=Buffer.from(view.buffer,view.byteOffset,view.length),at=block.indexOf(key);assert.ok(at>0);assert.equal(block.subarray(at+key.length,at+key.length+4).toString(),'VlLs');const first=at+key.length+4+4+4;block.writeDoubleBE(block.readDoubleBE(first)+.25,first);await expectRejected(bytes);}});
test('none-warp native export preserves original IN embedded layers and both geometry descriptors', {timeout:120000},async()=>{
  const bytes=syntheticNonePSD(await publicAcrylic()),opened=await openNativePSD(bytes),inputID=opened.roles.input.embeddedID,originalInput=readPSDLinkedFiles(bytes).find(file=>file.id===inputID).bytes;
  const depth={width:1,height:1,rgba:Uint8ClampedArray.from([128,128,128,255])},output=await buildNativePSD(bytes,{depth}),reopened=await openNativePSD(output),savedInput=readPSDLinkedFiles(output).find(file=>file.id===inputID).bytes;
  assert.equal(reopened.family,'acrylic');assert.equal(hash(originalInput),hash(savedInput));
  for(const name of names){assert.deepEqual(layer(output,name).placedWarpBytes,layer(bytes,name).placedWarpBytes);assert.deepEqual(layer(output,name).placedObjectBytes,layer(bytes,name).placedObjectBytes);}
});
