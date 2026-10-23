import test from 'node:test';
import assert from 'node:assert/strict';
import {createPSDJobs} from '../depth-psd-jobs.js';

const art={width:8,height:8,rgba:new Uint8Array(256).fill(255)};
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return{resolve,reject,promise};};
function harness(overrides={}){
  const runs=[],disposed=[];
  const jobs=createPSDJobs({open:async input=>({family:input.family||'acrylic',title:'Template',artwork:art,bytes:new Uint8Array(30)}),prepareArtwork:async()=>art,
    createPipeline(onProgress){const wait=deferred(),id=runs.length;runs.push({wait,onProgress});return{run(input){runs[id].input=input;return wait.promise;},dispose(){disposed.push(id);}};},
    build:async()=>new Uint8Array(30),...overrides});
  return {jobs,runs,disposed};
}
const result=()=>({width:8,height:8,depth:new Float32Array(64),alpha:new Uint8Array(64).fill(255),depth16PNG:new Uint8Array([137,80,78,71])});
test('blank template requires artwork; prepared PSD is immediately ready',async()=>{
  const {jobs}=harness();await jobs.load(0,{family:'acrylic'});assert.equal(jobs.snapshot().jobs[0].hasArtwork,false);assert.throws(()=>jobs.generate(0),/artwork/);
  await jobs.setArtwork(0,{});assert.equal(jobs.snapshot().jobs[0].hasArtwork,true);await jobs.load(1,{name:'prepared.psd'});assert.equal(jobs.snapshot().jobs[1].hasArtwork,true);jobs.dispose();
});
test('default queues second job and starts it after first PSD completes',async()=>{
  const {jobs,runs,disposed}=harness();await jobs.load(0,{});await jobs.load(1,{});jobs.generate(0);jobs.generate(1);
  assert.deepEqual(jobs.snapshot().jobs.map(j=>j.status),['running','queued']);assert.equal(runs.length,1);
  runs[0].wait.resolve(result());await tick();assert.equal(runs.length,2);assert.deepEqual(jobs.snapshot().jobs.map(j=>j.status),['done','running']);assert.deepEqual(disposed,[0]);
  runs[1].wait.resolve(result());await tick();assert.equal(jobs.busy,false);assert.equal(jobs.result(1).length,30);jobs.dispose();
});
test('uploading the bundled blank PSD also requires adding artwork',async()=>{
  const {jobs}=harness({open:async()=>({family:'acrylic',artwork:art,isBundledBlank:true})});await jobs.load(0,{name:'blank.psd'});assert.equal(jobs.snapshot().jobs[0].hasArtwork,false);assert.throws(()=>jobs.generate(0),/artwork/);jobs.dispose();
});
test('explicit two slots start two independent CPU workers; no automatic downgrade',async()=>{
  const {jobs,runs}=harness();jobs.setParallel(2);await jobs.load(0,{});await jobs.load(1,{});jobs.generate(0);jobs.generate(1);assert.equal(runs.length,2);assert.equal(jobs.snapshot().running,2);assert.equal(runs[0].input.outputMode,'full');assert.equal(runs[0].input.threads,1);jobs.dispose();
});
test('cancelling one slot preserves another and discards late result',async()=>{
  const {jobs,runs}=harness();jobs.setParallel(2);await jobs.load(0,{});await jobs.load(1,{});jobs.generate(0);jobs.generate(1);jobs.cancel(0);runs[0].wait.resolve(result());runs[1].wait.resolve(result());await tick();assert.equal(jobs.snapshot().jobs[0].status,'ready');assert.equal(jobs.snapshot().jobs[1].status,'done');assert.throws(()=>jobs.result(0),/hoàn tất/);jobs.dispose();
});
test('load selection race never reuses stale PSD or artwork',async()=>{
  const first=deferred(),{jobs}=harness({open:input=>input.name==='old'?first.promise:Promise.resolve({family:'ceramic_heart',artwork:art})});const old=jobs.load(0,{name:'old'});await jobs.load(0,{name:'new'});first.resolve({family:'acrylic',artwork:art});await old;assert.equal(jobs.snapshot().jobs[0].family,'ceramic_heart');assert.equal(jobs.snapshot().jobs[0].name,'new');jobs.dispose();
});
test('generate reads exact IN pixels and snapshots options',async()=>{
  const {jobs,runs}=harness();await jobs.load(0,{});const settings={fullprint:{support_enabled:true}};jobs.generate(0,settings);settings.fullprint.support_enabled=false;assert.equal(runs[0].input.rgba,art.rgba);assert.equal(runs[0].input.options.fullprint.support_enabled,true);jobs.dispose();
});
test('build receives full composition depth and original IN; 16 bit PNG kept separately',async()=>{
  let built;const {jobs,runs}=harness({build:async(native,options)=>{built=options;return new Uint8Array(30);}});await jobs.load(0,{});jobs.generate(0);const r=result();runs[0].wait.resolve(r);await tick();assert.equal(built.artwork,art);assert.equal(built.depth.depth,r.depth);assert.equal(built.depth.alpha,r.alpha);assert.equal(jobs.result(0,'map'),r.depth16PNG);jobs.dispose();
});
test('failed native build never offers a flattened or stale PSD',async()=>{
  const {jobs,runs}=harness({build:async()=>{throw Error('unsupported warp');}});await jobs.load(0,{});jobs.generate(0);runs[0].wait.resolve(result());await tick();assert.equal(jobs.snapshot().jobs[0].status,'error');assert.throws(()=>jobs.result(0),/hoàn tất/);jobs.dispose();
});
test('external PNG inference blocks a third concurrent AI run',async()=>{
  const {jobs,runs}=harness({isExternalBusy:()=>true});await jobs.load(0,{});assert.throws(()=>jobs.generate(0),/PNG/);assert.equal(runs.length,0);jobs.dispose();
});
test('lowering concurrency never interrupts active jobs; invalid slots rejected',async()=>{
  const {jobs,runs,disposed}=harness();jobs.setParallel(2);await jobs.load(0,{});await jobs.load(1,{});jobs.generate(0);jobs.generate(1);jobs.setParallel(1);assert.equal(jobs.snapshot().running,2);assert.equal(disposed.length,0);assert.throws(()=>jobs.setParallel(3));assert.throws(()=>jobs.cancel(2));jobs.dispose();assert.equal(disposed.length,2);runs.forEach(r=>r.wait.resolve(result()));await tick();
});
test('removal and disposal cancel active work and release large file references',async()=>{
  const {jobs,runs}=harness();await jobs.load(0,{});jobs.generate(0);jobs.remove(0);assert.equal(jobs.snapshot().jobs[0],null);jobs.dispose();runs[0].wait.resolve(result());await tick();assert.throws(()=>jobs.generate(0),/đóng/);
});
test('transparent artwork is rejected before any AI model work',async()=>{
  const {jobs,runs}=harness({prepareArtwork:async()=>({...art,rgba:new Uint8Array(256)})});await jobs.load(0,{family:'acrylic'});await jobs.setArtwork(0,{});assert.equal(jobs.snapshot().jobs[0].hasArtwork,false);assert.equal(jobs.snapshot().jobs[0].status,'error');assert.throws(()=>jobs.generate(0));assert.equal(runs.length,0);jobs.dispose();
});
test('replacing an active source lets the other queued job continue',async()=>{
  const {jobs,runs}=harness();await jobs.load(0,{});await jobs.load(1,{});jobs.generate(0);jobs.generate(1);await jobs.load(0,{name:'replacement.psd'});assert.equal(jobs.snapshot().jobs[1].status,'running');assert.equal(runs.length,2);jobs.dispose();runs.forEach(r=>r.wait.resolve(result()));await tick();
});
test('WASM CPU heap is disposed before allocating native PSD output',async()=>{
  const h=harness({build:async()=>{assert.deepEqual(h.disposed,[0]);return new Uint8Array(30);}});await h.jobs.load(0,{});h.jobs.generate(0);h.runs[0].wait.resolve(result());await tick();assert.equal(h.jobs.snapshot().jobs[0].status,'done');assert.deepEqual(h.disposed,[0]);h.jobs.dispose();
});
