(function(){
  'use strict';
  const fields={rated_power_w:['额定功率','W'],front_static_load_pa:['正面机械载荷','Pa'],back_static_load_pa:['背面机械载荷','Pa']};
  const statuses={active:'生效中',scheduled:'待生效',expired:'已到期',revoked:'已撤销'},bases={test:'测试载荷',design:'设计载荷',unspecified:'口径未明确'};
  const $=id=>document.getElementById(id),form=$('approvalForm');let records=[];
  function el(tag,text,cls){const node=document.createElement(tag);if(text!=null)node.textContent=text;if(cls)node.className=cls;return node;}
  function message(text,error=false){$('status').textContent=text;$('status').classList.toggle('error',error);}
  function time(value){return new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false});}
  function local(value){return new Date(value.getTime()-value.getTimezoneOffset()*60000).toISOString().slice(0,16);}
  function dates(){form.elements.valid_from.value=local(new Date());form.elements.expires_at.value=local(new Date(Date.now()+90*86400000));}
  async function api(path='',body){const response=await fetch('/api/v1/components/verified'+path,{method:body?'POST':'GET',headers:body?{'Content-Type':'application/json'}:{},body:body?JSON.stringify(body):undefined,cache:'no-store'});const data=await response.json();if(!response.ok)throw Error(response.status===403?'当前账号无权维护参数，请使用核保或管理员账号。':response.status===401?'请先完成工作台账号认证。':typeof data.detail==='string'?data.detail:response.status===422?'请核对必填项、HTTPS 来源链接、完整型号及有效期。':'操作未完成，请重试');return data;}
  function input(label,name,type='text',required=false){const wrap=el('label',label,'field'),node=el(type==='textarea'?'textarea':'input');node.name=name;node.required=required;if(type!=='textarea')node.type=type;wrap.append(node);return wrap;}
  for(const [key,[label,unit]] of Object.entries(fields)){
    const box=el('fieldset'),legend=el('legend',label+'（'+unit+'，选填）'),grid=el('div',null,'grid');box.append(legend,grid);
    const value=input('数值',key,'number');value.lastChild.min='0.000001';value.lastChild.step='any';grid.append(value);
    const basis=el('label','来源口径','field'),select=el('select');select.name=key+'_basis';for(const [v,t]of Object.entries(bases)){const o=el('option',t);o.value=v;o.defaultSelected=v==='unspecified';select.append(o);}select.value='unspecified';if(key==='rated_power_w'){select.replaceChildren();const o=el('option','不适用（额定功率）');o.value='unspecified';select.append(o);}basis.append(select);grid.append(basis);
    const url=input('该参数的 HTTPS 来源链接',key+'_url','url'),quote=input('来源原文／页码',key+'_quote','textarea');quote.classList.add('full');url.classList.add('full');url.lastChild.maxLength=2000;quote.lastChild.maxLength=2000;grid.append(url,quote);
    value.lastChild.addEventListener('input',()=>{url.lastChild.required=quote.lastChild.required=value.lastChild.value!=='';});$('parameterFields').append(box);
  }
  function render(){
    const q=$('search').value.trim().toLowerCase(),filter=$('statusFilter').value,items=records.filter(r=>(!q||(r.component_model+' '+r.manufacturer).toLowerCase().includes(q))&&(!filter||r.status===filter));
    $('records').replaceChildren();$('count').textContent=`共 ${records.length} 条记录，当前显示 ${items.length} 条 · 时间为北京时间`;
    if(!items.length){$('records').append(el('p',records.length?'没有符合筛选条件的记录。':'暂无已核验参数。核验并登记后，后续核保查询可按完整型号复用。','muted'));return;}
    for(const r of items){
      const box=el('article',null,'record'),heading=el('div',null,'record-title');heading.append(el('h2',r.component_model+' · V'+r.version),el('span',statuses[r.status],'badge '+r.status));box.append(heading,el('p',r.manufacturer+' · '+r.document_title),el('small',`核验人：${r.approved_by} · 核验时间：${time(r.approved_at)}`,'muted'),el('small',`有效期：${time(r.valid_from)} 至 ${time(r.expires_at)}`,'muted'));
      const table=el('table'),tr=el('tr');['参数','数值','口径'].forEach(v=>tr.append(el('th',v)));table.append(tr);
      for(const [key,p]of Object.entries(r.parameters)){const row=el('tr');[fields[key][0],p.value+' '+fields[key][1],(key==='rated_power_w'?'额定功率':bases[p.load_basis])].forEach(v=>row.append(el('td',v)));table.append(row);}box.append(table);
      const details=el('details');details.append(el('summary','来源、适用条件与核验依据'),el('p','适用条件：'+r.applicability),el('p','核验依据与复用原因：'+r.reason),el('small','记录编号：'+r.id,'muted'));
      for(const [key,p]of Object.entries(r.parameters)){const a=el('a','查看 '+fields[key][0]+' 来源');if(/^https:\/\//i.test(p.source_url)){a.href=p.source_url;a.target='_blank';a.rel='noopener noreferrer';}details.append(el('p',fields[key][0]+' 原文：'+p.quote),a);}box.append(details);
      if(r.revoked_at)box.append(el('p',`已由 ${r.revoked_by} 于 ${time(r.revoked_at)} 撤销：${r.revoke_reason}`,'muted'));
      else if(['active','scheduled'].includes(r.status)){
        const panel=el('details',null,'revoke');panel.append(el('summary','撤销本条复用记录'));const rev=el('form'),name=input('撤销人','reviewer_name','text',true),reason=input('撤销原因','reason','textarea',true);name.lastChild.maxLength=100;reason.lastChild.minLength=2;reason.lastChild.maxLength=2000;const button=el('button','确认撤销');button.type='submit';rev.append(name,reason,el('p','撤销后新查询与重试不再使用本条记录。历史报告保留当时采用的证据，必要时请重新核保。','muted'),button);panel.append(rev);box.append(panel);
        rev.addEventListener('submit',async e=>{e.preventDefault();button.disabled=true;try{await api('/'+encodeURIComponent(r.id)+'/revoke',{reviewer_name:name.lastChild.value,reason:reason.lastChild.value});await reload();message('已撤销。历史核验记录和历史报告仍可追溯。');}catch(error){message(error.message,true);}finally{button.disabled=false;}});
      }
      $('records').append(box);
    }
  }
  async function reload(){records=(await api()).records;render();}
  form.addEventListener('submit',async e=>{e.preventDefault();const button=form.querySelector('button[type=submit]');button.disabled=true;try{
    const data=Object.fromEntries(new FormData(form)),parameters={};for(const key of Object.keys(fields))if(data[key]!=='')parameters[key]={value:Number(data[key]),source_url:data[key+'_url'],quote:data[key+'_quote'],load_basis:data[key+'_basis']};
    if(!Object.keys(parameters).length)throw Error('请至少填写一项参数及其来源原文。');
    const body={};for(const key of ['component_model','manufacturer','document_title','applicability','reason','reviewer_name'])body[key]=data[key];Object.assign(body,{valid_from:new Date(data.valid_from).toISOString(),expires_at:new Date(data.expires_at).toISOString(),exact_model_verified:true,parameters});
    await api('',body);form.reset();dates();for(const key of Object.keys(fields)){form.elements[key+'_url'].required=false;form.elements[key+'_quote'].required=false;}$('editor').open=false;await reload();message('核验记录已保存；仅复用所列来源参数，其他核保检查照常执行。');
  }catch(error){message(error.message,true);}finally{button.disabled=false;}});
  $('search').addEventListener('input',render);$('statusFilter').addEventListener('change',render);$('refresh').addEventListener('click',()=>reload().then(()=>message('记录已刷新。')).catch(e=>message(e.message,true)));
  dates();PvWorkbench.init('verifiedComponents');reload().catch(e=>message(e.message,true));
  const sourceCase=new URL(location.href).searchParams.get('from_case');
  if(sourceCase)(async()=>{try{const response=await fetch('/api/v1/cases/'+encodeURIComponent(sourceCase));if(!response.ok)throw Error('来源案件读取失败');const saved=await response.json(),review=saved.case?.component_review;if(!review?.applied)throw Error('该案件没有已采用的本案补充参数');
    form.elements.component_model.value=review.component_model;form.elements.manufacturer.value=review.manufacturer;form.elements.document_title.value=[...new Set(Object.values(review.parameters).map(p=>p.document_title+' '+p.document_version))].join('；').slice(0,200);form.elements.applicability.value=Object.entries(review.parameters).map(([k,p])=>fields[k][0]+'：'+p.applicability).join('；');form.elements.reason.value='来自案件 '+sourceCase+' 的本案参数，待重新核验共享范围。原本案依据：'+review.reason;
    for(const [k,p]of Object.entries(review.parameters)){if(!fields[k])continue;form.elements[k].value=p.normalized_value;form.elements[k+'_url'].value=/^https:\/\//i.test(p.source_url||'')?p.source_url:'';form.elements[k+'_quote'].value=p.quote+'（'+p.document_version+'，'+p.page+'）';form.elements[k+'_basis'].value=p.load_basis;form.elements[k+'_url'].required=form.elements[k+'_quote'].required=true;}
    form.elements.exact_model_verified.checked=false;form.elements.reviewer_name.value='';$('editor').open=true;message('已载入待核草稿，尚未入库。请重新核验、补齐可长期复用的 HTTPS 来源，并填写审批人和有效期。');
  }catch(e){message(e.message,true);}})();

  // Expiry changes without a page reload. Never replace an in-progress revocation form.
  setInterval(()=>{if(!document.querySelector('.revoke[open]'))reload().catch(()=>{});},60000);
})();
