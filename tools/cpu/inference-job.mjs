import {check,resource,trustedURL} from './contracts.mjs?v=20261030-memory-r80';
import {executionDevice} from './execution-device.mjs?v=20261030-memory-r80';

let sequence=0;
const SHAPES={moge:{points:[1,490,490,3],normal:[1,490,490,3],mask:[1,490,490],metric_scale:[1]},da2:{disparity:[1,1036,1036]}};
const own=value=>value instanceof ArrayBuffer;
const bounded=value=>String(value||'Unknown runtime error').replace(/[\u0000-\u001f\u007f]/g,' ').slice(0,800);

export function inferenceOutputShapes(kind){check(Object.hasOwn(SHAPES,kind),'Choose MoGe or DA2 for inference.');return SHAPES[kind];}
export function validateInferenceRequest({kind,input,spec,base,origins=[],device='cpu',threads=1}={}){
  inferenceOutputShapes(kind);executionDevice(device);resource(spec);
  const side=kind==='moge'?490:1036;
  check(spec.side===side&&spec.dtype==='float32',`${kind}: model must retain the reviewed FP32 input shape.`);
  if(kind==='moge')check(spec.tokens===1225&&spec.queryChunk===64,'MoGe must retain 1225 tokens / query64.');
  check([1,2,4].includes(threads)&&(threads===1||globalThis.crossOriginIsolated===true),'Multiple threads require crossOriginIsolated.');
  check(input instanceof Float32Array&&own(input.buffer)&&input.length===3*side*side,`${kind}: input must be an owned RGB Float32Array of the reviewed shape.`);
  check(input.every(Number.isFinite),`${kind}: input has NaN/Infinity.`);
  if(kind==='moge')check(input.every(value=>value>=0&&value<=1),'MoGe RGB input must be in 0..1.');
  check(typeof base==='string'&&base.length<4096,'Model manifest URL is required.');
  const target=new URL(base);check(!target.username&&!target.password&&!target.hash&&(target.protocol==='https:'||target.protocol==='http:'&&['localhost','127.0.0.1','[::1]'].includes(target.hostname)),'Model base must be HTTPS or loopback preview.');
  check(Array.isArray(origins)&&origins.length<=8&&origins.every(value=>typeof value==='string'&&new URL(value).origin===value&&value.startsWith('https://')),'Model origins are invalid.');
  if(spec.parts){
    check(!spec.url&&Array.isArray(spec.parts)&&spec.parts.length>0&&spec.parts.length<=64,'Model parts are invalid.');let total=0;
    for(const part of spec.parts){resource(part,{max:48*1024*1024});trustedURL(part.url,base,origins);total+=part.bytes;}
    check(total===spec.bytes,'Model part byte count does not match.');
  }else trustedURL(spec.url,base,origins);
  return {kind,input,spec,base,origins,device,threads};
}
export function validateInferenceResult(result,{kind,spec,device='cpu'}={}){
  const shapes=inferenceOutputShapes(kind),outputs=result?.outputs;
  check(outputs&&typeof outputs==='object'&&!Array.isArray(outputs)&&Object.keys(outputs).sort().join('|')===Object.keys(shapes).sort().join('|'),`${kind}: inference output names do not match.`);
  for(const [name,dims] of Object.entries(shapes)){
    const value=outputs[name],count=dims.reduce((a,b)=>a*b,1);
    check(value?.type==='float32'&&JSON.stringify(value.dims)===JSON.stringify(dims)&&value.data instanceof Float32Array&&own(value.data.buffer)&&value.data.length===count,`${kind}/${name}: inference output shape/type does not match.`);
    check(value.data.every(Number.isFinite),`${kind}/${name}: output has NaN/Infinity.`);
    if(name==='mask')check(value.data.every(v=>v>=0&&v<=1),'MoGe mask is outside 0..1.');
    if(name==='metric_scale')check(value.data[0]>0,'MoGe metric scale must be positive.');
  }
  const report=result.modelReport,parts=spec.parts?.length||1,cache=report?.cache;
  check(report&&report.sha256===spec.sha256&&report.bytes===spec.bytes&&report.side===spec.side&&report.dtype==='float32'&&report.tokens===(kind==='moge'?1225:null),'Model report does not match the verified graph.');
  check(report.requestedDevice===device&&report.actualDevice===device&&report.provider===(device==='gpu'?'webgpu':'wasm')&&report.cpuFallback===false,'Inference provider changed or silently fell back.');
  check(cache?.allBytesAndSHA256Verified===true&&cache.verifiedParts===parts&&Number.isInteger(cache.cacheHits)&&cache.cacheHits>=0&&cache.cacheHits<=parts,'Model hash verification report is incomplete.');
  if(device==='gpu')check(report.cpuFallbackDisabled===true&&result.gpuReport?.hardware===true&&result.gpuReport.deviceInitialized===true,'GPU inference was not verified on a hardware device.');
  else check(result.gpuReport===null,'CPU inference must not report a GPU run.');
  return result;
}
export function inferenceFailure(kind,phase,error){
  const detail=bounded(error?.message||error),abort=/\b(?:abort(?:ed)?|out of memory|memory access out of bounds)\b/i.test(detail);
  const prefix=`${kind.toUpperCase()} · ${String(phase||'startup').slice(0,100)}: `;
  return {kind,phase:String(phase||kind+'-startup').slice(0,100),abort,code:abort?'AI_RUNTIME_ABORTED':'AI_INFERENCE_FAILED',
    message:`${detail.startsWith(prefix)?detail:prefix+detail}${abort&&!detail.includes('WASM bị dừng')?' WASM bị dừng trước khi có kết quả hoàn chỉnh; có thể thiếu bộ nhớ/tài nguyên hoặc lỗi runtime. Đóng các tab AI đang chạy trùng rồi thử lại. PSD nguồn và kết quả trước được giữ nguyên.':''}`};
}

/** One nested worker per graph. Its ONNX/WASM/GPU resources are destroyed after
 * every run; none enter the long-lived Python/composition worker. A nested worker
 * is also owned by its parent worker, so terminating the pipeline terminates it.
 * signal supplies explicit cancellation for callers with a live parent scope. */
export function runInferenceJob({kind,input,spec,base,origins=[],device='cpu',threads=1,onProgress=()=>{},
  workerURL=new URL('./inference-worker.mjs?v=20261030-memory-r80',import.meta.url),WorkerImpl=globalThis.Worker,timeoutMs=20*60*1000,signal}={}){
  validateInferenceRequest({kind,input,spec,base,origins,device,threads});
  check(typeof WorkerImpl==='function'&&typeof onProgress==='function','Inference worker is unavailable.');
  check(Number.isSafeInteger(timeoutMs)&&timeoutMs>0&&timeoutMs<=2147483647,'Inference deadline is invalid.');
  check(!signal||typeof signal.addEventListener==='function'&&typeof signal.removeEventListener==='function','Cancellation signal is invalid.');
  if(signal?.aborted)return Promise.reject(Object.assign(new Error('Inference cancelled; source and previous result are preserved.'),{code:'CANCELLED'}));
  const id=`inference-${++sequence}`,owned=input.byteOffset===0&&input.byteLength===input.buffer.byteLength?input:input.slice();
  return new Promise((resolve,reject)=>{
    let worker=null,timer=null,finished=false,phase=kind+'-startup';
    function finish(error,result){
      if(finished)return;finished=true;clearTimeout(timer);signal?.removeEventListener('abort',abort);
      if(worker){worker.onmessage=null;worker.onerror=null;worker.onmessageerror=null;try{worker.terminate();}catch{}}
      error?reject(error):resolve(result);
    }
    function fail(error,code){const details=inferenceFailure(kind,phase,error);finish(Object.assign(new Error(details.message),details,code?{code}:{}));}
    function abort(){finish(Object.assign(new Error('Inference cancelled; source and previous result are preserved.'),{code:'CANCELLED',kind,phase}));}
    try{
      worker=new WorkerImpl(workerURL,{type:'module',name:`MITOM ${device.toUpperCase()} ${kind} isolated inference`});
      signal?.addEventListener('abort',abort,{once:true});
      if(signal?.aborted){abort();return;}
      timer=setTimeout(()=>fail(Error('Inference exceeded its deadline; no incomplete result was accepted.'),'AI_INFERENCE_TIMEOUT'),timeoutMs);
      worker.onmessage=({data})=>{
        if(finished||data?.id!==id)return;
        try{
          if(data.type==='result'){finish(null,validateInferenceResult(data.result,{kind,spec,device}));return;}
          if(data.type==='error'){
            if(typeof data.phase==='string'&&data.phase.length<=100)phase=data.phase;
            fail(Error(bounded(data.message)),/^[A-Z0-9_]{1,64}$/.test(data.code||'')?data.code:undefined);return;
          }
          if(data.type==='phase'){
            check(typeof data.phase==='string'&&data.phase.startsWith(kind+'-')&&data.phase.length<=100,'Invalid inference phase.');phase=data.phase;
            onProgress({type:'phase',phase});
          }else if(data.type==='download'){
            const progress={type:'download',kind};
            check(typeof data.phase==='string'&&data.phase.length<=100,'Invalid download phase.');progress.phase=data.phase;
            for(const key of ['part','parts','received','total','hashed'])if(data[key]!==undefined){check(Number.isFinite(data[key])&&data[key]>=0&&data[key]<=1_600_000_000,'Invalid model download progress.');progress[key]=data[key];}
            onProgress(progress);
          }else if(data.type==='timing'){
            check(data.timings&&typeof data.timings==='object'&&!Array.isArray(data.timings)&&Object.keys(data.timings).length<=12,'Invalid inference timing.');
            for(const [name,ms] of Object.entries(data.timings))check(name.startsWith(kind+'-')&&name.length<=100&&Number.isFinite(ms)&&ms>=0,'Invalid inference timing.');
            onProgress({type:'timing',timings:{...data.timings}});
          }
        }catch(error){fail(error);}
      };
      worker.onerror=event=>{event.preventDefault?.();fail(Error(bounded(event.message)));};
      worker.onmessageerror=()=>fail(Error('Inference worker returned an unreadable message.'));
      worker.postMessage({id,kind,input:owned.buffer,spec,base,origins,device,threads},[owned.buffer]);
    }catch(error){fail(error);}
  });
}
