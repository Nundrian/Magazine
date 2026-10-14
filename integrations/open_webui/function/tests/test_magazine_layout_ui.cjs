// C2.2: actual markup structure and script behaviour, without a browser dependency.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const {html,page,chooseModel,chooseShared,open,state}=require('./ui_test_harness.cjs');
function parse(source){
  const root={tag:'root',attrs:{},children:[]},stack=[root];let end=0;
  for(const match of source.split('<script>')[0].matchAll(/<\/?[\w-]+\b[^>]*>/g)){
    const text=source.slice(end,match.index).trim();if(text)stack.at(-1).children.push({tag:'#text',text,attrs:{},children:[]});
    end=match.index+match[0].length;
    if(match[0].startsWith('</')){stack.pop();continue;}
    const tag=match[0].match(/^<([\w-]+)/)[1],attrs={};
    const raw=match[0].slice(tag.length+1,-1);
    for(const a of raw.matchAll(/([\w:-]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?/g))attrs[a[1]]=a[2]??a[3]??a[4]??'';
    const node={tag,attrs,children:[],parent:stack.at(-1)};stack.at(-1).children.push(node);
    if(!['meta','input','br','hr','img','link'].includes(tag))stack.push(node);
  }
  return root;
}
const dom=parse(html);
function all(node,predicate){return [...(predicate(node)?[node]:[]),...node.children.flatMap(c=>all(c,predicate))];}
const byId=(id,root=dom)=>all(root,n=>n.attrs.id===id)[0];
const hasClass=(node,name)=>(node.attrs.class||'').split(/\s+/).includes(name);
const children=node=>node.children.filter(c=>c.tag!=='#text');
const text=node=>node.tag==='#text'?node.text:node.children.map(text).join(' ');
function rules(source){return source.match(/<style>([\s\S]*?)<\/style>/)[1];}
function rule(selector){const escaped=selector.replace(/[.*+?^${}()|[\]\\]/g,'\\$&');return rules(html).match(new RegExp(escaped+'\\s*\\{([^}]+)\\}'))?.[1]||'';}
function field(id,label){
  const input=byId(id),group=input.parent;assert.ok(hasClass(group,'field-group'),id+' uses a stacked group');
  const parts=children(group);assert.equal(parts[0].tag,'label');assert.equal(parts[0].attrs.for,id);
  assert.equal(text(parts[0]),label);assert.equal(parts[1],input);return parts;
}
test('C2.2 Workflow and Previous work labels precede a separate expanding control row',()=>{
  for(const [id,label,buttons] of [['library-workflows','Workflow',['btn-library-select','btn-library-refresh']],
    ['history-runs','Previous work',['btn-history-open','btn-history-refresh']]]){
    const input=byId(id),row=input.parent,group=row.parent;assert.ok(hasClass(row,'field-actions'));
    assert.ok(hasClass(group,'field-group'));const parts=children(group);
    assert.equal(parts[0].tag,'label');assert.equal(parts[0].attrs.for,id);assert.equal(text(parts[0]),label);
    assert.equal(parts[1],row);assert.deepEqual(children(row).map(n=>n.attrs.id),[id,...buttons]);
  }
});
test('C2.2 Package file keeps its native chooser and help beneath it',()=>{
  const parts=field('file','Package file');assert.equal(parts[1].tag,'input');assert.equal(parts[1].attrs.type,'file');
  assert.equal(parts[1].attrs.accept,'.magazine,.json,.zip');assert.equal(parts[2].tag,'span');
  assert.equal(text(parts[2]),'Use a .magazine package or a JSON file.');
});
test('C2.2 JSON and both model-selection controls use the same label-above pattern',()=>{
  field('json','Or paste JSON');field('model-mode','AI model selection');field('model-all','AI model for all steps');
  assert.equal(byId('json').attrs.rows,'3');assert.equal(byId('json').attrs.spellcheck,'false');
  assert.ok(hasClass(byId('model-mode-control'),'hidden'));assert.ok(hasClass(byId('shared-model-control'),'hidden'));
});
test('C2.2 import ZIP and optional short name have consistent stacked labels',()=>{
  field('project-zip','Workflow ZIP');field('project-id','Short name (optional)');
  assert.equal(byId('project-zip').attrs.accept,'.zip');assert.equal(byId('project-id').attrs.maxlength,'64');
  assert.equal(text(byId('btn-project-import')),'Add a workflow');
});
test('C2.2 stacked groups have a consistent gap without changing unrelated rows',()=>{
  assert.match(rule('.field-group'),/display:\s*flex/);assert.match(rule('.field-group'),/flex-direction:\s*column/);
  assert.match(rule('.field-group'),/gap:\s*6px/);assert.match(rule('.field-group'),/min-width:\s*0/);
  assert.match(rule('.field-group + .field-group'),/margin-top:\s*10px/);
});
test('C2.2 control rows can shrink long dropdowns and wrap buttons at narrow or zoomed widths',()=>{
  assert.match(rule('.field-actions'),/display:\s*flex/);assert.match(rule('.field-actions'),/flex-wrap:\s*wrap/);
  assert.match(rule('.field-actions'),/min-width:\s*0/);
  assert.match(rule('.field-actions > select'),/flex:\s*1 1 260px/);
  assert.match(rule('.field-actions > select'),/min-width:\s*0/);
  assert.match(rule('.field-actions > select'),/max-width:\s*100%/);
  assert.match(rule('.field-actions > button'),/flex:\s*0 0 auto/);
  assert.match(rule('.field-actions > button'),/max-width:\s*100%/);
  assert.match(rule('.field-group > input,.field-group > select,.field-group > textarea'),/min-width:\s*0/);
});
test('C2.2 release markup keeps unique IDs and explicit accessibility relationships',()=>{
  const ids=all(dom,n=>!!n.attrs.id).map(n=>n.attrs.id);
  assert.equal(ids.length,new Set(ids).size);
  assert.match(html,/<header>[\s\S]*?<h1>Magazine<\/h1>[\s\S]*?<\/header>/);
  assert.equal(byId('library-workflows').attrs['aria-describedby'],'library-guidance library-msg');
  assert.equal(byId('tools').attrs.role,'group');
  assert.equal(byId('tools').attrs['aria-labelledby'],'tools-heading');
  assert.equal(byId('tool-count').attrs.role,'status');
  assert.equal(byId('btn-create').attrs['aria-describedby'],'prepare-note prepare-help');
});
test('C2.2 existing workflow, history, upload and execution handlers remain attached',async()=>{
  const p=await page();
  for(const [id,event] of [['btn-library-select','click'],['btn-library-refresh','click'],['file','change'],
    ['json','input'],['btn-validate','click'],['btn-project-import','click'],
    ['model-mode','change'],['appearance','change'],['history-runs','change'],['btn-history-open','click'],
    ['btn-history-refresh','click'],['btn-create','click'],['btn-start','click'],['btn-recover','click']]){
    assert.ok(p.el(id).listeners[event]?.length || typeof p.el(id)['on'+event]==='function',id+' '+event);
  }
});
test('C2.2 long catalogue/history descriptions and theme interactions never prepare or start',async()=>{
  const long='A long workflow description '.repeat(30);
  const p=await page((url,body,opts)=>{
    if(url==='/api/magazine/library')return {available:true,can_import:true,workflows:[{workflow_id:'Demo',name:long,valid:true,cartridge_count:2}]};
    if(url==='/api/magazine/runs'&&opts.method==='GET')return {runs:[state('COMPLETED',{run_id:'old',name:long,done:['first','second'],todo:[]})]};
  });assert.match(p.el('library-workflows').children[0].textContent,/long workflow/);
  p.el('library-workflows').value='Demo';await p.el('btn-library-select').click();
  for(const theme of ['light','dark','follow']){p.el('appearance').value=theme;await p.el('appearance').emit('change');}
  await open(p,'old');await p.el('btn-history-refresh').click();
  assert.equal(p.calls.filter(c=>c.method==='POST').length,0);
});
test('C2.2 independent/shared choices remain deliberate and restoring modes sends no writes',async()=>{
  const p=await page();p.el('library-workflows').value='Demo';await p.el('btn-library-select').click();
  await chooseModel(p,'first');await chooseModel(p,'second','other');assert.equal(p.el('model-first').value,'ornith:9b-q8_0');
  await chooseShared(p,'other');assert.equal(p.el('model-first').disabled,true);
  p.el('model-mode').value='independent';await p.el('model-mode').emit('change');
  assert.equal(p.el('model-first').value,'ornith:9b-q8_0');assert.equal(p.el('model-second').value,'other');
  assert.equal(p.calls.filter(c=>c.method==='POST').length,0);
});
