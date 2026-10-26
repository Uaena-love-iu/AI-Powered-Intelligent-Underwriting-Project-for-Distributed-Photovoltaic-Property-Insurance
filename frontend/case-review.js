(function(global){
  'use strict';
  const decisions={accept:'通过',conditional_accept:'有条件通过',recommend_reject:'拒保',request_more:'要求补材',manual_review:'待人工复核',surcharge:'加费建议',notify:'风险提示',none:'无命中'};
  const names={rated_power_w:'额定功率（W）',front_static_load_pa:'正面机械载荷（Pa）',back_static_load_pa:'背面机械载荷（Pa）',component_model:'组件型号',project_name:'项目名称',insured_name:'被保险人',site_address:'电站地址',longitude:'经度',latitude:'纬度',proposed_start_date:'拟起保日期'};
  const time=v=>v?new Date(v).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'}):'';
  const text=v=>v===null||v===undefined?'未提供':typeof v==='object'?JSON.stringify(v):String(v);
  const el=(tag,value,cls)=>{const e=document.createElement(tag);if(value!==undefined)e.textContent=value;if(cls)e.className=cls;return e;};
  const link=(name,href)=>{const a=el('a',name);a.href=href;a.target='_blank';a.rel='noopener';return a;};
  const api=id=>'/api/v1/cases/'+encodeURIComponent(id);
  const label=v=>decisions[v]||v||'未形成建议';
  function revisionFor(state){
    if(!state.fullCase)return undefined;
    const draft=state.revisionDraft||{};
    if(!draft.actor?.trim()||!draft.reason?.trim())throw Error('请在“补材复核与人工审核”中填写修改人和本轮修改原因。');
    return {parent_case_id:state.fullCase.case_id,actor:draft.actor.trim(),reason:draft.reason.trim(),corrections:draft.corrections||[]};
  }
  function render(root,{state,onChange,onRun,onRefresh}){
    root.hidden=!state.fullCase||new URL(location.href).searchParams.get('view')==='replay';
    if(root.hidden){root.dataset.key='';return;}
    const caseId=state.fullCase.case_id;
    const key=JSON.stringify([caseId,state.stale,state.running,state.revisionDraft?.corrections,state.reviewRefresh]);
    if(root.dataset.key===key)return;
    root.dataset.key=key;root.replaceChildren();root.style.lineHeight='1.7';
    if(state.fullCase.verification?.schema){root.append(el('h2','v2 案件复核'),el('p','补材只上传新增或替换材料；旧材料由服务端保留。请在报告复核页处理存疑点并提交整单决定。'),link('进入证据复核与整单提交','/verification?case='+encodeURIComponent(caseId)));return;}
    const draft=state.revisionDraft??={actor:'',reason:'',corrections:[]};
    root.append(el('h2','补材复核与人工审核'));
    root.append(el('p','同一案件保留每轮材料、AI 建议及人工意见。材料或参数更新后，需重新分析并重新审核。','small'));
    const detail=el('details');detail.open=!!state.stale;
    detail.append(el('summary','补材、替换材料或修正组件型号后重新核保'));
    const controls=el('div');controls.style.cssText='display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:12px;margin:12px 0';
    function field(parent,title,value,handler,multiline=false){const box=el('label',title,'field');const input=el(multiline?'textarea':'input');input.setAttribute('aria-label',title);input.value=value||'';input.disabled=state.running;input.oninput=()=>{handler(input.value);onChange();};box.append(input);parent.append(box);return input;}
    field(controls,'修改人',draft.actor,v=>draft.actor=v);
    field(controls,'本轮修改原因',draft.reason,v=>draft.reason=v,true);
    detail.append(controls,el('p','在材料区添加、替换或移除本轮材料；历史原文件仍可在下方轮次记录中下载。项目与风雪参数可在原资料区修改。','small'));
    const correction=el('details');correction.append(el('summary','人工修正组件完整型号'));
    correction.append(el('p','修正后重新查询该型号的技术参数，并按所选规则重跑核保；无法取得可信参数时仍保留待核验状态。','small'));
    const sourceLabel=el('label','修正对应的原始材料','field');const source=el('select');source.setAttribute('aria-label','修正对应的原始材料');for(const item of state.items.filter(i=>['component_nameplate','equipment_inventory'].includes(i.category))){const opt=el('option',item.file.name);opt.value=item.id;source.append(opt);}source.value=draft.corrections?.[0]?.material_id||source.value;source.disabled=state.running;sourceLabel.append(source);correction.append(sourceLabel);
    const original=state.fullCase.component_profile?.component_model||state.fullCase.project?.component_model;
    const originalText=el('p');const showOriginal=()=>{const f=state.fullCase.ocr_fields?.find(f=>f.material_id===source.value&&f.field_name==='component_model');originalText.textContent='上一轮该材料识别／使用值：'+text(f?.normalized_value||f?.raw_value||original);};source.onchange=showOriginal;showOriginal();correction.append(originalText);
    let model=state.project?.component_model||'',evidence=draft.corrections?.find(c=>c.field==='component_model')?.evidence||'';
    const value=field(correction,'修正后的组件完整型号',model,v=>model=v);
    const proof=field(correction,'型号修正依据（原件、页码或补充证据）',evidence,v=>evidence=v,true);
    const apply=el('button','应用型号修正并标记待重核','btn');apply.disabled=state.running;
    const message=el('p','','small');
    apply.onclick=()=>{if(!source.value||!value.value.trim()||!proof.value.trim()){message.textContent='请填写完整型号与修正依据。';return;}
      state.project.component_model=value.value.trim();draft.corrections=[{field:'component_model',material_id:source.value,evidence:proof.value.trim()}];
      state.stale=true;state.staleReason='组件型号已人工修正，请重新核保；上一轮参数和人工决定不适用于本轮草稿。';onChange(true);};
    correction.append(apply,message);detail.append(correction);
    if(draft.corrections?.length)detail.append(el('p','已登记型号修正：'+text(state.project.component_model)+'；依据：'+draft.corrections[0].evidence,'notice'));
    const run=el('button','提交新一轮核保','btn primary');run.disabled=state.running;run.style.marginTop='12px';run.onclick=()=>onRun();detail.append(run);root.append(detail);
    const review=el('section');review.style.cssText='border-top:1px solid #dbe5dc;margin-top:18px;padding-top:12px';review.append(el('h3','人工业务决定'));
    review.append(el('p','AI 建议：'+label(state.fullCase.decision?.decision)+'。人工决定单独保存，不覆盖 AI 建议和依据。'));
    const info=el('div','正在读取审核记录…','notice');review.append(info);
    const reviewer=field(review,'审核人员',draft.actor,()=>{});
    const choice=el('select');choice.setAttribute('aria-label','最终业务决定');choice.style.cssText='padding:9px;border:1px solid #cbdacf;border-radius:8px';
    for(const [v,n] of Object.entries(decisions).filter(([v])=>['accept','conditional_accept','request_more','recommend_reject'].includes(v))){const opt=el('option',n);opt.value=v;choice.append(opt);}choice.value='request_more';choice.disabled=state.running;const decisionLabel=el('label','最终业务决定','field');decisionLabel.append(choice);review.append(decisionLabel);
    const opinion=field(review,'人工审核意见与证据依据','',()=>{},true);
    const save=el('button','保存人工审核决定','btn primary');save.disabled=true;
    const feedback=el('p','','small');
    save.onclick=async()=>{if(state.running||state.stale)return;if(!reviewer.value.trim()||!opinion.value.trim()){feedback.textContent='请填写审核人员和审核依据。';return;}save.disabled=true;
      try{await TaskRecovery.request(api(caseId)+'/review',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({reviewer_name:reviewer.value.trim(),comment:opinion.value.trim(),final_decision:choice.value})});state.reviewRefresh=(state.reviewRefresh||0)+1;onRefresh();}
      catch(e){feedback.textContent='未保存：'+e.message;save.disabled=false;}};
    review.append(save,feedback,el('p','本机工作台记录填写的审核人姓名；实际生产使用需由登录账号和审核权限绑定身份。','small'));root.append(review);
    const history=el('details');history.open=true;history.append(el('summary','核保轮次与前后变化'));const historyBody=el('div','正在读取轮次…');history.append(historyBody);root.append(history);
    const reload=el('button','刷新审核与轮次记录','btn');reload.onclick=()=>{state.reviewRefresh=(state.reviewRefresh||0)+1;onRefresh();};root.append(reload);
    Promise.all([TaskRecovery.request(api(caseId)),TaskRecovery.request(api(caseId)+'/rounds')]).then(([record,data])=>{
      if(root.dataset.key!==key)return;
      const current=!record.revision||record.revision.is_current;
      info.replaceChildren(el('p',state.stale?'本轮草稿已修改：原人工决定仅适用于旧材料；完成新一轮分析后才能审核。':!current?'已有后续轮次，本轮人工决定仅为历史记录。':record.review_effective?'当前人工决定：'+label(record.final_decision)+' · '+record.reviewer_name+' · '+time(record.reviewed_at):'当前轮次尚未完成人工审核。'));
      for(const h of record.review_history||[])info.append(el('p',time(h.reviewed_at)+' · '+h.reviewer_name+' · '+label(h.final_decision)+'；'+h.comment));
      save.disabled=state.running||state.stale||!current;
      if(!current)feedback.textContent='请从历史案例打开最新轮次后继续操作。';
      review.append(link('下载本轮报告（含人工意见与修正审计）',api(caseId)+'/report'));
      historyBody.replaceChildren();
      if(!data.rounds?.length)historyBody.append(el('p','历史案件尚无轮次档案；重新核保后建立关联。'));
      for(const round of data.rounds||[])renderRound(historyBody,round);
    }).catch(e=>{if(root.dataset.key===key){info.textContent='审核记录读取失败：'+e.message;historyBody.textContent='请刷新后重试。';}});
  }
  function renderRound(root,r){
    const box=el('details');box.open=r.is_current;box.style.cssText='margin:12px 0;padding:12px;border:1px solid #dbe5dc;border-radius:8px';
    box.append(el('summary','R'+r.round_number+' · '+({completed:'已完成',failed:'执行失败',running:'分析中',queued:'等待执行',cancelled:'已停止'}[r.status]||r.status)+(r.is_current?' · 当前轮次':' · 历史轮次')));
    box.append(el('p',time(r.created_at)+' · '+(r.actor||'首次提交')+'；'+r.reason));
    box.append(el('p','AI 建议：'+label(r.ai_decision?.decision)+'；人工决定：'+(r.human_decision?label(r.human_decision)+(r.is_current?'':'（仅适用于本轮历史材料）'):'尚未记录')));
    if(r.review_comment)box.append(el('p','审核人：'+r.reviewer_name+'；意见：'+r.review_comment));
    for(const c of r.corrections||[])box.append(el('p','人工修正 '+(names[c.field]||c.field)+'：'+text(c.before)+' → '+text(c.after)+'；'+c.actor+' · '+time(c.at)+'；依据：'+c.evidence));
    const d=r.comparison;
    if(d){
      box.append(el('h4','与上一轮对比'));
      const coverageLabel=v=>({satisfied:'已取得覆盖证据',insufficient:'需补材',review:'内部核验',not_selected:'未选择',not_applicable:'不适用',not_executed:'未执行'}[v]||v);
      for(const change of d.evidence_coverage_changes||[]){box.append(el('p','材料覆盖 '+change.field+'：'+(change.before?coverageLabel(change.before.status):'未记录')+' → '+(change.after?coverageLabel(change.after.status):'未记录')));for(const target of new Set([...Object.keys(change.before?.targets||{}),...Object.keys(change.after?.targets||{})])){const a=change.before?.targets?.[target],b=change.after?.targets?.[target];if(a!==b)box.append(el('p',target+'：'+(coverageLabel(a)||'未记录')+' → '+(coverageLabel(b)||'未记录'),'small'));}}

      if(!d.materials.length)box.append(el('p','材料文件与分类未变化。'));
      for(const m of d.materials){const line=el('p',({added:'新增',removed:'移除',replaced:'替换',reclassified:'调整分类'}[m.action])+'：'+(m.after?.file_name||m.before?.file_name)+' ');
        if(m.before)line.append(link('上一轮原件',api(r.parent_id)+'/materials/'+encodeURIComponent(m.material_id)+'/source'));
        if(m.after){line.append(document.createTextNode(' · '),link('本轮原件',api(r.case_id)+'/materials/'+encodeURIComponent(m.material_id)+'/source'));}box.append(line);}
      for(const p of [...d.parameters,...(d.component_parameters||[]),...(d.screening_changes||[])])box.append(el('p','参数 '+(names[p.field]||p.field)+'：'+text(p.before)+' → '+text(p.after)));
      box.append(el('p','检查方案：'+(d.rules_changed?'已变化':'未变化')+'；新增触发规则：'+(d.risks_added.map(id=>d.risk_labels?.[id]||id).join('、')||'无')+'；本轮未再触发：'+(d.risks_removed.map(id=>d.risk_labels?.[id]||id).join('、')||'无')));
      box.append(el('p','AI 建议：'+label(d.decision_before?.decision)+' → '+label(d.decision_after?.decision)));
      const reasons=el('details');reasons.append(el('summary','查看本轮结论依据'));for(const reason of d.decision_after?.reasons||[])reasons.append(el('p',text(reason)));box.append(reasons);
      box.append(el('p',d.comparison_note,'small'));
    }
    if(r.status==='completed')box.append(link('查看本轮完整报告',api(r.case_id)+'/report'));
    root.append(box);
  }
  global.CaseReview={render,revisionFor};
  if(typeof module!=='undefined')module.exports={revisionFor};
})(typeof window==='undefined'?globalThis:window);
