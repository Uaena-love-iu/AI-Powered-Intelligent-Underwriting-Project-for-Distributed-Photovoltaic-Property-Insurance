const assert=require('node:assert/strict');
const {test}=require('node:test');
const {render,weatherQueryReasons}=require('../../frontend/underwriting-report.js');
class Element {
 constructor(tag){this.tag=tag;this.children=[];this.textContent='';}
 append(...children){this.children.push(...children);}
 querySelector(tag){for(const c of this.children){if(c.tag===tag)return c;const found=c.querySelector?.(tag);if(found)return found;}return null;}
}
const content=el=>[el.textContent,...el.children.map(content)].join(' ');
test('Report visibly explains skipped weather and identifies unadopted candidates',()=>{
 global.document={createElement:tag=>new Element(tag)};
 const root=new Element('main');
 render(root,{location_assessment:{evidence:[{geocode_candidates:[{address:'北京市乙路1号',level:'门牌号'}]}]},processing_trace:[{step:'weather_lookup',status:'skipped',detail:'高德返回多个候选位置，未发起气象查询。'}]});
 const text=content(root);
 assert.match(text,/高德返回多个候选位置/);
 assert.match(text,/北京市乙路1号/);
 assert.match(text,/尚未采用/);
 assert.match(text,/历史气象 · 跳过/);
 delete global.document;
});
test('Old records disclose missing diagnostics rather than invent a cause',()=>{
 assert.match(weatherQueryReasons({location_assessment:{status:'single_source'}})[0],/未保存具体查询原因/);
 assert.match(weatherQueryReasons({processing_trace:[{step:'weather_lookup',status:'failed',error_message:'历史气象接口超时'}]})[0],/超时/);
});
