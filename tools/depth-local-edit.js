/* Browser equivalents of local Depth Fix and explicitly added Type layers.
 * Original artwork and AI maps are never rewritten. No Photoshop automation. */
const clamp = n => Math.max(0, Math.min(1, n));
export function paintDepth(depth, alpha, width, height, {x,y,radius,strength=.1,mode='light'}, before) {
  if (!Number.isInteger(width)||!Number.isInteger(height)||width<2||height<2||width*height>4194304||!(depth instanceof Float32Array) || depth.length!==width*height || (alpha&&(!(alpha instanceof Uint8Array||alpha instanceof Uint8ClampedArray)||alpha.length!==width*height)) || ![x,y,radius,strength].every(Number.isFinite) || radius<1 || radius>Math.max(width,height) || strength<0 || strength>1 || !['light','dark'].includes(mode)) throw Error('Brush depth không hợp lệ.');
  const left=Math.max(0,Math.floor(x-radius)),right=Math.min(width-1,Math.ceil(x+radius)),top=Math.max(0,Math.floor(y-radius)),bottom=Math.min(height-1,Math.ceil(y+radius));
  let changed=0;
  for(let py=top;py<=bottom;py++)for(let px=left;px<=right;px++){
    const at=py*width+px;if(alpha&&alpha[at]<128)continue;
    const distance=Math.hypot(px-x,py-y)/radius;if(distance>=1)continue;
    const old=depth[at],weight=(1-distance*distance)**2,midtones=Math.exp(-(((old-.5)/.4)**2));
    const next=clamp(old+(mode==='light'?1-old:-old)*strength*weight*midtones);
    if(next!==old){if(before&&!before.has(at))before.set(at,old);depth[at]=next;changed++;}
  }
  return changed;
}
export function createLocalDepthEdits(result, document) {
  const {width,height}=result,n=width*height;
  function validate(value){if(!Number.isInteger(width)||!Number.isInteger(height)||width<2||height<2||n>4194304||value.width!==width||value.height!==height||!(value.depth instanceof Float32Array)||value.depth.length!==n||!value.depth.every(v=>Number.isFinite(v)&&v>=0&&v<=1)||!(value.rgba instanceof Uint8Array||value.rgba instanceof Uint8ClampedArray)||value.rgba.length!==n*4)throw Error('Map để chỉnh không hợp lệ.');if(value.mapDepth!==undefined&&(!(value.mapDepth instanceof Float32Array)||value.mapDepth.length!==n||!value.mapDepth.every(v=>Number.isFinite(v)&&v>=0&&v<=1)))throw Error('Map gốc để chỉnh không hợp lệ.');for(const key of ['alpha','mapAlpha'])if(value[key]!==undefined&&(!(value[key] instanceof Uint8Array||value[key] instanceof Uint8ClampedArray)||value[key].length!==n))throw Error('Alpha của map không hợp lệ.');}
  validate(result);
  let alpha=result.mapAlpha||result.alpha||Uint8Array.from({length:n},(_,i)=>result.rgba[i*4+3]);
  const base=value=>value.mapDepth||value.depth;
  let painted=base(result).slice(),text=null,revision=0,cached=null,stroke=null,history=[],paintedChanged=false;
  function invalidate(){revision++;cached=null;}
  function current(){
    if(cached)return cached;
    const mapDepth=painted.slice(),rgba=new Uint8ClampedArray(result.rgba);
    if(text?.value){
      const mask=document.createElement('canvas');mask.width=width;mask.height=height;
      const ctx=mask.getContext('2d',{willReadFrequently:true});
      ctx.fillStyle='#fff';ctx.textAlign='center';ctx.textBaseline='middle';
      const px=Math.max(8,Math.round(Math.min(width,height)*text.size/100));ctx.font=`${text.bold?'700':'400'} ${px}px ${text.font}`;
      const lines=text.value.split(/\r?\n/),cx=width*text.x/100,cy=height*text.y/100;
      lines.forEach((line,i)=>ctx.fillText(line,cx,cy+(i-(lines.length-1)/2)*px*1.2,width*.95));
      const coverage=ctx.getImageData(0,0,width,height).data;
      const rgb=[1,3,5].map(at=>parseInt(text.color.slice(at,at+2),16));
      for(let i=0;i<n;i++){
        const a=coverage[i*4+3]/255;if(!a||alpha[i]<128)continue;
        if(text.mode!=='plain')mapDepth[i]=clamp(mapDepth[i]+(text.mode==='raised'?1:-1)*text.strength*a);
        for(let c=0;c<3;c++)rgba[i*4+c]=rgba[i*4+c]*(1-a)+rgb[c]*a;
      }
      mask.width=mask.height=1;
    }
    const depth=result.depth.slice(),original=base(result);
    for(let i=0;i<n;i++)depth[i]=clamp(result.depth[i]+mapDepth[i]-original[i]);
    cached={...result,depth,mapDepth,rgba,alpha:result.alpha||alpha,revision};return cached;
  }
  return {
    current,
    setText(value){
      if(!value?.value){text=null;invalidate();return;}
      if(typeof value.value!=='string'||value.value.length>500||value.value.split(/\r?\n/).length>6||!['Arial','Georgia','Verdana','serif','cursive'].includes(value.font)||!/^#[a-f0-9]{6}$/i.test(value.color)||!['raised','recessed','plain'].includes(value.mode))throw Error('Thông số chữ không hợp lệ.');
      for(const [key,lo,hi]of[['x',0,100],['y',0,100],['size',1,40],['strength',0,.3]])if(!Number.isFinite(value[key])||value[key]<lo||value[key]>hi)throw Error('Vị trí / độ nổi chữ nằm ngoài giới hạn.');
      text={...value};invalidate();
    },
    beginStroke(){if(stroke)throw Error('Nét Fix trước chưa kết thúc.');stroke=new Map();},
    paint(options){if(!stroke)throw Error('Chưa bắt đầu nét Fix.');const changed=paintDepth(painted,alpha,width,height,options,stroke);if(changed){paintedChanged=true;invalidate();}return changed;},
    endStroke(){if(stroke?.size){const indices=new Uint32Array(stroke.size),values=new Float32Array(stroke.size);let i=0;for(const [at,value]of stroke){indices[i]=at;values[i++]=value;}history.push({indices,values});while(history.length>6||history.reduce((sum,row)=>sum+row.indices.byteLength+row.values.byteLength,0)>64*1024*1024)history.shift();}stroke=null;},
    undo(){if(stroke)throw Error('Kết thúc nét Fix trước khi Undo.');const prior=history.pop();if(!prior)return false;for(let i=0;i<prior.indices.length;i++)painted[prior.indices[i]]=prior.values[i];const original=base(result);paintedChanged=painted.some((v,i)=>v!==original[i]);invalidate();return true;},
    rebase(next){validate(next);const prior=base(result),fresh=base(next);for(let i=0;i<n;i++)painted[i]=clamp(fresh[i]+painted[i]-prior[i]);result=next;alpha=next.mapAlpha||next.alpha||alpha;history=[];invalidate();},
    reset(){stroke=null;history=[];painted=base(result).slice();text=null;paintedChanged=false;invalidate();},
    get changed(){return paintedChanged||!!text?.value;},get canUndo(){return history.length>0;}
  };
}
