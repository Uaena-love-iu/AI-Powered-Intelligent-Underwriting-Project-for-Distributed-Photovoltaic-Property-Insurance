(function(global){
  'use strict';
  const terminal=new Set(['completed','failed','cancelled']);
  async function request(url,options={}){
    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),15000);
    try{
      const response=await fetch(url,{...options,signal:controller.signal});
      const data=await response.json().catch(()=>({}));
      if(!response.ok){const error=new Error(typeof data.detail==='string'?data.detail:'任务服务暂时无法处理，请稍后查询。');error.status=response.status;throw error;}
      return data;
    }finally{clearTimeout(timer);}
  }
  async function poll(id,onUpdate,{delay=2000,maxPolls=900,wait=ms=>new Promise(resolve=>setTimeout(resolve,ms)),load=request}={}){
    let failures=0;
    for(let i=0;i<maxPolls;i++){
      try{
        const job=await load('/api/v1/underwriting/jobs/'+encodeURIComponent(id));
        failures=0;onUpdate(job);
        if(terminal.has(job.status))return job;
      }catch(error){
        failures++;
        if(error.status===404||error.status===401||error.status===403||failures>=3)throw error;
      }
      await wait(delay);
    }
    throw new Error('页面等待时间已到，后台任务未被停止；请点击“重新连接进度”。');
  }
  function render(root,{job,id,busy,disconnected,submissionIssue,compatible=true,onReconnect,onRetry,onStop,onMaterials,onManual}){
    root.replaceChildren();
    const node=(tag,text)=>{const element=document.createElement(tag);element.textContent=text;return element;};
    if(!id){
      if(submissionIssue){const box=node('div','');box.className='notice';box.append(node('strong','任务未提交成功'),node('p',submissionIssue));const link=node('a','检查服务配置');link.href='/llm-config';link.target='_blank';link.rel='noopener';box.append(link);root.append(box);}
      return;
    }
    const box=node('section','');box.className='task-recovery';box.setAttribute('aria-label','任务异常与恢复');
    Object.assign(box.style,{border:'1px solid #9bc6bb',borderRadius:'12px',padding:'18px',margin:'12px 0',background:'#f5faf8'});
    const names={queued:'等待执行',running:'正在处理',completed:'分析完成',failed:'处理异常',cancelled:'已停止'};
    box.style.lineHeight='1.65';
    box.append(node('h3',disconnected?'连接已中断 · 后台状态待确认':job?.status==='completed'&&job?.recovery?.issues?.length?'分析结束 · 有步骤待处理':names[job?.status]||'任务状态待确认'));
    box.append(node('p','任务编号：'+id+' · '+(job?.current_step||'等待查询')));
    const recovery=job?.recovery||{},steps=recovery.steps||[],issues=recovery.issues||[];
    box.append(node('p','已保留 '+(recovery.retained||0)+' 个成功步骤，本轮复用 '+steps.filter(s=>s.status==='reused').length+' 个；恢复时校验材料、规则和模型配置，失效结果重新计算。'));
    if(disconnected)box.append(node('p','页面连接异常不代表核保任务失败。重新连接只查询原任务，不会重复调用模型。'));
    if(!compatible)box.append(node('p','当前材料、项目资料或规则已修改。下面是修改前的任务；请使用“运行整条链路”创建新一轮分析。'));
    if(job?.error_code==='SERVER_RESTARTED')box.append(node('p','服务重启中断了执行，可恢复未完成步骤，已保存的有效结果会复用。'));
    if(job?.error_code==='SERVICE_TIMEOUT')box.append(node('p','任务执行时间超过限制，可恢复未完成步骤。'));
    if(recovery.cancel_requested&&job?.status==='running')box.append(node('p','正在停止：当前服务调用返回后不再启动后续步骤。'));
    for(const issue of issues){
      const card=node('div','');card.style.cssText='border-left:4px solid #b85b32;padding:8px 12px;margin:10px 0;background:#fff7ef';
      card.append(node('strong',issue.name+'：'+issue.message),node('p','影响材料：'+(issue.material_ids?.join('、')||'整单')+'；已尝试 '+issue.attempts+' 次'),node('p','下一步：'+issue.action+'。处理人：'+issue.owner));box.append(card);
    }
    if(job?.status==='completed'&&!issues.length&&job.result?.decision){
      const decision=job.result.decision,requests=decision.missing_requirements||[];
      box.append(node('p',requests.length?'材料或信息不足：请补充后发起新一轮分析，重复调用同一材料不能补齐证据。':'AI 建议已生成，请由业务人员复核；命中风险规则不属于服务故障。'));
      for(const text of requests.slice(0,10))box.append(node('p','• '+text));
    }
    if(recovery.attempt>=3)box.append(node('p','本轮已达到三次人工重试上限，请人工核验或修正后新建一轮。'));
    const actions=node('div','');actions.style.cssText='display:flex;flex-wrap:wrap;gap:8px';
    function button(text,handler,disabled=false){const b=node('button',text);b.type='button';b.className='btn';b.disabled=disabled;b.onclick=async()=>{b.disabled=true;try{await handler();}catch(error){box.append(node('p',error.message));b.disabled=false;}};actions.append(b);}
    if(!busy&&compatible)button(disconnected||!terminal.has(job?.status)?'重新连接进度':'刷新任务状态',onReconnect);
    if(job?.can_retry&&compatible)button(job.status==='cancelled'?'恢复未完成步骤':issues.some(s=>s.code==='SERVICE_AUTH'||s.code==='SERVICE_CONFIG')?'已修正配置，重试失败步骤':'重试失败步骤',onRetry,busy);
    if(recovery.child_id&&compatible&&!busy)button('查看已发起的重试',()=>onReconnect(recovery.child_id));
    if(['queued','running'].includes(job?.status))button('停止后续处理',onStop,!!recovery.cancel_requested);
    if(!busy){button('查看／补充材料',onMaterials);if(issues.length)button('记录转人工核验',onManual);}
    if(issues.some(s=>['SERVICE_AUTH','SERVICE_CONFIG'].includes(s.code))){const link=node('a','打开服务配置');link.href='/llm-config';link.target='_blank';link.rel='noopener';link.className='btn';actions.append(link);}
    box.append(actions);
    if(steps.length){const detail=node('details','');detail.append(node('summary','查看步骤处理记录'));const labels={running:'处理中',retrying:'自动重试中',failed:'失败',success:'成功并保留',reused:'已复用'};for(const step of steps)detail.append(node('p',step.name+' · '+(step.material_ids?.join('、')||'整单')+' · '+(labels[step.status]||step.status)));box.append(detail);}
    if(recovery.events?.length){const detail=node('details','');detail.append(node('summary','任务提醒（已去重）'));for(const event of recovery.events)detail.append(node('p',new Date(event.created_at).toLocaleString('zh-CN')+' · '+event.message));box.append(detail);}
    root.append(box);
  }
  global.TaskRecovery={request,poll,render};
  if(typeof module!=='undefined')module.exports=global.TaskRecovery;
})(typeof window==='undefined'?globalThis:window);
