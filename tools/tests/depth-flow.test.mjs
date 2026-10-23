import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {depthFlowCases} from '../../qa/depth-r5mock-flow.mjs';
if(!globalThis.ImageData)globalThis.ImageData=class{constructor(data,width,height){this.data=data;this.width=width;this.height=height;}};
class MemoryCanvas{
  constructor(ownerDocument){this.ownerDocument=ownerDocument;this.width=0;this.height=0;this.pixels=null;this.context=null;}
  getContext(type){if(type!=='2d')throw Error('WebGL must not be created by map-only tests');if(this.context)return this.context;const self=this;const allocated=()=>{const n=self.width*self.height*4;if(!self.pixels||self.pixels.length!==n)self.pixels=new Uint8ClampedArray(n);return self.pixels;};
    this.context={fillStyle:'#ffffff',globalCompositeOperation:'source-over',putImageData(image,x,y){const p=allocated();for(let iy=0;iy<image.height;iy++)for(let ix=0;ix<image.width;ix++){const at=((y+iy)*self.width+x+ix)*4;p.set(image.data.slice((iy*image.width+ix)*4,(iy*image.width+ix+1)*4),at);}},getImageData(x,y,width,height){const p=allocated(),out=new Uint8ClampedArray(width*height*4);for(let iy=0;iy<height;iy++)for(let ix=0;ix<width;ix++)out.set(p.slice(((y+iy)*self.width+x+ix)*4,((y+iy)*self.width+x+ix+1)*4),(iy*width+ix)*4);return{data:out,width,height};},clearRect(x,y,w,h){const p=allocated();for(let iy=Math.max(0,Math.floor(y));iy<Math.min(self.height,Math.ceil(y+h));iy++)for(let ix=Math.max(0,Math.floor(x));ix<Math.min(self.width,Math.ceil(x+w));ix++)p.fill(0,(iy*self.width+ix)*4,(iy*self.width+ix+1)*4);},fillRect(x,y,w,h){const p=allocated();for(let iy=y;iy<y+h;iy++)for(let ix=x;ix<x+w;ix++)p.fill(255,(iy*self.width+ix)*4,(iy*self.width+ix+1)*4);},drawImage(image,...args){const p=allocated();const [x=0,y=0,w=image.width,h=image.height]=args;const src=image.getContext('2d').getImageData(0,0,image.width,image.height).data;for(let iy=Math.max(0,Math.floor(y));iy<Math.min(self.height,Math.ceil(y+h));iy++)for(let ix=Math.max(0,Math.floor(x));ix<Math.min(self.width,Math.ceil(x+w));ix++){const sx=Math.min(image.width-1,Math.max(0,Math.floor((ix-x)/w*image.width))),sy=Math.min(image.height-1,Math.max(0,Math.floor((iy-y)/h*image.height)));p.set(src.slice((sy*image.width+sx)*4,(sy*image.width+sx+1)*4),(iy*self.width+ix)*4);}},fillText(text,x,y){if(!text)return;const ix=Math.floor(x),iy=Math.floor(y);if(ix>=0&&iy>=0&&ix<self.width&&iy<self.height)allocated().fill(255,(iy*self.width+ix)*4,(iy*self.width+ix+1)*4);}};return this.context;}
}
const document={createElement(tag){if(tag!=='canvas')throw Error('Only bounded canvas is allowed');return new MemoryCanvas(document);}};
for(const[name,work]of depthFlowCases(document))test(name,work);
const workbench=fs.readFileSync(new URL('../workbench.js',import.meta.url),'utf8');
for(const id of ['depth-undo','depth-reset-edits'])test(id+' UI awaits 3D refresh before enabling export from the edited result',()=>{
  const line=workbench.split(/\r?\n/).find(value=>value.startsWith(`$('${id}').addEventListener('click'`));assert.ok(line,'handler exists');assert.match(line,/run\(async\(\)=>/);assert.match(line,/if\(depthView==='3d'\)await depth\.show3D\(\);visibleResult\(\)/);
  assert.ok(line.indexOf(id==='depth-undo'?'depth?.undo()':'depth?.resetEdits()')<line.indexOf('await depth.show3D()'),'local edit applied before renderer refresh');
});
