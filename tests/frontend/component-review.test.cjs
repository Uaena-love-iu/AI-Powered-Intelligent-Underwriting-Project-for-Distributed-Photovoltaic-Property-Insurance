const {test}=require('node:test');
const assert=require('node:assert/strict');
const {requestFor,invalidate,diff}=require('../../frontend/component-review.js');
const review=()=>({component_model:'TEST-600/AB',identity_material_id:'plate',parameters:{front_static_load_pa:{source_material_id:'spec'}}});
test('changed model cannot silently reuse case parameters',()=>{
 const state={project:{component_model:'TEST-600/AB'},revisionDraft:{componentReview:review()}};
 assert.equal(requestFor(state),state.revisionDraft.componentReview);
 state.project.component_model='TEST-605/AB';assert.throws(()=>requestFor(state),/重新确认/);
});
test('replacement of either identity or parameter evidence requires reconfirmation',()=>{
 for(const id of ['plate','spec']){const state={revisionDraft:{componentReview:review()}};invalidate(state,id);assert.equal(requestFor(state),undefined);assert.match(state.revisionDraft.componentInvalidation,/重新核验/);}
 const state={revisionDraft:{componentReview:review()}};invalidate(state,'unrelated');assert.ok(state.revisionDraft.componentReview);
});
test('draft and candidate selections alone never become submitted facts',()=>{
 assert.equal(requestFor({revisionDraft:{componentEditor:review(),componentLookup:{profile:review()}}}),undefined);
 assert.match(diff('TEST-600/AB','TEST-605/AB'),/0 → 5/);
 assert.match(diff('TEST-600/AB','TEST-600/AC'),/B → C/);
});

// Real textarea.type is read-only; emulate it to catch a partial-render crash.
test('complete editor renders without writing to textarea read-only properties',()=>{
 class Element{constructor(tag){this.tag=tag;this.children=[];this.dataset={};this.style={};if(tag==='textarea')Object.defineProperty(this,'type',{get:()=> 'textarea'});}append(...items){this.children.push(...items);}replaceChildren(){this.children=[];}setAttribute(){} }
 global.document={createElement:t=>new Element(t),createTextNode:t=>t};global.location={href:'http://localhost/evaluation'};global.Option=function(text,value){this.text=text;this.value=value;};
 const root=new Element('section');
 global.ComponentReview.render(root,{state:{project:{},items:[{id:'m1',category:'component_nameplate',file:{size:1,name:'plate.png'}}]},onChange:()=>{}});
 const words=n=>typeof n==='string'?n:(n.textContent||'')+(n.children||[]).map(words).join('');
 assert.match(words(root),/确认本案参数并标记待重核/);assert.match(words(root),/另行核验入库/);
});
