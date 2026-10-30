import {ORT_VERSION,check} from './contracts.mjs';

export function executionDevice(value='cpu'){
  check(value==='cpu'||value==='gpu','Choose CPU or GPU for Generate.');
  return value;
}
export function runtimeURL(device){
  return `https://cdn.jsdelivr.net/npm/onnxruntime-web@${ORT_VERSION}/dist/${executionDevice(device)==='gpu'?'ort.webgpu.min.mjs':'ort.min.mjs'}`;
}
export async function requestGPUAdapter(gpu=globalThis.navigator?.gpu){
  check(gpu&&typeof gpu.requestAdapter==='function','WebGPU is unavailable in this browser. Use current Chrome/Edge with hardware acceleration, or choose Generate CPU.');
  const adapter=await gpu.requestAdapter({powerPreference:'high-performance',forceFallbackAdapter:false});
  check(adapter,'No WebGPU hardware adapter is available. Choose Generate CPU.');
  const info=adapter.info||(adapter.requestAdapterInfo?await adapter.requestAdapterInfo():{});
  check(adapter.isFallbackAdapter!==true&&info?.isFallbackAdapter!==true&&!/swiftshader|llvmpipe|software renderer/i.test([info?.vendor,info?.architecture,info?.device,info?.description].join(' ')),
    'Only a software GPU adapter is available. Choose Generate CPU.');
  check(typeof adapter.requestDevice==='function'&&adapter.limits&&Number(adapter.limits.maxStorageBufferBindingSize)>=128*1024*1024&&Number(adapter.limits.maxBufferSize)>=256*1024*1024,
    'This GPU does not expose the buffer limits required by the depth models. Choose Generate CPU.');
  return {adapter,report:{vendor:String(info?.vendor||''),architecture:String(info?.architecture||''),device:String(info?.device||''),description:String(info?.description||''),
    maxStorageBufferBindingSize:Number(adapter.limits.maxStorageBufferBindingSize),maxBufferSize:Number(adapter.limits.maxBufferSize),hardware:true}};
}
export function configureONNXRuntime(ort,{device='cpu',threads=1,gpu}={}){
  executionDevice(device);
  ort.env.wasm.numThreads=threads;ort.env.wasm.proxy=false;
  // The WebGPU bundle selects its matching JSEP/Asyncify WASM. A single pinned
  // prefix keeps the JavaScript glue and binary on exactly the same release.
  ort.env.wasm.wasmPaths=`https://cdn.jsdelivr.net/npm/onnxruntime-web@${ORT_VERSION}/dist/`;
  if(device==='gpu'){
    check(gpu?.adapter,'GPU preflight must finish before creating a session.');
    ort.env.webgpu.adapter=gpu.adapter;
    ort.env.webgpu.powerPreference='high-performance';
    ort.env.webgpu.forceFallbackAdapter=false;
  }
}
export function inferenceSessionOptions(device){
  executionDevice(device);
  return {executionProviders:[device==='gpu'?'webgpu':'wasm'],graphOptimizationLevel:'basic',executionMode:'sequential',enableCpuMemArena:false,enableMemPattern:false,
    // Avoid a second packed copy of the 1.3GB FP32 weights during CPU session
    // creation. This changes memory layout only, never weights or precision.
    extra:{session:device==='gpu'?{disable_cpu_ep_fallback:'1'}:{disable_prepacking:'1'}}};
}
