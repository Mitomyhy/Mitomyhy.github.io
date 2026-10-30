import {ORT_VERSION,check} from './contracts.mjs?v=20261030-memory-r80';
import {loadPinnedModel} from './model-cache.mjs?v=20261030-memory-r80';
import {runtimeURL,requestGPUAdapter,configureONNXRuntime,inferenceSessionOptions} from './execution-device.mjs?v=20261030-memory-r80';
import {validateInferenceRequest,validateInferenceResult,inferenceOutputShapes,inferenceFailure} from './inference-job.mjs?v=20261030-memory-r80';

/** Fixed inference only. No Python runtime, composition, PSD or artwork upload.
 * Dependencies are injectable for small synthetic cleanup tests, never supplied
 * by worker messages or user options. */
export async function executeInferenceRequest(request,{onProgress=()=>{},loadModel=loadPinnedModel,loadRuntime=()=>import(runtimeURL(request.device)),getGPU=requestGPUAdapter}={}){
  const {kind,spec,base,origins=[],device='cpu',threads=1}=request||{};
  const input=request?.input instanceof ArrayBuffer?new Float32Array(request.input):request?.input;
  validateInferenceRequest({kind,input,spec,base,origins,device,threads});
  let phase=kind+'-startup',session=null,inputTensor=null,raw=null,loaded=null,result=null,failure=null,gpu=null;
  const timings={};
  async function stage(name,run){phase=name;onProgress({type:'phase',phase});const start=performance.now();const value=await run();timings[phase]=performance.now()-start;onProgress({type:'timing',timings:{...timings}});return value;}
  try{
    if(device==='gpu')gpu=await stage(kind+'-gpu-capability',()=>getGPU());
    const ort=await stage(kind+'-runtime',()=>loadRuntime());configureONNXRuntime(ort,{device,threads,gpu});
    loaded=await stage(kind+'-download-verify',()=>loadModel(spec,{base,origins,progress:value=>onProgress({type:'download',kind,...value})}));
    const cache=loaded.cache;
    session=await stage(kind+'-session',()=>ort.InferenceSession.create(loaded.bytes,inferenceSessionOptions(device)));
    loaded.bytes=null;loaded=null;
    if(device==='gpu'){
      const actual=await ort.env.webgpu.device;
      check(actual&&typeof actual.queue?.onSubmittedWorkDone==='function','The ONNX session did not initialize a real WebGPU device.');
      gpu.report.deviceInitialized=true;gpu.report.deviceLimits={maxStorageBufferBindingSize:Number(actual.limits.maxStorageBufferBindingSize),maxBufferSize:Number(actual.limits.maxBufferSize)};
    }
    inputTensor=new ort.Tensor('float32',input,[1,3,spec.side,spec.side]);
    raw=await stage(kind+'-inference',()=>session.run({image:inputTensor}));
    const outputs={};
    for(const [name,dims] of Object.entries(inferenceOutputShapes(kind))){
      const tensor=raw[name];check(tensor?.type==='float32'&&JSON.stringify(tensor.dims)===JSON.stringify(dims)&&tensor.data instanceof Float32Array,`${kind}/${name}: inference output shape/type does not match.`);
      // A detached child result must never reference Emscripten linear memory.
      outputs[name]={type:'float32',dims:[...dims],data:Float32Array.from(tensor.data)};
    }
    result=validateInferenceResult({outputs,modelReport:{sha256:spec.sha256,bytes:spec.bytes,cache,provider:device==='gpu'?'webgpu':'wasm',requestedDevice:device,actualDevice:device,cpuFallback:false,
      ...(device==='gpu'?{cpuFallbackDisabled:true}:{}),dtype:'float32',side:spec.side,tokens:kind==='moge'?1225:null,onnxVersion:ORT_VERSION},gpuReport:gpu?.report||null,timings},{kind,spec,device});
  }catch(error){failure=inferenceFailure(kind,phase,error);}
  finally{
    if(loaded)loaded.bytes=null;loaded=null;
    // Dispose even unexpected output tensors and tensors produced before a
    // validation error. A session is released before any result is published.
    for(const tensor of new Set(Object.values(raw||{})))try{await tensor?.dispose?.();}catch(error){failure??=inferenceFailure(kind,phase,error);}
    try{await inputTensor?.dispose?.();}catch(error){failure??=inferenceFailure(kind,phase,error);}
    try{await session?.release();}catch(error){failure??=inferenceFailure(kind,phase,error);}
  }
  if(failure)throw Object.assign(new Error(failure.message),failure);
  return result;
}

if(typeof self!=='undefined'&&typeof self.postMessage==='function'){
  let used=false;
  self.onmessage=async({data})=>{
    if(used)return;used=true;const id=data?.id;
    try{
      const result=await executeInferenceRequest(data,{onProgress:value=>self.postMessage({id,...value})});
      const transfers=Object.values(result.outputs).map(output=>output.data.buffer);
      self.postMessage({id,type:'result',result},transfers);
    }catch(error){
      const details=error?.kind?error:inferenceFailure(['moge','da2'].includes(data?.kind)?data.kind:'moge','startup',error);
      self.postMessage({id,type:'error',message:String(details.message).slice(0,1400),code:details.code,phase:details.phase,abort:details.abort});
    }finally{self.close();}
  };
}
