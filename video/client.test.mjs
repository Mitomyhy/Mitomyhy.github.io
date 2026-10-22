import test from 'node:test';
import assert from 'node:assert/strict';
import {buildVideoPayload,parseVideoResponse,requestVideo,validateEndpoint} from './client.mjs';

test('manual prompt stays unchanged and optional photo selects image flow',()=>{
  const prompt='Keep the printed names unchanged. Slow clockwise orbit, soft daylight.';
  assert.deepEqual(buildVideoPayload({prompt,aspectRatio:'9:16'}),{prompt,aspect_ratio:'9:16'});
  assert.deepEqual(buildVideoPayload({prompt,imageBase64:'data:image/png;base64,aGVsbG8=',imageName:'photo.png'}),{prompt,aspect_ratio:'16:9',imageBase64:'data:image/png;base64,aGVsbG8=',imageName:'photo.png'});
  assert.equal(buildVideoPayload({prompt,imageUrl:'https://example.com/product.png'}).imageUrl,'https://example.com/product.png');
});
test('bad input cannot trigger a generation',()=>{
  for(const value of [{prompt:''},{prompt:'x'.repeat(5001)},{prompt:'ok',aspectRatio:'1:1'},{prompt:'ok',imageUrl:'file:///private.png'},{prompt:'ok',imageBase64:'data:text/html;base64,aA=='}])assert.throws(()=>buildVideoPayload(value));
  for(const url of ['javascript:alert(1)','http://public.example/webhook','https://user:password@example.com/webhook'])assert.throws(()=>validateEndpoint(url));
  assert.equal(validateEndpoint('http://localhost:5678/webhook/test'),'http://localhost:5678/webhook/test');
});
test('pending task, successful video and provider errors are distinct, including HTTP200 body errors',()=>{
  assert.deepEqual(parseVideoResponse({code:200,data:{taskId:'job-42'}}),{state:'pending',taskId:'job-42'});
  assert.deepEqual(parseVideoResponse({code:200,data:{taskId:'job-42',state:'success',resultJson:JSON.stringify({resultUrls:['https://cdn.example.com/video.mp4']})}}),{state:'success',taskId:'job-42',videoUrl:'https://cdn.example.com/video.mp4'});
  assert.throws(()=>parseVideoResponse({code:402,msg:'Insufficient credits'}),/Insufficient credits/);
  assert.throws(()=>parseVideoResponse({code:200,data:{taskId:'job-42',state:'fail',failMsg:'Generation rejected'}}),/Generation rejected/);
  assert.throws(()=>parseVideoResponse({code:200,data:{taskId:'job-42',state:'success',resultJson:'{}'}}),/without a download URL/);
  assert.throws(()=>parseVideoResponse({code:200,data:{resultUrls:['javascript:alert(1)']}}),/unsafe/);
});
test('client submits once with session webhook token, then polls existing task without re-generating',async()=>{
  const calls=[];
  const fetchFn=async(url,options)=>{calls.push({url,...options});return{ok:true,json:async()=>calls.length===1?{code:200,data:{taskId:'job-42'}}:{code:200,data:{taskId:'job-42',state:'success',resultJson:'{"resultUrls":["https://cdn.example.com/video.mp4"]}'}}};};
  const connection='https://n8n.example.com/webhook/';
  const pending=await requestVideo(connection+'mitom-video-generate',buildVideoPayload({prompt:'Show my ornament'}),'session-token',{fetchFn});
  const complete=await requestVideo(connection+'mitom-video-status',{taskId:pending.taskId},'session-token',{fetchFn});
  assert.equal(calls.length,2);assert.equal(calls[0].headers['X-MITOM-Token'],'session-token');assert.deepEqual(JSON.parse(calls[1].body),{taskId:'job-42'});assert.equal(complete.state,'success');assert.equal(calls[0].credentials,'omit');
});
test('failed transport is never retried implicitly or reported as a generated video',async()=>{
  let calls=0;
  await assert.rejects(requestVideo('https://n8n.example.com/webhook/generate',{prompt:'hello'},'',{fetchFn:async()=>{calls++;throw new TypeError('Failed to fetch');}}),/Failed to fetch/);assert.equal(calls,1);
  await assert.rejects(requestVideo('https://n8n.example.com/webhook/generate',{},'',{fetchFn:async()=>({ok:false,status:401})}),/HTTP 401/);
});
