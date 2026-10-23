import {fetchNativeTemplate,openNativePSD,buildNativePSD,resizeNativeRaster,NATIVE_TEMPLATE_CATALOG} from './depth-psd-template.js?v=20261023-psd-r9';
import {decodePSDComposite,inspectPSD} from './psd-roundtrip.js?v=20261023-psd-r8';
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
async function savedPreview(bytes,native){
  const source=await decodePSDComposite(bytes),scale=Math.min(1,1600/Math.max(source.width,source.height));
  const image=resizeNativeRaster(source,Math.max(1,Math.round(source.width*scale)),Math.max(1,Math.round(source.height*scale)));
  const info=inspectPSD(bytes);
  const item=NATIVE_TEMPLATE_CATALOG.find(item=>item.id===native.family);
  const roles=native.roles||Object.fromEntries(item.roles.map(([role,name,index])=>{
    const quad=info.layers[index].placedTransform;
    return [role,{name,index,width:item.inputWidth,height:item.inputHeight,transform:[quad[0],quad[1],quad[2],quad[5]]}];
  }));
  return {...image,roles,sourceWidth:source.width,sourceHeight:source.height};
}
self.onmessage=async({data})=>{
  const {id,op}=data||{};
  try{
    if(op==='preview'){
      // A pinned, unchanged template needs only its saved parent composite here.
      // The IN / AI input is kept entirely separate from this layout preview.
      const bytes=await fetchNativeTemplate(data.family),value=await savedPreview(bytes,{family:data.family});
      self.postMessage({id,ok:true,value},[value.rgba.buffer]);
    }else if(op==='open'){
      const bytes=data.family?await fetchNativeTemplate(data.family):data.bytes;
      const native=await openNativePSD(bytes);
      const preview=await savedPreview(bytes,native);
      const value={family:native.family,title:native.title,width:native.width,height:native.height,artwork:native.artwork,isBundledBlank:native.isBundledBlank,preview,bytes};
      self.postMessage({id,ok:true,value},[bytes.buffer,value.artwork.rgba.buffer,preview.rgba.buffer]);
    }else if(op==='build'){
      const family=(await openNativePSD(data.bytes)).family;
      if(data.family&&data.family!==family)throw Error('Loại template đã thay đổi; không ghép nhầm shape Ceramic.');
      const bytes=await buildNativePSD(data.bytes,{artwork:data.artwork,depth:data.depth,ceramicShape:await ceramicShape(family)});
      const preview=await savedPreview(bytes,{family});
      self.postMessage({id,ok:true,value:{bytes,preview}},[bytes.buffer,preview.rgba.buffer]);
    }else throw Error('Lệnh PSD không hợp lệ.');
  }catch(error){self.postMessage({id,ok:false,error:String(error?.message||error),code:error?.code});}
};
