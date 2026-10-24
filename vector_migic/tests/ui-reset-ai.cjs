'use strict';
// Actual owned HTTP server and tracing worker; isolated browser, synthetic art.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {spawn,spawnSync}=require('node:child_process');
const {chromium}=require(path.resolve(path.dirname(process.execPath),'../node_modules/playwright'));
const root=path.resolve(__dirname,'../..'),python=path.join(root,'.work/vector-migic-env/Scripts/python.exe');
const qa=path.join(root,'build/vector-migic/ui-reset-ai');fs.mkdirSync(qa,{recursive:true});
const run=fs.mkdtempSync(path.join(qa,'run-')),infoFile=path.join(run,'session.json');
const prepared=spawnSync(python,['-c',
 'from PIL import Image,ImageDraw; import sys; im=Image.new("RGBA",(160,120)); d=ImageDraw.Draw(im); d.rectangle((10,10,70,100),fill="#1559df"); d.ellipse((45,20,145,110),fill="#e02030"); d.ellipse((70,45,115,85),fill=(0,0,0,0)); im.save(sys.argv[1])',path.join(run,'synthetic.png')],{cwd:root,encoding:'utf8'});
assert.equal(prepared.status,0,prepared.stderr);
const server=spawn(python,['-m','vector_migic.main','--no-browser','--info-json',infoFile,'--idle-timeout','90'],{cwd:root,stdio:'ignore',windowsHide:true});
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
let info,browser;
const expected={preset:'logo',color_mode:'color',colors:0,smoothing:.5,speckle:1,transparent:true,mode:'spline',max_dimension:2048,corner_threshold:60,color_precision:8,bw_threshold:128};
async function controls(page){return page.evaluate(()=>({preset:document.querySelector('#preset').value,color_mode:document.querySelector('#color-mode').value,colors:Number(document.querySelector('#colors').value),smoothing:Number(document.querySelector('#smoothing').value)/100,speckle:Number(document.querySelector('#speckle').value),transparent:document.querySelector('#transparent').checked,mode:document.querySelector('#curve-mode').value,max_dimension:Number(document.querySelector('#max-dimension').value),corner_threshold:Number(document.querySelector('#corner').value),color_precision:Number(document.querySelector('#precision').value),bw_threshold:Number(document.querySelector('#threshold').value)}));}
(async()=>{
 try{
  const deadline=Date.now()+20000;
  while(!fs.existsSync(infoFile)){assert.equal(server.exitCode,null,'HTTP server exited');if(Date.now()>deadline)throw Error('HTTP server startup timeout');await pause(100);}
  info=JSON.parse(fs.readFileSync(infoFile,'utf8'));
  browser=await chromium.launch({channel:'msedge',headless:true});
  const initial=await browser.newContext({javaScriptEnabled:false}),initialPage=await initial.newPage();
  await initialPage.goto(info.origin);assert.deepEqual(await controls(initialPage),expected,'Initial HTML must agree before config loads');await initial.close();
  const context=await browser.newContext({viewport:{width:1480,height:1000},acceptDownloads:true});
  const page=await context.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
  await page.route('**/*',route=>route.request().url().startsWith(info.origin)?route.continue():route.abort());
  await page.goto(info.url);await page.waitForFunction(()=>document.querySelector('#status').textContent==='Ready when you are.');
  const config=await (await context.request.get(info.origin+'/api/config',{headers:{'X-Migic-Token':info.token}})).json();
  assert.deepEqual(config.presets.logo,{...expected,alpha_threshold:128});
  assert.deepEqual(await controls(page),expected);
  await page.locator('#advanced').evaluate(element=>element.open=true);
  await page.locator('#preset').selectOption('photo');
  await page.locator('#color-mode').selectOption('bw');await page.locator('#curve-mode').selectOption('polygon');
  await page.locator('#max-dimension').selectOption('4096');await page.locator('#threshold').fill('211');
  await page.locator('#colors').selectOption('8');await page.locator('#smoothing').fill('80');
  await page.locator('#speckle').fill('30');await page.locator('#precision').fill('2');
  await page.locator('#corner').fill('120');await page.locator('#transparent').uncheck();
  assert.equal((await controls(page)).color_mode,'bw');assert.equal((await controls(page)).bw_threshold,211);
  await page.locator('#reset').click();assert.deepEqual(await controls(page),expected,'Reset must restore all advanced options');
  assert.equal(await page.locator('#speckle-value').textContent(),'1 px²');
  assert.equal(await page.locator('#precision-value').textContent(),'8');
  assert.equal(await page.locator('#smoothing-value').textContent(),'50%');
  await page.locator('#file').setInputFiles([path.join(run,'synthetic.png'),path.join(run,'synthetic.png')]);
  await page.waitForFunction(()=>document.querySelectorAll('.file-row').length===2&&!document.querySelector('#vectorize').disabled);
  const jobResponse=page.waitForResponse(response=>response.url()===info.origin+'/api/vectorize'&&response.request().method()==='POST');
  await page.locator('#vectorize').click();const job=await (await jobResponse).json();
  assert.deepEqual(job.options,config.presets.logo,'The actual worker must receive normalized Reset defaults');
  await page.waitForFunction(()=>document.querySelector('#status').textContent==='Vector ready. Compare or export your result.',null,{timeout:60000});
  const ai=page.locator('[data-export="ai"]');assert.equal(await ai.isEnabled(),true);
  assert.match(await ai.getAttribute('title'),/Illustrator 8-compatible/);
  const downloadPending=page.waitForEvent('download');await ai.click();const download=await downloadPending;
  assert.equal(download.suggestedFilename(),'synthetic.ai');await download.saveAs(path.join(run,'synthetic.ai'));
  const data=fs.readFileSync(path.join(run,'synthetic.ai'));assert.ok(data.subarray(0,14).toString()==='%!PS-Adobe-3.0');
  assert.match(data.toString('ascii'),/%AI5_FileFormat 4\.0/);assert.doesNotMatch(data.subarray(0,80).toString(),/EPSF|PDF/);
  await page.locator('#batch').click();
  await page.waitForFunction(()=>[...document.querySelectorAll('.file-row small')].every(element=>element.textContent==='Ready'),null,{timeout:60000});
  await page.locator('#batch-format').selectOption('AI');assert.equal(await page.locator('#batch-format').inputValue(),'AI');
  const batchPending=page.waitForEvent('download');await page.locator('#download-all').click();const batch=await batchPending;
  assert.equal(batch.suggestedFilename(),'VectorMigic-ai.zip');await batch.saveAs(path.join(run,'batch-ai.zip'));
  const checked=spawnSync(python,['-c','import sys,zipfile; z=zipfile.ZipFile(sys.argv[1]); names=z.namelist(); assert len(names)==len(set(names))==2; assert all(n.endswith(".ai") and b"%AI5_FileFormat 4.0" in z.read(n) for n in names)',path.join(run,'batch-ai.zip')],{encoding:'utf8'});
  assert.equal(checked.status,0,checked.stderr);
  await page.locator('#language').selectOption('vi');assert.match(await ai.textContent(),/Lưu AI/);assert.match(await ai.getAttribute('title'),/Illustrator 8/);
  await page.locator('#language').selectOption('en');assert.match(await ai.textContent(),/Save AI/);
  await page.screenshot({path:path.join(run,'reset-ai-result.png')});assert.deepEqual(errors,[]);
  const result={ok:true,initialHTMLDefaults:true,completeHTTPPresets:true,resetAllAdvancedSettings:true,actualWorkerDefaults:job.options,aiDownload:true,aiEnglishVietnamese:true,batchAIExport:true,consoleErrors:errors};
  fs.writeFileSync(path.join(run,'verification.json'),JSON.stringify(result,null,2));console.log(JSON.stringify({path:run,...result}));
 }finally{
  if(browser)await browser.close();
  if(info){try{await fetch(info.origin+'/api/shutdown',{method:'POST',headers:{'X-Migic-Token':info.token,'Content-Type':'application/json'},body:'{}'});}catch{}}
  const deadline=Date.now()+5000;while(server.exitCode===null&&Date.now()<deadline)await pause(100);
  if(server.exitCode===null)throw Error('Owned test server did not exit after authenticated shutdown');
 }
})().catch(error=>{console.error(error);process.exitCode=1;});
