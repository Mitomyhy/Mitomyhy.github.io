// Bounded local PSD work stays off the UI thread. Never sends PSDs to a server.
export function callPSDWorker(op,data,{signal,timeout=5*60*1000}={}) {
  const cancelled=()=>Object.assign(Error('Đã hủy tác vụ PSD.'),{code:'CANCELLED',name:'AbortError'});
  return new Promise((resolve,reject)=>{
    if(signal?.aborted){reject(cancelled());return;}
    const worker=new Worker(new URL('./depth-psd-worker.js?v=20261023-psd-r9',import.meta.url),{type:'module',name:'MITOM native PSD'});
    let settled=false;
    const finish=(error,value)=>{if(settled)return;settled=true;clearTimeout(timer);signal?.removeEventListener('abort',cancel);worker.terminate();error?reject(error):resolve(value);};
    const cancel=()=>finish(cancelled());
    const timer=setTimeout(()=>finish(Error('Đọc / ghi PSD quá lâu. Nguồn của bạn không bị sửa.')),timeout);
    signal?.addEventListener('abort',cancel,{once:true});
    worker.onmessage=({data})=>data?.ok?finish(null,data.value):finish(Object.assign(Error(data?.error||'Không đọc được PSD.'),{code:data?.code}));
    worker.onerror=event=>finish(Error(event.message||'Engine PSD dừng; có thể thiếu RAM.'));
    try{worker.postMessage({id:1,op,...data});}catch(error){finish(error);}
  });
}
