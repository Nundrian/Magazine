// C2.1 regressions run the actual inline script, not a second implementation.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const {page,chooseModel,snapshot,tick,html,open,state}=require('./ui_test_harness.cjs');
const clone=x=>JSON.parse(JSON.stringify(x));
const posts=p=>p.calls.filter(c=>c.method==='POST'&&c.path==='/api/magazine/runs');
async function load(p){p.el('library-workflows').value='Demo';await p.el('btn-library-select').click();}
async function mode(p,value){p.el('model-mode').value=value;await p.el('model-mode').emit('change');}
async function shared(p,value){p.el('model-all').value=value;await p.el('model-all').onchange.call(p.el('model-all'));}
test('C2.1 newly loaded multi-step workflow defaults to independent native controls',async()=>{
  const p=await page();await load(p);assert.equal(p.el('model-mode').value,'independent');
  assert.equal(p.el('model-mode-control').classList.contains('hidden'),false);
  assert.equal(p.el('model-first').value,'');assert.equal(p.el('model-second').value,'');
  assert.equal(p.el('shared-model-control').classList.contains('hidden'),true);
  assert.match(html,/Choose a model for each step/);assert.match(html,/Use the same model for all steps/);
});
test('C2.1 changing second step does not overwrite first even with one original logical key',async()=>{
  const p=await page();await load(p);await chooseModel(p,'first');await chooseModel(p,'second','other');
  assert.equal(p.el('model-first').value,'ornith:9b-q8_0');assert.equal(p.el('model-second').value,'other');
});
test('C2.1 independent request clones document, uses unique IDs and leaves canonical snapshot unchanged',async()=>{
  const source=clone(snapshot),before=JSON.stringify(source);const p=await page(path=>path==='/api/magazine/library/Demo'?source:undefined);
  await load(p);const display=p.el('pkg-doc').textContent;await chooseModel(p,'first');await chooseModel(p,'second','other');
  await p.el('btn-create').click();const body=posts(p)[0].body,doc=JSON.parse(body.magazine);
  assert.deepEqual(body.model_map,{first:'ornith:9b-q8_0',second:'other'});
  assert.deepEqual(doc,{...source.document,cartridges:source.document.cartridges.map(c=>({...c,job_type:c.id}))});
  assert.equal(JSON.stringify(source),before);assert.equal(p.el('pkg-doc').textContent,display);
  assert.equal(p.calls.filter(c=>c.path.endsWith('/start')).length,0);
});
test('C2.1 shared mode requires explicit choice and preserves original document with all required keys',async()=>{
  const source=clone(snapshot);source.document.cartridges[1].job_type='review';source.cartridges[1].model='review';
  const p=await page(path=>path==='/api/magazine/library/Demo'?source:undefined);await load(p);await mode(p,'shared');
  assert.equal(p.el('model-all').value,'');assert.equal(p.el('shared-model-control').classList.contains('hidden'),false);
  assert.equal(p.el('model-first').disabled,true);assert.equal(p.el('model-second').disabled,true);
  assert.equal(p.el('btn-create').disabled,true);await p.el('btn-create').emit('click');assert.equal(posts(p).length,0);
  assert.equal(p.el('prepare-help').textContent,'Choose an AI model for the workflow before continuing.');
  await shared(p,'other');await p.el('btn-create').click();
  assert.deepEqual(JSON.parse(posts(p)[0].body.magazine),source.document);
  assert.deepEqual(posts(p)[0].body.model_map,{research:'other',review:'other'});
});
test('C2.1 mode switches keep deliberate independent and shared choices without creating work',async()=>{
  const p=await page();await load(p);await chooseModel(p,'first');await chooseModel(p,'second','other');
  await mode(p,'shared');assert.equal(p.el('model-all').value,'');await shared(p,'other');
  await mode(p,'independent');assert.equal(p.el('model-first').value,'ornith:9b-q8_0');assert.equal(p.el('model-second').value,'other');
  await mode(p,'shared');assert.equal(p.el('model-all').value,'other');assert.equal(posts(p).length,0);
});
test('C2.1 entering shared mode never selects a model even if independent choices agree',async()=>{
  const p=await page();await load(p);await chooseModel(p,'first');await chooseModel(p,'second');await mode(p,'shared');
  assert.equal(p.el('model-all').value,'');
});
test('C2.1 same workflow retains choices, changed content resets mode and both caches',async()=>{
  let changed=false;const altered=clone(snapshot);altered.document.cartridges[0].prompt+=' changed';
  const p=await page(path=>path==='/api/magazine/library/Demo'&&changed?altered:undefined);await load(p);
  await chooseModel(p,'first','other');await mode(p,'shared');await shared(p,'other');await load(p);
  assert.equal(p.el('model-mode').value,'shared');assert.equal(p.el('model-all').value,'other');
  changed=true;await load(p);assert.equal(p.el('model-mode').value,'independent');assert.equal(p.el('model-first').value,'');
  await mode(p,'shared');assert.equal(p.el('model-all').value,'');
});
test('C2.1 one unselected independent step prevents admission despite original shared key',async()=>{
  const p=await page();await load(p);await chooseModel(p,'first');assert.equal(p.el('btn-create').disabled,true);await p.el('btn-create').emit('click');
  assert.equal(posts(p).length,0);assert.equal(p.el('prepare-help').textContent,'Choose an AI model for Step 2 before continuing.');
});
test('C2.1 unavailable cached choice is cleared on returning from shared, valid choice retained',async()=>{
  let expired=false;const p=await page((path,body,opts)=>{
    if(path.endsWith('/models')&&expired)return {models:[{id:'ornith:9b-q8_0'}]};
    if(path==='/api/magazine/runs'&&opts.method==='POST'){expired=true;return {_status:400,detail:{admission_errors:["model 'other' is not in the user's available models"]}};}
  });await load(p);await chooseModel(p,'first');await chooseModel(p,'second','other');await mode(p,'shared');await shared(p,'other');
  await p.el('btn-create').click();await mode(p,'independent');assert.equal(p.el('model-first').value,'ornith:9b-q8_0');
  assert.equal(p.el('model-second').value,'');assert.match(p.el('setup-summary').textContent,/no longer available/);
  assert.equal(p.el('btn-create').disabled,true);await p.el('btn-create').emit('click');assert.equal(posts(p).length,1);
});
test('C2.1 late catalogue refresh preserves newer explicit choices and never retries admission',async()=>{
  let release,refresh=false;const pending=new Promise(r=>release=r);
  const p=await page((path,body,opts)=>{
    if(path.endsWith('/models')&&refresh)return pending;
    if(path==='/api/magazine/runs'&&opts.method==='POST'){refresh=true;return {_status:400,detail:{admission_errors:['model is unavailable']}};}
  });await load(p);await chooseModel(p,'first');await chooseModel(p,'second');
  const creating=p.el('btn-create').click();await tick();await chooseModel(p,'second','other');
  release({models:[{id:'other'},{id:'ornith:9b-q8_0'}]});await creating;
  assert.equal(p.el('model-first').value,'ornith:9b-q8_0');assert.equal(p.el('model-second').value,'other');assert.equal(posts(p).length,1);
});
test('C2.1 single-step workflow omits mode and prepares with its explicit choice',async()=>{
  const source=clone(snapshot);source.cartridges.pop();source.document.cartridges.pop();
  const p=await page(path=>path==='/api/magazine/library/Demo'?source:undefined);await load(p);
  assert.equal(p.el('model-mode-control').classList.contains('hidden'),true);await chooseModel(p);await p.el('btn-create').click();
  assert.deepEqual(posts(p)[0].body.model_map,{first:'ornith:9b-q8_0'});
});
test('C2.1 prototype-like IDs are independent safe keys',async()=>{
  const source=clone(snapshot);source.document.cartridges.forEach((c,i)=>c.id=['__proto__','constructor'][i]);
  source.cartridges.forEach((c,i)=>c.id=source.document.cartridges[i].id);
  const p=await page(path=>path==='/api/magazine/library/Demo'?source:undefined);await load(p);
  await chooseModel(p,'__proto__');await chooseModel(p,'constructor','other');await p.el('btn-create').click();
  assert.deepEqual(posts(p)[0].body.model_map,JSON.parse('{"__proto__":"ornith:9b-q8_0","constructor":"other"}'));
});
test('C2.1 theme changes leave both modes and deliberate choices intact',async()=>{
  const p=await page();await load(p);await chooseModel(p,'first','other');
  for(const theme of ['dark','light','follow']){p.el('appearance').value=theme;await p.el('appearance').emit('change');
    assert.equal(p.el('model-first').value,'other');await mode(p,'shared');await shared(p,'ornith:9b-q8_0');await mode(p,'independent');
    assert.equal(p.el('model-first').value,'other');}
});
test('C2.1 editing new setup never changes existing persisted mapping or triggers recovery/start',async()=>{
  const persisted={acceptance:'historical-model'};const p=await page(path=>path==='/api/magazine/runs/test-run'?state('HALTED',{model_map:persisted}):undefined);
  await open(p,'test-run');const before=p.el('run-detail').textContent;await load(p);await chooseModel(p,'second','other');
  await mode(p,'shared');await shared(p,'other');assert.equal(p.el('run-detail').textContent,before);
  assert.deepEqual(persisted,{acceptance:'historical-model'});assert.equal(p.calls.filter(c=>c.method==='POST').length,0);
});
test('C2.1 three steps can deliberately choose three distinct models',async()=>{
  const source=clone(snapshot);source.document.cartridges.push({id:'third',job_type:'research',prompt:'THIRD'});
  source.cartridges.push({id:'third',model:'research',prompt:'THIRD',order:3});
  const p=await page(path=>path==='/api/magazine/library/Demo'?source:path.endsWith('/models')?{models:['Bonsai','Qwen','Ornith'].map(id=>({id}))}:undefined);
  await load(p);for(const [id,model] of [['first','Bonsai'],['second','Qwen'],['third','Ornith']])await chooseModel(p,id,model);
  await p.el('btn-create').click();assert.deepEqual(posts(p)[0].body.model_map,{first:'Bonsai',second:'Qwen',third:'Ornith'});
});
test('C2.1 changing setup during an uncertain preparation never changes its submitted document/map',async()=>{
  let release;const pending=new Promise(r=>release=r);const p=await page((path,body,opts)=>path==='/api/magazine/runs'&&opts.method==='POST'?pending:undefined);
  await load(p);await chooseModel(p,'first');await chooseModel(p,'second','other');const creating=p.el('btn-create').click();await tick();
  await mode(p,'shared');await shared(p,'other');await p.el('btn-create').emit('click');
  assert.equal(posts(p).length,1);assert.deepEqual(posts(p)[0].body.model_map,{first:'ornith:9b-q8_0',second:'other'});
  release(state());await creating;assert.equal(posts(p).length,1);assert.equal(p.el('model-mode').value,'shared');
});
test('C2.1 delayed initial discovery supplies options without selecting or changing modes',async()=>{
  let release;const pending=new Promise(r=>release=r);const p=await page(path=>path.endsWith('/models')?pending:undefined);
  await load(p);await mode(p,'shared');release({models:[{id:'other'}]});await tick();
  assert.equal(p.el('model-mode').value,'shared');assert.equal(p.el('model-all').value,'');assert.equal(posts(p).length,0);
});
test('C2.1 an undiscovered selection cannot prepare and shared wording matches its action',async()=>{
  const p=await page();await load(p);await chooseModel(p,'first');await chooseModel(p,'second','not-available');
  assert.equal(p.el('btn-create').disabled,true);await p.el('btn-create').emit('click');assert.equal(posts(p).length,0);await mode(p,'shared');
  assert.match(p.el('setup-summary').textContent,/AI model for the workflow/);await shared(p,'not-available');
  assert.equal(p.el('btn-create').disabled,true);await p.el('btn-create').emit('click');assert.equal(posts(p).length,0);
});
test('C2.1 JSON upload and its validated source stay unchanged when preparing independent models',async()=>{
  const source=clone(snapshot),raw=JSON.stringify(source.document),file={name:'example.json',text:raw};
  const p=await page(path=>path==='/api/magazine/packages'?source:undefined);p.el('file').files=[file];await p.el('btn-validate').click();
  await chooseModel(p,'first');await chooseModel(p,'second','other');await p.el('btn-create').click();
  assert.equal(file.text,raw);assert.equal(JSON.stringify(source.document),raw);assert.deepEqual(posts(p)[0].body.model_map,{first:'ornith:9b-q8_0',second:'other'});
});
