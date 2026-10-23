import test from 'node:test';
import assert from 'node:assert/strict';
import {callPSDWorker} from '../depth-psd-client.js';
const pending=[];
globalThis.Worker=class {
  constructor(url,options){this.url=url;this.options=options;this.terminated=0;pending.push(this);}
  postMessage(value){this.request=value;}
  terminate(){this.terminated++;}
};
test('local PSD worker preserves caller buffers, returns values and terminates',async()=>{
  const bytes=new Uint8Array([1,2,3]),promise=callPSDWorker('open',{bytes});const worker=pending.at(-1);
  assert.match(worker.url.pathname,/depth-psd-worker.js$/);assert.equal(worker.options.type,'module');assert.equal(worker.request.bytes,bytes);assert.equal(bytes.length,3);
  const value={preview:{width:3,height:1}};worker.onmessage({data:{ok:true,value}});assert.equal(await promise,value);assert.equal(worker.terminated,1);
});
test('worker error and explicit cancellation are surfaced and stop the worker',async()=>{
  const work=callPSDWorker('open',{}),worker=pending.at(-1);worker.onmessage({data:{ok:false,error:'bad geometry',code:'NATIVE_PSD_UNSUPPORTED'}});
  await assert.rejects(work,error=>error.code==='NATIVE_PSD_UNSUPPORTED');assert.equal(worker.terminated,1);
  const control=new AbortController(),cancelled=callPSDWorker('preview',{}, {signal:control.signal}),running=pending.at(-1);control.abort();
  await assert.rejects(cancelled,error=>error.name==='AbortError');assert.equal(running.terminated,1);
  const count=pending.length;await assert.rejects(callPSDWorker('open',{}, {signal:control.signal}),error=>error.name==='AbortError');assert.equal(pending.length,count);
});
