import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {runInNewContext} from 'node:vm';
import {createPlaceResolver} from '../sky-place-controller.js';

const leeds={geonameId:2644688,label:'Leeds, England, United Kingdom',lat:53.79648,lon:-1.54785,timeZone:'Europe/London'};
const matched=()=>({status:'matched',result:leeds});

async function pageLifecycle(resolver){
  const code=await readFile(new URL('../workbench.js',import.meta.url),'utf8');
  // Exercise the shipped handlers themselves, with no browser teardown. A
  // beforeunload event does not establish that the document actually left.
  const handlers=code.split('\n').filter(line=>/^window\.addEventListener\('(beforeunload|pagehide)',/.test(line));
  assert.ok(handlers.some(line=>line.startsWith("window.addEventListener('pagehide'")));
  const events=new Map(),clearedIntervals=[],templateTimer={kind:'template-poll'};
  runInNewContext(handlers.join('\n'),{
    window:{addEventListener(name,fn){const listeners=events.get(name)||[];listeners.push(fn);events.set(name,listeners);}},
    skyPlace:resolver,skyZoom:{dispose(){}},worker:null,depth:null,photoshop:null,moonAbort:null,
    clearTimeout(){},scrabbleNameTimer:null,clearInterval(timer){clearedIntervals.push(timer);},templatePollTimer:templateTimer
  });
  const dispatch=(name,event={})=>{for(const listener of events.get(name)||[])listener(event);};
  dispatch.clearedIntervals=clearedIntervals;dispatch.templateTimer=templateTimer;return dispatch;
}

test('canceled navigation or download does not close the Sky geocoder',async()=>{
  const resolver=createPlaceResolver({search:async()=>matched()}),dispatch=await pageLifecycle(resolver);
  await resolver.resolve('Leeds, UK');
  dispatch('beforeunload',{defaultPrevented:true});
  assert.equal((await resolver.resolve('Leeds, UK')).geonameId,leeds.geonameId);
  resolver.input('Leeds, UK');
  assert.equal((await resolver.resolve('Leeds, UK')).timeZone,'Europe/London');
  resolver.dispose();
});

test('restoring a cached page keeps Sky usable; final page exit disposes it',async()=>{
  const resolver=createPlaceResolver({search:async()=>matched()}),dispatch=await pageLifecycle(resolver);
  dispatch('pagehide',{persisted:true});
  assert.deepEqual(dispatch.clearedIntervals,[],'BFCache entry keeps the template poll timer alive');
  assert.equal((await resolver.resolve('Leeds, UK')).geonameId,leeds.geonameId);
  dispatch('pagehide',{persisted:false});
  assert.deepEqual(dispatch.clearedIntervals,[dispatch.templateTimer],'final exit clears the owned template polling timer once');
  await assert.rejects(resolver.resolve('Leeds, UK'),/Bộ tra địa điểm đã đóng/);
});

test('already queued canceled debounce cannot erase a newer resolved city',async()=>{
  let queued,searches=0;
  const resolver=createPlaceResolver({search:async()=>{searches++;return matched();},
    setTimer(fn){queued=fn;return 1;},clearTimer(){}});
  resolver.input('London, UK');
  await resolver.resolve('Leeds, UK');
  const before=resolver.state();
  queued();
  await Promise.resolve();await Promise.resolve();
  assert.equal(searches,1);
  assert.deepEqual(resolver.state(),before);
  resolver.dispose();
});
