import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { createRequire } from 'node:module';
import { File } from 'node:buffer';
const require=createRequire(import.meta.url);
const engine=require('../vendor/scrabble-layout.js');
const source=fs.readFileSync(new URL('../scrabble-browser.js',import.meta.url),'utf8');
const api=await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
function canvasFactory() {
  const calls=[];const context={calls,beginPath(){},moveTo(){},lineTo(){},quadraticCurveTo(){},closePath(){},clearRect(){},save(){},restore(){},clip(){},fillRect(){},fillText(...args){calls.push(['text',...args]);},measureText(text){return{width:text.length*70};},drawImage(...args){calls.push(['image',...args]);}};
  return{width:0,height:0,getContext(){return context;},toBlob(callback){callback(new Blob([new Uint8Array([137,80,78,71,1,2,3])],{type:'image/png'}));},context};
}
test('unchanged panel parser preserves duplicate names and Vietnamese normalization',()=>{
  const parsed=engine.parseNames('Hương\nMIA\nMIA');assert.deepEqual(parsed.names.map(n=>n.word),['HUONG','MIA','MIA']);assert.equal(new Set(parsed.names.map(n=>n.id)).size,3);assert.ok(parsed.warnings.length);
});
test('all generated options include all names, validate, and stay bounded at 60',()=>{
  const names=engine.parseNames('HUONG\nMITOM\nTOM\nMIA\nOLIVER').names;
  const result=engine.generate(names,{variants:60,maxChecks:96000});assert.ok(result.layouts.length>0&&result.layouts.length<=60);assert.equal(result.completeEnumeration,false);
  for(const layout of result.layouts){assert.equal(engine.validate(layout,names).ok,true);assert.equal(layout.placements.length,5);}
});
test('name errors are not silently dropped',()=>{for(const raw of ['MIA\n2026','MIA\tTOM',Array(25).fill('MIA').join('\n')])assert.ok(engine.parseNames(raw).errors.length);});
test('imported tile names strictly map A to Z, reject accidental files',()=>{
  for(const [filename,letter] of [['A.png','A'],['z.WEBP','Z'],['tile-B.jpg','B'],['tile_C.jpeg','C']])assert.equal(api.tileLetterFromFilename(filename),letter);
  for(const filename of ['mockupA.png','AA.png','A.psd','../A.png','1.png','Z.svg'])assert.throws(()=>api.tileLetterFromFilename(filename));
});
test('required source letters are exact, unique, sorted',()=>{
  const layout={cells:[{letter:'Z'},{letter:'A'},{letter:'Z'},{letter:'C'}]};assert.deepEqual(api.requiredLetters(layout),['A','C','Z']);assert.deepEqual(api.missingLetters(layout,new Map([['A',{}]])),['C','Z']);
});
test('export dimensions use canvas aspect ratio, never stretch square output',()=>{
  assert.deepEqual(api.outputDimensions(800,1200,3000),{width:2000,height:3000});assert.deepEqual(api.outputDimensions(1200,800,1024),{width:1024,height:683});
  for(const args of [[0,800,2000],[800,Infinity,2000],[800,800,8000]])assert.throws(()=>api.outputDimensions(...args));
});
test('template style validates colors, font names and role positions',()=>{
  assert.equal(api.normalizeStyle({font:'Times New Roman'}).font,'Times New Roman');
  for(const values of [{font:'bad;url(x)'},{tile:'red'},{ink:'#fff'},{letterX:0},{scoreSize:.9},{letterY:NaN}])assert.throws(()=>api.normalizeStyle(values));
});
test('A–1 template uses exact panel letter score; Z is 10, J is 8',()=>{
  const browser=api.createScrabbleBrowser({engine,createCanvas:canvasFactory});
  for(const [letter,score] of [['A','1'],['J','8'],['Z','10']]){const canvas=browser.tile(letter);assert.equal(canvas.width,800);assert.equal(canvas.height,800);assert.deepEqual(canvas.context.calls.filter(c=>c[0]==='text').map(c=>c[1]),[letter,score]);}
});
test('score checkbox removes only scores, preserves letters',()=>{
  const browser=api.createScrabbleBrowser({engine,createCanvas:canvasFactory});browser.setStyle({showScore:false});assert.deepEqual(browser.tile('A').context.calls.filter(c=>c[0]==='text').map(c=>c[1]),['A']);
});
test('layout render uses source tiles and exact ellipse fit, transparent canvas',()=>{
  const browser=api.createScrabbleBrowser({engine,createCanvas:canvasFactory}),layout=engine.generate('MIA\nTOM',{variants:3}).layouts[0],canvas=canvasFactory();
  const result=browser.render(canvas,layout,{width:800,height:1200,maxEdge:2048,shape:'ellipse',gapRatio:.04,inset:.04});assert.equal(result.width,1365);assert.equal(result.height,2048);assert.equal(result.cells,layout.cells.length);assert.equal(canvas.context.calls.filter(c=>c[0]==='image').length,layout.cells.length);assert.equal(result.fit.shape,'ellipse');
});
test('custom tile mode fails closed for missing source, no font substitutions',()=>{
  const browser=api.createScrabbleBrowser({engine,createCanvas:canvasFactory});browser.setSource('images');assert.throws(()=>browser.tile('A'),/Thiếu/);assert.throws(()=>browser.render(canvasFactory(),engine.generate('MIA',{variants:1}).layouts[0]),/thiếu/);assert.throws(()=>browser.setSource('psd'));
});
test('ZIP contains 26 separate 800 px PNG entries with valid CRC and directory offsets',async()=>{
  const browser=api.createScrabbleBrowser({engine,createCanvas:canvasFactory}),blob=await browser.alphabetZip(),bytes=Buffer.from(await blob.arrayBuffer());
  assert.equal(blob.type,'application/zip');let at=0,count=0;
  while(bytes.readUInt32LE(at)===0x04034b50){const n=bytes.readUInt16LE(at+26),length=bytes.readUInt32LE(at+18),name=bytes.toString('utf8',at+30,at+30+n);assert.equal(name,api.LETTERS[count]+'.png');assert.ok(bytes.readUInt32LE(at+14)>0);at+=30+n+length;count++;}
  assert.equal(count,26);const eocd=bytes.length-22;assert.equal(bytes.readUInt32LE(eocd),0x06054b50);assert.equal(bytes.readUInt16LE(eocd+10),26);assert.equal(bytes.readUInt32LE(eocd+16),at);assert.equal(bytes.readUInt32LE(at),0x02014b50);assert.equal(bytes.readUInt32LE(at+42),0);
});
test('ZIP rejects traversal, duplicate letters, or empty file bytes',()=>{
  for(const entries of [[{name:'../A.png',bytes:new Uint8Array([1])}],[{name:'A.png',bytes:new Uint8Array()}],[{name:'A.png',bytes:new Uint8Array([1])},{name:'A.png',bytes:new Uint8Array([2])}]])assert.throws(()=>api.makeStoredZip(entries));
});
test('custom incomplete alphabet never exports a fallback full set',async()=>{
  const browser=api.createScrabbleBrowser({engine,createCanvas:canvasFactory});browser.setSource('images');await assert.rejects(browser.alphabetZip(),/chưa đủ A–Z/);
});
test('image decode validates size/pixels and always releases temporary URL',async()=>{
  let revocations=0;class FakeImage{naturalWidth=800;naturalHeight=800;async decode(){}}
  const deps={ImageClass:FakeImage,urlAPI:{createObjectURL(){return'blob:local';},revokeObjectURL(){revocations++;}}};
  await api.decodeLocalImage({name:'A.png',size:100},deps);assert.equal(revocations,1);
  class HugeImage{naturalWidth=8192;naturalHeight=8192;async decode(){}}
  await assert.rejects(api.decodeLocalImage({name:'A.png',size:100},{...deps,ImageClass:HugeImage}),/giới hạn/);assert.equal(revocations,2);
  await assert.rejects(api.decodeLocalImage({name:'A.svg',size:100},deps));await assert.rejects(api.decodeLocalImage({name:'A.png',size:13*1024*1024},deps));assert.equal(revocations,2);
});
test('source import is atomic, rejects duplicates or non-square tiles, and retains only normalized tiles',async()=>{
  const originalImage=globalThis.Image;class TileImage{naturalWidth=1200;naturalHeight=1200;async decode(){}}
  globalThis.Image=TileImage;
  try{
    const browser=api.createScrabbleBrowser({engine,createCanvas:canvasFactory});
    const a=new File([new Uint8Array([1])],'A.png',{type:'image/png'}),b=new File([new Uint8Array([2])],'B.png',{type:'image/png'});
    assert.deepEqual(await browser.importTiles([a,b]),['A','B']);assert.deepEqual(browser.state().letters,['A','B']);assert.equal(browser.tile('A').width,800);assert.equal(browser.tile('B').height,800);
    await assert.rejects(browser.importTiles([a,new File([new Uint8Array([2])],'tile-A.png')]),/Hai file cùng/);assert.deepEqual(browser.state().letters,['A','B']);
    globalThis.Image=class{naturalWidth=1600;naturalHeight=800;async decode(){}};
    await assert.rejects(browser.importTiles([a]),/gần vuông/);assert.deepEqual(browser.state().letters,['A','B']);
  }finally{if(originalImage===undefined)delete globalThis.Image;else globalThis.Image=originalImage;}
});
test('module does not call network, AI, file system, Photoshop or Node',()=>{for(const pattern of [/\bfetch\s*\(/,/\brequire\s*\(/,/evalScript\s*\(/,/DoJavaScript\s*\(/])assert.doesNotMatch(source,pattern);});
