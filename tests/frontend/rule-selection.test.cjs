const assert=require('node:assert/strict');
const test=require('node:test');
const vm=require('node:vm');
const fs=require('node:fs');
const path=require('node:path');

// Execute the actual panel and its handlers, including the submitted selection.
class Element{
  constructor(tag){this.tag=tag;this.children=[];this.dataset={};this.value='';this.checked=false;this.disabled=false;this.classList={toggle(){}};}
  append(...nodes){this.children.push(...nodes);}
  replaceChildren(...nodes){this.children=[...nodes];}
  setAttribute(key,value){this[key]=value;}
  addEventListener(){}
  checkValidity(){return true;}
  querySelectorAll(selector){
    const tags=selector.split(',');
    return this.children.flatMap(child=>[...(tags.includes(child.tag)?[child]:[]),...child.querySelectorAll(selector)]);
  }
}
const catalog=[
  {id:'installation',name:'安装类型准入',dependencies:[]},
  {id:'location',name:'备案与现场位置一致性',dependencies:[]},
  {id:'component',name:'组件型号与承载参数',dependencies:[]},
  {id:'connection',name:'连接与锚固',dependencies:['installation']},
  {id:'wind_snow',name:'风雪承载风险',dependencies:['installation','location','component']},
  {id:'shading',name:'组件遮挡',dependencies:[]},
  {id:'downstream',name:'间接依赖检查',dependencies:['wind_snow']},
].map(r=>({...r,purpose:'检查',materials:[],evidence_guidance:[r.id+'-shot-guide']}));
async function panel(){
  const root=new Element('section'),head=new Element('head');
  const window={dispatchEvent(){}};
  const context=vm.createContext({window,document:{head,getElementById:()=>root,createElement:tag=>new Element(tag)},
    fetch:async()=>({ok:true,json:async()=>({catalog,schemes:[{id:'all',version:1,name:'全量',builtin:true,
      selection:{selected_rule_ids:catalog.map(r=>r.id)}}]})}),
    CustomEvent:class{},console});
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../../frontend/rule-selection.js'),'utf8'),context);
  await window.RuleSelection.ready();
  const boxes=Object.fromEntries(root.querySelectorAll('input').filter(e=>e.type==='checkbox').map(e=>[e.dataset.ruleId,e]));
  return {api:window.RuleSelection,boxes,root,click(id,value){boxes[id].checked=value;boxes[id].onchange();}};
}
for(const id of ['installation','location','component']){
  test(`${id} can be unchecked and selected again without being locked`,async()=>{
    const p=await panel();
    p.click(id,false);
    assert.equal(p.boxes[id].checked,false);
    assert.equal(p.boxes[id].disabled,false);
    assert.equal(p.boxes.wind_snow.checked,false);
    assert.equal(p.boxes.downstream.checked,false,'Indirect dependants must also be removed');
    assert.equal(p.boxes.shading.checked,true,'Unrelated checks remain selected');
    const sent=p.api.build({}).rule_selection.selected_rule_ids;
    assert.ok(!sent.includes(id)&&!sent.includes('wind_snow')&&!sent.includes('downstream'));
    p.click(id,true);
    assert.equal(p.boxes[id].checked,true);
    assert.equal(p.boxes[id].disabled,false);
    assert.ok(p.api.build({}).rule_selection.selected_rule_ids.includes(id));
    const draft=p.api.draft();
    await p.api.restore(draft);
    assert.equal(p.boxes[id].checked,true);
    assert.equal(p.boxes[id].disabled,false);
  });
}
test('automatically included prerequisites from an existing draft can be cancelled',async()=>{
  const p=await panel();
  await p.api.restore({selection:{selected_rule_ids:['wind_snow','shading']}});
  for(const id of ['installation','location','component']){
    assert.equal(p.boxes[id].checked,true);
    assert.equal(p.boxes[id].disabled,false);
  }
  p.click('location',false);
  assert.deepEqual(Array.from(p.api.build({}).rule_selection.selected_rule_ids),['shading']);
  assert.equal(p.boxes.location.checked,false);
  p.click('wind_snow',true);
  assert.equal(p.boxes.location.checked,true,'Selecting a dependent still supplies prerequisites');
  assert.equal(p.boxes.location.disabled,false);
  p.api.lock(true);
  assert.equal(p.boxes.location.disabled,true,'Running cases remain protected from edits');
  p.api.lock(false);
  assert.equal(p.boxes.location.disabled,false);
});

test('material preparation follows selected rules including dependencies',async()=>{
 const p=await panel();const words=e=>[e.textContent||'',...e.children.map(words)].join(' ');
 assert.match(words(p.root),/connection-shot-guide/);
 p.click('connection',false);assert.doesNotMatch(words(p.root),/connection-shot-guide/);
 p.click('connection',true);assert.match(words(p.root),/connection-shot-guide/);assert.match(words(p.root),/installation-shot-guide/);
});
