import test from'node:test';
import assert from'node:assert/strict';
import{fetchMoonProviderMetadata,normalizeMoonProviderMetadata,moonProviderAPIURL}from'../moon-provider.js';
const fixtures=[{dateISO:'1998-05-11',date_serial:'19980511',phase:.5,phase_name:'Full Moon',phase_img:'8.png',illumination:100,tilt:0},{dateISO:'2000-06-15',date_serial:'20000615',phase:.46,phase_name:'Waxing Gibbous',phase_img:'9.png',illumination:98,tilt:3.1},{dateISO:'2024-08-10',date_serial:'20240810',phase:.19,phase_name:'Waxing Crescent',phase_img:'13.png',illumination:30,tilt:23.9}];
const payload=item=>({days:[{...item}]});
for(const item of fixtures)test(`exact panel fixture ${item.dateISO} yields matching date, image, illumination and rotation`,async()=>{
  let actualURL,actualOptions;const meta=await fetchMoonProviderMetadata(item.dateISO,{fetchImpl:async(url,options)=>{actualURL=url;actualOptions=options;return new Response(JSON.stringify(payload(item)),{headers:{'Content-Type':'application/json'}});}});
  const date=item.dateISO.split('-');assert.equal(meta.displayMMDD,`${date[1]}/${date[2]}/${date[0]}`);assert.equal(meta.sourceDateSerial,item.date_serial);assert.equal(meta.phaseImage,item.phase_img);assert.equal(meta.phaseName,item.phase_name);assert.equal(meta.providerTilt,item.tilt);assert.equal(meta.illumination,item.illumination);assert.equal(meta.sourceImageURL,`https://moonphases.co.uk/images/moons/big/${item.phase_img}`);assert.equal(actualURL,meta.apiURL);
  const url=new URL(actualURL);assert.equal(url.origin,'https://moonphases.co.uk');assert.equal(url.pathname,'/service/getMoonDetails.php');assert.equal(url.searchParams.get('day'),String(Number(date[2])));assert.equal(url.searchParams.get('month'),String(Number(date[1])));assert.equal(url.searchParams.get('year'),date[0]);assert.equal(url.searchParams.get('lat'),'51.4809181');assert.equal(url.searchParams.get('lng'),'-0.0207105');assert.equal(url.searchParams.get('len'),'1');assert.equal(url.searchParams.get('tz'),'0');assert.equal(url.searchParams.has('hour'),false);assert.equal(actualOptions.mode,'cors');assert.equal(actualOptions.credentials,'omit');assert.equal(actualOptions.redirect,'error');
});
test('date contract is strict ISO downstream of MM/DD, bounds/leap dates checked without swapping',()=>{
  assert.match(moonProviderAPIURL('2024-08-10'),/day=10&month=8&year=2024/);
  for(const value of ['08/10/2024','2024-02-30','1949-12-31','2051-01-01','2023-02-29','2024-8-10'])assert.throws(()=>moonProviderAPIURL(value),error=>error.code==='MOON_PROVIDER_DATE');assert.match(moonProviderAPIURL('2024-02-29'),/day=29&month=2/);
});
test('wrong/missing date_serial, unexpected day count and malicious image leaves are refused',()=>{
  for(const patch of [{date_serial:'20241008'},{date_serial:20240810},{date_serial:null},{phase_img:'../13.png'},{phase_img:'https://evil.example/13.png'},{phase_img:'16.png'},{phase_img:'13.PNG'}])assert.throws(()=>normalizeMoonProviderMetadata(payload({...fixtures[2],...patch}),'2024-08-10'));
  assert.throws(()=>normalizeMoonProviderMetadata({days:[]},'2024-08-10'));assert.throws(()=>normalizeMoonProviderMetadata({days:[fixtures[2],fixtures[2]]},'2024-08-10'));
});
test('phase, illumination and tilt validated; returned object excludes untrusted extras',()=>{
  for(const patch of [{phase:-.1},{phase:1.1},{phase:null},{illumination:101},{illumination:''},{illumination:true},{tilt:181},{tilt:'NaN'},{tilt:null},{phase_name:'<script>wrong</script>'}])assert.throws(()=>normalizeMoonProviderMetadata(payload({...fixtures[2],...patch}),'2024-08-10'));
  const meta=normalizeMoonProviderMetadata(payload({...fixtures[2],phase:'.19',illumination:'30',tilt:'23.9',unknown:'do not copy',imageUrl:'https://evil.example/'}),'2024-08-10');assert.equal(meta.illumination,30);assert.equal(meta.providerTilt,23.9);assert.equal(meta.unknown,undefined);assert.ok(meta.sourceImageURL.startsWith('https://moonphases.co.uk/'));
});
test('CORS/network/HTTP/invalid JSON failures are visible, never analytical fake fallback',async()=>{
  await assert.rejects(fetchMoonProviderMetadata('2024-08-10',{fetchImpl:async()=>{throw new TypeError('Failed to fetch');}}),error=>error.code==='MOON_PROVIDER_NETWORK'&&/CORS/.test(error.message));
  await assert.rejects(fetchMoonProviderMetadata('2024-08-10',{fetchImpl:async()=>new Response('blocked',{status:403})}),error=>error.code==='MOON_PROVIDER_HTTP');
  await assert.rejects(fetchMoonProviderMetadata('2024-08-10',{fetchImpl:async()=>new Response('<html>login</html>')}),error=>error.code==='MOON_PROVIDER_INVALID_RESPONSE');
});
test('already-cancelled calls do not fetch; in-flight ignored fetch is still cancellable',async()=>{
  const stopped=new AbortController();stopped.abort();let called=false;await assert.rejects(fetchMoonProviderMetadata('2024-08-10',{signal:stopped.signal,fetchImpl:async()=>{called=true;}}),error=>error.code==='CANCELLED');assert.equal(called,false);
  const live=new AbortController(),operation=fetchMoonProviderMetadata('2024-08-10',{signal:live.signal,fetchImpl:()=>new Promise(()=>{})});live.abort();await assert.rejects(operation,error=>error.code==='CANCELLED');
});
test('declared and streamed bodies over 2MB are bounded and refused',async()=>{
  await assert.rejects(fetchMoonProviderMetadata('2024-08-10',{fetchImpl:async()=>new Response('{}',{headers:{'Content-Length':String(2*1024*1024+1)}})}),error=>error.code==='MOON_PROVIDER_INVALID_RESPONSE');
  let cancelled=false;const stream=new ReadableStream({start(controller){controller.enqueue(new Uint8Array(2*1024*1024));controller.enqueue(new Uint8Array(1));},cancel(){cancelled=true;}});
  await assert.rejects(fetchMoonProviderMetadata('2024-08-10',{fetchImpl:async()=>new Response(stream)}),error=>error.code==='MOON_PROVIDER_INVALID_RESPONSE');assert.equal(cancelled,true);
});
