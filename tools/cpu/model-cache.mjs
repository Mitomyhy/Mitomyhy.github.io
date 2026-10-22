import {SHA256,sha256} from './sha256.mjs';
import {check,trustedURL} from './contracts.mjs';
const CACHE='mitom-cpu-models-v4';
const MiB=1024*1024;
export async function loadPinnedModel(spec,{base,origins=[],progress=()=>{},fetchImpl=fetch,storage=navigator.storage}={}) {
  let directory=null;
  if(storage?.getDirectory)directory=await(await storage.getDirectory()).getDirectoryHandle(CACHE,{create:true});
  const estimate=storage?.estimate?await storage.estimate():null;
  const parts=spec.parts||[{url:spec.url,bytes:spec.bytes,sha256:spec.sha256}], finalHash=new SHA256();
  // Allocate final graph only once. One bounded part and stream chunk coexist,
  // never an array of all >1GB part buffers or Response.clone() tee.
  let graph=parts.length===1?null:new Uint8Array(spec.bytes), offset=0, cacheHits=0;
  for(let index=0;index<parts.length;index++){
    const part=parts[index], key=`sha256-${part.sha256}.onnxpart`;
    let handle=null, bytes=null;
    if(directory){try{handle=await directory.getFileHandle(key);}catch(e){if(e.name!=='NotFoundError')throw e;}}
    if(handle){
      const file=await handle.getFile();
      if(file.size===part.bytes){
        const verifier=new SHA256();let n=0,last=0;
        for await(const chunk of file.stream()){verifier.update(chunk);n+=chunk.length;if(n>part.bytes)throw new Error('Cache model vượt kích thước.');if(performance.now()-last>500){progress({phase:'model-cache-hash',part:index+1,parts:parts.length,received:offset+n,total:spec.bytes});last=performance.now();}}
        if(n===part.bytes&&verifier.hex()===part.sha256){bytes=new Uint8Array(await file.arrayBuffer());cacheHits++;progress({phase:'model-cache-verified',part:index+1,parts:parts.length,received:offset+part.bytes,total:spec.bytes});}
      }
    }
    if(!bytes){
      check(!estimate||!Number.isFinite(estimate.quota)||estimate.quota-(estimate.usage||0)>=part.bytes+64*MiB,'Không đủ dung lượng cache model; không tự tải lại vô hạn.');
      const response=await fetchImpl(trustedURL(part.url,base,origins),{cache:'no-store',credentials:'omit',referrerPolicy:'no-referrer'});
      check(response.ok&&response.body,`Tải model thất bại (${response.status}).`);
      const reader=response.body.getReader(), verifier=new SHA256();let received=0,writable=null,completed=false,last=0;
      if(directory){handle||=await directory.getFileHandle(key,{create:true});writable=await handle.createWritable();}
      else bytes=new Uint8Array(part.bytes);
      try{
        for(;;){const {done,value}=await reader.read();if(done)break;received+=value.length;if(received>part.bytes){await reader.cancel();throw new Error('Part model vượt kích thước đã ghim.');}verifier.update(value);if(writable)await writable.write(value);else bytes.set(value,received-value.length);if(performance.now()-last>500){progress({phase:'model-download',part:index+1,parts:parts.length,received:offset+received,total:spec.bytes});last=performance.now();}}
        check(received===part.bytes&&verifier.hex()===part.sha256,'SHA-256 hoặc số byte part model không khớp. Không chạy model này.');
        if(writable){await writable.close();writable=null;bytes=new Uint8Array(await(await handle.getFile()).arrayBuffer());}
        completed=true;
      }finally{if(!completed){try{await reader.cancel();}catch{}if(writable)try{await writable.abort();}catch{}}}
    }
    check(bytes.length===part.bytes,'Cache part model không đầy đủ.');
    // Even a single >1GB local-preview graph is hashed in yielding chunks, so
    // a slow CPU cannot look like an idle network download to the UI watchdog.
    for(let at=0;at<bytes.length;at+=4*MiB){finalHash.update(bytes.subarray(at,Math.min(bytes.length,at+4*MiB)));if(at%(32*MiB)===0){progress({phase:'model-assembly-hash',part:index+1,parts:parts.length,received:offset+bytes.length,hashed:offset+Math.min(bytes.length,at+4*MiB),total:spec.bytes});await new Promise(resolve=>setTimeout(resolve,0));}}
    if(graph)graph.set(bytes,offset);else graph=bytes;
    offset+=bytes.length;bytes=null;
    progress({phase:'model-part-complete',part:index+1,parts:parts.length,received:offset,total:spec.bytes});
    await new Promise(resolve=>setTimeout(resolve,0));
  }
  check(offset===spec.bytes&&finalHash.hex()===spec.sha256,'SHA-256 model ghép không khớp. Không tạo session.');
  return {bytes:graph,cache:{verifiedParts:parts.length,cacheHits,allBytesAndSHA256Verified:true}};
}

export async function loadPinnedText(spec,{base,fetchImpl=fetch}={}){
  const response=await fetchImpl(trustedURL(spec.url,base),{cache:'force-cache',credentials:'omit'});
  check(response.ok,'Không tải được module xử lý.');const bytes=new Uint8Array(await response.arrayBuffer());
  check(bytes.length===spec.bytes&&sha256(bytes)===spec.sha256,'SHA-256 module xử lý không khớp.');return new TextDecoder('utf-8',{fatal:true}).decode(bytes);
}
