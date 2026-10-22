import {check,size} from './contracts.mjs';
export function createCPUPipeline({manifestURL,onProgress=()=>{},workerURL=new URL('./pipeline-worker.mjs',import.meta.url),timeoutMs=20*60*1000,downloadIdleMs=90*1000}={}){
  check(typeof manifestURL==='string','Cần URL manifest CPU đã phát hành.');let active=null,serial=0,disposed=false;
  function cancel(reason='Bạn đã hủy; mọi tính toán của lượt này đã dừng.'){
    serial++;const job=active;active=null;if(job){clearTimeout(job.timer);job.worker.terminate();job.reject(Object.assign(new Error(reason),{code:'CANCELLED'}));}
  }
  return {
    run({rgba,width,height,productType='acrylic',options={},threads=1}={}){
      check(!disposed&&!active,'CPU đang bận hoặc đã đóng.');size(width,height);
      check(rgba instanceof Uint8Array||rgba instanceof Uint8ClampedArray,'Ảnh cần RGBA uint8.');check(rgba.byteLength===width*height*4,'RGBA không khớp kích thước.');
      check(['acrylic','ceramic_circle','ceramic_heart'].includes(productType),'Loại sản phẩm không hợp lệ.');
      check([1,2,4].includes(threads)&&(threads===1||globalThis.crossOriginIsolated),'Nhiều luồng cần crossOriginIsolated; chọn 1 luồng.');
      const id=`cpu4-${++serial}`,input=Uint8Array.from(rgba);
      return new Promise((resolve,reject)=>{
        const worker=new Worker(workerURL,{type:'module',name:'MITOM CPU full-flow'}),job={worker,reject,timer:null};active=job;
        const finish=()=>{if(active===job)active=null;clearTimeout(job.timer);worker.terminate();};
        const arm=(delay,message)=>{clearTimeout(job.timer);job.timer=setTimeout(()=>cancel(message),delay);};
        arm(timeoutMs,'Đã dừng do bước CPU vượt thời gian; file cũ không bị thay.');
        worker.onmessage=({data})=>{if(active!==job||data?.id!==id)return;
          if(data.type==='result'){finish();resolve(data.result);}else if(data.type==='error'){finish();reject(Object.assign(new Error(data.message),{code:data.code||'CPU_FLOW_ERROR'}));}
          else {
            if(data.type==='download')arm(downloadIdleMs,'Đã dừng vì tải model không tiến triển trong90giây. Các part đã kiểm tra được giữ để tải tiếp.');
            else if(data.type==='phase')arm(data.phase.endsWith('-download-verify')?downloadIdleMs:timeoutMs,'Đã dừng vì bước hiện tại quá lâu. Không thay file cũ hoặc dùng model khác.');
            onProgress(data);
          }
        };
        worker.onerror=event=>{if(active!==job)return;finish();reject(new Error(event.message||'CPU worker dừng, có thể thiếu RAM. Không tự dùng model khác.'));};
        worker.postMessage({id,manifestURL:new URL(manifestURL,location.href).href,rgba:input.buffer,width,height,productType,options,threads},[input.buffer]);
      });
    },
    cancel,
    get busy(){return !!active;},
    dispose(){cancel('Đã đóng CPU workflow.');disposed=true;}
  };
}
