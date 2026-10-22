import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';

const workflow=JSON.parse(await readFile(new URL('./MITOM-Product-Video.json',import.meta.url),'utf8'));
const nodes=new Map(workflow.nodes.map(node=>[node.name,node]));
const AsyncFunction=Object.getPrototypeOf(async function(){}).constructor;
const png='data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8/5+hHgAHggJ/PchI7wAAAABJRU5ErkJggg==';
const success={code:200,msg:'success',data:{taskId:'veo_task_123'}};
function expression(value,json){if(typeof value!=='string'||!value.startsWith('='))return value;return new Function('$json','return ('+value.slice(1).trim().replace(/^{{|}}$/g,'')+');')(json);}
async function execute(trigger,body,provider=async()=>({statusCode:200,body:success})){
  let name=trigger,json={body},steps=0;const seen=new Map(),requests=[];
  while(name){assert.ok(++steps<30,'workflow path terminates');const node=nodes.get(name);assert.ok(node,name);let output=0;
    if(node.type.endsWith('.code')){const result=await new AsyncFunction('$input','$','Buffer','URL',node.parameters.jsCode)({first:()=>({json})},lookup=>({first:()=>({json:seen.get(lookup)})}),Buffer,URL);assert.equal(result.length,1);json=result[0].json;}
    else if(node.type.endsWith('.if')){output=expression(node.parameters.conditions.conditions[0].leftValue,json)?0:1;}
    else if(node.type.endsWith('.httpRequest')){const request={url:node.parameters.url,method:node.parameters.method,body:expression(node.parameters.jsonBody,json),query:Object.fromEntries((node.parameters.queryParameters?.parameters||[]).map(row=>[row.name,expression(row.value,json)]))};requests.push(request);json=await provider(request);}
    else if(node.type.endsWith('.respondToWebhook'))return{status:expression(node.parameters.options.responseCode,json),body:expression(node.parameters.responseBody,json),requests};
    seen.set(name,json);
    const next=workflow.connections[name]?.main?.[output]||[];assert.equal(next.length,1,'each executed branch has one destination');name=next[0].node;
  }
  throw Error('Workflow did not respond');
}
test('sanitized inactive export has two protected POST triggers and complete graph',()=>{
  assert.equal(workflow.active,false);assert.deepEqual(workflow.pinData,{});
  assert.equal(workflow.settings.saveDataSuccessExecution,'none');assert.equal(workflow.settings.saveDataErrorExecution,'none');
  const triggers=workflow.nodes.filter(node=>node.type.endsWith('.webhook'));assert.equal(triggers.length,2);
  assert.deepEqual(triggers.map(node=>node.parameters.path),['mitom-video-generate','mitom-video-status']);
  for(const trigger of triggers){assert.equal(trigger.parameters.httpMethod,'POST');assert.equal(trigger.parameters.authentication,'headerAuth');assert.equal(trigger.parameters.responseMode,'responseNode');assert.equal(trigger.parameters.options.allowedOrigins,'https://mitomyhy.github.io');}
  for(const node of workflow.nodes){assert.equal(node.credentials,undefined);if(node.type.endsWith('.httpRequest')){assert.equal(node.parameters.authentication,'genericCredentialType');assert.equal(node.parameters.genericAuthType,'httpHeaderAuth');assert.equal(node.retryOnFail,false);assert.ok(node.parameters.url.startsWith('https://api.kie.ai/'));}for(const branch of workflow.connections[node.name]?.main||[])for(const edge of branch)assert.ok(nodes.has(edge.node));}
});
test('prompt only creates one current Veo Fast task and returns raw provider body',async()=>{
  const result=await execute('Generate webhook',{prompt:'  Slow orbit around the ornament.  ',aspect_ratio:'16:9'});
  assert.equal(result.status,200);assert.deepEqual(result.body,success);assert.equal(result.requests.length,1);
  assert.deepEqual(result.requests[0],{url:'https://api.kie.ai/api/v1/jobs/createTask',method:'POST',query:{},body:{model:'veo-3-1',input:{prompt:'Slow orbit around the ornament.',model:'veo3_fast',aspect_ratio:'16:9',resolution:'720p',duration:8,generation_type:'TEXT_2_VIDEO'}}});
});
test('HTTPS reference image skips upload and becomes one first frame',async()=>{
  const result=await execute('Generate webhook',{prompt:'Slow orbit',aspect_ratio:'9:16',imageUrl:'https://images.example/product.png'});
  assert.equal(result.requests.length,1);assert.deepEqual(result.requests[0].body.input.image_urls,['https://images.example/product.png']);assert.equal(result.requests[0].body.input.generation_type,'FIRST_AND_LAST_FRAMES_2_VIDEO');
});
test('local product image uploads once then uses returned HTTPS URL',async()=>{
  const result=await execute('Generate webhook',{prompt:'Slow orbit',imageBase64:png,imageName:'../../my product.png'},async request=>({statusCode:200,body:request.url.endsWith('file-base64-upload')?{success:true,code:200,data:{downloadUrl:'https://tempfile.example/new-product.png'}}:success}));
  assert.equal(result.requests.length,2);assert.equal(result.requests[0].body.base64Data,png);assert.equal(result.requests[0].body.uploadPath,'mitom/products');assert.match(result.requests[0].body.fileName,/^my-product-\d+-[a-z0-9]+\.png$/);
  assert.deepEqual(result.requests[1].body.input.image_urls,['https://tempfile.example/new-product.png']);
});
test('invalid user inputs never reach a provider',async()=>{
  for(const body of [null,[],{prompt:''},{prompt:'x'.repeat(5001)},{prompt:'orbit',aspect_ratio:'1:1'},{prompt:'orbit',imageUrl:'http://images.example/product.png'},{prompt:'orbit',imageUrl:'https://user:pass@images.example/a.png'},{prompt:'orbit',imageUrl:'https://images.example/a.png',imageBase64:png},{prompt:'orbit',imageBase64:'data:image/svg+xml;base64,PHN2Zz4='},{prompt:'orbit',imageBase64:'data:image/png;base64,YWJjZA=='},{prompt:'orbit',imageBase64:'data:image/png;base64,'+'A'.repeat(7*1024*1024)}]){
    const result=await execute('Generate webhook',body);assert.equal(result.status,400);assert.equal(result.requests.length,0);
  }
});
test('upload provider errors inside HTTP200 stop before paid task creation',async()=>{
  const error={code:402,msg:'Insufficient credits',success:false,data:null};
  const result=await execute('Generate webhook',{prompt:'orbit',imageBase64:png},async()=>({statusCode:200,body:error}));
  assert.equal(result.status,502);assert.deepEqual(result.body,error);assert.equal(result.requests.length,1);
  const missing=await execute('Generate webhook',{prompt:'orbit',imageBase64:png},async()=>({statusCode:200,body:{code:200,success:true,data:{}}}));assert.equal(missing.status,502);assert.equal(missing.requests.length,1);
});
test('create provider code failure is preserved and marked HTTP502',async()=>{
  const error={code:429,msg:'Rate limit',data:null};const result=await execute('Generate webhook',{prompt:'orbit'},async()=>({statusCode:200,body:error}));
  assert.equal(result.status,502);assert.deepEqual(result.body,error);assert.equal(result.requests.length,1);
});
test('malformed/transport failures produce safe JSON without execution internals',async()=>{
  for(const output of [{error:{message:'private network diagnostics'}},{statusCode:200,body:'not JSON'}]){
    const result=await execute('Generate webhook',{prompt:'orbit'},async()=>output);assert.equal(result.status,502);assert.equal(result.body.code,502);assert.doesNotMatch(JSON.stringify(result.body),/private network diagnostics/);
  }
});
test('status queries existing task only and returns success or fail state unchanged',async()=>{
  for(const state of ['waiting','queuing','generating','success','fail']){
    const body={code:200,msg:'success',data:{taskId:'veo_task_123',state,resultJson:state==='success'?'{"resultUrls":["https://video.example/product.mp4"]}':null,failMsg:state==='fail'?'Provider failed':''}};
    const result=await execute('Status webhook',{taskId:'veo_task_123'},async()=>({statusCode:200,body}));
    assert.equal(result.status,200);assert.deepEqual(result.body,body);assert.deepEqual(result.requests,[{url:'https://api.kie.ai/api/v1/jobs/recordInfo',method:'GET',body:undefined,query:{taskId:'veo_task_123'}}]);
  }
});
test('invalid status identifiers stop before a provider query',async()=>{
  for(const taskId of ['',null,42,'../other?x=1','x'.repeat(201)]){const result=await execute('Status webhook',{taskId});assert.equal(result.status,400);assert.equal(result.requests.length,0);}
});

