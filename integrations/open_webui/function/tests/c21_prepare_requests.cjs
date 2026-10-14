// Capture actual interface POST bodies for the offline Python boundary tests.
const fs=require('node:fs'),crypto=require('node:crypto'),assert=require('node:assert/strict');
const {page,chooseModel,chooseShared}=require('./ui_test_harness.cjs');
const path=require('node:path');
async function capture(){
  const root=path.join(__dirname,'fixtures/Projects-Smoke');
  const document={schema_version:'1.0',cartridges:[
    {id:'first',job_type:'acceptance',prompt:fs.readFileSync(path.join(root,'cartridges/99-first.md'),'utf8')},
    {id:'second',job_type:'acceptance',prompt:fs.readFileSync(path.join(root,'cartridges/01-second.md'),'utf8')}]};
  const preview={valid:true,name:'Magazine Projects Smoke',schema_version:'1.0',document,
    cartridges:document.cartridges.map((c,i)=>({order:i+1,id:c.id,model:c.job_type,prompt:c.prompt}))};
  const before=JSON.stringify(preview),requests={};
  for(const mode of ['independent','shared']){
    const p=await page(path=>{
      if(path==='/api/magazine/library')return {available:true,can_import:true,workflows:[{workflow_id:'Smoke',name:preview.name,valid:true,cartridge_count:2}]};
      if(path==='/api/magazine/library/Smoke')return preview;
      if(path.endsWith('/models'))return {models:[{id:'mock-first'},{id:'mock-second'}]};
    });p.el('library-workflows').value='Smoke';await p.el('btn-library-select').click();
    if(mode==='shared')await chooseShared(p,'mock-first');
    else{await chooseModel(p,'first','mock-first');await chooseModel(p,'second','mock-second');}
    await p.el('btn-create').click();const posts=p.calls.filter(c=>c.path==='/api/magazine/runs'&&c.method==='POST');
    assert.equal(posts.length,1);assert.equal(p.calls.filter(c=>c.path.endsWith('/start')).length,0);
    requests[mode]=posts[0].body;
    assert.equal(JSON.stringify(preview),before,'canonical validated snapshot was modified');
  }
  return {source_sha256:crypto.createHash('sha256').update(fs.readFileSync(path.join(__dirname,'../magazine_ef.py'))).digest('hex'),
    validated_document:document,requests};
}
if(require.main===module)capture().then(result=>process.stdout.write(JSON.stringify(result,null,2)+'\n')).catch(e=>{console.error(e);process.exitCode=1;});
module.exports={capture};
