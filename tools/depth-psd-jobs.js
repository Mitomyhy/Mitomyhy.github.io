/** Two independent native-template jobs. No Photoshop bridge or server uploads. */
export function createPSDJobs({open,prepareArtwork,createPipeline,build,onChange=()=>{},isExternalBusy=()=>false}={}) {
  if (![open,prepareArtwork,createPipeline,build].every(fn=>typeof fn==='function')) throw Error('Thiếu engine PSD.');
  const jobs=[null,null]; let parallel=1,disposed=false;
  const emit=()=>onChange(snapshot());
  const check=()=>{if(disposed)throw Error('Đã đóng hàng đợi PSD.');};
  const slot=index=>{check();if(!Number.isInteger(index)||index<0||index>1)throw Error('Chỉ có hai tác vụ PSD.');return jobs[index];};
  function snapshot(){return {parallel,running:jobs.filter(j=>j?.status==='running').length,jobs:jobs.map(j=>j?{id:j.id,name:j.name,family:j.native?.family||null,title:j.native?.title||'',status:j.status,message:j.message,progress:j.progress,artwork:j.artwork,hasArtwork:j.hasArtwork,hasPSD:!!j.output,hasMap:!!j.map,error:j.error||null}:null)};}
  const current=(index,job,serial)=>!disposed&&jobs[index]===job&&job.serial===serial;
  const cancelJob=job=>{job.serial++;job.abort?.abort();job.pipeline?.dispose();job.pipeline=null;};
  async function load(index,input){
    const previous=slot(index);if(previous)cancelJob(previous);
    const job={id:index,name:String(input.name||'Template PSD').slice(0,200),status:'loading',message:'Đang đọc đúng layer IN…',serial:0,abort:new AbortController(),output:null,map:null,hasArtwork:false};
    jobs[index]=job;emit();const serial=job.serial;
    try {
      const native=await open(input,{signal:job.abort.signal});
      if(!current(index,job,serial))return;
      job.native=native;job.artwork=native.artwork;job.hasArtwork=!input.family&&!native.isBundledBlank&&native.artwork.rgba.some((value,index)=>index%4===3&&value>0);
      job.status='ready';job.message=job.hasArtwork?'Đã đọc artwork đã lưu trong IN · sẵn sàng Generate.':'Template đã mở · thêm artwork vào IN.';emit();
    } catch(error){if(!current(index,job,serial))return;job.status='error';job.error=error.message;job.message=error.message;emit();}
    finally{if(current(index,job,serial))pump();}
  }
  async function setArtwork(index,file){
    const job=slot(index);if(!job?.native||['running','queued','loading'].includes(job.status))throw Error('Mở template và chờ tác vụ hiện tại trước.');
    cancelJob(job);const serial=job.serial;job.abort=new AbortController();job.status='loading';job.message='Đang đặt artwork vào IN…';job.error=null;job.output=job.map=null;emit();
    try{
      const artwork=await prepareArtwork(file,job.native,{signal:job.abort.signal});
      if(!current(index,job,serial))return;
      if(!artwork?.rgba?.some((value,index)=>index%4===3&&value>0))throw Error('Artwork trong suốt hoàn toàn. Chọn ảnh khác trước khi chạy AI.');
      job.artwork=artwork;job.hasArtwork=true;job.status='ready';job.message='Artwork đã đặt vào IN · sẵn sàng Generate.';emit();
    }catch(error){if(!current(index,job,serial))return;job.status='error';job.error=error.message;job.message=error.message;emit();}
  }
  function pump(){
    if(disposed||isExternalBusy())return;
    let free=parallel-jobs.filter(j=>j?.status==='running').length;
    for(let index=0;index<2&&free>0;index++)if(jobs[index]?.status==='queued'){free--;void execute(index,jobs[index]);}
  }
  async function execute(index,job){
    const serial=job.serial;job.status='running';job.message='Đang khởi động MoGe + DA2…';job.progress=null;job.abort=new AbortController();emit();
    try{
      job.pipeline=createPipeline(value=>{
        if(!current(index,job,serial))return;
        if(value.type==='download'){job.progress=value.total?Math.min(100,value.received/value.total*100):null;job.message=`${value.kind==='moge'?'MoGe':'DA2'} · tải / kiểm tra ${job.progress===null?'':Math.round(job.progress)+'%'}`;}
        else if(value.type==='phase'){job.progress=null;job.message='Đang xử lý: '+String(value.phase).replaceAll('-',' ');}
        emit();
      });
      const result=await job.pipeline.run({...job.artwork,productType:job.native.family,options:job.settings,threads:1,outputMode:'full'});
      if(!current(index,job,serial))return;
      // No cached compose is needed for PSD jobs. Release the large WASM heap
      // before allocating full-resolution PSB/channel/composite output buffers.
      job.pipeline.dispose();job.pipeline=null;
      job.message='Đang ghi IN / DEPTH / CUT và ảnh xem trước vào PSD…';job.progress=null;emit();
      const output=await build(job.native,{artwork:job.artwork,depth:{width:result.width,height:result.height,depth:result.depth,alpha:result.alpha},signal:job.abort.signal});
      if(!current(index,job,serial))return;
      if(!(output instanceof Uint8Array)||output.length<26)throw Error('Engine chưa trả về PSD hợp lệ.');
      job.output=output;job.map=result.depth16PNG;job.status='done';job.progress=100;job.message='Hoàn tất · PSD giữ Smart Object IN / DEPTH / CUT. PNG depth riêng giữ 16 bit.';job.error=null;
    }catch(error){if(current(index,job,serial)){job.status='error';job.error=error.message;job.message=error.message;}}
    finally{if(current(index,job,serial)){job.pipeline?.dispose();job.pipeline=null;emit();pump();}}
  }
  return {
    snapshot,load,setArtwork,
    generate(index,settings={}){const job=slot(index);if(!job?.native||!job.hasArtwork||['loading','running','queued'].includes(job.status))throw Error('Cần artwork trong IN trước khi Generate.');if(isExternalBusy())throw Error('Lượt Generate PNG đang chạy; hãy hoàn tất hoặc hủy trước khi chạy PSD.');job.settings=structuredClone(settings);job.output=job.map=null;job.error=null;job.status='queued';job.message='Đang chờ lượt CPU…';job.progress=null;emit();pump();},
    cancel(index){const job=slot(index);if(!job)return;cancelJob(job);job.status=job.native?'ready':'error';job.message='Đã hủy · nguồn PSD và artwork vẫn giữ trong tab.';job.output=job.map=null;emit();pump();},
    remove(index){const job=slot(index);if(job)cancelJob(job);jobs[index]=null;emit();pump();},
    result(index,kind='psd'){const job=slot(index),value=kind==='map'?job?.map:job?.output;if(job?.status!=='done'||!value)throw Error('Chờ Generate hoàn tất trước khi tải.');return value;},
    source(index){const job=slot(index);if(!job?.native?.bytes)throw Error('Mở template hoặc PSD trước.');return job.native.bytes;},
    setParallel(value){check();if(![1,2].includes(value))throw Error('Chọn một hoặc hai tác vụ song song.');parallel=value;emit();pump();},
    get busy(){return jobs.some(j=>['queued','running'].includes(j?.status));},
    dispose(){disposed=true;for(const job of jobs)if(job)cancelJob(job);jobs.fill(null);}
  };
}
