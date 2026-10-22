export const MAX_IMAGE_BYTES=15*1024*1024;
export const MAX_IMAGE_PIXELS=24_000_000;
export const MOTIONS=['zoomIn','zoomOut','panLeft','panRight','still'];
export const ASPECTS={'1:1':{width:720,height:720},'4:5':{width:720,height:900},'16:9':{width:1280,height:720},'9:16':{width:720,height:1280}};
export function validatePhoto(file){
  if(!file||!['image/png','image/jpeg','image/webp'].includes(file.type)||!file.size||file.size>MAX_IMAGE_BYTES)throw Error('Choose one PNG, JPG or WebP photo up to 15 MiB.');
  return file;
}
export function readImageDimensions(buffer,type){
  const view=new DataView(buffer),u8=new Uint8Array(buffer);let width=0,height=0;
  const text=(at,n)=>String.fromCharCode(...u8.slice(at,at+n));
  if(type==='image/png'&&u8.length>=24&&text(1,3)==='PNG'&&text(12,4)==='IHDR'){width=view.getUint32(16);height=view.getUint32(20);}
  else if(type==='image/jpeg'&&u8[0]===255&&u8[1]===216){
    let at=2;while(at+3<u8.length){if(u8[at++]!==255)continue;while(u8[at]===255)at++;const marker=u8[at++];if(marker===218||marker===217)break;if(marker===1||(marker>=208&&marker<=215))continue;if(at+2>u8.length)break;const length=view.getUint16(at);if(length<2||at+length>u8.length)break;
      if(marker>=192&&marker<=207&&![196,200,204].includes(marker)&&length>=7){height=view.getUint16(at+3);width=view.getUint16(at+5);break;}at+=length;
    }
  }else if(type==='image/webp'&&u8.length>=25&&text(0,4)==='RIFF'&&text(8,4)==='WEBP'){
    for(let at=12;at+8<=u8.length;){const chunk=text(at,4),length=view.getUint32(at+4,true),data=at+8;if(data+length>u8.length)break;
      if(chunk==='VP8X'&&length>=10){width=1+u8[data+4]+(u8[data+5]<<8)+(u8[data+6]<<16);height=1+u8[data+7]+(u8[data+8]<<8)+(u8[data+9]<<16);break;}
      if(chunk==='VP8 '&&length>=10&&u8[data+3]===157&&u8[data+4]===1&&u8[data+5]===42){width=view.getUint16(data+6,true)&16383;height=view.getUint16(data+8,true)&16383;break;}
      if(chunk==='VP8L'&&length>=5&&u8[data]===47){const packed=view.getUint32(data+1,true);width=1+(packed&16383);height=1+((packed>>>14)&16383);break;}at=data+length+(length%2);
    }
  }
  if(!width||!height)throw Error('This photo is invalid or its image format is unsupported.');
  if(width*height>MAX_IMAGE_PIXELS||width>12000||height>12000)throw Error('Choose a photo up to 24 megapixels and 12,000 px per side.');
  return{width,height};
}
export function bitmapSize(width,height,maxEdge=4096){const scale=Math.min(1,maxEdge/Math.max(width,height));return{width:Math.max(1,Math.round(width*scale)),height:Math.max(1,Math.round(height*scale))};}
export function motionFromBrief(value){
  const text=String(value||'').normalize('NFD').replace(/[\u0300-\u036f]/g,'').replace(/đ/g,'d').toLowerCase();
  if(/zoom\s*out|pull\s*back|thu\s*nho|ra\s*xa/.test(text))return'zoomOut';
  if(/pan\s*left|move\s*left|sang\s*trai|dich\s*trai/.test(text))return'panLeft';
  if(/pan\s*right|move\s*right|sang\s*phai|dich\s*phai/.test(text))return'panRight';
  if(/still|static|no\s*motion|dung\s*yen|tinh/.test(text))return'still';
  if(/zoom\s*in|push\s*in|closer|phong\s*to|lai\s*gan/.test(text))return'zoomIn';
  return null;
}
export function photoGeometry(sourceWidth,sourceHeight,width,height,{motion='zoomIn',fit='fit'}={},progress=0){
  if(![sourceWidth,sourceHeight,width,height].every(value=>Number.isFinite(value)&&value>0))throw Error('Invalid image or frame dimensions.');
  if(!MOTIONS.includes(motion)||!['fit','cover'].includes(fit))throw Error('Invalid photo motion settings.');
  const t=Math.min(1,Math.max(0,Number.isFinite(progress)?progress:0)),ease=t*t*(3-2*t),base=(fit==='cover'?Math.max:Math.min)(width/sourceWidth,height/sourceHeight);
  let zoom=1,offset=0;
  if(motion==='zoomIn')zoom=fit==='fit'?.88+.12*ease:1+.12*ease;
  if(motion==='zoomOut')zoom=fit==='fit'?1-.12*ease:1.12-.12*ease;
  if(motion==='panLeft'||motion==='panRight')zoom=fit==='fit'?.94:1.12;
  const drawWidth=sourceWidth*base*zoom,drawHeight=sourceHeight*base*zoom;
  if(motion==='panLeft'||motion==='panRight'){const travel=Math.min(width*.06,Math.abs(width-drawWidth)/2);offset=(motion==='panLeft'?1:-1)*travel*(1-2*ease);}
  return{x:(width-drawWidth)/2+offset,y:(height-drawHeight)/2,width:drawWidth,height:drawHeight};
}
export function drawPhotoFrame(ctx,image,settings,t,width,height){
  const geometry=photoGeometry(image.width||image.naturalWidth,image.height||image.naturalHeight,width,height,settings,t);
  ctx.fillStyle=/^#[a-f0-9]{6}$/i.test(settings.background)?settings.background:'#ffffff';ctx.fillRect(0,0,width,height);ctx.imageSmoothingEnabled=true;ctx.imageSmoothingQuality='high';ctx.drawImage(image,geometry.x,geometry.y,geometry.width,geometry.height);
}
