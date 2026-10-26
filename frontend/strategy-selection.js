(function(global){
'use strict';let records=[],loaded=false,loading=false,error='';
const names={notify:'风险提示',request_more:'补材建议',manual_review:'人工复核',recommend_reject:'拒保建议',surcharge:'加费建议（比例待业务确认）'};
const el=(t,text)=>{const n=document.createElement(t);if(text!=null)n.textContent=text;return n;};
function requestFor(state){return state.revisionDraft?.strategySelection||undefined;}
function render(root,{state,onChange}){
 root.hidden=new URL(location.href).searchParams.get('view')==='replay';if(root.hidden)return;
 if(!loaded&&!loading){loading=true;fetch('/api/v1/strategies',{cache:'no-store'}).then(async r=>{if(!r.ok)throw Error('策略列表加载失败，请刷新后再试');records=(await r.json()).records;error='';}).catch(e=>error=e.message).finally(()=>{loading=false;loaded=true;render(root,{state,onChange});});}
 root.replaceChildren();root.append(el('h2','本次承保策略'),el('p','选择业务处理方案；策略在风险评估完成后匹配，不改变检查范围与原始结论。季节按拟起保日期判断。地区与规模在下方规则区的“适用条件所需项目资料”填写，拟起保日期在“投保项目资料”填写。'));
 const label=el('label','策略方案及版本'),select=el('select');select.setAttribute('aria-label','本次承保策略版本');select.append(new Option('不附加策略，保留原 AI 建议',''));
 const selected=requestFor(state)?.record_id;
 for(const r of records.filter(x=>x.status==='active'||x.id===selected)){const option=new Option(r.name+' · '+r.company+' · V'+r.version+(r.demo?'（演示）':'')+(r.status==='active'?'':'（不可新用）'),r.id);select.append(option);}
 if(selected&&!records.some(r=>r.id===selected))select.append(new Option('已选版本尚未载入，请刷新确认',selected));select.value=selected||'';select.disabled=state.running||!loaded||!!error;
 select.onchange=()=>{state.revisionDraft??={actor:'',reason:'',corrections:[]};state.revisionDraft.strategySelection=select.value?{record_id:select.value}:null;state.stale=!!state.fullCase;state.staleReason='承保策略已调整，需新一轮核保；旧报告仍对应原策略。';onChange();};label.append(select);root.append(label);
 const record=records.find(r=>r.id===selected);if(record){root.append(el('p','业务依据：'+record.basis),el('p','策略关注等级与计算风险等级分开保存；加费只给出建议，不输出费率。'));const d=el('details');d.append(el('summary','查看 '+record.rules.length+' 条策略'));for(const r of record.rules)d.append(el('p',r.name+' · '+names[r.action]+' · 优先级 '+r.priority));root.append(d);}
 if(error)root.append(el('p',error));const refresh=el('button','刷新策略列表');refresh.type='button';refresh.disabled=state.running||loading;refresh.onclick=()=>{loaded=false;render(root,{state,onChange});};const link=el('a','管理策略与发布版本');link.href='/strategies';link.target='_blank';link.rel='noopener';root.append(refresh,document.createTextNode('　'),link);
}
global.StrategySelection={render,requestFor};if(typeof module!=='undefined')module.exports={requestFor};
})(typeof window==='undefined'?globalThis:window);