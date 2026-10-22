import {ORT_VERSION,PYODIDE_VERSION,PROFILE,check,size,validateManifest} from './contracts.mjs';
import {loadPinnedModel,loadPinnedText} from './model-cache.mjs';
import {BRIDGE_PYTHON} from './bridge-python.mjs?v=20261022-flow-r5';

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
  const {id,manifestURL,rgba,width,height,productType,options,threads,outputMode='full'}=data||{};
  const send=(type,values={})=>self.postMessage({id,type,...values});
  const timings={}, modelReports={};let pyodide=null,session=null;
  async function stage(phase,run){send('phase',{phase});const start=performance.now();const result=await run();timings[phase]=performance.now()-start;send('timing',{timings:{...timings}});return result;}
  function finite(value,name){check(value.every(Number.isFinite),`${name} có NaN/Infinity.`);return value;}
  function readBytes(name){return pyodide.FS.readFile('/mitom_work/'+name);}
  function readFloat(name){const bytes=readBytes(name);return finite(new Float32Array(bytes.buffer,bytes.byteOffset,bytes.byteLength/4),name);}
  function pyCall(name,config){
    const fn=pyodide.globals.get(name);
    try{return JSON.parse(fn(JSON.stringify(config)));}finally{fn.destroy();}
  }
  async function infer(ort,kind,inputFile,spec,base,origins){
    let loaded=await stage(kind+'-download-verify',()=>loadPinnedModel(spec,{base,origins,progress:value=>send('download',{kind,...value})}));
    const cache=loaded.cache;
    session=await stage(kind+'-session',()=>ort.InferenceSession.create(loaded.bytes,{
      executionProviders:['wasm'],graphOptimizationLevel:'basic',enableCpuMemArena:false,enableMemPattern:false
    }));
    loaded.bytes=null;loaded=null;
    const values=readFloat(inputFile),side=spec.side;
    check(values.length===3*side*side,`${kind}: tensor input sai kích thước.`);
    if(kind==='moge')check(values.every(v=>v>=0&&v<=1),'MoGe RGB phải trong0..1.');
    // DA2 is already normalized by the original OpenCV/float64 preparation.
    const raw=await stage(kind+'-inference',()=>session.run({image:new ort.Tensor('float32',values,[1,3,side,side])}));
    const shapes=kind==='moge'?{points:[1,490,490,3],normal:[1,490,490,3],mask:[1,490,490],metric_scale:[1]}:{disparity:[1,1036,1036]};
    for(const [name,shape] of Object.entries(shapes)){
      const t=raw[name];check(t&&t.type==='float32'&&JSON.stringify(t.dims)===JSON.stringify(shape),`Output ${kind}/${name} sai hợp đồng.`);
      finite(t.data,name);
      if(name==='mask')check(t.data.every(v=>v>=0&&v<=1),'MoGe mask ngoài0..1.');
      if(name==='metric_scale')check(t.data[0]>0,'MoGe metric scale không dương.');
      pyodide.FS.writeFile(`/mitom_work/raw-${name}.f32`,new Uint8Array(t.data.buffer,t.data.byteOffset,t.data.byteLength));
      t.dispose?.();
    }
    await session.release();session=null;
    modelReports[kind]={sha256:spec.sha256,bytes:spec.bytes,cache,provider:'wasm',dtype:'float32',side,tokens:kind==='moge'?1225:null};
  }
  try{
    size(width,height);check(rgba instanceof ArrayBuffer&&rgba.byteLength===width*height*4,'Nguồn RGBA không hợp lệ.');
    check(['acrylic','ceramic_circle','ceramic_heart'].includes(productType),'Loại sản phẩm không hợp lệ.');
    check(['maps','full'].includes(outputMode),'Output mode không hợp lệ.');
    check([1,2,4].includes(threads)&&(threads===1||self.crossOriginIsolated),'Nhiều luồng cần crossOriginIsolated.');
    check(options&&typeof options==='object'&&!Array.isArray(options)&&JSON.stringify(options).length<100_000,'Tùy chọn xử lý không hợp lệ.');
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
    pyodide.globals.set('_mitom_stage',name=>send('phase',{phase:String(name).slice(0,100)}));
    pyodide.runPython("import sys\nsys.path.insert(0,'/mitom_native')\nsys.dont_write_bytecode=True");
    pyodide.runPython(BRIDGE_PYTHON);
    pyodide.FS.writeFile('/mitom_work/source.rgba',new Uint8Array(rgba));
    const preparation=await stage('native-preparation',()=>pyCall('mitom_prepare',{width,height,moge_side:490,da2_graph_side:1036,output_max_side:2048}));
    size(preparation.width,preparation.height,2048*2048);
    // Source may be bounded by native premultiplied PIL Lanczos; keep it as
    // the authoritative output-grid texture, never mix the old UI pixel grid.
    pyodide.FS.unlink('/mitom_work/source.rgba');
    const ort=await stage('onnx-runtime',()=>import(`https://cdn.jsdelivr.net/npm/onnxruntime-web@${ORT_VERSION}/dist/ort.min.mjs`));
    ort.env.wasm.numThreads=threads;ort.env.wasm.proxy=false;
    ort.env.wasm.wasmPaths=`https://cdn.jsdelivr.net/npm/onnxruntime-web@${ORT_VERSION}/dist/`;
    await infer(ort,'moge','prep-moge_f32',manifest.models.moge,base,manifest.modelOrigins||[]);
    const camera=await stage('native-scipy-camera-recovery',()=>pyCall('mitom_recover',{width:490,height:490}));
    for(const name of ['points','normal','mask','metric_scale'])pyodide.FS.unlink(`/mitom_work/raw-${name}.f32`);
    await infer(ort,'da2','prep-da2_f32',manifest.models.da2,base,manifest.modelOrigins||[]);
    async function produce(mode,settings,type,inferenceReused=false){
      check(['maps','full'].includes(mode)&&['acrylic','ceramic_circle','ceramic_heart'].includes(type),'Chế độ hoặc sản phẩm không hợp lệ.');
      check(settings&&typeof settings==='object'&&!Array.isArray(settings)&&JSON.stringify(settings).length<100_000,'Tùy chọn xử lý không hợp lệ.');
      const metadata=await stage(mode==='maps'?'native-depth-map-export':'native-relief-composition',()=>pyCall('mitom_compose',{options:settings,product_type:type,output_mode:mode}));
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
        runtime:{onnx:ORT_VERSION,python:PYODIDE_VERSION,provider:'wasm-cpu'},
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
  }catch(error){send('error',{message:String(error?.message||error),code:'CPU_FULL_FLOW_FAILED'});}
  finally{running=false;if(session)try{await session.release();}catch{}}
};
