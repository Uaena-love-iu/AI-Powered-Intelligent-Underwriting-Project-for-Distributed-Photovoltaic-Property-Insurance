(function(global){
 'use strict';
 const fields={rated_power_w:['额定功率','W','not_applicable'],front_static_load_pa:['正面机械载荷','Pa','front'],back_static_load_pa:['背面机械载荷','Pa','back']};
 const basis={test:'测试载荷',design:'设计载荷',unspecified:'口径待确认'},status={found:'已取得',conflict:'来源冲突',unknown:'缺失',observed_not_found:'资料未披露',unconfirmed:'待确认',exact:'精确匹配',series_only:'仅系列候选'};
 const el=(tag,text,cls)=>{const n=document.createElement(tag);if(text!=null)n.textContent=text;if(cls)n.className=cls;return n;};
 function diff(a,b){a=String(a||'');b=String(b||'');if(a===b)return '完整字符一致';let start=0,end=0;while(start<Math.min(a.length,b.length)&&a[start]===b[start])start++;while(end<Math.min(a.length,b.length)-start&&a[a.length-end-1]===b[b.length-end-1])end++;return `差异片段：${a.slice(start,a.length-end)||'（空）'} → ${b.slice(start,b.length-end)||'（空）'}；须核对功率档、后缀与市场版本`;}
 async function hash(item){const bytes=item.fileBytes||await item.file.arrayBuffer();return [...new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))].map(x=>x.toString(16).padStart(2,'0')).join('');}
 function invalidate(state,id){const d=state.revisionDraft;if(!d)return;const r=d.componentReview;if(r&&(r.identity_material_id===id||Object.values(r.parameters).some(p=>p.source_material_id===id))){d.componentReview=null;d.componentInvalidation='型号或参数依据文件已替换／移除，请重新核验后确认。';}}
 function requestFor(state){const d=state.revisionDraft||{},r=d.componentReview;if(!r)return undefined;if(String(state.project.component_model||'').trim().toLowerCase()!==r.component_model.trim().toLowerCase())throw Error('项目型号已改变，请重新确认本案补充参数，或取消采用。');return r;}
 function render(root,{state,onChange,onRun,onAddDocuments,onOpenSource}){
  root.hidden=new URL(location.href).searchParams.get('view')==='replay'||!state.items.length;
  if(root.hidden){root.dataset.key='';return;}
  const d=state.revisionDraft??={actor:'',reason:'',corrections:[]};
  const key=JSON.stringify([state.workId,state.fullCase?.case_id,state.project.component_model,state.running,state.stale,state.items.map(i=>[i.id,i.category,i.file.size,i.file.lastModified,i.sourceOnly]),d.componentReview,d.componentLookup?.id,d.componentInvalidation]);
  if(root.dataset.key===key)return;root.dataset.key=key;root.replaceChildren();
  const raw=state.fullCase?.component_module_result||{},lookup=d.componentLookup||raw.case_parameter_review?.automatic_result||raw;
  const profile=lookup.profile||state.fullCase?.component_profile||{};
  const editor=d.componentEditor??={component_model:state.project.component_model||profile.component_model||'',manufacturer:profile.manufacturer||'',reviewer_name:d.actor||'',model_reason:'',reason:'',parameters:{}};
  root.append(el('h2','组件核验与人工补充'),el('p','识别依据 → 候选比对 → 参数来源 → 本案确认 → 重新核保','small'),el('p','本案补充与共享参数库分开管理。草稿、候选和来源冲突值不会自动进入计算；原识别与检索结果保留。','notice'));
  if(d.componentInvalidation)root.append(el('p',d.componentInvalidation,'error'));
  if(d.componentReview)root.append(el('p','已确认本案补充：'+d.componentReview.component_model+' · '+d.componentReview.reviewer_name+(state.fullCase?.component_review?.applied&&!state.stale?'；本轮已采用，具体数值与来源见整单建议。':'；需提交核保才会产生新结果。'),'notice'));
  for(const failure of state.fullCase?.component_review?.ocr_failures||[])root.append(el('p',failure.reason,'notice'));
  const evidence=el('details');evidence.open=true;evidence.append(el('summary','1. 查看原始识别、候选差异与来源证据'));
  function originalPreview(parent,id,box,page){
   const material=state.fullCase?.materials?.find(m=>m.material_id===id);
   if(!material)return;
   const href='/api/v1/cases/'+encodeURIComponent(state.fullCase.case_id)+'/materials/'+encodeURIComponent(id)+'/source';
   const a=el('a','查看原轮材料'+(page?' · 第 '+page+' 页':''));a.href=href+(page?'#page='+encodeURIComponent(page):'');a.target='_blank';a.rel='noopener';parent.append(a);
   if(!/^image\//.test(material.media_type||''))return;
   const detail=el('details'),frame=el('div'),img=el('img');detail.append(el('summary',box?'展开原图与识别位置':'展开原图（未返回定位框）'));frame.style.cssText='position:relative;width:min(100%,480px);margin:10px 0';img.src=href;img.alt='该轮型号核验原图';img.style.cssText='display:block;width:100%;height:auto';frame.append(img);
   let coords=Array.isArray(box)?box:box?[box.x_min,box.y_min,box.x_max,box.y_max]:null;
   if(coords?.length===4&&coords.every(Number.isFinite)){const scale=coords.some(v=>v>1)?1000:1;coords=coords.map(v=>Math.max(0,Math.min(1,v/scale)));if(coords[2]>coords[0]&&coords[3]>coords[1]){const border=el('span');border.style.cssText=`position:absolute;pointer-events:none;border:2px solid #c23b36;left:${coords[0]*100}%;top:${coords[1]*100}%;width:${(coords[2]-coords[0])*100}%;height:${(coords[3]-coords[1])*100}%`;frame.append(border);}}
   detail.append(frame);parent.append(detail);
  }
  const originals=state.fullCase?.original_ocr_fields?.length?state.fullCase.original_ocr_fields:state.fullCase?.ocr_fields||[];
  for(const f of originals.filter(x=>x.field_name==='component_model')){
   const row=el('p','原识别：'+(f.raw_value||f.normalized_value||'未取得')+'；'+(f.evidence_text||'未记录原文定位'));
   originalPreview(row,f.material_id,f.bbox,f.page);evidence.append(row);
  }
  const candidates=[];for(const r of lookup.recognition||raw.recognition||[])for(const c of [...r.candidates||[],...r.uncertain_candidates||[],...r.unresolved_candidates||[]])candidates.push({...c,material_id:r.material_id});
  for(const c of lookup.local_candidates||[])candidates.push(c);
  const unique=new Set();
  for(const c of candidates){const model=c.lookup_model||c.raw_model||c.component_model||c.model;if(!model||unique.has(model))continue;unique.add(model);const row=el('div',null,'cr-candidate');row.append(el('strong',model),el('p',diff(editor.component_model,model),'small'),el('p',(c.manufacturer||'厂商待核')+' · '+(c.note||status[c.model_match_status]||c.candidate_kind||'候选，待确认')+(c.page?' · 第 '+c.page+' 页':''),'small'));const choose=el('button','填入型号草稿','btn');choose.disabled=state.running;choose.onclick=()=>{editor.component_model=model;if(c.manufacturer)editor.manufacturer=c.manufacturer;if(c.material_id)editor.identity_material_id=c.material_id;root.dataset.key='';onChange(true);};row.append(choose);evidence.append(row);}
  if(!originals.length&&!candidates.length)evidence.append(el('p','尚无型号识别结果；可先运行核保，或根据本案铭牌／设备清单人工核对完整型号。','small'));
  const table=el('table'),head=el('tr');['参数／方向','检索值（待核）','来源与核验状态'].forEach(t=>head.append(el('th',t)));table.append(head);
  for(const [field,[name,unit,direction]]of Object.entries(fields)){
   let records=profile.parameter_evidence?.[field]||[];if(!Array.isArray(records))records=records.records||[];
   const row=el('tr');row.append(el('td',name),el('td',records.some(r=>r.status==='conflict')?'来源冲突，待人工核验':profile[field]==null?'缺失，需补充证据':profile[field]+' '+unit));const cell=el('td');
   if(!records.length)cell.append(el('p','未取得字段原文证据','small'));
   for(const r of records){const card=el('div',null,'cr-evidence');card.append(el('p',`${r.value??'无数值'} ${r.unit||unit} · ${status[r.status]||r.status||'待核验'} · ${basis[r.load_basis]||'口径待确认'}`),el('p',(r.document_title||'来源文件未注明')+' · 版本：'+(r.document_version||r.market_version||'未注明')+' · 页码：'+(r.page||'未注明'),'small'),el('p',r.quote||'未取得原文','small'),el('p','适用条件：'+(r.applicability||r.installation_conditions||'未注明，需核对'),'small'));
    if(/^https:\/\//i.test(r.source_url||'')){const a=el('a','查看原始来源');a.href=r.source_url;a.target='_blank';a.rel='noopener';card.append(a);}
    if(Number.isFinite(r.value)&&r.value>0){const fill=el('button','将此证据填入待核草稿','btn');fill.disabled=state.running;fill.onclick=()=>{editor.parameters[field]={value:r.value,unit:r.unit||unit,direction,load_basis:['test','design'].includes(r.load_basis)?r.load_basis:'unspecified',document_title:r.document_title||'',document_version:r.document_version||'',quote:r.quote||'',page:String(r.page||''),source_url:/^https:\/\//i.test(r.source_url||'')?r.source_url:'',applicability:r.applicability||'',installation_confirmed:false};root.dataset.key='';onChange(true);};card.append(fill);}cell.append(card);
   }row.append(cell);table.append(row);
  }evidence.append(table);root.append(evidence);
  const edit=el('details');edit.open=!!d.componentInvalidation||!state.fullCase;edit.append(el('summary','2. 查询其他来源或人工补充本案参数'));
  const form=el('form'),grid=el('div',null,'cr-grid');form.append(grid);
  function field(parent,label,key,obj,type='text',required=false){const wrap=el('label',label,'cr-field'),i=el(type==='textarea'?'textarea':'input');if(type!=='textarea')i.type=type;i.value=obj[key]??'';i.required=required;i.disabled=state.running;i.setAttribute('aria-label',label);i.oninput=()=>{obj[key]=type==='number'?(i.value===''?'':Number(i.value)):i.value;onChange(false);};wrap.append(i);parent.append(wrap);return i;}
  const model=field(grid,'待核完整型号','component_model',editor,'text',true);model.maxLength=100;
  field(grid,'制造商','manufacturer',editor,'text',true).maxLength=120;
  const identityLabel=el('label','型号依据：本案铭牌／设备清单','cr-field'),identity=el('select');identity.setAttribute('aria-label','型号依据：本案铭牌／设备清单');identity.append(new Option('请选择',''));for(const i of state.items.filter(i=>!i.sourceOnly&&['component_nameplate','equipment_inventory'].includes(i.category)))identity.append(new Option(i.file.name,i.id));identity.value=editor.identity_material_id||'';identity.disabled=state.running;identity.required=true;identity.onchange=()=>{editor.identity_material_id=identity.value;onChange(false);};identityLabel.append(identity);grid.append(identityLabel);
  field(grid,'型号核验依据（字符、后缀及原件位置）','model_reason',editor,'textarea',true);
  field(grid,'本案参数核验人','reviewer_name',editor,'text',true);
  field(grid,'本案补充或处理冲突的理由','reason',editor,'textarea',true);
  const query=el('button','按此型号重新检索其他来源','btn');query.type='button';query.disabled=state.running;const queryStatus=el('p','','small');query.onclick=async()=>{
   if(!model.value.trim()){queryStatus.textContent='请先填入完整型号；候选字串需人工核实。';return;}query.disabled=true;queryStatus.textContent='正在查询，结果只更新证据区，不自动采用。';const work=state.workId;
   try{const start=await TaskRecovery.request('/api/v1/underwriting/component-model-workbench/api/lookup',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({model:model.value.trim(),manufacturer:editor.manufacturer||'',strategy:'auto',use_agent:true})});let completed=false;
    for(let count=0;count<180;count++){await new Promise(resolve=>setTimeout(resolve,1500));const job=await TaskRecovery.request('/api/v1/underwriting/component-model-workbench/api/jobs/'+encodeURIComponent(start.job_id));if(state.workId!==work)return;if(job.state==='failed')throw Error(job.error||'检索失败，仍可凭厂家资料人工补充');if(job.state==='done'){d.componentLookup=job.result;completed=true;root.dataset.key='';onChange(true);break;}queryStatus.textContent='正在检索与核对来源 · '+(job.steps?.at(-1)?.message||'请稍候');}if(!completed)throw Error('等待超时，可在组件型号工作台历史查询中查看任务；请勿连续重复提交。');
   }catch(error){queryStatus.textContent=error.message;}finally{query.disabled=state.running;}
  };form.append(query,queryStatus);
  const upload=el('input');upload.type='file';upload.accept='.pdf,.png,.jpg,.jpeg';upload.multiple=true;upload.disabled=state.running;upload.setAttribute('aria-label','上传厂家规格书或参数证明');upload.onchange=async()=>{await onAddDocuments(upload.files);};form.append(el('p','厂家资料附件（PDF／图片）仅作为参数依据，不替代现场材料。'),upload);
  for(const [key,[name,unit,direction]]of Object.entries(fields)){
   const p=editor.parameters[key]??={value:'',unit,direction,load_basis:'unspecified',source_url:'',source_material_id:'',document_title:'',document_version:'',quote:'',page:'',applicability:'',installation_confirmed:false};
   const box=el('fieldset');box.append(el('legend',name+' · '+(direction==='front'?'正面':direction==='back'?'背面':'额定值')+'（选填）'));const g=el('div',null,'cr-grid');box.append(g);
   const value=field(g,name+'数值','value',p,'number');value.min='0.000001';value.step='any';
   function select(label,key,choices){const wrap=el('label',label,'cr-field'),i=el('select');i.setAttribute('aria-label',name+' '+label);for(const [v,t]of choices)i.append(new Option(t,v));i.value=p[key]||'';i.disabled=state.running;i.onchange=()=>{p[key]=i.value;onChange(false);};wrap.append(i);g.append(wrap);return i;}
   select('单位','unit',[[unit,unit],['k'+unit,'k'+unit+'（自动换算为 '+unit+'）']]);
   select('来源口径','load_basis',key==='rated_power_w'?[['unspecified','不适用（额定功率）']]:Object.entries(basis));
   select('上传来源文件','source_material_id',[['','使用 HTTPS 链接'],...state.items.filter(i=>i.sourceOnly).map(i=>[i.id,i.file.name])]);
   field(g,name+' HTTPS 来源链接','source_url',p,'url');field(g,name+' 来源文件名称','document_title',p);field(g,name+' 文件版本／日期','document_version',p);field(g,name+' 原文页码／位置','page',p);
   field(g,name+' 来源原文','quote',p,'textarea');field(g,name+' 适用安装条件','applicability',p,'textarea');
   if(key!=='rated_power_w'){const label=el('label',null,'cr-check'),check=el('input');check.type='checkbox';check.checked=p.installation_confirmed;check.disabled=state.running;check.onchange=()=>{p.installation_confirmed=check.checked;onChange(false);};label.append(check,document.createTextNode(name+'的现场安装适用条件已核验（否则保留待复核）'));box.append(label);}form.append(box);
  }
  const ack=el('label',null,'cr-check'),check=el('input');check.type='checkbox';check.required=true;check.disabled=state.running;ack.append(check,document.createTextNode('我已核对完整型号、单位、载荷方向及来源原文，仅确认本案使用；不写入共享参数库。'));form.append(ack);
  const apply=el('button','确认本案参数并标记待重核','btn primary');apply.type='submit';apply.disabled=state.running;const feedback=el('p','','small');form.append(apply,feedback);
  form.onsubmit=async e=>{e.preventDefault();apply.disabled=true;try{
   const id=state.items.find(i=>i.id===editor.identity_material_id);if(!id)throw Error('请选择本案铭牌或设备清单作为型号核验依据。');const parameters={};
   for(const [key,p]of Object.entries(editor.parameters)){if(p.value===''||p.value==null)continue;if(!Number.isFinite(Number(p.value))||Number(p.value)<=0)throw Error('参数必须为有限正数。');for(const name of ['document_title','document_version','quote','page','applicability'])if(!String(p[name]||'').trim())throw Error(fields[key][0]+'：请补齐来源名称、版本、原文、位置和适用条件。');if(!p.source_url&&!p.source_material_id)throw Error(fields[key][0]+'：请指定上传来源或 HTTPS 链接。');if(p.source_url&&!/^https:\/\//i.test(p.source_url))throw Error('来源链接必须使用 HTTPS。');const item=state.items.find(i=>i.id===p.source_material_id);if(p.source_material_id&&!item)throw Error('来源文件已不存在，请重新选择。');parameters[key]={...p,value:Number(p.value),direction:fields[key][2],source_url:p.source_url||null,source_material_id:p.source_material_id||null,source_sha256:item?await hash(item):null};}
   if(!Object.keys(parameters).length)throw Error('请至少补充一项参数及其证据。');
   d.componentReview={scope:'case_only',component_model:editor.component_model.trim(),manufacturer:editor.manufacturer.trim(),identity_material_id:id.id,identity_sha256:await hash(id),model_reason:editor.model_reason.trim(),reviewer_name:editor.reviewer_name.trim(),reason:editor.reason.trim(),confirmed:true,parameters};
   d.componentInvalidation='';d.actor=d.actor||editor.reviewer_name;d.reason=d.reason||editor.reason;d.corrections=[];state.project.component_model=d.componentReview.component_model;state.stale=!!state.fullCase;state.staleReason='本案组件参数已确认，需重新核保；旧报告及人工意见仅适用于上一轮。';root.dataset.key='';onChange(true);
  }catch(error){feedback.textContent=error.message;}finally{apply.disabled=state.running;}};
  edit.append(form);root.append(edit);
  if(d.componentReview){const remove=el('button','取消采用本案补充参数','btn');remove.disabled=state.running;remove.onclick=()=>{d.componentReview=null;d.componentInvalidation='';state.stale=!!state.fullCase;state.staleReason='已取消本案补充参数，请重新核保以使用重新检索的参数。';root.dataset.key='';onChange(true);};const run=el('button',state.fullCase?'提交新一轮核保':'使用已确认参数开始核保','btn primary');run.disabled=state.running;run.onclick=onRun;root.append(remove,run);}
  const sharing=el('details');sharing.append(el('summary','3. 供后续案件复用：另行核验入库'),el('p','本案确认不会写入共享库。完成本轮后，可将已采用字段带到共享库草稿，重新核验适用范围、审批人和有效期；保存前不会生效。','small'));
  const a=el('a','打开共享参数核验草稿');a.href=state.fullCase?.component_review?.applied?'/verified-components?from_case='+encodeURIComponent(state.fullCase.case_id):'/verified-components';a.target='_blank';a.rel='noopener';sharing.append(a);root.append(sharing);
 }
 global.ComponentReview={render,requestFor,invalidate,diff};if(typeof module!=='undefined')module.exports={requestFor,invalidate,diff};
})(typeof window==='undefined'?globalThis:window);
