import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import * as photo from './client.mjs';
const source=readFileSync(new URL('./video.js',import.meta.url),'utf8').replace(/^import[^\n]+\n/gm,'');
function deferred(){let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return{promise,resolve,reject};}
function photoFile(){const bytes=new Uint8Array(24);bytes.set([137,80,78,71,13,10,26,10]);bytes.set([73,72,68,82],12);const view=new DataView(bytes.buffer);view.setUint32(16,800);view.setUint32(20,600);return{name:'my-product.png',type:'image/png',size:24,arrayBuffer:async()=>bytes.buffer};}
function harness({exporter=async()=>({blob:new Blob(['local-video']),extension:'mp4',mimeType:'video/mp4'}),support={supported:true,mp4:true,webm:true}}={}){
  const ids=new Map(),events=new Map(),frames=new Map(),calls=[],revoked=[];let frameId=0,urlId=0,observer=null;
  const ctx={fillRect(){},drawImage(){}};
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
    get options(){return this.children.filter(node=>node.tagName==='option');}
    querySelectorAll(selector){const selectors=selector.split(',').map(s=>s.trim());const matches=node=>selectors.some(s=>s.startsWith('[')?node.attributes.has(s.slice(1,-1)):s.startsWith('.')?(node.attributes.get('class')||'').split(/\s+/).includes(s.slice(1)):node.tagName===s);const found=[];const walk=node=>{for(const child of node.children){if(matches(child))found.push(child);walk(child);}};walk(this);return found;}
    set innerHTML(html){const stack=[this],voids=new Set(['input','img','br','meta','link','hr']);for(const match of html.matchAll(/<\s*(\/?)([\w:-]+)\b([^>]*)>/g)){const[,closing,tag,attrs]=match;if(closing){const closed=stack.pop();if(closed.tagName==='select')closed.value=(closed.options.find(option=>option.attributes.has('selected'))||closed.options[0])?.value||'';continue;}const child=new Element(tag);for(const attr of attrs.matchAll(/([^\s=\/]+)(?:="([^"]*)")?/g))child.setAttribute(attr[1],attr[2]??'');stack.at(-1).append(child);if(!voids.has(tag)&&!attrs.trim().endsWith('/'))stack.push(child);}}
    getContext(){return ctx;}focus(){}pause(){this.pauses++;}load(){this.loads++;}
  }
  const body=new Element('body'),main=new Element('main'),tools=new Element('main');main.id='main';tools.id='tools-app';body.append(main);body.append(tools);for(let i=0;i<2;i++){const nav=new Element('a');nav.className='nav-video';body.append(nav);}
  const document={body,documentElement:{lang:'en'},createElement:tag=>new Element(tag),getElementById:id=>ids.get(id),querySelectorAll:selector=>body.querySelectorAll(selector)};
  const location={hash:'#video'},window={addEventListener:(name,handler)=>events.set(name,handler)};
  class BrowserURL extends URL{static createObjectURL(){return`blob:local-${++urlId}`;}static revokeObjectURL(value){revoked.push(value);}}
  const context=vm.createContext({...photo,document,location,window,Error,TypeError,AbortController,Blob,URL:BrowserURL,Date,console,MutationObserver:class{constructor(fn){observer=fn;}observe(){}},
    Image:class{constructor(){this.naturalWidth=800;this.naturalHeight=600;}async decode(){}},createImageBitmap:async()=>({width:800,height:600,close(){}}),
    requestAnimationFrame:fn=>{const id=++frameId;frames.set(id,fn);return id;},cancelAnimationFrame:id=>frames.delete(id),fetch:()=>{throw Error('Photo video must not make a network request');},
    getExportSupport:()=>support,exportPhotoVideo:async options=>{calls.push(options);return exporter(options);}});
  vm.runInContext(source,context,{filename:'video.js'});
  return{node:id=>ids.get(id),calls,revoked,submit:()=>ids.get('video-form').fire('submit'),cancel:()=>ids.get('video-cancel').fire('click'),
    async loadPhoto(){ids.get('video-file').files=[photoFile()];ids.get('video-file').fire('change');for(let i=0;i<12;i++)await Promise.resolve();},
    navigate(hash){location.hash=hash;events.get('hashchange')();},language(lang){document.documentElement.lang=lang;observer();}};
}
test('localphoto opens withoutnetwork and enablespreview/export',async()=>{const app=harness();assert.equal(app.node('video-generate').disabled,true);await app.loadPhoto();assert.equal(app.node('video-generate').disabled,false);assert.equal(app.node('video-canvas').hidden,false);assert.equal(app.node('video-empty').hidden,true);assert.equal(app.node('video-offline-download').getAttribute('href'),'video/downloads/MITOM-Photo-Video-Local.html');});
test('export uses selectedframe geometry,durationand24fps andonlyonecall',async()=>{const app=harness({exporter:async options=>{options.drawFrame({fillRect(){},drawImage(){}},.5,options.width,options.height);options.onProgress(.6);return{blob:new Blob(['mp4']),extension:'mp4',mimeType:'video/mp4'};}});await app.loadPhoto();await app.submit();assert.equal(app.calls.length,1);assert.equal(app.calls[0].width,1280);assert.equal(app.calls[0].height,720);assert.equal(app.calls[0].duration,8);assert.equal(app.calls[0].fps,24);assert.equal(app.node('video-player').hidden,false);assert.equal(app.node('video-download').hidden,false);assert.match(app.node('video-download').download,/\.mp4$/);assert.equal(app.node('video-generate').disabled,false);});
test('cancel abortsexport, ignoreslatelateoutput andleavescontrolsready',async()=>{const wait=deferred(),app=harness({exporter:()=>wait.promise});await app.loadPhoto();const sending=app.submit();app.cancel();assert.equal(app.calls[0].signal.aborted,true);wait.resolve({blob:new Blob(['late']),extension:'mp4'});await sending;assert.equal(app.node('video-download').hidden,true);assert.equal(app.node('video-generate').disabled,false);assert.match(app.node('video-status').textContent,/cancelled/);});
test('navigationdoesnotdiscardfinishedlocalexport andreturnshowssaveddownload',async()=>{const wait=deferred(),app=harness({exporter:()=>wait.promise});await app.loadPhoto();const sending=app.submit();app.navigate('#home');assert.equal(app.calls[0].signal.aborted,false);wait.resolve({blob:new Blob(['mp4']),extension:'mp4'});await sending;app.navigate('#video');assert.equal(app.node('video-app').hidden,false);assert.equal(app.node('video-download').hidden,false);});
test('actualWebMfallback keepscorrectextension andEtsyMP4warning',async()=>{const app=harness({exporter:async()=>({blob:new Blob(['webm']),extension:'webm'})});await app.loadPhoto();await app.submit();assert.match(app.node('video-download').download,/\.webm$/);assert.equal(app.node('video-codec-note').hidden,false);assert.match(app.node('video-codec-note').textContent,/Etsy needs MP4/);assert.match(app.node('video-format').textContent,/WEBM/);});
test('changingphotomotion revokespreviousresult andremovesstalepreview',async()=>{const app=harness();await app.loadPhoto();await app.submit();const previous=app.node('video-download').href;app.node('video-motion').value='panLeft';app.node('video-motion').fire('change');assert.ok(app.revoked.includes(previous));assert.equal(app.node('video-download').hidden,true);assert.equal(app.node('video-player').hidden,true);assert.equal(app.node('video-canvas').hidden,false);});
test('Vietnamese switch updatescontrols andkeywordsselectrealpreset',async()=>{const app=harness();app.language('vi');assert.equal(app.node('video-generate').textContent,'Xuất video');app.node('video-prompt').value='sang trái';app.node('video-apply').fire('click');assert.equal(app.node('video-motion').value,'panLeft');assert.match(app.node('video-status').textContent,/Đã chọn/);});
test('unsupportedbrowsercannotstartanexport',async()=>{const app=harness({support:{supported:false,mp4:false,webm:false}});await app.loadPhoto();assert.equal(app.node('video-generate').disabled,true);await app.submit();assert.equal(app.calls.length,0);assert.match(app.node('video-status').textContent,/cannot export/);});
