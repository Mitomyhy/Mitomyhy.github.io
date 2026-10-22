import test from 'node:test';
import assert from 'node:assert/strict';
import {paintDepth,createLocalDepthEdits} from '../depth-local-edit.js';
function result(width=8,height=8,value=.5){const n=width*height,rgba=new Uint8ClampedArray(n*4);for(let i=0;i<n;i++){rgba[i*4]=80;rgba[i*4+1]=100;rgba[i*4+2]=120;rgba[i*4+3]=255;}return{width,height,depth:new Float32Array(n).fill(value),rgba,alpha:new Uint8Array(n).fill(255)};}
function maskDocument(index=27,coverage=255){return{createElement(tag){assert.equal(tag,'canvas');const canvas={width:0,height:0};let drawn=false;canvas.getContext=()=>({fillText(){drawn=true;},getImageData(){const data=new Uint8ClampedArray(canvas.width*canvas.height*4);if(drawn)data[index*4+3]=coverage;return{data};}});return canvas;}};}
const brush={x:3,y:3,radius:2,strength:.25,mode:'light'};
const type={value:'A',font:'Arial',color:'#ff0000',mode:'raised',x:50,y:50,size:20,strength:.1,bold:true};
test('Fix light/dark act locally and leave alpha / input source unchanged',()=>{
  for(const mode of ['light','dark']){const input=result(),before=input.depth.slice(),alpha=input.alpha.slice(),values=input.depth.slice();const changed=paintDepth(values,input.alpha,8,8,{...brush,mode});assert.ok(changed>0);assert.ok(mode==='light'?values[27]>.5:values[27]<.5);assert.equal(values[0],.5);assert.deepEqual(input.depth,before);assert.deepEqual(input.alpha,alpha);}
});
test('Fix ignores transparent and antialiased outside-alpha pixels',()=>{
  const input=result();input.alpha[27]=0;input.alpha[26]=127;paintDepth(input.depth,input.alpha,8,8,brush);assert.equal(input.depth[27],.5);assert.equal(input.depth[26],.5);assert.ok(input.depth[28]>.5);
});
test('brush validation rejects malformed dimensions, alpha and options before mutation',()=>{
  const input=result(),before=input.depth.slice();for(const [width,height,alpha,options]of [[7,8,input.alpha,brush],[8,8,new Uint8Array(1),brush],[8,8,new Float32Array(64),brush],[8,8,input.alpha,{...brush,x:NaN}],[8,8,input.alpha,{...brush,radius:0}],[8,8,input.alpha,{...brush,strength:2}],[8,8,input.alpha,{...brush,mode:'erase'}]])assert.throws(()=>paintDepth(input.depth,alpha,width,height,options));assert.deepEqual(input.depth,before);
});
test('multiple samples in one stroke undo to first pre-stroke values exactly',()=>{
  const input=result(),edits=createLocalDepthEdits(input,maskDocument());edits.beginStroke();edits.paint(brush);edits.paint({...brush,x:4});edits.paint(brush);edits.endStroke();assert.equal(edits.canUndo,true);assert.ok(edits.current().depth[27]>.5);assert.equal(edits.undo(),true);assert.deepEqual(edits.current().depth,input.depth);assert.equal(edits.undo(),false);assert.deepEqual(input.depth,new Float32Array(64).fill(.5));
});
test('stroke sequencing rejects painting without begin and undo during an active stroke',()=>{
  const edits=createLocalDepthEdits(result(),maskDocument());assert.throws(()=>edits.paint(brush));edits.beginStroke();assert.throws(()=>edits.beginStroke());assert.throws(()=>edits.undo());edits.endStroke();assert.equal(edits.undo(),false);
});
test('history is capped at six strokes, not unbounded RAM',()=>{
  const edits=createLocalDepthEdits(result(),maskDocument());for(let i=0;i<7;i++){edits.beginStroke();edits.paint({...brush,strength:.05});edits.endStroke();}for(let i=0;i<6;i++)assert.equal(edits.undo(),true);assert.equal(edits.undo(),false);assert.ok(edits.current().depth[27]>.5);
});
test('raised, recessed and plain text are local overlays, never source rewrites',()=>{
  for(const mode of ['raised','recessed','plain']){const input=result(),before=input.rgba.slice(),edits=createLocalDepthEdits(input,maskDocument());edits.setText({...type,mode});const current=edits.current();assert.equal(current.rgba[27*4],255);assert.equal(current.rgba[27*4+1],0);assert.equal(current.rgba[27*4+3],255);assert.equal(current.depth[0],.5);assert.ok(mode==='raised'?current.depth[27]>.5:mode==='recessed'?current.depth[27]<.5:current.depth[27]===.5);assert.deepEqual(input.rgba,before);assert.deepEqual(input.depth,new Float32Array(64).fill(.5));}
});
test('text respects map alpha and clearing text preserves the underlying Fix',()=>{
  const input=result(),edits=createLocalDepthEdits(input,maskDocument());edits.beginStroke();edits.paint(brush);edits.endStroke();const painted=edits.current().depth[27];edits.setText(type);assert.ok(edits.current().depth[27]>painted);edits.setText(null);assert.equal(edits.current().depth[27],painted);
  const transparent=result();transparent.alpha[27]=0;const excluded=createLocalDepthEdits(transparent,maskDocument());excluded.setText(type);assert.equal(excluded.current().depth[27],.5);assert.equal(excluded.current().rgba[27*4],80);
});
test('rebase preserves Fix delta and re-applies text once on the new composed base',()=>{
  const input=result(),edits=createLocalDepthEdits(input,maskDocument());edits.beginStroke();edits.paint(brush);edits.endStroke();const delta=edits.current().depth[27]-.5;edits.setText(type);const next=result(8,8,.6);edits.rebase(next);assert.ok(Math.abs(edits.current().depth[27]-(.6+delta+.1))<2e-7);assert.equal(edits.current().depth[0],next.depth[0]);assert.equal(edits.canUndo,false);edits.setText(null);assert.ok(Math.abs(edits.current().depth[27]-(.6+delta))<2e-7);assert.deepEqual(next.depth,new Float32Array(64).fill(.6));assert.deepEqual(input.depth,new Float32Array(64).fill(.5));
});
test('reset restores current composed base and removes both Fix and text',()=>{
  const input=result(),edits=createLocalDepthEdits(input,maskDocument());edits.beginStroke();edits.paint(brush);edits.endStroke();edits.setText(type);edits.reset();assert.deepEqual(edits.current().depth,input.depth);assert.deepEqual(edits.current().rgba,input.rgba);assert.equal(edits.canUndo,false);
});
test('constructor rejects malformed or nonfinite payloads, no partial adoption',()=>{
  for(const change of [{width:8.5},{height:0},{depth:new Float32Array(63)},{depth:new Float32Array(64).fill(NaN)},{depth:new Float32Array(64).fill(Infinity)},{depth:new Float32Array(64).fill(-.1)},{depth:new Float32Array(64).fill(1.1)},{rgba:new Uint8Array(255)},{rgba:new Float32Array(256)},{alpha:new Uint8Array(1)},{mapAlpha:new Uint8Array(1)}])assert.throws(()=>createLocalDepthEdits({...result(),...change},maskDocument()));
});
test('failed rebase leaves accepted map, Fix, history and text intact',()=>{
  const edits=createLocalDepthEdits(result(),maskDocument());edits.beginStroke();edits.paint(brush);edits.endStroke();edits.setText(type);const before=edits.current();for(const next of [{...result(),depth:new Float32Array(64).fill(NaN)},{...result(),alpha:new Uint8Array(1)},{...result(),width:9}]){assert.throws(()=>edits.rebase(next));assert.deepEqual(edits.current().depth,before.depth);assert.equal(edits.canUndo,true);}
});
test('text validation rejects injected font/css, excess lines, unsupported modes and nonfinite positions',()=>{
  const edits=createLocalDepthEdits(result(),maskDocument());for(const changes of [{font:'Arial; url(secret)'},{color:'red'},{value:'A'.repeat(501)},{value:'1\n2\n3\n4\n5\n6\n7'},{mode:'html'},{x:NaN},{strength:.4},{size:0}])assert.throws(()=>edits.setText({...type,...changes}));
});
test('cache revision after unedited rebase is not a user edit',()=>{
  const edits=createLocalDepthEdits(result(),maskDocument());assert.equal(edits.changed,false);
  edits.rebase(result(8,8,.6));assert.equal(edits.changed,false);assert.deepEqual(edits.current().depth,new Float32Array(64).fill(.6));
  edits.rebase(result(8,8,.7));assert.equal(edits.changed,false);assert.equal(edits.canUndo,false);
});
test('clearing only text resets changed even though cached render revision advanced',()=>{
  const edits=createLocalDepthEdits(result(),maskDocument());edits.setText(type);assert.equal(edits.changed,true);edits.current();edits.setText(null);assert.equal(edits.changed,false);assert.deepEqual(edits.current().depth,new Float32Array(64).fill(.5));
  edits.setText(type);edits.setText({value:''});assert.equal(edits.changed,false);
});
test('undoing all paint resets changed; text remains independently dirty until cleared',()=>{
  const edits=createLocalDepthEdits(result(),maskDocument());
  for(const x of [3,4]){edits.beginStroke();edits.paint({...brush,x});edits.endStroke();}assert.equal(edits.changed,true);
  assert.equal(edits.undo(),true);assert.equal(edits.changed,true);assert.equal(edits.undo(),true);assert.equal(edits.changed,false);
  edits.beginStroke();edits.paint(brush);edits.endStroke();edits.setText(type);edits.undo();assert.equal(edits.changed,true);edits.setText(null);assert.equal(edits.changed,false);
});
test('reset clears changed after paint, text and rebase; a no-op stroke never marks edited',()=>{
  const edits=createLocalDepthEdits(result(),maskDocument());edits.beginStroke();assert.equal(edits.paint({...brush,strength:0}),0);edits.endStroke();assert.equal(edits.changed,false);
  edits.beginStroke();edits.paint(brush);edits.endStroke();edits.setText(type);edits.rebase(result(8,8,.6));assert.equal(edits.changed,true);edits.reset();assert.equal(edits.changed,false);assert.equal(edits.canUndo,false);assert.deepEqual(edits.current().depth,new Float32Array(64).fill(.6));
});
test('mapAlpha, not full source alpha, controls the valid Fix and Type mask',()=>{
  const input=result();input.mapAlpha=new Uint8Array(64).fill(255);input.mapAlpha[27]=0;
  const edits=createLocalDepthEdits(input,maskDocument());edits.beginStroke();edits.paint(brush);edits.endStroke();edits.setText(type);const actual=edits.current();
  assert.equal(actual.depth[27],.5);assert.equal(actual.rgba[27*4],80);assert.equal(actual.alpha[27],255,'full/source render alpha is retained');assert.ok(actual.depth[28]>.5,'valid map neighbor remains editable');
  assert.equal(input.mapAlpha[27],0);assert.equal(input.alpha[27],255);assert.equal(input.depth[28],.5);
});
test('a valid map pixel can be edited without changing its transparent fullprint alpha',()=>{
  const input=result();input.alpha[27]=0;input.rgba[27*4+3]=0;input.mapAlpha=new Uint8Array(64).fill(255);
  const edits=createLocalDepthEdits(input,maskDocument());edits.beginStroke();edits.paint(brush);edits.endStroke();assert.ok(edits.current().depth[27]>.5);assert.equal(edits.current().alpha[27],0);assert.equal(edits.current().rgba[27*4+3],0);
  edits.setText(type);assert.equal(edits.current().rgba[27*4],255);assert.equal(edits.current().rgba[27*4+3],0);assert.equal(input.rgba[27*4],80);
});
test('rebase updates the editable map mask independently from the displayed fullprint alpha',()=>{
  const input=result();input.mapAlpha=new Uint8Array(64).fill(255);input.mapAlpha[27]=0;const edits=createLocalDepthEdits(input,maskDocument());
  edits.beginStroke();edits.paint(brush);edits.endStroke();assert.equal(edits.current().depth[27],.5);
  const next=result(8,8,.6);next.alpha[27]=0;next.mapAlpha=new Uint8Array(64).fill(255);edits.rebase(next);edits.beginStroke();edits.paint(brush);edits.endStroke();assert.ok(edits.current().depth[27]>.6);assert.equal(edits.current().alpha[27],0);assert.equal(next.depth[27],new Float32Array([.6])[0]);
});
