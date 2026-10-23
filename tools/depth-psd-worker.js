import {fetchNativeTemplate,openNativePSD,buildNativePSD} from './depth-psd-template.js?v=20261023-psd-r8b';
import {fetchNativeShape} from './depth-native-shapes.js?v=20261023-psd-r8';

async function ceramicShape(family){
  if(family==='acrylic')return undefined;
  const spec=await fetchNativeShape(family),bitmap=await createImageBitmap(new Blob([spec.bytes],{type:'image/png'}));
  try{
    if(bitmap.width!==spec.width||bitmap.height!==spec.height)throw Error('Shape Ceramic sai canvas gốc.');
    const canvas=new OffscreenCanvas(bitmap.width,bitmap.height),ctx=canvas.getContext('2d',{willReadFrequently:true});ctx.drawImage(bitmap,0,0);
    return {width:bitmap.width,height:bitmap.height,rgba:ctx.getImageData(0,0,bitmap.width,bitmap.height).data};
  }finally{bitmap.close();}
}
self.onmessage=async({data})=>{
  const {id,op}=data||{};
  try{
    if(op==='open'){
      const bytes=data.family?await fetchNativeTemplate(data.family):data.bytes;
      const native=await openNativePSD(bytes);
      const value={family:native.family,title:native.title,width:native.width,height:native.height,artwork:native.artwork,isBundledBlank:native.isBundledBlank,bytes};
      self.postMessage({id,ok:true,value},[bytes.buffer,value.artwork.rgba.buffer]);
    }else if(op==='build'){
      const family=(await openNativePSD(data.bytes)).family;
      if(data.family&&data.family!==family)throw Error('Loại template đã thay đổi; không ghép nhầm shape Ceramic.');
      const bytes=await buildNativePSD(data.bytes,{artwork:data.artwork,depth:data.depth,ceramicShape:await ceramicShape(family)});
      self.postMessage({id,ok:true,value:bytes},[bytes.buffer]);
    }else throw Error('Lệnh PSD không hợp lệ.');
  }catch(error){self.postMessage({id,ok:false,error:String(error?.message||error)});}
};
