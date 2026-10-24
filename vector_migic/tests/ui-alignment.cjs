'use strict';
// Render only the owned local frontend in an isolated headless browser. Mock
// decoded raster/SVG responses separately from the engine's fidelity tests.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {spawnSync}=require('node:child_process');
const playwright=require(path.resolve(path.dirname(process.execPath),'../node_modules/playwright'));
const root=path.resolve(__dirname,'../..'),qa=path.join(root,'build/vector-migic/ui-alignment');
const python=path.join(root,'.work/vector-migic-env/Scripts/python.exe');
const sourceFlag=process.argv.indexOf('--real-source'),vectorFlag=process.argv.indexOf('--real-vector');
const realArgs=sourceFlag>=0&&vectorFlag>=0?[process.argv[sourceFlag+1],process.argv[vectorFlag+1]]:[];
const prepared=spawnSync(python,[path.join(__dirname,'ui-alignment-fixtures.py'),qa,...realArgs],{encoding:'utf8'});
assert.equal(prepared.status,0,prepared.stderr);
const origin='http://migic-ui.test',fixtures=JSON.parse(fs.readFileSync(path.join(qa,'fixtures.json')));
const presets={logo:{colors:16,color_mode:'color',smoothing:.5,speckle:4,transparent:true,mode:'spline',max_dimension:2048,corner_threshold:60,color_precision:6,bw_threshold:128}};
const jobs=new Map(),delays={source:0,vector:0};
let contrast=false;
const baseline=process.argv.includes('--probe-baseline');
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
function content(file){return fs.readFileSync(path.join(root,'vector_migic',file));}
function mime(file){return file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':file.endsWith('.png')?'image/png':file.endsWith('.ttf')?'font/ttf':'text/html';}
(async()=>{
 const browser=await playwright.chromium.launch({executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true});
 const context=await browser.newContext({viewport:{width:1480,height:960}}),page=await context.newPage(),errors=[];
 page.on('pageerror',error=>errors.push(error.message));
 await page.route('**/*',async route=>{
  const request=route.request(),url=new URL(request.url());
  if(url.origin!==origin)return route.abort();
  const json=value=>route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(value)});
  if(url.pathname==='/')return route.fulfill({status:200,contentType:'text/html',body:content('static/index.html')});
  if(url.pathname.startsWith('/static/')||url.pathname.startsWith('/assets/')){
   const filename=url.pathname.split('/').pop();
   const body=baseline&&['app.js','style.css'].includes(filename)?fs.readFileSync(path.join(qa,'baseline-'+filename)):content(url.pathname.slice(1));
   return route.fulfill({status:200,contentType:mime(url.pathname),body});
  }
  if(url.pathname==='/api/config')return json({version:'1.0.1',presets});
  if(url.pathname==='/api/ping')return json({ok:true});
  if(url.pathname==='/api/upload'){
   const filename=decodeURIComponent(request.headers()['x-file-name']);
   return json(fixtures.find(item=>item.name===filename));
  }
  if(url.pathname.startsWith('/api/source/')){
   const id=url.pathname.split('/').pop();await pause(delays.source);
   return route.fulfill({status:200,contentType:'image/png',body:fs.readFileSync(path.join(qa,id+'-preview.png'))});
  }
  if(url.pathname==='/api/vectorize'){
   const body=request.postDataJSON(),id='job-'+body.image_id;
   const job={id,image_id:body.image_id,status:'done',progress:100,options:body.options,
    metrics:{path_count:4,color_count:4,output_bytes:400,elapsed_seconds:.01,warnings:[]}};
   jobs.set(id,job);return json(job);
  }
  if(url.pathname.startsWith('/api/job/'))return json(jobs.get(url.pathname.split('/').pop()));
  if(url.pathname.startsWith('/api/vector/')){
   const id=url.pathname.split('/').pop().replace('job-','');await pause(delays.vector);
   const fixture=fixtures.find(item=>item.id===id),{width,height}=fixture.info;
   const body=contrast?`<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}"><path fill="#a020f0" d="M0 0H${width}V${height}H0Z"/></svg>`:fs.readFileSync(path.join(qa,id+'.svg'));
   return route.fulfill({status:200,contentType:'image/svg+xml',body});
  }
  throw Error('Unexpected request '+url.pathname);
 });
 try{
  await page.goto(origin+'/#token=owned-ui-test');
  await page.waitForFunction(()=>document.querySelector('#status').textContent==='Ready when you are.');
  if(!baseline)assert.equal(await page.locator('.result-foot>span:last-child').textContent(),'v1.0.1');
  await page.locator('#file').setInputFiles(fixtures.map(item=>item.source_path||path.join(qa,item.name)));
  await page.waitForFunction(()=>!document.querySelector('#stage').hidden);
  await page.locator('#vectorize').click();
  await page.waitForFunction(()=>!document.querySelector('[data-view="compare"]').disabled);
  const shots=[];
  async function cameraCase(name){
   if(baseline)return;
   await page.locator('[data-view="original"]').click();
   await page.locator('#source-image').evaluate(image=>image.decode());
   await page.locator('#viewport').screenshot({path:path.join(qa,name+'-original.png')});
   await page.locator('[data-view="vector"]').click();
   const geometry=await page.evaluate(()=>{
    const source=document.querySelector('#source-image').getBoundingClientRect(),vector=document.querySelector('#vector-image').getBoundingClientRect();
    const frame=document.querySelector('#stage').getBoundingClientRect(),viewport=document.querySelector('#viewport').getBoundingClientRect();
    return {source:{x:source.x,y:source.y,width:source.width,height:source.height},vector:{x:vector.x,y:vector.y,width:vector.width,height:vector.height},
      frame:{x:frame.x-viewport.x,y:frame.y-viewport.y,width:frame.width,height:frame.height},
      objectFit:getComputedStyle(document.querySelector('#source-image')).objectFit};
   });
   for(const key of ['x','y','width','height'])assert.ok(Math.abs(geometry.source[key]-geometry.vector[key])<.05,`${name}: ${key} differs`);
   assert.equal(geometry.objectFit,'fill');
   await page.locator('#viewport').screenshot({path:path.join(qa,name+'-vector.png')});
   await page.locator('[data-view="compare"]').click();
   await page.locator('#compare-slider').fill('37');
   const clip=await page.locator('#vector-layer').evaluate(element=>getComputedStyle(element).clipPath);
   assert.match(clip,/inset\(/);
   await page.addStyleTag({content:'#divider,#compare-slider{visibility:hidden!important}'}).then(async style=>{
    await page.locator('#viewport').screenshot({path:path.join(qa,name+'-compare.png')});await style.evaluate(element=>element.remove());
   });
   shots.push({name,frame:geometry.frame,fidelityIndependent:!name.startsWith('actual-')});
  }
  await cameraCase('wide-fit');
  for(let index=0;index<3;index++)await page.locator('#zoom-in').click();
  await cameraCase('wide-zoom');
  const viewport=await page.locator('#viewport').boundingBox();
  await page.mouse.move(viewport.x+viewport.width*.6,viewport.y+viewport.height*.45);await page.mouse.down();
  await page.mouse.move(viewport.x+viewport.width*.6+61,viewport.y+viewport.height*.45-47,{steps:5});await page.mouse.up();
  await cameraCase('wide-pan');
  await page.setViewportSize({width:1024,height:930});await pause(70);
  await cameraCase('wide-resize');
  await page.locator('#fit').click();
  await cameraCase('wide-resize-fit');
  await page.locator('.file-row').nth(1).click();
  await page.waitForFunction(()=>!document.querySelector('#stage').hidden&&document.querySelector('#source-image').naturalHeight===1600);
  await page.locator('#vectorize').click();await page.waitForFunction(()=>!document.querySelector('[data-view="compare"]').disabled);
  await cameraCase('portrait-fit');
  await page.setViewportSize({width:1500,height:900});await pause(70);
  await cameraCase('portrait-resize');
  // A completed job is not sufficient: compare must remain unavailable until
  // this selection's raster and SVG both decode. Never reuse another SVG.
  delays.vector=350;
  await page.locator('.file-row').nth(0).click();
  await page.waitForFunction(()=>!document.querySelector('#stage').hidden&&document.querySelector('#source-image').naturalWidth===1600);
  if(baseline){
   assert.equal(await page.locator('[data-view="compare"]').isDisabled(),false);
   await page.locator('[data-view="compare"]').click();
   const mismatch=await page.evaluate(()=>({source:[document.querySelector('#source-image').naturalWidth,document.querySelector('#source-image').naturalHeight],vector:[document.querySelector('#vector-image').naturalWidth,document.querySelector('#vector-image').naturalHeight],overlayVisible:!document.querySelector('#vector-layer').hidden}));
   assert.deepEqual(mismatch.source,[1600,532]);assert.deepEqual(mismatch.vector,[701,1903]);assert.equal(mismatch.overlayVisible,true);
   fs.writeFileSync(path.join(qa,'baseline-load-race.json'),JSON.stringify({reproduced:true,...mismatch},null,2));
   await page.locator('#viewport').screenshot({path:path.join(qa,'baseline-wrong-overlay.png')});
   console.log(JSON.stringify({baselineWrongOverlayReproduced:true,...mismatch}));return;
  }
  assert.equal(await page.locator('[data-view="compare"]').isDisabled(),true);
  assert.equal(await page.locator('#vector-layer').isVisible(),false);
  await page.waitForFunction(()=>!document.querySelector('[data-view="compare"]').disabled);
  assert.equal(await page.locator('#vector-image').evaluate(image=>image.naturalWidth),3001);
  delays.source=100;delays.vector=120;
  await page.locator('.file-row').nth(1).click();
  await page.locator('.file-row').nth(0).click();
  await page.locator('.file-row').nth(1).click();
  await page.waitForFunction(()=>!document.querySelector('[data-view="compare"]').disabled&&document.querySelector('#vector-image').naturalHeight===1903);
  assert.equal(await page.locator('#source-image').evaluate(image=>image.naturalHeight),1600);
  await cameraCase('rapid-switch-final');
  await page.locator('#speckle').fill('30');await page.locator('#speckle').dispatchEvent('input');
  assert.equal(await page.locator('#speckle-value').textContent(),'900 px²');
  assert.equal(await page.locator('.result-panel').getAttribute('data-result-state'),'stale');
  assert.match(await page.locator('#warnings').textContent(),/last completed result/);
  assert.equal(await page.locator('#vectorize [data-i18n]').textContent(),'Update vector');
  await page.locator('#vectorize').click();await page.waitForFunction(()=>document.querySelector('.result-panel').dataset.resultState==='ready');
  assert.equal(jobs.get('job-portrait').options.speckle,30);
  if(realArgs.length){
   await page.locator('.file-row').nth(2).click();
   await page.waitForFunction(()=>!document.querySelector('#stage').hidden);
   await page.locator('#vectorize').click();await page.waitForFunction(()=>!document.querySelector('[data-view="compare"]').disabled);
   await cameraCase('actual-fit');
   for(let index=0;index<3;index++)await page.locator('#zoom-in').click();
   await cameraCase('actual-zoom');
  }
  contrast=true;delays.source=delays.vector=0;
  await page.locator('.file-row').nth(0).click();
  await page.waitForFunction(()=>!document.querySelector('[data-view="compare"]').disabled&&document.querySelector('#vector-image').naturalWidth===3001);
  for(let index=0;index<3;index++)await page.locator('#zoom-in').click();
  const clipCases=[];
  for(const split of [15,50,85]){
   await page.locator('#compare-slider').fill(String(split));
   const frame=await page.evaluate(()=>{const a=document.querySelector('#stage').getBoundingClientRect(),b=document.querySelector('#viewport').getBoundingClientRect();return {x:a.x-b.x,y:a.y-b.y,width:a.width,height:a.height};});
   await page.addStyleTag({content:'#divider,#compare-slider{visibility:hidden!important}'}).then(async style=>{
    await page.locator('#viewport').screenshot({path:path.join(qa,'clip-'+split+'.png')});await style.evaluate(element=>element.remove());
   });
   clipCases.push({split,frame});
  }
  assert.deepEqual(errors,[]);
  fs.writeFileSync(path.join(qa,'shot-cases.json'),JSON.stringify(shots,null,2));
  fs.writeFileSync(path.join(qa,'clip-cases.json'),JSON.stringify(clipCases,null,2));
  const pixels=spawnSync(python,[path.join(__dirname,'ui-alignment-pixels.py'),qa],{encoding:'utf8'});
  assert.equal(pixels.status,0,pixels.stdout+pixels.stderr);
  const report={ok:true,cases:shots.map(item=>item.name),decodedReadiness:true,rapidSwitch:true,staleSettings:true,speckleAreaUnits:true,
    sourceDimensions:[3001,997],rasterPreviewDimensions:[1600,532],consoleErrors:errors,pixelChecks:JSON.parse(pixels.stdout)};
  fs.writeFileSync(path.join(qa,'verification.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report));
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
