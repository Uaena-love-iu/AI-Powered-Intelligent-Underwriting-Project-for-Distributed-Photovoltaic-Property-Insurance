/* Rules are resolved again by the server. This UI is never the authority. */
(() => {
  'use strict';
  const statusNames = {not_selected:'未选择',not_applicable:'场景不适用',applicability_pending:'适用条件待确认',dependency_pending:'前置规则待确认',active:'将执行',not_executed:'未执行',material_missing:'材料不足',evidence_insufficient:'证据待核',risk_found:'发现关注事项',completed:'检查已完成'};
  const n=(tag,text='',cls='')=>{const e=document.createElement(tag);e.textContent=text;e.className=cls;return e;};
  const root=document.getElementById('ruleSelection');
  if(!root)return;
  const style=n('style');style.textContent=`
  .rs-panel{margin:16px 24px;padding:22px;border:1px solid #d8e1e4;border-radius:14px;background:#fff}
  .rs-panel h2{font-size:18px;margin:0 0 8px}.rs-panel p{font-size:13px;line-height:1.7;color:#526168}
  .rs-toolbar,.rs-fields{display:flex;gap:12px;align-items:end;flex-wrap:wrap;margin:14px 0}
  .rs-panel label{display:flex;flex-direction:column;gap:5px;font-size:12px;color:#475a61}
  .rs-panel input:not([type=checkbox]),.rs-panel select{border:1px solid #cbd7da;border-radius:7px;padding:8px;background:#fff;max-width:100%;min-width:130px}
  .rs-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(245px,1fr));gap:10px}
  .rs-card{padding:12px;border:1px solid #e0e7e9;border-radius:9px;background:#fbfcfc}
  .rs-card.rs-selected{border-color:#78b6a6;background:#f3faf7}
  .rs-card label.rs-check{display:flex;flex-direction:row;align-items:center;font-size:14px;color:#183f37;font-weight:600;cursor:pointer}
  .rs-card input[type=checkbox]{width:16px;height:16px;accent-color:#216c58}
  .rs-card summary{font-size:12px;color:#547069;cursor:pointer;margin-top:8px}
  .rs-card p{margin:8px 0;min-height:42px}.rs-card .rs-fields{display:grid;grid-template-columns:1fr 1fr}
  .rs-card .rs-fields input{min-width:0;width:100%;box-sizing:border-box}
  .rs-note{padding:10px 12px;border-radius:8px;background:#edf6f2;color:#225b4b;font-size:13px;line-height:1.7}
  .rs-error{color:#a13232!important}.rs-preview{display:flex;flex-wrap:wrap;gap:8px;margin-top:12px}
  .rs-chip{font-size:12px;background:#eef2f4;border-radius:5px;padding:6px 9px}
  .rs-report-table{border-collapse:collapse;width:100%;font-size:13px}.rs-report-table th,.rs-report-table td{text-align:left;border-bottom:1px solid #e4e9e7;padding:9px;vertical-align:top}
  @media(max-width:600px){.rs-panel{margin:12px;padding:14px}.rs-toolbar>*{max-width:100%}}
  `;document.head.append(style);
  root.className='rs-panel';
  root.append(n('h2','本次核保规则'),n('p','先选择规则方案，再调整需要检查的事项。规则会影响实际识别、核验和报告；材料可读性与证据校验始终保留。'));
  const toolbar=n('div','','rs-toolbar'),scheme=n('select'),saveName=n('input'),save=n('button','保存为我的方案','btn'),revision=n('button','保存新版本','btn'),preview=n('button','预览执行范围','btn');
  scheme.setAttribute('aria-label','规则方案');saveName.placeholder='例如：某公司屋顶核保方案';saveName.setAttribute('aria-label','方案名称');
  toolbar.append(scheme,saveName,save,revision,preview);root.append(toolbar);
  const feedback=n('p','正在加载规则…');feedback.setAttribute('role','status');root.append(feedback);
  const context=n('details'),ctxFields=n('div','','rs-fields');context.append(n('summary','适用条件所需项目资料（设置地区、规模或投保人条件时填写）'),ctxFields);
  const input=(label,type='text')=>{const wrap=n('label',label),el=n('input');el.type=type;wrap.append(el);return {wrap,el};};
  const ctx={};
  for(const [key,label,type] of [['province','省份','text'],['city','城市','text'],['district','区县','text'],['installed_capacity_kw','装机规模（kW）','number'],['insured_type','投保人类型（如企业、个人）','text']]){
    const item=input(label,type);if(type==='number'){item.el.min='0';item.el.step='any';}ctx[key]=item.el;ctxFields.append(item.wrap);
  }
  context.append(n('p','地区按填写的省、市或区县完整名称匹配，例如“上海市”。任一地区符合即可；地区、规模与投保人条件之间须同时符合。资料缺失会标为待确认。'));
  root.append(context);
  const parameterDetails=n('details'),paramFields=n('div','','rs-fields');parameterDetails.append(n('summary','判断参数'),paramFields);root.append(parameterDetails);
  const threshold=input('环境与相关强风险判断的模型参考分门槛','number');threshold.el.min='.5';threshold.el.max='1';threshold.el.step='.01';
  const days=input('照片有效期（拟起保日前天数）','number');days.el.min='1';days.el.max='90';
  const profile=n('select');profile.setAttribute('aria-label','材料要求');for(const [v,t] of [['demo_minimum','基础材料要求'],['full_intake','完整材料要求']]){const o=n('option',t);o.value=v;profile.append(o);}
  const profileLabel=n('label','材料要求');profileLabel.append(profile);paramFields.append(threshold.wrap,days.wrap,profileLabel);
  parameterDetails.append(n('p','模型参考分不是准确率。修改判断参数会写入本次规则快照，供后续复核。'));
  const grid=n('div','','rs-grid'),note=n('div','','rs-note'),previewArea=n('div','','rs-preview');root.append(grid,note,previewArea);
  const evidenceList=n('div'),evidenceGuide=n('details');evidenceGuide.append(n('summary','按本次规则准备材料与拍摄部位'),n('p','这是提交前的准备清单；适用性和安装区域以本轮核验为准。扩展检查仅启用时适用。同一部位重复拍摄不能替代其他关键部位；不要为拍摄进行危险作业。'),evidenceList);root.append(evidenceGuide);
  let catalog=[],schemes=[],loaded=false,current=null,requested=new Set(),cards=new Map(),getProject=()=>({}),locked=false;
  const notify=(text,error=false)=>{feedback.textContent=text;feedback.classList.toggle('rs-error',error);};
  const expanded=()=>{const ids=new Set(requested);for(let i=0;i<catalog.length;i++)for(const r of catalog)if(ids.has(r.id))for(const dep of r.dependencies)ids.add(dep);return ids;};
  function update(){
    const ids=expanded();
    for(const r of catalog){const c=cards.get(r.id),automatic=ids.has(r.id)&&!requested.has(r.id);c.box.checked=ids.has(r.id);c.box.disabled=locked;c.card.classList.toggle('rs-selected',ids.has(r.id));c.automatic.textContent=automatic?'前置依赖，自动纳入；取消时会联动取消依赖项':'';}
    evidenceList.replaceChildren();
    for(const r of catalog.filter(r=>ids.has(r.id))){const item=n('section');item.append(n('h4',r.name));for(const text of r.evidence_guidance||[])item.append(n('p',text));evidenceList.append(item);}
    const auto=catalog.filter(r=>ids.has(r.id)&&!requested.has(r.id));
    note.textContent=`本次纳入 ${ids.size} 项规则。`+(auto.length?' 自动带入：'+auto.map(r=>r.name).join('、')+'。':'')+' 未选规则不会被当作“检查通过”；专项核验需人工确认最终承保范围。';
    revision.disabled=locked||!current||current.builtin;previewArea.replaceChildren();
  }
  function toggleRule(rule, checked){
    if(locked)return;
    if(checked){
      requested.add(rule.id);
      update();
      notify('已选择「'+rule.name+'」；必要的前置规则会自动纳入。');
      return;
    }
    // Remove all selected dependants, including indirect ones, so the server's
    // dependency closure cannot silently add the unchecked rule back.
    const removed=new Set([rule.id]);
    for(let i=0;i<catalog.length;i++)for(const item of catalog)
      if(item.dependencies.some(id=>removed.has(id)))removed.add(item.id);
    const affected=catalog.filter(item=>item.id!==rule.id&&requested.has(item.id)&&removed.has(item.id));
    for(const id of removed)requested.delete(id);
    update();
    notify('已取消「'+rule.name+'」。'+(affected.length?'同时取消依赖它的规则：'+affected.map(item=>item.name).join('、')+'。':''));
  }
  const split=v=>v.split(/[,，、\n]/).map(s=>s.trim()).filter(Boolean);
  function selection(){
    if(!loaded)throw Error('规则尚未加载成功，请稍后重试');
    if(!requested.size)throw Error('至少选择一项核保规则');
    for(const el of root.querySelectorAll('input'))if(!el.checkValidity())throw Error('请检查规则参数及装机规模的输入范围');
    const scopes={};
    for(const [key,c] of cards){const min=c.min.value===''?null:Number(c.min.value),max=c.max.value===''?null:Number(c.max.value);if(min!==null&&max!==null&&min>max)throw Error('规则规模下限不能大于上限');scopes[key]={regions:split(c.regions.value),insured_types:split(c.types.value),min_capacity_kw:min,max_capacity_kw:max};}
    return {scheme_id:current?.id||null,scheme_version:current?.version||null,selected_rule_ids:[...requested],scopes,parameters:{environment_min_confidence:Number(threshold.el.value),watermark_days:Number(days.el.value),material_profile:profile.value}};
  }
  function mergeProject(project){const out={...project};for(const [key,el] of Object.entries(ctx))if(el.value.trim()!=='')out[key]=key==='installed_capacity_kw'?Number(el.value):el.value.trim();return out;}
  function apply(item){
    current=item;const s=item.selection;requested=new Set(s.selected_rule_ids);saveName.value=item.name;
    threshold.el.value=s.parameters?.environment_min_confidence??.65;days.el.value=s.parameters?.watermark_days??15;profile.value=s.parameters?.material_profile||'demo_minimum';
    for(const [key,c] of cards){const v=s.scopes?.[key]||{};c.regions.value=(v.regions||[]).join('、');c.types.value=(v.insured_types||[]).join('、');c.min.value=v.min_capacity_kw??'';c.max.value=v.max_capacity_kw??'';}
    update();notify('已载入 '+item.name+' · v'+item.version+'；可按本次业务调整。');
  }
  function fillSchemes(){scheme.replaceChildren();for(const s of schemes){const o=n('option',s.name+' · v'+s.version);o.value=s.id+':'+s.version;scheme.append(o);}}
  async function request(url,body){const response=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const data=await response.json();if(!response.ok)throw Error(typeof data.detail==='string'?data.detail:JSON.stringify(data.detail));return data;}
  async function saveScheme(asNew){
    try{const s=selection();if(asNew){s.scheme_id=null;s.scheme_version=null;}const data=await request('/api/v1/rule-selection/schemes',{name:saveName.value.trim(),selection:s});schemes.push(data);fillSchemes();scheme.value=data.id+':'+data.version;apply(data);window.dispatchEvent(new CustomEvent('rule-selection-changed'));notify('已保存 '+data.name+' · v'+data.version+'，此前版本和历史案件保持不变。');}catch(e){notify(e.message,true);}
  }
  save.onclick=()=>saveScheme(true);revision.onclick=()=>saveScheme(false);
  preview.onclick=async()=>{try{const data=await request('/api/v1/rule-selection/preview',{project:mergeProject(getProject()),selection:selection()});previewArea.replaceChildren(...data.rules.map(r=>n('span',r.name+' · '+statusNames[r.status],'rs-chip')));notify('执行范围已确认；正式提交时将再次按当时的项目资料生成快照。');}catch(e){notify(e.message,true);}};
  scheme.onchange=()=>{apply(schemes.find(s=>s.id+':'+s.version===scheme.value));window.dispatchEvent(new CustomEvent('rule-selection-changed'));};
  root.addEventListener('change',e=>{if(e.target!==scheme){previewArea.replaceChildren();window.dispatchEvent(new CustomEvent('rule-selection-changed'));}});
  window.RuleSelection={
    setProjectGetter(fn){getProject=fn;},
    build(project){return {project:mergeProject(project),rule_selection:selection()};},
    draft(){if(!loaded)return null;try{return {selection:selection(),context:Object.fromEntries(Object.entries(ctx).map(([k,e])=>[k,e.value])),name:current?.name||'本次自定义规则'};}catch{return null;}},
    async restore(draft){
      await loading;if(!loaded)return;
      for(const [key,el] of Object.entries(ctx))el.value=draft?.context?.[key]??'';
      if(draft?.selection){const source=schemes.find(s=>s.id===draft.selection.scheme_id&&s.version===draft.selection.scheme_version);
        apply({...source,id:draft.selection.scheme_id,version:draft.selection.scheme_version,name:draft.name||source?.name||'恢复的规则草稿',selection:draft.selection});
        scheme.value=source?source.id+':'+source.version:'';
      }else{scheme.value=schemes[0].id+':'+schemes[0].version;apply(schemes[0]);}
    },
    lock(value){locked=value;for(const c of root.querySelectorAll('input,select,button'))c.disabled=value;if(loaded)update();},
    async ready(){return loading;},
  };
  const loading=fetch('/api/v1/rule-selection').then(async response=>{
    if(!response.ok)throw Error('规则加载失败，请刷新页面');const data=await response.json();catalog=data.catalog;schemes=data.schemes;
    for(const r of catalog){
      const card=n('article','','rs-card'),label=n('label','','rs-check'),box=n('input');box.type='checkbox';box.dataset.ruleId=r.id;label.append(box,n('span',r.name));
      const automatic=n('small'),details=n('details');details.append(n('summary','适用条件与所需材料'),n('p','所需材料：'+r.materials.map(c=>({panorama:'现场全景',roof_connection:'连接节点',parapet:'边缘防护',workshop:'车间',component_nameplate:'组件铭牌',equipment_inventory:'设备清单',filing_certificate:'备案证明',project_document:'项目证明',grid_connection_document:'并网材料',electrical_grounding:'接地照片/报告',combiner_box:'汇流箱',component_surface:'组件表面',inverter_nameplate:'逆变器铭牌'}[c]||c)).join('、')));
      if(r.dependencies.length)details.append(n('p','依赖：'+r.dependencies.map(k=>catalog.find(x=>x.id===k).name).join('、')));
      const fields=n('div','','rs-fields'),regions=input('适用地区（空白为不限）'),types=input('投保人类型（空白为不限）'),min=input('最小规模 kW','number'),max=input('最大规模 kW','number');
      for(const f of [min,max]){f.el.min='0';f.el.step='any';}
      for(const f of [regions,types,min,max])fields.append(f.wrap);details.append(fields);
      card.append(label,automatic,n('p',r.purpose),details);grid.append(card);cards.set(r.id,{card,box,automatic,regions:regions.el,types:types.el,min:min.el,max:max.el});
      box.onchange=()=>toggleRule(r,box.checked);
    }
    loaded=true;fillSchemes();apply(schemes[0]);
  }).catch(e=>{notify(e.message,true);});
})();
