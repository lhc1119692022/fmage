import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile,readFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,dirname} from 'node:path';
import {createWorkflowService,validateInputs,imageExtension} from '../mcp/workflows.mjs';

const png=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl6Z8sAAAAASUVORK5CYII=','base64');
async function fixture(t,options={}) {
  const dir=await mkdtemp(join(tmpdir(),'fmage-workflow-test-'));
  t.after(()=>rm(dir,{recursive:true,force:true}));
  const config=JSON.parse(await readFile(new URL('../config/workflows.example.json',import.meta.url),'utf8'));
  config.workflow_connections.runninghub.api_key='test-only-secret';
  const path=join(dir,'providers.json');await writeFile(path,JSON.stringify(config));
  const image=join(dir,'input.png');await writeFile(image,png);
  const calls=[];let failDownload=Boolean(options.failDownload);
  const fetchImpl=async(url,init)=>{
    const endpoint=String(url).split('/').at(-1);calls.push({endpoint,init});
    if(endpoint==='create'&&options.uncertain) throw new Error('network secret');
    if(endpoint==='image2'&&failDownload) {failDownload=false;return new Response('failed',{status:503});}
    if(endpoint.startsWith('image')) {assert.equal(init.headers,undefined);return new Response(options.html?'<!doctype html>':png);}
    const outputs=[{nodeId:'16',fileUrl:'https://cdn.example/image-preview'},{nodeId:'170',fileUrl:'https://cdn.example/image1'},{nodeId:'170',fileUrl:'https://cdn.example/image2'}];
    const data={upload:{fileName:'api/input.png'},create:{taskId:'123456'},status:options.status||'SUCCESS',outputs:options.missing?[]:outputs}[endpoint];
    return Response.json({code:0,data});
  };
  let clock=0;
  const runtime={fetchImpl,now:()=>clock,sleep:async ms=>{clock+=ms;}};
  return {call:createWorkflowService(path,runtime),restart:()=>createWorkflowService(path,runtime),calls,image,dir,path};
}
test('download all configured outputs, skip previews, preserve original bytes and prevent duplicate submission',async t=>{
  const f=await fixture(t);const args={operation:'run',request_id:'job',inputs:{image:f.image,resolution_k:6}};
  assert.equal((await f.call(args)).task_id,'123456');
  await f.restart()(args);
  const body=JSON.parse(f.calls.find(c=>c.endpoint==='create').init.body);
  assert.deepEqual(body.nodeInfoList,[{nodeId:'169',fieldName:'image',fieldValue:'api/input.png'},{nodeId:'134',fieldName:'value',fieldValue:'6'}]);
  const result=await f.restart()({operation:'status',request_id:'job'});
  assert.equal(result.status,'completed');assert.equal(result.files.length,2);
  assert.deepEqual(await readFile(result.files[0].path),png);
  assert.equal(dirname(result.files[0].path),join(f.dir,'outputs','workflows'));
  assert.equal(result.files[0].width,1);assert.equal(result.files[0].height,1);
  assert.equal(result.files[0].format,'png');
  assert.equal(f.calls.filter(c=>c.endpoint==='create').length,1);
  assert(!f.calls.some(c=>c.endpoint==='image-preview'));
  assert(!(await readFile(join(f.dir,'workflow-tasks','job.json'),'utf8')).includes('test-only-secret'));
});
test('partial download can resume after restart without re-running or downloading successful files',async t=>{
  const f=await fixture(t,{failDownload:true});await f.call({operation:'run',request_id:'partial',inputs:{image:f.image}});
  const a=await f.call({operation:'status',request_id:'partial'});assert.equal(a.status,'download_incomplete');assert.equal(a.files.length,1);
  const b=await f.restart()({operation:'status',request_id:'partial'});assert.equal(b.status,'completed');assert.equal(b.files.length,2);
  assert.equal(f.calls.filter(c=>c.endpoint==='create').length,1);assert.equal(f.calls.filter(c=>c.endpoint==='image1').length,1);
});
test('uncertain create never automatically resubmits, and errors do not leak network details',async t=>{
  const f=await fixture(t,{uncertain:true});const a={operation:'run',request_id:'uncertain',inputs:{image:f.image}};
  const result=await f.call(a);assert.equal(result.status,'submission_unknown');assert(!JSON.stringify(result).includes('network secret'));
  await f.restart()(a);assert.equal(f.calls.filter(c=>c.endpoint==='create').length,1);
});
test('dry-run and list make no network requests; switching persists without altering connection',async t=>{
  const f=await fixture(t);assert.equal((await f.call({operation:'run',request_id:'dry',inputs:{image:f.image},dry_run:true})).status,'validated');
  const listed=await f.call({operation:'list'});assert(!JSON.stringify(listed).includes('test-only-secret'));
  await f.call({operation:'set_default',workflow:'SeedVR2 放大'});assert.equal(f.calls.length,0);
  assert.equal(JSON.parse(await readFile(f.path,'utf8')).workflow_connections.runninghub.api_key,'test-only-secret');
});
test('missing outputs and HTML downloads cannot report completion',async t=>{
  for(const options of [{missing:true},{html:true}]) {
    const f=await fixture(t,options);await f.call({operation:'run',request_id:'invalid',inputs:{image:f.image}});
    const result=await f.call({operation:'status',request_id:'invalid'});assert.notEqual(result.status,'completed');assert(result.errors.length);assert.equal(result.files.length,0);
  }
});
test('pending/failed tasks never download and input validation precedes submission',async t=>{
  for(const status of ['QUEUED','RUNNING','FAILED']) {
    const f=await fixture(t,{status});await f.call({operation:'run',request_id:'pending',inputs:{image:f.image}});
    assert.equal((await f.call({operation:'status',request_id:'pending'})).status,status);
    assert(!f.calls.some(c=>c.endpoint==='outputs'));
    assert.equal(f.calls.filter(c=>c.endpoint==='status').length,status==='FAILED'?1:5);
  }
  assert.throws(()=>validateInputs({inputs:{}},{unknown:1}),/Unknown/);
  assert.throws(()=>imageExtension(Buffer.from('html')),/Expected/);
  const f=await fixture(t);await assert.rejects(f.call({operation:'run',request_id:'bad',inputs:{image:f.image,resolution_k:10}}),/range/);assert.equal(f.calls.length,0);
});
