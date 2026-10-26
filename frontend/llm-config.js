(function () {
  'use strict';
  PvWorkbench.init('llmConfig');
  const $ = (id) => document.getElementById(id);
  const modules = [
    {id:'classification', number:'01', title:'材料分类', subtitle:'上传预检 · 按图片推荐材料类别', model:'classification', prompts:[['classification','分类判断与结构化输出']]},
    {id:'filing', number:'02', title:'备案证识别', subtitle:'分布式／屋顶／车棚及涉农涉水语义判断', model:'filing', prompts:[['filing_assessment','备案建设方式与环境判断规则']], bundle:'document'},
    {id:'installation', number:'03', title:'组图场景识别', subtitle:'同一组现场照片，判断安装类型并观察可见通道阻塞', model:'installation', prompts:[['installation','组图安装类型判断'],['aisle_group','可见通道阻塞观察'],['installation_localization','安装类型证据补定位']]},
    {id:'vision', number:'04', title:'单图风险识别', subtitle:'按材料类别检查现场风险与拍摄水印', model:'vision', bundle:'vision'},
    {id:'ocr', number:'05', title:'文字与水印提取', subtitle:'设备清单及现场照片文字', model:'ocr', prompts:[['watermark_guidance','现场拍摄水印提取']], bundle:'equipment'},
    {id:'geometry', number:'06', title:'抗灾几何识别', subtitle:'倾角、离地高度与地面粗糙度', model:'geometry', prompts:[['geometry','抗灾几何估计']]},
    {id:'summary', number:'07', title:'AI 总结', subtitle:'报告生成后按需调用 · 流式摘要', model:'summary', prompts:[
      ['report_summary','通用系统提示词'],
      ['report_summary_recommend_reject','建议拒保 · 场景要求'],
      ['report_summary_request_more','需补充材料 · 场景要求'],
      ['report_summary_manual_review','需人工复核 · 场景要求'],
      ['report_summary_surcharge','历史“建议加费”案件 · 兼容提示词'],
      ['report_summary_conditional_accept','建议附条件承保 · 场景要求'],
      ['report_summary_accept','建议承保 · 场景要求'],
    ]},
  ];
  let runtime, lab;
  const channel = typeof BroadcastChannel === 'function' ? new BroadcastChannel('pv-llm-config') : null;
  const snapshots = new Map();
  const specialtyDrafts = {vision:{}, document:{}};

  function el(tag, cls, content) {
    const result = document.createElement(tag);
    if (cls) result.className = cls;
    if (content != null) result.textContent = content;
    return result;
  }
  function status(message, error=false) {
    $('status').textContent = message;
    $('status').classList.toggle('error', error);
  }
  async function api(path, options) {
    const response = await fetch(path, options);
    let data;
    try { data = await response.json(); } catch { throw Error('服务返回了无法解析的内容'); }
    if (!response.ok) throw Error(typeof data.detail === 'string' ? data.detail : `HTTP ${response.status}`);
    return data;
  }
  function field(label, value, kind='text', note='') {
    const wrapper = el('label', 'field');
    wrapper.append(el('span', '', label));
    const input = el(kind === 'textarea' ? 'textarea' : 'input');
    if (kind !== 'textarea') input.type = 'text';
    input.value = value ?? '';
    input.spellcheck = false;
    wrapper.append(input);
    if (note) wrapper.append(el('small', '', note));
    return [wrapper, input];
  }
  function makeBundleFields(card, module) {
    if (!module.bundle) return;
    const group = module.bundle === 'vision' ? 'vision' : 'document';
    if (module.bundle !== 'equipment') {
      const key = group === 'vision' ? 'prompt' : 'document_prompt';
      const [wrapper, input] = field(group === 'vision' ? '现场照片公共提示词' : '文档公共提示词', lab[key], 'textarea',
        group === 'vision' ? '与图片识别调试页使用同一份默认提示词。' : '同时作用于备案证与设备清单。');
      input.classList.add('long'); input.dataset.bundleKey = key;
      card.append(wrapper);
    } else {
      card.append(el('p','hint','设备清单与备案证共用上方“文档公共提示词”，此处只编辑设备清单专属规则。'));
    }
    const categories = group === 'vision'
      ? lab.material_types.filter(item => item.pipeline === 'vision').map(item => [item.category, item.label])
      : module.bundle === 'equipment' ? [['equipment_inventory','设备清单']] : [['project_document','项目证照／备案证']];
    const row = el('div','specialty-row');
    const label = el('label','field'); label.append(el('span','','材料专属提示词'));
    const select = el('select');
    for (const [key, name] of categories) {
      const option = el('option','',name); option.value = key; select.append(option);
    }
    label.append(select); row.append(label);
    row.append(el('p','note','按材料类别选择模板。保存时只提交本模块改过的类别，其他类别保持原值。'));
    card.append(row);
    const [editorField, editor] = field('所选类别的专属提示词', '', 'textarea');
    editorField.classList.add('specialty-editor');
    editor.classList.add('long');
    card.append(editorField);
    const source = group === 'vision' ? lab.specialized_prompts : lab.document_specialized_prompts;
    const drafts = specialtyDrafts[group];
    select.onchange = () => { editor.value = drafts[select.value] ?? source[select.value] ?? ''; };
    editor.oninput = () => { drafts[select.value] = editor.value; };
    select.onchange();
    const contract = el('details');
    contract.append(el('summary','','查看程序固定的结构化输出契约'));
    const code = el('pre','note',group === 'vision' ? lab.contract : lab.document_contract);
    code.style.whiteSpace='pre-wrap';code.style.overflowWrap='anywhere';
    contract.append(code);
    card.append(contract);
  }
  function render() {
    $('moduleNav').replaceChildren(); $('modules').replaceChildren(); snapshots.clear();
    for (const module of modules) {
      const link = el('a','',`${module.number}  ${module.title}`); link.href = '#' + module.id; $('moduleNav').append(link);
      const section = el('section','module'); section.id = module.id;
      const head = el('div','module-head'); const title = el('div');
      title.append(el('small','',`运行模块 ${module.number} / 07`),el('h2','',module.title),el('p','',module.subtitle));
      head.append(title,el('span','pill',module.id === 'summary' ? '报告生成后调用' : '真实整单链路')); section.append(head);
      const body = el('div','module-body');
      const currentModel = runtime.models[module.model];
      const [modelField, modelInput] = field('调用模型', runtime.model_overrides[module.model] || currentModel || '', 'text',
        `当前生效：${currentModel || '未配置'}。清空并保存可撤销页面覆盖，恢复服务端环境配置。`);
      modelInput.dataset.model = module.model; body.append(modelField);
      const scenarioGroup = module.id === 'summary' ? el('details') : null;
      if (scenarioGroup) scenarioGroup.append(el('summary','','按五种主状态编辑专属要求'));
      const legacyGroup = module.id === 'summary' ? el('details') : null;
      if (legacyGroup) legacyGroup.append(el('summary','','历史案件兼容设置'));
      for (const [key, label] of module.prompts || []) {
        const [wrapper, input] = field(label, runtime.prompts[key], 'textarea',
          (runtime.required_placeholders[key] || []).length
            ? '必须保留动态字段：' + runtime.required_placeholders[key].join('、')
            : '此提示词与程序固定的输出格式和字段解析共同工作。');
        input.dataset.prompt = key;
        const parent = key === 'report_summary_surcharge' ? legacyGroup
          : scenarioGroup && key !== 'report_summary' ? scenarioGroup : body;
        parent.append(wrapper);
        const reset = el('button','', '恢复内置提示词'); reset.type = 'button';
        reset.onclick = () => { input.value = runtime.prompt_defaults[key]; };
        parent.append(reset);
      }
      if (scenarioGroup) body.append(scenarioGroup);
      if (legacyGroup) body.append(legacyGroup);
      makeBundleFields(body,module);
      const footer = el('div','module-footer');
      footer.append(el('span','',module.bundle ? '照片／文档模板与图片识别调试页同步' : module.id === 'summary' ? '保存后下一次生成或重新生成摘要立即使用' : '保存后下一次请求立即使用'));
      const save = el('button','save','保存此模块'); save.type='button'; save.onclick=()=>saveModule(module,section);
      footer.append(save); body.append(footer); section.append(body); $('modules').append(section);
      snapshots.set(module.id, readModule(module,section));
    }
  }
  function readModule(module,section) {
    const model = section.querySelector('[data-model]').value.trim();
    const prompts = {};
    for (const input of section.querySelectorAll('[data-prompt]')) prompts[input.dataset.prompt] = input.value;
    const common = section.querySelector('[data-bundle-key]');
    const group = module.bundle === 'vision' ? 'vision' : 'document';
    const source = group === 'vision' ? lab.specialized_prompts : lab.document_specialized_prompts;
    const allowed = module.bundle === 'vision'
      ? new Set(lab.material_types.filter(item => item.pipeline === 'vision').map(item => item.category))
      : new Set([module.bundle === 'equipment' ? 'equipment_inventory' : 'project_document']);
    const changedSpecialties = {};
    for (const [key,value] of Object.entries(specialtyDrafts[group])) {
      if (allowed.has(key) && value !== source[key]) changedSpecialties[key] = value;
    }
    return {model,prompts,common:common?.value ?? null,specialties:changedSpecialties};
  }
  function changed(module,section) { return JSON.stringify(readModule(module,section)) !== JSON.stringify(snapshots.get(module.id)); }
  function anyChanges() { return modules.some(module => {const section=$(module.id); return section && changed(module,section);}); }
  async function saveModule(module,section) {
    const save = section.querySelector('.save');
    save.disabled=true; section.setAttribute('aria-busy','true');
    try {
      const form = readModule(module,section);
      const modelUpdate = form.model === (runtime.model_overrides[module.model] || runtime.models[module.model])
        ? {} : {[module.model]: form.model || null};
      const promptUpdate = {};
      for (const [key,value] of Object.entries(form.prompts)) {
        if (value !== runtime.prompts[key]) promptUpdate[key] = value;
      }
      if (Object.keys(modelUpdate).length || Object.keys(promptUpdate).length) {
        runtime = await api('/api/v1/config/llm',{method:'PATCH',headers:{'Content-Type':'application/json'},
          body:JSON.stringify({models:modelUpdate,prompts:promptUpdate})});
      }
      if (module.bundle) {
        const latest = await api('/api/v1/vision/lab/config');
        const update = {prompt:latest.prompt,version_name:'运行配置 · 模块化编辑'};
        if (module.bundle === 'vision') {
          if (form.common !== null && form.common !== lab.prompt) update.prompt=form.common;
          if (Object.keys(form.specialties).length) update.specialized_prompts=form.specialties;
        } else {
          if (form.common !== null && form.common !== lab.document_prompt) update.document_prompt=form.common;
          if (Object.keys(form.specialties).length) update.document_specialized_prompts=form.specialties;
        }
        if (update.prompt !== latest.prompt || update.document_prompt != null ||
            update.specialized_prompts || update.document_specialized_prompts) {
          await api('/api/v1/vision/lab/default',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(update)});
          lab = await api('/api/v1/vision/lab/config');
          const group = module.bundle === 'vision' ? 'vision' : 'document';
          for (const key of Object.keys(form.specialties)) delete specialtyDrafts[group][key];
        }
      }
      modelInputRefresh(section,module,runtime);
      snapshots.set(module.id,readModule(module,section));
      status(`${module.title}已保存；${module.id === 'summary' ? '下一次生成或重新生成摘要会读取更新后的配置。' : '新的整单运行和模块调试会读取更新后的配置。'}`);
      channel?.postMessage({type:'updated',module:module.id});
    } catch(error) { status(`${module.title}保存失败：${error.message}`,true); }
    finally { save.disabled=false; section.removeAttribute('aria-busy'); }
  }
  function modelInputRefresh(section,module,latest) {
    section.querySelector('[data-model]').value = latest.model_overrides[module.model] || latest.models[module.model] || '';
  }
  async function load() {
    if (anyChanges() && !confirm('当前有未保存的修改。确定重新读取并放弃这些修改吗？')) return;
    try {
      [runtime,lab] = await Promise.all([api('/api/v1/config/llm'),api('/api/v1/vision/lab/config')]);
      specialtyDrafts.vision={}; specialtyDrafts.document={}; render();
      status('配置已读取。每个模块单独保存；新的分析或 AI 总结请求会使用保存后的配置。');
    } catch(error) { status(`读取配置失败：${error.message}`,true); }
  }
  $('reload').onclick=load;
  if (channel) channel.onmessage=event=>{
    if (event.data?.type !== 'updated') return;
    if (anyChanges()) status('其他页面更新了配置；此页有未保存修改，请保存或手动重新读取。',true);
    else load();
  };
  window.addEventListener('beforeunload',event=>{if(anyChanges()){event.preventDefault();event.returnValue='';}});
  PvWorkbench.beforeNavigate(async()=>!anyChanges() || confirm('有未保存的配置。确定离开吗？'));
  load();
})();
