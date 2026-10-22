import test from 'node:test';
import assert from 'node:assert/strict';
import {createPlaceResolver} from '../sky-place-controller.js';
import {zoomAt,createWorkspaceZoom} from '../workspace-zoom.js';
import {readFileSync} from 'node:fs';
const london={geonameId:2643743,label:'London, England, United Kingdom',lat:51.50853,lon:-.12574,timeZone:'Europe/London'};
const leeds={geonameId:2644688,label:'Leeds, England, United Kingdom',lat:53.79648,lon:-1.54785,timeZone:'Europe/London'};
const matched=result=>({status:'matched',result});
function clock(){let next=0;const jobs=new Map();return{setTimer(fn,delay){const id=++next;jobs.set(id,{fn,delay});return id;},clearTimer(id){jobs.delete(id);},get size(){return jobs.size;},async flush(){const tasks=[...jobs.values()];jobs.clear();await Promise.all(tasks.map(row=>row.fn()));},delays(){return [...jobs.values()].map(row=>row.delay);}};}
function deferred(){let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return{promise,resolve,reject};}
test('pasting city automatically debounces local lookup, without a Find button click',async()=>{
  const timer=clock(),calls=[],states=[];const resolver=createPlaceResolver({search:async query=>{calls.push(query);return matched(leeds);},onState:state=>states.push(state),setTimer:timer.setTimer,clearTimer:timer.clearTimer});
  resolver.input('  Leeds, UK  ');assert.deepEqual(calls,[]);assert.equal(timer.size,1);assert.deepEqual(timer.delays(),[350]);await timer.flush();assert.deepEqual(calls,['Leeds, UK']);assert.equal(resolver.state().status,'matched');assert.equal(resolver.state().place.timeZone,'Europe/London');assert.ok(states.some(state=>state.status==='loading'));resolver.dispose();
});
test('repeated edits cancel prior debounce; only final pasted city resolves',async()=>{
  const timer=clock(),calls=[];const resolver=createPlaceResolver({search:async query=>{calls.push(query);return matched(london);},setTimer:timer.setTimer,clearTimer:timer.clearTimer});resolver.input('Lon');resolver.input('London');resolver.input('London, UK');assert.equal(timer.size,1);await timer.flush();assert.deepEqual(calls,['London, UK']);resolver.dispose();
});
test('stale city result cannot overwrite the coordinates/timezone of a newer paste',async()=>{
  const old=deferred(),timer=clock();const resolver=createPlaceResolver({search:query=>query==='London, UK'?old.promise:Promise.resolve(matched(leeds)),setTimer:timer.setTimer,clearTimer:timer.clearTimer});
  const first=resolver.resolve('London, UK');resolver.input('Leeds, UK');const latest=await resolver.resolve('Leeds, UK');assert.equal(latest.geonameId,leeds.geonameId);old.resolve(matched(london));await assert.rejects(first,error=>error.code==='CANCELLED');assert.equal(resolver.state().place.geonameId,leeds.geonameId);resolver.dispose();
});
test('unknown, invalid coordinates or missing IANA zone never fall back to manual guesses',async()=>{
  for(const answer of [{status:'unmatched'},matched({...leeds,lat:91}),matched({...leeds,lon:181}),matched({...leeds,timeZone:undefined}),matched({...leeds,timeZone:'unknown/zone'})]){const resolver=createPlaceResolver({search:async()=>answer});await assert.rejects(resolver.resolve('Pasted city'));assert.equal(resolver.state().place,null);assert.equal(resolver.state().status,'error');resolver.dispose();}
});
test('ambiguous cities require an exact displayed selection, never the first result',async()=>{
  const resolver=createPlaceResolver({search:async()=>({status:'ambiguous',candidates:[london,leeds]})});await assert.rejects(resolver.resolve('Ambiguous'));assert.equal(resolver.state().status,'ambiguous');assert.equal(resolver.state().place,null);assert.throws(()=>resolver.choose({...leeds,lat:0}));assert.equal(resolver.choose(leeds).geonameId,leeds.geonameId);assert.equal(resolver.state().status,'matched');assert.equal(resolver.state().candidates.length,0);resolver.dispose();
});
test('accepted automatic city is reused by Render; disposal cancels timers and ignores in-flight results',async()=>{
  let calls=0;const timer=clock(),resolver=createPlaceResolver({search:async()=>{calls++;return matched(leeds);},setTimer:timer.setTimer,clearTimer:timer.clearTimer});await resolver.resolve('Leeds, UK');await resolver.resolve('Leeds, UK');await resolver.resolve(leeds.label);assert.equal(calls,1);resolver.input('London, UK');resolver.dispose();assert.equal(timer.size,0);await assert.rejects(resolver.resolve('London, UK'));
  const pending=deferred(),live=createPlaceResolver({search:()=>pending.promise}),request=live.resolve('Leeds, UK');live.dispose();pending.resolve(matched(leeds));await assert.rejects(request,error=>error.code==='CANCELLED');assert.equal(live.state().place,null);
});
test('short location is not searched and overlong pasted query is rejected',async()=>{
  const timer=clock(),resolver=createPlaceResolver({search:async()=>{throw Error('Must not search');},setTimer:timer.setTimer,clearTimer:timer.clearTimer});resolver.input('L');assert.equal(timer.size,0);await assert.rejects(resolver.resolve('L'));assert.throws(()=>resolver.input('x'.repeat(201)));resolver.dispose();
});
test('changing export size discards the old native Sky raster before effect can republish it',()=>{
  const code=readFileSync(new URL('../workbench.js',import.meta.url),'utf8');
  const handler=code.match(/\$\('tool-size'\)\.addEventListener\('change',\(\)=>\{([^\n]+)/)?.[1];
  assert.ok(handler);assert.match(handler,/rawSkyCanvas=null/);assert.match(handler,/rawSkyMeta=null/);
  assert.ok(handler.indexOf('rawSkyCanvas=null')<handler.indexOf('updateActions()'));
});
test('zoom is pointer-anchored, finite and bounded to 50%–800%',()=>{
  const prior={scale:1,x:5,y:-10},point={x:100,y:60},next=zoomAt(prior,point,-100);assert.ok(next.scale>1);assert.ok(Math.abs((point.x-prior.x)/prior.scale-(point.x-next.x)/next.scale)<1e-10);assert.ok(Math.abs((point.y-prior.y)/prior.scale-(point.y-next.y)/next.scale)<1e-10);
  let high=prior,low=prior;for(let i=0;i<20;i++){high=zoomAt(high,point,-300);low=zoomAt(low,point,300);}assert.equal(high.scale,8);assert.equal(low.scale,.5);assert.throws(()=>zoomAt({...prior,scale:0},point,1));assert.throws(()=>zoomAt(prior,point,NaN));
});
function stageFixture(){const handlers=new Map(),classes=new Set(),captures=[];const stage={addEventListener(name,fn,options){handlers.set(name,{fn,options});},removeEventListener(name,fn){if(handlers.get(name)?.fn===fn)handlers.delete(name);},classList:{toggle(name,value){if(value)classes.add(name);else classes.delete(name);},remove(name){classes.delete(name);}},getBoundingClientRect(){return{left:10,top:20,width:800,height:600};},setPointerCapture(id){captures.push(id);}};const canvas={style:{},width:2048,height:2048,getContext(){throw Error('Zoom must not touch canvas pixels');},toDataURL(){throw Error('Zoom must not re-encode PNG');}};return{stage,canvas,handlers,classes,captures,fire(name,event={}){handlers.get(name)?.fn(event);}};}
test('wheel zoom is disabled without a result; enabled wheel is CSS-only and preserves PNG raster size',()=>{
  const f=stageFixture();let enabled=false,prevented=0;const zoom=createWorkspaceZoom(f.stage,f.canvas,{isEnabled:()=>enabled});const event={clientX:610,clientY:420,deltaY:-100,deltaMode:0,preventDefault(){prevented++;}};
  f.fire('wheel',event);assert.equal(prevented,0);assert.deepEqual(zoom.state(),{scale:1,x:0,y:0});enabled=true;f.fire('wheel',event);assert.equal(prevented,1);assert.ok(zoom.state().scale>1);assert.match(f.canvas.style.transform,/translate\(.+scale\(/);assert.equal(f.canvas.width,2048);assert.equal(f.canvas.height,2048);assert.equal(f.handlers.get('wheel').options.passive,false);zoom.dispose();
});
test('wheel line/page units convert correctly, drag pans and reset restores fit',()=>{
  const f=stageFixture(),zoom=createWorkspaceZoom(f.stage,f.canvas),event={clientX:410,clientY:320,deltaY:-2,deltaMode:1,preventDefault(){}};f.fire('wheel',event);assert.ok(Math.abs(zoom.state().scale-Math.exp(32*.002))<1e-10);zoom.reset();f.fire('wheel',{...event,deltaY:-1,deltaMode:2});assert.ok(Math.abs(zoom.state().scale-Math.exp(300*.002))<1e-10);zoom.reset();f.fire('pointerdown',{button:0,pointerId:7,clientX:100,clientY:200,preventDefault(){}});f.fire('pointermove',{pointerId:8,clientX:160,clientY:240});assert.equal(zoom.state().x,0);f.fire('pointermove',{pointerId:7,clientX:160,clientY:240});assert.deepEqual(zoom.state(),{scale:1,x:60,y:40});f.fire('pointerup');f.fire('pointermove',{pointerId:7,clientX:200,clientY:300});assert.equal(zoom.state().x,60);zoom.reset();assert.deepEqual(zoom.state(),{scale:1,x:0,y:0});zoom.dispose();assert.equal(f.handlers.size,0);assert.equal(f.canvas.style.transform,'');assert.equal(f.classes.has('workspace-can-pan'),false);
});
