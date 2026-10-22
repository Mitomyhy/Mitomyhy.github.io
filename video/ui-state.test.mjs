import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import {buildVideoPayload,parseVideoResponse,requestVideo,validateEndpoint} from './client.mjs';

// Exercise the shipped UI handlers without a network, provider credits or a browser.
const source=readFileSync(new URL('./video.js',import.meta.url),'utf8').replace(/^import[^\n]+\n/,'');
const connection={generate:'https://n8n.example.test/webhook/generate',status:'https://n8n.example.test/webhook/status'};
const pending=id=>({code:200,data:{taskId:id}});
const complete=id=>({code:200,data:{taskId:id,state:'success',resultUrls:[`https://cdn.example.test/${id}.mp4`]}});
const response=body=>({ok:true,json:async()=>body});
function deferred(){let resolve;const promise=new Promise(yes=>resolve=yes);return{promise,resolve};}

function harness(transport,{storedTask=null}={}){
  const ids=new Map(),events=new Map(),storage=new Map([['mitom-video-connection',JSON.stringify(connection)]]),timers=new Map(),calls=[];
  if(storedTask)storage.set('mitom-video-task',JSON.stringify(storedTask));
  let timerId=0,reads=0;
  class Element{
    constructor(tag){this.tagName=tag;this.children=[];this.parent=null;this.attributes=new Map();this.dataset={};this.hidden=false;this.disabled=false;this.value='';this.files=[];this.listeners=new Map();this.src='';this.href='';this.pauses=0;this.loads=0;const names=new Set();this.classList={toggle(name,on){if(on??!names.has(name))names.add(name);else names.delete(name);},add:name=>names.add(name),remove:name=>names.delete(name),contains:name=>names.has(name)};}
    set id(value){this._id=value;ids.set(value,this);}get id(){return this._id;}
    set className(value){this.attributes.set('class',value);}
    setAttribute(key,value){this.attributes.set(key,String(value));if(key==='id')this.id=value;if(key==='hidden')this.hidden=true;if(key==='disabled')this.disabled=true;if(key==='value')this.value=String(value);if(key.startsWith('data-'))this.dataset[key.slice(5).replace(/-([a-z])/g,(_,c)=>c.toUpperCase())]=String(value);}
    getAttribute(key){return this.attributes.get(key)??null;}
    removeAttribute(key){this.attributes.delete(key);if(['src','href'].includes(key))this[key]='';}
    append(child){this.children.push(child);child.parent=this;}
    before(child){this.parent.children.splice(this.parent.children.indexOf(this),0,child);child.parent=this.parent;}
    addEventListener(name,handler){this.listeners.set(name,handler);}
    fire(name){return this.listeners.get(name)?.({preventDefault(){}});}
    querySelectorAll(selector){const selectors=selector.split(',').map(s=>s.trim());const matches=node=>selectors.some(s=>s.startsWith('[')?node.attributes.has(s.slice(1,-1)):s.startsWith('.')?(node.attributes.get('class')||'').split(/\s+/).includes(s.slice(1)):node.tagName===s);const found=[];const walk=node=>{for(const child of node.children){if(matches(child))found.push(child);walk(child);}};walk(this);return found;}
    set innerHTML(html){const stack=[this],voids=new Set(['input','img','br','meta','link','hr']);for(const match of html.matchAll(/<\s*(\/?)([\w:-]+)\b([^>]*)>/g)){const[,closing,tag,attrs]=match;if(closing){stack.pop();continue;}const child=new Element(tag);for(const attr of attrs.matchAll(/([^\s=\/]+)(?:="([^"]*)")?/g))child.setAttribute(attr[1],attr[2]??'');stack.at(-1).append(child);if(!voids.has(tag)&&!attrs.trim().endsWith('/'))stack.push(child);}}
    focus(){}pause(){this.pauses++;}load(){this.loads++;}
  }
  const body=new Element('body'),main=new Element('main'),tools=new Element('main');main.id='main';tools.id='tools-app';body.append(main);body.append(tools);
  for(let i=0;i<2;i++){const nav=new Element('a');nav.className='nav-video';body.append(nav);}
  const document={body,documentElement:{lang:'en'},createElement:tag=>new Element(tag),getElementById:id=>ids.get(id),querySelectorAll:selector=>body.querySelectorAll(selector)};
  const location={hash:'#video'},window={addEventListener:(name,handler)=>events.set(name,handler)};
  class BrowserURL extends URL{static createObjectURL(){return 'blob:product-photo';}static revokeObjectURL(){}}
  const context=vm.createContext({document,location,window,Error,TypeError,AbortController,URL:BrowserURL,console,MutationObserver:class{observe(){}},
    FileReader:class{readAsDataURL(){reads++;this.result='data:image/png;base64,aGVsbG8=';this.onload();}},
    localStorage:{getItem:key=>storage.get(key)??null,setItem:(key,value)=>storage.set(key,value),removeItem:key=>storage.delete(key)},
    setTimeout:(fn,delay)=>{const id=++timerId;timers.set(id,{fn,delay});return id;},clearTimeout:id=>timers.delete(id),buildVideoPayload,validateEndpoint,
    requestVideo:(endpoint,payload,token,options)=>requestVideo(endpoint,payload,token,{...options,fetchFn:async(url,options)=>{calls.push({url,...options});return transport(url,options,calls.length);}})});
  vm.runInContext(source,context,{filename:'video.js'});
  ids.get('video-prompt').value='A slow orbit of my personalized ornament.';ids.get('video-ratio').value='16:9';
  return{node:id=>ids.get(id),calls,timers,reads:()=>reads,savedTask:()=>JSON.parse(storage.get('mitom-video-task')||'null'),
    submit:()=>ids.get('video-form').fire('submit'),check:()=>ids.get('video-check').fire('click'),stop:()=>ids.get('video-stop').fire('click'),
    navigate(hash){location.hash=hash;events.get('hashchange')();}};
}

test('terminal provider failure ends the task and allows a fresh submission',async()=>{
  const app=harness(async(_url,_options,count)=>response(count===1?pending('failed-job'):count===2?{code:200,data:{taskId:'failed-job',state:'fail',failMsg:'Product was rejected'}}:complete('next-job')));
  await app.submit();
  assert.equal(app.savedTask().done,true);assert.equal(app.savedTask().failed,true);assert.equal(app.node('video-generate').disabled,false);assert.equal(app.node('video-check').hidden,true);assert.match(app.node('video-status').textContent,/Product was rejected/);
  await app.submit();assert.equal(app.calls.length,3);assert.equal(app.savedTask().taskId,'next-job');assert.equal(app.node('video-result').hidden,false);
});

test('documented nested provider URL arrays work and malformed lists are rejected',()=>{
  for(const urls of [{resultUrls:['https://cdn.example.test/result.mp4']},{data:{result_urls:['https://cdn.example.test/result.mp4']}},{data:{resultUrls:['https://cdn.example.test/result.mp4']}}]){
    assert.equal(parseVideoResponse({code:200,data:{taskId:'nested-job',state:'success',resultJson:JSON.stringify(urls)}}).videoUrl,'https://cdn.example.test/result.mp4');
  }
  for(const urls of [{resultUrls:'https://cdn.example.test/result.mp4'},{data:{result_urls:{0:'https://cdn.example.test/result.mp4',length:1}}}]){
    assert.throws(()=>parseVideoResponse({code:200,data:{taskId:'bad-job',state:'success',resultJson:JSON.stringify(urls)}}),error=>error.terminal===true&&/invalid video URL list/.test(error.message));
  }
});

test('completed task without a video is terminal, but HTTP 401 keeps the pending ID recoverable',async()=>{
  const app=harness(async(_url,_options,count)=>count===1?response(pending('recoverable-job')):count===2?{ok:false,status:401}:response(complete('recoverable-job')));
  await app.submit();assert.equal(app.savedTask().done,false);assert.equal(app.savedTask().taskId,'recoverable-job');assert.equal(app.node('video-generate').disabled,true);assert.equal(app.node('video-check').hidden,false);
  await app.check();assert.equal(app.savedTask().done,true);assert.equal(app.node('video-result').hidden,false);
  const missing=harness(async(_url,_options,count)=>response(count===1?pending('missing-video'):{code:200,data:{taskId:'missing-video',state:'success'}}));
  await missing.submit();assert.equal(missing.savedTask().failed,true);assert.equal(missing.node('video-generate').disabled,false);
});

test('navigating away during generation POST retains its response and does not start hidden polling',async()=>{
  const wait=deferred(),app=harness(()=>wait.promise),sending=app.submit();
  assert.equal(app.calls.length,1);app.navigate('#tools/sky');
  assert.equal(app.calls[0].signal.aborted,false);assert.equal(app.node('video-generate').disabled,true);
  wait.resolve(response(pending('background-job')));await sending;
  assert.equal(app.savedTask().taskId,'background-job');assert.equal(app.savedTask().done,false);assert.equal(app.calls.length,1);assert.equal(app.timers.size,0);
  app.navigate('#video');assert.equal(app.node('video-check').hidden,false);assert.equal(app.node('video-check').disabled,false);
});

test('accepted next submission immediately removes the previous MP4 and download',async()=>{
  const wait=deferred(),app=harness(()=>wait.promise,{storedTask:{taskId:'old-job',endpoint:connection.status,done:true,videoUrl:'https://cdn.example.test/old-job.mp4'}});
  assert.equal(app.node('video-result').hidden,false);assert.match(app.node('video-player').src,/old-job/);
  const sending=app.submit();assert.equal(app.node('video-result').hidden,true);assert.equal(app.node('video-empty').hidden,false);assert.equal(app.node('video-player').src,'');assert.equal(app.node('video-download').href,'');assert.equal(app.savedTask(),null);assert.ok(app.node('video-player').loads>0);
  app.navigate('#home');wait.resolve(response(pending('new-job')));await sending;assert.equal(app.savedTask().taskId,'new-job');
});

test('stopping checks ignores a late status response and retains the pending task',async()=>{
  const wait=deferred(),app=harness(()=>wait.promise,{storedTask:{taskId:'pending-job',endpoint:connection.status,done:false}}),checking=app.check();
  app.stop();assert.equal(app.calls[0].signal.aborted,true);wait.resolve(response(complete('pending-job')));await checking;
  assert.equal(app.savedTask().done,false);assert.equal(app.node('video-result').hidden,true);assert.match(app.node('video-status').textContent,/checks stopped/);
});

test('restored failed tasks remain finished and image upload enforces the 5 MiB cap',async()=>{
  const app=harness(()=>{throw Error('Must not submit');},{storedTask:{taskId:'failed-job',endpoint:connection.status,done:true,failed:true}});
  assert.equal(app.node('video-generate').disabled,false);assert.match(app.node('video-status').textContent,/previous task failed/);
  const file=app.node('video-file');file.files=[{type:'image/png',size:5*1024*1024+1}];await file.fire('change');assert.equal(app.reads(),0);assert.match(app.node('video-status').textContent,/up to 5 MB/);
  file.files=[{type:'image/png',size:5*1024*1024}];await file.fire('change');assert.equal(app.reads(),1);
});
