// Browser-only photo animation. No model, server, API key or network request.
const MP4_TYPES = ['video/mp4;codecs=avc1.42E01E','video/mp4;codecs=avc1','video/mp4'];
const WEBM_TYPES = ['video/webm;codecs=vp8','video/webm;codecs=vp9','video/webm'];
function supportedTypes() {
  if (typeof MediaRecorder === 'undefined' || typeof HTMLCanvasElement === 'undefined' || !HTMLCanvasElement.prototype.captureStream) return [];
  return [...MP4_TYPES,...WEBM_TYPES].filter(type=>MediaRecorder.isTypeSupported(type));
}
export function getExportSupport() {
  const types=supportedTypes();
  return {supported:types.length>0,mp4:types.some(t=>t.startsWith('video/mp4')),webm:types.some(t=>t.startsWith('video/webm'))};
}
function abortError(){const error=Error('Export cancelled.');error.name='AbortError';return error;}
export async function verifyVideoContainer(blob,extension){
  const header=new Uint8Array(await blob.slice(0,16).arrayBuffer());
  const mp4=header.length>=8&&String.fromCharCode(...header.slice(4,8))==='ftyp';
  const webm=header.length>=4&&header[0]===0x1a&&header[1]===0x45&&header[2]===0xdf&&header[3]===0xa3;
  if(extension==='mp4'?!mp4:!webm)throw Error('The browser returned a different video format. Export with a current Chrome or Edge browser.');
}
export async function exportPhotoVideo({image,drawFrame,duration=8,fps=24,width=1280,height=720,onProgress=()=>{},signal}){
  if(!image||typeof drawFrame!=='function')throw Error('Choose a product photo first.');
  if(!Number.isFinite(duration)||duration<3||duration>15||!Number.isInteger(fps)||fps<12||fps>30||!Number.isInteger(width)||!Number.isInteger(height)||width<500||height<500||width>1920||height>1920)throw Error('Choose a 3–15 second video between 500 and 1920 pixels.');
  if(signal?.aborted)throw abortError();
  const types=supportedTypes();if(!types.length)throw Error('Video export is not supported in this browser. Use a current Chrome or Edge browser.');
  const canvas=document.createElement('canvas');canvas.width=width;canvas.height=height;
  const ctx=canvas.getContext('2d',{alpha:false});if(!ctx)throw Error('Could not create the video canvas.');
  drawFrame(ctx,0,width,height);
  const stream=canvas.captureStream(fps);let recorder,chosen;
  for(const type of types){try{recorder=new MediaRecorder(stream,{mimeType:type,videoBitsPerSecond:4_000_000});chosen=type;break;}catch(_) {}}
  if(!recorder){stream.getTracks().forEach(track=>track.stop());throw Error('No video encoder is available. Close other apps or try Chrome / Edge.');}
  const chunks=[];let timer,watchdog,settled=false;
  const blob=await new Promise((resolve,reject)=>{
    function cleanup(){clearTimeout(timer);clearTimeout(watchdog);signal?.removeEventListener('abort',cancel);stream.getTracks().forEach(track=>track.stop());}
    function fail(error){if(settled)return;settled=true;cleanup();try{if(recorder.state!=='inactive')recorder.stop();}catch(_){}reject(error);}
    function cancel(){fail(abortError());}
    recorder.ondataavailable=event=>{if(event.data?.size)chunks.push(event.data);};
    recorder.onerror=event=>fail(Error(event.error?.message||'The video encoder failed. Try Chrome / Edge or close other apps.'));
    recorder.onstop=()=>{if(settled)return;settled=true;cleanup();if(!chunks.length){reject(Error('No video frames were recorded. Keep this tab open during export.'));return;}resolve(new Blob(chunks,{type:recorder.mimeType||chosen}));};
    signal?.addEventListener('abort',cancel,{once:true});
    try{recorder.start(250);}catch(error){fail(error);return;}
    const start=performance.now();
    function tick(){
      if(settled)return;
      try{
        const elapsed=performance.now()-start,t=Math.min(1,elapsed/(duration*1000));
        drawFrame(ctx,t,width,height);onProgress(t);
        if(t>=1){recorder.stop();return;}
        timer=setTimeout(tick,1000/fps);
      }catch(error){fail(error);}
    }
    watchdog=setTimeout(()=>fail(Error('Video export timed out. Keep the tab open and try a shorter clip.')),(duration+30)*1000);
    tick();
  });
  if(signal?.aborted)throw abortError();
  const mimeType=blob.type||chosen,extension=mimeType.startsWith('video/mp4')?'mp4':'webm';
  await verifyVideoContainer(blob,extension);
  if(blob.size>100*1024*1024)throw Error('The video exceeds 100 MB. Choose a shorter duration.');
  onProgress(1);return{blob,extension,mimeType};
}
