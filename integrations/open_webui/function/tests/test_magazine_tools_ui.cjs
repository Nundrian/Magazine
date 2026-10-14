// C2.3 requested tool defaults, retained exclusions and adaptive native layout.
const {test}=require('node:test');const assert=require('node:assert/strict');
const {page,chooseModel,chooseShared,open,snapshot,tick,html,textTree}=require('./ui_test_harness.cjs');
const tools=n=>Array.from({length:n},(_,i)=>({id:'tool-'+(i+1),name:i%3===1?'A longer tool name that should wrap naturally '+i:'Tool '+(i+1)}));
async function load(p){p.el('library-workflows').value='Demo';await p.el('btn-library-select').click();}
async function make(list){return page(url=>url.endsWith('/tools')?{tools:list}:undefined);}
function boxes(p){return p.el('tools').children.map(row=>row.children[0].children[0]);}
async function check(p,id,value){const box=p.el('tool-'+id);box.checked=value;await box.onchange.call(box);}
for(const n of [0,1,2,3,10,30,100])test('C2.3 '+n+' tools start selected for a newly configured workflow with an exact count',async()=>{
  const p=await make(tools(n));await load(p);assert.equal(boxes(p).length,n);
  assert.ok(boxes(p).every(b=>b.checked&&!b.disabled));assert.equal(p.el('tool-count').textContent,'Tools selected: '+n+' of '+n);
  assert.equal(p.el('btn-tools-select-all').disabled,n===0);assert.equal(p.el('btn-tools-clear-all').disabled,n===0);
  if(n===0)assert.match(p.el('tools-empty').textContent,/No tools/);
});
test('C2.3 full long and unbroken tool names remain safe text with native checkbox labels',async()=>{
  const names=['A long descriptive tool name '.repeat(15),'unbroken'.repeat(100),'<img src=x onerror=alert(1)>'];
  const p=await make(names.map((name,i)=>({id:'id-'+i,name})));await load(p);
  p.el('tools').children.forEach((row,i)=>{
    const label=row.children[0],box=label.children[0],content=label.children[1];
    assert.equal(label.tagName,'label');assert.equal(label.getAttribute('for'),box.id);
    assert.equal(box.tagName,'input');assert.equal(box.type,'checkbox');assert.equal(box.disabled,false);
    assert.equal(content.children[0].textContent,names[i]);assert.equal(box.getAttribute('tabindex'),undefined);
    assert.ok(!content.children[0].textContent.includes('id-'+i));
  });
});
test('C2.3 adaptive list caps height without fixing it; narrow layout, wrapping and non-shrinking checkboxes are scoped',()=>{
  const css=html.match(/<style>([\s\S]*?)<\/style>/)[1];
  const grid=css.match(/\.tool-grid\s*\{([^}]+)\}/)[1];
  assert.match(grid,/max-height:\s*min\(24rem,50vh\)/);assert.doesNotMatch(grid,/(?:^|;)height:/);
  assert.match(grid,/overflow-y:\s*auto/);assert.match(grid,/overflow-x:\s*hidden/);
  assert.match(grid,/minmax\(min\(100%,18rem\),1fr\)/);assert.match(css,/\.tool-grid\{grid-template-columns:1fr\}/);
  assert.match(css,/\.tool \.tn,\.tool \.td\{display:block;overflow-wrap:anywhere\}/);
  assert.match(css,/\.tool input\[type=checkbox\]\{flex:0 0 auto/);
  assert.match(css,/\.tool label\{display:flex;align-items:flex-start;gap:8px;min-width:0/);
  assert.match(html,/id="tools"[^>]*role="group"[^>]*aria-labelledby="tools-heading"/);
  assert.doesNotMatch(html,/onkeydown|addEventListener\("keydown"/);
});
test('C2.3 tool count and bulk controls are outside the scrolling list and remain enabled independently of exclusions',async()=>{
  const p=await make(tools(30));await load(p);
  const grid=html.indexOf('<div id="tools"');for(const id of ['tool-count','btn-tools-select-all','btn-tools-clear-all']){
    assert.ok(html.indexOf('id="'+id+'"')<grid);
  }
  await p.el('btn-tools-clear-all').click();assert.ok(boxes(p).every(b=>!b.checked));
  assert.equal(p.el('tool-count').textContent,'Tools selected: 0 of 30');
  await p.el('btn-tools-select-all').click();assert.ok(boxes(p).every(b=>b.checked));
  assert.equal(p.el('tool-count').textContent,'Tools selected: 30 of 30');assert.equal(p.calls.filter(c=>c.method==='POST').length,0);
});
test('C2.3 exclusions survive models, modes, themes, history and rechecking the same workflow',async()=>{
  const p=await make(tools(3));await load(p);await check(p,'tool-2',false);
  await chooseModel(p,'first');await chooseModel(p,'second','other');await chooseShared(p,'other');
  p.el('model-mode').value='independent';await p.el('model-mode').emit('change');
  p.el('appearance').value='dark';await p.el('appearance').emit('change');await open(p,'new');await load(p);
  assert.equal(p.el('tool-tool-2').checked,false);assert.equal(p.el('tool-count').textContent,'Tools selected: 2 of 3');
  assert.equal(p.el('model-first').value,'ornith:9b-q8_0');assert.equal(p.calls.filter(c=>c.method==='POST').length,0);
});
test('C2.3 a genuinely different workflow resets tool defaults without rewriting its source',async()=>{
  let altered=false;const changed=JSON.parse(JSON.stringify(snapshot));changed.document.cartridges[0].prompt='A different workflow';
  const original=JSON.stringify(changed);const p=await page(url=>url.endsWith('/tools')?{tools:tools(3)}:
    url==='/api/magazine/library/Demo'&&altered?changed:undefined);
  await load(p);await check(p,'tool-2',false);altered=true;await load(p);
  assert.ok(boxes(p).every(b=>b.checked));assert.equal(JSON.stringify(changed),original);
});
test('C2.3 exact selected tool IDs are frozen in the prepared body; names are not routing identifiers',async()=>{
  const p=await make(tools(3));await load(p);await check(p,'tool-2',false);await chooseModel(p,'first');await chooseModel(p,'second','other');
  await p.el('btn-create').click();const body=p.calls.find(c=>c.method==='POST'&&c.path==='/api/magazine/runs').body;
  assert.deepEqual(body.tool_ids,['tool-1','tool-3']);assert.deepEqual(body.model_map,{first:'ornith:9b-q8_0',second:'other'});
  await p.el('btn-tools-clear-all').click();assert.deepEqual(body.tool_ids,['tool-1','tool-3']);
  assert.equal(p.calls.filter(c=>c.path.endsWith('/start')).length,0);
});
test('C2.3 no workflow means no active tool selection; delayed discovery applies defaults to the current setup',async()=>{
  let release;const pending=new Promise(r=>release=r);const p=await page(url=>url.endsWith('/tools')?pending:undefined);
  assert.equal(p.el('btn-tools-select-all').disabled,true);await load(p);assert.equal(p.el('btn-create').disabled,true);
  release({tools:tools(3)});await tick();assert.ok(boxes(p).every(b=>b.checked));
  assert.equal(p.el('tool-count').textContent,'Tools selected: 3 of 3');assert.equal(p.el('btn-create').disabled,true);
  await chooseModel(p,'first');await chooseModel(p,'second');assert.equal(p.el('btn-create').disabled,false);
  await p.el('json').emit('input');assert.ok(boxes(p).every(b=>b.disabled));assert.equal(p.el('btn-tools-clear-all').disabled,true);
});
test('C2.3 discovery failure is not misreported as zero available tools and prevents preparation',async()=>{
  const p=await page(url=>url.endsWith('/tools')?{_status:503,detail:'Discovery unavailable'}:undefined);await load(p);
  assert.match(p.el('tools-empty').textContent,/couldn't load/);assert.equal(p.el('btn-create').disabled,true);
  await p.el('btn-create').emit('click');assert.equal(p.calls.filter(c=>c.method==='POST').length,0);
});
test('C2.3 prototype-like tool IDs remain explicit selections, not object properties',async()=>{
  const p=await make([{id:'__proto__',name:'Prototype tool'},{id:'constructor',name:'Constructor tool'}]);await load(p);
  await check(p,'__proto__',false);await chooseModel(p,'first');await chooseModel(p,'second');await p.el('btn-create').click();
  assert.deepEqual(p.calls.find(c=>c.method==='POST').body.tool_ids,['constructor']);
});
