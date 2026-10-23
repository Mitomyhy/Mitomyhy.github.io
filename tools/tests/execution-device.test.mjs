import test from 'node:test';
import assert from 'node:assert/strict';
import {executionDevice,runtimeURL,requestGPUAdapter,configureONNXRuntime,inferenceSessionOptions} from '../cpu/execution-device.mjs';
import {createCPUPipeline} from '../cpu/client.mjs';

function adapter(extra={}){return{info:{vendor:'test-hardware',architecture:'test'},limits:{maxStorageBufferBindingSize:1024**3,maxBufferSize:2*1024**3},requestDevice(){},...extra};}
test('CPU and GPU select different pinned ONNX bundles and explicit providers',()=>{
  assert.equal(executionDevice(),'cpu');assert.throws(()=>executionDevice('auto'));
  assert.match(runtimeURL('cpu'),/@1\.23\.2\/dist\/ort\.min\.mjs$/);
  assert.match(runtimeURL('gpu'),/@1\.23\.2\/dist\/ort\.webgpu\.min\.mjs$/);
  assert.deepEqual(inferenceSessionOptions('cpu').executionProviders,['wasm']);
  const gpu=inferenceSessionOptions('gpu');assert.deepEqual(gpu.executionProviders,['webgpu']);
  assert.equal(gpu.extra.session.disable_cpu_ep_fallback,'1');
});
test('GPU preflight rejects missing, absent, software and insufficient adapters',async()=>{
  await assert.rejects(requestGPUAdapter(null),/WebGPU is unavailable/);
  await assert.rejects(requestGPUAdapter({requestAdapter:async()=>null}),/No WebGPU hardware adapter/);
  await assert.rejects(requestGPUAdapter({requestAdapter:async()=>adapter({isFallbackAdapter:true})}),/software GPU/);
  await assert.rejects(requestGPUAdapter({requestAdapter:async()=>adapter({info:{description:'Google SwiftShader'}})}),/software GPU/);
  await assert.rejects(requestGPUAdapter({requestAdapter:async()=>adapter({limits:{maxStorageBufferBindingSize:64*1024**2,maxBufferSize:256*1024**2}})}),/buffer limits/);
});
test('GPU preflight pins a high performance hardware adapter and matching runtime files',async()=>{
  const hardware=adapter();let options;
  const gpu=await requestGPUAdapter({requestAdapter:async value=>{options=value;return hardware;}});
  assert.deepEqual(options,{powerPreference:'high-performance',forceFallbackAdapter:false});
  assert.equal(gpu.adapter,hardware);assert.equal(gpu.report.hardware,true);
  const ort={env:{wasm:{},webgpu:{}}};configureONNXRuntime(ort,{device:'gpu',threads:1,gpu});
  assert.equal(ort.env.webgpu.adapter,hardware);assert.equal(ort.env.webgpu.forceFallbackAdapter,false);
  assert.equal(ort.env.wasm.proxy,false);assert.match(ort.env.wasm.wasmPaths,/@1\.23\.2\/dist\/$/);
  assert.throws(()=>configureONNXRuntime(ort,{device:'gpu'}),/GPU preflight/);
});
test('client sends the selected device and composition reuses that worker; GPU failure never reruns CPU',async()=>{
  const priorWorker=globalThis.Worker,priorLocation=globalThis.location,pending=[];
  globalThis.location={href:'https://example.test/'};
  globalThis.Worker=class{constructor(url,options){this.url=url;this.options=options;this.messages=[];this.terminated=0;pending.push(this);}postMessage(message){this.messages.push(message);}terminate(){this.terminated++;}};
  const pipeline=createCPUPipeline({manifestURL:'/manifest.json'}),input={rgba:new Uint8Array(8*8*4),width:8,height:8};
  try{
    const first=pipeline.run({...input,device:'gpu'}),worker=pending.at(-1),request=worker.messages.at(-1);
    assert.equal(request.device,'gpu');assert.match(worker.options.name,/GPU/);assert.equal(input.rgba.byteLength,256);
    worker.onmessage({data:{id:request.id,type:'result',result:{metadata:{actualDevice:'gpu'}},cacheReady:true}});
    assert.equal((await first).metadata.actualDevice,'gpu');
    const composed=pipeline.compose();assert.equal(pending.length,1);assert.equal(worker.messages.at(-1).command,'compose');
    worker.onmessage({data:{id:request.id,type:'result',result:{metadata:{actualDevice:'gpu',inferenceReused:true}},cacheReady:true}});await composed;
    const failure=pipeline.run({...input,device:'gpu'}),failed=pending.at(-1);failed.onmessage({data:{id:failed.messages[0].id,type:'error',code:'GPU_FULL_FLOW_FAILED',message:'GPU allocation failed'}});
    await assert.rejects(failure,error=>error.code==='GPU_FULL_FLOW_FAILED');assert.equal(pending.length,2,'no CPU retry');
    assert.throws(()=>pipeline.run({...input,device:'auto'}),/Choose CPU or GPU/);
  }finally{pipeline.dispose();globalThis.Worker=priorWorker;globalThis.location=priorLocation;}
});
