// Synthetic fixtures made only from the public, pinned Acrylic template.
import {readFile} from 'node:fs/promises';
import {gunzipSync} from 'node:zlib';
import {createHash} from 'node:crypto';
import assert from 'node:assert/strict';
import {inspectPSD} from '../../psd-roundtrip.js';
export const hash=bytes=>createHash('sha256').update(bytes).digest('hex');
export const publicAcrylic=async()=>new Uint8Array(gunzipSync(await readFile(new URL('../../assets/templates/psd/template-acrylic.psd.gz',import.meta.url))));
const u32=value=>{const b=Buffer.alloc(4);b.writeUInt32BE(value);return b;};
const align4=bytes=>Buffer.concat([bytes,Buffer.alloc((4-bytes.length%4)%4)]);
export function geometryBytes(value){const b=Buffer.from(value);let p=12;const key=()=>{const n=b.readUInt32BE(p);p+=4;const s=b.subarray(p,p+(n||4));p+=n||4;return s.toString('ascii');};const text=()=>{const n=b.readUInt32BE(p);p+=4+n*2;};text();key();assert.equal(b.readUInt32BE(p),18);p+=4;for(let i=0;i<2;i++){key();assert.equal(b.subarray(p,p+4).toString(),'TEXT');p+=4;text();}return b.subarray(p);}
export function noneWarp(original){
  const b=Buffer.from(original),style=b.indexOf('warpCustom'),mesh=b.indexOf('customEnvelopeWarp')-4;
  assert.ok(style>4&&mesh>style);assert.equal(b.readUInt32BE(style-4),10);assert.equal(b.readUInt32BE(mesh),18);assert.equal(b.subarray(mesh+22,mesh+26).toString(),'Objc');assert.equal(b.readUInt32BE(22),9);
  const descriptor=Buffer.concat([b.subarray(0,style-4),u32(8),Buffer.from('warpNone'),b.subarray(style+10,mesh)]);descriptor.writeUInt32BE(8,22);
  // PlLd prefix preceding this warp is 125 bytes for a native 36-byte UUID.
  const padding=Buffer.alloc((4-(125+descriptor.length)%4)%4);
  return {body:descriptor.subarray(8),bytes:Buffer.concat([descriptor,padding])};
}
export function noneObject(object,originalWarp,none){
  const b=Buffer.from(object),needle=Buffer.concat([u32(4),Buffer.from('warpObjc')]),at=b.indexOf(needle);assert.ok(at>0);assert.equal(b.indexOf(needle,at+1),-1);
  const body=Buffer.from(originalWarp).subarray(8,-2),start=at+needle.length;
  assert.deepEqual(b.subarray(start,start+body.length),body);assert.equal(b.readUInt16BE(b.length-2),0);
  return align4(Buffer.concat([b.subarray(0,start),none.body,b.subarray(start+body.length,b.length-2)]));
}
export function syntheticNonePSD(value,{roles=['layer in','layer depth map','layer cắt'],counterpart='both'}={}){
  const input=Buffer.from(value),bytes=Buffer.from(input),info=inspectPSD(bytes),changes=[],deltas=new Map();
  const colorSize=bytes.readUInt32BE(26),resourcesAt=30+colorSize,resourcesSize=bytes.readUInt32BE(resourcesAt),layerSizeAt=resourcesAt+4+resourcesSize,infoSizeAt=layerSizeAt+4;
  let record=infoSizeAt+6;
  for(const layer of info.layers){const channels=bytes.readUInt16BE(record+16),extraAt=record+18+channels*6+12,extraSize=bytes.readUInt32BE(extraAt);deltas.set(layer.index,{at:extraAt,delta:0});record=extraAt+4+extraSize;}
  for(const layer of info.layers.filter(layer=>roles.includes(layer.name))){
    const original=Buffer.from(layer.placedWarpBytes),none=noneWarp(original),object=noneObject(layer.placedObjectBytes,original,none);
    for(const [kind,data]of [['PlLd',none.bytes],['SoLd',object]]){
      if(counterpart!=='both'&&counterpart!==kind)continue;
      const dataAt=kind==='PlLd'?layer.placedWarpBytes.byteOffset-bytes.byteOffset-125:layer.placedObjectBytes.byteOffset-bytes.byteOffset,tagAt=dataAt-12,size=bytes.readUInt32BE(dataAt-4);
      assert.equal(bytes.subarray(tagAt,tagAt+8).toString(),'8BIM'+kind);
      const payload=kind==='PlLd'?Buffer.concat([bytes.subarray(dataAt,dataAt+125),data]):data;
      const replacement=Buffer.concat([bytes.subarray(tagAt,tagAt+8),u32(payload.length),payload,Buffer.alloc(payload.length%2)]),oldSize=12+size+size%2;
      changes.push({at:tagAt,oldSize,bytes:replacement});deltas.get(layer.index).delta+=replacement.length-oldSize;
    }
  }
  const total=[...deltas.values()].reduce((n,row)=>n+row.delta,0);
  for(const row of deltas.values())if(row.delta)bytes.writeUInt32BE(bytes.readUInt32BE(row.at)+row.delta,row.at);
  bytes.writeUInt32BE(bytes.readUInt32BE(layerSizeAt)+total,layerSizeAt);bytes.writeUInt32BE(bytes.readUInt32BE(infoSizeAt)+total,infoSizeAt);
  const result=[];let at=0;for(const change of changes.sort((a,b)=>a.at-b.at)){result.push(bytes.subarray(at,change.at),change.bytes);at=change.at+change.oldSize;}result.push(bytes.subarray(at));
  return new Uint8Array(Buffer.concat(result));
}
