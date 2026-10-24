'use strict';
// Owned local app, new headless Edge context; no user browser/profile access.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const playwright=require(path.resolve(path.dirname(process.execPath),'../node_modules/playwright'));
const root=path.resolve(__dirname,'../..'),qa=path.join(root,'build/vector-migic/qa');
const info=JSON.parse(fs.readFileSync(path.join(qa,'server.json')));
(async()=>{
 const browser=await playwright.chromium.launch({executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true});
 try{
  const context=await browser.newContext({viewport:{width:1480,height:960},acceptDownloads:true});
  const page=await context.newPage(),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/*',route=>route.request().url().startsWith(info.origin)?route.continue():route.abort());
  await page.goto(info.url);await page.waitForFunction(()=>document.querySelector('#status').textContent==='Ready when you are.');
  assert.equal(await page.locator('#language').inputValue(),'en');
  await page.screenshot({path:path.join(qa,'vector-migic-empty.png')});
  await page.locator('#file').setInputFiles([path.join(qa,'shapes.png'),path.join(root,'vector_migic/assets/icon.png')]);
  await page.waitForFunction(()=>document.querySelectorAll('.file-row').length===2);
  await page.locator('#vectorize').click();
  await page.waitForFunction(()=>document.querySelector('#status').textContent==='Vector ready. Compare or export your result.',null,{timeout:60000});
  assert.ok(Number((await page.locator('#stat-paths').textContent()).replace(/,/g,''))>0);
  assert.equal(await page.locator('[data-view="compare"]').getAttribute('class'),'active');
  await page.locator('#compare-slider').fill('75');
  await page.locator('#zoom-in').click();await page.locator('#fit').click();
  await page.screenshot({path:path.join(qa,'vector-migic-result.png')});
  const formats=[];
  for(const format of ['svg','pdf','eps']){
   const pending=page.waitForEvent('download');await page.locator(`[data-export="${format}"]`).click();
   const download=await pending;const destination=path.join(qa,'export.'+format);await download.saveAs(destination);
   const data=fs.readFileSync(destination);assert.ok(data.length>100);
   if(format==='svg'){assert.match(data.toString(),/<path/);assert.doesNotMatch(data.toString(),/<image/);}
   if(format==='pdf')assert.ok(data.subarray(0,5).toString()==='%PDF-');
   if(format==='eps')assert.ok(data.subarray(0,4).toString()==='%!PS');
   formats.push(format);
  }
  await page.locator('#language').selectOption('vi');assert.equal(await page.locator('html').getAttribute('lang'),'vi');
  assert.match(await page.locator('#vectorize').textContent(),/Chuyển sang vector/);
  await page.locator('#language').selectOption('en');
  await page.locator('#batch').click();
  await page.waitForFunction(()=>[...document.querySelectorAll('.file-row small')].every(x=>x.textContent==='Ready'),null,{timeout:60000});
  assert.equal(await page.locator('#batch-export').isVisible(),true);
  const pending=page.waitForEvent('download');await page.locator('#download-all').click();const batch=await pending;await batch.saveAs(path.join(qa,'batch.zip'));
  await page.setViewportSize({width:700,height:920});await page.screenshot({path:path.join(qa,'vector-migic-narrow.png')});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  assert.deepEqual(errors,[]);
  const unauth=await context.request.get(info.origin+'/api/config');assert.equal(unauth.status(),403);
  const denied=await context.request.get(info.origin+'/api/config',{headers:{'X-Migic-Token':info.token,Origin:'https://unrelated.invalid'}});assert.equal(denied.status(),403);
  const bad=await context.request.post(info.origin+'/api/vectorize',{headers:{'X-Migic-Token':info.token},data:{image_id:'../../outside',options:{}}});assert.equal(bad.status(),400);
  const upload=await context.request.post(info.origin+'/api/upload',{headers:{'X-Migic-Token':info.token,'X-File-Name':'cancel.png','Content-Type':'image/png'},data:fs.readFileSync(path.join(qa,'shapes.png'))});const uploaded=await upload.json();
  const start=await context.request.post(info.origin+'/api/vectorize',{headers:{'X-Migic-Token':info.token},data:{image_id:uploaded.id,options:{preset:'photo',colors:256,max_dimension:4096}}});const job=await start.json();
  const cancel=await context.request.post(info.origin+'/api/cancel',{headers:{'X-Migic-Token':info.token},data:{job_id:job.id}});assert.equal((await cancel.json()).status,'cancelled');
  const result={ok:true,sourceUpload:true,realWorker:true,formats,batch:true,cancellation:true,englishDefault:true,vietnameseSwitch:true,responsive:true,consoleErrors:errors,authAndOrigin:true};
  fs.writeFileSync(path.join(qa,'ui-verification.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result));
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
