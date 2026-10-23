import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {parseCivilDate,parseDateList,zonedDateTime,projectEquatorial,projectStereographic,moonMetadata,moonPhaseName,shadeMoonPixels,renderMoons,renderSky,applySkyAppearance,searchPlace,getCapabilities} from '../sky-moon.js';
import {createHash} from 'node:crypto';
import * as inputAPI from '../sky-moon-inputs.js';
import {normalizeMoonProviderMetadata} from '../moon-provider.js';

test('pasted panel MDY and explicit DMY are distinct; ISO and named dates stay unambiguous',()=>{
  assert.equal(parseCivilDate('05/11/1998').iso,'1998-05-11');
  assert.equal(parseCivilDate('05/11/1998','DMY').iso,'1998-11-05');
  for(const value of ['1998-05-11','May 11, 1998','11 May 1998','ngày 11 tháng 5 năm 1998'])assert.equal(parseCivilDate(value).iso,'1998-05-11');
  assert.throws(()=>parseCivilDate('15/06/2000','MDY'),'strict MDY never flips a day larger than 12 into the month');
  assert.equal(parseCivilDate('15/06/2000','DMY').iso,'2000-06-15');
  assert.deepEqual(parseDateList('05/11/1998\n06/15/2000\n08/10/2024').map(x=>x.iso),['1998-05-11','2000-06-15','2024-08-10']);
});
test('real dates checked and ambiguous numeric order is never machine-local',()=>{
  for(const value of ['02/30/2024','02/29/2023','2024-13-01','','1899-12-31'])assert.throws(()=>parseCivilDate(value));
  assert.equal(parseCivilDate('02/29/2024').iso,'2024-02-29');
  assert.throws(()=>parseDateList(Array(61).fill('2024-01-01')));
});
test('IANA timezone uses civil-date DST and fractional offsets',()=>{
  assert.equal(zonedDateTime('2024-08-10','12:00','Europe/London').instant.toISOString(),'2024-08-10T11:00:00.000Z');
  assert.equal(zonedDateTime('2024-01-10','12:00','Europe/London').instant.toISOString(),'2024-01-10T12:00:00.000Z');
  assert.equal(zonedDateTime('2024-08-10','12:00','Asia/Kathmandu').utcOffset,5.75);
  assert.equal(zonedDateTime('2024-07-10','12:00','America/New_York').utcOffset,-4);
  assert.equal(zonedDateTime('2024-01-10','12:00','America/New_York').utcOffset,-5);
  assert.equal(zonedDateTime('2024-08-10','12:00','Asia/Ho_Chi_Minh').utcOffset,7);
});
test('DST gaps/repeated hours reject rather than silently shifting the requested time',()=>{
  assert.throws(()=>zonedDateTime('2024-03-10','02:30','America/New_York'),/không tồn tại/);
  assert.throws(()=>zonedDateTime('2024-11-03','01:30','America/New_York'),/lặp lại/);
  assert.throws(()=>zonedDateTime('2024-08-10','12:00','unknown/zone'),/Múi giờ/);
});
test('stereographic horizon/zenith and east-left sky conventions',()=>{
  const zenith=projectStereographic(100,45,45,100);assert.ok(Math.abs(zenith.x)<1e-12&&Math.abs(zenith.y)<1e-12&&zenith.z>.999);
  const east=projectStereographic(90,0,0,0);assert.ok(east.x<-.999&&Math.abs(east.z)<1e-12);
  const halfway=projectStereographic(45,0,0,0),orthographic=projectEquatorial(45,0,0,0);assert.ok(Math.abs(halfway.x)<Math.abs(orthographic.x));
  assert.equal(getCapabilities().skyProjection,'stereographic-upper-hemisphere');
});
const surface={width:16,height:8,pixels:Uint8ClampedArray.from({length:16*8*4},(_,i)=>i%4===3?255:200)};
test('known screenshot dates have correct phase and angle units',()=>{
  const meta=moonMetadata('2024-08-10');assert.equal(meta.phaseName,'Waxing Crescent');assert.ok(meta.illumination>29&&meta.illumination<31);assert.ok(meta.brightLimbAngle<-60&&meta.brightLimbAngle>-80);
  assert.equal(moonMetadata('1998-05-11').phaseName,'Full Moon');
  assert.equal(moonMetadata('2000-06-15').phaseName,'Waxing Gibbous');
  const right=shadeMoonPixels(80,{...meta,brightLimbAngle:-90},surface,{appearance:'warm',orientation:'phase'});
  const at=(40*80+65)*4;assert.ok(right[at]>right[at+2]);
  assert.equal(right[3],0);
  const grey=shadeMoonPixels(80,meta,surface,{appearance:'grayscale'});assert.ok(grey.every((v,i)=>i%4===3||i%4===0||v===grey[i-i%4]));
});
test('neighbouring gibbous dates are not mislabeled Full Moon by nearest-eighth rounding',()=>{
  assert.equal(moonPhaseName(.456),'Waxing Gibbous');assert.equal(moonPhaseName(.544),'Waning Gibbous');
  for(const[phase,name]of[[0,'New Moon'],[.25,'First Quarter'],[.5,'Full Moon'],[.75,'Last Quarter'],[.999,'New Moon']])assert.equal(moonPhaseName(phase),name);
});

class Canvas {
  constructor(){this.width=1;this.height=1;this.text=[];this.pixels=null;this.ctx={clearRect:()=>{this.pixels=null;},fillRect(){},save(){},restore(){},beginPath(){},arc(){},clip(){},stroke(){},fill(){},moveTo(){},lineTo(){},drawImage:source=>{this.pixels=source.pixels?new Uint8ClampedArray(source.pixels):null;},fillText:(...args)=>this.text.push(args),getImageData:(_x,_y,w,h)=>({data:this.pixels??Uint8ClampedArray.from({length:w*h*4},(_,i)=>i%4===3?255:180)}),createImageData:(w,h)=>({data:new Uint8ClampedArray(w*h*4)}),putImageData:image=>{this.pixels=new Uint8ClampedArray(image.data);}};}
  getContext(){return this.ctx;}
}
globalThis.document={createElement:tag=>{assert.equal(tag,'canvas');return new Canvas();}};
globalThis.Image=class {naturalWidth=16;naturalHeight=8;set src(_value){queueMicrotask(()=>this.onload());}};
globalThis.fetch=async url=>new Response(await readFile(url));
test('render provides combined grid and independent transparent PNG canvases',async()=>{
  const canvas=new Canvas(),result=await renderMoons(canvas,{dates:['05/11/1998','06/15/2000','08/10/2024'],columns:5,size:400,transparent:true,appearance:'warm',showLabels:true});
  assert.equal(result.items.length,3);assert.equal(result.width,1200);assert.equal(result.height,452);assert.equal(result.items[0].display,'05/11/1998');
  assert.equal(result.items[0].canvas.width,400);assert.notEqual(result.items[0].canvas,result.items[1].canvas);assert.equal(canvas.text[0][0],'05/11/1998');
});
test('limits checked before canvas allocation',async()=>{
  await assert.rejects(renderMoons(new Canvas(),{dates:Array(60).fill('2024-08-10'),columns:12,size:1200}),/quá lớn/);
  await assert.rejects(renderMoons(new Canvas(),{dates:['2024-08-10'],size:100.5}),/số nguyên/);
  await assert.rejects(renderMoons(new Canvas(),{dates:['2024-08-10','2024-08-11'],instants:[new Date()]}),/không khớp/);
});
test('small moon labels do not overlap, and cancellation prevents incomplete export',async()=>{
  const canvas=new Canvas();await renderMoons(canvas,{dates:['2024-08-10'],size:100,showLabels:true});assert.ok(canvas.text[1][2]-canvas.text[0][2]>=16);
  const stopped=new AbortController();stopped.abort();await assert.rejects(renderMoons(new Canvas(),{dates:['2024-08-10'],signal:stopped.signal}),error=>error.code==='CANCELLED');
  const controller=new AbortController(),operation=renderMoons(new Canvas(),{dates:Array(20).fill('2024-08-10'),size:100,columns:5,signal:controller.signal});setTimeout(()=>controller.abort(),0);await assert.rejects(operation,error=>error.code==='CANCELLED');
});
test('same city ID gains IANA timezone without country or longitude guessing',async()=>{
  const london=await searchPlace('London, UK');assert.ok((london.candidates??[london.result]).every(place=>place.timeZone==='Europe/London'));
  const leeds=await searchPlace('Leeds, UK');assert.ok((leeds.candidates??[leeds.result]).every(place=>place.timeZone==='Europe/London'));
  const coordinates=await searchPlace('53.79648, -1.54785');assert.equal(coordinates.result.timeZone,undefined);
});
test('sky renderer uses real catalog and exported panel effect is non-compounding',async()=>{
  const raw=new Canvas(),meta=await renderSky(raw,{date:'08/10/2024',time:'20:00',utcOffset:1,lat:53.79648,lon:-1.54785,size:256,labels:false});
  assert.equal(meta.projection,'stereographic-upper-hemisphere');assert.ok(meta.starsRendered>500);assert.equal(meta.utc,'2024-08-10T19:00:00.000Z');
  const before=raw.pixels,target=new Canvas();applySkyAppearance(target,raw,{effect:true,transparent:true});const once=new Uint8ClampedArray(target.pixels);applySkyAppearance(target,raw,{effect:true,transparent:true});assert.deepEqual(target.pixels,once);assert.equal(raw.pixels,before);
});
test('UI supports pasted dates and per-moon PNG with strict Moon MDY, automatic Sky timezone and no manual coordinate controls',async()=>{
  const html=await readFile(new URL('../../index.html',import.meta.url),'utf8'),controller=await readFile(new URL('../workbench.js',import.meta.url),'utf8');
  assert.match(html,/id="sky-date" type="text"/);assert.doesNotMatch(html,/id="sky-date" type="date"/);
  for(const id of ['moon-size','moon-results','sky-reapply'])assert.match(html,new RegExp(`id="${id}"`));
  assert.doesNotMatch(html,/id="moon-appearance"/,'original source colors are not tinted');
  assert.match(controller,/import \{ renderSourceMoons \} from '\.\/moon-source-renderer\.js/);
  assert.match(controller,/await renderSourceMoons\(\$\('moon-canvas'\)/);
  assert.doesNotMatch(controller,/\brenderMoons\b|\$\('moon-appearance'\)/,'production Moon does not reconstruct a NASA sphere');
  assert.match(controller,/Tải PNG riêng/);assert.match(controller,/api\.zonedDateTime/);
  assert.match(controller,/place\.timeZone/);assert.match(controller,/moonAbort\.signal/);assert.match(html,/id="moon-cancel"/);
  const sky=html.match(/<section data-controls="sky"[^>]*>([\s\S]*?)<\/section>/)?.[1],moon=html.match(/<section data-controls="moon"[^>]*>([\s\S]*?)<\/section>/)?.[1];assert.ok(sky);assert.ok(moon);
  for(const id of ['sky-lat','sky-lon','sky-zone','sky-offset','sky-manual','sky-manual-offset']){assert.doesNotMatch(sky,new RegExp(`id="${id}"`));assert.doesNotMatch(controller,new RegExp(`\\$\\('${id}'\\)`));}
  assert.doesNotMatch(moon,/id="moon-date-order"/);assert.doesNotMatch(moon,/<option[^>]*value="DMY"/);assert.doesNotMatch(moon,/DD\/MM\/YYYY/);assert.match(moon,/MM\/DD\/YYYY/);
  assert.doesNotMatch(moon,/id="moon-orientation"/,'provider tilt is automatic, not a user interpretation selector');
  for(const id of ['moon-lat','moon-lon','moon-zone','moon-offset','moon-manual-offset','moon-time'])assert.doesNotMatch(moon,new RegExp(`id="${id}"`));
});
test('Moon pasted date helper accepts only numeric month/day/year and never auto-swaps',()=>{
  assert.equal(typeof inputAPI.parseMoonDates,'function','strict Moon-specific paste parser exists');
  const dates=inputAPI.parseMoonDates('08/10/2024\n05/11/1998\n06/15/2000\n8/1/2024');
  assert.deepEqual(dates.map(value=>value.iso),['2024-08-10','1998-05-11','2000-06-15','2024-08-01']);
  assert.deepEqual(dates.map(value=>value.display),['08/10/2024','05/11/1998','06/15/2000','08/01/2024']);
  for(const date of ['02/30/2024','13/01/2024','15/06/2000','02/29/2023','2024-08-10','August 10, 2024'])assert.throws(()=>inputAPI.parseMoonDates(date),date);
  assert.equal(inputAPI.parseMoonDates('02/29/2024')[0].iso,'2024-02-29');
});
test('Moon numeric dates render exactly the intended month/day, including ambiguous dates',async()=>{
  const result=await renderMoons(new Canvas(),{dates:['08/10/2024','05/11/1998','06/15/2000'],columns:3,size:100,transparent:true});
  assert.deepEqual(result.items.map(item=>item.date),['2024-08-10','1998-05-11','2000-06-15']);
  assert.deepEqual(result.items.map(item=>item.utc),['2024-08-10T12:00:00.000Z','1998-05-11T12:00:00.000Z','2000-06-15T12:00:00.000Z']);
});
test('Moon rendering rejects invalid MDY or a DMY override, but accepts internal ISO dates',async()=>{
  for(const date of ['02/30/2024','13/01/2024','15/06/2000'])await assert.rejects(renderMoons(new Canvas(),{dates:[date],size:100}),date);
  await assert.rejects(renderMoons(new Canvas(),{dates:['05/11/1998'],dateOrder:'DMY',size:100}),'Moon does not expose a DMY reinterpretation');
  assert.equal((await renderMoons(new Canvas(),{dates:['2024-08-10'],size:100})).items[0].display,'08/10/2024');
});
const providerFixtures=[
  {dateISO:'1998-05-11',date_serial:'19980511',phase:.5,phase_name:'Full Moon',phase_img:'8.png',illumination:100,tilt:0},
  {dateISO:'2000-06-15',date_serial:'20000615',phase:.46,phase_name:'Waxing Gibbous',phase_img:'9.png',illumination:98,tilt:3.1},
  {dateISO:'2024-08-10',date_serial:'20240810',phase:.19,phase_name:'Waxing Crescent',phase_img:'13.png',illumination:30,tilt:23.9}
];
test('Moon reconstruction uses date-verified provider illumination, phase and tilt fixtures, not recalculated substitutions',async()=>{
  const metadata=providerFixtures.map(item=>normalizeMoonProviderMetadata({days:[item]},item.dateISO));
  const result=await renderMoons(new Canvas(),{dates:['05/11/1998','06/15/2000','08/10/2024'],providerMetadata:metadata,orientation:'provider',size:100,transparent:true});
  for(let i=0;i<metadata.length;i++){const item=result.items[i],expected=metadata[i];assert.equal(item.date,expected.dateISO);assert.equal(item.sourceDateSerial,expected.sourceDateSerial);assert.equal(item.illumination,expected.illumination);assert.equal(item.fraction,expected.illumination/100);assert.equal(item.phaseName,expected.phaseName);assert.equal(item.providerTilt,expected.providerTilt);assert.equal(item.sourceImageURL,expected.sourceImageURL);}
  assert.match(result.model,/MoonPhases/);assert.match(result.note,/not the provider photograph/);
});
test('Moon reconstruction refuses provider metadata from another date or incomplete provider lists',async()=>{
  const fixture=normalizeMoonProviderMetadata({days:[providerFixtures[2]]},'2024-08-10');
  for(const source of [{...fixture,dateISO:'2024-10-08'},{...fixture,sourceDateSerial:'20241008'},{...fixture,illumination:101},{...fixture,providerTilt:Infinity},{...fixture,phaseName:'unknown'}])await assert.rejects(renderMoons(new Canvas(),{dates:['08/10/2024'],providerMetadata:[source],orientation:'provider',size:100}));
  await assert.rejects(renderMoons(new Canvas(),{dates:['08/10/2024'],providerMetadata:[],orientation:'provider',size:100}));
});
function brightnessCentroid(pixels,size){let weight=0,x=0,y=0;for(let row=0;row<size;row++)for(let col=0;col<size;col++){const at=(row*size+col)*4,w=pixels[at]*pixels[at+3]/255;weight+=w;x+=(col+.5-size/2)*w;y+=(row+.5-size/2)*w;}return{x:x/weight,y:y/weight};}
test('provider tilt rotates lit-hemisphere centroid clockwise rather than reversing angle units',()=>{
  const size=80,meta={fraction:.3,brightLimbAngle:-90,waxing:true,providerTilt:0};const zero=shadeMoonPixels(size,meta,surface,{appearance:'natural',orientation:'provider'}),clockwise=shadeMoonPixels(size,{...meta,providerTilt:90},surface,{appearance:'natural',orientation:'provider'});
  const a=brightnessCentroid(zero,size),b=brightnessCentroid(clockwise,size);assert.ok(a.x>5);assert.ok(Math.abs(a.y)<.1);assert.ok(b.y>5,'positive provider tilt rotates bright right side down');assert.ok(Math.abs(b.x)<.1);assert.ok(Math.abs(a.x-b.y)<.1);
});
test('provider tilt rotates the complete texture and alpha disk, not only the terminator',()=>{
  const size=80,pattern={width:32,height:16,pixels:Uint8ClampedArray.from({length:32*16*4},(_,i)=>i%4===3?255:i%4===0?30+Math.floor(i/4)%32*6:i%4===1?40+Math.floor(Math.floor(i/4)/32)*10:100)};
  const meta={fraction:.3,brightLimbAngle:-90,waxing:true,providerTilt:0},zero=shadeMoonPixels(size,meta,pattern,{appearance:'natural',orientation:'provider'}),turned=shadeMoonPixels(size,{...meta,providerTilt:90},pattern,{appearance:'natural',orientation:'provider'});
  for(const[x,y]of [[22,17],[56,24],[31,61],[58,53]])for(let channel=0;channel<4;channel++){const expected=zero[((size-1-x)*size+y)*4+channel],actual=turned[(y*size+x)*4+channel];assert.ok(Math.abs(expected-actual)<=1,`rotated texture sample ${x},${y},${channel}`);}
});
test('Sky/Moon manifest lists all current asset hashes including timezone supplement and sources',async()=>{
  const manifest=JSON.parse(await readFile(new URL('../assets/sky-moon/asset-manifest.json',import.meta.url),'utf8'));assert.equal(manifest.files.length,12);
  assert.ok(manifest.files.some(item=>item.name==='moonphases-original/manifest.json'));
  for(const item of manifest.files){const bytes=await readFile(new URL('../assets/sky-moon/'+item.name,import.meta.url));assert.equal(bytes.length,item.bytes,item.name);assert.equal(createHash('sha256').update(bytes).digest('hex'),item.sha256,item.name);}
});
