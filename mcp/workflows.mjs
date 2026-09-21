import { readFile, writeFile, mkdir, rename, stat } from 'node:fs/promises';
import { dirname, join, resolve, basename, isAbsolute } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
export const workflowTools = [{name:'workflow_image',title:'Fmage 工作流',description:'List/select image workflows, submit once, or poll and download. Reuse request_id only for the same job. Never resubmit failed or uncertain tasks.',inputSchema:{type:'object',additionalProperties:false,required:['operation'],properties:{operation:{type:'string',enum:['list','set_default','run','status']},workflow:{type:'string'},inputs:{type:'object'},request_id:{type:'string'},task_id:{type:'string',description:'Status only: verified task ID for uncertain submission recovery.'},output_dir:{type:'string'},dry_run:{type:'boolean'}}}}];
export function imageExtension(b) {
  if(b.length>=24 && b.subarray(0,8).equals(Buffer.from([137,80,78,71,13,10,26,10]))) return 'png';
  if(b.length>=4 && b[0]===255 && b[1]===216 && b[2]===255) return 'jpg';
  if(b.length>=16 && b.toString('ascii',0,4)==='RIFF' && b.toString('ascii',8,12)==='WEBP') return 'webp';
  throw new Error('Expected PNG, JPEG or WebP image data.');
}
export function validateInputs(w,inputs={}) {
  const specs=w.inputs||{};
  for(const name of Object.keys(inputs)) if(!Object.hasOwn(specs,name)) throw new Error(`Unknown input: ${name}`);
  return Object.entries(specs).flatMap(([name,spec])=>{
    const value=inputs[name]??spec.default;
    if(value===undefined) {if(spec.required) throw new Error(`Required input: ${name}`); return [];}
    if(!spec.node_id || !spec.field_name) throw new Error(`Missing mapping: ${name}`);
    if(!['image','string','number','boolean'].includes(spec.type)) throw new Error(`Unsupported type: ${name}`);
    if(typeof value!==(spec.type==='image'?'string':spec.type)) throw new Error(`Invalid type: ${name}`);
    if(spec.type==='number' && (!Number.isFinite(value)||(spec.min!==undefined&&value<spec.min)||(spec.max!==undefined&&value>spec.max))) throw new Error(`Out of range: ${name}`);
    if(spec.type==='image'&&!isAbsolute(value)) throw new Error(`Image must use absolute path: ${name}`);
    return [{spec,value}];
  });
}
export function createWorkflowService(configPath,{fetchImpl=fetch,sleep=delay,now=Date.now,waitMs=40000}={}) {
  const root=join(dirname(configPath),'workflow-tasks');
  const load=async p=>JSON.parse((await readFile(p,'utf8')).replace(/^\uFEFF/,''));
  async function save(p,data) {
    await mkdir(dirname(p),{recursive:true}); const tmp=p+'.'+crypto.randomUUID()+'.tmp';
    await writeFile(tmp,JSON.stringify(data,null,2)+'\n',{mode:0o600}); await rename(tmp,p);
  }
  function connection(config,name) {
    const c=config.workflow_connections?.[name]; if(!c) throw new Error('Missing workflow connection.');
    const u=new URL(c.base_url);
    if(u.protocol!=='https:'||!['www.runninghub.cn','www.runninghub.ai'].includes(u.hostname)||u.username||u.password||u.port||u.pathname!=='/'||u.search||u.hash) throw new Error('Invalid RunningHub base_url.');
    return {base:u.origin,key:(c.api_key_env?process.env[c.api_key_env]:c.api_key)||''};
  }
  async function api(c,endpoint,body) {
    if(!c.key) throw new Error('Set RunningHub API key locally in workflow_connections.');
    let r;
    try {r=await fetchImpl(c.base+'/task/openapi/'+endpoint,{method:'POST',redirect:'error',signal:AbortSignal.timeout(60000),headers:{Authorization:'Bearer '+c.key,...(body instanceof FormData?{}:{'Content-Type':'application/json'})},body:body instanceof FormData?body:JSON.stringify({...body,apiKey:c.key})});}
    catch {throw new Error(`RunningHub ${endpoint}: network error or timeout.`);}
    if(!r.ok) throw new Error(`RunningHub ${endpoint}: HTTP ${r.status}`);
    let d;try{d=await r.json();}catch{throw new Error('Invalid API response.');}
    if(d.code!==0) throw new Error(`RunningHub ${endpoint}: error code ${Number(d.code)}`);
    return d.data;
  }
  const summary=s=>({request_id:s.request_id,task_id:s.task_id,workflow:s.workflow,status:s.status,files:s.files||[],errors:s.errors||[]});
  const locks=new Set();
  return async function call(args) {
    const config=await load(configPath);
    if(args.operation==='list') return {active_workflow:config.active_workflow,workflows:Object.entries(config.workflows||{}).map(([name,w])=>({name,workflow_id:w.workflow_id,description:w.description,inputs:w.inputs,output_node_ids:w.output_node_ids,api_key_configured:Boolean(connection(config,w.connection).key)}))};
    if(args.operation==='set_default') {
      if(!config.workflows?.[args.workflow]) throw new Error('Unknown workflow.');
      config.active_workflow=args.workflow;await save(configPath,config);return {active_workflow:args.workflow};
    }
    if(!['run','status'].includes(args.operation)) throw new Error('Unknown operation.');
    if(!/^[a-zA-Z0-9_-]{1,100}$/.test(args.request_id||'')) throw new Error('Supply stable request_id using letters, numbers, underscores or hyphens.');
    const path=join(root,args.request_id+'.json');
    if(locks.has(path)) throw new Error('Request already processing; query status later.');
    locks.add(path);
    try {
      let state;try{state=await load(path);}catch(e){if(e.code!=='ENOENT') throw e;}
      if(args.operation==='run') {
        if(state) return summary(state);
        const name=args.workflow||config.active_workflow;const w=config.workflows?.[name];
        if(!w||!/^\d+$/.test(w.workflow_id||'')) throw new Error('Configure valid workflow_id.');
        if(!Array.isArray(w.output_node_ids)||!w.output_node_ids.length||w.output_node_ids.some(id=>typeof id!=='string'||!/^\d+$/.test(id))) throw new Error('Configure output_node_ids explicitly.');
        if(w.instance_type && !['default','plus','ultra'].includes(w.instance_type)) throw new Error('Invalid instance_type.');
        const c=connection(config,w.connection);const entries=validateInputs(w,args.inputs);
        for(const e of entries) if(e.spec.type==='image') {
          if((await stat(e.value)).size>30*1024*1024) throw new Error('Input exceeds 30 MB.');
          e.bytes=await readFile(e.value);imageExtension(e.bytes);
        }
        if(args.dry_run) return {status:'validated',workflow:name,workflow_id:w.workflow_id,output_node_ids:w.output_node_ids,api_key_configured:Boolean(c.key)};
        if(!c.key) throw new Error('Set RunningHub API key locally in workflow_connections.');
        state={request_id:args.request_id,workflow:name,connection:w.connection,base_url:c.base,output_node_ids:w.output_node_ids,output_dir:resolve(args.output_dir||config.output_dir||join(dirname(configPath),'outputs','workflows')),status:'preparing',files:[]};
        await mkdir(root,{recursive:true});
        // Exclusive reservation prevents duplicate paid submissions across MCP processes.
        await writeFile(path,JSON.stringify(state),{flag:'wx',mode:0o600});
        try {
          const nodes=[];
          for(const e of entries) {
            let value=e.value;
            if(e.bytes) {
              const form=new FormData();form.set('apiKey',c.key);form.set('fileType','input');form.set('file',new Blob([e.bytes]),basename(e.value));
              value=(await api(c,'upload',form))?.fileName;if(!value) throw new Error('Upload returned no fileName.');
            }
            nodes.push({nodeId:String(e.spec.node_id),fieldName:e.spec.field_name,fieldValue:String(value)});
          }
          state.status='submitting';await save(path,state);
          const d=await api(c,'create',{workflowId:w.workflow_id,nodeInfoList:nodes,...(w.instance_type?{instanceType:w.instance_type}:{})});
          if(!d?.taskId) throw new Error('Submission returned no taskId.');
          state.task_id=String(d.taskId);state.status='QUEUED';
        } catch(e) {state.status=state.status==='submitting'?'submission_unknown':'preparation_failed';state.errors=[e.message];}
        await save(path,state);return summary(state);
      }
      if(!state) throw new Error('Unknown request_id.');
      if(state.status==='completed'||state.status==='FAILED') return summary(state);
      if(!state.task_id&&args.task_id&&/^\d+$/.test(args.task_id)) {state.task_id=args.task_id;await save(path,state);}
      if(!state.task_id) return summary(state);
      const c=connection(config,state.connection);
      if(c.base!==state.base_url) throw new Error('Connection domain changed; restore it before retrieving this task.');
      try {
        const deadline=now()+waitMs;
        let status=await api(c,'status',{taskId:state.task_id});
        while(['QUEUED','RUNNING'].includes(status) && now()+10000<=deadline) {
          await sleep(10000);
          status=await api(c,'status',{taskId:state.task_id});
        }
        if(!['QUEUED','RUNNING','SUCCESS','FAILED'].includes(status)) throw new Error('Unknown RunningHub task status.');
        state.status=status;state.errors=[];
        if(status==='SUCCESS') {
          const outputs=await api(c,'outputs',{taskId:state.task_id});
          if(!Array.isArray(outputs)) throw new Error('Invalid output list.');
          const selected=outputs.filter(o=>state.output_node_ids.includes(String(o.nodeId)));
          if(!selected.length) throw new Error('Configured output nodes returned no files; check output_node_ids.');
          const folder=state.output_dir;await mkdir(folder,{recursive:true});
          for(let i=0;i<selected.length;i++) {
            const o=selected[i];const old=state.files.find(f=>f.index===i);
            if(old) {try{imageExtension(await readFile(old.path));continue;}catch{state.files=state.files.filter(f=>f.index!==i);}}
            try {
              const url=new URL(o.fileUrl);
              if(url.protocol!=='https:'||url.username||url.password) throw new Error('Invalid output URL.');
              // API credentials must never be forwarded to the image host.
              const response=await fetchImpl(url,{signal:AbortSignal.timeout(60000),redirect:'error'});
              if(!response.ok) throw new Error(`Download HTTP ${response.status}`);
              if(Number(response.headers.get('content-length'))>100*1024*1024) throw new Error('Output exceeds 100 MB.');
              const chunks=[];let size=0;
              for await(const chunk of response.body) {size+=chunk.length;if(size>100*1024*1024) throw new Error('Output exceeds 100 MB.');chunks.push(chunk);}
              const bytes=Buffer.concat(chunks);const ext=imageExtension(bytes);
              const target=join(folder,`${state.request_id}-${i+1}-${crypto.randomUUID().slice(0,8)}.${ext}`);
              await writeFile(target+'.part',bytes);await rename(target+'.part',target);
              const dimensions=ext==='png'?{width:bytes.readUInt32BE(16),height:bytes.readUInt32BE(20)}:{};
              state.files.push({index:i,node_id:String(o.nodeId),path:target,bytes:bytes.length,format:ext,...dimensions});await save(path,state);
            } catch {state.errors.push(`Output ${i+1}: download failed or unsupported image; status can retry retrieval without resubmitting.`);}
          }
          state.status=state.errors.length?'download_incomplete':'completed';
        }
      } catch(e) {state.errors=[e.message];}
      await save(path,state);return summary(state);
    } finally {locks.delete(path);}
  };
}
