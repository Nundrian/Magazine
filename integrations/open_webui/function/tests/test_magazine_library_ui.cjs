// C1 regressions exercise the same real script as C2 through a shared test facade.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {page, tick, snapshot, html, chooseModel, chooseShared} = require('./ui_test_harness.cjs');

test('library buttons load catalogue, preview manifest order and create existing READY request',async()=>{
  const p=await page();assert.ok(p.el('btn-library-refresh'));
  await p.el('btn-library-refresh').click();
  assert.equal(p.el('library-workflows').children.length,2);
  assert.match(p.el('library-workflows').children[0].textContent,/Demo/);
  assert.equal(p.el('library-workflows').children[1].disabled,true);
  p.el('library-workflows').value='Demo';await p.el('btn-library-select').click();
  assert.equal(p.el('cartridge-s').classList.contains('hidden'),false);
  assert.equal(p.el('btn-create').disabled,true);assert.match(p.el('prepare-help').textContent,/AI model for Step 1/);
  assert.deepEqual(p.el('cartridges').children.map(r=>r.children[1].textContent),['first','second']);
  assert.ok(p.el('pkg-doc').textContent.includes('FIRST <script>prompt</script>'));
  assert.equal(p.el('cartridges').children[0].children[3].textContent,'FIRST <script>prompt</script>');
  await chooseShared(p);
  await p.el('btn-create').click();
  const posted=p.calls.find(c=>c.path==='/api/magazine/runs' && c.method==='POST').body;
  assert.deepEqual(JSON.parse(posted.magazine),snapshot.document);
  assert.deepEqual(posted.model_map,{research:'ornith:9b-q8_0'});assert.deepEqual(posted.tool_ids,[]);
  assert.ok(!('workflow_id' in posted));assert.ok(!('owner_id' in posted));
});

test('unavailable catalogue is nonfatal and pasted JSON validation still works',async()=>{
  const p=await page(path=>path==='/api/magazine/library'?{available:false,workflows:[],message:'Library unavailable'}:undefined);
  assert.match(p.el('library-msg').textContent,/unavailable/i);
  p.el('json').value=JSON.stringify(snapshot.document);await p.el('btn-validate').click();
  assert.deepEqual(p.calls.find(c=>c.path==='/api/magazine/packages').body,
    {document_json:JSON.stringify(snapshot.document),file_type:'json'});
  assert.equal(p.el('btn-create').disabled,true);assert.equal(p.el('cartridge-s').classList.contains('hidden'),false);
});

test('JSON file upload sends its text to the existing package validator',async()=>{
  const p=await page();p.el('file').files=[{name:'demo.json',text:JSON.stringify(snapshot.document)}];
  await p.el('btn-validate').click();
  const body=p.calls.find(c=>c.path==='/api/magazine/packages').body;
  assert.equal(body.file_type,'json');assert.equal(body.document_json,JSON.stringify(snapshot.document));
});

test('magazine archive upload sends base64 rather than an ArrayBuffer',async()=>{
  const p=await page();p.el('file').files=[{name:'demo.magazine',b64:'UEsDBA=='}];
  await p.el('btn-validate').click();
  const body=p.calls.find(c=>c.path==='/api/magazine/packages').body;
  assert.equal(body.file_type,'magazine');assert.equal(body.file_data_b64,'UEsDBA==');
});

test('failed recheck preserves prior validated configuration with explicit feedback and no automatic READY creation',async()=>{
  let bad=false;
  const p=await page(path=>path==='/api/magazine/library/Demo' && bad?{valid:false,error:'Invalid YAML'}:undefined);
  p.el('library-workflows').value='Demo';await p.el('btn-library-select').click();
  await chooseModel(p,'first');await chooseModel(p,'second');const before=p.el('pkg-doc').textContent;
  bad=true;await p.el('btn-library-select').click();
  assert.equal(p.el('btn-create').disabled,false);assert.equal(p.el('pkg-doc').textContent,before);
  assert.match(p.el('library-msg').textContent,/couldn't load/);assert.match(p.el('library-msg').textContent,/still Demo/);
  assert.match(p.el('error-detail').textContent,/Invalid YAML/);
  assert.equal(p.calls.filter(c=>c.method==='POST').length,0);
});

test('editing source inputs invalidates a selected document',async()=>{
  const p=await page();p.el('library-workflows').value='Demo';await p.el('btn-library-select').click();
  await p.el('json').emit('input');assert.equal(p.el('btn-create').disabled,true);
  await p.el('btn-library-select').click();await p.el('file').emit('change');
  assert.equal(p.el('btn-create').disabled,true);
});

test('late preview response cannot restore a document after user edits',async()=>{
  let release;const pending=new Promise(resolve=>release=resolve);
  const p=await page(path=>path==='/api/magazine/library/Demo'?pending:undefined);
  p.el('library-workflows').value='Demo';const selecting=p.el('btn-library-select').click();
  await tick();await p.el('json').emit('input');release(snapshot);await selecting;
  assert.equal(p.el('btn-create').disabled,true);
});

test('refresh catalogue preserves already validated immutable snapshot',async()=>{
  const p=await page();p.el('library-workflows').value='Demo';await p.el('btn-library-select').click();
  await chooseModel(p,'first');await chooseModel(p,'second');
  const before=p.el('pkg-doc').textContent;await p.el('btn-library-refresh').click();
  assert.equal(p.el('pkg-doc').textContent,before);assert.equal(p.el('btn-create').disabled,false);
});

test('READY response arriving after an input edit cannot enable an invalid document',async()=>{
  let release;const pending=new Promise(resolve=>release=resolve);
  const p=await page(path=>path==='/api/magazine/runs'?pending:undefined);
  p.el('library-workflows').value='Demo';await p.el('btn-library-select').click();
  await chooseModel(p);
  await chooseModel(p,'second');
  const creating=p.el('btn-create').click();await tick();
  await p.el('json').emit('input');release({run_id:'already-admitted',status:'READY'});
  await creating;assert.equal(p.el('btn-create').disabled,true);
});

test('Magazine Projects imports ZIP, refreshes and selects identifier without executing',async()=>{
  let imported=false;
  const p=await page((path,body)=>{
    if(path==='/api/magazine/library/import') {
      imported=true;assert.equal(body.file_name,'Uploaded.zip');assert.equal(body.file_data_b64,'UEsDBA==');
      return {imported:true,project_id:'Uploaded',name:'Uploaded',cartridge_count:2};
    }
    if(path==='/api/magazine/library' && imported) return {available:true,can_import:true,workflows:[
      {workflow_id:'Uploaded',name:'Uploaded',valid:true,cartridge_count:2}],message:''};
  });
  assert.ok(html.includes('Your workflows'));
  p.el('project-zip').files=[{name:'Uploaded.zip',b64:'UEsDBA=='}];
  await p.el('btn-project-import').click();
  assert.equal(p.el('library-workflows').value,'Uploaded');
  assert.match(p.el('project-import-msg').textContent,/Added/i);
  assert.equal(p.el('btn-create').disabled,true);
  assert.ok(!p.calls.some(c=>c.method==='POST' && (c.path==='/api/magazine/runs' || c.path.endsWith('/start'))));
});

test('non-admin can browse projects but import control is disabled',async()=>{
  const p=await page(path=>path==='/api/magazine/library'?{available:true,can_import:false,workflows:[
    {workflow_id:'Demo',name:'Demo',valid:true,cartridge_count:2}],message:'Select project'}:undefined);
  assert.equal(p.el('btn-project-import').disabled,true);
  assert.equal(p.el('btn-library-select').disabled,false);
});

test('invalid ZIP import shows error and keeps existing validated snapshot',async()=>{
  const p=await page(path=>path==='/api/magazine/library/import'?{imported:false,detail:'Duplicate project'}:undefined);
  p.el('library-workflows').value='Demo';await p.el('btn-library-select').click();
  await chooseModel(p,'first');await chooseModel(p,'second');
  const before=p.el('pkg-doc').textContent;
  p.el('project-zip').files=[{name:'Demo.zip',b64:'UEsDBA=='}];await p.el('btn-project-import').click();
  assert.match(p.el('error-detail').textContent,/Duplicate project/);
  assert.equal(p.el('pkg-doc').textContent,before);assert.equal(p.el('btn-create').disabled,false);
});

test('unavailable model admission is shown as a readable error',async()=>{
  const p=await page(path=>path==='/api/magazine/runs'?{_status:400,
    detail:{admission_errors:['Selected model is not available to this user']}}:undefined);
  p.el('library-workflows').value='Demo';await p.el('btn-library-select').click();
  await chooseModel(p);
  await chooseModel(p,'second');
  await p.el('btn-create').click();
  assert.match(p.el('create-msg').textContent,/AI model isn't available/);
  assert.match(p.el('error-detail').textContent,/Selected model is not available/);
  assert.ok(!p.el('create-msg').textContent.includes('[object Object]'));
});

test('valid project model keys cannot collide with JavaScript object prototypes',async()=>{
  for (const key of ['__proto__','constructor']) {
    const preview=JSON.parse(JSON.stringify(snapshot));
    preview.cartridges.forEach(c=>c.model=key);
    preview.document.cartridges.forEach(c=>c.job_type=key);
    const p=await page(path=>path==='/api/magazine/library/Demo'?preview:undefined);
    p.el('library-workflows').value='Demo';await p.el('btn-library-select').click();
    await chooseShared(p);
    await p.el('btn-create').click();
    assert.equal(p.calls.find(c=>c.path==='/api/magazine/runs' && c.method==='POST').body.model_map[key],'ornith:9b-q8_0');
  }
});
