const {test}=require('node:test');
const assert=require('node:assert/strict');
const {page,prepare,chooseModel,chooseShared,open,state,snapshot,tick,html,textTree}=require('./ui_test_harness.cjs');
const statusPath='/api/magazine/runs/test-run';
const posts=(p,suffix)=>p.calls.filter(c=>c.method==='POST'&&c.path.endsWith(suffix));

test('four compact areas, accessible theme and advanced input retained',()=>{
  for(const heading of ['Your workflows','Set up','Run workflow','Results and previous work'])assert.ok(html.includes(heading));
  assert.match(html,/<label[^>]*for="appearance"/);assert.match(html,/<details/);
  assert.match(html,/id="run-progress"[^>]*aria-label=/);assert.match(html,/@media/);
});
test('prepare shows persisted READY and never starts; setup edits leave selected run separate',async()=>{
  const p=await page();await prepare(p);
  assert.equal(p.el('run-badge').textContent,'Ready to start');assert.equal(p.el('btn-start').disabled,false);
  assert.match(p.el('run-title').textContent,/Demo/);assert.match(p.el('run-summary').textContent,/Nothing|Start workflow/);
  assert.equal(posts(p,'/start').length,0);await p.el('json').emit('input');
  assert.equal(p.el('btn-create').disabled,true);assert.equal(p.el('btn-start').disabled,false);
});
test('one explicit start; duplicate handler call refused while pending; 202 reconciles, not completed',async()=>{
  let resolve,working=false;const pending=new Promise(r=>resolve=r);
  const p=await page((path,body,opts)=>{
    if(path.endsWith('/start'))return pending;
    if(path===statusPath&&working)return state('RUNNING',{current_cartridge:'first',todo:['second']});
  });await prepare(p);const start=p.el('btn-start').click();await tick();
  assert.equal(p.el('btn-start').disabled,true);await p.el('btn-start').emit('click');
  working=true;resolve({_status:202,status:'accepted',attempt:1});await start;
  assert.equal(posts(p,'/start').length,1);assert.equal(p.el('run-badge').textContent,'Working');
  assert.equal(p.el('run-progress').value,0);assert.ok([...p.timers.values()].some(t=>t.ms===5000));
});
test('Stop is explicit, preserves the original page, and shows confirmed durable termination',async()=>{
  let stopped=false;const control={request:'STOP',cancellation_status:'CONFIRMED',termination_status:'CONFIRMED',termination_method:'active-inference-cancelled',actual_at:'2026-10-13T10:00:00Z'};
  const p=await page(path=>{
    if(path.endsWith('/stop')){stopped=true;return {status:'stopped',control,message:'Workflow termination is durably confirmed'};}
    if(path===statusPath)return stopped?state('HALTED',{done:['first'],todo:['second'],control}):state('RUNNING',{current_cartridge:'first',todo:['second']});
  });
  await open(p,'test-run');assert.equal(p.el('btn-stop').disabled,false);
  await p.el('btn-stop').click();assert.equal(posts(p,'/stop').length,1);
  assert.equal(posts(p,'/start').length,0);assert.equal(posts(p,'/recover').length,0);
  assert.equal(p.el('run-badge').textContent,'Stopped — needs attention');
  assert.match(p.el('run-summary').textContent,/stopped as requested|preserved/i);
  assert.equal(p.el('btn-stop').disabled,true);
});
test('unconfirmed cancellation is labelled as requested, never as stopped',async()=>{
  const control={request:'STOP',cancellation_status:'FAILED',cancellation_detail:'HTTP 500',termination_status:'REQUESTED',termination_method:null,actual_at:null};
  let requested=false;const p=await page(path=>{
    if(path.endsWith('/stop')){requested=true;return {_status:202,status:'stop_requested',control,message:'active inference cancellation was not confirmed'};}
    if(path===statusPath)return state('RUNNING',{current_cartridge:'first',todo:['second'],control:requested?control:null});
  });
  await open(p,'test-run');await p.el('btn-stop').click();
  assert.equal(p.el('run-badge').textContent,'Working');
  assert.match(p.el('run-summary').textContent,/wasn't confirmed|may finish|no later step/i);
  assert.doesNotMatch(p.el('control-msg').textContent,/Workflow stopped/i);
  assert.equal(p.el('btn-stop').disabled,true);
});
test('uncertain Start reconciles RUNNING without retry',async()=>{
  let started=false;const p=await page(path=>{
    if(path.endsWith('/start')){started=true;throw Error('connection lost');}
    if(path===statusPath&&started)return state('RUNNING',{current_cartridge:'first',todo:['second']});
  });await prepare(p);await p.el('btn-start').click();
  assert.equal(posts(p,'/start').length,1);assert.equal(p.el('run-badge').textContent,'Working');
  assert.match(p.el('control-msg').textContent,/connection|confirm|response/i);
});
test('uncertain Start with READY or failed reconciliation disables further start until manual check',async()=>{
  let started=false,offline=false;const p=await page(path=>{
    if(path.endsWith('/start')){started=true;throw Error('lost');}
    if(path===statusPath&&offline)throw Error('offline');
  });await prepare(p);await p.el('btn-start').click();
  assert.equal(p.el('btn-start').disabled,true);assert.match(p.el('control-msg').textContent,/Check progress/);
  await p.el('btn-refresh-run').click();assert.equal(p.el('btn-start').disabled,false);
  offline=true;await p.el('btn-start').click();assert.equal(posts(p,'/start').length,2);
  assert.equal(p.el('btn-start').disabled,true);
});
test('409 HALTED is a refusal, not blanket already-started; refresh authoritative state',async()=>{
  let stopped=false;const p=await page(path=>{
    if(path.endsWith('/start')){stopped=true;return {_status:409,detail:'auto-halted (lost runtime); recover to READY first'};}
    if(path===statusPath&&stopped)return state('HALTED');
  });await prepare(p);await p.el('btn-start').click();
  assert.equal(p.el('run-badge').textContent,'Stopped — needs attention');
  assert.doesNotMatch(p.el('control-msg').textContent,/already started/);
  assert.match(p.el('control-msg').textContent,/stopped|continue/i);
});
test('RUNNING completion gets exact safe multiline results in done order and stops polling',async()=>{
  let finished=false;const output='<img src=x onerror=alert(1)>\nsecond line';
  const p=await page(path=>{
    if(path===statusPath)return finished?state('COMPLETED',{done:['second','first'],todo:[]}):state('RUNNING',{done:['second'],current_cartridge:'first',todo:[]});
    if(path.endsWith('/results'))return {run_id:'test-run',done:finished?['second','first']:['second'],results:{first:'',second:output}};
  });await open(p,'test-run');assert.equal(p.el('run-progress').value,1);
  finished=true;await p.poll();assert.equal(p.el('run-badge').textContent,'Finished');assert.equal(p.el('run-progress').value,2);
  assert.deepEqual(p.el('saved-results').children.map(e=>e.children[0].textContent),['second','first']);
  assert.equal(p.el('saved-results').children[0].children[1].textContent,output);
  await p.el('saved-results').children[0].children[2].click();assert.equal(p.copied[0],output);
  assert.match(textTree(p.el('saved-results').children[1]),/empty/i);
  assert.ok(![...p.timers.values()].some(t=>t.ms===5000));
});
test('HALTED partial results distinguish missing and empty; recovery success never starts',async()=>{
  let recovered=false;const p=await page(path=>{
    if(path.endsWith('/recover')){recovered=true;return state('READY',{done:['first'],todo:['second']});}
    if(path===statusPath)return state(recovered?'READY':'HALTED',{done:['first'],todo:['second'],execution:{halt_reason:'Dispatch failed'}});
    if(path.endsWith('/results'))return {run_id:'test-run',done:['first'],results:{first:null}};
  });await open(p,'test-run');assert.match(textTree(p.el('saved-results')),/unavailable/i);
  assert.equal(p.el('btn-recover').disabled,false);await p.el('btn-recover').click();
  assert.equal(p.el('run-badge').textContent,'Ready to start');assert.equal(posts(p,'/recover').length,1);
  assert.equal(posts(p,'/start').length,0);assert.match(p.el('control-msg').textContent,/won't|not started|Nothing/i);
});
test('recovery refusal uses actual cause and refreshes; cannot recover running/completed',async()=>{
  let working=false;const p=await page(path=>{
    if(path.endsWith('/recover')){working=true;return {_status:409,detail:'lock_busy: execution lock is held'};}
    if(path===statusPath)return state(working?'RUNNING':'HALTED');
  });await open(p,'test-run');await p.el('btn-recover').click();
  assert.match(p.el('control-msg').textContent,/busy|in use/i);assert.equal(p.el('btn-recover').disabled,true);
  await p.el('btn-recover').emit('click');assert.equal(posts(p,'/recover').length,1);
});
test('READY with an unretired claim offers server eligibility check, not inferred death',async()=>{
  const p=await page(path=>path===statusPath?state('READY',{execution:{owner_alive:false,halted:false}}):undefined);
  await open(p,'test-run');assert.equal(p.el('btn-recover').disabled,false);
  assert.match(p.el('recovery-note').textContent,/check whether/);assert.doesNotMatch(p.el('recovery-note').textContent,/dead|abandoned|extinct/i);
  assert.equal(posts(p,'/recover').length,0);
});
test('history refresh does not fetch every result; reopening never starts',async()=>{
  const p=await page((path,body,opts)=>path==='/api/magazine/runs'&&opts.method==='GET'?{runs:[state(),state('COMPLETED',{run_id:'old'})]}:undefined);
  assert.equal(p.el('history-runs').children.length,2);assert.match(p.el('history-runs').children[1].textContent,/Finished/);
  assert.equal(p.calls.filter(c=>c.path.endsWith('/results')).length,0);
  await open(p,'old');assert.match(p.el('run-reference').textContent,/old/);assert.equal(posts(p,'/start').length,0);
});
test('status requests never overlap; changing runs aborts and ignores old status and results',async()=>{
  let release,delay=false;const pending=new Promise(r=>release=r);
  const p=await page(path=>path===statusPath?(delay?pending:state('RUNNING')):undefined);
  await open(p,'test-run');delay=true;const polling=p.poll();await tick();
  const old=p.calls.filter(c=>c.path===statusPath).at(-1);
  await p.el('btn-refresh-run').emit('click');assert.equal(p.calls.filter(c=>c.path===statusPath).length,2);
  await open(p,'new');assert.equal(old.signal.aborted,true);release(state('COMPLETED'));await polling;
  assert.match(p.el('run-reference').textContent,/new/);assert.equal(p.el('run-badge').textContent,'Ready to start');
});
test('late results and late Start do not overwrite a newly selected run',async()=>{
  let release,startRelease;const pending=new Promise(r=>release=r),startPending=new Promise(r=>startRelease=r);
  const p=await page(path=>{
    if(path===statusPath+'/results')return pending;
    if(path===statusPath+'/start')return startPending;
  });const opening=open(p,'test-run');await tick();
  const starting=p.el('btn-start').click();await tick();await open(p,'new');
  release({run_id:'test-run',done:['first'],results:{first:'OLD'}});startRelease({_status:202,status:'accepted'});
  await opening;await starting;assert.match(p.el('run-reference').textContent,/new/);assert.doesNotMatch(textTree(p.el('saved-results')),/OLD/);
});
test('polling network failure retries with bounded backoff and manual refresh restores cadence',async()=>{
  let failed=false;const p=await page(path=>{if(path===statusPath){if(failed)throw Error('offline');return state('RUNNING');}});
  await open(p,'test-run');failed=true;await p.poll();
  assert.ok([...p.timers.values()].some(t=>t.ms===10000));assert.match(p.el('control-msg').textContent,/Check progress/);
  failed=false;await p.el('btn-refresh-run').click();assert.ok([...p.timers.values()].some(t=>t.ms===5000));
});
test('failed results are not shown as an empty workflow and can be retried',async()=>{
  let fail=true;const p=await page(path=>{
    if(path===statusPath)return state('COMPLETED',{done:['first'],todo:[]});
    if(path.endsWith('/results')){if(fail)throw Error('offline');return {run_id:'test-run',done:['first'],results:{first:'saved'}};}
  });await open(p,'test-run');assert.match(p.el('results-msg').textContent,/couldn't load/);
  fail=false;await p.el('btn-refresh-run').click();assert.match(textTree(p.el('saved-results')),/saved/);
});
test('permission and model errors have useful primary explanations with details retained',async()=>{
  const p=await page((path,body,opts)=>path==='/api/magazine/runs'&&opts.method==='POST'?{_status:400,detail:{admission_errors:['Selected model is not available to this user']}}:undefined);
  await prepare(p);assert.match(p.el('create-msg').textContent,/AI model isn't available/);
  assert.match(p.el('error-detail').textContent,/Selected model/);
  const q=await page(path=>path===statusPath?{_status:403,detail:'Forbidden'}:undefined);
  await open(q,'test-run');assert.match(q.el('control-msg').textContent,/permission/);assert.equal(q.el('btn-start').disabled,true);
});
test('explicit shared AI setting applies to all steps and C1 import stays separate from execution',async()=>{
  const p=await page();p.el('library-workflows').value='Demo';await p.el('btn-library-select').click();
  await chooseShared(p,'other');await p.el('btn-create').click();
  assert.equal(posts(p,'/api/magazine/runs')[0].body.model_map.research,'other');assert.equal(posts(p,'/start').length,0);
});
test('appearance follows Open WebUI, applies immediately, persists and isolates explicit choice',async()=>{
  const storage=new Map([['theme','dark']]);const p=await page(undefined,{storage});
  assert.equal(p.root.dataset.theme,'dark');p.el('appearance').value='light';await p.el('appearance').emit('change');
  assert.equal(p.root.dataset.theme,'light');assert.equal(storage.get('theme'),'dark');
  p.root.classList.add('dark');p.themeChanged();assert.equal(p.root.dataset.theme,'light');
  const q=await page(undefined,{storage});assert.equal(q.root.dataset.theme,'light');
  q.el('appearance').value='follow';await q.el('appearance').emit('change');assert.equal(q.root.dataset.theme,'dark');
});
test('default follows class, storage then system; storage unavailable is harmless',async()=>{
  assert.equal((await page(undefined,{owuiDark:true})).root.dataset.theme,'dark');
  const p=await page(undefined,{osDark:true,storageError:true});assert.equal(p.root.dataset.theme,'dark');
  p.el('appearance').value='light';await p.el('appearance').emit('change');assert.equal(p.root.dataset.theme,'light');
});
test('light/dark palette text, errors and controls meet WCAG AA contrast by calculation',()=>{
  function lum(hex){const rgb=hex.match(/\w\w/g).map(c=>parseInt(c,16)/255).map(c=>c<=.04045?c/12.92:((c+.055)/1.055)**2.4);return rgb[0]*.2126+rgb[1]*.7152+rgb[2]*.0722;}
  function ratio(a,b){const ls=[lum(a),lum(b)].sort((a,b)=>b-a);return (ls[0]+.05)/(ls[1]+.05);}
  for(const selector of [':root','[data-theme="dark"]']){
    const escaped=selector.replace(/[.*+?^${}()|[\]\\]/g,'\\$&');
    const block=html.match(new RegExp(escaped+'\\s*\\{([^}]+)\\}'))[1];
    const vars=Object.fromEntries([...block.matchAll(/--([\w-]+):\s*#([0-9a-f]{6})/g)].map(m=>[m[1],m[2]]));
    for(const fg of ['ink','muted','err','ok'])for(const bg of ['bg','card','soft'])assert.ok(ratio(vars[fg],vars[bg])>=4.5,`${selector} ${fg}/${bg}`);
    for(const bg of ['bg','card','soft'])assert.ok(ratio(vars.line,vars[bg])>=3,`${selector} control border/${bg}`);
    assert.ok(ratio(vars['button-ink'],vars.accent)>=4.5,selector+' button');
  }
});
test('accepted Start followed by READY must not enable a second Start without a manual check',async()=>{
  const p=await page(path=>path.endsWith('/start')?{_status:202,status:'accepted'}:undefined);
  await prepare(p);await p.el('btn-start').click();assert.equal(p.el('btn-start').disabled,true);
  assert.match(p.el('control-msg').textContent,/Check progress/);
});
test('results connection failure schedules a bounded retry rather than stopping automatic polling',async()=>{
  const p=await page(path=>{
    if(path===statusPath)return state('RUNNING');
    if(path.endsWith('/results'))throw Error('offline');
  });await open(p,'test-run');assert.ok([...p.timers.values()].some(t=>t.ms===10000));
});
test('late preparation leaves a subsequently reopened run selected',async()=>{
  let release;const pending=new Promise(r=>release=r);
  const p=await page((path,body,opts)=>path==='/api/magazine/runs'&&opts.method==='POST'?pending:undefined);
  p.el('library-workflows').value='Demo';await p.el('btn-library-select').click();
  await chooseModel(p);
  await chooseModel(p,'second');
  const creating=p.el('btn-create').click();await tick();await open(p,'new');
  release(state());await creating;assert.match(p.el('run-reference').textContent,/new/);
});
test('empty model discovery prevents preparing; actual engine admission wording is translated',async()=>{
  const p=await page(path=>path.endsWith('/models')?{models:[]}:undefined);
  p.el('library-workflows').value='Demo';await p.el('btn-library-select').click();assert.equal(p.el('btn-create').disabled,true);
  const q=await page((path,body,opts)=>path==='/api/magazine/runs'&&opts.method==='POST'?{_status:400,
    detail:{admission_errors:["model 'not-here' (for key 'research') is not in the user's available models"]}}:undefined);
  await prepare(q);assert.match(q.el('create-msg').textContent,/AI model isn't available/);
});
test('read requests have a 20-second abort deadline; timeout leaves stale actions disabled',async()=>{
  let hanging=false;const p=await page((path,body,opts)=>{
    if(path===statusPath&&hanging)return new Promise((resolve,reject)=>opts.signal.addEventListener('abort',()=>reject(Error('timeout'))));
  });await open(p,'test-run');hanging=true;const checking=p.el('btn-refresh-run').click();await tick();
  const timer=[...p.timers].find(([,t])=>t.ms===20000);assert.ok(timer);p.timers.delete(timer[0]);timer[1].fn();await checking;
  assert.equal(p.calls.filter(c=>c.path===statusPath).at(-1).signal.aborted,true);assert.equal(p.el('btn-start').disabled,true);
});
test('uncertain recovery response never starts and requires checking persisted state',async()=>{
  let recovered=false;const p=await page(path=>{
    if(path===statusPath)return state(recovered?'READY':'HALTED');
    if(path.endsWith('/recover')){recovered=true;throw Error('lost response');}
  });await open(p,'test-run');await p.el('btn-recover').click();
  assert.equal(p.el('run-badge').textContent,'Ready to start');assert.equal(p.el('btn-start').disabled,true);
  assert.equal(posts(p,'/start').length,0);await p.el('btn-refresh-run').click();assert.equal(p.el('btn-start').disabled,false);
});
test('401, 404 and non-start conflicts are not reported as already started',async()=>{
  for(const [code,detail,expected] of [[401,'no usable bearer token',/Sign in/],[404,'run not found',/account/],
    [409,'READY recovery refused: changed',/couldn't prepare/],[409,'another conflicting admission',/couldn't start/]]){
    const p=await page(path=>path.endsWith('/start')?{_status:code,detail}:undefined);
    await prepare(p);await p.el('btn-start').click();assert.match(p.el('control-msg').textContent,expected);
    assert.doesNotMatch(p.el('control-msg').textContent,/already started/);
  }
});
test('no recovery for completed work, unknown state or ordinary READY work',async()=>{
  for(const status of ['COMPLETED','READY','error']){
    const p=await page(path=>path===statusPath?state(status):undefined);await open(p,'test-run');
    assert.equal(p.el('btn-recover').disabled,true);await p.el('btn-recover').emit('click');assert.equal(posts(p,'/recover').length,0);
  }
});

test('v3 new setup starts with Choose an AI model and no automatic choice',async()=>{
  const p=await page();p.el('library-workflows').value='Demo';await p.el('btn-library-select').click();
  for(const id of ['first','second']){
    assert.equal(p.el('model-'+id).value,'');
    assert.equal(p.el('model-'+id).children[0].textContent,'Choose an AI model');
  }
  assert.equal(posts(p,'/api/magazine/runs').length,0);
});
test('v3 missing AI settings explain what to do and never create READY work',async()=>{
  const p=await page();p.el('library-workflows').value='Demo';await p.el('btn-library-select').click();
  await p.el('btn-create').emit('click');
  assert.equal(p.el('create-msg').textContent,'Choose an AI model for each step before continuing.');
  assert.equal(posts(p,'/api/magazine/runs').length,0);
});
test('every independent step needs a choice; explicit shared choice maps the original key',async()=>{
  const preview=JSON.parse(JSON.stringify(snapshot));preview.cartridges[1].model='review';preview.document.cartridges[1].job_type='review';
  const p=await page(path=>path==='/api/magazine/library/Demo'?preview:undefined);
  p.el('library-workflows').value='Demo';await p.el('btn-library-select').click();await chooseModel(p,'first','other');
  assert.equal(p.el('model-second').value,'');await p.el('btn-create').emit('click');assert.equal(posts(p,'/api/magazine/runs').length,0);
  await chooseModel(p,'second');await p.el('btn-create').click();assert.deepEqual(posts(p,'/api/magazine/runs')[0].body.model_map,{first:'other',second:'ornith:9b-q8_0'});
  const q=await page();q.el('library-workflows').value='Demo';await q.el('btn-library-select').click();await chooseShared(q,'other');
  await q.el('btn-create').click();assert.equal(posts(q,'/api/magazine/runs')[0].body.model_map.research,'other');
});
test('v3 rechecking the same workflow preserves an explicit available selection, not one for different content',async()=>{
  let changed=false;const altered=JSON.parse(JSON.stringify(snapshot));altered.document.cartridges[0].prompt='Changed workflow';
  const p=await page(path=>path==='/api/magazine/library/Demo'&&changed?altered:undefined);
  p.el('library-workflows').value='Demo';await p.el('btn-library-select').click();await chooseModel(p,'first','other');
  await p.el('btn-library-select').click();assert.equal(p.el('model-first').value,'other');assert.equal(p.el('model-second').value,'');
  changed=true;await p.el('btn-library-select').click();assert.equal(p.el('model-first').value,'');
});
test('v3 model admission refusal refreshes discovery, clears unavailable choice and preserves valid settings',async()=>{
  let unavailable=false;const preview=JSON.parse(JSON.stringify(snapshot));preview.cartridges[1].model='review';preview.document.cartridges[1].job_type='review';
  const p=await page((path,body,opts)=>{
    if(path==='/api/magazine/library/Demo')return preview;
    if(path.endsWith('/models')&&unavailable)return {models:[{id:'ornith:9b-q8_0'}]};
    if(path==='/api/magazine/runs'&&opts.method==='POST'){unavailable=true;return {_status:400,detail:{admission_errors:["model 'other' is not in the user's available models"]}};}
  });p.el('library-workflows').value='Demo';await p.el('btn-library-select').click();
  await chooseModel(p,'first','other');await chooseModel(p,'second');await p.el('btn-create').click();await tick();
  assert.equal(p.el('model-first').value,'');assert.equal(p.el('model-second').value,'ornith:9b-q8_0');
  assert.match(p.el('setup-summary').textContent,/no longer available|Choose another/);
  await p.el('btn-create').emit('click');assert.equal(posts(p,'/api/magazine/runs').length,1);
});
test('v3 existing run mapping is authoritative and is not copied into a new setup',async()=>{
  const p=await page(path=>path===statusPath?state('READY',{model_map:{research:'persisted-not-discovered'}}):undefined);
  await open(p,'test-run');assert.match(p.el('run-detail').textContent,/persisted-not-discovered/);
  assert.equal(p.el('btn-start').disabled,false);
  p.el('library-workflows').value='Demo';await p.el('btn-library-select').click();
  assert.equal(p.el('model-first').value,'');assert.match(p.el('run-detail').textContent,/persisted-not-discovered/);
});
test('v3 singular and plural completion summaries are concise, not duplicated',async()=>{
  for(const [done,expected] of [[['first'],'The step is finished. Your result is ready.'],[['first','second'],'All 2 steps are finished. Your results are ready.']]){
    const p=await page(path=>path===statusPath?state('COMPLETED',{done,todo:[]}):undefined);await open(p,'test-run');
    assert.equal(p.el('run-summary').textContent,expected);assert.equal(p.el('run-progress').value,done.length);
    assert.doesNotMatch(p.el('run-summary').textContent,/of \d+ steps finished/);
  }
});
test('v3 RUNNING summary and partial results use persisted state even if results advance',async()=>{
  const p=await page(path=>{
    if(path===statusPath)return state('RUNNING',{done:['first'],current_cartridge:'second',todo:[]});
    if(path.endsWith('/results'))return {run_id:'test-run',status:'COMPLETED',done:['first','second'],results:{first:'partial',second:'final'}};
  });await open(p,'test-run');assert.equal(p.el('run-badge').textContent,'Working');assert.equal(p.el('run-progress').value,1);
  assert.equal(p.el('run-summary').textContent,'Magazine is working on step 2 of 2. 1 of 2 steps finished.');
  assert.deepEqual(p.el('saved-results').children.map(c=>c.children[1].textContent),['partial','final']);
});
test('v3 completion after explicit Start removes transient notice rather than adding a second completion message',async()=>{
  let finished=false;const p=await page(path=>{
    if(path.endsWith('/start'))return {_status:202,status:'accepted'};
    if(path===statusPath)return state(finished?'COMPLETED':'RUNNING',finished?{done:['first','second'],todo:[]}:{current_cartridge:'first',todo:['second']});
  });await prepare(p);await p.el('btn-start').emit('click');finished=true;await p.poll();
  assert.equal(p.el('run-summary').textContent,'All 2 steps are finished. Your results are ready.');
  assert.equal(p.el('control-msg').classList.contains('hidden'),true);
});
