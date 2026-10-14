// Native semantics are checked structurally here; this facade is not a browser.
const {test}=require('node:test');const assert=require('node:assert/strict');
const {page,chooseModel,chooseShared,open,state,snapshot,html,textTree}=require('./ui_test_harness.cjs');
const models=Array.from({length:12},(_,i)=>({id:'mock-'+(i+1)}));
async function load(p){p.el('library-workflows').value='Demo';await p.el('btn-library-select').click();}
test('C2.3 reference information is clearly named and directs users to actual dropdowns',()=>{
  assert.match(html,/<summary>Model information<\/summary>/);
  assert.match(html,/Available models \(reference only\)/);
  assert.match(html,/Choose models using the dropdowns above\./);
  assert.match(html,/<pre id="models"/);
});
test('C2.3 each actual selector has a visible associated step-specific native label',async()=>{
  const p=await page();await load(p);
  p.el('cartridges').children.forEach((row,i)=>{
    const cell=row.children[2],label=cell.children[0],select=cell.children[1];
    assert.equal(label.tagName,'label');assert.equal(label.textContent,'AI model for Step '+(i+1));
    assert.equal(label.getAttribute('for'),select.id);assert.equal(select.tagName,'select');
  });
});
test('C2.3 twelve discovered models populate enabled empty native selectors, not the reference list',async()=>{
  const p=await page(url=>url.endsWith('/models')?{models}:undefined);await load(p);
  for(const id of ['first','second']){
    const select=p.el('model-'+id);assert.equal(select.tagName,'select');assert.equal(select.disabled,false);
    assert.deepEqual(select.children.map(o=>o.value),['',...models.map(m=>m.id)]);
    assert.equal(select.value,'');assert.equal(select.children[0].textContent,'Choose an AI model');
    assert.ok(!p.el('model-cell-'+id).classList.contains('hidden'));
  }
  assert.equal(p.el('models').tagName,'pre');assert.equal(p.el('models').onchange,undefined);
});
test('C2.3 usable selects remain focus-eligible by native attributes with no keyboard override',async()=>{
  const p=await page();await load(p);
  for(const id of ['first','second']){
    const select=p.el('model-'+id);assert.equal(select.disabled,false);
    assert.notEqual(select.getAttribute('tabindex'),'-1');assert.match(select.getAttribute('aria-label'),/AI model for step/);
  }
  assert.doesNotMatch(html,/onkeydown|addEventListener\("keydown"/);
});
test('C2.3 change handlers visibly retain separate choices and prepare the same internal assignment',async()=>{
  const original=JSON.stringify(snapshot);const p=await page();await load(p);
  await chooseModel(p,'first');await chooseModel(p,'second','other');
  assert.equal(p.el('model-first').value,'ornith:9b-q8_0');assert.equal(p.el('model-second').value,'other');
  assert.equal(p.calls.filter(c=>c.method==='POST').length,0);await p.el('btn-create').click();
  const body=p.calls.find(c=>c.method==='POST'&&c.path==='/api/magazine/runs').body;
  assert.deepEqual(body.model_map,{first:'ornith:9b-q8_0',second:'other'});
  assert.deepEqual(JSON.parse(body.magazine).cartridges.map(c=>c.job_type),['first','second']);
  assert.equal(JSON.stringify(snapshot),original);assert.equal(p.calls.filter(c=>c.path.endsWith('/start')).length,0);
});
test('C2.3 shared mode has one enabled model selector and returning restores independent choices',async()=>{
  const p=await page();await load(p);await chooseModel(p,'first');await chooseModel(p,'second','other');
  await chooseShared(p,'other');assert.equal(p.el('model-all').disabled,false);
  assert.equal(p.el('model-first').disabled,true);assert.equal(p.el('model-second').disabled,true);
  p.el('model-mode').value='independent';await p.el('model-mode').emit('change');
  assert.equal(p.el('model-first').value,'ornith:9b-q8_0');assert.equal(p.el('model-second').value,'other');
  assert.equal(p.calls.filter(c=>c.method==='POST').length,0);
});
test('C2.3 selections survive history/theme interactions; existing persisted mappings stay authoritative',async()=>{
  const p=await page(url=>url==='/api/magazine/runs/new'?state('READY',{run_id:'new',model_map:{acceptance:'historical'}}):undefined);
  await load(p);await chooseModel(p,'first','other');await chooseModel(p,'second');await open(p,'new');
  for(const theme of ['light','dark','follow']){p.el('appearance').value=theme;await p.el('appearance').emit('change');}
  assert.equal(p.el('model-first').value,'other');assert.equal(p.el('model-second').value,'ornith:9b-q8_0');
  assert.match(p.el('run-detail').textContent,/historical/);assert.equal(p.calls.filter(c=>c.method==='POST').length,0);
});
test('C2.3 unavailable models keep selectors disabled and do not admit a run',async()=>{
  const p=await page(url=>url.endsWith('/models')?{models:[]}:undefined);await load(p);
  assert.equal(p.el('model-first').disabled,true);assert.equal(p.el('btn-create').disabled,true);
  await p.el('btn-create').emit('click');assert.equal(p.calls.filter(c=>c.method==='POST').length,0);
});
