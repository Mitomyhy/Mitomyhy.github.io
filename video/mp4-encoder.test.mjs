import test from 'node:test';
import assert from 'node:assert/strict';
import {getExportSupport,verifyVideoContainer,exportPhotoVideo} from './mp4-encoder.mjs';
test('unsupported browser reports export unavailable without a network fallback',()=>{
  assert.deepEqual(getExportSupport(),{supported:false,mp4:false,webm:false});
});
test('MP4 and WebM are detected by actual file header, never filename renaming',async()=>{
  const mp4=new Blob([new Uint8Array([0,0,0,16,102,116,121,112,105,115,111,109])]);
  const webm=new Blob([new Uint8Array([0x1a,0x45,0xdf,0xa3,0,0,0,0])]);
  await verifyVideoContainer(mp4,'mp4');await verifyVideoContainer(webm,'webm');
  await assert.rejects(verifyVideoContainer(webm,'mp4'),/different video format/);
  await assert.rejects(verifyVideoContainer(mp4,'webm'),/different video format/);
});
test('invalid input and already cancelled export never allocate a recorder',async()=>{
  await assert.rejects(exportPhotoVideo({drawFrame(){}}),/photo first/);
  await assert.rejects(exportPhotoVideo({image:{},drawFrame(){},duration:30}),/3–15/);
  const abort=new AbortController();abort.abort();
  await assert.rejects(exportPhotoVideo({image:{},drawFrame(){},signal:abort.signal}),{name:'AbortError'});
});
