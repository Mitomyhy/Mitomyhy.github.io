import {ORT_VERSION,PYODIDE_VERSION,PROFILE,check,size,validateManifest} from './contracts.mjs';
import {loadPinnedText} from './model-cache.mjs';
import {BRIDGE_PYTHON} from './bridge-python.mjs?v=20261022-flow-r5';
import {executionDevice,requestGPUAdapter} from './execution-device.mjs?v=20261030-memory-r80';
import {runInferenceJob} from './inference-job.mjs?v=20261030-memory-r80';

let used=false, running=false, composeCached=null;
self.onmessage=async({data})=>{
  if(data?.command==='compose'){
    if(!composeCached||running){self.postMessage({id:data.id,type:'error',code:'CPU_NO_CACHED_MAPS',message:'Chưa có dữ liệu AI để tạo 3D; hãy tạo map trước.'});return;}
    running=true;
    try{await composeCached(data);}catch(error){self.postMessage({id:data.id,type:'error',code:'CPU_COMPOSITION_FAILED',message:String(error?.message||error)});}
    finally{running=false;}
    return;
  }
  if(used)return;used=true;running=true;
  const {id,manifestURL,rgba,width,height,productType,options,threads,outputMode='full',device='cpu'}=data||{};
  const send=(type,values={})=>self.postMessage({...values,id,type});
  const timings={}, modelReports={};let pyodide=null,gpu=null,currentPhase='validation';
  async function stage(phase,run){currentPhase=phase;send('phase',{phase});const start=performance.now();const result=await run();timings[phase]=performance.now()-start;send('timing',{timings:{...timings}});return result;}
  function finite(value,name){check(value.every(Number.isFinite),`${name} có NaN/Infinity.`);return value;}
  function readBytes(name){return pyodide.FS.readFile('/mitom_work/'+name);}
  function readFloat(name){const bytes=readBytes(name);return finite(new Float32Array(bytes.buffer,bytes.byteOffset,bytes.byteLength/4),name);}
  function pyCall(name,config){
    const fn=pyodide.globals.get(name);
    try{return JSON.parse(fn(JSON.stringify(config)));}finally{fn.destroy();}
  }
  async function infer(kind,inputFile,spec,base,origins){
    // The model graph and ONNX WASM heap live in a short-lived, one-model
    // worker, not beside Pyodide/OpenCV. Terminating it reclaims the whole
    // runtime before DA2 starts, rather than retaining MoGe's peak heap.
    const input=readFloat(inputFile);
    pyodide.FS.unlink('/mitom_work/'+inputFile);
    const job=await runInferenceJob({kind,input,spec,base,origins,device,threads,onProgress:event=>{
      if(event.type==='phase')currentPhase=event.phase;
      if(event.type==='timing')Object.assign(timings,event.timings);
      send(event.type,event);
    }});
    const shapes=kind==='moge'?{points:[1,490,490,3],normal:[1,490,490,3],mask:[1,490,490],metric_scale:[1]}:{disparity:[1,1036,1036]};
    for(const [name,shape] of Object.entries(shapes)){
      const t=job.outputs[name];check(t&&t.type==='float32'&&t.data instanceof Float32Array&&JSON.stringify(t.dims)===JSON.stringify(shape),`Output ${kind}/${name} sai hợp đồng.`);
      finite(t.data,name);
      if(name==='mask')check(t.data.every(v=>v>=0&&v<=1),'MoGe mask ngoài0..1.');
      if(name==='metric_scale')check(t.data[0]>0,'MoGe metric scale không dương.');
      pyodide.FS.writeFile(`/mitom_work/raw-${name}.f32`,new Uint8Array(t.data.buffer,t.data.byteOffset,t.data.byteLength));
    }
    modelReports[kind]=job.modelReport;
    if(job.gpuReport&&gpu)Object.assign(gpu.report,job.gpuReport);
  }
  try{
    executionDevice(device);
    size(width,height);check(rgba instanceof ArrayBuffer&&rgba.byteLength===width*height*4,'Nguồn RGBA không hợp lệ.');
    check(['acrylic','ceramic_circle','ceramic_heart'].includes(productType),'Loại sản phẩm không hợp lệ.');
    check(['maps','full'].includes(outputMode),'Output mode không hợp lệ.');
    check([1,2,4].includes(threads)&&(threads===1||self.crossOriginIsolated),'Nhiều luồng cần crossOriginIsolated.');
    check(options&&typeof options==='object'&&!Array.isArray(options)&&JSON.stringify(options).length<100_000,'Tùy chọn xử lý không hợp lệ.');
    if(device==='gpu')gpu=await stage('gpu-capability',()=>requestGPUAdapter());
    send('device',{requestedDevice:device,actualDevice:device,provider:device==='gpu'?'webgpu':'wasm',...(gpu?{adapter:gpu.report}:{})});
    const base=new URL(manifestURL,self.location.href).href;
    const manifest=await stage('manifest',async()=>{const response=await fetch(base,{cache:'no-store',credentials:'omit'});check(response.ok,'Chưa tải được manifest CPU.');return validateManifest(await response.json(),base);});
    const pyIndex=`https://cdn.jsdelivr.net/pyodide/v${PYODIDE_VERSION}/full/`;
    const {loadPyodide}=await stage('python-runtime',()=>import(pyIndex+'pyodide.mjs'));
    pyodide=await loadPyodide({indexURL:pyIndex});
    await stage('python-packages',()=>pyodide.loadPackage(['numpy','scipy','pillow','opencv-python']));
    pyodide.FS.mkdirTree('/mitom_native');pyodide.FS.mkdirTree('/mitom_work');
    await stage('native-module-integrity',async()=>{
      for(const entry of manifest.python.modules){const source=await loadPinnedText(entry,{base});pyodide.FS.writeFile('/mitom_native/'+entry.name,source,{encoding:'utf8'});}
    });
    pyodide.globals.set('_mitom_stage',name=>{currentPhase=String(name).slice(0,100);send('phase',{phase:currentPhase});});
    pyodide.runPython("import sys\nsys.path.insert(0,'/mitom_native')\nsys.dont_write_bytecode=True");
    pyodide.runPython(BRIDGE_PYTHON);
    pyodide.FS.writeFile('/mitom_work/source.rgba',new Uint8Array(rgba));
    const preparation=await stage('native-preparation',()=>pyCall('mitom_prepare',{width,height,moge_side:490,da2_graph_side:1036,output_max_side:2048}));
    size(preparation.width,preparation.height,2048*2048);
    // Source may be bounded by native premultiplied PIL Lanczos; keep it as
    // the authoritative output-grid texture, never mix the old UI pixel grid.
    pyodide.FS.unlink('/mitom_work/source.rgba');
    await infer('moge','prep-moge_f32',manifest.models.moge,base,manifest.modelOrigins||[]);
    const camera=await stage('native-scipy-camera-recovery',()=>pyCall('mitom_recover',{width:490,height:490}));
    for(const name of ['points','normal','mask','metric_scale'])pyodide.FS.unlink(`/mitom_work/raw-${name}.f32`);
    await infer('da2','prep-da2_f32',manifest.models.da2,base,manifest.modelOrigins||[]);
    async function produce(mode,settings,type,inferenceReused=false){
      check(['maps','full'].includes(mode)&&['acrylic','ceramic_circle','ceramic_heart'].includes(type),'Chế độ hoặc sản phẩm không hợp lệ.');
      check(settings&&typeof settings==='object'&&!Array.isArray(settings)&&JSON.stringify(settings).length<100_000,'Tùy chọn xử lý không hợp lệ.');
      const metadata=await stage(mode==='maps'?'native-depth-map-export':'native-relief-composition',()=>pyCall('mitom_compose',{options:settings,product_type:type,output_mode:mode}));
      // Inference moves to WebGPU; the unchanged native Python preparation,
      // geometry recovery, relief composition and PNG export still use CPU.
      if(metadata.model_budget)metadata.model_budget={...metadata.model_budget,
        label:`Browser ${device.toUpperCase()} Standard FP32 1225; native composition on CPU; NOT 14400-token UHD inference`};
      const w=preparation.width,h=preparation.height,n=w*h;
      const result={width:w,height:h,depth:readFloat('final-height_f32'),baseline:readFloat('final-baseline_f32'),
      depth16:new Uint16Array(readBytes('final-depth16_u16').buffer),alpha:readBytes('final-alpha_u8'),
      normal:readBytes('final-normal_rgb_u8'),support:readBytes('final-support_u8'),rgba:readBytes('final-rgba_u8'),
      depth16PNG:readBytes('final-depth_png'),previewPNG:readBytes('final-preview_png'),alphaPNG:readBytes('final-alpha_png'),
      normalPNG:readBytes('final-normal_png'),supportPNG:readBytes('final-support_png'),
      mapDepth:readFloat('final-map-height_f32'),mogeDepth:readFloat('final-moge-height_f32'),da2Depth:readFloat('final-da2-height_f32'),
      mapAlpha:readBytes('final-map-alpha_u8'),mogeAlpha:readBytes('final-moge-alpha_u8'),da2Alpha:readBytes('final-da2-alpha_u8'),
      mapDepth16PNG:readBytes('final-map_depth_png'),mogeDepth16PNG:readBytes('final-moge_depth_png'),da2Depth16PNG:readBytes('final-da2_depth_png'),
      metadata:{...metadata,profile:PROFILE,models:modelReports,preparation,camera,timings,threads,
        requestedDevice:device,actualDevice:device,runtime:{onnx:ORT_VERSION,python:PYODIDE_VERSION,provider:device==='gpu'?'webgpu':'wasm-cpu',inferenceDevice:device,preparationDevice:'cpu',compositionDevice:'cpu',cpuFallback:false,...(gpu?{adapter:gpu.report}:{})},
        outputMode:mode,inferenceReused,fullNativeComposition:mode==='full',nativeDepthComposition:true,nativeUHDInference:false,photoshopRequired:false,artworkUploaded:false}};
      check(result.depth.length===n&&result.alpha.length===n&&result.rgba.length===n*4&&result.support.length===n&&result.depth16.length===n&&result.normal.length===n*3,'Kích thước kết quả không khớp ảnh nguồn đã xử lý.');
      check(result.depth.every(v=>v>=0&&v<=1),'Height cuối ngoài0..1.');
      for(const prefix of ['map','moge','da2']){
        check(result[prefix+'Depth'].length===n&&result[prefix+'Alpha'].length===n,'Kích thước map không khớp canvas.');
        check(result[prefix+'Depth'].every(v=>v>=0&&v<=1),'Map ngoài0..1.');
      }
      return result;
    }
    function publish(result,commandId){
      const transfers=[...new Set(Object.values(result).filter(ArrayBuffer.isView).map(value=>value.buffer))];
      check(transfers.every(value=>value instanceof ArrayBuffer),'Không chuyển shared WASM memory sang giao diện.');
      send('phase',{phase:'complete'});self.postMessage({id:commandId,type:'result',result,cacheReady:true},transfers);
    }
    // Only the reviewed native composition may be replayed. AI outputs remain
    // in this worker; there is no second model inference or artwork upload.
    composeCached=async command=>publish(await produce('full',command.options,command.productType||productType,true),command.id);
    publish(await produce(outputMode,options,productType),id);
  }catch(error){
    const phase=error?.phase||currentPhase,detail=String(error?.message||error).slice(0,2000);
    send('error',{message:error?.kind?`${device.toUpperCase()} · ${detail}`:`${device.toUpperCase()} · ${phase}: ${detail}`,code:error?.code||(device==='gpu'?'GPU_FULL_FLOW_FAILED':'CPU_FULL_FLOW_FAILED'),phase,...(error?.abort===true?{abort:true}:{})});
  }
  finally{running=false;}
};
