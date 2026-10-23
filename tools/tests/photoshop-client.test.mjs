import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {runInNewContext} from 'node:vm';
import {webcrypto,createHash} from 'node:crypto';
import {existsSync} from 'node:fs';
import {createRequire} from 'node:module';
import http from 'node:http';
import {gunzipSync} from 'node:zlib';
import {fileURLToPath} from 'node:url';

const source=await readFile(new URL('../photoshop-client.js',import.meta.url),'utf8');
const token='a'.repeat(64),handoff='b'.repeat(64),id='c'.repeat(32);
const caps=['psd-import-v1','psd-template-export-v1'];
const json=(value,status=200)=>new Response(JSON.stringify(value),{status,headers:{'content-type':'application/json'}});
const health=(capabilities=caps)=>json({service:'mitom-web-image',version:1,capabilities});
function make(fetch){const module={exports:{}};runInNewContext(source,{module,Blob,File,Uint8Array,DataView,AbortController,setTimeout,clearTimeout,Number,Error});return module.exports.create({fetch,crypto:webcrypto});}
function psd(){const b=new Uint8Array(80);b.set([56,66,80,83]);const v=new DataView(b.buffer);v.setUint16(4,1);v.setUint16(12,4);v.setUint32(14,2);v.setUint32(18,3);v.setUint16(22,8);v.setUint16(24,3);return b;}
function metadata(bytes=psd()){return{id,name:'Acrylic artwork.psd',bytes:bytes.length,sha256:createHash('sha256').update(bytes).digest('hex'),sourceDocumentId:17,autoGenerate:true};}
const code=expected=>error=>error.code===expected;

test('handoff exchanges once without bearer, validates health, and stores no token in public state',async()=>{
  const calls=[],client=make(async(url,init)=>{calls.push({url,init});return url.endsWith('/v1/handoff/'+handoff)?json({ok:true,token}):health();});
  await client.acceptHandoff(handoff);
  assert.equal(calls.length,2);assert.equal(calls[0].init.headers.Authorization,undefined);assert.match(calls[0].init.headers['X-Mitom-Client-Id'],/^[a-f0-9]{32}$/);assert.equal(calls[1].init.headers.Authorization,'Bearer '+token);
  assert.ok(calls.every(call=>call.init.credentials==='omit'&&call.init.redirect==='error'&&call.init.referrerPolicy==='no-referrer'));
  assert.equal(client.status().connected,true);assert.equal(client.status().supportsPSD,true);assert.equal(client.status().supportsTemplates,true);
  assert.ok(!JSON.stringify(client.status()).includes(token));client.forget();assert.equal(client.status().connected,false);assert.equal(client.status().supportsTemplates,false);
});

test('lost one-use handoff response is never retried and malformed codes never request',async()=>{
  let calls=0;const client=make(async()=>{calls++;throw Error('network');});
  await assert.rejects(client.acceptHandoff('bad'),code('HANDOFF_INVALID'));assert.equal(calls,0);
  await assert.rejects(client.acceptHandoff(handoff),code('HANDOFF_UNCERTAIN'));assert.equal(calls,1);assert.equal(client.status().connected,false);
});

test('old bridges disable template polling and PSD sends without probing mutating endpoints',async()=>{
  let calls=0;const client=make(async()=>{calls++;return health([]);});await client.pair(token);
  assert.equal(await client.nextTemplate(),null);await assert.rejects(client.sendPSD(new Blob([psd()])),code('PSD_UNSUPPORTED'));assert.equal(calls,1);
});

test('unsupported queue endpoint disables following polls while preserving PNG pairing',async()=>{
  let calls=0;const client=make(async url=>{calls++;return url.endsWith('/v1/health')?health():json({error:'NOT_FOUND'},404);});await client.pair(token);
  assert.equal(await client.nextTemplate(),null);assert.equal(await client.nextTemplate(),null);assert.equal(calls,2);assert.equal(client.status().connected,true);assert.equal(client.status().supportsTemplates,false);
});

test('template binary size and SHA256 are checked before a named PSD File is returned; ack is separate',async()=>{
  const bytes=psd(),meta=metadata(bytes),calls=[];
  const client=make(async(url,init)=>{calls.push({url,init});if(url.endsWith('/v1/health'))return health();if(url.endsWith('/next'))return json({ok:true,template:meta});if(url.endsWith('/ack'))return json({ok:true});return new Response(bytes,{headers:{'content-length':String(bytes.length),'content-type':'application/octet-stream'}});});
  await client.pair(token);const next=await client.nextTemplate(),file=await client.readTemplate(next);
  assert.equal(file.name,meta.name);assert.equal(file.type,'image/vnd.adobe.photoshop');assert.deepEqual(new Uint8Array(await file.arrayBuffer()),bytes);
  assert.equal(calls.filter(call=>call.url.endsWith('/ack')).length,0,'reading alone never acknowledges');await client.ackTemplate(meta.id);
  assert.equal(calls.filter(call=>call.url.endsWith('/ack')).length,1);assert.equal(calls.at(-1).init.method,'POST');
  for(const call of calls)assert.equal(call.init.headers.Authorization,'Bearer '+token);
  const templateCalls=calls.filter(call=>call.url.includes('/v1/templates/')),clientIDs=templateCalls.map(call=>call.init.headers['X-Mitom-Client-Id']);
  assert.equal(new Set(clientIDs).size,1,'one page nonce persists through next, read and ack');assert.match(clientIDs[0],/^[a-f0-9]{32}$/);
  assert.equal(calls[0].init.headers['X-Mitom-Client-Id'],undefined,'health needs no queue ownership nonce');
});

test('changed, truncated, oversized and non-PSD template bodies never get accepted or acknowledged',async()=>{
  const bytes=psd(),meta=metadata(bytes);
  for(const kind of ['changed','short','long','wrong-length','wrong-magic']){
    let body=bytes.slice(),expected={...meta};
    if(kind==='changed')body[70]^=1;if(kind==='short')body=body.subarray(0,-1);if(kind==='long')body=new Uint8Array(bytes.length+1);
    if(kind==='wrong-magic'){body[0]=0;expected=metadata(body);}
    const calls=[],client=make(async(url,init)=>{calls.push(url);if(url.endsWith('/v1/health'))return health();return new Response(body,{headers:kind==='wrong-length'?{'content-length':'1'}:{}});});await client.pair(token);
    await assert.rejects(client.readTemplate(expected),error=>['TEMPLATE_INTEGRITY','TEMPLATE_SIZE','PSD_REQUIRED'].includes(error.code));assert.ok(calls.every(url=>!url.endsWith('/ack')));
  }
});

test('queue rejects malformed descriptors and unpaired clients make no request',async()=>{
  let calls=0;let next=metadata();const client=make(async url=>{calls++;return url.endsWith('/health')?health():json({ok:true,template:next});});
  await assert.rejects(client.nextTemplate(),code('NOT_PAIRED'));assert.equal(calls,0);await client.pair(token);
  for(const patch of [{id:'../file'},{name:'../private.psd'},{bytes:257*1024*1024},{sha256:'bad'},{sourceDocumentId:-1},{autoGenerate:false}]){next={...metadata(),...patch};await assert.rejects(client.nextTemplate(),code('TEMPLATE_METADATA'));}
});

test('decimal string Photoshop document IDs remain compatible and normalize to safe numeric IDs',async()=>{
  let sourceDocumentId='17';const client=make(async url=>url.endsWith('/health')?health():json({ok:true,template:{...metadata(),sourceDocumentId}}));await client.pair(token);
  assert.equal((await client.nextTemplate()).sourceDocumentId,17);
  for(const invalid of [0,'0','-1',' 17','1e3','9007199254740992','../17']){sourceDocumentId=invalid;await assert.rejects(client.nextTemplate(),code('TEMPLATE_METADATA'));}
});

test('PSD import uses one octet-stream POST and requires a matching new-document receipt',async()=>{
  const calls=[],client=make(async(url,init)=>{calls.push({url,init});return url.endsWith('/health')?health():json({ok:true,requestId:init.headers['X-Mitom-Request-Id'],mode:'document'});});await client.pair(token);
  const result=await client.sendPSD(new Blob([psd()],{type:'image/vnd.adobe.photoshop'}),'3D IN / DEPTH / CUT');
  assert.equal(result.mode,'document');assert.equal(calls.length,2);const sent=calls[1];assert.ok(sent.url.endsWith('/v1/import-psd'));assert.equal(sent.init.method,'POST');assert.equal(sent.init.headers['Content-Type'],'application/octet-stream');assert.match(sent.init.headers['X-Mitom-Request-Id'],/^[a-f0-9]{32}$/);assert.equal(client.status().busy,false);
});

test('invalid PSD cannot POST; uncertain import never retries; invalid receipt stays explicit',async()=>{
  for(const outcome of ['network','receipt']){
    let posted=0;const client=make(async(url,init)=>{if(url.endsWith('/health'))return health();posted++;if(outcome==='network')throw Error('lost');return json({ok:true,requestId:init.headers['X-Mitom-Request-Id'],mode:'layer'});});await client.pair(token);
    const bad=psd();bad[22]=16;await assert.rejects(client.sendPSD(new Blob([bad])),code('PSD_REQUIRED'));assert.equal(posted,0);
    await assert.rejects(client.sendPSD(new Blob([psd()])),code(outcome==='network'?'IMPORT_UNCERTAIN':'INVALID_RECEIPT'));assert.equal(posted,1);assert.equal(client.status().busy,false);
  }
});

test('forgetting pairing while a template is downloaded prevents a stale acceptance',async()=>{
  let release;const pending=new Promise(resolve=>release=resolve),bytes=psd();const client=make(async url=>url.endsWith('/health')?health():pending);
  await client.pair(token);const work=client.readTemplate(metadata(bytes));client.forget();release(new Response(bytes));await assert.rejects(work,code('PAIR_CANCELLED'));
});

test('existing PNG send remains available on the original health contract',async()=>{
  let posts=0;const client=make(async(url,init)=>{if(url.endsWith('/health'))return health([]);posts++;assert.ok(url.endsWith('/v1/import'));assert.equal(init.headers['Content-Type'],'image/png');return json({ok:true,requestId:init.headers['X-Mitom-Request-Id'],mode:'layer'});});
  await client.pair(token);const result=await client.send(new Blob([Uint8Array.of(137,80,78,71)],{type:'image/png'}),'Moon');assert.equal(result.mode,'layer');assert.equal(posts,1);
});

test('one in-flight PSD transfer blocks duplicate sends and re-pairing',async()=>{
  let release,entered;const pending=new Promise(resolve=>release=resolve),started=new Promise(resolve=>entered=resolve);let posts=0,requestId;
  const client=make(async(url,init)=>{if(url.endsWith('/health'))return health();posts++;requestId=init.headers['X-Mitom-Request-Id'];entered();return pending;});await client.pair(token);
  const first=client.sendPSD(new Blob([psd()]));await started;await assert.rejects(client.sendPSD(new Blob([psd()])),code('BUSY'));await assert.rejects(client.pair(token),code('BUSY'));assert.equal(posts,1);
  release(json({ok:true,requestId,mode:'document'}));await first;assert.equal(client.status().busy,false);
});

const bridgePath=new URL('../../../panel-bridge/js/web-tools-bridge.js',import.meta.url);
test('actual staged panel HTTP bridge interoperates with the browser handoff, queue, hash validation and acknowledgment',{skip:!existsSync(bridgePath),timeout:30000},async()=>{
  const bridgeModule=createRequire(import.meta.url)(fileURLToPath(bridgePath)),bytes=gunzipSync(await readFile(new URL('../assets/templates/psd/template-acrylic.psd.gz',import.meta.url)));
  let server,time=1000;
  const receiver=bridgeModule.create({now:()=>time,onImport:async()=>({mode:'layer'}),onImportPsd:async()=>({mode:'document'}),http:{createServer(handler){server=http.createServer(handler);const listen=server.listen.bind(server);server.listen=(_port,host,callback)=>listen(0,host,callback);return server;}}});
  try{
    const session=await receiver.start();
    // A real HTTP exchange uses an ephemeral test port; preserve the production
    // Host and Origin checks rather than binding or interrupting port 8791.
    const transport=async(url,init)=>{
      const body=init.body?Buffer.from(await init.body.arrayBuffer()):null;
      return new Promise((resolve,reject)=>{
        const req=http.request({hostname:'127.0.0.1',port:server.address().port,path:new URL(url).pathname,method:init.method||'GET',headers:{Host:'127.0.0.1:8791',Origin:'https://mitomyhy.github.io',...init.headers,...(body?{'Content-Length':body.length}:{})},signal:init.signal},res=>{
          const chunks=[];res.on('data',chunk=>chunks.push(chunk));res.on('error',reject);res.on('end',()=>resolve(new Response(Buffer.concat(chunks),{status:res.statusCode,headers:res.headers})));
        });req.on('error',reject);req.end(body);
      });
    };
    const expected=receiver.enqueueTemplate({bytes,name:'Contract fixture.psd',sourceDocumentId:17}),codeValue=receiver.createHandoff(),client=make(transport);
    await client.acceptHandoff(codeValue);assert.equal(client.status().supportsTemplates,true);
    time+=16*60*1000; // The template must survive a slow CPU / model download.
    const queued=await client.nextTemplate();assert.equal(queued.id,expected.id);assert.equal(queued.sourceDocumentId,17);
    const other=make(transport);await other.pair(session.token);assert.equal(await other.nextTemplate(),null,'another tab cannot claim an owned PSD');
    await assert.rejects(other.readTemplate(queued),code('TEMPLATE_CLAIMED'));await assert.rejects(other.ackTemplate(queued.id),code('TEMPLATE_CLAIMED'));
    time+=6*60*1000;const reclaimed=await other.nextTemplate();assert.equal(reclaimed.id,queued.id,'expired ownership releases the claim, not the PSD');
    await assert.rejects(client.readTemplate(queued),code('TEMPLATE_CLAIMED'));await assert.rejects(client.ackTemplate(queued.id),code('TEMPLATE_CLAIMED'));
    const file=await other.readTemplate(reclaimed);assert.equal(file.name,'Contract fixture.psd');assert.equal(file.size,bytes.length);
    assert.equal(createHash('sha256').update(new Uint8Array(await file.arrayBuffer())).digest('hex'),expected.sha256);
    await other.ackTemplate(reclaimed.id);assert.equal(await client.nextTemplate(),null);
    const reserved=receiver.enqueueTemplate({bytes,name:'Reserved Photoshop button.psd',sourceDocumentId:18}),reservedCode=receiver.createHandoff(reserved.id);
    assert.equal(await client.nextTemplate(),null,'an older paired tab cannot steal the button snapshot before handoff');
    const targetTab=make(transport);await targetTab.acceptHandoff(reservedCode);const targeted=await targetTab.nextTemplate();assert.equal(targeted.id,reserved.id,'handoff claims the matching button snapshot');
    assert.equal(await other.nextTemplate(),null,'other tab cannot race a reserved handoff owner');
    await targetTab.readTemplate(targeted);await targetTab.ackTemplate(targeted.id);
    const replay=make(transport);await assert.rejects(replay.acceptHandoff(codeValue),error=>error.code==='HANDOFF_EXPIRED');assert.equal(replay.status().connected,false);
  }finally{const closed=server?new Promise(resolve=>server.once('close',resolve)):Promise.resolve();receiver.dispose();await closed;}
});
