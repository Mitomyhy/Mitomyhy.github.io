import {check,size} from './contracts.mjs';
export function createCPUPipeline({manifestURL,onProgress=()=>{},workerURL=new URL('./pipeline-worker.mjs?v=20261022-flow-r5',import.meta.url),timeoutMs=20*60*1000,downloadIdleMs=90*1000}={}){
  check(typeof manifestURL==='string','Cần URL manifest CPU đã phát hành.');let active=null,cached=null,serial=0,disposed=false;
  function cancel(reason='Bạn đã hủy; mọi tính toán của lượt này đã dừng.'){
    serial++;const job=active,cache=cached;active=cached=null;
    if(cache)cache.worker.terminate();
    if(job){clearTimeout(job.timer);if(job.worker!==cache?.worker)job.worker.terminate();job.reject(Object.assign(new Error(reason),{code:'CANCELLED'}));}
  }
  function dispatch(worker,id,message,transfer=[]){
    return new Promise((resolve,reject)=>{
      const job={worker,reject,timer:null};active=job;
      const finish=keep=>{if(active===job)active=null;clearTimeout(job.timer);if(keep)cached={worker,id};else{if(cached?.worker===worker)cached=null;worker.terminate();}};
      const arm=(delay,message)=>{clearTimeout(job.timer);job.timer=setTimeout(()=>cancel(message),delay);};
      arm(timeoutMs,'Đã dừng do bước CPU vượt thời gian; file cũ không bị thay.');
      worker.onmessage=({data})=>{if(active!==job||data?.id!==id)return;
        if(data.type==='result'){finish(data.cacheReady===true);resolve(data.result);}else if(data.type==='error'){finish(false);reject(Object.assign(new Error(data.message),{code:data.code||'CPU_FLOW_ERROR'}));}
        else {
          if(data.type==='download')arm(downloadIdleMs,'Đã dừng vì tải model không tiến triển trong90giây. Các part đã kiểm tra được giữ để tải tiếp.');
          else if(data.type==='phase')arm(data.phase.endsWith('-download-verify')?downloadIdleMs:timeoutMs,'Đã dừng vì bước hiện tại quá lâu. Không thay file cũ hoặc dùng model khác.');
          onProgress(data);
        }
      };
      worker.onerror=event=>{if(cached?.worker===worker)cached=null;if(active!==job){worker.terminate();return;}finish(false);reject(new Error(event.message||'CPU worker dừng, có thể thiếu RAM. Không tự dùng model khác.'));};
      try{worker.postMessage(message,transfer);}catch(error){finish(false);reject(error);}
    });
  }
  return {
    run({rgba,width,height,productType='acrylic',options={},threads=1,outputMode='full'}={}){
      check(!disposed&&!active,'CPU đang bận hoặc đã đóng.');size(width,height);
      check(rgba instanceof Uint8Array||rgba instanceof Uint8ClampedArray,'Ảnh cần RGBA uint8.');check(rgba.byteLength===width*height*4,'RGBA không khớp kích thước.');
      check(['acrylic','ceramic_circle','ceramic_heart'].includes(productType),'Loại sản phẩm không hợp lệ.');
      check(['maps','full'].includes(outputMode),'Chọn xuất map hoặc workflow 3D đầy đủ.');
      check([1,2,4].includes(threads)&&(threads===1||globalThis.crossOriginIsolated),'Nhiều luồng cần crossOriginIsolated; chọn 1 luồng.');
      if(cached){cached.worker.terminate();cached=null;}
      const id=`cpu4-${++serial}`,input=Uint8Array.from(rgba);
      const worker=new Worker(workerURL,{type:'module',name:'MITOM CPU depth workflow'});
      return dispatch(worker,id,{id,manifestURL:new URL(manifestURL,location.href).href,rgba:input.buffer,width,height,productType,options,threads,outputMode},[input.buffer]);
    },
    compose({options={},productType='acrylic'}={}){
      check(!disposed&&!active&&cached,'Tạo map trước khi dựng 3D; dữ liệu AI phải còn trong tab này.');
      check(['acrylic','ceramic_circle','ceramic_heart'].includes(productType),'Loại sản phẩm không hợp lệ.');
      check(options&&typeof options==='object'&&!Array.isArray(options)&&JSON.stringify(options).length<100_000,'Tùy chọn xử lý không hợp lệ.');
      return dispatch(cached.worker,cached.id,{id:cached.id,command:'compose',options,productType});
    },
    cancel,
    get busy(){return !!active;},
    get canCompose(){return !!cached&&!active&&!disposed;},
    dispose(){cancel('Đã đóng CPU workflow.');disposed=true;}
  };
}
