// Test-only DOM/fetch/clock facade for the real inline Magazine script.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(`${__dirname}/../magazine_ef.py`, 'utf8');
const html = source.match(/_HTML\s*=\s*r?"""([\s\S]*?)"""/)[1];
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
const tick = () => new Promise(resolve => setImmediate(resolve));
const snapshot = {valid:true, name:'Demo <safe>', schema_version:'1.0',
  cartridges:[{order:1,id:'first',model:'research',prompt:'FIRST <script>prompt</script>'},
              {order:2,id:'second',model:'research',prompt:'SECOND prompt'}],
  document:{schema_version:'1.0',cartridges:[
    {id:'first',job_type:'research',prompt:'FIRST <script>prompt</script>'},
    {id:'second',job_type:'research',prompt:'SECOND prompt'}]}};
const state = (status='READY',extra={}) => ({run_id:'test-run',status,done:[],todo:['first','second'],
  current_cartridge:null,revision:0,execution:null,created_at:'2026-10-12T10:00:00Z',...extra});

async function page(override = () => undefined, config = {}) {
  const elements=new Map(), all=[], calls=[], timers=new Map(), storage=config.storage || new Map(), copied=[];
  let timerId=0;
  class Element {
    constructor(tag='div') {
      this.tagName=tag;this.children=[];this.listeners={};this.dataset={};this.attributes={};
      this.value='';this.files=[];this.textContent='';this.disabled=false;this.checked=false;
      this.classes=new Set();all.push(this);
      this.classList={add:(...ns)=>ns.forEach(n=>this.classes.add(n)),
        remove:(...ns)=>ns.forEach(n=>this.classes.delete(n)),contains:n=>this.classes.has(n)};
    }
    set id(value){this._id=value;elements.set(value,this);} get id(){return this._id;}
    set className(value){this.classes=new Set(value.split(/\s+/));} get className(){return [...this.classes].join(' ');}
    set innerHTML(value){assert.equal(value,'','untrusted HTML must never be assigned');this.children=[];}
    appendChild(child){this.children.push(child);return child;}
    setAttribute(key,value){this.attributes[key]=String(value);if(key==='data-theme')this.dataset.theme=String(value);}
    getAttribute(key){return this.attributes[key];}
    addEventListener(name,fn){(this.listeners[name] ||= []).push(fn);}
    async emit(name){for(const fn of this.listeners[name]||[])await fn.call(this);}
    async click(){assert.equal(this.disabled,false,'button must be enabled');await this.emit('click');}
  }
  for(const match of html.matchAll(/<([\w-]+)\b([^>]*\bid="([^"]+)"[^>]*)>/g)){
    const e=new Element(match[1]);e.id=match[3];
    const cls=match[2].match(/class="([^"]+)"/);if(cls)e.className=cls[1];
    e.disabled=/\bdisabled\b/.test(match[2]);
  }
  const root=new Element('html');if(config.owuiDark)root.classList.add('dark');
  const document={documentElement:root,getElementById:id=>elements.get(id),createElement:tag=>new Element(tag),
    querySelectorAll:()=>all.filter(e=>e.tagName==='input' && e.type==='checkbox')};
  class FileReader {
    readAsText(file){this.result=file.text;queueMicrotask(()=>this.onload());}
    readAsDataURL(file){this.result='data:application/octet-stream;base64,'+file.b64;queueMicrotask(()=>this.onload());}
  }
  const fetch=async(path,opts)=>{
    const body=opts.body?JSON.parse(opts.body):null;calls.push({path,body,method:opts.method,signal:opts.signal});
    let data=await override(path,body,opts);
    if(data===undefined){
      if(path.endsWith('/models'))data={models:[{id:'ornith:9b-q8_0'},{id:'other'}]};
      else if(path.endsWith('/tools'))data={tools:[]};
      else if(path==='/api/magazine/library')data={available:true,can_import:true,workflows:[
        {workflow_id:'Demo',name:'Demo <safe>',valid:true,cartridge_count:2},
        {workflow_id:'Bad',name:'Bad',valid:false,error:'Invalid YAML',cartridge_count:0}],message:''};
      else if(path==='/api/magazine/library/Demo'||path==='/api/magazine/packages')data=snapshot;
      else if(path==='/api/magazine/runs'&&opts.method==='GET')data={runs:[state(),state('READY',{run_id:'new'})],count:2};
      else if(path==='/api/magazine/runs')data={...state(),model_map:body.model_map,tool_ids:body.tool_ids};
      else if(path.endsWith('/results'))data={run_id:path.split('/')[4],status:'READY',done:[],results:{}};
      else if(path.startsWith('/api/magazine/runs/'))data=state('READY',{run_id:path.split('/')[4]});
      else throw Error('unexpected path '+path);
    }
    const status=data._status||200;
    return {ok:status<400,status,text:async()=>JSON.stringify(data)};
  };
  let themeObserver, mediaListener;
  class MutationObserver {constructor(fn){themeObserver=fn;}observe(){}disconnect(){}}
  const media={matches:!!config.osDark,addEventListener:(name,fn)=>mediaListener=fn};
  const localStorage={getItem:key=>{if(config.storageError)throw Error('denied');return storage.get(key)||null;},
    setItem:(key,value)=>{if(config.storageError)throw Error('denied');storage.set(key,value);}};
  const context={document,fetch,FileReader,console,encodeURIComponent,AbortController,MutationObserver,
    localStorage,navigator:{clipboard:{writeText:async text=>copied.push(text)}},
    window:{matchMedia:()=>media,addEventListener:()=>{}},
    setTimeout:(fn,ms)=>{const id=++timerId;timers.set(id,{fn,ms});return id;},
    clearTimeout:id=>timers.delete(id)};
  vm.runInNewContext(script,context);await tick();
  return {elements,all,calls,storage,copied,timers,root,el:id=>elements.get(id),
    themeChanged:()=>themeObserver?.(),osChanged:dark=>{media.matches=dark;mediaListener?.();},
    async poll(){const entry=[...timers].find(([,t])=>t.ms===2500);assert.ok(entry,'scheduled poll');
      timers.delete(entry[0]);await entry[1].fn();await tick();}};
}
async function chooseModel(p,id='first',model='ornith:9b-q8_0'){
  const select=p.el('model-'+id);select.value=model;await select.onchange.call(select);
}
async function chooseShared(p,model='ornith:9b-q8_0'){
  p.el('model-mode').value='shared';await p.el('model-mode').emit('change');
  const select=p.el('model-all');select.value=model;await select.onchange.call(select);
}
async function prepare(p){p.el('library-workflows').value='Demo';await p.el('btn-library-select').click();await chooseModel(p);await chooseModel(p,'second');await p.el('btn-create').click();}
async function open(p,rid){p.el('history-runs').value=rid;await p.el('history-runs').emit('change');await p.el('btn-history-open').click();}
const textTree=e=>[e.textContent,...e.children.map(textTree)].join('\n');
module.exports={page,prepare,chooseModel,chooseShared,open,state,snapshot,tick,html,textTree};
