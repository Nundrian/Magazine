// C2.4 first-time flow, explicit replacement ownership and visible state.
const {test}=require('node:test'),assert=require('node:assert/strict');
const {page,chooseModel,chooseShared,open,snapshot,state,tick,html,textTree}=require('./ui_test_harness.cjs');
const copy=v=>JSON.parse(JSON.stringify(v)),replacement=()=>{const v=copy(snapshot);v.name='Second workflow';v.cartridges[0].id='different';v.document.cartridges[0].id='different';return v;};
const catalogue={available:true,can_import:true,workflows:[{workflow_id:'Demo',name:snapshot.name,valid:true,cartridge_count:2},
 {workflow_id:'Second',name:'Second workflow',valid:true,cartridge_count:2},{workflow_id:'Bad',name:'Bad',valid:false,cartridge_count:0}]};
const tools=[{id:'alpha',name:'First tool'},{id:'beta',name:'Second tool'}];
async function make(fn=()=>undefined){return page((url,body,opts)=>fn(url,body,opts)??(url==='/api/magazine/library'?catalogue:
 url==='/api/magazine/library/Second'?replacement():url.endsWith('/tools')?{tools}:undefined));}
async function select(p,id){p.el('library-workflows').value=id;await p.el('library-workflows').emit('change');}
async function load(p,id='Demo'){p.el('library-workflows').value=id;await p.el('btn-library-select').click();}
async function models(p){await chooseModel(p,'first');await chooseModel(p,'second','other');}
const writes=p=>p.calls.filter(c=>c.method==='POST'),loads=p=>p.calls.filter(c=>/^\/api\/magazine\/library\//.test(c.path)&&c.method==='GET');
test('C2.4 initial adjacent instruction and explicit Load your workflow label',async()=>{
 assert.match(html,/>Load your workflow<\/button>/);const p=await make();
 assert.equal(p.el('library-guidance').textContent,'Select a workflow, then choose Load your workflow.');
 assert.ok(html.indexOf('id="library-guidance"')<html.indexOf('<details><summary>Add a workflow'));
 assert.equal(p.el('btn-create').disabled,true);assert.match(p.el('prepare-help').textContent,/Load a workflow/);
});
test('C2.4 dropdown change never loads, validates, prepares or starts',async()=>{
 const p=await make();await select(p,'Second');assert.equal(loads(p).length,0);assert.equal(writes(p).length,0);
 assert.equal(p.el('cartridge-s').classList.contains('hidden'),true);
 assert.match(p.el('library-guidance').textContent,/Load your workflow/);
});
test('C2.4 no valid selection keeps Load unavailable and a bypassed handler cannot load',async()=>{
 const p=await make();for(const id of ['', 'Bad', 'Unknown']){
  await select(p,id);assert.equal(p.el('btn-library-select').disabled,true);await p.el('btn-library-select').emit('click');
 }assert.equal(loads(p).length,0);assert.equal(writes(p).length,0);
});
test('C2.4 one explicit load displays correct steps, name, count and setup guidance',async()=>{
 const p=await make();await load(p);assert.equal(loads(p).length,1);assert.equal(writes(p).length,0);
 assert.equal(p.el('cartridge-s').classList.contains('hidden'),false);
 assert.equal(p.el('library-msg').textContent,'Workflow loaded: '+snapshot.name+' — 2 steps.');
 assert.equal(p.el('setup-workflow').textContent,snapshot.name);assert.equal(p.el('setup-summary').textContent,'Choose an AI model for each step.');
 assert.deepEqual(p.el('cartridges').children.map(c=>c.children[1].textContent),['first','second']);
});
test('C2.4 loading is immediately visible and duplicate handler calls send only one request',async()=>{
 let resolve;const pending=new Promise(r=>resolve=r),p=await make(url=>url==='/api/magazine/library/Demo'?pending:undefined);
 p.el('library-workflows').value='Demo';const request=p.el('btn-library-select').click();await tick();
 assert.match(p.el('library-msg').textContent,/Loading workflow…/);assert.equal(p.el('btn-library-select').disabled,true);
 await p.el('btn-library-select').emit('click');assert.equal(loads(p).length,1);resolve(snapshot);await request;
 assert.equal(p.el('btn-library-select').disabled,false);assert.equal(writes(p).length,0);
});
test('C2.4 failed initial load gives retry guidance without enabling setup',async()=>{
 const p=await make(url=>url==='/api/magazine/library/Demo'?{_status:503,detail:'Unavailable'}:undefined);await load(p);
 assert.match(p.el('library-msg').textContent,/couldn't|unavailable/i);assert.match(p.el('library-msg').textContent,/Try|again/i);
 assert.equal(p.el('btn-create').disabled,true);assert.equal(p.el('btn-library-select').disabled,false);
});
test('C2.4 merely selecting a replacement keeps old configuration visible and clearly blocks mismatched Prepare',async()=>{
 const p=await make();await load(p);await models(p);const doc=p.el('pkg-doc').textContent;
 const box=p.el('tool-beta');box.checked=false;await box.onchange.call(box);await select(p,'Second');
 assert.equal(p.el('pkg-doc').textContent,doc);assert.equal(p.el('model-first').value,'ornith:9b-q8_0');
 assert.equal(p.el('tool-beta').checked,false);assert.equal(p.el('btn-create').disabled,true);
 assert.match(p.el('library-guidance').textContent,/Second workflow/);assert.match(p.el('library-guidance').textContent,/Demo/);
 assert.equal(p.el('setup-summary').textContent,'These settings are for your loaded workflow.');
 assert.match(p.el('prepare-help').textContent,/Load the selected workflow/);assert.equal(loads(p).length,1);
 await p.el('btn-create').emit('click');assert.equal(writes(p).length,0);
});
test('C2.4 failed replacement preserves source, model and tool exclusions, and names the still-loaded workflow',async()=>{
 const p=await make(url=>url==='/api/magazine/library/Second'?{valid:false,error:'Invalid YAML'}:undefined);
 await load(p);await models(p);const doc=p.el('pkg-doc').textContent;
 const box=p.el('tool-beta');box.checked=false;await box.onchange.call(box);await select(p,'Second');await p.el('btn-library-select').click();
 assert.equal(p.el('pkg-doc').textContent,doc);assert.equal(p.el('model-second').value,'other');assert.equal(box.checked,false);
 assert.equal(p.el('cartridge-s').classList.contains('hidden'),false);assert.match(p.el('library-msg').textContent,/still.*Demo/i);
 assert.match(p.el('error-detail').textContent,/Invalid YAML/);assert.equal(p.el('btn-create').disabled,true);
 await select(p,'Demo');assert.equal(p.el('btn-create').disabled,false);assert.equal(writes(p).length,0);
});
test('C2.4 successful replacement resets fresh models/tools only after its response',async()=>{
 let resolve;const pending=new Promise(r=>resolve=r),p=await make(url=>url==='/api/magazine/library/Second'?pending:undefined);
 await load(p);await models(p);const before=p.el('pkg-doc').textContent;await select(p,'Second');const request=p.el('btn-library-select').click();await tick();
 assert.equal(p.el('pkg-doc').textContent,before);assert.equal(p.el('btn-create').disabled,true);
 resolve(replacement());await request;assert.match(p.el('library-msg').textContent,/Second workflow — 2 steps/);
 assert.equal(p.el('model-different').value,'');assert.equal(p.el('tool-beta').checked,false);assert.equal(writes(p).length,0);
});
test('C2.4 old responses and finally blocks cannot overwrite a newer explicit load',async()=>{
 let first,second;const a=new Promise(r=>first=r),b=new Promise(r=>second=r);
 const p=await make(url=>url==='/api/magazine/library/Demo'?a:url==='/api/magazine/library/Second'?b:undefined);
 p.el('library-workflows').value='Demo';const old=p.el('btn-library-select').click();await tick();
 await select(p,'Second');const current=p.el('btn-library-select').click();await tick();
 first(snapshot);await old;assert.equal(p.el('btn-library-select').disabled,true);assert.match(p.el('library-msg').textContent,/Loading/);
 second(replacement());await current;assert.equal(p.el('setup-workflow').textContent,'Second workflow');assert.equal(loads(p).length,2);
});
test('C2.4 a changed dropdown cancels its request and cannot make a stale response current',async()=>{
 let resolve;const pending=new Promise(r=>resolve=r),p=await make(url=>url==='/api/magazine/library/Demo'?pending:undefined);
 p.el('library-workflows').value='Demo';const request=p.el('btn-library-select').click();await tick();
 const call=loads(p)[0];await select(p,'Second');assert.equal(call.signal.aborted,true);resolve(snapshot);await request;
 assert.equal(p.el('cartridge-s').classList.contains('hidden'),true);assert.equal(p.el('btn-create').disabled,true);
 assert.equal(p.el('btn-library-select').disabled,false);assert.doesNotMatch(p.el('library-msg').textContent,/Workflow loaded/);
});
test('C2.4 stale load cannot overwrite explicit JSON validation or later source edits',async()=>{
 let resolve;const pending=new Promise(r=>resolve=r),p=await make(url=>url==='/api/magazine/library/Demo'?pending:undefined);
 p.el('library-workflows').value='Demo';const request=p.el('btn-library-select').click();await tick();
 p.el('json').value=JSON.stringify(snapshot.document);await p.el('json').emit('input');await p.el('btn-validate').click();
 const before=p.el('pkg-doc').textContent;resolve(replacement());await request;assert.equal(p.el('pkg-doc').textContent,before);
 assert.ok(p.el('cartridge-s').classList.contains('hidden')===false);assert.equal(loads(p).length,1);
});
test('C2.4 catalogue refresh keeps selected and loaded identities without silently loading the first item',async()=>{
 const p=await make();await load(p,'Second');await p.el('btn-library-refresh').click();
 assert.equal(p.el('library-workflows').value,'Second');assert.equal(p.el('setup-workflow').textContent,'Second workflow');
 assert.equal(loads(p).length,1);assert.match(p.el('library-msg').textContent,/Workflow loaded: Second workflow/);
});
test('C2.4 delayed catalogue refresh cannot override a newer selection',async()=>{
 let delay=false,resolve;const pending=new Promise(r=>resolve=r),p=await make(url=>url==='/api/magazine/library'&&delay?pending:undefined);
 delay=true;const refresh=p.el('btn-library-refresh').click();await tick();await select(p,'Second');resolve(catalogue);await refresh;
 assert.equal(p.el('library-workflows').value,'Second');assert.equal(loads(p).length,0);
});
test('C2.4 preparation names the missing step next to a disabled control and enables only after all models',async()=>{
 const p=await make();await load(p);assert.equal(p.el('btn-create').disabled,true);assert.match(p.el('prepare-help').textContent,/AI model for Step 1/);
 await chooseModel(p);assert.match(p.el('prepare-help').textContent,/AI model for Step 2/);assert.equal(p.el('btn-create').disabled,true);
 await p.el('btn-create').emit('click');assert.equal(writes(p).length,0);await chooseModel(p,'second','other');assert.equal(p.el('btn-create').disabled,false);
 assert.ok(p.el('prepare-help').classList.contains('hidden'));assert.ok(html.includes("This saves your choices. It doesn't start the workflow."));
});
test('C2.4 explicit shared selection satisfies preparation without losing independent choices',async()=>{
 const p=await make();await load(p);await models(p);await chooseShared(p,'');assert.equal(p.el('btn-create').disabled,true);
 assert.match(p.el('prepare-help').textContent,/AI model for the workflow/);await chooseShared(p,'other');assert.equal(p.el('btn-create').disabled,false);
 p.el('model-mode').value='independent';await p.el('model-mode').emit('change');assert.equal(p.el('model-first').value,'ornith:9b-q8_0');
 assert.equal(p.el('model-second').value,'other');assert.equal(p.el('btn-create').disabled,false);assert.equal(writes(p).length,0);
});
test('C2.4 source and explicit tool grants survive rechecking the same workflow, themes and history',async()=>{
 const p=await make();await load(p);await models(p);const box=p.el('tool-alpha');box.checked=true;await box.onchange.call(box);
 p.el('appearance').value='dark';await p.el('appearance').emit('change');await open(p,'new');await load(p);
 assert.equal(box.checked,true);assert.equal(p.el('tool-beta').checked,false);assert.equal(p.el('tool-count').textContent,'Tools selected: 1 of 2');assert.equal(p.el('model-second').value,'other');
 assert.equal(p.el('btn-create').disabled,false);assert.equal(writes(p).length,0);
});
test('C2.4 prepare persists exactly current setup as READY; Start is a separate explicit mock action',async()=>{
 const p=await make();await load(p);await models(p);const box=p.el('tool-alpha');box.checked=true;await box.onchange.call(box);
 await p.el('btn-create').click();assert.equal(writes(p).length,1);assert.deepEqual(writes(p)[0].body.tool_ids,['alpha']);
 assert.equal(writes(p)[0].body.project_id,'Demo');assert.equal(writes(p)[0].body.owner_id,undefined);
 assert.deepEqual(writes(p)[0].body.model_map,{first:'ornith:9b-q8_0',second:'other'});assert.equal(p.el('run-badge').textContent,'Ready to start');
 assert.match(p.el('run-summary').textContent,/hasn't started|not started|Nothing has started/i);assert.equal(writes(p).filter(c=>c.path.endsWith('/start')).length,0);
 await p.el('btn-start').click();assert.equal(writes(p).filter(c=>c.path.endsWith('/start')).length,1);
});
test('C2.4 existing completed work remains separate from loaded setup and is opened with accurate history wording',async()=>{
 const p=await make(url=>url==='/api/magazine/runs/old'?state('COMPLETED',{run_id:'old',done:['first','second'],todo:[],model_map:{old:'saved'}}):undefined);
 await load(p);await open(p,'old');assert.equal(p.el('run-badge').textContent,'Finished');assert.equal(p.el('setup-workflow').textContent,snapshot.name);
 assert.match(html,/>View previous work<\/button>/);assert.match(p.el('run-detail').textContent,/saved/);assert.equal(writes(p).length,0);
});
test('C2.4 model reference remains collapsed, noninteractive and separate from visible tools',async()=>{
 assert.match(html,/<details><summary>Model information<\/summary>/);assert.match(html,/Available models \(reference only\)/);
 assert.ok(html.indexOf('</details>',html.indexOf('<summary>Model information'))<html.indexOf('id="tools-heading"'));
 const p=await make();await load(p);assert.ok(p.el('model-first'));assert.equal(p.el('models').listeners.change,undefined);
 assert.equal(p.el('tool-alpha').checked,false);assert.equal(p.el('btn-tools-clear-all').disabled,false);
});
test('C2.4 optional JSON/file and ZIP import paths retain contracts and never prepare automatically',async()=>{
 const p=await make(url=>url==='/api/magazine/library/import'?{imported:true,project_id:'Second',name:'Second workflow'}:undefined);
 p.el('json').value=JSON.stringify(snapshot.document);await p.el('btn-validate').click();assert.ok(!p.el('cartridge-s').classList.contains('hidden'));
 p.el('project-zip').files=[{name:'Second.zip',b64:'UEsDBA=='}];await p.el('btn-project-import').click();
 assert.equal(p.el('library-workflows').value,'Second');assert.equal(p.el('setup-workflow').textContent,snapshot.name);
 assert.equal(p.el('btn-create').disabled,true);assert.equal(writes(p).filter(c=>c.path==='/api/magazine/runs'||c.path.endsWith('/start')).length,0);
});
test('C2.4 safe full names and singular workflow confirmation',async()=>{
 const single=copy(snapshot);single.name='<img src=x> '+('unbroken'.repeat(50));single.cartridges=single.cartridges.slice(0,1);single.document.cartridges=single.document.cartridges.slice(0,1);
 const p=await make(url=>url==='/api/magazine/library/Demo'?single:undefined);await load(p);
 assert.equal(p.el('library-msg').textContent,'Workflow loaded: '+single.name+' — 1 step.');assert.equal(p.el('setup-workflow').textContent,single.name);
 assert.equal(p.el('model-first').value,'');assert.equal(writes(p).length,0);
});

test('beta.5 retention warning remains visible after successful import and catalogue refresh',async()=>{
 const p=await make(url=>url==='/api/magazine/library/import'?{imported:true,project_id:'Second',name:'Second workflow',
   input_zip:{retained:false,message:'WARNING: project imported; keep original ZIP.'}}:undefined);
 p.el('project-zip').files=[{name:'Second.zip',b64:'UEsDBA=='}];await p.el('btn-project-import').click();
 assert.match(p.el('project-import-msg').textContent,/WARNING.*keep original ZIP/);
 assert.ok(p.el('project-import-msg').classList.contains('err'));
 assert.equal(writes(p).length,1);assert.equal(writes(p)[0].path,'/api/magazine/library/import');
});

test('beta.5 export warning preserves completed display and canonical text without execution writes',async()=>{
 const saved=state('COMPLETED',{run_id:'old',done:['first','second'],todo:[]});
 const p=await make(url=>url==='/api/magazine/runs/old'?saved:url==='/api/magazine/runs/old/results'?
   {run_id:'old',status:'COMPLETED',done:['first','second'],results:{first:'EXACT FIRST',second:'EXACT SECOND'},
    export:{status:'FAILED',message:'Finished Work export failed. Canonical results unchanged.'}}:undefined);
 await open(p,'old');assert.equal(p.el('run-badge').textContent,'Finished');
 assert.match(p.el('results-msg').textContent,/export failed.*unchanged/);
 assert.match(textTree(p.el('saved-results')),/EXACT FIRST/);assert.equal(writes(p).length,0);
});
