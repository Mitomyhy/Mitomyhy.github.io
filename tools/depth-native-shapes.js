const BASE=new URL('./assets/templates/',import.meta.url);
const SHAPES=Object.freeze({
  ceramic_circle:Object.freeze({file:'ceramic-circle-shape-rgba.png',bytes:134166,sha256:'f931560a71278a9fd60acaa1c9a33cc3dafbf3be22b06b47bed671e2aa8aeb8f',width:2799,height:2799}),
  ceramic_heart:Object.freeze({file:'ceramic-heart-shape-rgba.png',bytes:127713,sha256:'7afd85b91063f2086e49d74d5f649455ce756f476189f65e9ba936defbd90e61',width:2799,height:2568})
});
export async function fetchNativeShape(family,{signal,fetchImpl=globalThis.fetch}={}){
  if(family==='acrylic')return null;const spec=SHAPES[family];if(!spec)throw Error('Loại shape Ceramic không hợp lệ.');
  if(signal?.aborted)throw Object.assign(Error('Đã hủy tải shape.'),{name:'AbortError'});
  const response=await fetchImpl(new URL(spec.file,BASE),{signal,credentials:'omit'});
  if(!response.ok||!response.body?.getReader)throw Error('Không tải được shape Ceramic gốc.');
  const reader=response.body.getReader(),bytes=new Uint8Array(spec.bytes);let offset=0;
  try{
    while(true){if(signal?.aborted)throw Object.assign(Error('Đã hủy tải shape.'),{name:'AbortError'});const {done,value}=await reader.read();if(done)break;if(offset+value.length>spec.bytes)throw Error('Shape Ceramic vượt dung lượng bản gốc.');bytes.set(value,offset);offset+=value.length;}
  }catch(error){await reader.cancel().catch(()=>{});throw error;}finally{reader.releaseLock();}
  const hash=[...new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))].map(b=>b.toString(16).padStart(2,'0')).join('');
  if(offset!==spec.bytes||hash!==spec.sha256)throw Error('Shape Ceramic không khớp SHA-256 bản gốc.');
  return {bytes,width:spec.width,height:spec.height};
}
