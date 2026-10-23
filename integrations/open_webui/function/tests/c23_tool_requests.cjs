// Actual inline UI -> captured request -> offline HTTP admission tests.
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto'),assert=require('node:assert/strict');
const {page,chooseModel,snapshot}=require('./ui_test_harness.cjs');
const tools=[{id:'tool-a',name:'A descriptive tool name'},
  {id:'tool-b',name:'LongUnbroken'.repeat(60)}, {id:'tool-c',name:'Third tool'}];
async function capture(){
  const requests={},before=JSON.stringify(snapshot);
  for(const mode of ['default','excluded','cleared','reselected']){
    const p=await page(url=>url.endsWith('/tools')?{tools}:
      url.endsWith('/models')?{models:[{id:'mock-first'},{id:'mock-second'}]}:undefined);
    p.el('library-workflows').value='Demo';await p.el('btn-library-select').click();
    if(mode==='excluded'){
      // Explicitly grant the set before testing a single deselection.
      await p.el('btn-tools-select-all').click();
      const box=p.el('tool-tool-b');box.checked=false;await box.onchange.call(box);
    }
    if(mode==='cleared'||mode==='reselected')await p.el('btn-tools-clear-all').click();
    if(mode==='reselected')await p.el('btn-tools-select-all').click();
    await chooseModel(p,'first','mock-first');await chooseModel(p,'second','mock-second');
    await p.el('btn-create').click();
    const posts=p.calls.filter(c=>c.path==='/api/magazine/runs'&&c.method==='POST');
    assert.equal(posts.length,1);assert.equal(p.calls.filter(c=>c.path.endsWith('/start')).length,0);
    const expected=mode==='excluded'?['tool-a','tool-c']:mode==='reselected'?['tool-a','tool-b','tool-c']:[];
    assert.deepEqual(posts[0].body.tool_ids,expected);requests[mode]=posts[0].body;
    assert.equal(JSON.stringify(snapshot),before);
  }
  return {source_sha256:crypto.createHash('sha256').update(fs.readFileSync(path.join(__dirname,'../magazine_ef.py'))).digest('hex'),
    validated_document:snapshot.document,tools,requests};
}
if(require.main===module)capture().then(result=>process.stdout.write(JSON.stringify(result,null,2)+'\n')).catch(e=>{console.error(e);process.exitCode=1;});
module.exports={capture};
